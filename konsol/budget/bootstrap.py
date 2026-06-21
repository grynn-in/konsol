"""Post-fixture bootstrap for Budget Cycle / Sheet demo data.

Fixture import runs before Budget Line's in_budget Custom Fields are provisioned
(``after_migrate`` → ``_sync_budget_line_custom_fields``), so dimension values
are applied here. Sheets are then pushed to ClickHouse so open demo budgets are
readable without locking the cycle first.
"""
import frappe

# Known demo lines: main_account → in_budget dim values (by fieldname).
_DEMO_LINE_DIMS = {
    "4010": {"dim_cost_center": "SALES", "dim_department": "SALES"},
    "5010": {"dim_cost_center": "HQ", "dim_department": "MGMT"},
}


def enrich_budget_fixture_lines():
    """Apply in_budget dim values after Custom Fields exist on Budget Line."""
    if not frappe.db.table_exists("Budget Sheet"):
        return

    from konsol.epm.budget_grain import budget_dimension_names

    dims = budget_dimension_names()
    if not dims:
        return

    for sheet_name in frappe.get_all("Budget Sheet", pluck="name"):
        sheet = frappe.get_doc("Budget Sheet", sheet_name)
        changed = False
        for line in sheet.lines:
            patch = _DEMO_LINE_DIMS.get(line.main_account)
            if not patch:
                continue
            for field in dims:
                val = patch.get(field)
                if val and not (line.get(field) or ""):
                    line.set(field, val)
                    changed = True
        if changed:
            sheet.flags.ignore_permissions = True
            sheet.save()


def sync_budget_sheets_to_clickhouse():
    """Push fixture-loaded Budget Sheet docs to ``epm_gold.budget_monthly_input``."""
    if not frappe.db.table_exists("Budget Sheet"):
        return

    for sheet_name in frappe.get_all("Budget Sheet", pluck="name"):
        sheet = frappe.get_doc("Budget Sheet", sheet_name)
        cycle = frappe.db.get_value(
            "Budget Cycle",
            sheet.cycle,
            ["scenario_id", "fiscal_year"],
            as_dict=True,
        )
        if not cycle:
            continue
        sheet._sync_to_clickhouse(
            cycle.scenario_id,
            cycle.fiscal_year,
            active=True,
        )