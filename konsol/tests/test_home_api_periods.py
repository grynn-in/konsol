"""home_api.period_tree reads the declared fiscal calendar (konsol#189), host-run.

The navigator lists each EPM Fiscal Year with its own period rows, in
fiscal_period order, and each row's status is the stricter of the row and
its year. A year known only from budget data, with no EPM Fiscal Year, is
listed as undeclared with no periods: nothing is shown as open that no one
declared. A stub frappe serves canned years and rows and stays installed
while period_tree runs.
"""
import datetime
import importlib
import sys
import types

TODAY = "2026-09-14"
KONSOL_MODULES = ("konsol.home_api", "konsol.period_status", "konsol.entity_permissions")


class _Row(dict):
    __getattr__ = dict.get


def _getdate(value=None):
    if value is None:
        value = TODAY
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


class _Site:
    def __init__(self, years=(), rows=(), cycles=(), runs=(), perms=(), today=TODAY):
        self.years = [_Row(y) for y in years]
        self.rows = [_Row(r) for r in rows]
        self.cycles = [_Row(c) for c in cycles]
        self.runs = [_Row(r) for r in runs]     # Assertion Run, latest first
        self.perms = set(perms)                 # {(doctype, ptype)} the user holds
        self.today = today                      # override for tests picking a fiscal year off-calendar

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")
        utils = types.ModuleType("frappe.utils")
        utils.getdate = _getdate
        utils.today = lambda: site.today
        utils.get_fullname = lambda user: user
        frappe.utils = utils

        class PermissionError(Exception):
            pass

        class ValidationError(Exception):
            pass

        def throw(msg, exc=Exception, *a, **k):
            raise exc(msg)

        def whitelist(*a, **k):
            return lambda fn: fn

        def get_all(doctype, *a, **k):
            if doctype == "Budget Cycle":
                return list(site.cycles)
            if doctype == "EPM Fiscal Year":
                return list(site.years)
            if doctype == "EPM Fiscal Year Period":
                return list(site.rows)
            if doctype == "Assertion Run":
                return list(site.runs)
            # the rest of month()'s context: nothing on this site
            return []

        def sql(query, values=None, as_dict=False, **k):
            q = " ".join(query.split())
            if "`tabEPM Fiscal Year Period`" in q:
                rows = site.rows
                if values:   # period_row: parent=%s ... fiscal_period=%s
                    parent, period = values
                    rows = [r for r in rows if str(r.parent) == str(parent) and r.fiscal_period == int(period)]
            elif "`tabEPM Fiscal Year`" in q:
                rows = site.years
                if values:   # period_row: fiscal_year=%s
                    rows = [y for y in rows if y.fiscal_year == int(values[0])]
            else:
                raise AssertionError("unexpected query: %s" % q)
            rows = [_Row(r) for r in rows]
            return rows if as_dict else [tuple(r.values()) for r in rows]

        def get_value(doctype, filters, fields, as_dict=False, **k):
            table = {"EPM Fiscal Year": site.years, "EPM Fiscal Year Period": site.rows}.get(doctype)
            if table is None:
                raise AssertionError("unexpected get_value(%s)" % doctype)
            match = [r for r in table if all(str(r.get(f)) == str(v) for f, v in filters.items()
                                             if f not in ("parenttype", "parentfield"))]
            if not match:
                return None
            row = _Row({f: match[0].get(f) for f in fields})
            return row if as_dict else tuple(row.values())

        frappe.db = types.SimpleNamespace(sql=sql, get_value=get_value)
        frappe.get_all = get_all
        frappe.has_permission = lambda doctype, ptype="read", doc=None, **k: (doctype, ptype) in site.perms
        frappe._ = lambda s: s
        frappe.throw = throw
        frappe.whitelist = whitelist
        frappe.PermissionError = PermissionError
        frappe.ValidationError = ValidationError
        frappe.session = types.SimpleNamespace(user="Administrator")
        frappe.get_roles = lambda user=None: []
        return frappe, utils


