"""Close policy model, pure: konsol/close/close_policy_model.py (konsol#305-D2-3, D2-9).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "close_policy_model.py")
_spec = importlib.util.spec_from_file_location("close_policy_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _src(path):
    with open(path) as fh:
        return fh.read()


# --- policy_gaps -------------------------------------------------------

def test_both_undeclared_gives_both_codes_in_order():
    gaps = M.policy_gaps("", 0)
    assert [g["code"] for g in gaps] == [M.SELF_APPROVAL_UNDECLARED, M.RATE_MOVE_UNDECLARED]


def test_none_undeclared_gives_the_same_codes():
    gaps = M.policy_gaps(None, None)
    assert [g["code"] for g in gaps] == [M.SELF_APPROVAL_UNDECLARED, M.RATE_MOVE_UNDECLARED]


def test_both_declared_gives_no_gaps():
    assert M.policy_gaps("Blocked", 50) == []


def test_only_the_undeclared_one_is_reported():
    gaps = M.policy_gaps("Allowed with reason", 0)
    assert [g["code"] for g in gaps] == [M.RATE_MOVE_UNDECLARED]


def test_gap_messages_name_close_settings():
    gaps = M.policy_gaps("", 0)
    for gap in gaps:
        assert "Close Settings" in gap["message"]
        assert set(gap.keys()) == {"code", "message"}


# --- settings_problems ---------------------------------------------------

def test_unknown_policy_names_the_two_allowed_values():
    problems = M.settings_problems("Sometimes", 50)
    assert len(problems) == 1
    assert M.BLOCKED in problems[0]
    assert M.ALLOWED_WITH_REASON in problems[0]


def test_negative_threshold_refused():
    """This is the failure path: a negative threshold is refused even when
    self_approval is blank (undeclared, allowed on its own)."""
    problems = M.settings_problems("", -5)
    assert problems


def test_blank_and_zero_are_allowed():
    """Undeclared is allowed and reported as a gap elsewhere; it is never
    defaulted here."""
    assert M.settings_problems("", 0) == []


def test_declared_values_are_allowed():
    assert M.settings_problems("Blocked", 50) == []
    assert M.settings_problems("Allowed with reason", 0) == []


# --- move_fraction --------------------------------------------------------

def test_move_fraction_converts_percent_to_fraction():
    assert M.move_fraction(50) == 0.5


def test_move_fraction_zero_is_undeclared():
    assert M.move_fraction(0) is None


def test_move_fraction_none_is_undeclared():
    assert M.move_fraction(None) is None


# --- APPROVAL_DOCTYPES -----------------------------------------------------

def test_approval_doctypes_excludes_the_four():
    """This is the failure path: adding any of these back in fails."""
    excluded = ("Trial Balance Submission", "TB Exception", "Build Approval", "Budget Cycle")
    for doctype in excluded:
        assert doctype not in M.APPROVAL_DOCTYPES


def test_approval_doctypes_holds_the_seven():
    assert set(M.APPROVAL_DOCTYPES) == {
        "Consolidation Journal",
        "Business Combination",
        "Business Disposal",
        "Group Exchange Rate",
        "Ownership Period",
        "Historical Equity Rate",
        "IC Balance",
    }


# --- self_approval_problem -------------------------------------------------

def test_a_different_approver_passes():
    """Rule 1: user != owner. This never needs a policy at all."""
    assert M.self_approval_problem("Blocked", {"alice"}, "bob", "IC Balance", "ICB-1", None, None) is None


def test_an_exempt_self_approval_passes_even_under_blocked():
    assert M.self_approval_problem("Blocked", {"alice"}, "alice", "Ownership Period", "OP-1", None, "derived") is None
    assert M.self_approval_problem("Blocked", {"alice"}, "alice", "IC Balance", "ICB-1", None, "system") is None


def test_undeclared_policy_names_the_close_settings_gap_and_the_document():
    msg = M.self_approval_problem("", {"alice"}, "alice", "IC Balance", "ICB-1", None, None)
    assert msg.startswith("alice prepared IC Balance ICB-1; ")
    assert "Close Settings" in msg


def test_none_policy_is_also_undeclared():
    msg = M.self_approval_problem(None, {"alice"}, "alice", "IC Balance", "ICB-1", None, None)
    assert msg.startswith("alice prepared IC Balance ICB-1; ")


def test_blocked_refuses_naming_the_preparer_and_document():
    msg = M.self_approval_problem("Blocked", {"alice"}, "alice", "IC Balance", "ICB-1", None, None)
    assert msg == (
        "Close Settings blocks self-approval: alice prepared IC Balance ICB-1, "
        "so another Close Lead must approve it."
    )


def test_blocked_refuses_even_when_a_reason_is_supplied():
    """This is the failure path: a reason never overrides Blocked."""
    msg = M.self_approval_problem("Blocked", {"alice"}, "alice", "IC Balance", "ICB-1", "a good reason", None)
    assert msg == (
        "Close Settings blocks self-approval: alice prepared IC Balance ICB-1, "
        "so another Close Lead must approve it."
    )


def test_allowed_with_reason_and_a_blank_reason_refuses():
    msg = M.self_approval_problem("Allowed with reason", {"alice"}, "alice", "IC Balance", "ICB-1", "", None)
    assert msg == (
        "Close Settings allows self-approval only with a reason: approve IC Balance ICB-1 "
        "through konsol.close.approval_api.approve with a reason, or ask another Close Lead to approve it."
    )


def test_allowed_with_reason_and_a_whitespace_only_reason_refuses():
    """A whitespace-only reason counts as blank."""
    msg = M.self_approval_problem("Allowed with reason", {"alice"}, "alice", "IC Balance", "ICB-1", "   ", None)
    assert msg is not None
    assert "only with a reason" in msg


def test_allowed_with_reason_and_a_reason_passes():
    assert M.self_approval_problem(
        "Allowed with reason", {"alice"}, "alice", "IC Balance", "ICB-1", "moved because of X", None
    ) is None


# --- self_approval_note ------------------------------------------------------

def test_self_approval_note_names_the_user_policy_and_reason():
    assert M.self_approval_note("Allowed with reason", "alice", "moved because of X") == (
        "Self-approved by alice under Close Settings (Allowed with reason): moved because of X"
    )


# --- approval_build_reason ---------------------------------------------------

def test_on_submit_by_epm_admin_gives_a_reason():
    msg = M.approval_build_reason(
        "Historical Equity Rate", "HER-1", "on_submit", "alice", ["EPM Admin"]
    )
    assert msg == "Auto-approved: alice approved Historical Equity Rate HER-1 (konsol#305-D2-10)."


def test_on_submit_by_system_manager_gives_a_reason():
    msg = M.approval_build_reason(
        "Historical Equity Rate", "HER-1", "on_submit", "alice", ["System Manager"]
    )
    assert msg == "Auto-approved: alice approved Historical Equity Rate HER-1 (konsol#305-D2-10)."


def test_on_cancel_is_not_an_approval():
    """This is the failure path: a Reverse (cancel) never auto-approves (P22)."""
    assert M.approval_build_reason(
        "Historical Equity Rate", "HER-1", "on_cancel", "alice", ["EPM Admin"]
    ) is None


def test_on_update_is_not_an_approval():
    assert M.approval_build_reason(
        "Historical Equity Rate", "HER-1", "on_update", "alice", ["EPM Admin"]
    ) is None


def test_trial_balance_submission_is_never_an_approval():
    """An Entity Accountant's own submit, not an approval of someone else's
    work (excluded from APPROVAL_DOCTYPES above; P22)."""
    assert M.approval_build_reason(
        "Trial Balance Submission", "TBS-1", "on_submit", "alice", ["EPM Admin"]
    ) is None


def test_consolidation_group_save_is_not_an_approval():
    """Consolidation Group is a save, not a submit, and is not in
    APPROVAL_DOCTYPES (P22)."""
    assert M.approval_build_reason(
        "Consolidation Group", "ECL_GROUP", "on_submit", "alice", ["EPM Admin"]
    ) is None


def test_an_epm_analyst_alone_gives_no_reason():
    """This is the failure path: roles must meet APPROVER_ROLES."""
    assert M.approval_build_reason(
        "Historical Equity Rate", "HER-1", "on_submit", "alice", ["EPM Analyst"]
    ) is None


def test_administrator_holding_every_role_counts_as_an_approval():
    """Administrator holds every role via frappe.get_roles; there is no
    exemption (accepted 27 Sep)."""
    msg = M.approval_build_reason(
        "Historical Equity Rate", "HER-1", "on_submit", "Administrator",
        ["EPM Admin", "EPM Analyst", "System Manager", "Entity Accountant", "EPM User"],
    )
    assert msg == "Auto-approved: Administrator approved Historical Equity Rate HER-1 (konsol#305-D2-10)."


# --- module contract --------------------------------------------------------

def test_module_imports_no_frappe():
    """Same contract as konsol.build_command / konsol.assertion_status — keeps
    it host-testable (mirror test_assertion_warn_amber.py:269-277)."""
    tree = ast.parse(_src(M.__file__))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("frappe") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("frappe")


def test_an_unknown_policy_refuses_a_self_approval():
    """P03b: a value that is neither declared policy never approves (fail closed)."""
    msg = M.self_approval_problem("Allowed", {"alice"}, "alice", "IC Balance", "ICB-1", "a reason", None)
    assert msg is not None
    assert "'Allowed'" in msg and "Close Settings" in msg


# --- preparers (#305-W2-14: the owner, plus everyone who edited the draft) ---

def _v(owner, **data):
    return {"owner": owner, "data": data}


def test_preparers_with_no_versions_is_the_owner():
    assert M.preparers("a", []) == frozenset({"a"})


def test_preparers_returns_a_frozenset():
    assert isinstance(M.preparers("a", []), frozenset)


def test_preparers_blank_owner_raises():
    for owner in ("", None):
        try:
            M.preparers(owner, [])
        except ValueError:
            continue
        raise AssertionError("a blank owner must raise ValueError")


def test_a_field_edit_by_another_user_makes_them_a_preparer():
    versions = [_v("b", changed=[["quote", 1, 2]])]
    assert M.preparers("a", versions) == {"a", "b"}


def test_a_state_field_only_change_is_not_an_edit():
    """Failure path: a Reject (the workflow state only) would otherwise make
    the Close Lead a preparer."""
    versions = [_v("b", changed=[["status", "Draft", "Pending Approval"]])]
    assert M.preparers("a", versions, state_field="status") == {"a"}


def test_a_state_field_change_counts_when_there_is_no_workflow():
    versions = [_v("b", changed=[["status", "Draft", "Pending Approval"]])]
    assert M.preparers("a", versions, state_field=None) == {"a", "b"}


def test_a_state_field_change_with_another_field_is_an_edit():
    versions = [_v("b", changed=[["status", "Draft", "Pending Approval"], ["quote", 1, 2]])]
    assert M.preparers("a", versions, state_field="status") == {"a", "b"}


def test_a_submit_is_not_an_edit():
    versions = [_v("b", changed=[["docstatus", 0, 1]])]
    assert M.preparers("a", versions) == {"a"}


def test_a_submit_with_a_state_change_is_not_an_edit():
    versions = [_v("b", changed=[["docstatus", 0, 1], ["status", "Pending Approval", "Approved"]])]
    assert M.preparers("a", versions, state_field="status") == {"a"}


def test_a_cancel_and_an_amendment_first_save_are_not_edits():
    versions = [
        _v("b", changed=[["docstatus", 1, 2]]),
        _v("c", changed=[["docstatus", 2, 0]]),
    ]
    assert M.preparers("a", versions) == {"a"}


def test_a_child_row_change_is_an_edit():
    """A journal line edit: only row_changed is non-empty."""
    versions = [_v("b", changed=[], added=[], removed=[],
                   row_changed=[["lines", 0, "CJL-1", [["debit", 1, 2]]]])]
    assert M.preparers("a", versions, state_field="status") == {"a", "b"}


def test_an_added_or_removed_child_row_is_an_edit():
    added = [_v("b", added=[["lines", {"name": "CJL-2"}]])]
    removed = [_v("c", removed=[["lines", {"name": "CJL-1"}]])]
    assert M.preparers("a", added, state_field="status") == {"a", "b"}
    assert M.preparers("a", removed, state_field="status") == {"a", "c"}


def test_an_empty_diff_is_not_an_edit():
    versions = [_v("b", changed=[], added=[], removed=[], row_changed=[])]
    assert M.preparers("a", versions) == {"a"}


def test_the_owners_own_edits_add_nobody():
    versions = [_v("a", changed=[["quote", 1, 2]])]
    assert M.preparers("a", versions) == {"a"}


def test_json_text_gives_the_same_answers_as_a_dict():
    import json
    cases = [
        ([_v("b", changed=[["quote", 1, 2]])], "status"),
        ([_v("b", changed=[["status", "Draft", "Pending Approval"]])], "status"),
        ([_v("b", changed=[["status", "Draft", "Pending Approval"]])], None),
        ([_v("b", changed=[["docstatus", 0, 1]])], None),
        ([_v("b", row_changed=[["lines", 0, "CJL-1", [["debit", 1, 2]]]])], "status"),
    ]
    for versions, state_field in cases:
        as_text = [{"owner": v["owner"], "data": json.dumps(v["data"])} for v in versions]
        assert M.preparers("a", as_text, state_field) == M.preparers("a", versions, state_field)


def test_bad_json_text_raises_naming_the_version_owner():
    try:
        M.preparers("a", [{"owner": "zz-b@example.com", "data": "{bad"}])
    except ValueError as exc:
        assert "zz-b@example.com" in str(exc)
    else:
        raise AssertionError("JSON text that does not parse must raise ValueError")


def test_an_editor_approver_is_a_self_approver():
    """Failure path, #305-W2-14: the Close Lead edited the Analyst's draft."""
    msg = M.self_approval_problem("Blocked", {"a", "b"}, "b", "IC Balance", "ICB-1", None, None)
    assert msg is not None and "blocks self-approval" in msg


