"""Trial balance amount basis (konsol/tb_basis_model.py, konsolidat#199):
every submission says whether its rows are period movements, year-to-date
movements or period-end balances, and the file may not contradict the form."""
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("tb_basis_model", os.path.join(APP_DIR, "tb_basis_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

PERIOD, YTD, CLOSING = "Period movement", "Year-to-date movement", "Period-end balance"


def test_the_three_bases_in_order_and_the_column_aliases():
    assert M.AMOUNT_BASES == (PERIOD, YTD, CLOSING)
    assert M.COLUMN == "amount_basis"
    assert "amount_basis" in M.ALIASES and "basis" in M.ALIASES and "amount basis" in M.ALIASES
    assert "konsolidat#199" in M.__doc__


def test_canonical_ignores_case_and_surrounding_space():
    assert M.canonical("period-end balance") == CLOSING
    assert M.canonical(" Period Movement ") == PERIOD
    assert M.canonical("YEAR-TO-DATE MOVEMENT") == YTD
    for exact in M.AMOUNT_BASES:
        assert M.canonical(exact) == exact


def test_canonical_rejects_anything_else():
    assert M.canonical("balances") is None
    assert M.canonical("") is None
    assert M.canonical(None) is None
    assert M.canonical("   ") is None


def test_blank_form_basis_is_required():
    problems = M.basis_problems("", [])
    assert len(problems) == 1
    assert "Amount Basis is required" in problems[0]
    assert M.basis_problems(None, []) == problems


def test_unknown_form_basis_names_the_value_and_the_allowed_three():
    problems = M.basis_problems("Closing balance", [])
    assert len(problems) == 1
    assert "Closing balance" in problems[0]
    for basis in M.AMOUNT_BASES:
        assert basis in problems[0]


def test_matching_column_values_are_fine():
    rows = [(2, PERIOD), (3, "period movement"), (4, " PERIOD MOVEMENT ")]
    assert M.basis_problems(PERIOD, rows) == []
    assert M.basis_problems(PERIOD, []) == []


def test_a_line_that_differs_from_the_form_is_named():
    problems = M.basis_problems(PERIOD, [(2, PERIOD), (7, CLOSING), (8, PERIOD)])
    assert len(problems) == 1
    assert "7" in problems[0]
    assert CLOSING in problems[0]
    assert PERIOD in problems[0]


def test_unknown_or_blank_column_value_is_one_sentence_per_first_offence():
    problems = M.basis_problems(PERIOD, [(3, "balances")])
    assert len(problems) == 1
    assert "3" in problems[0] and "balances" in problems[0]

    problems = M.basis_problems(PERIOD, [(4, ""), (5, "  ")])
    assert len(problems) == 1
    assert "4" in problems[0]

    # the same unknown value on many lines is reported once, at its first line
    problems = M.basis_problems(PERIOD, [(2, "balances"), (6, "balances"), (9, "movements")])
    assert len(problems) == 2
    assert "2" in problems[0] and "9" in problems[1]
