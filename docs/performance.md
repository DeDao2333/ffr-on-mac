# Performance

All numbers come from the opening Battlefield with a fixed camera on an M5 Max, from one copy of a save. At the 60 fps cap they measure the game loop's frame time; CPU and GPU times overlap and do not add up.

## Baseline, before and after the fixes

| Build | fps | Frame p50 / p95 / p99 (ms) | GPU p50 (ms) | Render passes (median) |
|---|---|---|---|---|
| Before the fixes | 59.81 | 16.70 / 17.25 / 17.40 | 5.56 | 55 |
| With the fixes (chain `efaa6fba9dba`) | 59.80 | 16.71 / 17.48 / 17.95 | 5.51 | 78 |

The same window with the fixes (frames 2400–3599):

| | |
|---|---|
| GPU p95 / p99 | 10.56 / 13.75 ms |
| Draws a frame | 737 |
| Render passes p95 | 163 |
| Shader libraries / pipelines made at run time | 0 / 0 |
| Internal render size → output | 1040×584 → 1920×1080 (the game upscales itself) |

The GPU median uses a third of the 16.7 ms budget, so the GPU is not the limit. At the 60 fps cap, faster CPU or GPU work does not raise the frame rate; it can only buy image quality, fewer stalls or less power.

## Pipeline prewarming

Following [MacNativeGames](https://github.com/ly05010419/MacNativeGames): record the shader and pipeline states the game actually uses, check them against this build's pipeline-key ABI, shader identities and reflection, and prepare them before gameplay.

`nxnative/dm/games/0100B8A026F14000/pso_manifest.tsv` holds 778 shaders, 586 render pipelines and 97 compute pipelines, plus two explicit layered-entry recipes. Another 515 observed VS/PS pairs (`pso_observed_pairs.tsv`) join the offline base prebuild; none overlaps the 9,253 static pairs. The offline archive has 9,758 bases.

Cold-start A/B (empty user manifests and device archives, same chain and host, no screenshots, OS Metal caches kept):

| | No prewarm | Prewarm |
|---|---|---|
| Libraries / pipelines made at run time | 184 / 145 | 0 / 2 |
| Synchronous pipeline work | 2,069.1 ms | 397.8 ms |
| Longest frame entering the Battlefield | 1,588.7 ms | 40.7 ms |
| First present | 0.606 s | 2.474 s |
| Warm fps / p99 | 59.802 / 17.437 ms | 59.804 / 17.443 ms |

About 1.7 s more at startup buys no 1.6 s stall on entering the scene. The two remaining pipelines came from a layered-entry defect; with it fixed, the final app prepared all 1,463 recipes in 1.50 s, presented first at 2.166 s, and made no library or pipeline synchronously through frame 4,500.

Coverage is the recorded route only. A first visit to a new area builds its pipelines once; recording that play and merging it into the manifest covers it next time.

## Shader table analysis

The constant-table analysis of all 73,496 shader keys now shares parsed shader facts, caches by content and uses worker processes on cache misses:

| Pass | Before (serial) | Cold cache | Warm cache |
|---|---|---|---|
| C1 | about 507 s | 200.2 s | 5.0 s |
| CB | about 76 s | 16.3 s | 4.7 s |

The generated rows match the previous implementation exactly.

## Audio

The game submits three 240-frame six-channel buffers at a time, while this Mac asks for 512 frames per callback. A 1,024-frame (21.3 ms) PCM FIFO in the host bridges them. Five windows of 2,000 callbacks each had no underrun and no zero padding. These were muted measurements; listening is a separate check.

## Why 60 fps

Three layers cap it:

1. The game's own `t.MaxFPS` and `rhi.SyncInterval`, which the host holds for this build ([addresses](build-specific.md#frame-rate-tmaxfps-and-rhisyncinterval))
2. nxnative's frame-rate target accepts only 0, 30 or 60 (`hle_fpslock.c` and `dm/games/game.py` both check; 0 means the game's own limit, not unlimited)
3. Presentation: the `CAMetalDisplayLink` frame-rate range tops out at 60, and its 120 Hz mode presents the game every other tick

Going higher needs all three changed. Even then the GPU p95 of 10.56 ms is over the 8.33 ms budget of 120 fps, so frame times would be uneven for little gain.

## Raising the internal resolution (not done yet)

The GPU p50 and p95 scaled linearly with pixel count; not all GPU work grows with pixels, so these are pessimistic estimates:

| Internal size | GPU p50 (estimate) | GPU p95 (estimate) |
|---|---|---|
| 1040×584 (now) | 5.5 ms | 10.6 ms |
| 1280×720 | 8.4 ms | 16.0 ms |
| 1536×864 | 12.0 ms | 23.1 ms |
| 1920×1080 | 18.8 ms | 36.0 ms |

By the median, 720p to 864p fit the 16.7 ms budget; by p95, 720p is already at its edge. It needs measuring. The way to do it is to hold `r.ScreenPercentage` with dynamic resolution off ([addresses](build-specific.md#found-not-used-yet-rscreenpercentage)).

## Tried, no use for this game

- **MetalFX upscaling.** The host has it (`DM_PRESENT_UPSCALE=metalfx`), but the game composes to 1920×1080 before it presents, so the host gets the output size and has nothing to upscale
- **Uniform copies and indirect arguments.** No stable benefit, 0.905 ms more mean submission time; not enabled

## Method

- Same save, camera and route; warm up, then measure a fixed window of frames
- Alternate the variants and repeat; report p50 / p95 / p99 / max, not only the mean
- Record the shader chain, the host build and the cache state
- Keep performance runs and screenshot runs apart (screenshots slow frames down)
