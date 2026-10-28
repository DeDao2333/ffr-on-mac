#!/usr/bin/env python3
"""Copy a local nxnative checkout (committed files plus its uncommitted changes) into nxnative/.

Usage: python3 tools/sync_nxnative.py <nxnative checkout> [--dest nxnative]
  Copies every file git would commit in the checkout (tracked plus untracked, minus its .gitignore),
  except the paths in EXCLUDE, and writes nxnative/VENDORED.txt with the upstream commit it is based on.
  Image links in the copied Markdown that point at left-out files are pointed at the same files upstream,
  pinned to that commit, so the pages still show them.
  The checkout is only read. The destination is replaced only when it is an earlier copy made by this script.
"""
import argparse, datetime, posixpath, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = 'https://github.com/seedds/nxnative'
RAW = 'https://raw.githubusercontent.com/seedds/nxnative'
MARKER = 'VENDORED.txt'
IMAGE = re.compile(r'(<img\b[^>]*?\bsrc="|!\[[^\]]*\]\()([^")\s]+)')
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


def relink_images(dest, base, upstream_files):
    """Point image links at left-out files to the same files upstream at the base commit; returns the pages changed."""
    changed = []
    for md in sorted(dest.rglob('*.md')):
        here = md.parent.relative_to(dest).as_posix()
        def fix(m):
            rel = posixpath.normpath(posixpath.join(here, m.group(2)))
            if '://' in m.group(2) or not excluded(rel) or rel not in upstream_files:
                return m.group(0)
            return f'{m.group(1)}{RAW}/{base}/{rel}'
        text = md.read_text(encoding='utf-8')
        new = IMAGE.sub(fix, text)
        if new != text:
            md.write_text(new, encoding='utf-8')
            changed.append(md.relative_to(dest).as_posix())
    return changed


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
    upstream_files = set(git(src, 'ls-tree', '-r', '--name-only', '-z', base).decode().split('\0'))
    relinked = relink_images(dest, base, upstream_files)
    kept = lambda s: sorted(r for r in s if not excluded(r) and (src / r).is_file())
    lines = [f'nxnative, vendored from {UPSTREAM}',
             f'Base commit: {base}',
             f'Copied: {datetime.date.today().isoformat()}, {copied} files',
             'Local changes on top of the base commit (not in upstream yet):',
             *(f'  M {r}' for r in kept(changed)), *(f'  A {r}' for r in kept(added)),
             'Left out:', *(f'  {e}' for e in EXCLUDE),
             'Image links pointed at upstream (the images are left out):', *(f'  {r}' for r in relinked), '']
    (dest / MARKER).write_text('\n'.join(lines))
    print(f'{copied} files copied to {dest}, {skipped} left out; base {base[:7]}, '
          f'{len(kept(changed))} modified and {len(kept(added))} added files')


if __name__ == '__main__':
    main()
