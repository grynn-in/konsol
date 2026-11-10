"""konsol#509: the patch that moves Close Settings holidays into per-year
Close Holiday Calendars. Runs against a stub frappe that models the site."""
import importlib.util
import os
import sys
import types
from datetime import date

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "move_holidays_to_calendars.py")
DOTTED = "konsol.patches.move_holidays_to_calendars"


class _Doc:
    def __init__(self, site, year, no_holidays=0, rows=()):
        self.site, self.year, self.no_holidays = site, year, no_holidays
        self.holidays = [types.SimpleNamespace(**r) for r in rows]
        self.flags = types.SimpleNamespace()

    def get(self, field):
        return self.holidays

    def append(self, field, values):
        self.holidays.append(types.SimpleNamespace(**values))

    def save(self):
        self.site.calendars[self.year] = self


class _Site:
    def __init__(self, settings_rows=(), calendars=()):
        self.rows = [dict(name="H%d" % i, parent="Close Settings", **r)
                     for i, r in enumerate(settings_rows)]
        self.calendars = {}
        for year, no_holidays, dates in calendars:
            self.calendars[year] = _Doc(self, year, no_holidays,
                                        [{"holiday_date": d, "description": None} for d in dates])
        self.reloaded = []
        self.printed = []

    def frappe(self):
        site = self
        frappe = types.ModuleType("frappe")

        def get_all(doctype, filters=None, **k):
            assert doctype == "Close Holiday"
            assert filters == {"parent": "Close Settings", "parenttype": "Close Settings"}
            return [dict(r) for r in site.rows]

        def exists(doctype, name):
            return name in site.calendars

        def get_doc(arg, name=None):
            if isinstance(arg, dict):
                assert arg["doctype"] == "Close Holiday Calendar" and arg["no_holidays"] == 0
                return _Doc(site, arg["year"])
            return site.calendars[name]

        def delete(doctype, filters):
            assert doctype == "Close Holiday"
            names = set(filters["name"][1])
            site.rows = [r for r in site.rows if r["name"] not in names]

        frappe.get_all = get_all
        frappe.get_doc = get_doc
        frappe.reload_doc = lambda *a: site.reloaded.append(a)
        frappe.db = types.SimpleNamespace(exists=exists, delete=delete)
        return frappe


def _run(site):
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = site.frappe()
    try:
        spec = importlib.util.spec_from_file_location("_patch_under_test", PATCH_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.print = lambda *a: site.printed.append(" ".join(str(x) for x in a))
        mod.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return site


def _row(d, desc=None):
    return {"holiday_date": d, "description": desc}


def test_the_patch_is_registered_once():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        assert f.read().split().count(DOTTED) == 1


def test_rows_move_into_one_calendar_per_year_and_old_rows_go():
    site = _run(_Site([_row(date(2026, 1, 1), "NY"), _row(date(2026, 12, 25)),
                       _row(date(2027, 1, 1))]))
    assert sorted(site.calendars) == [2026, 2027]
    assert [h.holiday_date for h in site.calendars[2026].holidays] == [date(2026, 1, 1), date(2026, 12, 25)]
    assert site.calendars[2026].holidays[0].description == "NY"
    assert site.calendars[2026].no_holidays == 0 and site.calendars[2027].no_holidays == 0
    assert site.rows == []
    assert site.reloaded == [("consolidation", "doctype", "close_holiday_calendar"),
                             ("consolidation", "doctype", "close_settings")]
    assert site.printed == ["move_holidays_to_calendars: 3 rows found, 3 placed, 0 already present, "
                            "2 calendars created, 3 rows deleted"]


def test_a_second_run_is_a_no_op():
    site = _run(_Site([_row(date(2026, 1, 1))]))
    before = {y: [h.holiday_date for h in c.holidays] for y, c in site.calendars.items()}
    site.printed.clear()
    _run(site)
    assert {y: [h.holiday_date for h in c.holidays] for y, c in site.calendars.items()} == before
    assert site.printed == ["move_holidays_to_calendars: 0 rows found, 0 placed, 0 already present, "
                            "0 calendars created, 0 rows deleted"]


def test_a_site_with_no_holidays_creates_nothing():
    site = _run(_Site([]))
    assert site.calendars == {} and site.rows == []


def test_an_existing_calendar_keeps_its_rows_and_a_date_is_not_added_twice():
    site = _run(_Site([_row(date(2026, 1, 1)), _row(date(2026, 5, 1))],
                      calendars=[(2026, 0, [date(2026, 1, 1)])]))
    assert [h.holiday_date for h in site.calendars[2026].holidays] == [date(2026, 1, 1), date(2026, 5, 1)]
    assert "1 placed, 1 already present, 0 calendars created" in site.printed[0]
    assert site.rows == []


def test_no_row_is_lost():
    rows = [_row(date(2025, 12, 31)), _row(date(2026, 1, 1)), _row(date(2026, 1, 2))]
    site = _run(_Site(rows))
    placed = sum(len(c.holidays) for c in site.calendars.values())
    assert placed == len(rows)
