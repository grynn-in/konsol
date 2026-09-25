"""TB read API: konsol/close/tb_read_api.py `my_tbs` (konsol#305 A25; stories 3.1, 3.7).

GET `my_tbs(fiscal_year, fiscal_period)` lists the caller's in-scope entities
with a status each for the period. Loaded against a stub frappe (pattern:
test_close_signoff_gate.py `_load`/`_call`, copied, not imported). The stub
site applies the filters the module sends (docstatus, "in", period), so a rule
enforced by the query is really exercised, not assumed by the stub.
`signoff_gate.in_scope_entities` (A17, tested there) and
`entity_permissions.allowed_entity_codes` are stubbed; `signoff_model` (A10)
is the real module, loaded by path.
"""
import importlib.util
import json
import os
import sys
import types
from datetime import date, datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PY = os.path.join(APP_DIR, "close", "tb_read_api.py")
SIGNOFF_MODEL_PY = os.path.join(APP_DIR, "close", "signoff_model.py")
VIEW_MODEL_PY = os.path.join(APP_DIR, "close", "tb_view_model.py")
BASIS_MODEL_PY = os.path.join(APP_DIR, "tb_basis_model.py")
CONTROLLER_PY = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_submission",
                             "trial_balance_submission.py")
CONTROLLER = "konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission"

ALL_CLOSE_ROLES = ("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager")
QUARTERS = {1: "Q1", 2: "Q1", 3: "Q1", 4: "Q2", 5: "Q2", 6: "Q2",
            7: "Q3", 8: "Q3", 9: "Q3", 10: "Q4", 11: "Q4", 12: "Q4"}


class _D(dict):
    """frappe._dict: item and attribute access."""

    def __getattr__(self, name):
        return self.get(name)


