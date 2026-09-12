"""Submit only into an open period (#149).

A closed period takes no further change (decided 12 Sep 2026): after close, a
correction is a new document in an open period. Consolidation Adjustment, IC
Balance and Allocation Run gated only their cancel, so a draft whose period
closed while it waited could still be submitted into it.

The home disables only what the server refuses and annotates what it allows
but can't complete, so its two lists (submit needs an open period, every save
does) are checked here against the controllers themselves, run against a
closed period."""
import ast
import glob
import importlib.util
import json
import os
import sys
import tempfile
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATED_IN_BEFORE_SUBMIT = ("Consolidation Adjustment", "IC Balance", "Allocation Run")
PERIOD_GATES = {"assert_open", "assert_open_between"}
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


PATHS = _controller_paths()


def _home_api_tree():
    with open(os.path.join(APP_DIR, "home_api.py")) as f:
        return ast.parse(f.read())


def _home_actions(tree=None):
    """(doctype, ptype, call node) for every _action call in home_api."""
    for c in ast.walk(tree or _home_api_tree()):
        if (isinstance(c, ast.Call) and getattr(c.func, "id", None) == "_action" and len(c.args) >= 3
                and all(isinstance(a, ast.Constant) for a in c.args[1:3])):
            yield c.args[1].value, c.args[2].value, c


# --- which private methods may be skipped -----------------------------------

def _called_names(fn):
    """Names a function calls: bare names, ``self.<method>`` as "self.<method>",
    and the attribute of any other call (``period_status.assert_open``)."""
    names = set()
    for c in ast.walk(fn):
        if isinstance(c, ast.Call):
            f = c.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f"self.{f.attr}" if ast.unparse(f.value) == "self" else f.attr)
    return names


