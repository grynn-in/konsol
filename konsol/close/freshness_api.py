"""Are the consolidated numbers current? (konsol#305 A16; story 0.2).

Reads the live site and passes it through the pure A05 model
(``konsol.close.freshness_model.freshness``):

- builds: Build Approval rows in a terminal (Completed, Failed) or flagged
  state; Cancelled rows are not read.
- changes: the latest ``modified`` per build trigger doctype, i.e.
  ``hooks._dbt_trigger_doctypes`` plus ``Entity`` (its build is requested from
  its controller, entity.py). A submittable doctype counts only
  ``docstatus IN (1,2)``: a draft never triggers a build (tasks.py
  queue_consolidation_build).
- scope_of: ``tasks.DOCTYPE_BUILD_MAP``. A trigger doctype missing from it
  raises ValueError, whether or not it has records.

Deletes (konsol#305 A42): Frappe leaves no ``modified`` behind on delete, only
a ``Deleted Document`` row (``deleted_doctype``, ``creation``). For every
NON-submittable trigger doctype (Consolidation Group, IC Elimination Rule,
Entity, ...) the latest ``Deleted Document.creation`` is taken as a change
too, whichever is later than the live table's own ``MAX(modified)``.
Submittable doctypes are excluded: they can only delete drafts (which never
triggered a build) or cancelled records (whose cancel already left a
``docstatus IN (1,2)`` row in the live table, already counted).
"""
import frappe

from konsol import build_lock, hooks, tasks
from konsol.close.freshness_model import COMPLETED, FAILED, freshness
from konsol.close.timefmt import zoned_iso

#: Requested from Entity's controller, not from doc_events (hooks.py).
CONTROLLER_TRIGGERS = ("Entity",)


def _trigger_doctypes():
    return list(hooks._dbt_trigger_doctypes) + [
        dt for dt in CONTROLLER_TRIGGERS if dt not in hooks._dbt_trigger_doctypes
    ]


def _scope_of(doctypes):
    build_map = tasks.DOCTYPE_BUILD_MAP
    missing = [dt for dt in doctypes if dt not in build_map]
    if missing:
        raise ValueError(
            f"{', '.join(missing)} trigger(s) a build but has no build scope declared; "
            "add it to tasks.DOCTYPE_BUILD_MAP so its changes can be judged."
        )
    return {dt: build_map[dt]["scope"] for dt in doctypes}


def _deleted_since(doctypes):
    """Latest ``Deleted Document.creation`` per doctype (konsol#305 A42): a
    delete leaves no ``modified`` on the (now gone) row."""
    if not doctypes:
        return {}
    rows = frappe.db.sql(
        "SELECT deleted_doctype, MAX(creation) FROM `tabDeleted Document` "
        "WHERE deleted_doctype IN %(doctypes)s GROUP BY deleted_doctype",
        {"doctypes": tuple(doctypes)},
    )
    return {dt: at for dt, at in rows if at is not None}


def _latest_changes(doctypes):
    modified = {}
    non_submittable = []
    for dt in doctypes:
        is_submittable = frappe.get_meta(dt).is_submittable
        where = " WHERE docstatus IN (1,2)" if is_submittable else ""
        rows = frappe.db.sql(f"SELECT MAX(modified) FROM `tab{dt}`{where}")
        modified[dt] = rows[0][0] if rows else None
        if not is_submittable:
            non_submittable.append(dt)

    deleted = _deleted_since(non_submittable)

    changes = []
    for dt in doctypes:
        candidates = [v for v in (modified.get(dt), deleted.get(dt)) if v is not None]
        if candidates:
            changes.append({"doctype": dt, "modified": max(candidates)})
    return changes


def _builds():
    states = [COMPLETED, FAILED] + list(build_lock.FLAGGED_STATES)
    return frappe.get_all(
        "Build Approval",
        filters={"workflow_state": ["in", states]},
        fields=["name", "build_scope", "workflow_state", "completed_at", "error_message"],
    )


def _iso(value):
    """A naive datetime as ISO 8601 with the site's own offset attached
    (konsol#305 A16b): a zone-less time cannot be placed on a timeline."""
    if not hasattr(value, "isoformat"):
        return value
    return zoned_iso(value, frappe.utils.get_system_timezone())


def current_freshness():
    """The A05 result for the live site, JSON-safe. Not whitelisted: callers
    (A26, A29) gate their own endpoints."""
    doctypes = _trigger_doctypes()
    scope_of = _scope_of(doctypes)
    result = freshness(_builds(), _latest_changes(doctypes), scope_of,
                       build_lock.FLAGGED_STATES)
    result["as_of"] = _iso(result["as_of"])
    if result["last_failed"]:
        result["last_failed"] = dict(result["last_failed"], at=_iso(result["last_failed"]["at"]))
    return result


@frappe.whitelist(methods=["GET"])
def get_freshness():
    """Whether the consolidated numbers are current, for the close header."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager"))
    return current_freshness()
