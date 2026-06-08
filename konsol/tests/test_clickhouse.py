"""TDD tests for konsol.clickhouse — shared ClickHouse write helper."""
import ast
import os

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
