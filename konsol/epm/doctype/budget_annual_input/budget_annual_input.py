"""Budget Annual Input — a top-down annual figure, spread into months by profile.

konsolidat#146: this was `seeds/budget_annual_input.csv`, the last seed the dbt
project owned. Like the ISO currency and fiscal-calendar lists it had no second
writer, but budget figures are not something a transform repo should hold.

konsol already owned the *bottom-up* path — Budget Cycle → Budget Sheet →
Budget Line, exploded into `epm_gold.budget_monthly_input` per period. This is
the other half: one annual number plus a Spread Profile, which
`gold_spread_budget` turns into twelve monthly rows. The two branches of that
model are now both konsol's.
"""
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class BudgetAnnualInput(Document):
    CH_TABLE = "epm_gold.budget_annual_input"
    CH_FIELD_MAP = {
        "scenario_id": "scenario_id",
        "data_area_id": "data_area_id",
        "fiscal_year": "fiscal_year",
        "main_account": "main_account",
        "dim_cost_center": "dim_cost_center",
        "dim_department": "dim_department",
        "annual_amount": "annual_amount",
        "spread_profile_id": "spread_profile_id",
        "submitted_by": "submitted_by",
    }

    def on_update(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