def _tree(site):
    return _call(site, "period_tree")


def _call(site, endpoint, *args):
    """Import home_api against the stub and call ``endpoint`` with the stub
    still installed; restore sys.modules afterwards. The group-rate gate and
    the worker health check reach other modules and are answered here."""
    import konsol
    frappe, utils = site.module()
    names = ("frappe", "frappe.utils") + KONSOL_MODULES
    saved = {n: sys.modules.get(n) for n in names}
    # A fresh import needs both the sys.modules entry and the package
    # attribute gone, or an earlier call's module (bound to its stub) returns.
    attrs = {n: getattr(konsol, n.split(".")[1], None) for n in KONSOL_MODULES}
    for n in KONSOL_MODULES:
        sys.modules.pop(n, None)
        konsol.__dict__.pop(n.split(".")[1], None)
    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils
    try:
        home_api = importlib.import_module("konsol.home_api")
        home_api._rate_gate = lambda fy, p: {"missing_rates": [], "rates_error": None, "rate_blockers": []}
        home_api._health = lambda ctx, wide: {"worker": True, "connectors": [], "last_build": None}
        return getattr(home_api, endpoint)(*args)
    finally:
        for n, mod in saved.items():
            if mod is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = mod
        for n, mod in attrs.items():
            if mod is None:
                konsol.__dict__.pop(n.split(".")[1], None)
            else:
                setattr(konsol, n.split(".")[1], mod)


def _thirteen_period_rows(fy):
    """OPN, P01..P13 (four weeks each) and CLS, returned out of order."""
    start = datetime.date(fy, 1, 1)
    rows = [{"parent": str(fy), "fiscal_period": 0, "period_code": "OPN", "period_label": "Opening",
             "period_type": "Opening", "start_date": start, "end_date": start, "status": "Closed"}]
    for n in range(1, 14):
        s = start + datetime.timedelta(days=28 * (n - 1))
        rows.append({"parent": str(fy), "fiscal_period": n, "period_code": f"P{n:02d}",
                     # P05 has no label: the code stands in
                     "period_label": None if n == 5 else f"Period {n}",
                     "period_type": "Regular", "start_date": s, "end_date": s + datetime.timedelta(days=27),
                     "status": "Closed" if n <= 8 else "Open"})
    end = datetime.date(fy, 12, 31)
    rows.append({"parent": str(fy), "fiscal_period": 14, "period_code": "CLS", "period_label": "Closing",
                 "period_type": "Closing", "start_date": end, "end_date": end, "status": "Open"})
    return rows[7:] + rows[:7]


def _by_year(tree):
    return {y["fiscal_year"]: y for y in tree["years"]}


