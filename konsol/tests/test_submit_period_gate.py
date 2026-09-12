"""Submit only into an open period (#149).

A closed period takes no further change (decided 12 Sep 2026): after close, a
correction is a new document in an open period. Consolidation Adjustment, IC
Balance and Allocation Run gated only their cancel, so a draft whose period
closed while it waited could still be submitted into it. The home offers a
submit button only where the server takes the submit, so its list and the
controllers are checked against each other here."""
import ast
import glob
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATED_IN_BEFORE_SUBMIT = {
    "Consolidation Adjustment": os.path.join("consolidation", "doctype", "consolidation_adjustment", "consolidation_adjustment.py"),
    "IC Balance": os.path.join("consolidation", "doctype", "ic_balance", "ic_balance.py"),
    "Allocation Run": os.path.join("allocation", "doctype", "allocation_run", "allocation_run.py"),
}

_spec = importlib.util.spec_from_file_location("home_model", os.path.join(APP_DIR, "home_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _calls(fn):
    return [getattr(c.func, "id", None) or getattr(c.func, "attr", None)
            for c in ast.walk(fn) if isinstance(c, ast.Call)]


def _controllers():
    """(doctype, class node) for every doctype controller in the app."""
    for path in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.py")):
        if path.endswith("__init__.py") or os.path.basename(path).startswith("test_"):
            continue
        meta = path[:-3] + ".json"
        if not os.path.exists(meta):
            continue
        with open(meta) as f:
            doctype = json.load(f).get("name")
        with open(path) as f:
            tree = ast.parse(f.read())
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            yield doctype, cls


def _method(cls, name):
    return next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name), None)


def _server_gates_submit(cls):
    """The server refuses the submit in a closed period: before_submit calls
    assert_open, or validate does (a trial balance checks on every save)."""
    return any(fn is not None and "assert_open" in _calls(fn)
               for fn in (_method(cls, "before_submit"), _method(cls, "validate")))


def test_the_gate_is_the_first_thing_before_submit_does():
    """Nothing is set, and no build requested, before the period is checked."""
    seen = set()
    for doctype, cls in _controllers():
        if doctype not in GATED_IN_BEFORE_SUBMIT:
            continue
        seen.add(doctype)
        fn = _method(cls, "before_submit")
        assert fn is not None, f"{doctype} has no before_submit"
        body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        first = body[0]
        assert (isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
                and ast.unparse(first.value.func) == "assert_open"), (doctype, ast.unparse(first))
        assert ast.unparse(first.value.args[0]) == "self.fiscal_year", doctype
        assert ast.unparse(first.value.args[1]) == "self.fiscal_period", doctype
    assert seen == set(GATED_IN_BEFORE_SUBMIT), set(GATED_IN_BEFORE_SUBMIT) - seen


def test_the_home_blocks_exactly_the_submits_the_server_refuses():
    server = {doctype for doctype, cls in _controllers() if _server_gates_submit(cls)}
    assert server == set(M.SUBMIT_NEEDS_OPEN_PERIOD), (
        f"server-only: {server - M.SUBMIT_NEEDS_OPEN_PERIOD}, home-only: {M.SUBMIT_NEEDS_OPEN_PERIOD - server}")


def test_submit_blocked():
    assert M.submit_blocked("IC Balance", "Dec 2099 is closed.") == "Dec 2099 is closed."
    assert M.submit_blocked("IC Balance", None) is None                      # period open
    assert M.submit_blocked("Ownership Period", "Dec 2099 is closed.") is None  # server takes it


def test_every_home_submit_action_carries_its_period_block():
    with open(os.path.join(APP_DIR, "home_api.py")) as f:
        tree = ast.parse(f.read())
    submits = [c for c in ast.walk(tree) if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "_action"
               and len(c.args) >= 3 and isinstance(c.args[2], ast.Constant) and c.args[2].value == "submit"]
    doctypes = {c.args[1].value for c in submits}
    assert set(M.SUBMIT_NEEDS_OPEN_PERIOD) <= doctypes, doctypes
    for c in submits:
        doctype = c.args[1].value
        blocked = next((k.value for k in c.keywords if k.arg == "blocked"), None)
        if doctype in M.SUBMIT_NEEDS_OPEN_PERIOD:
            assert blocked is not None and ast.unparse(blocked) == f"M.submit_blocked({doctype!r}, closed)", doctype
        else:
            assert blocked is None, f"{doctype}: the server takes this submit in a closed period"