def test_an_approver_who_is_not_a_preparer_passes():
    assert M.self_approval_problem("Blocked", {"a"}, "b", "IC Balance", "ICB-1", None, None) is None


def test_a_string_of_preparers_raises_type_error():
    """Failure path: "b" in "ab" would match a substring."""
    for preparers in ("ab", "b"):
        try:
            M.self_approval_problem("Blocked", preparers, "b", "IC Balance", "ICB-1", None, None)
        except TypeError as exc:
            assert "preparers" in str(exc)
            continue
        raise AssertionError("a str of preparers must raise TypeError")


# --- R01a (#305-W2-14, review M1): edits carried in the submitting request ---
# ``submit_carries_edit(diff, state_field)`` reads the diff Frappe computes for
# the submitting save (frappe.core.doctype.version.version.get_diff, the same
# dict its Version will hold) and says whether the submitter edited the draft
# in that same request.

def test_no_diff_is_not_an_edit():
    assert M.submit_carries_edit(None) is False
    assert M.submit_carries_edit({}) is False


def test_a_submit_that_changes_only_docstatus_is_not_an_edit():
    assert M.submit_carries_edit({"changed": [["docstatus", 0, 1]]}) is False


def test_a_submit_that_also_changes_a_field_is_an_edit():
    """Failure path, review M1: the quote changed in the submitting request."""
    diff = {"changed": [("quote", 1.1, 1.5), ("docstatus", 0, 1)],
            "added": [], "removed": [], "row_changed": []}
    assert M.submit_carries_edit(diff) is True
    assert M.submit_carries_edit(diff, state_field="status") is True


