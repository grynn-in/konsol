"""The Konsol home workspace's close band and period close checklist (konsol#407 phase 1).

``get_close_band`` for the "Konsol Close Band" Custom HTML Block (and the
same band inside the Konsol Home Tabs block), and ``get_checklist`` for the
"Konsol Close Checklist" block. Each reads the close app's own readers and
lays the result out through ``overview_model``; no rule is decided here:

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

The band was six Custom Number Cards until konsol#610 (option B, Deepak Pai,
26 Nov 2026): Frappe 16.60 draws a Custom card's value as text, so their
HTML printed as markup, and a Custom card cannot carry text, a route and a
per-state colour together. ``get_close_band`` returns data, never markup
(``overview_model.band_item``); the block sets every text as text.

Both methods are read-only GETs, gated to the same roles (no Entity
Accountant, E2-7).
"""
import frappe

from konsol import fiscal_calendar, group_rates
from konsol.close import (approvals_api, freshness_api, ic_api, overview_model as model,
                          period_api, readiness_model, signoff_gate)
from konsol.consolidation.doctype.assertion_run.assertion_run import latest_close_run
from konsol.entity_permissions import allowed_entity_codes

TB_SUBMISSIONS = "/desk/trial-balance-submission"
TB_EXCEPTIONS = "/desk/tb-exception"


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


def _once(fn):
    """``fn``'s result, read on the first call and replayed after; an
    exception is replayed too, so every period item says the same thing."""
    memo = []

    def call():
        if not memo:
            try:
                memo.append((fn(), None))
            except Exception as e:  # replayed to each caller, never swallowed
                memo.append((None, e))
        value, error = memo[0]
        if error is not None:
            raise error
        return value
    return call


def _shown(label, route, build):
    """``build()``'s ``(text, state, route)`` as a band item, or a gray
    "Cannot read" item naming the exception (an unknown status, an undeclared
    build scope, a permission refused). One unreadable item never hides the
    other five (review of #409); the reason is shown, never guessed away.
    ``route`` is the item's link when ``build`` gives none or fails."""
    try:
        text, state, own_route = build()
        return model.band_item(label, text, state, own_route or route)
    except Exception as e:
        return model.band_item(label, "Cannot read: %s" % (str(e) or type(e).__name__),
                               model.UNKNOWN, route)


def _band(current):
    """``[(label, default route, build)]`` in the old cards' order."""

    def period_item(make):
        def build():
            key, reason, state = current()
            if key is None:
                return reason or "No current period", model.UNKNOWN, model.PERIOD_CLOSE
            return make(key, state)
        return build

    def close_status(key, state):
        text, tone = model.close_status(key, state)
        return text, tone, model.PERIOD_CLOSE

    def trial_balances_in(key, _state):
        progress = signoff_gate.trial_balance_progress(*key)
        text, tone = model.tb_progress(progress["expected"], progress["missing"],
                                       allowed_entity_codes())
        return text, tone, TB_SUBMISSIONS + "?fiscal_year=%d&fiscal_period=%d" % key

    def waiting_for_me():
        user = frappe.session.user
        count = approvals_api.queue_for(user, frappe.get_roles(user))["waiting"]["count"]
        text, tone = model.waiting(count)
        return text, tone, model.MY_WORK

    def ic_over_tolerance(key, _state):
        text, tone = model.ic_card(ic_api.signoff_summary(*key))
        return text, tone, model.PERIOD_CLOSE

    def tb_exceptions(key, _state):
        frappe.has_permission("TB Exception", "read", throw=True)
        entities = frappe.get_list(
            "TB Exception", filters={"fiscal_year": key[0], "fiscal_period": key[1], "docstatus": 1},
            pluck="data_area_id", limit_page_length=0, order_by="name asc")
        allowed = allowed_entity_codes()
        count = len([e for e in entities if allowed is None or e in allowed])
        text, tone = model.exceptions_card(count)
        return text, tone, TB_EXCEPTIONS + "?fiscal_year=%d&fiscal_period=%d&docstatus=1" % key

    def warehouse_freshness():
        text, tone = model.freshness_card(freshness_api.current_freshness())
        return text, tone, model.BUILDS

    return (
        ("Close Status", model.PERIOD_CLOSE, period_item(close_status)),
        ("Trial Balances In", TB_SUBMISSIONS, period_item(trial_balances_in)),
        ("Waiting for My Approval", model.MY_WORK, waiting_for_me),
        ("IC Pairs over Tolerance", model.PERIOD_CLOSE, period_item(ic_over_tolerance)),
        ("TB Exceptions Declared", TB_EXCEPTIONS, period_item(tb_exceptions)),
        ("Warehouse Freshness", model.BUILDS, warehouse_freshness),
    )


@frappe.whitelist(methods=["GET"])
def get_close_band():
    """The home close band: six ``{label, text, state, colour, route}`` items."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    current = _once(_current)
    return [_shown(label, route, build) for label, route, build in _band(current)]


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
        equity_scoped=allowed_entity_codes() is not None,
        freshness=freshness_api.current_freshness(),
    )
