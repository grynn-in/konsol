"""Statement model, pure: konsol/close/statement_model.py
(konsol#305 Wave 4, N48 part 1 + N49 part 2: sign, columns, CTA,
current-year result, residual — AMENDED 4 Oct, W4-2 2a-ii: BS display
shows liabilities and equity POSITIVE, each BS heading's own side from
Main Account ``normal_balance`` (Credit -> flip); W4-E3 debit-positive is
WITHDRAWN).

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

_CPM_PATH = os.path.join(APP_DIR, "close", "close_policy_model.py")
_cpm_spec = importlib.util.spec_from_file_location("close_policy_model_under_test", _CPM_PATH)
CPM = importlib.util.module_from_spec(_cpm_spec)
_cpm_spec.loader.exec_module(CPM)


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


# --- statement (N49, sign/columns/CTA/current-year result/residual) -------
#
# W4-2 2a-ii (AMENDED 4 Oct): BS display shows liabilities and equity
# POSITIVE. A BS heading's own side comes from Main Account
# ``normal_balance`` on the heading (Credit -> flip; Debit -> unflipped).
# P&L keeps the single section-wide flip (income positive, costs negative).
# The residual line stays in net-debit terms (unflipped, by construction it
# needs no extra sign to prove assets = liabilities + equity): verified
# below by an explicit invariant on the balanced fixture.

_S_CALENDAR = _calendar((2024, 2025))
_S_KEY = (2025, 7)

#: #305-W4-1: Main Account rows for the two declared statement accounts
#: (3300 the CTA account, 3100 the current-year result account — both
#: Published Balance Sheet leaves under heading 3, EQUITY, N41 facts).
_S_DECLARED_ROWS = {
    "3300": {"is_group": False, "status": "Published", "statement_section": M.BS,
              "account_name": "AOCI - CTA"},
    "3100": {"is_group": False, "status": "Published", "statement_section": M.BS,
              "account_name": "Retained earnings"},
}


def _s_accounts():
    """The live shape (recon, N41/N48/N49 facts): ASSETS (1, Debit),
    LIABILITIES (2, Credit), EQUITY (3, Credit) on the Balance Sheet;
    COST OF SALES (4) on the Profit and Loss; 3300/3100 are leaves under 3
    so N49 places the CTA and the current-year result on EQUITY's line."""
    return {
        "1": {"account_name": "ASSETS", "parent_account": None, "is_group": True,
              "statement_section": M.BS, "lft": 1, "normal_balance": "Debit"},
        "2": {"account_name": "LIABILITIES", "parent_account": None, "is_group": True,
              "statement_section": M.BS, "lft": 5, "normal_balance": "Credit"},
        "3": {"account_name": "EQUITY", "parent_account": None, "is_group": True,
              "statement_section": M.BS, "lft": 9, "normal_balance": "Credit"},
        "4": {"account_name": "COST OF SALES", "parent_account": None, "is_group": True,
              "statement_section": M.PL, "lft": 13},
        "1110": {"account_name": "Cash", "parent_account": "1", "is_group": False,
                  "statement_section": M.BS, "lft": 2},
        "2100": {"account_name": "Payables", "parent_account": "2", "is_group": False,
                  "statement_section": M.BS, "lft": 6},
        "3200": {"account_name": "Share capital", "parent_account": "3", "is_group": False,
                  "statement_section": M.BS, "lft": 10},
        "3300": {"account_name": "AOCI - CTA", "parent_account": "3", "is_group": False,
                  "statement_section": M.BS, "lft": 11},
        "3100": {"account_name": "Retained earnings", "parent_account": "3", "is_group": False,
                  "statement_section": M.BS, "lft": 12},
        "4100": {"account_name": "Net sales", "parent_account": "4", "is_group": False,
                  "statement_section": M.PL, "lft": 14},
    }


