"""Analyzer edits invalidate derived tables without rebuilding shader libraries."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / 'dm/gfx/tools'
sys.path.insert(0, str(TOOLS))
from dmsh_analysis_stamp import SOURCES, table_version

spec = importlib.util.spec_from_file_location('nsp_analysis_stamps', REPO / 'scripts/build_ipa_from_nsp.py')
nsp = importlib.util.module_from_spec(spec); spec.loader.exec_module(nsp)


class AnalysisTableStamps(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='analysis_table_stamps_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.tools = self.repo / 'dm/gfx/tools'; self.tools.mkdir(parents=True)
        for name in set(sum(SOURCES.values(), ())) | {'dmsh_chain.py'}:
            shutil.copyfile(TOOLS / name, self.tools / name)
        self.sh = self.root / 'shader'
        self.lib = self.sh / 'cache/b-unit'; self.lib.mkdir(parents=True)
        (self.lib / 'inputs.tsv').write_text('game\tunit\ninputs\tfixture\n')
        (self.lib / 'shader_b.metal').write_text('float4 a = (*spvDescriptorSet0.vp_c3)._m0[1]; (vp_c1)._m0[input];')
        (self.lib / 'shader_b.json').write_text('{"c1":"", "bindings":[{"kind":"cbuf","reg":0,"msl_name":"vp_c3"}]}')
        self.g = types.SimpleNamespace(table=lambda name: self.root / name)
        self.env = dict(os.environ, DMSH_ANALYSIS_JOBS='1', DMSH_ANALYSIS_CACHE=str(self.root / 'analysis-cache'))
        self.ran = []
        def run(cmd, log, env=None):
            self.ran.append(Path(cmd[1]).name)
            subprocess.run(list(map(str, cmd)), env=env, capture_output=True, text=True, check=True, timeout=30)
        for cm in (mock.patch.object(nsp, 'run', run), mock.patch.object(nsp, 'PROG', mock.Mock()), mock.patch.object(nsp, 'say')):
            cm.__enter__(); self.addCleanup(cm.__exit__, None, None, None)

    def tables(self):
        self.assertTrue(nsp.chain_tables(sys.executable, self.repo, self.sh, self.env, self.root / 'logs', self.g, 'unit'))

    def edit(self, name):
        path = self.tools / name; stat = path.stat()
        data = path.read_bytes()
        # Change one comment byte, retaining size, timestamp and executable code.
        lines = data.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.lstrip().startswith(b'#') and b' ' in line.lstrip():
                pos = line.index(b' ', line.index(b'#'))
                lines[i] = line[:pos] + b'_' + line[pos + 1:]
                break
        else:
            # The shared parser starts with a module docstring and has no comments.
            pos = data.index(b'Shared')
            lines = [data[:pos] + b'shared' + data[pos + len(b'Shared'):]]
        changed = b''.join(lines)
        self.assertNotEqual(changed, data)
        self.assertEqual(len(changed), len(data))
        path.write_bytes(changed)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    def test_missing_stamps_then_repeated_build_reuses_both(self):
        self.tables(); self.assertEqual(self.ran, ['c1_guard.py', 'cb_extent.py'])
        for name, tool, _ in nsp.CHAIN_TABLES:
            head = self.g.table(name).read_text().splitlines()[0]
            self.assertTrue(head.startswith('# chain b-unit inputs fixture '))   # renderer's unchanged prefix check
            self.assertTrue(head.endswith(' analysis ' + table_version(tool, self.tools)))
        self.ran.clear(); self.tables(); self.assertEqual(self.ran, [])

    def test_cb_source_change_regenerates_only_cb(self):
        self.tables()
        c1 = self.g.table('c1_full.tsv').read_bytes()
        stamp = (self.lib / 'inputs.tsv').read_bytes()
        self.edit('cb_extent.py')
        self.ran.clear(); self.tables(); self.assertEqual(self.ran, ['cb_extent.py'])
        self.assertEqual(self.g.table('c1_full.tsv').read_bytes(), c1)
        self.assertEqual((self.lib / 'inputs.tsv').read_bytes(), stamp)
        self.ran.clear(); self.tables(); self.assertEqual(self.ran, [])

    def test_legacy_headers_are_upgraded_once_without_changing_rows(self):
        self.tables()
        rows = {}
        for name, _, _ in nsp.CHAIN_TABLES:
            path = self.g.table(name); lines = path.read_text().splitlines()
            rows[name] = lines[1:]
            lines[0] = lines[0].partition(' analysis ')[0]
            path.write_text('\n'.join(lines) + '\n')
        self.ran.clear(); self.tables(); self.assertEqual(self.ran, ['c1_guard.py', 'cb_extent.py'])
        for name, expected in rows.items():
            self.assertEqual(self.g.table(name).read_text().splitlines()[1:], expected)
        self.ran.clear(); self.tables(); self.assertEqual(self.ran, [])

    def test_c1_interval_dependency_regenerates_both(self):
        self.tables(); self.edit('c1_guard.py')
        self.ran.clear(); self.tables(); self.assertEqual(self.ran, ['c1_guard.py', 'cb_extent.py'])
        self.ran.clear(); self.tables(); self.assertEqual(self.ran, [])

    def test_shared_parser_dependency_regenerates_both(self):
        self.tables(); self.edit('dmsh_analysis.py')
        self.ran.clear(); self.tables(); self.assertEqual(self.ran, ['c1_guard.py', 'cb_extent.py'])


if __name__ == '__main__':
    unittest.main()
