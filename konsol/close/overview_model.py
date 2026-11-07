"""Konsol home overview, pure (konsol#407 phase 1).

Lays out the home workspace's close band (number cards) and its period
close checklist (the "Konsol Close Checklist" Custom HTML Block) from what
the close app's own readers return. No rule is decided here:

- the current period is the D5 working landing (``period_model.landing``,
  through ``period_api.get_context``): the oldest ended Regular period still
  Open from the first close on, else the one containing today. A Viewer's
  own landing is the latest signed period; the home page shows the close in
  progress, so a Viewer gets the landing's ``provisional`` (the same working
  landing every other persona gets);
- the checklist carries every ``readiness_model.readiness`` item (the
  sign-off gate's ``sign_off_problems``, the rate gate and the latest run),
  in ``CHECKLIST`` order, interleaved with the intercompany line (``ic_api.signoff_summary``,
  read through ``signoff_model.ic_over_tolerance``), the open journals
  (``deadline_model.journals_open``'s count), the equity rates awaiting
  approval, the build (``freshness_model``) and the sign-off itself
  (``signoff_model.SIGNED_STATES``).

Colours are presentation only: ``ok`` green, ``warn`` orange (Amber: the
gate lets it through with an acknowledgement, or it is not a sign-off gate),
``blocked`` red, ``unknown`` gray. Every text that reaches HTML goes through
``escape``; a card links only to a ``/desk/`` route.

Imports nothing from frappe; siblings are loaded by path (as
readiness_model does), so the host tests need no site.
"""
import html as _html
import importlib.util
import os

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_sibling(name):
    spec = importlib.util.spec_from_file_location(
        "konsol_close_overview_model_" + name, os.path.join(_HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


signoff_model = _load_sibling("signoff_model")
period_name = _load_sibling("period_name").period_name

OK = "ok"
WARN = "warn"
BLOCKED = "blocked"
UNKNOWN = "unknown"
STATES = (OK, WARN, BLOCKED, UNKNOWN)

#: Frappe's indicator-pill colour classes.
_COLOUR = {OK: "green", WARN: "orange", BLOCKED: "red", UNKNOWN: "gray"}

PERIOD_CLOSE = "/desk/query-report/Period%20Close"
MY_WORK = "/desk/query-report/My%20Work"
CLOSE_SETTINGS = "/desk/close-settings"
BUILDS = "/desk/build-approval"

#: Fixed order (readiness_model.ITEMS first, then the lines added here).
CHECKLIST = (
    "period_open",
    "first_close",
    "previous_signed",
    "policies",
    "configuration",
    "ownership",
    "trial_balances",
    "rates",
    "equity_rates",
    "intercompany",
    "journals",
    "build",
    "checks",
    "signed_off",
)

_LABELS = {
    "equity_rates": "Equity rates approved",
    "intercompany": "Intercompany within tolerance",
    "journals": "Journals approved",
    "build": "Warehouse built",
    "signed_off": "Signed off",
}

#: Where each step is done. ``{fy}`` / ``{fp}`` are the period's parts.
_ROUTES = {
    "period_open": "/desk/epm-fiscal-year/{fy}",
    "first_close": CLOSE_SETTINGS,
    "previous_signed": PERIOD_CLOSE,
    "policies": CLOSE_SETTINGS,
    "configuration": CLOSE_SETTINGS,
    "ownership": "/desk/ownership-period",
    "trial_balances": "/desk/trial-balance-submission?fiscal_year={fy}&fiscal_period={fp}",
    "rates": "/desk/group-exchange-rate",
    "equity_rates": "/desk/historical-equity-rate?docstatus=0",
    "intercompany": PERIOD_CLOSE,
    "journals": "/desk/consolidation-journal?fiscal_year={fy}&fiscal_period={fp}",
    "build": BUILDS,
    "checks": PERIOD_CLOSE,
    "signed_off": PERIOD_CLOSE,
}

_RUN_STATUSES = ("Green", "Amber", "Red", "Error")
_ENTITIES_SHOWN = 5


def _noun(n, singular, plural=None):
    return "%d %s" % (n, singular if n == 1 else (plural or singular + "s"))


def _state(state):
    if state not in STATES:
        raise ValueError("unknown overview state %r; expected one of %s" % (state, ", ".join(STATES)))
    return state


def escape(text):
    """``text`` safe inside HTML text and a double-quoted attribute."""
    return _html.escape("" if text is None else str(text), quote=True)


def _route(route):
    """A same-site Desk path, or ValueError: nothing else is ever linked."""
    if not isinstance(route, str) or not route.startswith("/desk/") or any(
            c in route for c in "\"'<>\\ \t\r\n"):
        raise ValueError("not a Desk route: %r" % (route,))
    return route


def card(text, state, route=None):
    """A number card's value: an indicator pill, linked when ``route`` is given.

    A Custom number card that returns a string has it written into the card
    as HTML (frappe number_card_widget.js ``render_number``), so the text is
    escaped here and the colour comes from a fixed map."""
    pill = '<span class="indicator-pill %s">%s</span>' % (_COLOUR[_state(state)], escape(text))
    if route is None:
        return pill
    return '<a href="%s">%s</a>' % (escape(_route(route)), pill)


def current_period(landing):
    """``(key, None)`` for the working landing, or ``(None, reason)``."""
    working = landing.get("provisional") if "provisional" in landing else landing
    working = working or {}
    period = working.get("period")
    if period is None:
        return None, working.get("reason") or landing.get("reason")
    return (int(period[0]), int(period[1])), None


# --- the band ---------------------------------------------------------------

def close_status(key, state):
    """``(text, state)`` for the period's close status card. ``state`` is the
    period's ``period_model.period_states`` entry."""
    name = period_name(*key)
    signoff = state.get("signoff")
    if state.get("is_signed"):
        done = "Closed" if state.get("status") in ("Closed", "Locked") else "Signed off"
        return "%s · %s" % (name, done), OK
    if signoff == "Re-sign Needed":
        return "%s · Re-sign needed" % name, BLOCKED
    checks = state.get("checks")
    if checks == "Not run":
        return "%s · Checks not run" % name, UNKNOWN
    if checks not in _RUN_STATUSES:
        raise ValueError("unknown close run status %r" % (checks,))
    tone = {"Green": OK, "Amber": WARN}.get(checks, BLOCKED)
    return "%s · %s" % (name, checks), tone


def tb_progress(expected, missing, allowed):
    """``(text, state)``: trial balances in / expected, counted over the
    entities ``allowed`` (None: every entity). A missing TB outside the
    caller's scope still blocks and is counted, never named."""
    expected, missing = set(expected or ()), set(missing or ())
    visible = expected if allowed is None else expected & set(allowed)
    hidden = expected - visible
    hidden_missing = missing & hidden
    text = "%d / %d" % (len(visible - missing), len(visible))
    if hidden_missing:
        text += " · %d outside your scope missing" % len(hidden_missing)
    elif hidden:
        text += " · %d outside your scope" % len(hidden)
    if not expected:
        return text, UNKNOWN
    return text, BLOCKED if missing else OK


def waiting(count):
    return str(int(count)), WARN if count else OK


def ic_card(ic):
    """``(text, state)`` from the sign-off gate's intercompany line."""
    state = ic.get("state")
    if state in ("not_built", "error"):
        return "Cannot check", BLOCKED
    if state == "not_configured":
        return "Not configured", OK
    if state == "not_applicable":
        return "Not applicable", OK
    n = signoff_model.ic_over_tolerance(ic)
    return str(n), WARN if n else OK


def exceptions_card(count):
    return str(int(count)), WARN if count else OK


def _when(iso):
    """"2026-11-07 10:42" of an ISO timestamp (the site's own clock)."""
    return iso[:16].replace("T", " ") if iso else ""


def freshness_card(result):
    state = result.get("state")
    if state == "fresh":
        return "Fresh · %s" % _when(result.get("as_of")), OK
    if state == "stale":
        return "Stale · %s" % _when(result.get("as_of")), WARN
    if state == "pending":
        return "Build pending", WARN
    if state == "failed":
        return "Last build failed", BLOCKED
    if state == "never_built":
        return "Never built", BLOCKED
    raise ValueError("unknown freshness state %r" % (state,))


# --- the checklist ----------------------------------------------------------

def _entities(item):
    names = list(item.get("entities") or ())
    hidden = int(item.get("hidden") or 0)
    if not names and not hidden:
        return ""
    parts = ", ".join(names[:_ENTITIES_SHOWN])
    if len(names) > _ENTITIES_SHOWN:
        parts += " and %d more" % (len(names) - _ENTITIES_SHOWN)
    if hidden:
        parts = ("%s and %d outside your scope" % (parts, hidden)) if parts else (
            "%d outside your scope" % hidden)
    return " (%s)" % parts


def _line(code, label, state, detail, key):
    return {"code": code, "label": label, "state": _state(state), "detail": detail,
            "route": _route(_ROUTES[code].format(fy=key[0], fp=key[1]))}


def _readiness_lines(readiness, run, key):
    out = {}
    for item in readiness["items"]:
        state = {"ok": OK, "blocked": BLOCKED, "unknown": UNKNOWN}[item["state"]]
        if item["code"] == "checks" and run and run.get("status") == "Amber" and (
                run.get("signoff_status") not in signoff_model.SIGNED_STATES):
            state = WARN
        out[item["code"]] = _line(item["code"], item["label"], state,
                                  (item.get("detail") or "") + _entities(item), key)
    return out


def _ic_line(ic, key):
    state = ic.get("state")
    if state in ("not_built", "error"):
        return _line("intercompany", _LABELS["intercompany"], BLOCKED,
                     ic.get("message") or "Intercompany cannot be checked", key)
    n = signoff_model.ic_over_tolerance(ic)
    if n:
        return _line("intercompany", _LABELS["intercompany"], WARN,
                     "%s over tolerance: acknowledge at sign-off or correct them"
                     % _noun(n, "pair"), key)
    detail = ic.get("message") or "No pair over tolerance"
    return _line("intercompany", _LABELS["intercompany"], OK, detail, key)


_UNREADABLE = {"equity_rates": "Historical Equity Rates", "journals": "Consolidation Journals"}


def _count_line(code, count, some, none, key):
    """``count`` None: the caller may not read the doctype, so nothing was
    counted and the line is unknown, never a tick."""
    if count is None:
        return _line(code, _LABELS[code], UNKNOWN, "You cannot read %s" % _UNREADABLE[code], key)
    if count:
        return _line(code, _LABELS[code], WARN, some(count), key)
    return _line(code, _LABELS[code], OK, none, key)


def _build_line(result, key):
    text, state = freshness_card(result)
    if result.get("state") == "stale" and result.get("changed_since"):
        text = "Changed since the last build: %s" % ", ".join(result["changed_since"])
    return _line("build", _LABELS["build"], state, text, key)


def _signed_line(run, key):
    if run is None:
        return _line("signed_off", _LABELS["signed_off"], BLOCKED, "Checks not run", key)
    status = run.get("signoff_status") or "Not Signed Off"
    if status in signoff_model.SIGNED_STATES:
        return _line("signed_off", _LABELS["signed_off"], OK, status, key)
    if status == "Re-sign Needed":
        return _line("signed_off", _LABELS["signed_off"], BLOCKED, "Re-sign needed", key)
    if status == "Not Signed Off":
        return _line("signed_off", _LABELS["signed_off"], BLOCKED, "Not signed off", key)
    raise ValueError("unknown sign-off status %r" % (status,))


_NOT_COUNTED = "drafts outside your scope are not counted"


def _equity_line(count, scoped, key):
    """Historical Equity Rate is entity-scoped (entity_permissions), so a
    caller restricted to some entities (``scoped``) counts only theirs: none
    visible is unknown, never a tick, and the detail says so."""
    def some(n):
        text = ("1 equity rate is a draft or awaiting approval" if n == 1
                else "%d equity rates are drafts or awaiting approval" % n)
        return text + ("; " + _NOT_COUNTED if scoped else "")
    line = _count_line("equity_rates", count, some, "No equity rate is a draft or awaiting approval",
                       key)
    if scoped and count == 0:
        line.update(state=UNKNOWN, detail="No equity rate draft you can see; " + _NOT_COUNTED)
    return line


def checklist(key, readiness, run, ic, journals_open, equity_pending, freshness,
              equity_scoped=False):
    """``{"title", "reason", "lines", "done", "total"}``: one line per
    ``CHECKLIST`` code, in that order. ``done`` counts the ``ok`` lines.
    ``equity_scoped``: the caller sees only some entities' equity rates."""
    lines = _readiness_lines(readiness, run, key)
    lines["equity_rates"] = _equity_line(equity_pending, equity_scoped, key)
    lines["intercompany"] = _ic_line(ic, key)
    lines["journals"] = _count_line(
        "journals", journals_open,
        lambda n: ("1 journal is a draft or awaiting approval" if n == 1
                   else "%d journals are drafts or awaiting approval" % n),
        "No journal is a draft or awaiting approval", key)
    lines["build"] = _build_line(freshness, key)
    lines["signed_off"] = _signed_line(run, key)
    missing = [c for c in CHECKLIST if c not in lines]
    if missing:
        raise ValueError("the readiness checklist has no %s line" % ", ".join(missing))
    ordered = [lines[c] for c in CHECKLIST]
    return {"title": period_name(*key), "reason": None, "lines": ordered,
            "done": sum(1 for l in ordered if l["state"] == OK), "total": len(ordered)}


def no_period(reason):
    return {"title": "No current period", "reason": reason, "lines": [], "done": 0, "total": 0}
