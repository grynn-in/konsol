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


# --- A01: statement_effect — the journal's change per statement heading

def _acct(heading, heading_name, section):
    return {"heading": heading, "heading_name": heading_name, "statement_section": section}


def _se_line(main_account, debit=0, credit=0):
    return {"main_account": main_account, "debit_amount": debit, "credit_amount": credit}


def test_statement_effect_the_wireframes_case():
    lines = [
        _se_line("6100", debit=18500),
        _se_line("2310", credit=18500),
    ]
    accounts = {
        "6100": _acct("6000", "Operating expenses", "Profit and Loss"),
        "2310": _acct("2300", "Current liabilities", "Balance Sheet"),
    }
    result = M.statement_effect(lines, accounts)
    assert result["headings"] == [
        {"section": "Profit and Loss", "heading": "6000",
         "heading_name": "Operating expenses", "net_debit": 18500.0},
        {"section": "Balance Sheet", "heading": "2300",
         "heading_name": "Current liabilities", "net_debit": -18500.0},
    ]
    assert result["sections"] == [
        {"section": "Profit and Loss", "net_debit": 18500.0},
        {"section": "Balance Sheet", "net_debit": -18500.0},
    ]
    assert result["no_heading"] == 0


def test_statement_effect_a_reclass_inside_one_heading_nets_to_zero_but_is_not_dropped():
    lines = [
        _se_line("6100", debit=100),
        _se_line("6150", credit=100),
    ]
    accounts = {
        "6100": _acct("6000", "Operating expenses", "Profit and Loss"),
        "6150": _acct("6000", "Operating expenses", "Profit and Loss"),
    }
    result = M.statement_effect(lines, accounts)
    assert result["headings"] == [
        {"section": "Profit and Loss", "heading": "6000",
         "heading_name": "Operating expenses", "net_debit": 0.0},
    ]


def test_statement_effect_never_drops_heading_none_heading_blank_or_account_absent():
    lines = [
        _se_line("1000", debit=10),   # heading is None
        _se_line("1100", debit=20),   # heading is ""
        _se_line("1200", debit=30),   # absent from accounts entirely
    ]
    accounts = {
        "1000": _acct(None, None, None),
        "1100": _acct("", "", ""),
    }
    result = M.statement_effect(lines, accounts)
    assert result["headings"] == [
        {"section": None, "heading": None, "heading_name": None, "net_debit": 60.0},
    ]
    assert result["sections"] == [{"section": None, "net_debit": 60.0}]
    assert result["no_heading"] == 3


def test_statement_effect_two_accounts_under_one_heading_are_summed():
    lines = [
        _se_line("6100", debit=100),
        _se_line("6110", debit=50),
    ]
    accounts = {
        "6100": _acct("6000", "Operating expenses", "Profit and Loss"),
        "6110": _acct("6000", "Operating expenses", "Profit and Loss"),
    }
    result = M.statement_effect(lines, accounts)
    assert len(result["headings"]) == 1
    assert result["headings"][0]["net_debit"] == 150.0


def test_statement_effect_fractional_cents_net_to_zero_exactly():
    """0.1 + 0.2 must balance against 0.3 — a plain float sum would not."""
    lines = [
        _se_line("6100", debit=0.10),
        _se_line("6110", debit=0.20),
        _se_line("6120", credit=0.30),
    ]
    accounts = {
        "6100": _acct("6000", "Operating expenses", "Profit and Loss"),
        "6110": _acct("6000", "Operating expenses", "Profit and Loss"),
        "6120": _acct("6000", "Operating expenses", "Profit and Loss"),
    }
    result = M.statement_effect(lines, accounts)
    assert result["headings"][0]["net_debit"] == 0.0


def test_statement_effect_ordering_sections_then_heading_name_then_heading_none_last():
    lines = [
        _se_line("2310", credit=5),     # Balance Sheet, Current liabilities
        _se_line("9999", debit=1),      # absent -> no heading, no section
        _se_line("6200", credit=4),     # Profit and Loss, Revenue
        _se_line("6100", debit=10),     # Profit and Loss, Operating expenses
    ]
    accounts = {
        "2310": _acct("2300", "Current liabilities", "Balance Sheet"),
        "6200": _acct("6500", "Revenue", "Profit and Loss"),
        "6100": _acct("6000", "Operating expenses", "Profit and Loss"),
    }
    result = M.statement_effect(lines, accounts)
    assert [(h["section"], h["heading"]) for h in result["headings"]] == [
        ("Profit and Loss", "6000"),
        ("Profit and Loss", "6500"),
        ("Balance Sheet", "2300"),
        (None, None),
    ]
    assert result["sections"] == [
        {"section": "Profit and Loss", "net_debit": 6.0},
        {"section": "Balance Sheet", "net_debit": -5.0},
        {"section": None, "net_debit": 1.0},
    ]
    assert result["no_heading"] == 1


