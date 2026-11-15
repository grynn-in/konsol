"""S4 K6 (konsol#348, S4-Q2 a, chosen under the standing rule on 15 Nov 2026):
an approved Budget Cycle writes through to ``epm_staging.budget_cycles``.

The warehouse knew a Cycle only by the ``cycle`` name on its fact rows, so
nothing there could read the Cycle's Rate Set (K5), its scenario or its round.
Option (b), stamping the rate set on every budget row, was rejected: two more
positioned columns and the value repeated on every row.

The row is one per Cycle: name, scenario_id, scenario_type, fiscal_year,
rate_set, as_of_date, horizon_periods. Submitted Cycles only (docstatus 1,
through resolve_sync_filters' submittable rule, never a declared filter): the
row enters on submit and leaves on cancel, after the commit, once per
transaction, in the pattern the Cycle already uses for the Annual Input and
Entry Line syncs; a draft save queues nothing. A budget Cycle has no round, so
its as_of_date is unset: written as DEFAULT, and the column's declared default
is 1900-01-01 on a Date32, never a silent 1970-01-01 (a Date clamps and
defaults there; CLAUDE.md).

Same harness as test_budget_cycle_syncs_entries.py: the real after-commit
helpers and ``_sql_value`` are lifted from clickhouse.py by AST, so the key
and the literal are the production ones. The DDL is pinned here; konsolidat
copies it into clickhouse/init-db.sql.
"""
import ast
import functools
import importlib.util
import json
import os
import sys
import types
from datetime import date, datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CYCLE_DIR = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle")
CYCLE_PY = os.path.join(CYCLE_DIR, "budget_cycle.py")
CYCLE_JSON = os.path.join(CYCLE_DIR, "budget_cycle.json")
CLICKHOUSE = os.path.join(APP_DIR, "clickhouse.py")
BAI_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_annual_input", "budget_annual_input.py")
BEL_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_entry_line", "budget_entry_line.py")

TABLE = "epm_staging.budget_cycles"
KEY = ("sync_doctype", "Budget Cycle", TABLE)
#: Identical to konsolidat's clickhouse/init-db.sql; keep them identical.
DDL = (
    "(name String, scenario_id String, scenario_type String, fiscal_year UInt16, "
    "rate_set String, as_of_date Date32 DEFAULT '1900-01-01', horizon_periods UInt16) "
    "ENGINE = MergeTree ORDER BY name"
)
COLUMNS = ["name", "scenario_id", "scenario_type", "fiscal_year", "rate_set", "as_of_date", "horizon_periods"]


def _literal(name):
    with open(CLICKHOUSE) as f:
        tree = ast.parse(f.read())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == name)


def _clickhouse_stub(frappe):
    """konsol.clickhouse with the real after_commit_once, sync_doctype_after_commit
    and _sql_value."""
    with open(CLICKHOUSE) as f:
        tree = ast.parse(f.read())
    wanted = {"after_commit_once", "sync_doctype_after_commit", "_sql_value"}
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in fns} == wanted
    ch = types.ModuleType("konsol.clickhouse")
    ch.frappe = frappe
    ch.functools = functools
    ch.date = date
    ch.datetime = datetime
    ch.sync_doctype = lambda *a, **k: None
    exec(compile(ast.Module(body=fns, type_ignores=[]), CLICKHOUSE, "exec"), ch.__dict__)
    return ch


