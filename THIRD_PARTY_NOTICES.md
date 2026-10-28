# Third-party notices

This repository's own code and notes are under the [MIT license](LICENSE). It ships or uses the following components, each under its own license.

## Vendored in `nxnative/`

| Component | Where | License |
|---|---|---|
| [nxnative](https://github.com/seedds/nxnative) by seedds | `nxnative/` (base commit and local changes in `nxnative/VENDORED.txt`) | MIT, `nxnative/LICENSE` |
| [d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile) | nxnative started as a copy of its NSO rewriter, Horizon host and NVN-on-Metal layer | MIT |
| Ryujinx shader translator (`Ryujinx.Graphics.Shader`, `Ryujinx.Common`, `Spv.Generator` 1.3.2), with local fixes | `nxnative/dm/third_party/ryujinx/` | MIT, `nxnative/dm/third_party/ryujinx/LICENSE.txt` |
| [MacNativeGames](https://github.com/ly05010419/MacNativeGames) (two adapted pieces) | listed in `nxnative/dm/third_party/README.md` | MIT, `nxnative/dm/third_party/macnativegames/LICENSE` |
| libvpx 1.17.0 (VP9 decoder) | `nxnative/dm/third_party/libvpx/` | BSD-3-Clause, `nxnative/dm/third_party/libvpx/LICENSE` |
| stb_vorbis 1.22 | `nxnative/dm/third_party/stb_vorbis/` | MIT or public domain, `nxnative/dm/third_party/stb_vorbis/LICENSE` |
| Vulkan-Headers 1.4.363 | `nxnative/dm/third_party/vulkan-headers/` | Apache-2.0 or MIT, `nxnative/dm/third_party/vulkan-headers/LICENSE.md` |
| NVIDIA FLIP (with stb_image, stb_image_write, tinyexr) | `nxnative/dm/third_party/flip/` | BSD-3-Clause, `nxnative/dm/third_party/flip/LICENSE`; the single-header libraries carry their notices in the files |
| deko3d texture layout size algorithm | ported in `nxnative/dm/gfx/nvn/nvn_tex.c` | zlib |
| `nvn.h` | `nxnative/dm/gfx/nvn/include/nvn.h` | MIT, author in the file header |

`nxnative/dm/third_party/README.md` lists versions, changes and uses in full.

## Shipped as built .NET assemblies

The shader translator runs from `nxnative/dm/gfx/tools/dmsh_xlate_bin/`, built from `nxnative/dm/gfx/tools/dmsh_xlate` and the Ryujinx sources above. Its NuGet dependencies:

| Package | License |
|---|---|
| Humanizer.Core 3.0.10 (and its resource assemblies) | MIT |
| MsgPack.Cli 1.0.1 | Apache-2.0 |
| Gommon 2.8.1.2 | MIT |
| Microsoft.IO.RecyclableMemoryStream 3.0.1 | MIT |
| System.CodeDom 4.4.0 | MIT (.NET) |

## Downloaded at build time

| Component | Used by | License |
|---|---|---|
| [LibHac](https://www.nuget.org/packages/LibHac) 0.19.0 (NuGet) | `tools/split_xci`, `nxnative/dm/tools/nx_extract` | MIT |
| Capstone, lz4, fontTools (pip) | the build | BSD-3-Clause, BSD-3-Clause, MIT |
| SPIRV-Cross, zstd (Homebrew) | the build | Apache-2.0, BSD-3-Clause or GPL-2.0 |

## Not included

No game program, art, text, audio, shaders, saves, keys or firmware. FINAL FANTASY RESONANCE is © SQUARE ENIX; Nintendo Switch is a trademark of Nintendo. This project is affiliated with neither.