def _month_end(fy, fp):
    nxt = date(fy + (fp == 12), fp % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _year(fy, status="Open", quarters=QUARTERS, closing=None):
    rows = []
    for fp in range(1, 13):
        rows.append({"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
                     "period_label": "P%02d" % fp, "period_type": "Regular",
                     "start_date": date(fy, fp, 1), "end_date": _month_end(fy, fp),
                     "quarter": quarters.get(fp, ""), "status": status})
    if closing is not None:
        rows.append({"fiscal_year": fy, "fiscal_period": 13, "period_code": "P13",
                     "period_label": "Closing", "period_type": "Closing",
                     "start_date": date(fy, 12, 31), "end_date": date(fy, 12, 31),
                     "quarter": "", "status": closing})
    return rows


def _entity(name, frequency="Monthly"):
    return {"name": name, "entity_name": "Entity " + name, "reporting_frequency": frequency}


def _tb(name, entity, fy=2025, fp=9, docstatus=1, owner="zz-lead@example.com", on_behalf="No",
        basis="Period movement"):
    return {"name": name, "data_area_id": entity, "fiscal_year": fy, "fiscal_period": fp,
            "docstatus": docstatus, "owner": owner, "uploaded_on_behalf": on_behalf,
            "creation": datetime(2025, 10, 3, 9, 30), "amount_basis": basis,
            "tb_file": "/private/files/%s.csv" % name}


def _exc(name, entity, fy=2025, fp=9, docstatus=1):
    return {"name": name, "data_area_id": entity, "fiscal_year": fy, "fiscal_period": fp,
            "docstatus": docstatus, "reason": "Dormant", "declared_by": "zz-lead@example.com"}


class _Site:
    """FY2025, every period Open. In scope: ZZA (Monthly, TB received),
    ZZB (Quarterly; P09 is a quarter-end), ZZC (Monthly, no TB).
    The caller is unrestricted (allowed_entity_codes -> None) and may create TBs."""

    def __init__(self):
        self.rows = _year(2025)
        self.in_scope = ["ZZA", "ZZB", "ZZC"]
        self.allowed = None
        self.can_create = True
        self.records = {
            "Entity": [_entity("ZZA"), _entity("ZZB", "Quarterly"), _entity("ZZC"),
                       _entity("ZZOUT")],
            "Trial Balance Submission": [_tb("TB-A", "ZZA")],
            "TB Exception": [],
        }
        self.only_for = []
        self.whitelisted = {}
        self.get_all_calls = []
        self.files = {}          # file_url -> content (bytes or str), for tb_compare
        self.files_read = []
        self.access_checked = []


def _match(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        if op in ("=", "=="):
            return value == arg
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


def _load(site):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, title=None, **k):
        err = (exc or frappe.ValidationError)(msg)
        err.title = title
        raise err

    def whitelist(*a, **k):
        def deco(fn):
            site.whitelisted[fn.__name__] = k.get("methods")
            return fn
        return deco

    def only_for(roles, *a, **k):
        site.only_for.append(tuple(roles) if isinstance(roles, (list, tuple)) else (roles,))

    def get_all(doctype, filters=None, fields=None, order_by=None, pluck=None, **k):
        site.get_all_calls.append((doctype, dict(filters or {})))
        assert doctype in site.records, "stub: unexpected doctype %s" % doctype
        rows = [r for r in site.records[doctype]
                if all(_match(r.get(f), c) for f, c in (filters or {}).items())]
        if pluck:
            return [r.get(pluck) for r in rows]
        return [_D({f: r.get(f) for f in (fields or ["name"])}) for r in rows]

    def has_permission(doctype, ptype="read", *a, **k):
        assert (doctype, ptype) == ("Trial Balance Submission", "create"), (doctype, ptype)
        return site.can_create

    class _File:
        def __init__(self, url):
            self.url = url

        def get_content(self):
            site.files_read.append(self.url)
            return site.files[self.url]

    def get_doc(doctype, filters=None, *a, **k):
        assert doctype == "File" and set(filters) == {"file_url"}, (doctype, filters)
        assert filters["file_url"] in site.files, "stub: no file %s" % filters["file_url"]
        return _File(filters["file_url"])

    frappe.get_doc = get_doc
    frappe.throw = throw
    frappe.whitelist = whitelist
    frappe.only_for = only_for
    frappe.get_all = get_all
    frappe.has_permission = has_permission
    frappe._ = lambda s: s
    frappe._dict = _D
    frappe.session = types.SimpleNamespace(user="zz-ea@example.com")

    def _by_path(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    konsol = types.ModuleType("konsol")
    close = types.ModuleType("konsol.close")
    signoff_model = _by_path("konsol.close.signoff_model", SIGNOFF_MODEL_PY)
    gate = types.ModuleType("konsol.close.signoff_gate")

    def in_scope_entities(fy, fp):
        assert (fy, fp) == site.asked, (fy, fp)
        return list(site.in_scope)

    gate.in_scope_entities = in_scope_entities
    close.signoff_model, close.signoff_gate = signoff_model, gate
    calendar = types.ModuleType("konsol.fiscal_calendar")
    calendar.fiscal_period_rows = lambda: [dict(r) for r in site.rows]
    perms = types.ModuleType("konsol.entity_permissions")
    perms.allowed_entity_codes = lambda user=None: (
        None if site.allowed is None else set(site.allowed))

    def assert_entity_access(code, user=None):
        site.access_checked.append(code)
        if site.allowed is not None and code not in site.allowed:
            raise frappe.PermissionError("Not permitted to access entity '%s'" % code)

    perms.assert_entity_access = assert_entity_access
    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    period_status.assert_open = period_status.assert_postable = lambda *a, **k: None
    konsol.close, konsol.fiscal_calendar = close, calendar
    konsol.entity_permissions, konsol.period_status = perms, period_status

    # tb_compare parses with the REAL parse_tb_csv (the controller, loaded by
    # path) and compares with the REAL A12 model; only frappe is stubbed.
    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = type("Document", (), {})
    clickhouse = types.ModuleType("konsol.clickhouse")

    def _no_clickhouse(*a, **k):
        raise AssertionError("stub: a TB read must not touch ClickHouse")

    clickhouse.execute = clickhouse.ensure_raw_tables = _no_clickhouse
    mods = {"frappe": frappe, "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": doc_mod, "konsol": konsol, "konsol.close": close,
            "konsol.close.signoff_model": signoff_model,
            "konsol.close.signoff_gate": gate,
            "konsol.fiscal_calendar": calendar, "konsol.entity_permissions": perms,
            "konsol.period_status": period_status, "konsol.clickhouse": clickhouse,
            "konsol.consolidation": types.ModuleType("konsol.consolidation"),
            "konsol.consolidation.doctype": types.ModuleType("konsol.consolidation.doctype"),
            "konsol.consolidation.doctype.trial_balance_submission":
                types.ModuleType("konsol.consolidation.doctype.trial_balance_submission")}
    saved = {n: sys.modules.get(n) for n in list(mods) + ["konsol.tb_basis_model", CONTROLLER,
                                                          "konsol.close.tb_view_model"]}
    sys.modules.update(mods)
    try:
        mods["konsol.tb_basis_model"] = _by_path("konsol.tb_basis_model", BASIS_MODEL_PY)
        sys.modules["konsol.tb_basis_model"] = mods["konsol.tb_basis_model"]
        mods[CONTROLLER] = _by_path(CONTROLLER, CONTROLLER_PY)
        sys.modules[CONTROLLER] = mods[CONTROLLER]
        close.tb_view_model = _by_path("konsol.close.tb_view_model", VIEW_MODEL_PY)
        mods["konsol.close.tb_view_model"] = close.tb_view_model
        sys.modules["konsol.close.tb_view_model"] = close.tb_view_model
        spec = importlib.util.spec_from_file_location("close_tb_read_api_under_test", API_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module, mods


def _my_tbs(site, fy=2025, fp=9):
    """Call my_tbs with the stubs installed; the result must be JSON-safe."""
    site.asked = (fy, fp)
    module, mods = _load(site)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        result = module.my_tbs(fy, fp)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    json.dumps(result)  # dates must already be ISO strings
    return result


def _by_entity(result):
    return {e["entity"]: e for e in result["entities"]}


# --- the gate and the method ---------------------------------------------------

def test_is_a_get_endpoint_gated_on_every_close_role():
    site = _Site()
    _my_tbs(site)
    assert site.whitelisted["my_tbs"] == ["GET"]
    assert site.only_for == [ALL_CLOSE_ROLES]


# --- who sees what ---------------------------------------------------------------

def test_an_admin_sees_every_in_scope_entity_and_nothing_else():
    site = _Site()
    site.records["Trial Balance Submission"].append(_tb("TB-OUT", "ZZOUT"))
    result = _my_tbs(site)
    assert sorted(_by_entity(result)) == ["ZZA", "ZZB", "ZZC"]
    assert "ZZOUT" not in json.dumps(result)


def test_an_entity_accountant_sees_only_their_entities():
    # ZZB is quarterly and FY2025's quarters are undeclared, so a quarter gap
    # exists for ZZB. Neither it nor ZZB's TB nor ZZC may appear anywhere.
    site = _Site()
    site.rows = _year(2025, quarters={})
    site.allowed = {"ZZA", "ZZNOTINSCOPE"}
    site.records["Trial Balance Submission"].append(_tb("TB-B", "ZZB"))
    site.records["TB Exception"].append(_exc("EXC-C", "ZZC"))
    result = _my_tbs(site)
    assert [e["entity"] for e in result["entities"]] == ["ZZA"]
    text = json.dumps(result)
    for other in ("ZZB", "ZZC", "TB-B", "EXC-C", "ZZNOTINSCOPE"):
        assert other not in text, (other, text)
    # and no other entity's records are even read
    reads = [f for d, f in site.get_all_calls
             if d in ("Trial Balance Submission", "TB Exception")]
    assert len(reads) == 2, site.get_all_calls
    for filters in reads:
        assert filters["data_area_id"] == ["in", ["ZZA"]], filters


def test_an_empty_allowed_set_means_no_entities_not_all():
    site = _Site()
    site.allowed = set()
    result = _my_tbs(site)
    assert result["entities"] == []
    assert "ZZ" not in json.dumps(result)


# --- statuses -----------------------------------------------------------------------

def test_statuses_and_missing_entities_sort_first():
    site = _Site()
    site.in_scope = ["ZZA", "ZZB", "ZZC", "ZZD", "ZZE"]
    site.records["Entity"] += [_entity("ZZD"), _entity("ZZE")]
    site.records["TB Exception"].append(_exc("EXC-D", "ZZD"))
    result = _my_tbs(site)
    got = [(e["entity"], e["status"]) for e in result["entities"]]
    assert got == [("ZZB", "Missing"), ("ZZC", "Missing"), ("ZZE", "Missing"),
                   ("ZZA", "Received"), ("ZZD", "Exception declared")], got
    by = _by_entity(result)
    assert by["ZZA"]["name"] == "Entity ZZA"
    assert by["ZZD"]["exception"]["name"] == "EXC-D"
    assert by["ZZD"]["tb"] is None
    assert by["ZZC"]["tb"] is None and by["ZZC"]["exception"] is None


def test_a_quarterly_entity_is_not_expected_before_quarter_end():
    site = _Site()
    by = _by_entity(_my_tbs(site, 2025, 8))
    assert by["ZZB"]["status"] == "Not expected this period"
    # and at the quarter-end it is missing
    assert _by_entity(_my_tbs(site, 2025, 9))["ZZB"]["status"] == "Missing"


def test_a_blank_frequency_is_not_assumed_monthly():
    site = _Site()
    site.records["Entity"] = [_entity("ZZA"), _entity("ZZB", "Quarterly"), _entity("ZZC", "")]
    by = _by_entity(_my_tbs(site))
    assert by["ZZC"]["status"] == "Frequency not declared"


def test_an_undeclared_quarter_is_named_not_guessed():
    site = _Site()
    site.rows = _year(2025, quarters={})
    by = _by_entity(_my_tbs(site))
    assert by["ZZB"]["status"] == "Quarter not declared"
    assert by["ZZC"]["status"] == "Missing"


def test_a_draft_or_other_period_tb_is_not_received():
    site = _Site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A0", "ZZA", docstatus=0), _tb("TB-A2", "ZZA", docstatus=2),
        _tb("TB-A8", "ZZA", fp=8), _tb("TB-A24", "ZZA", fy=2024)]
    site.records["TB Exception"] = [_exc("EXC-A", "ZZA", docstatus=0)]
    by = _by_entity(_my_tbs(site))
    assert by["ZZA"]["status"] == "Missing"
    assert by["ZZA"]["tb"] is None and by["ZZA"]["exception"] is None


# --- the TB and the on-behalf label (R4) --------------------------------------------

def test_the_received_tb_is_json_safe():
    tb = _by_entity(_my_tbs(_Site()))["ZZA"]["tb"]
    assert tb == {"name": "TB-A", "owner": "zz-lead@example.com", "on_behalf": False,
                  "on_behalf_label": "by zz-lead@example.com",
                  "creation": "2025-10-03T09:30:00"}, tb


def test_an_on_behalf_tb_is_labelled():
    site = _Site()
    site.records["Trial Balance Submission"] = [_tb("TB-A", "ZZA", on_behalf="Yes")]
    tb = _by_entity(_my_tbs(site))["ZZA"]["tb"]
    assert tb["on_behalf"] is True
    assert tb["on_behalf_label"] == "by zz-lead@example.com for ZZA"


def test_a_blank_on_behalf_is_unknown_not_no():
    for blank in ("", None):
        site = _Site()
        site.records["Trial Balance Submission"] = [_tb("TB-A", "ZZA", on_behalf=blank)]
        tb = _by_entity(_my_tbs(site))["ZZA"]["tb"]
        assert tb["on_behalf"] is None, blank
        assert tb["on_behalf_label"] == "by zz-lead@example.com (on behalf: not recorded)", tb


# --- the period and can_upload ------------------------------------------------------

def test_can_upload_needs_create_permission_and_an_open_period():
    site = _Site()
    result = _my_tbs(site)
    assert (result["period_open"], result["can_upload"]) == (True, True)
    site.can_create = False
    result = _my_tbs(site)
    assert (result["period_open"], result["can_upload"]) == (True, False)
    site.can_create = True
    site.rows = _year(2025, status="Closed")
    result = _my_tbs(site)
    assert (result["period_open"], result["can_upload"]) == (False, False)


def test_an_undeclared_period_is_refused():
    site = _Site()
    with pytest.raises(Exception) as info:
        _my_tbs(site, 2031, 1)
    assert type(info.value).__name__ == "PeriodNotDeclared"
    assert "EPM Fiscal Year" in str(info.value)


def test_a_non_regular_period_is_refused():
    site = _Site()
    site.rows = _year(2025, closing="Open")
    with pytest.raises(Exception) as info:
        _my_tbs(site, 2025, 13)
    assert "Regular" in str(info.value), str(info.value)


# === tb_compare (konsol#305 A28; story 3.4) ===========================================
#
# GET tb_compare(entity, fiscal_year, fiscal_period): the A12 comparison of the
# entity's submitted TB against the previous declared Regular period's, plus the
# basis and the TB name of both periods. The TB files go through the REAL
# parse_tb_csv and the REAL tb_view_model.compare.

BOM = "﻿"
CUR_CSV = (BOM + "main_account,debit,credit,partner_data_area_id\n"
           "1010,100,0,\n2010,0,130,\n4010,30,0,ZZB\n").encode("utf-8")
PREV_CSV = "main_account,debit,credit\n1010,60,0\n3000,0,60\n"


def _compare_site():
    site = _Site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A9", "ZZA", fp=9), _tb("TB-A8", "ZZA", fp=8),
        _tb("TB-A9-DRAFT", "ZZA", fp=9, docstatus=0), _tb("TB-B8", "ZZB", fp=8)]
    site.files = {"/private/files/TB-A9.csv": CUR_CSV, "/private/files/TB-A8.csv": PREV_CSV,
                  "/private/files/TB-A9-DRAFT.csv": "main_account,debit,credit\n9999,1,1\n",
                  "/private/files/TB-B8.csv": "main_account,debit,credit\n8888,5,5\n"}
    return site


def _tb_compare(site, entity="ZZA", fy=2025, fp=9):
    """Call tb_compare with the stubs installed; the result must be JSON-safe."""
    module, mods = _load(site)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        result = module.tb_compare(entity, fy, fp)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    json.dumps(result)
    return result


def _raises_compare(site, **kwargs):
    try:
        _tb_compare(site, **kwargs)
    except Exception as e:  # noqa: BLE001 - the type is asserted by the caller
        return e
    raise AssertionError("tb_compare did not raise")


def _row_map(result):
    return {(r["account"], r["partner"]): r for r in result["rows"]}


def test_tb_compare_is_a_get_endpoint_gated_on_every_close_role():
    site = _compare_site()
    _tb_compare(site)
    assert site.whitelisted["tb_compare"] == ["GET"]
    assert site.only_for == [ALL_CLOSE_ROLES]


def test_the_comparison_is_joined_by_account_and_partner():
    site = _compare_site()
    result = _tb_compare(site)
    rows = _row_map(result)
    assert sorted(rows) == [("1010", ""), ("2010", ""), ("3000", ""), ("4010", "ZZB")], rows
    assert rows[("1010", "")]["current"] == 100 and rows[("1010", "")]["previous"] == 60
    assert rows[("1010", "")]["change"] == 40
    assert rows[("2010", "")]["previous"] is None and rows[("2010", "")]["change"] is None
    assert rows[("3000", "")]["current"] is None and rows[("3000", "")]["previous"] == -60
    assert rows[("4010", "ZZB")]["is_ic"] is True
    assert result["basis_note"] is None and result["previous_note"] is None
    # the basis and the TB names of both periods
    assert result["entity"] == "ZZA"
    assert result["current"] == {"fiscal_year": 2025, "fiscal_period": 9, "code": "P09",
                                 "tb": "TB-A9", "basis": "Period movement"}, result["current"]
    assert result["previous"] == {"fiscal_year": 2025, "fiscal_period": 8, "code": "P08",
                                  "tb": "TB-A8", "basis": "Period movement"}, result["previous"]
    assert result["previous_code"] == "P08"
    # only the entity's submitted TBs were read; a draft and another entity's TB were not
    assert sorted(site.files_read) == ["/private/files/TB-A8.csv", "/private/files/TB-A9.csv"]
    assert site.access_checked == ["ZZA"]


def test_the_previous_period_crosses_the_year_boundary():
    site = _compare_site()
    # FY2024 has a Closing period (P13) after P12: it is never the "previous".
    site.rows = _year(2024, closing="Open") + _year(2025)
    site.records["Trial Balance Submission"] = [
        _tb("TB-A1", "ZZA", fy=2025, fp=1), _tb("TB-A24-12", "ZZA", fy=2024, fp=12),
        _tb("TB-A24-13", "ZZA", fy=2024, fp=13)]
    site.files = {"/private/files/TB-A1.csv": CUR_CSV,
                  "/private/files/TB-A24-12.csv": PREV_CSV,
                  "/private/files/TB-A24-13.csv": "main_account,debit,credit\n9999,1,1\n"}
    result = _tb_compare(site, fy=2025, fp=1)
    assert result["previous"]["fiscal_year"] == 2024 and result["previous"]["fiscal_period"] == 12
    assert result["previous"]["tb"] == "TB-A24-12"
    assert result["previous_code"] == "FY2024 P12", result["previous_code"]
    assert _row_map(result)[("1010", "")]["change"] == 40
    assert "/private/files/TB-A24-13.csv" not in site.files_read


# --- failure paths ---------------------------------------------------------------------

def test_no_previous_tb_is_a_note_never_a_comparison_against_zero():
    site = _compare_site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A9", "ZZA", fp=9), _tb("TB-A8-DRAFT", "ZZA", fp=8, docstatus=0),
        _tb("TB-A8-CANCELLED", "ZZA", fp=8, docstatus=2)]
    result = _tb_compare(site)
    assert result["previous_note"] == "No trial balance for P08", result
    assert result["previous"] == {"fiscal_year": 2025, "fiscal_period": 8, "code": "P08",
                                  "tb": None, "basis": None}, result["previous"]
    assert result["rows"], result
    for row in result["rows"]:
        assert row["previous"] is None and row["change"] is None, row
    assert site.files_read == ["/private/files/TB-A9.csv"]


