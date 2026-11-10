"""Consolidation worksheet model, pure: konsol/close/tb_worksheet_model.py
(konsol#494 row 1 — layer -> column rule). Loaded by path; no frappe."""
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PATH = os.path.join(APP_DIR, "close", "tb_worksheet_model.py")
_spec = importlib.util.spec_from_file_location("tb_worksheet_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

SHOWN = {"A", "B"}


def col(adjustment_type, data_area_id, shown=SHOWN, allowed=None):
    return M.column_of(adjustment_type, data_area_id, shown, allowed)


def test_entity_row_is_its_entity_code():
    assert col("entity", "A") == ("entity", "A")


def test_cta_row_sits_with_its_entity():
    assert col("cta", "B") == ("entity", "B")


def test_ic_elimination_blank_area_is_eliminations():
    assert col("ic_elimination", "") == ("layer", "ic_elimination")


def test_topside_family_with_area_is_one_group_never_an_entity():
    for t in ("topside", "reclassification", "auto_reversal"):
        assert col(t, "A") == ("layer", "topside")


def test_equity_method_with_area_is_its_group():
    assert col("equity_method", "A") == ("layer", "equity_method")


def test_nci_attribution_with_area_is_its_group():
    assert col("nci_attribution", "A") == ("layer", "nci_attribution")


def test_deal_types_with_area_are_one_group():
    for t in ("acquisition", "pnl_proration", "goodwill_amortisation", "disposal"):
        assert col(t, "A") == ("layer", "acquisitions_and_disposals")


def test_unknown_type_is_its_own_column_by_raw_code():
    assert col("zz_new_type", "A") == ("layer", "zz_new_type")


def test_entity_outside_shown_goes_to_other_entities():
    assert col("entity", "C") == ("bucket", "other_entities")
    assert col("cta", "C") == ("bucket", "other_entities")


def test_entity_outside_allowed_goes_to_outside_scope():
    assert col("entity", "B", allowed={"A"}) == ("bucket", "outside_scope")
    assert col("cta", "B", allowed={"A"}) == ("bucket", "outside_scope")


def test_scope_wins_over_shown():
    assert col("entity", "C", shown={"A"}, allowed={"A"}) == ("bucket", "outside_scope")


def test_allowed_none_is_unrestricted():
    assert col("entity", "B", allowed=None) == ("entity", "B")


def test_non_entity_layers_ignore_shown_and_allowed():
    assert col("topside", "Z", shown={"A"}, allowed={"A"}) == ("layer", "topside")
    assert col("ic_elimination", "", shown=set(), allowed=set()) == ("layer", "ic_elimination")


def test_entity_coded_like_a_layer_or_bucket_never_collides():
    # An entity may legitimately be coded "topside", "ic_elimination",
    # "other_entities" or "outside_scope"; its column must differ from the
    # layer/bucket of the same name.
    for code in ("topside", "ic_elimination", "other_entities", "outside_scope"):
        shown = {code}
        entity_col = col("entity", code, shown=shown)
        assert entity_col == ("entity", code)
        assert entity_col != col("topside", "A", shown=shown)
        assert entity_col != col("ic_elimination", "", shown=shown)
        assert entity_col != col("entity", "ZZ", shown=shown)  # other_entities
        assert entity_col != col("entity", "ZZ", shown=shown, allowed={code})  # outside_scope


def test_tagged_keys_for_layers_and_buckets():
    assert col("topside", "A") == ("layer", "topside")
    assert col("zz_new_type", "A") == ("layer", "zz_new_type")
    assert M.OTHER_ENTITIES == ("bucket", "other_entities")
    assert M.OUTSIDE_SCOPE == ("bucket", "outside_scope")


# -- row 2: account placement and window ------------------------------------

ACCOUNTS = {
    "4000": {"account_name": "Sales", "statement_section": "Profit and Loss", "is_group": 0},
    "1000": {"account_name": "Cash", "statement_section": "Balance Sheet", "is_group": 0},
    "3100": {"account_name": "CTA", "statement_section": "Balance Sheet", "is_group": 0},
}

PERIODS = (
    [{"fiscal_year": 2024, "fiscal_period": p, "period_type": "Regular"} for p in range(1, 13)]
    + [{"fiscal_year": 2024, "fiscal_period": 13, "period_type": "Closing"}]
    + [{"fiscal_year": 2025, "fiscal_period": p, "period_type": "Regular"} for p in range(1, 13)]
)


def arow(account, fy, fp, adjustment_type="entity"):
    return {"main_account": account, "fiscal_year": fy, "fiscal_period": fp,
            "adjustment_type": adjustment_type}


def win(row, key):
    return M.in_window(row, ACCOUNTS, PERIODS, key)


def test_row_key_chart_account_is_its_code():
    assert M.row_key(arow("4000", 2025, 3), ACCOUNTS, {}) == ("account", "4000")


def test_cta_row_goes_to_declared_cta_account():
    r = arow("CTA", 2025, 3, "cta")
    assert M.row_key(r, ACCOUNTS, {"cta_account": "3100"}) == ("account", "3100")


def test_cta_row_without_declared_account_is_visible_row():
    r = arow("CTA", 2025, 3, "cta")
    for declared in ({}, {"cta_account": ""}, {"cta_account": None}):
        k = M.row_key(r, ACCOUNTS, declared)
        assert k == M.CTA_UNDECLARED
    assert "no CTA account declared" in M.CTA_UNDECLARED_LABEL


def test_unknown_code_is_own_not_in_chart_row():
    k = M.row_key(arow("9999", 2025, 3), ACCOUNTS, {})
    assert k == ("not_in_chart", "9999")
    assert "not in the published chart" in M.NOT_IN_CHART_LABEL


def test_pl_last_fiscal_year_is_out():
    assert not win(arow("4000", 2024, 12), (2025, 3))


def test_pl_same_year_up_to_key_is_in_later_period_out():
    assert win(arow("4000", 2025, 1), (2025, 3))
    assert win(arow("4000", 2025, 3), (2025, 3))
    assert not win(arow("4000", 2025, 4), (2025, 3))


def test_bs_last_fiscal_year_is_in_and_later_out():
    assert win(arow("1000", 2024, 12), (2025, 3))
    assert win(arow("1000", 2025, 3), (2025, 3))
    assert not win(arow("1000", 2025, 4), (2025, 3))


def test_closing_period_follows_period_keys():
    # Closing 2024/13 is in P&L YTD only when it is the selected period.
    assert not win(arow("4000", 2024, 13), (2024, 12))
    assert win(arow("4000", 2024, 13), (2024, 13))
    assert win(arow("1000", 2024, 13), (2025, 1))


def test_cta_is_cumulative_like_bs():
    r = arow("CTA", 2024, 12, "cta")
    assert win(r, (2025, 3))


def test_cta_row_on_a_pl_section_account_is_still_cumulative():
    # The layer decides, not the account's section (#494 W4, mutation M5).
    r = arow("4000", 2024, 12, "cta")
    assert win(r, (2025, 3))
    assert not win(arow("4000", 2024, 12, "entity"), (2025, 3))


def test_a_group_account_row_is_cumulative_not_pl_ytd():
    # A group account in the P&L section is not a P&L leaf (#494 W4, mutation M8).
    accounts = dict(ACCOUNTS, **{"40": {"account_name": "Revenue",
                                        "statement_section": "Profit and Loss", "is_group": 1}})
    r = arow("40", 2024, 12)
    assert M.in_window(r, accounts, PERIODS, (2025, 3))


def test_unknown_code_is_cumulative_like_statement_not_in_chart():
    assert win(arow("9999", 2024, 12), (2025, 3))


# -- row 3: worksheet + cross-foot ------------------------------------------
from decimal import Decimal  # noqa: E402

SM = M.statement_model

WS_ACCOUNTS = {
    "40": {"account_name": "Revenue", "statement_section": "Profit and Loss", "is_group": 1, "lft": 1},
    "4000": {"account_name": "Sales", "statement_section": "Profit and Loss", "is_group": 0,
             "parent_account": "40", "lft": 2},
    "50": {"account_name": "Expenses", "statement_section": "Profit and Loss", "is_group": 1, "lft": 3},
    "5000": {"account_name": "Costs", "statement_section": "Profit and Loss", "is_group": 0,
             "parent_account": "50", "lft": 4},
    "10": {"account_name": "Assets", "statement_section": "Balance Sheet", "is_group": 1, "lft": 5},
    "1000": {"account_name": "Cash", "statement_section": "Balance Sheet", "is_group": 0,
             "parent_account": "10", "lft": 6},
    "20": {"account_name": "Liabilities", "statement_section": "Balance Sheet", "is_group": 1, "lft": 7},
    "2000": {"account_name": "Payables", "statement_section": "Balance Sheet", "is_group": 0,
             "parent_account": "20", "lft": 8},
    "31": {"account_name": "Reserves", "statement_section": "Balance Sheet", "is_group": 1, "lft": 9},
    "3100": {"account_name": "Translation reserve", "statement_section": "Balance Sheet",
             "is_group": 0, "parent_account": "31", "lft": 10},
}
WS_PERIODS = (
    [{"fiscal_year": 2024, "fiscal_period": p, "period_type": "Regular"} for p in range(1, 13)]
    + [{"fiscal_year": 2025, "fiscal_period": p, "period_type": "Regular"} for p in range(1, 13)]
)
WS_KEY = (2025, 3)
WS_DECLARED = {"cta_account": "3100"}
EA, EB, EC = "E-ALPHA", "E-BETA", "E-GAMMA"


def wrow(adjustment_type, area, account, fy, fp, amount, null_rows=0):
    return {"adjustment_type": adjustment_type, "data_area_id": area, "main_account": account,
            "fiscal_year": fy, "fiscal_period": fp, "amount": amount, "null_rows": null_rows}


# 4-decimal amounts chosen so cells rounded one by one miss the line total
# rounded once (Sales: -100.00 + -50.00 + -10.00 + ... vs -160.01...).
WS_ROWS = [
    wrow("entity", EA, "4000", 2025, 1, -100.0049),
    wrow("entity", EB, "4000", 2025, 2, -50.0049),
    wrow("entity", EC, "4000", 2025, 3, -10.0012),
    wrow("entity", EA, "4000", 2024, 12, -999.0),          # P&L last FY: out
    wrow("entity", EA, "5000", 2025, 3, 60.0035),
    wrow("entity", EB, "5000", 2025, 3, 40.0035),
    wrow("entity", EA, "1000", 2024, 12, 500.1251),         # BS last FY: in
    wrow("entity", EB, "1000", 2025, 3, 200.1251),
    wrow("entity", EC, "1000", 2025, 2, 300.0001),
    wrow("entity", EA, "2000", 2025, 3, -400.0025),
    wrow("entity", EB, "2000", 2025, 3, -200.0025),
    wrow("cta", EA, "CTA", 2025, 3, -3.3333),
    wrow("cta", EB, "CTA", 2024, 6, -1.3333),
    wrow("ic_elimination", "", "4000", 2025, 3, 20.0049),
    wrow("ic_elimination", "", "5000", 2025, 3, -20.0049),
    wrow("topside", EA, "1000", 2025, 3, 5.0051),
    wrow("topside", EA, "2000", 2025, 3, -5.0051),
    wrow("topside", EA, "1000", 2025, 4, 99.0),             # after the key: out
    wrow("acquisition", EB, "1000", 2025, 2, 7.1234),
    wrow("acquisition", EB, "2000", 2025, 2, -7.1234),
    wrow("equity_method", EC, "1000", 2025, 3, 2.2222),
    wrow("equity_method", EC, "4000", 2025, 3, -2.2222),
    wrow("zz_new_type", "", "9999", 2025, 3, 0.4444),
    wrow("nci_attribution", EA, "2000", 2025, 5, 1.0),      # only outside the window
]


def ws(rows=None, minor_unit=2, shown=None, allowed=None, declared=None):
    return M.worksheet(rows if rows is not None else WS_ROWS, WS_ACCOUNTS, WS_PERIODS, WS_KEY,
                       WS_DECLARED if declared is None else declared, minor_unit, shown, allowed)


def _d(x):
    return Decimal(str(x))


def _foot(cells):
    return sum((_d(v) for v in cells.values()), Decimal("0"))


def test_cross_foot_every_line_and_total_on_displayed_values():
    sheet = ws()
    keys = [c["key"] for c in sheet["columns"]]
    assert sheet["lines"]
    for line in sheet["lines"]:
        assert set(line["cells"]) == set(keys) - {M.CONSOLIDATED}
        assert _foot(line["cells"]) == _d(line["consolidated"]), line
    assert _foot(sheet["total"]["cells"]) == _d(sheet["total"]["consolidated"])
    # The total foots down as well as across, on displayed values.
    for key in set(keys) - {M.CONSOLIDATED}:
        assert sum((_d(l["cells"][key]) for l in sheet["lines"]), Decimal("0")) == _d(sheet["total"]["cells"][key])
    assert sum((_d(l["consolidated"]) for l in sheet["lines"]), Decimal("0")) == _d(sheet["total"]["consolidated"])
    # Not vacuous: the fixture forces a rounding difference.
    assert M.ROUNDING in keys
    assert any(line["cells"][M.ROUNDING] != 0 for line in sheet["lines"])


def test_consolidated_is_the_line_total_rounded_once():
    sheet = ws()
    sales = next(l for l in sheet["lines"] if l["key"] == ("account", "4000"))
    # -100.0049 - 50.0049 - 10.0012 + 20.0049 - 2.2222 = -142.2283
    assert sales["consolidated"] == -142.23
    assert sales["cells"][("entity", EA)] == -100.0
    assert sales["cells"][M.ROUNDING] == -0.01


def test_column_order():
    keys = [c["key"] for c in ws()["columns"]]
    assert keys == [
        ("entity", EA), ("entity", EB), ("entity", EC),
        ("layer", "ic_elimination"),
        ("layer", "topside"), ("layer", "equity_method"), ("layer", "acquisitions_and_disposals"),
        ("layer", "zz_new_type"),
        M.ROUNDING, M.CONSOLIDATED,
    ]


def test_adjustment_column_only_when_present_in_window():
    keys = [c["key"] for c in ws()["columns"]]
    assert ("layer", "nci_attribution") not in keys
    rows = WS_ROWS + [wrow("nci_attribution", EA, "2000", 2025, 3, 1.0)]
    keys = [c["key"] for c in ws(rows)["columns"]]
    assert keys.index(("layer", "equity_method")) < keys.index(("layer", "nci_attribution")) \
        < keys.index(("layer", "acquisitions_and_disposals"))


def test_rounding_column_absent_when_nothing_to_round():
    keys = [c["key"] for c in ws(minor_unit=4)["columns"]]
    assert M.ROUNDING not in keys
    assert keys[-1] == M.CONSOLIDATED


def test_lines_in_chart_order_then_not_in_chart_with_names():
    lines = ws()["lines"]
    assert [l["key"] for l in lines] == [
        ("account", "4000"), ("account", "5000"), ("account", "1000"),
        ("account", "2000"), ("account", "3100"), ("not_in_chart", "9999")]
    assert lines[0]["name"] == "Sales"
    assert lines[-1]["account"] == "9999"
    assert lines[-1]["name"] == M.NOT_IN_CHART_LABEL


def test_cta_without_declared_account_is_its_own_line():
    sheet = ws(declared={})
    line = next(l for l in sheet["lines"] if l["key"] == M.CTA_UNDECLARED)
    assert line["name"] == M.CTA_UNDECLARED_LABEL
    assert line["consolidated"] == -4.67
    for l in sheet["lines"]:
        assert _foot(l["cells"]) == _d(l["consolidated"])


def test_consolidated_per_heading_ties_to_statement_model_heading_amounts():
    sheet = ws()
    by_heading = {}
    not_in_chart = {}
    for line in sheet["lines"]:
        if line["key"][0] == "not_in_chart":
            not_in_chart[line["account"]] = _d(line["consolidated"])
            continue
        heading = WS_ACCOUNTS[line["account"]]["parent_account"]
        by_heading[heading] = by_heading.get(heading, Decimal("0")) + _d(line["consolidated"])

    ytd = SM.period_keys(WS_PERIODS, WS_KEY)["ytd"]
    cum = SM._keys_up_to(WS_PERIODS, WS_KEY)
    ha_pl = SM.heading_amounts(WS_ROWS, WS_ACCOUNTS, ytd, 2)
    ha_bs = SM.heading_amounts(WS_ROWS, WS_ACCOUNTS, cum, 2)
    for heading in ("40", "50"):
        assert by_heading[heading] == _d(ha_pl["headings"][heading]), heading
    for heading in ("10", "20"):
        assert by_heading[heading] == _d(ha_bs["headings"][heading]), heading
    # The CTA sits under its declared account: heading + CTA, rounded once
    # (statement_model's _heading_exact convention).
    exact = ha_bs["exact"]
    assert by_heading["31"] == _d(SM._round(exact["headings"].get("31", Decimal("0")) + exact["cta"], 2))
    assert not_in_chart == {k: _d(v) for k, v in ha_bs["not_in_chart"].items()}


def test_narrowing_shown_moves_entities_to_other_without_changing_consolidated():
    full = ws()
    narrow = ws(shown={EA})
    keys = [c["key"] for c in narrow["columns"]]
    assert ("entity", EB) not in keys and ("entity", EC) not in keys
    assert keys[:2] == [("entity", EA), M.OTHER_ENTITIES]
    assert [l["consolidated"] for l in narrow["lines"]] == [l["consolidated"] for l in full["lines"]]
    assert narrow["total"]["consolidated"] == full["total"]["consolidated"]
    cash = next(l for l in narrow["lines"] if l["key"] == ("account", "1000"))
    assert cash["cells"][M.OTHER_ENTITIES] == 500.13  # 200.1251 + 300.0001
    for line in narrow["lines"]:
        assert _foot(line["cells"]) == _d(line["consolidated"])


def test_scoped_caller_never_sees_an_out_of_scope_code():
    full = ws()
    sheet = ws(allowed={EA}, shown={EA, EB})  # EB asked for, but outside scope
    text = repr(sheet)
    assert EB not in text and EC not in text
    keys = [c["key"] for c in sheet["columns"]]
    assert keys[:2] == [("entity", EA), M.OUTSIDE_SCOPE]
    assert sheet["outside_count"] == 2
    assert [l["consolidated"] for l in sheet["lines"]] == [l["consolidated"] for l in full["lines"]]
    for line in sheet["lines"]:
        assert _foot(line["cells"]) == _d(line["consolidated"])


def test_null_amount_refused_naming_account_and_layer():
    for bad in (wrow("topside", EA, "2000", 2025, 3, 1.0, null_rows=2),
                wrow("topside", EA, "2000", 2025, 3, None)):
        try:
            ws(WS_ROWS + [bad])
        except ValueError as e:
            assert "2000" in str(e) and "topside" in str(e)
        else:
            raise AssertionError("NULL amount was read as 0")


# -- row 6b: one key form (string ids) ---------------------------------------

def test_to_ids_turns_every_key_into_a_string_id_and_keeps_the_shape():
    out = M.to_ids(ws())
    assert [c["key"] for c in out["columns"]] == [
        M._id(c["key"]) for c in ws()["columns"]]
    assert out["columns"][0]["key"] == "entity:E-ALPHA"
    assert out["columns"][-1] == {"key": "consolidated", "label": "Consolidated"}
    assert set(out) == set(ws())
    for line in out["lines"]:
        assert isinstance(line["key"], str)
        assert all(isinstance(k, str) for k in line["cells"])
    assert all(isinstance(k, str) for k in out["total"]["cells"])
    assert out["total"]["consolidated"] == ws()["total"]["consolidated"]
    assert out["outside_count"] == ws()["outside_count"]
    assert out["minor_unit"] == ws()["minor_unit"]


def test_to_ids_line_ids_and_cell_values():
    out = M.to_ids(ws())
    ids = [line["key"] for line in out["lines"]]
    assert "account:1000" in ids and "not_in_chart:9999" in ids
    src = {line["account"]: line for line in ws()["lines"]}
    for line in out["lines"]:
        assert line["consolidated"] == src[line["account"]]["consolidated"]
        assert line["cells"]["entity:E-ALPHA"] == src[line["account"]]["cells"][("entity", EA)]


def test_to_ids_cta_undeclared_and_rounding_ids():
    out = M.to_ids(ws(declared={}))
    assert "cta_undeclared" in [line["key"] for line in out["lines"]]
    assert "rounding" in [c["key"] for c in out["columns"]]


def test_to_ids_entity_code_with_a_colon_is_not_split():
    rows = [wrow("entity", "A:1", "1000", 2025, 3, 5.0)]
    out = M.to_ids(ws(rows))
    assert out["columns"][0]["key"] == "entity:A:1"
    assert out["lines"][0]["cells"]["entity:A:1"] == 5.0


def test_to_ids_ids_of_different_kinds_never_collide():
    rows = [wrow("entity", "topside", "1000", 2025, 3, 1.0),
            wrow("topside", EA, "1000", 2025, 3, 2.0)]
    ids = [c["key"] for c in M.to_ids(ws(rows))["columns"]]
    assert len(ids) == len(set(ids))
    assert "entity:topside" in ids and "layer:topside" in ids


def test_to_ids_dimensioned_lines_get_distinct_ids():
    rows = [dict(wrow("entity", EA, "4000", 2025, 3, -1.0), dimension_value="N"),
            dict(wrow("entity", EA, "4000", 2025, 3, -2.0), dimension_value="S")]
    out = M.to_ids(M.worksheet(rows, WS_ACCOUNTS, WS_PERIODS, WS_KEY, WS_DECLARED, 2,
                               None, None, dimension="dim_x"))
    ids = [line["key"] for line in out["lines"]]
    assert len(ids) == len(set(ids)) == 2
    assert [line["dimension_value"] for line in out["lines"]] == ["N", "S"]


# -- #494 row 10b: each column's exact total -----------------------------------

def test_total_rounded_exact_is_the_rounded_column_sum_and_survives_to_ids():
    rows = [wrow("entity", EA, "4000", 2025, 3, 0.004),
            wrow("entity", EA, "1000", 2025, 3, 0.004),
            wrow("entity", EA, "3100", 2025, 3, -0.008),
            wrow("topside", EA, "1000", 2025, 3, 0.0004)]
    sheet = ws(rows)
    exact = sheet["total_rounded_exact"]
    assert "total_exact" not in sheet and "total_exact" not in M.to_ids(sheet)
    assert set(exact) == {c["key"] for c in sheet["columns"]} - {M.ROUNDING}
    assert exact[("entity", EA)] == 0.0                      # exact 0, cells sum to -0.01
    assert sheet["total"]["cells"][("entity", EA)] == -0.01
    assert exact[("layer", "topside")] == 0.0                # 0.0004 rounds to 0.00
    assert exact[M.CONSOLIDATED] == 0.0
    ids = M.to_ids(sheet)
    assert ids["total_rounded_exact"] == {M._id(k): v for k, v in exact.items()}
    assert "entity:" + EA in ids["total_rounded_exact"] and "consolidated" in ids["total_rounded_exact"]


# -- #494 PR review V2: the exact totals come back rounded the model's way -------

def _topside_sheet(amount, minor_unit=2):
    rows = [wrow("topside", "", "1000", 2025, 3, amount)]
    return M.worksheet(rows, ACCOUNTS, PERIODS, (2025, 3), {"cta_account": "3100"},
                       minor_unit, None, None)


def test_total_rounded_exact_uses_the_models_half_even_decimal_rounding():
    for amount, shown in ((0.005, 0.0), (0.015, 0.02), (0.025, 0.02)):
        ws = _topside_sheet(amount)
        assert ws["total"]["cells"][("layer", "topside")] == shown
        assert ws["total_rounded_exact"][("layer", "topside")] == shown
        assert ws["total_rounded_exact"][M.CONSOLIDATED] == shown


def test_total_rounded_exact_has_no_rounding_column_and_survives_to_ids():
    ws = _topside_sheet(0.015)
    assert M.ROUNDING not in ws["total_rounded_exact"]
    ids = M.to_ids(ws)
    assert ids["total_rounded_exact"] == {M._id(k): v for k, v in ws["total_rounded_exact"].items()}
