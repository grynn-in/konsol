"""Resolve D365 write-back runtime configuration from Connector or EPM Settings."""
from __future__ import annotations

from konsol.connector_credentials import build_writeback_config, find_connector_for_entity

REQUIRED_KEYS = frozenset({"resource_url", "tenant_id", "client_id", "client_secret"})


def _normalize_d365_writeback_cfg(
    *,
    enabled,
    resource_url,
    tenant_id,
    client_id,
    client_secret,
    fiscal_year_start_month,
    source,
    connector_name=None,
    connector_id=None,
):
    return {
        "enabled": bool(enabled),
        "resource_url": (resource_url or "").rstrip("/"),
        "tenant_id": tenant_id or "",
        "client_id": client_id or "",
        "client_secret": client_secret or "",
        "fiscal_year_start_month": int(fiscal_year_start_month or 1),
        "source": source,
        "connector_name": connector_name,
        "connector_id": connector_id,
    }


def _connector_writeback_config(entity_id=None):
    """Return a normalized D365 write-back config from Connector, or None."""
    import frappe

    if not frappe.db.table_exists("Connector"):
        return None

    connector = None
    if entity_id:
        connector = find_connector_for_entity(entity_id, erp_type="d365_fo")
        if not connector:
            return None
    else:
        names = frappe.get_all(
            "Connector",
            filters={
                "enabled": 1,
                "erp_type": "d365_fo",
                "writeback_enabled": 1,
            },
            pluck="name",
            order_by="connector_name asc",
            limit_page_length=0,
        )
        for name in names:
            doc = frappe.get_doc("Connector", name)
            if build_writeback_config(doc):
                connector = doc
                break

    if not connector:
        return None

    raw = build_writeback_config(connector)
    if not raw:
        return None

    return _normalize_d365_writeback_cfg(
        enabled=raw.get("enabled"),
        resource_url=raw.get("resource_url"),
        tenant_id=raw.get("tenant_id"),
        client_id=raw.get("client_id"),
        client_secret=raw.get("client_secret"),
        fiscal_year_start_month=raw.get("fiscal_year_start_month"),
        source="connector",
        connector_name=raw.get("connector_name"),
        connector_id=raw.get("connector_id"),
    )


def _epm_settings_writeback_config():
    """Legacy global D365 write-back settings (migration shim)."""
    import frappe

    settings = frappe.get_single("EPM Settings")
    return _normalize_d365_writeback_cfg(
        enabled=bool(getattr(settings, "enable_d365_budget_writeback", 0)),
        resource_url=getattr(settings, "d365_resource_url", "") or "",
        tenant_id=getattr(settings, "d365_tenant_id", "") or "",
        client_id=getattr(settings, "d365_client_id", "") or "",
        client_secret=settings.get_password("d365_client_secret", raise_exception=False)
        or "",
        fiscal_year_start_month=getattr(settings, "d365_fiscal_year_start_month", 1),
        source="epm_settings",
    )


def resolve_d365_writeback_config(entity_id=None):
    """Prefer Connector write-back profile; fall back to EPM Settings."""
    connector_cfg = _connector_writeback_config(entity_id)
    if connector_cfg and connector_cfg.get("enabled"):
        return connector_cfg

    legacy_cfg = _epm_settings_writeback_config()
    if connector_cfg and not legacy_cfg.get("enabled"):
        # Connector exists for the entity but write-back is disabled there and
        # legacy settings are off — honour the explicit Connector choice.
        return connector_cfg
    return legacy_cfg


def writeback_config_is_complete(cfg):
    return bool(cfg.get("enabled")) and all(cfg.get(key) for key in REQUIRED_KEYS)