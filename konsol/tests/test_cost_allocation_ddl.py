"""konsol#269 K1: the cost allocation warehouse shape, pinned verbatim.

The seven epm_staging tables are created by konsol's _REFERENCE_TABLE_DDL on
every migrate and by konsolidat's clickhouse/init-db.sql on a fresh volume.
The bodies below are the W1 contract (tasks-cost-allocation.md); the test
compares them whitespace-normalised against clickhouse.py.

The seven names must never be retired: a name in _RETIRED_TABLES,
_RETIRED_DATABASES or the allocation retire patch would be dropped on the next
migrate. The old `allocation_*` names are retired; `cost_allocation_*` is new.

Phase 2b-1 (task C): the weights carry `source` and `effective_from` after
driver_value (an existing table gets them by _POSITIONED_COLUMNS), and the
Account Set driver's accounts are a seventh table,
cost_allocation_run_driver_accounts. An unset effective_from (a statistic or
account-set row) is written as DEFAULT, which a Date reads as 1970-01-01.
"""
import ast
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")
RETIRE_PATCH_PATH = os.path.join(APP_DIR, "patches", "retire_allocation.py")

#: W1 DDL, verbatim (bodies after the table name).
BODIES = {
    "epm_staging.cost_allocation_runs": (
        "(name String, rule_set String, method String, allocation_dimension String, "
        "receiver_account_mode String, allocation_account String, "
        "amounts_after_approval String, data_area_id String, scenario_id String, "
        "fiscal_year UInt16, fiscal_period UInt8, computed_at DateTime) "
        "ENGINE = MergeTree ORDER BY name"
    ),
    "epm_staging.cost_allocation_run_accounts": (
        "(parent String, idx UInt32, main_account String, declared_account String) "
        "ENGINE = MergeTree ORDER BY (parent, idx)"
    ),
    "epm_staging.cost_allocation_run_weights": (
        "(parent String, idx UInt32, step UInt16, sender_member String, "
        "receiver_member String, driver String, statistic String, value_period UInt8, "
        "carried_forward UInt8, driver_value Decimal(38,9), source String, effective_from Date) "
        "ENGINE = MergeTree ORDER BY (parent, idx)"
    ),
    "epm_staging.cost_allocation_run_driver_accounts": (
        "(parent String, idx UInt32, driver String, main_account String, value_sign String) "
        "ENGINE = MergeTree ORDER BY (parent, idx)"
    ),
    "epm_staging.cost_allocation_coefficients": (
        "(run String, origin_member String, step UInt16, sender_member String, "
        "receiver_member String, driver String, hop_kind String, "
        "pool_coefficient Decimal(38,12), coefficient Decimal(38,12)) "
        "ENGINE = MergeTree ORDER BY (run, origin_member, step, receiver_member)"
    ),
    "epm_staging.cost_allocation_statistics": (
        "(name String, driver String, dimension String, data_area_id String, "
        "scenario_id String, fiscal_year UInt16) "
        "ENGINE = MergeTree ORDER BY name"
    ),
    "epm_staging.cost_allocation_statistic_values": (
        "(parent String, idx UInt32, fiscal_period UInt8, member String, "
        "value Decimal(38,9)) "
        "ENGINE = MergeTree ORDER BY (parent, idx)"
    ),
}


def _normalise(text):
    return " ".join(text.split())


def _literal(name):
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == name):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found in clickhouse.py")


def test_reference_ddl_carries_the_seven_bodies():
    ddl = _literal("_REFERENCE_TABLE_DDL")
    for table, body in BODIES.items():
        assert table in ddl, table
        assert _normalise(ddl[table]) == _normalise(body), (table, ddl[table])


def test_an_existing_coefficients_table_gains_hop_kind_after_driver():
    """An existing stack gets hop_kind by ALTER ... AFTER driver, the same place
    the CREATE puts it (_POSITIONED_COLUMNS, asked from system.columns first)."""
    positioned = _literal("_POSITIONED_COLUMNS")
    assert positioned["epm_staging.cost_allocation_coefficients"] == [
        ("hop_kind", "String", "driver")]


def _columns(body):
    inner = body[body.index("(") + 1:body.index(") ENGINE")]
    return [part.strip().split()[0] for part in re.split(r",(?![^(]*\))", inner)]


def test_an_existing_weights_table_gains_source_and_effective_from_after_driver_value():
    """Phase 2b-1 (task C): the two new weight columns sit after driver_value in
    the CREATE and are ADDed there on an existing table, so a fresh table and an
    upgraded one agree. effective_from is a plain Date: its unset value is the
    column default (1970-01-01), which the sync writes as DEFAULT."""
    positioned = _literal("_POSITIONED_COLUMNS")
    table = "epm_staging.cost_allocation_run_weights"
    assert positioned[table] == [("source", "String", "driver_value"),
                                 ("effective_from", "Date", "source")]
    names = _columns(BODIES[table])
    for column, typ, after in positioned[table]:
        assert names.index(column) == names.index(after) + 1, (column, after)
        assert f"{column} {typ}" in BODIES[table], column
    assert "epm_staging.cost_allocation_run_driver_accounts" not in positioned, \
        "a new table needs no positioned column: its CREATE has the shape"


def test_the_seven_names_are_not_retired():
    retired_tables = set(_literal("_RETIRED_TABLES"))
    retired_databases = set(_literal("_RETIRED_DATABASES"))
    assert os.path.exists(RETIRE_PATCH_PATH), RETIRE_PATCH_PATH
    with open(RETIRE_PATCH_PATH) as f:
        patch_text = f.read()
    for table in BODIES:
        assert table not in retired_tables, table
        assert table.split(".", 1)[0] not in retired_databases, table
        assert table not in patch_text, (table, "retire_allocation.py names it")
