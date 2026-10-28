# Contributing

## Never post game content or keys

Issues and pull requests that contain or ask for game files, keys, firmware, saves, extracted assets, screenshots of extracted assets, or a built app will be deleted. That includes links to them. A built app contains the game.

## Reporting a problem

Include:

- Your Mac model, chip and macOS version
- The game version (the build should be 1.0.1, main build `b7f965b9d7227db8e29f04e84ea7331b`)
- What you did and what you saw; where in the game it happens
- The relevant part of `~/Library/Logs/nxnative/0100B8A026F14000.log`, especially lines with `[RED]`. Replace your user name in paths with `<user>`

A gameplay screenshot of the problem is fine.

## Pull requests

- One change per pull request, with how you verified it (before/after numbers on the same save and route, screenshots at the same frame)
- Run `python3 tools/check_publish.py --deny <your user name>` before you push; it must print `ok: nothing found`
- Changes to the toolchain itself (`nxnative/`) are better sent to [upstream nxnative](https://github.com/seedds/nxnative) when they are not specific to this game, so every game gets them

## Updating the vendored toolchain

`nxnative/` is a copy of an nxnative checkout, not a submodule, so the repository builds without network access to a fork. To refresh it from a local checkout (which may carry uncommitted changes):

```sh
python3 tools/sync_nxnative.py <path to the nxnative checkout>
```

It copies every file git would commit there, leaves out the paths it lists (game cover art, compiled test binaries), and rewrites `nxnative/VENDORED.txt` with the base commit and the changed files. Rebuild and re-run the Battlefield checks before committing a refresh.
