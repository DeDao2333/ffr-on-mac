"""Capture import gates use fake app/shader files and the real C serialization header.
No game, Metal compiler, GPU, user cache or profile writes are needed.
"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('import_prewarm_capture', REPO / 'scripts/import_prewarm_capture.py')
im = importlib.util.module_from_spec(spec)
spec.loader.exec_module(im)
TITLE, BUILD = '0100000000001000', 'a' * 32
OLD, NEW = '0123456789ab', 'fedcba987654'
V, F, C = '1' * 16 + '_b', '2' * 16 + '_b', '3' * 16 + '_b'

FIXTURE_C = r'''
#include <stdio.h>
typedef struct { char key[24]; int stage; } NvShader;
#include "nvn_pso_skey.h"
int main(int argc, char **argv) {
    NvShader vs = {.key="1111111111111111_b",.stage=0};
    NvShader fs = {.key="2222222222222222_b",.stage=1};
    pso_key k = {0}; k.cfmt[0]=70; k.samples=1; k.chmask=15;
    k.attr[0].fmt=46; k.stream[0].stride=16;
    pso_skey sk; pso_skey_from(&k,&vs,&fs,0,NULL,&sk);
    if (argc>1 && !strcmp(argv[1],"pointer")) sk.k.vs=(void *)1;
    if (argc>1 && !strcmp(argv[1],"merged")) sk.merged=1;
    if (argc>1 && !strcmp(argv[1],"vertex_stage")) sk.vs_stage=1;
    if (argc>1 && !strcmp(argv[1],"alias")) { sk.k.blend_en|=1u<<5; sk.k.chmask|=15u<<20; }
    char hex[sizeof sk*2+1]; pso_skey_hex(&sk,hex); puts(hex);
}
'''


@unittest.skipUnless(shutil.which('cc'), 'needs a C compiler to inspect the host ABI')
class CaptureImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build_tmp = tempfile.TemporaryDirectory()
        cls.decoder = im.compile_schema(cls.build_tmp.name)
        c = Path(cls.build_tmp.name) / 'fixture.c'
        c.write_text(FIXTURE_C)
        cls.fixture = c.with_suffix('')
        subprocess.run([shutil.which('cc'), '-std=gnu11', '-I', str(REPO / 'dm/gfx/nvn'),
                        '-I', str(REPO / 'dm/gfx/nvn/include'), str(c), '-o', str(cls.fixture)],
                       check=True, capture_output=True)
        cls.rhex = cls.render_hex()

    @classmethod
    def tearDownClass(cls):
        cls.build_tmp.cleanup()

    @classmethod
    def render_hex(cls, option=None):
        return subprocess.run([str(cls.fixture)] + ([option] if option else []), check=True,
                              capture_output=True, text=True).stdout.strip()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.app = self.root / 'Game.app'
        self.dm = self.app / 'Contents/Resources/dm'
        self.dm.mkdir(parents=True)
        mac = self.app / 'Contents/MacOS'
        mac.mkdir()
        (mac / 'dmhost').write_bytes(b'fake source host')
        (self.app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'DMTitleID': TITLE, 'CFBundleShortVersionString': '1.0'}))
        (self.dm / 'main.fixups.tsv').write_text('# BUILD_ID\t' + BUILD + '\n')
        (self.dm / 'main.patched.bin').write_bytes(b'fake main')
        self.old = self.app / 'Contents/Resources/msl' / ('b-' + OLD)
        self.new = self.root / ('b-' + NEW)
        for lib, chain in ((self.old, OLD), (self.new, NEW)):
            lib.mkdir(parents=True)
            (lib / 'inputs.tsv').write_text(f'game\t{TITLE}\ninputs\tabcdef012345\n')
            for key, stage in ((V, 'vs'), (F, 'ps'), (C, 'cs')):
                j = {'chain': chain, 'key': key, 'dm_ctl': key[:-2], 'stage': stage, 'entry': 'main0',
                     'bindings': [{'kind': 'tex', 'msl_type': 'texture2d<float>', 'arg_id': 0}],
                     'vertex_inputs': [], 'raster': {}, 'c1': '', 'drv_reads': {}}
                if stage == 'vs':
                    j.update(entry_z='main0_z', entry_layer='main0_layer', entry_layer_z='main0_layer_z',
                             layer_from={'loc': 0, 'comp': 'x'},
                             vertex_inputs=[{'location': 0, 'msl_type': 'float4'}],
                             pull={'entry': 'main0', 'entry_z': 'main0_z', 'inputs': [0], 'slot': 16})
                    (lib / f'{key}p.metallib').write_bytes(b'MTLB fake pulling library')
                    (lib / f'{key}v.metallib').write_bytes(b'MTLB fake shadow-input library')
                (lib / f'{key}.json').write_text(json.dumps(j))
                (lib / f'{key}.metallib').write_bytes(b'MTLB fake library')
        (self.dm / 'shader_map.tsv').write_text('package\tindex\tstage\tdm_ctl_sha16\tsource\n' + ''.join(
            f'a.bin\t0\t{stage}\t{key[:-2]}\tx\n' for key, stage in ((V, 'vertex'), (F, 'pixel'), (C, 'compute'))))
        self.capture = self.root / 'capture/pso_user.tsv'
        self.capture.parent.mkdir()
        self.rows = [f'S\t{V}\t0', f'S\t{F}\t1', 'R\t' + self.rhex, f'S\t{C}\t5', f'C\t{C}']
        self.log = self.root / 'host.log'
        self.args = argparse.Namespace(source_lib=self.old, target_lib=self.new, capture=self.capture,
                                       capture_log=self.log, title=TITLE, main_build=BUILD,
                                       depth='zero', expected_abi=432, out=self.root / 'audit')
        self.refresh_capture()

    def refresh_capture(self):
        self.capture.write_text('\n'.join(self.rows) + '\n')
        near, entry = ('0', 'main0_z') if self.args.depth == 'zero' else ('-w', 'main0')
        self.log.write_text(f'[dm] [nvn-mtl] depth mode: near={near}, vertex shaders use the {entry} entry\n'
                            f'[dm] shader library stamp: game {TITLE}, inputs abcdef012345\n'
                            f'[dm] metallib directory {self.old}\n'
                            f'[dm] pipeline prewarm list {self.capture}: {len(self.rows)} lines\n')

    def validate(self):
        return im.validate(self.args, self.decoder)

    def mutate_json(self, key, change, lib=None):
        p = (lib or self.new) / f'{key}.json'
        j = json.loads(p.read_text())
        change(j)
        p.write_text(json.dumps(j))

    def test_cross_chain_semantics_and_export_without_input_writes(self):
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        manifest, pairs, audit = self.validate()
        self.assertEqual([x for x in manifest.decode().splitlines() if not x.startswith('#')], self.rows)
        self.assertIn(V[:-2] + '\t' + F[:-2], pairs.decode())
        self.assertEqual(audit['counts']['rows'], {'S': 3, 'R': 1, 'C': 1})
        self.assertEqual(audit['comparison']['shaders_checked'], 3)
        self.assertEqual(audit['source_library']['chain'], OLD)
        self.assertEqual(audit['target_library']['chain'], NEW)
        self.assertEqual(audit['abi']['size'], 432)
        self.assertEqual(audit['counts']['shaders_with_layer_entries'], 1)
        self.assertIn('do not identify shader_get_layer', audit['limitations'][0])
        im.write_candidates(self.args.out, (manifest, pairs, audit), [self.app, self.new, self.capture.parent])
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual((self.args.out / 'pso_manifest.candidate.tsv').read_bytes(), manifest)
        self.assertEqual(json.loads((self.args.out / 'provenance.json').read_text())['source_capture']['sha256'], im.digest(self.capture.read_bytes()))

    def test_typed_layout_entry_and_unknown_semantic_changes_are_rejected(self):
        original = json.loads((self.new / f'{V}.json').read_text())
        changes = {'bindings': [{'kind': 'tex', 'msl_type': 'texture2d<uint>', 'arg_id': 0}],
                   'vertex_inputs': [{'location': 0, 'msl_type': 'uint4'}], 'entry_z': 'other',
                   'entry_layer_z': 'other', 'pull': {'slot': 17}, 'vin': {'inputs': [1]},
                   'raster': {'color_mask': 3}, 'uint_mask_m': 1, 'threadgroup_size': [2, 1, 1],
                   'geometry': {'output': 6}, 'c1': '01', 'future_semantic_field': 1}
        for field, value in changes.items():
            with self.subTest(field=field):
                changed = copy.deepcopy(original)
                changed[field] = value
                (self.new / f'{V}.json').write_text(json.dumps(changed))
                with self.assertRaisesRegex(im.InvalidCapture, 'incompatible shader semantics'):
                    self.validate()
        (self.new / f'{V}.json').write_text(json.dumps(original))

    def test_metadata_differences_are_recorded_but_allowed(self):
        self.mutate_json(V, lambda j: j.update(notes=['compiler note'], source='other location', chain_metal='other compiler'))
        self.assertEqual(self.validate()[2]['comparison']['semantic_mismatches'], 0)

    def test_explicit_layer_rows_round_trip_for_both_depth_modes(self):
        self.rows[2] = 'RL\t' + self.rhex
        for depth in ('zero', 'gl'):
            with self.subTest(depth=depth):
                self.args.depth = depth
                self.refresh_capture()
                manifest, pairs, audit = self.validate()
                self.assertEqual([x for x in manifest.decode().splitlines() if not x.startswith('#')], self.rows)
                self.assertIn(V[:-2] + '\t' + F[:-2], pairs.decode())
                self.assertEqual(audit['counts']['rows'], {'S': 3, 'RL': 1, 'C': 1})
                self.assertEqual(audit['explicit_layer_shader_keys'], [V])
                self.assertNotIn(im.LAYER_LIMIT, audit['limitations'])
                self.assertNotIn(im.LAYER_LIMIT, manifest.decode())
                self.assertEqual(audit['abi']['size'], 432)

    def test_same_render_hex_keeps_normal_and_explicit_layer_identities(self):
        self.rows += ['RL\t' + self.rhex, 'R\t' + self.render_hex('alias'),
                      'RL\t' + self.render_hex('alias')]
        self.refresh_capture()
        manifest, pairs, audit = self.validate()
        output = [x for x in manifest.decode().splitlines() if not x.startswith('#')]
        self.assertEqual(output, self.rows[:5] + ['RL\t' + self.rhex])
        self.assertEqual(audit['counts']['rows'], {'S': 3, 'R': 1, 'C': 1, 'RL': 1})
        self.assertEqual(audit['counts']['duplicates_or_normalized_aliases_removed'], 2)
        self.assertEqual(audit['counts']['observed_pairs'], 1)
        self.assertIn(im.LAYER_LIMIT, audit['limitations'])
        self.assertEqual(len([x for x in pairs.decode().splitlines() if not x.startswith('#')]), 1)

    def test_explicit_layer_requires_metadata_and_selected_entry_in_both_builds(self):
        self.rows[2] = 'RL\t' + self.rhex
        for depth, entry in (('zero', 'entry_layer_z'), ('gl', 'entry_layer')):
            self.args.depth = depth
            self.refresh_capture()
            for lib in (self.old, self.new):
                path = lib / f'{V}.json'
                original = path.read_text()
                for field, value in (('layer_from', None), ('layer_from', []),
                                     (entry, None), (entry, ''), (entry, [])):
                    with self.subTest(depth=depth, lib=lib.name, field=field, value=value):
                        changed = json.loads(original)
                        if value is None:
                            changed.pop(field)
                        else:
                            changed[field] = value
                        path.write_text(json.dumps(changed))
                        with self.assertRaisesRegex(im.InvalidCapture, field):
                            self.validate()
                        path.write_text(original)

    def test_explicit_layer_metadata_must_match_semantically(self):
        self.rows[2] = 'RL\t' + self.rhex
        self.refresh_capture()
        self.mutate_json(V, lambda j: j.update(layer_from={'loc': 1, 'comp': 'x'}))
        with self.assertRaisesRegex(im.InvalidCapture, 'incompatible shader semantics.*layer_from'):
            self.validate()

    def test_explicit_layer_schema_and_vertex_stage_reject(self):
        original = self.rows[:]
        for row, reason in (('RL', 'bad capture row'), ('RL\t' + self.rhex + '\textra', 'bad capture row'),
                            ('RL\tzz', 'bad RL hex'), ('RL\t' + self.rhex[:-2], 'ABI length'),
                            ('RL\t' + self.render_hex('vertex_stage'), 'invalid RL shader stages')):
            with self.subTest(row=row[:32]):
                self.rows = original + [row]
                self.refresh_capture()
                with self.assertRaisesRegex(im.InvalidCapture, reason):
                    self.validate()

    def test_unknown_bad_schema_stage_and_abi_reject(self):
        original = self.rows[:]
        for row, reason in ((f'S\t{"4" * 16}_b\t0', 'unknown shader'), ('X\twhatever', 'bad capture row'),
                            (f'S\t{V}\t1', 'unknown shader'), ('R\tzz', 'bad R hex'),
                            ('R\t' + self.rhex[:-2], 'ABI length')):
            with self.subTest(row=row[:32]):
                self.rows = original + [row]
                self.refresh_capture()
                with self.assertRaisesRegex(im.InvalidCapture, reason):
                    self.validate()
        self.rows = original
        self.refresh_capture()
        self.args.expected_abi = 431
        with self.assertRaisesRegex(im.InvalidCapture, 'ABI mismatch'):
            self.validate()

    def test_unsafe_or_unsupported_serialized_states_reject(self):
        for mode, reason in (('pointer', 'process pointers'), ('merged', 'explicit supported recipe')):
            self.rows[2] = 'R\t' + self.render_hex(mode)
            self.refresh_capture()
            with self.assertRaisesRegex(im.InvalidCapture, reason):
                self.validate()

    def test_normalization_aliases_deduplicate_and_keep_record_order(self):
        self.rows += ['R\t' + self.render_hex('alias'), self.rows[0]]
        self.refresh_capture()
        result = self.validate()
        self.assertEqual(result[2]['counts']['duplicates_or_normalized_aliases_removed'], 2)
        self.assertEqual(result[2]['counts']['rows'], {'S': 3, 'R': 1, 'C': 1})

    def test_missing_or_wrong_inputs_and_chain_reject(self):
        stamp = self.new / 'inputs.tsv'
        original = stamp.read_bytes()
        for data in (None, f'game\t{TITLE}\n', 'game\t0100000000002000\ninputs\tabcdef012345\n'):
            with self.subTest(data=data):
                if data is None:
                    stamp.unlink()
                else:
                    stamp.write_text(data)
                with self.assertRaises(im.InvalidCapture):
                    self.validate()
                stamp.write_bytes(original)
        self.mutate_json(V, lambda j: j.update(chain=OLD))
        with self.assertRaisesRegex(im.InvalidCapture, 'chain provenance mismatch'):
            self.validate()

    def test_missing_library_and_identity_reject(self):
        lib = self.new / f'{V}p.metallib'
        original = lib.read_bytes()
        lib.unlink()
        with self.assertRaisesRegex(im.InvalidCapture, 'missing shader library'):
            self.validate()
        lib.write_bytes(original)
        self.mutate_json(V, lambda j: j.update(dm_ctl='0' * 16))
        with self.assertRaisesRegex(im.InvalidCapture, 'identity mismatch'):
            self.validate()

    def test_source_build_title_and_log_linkage_reject(self):
        self.args.main_build = 'b' * 32
        with self.assertRaisesRegex(im.InvalidCapture, 'BUILD_ID'):
            self.validate()
        self.args.main_build = BUILD
        original = self.log.read_text()
        for text, reason in ((original.replace('near=0', 'near=-w'), 'depth mode'),
                             (original.replace(str(self.old), '/other/b-' + OLD), 'source-lib'),
                             (original.replace('abcdef012345', 'abcdef012346'), 'inputs/chain'),
                             (original.replace(str(self.capture), '/missing/pso_user.tsv'), 'byte-identical')):
            with self.subTest(reason=reason):
                self.log.write_text(text)
                with self.assertRaisesRegex(im.InvalidCapture, reason):
                    self.validate()
        self.log.write_text(original)
        info = self.app / 'Contents/Info.plist'
        info.write_bytes(plistlib.dumps({'DMTitleID': '0100000000002000'}))
        with self.assertRaisesRegex(im.InvalidCapture, 'source app title'):
            self.validate()

    def test_output_cannot_enter_input_or_profile_directories(self):
        result = self.validate()
        for folder in (self.app / 'audit', self.new / 'audit', self.capture.parent / 'audit', REPO / 'dm/games/test-import-audit'):
            with self.subTest(folder=folder):
                with self.assertRaisesRegex(im.InvalidCapture, 'must not modify'):
                    im.write_candidates(folder, result, [self.app, self.new, self.capture.parent])
                self.assertFalse(folder.exists())


if __name__ == '__main__':
    unittest.main()