def test_the_first_declared_period_has_no_previous_period():
    site = _compare_site()
    site.records["Trial Balance Submission"] = [_tb("TB-A1", "ZZA", fp=1)]
    site.files = {"/private/files/TB-A1.csv": CUR_CSV}
    result = _tb_compare(site, fp=1)
    assert result["previous"] is None and result["previous_code"] is None
    assert result["previous_note"] == "No previous period is declared in the fiscal calendar"
    for row in result["rows"]:
        assert row["change"] is None, row


def test_different_bases_are_shown_but_not_compared():
    site = _compare_site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A9", "ZZA", fp=9, basis="Period-end balance"),
        _tb("TB-A8", "ZZA", fp=8, basis="Period movement")]
    result = _tb_compare(site)
    assert result["basis_note"] == (
        "This period is Period-end balance and P08 is Period movement: "
        "the change is not comparable."), result["basis_note"]
    assert result["current"]["basis"] == "Period-end balance"
    assert result["previous"]["basis"] == "Period movement"
    for row in result["rows"]:
        assert row["change"] is None, row


def test_no_current_tb_is_refused_by_name():
    site = _compare_site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A9-DRAFT", "ZZA", fp=9, docstatus=0), _tb("TB-A8", "ZZA", fp=8)]
    err = _raises_compare(site)
    assert "No submitted trial balance for ZZA P09" in str(err), str(err)
    assert site.files_read == []


