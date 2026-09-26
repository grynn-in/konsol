"""konsol#305 A48 — an Assertion Run's sign-off and result fields change only
through their writers.

Measured live 25 Sep 2026 (A22): as the Close Lead, POST
frappe.client.set_value(Assertion Run, <run>, "signoff_status", "Signed Off")
returned 200 and stored, and the dict form stored an Overridden signature with
override_reason="forged". set_value goes through doc.save, so validate is the
place to refuse it. read_only on the doctype only guards the form.

- Sign-off fields change only inside sign_off_close (the sign-off writer).
- Result fields change only inside run_close_assertions (the worker).
- Any other save that changes one is refused; a change to neither is allowed.

A50 (found by A48): the run's scope and origin (fiscal_year, fiscal_period,
pipeline_run, triggered_by) never change after insert, for any writer: a Close
Lead could MOVE a signed run to another period with set_value(fiscal_period=N),
and that period then read as signed. The results table (Assertion Step rows)
changes only inside the worker: a frappe.client.save with edited step rows
could rewrite which checks failed.

A62 (review #8): `log` (the dbt output, the run's evidence) is a result field,
written only by the worker, and `title` is fixed at insert with the scope. A
Close Lead could otherwise rewrite a signed run's log with set_value.

assertion_run.py is loaded against a stub frappe, as in
test_close_assertion_suite.py (copied, not imported).
"""
import ast
import datetime
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AR_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run")
AR_PY = os.path.join(AR_DIR, "assertion_run.py")
AR_JSON = os.path.join(AR_DIR, "assertion_run.json")

SIGNOFF_FIELDS = ("signoff_status", "signed_off_by", "signed_off_at", "override_reason",
                  "acknowledgement", "warnings_at_signoff", "affected_by")
RESULT_FIELDS = ("status", "total", "passed", "failed", "errored", "warned",
                 "started_at", "completed_at", "duration_seconds", "log")
SCOPE_FIELDS = ("fiscal_year", "fiscal_period", "pipeline_run", "triggered_by", "title")
STEP_JSON = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_step",
                         "assertion_step.json")

#: A value for each field that differs from the saved row in _saved().
FORGED = {
    "signoff_status": "Signed Off", "signed_off_by": "forger@example.com",
    "signed_off_at": datetime.datetime(2026, 9, 25, 12, 0, 0),
    "override_reason": "forged", "acknowledgement": "forged",
    "warnings_at_signoff": "forged", "affected_by": "forged", "status": "Green", "total": 99, "passed": 99,
    "failed": 7, "errored": 7, "warned": 7,
    "started_at": datetime.datetime(2026, 9, 25, 12, 0, 0),
    "completed_at": datetime.datetime(2026, 9, 25, 12, 5, 0),
    "duration_seconds": 1.5,
    "fiscal_year": 2098, "fiscal_period": 2, "pipeline_run": "PR-FORGED",
    "triggered_by": "forger@example.com",
    "log": "forged: all 131 assertions passed", "title": "forged title",
}


def _steps():
    """The stored results: one pass, two fails (fresh dicts each call)."""
    return [
        {"assertion": "assert_a", "dimension": "Other", "status": "Pass", "rows_failed": 0,
         "severity": "error", "message": "", "failures_table": "", "sample_rows": ""},
        {"assertion": "assert_b", "dimension": "FX", "status": "Fail", "rows_failed": 4,
         "severity": "error", "message": "4 rows", "failures_table": "audit.assert_b",
         "sample_rows": "x"},
        {"assertion": "assert_c", "dimension": "Consolidation", "status": "Fail",
         "rows_failed": 2, "severity": "error", "message": "2 rows",
         "failures_table": "audit.assert_c", "sample_rows": "y"},
    ]


def _edited_steps():
    """Each way a forged save can rewrite which checks failed."""
    added = _steps() + [dict(_steps()[0], assertion="assert_d")]
    removed = _steps()[:2]
    status = _steps()
    status[1]["status"] = "Pass"
    rows = _steps()
    rows[2]["rows_failed"] = 0
    message = _steps()
    message[1]["message"] = "all fine"
    emptied = []
    return {"added": added, "removed": removed, "status": status, "rows_failed": rows,
            "message": message, "emptied": emptied}


