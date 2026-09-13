"""The rules behind the Konsol home (F7, 12 Sep 2026): navigator states, the
eight-stage lane, job titles and queue order. home_model imports nothing from
Frappe, so these run on a host."""
import datetime
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("home_model", os.path.join(APP_DIR, "home_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

TODAY = datetime.date(2026, 9, 12)


def test_fourteen_periods_have_codes_and_labels():
    assert [M.period_code(p) for p in (0, 1, 9, 12, 13)] == ["OPN", "P01", "P09", "P12", "CLS"]
    assert M.period_label(2026, 9) == "Sep 2026"
    assert M.period_label(2026, 0) == "Opening balances"
    assert M.period_label(2026, 13) == "Year-end close"


def test_period_start_matches_the_warehouse_calendar():
    assert M.period_start(2026, 9) == datetime.date(2026, 9, 1)
    assert M.period_start(2026, 0) == datetime.date(2026, 1, 1)
    assert M.period_start(2026, 13) == datetime.date(2026, 12, 31)


def test_navigator_state():
    assert M.period_state("Locked", datetime.date(2026, 1, 1), TODAY) == "locked"
    assert M.period_state("Closed", datetime.date(2026, 8, 1), TODAY) == "closed"
    assert M.period_state("Open", datetime.date(2026, 9, 1), TODAY) == "open"
    assert M.period_state("Open", datetime.date(2026, 10, 1), TODAY) == "future"
    # a closed future period is still closed: status beats the calendar
    assert M.period_state("Closed", datetime.date(2026, 12, 31), TODAY) == "closed"


def test_year_kind():
    assert [M.year_kind(y, 2026) for y in (2025, 2026, 2027)] == ["past", "current", "planning"]


def test_job_titles_follow_priority_and_merge_aliases():
    assert M.job_titles(["EPM User", "EPM Admin"]) == ["Close Lead", "Viewer"]
    assert M.job_titles(["Budget Submitter", "Entity Accountant"]) == ["Entity Accountant"]
    assert M.job_titles(["Budget Controller", "Budget Manager"]) == ["Budget Reviewer"]
    assert M.job_titles(["Guest"]) == []


def test_initials():
    assert M.initials("Anna Keller") == "AK"
    assert M.initials("ops@example.com") == "OE"
    assert M.initials("") == "?"


def test_lane_has_eight_numbered_stages_in_order():
    ids = [sid for sid, _ in M.STAGES]
    assert ids == ["source", "trial_balances", "ownership", "intercompany", "adjustments",
                   "consolidate", "assertions", "signoff"]
    assert set(M.STAGE_OWNERS) == set(ids)
    assert M.stage("signoff", "waiting", "x")["n"] == 8


def test_source_stage():
    assert M.source_stage([])["state"] == "idle"
    ok = {"enabled": 1, "last_sync_status": "Success"}
    assert M.source_stage([ok, ok])["summary"] == "2 of 2 synced"
    assert M.source_stage([ok, {"enabled": 1, "last_sync_status": "Failed"}])["state"] == "error"
    assert M.source_stage([ok, {"enabled": 0, "last_sync_status": "Failed"}])["state"] == "done"


def test_trial_balance_stage():
    s = M.tb_stage({"AMDE", "AMUS", "AMHQ"}, {"AMDE"}, {"AMUS"})
    assert s["state"] == "incomplete"
    assert s["summary"] == "1 of 3 in · 1 draft"
    assert s["missing"] == ["AMHQ", "AMUS"]
    assert M.tb_stage({"AMDE"}, {"AMDE"}, set())["state"] == "done"
    assert M.tb_stage(set(), set(), set(), via_connector={"USMF"})["summary"] == "All 1 via connectors"
    # a submission for an entity outside the close does not count
    assert M.tb_stage({"AMDE"}, {"ZZX"}, set())["state"] == "incomplete"


def test_ownership_stage_counts_the_group_rates():
    """konsol#103: the stage is never Complete while the close gate would
    refuse, and pre-filled drafts are counted."""
    done = M.ownership_stage(set(), 0, 0)
    assert (done["state"], done["summary"], done["missing_rates"]) == ("done", "Complete", [])
    s = M.ownership_stage(set(), 0, 0, missing_rates=["EUR → USD Average", "EUR → USD Closing"])
    assert (s["state"], s["summary"]) == ("incomplete", "2 rates missing")
    assert s["missing_rates"] == ["EUR → USD Average", "EUR → USD Closing"]
    s = M.ownership_stage(set(), 0, 0, group_rate_drafts=3, missing_rates=["EUR → USD Closing"])
    assert s["summary"] == "1 rate missing · 3 rates to approve"
    assert M.ownership_stage(set(), 0, 0, group_rate_drafts=1)["summary"] == "1 rate to approve"
    s = M.ownership_stage(set(), 0, 0, rates_error="ConnectionError")
    assert (s["state"], s["summary"]) == ("error", "rates can't be checked"), "the gate refuses: never Complete"
    s = M.ownership_stage({"AMDE"}, 1, 0, missing_rates=["X"])
    assert s["summary"] == "1 without ownership · 1 rate missing · 1 to submit" and s["missing"] == ["AMDE"]
    assert M.rate_label(("IDR", "USD", "Closing")) == "IDR → USD Closing"


def test_group_rates_in_the_queues():
    """Approval is the Close Lead's; missing rates are the Group Accountant's."""
    drafts, missing = ["EUR → USD Closing", "EUR → USD Average"], ["IDR → USD Closing"]

    def items(lead, group, error=None, period_open=True, d=drafts, m=missing):
        return {(i["queue"], i["id"], i["state"], i["who"], i["action"])
                for i in M.group_rate_items(lead, group, d, m, error, period_open, "Dec 2099")}
    assert items(True, False) == {("mine", "gxr:approve", "paused", None, "approve"),
                                  ("waiting", "gxr:missing", "incomplete", "Group Accountants", None)}
    assert items(False, True) == {("waiting", "gxr:approve", "waiting", "Close Lead", None),
                                  ("mine", "gxr:missing", "incomplete", None, "prefill")}
    assert items(True, True) == {("mine", "gxr:approve", "paused", None, "approve"),
                                 ("mine", "gxr:missing", "incomplete", None, "prefill")}
    assert items(False, False) == set(), "nobody else sees group rates"
    assert ("mine", "gxr:unknown", "error", None, None) in items(True, False, error="ConnectionError")
    assert not any(i[1] == "gxr:missing" for i in items(False, True, period_open=False)), \
        "no rate can be approved into a closed period"
    [approve] = [i for i in M.group_rate_items(True, False, drafts, [], None, True, "Dec 2099")]
    assert approve["title"] == "Approve 2 group exchange rates" and approve["detail"] == ", ".join(drafts)
    assert M.few([str(i) for i in range(8)]) == "0, 1, 2, 3, 4, 5 and 2 more"


def test_ownership_and_ic_stages():
    assert M.ownership_stage({"AMDE"}, 0, 0)["state"] == "incomplete"
    assert M.ownership_stage(set(), 1, 1)["summary"] == "2 to submit"
    assert M.ownership_stage(set(), 0, 0)["state"] == "done"
    assert M.ic_stage(0, 0)["state"] == "idle"
    assert M.ic_stage(3, 1)["state"] == "incomplete"
    assert M.ic_stage(3, 0)["state"] == "done"


def test_adjustments_stage():
    assert M.adjustments_stage({"Pending Approval": 2, "Draft": 1})["state"] == "paused"
    assert M.adjustments_stage({"Draft": 1})["state"] == "incomplete"
    assert M.adjustments_stage({})["summary"] == "None"


def test_consolidate_assertions_and_signoff():
    assert M.consolidate_stage(None)["state"] == "idle"
    assert M.consolidate_stage({"workflow_state": "Pending Review", "name": "B"})["state"] == "paused"
    assert M.consolidate_stage({"workflow_state": "Completed", "name": "B"})["state"] == "done"
    assert M.assertions_stage(None)["state"] == "waiting"
    assert M.assertions_stage({"status": "Green", "passed": 12, "total": 12})["summary"] == "Green · 12 of 12"
    assert M.assertions_stage({"status": "Red", "failed": 2})["state"] == "error"
    assert M.signoff_stage("Open", "done")["state"] == "ready"
    assert M.signoff_stage("Open", "error")["state"] == "waiting"
    assert M.signoff_stage("Locked", "waiting")["state"] == "done"


def test_a_signed_off_or_overridden_run_is_settled():
    assert M.assertions_stage({"status": "Red", "failed": 2, "signoff_status": "Overridden"})["state"] == "done"
    assert M.assertions_stage({"status": "Green", "signoff_status": "Signed Off"})["summary"] == "Signed off"
    assert M.signoff_stage("Open", M.assertions_stage({"status": "Red", "signoff_status": "Overridden"})["state"])["state"] == "ready"


def test_builds_show_as_latest_and_never_on_a_closed_period():
    s = M.consolidate_stage({"workflow_state": "Failed", "name": "B"}, tracked=False)
    assert (s["state"], s["summary"]) == ("idle", "Not shown for a closed period")
    assert M.consolidate_stage({"workflow_state": "Failed", "name": "B"})["summary"] == "Latest build failed"


def test_queue_dedupes_and_puts_urgent_first():
    items = [{"id": "a", "state": "done"}, {"id": "b", "state": "error"},
             {"id": "a", "state": "error"}, {"id": "c", "state": "paused"}]
    out = M.ordered(items)
    assert [i["id"] for i in out] == ["b", "c", "a"]
    assert out[-1]["state"] == "done"   # the first "a" wins over the duplicate
