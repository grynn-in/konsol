"""Create ``epm_gold.budget_monthly_input`` — the budget write-back table.

Nothing created this table. Three components each assumed another one did:

* ``BudgetSheet._sync_to_clickhouse`` **writes** to it via ``sync_rows``, which
  issues DELETE + INSERT and therefore requires the table to already exist.
* dbt **reads** it as a declared *source* (``_staging__sources.yml``), and dbt
  never creates sources by design.
* It is not a ``Dataset``, so ``schema_apply._apply_fact_tables`` — which does
  ``CREATE TABLE IF NOT EXISTS`` for every Published ``generates_source`` fact —
  never sees it.

On a fresh deployment the result is that ``gold_spread_budget`` fails with
``UNKNOWN_TABLE`` and every model downstream of it is skipped, which on the
demo deployment meant 139 skipped models and no ``gold_trial_balance`` at all
— so every ``=K.EPM()`` formula in Excel was dead. The failure was silent
because ``install._bootstrap_budget_fixtures`` swallowed it (fixed separately
in this change).

The dimension columns are derived from ``budget_dimension_names()`` rather than
hard-coded, so this stays in step with ``_sync_to_clickhouse``, which builds its
INSERT column list the same way.

Idempotent: ``CREATE TABLE IF NOT EXISTS``. Existing tables are left untouched —
this patch does not attempt to add columns to a table that already exists, so a
later change to the ``in_budget`` dimension set still needs its own migration.
"""
import frappe

from konsol.clickhouse import execute as ch_execute

TABLE = "epm_gold.budget_monthly_input"

# Grain + payload columns written by BudgetSheet._sync_to_clickhouse. The
# ORDER BY matches the sync_rows delete key (scenario, entity, fy, layer) so a
# sheet re-sync rewrites a contiguous range.
_BASE_COLUMNS = [
    "scenario_id String",
    "data_area_id String",
    "fiscal_year UInt16",
    "main_account String",
    "fiscal_period UInt8",
    "amount Float64",
    "layer String",
    "updated_at DateTime DEFAULT now()",
]


def _ddl(dim_names):
    dim_cols = [f"{name} String" for name in dim_names]
    cols = _BASE_COLUMNS[:4] + dim_cols + _BASE_COLUMNS[4:]
    return (
        f"CREATE TABLE IF NOT EXISTS {TABLE} ({', '.join(cols)}) "
        "ENGINE = MergeTree "
        "ORDER BY (scenario_id, data_area_id, fiscal_year, layer, "
        "main_account, fiscal_period)"
    )


def execute():
    from konsol.epm.budget_grain import budget_dimension_names

    try:
        dim_names = budget_dimension_names()
    except Exception:
        # Dimension registry not migrated yet on a first install; fall back to
        # no dimension columns rather than aborting the whole migrate. The
        # table is still usable for the undimensioned grain.
        dim_names = []

    ch_execute(_ddl(dim_names))
    frappe.logger().info(f"ensured ClickHouse table {TABLE}")
