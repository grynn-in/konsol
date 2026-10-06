"""IC Balance on the Intercompany screen (konsol#305 story 5.4; decision
#305-W5-4, Deepak 6 Oct 2026).

- ``get_ic_balances(fiscal_year, fiscal_period)`` (GET): the period's draft
  and approved IC Balances the caller may see (either entity in scope), each
  with the margin of every unrealised-profit IC Elimination Rule that
  matches its pair (read-only: the rule is configured in Desk), the
  missing-rule gap over the shown balances, the hidden count, the Active
  leaf entity codes for the draft form, and ``can_draft``.
- ``save_ic_balance(...)`` (POST): an Analyst or System Manager drafts a new
  balance or edits a draft's amounts. Never an Admin: under R2 the Admin
  approves (submits) in the existing Approvals queue, which already lists IC
  Balance drafts (``approvals_model``). No ``**kwargs``: Frappe drops a
  request key the signature does not name, so a forged status, docstatus,
  owner or workflow state never reaches the document. The document goes
  through ``insert()`` / ``save()`` with no ignore flag, so the doctype's
  own validate (period declared) and Frappe's permissions still apply. A
  missing rule is NOT refused here (W5-4 rejected option): it is a gap.
- ``rule_gap(fy, fp)`` (sign-off gate) and ``open_rule_gap()`` (My work):
  ``ic_balance_model.rule_gap`` over the period's / every Open period's
  draft and approved balances. Not whitelisted: the callers gate and scope.
  Reads: IC Balance 1; IC Elimination Rule 1 only when a balance exists.

Import warning: test loaders that build ``konsol.close`` as a stub package
and load the real ``signoff_gate`` or ``mywork_api`` must stub
``konsol.close.ic_balance_api``; both import it lazily.
"""
import frappe

from konsol import fiscal_calendar
from konsol.close import ic_balance_model
from konsol.entity_permissions import allowed_entity_codes

IC_BALANCE = "IC Balance"
RULE = "IC Elimination Rule"
READ_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")
#: R2 / W5-4: the Analyst drafts, the Admin approves in Approvals.
DRAFT_ROLES = ("EPM Analyst", "System Manager")

BALANCE_FIELDS = ["name", "selling_entity", "buying_entity", "fiscal_year", "fiscal_period",
                  "ic_sales_amount", "ending_inventory_from_ic", "docstatus"]
RULE_FIELDS = ["rule_id", "rule_name", "rule_type", "margin_pct", "debit_entity_pattern",
               "credit_entity_pattern"]
_LIVE = ["in", [0, 1]]


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


def _balances(filters):
    return frappe.get_all(IC_BALANCE, filters=dict(filters, docstatus=_LIVE),
                          fields=BALANCE_FIELDS, order_by="creation asc", limit_page_length=0)


def _rules():
    return frappe.get_all(RULE, fields=RULE_FIELDS, limit_page_length=0)


def _entity_codes():
    return frappe.get_all("Entity", filters={"is_group": 0, "status": "Active"},
                          pluck="name", limit_page_length=0)


def rule_gap(fiscal_year, fiscal_period):
    """The sign-off gate's missing-rule gap for one period, or None."""
    balances = _balances({"fiscal_year": int(fiscal_year), "fiscal_period": int(fiscal_period)})
    if not balances:
        return None
    return ic_balance_model.rule_gap(balances, _rules())


def open_rule_gap():
    """My work's missing-rule gap over every Open period's balances, or None."""
    open_keys = {(int(r["fiscal_year"]), int(r["fiscal_period"]))
                 for r in fiscal_calendar.fiscal_period_rows() if r.get("status") == "Open"}
    if not open_keys:
        return None
    years = sorted({fy for fy, _fp in open_keys})
    balances = [b for b in _balances({"fiscal_year": ["in", years]})
                if (int(b["fiscal_year"]), int(b["fiscal_period"])) in open_keys]
    if not balances:
        return None
    return ic_balance_model.rule_gap(balances, _rules())


