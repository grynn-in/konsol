"""Connector extract / writeback credential profiles.

Pure helpers used by the Connector DocType controller, config export, and (later)
Airbyte provisioning and ERP write-back modules. Secrets are read via Frappe
Password fields and are never included in GitOps export bundles.
"""
from __future__ import annotations

D365_ERP_TYPES = frozenset({"d365_fo", "d365_bc"})
ERP_NEXT_ERP_TYPES = frozenset({"erpnext"})

EXTRACT_SECRET_FIELDS = frozenset(
    {"extract_client_secret", "extract_api_secret"},
)
WRITEBACK_SECRET_FIELDS = frozenset(
    {"writeback_client_secret", "writeback_api_secret"},
)
CONNECTOR_SECRET_FIELDS = EXTRACT_SECRET_FIELDS | WRITEBACK_SECRET_FIELDS

CONNECTOR_EXPORTABLE_FIELDS = frozenset(
    {
        "tenant_id",
        "environment_url",
        "host_url",
        "extract_client_id",
        "extract_api_key",
        "extract_page_size",
        "extract_cross_company",
        "writeback_enabled",
        "writeback_credentials_separate",
        "writeback_client_id",
        "writeback_api_key",
        "writeback_fiscal_year_start_month",
    },
)


def _doc_get(doc, field, default=None):
    return getattr(doc, field, default)


def _doc_password(doc, field):
    get_password = getattr(doc, "get_password", None)
    if not callable(get_password):
        return ""
    return get_password(field, raise_exception=False) or ""


def _is_d365(erp_type):
    return erp_type in D365_ERP_TYPES


def _is_erpnext(erp_type):
    return erp_type in ERP_NEXT_ERP_TYPES


def _resolve_auth_pair(doc, *, profile):
    """Return (principal_id, secret) for extract or writeback, with optional inherit."""
    erp_type = _doc_get(doc, "erp_type")
    separate = bool(_doc_get(doc, "writeback_credentials_separate"))

    if profile == "extract":
        if _is_d365(erp_type):
            return (
                _doc_get(doc, "extract_client_id", "") or "",
                _doc_password(doc, "extract_client_secret"),
            )
        if _is_erpnext(erp_type):
            return (
                _doc_get(doc, "extract_api_key", "") or "",
                _doc_password(doc, "extract_api_secret"),
            )
        return "", ""

    if profile == "writeback":
        if _is_d365(erp_type):
            if separate:
                return (
                    _doc_get(doc, "writeback_client_id", "") or "",
                    _doc_password(doc, "writeback_client_secret"),
                )
            return (
                _doc_get(doc, "extract_client_id", "") or "",
                _doc_password(doc, "extract_client_secret"),
            )
        if _is_erpnext(erp_type):
            if separate:
                return (
                    _doc_get(doc, "writeback_api_key", "") or "",
                    _doc_password(doc, "writeback_api_secret"),
                )
            return (
                _doc_get(doc, "extract_api_key", "") or "",
                _doc_password(doc, "extract_api_secret"),
            )
        return "", ""

    raise ValueError(f"Unknown credential profile: {profile}")


def credentials_configured(doc, profile):
    """True when the connector has enough non-secret + secret fields for a profile."""
    erp_type = _doc_get(doc, "erp_type")
    principal, secret = _resolve_auth_pair(doc, profile=profile)

    if profile == "writeback" and not bool(_doc_get(doc, "writeback_enabled")):
        return False

    if _is_d365(erp_type):
        if not (_doc_get(doc, "tenant_id") and _doc_get(doc, "environment_url")):
            return False
        return bool(principal and secret)

    if _is_erpnext(erp_type):
        if not _doc_get(doc, "host_url"):
            return False
        return bool(principal and secret)

    return False


def writeback_inherits_extract_credentials(doc):
    """True when writeback reuses the extract principal (dev-friendly default)."""
    if not bool(_doc_get(doc, "writeback_enabled")):
        return False
    return not bool(_doc_get(doc, "writeback_credentials_separate"))


