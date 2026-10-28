# FINAL FANTASY RESONANCE on Mac

**English** | [简体中文](README.zh-CN.md)

<p align="center">
  <img src="docs/screenshots/title.jpg" width="49%" alt="The title screen running on a Mac">
  <img src="docs/screenshots/battlefield.jpg" width="49%" alt="The opening Battlefield at night, with fires along the road">
</p>

Run the Nintendo Switch release of FINAL FANTASY RESONANCE (Asian release, `0100B8A026F14000`) natively on an Apple silicon Mac. Bring your own game file and `prod.keys`; one script builds a Mac app from them.

It is not an emulator: the game's own ARM64 code runs directly on the Apple chip, and the Switch's NVN graphics are rebuilt on Metal. The toolchain is [nxnative](https://github.com/seedds/nxnative), vendored in [`nxnative/`](nxnative/) with the fixes this game needs.

**Apart from the two screenshots above, this repository contains no game content and distributes none:** no game program, art, text, audio, shaders, saves or keys.

## Status

| | |
|---|---|
| Game | Asian release 1.0.1 (v65536), main build `b7f965b9d7227db8e29f04e84ea7331b`, Unreal Engine 5.6.1. Languages: Simplified Chinese, Traditional Chinese, Korean |
| Tested on | Apple M5 Max, 128 GB, macOS 27.0 |
| Progress | The opening Battlefield and the first chapter play; not a full-game playthrough |
| Frame rate | 59.80 fps at the game's 60 fps cap; frame time p50/p95/p99 16.71/17.48/17.95 ms, GPU p50 5.51 ms |
| Audio | Six-channel output, no underruns in long measurements |
| Fixed here | Flickering light shafts and ground shadows; missing flames and embers; black ellipses on the road ([details](docs/graphics-fixes.md)) |
| Open | Eight compute shaders using image atomics are not translated (no effect seen so far); other chips than the M5 Max are untested ([known issues](docs/known-issues.md)) |

Other versions of the game are not supported: the game-specific changes are keyed to the build ID above.

## Install

**With an AI agent (easiest).** Open this repository in Claude Code, Codex, Cursor or a similar agent and say: "Read AGENTS.md and build the game for me." Give it the path of your game file and your `prod.keys` when it asks. [AGENTS.md](AGENTS.md) walks it through checking the Mac, identifying your files and running the build to the end.

**By hand.** Install the prerequisites in [docs/install.md](docs/install.md), then:

```sh
python3 build_app.py "<your game>.xci" -k "<your>/prod.keys"
```

The first build takes about 85 minutes on an M5 Max, most of it compiling shaders. Let it run to the end; if it is interrupted, run the same command again and finished steps are reused.

## Documentation

| Page | Contents |
|---|---|
| [docs/install.md](docs/install.md) | Prerequisites, splitting the XCI, building, running, controls, troubleshooting |
| [docs/build-specific.md](docs/build-specific.md) | Addresses and changes for this build: the allocator, the frame-rate console variables |
| [docs/graphics-fixes.md](docs/graphics-fixes.md) | The three rendering bugs: symptoms, how they were found, causes, fixes |
| [docs/performance.md](docs/performance.md) | Baseline, A/B results, pipeline prewarming, the 60 fps cap, render resolution |
| [docs/known-issues.md](docs/known-issues.md) | Open problems and what was not verified |
| [nxnative/VENDORED.txt](nxnative/VENDORED.txt) | The upstream commit the toolchain is based on, and every file changed on top of it |

## Repository layout

```
AGENTS.md                    install and repository guide for AI agents
build_app.py                 one-command build: XCI or NSPs + prod.keys -> Mac app
nxnative/                    the toolchain (seedds/nxnative + this game's profile and fixes)
  dm/games/0100B8A026F14000/ the game profile: patches, shader tables, pipeline manifest
tools/split_xci/             splits a combined XCI into base / update / DLC NSPs
tools/check_publish.py       pre-publish check for game files, keys and personal paths
tools/sync_nxnative.py       refreshes nxnative/ from a local nxnative checkout
bench/bench.tsv              repeatable benchmark routes
docs/                        notes
  screenshots/               the two screenshots on this page
```

## Acknowledgements

- [nxnative](https://github.com/seedds/nxnative) by seedds: the whole toolchain this builds on
- [d2r-mobile](https://github.com/jhaizhou-ops/d2r-mobile): the native-execution approach nxnative started from
- [MacNativeGames](https://github.com/ly05010419/MacNativeGames): the measured, per-game method of recording and prewarming pipeline states, and the AI-assisted install
- [Ryujinx](https://github.com/Ryujinx/Ryujinx) (shader translator), [LibHac](https://github.com/Thealexbarney/LibHac) and the other components in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)

## License

[MIT](LICENSE). Third-party components keep their own licenses ([THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)). FINAL FANTASY RESONANCE and its trademarks belong to SQUARE ENIX; this project is not affiliated with SQUARE ENIX, Nintendo or any publisher. Contributions: [CONTRIBUTING.md](CONTRIBUTING.md).