@pytest.fixture
def env(monkeypatch):
    queue = []

    class Document:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def get(self, key, default=None):
            return self.__dict__.get(key, default)

    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe.session = types.SimpleNamespace(user="Administrator")
    frappe.flags = types.SimpleNamespace(in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe.db = types.SimpleNamespace(after_commit=types.SimpleNamespace(_functions=queue, add=queue.append),
                                      count=lambda *a, **k: 0)
    frappe.get_all = lambda *a, **k: []
    frappe.get_meta = lambda dt: types.SimpleNamespace(fields=[])
    frappe.throw = lambda *a, **k: (_ for _ in ()).throw(Exception(a))
    model = types.ModuleType("frappe.model")
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: "2026-11-15 00:00:00"
    grain = types.ModuleType("konsol.epm.budget_grain")
    grain.digest_name = lambda prefix, parts: prefix + "-" + "-".join(str(p) for p in parts)
    scenario = types.ModuleType("konsol.epm.doctype.scenario.scenario")
    scenario.locked_scenario_type = lambda s: "budget"
    intake = types.ModuleType("konsol.budget_intake")
    intake.lock_cycles = lambda *a, **k: []
    monkeypatch.setattr(sys.modules["konsol"], "budget_intake", intake, raising=False)
    ch = _clickhouse_stub(frappe)

    for name, mod in {
        "frappe": frappe, "frappe.model": model, "frappe.model.document": docmod,
        "frappe.utils": utils, "konsol.clickhouse": ch,
        "konsol.epm.budget_grain": grain,
        "konsol.epm.doctype.scenario.scenario": scenario,
        "konsol.budget_intake": intake,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    for modname, path in (("konsol.epm.doctype.budget_annual_input.budget_annual_input", BAI_PY),
                          ("konsol.epm.doctype.budget_entry_line.budget_entry_line", BEL_PY)):
        spec = importlib.util.spec_from_file_location(modname, path)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        monkeypatch.setitem(sys.modules, modname, m)

    spec = importlib.util.spec_from_file_location("_budget_cycle_k6_under_test", CYCLE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def cycle(name="BCYC-ZZB-2027", **kw):
        kw.setdefault("rate_set", "ZZRS")
        doc = mod.BudgetCycle(name=name, scenario_id="ZZB", scenario_type="budget", fiscal_year=2027,
                              doctype="Budget Cycle", **kw)
        doc.save = lambda *a, **k: pytest.fail("on_submit/on_cancel must not save")
        return doc

    return types.SimpleNamespace(cycle=cycle, queue=queue, mod=mod, ch=ch)


def _keys(queue):
    return [getattr(f, "_konsol_key", None) for f in queue]


# ---- the table -----------------------------------------------------------------------

def test_the_ddl_is_pinned_and_the_table_ships_whole():
    assert _literal("_REFERENCE_TABLE_DDL")[TABLE] == DDL
    assert TABLE not in _literal("_ADDED_COLUMNS")
    assert TABLE not in _literal("_RETIRED_TABLES")


def test_an_unset_as_of_date_is_a_declared_sentinel_never_1970():
    """A budget Cycle has no round. The column declares its default on a
    Date32, so an unset date is 1900-01-01 by declaration, not a Date's
    silent 1970-01-01, and konsolidat can read 'no round' by name."""
    assert "as_of_date Date32 DEFAULT '1900-01-01'" in DDL
    assert "1970" not in DDL


def test_the_controller_names_the_table_and_its_columns_in_ddl_order(env):
    cls = env.mod.BudgetCycle
    assert cls.CH_TABLE == TABLE
    assert list(cls.CH_FIELD_MAP) == COLUMNS
    assert cls.CH_FIELD_MAP == {c: c for c in COLUMNS}
    # Submitted only, by the doctype's own is_submittable (resolve_sync_filters);
    # a declared filter here would be a second rule.
    assert not hasattr(cls, "CH_SYNC_FILTERS")
    with open(CYCLE_JSON) as f:
        assert json.load(f)["is_submittable"] == 1


# ---- the hooks -----------------------------------------------------------------------

def test_submit_queues_the_cycle_sync_once_after_the_commit(env):
    env.cycle().on_submit()
    assert _keys(env.queue).count(KEY) == 1


def test_cancel_queues_the_cycle_sync_once_after_the_commit(env):
    env.cycle().on_cancel()
    assert _keys(env.queue).count(KEY) == 1


def test_a_draft_save_queues_no_cycle_sync(env):
    doc = env.cycle()
    for hook in ("validate", "before_save", "on_update"):
        if hasattr(doc, hook):
            getattr(doc, hook)()
    assert KEY not in _keys(env.queue)


def test_two_cycles_in_one_transaction_queue_one_sync_per_table(env):
    env.cycle().on_submit()
    env.cycle("BCYC-ZZC-2027").on_submit()
    env.cycle().on_cancel()
    keys = _keys(env.queue)
    assert keys.count(KEY) == 1
    # the two existing syncs still ride along, once each
    assert keys.count(("sync_doctype", "Budget Annual Input", "epm_gold.budget_annual_input")) == 1
    assert keys.count(("sync_doctype", "Budget Entry Line", "epm_raw.budget_entries")) == 1


def test_no_hook_writes_clickhouse_in_the_transaction():
    with open(CYCLE_PY) as f:
        src = f.read()
    assert "sync_doctype_after_commit" in src
    for forbidden in ("sync_table(", "sync_rows(", "execute(", "db.commit"):
        assert forbidden not in src, forbidden


# ---- the literal -----------------------------------------------------------------------

def test_an_unset_round_is_written_as_default_not_a_1970_date(env):
    """_sql_value renders an unset field as the DEFAULT keyword, so the Date32
    column's declared 1900-01-01 is what lands; a literal would clamp."""
    row = [env.ch._sql_value(v) for v in ("BCYC-ZZB-2027", "ZZB", "budget", 2027, "ZZRS", None, None)]
    assert row == ["'BCYC-ZZB-2027'", "'ZZB'", "'budget'", "2027", "'ZZRS'", "DEFAULT", "DEFAULT"]
    assert env.ch._sql_value(date(2027, 3, 31)) == "'2027-03-31'"
