"""The ISO 4217 list konsol ships, and each currency's magnitude reference.

``konsol/reference_data/iso_currencies.json`` holds the codes, names, symbols,
ISO exponents and ``usd_log10`` (roughly log10 of units per 1 USD, the group
exchange rate guard's reference; konsol#103, #138). It is SEEDED, not a
fixture: Frappe re-imports ``konsol/fixtures/`` with force on every migrate,
deleting and re-inserting each row, which reverted a site's own
``usd_log10`` (review of #174). ``seed_iso_currencies`` inserts a code a site
lacks and fills a value that is unset. It never overwrites a value that is set,
so a site's edit survives every migrate. It runs in the rate adoption patch
(before it adopts), after_migrate and after_sync (a fresh install).

The references are rough by design, about +/-0.05 from typical 2024-25 market
levels, never quotes. The guard refuses only a rate more than 10x from what
they imply.

Unset means what the warehouse's rule says (konsolidat macros/fx_magnitude.sql):
NULL, NaN, or 0 for any currency but USD. USD is the anchor and really is 0.
A currency pegged 1:1 to the dollar (PAB, BSD, BMD) really is 0 too, so it
ships as 0.001: a thousandth of a decade (0.2%) is negligible to a 10x check,
and it keeps the value "set".

One reference per currency serves every period. A currency whose value moved
more than 10x over the ledgers' history (ARS, LBP) needs a person to update it
as it goes; see the PR notes.
"""
import json
import math
import os

import frappe

REFERENCE_CURRENCY = "USD"
REFERENCE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_data", "iso_currencies.json")
FIELDS = ("currency_code", "currency_name", "symbol", "minor_unit", "usd_log10")


def reference_rows():
    """The shipped ISO list, one dict per currency."""
    with open(REFERENCE_FILE) as f:
        return json.load(f)


def usd_log10_defaults():
    return {r["currency_code"]: r["usd_log10"] for r in reference_rows()}


def is_unset(code, value):
    """The warehouse's "no reference" rule: NULL, NaN, or 0 for any currency but USD."""
    if value in (None, ""):
        return True
    value = float(value)
    return math.isnan(value) or (value == 0 and code != REFERENCE_CURRENCY)


def seed_iso_currencies(rows=None):
    """Insert the codes a site lacks; fill ``usd_log10`` where it is unset.

    Never overwrites a value that is set, so a site's own edit survives.
    Idempotent. Needs the ``usd_log10`` column: the adoption patch reloads
    the doctype first; after_migrate and after_sync run after model sync.
    Returns {"inserted": [...], "filled": [...]}."""
    rows = reference_rows() if rows is None else rows
    have = {r.name: r for r in frappe.get_all("ISO Currency", fields=["name", "usd_log10"],
                                              limit_page_length=0)}
    out = {"inserted": [], "filled": []}
    for row in rows:
        code = row["currency_code"]
        if code not in have:
            doc = frappe.get_doc(dict({"doctype": "ISO Currency"}, **{k: row[k] for k in FIELDS}))
            doc.insert(ignore_permissions=True)
            out["inserted"].append(code)
        elif is_unset(code, have[code].usd_log10) and not is_unset(code, row["usd_log10"]):
            # update_modified=False: a seed is not an edit
            frappe.db.set_value("ISO Currency", code, "usd_log10", row["usd_log10"], update_modified=False)
            out["filled"].append(code)
    return out
