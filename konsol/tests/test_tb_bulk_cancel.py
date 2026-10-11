"""konsol#332, option #332-1 (Deepak Pai, 11 Oct 2026): cancelling a Trial
Balance Submission that a bulk upload loaded updates that upload's report row
and its loaded / failed counts.

The row keeps a marker, ``was_loaded``, naming the cancelled submission.
``merge_loaded`` treats it as the earlier name, so resuming the upload does
not load the cancelled trial balance again without warning. Before this, a
row that ``keep_submitted`` marked lost its ``loaded`` key and the next
re-check rebuilt it from the fresh check as ready (the issue's follow-up):

    merge_loaded([fresh], keep_submitted([row(loaded='TBS-2')], set()), 'TBU-00004')
    -> ok: True, errors: []
"""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TB_BULK_PATH = os.path.join(APP_DIR, "tb_bulk.py")
TBS_CONTROLLER = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_submission",
                              "trial_balance_submission.py")
UPLOAD = "TBU-00004"


def _by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _by_path("tb_bulk_model_332", os.path.join(APP_DIR, "tb_bulk_model.py"))
#: the konsol#281 harness: the real run_load against a stub site
H = _by_path("tb_bulk_loaded_count_332", os.path.join(APP_DIR, "tests", "test_tb_bulk_loaded_count.py"))


def row(entity="AMDE", **kw):
    out = {"entity": entity, "fiscal_year": 2025, "fiscal_period": 12, "ok": True, "errors": [],
           "rows": 2, "existing": None}
    out.update(kw)
    return out


def fresh(entity="AMDE", existing=None, existing_file=None):
    """What a re-check reports for an entity-period with nothing wrong in the file."""
    r = row(entity, existing=existing, existing_file=existing_file)
    if existing:
        r.update(ok=False, errors=[f"{existing} is already submitted for this entity and period; "
                                   "cancel or amend it first"])
    return r


# -- the resume path (pure) --------------------------------------------------------

def test_the_issues_repro_a_row_keep_submitted_dropped_is_not_reloaded_on_resume():
    out = M.merge_loaded([fresh()], M.keep_submitted([row(loaded="TBS-2")], set()), UPLOAD)[0]
    assert not out["ok"], out
    assert out["errors"] and "TBS-2" in out["errors"][0] and "cancelled" in out["errors"][0], out
    assert out.get("was_loaded") == "TBS-2" and "loaded" not in out, out


def test_keep_submitted_keeps_the_name_as_was_loaded():
    out = M.keep_submitted([row(loaded="TBS-2"), row("AMUS", loaded="TBS-3")], {"TBS-3"})
    assert "loaded" not in out[0] and out[0]["was_loaded"] == "TBS-2" and not out[0]["ok"], out[0]
    assert out[1]["loaded"] == "TBS-3" and "was_loaded" not in out[1], out[1]


def test_the_marker_survives_every_later_recheck():
    """A second resume must refuse it as the first did, not reload it."""
    previous = M.keep_submitted([row(loaded="TBS-2")], set())
    for _ in range(3):
        previous = M.merge_loaded([fresh()], previous, UPLOAD)
        assert not previous[0]["ok"] and previous[0]["was_loaded"] == "TBS-2", previous


def test_a_tb_amended_after_cancel_is_reported_not_claimed_and_never_reloaded():
    """TBS-2 (loaded by this upload) is cancelled and amended as TBS-2-1. The
    amendment copies tb_file, so its file still carries the upload's prefix;
    it was submitted by a person, not loaded by this upload."""
    previous = M.cancelled_upload_values(
        {"report": json.dumps([row(loaded="TBS-2")]), "failed_count": 0, "status": "Loaded"}, "TBS-2")
    previous = json.loads(previous["report"])
    amended = fresh(existing="TBS-2-1", existing_file=f"/private/files/{UPLOAD}-AMDE-2025-P12.csv")
    out = M.merge_loaded([amended], previous, UPLOAD)[0]
    assert "loaded" not in out and not out["ok"], out
    assert any("TBS-2-1" in e for e in out["errors"]), out
    assert out["was_loaded"] == "TBS-2", out
    # the amendment is cancelled too: still a problem, still not reloaded
    out = M.merge_loaded([fresh()], [out], UPLOAD)[0]
    assert not out["ok"] and out["was_loaded"] == "TBS-2" and "loaded" not in out, out


