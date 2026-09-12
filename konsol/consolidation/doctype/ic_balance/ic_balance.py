"""IC Balance — intercompany sales and inventory balances between entity pairs.

PRD-15: Used by unrealized profit elimination (ending_inventory_from_ic × margin%).
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype_after_commit
from konsol.period_status import assert_open


class ICBalance(Document):
    CH_TABLE = "epm_staging.ic_balances"
    CH_FIELD_MAP = {
        "selling_entity": "selling_entity",
        "buying_entity": "buying_entity",
        "fiscal_year": "fiscal_year",
        "fiscal_period": "fiscal_period",
        "ic_sales_amount": "ic_sales_amount",
        "ending_inventory_from_ic": "ending_inventory_from_ic",
    }

    def before_cancel(self):
        """Cancel only while the period is open (decided 12 Sep 2026): a
        cancelled balance leaves the warehouse, so after close it would rewrite
        a closed period's eliminations. Correct with a new balance (#136)."""
        assert_open(self.fiscal_year, self.fiscal_period, action="cancel an IC balance")

    def on_submit(self):
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_cancel(self):
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def after_delete(self):
        """after_delete, not on_trash: on_trash runs before the row is gone, so
        the full-table re-send put it straight back (#120)."""
        sync_doctype_after_commit(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
