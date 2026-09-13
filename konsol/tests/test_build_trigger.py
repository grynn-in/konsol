"""The build trigger runs as a job after the commit (konsol#126).

on_consolidation_doc_update inserts a Build Approval and commits. Wired straight
to the document hooks, that commit landed mid-transaction. On submit, Frappe
runs on_update before on_submit, so docstatus=1 was committed before the
controller's on_submit synced to ClickHouse, and a failed sync left the document
Submitted with nothing behind it. The hooks now call queue_consolidation_build,
which enqueues a job after the commit. These tests run the real function against
a stub frappe rather than grepping it: 814 static tests once passed while every
Entity save raised TypeError.
"""
import ast
import os
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TASKS = os.path.join(APP_DIR, "tasks.py")
HOOKS = os.path.join(APP_DIR, "hooks.py")

# frappe.enqueue's own parameters in Frappe v15 (frappe/utils/background_jobs.py)
ENQUEUE_PARAMS = {"async", "method", "queue", "timeout", "event", "is_async", "job_name", "now",
                  "enqueue_after_commit", "on_success", "on_failure", "at_front", "job_id", "deduplicate"}
FLAGS = ("in_install", "in_migrate", "in_patch", "in_import")


def _fn(name):
    with open(TASKS) as f:
        tree = ast.parse(f.read())
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _run(method="on_submit", submittable=True, flags=(), raises=False):
    calls, logs = [], []

    # Frappe v15's real signature, so a job kwarg that reuses one of enqueue's
    # own names raises the same TypeError it raised live (#110).
    def enqueue(method, queue="default", timeout=None, event=None, is_async=True, job_name=None,
                now=False, enqueue_after_commit=False, *, on_success=None, on_failure=None,
                at_front=False, job_id=None, deduplicate=False, **kwargs):
        if raises:
            raise ConnectionError("redis down")
        calls.append(((method,), dict(enqueue_after_commit=enqueue_after_commit, job_id=job_id,
                                      deduplicate=deduplicate, **kwargs)))

    state = types.SimpleNamespace(**{f: f in flags for f in FLAGS})
    ns = {"frappe": types.SimpleNamespace(flags=state, enqueue=enqueue,
                                          log_error=lambda **k: logs.append(k))}
    exec(compile(ast.Module(body=[_fn("queue_consolidation_build")], type_ignores=[]), TASKS, "exec"), ns)
    doc = types.SimpleNamespace(doctype="Ownership Period", name="OP-1",
                                meta=types.SimpleNamespace(is_submittable=submittable))
    ns["queue_consolidation_build"](doc, method)
    return calls, logs


def _doc_events():
    with open(HOOKS) as f:
        tree = ast.parse(f.read())
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "doc_events" for t in n.targets))
    return {ast.literal_eval(k): ast.literal_eval(v)
            for k, v in zip(node.value.value.keys, node.value.value.values)}


def test_the_hooks_call_the_queue_never_the_committing_trigger():
    events = _doc_events()
    assert set(events) == {"on_update", "on_submit", "on_cancel"}
    assert set(events.values()) == {"konsol.tasks.queue_consolidation_build"}, events


def test_submit_and_cancel_queue_one_job_after_the_commit():
    for method in ("on_submit", "on_cancel"):
        calls, logs = _run(method)
        assert len(calls) == 1 and not logs
        args, kw = calls[0]
        assert args[0] == "konsol.tasks.request_consolidation_build"
        assert kw["enqueue_after_commit"] is True
        assert kw["deduplicate"] is True and kw.get("job_id"), "deduplicate needs a job_id"
        assert kw["trigger_method"] == method


def test_a_submittable_doctypes_on_update_queues_nothing():
    """Drafts never reach the warehouse, and on submit on_update fires as well
    as on_submit. A non-submittable doctype's save is the signal."""
    assert _run("on_update", submittable=True)[0] == []
    assert len(_run("on_update", submittable=False)[0]) == 1


def test_nothing_is_queued_during_install_migrate_patch_or_import():
    for flag in FLAGS:
        assert _run(flags=[flag])[0] == [], flag


