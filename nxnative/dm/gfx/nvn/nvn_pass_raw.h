// Opt-in raw attachment readbacks on the replay timeline. Included beside ts_note/ts_after.
// DM_NVN_PASS_RAW=first-last:1-based-pass-ordinals:absolute-directory
// Full attachments: at most four frames. With DM_NVN_PASS_RAW_ROI=x,y,w,h (at most 65536 pixels): at most 120.
// DM_NVN_PASS_RAW_MAX_MB (1..1024, default 128) caps the whole capture's buffer bytes, including queued disk writes.
// No GPU waits or disk I/O on replay / feedback threads; the owning submission's feedback releases a serial utility writer.
@interface NvPassRawAttachment : NSObject {
@public id<MTLTexture> texture; NSDictionary *info; NSUInteger level, slice, plane, layers; int kind, slot, store, forced;
} @end
@implementation NvPassRawAttachment @end
static int g_raw_on = -1, g_raw_roi, g_raw_npass, g_raw_keep;
static uint64_t g_raw_first, g_raw_last, g_raw_frame = UINT64_MAX, g_raw_pass_first, g_raw_budget, g_raw_bytes, g_raw_count;
static _Atomic uint64_t g_raw_pending;
static unsigned g_raw_passes[64], g_raw_rect[4];
static NSString *g_raw_dir;
static NSMutableArray<NvPassRawAttachment *> *g_raw_att;
static dispatch_queue_t g_raw_writer;

