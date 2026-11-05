"""The Desk "Period Close" report's rows, from the close app's own payloads.

One row per Regular period. Every value comes from a read endpoint:

- ``period_api.get_context()["periods"]``: which periods there are;
- ``signoff_api.get_signoff``: the period status, the sign-off summary, its
  next step (``label``), the gate messages, the commentary counts and the
  ``can_sign`` / ``can_override`` / ``can_reject`` flags;
- ``checks_api.get_checks``: the latest run, its staleness and ``can_run``;
- the signed run's ``signed_off_by`` / ``signed_off_at`` (read by the report).

Nothing here decides a close rule. ``allow`` says which buttons the row
offers, from the payload's own flags, read the way the close app reads them
(signoffMachine.js and Checks.vue): the server refuses whatever it refuses,
and its message is shown as it is.

- run_checks: ``can_run`` and no run in flight (Checks.vue disables Run while
  one runs).
- sign: action ``sign`` or ``acknowledge`` with ``can_sign``; ``override``
  also needs ``can_override`` (signoffMachine review state).
- reject: action ``signed``, period Open, ``can_reject`` (signed state).
- close: action ``signed``, period Open (signed state; the close endpoint
  holds its own role gate).
- reopen: period Closed, never Locked. The close app offers Reopen only from
  its signed-and-closed state; here a Closed period whose summary says
  "reopen it" is offered Reopen too, and the endpoint holds the gates.

Imports nothing from frappe or konsol: ``period_name`` is loaded by path.
"""
import importlib.util as _importlib_util
import os as _os


def _load_period_name():
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name_pcr", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()

OPEN, CLOSED = "Open", "Closed"
SIGN_ACTIONS = ("sign", "acknowledge")
OVERRIDE = "override"
SIGNED = "signed"
BLOCKED = "blocked"

#: checks_model.staleness states and how the row says them (None: status only).
_STALENESS = {
    "not_run": None,
    "running": None,
    "current": None,
    "stale": "stale: the numbers changed after this run",
}


def _key(state):
    return int(state["key"][0]), int(state["key"][1])


def periods_for(periods, fiscal_year):
    """The states of ``fiscal_year``'s Regular periods, oldest first; with no
    year, the Open periods of every year."""
    if fiscal_year:
        chosen = [s for s in periods if _key(s)[0] == int(fiscal_year)]
    else:
        chosen = [s for s in periods if s.get("status") == OPEN]
    return sorted(chosen, key=_key)


def _checks_text(checks):
    latest = checks.get("latest")
    state = checks.get("staleness")
    if state not in _STALENESS:
        raise ValueError("Unknown checks staleness %r; expected one of %s."
                         % (state, ", ".join(_STALENESS)))
    if not latest:
        return "Not run"
    notes = [n for n in (_STALENESS[state], checks.get("staleness_note")) if n]
    return "%s (%s)" % (latest["status"], "; ".join(notes)) if notes else latest["status"]


def _blockers(signoff):
    messages = list(signoff["gates"]["messages"])
    if signoff["action"] == BLOCKED and signoff["label"] not in messages:
        messages.insert(0, signoff["label"])
    return "; ".join(messages)


def _commentary(signoff, group):
    lines = signoff.get("commentary") or []

    def counts(c):
        return "%d of %d headings" % (c["with_commentary"], c["headings"])

    if group:
        line = next((c for c in lines if c["consolidation_group"] == group), None)
        if line is None:
            return "%s is not a root consolidation group of this site" % group
        text = counts(line)
        return text + ("; missing: " + ", ".join(line["missing"]) if line["missing"] else "")
    return "; ".join("%s: %s" % (c["consolidation_group"], counts(c)) for c in lines)


def _allow(signoff, checks):
    action = signoff["action"]
    status = signoff["period_status"]
    can_sign = bool(signoff.get("can_sign"))
    return {
        "run_checks": bool(checks.get("can_run")) and checks.get("staleness") != "running",
        "sign": (action in SIGN_ACTIONS and can_sign)
                or (action == OVERRIDE and can_sign and bool(signoff.get("can_override"))),
        "reject": action == SIGNED and status == OPEN and bool(signoff.get("can_reject")),
        "close": action == SIGNED and status == OPEN,
        "reopen": status == CLOSED,
    }


def row(state, signoff, checks, signed, group, error=None):
    """The report row of one period state (``get_context``'s ``periods``).

    ``signoff`` and ``checks`` are the period's ``get_signoff`` and
    ``get_checks`` payloads; ``signed`` is the summary's run's
    ``{signed_off_by, signed_off_at}`` (or None); ``group`` is a root group
    code or None. ``error`` is the message an endpoint refused the period
    with: the row then shows it and offers no action.
    """
    fy, fp = _key(state)
    out = {"period": period_name(fy, fp), "fiscal_year": fy, "fiscal_period": fp}
    if error is not None:
        out.update({
            "period_status": state.get("status"), "checks": None, "run": None, "signoff": None,
            "signed_by": None, "signed_at": None, "next_step": None, "blockers": error,
            "commentary": None, "action": None, "action_label": None,
            "allow": {k: False for k in ("run_checks", "sign", "reject", "close", "reopen")},
        })
        return out
    run = signoff["checks"]["run"]
    signed = signed or {}
    out.update({
        "period_status": signoff["period_status"],
        "checks": _checks_text(checks),
        "run": run,
        "signoff": signoff["checks"]["signoff_status"] if run else "No checks run",
        "signed_by": signed.get("signed_off_by") or None,
        "signed_at": signed.get("signed_off_at") or None,
        "next_step": signoff["label"],
        "blockers": _blockers(signoff),
        "commentary": _commentary(signoff, group),
        "action": signoff["action"],
        "action_label": signoff["label"],
        "allow": _allow(signoff, checks),
    })
    return out


def columns():
    return [
        {"label": "Period", "fieldname": "period", "fieldtype": "Data", "width": 110},
        {"label": "Period Status", "fieldname": "period_status", "fieldtype": "Data", "width": 110},
        {"label": "Checks", "fieldname": "checks", "fieldtype": "Data", "width": 200},
        {"label": "Checks Run", "fieldname": "run", "fieldtype": "Link", "options": "Assertion Run",
         "width": 150},
        {"label": "Sign-off", "fieldname": "signoff", "fieldtype": "Data", "width": 130},
        {"label": "Signed By", "fieldname": "signed_by", "fieldtype": "Link", "options": "User",
         "width": 160},
        {"label": "Signed At", "fieldname": "signed_at", "fieldtype": "Datetime", "width": 160},
        {"label": "Next Step", "fieldname": "next_step", "fieldtype": "Data", "width": 220},
        {"label": "Blocks Sign-off", "fieldname": "blockers", "fieldtype": "Data", "width": 420},
        {"label": "Commentary", "fieldname": "commentary", "fieldtype": "Data", "width": 260},
    ]


def message(fiscal_year, rows, config_gaps):
    """The line above the table: why it is empty, or the context's setup gaps."""
    if not rows:
        if fiscal_year:
            return "No Regular period of FY%d is declared." % int(fiscal_year)
        return "No Regular period is Open."
    gaps = [g["message"] for g in config_gaps or ()]
    return " ".join(gaps) if gaps else None
