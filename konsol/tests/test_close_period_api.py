"""Period API: konsol/close/period_api.py (konsol#305 A15; stories 0.1, 0.3).

`get_context(fiscal_year=None, fiscal_period=None)` reads the live calendar,
the latest terminal Assertion Run per period, the first close period (Close
Settings) and the loaded periods, and passes them through the pure
period_model (A03/A04). Loaded against a stub frappe (pattern:
test_assertion_warn_amber.py `_load`); the real period_model and
signoff_model are loaded by path, so the landing is D5 itself, not a stub.
"""
import ast
import datetime
import importlib.util
import json
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PY = os.path.join(APP_DIR, "close", "period_api.py")
PERIOD_MODEL_PY = os.path.join(APP_DIR, "close", "period_model.py")
SIGNOFF_MODEL_PY = os.path.join(APP_DIR, "close", "signoff_model.py")
ASSERTION_RUN_PY = os.path.join(
    APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.py")

ALL_CLOSE_ROLES = ("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager")
TODAY = datetime.date(2025, 9, 15)


def _row(fy, fp, start, end, status="Open", ptype="Regular"):
    return {
        "fiscal_year": fy, "fiscal_period": fp,
        "period_code": "%d-P%02d" % (fy, fp), "period_label": "P%02d %d" % (fp, fy),
        "period_type": ptype, "start_date": start, "end_date": end,
        "quarter": "", "status": status,
    }


def _d(m, day):
    return datetime.date(2025, m, day)


# 2025 P05-P09: P05/P06 history-shaped, P07 and P08 Open and ended, P09 contains today.
ROWS = [
    _row(2010, 1, datetime.date(2010, 1, 1), datetime.date(2010, 1, 31)),
    _row(2025, 5, _d(5, 1), _d(5, 31), status="Closed"),
    _row(2025, 6, _d(6, 1), _d(6, 30)),
    _row(2025, 7, _d(7, 1), _d(7, 31)),
    _row(2025, 8, _d(8, 1), _d(8, 31)),
    _row(2025, 9, _d(9, 1), _d(9, 30)),
    _row(2025, 13, _d(12, 31), _d(12, 31), ptype="Adjustment"),
]


class _Site:
    def __init__(self, roles=("EPM Admin",), first_close=(2025, 7), runs=(), loaded=(),
                 rows=None, user="zz-lead@example.com"):
        self.roles = list(roles)
        self.first_close = first_close
        self.runs = list(runs)
        self.loaded = list(loaded)
        self.rows = list(ROWS if rows is None else rows)
        self.user = user
        self.only_for_calls = []
        self.get_all_calls = []
        self.sql_calls = []
        self.single_calls = []
        self.whitelisted = {}


def _load(site):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    def only_for(roles, message=False):
        roles = [roles] if isinstance(roles, str) else list(roles)
        site.only_for_calls.append(tuple(roles))
        if not set(site.roles).intersection(roles):
            raise frappe.PermissionError("Not permitted")

    def whitelist(*a, **k):
        def deco(fn):
            site.whitelisted[fn.__name__] = k
            return fn
        return deco

    def get_all(doctype, filters=None, fields=None, order_by=None, **k):
        site.get_all_calls.append({"doctype": doctype, "filters": filters,
                                   "fields": fields, "order_by": order_by})
        assert doctype == "Assertion Run", doctype
        op, values = filters["status"]
        assert op == "in"
        return [dict(r) for r in site.runs if r["status"] in values]

    def sql(query, values=None, as_dict=False, **k):
        site.sql_calls.append(query)
        assert "tabTrial Balance Submission" in query, query
        assert "docstatus=1" in query.replace(" ", ""), query
        assert "DISTINCT" in query.upper(), query
        return [tuple(k) for k in site.loaded]

    def get_single_value(doctype, field):
        site.single_calls.append((doctype, field))
        assert doctype == "Close Settings", doctype
        fy, fp = site.first_close
        return {"first_close_fiscal_year": fy, "first_close_fiscal_period": fp}[field]

    frappe.throw = throw
    frappe.only_for = only_for
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe.get_roles = lambda user=None: list(site.roles) + ["All", "Guest"]
    frappe.db = types.SimpleNamespace(sql=sql, get_single_value=get_single_value)
    frappe.session = types.SimpleNamespace(user=site.user)
    frappe.utils = types.SimpleNamespace(
        getdate=lambda value=None: TODAY,
        get_fullname=lambda user=None: "ZZ " + (user or site.user),
    )
    frappe._ = lambda s: s

    konsol = types.ModuleType("konsol")
    close = types.ModuleType("konsol.close")

    def _by_path(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    period_model = _by_path("konsol.close.period_model", PERIOD_MODEL_PY)
    signoff_model = _by_path("konsol.close.signoff_model", SIGNOFF_MODEL_PY)
    close.period_model, close.signoff_model = period_model, signoff_model

    fiscal_calendar = types.ModuleType("konsol.fiscal_calendar")
    fiscal_calendar.fiscal_period_rows = lambda: [dict(r) for r in site.rows]

    period_status = types.ModuleType("konsol.period_status")
    PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    period_status.PeriodNotDeclared = PeriodNotDeclared

    def period_row(fiscal_year, fiscal_period):
        if fiscal_year in (None, "") or fiscal_period in (None, ""):
            raise PeriodNotDeclared("No fiscal year and period given.")
        key = (int(fiscal_year), int(fiscal_period))
        for r in site.rows:
            if (r["fiscal_year"], r["fiscal_period"]) == key:
                return {"fiscal_year": key[0], "fiscal_period": key[1],
                        "code": r["period_code"], "type": r["period_type"],
                        "start_date": r["start_date"], "end_date": r["end_date"],
                        "status": r["status"]}
        raise PeriodNotDeclared("FY%d has no period %d." % key)

    period_status.period_row = period_row
    konsol.close, konsol.fiscal_calendar, konsol.period_status = close, fiscal_calendar, period_status

    mods = {"frappe": frappe, "frappe.utils": frappe.utils, "konsol": konsol,
            "konsol.close": close, "konsol.close.period_model": period_model,
            "konsol.close.signoff_model": signoff_model,
            "konsol.fiscal_calendar": fiscal_calendar, "konsol.period_status": period_status}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("close_period_api_under_test", API_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module, frappe, period_status


def _call(site, *args, **kwargs):
    module, frappe, period_status = _load(site)
    mods = {"frappe": frappe}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        return module.get_context(*args, **kwargs)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _run(name, fy, fp, status="Green", signoff="Not Signed Off"):
    return {"name": name, "fiscal_year": fy, "fiscal_period": fp, "status": status,
            "signoff_status": signoff, "completed_at": None}


def _keys(states):
    return [tuple(s["key"]) for s in states]


# --- landing and selection ---------------------------------------------------

def test_with_no_arguments_the_d5_landing_is_selected():
    site = _Site()
    out = _call(site)
    assert tuple(out["landing"]["period"]) == (2025, 7)
    assert out["landing"]["rule"] == "oldest_open_ended"
    assert tuple(out["selected"]["key"]) == (2025, 7)
    # other_open: Open, not history, not the selected one, oldest first.
    assert _keys(out["selected"]["other_open"]) == [(2025, 8), (2025, 9)]
    assert out["config_gaps"] == []


def test_an_explicit_declared_period_is_selected_and_left_out_of_other_open():
    site = _Site()
    out = _call(site, "2025", "8")  # GET arguments arrive as strings
    assert tuple(out["selected"]["key"]) == (2025, 8)
    assert _keys(out["selected"]["other_open"]) == [(2025, 7), (2025, 9)]
    # The landing is still reported, so the screen can offer it.
    assert tuple(out["landing"]["period"]) == (2025, 7)


def test_the_result_is_json_safe_with_iso_dates():
    out = _call(_Site())
    json.dumps(out)  # raises on a date object
    first = next(s for s in out["periods"] if tuple(s["key"]) == (2025, 7))
    assert first["start_date"] == "2025-07-01"
    assert first["end_date"] == "2025-07-31"
    assert out["selected"]["start_date"] == "2025-07-01"


def test_periods_are_the_regular_rows_with_their_state():
    site = _Site(runs=[_run("AR-1", 2025, 7, "Amber", "Acknowledged")], loaded=[(2025, 7)])
    out = _call(site)
    assert _keys(out["periods"]) == [(2010, 1), (2025, 5), (2025, 6), (2025, 7), (2025, 8), (2025, 9)]
    p7 = next(s for s in out["periods"] if tuple(s["key"]) == (2025, 7))
    assert p7["checks"] == "Amber" and p7["signoff"] == "Acknowledged" and p7["is_signed"] is True
    assert p7["run"] == "AR-1"
    p6 = next(s for s in out["periods"] if tuple(s["key"]) == (2025, 6))
    assert p6["is_history"] is True and p6["checks"] == "Not run"


def test_only_the_latest_terminal_run_per_period_counts():
    # The DB returns runs ordered completed_at desc, creation desc: the first per key wins.
    site = _Site(runs=[
        _run("AR-2", 2025, 7, "Red", "Not Signed Off"),
        _run("AR-1", 2025, 7, "Green", "Signed Off"),
        _run("AR-0", 2025, 8, "Running", "Signed Off"),
    ])
    out = _call(site)
    p7 = next(s for s in out["periods"] if tuple(s["key"]) == (2025, 7))
    assert p7["run"] == "AR-2" and p7["is_signed"] is False
    p8 = next(s for s in out["periods"] if tuple(s["key"]) == (2025, 8))
    assert p8["run"] is None  # a non-terminal run is not read
    call = site.get_all_calls[0]
    assert call["filters"]["status"] == ["in", ("Green", "Amber", "Red", "Error")]
    assert call["order_by"] == "completed_at desc, creation desc"
    for field in ("name", "fiscal_year", "fiscal_period", "status", "signoff_status", "completed_at"):
        assert field in call["fields"]


def test_the_viewer_lands_on_the_latest_signed_period_with_a_provisional_switch():
    site = _Site(roles=("EPM User",), runs=[
        _run("AR-1", 2025, 7, "Green", "Signed Off"),
        _run("AR-2", 2025, 8, "Green", "Signed Off"),
    ])
    out = _call(site)
    assert out["me"]["persona"] == "viewer"
    assert tuple(out["landing"]["period"]) == (2025, 8)
    assert out["landing"]["rule"] == "latest_signed"
    assert tuple(out["landing"]["provisional"]["period"]) == (2025, 7)
    assert tuple(out["selected"]["key"]) == (2025, 8)


def test_a_viewer_with_nothing_signed_has_no_selection_and_a_reason():
    out = _call(_Site(roles=("EPM User",)))
    assert out["landing"]["period"] is None
    assert "No period has been signed off yet" in out["landing"]["reason"]
    assert out["selected"] is None


def test_me_names_the_user_their_close_roles_and_persona():
    site = _Site(roles=("EPM Analyst", "Entity Accountant"), user="zz-ga@example.com")
    out = _call(site)
    assert out["me"] == {
        "user": "zz-ga@example.com",
        "full_name": "ZZ zz-ga@example.com",
        "roles": ["EPM Analyst", "Entity Accountant"],
        "persona": "group_accountant",
    }


def test_the_first_close_is_read_from_close_settings():
    site = _Site()
    _call(site)
    assert set(site.single_calls) == {("Close Settings", "first_close_fiscal_year"),
                                      ("Close Settings", "first_close_fiscal_period")}


def test_catch_up_uses_the_loaded_periods():
    site = _Site(loaded=[(2025, 5)])
    out = _call(site)
    p7 = next(s for s in out["periods"] if tuple(s["key"]) == (2025, 7))
    assert p7["catch_up"] == "Catch-up: covers from FY2025 P06"
    assert site.sql_calls, "loaded periods must be read from submitted TBs"


# --- failure paths -----------------------------------------------------------

def test_an_undeclared_period_raises_period_not_declared():
    with pytest.raises(Exception) as err:
        _call(_Site(), 2030, 1)
    assert type(err.value).__name__ == "PeriodNotDeclared"
    assert "2030" in str(err.value)


def test_half_a_period_is_refused_not_guessed():
    site = _Site()
    with pytest.raises(Exception) as err:
        _call(site, 2025, None)
    assert type(err.value).__name__ == "PeriodNotDeclared"


def test_a_non_regular_period_is_refused_with_a_sentence():
    with pytest.raises(Exception) as err:
        _call(_Site(), 2025, 13)
    assert type(err.value).__name__ == "ValidationError"
    assert "Regular" in str(err.value)


def test_a_user_with_no_close_role_is_refused():
    site = _Site(roles=("Accounts User",))
    with pytest.raises(Exception) as err:
        _call(site)
    assert type(err.value).__name__ == "PermissionError"
    assert site.only_for_calls == [ALL_CLOSE_ROLES]
    assert site.get_all_calls == [] and site.sql_calls == []


def test_an_undeclared_first_close_is_a_config_gap_and_never_lands_on_history():
    # Close Settings Int fields read back as 0 when unset (A06): 0 in either part is undeclared.
    for first_close in [(0, 0), (None, None), (2025, 0), (0, 7)]:
        out = _call(_Site(first_close=first_close))
        codes = [g["code"] for g in out["config_gaps"]]
        assert codes == ["first_close_undeclared"], (first_close, codes)
        assert out["landing"]["period"] is None, first_close
        assert out["landing"]["rule"] == "first_close_undeclared"
        assert out["selected"] is None
        assert all(s["is_history"] is None for s in out["periods"])


def test_an_undeclared_first_close_still_serves_an_explicit_period():
    out = _call(_Site(first_close=(0, 0)), 2025, 8)
    assert tuple(out["selected"]["key"]) == (2025, 8)
    assert out["selected"]["other_open"] == []
    assert [g["code"] for g in out["config_gaps"]] == ["first_close_undeclared"]


# --- source rules ------------------------------------------------------------

def test_get_context_is_a_get_endpoint():
    site = _Site()
    _load(site)
    assert site.whitelisted["get_context"] == {"methods": ["GET"]}


def test_terminal_statuses_match_assertion_run():
    tree = ast.parse(open(ASSERTION_RUN_PY).read())
    theirs = next(
        ast.literal_eval(n.value) for n in tree.body
        if isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "TERMINAL_STATUSES" for t in n.targets)
    )
    module, _f, _p = _load(_Site())
    assert tuple(module.TERMINAL_STATUSES) == tuple(theirs)