static int raw_number(const char **p, uint64_t *v) {
    if (**p < '0' || **p > '9') return 0;
    errno = 0; char *end; *v = strtoull(*p, &end, 10); if (errno) return 0; *p = end; return 1;
}
static int raw_init(void) {
    if (g_raw_on >= 0) return g_raw_on;
    g_raw_on = 0; const char *s = getenv("DM_NVN_PASS_RAW"); if (!s || !*s) return 0;
    const char *p = s; uint64_t first, last, v;
    if (!raw_number(&p, &first)) goto bad;
    last = first; if (*p == '-') { p++; if (!raw_number(&p, &last)) goto bad; }
    if (*p++ != ':' || last < first) goto bad;
    do { if (!raw_number(&p, &v) || !v || v > 4096 || g_raw_npass == 64) goto bad; g_raw_passes[g_raw_npass++] = (unsigned)v; } while (*p == ',' && ++p);
    if (*p++ != ':' || *p != '/') goto bad;
    const char *r = getenv("DM_NVN_PASS_RAW_ROI");
    if (r) { for (int i = 0; i < 4; i++) { if (!raw_number(&r, &v) || v > UINT32_MAX || (i < 3 ? *r++ != ',' : *r != 0)) goto bad; g_raw_rect[i] = (unsigned)v; }
        if (!g_raw_rect[2] || !g_raw_rect[3] || (uint64_t)g_raw_rect[2] * g_raw_rect[3] > 65536) goto bad; g_raw_roi = 1; }
    if (last - first >= (g_raw_roi ? 120u : 4u)) goto bad;
    g_raw_budget = 128ull << 20; const char *b = getenv("DM_NVN_PASS_RAW_MAX_MB");
    if (b) { if (!raw_number(&b, &v) || *b || v < 1 || v > 1024) goto bad; g_raw_budget = v << 20; }
    g_raw_dir = @(p);
    g_raw_first = first; g_raw_last = last; g_raw_att = [NSMutableArray array];
    g_raw_keep = getenv("DM_NVN_PASS_RAW_KEEP") && !strcmp(getenv("DM_NVN_PASS_RAW_KEEP"), "1");
    g_raw_writer = dispatch_queue_create("dm-pass-raw-write", dispatch_queue_attr_make_with_qos_class(DISPATCH_QUEUE_SERIAL, QOS_CLASS_UTILITY, 0));
    g_raw_on = 1; return 1;
bad:
    MLOG("[RED] PASS_RAW: invalid selection; use first-last:1-based-passes:/directory (at most 4 full frames, or 120 with ROI <=65536 pixels; MAX_MB 1..1024)"); return 0;
}
static void raw_finish(NSDictionary *info, id<MTLBuffer> buffer, id<MTLTexture> source) {
    NSString *dir = g_raw_dir;
    atomic_fetch_add(&g_raw_pending, 1);
    [g_commit_blocks addObject:^{
        (void)source;   // Metal 4 does not retain the source allocation for us.
        dispatch_async(g_raw_writer, ^{ @autoreleasepool {
            NSError *error = nil;
            if (![[NSFileManager defaultManager] createDirectoryAtPath:dir withIntermediateDirectories:YES attributes:nil error:&error])
                MLOG("[RED] PASS_RAW: cannot create output directory: %s", error.localizedDescription.UTF8String);
            NSMutableDictionary *record = [info mutableCopy];
            if (buffer) {
                NSString *file = [NSString stringWithFormat:@"F%@_p%@_%@_ser%@_m%@_s%@_z%@.bin", info[@"frame"], info[@"pass"], info[@"attachment"], info[@"serial"], info[@"mip"], info[@"slice"], info[@"depth_plane"]];
                NSString *path = [dir stringByAppendingPathComponent:file]; FILE *f = fopen(path.fileSystemRepresentation, "wb");
                int ok = f && fwrite(buffer.contents, 1, buffer.length, f) == buffer.length; if (f && fclose(f)) ok = 0;
                record[@"status"] = ok ? @"captured" : @"write_failed"; record[@"file"] = file;
                if (!ok) MLOG("[RED] PASS_RAW: cannot write %s", path.fileSystemRepresentation);
                res_remove(buffer);
            }
            NSData *json = [NSJSONSerialization dataWithJSONObject:record options:NSJSONWritingSortedKeys error:nil];
            NSString *manifest = [dir stringByAppendingPathComponent:@"manifest.jsonl"]; FILE *f = fopen(manifest.fileSystemRepresentation, "ab");
            if (f) { if (json) fwrite(json.bytes, 1, json.length, f); fputc('\n', f); fclose(f); }
            else MLOG("[RED] PASS_RAW: cannot append manifest %s", manifest.fileSystemRepresentation);
            atomic_fetch_sub(&g_raw_pending, 1);
        }});
    }];
}
static NSDictionary *raw_provenance(const tex_t *t) {
    if (!t) return @{};
    return @{@"serial":@(t->serial), @"pool":[NSString stringWithFormat:@"%p", t->pool], @"pool_memory":[NSString stringWithFormat:@"%p", t->pool ? t->pool->mem : NULL],
             @"pool_offset":@(t->off), @"storage_bytes":@(t->L.size), @"nvn_format":@(t->d.format), @"nvn_target":@(t->d.target),
             @"nvn_width":@(t->d.width), @"nvn_height":@(t->d.height), @"nvn_depth":@(t->d.depth), @"nvn_levels":@(t->d.levels), @"nvn_flags":@(t->d.flags)};
}
static void raw_attachment(MTLRenderPassAttachmentDescriptor *a, const tex_t *t, NSString *name, int kind, NSUInteger layers, uint64_t frame, unsigned ordinal) {
    if (!a.texture) return;
    NvPassRawAttachment *e = [NvPassRawAttachment new]; e->texture = a.texture; e->level = a.level; e->slice = a.slice; e->plane = a.depthPlane; e->layers = layers; e->kind = kind;
    e->slot = kind ? -1 : [name substringFromIndex:1].intValue; e->store = (int)a.storeAction;
    NSMutableDictionary *info = [raw_provenance(t) mutableCopy]; id<MTLTexture> tx = a.texture;
    [info addEntriesFromDictionary:@{@"schema":@"nvn-pass-raw-v1", @"byte_order":@"little", @"frame":@(frame), @"pass":@(ordinal), @"pass_serial":@(g_stat_pass + 1), @"attachment":name,
        @"mtl":[NSString stringWithFormat:@"%p", (__bridge void *)tx], @"mtl_parent":[NSString stringWithFormat:@"%p", (__bridge void *)tx.parentTexture],
        @"pixel_format":@(tx.pixelFormat), @"texture_type":@(tx.textureType), @"texture_width":@(tx.width), @"texture_height":@(tx.height), @"texture_depth":@(tx.depth),
        @"texture_mips":@(tx.mipmapLevelCount), @"array_length":@(tx.arrayLength), @"samples":@(tx.sampleCount), @"mip":@(a.level), @"slice":@(a.slice), @"depth_plane":@(a.depthPlane),
        @"parent_mip":@(tx.parentTexture ? tx.parentRelativeLevel : 0), @"parent_slice":@(tx.parentTexture ? tx.parentRelativeSlice : 0), @"pass_layers":@(layers),
        @"load_action":@(a.loadAction), @"store_action":@(a.storeAction)}];
    e->info = info; [g_raw_att addObject:e];
    if (g_raw_keep && a.storeAction != MTLStoreActionStore && a.texture.storageMode != MTLStorageModeMemoryless && a.texture.sampleCount == 1)
        a.storeAction = MTLStoreActionUnknown;   // only Unknown permits a later explicit KEEP override
}
static void raw_note(MTL4RenderPassDescriptor *pd, NSUInteger nc) {
    if (__builtin_expect(!raw_init(), 1)) return;
    [g_raw_att removeAllObjects]; uint64_t frame = atomic_load(&g_cur_frame);
    if (frame != g_raw_frame) { g_raw_frame = frame; g_raw_pass_first = g_stat_pass; }
    if (frame < g_raw_first || frame > g_raw_last) return;
    uint64_t ordinal = g_stat_pass - g_raw_pass_first + 1; int selected = 0;
    for (int i = 0; i < g_raw_npass; i++) selected |= ordinal == g_raw_passes[i];
    if (!selected) return;
    NSUInteger layers = MAX(pd.renderTargetArrayLength, 1ul);
    for (NSUInteger i = 0; i < nc && i < 8; i++) raw_attachment(pd.colorAttachments[i], R.rt.color[i], [NSString stringWithFormat:@"c%lu", (unsigned long)i], 0, layers, frame, (unsigned)ordinal);
    raw_attachment(pd.depthAttachment, R.rt.depth, @"depth", 1, layers, frame, (unsigned)ordinal);
    raw_attachment(pd.stencilAttachment, R.rt.depth, @"stencil", 2, layers, frame, (unsigned)ordinal);
}
static void raw_store_action(int kind, int slot, MTLStoreAction action) {
    if (g_raw_on != 1 || !g_raw_att.count) return;
    for (NvPassRawAttachment *e in g_raw_att) if (e->kind == kind && e->slot == slot) e->store = (int)action;
}
static void raw_store(void) {
    if (g_raw_on != 1 || !g_raw_att.count) return;
    for (NvPassRawAttachment *e in g_raw_att) {
        if (!g_raw_keep || e->store == MTLStoreActionStore || e->texture.storageMode == MTLStorageModeMemoryless || e->texture.sampleCount > 1) continue;
        if (e->kind == 1) [g_enc setDepthStoreAction:MTLStoreActionStore];
        else if (e->kind == 2) [g_enc setStencilStoreAction:MTLStoreActionStore];
        else [g_enc setColorStoreAction:MTLStoreActionStore atIndex:(NSUInteger)e->slot];
        e->forced = 1;
    }
}
static void raw_after(void) {
    if (g_raw_on != 1 || !g_raw_att.count) return;
    NSArray *attachments = [g_raw_att copy]; [g_raw_att removeAllObjects];   // blit() calls rp_end again.
    for (NvPassRawAttachment *e in attachments) for (NSUInteger layer = 0; layer < e->layers; layer++) {
        id<MTLTexture> t = e->texture; NSUInteger lv = e->level, sl = e->slice, z = e->plane;
        if (t.textureType == MTLTextureType3D) z += layer; else sl += layer;
        NSMutableDictionary *info = [e->info mutableCopy]; info[@"slice"] = @(sl); info[@"depth_plane"] = @(z); info[@"fence"] = @(g_fence + 1);
        info[@"final_store_action"] = @(e->store); info[@"capture_forced_store"] = @(e->forced);
        const char *why = NULL; NSUInteger bpp = mtl_pf_bytes(t.pixelFormat); MTLBlitOption option = MTLBlitOptionNone;
        if (e->kind == 1) { bpp = t.pixelFormat == MTLPixelFormatDepth16Unorm ? 2 : t.pixelFormat == MTLPixelFormatDepth32Float || t.pixelFormat == MTLPixelFormatDepth32Float_Stencil8 ? 4 : 0;
            if (t.pixelFormat == MTLPixelFormatDepth32Float_Stencil8) option = MTLBlitOptionDepthFromDepthStencil; }
        if (e->kind == 2) { bpp = t.pixelFormat == MTLPixelFormatStencil8 || t.pixelFormat == MTLPixelFormatDepth32Float_Stencil8 || t.pixelFormat == MTLPixelFormatX32_Stencil8 ? 1 : 0;
            if (t.pixelFormat == MTLPixelFormatDepth32Float_Stencil8) option = MTLBlitOptionStencilFromDepthStencil; }
        NSUInteger w = lv < t.mipmapLevelCount ? MAX(t.width >> lv, 1ul) : 0, h = lv < t.mipmapLevelCount ? MAX(t.height >> lv, 1ul) : 0;
        NSUInteger x = g_raw_roi ? g_raw_rect[0] : 0, y = g_raw_roi ? g_raw_rect[1] : 0;
        NSUInteger rw = g_raw_roi ? MIN((NSUInteger)g_raw_rect[2], w > x ? w - x : 0) : w, rh = g_raw_roi ? MIN((NSUInteger)g_raw_rect[3], h > y ? h - y : 0) : h;
        NSUInteger slices = t.arrayLength * (t.textureType == MTLTextureTypeCube || t.textureType == MTLTextureTypeCubeArray ? 6 : 1);
        if (t.sampleCount > 1 || t.framebufferOnly || t.storageMode == MTLStorageModeMemoryless) why = "multisample/framebuffer-only/memoryless attachment";
        else if (!bpp) why = "unsupported raw pixel format/plane";
        else if (e->store != MTLStoreActionStore && !e->forced) why = "attachment not stored; KEEP=1 explicitly forces diagnostic Store";
        else if (!w || !rw || !rh || sl >= slices || (t.textureType == MTLTextureType3D && z >= MAX(t.depth >> lv, 1ul))) why = "attachment range or ROI outside actual mip/slice";
        NSUInteger row = (rw * bpp + 255) & ~255ul, bytes = row * rh;
        if (!why && (g_raw_count >= 8192 || bytes > g_raw_budget - g_raw_bytes)) why = "capture count/total buffer budget exhausted";
        if (why) { info[@"status"] = @"refused"; info[@"reason"] = @(why); info[@"remaining_layers"] = @(e->layers - layer); raw_finish(info, nil, nil); break; }
        id<MTLBuffer> buffer = [g_dev newBufferWithLength:bytes options:MTLResourceStorageModeShared];
        if (!buffer) { info[@"status"] = @"refused"; info[@"reason"] = @"readback allocation failed"; raw_finish(info, nil, nil); continue; }
        g_raw_count++; g_raw_bytes += bytes; res_add(buffer);
        info[@"x"] = @(x); info[@"y"] = @(y); info[@"width"] = @(rw); info[@"height"] = @(rh); info[@"bytes_per_pixel"] = @(bpp);
        info[@"buffer_offset"] = @0; info[@"bytes_per_row"] = @(row); info[@"bytes_per_image"] = @(bytes); info[@"destination_bytes_per_image"] = @0;
        info[@"buffer_bytes"] = @(bytes); info[@"blit_option"] = @(option);
        [blit() copyFromTexture:t sourceSlice:sl sourceLevel:lv sourceOrigin:MTLOriginMake(x, y, z) sourceSize:MTLSizeMake(rw, rh, 1) toBuffer:buffer destinationOffset:0 destinationBytesPerRow:row destinationBytesPerImage:0 options:option];
        raw_finish(info, buffer, t);
    }
}
