"""Who am I, which periods exist, and where do I land? (konsol#305 A15; stories 0.1, 0.3).

``get_context`` reads the live site and passes it through the pure period
model (``konsol.close.period_model``, A03/A04):

- rows: ``fiscal_calendar.fiscal_period_rows()`` (effective status);
- runs: the latest terminal Assertion Run per period, ordered by completion
  (mirrors ``assertion_run.latest_close_run``);
- first close: Close Settings (A36a). Unset Int fields read back as 0, so 0
  in either part is undeclared (``signoff_model.first_close_key``) and becomes
  the named gap ``first_close_undeclared``; it is never passed on as (0, 0);
- loaded keys: periods with a submitted Trial Balance Submission.

Read-only; dates are returned as ISO strings.
"""
import frappe

from konsol import fiscal_calendar, period_status
from konsol.close import period_model, signoff_model

#: The close roles; the gate spells them out (the A01 contract reads a literal).
ALL_CLOSE_ROLES = ("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager")

#: Copied from assertion_run.TERMINAL_STATUSES; a test holds them equal.
TERMINAL_STATUSES = ("Green", "Amber", "Red", "Error")


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _json_state(state):
    out = dict(state)
    out["key"] = list(state["key"])
    out["start_date"] = _iso(state.get("start_date"))
    out["end_date"] = _iso(state.get("end_date"))
    return out


def _json_landing(result):
    out = dict(result)
    if out.get("period") is not None:
        out["period"] = list(out["period"])
    if out.get("provisional") is not None:
        out["provisional"] = _json_landing(out["provisional"])
    return out


def _first_close():
    return signoff_model.first_close_key((
        frappe.db.get_single_value("Close Settings", "first_close_fiscal_year"),
        frappe.db.get_single_value("Close Settings", "first_close_fiscal_period"),
    ))


def _latest_runs():
    rows = frappe.get_all(
        "Assertion Run",
        filters={"status": ["in", TERMINAL_STATUSES]},
        fields=["name", "fiscal_year", "fiscal_period", "status", "signoff_status", "completed_at"],
        order_by="completed_at desc, creation desc",
    )
    runs = {}
    for row in rows:
        key = (int(row["fiscal_year"]), int(row["fiscal_period"]))
        if key not in runs:
            runs[key] = {"name": row["name"], "status": row["status"],
                         "signoff_status": row["signoff_status"]}
    return runs


def _loaded_keys():
    rows = frappe.db.sql(
        "SELECT DISTINCT fiscal_year, fiscal_period "
        "FROM `tabTrial Balance Submission` WHERE docstatus=1"
    )
    return {(int(fy), int(fp)) for fy, fp in rows}


def _me():
    user = frappe.session.user
    roles = [r for r in ALL_CLOSE_ROLES if r in set(frappe.get_roles(user))]
    return {"user": user, "full_name": frappe.utils.get_fullname(user),
            "roles": roles, "persona": period_model.persona(roles)}


def _explicit_key(fiscal_year, fiscal_period):
    row = period_status.period_row(fiscal_year, fiscal_period)
    if row["type"] != "Regular":
        frappe.throw(
            "{0} is an {1} period; the close app works on Regular periods. "
            "Choose a Regular period of FY{2}.".format(row["code"], row["type"], row["fiscal_year"])
        )
    return (int(row["fiscal_year"]), int(row["fiscal_period"]))


@frappe.whitelist(methods=["GET"])
def get_context(fiscal_year=None, fiscal_period=None):
    """The user, every Regular period's state, the D5 landing and the selected period.

    With no period given the landing is selected; an explicit period must be a
    declared Regular period (PeriodNotDeclared propagates).
    """
    frappe.only_for(("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager"))
    me = _me()
    explicit = None
    if fiscal_year is not None or fiscal_period is not None:
        explicit = _explicit_key(fiscal_year, fiscal_period)

    first_close = _first_close()
    rows = fiscal_calendar.fiscal_period_rows()
    model = period_model.period_states(rows, _latest_runs(), first_close, _loaded_keys())
    states = model["states"]
    signed = {tuple(s["key"]) for s in states if s["is_signed"]}
    landing = period_model.landing(rows, signed, me["persona"], frappe.utils.getdate(), first_close)

    selected_key = explicit if explicit is not None else landing["period"]
    selected = None
    if selected_key is not None:
        state = next((s for s in states if tuple(s["key"]) == tuple(selected_key)), None)
        if state is not None:
            selected = _json_state(state)
            selected["other_open"] = [
                _json_state(s) for s in period_model.other_open(states, selected_key, first_close)
            ]

    return {
        "me": me,
        "periods": [_json_state(s) for s in states],
        "landing": _json_landing(landing),
        "selected": selected,
        "config_gaps": model["config_gaps"],
    }
