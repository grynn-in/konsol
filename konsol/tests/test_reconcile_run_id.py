"""reconcile_all() stamps one run id on every watermark it writes
(grynn-in/konsolidat#246, option #246-1; Deepak Pai, 11 Oct 2026).

konsolidat's assert_staging_not_stale used to call a table LAGGING when its
watermark was more than 24h older than the NEWEST watermark of any table. One
write-through edit (a Reporting Hierarchy publish) therefore made every
untouched table look stale: measured on konsolidat.local, 26 false LAGGING rows.
Decided: reconcile_all() stamps a run id on every watermark it writes, and
LAGGING means a table missing from the latest reconcile run. These tests pin the
konsol half: what reconcile writes, what a write-through writes, and the
watermark DDL that keeps both rows.

Rejected: #246-2 (drop LAGGING) and #246-3 (raise the lag hours).

They exercise the real functions with frappe stubbed, like
test_write_through_contract.py.
"""
import importlib.util
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")

try:
    import requests  # noqa: F401 — clickhouse.py imports it at module level
except ModuleNotFoundError:  # host runner without requests — runner skips
    raise ImportError("needs requests")

WATERMARK = "epm_staging.sync_watermark"


class _Logger:
    def __init__(self):
        self.messages = []

    def warning(self, msg, *a, **k):
        self.messages.append(str(msg))

    error = warning
    info = warning
    exception = warning


class _A:
    CH_TABLE = "epm_staging.zz_a"
    CH_FIELD_MAP = {"code": "code"}


class _B:
    CH_TABLE = "epm_staging.zz_b"
    CH_FIELD_MAP = {"code": "code"}


class _C:
    CH_TABLE = "epm_staging.zz_c"
    CH_FIELD_MAP = {"code": "code"}


class _Boom(Exception):
    pass


class _Broken:
    """A controller whose doctype cannot be read: reconcile skips it."""
    CH_TABLE = "epm_staging.zz_broken"
    CH_FIELD_MAP = {"code": "code"}


class _Abort(BaseException):
    """What a worker timeout or a kill looks like to reconcile_all: not an
    Exception, so its per-doctype guard does not catch it."""