def test_a_workflow_approve_changes_only_the_state_field_and_docstatus():
    """A workflow Approve sets the state field and submits: not an edit."""
    diff = {"changed": [["status", "Pending Approval", "Approved"], ["docstatus", 0, 1]]}
    assert M.submit_carries_edit(diff, state_field="status") is False


def test_the_state_field_counts_when_the_doctype_has_no_workflow():
    diff = {"changed": [["status", "Pending Approval", "Approved"], ["docstatus", 0, 1]]}
    assert M.submit_carries_edit(diff, state_field=None) is True


def test_a_child_row_change_in_the_submit_is_an_edit():
    for key, rows in (("row_changed", [["lines", 0, "CJL-1", [["debit", 1, 2]]]]),
                      ("added", [["lines", {"name": "CJL-2"}]]),
                      ("removed", [["lines", {"name": "CJL-1"}]])):
        diff = {"changed": [["docstatus", 0, 1]], key: rows}
        assert M.submit_carries_edit(diff, state_field="status") is True, key


# --- intercompany_declaration_problems (konsol#305-W3-7, C16) ------------

def test_intercompany_declarations_hold_only_none_in_this_group():
    assert M.INTERCOMPANY_NONE == "None in this group"
    assert M.INTERCOMPANY_DECLARATIONS == (M.INTERCOMPANY_NONE,)


