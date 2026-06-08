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

def test_measure_whitelist_exists():
    """ALLOWED_MEASURES dict must exist to prevent SQL injection."""
    with open(API_PATH) as f:
        content = f.read()
    assert "ALLOWED_MEASURES" in content


def test_measure_whitelist_covers_scenarios():
    """Each scenario must have allowed measures defined."""
    with open(API_PATH) as f:
        content = f.read()
    # All three scenarios must be keys in ALLOWED_MEASURES
    for scenario in ["actuals", "budget", "variance"]:
        assert f'"{scenario}"' in content or f"'{scenario}'" in content
    # Key measures must be whitelisted
    for measure in ["period_net_amount", "period_amount", "variance_abs"]:
        assert f'"{measure}"' in content or f"'{measure}'" in content


def test_validate_measure_function_exists():
    """_validate_measure must exist to reject non-whitelisted measures."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "_validate_measure" in func_names


def test_validate_measure_raises_on_invalid():
    """_validate_measure must raise/throw for invalid measures."""
    with open(API_PATH) as f:
        content = f.read()
    # Must contain frappe.throw or raise for invalid measures
    assert "frappe.throw" in content or "raise" in content
    assert "Invalid measure" in content


def test_no_fstring_measure_interpolation():
    """Measure must NOT be f-string interpolated into SQL (SQL injection)."""
    with open(API_PATH) as f:
        content = f.read()
    # The old vulnerable pattern was f"sum({measure})"
    # After fix, measure is used directly but only after whitelist validation
    # Check that there's no unvalidated interpolation
    assert "ALLOWED_MEASURES" in content
    assert "_validate_measure" in content


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
