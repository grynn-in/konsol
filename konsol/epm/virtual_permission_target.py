"""Helpers for read-only Virtual DocTypes used as budget permission targets.

Spec grynn-in/konsolidat#51 §B. These doctypes proxy a list of string values
from the dbt/ClickHouse layer (account categories, dimension values) live, so
admins can grant ownership with native Frappe User Permissions WITHOUT syncing
a copy into Frappe. The values are the documents; the document `name` is the
value.

Each concrete controller must define the read-only instance methods on ITSELF
(not inherit them) — Frappe's virtual-doctype validator compares against
`mro()[1]`, so an inherited base method would be flagged as "not overridden".
Hence the shared *logic* lives in these module functions and each controller
holds thin wrappers. The static get_list/get_count/get_stats are not subject to
that check, but must be `staticmethod` and so are defined per controller too
(they need the controller's own value source).
"""
import frappe
from frappe.model.document import Document


def readonly_guard(doc):
    """Reject any write — the master lives in ClickHouse, not Frappe."""
    frappe.throw(
        frappe._("{0} is read-only (sourced from ClickHouse).").format(doc.doctype),
        frappe.ValidationError,
    )


def load_value(doc):
    """Hydrate a single proxy document: it simply *is* its name."""
    super(Document, doc).__init__({"name": doc.name, "value": doc.name})


def _filtered(values, args):
    """Apply the link-picker text search to a list of string values."""
    args = frappe._dict(args or {})
    txt = (args.get("txt") or "").strip().lower()
    if txt:
        values = [v for v in values if txt in (v or "").lower()]
    return values


def proxy_get_list(values, args):
    args = frappe._dict(args or {})
    values = _filtered(values, args)
    start = int(args.get("start") or 0)
    page_length = int(args.get("page_length") or 0)
    page = values[start:start + page_length] if page_length else values[start:]
    return [frappe._dict(name=v, value=v) for v in page]


def proxy_get_count(values, args):
    return len(_filtered(values, args))


def proxy_get_stats(args):
    return {}


def distinct_ch_values(sql):
    """Run a single-column ClickHouse query and return the non-empty values.

    Best-effort: never raises into the desk (an unreachable warehouse yields an
    empty picker, not a 500). The SQL must select one column and end with
    ``FORMAT TabSeparated``.
    """
    try:
        from konsol.clickhouse import execute

        raw = execute(sql)
    except Exception:  # noqa: BLE001 - picker must degrade gracefully
        frappe.log_error("virtual_permission_target value fetch failed", frappe.get_traceback())
        return []
    return [ln for ln in (raw or "").splitlines() if ln]