# --- A02: clean_lines, reversal_choices, duration_label

def test_clean_lines_forge_extra_keys_give_a_problem_each_but_the_row_is_kept():
    lines = [{
        "main_account": "A", "debit_amount": 1,
        "docstatus": 1, "parent": "X", "name": "Y", "idx": 9,
    }]
    rows, problems = M.clean_lines(lines)
    assert len(problems) == 4
    for forged in ("docstatus", "parent", "name", "idx"):
        assert any(forged in p and "Line 1" in p for p in problems), problems
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == set(M.LINE_KEYS)
    assert "docstatus" not in row and "parent" not in row
    assert "name" not in row and "idx" not in row
    assert row["main_account"] == "A"
    assert row["debit_amount"] == 1


def test_clean_lines_a_non_list_item_is_a_problem_not_an_exception():
    rows, problems = M.clean_lines(["not a dict", None, {"main_account": "A"}])
    assert len(problems) == 2
    assert len(rows) == 1
    assert rows[0]["main_account"] == "A"


def test_clean_lines_lines_itself_not_a_list_is_a_problem():
    rows, problems = M.clean_lines("not a list")
    assert rows == []
    assert len(problems) == 1


def test_clean_lines_clean_input_keeps_every_key_missing_as_none_in_order():
    lines = [{
        "data_area_id": "E1", "main_account": "1000",
        "debit_amount": 100, "credit_amount": 0, "description": "d",
    }]
    rows, problems = M.clean_lines(lines)
    assert problems == []
    assert rows == [{
        "data_area_id": "E1", "main_account": "1000",
        "debit_amount": 100, "credit_amount": 0, "description": "d",
    }]
    assert tuple(rows[0]) == M.LINE_KEYS


def test_clean_lines_missing_keys_become_none():
    rows, problems = M.clean_lines([{"main_account": "1000"}])
    assert problems == []
    assert rows == [{
        "data_area_id": None, "main_account": "1000",
        "debit_amount": None, "credit_amount": None, "description": None,
    }]


#: A02's own fixture (the row's stated case): the journal's own period is
#: FY2024 P07. P08 Open Regular and P11 Open Regular qualify; P09 Closed and
#: P10 Open Closing do not, nor does the own period or an earlier one.
_A02_OWN = (2024, 7)
_A02_ROWS = [
    _period_row(2024, 6, "Regular", "Open"),     # earlier than own
    _period_row(2024, 7, "Regular", "Open"),     # the journal's own period
    _period_row(2024, 8, "Regular", "Open"),     # qualifies
    _period_row(2024, 9, "Regular", "Closed"),   # not Open
    _period_row(2024, 10, "Closing", "Open"),    # not Regular
    _period_row(2024, 11, "Regular", "Open"),    # qualifies
]


def test_reversal_choices_lists_only_the_qualifying_periods_in_order():
    choices = M.reversal_choices(*_A02_OWN, _A02_ROWS)
    assert choices == [
        {"fiscal_year": 2024, "fiscal_period": 8, "code": "P8"},
        {"fiscal_year": 2024, "fiscal_period": 11, "code": "P11"},
    ]


def test_reversal_choices_never_offers_the_own_or_an_earlier_period():
    choices = M.reversal_choices(*_A02_OWN, _A02_ROWS)
    offered = {(c["fiscal_year"], c["fiscal_period"]) for c in choices}
    assert (2024, 7) not in offered
    assert (2024, 6) not in offered


def test_duration_label_no_reversal():
    assert M.duration_label(0, 0, _A02_ROWS) == "This period only, no reversal"


def test_duration_label_a_declared_period():
    assert M.duration_label(2024, 11, _A02_ROWS) == "Reverses in P11"


def test_duration_label_an_undeclared_period_is_never_blank():
    label = M.duration_label(2099, 3, _A02_ROWS)
    assert label == "Reverses in FY2099 P03 (not a declared period)"

# -- konsolidat#245 option D: declared dimensions on a journal line --------------------

def test_staging_columns_appends_declared_dimensions_at_the_end():
    """The table already exists, so new columns go LAST — the _ADDED_COLUMNS
    convention (clickhouse.py), the same reason main_account's CH_FIELD_MAP
    keeps is_retained_earnings last."""
    assert M.staging_columns() == tuple(_STAGING_COLUMNS)
    cols = M.staging_columns(("dim_cost_center", "dim_segment"))
    assert cols[:len(_STAGING_COLUMNS)] == tuple(_STAGING_COLUMNS)
    assert cols[len(_STAGING_COLUMNS):] == ("dim_cost_center", "dim_segment")


