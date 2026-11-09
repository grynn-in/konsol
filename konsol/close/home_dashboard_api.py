"""The Close home dashboard's one read (trial, Deepak Pai, 8 Nov 2026).

``get_home(group=None, periods=6)`` feeds the "Konsol Home Dashboard"
Custom HTML Block on the Konsol workspace. It assembles sections from the
readers that already exist and lays each out through ``home_dashboard_model``; it
writes nothing and adds no warehouse query:

- header: ``period_api.get_context`` (the D5 working landing, as the home
  band: ``overview_model.current_period``) and ``statement_api.root_groups``
  / ``choose_group`` (the one rule for which group a statement shows);
- headline: ``mywork_api.get_my_work``'s blocking count;
- banners and setup pill: ``freshness_api.current_freshness`` (a build in
  progress) and ``setup.status`` (read only for ``setup.SETUP_ROLES``; for
  anyone else there is no setup section, as the Setup page is theirs only);
- tiles, over the last ``periods`` Regular periods up to the current one:
  TB submitted (``signoff_gate.trial_balance_progress`` and the caller's
  submitted TBs and TB Exceptions through ``frappe.get_list``, laid out by
  ``charts_model.tb_progress``, as the TB chart); IC differences
  (``ic_api.get_ic`` per period, masked to the caller); days to close
  (``signoff_gate.latest_signed_runs``); consolidated net result
  (``statement_api._statement`` once per fiscal year of the window, then
  ``statement_model.statement`` per period and ``charts_model.pl_trend``, as
  the net result chart, for the chosen group);
- period card: the current period's state, the freshness, and
  ``approvals_api.queue_for``'s waiting count;
- entities: Active leaf entities (``frappe.get_list``) with their trial
  balance status (the TB tile's reads for the current period) and
  intercompany status (the IC tile's ``get_ic`` for the current period).
  There is no per-entity sign-off: sign-off is per period.

Each section is isolated (``_section``): a reader that raises makes that
section ``{"state": "unavailable", "reason"}`` and leaves the others, and
its ``frappe.throw`` message is taken off the response so the block shows
the reason in place rather than a dialog. Nothing is ever shown as zero for
want of a read. One IC period that cannot be read is a gap in the IC
sparkline, with its reason.

``methods=["GET"]``: a read; a GET rolls back.
"""
import frappe

from konsol import setup
from konsol.close import (approvals_api, charts_model, freshness_api, home_dashboard_model as model,
                          ic_api, mywork_api, overview_model, period_api, signoff_gate,
                          statement_api, statement_model)
from konsol.entity_permissions import allowed_entity_codes

STATEMENT = "/desk/query-report/Consolidated%20Statement"


def _reason(e):
    text = str(e).strip()
    return text or type(e).__name__


def _quiet(build):
    """``(build(), None)``, or ``(None, reason)`` when it raises. A message
    ``frappe.throw`` queued for a dialog is dropped: the block shows the
    reason where the section would be."""
    log = getattr(frappe.local, "message_log", None)
    mark = len(log) if log is not None else 0
    try:
        return build(), None
    except Exception as e:  # noqa: BLE001 — the section says why, the page stays
        if log is not None:
            del log[mark:]
        return None, _reason(e)


def _section(build):
    """``build()``, or ``model.unavailable(reason)`` when it raises."""
    value, reason = _quiet(build)
    return model.unavailable(reason) if reason is not None else value


def _periods(periods):
    try:
        n = int(periods)
    except (TypeError, ValueError):
        n = None
    if n not in model.PERIOD_CHOICES:
        frappe.throw("Periods must be one of %s."
                     % ", ".join(str(c) for c in model.PERIOD_CHOICES))
    return n


class _Context:
    """The period context, read once: the current key, the window of keys
    (oldest first) and each Regular period's state. A failed read, or no
    current period, leaves ``key`` None and ``need()`` raising the reason."""

    def __init__(self, n):
        self.key, self.keys, self.states = None, [], {}
        read, self.reason = _quiet(period_api.get_context)
        if read is None:
            return
        key, reason = overview_model.current_period(read["landing"])
        self.states = {tuple(int(v) for v in s["key"]): s for s in read["periods"]}
        if key is None:
            self.reason = reason or "there is no current period"
        elif key not in self.states:
            self.reason = ("%s is not a Regular period of the calendar."
                           % overview_model.period_name(*key))
        else:
            self.key = key
            self.keys = model.window(read["periods"], key, n)

    def need(self):
        if self.key is None:
            raise ValueError(self.reason)
        return self.key


