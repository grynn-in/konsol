"""Frappe Data Import for Entity and Dimension: one sync and one build per import.

Before this, the Desk refused both ("Data Import is not allowed for Entity"),
and had it not, nothing would have reached the warehouse: Frappe runs the rows
under ``frappe.flags.in_import``, and konsol's write-through
(``clickhouse.after_commit_once``) and build trigger
(``tasks.queue_consolidation_build``) both skip under that flag. A
row-by-row sync during the import is the wrong fix anyway: it is one
full-table TRUNCATE+INSERT and one build request per row.

What Frappe v16 does (``frappe/core/doctype/data_import/importer.py``):
``Importer.import_data`` sets ``in_import``, ``db_set``s the Data Import's
status to "In Progress", imports each row and commits after each one, then
``db_set``s the final status ("Success", "Partial Success", "Error" or
"Pending"), and only then clears ``in_import``. ``db_set`` runs ``on_change``,
never ``on_update``, so the hook is a Data Import ``on_change``. The RQ job
runner commits after ``start_import`` returns, and the after-commit queue runs
then, outside ``in_import``.

These tests replay that sequence of ``on_change`` calls against
``konsol.data_import_sync`` with frappe stubbed, and check the job it queues.
"""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(APP_DIR, "data_import_sync.py")


def _json(*parts):
    with open(os.path.join(APP_DIR, *parts)) as f:
        return json.load(f)


ENTITY_JSON = ("epm", "doctype", "entity", "entity.json")
DIMENSION_JSON = ("epm", "doctype", "dimension", "dimension.json")


# --- the DocTypes ---------------------------------------------------------


def test_entity_and_dimension_allow_import():
    for parts in (ENTITY_JSON, DIMENSION_JSON):
        assert _json(*parts).get("allow_import") == 1, parts[-1]


def test_the_roles_that_create_them_may_import_them():
    """DataImport.validate_doctype also asks frappe.has_permission(dt, "import"):
    allow_import alone still refuses everyone but Administrator."""
    for parts in (ENTITY_JSON, DIMENSION_JSON):
        perms = {p["role"]: p for p in _json(*parts)["permissions"]}
        for role in ("System Manager", "EPM Admin"):
            assert perms[role].get("import") == 1, (parts[-1], role)


def test_the_list_says_data_import_works():
    for parts in (ENTITY_JSON, DIMENSION_JSON):
        assert "Data Import" in (_json(*parts).get("description") or ""), parts[-1]


# --- hooks.py -------------------------------------------------------------


def test_hooks_wire_the_data_import_hook_on_change():
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    found = []
    for n in tree.body:
        if not isinstance(n, ast.Assign):
            continue
        for t in n.targets:
            if (isinstance(t, ast.Subscript) and getattr(t.value, "id", None) == "doc_events"
                    and ast.literal_eval(t.slice) == "Data Import"):
                found.append(ast.literal_eval(n.value))
    assert found == [{"on_change": "konsol.data_import_sync.on_change"}], found


# --- the stubbed frappe ---------------------------------------------------


class _Dict(dict):
    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


_FLAGS = _Dict()
_ENQUEUED = []
#: Data Import Log success rows, by data import name.
_SUCCESS_LOGS = {}
#: What the after-import job called.
_CALLS = []


def _count(doctype, filters):
    assert doctype == "Data Import Log"
    assert filters == {"data_import": filters["data_import"], "success": 1}
    return _SUCCESS_LOGS.get(filters["data_import"], 0)


def _install_stubs():
    frappe = types.ModuleType("frappe")
    frappe.flags = _FLAGS
    frappe._dict = _Dict
    frappe.db = types.SimpleNamespace(count=_count)
    frappe.enqueue = lambda method, **kw: _ENQUEUED.append((method, kw))
    frappe.log_error = lambda *a, **k: None
    sys.modules["frappe"] = frappe

    konsol = types.ModuleType("konsol")
    konsol.__path__ = [APP_DIR]
    sys.modules["konsol"] = konsol

    entity = types.ModuleType("konsol.epm.doctype.entity.entity")
    entity._sync_entity_registry = lambda: _CALLS.append(("sync", "Entity"))
    for name in ("konsol.epm", "konsol.epm.doctype", "konsol.epm.doctype.entity"):
        pkg = types.ModuleType(name)
        pkg.__path__ = []
        sys.modules[name] = pkg
    sys.modules["konsol.epm.doctype.entity.entity"] = entity

    tasks = types.ModuleType("konsol.tasks")
    tasks.DOCTYPE_BUILD_MAP = {"Entity": {"scope": "consolidation", "risk": "high"}}
    tasks.request_build_for_scope = lambda scope, dt, dn, **kw: _CALLS.append(
        ("build", scope, dt, dn, kw))
    sys.modules["konsol.tasks"] = tasks

    lifecycle = types.ModuleType("konsol.schema_lifecycle")
    lifecycle.apply_and_rebuild = lambda doc, action: _CALLS.append(
        ("apply_and_rebuild", doc.doctype, doc.name, action))
    sys.modules["konsol.schema_lifecycle"] = lifecycle


_install_stubs()
_spec = importlib.util.spec_from_file_location("data_import_sync_under_test", _SRC)
m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(m)


def _reset():
    _FLAGS.clear()
    del _ENQUEUED[:]
    del _CALLS[:]
    _SUCCESS_LOGS.clear()


class _Import:
    """A Data Import doc, as the importer's db_set leaves it."""

    doctype = "Data Import"

    def __init__(self, reference_doctype="Entity", status="Pending", name="ZZ-IMPORT-1"):
        self.name = name
        self.reference_doctype = reference_doctype
        self.status = status

    def db_set(self, status):
        self.status = status
        m.on_change(self, "on_change")


