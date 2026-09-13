"""epm_staging.fiscal_periods (konsol#189): the declared fiscal periods, one
row per Fiscal Year Period with the effective status. konsolidat reads it for
period dates; identical to konsolidat's clickhouse/init-db.sql, keep them
identical.

This reads clickhouse.py as source (AST), without importing frappe — see
test_main_account.py's test_staging_ddl_character_for_character for the same
approach.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")

#: konsolidat's clickhouse/init-db.sql line, as pinned (konsol#189).
INIT_DB_LINE = (
    "CREATE TABLE IF NOT EXISTS epm_staging.fiscal_periods "
    "(fiscal_year UInt16, fiscal_period UInt8, period_code String, "
    "period_label String, period_type String, start_date Date, end_date Date, "
    "quarter String, status String) "
    "ENGINE = MergeTree ORDER BY (fiscal_year, fiscal_period);"
)


def _literal(name):
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == name)


def _ddl():
    return {k: _literal(k) for k in ("_REFERENCE_TABLE_DDL", "_ADDED_COLUMNS", "_RETIRED_TABLES")}


def test_fiscal_periods_ddl_matches_init_db():
    body = _ddl()["_REFERENCE_TABLE_DDL"]["epm_staging.fiscal_periods"]
    assert f"CREATE TABLE IF NOT EXISTS epm_staging.fiscal_periods {body};" == INIT_DB_LINE


def test_new_table_ships_complete_not_via_added_columns():
    consts = _ddl()
    assert "epm_staging.fiscal_periods" not in consts["_ADDED_COLUMNS"]
    assert "epm_staging.fiscal_periods" not in consts["_RETIRED_TABLES"]


def test_reference_table_ddl_assigned_exactly_once():
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", None) == "_REFERENCE_TABLE_DDL" for t in n.targets)]
    assert len(assignments) == 1