def _s_rows():
    """The live shape (recon): assets 1,735.10, liabilities -803.70, equity
    (own leaf) -684.90, CTA -5.07, FY P&L to date -241.43 — balanced."""
    return [
        _row("1110", 1735.10),
        _row("2100", -803.70),
        _row("3200", -684.90),
        _row("CTA", -5.07, adjustment_type="cta"),
        _row("4100", -241.43),
    ]


def _s_declared(cta_account="3300", result_account="3100"):
    return CPM.statement_accounts(cta_account, result_account, _S_DECLARED_ROWS)


def _bs_line(result, heading):
    for line in result["sections"][1]["lines"]:
        if line.get("heading") == heading:
            return line
    raise AssertionError(f"no BS line for heading {heading!r}")


def _residual_line(result):
    lines = result["sections"][1]["lines"]
    assert lines[-1]["kind"] == "residual"
    return lines[-1]


def test_legend_text_is_the_amended_4_oct_text():
    result = M.statement(_s_rows(), _s_accounts(), _S_CALENDAR, _S_KEY, _s_declared())
    assert result["legend"] == (
        "Profit and loss: income positive, costs in brackets. "
        "Balance sheet: assets, liabilities and equity positive."
    )


def test_balanced_fixture_both_declared_residual_is_zero_and_bs_balances():
    """W4-2 2a-ii: assets positive, liabilities and equity positive (not
    bracketed); the equity line includes the CTA and the current-year
    result; residual 0.00; P&L net result 241.43 (profit positive); and the
    balance-sheet identity (assets = liabilities + equity incl. CTA/CYR)
    holds exactly in display terms, proving the flip did not break it."""
    result = M.statement(_s_rows(), _s_accounts(), _S_CALENDAR, _S_KEY, _s_declared())

    assets = _bs_line(result, "1")
    liabilities = _bs_line(result, "2")
    equity = _bs_line(result, "3")
    residual = _residual_line(result)

    assert assets["current"] == 1735.10
    assert liabilities["current"] == 803.70          # positive, not bracketed (2a-ii)
    assert equity["current"] == 931.40                # -684.9 -5.07 -241.43, flipped positive
    assert residual["current"] == 0.0
    assert "explained" not in residual

    includes = {i["kind"]: i["current"] for i in equity["includes"]}
    assert includes["cta"] == 5.07
    assert includes["current_year_result"] == 241.43

    net_result = result["sections"][0]["lines"][-1]
    assert net_result["kind"] == "net_result"
    assert net_result["current"] == 241.43

    # The identity the row asks to prove after the flip: assets equal
    # liabilities + equity (which already includes CTA and the
    # current-year result).
    assert round(assets["current"] - (liabilities["current"] + equity["current"]), 2) == 0.0


def test_both_undeclared_residual_is_the_unplaced_total_never_balanced_silently():
    """Failure path: never silently balanced. Residual 246.50 =
    241.43 + 5.07, explained naming both parts, unexplained 0.00."""
    result = M.statement(_s_rows(), _s_accounts(), _S_CALENDAR, _S_KEY, _s_declared("", ""))
    residual = _residual_line(result)
    assert residual["current"] == 246.50
    amounts = sorted(e["amount"] for e in residual["explained"])
    assert amounts == [5.07, 241.43]
    assert residual["unexplained"] == 0.0

    equity = _bs_line(result, "3")
    assert "includes" not in equity  # placed nowhere, never defaulted


def test_cta_declared_only_residual_is_the_result_only():
    result = M.statement(_s_rows(), _s_accounts(), _S_CALENDAR, _S_KEY, _s_declared("3300", ""))
    residual = _residual_line(result)
    assert residual["current"] == 241.43
    assert len(residual["explained"]) == 1
    assert residual["explained"][0]["amount"] == 241.43
    assert residual["unexplained"] == 0.0

    equity = _bs_line(result, "3")
    kinds = {i["kind"] for i in equity["includes"]}
    assert kinds == {"cta"}


