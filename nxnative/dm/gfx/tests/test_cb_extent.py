"""Uniform-range and storage-write analysis of synthetic translated MSL."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import cb_extent
import dmsh_analysis


def binding(name='vp_c3', reg=0, kind='cbuf'):
    return dict(msl_name=name, reg=reg, kind=kind)


def uniform(index, name='vp_c3'):
    return f'(*spvDescriptorSet0.{name})._m0[{index}]'


class UniformRanges(unittest.TestCase):
    def check(self, source, bindings, expected):
        self.assertEqual(cb_extent.extents(source, bindings), expected)
        parsed = dmsh_analysis.parse_source(source)
        with mock.patch.object(cb_extent, 'parse_source', side_effect=AssertionError('parsed twice')):
            self.assertEqual(cb_extent.extents(parsed, bindings), expected)

    def test_bounded_indices_and_repeated_reads(self):
        source = ('constant Block* vp_c3 [[id(0)]]; uint _1 = input & 7u; '
                  f'float4 a = {uniform("_1")}; float4 b = {uniform("_1")}; '
                  f'float4 c = {uniform("12")};')
        self.check(source, [binding()], {0: 208})

    def test_unread_binding_and_argument_buffer_declarations(self):
        self.check('constant Block* vp_c3 [[id(0)]];', [binding()], {0: 0})
        self.check('', [binding()], {0: 0})
        source = f'constant Block* vp_c3 [[id(0)]]; float4 a = {uniform("2")};'
        self.check(source + source, [binding()], {0: 48})

    def test_unknown_negative_and_reassigned_indices_are_unbounded(self):
        for index, definitions in [('input', ''), ('-1', ''), ('_1', 'uint _1 = input & 7u; _1 += 1u;')]:
            with self.subTest(index=index):
                self.check(definitions + uniform(index) + ';', [binding()], {})

    def test_exact_maximum_is_omitted(self):
        self.check(uniform('4094') + ';', [binding()], {0: cb_extent.MAX - 16})
        for index in ('4095', '4096'):
            with self.subTest(index=index):
                self.check(uniform(index) + ';', [binding()], {})

    def test_other_uses_make_uniform_range_unknown(self):
        for extra in ('consume(spvDescriptorSet0.vp_c3);',
                      '(*spvDescriptorSet0.vp_c3)._m1[0];',
                      'float vp_c3 = 1;'):
            with self.subTest(extra=extra):
                self.check(uniform('0') + ';' + extra, [binding()], {})
        self.check(uniform('0') + '; vp_c30;', [binding()], {0: 16})

    def test_only_uniform_bindings_with_registers_are_used(self):
        self.check(uniform('1') + ';', [binding(), binding(kind='sbuf'),
                                      dict(kind='cbuf', msl_name='missing')], {0: 32})


class StorageWrites(unittest.TestCase):
    def check(self, source, expected):
        bindings = [binding('cp_s3', 3, 'sbuf'), binding('cp_s3', 3, 'sbuf'),
                    binding('absent', 1, 'sbuf'), binding('cp_s3', 9, 'cbuf'),
                    dict(kind='sbuf', msl_name='cp_s3')]
        self.assertEqual(cb_extent.unwritten(source, bindings), expected)
        parsed = dmsh_analysis.parse_source(source)
        with mock.patch.object(cb_extent, 'parse_source', side_effect=AssertionError('parsed twice')):
            self.assertEqual(cb_extent.unwritten(parsed, bindings), expected)

    def test_reads_nested_members_and_absent_bindings(self):
        for expression in ('[3]', '[index[0]].member[2]', '[0].x'):
            with self.subTest(expression=expression):
                self.check(f'float v = (*spvDescriptorSet0.cp_s3)._m0{expression};', [1, 3])
        self.check('consume(spvDescriptorSet0.cp_s30);', [1, 3])

    def test_writes_addresses_and_pointer_escape(self):
        for expression in ('(*spvDescriptorSet0.cp_s3)._m0[0] = value;',
                           '(*spvDescriptorSet0.cp_s3)._m0[0].x += value;',
                           '(*spvDescriptorSet0.cp_s3)._m0[0] <<= 1;',
                           '(*spvDescriptorSet0.cp_s3)._m0[0]++;',
                           'atomic_add(&(*spvDescriptorSet0.cp_s3)._m0[0], 1);',
                           'consume(spvDescriptorSet0.cp_s3);',
                           'consume((*spvDescriptorSet0.cp_s3));',
                           '(*spvDescriptorSet0.cp_s3)._m1[0];'):
            with self.subTest(expression=expression):
                self.check(expression, [1])

    def test_comparisons_are_reads(self):
        for operator in ('==', '!=', '<=', '>='):
            with self.subTest(operator=operator):
                self.check(f'bool b = (*spvDescriptorSet0.cp_s3)._m0[0] {operator} value;', [1, 3])


class TableRows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='cb_extent_test_')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {'DMSH_ANALYSIS_CACHE': str(self.root / 'cache'),
                                                'DMSH_ANALYSIS_JOBS': '1'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def shader(self, key='shader', source=None, reflection=None, suffix='_b'):
        path = self.root / f'{key}{suffix}.json'
        if source is None: source = uniform('1') + ';'
        if reflection is None: reflection = dict(bindings=[binding()])
        path.write_text(json.dumps(reflection))
        path.with_suffix('.metal').write_text(source)
        return str(path)

    def test_cache_tokens_do_not_capture_the_shader_key(self):
        first = self.shader('first')
        second = self.shader('second')
        self.assertEqual(cb_extent.row(first), 'first\t0:32')
        with mock.patch.object(cb_extent, '_tokens', side_effect=AssertionError('cache miss')):
            self.assertEqual(cb_extent.row(first), 'first\t0:32')
            self.assertEqual(cb_extent.row(second), 'second\t0:32')

    def test_source_and_reflection_changes_invalidate_cache(self):
        path = Path(self.shader())
        self.assertEqual(cb_extent.row(str(path)), 'shader\t0:32')
        path.with_suffix('.metal').write_text(uniform('2') + ';')
        self.assertEqual(cb_extent.row(str(path)), 'shader\t0:48')
        path.write_text(json.dumps(dict(bindings=[binding(reg=5)])))
        self.assertEqual(cb_extent.row(str(path)), 'shader\t5:48')

    def test_missing_malformed_or_irrelevant_inputs_have_no_row(self):
        path = Path(self.shader())
        path.write_text('{broken')
        self.assertIsNone(cb_extent.row(str(path)))
        path.unlink()
        self.assertIsNone(cb_extent.row(str(path)))
        path = Path(self.shader())
        path.with_suffix('.metal').unlink()
        self.assertIsNone(cb_extent.row(str(path)))
        path = self.shader(reflection=dict(bindings=[binding(kind='texture')]))
        self.assertIsNone(cb_extent.row(path))

    def test_table_filters_reflection_names_and_keeps_headers_and_order(self):
        source = uniform('3', 'vp_c8') + '; ' + uniform('1') + ';'
        reflection = dict(bindings=[binding('vp_c8', 8), binding(), binding('s6', 6, 'sbuf'),
                                    binding('s2', 2, 'sbuf')])
        self.shader('z', source, reflection)
        self.shader('a')
        self.shader('ignored', suffix='')
        (self.root / 'unused_b.metal').write_text(uniform('0') + ';')
        output = self.root / 'tables' / 'cb_extent.tsv'
        with mock.patch.object(sys, 'argv', ['cb_extent.py', str(self.root), str(output)]), \
             mock.patch.object(cb_extent.dmsh_chain, 'table_head', return_value='# chain b-test inputs test'), \
             contextlib.redirect_stdout(io.StringIO()) as said:
            cb_extent.main()
        lines = output.read_text().splitlines()
        self.assertRegex(lines[0], r'^# chain b-test inputs test \d{4}-\d\d-\d\dT')
        self.assertEqual(lines[1], '# shader key, then reg:bytes for each uniform slot whose reads stay inside its first bytes, and s<reg> for each storage slot it never writes (cb_extent.py)')
        self.assertEqual(lines[2:], ['a\t0:32', 'z\t0:32 8:64 s2 s6'])
        self.assertEqual(said.getvalue().splitlines()[-1],
                         'bounded uniform reads: 3 slots; storage slots never written: 2; 2 shaders')


if __name__ == '__main__':
    unittest.main()
