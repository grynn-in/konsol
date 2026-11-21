"""Setup and empty-state messages say where to go (Deepak Pai, 21 Nov 2026).

On an empty site every Home card read "Declare the first close period
(Close Settings, Close Order) ...", "Never built" or "0", with no path to the
place where the step is done. ``konsol/setup_where.py`` now holds, per setup
step, the Desk path and what to do there; these tests pin every message that
quotes it, the order the steps come in (a fiscal year before the first close
period), and the routes. Pure: every module is loaded by path, no site.
"""
import datetime
import importlib.util
import json
import os
import re

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, *parts):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, *parts))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


W = _load("test_setup_messages_where", "setup_where.py")
PM = _load("test_setup_messages_period_model", "close", "period_model.py")
SM = _load("test_setup_messages_signoff_model", "close", "signoff_model.py")
OM = _load("test_setup_messages_overview_model", "close", "overview_model.py")
MR = _load("test_setup_messages_mywork_report_model", "close", "mywork_report_model.py")
SU = _load("test_setup_messages_setup_model", "setup_model.py")

TODAY = datetime.date(2026, 11, 21)


def _row(fy, fp, start, end, status="Open"):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "FY%d-P%02d" % (fy, fp),
            "period_type": "Regular", "start_date": start, "end_date": end, "status": status}


ROWS = [_row(2026, 11, datetime.date(2026, 11, 1), datetime.date(2026, 11, 30))]
CLOSED = [_row(2026, 11, datetime.date(2026, 11, 1), datetime.date(2026, 11, 30), "Closed")]


# --- the shared source ------------------------------------------------------------

def test_every_setup_step_and_the_first_close_have_a_path_and_a_route():
    keys = {key for key, _label, _route in SU.STEPS}
    assert "first_close" in keys
    assert set(W.WHERE) == keys == set(W.ROUTES)
    for key in keys:
        assert W.where(key).startswith("Go to " + W.path(key) + ": "), key
        assert W.where(key).endswith("."), key
        assert re.fullmatch(r"/desk/[a-z0-9-]+", W.route(key)), key


def test_a_path_starts_at_a_dock_module_or_names_its_list():
    for key, (place, _todo) in W.WHERE.items():
        assert place.split(" → ")[0] in ("Close", "Planning", "Structure", "Data", "the User list "
                                         "(System Manager only)"), (key, place)


def test_each_route_is_the_doctype_the_setup_step_opens():
    """setup_model's ``route`` (``["List", doctype]`` / ``["Form", single]``)
    and setup_where's Desk route name the same place."""
    for key, _label, route in SU.STEPS:
        assert W.route(key) == SU.desk_route(route), key


def test_the_dock_has_the_modules_the_paths_name():
    with open(os.path.join(APP_DIR, "dock", "konsol", "konsol.json")) as f:
        titles = {item["title"] for item in json.load(f)["items"]}
    for place, _todo in W.WHERE.values():
        head = place.split(" → ")[0]
        if not head.startswith("the "):
            assert head in titles, head


def test_the_sidebar_links_the_paths_name_exist():
    """"Structure → EPM Fiscal Years" is a link labelled "EPM Fiscal Years" in
    the Structure sidebar, and so on for every path."""
    sidebars = {}
    for module, name in (("structure", "structure"), ("consolidation", "close"),
                         ("pipeline", "pipeline")):
        with open(os.path.join(APP_DIR, module, "sidebar", name, name + ".json")) as f:
            sidebars[{"close": "Close", "structure": "Structure", "pipeline": "Data"}[name]] = {
                i.get("label") for i in json.load(f)["items"]}
    for key, (place, _todo) in W.WHERE.items():
        parts = place.split(" → ")
        if parts[0] in sidebars:
            assert parts[1] in sidebars[parts[0]], (key, parts)


def test_close_settings_has_the_tab_and_fields_the_first_close_path_names():
    with open(os.path.join(APP_DIR, "consolidation", "doctype", "close_settings",
                           "close_settings.json")) as f:
        labels = {fld.get("label") for fld in json.load(f)["fields"]}
    assert {"Starting Point", "First Close Fiscal Year", "First Close Fiscal Period",
            "Statement Accounts", "Intercompany Declaration"} <= labels


# --- the first close period: a fiscal year comes first ---------------------------

