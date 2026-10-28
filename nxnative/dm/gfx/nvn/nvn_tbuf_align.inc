/* Read-only buffer textures below Metal's required offset alignment.
 * Copy at the consumer's place in the GPU stream, before opening its encoder.
 * Sources remain live GPU storage: no CPU snapshot, serial-cache replacement or
 * wait. Scratch arenas and texture retirement use the final submission fence,
 * including all sub_cut segments. A new submission/segment or GPU producer
 * invalidates the cache. Aligned bindings retain their ordinary zero-copy path.
 */
typedef struct {
    uint64_t source, offset, bytes;
    uint32_t format, width, height, row;
    uint8_t swizzle[4], rows, pad[3];
} tba_key;
typedef struct { tba_key key; int tid; __unsafe_unretained id<MTLBuffer> source; } tba_req;
static nvn_hmap g_tba_cache = {.klen = sizeof(tba_key)}, g_tba_bound = {.klen = 8};
static tba_req *g_tba_req; static size_t g_tba_n, g_tba_cap;
static uint64_t g_tba_sub, g_tba_segment, g_tba_writes, g_tba_shwrites, g_tba_pool;
static int g_tba_pending;
static _Atomic uint64_t g_tba_copies, g_tba_bytes, g_tba_reuse, g_tba_refused;
static void tba_retire(void *value) { id texture = CFBridgingRelease(value); defer_obj_release(texture); }
static void tbuf_align_reset(void) {
    nvn_hm_clear(&g_tba_bound, NULL); nvn_hm_clear(&g_tba_cache, tba_retire);
}
static void tba_epoch(void) {
    uint64_t pool = atomic_load(&g_pool_gen);
    if (g_tba_sub != g_sub_serial || g_tba_segment != g_seg || g_tba_writes != g_wr_epoch ||
        g_tba_shwrites != g_sbw_gen || g_tba_pool != pool || g_tba_pending != g_npend) {
        tbuf_align_reset(); g_tba_sub = g_sub_serial; g_tba_segment = g_seg;
        g_tba_writes = g_wr_epoch; g_tba_shwrites = g_sbw_gen; g_tba_pool = pool; g_tba_pending = g_npend;
    }
}
// The device's answer per format never changes, and this runs for every buffer-texture binding of every draw: ask once per format.
static NSUInteger tba_align_mtl(MTLPixelFormat pf) {
    static uint32_t cache[1024];
    if ((uint32_t)pf >= sizeof cache / sizeof cache[0]) return [g_dev minimumTextureBufferAlignmentForPixelFormat:pf];
    uint32_t a = cache[pf];
    if (!a) { a = (uint32_t)[g_dev minimumTextureBufferAlignmentForPixelFormat:pf]; cache[pf] = a; }
    return (NSUInteger)a;
}
static int tba_refuse(int tid, const char *why) {
    uint64_t n = atomic_fetch_add(&g_tba_refused, 1);
    if (n < 8) MLOG("[RED] unaligned buffer texture ID %d cannot be sampled: %s (consumer refused)", tid, why);
    return 0;
}
// Match the texture IDs encode_args resolves, including every array element.
static int tba_read_tid(NvShader *sh, const nvn_rbind *b, unsigned element) {
    unsigned st = sh->stage, reg = b->reg; uint64_t h = 0;
    switch (b->kind) {
        case NVN_RB_TBUF: case NVN_RB_TEX:
            if (reg < NTEX) h = tex_slot_h(S.tex[st], S.img[st], reg); break;
        case NVN_RB_TEXU:
            return ubo_tex_id(ubo_handle(st, reg, b->n > 1 ? 8 * element : b->off));
        case NVN_RB_TEXD:
            if (element < 128) h = S.tex[st][element] ? S.tex[st][element] : S.tex[st][NVN_SEPTEX_SLOT0 + element]; break;
        case NVN_RB_TEXA: h = c2_handle(st, b->off + 8 * element); break;
        default: break;
    }
    return (int)(h & 0xfffff);
}
static int tba_is_reader(const nvn_rbind *b) {
    return b->kind == NVN_RB_TBUF || ((b->kind == NVN_RB_TEX || b->kind == NVN_RB_TEXU ||
        b->kind == NVN_RB_TEXD || b->kind == NVN_RB_TEXA) && (b->dtt == MTLTextureTypeTextureBuffer || b->f1d));
}
// Same-consumer writes cannot use a pre-consumer snapshot of their alias.
// Unknown writable pointers refuse the snapshot instead of claiming independence.
static int tba_writer_alias(NvShader *const *shaders, unsigned count, uint64_t lo, uint64_t hi, int graphics) {
    for (unsigned s = 0; s < count; s++) { NvShader *sh = shaders[s]; if (!sh) continue;
        for (int j = 0; j < sh->rf.nb; j++) { const nvn_rbind *b = &sh->rf.b[j];
            if (!b->rw) continue; uint64_t address = 0, length = 0;
            if (b->kind == NVN_RB_SBUFP) {
                if (b->reg >= 16 || (sh->sb_ro >> b->reg & 1)) continue;
                address = S.ssbo[sh->stage][b->reg].addr; length = S.ssbo[sh->stage][b->reg].size;
            } else if (b->kind == NVN_RB_SBUFU) return 1;
            else if (b->kind == NVN_RB_IMG || b->kind == NVN_RB_IMGA) {
                int tid = b->reg < 32 ? (int)(S.img[sh->stage][b->reg] & 0xfffff) : 0;
                if (tid > 0 && tid < g_ntreg && g_treg[tid].ok) { const tex_t *t = &g_treg[tid].t; address = (uint64_t)(uintptr_t)t->mem; length = t->L.size; }
            } else continue;
            if (address && (!length || length > UINT64_MAX - address || nvn_sparse_alias(address, address + length) ||
                (lo < address + length && address < hi))) return 1;
        }
    }
    if (graphics) for (int i = 0; i < R.rt.n + 1 && i < 9; i++) {
        const tex_t *t = i < R.rt.n ? R.rt.color[i] : R.rt.depth; if (!t || !t->mem) continue;
        uint64_t a = (uint64_t)(uintptr_t)t->mem, n = t->L.size;
        if (n && (n > UINT64_MAX - a || (lo < a + n && a < hi))) return 1;
    }
    return 0;
}
// Caller holds g_rmu. Resolve only descriptions and source buffers here; the
// old tex_by_id path must never first create an illegal Metal texture view.
static int tba_collect(int tid, const nvn_rbind *binding, NvShader *const *shaders, unsigned count, int graphics) {
    if (tid <= 0 || tid >= g_ntreg || !g_treg[tid].ok || (g_tpool_n && tid >= g_tpool_n)) return 1;
    const treg_t *r = &g_treg[tid]; const tex_t *t = &r->t;
    if (t->d.target != NVN_TEXTURE_TARGET_BUFFER) return 1;
    uint32_t pf = 0; uint8_t fsw[4]; int bw, bh, bpb = nvn_fmt_bpb(t->d.format, &bw, &bh);
    if (!nvn_fmt_to_mtl(t->d.format, &pf, fsw) || !pf || bpb <= 0 || bw != 1 || bh != 1) return tba_refuse(tid, "unsupported buffer format");
    uint64_t n = (uint64_t)t->d.width * (unsigned)bpb, address = (uint64_t)(uintptr_t)t->mem;
    void *mb = NULL; uint64_t offset = 0;
    if (!address || !n || n > UINT64_MAX - address || !mtl_addr(address, n, &mb, &offset)) return tba_refuse(tid, "source range is not in a live memory pool");
    NSUInteger alignment = tba_align_mtl((MTLPixelFormat)pf);
    int rows = binding->f1d && binding->dtt == MTLTextureType2D;
    if (rows) alignment = MAX(alignment, [g_dev minimumLinearTextureAlignmentForPixelFormat:(MTLPixelFormat)pf]);
    if (!(offset % alignment)) return 1;
    if ((offset & 3) || (n & 3)) return tba_refuse(tid, "GPU buffer copies require four-byte source offset and size");
    if (nvn_sparse_alias(address, address + n)) return tba_refuse(tid, "sparse virtual source storage is not directly addressable");
    if (tba_writer_alias(shaders, count, address, address + n, graphics)) return tba_refuse(tid, "source aliases a writable resource of this consumer");
    if (r->has_v && ((r->v.set_levels && (r->v.base_level || r->v.nlevels > 1)) ||
        (r->v.set_layers && (r->v.min_layer || r->v.nlayers > 1)) || (r->v.set_target && r->v.target != NVN_TEXTURE_TARGET_BUFFER)))
        return tba_refuse(tid, "buffer view changes its level, layer or target");
    if (r->has_v && r->v.set_format) {
        int vw, vh, bytes = nvn_fmt_bpb(r->v.format, &vw, &vh);
        if (vw != 1 || vh != 1 || bytes != bpb || !nvn_fmt_to_mtl(r->v.format, &pf, fsw) || !pf) return tba_refuse(tid, "buffer format view changes its element size");
    }
    tba_key key = {0}; key.source = (uint64_t)(uintptr_t)mb; key.offset = offset; key.bytes = n; key.format = pf;
    key.width = t->d.width; key.height = 1; key.rows = rows;
    uint8_t sw[4] = {2,3,4,5};
    if (r->has_v && r->v.set_swizzle) memcpy(sw, r->v.swz, 4);
    else if (t->set_swz) for (int c = 0; c < 4; c++) sw[c] = (uint8_t)(t->swz >> (3*c) & 7);
    for (int c = 0; c < 4; c++) { if (sw[c] > 5) sw[c] = 2 + c; key.swizzle[c] = sw[c] >= 2 ? fsw[sw[c] - 2] : sw[c]; }
    NSUInteger al = rows ? [g_dev minimumLinearTextureAlignmentForPixelFormat:(MTLPixelFormat)pf] : tba_align_mtl((MTLPixelFormat)pf);
    if (rows) { if (!nvn_tbuf_rows(t->d.width, bpb, (uint32_t)al, &key.width, &key.height, &key.row)) return tba_refuse(tid, "wrapped row layout is unsupported"); }
    else key.row = (uint32_t)((n + al - 1) / al * al);
    for (size_t i = 0; i < g_tba_n; i++) if (g_tba_req[i].tid == tid && g_tba_req[i].key.rows == rows) return 1;
    if (g_tba_n == g_tba_cap) { size_t cap = g_tba_cap ? g_tba_cap * 2 : 32; void *p = realloc(g_tba_req, cap * sizeof *g_tba_req); if (!p) return tba_refuse(tid, "request allocation failed"); g_tba_req = p; g_tba_cap = cap; }
    g_tba_req[g_tba_n++] = (tba_req){key, tid, (__bridge id<MTLBuffer>)mb}; return 1;
}
static int tbuf_align_prepare(NvShader *const *shaders, unsigned count, int graphics) {
    int previous = g_tba_bound.n != 0; nvn_hm_clear(&g_tba_bound, NULL); g_tba_n = 0; tba_epoch();
    os_unfair_lock_lock(&g_rmu); int ok = 1;
    for (unsigned s = 0; s < count && ok; s++) { NvShader *sh = shaders[s]; if (!sh || sh->stage >= 6) continue;
        for (int j = 0; j < sh->rf.nb && ok; j++) { const nvn_rbind *b = &sh->rf.b[j]; if (!tba_is_reader(b)) continue;
            unsigned n = b->n ? b->n : 1;
            for (unsigned k = 0; k < n && ok; k++) ok = tba_collect(tba_read_tid(sh,b,k), b, shaders,count,graphics);
        }
    }
    os_unfair_lock_unlock(&g_rmu);
    if (!ok) { if (previous) args_dirty_all(); return 0; }
    if (!g_tba_n) { if (previous) args_dirty_all(); return 1; }
    int missing = 0; for (size_t i = 0; i < g_tba_n; i++) missing |= nvn_hm_get(&g_tba_cache, &g_tba_req[i].key) == NULL;
    if (missing) {
        // Ending a pass may land deferred updates. All prior producers must run
        // before these copies, and every copy must finish before the consumer.
        rp_end(); ubd_land(); tba_epoch();
    }
    for (size_t i = 0; i < g_tba_n; i++) { const tba_req *r = &g_tba_req[i]; id<MTLTexture> texture = hm_get_id(&g_tba_cache, &r->key);
        if (texture) atomic_fetch_add(&g_tba_reuse, 1);
        else {
            g_blit_alias = 1; id<MTL4ComputeCommandEncoder> ce = blit(); g_blit_alias = 1;
            id<MTLBuffer> scratch; size_t bytes = (size_t)r->key.row * r->key.height, off = arena_alloc(bytes, &scratch);
            if (off % [g_dev minimumLinearTextureAlignmentForPixelFormat:(MTLPixelFormat)r->key.format]) return tba_refuse(r->tid, "scratch arena offset is not aligned");
            if (bytes > r->key.bytes) { [ce fillBuffer:scratch range:NSMakeRange(off,bytes) value:0]; ce = blit(); }
            enc_b_copy_buffer(ce, r->source, r->key.offset, scratch, off, r->key.bytes);
            MTLTextureDescriptor *d = r->key.rows ? [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:(MTLPixelFormat)r->key.format width:r->key.width height:r->key.height mipmapped:NO] :
                [MTLTextureDescriptor textureBufferDescriptorWithPixelFormat:(MTLPixelFormat)r->key.format width:r->key.width resourceOptions:scratch.resourceOptions usage:MTLTextureUsageShaderRead];
            d.resourceOptions = scratch.resourceOptions; d.usage = MTLTextureUsageShaderRead;
            d.swizzle = MTLTextureSwizzleChannelsMake(r->key.swizzle[0],r->key.swizzle[1],r->key.swizzle[2],r->key.swizzle[3]);
            texture = [scratch newTextureWithDescriptor:d offset:off bytesPerRow:r->key.row];
            if (!texture) return tba_refuse(r->tid, "aligned GPU scratch texture allocation failed");
            res_add(texture); hm_put_id(&g_tba_cache, &r->key, texture);
            atomic_fetch_add(&g_tba_copies,1); atomic_fetch_add(&g_tba_bytes,r->key.bytes);
        }
        uint64_t slot = (uint32_t)r->tid | ((uint64_t)r->key.rows << 32);
        nvn_hm_put(&g_tba_bound, &slot, (__bridge void *)texture);
    }
    args_dirty_all(); return 1;
}
// Only sampling paths call this. Images retain their original backing; an
// unaligned writable image is explicitly unsupported by tex_mtl.
static uint64_t sample_tex_rid(int tid, const nvn_rbind *b, void **out) {
    uint64_t slot = (uint32_t)tid | ((uint64_t)(b->f1d && b->dtt == MTLTextureType2D) << 32);
    id<MTLTexture> t = (__bridge id<MTLTexture>)nvn_hm_get(&g_tba_bound,&slot);
    if (t) { if (out) *out = (__bridge void *)t; return t.gpuResourceID._impl; }
    return tex_fast_rid(tid,out);
}
static void tbuf_align_stat(uint64_t frame) {
    static uint64_t last; if (frame < last + 300 || !atomic_load(&g_tba_copies)) return; last = frame;
    MLOG("aligned buffer sampling up to frame %llu: GPU copies %llu / %llu bytes, cache reuses %llu, unsupported consumers %llu (cumulative)",
        (unsigned long long)frame,(unsigned long long)atomic_load(&g_tba_copies),(unsigned long long)atomic_load(&g_tba_bytes),
        (unsigned long long)atomic_load(&g_tba_reuse),(unsigned long long)atomic_load(&g_tba_refused));
}
