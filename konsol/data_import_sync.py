"""Frappe Data Import for Entity and Dimension: one sync and one build per import.

Frappe's importer (``frappe/core/doctype/data_import/importer.py``, v16) saves
each row under ``frappe.flags.in_import`` and commits after each one. Under
that flag konsol's write-through (``clickhouse.after_commit_once``) and build
trigger (``tasks.queue_consolidation_build``) do nothing, on purpose: a sync
per row is one full-table TRUNCATE+INSERT per row, and install-time fixture
loads also run under the flag. So the import is handled once, at its end:

1. While the rows import, a controller that would have requested a build
   calls ``defer_rebuild(doctype)`` instead (Entity: a watched field changed;
   Dimension: the save would have applied the schema).
2. The importer ``db_set``s the Data Import's final status before it clears
   ``in_import``. ``db_set`` runs ``on_change``, never ``on_update``, so
   ``on_change`` here sees "Success" or "Partial Success" and queues one job,
   after the commit (the RQ job runner commits once ``start_import`` returns).
3. The job runs outside ``in_import``. Entity: sync ``epm_staging.entities``
   once, then, if a row deferred a rebuild, one consolidation Build Approval
   (konsol.tasks' scope request: debounced and serialised). Dimension:
   if a row deferred, ``schema_lifecycle.apply_and_rebuild`` once: one
   ``apply_schema_for_publish`` (it reads every Dimension, so once covers the
   whole import) and one full-refresh Build Approval, debounced.

Main Account is not here: it keeps Upload chart (``konsol.chart_upload``).
"""
import frappe

#: The doctypes whose import this module finishes.
DOCTYPES = ("Entity", "Dimension")

#: The Data Import statuses that mean at least one row may have imported.
_FINISHED = ("Success", "Partial Success")

#: The after-commit job.
AFTER_IMPORT_JOB = "konsol.data_import_sync.after_import"

#: frappe.flags keys. Flags live for one request or job, so neither outlives
#: the import that set it.
_STARTED = "konsol_import_started"
_REBUILD = "konsol_import_rebuild"


def defer_rebuild(doctype):
    """Called by a controller, under ``in_import``, where it would have
    requested a build: the import requests one at its end instead."""
    frappe.flags[_REBUILD] = set(frappe.flags.get(_REBUILD) or ()) | {doctype}


def _success_count(name):
    return frappe.db.count("Data Import Log", {"data_import": name, "success": 1})


def on_change(doc, method=None):
    """doc_events["Data Import"]["on_change"].

    Acts only on the importer's own status ``db_set``s, which run under
    ``in_import``: a Desk save of a finished import runs ``on_change`` too,
    outside the flag, and queues nothing.

    "In Progress" opens the import: the count of success logs is noted, and
    any deferral left from an earlier run is cleared. A final status queues the
    job only if this run added success logs. On a retry Frappe keeps the
    earlier run's success logs, and its first ``db_set`` (template warnings)
    fires at the old status, before "In Progress": neither queues a job.
    """
    if doc.reference_doctype not in DOCTYPES or not frappe.flags.in_import:
        return
    started = dict(frappe.flags.get(_STARTED) or {})
    if doc.status == "In Progress":
        started[doc.name] = _success_count(doc.name)
        frappe.flags[_STARTED] = started
        frappe.flags[_REBUILD] = set()
        return
    if doc.status not in _FINISHED or doc.name not in started:
        return
    before = started.pop(doc.name)
    frappe.flags[_STARTED] = started
    if _success_count(doc.name) <= before:
        return
    rebuild = doc.reference_doctype in (frappe.flags.get(_REBUILD) or ())
    frappe.flags[_REBUILD] = set()
    try:
        frappe.enqueue(
            AFTER_IMPORT_JOB,
            queue="default",
            enqueue_after_commit=True,
            job_id=f"konsol-data-import::{doc.name}",
            deduplicate=True,
            reference_doctype=doc.reference_doctype,
            data_import=doc.name,
            rebuild=rebuild,
        )
    except Exception:  # noqa: BLE001 — never fail the import's status write
        # deduplicate=True asks Redis now. The rows stand; reconcile_all after
        # the next migrate repairs the table, and a save requests the build.
        frappe.log_error(title=f"Data Import {doc.name}: konsol sync not queued")


def after_import(reference_doctype, data_import, rebuild):
    """The job ``on_change`` queues. It runs in its own transaction, which the
    job runner commits; ``request_build_for_scope`` commits itself, which a job
    may do. The trigger recorded on the Build Approval is the Data Import."""
    if reference_doctype == "Entity":
        from konsol.epm.doctype.entity.entity import _sync_entity_registry

        _sync_entity_registry()
        if rebuild:
            from konsol.tasks import DOCTYPE_BUILD_MAP, request_build_for_scope

            request_build_for_scope(DOCTYPE_BUILD_MAP["Entity"]["scope"],
                                    "Data Import", data_import)
    elif reference_doctype == "Dimension" and rebuild:
        # Dimension has no warehouse table of its own: its warehouse footprint
        # is the schema (dbt vars, columns, Custom Fields), and apply_and_rebuild
        # is the one path that writes it.
        from konsol.schema_lifecycle import apply_and_rebuild

        apply_and_rebuild(frappe._dict(doctype="Data Import", name=data_import), "Data Import")
