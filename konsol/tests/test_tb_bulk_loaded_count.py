"""konsol#281: a bulk upload's loaded count is the submissions that exist.

`run_load` used to count `loaded` from the upload's stored report JSON: the
names a previous run wrote there. When those submissions had since gone (a
site reloaded with its uploads reset to Checked but their reports kept), it
found nothing ready, created nothing, and reported every row as loaded:

    TBU-00004: status=Loaded loaded=621 failed=0
    Trial Balance Submissions in the site: 0

These tests run the real `run_load` from konsol/tb_bulk.py with frappe
stubbed, and check what it saves against the submissions the site holds.
"""
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TB_BULK_PATH = os.path.join(APP_DIR, "tb_bulk.py")
_spec = importlib.util.spec_from_file_location("tb_bulk_model_281", os.path.join(APP_DIR, "tb_bulk_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

HEADER = ["data_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit", "currency"]
UPLOAD = "TBU-00004"


class _Site:
    """The stand-in site: which Trial Balance Submissions are submitted, and
    every value run_load saved onto the upload."""

    def __init__(self, submitted):
        self.submitted = set(submitted)
        self.saves = []
        self.errors = []

    def _match(self, filters):
        filters = dict(filters or {})
        names = filters.get("name")
        if isinstance(names, (list, tuple)) and names and names[0] == "in":
            names = set(names[1])
        elif isinstance(names, str):
            names = {names}
        else:
            raise AssertionError(f"unexpected Trial Balance Submission filter: {filters}")
        assert filters.get("docstatus") == 1, "a loaded row means a SUBMITTED submission"
        return sorted(names & self.submitted)

    @property
    def final(self):
        out = {}
        for values in self.saves:
            out.update(values)
        return out


def _load(site, report, table):
    frappe = types.ModuleType("frappe")
    frappe_utils = types.ModuleType("frappe.utils")
    frappe_utils.cint = lambda v: int(v or 0)
    frappe_utils.strip_html = lambda v: v
    frappe.utils = frappe_utils
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
    frappe.clear_messages = lambda: None
    frappe.log_error = lambda *a, **k: site.errors.append(k.get("title") or a)

    doc = types.SimpleNamespace(name=UPLOAD, report=json.dumps(report), upload_file="/private/files/tb.csv",
                                amount_basis="Period movement", status="Checked")

    def get_doc(*args, **kwargs):
        if args and args[0] == "Trial Balance Upload":
            return doc
        raise AssertionError(f"run_load created a document: {args or kwargs}")

    def get_all(doctype, filters=None, fields=None, pluck=None, limit_page_length=None, **kw):
        assert doctype == "Trial Balance Submission", doctype
        names = site._match(filters)
        return names if pluck else [types.SimpleNamespace(name=n) for n in names]

    db = types.SimpleNamespace(
        set_value=lambda doctype, name, values, update_modified=True: site.saves.append(dict(values)),
        commit=lambda: None, rollback=lambda: None, is_deadlocked=lambda e: False,
        count=lambda doctype, filters=None: len(site._match(filters)),
        exists=lambda doctype, filters=None: bool(site._match(
            filters if isinstance(filters, dict) else {"name": filters, "docstatus": 1})),
        get_value=lambda doctype, filters=None, fieldname="name", **kw: (site._match(filters) or [None])[0],
        get_all=get_all,
    )
    frappe.get_doc, frappe.get_all, frappe.get_list, frappe.db = get_doc, get_all, get_all, db

    def stub(name, **attrs):
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        return mod

    konsol_pkg = stub("konsol", tb_bulk_model=M)
    stubs = {
        "frappe": frappe, "frappe.utils": frappe_utils, "konsol": konsol_pkg, "konsol.tb_bulk_model": M,
        "konsol.period_status": stub("konsol.period_status"),
        "konsol.clickhouse": stub("konsol.clickhouse", execute=lambda *a, **k: ""),
        "konsol.consolidation.doctype.intercompany_account.intercompany_account":
            stub("ica", intercompany_accounts=lambda: []),
        "konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission":
            stub("tbs", CONTROL_TABLE="tb_control", _sql_str=lambda s: s, partnerless_ic_accounts=lambda r, i: [],
                 partnerless_warning=lambda n: "", validate_tb_rows=lambda rows, **kw: []),
        "konsol.entity_permissions": stub("ep", allowed_entity_codes=lambda user=None: None),
        "konsol.group_chart": stub("gc", chart_accounts=lambda: {}),
        "konsol.tb_dimension": stub("td", declared_dimensions=lambda: []),
        "rq": stub("rq"),
        "rq.timeouts": stub("rq.timeouts", BaseTimeoutException=type("BaseTimeoutException", (Exception,), {})),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("tb_bulk_281_under_test", TB_BULK_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._read_table = lambda url: table
        mod._release_orphan_claims = lambda report: 0
        mod.run_load(UPLOAD)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return site.final


def _row(entity, loaded):
    return {"entity": entity, "fiscal_year": 2025, "fiscal_period": 12, "ok": True, "errors": [],
            "rows": 2, "loaded": loaded, "amount_basis": "Period movement"}


def _table(*entities):
    out = [HEADER]
    for e in entities:
        out += [[e, "2025", "12", "1010", "5", "0", "EUR"], [e, "2025", "12", "2010", "0", "5", "EUR"]]
    return out


def test_a_report_naming_submissions_that_do_not_exist_is_not_a_load():
    """The issue as observed: every row names a submission, none exists."""
    site = _Site(submitted=[])
    report = [_row("AMDE", "TBS-1"), _row("AMUS", "TBS-2"), _row("AMHQ", "TBS-3")]
    final = _load(site, report, _table("AMDE", "AMUS", "AMHQ"))
    assert site.errors == []
    assert final["loaded_count"] == 0, f"claimed {final['loaded_count']} loaded with no submission in the site"
    assert final["status"] != "Loaded", final
    # the vanished rows are reported, not silently dropped
    assert final["failed_count"] == 3, final
    rows = json.loads(final["report"])
    assert not any(r.get("loaded") for r in rows), rows
    assert all(not r["ok"] and any("TBS-" in e for e in r["errors"]) for r in rows), rows


def test_only_the_submissions_that_exist_count_as_loaded():
    site = _Site(submitted=["TBS-1"])
    report = [_row("AMDE", "TBS-1"), _row("AMUS", "TBS-2")]
    final = _load(site, report, _table("AMDE", "AMUS"))
    assert site.errors == []
    assert final["loaded_count"] == 1, final
    assert final["failed_count"] == 1, final
    assert final["status"] == "Partly Loaded", final
    rows = {r["entity"]: r for r in json.loads(final["report"])}
    assert rows["AMDE"]["loaded"] == "TBS-1"
    assert "loaded" not in rows["AMUS"] and not rows["AMUS"]["ok"]
    assert "TBS-2" in rows["AMUS"]["errors"][0]


def test_a_load_whose_submissions_all_exist_is_still_loaded():
    """Control: the fix must not demote a load that is true."""
    site = _Site(submitted=["TBS-1", "TBS-2"])
    final = _load(site, [_row("AMDE", "TBS-1"), _row("AMUS", "TBS-2")], _table("AMDE", "AMUS"))
    assert site.errors == []
    assert (final["status"], final["loaded_count"], final["failed_count"]) == ("Loaded", 2, 0), final
