"""The create_fiscal_years migration patch (konsol#189), host-run.

patches.txt has no sections, so every patch runs pre_model_sync: the patch
must reload EPM Fiscal Year Period and EPM Fiscal Year before it queries
anything, or the tables do not exist, it no-ops and still records itself as
run. A stub frappe records every call in order; the real planner
(fiscal_migration_model.plan) decides what the patch writes.
"""
import datetime
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "create_fiscal_years.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
MODULE = "konsol.patches.create_fiscal_years"


class _Throw(Exception):
    pass


class _Site:
    """Stub frappe backed by in-memory tables. ``calls`` records, in order,
    every frappe call the patch makes."""

    def __init__(self, used=None, period_status=None, years=None):
        self.calls = []
        # doctype -> list of (fiscal_year, fiscal_period, docstatus)
        self.used = used or {}
        self.period_status = period_status or []
        # name -> doc (SimpleNamespace with .periods of SimpleNamespace rows)
        self.years = {}
        for doc in years or []:
            self.years[str(doc["fiscal_year"])] = self._doc(doc)

    @staticmethod
    def _doc(data):
        doc = types.SimpleNamespace(**{k: v for k, v in data.items() if k != "periods"})
        doc.periods = [types.SimpleNamespace(**r) for r in data.get("periods", [])]
        doc.flags = types.SimpleNamespace()
        return doc

    # -- frappe surface -------------------------------------------------
    def module(self):
        site = self
        frappe = types.ModuleType("frappe")

        def reload_doc(module, dt, name, *a, **k):
            site.calls.append(("reload_doc", module, dt, name))

        def throw(msg, *a, **k):
            site.calls.append(("throw", msg))
            raise _Throw(msg)

        def get_doc(arg, name=None):
            if isinstance(arg, dict):
                site.calls.append(("get_doc", "new", arg.get("fiscal_year")))
                return site._new(arg)
            site.calls.append(("get_doc", arg, name))
            return site.years[str(name)]

        db = types.SimpleNamespace()

        def table_exists(dt, *a, **k):
            site.calls.append(("db.table_exists", dt))
            return dt in site.used or (dt == "Period Status") or dt.startswith("EPM Fiscal Year")

        def has_column(dt, col):
            site.calls.append(("db.has_column", dt, col))
            if dt not in site.used and dt != "Period Status" and not dt.startswith("EPM Fiscal Year"):
                raise AssertionError("has_column on a missing table: %s" % dt)
            return True

        def sql(query, values=None, as_dict=False, **k):
            site.calls.append(("db.sql", " ".join(query.split())))
            return site._sql(query, as_dict)

        def get_all(*a, **k):
            site.calls.append(("get_all", a, k))
            return site._get_all(*a, **k)

        db.table_exists = table_exists
        db.has_column = has_column
        db.sql = sql
        frappe.db = db
        frappe.reload_doc = reload_doc
        frappe.throw = throw
        frappe.get_doc = get_doc
        frappe.get_all = get_all
        frappe.flags = types.SimpleNamespace()
        frappe.whitelist = lambda *a, **k: (lambda fn: fn)
        return frappe

    def _new(self, data):
        doc = self._doc(data)
        site = self

        def insert(ignore_permissions=False, **k):
            site.calls.append(("insert", doc.fiscal_year, bool(getattr(doc.flags, "konsol_fiscal_migration", False))))
            site.years[str(doc.fiscal_year)] = doc
            return doc

        doc.insert = insert
        return doc

    def _attach_save(self):
        site = self
        for doc in self.years.values():
            def save(ignore_permissions=False, _doc=doc, **k):
                site.calls.append(("save", _doc.fiscal_year, bool(getattr(_doc.flags, "konsol_fiscal_migration", False))))
                return _doc
            doc.save = save

    def _year_rows(self):
        return [{"name": name, "fiscal_year": doc.fiscal_year} for name, doc in self.years.items()]

    def _period_rows(self):
        out = []
        for name, doc in self.years.items():
            for r in doc.periods:
                out.append({
                    "parent": name,
                    "fiscal_period": r.fiscal_period,
                    "period_code": r.period_code,
                    "start_date": r.start_date,
                    "end_date": r.end_date,
                    "status": getattr(r, "status", "Open"),
                    "closed_by": getattr(r, "closed_by", None),
                    "closed_on": getattr(r, "closed_on", None),
                })
        return out

    def _get_all(self, doctype, filters=None, fields=None, **k):
        if doctype == "EPM Fiscal Year":
            return self._year_rows()
        if doctype == "EPM Fiscal Year Period":
            return self._period_rows()
        raise AssertionError("unexpected get_all(%s)" % doctype)

    def _sql(self, query, as_dict):
        q = " ".join(query.split())
        if "`tabEPM Fiscal Year Period`" in q:
            rows = self._period_rows()
        elif "`tabEPM Fiscal Year`" in q:
            rows = self._year_rows()
        elif "`tabPeriod Status`" in q:
            rows = [dict(r) for r in self.period_status]
        else:
            for dt, entries in self.used.items():
                if "`tab%s`" % dt in q:
                    live = [e for e in entries if not ("docstatus < 2" in q and e[2] == 2)]
                    rows = [{"fiscal_year": y, "fiscal_period": p} for y, p, _ in live]
                    break
            else:
                raise AssertionError("unexpected query: %s" % q)
        if as_dict:
            return rows
        return [tuple(r.values()) for r in rows]


