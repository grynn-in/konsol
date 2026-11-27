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

def _compute(case):
    return _cap.compute(_compiler, case)


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


# ---- T6: the snapshot goes through the template interpreter ----------------

def _tb_cases():
    return {n: c for n, c in CASES.items() if n.startswith("tb_long__")}


def _tb_template():
    return _compiler.load_templates(_compiler._TEMPLATE_DIR)["trial_balance_long"]


def test_tb_builder_delegates_to_compile_snapshot():
    calls = []
    real = _compiler.compile_snapshot

    def spy(template, params, rows):
        calls.append((template["id"], params))
        return real(template, params, rows)

    _compiler.compile_snapshot = spy
    try:
        _compiler.build_trial_balance_long_map("ZZ1", 2024, [], 3, 5)
    finally:
        _compiler.compile_snapshot = real
    assert calls == [("trial_balance_long",
                      {"entity": "ZZ1", "year": 2024, "period_from": 3, "period_to": 5})]


def test_compile_snapshot_matches_every_tb_golden():
    bad = []
    for name, (fn, args, kwargs) in _tb_cases().items():
        entity, year, rows = args
        got = _compiler.compile_snapshot(
            _tb_template(),
            {"entity": entity, "year": year, **kwargs},
            rows,
        )
        got.pop("report")
        with open(os.path.join(GOLDEN, f"{name}.json"), encoding="utf-8") as f:
            text = f.read()
        if text != _cap.dumps(got):
            bad.append(name)
    assert not bad, bad


def test_compile_snapshot_report_key_and_area():
    t = _tb_template()
    got = _compiler.compile_snapshot(t, {"entity": "ZZ1", "year": 2024}, [{}, {}])
    assert got["report"] == {
        "template_id": "trial_balance_long",
        "template_version": t["version"],
        "params": {"entity": "ZZ1", "year": 2024, "period_from": 1, "period_to": 12},
        "area": "A1:J5",
    }
    empty = _compiler.compile_snapshot(t, {"entity": "ZZ1", "year": 2024}, [])
    assert empty["report"]["area"] == "A1:J3"


def test_tb_constants_come_from_the_template_file():
    t = _tb_template()
    assert _compiler.TB_LONG_TITLE == t["title"]
    assert _compiler.TB_LONG_HEADERS == [f["h"] for f in t["fields"]]
    assert _compiler.TB_LONG_MAX_ROWS == t["max_rows"]
    assert _compiler.TB_LONG_LAST_COL == "J"


def test_list_templates_first_two_entries_are_unchanged():
    """PR #626 finding 3 re-captured the actual-vs-budget entry only."""
    first_two = [
        {"id": "pnl_monthly", "title": "Monthly P&L",
         "description": "12-month income statement: every Published Profit and Loss account of "
                        "the group chart (debit positive; a negative total is a profit)",
         "mode": "formulas"},
        {"id": "trial_balance_long",
         "title": "Entity TB — local currency, account level (pre-consolidation)",
         "description": "One row per account × period, local currency, before FX translation, "
                        "ownership and eliminations — snapshot values for Excel models",
         "mode": "snapshot"},
    ]
    with open(os.path.join(GOLDEN, "list_templates.json"), encoding="utf-8") as f:
        golden = json.load(f)
    assert golden[:2] == first_two
    assert _compiler.list_templates()[:2] == first_two
    third = _compiler.list_templates()[2]
    assert third["id"] == "pnl_actual_budget_monthly" and "()" not in third["description"], third
