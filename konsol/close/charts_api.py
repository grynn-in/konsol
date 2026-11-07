"""The Konsol home's seven charts (konsol#407 phase 2), one method per Custom chart.

Each Dashboard Chart on the Konsol workspace and the "Konsol Overview"
dashboard is of type Custom, with its own Dashboard Chart Source whose
method is here. None is a group-by chart: Frappe caches group-by data under
the chart name alone (frappe/utils/dashboard.py ``chart-data:{chart_name}``)
and ``dashboard_chart.get`` checks no permission, so one user could be served
counts computed under another's. Every method here, on every call:

1. admits only the overview roles (``frappe.only_for``; no Entity
   Accountant, as overview_api) and refuses filters rather than ignoring
   them (none are defined);
2. checks read on each doctype it lists itself (``frappe.has_permission``,
   throw), and reads through ``frappe.get_list`` (the caller's permissions)
   or the permission-scoped reader it names, cut to ``allowed_entity_codes``
   where the reader is unscoped;
3. lays the result out through ``charts_model``; no rule is decided here.

The readers:

1. TB submission progress: ``signoff_gate.trial_balance_progress`` per
   Regular period of the current fiscal year (the gate's expected and
   missing), and the caller's submitted Trial Balance Submissions and TB
   Exceptions for that year (get_list);
2. net sales and net result: ``statement_api._statement`` for the current
   period (the default group by ``statement_api.choose_group``: the only root
   group), then ``statement_model.statement`` for each period of the year
   over the same reads, so each point is the Numbers screen's statement.
   Net sales is the Close Settings "Net Sales Heading" (declared, never
   guessed);
3. top IC differences: ``ic_api.get_ic`` for the current period (masked to
   the caller, W3-2);
4. approvals backlog: ``approvals_api.queue_for`` (the approvals queue's
   items, as My work);
5. closing vs average rate: submitted Group Exchange Rates of the current
   year into the default group's reporting currency, for the three
   functional currencies held by the most Active entities the caller can
   see (get_list; none is the chart's note), each quote made a rate by
   ``group_rates.true_rate``;
6. entities by status: Entity (not group nodes) by ``status`` (get_list);
7. days to close: ``signoff_gate.latest_signed_runs`` (the periods whose
   latest run is signed) against each period's end date.

"The current period" is the home band's: ``period_api.get_context``'s D5
working landing (``overview_model.current_period``); its fiscal year is the
"current fiscal year".

A chart that cannot be drawn (no current period, a statement or IC state
other than ok, a reader's ``ValueError``) throws a ValidationError whose
message names the chart and the reason. The chart widget calls these with a
``silent`` xcall and shows that message inside the chart's card, so the
reason is shown where the chart would be, never as a dialog and never as a
chart of guessed zeros. A refused role is a PermissionError; Frappe hides
every chart from a role outside its ``roles``, which list the same set. A
refused doctype read (``has_permission`` throw) carries its message and shows
in the card too; the bare ``only_for`` refusal would open Frappe's "Not
permitted" dialog, but only a role the chart's ``roles`` already hide can
reach it (review of #411, L1, accepted). Every chart covers the current
fiscal year up to and including the current period, never a later one.

``methods=["POST"]``: the widget's ``frappe.xcall`` POSTs. Nothing writes.
"""
import frappe

from konsol import group_rates
from konsol.close import (approvals_api, charts_model as model, close_policy_model, ic_api,
                          overview_model, period_api, signoff_gate, statement_api,
                          statement_model)
from konsol.entity_permissions import allowed_entity_codes

TOP_CURRENCIES = 3
TOP_IC_PAIRS = 10


def _start(filters, *doctypes):
    """Refuse filters, then check read on each doctype (the caller has
    already run ``frappe.only_for``)."""
    if frappe.parse_json(filters) if isinstance(filters, str) else filters:
        frappe.throw("The Konsol home charts take no filters.")
    for doctype in doctypes:
        frappe.has_permission(doctype, "read", throw=True)


