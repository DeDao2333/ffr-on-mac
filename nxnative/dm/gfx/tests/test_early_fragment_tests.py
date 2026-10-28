"""Early fragment tests come from bounded NVN controls; all fixtures are synthetic.

DMSH_TEST_XLATE=/path/to/rebuilt/dmsh_xlate also checks the emitted SPIR-V / MSL (requires spirv-cross).
"""
import ast
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
SOURCE = TOOLS / 'dmsh_build.py'
tree = ast.parse(SOURCE.read_text())
selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in ('early_fragment_tests', 'job_for')]
scope = {'struct': struct, 'os': os, 'FACTS': {}, 'vertex_ab': lambda *args: None, 'tf_layout': lambda *args: None, 'BINDLESS_TABLES': False}
exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), scope)
early_fragment_tests = scope['early_fragment_tests']
job_for = scope['job_for']


def record(flag=1, *, code_size=0x80, control_size=0x880, own_size=None):
    header = bytearray(b'BTCDBTCD' + bytes(0x30))
    struct.pack_into('<2I', header, 0x28, control_size, code_size)
    code = bytearray(code_size)
    code[:4] = bytes.fromhex('78563412')
    control = bytearray(control_size)
    control[:4] = bytes.fromhex('34127698')
    struct.pack_into('<I', control, 0x20, control_size if own_size is None else own_size)
    struct.pack_into('<2I', control, 0x700, code_size, code_size)
    control[0x718] = flag
    return header + code + control


class ControlFlag(unittest.TestCase):
    def test_flag_bit_and_stage(self):
        for flag in (0, 1, 2, 0x80, 0x81, 0xff):
            for stage in ('ps', 'vs', 'cs', 'gs', 'hs', 'ds', ''):
                with self.subTest(flag=flag, stage=stage):
                    self.assertIs(early_fragment_tests(stage, record(flag)), stage == 'ps' and bool(flag & 1))

    def test_header_lengths_locate_control_instead_of_raw_offset(self):
        for code_size in (0x80, 0x100, 0x1000):
            with self.subTest(code_size=code_size):
                self.assertTrue(early_fragment_tests('ps', record(code_size=code_size)))
        data = record(0, code_size=0x1000)
        data[0x718] = 1   # shader code, not the control's flag
        self.assertFalse(early_fragment_tests('ps', data))

    def test_short_control_is_not_the_0x880_layout(self):
        for size in (0x760, 0x7cc, 0x848, 0x850, 0x87f):
            with self.subTest(size=size):
                self.assertFalse(early_fragment_tests('ps', record(control_size=size)))
        self.assertFalse(early_fragment_tests('ps', record(own_size=0x848)))
        self.assertFalse(early_fragment_tests('ps', record(own_size=0x88c)))
        self.assertTrue(early_fragment_tests('ps', record(control_size=0x88c, own_size=0x880)))

    def test_truncated_and_trailing_records_are_not_read(self):
        data = record()
        for length in (0, 7, 8, 0x27, 0x28, 0x2f, 0x37, 0x38, 0xb8, len(data) - 1):
            with self.subTest(length=length):
                self.assertFalse(early_fragment_tests('ps', data[:length]))
        self.assertFalse(early_fragment_tests('ps', data + b'\x00'))
        for offset, value in ((0x28, 0xffffffff), (0x2c, 0xffffffff), (0x28, 0x87f), (0x2c, 0x81)):
            malformed = data.copy(); struct.pack_into('<I', malformed, offset, value)
            with self.subTest(offset=offset, value=value):
                self.assertFalse(early_fragment_tests('ps', malformed))

    def test_magic_and_control_code_length_must_match(self):
        ci = 0x38 + 0x80
        for offset in (0, 0x38, ci):
            data = record(); data[offset] ^= 1
            with self.subTest(offset=offset):
                self.assertFalse(early_fragment_tests('ps', data))
        data = record(); data[0x48:0x4c] = bytes.fromhex('34127698')
        self.assertFalse(early_fragment_tests('ps', data))   # job_for would find a different control
        data = record(); struct.pack_into('<I', data, ci + 0x704, 0x100)
        self.assertFalse(early_fragment_tests('ps', data))
        self.assertFalse(early_fragment_tests('ps', record(code_size=0x40)))