def test_a_row_without_a_marker_is_still_recognised_by_its_file():
    """Control: a load that stopped before recording its name (no marker)."""
    stopped = fresh(existing="TBS-5", existing_file=f"/private/files/{UPLOAD}-AMDE-2025-P12.csv")
    assert M.merge_loaded([stopped], [], UPLOAD)[0]["loaded"] == "TBS-5"


# -- the cancel (pure) -------------------------------------------------------------

def _upload(rows, status="Loaded", failed_count=0):
    return {"report": json.dumps(rows), "failed_count": failed_count, "status": status}


def test_cancel_marks_the_row_and_moves_it_from_loaded_to_failed():
    values = M.cancelled_upload_values(
        _upload([row(loaded="TBS-1"), row("AMUS", loaded="TBS-2")]), "TBS-2")
    assert (values["loaded_count"], values["failed_count"], values["valid_count"]) == (1, 1, 1), values
    assert values["status"] == "Partly Loaded", values
    rows = {r["entity"]: r for r in json.loads(values["report"])}
    assert rows["AMDE"]["loaded"] == "TBS-1" and rows["AMDE"]["ok"]
    us = rows["AMUS"]
    assert "loaded" not in us and us["was_loaded"] == "TBS-2" and not us["ok"], us
    assert "TBS-2" in us["errors"][0] and "cancelled" in us["errors"][0], us


def test_cancelling_every_load_leaves_a_failed_upload():
    up = _upload([row(loaded="TBS-1"), row("AMUS", loaded="TBS-2")])
    up.update(M.cancelled_upload_values(up, "TBS-1"))
    values = M.cancelled_upload_values(up, "TBS-2")
    assert (values["loaded_count"], values["failed_count"], values["status"]) == (0, 2, "Failed"), values


def test_a_skipped_problem_row_is_not_counted_as_failed_by_the_cancel():
    """A row skipped with skip_invalid (never loaded) is not a failure."""
    problem = row("AMHQ", ok=False, errors=["1010 is not in the chart"])
    values = M.cancelled_upload_values(_upload([row(loaded="TBS-1"), problem]), "TBS-1")
    assert (values["loaded_count"], values["failed_count"]) == (0, 1), values


def test_an_upload_whose_report_no_longer_has_the_row_is_left_alone():
    assert M.cancelled_upload_values(_upload([row(loaded="TBS-1")]), "TBS-9") is None
    assert M.cancelled_upload_values(_upload([]), "TBS-1") is None
    # already marked (the name appears only as the marker): nothing to do
    assert M.cancelled_upload_values(_upload([row(ok=False, was_loaded="TBS-1")]), "TBS-1") is None


def test_a_checked_or_loading_upload_keeps_its_status():
    for status in ("Checked", "Loading"):
        values = M.cancelled_upload_values(_upload([row(loaded="TBS-1")], status=status), "TBS-1")
        assert "status" not in values and values["loaded_count"] == 0, (status, values)


# -- run_load's counts ---------------------------------------------------------------

def test_run_load_does_not_count_a_skipped_problem_row_as_failed():
    """The #331 test gap: `failed = len([r for r in report if not r["ok"]])`
    survived because no test had a problem row skipped with skip_invalid."""
    site = H._Site(submitted=["TBS-1"])
    problem = row("AMHQ", ok=False, errors=["1010 is not in the chart"])
    final = H._load(site, [H._row("AMDE", "TBS-1"), problem], H._table("AMDE", "AMHQ"))
    assert site.errors == []
    assert (final["loaded_count"], final["failed_count"], final["status"]) == (1, 0, "Loaded"), final


