"""Rates model, pure: konsol/close/rates_model.py (konsol#305 E401, E402).

Loaded by path; the module imports nothing from frappe or konsol.

E402 injects the real ``self_approval_problem`` and ``APPROVER_ROLES`` from
close_policy_model.py (loaded by path too), rather than copying the policy
rule (#305-W2-14).
"""
import ast
import datetime
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "rates_model_under_test", os.path.join(APP_DIR, "close", "rates_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

CPM_PATH = os.path.join(APP_DIR, "close", "close_policy_model.py")
_cpm_spec = importlib.util.spec_from_file_location(
    "rates_model_test_close_policy_model", CPM_PATH)
CPM = importlib.util.module_from_spec(_cpm_spec)
_cpm_spec.loader.exec_module(CPM)
SELF_APPROVAL_PROBLEM = CPM.self_approval_problem
APPROVER_ROLES = CPM.APPROVER_ROLES


def _doc(name, from_currency, to_currency, rate_type, rate, docstatus, owner,
         modified, quote=None, quoted_per=1, erp_rate=None, change_reason=None, source=None):
    return {
        "name": name,
        "from_currency": from_currency,
        "to_currency": to_currency,
        "rate_type": rate_type,
        "quote": quote if quote is not None else rate,
        "quoted_per": quoted_per,
        "rate": rate,
        "erp_rate": erp_rate,
        "docstatus": docstatus,
        "owner": owner,
        "modified": modified,
        "change_reason": change_reason,
        "source": source,
    }


def _earlier(name, from_currency, to_currency, rate_type, rate, fiscal_year, fiscal_period, quoted_per=1):
    return {
        "name": name,
        "from_currency": from_currency,
        "to_currency": to_currency,
        "rate_type": rate_type,
        "quote": rate,
        "quoted_per": quoted_per,
        "rate": rate,
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
    }


def _no_op_move_problem(rate, previous, erp_rate, unit="", threshold=None):
    return None


def test_required_pair_with_no_docs_is_missing_both_cells_and_flag_never_called():
    calls = []

    def move_problem(rate, previous, erp_rate, unit="", threshold=None):
        calls.append((rate, previous, erp_rate, unit, threshold))
        return "SHOULD NOT BE CALLED"

    result = M.grid(required={("EUR", "GBP")}, docs=[], earlier=[], threshold=0.5, move_problem=move_problem)
    assert len(result["rows"]) == 1
    row = result["rows"][0]
    assert row["required"] is True
    for cell in (row["closing"], row["average"]):
        assert cell["status"] == "missing"
        assert cell["flag"] is None
        assert cell["name"] is None
    assert calls == []


def test_approved_closing_plus_draft_average_statuses_and_owner_carried():
    docs = [
        _doc("GER-1", "EUR", "GBP", "Closing", 0.86, 1, "alice@example.com", "2026-09-01 10:00:00"),
        _doc("GER-2", "EUR", "GBP", "Average", 0.85, 0, "bob@example.com", "2026-09-02 10:00:00"),
    ]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5, move_problem=_no_op_move_problem)
    row = result["rows"][0]
    assert row["closing"]["status"] == "approved"
    assert row["closing"]["owner"] == "alice@example.com"
    assert row["average"]["status"] == "awaiting_approval"
    assert row["average"]["owner"] == "bob@example.com"