def test_no_fiscal_year_says_to_create_one_before_the_first_close():
    res = PM.landing([], set(), "close_lead", TODAY, first_close=None)
    assert res["period"] is None and res["rule"] == "no_calendar"
    assert W.where("fiscal_calendar") in res["reason"]
    assert res["reason"].index(W.path("fiscal_calendar")) < res["reason"].index("first close")


def test_undeclared_first_close_says_where_to_declare_it():
    res = PM.landing(ROWS, set(), "close_lead", TODAY, first_close=None)
    assert res["rule"] == "first_close_undeclared"
    assert W.where("first_close") in res["reason"]
    assert "(Close Settings, Close Order)" not in res["reason"]
    gaps = PM.period_states(ROWS, {}, None, [])["config_gaps"]
    assert gaps == [{"code": "first_close_undeclared", "message": PM.FIRST_CLOSE_UNDECLARED}]


def test_no_period_containing_today_says_to_add_the_year():
    res = PM.landing(CLOSED, set(), "close_lead", datetime.date(2027, 3, 1), first_close=(2026, 11))
    assert res["period"] is None
    assert W.where("fiscal_calendar") in res["reason"]


def test_the_sign_off_gap_says_where_to_declare_the_first_close():
    gaps = SM.config_gaps(None, (2026, 11), {})
    assert [g["code"] for g in gaps] == [SM.FIRST_CLOSE_UNDECLARED]
    assert W.where("first_close") in gaps[0]["message"]


def test_the_close_refusal_quotes_the_path():
    with open(os.path.join(APP_DIR, "close", "signoff_gate.py")) as f:
        src = f.read()
    assert 'setup_where.where("first_close")' in src


def test_my_work_fix_creates_the_fiscal_year_first_then_declares():
    steps, _slug = MR.FIXES["gap:first_close"]
    assert W.path("fiscal_calendar") in steps[0]
    assert steps[-1] == W.where("first_close")


# --- the Konsol Setup page --------------------------------------------------------

def test_setup_first_close_step_follows_the_fiscal_calendar():
    keys = [key for key, _l, _r in SU.STEPS]
    assert keys.index("first_close") == keys.index("fiscal_calendar") + 1


def test_setup_first_close_step_without_periods_points_at_the_fiscal_year():
    s = SU.first_close_step(has_periods=False, first_close_name=None)
    assert s["state"] == SU.NOT_DONE
    assert W.where("fiscal_calendar") in s["detail"]
    assert s["route"] == ["List", "EPM Fiscal Year"]


def test_setup_first_close_step_undeclared_and_declared():
    s = SU.first_close_step(has_periods=True, first_close_name=None)
    assert s["state"] == SU.NOT_DONE and W.where("first_close") in s["detail"]
    assert s["route"] == ["Form", "Close Settings"]
    done = SU.first_close_step(has_periods=True, first_close_name="FY2026 P11")
    assert done["state"] == SU.DONE and "FY2026 P11" in done["detail"]
    assert "Go to" not in done["detail"]


@pytest.mark.parametrize("step", [
    lambda: SU.fiscal_calendar_step(0, [], 0),
    lambda: SU.consolidation_group_step([], False),
    lambda: SU.entities_step([], [], None),
    lambda: SU.chart_step(0, [], ""),
    lambda: SU.trial_balance_step(0, None),
    lambda: SU.warehouse_step({"clickhouse_reachable": True}, {"state": "never_built"}),
])
def test_a_setup_step_not_done_ends_with_where_to_go(step):
    s = step()
    assert s["state"] != SU.DONE
    assert s["detail"].endswith(W.where(s["key"])), s["detail"]


def test_a_done_step_and_an_unchecked_one_say_no_path():
    assert "Go to" not in SU.trial_balance_step(2, "FY2026 P11")["detail"]
    assert "Go to" not in SU.unchecked("entities", "boom")["detail"]


# --- the Home cards ---------------------------------------------------------------

def _gap(rows, today, first_close):
    return OM.setup_gap(PM.landing(rows, set(), "close_lead", today, first_close))


def test_home_card_with_no_fiscal_year():
    gap = _gap([], TODAY, None)
    assert gap["text"] == "No fiscal year"
    assert gap["hint"] == "Go to %s." % W.path("fiscal_calendar")
    assert gap["route"] == "/desk/epm-fiscal-year"


