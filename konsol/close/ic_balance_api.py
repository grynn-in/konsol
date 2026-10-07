"""IC Balance on the Intercompany screen (konsol#305 story 5.4; decision
#305-W5-4, Deepak 6 Oct 2026).

- ``get_ic_balances(fiscal_year, fiscal_period)`` (GET): the period's draft
  and approved IC Balances the caller may see (either entity in scope), each
  with the margin of every unrealised-profit IC Elimination Rule that
  matches its pair (read-only: the rule is configured in Desk), the
  missing-rule gap and the ambiguous-rule gap (two or more rules on one
  pair, F51b) over the shown balances, the hidden count, the Active
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
- ``rule_gaps(fy, fp, reads=None)`` (sign-off gate) and
  ``open_rule_gaps(reads=None)`` (My work): ``ic_balance_model.rule_gaps``
  (a list: the undeclared gap, then the ambiguous gap, F51b) over the
  period's / every Open period's draft and approved balances. Not
  whitelisted: the callers gate and scope. Reads: IC Balance 1; IC
  Elimination Rule 1 only when a balance exists.
- ``open_reads()`` (review-w5 S9): those two reads made once for every Open
  period, ``{"keys", "balances", "rules"}``. A request that asks about
  several periods (My work) reads once and passes the result to each
  ``rule_gaps`` / ``open_rule_gaps`` call as ``reads``, which then read
  nothing. A period the reads do not cover raises ValueError: it is never
  answered from rows that were not read for it. No cache: the value lives
  only as long as the caller holds it.

Import warning: test loaders that build ``konsol.close`` as a stub package
and load the real ``signoff_gate`` or ``mywork_api`` must stub
``konsol.close.ic_balance_api``; both import it lazily.
"""
import importlib.util as _importlib_util
import os as _os

import frappe

from konsol import fiscal_calendar
from konsol.close import ic_balance_model
from konsol.entity_permissions import allowed_entity_codes


def _load_period_name():
    """konsol/close/period_name.py loaded by path (konsol#305 review-w5): the
    one "FY2025 P07" format, reachable even under the host tests' stub
    ``konsol.close`` package."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()


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
        frappe.throw(f"Fiscal year {fiscal_year!r}, period {fiscal_period!r} is not a period: "
                     "pass the fiscal year and period as whole numbers.")


def _period_row(key):
    for row in fiscal_calendar.fiscal_period_rows():
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            return row
    frappe.throw("%s is not declared: declare it in EPM Fiscal Year." % period_name(*key))


def _balances(filters):
    return frappe.get_all(IC_BALANCE, filters=dict(filters, docstatus=_LIVE),
                          fields=BALANCE_FIELDS, order_by="creation asc", limit_page_length=0)


def _rules():
    return frappe.get_all(RULE, fields=RULE_FIELDS, limit_page_length=0)


def _entity_codes():
    return frappe.get_all("Entity", filters={"is_group": 0, "status": "Active"},
                          pluck="name", limit_page_length=0)


def _key_of(balance):
    return int(balance["fiscal_year"]), int(balance["fiscal_period"])


def open_reads():
    """``{"keys": frozenset of the Open periods, "balances": their draft and
    approved IC Balances, "rules": the IC Elimination Rules}``, read once
    (S9). No Open period reads nothing; no balance reads no rule."""
    open_keys = frozenset(
        (int(r["fiscal_year"]), int(r["fiscal_period"]))
        for r in fiscal_calendar.fiscal_period_rows() if r.get("status") == "Open")
    balances = []
    if open_keys:
        years = sorted({fy for fy, _fp in open_keys})
        balances = [b for b in _balances({"fiscal_year": ["in", years]})
                    if _key_of(b) in open_keys]
    return {"keys": open_keys, "balances": balances, "rules": _rules() if balances else []}


def rule_gaps(fiscal_year, fiscal_period, reads=None):
    """The sign-off gate's rule gaps for one period (missing, then
    ambiguous); ``[]`` when none applies. ``reads`` (``open_reads()``)
    answers it with no read of its own."""
    key = (int(fiscal_year), int(fiscal_period))
    if reads is None:
        balances = _balances({"fiscal_year": key[0], "fiscal_period": key[1]})
        if not balances:
            return []
        return ic_balance_model.rule_gaps(balances, _rules())
    if key not in reads["keys"]:
        raise ValueError("%s is not an Open period: the shared IC Balance reads "
                         "cover the Open periods only." % period_name(*key))
    balances = [b for b in reads["balances"] if _key_of(b) == key]
    if not balances:
        return []
    return ic_balance_model.rule_gaps(balances, reads["rules"])


def open_rule_gaps(reads=None):
    """My work's rule gaps over every Open period's balances; ``[]`` when
    none applies. ``reads`` (``open_reads()``) answers it with no read of
    its own."""
    if reads is None:
        reads = open_reads()
    if not reads["balances"]:
        return []
    return ic_balance_model.rule_gaps(reads["balances"], reads["rules"])


@frappe.whitelist(methods=["GET"])
def get_ic_balances(fiscal_year, fiscal_period):
    """``{"period", "balances", "gap", "ambiguous_gap", "hidden", "entities",
    "can_draft", "rules_desk"}``. Read-only."""
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
        "ambiguous_gap": ic_balance_model.ambiguous_gap(shown, rules),
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
    return "%s → %s %s" % (selling_entity, buying_entity, period_name(fy, fp))


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
        frappe.throw("%s is %s: an IC Balance is drafted in an open period."
                     % (period_name(fy, fp), status))
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
