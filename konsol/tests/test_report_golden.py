"""Golden pin of report_compiler outputs (konsol#548 v1, T1).

Green on unchanged code by design: a later refactor must reproduce every
fixture exactly. Regenerate with scripts/capture_report_golden.py.

T5: the template interpreter needs the year's Regular periods. Every
pnl_monthly case is run with a Jan–Dec calendar, and the one key the
interpreter adds (``report``) is left out of the comparison.
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

# OPN, P01..P12 each starting the 1st of its month, CLS. Only the Regular ones
# are report columns, so only they reach the compiler.
CALENDAR = (
    [{"number": 0, "code": "OPN", "label": "Opening", "period_type": "Opening",
      "start_date": "2024-01-01"}]
    + [{"number": m, "code": f"P{m:02d}", "label": f"P{m:02d}", "period_type": "Regular",
        "start_date": f"2024-{m:02d}-01"} for m in range(1, 13)]
    + [{"number": 13, "code": "CLS", "label": "Closing", "period_type": "Closing",
        "start_date": "2024-12-31"}]
)
JAN_DEC = [p for p in CALENDAR if p["period_type"] == "Regular"]


def _compute(case):
    fn, args, kwargs = case
    if fn == "build_cell_map":
        case = (fn, args, dict(kwargs, periods=JAN_DEC))
    got = _cap.compute(_compiler, case)
    if isinstance(got, dict):
        got.pop("report", None)
    return got


def test_fixture_set_equals_case_list():
    on_disk = {f[:-5] for f in os.listdir(GOLDEN) if f.endswith(".json")}
    assert on_disk == set(CASES), (sorted(on_disk - set(CASES)), sorted(set(CASES) - on_disk))


def test_every_case_matches_its_fixture():
    bad = []
    for name, case in CASES.items():
        with open(os.path.join(GOLDEN, f"{name}.json"), encoding="utf-8") as f:
            text = f.read()
        got = _compute(case)
        # Parsed deep equality (lists keep order, so cell order is pinned too),
        # then byte equality of the canonical text.
        if json.loads(text) != json.loads(_cap.dumps(got)) or text != _cap.dumps(got):
            bad.append(name)
    assert not bad, bad
