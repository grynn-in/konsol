import frappe
from frappe.model.document import Document


class EPMSettings(Document):
    # konsolidat#93 (decided 13 Sep 2026): the presentation currency lives on
    # each Consolidation Group node, which is what the translation reads. The
    # group consolidation currency setting that used to be here was read by
    # nothing, so it is gone (see the drop_epm_settings_… patch).

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
