"""A publish commits or rolls back as one transaction (konsol#135).

The Budget Line Custom Field sync inserts Custom Fields, and CustomField.on_update
calls frappe.db.updatedb, which ends with a commit. Run inside a publish, it
committed the Dimension's save halfway, before the build request was taken.
The publish now enqueues the sync as a job after the commit.

Site-free: the functions are compiled from source against a stub frappe.
"""
import ast
import os
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_APPLY = os.path.join(APP_DIR, "schema_apply.py")
LIFECYCLE = os.path.join(APP_DIR, "schema_lifecycle.py")
INSTALL = os.path.join(APP_DIR, "install.py")

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
    """Compile the named schema_apply functions (and its module constants) into ns."""
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
    assert method == "konsol.schema_apply.sync_budget_custom_fields_job"
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
    """It has nothing else in its transaction, and a GET would roll back an
    after-commit enqueue, so it keeps the inline sync and reports it."""
    enqueued, logged, synced = [], [], []
    ns = _publish_ns(enqueued, logged, synced)
    summary = ns["apply_schema"]()
    assert synced == ["inline"] and enqueued == []
    assert summary["budget_fields_synced"] == ["added dim_x"]


def test_the_queued_job_path_resolves_to_a_module_function():
    fns = _functions(SCHEMA_APPLY)
    assert "sync_budget_custom_fields_job" in fns
    job = fns["sync_budget_custom_fields_job"]
    assert not job.args.args, "the job takes no kwargs"
    assert "_sync_budget_custom_fields" in {_call_name(c) for c in _calls(job)}


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


def test_after_migrate_re_runs_the_sync_as_the_repair_path():
    """A crash between the publish's commit and the job leaves Budget Line
    behind; the next migrate (or publish, or apply_schema) repairs it."""
    fns = _functions(INSTALL)
    after = {_call_name(c) for c in _calls(fns["after_migrate"])}
    assert "_sync_budget_line_custom_fields" in after
    helper = {_call_name(c) for c in _calls(fns["_sync_budget_line_custom_fields"])}
    assert "_sync_budget_custom_fields" in helper


# --- the sync itself: idempotent, and right after a publish and an unpublish ---

class _Site:
    """Dimension rows and Budget Line Custom Fields, in memory."""

    def __init__(self):
        self.dimensions = {}
        self.fields = {}

    def get_all(self, doctype, filters=None, fields=None, limit_page_length=None):
        if doctype == "Dimension":
            return [types.SimpleNamespace(dimension_name=n, label=d["label"])
                    for n, d in self.dimensions.items()
                    if d["in_budget"] == filters["in_budget"] and d["status"] == filters["status"]]
        assert doctype == "Custom Field" and filters == {"dt": "Budget Line", "fieldname": ("like", "dim_%")}
        return [types.SimpleNamespace(name=n, fieldname=f) for n, f in self.fields.items()
                if f.startswith("dim_")]

    def new_doc(self, doctype):
        site = self

        class CustomField(types.SimpleNamespace):
            def insert(self):
                name = f"{self.dt}-{self.fieldname}"
                assert name not in site.fields, "duplicate insert"
                site.fields[name] = self.fieldname

        return CustomField()

    def delete_doc(self, doctype, name):
        del self.fields[name]


def _sync(site):
    ns = {"frappe": types.SimpleNamespace(get_all=site.get_all, new_doc=site.new_doc,
                                          delete_doc=site.delete_doc)}
    return _load({"_sync_budget_custom_fields"}, ns)["_sync_budget_custom_fields"]


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


def test_a_dimension_not_in_budget_gets_no_field():
    site = _Site()
    site.dimensions["dim_business_unit"] = {"label": "BU", "in_budget": 0, "status": "Published"}
    assert _sync(site)() == [] and site.fields == {}
