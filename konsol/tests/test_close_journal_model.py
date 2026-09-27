"""Consolidation Journal rules, pure: konsol/close/journal_model.py
(konsol#305 J01; #292 "What validate() gains").

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "journal_model.py")
_spec = importlib.util.spec_from_file_location("close_journal_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _line(idx, debit=0, credit=0):
    return {"idx": idx, "main_account": "1000", "debit_amount": debit, "credit_amount": credit}


def test_a_line_with_both_amounts_is_refused_naming_the_line():
    lines = [_line(1, debit=100), _line(2, debit=50, credit=50)]
    problems = M.line_problems(lines)
    assert problems
    assert any("2" in p for p in problems), problems


def test_a_line_with_neither_amount_is_refused():
    lines = [_line(1, debit=100), _line(2)]
    problems = M.line_problems(lines)
    assert problems
    assert any("2" in p for p in problems), problems


def test_a_negative_amount_is_refused():
    lines = [_line(1, debit=100), _line(2, debit=-50)]
    problems = M.line_problems(lines)
    assert problems
    assert any("2" in p for p in problems), problems


def test_a_single_line_is_refused():
    lines = [_line(1, debit=100)]
    problems = M.line_problems(lines)
    assert problems


def test_a_clean_two_line_journal_has_no_line_problems():
    lines = [_line(1, debit=100), _line(2, credit=100)]
    assert M.line_problems(lines) == []


def test_totals_and_balance_problem_name_both_totals():
    lines = [_line(1, debit=100.00), _line(2, credit=99.99)]
    total_debit, total_credit = M.totals(lines)
    problem = M.balance_problem(total_debit, total_credit)
    assert problem is not None
    assert "100" in problem
    assert "99.99" in problem


def test_fractional_cents_balance_exactly():
    """0.1 + 0.2 must balance against 0.3 — a plain float sum would not."""
    lines = [_line(1, debit=0.10), _line(2, debit=0.20), _line(3, credit=0.30)]
    total_debit, total_credit = M.totals(lines)
    assert M.balance_problem(total_debit, total_credit) is None


def test_a_clean_journal_has_no_problems_at_all():
    lines = [_line(1, debit=100), _line(2, credit=100)]
    problems = list(M.line_problems(lines))
    total_debit, total_credit = M.totals(lines)
    bp = M.balance_problem(total_debit, total_credit)
    if bp:
        problems.append(bp)
    assert problems == []


def _period_row(fiscal_year, fiscal_period, period_type="Regular", status="Open"):
    """A fiscal_calendar.fiscal_period_rows() row, minimal but with every key
    reversal_problem might read."""
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


#: The journal's own period is FY2024 P12.
_OWN = (2024, 12)
_ROWS = [
    _period_row(2024, 11, "Regular", "Open"),      # an earlier period
    _period_row(2024, 12, "Regular", "Open"),       # the journal's own period
    _period_row(2024, 13, "Closing", "Open"),       # a Closing period
    _period_row(2025, 1, "Regular", "Open"),        # next year's P1: Regular + Open
    _period_row(2025, 2, "Regular", "Closed"),      # a Closed target
    _period_row(2025, 3, "Regular", "Locked"),       # a Locked target
]


def test_reversal_pair_problem_both_zero_is_none():
    assert M.reversal_pair_problem(0, 0) is None


def test_reversal_pair_problem_year_only_is_refused():
    assert M.reversal_pair_problem(2025, 0) is not None


def test_reversal_pair_problem_period_only_is_refused():
    assert M.reversal_pair_problem(0, 1) is not None


def test_reversal_problem_both_zero_is_none():
    assert M.reversal_problem(*_OWN, 0, 0, _ROWS) is None


def test_reversal_problem_an_undeclared_period_is_refused():
    problem = M.reversal_problem(*_OWN, 2099, 1, _ROWS)
    assert problem is not None and "declared" in problem


def test_reversal_problem_a_closing_period_is_refused():
    problem = M.reversal_problem(*_OWN, 2024, 13, _ROWS)
    assert problem is not None and "Closing" in problem


def test_reversal_problem_the_journals_own_period_is_refused():
    problem = M.reversal_problem(*_OWN, *_OWN, _ROWS)
    assert problem is not None and "after" in problem


def test_reversal_problem_an_earlier_period_is_refused():
    problem = M.reversal_problem(*_OWN, 2024, 11, _ROWS)
    assert problem is not None and "after" in problem


def test_reversal_problem_a_closed_target_is_refused():
    problem = M.reversal_problem(*_OWN, 2025, 2, _ROWS)
    assert problem is not None and "Closed" in problem


def test_reversal_problem_a_locked_target_is_refused():
    problem = M.reversal_problem(*_OWN, 2025, 3, _ROWS)
    assert problem is not None and "Locked" in problem


def test_reversal_problem_next_years_open_regular_period_is_none():
    assert M.reversal_problem(*_OWN, 2025, 1, _ROWS) is None


def test_module_imports_no_frappe():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.split(".")[0] in ("frappe", "konsol") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert (node.module or "").split(".")[0] not in ("frappe", "konsol")


# --- J05: the rows the journal writes to epm_staging.consolidation_adjustments

#: J05a's DDL order without created_at (the column's DEFAULT now() fills it).
_STAGING_COLUMNS = (
    "consolidation_group", "adjustment_type", "journal_id", "data_area_id",
    "fiscal_year", "fiscal_period", "main_account", "debit_amount",
    "credit_amount", "description", "posted_by", "status", "approved_by",
    "approved_at", "reversal_journal_id", "reverse_fiscal_year",
    "reverse_fiscal_period",
)


def _header(name, description, reverse=(0, 0), approved_at="2026-09-27 10:00:00"):
    return {
        "name": name,
        "consolidation_group": "ZZ_GROUP",
        "adjustment_type": "topside",
        "fiscal_year": 2024,
        "fiscal_period": 12,
        "description": description,
        "owner": f"preparer-{name}@example.com",
        "status": "Approved",
        "approved_by": "approver@example.com",
        "approved_at": approved_at,
        "modified": "2026-09-27 10:00:00",
        "reverse_fiscal_year": reverse[0],
        "reverse_fiscal_period": reverse[1],
    }


def _staging_line(parent, idx, entity, account, debit=0, credit=0, description=None):
    return {
        "parent": parent, "idx": idx, "data_area_id": entity,
        "main_account": account, "debit_amount": debit,
        "credit_amount": credit, "description": description,
    }


def test_staging_columns_are_the_tables_ddl_order_without_created_at():
    assert tuple(M.STAGING_COLUMNS) == _STAGING_COLUMNS


def test_staging_rows_one_tuple_per_line_in_column_order():
    """2 journals x 2 lines on two entities -> 4 tuples. The line's entity and
    description win over the header; the header's reversal pair is carried;
    posted_by is the owner (D2-3); reversal_journal_id is blank."""
    headers = [
        _header("CJ-00001", "Header one", reverse=(2025, 1)),
        _header("CJ-00002", "Header two", approved_at=None),
    ]
    lines = [
        _staging_line("CJ-00001", 1, "E1", "1000", debit=100, description="Line one"),
        _staging_line("CJ-00001", 2, "E2", "2000", credit=100),
        _staging_line("CJ-00002", 1, "E2", "3000", debit=5.5),
        _staging_line("CJ-00002", 2, "E1", "4000", credit=5.5, description="Line four"),
    ]
    rows = M.staging_rows(headers, lines)
    assert len(rows) == 4
    for row in rows:
        assert len(row) == len(_STAGING_COLUMNS)
    as_dicts = [dict(zip(_STAGING_COLUMNS, r)) for r in rows]
    first, second, third, fourth = as_dicts

    assert first == {
        "consolidation_group": "ZZ_GROUP", "adjustment_type": "topside",
        "journal_id": "CJ-00001", "data_area_id": "E1", "fiscal_year": 2024,
        "fiscal_period": 12, "main_account": "1000", "debit_amount": 100,
        "credit_amount": 0, "description": "Line one",
        "posted_by": "preparer-CJ-00001@example.com", "status": "Approved",
        "approved_by": "approver@example.com",
        "approved_at": "2026-09-27 10:00:00", "reversal_journal_id": "",
        "reverse_fiscal_year": 2025, "reverse_fiscal_period": 1,
    }
    # the line's entity wins; a line with no description takes the header's
    assert (second["data_area_id"], second["description"]) == ("E2", "Header one")
    assert (second["reverse_fiscal_year"], second["reverse_fiscal_period"]) == (2025, 1)
    # the second journal: no reversal (0/0), its own entities and owner
    assert (third["journal_id"], third["data_area_id"], third["description"]) == (
        "CJ-00002", "E2", "Header two")
    assert (third["reverse_fiscal_year"], third["reverse_fiscal_period"]) == (0, 0)
    assert third["posted_by"] == "preparer-CJ-00002@example.com"
    assert (fourth["data_area_id"], fourth["description"]) == ("E1", "Line four")
    # an empty approved_at stays None: _sql_value writes DEFAULT for it
    assert third["approved_at"] is None and fourth["approved_at"] is None


def test_staging_rows_skip_lines_whose_journal_is_not_in_the_headers():
    """Only submitted journals are read as headers; a stray line (a draft's)
    must not reach the warehouse without its header."""
    headers = [_header("CJ-00001", "Header one")]
    lines = [
        _staging_line("CJ-00001", 1, "E1", "1000", debit=1),
        _staging_line("CJ-00009", 1, "E1", "1000", debit=1),
    ]
    rows = M.staging_rows(headers, lines)
    assert [r[_STAGING_COLUMNS.index("journal_id")] for r in rows] == ["CJ-00001"]


def test_staging_rows_of_nothing_is_empty():
    assert M.staging_rows([], []) == []


def test_a_renamed_approved_state_still_reaches_the_warehouse_as_approved():
    """Review finding 4 (27 Sep): dbt keeps only status in ('Approved','Reversed').
    A site may rename its workflow states (consolidation_journal._states), and
    resync reads only submitted journals, so the warehouse status is the
    contract's 'Approved', never the site's label."""
    header = _header("CJ-00009", "Renamed")
    header["status"] = "Posted"
    rows = M.staging_rows([header], [
        _staging_line("CJ-00009", 1, "E1", "1000", debit=10),
        _staging_line("CJ-00009", 2, "E2", "2000", credit=10),
    ])
    assert {dict(zip(_STAGING_COLUMNS, r))["status"] for r in rows} == {"Approved"}
