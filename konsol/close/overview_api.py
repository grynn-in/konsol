"""The Konsol home workspace's close band and period close checklist (konsol#407 phase 1).

Six band methods, one per Custom number card on the Konsol workspace, and
``get_checklist`` for the "Konsol Close Checklist" Custom HTML Block. Each
reads the close app's own readers and lays the result out through
``overview_model``; no rule is decided here:

- the current period: ``period_api.get_context``'s D5 landing
  (``overview_model.current_period``: a Viewer gets the working landing,
  its ``provisional``);
- close status: that period's ``period_model.period_states`` entry;
- trial balances in / expected: ``signoff_gate.trial_balance_progress``,
  cut to ``allowed_entity_codes``;
- waiting for my approval: ``approvals_api.queue_for``'s ``waiting`` count
  (the My work count);
- IC pairs over tolerance: ``ic_api.signoff_summary`` (counts only and
  unscoped, E5-P13: every role reads the same counts);
- TB exceptions declared for the period: ``frappe.get_list`` (the caller's
  permissions) cut to ``allowed_entity_codes``;
- warehouse freshness: ``freshness_api.current_freshness``;
- the checklist: ``readiness_model.readiness`` over the sign-off gate
  (exactly what ``grid_api.get_readiness`` returns), the intercompany line,
  the period's draft or pending journals and the equity rates awaiting
  approval (each counted with ``frappe.get_list`` only when
  ``frappe.has_permission`` allows the read; otherwise the line says the
  caller cannot read it), the build and the sign-off.

The band methods are ``methods=["POST"]`` because Frappe's number card
widget calls a Custom card's method with ``frappe.xcall``, which POSTs; they
write nothing. A card returns HTML (an escaped indicator pill linked to a
Desk route: ``overview_model.card``), which the widget writes into the card.
Each card's ``document_type`` is a doctype the Entity Accountant cannot
read, so Frappe does not show it to a role these methods refuse.

Every method refuses filters rather than ignoring them (none are defined).
"""
import frappe

from konsol import fiscal_calendar, group_rates
from konsol.close import (approvals_api, freshness_api, ic_api, overview_model as model,
                          period_api, readiness_model, signoff_gate)
from konsol.consolidation.doctype.assertion_run.assertion_run import latest_close_run
from konsol.entity_permissions import allowed_entity_codes


def _no_filters(filters):
    if frappe.parse_json(filters) if isinstance(filters, str) else filters:
        frappe.throw("The Konsol home cards take no filters.")


def _current():
    """``(key, reason, state)``: the current period, why there is none, and
    its period state (``period_model.period_states``)."""
    context = period_api.get_context()
    key, reason = model.current_period(context["landing"])
    if key is None:
        return None, reason, None
    state = next((s for s in context["periods"] if tuple(s["key"]) == key), None)
    if state is None:
        frappe.throw("%s is not a Regular period of the calendar." % model.period_name(*key))
    return key, None, state


def _none(reason, route=model.PERIOD_CLOSE):
    return model.card(reason or "No current period", model.UNKNOWN, route)


@frappe.whitelist(methods=["POST"])
def close_status(filters=None):
    """The current period's close status: "FY2025 P07 · Amber"."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    _no_filters(filters)
    key, reason, state = _current()
    if key is None:
        return _none(reason)
    text, tone = model.close_status(key, state)
    return model.card(text, tone, model.PERIOD_CLOSE)


@frappe.whitelist(methods=["POST"])
def trial_balances_in(filters=None):
    """Trial balances in / expected for the current period."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    _no_filters(filters)
    key, reason, _state = _current()
    if key is None:
        return _none(reason)
    progress = signoff_gate.trial_balance_progress(*key)
    text, tone = model.tb_progress(progress["expected"], progress["missing"],
                                   allowed_entity_codes())
    return model.card(text, tone, "/desk/trial-balance-submission?fiscal_year=%d&fiscal_period=%d"
                      % key)


@frappe.whitelist(methods=["POST"])
def waiting_for_me(filters=None):
    """How many documents the caller may approve now (the My work count)."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    _no_filters(filters)
    user = frappe.session.user
    count = approvals_api.queue_for(user, frappe.get_roles(user))["waiting"]["count"]
    text, tone = model.waiting(count)
    return model.card(text, tone, model.MY_WORK)


@frappe.whitelist(methods=["POST"])
def ic_over_tolerance(filters=None):
    """Intercompany pairs over tolerance in the current period's last build."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    _no_filters(filters)
    key, reason, _state = _current()
    if key is None:
        return _none(reason)
    text, tone = model.ic_card(ic_api.signoff_summary(*key))
    return model.card(text, tone, model.PERIOD_CLOSE)


@frappe.whitelist(methods=["POST"])
def tb_exceptions(filters=None):
    """TB Exceptions declared (submitted) for the current period that the
    caller may read."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    _no_filters(filters)
    key, reason, _state = _current()
    if key is None:
        return _none(reason)
    frappe.has_permission("TB Exception", "read", throw=True)
    entities = frappe.get_list(
        "TB Exception", filters={"fiscal_year": key[0], "fiscal_period": key[1], "docstatus": 1},
        pluck="data_area_id", limit_page_length=0, order_by="name asc")
    allowed = allowed_entity_codes()
    count = len([e for e in entities if allowed is None or e in allowed])
    text, tone = model.exceptions_card(count)
    return model.card(text, tone, "/desk/tb-exception?fiscal_year=%d&fiscal_period=%d&docstatus=1"
                      % key)


@frappe.whitelist(methods=["POST"])
def warehouse_freshness(filters=None):
    """Whether the consolidated numbers are current (the last build)."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    _no_filters(filters)
    text, tone = model.freshness_card(freshness_api.current_freshness())
    return model.card(text, tone, model.BUILDS)


def _count(doctype, filters):
    """How many ``doctype`` rows match, through the caller's permissions;
    None when the caller may not read the doctype."""
    if not frappe.has_permission(doctype, "read"):
        return None
    return len(frappe.get_list(doctype, filters=filters, pluck="name", limit_page_length=0,
                               order_by="name asc"))


@frappe.whitelist(methods=["GET"])
def get_checklist():
    """The current period's close checklist: ``overview_model.checklist``."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key, reason, _state = _current()
    if key is None:
        return model.no_period(reason)
    row = next(r for r in fiscal_calendar.fiscal_period_rows()
               if (int(r["fiscal_year"]), int(r["fiscal_period"])) == key)
    run = latest_close_run(*key)
    readiness = readiness_model.readiness(
        {"code": row.get("period_code"), "status": row.get("status"),
         "period_type": row.get("period_type")},
        signoff_gate.sign_off_problems(*key), group_rates.rate_gate(*key), run,
        allowed_entity_codes())
    return model.checklist(
        key=key, readiness=readiness, run=run,
        ic=ic_api.signoff_summary(*key),
        journals_open=_count("Consolidation Journal",
                             {"fiscal_year": key[0], "fiscal_period": key[1], "docstatus": 0}),
        equity_pending=_count("Historical Equity Rate", {"docstatus": 0}),
        freshness=freshness_api.current_freshness(),
    )