def test_not_in_chart_row_names_it_and_the_residual_explains_it():
    """Failure path: a row outside the chart (other than CTA) never
    vanishes — it is its own line, and the residual's ``explained``
    names it."""
    rows = _s_rows() + [_row("ZZ_UNMAPPED", 10.0)]
    result = M.statement(rows, _s_accounts(), _S_CALENDAR, _S_KEY, _s_declared())
    lines = result["sections"][1]["lines"]
    not_in_chart = next(l for l in lines if l["kind"] == "not_in_chart")
    assert not_in_chart["codes"] == ["ZZ_UNMAPPED"]
    assert not_in_chart["current"] == 10.0

    residual = _residual_line(result)
    assert residual["current"] == 10.0
    assert residual["explained"] == [{"label": M._NOT_IN_CHART_LABEL, "amount": 10.0}]
    assert residual["unexplained"] == 0.0


def test_sign_revenue_positive_cost_negative_asset_positive_liability_positive():
    """P&L: income positive, costs negative (unchanged). BS (2a-ii):
    liabilities positive too (flipped from their net-credit raw amount)."""
    accounts = _s_accounts()
    rows = [
        _row("4100", -100.0),   # a revenue row, net debit -100
        _row("1110", 500.0),    # an asset, net debit +500
        _row("2100", -300.0),   # a liability, net debit -300
    ]
    result = M.statement(rows, accounts, _S_CALENDAR, _S_KEY, _s_declared("", ""))
    pl_heading = next(l for l in result["sections"][0]["lines"] if l.get("heading") == "4")
    assert pl_heading["current"] == 100.0           # revenue shows positive

    assert _bs_line(result, "1")["current"] == 500.0    # asset positive
    assert _bs_line(result, "2")["current"] == 300.0    # liability POSITIVE (2a-ii), not -300


def test_a_cost_row_shows_negative_and_net_result_nets_correctly():
    accounts = _s_accounts()
    rows = [
        _row("4100", 40.0),  # a cost, net debit +40
    ]
    result = M.statement(rows, accounts, _S_CALENDAR, _S_KEY, _s_declared("", ""))
    pl_heading = next(l for l in result["sections"][0]["lines"] if l.get("heading") == "4")
    assert pl_heading["current"] == -40.0
    net_result = result["sections"][0]["lines"][-1]
    assert net_result["current"] == -40.0


def test_no_subtotal_labels_are_present():
    """#305-W4-3 3a: headings + section totals only."""
    result = M.statement(_s_rows(), _s_accounts(), _S_CALENDAR, _S_KEY, _s_declared())
    labels = [l.get("label", "") for section in result["sections"] for l in section["lines"]]
    for label in labels:
        assert "Gross profit" not in label
        assert "EBITDA" not in label
        assert "Operating profit" not in label


def test_headings_are_in_lft_order_in_both_sections():
    result = M.statement(_s_rows(), _s_accounts(), _S_CALENDAR, _S_KEY, _s_declared())
    pl_headings = [l["heading"] for l in result["sections"][0]["lines"] if l["kind"] == "heading"]
    bs_headings = [l["heading"] for l in result["sections"][1]["lines"] if l["kind"] == "heading"]
    assert pl_headings == ["4"]
    assert bs_headings == ["1", "2", "3"]


def test_comparison_not_loaded_names_the_period_never_shows_zero():
    """Failure path: FY2025 P7 with no P6 rows -> P&L comparison None,
    variance None, the note names FY2025 P06 (never silently 0)."""
    result = M.statement(_s_rows(), _s_accounts(), _S_CALENDAR, _S_KEY, _s_declared())
    assert result["periods"]["comparison"] == (2025, 6)
    assert result["periods"]["comparison_note"] == "No rows in the warehouse for FY2025 P06"
    pl_heading = next(l for l in result["sections"][0]["lines"] if l.get("heading") == "4")
    assert pl_heading["comparison"] is None
    assert pl_heading["variance"] is None


