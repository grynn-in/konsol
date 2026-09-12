"""Write-through syncs run after the commit, once per transaction (konsol#124).

ClickHouse has no transaction, so a sync inside a document hook is final at
once: a save that later rolled back left its row in the warehouse (measured on
#110: a rolled-back Entity insert stayed in epm_staging.entities), and the sync
read its rows through the uncommitted transaction. Hooks now queue the sync
with clickhouse.after_commit_once / sync_doctype_after_commit.
"""
import ast
import functools
import glob
import os
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLICKHOUSE = os.path.join(APP_DIR, "clickhouse.py")

HOOKS = {"validate", "before_save", "before_submit", "before_cancel", "on_update", "on_submit",
         "on_cancel", "on_trash", "after_delete", "after_insert", "after_rename", "on_update_after_submit"}
# Calls that write a write-through table right now. Passing one as an
# argument (after_commit_once(key, self._sync_tiers)) is queuing it, not calling it.
INLINE = {"sync_doctype", "sync_doctype_filtered", "sync_table", "resync_staging",
          "_sync_tiers", "_sync_hierarchy", "sync_allocation_drivers"}


def _name(call):
    return getattr(call.func, "attr", None) or getattr(call.func, "id", None)


def _controllers():
    paths = glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.py"))
    paths.append(os.path.join(APP_DIR, "governed_reference.py"))
    for path in paths:
        if path.endswith("__init__.py") or "/tests/" in path:
            continue
        with open(path) as f:
            tree = ast.parse(f.read())
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef)]:
            yield os.path.relpath(path, APP_DIR), cls


def test_there_are_write_through_controllers_to_check():
    names = {cls.name for _, cls in _controllers()}
    assert {"Scenario", "OwnershipPeriod", "ConsolidationGroup", "AllocationRule"} <= names


def _methods(cls, classes, seen=()):
    """A class's methods with the ones it inherits from app classes; its own
    win. Reporting Hierarchy inherits on_update from GovernedReferenceDocument
    and overrides the _resync it calls (#141 review)."""
    merged = {}
    for base in cls.bases:
        name = ast.unparse(base).split(".")[-1]
        if name in classes and name not in seen:
            merged.update(_methods(classes[name], classes, seen + (cls.name,)))
    merged.update({n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)})
    return merged


def test_no_document_hook_writes_clickhouse_inside_the_transaction():
    """Enumerated across every controller, inherited hooks included, following
    one level of self.<helper>()."""
    classes = {cls.name: cls for _, cls in _controllers()}
    offenders = []
    for rel, cls in _controllers():
        methods = _methods(cls, classes)
        for hook in sorted(HOOKS & set(methods)):
            for call in [n for n in ast.walk(methods[hook]) if isinstance(n, ast.Call)]:
                name = _name(call)
                if name in INLINE:
                    offenders.append(f"{rel}:{cls.name}.{hook} calls {name}")
                helper = methods.get(name)
                is_self = isinstance(call.func, ast.Attribute) and ast.unparse(call.func.value) in ("self", "type(self)")
                if helper is not None and is_self and name not in HOOKS:
                    for inner in [n for n in ast.walk(helper) if isinstance(n, ast.Call)]:
                        if _name(inner) in INLINE:
                            offenders.append(f"{rel}:{cls.name}.{hook} -> {name} calls {_name(inner)}")
    assert not offenders, offenders


def _after_commit_once(queue, flags=(), logged=None):
    """clickhouse.after_commit_once, run against a stub after-commit queue."""
    with open(CLICKHOUSE) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "after_commit_once")
    stub = types.SimpleNamespace(
        db=types.SimpleNamespace(after_commit=types.SimpleNamespace(_functions=queue, add=queue.append)),
        flags=types.SimpleNamespace(**{f: f in flags for f in ("in_install", "in_import", "in_migrate", "in_patch")}),
        log_error=lambda **kw: (logged if logged is not None else []).append(kw),
        logger=lambda: types.SimpleNamespace(exception=lambda msg: None))
    ns = {"functools": functools, "frappe": stub}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), CLICKHOUSE, "exec"), ns)
    return ns["after_commit_once"]


def test_one_sync_per_key_per_transaction_and_nothing_runs_before_the_commit():
    queue, ran = [], []
    once = _after_commit_once(queue)
    for _ in range(3):   # a bulk edit of three Scenarios
        once(("sync_doctype", "Scenario", "t1"), lambda: ran.append("scenario"))
    once(("sync_doctype", "Spread Profile", "t2"), lambda: ran.append("spread"))
    assert len(queue) == 2 and ran == [], "queued once per key, and nothing ran in the hook"
    for job in list(queue):   # the commit
        job()
    assert ran == ["scenario", "spread"]
    queue.clear()             # the next transaction queues again
    once(("sync_doctype", "Scenario", "t1"), lambda: ran.append("scenario"))
    assert len(queue) == 1


def test_the_deferred_doctype_sync_is_keyed_by_doctype_and_table():
    with open(CLICKHOUSE) as f:
        src = f.read()
    body = src.split("def sync_doctype_after_commit")[1].split("\ndef ")[0]
    assert 'after_commit_once(("sync_doctype", doctype, table)' in body


def test_nothing_is_queued_where_the_inline_sync_was_skipped():
    """An install ran the queue after in_install was cleared, syncing fixtures
    before the warehouse existed (#141 review)."""
    for flag in ("in_install", "in_import", "in_migrate", "in_patch"):
        queue = []
        _after_commit_once(queue, flags=(flag,))(("sync_doctype", "Scenario", "t1"), lambda: None)
        assert queue == [], flag


def test_a_failing_sync_is_logged_not_raised():
    """It runs after the commit: a raise would turn a committed save into an
    error response and drop the syncs queued behind it (#141 review)."""
    queue, logged = [], []
    once = _after_commit_once(queue, logged=logged)
    once(("sync_doctype", "Scenario", "t1"), lambda: 1 / 0)
    queue[0]()   # the commit
    assert len(logged) == 1 and "Scenario" in logged[0]["title"]
    assert logged[0].get("defer_insert") is True, "after the last commit, a plain insert is never committed"

