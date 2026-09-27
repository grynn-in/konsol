"""Which doctypes carry fiscal-period data, and which periods of a year are in
use (konsol#189). A used period is frozen by the fiscal-structure rules, so
every doctype with fiscal_year + fiscal_period must be classified — a new one
that is left out would let a period be removed from under its documents.

The module is loaded by path against a stub frappe; sys.modules is restored."""
import glob
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(APP_DIR, "fiscal_calendar.py")


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def sql(self, query, values=None, *args, **kwargs):
        self.calls.append((query, values))
        return self.rows


def _load(db=None):
    saved = sys.modules.get("frappe")
    frappe = types.ModuleType("frappe")
    frappe.db = db or _DB([])
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("fiscal_calendar_under_test", MODULE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


def _doctype_jsons():
    """name -> DocType JSON for every doctype under konsol/."""
    out = {}
    for path in glob.glob(os.path.join(APP_DIR, "**", "doctype", "*", "*.json"), recursive=True):
        try:
            with open(path) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("doctype") == "DocType" and d.get("name"):
            out[d["name"]] = d
    return out


def _has_period_fields(d):
    fields = {f.get("fieldname"): f for f in d.get("fields", [])}
    return ("fiscal_year" in fields and "fiscal_period" in fields
            and fields["fiscal_period"].get("fieldtype") == "Int")


def test_every_period_doctype_is_classified():
    M = _load()
    doctypes = _doctype_jsons()
    using = set(M.DOCTYPES_USING_PERIODS)
    not_data = set(M.NOT_PERIOD_DATA)

    assert len(using) == len(M.DOCTYPES_USING_PERIODS), "duplicate in DOCTYPES_USING_PERIODS"
    assert not (using & not_data), f"classified both ways: {sorted(using & not_data)}"
    for name in using | not_data:
        assert name in doctypes, f"{name!r} is classified but has no doctype JSON"
    for name, reason in M.NOT_PERIOD_DATA.items():
        assert isinstance(reason, str) and reason.strip(), f"{name!r} needs a reason"

    with_periods = {n for n, d in doctypes.items() if _has_period_fields(d)}
    unclassified = with_periods - using - not_data
    assert not unclassified, (
        f"doctypes with fiscal_year + fiscal_period not classified: {sorted(unclassified)} — "
        "add each to DOCTYPES_USING_PERIODS or NOT_PERIOD_DATA in konsol/fiscal_calendar.py")
    # a period-data doctype must really have the fields the query reads
    for name in using:
        assert _has_period_fields(doctypes[name]), f"{name!r} lacks fiscal_year/fiscal_period"


def test_assertion_runs_freeze_periods():
    """Assertion Run records sign-off evidence against a period, so a period
    with assertion runs must be frozen like one with postings — Pipeline Run
    stays a build log (NOT_PERIOD_DATA)."""
    M = _load()
    assert "Assertion Run" in M.DOCTYPES_USING_PERIODS
    assert "Assertion Run" not in M.NOT_PERIOD_DATA
    assert "Pipeline Run" in M.NOT_PERIOD_DATA

    db = _DB([])
    M2 = _load(db)
    saved = sys.modules.get("frappe")
    frappe = types.ModuleType("frappe")
    frappe.db = db
    sys.modules["frappe"] = frappe
    try:
        M2.periods_in_use(2026)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    assert len(db.calls) == 1, "must be one UNION query"
    query, _ = db.calls[0]
    assert "`tabAssertion Run`" in query


def test_periods_in_use_query():
    db = _DB([(3,), (0,), (12,), (3,), (None,)])
    M = _load(db)
    doctypes = _doctype_jsons()

    # patch frappe back in for the call: the module imports it lazily
    saved = sys.modules.get("frappe")
    frappe = types.ModuleType("frappe")
    frappe.db = db
    sys.modules["frappe"] = frappe
    try:
        result = M.periods_in_use(2026)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved

    assert result == {0, 3, 12}
    assert len(db.calls) == 1, "must be one UNION query"
    query, values = db.calls[0]
    assert "2026" not in query, "the year must be a parameter, not formatted in"
    flat = list(values.values()) if isinstance(values, dict) else list(values or ())
    assert 2026 in flat

    parts = query.split("UNION")
    assert len(parts) == len(M.DOCTYPES_USING_PERIODS)
    for name in M.DOCTYPES_USING_PERIODS:
        [part] = [p for p in parts if f"`tab{name}`" in p]
        assert "fiscal_period" in part and "fiscal_year" in part
        if doctypes[name].get("is_submittable"):
            assert "docstatus < 2" in part, f"{name}: cancelled documents must not count"
        else:
            assert "docstatus" not in part, f"{name}: not submittable, every row counts"


def test_periods_in_use_lock_flag():
    """lock=True makes every per-doctype SELECT a locking read (LOCK IN SHARE
    MODE). Under REPEATABLE READ a plain SELECT returns the transaction's
    snapshot, so a freeze gate would miss a document committed after it; a
    locking read returns the latest committed rows (review #191, 6). The
    default stays a plain read for callers that only look."""
    for kwargs, locked in (({}, False), ({"lock": False}, False), ({"lock": True}, True)):
        db = _DB([(3,), (None,)])
        M = _load(db)
        saved = sys.modules.get("frappe")
        frappe = types.ModuleType("frappe")
        frappe.db = db
        sys.modules["frappe"] = frappe
        try:
            result = M.periods_in_use(2026, **kwargs)
        finally:
            if saved is None:
                sys.modules.pop("frappe", None)
            else:
                sys.modules["frappe"] = saved

        assert result == {3}, kwargs
        assert len(db.calls) == 1, "must be one UNION query"
        query, values = db.calls[0]
        assert "2026" not in query, "the year must be a parameter, not formatted in"
        parts = query.split("UNION")
        assert len(parts) == len(M.DOCTYPES_USING_PERIODS), query
        for part in parts:
            if locked:
                assert part.strip().rstrip(")").rstrip().endswith("LOCK IN SHARE MODE"), part
            else:
                assert "LOCK" not in part and "FOR UPDATE" not in part, part


def test_connections_cover_period_doctypes():
    """The EPM Fiscal Year Connections tab must list every period-data
    doctype exactly once: fiscal_year on a document is an Int, a year's
    record name is its text, and MariaDB compares them equal, so the plain
    fieldname works. Leaving a doctype out would hide it from the dashboard
    a period-freeze conflict points people to (konsol#189)."""
    M = _load()
    dash_path = os.path.join(
        APP_DIR, "epm", "doctype", "epm_fiscal_year", "epm_fiscal_year_dashboard.py")
    spec = importlib.util.spec_from_file_location(
        "epm_fiscal_year_dashboard_under_test", dash_path)
    dash = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dash)
    data = dash.get_data()

    assert data["fieldname"] == "fiscal_year"
    items = [item for group in data["transactions"] for item in group["items"]]
    assert len(items) == len(set(items)), f"duplicate items across groups: {items}"
    assert set(items) == set(M.DOCTYPES_USING_PERIODS), (
        sorted(set(items) ^ set(M.DOCTYPES_USING_PERIODS)))


