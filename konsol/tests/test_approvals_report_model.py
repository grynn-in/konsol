"""Approvals Desk report, pure: konsol/close/approvals_report_model.py.

The report lays out ``approvals_api.get_queue``'s payload; it decides
nothing. The payloads here are built by the real pure model
(``approvals_model.queue_items`` and ``waiting_for_me``, with the real
``close_policy_model.self_approval_problem``), then wrapped exactly as
``approvals_api.queue_for`` wraps them, so a change to the queue's shape
breaks these tests rather than passing a hand-built dict.
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


R = _load("test_approvals_report_model_under_test", os.path.join("close", "approvals_report_model.py"))
AM = _load("test_approvals_report_model_approvals_model", os.path.join("close", "approvals_model.py"))
CPM = _load("test_approvals_report_model_close_policy_model", os.path.join("close", "close_policy_model.py"))

#: Mirrors close_event.ENTITY_FIELDS, injected (as test_close_approvals_model.py does).
ENTITY_FIELDS = {
    "Ownership Period": "data_area_id",
    "Historical Equity Rate": "data_area_id",
    "Business Combination": "acquired_entity",
    "Business Disposal": "disposed_entity",
    "Group Exchange Rate": None,
    "Consolidation Journal": None,
    "IC Balance": None,
}


def _docs(owner="alice"):
    """One pending document per approval doctype, in API row shape."""
    return {
        "Historical Equity Rate": [{
            "name": "HER-1", "consolidation_group": "G1", "data_area_id": "DE02",
            "main_account": "3000", "rate_date": "2026-09-30", "historical_rate": 1.2,
            "owner": owner, "created": "2026-10-01T07:00:00+00:00"}],
        "Business Combination": [{
            "name": "BC-1", "consolidation_group": "G1", "acquired_entity": "DE02",
            "acquisition_date": "2026-01-15", "share_acquired_pct": 60, "goodwill": 1000,
            "owner": owner, "created": "2026-10-01T08:00:00+00:00"}],
        "Consolidation Journal": [{
            "name": "CJ-1", "owner": owner, "created": "2026-10-01T09:00:00+00:00",
            "fiscal_year": 2026, "fiscal_period": 7, "adjustment_type": "topside",
            "description": "Accrue bonus\nmore", "total_debit": 18500.0, "currency": "USD",
            "reverse_fiscal_year": 0, "reverse_fiscal_period": 0,
            "duration": "This period only, no reversal",
            "lines": [], "effect": {"headings": [], "sections": [], "no_heading": 0},
            "consolidation_group": "G1", "dimensions": []}],
        "Group Exchange Rate": [{
            "name": "GER-1", "owner": owner, "created": "2026-10-01T10:00:00+00:00",
            "from_currency": "USD", "to_currency": "EUR", "rate_type": "Closing",
            "fiscal_year": 2026, "fiscal_period": 7, "quote_label": "1.10 EUR per USD",
            "change_reason": None}],
        "IC Balance": [{
            "name": "ICB-1", "owner": owner, "created": "2026-10-01T11:00:00+00:00",
            "selling_entity": "UK01", "buying_entity": "DE02", "fiscal_year": 2026,
            "fiscal_period": 7, "ic_sales_amount": 1500.0, "ending_inventory_from_ic": 0}],
    }


def _payload(user="bob", roles=("EPM Admin",), policy="Allowed with reason", owner="alice",
             rejections=None, modified=None, allowed=None):
    """``approvals_api.queue_for``'s return value, built from the real model."""
    docs = _docs(owner)
    preparers = {(dt, d["name"]): frozenset({d["owner"]}) for dt, rows in docs.items() for d in rows}
    modified_by_ref = {(dt, d["name"]): (modified or {}).get((dt, d["name"]), d["created"])
                       for dt, rows in docs.items() for d in rows}
    result = AM.queue_items(docs, preparers, user, roles, policy, CPM.APPROVER_ROLES,
                            CPM.self_approval_problem, ENTITY_FIELDS, allowed,
                            rejections or {}, modified_by_ref)
    items = result["items"]
    counts = {doctype: 0 for doctype in CPM.APPROVAL_DOCTYPES}
    for item in items:
        counts[item["doctype"]] += 1
    return {
        "items": [i for i in items if not i["sent_back"]],
        "sent_back": [i for i in items if i["sent_back"]],
        "counts": counts,
        "hidden": result["hidden"],
        "self_approval": policy or None,
        "can_approve": bool(set(roles) & set(CPM.APPROVER_ROLES)),
        "waiting": AM.waiting_for_me(items),
    }


def _by_name(rows):
    return {row["name"]: row for row in rows}


def test_one_row_per_waiting_item_in_queue_order():
    payload = _payload()
    rows = R.rows(payload)
    assert [r["name"] for r in rows] == [i["name"] for i in payload["items"]]
    assert [r["name"] for r in rows] == ["HER-1", "BC-1", "CJ-1", "GER-1", "ICB-1"]
    assert {r["status"] for r in rows} == {"Waiting"}


