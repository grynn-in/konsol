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