# ---- declare_years_in_use (konsol#189) --------------------------------------
# The one-off tool for stacks whose warehouse holds years no konsol document
# names. A stub frappe backed by in-memory tables and a stub konsol.clickhouse
# stay installed for the whole call; both record every call they get.

import ast  # noqa: E402

_WAREHOUSE_TABLE = "epm_gold.gold_trial_balance"


class _Refused(Exception):
    pass


class _Throw(Exception):
    pass


def _sbool(x):
    """frappe.utils.sbool, verbatim in behaviour."""
    try:
        val = x.lower()
        if val in ("true", "1"):
            return True
        if val in ("false", "0"):
            return False
        return x
    except Exception:  # noqa: BLE001
        return x


class _Site:
    """Stub frappe. ``used``: doctype -> [(fiscal_year, fiscal_period)]."""

    def __init__(self, used=None, roles=("System Manager",)):
        self.used = used or {}
        self.roles = set(roles)
        self.calls = []

    def writes(self):
        return [c for c in self.calls if c[0] in ("get_doc", "insert", "save")]

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")
        frappe.PermissionError = _Refused
        frappe.flags = types.SimpleNamespace()
        frappe.utils = types.SimpleNamespace(sbool=_sbool)

        def whitelist(*a, **k):
            return lambda fn: fn

        def only_for(roles, message=False):
            roles = [roles] if isinstance(roles, str) else list(roles)
            site.calls.append(("only_for", tuple(roles)))
            if not site.roles & set(roles):
                raise _Refused("not permitted")

        def throw(msg, *a, **k):
            site.calls.append(("throw", msg))
            raise _Throw(msg)

        def get_doc(arg, name=None):
            site.calls.append(("get_doc", arg.get("fiscal_year") if isinstance(arg, dict) else name))
            doc = types.SimpleNamespace(**arg) if isinstance(arg, dict) else types.SimpleNamespace()
            doc.flags = types.SimpleNamespace()
            doc.insert = lambda **k: site.calls.append(("insert", doc.fiscal_year))
            doc.save = lambda **k: site.calls.append(("save", doc.fiscal_year))
            return doc

        def table_exists(dt, *a, **k):
            return dt in site.used or dt == "Period Status" or dt.startswith("EPM Fiscal Year")

        def has_column(dt, col):
            return True

        def sql(query, values=None, as_dict=False, **k):
            q = " ".join(query.split())
            site.calls.append(("db.sql", q))
            if "`tabEPM Fiscal Year" in q or "`tabPeriod Status`" in q:
                return []
            for dt, pairs in site.used.items():
                if "`tab%s`" % dt in q:
                    if as_dict:
                        return [{"fiscal_year": y, "fiscal_period": p} for y, p in pairs]
                    return list(pairs)
            raise AssertionError("unexpected query: %s" % q)

        frappe.whitelist = whitelist
        frappe.only_for = only_for
        frappe.throw = throw
        frappe.get_doc = get_doc
        frappe.db = types.SimpleNamespace(table_exists=table_exists, has_column=has_column, sql=sql)
        return frappe


