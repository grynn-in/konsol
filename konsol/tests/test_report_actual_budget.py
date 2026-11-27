"""pnl_actual_budget_monthly: P&L actual vs budget, entity or group (konsol#548 v1 T12).

Pure: the shipped template file compiled with a stub ctx. Variance is
Actual - Budget in the report's own sign, and its caption says so (Deepak Pai,
27 Nov 2026, Q4). A group reads the consolidated / consolidated_budget words;
an entity reads actuals / budget. Every budget read names the scenario id.
"""
import os

from konsol import report_template_model as model

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(APP_DIR, "report_templates")
TID = "pnl_actual_budget_monthly"

PERIODS = [{"number": m, "code": f"P{m:02d}", "label": f"P{m:02d}",
            "start_date": f"2026-{m:02d}-01"} for m in range(1, 13)]
TREE = [
    {"code": "4", "caption": "ZZ Revenue", "depth": 0, "is_group": True, "parent": None},
    {"code": "4000", "caption": "ZZ Sales", "depth": 1, "is_group": False, "parent": "4"},
    {"code": "4100", "caption": "ZZ Other income", "depth": 1, "is_group": False, "parent": "4"},
    {"code": "5000", "caption": "ZZ Costs", "depth": 0, "is_group": False, "parent": None},
]


def _template():
    return model.load_templates(DIR)[TID]


def _compile(kind="group", code="ZZG", sign="debit_positive", through="P03", charts=None):
    t = _template()
    block = next(model.block_key(b, i) for i, b in enumerate(t["rows"]["blocks"])
                 if b["type"] == "account_tree")
    return model.compile_formulas(
        t,
        {"scope": {"kind": kind, "code": code}, "year": 2026, "budget": "ZZB1",
         "through": through, "sign": sign},
        {"periods": PERIODS, "trees": {block: TREE},
         "charts": {block: charts or ["ZZ_CHART"]}},
    )


def _cell(out, ref):
    """The formula or value written at ``ref`` (a row range is split by column)."""
    col, row = model._A1.match(ref).groups()
    want = model._column_number(col)
    for c in out["cells"]:
        a, _, b = c["range"].partition(":")
        b = b or a
        ca, ra = model._A1.match(a).groups()
        cb, rb = model._A1.match(b).groups()
        if int(ra) <= int(row) <= int(rb) and model._column_number(ca) <= want <= model._column_number(cb):
            grid = c.get("formulas") or c.get("values")
            return grid[int(row) - int(ra)][want - model._column_number(ca)]
    raise KeyError(ref)


def test_the_file_validates_with_zero_problems():
    import json
    with open(os.path.join(DIR, f"{TID}.json"), encoding="utf-8") as f:
        raw = json.load(f)
    assert model.validate(raw, TID) == []
    t = _template()
    assert t["order"] == 30
    assert [p["name"] for p in t["params"]] == ["scope", "year", "budget", "through", "sign"]
    scope = t["params"][0]
    assert scope["kinds"] == ["entity", "group"] and scope["label"] == "Entity or group"
    budget = t["params"][2]
    assert budget["scenario_type"] == "budget" and budget["default"] == "rule"
    assert budget["depends_on"] == ["year"]
    assert t["params"][3]["label"] == "Year to date through"
    assert t["params"][4]["choices"] == ["debit_positive", "credit_positive"]
    assert t["series"]["var"] == {"formula": "act - bud", "caption": "Actual − Budget"}


def test_group_reads_consolidated_and_consolidated_budget_with_the_scenario_id():
    out = _compile()
    # Columns: P01..P12 x (act, bud, var) = B..AK, YTD = AL..AN, FY = AO..AQ.
    assert _cell(out, "B5") == '=K.EPM("ZZG", 2026, 1, "4000", , "consolidated")'
    assert _cell(out, "C5") == ('=K.EPM("ZZG", 2026, 1, "4000", , "consolidated_budget", , '
                                '"ZZB1")')
    assert _cell(out, "D5") == "=B5-C5"
    assert _cell(out, "AK6") == "=AI6-AJ6"
    assert _cell(out, "AO5") == '=K.EPM("ZZG", 2026, "FY", "4000", , "consolidated")'
    assert _cell(out, "AP5") == ('=K.EPM("ZZG", 2026, "FY", "4000", , "consolidated_budget", , '
                                 '"ZZB1")')
    assert _cell(out, "AQ5") == "=AO5-AP5"


