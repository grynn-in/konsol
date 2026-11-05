"""My Work Desk report, pure: konsol/close/mywork_report_model.py.

The report lays out ``mywork_api.get_my_work``'s payload. Every item here is
built by the real pure model (``mywork_model.setup_gap_items``,
``period_items``, ``approvals_item``, ``sent_back_items``,
``signoff_sent_back_items``, ``rank``), the same calls ``get_my_work`` makes,
so a change to an item's shape breaks these tests.

The Desk is the target UI: a period item's Close-app screen maps to the
nearest Desk destination, or to no link. A ``/close`` link is never shown.
"""
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = _load("test_mywork_report_model_under_test", os.path.join("close", "mywork_report_model.py"))
MW = _load("test_mywork_report_model_mywork_model", os.path.join("close", "mywork_model.py"))

FIRST = (2025, 7)
P07 = (2025, 7)
P08 = (2025, 8)


def _facts(**over):
    facts = {
        "first_close": FIRST,
        "chart_published": True,
        "frequency_missing": [],
        "ownership_missing": [],
        "accountants_without_entities": [],
        "policy_gaps": [],
        "ic_accounts_gap": None,
        "ic_tolerance_gap": None,
        "ic_rule_gaps": [],
    }
    facts.update(over)
    return facts


def _period(code, **over):
    facts = {
        "code": code, "ended": True, "my_missing": [], "missing": [], "checks": "current",
        "failed": 0, "signoff": "Not signed off", "gates_blocked": False, "rates_missing": 0,
        "status": "Open", "unowned": [], "since": "2025-07-31",
    }
    facts.update(over)
    return facts


def _close_lead_payload():
    items = MW.setup_gap_items(_facts(chart_published=False))
    items += MW.period_items("close_lead", {
        P07: _period("FY2025 P07", missing=["ZZA"], rates_missing=2, unowned=["ZZB"],
                     checks="stale"),
        P08: _period("FY2025 P08", since="2025-08-31", signoff="Signed Off"),
    }, FIRST)
    items.append(MW.approvals_item({"count": 3, "oldest": "2026-10-01T09:00:00+00:00"}))
    rejection = {"reason": "Wrong rate", "actor": "carol", "at": "2026-10-02T09:00:00"}
    items += MW.sent_back_items([
        {"doctype": "Consolidation Journal", "name": "CJ-1", "kind_label": "Adjustment · CJ-1",
         "title": "Accrue", "fiscal_year": 2025, "fiscal_period": 7, "rejection": rejection},
        {"doctype": "Historical Equity Rate", "name": "HER-1", "kind_label": "Historical equity rate",
         "title": "DE02 · 3000 · 2026-09-30", "fiscal_year": None, "fiscal_period": None,
         "rejection": rejection},
        {"doctype": "IC Balance", "name": "ICB-1", "kind_label": "IC balance · UK01 → DE02",
         "title": "IC balance · UK01 → DE02", "fiscal_year": 2025, "fiscal_period": 8,
         "rejection": rejection},
    ], "close_lead", {P07: "FY2025 P07", P08: "FY2025 P08"})
    items += MW.signoff_sent_back_items(
        [{"name": "CE-000000001", "kind": "signoff_rejected", "fiscal_year": 2025, "fiscal_period": 7,
          "actor": "carol", "at": "2026-10-03T09:00:00", "reason": "Recheck",
          "detail": {"preparer": "dave"}}],
        "dave", "close_lead", {P07: "FY2025 P07"}, {"carol": "Carol C"})
    return {"items": MW.rank(items), "counts": {}, "entities_assigned": None}


def _entity_accountant_payload():
    items = MW.period_items("entity_accountant", {
        P07: _period("FY2025 P07", my_missing=["ZZA"], missing=["ZZA"]),
    }, FIRST)
    return {"items": MW.rank(items), "counts": {}, "entities_assigned": True}


def _by_id(rows):
    return {row["id"]: row for row in rows}


def test_one_row_per_item():
    payload = _close_lead_payload()
    rows = R.rows(payload)
    assert sorted(r["id"] for r in rows) == sorted(i["id"] for i in payload["items"])


def test_blocking_rows_come_first_then_to_do_then_waiting():
    rows = R.rows(_close_lead_payload())
    kinds = [r["kind"] for r in rows]
    assert kinds == sorted(kinds, key=["Blocking", "To do", "Waiting"].index)
    assert kinds[0] == "Blocking" and "To do" in kinds and "Waiting" in kinds


def test_order_within_a_kind_is_the_apis_own():
    payload = _close_lead_payload()
    payload["items"] = list(reversed(payload["items"]))
    rows = R.rows(payload)
    for kind in ("Blocking", "To do", "Waiting"):
        got = [r["id"] for r in rows if r["kind"] == kind]
        want = [i["id"] for i in payload["items"] if R.KIND_LABELS[i["kind"]] == kind]
        assert got == want


