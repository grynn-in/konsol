"""Which approvals change the numbers, and which periods, pure:
konsol/close/data_change_model.py (konsol#305 S41).

Loaded by path; the module imports nothing from frappe or konsol.
close_policy_model is loaded by path too (separately, not imported by the
module under test), only to pin NUMBER_DRIVING against its
APPROVAL_DOCTYPES.
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "data_change_model.py")
_spec = importlib.util.spec_from_file_location("close_data_change_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

_POLICY_PATH = os.path.join(APP_DIR, "close", "close_policy_model.py")
_policy_spec = importlib.util.spec_from_file_location(
    "close_policy_model_for_data_change_test", _POLICY_PATH)
POLICY = importlib.util.module_from_spec(_policy_spec)
_policy_spec.loader.exec_module(POLICY)


def _period_row(fiscal_year, fiscal_period, period_type="Regular", status="Open"):
    """A fiscal_calendar.fiscal_period_rows() row, minimal (mirrors
    test_close_journal_model.py's _period_row)."""
    return {
        "fiscal_year": fiscal_year,
        "fiscal_period": fiscal_period,
        "period_code": f"P{fiscal_period}",
        "period_label": f"P{fiscal_period}",
        "period_type": period_type,
        "start_date": None,
        "end_date": None,
        "quarter": "",
        "status": status,
    }


#: FY2025 P1..P12 declared, plus one earlier and one later-year period.
_ROWS = (
    [_period_row(2024, 12)]
    + [_period_row(2025, p) for p in range(1, 13)]
    + [_period_row(2026, 1)]
)


# --- NUMBER_DRIVING ----------------------------------------------------

def test_number_driving_is_every_approval_doctype_ic_balance_included():
    """AMENDED 4 Oct: all 7 close_policy_model.APPROVAL_DOCTYPES, not six of
    seven — IC Balance counts too."""
    assert set(M.NUMBER_DRIVING) == set(POLICY.APPROVAL_DOCTYPES)
    assert "IC Balance" in M.NUMBER_DRIVING


def test_number_driving_has_no_duplicates():
    assert len(M.NUMBER_DRIVING) == len(set(M.NUMBER_DRIVING))


# --- changed_periods: a doctype outside NUMBER_DRIVING -----------------

def test_a_trial_balance_submission_changes_no_period():
    assert M.changed_periods("Trial Balance Submission", (2025, 7), _ROWS) == []


def test_a_build_approval_changes_no_period():
    assert M.changed_periods("Build Approval", (2025, 7), _ROWS) == []


def test_a_tb_exception_changes_no_period():
    assert M.changed_periods("TB Exception", (2025, 7), _ROWS) == []


# --- changed_periods: a NUMBER_DRIVING doctype, no reversal -------------

def test_a_group_exchange_rate_changes_its_own_period_and_every_later_one():
    result = M.changed_periods("Group Exchange Rate", (2025, 7), _ROWS)
    expected = [(2025, p) for p in range(7, 13)] + [(2026, 1)]
    assert result == expected


def test_an_ic_balance_is_number_driving_now_amended_4_oct():
    """Before the amendment IC Balance returned []; now it is in
    NUMBER_DRIVING like any other approval doctype."""
    result = M.changed_periods("IC Balance", (2025, 11), _ROWS)
    assert result == [(2025, 11), (2025, 12), (2026, 1)]


def test_an_ownership_period_changes_its_own_period_and_later_ones():
    result = M.changed_periods("Ownership Period", (2025, 12), _ROWS)
    assert result == [(2025, 12), (2026, 1)]


def test_the_last_declared_period_has_no_later_period():
    result = M.changed_periods("Historical Equity Rate", (2026, 1), _ROWS)
    assert result == [(2026, 1)]


def test_reverse_both_zero_is_treated_as_no_reversal():
    result = M.changed_periods("Consolidation Journal", (2025, 7), _ROWS, reverse=(0, 0))
    expected = [(2025, p) for p in range(7, 13)] + [(2026, 1)]
    assert result == expected


# --- changed_periods: a Consolidation Journal with a reversal ----------

def test_a_journal_reversal_folds_in_the_reversal_period():
    """The reversal period is already "later" than the journal's own
    period here, so the result is unchanged by naming it explicitly."""
    result = M.changed_periods(
        "Consolidation Journal", (2025, 7), _ROWS, reverse=(2025, 8))
    expected = [(2025, p) for p in range(7, 13)] + [(2026, 1)]
    assert result == expected


def test_a_journal_reversal_outside_the_known_rows_is_still_included():
    """period_rows can be sparse (a caller that only loaded part of the
    calendar); the named reversal period is always in the result even when
    it is not one of period_rows's own rows."""
    sparse_rows = [_period_row(2025, 7), _period_row(2025, 9)]
    result = M.changed_periods(
        "Consolidation Journal", (2025, 7), sparse_rows, reverse=(2025, 8))
    assert result == [(2025, 7), (2025, 8), (2025, 9)]


def test_a_journal_with_no_rows_and_no_reversal_is_just_its_own_period():
    assert M.changed_periods("Consolidation Journal", (2025, 7), []) == [(2025, 7)]


# --- change_text ---------------------------------------------------------

def test_change_text_approved():
    assert M.change_text("Consolidation Journal", "CJ-00001", "approved") == \
        "Consolidation Journal CJ-00001 approved"


def test_change_text_cancelled():
    assert M.change_text("IC Balance", "ICB-0007", "cancelled") == \
        "IC Balance ICB-0007 cancelled"


def test_change_text_rejects_an_unknown_action():
    try:
        M.change_text("Consolidation Journal", "CJ-00001", "edited")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "edited" in str(exc)


# --- module contract -----------------------------------------------------

def test_module_imports_no_frappe_or_konsol():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("frappe", "konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol")