def _run_import(doc, rows_imported, rebuild_from=(), final="Success"):
    """Importer.import_data, as on_change sees it: the template_warnings
    db_set at the old status, In Progress, the rows (each committing a log
    row; a controller may defer a rebuild), the final status, then
    in_import cleared."""
    _FLAGS.in_import = True
    doc.db_set(doc.status)
    doc.db_set("In Progress")
    _SUCCESS_LOGS[doc.name] = _SUCCESS_LOGS.get(doc.name, 0) + rows_imported
    for dt in rebuild_from:
        m.defer_rebuild(dt)
    doc.db_set(final)
    _FLAGS.in_import = False


def _jobs():
    return [kw for method, kw in _ENQUEUED if method == m.AFTER_IMPORT_JOB]


# --- the hook -------------------------------------------------------------


def test_an_entity_import_queues_one_job_after_the_commit():
    _reset()
    _run_import(_Import(), rows_imported=3, rebuild_from=("Entity",) * 3)
    jobs = _jobs()
    assert len(jobs) == 1, _ENQUEUED
    job = jobs[0]
    assert job["enqueue_after_commit"] is True
    assert job["deduplicate"] is True and job["job_id"] == "konsol-data-import::ZZ-IMPORT-1"
    assert job["reference_doctype"] == "Entity"
    assert job["data_import"] == "ZZ-IMPORT-1"
    assert job["rebuild"] is True


def test_partial_success_queues_the_job_too():
    _reset()
    _run_import(_Import(), rows_imported=2, final="Partial Success")
    assert len(_jobs()) == 1


def test_a_dimension_import_queues_the_job():
    _reset()
    _run_import(_Import("Dimension"), rows_imported=1, rebuild_from=("Dimension",))
    (job,) = _jobs()
    assert job["reference_doctype"] == "Dimension" and job["rebuild"] is True


def test_no_job_for_other_doctypes():
    _reset()
    _run_import(_Import("Main Account"), rows_imported=5, rebuild_from=("Main Account",))
    assert _ENQUEUED == []


def test_no_job_when_no_row_was_imported():
    _reset()
    _run_import(_Import(), rows_imported=0)
    assert _ENQUEUED == []


def test_no_job_for_error_or_pending():
    for final in ("Error", "Pending"):
        _reset()
        _run_import(_Import(), rows_imported=0, final=final)
        assert _ENQUEUED == [], final


def test_a_resave_of_a_finished_import_queues_nothing():
    """The Desk's save runs on_change too, outside in_import."""
    _reset()
    doc = _Import(status="Success")
    _SUCCESS_LOGS[doc.name] = 3
    m.on_change(doc, "on_change")
    _FLAGS.in_import = True   # and a status db_set that is not the import's end
    m.on_change(doc, "on_change")
    _FLAGS.in_import = False
    assert _ENQUEUED == []


def test_a_retry_queues_only_when_it_imported_more_rows():
    """A Partial Success retried: Frappe keeps the earlier success logs, and
    the template_warnings db_set fires on_change at the old status before In
    Progress. Only rows imported by this run count."""
    _reset()
    doc = _Import(status="Partial Success")
    _SUCCESS_LOGS[doc.name] = 2
    _run_import(doc, rows_imported=0, final="Partial Success")
    assert _ENQUEUED == []
    _run_import(doc, rows_imported=1, final="Success")
    assert len(_jobs()) == 1


def test_rebuild_is_false_when_no_row_changed_what_consolidation_sees():
    """An import that only renames countries: the controller defers nothing."""
    _reset()
    _run_import(_Import(), rows_imported=2)
    (job,) = _jobs()
    assert job["rebuild"] is False


def test_a_deferral_from_an_earlier_import_does_not_leak():
    _reset()
    m.defer_rebuild("Entity")       # left over, say, from a failed run
    _run_import(_Import(), rows_imported=1)
    (job,) = _jobs()
    assert job["rebuild"] is False


def test_a_queue_failure_never_fails_the_import():
    _reset()
    sys.modules["frappe"].enqueue = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("redis"))
    try:
        _run_import(_Import(), rows_imported=1)
    finally:
        sys.modules["frappe"].enqueue = lambda method, **kw: _ENQUEUED.append((method, kw))


# --- the job --------------------------------------------------------------


def test_the_entity_job_syncs_once_then_requests_one_build():
    _reset()
    m.after_import("Entity", "ZZ-IMPORT-1", rebuild=True)
    assert _CALLS == [
        ("sync", "Entity"),
        ("build", "consolidation", "Data Import", "ZZ-IMPORT-1", {}),
    ], _CALLS


def test_the_entity_job_syncs_without_a_build_when_none_is_owed():
    _reset()
    m.after_import("Entity", "ZZ-IMPORT-1", rebuild=False)
    assert _CALLS == [("sync", "Entity")], _CALLS


def test_the_dimension_job_applies_the_schema_once_with_one_full_refresh_request():
    """apply_and_rebuild: apply_schema_for_publish (reads every Dimension) and
    one full-refresh, debounced Build Approval, triggered by the import."""
    _reset()
    m.after_import("Dimension", "ZZ-IMPORT-2", rebuild=True)
    assert _CALLS == [("apply_and_rebuild", "Data Import", "ZZ-IMPORT-2", "Data Import")], _CALLS


def test_the_dimension_job_does_nothing_for_a_draft_only_import():
    _reset()
    m.after_import("Dimension", "ZZ-IMPORT-2", rebuild=False)
    assert _CALLS == []


def test_the_job_never_commits_and_reaches_the_build_through_konsol_paths():
    with open(_SRC) as f:
        src = f.read()
    assert "commit(" not in src
    assert "request_build_for_scope" in src and "apply_and_rebuild" in src
