"""Who may start a close run, who may see the launch form's options
(konsol#166), and who may read the failure sample (konsol#165).

`trigger_close_run` writes: it inserts an Assertion Run, commits, and enqueues
the assertion suite on the long queue. So it is POST-only, and gated to the
roles that run the close — Close Lead (`EPM Admin`) and System Manager.

`launch_options` writes nothing, so it stays a GET, but it reads with
`frappe.get_all`, which ignores permissions: it must be gated to the roles that
launch pipelines — `EPM Admin`, `EPM Analyst` and System Manager.

`sample_rows` on an Assertion Step holds up to 20 of the rows that failed an
assertion, straight from the dbt failure table, and about 30 of those tables
carry `data_area_id` — so the sample can hold another entity's figures, on a
doctype four roles read and nothing scopes by entity. It sits at permlevel 1,
which only the group roles that run the close (`EPM Admin`, System Manager)
hold: EPM Analyst and EPM User keep the run and its counts, not the offending
rows.

Source-level, in the `test_role_access.py` style: the source is parsed with
`ast` and the doctype JSON is read as JSON, so no frappe and no site are
needed.
"""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSERTION_RUN = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.py")
ASSERTION_RUN_JSON = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.json")
ASSERTION_STEP_JSON = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_step", "assertion_step.json")
ORCHESTRATOR_API = os.path.join(APP_DIR, "orchestrator", "api.py")
SAMPLE_ROLES = {"EPM Admin", "System Manager"}


def _function(path, name):
    with open(path) as f:
        tree = ast.parse(f.read())
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert fn is not None, f"{os.path.relpath(path, APP_DIR)} has no {name}"
    return fn


def _whitelist_methods(fn):
    """The `methods=` of the function's frappe.whitelist decorator, or None."""
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "whitelist"
                and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "frappe"):
            for kw in dec.keywords:
                if kw.arg == "methods":
                    return ast.literal_eval(kw.value)
    return None


def _first_statement(fn):
    """The first real statement of the body — the docstring and the lazy
    `import frappe` some modules open with (they must import on the host, with
    no bench) are not statements a caller can be refused by."""
    body = fn.body
    if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    while body and isinstance(body[0], (ast.Import, ast.ImportFrom)):
        body = body[1:]
    assert body, f"{fn.name} has no body"
    return body[0]


def _only_for_roles(stmt):
    """The roles named by a `frappe.only_for(...)` statement, else None."""
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return None
    call = stmt.value
    if not (isinstance(call.func, ast.Attribute) and call.func.attr == "only_for"
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "frappe"):
        return None
    (arg,) = call.args
    roles = ast.literal_eval(arg)
    return {roles} if isinstance(roles, str) else set(roles)


def test_trigger_close_run_is_post_only():
    """It inserts a run, commits and enqueues the suite, so a GET must never
    reach it."""
    methods = _whitelist_methods(_function(ASSERTION_RUN, "trigger_close_run"))
    assert methods == ["POST"], f'trigger_close_run is not @frappe.whitelist(methods=["POST"]): {methods}'


def test_only_the_close_roles_may_start_a_close_run():
    """The gate is the first statement, ahead of the "already in progress"
    check, so a user without the role is refused whatever the run state.

    R3 (konsol#297): the Analyst runs checks too, so it joins the Close Lead
    and System Manager here."""
    roles = _only_for_roles(_first_statement(_function(ASSERTION_RUN, "trigger_close_run")))
    assert roles == {"EPM Admin", "EPM Analyst", "System Manager"}, (
        f"trigger_close_run does not open with frappe.only_for of the close roles: {roles}")


