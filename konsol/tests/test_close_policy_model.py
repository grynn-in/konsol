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
    assert M.self_approval_problem("Blocked", "alice", "bob", "IC Balance", "ICB-1", None, None) is None


def test_an_exempt_self_approval_passes_even_under_blocked():
    assert M.self_approval_problem("Blocked", "alice", "alice", "Ownership Period", "OP-1", None, "derived") is None
    assert M.self_approval_problem("Blocked", "alice", "alice", "IC Balance", "ICB-1", None, "system") is None


def test_undeclared_policy_names_the_close_settings_gap_and_the_document():
    msg = M.self_approval_problem("", "alice", "alice", "IC Balance", "ICB-1", None, None)
    assert msg.startswith("alice prepared IC Balance ICB-1; ")
    assert "Close Settings" in msg


def test_none_policy_is_also_undeclared():
    msg = M.self_approval_problem(None, "alice", "alice", "IC Balance", "ICB-1", None, None)
    assert msg.startswith("alice prepared IC Balance ICB-1; ")


def test_blocked_refuses_naming_the_preparer_and_document():
    msg = M.self_approval_problem("Blocked", "alice", "alice", "IC Balance", "ICB-1", None, None)
    assert msg == (
        "Close Settings blocks self-approval: alice prepared IC Balance ICB-1, "
        "so another Close Lead must approve it."
    )


def test_blocked_refuses_even_when_a_reason_is_supplied():
    """This is the failure path: a reason never overrides Blocked."""
    msg = M.self_approval_problem("Blocked", "alice", "alice", "IC Balance", "ICB-1", "a good reason", None)
    assert msg == (
        "Close Settings blocks self-approval: alice prepared IC Balance ICB-1, "
        "so another Close Lead must approve it."
    )


def test_allowed_with_reason_and_a_blank_reason_refuses():
    msg = M.self_approval_problem("Allowed with reason", "alice", "alice", "IC Balance", "ICB-1", "", None)
    assert msg == (
        "Close Settings allows self-approval only with a reason: approve IC Balance ICB-1 "
        "through konsol.close.approval_api.approve with a reason, or ask another Close Lead to approve it."
    )


def test_allowed_with_reason_and_a_whitespace_only_reason_refuses():
    """A whitespace-only reason counts as blank."""
    msg = M.self_approval_problem("Allowed with reason", "alice", "alice", "IC Balance", "ICB-1", "   ", None)
    assert msg is not None
    assert "only with a reason" in msg


def test_allowed_with_reason_and_a_reason_passes():
    assert M.self_approval_problem(
        "Allowed with reason", "alice", "alice", "IC Balance", "ICB-1", "moved because of X", None
    ) is None


# --- self_approval_note ------------------------------------------------------

def test_self_approval_note_names_the_user_policy_and_reason():
    assert M.self_approval_note("Allowed with reason", "alice", "moved because of X") == (
        "Self-approved by alice under Close Settings (Allowed with reason): moved because of X"
    )


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
    msg = M.self_approval_problem("Allowed", "alice", "alice", "IC Balance", "ICB-1", "a reason", None)
    assert msg is not None
    assert "'Allowed'" in msg and "Close Settings" in msg