def test_an_entity_the_user_cannot_access_is_refused_before_anything_is_read():
    site = _compare_site()
    site.allowed = {"ZZC"}
    err = _raises_compare(site, entity="ZZA")
    assert type(err).__name__ == "PermissionError", type(err)
    assert site.files_read == []
    assert not [c for c in site.get_all_calls if c[0] == "Trial Balance Submission"]
    # and an empty allowed set is not "all"
    site = _compare_site()
    site.allowed = set()
    assert type(_raises_compare(site)).__name__ == "PermissionError"


def test_an_undeclared_basis_is_refused_not_guessed():
    site = _compare_site()
    site.records["Trial Balance Submission"] = [
        _tb("TB-A9", "ZZA", fp=9), _tb("TB-A8", "ZZA", fp=8, basis="")]
    err = _raises_compare(site)
    assert "TB-A8" in str(err) and "amount basis" in str(err), str(err)


def test_tb_compare_refuses_undeclared_and_non_regular_periods():
    site = _compare_site()
    err = _raises_compare(site, fy=2031, fp=1)
    assert type(err).__name__ == "PeriodNotDeclared", type(err)
    site = _compare_site()
    site.rows = _year(2025, closing="Open")
    err = _raises_compare(site, fp=13)
    assert "Regular" in str(err), str(err)