@frappe.whitelist(methods=["GET"])
def get_ic_balances(fiscal_year, fiscal_period):
    """``{"period", "balances", "gap", "hidden", "entities", "can_draft",
    "rules_desk"}``. Read-only."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key = _period(fiscal_year, fiscal_period)
    row = _period_row(key)
    fy, fp = key
    shown, hidden = ic_balance_model.visible(
        _balances({"fiscal_year": fy, "fiscal_period": fp}), allowed_entity_codes())
    rules = _rules()
    status = row.get("status")
    roles = set(frappe.get_roles())
    return {
        "period": {"fiscal_year": fy, "fiscal_period": fp,
                   "period_code": row.get("period_code"), "status": status},
        "balances": ic_balance_model.balance_rows(shown, rules),
        "gap": ic_balance_model.rule_gap(shown, rules),
        "hidden": hidden,
        "entities": sorted(_entity_codes()),
        "can_draft": bool(roles.intersection(DRAFT_ROLES)) and status == "Open",
        "rules_desk": ic_balance_model.RULES_DESK,
    }


def _in_scope(selling_entity, buying_entity, name=None):
    """Refuse a pair with neither entity in scope. ``name``: a stored
    balance, refused by the name the caller sent and never by its stored
    pair (F51b, review S6: an out-of-scope balance tells nothing)."""
    allowed = allowed_entity_codes()
    if allowed is not None and selling_entity not in allowed and buying_entity not in allowed:
        if name:
            frappe.throw("You can see neither entity of %s." % name)
        frappe.throw("You can see neither entity of this pair (%s → %s)."
                     % (selling_entity, buying_entity))


def _key_text(selling_entity, buying_entity, fy, fp):
    return "%s → %s FY%d P%02d" % (selling_entity, buying_entity, fy, fp)


@frappe.whitelist(methods=["POST"])
def save_ic_balance(fiscal_year, fiscal_period, selling_entity, buying_entity, ic_sales_amount,
                    ending_inventory_from_ic, name=None):
    """Draft a new IC Balance, or edit the named draft's amounts. Returns
    ``{"name", "docstatus"}``. Every refusal comes before the write."""
    frappe.only_for(("EPM Analyst", "System Manager"))
    key = _period(fiscal_year, fiscal_period)
    row = _period_row(key)
    fy, fp = key
    status = row.get("status")
    if status != "Open":
        frappe.throw("FY%d P%02d is %s: an IC Balance is drafted in an open period."
                     % (fy, fp, status))
    problems = ic_balance_model.draft_problems(
        selling_entity, buying_entity, ic_sales_amount, ending_inventory_from_ic,
        set(_entity_codes()))
    if problems:
        frappe.throw("<br>".join(problems))
    _in_scope(selling_entity, buying_entity)
    sales, inventory = float(ic_sales_amount), float(ending_inventory_from_ic)

    if not name:
        existing = frappe.get_all(
            IC_BALANCE, filters={"selling_entity": selling_entity, "buying_entity": buying_entity,
                                 "fiscal_year": fy, "fiscal_period": fp},
            fields=["name", "docstatus"], limit_page_length=0)
        by_state = {}
        for r in existing:
            by_state.setdefault(int(r["docstatus"]), r["name"])
        pair = _key_text(selling_entity, buying_entity, fy, fp)
        if 1 in by_state:
            frappe.throw(f"{by_state[1]} is the approved IC Balance for {pair}; a change is a "
                         "cancel and an amendment (Desk).")
        if 0 in by_state:
            frappe.throw(f"{by_state[0]} is already a draft for {pair}: edit it.")
        if 2 in by_state:
            frappe.throw(f"{by_state[2]} is a cancelled IC Balance for {pair}: amend it in Desk.")
        doc = frappe.get_doc({"doctype": IC_BALANCE, "selling_entity": selling_entity,
                              "buying_entity": buying_entity, "fiscal_year": fy,
                              "fiscal_period": fp, "ic_sales_amount": sales,
                              "ending_inventory_from_ic": inventory})
        doc.insert()
    else:
        doc = frappe.get_doc(IC_BALANCE, name)
        # F51b (review S6): scope first, so an out-of-scope balance's state
        # is never told to the caller.
        _in_scope(doc.selling_entity, doc.buying_entity, name)
        if int(doc.docstatus) != 0:
            frappe.throw(f"{name} is {'approved' if int(doc.docstatus) == 1 else 'cancelled'}, "
                         "not a draft: a change is a cancel and an amendment (Desk).")
        theirs = (doc.selling_entity, doc.buying_entity, int(doc.fiscal_year),
                  int(doc.fiscal_period))
        asked = (selling_entity, buying_entity, fy, fp)
        if theirs != asked:
            frappe.throw(f"{name} is the {_key_text(*theirs)} balance, not "
                         f"{_key_text(*asked)}: save it under its own key.")
        doc.ic_sales_amount = sales
        doc.ending_inventory_from_ic = inventory
        doc.save()

    return {"name": doc.name, "docstatus": int(doc.docstatus)}
