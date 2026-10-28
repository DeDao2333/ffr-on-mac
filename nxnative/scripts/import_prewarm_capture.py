#!/usr/bin/env python3
"""Validate a recorded S/R/RL/C prewarm list against two shader builds; export review candidates.

The capture has no intrinsic producer/version header. A log must link a byte-identical
capture to the source app's shader directory, inputs stamp and depth mode; the app's
Info.plist and main.fixups.tsv identify the title/build. This is local evidence, not
an attestation of historical app bytes. No profile, cache or pipeline archive is edited.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO = Path(__file__).resolve().parents[1]
KEY = re.compile(r'[0-9a-f]{16}_b\Z')
CHAIN = re.compile(r'b-([0-9a-f]{12})\Z')
STAGES = {0: 'vs', 1: 'ps', 2: 'gs', 3: 'hs', 4: 'ds', 5: 'cs'}
MAP_STAGES = {'vertex': 0, 'pixel': 1, 'geometry': 2, 'hull': 3, 'domain': 4, 'compute': 5}
RENDER_KINDS = ('R', 'RL')
# These describe provenance/compiler bookkeeping, not runtime reflection semantics.
# Every other field, including unknown/new fields, must compare exactly.
NON_SEMANTIC = {'chain', 'chain_metal', 'source', 'notes'}
LAYER_LIMIT = ('Legacy R rows do not identify shader_get_layer variants. The importer preserves '
               'that limitation: validation does not prove that layered pipelines will be prewarmed.')


class InvalidCapture(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise InvalidCapture(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_evidence(path):
    path = Path(path).resolve()
    h = hashlib.sha256()
    with path.open('rb') as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return {'path': str(path), 'bytes': path.stat().st_size, 'sha256': h.hexdigest()}


# Compile against the actual host header. Python does not duplicate C padding,
# pointer offsets or the va_st / blend_st bit-field layout.
SCHEMA_C = r'''
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
typedef struct { char key[24]; int stage; } NvShader;
#include "nvn_pso_skey.h"
int main(void) {
    unsigned short endian = 1;
    printf("{\"size\":%zu,\"pointer_size\":%zu,\"little_endian\":%s,"
           "\"key_size\":%zu,\"key_offset\":%zu,\"attr_offset\":%zu,\"stream_offset\":%zu}\n",
           sizeof(pso_skey), sizeof(void *), *(unsigned char *)&endian ? "true" : "false",
           sizeof(pso_key), offsetof(pso_skey,k), offsetof(pso_key,attr), offsetof(pso_key,stream));
    char line[8192];
    while (fgets(line,sizeof line,stdin)) {
        line[strcspn(line,"\r\n")] = 0;
        pso_skey sk;
        if (pso_skey_parse(line,&sk)) { puts("{\"error\":\"invalid R hex or ABI length\"}"); continue; }
        if (strnlen(sk.vs,sizeof sk.vs)==sizeof sk.vs || strnlen(sk.fs,sizeof sk.fs)==sizeof sk.fs) {
            puts("{\"error\":\"unterminated shader name\"}"); continue;
        }
        char normalized[sizeof sk * 2 + 1]; pso_skey_hex(&sk,normalized);
        printf("{\"vs\":\"%s\",\"fs\":\"%s\",\"vs_stage\":%u,\"fs_stage\":%u,"
               "\"merged\":%u,\"fz\":%u,\"pointers_zero\":%s,\"samples\":%u,\"layout_hex\":\"",
               sk.vs,sk.fs,sk.vs_stage,sk.fs_stage,sk.merged,sk.fz,
               (!sk.k.vs && !sk.k.fs && !sk.k.fsfn) ? "true" : "false",sk.k.samples);
        unsigned char *a=(unsigned char *)sk.k.attr, *s=(unsigned char *)sk.k.stream;
        for (size_t i=0;i<sizeof sk.k.attr;i++) printf("%02x",a[i]);
        for (size_t i=0;i<sizeof sk.k.stream;i++) printf("%02x",s[i]);
        printf("\",\"normalized\":\"%s\"}\n",normalized);
    }
    return ferror(stdin) ? 1 : 0;
}
'''


def compile_schema(folder):
    cc = shutil.which('cc')
    require(cc, 'a C compiler is required to read the current pso_skey ABI')
    folder = Path(folder)
    src, exe = folder / 'schema.c', folder / 'schema'
    src.write_text(SCHEMA_C)
    r = subprocess.run([cc, '-std=gnu11', '-I', str(REPO / 'dm/gfx/nvn'),
                        '-I', str(REPO / 'dm/gfx/nvn/include'), str(src), '-o', str(exe)],
                       capture_output=True, text=True)
    require(r.returncode == 0, f'cannot read pso_skey ABI: {r.stderr.strip()}')
    return exe


def decode_rows(exe, hex_rows, expected_abi):
    r = subprocess.run([str(exe)], input=''.join(x + '\n' for x in hex_rows), capture_output=True, text=True)
    require(r.returncode == 0, 'pso_skey decoder failed')
    try:
        schema, *rows = [json.loads(x) for x in r.stdout.splitlines()]
    except (ValueError, TypeError) as e:
        raise InvalidCapture(f'invalid pso_skey decoder output: {e}') from e
    require(schema['size'] == expected_abi, f'ABI mismatch: current header {schema["size"]}, expected {expected_abi}')
    require(schema['pointer_size'] == 8 and schema['little_endian'], 'only the captured 64-bit little-endian ABI is supported')
    require(len(rows) == len(hex_rows), 'incomplete pso_skey decoder output')
    for row in rows:
        require('error' not in row, row.get('error', 'invalid R row'))
    return schema, iter(rows)


def read_stamp(lib, title):
    m = CHAIN.fullmatch(lib.name)
    require(m, f'library folder must be b-<12 hex chain>: {lib}')
    p = lib / 'inputs.tsv'
    require(p.is_file(), f'missing inputs provenance: {p}')
    stamp = {}
    for line in p.read_text().splitlines():
        if not line or line.startswith('#'):
            continue
        c = line.split('\t')
        require(len(c) == 2 and c[0] not in stamp, f'invalid inputs stamp: {p}')
        stamp[c[0]] = c[1]
    require(stamp.get('game') == title, f'inputs game differs from {title}: {p}')
    require(re.fullmatch(r'[0-9a-f]{12}', stamp.get('inputs', '')), f'missing/invalid inputs digest: {p}')
    return {'chain': m[1], 'game': title, 'inputs': stamp['inputs'], 'stamp': file_evidence(p)}


def source_app(lib, title, build):
    require(lib.parent.name == 'msl' and lib.parent.parent.name == 'Resources',
            'source library must be inside the original app Contents/Resources/msl')
    contents = lib.parent.parent.parent
    require(contents.name == 'Contents' and contents.parent.suffix == '.app', 'cannot locate source app')
    info_path, fixups = contents / 'Info.plist', contents / 'Resources/dm/main.fixups.tsv'
    info = plistlib.loads(info_path.read_bytes())
    require(info.get('DMTitleID') == title, 'source app title differs from --title')
    ids = re.findall(r'^# BUILD_ID\t([0-9a-f]+)$', fixups.read_text(), re.M)
    require(ids == [build], 'source app main BUILD_ID differs from --main-build')
    files = [info_path, fixups, contents / 'Resources/dm/main.patched.bin', contents / 'MacOS/dmhost']
    return contents.parent, {'path': str(contents.parent), 'title': title, 'main_build': build,
                            'display_version': info.get('CFBundleShortVersionString'),
                            'evidence': [file_evidence(p) for p in files]}


def shader_map(path):
    keys = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith('#') or line.startswith('package\t'):
            continue
        c = line.split('\t')
        require(len(c) >= 4 and c[2] in MAP_STAGES and re.fullmatch(r'[0-9a-f]{16}', c[3]),
                f'invalid shader map row: {path}')
        key, stage = c[3] + '_b', MAP_STAGES[c[2]]
        require(key not in keys or keys[key] == stage, f'ambiguous shader stage: {key}')
        keys[key] = stage
    require(keys, f'empty shader map: {path}')
    return keys


def log_provenance(log, capture_bytes, lib, stamp, depth, data_lines):
    lines = log.read_text().splitlines()
    wanted = ('depth mode: near=0' if depth == 'zero' else 'depth mode: near=-w')
    opposite = ('depth mode: near=-w' if depth == 'zero' else 'depth mode: near=0')
    require(any(wanted in s for s in lines) and not any(opposite in s for s in lines),
            'capture log depth mode missing, mixed or incompatible')
    dirs = [m[1] for s in lines if (m := re.search(r'metallib directory (.+)$', s))]
    require(any(Path(p).resolve() == lib for p in dirs), 'capture log does not name --source-lib')
    marker = f'shader library stamp: game {stamp["game"]}, inputs {stamp["inputs"]}'
    require(any(marker in s for s in lines), 'capture log inputs/chain provenance missing or incompatible')
    matches = []
    for n, line in enumerate(lines, 1):
        m = re.search(r'pipeline prewarm list (.+): (\d+) lines$', line)
        if not m or int(m[2]) != data_lines:
            continue
        p = Path(m[1])
        if p.is_file() and p.read_bytes() == capture_bytes:
            matches.append({'line': n, 'text': line, 'capture': file_evidence(p)})
    require(matches, 'capture log has no byte-identical recorded prewarm list with the same row count')
    return {'log': file_evidence(log), 'matching_lists': matches,
            'producer_binding': 'Log-linked current app evidence; capture has no intrinsic producer header.'}


def checked_shader(key, stage, old, new, source_stamp, target_stamp, layer_depth=None):
    require(KEY.fullmatch(key), f'invalid shader key: {key!r}')
    js, evidence = [], []
    for lib, stamp in ((old, source_stamp), (new, target_stamp)):
        p = lib / f'{key}.json'
        require(p.is_file(), f'unknown/missing shader reflection: {p}')
        j = json.loads(p.read_bytes())
        require(isinstance(j, dict), f'invalid shader reflection: {p}')
        require(j.get('chain') == stamp['chain'], f'reflection chain provenance mismatch: {p}')
        require(j.get('key') == key and j.get('dm_ctl') == key[:-2], f'reflection shader identity mismatch: {p}')
        require(j.get('stage') == STAGES[stage], f'reflection stage mismatch: {p}')
        if layer_depth is not None:
            require(stage == 0, f'RL row requires a vertex shader: {key}')
            require(isinstance(j.get('layer_from'), dict), f'RL vertex shader has no layer_from metadata: {p}')
            layer_entry = 'entry_layer_z' if layer_depth == 'zero' else 'entry_layer'
            require(isinstance(j.get(layer_entry), str) and j[layer_entry],
                    f'RL vertex shader has no {layer_entry} entry: {p}')
        if stage == 2 and isinstance(j.get('gs_slice'), dict):
            libraries = []  # the runtime folds this stub into vertex layer entry points
        else:
            require(isinstance(j.get('entry'), str) and j['entry'], f'missing shader entry: {p}')
            libraries = [lib / f'{key}.metallib']
            if isinstance(j.get('pull'), dict):
                libraries.append(lib / f'{key}p.metallib')
            if isinstance(j.get('vin'), dict):
                libraries.append(lib / f'{key}v.metallib')
        for binary in libraries:
            require(binary.is_file(), f'missing shader library: {binary}')
            with binary.open('rb') as f:
                require(f.read(4) == b'MTLB', f'invalid shader library header: {binary}')
        js.append(j)
        evidence.append({'reflection': file_evidence(p), 'libraries': [file_evidence(p) for p in libraries]})
    semantic = [{k: v for k, v in j.items() if k not in NON_SEMANTIC} for j in js]
    diffs = sorted(k for k in semantic[0].keys() | semantic[1].keys() if semantic[0].get(k) != semantic[1].get(k)
                   or (k in semantic[0]) != (k in semantic[1]))
    require(not diffs, f'incompatible shader semantics (typed bindings/layout/entries): {key}: {", ".join(diffs)}')
    return {'key': key, 'stage': stage, 'semantic_sha256': digest(json.dumps(semantic[0], sort_keys=True, separators=(',', ':')).encode()),
            'semantic_fields': sorted(semantic[0]), 'source': evidence[0], 'target': evidence[1]}, js[1]


def validate(a, decoder):
    require(re.fullmatch(r'[0-9A-F]{16}', a.title), '--title must be 16 uppercase hexadecimal digits')
    require(re.fullmatch(r'[0-9a-f]{32}', a.main_build), '--main-build must be 32 lowercase hexadecimal digits')
    old, new, capture, log = (Path(x).resolve() for x in (a.source_lib, a.target_lib, a.capture, a.capture_log))
    source_stamp, target_stamp = read_stamp(old, a.title), read_stamp(new, a.title)
    app, app_info = source_app(old, a.title, a.main_build)
    map_path = app / 'Contents/Resources/dm/shader_map.tsv'
    known = shader_map(map_path)
    raw = capture.read_bytes()
    rows = [x for x in raw.decode('utf-8').splitlines() if x and not x.startswith('#')]
    require(rows, 'empty capture')
    for n, row in enumerate(rows, 1):
        c = row.split('\t')
        require((c[0] == 'S' and len(c) == 3 and c[2] in {str(x) for x in STAGES})
                or (c[0] in (*RENDER_KINDS, 'C') and len(c) == 2), f'bad capture row {n}: expected S/R/RL/C schema')
        if c[0] in RENDER_KINDS:
            require(re.fullmatch(r'[0-9a-fA-F]+', c[1]), f'bad {c[0]} hex at row {n}')
    linkage = log_provenance(log, raw, old, source_stamp, a.depth, len(rows))
    schema, decoded = decode_rows(decoder, [x.split('\t')[1] for x in rows if x.split('\t', 1)[0] in RENDER_KINDS], a.expected_abi)
    used, normalized, pairs, layouts = {}, [], [], set()
    explicit_layer_keys = set()
    no_fs = 0

    def use(key, stage):
        require(KEY.fullmatch(key), f'invalid shader key: {key!r}')
        require(known.get(key) == stage, f'unknown shader or stage not in source map: {key} stage {stage}')
        require(key not in used or used[key] == stage, f'inconsistent shader stage: {key}')
        used[key] = stage

    for row in rows:
        c = row.split('\t')
        if c[0] in ('S', 'C'):
            use(c[1], int(c[2]) if c[0] == 'S' else 5)
        else:
            sk = next(decoded)
            require(sk['pointers_zero'], f'{c[0]} row contains serialized process pointers')
            require(not sk['merged'] and not sk['fz'], f'merged/specialized {c[0]} rows need an explicit supported recipe')
            require(sk['vs_stage'] == 0 and (not sk['fs'] or sk['fs_stage'] == 1), f'invalid {c[0]} shader stages')
            require(sk['samples'] in (0, 1, 2, 4, 8, 16), f'invalid {c[0]} sample count')
            use(sk['vs'], 0)
            if c[0] == 'RL':
                explicit_layer_keys.add(sk['vs'])
            if sk['fs']:
                use(sk['fs'], 1)
                pairs.append((sk['vs'][:-2], sk['fs'][:-2]))
            else:
                no_fs += 1
            layouts.add(sk['layout_hex'])
            row = c[0] + '\t' + sk['normalized']
        normalized.append(row)
    records, layer_keys, typed = [], [], Counter()
    for key, stage in sorted(used.items()):
        record, j = checked_shader(key, stage, old, new, source_stamp, target_stamp,
                                   layer_depth=a.depth if key in explicit_layer_keys else None)
        if stage == 0 and a.depth == 'zero':
            require(j.get('entry_z'), f'vertex shader has no near=0 entry: {key}')
        records.append(record)
        if j.get('entry_layer'):
            layer_keys.append(key)
        typed.update(x for x in ('entry_u', 'entry_m') if j.get(x))
    output_rows = list(dict.fromkeys(normalized))
    output_pairs = list(dict.fromkeys(pairs))
    legacy_layer_limit = [LAYER_LIMIT] if any(x.startswith('R\t') for x in output_rows) else []
    header = (f'# Validated capture candidate; review before profile import.\n# title {a.title} main {a.main_build} '
              f'depth {a.depth} ABI {schema["size"]}\n# source chain {source_stamp["chain"]} target chain {target_stamp["chain"]} '
              f'capture SHA256 {digest(raw)}\n' + ''.join(f'# {x}\n' for x in legacy_layer_limit))
    manifest = (header + ''.join(x + '\n' for x in output_rows)).encode()
    pair_bytes = ('# Observed VS/PS pairs only; not merged into static or runtime pair tables.\n'
                  + ''.join(v + '\t' + f + '\n' for v, f in output_pairs)).encode()
    audit = {'schema_version': 1, 'status': 'validated_review_candidate', 'title': a.title, 'main_build': a.main_build,
             'depth': a.depth, 'abi': schema, 'source_capture': file_evidence(capture), 'source_app': app_info,
             'source_library': source_stamp, 'target_library': target_stamp, 'source_shader_map': file_evidence(map_path),
             'capture_provenance': linkage, 'schema_headers': [file_evidence(REPO / 'dm/gfx/nvn' / f)
                                                             for f in ('nvn_pso_skey.h', 'nvn_obj.h')],
             'comparison': {'ignored_metadata_fields': sorted(NON_SEMANTIC), 'shaders_checked': len(records),
                            'semantic_mismatches': 0, 'shader_records': records},
             'counts': {'capture_rows': len(rows), 'candidate_rows': len(output_rows), 'rows': dict(Counter(x.split('\t', 1)[0] for x in output_rows)),
                        'duplicates_or_normalized_aliases_removed': len(rows) - len(output_rows), 'observed_pairs': len(output_pairs),
                        'vertex_layouts': len(layouts), 'render_rows_without_fragment': no_fs,
                        'shaders_with_layer_entries': len(layer_keys), 'typed_entries': dict(typed)},
             'limitations': legacy_layer_limit + ['Observed pairs describe pulling bases, not exact pipelines with vertex layouts.'],
             'layer_shader_keys': layer_keys,
             'explicit_layer_shader_keys': sorted(explicit_layer_keys),
             'outputs': {'pso_manifest.candidate.tsv': {'sha256': digest(manifest), 'bytes': len(manifest)},
                         'pso_observed_pairs.candidate.tsv': {'sha256': digest(pair_bytes), 'bytes': len(pair_bytes)}}}
    return manifest, pair_bytes, audit


def write_candidates(out, result, protected):
    out = Path(out).resolve()
    for path in [REPO / 'dm/games', *protected]:
        path = Path(path).resolve()
        require(out != path and path not in out.parents, f'output must not modify a profile or input directory: {out}')
    out.mkdir(parents=True, exist_ok=True)
    manifest, pairs, audit = result
    payloads = {'pso_manifest.candidate.tsv': manifest, 'pso_observed_pairs.candidate.tsv': pairs,
                'provenance.json': (json.dumps(audit, indent=2, sort_keys=True) + '\n').encode()}
    for name, data in payloads.items():
        tmp = out / (name + '.partial')
        tmp.write_bytes(data)
        tmp.replace(out / name)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--source-lib', required=True, type=Path)
    ap.add_argument('--target-lib', required=True, type=Path)
    ap.add_argument('--capture', required=True, type=Path)
    ap.add_argument('--capture-log', required=True, type=Path, help='log naming source library/stamp/depth and a byte-identical capture')
    ap.add_argument('--title', required=True)
    ap.add_argument('--main-build', required=True)
    ap.add_argument('--depth', required=True, choices=('zero', 'gl'))
    ap.add_argument('--expected-abi', required=True, type=int)
    ap.add_argument('--out', required=True, type=Path)
    a = ap.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix='prewarm-schema-') as folder:
            result = validate(a, compile_schema(folder))
        write_candidates(a.out, result, [a.source_lib.parent.parent.parent.parent, a.target_lib, a.capture.parent])
    except (InvalidCapture, OSError, ValueError, KeyError) as e:
        print(f'[RED] prewarm capture rejected: {e}', file=sys.stderr)
        return 1
    print(f'prewarm capture validated: {result[2]["counts"]}; candidates: {a.out.resolve()}')
    if LAYER_LIMIT in result[2]['limitations']:
        print(LAYER_LIMIT)
    return 0


if __name__ == '__main__':
    sys.exit(main())
