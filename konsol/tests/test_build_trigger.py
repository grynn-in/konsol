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

    def enqueue(*args, **kwargs):
        if raises:
            raise ConnectionError("redis down")
        calls.append((args, kwargs))

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
    _, kw = _run()[0][0]
    job_args = {k for k in kw if k not in ENQUEUE_PARAMS}
    assert not job_args & ENQUEUE_PARAMS
    target = _fn("request_consolidation_build")
    assert {a.arg for a in target.args.args} == job_args


def test_only_the_job_calls_the_committing_trigger():
    """Enumerated across every module of the app: a second caller of
    on_consolidation_doc_update from a hook would bring the mid-transaction
    commit back."""
    callers = []
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
                for call in [n for n in ast.walk(func) if isinstance(n, ast.Call)]:
                    name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
                    if name == "on_consolidation_doc_update":
                        callers.append(f"{os.path.relpath(path, APP_DIR)}:{func.name}")
    assert callers == ["tasks.py:request_consolidation_build"], callers