def test_a_queue_outage_logs_instead_of_failing_the_save():
    calls, logs = _run(raises=True)
    assert calls == [] and len(logs) == 1


def test_job_kwargs_do_not_collide_with_enqueue_and_match_the_job():
    calls, logs = _run()
    assert calls and not logs, "a collision raises TypeError in the real-signature stub"
    _, kw = calls[0]
    job_args = set(kw) - {"enqueue_after_commit", "job_id", "deduplicate"}
    target = _fn("request_consolidation_build")
    assert {a.arg for a in target.args.args} == job_args


def test_only_the_job_calls_the_committing_trigger():
    """Enumerated across every module of the app. A call, a bare reference
    (after_commit.add(fn), a dict of handlers), or the dotted-path string (a
    hook, enqueue, get_attr) outside request_consolidation_build would bring
    the mid-transaction commit back."""
    name, dotted = "on_consolidation_doc_update", "konsol.tasks.on_consolidation_doc_update"
    offenders = []
    for root, _, files in os.walk(APP_DIR):
        if "/tests" in root:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, APP_DIR)
            with open(path) as f:
                tree = ast.parse(f.read())
            allowed = set()
            for func in ast.walk(tree):
                if isinstance(func, ast.FunctionDef) and rel == "tasks.py" and func.name == "request_consolidation_build":
                    allowed |= {id(n) for n in ast.walk(func)}
            for n in ast.walk(tree):
                ref = (isinstance(n, ast.Name) and n.id == name) or (isinstance(n, ast.Attribute) and n.attr == name)
                if ref and id(n) not in allowed:
                    offenders.append(f"{rel}:{n.lineno} reference")
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and dotted in n.value:
                    offenders.append(f"{rel}:{n.lineno} string")
    assert not offenders, offenders


def test_the_approved_build_is_enqueued_after_commit():
    """BuildApproval._enqueue_build ran inside the transaction that inserted
    the row; a worker could start it before the commit and leave the row
    Approved forever (#125). Queued after commit, it cannot."""
    path = os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.py")
    with open(path) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_enqueue_build")
    call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "enqueue")
    kw = {k.arg: k.value for k in call.keywords}
    assert "enqueue_after_commit" in kw and ast.literal_eval(kw["enqueue_after_commit"]) is True


def _creates_build_approval(call):
    """new_doc("Build Approval") or get_doc({"doctype": "Build Approval", ...}).
    Loading one by name (run_governed_build, inside its build job) is not
    creating one."""
    name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
    if name == "new_doc" and call.args and isinstance(call.args[0], ast.Constant):
        return call.args[0].value == "Build Approval"
    if name == "get_doc" and call.args and isinstance(call.args[0], ast.Dict):
        return any(isinstance(k, ast.Constant) and k.value == "doctype"
                   and isinstance(v, ast.Constant) and v.value == "Build Approval"
                   for k, v in zip(call.args[0].keys, call.args[0].values))
    return False


def test_no_build_request_commits_inside_its_callers_transaction():
    """Every function that creates a Build Approval, enumerated across the app,
    must leave the commit to its caller. Only request_build_for_scope may
    commit: it runs only inside jobs (#126, #129). _request_governed_build
    committed from AllocationRun.before_submit, publish and after_delete, and
    a failed submit left an orphaned approval (#130)."""
    offenders = []
    for root, _, files in os.walk(APP_DIR):
        if "/tests" in root:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            with open(path) as f:
                tree = ast.parse(f.read())
            for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
                # Exempt, with reason: on_consolidation_doc_update runs inside its
                # own job (#126); a @frappe.whitelist() endpoint (control_api's
                # start_process) is a top-level request with no caller transaction.
                if func.name == "request_build_for_scope" or any(
                        "whitelist" in ast.unparse(d) for d in func.decorator_list):
                    continue
                creates = any(_creates_build_approval(c) for c in ast.walk(func) if isinstance(c, ast.Call))
                commits = any(isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "commit"
                              for c in ast.walk(func))
                if creates and commits:
                    offenders.append(f"{os.path.relpath(path, APP_DIR)}:{func.name}")
    assert not offenders, offenders


