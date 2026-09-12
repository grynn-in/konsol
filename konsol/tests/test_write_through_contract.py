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

def test_unset_fields_insert_the_column_default_never_null():
    """Every one of these columns is non-Nullable in init-db.sql. A literal NULL
    only survives while ClickHouse keeps input_format_null_as_default on; with
    it off the INSERT is rejected AFTER _sync_table_inner has already TRUNCATEd,
    so one publish of a mapping with a blank entity empties the crosswalk.

    Writing '' instead fixes that for String columns and breaks every Date one —
    epm_staging.ownership_periods stopped syncing entirely on the live stack
    because an Ownership Period with no acquisition_date sent '' into a Date.
    DEFAULT means "whatever this column declares" and is right for both."""
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
    assert "('dim_cost_center', DEFAULT)" in inserts[0], inserts[0]


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
        # F2: epm_gold.consolidation_groups used to be created by `dbt seed`
        # from the SAME relation konsol writes; the seed is deleted, so this is
        # now the only thing that creates it.
        "epm_gold.consolidation_groups",
        "epm_staging.consolidation_ancestry",
        # listed because _RETIRED_COLUMNS ALTERs it — an ALTER against a table
        # that does not exist fails, and so does the sync behind it
        "epm_staging.consolidation_hierarchy",
        # konsolidat#146: these were created by a dbt seed that turned out to be
        # the SAME relation konsol writes. The seeds are deleted, so nothing
        # else creates them. epm_gold.currencies joins them because the ISO list
        # moved into the app as its own doctype.
        "epm_gold.spread_profiles",
        "epm_gold.scenario_definitions",
        "epm_gold.currencies",
        "epm_gold.entity_fiscal_calendars",
        "epm_gold.budget_annual_input",
        # a konsol write-through that nothing ever created — the cause of the
        # long-standing gold_spread_budget build failure
        "epm_gold.budget_monthly_input",
    }
    sql = []
    m.execute = lambda s, params=None: sql.append(s) or ""
    m.ensure_reference_tables()
    for db in ("epm_staging", "epm_gold"):
        assert any(s.startswith(f"CREATE DATABASE IF NOT EXISTS {db}") for s in sql), db
    for table in m._REFERENCE_TABLE_DDL:
        assert any(s.startswith(f"CREATE TABLE IF NOT EXISTS {table} (") for s in sql), table


def test_retired_columns_are_dropped_not_left_defaulting():
    """A column the writer stopped sending keeps its last value or its default
    forever — an ownership percentage that no longer updates is exactly the
    second source of truth F2 deletes."""
    m, _ = _load_clickhouse()
    assert m._RETIRED_COLUMNS["epm_gold.consolidation_groups"] == [
        "ownership_pct", "consolidation_method"]
    assert m._RETIRED_COLUMNS["epm_staging.consolidation_hierarchy"] == [
        "effective_ownership_pct"]
    sql = []
    m.execute = lambda s, params=None: sql.append(s) or ""
    m.ensure_reference_tables()
    for table, cols in m._RETIRED_COLUMNS.items():
        assert table in m._REFERENCE_TABLE_DDL, (
            f"{table} is ALTERed but never created — the ALTER, and the sync "
            f"behind it, fail on a volume that has never run dbt")
        for col in cols:
            assert f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}" in sql, (table, col)
        create = sql.index(f"CREATE TABLE IF NOT EXISTS {table} {m._REFERENCE_TABLE_DDL[table]}")
        for col in cols:
            assert create < sql.index(
                f"ALTER TABLE {table} DROP COLUMN IF EXISTS {col}"), (table, col)

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


# --- what a value looks like in the INSERT ----------------------------------

def test_datetimes_render_at_one_second_precision():
    """ClickHouse DateTime has one-second resolution and rejects a microsecond
    timestamp with a 400. Frappe's now_datetime() carries microseconds, so
    epm_staging.allocation_runs never reconciled — invisibly, because
    reconcile_all reported frappe.db.count() when a sync returned nothing.
    Reproduced on the running stack: the same row inserts once truncated."""
    m, _ = _load_clickhouse()
    assert m._sql_value(datetime.datetime(2026, 6, 21, 16, 23, 8, 118700)) == \
        "'2026-06-21 16:23:08'"
    assert m._sql_value(datetime.date(2026, 6, 21)) == "'2026-06-21'"


