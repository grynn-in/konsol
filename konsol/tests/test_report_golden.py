"""Golden pin of report_compiler outputs (konsol#548 v1, T1).

Green on unchanged code by design: a later refactor must reproduce every
fixture exactly. Regenerate with scripts/capture_report_golden.py.
"""
import importlib.util
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(APP_DIR)
GOLDEN = os.path.join(APP_DIR, "tests", "fixtures", "report_golden")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_cap = _load("capture_report_golden_t", os.path.join(ROOT, "scripts", "capture_report_golden.py"))
_compiler = _load("report_compiler_golden_t", os.path.join(APP_DIR, "report_compiler.py"))
CASES = _cap.cases()


def test_fixture_set_equals_case_list():
    on_disk = {f[:-5] for f in os.listdir(GOLDEN) if f.endswith(".json")}
    assert on_disk == set(CASES), (sorted(on_disk - set(CASES)), sorted(set(CASES) - on_disk))


def test_every_case_matches_its_fixture():
    bad = []
    for name, case in CASES.items():
        with open(os.path.join(GOLDEN, f"{name}.json"), encoding="utf-8") as f:
            text = f.read()
        got = _cap.compute(_compiler, case)
        # Parsed deep equality (lists keep order, so cell order is pinned too),
        # then byte equality of the canonical text.
        if json.loads(text) != json.loads(_cap.dumps(got)) or text != _cap.dumps(got):
            bad.append(name)
    assert not bad, bad
