# Addresses and changes for this build

Everything here applies to main build `b7f965b9d7227db8e29f04e84ea7331b` (Asian release 1.0.1). nxnative selects these changes by build ID, so other versions never get them. Offsets are from the start of the main module.

## Allocator: no 38 GiB FMallocBinned3 reservation

Unreal Engine 5.6 constructs FMallocBinned3 in `nninitStartup`, which reserves 38 GiB of address space at once. On the Mac that is neither needed nor practical.

| | Offset |
|---|---|
| Call `bl` | `main+0x137f0` → `main+0xae770` |
| Allocator object | `main+0x77c6210` |
| 32-slot vtable | `main+0x67df800` |
| 38 GiB reservation | `main+0xae860` |

The call becomes a `nop` (`nxnative/dm/games/0100B8A026F14000/patches.py`, which asserts the original bytes first), and the host's allocator stands in (`nxnative/dm/host/hle_uemalloc.c`).

## Frame rate: t.MaxFPS and rhi.SyncInterval

Both console variables register through UTF-16 names; the `TAutoConsoleVariable` object they construct keeps its value pointer at `+0x10`. The host holds the values there (`nxnative/dm/host/hle_fpslock.c`).

| Console variable | Name | Registration call | Object |
|---|---|---|---|
| `t.MaxFPS` | `main+0x6179a4a` | `main+0x33a4dec` | `main+0x8c29af0` |
| `rhi.SyncInterval` | `main+0x6046432` | `main+0x149e0bc` | `main+0x8adca10` |

To find them: search the main module for the variable's name in UTF-16LE, then for the `adrp` + `add` pair that points at it. After the registration call another `adrp` + `add` loads the object, a constructor runs, and the value pointer is stored to `[object + 0x10]`.

## Found, not used yet: r.ScreenPercentage

Located for raising the internal render resolution; the host has no row for it yet.

| | Offset |
|---|---|
| Name (UTF-16) | `main+0x60fba5e` |
| Registration | `main+0x2d4d518` (flags `0x40`) |
| Bound global | `main+0x8bf6de8` |
| Query that caches the `IConsoleVariable*` | `main+0x30c0c64` (cached at `main+0x8c18418+8`) |

`r.DynamicRes.OperationMode` has its name at `main+0x610a050` and a reference at `main+0x33a45c8`. Holding a fixed resolution also needs dynamic resolution off.

## Languages and input files

- The control data lists Korean, Traditional Chinese and Simplified Chinese; `build_app.py` picks Simplified Chinese unless told otherwise
- The combined XCI's content order: see [Install · What it does](install.md#what-it-does)

## The other release

The SDK and movie modules are the same builds as the other release's (`0100606026F12000`, in upstream nxnative). Memory settings, dynamic particle texture slots and the manual integer-output shader fact follow that profile. Both releases have the same 73,496 shader keys, but their shader-map source positions differ, so this profile registers tables generated from its own files.
