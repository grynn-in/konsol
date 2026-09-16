"""TDD tests for konsol.clickhouse — shared ClickHouse write helper."""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")


def test_clickhouse_module_exists():
    """clickhouse.py must exist."""
    assert os.path.exists(CH_PATH)


def test_clickhouse_has_get_connection():
    """Must expose get_connection() that reads EPM Settings."""
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "get_connection" in func_names


def test_clickhouse_has_execute():
    """Must expose execute(sql, params) for raw HTTP queries."""
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "execute" in func_names


def test_clickhouse_has_sync_table():
    """Must expose sync_table(table, columns, rows) — TRUNCATE + INSERT."""
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "sync_table" in func_names


def test_clickhouse_has_sync_doctype():
    """Must expose sync_doctype(doctype, table, field_map) — fetches docs, calls sync_table."""
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "sync_doctype" in func_names


def test_clickhouse_sync_table_truncates():
    """sync_table must TRUNCATE before INSERT."""
    with open(CH_PATH) as f:
        content = f.read()
    assert "TRUNCATE" in content.upper()
    assert "INSERT" in content.upper()


def test_clickhouse_uses_epm_settings():
    """Must read connection from EPM Settings."""
    with open(CH_PATH) as f:
        content = f.read()
    assert "EPM Settings" in content


# --- a forced sync fails loudly (konsol#194) ---

def _load_clickhouse():
    """clickhouse.py against a stub frappe (pattern from test_clickhouse_execute.py).

    ``requests`` is imported here, not at module scope: clickhouse.py needs it,
    but the source-reading tests above do not, and a host without it should
    skip only these."""
    import requests  # noqa: F401 — clickhouse.py imports it at module level

    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(
        in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe._logger = types.SimpleNamespace(
        warning=lambda *a, **k: None, error=lambda *a, **k: None,
        info=lambda *a, **k: None)
    frappe.logger = lambda: frappe._logger
    frappe.publish_realtime = lambda *a, **k: None

    model = types.ModuleType("frappe.model")
    base_document = types.ModuleType("frappe.model.base_document")
    base_document.get_controller = lambda dt: None
    model.base_document = base_document
    frappe.model = model

    sys.modules.update({"frappe": frappe, "frappe.model": model,
                        "frappe.model.base_document": base_document})
    spec = importlib.util.spec_from_file_location(
        "konsol_clickhouse_under_test_force", CH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Resp:
    """Just enough response for the HTTP branch's ``e.response.status_code``."""
    status_code = 500
    text = "Code: 60. DB::Exception: Table epm_staging.zz_rows does not exist"


_TABLE = "epm_staging.zz_rows"


def _errors(module):
    """The exception classes sync_table's three failure branches catch."""
    exc = module.requests.exceptions
    return {
        "connection_refused": exc.ConnectionError("connection refused"),
        "timeout": exc.Timeout("read timed out"),
        "http_error": exc.HTTPError("500 from ClickHouse", response=_Resp()),
    }


def _sync(module, error, force):
    """sync_table with every execute() raising ``error``.

    Returns (raised exception or None, return value or None)."""
    def boom(sql, params=None):
        raise error

    module.execute = boom
    try:
        return None, module.sync_table(_TABLE, ["a"], [("ZZ01",)], force=force)
    except BaseException as exc:  # noqa: BLE001 — which one is the assertion
        return exc, None


def test_a_forced_sync_raises_instead_of_going_quiet():
    """konsol#194: sync_table logged, recorded the failure and returned None even
    with force=True, so a manual ``bench execute`` of reconcile_all reported
    nothing wrong while the table stayed empty. A forced sync must raise, so
    reconcile_all's per-doctype try sees it and a manual run fails loudly.
    The failure is still recorded for check_health()."""
    module = _load_clickhouse()
    for error_type, error in _errors(module).items():
        module._sync_failures.clear()
        raised, returned = _sync(module, error, force=True)
        assert raised is error, (
            f"a forced sync must re-raise the {error_type} failure, not return "
            f"{returned!r}")
        recorded = module._sync_failures.get(_TABLE)
        assert recorded and recorded["error_type"] == error_type, (
            f"the {error_type} failure must still be recorded: {recorded!r}")


def test_an_unforced_sync_stays_best_effort():
    """A document save must not fail because ClickHouse is down: the unforced
    path keeps logging, recording and returning None."""
    module = _load_clickhouse()
    for error_type, error in _errors(module).items():
        module._sync_failures.clear()
        raised, returned = _sync(module, error, force=False)
        assert raised is None, f"an unforced sync must not raise: {raised!r}"
        assert returned is None, error_type
        recorded = module._sync_failures.get(_TABLE)
        assert recorded and recorded["error_type"] == error_type, repr(recorded)
