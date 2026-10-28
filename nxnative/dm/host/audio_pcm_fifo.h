// Fixed-storage PCM FIFO: one producer, one consumer, no work on the RT path
// other than lock-free counter loads/stores and copies of complete frames.
#ifndef DM_AUDIO_PCM_FIFO_H
#define DM_AUDIO_PCM_FIFO_H

#include <stddef.h>
#include <stdint.h>
#include <stdatomic.h>
#include <string.h>

typedef struct dm_audio_fifo {
    uint8_t *storage;
    uint32_t capacity_frames, bytes_per_frame;
    _Atomic uint32_t read_frame, write_frame;
} dm_audio_fifo;

// Returns one on success. Capacity is a power of two below the uint32_t
// half range; callers own at least capacity_frames * bytes_per_frame bytes.
// Storage and source/destination regions must not overlap. Initialization and
// reset require both endpoints to be quiescent; neither allocates storage.
static inline int dm_audio_fifo_init(dm_audio_fifo *f, void *storage,
                                     uint32_t capacity_frames, uint32_t bytes_per_frame) {
    if (!f || !storage || !capacity_frames ||
        (capacity_frames & (capacity_frames - 1)) ||
        capacity_frames > UINT32_MAX / 2 || !bytes_per_frame ||
        (size_t)capacity_frames > SIZE_MAX / bytes_per_frame) return 0;
    atomic_init(&f->read_frame, 0);
    atomic_init(&f->write_frame, 0);
    if (!atomic_is_lock_free(&f->read_frame) || !atomic_is_lock_free(&f->write_frame)) return 0;
    f->storage = storage;
    f->capacity_frames = capacity_frames;
    f->bytes_per_frame = bytes_per_frame;
    return 1;
}

// Consumer-side observation (or either side while quiescent). The producer
// can publish more frames immediately after this snapshot.
static inline uint32_t dm_audio_fifo_available(const dm_audio_fifo *f) {
    uint32_t r = atomic_load_explicit(&f->read_frame, memory_order_relaxed);
    uint32_t w = atomic_load_explicit(&f->write_frame, memory_order_acquire);
    return w - r;
}

// Producer-side observation (or either side while quiescent).
static inline uint32_t dm_audio_fifo_space(const dm_audio_fifo *f) {
    uint32_t w = atomic_load_explicit(&f->write_frame, memory_order_relaxed);
    uint32_t r = atomic_load_explicit(&f->read_frame, memory_order_acquire);
    return f->capacity_frames - (w - r);
}

// Publish as many complete frames as fit, returning the count copied.
static inline uint32_t dm_audio_fifo_write(dm_audio_fifo *f, const void *src, uint32_t frames) {
    if (!frames || !src) return 0;
    uint32_t w = atomic_load_explicit(&f->write_frame, memory_order_relaxed);
    uint32_t r = atomic_load_explicit(&f->read_frame, memory_order_acquire);
    uint32_t space = f->capacity_frames - (w - r);
    if (frames > space) frames = space;
    if (!frames) return 0;
    uint32_t at = w & (f->capacity_frames - 1);
    uint32_t first = f->capacity_frames - at;
    if (first > frames) first = frames;
    size_t first_bytes = (size_t)first * f->bytes_per_frame;
    memcpy(f->storage + (size_t)at * f->bytes_per_frame, src, first_bytes);
    if (first < frames)
        memcpy(f->storage, (const uint8_t *)src + first_bytes,
               (size_t)(frames - first) * f->bytes_per_frame);
    atomic_store_explicit(&f->write_frame, w + frames, memory_order_release);
    return frames;
}

// Consume as many complete frames as are available. The caller decides how
// to fill any unfulfilled request; bytes beyond the returned count stay intact.
static inline uint32_t dm_audio_fifo_read(dm_audio_fifo *f, void *dst, uint32_t frames) {
    if (!frames || !dst) return 0;
    uint32_t r = atomic_load_explicit(&f->read_frame, memory_order_relaxed);
    uint32_t w = atomic_load_explicit(&f->write_frame, memory_order_acquire);
    uint32_t available = w - r;
    if (frames > available) frames = available;
    if (!frames) return 0;
    uint32_t at = r & (f->capacity_frames - 1);
    uint32_t first = f->capacity_frames - at;
    if (first > frames) first = frames;
    size_t first_bytes = (size_t)first * f->bytes_per_frame;
    memcpy(dst, f->storage + (size_t)at * f->bytes_per_frame, first_bytes);
    if (first < frames)
        memcpy((uint8_t *)dst + first_bytes, f->storage,
               (size_t)(frames - first) * f->bytes_per_frame);
    atomic_store_explicit(&f->read_frame, r + frames, memory_order_release);
    return frames;
}

static inline void dm_audio_fifo_reset(dm_audio_fifo *f) {
    atomic_store_explicit(&f->read_frame, 0, memory_order_relaxed);
    atomic_store_explicit(&f->write_frame, 0, memory_order_relaxed);
}

#endif
