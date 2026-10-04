"""Statement model part 1, pure: konsol/close/statement_model.py
(konsol#305 Wave 4, N48).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "statement_model.py")
_spec = importlib.util.spec_from_file_location("statement_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _calendar(years):
    """A declared calendar: Opening P0, Regular P1-P12, Closing P13 for
    each fiscal year in ``years`` (fiscal_calendar.fiscal_period_rows()
    shape, keys only the ones this module reads)."""
    rows = []
    for fiscal_year in years:
        rows.append({"fiscal_year": fiscal_year, "fiscal_period": 0, "period_type": "Opening"})
        for fiscal_period in range(1, 13):
            rows.append({"fiscal_year": fiscal_year, "fiscal_period": fiscal_period, "period_type": "Regular"})
        rows.append({"fiscal_year": fiscal_year, "fiscal_period": 13, "period_type": "Closing"})
    return rows


_CALENDAR = _calendar((2024, 2025))


# --- period_keys -------------------------------------------------------

def test_regular_p1_compares_to_previous_fys_last_regular_period():
    """#305-W4-7 7a."""
    assert M.period_keys(_CALENDAR, (2025, 1))["comparison"] == (2024, 12)


def test_regular_period_compares_to_the_previous_regular_period():
    assert M.period_keys(_CALENDAR, (2025, 7))["comparison"] == (2025, 6)


def test_closing_period_compares_to_the_last_regular_period():
    assert M.period_keys(_CALENDAR, (2025, 13))["comparison"] == (2025, 12)


def test_ytd_for_p3_is_opening_through_p3():
    assert M.period_keys(_CALENDAR, (2025, 3))["ytd"] == [
        (2025, 0), (2025, 1), (2025, 2), (2025, 3),
    ]


def test_ytd_for_p12_excludes_closing():
    ytd = M.period_keys(_CALENDAR, (2025, 12))["ytd"]
    assert (2025, 13) not in ytd
    assert ytd[-1] == (2025, 12)


def test_ytd_for_p13_includes_closing():
    ytd = M.period_keys(_CALENDAR, (2025, 13))["ytd"]
    assert ytd[-1] == (2025, 13)


def test_cumulative_to_is_the_key_itself():
    assert M.period_keys(_CALENDAR, (2025, 7))["cumulative_to"] == (2025, 7)


def test_no_earlier_regular_period_gives_no_comparison():
    """Failure path: FY2010 P1 with nothing declared before it."""
    calendar = _calendar((2010,))
    assert M.period_keys(calendar, (2010, 1))["comparison"] is None


def test_an_undeclared_key_raises_naming_it():
    """Failure path."""
    with pytest.raises(ValueError, match=r"\(2099, 1\)"):
        M.period_keys(_CALENDAR, (2099, 1))


# --- heading_amounts -----------------------------------------------------

_ACCOUNTS = {
    "1": {"account_name": "ASSETS", "parent_account": None, "is_group": True,
          "statement_section": M.BS, "lft": 1},
    "3": {"account_name": "EQUITY", "parent_account": None, "is_group": True,
          "statement_section": M.BS, "lft": 9},
    "4": {"account_name": "COST OF SALES", "parent_account": None, "is_group": True,
          "statement_section": M.PL, "lft": 13},
    "1110": {"account_name": "Cash", "parent_account": "1", "is_group": False,
              "statement_section": M.BS, "lft": 2},
    "3300": {"account_name": "AOCI - CTA", "parent_account": "3", "is_group": False,
              "statement_section": M.BS, "lft": 10},
    "4100": {"account_name": "COGS", "parent_account": "4", "is_group": False,
              "statement_section": M.PL, "lft": 14},
    "99": {"account_name": "NET INCOME", "parent_account": "", "is_group": False,
            "statement_section": M.PL, "lft": 21},
}

_KEY = (2025, 7)


def _row(main_account, amount, adjustment_type=None, null_rows=0):
    return {
        "fiscal_year": _KEY[0],
        "fiscal_period": _KEY[1],
        "main_account": main_account,
        "adjustment_type": adjustment_type,
        "amount": amount,
        "null_rows": null_rows,
    }


