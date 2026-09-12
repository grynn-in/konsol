"""Submit only into an open period (#149).

A closed period takes no further change (decided 12 Sep 2026): after close, a
correction is a new document in an open period. Consolidation Adjustment, IC
Balance and Allocation Run gated only their cancel, so a draft whose period
closed while it waited could still be submitted into it.

The home disables only what the server refuses and annotates what it allows
but can't complete, so its list of period-gated submits is checked here
against the controllers themselves, run against a closed period."""
import ast
import glob
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATED_IN_BEFORE_SUBMIT = ("Consolidation Adjustment", "IC Balance", "Allocation Run")
CLOSED = "Dec 2099 is closed."

_spec = importlib.util.spec_from_file_location("home_model", os.path.join(APP_DIR, "home_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _controller_paths():
    """doctype -> controller path, for every doctype in the app."""
    out = {}
    for meta in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.json")):
        py = meta[:-5] + ".py"
        if not os.path.exists(py):
            continue
        with open(meta) as f:
            data = json.load(f)
        if isinstance(data, dict) and data.get("doctype") == "DocType":
            out[data["name"]] = py
    return out


def _home_actions():
    """(doctype, ptype, call node) for every _action call in home_api."""
    with open(os.path.join(APP_DIR, "home_api.py")) as f:
        tree = ast.parse(f.read())
    for c in ast.walk(tree):
        if (isinstance(c, ast.Call) and getattr(c.func, "id", None) == "_action" and len(c.args) >= 3
                and all(isinstance(a, ast.Constant) for a in c.args[1:3])):
            yield c.args[1].value, c.args[2].value, c


# --- the hooks, run against a stub frappe -----------------------------------

class Refused(Exception):
    pass


def _load(path, period_open):
    """Import one controller with frappe stubbed; returns (module, record)."""
    record = {"checked": [], "builds": []}

    def throw(msg, *args, **kwargs):
        raise AssertionError(f"refused for a reason other than the period: {msg}")

    def assert_open(fiscal_year, fiscal_period, action="run"):
        record["checked"].append((fiscal_year, fiscal_period, action))
        if not period_open:
            raise Refused(f"Cannot {action}: period closed")

    def assert_open_between(start_date, end_date=None, action="run", end_exclusive=False):
        record["checked"].append((start_date, end_date, action))
        if not period_open:
            raise Refused(f"Cannot {action}: period closed")

    def request_governed_rebuild(doc, event, scope=None):
        record["builds"].append(scope)
        return "BA-TEST"

    class Document:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

        def __getattr__(self, name):   # an unset field reads as None, as in Frappe
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, name, default=None):
            return self.__dict__.get(name, default)

    mods = {name: types.ModuleType(name) for name in (
        "frappe", "frappe.model", "frappe.model.document", "frappe.model.workflow", "frappe.utils",
        "konsol", "konsol.clickhouse", "konsol.period_status", "konsol.schema_lifecycle",
        "konsol.epm", "konsol.epm.budget_grain")}
    frappe = mods["frappe"]
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.session = types.SimpleNamespace(user="approver@example.com")
    frappe.db = types.SimpleNamespace(after_commit=types.SimpleNamespace(add=lambda fn: None, _functions=[]))
    mods["frappe.model.document"].Document = Document
    mods["frappe.model.workflow"].get_workflow_name = lambda doctype: None
    mods["frappe.utils"].cint = lambda v: int(v or 0)
    mods["frappe.utils"].now_datetime = lambda: "NOW"
    mods["frappe.utils"].getdate = lambda v=None: v
    mods["konsol.clickhouse"].sync_doctype = lambda *a: None
    mods["konsol.clickhouse"].sync_doctype_after_commit = lambda *a: None
    mods["konsol.clickhouse"].execute = lambda *a, **k: None
    mods["konsol.period_status"].assert_open = assert_open
    mods["konsol.period_status"].assert_open_between = assert_open_between
    mods["konsol.period_status"].first_period_affected = lambda d: d
    mods["konsol.schema_lifecycle"].request_governed_rebuild = request_governed_rebuild
    mods["konsol.epm.budget_grain"].digest_name = lambda *a, **k: "ZZ"

    saved = {name: sys.modules.get(name) for name in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("controller_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old
    return module, record


def _doc(module, doctype, neutralise_helpers=False):
    """A draft in FY2099 P12. With ``neutralise_helpers`` the controller's own
    private checks (entity access, node lookups, overlaps) become no-ops, so
    only the hook bodies run: what remains to refuse is the period."""
    cls = getattr(module, doctype.replace(" ", ""))
    d = cls(doctype=doctype, name="ZZ-TEST", docstatus=0, fiscal_year=2099, fiscal_period=12,
            status="Draft", batch_id="zz", data_area_id="ZZOP")
    if neutralise_helpers:
        for attr, value in vars(cls).items():
            if attr.startswith("_") and not attr.startswith("__") and callable(value):
                setattr(d, attr, lambda *a, **k: None)
    return d


def _submit_hooks(d):
    """Run the controller hooks Frappe's submit runs, in its order."""
    for hook in ("validate", "before_submit"):
        fn = getattr(type(d), hook, None)
        if fn is not None:
            fn(d)


def test_the_home_blocks_exactly_the_submits_the_server_refuses():
    """Every doctype the home offers a submit for, run through validate and
    before_submit in a closed period: the set that refuses is the home's list."""
    paths = _controller_paths()
    offered = {doctype for doctype, ptype, _ in _home_actions() if ptype == "submit"}
    assert set(M.SUBMIT_NEEDS_OPEN_PERIOD) <= offered, offered
    refuses = set()
    for doctype in sorted(offered):
        module, record = _load(paths[doctype], period_open=False)
        d = _doc(module, doctype, neutralise_helpers=True)
        try:
            _submit_hooks(d)
        except Refused:
            refuses.add(doctype)
    assert refuses == set(M.SUBMIT_NEEDS_OPEN_PERIOD), (
        f"server-only: {refuses - M.SUBMIT_NEEDS_OPEN_PERIOD}, home-only: {M.SUBMIT_NEEDS_OPEN_PERIOD - refuses}")


def test_submit_into_a_closed_period_is_refused_and_changes_nothing():
    """Refused before anything is set and before a build is requested."""
    paths = _controller_paths()
    for doctype in GATED_IN_BEFORE_SUBMIT:
        module, record = _load(paths[doctype], period_open=False)
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
    paths = _controller_paths()
    for doctype in GATED_IN_BEFORE_SUBMIT:
        module, record = _load(paths[doctype], period_open=True)
        d = _doc(module, doctype)
        d.before_submit()
        assert [c[:2] for c in record["checked"]] == [(2099, 12)], doctype
        assert record["checked"][0][2].split()[0] in ("approve", "submit"), record["checked"]
    module, _ = _load(paths["Consolidation Adjustment"], period_open=True)
    d = _doc(module, "Consolidation Adjustment")
    d.before_submit()
    assert (d.status, d.approved_by) == ("Approved", "approver@example.com")
    module, record = _load(paths["Allocation Run"], period_open=True)
    d = _doc(module, "Allocation Run")
    d.before_submit()
    assert (d.status, d.build_approval, record["builds"]) == ("Active", "BA-TEST", ["consolidation"])


# --- what the home shows in a closed period ---------------------------------

def test_closed_period_blocks_what_the_server_refuses_and_notes_what_it_allows():
    # a trial balance takes no save in a closed period: nothing left to do there
    assert M.closed_period("Trial Balance Submission", CLOSED) == {"blocked": CLOSED, "note": None}
    # the form still opens for review, reject, edit or delete: enabled, with a note
    assert M.closed_period("Consolidation Adjustment", CLOSED, "approve") == {
        "blocked": None, "note": "Can't approve: Dec 2099 is closed."}
    assert M.closed_period("IC Balance", CLOSED) == {"blocked": None, "note": "Can't submit: Dec 2099 is closed."}
    # open period, or a doctype whose submit the server takes: nothing to say
    assert M.closed_period("IC Balance", None) == {"blocked": None, "note": None}
    assert M.closed_period("Ownership Period", CLOSED) == {"blocked": None, "note": None}
    assert M.SAVE_NEEDS_OPEN_PERIOD <= M.SUBMIT_NEEDS_OPEN_PERIOD


def test_every_home_link_to_a_gated_doctype_says_what_a_closed_period_does():
    """Each submit or edit link to a period-gated doctype takes its blocked /
    note from M.closed_period, and nothing else is blocked on the period."""
    seen = set()
    for doctype, ptype, call in _home_actions():
        gate = [k.value for k in call.keywords if k.arg is None
                and isinstance(k.value, ast.Call) and ast.unparse(k.value.func) == "M.closed_period"]
        assert not any(k.arg in ("blocked", "note") for k in call.keywords), ast.unparse(call)
        if doctype in M.SUBMIT_NEEDS_OPEN_PERIOD and ptype in ("submit", "write"):
            assert len(gate) == 1, ast.unparse(call)
            assert ast.unparse(gate[0].args[0]) == repr(doctype) and ast.unparse(gate[0].args[1]) == "closed", ast.unparse(call)
            seen.add(doctype)
        else:
            assert not gate, ast.unparse(call)
    assert seen == set(M.SUBMIT_NEEDS_OPEN_PERIOD), seen
