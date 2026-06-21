"""Budget grain helpers — the dimensional unique key for budgets.

Spec: grynn-in/konsolidat#51. The budget grain is the fixed keys
(scenario, data_area/LE, fiscal_year, main_account) PLUS every Dimension
flagged ``in_budget`` (Published). Centralised here so the API line upsert
(``konsol.api._line_identity`` / ``find_budget_line``), the migration pivot and
the ClickHouse incremental-sync key all agree on the same grain — two writers
differing only by a budget dimension (e.g. cost center on a shared Travel
account) resolve to *different* lines instead of silently clobbering each other.
"""
import hashlib
import re

import frappe

# Fixed (non-dimension) components of the budget key, in name order.
FIXED_KEYS = ("scenario_id", "data_area_id", "fiscal_year", "main_account")

# Canonical additive budget layers (final budget = sum of layers).
VALID_LAYERS = ("base", "challenge", "management", "board")


def normalize_layer(value):
    """Canonicalise a layer value: trimmed lowercase. Empty → 'base'.

    Both the API write paths and the migration funnel through this so a sheet's
    (cycle, entity, layer) grain never splits on case/whitespace (e.g. legacy
    'Base' vs API 'base').
    """
    return (str(value or "").strip().lower()) or "base"


def is_valid_layer(value):
    return normalize_layer(value) in VALID_LAYERS


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


def digest_name(prefix, parts):
    """Collision-safe, length-capped document name: ``PREFIX-<slugged>-<sha8>``.

    ``parts`` is an ordered list of key components. The readable head is for
    humans (slugged, ``-``-joined); the 8-char sha1 digest of the *exact*
    component tuple guarantees two distinct grains never collide even if their
    slugs collapse or the readable head is truncated at the 140-char cap.
    Used by Budget Cycle / Budget Sheet autoname so realistic long entity /
    scenario codes can't truncate two different grains onto one name.
    """
    readable = prefix + "-" + "-".join(_slug(p) for p in parts)
    canonical = "\x1f".join("" if p is None else str(p) for p in parts)
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:_DIGEST_LEN]
    head = readable[: _NAME_MAX - _DIGEST_LEN - 1]
    return f"{head}-{digest}"


def line_matches(line, ident, dims):
    """True if a Budget Line row matches the (main_account + dims) identity."""
    if line.main_account != ident["main_account"]:
        return False
    return all((line.get(d) or "") == (ident.get(d, "") or "") for d in dims)


def find_budget_line(sheet, ident, dims, append=False):
    """Return the sheet's Budget Line matching ``ident`` (account + dims).

    Single source of truth for the line-grain match, shared by the API upsert,
    the cell-conflict read and the migration pivot so they can never disagree on
    whether two writes target the same line. ``dims`` is passed in (resolved once
    per request) to avoid a per-cell ``budget_dimension_names()`` query. With
    ``append=True`` a missing line is appended and returned; otherwise ``None``.
    """
    for line in sheet.lines:
        if line_matches(line, ident, dims):
            return line
    return sheet.append("lines", dict(ident)) if append else None
