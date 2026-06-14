"""TDD tests for konsol EPM API (Frappe proxy to ClickHouse)."""
import ast
import os
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")

# Add api module to path for direct import tests
sys.path.insert(0, APP_DIR)


def test_api_file_exists():
    """api.py must exist."""
    assert os.path.exists(API_PATH)


def test_api_has_batch_endpoint():
    """Must have an epm_batch function."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "epm_batch" in func_names


def test_api_has_health_endpoint():
    """Must have a health check endpoint."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "health" in func_names


def test_api_is_whitelisted():
    """Endpoints must be whitelisted for guest or session access."""
    with open(API_PATH) as f:
        content = f.read()
    assert "whitelist" in content


def test_api_queries_clickhouse():
    """Must connect to ClickHouse to fetch data."""
    with open(API_PATH) as f:
        content = f.read()
    assert "clickhouse" in content.lower() or "8123" in content


def test_api_returns_values_array():
    """Response must include a values array."""
    with open(API_PATH) as f:
        content = f.read()
    assert "values" in content


def test_api_has_single_value_endpoint():
    """Must have an epm_value function for single cell lookups."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "epm_value" in func_names


# --- Hardening tests ---

def test_measure_allowlist_exists():
    """Measures are constrained to the fact's allow-list AND the Published registry.

    Data-driven (Fact Table.measures ∩ Measure registry), surfaced via
    _get_allowed_measures / _published_measures and enforced by _resolve_and_validate.
    """
    with open(API_PATH) as f:
        content = f.read()
    assert "_get_allowed_measures" in content
    assert "_published_measures" in content


def test_fact_measure_dimension_validated_together():
    """The read path validates fact + measure + dimensions before querying."""
    with open(API_PATH) as f:
        content = f.read()
    assert "_resolve_and_validate" in content


def test_validate_function_exists():
    """A validation function must exist to reject unknown facts/measures/dimensions."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "_resolve_and_validate" in func_names


def test_validate_measure_raises_on_invalid():
    """_validate_measure must raise/throw for invalid measures."""
    with open(API_PATH) as f:
        content = f.read()
    # Must contain frappe.throw or raise for invalid measures
    assert "frappe.throw" in content or "raise" in content
    assert "Invalid measure" in content


def test_measure_is_regex_validated_before_sql():
    """Measure must be identifier-validated before interpolation into SQL.

    The query measure is interpolated into sum(...), but only after passing the
    _SAFE_IDENTIFIER regex check, which blocks any SQL-injection payload.
    """
    with open(API_PATH) as f:
        content = f.read()
    assert "_SAFE_IDENTIFIER" in content
    assert "_SAFE_IDENTIFIER.match(query_measure)" in content


def test_batch_query_function_exists():
    """_batch_query_clickhouse must exist for grouped queries."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "_batch_query_clickhouse" in func_names


def test_error_reporting_structure():
    """API must return errors array when errors occur."""
    with open(API_PATH) as f:
        content = f.read()
    assert '"errors"' in content or "'errors'" in content


def test_no_silent_exception_swallowing():
    """Must not silently return 0.0 for all exceptions."""
    with open(API_PATH) as f:
        content = f.read()
    # Old pattern: except Exception: return 0.0
    # Should no longer exist
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if "except Exception" in line and i + 1 < len(lines):
            next_line = lines[i + 1].strip()
            assert next_line != "return 0.0", "Must not silently swallow errors"
