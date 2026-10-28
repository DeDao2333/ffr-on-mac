# FINAL FANTASY RESONANCE, Asian release (`0100B8A026F14000`)

Update 1.0.1 (v65536), main build `b7f965b9d7227db8e29f04e84ea7331b`, SDK 20.5.17, Unreal Engine 5.6.1, NVN. The control data lists Korean, Traditional Chinese and Simplified Chinese. The SDK and movie modules are the same builds as the [other release](../0100606026F12000/README.md).

## Status

Built and run on an Apple M5 Max with macOS 27.0. A cloned save reaches the female protagonist's opening Battlefield and the first chapter. The supplied release has the same 73,496 unique shader keys and identical shader facts/pairs as the other release, but its shader-map source positions differ. This profile registers the tables generated from its own files. This is a limited scene validation, not a full-game playthrough.

## Measurements and remaining work

- Headless fixed-camera Battlefield baseline, 1,800 measured frames: 59.807 game fps, frame p50/p95/p99 16.738/17.568/17.905 ms, submit plus present p50 2.102 ms, GPU p50 5.951 ms. No new shader libraries or pipelines were made during this warm window. Captures and performance runs are separate. These measure the capped game loop, not physical display presentation; CPU and GPU timings overlap.
- The AudioOut bridge fixes the device/guest quantum mismatch: the real six-channel output submits three 240-frame buffers while this Mac requests 512 frames per callback. The candidate recorded five 2,000-callback windows for each output with no underrun or zero padding. The PCM FIFO held 1,024 frames (21.3 ms). These were muted real-CoreAudio measurements; subjective listening remains a separate check.
- `depth_mode zero` matches the game's observed `near=0/main0_z` mode. The previous GL-mode offline archive did not match startup and caused the device to rebuild bases. Regenerate the archive after changing this profile setting.
- 54 of the 61,305 pixel shader controls request early fragment tests. The translator now preserves that NVN flag. None of those 54 is used in the captured Battlefield frame, so this correction alone does not explain or remove this scene's light-shaft flicker.
- Resolved: the Battlefield's light shafts and ground shadow flickered because particle buffer textures at 8 mod 16 read 0 on the M5 Max ([Buffer textures at offsets below 16 bytes](../../../docs/reference/rendering.md#buffer-textures-at-offsets-below-16-bytes)); over 120 fixed-camera frames the sky's mean luma ranged 14.2 before and 0.7 after the aligned copies.
- Resolved: the Battlefield's flames on buildings and walls and their embers were missing: the translator had removed 12 to 14 particle-buffer fetches from each of the scene's Unreal particle vertex shaders ([Run-time c2 handles kept through phis](../../../docs/reference/shaders.md#run-time-c2-handles-kept-through-phis)).
- Resolved with the flames above: the four flat dark ellipses on the road are gone and fires render there. They were the same scene's particle draws (fragment shader `6967897c65605995`, vertex shader `1300ca45ddca3566`, alpha-blended); with the removed fetches their inputs read 0 and the draws painted the road black.
- With both fixes built (chain `efaa6fba9dba`), the same fixed-camera window measures 59.80 game fps, frame p50/p95/p99 16.71/17.48/17.95 ms, GPU p50 5.51 ms and 78 render passes a frame; the build before the fixes measured 59.81 fps, 16.70/17.25/17.40 ms, GPU 5.56 ms and 55 passes. The aligned copies therefore cost about 0.3 ms of GPU time a frame at the 60 fps cap. The 120-frame sky range is 1.2 (14.2 before). The real save's hashes were unchanged by these runs.
- Optional uniform-copy and indirect-argument experiments are kept outside the production renderer; a 120-frame snapshot A/B showed no stable benefit and added 0.905 ms to mean submission time, so it is not enabled.
- The build has eight untranslated compute shaders using unsupported image atomics. No missing-library error was observed in the captured Battlefield route.

`bench.tsv` supplies repeatable fixed-camera and camera-pan routes. Use a separate copy of a Battlefield save with `scripts/bench.py --save`; no game save is stored in this repository. Keep the shader chain, image settings, route and cache state identical when comparing variants.

## Optimization method

Following the measured, per-game approach of [MacNativeGames](https://github.com/ly05010419/MacNativeGames), record the actual shader and pipeline states used by this release, validate them against its build and shader reflection, then prepare them before gameplay. Do not copy Zelda addresses, rendering presets or shader assumptions into this Unreal title.

The recorded `pso_manifest.tsv` contains 778 shader, 586 ordinary render-pipeline and 97 compute-pipeline rows from local gameplay, plus two explicit layered-entry recipes. The import checks the actual C pipeline-key ABI, title/main build, depth convention, shader identities and reflection semantics across chains. Legacy render rows do not record the layered vertex entry; new `RL` rows preserve it explicitly. This is partial route coverage rather than a full-game cache. The generated static pair tables remain unchanged. A separate `pso_observed_pairs.tsv` adds 515 recorded VS/PS combinations to the offline and runtime base prebuild; none overlap the 9,253 static combinations. The two kinds of seed complement each other: base pairs cover pulling fallbacks, while render rows prepare exact recorded layouts and state. The rebuilt offline archive contains 9,758 eligible bases; ten intrinsically layered static pairs remain device-built.

An app-local cold A/B used independent empty user manifests and device archives, a cloned Battlefield save, the same chain/host/bundled archive, and no screenshots. OS Metal caches were retained. The 1,461-row seed prepared in 1.70 s and moved first present from 0.606 s to 2.474 s. Runtime library/pipeline creation fell from 184/145 to 0/2; synchronous pipeline work fell from 2,069.1 ms to 397.8 ms. The first Battlefield creation burst dropped from a 1,588.7 ms frame to 40.7 ms. Warm performance remained 59.802/59.804 fps, with p99 frame times 17.437/17.443 ms. The two remaining builds exposed the layered-entry recipe defect, addressed by the two `RL` rows and variant-aware runtime.

The final signed app was tested in a real window with independent empty local caches, its bundled default manifest, and muted real CoreAudio. All 1,463 recipes prepared in 1.50 s; first present was at 2.166 s. Through frame 4,500 the run created no shader library or pipeline synchronously, including both previous layered misses. Both audio outputs completed three 2,000-callback windows with no underrun or zero padding and a 1,024-frame FIFO. This windowed run also retained a 15.122 s display-link stall (88 ms active on recovery) and discarded frames; it therefore does not establish uninterrupted foreground FPS. No image-quality claim or steady-FPS improvement follows from these startup results.

Constant-table analysis shares parsed shader facts across C1 and CB analysis, caches by source/reflection/analyzer content, and uses worker processes on cache misses. For all 73,496 shader keys, C1 took 200.196 s and CB 16.326 s on a cold analysis cache; repeat runs took 5.004 s and 4.684 s. Generated data rows match the previous implementation exactly. The earlier serial passes took approximately 507 s and 76 s.

The generated table headers also name their analyzer source dependencies. A shader-chain/input stamp alone is insufficient to reuse a table after changing its analyzer. CB-only edits invalidate CB alone; edits to the shared parser or C1 interval evaluator invalidate both. This does not change shader libraries or their chain.

## Found on this game

- [Device callbacks larger than a finite guest buffer pool](../../../docs/reference/audio.md#device-callbacks-larger-than-a-finite-guest-buffer-pool).
- [Prewarm recipes preserve the layered vertex entry](../../../docs/reference/shaders.md#prewarm-recipes-preserve-the-layered-vertex-entry).

## Build-specific changes

- The FMallocBinned3 call at `main+0x137f0` targets `main+0xae770` with the object at `main+0x77c6210`. Its 32-slot vtable is at `main+0x67df800`. The constructor reserves 38 GiB at `main+0xae860`. The cut and the host allocator are gated by this release's own main build ID.
- `t.MaxFPS` and `rhi.SyncInterval` register through the UTF-16 names at `main+0x6179a4a` and `main+0x6046432`. The calls at `main+0x33a4dec` and `main+0x149e0bc` construct console objects at `main+0x8c29af0` and `main+0x8adca10`, then store their value pointers at `+0x10`. The host's frame-limit rows are gated by this release's main build ID.
- Memory settings, dynamic particle texture slots and the manual integer-output shader fact follow the other release; see its note for the mechanism and remaining graphics limitations.

## Input files

The supplied combined XCI has its patch Program NCA before its base Program NCA. Split its content into base, update and DLC NSPs using their CNMT content lists before passing them to the build script. Direct extraction otherwise picks the patch without its base and fails while reading the RomFS. The original XCI is unchanged.