def _unavailable(title, reason):
    frappe.throw("%s cannot be shown: %s" % (title, reason))


def _shown(title, build):
    """``build()``'s chart; a reader's or the model's ValueError becomes the
    chart's note (``_unavailable``). Permission errors propagate."""
    try:
        return build()
    except ValueError as e:
        _unavailable(title, e)


def _year(title):
    """``(current key, the current fiscal year's period states up to and
    including the current period)``, oldest first. A later period is not
    charted: it is not due yet (review of #411, M2)."""
    context = period_api.get_context()
    key, reason = overview_model.current_period(context["landing"])
    if key is None:
        _unavailable(title, reason or "there is no current period")
    states = [s for s in context["periods"]
              if int(s["key"][0]) == key[0] and int(s["key"][1]) <= key[1]]
    states.sort(key=lambda s: (int(s["key"][0]), int(s["key"][1])))
    return key, states


def _keys(states):
    return [(int(s["key"][0]), int(s["key"][1])) for s in states]


def _by_period(rows):
    out = {}
    for row in rows:
        out.setdefault(int(row["fiscal_period"]), set()).add(row["data_area_id"])
    return out


@frappe.whitelist(methods=["POST"])
def tb_submission_progress(chart_name=None, filters=None, refresh=None, time_interval=None,
                           timespan=None, from_date=None, to_date=None, heatmap_year=None):
    """Chart 1: TB submission progress by period, current fiscal year."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    title = "TB Submission Progress"
    _start(filters, "Trial Balance Submission", "TB Exception")

    def build():
        key, states = _year(title)
        fy = key[0]
        read = {"fiscal_year": fy, "docstatus": 1}
        fields = ["data_area_id", "fiscal_period"]
        tbs = _by_period(frappe.get_list("Trial Balance Submission", filters=dict(read),
                                         fields=fields, limit_page_length=0, order_by="name asc"))
        excs = _by_period(frappe.get_list("TB Exception", filters=dict(read), fields=fields,
                                          limit_page_length=0, order_by="name asc"))
        keys = _keys(states)
        progress = {k: signoff_gate.trial_balance_progress(*k) for k in keys}
        return model.tb_progress(keys, progress, {k: tbs.get(k[1], set()) for k in keys},
                                 {k: excs.get(k[1], set()) for k in keys}, allowed_entity_codes())

    return _shown(title, build)


@frappe.whitelist(methods=["POST"])
def net_sales_and_result(chart_name=None, filters=None, refresh=None, time_interval=None,
                         timespan=None, from_date=None, to_date=None, heatmap_year=None):
    """Chart 2: consolidated net sales and net result by period (the default group)."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    title = "Net Sales and Net Result"
    _start(filters)

    def build():
        key, states = _year(title)
        payload, reads = statement_api._statement(key[0], key[1], None)
        if payload["state"] != "ok":
            _unavailable(title, payload["message"])
        minor_unit = payload["statement"]["minor_unit"]
        keys = _keys(states)
        statements = {k: statement_model.statement(reads["tb_rows"], reads["accounts"],
                                                   reads["period_rows"], k, reads["declared"],
                                                   minor_unit)
                      for k in keys}
        heading = frappe.db.get_single_value("Close Settings", "statement_net_sales_heading")
        return model.pl_trend(keys, statements, heading or None, payload["reporting_currency"])

    return _shown(title, build)


