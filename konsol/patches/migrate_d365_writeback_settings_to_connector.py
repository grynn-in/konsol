"""Migrate legacy D365 write-back credentials from EPM Settings to Connector.

EPM Settings stored a single global D365 OAuth profile for budget write-back.
Connector v2 stores per-connector write-back credentials (separate from extract).

This patch copies configured values into the first ``d365_fo`` Connector, or
creates a disabled placeholder named ``D365 F&O (migrated)`` when none exists.
Only empty Connector fields are overwritten so manual setup is preserved.
"""
import frappe

from konsol.connector_credentials import CONNECTOR_SECRET_FIELDS


def _set_if_empty(doc, field, value):
    if value in (None, ""):
        return
    if getattr(doc, field, None) in (None, ""):
        setattr(doc, field, value)


def execute():
    if not frappe.db.table_exists("Connector"):
        return

    settings = frappe.get_single("EPM Settings")
    tenant_id = (settings.d365_tenant_id or "").strip()
    resource_url = (settings.d365_resource_url or "").strip().rstrip("/")
    client_id = (settings.d365_client_id or "").strip()
    client_secret = settings.get_password("d365_client_secret", raise_exception=False) or ""

    if not (tenant_id and resource_url and client_id and client_secret):
        return

    matches = frappe.get_all(
        "Connector",
        filters={"erp_type": "d365_fo"},
        pluck="name",
        limit=1,
    )
    if matches:
        doc = frappe.get_doc("Connector", matches[0])
    else:
        doc = frappe.new_doc("Connector")
        doc.connector_name = "D365 F&O (migrated)"
        doc.erp_type = "d365_fo"
        doc.enabled = 0

    _set_if_empty(doc, "tenant_id", tenant_id)
    _set_if_empty(doc, "environment_url", resource_url)

    if settings.enable_d365_budget_writeback and not doc.writeback_enabled:
        doc.writeback_enabled = 1

    doc.writeback_credentials_separate = 1
    _set_if_empty(doc, "writeback_client_id", client_id)

    if not _doc_password_set(doc, "writeback_client_secret"):
        doc.writeback_client_secret = client_secret

    fiscal_month = int(settings.d365_fiscal_year_start_month or 1)
    if not doc.writeback_fiscal_year_start_month:
        doc.writeback_fiscal_year_start_month = fiscal_month

    doc.save(ignore_permissions=True)
    frappe.db.commit()


def _doc_password_set(doc, field):
    if field not in CONNECTOR_SECRET_FIELDS:
        return False
    return bool(doc.get_password(field, raise_exception=False))