def test_row_carries_the_items_own_facts():
    rows = _by_name(R.rows(_payload()))
    cj = rows["CJ-1"]
    assert cj["doctype"] == "Consolidation Journal"
    assert cj["kind_label"] == "Adjustment · CJ-1"
    assert cj["title"] == "Accrue bonus"
    assert cj["detail"] == "Topside · FY2026 P07 · This period only, no reversal"
    assert cj["period"] == "FY2026 P07"
    assert cj["amount"] == 18500.0
    assert cj["currency"] == "USD"
    assert cj["preparer"] == "alice"
    assert cj["waiting_since"] == "2026-10-01 09:00:00"
    her = rows["HER-1"]
    assert her["entity"] == "DE02"
    assert her["period"] is None  # not period-keyed: never a guessed period
    assert her["amount"] is None and her["currency"] is None


def test_document_link_is_the_desk_form():
    rows = _by_name(R.rows(_payload()))
    assert rows["CJ-1"]["desk"] == "/desk/consolidation-journal/CJ-1"
    assert rows["HER-1"]["desk"] == "/desk/historical-equity-rate/HER-1"
    assert rows["BC-1"]["desk"] == "/desk/business-combination/BC-1"
    assert R.desk_path("IC Balance", "ICB 1/2") == "/desk/ic-balance/ICB%201%2F2"


def test_direct_item_can_be_approved_and_rejected_here():
    row = _by_name(R.rows(_payload()))["CJ-1"]
    assert row["approve_mode"] == "direct"
    assert row["can_act"] == 1
    assert row["approve_message"] is None


def test_desk_only_item_is_never_actionable_here():
    row = _by_name(R.rows(_payload()))["BC-1"]
    assert row["approve_mode"] == "desk"
    assert row["can_act"] == 0
    assert "form" in row["action"]


def test_self_approval_with_reason_carries_the_servers_message():
    row = _by_name(R.rows(_payload(user="alice", owner="alice", policy="Allowed with reason")))["CJ-1"]
    expected = CPM.self_approval_problem("Allowed with reason", frozenset({"alice"}), "alice",
                                         "Consolidation Journal", "CJ-1", None, None)
    assert row["approve_mode"] == "reason"
    assert row["can_act"] == 1
    assert row["approve_message"] == expected
    assert expected in row["action"]


def test_blocked_self_approval_is_refused_with_the_servers_message():
    row = _by_name(R.rows(_payload(user="alice", owner="alice", policy="Blocked")))["CJ-1"]
    assert row["approve_mode"] == "refused"
    assert row["can_act"] == 0
    assert row["approve_message"]
    assert row["approve_message"] in row["action"]


def test_not_an_approver_cannot_act():
    rows = R.rows(_payload(roles=("EPM Analyst",)))
    assert {r["approve_mode"] for r in rows if r["doctype"] != "Business Combination"} == {"not_approver"}
    assert {r["can_act"] for r in rows} == {0}


def test_sent_back_items_follow_as_their_own_rows_and_cannot_be_acted_on():
    ref = ("Group Exchange Rate", "GER-1")
    rejection = {"reason": "Wrong source", "actor": "carol", "at": "2026-10-02T09:00:00+00:00"}
    payload = _payload(rejections={ref: rejection})
    assert [i["name"] for i in payload["sent_back"]] == ["GER-1"]
    rows = R.rows(payload)
    assert rows[-1]["name"] == "GER-1"
    assert [r["status"] for r in rows].count("Sent back") == 1
    row = rows[-1]
    assert row["status"] == "Sent back"
    assert row["approve_mode"] == "sent_back"
    assert row["can_act"] == 0
    assert "carol" in row["action"] and "Wrong source" in row["action"] and "2026-10-02" in row["action"]


def test_message_states_the_waiting_count_scope_and_policy():
    msg = R.message(_payload(allowed={"UK01"}))
    assert "waiting for you" in msg
    assert "oldest since 2026-10-01" in msg
    assert "awaiting outside your scope" in msg
    assert "Self-approval policy: Allowed with reason" in msg


def test_message_with_nothing_waiting():
    payload = _payload(roles=("EPM Analyst",))
    msg = R.message(payload)
    assert "Nothing waiting for you" in msg
    assert "Self-approval policy" not in msg  # only an approver is told the policy


def test_a_payload_without_waiting_or_hidden_is_a_contract_break():
    payload = _payload()
    del payload["waiting"]
    with pytest.raises(ValueError):
        R.message(payload)
    payload = _payload()
    del payload["hidden"]
    with pytest.raises(ValueError):
        R.message(payload)


def test_an_unknown_approve_mode_raises_never_guessed():
    payload = _payload()
    payload["items"][0]["approve"] = {"mode": "maybe", "message": None}
    with pytest.raises(ValueError):
        R.rows(payload)


def test_columns_name_every_row_key_shown():
    fields = [c["fieldname"] for c in R.columns()]
    row = R.rows(_payload())[0]
    for field in fields:
        assert field in row
    document = next(c for c in R.columns() if c["fieldname"] == "name")
    assert document["fieldtype"] == "Dynamic Link" and document["options"] == "doctype"