def test_the_analyst_may_create_but_never_write_a_run():
    """R3 (konsol#297): the Analyst runs checks, but the sign-off is still the
    Close Lead's. The JSON grants create with no write on the level-0 row, and
    `sign_off_close`'s first statement enforces write on the caller, so a
    create-only Analyst is refused there whatever else changes."""
    meta = _meta(ASSERTION_RUN_JSON)
    rows = [p for p in meta["permissions"] if p.get("role") == "EPM Analyst" and not p.get("permlevel")]
    assert rows, "no level-0 EPM Analyst permission row on Assertion Run"
    assert all(p.get("create") for p in rows), f"EPM Analyst has no create on Assertion Run: {rows}"
    assert not any(p.get("write") for p in rows), f"EPM Analyst gained write on Assertion Run: {rows}"

    stmt = _first_statement(_function(ASSERTION_RUN, "sign_off_close"))
    assert isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call), (
        f"sign_off_close does not open with a call: {ast.dump(stmt)}")
    call = stmt.value
    assert (isinstance(call.func, ast.Attribute) and call.func.attr == "has_permission"
            and isinstance(call.func.value, ast.Name) and call.func.value.id == "frappe"), (
        f"sign_off_close does not open with frappe.has_permission: {ast.dump(stmt)}")
    positional = [ast.literal_eval(a) for a in call.args]
    assert positional[:2] == ["Assertion Run", "write"], (
        f"sign_off_close's has_permission does not check write on Assertion Run: {positional}")


def test_only_the_launch_roles_may_read_the_launch_options():
    """`launch_options` reads every Pipeline and the declared calendar with
    `frappe.get_all`, which ignores permissions, so the gate is the function's
    own first statement."""
    roles = _only_for_roles(_first_statement(_function(ORCHESTRATOR_API, "launch_options")))
    assert roles == {"EPM Admin", "EPM Analyst", "System Manager"}, (
        f"launch_options does not open with frappe.only_for of the launch roles: {roles}")


def _meta(path):
    with open(path) as f:
        return json.load(f)


def _permlevel_rows(meta):
    return [p for p in meta.get("permissions", []) if p.get("permlevel")]


def test_the_failure_sample_is_the_only_field_above_permlevel_0():
    """`sample_rows` alone is raised: the run's own fields and the rest of the
    step — assertion, status, counts, message — stay at 0, so every role that
    reads the run keeps them."""
    levels = {f["fieldname"]: f.get("permlevel", 0) for f in _meta(ASSERTION_STEP_JSON)["fields"]}
    assert levels.get("sample_rows") == 1, (
        f"sample_rows is not at permlevel 1: {levels.get('sample_rows')}")
    raised = {name: lvl for name, lvl in levels.items() if name != "sample_rows" and lvl}
    assert not raised, f"fields other than sample_rows were raised above permlevel 0: {raised}"
    run_raised = {f["fieldname"]: f.get("permlevel", 0) for f in _meta(ASSERTION_RUN_JSON)["fields"]
                  if f.get("permlevel")}
    assert not run_raised, f"Assertion Run fields were raised above permlevel 0: {run_raised}"


def test_only_the_close_roles_read_the_failure_sample():
    """The sample can hold another entity's rows, so permlevel 1 is granted to
    the close roles only — EPM Analyst and EPM User get no row at all, which is
    how frappe denies them the field."""
    meta = _meta(ASSERTION_RUN_JSON)
    rows = _permlevel_rows(meta)
    assert {p["role"] for p in rows} == SAMPLE_ROLES, (
        f"permlevel-1 rows are not exactly the close roles: {[p.get('role') for p in rows]}")
    assert all(p.get("permlevel") == 1 for p in rows), f"a permlevel row is not at level 1: {rows}"
    assert all(p.get("read") for p in rows), f"a close role cannot read the sample: {rows}"
    # write at level 1 follows the role's own level-0 write, so no role gains
    # an edit it does not already have on the run
    level_0 = {p["role"]: p for p in meta["permissions"] if not p.get("permlevel")}
    for p in rows:
        assert bool(p.get("write")) == bool(level_0[p["role"]].get("write")), (
            f"{p['role']} permlevel-1 write does not match its level-0 write: {p}")


# ---------------------------------------------------------------------------
# A02b (konsol#305): with R3 the Analyst may CREATE an Assertion Run. Measured
# live 25 Sep 2026: an EPM Analyst inserted a run carrying status "Green" and
# signoff_status "Signed Off" and it saved as sent (read_only is a form
# property, not an insert guard). A new run must always start Queued and
# unsigned, whatever the request carries.
# ---------------------------------------------------------------------------
def _before_insert_runner():
    """Compile AssertionRun.before_insert (and the module constant it reads)
    against a stub frappe, so the reset is exercised, not just grepped."""
    import types

    path = os.path.join(APP_DIR, "consolidation/doctype/assertion_run/assertion_run.py")
    with open(path) as f:
        tree = ast.parse(f.read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "AssertionRun")
    fn = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "before_insert"), None)
    assert fn is not None, "AssertionRun has no before_insert"
    consts = [n for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == "NEW_RUN_BLANK_FIELDS" for t in n.targets)]
    module = ast.Module(body=consts + [fn], type_ignores=[])
    ns = {"frappe": types.SimpleNamespace(session=types.SimpleNamespace(user="caller@example.com"))}
    exec(compile(ast.fix_missing_locations(module), path, "exec"), ns)
    return ns["before_insert"]


