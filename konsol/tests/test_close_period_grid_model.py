"""Period grid model, part 1, pure: konsol/close/period_grid_model.py (konsol#305 E202a).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import datetime
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "close", "period_grid_model.py")
_spec = importlib.util.spec_from_file_location("period_grid_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _src(path):
    with open(path) as fh:
        return fh.read()


START = datetime.date(2025, 9, 1)


# --- grid_scope ------------------------------------------------------------

def test_grid_scope_in_scope_active_leaf():
    entities = [{"name": "ZZA", "status": "Active"}]
    ownership_rows = [
        {"data_area_id": "ZZA", "effective_date": datetime.date(2020, 1, 1), "end_date": None},
    ]
    result = M.grid_scope(entities, ownership_rows, START, set())
    assert result["in_scope"] == {"ZZA"}
    assert result["unowned"] == set()


def test_grid_scope_dormant_covered_leaf_is_neither_in_scope_nor_unowned():
    """E2-3: covered and Dormant, with a submitted TB — in neither set."""
    entities = [{"name": "ZZD", "status": "Dormant"}]
    ownership_rows = [
        {"data_area_id": "ZZD", "effective_date": datetime.date(2020, 1, 1), "end_date": None},
    ]
    result = M.grid_scope(entities, ownership_rows, START, {"ZZD"})
    assert "ZZD" not in result["in_scope"]
    assert "ZZD" not in result["unowned"]


def test_grid_scope_uncovered_entity_with_tb_is_unowned():
    entities = [{"name": "ZZX", "status": "Active"}]
    result = M.grid_scope(entities, [], START, {"ZZX"})
    assert result["unowned"] == {"ZZX"}
    assert "ZZX" not in result["in_scope"]


def test_grid_scope_uncovered_entity_with_no_tb_is_in_neither_set():
    entities = [{"name": "ZZY", "status": "Active"}]
    result = M.grid_scope(entities, [], START, set())
    assert "ZZY" not in result["in_scope"]
    assert "ZZY" not in result["unowned"]


def test_grid_scope_ownership_ended_the_day_before_the_start_is_uncovered():
    entities = [{"name": "ZZE", "status": "Active"}]
    ownership_rows = [
        {"data_area_id": "ZZE", "effective_date": datetime.date(2020, 1, 1),
         "end_date": datetime.date(2025, 8, 31)},
    ]
    result = M.grid_scope(entities, ownership_rows, START, {"ZZE"})
    assert "ZZE" not in result["in_scope"]
    assert result["unowned"] == {"ZZE"}


def test_grid_scope_covering_groups_rows_by_entity():
    entities = [{"name": "ZZA", "status": "Active"}]
    covering_row = {"data_area_id": "ZZA", "effective_date": datetime.date(2020, 1, 1),
                     "end_date": None, "consolidation_group": "G1"}
    not_covering_row = {"data_area_id": "ZZB", "effective_date": datetime.date(2025, 9, 2),
                         "end_date": None, "consolidation_group": "G2"}
    result = M.grid_scope(entities, [covering_row, not_covering_row], START, set())
    assert result["covering"] == {"ZZA": [covering_row]}


# --- ownership_cell ----------------------------------------------------------

def test_ownership_cell_empty_is_blocking():
    cell = M.ownership_cell([], "P09")
    assert cell == {"tone": M.BLOCKING, "label": "None for P09"}


def test_ownership_cell_single_row():
    row = {"consolidation_group": "G1", "consolidation_method": "full", "ownership_pct": 100.0}
    cell = M.ownership_cell([row], "P09")
    assert cell == {"tone": M.NONE, "label": "Full · 100%"}


def test_ownership_cell_pct_without_trailing_zeros():
    row = {"consolidation_group": "G1", "consolidation_method": "equity", "ownership_pct": 62.5}
    cell = M.ownership_cell([row], "P09")
    assert cell["label"] == "Equity · 62.5%"
    row80 = {"consolidation_group": "G1", "consolidation_method": "full", "ownership_pct": 80.0}
    assert M.ownership_cell([row80], "P09")["label"] == "Full · 80%"


def test_ownership_cell_multi_group_joined_and_sorted():
    rows = [
        {"consolidation_group": "Zeta", "consolidation_method": "proportional", "ownership_pct": 40.0},
        {"consolidation_group": "Alpha", "consolidation_method": "full", "ownership_pct": 100.0},
    ]
    cell = M.ownership_cell(rows, "P09")
    assert cell == {"tone": M.NONE, "label": "Alpha: Full · 100%; Zeta: Proportional · 40%"}


def test_ownership_cell_unknown_method_raises_value_error():
    row = {"consolidation_group": "G1", "consolidation_method": "bogus", "ownership_pct": 50.0}
    try:
        M.ownership_cell([row], "P09")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- tb_cell / tb_status: the 7 statuses and tones ---------------------------

STATUS_TONES = [
    (M.RECEIVED, M.OK),
    (M.EXCEPTION_DECLARED, M.NONE),
    (M.NOT_EXPECTED, M.NONE),
    (M.MISSING, M.BLOCKING),
    (M.FREQUENCY_NOT_DECLARED, M.BLOCKING),
    (M.QUARTER_NOT_DECLARED, M.BLOCKING),
    (M.NOT_CONSOLIDATED, M.BLOCKING),
]


def test_tb_cell_each_status_and_tone():
    assert len(STATUS_TONES) == 7
    for status, tone in STATUS_TONES:
        cell = M.tb_cell(status, None)
        assert cell["tone"] == tone, status
        assert cell["label"] == status, status


def test_tb_cell_received_on_behalf_label():
    tb = {"owner": "alice@example.com", "uploaded_on_behalf": "Yes"}
    cell = M.tb_cell(M.RECEIVED, tb)
    assert cell == {"tone": M.OK, "label": "Received · on behalf by alice@example.com"}


def test_tb_cell_received_not_on_behalf_plain_label():
    tb = {"owner": "alice@example.com", "uploaded_on_behalf": "No"}
    cell = M.tb_cell(M.RECEIVED, tb)
    assert cell["label"] == M.RECEIVED


def test_tb_cell_unknown_status_raises_value_error():
    try:
        M.tb_cell("Bogus", None)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_tb_status_precedence_matches_tb_read_api():
    expected = {"frequency_undeclared": ["ZZF"], "not_expected": ["ZZN"], "gaps": [
        {"code": "quarter_undeclared", "entities": ["ZZQ"]},
    ]}
    tbs = {"ZZR": {"name": "TB-1"}}
    exceptions = {"ZZE": {"name": "EX-1"}}
    unowned = {"ZZU"}

    assert M.tb_status("ZZU", tbs, exceptions, expected, unowned) == M.NOT_CONSOLIDATED
    assert M.tb_status("ZZR", tbs, exceptions, expected, unowned) == M.RECEIVED
    assert M.tb_status("ZZE", tbs, exceptions, expected, unowned) == M.EXCEPTION_DECLARED
    assert M.tb_status("ZZF", tbs, exceptions, expected, unowned) == M.FREQUENCY_NOT_DECLARED
    assert M.tb_status("ZZQ", tbs, exceptions, expected, unowned) == M.QUARTER_NOT_DECLARED
    assert M.tb_status("ZZN", tbs, exceptions, expected, unowned) == M.NOT_EXPECTED
    assert M.tb_status("ZZM", tbs, exceptions, expected, unowned) == M.MISSING


def test_tb_status_unowned_overrides_everything_else():
    """"An entity in unowned gets NOT_CONSOLIDATED whatever else holds" (facts)."""
    expected = {"frequency_undeclared": [], "not_expected": [], "gaps": []}
    tbs = {"ZZU": {"name": "TB-1"}}
    exceptions = {}
    unowned = {"ZZU"}
    assert M.tb_status("ZZU", tbs, exceptions, expected, unowned) == M.NOT_CONSOLIDATED


# --- drift guard: tb_read_api.py's six status constants -----------------------

_TB_READ_API_STATUS_NAMES = (
    "RECEIVED", "EXCEPTION_DECLARED", "NOT_EXPECTED", "MISSING",
    "FREQUENCY_NOT_DECLARED", "QUARTER_NOT_DECLARED",
)


def _string_constants(path, names):
    tree = ast.parse(_src(path))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in names \
                and isinstance(node.value, ast.Constant):
            found[node.targets[0].id] = node.value.value
    return found


def test_drift_guard_status_constants_match_tb_read_api():
    tb_read_api_path = os.path.join(APP_DIR, "close", "tb_read_api.py")
    tb_read_api_constants = _string_constants(tb_read_api_path, _TB_READ_API_STATUS_NAMES)
    assert set(tb_read_api_constants) == set(_TB_READ_API_STATUS_NAMES), \
        "tb_read_api.py no longer defines all six status constants at module level"
    for name, value in tb_read_api_constants.items():
        assert getattr(M, name) == value, name


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
