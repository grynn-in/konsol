"""A change made while a build runs gets one more build (#129).

The debounce counted a Running build as pending, so a change made after the
build had read its inputs was absorbed and never reached gold. Now the request
flags the Running build, and the build requests one more when it finishes.
The concurrency (flag vs finish) is proved live; these pin the structure."""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS = os.path.join(APP_DIR, "tasks.py")


def _fn(path, name):
    with open(path) as f:
        tree = ast.parse(f.read())
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def test_both_debounces_flag_a_running_build():
    for path, name in ((TASKS, "request_build_for_scope"),
                       (os.path.join(APP_DIR, "schema_lifecycle.py"), "_request_governed_build")):
        src = ast.unparse(_fn(path, name))
        assert "SELECT name, workflow_state FROM `tabBuild Approval`" in src, name
        assert src.index("flag_running_build(existing[0])") > src.index("FOR UPDATE"), name


def test_only_an_approved_or_running_build_is_flagged_and_modified_is_not_bumped():
    path = os.path.join(APP_DIR, "build_lock.py")
    src = ast.unparse(_fn(path, "flag_running_build"))
    assert "row.get('workflow_state') in FLAGGED_STATES" in src
    with open(path) as f:
        assert 'FLAGGED_STATES = ("Approved", "Running")' in f.read()   # #140
    sql = src.split("frappe.db.sql(")[1]
    assert "rebuild_requested = 1" in sql and "modified" not in sql


def test_every_terminal_path_after_running_goes_through_the_finish():
    fn = _fn(TASKS, "run_governed_build")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)]
    saves = [c for c in calls if getattr(c.func, "attr", "") == "save"]
    finishes = [c for c in calls if getattr(c.func, "id", "") == "_finish_governed_build"]
    # the Running save, and the could-not-start save (a flag needs Running)
    assert len(saves) == 2, len(saves)
    assert len(finishes) == 1, "one finish, after the try, that every terminal path reaches once"
    assert any(isinstance(n, ast.Expr) and getattr(n.value, "func", None) is not None
               and getattr(n.value.func, "id", "") == "_finish_governed_build" for n in fn.body), (
        "the finish is a top-level statement, not inside the try (#139 review)")


def test_the_finish_rereads_the_flag_under_lock_and_requests_after_commit():
    src = ast.unparse(_fn(TASKS, "_finish_governed_build"))
    order = [src.index(s) for s in ("FOR UPDATE", "doc.save(", "frappe.db.commit()", "request_build_for_scope(")]
    assert order == sorted(order), order


def test_a_reaped_flagged_build_requests_its_follow_up_after_the_commit():
    with open(os.path.join(APP_DIR, "orchestrator", "reaper.py")) as f:
        body = f.read().split("def reap_stale_build_approvals")[1]
    assert '"rebuild_requested"' in body
    assert body.index("frappe.db.commit()") < body.index("request_build_for_scope(row")


def test_the_flag_is_a_read_only_check_on_build_approval():
    with open(os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.json")) as f:
        field = next(f for f in json.load(f)["fields"] if f["fieldname"] == "rebuild_requested")
    assert field["fieldtype"] == "Check" and field["read_only"] == 1


def test_request_build_for_scope_is_called_only_from_jobs():
    """It commits. Allowed callers all run in a job of their own."""
    allowed_callers = {"on_consolidation_doc_update", "_finish_governed_build", "reap_stale_build_approvals",
                       "follow_up_failed_starts"}
    offenders = []
    for root, _, files in os.walk(APP_DIR):
        if "/tests" in root:
            continue
        for fn in [f for f in files if f.endswith(".py")]:
            path = os.path.join(root, fn)
            with open(path) as f:
                tree = ast.parse(f.read())
            allowed = set()
            for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
                if func.name in allowed_callers:
                    allowed |= {id(n) for n in ast.walk(func)}
            for n in ast.walk(tree):
                ref = (isinstance(n, ast.Name) and n.id == "request_build_for_scope") or (
                    isinstance(n, ast.Attribute) and n.attr == "request_build_for_scope")
                text = isinstance(n, ast.Constant) and isinstance(n.value, str) and "request_build_for_scope" in n.value
                if (ref or text) and id(n) not in allowed:
                    offenders.append(f"{os.path.relpath(path, APP_DIR)}:{n.lineno}")
    assert not offenders, offenders


def test_a_save_never_clears_the_flag():
    """The flag doesn't bump modified, so a form opened before it passes the
    timestamp check; its save must not write the flag back to 0 (#139 review)."""
    path = os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.py")
    with open(path) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "before_save")
    src = ast.unparse(fn)
    assert "before = self.get_doc_before_save()" in src   # loaded FOR UPDATE by check_if_latest
    assert "if before and before.rebuild_requested and (not self.rebuild_requested) and (not starting):" in src
    assert "starting = before and before.workflow_state == 'Approved' and (self.workflow_state == 'Running')" in src
    assert src.index("self.rebuild_requested = 1") < src.index("self.rebuild_requested = 0"), (
        "keep a set flag, then clear it only on a reset to Draft")


def test_the_reaper_reads_the_flag_after_its_own_update():
    with open(os.path.join(APP_DIR, "orchestrator", "reaper.py")) as f:
        body = f.read().split("def reap_stale_build_approvals")[1]
    assert body.index("WHERE name = %s AND workflow_state = %s") < body.index('"rebuild_requested"], as_dict=True)')
    assert "if after.rebuild_requested:" in body

