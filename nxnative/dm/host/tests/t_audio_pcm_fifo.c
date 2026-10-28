// Standalone C11 tests: no AudioUnit or guest runtime is needed.
#include "../audio_pcm_fifo.h"
#include <pthread.h>
#include <sched.h>
#include <stdio.h>

#define CHECK(expr) do { if (!(expr)) { \
    fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #expr); return 1; \
} } while (0)

static int test_init(void) {
    dm_audio_fifo f;
    uint8_t storage[24];
    CHECK(!dm_audio_fifo_init(NULL, storage, 8, 3));
    CHECK(!dm_audio_fifo_init(&f, NULL, 8, 3));
    CHECK(!dm_audio_fifo_init(&f, storage, 0, 3));
    CHECK(!dm_audio_fifo_init(&f, storage, 3, 3));
    CHECK(!dm_audio_fifo_init(&f, storage, 8, 0));
    CHECK(!dm_audio_fifo_init(&f, storage, UINT32_C(0x80000000), 1));
#if SIZE_MAX <= UINT32_MAX
    CHECK(!dm_audio_fifo_init(&f, storage, 8, UINT32_MAX));
#endif
    CHECK(dm_audio_fifo_init(&f, storage, 1, 3));
    CHECK(dm_audio_fifo_available(&f) == 0 && dm_audio_fifo_space(&f) == 1);
    CHECK(dm_audio_fifo_write(&f, NULL, 0) == 0);
    CHECK(dm_audio_fifo_read(&f, NULL, 0) == 0);
    CHECK(dm_audio_fifo_write(&f, storage, 0) == 0);
    CHECK(dm_audio_fifo_read(&f, storage, 0) == 0);
    return 0;
}

static int test_frames(void) {
    struct { uint8_t before[3], pcm[8 * 3], after[3]; } storage;
    uint8_t input[20][3], output[10][3];
    dm_audio_fifo f;
    memset(&storage, 0xa5, sizeof storage);
    for (unsigned i = 0; i < 20; i++) {
        input[i][0] = (uint8_t)i;
        input[i][1] = (uint8_t)(i ^ 0x5a);
        input[i][2] = (uint8_t)(i ^ 0xc3);
    }
    memset(output, 0xcc, sizeof output);
    CHECK(dm_audio_fifo_init(&f, storage.pcm, 8, 3));
    CHECK(dm_audio_fifo_read(&f, output, 10) == 0);
    for (unsigned i = 0; i < sizeof output; i++) CHECK(((uint8_t *)output)[i] == 0xcc);
    CHECK(dm_audio_fifo_write(&f, input, 10) == 8);
    CHECK(dm_audio_fifo_space(&f) == 0 && dm_audio_fifo_available(&f) == 8);
    CHECK(dm_audio_fifo_write(&f, input, 1) == 0);
    CHECK(dm_audio_fifo_read(&f, output, 3) == 3);
    CHECK(memcmp(output, input, 3 * 3) == 0);
    CHECK(dm_audio_fifo_write(&f, input + 8, 6) == 3);
    memset(output, 0xcc, sizeof output);
    CHECK(dm_audio_fifo_read(&f, output, 10) == 8); // read crosses storage end
    CHECK(memcmp(output, input + 3, 8 * 3) == 0);
    for (unsigned i = 8 * 3; i < sizeof output; i++) CHECK(((uint8_t *)output)[i] == 0xcc);
    CHECK(dm_audio_fifo_available(&f) == 0 && dm_audio_fifo_space(&f) == 8);
    CHECK(dm_audio_fifo_write(&f, input + 11, 6) == 6); // write crosses storage end
    CHECK(dm_audio_fifo_read(&f, output, 6) == 6);
    CHECK(memcmp(output, input + 11, 6 * 3) == 0);
    CHECK(dm_audio_fifo_write(&f, input, UINT32_MAX) == 8);
    CHECK(dm_audio_fifo_read(&f, output, UINT32_MAX) == 8);
    CHECK(memcmp(output, input, 8 * 3) == 0);
    CHECK(dm_audio_fifo_write(&f, input, 5) == 5);
    dm_audio_fifo_reset(&f);
    CHECK(dm_audio_fifo_available(&f) == 0 && dm_audio_fifo_space(&f) == 8);
    for (unsigned i = 0; i < 3; i++) CHECK(storage.before[i] == 0xa5 && storage.after[i] == 0xa5);
    return 0;
}

