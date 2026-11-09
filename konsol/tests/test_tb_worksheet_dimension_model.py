"""Consolidation worksheet dimension group-by, pure (konsol#494 row 4).
Loaded by path; no frappe."""
import importlib.util
import os
from decimal import Decimal

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PATH = os.path.join(APP_DIR, "close", "tb_worksheet_model.py")
_spec = importlib.util.spec_from_file_location("tb_worksheet_dim_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

ACCOUNTS = {
    "40": {"account_name": "Revenue", "statement_section": "Profit and Loss", "is_group": 1, "lft": 1},
    "4000": {"account_name": "Sales", "statement_section": "Profit and Loss", "is_group": 0,
             "parent_account": "40", "lft": 2},
    "10": {"account_name": "Assets", "statement_section": "Balance Sheet", "is_group": 1, "lft": 3},
    "1000": {"account_name": "Cash", "statement_section": "Balance Sheet", "is_group": 0,
             "parent_account": "10", "lft": 4},
}
PERIODS = [{"fiscal_year": 2025, "fiscal_period": p, "period_type": "Regular"} for p in range(1, 13)]
KEY = (2025, 3)
DECLARED = {}
NO_VALUE = M.NO_DIMENSION_VALUE


def wrow(adjustment_type, area, account, fp, amount, dim=None, null_rows=0):
    row = {"adjustment_type": adjustment_type, "data_area_id": area, "main_account": account,
           "fiscal_year": 2025, "fiscal_period": fp, "amount": amount, "null_rows": null_rows}
    if dim is not None:
        row["dimension_value"] = dim
    return row


# 2-decimal amounts: per-account sums are exact, so the dimensioned lines
# add up to the undimensioned line with no rounding slack.
ROWS = [
    wrow("entity", "A", "4000", 1, -100.25, "North"),
    wrow("entity", "A", "4000", 2, -50.50, "South"),
    wrow("entity", "B", "4000", 3, -10.10, "North"),
    wrow("entity", "B", "4000", 3, -4.40, ""),
    wrow("entity", "A", "1000", 3, 200.75, "North"),
    wrow("entity", "B", "1000", 3, 20.05, "South"),
    wrow("cta", "A", "CTA", 3, -1.25, ""),
    wrow("ic_elimination", "", "4000", 3, 20.10, ""),
    wrow("ic_elimination", "", "1000", 3, -20.10, ""),
    wrow("topside", "A", "1000", 3, 5.05, ""),
]


def sheet(dimension, rows=ROWS, minor_unit=2):
    return M.worksheet(rows, ACCOUNTS, PERIODS, KEY, {"cta_account": "1000"}, minor_unit,
                       None, None, dimension=dimension)


def plain(rows=ROWS, minor_unit=2):
    return M.worksheet([{k: v for k, v in r.items() if k != "dimension_value"} for r in rows],
                       ACCOUNTS, PERIODS, KEY, {"cta_account": "1000"}, minor_unit, None, None)


def _d(x):
    return Decimal(str(x))


def _foot(cells):
    return sum((_d(v) for v in cells.values()), Decimal("0"))


def test_lines_are_keyed_on_account_and_value():
    s = sheet("dim_region")
    values = {(l["account"], l["dimension_value"]) for l in s["lines"]}
    assert ("4000", "North") in values and ("4000", "South") in values
    assert ("4000", M.BLANK_VALUE) in values
    assert NO_VALUE == "(no value)"  # the display label; the key holds M.BLANK_VALUE


def test_per_account_dimension_sum_equals_undimensioned():
    s, p = sheet("dim_region"), plain()
    for pl in p["lines"]:
        parts = [l for l in s["lines"] if l["key"][0] == pl["key"]]
        assert parts, pl
        assert sum((_d(l["consolidated"]) for l in parts), Decimal("0")) == _d(pl["consolidated"])
        for ck in pl["cells"]:
            assert sum((_d(l["cells"][ck]) for l in parts), Decimal("0")) == _d(pl["cells"][ck]), ck
    assert s["total"]["consolidated"] == p["total"]["consolidated"]


def test_eliminations_and_other_layers_sit_on_no_value():
    s = sheet("dim_region")
    elim = ("layer", "ic_elimination")
    for l in s["lines"]:
        if l["dimension_value"] != M.BLANK_VALUE:
            assert l["cells"][elim] == 0, l
    sales_nv = next(l for l in s["lines"] if l["key"] == (("account", "4000"), M.BLANK_VALUE))
    assert sales_nv["cells"][elim] == 20.10
    cash_nv = next(l for l in s["lines"] if l["key"] == (("account", "1000"), M.BLANK_VALUE))
    assert cash_nv["cells"][("layer", "topside")] == 5.05


def test_blank_entity_value_is_no_value_too():
    s = sheet("dim_region")
    sales_nv = next(l for l in s["lines"] if l["key"] == (("account", "4000"), M.BLANK_VALUE))
    assert sales_nv["cells"][("entity", "B")] == -4.40


def test_cross_foot_every_dimensioned_line_and_total():
    s = sheet("dim_region")
    keys = [c["key"] for c in s["columns"]]
    assert len(s["lines"]) > len(plain()["lines"])
    for l in s["lines"]:
        assert set(l["cells"]) == set(keys) - {M.CONSOLIDATED}
        assert _foot(l["cells"]) == _d(l["consolidated"]), l
    assert _foot(s["total"]["cells"]) == _d(s["total"]["consolidated"])
    for k in set(keys) - {M.CONSOLIDATED}:
        assert sum((_d(l["cells"][k]) for l in s["lines"]), Decimal("0")) == _d(s["total"]["cells"][k])


def test_cross_foot_holds_with_rounding_forced():
    rows = [wrow("entity", "A", "4000", 1, -100.0049, "N"),
            wrow("entity", "B", "4000", 1, -50.0049, "N"),
            wrow("ic_elimination", "", "4000", 1, 20.0049, ""),
            wrow("topside", "A", "4000", 1, 0.0049, "")]
    s = sheet("dim_region", rows=rows)
    for l in s["lines"]:
        assert _foot(l["cells"]) == _d(l["consolidated"]), l
    assert any(l["cells"].get(M.ROUNDING) for l in s["lines"])


def test_undimensioned_lines_carry_no_dimension_value():
    for l in plain()["lines"]:
        assert "dimension_value" not in l
        assert not isinstance(l["key"][0], tuple)


def test_dimension_ignores_a_row_without_value_as_no_value():
    s = sheet("dim_region", rows=[wrow("entity", "A", "4000", 1, -5.0)])
    assert [l["dimension_value"] for l in s["lines"]] == [M.BLANK_VALUE]


# -- dimension_column --------------------------------------------------------

DECLARED_ROWS = [
    {"dimension_name": "dim_region", "status": "Published", "in_trial_balance": 1},
    {"dimension_name": "dim_draft", "status": "Draft", "in_trial_balance": 1},
    {"dimension_name": "dim_off", "status": "Published", "in_trial_balance": 0},
    {"dimension_name": "dim_unbuilt", "status": "Published", "in_trial_balance": 1},
]
PRESENT = {"dim_region", "dim_draft", "dim_off"}


def test_dimension_column_accepts_declared_published_built():
    assert M.dimension_column("dim_region", DECLARED_ROWS, PRESENT) == "dim_region"


@pytest.mark.parametrize("name,fragment", [
    ("dim_x; drop table t", "not a legal"),
    ("dim_Region", "not a legal"),
    ("region", "not a legal"),
    ("dim_region\n", "not a legal"),
    ("dim_nope", "not declared"),
    ("dim_draft", "not declared"),
    ("dim_off", "not declared"),
    ("dim_unbuilt", "not built"),
])
def test_dimension_column_refuses_naming_the_failed_check(name, fragment):
    present = PRESENT | {name}
    if name == "dim_unbuilt":
        present = PRESENT
    with pytest.raises(ValueError) as e:
        M.dimension_column(name, DECLARED_ROWS, present)
    assert fragment in str(e.value)
    assert repr(name)[1:-1] in str(e.value) or name.strip() in str(e.value)


def test_dimension_column_none_is_refused():
    with pytest.raises(ValueError):
        M.dimension_column(None, DECLARED_ROWS, PRESENT)


# -- #494 PR review V1: an out-of-scope entity's dimension value never shows --

HIDDEN = "ZZA-ONLY-PROJECT"


def _scoped_rows():
    return [wrow("entity", "A", "4000", 3, -10.0, HIDDEN),
            wrow("entity", "A", "1000", 3, 10.0, HIDDEN),
            wrow("entity", "B", "4000", 3, -4.0, "North"),
            wrow("entity", "B", "1000", 3, 4.0, "")]


def _scoped(dimension="dim_region"):
    return M.worksheet(_scoped_rows(), ACCOUNTS, PERIODS, KEY, {"cta_account": "1000"}, 2,
                       None, {"B"}, dimension=dimension)


def test_a_hidden_entitys_dimension_value_appears_nowhere_in_the_payload():
    import json
    out = M.to_ids(_scoped())
    assert HIDDEN not in json.dumps(out)


def test_hidden_entity_lines_use_the_fixed_outside_scope_marker():
    s = _scoped()
    values = {(l["account"], l["dimension_value"]) for l in s["lines"]}
    assert ("4000", M.OUTSIDE_SCOPE_VALUE) in values
    assert ("1000", M.OUTSIDE_SCOPE_VALUE) in values
    assert M.OUTSIDE_SCOPE_VALUE != HIDDEN


def test_scoping_changes_no_consolidated_amount_per_account():
    scoped, open_ = _scoped(), sheet_all_scopes()

    def per_account(ws):
        out = {}
        for l in ws["lines"]:
            out[l["account"]] = out.get(l["account"], _d(0)) + _d(l["consolidated"])
        return out
    assert per_account(scoped) == per_account(open_)
    for l in scoped["lines"] + [scoped["total"]]:
        assert _foot({k: v for k, v in l["cells"].items()}) == _d(l["consolidated"])


def sheet_all_scopes():
    return M.worksheet(_scoped_rows(), ACCOUNTS, PERIODS, KEY, {"cta_account": "1000"}, 2,
                       None, None, dimension="dim_region")


# -- #494 PR review V4: markers are not in the value space ---------------------

def test_a_real_value_spelled_no_value_stays_apart_from_blank():
    rows = [wrow("entity", "A", "4000", 3, -1.0, "(no value)"),
            wrow("entity", "B", "4000", 3, -2.0, ""),
            wrow("ic_elimination", "", "4000", 3, 3.0, "")]
    s = sheet("dim_region", rows=rows)
    assert len(s["lines"]) == 2
    by_value = {l["dimension_value"]: l for l in s["lines"]}
    assert by_value["(no value)"]["consolidated"] == -1.0
    assert by_value[M.BLANK_VALUE]["consolidated"] == 1.0


def test_a_real_value_spelled_like_the_outside_label_stays_apart_from_the_marker():
    rows = [wrow("entity", "A", "4000", 3, -1.0, "x"),
            wrow("entity", "B", "4000", 3, -2.0, "(outside your scope)")]
    s = M.worksheet(rows, ACCOUNTS, PERIODS, KEY, {"cta_account": "1000"}, 2,
                    None, {"B"}, dimension="dim_region")
    assert len(s["lines"]) == 2
    assert sorted(l["consolidated"] for l in s["lines"]) == [-2.0, -1.0]
    ids = [l["key"] for l in M.to_ids(s)["lines"]]
    assert len(ids) == len(set(ids))


def sheet_ids_rows(rows):
    return M.worksheet(rows, ACCOUNTS, PERIODS, KEY, {"cta_account": "1000"}, 2,
                       None, None, dimension="dim_region")


def test_blank_and_a_real_no_value_get_distinct_line_ids_and_one_label():
    rows = [wrow("entity", "A", "4000", 3, -1.0, "(no value)"),
            wrow("entity", "A", "4000", 3, -2.0, "")]
    out = M.to_ids(sheet_ids_rows(rows))
    assert len({l["key"] for l in out["lines"]}) == 2
    assert [l["dimension_value"] for l in out["lines"]] == ["(no value)", "(no value)"]


def test_the_markers_are_not_strings():
    assert not isinstance(M.BLANK_VALUE, str)
    assert not isinstance(M.OUTSIDE_SCOPE_VALUE, str)
    assert M.BLANK_VALUE != M.OUTSIDE_SCOPE_VALUE


def test_a_real_value_starting_with_a_tilde_never_equals_a_marker_id():
    rows = [wrow("entity", "A", "4000", 3, -1.0, "~blank"),
            wrow("entity", "A", "4000", 3, -2.0, "")]
    ids = [l["key"] for l in M.to_ids(sheet_ids_rows(rows))["lines"]]
    assert len(ids) == len(set(ids)) == 2


# -- #494 PR #500 re-review W1: a hidden entity's value hides on every layer ----

def _layer_rows(area, value):
    return [wrow("entity", "B", "4000", 3, -4.0, "North"),
            wrow("topside", area, "4000", 3, 1.5, value),
            wrow("ic_elimination", area, "4000", 3, 2.5, value),
            wrow("equity_method", area, "4000", 3, 3.5, value),
            wrow("cta", area, "CTA", 3, 4.5, value)]


def _layered(rows, allowed):
    return M.worksheet(rows, ACCOUNTS, PERIODS, KEY, {"cta_account": "1000"}, 2,
                       None, allowed, dimension="dim_region")


def test_a_hidden_entitys_layer_rows_never_show_their_dimension_value():
    import json
    s = _layered(_layer_rows("A", HIDDEN), {"B"})
    assert HIDDEN not in json.dumps(M.to_ids(s))
    values = {l["dimension_value"] for l in s["lines"]}
    assert HIDDEN not in values
    assert M.OUTSIDE_SCOPE_VALUE in values


def test_hidden_entity_layer_rows_change_no_consolidated_or_cross_foot():
    scoped = _layered(_layer_rows("A", HIDDEN), {"B"})
    open_ = _layered(_layer_rows("A", HIDDEN), None)

    def per_account(ws):
        out = {}
        for l in ws["lines"]:
            out[l["account"]] = out.get(l["account"], _d(0)) + _d(l["consolidated"])
        return out
    assert per_account(scoped) == per_account(open_)
    assert _d(scoped["total"]["consolidated"]) == _d(open_["total"]["consolidated"])
    for l in scoped["lines"] + [scoped["total"]]:
        assert _foot(l["cells"]) == _d(l["consolidated"])


def test_a_visible_entitys_layer_row_value_still_shows():
    s = _layered(_layer_rows("B", "Visible-V"), {"B"})
    topside = [l for l in s["lines"] if l["dimension_value"] == "Visible-V"]
    assert topside and all(M.OUTSIDE_SCOPE_VALUE != l["dimension_value"] for l in topside)
    assert M.OUTSIDE_SCOPE_VALUE not in {l["dimension_value"] for l in s["lines"]}


def test_a_layer_row_with_no_entity_area_keeps_its_value():
    rows = [wrow("ic_elimination", "", "4000", 3, 2.5, "Group-V")]
    s = _layered(rows, {"B"})
    assert [l["dimension_value"] for l in s["lines"]] == ["Group-V"]