def test_every_row_lands_in_exactly_one_bucket_none_dropped():
    rows = [
        _row("1110", 100.0),               # leaf under heading 1
        _row("3300", 50.0),                 # leaf under heading 3
        _row("4100", 40.0),                 # leaf under heading 4 (P&L)
        _row("99", 30.0),                   # Published leaf, blank parent, P&L
        _row("ZZ_UNMAPPED", 10.0),          # not in chart
        _row("CTA", 5.07, adjustment_type="cta"),  # CTA, literal "CTA" not a chart code
        _row("1", 20.0),                    # posted to a heading (is_group)
    ]
    out = M.heading_amounts(rows, _ACCOUNTS, {_KEY})

    assert out["headings"] == {"1": 100.0, "3": 50.0, "4": 40.0}
    assert out["no_heading"] == {M.PL: 30.0}
    assert out["not_in_chart"] == {"ZZ_UNMAPPED": 10.0, "1": 20.0}
    assert out["cta"] == 5.07
    # pl_total = no_heading[PL] (30) + heading "4" (P&L, 40); headings 1/3 are BS.
    assert out["pl_total"] == 70.0

    total_in = sum(r["amount"] for r in rows)
    total_out = (
        sum(out["headings"].values())
        + sum(out["no_heading"].values())
        + sum(out["not_in_chart"].values())
        + out["cta"]
    )
    assert round(total_in, 2) == round(total_out, 2)


def test_cta_row_is_never_in_not_in_chart():
    rows = [_row("CTA", 5.07, adjustment_type="cta")]
    out = M.heading_amounts(rows, _ACCOUNTS, {_KEY})
    assert "CTA" not in out["not_in_chart"]
    assert out["cta"] == 5.07
    assert out["not_in_chart"] == {}


def test_amount_none_raises_naming_the_account():
    """Failure path: never read as 0."""
    rows = [_row("1110", None)]
    with pytest.raises(ValueError, match="1110"):
        M.heading_amounts(rows, _ACCOUNTS, {_KEY})


def test_null_rows_raises_naming_the_account():
    """Failure path: never read as 0."""
    rows = [_row("1110", 100.0, null_rows=1)]
    with pytest.raises(ValueError, match="1110"):
        M.heading_amounts(rows, _ACCOUNTS, {_KEY})


def test_decimal_sum_is_exact():
    rows = [_row("1110", 0.10), _row("1110", 0.20)]
    out = M.heading_amounts(rows, _ACCOUNTS, {_KEY})
    assert out["headings"]["1"] == 0.30


def test_a_row_outside_keys_is_not_summed():
    rows = [_row("1110", 100.0)]
    out = M.heading_amounts(rows, _ACCOUNTS, {(2024, 1)})
    assert out["headings"] == {}


# --- heading_order -------------------------------------------------------

def test_heading_order_is_by_lft_not_alphabetical():
    """4 (COST OF SALES) before 5 (NET SALES) even though 5's name sorts
    first (journal_model.statement_effect sorts by name; this is a
    deliberate difference — chart order, recon)."""
    accounts = {
        "4": {"account_name": "COST OF SALES", "is_group": True, "statement_section": M.PL, "lft": 13},
        "5": {"account_name": "NET SALES", "is_group": True, "statement_section": M.PL, "lft": 17},
    }
    assert M.heading_order(accounts)[M.PL] == ["4", "5"]


def test_heading_order_splits_by_section():
    accounts = {
        "1": {"account_name": "ASSETS", "is_group": True, "statement_section": M.BS, "lft": 1},
        "4": {"account_name": "COST OF SALES", "is_group": True, "statement_section": M.PL, "lft": 13},
    }
    order = M.heading_order(accounts)
    assert order[M.BS] == ["1"]
    assert order[M.PL] == ["4"]


def test_heading_order_ignores_leaves():
    accounts = {
        "1": {"account_name": "ASSETS", "is_group": True, "statement_section": M.BS, "lft": 1},
        "1110": {"account_name": "Cash", "is_group": False, "statement_section": M.BS, "lft": 2},
    }
    assert M.heading_order(accounts)[M.BS] == ["1"]


def test_heading_with_no_statement_section_raises_naming_it():
    """Failure path."""
    accounts = {
        "9": {"account_name": "SUSPENSE", "is_group": True, "statement_section": "", "lft": 25},
    }
    with pytest.raises(ValueError, match="9"):
        M.heading_order(accounts)


# --- module contract --------------------------------------------------------

def test_module_imports_no_frappe():
    """Mirror test_close_journal_model.py / test_assertion_warn_amber.py:269."""
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("frappe", "konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol")