# --- the hooks, run against a stub frappe -----------------------------------

class Refused(Exception):
    pass


def _load(doctype, period_open):
    """Import one controller with frappe stubbed; returns (module, record)."""
    record = {"checked": [], "builds": []}

    def throw(msg, *args, **kwargs):
        raise Refused(msg)

    def assert_open(fiscal_year, fiscal_period, action="run"):
        record["checked"].append((fiscal_year, fiscal_period, action))
        if not period_open:
            raise Refused(f"Cannot {action}: period closed")

    def request_governed_rebuild(doc, event, scope=None):
        record["builds"].append(scope)
        return "BA-TEST"

    class Document:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    mods = {name: types.ModuleType(name) for name in (
        "frappe", "frappe.model", "frappe.model.document", "frappe.model.workflow", "frappe.utils",
        "konsol", "konsol.clickhouse", "konsol.period_status", "konsol.schema_lifecycle")}
    frappe = mods["frappe"]
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.session = types.SimpleNamespace(user="approver@example.com")
    frappe.db = types.SimpleNamespace(after_commit=types.SimpleNamespace(add=lambda fn: None, _functions=[]))
    mods["frappe.model.document"].Document = Document
    mods["frappe.model.workflow"].get_workflow_name = lambda doctype: None
    mods["frappe.utils"].cint = lambda v: int(v or 0)
    mods["frappe.utils"].now_datetime = lambda: "NOW"
    mods["konsol.clickhouse"].sync_doctype = lambda *a: None
    mods["konsol.clickhouse"].sync_doctype_after_commit = lambda *a: None
    mods["konsol.period_status"].assert_open = assert_open
    mods["konsol.schema_lifecycle"].request_governed_rebuild = request_governed_rebuild

    saved = {name: sys.modules.get(name) for name in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("controller_under_test",
                                                      os.path.join(APP_DIR, GATED_IN_BEFORE_SUBMIT[doctype]))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    return module, record


def _doc(module, doctype):
    cls = getattr(module, doctype.replace(" ", ""))
    return cls(doctype=doctype, name="ZZ-TEST", docstatus=0, fiscal_year=2099, fiscal_period=12,
               status="Draft", approved_by=None, approved_at=None, allocation_run_id=None,
               run_by=None, run_at=None, build_approval=None)


def test_submit_into_a_closed_period_is_refused_and_changes_nothing():
    for doctype in GATED_IN_BEFORE_SUBMIT:
        module, record = _load(doctype, period_open=False)
        d = _doc(module, doctype)
        before = dict(d.__dict__)
        try:
            d.before_submit()
        except Refused:
            pass
        else:
            raise AssertionError(f"{doctype}: submit into a closed period was allowed")
        assert d.__dict__ == before, doctype
        assert [c[:2] for c in record["checked"]] == [(2099, 12)], doctype
        assert record["builds"] == [], f"{doctype}: a refused submit requested a build"


def test_submit_into_an_open_period_still_works():
    for doctype in GATED_IN_BEFORE_SUBMIT:
        module, record = _load(doctype, period_open=True)
        d = _doc(module, doctype)
        d.before_submit()
        assert [c[:2] for c in record["checked"]] == [(2099, 12)], doctype
        assert record["checked"][0][2].split()[0] in ("approve", "submit"), record["checked"]
    module, _ = _load("Consolidation Adjustment", period_open=True)
    d = _doc(module, "Consolidation Adjustment")
    d.before_submit()
    assert (d.status, d.approved_by) == ("Approved", "approver@example.com")
    module, record = _load("Allocation Run", period_open=True)
    d = _doc(module, "Allocation Run")
    d.before_submit()
    assert (d.status, d.build_approval, record["builds"]) == ("Active", "BA-TEST", ["consolidation"])
