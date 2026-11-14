"""Budget grain helpers — the dimensional unique key for budgets.

Spec: grynn-in/konsolidat#51. The budget grain is the fixed keys
(scenario, data_area/LE, fiscal_year, main_account) PLUS every Dimension
flagged ``in_budget`` (Published). Centralised here so every reader of the
grain agrees on it — two writers differing only by a budget dimension (e.g.
cost center on a shared Travel account) resolve to *different* rows instead of
silently clobbering each other. ``digest_name`` is the collision-safe document
name several doctypes share.
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
    """Build the deterministic, injective legacy ``Budget Input`` document name.

    ``values`` must provide the fixed keys and every grain dimension. Kept for
    the historical ``rekey_budget_input_dimensional_grain`` patch (the Budget
    Input doctype itself is retired, and its ``autoname()`` produced the same
    names). The readable head is for humans; an 8-char digest of
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


def digest_name(prefix, parts):
    """Collision-safe, length-capped document name: ``PREFIX-<slugged>-<sha8>``.

    ``parts`` is an ordered list of key components. The readable head is for
    humans (slugged, ``-``-joined); the 8-char sha1 digest of the *exact*
    component tuple guarantees two distinct grains never collide even if their
    slugs collapse or the readable head is truncated at the 140-char cap.
    Used by Budget Cycle, Budget Entry and Historical Equity Rate autoname so
    realistic long entity / scenario codes can't truncate two different grains
    onto one name.
    """
    readable = prefix + "-" + "-".join(_slug(p) for p in parts)
    canonical = "\x1f".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:_DIGEST_LEN]
    head = readable[: _NAME_MAX - _DIGEST_LEN - 1]
    return f"{head}-{digest}"

