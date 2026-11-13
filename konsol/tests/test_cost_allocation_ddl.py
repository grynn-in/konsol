"""konsol#269 K1: the cost allocation warehouse shape, pinned verbatim.

The six epm_staging tables are created by konsol's _REFERENCE_TABLE_DDL on
every migrate and by konsolidat's clickhouse/init-db.sql on a fresh volume.
The bodies below are the W1 contract (tasks-cost-allocation.md); the test
compares them whitespace-normalised against clickhouse.py.

The six names must never be retired: a name in _RETIRED_TABLES,
_RETIRED_DATABASES or the allocation retire patch would be dropped on the next
migrate. The old `allocation_*` names are retired; `cost_allocation_*` is new.
"""
import ast
import os

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
        "carried_forward UInt8, driver_value Decimal(38,9)) "
        "ENGINE = MergeTree ORDER BY (parent, idx)"
    ),
    "epm_staging.cost_allocation_coefficients": (
        "(run String, origin_member String, step UInt16, sender_member String, "
        "receiver_member String, driver String, pool_coefficient Decimal(38,12), "
        "coefficient Decimal(38,12)) "
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


def test_reference_ddl_carries_the_six_w1_bodies():
    ddl = _literal("_REFERENCE_TABLE_DDL")
    for table, body in BODIES.items():
        assert table in ddl, table
        assert _normalise(ddl[table]) == _normalise(body), (table, ddl[table])


def test_the_six_names_are_not_retired():
    retired_tables = set(_literal("_RETIRED_TABLES"))
    retired_databases = set(_literal("_RETIRED_DATABASES"))
    assert os.path.exists(RETIRE_PATCH_PATH), RETIRE_PATCH_PATH
    with open(RETIRE_PATCH_PATH) as f:
        patch_text = f.read()
    for table in BODIES:
        assert table not in retired_tables, table
        assert table.split(".", 1)[0] not in retired_databases, table
        assert table not in patch_text, (table, "retire_allocation.py names it")
