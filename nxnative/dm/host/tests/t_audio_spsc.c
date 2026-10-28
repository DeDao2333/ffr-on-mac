// hle_audio.c lock-free queue stress test: three threads producing / playing / reclaiming 100 thousand blocks concurrently, the byte stream and reclaim order checked one by one by sequence number, no event lost.
#include "../hle_audio.c"
#include <sched.h>
#include <time.h>

enum { NBUF = 100000, MAX_SIZE = 37 };

uint64_t now_ns(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return (uint64_t)t.tv_sec * 1000000000ull + t.tv_nsec; }
static struct { const void *k; void *v; } om[8];
void *objmap_get(const void *k) { for (int i = 0; i < 8; i++) if (om[i].k == k) return om[i].v; return NULL; }
void objmap_put(const void *k, void *v) { for (int i = 0; i < 8; i++) if (!om[i].k) { om[i].k = k; om[i].v = v; return; } }
void *objmap_del(const void *k) { for (int i = 0; i < 8; i++) if (om[i].k == k) { om[i].k = 0; return om[i].v; } return NULL; }

static _Atomic int g_sig, g_after_last;
static _Atomic int g_last_pull;
void hle_signal_system_event(void *g) {
    (void)g;
    atomic_fetch_add_explicit(&g_sig, 1, memory_order_seq_cst);
    if (atomic_load_explicit(&g_last_pull, memory_order_seq_cst)) atomic_store(&g_after_last, 1);
}
void hle_bind_system_event(void *g, int autoclear) { (void)g; (void)autoclear; }
module_t *module_of(const void *p) { (void)p; return NULL; }   // call sites in the log: no guest modules in the unit test
void dm_host_helper_qos(qos_class_t q) { (void)q; }   // hle_thread.c: the helper threads keep the default class in the unit test

typedef struct {
    void *out;
    aout *a;
    gbuf *bufs;
    uint8_t (*data)[MAX_SIZE];
    uint8_t *scratch;
    uint32_t *sizes;
    uint64_t total_bytes;
    _Atomic int failed;
    _Atomic int released;
} test_ctx;

static uint32_t next_random(uint32_t *state) {
    *state = *state * 1664525u + 1013904223u;
    return *state;
}

static uint64_t available_bytes(aout *a) {
    uint32_t qh = atomic_load_explicit(&a->q_head, memory_order_relaxed);
    uint32_t qt = atomic_load_explicit(&a->q_tail, memory_order_acquire);
    if (qh == qt) return 0;
    uint64_t bytes = a->q[qh & (QMAX - 1)]->size - a->pos;
    for (uint32_t i = qh + 1; i != qt; i++) bytes += a->q[i & (QMAX - 1)]->size;
    return bytes;
}

static void *producer(void *p) {
    test_ctx *t = p;
    for (uint32_t i = 0; i < NBUF; i++) {
        t->bufs[i].ptr = t->data[i]; t->bufs[i].cap = MAX_SIZE; t->bufs[i].size = t->sizes[i];
        // Real buffer pool: buffers in flight (appended, not yet reclaimed) do not exceed QMAX, and more can be appended only after reclaiming
        while (i - (uint32_t)atomic_load_explicit(&t->released, memory_order_acquire) >= QMAX) sched_yield();
        while (!h_au_Append(t->out, &t->bufs[i])) sched_yield();
    }
    return NULL;
}

static void *consumer(void *p) {
    test_ctx *t = p;
    uint32_t state = 7;
    uint64_t want = 0, done = 0;
    while (done < t->total_bytes) {
        uint64_t avail = available_bytes(t->a);
        if (!avail) {
            if (atomic_load_explicit(&t->released, memory_order_acquire) == NBUF) break;
            sched_yield();
            continue;
        }
        uint64_t n = 1 + next_random(&state) % 512;
        if (n > avail) n = avail;
        if (done + n == t->total_bytes) atomic_store(&g_last_pull, 1);
        pull(t->a, t->scratch, n);
        for (uint64_t i = 0; i < n; i++) {
            if (t->scratch[i] != (uint8_t)(want++)) atomic_store(&t->failed, 1);
        }
        done += n;
        if (atomic_load(&t->released) == NBUF && !available_bytes(t->a)) break;
    }
    return NULL;
}

static void *release_reader(void *p) {
    test_ctx *t = p;
    for (int i = 0; i < NBUF; ) {
        gbuf *b = h_au_Released(t->out);
        if (!b) { sched_yield(); continue; }
        if (b != &t->bufs[i]) atomic_store(&t->failed, 1);
        i++;
        atomic_store_explicit(&t->released, i, memory_order_release);
    }
    return NULL;
}

