"""Shared, content-addressed MSL analysis for the two runtime constant tables.

This does not change translated shaders or the translation chain. Cache entries
are derived data: unreadable, stale or incomplete entries are recomputed.
"""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import re
import sys
import time
import uuid
import zlib

ACCESS = re.compile(r"\b[a-z]+_c1\)\._m0\[([^\]]+)\]")
DEFINITION = re.compile(r"\b(u?int|u?short|u?long|bool)\s+(_\d+)\s*=\s*([^;]+);")
ASSIGNMENT = re.compile(r"(?<![\w.])(_\d+)\s*(?:<<|>>|[-+*/%&|^])?=(?!=)|(?<![\w.])(_\d+)\s*(?:\+\+|--)|(?:\+\+|--)\s*(_\d+)\b")
IDENTIFIER = re.compile(r"\b\w+\b")
READ = re.compile(r"\(\*spvDescriptorSet0\.(\w+)\)\._m0\[([^\]]+)\]")
DECLARATION = re.compile(r"\b(\w+)\s*\[\[id\(")
STORAGE_USE = re.compile(r"spvDescriptorSet0\.(\w+)\b")
DEREFERENCE = re.compile(r"\(\*spvDescriptorSet0\.(\w+)\b")
WRITE = re.compile(r"\s*(?:=(?!=)|[-+*/%&|^]=|<<=|>>=|\+\+|--)")
SCHEMA = 1


@dataclass
class ParsedSource:
    definitions: dict
    c1_accesses: tuple
    identifier_uses: dict
    uniform_reads: dict
    declarations: dict
    storage_unwritten: dict


def parse_source(source):
    """Collect facts once, preserving the existing conservative analyses."""
    writes, declarations, definitions = Counter(), Counter(), {}
    for match in ASSIGNMENT.finditer(source):
        writes[match.group(1) or match.group(2) or match.group(3)] += 1
    for kind, name, text in DEFINITION.findall(source):
        declarations[name] += 1
        definitions.setdefault(name, set()).add((kind, text.strip()))
    definitions = {name: next(iter(values)) for name, values in definitions.items()
                   if len(values) == 1 and writes[name] == declarations[name]}
    reads = {}
    for name, index in READ.findall(source):
        reads.setdefault(name, []).append(index)
    uses = Counter(STORAGE_USE.findall(source))
    dereferences, safe = Counter(), {}
    for match in DEREFERENCE.finditer(source):
        name, j = match.group(1), match.end()
        dereferences[name] += 1
        valid = source[match.start() - 1:match.start()] != "&" and source.startswith(")._m0[", j)
        if valid:
            j += len(")._m0")
            depth = 0
            while j < len(source):
                c = source[j]
                if c == "[": depth += 1
                elif c == "]": depth -= 1
                elif depth == 0 and not (c == "." or c.isalnum() or c == "_"): break
                j += 1
            valid = WRITE.match(source, j) is None
        safe[name] = safe.get(name, True) and valid
    unwritten = {name: safe.get(name, True) and count == dereferences[name] for name, count in uses.items()}
    return ParsedSource(definitions, tuple(sorted(set(ACCESS.findall(source)))),
                        dict(Counter(IDENTIFIER.findall(source))),
                        {name: tuple(values) for name, values in reads.items()},
                        dict(Counter(DECLARATION.findall(source))), unwritten)


@lru_cache(maxsize=None)
def _version(results):
    files = [Path(__file__)]
    if results:
        files += [Path(__file__).with_name(n) for n in ('c1_guard.py', 'cb_extent.py')]
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def _cache_root(source_path):
    asked = os.environ.get('DMSH_ANALYSIS_CACHE')
    if asked == '0': return None
    return Path(asked) if asked else Path(source_path).parent.parent / 'analysis-cache'


def _path(root, kind, version, digest):
    return root / kind / version / digest[:2] / (digest + '.json.z')


def _read(path, digest):
    try:
        data = json.loads(zlib.decompress(path.read_bytes()))
        if isinstance(data, dict) and data.get('schema') == SCHEMA and data.get('digest') == digest:
            return data
    except (OSError, ValueError, TypeError, zlib.error):
        pass
    return None


def _write(path, digest, **payload):
    temp = path.with_name(path.name + '.tmp-' + str(os.getpid()) + '-' + uuid.uuid4().hex)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(dict(schema=SCHEMA, digest=digest, **payload), separators=(',', ':')).encode()
        temp.write_bytes(zlib.compress(content, 1))
        os.replace(temp, path)
    except OSError:
        pass
    finally:
        try: temp.unlink(missing_ok=True)
        except OSError: pass