def test_row_carries_title_message_owner_period_and_since():
    rows = _by_id(R.rows(_close_lead_payload()))
    unowned = rows["unowned:2025-07"]
    assert unowned["title"] == "Trial balance with no ownership (1)"
    assert unowned["message"] == "ZZB"
    assert unowned["owner"] == "EPM Admin"
    assert unowned["period"] == "FY2025 P07"
    assert unowned["since"] == "2025-07-31"
    gap = rows["gap:chart"]
    assert gap["period"] is None
    assert gap["message"] == "Publish the Main Accounts of the group chart."
    assert gap["since"] is None
    sent = rows["sent-back:Historical Equity Rate:HER-1"]
    assert sent["since"] == "2026-10-02"
    assert "Wrong rate" in sent["message"]


def test_setup_gap_desk_paths_pass_through():
    rows = _by_id(R.rows(_close_lead_payload()))
    assert rows["gap:chart"]["open"] == "/desk/main-account"
    assert rows["unowned:2025-07"]["open"] == "/desk/ownership-period"
    assert rows["sent-back:IC Balance:ICB-1"]["open"] == "/desk/ic-balance/ICB-1"


def test_period_screens_map_to_the_nearest_desk_destination():
    rows = _by_id(R.rows(_close_lead_payload()))
    assert rows["rates:2025-07"]["open"] == "/desk/group-exchange-rate?fiscal_year=2025&fiscal_period=7"
    assert rows["tbs-waiting:2025-07"]["open"] == "/desk/trial-balance-submission?fiscal_year=2025&fiscal_period=7"
    assert rows["checks-waiting:2025-07"]["open"] == "/desk/assertion-run?fiscal_year=2025&fiscal_period=7"
    assert rows["close:2025-08"]["open"] == "/desk/assertion-run?fiscal_year=2025&fiscal_period=8"
    assert rows["approvals"]["open"] == "/desk/query-report/Approvals"


def test_entity_items_filter_the_trial_balance_list_to_the_entity():
    rows = R.rows(_entity_accountant_payload())
    assert rows[0]["open"] == ("/desk/trial-balance-submission?fiscal_year=2025&fiscal_period=7"
                               "&data_area_id=ZZA")


def test_sent_back_draft_opens_its_own_desk_form():
    rows = _by_id(R.rows(_close_lead_payload()))
    assert rows["sent-back:Consolidation Journal:CJ-1"]["open"] == "/desk/consolidation-journal/CJ-1"
    assert rows["sent-back:Historical Equity Rate:HER-1"]["open"] == "/desk/historical-equity-rate/HER-1"


def test_rejected_sign_off_opens_the_periods_runs():
    rows = _by_id(R.rows(_close_lead_payload()))
    assert rows["sent-back:signoff:2025-07"]["open"] == "/desk/assertion-run?fiscal_year=2025&fiscal_period=7"


def test_no_row_ever_links_the_close_app():
    for payload in (_close_lead_payload(), _entity_accountant_payload()):
        for row in R.rows(payload):
            assert "/close" not in (row["open"] or "")
            assert not row["open"] or row["open"].startswith("/desk/")


def test_every_close_screen_has_a_declared_desk_destination_or_none():
    for screen in ("my-work", "approvals", "period", "trial-balances", "intercompany", "rates",
                   "adjustments", "checks", "numbers", "sign-off", "audit-trail"):
        assert screen in R.SCREEN_DESTINATIONS


def test_numbers_opens_the_consolidated_statement_for_the_period():
    item = {"id": "x:2025-07", "kind": "todo", "title": "t", "owner": "EPM Admin",
            "action": {"screen": "numbers"},
            "period": {"fiscal_year": 2025, "fiscal_period": 7, "code": "FY2025 P07", "since": None}}
    assert R.open_path(item) == ("/desk/query-report/Consolidated%20Statement"
                                 "?fiscal_year=2025&fiscal_period=7")


def test_a_screen_with_no_desk_destination_shows_no_link():
    item = {"id": "x", "kind": "todo", "title": "t", "owner": "EPM Admin", "action": {"screen": "my-work"}}
    assert R.open_path(item) is None


def test_an_unknown_screen_raises_never_guessed():
    item = {"id": "x", "kind": "todo", "title": "t", "owner": "EPM Admin", "action": {"screen": "nowhere"}}
    with pytest.raises(ValueError):
        R.open_path(item)


def test_a_close_app_path_in_a_desk_action_is_refused():
    item = {"id": "x", "kind": "todo", "title": "t", "owner": "EPM Admin", "action": {"desk": "/close/rates"}}
    with pytest.raises(ValueError):
        R.open_path(item)


def test_an_unknown_kind_raises():
    payload = {"items": [{"id": "x", "kind": "urgent", "title": "t", "owner": "EPM Admin",
                          "action": {"screen": "my-work"}}]}
    with pytest.raises(ValueError):
        R.rows(payload)


def test_approval_doctypes_are_close_policys():
    cpm = _load("test_mywork_report_model_cpm", os.path.join("close", "close_policy_model.py"))
    assert tuple(R.APPROVAL_DOCTYPES) == tuple(cpm.APPROVAL_DOCTYPES)


def test_columns_name_every_row_key_shown():
    fields = [c["fieldname"] for c in R.columns()]
    row = R.rows(_close_lead_payload())[0]
    for field in fields:
        assert field in row
