"""Konsol home overview model, pure: konsol/close/overview_model.py (konsol#407 phase 1).

The home workspace's close band (number cards) and period close checklist
(a Custom HTML Block) are laid out here from what the close app's own
readers return: the D5 landing (``period_api.get_context``), the sign-off
gate's readiness (``readiness_model.readiness``), the intercompany line
(``ic_api.signoff_summary``), freshness (``freshness_api.current_freshness``)
and the latest close run. No rule is decided here; these tests pin the
layout, the colours and the escaping. Loaded by path; the module imports
nothing from frappe.
"""
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(APP_DIR, "close", "overview_model.py")
_spec = importlib.util.spec_from_file_location("close_overview_model_under_test", MODEL_PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

KEY = (2025, 7)


def _state(**kw):
    s = {"key": [2025, 7], "code": "P07", "status": "Open", "checks": "Not run",
         "signoff": "Not signed off", "is_signed": False}
    s.update(kw)
    return s


# --- the current period -----------------------------------------------------

def test_current_period_is_the_working_landing():
    assert M.current_period({"period": [2025, 7], "rule": "oldest_open_ended", "reason": None}) == (
        (2025, 7), None)


def test_a_viewer_gets_the_provisional_landing_not_the_latest_signed():
    """The home page shows the close in progress for everyone: the Viewer's
    own landing is the latest signed period, its provisional the working one."""
    landing = {"period": [2025, 5], "rule": "latest_signed", "reason": None,
               "provisional": {"period": [2025, 7], "rule": "contains_today", "reason": None}}
    assert M.current_period(landing) == ((2025, 7), None)


def test_no_current_period_carries_the_reason():
    landing = {"period": None, "rule": "first_close_undeclared", "reason": "Declare it."}
    assert M.current_period(landing) == (None, "Declare it.")


# --- the card HTML ----------------------------------------------------------

def test_card_escapes_text_and_colours_by_state():
    html = M.card('<img src=x onerror="alert(1)">', "blocked")
    assert "<img" not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in html
    assert 'class="indicator-pill red"' in html


@pytest.mark.parametrize("state,colour", [("ok", "green"), ("warn", "orange"),
                                          ("blocked", "red"), ("unknown", "gray")])
def test_card_colours(state, colour):
    assert 'indicator-pill %s"' % colour in M.card("x", state)


def test_card_refuses_an_unknown_state():
    with pytest.raises(ValueError):
        M.card("x", "amber")


def test_card_links_only_to_a_desk_route():
    html = M.card("x", "ok", "/desk/query-report/Period%20Close")
    assert html.startswith('<a href="/desk/query-report/Period%20Close"')
    for bad in ("javascript:alert(1)", "https://evil.example", "//evil.example", "/desk/x\"y"):
        with pytest.raises(ValueError):
            M.card("x", "ok", bad)


# --- the band ---------------------------------------------------------------

def test_close_status_names_the_period_and_the_run():
    assert M.close_status(KEY, _state(checks="Amber")) == ("FY2025 P07 · Amber", "warn")
    assert M.close_status(KEY, _state(checks="Green")) == ("FY2025 P07 · Green", "ok")
    assert M.close_status(KEY, _state(checks="Red")) == ("FY2025 P07 · Red", "blocked")
    assert M.close_status(KEY, _state(checks="Error")) == ("FY2025 P07 · Error", "blocked")
    assert M.close_status(KEY, _state()) == ("FY2025 P07 · Checks not run", "unknown")


def test_close_status_signed_and_closed_and_resign():
    assert M.close_status(KEY, _state(checks="Amber", signoff="Acknowledged", is_signed=True)) == (
        "FY2025 P07 · Signed off", "ok")
    assert M.close_status(KEY, _state(status="Closed", signoff="Signed Off", is_signed=True)) == (
        "FY2025 P07 · Closed", "ok")
    assert M.close_status(KEY, _state(checks="Green", signoff="Re-sign Needed")) == (
        "FY2025 P07 · Re-sign needed", "blocked")


def test_close_status_refuses_an_unknown_run_status():
    with pytest.raises(ValueError):
        M.close_status(KEY, _state(checks="Purple"))


def test_tb_progress_counts_what_the_caller_may_see():
    assert M.tb_progress(["A", "B", "C"], [], None) == ("3 / 3", "ok")
    assert M.tb_progress(["A", "B", "C"], ["C"], None) == ("2 / 3", "blocked")
    # A restricted caller sees their own entities; a hidden missing TB still
    # blocks and is counted, never named (readiness_model E201b).
    assert M.tb_progress(["A", "B", "C"], ["C"], {"A", "B"}) == (
        "2 / 2 · 1 outside your scope missing", "blocked")
    assert M.tb_progress(["A", "B"], [], set()) == ("0 / 0 · 2 outside your scope", "ok")
    assert M.tb_progress([], [], None) == ("0 / 0", "unknown")


def test_waiting_for_me():
    assert M.waiting(0) == ("0", "ok")
    assert M.waiting(4) == ("4", "warn")


def test_ic_over_tolerance_reads_the_gate_line():
    checked = lambda n: {"state": "checked", "message": None,  # noqa: E731
                         "counts": {"pairs": 3, "over_tolerance": n}, "sent_back_open": 0}
    assert M.ic_card(checked(0)) == ("0", "ok")
    assert M.ic_card(checked(2)) == ("2", "warn")
    assert M.ic_card({"state": "not_configured", "message": "m", "counts": None,
                      "sent_back_open": None}) == ("Not configured", "unknown")
    assert M.ic_card({"state": "not_applicable", "message": "m", "counts": None,
                      "sent_back_open": None}) == ("Not applicable", "unknown")
    assert M.ic_card({"state": "not_built", "message": "m", "counts": None,
                      "sent_back_open": None}) == ("Cannot check", "blocked")
    assert M.ic_card({"state": "error", "message": "m", "counts": None,
                      "sent_back_open": None}) == ("Cannot check", "blocked")


def test_tb_exceptions_card():
    assert M.exceptions_card(0) == ("0", "ok")
    assert M.exceptions_card(2) == ("2", "warn")


def test_freshness_card():
    f = lambda state, **kw: dict({"state": state, "as_of": "2026-11-07T10:42:13+01:00",  # noqa: E731
                                  "pending": 0, "changed_since": [], "last_failed": None}, **kw)
    assert M.freshness_card(f("fresh")) == ("Fresh · 2026-11-07 10:42", "ok")
    assert M.freshness_card(f("stale", changed_since=["Entity"])) == (
        "Stale · 2026-11-07 10:42", "warn")
    assert M.freshness_card(f("pending", pending=1)) == ("Build pending", "warn")
    assert M.freshness_card(f("failed")) == ("Last build failed", "blocked")
    assert M.freshness_card(f("never_built", as_of=None)) == ("Never built", "blocked")
    with pytest.raises(ValueError):
        M.freshness_card(f("purple"))


# --- the checklist ----------------------------------------------------------

def _readiness(**states):
    codes = ("period_open", "first_close", "previous_signed", "policies", "configuration",
             "ownership", "trial_balances", "rates", "checks")
    return {"ready": 0, "total": len(codes), "items": [
        {"code": c, "state": states.get(c, "ok"), "label": c.title(), "detail": "d-" + c,
         "entities": [], "hidden": 0} for c in codes]}


def _inputs(**kw):
    base = dict(
        key=KEY,
        readiness=_readiness(),
        run={"name": "AR-1", "status": "Green", "signoff_status": "Signed Off"},
        ic={"state": "checked", "message": None, "counts": {"pairs": 1, "over_tolerance": 0},
            "sent_back_open": 0},
        journals_open=0,
        equity_pending=0,
        freshness={"state": "fresh", "as_of": "2026-11-07T10:42:13+01:00", "pending": 0,
                   "changed_since": [], "last_failed": None},
    )
    base.update(kw)
    return base


def test_checklist_has_one_line_per_check_in_a_fixed_order():
    result = M.checklist(**_inputs())
    assert [l["code"] for l in result["lines"]] == list(M.CHECKLIST)
    assert result["title"] == "FY2025 P07"
    assert result["done"] == len(M.CHECKLIST) and result["total"] == len(M.CHECKLIST)
    for line in result["lines"]:
        assert line["route"].startswith("/desk/"), line
        assert line["state"] in M.STATES


def test_checklist_readiness_lines_carry_the_gate_result():
    r = _readiness(trial_balances="blocked")
    r["items"][6].update(detail="2 trial balances missing", entities=["E1", "E2"], hidden=1)
    lines = {l["code"]: l for l in M.checklist(**_inputs(readiness=r))["lines"]}
    tb = lines["trial_balances"]
    assert tb["state"] == "blocked"
    assert tb["detail"] == "2 trial balances missing (E1, E2 and 1 outside your scope)"
    assert tb["route"] == "/desk/trial-balance-submission?fiscal_year=2025&fiscal_period=7"


def test_checklist_amber_checks_are_amber_not_ticked():
    run = {"name": "AR-1", "status": "Amber", "signoff_status": "Not Signed Off"}
    lines = {l["code"]: l for l in M.checklist(**_inputs(run=run))["lines"]}
    assert lines["checks"]["state"] == "warn"
    assert lines["signed_off"]["state"] == "blocked"
    assert lines["signed_off"]["detail"] == "Not signed off"


def test_checklist_signed_off_line():
    lines = lambda run: {l["code"]: l for l in M.checklist(**_inputs(run=run))["lines"]}  # noqa: E731
    assert lines(None)["signed_off"] == dict(lines(None)["signed_off"], state="blocked",
                                             detail="Checks not run")
    resign = lines({"name": "AR-1", "status": "Green", "signoff_status": "Re-sign Needed"})
    assert resign["signed_off"]["state"] == "blocked"
    assert resign["signed_off"]["detail"] == "Re-sign needed"
    with pytest.raises(ValueError):
        lines({"name": "AR-1", "status": "Green", "signoff_status": "Maybe"})


def test_checklist_intercompany_line():
    over = {"state": "checked", "message": None, "counts": {"pairs": 4, "over_tolerance": 2},
            "sent_back_open": 0}
    line = {l["code"]: l for l in M.checklist(**_inputs(ic=over))["lines"]}["intercompany"]
    assert line["state"] == "warn"
    assert line["detail"] == "2 pairs over tolerance: acknowledge at sign-off or correct them"
    broken = {"state": "error", "message": "ClickHouse said no.", "counts": None,
              "sent_back_open": None}
    line = {l["code"]: l for l in M.checklist(**_inputs(ic=broken))["lines"]}["intercompany"]
    assert line == dict(line, state="blocked", detail="ClickHouse said no.")


def test_checklist_journals_equity_and_build_lines():
    lines = {l["code"]: l for l in M.checklist(**_inputs(
        journals_open=3, equity_pending=1,
        freshness={"state": "stale", "as_of": "2026-11-07T10:42:13+01:00", "pending": 0,
                   "changed_since": ["Entity", "IC Balance"], "last_failed": None}))["lines"]}
    assert lines["journals"]["state"] == "warn"
    assert lines["journals"]["detail"] == "3 journals are drafts or awaiting approval"
    assert lines["journals"]["route"] == (
        "/desk/consolidation-journal?fiscal_year=2025&fiscal_period=7")
    assert lines["equity_rates"]["state"] == "warn"
    assert lines["equity_rates"]["detail"] == "1 equity rate awaiting approval"
    assert lines["build"]["state"] == "warn"
    assert lines["build"]["detail"] == "Changed since the last build: Entity, IC Balance"


def test_checklist_counts_only_ticked_lines_as_done():
    result = M.checklist(**_inputs(journals_open=1, readiness=_readiness(rates="blocked")))
    assert result["done"] == len(M.CHECKLIST) - 2


def test_checklist_with_no_current_period():
    result = M.no_period("Declare the first close period.")
    assert result == {"title": "No current period", "reason": "Declare the first close period.",
                      "lines": [], "done": 0, "total": 0}
