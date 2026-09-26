import frappe
from frappe.model.document import Document

REGULAR = "Regular"
# Effective period statuses that fix the first close (fiscal_status_model).
_CLOSED_STATUSES = ("Closed", "Locked")


def _first_close_key(year, period):
    """``(year, period)``, or None when undeclared (blank or 0 in either part)."""
    year, period = int(year or 0), int(period or 0)
    if not year or not period:
        return None
    return (year, period)


def _label(key):
    return "FY%d P%02d" % key


class CloseSettings(Document):
    # konsol#305 A36a (decision P13): close policy the Close Lead (EPM Admin)
    # owns. It is its own single doctype because EPM Settings is System
    # Manager only, and Frappe's base write check reads permlevel-0 rows only,
    # so field-level write could not open just these fields (A36, proved live).

    def validate(self):
        self.validate_first_close_period()
        self.validate_first_close_locked()

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

    def validate_first_close_locked(self):
        """konsol#305 A64 (#305-R5a, Deepak 26 Sep): once the first close
        period has been used, it cannot move. A change of year or period (or
        clearing it) is refused when any Regular period from min(old, new)
        onward is Closed or Locked, or has a signed latest close run. A first
        declaration (nothing stored) and a save with no change are allowed."""
        old = _first_close_key(
            frappe.db.get_single_value("Close Settings", "first_close_fiscal_year"),
            frappe.db.get_single_value("Close Settings", "first_close_fiscal_period"),
        )
        new = _first_close_key(self.first_close_fiscal_year, self.first_close_fiscal_period)
        if old is None or old == new:
            return
        start = old if new is None else min(old, new)

        # Imported lazily: both need a live site; the controller test stubs them.
        from konsol import fiscal_calendar
        from konsol.close import signoff_gate
        from konsol.consolidation.doctype.assertion_run.assertion_run import SIGNED_STATES

        runs = signoff_gate._latest_runs()
        rows = sorted(
            (r for r in fiscal_calendar.fiscal_period_rows()
             if r.get("period_type") == REGULAR),
            key=lambda r: (int(r["fiscal_year"]), int(r["fiscal_period"])),
        )
        for row in rows:
            key = (int(row["fiscal_year"]), int(row["fiscal_period"]))
            if key < start:
                continue
            if row["status"] in _CLOSED_STATUSES:
                used = row["status"].lower()
            elif (runs.get(key) or {}).get("signoff_status") in SIGNED_STATES:
                used = "signed"
            else:
                continue
            frappe.throw(frappe._(
                "{0} is already {1} under the current first close ({2}); "
                "the first close period can no longer move."
            ).format(_label(key), used, _label(old)))
