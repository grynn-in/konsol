"""Connector Health — derived per-connector sync health snapshot.

One row per enabled Connector, refreshed by the ``refresh_connector_health``
scheduler job (5-min cron, see hooks.py). Status, lag, and entity counts are
derived from the Connector's webhook-fed ``last_sync_*`` fields plus a live
ClickHouse count — this doctype is never edited by hand.

Source of sync timing/status is the Connector doctype (populated by the
``airbyte_sync_complete`` webhook), not a direct Airbyte poll: the webhook
plumbing already exists and avoids depending on a specific Airbyte job-status
API (PRD Scale Architecture, Open Question 4).
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, get_datetime

from konsol import clickhouse

# Connector.last_sync_status -> Connector Health.last_sync_status
_STATUS_MAP = {
    "Success": "Succeeded",
    "Partial": "Succeeded",
    "Failed": "Failed",
    "Running": "Running",
}

# Health states that warrant an operator alert.
_UNHEALTHY = {"Failed", "Stale"}


class ConnectorHealth(Document):
    pass


def _entities_loaded(erp_source):
    """count(distinct entity_id) for this erp_source in canonical staging.

    Best-effort: returns 0 if ClickHouse is unreachable or the table is absent,
    so a health refresh never fails on a transient DB issue.
    """
    if not erp_source:
        return 0
    try:
        res = clickhouse.execute(
            "SELECT count(distinct entity_id) FROM epm_staging.stg_gl_entries "
            "WHERE erp_source = {erp:String}",
            {"param_erp": erp_source},
        )
        return int(res or 0)
    except Exception:
        frappe.log_error(
            title="Connector Health: entities_loaded query failed",
            message=frappe.get_traceback(),
        )
        return 0


def _derive(connector, now):
    """Return the derived health fields for one Connector doc."""
    last_at = get_datetime(connector.last_sync_at) if connector.last_sync_at else None
    raw_status = connector.last_sync_status or ""
    freq = connector.sync_frequency_minutes or 0

    lag_minutes = int((now - last_at).total_seconds() // 60) if last_at else 0

    if raw_status == "Running":
        status = "Running"
    elif last_at is None:
        status = "Never"
    elif raw_status == "Failed":
        status = "Failed"
    elif freq and lag_minutes > freq:
        status = "Stale"
    else:
        status = _STATUS_MAP.get(raw_status, "Succeeded")

    last_error = ""
    if status == "Stale":
        last_error = f"No successful sync for {lag_minutes} min (threshold {freq})."
    elif status == "Failed":
        last_error = "Last Airbyte sync reported Failed."

    return {
        "erp_source": connector.erp_type,
        "last_sync_status": status,
        "lag_minutes": lag_minutes,
        "entities_loaded": _entities_loaded(connector.erp_type),
        "rows_emitted": connector.last_sync_rows or 0,
        "last_sync_end": last_at,
        "checked_at": now,
        "last_error": last_error,
    }


def _alert_recipients():
    users = set()
    for role in ("System Manager", "EPM Admin"):
        users.update(
            frappe.get_all(
                "Has Role",
                filters={"role": role, "parenttype": "User"},
                pluck="parent",
            )
        )
    return [u for u in users if u not in ("Administrator", "Guest")]


def _notify(doc):
    """Raise a persistent Frappe Notification for each operator."""
    subject = f"Connector {doc.connector} is {doc.last_sync_status}"
    for user in _alert_recipients():
        frappe.get_doc({
            "doctype": "Notification Log",
            "subject": subject,
            "email_content": doc.last_error or subject,
            "type": "Alert",
            "document_type": "Connector Health",
            "document_name": doc.name,
            "for_user": user,
        }).insert(ignore_permissions=True)


def refresh_connector_health():
    """Scheduler entry: upsert one Connector Health row per enabled Connector.

    Alerts fire only on the transition INTO an unhealthy state, so a connector
    that stays Failed/Stale does not re-notify every cycle.
    """
    if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
        return
    if not frappe.db.table_exists("Connector"):
        return

    now = now_datetime()
    names = frappe.get_all("Connector", filters={"enabled": 1}, pluck="name")
    for name in names:
        connector = frappe.get_doc("Connector", name)
        vals = _derive(connector, now)

        if frappe.db.exists("Connector Health", name):
            doc = frappe.get_doc("Connector Health", name)
            prev_status = doc.last_sync_status
        else:
            doc = frappe.new_doc("Connector Health")
            doc.connector = name
            prev_status = None

        doc.update(vals)
        doc.flags.ignore_permissions = True
        doc.save()

        if doc.last_sync_status in _UNHEALTHY and prev_status not in _UNHEALTHY:
            _notify(doc)

    frappe.db.commit()