def test_a_line_carries_its_declared_dimension_values():
    headers = [{"name": "CJ-1", "consolidation_group": "G", "adjustment_type": "Topside",
                "fiscal_year": 2026, "fiscal_period": 3, "description": "h",
                "owner": "a@b.c", "status": "Approved", "approved_by": "a@b.c",
                "approved_at": None, "reverse_fiscal_year": 0, "reverse_fiscal_period": 0}]
    lines = [{"parent": "CJ-1", "idx": 1, "data_area_id": "E1", "main_account": "4000",
              "debit_amount": 10, "credit_amount": 0, "description": "",
              "dim_cost_center": "CC1", "dim_segment": "S1"},
             {"parent": "CJ-1", "idx": 2, "data_area_id": "E1", "main_account": "5000",
              "debit_amount": 0, "credit_amount": 10, "description": ""}]
    declared = ("dim_cost_center", "dim_segment")
    rows = M.staging_rows(headers, lines, declared=declared)
    cols = M.staging_columns(declared)
    as_dicts = [dict(zip(cols, r)) for r in rows]
    assert all(len(r) == len(cols) for r in rows)
    assert (as_dicts[0]["dim_cost_center"], as_dicts[0]["dim_segment"]) == ("CC1", "S1")
    # blank stays valid and is stored as '' — never None, never absent
    assert (as_dicts[1]["dim_cost_center"], as_dicts[1]["dim_segment"]) == ("", "")


def test_a_dimension_the_site_has_not_declared_is_never_written():
    """konsol#247's third clause: data does not create configuration. A stray
    dim_* key on a line is ignored, not promoted into a column."""
    headers = [{"name": "CJ-1", "consolidation_group": "G", "adjustment_type": "Topside",
                "fiscal_year": 2026, "fiscal_period": 3, "description": "h",
                "owner": "a@b.c", "status": "Approved", "approved_by": "", 
                "approved_at": None, "reverse_fiscal_year": 0, "reverse_fiscal_period": 0}]
    lines = [{"parent": "CJ-1", "idx": 1, "data_area_id": "E1", "main_account": "4000",
              "debit_amount": 10, "credit_amount": 0, "description": "",
              "dim_undeclared": "X", "dim_cost_center": "CC1"}]
    rows = M.staging_rows(headers, lines, declared=("dim_cost_center",))
    assert len(rows[0]) == len(_STAGING_COLUMNS) + 1
    assert rows[0][-1] == "CC1"
    assert "X" not in rows[0]


def test_staging_rows_without_declared_dimensions_is_unchanged():
    """A site with no journal dimensions writes exactly what it wrote before."""
    headers = [{"name": "CJ-1", "consolidation_group": "G", "adjustment_type": "Topside",
                "fiscal_year": 2026, "fiscal_period": 3, "description": "h",
                "owner": "a@b.c", "status": "Approved", "approved_by": "",
                "approved_at": None, "reverse_fiscal_year": 0, "reverse_fiscal_period": 0}]
    lines = [{"parent": "CJ-1", "idx": 1, "data_area_id": "E1", "main_account": "4000",
              "debit_amount": 10, "credit_amount": 0, "description": ""}]
    assert len(M.staging_rows(headers, lines)[0]) == len(_STAGING_COLUMNS)


def test_journal_dimension_columns_takes_only_the_declared_that_exist():
    """The Custom Field sync is queued after the commit (konsol#135), so between
    a Dimension publish and that job running, a dimension is Published with
    in_journal set and its field does not exist yet. Selecting it would make
    frappe.get_all raise and break the whole resync, so the window is handled
    rather than risked: declared AND present, in declared order."""
    declared = ["dim_cost_center", "dim_segment", "dim_brand_new"]
    present = {"data_area_id", "main_account", "dim_cost_center", "dim_segment"}
    assert M.journal_dimension_columns(declared, present) == ("dim_cost_center", "dim_segment")


def test_journal_dimension_columns_is_empty_when_nothing_is_declared():
    assert M.journal_dimension_columns([], {"dim_cost_center"}) == ()


def test_journal_dimension_columns_ignores_a_field_that_is_not_declared():
    """An orphan column from an un-ticked dimension stays on the table and
    stays readable, but nothing new is written to it (konsol#255 option A)."""
    assert M.journal_dimension_columns(["dim_a"], {"dim_a", "dim_orphan"}) == ("dim_a",)
