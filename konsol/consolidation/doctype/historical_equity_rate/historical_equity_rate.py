"""Historical Equity Rate — IAS 21 historical FX rates for equity accounts.

PRD-10: Equity accounts translated at the rate on the date the equity was
acquired/established, rather than closing rate.
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype
from konsol.epm.budget_grain import digest_name


class HistoricalEquityRate(Document):
    CH_TABLE = "epm_staging.historical_equity_rates"
    CH_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "data_area_id": "data_area_id",
        "main_account": "main_account",
        "rate_date": "rate_date",
        "historical_rate": "historical_rate",
    }

    def autoname(self):
        """Collision-safe name for the (group, entity, account, date) grain.

        The four keys are free text and were packed into a `format:` name that
        could blow Frappe's 140-char cap and silently truncate/collide
        (grynn-in/konsolidat#92, finding #6). digest_name keeps a readable head
        and appends a hash of the exact key tuple so distinct grains never
        collide.
        """
        self.name = digest_name(
            "HER",
            [self.consolidation_group, self.data_area_id,
             self.main_account, self.rate_date],
        )

    def validate(self):
        """Reject a non-positive historical rate (#92, finding #4).

        A 0/negative FX rate is never valid and would silently zero-out or
        sign-flip translated equity. Duplicate tranches need no separate guard:
        autoname() derives the document name deterministically from the
        (group, entity, account, rate_date) tuple, so a second rate for the same
        tranche collides on the primary key and is rejected at insert.
        """
        if self.historical_rate is None or float(self.historical_rate) <= 0:
            frappe.throw(
                "Historical Rate must be a positive number.",
                frappe.ValidationError,
            )

    def on_submit(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_cancel(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
