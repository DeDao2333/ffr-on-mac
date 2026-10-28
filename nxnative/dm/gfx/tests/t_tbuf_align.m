// Real replay sampling of misaligned typed buffers; no game assets or keys.
#include "../nvn/nvn_mtl.m"
void *front_metal_layer(void) { return NULL; }
module_t *module_of(const void *p) { (void)p; return NULL; }
module_t g_mod[MAX_MOD]; int g_nmod;
void svc_thread_dump(void) {}
uint64_t now_ns(void) { return bp_now(); }
static int checks, failed;
#define CHECK(c, label) do { checks++; if (!(c)) { failed++; fprintf(stderr,"FAIL: %s\n",label); } } while(0)
static id<MTLLibrary> lib;
static uint8_t *memory;
static pool_t pool;
static uint64_t serial = 998000;
static id<MTLBuffer> readback;
static const char *msl =
"#include <metal_stdlib>\nusing namespace metal;\n"
"struct A {texture_buffer<float> t[[id(0)]];device uint *o[[id(1)]];};\n"
"kernel void k(constant A&a[[buffer(0)]],uint i[[thread_position_in_grid]]){a.o[i]=as_type<uint>(a.t.read(i).x);}\n"
"struct B {array<texture_buffer<float>,4> t[[id(0)]];device uint *o[[id(4)]];};\n"
"kernel void ka(constant B&a[[buffer(0)]],uint i[[thread_position_in_grid]]){a.o[i]=as_type<uint>(a.t[3].read(i).x);}\n"
"struct C {texture2d<float> t[[id(0)]];device uint *o[[id(1)]];};\n"
"kernel void kr(constant C&a[[buffer(0)]],uint i[[thread_position_in_grid]]){a.o[i]=as_type<uint>(a.t.read(uint2(i&16383,i>>14)).x);}\n"
"struct D {device uint *o[[id(0)]];};\n"
"kernel void producer(constant D&a[[buffer(0)]],uint i[[thread_position_in_grid]]){a.o[i]=0x40000000u+i;}\n"
"struct V {float4 p[[position]];uint x[[user(locn0),flat]];};\n"
"vertex V v(uint i[[vertex_id]]){V o;o.p=float4(i==1?3:-1,i==2?3:-1,0,1);o.x=0;return o;}\n"
"vertex V vt(uint i[[vertex_id]],constant A&a[[buffer(0)]]){V o;o.p=float4(i==1?3:-1,i==2?3:-1,0,1);o.x=as_type<uint>(a.t.read(0u).x);return o;}\n"
"fragment uint4 f(V v[[stage_in]],constant A&a[[buffer(0)]]){return uint4(as_type<uint>(a.t.read(0u).x),0,0,0);}\n"
"fragment uint4 fv(V v[[stage_in]]){return uint4(v.x,0,0,0);}\n";
static NvShader *shader(const char *entry, int stage, int kind) {
    NvShader *s=[NvShader new]; s->stage=stage;s->fn=[lib newFunctionWithName:@(entry)];
    // Keep every synthetic shader alive: the compute pipeline cache is keyed by the NvShader pointer (nvn_mtl.m g_cps), so a released
    // shader's address can be reused by a new one with a different argument layout, which then ran the old pipeline and read zeros
    static NSMutableArray *keep; if (!keep) keep=[NSMutableArray new]; [keep addObject:s];
    s->ae=[s->fn newArgumentEncoderWithBufferIndex:0];s->arg_len=s->ae.encodedLength;
    s->rf.tg[0]=s->rf.tg[1]=s->rf.tg[2]=1;
    s->rf.nb=2;s->rf.b[0]=(nvn_rbind){.kind=kind,.reg=128,.aid=0,.dtt=MTLTextureTypeTextureBuffer};
    if(kind==NVN_RB_TEXD){s->rf.b[0].n=4;s->rf.b[1].aid=4;}
    else s->rf.b[1].aid=1;
    if(!strcmp(entry,"kr")){s->rf.b[0].dtt=MTLTextureType2D;s->rf.b[0].f1d=1;}
    s->rf.b[1].kind=NVN_RB_SBUFP;s->rf.b[1].reg=0;s->rf.b[1].rw=1;
    if(stage!=5){s->rf.nb=1;s->rf.b[1].rw=0;}
    return s;
}
static tex_t texture(int offset, unsigned n, int format, int tid) {
    tex_t t={.magic=MAGIC('T','E','X',0),.serial=++serial,.pool=&pool,.off=offset,.mem=memory+offset};
    t.d=(nvn_texdesc){.target=NVN_TEXTURE_TARGET_BUFFER,.format=format,.width=n,.height=1,.depth=1,.levels=1,.samples=1};
    nvn_tex_layout(&t.d,&t.L);mtl_tex_init(&t);mtl_reg_tex(tid,&t,NULL);return t;
}
static void finish(void){CHECK(mtl_wait(mtl_sub_commit(),5000000000ull),"GPU completion");CHECK(!mtl_last_commit_error(),"no GPU error");}
static void bind(NvShader *s,int tid,uint64_t output) {
    S.tex[s->stage][128]=tid;S.tex[s->stage][131]=tid;
    if(s->rf.b[0].kind==NVN_RB_TEXU){s->rf.b[0].reg=0;*(uint32_t *)(memory+0x6000)=tid;S.ubo[s->stage][0]=(p_bind_buf){s->stage,0,(uint64_t)memory+0x6000,16};}
    S.ssbo[s->stage][0]=(p_bind_buf){s->stage,0,output,4096};
}
static void consume(NvShader *s,unsigned n) {
    static prog_t p;NvProg *pr=[NvProg new];pr->sh[5]=s;p.serial=++serial;serial_put(p.serial,pr);S.cprog=&p;
    dispatch(&(p_dispatch){.x=(int)n,.y=1,.z=1});serial_del(p.serial);
}
static void expect(unsigned n,const uint32_t *want,const char *label){CHECK(!memcmp(memory+0x4000,want,n*4),label);}
static void compute_cases(void) {
    const int offsets[]={0x100,0x104,0x108,0x10c};const int formats[]={NVN_FORMAT_R32F,NVN_FORMAT_R32UI,NVN_FORMAT_R32I};
    const unsigned counts[]={1,38,44,50,52};
    NvShader *cs=shader("k",5,NVN_RB_TBUF);
    for(int f=0;f<3;f++)for(int oi=0;oi<4;oi++)for(int ni=0;ni<5;ni++){
        unsigned n=counts[ni];int off=offsets[oi];uint32_t want[64];for(unsigned i=0;i<n;i++)want[i]=i%7==0?0x7fc12345u:i%7==1?0x80000000u:i+1;
        memcpy(memory+off,want,n*4);memset(memory+0x4000,0,n*4);tex_t t=texture(off,n,formats[f],700);
        state_reset();mtl_sub_begin();bind(cs,700,(uint64_t)memory+0x4000);uint64_t before=atomic_load(&g_tba_copies);
        consume(cs,n);finish();expect(n,want,"typed sample bit-exact at required and smaller NVN offsets");
        CHECK((atomic_load(&g_tba_copies)>before)==(oi!=0),"aligned path does not copy");mtl_tex_release(&t);
    }
    // A serial reused after CPU writes must read the new submission's bytes.
    tex_t t=texture(0x108,44,NVN_FORMAT_R32F,701);uint32_t want[44];
    for(int frame=0;frame<3;frame++){for(int i=0;i<44;i++)want[i]=100*frame+i+1;memcpy(memory+0x108,want,sizeof want);state_reset();mtl_sub_begin();bind(cs,701,(uint64_t)memory+0x4000);consume(cs,44);finish();expect(44,want,"reused serial refreshes across submissions");}
    // GPU copy, fill, inline update and dispatch produce the sampled bytes.
    for(int op=0;op<4;op++){
        memset(memory+0x108,0,sizeof want);for(int i=0;i<44;i++)want[i]=op==3?0x40000000u+i:0x3f900000u;memcpy(memory+0x7000,want,sizeof want);
        state_reset();mtl_sub_begin();
        if(op==0)mtl_sub_copy((uint64_t)memory+0x7000,(uint64_t)memory+0x108,sizeof want);
        if(op==1)mtl_sub_fill((uint64_t)memory+0x108,sizeof want,want[0]);
        if(op==2)mtl_sub_update(want,(uint64_t)memory+0x108,sizeof want);
        if(op==3){NvShader *ps=shader("producer",5,NVN_RB_TBUF);ps->rf.nb=1;ps->rf.b[0]=(nvn_rbind){.kind=NVN_RB_SBUFP,.reg=0,.aid=0,.rw=1};S.ssbo[5][0]=(p_bind_buf){5,0,(uint64_t)memory+0x108,sizeof want};consume(ps,44);}
        bind(cs,701,(uint64_t)memory+0x4000);consume(cs,44);finish();expect(44,want,"GPU producer precedes the aligned texture copy");
    }
    // A producer after a first fetch invalidates that cached scratch without
    // changing the registered texture serial. Both results must survive.
    for(int op=0;op<4;op++){
        uint32_t old[44];for(int i=0;i<44;i++){old[i]=0x3e000000u+i;want[i]=op==3?0x40000000u+i:0x3f900000u;}memcpy(memory+0x108,old,sizeof old);memcpy(memory+0x7000,want,sizeof want);
        state_reset();mtl_sub_begin();bind(cs,701,(uint64_t)memory+0x4000);consume(cs,44);
        if(op==0)mtl_sub_copy((uint64_t)memory+0x7000,(uint64_t)memory+0x108,sizeof want);
        if(op==1)mtl_sub_fill((uint64_t)memory+0x108,sizeof want,want[0]);
        if(op==2){
            // Force UpdateUniformBuffer into its deferred-in-pass path.
            tex_t rt={.magic=MAGIC('T','E','X',0),.serial=++serial};rt.d=(nvn_texdesc){.target=NVN_TEXTURE_TARGET_2D,.format=NVN_FORMAT_RGBA8,.width=4,.height=4,.depth=1,.levels=1,.samples=1};
            R.has_rt=1;R.rt.n=1;R.rt.color[0]=&rt;R.clear_c[0]=1;R.cc[0]=MTLClearColorMake(0,0,0,0);CHECK(rp_begin()!=nil,"deferred-update test pass");
            mtl_sub_update(want,(uint64_t)memory+0x108,sizeof want);CHECK(g_nubd>0,"inline update really deferred");rp_end();R.has_rt=0;R.rt.n=0;mtl_tex_release(&rt);
        }
        if(op==3){NvShader *ps=shader("producer",5,NVN_RB_TBUF);ps->rf.nb=1;ps->rf.b[0]=(nvn_rbind){.kind=NVN_RB_SBUFP,.reg=0,.aid=0,.rw=1};S.ssbo[5][0]=(p_bind_buf){5,0,(uint64_t)memory+0x108,sizeof want};consume(ps,44);}
        bind(cs,701,(uint64_t)memory+0x5000);consume(cs,44);finish();CHECK(!memcmp(memory+0x4000,old,sizeof old),"old scratch survives later producer");CHECK(!memcmp(memory+0x5000,want,sizeof want),"same-serial scratch refreshes after GPU producer");
    }
    for(int kind=0;kind<3;kind++){
        NvShader *s=shader(kind==0?"ka":kind==1?"kr":"k",5,kind==0?NVN_RB_TEXD:kind==1?NVN_RB_TEX:NVN_RB_TEXU);
        for(int i=0;i<44;i++)want[i]=0x3f800000u+i;memcpy(memory+0x108,want,sizeof want);
        state_reset();mtl_sub_begin();bind(s,701,(uint64_t)memory+0x4000);consume(s,44);finish();expect(44,want,"dynamic array, wrapped 1D and UBO handles use scratch bindings");
    }
    state_reset();mtl_sub_begin();bind(cs,701,(uint64_t)memory+0x4000);NvShader *only[]={cs};
    CHECK(tbuf_align_prepare(only,1,0),"first preparation");uint64_t copies=atomic_load(&g_tba_copies);CHECK(tbuf_align_prepare(only,1,0),"same-epoch preparation");CHECK(atomic_load(&g_tba_copies)==copies,"unchanged source reuses scratch");
    // The source and output alias in this consumer: a pre-draw copy is invalid.
    S.ssbo[5][0]=(p_bind_buf){5,0,(uint64_t)memory+0x108,sizeof want};CHECK(!tbuf_align_prepare(only,1,0),"same-consumer writable alias refused");finish();
    // sub_cut submits a segment, but its arena remains leased to the final fence.
    state_reset();mtl_sub_begin();bind(cs,701,(uint64_t)memory+0x4000);consume(cs,44);
    rp_end();g_cut_copy=1;sub_cut();CHECK(g_seg>0,"segment actually submitted");
    for(int i=0;i<44;i++)want[i]=0x3f000000u+i;mtl_sub_update(want,(uint64_t)memory+0x108,sizeof want);consume(cs,44);finish();expect(44,want,"later segment gets fresh bytes and a live arena");
    mtl_tex_release(&t);
}
static void wrapped_rows_case(void) {
    size_t size=0x80000;uint8_t *mem=mmap(NULL,size,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);pool_t p={.mem=mem,.size=size};mtl_pool_add(mem,size);
    unsigned n=16385;uint32_t *want=malloc(n*4);for(unsigned i=0;i<n;i++)want[i]=0x3f000000u+i;memcpy(mem+8,want,n*4);
    tex_t t={.magic=MAGIC('T','E','X',0),.serial=++serial,.pool=&p,.off=8,.mem=mem+8};t.d=(nvn_texdesc){.target=NVN_TEXTURE_TARGET_BUFFER,.format=NVN_FORMAT_R32F,.width=n,.height=1,.depth=1,.levels=1,.samples=1};nvn_tex_layout(&t.d,&t.L);mtl_tex_init(&t);mtl_reg_tex(704,&t,NULL);
    NvShader *s=shader("kr",5,NVN_RB_TEX);state_reset();mtl_sub_begin();S.tex[5][128]=704;S.ssbo[5][0]=(p_bind_buf){5,0,(uint64_t)mem+0x20000,n*4};consume(s,n);uint64_t key=704ull|(1ull<<32);id<MTLTexture> snap=(__bridge id<MTLTexture>)nvn_hm_get(&g_tba_bound,&key);fprintf(stderr,"rows snapshot type=%lu w=%lu h=%lu row=%lu off=%lu req=%zu copies=%llu\n",(unsigned long)snap.textureType,(unsigned long)snap.width,(unsigned long)snap.height,(unsigned long)snap.bufferBytesPerRow,(unsigned long)snap.bufferOffset,g_tba_n,(unsigned long long)atomic_load(&g_tba_copies));id<MTLBuffer> rb=[g_dev newBufferWithLength:n*4 options:MTLResourceStorageModeShared];res_add(rb);enc_b_copy_buffer(blit(),snap.buffer,snap.bufferOffset,rb,0,n*4);finish();fprintf(stderr,"rows raw first=%#x last=%#x\n",((uint32_t *)rb.contents)[0],((uint32_t *)rb.contents)[n-1]);res_remove(rb);for(unsigned i=0;i<n;i++)if(((uint32_t *)(mem+0x20000))[i]!=want[i]){fprintf(stderr,"wrapped mismatch %u got=%#x want=%#x\n",i,((uint32_t *)(mem+0x20000))[i],want[i]);break;}CHECK(!memcmp(mem+0x20000,want,n*4),"wrapped scratch crosses the 16384-element row boundary");
    mtl_tex_release(&t);mtl_pool_del(mem);defer_drain();munmap(mem,size);free(want);
}
static void render_case(int vertex) {
    NvShader *s=shader(vertex?"vt":"f",vertex?0:1,NVN_RB_TBUF);tex_t t=texture(0x108,44,NVN_FORMAT_R32F,702);
    *(uint32_t *)(memory+0x108)=0x40012345u;
    MTLRenderPipelineDescriptor *pd=[MTLRenderPipelineDescriptor new];pd.vertexFunction=[lib newFunctionWithName:vertex?@"vt":@"v"];pd.fragmentFunction=[lib newFunctionWithName:vertex?@"fv":@"f"];pd.colorAttachments[0].pixelFormat=MTLPixelFormatRGBA32Uint;
    NSError *er=nil;id<MTLRenderPipelineState> ps=[g_dev newRenderPipelineStateWithDescriptor:pd error:&er];CHECK(ps!=nil,"render pipeline");
    tex_t target={.magic=MAGIC('T','E','X',0),.serial=++serial};target.d=(nvn_texdesc){.target=NVN_TEXTURE_TARGET_2D,.format=NVN_FORMAT_RGBA32UI,.width=4,.height=4,.depth=1,.levels=1,.samples=1};
    state_reset();memset(&R,0,sizeof R);mtl_sub_begin();bind(s,702,(uint64_t)memory+0x4000);NvShader *stages[]={s};CHECK(tbuf_align_prepare(stages,1,1),"render input prepared before encoder");
    R.has_rt=1;R.rt.n=1;R.rt.color[0]=&target;R.clear_c[0]=1;R.cc[0]=MTLClearColorMake(0,0,0,0);id<MTL4RenderCommandEncoder> en=rp_begin();
    enc_r_pso(en,ps);enc_r_viewport(en,(MTLViewport){0,0,4,4,0,1});CHECK(encode_args((id<MTLCommandEncoder>)en,s,s->stage,vertex),"actual stage argument table");enc_r_draw(en,MTLPrimitiveTypeTriangle,0,3);rp_end();
    id<MTLTexture> output=tex_mtl(&target);enc_b_copy_texture_buffer(blit(),output,0,0,MTLOriginMake(0,0,0),MTLSizeMake(4,4,1),readback,0,256,1024);finish();
    int ok=1;for(int y=0;y<4;y++)for(int x=0;x<4;x++)ok&=*(uint32_t *)((uint8_t *)readback.contents+y*256+x*16)==0x40012345u;CHECK(ok,vertex?"vertex texture sampling reads copied bytes":"pixel texture sampling reads copied bytes");mtl_tex_release(&t);mtl_tex_release(&target);
}
int main(void){@autoreleasepool{
    alarm(30);setbuf(stdout,NULL);setenv("DM_NVN_TBDR","0",1);mtl_init();size_t bytes=8*getpagesize();memory=mmap(NULL,bytes,PROT_READ|PROT_WRITE,MAP_PRIVATE|MAP_ANON,-1,0);CHECK(memory!=MAP_FAILED,"pool allocation");pool=(pool_t){.mem=memory,.size=bytes};mtl_pool_add(memory,bytes);
    readback=[g_dev newBufferWithLength:2048 options:MTLResourceStorageModeShared];res_add(readback);NSError *er=nil;lib=[g_dev newLibraryWithSource:@(msl) options:nil error:&er];CHECK(lib!=nil,"synthetic shader library");if(!lib){fprintf(stderr,"%s\n",er.description.UTF8String);return 1;}
    compute_cases();wrapped_rows_case();render_case(0);render_case(1);
    // Finalizing the pool after preparing a GPU copy cannot release its backing
    // before that submitted copy finishes; no CPU access is needed by sampling.
    tex_t t=texture(0x108,44,NVN_FORMAT_R32F,703);NvShader *s=shader("k",5,NVN_RB_TBUF);uint32_t want[44];for(int i=0;i<44;i++)want[i]=0x3fa00000u+i;memcpy(memory+0x108,want,sizeof want);
    state_reset();mtl_sub_begin();bind(s,703,(uint64_t)memory+0x4000);NvShader *one[]={s};CHECK(tbuf_align_prepare(one,1,0),"pre-finalize preparation");
    void *out;uint64_t outoff;CHECK(mtl_addr((uint64_t)memory+0x4000,4096,&out,&outoff),"output captured");
    mtl_pool_del(memory); // Use a separate retained output below: direct sampling
    // survives pool retirement; production argument collection cannot resolve a
    // newly bound output after its pool was removed, so inspect scratch itself.
    uint64_t slot=703;id<MTLTexture> scratch=(__bridge id<MTLTexture>)nvn_hm_get(&g_tba_bound,&slot);CHECK(scratch!=nil,"scratch survives source pool retirement");
    enc_b_copy_buffer(blit(),scratch.buffer,scratch.bufferOffset,readback,0,sizeof want);finish();CHECK(!memcmp(readback.contents,want,sizeof want),"GPU copy source remains alive through finalization");mtl_tex_release(&t);defer_drain();munmap(memory,bytes);
    printf("t_tbuf_align: %d checks, %d failed\n",checks,failed);return failed!=0;
}}