@frappe.whitelist(methods=["POST"])
def top_ic_differences(chart_name=None, filters=None, refresh=None, time_interval=None,
                       timespan=None, from_date=None, to_date=None, heatmap_year=None):
    """Chart 3: the ten largest IC differences by pair, current period."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    title = "Top IC Differences"
    _start(filters)

    def build():
        key, _states = _year(title)
        ic = ic_api.get_ic(*key)
        if ic["state"] == "not_applicable":
            # Close Settings declares no intercompany: a declaration, not a
            # failure, so the chart's empty state shows (review of #411, L5).
            return model.ic_top([], TOP_IC_PAIRS)
        if ic["state"] != "checked":
            _unavailable(title, ic["message"])
        return model.ic_top(ic["groups"], TOP_IC_PAIRS)

    return _shown(title, build)


@frappe.whitelist(methods=["POST"])
def approvals_backlog(chart_name=None, filters=None, refresh=None, time_interval=None,
                      timespan=None, from_date=None, to_date=None, heatmap_year=None):
    """Chart 4: documents waiting for approval, by document type."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    title = "Approvals Backlog"
    _start(filters)

    def build():
        user = frappe.session.user
        queue = approvals_api.queue_for(user, frappe.get_roles(user))
        return model.approvals(queue["items"], close_policy_model.APPROVAL_DOCTYPES)

    return _shown(title, build)


@frappe.whitelist(methods=["POST"])
def closing_vs_average_rate(chart_name=None, filters=None, refresh=None, time_interval=None,
                            timespan=None, from_date=None, to_date=None, heatmap_year=None):
    """Chart 5: closing and average rate of the group's top currencies, current year."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    title = "Closing vs Average Rate"
    _start(filters, "Group Exchange Rate", "Entity")

    def build():
        key, states = _year(title)
        groups = statement_api.root_groups()
        chosen, _note, state, message = statement_api.choose_group(groups)
        if state != "ok":
            _unavailable(title, message)
        currency = next(g for g in groups if g["consolidation_group"] == chosen).get(
            "reporting_currency")
        if not currency:
            _unavailable(title, "consolidation group %s has no reporting currency" % chosen)
        held = frappe.get_list("Entity", filters={"is_group": 0, "status": "Active"},
                               pluck="functional_currency", limit_page_length=0,
                               order_by="name asc")
        top = model.top_currencies(held, currency, TOP_CURRENCIES)
        if not top:
            # An empty chart would read as "no rate approved" (review of the
            # live check, 7 Nov): say why there is no currency to chart.
            _unavailable(title, "none of the Active entities you can see holds a currency "
                         "other than %s" % currency)
        rows = []
        for row in frappe.get_list(
                "Group Exchange Rate",
                filters={"docstatus": 1, "fiscal_year": key[0], "to_currency": currency,
                         "from_currency": ["in", top]},
                fields=["from_currency", "rate_type", "fiscal_year", "fiscal_period",
                        "quote", "quoted_per"],
                limit_page_length=0, order_by="name asc"):
            row["rate"] = group_rates.true_rate(row["quote"], row["quoted_per"])
            rows.append(row)
        return model.rates(_keys(states), top, rows)

    return _shown(title, build)


@frappe.whitelist(methods=["POST"])
def entities_by_status(chart_name=None, filters=None, refresh=None, time_interval=None,
                       timespan=None, from_date=None, to_date=None, heatmap_year=None):
    """Chart 6: entities by status."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    title = "Entities by Status"
    _start(filters, "Entity")

    def build():
        options = [o for o in (frappe.get_meta("Entity").get_field("status").options or "")
                   .split("\n") if o]
        statuses = frappe.get_list("Entity", filters={"is_group": 0}, pluck="status",
                                   limit_page_length=0, order_by="name asc")
        return model.entities_by_status(statuses, options)

    return _shown(title, build)


@frappe.whitelist(methods=["POST"])
def days_to_close(chart_name=None, filters=None, refresh=None, time_interval=None,
                  timespan=None, from_date=None, to_date=None, heatmap_year=None):
    """Chart 7: days from period end to sign-off, current fiscal year."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    title = "Days to Close"
    _start(filters, "Assertion Run")

    def build():
        _key, states = _year(title)
        signed = signoff_gate.latest_signed_runs(("signed_off_at",))
        return model.days_to_close(states, signed)

    return _shown(title, build)