def test_blank_intercompany_declaration_has_no_problem():
    assert M.intercompany_declaration_problems("", 0) == []
    assert M.intercompany_declaration_problems(None, 0) == []
    # Blank is undeclared: nothing to refuse, whatever is Published.
    assert M.intercompany_declaration_problems("", 3) == []


def test_none_in_this_group_with_nothing_published_has_no_problem():
    assert M.intercompany_declaration_problems("None in this group", 0) == []


def test_none_in_this_group_with_published_accounts_is_refused():
    """Failure path: 3 Published Intercompany Accounts contradict 'none'."""
    problems = M.intercompany_declaration_problems("None in this group", 3)
    assert len(problems) == 1, problems
    assert "3" in problems[0]
    assert "Inactive" in problems[0]


def test_unknown_intercompany_declaration_is_refused():
    """Failure path: a value that is not declared is refused, naming it."""
    problems = M.intercompany_declaration_problems("Some", 0)
    assert problems == [
        "Unknown intercompany declaration 'Some'; expected blank or None in this group."]


def test_published_must_be_a_non_negative_int():
    for declaration in ("None in this group", ""):
        for bad in (-1, "2", None, 1.5, True):
            try:
                M.intercompany_declaration_problems(declaration, bad)
            except ValueError:
                continue
            raise AssertionError("published=%r must raise ValueError" % (bad,))


