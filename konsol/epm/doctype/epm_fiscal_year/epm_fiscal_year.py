import frappe
from frappe.model.document import Document
from frappe.utils import getdate

from konsol import fiscal_structure_model as fsm


def _int(value):
    """An Int field as an int; blank stays None and a non-number stays as
    given, so the checks name it rather than reading it as 0."""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _date(value):
    """A Date field as a date; blank stays None (getdate(None) is today)."""
    return getdate(value) if value else None


class EPMFiscalYear(Document):
    def validate(self):
        year = {
            "year": _int(self.fiscal_year),
            "start_date": _date(self.start_date),
            "end_date": _date(self.end_date),
        }
        rows = [
            {
                "period": _int(r.fiscal_period),
                "code": r.period_code,
                "type": r.period_type,
                "start_date": _date(r.start_date),
                "end_date": _date(r.end_date),
            }
            for r in (self.periods or [])
        ]

        errors = (
            fsm.period_problems(rows)
            + fsm.year_problems(year, rows)
            + fsm.regular_period_problems(year, rows)
            + fsm.placement_problems(year, rows)
        )
        if errors:
            frappe.throw("\n".join(errors))
