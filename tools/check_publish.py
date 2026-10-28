#!/usr/bin/env python3
"""Check the files this repository would publish for game data, keys and personal paths.

Usage: python3 tools/check_publish.py [--deny WORD ...] [--max-mb N]
  Checks the files git would commit (tracked plus untracked, minus .gitignore), or every file
  when the folder is not a git repository yet. Exit code 0 when nothing is found, 1 otherwise.
  --deny: extra words that must not appear (your user name, nickname, save folder name...).
"""
import argparse, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

# Game images, extracted content, build products, keys and saves.
BAD_EXT = {'.xci', '.xcz', '.nsp', '.nsz', '.nca', '.nso', '.nro', '.pak', '.ucas', '.utoc',
           '.uasset', '.uexp', '.bin', '.keys', '.sav', '.ipa', '.metallib', '.dylib', '.png',
           '.jpg', '.mov', '.mp4', '.wav'}
BAD_PARTS = {'romfs', 'exefs', 'inputs', 'save', 'save.work', 'merged_probe'}
# The vendored toolchain: its generated shader tables are larger than notes, and its shader translator
# ships as .NET assemblies (built from nxnative/dm/third_party/ryujinx and nxnative/dm/gfx/tools/dmsh_xlate).
VENDOR = 'nxnative/'
VENDOR_MAX_MB = 20.0
VENDOR_BINARY = ('nxnative/dm/gfx/tools/dmsh_xlate_bin/',)
# Screenshots shown on the README pages: JPG or PNG under docs/screenshots/ only.
SCREENSHOTS = 'docs/screenshots/'
SCREENSHOT_EXT = {'.jpg', '.png'}
TEXT_RULES = [
    ('home path', re.compile(r'/(?:Users|home)/(?!<)[^/\s"\'`)]+/')),
    ('key file line', re.compile(r'^\s*[a-z][a-z0-9_]*\s*=\s*[0-9a-fA-F]{32,}\s*$', re.M)),
    ('encryption key setting', re.compile('Encryption' + r'Key\s*=', re.I)),
    ('private key', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
]


def files():
    if (ROOT / '.git').exists():
        out = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z', '--cached', '--others',
                              '--exclude-standard'], check=True, capture_output=True).stdout
        return [ROOT / p for p in out.decode().split('\0') if p]
    return [p for p in ROOT.rglob('*') if p.is_file() and '.git' not in p.relative_to(ROOT).parts]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--deny', action='append', default=[], help='a word that must not appear')
    ap.add_argument('--max-mb', type=float, default=2.0, help='largest allowed file (default 2)')
    a = ap.parse_args()
    found = []
    for p in sorted(files()):
        if not p.is_file() or p.resolve() == SELF:
            continue
        rel = p.relative_to(ROOT)
        posix = rel.as_posix()
        shot = posix.startswith(SCREENSHOTS) and p.suffix.lower() in SCREENSHOT_EXT
        if p.suffix.lower() in BAD_EXT and not shot:
            found.append((rel, f'file type {p.suffix}'))
        if BAD_PARTS & set(rel.parts[:-1]) or rel.parts[0].endswith('.app'):
            found.append((rel, 'inside a game/save/build folder'))
        size = p.stat().st_size
        if size > (VENDOR_MAX_MB if posix.startswith(VENDOR) else a.max_mb) * 1024 * 1024:
            found.append((rel, f'{size / 1048576:.1f} MB'))
            continue
        data = p.read_bytes()
        if b'\0' in data[:8192]:
            if not posix.startswith(VENDOR_BINARY) and not shot:
                found.append((rel, 'binary file'))
            continue
        text = data.decode('utf-8', errors='replace')
        for name, rx in TEXT_RULES:
            for m in rx.finditer(text):
                line = text.count('\n', 0, m.start()) + 1
                found.append((rel, f'{name} at line {line}: {m.group(0).strip()[:60]}'))
        for w in a.deny:
            # ASCII words match whole words only ('lee' is not in 'LittleEndian'); others match anywhere
            rx = re.compile(rf'(?<![A-Za-z0-9_]){re.escape(w)}(?![A-Za-z0-9_])' if w.isascii() else re.escape(w), re.I)
            m = rx.search(text) if w else None
            if m:
                found.append((rel, f'denied word {w!r} at line {text.count(chr(10), 0, m.start()) + 1}'))
    for rel, why in found:
        print(f'{rel}: {why}')
    print(f'{len(found)} problem(s)' if found else 'ok: nothing found')
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