static int stop_pending(void) {
    char out[64], ev[64]; uint8_t prm[16], bytes[5][8] = {{0}}, played[13]; gbuf buffers[5] = {0};
    h_au_InitParam(prm); if (h_au_Open(out, ev, NULL, prm)) return 1;
    aout *a = ao(out); atomic_store(&a->state, STATE_STARTED);   // deterministic callback, no backend thread
    atomic_store(&a->q_head, UINT32_MAX-2); atomic_store(&a->q_tail, UINT32_MAX-2);
    atomic_store(&a->rel_head, UINT32_MAX-1); atomic_store(&a->rel_tail, UINT32_MAX-1);
    for (int i=0; i<5; i++) { buffers[i].ptr=bytes[i]; buffers[i].size=buffers[i].cap=8; if (!h_au_Append(out,&buffers[i])) return 1; }
    pull(a,played,sizeof played);   // one released buffer followed by a partly played one
    a->rel_sem=dispatch_semaphore_create(0);
    h_au_Stop(out);
    int failed = h_au_BufCount(out)!=0 || a->pos!=0 || dispatch_semaphore_wait(a->rel_sem,dispatch_time(DISPATCH_TIME_NOW,100000000))!=0;
    for (int i=0; i<5; i++) failed |= h_au_Released(out)!=&buffers[i];
    h_au_Stop(out); failed |= h_au_Released(out)!=NULL;
    // Buffers queued while stopped belong to the next Start; a repeated Stop keeps them.
    h_au_Append(out,&buffers[0]); h_au_Stop(out); failed |= h_au_BufCount(out)!=1;
    atomic_store(&a->state,STATE_STARTED); h_au_Stop(out); failed |= h_au_Released(out)!=&buffers[0] || h_au_Released(out)!=NULL;
    h_au_Close(out);
    printf("Stop releases partial/pending buffers in order, signals and preserves pre-start buffers: %s\n",failed?"FAIL":"PASS");
    atomic_store(&g_sig,0); return failed;
}

static void fill_frames(gbuf *buffer, int ch, uint64_t *frame) {
    int16_t *samples = buffer->ptr;
    for (uint64_t f = 0; f < buffer->size / (2u * (unsigned)ch); f++) {
        int16_t value = (int16_t)(1 + (*frame)++ % 10000);
        for (int c = 0; c < ch; c++) samples[f * ch + c] = c == 0 ? value : c == 1 ? -value : 0;
    }
}

static int feedback_fill(aout *a, void *out, int ch, uint64_t *generated) {
    for (int step = 0; step < 100; step++) {
        uint32_t copied = pcm_pump_step(a); int appended = 0; gbuf *buffer;
        while ((buffer = h_au_Released(out))) {
            fill_frames(buffer, ch, generated);
            if (!h_au_Append(out, buffer)) return 1;
            appended = 1;
        }
        if (!copied && !appended) break;
    }
    return 0;
}

static int empty_and_partial_frames(void) {
    char out[64]; uint8_t prm[16], bytes[3] = {0x34, 0x12, 0x78}, played[4] = {0};
    gbuf buffers[2] = {{0}, {bytes, sizeof bytes, sizeof bytes}};
    h_au_InitParam(prm); if (h_au_OpenDef0(out, prm)) return 1;
    aout *a = ao(out); atomic_store(&a->state, STATE_STARTED);
    h_au_Append(out, &buffers[0]); h_au_Append(out, &buffers[1]);
    int failed = pcm_pump_step(a) != 1 || dm_audio_fifo_read(&a->pcm, played, 1) != 1;
    failed |= memcmp(bytes, played, 3) != 0 || played[3] != 0;
    failed |= h_au_Released(out) != &buffers[0] || h_au_Released(out) != &buffers[1];
    h_au_Append(out, &buffers[0]);
    failed |= pcm_pump_step(a) != 0 || h_au_Released(out) != &buffers[0];
    h_au_Close(out);
    printf("zero-byte and incomplete-frame descriptors released, tail zero-padded: %s\n", failed ? "FAIL" : "PASS");
    return failed;
}

static int startup_priming(void) {
    char out[64], ev[64]; uint8_t prm[16]; int16_t pcm[3][240 * 6], played[1024 * 2]; gbuf buffers[3];
    uint64_t generated = 0;
    h_au_InitParam(prm); *(uint16_t *)(prm + 4) = 6;
    if (h_au_Open(out, ev, NULL, prm)) return 1;
    aout *a = ao(out); atomic_store(&a->state, STATE_STARTED); atomic_store(&a->priming, 1);
    for (int i = 0; i < 3; i++) {
        buffers[i] = (gbuf){pcm[i], sizeof pcm[i], sizeof pcm[i]};
        fill_frames(&buffers[i], 6, &generated); h_au_Append(out, &buffers[i]);
    }
    if (feedback_fill(a, out, 6, &generated)) return 1;
    uint32_t before = dm_audio_fifo_available(&a->pcm);
    AudioBufferList bl = {.mNumberBuffers = 1, .mBuffers = {{2, sizeof played, played}}};
    au_render(a, NULL, NULL, 0, 1024, &bl);
    int failed = before != 480 || dm_audio_fifo_available(&a->pcm) != before || atomic_load(&a->under_n) != 0;
    for (int i = 0; i < 2048; i++) failed |= played[i] != 0;
    failed |= feedback_fill(a, out, 6, &generated);
    au_render(a, NULL, NULL, 0, 1024, &bl);
    for (int i = 0; i < 1024; i++) failed |= played[2*i] != i+1 || played[2*i+1] != -(i+1);
    failed |= atomic_load(&a->under_n) != 0 || atomic_load(&a->priming) != 0;
    h_au_Stop(out); failed |= dm_audio_fifo_available(&a->pcm) != 0;
    h_au_Close(out);
    printf("first callback 1024 vs initial 240 quantum: prefill preserves PCM, Stop flushes FIFO: %s\n", failed ? "FAIL" : "PASS");
    return failed;
}

