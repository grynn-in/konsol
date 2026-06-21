"""Budget Cycle — the single lock gate for a scenario × fiscal year budget.

The cycle is the *only* approval/lock construct: there is no per-line or
per-owner workflow. While the cycle is Open (docstatus 0) anyone with edit
rights writes budget cells via Excel; the application manager **submits** the
cycle (docstatus 1) at the deadline, which locks every sheet and fires the
ClickHouse sync + D365 write-back once per sheet. Cancel reopens for amendment.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class BudgetCycle(Document):
    def on_submit(self):
        """Lock the cycle: freeze sheets, sync ClickHouse, push D365."""
        self.db_set("status", "Locked", update_modified=False)
        self.db_set("locked_by", frappe.session.user, update_modified=False)
        self.db_set("locked_on", now_datetime(), update_modified=False)

        for sheet in self._sheets():
            sheet._sync_to_clickhouse(self.scenario_id, self.fiscal_year, active=True)
            if self._d365_enabled_for(sheet.data_area_id):
                from konsol.d365_writeback import enqueue_push_budget_sheet
                enqueue_push_budget_sheet(sheet.name)

    def on_cancel(self):
        """Unlock the cycle: withdraw downstream and reopen sheets for editing."""
        self.db_set("status", "Open", update_modified=False)
        self.db_set("locked_by", None, update_modified=False)
        self.db_set("locked_on", None, update_modified=False)

        for sheet in self._sheets():
            sheet._sync_to_clickhouse(self.scenario_id, self.fiscal_year, active=False)
            if self._d365_enabled_for(sheet.data_area_id):
                frappe.enqueue(
                    "konsol.d365_writeback.withdraw_budget_sheet",
                    queue="long",
                    name=sheet.name,
                )

    # ------------------------------------------------------------------

    def _sheets(self):
        """Load this cycle's Budget Sheet docs."""
        names = frappe.get_all("Budget Sheet", filters={"cycle": self.name}, pluck="name")
        return [frappe.get_doc("Budget Sheet", n) for n in names]

    @staticmethod
    def _d365_enabled_for(entity_id):
        """Whether D365 budget write-back is enabled for an entity.

        Lazy import + defensive: returns False (not raise) when D365 config is
        absent, so locking a cycle never fails just because write-back is off.
        """
        try:
            from konsol.d365_writeback import get_config
            return bool(get_config(entity_id=entity_id).get("enabled"))
        except Exception:
            return False