def test_the_governed_build_debounce_is_serialised():
    path = os.path.join(APP_DIR, "schema_lifecycle.py")
    with open(path) as f:
        src = f.read()
    body = src.split("def _request_governed_build")[1].split("\ndef ")[0]
    _assert_locked_debounce(body)


def _assert_locked_debounce(body):
    """The build lock comes first, and the pending-approval check is ITSELF a
    locking read. A plain read after the lock still sees the old snapshot
    under REPEATABLE READ (#133 review)."""
    build_lock = body.index("lock_build_requests()")
    check = body.index("FROM `tabBuild Approval`")
    assert build_lock < check
    assert "FOR UPDATE" in body[check:check + 300], "the pending-approval read must lock"


def test_both_build_debounces_use_a_locking_read():
    with open(TASKS) as f:
        src = f.read()
    _assert_locked_debounce(src.split("def request_build_for_scope")[1].split("\ndef ")[0])


def test_the_publish_build_is_requested_after_the_ddl():
    """Its row locks must not be held across ClickHouse ALTERs (#133 re-review)."""
    with open(os.path.join(APP_DIR, "schema_lifecycle.py")) as f:
        body = f.read().split("def apply_and_rebuild")[1].split("\ndef ")[0]
    code = "\n".join(l for l in body.splitlines() if not l.strip().startswith("#"))
    assert code.index("apply_schema_for_publish()") < code.index("_request_governed_build(")


def test_the_debounce_column_is_indexed_on_fresh_and_existing_sites():
    """Unindexed, FOR UPDATE scanned and locked every Build Approval row. The
    DocType covers fresh installs (patches never run there); the patch covers
    existing sites."""
    import json
    meta = os.path.join(APP_DIR, "pipeline", "doctype", "build_approval", "build_approval.json")
    field = next(f for f in json.load(open(meta))["fields"] if f["fieldname"] == "build_scope")
    assert field.get("search_index") == 1
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        assert "konsol.patches.add_build_approval_scope_index" in f.read()


def test_every_cli_write_endpoint_is_post_only():
    """Enumerated from cli_api.py. A GET request is rolled back at the end, so
    a write over GET silently vanished. Only the named read endpoints may take
    GET; every new endpoint is POST-only until added to that list."""
    with open(os.path.join(APP_DIR, "cli_api.py")) as f:
        tree = ast.parse(f.read())
    # Explicit, not by prefix: a future write named get_... must not slip through.
    reads = {"list_dimensions_api", "get_dimension_api", "list_measures_api", "get_measure_api",
             "get_schema_status_api", "list_fact_tables_api", "get_fact_table_api",
             "list_connectors_api", "get_connector_api", "test_connector_extract_api",
             "test_connector_writeback_api", "list_erp_sources_api", "export_config_api",
             "diff_config_api"}
    offenders = []
    for fn in [n for n in tree.body if isinstance(n, ast.FunctionDef)]:
        deco = [ast.unparse(d) for d in fn.decorator_list if "whitelist" in ast.unparse(d)]
        if not deco or fn.name in reads:
            continue
        if deco[0] != "frappe.whitelist(methods=['POST'])":
            offenders.append(f"{fn.name}: {deco[0]}")
    assert not offenders, offenders


def test_the_build_lock_is_one_row_that_always_exists():
    """Per scope, "full" (which may not have a Build Scope row) locked only a
    gap, and two full requests deadlocked (1213, live). Every Build Scope row
    in name order could deadlock with the migrate's fixture re-import, which
    rewrites them in file order (#133 re-review). One row that always exists
    has neither problem."""
    with open(os.path.join(APP_DIR, "build_lock.py")) as f:
        src = f.read()
    sql = src.split("frappe.db.sql(")[1].split(")")[0]
    assert "FROM `tabDocType` WHERE name = 'Build Approval'" in sql and "FOR UPDATE" in sql
    assert "Build Scope`" not in sql

