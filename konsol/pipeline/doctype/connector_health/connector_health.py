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


def _derive_status(raw_status, last_at, now, freq):
    """Pure status/lag/error derivation (no Frappe/ClickHouse) — unit-testable.

    Returns (status, lag_minutes, last_error). A sync stuck in ``Running`` past
    the staleness threshold is treated as Stale — a never-completing sync is
    exactly the silent failure this dashboard exists to surface.
    """
    lag_minutes = int((now - last_at).total_seconds() // 60) if last_at else 0
    stale = bool(freq) and last_at is not None and lag_minutes > freq

    if raw_status == "Running":
        if stale:
            return "Stale", lag_minutes, (
                f"Sync stuck in Running for {lag_minutes} min (threshold {freq})."
            )
        return "Running", lag_minutes, ""
    if last_at is None:
        return "Never", lag_minutes, ""
    if raw_status == "Failed":
        return "Failed", lag_minutes, "Last Airbyte sync reported Failed."
    if stale:
        return "Stale", lag_minutes, (
            f"No successful sync for {lag_minutes} min (threshold {freq})."
        )
    return _STATUS_MAP.get(raw_status, "Succeeded"), lag_minutes, ""


def _entities_loaded(erp_source, entity_ids=None):
    """count(distinct entity_id) loaded for this connector in canonical staging.

    Scoped to the connector's own legal entities when defined, so multiple
    connectors sharing an erp_source don't each report the full erp_source
    total. Best-effort: returns 0 if ClickHouse is unreachable/table absent, so
    a refresh never fails on a transient DB issue. Note 0 != unhealthy (a
    connector may sync only master data / no GL yet).
    """
    if not erp_source:
        return 0
    try:
        if entity_ids:
            arr = "[" + ",".join("'" + e.replace("'", "''") + "'" for e in entity_ids) + "]"
            res = clickhouse.execute(
                "SELECT count(distinct entity_id) FROM epm_staging.stg_gl_entries "
                "WHERE erp_source = {erp:String} AND entity_id IN {ents:Array(String)}",
                {"param_erp": erp_source, "param_ents": arr},
            )
        else:
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
    freq = connector.sync_frequency_minutes or 0
    status, lag_minutes, last_error = _derive_status(
        connector.last_sync_status or "", last_at, now, freq
    )
    entity_ids = [
        r.entity_id for r in (connector.get("legal_entities") or [])
        if getattr(r, "entity_id", None)
    ]
    return {
        "erp_source": connector.erp_type,
        "last_sync_status": status,
        "lag_minutes": lag_minutes,
        "entities_loaded": _entities_loaded(connector.erp_type, entity_ids),
        "rows_emitted": connector.last_sync_rows or 0,
        "last_sync_end": last_at,
        "checked_at": now,
        "last_error": last_error,
    }


def _alert_recipients():
    # Operators only: System Manager + EPM Admin. EPM Analyst/User can read the
    # dashboard but are not paged. Intentional asymmetry with the read matrix.
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


def _prune_orphans(enabled_names):
    """Drop Health rows for connectors no longer enabled / no longer existing,
    so the dashboard never shows a frozen status for a disabled connector."""
    for name in frappe.get_all("Connector Health", pluck="name"):
        if name not in enabled_names:
            frappe.delete_doc(
                "Connector Health", name, ignore_permissions=True, force=True
            )


def refresh_connector_health():
    """Scheduler entry: upsert one Connector Health row per enabled Connector.

    Each connector is processed independently (try/except + per-connector
    commit) so one failure cannot roll back the others. Alerts fire on the
    transition INTO an unhealthy state OR a change between unhealthy states
    (Failed<->Stale), never on an unchanged repeat.
    """
    if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
        return
    if not frappe.db.table_exists("Connector"):
        return

    now = now_datetime()
    enabled = frappe.get_all("Connector", filters={"enabled": 1}, pluck="name")
    for name in enabled:
        try:
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

            if doc.last_sync_status in _UNHEALTHY and doc.last_sync_status != prev_status:
                try:
                    _notify(doc)
                except Exception:
                    frappe.log_error(
                        title="Connector Health: notify failed",
                        message=frappe.get_traceback(),
                    )
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(
                title=f"Connector Health refresh failed: {name}",
                message=frappe.get_traceback(),
            )

    try:
        _prune_orphans(set(enabled))
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(
            title="Connector Health: prune failed",
            message=frappe.get_traceback(),
        )