def test_tree_uses_declared_rows():
    y2025 = [
        {"parent": "2025", "fiscal_period": 13, "period_code": "CLS", "period_label": None,
         "period_type": "Closing", "start_date": datetime.date(2025, 12, 31),
         "end_date": datetime.date(2025, 12, 31), "status": "Open"},
        {"parent": "2025", "fiscal_period": 1, "period_code": "P01", "period_label": "January 2025",
         "period_type": "Regular", "start_date": datetime.date(2025, 1, 1),
         "end_date": datetime.date(2025, 1, 31), "status": "Open"},
        {"parent": "2025", "fiscal_period": 2, "period_code": "P02", "period_label": "February 2025",
         "period_type": "Regular", "start_date": datetime.date(2025, 2, 1),
         "end_date": datetime.date(2025, 2, 28), "status": "Locked"},
    ]
    site = _Site(
        years=[{"name": "2026", "fiscal_year": 2026, "status": "Open",
                "start_date": datetime.date(2026, 1, 1), "end_date": datetime.date(2026, 12, 31)},
               {"name": "2025", "fiscal_year": 2025, "status": "Closed",
                "start_date": datetime.date(2025, 1, 1), "end_date": datetime.date(2025, 12, 31)}],
        rows=_thirteen_period_rows(2026) + y2025,
        cycles=[{"name": "BC-2026", "fiscal_year": 2026, "status": "Approved", "deadline": None}],
    )
    tree = _tree(site)
    years = _by_year(tree)
    assert "current" in tree, tree.keys()

    fy = years[2026]
    assert fy["declared"] is True
    assert (fy["label"], fy["kind"]) == ("FY2026", "current")
    assert fy["budget"] == {"name": "BC-2026", "status": "Approved", "deadline": None}
    rows = fy["periods"]
    assert len(rows) == 15, len(rows)
    assert [r["fiscal_period"] for r in rows] == list(range(15))
    assert [r["code"] for r in rows] == ["OPN"] + [f"P{n:02d}" for n in range(1, 14)] + ["CLS"]
    assert [r["type"] for r in rows] == ["Opening"] + ["Regular"] * 13 + ["Closing"]
    assert rows[1]["label"] == "Period 1" and rows[5]["label"] == "P05"
    assert rows[1]["start_date"] == "2026-01-01"
    assert rows[13]["start_date"] == "2026-12-03"
    assert [r["status"] for r in rows] == ["Closed"] * 9 + ["Open"] * 6
    assert rows[8]["state"] == "closed"
    assert rows[9]["state"] == "open"      # P09 began 13 Aug
    assert rows[13]["state"] == "future"   # P13 begins 3 Dec

    closed = years[2025]
    assert closed["declared"] is True
    assert closed["kind"] == "past"
    assert [r["code"] for r in closed["periods"]] == ["P01", "P02", "CLS"]
    # A Closed year closes its Open rows; a Locked row stays Locked.
    assert [r["status"] for r in closed["periods"]] == ["Closed", "Locked", "Closed"]
    assert [r["state"] for r in closed["periods"]] == ["closed", "locked", "closed"]
    assert closed["periods"][2]["label"] == "CLS"


def test_no_record_is_not_open():
    site = _Site(
        years=[{"name": "2026", "fiscal_year": 2026, "status": "Open",
                "start_date": datetime.date(2026, 1, 1), "end_date": datetime.date(2026, 12, 31)}],
        rows=_thirteen_period_rows(2026),
        cycles=[{"name": "BC-2027", "fiscal_year": 2027, "status": "Draft", "deadline": datetime.date(2026, 11, 30)}],
    )
    years = _by_year(_tree(site))
    fy = years[2027]
    assert fy["declared"] is False
    assert fy["periods"] == [], fy["periods"]
    assert fy["budget"] == {"name": "BC-2027", "status": "Draft", "deadline": "2026-11-30"}
    assert (fy["label"], fy["kind"]) == ("FY2027", "undeclared")
    for y in years.values():
        if not y["declared"]:
            assert not y["periods"], y


def test_year_kind_from_declared_dates_not_calendar_year():
    """konsol#189 review finding 2b: the year's kind comes from its own
    start_date/end_date, never from comparing fiscal_year to today's calendar
    year. FY2026 runs April 2026 - March 2027, so on 2027-02-10 it is the
    year in progress ("current"), not "past" as a calendar-year comparison
    (2026 < 2027) would call it. A year known only from a Budget Cycle (no
    EPM Fiscal Year row, so no dates) is "undeclared", never "planning"."""
    site = _Site(
        years=[{"name": "2026", "fiscal_year": 2026, "status": "Open",
                "start_date": datetime.date(2026, 4, 1), "end_date": datetime.date(2027, 3, 31)}],
        rows=_calendar_months(2026, 2026, 4, 12),
        cycles=[{"name": "BC-2028", "fiscal_year": 2028, "status": "Draft", "deadline": None}],
        today="2027-02-10",
    )
    years = _by_year(_tree(site))
    assert years[2026]["kind"] == "current"
    assert years[2028]["declared"] is False
    assert years[2028]["kind"] == "undeclared"