def _unpack(data):
    """Reject malformed parsed entries instead of trusting partial cache data."""
    p = data['parsed']
    if set(p) != set(ParsedSource.__dataclass_fields__): raise ValueError('incomplete parsed cache')
    if not all(isinstance(p[n], dict) for n in ('definitions', 'identifier_uses', 'uniform_reads', 'declarations', 'storage_unwritten')):
        raise ValueError('invalid parsed dictionaries')
    if not isinstance(p['c1_accesses'], list) or not all(isinstance(x, str) for x in p['c1_accesses']):
        raise ValueError('invalid accesses')
    if not all(isinstance(k, str) and isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v) for k, v in p['definitions'].items()):
        raise ValueError('invalid definitions')
    if not all(isinstance(k, str) and isinstance(v, int) and v >= 0 for n in ('identifier_uses', 'declarations') for k, v in p[n].items()):
        raise ValueError('invalid counts')
    if not all(isinstance(k, str) and isinstance(v, list) and all(isinstance(x, str) for x in v) for k, v in p['uniform_reads'].items()):
        raise ValueError('invalid uniform reads')
    if not all(isinstance(k, str) and isinstance(v, bool) for k, v in p['storage_unwritten'].items()):
        raise ValueError('invalid storage facts')
    return ParsedSource({k: tuple(v) for k, v in p['definitions'].items()}, tuple(p['c1_accesses']),
                        p['identifier_uses'], {k: tuple(v) for k, v in p['uniform_reads'].items()},
                        p['declarations'], p['storage_unwritten'])


def analyze_cached(source_path, reflection_path, namespace, evaluate):
    """Cache parsing by MSL bytes and each table result by MSL + reflection bytes."""
    source = Path(source_path).read_bytes()
    try:
        raw = Path(reflection_path).read_bytes()
        reflection = json.loads(raw)
        if not isinstance(reflection, dict): reflection = None
    except (OSError, ValueError):
        raw, reflection = b'', None
    digest = hashlib.sha256(source).hexdigest()
    result_digest = hashlib.sha256(namespace.encode() + b'\0' + digest.encode() + b'\0' + raw).hexdigest()
    root = _cache_root(source_path)
    if root is not None:
        result_path = _path(root, 'results', _version(True), result_digest)
        cached = _read(result_path, result_digest)
        if cached is not None and 'value' in cached:
            value = cached['value']
            valid = value is None or (isinstance(value, bool) if namespace == 'c1_guard' else
                    isinstance(value, list) and all(isinstance(x, str) for x in value))
            if valid:
                return value
        parsed_path = _path(root, 'parsed', _version(False), digest)
        cached = _read(parsed_path, digest)
    else:
        cached = None
    try:
        parsed = _unpack(cached) if cached is not None else None
    except (KeyError, ValueError, TypeError):
        parsed = None
    if parsed is None:
        parsed = parse_source(source.decode('utf-8'))
        if root is not None:
            _write(parsed_path, digest, parsed=vars(parsed))
    value = evaluate(parsed, reflection)
    if root is not None:
        _write(result_path, result_digest, value=value)
    return value


def parallel_map(worker, paths, label='analysis'):
    """Bounded process parallelism; input order fixes output-table ordering."""
    paths = list(paths)
    if not paths: return []
    requested = os.environ.get('DMSH_ANALYSIS_JOBS') or os.environ.get('J') or str(os.cpu_count() or 1)
    jobs = min(len(paths), max(1, int(requested)))
    started, last = time.monotonic(), time.monotonic()
    print(f'analysis {label}: 0/{len(paths)} files, {jobs} processes', flush=True)
    output = []
    def collect(results):
        nonlocal last
        for value in results:
            output.append(value)
            now = time.monotonic()
            if now - last >= 5:
                print(f'analysis {label}: {len(output)}/{len(paths)} files ({now-started:.1f}s)', flush=True)
                last = now
    if jobs == 1:
        collect(map(worker, paths))
    else:
        with ProcessPoolExecutor(jobs, mp_context=multiprocessing.get_context('spawn')) as pool:
            collect(pool.map(worker, paths, chunksize=16))
    print(f'analysis {label}: {len(paths)}/{len(paths)} files ({time.monotonic()-started:.1f}s)', flush=True)
    return output