def _saved():
    """The stored row: a finished Red run, not signed."""
    return {
        "name": "AR-1", "title": "old title", "fiscal_year": 2099, "fiscal_period": 1,
        "status": "Red", "total": 3, "passed": 1, "failed": 2, "errored": 0, "warned": 0,
        "started_at": datetime.datetime(2026, 9, 1, 9, 0, 0),
        "completed_at": datetime.datetime(2026, 9, 1, 9, 5, 0, 250000),
        "duration_seconds": 300.0,
        "signoff_status": "Not Signed Off", "signed_off_by": None, "signed_off_at": None,
        "override_reason": None, "acknowledgement": None, "warnings_at_signoff": None,
        "affected_by": None, "pipeline_run": None, "triggered_by": "analyst@example.com", "results": _steps(),
        "log": "dbt test: 1 pass, 2 fail",
    }


class _Refused(Exception):
    pass


def _load():
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = _Refused
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, *a, **k):
        raise (exc or frappe.ValidationError)(str(msg))

    frappe.throw = throw
    frappe._ = lambda s: types.SimpleNamespace(format=lambda *a: s.format(*a))
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.get_roles = lambda: ["EPM Admin"]
    frappe.has_permission = lambda *a, **k: True
    frappe.session = types.SimpleNamespace(user="lead@example.com")
    frappe.utils = types.SimpleNamespace(
        get_bench_path=lambda: "/bench",
        now_datetime=lambda: datetime.datetime(2026, 9, 25, 10, 0, 0))
    frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: None, commit=lambda: None,
                                      set_value=lambda *a, **k: None,
                                      exists=lambda *a, **k: True)
    frappe.get_all = lambda *a, **k: []
    frappe.publish_realtime = lambda *a, **k: None

    class Document:
        def __init__(self, **fields):
            is_new = fields.pop("_is_new", False)
            before_save = fields.pop("_before_save", None)
            self.__dict__.update(fields)
            self.__dict__["_is_new"] = is_new
            self.__dict__["_before_save"] = before_save

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, fieldname):
            return getattr(self, fieldname)

        def set(self, fieldname, value):
            setattr(self, fieldname, value)

        def append(self, fieldname, row):
            rows = self.__dict__.setdefault(fieldname, [])
            rows.append(row)

        def is_new(self):
            return self._is_new

        def get_doc_before_save(self):
            return self._before_save

        def has_value_changed(self, fieldname):
            previous = self.get_doc_before_save()
            if not previous:
                return True
            return previous.get(fieldname) != self.get(fieldname)

    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = Document

    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (_Refused,), {})
    period_status.assert_declared = lambda *a: None
    period_status.OPEN = "Open"
    # A59: trigger_close_run and sign_off_close read the period's status; open here.
    period_status.period_row = lambda fy, fp: {"code": "P%02d" % int(fp), "status": "Open"}

    as_spec = importlib.util.spec_from_file_location(
        "konsol.assertion_status", os.path.join(APP_DIR, "assertion_status.py"))
    assertion_status = importlib.util.module_from_spec(as_spec)
    as_spec.loader.exec_module(assertion_status)

    mods = {"frappe": frappe, "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": doc_mod, "konsol": types.ModuleType("konsol"),
            "konsol.period_status": period_status,
            "konsol.assertion_status": assertion_status}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("assertion_run_frozen_under_test", AR_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module, frappe


def _doc(module, **changes):
    """A saved run, reloaded and changed, ready for validate()."""
    fields = _saved()
    fields.update(changes)
    return module.AssertionRun(_is_new=False, _before_save=_saved(), **fields)


def _refused(fn):
    try:
        fn()
    except _Refused as e:
        return str(e)
    return None


# --- the field lists ---------------------------------------------------------

def test_field_lists_are_the_declared_ones():
    module, _ = _load()
    assert tuple(module.SIGNOFF_FIELDS) == SIGNOFF_FIELDS
    assert tuple(module.RESULT_FIELDS) == RESULT_FIELDS
    declared = {f["fieldname"] for f in json.load(open(AR_JSON))["fields"]}
    missing = [f for f in SIGNOFF_FIELDS + RESULT_FIELDS if f not in declared]
    assert not missing, "not fields of Assertion Run: %s" % missing


# --- unflagged saves ---------------------------------------------------------

def test_unflagged_change_to_any_frozen_field_is_refused():
    module, _ = _load()
    accepted = []
    for field in SIGNOFF_FIELDS + RESULT_FIELDS:
        msg = _refused(_doc(module, **{field: FORGED[field]}).validate)
        if msg is None:
            accepted.append(field)
        else:
            assert field in msg, "the refusal must name the field: %r" % msg
    assert not accepted, "unflagged saves changed: %s" % accepted


def test_the_live_forge_is_refused():
    """A22 7c: the dict form (Overridden, signed_off_by, override_reason)."""
    module, _ = _load()
    doc = _doc(module, signoff_status="Overridden", signed_off_by="lead@example.com",
               override_reason="forged")
    assert _refused(doc.validate) is not None


def test_a_save_that_changes_nothing_is_allowed():
    """A62: every field of a stored run is now frozen, so the allowed save is
    one that changes nothing (the Desk sending the row back)."""
    module, _ = _load()
    _doc(module).validate()


def test_a_desk_form_save_with_string_values_is_allowed():
    """The Desk sends every field back as JSON: datetimes as strings, a Float
    that is whole as an int. Equal values are not a change."""
    module, _ = _load()
    s = _saved()
    doc = _doc(module,
               started_at=str(s["started_at"]), completed_at=str(s["completed_at"]),
               duration_seconds=300, signed_off_by="", override_reason="")
    doc.validate()


def test_a_new_run_is_not_checked_here():
    """before_insert blanks a new run; validate does not compare it."""
    module, _ = _load()
    module.AssertionRun(_is_new=True, fiscal_year=2099, fiscal_period=1,
                        signoff_status="Not Signed Off", status="Queued").validate()


def test_a_run_with_no_saved_version_is_not_compared():
    """Frappe loads the saved version on every save of a stored row; it is
    None only when the row is gone (nothing to forge). test_close_assertion_suite
    builds its validate() cases this way, so they must keep passing."""
    module, _ = _load()
    fields = _saved()
    fields["title"] = "x"
    module.AssertionRun(_is_new=False, _before_save=None, **fields).validate()


# --- the writers (failure path: they must still save) ------------------------

def test_signoff_writer_may_change_signoff_fields_only():
    module, _ = _load()
    changes = {f: FORGED[f] for f in SIGNOFF_FIELDS}
    with module.writing(module.SIGNOFF_WRITER, "AR-1"):
        _doc(module, **changes).validate()
        assert _refused(_doc(module, status="Green").validate) is not None
    # the flag ends with the block
    assert _refused(_doc(module, **changes).validate) is not None


def test_re_sign_needed_is_set_only_by_the_signoff_writer():
    """A27: marking a signed run "Re-sign Needed" (and naming what reopened)
    is a sign-off change: refused as a forge, accepted inside the writer."""
    module, _ = _load()
    mark = {"signoff_status": "Re-sign Needed", "affected_by": "FY2099 P01 reopened"}
    signed = dict(signoff_status="Signed Off", signed_off_by="lead@example.com")
    for changes in (mark, {"affected_by": "forged"}):
        before = dict(_saved(), **signed)
        doc = module.AssertionRun(_is_new=False, _before_save=before, **dict(before, **changes))
        msg = _refused(doc.validate)
        assert msg is not None, "an unflagged save changed %s" % sorted(changes)
        assert all(f in msg for f in changes), msg
    before = dict(_saved(), **signed)
    with module.writing(module.SIGNOFF_WRITER, "AR-1"):
        module.AssertionRun(_is_new=False, _before_save=before,
                            **dict(before, **mark)).validate()


def test_worker_may_change_result_fields_only():
    module, _ = _load()
    changes = {f: FORGED[f] for f in RESULT_FIELDS}
    with module.writing(module.WORKER_WRITER, "AR-1"):
        _doc(module, **changes).validate()
        assert _refused(_doc(module, signoff_status="Signed Off").validate) is not None
    assert _refused(_doc(module, **changes).validate) is not None


def test_a_writer_flag_covers_only_its_own_run():
    module, _ = _load()
    with module.writing(module.SIGNOFF_WRITER, "AR-OTHER"):
        assert _refused(_doc(module, signoff_status="Signed Off").validate) is not None


def test_sign_off_close_saves_as_the_signoff_writer():
    module, frappe = _load()
    seen = []
    doc = types.SimpleNamespace(name="AR-1", status="Green", warned=0, fiscal_year=2099,
                                fiscal_period=1, signoff_status="Not Signed Off",
                                started_at=None)
    doc.save = lambda **k: seen.append(module.active_writer())
    frappe.get_doc = lambda *a, **k: doc
    gate = types.ModuleType("konsol.close.signoff_gate")
    gate.assert_can_sign = lambda *a: None
    # A63: no data change recorded, so the run is current.
    gate.data_change = lambda *a: {"data_changed_at": None, "data_changed_by": None,
                                   "data_change": None}
    # A66: the data-change rule is the real, pure signoff_model (loaded by path).
    sm_spec = importlib.util.spec_from_file_location(
        "signoff_model_for_frozen_fields", os.path.join(APP_DIR, "close", "signoff_model.py"))
    signoff_model = importlib.util.module_from_spec(sm_spec)
    sm_spec.loader.exec_module(signoff_model)
    pkg = types.ModuleType("konsol.close")
    pkg.signoff_gate = gate
    pkg.signoff_model = signoff_model
    names = ("konsol", "konsol.close", "konsol.close.signoff_gate", "konsol.close.signoff_model")
    before = {n: sys.modules.get(n) for n in names}
    konsol_pkg = types.ModuleType("konsol")
    konsol_pkg.close = pkg
    sys.modules.update({"konsol": konsol_pkg, "konsol.close": pkg,
                        "konsol.close.signoff_gate": gate,
                        "konsol.close.signoff_model": signoff_model})
    try:
        module.sign_off_close("AR-1")
    finally:
        for n, old in before.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    assert seen == [(module.SIGNOFF_WRITER, "AR-1")], seen
    assert module.active_writer() is None


def test_worker_saves_as_the_worker_writer():
    module, frappe = _load()
    seen = []

    class Run(types.SimpleNamespace):
        def save(self, **k):
            seen.append(module.active_writer())

        def reload(self):
            pass

        def set(self, field, value):
            setattr(self, field, value)

        def append(self, field, row):
            getattr(self, field).append(row)

    run = Run(name="AR-1", status="Queued", started_at=None, completed_at=None, log="",
              results=[])
    frappe.get_doc = lambda *a, **k: run
    frappe.get_single = lambda *a: types.SimpleNamespace(dbt_project_path="/nonexistent-a48")

    def no_dbt(*a, **k):
        raise OSError("no dbt on the host")

    module.subprocess = types.SimpleNamespace(Popen=no_dbt, PIPE=-1, STDOUT=-2,
                                              TimeoutExpired=TimeoutError)
    module.run_close_assertions("AR-1")
    assert seen == [(module.WORKER_WRITER, "AR-1")] * 2, seen
    assert run.status == "Error"
    assert module.active_writer() is None


def test_no_other_save_of_an_assertion_run_in_the_module():
    """Every doc.save in assertion_run.py sits inside a `with writing(...)`
    block; a new save outside one would be refused on live."""
    tree = ast.parse(open(AR_PY).read())
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    bare = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "save"):
            p = parents.get(node)
            inside = False
            while p is not None:
                if isinstance(p, ast.With) and any(
                        isinstance(i.context_expr, ast.Call)
                        and getattr(i.context_expr.func, "id", None) == "writing"
                        for i in p.items):
                    inside = True
                    break
                p = parents.get(p)
            if not inside:
                bare.append(node.lineno)
    assert not bare, "doc.save outside a writing() block at line(s) %s" % bare