def test_p1_compares_to_previous_fys_p12_w4_7():
    rows = [
        {**_row("4100", -10.0), "fiscal_year": 2025, "fiscal_period": 1},
        {**_row("4100", -20.0), "fiscal_year": 2024, "fiscal_period": 12},
    ]
    result = M.statement(rows, _s_accounts(), _S_CALENDAR, (2025, 1), _s_declared())
    assert result["periods"]["comparison"] == (2024, 12)
    assert result["periods"]["comparison_note"] is None
    pl_heading = next(l for l in result["sections"][0]["lines"] if l.get("heading") == "4")
    assert pl_heading["current"] == 10.0
    assert pl_heading["comparison"] == 20.0
    assert pl_heading["variance"] == -10.0


def test_bs_heading_with_blank_normal_balance_is_a_setup_gap_never_defaulted():
    """Failure path (AMENDED 4 Oct, W4-2 2a-ii): a BS heading with no
    declared ``normal_balance`` is never silently defaulted to a side."""
    accounts = _s_accounts()
    accounts["2"]["normal_balance"] = ""
    with pytest.raises(ValueError, match="statement_heading_side_undeclared"):
        M.statement(_s_rows(), accounts, _S_CALENDAR, _S_KEY, _s_declared())
    with pytest.raises(ValueError, match=r"\b2\b"):
        M.statement(_s_rows(), accounts, _S_CALENDAR, _S_KEY, _s_declared())


def test_sign_key_present_only_on_heading_lines_pl_and_bs():
    """N54: ``statement()`` already applies a display-sign multiplier to
    every heading line's raw amount (P&L: the section-wide -1; BS: the
    heading's own ``_bs_heading_sides`` result). That exact multiplier
    now rides along as ``"sign"`` on the line, so a JS consumer can reuse
    it instead of re-deriving it from ``normal_balance`` (one sign rule,
    never two). P&L headings always carry -1; BS headings carry +1
    (Debit) or -1 (Credit). No other line kind — ``no_heading``,
    ``net_result``, ``not_in_chart``, ``residual`` — carries a ``sign``
    key at all."""
    accounts = _s_accounts()
    accounts["99"] = {"account_name": "SUSPENSE", "parent_account": "", "is_group": False,
                       "statement_section": M.PL, "lft": 99}
    accounts["98"] = {"account_name": "BS SUSPENSE", "parent_account": "", "is_group": False,
                       "statement_section": M.BS, "lft": 98}
    rows = _s_rows() + [_row("99", 15.0), _row("98", 25.0), _row("ZZ_UNMAPPED", 10.0)]
    result = M.statement(rows, accounts, _S_CALENDAR, _S_KEY, _s_declared())

    pl_lines = result["sections"][0]["lines"]
    pl_kinds_seen = {line["kind"] for line in pl_lines}
    assert {"heading", "no_heading", "net_result"} <= pl_kinds_seen
    for line in pl_lines:
        if line["kind"] == "heading":
            assert line["sign"] == -1
        else:
            assert "sign" not in line

    bs_sides = {"1": 1, "2": -1, "3": -1}  # ASSETS Debit, LIABILITIES/EQUITY Credit
    bs_lines = result["sections"][1]["lines"]
    bs_kinds_seen = {line["kind"] for line in bs_lines}
    assert {"heading", "no_heading", "not_in_chart", "residual"} <= bs_kinds_seen
    for line in bs_lines:
        if line["kind"] == "heading":
            assert line["sign"] == bs_sides[line["heading"]]
        else:
            assert "sign" not in line


def test_bs_heading_sign_matches_debit_and_credit_declared_sides():
    """A Debit-normal BS heading's line carries ``sign: 1``; a
    Credit-normal one carries ``sign: -1`` — the exact
    ``_bs_heading_sides`` result for that heading, not re-derived."""
    result = M.statement(_s_rows(), _s_accounts(), _S_CALENDAR, _S_KEY, _s_declared())
    assert _bs_line(result, "1")["sign"] == 1    # ASSETS, Debit
    assert _bs_line(result, "2")["sign"] == -1   # LIABILITIES, Credit
    assert _bs_line(result, "3")["sign"] == -1   # EQUITY, Credit


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