def test_cycle_only_year_is_undeclared_not_planning():
    """konsol#189 review nit 5 (from 70b2): a year known only from a Budget
    Cycle has no EPM Fiscal Year row, so no dates to judge past/current/
    planning by. That is a different fact than "planning" (a future year
    someone HAS declared): FY2019 is long past, yet a cycle-only FY2019 must
    still say "undeclared", never "past" and never "planning"."""
    site = _Site(
        years=[{"name": "2026", "fiscal_year": 2026, "status": "Open",
                "start_date": datetime.date(2026, 1, 1), "end_date": datetime.date(2026, 12, 31)}],
        rows=_thirteen_period_rows(2026),
        cycles=[{"name": "BC-2019", "fiscal_year": 2019, "status": "Approved", "deadline": None}],
    )
    fy = _by_year(_tree(site))[2019]
    assert fy["declared"] is False
    assert fy["kind"] == "undeclared"


def test_blank_row_status_shown_as_is_not_open():
    """Bad data (a blank status validate() would now refuse on save) must not
    read as Open in the navigator, and must not crash it for every year
    (konsol#189 review finding 4)."""
    rows = _thirteen_period_rows(2026)
    for r in rows:
        if r["period_code"] == "P05":
            r["status"] = ""
    site = _Site(years=[{"name": "2026", "fiscal_year": 2026, "status": "Open"}], rows=rows)
    fy = _by_year(_tree(site))[2026]
    by_code = {r["code"]: r for r in fy["periods"]}
    assert by_code["P05"]["status"] == "Unknown", by_code["P05"]
    # every other row is unaffected
    assert by_code["P04"]["status"] == "Closed"
    assert by_code["P06"]["status"] == "Closed"


def _calendar_months(fy, start_year, start_month, count):
    """count consecutive Regular monthly periods starting (start_year,
    start_month) — a fiscal year that does not run Jan-Dec, so a test using
    it proves ``current`` isn't guessed from the calendar year/month."""
    import calendar

    rows = []
    y, m = start_year, start_month
    for n in range(1, count + 1):
        last_day = calendar.monthrange(y, m)[1]
        rows.append({"parent": str(fy), "fiscal_period": n, "period_code": f"P{n:02d}",
                     "period_label": None, "period_type": "Regular",
                     "start_date": datetime.date(y, m, 1), "end_date": datetime.date(y, m, last_day),
                     "status": "Open"})
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return rows


def test_current_is_the_declared_period_containing_today():
    """``current`` is the declared Regular period whose dates contain today —
    never guessed from today's calendar month/year (konsol#189 review
    finding 2). FY2026 runs April 2026 - March 2027, so today 2027-02-10
    falls in FY2026's P11, not calendar (2027, 2)."""
    site = _Site(years=[{"name": "2026", "fiscal_year": 2026, "status": "Open"}],
                 rows=_calendar_months(2026, 2026, 4, 12), today="2027-02-10")
    assert _tree(site)["current"] == {"fiscal_year": 2026, "fiscal_period": 11}


def test_current_is_none_when_nothing_declared_covers_today():
    site = _Site(years=[], rows=[], today="2026-09-14")
    assert _tree(site)["current"] is None


# --- month() on the declared calendar ---------------------------------------

def _refused(site, *args):
    """(exception class name, message) month() refuses with; fails if it answers."""
    try:
        out = _call(site, "month", *args)
    except Exception as e:  # noqa: BLE001 - the stub's classes are per-call
        return type(e).__name__, str(e)
    raise AssertionError(f"month{args} answered: {out['period']}")


def _year_2026():
    return _Site(years=[{"name": "2026", "fiscal_year": 2026, "status": "Open"}],
                 rows=_thirteen_period_rows(2026))