def test_sql_value_still_escapes_and_passes_numbers_raw():
    m, _ = _load_clickhouse()
    assert m._sql_value(None) == "DEFAULT"
    assert m._sql_value(12) == "12"
    assert m._sql_value(1.5) == "1.5"
    assert m._sql_value("O'Brien") == "'O\\'Brien'"
    assert m._sql_value("back\\slash") == "'back\\\\slash'"


def test_both_insert_paths_share_one_value_formatter():
    """sync_table and sync_rows had the same eight-line quoting block copied;
    only one of them can be fixed at a time that way."""
    src = open(CH_PATH).read()
    assert src.count("_sql_value(v) for v in row") == 2
    assert src.count('vals.append("NULL")') == 0


def test_a_date_column_survives_an_unset_field():
    """The regression that took epm_staging.ownership_periods offline: an
    Ownership Period with no acquisition_date. '' is not a Date."""
    m, _ = _load_clickhouse()
    assert m._sql_value(None) != "''"
    assert m._sql_value(None) != "NULL"


def test_no_controller_still_writes_a_deleted_seeds_relation():
    """konsolidat#146: a dbt seed materialises into epm_gold, so a seed and a
    write-through targeting `epm_gold.<same name>` are one ClickHouse table with
    two writers — `dbt seed` and `bench migrate` overwrite each other, and
    publishing the doctype fires the governed build that reverts the publish.

    The three relations whose dbt readers moved to the staging tables have no
    write-through left at all; the two that keep a reader are created here.
    """
    import pathlib

    gone = ("epm_gold.allocation_rules", "epm_gold.ic_elimination_rules",
            "epm_gold.consolidation_adjustments")
    offenders = []
    for path in pathlib.Path(APP_DIR).rglob("*.py"):
        if "/tests/" in str(path) or path.name == "clickhouse.py":
            continue  # clickhouse.py names them in _RETIRED_TABLES, to DROP them
        text = path.read_text()
        for relation in gone:
            # a mention inside a comment explaining the removal is fine
            for line in text.splitlines():
                if relation in line and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.name}: {line.strip()[:70]}")
    assert not offenders, offenders

    # and the only place that may name them is the drop list
    m, _ = _load_clickhouse()
    for relation in gone:
        assert relation in m._RETIRED_TABLES, relation

    m, _ = _load_clickhouse()
    for kept in ("epm_gold.spread_profiles", "epm_gold.scenario_definitions"):
        assert kept in m._REFERENCE_TABLE_DDL, kept


class _StagingOnly:
    """A controller with a field-mapped STAGING table and no gold counterpart."""
    CH_STAGING_TABLE = "epm_staging.ic_elimination_rules"
    CH_STAGING_FIELD_MAP = {"rule_id": "rule_id"}


class _Computed:
    """Rows are computed, so reconcile calls resync_staging()."""
    CH_STAGING_TABLE = "epm_staging.reporting_hierarchies"

    @classmethod
    def resync_staging(cls, force=False):
        return 0


def test_a_staging_only_controller_is_still_reconciled():
    """konsolidat#146 deleted the legacy gold write-through from Allocation
    Rule, IC Elimination Rule and Consolidation Adjustment — and all three
    silently dropped out of reconcile with it, because discovery's staging arm
    required resync_staging() while these carry a plain CH_STAGING_FIELD_MAP.
    Their staging tables would then never be repaired after a fixture import,
    which is the drift reconcile exists for. Caught by wiping
    epm_staging.ic_elimination_rules and finding a migrate did not refill it.

    reconcile_all's body already handled this shape; only the discovery
    predicate did not reach it.
    """
    m, frappe = _load_clickhouse(controllers={
        "IC Elimination Rule": _StagingOnly,
        "Reporting Hierarchy": _Computed,
        "Dimension Mapping": _Governed,
    })
    def get_all(doctype, filters=None, fields=None, pluck=None, **kw):
        if doctype == "Module Def":
            return ["Consolidation"] if pluck else [{"name": "Consolidation"}]
        if doctype == "DocType":
            names = ["IC Elimination Rule", "Reporting Hierarchy", "Dimension Mapping"]
            return names if pluck else [{"name": n} for n in names]
        return []
    frappe.get_all = get_all

    found = m._write_through_doctypes()
    assert "IC Elimination Rule" in found, (
        "a field-mapped staging table with no gold counterpart must reconcile")
    assert "Reporting Hierarchy" in found
    assert "Dimension Mapping" in found


