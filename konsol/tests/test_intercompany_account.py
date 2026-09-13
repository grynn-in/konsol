"""Intercompany Account: the intercompany flag on the group chart (konsol#159,
decision 3 of 13 Sep 2026). The pure pairing rules run against the module
loaded by path with stubbed frappe/konsol imports; the rest is contract."""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "intercompany_account")


def _load():
    saved = {k: sys.modules.get(k) for k in
             ("frappe", "konsol", "konsol.governed_reference", "konsol.schema_lifecycle")}
    frappe = types.ModuleType("frappe")
    gov = types.ModuleType("konsol.governed_reference")
    gov.GovernedReferenceDocument = type("GovernedReferenceDocument", (), {})
    sl = types.ModuleType("konsol.schema_lifecycle")
    sl.check_epm_admin = lambda: None
    sys.modules.update({"frappe": frappe, "konsol": types.ModuleType("konsol"),
                        "konsol.governed_reference": gov, "konsol.schema_lifecycle": sl})
    try:
        spec = importlib.util.spec_from_file_location("ica_under_test", os.path.join(DT_DIR, "intercompany_account.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


M = _load()


def test_a_pair_is_unordered_and_a_blank_counterpart_is_the_account_itself():
    assert M.pair_of("1300", "2300") == ("1300", "2300") == M.pair_of("2300", "1300")
    assert M.pair_of("1300", "") == ("1300",) == M.pair_of(" 1300 ", "1300")


def test_each_account_belongs_to_one_pair():
    others = [("ICA-4030", "4030", "5030"), ("ICA-1900", "1900", "")]
    # the same pair from the other side is not a conflict
    assert M.pair_conflicts("5030", "4030", others) == []
    # 5030 on its own, or with another account, would be matched twice
    assert M.pair_conflicts("5030", "", others) == ["ICA-4030"]
    assert M.pair_conflicts("5030", "6000", others) == ["ICA-4030"]
    # 1900 is a same-account pair: pairing it with anything else conflicts
    assert M.pair_conflicts("2900", "1900", others) == ["ICA-1900"]
    assert M.pair_conflicts("7000", "7100", others) == []


def test_doctype_contract():
    with open(os.path.join(DT_DIR, "intercompany_account.json")) as f:
        meta = json.load(f)
    assert meta["name"] == "Intercompany Account" and meta["module"] == "Consolidation"
    fields = {f["fieldname"]: f for f in meta["fields"]}
    assert fields["main_account"].get("unique") == 1 and fields["main_account"].get("reqd") == 1
    assert not fields["counterpart_account"].get("reqd")
    assert fields["status"]["options"] == "Draft\nPublished\nInactive"
    assert meta["autoname"] == "format:ICA-{main_account}"
    assert set(meta["field_order"]) == set(fields)


def test_published_rows_write_through_like_every_governed_reference():
    with open(os.path.join(DT_DIR, "intercompany_account.py")) as f:
        src = f.read()
    assert "class IntercompanyAccount(GovernedReferenceDocument)" in src
    assert 'CH_TABLE = "epm_staging.intercompany_accounts"' in src
    assert 'CH_SYNC_FILTERS = {"status": _PUBLISHED}' in src
    assert set(M.IntercompanyAccount.CH_FIELD_MAP) == {"main_account", "counterpart_account", "description", "status"}
    # the governed base owns publish/unpublish/after_delete
    for copied in ("def publish(", "def unpublish(", "def after_delete("):
        assert copied not in src


def test_the_staging_table_ddl_matches_the_field_map():
    """The columns synced must be the columns created, in the bootstrap DDL
    konsolidat's init-db.sql mirrors."""
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        src = f.read()
    start = src.index('"epm_staging.intercompany_accounts": (')
    body = src[start:src.index("),", start)]
    for col in M.IntercompanyAccount.CH_FIELD_MAP:
        assert f"{col} String" in body, col


def test_upload_and_consolidation_group_read_the_same_flag():
    for rel in ("tb_bulk.py", os.path.join("consolidation", "doctype", "consolidation_group", "consolidation_group.py"),
                os.path.join("consolidation", "doctype", "trial_balance_submission", "trial_balance_submission.py")):
        with open(os.path.join(APP_DIR, rel)) as f:
            assert "intercompany_accounts" in f.read(), rel