def _group(group):
    groups = statement_api.root_groups()
    chosen, note, state, message = statement_api.choose_group(groups, group or None)
    currency = next((g.get("reporting_currency") for g in groups
                     if g["consolidation_group"] == chosen), None)
    return {"groups": sorted(g["consolidation_group"] for g in groups), "chosen": chosen,
            "note": note, "state": state, "message": message, "currency": currency}


def _by_period(rows):
    out = {}
    for row in rows:
        out.setdefault((int(row["fiscal_year"]), int(row["fiscal_period"])), set()).add(
            row["data_area_id"])
    return out


def _tb_reads(ctx):
    """``(chart, progress, submitted, exceptions)`` over the window."""
    ctx.need()
    keys = ctx.keys
    frappe.has_permission("Trial Balance Submission", "read", throw=True)
    frappe.has_permission("TB Exception", "read", throw=True)
    years = sorted({k[0] for k in keys})
    fields = ["data_area_id", "fiscal_year", "fiscal_period"]
    read = {"fiscal_year": ["in", years], "docstatus": 1}
    tbs = _by_period(frappe.get_list("Trial Balance Submission", filters=dict(read),
                                     fields=fields, limit_page_length=0, order_by="name asc"))
    excs = _by_period(frappe.get_list("TB Exception", filters=dict(read), fields=fields,
                                      limit_page_length=0, order_by="name asc"))
    progress = {k: signoff_gate.trial_balance_progress(*k) for k in keys}
    submitted = {k: tbs.get(k, set()) for k in keys}
    exceptions = {k: excs.get(k, set()) for k in keys}
    chart = charts_model.tb_progress(keys, progress, submitted, exceptions,
                                     allowed_entity_codes())
    return {"chart": chart, "progress": progress, "submitted": submitted,
            "exceptions": exceptions}


def _ic_reads(keys):
    """``{key: get_ic result}``; a period that raises is ``{"state":
    "error", "message"}`` (a gap in the sparkline, with its reason)."""
    out = {}
    for key in keys:
        result, reason = _quiet(lambda: ic_api.get_ic(*key))
        out[key] = result if reason is None else {"state": "error", "message": reason}
    return out


def _ok(section):
    """A section another depends on; its own failure becomes this one's."""
    if section.get("state") == model.UNAVAILABLE:
        raise ValueError(section["reason"])
    return section


def _tb_tile(ctx, tb):
    current = ctx.need()
    chart = _ok(tb)["chart"]
    name = overview_model.period_name(*current)
    points = model.tb_points(chart)
    tile = model.tile(
        "TB submitted", points,
        route="/desk/trial-balance-submission?fiscal_year=%d&fiscal_period=%d" % current,
        higher_is_better=True, breakdown=model.tb_breakdown(chart),
        empty_action=("Upload", model.TB_UPLOAD))
    if tile["state"] == model.OK and tile["value"] == 0:
        # Owed, none in yet: the value 0 is true; the action is the upload.
        tile["empty"] = {"text": "No trial balance for %s yet" % name, "action": "Upload",
                         "route": model.TB_UPLOAD}
    return tile


def _ic_tile(ctx, ic, group):
    current = ctx.need()
    return model.tile(
        "IC differences", model.ic_points(ctx.keys, ic, group["chosen"]),
        route=overview_model.PERIOD_CLOSE, higher_is_better=False,
        breakdown=model.ic_breakdown(ic.get(current), group["chosen"]),
        empty_action=("Open Period Close", overview_model.PERIOD_CLOSE))


def _days_tile(ctx):
    ctx.need()
    frappe.has_permission("Assertion Run", "read", throw=True)
    signed = signoff_gate.latest_signed_runs(("signed_off_at",))
    points = model.days_points([ctx.states[k] for k in ctx.keys], signed)
    return model.tile("Days to close", points, route="/desk/assertion-run",
                      higher_is_better=False, delta_unit=" days",
                      empty_action=("Sign off", overview_model.PERIOD_CLOSE))