def _load(controllers, broken=(), abort_on=()):
    """clickhouse.py against a stub frappe. Returns (module, statements)."""
    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(
        in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe._logger = _Logger()
    frappe.logger = lambda: frappe._logger
    frappe.publish_realtime = lambda *a, **k: None
    frappe.get_meta = lambda dt: types.SimpleNamespace(is_submittable=0)
    names = list(controllers)

    def get_all(doctype, filters=None, fields=None, pluck=None,
                limit_page_length=None, **kw):
        if doctype == "Module Def":
            return ["ZZ"]
        if doctype == "DocType":
            return names
        if doctype in abort_on:
            raise _Abort(doctype)
        if doctype in broken:
            raise _Boom(doctype)
        return [{"code": "ZZ1", "modified": None}]

    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(count=lambda *a, **k: 0)

    model = types.ModuleType("frappe.model")
    base_document = types.ModuleType("frappe.model.base_document")
    base_document.get_controller = lambda dt: controllers[dt]
    model.base_document = base_document
    frappe.model = model
    sys.modules.update({"frappe": frappe, "frappe.model": model,
                        "frappe.model.base_document": base_document})
    spec = importlib.util.spec_from_file_location("konsol_clickhouse_k246", CH_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    statements = []

    def execute(sql, params=None):
        statements.append(sql)
        return ""

    module.execute = execute
    return module, statements


_INSERT_RE = re.compile(
    r"INSERT INTO " + re.escape(WATERMARK) + r"\s*\(([^)]*)\)\s*VALUES\s*\((.*)\)\s*$",
    re.S)


def _stamps(statements):
    """[(table_name, reconcile_run_id or None)] for every watermark INSERT.
    None = the INSERT does not name the column, so ClickHouse writes ''."""
    out = []
    for sql in statements:
        m = _INSERT_RE.search(sql)
        if not m:
            continue
        cols = [c.strip() for c in m.group(1).split(",")]
        vals = re.findall(r"'((?:[^'\\]|\\.)*)'|([^,\s][^,]*)", m.group(2))
        vals = [a if a or not b else b.strip() for a, b in vals]
        row = dict(zip(cols, vals))
        out.append((row["table_name"], row.get("reconcile_run_id")))
    return out


# --- what reconcile writes ---------------------------------------------------

def test_reconcile_stamps_one_run_id_on_every_table_it_writes():
    m, sql = _load({"ZZ A": _A, "ZZ B": _B, "ZZ C": _C})
    m.reconcile_all()
    stamps = _stamps(sql)
    assert sorted(t for t, _ in stamps) == [_A.CH_TABLE, _B.CH_TABLE, _C.CH_TABLE], stamps
    run_ids = {r for _, r in stamps}
    assert len(run_ids) == 1, f"one reconcile, one run id: {stamps}"
    (run_id,) = run_ids
    assert run_id, f"the run id must not be blank: {stamps}"


def test_two_reconciles_stamp_two_different_run_ids():
    m, sql = _load({"ZZ A": _A})
    m.reconcile_all()
    first = _stamps(sql)
    del sql[:]
    m.reconcile_all()
    second = _stamps(sql)
    assert first[0][1] and second[0][1] and first[0][1] != second[0][1], (first, second)


def test_a_write_through_stamps_no_run_id():
    """A document event is not a reconcile: its stamp leaves the column to its
    default (''), so it can never be mistaken for a member of a run."""
    m, sql = _load({"ZZ A": _A})
    m.sync_doctype("ZZ A", _A.CH_TABLE, _A.CH_FIELD_MAP)
    assert _stamps(sql) == [(_A.CH_TABLE, None)], _stamps(sql)


def test_a_write_through_after_a_reconcile_carries_no_run_id():
    """The run id is scoped to the reconcile call: once it returns, a later
    write-through in the same process must not inherit it."""
    m, sql = _load({"ZZ A": _A, "ZZ B": _B})
    m.reconcile_all()
    del sql[:]
    m.sync_doctype("ZZ B", _B.CH_TABLE, _B.CH_FIELD_MAP)
    assert _stamps(sql) == [(_B.CH_TABLE, None)], _stamps(sql)


# --- a reconcile that fails partway -----------------------------------------

def test_a_skipped_doctype_is_missing_from_the_run_and_the_rest_are_in_it():
    """One doctype that cannot be read is skipped (reconcile is best-effort per
    table). Its table gets no stamp from this run, which is exactly what
    konsolidat's LAGGING rule reads; the tables either side are in the run."""
    m, sql = _load({"ZZ A": _A, "ZZ Broken": _Broken, "ZZ C": _C}, broken=("ZZ Broken",))
    m.reconcile_all()
    stamps = _stamps(sql)
    assert sorted(t for t, _ in stamps) == [_A.CH_TABLE, _C.CH_TABLE], stamps
    assert len({r for _, r in stamps}) == 1 and stamps[0][1], stamps


def test_an_aborted_reconcile_leaves_its_partial_run_visible():
    """A reconcile killed partway (a worker timeout is not an Exception) must
    leave the stamps it already wrote, under its run id, so the partial run is
    visible to the test, and must not leak the run id into later stamps.
    Buffering the stamps until the end would hide exactly this run."""
    m, sql = _load({"ZZ A": _A, "ZZ B": _B, "ZZ C": _C}, abort_on=("ZZ B",))
    try:
        m.reconcile_all()
    except _Abort:
        pass
    else:
        raise AssertionError("the abort must propagate, not be swallowed")
    stamps = _stamps(sql)
    assert [t for t, _ in stamps] == [_A.CH_TABLE], stamps
    assert stamps[0][1], f"the partial run's stamp carries its run id: {stamps}"
    del sql[:]
    m.sync_doctype("ZZ C", _C.CH_TABLE, _C.CH_FIELD_MAP)
    assert _stamps(sql) == [(_C.CH_TABLE, None)], (
        f"the aborted run's id leaked into a later write-through: {_stamps(sql)}")


# --- the watermark DDL -------------------------------------------------------

def _create(sql):
    return [s for s in sql if s.startswith(f"CREATE TABLE IF NOT EXISTS {WATERMARK}")]


def test_a_new_watermark_keys_on_table_and_run():
    """ReplacingMergeTree keeps one row per sorting key. Keyed on table_name
    alone, a write-through after a reconcile REPLACES that table's run row once
    the parts merge, and the table drops out of the run it was in. Keyed on
    (table_name, reconcile_run_id), the run row and the latest write-through
    row both survive."""
    m, sql = _load({"ZZ A": _A})
    m.sync_doctype("ZZ A", _A.CH_TABLE, _A.CH_FIELD_MAP)
    (create,) = _create(sql)
    assert "reconcile_run_id String" in create, create
    assert "ORDER BY (table_name, reconcile_run_id)" in create, create


def _upgrade_probe(sorting_key):
    """ensure_reference_tables() with system.tables answering `sorting_key`
    for the watermark ('' = the table does not exist yet)."""
    m, sql = _load({})

    def execute(statement, params=None):
        sql.append(statement)
        if "sorting_key" in statement and "system.tables" in statement:
            return sorting_key
        return ""

    m.execute = execute
    m.ensure_reference_tables()
    return [s for s in sql if s.startswith(f"ALTER TABLE {WATERMARK}") and "reconcile_run_id" in s]


def test_an_existing_watermark_gains_the_column_and_the_key_in_one_alter():
    """ClickHouse adds a column to the sorting key only in the ALTER that adds
    the column, and only when the column has no DEFAULT expression (measured on
    24.8: 'Newly added column ... has a default expression, so adding
    expressions that use it to the sorting key is forbidden')."""
    (alter,) = _upgrade_probe("table_name")
    assert "ADD COLUMN IF NOT EXISTS reconcile_run_id String" in alter, alter
    assert "MODIFY ORDER BY (table_name, reconcile_run_id)" in alter, alter
    assert "DEFAULT" not in alter, alter


def test_an_upgraded_watermark_is_not_altered_again():
    """Asked first, like the retired-watermark cleanup: an ALTER that changes
    nothing still rewrites the table's metadata_modification_time (measured on
    24.8), and konsolidat's test measures its no-run-yet grace from that."""
    assert _upgrade_probe("table_name, reconcile_run_id") == []


def test_a_missing_watermark_is_left_to_the_first_stamp():
    assert _upgrade_probe("") == []