// A finite pool cannot refill in the middle of the device callback. The PCM
// producer runs separately, returns copied blocks, and is allowed to wait for
// their replacements before the next hardware deadline. Exercise both the
// 3*240-frame surround pool and a single non-divisible large stereo block.
static int finite_pool(int ch, int count, unsigned frames) {
    char out[64], ev[64]; uint8_t prm[16]; gbuf buffers[3] = {0};
    uint64_t generated = 0, played = 0;
    h_au_InitParam(prm); *(uint16_t *)(prm + 4) = (uint16_t)ch;
    if (h_au_Open(out, ev, NULL, prm)) return 1;
    aout *a = ao(out);
    atomic_store(&a->state, STATE_STARTED); atomic_store(&a->device_frames, 512);
    for (int i = 0; i < count; i++) {
        buffers[i].size = buffers[i].cap = (uint64_t)frames * ch * 2;
        buffers[i].ptr = malloc((size_t)buffers[i].size);
        if (!buffers[i].ptr) return 1;
        fill_frames(&buffers[i], ch, &generated);
        if (!h_au_Append(out, &buffers[i])) return 1;
    }
    int failed = 0;
    for (int callback = 0; callback < 2000 && !failed; callback++) {
        if (feedback_fill(a, out, ch, &generated)) { failed = 1; break; }
        int16_t output[512 * 2];
        AudioBufferList bl = {.mNumberBuffers = 1, .mBuffers = {{2, sizeof output, output}}};
        au_render(a, NULL, NULL, 0, 512, &bl);
        for (int f = 0; f < 512; f++) {
            int16_t expected = (int16_t)(1 + played++ % 10000);
            if (output[2*f] != expected || output[2*f+1] != -expected) { failed = 1; break; }
        }
    }
    uint64_t underruns = atomic_load(&a->under_n);
    h_au_Close(out);
    for (int i = 0; i < count; i++) free(buffers[i].ptr);
    printf("finite pool %dch %d*%u frames, 2000*512 device frames: underruns=%llu, stream %s\n",
           ch, count, frames, (unsigned long long)underruns, failed ? "FAIL" : "PASS");
    atomic_store(&g_sig, 0);
    return failed || underruns;
}
int main(void) {
    if (stop_pending() || empty_and_partial_frames() || startup_priming() || finite_pool(6, 3, 240) || finite_pool(2, 1, 65537)) return 1;
    char out[64], ev[64]; uint8_t prm[16];
    test_ctx t = {0};
    t.bufs = calloc(NBUF, sizeof *t.bufs); t.data = calloc(NBUF, sizeof *t.data); t.sizes = calloc(NBUF, sizeof *t.sizes); t.scratch = malloc(512);
    if (!t.bufs || !t.data || !t.sizes || !t.scratch) return 2;
    h_au_InitParam(prm);
    uint32_t state = 1; uint64_t cursor = 0;
    for (uint32_t i = 0; i < NBUF; i++) {
        t.sizes[i] = 1 + next_random(&state) % MAX_SIZE;
        for (uint32_t j = 0; j < t.sizes[i]; j++) t.data[i][j] = (uint8_t)(cursor++);
        t.total_bytes += t.sizes[i];
    }
    if (h_au_Open(out, ev, NULL, prm)) return 3;
    t.out = out; t.a = ao(out);
    atomic_store(&t.a->state, STATE_STARTED);
    pthread_t p, c, r;
    pthread_create(&p, NULL, producer, &t);
    pthread_create(&c, NULL, consumer, &t);
    pthread_create(&r, NULL, release_reader, &t);
    pthread_join(p, NULL); pthread_join(c, NULL); pthread_join(r, NULL);
    atomic_store(&t.a->state, STATE_STOPPED);
    h_au_Close(out);
    int sig = atomic_load(&g_sig), after = atomic_load(&g_after_last), released = atomic_load(&t.released), failed = atomic_load(&t.failed);
    printf("SPSC: appended/released %d/%d, events %d, signaled after last release=%d, failed=%d\n", NBUF, released, sig, after, failed);
    free(t.scratch); free(t.sizes); free(t.data); free(t.bufs);
    return released != NBUF || failed || sig < 1 || !after;
}
