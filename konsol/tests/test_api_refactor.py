"""TDD tests for api.py CH helper refactor."""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")


def test_api_imports_shared_helper():
    """api.py must import from konsol.clickhouse."""
    with open(API_PATH) as f:
        content = f.read()
    assert "konsol.clickhouse" in content


def test_api_get_clickhouse_settings_delegates():
    """_get_clickhouse_settings must delegate to shared module."""
    with open(API_PATH) as f:
        content = f.read()
    # Should NOT have the old inline implementation
    assert 'frappe.get_single("EPM Settings")' not in content.split("def _get_clickhouse_settings")[1].split("def ")[0]


def test_api_still_has_epm_value():
    """Existing endpoints must still exist."""
    with open(API_PATH) as f:
        content = f.read()
    assert "def epm_value" in content
    assert "def epm_batch" in content
    assert "def health" in content


# --- scenario_id filter tests ---

def test_api_has_tables_with_scenario_id():
    """Must define which tables support scenario_id filtering."""
    with open(API_PATH) as f:
        content = f.read()
    assert "TABLES_WITH_SCENARIO_ID" in content
    assert "gold_spread_budget" in content


def test_epm_value_accepts_scenario_id():
    """epm_value must accept scenario_id parameter."""
    with open(API_PATH) as f:
        content = f.read()
    # Find the epm_value function signature
    sig = content.split("def epm_value")[1].split("):")[0]
    assert "scenario_id" in sig


def test_epm_batch_passes_scenario_id():
    """epm_batch must pass scenario_id to the query engine."""
    with open(API_PATH) as f:
        content = f.read()
    # In the normalization block
    assert 'scenario_id' in content
    assert 'req.get("scenario_id"' in content


def test_batch_query_groups_by_scenario_id():
    """_batch_query_clickhouse must include scenario_id in grouping key."""
    with open(API_PATH) as f:
        content = f.read()
    # The grouping key tuple should reference scenario_id
    batch_fn = content.split("def _batch_query_clickhouse")[1]
    assert 'scenario_id' in batch_fn.split("groups[key]")[0]


def test_scenario_id_clause_is_parameterized():
    """scenario_id WHERE clause must use parameterized query, not string interpolation."""
    with open(API_PATH) as f:
        content = f.read()
    assert "{sid:String}" in content
    assert "param_sid" in content


def test_scenario_id_is_validated():
    """scenario_id must be validated against injection (alphanumeric + underscore only)."""
    with open(API_PATH) as f:
        content = f.read()
    assert "re.match" in content
    assert "A-Za-z0-9_" in content
