"""Point the shipped ``forecast_input`` Dataset at the declared current round
(konsol#348 S6-Q1 b, decided by Deepak Pai, 17 Nov 2026).

A forecast scenario holds several approved rounds per year side by side, and
the flat ``K.EPM(..., "forecast")`` read binds ``scenario_id`` and ``layer``
but never ``cycle``: over ``gold_spread_budget`` it adds the rounds together.
The warehouse view ``gold_forecast_current`` has the fact's columns and only
the declared current round's rows, so the shipped Dataset now reads it.

Datasets are site-owned and seeded create-if-missing (konsol#230), so the new
seed reaches only a fresh site. This patch moves an existing site's row, and
only while it still holds the shipped value: a site that pointed
``forecast_input`` at a table of its own is left alone and named in the
migrate log, with the table it should read. Its reads keep working (the old
table has the same columns); they sum rounds until the site repoints them.
A site without the row gets the new seed from ``install_defaults``.

Idempotent: once moved, the row no longer holds the old value.
"""
import frappe

DATASET = "forecast_input"
OLD_TABLE, OLD_MODEL = "epm_gold.gold_spread_budget", "gold_spread_budget"
NEW_TABLE, NEW_MODEL = "epm_gold.gold_forecast_current", "gold_forecast_current"


def execute():
    row = frappe.db.get_value("Dataset", DATASET, ["clickhouse_table", "dbt_model"], as_dict=True)
    if not row:
        print(f"konsol#348 S6: no Dataset {DATASET} on this site; the seed creates it reading {NEW_TABLE}")
        return
    if row.clickhouse_table == NEW_TABLE:
        return
    if row.clickhouse_table != OLD_TABLE:
        print(f"konsol#348 S6: Dataset {DATASET} reads {row.clickhouse_table}, not the shipped "
              f"{OLD_TABLE}; left as the site set it. A forecast read sums every approved round of a "
              f"year unless that table holds one round: point it at {NEW_TABLE} (the declared current "
              "round) to read one.")
        return
    frappe.db.set_value("Dataset", DATASET, {"clickhouse_table": NEW_TABLE, "dbt_model": NEW_MODEL},
                        update_modified=False)
    print(f"konsol#348 S6: Dataset {DATASET} now reads {NEW_TABLE} (the declared current round), "
          f"was {OLD_TABLE}")
