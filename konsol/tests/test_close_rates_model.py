"""Rates model, pure: konsol/close/rates_model.py (konsol#305 E401, E402).

Loaded by path; the module imports nothing from frappe or konsol.

E402 injects the real ``self_approval_problem`` and ``APPROVER_ROLES`` from
close_policy_model.py (loaded by path too), rather than copying the policy
rule (#305-W2-14).
"""
import ast
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
