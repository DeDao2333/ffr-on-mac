"""Constant-segment bounds and per-shader table generation."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import c1_guard


class Bounds(unittest.TestCase):
    def test_shared_definitions_preserve_single_write_rules(self):
        source = """
            uint _1 = 3u;
            uint _2 = 7u; _2 += 1u;
            uint _3 = 9u; ++_3;
            uint _4 = 11u;
            uint _4 = 11u;
            int _5 = 1;
            int _5 = 2;
            bool _6 = false;
        """
        self.assertEqual(c1_guard.definitions_of(source), {
            "_1": ("uint", "3u"), "_4": ("uint", "11u"), "_6": ("bool", "false")})

    def test_interval_proofs(self):
        definitions = {"_1": ("uint", "input"), "_2": ("uint", "_1 & 3u")}
        for index, span, expected in [
            ("15", 256, True), ("16", 256, False), ("-1", 256, False),
            ("0", None, False), ("_2", 256, True), ("_1 + (_2 & 3u)", 256, False),
            ("_1 & 31u", 256, False), ("_1 % 16u", 256, True),
            ("clamp(int(input), 0, 15)", 256, True), ("min(input, 15)", 256, False),
        ]:
            with self.subTest(index=index, span=span):
                self.assertEqual(c1_guard.proven(index, definitions, span), expected)

    def test_span_boundaries_and_unavailable_reflection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shader_b.json"
            self.assertIsNone(c1_guard.c1_span(path))
            path.write_text("{broken", encoding="utf-8")
            self.assertIsNone(c1_guard.c1_span(path))
            for reflection, expected in [({}, 256), ({"c1": None}, 256),
                                         ({"c1": "00"}, 512), ({"c1": "00" * 256}, 512),
                                         ({"c1": "00" * 257}, 768)]:
                with self.subTest(length=len(reflection.get("c1") or "")):
                    path.write_text(json.dumps(reflection), encoding="utf-8")
                    self.assertEqual(c1_guard.c1_span(path), expected)

    def test_cached_result_has_no_filename(self):
        for accesses, reflection, expected in [((), None, None), (("0",), None, True),
                                               (("0",), {}, False), (("16",), {}, True)]:
            with self.subTest(accesses=accesses, reflection=reflection):
                parsed = SimpleNamespace(c1_accesses=accesses, definitions={})
                self.assertIs(c1_guard._needs_full_c1(parsed, reflection), expected)


class Table(unittest.TestCase):
    def test_cache_worker_uses_original_variant_keys(self):
        for name, key in [("shader_b.metal", "shader"), ("shader.metal", "shader"),
                          ("shader_b_vs.metal", "shader_b_vs")]:
            with self.subTest(name=name):
                path = os.path.join("version", name)
                for cached, expected in [(True, key), (False, None), (None, None)]:
                    with mock.patch.object(c1_guard.dmsh_analysis, "analyze_cached", return_value=cached) as analyze:
                        self.assertEqual(c1_guard._analyze_shader(path), expected)
                        analyze.assert_called_once_with(
                            path, os.path.join("version", key + "_b.json"), "c1_guard", c1_guard._needs_full_c1)

    def test_serial_parallel_and_warm_tables_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            version = root / "b-unit"
            version.mkdir()
            (version / "inputs.tsv").write_text("game\tunit\ninputs\tfixture\n", encoding="utf-8")
            sources = {
                "alpha.metal": "(cp_c1)._m0[input];",
                "alpha_b.metal": "(cp_c1)._m0[input];",
                "bare.metal": "(vp_c1)._m0[0];",
                "broken_b.metal": "(fp_c1)._m0[0];",
                "empty_b.metal": "uint _1 = 1u;",
                "safe_b.metal": "(gp_c1)._m0[15];",
                "variant_b_vs.metal": "(vp_c1)._m0[input];",
                "ignored.txt": "(vp_c1)._m0[input];",
            }
            for name, source in sources.items():
                (version / name).write_text(source, encoding="utf-8")
            for key in ("alpha", "safe", "variant_b_vs"):
                (version / (key + "_b.json")).write_text("{}", encoding="utf-8")
            (version / "broken_b.json").write_text("{broken", encoding="utf-8")
            expected = ["alpha", "alpha", "bare", "broken", "variant_b_vs"]
            for run, jobs in enumerate((1, 2, 2)):
                with self.subTest(run=run, jobs=jobs):
                    output = root / f"c1-{run}.tsv"
                    env = dict(os.environ, DMSH_ANALYSIS_JOBS=str(jobs), DMSH_ANALYSIS_CACHE=str(root / "cache"))
                    result = subprocess.run([sys.executable, str(TOOLS / "c1_guard.py"), str(version), str(output)],
                                            env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    rows = output.read_text(encoding="utf-8").splitlines()
                    self.assertRegex(rows[0], r"^# chain b-unit inputs fixture \d{4}-\d{2}-\d{2}T")
                    self.assertEqual(rows[1:], expected)
                    self.assertIn("not proven bounded: 5 shaders", result.stdout)


if __name__ == "__main__":
    unittest.main()