class _Warehouse:
    """Stub konsol.clickhouse: execute() answers FORMAT JSONCompact."""

    def __init__(self, rows=()):
        self.rows = [list(r) for r in rows]
        self.calls = []

    def module(self):
        wh = self
        mod = types.ModuleType("konsol.clickhouse")

        def execute(sql, params=None):
            wh.calls.append(sql)
            return json.dumps({"meta": [], "data": wh.rows})

        mod.execute = execute
        return mod


def _declare(site, warehouse, **kwargs):
    names = ("frappe", "konsol.clickhouse")
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules["frappe"] = site.module()
    sys.modules["konsol.clickhouse"] = warehouse.module()
    try:
        spec = importlib.util.spec_from_file_location("fiscal_calendar_declare_under_test", MODULE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.declare_years_in_use(**kwargs)
    finally:
        for n in names:
            if saved[n] is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = saved[n]


def _created(summary):
    return {y["fiscal_year"]: y["periods"] for y in summary["create"]}


def test_declare_dry_run_changes_nothing():
    used = {"Trial Balance Submission": [(2025, 3), (2025, 14)], "IC Balance": [(2024, 1)]}
    for flags in ({}, {"dry_run": "1"}, {"dry_run": "true"}, {"dry_run": True}):
        site = _Site(used=used)
        summary = _declare(site, _Warehouse(), **flags)
        assert not site.writes(), (flags, site.writes())
        assert summary["dry_run"] is True, flags
        # Monthly (12) with OPN and CLS is 14 rows; 2025's P14 adds one.
        assert _created(summary) == {2024: 14, 2025: 15}, (flags, summary)
        assert summary["conflicts"] == [] and summary["moves"] == [], summary


def test_declare_writes_when_dry_run_is_off():
    used = {"Trial Balance Submission": [(2025, 3)]}
    for flag in ("0", "false", 0, False):
        site = _Site(used=used)
        summary = _declare(site, _Warehouse(), dry_run=flag)
        assert [c for c in site.calls if c[0] == "insert"] == [("insert", 2025)], (flag, site.calls)
        assert summary["dry_run"] is False and _created(summary) == {2025: 14}, summary


def test_declare_conflicts_throw_when_writing_and_are_listed_in_a_dry_run():
    used = {"IC Balance": [(2025, 300)]}
    site = _Site(used=used)
    summary = _declare(site, _Warehouse())
    assert any("period 300" in c for c in summary["conflicts"]), summary
    assert not site.writes()

    site = _Site(used=used)
    try:
        _declare(site, _Warehouse(), dry_run=0)
    except _Throw as exc:
        assert "period 300" in str(exc), exc
    else:
        raise AssertionError("conflicts must stop a real run")
    assert not site.writes(), site.writes()


def test_declare_reads_warehouse_only_when_asked():
    used = {"Trial Balance Submission": [(2025, 3)]}
    # 2023 lives only in the warehouse; a 64-bit column arrives quoted.
    rows = [[2023, 5], ["2025", "3"]]
    for flag in (None, "0", "false", False):
        site, wh = _Site(used=used), _Warehouse(rows)
        kwargs = {} if flag is None else {"include_warehouse": flag}
        summary = _declare(site, wh, **kwargs)
        assert wh.calls == [], (flag, wh.calls)
        assert _created(summary) == {2025: 14}, (flag, summary)

    for flag in ("1", "true", True):
        site, wh = _Site(used=used), _Warehouse(rows)
        summary = _declare(site, wh, include_warehouse=flag)
        assert len(wh.calls) == 1 and _WAREHOUSE_TABLE in wh.calls[0], wh.calls
        assert "DISTINCT fiscal_year, fiscal_period" in wh.calls[0], wh.calls
        assert _created(summary) == {2023: 14, 2025: 14}, (flag, summary)
        assert not site.writes()


def test_declare_sm_only_post():
    with open(MODULE) as f:
        tree = ast.parse(f.read())
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "declare_years_in_use"), None)
    assert fn is not None, "no top-level def declare_years_in_use"
    [dec] = fn.decorator_list
    assert isinstance(dec, ast.Call) and ast.unparse(dec.func) == "frappe.whitelist", ast.unparse(dec)
    kw = {k.arg: ast.literal_eval(k.value) for k in dec.keywords}
    assert kw == {"methods": ["POST"]}, kw
    assert not dec.args, ast.unparse(dec)

    site, wh = _Site(used={"Trial Balance Submission": [(2025, 3)]}, roles=("Accounts User",)), _Warehouse([[2023, 1]])
    try:
        _declare(site, wh, include_warehouse=1, dry_run=0)
    except _Refused:
        pass
    else:
        raise AssertionError("a non-System Manager must be refused")
    assert site.calls == [("only_for", ("System Manager",))], site.calls
    assert wh.calls == []
