"""TDD tests for api.py CH helper refactor."""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")


def test_api_imports_shared_helper():
    """api.py must import from konsol.clickhouse."""
    with open(API_PATH) as f:
        content = f.read()
    assert "konsol.clickhouse" in content


def test_api_clickhouse_settings_delegated_to_shared_module():
    """ClickHouse connection settings come from konsol.clickhouse, not inlined."""
    with open(API_PATH) as f:
        content = f.read()
    assert "from konsol.clickhouse import" in content
    # The shared connection helper is used to fetch settings.
    assert "_get_ch_connection" in content


def test_api_still_has_epm_value():
    """Existing endpoints must still exist."""
    with open(API_PATH) as f:
        content = f.read()
    assert "def epm_value" in content
    assert "def epm_batch" in content
    assert "def health" in content


# --- scenario_id filter tests ---

def test_api_uses_per_fact_scenario_id_flag():
    """Scenario_id support is a per-Fact-Table flag, not a hardcoded table list."""
    with open(API_PATH) as f:
        content = f.read()
    assert "has_scenario_id" in content


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
    # Validation uses a precompiled pattern (_SAFE_SCENARIO_ID), not re.match directly.
    assert "_SAFE_SCENARIO_ID.match" in content
    assert "A-Za-z0-9_" in content


def test_epm_batch_guards_year_per_cell():
    """A non-numeric year must fail only its own row, not 500 the whole batch.

    int(year) must be coerced inside the per-cell try/except (like the period
    guard) so a bad value (e.g. JSON null from a blank Excel cell) records a
    per-cell error and `continue`s, rather than raising in the normalized dict
    where it would propagate and fail every other request in the batch.
    """
    with open(API_PATH) as f:
        content = f.read()
    batch = content.split("def epm_batch")[1].split("\ndef ")[0]
    # Per-cell error path exists.
    assert "Invalid year" in batch
    # year is coerced (once) but NOT re-coerced inside the normalized dict,
    # which would sit outside the try and raise unhandled.
    assert 'int(req.get("year"' in batch
    assert '"year": int(req.get("year"' not in batch
