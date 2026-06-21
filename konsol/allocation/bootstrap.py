"""Post-fixture bootstrap: sync allocation config to ClickHouse.

Fixture import does not run DocType ``on_update`` hooks, so ``after_migrate``
must push Allocation Rule / Driver docs to ClickHouse before governed builds.
"""
import frappe

from konsol.clickhouse import sync_doctype, sync_table

RULE_LEGACY_TABLE = "epm_gold.allocation_rules"
RULE_LEGACY_FIELD_MAP = {
    "allocation_rule_id": "allocation_rule_id",
    "rule_name": "rule_name",
    "step_order": "step_order",
    "source_account": "source_account",
    "source_cost_center": "source_cost_center",
    "driver_type": "driver_type",
    "target_account": "target_account",
    "description": "description",
}
RULE_STAGING_TABLE = "epm_staging.allocation_rules"
RULE_STAGING_FIELD_MAP = {
    "allocation_rule_id": "allocation_rule_id",
    "rule_name": "rule_name",
    "step_order": "step_order",
    "source_account": "source_account",
    "source_cost_center": "source_cost_center",
    "driver_type": "driver_type",
    "target_account": "target_account",
    "description": "description",
    "allocation_method": "allocation_method",
    "driver_formula": "driver_formula",
}

LEGACY_DRIVER_TYPES = ["headcount", "revenue", "sqm"]
LEGACY_DRIVER_COLUMNS = [
    "data_area_id",
    "cost_center",
    "driver_value",
    "fiscal_year",
    "fiscal_period",
]
STAGING_DRIVER_COLUMNS = [
    "driver_type",
    "data_area_id",
    "cost_center",
    "fiscal_year",
    "fiscal_period",
    "driver_value",
]


def sync_allocation_config_to_clickhouse():
    """Best-effort sync of fixture-loaded allocation docs. Never fail migrate."""
    if not frappe.db.table_exists("Allocation Rule"):
        return

    try:
        _sync_allocation_rules()
        _sync_allocation_drivers()
        _sync_allocation_tiers()
    except Exception:
        frappe.logger().warning(
            "allocation config ClickHouse sync skipped after migrate",
            exc_info=True,
        )


def _sync_allocation_rules():
    sync_doctype("Allocation Rule", RULE_LEGACY_TABLE, RULE_LEGACY_FIELD_MAP)
    sync_doctype("Allocation Rule", RULE_STAGING_TABLE, RULE_STAGING_FIELD_MAP)


def _sync_allocation_drivers():
    if not frappe.db.table_exists("Allocation Driver"):
        return

    for dtype in LEGACY_DRIVER_TYPES:
        table = f"epm_gold.allocation_drivers_{dtype}"
        docs = frappe.get_all(
            "Allocation Driver",
            filters={"driver_type": dtype},
            fields=LEGACY_DRIVER_COLUMNS,
            limit_page_length=0,
        )
        rows = [[doc.get(column) for column in LEGACY_DRIVER_COLUMNS] for doc in docs]
        sync_table(table, LEGACY_DRIVER_COLUMNS, rows)

    docs = frappe.get_all(
        "Allocation Driver",
        fields=STAGING_DRIVER_COLUMNS,
        limit_page_length=0,
    )
    rows = [[doc.get(column) for column in STAGING_DRIVER_COLUMNS] for doc in docs]
    sync_table("epm_staging.allocation_drivers", STAGING_DRIVER_COLUMNS, rows)


def _sync_allocation_tiers():
    columns = [
        "allocation_rule_id",
        "tier_order",
        "lower_bound",
        "upper_bound",
        "rate",
        "cap",
        "floor",
    ]
    tiers = frappe.get_all(
        "Allocation Tier",
        fields=[
            "parent as allocation_rule_id",
            "tier_order",
            "lower_bound",
            "upper_bound",
            "rate",
            "cap",
            "floor_amount",
        ],
        limit_page_length=0,
    )
    rows = [
        [
            tier.allocation_rule_id,
            tier.tier_order,
            tier.lower_bound or 0,
            tier.upper_bound or 999999999.99,
            tier.rate or 1,
            tier.cap or 999999999.99,
            tier.floor_amount or 0,
        ]
        for tier in tiers
    ]
    sync_table("epm_staging.allocation_tiers", columns, rows)