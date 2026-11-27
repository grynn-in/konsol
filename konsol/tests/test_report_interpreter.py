"""The FORMULAS-mode template interpreter (konsol#548 v1, T5). Pure: no site.

Label rule (Deepak Pai, 27 Nov 2026, Q1): ``{month_abbrev}`` shows the
calendar-month abbreviation of each Regular period's start only when every
Regular period of the year starts in a different calendar month; otherwise
each column shows the period's declared label. A year without Regular periods
is refused, never filled with Jan–Dec.
"""
import datetime
import os

from konsol import report_compiler
from konsol import report_template_model as model

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = model.load_templates(os.path.join(APP_DIR, "report_templates"))
PNL = TEMPLATES["pnl_monthly"]

ACCOUNTS = [("ZZ4000", "ZZ Sales"), ("ZZ5000", "ZZ Costs")]
SIGN = "debit positive; a negative total is a profit"


def _period(number, code, label, start):
    return {"number": number, "code": code, "label": label, "start_date": start}


JAN_DEC = [_period(m, f"P{m:02d}", f"P{m:02d}", f"2024-{m:02d}-01") for m in range(1, 13)]
APR_MAR = [
    _period(i + 1, f"P{i + 1:02d}", f"P{i + 1:02d}",
            f"{2024 + (3 + i) // 12}-{(3 + i) % 12 + 1:02d}-01")
    for i in range(12)
]
# Thirteen four-week periods from 1 Jan 2024: P13 starts 3 Dec, in P12's month.
THIRTEEN = [
    _period(i + 1, f"P{i + 1:02d}", f"Week {4 * i + 1}",
            (datetime.date(2024, 1, 1) + datetime.timedelta(days=28 * i)).isoformat())
    for i in range(13)
]


def _letters(n, start="B"):
    out = []
    for i in range(n):
        k = ord(start) - ord("A") + i + 1
        s = ""
        while k:
            k, r = divmod(k - 1, 26)
            s = chr(65 + r) + s
        out.append(s)
    return out


def _by_range(spec):
    return {c["range"]: c for c in spec["cells"]}


def _compile(periods, accounts=ACCOUNTS, year=2024, **params):
    return model.compile_formulas(
        PNL,
        dict({"entity": "ZZE", "year": year}, **params),
        {"periods": periods, "accounts": {"lines": list(accounts)}},
    )


def _refusal(fn):
    try:
        fn()
    except ValueError as exc:
        return str(exc)
    return None


# -- formula builder -------------------------------------------------------

def test_builder_four_arguments_when_the_rest_are_defaults():
    assert model.epm_formula("ZZE", 2024, 3, "4000") == '=K.EPM("ZZE", 2024, 3, "4000")'
    assert model.epm_formula("ZZE", 2024, 3, "4000", scenario="actuals",
                             scenario_id="actuals") == '=K.EPM("ZZE", 2024, 3, "4000")'


def test_builder_doubles_quotes_like_today():
    assert model.epm_formula('ZZ "Q" Ltd', "2024", "1", '40"00') == (
        '=K.EPM("ZZ ""Q"" Ltd", 2024, 1, "40""00")'
    )


def test_builder_keeps_empty_middle_arguments_and_drops_trailing_ones():
    assert model.epm_formula("E1", 2026, 3, "4000", dimensions="a=1;b=2") == (
        '=K.EPM("E1", 2026, 3, "4000", , , "a=1;b=2")'
    )
    assert model.epm_formula("E1", 2026, 3, "4000", scenario="budget", scenario_id="BUD",
                             layer="board") == (
        '=K.EPM("E1", 2026, 3, "4000", , "budget", , "BUD", , , "board")'
    )
    assert model.epm_formula("E1", 2026, "Q1", "4000", measure="period_amount") == (
        '=K.EPM("E1", 2026, "Q1", "4000", "period_amount")'
    )


# -- compile_formulas on pnl_monthly ----------------------------------------

def test_jan_dec_compiles_to_todays_cells():
    spec = _compile(JAN_DEC)
    by_range = _by_range(spec)
    assert by_range["B2:M2"]["values"] == [[
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]]
    assert by_range["A3"]["values"] == [["ZZ Sales"]]
    assert by_range["B3:M3"]["formulas"] == [[
        f'=K.EPM("ZZE", 2024, {p}, "ZZ4000")' for p in range(1, 13)]]
    assert by_range["A5"]["values"] == [[f"Net Profit and Loss ({SIGN})"]]
    assert by_range["B5:M5"]["formulas"] == [[f"=SUM({c}3:{c}4)" for c in _letters(12)]]
    assert [spec[k] for k in ("template_id", "mode", "sheet_name", "entity", "year", "scenario_id")] == [
        "pnl_monthly", "formulas", "P&L", "ZZE", 2024, "actuals"]