def _methods_reaching_a_gate(path, class_name):
    """Methods of the class that call a period gate, directly, through a
    module-level helper, or through another of its own methods."""
    with open(path) as f:
        tree = ast.parse(f.read())
    # A gate imported under another name (`import assert_open as ao`) is a
    # gate. Known limits: a gate in an inherited method, and a gate reached
    # through a helper imported from another konsol module, are not seen.
    gates = set(PERIOD_GATES) | {
        alias.asname for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        for alias in node.names if alias.name in PERIOD_GATES and alias.asname}
    funcs ={n.name: _called_names(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
    grew = True
    while grew:
        grew = False
        for name, calls in funcs.items():
            if name not in gates and calls & gates:
                gates.add(name)
                grew = True
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    methods = {n.name: _called_names(n) for n in cls.body if isinstance(n, ast.FunctionDef)}
    reaching = {m for m, calls in methods.items() if calls & gates}
    grew = True
    while grew:
        grew = False
        for m, calls in methods.items():
            if m not in reaching and any(f"self.{r}" in calls for r in reaching):
                reaching.add(m)
                grew = True
    return reaching


# --- the hooks, run against a stub frappe -----------------------------------

class Refused(Exception):
    """A period gate refused."""


class ReachedDatabase(Exception):
    """The hook got to the database without a period gate refusing first."""


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

    def database(name):
        def reached(*args, **kwargs):
            raise ReachedDatabase(f"frappe.{name}")
        return reached

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
    frappe.db = types.SimpleNamespace(
        after_commit=types.SimpleNamespace(add=lambda fn: None, _functions=[]),
        **{name: database(f"db.{name}") for name in ("sql", "get_value", "get_all", "exists", "count")})
    frappe.get_all = database("get_all")
    frappe.get_list = database("get_list")
    frappe.get_doc = database("get_doc")
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
    only the hook bodies run, except a helper that reaches a period gate:
    that one keeps running, so a gate moved into a helper still counts."""
    class_name = doctype.replace(" ", "")
    cls = getattr(module, class_name)
    d = cls(doctype=doctype, name="ZZ-TEST", docstatus=0, fiscal_year=2099, fiscal_period=12,
            status="Draft", batch_id="zz", data_area_id="ZZOP", ownership_pct=50,
            effective_date="2099-12-01", rate_date="2099-12-01", rate=1)
    if neutralise_helpers:
        keep = _methods_reaching_a_gate(PATHS[doctype], class_name)
        for attr, value in vars(cls).items():
            if (attr.startswith("_") and not attr.startswith("__") and callable(value)
                    and attr not in keep):
                setattr(d, attr, lambda *a, **k: None)
    return d


def _refuses_in_a_closed_period(doctype, hooks):
    """(refused, why not): run ``hooks`` in order on a closed-period draft."""
    module, _ = _load(PATHS[doctype], period_open=False)
    d = _doc(module, doctype, neutralise_helpers=True)
    try:
        for hook in hooks:
            fn = getattr(type(d), hook, None)
            if fn is not None:
                fn(d)
    except Refused:
        return True, None
    except ReachedDatabase as e:
        return False, f"{doctype}: {'/'.join(hooks)} reached {e} before any period gate refused"
    return False, f"{doctype}: {'/'.join(hooks)} ran to the end in a closed period"


def _compare(refusing, expected, why_not, what):
    assert refusing == set(expected), (
        f"{what}: server-only {sorted(refusing - expected)}, home-only {sorted(expected - refusing)}; "
        + "; ".join(w for w in why_not if w))


def test_the_home_blocks_exactly_the_submits_the_server_refuses():
    """Every doctype the home offers a submit for, run through validate and
    before_submit (Frappe's submit order) in a closed period: the set that
    refuses is SUBMIT_NEEDS_OPEN_PERIOD."""
    offered = {doctype for doctype, ptype, _ in _home_actions() if ptype == "submit"}
    assert set(M.SUBMIT_NEEDS_OPEN_PERIOD) <= offered, offered
    results = {dt: _refuses_in_a_closed_period(dt, ("validate", "before_submit")) for dt in sorted(offered)}
    _compare({dt for dt, (r, _) in results.items() if r}, M.SUBMIT_NEEDS_OPEN_PERIOD,
             [w for _, w in results.values()], "submit refused in a closed period")


def test_the_home_knows_exactly_which_saves_the_server_refuses():
    """validate alone, in a closed period: the doctypes that refuse every save
    are SAVE_NEEDS_OPEN_PERIOD, and the others still take a save there."""
    results = {dt: _refuses_in_a_closed_period(dt, ("validate",)) for dt in sorted(M.SUBMIT_NEEDS_OPEN_PERIOD)}
    _compare({dt for dt, (r, _) in results.items() if r}, M.SAVE_NEEDS_OPEN_PERIOD,
             [w for _, w in results.values()], "save refused in a closed period")


def test_a_helper_that_reaches_a_gate_is_never_skipped():
    src = (
        "from konsol.period_status import assert_open\n"
        "from konsol.period_status import assert_open_between as aob\n"
        "def _module_gate(doc):\n    assert_open(doc.fiscal_year, doc.fiscal_period)\n"
        "class X:\n"
        "    def validate(self):\n        self._a()\n"
        "    def _a(self):\n        self._b()\n"
        "    def _b(self):\n        _module_gate(self)\n"
        "    def _c(self):\n        pass\n"
        "    def _d(self):\n        period_status.assert_open_between(self.x)\n"
        "    def _e(self):\n        aob(self.x)\n")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "gate_probe.py")
        with open(path, "w") as f:
            f.write(src)
        assert _methods_reaching_a_gate(path, "X") == {"validate", "_a", "_b", "_d", "_e"}


def test_submit_into_a_closed_period_is_refused_and_changes_nothing():
    """Refused before anything is set and before a build is requested."""
    for doctype in GATED_IN_BEFORE_SUBMIT:
        module, record = _load(PATHS[doctype], period_open=False)
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
        module, record = _load(PATHS[doctype], period_open=True)
        d = _doc(module, doctype)
        d.before_submit()
        assert [c[:2] for c in record["checked"]] == [(2099, 12)], doctype
        assert record["checked"][0][2].split()[0] in ("approve", "submit"), record["checked"]
    module, _ = _load(PATHS["Consolidation Adjustment"], period_open=True)
    d = _doc(module, "Consolidation Adjustment")
    d.before_submit()
    assert (d.status, d.approved_by) == ("Approved", "approver@example.com")
    module, record = _load(PATHS["Allocation Run"], period_open=True)
    d = _doc(module, "Allocation Run")
    d.before_submit()
    assert (d.status, d.build_approval, record["builds"]) == ("Active", "BA-TEST", ["consolidation"])


# --- what the home shows in a closed period ---------------------------------

def test_closed_period_notes_what_the_server_allows_but_cannot_complete():
    # every link opens a form the server still takes something on: none is blocked
    assert M.closed_period("Consolidation Adjustment", CLOSED, "approve") == {
        "note": "Can't approve: Dec 2099 is closed."}
    assert M.closed_period("Consolidation Adjustment", CLOSED, "be approved") == {
        "note": "Can't be approved: Dec 2099 is closed."}
    assert M.closed_period("IC Balance", CLOSED) == {"note": "Can't submit: Dec 2099 is closed."}
    # a trial balance draft takes no save there, only a delete: the note says
    # so to a viewer who may delete, and says who may to one who can't
    assert M.closed_period("Trial Balance Submission", CLOSED, can_delete=True) == {
        "note": "Can't submit: Dec 2099 is closed. Delete the draft if it isn't needed."}
    assert M.closed_period("Trial Balance Submission", CLOSED, can_delete=False) == {
        "note": "Can't submit: Dec 2099 is closed. Ask an EPM Admin to delete the draft if it isn't needed."}
    assert M.closed_period("Trial Balance Submission", CLOSED) == M.closed_period(
        "Trial Balance Submission", CLOSED, can_delete=False)
    assert M.closed_period("Trial Balance Submission", None, can_delete=True) == {"note": None}
    # the delete right only changes the trial balance note
    assert M.closed_period("IC Balance", CLOSED, can_delete=True) == M.closed_period("IC Balance", CLOSED)
    # open period, or a doctype whose submit the server takes: nothing to say
    assert M.closed_period("IC Balance", None) == {"note": None}
    assert M.closed_period("Ownership Period", CLOSED) == {"note": None}
    assert M.SAVE_NEEDS_OPEN_PERIOD <= M.SUBMIT_NEEDS_OPEN_PERIOD


def test_every_home_link_to_a_gated_doctype_says_what_a_closed_period_does():
    """Each submit or edit link to a period-gated doctype takes its note from
    M.closed_period, and no queue link is blocked on the period."""
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


def test_the_trial_balance_note_rests_on_who_may_delete_a_draft():
    """The closed-period note sends an Entity Accountant (the only role that
    gets trial balance rows) to an EPM Admin, because only EPM Admin and
    System Manager may delete a draft. If these rights change, revisit
    home_model.closed_period's trial balance note."""
    with open(PATHS["Trial Balance Submission"][:-3] + ".json") as f:
        perms = {p["role"]: p for p in json.load(f)["permissions"]}
    assert perms["Entity Accountant"].get("submit") and not perms["Entity Accountant"].get("delete"), (
        "Entity Accountant's trial balance rights changed: revisit the closed-period note")
    assert perms["EPM Admin"].get("delete") and perms["System Manager"].get("delete"), (
        "EPM Admin / System Manager lost delete on Trial Balance Submission: revisit the closed-period note")


def test_the_trial_balance_link_asks_the_viewers_delete_right():
    """home_api decides can_delete with the permission helper, only in a
    closed period, and passes it to M.closed_period for the draft's link."""
    tree = _home_api_tree()
    assigns = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
               and [ast.unparse(t) for t in n.targets] == ["can_delete"]]
    assert len(assigns) == 1, [ast.unparse(a) for a in assigns]
    assert ast.unparse(assigns[0].value) == (
        "bool(closed) and _can('Trial Balance Submission', 'delete', doc=draft.name)"), ast.unparse(assigns[0].value)
    tb = [call for dt, ptype, call in _home_actions(tree) if dt == "Trial Balance Submission" and ptype == "submit"]
    assert len(tb) == 1
    gate = next(k.value for k in tb[0].keywords if k.arg is None)
    assert [(k.arg, ast.unparse(k.value)) for k in gate.keywords] == [("can_delete", "can_delete")], ast.unparse(gate)


def test_an_action_is_never_blocked_behind_the_permission_check():
    """The closed period no longer blocks any queue link, so _action has no
    `blocked` override: allowed is the user's permission, nothing else."""
    fn = next(n for n in _home_api_tree().body if isinstance(n, ast.FunctionDef) and n.name == "_action")
    params = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    assert "blocked" not in params and "blocked" not in ast.unparse(fn), params


def test_send_for_approval_says_the_draft_cannot_be_approved():
    for doctype, ptype, call in _home_actions():
        if call.args[0].value == "Send for approval":
            gate = next(k.value for k in call.keywords if k.arg is None)
            assert ast.unparse(gate.args[2]) == "'be approved'", ast.unparse(call)
            return
    raise AssertionError("no Send for approval action")


def test_the_trial_balance_upload_is_offered_only_in_an_open_period():
    """The server refuses the upload in a closed period, so the home doesn't
    offer it there: the create action sits under `period_open`."""
    tree = _home_api_tree()
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    uploads = [c for dt, ptype, c in _home_actions(tree) if dt == "Trial Balance Submission" and ptype == "create"]
    assert uploads, "no trial balance upload action"
    for call in uploads:
        node = call
        while node in parents:
            parent = parents[node]
            if isinstance(parent, ast.If) and node in parent.body:
                assert ast.unparse(parent.test) == "period_open", ast.unparse(parent.test)
                break
            node = parent
        else:
            raise AssertionError("the upload is offered unconditionally")