def test_month_refuses_undeclared():
    site = _year_2026()
    # a period number the year has no row for, and a year nobody declared
    assert _refused(site, 2026, 15) == ("PeriodNotDeclared", "FY2026 has no period 15.")
    assert _refused(site, "2031", "3") == (
        "PeriodNotDeclared", "FY2031 is not declared: create it in EPM Fiscal Year.")
    # outside 0..255 (or not a number) is not a period at all
    assert _refused(site, 2026, 256) == ("ValidationError", "No such period: FY2026 period 256")
    assert _refused(site, 2026, -1)[0] == "ValidationError"
    assert _refused(site, 2026, "P01")[0] == "ValidationError"


def test_month_accepts_declared_period_14():
    """A 13-period year's CLS is period 14: shown as declared, not refused."""
    out = _call(_year_2026(), "month", 2026, "14")
    assert out["period"] == {
        "fiscal_year": 2026, "fiscal_period": 14, "code": "CLS", "label": "Closing",
        "status": "Open", "state": "future", "closed_by": None, "closed_on": None,
        "entities_in_close": 0,
    }, out["period"]
    assert len(out["stages"]) == 8 and {"mine", "waiting", "health"} <= set(out)
    # a regular period of the same year takes its code, label and dates from its row
    p09 = _call(_year_2026(), "month", 2026, 9)["period"]
    assert (p09["code"], p09["label"], p09["status"], p09["state"]) == ("P09", "Period 9", "Open", "open")


def test_month_closed_by_from_row_or_year():
    rows = _thirteen_period_rows(2026)
    for r in rows:
        if r["period_code"] == "P08":
            r.update(closed_by="lead@example.com", closed_on=datetime.datetime(2026, 8, 31, 18, 0))
    rows += [
        {"parent": "FY-2025", "fiscal_period": 1, "period_code": "P01", "period_label": "January 2025",
         "period_type": "Regular", "start_date": datetime.date(2025, 1, 1),
         "end_date": datetime.date(2025, 1, 31), "status": "Open"},
        {"parent": "FY-2025", "fiscal_period": 2, "period_code": "P02", "period_label": "February 2025",
         "period_type": "Regular", "start_date": datetime.date(2025, 2, 1),
         "end_date": datetime.date(2025, 2, 28), "status": "Locked",
         "closed_by": "sm@example.com", "closed_on": datetime.datetime(2025, 3, 5, 10, 0)},
    ]
    site = _Site(years=[{"name": "2026", "fiscal_year": 2026, "status": "Open"},
                        {"name": "FY-2025", "fiscal_year": 2025, "status": "Closed",
                         "closed_by": "admin@example.com", "closed_on": datetime.datetime(2026, 1, 15, 9, 0)}],
                 rows=rows)

    def closed(fy, p):
        out = _call(site, "month", fy, p)["period"]
        return out["status"], out["closed_by"], out["closed_on"]

    # the row's own close
    assert closed(2026, 8) == ("Closed", "lead@example.com", "2026-08-31 18:00:00")
    # open row in an open year: nobody closed it
    assert closed(2026, 10) == ("Open", None, None)
    # an Open row of a Closed year: closed by the year's close
    assert closed(2025, 1) == ("Closed", "admin@example.com", "2026-01-15 09:00:00")
    # a row locked on its own keeps its own closer
    assert closed(2025, 2) == ("Locked", "sm@example.com", "2025-03-05 10:00:00")


def test_may_close_follows_fiscal_year_permission():
    """Sign off is offered to whoever may write EPM Fiscal Year, where the
    close actions live; a Period Status write right no longer counts."""
    def signoff(perms):
        site = _year_2026()
        site.runs = [_Row(name="AR-1", status="Green", passed=5, total=5, signoff_status=None)]
        site.perms = set(perms)
        out = _call(site, "month", 2026, 9)
        item = next(i for i in out["mine"] if i["id"] == "signoff")
        assert item["state"] == "ready", item
        return item["action"]["allowed"], item["action"]["reason"]

    assert signoff({("EPM Fiscal Year", "write")}) == (True, None)
    assert signoff({("Period Status", "write")}) == (False, "You don't have permission for this.")
    assert signoff(set()) == (False, "You don't have permission for this.")
