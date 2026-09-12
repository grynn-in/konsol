"""A publish commits or rolls back as one transaction (konsol#135).

The Budget Line Custom Field sync inserts Custom Fields, and CustomField.on_update
calls frappe.db.updatedb, which ends with a commit. Run inside a publish, it
committed the Dimension's save halfway, before the build request was taken.
The publish now enqueues the sync as a job after the commit.

Site-free: the code runs against a stub frappe.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_APPLY = os.path.join(APP_DIR, "schema_apply.py")
LIFECYCLE = os.path.join(APP_DIR, "schema_lifecycle.py")
INSTALL = os.path.join(APP_DIR, "install.py")
JOB = "konsol.schema_apply.sync_budget_custom_fields_job"

# frappe.enqueue's own parameters: none of them may be a job kwarg.
_ENQUEUE_OWN = {"method", "queue", "timeout", "event", "is_async", "job_name", "now",
                "enqueue_after_commit", "on_success", "on_failure", "at_front",
                "job_id", "deduplicate"}


def _tree(path):
    with open(path) as f:
        return ast.parse(f.read())


def _functions(path):
    return {n.name: n for n in _tree(path).body if isinstance(n, ast.FunctionDef)}


def _calls(fn):
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call)]


def _call_name(call):
    return getattr(call.func, "attr", None) or getattr(call.func, "id", None)


def _load(names, ns):
    """Compile the named schema_apply functions (and its _BUDGET constants) into ns."""
    tree = _tree(SCHEMA_APPLY)
    body = [n for n in tree.body
            if (isinstance(n, ast.FunctionDef) and n.name in names)
            or (isinstance(n, ast.Assign) and all(isinstance(t, ast.Name) and t.id.startswith("_BUDGET")
                                                   for t in n.targets))]
    for node in body:   # drop @frappe.whitelist(): the stub has none
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
    exec(compile(ast.Module(body=body, type_ignores=[]), SCHEMA_APPLY, "exec"), ns)
    return ns


class _Throw(Exception):
    pass


class _Duplicate(Exception):
    pass


def _stub_frappe(enqueued, logged, enqueue_error=None):
    def enqueue(method, **kw):
        if enqueue_error:
            raise enqueue_error
        enqueued.append((method, kw))

    def throw(msg, exc=None):
        raise _Throw(msg)

    return types.SimpleNamespace(
        enqueue=enqueue, throw=throw, PermissionError=PermissionError,
        get_roles=lambda: ["EPM Admin"],
        log_error=lambda *a, **kw: logged.append(a),
        get_traceback=lambda: "tb",
        form_dict={},
        logger=lambda: types.SimpleNamespace(info=lambda msg: None, warning=lambda msg: None),
    )


def _publish_ns(enqueued, logged, synced, enqueue_error=None):
    ns = {
        "frappe": _stub_frappe(enqueued, logged, enqueue_error),
        "regenerate_vars": lambda: None,
        "_apply_clickhouse_columns": lambda: [],
        "_apply_fact_tables": lambda: ([], []),
        "_sync_budget_custom_fields": lambda: synced.append("inline") or ["added dim_x"],
    }
    return _load({"apply_schema", "apply_schema_for_publish", "queue_budget_custom_field_sync",
                  "_check_schema_role", "_apply_schema_steps"}, ns)


def test_a_publish_queues_the_custom_field_sync_after_the_commit_and_never_runs_it_inline():
    enqueued, logged, synced = [], [], []
    ns = _publish_ns(enqueued, logged, synced)
    summary = ns["apply_schema_for_publish"]()
    assert synced == [], "the sync commits through updatedb: never inside the publish"
    assert len(enqueued) == 1
    method, kw = enqueued[0]
    assert method == JOB
    assert kw.get("enqueue_after_commit") is True, "enqueued now, a worker could run it before the commit"
    assert set(kw) <= _ENQUEUE_OWN, "no job kwargs: the job reads the committed dimensions"
    assert summary["budget_fields_synced"] == ["queued after commit"] and not summary["errors"]


def test_a_publish_is_not_failed_by_an_enqueue_error():
    enqueued, logged, synced = [], [], []
    ns = _publish_ns(enqueued, logged, synced, enqueue_error=ConnectionError("redis down"))
    summary = ns["apply_schema_for_publish"]()
    assert synced == [] and enqueued == []
    assert any("Budget fields" in e for e in summary["errors"]) and logged


def test_the_standalone_apply_schema_still_syncs_inline():
    """Nothing else is in its transaction, so it keeps the inline sync and reports it."""
    enqueued, logged, synced = [], [], []
    ns = _publish_ns(enqueued, logged, synced)
    summary = ns["apply_schema"]()
    assert synced == ["inline"] and enqueued == []
    assert summary["budget_fields_synced"] == ["added dim_x"]


def test_the_standalone_apply_schema_is_post_only():
    """A GET rolls back at the end of the request: the sync's inserts (committed
    by updatedb) stuck while its deletes were dropped."""
    fn = _functions(SCHEMA_APPLY)["apply_schema"]
    [deco] = fn.decorator_list
    assert ast.unparse(deco) == "frappe.whitelist(methods=['POST'])"


def test_the_queued_job_path_resolves_to_a_module_function():
    fns = _functions(SCHEMA_APPLY)
    assert JOB.rsplit(".", 1)[1] in fns
    job = fns["sync_budget_custom_fields_job"]
    assert not job.args.args, "the job takes no kwargs"
    assert "_sync_budget_custom_fields" in {_call_name(c) for c in _calls(job)}


def test_the_job_syncs_as_administrator():
    """It runs as the publisher. An EPM Admin can neither create a Custom Field
    nor delete one Administrator created (the migrate-provisioned dim fields)."""
    calls = []
    ns = {"frappe": types.SimpleNamespace(set_user=lambda u: calls.append(("set_user", u)),
                                          logger=lambda: types.SimpleNamespace(info=lambda m: None)),
          "_sync_budget_custom_fields": lambda: calls.append("sync") or []}
    _load({"sync_budget_custom_fields_job"}, ns)["sync_budget_custom_fields_job"]()
    assert calls == [("set_user", "Administrator"), "sync"]


def test_the_publish_path_neither_syncs_inline_nor_commits():
    fns = _functions(SCHEMA_APPLY)
    for name in ("apply_schema_for_publish", "queue_budget_custom_field_sync", "_apply_schema_steps"):
        called = {_call_name(c) for c in _calls(fns[name])}
        assert "_sync_budget_custom_fields" not in called, name
        assert "commit" not in called, name
    lifecycle = _functions(LIFECYCLE)["apply_and_rebuild"]
    called = {_call_name(c) for c in _calls(lifecycle)}
    assert "apply_schema_for_publish" in called
    assert "apply_schema" not in called and "_sync_budget_custom_fields" not in called


# --- apply_and_rebuild, run for real against a stub frappe ---

class _Touched(Exception):
    pass


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _run_publish(touched, enqueued):
    """apply_and_rebuild with the real konsol.schema_apply / schema_lifecycle
    (and any konsol module they import), against a frappe that records, and
    refuses, everything the Custom Field sync or a commit would touch. So an
    inline sync is caught whatever name it is called under, even through a
    helper that swallows the error (install._sync_budget_line_custom_fields)."""
    def refuse(what):
        def fn(*a, **kw):
            touched.append(what)
            raise _Touched(what)
        return fn

    def sql(query, *a, **kw):
        if "`tabBuild Approval`" in query:   # the build request's debounce read
            return []
        return refuse("db.sql " + " ".join(query.split())[:50])()

    def new_doc(doctype):
        if doctype == "Build Approval":
            return types.SimpleNamespace(insert=lambda **kw: None, name="BAPR-TEST")
        return refuse("new_doc " + doctype)()

    def get_all(doctype, **kw):
        if doctype in ("Dimension", "Dataset"):   # steps 1-3 (ClickHouse DDL)
            return []
        return refuse("get_all " + doctype)()

    frappe = types.ModuleType("frappe")
    frappe.__dict__.update(
        whitelist=lambda *a, **kw: (lambda f: f),
        enqueue=lambda method, **kw: enqueued.append((method, kw)),
        get_roles=lambda *a: ["EPM Admin"], throw=refuse("throw"), PermissionError=PermissionError,
        DuplicateEntryError=_Duplicate, log_error=lambda *a, **kw: None, get_traceback=lambda: "",
        form_dict={}, msgprint=lambda *a, **kw: None,
        logger=lambda: types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        session=types.SimpleNamespace(user="zz@example.com"),
        new_doc=new_doc, get_all=get_all, get_doc=refuse("get_doc"), delete_doc=refuse("delete_doc"),
        set_user=refuse("set_user"),
        db=types.SimpleNamespace(sql=sql, commit=refuse("db.commit"), rollback=refuse("db.rollback")),
    )
    konsol = types.ModuleType("konsol")
    konsol.__path__ = [APP_DIR]   # other konsol modules load from source
    stubs = {
        "frappe": frappe,
        "konsol": konsol,
        "konsol.clickhouse": types.SimpleNamespace(execute=refuse("clickhouse"), get_connection=refuse("clickhouse")),
        "konsol.dbt_config": types.SimpleNamespace(regenerate_vars=lambda: None),
        "konsol.build_lock": types.SimpleNamespace(lock_build_requests=lambda: None,
                                                   flag_running_build=lambda row: None),
    }
    before = {k: v for k, v in sys.modules.items() if k.split(".")[0] in ("frappe", "konsol")}
    try:
        for key in before:
            del sys.modules[key]
        sys.modules.update(stubs)
        _module("konsol.schema_apply", SCHEMA_APPLY)
        lifecycle = _module("konsol.schema_lifecycle", LIFECYCLE)
        return lifecycle.apply_and_rebuild(types.SimpleNamespace(doctype="Dimension", name="dim_zz"), "Publish")
    finally:
        for key in [k for k in sys.modules if k.split(".")[0] in ("frappe", "konsol")]:
            del sys.modules[key]
        sys.modules.update(before)


def test_apply_and_rebuild_touches_no_custom_field_and_never_commits():
    touched, enqueued = [], []
    assert _run_publish(touched, enqueued) == "BAPR-TEST"
    assert touched == [], f"the publish's transaction reached the sync or a commit: {touched}"
    assert [m for m, _ in enqueued] == [JOB]


def test_after_migrate_re_runs_the_sync_as_the_repair_path():
    """A crash between the publish's commit and the job leaves Budget Line
    behind; the next migrate (or publish, or apply_schema) repairs it."""
    fns = _functions(INSTALL)
    after = {_call_name(c) for c in _calls(fns["after_migrate"])}
    assert "_sync_budget_line_custom_fields" in after
    helper = {_call_name(c) for c in _calls(fns["_sync_budget_line_custom_fields"])}
    assert "_sync_budget_custom_fields" in helper


# --- the sync itself: serialised, idempotent, right after publish and unpublish ---

class _Site:
    """MariaDB for the sync: a named lock, Dimension rows, Budget Line Custom Fields."""

    def __init__(self, lock=1):
        self.lock = lock          # what GET_LOCK returns: 1, 0 (timeout) or None (error)
        self.held = False
        self.dimensions = {}
        self.fields = {}
        self.created_elsewhere = set()   # inserted by someone else just before ours
        self.fail_insert = False
        self.logged = []

    def sql(self, query, values=None, as_dict=False):
        q = " ".join(query.split())
        if q.startswith("SELECT GET_LOCK"):
            self.held = self.lock == 1
            return ((self.lock,),)
        if q.startswith("SELECT RELEASE_LOCK"):
            assert self.held, "released a lock it does not hold"
            self.held = False
            return ((1,),)
        assert self.held, "read outside the lock"
        assert q.endswith("LOCK IN SHARE MODE"), "a plain read sees the snapshot from before the lock"
        if "`tabDimension`" in q:
            return [types.SimpleNamespace(dimension_name=n, label=d["label"])
                    for n, d in self.dimensions.items()
                    if d["in_budget"] == 1 and d["status"] == "Published"]
        assert "`tabCustom Field`" in q and values == ("Budget Line", "dim_%")
        return [types.SimpleNamespace(name=n, fieldname=f) for n, f in self.fields.items()
                if f.startswith("dim_")]

    def new_doc(self, doctype):
        site = self

        class CustomField(types.SimpleNamespace):
            def insert(self):
                assert site.held, "wrote outside the lock"
                if site.fail_insert:
                    raise RuntimeError("DDL failed")
                name = f"{self.dt}-{self.fieldname}"
                if self.fieldname in site.created_elsewhere or name in site.fields:
                    site.fields[name] = self.fieldname
                    raise _Duplicate(name)
                site.fields[name] = self.fieldname

        return CustomField()

    def delete_doc(self, doctype, name):
        assert self.held, "wrote outside the lock"
        del self.fields[name]


def _sync(site):
    ns = {"frappe": types.SimpleNamespace(
        db=types.SimpleNamespace(sql=site.sql), new_doc=site.new_doc, delete_doc=site.delete_doc,
        DuplicateEntryError=_Duplicate, log_error=lambda *a, **kw: site.logged.append(a))}
    return _load({"_sync_budget_custom_fields", "_sync_budget_custom_fields_locked"}, ns)[
        "_sync_budget_custom_fields"]


def test_the_sync_is_idempotent_and_follows_publish_and_unpublish():
    site = _Site()
    site.dimensions["dim_cost_center"] = {"label": "Cost Center", "in_budget": 1, "status": "Published"}
    site.fields["Budget Line-dim_cost_center"] = "dim_cost_center"
    site.fields["Budget Line-other_field"] = "other_field"   # not a dimension field: left alone
    sync = _sync(site)

    site.dimensions["dim_zz"] = {"label": "ZZ", "in_budget": 1, "status": "Published"}
    assert sync() == ["added dim_zz"]
    assert sync() == [], "a second run (a duplicate job, or after_migrate) changes nothing"

    site.dimensions["dim_zz"]["status"] = "Inactive"   # unpublished
    assert sync() == ["removed dim_zz"]
    assert sync() == []
    assert set(site.fields.values()) == {"dim_cost_center", "other_field"}
    assert not site.held, "the lock is released"


def test_a_dimension_not_in_budget_gets_no_field():
    site = _Site()
    site.dimensions["dim_business_unit"] = {"label": "BU", "in_budget": 0, "status": "Published"}
    assert _sync(site)() == [] and site.fields == {}


def test_a_field_inserted_meanwhile_counts_as_synced():
    site = _Site()
    site.dimensions["dim_zz"] = {"label": "ZZ", "in_budget": 1, "status": "Published"}
    site.created_elsewhere.add("dim_zz")
    assert _sync(site)() == [], "DuplicateEntryError is success, not a failed job"
    assert site.fields == {"Budget Line-dim_zz": "dim_zz"} and not site.held


def test_a_sync_that_cannot_get_the_lock_is_logged_and_skipped():
    for result in (0, None):   # 0: timed out; NULL: error
        site = _Site(lock=result)
        site.dimensions["dim_zz"] = {"label": "ZZ", "in_budget": 1, "status": "Published"}
        assert _sync(site)() == [], result
        assert site.fields == {} and len(site.logged) == 1, result


def test_the_lock_is_released_when_the_sync_fails():
    site = _Site()
    site.dimensions["dim_zz"] = {"label": "ZZ", "in_budget": 1, "status": "Published"}
    site.fail_insert = True
    try:
        _sync(site)()
    except RuntimeError:
        pass
    else:
        raise AssertionError("the failure propagates (the job runner rolls back and logs)")
    assert not site.held