# --- statement_accounts / statement_account_problems ------------------------
# (#305-W4-1 option 1c, N41): the declared CTA account and current-year result
# account, and the one setup gap when either is missing or unusable.

def _account(is_group=0, status="Published", statement_section="Balance Sheet",
             account_name="Acct", parent_account="3"):
    return {
        "is_group": is_group,
        "status": status,
        "statement_section": statement_section,
        "account_name": account_name,
        "parent_account": parent_account,
    }


def test_statement_accounts_both_blank_gives_both_none_and_one_gap():
    chart = {
        "3100": _account(account_name="Retained earnings"),
        "3300": _account(account_name="AOCI — CTA"),
    }
    result = M.statement_accounts("", "", chart)
    assert result["cta_account"] is None
    assert result["result_account"] is None
    assert result["gap"]["code"] == M.STATEMENT_ACCOUNTS_UNDECLARED
    assert "unmatched residual" in result["gap"]["message"]


def test_statement_accounts_blank_never_defaults_to_a_chart_account():
    """Failure path: even though the fixture chart has 3100 and 3300, a blank
    declaration is never guessed from the chart (no policy defaults)."""
    import json
    chart = {
        "3100": _account(account_name="Retained earnings"),
        "3300": _account(account_name="AOCI — CTA"),
    }
    result = M.statement_accounts("", "", chart)
    assert result["cta_account"] is None and result["result_account"] is None
    dumped = json.dumps(result)
    assert "3100" not in dumped and "3300" not in dumped