def test_apr_mar_year_labels_apr_to_mar():
    spec = _compile(APR_MAR)
    assert _by_range(spec)["B2:M2"]["values"] == [[
        "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]]
    assert _by_range(spec)["B3:M3"]["formulas"][0][0] == '=K.EPM("ZZE", 2024, 1, "ZZ4000")'


def test_thirteen_periods_give_thirteen_columns_with_declared_labels():
    spec = _compile(THIRTEEN, accounts=ACCOUNTS[:1])
    by_range = _by_range(spec)
    assert by_range["B2:N2"]["values"] == [[p["label"] for p in THIRTEEN]]
    assert by_range["B3:N3"]["formulas"] == [[
        f'=K.EPM("ZZE", 2024, {n}, "ZZ4000")' for n in range(1, 14)]]
    assert by_range["B4:N4"]["formulas"] == [[f"=SUM({c}3:{c}3)" for c in _letters(13)]]
    assert spec["report"]["area"] == "A1:N4"


def test_two_periods_starting_in_one_month_use_declared_labels():
    # A 4-4-5 year from 1 Jan 2024: P01 starts 1 Jan and P02 starts 29 Jan,
    # so two Regular periods start in January.
    starts = ["2024-01-01", "2024-01-29", "2024-02-26", "2024-04-01", "2024-04-29",
              "2024-05-27", "2024-07-01", "2024-07-29", "2024-08-26", "2024-09-30",
              "2024-10-28", "2024-11-25"]
    periods = [_period(i + 1, f"P{i + 1:02d}", f"M{i + 1}", s) for i, s in enumerate(starts)]
    periods[4]["label"] = "P05"  # a period without a declared label: the code
    labels = _by_range(_compile(periods))["B2:M2"]["values"][0]
    assert labels == ["M1", "M2", "M3", "M4", "P05", "M6", "M7", "M8", "M9", "M10", "M11", "M12"]


def test_a_period_without_a_start_date_uses_declared_labels():
    periods = [dict(p) for p in JAN_DEC]
    periods[0]["start_date"] = None
    assert _by_range(_compile(periods))["B2:M2"]["values"][0] == [p["code"] for p in JAN_DEC]


def test_columns_follow_period_number_order():
    shuffled = list(reversed(JAN_DEC))
    assert _by_range(_compile(shuffled))["B2:M2"]["values"][0][:2] == ["Jan", "Feb"]


def test_no_regular_periods_is_refused_never_jan_dec():
    for periods in (None, []):
        msg = _refusal(lambda: _compile(periods))
        assert msg is not None and msg.startswith("No Regular periods are declared for FY2024"), msg
        msg = _refusal(lambda: report_compiler.build_cell_map(
            "pnl_monthly", "ZZE", 2024, accounts=ACCOUNTS, periods=periods))
        assert msg is not None and msg.startswith("No Regular periods are declared for FY2024"), msg


def test_a_non_regular_period_is_refused():
    periods = [_period(0, "OPN", "Opening", "2024-01-01")] + JAN_DEC
    periods[0]["period_type"] = "Opening"
    msg = _refusal(lambda: _compile(periods))
    assert msg is not None and "OPN" in msg and "Regular" in msg, msg


def test_no_rows_uses_the_template_text():
    for accounts in ([],):
        assert _refusal(lambda: _compile(JAN_DEC, accounts=accounts)) == PNL["errors"]["no_rows"]
    msg = _refusal(lambda: model.compile_formulas(
        PNL, {"entity": "ZZE", "year": 2024}, {"periods": JAN_DEC}))
    assert msg == PNL["errors"]["no_rows"]


def test_several_charts_uses_the_template_text_with_sorted_charts():
    msg = _refusal(lambda: model.compile_formulas(
        PNL, {"entity": "ZZE", "year": 2024},
        {"periods": JAN_DEC, "accounts": {"lines": ACCOUNTS},
         "charts": {"lines": ["Zeta", "Alpha"]}}))
    assert msg == (
        "The monthly P&L reads one chart, and Published Profit and Loss accounts exist "
        "in charts Alpha, Zeta. Make one chart Inactive or Draft."
    )


def test_missing_required_and_unknown_params_are_refused():
    assert "entity" in _refusal(lambda: model.compile_formulas(
        PNL, {"year": 2024}, {"periods": JAN_DEC, "accounts": {"lines": ACCOUNTS}}))
    assert "colour" in _refusal(lambda: model.compile_formulas(
        PNL, {"entity": "ZZE", "year": 2024, "colour": "red"},
        {"periods": JAN_DEC, "accounts": {"lines": ACCOUNTS}}))


def test_report_key_names_template_version_params_and_area():
    spec = _compile(JAN_DEC, scenario_id="BUD")
    assert spec["report"] == {
        "template_id": "pnl_monthly",
        "template_version": 1,
        "params": {"entity": "ZZE", "year": 2024, "scenario_id": "BUD"},
        "area": "A1:M5",
    }
    assert spec["scenario_id"] == "BUD"
    assert "formats" not in spec


def test_facade_report_key_matches_its_arguments():
    spec = report_compiler.build_cell_map("pnl_monthly", "ZZE", "2026", "BUD",
                                          accounts=ACCOUNTS[:1], periods=JAN_DEC)
    assert spec["year"] == 2026
    assert spec["report"]["params"] == {"entity": "ZZE", "year": 2026, "scenario_id": "BUD"}
    assert spec["report"]["area"] == "A1:M4"


def test_facade_refuses_a_snapshot_template_for_formulas():
    msg = _refusal(lambda: report_compiler.build_cell_map(
        "trial_balance_long", "ZZE", 2024, periods=JAN_DEC))
    assert msg == "Unknown template_id for formulas: 'trial_balance_long'"