# --- A50: scope and origin never change after insert ------------------------

def test_scope_fields_are_declared():
    module, _ = _load()
    assert tuple(module.SCOPE_FIELDS) == SCOPE_FIELDS
    declared = {f["fieldname"] for f in json.load(open(AR_JSON))["fields"]}
    missing = [f for f in SCOPE_FIELDS if f not in declared]
    assert not missing, "not fields of Assertion Run: %s" % missing


def test_scope_change_is_refused_for_every_writer():
    """No writer may move a run: the gates find a period's runs by
    fiscal_year/fiscal_period, so a moved signed run signs another period."""
    module, _ = _load()
    accepted = []
    for writer in (None, module.SIGNOFF_WRITER, module.WORKER_WRITER):
        for field in SCOPE_FIELDS:
            doc = _doc(module, **{field: FORGED[field]})
            if writer is None:
                msg = _refused(doc.validate)
            else:
                with module.writing(writer, "AR-1"):
                    msg = _refused(doc.validate)
            if msg is None:
                accepted.append((writer, field))
            else:
                assert field in msg, "the refusal must name the field: %r" % msg
    assert not accepted, "scope changed: %s" % accepted


def test_the_live_move_is_refused():
    """A48 live: set_value(fiscal_period=N) on a signed run."""
    module, _ = _load()
    doc = _doc(module, fiscal_period=3, signoff_status="Not Signed Off")
    msg = _refused(doc.validate)
    assert msg is not None and "fiscal_period" in msg, msg