def test_statement_accounts_both_declared_and_valid_has_no_gap():
    chart = {"3100": _account(), "3300": _account()}
    result = M.statement_accounts("3300", "3100", chart)
    assert result == {"cta_account": "3300", "result_account": "3100", "gap": None}


def test_statement_accounts_a_heading_cta_is_refused_but_a_valid_result_still_counts():
    """Failure path: partial is honoured — the valid result account still
    resolves even though the CTA is invalid."""
    chart = {"3": _account(is_group=1), "3100": _account()}
    result = M.statement_accounts("3", "3100", chart)
    assert result["cta_account"] is None
    assert result["result_account"] == "3100"
    assert result["gap"] is not None
    assert "3" in result["gap"]["message"] and "heading" in result["gap"]["message"]


def test_statement_accounts_a_draft_cta_is_refused():
    """Failure path: a Draft (not Published) account is unusable."""
    chart = {"3300": _account(status="Draft")}
    result = M.statement_accounts("3300", "", chart)
    assert result["cta_account"] is None
    assert "not Published" in result["gap"]["message"]


def test_statement_accounts_a_profit_and_loss_cta_is_refused():
    """Failure path: a Profit and Loss account cannot hold a balance-sheet
    plug."""
    chart = {"4100": _account(statement_section="Profit and Loss")}
    result = M.statement_accounts("4100", "", chart)
    assert result["cta_account"] is None
    assert "Profit and Loss" in result["gap"]["message"]


def test_statement_accounts_the_same_code_twice_gives_both_none_and_says_they_must_differ():
    """Failure path: declaring one account for both roles is refused."""
    chart = {"3300": _account()}
    result = M.statement_accounts("3300", "3300", chart)
    assert result["cta_account"] is None
    assert result["result_account"] is None
    assert "differ" in result["gap"]["message"]


def test_statement_accounts_a_leaf_with_no_heading_is_refused():
    """S3 (#305-W4-4): a Published BS leaf with a blank parent_account (live:
    account 99) resolves to no heading in the statement, so it must be
    refused rather than silently balancing the residual."""
    chart = {"99": _account(parent_account=""), "3100": _account()}
    result = M.statement_accounts("99", "3100", chart)
    assert result["cta_account"] is None
    assert result["result_account"] == "3100"
    assert result["gap"] is not None
    assert "99" in result["gap"]["message"]
    assert "heading" in result["gap"]["message"]


def test_statement_accounts_a_missing_parent_account_key_is_also_refused():
    """A caller's Main Account read that omits the ``parent_account`` field
    entirely must read the same as a blank one (fail closed, not open)."""
    row = _account()
    del row["parent_account"]
    chart = {"99": row}
    result = M.statement_accounts("99", "", chart)
    assert result["cta_account"] is None
    assert "heading" in result["gap"]["message"]


def test_statement_account_problems_a_leaf_with_no_heading_is_one_problem():
    chart = {"99": _account(parent_account="")}
    problems = M.statement_account_problems("99", "", chart)
    assert len(problems) == 1
    assert "heading" in problems[0]


def test_statement_account_problems_blank_is_allowed():
    """Blank is undeclared, reported elsewhere as a gap, never a SAVE
    problem."""
    assert M.statement_account_problems("", "", {}) == []


def test_statement_account_problems_a_heading_is_one_problem():
    chart = {"3": _account(is_group=1)}
    problems = M.statement_account_problems("3", "", chart)
    assert len(problems) == 1


def test_statement_account_problems_the_same_code_twice_is_one_problem():
    chart = {"3300": _account()}
    problems = M.statement_account_problems("3300", "3300", chart)
    assert len(problems) == 1
