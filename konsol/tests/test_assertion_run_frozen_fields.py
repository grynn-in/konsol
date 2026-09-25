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
                  "acknowledgement", "warnings_at_signoff")
RESULT_FIELDS = ("status", "total", "passed", "failed", "errored", "warned",
                 "started_at", "completed_at", "duration_seconds")

#: A value for each field that differs from the saved row in _saved().
FORGED = {
    "signoff_status": "Signed Off", "signed_off_by": "forger@example.com",
    "signed_off_at": datetime.datetime(2026, 9, 25, 12, 0, 0),
    "override_reason": "forged", "acknowledgement": "forged",
    "warnings_at_signoff": "forged", "status": "Green", "total": 99, "passed": 99,
    "failed": 7, "errored": 7, "warned": 7,
    "started_at": datetime.datetime(2026, 9, 25, 12, 0, 0),
    "completed_at": datetime.datetime(2026, 9, 25, 12, 5, 0),
    "duration_seconds": 1.5,
}


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


def test_a_change_to_neither_is_allowed():
    module, _ = _load()
    _doc(module, title="new title").validate()


def test_a_desk_form_save_with_string_values_is_allowed():
    """The Desk sends every field back as JSON: datetimes as strings, a Float
    that is whole as an int. Equal values are not a change."""
    module, _ = _load()
    s = _saved()
    doc = _doc(module, title="new title",
               started_at=str(s["started_at"]), completed_at=str(s["completed_at"]),
               duration_seconds=300, signed_off_by="", override_reason="")
    doc.validate()


def test_a_new_run_is_not_checked_here():
    """before_insert blanks a new run; validate does not compare it."""
    module, _ = _load()
    module.AssertionRun(_is_new=True, fiscal_year=2099, fiscal_period=1,
                        signoff_status="Not Signed Off", status="Queued").validate()


def test_a_saved_run_with_no_saved_version_is_refused():
    module, _ = _load()
    fields = _saved()
    fields["signoff_status"] = "Signed Off"
    doc = module.AssertionRun(_is_new=False, _before_save=None, **fields)
    assert _refused(doc.validate) is not None


# --- the writers (failure path: they must still save) ------------------------

def test_signoff_writer_may_change_signoff_fields_only():
    module, _ = _load()
    changes = {f: FORGED[f] for f in SIGNOFF_FIELDS}
    with module.writing(module.SIGNOFF_WRITER, "AR-1"):
        _doc(module, **changes).validate()
        assert _refused(_doc(module, status="Green").validate) is not None
    # the flag ends with the block
    assert _refused(_doc(module, **changes).validate) is not None


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
                                fiscal_period=1, signoff_status="Not Signed Off")
    doc.save = lambda **k: seen.append(module.active_writer())
    frappe.get_doc = lambda *a, **k: doc
    gate = types.ModuleType("konsol.close.signoff_gate")
    gate.assert_can_sign = lambda *a: None
    pkg = types.ModuleType("konsol.close")
    pkg.signoff_gate = gate
    names = ("konsol", "konsol.close", "konsol.close.signoff_gate")
    before = {n: sys.modules.get(n) for n in names}
    konsol_pkg = types.ModuleType("konsol")
    konsol_pkg.close = pkg
    sys.modules.update({"konsol": konsol_pkg, "konsol.close": pkg,
                        "konsol.close.signoff_gate": gate})
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
