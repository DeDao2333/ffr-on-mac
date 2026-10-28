"""The shared cache must preserve facts and invalidate by actual input bytes."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import dmsh_analysis as analysis


class SharedAnalysis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'shader_b.metal'
        self.reflection = self.root / 'shader_b.json'
        self.source.write_text('uint _1 = 3u; float4 x = (*spvDescriptorSet0.fp_c1)._m0[_1];')
        self.reflection.write_text('{"c1":"00"}')
        env = mock.patch.dict(os.environ, DMSH_ANALYSIS_CACHE=str(self.root / 'cache'))
        env.start(); self.addCleanup(env.stop)

    def run_cached(self, evaluate, namespace='c1_guard'):
        return analysis.analyze_cached(self.source, self.reflection, namespace, evaluate)

    def test_definitions_and_binding_facts(self):
        parsed = analysis.parse_source('''uint _1 = 3u; uint _2 = 4u; _2++;
uint _1 = 3u; fp_c1 [[id(0)]]; x = (*spvDescriptorSet0.fp_c1)._m0[_1];
y = (*spvDescriptorSet0.fp_c1)._m0[_1]; z = (*spvDescriptorSet0.buf)._m0[2];''')
        self.assertEqual(parsed.definitions, {'_1': ('uint', '3u')})
        self.assertEqual(parsed.c1_accesses, ('_1',))
        self.assertEqual(parsed.identifier_uses['fp_c1'], 3)
        self.assertEqual(parsed.uniform_reads['fp_c1'], ('_1', '_1'))
        self.assertEqual(parsed.declarations['fp_c1'], 1)
        self.assertTrue(parsed.storage_unwritten['buf'])

    def test_shared_parsing_across_tables_and_cached_none(self):
        with mock.patch.object(analysis, 'parse_source', wraps=analysis.parse_source) as parse:
            self.assertFalse(self.run_cached(lambda p, r: False))
            self.assertEqual(self.run_cached(lambda p, r: ['0:64'], 'cb_extent'), ['0:64'])
            self.assertEqual(parse.call_count, 1)
            self.assertFalse(self.run_cached(lambda p, r: self.fail('result should be cached')))
            self.assertIsNone(self.run_cached(lambda p, r: None, 'none'))
            self.assertIsNone(self.run_cached(lambda p, r: self.fail('None should be cached'), 'none'))

    def test_source_invalidation_even_with_preserved_size_and_mtime(self):
        stamp = self.source.stat()
        self.assertFalse(self.run_cached(lambda p, r: False))
        self.source.write_text(self.source.read_text().replace('3u', '4u'))
        os.utime(self.source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertTrue(self.run_cached(lambda p, r: p.definitions['_1'][1] == '4u'))

    def test_reflection_and_analysis_version_invalidate_results(self):
        self.assertFalse(self.run_cached(lambda p, r: False))
        self.reflection.write_text('{"c1":"0000"}')
        self.assertTrue(self.run_cached(lambda p, r: True))
        with mock.patch.object(analysis, '_version', return_value='new-algorithm'):
            self.assertFalse(self.run_cached(lambda p, r: False))

    def test_corrupt_or_incomplete_cache_is_rebuilt(self):
        self.run_cached(lambda p, r: False)
        for path in (self.root / 'cache').rglob('*.json.z'):
            path.write_bytes(b'partial cache')
        self.assertTrue(self.run_cached(lambda p, r: True))
        result = next((self.root / 'cache/results').rglob('*.json.z'))
        data = json.loads(zlib.decompress(result.read_bytes()))
        data['value'] = {'invalid': True}
        result.write_bytes(zlib.compress(json.dumps(data).encode()))
        self.assertFalse(self.run_cached(lambda p, r: False))

    def test_disabled_or_unwritable_cache_recomputes(self):
        with mock.patch.dict(os.environ, DMSH_ANALYSIS_CACHE='0'):
            self.assertTrue(self.run_cached(lambda p, r: True))
            self.assertFalse(self.run_cached(lambda p, r: False))
        blocked = self.root / 'not-a-directory'; blocked.write_text('x')
        with mock.patch.dict(os.environ, DMSH_ANALYSIS_CACHE=str(blocked)):
            self.assertTrue(self.run_cached(lambda p, r: True))

    def test_bad_reflection_does_not_prevent_conservative_analysis(self):
        self.reflection.write_text('broken')
        self.assertTrue(self.run_cached(lambda p, r: r is None))
        self.reflection.unlink()
        self.assertTrue(self.run_cached(lambda p, r: r is None))


if __name__ == '__main__':
    unittest.main()