class _Doc:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def set(self, key, value):
        setattr(self, key, value)


def test_a_new_run_cannot_arrive_signed_or_scored():
    before_insert = _before_insert_runner()
    forged = _Doc(flags=__import__("types").SimpleNamespace(started_by_trigger=True),
        status="Green", signoff_status="Signed Off", signed_off_by="x@example.com",
        signed_off_at="2026-09-25 10:00:00", override_reason="r", acknowledgement="a",
        warnings_at_signoff="w", total=127, passed=127, failed=0, errored=0, warned=0,
        started_at="t", completed_at="t", duration_seconds=1.0, log="l",
        triggered_by="someone-else@example.com", results=[{"status": "Pass"}],
        fiscal_year=2010, fiscal_period=2, title="kept",
    )
    before_insert(forged)
    assert forged.status == "Queued"
    assert forged.signoff_status == "Not Signed Off"
    for field in ("signed_off_by", "signed_off_at", "override_reason", "acknowledgement",
                  "warnings_at_signoff", "total", "passed", "failed", "errored", "warned",
                  "started_at", "completed_at", "duration_seconds", "log"):
        assert getattr(forged, field) in (None, 0, ""), field
    assert forged.results == []
    assert forged.triggered_by == "caller@example.com"
    # what the caller legitimately chooses survives
    assert (forged.fiscal_year, forged.fiscal_period, forged.title) == (2010, 2, "kept")


def test_every_read_only_result_field_is_reset_on_insert():
    """The reset list covers every read_only field of the doctype except the
    ones the caller legitimately sets (title) and the two with fixed starting
    values (status, signoff_status); a read_only field added later must be
    added to the reset list or here, on purpose."""
    import json

    before_insert = _before_insert_runner()  # also asserts the method exists
    del before_insert
    path = os.path.join(APP_DIR, "consolidation/doctype/assertion_run/assertion_run.py")
    with open(path) as f:
        tree = ast.parse(f.read())
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "NEW_RUN_BLANK_FIELDS" for t in n.targets))
    blanked = set(ast.literal_eval(node.value))
    meta = json.load(open(os.path.join(APP_DIR, "consolidation/doctype/assertion_run/assertion_run.json")))
    read_only = {f["fieldname"] for f in meta["fields"] if f.get("read_only")
                 and f["fieldtype"] not in ("Section Break", "Column Break", "Tab Break")}
    assert read_only - {"title", "status", "signoff_status", "triggered_by"} == blanked


def test_a_run_is_only_created_through_trigger_close_run():
    """A02c: a direct insert (REST, Desk) is refused; only trigger_close_run,
    which also enqueues the suite, may create a run. Otherwise a queued run
    with no job blocks every other run until the reaper errors it."""
    before_insert = _before_insert_runner()

    class Refused(Exception):
        pass

    import types
    before_insert.__globals__["frappe"] = types.SimpleNamespace(
        session=types.SimpleNamespace(user="caller@example.com"),
        throw=lambda *a, **k: (_ for _ in ()).throw(Refused(a[0] if a else "")),
        _=lambda s: s,
    )
    direct = _Doc(flags=types.SimpleNamespace(), results=[])
    try:
        before_insert(direct)
        raise AssertionError("a direct insert was not refused")
    except Refused:
        pass
    via_trigger = _Doc(flags=types.SimpleNamespace(started_by_trigger=True), results=[])
    before_insert(via_trigger)
    assert via_trigger.status == "Queued"


def test_trigger_close_run_marks_its_insert():
    path = os.path.join(APP_DIR, "consolidation/doctype/assertion_run/assertion_run.py")
    with open(path) as f:
        src = f.read()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "trigger_close_run")
    body = ast.get_source_segment(src, fn)
    assert "doc.flags.started_by_trigger = True" in body
    assert body.index("doc.flags.started_by_trigger = True") < body.index("doc.insert(")
