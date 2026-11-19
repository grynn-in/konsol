"""The input fingerprint of a Budget Cycle (konsol#348 S4 K8, BUD-11-3; S4-Q5 a,
chosen under the standing rule on 15 Nov 2026: a read-only field on the Cycle,
stamped at Approve; the pure model alone until BUD-4's Approved Budget was
rejected as a model nothing calls).

``compute`` reads what the Cycle approves and hands it to the pure model
(budget_fingerprint_model.input_fingerprint):

- the Cycle's Rate Set;
- its Budget Annual Input rows and Budget Entry Lines, by the ``cycle`` each
  row names, with the site's declared budget dimensions;
- the Spread Profile weights of the profiles those annual rows name;
- the approved Group Exchange Rates of the Cycle's set for its fiscal year,
  and the approved Historical Equity Rate tranches of its set. Every tranche
  of the set is read, dated before or after the year alike: a tranche's
  effect runs from its date onward, and reading all of them is conservative
  (a new tranche reads as a change, never a changed one as a match);
- every submitted Ownership Period link (the rows the warehouse syncs:
  docstatus 1) on the site, whatever its entity or date (konsol#552, Deepak
  Pai, 18 Nov 2026). An ownership exit recorded after Approve then reads as a
  changed input, including the exit of a holding that has no budget rows. Recipe v2: a Cycle approved before it holds a v1
  stamp and reads as stale until re-approved; there is no re-stamp.

Budget Cycle.before_submit stamps ``input_fingerprint`` with it; the Approve
is the submit, so the value lands with the approval and nothing saves in
on_submit. ``check_input_fingerprint`` is a GET read that recomputes over the
current rows and reports a mismatch with both values: it decides nothing and
writes nothing, so a GET's end-of-request rollback costs it nothing.
"""
import frappe
from frappe import _

from konsol.epm import budget_fingerprint_model as M

ANNUAL_FIELDS = ("name", "data_area_id", "main_account", "layer", "currency", "annual_amount",
                 "spread_profile_id")
LINE_FIELDS = ("name", "data_area_id", "fiscal_year", "fiscal_period", "main_account", "layer", "currency",
               "amount_basis", "debit", "credit")
WEIGHT_FIELDS = ("profile_id", "fiscal_period", "weight")
RATE_FIELDS = ("from_currency", "to_currency", "fiscal_year", "fiscal_period", "rate_type", "quote",
               "quoted_per")
HISTORICAL_FIELDS = ("consolidation_group", "data_area_id", "main_account", "rate_date", "to_currency",
                     "historical_rate")


def _rows(doctype, filters, fields):
    return [dict(r) for r in frappe.get_all(doctype, filters=filters, fields=list(fields), limit_page_length=0)]


def _dimensions():
    """The declared budget dimension fields, as the two budget doctypes carry
    them (Published Dimensions ticked in_budget, konsol#287)."""
    from konsol.epm.doctype.budget_annual_input.budget_annual_input import (
        budget_dimension_fields as annual_dimensions)
    from konsol.epm.doctype.budget_entry_line.budget_entry_line import (
        budget_dimension_fields as line_dimensions)

    return tuple(sorted(set(annual_dimensions()) | set(line_dimensions())))


OWNERSHIP_FIELDS = ("name", "consolidation_group", "data_area_id", "owner_group", "owner_data_area_id",
                    "ownership_pct", "consolidation_method", "effective_date", "end_date",
                    "acquisition_date", "is_first_acquisition", "is_disposal", "disposal_date",
                    "supersedes", "superseded_end_date")


def _ownership_links():
    """Every submitted link, by name (v16 sorts by creation otherwise)."""
    return sorted(_rows("Ownership Period", {"docstatus": 1}, OWNERSHIP_FIELDS), key=lambda r: r["name"])


def read_inputs(cycle):
    """The model's arguments for ``cycle`` (a Budget Cycle document)."""
    dims = _dimensions()
    annual = _rows("Budget Annual Input", {"cycle": cycle.name}, ANNUAL_FIELDS + dims)
    lines = _rows("Budget Entry Line", {"cycle": cycle.name}, LINE_FIELDS + dims)
    profiles = sorted({r["spread_profile_id"] for r in annual if r.get("spread_profile_id")})
    # Never ``["in", []]``: with no profile in use there is nothing to ask.
    weights = _rows("Spread Profile", {"profile_id": ["in", profiles]}, WEIGHT_FIELDS) if profiles else []
    rates = _rows("Group Exchange Rate",
                  {"docstatus": 1, "rate_set": cycle.rate_set, "fiscal_year": int(cycle.fiscal_year)},
                  RATE_FIELDS)
    historical = _rows("Historical Equity Rate", {"docstatus": 1, "rate_set": cycle.rate_set}, HISTORICAL_FIELDS)
    return {
        "rate_set": cycle.rate_set,
        "annual_inputs": annual,
        "entry_lines": lines,
        "weights": weights,
        "exchange_rates": rates,
        "historical_rates": historical,
        "ownership_links": _ownership_links(),
        "dimensions": dims,
    }


def compute(cycle):
    """The fingerprint of the inputs ``cycle`` approves, as of now."""
    return M.input_fingerprint(**read_inputs(cycle))


@frappe.whitelist(methods=["GET"])
def check_input_fingerprint(cycle):
    """Recompute Budget Cycle ``cycle``'s input fingerprint and compare it with
    the one stamped at Approve. A read: ``{"cycle", "docstatus", "stored",
    "current", "matches", "message"}``; ``matches`` is None while the Cycle is
    not approved (nothing was stamped). Reports, never decides (BUD-11-3): what
    follows a mismatch is the approver's call, on the Cycle."""
    doc = frappe.get_doc("Budget Cycle", cycle)
    if not frappe.has_permission("Budget Cycle", "read", doc):
        frappe.throw(_("Not permitted to read Budget Cycle {0}.").format(cycle), frappe.PermissionError)
    current = compute(doc)
    stored = doc.get("input_fingerprint") or None
    if int(doc.docstatus or 0) != 1 or not stored:
        matches = None
        message = _("Budget Cycle {0} is not approved: no input fingerprint is stamped yet.").format(cycle)
    elif stored == current:
        matches = True
        message = _("Budget Cycle {0}: the inputs are as approved.").format(cycle)
    else:
        matches = False
        message = _("Budget Cycle {0}: an input has changed since Approve (an annual figure, an entry line, "
                    "a spread weight, a rate of Rate Set {1}, or an ownership link). Stamped {2}, now {3}.").format(
            cycle, doc.rate_set, stored, current)
    return {"cycle": cycle, "docstatus": int(doc.docstatus or 0), "stored": stored, "current": current,
            "matches": matches, "message": message}
