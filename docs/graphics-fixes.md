# Rendering bugs and their fixes

All three showed in the opening Battlefield (the female protagonist's route) with a fixed camera. The fixes are local changes in `nxnative/`, listed in [VENDORED.txt](../nxnative/VENDORED.txt); they are not in upstream nxnative yet.

## 1. Flickering light shafts and ground shadows

**Symptom.** The light shafts and the ground shadow changed from frame to frame. Over 120 fixed-camera frames the sky region's mean luma ranged 14.2.

**Cause.** The particle data sits in R32 buffer textures of 38 to 52 elements that start at 8 mod 16 bytes, about 15 a frame. Metal reports a 16-byte alignment for texture-buffer views. On the M5 Max, R32Float, R32Uint and R32Sint views starting at 4, 8 or 12 mod 16 **read 0** (classic Metal and Metal 4 alike; aligned views are bit-exact). Upstream had measured such views reading correctly on an M6, so this did not show there.

**Fix.** Before a draw or dispatch, the host copies each misaligned read-only buffer texture the shader samples into an aligned scratch region, on the GPU at that point in the command stream, and binds a view of the copy (`nxnative/dm/gfx/nvn/nvn_tbuf_align.inc`). A copy is reused until the next submission or segment, or a write that could change its source. A misaligned source the same draw can also write is refused with a red log line.

**Result.** Sky luma range over 120 frames: 14.2 → 1.22; the rest is the restored flames (fix 2) moving. The median render passes per frame went from 55 to 78, since each copy splits the current pass. The first version cost about 0.3 ms GPU median (5.56 → 5.88 ms); the final one measures 5.51 ms, on par with before. Neither changes the frame rate at the 60 fps cap.

## 2. Missing flames and embers

**Symptom.** The flame columns on buildings, the fires on the walls and the drifting embers did not render.

**How it was found.** Running the flame draw's vertex stage once more through its geometry-capture entry into a buffer put all 238 vertices at one clip position (x −7575.8, y −1217.9, z 10, w 10000): triangles with no area. The shader's translation notes said `Failed to find handle source for bindless access of type "textureBuffer": phi(...)`.

**Cause.** Maxwell keeps the handle of a bindless TLD in a register that holds other values on the paths where the instruction is predicated off. In SSA the handle is a phi whose input on the executed path is `c2[texture word] | c2[sampler word]` and whose other inputs are index arithmetic or undefined. Ryujinx's `BindlessElimination` resolves neither form, so it **removed the instruction and wrote 0** to its destinations. Unreal's mesh particles read a scale of 0 and collapsed to a point. Each particle vertex shader of the scene lost 12 to 14 fetches.

**Fix** (shader translator):

- Profiles with `tex_dyn_slots` give every translation job `bindless_tables` (`dmsh_build.py`)
- The translator answers `IGpuAccessor.QueryBindlessTableHandles` from them
- `BindlessElimination.ClassifyHandle` sorts a handle into Table, Foreign or Neither. A handle built from c2 words and constants through ORs and phis is kept as an access and becomes Ryujinx's pool-array fallback, which the host fills at run time
- One table input of a phi is enough (the instruction only runs on that path). A handle with a shader-input or storage-buffer value anywhere in it keeps the old removal; image instructions are never converted
- ORs are classified over all operands, independent of their order; the recursion is memoized and limited to depth 64

**Result.** The Battlefield's five particle vertex shaders keep every fetch (for example `c55d54c8f41b1520`: 290 → 365 buffer reads, 12 removed fetches → 0). Flame columns, wall fires and embers are back.

## 3. Black ellipses on the road

**Symptom.** Four flat dark ellipses on the road.

**Cause.** The same particle draws as bug 2 (fragment shader `6967897c65605995`, vertex shader `1300ca45ddca3566`, alpha-blended). With the removed fetches their inputs read 0 and the draws painted the road black.

**Result.** Gone with fix 2; rows of fires render there instead.

## Ruled out

- **Early fragment tests.** 54 of the 61,305 pixel shaders request them, and the translator now keeps that NVN flag; none of the 54 is in the Battlefield frame, so it is unrelated to the flicker.
- **Uniform copies and indirect arguments.** A 120-frame snapshot A/B showed no stable benefit and added 0.905 ms to mean submission time; not enabled.

## Method

- Screenshots at fixed frame numbers (for example 120 consecutive frames from frame 2700) and the mean luma and range of a region of interest: steadier than judging by eye
- Every diagnostic run on a copy of the save, with the real save's hashes checked afterwards
- After each fix, the log checked for `[RED]` lines and `cannot be sampled` refusals
