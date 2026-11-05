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

Unset means what the warehouse's rule says (konsolidat macros/fx_magnitude.sql),
defined once in konsol.fx_reference: NULL, NaN, or 0 for any currency but USD. USD is the anchor and really is 0.
A currency pegged 1:1 to the dollar (PAB, BSD, BMD) really is 0 too, so it
ships as 0.001: a thousandth of a decade (0.2%) is negligible to a 10x check,
and it keeps the value "set".

One reference per currency serves every period. A currency whose value moved
more than 10x over the ledgers' history (ARS, LBP) needs a person to update it
as it goes; see the PR notes.
"""
import json
import os

import frappe

from konsol.fx_reference import REFERENCE_CURRENCY, is_unset  # noqa: F401 — the one rule
from konsol import iso_currency_model
from konsol.iso_currency_model import minor_unit_problem

REFERENCE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_data", "iso_currencies.json")
FIELDS = ("currency_code", "currency_name", "symbol", "minor_unit", "usd_log10")


def reference_rows():
    """The shipped ISO list, one dict per currency."""
    with open(REFERENCE_FILE) as f:
        return json.load(f)


def listed_minor_units():
    """{code: minor_unit} from the shipped list: the authority for
    ISO Currency.minor_unit (konsol#330). None: ISO 4217 sets no minor unit
    for the code (N.A.), so a site declares one (iso_currency_model)."""
    return {r["currency_code"]: r["minor_unit"] for r in reference_rows()}


def _stored():
    return frappe.get_all("ISO Currency", fields=["name", "usd_log10", "minor_unit", "minor_unit_declared"],
                          limit_page_length=0)


#: Where a currency is used: (doctype, field, filters, label). A number is
#: computed in each, so a wrong minor unit there makes the number wrong.
#: A group's reporting currency is read from its node with no entity
#: (consolidation_journal._root), so that node is the root here. Documents
#: count once submitted; a child row counts under a submitted parent of its
#: own doctype (Frappe keeps a child's docstatus equal to its parent's).
#: Business Combination Acquired Balance links no currency, so it is absent.
#: Historical Equity Rate's from/to currency are virtual (#373): computed from
#: the Entity's functional currency and the group root's reporting currency,
#: both counted above, and get_all cannot read a virtual field.
_SUBMITTED = {"docstatus": 1}
_USES = (
    ("Entity", "functional_currency", None, "Entity"),
    ("Consolidation Group", "reporting_currency", {"data_area_id": ["is", "not set"]},
     "Consolidation Group"),
    ("Group Exchange Rate", "from_currency", _SUBMITTED, "Group Exchange Rate"),
    ("Group Exchange Rate", "to_currency", _SUBMITTED, "Group Exchange Rate"),
    ("Trial Balance Submission", "currency", _SUBMITTED, "Trial Balance Submission"),
    ("Consolidation Journal", "currency", _SUBMITTED, "Consolidation Journal"),
    ("Business Combination", "consideration_currency", _SUBMITTED, "Business Combination"),
    ("Business Combination Consideration", "currency",
     {"docstatus": 1, "parenttype": "Business Combination"}, "Business Combination"),
    ("Business Combination Cost", "currency",
     {"docstatus": 1, "parenttype": "Business Combination"}, "Business Combination"),
    ("Business Disposal", "proceeds_currency", _SUBMITTED, "Business Disposal"),
    ("Business Disposal Proceeds", "currency",
     {"docstatus": 1, "parenttype": "Business Disposal"}, "Business Disposal"),
)


def used_currencies():
    """{code: [where, ...]} for every currency a number is computed in: an
    Entity's functional currency, a Consolidation Group root's reporting
    currency, and the currencies of a submitted Group Exchange Rate,
    Trial Balance Submission, Consolidation Journal,
    Business Combination or Business Disposal (PR #384 re-review)."""
    used = {}
    for doctype, field, filters, label in _USES:
        for code in frappe.get_all(doctype, filters=dict(filters or {}), pluck=field, distinct=True,
                                   limit_page_length=0):
            if code:
                used.setdefault(code, set()).add(label)
    return {c: sorted(w) for c, w in used.items()}


def minor_unit_gaps():
    """{"blocking", "reported"} (iso_currency_model.minor_unit_gaps): a stored
    ISO Currency the konsol#330 rule refuses blocks sign-off when it is used,
    and is reported (My work, Error Log) when it is not."""
    return iso_currency_model.minor_unit_gaps(
        [(r.name, r.minor_unit, r.minor_unit_declared) for r in _stored()], listed_minor_units(),
        used_currencies())


def minor_unit_gap():
    """The blocking gap only: used rows the rule refuses, or None. Read by
    the sign-off gate (PR #384 re-review)."""
    return minor_unit_gaps()["blocking"]


def usd_log10_defaults():
    return {r["currency_code"]: r["usd_log10"] for r in reference_rows()}


def seed_iso_currencies(rows=None):
    """Insert the codes a site lacks; fill ``usd_log10`` where it is unset.

    Never overwrites a value that is set, so a site's own edit survives.
    Idempotent. Needs the ``usd_log10`` column: the adoption patch reloads
    the doctype first; after_migrate and after_sync run after model sync.
    A stored row the ISO Currency rule refuses (a code not on the list, or a
    Minor Unit that differs from the list's, konsol#330) is not changed: it
    is returned in "refused" so the migrate names it. A listed code ISO
    gives no minor unit (N.A.) is never inserted.
    Returns {"inserted": [...], "filled": [...], "refused": [...]}."""
    rows = reference_rows() if rows is None else rows
    have = {r.name: r for r in _stored()}
    listed = listed_minor_units()
    out = {"inserted": [], "filled": [],
           "refused": sorted(c for c, r in have.items()
                             if minor_unit_problem(c, r.minor_unit, listed, bool(r.minor_unit_declared)))}
    for row in rows:
        code = row["currency_code"]
        if code not in have and row["minor_unit"] is None:
            # ISO 4217 sets no minor unit (N.A.): the column would store 0.
            # A site that uses the code creates it and declares its own.
            continue
        if code not in have:
            doc = frappe.get_doc(dict({"doctype": "ISO Currency"}, **{k: row[k] for k in FIELDS}))
            doc.insert(ignore_permissions=True)
            out["inserted"].append(code)
        elif is_unset(code, have[code].usd_log10) and not is_unset(code, row["usd_log10"]):
            # update_modified=False: a seed is not an edit
            frappe.db.set_value("ISO Currency", code, "usd_log10", row["usd_log10"], update_modified=False)
            out["filled"].append(code)
    return out