def test_home_card_with_the_first_close_undeclared():
    gap = _gap(ROWS, TODAY, None)
    assert gap["text"] == "First close not declared"
    assert gap["hint"] == "Go to %s." % W.path("first_close")
    assert gap["route"] == "/desk/close-settings"


def test_home_card_with_no_open_period():
    gap = _gap(CLOSED, datetime.date(2027, 3, 1), (2026, 11))
    assert gap["rule"] is None
    assert gap["text"] == "No open period"
    assert gap["route"] == "/desk/epm-fiscal-year"


def test_a_card_hint_is_escaped_and_inside_the_link():
    html = OM.card("No fiscal year", "unknown", "/desk/epm-fiscal-year", hint="Go to <b>x</b> → y.")
    assert html.startswith('<a href="/desk/epm-fiscal-year">') and html.endswith("</a>")
    assert '<span class="konsol-card-hint">Go to &lt;b&gt;x&lt;/b&gt; → y.</span>' in html
    assert "konsol-card-hint" not in OM.card("0", "ok", "/desk/x")


def test_the_hint_style_is_scoped_to_the_home_page():
    with open(os.path.join(APP_DIR, "public", "css", "konsol_desk.bundle.css")) as f:
        css = f.read()
    assert 'body[data-route="Workspaces/Konsol"] .widget.number-widget-box .konsol-card-hint' in css


def _fresh(state, **kw):
    return dict({"state": state, "as_of": None, "pending": 0, "changed_since": [],
                 "last_failed": None}, **kw)


def test_never_built_says_how_the_first_build_starts():
    hint, route = OM.freshness_hint(_fresh("never_built"))
    assert "Submit a trial balance" in hint and W.path("trial_balance") in hint
    assert W.path("warehouse") in hint
    assert route == "/desk/build-approval"


def test_last_build_failed_opens_the_failed_build():
    failed = _fresh("failed", last_failed={"name": "BAPR-00007", "at": "x", "reason": "dbt exit 2"})
    hint, route = OM.freshness_hint(failed)
    assert route == "/desk/build-approval/BAPR-00007"
    assert "Build Output tab" in hint and "BAPR-00007" in hint


def test_fresh_has_no_hint():
    assert OM.freshness_hint(_fresh("fresh", as_of="2026-11-07T10:42"))[0] is None


def test_the_checklist_build_line_says_how_to_start_and_where_to_look():
    never = OM._build_line(_fresh("never_built"), (2026, 11))
    assert "Submit a trial balance" in never["detail"]
    failed = OM._build_line(_fresh("failed", last_failed={"name": "BAPR-00007", "at": "x",
                                                          "reason": "dbt exit 2"}), (2026, 11))
    assert "dbt exit 2" in failed["detail"] and "Build Output tab" in failed["detail"]
    assert failed["route"] == "/desk/build-approval/BAPR-00007"


def test_no_trial_balance_expected_says_why():
    hint = OM.tb_hint([])
    assert "Ownership Period" in hint and "Reporting Frequency" in hint
    assert OM.tb_hint(["E1"]) is None


# --- the Home checklist with no current period ------------------------------------

def _no_period(rows, today, first_close):
    return OM.no_period(PM.landing(rows, set(), "close_lead", today, first_close))


def test_the_checklist_lists_the_setup_steps_in_order():
    result = _no_period([], TODAY, None)
    assert result["title"] == "No current period"
    lines = result["lines"]
    assert [l["code"] for l in lines] == ["fiscal_calendar", "first_close", "setup"]
    assert lines[0]["state"] == "blocked" and lines[0]["detail"] == W.where("fiscal_calendar")
    assert lines[1]["state"] == "unknown"
    assert "fiscal year" in lines[1]["detail"]
    assert lines[2]["route"] == W.SETUP_PAGE
    for line in lines:
        assert line["route"].startswith("/desk/")


def test_the_checklist_with_the_first_close_undeclared():
    lines = _no_period(ROWS, TODAY, None)["lines"]
    assert lines[0]["state"] == "ok"
    assert lines[1]["state"] == "blocked" and lines[1]["detail"] == W.where("first_close")
    assert lines[1]["route"] == "/desk/close-settings"
