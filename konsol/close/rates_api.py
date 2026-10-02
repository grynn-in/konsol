"""Rates endpoints for the close app (konsol#305 E403; stories 4.1, 4.3;
#305-W2-3, W2-10, W2-14; D2-9).

``get_rates(fiscal_year, fiscal_period)`` (GET) returns the period's group
rate grid, as ``rates_model.grid`` assembles it:

- the required (from, to) pairs from the last build
  (``group_rates.translation_needs``, a ClickHouse read). Any failure there is
  reported as ``pairs_error`` and every pair with a rate is listed with
  ``required: None``: it fails closed and visibly (E4-P9);
- the period's drafts and approved Group Exchange Rates, and the previous
  approved rate per key (one read of every earlier approved rate; the model
  picks the latest per key);
- the move flag of each cell under the declared threshold
  (``group_rates.move_threshold``), never a guessed one;
- on each awaiting cell, what the caller may do (``rates_model.approve_mode``)
  judged on the draft's preparers (``self_approval.preparers_for``,
  #305-W2-14), with ``edited_by``: the preparers other than the owner. An
  approved or missing cell carries ``approve: None`` and ``edited_by: None``;
- the policy gaps, and whether the caller may enter or approve.

Viewers (EPM User) read it and write nothing (#305-W2-10): ``approve_mode``
already says ``not_approver`` for them. The number of reads does not depend
on the number of pairs: 7 MariaDB reads and 1 ClickHouse query.
"""
import frappe

from konsol import fiscal_calendar, group_rates
from konsol.close import close_policy_model, rates_model, self_approval

#: Who reads the Rates screen (#305-W2-3, W2-10). The Entity Accountant does not.
RATES_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")
GER = "Group Exchange Rate"
DOC_FIELDS = ["name", "from_currency", "to_currency", "rate_type", "quote", "quoted_per",
              "erp_quote", "docstatus", "owner", "modified", "change_reason", "source"]


def _period(fiscal_year, fiscal_period):
    try:
        return int(fiscal_year), int(fiscal_period)
    except (TypeError, ValueError):
        frappe.throw(f"FY{fiscal_year} P{fiscal_period} is not a period: "
                     "pass the fiscal year and period as whole numbers.")


def _period_row(key):
    for row in fiscal_calendar.fiscal_period_rows():
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            return row
    frappe.throw("FY%d P%02d is not declared: declare it in EPM Fiscal Year." % key)


def _number(value):
    """A Float column as a JSON-safe float (MariaDB may hand back a Decimal)."""
    return None if value is None else float(value)


def _required(fy, fp):
    """(required pairs or None, pairs_error, blockers) — rate_gate's wording."""
    try:
        pairs, groups = group_rates.translation_needs(fy, fp)
    except Exception as e:  # noqa: BLE001 — any failure to read means "can't say"
        names = sorted(group_rates.ch_error_names(e))
        return None, type(e).__name__ + (f" {', '.join(names)}" if names else ""), []
    return set(pairs), None, [f"Consolidation Group {g} has no reporting currency"
                              for g in groups]


def _docs(fy, fp):
    docs = frappe.get_all(
        GER,
        filters={"fiscal_year": fy, "fiscal_period": fp, "docstatus": ["in", [0, 1]]},
        fields=DOC_FIELDS,
        limit_page_length=0,
    )
    out = []
    for doc in docs:
        doc = dict(doc)
        doc["quote"] = _number(doc.get("quote"))
        doc["rate"] = group_rates.true_rate(doc["quote"], doc.get("quoted_per"))
        erp_quote = doc.pop("erp_quote", None)
        doc["erp_rate"] = (group_rates.true_rate(erp_quote, doc.get("quoted_per"))
                           if erp_quote else None)
        out.append(doc)
    return out


def _earlier(fy, fp):
    rows = frappe.db.sql(
        "SELECT name, from_currency, to_currency, rate_type, quote, quoted_per, "
        "fiscal_year, fiscal_period FROM `tabGroup Exchange Rate` "
        "WHERE docstatus = 1 AND (fiscal_year < %s OR (fiscal_year = %s AND fiscal_period < %s))",
        (fy, fy, fp),
        as_dict=True,
    )
    out = []
    for row in rows:
        row = dict(row)
        row["quote"] = _number(row.get("quote"))
        row["rate"] = group_rates.true_rate(row["quote"], row.get("quoted_per"))
        out.append(row)
    return out


@frappe.whitelist(methods=["GET"])
def get_rates(fiscal_year, fiscal_period):
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key = _period(fiscal_year, fiscal_period)
    period = _period_row(key)
    fy, fp = key

    required, pairs_error, blockers = _required(fy, fp)
    docs = _docs(fy, fp)
    earlier = _earlier(fy, fp)
    threshold = group_rates.move_threshold()
    threshold_pct = threshold * 100 if threshold is not None else None
    policy = frappe.db.get_single_value("Close Settings", "self_approval")

    result = rates_model.grid(required, docs, earlier, threshold, group_rates.move_problem)

    cells = [row[k] for row in result["rows"] + result["unrequired"] for k in ("closing", "average")]
    awaiting = [c for c in cells if c["status"] == "awaiting_approval"]
    preparers = self_approval.preparers_for(GER, {c["name"]: c["owner"] for c in awaiting})

    user = frappe.session.user
    roles = frappe.get_roles(user)
    for cell in cells:
        if cell["status"] != "awaiting_approval":
            cell["approve"] = None
            cell["edited_by"] = None
            continue
        cell_preparers = preparers[cell["name"]]
        cell["approve"] = rates_model.approve_mode(
            user, roles, cell_preparers, GER, cell["name"], policy,
            close_policy_model.APPROVER_ROLES, close_policy_model.self_approval_problem)
        cell["edited_by"] = sorted(cell_preparers - {cell["owner"]})

    role_set = set(roles)
    return {
        "period": {"fiscal_year": fy, "fiscal_period": fp,
                   "period_code": period.get("period_code"), "status": period.get("status")},
        "rows": result["rows"],
        "unrequired": result["unrequired"],
        "summary": result["summary"],
        "pairs_error": pairs_error,
        "blockers": blockers,
        "threshold_pct": threshold_pct,
        "policy_gaps": close_policy_model.policy_gaps(policy, threshold_pct or 0),
        "self_approval": policy or None,
        "can_enter": bool(role_set & set(group_rates.PREFILL_ROLES))
        and period.get("status") == "Open",
        "can_approve": bool(role_set & set(close_policy_model.APPROVER_ROLES)),
    }