def _result_tile(ctx, group):
    ctx.need()
    if group["state"] != "ok":
        raise ValueError(group["message"])
    heading = frappe.db.get_single_value("Close Settings", "statement_net_sales_heading")
    values, sales, notes = {}, {}, {}
    for fy in sorted({k[0] for k in ctx.keys}):
        keys = [k for k in ctx.keys if k[0] == fy]
        payload, reads = statement_api._statement(fy, keys[-1][1], group["chosen"])
        if payload["state"] != "ok":
            notes.update(dict.fromkeys(keys, payload["message"]))
            continue
        minor_unit = payload["statement"]["minor_unit"]
        statements = {k: statement_model.statement(reads["tb_rows"], reads["accounts"],
                                                   reads["period_rows"], k, reads["declared"],
                                                   minor_unit) for k in keys}
        chart = charts_model.pl_trend(keys, statements, heading or None, group["currency"])
        for k, s, r in zip(keys, chart["datasets"][0]["values"],
                           chart["datasets"][1]["values"]):
            sales[k], values[k] = s, r
    labels = [overview_model.period_name(*k) for k in ctx.keys]
    shown = model.result_points(
        {"labels": labels, "datasets": [{"values": []},
                                        {"values": [values.get(k, 0) for k in ctx.keys]}]},
        group["currency"])
    points = []
    for k, point in zip(ctx.keys, shown):
        if k not in values:
            point = {"label": point["label"], "value": None, "display": None,
                     "note": notes.get(k) or "No statement for %s" % point["label"]}
        points.append(point)
    breakdown = None
    if ctx.key in sales:
        breakdown = ("Net sales %s" % model.compact(sales[ctx.key]) if heading
                     else "Declare the Net Sales Heading in Close Settings")
    return model.tile("Consolidated net result", points, route=STATEMENT,
                      higher_is_better=True, breakdown=breakdown,
                      empty_action=("Open the statement", STATEMENT))


def _period_card(ctx, freshness):
    key = ctx.need()
    user = frappe.session.user
    waiting = approvals_api.queue_for(user, frappe.get_roles(user))["waiting"]["count"]
    return model.period_card(key, ctx.states[key], freshness, waiting)


def _entities(ctx, tb, ic, group):
    key = ctx.need()
    tb = _ok(tb)
    frappe.has_permission("Entity", "read", throw=True)
    entities = frappe.get_list("Entity", filters={"is_group": 0, "status": "Active"},
                               fields=["name", "entity_name"], limit_page_length=0,
                               order_by="name asc")
    current_ic = ic.get(key) or {}
    rows = model.entity_rows(key, entities, tb["progress"][key], tb["submitted"][key],
                             tb["exceptions"][key], current_ic, group["chosen"],
                             allowed_entity_codes())
    return {"state": model.OK, "period": overview_model.period_name(*key), "rows": rows,
            "open": sum(1 for r in rows if r["open"]), "total": len(rows),
            "ic_state": current_ic.get("state"), "ic_message": current_ic.get("message"),
            "route": "/desk/entity?is_group=0&status=Active"}


def _setup():
    """``setup.status()`` for its roles; None for anyone else."""
    if not set(frappe.get_roles(frappe.session.user)) & set(setup.SETUP_ROLES):
        return None
    return setup.status()


@frappe.whitelist(methods=["GET"])
def get_home(group=None, periods=6):
    """The Close home dashboard: ``{"header", "headline", "banners", "tiles",
    "period", "entities", "setup"}``, each section its own state."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    n = _periods(periods)
    ctx = _Context(n)
    grp = _section(lambda: _group(group))
    if grp.get("state") == model.UNAVAILABLE:
        grp = {"groups": [], "chosen": None, "note": None, "state": model.UNAVAILABLE,
               "message": grp["reason"], "currency": None}
    tb = _section(lambda: _tb_reads(ctx))
    ic = _ic_reads(ctx.keys)
    freshness = _section(freshness_api.current_freshness)
    setup_status = _section(_setup)
    setup_ok = setup_status if setup_status and "done" in setup_status else None
    return {
        "header": {
            "period": overview_model.period_name(*ctx.key) if ctx.key else None,
            "period_reason": None if ctx.key else ctx.reason,
            "periods": n, "choices": list(model.PERIOD_CHOICES),
            "groups": grp["groups"], "group": grp["chosen"],
            "group_note": grp["note"], "group_message": grp["message"],
        },
        "headline": _section(lambda: model.headline(mywork_api.get_my_work())),
        "banners": model.banners(freshness, setup_ok),
        "tiles": [
            _section(lambda: _tb_tile(ctx, tb)),
            _section(lambda: _ic_tile(ctx, ic, grp)),
            _section(lambda: _days_tile(ctx)),
            _section(lambda: _result_tile(ctx, grp)),
        ],
        "period": _section(lambda: _period_card(ctx, freshness)),
        "entities": _section(lambda: _entities(ctx, tb, ic, grp)),
        # None: the caller's role does not read setup (no pill, no banner).
        "setup": model.setup_pill(setup_ok) if setup_ok is not None else setup_status,
    }
