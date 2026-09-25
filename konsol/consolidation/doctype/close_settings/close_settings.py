import frappe
from frappe.model.document import Document


class CloseSettings(Document):
    # konsol#305 A36a (decision P13): close policy the Close Lead (EPM Admin)
    # owns. It is its own single doctype because EPM Settings is System
    # Manager only, and Frappe's base write check reads permlevel-0 rows only,
    # so field-level write could not open just these fields (A36, proved live).

    def validate(self):
        self.validate_first_close_period()

    def validate_first_close_period(self):
        """konsol#303: the first period konsol closes. No default — a blank
        pair is fine (nothing declared yet), but a half-set pair or a
        non-Regular / undeclared period is refused outright."""
        year = self.first_close_fiscal_year
        period = self.first_close_fiscal_period
        if not year and not period:
            return
        if not (year and period):
            frappe.throw(frappe._("Give both the first close year and period, or neither."))
        # Imported lazily: konsol.period_status needs a live site, and this
        # keeps the pure-ish controller test free to stub it.
        import konsol.period_status as period_status

        row = period_status.period_row(year, period)
        if row["type"] != "Regular":
            frappe.throw(frappe._(
                "The first close period must be a Regular period; {0} is {1}."
            ).format(row["code"], row["type"]))
