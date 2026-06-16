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
import hashlib
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
    """Sanitise one key component for the readable part of a document name.

    ``-`` is the field separator, so it (and ``.``) must NOT survive inside a
    component — otherwise account ``X`` + cost center ``Y-Z`` and account
    ``X-Y`` + cost center ``Z`` would both render ``...-X-Y-Z`` and collide.
    Everything outside ``[A-Za-z0-9_]`` collapses to ``_``; a blank value (a
    dimension that doesn't apply to this line, e.g. cost center on a revenue
    account) becomes ``_``.
    """
    s = str(value if value is not None else "").strip()
    if not s:
        return "_"
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


# Length budget for a Frappe document name (hard cap is 140).
_NAME_MAX = 140
_DIGEST_LEN = 8


def budget_name(values):
    """Build the deterministic, injective Budget Input name from a values dict.

    ``values`` must provide the fixed keys and every grain dimension. Shared by
    ``BudgetInput.autoname()`` and the grain migration patch so both produce
    byte-identical names. The readable head is for humans; an 8-char digest of
    the *exact* (un-slugged) key tuple guarantees two distinct keys never share
    a name even if their slugs collapse to the same string.
    """
    ordered = [
        ("scenario_id", values.get("scenario_id")),
        ("data_area_id", values.get("data_area_id")),
        ("fiscal_year", int(values.get("fiscal_year") or 0)),
        ("main_account", values.get("main_account")),
    ]
    ordered += [(dim, values.get(dim, "")) for dim in budget_dimension_names()]

    readable = "BUD-" + "-".join(_slug(v) for _, v in ordered)
    # Canonical tuple with a non-printable delimiter (US, 0x1f) that no field
    # value can contain — so the digest is a true function of the key tuple.
    canonical = "\x1f".join(f"{k}={'' if v is None else v}" for k, v in ordered)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:_DIGEST_LEN]

    head = readable[: _NAME_MAX - _DIGEST_LEN - 1]
    return f"{head}-{digest}"
