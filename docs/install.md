# Install

## What you need

- An Apple silicon Mac. Tested on an M5 Max with macOS 27.0; other chips are untested ([why it matters](known-issues.md#untested))
- [Xcode](https://apps.apple.com/app/xcode/id497799835) (the full app, not only the Command Line Tools) with its Metal toolchain, and [Homebrew](https://brew.sh)
- Your own dump of the Asian release `0100B8A026F14000`: the base game and update 1.0.1 (v65536), as one combined XCI or as NSPs. The DLC is optional
- Your own `prod.keys`, dumped from your Switch
- Free disk space on one APFS volume: about 60 GB. A fresh build measured 47 GB: the split NSPs 9.2 GB, the extracted game 8.4 GB, the translated shaders 27 GB, the pipeline archive 0.7 GB and the app, whose 12 GB of game files are APFS clones of the extracted ones

## Prerequisites

```sh
brew install zstd spirv-cross
brew install --cask dotnet-sdk
xcodebuild -downloadComponent MetalToolchain      # Xcode 26 and later install it separately
python3 -m venv ~/.venvs/ffr && source ~/.venvs/ffr/bin/activate
python3 -m pip install lz4 capstone fonttools      # also backports.zstd before Python 3.14
```

Tested with Python 3.14, lz4 4.4.5, capstone 5.0.9 and fonttools 4.67.0. The build checks for every tool and module it needs and names the missing ones.

## Build

```sh
git clone <this repository> ffr-on-mac && cd ffr-on-mac
python3 build_app.py "<path>/game.xci" -k "<path>/prod.keys"
```

This writes `FINAL FANTASY RESONANCE.app` into `ffr-mac-work/` beside the XCI. Options (`-h` lists them all):

| Option | |
|---|---|
| `-o <app>` | where to write the app |
| `--work <folder>` | the work folder for the XCI split (default `ffr-mac-work/` beside the XCI) |
| `-l zh-Hant` / `-l ko` | another game language (default `zh-Hans`) |
| `--replace` | build over an existing app |
| `--dry-run` | print the commands only |

The first build takes about 85 minutes on an M5 Max (measured: 86 minutes from an XCI with no cache), most of it compiling the game's 73,496 shaders for the Mac (78 minutes). Do not interrupt it. Later builds reuse everything whose inputs did not change.

This is verified from a clean copy: a fresh checkout of this repository with a new virtual environment, building from the release XCI, produced the same app as the development build (shader chain `efaa6fba9dba`, 12.2 GB, ad-hoc signed) and wrote nothing into the checkout except the ignored shader tables.

### What it does

1. **Split the XCI.** The combined XCI lists the update's Program NCA before the base game's, so a direct extraction picks the update without its base and fails reading the RomFS. `tools/split_xci` writes one NSP per content meta (base, update, DLC). The XCI is only read. With a folder of NSPs this step is skipped.
2. **Build with nxnative** (`nxnative/scripts/build_ipa_from_nsp.py --platform mac-app`): extract and layer the update over the base, rewrite the game's code for the Mac, translate the shaders to Metal, build the pipeline archive, pack and ad-hoc sign the app. Intermediate files stay in `<NSP folder>/0100B8A026F14000_build/`.

## Run

Open the app from Finder.

| Path | |
|---|---|
| `~/Library/Application Support/nxnative/0100B8A026F14000/` | saves and settings |
| `~/Library/Logs/nxnative/0100B8A026F14000.log` | the log |

Every app of this title ID shares that save folder: do not run two builds at once, and back the folder up before trying a new build.

### Controls

Keyboard (Nintendo button layout); controllers work too, each one a player of its own.

| Key | Switch |
|---|---|
| WASD / arrow keys | left stick / D-pad |
| J / K / U / I | A (confirm) / B (back) / X / Y |
| Q / E, Z / C | L / R, ZL / ZR |
| F / G | press the left / right stick |
| Return / Tab | + / − |
| Space | shake |
| ⌃⌘F or the green window button | full screen |

## Troubleshooting

| Message | Fix |
|---|---|
| `tools not found: …` | install what it names (the Homebrew and .NET commands above) |
| `the Metal compiler does not run` | `xcodebuild -downloadComponent MetalToolchain` |
| `the current Python … is missing modules` | activate the venv and `pip install` what it names |
| `Xcode is not installed` | install Xcode; `build_app.py` sets `DEVELOPER_DIR` when `xcode-select` points at the Command Line Tools |
| the build says the version was not tested | you have another version than 1.0.1; the fixes do not apply to it |
| the app opens to a black window | check the log for lines with `[RED]` and include them in an issue |

## Benchmarks

`nxnative/scripts/bench.py` runs a fixed route on a copy of a save and reports frame-time percentiles. The routes for this game are in [bench/bench.tsv](../bench/bench.tsv) and start from the entrance of the opening Battlefield:

```sh
python3 nxnative/scripts/bench.py <NSP folder>/0100B8A026F14000_build --route battlefield_idle --save <a copy of your save folder>
```

Compare variants within one invocation; load on the Mac moves the numbers between invocations.
