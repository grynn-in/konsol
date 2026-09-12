"""Bulk trial balance files (konsol/tb_bulk_model.py): split one file into
entity-periods, and never accept what a single upload would refuse."""
import csv
import importlib.util
import io
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("tb_bulk_model", os.path.join(APP_DIR, "tb_bulk_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

HEADER = ["data_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit"]


def _raises(fn, *args):
    try:
        fn(*args)
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError")


def test_splits_into_entity_periods_in_file_order():
    table = [HEADER,
             ["AMDE", "2025", "12", "1010", "100", ""],
             ["AMUS", "2025", "12", "1010", "", "5"],
             ["AMDE", "2025", "12", "2010", "", "100"],
             ["AMDE", "2024", "12", "1010", "1", "0"]]
    groups = M.split_table(table)
    assert list(groups) == [("AMDE", 2025, 12), ("AMUS", 2025, 12), ("AMDE", 2024, 12)]
    assert [r["main_account"] for r in groups[("AMDE", 2025, 12)]] == ["1010", "2010"]
    assert groups[("AMUS", 2025, 12)][0] == {"main_account": "1010", "debit": 0.0, "credit": 5.0, "description": ""}


def test_header_is_forgiving_about_case_spaces_and_aliases():
    table = [["Entity", "Year", "Period", "Account", "Debit", "Credit", "Description"],
             ["AMDE", "2025", "1", "1010", "10.005", "0", "cash"]]
    rows = M.split_table(table)[("AMDE", 2025, 1)]
    assert rows[0]["debit"] == 10.01 or rows[0]["debit"] == 10.0   # rounded to cents like a single upload
    assert rows[0]["description"] == "cash"


def test_excel_cells_numbers_and_blank_lines():
    table = [HEADER, [None] * 6,
             ["AMDE", 2025.0, 12.0, 1010.0, 1234.5, None],
             ["AMDE", 2025, 12, 2010, None, 1234.5, None, None]]   # trailing empty cells are fine
    rows = M.split_table(table)[("AMDE", 2025, 12)]
    assert [r["main_account"] for r in rows] == ["1010", "2010"]
    assert rows[0]["debit"] == 1234.5 and rows[1]["credit"] == 1234.5


def test_structural_problems_are_reported_together_with_line_numbers():
    table = [HEADER,
             ["", "2025", "12", "1010", "1", "0"],
             ["AMDE", "twenty", "12", "1010", "1", "0"],
             ["AMDE", "2025", "12", "1010", "abc", "0"],
             ["AMDE", "2025", "12", "1010", "nan", "0"],
             ["AMDE", "2025", "12", "1010", "1", "0", "extra"]]
    msg = _raises(M.split_table, table)
    for expected in ("Line 2: data_area_id is blank", "Line 3: fiscal_year", "Line 4: debit must be a number",
                     "Line 5: debit must be a finite", "Line 6: more cells"):
        assert expected in msg, (expected, msg)


def test_missing_columns_and_empty_files():
    assert "Missing column(s) credit" in _raises(M.split_table, [HEADER[:-1], ["AMDE", "2025", "1", "1", "1"]])
    assert "empty" in _raises(M.split_table, [])
    assert "no data rows" in _raises(M.split_table, [HEADER])


def test_group_csv_is_the_single_upload_contract():
    rows = [{"main_account": "1010", "debit": 1234.5, "credit": 0.0, "description": "cash, main"}]
    parsed = list(csv.DictReader(io.StringIO(M.group_csv(rows))))
    assert parsed == [{"main_account": "1010", "debit": "1234.50", "credit": "0.00", "description": "cash, main"}]


def _check(**over):
    facts = dict(known_accounts={"1010", "2010"}, visible=True, leaf=True, period_status="Open", existing=None,
                 validate_rows=lambda rows, known_accounts=None: [])
    facts.update(over)
    rows = [{"main_account": "1010", "debit": 5.0, "credit": 0.0}, {"main_account": "2010", "debit": 0.0, "credit": 5.0}]
    return M.check_group(("AMDE", 2025, 12), rows, **facts)


def test_a_clean_group_is_ready():
    r = _check()
    assert r["ok"] and r["rows"] == 2 and r["total_debit"] == 5.0 and r["total_credit"] == 5.0


def test_every_single_upload_rule_applies():
    assert "no access" in _check(visible=False)["errors"][0]
    assert "is a group" in _check(leaf=False)["errors"][0]
    assert "is closed" in _check(period_status="Closed")["errors"][0]
    assert "already submitted" in _check(existing="TBS-00001")["errors"][0]
    # the single-submission validator's verdict is carried through as-is
    r = _check(validate_rows=lambda rows, known_accounts=None: ["Debits (5.00) do not equal credits"])
    assert not r["ok"] and r["errors"] == ["Debits (5.00) do not equal credits"]


def test_period_outside_1_to_12_is_refused():
    r = M.check_group(("AMDE", 2025, 13), [], known_accounts=set(), visible=True, leaf=True, period_status=None,
                      existing=None, validate_rows=lambda rows, known_accounts=None: [])
    assert "must be 1 to 12" in r["errors"][0]


def test_outcome():
    assert M.outcome(3, 0, 3) == "Loaded"
    assert M.outcome(2, 1, 3) == "Partly Loaded"
    assert M.outcome(0, 3, 3) == "Failed"
    assert M.outcome(0, 0, 0) == "Failed"


def test_a_byte_order_mark_before_the_header_is_ignored():
    table = [["\ufeffdata_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit"],
             ["AMDE", "2025", "12", "1010", "1", "0"]]
    assert list(M.split_table(table)) == [("AMDE", 2025, 12)]


def test_loaded_rows_are_carried_forward_and_never_loaded_again():
    previous = [{"entity": "AMDE", "fiscal_year": 2025, "fiscal_period": 12, "loaded": "TBS-1"},
                {"entity": "AMUS", "fiscal_year": 2025, "fiscal_period": 12, "load_error": "boom"}]
    fresh = [
        {"entity": "AMDE", "fiscal_year": 2025, "fiscal_period": 12, "ok": False,
         "errors": ["TBS-1 is already submitted"], "existing": "TBS-1"},
        {"entity": "AMUS", "fiscal_year": 2025, "fiscal_period": 12, "ok": True, "errors": [], "existing": None},
        # submitted by someone else in between: a real problem, not ours
        {"entity": "AMHQ", "fiscal_year": 2025, "fiscal_period": 12, "ok": False,
         "errors": ["TBS-9 is already submitted"], "existing": "TBS-9"},
    ]
    out = M.merge_loaded(fresh, previous)
    assert out[0]["loaded"] == "TBS-1" and out[0]["ok"] and out[0]["errors"] == []
    assert "loaded" not in out[1] and out[1]["ok"]
    assert "loaded" not in out[2] and not out[2]["ok"]


def test_the_report_names_the_existing_submission():
    assert _check(existing="TBS-7")["existing"] == "TBS-7"


def test_a_row_loaded_by_a_stopped_run_is_recognised_by_its_file():
    fresh = [{"entity": "AMDE", "fiscal_year": 2025, "fiscal_period": 12, "ok": False, "errors": ["already"],
              "existing": "TBS-5", "existing_file": "/private/files/TBU-00009-AMDE-2025-P12.csv"}]
    assert M.merge_loaded(fresh, [], "TBU-00009")[0]["loaded"] == "TBS-5"
    # another upload's file is not ours
    assert "loaded" not in M.merge_loaded(fresh, [], "TBU-00010")[0]


def test_a_loaded_row_since_cancelled_is_a_problem_not_a_reload():
    previous = [{"entity": "AMDE", "fiscal_year": 2025, "fiscal_period": 12, "loaded": "TBS-1"}]
    fresh = [{"entity": "AMDE", "fiscal_year": 2025, "fiscal_period": 12, "ok": True, "errors": [], "existing": None}]
    out = M.merge_loaded(fresh, previous, "TBU-00001")[0]
    assert not out["ok"] and "since been cancelled" in out["errors"][0] and "loaded" not in out