def test_previous_is_latest_earlier_period_with_label_and_delta():
    docs = [_doc("GER-9", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00")]
    earlier = [
        _earlier("GER-P05", "EUR", "GBP", "Closing", 0.80, 2025, 5),
        _earlier("GER-P03", "EUR", "GBP", "Closing", 0.70, 2025, 3),
    ]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=earlier, threshold=0.5,
                     move_problem=_no_op_move_problem)
    cell = result["rows"][0]["closing"]
    assert cell["previous"]["label"] == "FY2025 P5 (GER-P05)"
    assert cell["previous"]["rate"] == 0.80
    assert abs(cell["delta"] - (0.90 / 0.80 - 1)) < 1e-12


def test_missing_previous_rate_gives_none_delta_and_previous_none_arg():
    calls = []

    def move_problem(rate, previous, erp_rate, unit="", threshold=None):
        calls.append(previous)
        return None

    docs = [_doc("GER-1", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00")]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5, move_problem=move_problem)
    cell = result["rows"][0]["closing"]
    assert cell["previous"] is None
    assert cell["delta"] is None
    assert calls == [None]


def test_undeclared_threshold_is_never_defaulted():
    seen_thresholds = []

    def move_problem(rate, previous, erp_rate, unit="", threshold=None):
        seen_thresholds.append(threshold)
        if threshold is None:
            return "DECLARE-SENTINEL"
        return None

    docs = [
        _doc("GER-1", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00"),
        _doc("GER-2", "EUR", "GBP", "Average", 0.91, 1, "alice@example.com", "2026-09-01 10:00:00"),
    ]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=None, move_problem=move_problem)
    assert result["rows"][0]["closing"]["flag"] == "DECLARE-SENTINEL"
    assert result["rows"][0]["average"]["flag"] == "DECLARE-SENTINEL"
    assert seen_thresholds == [None, None]


def test_threshold_passed_through_and_unit_is_to_per_from():
    calls = []

    def move_problem(rate, previous, erp_rate, unit="", threshold=None):
        calls.append((rate, previous, erp_rate, unit, threshold))
        return None

    docs = [_doc("GER-1", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00",
                  erp_rate=0.88)]
    M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.3, move_problem=move_problem)
    assert len(calls) == 1
    rate, previous, erp_rate, unit, threshold = calls[0]
    assert rate == 0.90
    assert erp_rate == 0.88
    assert unit == "GBP per EUR"
    assert threshold == 0.3


def test_doc_for_pair_with_no_required_pair_goes_to_unrequired():
    docs = [_doc("GER-1", "JPY", "GBP", "Closing", 0.0066, 1, "alice@example.com", "2026-09-01 10:00:00")]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5,
                     move_problem=_no_op_move_problem)
    row_pairs = [(r["from_currency"], r["to_currency"]) for r in result["rows"]]
    assert ("JPY", "GBP") not in row_pairs
    assert len(result["unrequired"]) == 1
    unrequired_row = result["unrequired"][0]
    assert (unrequired_row["from_currency"], unrequired_row["to_currency"]) == ("JPY", "GBP")
    assert unrequired_row["required"] is False


def test_required_none_puts_every_docs_pair_in_rows_and_unrequired_is_empty():
    docs = [
        _doc("GER-1", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00"),
        _doc("GER-2", "USD", "GBP", "Closing", 0.79, 1, "alice@example.com", "2026-09-01 10:00:00"),
    ]
    result = M.grid(required=None, docs=docs, earlier=[], threshold=0.5, move_problem=_no_op_move_problem)
    assert result["unrequired"] == []
    assert len(result["rows"]) == 2
    for row in result["rows"]:
        assert row["required"] is None


def test_two_drafts_shows_latest_modified_other_in_extra_drafts():
    docs = [
        _doc("GER-OLD", "EUR", "GBP", "Closing", 0.90, 0, "alice@example.com", "2026-09-01 09:00:00"),
        _doc("GER-NEW", "EUR", "GBP", "Closing", 0.91, 0, "bob@example.com", "2026-09-01 11:00:00"),
    ]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5,
                     move_problem=_no_op_move_problem)
    cell = result["rows"][0]["closing"]
    assert cell["name"] == "GER-NEW"
    assert cell["extra_drafts"] == ["GER-OLD"]


def test_approved_plus_draft_shows_approved_draft_in_extra_drafts():
    docs = [
        _doc("GER-APPROVED", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 09:00:00"),
        _doc("GER-DRAFT", "EUR", "GBP", "Closing", 0.91, 0, "bob@example.com", "2026-09-01 11:00:00"),
    ]
    result = M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5,
                     move_problem=_no_op_move_problem)
    cell = result["rows"][0]["closing"]
    assert cell["name"] == "GER-APPROVED"
    assert cell["status"] == "approved"
    assert cell["extra_drafts"] == ["GER-DRAFT"]


def test_unknown_rate_type_raises_value_error_naming_it():
    docs = [_doc("GER-1", "EUR", "GBP", "Spot", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00")]
    try:
        M.grid(required={("EUR", "GBP")}, docs=docs, earlier=[], threshold=0.5, move_problem=_no_op_move_problem)
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "Spot" in str(e)


def test_summary_counts_add_up_to_twice_row_count():
    docs = [
        _doc("GER-1", "EUR", "GBP", "Closing", 0.90, 1, "alice@example.com", "2026-09-01 10:00:00"),
        _doc("GER-2", "USD", "GBP", "Average", 0.79, 0, "alice@example.com", "2026-09-01 10:00:00"),
    ]
    result = M.grid(required={("EUR", "GBP"), ("USD", "GBP"), ("JPY", "GBP")}, docs=docs, earlier=[],
                     threshold=0.5, move_problem=_no_op_move_problem)
    summary = result["summary"]
    assert sum(summary.values()) == 2 * len(result["rows"])
    assert sum(summary.values()) == 6


def test_approve_mode_by_an_analyst_is_not_approver_whatever_the_owner_or_policy():
    """Failure path — an Analyst never approves (R2)."""
    for policy in ("", "Blocked", "Allowed with reason", "Sometimes"):
        for preparers in ({"alice"}, {"bob"}):
            mode = M.approve_mode("bob", ("EPM Analyst",), preparers, "IC Balance", "ICB-1",
                                   policy, APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
            assert mode == {"mode": "not_approver", "message": "The Close Lead approves (R2)."}


def test_approve_mode_close_lead_approving_anothers_draft_is_direct_under_every_policy():
    for policy in ("Blocked", "Allowed with reason", ""):
        mode = M.approve_mode("lead", ("EPM Admin",), {"alice"}, "IC Balance", "ICB-1",
                               policy, APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
        assert mode == {"mode": "direct", "message": None}


def test_approve_mode_self_approval_under_blocked_is_refused():
    """Failure path — self-approval under Blocked is refused, and a reason never
    turns it into 'reason'."""
    mode = M.approve_mode("alice", ("EPM Admin",), {"alice"}, "IC Balance", "ICB-1",
                           "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    assert mode["mode"] == "refused"
    assert "blocks self-approval" in mode["message"]


def test_approve_mode_self_approval_under_allowed_with_reason_gives_reason_mode():
    mode = M.approve_mode("alice", ("EPM Admin",), {"alice"}, "IC Balance", "ICB-1",
                           "Allowed with reason", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    assert mode["mode"] == "reason"
    assert "approval_api.approve" in mode["message"]


def test_approve_mode_self_approval_with_undeclared_policy_is_refused():
    """Failure path — undeclared policy refuses, naming Close Settings."""
    mode = M.approve_mode("alice", ("EPM Admin",), {"alice"}, "IC Balance", "ICB-1",
                           "", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    assert mode["mode"] == "refused"
    assert "Close Settings" in mode["message"]


def test_approve_mode_self_approval_with_unknown_policy_is_refused():
    mode = M.approve_mode("alice", ("EPM Admin",), {"alice"}, "IC Balance", "ICB-1",
                           "Sometimes", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    assert mode["mode"] == "refused"


def test_approve_mode_w2_14_editor_approver_is_refused_under_blocked():
    """Failure path, the W2-14 case: owner 'analyst', preparers {'analyst',
    'lead'}, user 'lead', policy Blocked -> refused."""
    mode = M.approve_mode("lead", ("EPM Admin",), {"analyst", "lead"}, "Ownership Period", "OP-1",
                           "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    assert mode["mode"] == "refused"


def test_approve_mode_w2_14_editor_approver_gets_reason_mode_under_allowed_with_reason():
    mode = M.approve_mode("lead", ("EPM Admin",), {"analyst", "lead"}, "Ownership Period", "OP-1",
                           "Allowed with reason", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    assert mode["mode"] == "reason"


def test_approve_mode_w2_14_non_editor_approver_is_direct():
    mode = M.approve_mode("lead", ("EPM Admin",), {"analyst"}, "Ownership Period", "OP-1",
                           "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    assert mode == {"mode": "direct", "message": None}


def _her(name, consolidation_group, data_area_id, main_account, rate_date, historical_rate,
         owner, created):
    return {
        "name": name,
        "consolidation_group": consolidation_group,
        "data_area_id": data_area_id,
        "main_account": main_account,
        "rate_date": rate_date,
        "historical_rate": historical_rate,
        "owner": owner,
        "created": created,
    }


def _op(name, consolidation_group, data_area_id, effective_date, end_date, ownership_pct,
        consolidation_method, owner, created):
    return {
        "name": name,
        "consolidation_group": consolidation_group,
        "data_area_id": data_area_id,
        "effective_date": effective_date,
        "end_date": end_date,
        "ownership_pct": ownership_pct,
        "consolidation_method": consolidation_method,
        "owner": owner,
        "created": created,
    }


def test_pending_items_sorts_oldest_first_and_shapes_her_and_op():
    her = [_her("HER-1", "G1", "ZZENT", "4000 - Revenue", "2026-09-30", 1.2345,
                "alice", "2026-09-02T10:00:00")]
    ops = [_op("OP-1", "G1", "ZZENT", "2026-01-01", None, 80, "Full", "alice",
               "2026-09-01T09:00:00")]
    preparers_by_name = {"HER-1": frozenset({"alice"}), "OP-1": frozenset({"alice"})}
    items = M.pending_items(her, ops, preparers_by_name, "lead", ("EPM Admin",),
                             "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    assert [item["name"] for item in items] == ["OP-1", "HER-1"]

    op_item, her_item = items
    assert op_item["doctype"] == "Ownership Period"
    assert op_item["title"] == "ZZENT in G1 from 2026-01-01"
    assert op_item["detail"] == "80% · Full"
    assert op_item["preparer"] == "alice"
    assert op_item["edited_by"] == []
    assert op_item["created"] == "2026-09-01T09:00:00"
    assert op_item["approve"] == M.approve_mode("lead", ("EPM Admin",), {"alice"}, "Ownership Period",
                                                 "OP-1", "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)

    assert her_item["doctype"] == "Historical Equity Rate"
    assert her_item["title"] == "ZZENT · 4000 - Revenue · 2026-09-30"
    assert her_item["detail"] == "1.2345 (group G1)"
    assert her_item["preparer"] == "alice"
    assert her_item["edited_by"] == []


def test_pending_items_op_with_end_date_includes_to_suffix():
    ops = [_op("OP-2", "G1", "ZZENT", "2026-01-01", "2026-06-30", 60, "Equity", "alice",
               "2026-09-01T09:00:00")]
    preparers_by_name = {"OP-2": frozenset({"alice"})}
    items = M.pending_items([], ops, preparers_by_name, "lead", ("EPM Admin",),
                             "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    assert items[0]["detail"] == "60% · Equity to 2026-06-30"


def test_pending_items_edited_by_lists_the_other_preparers_sorted():
    her = [_her("HER-2", "G1", "ZZENT", "4000 - Revenue", "2026-09-30", 1.1,
                "alice", "2026-09-02T10:00:00")]
    preparers_by_name = {"HER-2": frozenset({"alice", "zed", "lead"})}
    items = M.pending_items(her, [], preparers_by_name, "lead", ("EPM Admin",),
                             "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    assert items[0]["preparer"] == "alice"
    assert items[0]["edited_by"] == ["lead", "zed"]


def test_pending_items_missing_name_raises_key_error():
    """Failure path: a name missing from preparers_by_name is never treated
    as owner-only."""
    her = [_her("HER-3", "G1", "ZZENT", "4000 - Revenue", "2026-09-30", 1.1,
                "alice", "2026-09-02T10:00:00")]
    try:
        M.pending_items(her, [], {}, "lead", ("EPM Admin",), "Blocked",
                         APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_pending_items_empty_inputs_give_empty_list():
    assert M.pending_items([], [], {}, "lead", ("EPM Admin",), "Blocked",
                            APPROVER_ROLES, SELF_APPROVAL_PROBLEM) == []


def test_module_imports_no_frappe():
    """Same contract as fiscal_status_model, extended to refuse a ``konsol``
    import too (rates_model.py is loaded by path, like the rate rule it
    takes injected)."""
    with open(M.__file__) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("frappe") or a.name.startswith("konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not module.startswith("frappe")
            assert not module.startswith("konsol")


# --- O57: an OP item carries the draft's structural effect (story 4.2, C-O4) -----
# ``_op_item`` is shared by get_pending (Rates) and approvals_model (Approvals),
# so the effect is passed through here, once. The effect is the REAL
# ownership_change_model.effect's output, never a hand-built dict.

_OCM_SPEC = importlib.util.spec_from_file_location(
    "rates_model_test_ownership_change_model",
    os.path.join(APP_DIR, "close", "ownership_change_model.py"))
OCM = importlib.util.module_from_spec(_OCM_SPEC)
_OCM_SPEC.loader.exec_module(OCM)


def _o57_calendar():
    rows = []
    for m in range(1, 13):
        start = "2025-%02d-01" % m
        nxt = "2026-01-01" if m == 12 else "2025-%02d-01" % (m + 1)
        end = (datetime.date.fromisoformat(nxt) - datetime.timedelta(days=1)).isoformat()
        rows.append({"fiscal_year": 2025, "fiscal_period": m, "period_code": "P%02d" % m,
                     "period_label": "P%02d" % m, "period_type": "Regular",
                     "start_date": start, "end_date": end, "status": "Open"})
    return rows


def _o57_real_effect():
    change = {"entity": "ZZENT", "effective_date": "2025-10-01", "ownership_pct": 80,
              "consolidation_method": "full"}
    current = {"name": "OP-0", "effective_date": "2025-01-01", "end_date": None,
               "ownership_pct": 100.0, "consolidation_method": "full"}
    # O64: the signed runs carry their date and signer, as
    # ownership_change.context hands them over.
    signed = {(2025, 9): {"run": "AR-9", "signed_on": "2025-10-04", "signed_by_name": "Jane Doe"},
              (2025, 11): {"run": "AR-11", "signed_on": "2025-12-04",
                           "signed_by_name": "Jane Doe"}}
    return OCM.effect(change, current, _o57_calendar(), signed)


def _o57_items(op):
    return M.pending_items([], [op], {op["name"]: frozenset({"alice"})}, "lead",
                           ("EPM Admin",), "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)


def test_o57_op_item_carries_the_real_models_effect():
    effect = _o57_real_effect()
    op = dict(_op("OP-1", "G1", "ZZENT", "2025-10-01", None, 80, "full", "alice",
                  "2026-09-01T09:00:00"), effect=effect)
    [item] = _o57_items(op)
    assert item["effect"] == effect
    assert item["effect"]["after"]["pct"] == 80.0
    assert item["effect"]["resign"] == ["FY2025 P11"]
    # The detail text is unchanged by the effect.
    assert item["detail"] == "80% · full"


def test_o57_failure_path_a_desk_draft_effect_none_stays_none():
    """A Desk "Record ownership" draft has no ``supersedes``: its effect is
    None, never a guessed before/after."""
    op = dict(_op("OP-2", "G1", "ZZENT", "2025-10-01", None, 80, "full", "alice",
                  "2026-09-01T09:00:00"), effect=None)
    [item] = _o57_items(op)
    assert "effect" in item and item["effect"] is None


def test_o57_failure_path_a_doc_without_the_effect_key_does_not_raise_or_invent_one():
    """approvals_model builds OP items through this function before O58 gives
    its docs an ``effect``: a missing key never raises, and never becomes a
    None that would read as "Drafted in Desk"."""
    op = _op("OP-3", "G1", "ZZENT", "2025-10-01", None, 80, "full", "alice",
             "2026-09-01T09:00:00")
    [item] = _o57_items(op)
    assert "effect" not in item
    assert item["detail"] == "80% · full"


def test_o57_her_items_never_carry_an_effect():
    her = [dict(_her("HER-1", "G1", "ZZENT", "4000", "2026-09-30", 1.1, "alice",
                     "2026-09-02T10:00:00"), effect=_o57_real_effect())]
    [item] = M.pending_items(her, [], {"HER-1": frozenset({"alice"})}, "lead", ("EPM Admin",),
                             "Blocked", APPROVER_ROLES, SELF_APPROVAL_PROBLEM)
    assert "effect" not in item


# --- O69: the pending Ownership Period item says how to edit it -----------------
# Story 4.2; wireframe-4.2.md §1 ("The Analyst can edit it until it is
# approved"). The server, never the client, decides which draft offers Edit
# and with which node, period, pct and method.

def _o69_calendar():
    """FY2025 as live declares it: P00 Opening and P01 both start 1 Jan,
    P13 Closing starts on P12's last day. Every period is Open (R52h: the
    rows carry the effective status, as fiscal_period_rows returns it)."""
    rows = [{"fiscal_year": 2025, "fiscal_period": 0, "period_type": "Opening",
             "start_date": datetime.date(2025, 1, 1), "status": "Open"}]
    for m in range(1, 13):
        rows.append({"fiscal_year": 2025, "fiscal_period": m, "period_type": "Regular",
                     "start_date": datetime.date(2025, m, 1), "status": "Open"})
    rows.append({"fiscal_year": 2025, "fiscal_period": 13, "period_type": "Closing",
                 "start_date": datetime.date(2025, 12, 31), "status": "Open"})
    return rows


def _o69_draft(**k):
    op = dict(_op("OP-ZZ-2025-10-01", "G1", "ZZENT", "2025-10-01", None, 80.0, "full",
                  "alice", "2026-09-01T09:00:00"), supersedes="OP-ZZ-1")
    op.update(k)
    return op


def test_o69_regular_starts_maps_each_regular_first_day_to_its_period():
    starts = M.regular_period_by_start(_o69_calendar())
    assert starts["2025-10-01"] == (2025, 10, "Open")
    # P00 Opening shares 1 Jan with P01: only the Regular one counts.
    assert starts["2025-01-01"] == (2025, 1, "Open")
    # P13 Closing's first day is no Regular period's.
    assert "2025-12-31" not in starts
    assert len(starts) == 12


def test_o69_failure_path_two_regular_periods_on_one_day_give_neither():
    rows = _o69_calendar() + [{"fiscal_year": 2026, "fiscal_period": 1,
                               "period_type": "Regular", "start_date": "2025-10-01"}]
    starts = M.regular_period_by_start(rows)
    assert "2025-10-01" not in starts, starts


def test_o69_op_edit_is_the_drafts_node_period_pct_and_method():
    edit = M.op_edit(_o69_draft(), M.regular_period_by_start(_o69_calendar()))
    assert edit == {"consolidation_group": "G1", "entity": "ZZENT", "fiscal_year": 2025,
                    "fiscal_period": 10, "ownership_pct": 80.0,
                    "consolidation_method": "full"}


def test_o69_failure_path_a_desk_draft_without_supersedes_has_no_edit():
    starts = M.regular_period_by_start(_o69_calendar())
    assert M.op_edit(_o69_draft(supersedes=None), starts) is None
    assert M.op_edit(_o69_draft(supersedes=""), starts) is None


def test_o69_failure_path_a_caller_who_may_not_save_has_no_edit():
    # ``starts`` None: the caller is not admitted by save_ownership_change.
    assert M.op_edit(_o69_draft(), None) is None


def test_o69_failure_path_a_date_no_regular_period_starts_on_gives_no_guessed_period():
    starts = M.regular_period_by_start(_o69_calendar())
    for day in ("2025-10-15", "2025-12-31", "2027-01-01"):
        assert M.op_edit(_o69_draft(effective_date=day), starts) is None, day


def test_o69_pending_items_pass_edit_through_and_never_invent_it():
    starts = M.regular_period_by_start(_o69_calendar())
    with_edit = dict(_o69_draft(), edit=M.op_edit(_o69_draft(), starts))
    desk = dict(_o69_draft(name="OP-DESK", supersedes=None), edit=None)
    bare = _o69_draft(name="OP-BARE")
    items = M.pending_items([], [with_edit, desk, bare],
                            {n: frozenset({"alice"}) for n in ("OP-ZZ-2025-10-01", "OP-DESK",
                                                               "OP-BARE")},
                            "lead", ("EPM Admin",), "Blocked", APPROVER_ROLES,
                            SELF_APPROVAL_PROBLEM)
    by = {i["name"]: i for i in items}
    assert by["OP-ZZ-2025-10-01"]["edit"]["fiscal_period"] == 10
    assert "edit" in by["OP-DESK"] and by["OP-DESK"]["edit"] is None
    # approvals_model builds OP items through the same function without an
    # ``edit``: the key stays absent, never a None that reads as "no Edit".
    assert "edit" not in by["OP-BARE"]
    # The title and detail are unchanged.
    assert by["OP-ZZ-2025-10-01"]["title"] == "ZZENT in G1 from 2025-10-01"
    assert by["OP-ZZ-2025-10-01"]["detail"] == "80% · full"


# --- R52h (review S7/U5): ``edit`` only where the form can load the draft --------
# Coordinator ruling S7/U5: the server's ``op_edit`` is None wherever the
# ownership change form cannot load the draft. The form's choices are
# rates_api._change_choices (rates_api.py:508-548): nodes that name an entity,
# and Regular periods whose effective status is Open.

def _r52h_calendar(closed=(), statuses=None):
    """``_o69_calendar`` (every period Open) with the periods in ``closed``
    Closed and those in ``statuses`` set to the given status."""
    rows = []
    for r in _o69_calendar():
        r = dict(r)
        if r["fiscal_period"] in closed:
            r["status"] = "Closed"
        if statuses and r["fiscal_period"] in statuses:
            r["status"] = statuses[r["fiscal_period"]]
        rows.append(r)
    return rows


def _r52h_choices(calendar, nodes):
    """The form's choices in _change_choices' shape, built from the same rows
    by its rule (rates_api.py:508-548): ``nodes`` are the submitted
    ``(consolidation_group, data_area_id)`` pairs; only those naming an
    entity are offered; only Regular periods whose status is Open are."""
    entities = sorted({(e, g) for g, e in nodes if e})
    periods = [{"fiscal_year": int(r["fiscal_year"]), "fiscal_period": int(r["fiscal_period"]),
                "start_date": M._iso_day(r["start_date"])}
               for r in calendar
               if r.get("period_type") == "Regular" and r.get("status") == "Open"]
    return {"entities": [{"entity": e, "consolidation_group": g} for e, g in entities],
            "periods": periods}


def test_r52h_failure_path_a_group_node_draft_has_no_edit():
    # Red at 843cdf7: {'consolidation_group': 'G1', 'entity': None, ...}.
    starts = M.regular_period_by_start(_r52h_calendar())
    assert M.op_edit(_o69_draft(data_area_id=None), starts) is None
    assert M.op_edit(_o69_draft(data_area_id=""), starts) is None


def test_r52h_a_draft_starting_a_closed_regular_period_has_no_edit():
    starts = M.regular_period_by_start(_r52h_calendar(closed=(10,)))
    assert M.op_edit(_o69_draft(), starts) is None


def test_r52h_any_status_but_open_gives_no_edit_and_a_missing_one_is_never_open():
    for status in ("Closed", "Locked", "Soft Closed", "", None):
        starts = M.regular_period_by_start(_r52h_calendar(statuses={10: status}))
        assert M.op_edit(_o69_draft(), starts) is None, status
    rows = [dict(r) for r in _r52h_calendar()]
    for r in rows:
        if r["fiscal_period"] == 10:
            del r["status"]
    assert M.op_edit(_o69_draft(), M.regular_period_by_start(rows)) is None


def test_r52h_an_open_entity_draft_is_unchanged():
    edit = M.op_edit(_o69_draft(), M.regular_period_by_start(_r52h_calendar(closed=(9, 11))))
    assert edit == {"consolidation_group": "G1", "entity": "ZZENT", "fiscal_year": 2025,
                    "fiscal_period": 10, "ownership_pct": 80.0,
                    "consolidation_method": "full"}


def test_r52h_the_map_carries_each_regular_periods_status_from_one_read():
    starts = M.regular_period_by_start(_r52h_calendar(closed=(3,)))
    assert starts["2025-10-01"] == (2025, 10, "Open")
    assert starts["2025-03-01"] == (2025, 3, "Closed")
    assert starts["2025-01-01"] == (2025, 1, "Open")
    assert "2025-12-31" not in starts
    assert len(starts) == 12


def test_r52h_edit_is_offered_exactly_where_the_forms_choices_hold_the_draft():
    """Pins op_edit to _change_choices: for every node and every calendar
    day, ``edit`` is not None exactly when the draft's (entity, period) is
    one of the choices, and then names that choice."""
    calendar = _r52h_calendar(closed=(1, 2, 3, 4, 5, 6), statuses={7: "Locked"})
    nodes = [("G1", "ZZENT"), ("G1", None), ("G2", "ZZENT"), ("G2", "ZZTWO"), ("G3", "")]
    choices = _r52h_choices(calendar, nodes)
    starts = M.regular_period_by_start(calendar)
    offered = 0
    for group, entity in nodes:
        for row in calendar:
            day = M._iso_day(row["start_date"])
            draft = _o69_draft(consolidation_group=group, data_area_id=entity, effective_date=day)
            edit = M.op_edit(draft, starts)
            node_ok = {"entity": entity, "consolidation_group": group} in choices["entities"]
            period = [p for p in choices["periods"] if p["start_date"] == day]
            if node_ok and period:
                offered += 1
                assert edit is not None, (group, entity, day)
                assert (edit["entity"], edit["consolidation_group"]) == (entity, group)
                assert (edit["fiscal_year"], edit["fiscal_period"]) == \
                    (period[0]["fiscal_year"], period[0]["fiscal_period"])
            else:
                assert edit is None, (group, entity, day, edit)
    # 3 entity nodes x P08..P12 (P00's day is P01's, which is Closed).
    assert offered == 3 * 5, offered
