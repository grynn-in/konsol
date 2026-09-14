"""epm_staging.fiscal_periods (konsol#189): the declared fiscal periods, one
row per Fiscal Year Period with the effective status. konsolidat reads it for
period dates; identical to konsolidat's clickhouse/init-db.sql, keep them
identical.

This reads clickhouse.py as source (AST), without importing frappe — see
test_main_account.py's test_staging_ddl_character_for_character for the same
approach.

fiscal_period_rows() itself is loaded by path against a stub frappe, the same
way test_fiscal_calendar.py loads fiscal_calendar.py; sys.modules is restored.
"""
import ast
import importlib.util
import os
import sys
import types
from contextlib import contextmanager
from datetime import date

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")
FISCAL_CALENDAR_PATH = os.path.join(APP_DIR, "fiscal_calendar.py")

#: konsolidat's clickhouse/init-db.sql line, as pinned (konsol#189).
INIT_DB_LINE = (
    "CREATE TABLE IF NOT EXISTS epm_staging.fiscal_periods "
    "(fiscal_year UInt16, fiscal_period UInt8, period_code String, "
    "period_label String, period_type String, start_date Date, end_date Date, "
    "quarter String, status String) "
    "ENGINE = MergeTree ORDER BY (fiscal_year, fiscal_period);"
)


def _literal(name):
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == name)


def _ddl():
    return {k: _literal(k) for k in ("_REFERENCE_TABLE_DDL", "_ADDED_COLUMNS", "_RETIRED_TABLES")}


def test_fiscal_periods_ddl_matches_init_db():
    body = _ddl()["_REFERENCE_TABLE_DDL"]["epm_staging.fiscal_periods"]
    assert f"CREATE TABLE IF NOT EXISTS epm_staging.fiscal_periods {body};" == INIT_DB_LINE


def test_new_table_ships_complete_not_via_added_columns():
    consts = _ddl()
    assert "epm_staging.fiscal_periods" not in consts["_ADDED_COLUMNS"]
    assert "epm_staging.fiscal_periods" not in consts["_RETIRED_TABLES"]