def _run(site):
    """Install the stub, load the patch by path under a private name, run
    execute() with the stub still installed, then restore sys.modules."""
    site._attach_save()
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = site.module()
    try:
        spec = importlib.util.spec_from_file_location("_create_fiscal_years_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        return patch.execute()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _is_query(call):
    return call[0].startswith("db.") or call[0] in ("get_all", "get_doc")


def test_reload_before_any_query():
    site = _Site(used={"Trial Balance Submission": [(2025, 3, 1)]})
    _run(site)
    assert site.calls[:2] == [
        ("reload_doc", "epm", "doctype", "epm_fiscal_year_period"),
        ("reload_doc", "epm", "doctype", "epm_fiscal_year"),
    ], site.calls[:4]
    first_query = next(i for i, c in enumerate(site.calls) if _is_query(c))
    assert first_query >= 2
    assert [c for c in site.calls if c[0] == "insert"] == [("insert", 2025, True)]


def test_conflicts_throw_and_insert_nothing():
    # Two conflicts in a year the plan would create: a Period Status row whose
    # dates differ from the calendar month, and a period outside 0..255.
    site = _Site(
        used={"IC Balance": [(2025, 300, 0)]},
        period_status=[{
            "fiscal_year": "2025", "fiscal_period": 3, "status": "Closed",
            "start_date": datetime.date(2025, 3, 5), "end_date": datetime.date(2025, 3, 31),
            "closed_by": "a@example.com", "closed_on": datetime.datetime(2025, 4, 2, 9, 0),
        }],
    )
    try:
        _run(site)
    except _Throw as exc:
        msg = str(exc)
    else:
        raise AssertionError("conflicts must stop the patch")
    assert "period 300" in msg, msg
    assert "2025-03-05" in msg, msg
    assert not [c for c in site.calls if c[0] in ("insert", "save")], site.calls
    assert not [c for c in site.calls if c[0] == "get_doc"], site.calls


def test_rerun_is_noop():
    # 2025 is new (a closed P03 moves across); 2024 already exists with an
    # Open P01 whose Period Status row is Locked. 2024 is already declared,
    # so it is its own source of truth: the stale Period Status row is
    # ignored, no move, no save.
    existing_2024 = {
        "doctype": "EPM Fiscal Year", "fiscal_year": 2024,
        "start_date": datetime.date(2024, 1, 1), "end_date": datetime.date(2024, 12, 31),
        "periods": [{
            "fiscal_period": 1, "period_code": "P01",
            "start_date": datetime.date(2024, 1, 1), "end_date": datetime.date(2024, 1, 31),
            "status": "Open", "closed_by": None, "closed_on": None,
        }],
    }
    closed_on = datetime.datetime(2025, 4, 2, 9, 0)
    site = _Site(
        used={
            "Trial Balance Submission": [(2025, 3, 1), (2025, 4, 2), (2024, 1, 1)],
            "Assertion Run": [(2025, 14, 0)],
        },
        period_status=[
            {"fiscal_year": "2025", "fiscal_period": 3, "status": "Closed",
             "start_date": datetime.date(2025, 3, 1), "end_date": datetime.date(2025, 3, 31),
             "closed_by": "a@example.com", "closed_on": closed_on},
            {"fiscal_year": "2024", "fiscal_period": 1, "status": "Locked",
             "start_date": None, "end_date": None,
             "closed_by": "b@example.com", "closed_on": closed_on},
        ],
        years=[existing_2024],
    )
    _run(site)
    writes = [c for c in site.calls if c[0] in ("insert", "save")]
    assert writes == [("insert", 2025, True)], writes

    y2025 = site.years["2025"]
    rows = {r.period_code: r for r in y2025.periods}
    assert rows["P03"].status == "Closed"
    assert rows["P03"].closed_by == "a@example.com" and rows["P03"].closed_on == closed_on
    assert rows["P04"].status == "Open"
    assert rows["P14"].period_type == "Adjustment"
    assert {"OPN", "CLS"} <= set(rows)
    # 2024 already existed: its own (Open) status stands, the stale Locked
    # Period Status row is ignored
    p01_2024 = site.years["2024"].periods[0]
    assert (p01_2024.status, p01_2024.closed_by) == ("Open", None)

    site.calls.clear()
    _run(site)
    assert not [c for c in site.calls if c[0] in ("insert", "save", "throw")], site.calls


def test_listed_once_in_patches_txt():
    with open(PATCHES_TXT) as f:
        lines = [l.strip() for l in f if l.strip()]
    assert lines.count(MODULE) == 1, lines.count(MODULE)
