# Notes for agents

This file tells an AI agent how to build FINAL FANTASY RESONANCE (Nintendo Switch, Asian release `0100B8A026F14000`, version 1.0.1) into a native Mac app from the player's own game file and `prod.keys`. `build_app.py` drives it; the toolchain is the vendored [nxnative](https://github.com/seedds/nxnative) in `nxnative/`. People reading by hand: [docs/install.md](docs/install.md).

## 1. Check the Mac

From the repository folder:

```sh
sw_vers -productVersion; uname -m
xcode-select -p; ls -d /Applications/Xcode.app
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun -sdk macosx metal --version
command -v zstd spirv-cross dotnet python3.14
df -h .
```

| Needed | If not |
|---|---|
| `arm64` (Apple silicon) | Stop: Intel Macs cannot run the game's ARM64 code. |
| macOS: tested on 27.0 | Older versions are untested; try, and say so if it fails. |
| The full **Xcode** app (the Command Line Tools alone cannot build the shaders) | The player installs Xcode from the App Store and opens it once. `build_app.py` sets `DEVELOPER_DIR` itself when `xcode-select` points at the Command Line Tools. |
| The Metal compiler runs | `xcodebuild -downloadComponent MetalToolchain` (Xcode 26 and later install it separately). |
| `zstd`, `spirv-cross` | `brew install zstd spirv-cross` (Homebrew from <https://brew.sh>; the player runs its installer). |
| `dotnet` (.NET 10 SDK) | `brew install --cask dotnet-sdk` (asks for the password: the player runs it). |
| Python 3.14 with `lz4`, `capstone`, `fonttools` | `brew install python@3.14`, then `python3.14 -m venv ~/.venvs/ffr && ~/.venvs/ffr/bin/pip install lz4 capstone fonttools`. Use `~/.venvs/ffr/bin/python` below. |

## 2. Find the player's files

Ask where they are, or look for names only (never open `prod.keys`):

```sh
find ~ /Volumes -maxdepth 5 \( -iname '*.xci' -o -iname '*.xcz' -o -iname '*.nsp' -o -iname '*.nsz' -o -iname 'prod.keys' \) 2>/dev/null
```

| Title ID | What | Needed |
|---|---|---|
| `0100B8A026F14000` | base game (v0) | yes |
| `0100B8A026F14800` | update 1.0.1 (v65536) | yes |
| `0100B8A026F15002` | DLC | optional |

What is accepted:

- **One combined XCI** holding all of them (the usual card image). `build_app.py` splits it. To see what an XCI holds without writing anything: `dotnet run -c Release --project tools/split_xci -- "<prod.keys>" "<game.xci>"`; it lists one `CNMT` line per title.
- **A folder of NSPs** (NSZ works too). Put only this game's files in it.
- An **XCZ**: decompress it first with the player's own `nsz -D` (`pip install nsz`).
- **Another region or version is not supported here.** The other release, `0100606026F12000`, is supported by nxnative itself (`nxnative/README.md`), not by `build_app.py`.

Also needed: **`prod.keys`**, passed as a path (`-k`); do not read or print it.

**Disk:** a fresh build from an XCI took about 47 GB on an M5 Max: the split NSPs 9.2 GB, the extracted game 8.4 GB, the translated shaders 27 GB, the pipeline archive 0.7 GB, and the app, whose 12 GB of game files are clones of the extracted ones (they take no space of their own). Allow about 60 GB free, on one **APFS** volume. If the game file is on an exFAT disk or a full one, use `--work <folder on an APFS disk with room>` and put `-o` on that disk too.

## 3. Build

Run from the repository folder. Quote every path (dump names contain spaces and brackets). Pick the app's final place now with `-o`, on the same volume as the work folder.

```sh
# from a combined XCI (the work folder ffr-mac-work/ goes beside it)
~/.venvs/ffr/bin/python build_app.py "<game.xci>" -k "<prod.keys>" -o "<folder>/FINAL FANTASY RESONANCE.app"

# from a folder of NSPs (the work folder 0100B8A026F14000_build/ goes inside it)
~/.venvs/ffr/bin/python build_app.py "<NSP folder>" -k "<prod.keys>" -o "<folder>/FINAL FANTASY RESONANCE.app"
```

- Language: Simplified Chinese by default; `-l zh-Hant` or `-l ko` for the others.
- Run it once with `--dry-run` and show the player where the work folder and the app will go.
- If it says the app exists: ask whether to build over it (`--replace`) or use another `-o`. Quit the game first.

## 4. Run it to the end

- A fresh build takes **about 85 minutes** on an M5 Max (measured: 86 minutes from an XCI with no cache): split the XCI (9 s), extract (6 s), rewrite the code (4 min), decode and split the shaders (41 s), **compile 73,496 shaders for the Mac (78 min)**, build the pipeline archive (1:40), pack and sign the app (1:05).
- Start it in the background or with a long timeout, and keep the output in a log outside the repository: `… 2>&1 | tee ~/Library/Logs/ffr-build.log`. Read the log every few minutes. Lines look like `[14:18:31] [7/9] Mac shaders · compiling: 9%, 6,873/73,496 shaders … ETA 1:07:11`. Do **not** stop it for being slow.
- `✓ [7/9] Mac shaders …: 73,488 of 73,496 shaders ready …; 8 failed` is **expected**: eight compute shaders use image atomics the translator does not support ([known issues](docs/known-issues.md)). It is not an error.
- If it was interrupted anyway, run **exactly the same command** again: finished steps are reused.
- Never run two builds on the same work folder at once.
- It worked when the last lines say `done: <path of the app>` (about 12 GB, ad-hoc signed), followed by where the saves and the log are.

If the build stops, `build_app.py` prints `error: …` and the nxnative build prints `[RED] …`, with each stage's full output in `<NSP folder>/0100B8A026F14000_build/logs/`. Fix the cause and run the same command again.
