"""Ownership Period — temporal ownership with acquisition and disposal fields.

PRD-9: Effective date ranges for ownership changes
PRD-11: Step acquisitions (acquisition_price, fair_value_adjustment, goodwill)
PRD-12: Disposals (disposal_date, disposal_price)
"""
import frappe
from frappe.model.document import Document

from konsol.clickhouse import sync_doctype


class OwnershipPeriod(Document):
    CH_TABLE = "epm_staging.ownership_periods"
    CH_FIELD_MAP = {
        "consolidation_group": "consolidation_group",
        "data_area_id": "data_area_id",
        "effective_date": "effective_date",
        "end_date": "end_date",
        "ownership_pct": "ownership_pct",
        "consolidation_method": "consolidation_method",
        "acquisition_date": "acquisition_date",
        "is_first_acquisition": "is_first_acquisition",
        "acquisition_price": "acquisition_price",
        "fair_value_adjustment": "fair_value_adjustment",
        "disposal_date": "disposal_date",
        "disposal_price": "disposal_price",
        "is_disposal": "is_disposal",
    }

    def validate(self):
        self._check_no_gaps_or_overlaps()

    def on_submit(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_cancel(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def on_trash(self):
        sync_doctype(self.doctype, self.CH_TABLE, self.CH_FIELD_MAP)

    def _check_no_gaps_or_overlaps(self):
        """PRD-9: Validate no overlapping periods for same entity in same group."""
        if not self.effective_date:
            return
        overlaps = frappe.get_all(
            self.doctype,
            filters={
                "consolidation_group": self.consolidation_group,
                "data_area_id": self.data_area_id,
                "name": ["!=", self.name],
                "docstatus": ["!=", 2],
            },
            fields=["name", "effective_date", "end_date"],
            limit_page_length=0,
        )
        for op in overlaps:
            if (self.effective_date <= (op.end_date or "9999-12-31")
                    and (self.end_date or "9999-12-31") >= op.effective_date):
                frappe.throw(
                    f"Ownership period overlaps with {op.name} "
                    f"({op.effective_date} to {op.end_date})"
                )
