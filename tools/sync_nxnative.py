#!/usr/bin/env python3
"""Copy a local nxnative checkout (committed files plus its uncommitted changes) into nxnative/.

Usage: python3 tools/sync_nxnative.py <nxnative checkout> [--dest nxnative]
  Copies every file git would commit in the checkout (tracked plus untracked, minus its .gitignore),
  except the paths in EXCLUDE, and writes nxnative/VENDORED.txt with the upstream commit it is based on.
  The checkout is only read. The destination is replaced only when it is an earlier copy made by this script.
"""
import argparse, datetime, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = 'https://github.com/seedds/nxnative'
MARKER = 'VENDORED.txt'
EXCLUDE = (
    'docs/images/',                          # game cover art: not ours to redistribute, not used by the build
    'dm/gfx/tests/t_tbuf_align',             # a compiled test binary
    'dm/gfx/tests/t_tbuf_align.dSYM/',
    'dm/gfx/nvn/nvn_tbuf_align_diag.inc',    # a diagnostic include nothing references
)


def git(src, *args):
    return subprocess.run(['git', '-C', str(src), *args], check=True, capture_output=True).stdout


def excluded(rel):
    return any(rel == e or (e.endswith('/') and rel.startswith(e)) for e in EXCLUDE)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('src', type=Path, help='the nxnative checkout')
    ap.add_argument('--dest', type=Path, default=ROOT / 'nxnative')
    a = ap.parse_args()
    src, dest = a.src.resolve(), a.dest.resolve()
    if not (src / 'scripts' / 'build_ipa_from_nsp.py').is_file():
        sys.exit(f'{src}: not an nxnative checkout')
    if dest.exists():
        if not (dest / MARKER).is_file():
            sys.exit(f'{dest}: exists and was not made by this script; not replacing it')
        shutil.rmtree(dest)
    base = git(src, 'rev-parse', 'HEAD').decode().strip()
    names = git(src, 'ls-files', '-z', '--cached', '--others', '--exclude-standard').decode().split('\0')
    changed = set(git(src, 'diff', '--name-only', '-z', 'HEAD').decode().split('\0')) - {''}
    added = set(git(src, 'ls-files', '-z', '--others', '--exclude-standard').decode().split('\0')) - {''}
    copied = skipped = 0
    for rel in filter(None, names):
        p = src / rel
        if excluded(rel) or not p.is_file():
            skipped += 1
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest / rel)
        copied += 1
    kept = lambda s: sorted(r for r in s if not excluded(r) and (src / r).is_file())
    lines = [f'nxnative, vendored from {UPSTREAM}',
             f'Base commit: {base}',
             f'Copied: {datetime.date.today().isoformat()}, {copied} files',
             'Local changes on top of the base commit (not in upstream yet):',
             *(f'  M {r}' for r in kept(changed)), *(f'  A {r}' for r in kept(added)),
             'Left out:', *(f'  {e}' for e in EXCLUDE), '']
    (dest / MARKER).write_text('\n'.join(lines))
    print(f'{copied} files copied to {dest}, {skipped} left out; base {base[:7]}, '
          f'{len(kept(changed))} modified and {len(kept(added))} added files')


if __name__ == '__main__':
    main()
