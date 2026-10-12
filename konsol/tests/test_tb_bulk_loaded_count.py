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
        self.list_calls = []

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


class _Dict(dict):
    """frappe._dict: what get_list returns."""
    __getattr__ = dict.get


class _Doc(types.SimpleNamespace):
    def check_permission(self, ptype):
        assert ptype == "read", ptype


def _module(site, doc, table, uploads=None):
    """tb_bulk.py loaded against the stand-in site. Returns (module, restore)."""
    frappe = types.ModuleType("frappe")
    frappe_utils = types.ModuleType("frappe.utils")
    frappe_utils.cint = lambda v: int(v or 0)
    frappe_utils.strip_html = lambda v: v
    frappe_utils.get_fullname = lambda user: user
    frappe.utils = frappe_utils
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
    frappe.clear_messages = lambda: None
    frappe.log_error = lambda *a, **k: site.errors.append(k.get("title") or a)

    def get_doc(*args, **kwargs):
        if args and args[0] == "Trial Balance Upload":
            return doc
        raise AssertionError(f"run_load created a document: {args or kwargs}")

    def get_all(doctype, filters=None, fields=None, pluck=None, limit_page_length=None, **kw):
        if doctype == "Trial Balance Upload":
            site.list_calls.append({"fields": list(fields or []), **kw})
            return [_Dict({f: u[f] for f in fields}) for u in uploads or []]
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

    def restore():
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    try:
        spec = importlib.util.spec_from_file_location("tb_bulk_281_under_test", TB_BULK_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._read_table = lambda url: table
        mod._release_orphan_claims = lambda report: 0
    except BaseException:
        restore()
        raise
    return mod, restore


def _load(site, report, table):
    doc = _Doc(name=UPLOAD, report=json.dumps(report), upload_file="/private/files/tb.csv",
               amount_basis="Period movement", status="Checked")
    mod, restore = _module(site, doc, table)
    try:
        mod.run_load(UPLOAD)
    finally:
        restore()
    return site.final


def _loaded(final, site):
    """The loaded count as read now (konsol#332-2): from the saved report and
    the submissions the site holds, never from a stored figure."""
    return M.read_state(json.loads(final["report"]), site.submitted, final["status"])["loaded_count"]


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
    assert _loaded(final, site) == 0, f"claimed {_loaded(final, site)} loaded with no submission in the site"
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
    assert _loaded(final, site) == 1, final
    assert final["failed_count"] == 1, final
    assert final["status"] == "Partly Loaded", final
    rows = {r["entity"]: r for r in json.loads(final["report"])}
    assert rows["AMDE"]["loaded"] == "TBS-1"
    assert "loaded" not in rows["AMUS"] and not rows["AMUS"]["ok"]
    assert "TBS-2" in rows["AMUS"]["errors"][0]


def test_an_earlier_problem_row_skipped_with_skip_invalid_is_not_a_failure():
    """konsol#332 (F7): failed = len(claimed) - loaded counts only the names
    the report claims. A row that was a problem before this run (ok False,
    never loaded) or whose load was cancelled before (was_loaded, no claim)
    is skipped, never loaded, and not counted again as failed."""
    site = _Site(submitted=["TBS-1"])
    problem = {**_row("AMUS", None), "ok": False, "errors": ["Unknown account 9999"]}
    problem.pop("loaded")
    cancelled = {**_row("AMHQ", None), "ok": False, "was_loaded": "TBS-3",
                 "errors": ["Loaded earlier as TBS-3, which has since been cancelled. "
                            "Upload the file again if it should be loaded anew."]}
    cancelled.pop("loaded")
    final = _load(site, [_row("AMDE", "TBS-1"), problem, cancelled], _table("AMDE", "AMUS", "AMHQ"))
    assert site.errors == []
    assert (_loaded(final, site), final["failed_count"]) == (1, 0), final
    rows = {r["entity"]: r for r in json.loads(final["report"])}
    assert rows["AMHQ"]["was_loaded"] == "TBS-3" and not rows["AMHQ"]["ok"], rows
    assert "loaded" not in rows["AMUS"] and not rows["AMUS"]["ok"], rows


def test_a_load_whose_submissions_all_exist_is_still_loaded():
    """Control: the fix must not demote a load that is true."""
    site = _Site(submitted=["TBS-1", "TBS-2"])
    final = _load(site, [_row("AMDE", "TBS-1"), _row("AMUS", "TBS-2")], _table("AMDE", "AMUS"))
    assert site.errors == []
    assert (final["status"], _loaded(final, site), final["failed_count"]) == ("Loaded", 2, 0), final


# ── konsol#332-2 (F9): counts derived on read (Deepak Pai 9 Oct, #332
#    issuecomment-6089571467). Live 9 Oct: TBU-00626 stored Loaded / 43 while
#    every one of its 43 submissions is cancelled. ─────────────────────────

DOCTYPE_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_upload")


def _report(n, prefix="TBS-"):
    return [_row(f"E{i:02d}", f"{prefix}{i}") for i in range(1, n + 1)]


def _get_upload(site, **stored):
    doc = _Doc(name=UPLOAD, upload_file="/private/files/tb.csv", amount_basis="Period movement",
               group_count=3, valid_count=3, total_rows=6, failed_count=0, error="", owner="zz@example.com",
               creation="2026-10-09 10:00:00", **stored)
    mod, restore = _module(site, doc, [])
    try:
        return mod.get_upload(UPLOAD)
    finally:
        restore()


def test_get_upload_reads_the_count_from_the_submissions_that_exist():
    """3 loaded, 1 then cancelled: reads 2 and Partly Loaded, not the stored 3."""
    report = _report(3)
    out = _get_upload(_Site(["TBS-1", "TBS-3"]), status="Loaded", loaded_count=3, report=json.dumps(report))
    assert (out["loaded_count"], out["status"]) == (2, "Partly Loaded"), (out["loaded_count"], out["status"])
    rows = {r["entity"]: r for r in out["report"]}
    assert "loaded" not in rows["E02"] and rows["E02"]["was_loaded"] == "TBS-2", rows["E02"]
    out = _get_upload(_Site([]), status="Loaded", loaded_count=3, report=json.dumps(report))
    assert (out["loaded_count"], out["status"]) == (0, "Cancelled"), (out["loaded_count"], out["status"])
    out = _get_upload(_Site(["TBS-1", "TBS-2", "TBS-3"]), status="Loaded", loaded_count=3,
                      report=json.dumps(report))
    assert (out["loaded_count"], out["status"]) == (3, "Loaded"), (out["loaded_count"], out["status"])


def test_recent_uploads_derives_the_count_and_never_reads_the_stored_one():
    """The TBU-00626 shape: stored Loaded / 43, all 43 submissions cancelled."""
    site = _Site(["TBS-B1", "TBS-B2"])
    uploads = [
        {"name": "TBU-00626", "status": "Loaded", "upload_file": "/private/files/p8.csv", "group_count": 43,
         "valid_count": 43, "loaded_count": 43, "failed_count": 0, "owner": "a@example.com",
         "creation": "2026-09-01", "report": json.dumps(_report(43))},
        {"name": "TBU-00900", "status": "Loaded", "upload_file": "/private/files/p9.csv", "group_count": 2,
         "valid_count": 2, "loaded_count": 2, "failed_count": 0, "owner": "a@example.com",
         "creation": "2026-10-01", "report": json.dumps(_report(2, "TBS-B"))},
    ]
    mod, restore = _module(site, None, [], uploads)
    try:
        out = mod.recent_uploads(8)
    finally:
        restore()
    got = {u["name"]: (u["loaded_count"], u["status"]) for u in out}
    assert got == {"TBU-00626": (0, "Cancelled"), "TBU-00900": (2, "Loaded")}, got
    assert all("report" not in u for u in out), "the report stays on the upload"
    assert set(out[0]) == {"name", "status", "upload_file", "group_count", "valid_count", "loaded_count",
                           "failed_count", "owner", "creation"}, sorted(out[0])
    assert [c for c in site.list_calls if "loaded_count" in c["fields"]] == [], site.list_calls


def test_the_load_job_never_stores_a_loaded_count():
    """loaded_count is a virtual field: a fresh site has no column for it."""
    site = _Site(submitted=["TBS-1"])
    final = _load(site, [_row("AMDE", "TBS-1"), _row("AMUS", "TBS-2")], _table("AMDE", "AMUS"))
    assert site.saves, "run_load saved its progress"
    assert [sorted(s) for s in site.saves if "loaded_count" in s] == [], [sorted(s) for s in site.saves]
    assert final["status"] == "Partly Loaded" and _loaded(final, site) == 1, final


def test_the_upload_doctype_derives_loaded_count_in_a_virtual_field():
    meta = json.load(open(os.path.join(DOCTYPE_DIR, "trial_balance_upload.json")))
    field = next(f for f in meta["fields"] if f["fieldname"] == "loaded_count")
    assert field.get("is_virtual") == 1, field

    class Document:
        pass

    calls = []
    stubs = {
        "frappe": types.ModuleType("frappe"), "frappe.model": types.ModuleType("frappe.model"),
        "frappe.model.document": types.ModuleType("frappe.model.document"),
        "konsol": types.ModuleType("konsol"), "konsol.tb_bulk": types.ModuleType("konsol.tb_bulk"),
    }
    stubs["frappe.model.document"].Document = Document
    stubs["konsol.tb_bulk"].upload_state = lambda doc: calls.append(doc) or {"loaded_count": 2, "status": "x"}
    stubs["konsol"].tb_bulk = stubs["konsol.tb_bulk"]
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("tbu_under_test",
                                                      os.path.join(DOCTYPE_DIR, "trial_balance_upload.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        doc = mod.TrialBalanceUpload()
        doc.__dict__["loaded_count"] = 43     # the stale column, as Frappe loads it
        assert doc.loaded_count == 2, doc.loaded_count
        assert calls == [doc]
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


# ── konsol#332-2-D1 (F14; Deepak Pai 12 Oct, #332 issuecomment-6131860387):
#    the upload FORM shows the derived status; the list column keeps the
#    stored job state, labelled "Job status"; "Cancelled" is derived only. ──

UPLOAD_JS = os.path.join(DOCTYPE_DIR, "trial_balance_upload.js")


def _meta():
    with open(os.path.join(DOCTYPE_DIR, "trial_balance_upload.json")) as f:
        return json.load(f)


def _controller(tb_bulk):
    """trial_balance_upload.py loaded against a stub Document carrying
    Frappe's set_onload, with `tb_bulk` as konsol.tb_bulk. Returns (module, restore)."""

    class Document:
        def set_onload(self, key, value):
            self.__dict__.setdefault("__onload", {})[key] = value

        def get_onload(self, key=None):
            onload = self.__dict__.get("__onload", {})
            return onload.get(key) if key else onload

    stubs = {
        "frappe": types.ModuleType("frappe"), "frappe.model": types.ModuleType("frappe.model"),
        "frappe.model.document": types.ModuleType("frappe.model.document"),
        "konsol": types.ModuleType("konsol"), "konsol.tb_bulk": tb_bulk,
    }
    stubs["frappe.model.document"].Document = Document
    stubs["konsol"].tb_bulk = tb_bulk
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)

    def restore():
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    try:
        spec = importlib.util.spec_from_file_location("tbu_f14_under_test",
                                                      os.path.join(DOCTYPE_DIR, "trial_balance_upload.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except BaseException:
        restore()
        raise
    return mod, restore


def _form_onload(site, stored_status, report):
    """The real tb_bulk.upload_state behind the real controller's onload."""
    bulk, restore_bulk = _module(site, None, [])
    try:
        ctl, restore_ctl = _controller(bulk)
        try:
            doc = ctl.TrialBalanceUpload()
            doc.__dict__.update(name="TBU-00626", status=stored_status, report=json.dumps(report))
            doc.onload()
            return doc
        finally:
            restore_ctl()
    finally:
        restore_bulk()


def test_the_form_is_sent_the_derived_status_and_the_job_status_is_left_alone():
    """TBU-00626's shape: stored Loaded, every claimed submission cancelled.
    The form reads Cancelled; the stored job status stays Loaded."""
    doc = _form_onload(_Site([]), "Loaded", _report(3))
    assert doc.get_onload("status_now") == "Cancelled", doc.get_onload()
    assert doc.status == "Loaded", "onload never rewrites the stored job status"
    doc = _form_onload(_Site(["TBS-1", "TBS-3"]), "Loaded", _report(3))
    assert doc.get_onload("status_now") == "Partly Loaded", doc.get_onload()
    doc = _form_onload(_Site(["TBS-1", "TBS-2", "TBS-3"]), "Loaded", _report(3))
    assert doc.get_onload("status_now") == "Loaded", doc.get_onload()
    doc = _form_onload(_Site([]), "Checked", [])
    assert doc.get_onload("status_now") == "Checked", doc.get_onload()


def test_the_form_script_shows_the_derived_status_as_its_indicator():
    """Failure path: Frappe's own form indicator is the stored status, so an
    upload stored Loaded with every submission cancelled reads Loaded."""
    with open(UPLOAD_JS) as f:
        js = f.read()
    assert 'frappe.ui.form.on("Trial Balance Upload"' in js
    assert "refresh(frm)" in js, "the indicator is set on every refresh"
    assert 'frm.doc.__onload' in js and "status_now" in js, "the server's derived status is read"
    assert "frm.page.set_indicator(" in js, "the form's indicator is replaced"
    assert "frm.set_value(\"status\"" not in js, "the stored job status is never written from the form"


def test_the_list_column_is_the_stored_job_status_labelled_so():
    """The list keeps the stored job state, labelled "Job status", so it does
    not claim the submissions still stand. "Cancelled" is never stored: it is
    not a Select option, only read_state's word."""
    field = next(f for f in _meta()["fields"] if f["fieldname"] == "status")
    assert field["label"] == "Job status", field
    assert field.get("in_list_view") == 1 and field.get("read_only") == 1, field
    assert field["options"].split("\n") == ["Draft", "Checked", "Loading", "Loaded", "Partly Loaded", "Failed"], field
    assert "Cancelled" not in field["options"]


def test_load_still_decides_resume_from_the_stored_job_status():
    """A stored Loaded upload whose submissions are all cancelled is not
    resumable: load() reads the stored status, not the derived Cancelled."""
    class _WDoc(_Doc):
        def check_permission(self, ptype):
            assert ptype == "write", ptype

    site = _Site([])
    doc = _WDoc(name=UPLOAD, status="Loaded", report=json.dumps(_report(3)), upload_file="/private/files/tb.csv",
                amount_basis="Period movement", failed_count=0, error="")
    mod, restore = _module(site, doc, [])
    try:
        mod.frappe.has_permission = lambda *a, **k: True
        mod.frappe.throw = _raise
        try:
            mod.load(UPLOAD)
        except _Thrown as e:
            assert str(e) == "This upload is loaded; there is nothing left to load.", str(e)
        else:
            raise AssertionError("load() resumed an upload stored Loaded")
    finally:
        restore()
    assert site.saves == [] and doc.status == "Loaded", (site.saves, doc.status)


class _Thrown(Exception):
    pass


def _raise(msg, *a, **k):
    raise _Thrown(msg)