def test_a_desk_save_of_an_unchanged_scope_is_allowed():
    """A year-only run is stored with fiscal_period 0; the Desk may send it
    back as 0, "" or None, and an empty Link as "". Neither is a move."""
    module, _ = _load()
    for period in (0, "", None):
        before = _saved()
        before["fiscal_period"] = 0
        fields = _saved()
        fields.update(fiscal_period=period, pipeline_run="", fiscal_year="2099")
        module.AssertionRun(_is_new=False, _before_save=before, **fields).validate()


# --- A50: the results table changes only inside the worker -------------------

def test_step_fields_are_the_declared_ones():
    module, _ = _load()
    fields = [f["fieldname"] for f in json.load(open(STEP_JSON))["fields"]
              if f["fieldtype"] not in ("Section Break", "Column Break", "Tab Break")]
    assert sorted(module.STEP_FIELDS) == sorted(fields), (module.STEP_FIELDS, fields)


def test_an_unflagged_edit_to_the_results_is_refused():
    module, _ = _load()
    accepted = []
    for case, rows in _edited_steps().items():
        msg = _refused(_doc(module, results=rows).validate)
        if msg is None:
            accepted.append(case)
        else:
            assert "results" in msg, "the refusal must name the table: %r" % msg
    assert not accepted, "unflagged results edits saved: %s" % accepted


