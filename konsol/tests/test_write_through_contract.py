"""Behavioural tests for the one write-through mechanism (F3 review).

publish() and reconcile_all() used to decide independently which rows belong in
ClickHouse, and they disagreed: publish() sent Published rows, reconcile sent
every row of a non-submittable doctype, so one `bench migrate` re-filled
epm_staging.cash_flow_categories with Drafts and Inactives that no dbt consumer
filters. These exercise the real functions (with frappe stubbed) rather than
grepping the source, because the bug was in what the code *did*, not in whether
a call site existed.
"""
import datetime
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")

try:
    import requests  # noqa: F401 — clickhouse.py imports it at module level
except ModuleNotFoundError:  # host runner without requests — runner skips
    raise ImportError("needs requests")


class _Logger:
    def __init__(self):
        self.messages = []

    def warning(self, msg, *a, **k):
        self.messages.append(str(msg))

    error = warning
    info = warning


def _load_clickhouse(controllers=None, docs=None, submittable=()):
    """Load clickhouse.py against a stub frappe and return (module, frappe)."""
    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(
        in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe._logger = _Logger()
    frappe.logger = lambda: frappe._logger
    frappe.publish_realtime = lambda *a, **k: None
    frappe.get_meta = lambda dt: types.SimpleNamespace(
        is_submittable=1 if dt in submittable else 0)
    frappe.calls = []

    def get_all(doctype, filters=None, fields=None, pluck=None,
                limit_page_length=None, **kw):
        frappe.calls.append({"doctype": doctype, "filters": filters})
        return list((docs or {}).get(doctype, []))

    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(count=lambda *a, **k: 99)

    model = types.ModuleType("frappe.model")
    base_document = types.ModuleType("frappe.model.base_document")
    base_document.get_controller = lambda dt: (controllers or {})[dt]
    model.base_document = base_document
    frappe.model = model

    # The stubs stay in sys.modules: clickhouse._controller() imports
    # frappe.model.base_document at CALL time (v15 has no frappe.get_controller),
    # so removing them again would make every test here skip with a
    # ModuleNotFoundError instead of running. Each test installs its own set
    # immediately before using it.
    sys.modules.update({"frappe": frappe, "frappe.model": model,
                        "frappe.model.base_document": base_document})
    spec = importlib.util.spec_from_file_location(
        "konsol_clickhouse_under_test", CH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, frappe


class _Governed:
    """A controller that publishes deliberately, like Dimension Mapping."""
    CH_TABLE = "epm_staging.dimension_mappings"
    CH_SYNC_FILTERS = {"status": "Published"}
    CH_FIELD_MAP = {"dimension": "dimension", "entity": "entity"}


class _Submittable:
    CH_TABLE = "epm_staging.ownership_periods"
    CH_FIELD_MAP = {"entity": "entity"}


class _Plain:
    CH_TABLE = "gold.consolidation_groups"
    CH_FIELD_MAP = {"entity": "entity"}


# --- which rows belong in the warehouse: one answer, both paths -------------

def test_declared_filters_win_over_docstatus():
    """A doctype that declares CH_SYNC_FILTERS uses them even if it is
    submittable — the declaration is the governed answer."""
    m, _ = _load_clickhouse(
        controllers={"Dimension Mapping": _Governed},
        submittable=("Dimension Mapping",))
    assert m.resolve_sync_filters("Dimension Mapping") == {"status": "Published"}


def test_submittable_without_declaration_syncs_submitted_only():
    m, _ = _load_clickhouse(
        controllers={"Ownership Period": _Submittable},
        submittable=("Ownership Period",))
    assert m.resolve_sync_filters("Ownership Period") == {"docstatus": 1}


def test_plain_doctype_syncs_everything():
    m, _ = _load_clickhouse(controllers={"Consolidation Group": _Plain})
    assert m.resolve_sync_filters("Consolidation Group") is None


def test_reconcile_uses_the_declared_filters_not_every_row():
    """The bug: reconcile_all synced ALL rows of Dimension Mapping / Cash Flow
    Category because they are not submittable, so a migrate re-filled the table
    with Draft and Inactive rows. Both paths read CH_SYNC_FILTERS now."""
    m, frappe = _load_clickhouse(controllers={"Dimension Mapping": _Governed})
    m.execute = lambda *a, **k: ""
    m.sync_doctype("Dimension Mapping", _Governed.CH_TABLE, _Governed.CH_FIELD_MAP,
                   force=True)
    fetched = [c for c in frappe.calls if c["doctype"] == "Dimension Mapping"]
    assert fetched and fetched[0]["filters"] == {"status": "Published"}


def test_filters_are_copied_not_shared():
    """resolve_sync_filters must not hand out the class attribute itself — a
    caller mutating it would silently change every later sync."""
    m, _ = _load_clickhouse(controllers={"Dimension Mapping": _Governed})
    got = m.resolve_sync_filters("Dimension Mapping")
    got["status"] = "Draft"
    assert _Governed.CH_SYNC_FILTERS == {"status": "Published"}


# --- what reaches the INSERT ------------------------------------------------

def test_unset_fields_insert_empty_string_never_null():
    """Every one of these columns is non-Nullable in init-db.sql. A literal
    NULL only survives because ClickHouse defaults input_format_null_as_default
    on; with it off the INSERT is rejected AFTER _sync_table_inner has already
    TRUNCATEd, so one publish of a mapping with a blank entity empties the
    whole crosswalk."""
    m, _ = _load_clickhouse(
        controllers={"Dimension Mapping": _Governed},
        docs={"Dimension Mapping": [
            {"dimension": "dim_cost_center", "entity": None,
             "modified": datetime.datetime(2026, 9, 11, 8, 0, 0)},
        ]})
    sql = []
    m.execute = lambda s, params=None: sql.append(s) or ""
    m.sync_doctype("Dimension Mapping", _Governed.CH_TABLE, _Governed.CH_FIELD_MAP,
                   force=True)

    inserts = [s for s in sql if s.startswith(f"INSERT INTO {_Governed.CH_TABLE}")]
    assert inserts, sql
    assert "NULL" not in inserts[0], inserts[0]
    assert "('dim_cost_center', '')" in inserts[0], inserts[0]


# --- reconcile reports what actually happened -------------------------------

def test_reconcile_records_none_when_the_write_failed():
    """It used to substitute frappe.db.count(doctype) for a failed sync, so a
    reconcile that reached nothing still logged a row count per table and the
    migrate read as a clean repair."""
    m, frappe = _load_clickhouse()
    synced = {}
    m._record(synced, "epm_staging.dimension_mappings", None)
    assert synced == {"epm_staging.dimension_mappings": None}
    assert any("NOT synced" in msg for msg in frappe._logger.messages)

    m._record(synced, "epm_staging.cash_flow_categories", 12)
    assert synced["epm_staging.cash_flow_categories"] == 12


# --- the upgrade path for stacks that predate F3 ----------------------------

def test_reference_tables_are_bootstrapped_before_reconciling():
    """init-db.sql runs only against an empty ClickHouse volume, so every stack
    older than F3 has no epm_staging.dimension_mappings at all and every sync
    fails until someone runs the DDL by hand."""
    m, _ = _load_clickhouse()
    assert set(m._REFERENCE_TABLE_DDL) == {
        "epm_staging.dimension_mappings",
        "epm_staging.cash_flow_categories",
        "epm_staging.reporting_hierarchies",
    }
    sql = []
    m.execute = lambda s, params=None: sql.append(s) or ""
    m.ensure_reference_tables()
    assert any(s.startswith("CREATE DATABASE IF NOT EXISTS epm_staging") for s in sql)
    for table in m._REFERENCE_TABLE_DDL:
        assert any(s.startswith(f"CREATE TABLE IF NOT EXISTS {table} (") for s in sql), table

    src = open(CH_PATH).read()
    body = src.split("def reconcile_all")[1].split("\ndef ")[0]
    assert "ensure_reference_tables()" in body


def test_bootstrap_ddl_never_fails_a_migrate():
    """ClickHouse being down must not break bench migrate."""
    m, frappe = _load_clickhouse()

    def boom(*a, **k):
        raise RuntimeError("connection refused")

    m.execute = boom
    m.ensure_reference_tables()
    assert any("bootstrap skipped" in msg for msg in frappe._logger.messages)


# --- the shared lifecycle ---------------------------------------------------

def _governed_reference_src():
    with open(os.path.join(APP_DIR, "governed_reference.py")) as f:
        return f.read()


def test_one_sync_call_for_the_whole_lifecycle():
    """Six copies of the filtered sync across two controllers is what let the
    paths drift; there is one now, and the lifecycle methods route through it."""
    src = _governed_reference_src()
    assert "from konsol.clickhouse import sync_doctype" in src
    assert src.count("sync_doctype(") == 1, "one sync call, not one per lifecycle hook"
    for method in ("def on_update", "def publish", "def unpublish",
                   "def after_delete", "def _resync"):
        assert method in src, method


def test_scripted_insert_of_a_published_row_syncs():
    """`frappe.get_doc({...,"status":"Published"}).insert()` never called
    publish(), so the row sat in Frappe and not in ClickHouse until the next
    migrate reconciled it. on_update covers it; a row that is neither now nor
    previously Published costs nothing."""
    src = _governed_reference_src()
    body = src.split("def on_update")[1].split("\n    @")[0]
    assert "get_doc_before_save()" in body
    assert "self.status == _PUBLISHED" in body
    assert "before.status == _PUBLISHED" in body
    assert "self._resync()" in body
