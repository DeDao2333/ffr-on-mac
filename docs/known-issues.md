# Known issues

## Open

- **Eight compute shaders are not translated.** They use image atomics, which the translator does not support. The Battlefield route logs no missing library and shows no visible effect, but other scenes may use them.
- **Two latent problems with `DM_NVN_TBDR=1` only.** After `defer_replay` ends, `draw()` keeps using the ended encoder; a prepare during replay clears a Q draw's bindings. TBDR optimizations are off by default and this game's profile does not turn them on.
- **`t_tbuf_align` fails its wrapped-rows case** (views of more than 16,384 elements). This game has no fetch1d bindings; before the fix such reads returned 0 as well.

## Untested

- **Other chips.** The flicker fix exists because R32 buffer-texture views at offsets 4, 8 and 12 mod 16 read 0 on the M5 Max; upstream measured them reading correctly on an M6. The copy is safe either way, but M1 to M4 have not been tried.
- **The rest of the game.** Only the opening Battlefield and the first chapter were played.
- **Audio by ear.** Audio was measured on muted real CoreAudio output; nobody has listened systematically.
- **First visits.** Pipeline prewarming covers the recorded route only; a new area can stall once while its pipelines are built.
- **Long foreground sessions.** One windowed run had a 15.1 s display-link stall (88 ms active on recovery) and discarded frames, so the numbers so far do not prove uninterrupted frame pacing.

## Things to keep in mind

- Every app of this title ID shares one save folder; do not run two at once.
- Benchmarks and diagnostics should run on a copy of the save (`bench.py --save` copies it for each run).
- Changing the profile's `depth_mode` requires regenerating the offline pipeline archive; an archive that does not match startup makes the device rebuild base pipelines.