def test_the_signoff_writer_cannot_edit_the_results():
    module, _ = _load()
    with module.writing(module.SIGNOFF_WRITER, "AR-1"):
        for case, rows in _edited_steps().items():
            assert _refused(_doc(module, results=rows).validate) is not None, case


def test_the_worker_may_replace_the_results():
    """Failure path: the worker's own save (it rebuilds the table in
    _parse_results) must still pass."""
    module, _ = _load()
    with module.writing(module.WORKER_WRITER, "AR-1"):
        for rows in _edited_steps().values():
            _doc(module, results=rows).validate()
        # the flag covers its own run only
    with module.writing(module.WORKER_WRITER, "AR-OTHER"):
        assert _refused(_doc(module, results=[]).validate) is not None


def test_a_desk_save_with_unchanged_results_is_allowed():
    """The Desk sends child rows back with row metadata and Ints as strings."""
    module, _ = _load()
    rows = _steps()
    for i, row in enumerate(rows, start=1):
        row.update(name="row-%d" % i, idx=i, parent="AR-1", doctype="Assertion Step")
        row["rows_failed"] = str(row["rows_failed"])
    _doc(module, results=rows).validate()


def test_child_rows_as_documents_are_compared():
    """Frappe holds child rows as Documents, read with .get(field)."""
    module, _ = _load()

    class Row:
        def __init__(self, data):
            self._d = data

        def get(self, field):
            return self._d.get(field)

    same = [Row(r) for r in _steps()]
    _doc(module, results=same).validate()
    changed = _steps()
    changed[1]["status"] = "Pass"
    assert _refused(_doc(module, results=[Row(r) for r in changed]).validate) is not None


# --- A51: a finished run cannot be deleted -----------------------------------
# Found by A50: System Manager has delete on Assertion Run. Deleting a newer
# unsigned run makes an older signed run the one latest_close_run returns, so
# the period reads as signed. frappe.client.delete -> frappe.delete_doc ->
# on_trash, and on_trash runs with force=True too. Test cleanup on live uses
# frappe.db.delete on ZZ rows, never the API.

TERMINAL = ("Green", "Amber", "Red", "Error")
SIGNED = ("Signed Off", "Acknowledged", "Overridden", "Re-sign Needed")


def _trash(module, **changes):
    fields = _saved()
    fields.update(changes)
    doc = module.AssertionRun(_is_new=False, _before_save=None, **fields)
    return _refused(doc.on_trash)


def test_a_terminal_run_cannot_be_deleted():
    module, _ = _load()
    for status in TERMINAL:
        msg = _trash(module, status=status, signoff_status="Not Signed Off")
        assert msg is not None, "a %s run was deleted" % status
        assert "AR-1" in msg and status in msg and "cannot be deleted" in msg, msg


