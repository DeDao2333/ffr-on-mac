#!/usr/bin/env python3
"""Build FINAL FANTASY RESONANCE (Asian release, 0100B8A026F14000) into a Mac app.

Usage: python3 build_app.py <game.xci | NSP folder> -k <prod.keys> [-o <app>] [options]
  An XCI is first split into base / update / DLC NSPs (tools/split_xci) in a work folder beside it,
  ffr-mac-work/ by default. A folder of NSPs is used as it is. nxnative (nxnative/) then builds the app;
  its intermediate files go to <title ID>_build/ in the NSP folder and are reused by the next build.
  The game file and the keys are only read. -h lists the options.
"""
import argparse, os, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NXNATIVE = ROOT / 'nxnative'
SPLIT = ROOT / 'tools' / 'split_xci'
XCODE = Path('/Applications/Xcode.app/Contents/Developer')
TOOL_PATHS = ['/opt/homebrew/bin', '/usr/local/bin', '/usr/local/share/dotnet', os.path.expanduser('~/.dotnet')]
APP_NAME = 'FINAL FANTASY RESONANCE.app'


def fail(msg):
    sys.exit(f'error: {msg}')


def run(cmd, env, dry):
    print('$ ' + ' '.join(f"'{c}'" if ' ' in str(c) else str(c) for c in cmd), flush=True)
    if not dry:
        r = subprocess.run([str(c) for c in cmd], env=env)
        if r.returncode:
            sys.exit(r.returncode)


def developer_dir(env):
    """Xcode itself, not the Command Line Tools: the build needs xcodebuild and the Metal compiler."""
    if env.get('DEVELOPER_DIR'):
        return
    sel = subprocess.run(['xcode-select', '-p'], capture_output=True, text=True).stdout.strip()
    if sel.endswith('/Contents/Developer'):
        return
    if not XCODE.is_dir():
        fail('Xcode is not installed (the Command Line Tools alone cannot build the shaders)')
    env['DEVELOPER_DIR'] = str(XCODE)
    print(f'using DEVELOPER_DIR={XCODE} (xcode-select points at {sel or "nothing"})')


def nsp_folder(game, work, keys, env, dry):
    """The folder of NSPs to build from: the game folder itself, or the split of an XCI."""
    if game.is_dir():
        if any(p.suffix.lower() in ('.nsp', '.nsz') for p in game.iterdir()):
            return game, False
        xcis = [p for p in game.iterdir() if p.suffix.lower() == '.xci']
        if len(xcis) != 1:
            fail(f'{game}: no NSP files and {len(xcis)} XCI files (want NSPs, or exactly one XCI)')
        game = xcis[0]
    if game.suffix.lower() != '.xci':
        fail(f'{game}: not an XCI file or a folder of NSPs')
    if not shutil.which('dotnet', path=env['PATH']):
        fail('dotnet not found (brew install --cask dotnet-sdk); it splits the XCI')
    out = (work or game.parent / 'ffr-mac-work') / 'nsp'
    if not dry:
        out.mkdir(parents=True, exist_ok=True)
    run(['dotnet', 'run', '-c', 'Release', '--project', SPLIT, '--', keys, game, out], env, dry)
    return out, True


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('game', type=Path, help='the game: a combined XCI, or a folder of NSPs (base, update 1.0.1, optional DLC)')
    ap.add_argument('-k', '--keys', type=Path, required=True, help='your prod.keys')
    ap.add_argument('-o', '--out', type=Path, help=f'the app to write (default: {APP_NAME} in the work folder)')
    ap.add_argument('--work', type=Path, help='work folder for an XCI split (default: ffr-mac-work beside the XCI)')
    ap.add_argument('-l', '--language', default='zh-Hans', help='game language: zh-Hans (default), zh-Hant or ko')
    ap.add_argument('--replace', action='store_true', help='replace the app if it exists')
    ap.add_argument('--dry-run', action='store_true', help='print the commands without running them')
    a = ap.parse_args()
    game, keys = a.game.expanduser().resolve(), a.keys.expanduser().resolve()
    if not game.exists():
        fail(f'{game}: not found')
    if not keys.is_file():
        fail(f'{keys}: not found')
    if sys.platform != 'darwin' or os.uname().machine != 'arm64':
        fail('needs an Apple silicon Mac')
    env = dict(os.environ)
    env['PATH'] = os.pathsep.join([env.get('PATH', '')] + [p for p in TOOL_PATHS if p not in env.get('PATH', '')])
    developer_dir(env)
    nsp, split = nsp_folder(game, a.work and a.work.expanduser().resolve(), keys, env, a.dry_run)
    out = (a.out.expanduser().resolve() if a.out else (nsp.parent if split else nsp) / APP_NAME)
    if out.exists() and not a.replace:
        fail(f'{out} exists; pass --replace to build over it, or -o for another name')
    run([sys.executable, NXNATIVE / 'scripts' / 'build_ipa_from_nsp.py', nsp, '-k', keys,
         '--platform', 'mac-app', '--language', a.language, '-o', out], env, a.dry_run)
    if not a.dry_run:
        print(f'\ndone: {out}\nsaves: ~/Library/Application Support/nxnative/0100B8A026F14000/\n'
              f'log:   ~/Library/Logs/nxnative/0100B8A026F14000.log')


if __name__ == '__main__':
    main()