def test_reference_table_ddl_assigned_exactly_once():
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    assignments = [n for n in ast.walk(tree) if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", None) == "_REFERENCE_TABLE_DDL" for t in n.targets)]
    assert len(assignments) == 1


def _ddl_columns():
    """Column names of epm_staging.fiscal_periods, in DDL order."""
    body = _ddl()["_REFERENCE_TABLE_DDL"]["epm_staging.fiscal_periods"]
    cols = body.split("(", 1)[1].split(")", 1)[0]
    return [c.strip().split()[0] for c in cols.split(",")]


# ---- fiscal_calendar.fiscal_period_rows() ------------------------------------


class _DB:
    """Stand-in for frappe.db: sql() returns pre-baked joined rows."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def sql(self, query, values=None, *args, **kwargs):
        self.calls.append((query, values, kwargs))
        return self.rows


@contextmanager
def _load(db):
    """Install a stub frappe, load fiscal_calendar.py against it, and keep the
    stub in place for the caller's `with` block — fiscal_period_rows() imports
    frappe lazily at call time, so the stub must still be installed when the
    caller invokes it, not just while this module executes. Restores
    sys.modules on the way out so later test files aren't affected.
    """
    saved = sys.modules.get("frappe")
    frappe = types.ModuleType("frappe")
    frappe.db = db
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location(
            "fiscal_calendar_under_test_warehouse", FISCAL_CALENDAR_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved


#: A joined row exactly as fiscal_period_rows()'s single frappe.db.sql call
#: would produce it: one EPM Fiscal Year Period, plus its parent year's status
#: aliased separately from the row's own status.
def _joined_row(**overrides):
    row = {
        "fiscal_year": 2026,
        "fiscal_period": 1,
        "period_code": "P01",
        "period_label": "January",
        "period_type": "Month",
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 1, 31),
        "quarter": "Q1",
        "year_status": "Open",
        "row_status": "Open",
    }
    row.update(overrides)
    return row


def test_rows_shape_matches_ddl_columns():
    db = _DB([_joined_row()])
    with _load(db) as M:
        rows = M.fiscal_period_rows()

    assert len(db.calls) == 1, "must be one join query"
    assert len(rows) == 1
    assert list(rows[0].keys()) == _ddl_columns()


def test_effective_status_published():
    db = _DB([
        # a Closed year with an Open row publishes the stricter, Closed status
        _joined_row(fiscal_period=1, period_code="P01", period_label="January",
                    year_status="Closed", row_status="Open"),
        # a blank label publishes the code
        _joined_row(fiscal_period=2, period_code="P02", period_label="",
                    quarter="", year_status="Open", row_status="Open"),
    ])
    with _load(db) as M:
        rows = M.fiscal_period_rows()

    assert rows[0]["status"] == "Closed"
    assert rows[1]["period_label"] == "P02"
    assert rows[1]["quarter"] == ""


# ---- write-through: EPM Fiscal Year -> epm_staging.fiscal_periods ------------
# Same pattern as Entity (konsol#110/#124): the hooks queue the sync with
# clickhouse.after_commit_once, once per transaction; reconcile_all finds the
# controller by CH_STAGING_TABLE + resync_staging and calls it with force=True.

CONTROLLER = os.path.join(APP_DIR, "epm", "doctype", "epm_fiscal_year", "epm_fiscal_year.py")

#: What the stub fiscal_period_rows() returns: one row, keys in DDL order.
_PUBLISHED = {
    "fiscal_year": 2026, "fiscal_period": 1, "period_code": "P01",
    "period_label": "January", "period_type": "Regular",
    "start_date": date(2026, 1, 1), "end_date": date(2026, 1, 31),
    "quarter": "Q1", "status": "Open",
}


def _clickhouse_stub():
    """konsol.clickhouse as the controller sees it: after_commit_once records
    what was queued; sync_table records what was written."""
    ch = types.ModuleType("konsol.clickhouse")
    ch.queued, ch.synced = [], []
    ch.after_commit_once = lambda key, fn: ch.queued.append((key, fn))

    def sync_table(table, columns, rows, source_max_modified=None, force=False):
        ch.synced.append((table, list(columns), [list(r) for r in rows], force))
        return len(rows)

    def sync_doctype(*a, **k):
        raise AssertionError("fiscal_periods is computed; it is not a field-mapped sync")

    ch.sync_table = sync_table
    ch.sync_doctype = sync_doctype
    return ch


@contextmanager
def _swap(mods):
    saved = {name: sys.modules.get(name) for name in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


@contextmanager
def _controller(clickhouse):
    """epm_fiscal_year.py loaded against a stub frappe and stub konsol modules;
    the stubs stay installed for the caller's block (the controller imports
    konsol.clickhouse at call time)."""
    names = ("frappe", "frappe.model", "frappe.model.document", "frappe.utils", "konsol",
             "konsol.fiscal_calendar", "konsol.fiscal_patterns_model",
             "konsol.fiscal_status_model", "konsol.fiscal_structure_model")
    mods = {n: types.ModuleType(n) for n in names}
    mods["konsol.clickhouse"] = clickhouse
    frappe = mods["frappe"]
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.utils = mods["frappe.utils"]
    for fn in ("cint", "get_datetime", "getdate"):
        setattr(mods["frappe.utils"], fn, lambda v=None: v)

    class Document:
        def __init__(self, **fields):
            self.__dict__.update(fields)

    mods["frappe.model.document"].Document = Document
    konsol = mods["konsol"]
    konsol.__path__ = []
    for n in names[5:] + ("konsol.clickhouse",):
        setattr(konsol, n.rsplit(".", 1)[1], mods[n])
    calendar = mods["konsol.fiscal_calendar"]
    calendar.fiscal_period_rows = lambda: [dict(_PUBLISHED)]
    calendar.periods_in_use = lambda fiscal_year, lock=False: set()
    frappe.db = types.SimpleNamespace(sql=lambda *a, **k: [])   # on_trash's year-row lock
    with _swap(mods):
        spec = importlib.util.spec_from_file_location("epm_fiscal_year_under_test_wh", CONTROLLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


@contextmanager
def _clickhouse(controllers):
    """clickhouse.py loaded against a stub frappe whose konsol doctypes are
    `controllers` (as test_write_through_contract._load_clickhouse does)."""
    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(
        in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe.logger = lambda *a, **k: types.SimpleNamespace(
        warning=lambda *a, **k: None, error=lambda *a, **k: None)
    frappe.publish_realtime = lambda *a, **k: None
    frappe.get_meta = lambda dt: types.SimpleNamespace(is_submittable=0)

    def get_all(doctype, filters=None, fields=None, pluck=None, **kw):
        if doctype == "Module Def":
            return ["EPM"]
        if doctype == "DocType":
            return list(controllers)
        return []

    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(count=lambda *a, **k: 99)
    model = types.ModuleType("frappe.model")
    base_document = types.ModuleType("frappe.model.base_document")
    base_document.get_controller = lambda dt: controllers[dt]
    model.base_document = base_document
    frappe.model = model
    with _swap({"frappe": frappe, "frappe.model": model,
                "frappe.model.base_document": base_document}):
        spec = importlib.util.spec_from_file_location("clickhouse_under_test_wh", CH_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module


def test_sync_is_after_commit_once():
    ch = _clickhouse_stub()
    with _controller(ch) as M:
        doc = M.EPMFiscalYear(name="2026", fiscal_year=2026, periods=[])
        doc.on_update()
        doc.after_delete()
        assert ch.synced == [], "nothing is written inside the transaction"
        keys = [k for k, _ in ch.queued]
        assert len(keys) == 2 and keys[0] == keys[1], keys
        assert keys[0][0] == "fiscal_periods", keys

        # on_trash runs before the delete: it only refuses, it never syncs
        doc.on_trash()
        assert len(ch.queued) == 2, "the delete sync belongs in after_delete, not on_trash"

        ch.queued[0][1]()   # the commit
    assert ch.synced == [("epm_staging.fiscal_periods", _ddl_columns(),
                          [list(_PUBLISHED.values())], False)]


def test_reconcile_discovers_fiscal_year():
    ch = _clickhouse_stub()
    with _controller(ch) as M:
        cls = M.EPMFiscalYear
        assert cls.CH_STAGING_TABLE == "epm_staging.fiscal_periods"
        assert cls.CH_STAGING_TABLE in _ddl()["_REFERENCE_TABLE_DDL"]
        with _clickhouse({"EPM Fiscal Year": cls}) as m:
            assert m._write_through_doctypes() == ["EPM Fiscal Year"]
            m.ensure_reference_tables = lambda: None
            synced = m.reconcile_all()
    assert synced == {"epm_staging.fiscal_periods": 1}
    assert ch.synced == [("epm_staging.fiscal_periods", _ddl_columns(),
                          [list(_PUBLISHED.values())], True)], "reconcile forces the sync"