def test_a_signed_run_cannot_be_deleted():
    """Signed, or signed and then invalidated (Re-sign Needed, A27): both are
    the record of a close. Refused whatever the status field says."""
    module, _ = _load()
    for signoff in SIGNED:
        for status in ("Green", "Queued"):
            msg = _trash(module, status=status, signoff_status=signoff,
                         results=[] if status == "Queued" else _steps())
            assert msg is not None, "a %s run (%s) was deleted" % (signoff, status)
            assert "AR-1" in msg and signoff in msg and "cannot be deleted" in msg, msg


def test_a_started_run_cannot_be_deleted():
    """Running, or Queued with results already written: it has started."""
    module, _ = _load()
    msg = _trash(module, status="Running", results=[], signoff_status="Not Signed Off")
    assert msg is not None and "AR-1" in msg and "Running" in msg, msg
    msg = _trash(module, status="Queued", signoff_status="Not Signed Off")
    assert msg is not None and "AR-1" in msg and "results" in msg, msg


def test_a_run_that_never_started_can_be_deleted():
    """Failure path: a Queued run with no results and no sign-off may go."""
    module, _ = _load()
    for results in ([], None):
        for signoff in ("Not Signed Off", None, ""):
            assert _trash(module, status="Queued", results=results,
                          signoff_status=signoff) is None, (results, signoff)


# --- A51: two A50 refusal messages corrected ---------------------------------

def test_a_results_refusal_names_only_the_worker():
    module, _ = _load()
    rows = _edited_steps()["status"]
    assert _refused(_doc(module, results=rows).validate) == (
        "Assertion Run AR-1: results can only be changed by running the checks. "
        "Nothing was saved.")
    assert _refused(_doc(module, status="Green").validate) == (
        "Assertion Run AR-1: status can only be changed by running the checks. "
        "Nothing was saved.")


def test_a_signoff_refusal_names_only_sign_off():
    module, _ = _load()
    assert _refused(_doc(module, signoff_status="Signed Off").validate) == (
        "Assertion Run AR-1: signoff_status can only be changed by Sign off "
        "(sign_off_close). Nothing was saved.")


def test_a_mixed_refusal_names_each_writer():
    module, _ = _load()
    assert _refused(_doc(module, signoff_status="Signed Off", status="Green").validate) == (
        "Assertion Run AR-1: signoff_status can only be changed by Sign off "
        "(sign_off_close); status can only be changed by running the checks. "
        "Nothing was saved.")


def test_the_scope_refusal_agrees_in_number():
    module, _ = _load()
    tail = (" when the run starts and cannot be changed. "
            "Run the checks for the other period instead. Nothing was saved.")
    assert _refused(_doc(module, fiscal_year=2098, fiscal_period=2).validate) == (
        "Assertion Run AR-1: fiscal_year, fiscal_period are fixed" + tail)
    assert _refused(_doc(module, fiscal_period=2).validate) == (
        "Assertion Run AR-1: fiscal_period is fixed" + tail)


# --- A62: the log and the title are frozen too -------------------------------

def test_the_log_forge_is_refused():
    """Review #8: set_value(log=...) by the Close Lead on a signed run."""
    module, _ = _load()
    before = dict(_saved(), signoff_status="Signed Off", signed_off_by="lead@example.com")
    doc = module.AssertionRun(_is_new=False, _before_save=before,
                              **dict(before, log=FORGED["log"]))
    msg = _refused(doc.validate)
    assert msg is not None, "an unflagged save rewrote the log"
    assert "log" in msg and "running the checks" in msg, msg


def test_the_log_is_refused_to_the_signoff_writer():
    module, _ = _load()
    with module.writing(module.SIGNOFF_WRITER, "AR-1"):
        msg = _refused(_doc(module, log=FORGED["log"]).validate)
    assert msg is not None and "log" in msg, msg


def test_the_worker_may_write_the_log():
    """Failure path: the worker's own saves (log cleared at start, full log at
    the end) must still go through."""
    module, _ = _load()
    with module.writing(module.WORKER_WRITER, "AR-1"):
        _doc(module, log="").validate()
        _doc(module, log="dbt test: done", status="Green").validate()
    with module.writing(module.WORKER_WRITER, "AR-OTHER"):
        assert _refused(_doc(module, log="x").validate) is not None


def test_the_title_is_refused_for_every_writer():
    module, _ = _load()
    for writer in (None, module.SIGNOFF_WRITER, module.WORKER_WRITER):
        doc = _doc(module, title=FORGED["title"])
        if writer is None:
            msg = _refused(doc.validate)
        else:
            with module.writing(writer, "AR-1"):
                msg = _refused(doc.validate)
        assert msg is not None and "title" in msg, (writer, msg)