def test_run_load_counts_a_row_cancelled_earlier_as_failed():
    """The cancel counted it as failed; the next run must not forget it."""
    site = H._Site(submitted=["TBS-1"])
    cancelled = row("AMUS", ok=False, was_loaded="TBS-2",
                    errors=["Loaded earlier as TBS-2, which has since been cancelled."])
    problem = row("AMHQ", ok=False, errors=["1010 is not in the chart"])
    final = H._load(site, [H._row("AMDE", "TBS-1"), cancelled, problem], H._table("AMDE", "AMUS", "AMHQ"))
    assert site.errors == []
    assert (final["loaded_count"], final["failed_count"], final["status"]) == (1, 1, "Partly Loaded"), final
    rows = {r["entity"]: r for r in json.loads(final["report"])}
    assert rows["AMUS"]["was_loaded"] == "TBS-2" and "loaded" not in rows["AMUS"]


# -- the wiring ----------------------------------------------------------------------

def _hook(uploads):
    """tb_bulk.on_submission_cancelled against a stub site holding `uploads`
    ({name: {report, failed_count, status}}). Returns the values written."""
    writes = []
    frappe = types.ModuleType("frappe")
    frappe_utils = types.ModuleType("frappe.utils")
    frappe_utils.cint = lambda v: int(v or 0)
    frappe_utils.strip_html = lambda v: v
    frappe.utils = frappe_utils
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)

    def get_all(doctype, filters=None, fields=None, pluck=None, limit_page_length=None, **kw):
        assert doctype == "Trial Balance Upload", doctype
        op, pattern = filters["report"]
        assert op == "like", filters
        needle = pattern.strip("%")
        hits = [n for n, u in uploads.items() if needle in u["report"]]
        if pluck:
            return hits
        return [types.SimpleNamespace(name=n, **uploads[n]) for n in hits]

    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(
        set_value=lambda doctype, name, values, update_modified=True: writes.append((doctype, name, dict(values))))

    def stub(name, **attrs):
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        return mod

    stubs = {
        "frappe": frappe, "frappe.utils": frappe_utils, "konsol": stub("konsol", tb_bulk_model=M),
        "konsol.tb_bulk_model": M, "konsol.period_status": stub("konsol.period_status"),
        "konsol.clickhouse": stub("konsol.clickhouse", execute=lambda *a, **k: ""),
        "konsol.consolidation.doctype.intercompany_account.intercompany_account":
            stub("ica", intercompany_accounts=lambda: []),
        "konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission":
            stub("tbs", CONTROL_TABLE="tb_control", _sql_str=lambda s: s, partnerless_ic_accounts=lambda r, i: [],
                 partnerless_warning=lambda n: "", validate_tb_rows=lambda rows, **kw: []),
        "konsol.entity_permissions": stub("ep", allowed_entity_codes=lambda user=None: None),
        "konsol.group_chart": stub("gc", chart_accounts=lambda: {}),
        "konsol.tb_dimension": stub("td", declared_dimensions=lambda: []),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        mod = _by_path("tb_bulk_332_under_test", TB_BULK_PATH)
        mod.on_submission_cancelled("TBS-2")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return writes


def test_the_cancel_hook_writes_the_upload_that_loaded_it_and_no_other():
    uploads = {
        UPLOAD: _upload([row(loaded="TBS-1"), row("AMUS", loaded="TBS-2")]),
        # names TBS-2 only as an existing submission it refused, never loaded it
        "TBU-00005": _upload([row("AMUS", ok=False, existing="TBS-2", errors=["TBS-2 is already submitted"])],
                             status="Checked"),
        "TBU-00006": _upload([row(loaded="TBS-20")]),
    }
    writes = _hook(uploads)
    assert [(d, n) for d, n, _ in writes] == [("Trial Balance Upload", UPLOAD)], writes
    values = writes[0][2]
    assert (values["loaded_count"], values["failed_count"], values["status"]) == (1, 1, "Partly Loaded"), values


def test_the_cancel_hook_writes_nothing_when_no_report_has_the_row():
    assert _hook({UPLOAD: _upload([row(loaded="TBS-1")]), "TBU-00009": _upload([])}) == []


def test_on_cancel_calls_the_hook():
    with open(TBS_CONTROLLER) as f:
        tree = ast.parse(f.read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TrialBalanceSubmission")
    on_cancel = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "on_cancel")
    calls = [ast.unparse(n) for n in ast.walk(on_cancel) if isinstance(n, ast.Call)]
    assert "on_submission_cancelled(self.name)" in calls, calls
