import datetime as _datetime
import importlib.util as _importlib_util
import os as _os

import frappe
from frappe.model.document import Document


def _load_deadline_model():
    """konsol/close/deadline_model.py loaded by path, as close_settings.py
    does: the pure rules, reachable under the host tests' stub packages."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "..", "close", "deadline_model.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_deadline_model_calendar", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deadline_model = _load_deadline_model()


def _as_date(value):
    """A Date field as a ``datetime.date``: a stored row reads a date, a row
    typed in the form arrives as an ISO string. Blank passes through for the
    child doctype's ``reqd`` to refuse."""
    if isinstance(value, _datetime.datetime):
        return value.date()
    if isinstance(value, str) and value:
        return _datetime.date.fromisoformat(value[:10])
    return value


class CloseHolidayCalendar(Document):
    """konsol#509 (option D, chosen under Deepak Pai's standing rule of 5 Nov
    2026): one document per calendar year, named by the year. It declares the
    year either with its holidays or with No Holidays This Year ticked.
    ``deadline_model.calendar_problems`` refuses the rest. Not synced to
    ClickHouse; ``track_changes`` keeps who declared the year and when."""

    def validate(self):
        dates = [_as_date(row.holiday_date) for row in self.holidays or []]
        problems = deadline_model.calendar_problems(self.year, bool(self.no_holidays), dates)
        if problems:
            frappe.throw("<br>".join(problems))
