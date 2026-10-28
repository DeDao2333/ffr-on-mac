// Real Metal 4 ordering: clear -> selected raw copies -> overwrite in one production submission.
// Repeat with DM_NVN_ENC_THREAD=1 to exercise the deferred encoder's depth/stencil option forwarding.
#include "../nvn/nvn_mtl.m"
void *front_metal_layer(void) { return NULL; }
module_t *module_of(const void *p) { (void)p; return NULL; }
module_t g_mod[MAX_MOD]; int g_nmod;
void svc_thread_dump(void) {}
uint64_t now_ns(void) { return bp_now(); }
static int cases, failures;
static int layered;
#define CHECK(c, message) do { cases++; if (!(c)) { failures++; fprintf(stderr, "FAIL: %s\n", message); } } while (0)
static tex_t color = {.serial=77100}, depth = {.serial=77101};
static MTL4RenderPassDescriptor *pass(id<MTLTexture> c, id<MTLTexture> d, double red, double z, unsigned stencil) {
    MTL4RenderPassDescriptor *p = [MTL4RenderPassDescriptor new];
    if (layered) p.renderTargetArrayLength = 2;
    p.colorAttachments[0].texture = c; p.colorAttachments[0].level = 1; p.colorAttachments[0].slice = 2;
    p.colorAttachments[0].loadAction = MTLLoadActionClear; p.colorAttachments[0].storeAction = MTLStoreActionStore; p.colorAttachments[0].clearColor = MTLClearColorMake(red, 0, 0, 1);
    p.depthAttachment.texture = p.stencilAttachment.texture = d;
    p.depthAttachment.level = p.stencilAttachment.level = 1; p.depthAttachment.slice = p.stencilAttachment.slice = 2;
    p.depthAttachment.loadAction = p.stencilAttachment.loadAction = MTLLoadActionClear;
    p.depthAttachment.storeAction = p.stencilAttachment.storeAction = MTLStoreActionStore;
    p.depthAttachment.clearDepth = z; p.stencilAttachment.clearStencil = stencil;
    return p;
}
static void render(MTL4RenderPassDescriptor *p, int capture) {
    if (g_blit) { enc_b_end(g_blit); g_blit = nil; }
    if (capture) ts_note(p, 1);
    g_enc = enc_r_begin(p); g_stat_pass++;
    if (capture) { color.off = depth.off = 99; ts_before(); enc_r_end(g_enc); g_enc = nil; ts_after(); }   // provenance must come from ts_note's copy
    else { enc_r_end(g_enc); g_enc = nil; }
}
int main(int argc, char **argv) { @autoreleasepool {
    int disabled = argc > 1 && !strcmp(argv[1], "--disabled"), discard = argc > 1 && !strcmp(argv[1], "--discard"), roi = argc > 1 && !strcmp(argv[1], "--roi");
    int invalid = argc > 1 && !strcmp(argv[1], "--invalid"), budget = argc > 1 && !strcmp(argv[1], "--budget");
    layered = argc > 1 && !strcmp(argv[1], "--layered");
    char dir[] = "/tmp/dm-pass-raw-XXXXXX"; CHECK(mkdtemp(dir) != NULL, "temporary output directory");
    NSString *manifest = [@(dir) stringByAppendingPathComponent:@"manifest.jsonl"];
    if (!disabled) { char spec[1024]; snprintf(spec, sizeof spec, "%s:1:%s", invalid ? "10-14" : "10", dir); setenv("DM_NVN_PASS_RAW", spec, 1); } else unsetenv("DM_NVN_PASS_RAW");
    if (invalid) { CHECK(!raw_init() && !g_raw_att && !g_raw_writer && !g_raw_dir, "five full frames rejected before allocation"); [[NSFileManager defaultManager] removeItemAtPath:@(dir) error:nil]; printf("PASS_RAW_GPU cases=%d failed=%d\n",cases,failures); return failures != 0; }
    if (budget) setenv("DM_NVN_PASS_RAW_MAX_MB", "1", 1);
    if (roi) setenv("DM_NVN_PASS_RAW_ROI", "2,1,3,2", 1);
    mtl_init();
    unsigned size = budget ? 1024 : 16;
    MTLTextureDescriptor *d = [MTLTextureDescriptor texture2DDescriptorWithPixelFormat:MTLPixelFormatRGBA8Unorm width:size height:size mipmapped:YES];
    d.textureType = MTLTextureType2DArray; d.arrayLength = 4; d.mipmapLevelCount = 3; d.storageMode = MTLStorageModePrivate; d.usage = MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead;
    id<MTLTexture> c = [g_dev newTextureWithDescriptor:d]; d.pixelFormat = MTLPixelFormatDepth32Float_Stencil8; d.usage |= MTLTextureUsagePixelFormatView;
    id<MTLTexture> z = [g_dev newTextureWithDescriptor:d]; CHECK(c && z, "array color and combined depth/stencil allocations"); res_add(c); res_add(z);
    color.d = (nvn_texdesc){.width=size,.height=size,.depth=4,.levels=3,.format=NVN_FORMAT_RGBA8,.target=NVN_TEXTURE_TARGET_2D_ARRAY};
    depth.d = color.d; depth.d.format = NVN_FORMAT_DEPTH32F_STENCIL8; color.off = 4096; color.L.size = 32768; depth.off = 8192; depth.L.size = 65536;
    R.rt.n = 1; R.rt.color[0] = &color; R.rt.depth = &depth; atomic_store(&g_cur_frame, 10);
    mtl_sub_begin();
    render(pass(c,z,1,.25,0x2d), 0);   // distinct previous value
    MTL4RenderPassDescriptor *middle = pass(c,z,.5,.625,0x67);
    if (discard) middle.colorAttachments[0].storeAction = middle.depthAttachment.storeAction = middle.stencilAttachment.storeAction = MTLStoreActionDontCare;
    // Reset the pass serial baseline: this selected helper pass is in-frame ordinal 1.
    g_raw_frame = UINT64_MAX; render(middle, 1);
    CHECK(![[NSFileManager defaultManager] fileExistsAtPath:manifest], "no CPU dump before producing command buffer completes");
    render(pass(c,z,0,.875,0xb4), 0);   // a later GPU overwrite must not change captured middle values
    uint64_t fence = mtl_sub_commit(); CHECK(mtl_wait(fence, 5000000000ull), "production submission completed");
    CHECK(!mtl_last_commit_error(), "Metal accepted combined depth/stencil plane copies");
    // FAST_WAKE may publish the GPU fence before Metal's feedback callback schedules the utility writer.
    for (int i=0; atomic_load(&g_raw_pending) && i<5000; i++) usleep(1000);
    CHECK(!atomic_load(&g_raw_pending), "asynchronous feedback and raw writer drained");
    if (disabled) {
        CHECK(g_raw_on == 0 && !g_raw_att && !g_raw_writer && !g_raw_dir && !g_raw_bytes, "disabled path has no capture allocations or queue");
        CHECK(![[NSFileManager defaultManager] fileExistsAtPath:manifest], "disabled path has no output");
    } else {
        NSString *text = [NSString stringWithContentsOfFile:manifest encoding:NSUTF8StringEncoding error:nil];
        NSArray *lines = [[text stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet] componentsSeparatedByString:@"\n"];
        CHECK(lines.count == (layered ? 6 : 3), "manifest has all selected color, depth and stencil layers");
        CHECK(g_raw_bytes <= g_raw_budget, "total readback allocations stay within hard budget");
        for (NSString *line in lines) {
            NSDictionary *r = [NSJSONSerialization JSONObjectWithData:[line dataUsingEncoding:NSUTF8StringEncoding] options:0 error:nil];
            CHECK([r[@"mip"] intValue] == 1 && ([r[@"slice"] intValue] == 2 || (layered && [r[@"slice"] intValue] == 3)) && [r[@"fence"] unsignedLongLongValue] == fence, "actual selected mip, slice and owning fence");
            CHECK([r[@"pool_offset"] intValue] == ([r[@"attachment"] isEqual:@"c0"] ? 4096 : 8192), "source descriptor provenance snapshotted");
            if (discard && !g_raw_keep) { CHECK([r[@"status"] isEqual:@"refused"] && !r[@"file"], "DontCare refused without modifying store behavior"); continue; }
            if (budget && ![r[@"attachment"] isEqual:@"c0"]) { CHECK([r[@"status"] isEqual:@"refused"] && [r[@"reason"] containsString:@"budget"], "excess depth/stencil allocation explicitly refused"); continue; }
            CHECK([r[@"status"] isEqual:@"captured"], "plane was captured");
            CHECK([r[@"capture_forced_store"] boolValue] == (discard && g_raw_keep), "forced Store is explicit");
            NSUInteger w = roi ? 3 : size / 2, h = roi ? 2 : size / 2, row = [r[@"bytes_per_row"] unsignedIntegerValue];
            CHECK([r[@"width"] unsignedIntegerValue] == w && [r[@"height"] unsignedIntegerValue] == h && row % 256 == 0, "actual mip / ROI extent and aligned row pitch");
            NSData *raw = [NSData dataWithContentsOfFile:[@(dir) stringByAppendingPathComponent:r[@"file"]]];
            CHECK(raw.length == row * h, "raw byte count follows manifest");
            unsigned wrong = 0; for (NSUInteger y=0;y<h;y++) for (NSUInteger x=0;x<w;x++) {
                const uint8_t *p = (const uint8_t *)raw.bytes + y * row;
                if ([r[@"attachment"] isEqual:@"c0"]) wrong += p[x*4] != 128 || p[x*4+1] != 0 || p[x*4+3] != 255;
                else if ([r[@"attachment"] isEqual:@"depth"]) wrong += ((const float *)p)[x] != .625f;
                else wrong += p[x] != 0x67;
            }
            CHECK(!wrong, "all captured texels equal GPU middle values despite later overwrite");
        }
    }
    [[NSFileManager defaultManager] removeItemAtPath:@(dir) error:nil];
    printf("PASS_RAW_GPU cases=%d failed=%d\n", cases, failures); return failures != 0;
} }
