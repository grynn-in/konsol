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
    def __init__(self, years=(), rows=(), cycles=()):
        self.years = [_Row(y) for y in years]
        self.rows = [_Row(r) for r in rows]
        self.cycles = [_Row(c) for c in cycles]

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")
        utils = types.ModuleType("frappe.utils")
        utils.getdate = _getdate
        utils.today = lambda: TODAY
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
            if doctype in ("Period Status", "Fiscal Period"):
                return []
            raise AssertionError("unexpected get_all(%s)" % doctype)

        def sql(query, values=None, as_dict=False, **k):
            q = " ".join(query.split())
            if "`tabEPM Fiscal Year Period`" in q:
                rows = site.rows
            elif "`tabEPM Fiscal Year`" in q:
                rows = site.years
            else:
                raise AssertionError("unexpected query: %s" % q)
            rows = [_Row(r) for r in rows]
            return rows if as_dict else [tuple(r.values()) for r in rows]

        frappe.db = types.SimpleNamespace(sql=sql)
        frappe.get_all = get_all
        frappe.throw = throw
        frappe.whitelist = whitelist
        frappe.PermissionError = PermissionError
        frappe.ValidationError = ValidationError
        frappe.session = types.SimpleNamespace(user="Administrator")
        frappe.get_roles = lambda user=None: []
        return frappe, utils


def _tree(site):
    """Import home_api against the stub and call period_tree with the stub
    still installed; restore sys.modules afterwards."""
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
        return home_api.period_tree()
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
        years=[{"name": "2026", "fiscal_year": 2026, "status": "Open"},
               {"name": "2025", "fiscal_year": 2025, "status": "Closed"}],
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
    assert [r["code"] for r in closed["periods"]] == ["P01", "P02", "CLS"]
    # A Closed year closes its Open rows; a Locked row stays Locked.
    assert [r["status"] for r in closed["periods"]] == ["Closed", "Locked", "Closed"]
    assert [r["state"] for r in closed["periods"]] == ["closed", "locked", "closed"]
    assert closed["periods"][2]["label"] == "CLS"


def test_no_record_is_not_open():
    site = _Site(
        years=[{"name": "2026", "fiscal_year": 2026, "status": "Open"}],
        rows=_thirteen_period_rows(2026),
        cycles=[{"name": "BC-2027", "fiscal_year": 2027, "status": "Draft", "deadline": datetime.date(2026, 11, 30)}],
    )
    years = _by_year(_tree(site))
    fy = years[2027]
    assert fy["declared"] is False
    assert fy["periods"] == [], fy["periods"]
    assert fy["budget"] == {"name": "BC-2027", "status": "Draft", "deadline": "2026-11-30"}
    assert (fy["label"], fy["kind"]) == ("FY2027", "planning")
    for y in years.values():
        if not y["declared"]:
            assert not y["periods"], y