class TranslationJob(unittest.TestCase):
    def test_flag_reaches_jobs_with_or_without_profile_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            for facts in ({}, {'shader': {'fout': {'0': 'uint'}}}):
                for flag in (0, 1):
                    with self.subTest(facts=facts, flag=flag), mock.patch.dict(scope, FACTS=facts):
                        job, constants = job_for('shader', 'ps_synthetic.bin', record(flag), directory)
                        self.assertIs(job['early_fragment_tests'], bool(flag))
                        self.assertEqual(job['stage'], 'ps')
                        self.assertEqual(job.get('fout'), facts.get('shader', {}).get('fout', {}))
                        self.assertEqual(job.get('no_facts', False), not facts)
                        self.assertEqual(constants, b'')
                        self.assertEqual(Path(job['code']).read_bytes(), record(flag)[0x38 + 0x30:0x38 + 0x80])

    def test_profile_answers_cannot_override_control_or_stage(self):
        with tempfile.TemporaryDirectory() as directory:
            for stage in ('ps', 'vs', 'cs', 'gs'):
                for flag in (0, 1):
                    with self.subTest(stage=stage, flag=flag), mock.patch.dict(scope, FACTS={'shader': {'early_fragment_tests': True}}):
                        job, _ = job_for('shader', stage + '_synthetic.bin', record(flag), directory)
                        self.assertIs(job['early_fragment_tests'], stage == 'ps' and bool(flag))

    def test_bindless_tables_follow_the_profile_switch(self):
        # tex_dyn_slots on: the translator keeps handles built from c2 table words (dmsh_xlate QueryBindlessTableHandles); off: no key, as before
        with tempfile.TemporaryDirectory() as directory:
            for on in (False, True):
                for stage in ('vs', 'ps', 'cs'):
                    with self.subTest(on=on, stage=stage), mock.patch.dict(scope, BINDLESS_TABLES=on):
                        job, _ = job_for('shader', stage + '_synthetic.bin', record(0), directory)
                        self.assertEqual('bindless_tables' in job, on)
                        if on: self.assertIs(job['bindless_tables'], True)


class ProfileRows(unittest.TestCase):
    def test_tex_dyn_slots_is_a_shader_input(self):
        import sys, types
        sys.path.insert(0, str(TOOLS)); import dmsh_chain
        def rows(*pairs): return dmsh_chain.profile_rows(types.SimpleNamespace(rows=[(k, [v]) for k, v in pairs]))
        self.assertEqual(rows(('tex_dyn_slots', 'on'), ('fps', '30')), b'tex_dyn_slots\ton\n')
        self.assertNotEqual(rows(('tex_dyn_slots', 'on')), rows(('tex_dyn_slots', 'off')))
        self.assertEqual(rows(('nvn_cut', '64'), ('shader_x', 'y')), b'nvn_cut\t64\nshader_x\ty\n')


@unittest.skipUnless(os.environ.get('DMSH_TEST_XLATE') and shutil.which('spirv-cross'), 'set DMSH_TEST_XLATE to check the rebuilt translator and spirv-cross')
class CompilerOutput(unittest.TestCase):
    def test_execution_mode_and_msl_attribute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jobs = []
            for key, stage, flag in (('early', 'ps', 1), ('late', 'ps', 0), ('missing', 'ps', 0), ('malformed', 'ps', 0), ('vertex', 'vs', 1)):
                data = record(flag, code_size=0xa0)
                struct.pack_into('<I', data, 0x38 + 0x30, 1 | ((5 if stage == 'ps' else 1) << 10))   # Maxwell SPH stage
                struct.pack_into('<Q', data, 0x38 + 0x88, 0xe30000000007000f)   # EXIT after a scheduling word
                job, _ = job_for(key, stage + '_synthetic.bin', data, directory)
                if key == 'missing': job.pop('early_fragment_tests')
                if key == 'malformed': job['early_fragment_tests'] = 'true'
                jobs.append(job)
            request = root / 'jobs.json'; request.write_text(json.dumps(jobs))
            result = subprocess.run([os.environ['DMSH_TEST_XLATE'], str(request), directory], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            for job in jobs:
                with self.subTest(shader=job['key']):
                    path = root / (job['key'] + '.spv')
                    words = struct.unpack('<%dI' % (path.stat().st_size // 4), path.read_bytes())
                    self.assertEqual(words[0], 0x07230203)
                    early = False; offset = 5
                    while offset < len(words):
                        count, opcode = words[offset] >> 16, words[offset] & 0xffff
                        self.assertGreater(count, 0); self.assertLessEqual(offset + count, len(words))
                        if opcode == 16 and count >= 3 and words[offset + 2] == 9: early = True   # OpExecutionMode EarlyFragmentTests
                        offset += count
                    self.assertEqual(early, job['key'] == 'early')
                    result = subprocess.run(['spirv-cross', str(path), '--msl', '--msl-version', '20300'], capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(bool(re.search(r'\[\[\s*early_fragment_tests\s*\]\]', result.stdout)), job['key'] == 'early')


if __name__ == '__main__':
    unittest.main()
