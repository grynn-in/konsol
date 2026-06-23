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
        """Guard against bad rates and duplicate tranches (#92, finding #4).

        - rate must be positive (a 0/negative FX rate is never valid and would
          silently zero-out or sign-flip translated equity);
        - no second *submitted* rate for the same (group, entity, account,
          rate_date) tranche.
        """
        if self.historical_rate is None or float(self.historical_rate) <= 0:
            frappe.throw(
                "Historical Rate must be a positive number.",
                frappe.ValidationError,
            )

        dupe = frappe.get_all(
            self.doctype,
            filters={
                "consolidation_group": self.consolidation_group,
                "data_area_id": self.data_area_id,
                "main_account": self.main_account,
                "rate_date": self.rate_date,
                "docstatus": 1,
                "name": ["!=", self.name],
            },
            limit=1,
        )
        if dupe:
            frappe.throw(
                "A submitted Historical Equity Rate already exists for "
                "{0} / {1} / account {2} on {3}.".format(
                    self.consolidation_group, self.data_area_id,
                    self.main_account, self.rate_date),
                frappe.DuplicateEntryError,
            )

    def on_submit(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_cancel(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)
