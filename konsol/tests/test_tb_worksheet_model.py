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


def test_unknown_code_is_cumulative_like_statement_not_in_chart():
    assert win(arow("9999", 2024, 12), (2025, 3))
