# FINAL FANTASY RESONANCE (0100B8A026F14000): patches of this regional release's own NSO builds, loaded by dm/tools/patch_nso.py.
# Nintendo SDK module patches remain in dm/tools/sdk_patches.py.
CUTS = {
    # 1.0.1 (v65536), main b7f965...: verified in the regional executable, rather than inherited from another build's addresses.
    # nninitStartup (0x13370) reserves the engine region (0x137d0), then calls 0xae770 with x0 = the bss object at 0x77c6210 and x1 = 0.
    # The constructor writes the 32-slot UE5.6 FMallocBinned3 vtable (0x67df800 at 0xae7ec) and reserves 0x980000000 bytes at 0xae860.
    # FPlatformMemory::BaseAllocator (0x13c64) returns that object; the TLS callbacks (0x13830 / 0x13870) call Malloc / Free through slots 6 / 10.
    # Slots 11 / 12 call slot 6, then memset; the pool-base and page-size globals (0x7c148a0 / 0x7c14800) are referenced only inside allocator code.
    # Cut only the constructor call: hle_uemalloc.c installs the host allocator in the object before guest code runs, gated on this build ID.
    'b7f965b9d7227db8e29f04e84ea7331b': [
        (0x137f0, 'e06b0294', '1f2003d5', 'nninitStartup: bl 0xae770 (FMallocBinned3 constructor, 38 GB of address space) → nop; the host allocator is in the object already (hle_uemalloc.c)'),
    ],
}