def test_abandoned_relations_are_dropped_not_left_looking_live():
    """konsolidat#146 removed the last writer from six epm_gold relations.
    Nothing truncates a table once its writer is gone, so each would sit there
    holding whichever of `dbt seed` and `bench migrate` wrote last — stale
    configuration that reads as current, which is the confusion this work
    exists to remove."""
    m, _ = _load_clickhouse()
    assert set(m._RETIRED_TABLES) == {
        "epm_gold.allocation_rules",
        "epm_gold.ic_elimination_rules",
        "epm_gold.consolidation_adjustments",
        "epm_gold.allocation_drivers_headcount",
        "epm_gold.allocation_drivers_revenue",
        "epm_gold.allocation_drivers_sqm",
    }
    # nothing may be both dropped and created
    assert not set(m._RETIRED_TABLES) & set(m._REFERENCE_TABLE_DDL)
    # the watermark cleanup reads first and only deletes what is there, because
    # ClickHouse logs a mutation for an ALTER ... DELETE even when it matches
    # nothing — six per migrate, forever. Pretend every stamp is present.
    sql = []

    def execute(statement, params=None):
        sql.append(statement)
        if statement.startswith("SELECT DISTINCT table_name"):
            return "\n".join(m._RETIRED_TABLES)
        return ""

    m.execute = execute
    m.ensure_reference_tables()
    for table in m._RETIRED_TABLES:
        assert f"DROP TABLE IF EXISTS {table}" in sql, table
        # the watermark row has to go too, or assert_staging_not_stale reports
        # the frozen stamp as LAGGING and fails every build
        assert any(f"DELETE WHERE table_name = '{table}'" in s for s in sql), table

    # ...and nothing is deleted when no stamp exists
    sql2 = []
    m.execute = lambda statement, params=None: sql2.append(statement) or ""
    m.ensure_reference_tables()
    assert not any("DELETE WHERE table_name" in s for s in sql2), (
        "a no-op ALTER ... DELETE still records a mutation")


def test_a_write_through_controller_deletes_with_after_delete():
    """`sync_doctype` re-sends the whole table from frappe.get_all, and on_trash
    runs BEFORE the row is removed — so syncing there re-publishes the row being
    deleted and the warehouse keeps it. Measured on a pre-existing offender:
    deleting one Spread Profile took Frappe 24 -> 23 and ClickHouse 24 -> 24.

    The rule is already documented in test_connector_registry and
    test_dimension_mapping; the trap is that grepping for precedent finds the
    WRONG answer, because ten controllers use on_trash and four of them appear
    to work (they are submittable, so resolve_sync_filters had already excluded
    the row). Three controllers added in konsolidat#146 copied that pattern.

    KNOWN_BAD is the pre-existing set, tracked in konsol#120. It may shrink,
    never grow.
    """
    import glob
    import json
    import re

    KNOWN_BAD = {
        # non-submittable: the bug is live for these
        "Allocation Driver", "Allocation Rule", "Consolidation Group",
        "IC Elimination Rule", "Scenario", "Spread Profile",
        # submittable: masked by the docstatus=1 filter, not by design
        "Consolidation Adjustment", "Historical Equity Rate", "IC Balance",
        "Ownership Period",
    }

    offenders = set()
    for py in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.py")):
        if py.endswith("__init__.py"):
            continue
        src = open(py).read()
        if "sync_doctype" not in src and "sync_table" not in src:
            continue
        meta_path = py[:-3] + ".json"
        if not os.path.exists(meta_path):
            continue
        if not re.search(r"def on_trash\b", src):
            continue
        body = src.split("def on_trash")[1].split("\n    def ")[0]
        if not any(token in body for token in ("sync_doctype", "sync_table", "_sync")):
            continue  # on_trash for something else entirely is fine
        offenders.add(json.load(open(meta_path))["name"])

    new = offenders - KNOWN_BAD
    assert not new, (
        f"{sorted(new)} publish to ClickHouse from on_trash — use after_delete, "
        f"which runs after the row is gone. See konsol#120.")
    assert offenders <= KNOWN_BAD