static int test_counter_wrap(void) {
    dm_audio_fifo f;
    uint32_t storage[8], input[11], output[8];
    for (uint32_t i = 0; i < 11; i++) input[i] = i;
    CHECK(dm_audio_fifo_init(&f, storage, 8, sizeof *storage));
    atomic_store_explicit(&f.read_frame, UINT32_MAX - 3, memory_order_relaxed);
    atomic_store_explicit(&f.write_frame, UINT32_MAX - 3, memory_order_relaxed);
    CHECK(dm_audio_fifo_write(&f, input, 6) == 6);
    CHECK(atomic_load(&f.write_frame) == 2);
    CHECK(dm_audio_fifo_available(&f) == 6 && dm_audio_fifo_space(&f) == 2);
    CHECK(dm_audio_fifo_read(&f, output, 3) == 3);
    CHECK(memcmp(output, input, 3 * sizeof *output) == 0);
    CHECK(atomic_load(&f.read_frame) == UINT32_MAX);
    CHECK(dm_audio_fifo_write(&f, input + 6, 5) == 5);
    CHECK(dm_audio_fifo_available(&f) == 8 && dm_audio_fifo_space(&f) == 0);
    CHECK(dm_audio_fifo_read(&f, output, 8) == 8);
    CHECK(memcmp(output, input + 3, sizeof output) == 0);
    CHECK(atomic_load(&f.read_frame) == 7 && atomic_load(&f.write_frame) == 7);
    CHECK(dm_audio_fifo_available(&f) == 0 && dm_audio_fifo_space(&f) == 8);
    return 0;
}

enum { THREAD_CAPACITY = 64, THREAD_FRAMES = 100000 };
typedef struct { uint32_t sequence, channel1, channel2; } test_frame;
typedef struct {
    dm_audio_fifo fifo;
    test_frame storage[THREAD_CAPACITY];
    _Atomic int failed;
} thread_test;

static uint32_t random_next(uint32_t *state) {
    *state = *state * UINT32_C(1664525) + UINT32_C(1013904223);
    return *state;
}

static void *produce_frames(void *arg) {
    thread_test *t = arg;
    uint32_t done = 0, seed = 5;
    while (done < THREAD_FRAMES && !atomic_load(&t->failed)) {
        test_frame batch[41];
        uint32_t want = 1 + random_next(&seed) % 41;
        if (want > THREAD_FRAMES - done) want = THREAD_FRAMES - done;
        for (uint32_t i = 0; i < want; i++) {
            uint32_t seq = done + i;
            batch[i] = (test_frame){seq, seq ^ UINT32_C(0x73a91ec4), ~seq};
        }
        if (dm_audio_fifo_space(&t->fifo) > THREAD_CAPACITY) { atomic_store(&t->failed, 1); break; }
        uint32_t wrote = dm_audio_fifo_write(&t->fifo, batch, want);
        done += wrote;
        if (!wrote) sched_yield();
    }
    return NULL;
}

static void *consume_frames(void *arg) {
    thread_test *t = arg;
    uint32_t done = 0, seed = 91;
    while (done < THREAD_FRAMES && !atomic_load(&t->failed)) {
        test_frame batch[37];
        uint32_t want = 1 + random_next(&seed) % 37;
        if (dm_audio_fifo_available(&t->fifo) > THREAD_CAPACITY) { atomic_store(&t->failed, 1); break; }
        uint32_t got = dm_audio_fifo_read(&t->fifo, batch, want);
        for (uint32_t i = 0; i < got; i++) {
            uint32_t seq = done + i;
            if (batch[i].sequence != seq || batch[i].channel1 != (seq ^ UINT32_C(0x73a91ec4)) || batch[i].channel2 != ~seq) {
                atomic_store(&t->failed, 1); return NULL;
            }
        }
        done += got;
        if (!got) sched_yield();
    }
    return NULL;
}

static int test_threads(void) {
    thread_test t;
    pthread_t producer, consumer;
    CHECK(dm_audio_fifo_init(&t.fifo, t.storage, THREAD_CAPACITY, sizeof *t.storage));
    atomic_init(&t.failed, 0);
    // Cross uint32_t wrap while both endpoints are active as well.
    atomic_store(&t.fifo.read_frame, UINT32_MAX - 511);
    atomic_store(&t.fifo.write_frame, UINT32_MAX - 511);
    CHECK(pthread_create(&producer, NULL, produce_frames, &t) == 0);
    if (pthread_create(&consumer, NULL, consume_frames, &t)) {
        atomic_store(&t.failed, 1); pthread_join(producer, NULL); return 1;
    }
    CHECK(pthread_join(producer, NULL) == 0);
    CHECK(pthread_join(consumer, NULL) == 0);
    CHECK(!atomic_load(&t.failed));
    CHECK(dm_audio_fifo_available(&t.fifo) == 0 && dm_audio_fifo_space(&t.fifo) == THREAD_CAPACITY);
    CHECK(atomic_load(&t.fifo.read_frame) == (uint32_t)(UINT32_MAX - 511 + (uint32_t)THREAD_FRAMES));
    return 0;
}

int main(void) {
    if (test_init() || test_frames() || test_counter_wrap() || test_threads()) return 1;
    puts("PCM FIFO: validation, partial/empty/full, complete frames, storage/counter wrap, reset and 100000 concurrent frames PASS");
    return 0;
}
