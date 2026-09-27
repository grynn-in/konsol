"""TB view model, pure: konsol/close/tb_view_model.py (konsol#305 A12, story 3.4).

This period against the previous declared Regular period, by account. Loaded
by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(APP_DIR, "close", "tb_view_model.py")
_spec = importlib.util.spec_from_file_location("close_tb_view_model_under_test", MODEL_PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

PM = "Period movement"
PEB = "Period-end balance"


def _row(account, debit=0.0, credit=0.0, partner=""):
    """A parse_tb_csv row (trial_balance_submission.py:80)."""
    return {
        "main_account": account, "debit": debit, "credit": credit,
        "description": "", "partner_data_area_id": partner, "amount_basis": "",
    }


def _period(fy, fp, period_type="Regular"):
    return {
        "fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
        "period_label": "FY%d P%02d" % (fy, fp), "period_type": period_type,
        "start_date": None, "end_date": None, "quarter": None, "status": "Open",
    }


def _calendar():
    """FY2024 and FY2025: P00 Opening, P01-P12 Regular, P13 Adjustment."""
    rows = []
    for fy in (2024, 2025):
        rows.append(_period(fy, 0, "Opening"))
        for fp in range(1, 13):
            rows.append(_period(fy, fp))
        rows.append(_period(fy, 13, "Adjustment"))
    return rows


# --- compare ---------------------------------------------------------------

def test_rows_are_joined_by_account_and_partner_and_sorted_by_account():
    current = [_row("4000", credit=150.0), _row("1100", debit=80.0, partner="ZZB"),
               _row("1000", debit=70.0)]
    previous = [_row("1000", debit=50.0), _row("4000", credit=100.0),
                _row("1100", debit=30.0, partner="ZZB")]
    out = M.compare(current, previous, PM, PM, "P08")
    assert [(r["account"], r["partner"]) for r in out["rows"]] == [
        ("1000", ""), ("1100", "ZZB"), ("4000", "")]
    by = {(r["account"], r["partner"]): r for r in out["rows"]}
    assert by[("1000", "")] == {"account": "1000", "partner": "", "is_ic": False,
                                "current": 70.0, "previous": 50.0, "change": 20.0}
    assert by[("4000", "")]["current"] == -150.0  # net = debit - credit
    assert by[("4000", "")]["change"] == -50.0
    assert by[("1100", "ZZB")]["is_ic"] is True
    assert by[("1100", "ZZB")]["change"] == 50.0
    assert out["basis_note"] is None
    assert out["previous_note"] is None
    assert out["previous_code"] == "P08"


def test_same_account_with_and_without_partner_are_separate_rows():
    current = [_row("1100", debit=10.0), _row("1100", debit=5.0, partner="ZZB")]
    out = M.compare(current, [], PM, PM, "P08")
    assert [(r["account"], r["partner"], r["is_ic"]) for r in out["rows"]] == [
        ("1100", "", False), ("1100", "ZZB", True)]


def test_a_row_in_only_one_period_has_the_other_side_none():
    current = [_row("1000", debit=10.0), _row("2000", credit=5.0)]
    previous = [_row("1000", debit=4.0), _row("3000", debit=9.0)]
    by = {r["account"]: r for r in M.compare(current, previous, PM, PM, "P08")["rows"]}
    assert by["2000"]["previous"] is None and by["2000"]["change"] is None
    assert by["2000"]["current"] == -5.0
    assert by["3000"]["current"] is None and by["3000"]["change"] is None
    assert by["3000"]["previous"] == 9.0


def test_different_bases_make_every_change_none_with_a_basis_note():
    current = [_row("1000", debit=10.0), _row("2000", credit=5.0)]
    previous = [_row("1000", debit=4.0), _row("2000", credit=1.0)]
    out = M.compare(current, previous, PEB, PM, "P08")
    assert all(r["change"] is None for r in out["rows"])
    # the amounts themselves are still shown
    assert {r["account"]: r["previous"] for r in out["rows"]} == {"1000": 4.0, "2000": -1.0}
    assert out["basis_note"] == (
        "This period is Period-end balance and P08 is Period movement: "
        "the change is not comparable.")


def test_no_previous_tb_is_said_and_never_compared_against_zero():
    current = [_row("1000", debit=10.0)]
    out = M.compare(current, None, PM, None, "P08")
    assert out["previous_note"] == "No trial balance for P08"
    assert out["basis_note"] is None
    (row,) = out["rows"]
    assert row["current"] == 10.0
    assert row["previous"] is None  # not 0.0
    assert row["change"] is None    # not 10.0


def test_an_empty_previous_tb_is_loaded_not_missing():
    """[] is a loaded TB with no rows; only None means no TB."""
    out = M.compare([_row("1000", debit=10.0)], [], PM, PM, "P08")
    assert out["previous_note"] is None
    assert out["rows"][0]["previous"] is None


def test_no_previous_declared_period_is_said():
    out = M.compare([_row("1000", debit=10.0)], None, PM, None, None)
    assert out["previous_note"] == "No previous period is declared in the fiscal calendar"
    assert out["rows"][0]["change"] is None


def test_unknown_basis_is_refused():
    for current_basis, previous_basis in (("Monthly", PM), (PM, "Monthly"), (None, PM)):
        try:
            M.compare([_row("1000", debit=1.0)], [], current_basis, previous_basis, "P08")
        except ValueError as exc:
            assert "Monthly" in str(exc) or "None" in str(exc)
        else:
            raise AssertionError("basis %r / %r was accepted" % (current_basis, previous_basis))


def test_repeated_key_is_summed_per_account():
    out = M.compare([_row("1000", debit=10.0), _row("1000", debit=2.5)], None, PM, None, "P08")
    assert out["rows"][0]["current"] == 12.5


# --- previous_period -------------------------------------------------------

def test_previous_period_inside_the_year():
    prev = M.previous_period(_calendar(), 2025, 9)
    assert (prev["fiscal_year"], prev["fiscal_period"]) == (2025, 8)


def test_previous_period_crosses_the_year_boundary_skipping_opening_and_adjustment():
    """P1's previous is last year's P12, not this year's P00 or last year's P13."""
    prev = M.previous_period(_calendar(), 2025, 1)
    assert (prev["fiscal_year"], prev["fiscal_period"]) == (2024, 12)
    assert prev["period_type"] == "Regular"


def test_previous_period_ignores_the_input_order():
    rows = list(reversed(_calendar()))
    prev = M.previous_period(rows, 2025, 1)
    assert (prev["fiscal_year"], prev["fiscal_period"]) == (2024, 12)


def test_first_declared_period_has_no_previous():
    assert M.previous_period(_calendar(), 2024, 1) is None


def test_period_not_in_the_calendar_is_refused():
    try:
        M.previous_period(_calendar(), 2030, 1)
    except ValueError as exc:
        assert "2030" in str(exc)
    else:
        raise AssertionError("an undeclared period was accepted")


def test_non_regular_current_period_is_refused():
    try:
        M.previous_period(_calendar(), 2025, 13)
    except ValueError as exc:
        assert "Regular" in str(exc)
    else:
        raise AssertionError("a non-Regular period was accepted")


def test_module_imports_no_frappe():
    with open(MODEL_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith(("frappe", "konsol")) for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("frappe", "konsol"))
