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

Known limit: a deleted record leaves no ``modified`` behind, so a delete is not
seen as a change here.
"""
import frappe

from konsol import build_lock, hooks, tasks
from konsol.close.freshness_model import COMPLETED, FAILED, freshness

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


def _latest_changes(doctypes):
    changes = []
    for dt in doctypes:
        where = " WHERE docstatus IN (1,2)" if frappe.get_meta(dt).is_submittable else ""
        rows = frappe.db.sql(f"SELECT MAX(modified) FROM `tab{dt}`{where}")
        latest = rows[0][0] if rows else None
        if latest is not None:
            changes.append({"doctype": dt, "modified": latest})
    return changes


def _builds():
    states = [COMPLETED, FAILED] + list(build_lock.FLAGGED_STATES)
    return frappe.get_all(
        "Build Approval",
        filters={"workflow_state": ["in", states]},
        fields=["name", "build_scope", "workflow_state", "completed_at", "error_message"],
    )


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


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