def test_entity_reads_actuals_and_budget():
    out = _compile(kind="entity", code="ZZE")
    assert _cell(out, "B5") == '=K.EPM("ZZE", 2026, 1, "4000")'
    assert _cell(out, "C5") == '=K.EPM("ZZE", 2026, 1, "4000", , "budget", , "ZZB1")'
    assert _cell(out, "D5") == "=B5-C5"
    assert _cell(out, "C1") == "ZZE"


def test_ytd_sums_the_periods_through_the_through_param():
    out = _compile(through="P03")
    assert _cell(out, "AL5") == "=SUM(B5,E5,H5)"
    assert _cell(out, "AM5") == "=SUM(C5,F5,I5)"
    assert _cell(out, "AN5") == "=AL5-AM5"
    out = _compile(through="P01")
    assert _cell(out, "AL7") == "=SUM(B7)"


def test_headers_label_every_column_and_caption_the_variance():
    out = _compile()
    assert _cell(out, "B2") == "P01" and _cell(out, "D2") == "P01" and _cell(out, "AK2") == "P12"
    assert _cell(out, "AL2") == "YTD P03"
    assert _cell(out, "AO2") == "FY2026"
    assert [_cell(out, r) for r in ("B3", "C3", "D3")] == ["Actual", "Budget", "Actual − Budget"]
    assert _cell(out, "A1") == "P&L actual vs budget"
    assert _cell(out, "B1") == "group:" and _cell(out, "C1") == "ZZG"
    assert _cell(out, "F1") == 2026
    assert _cell(out, "H1") == "Budget: ZZB1"


def test_tree_headings_sum_their_children_and_the_total_sums_leaves():
    out = _compile()
    assert _cell(out, "A4") == "ZZ Revenue"
    assert _cell(out, "B4") == "=SUM(B5:B6)"
    assert _cell(out, "AQ4") == "=SUM(AQ5:AQ6)"
    assert _cell(out, "A8") == "Net Profit and Loss (debit positive; a negative total is a profit)"
    assert _cell(out, "B8") == "=SUM(B5:B7)"
    assert _cell(out, "AQ8") == "=SUM(AQ5:AQ7)"
    assert {"range": "A8:AQ8", "bold": True} in out["formats"]
    assert {"range": "B4:AQ8", "number_format": "#,##0;-#,##0;-"} in out["formats"]


def test_credit_positive_wraps_every_read_and_says_so():
    out = _compile(sign="credit_positive")
    assert _cell(out, "B5") == '=-K.EPM("ZZG", 2026, 1, "4000", , "consolidated")'
    assert _cell(out, "C5").startswith('=-K.EPM("ZZG", 2026, 1, "4000", , "consolidated_budget"')
    assert _cell(out, "D5") == "=B5-C5"
    assert _cell(out, "AL5") == "=SUM(B5,E5,H5)"
    assert "credit positive" in _cell(out, "A8")


def test_several_charts_are_refused_with_the_templates_text():
    try:
        _compile(charts=["ZZ_A", "ZZ_B"])
    except ValueError as exc:
        assert "ZZ_A, ZZ_B" in str(exc) and "actual vs budget" in str(exc), str(exc)
    else:
        raise AssertionError("expected a refusal")


def test_the_unresolved_rule_is_refused_at_compile_time():
    t = _template()
    try:
        model.compile_formulas(t, {"scope": {"kind": "group", "code": "ZZG"}, "year": 2026,
                                   "budget": "rule", "through": "P03"},
                               {"periods": PERIODS, "trees": {"lines": TREE}})
    except ValueError as exc:
        assert "rule" in str(exc)
    else:
        raise AssertionError("expected a refusal")