def build_extract_config(doc):
    """Build an Airbyte-source-shaped config dict, or None if unsupported / incomplete."""
    erp_type = _doc_get(doc, "erp_type")
    principal, secret = _resolve_auth_pair(doc, profile="extract")
    page_size = int(_doc_get(doc, "extract_page_size") or 100)

    if _is_d365(erp_type):
        tenant_id = _doc_get(doc, "tenant_id", "") or ""
        environment_url = (_doc_get(doc, "environment_url", "") or "").rstrip("/")
        if not (tenant_id and environment_url and principal and secret):
            return None
        return {
            "tenant_id": tenant_id,
            "client_id": principal,
            "client_secret": secret,
            "environment_url": environment_url,
            "page_size": page_size,
            "cross_company": bool(int(_doc_get(doc, "extract_cross_company") or 1)),
        }

    if _is_erpnext(erp_type):
        host_url = (_doc_get(doc, "host_url", "") or "").rstrip("/")
        if not (host_url and principal and secret):
            return None
        return {
            "host_url": host_url,
            "api_key": principal,
            "api_secret": secret,
            "page_size": page_size,
        }

    return None


def build_writeback_config(doc):
    """Build a write-back runtime config dict, or None if disabled / incomplete."""
    if not bool(_doc_get(doc, "writeback_enabled")):
        return None

    erp_type = _doc_get(doc, "erp_type")
    principal, secret = _resolve_auth_pair(doc, profile="writeback")

    if _is_d365(erp_type):
        tenant_id = _doc_get(doc, "tenant_id", "") or ""
        resource_url = (_doc_get(doc, "environment_url", "") or "").rstrip("/")
        if not (tenant_id and resource_url and principal and secret):
            return None
        return {
            "enabled": True,
            "erp_type": erp_type,
            "connector_name": _doc_get(doc, "connector_name"),
            "connector_id": _doc_get(doc, "name"),
            "resource_url": resource_url,
            "tenant_id": tenant_id,
            "client_id": principal,
            "client_secret": secret,
            "fiscal_year_start_month": int(
                _doc_get(doc, "writeback_fiscal_year_start_month") or 1
            ),
            "credentials_separate": bool(_doc_get(doc, "writeback_credentials_separate")),
            "inherits_extract_credentials": writeback_inherits_extract_credentials(doc),
        }

    if _is_erpnext(erp_type):
        host_url = (_doc_get(doc, "host_url", "") or "").rstrip("/")
        if not (host_url and principal and secret):
            return None
        return {
            "enabled": True,
            "erp_type": erp_type,
            "connector_name": _doc_get(doc, "connector_name"),
            "connector_id": _doc_get(doc, "name"),
            "host_url": host_url,
            "api_key": principal,
            "api_secret": secret,
            "credentials_separate": bool(_doc_get(doc, "writeback_credentials_separate")),
            "inherits_extract_credentials": writeback_inherits_extract_credentials(doc),
        }

    return None


def connector_export_row(doc):
    """Non-secret connector fields safe for GitOps export."""
    row = {}
    for field in sorted(CONNECTOR_EXPORTABLE_FIELDS):
        if hasattr(doc, field):
            value = _doc_get(doc, field)
            if value is not None and value != "":
                row[field] = value
    row["writeback_enabled"] = bool(_doc_get(doc, "writeback_enabled"))
    row["writeback_credentials_separate"] = bool(
        _doc_get(doc, "writeback_credentials_separate")
    )
    row["extract_cross_company"] = bool(int(_doc_get(doc, "extract_cross_company") or 0))
    row["extract_credentials_configured"] = credentials_configured(doc, "extract")
    row["writeback_credentials_configured"] = credentials_configured(doc, "writeback")
    row["writeback_inherits_extract_credentials"] = writeback_inherits_extract_credentials(
        doc
    )
    return row


def find_connector_for_entity(entity_id, *, erp_type=None):
    """Resolve an enabled Connector serving ``entity_id`` (optional erp_type filter)."""
    import frappe

    if not entity_id or not frappe.db.table_exists("Connector"):
        return None

    filters = {"enabled": 1}
    if erp_type:
        filters["erp_type"] = erp_type

    for name in frappe.get_all("Connector", filters=filters, pluck="name"):
        doc = frappe.get_doc("Connector", name)
        for row in doc.legal_entities or []:
            if row.entity_id == entity_id:
                return doc
    return None