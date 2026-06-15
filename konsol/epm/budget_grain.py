"""Budget grain helpers — the dimensional unique key for Budget Input.

Spec: grynn-in/konsolidat#51. The budget grain is the fixed keys
(scenario, data_area/LE, fiscal_year, main_account) PLUS every Dimension
flagged ``in_budget`` (Published). Centralised here so the upsert lookup
(``konsol.api._budget_filters``), the document autoname
(``BudgetInput.autoname``) and the ClickHouse incremental-sync key all agree
on the same grain — two writers differing only by a budget dimension (e.g.
cost center on a shared Travel account) resolve to *different* docs instead of
silently clobbering each other.
"""
import re

import frappe

# Fixed (non-dimension) components of the budget key, in name order.
FIXED_KEYS = ("scenario_id", "data_area_id", "fiscal_year", "main_account")


def budget_dimension_names():
    """Ordered list of ``in_budget`` Published Dimension names (the grain dims).

    Ordered by ``dimension_name`` so the derived key and document name are
    deterministic regardless of registry insertion order.
    """
    return [
        d.dimension_name
        for d in frappe.get_all(
            "Dimension",
            filters={"in_budget": 1, "status": "Published"},
            fields=["dimension_name"],
            order_by="dimension_name asc",
            limit_page_length=0,
        )
    ]


def budget_key_fields():
    """All key field names in name order: fixed keys + grain dimensions."""
    return [*FIXED_KEYS, *budget_dimension_names()]


def _slug(value):
    """Sanitise one key component for use in a Frappe document name.

    Frappe names cannot contain ``/`` and are capped at 140 chars. A blank
    dimension value (a dimension that does not apply to this line — e.g. cost
    center on a revenue account) collapses to ``_`` so the name stays
    well-formed and the grain remains unambiguous.
    """
    s = str(value if value is not None else "").strip()
    if not s:
        return "_"
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)


def budget_name(values):
    """Build the deterministic Budget Input name from a values dict.

    ``values`` must provide the fixed keys and every grain dimension. Shared by
    ``BudgetInput.autoname()`` and the grain migration patch so both produce
    byte-identical names.
    """
    parts = [
        values.get("scenario_id"),
        values.get("data_area_id"),
        int(values.get("fiscal_year") or 0),
        values.get("main_account"),
    ]
    parts += [values.get(dim, "") for dim in budget_dimension_names()]
    return "BUD-" + "-".join(_slug(p) for p in parts)
