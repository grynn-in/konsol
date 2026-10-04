"""Ownership-scope model, pure: konsol/close/scope_model.py (konsol#305 G01).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import datetime
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "scope_model.py")
_spec = importlib.util.spec_from_file_location("scope_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _src(path):
    with open(path) as fh:
        return fh.read()


START = datetime.date(2025, 9, 1)

# (label, row, expected covers(row, START))
CASES = [
    ("open-ended, effective well before",
     {"data_area_id": "ZZA", "effective_date": datetime.date(2020, 1, 1), "end_date": None}, True),
    ("end equal to start",
     {"data_area_id": "ZZA", "effective_date": datetime.date(2020, 1, 1), "end_date": datetime.date(2025, 9, 1)}, True),
    ("end one day before start",
     {"data_area_id": "ZZA", "effective_date": datetime.date(2020, 1, 1), "end_date": datetime.date(2025, 8, 31)}, False),
    ("effective one day after start",
     {"data_area_id": "ZZA", "effective_date": datetime.date(2025, 9, 2), "end_date": None}, False),
    ("effective equal to start",
     {"data_area_id": "ZZA", "effective_date": datetime.date(2025, 9, 1), "end_date": None}, True),
    ("data_area_id blank string ignored",
     {"data_area_id": "", "effective_date": datetime.date(2020, 1, 1), "end_date": None}, False),
    ("data_area_id None ignored",
     {"data_area_id": None, "effective_date": datetime.date(2020, 1, 1), "end_date": None}, False),
    ("effective None never covers",
     {"data_area_id": "ZZA", "effective_date": None, "end_date": None}, False),
]


# --- covered -----------------------------------------------------------

def test_covered_cases():
    for label, row, expected in CASES:
        result = M.covered([row], START)
        expected_set = {row["data_area_id"]} if (expected and row.get("data_area_id")) else set()
        assert result == expected_set, label


def test_covered_iso_string_and_datetime_inputs_agree_with_date():
    base = {"data_area_id": "ZZA", "end_date": None}
    as_date = dict(base, effective_date=datetime.date(2020, 1, 1))
    as_datetime = dict(base, effective_date=datetime.datetime(2020, 1, 1, 8, 30))
    as_iso = dict(base, effective_date="2020-01-01")
    for row in (as_date, as_datetime, as_iso):
        assert M.covered([row], START) == {"ZZA"}


def test_covered_end_date_iso_string_and_datetime_inputs_agree_with_date():
    eff = datetime.date(2020, 1, 1)
    as_date = {"data_area_id": "ZZA", "effective_date": eff, "end_date": datetime.date(2025, 8, 31)}
    as_datetime = {"data_area_id": "ZZA", "effective_date": eff, "end_date": datetime.datetime(2025, 8, 31, 23, 0)}
    as_iso = {"data_area_id": "ZZA", "effective_date": eff, "end_date": "2025-08-31"}
    for row in (as_date, as_datetime, as_iso):
        assert M.covered([row], START) == set()


def test_covered_start_date_none_raises_value_error():
    try:
        M.covered([{"data_area_id": "ZZA", "effective_date": datetime.date(2020, 1, 1), "end_date": None}], None)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_covered_result_is_a_set():
    result = M.covered([], START)
    assert isinstance(result, set)


def test_covered_multiple_rows():
    rows = [
        {"data_area_id": "ZZA", "effective_date": datetime.date(2020, 1, 1), "end_date": None},
        {"data_area_id": "ZZB", "effective_date": datetime.date(2025, 9, 2), "end_date": None},
        {"data_area_id": "ZZC", "effective_date": datetime.date(2020, 1, 1), "end_date": datetime.date(2025, 8, 31)},
    ]
    assert M.covered(rows, START) == {"ZZA"}


# --- covers agrees with covered on every case ---------------------------

def test_covers_agrees_with_covered_on_every_case():
    for label, row, expected in CASES:
        assert M.covers(row, START) is expected, label


def test_covers_start_date_none_raises_value_error():
    try:
        M.covers({"data_area_id": "ZZA", "effective_date": datetime.date(2020, 1, 1), "end_date": None}, None)
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- in_scope ------------------------------------------------------------

def test_in_scope_gives_only_the_covered_keys():
    leaves = {"ZZA": "Monthly", "ZZB": "Monthly", "ZZC": "Quarterly"}
    covered_set = {"ZZA", "ZZX"}  # ZZX not a leaf
    assert M.in_scope(leaves, covered_set) == {"ZZA"}


def test_in_scope_covered_code_not_a_leaf_is_not_returned():
    leaves = {"ZZA": "Monthly"}
    covered_set = {"ZZA", "ZZQ"}
    result = M.in_scope(leaves, covered_set)
    assert "ZZQ" not in result
    assert result == {"ZZA"}


# --- uncovered_with_tb -----------------------------------------------------

def test_uncovered_with_tb():
    assert M.uncovered_with_tb({"ZZA", "ZZX"}, {"ZZA", "ZZB"}) == {"ZZX"}


def test_uncovered_with_tb_empty_tb_set_gives_empty_set():
    assert M.uncovered_with_tb(set(), {"ZZA", "ZZB"}) == set()


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
