"""S2-T2: a Budget Cycle submit or cancel resyncs Budget Entry Line.

Budget Entry Line reaches ClickHouse only while its cycle has docstatus 1
(``CH_SYNC_FILTERS``), and its sync replaces the whole table. Its rows change
membership when the cycle is submitted or cancelled, not when they are saved,
so the cycle must queue the Budget Entry Line sync: once per transaction,
after the commit, on submit and on cancel, and never on a draft save.

Same harness as test_budget_cycle_syncs_annual_input.py (A3): the real
``after_commit_once`` and ``sync_doctype_after_commit`` are lifted from
clickhouse.py by AST, and the real Budget Entry Line module is loaded, so the
once-per-transaction key is the production one.
"""
import ast
import functools
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CYCLE_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.py")
CLICKHOUSE = os.path.join(APP_DIR, "clickhouse.py")
BAI_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_annual_input", "budget_annual_input.py")
BAI_TABLE = "epm_gold.budget_annual_input"
BAI_KEY = ("sync_doctype", "Budget Annual Input", BAI_TABLE)
BEL_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_entry_line", "budget_entry_line.py")
BEL_TABLE = "epm_raw.budget_entries"
BEL_KEY = ("sync_doctype", "Budget Entry Line", BEL_TABLE)


def _clickhouse_stub(frappe):
    """konsol.clickhouse with the real after_commit_once / sync_doctype_after_commit."""
    with open(CLICKHOUSE) as f:
        tree = ast.parse(f.read())
    wanted = {"after_commit_once", "sync_doctype_after_commit"}
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in fns} == wanted
    ch = types.ModuleType("konsol.clickhouse")
    ch.frappe = frappe
    ch.functools = functools
    ch.sync_doctype = lambda *a, **k: None
    exec(compile(ast.Module(body=fns, type_ignores=[]), CLICKHOUSE, "exec"), ch.__dict__)
    return ch


@pytest.fixture
def env(monkeypatch):
    queue = []

    class Document:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe.session = types.SimpleNamespace(user="Administrator")
    frappe.flags = types.SimpleNamespace(in_install=False, in_import=False, in_migrate=False, in_patch=False)
    counts = {}
    frappe.db = types.SimpleNamespace(after_commit=types.SimpleNamespace(_functions=queue, add=queue.append),
                                      count=lambda dt, filters=None: counts.get((dt, (filters or {}).get("cycle")), 0))
    frappe.get_all = lambda *a, **k: []
    frappe.get_meta = lambda dt: types.SimpleNamespace(fields=[])
    frappe.throw = lambda *a, **k: (_ for _ in ()).throw(Exception(a))
    model = types.ModuleType("frappe.model")
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: "2026-11-13 00:00:00"
    grain = types.ModuleType("konsol.epm.budget_grain")
    grain.digest_name = lambda prefix, parts: prefix + "-" + "-".join(parts)
    scenario = types.ModuleType("konsol.epm.doctype.scenario.scenario")
    scenario.locked_scenario_type = lambda s: "budget"
    # #524: validate refuses a second live Cycle of the pair under this lock.
    intake = types.ModuleType("konsol.budget_intake")
    intake.lock_cycles = lambda *a, **k: []
    monkeypatch.setattr(sys.modules["konsol"], "budget_intake", intake, raising=False)

    for name, mod in {
        "frappe": frappe, "frappe.model": model, "frappe.model.document": docmod,
        "frappe.utils": utils, "konsol.clickhouse": _clickhouse_stub(frappe),
        "konsol.epm.budget_grain": grain,
        "konsol.epm.doctype.scenario.scenario": scenario,
        "konsol.budget_intake": intake,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location(
        "konsol.epm.doctype.budget_annual_input.budget_annual_input", BAI_PY)
    bai = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bai)
    monkeypatch.setitem(sys.modules, spec.name, bai)
    spec = importlib.util.spec_from_file_location(
        "konsol.epm.doctype.budget_entry_line.budget_entry_line", BEL_PY)
    bel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bel)
    monkeypatch.setitem(sys.modules, spec.name, bel)

    spec = importlib.util.spec_from_file_location("_budget_cycle_t2_under_test", CYCLE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def cycle(name="BCYC-ZZB-2026"):
        doc = mod.BudgetCycle(name=name, scenario_id="ZZB", fiscal_year="2026", doctype="Budget Cycle")
        doc.save = lambda *a, **k: pytest.fail("on_submit/on_cancel must not save")
        return doc

    return types.SimpleNamespace(cycle=cycle, queue=queue, counts=counts)


def _entry_syncs(queue):
    return [f for f in queue if getattr(f, "_konsol_key", None) == BEL_KEY]


def _keys(queue):
    return [getattr(f, "_konsol_key", None) for f in queue]


def test_submit_queues_one_entry_sync(env):
    env.cycle().on_submit()
    assert len(_entry_syncs(env.queue)) == 1


def test_cancel_queues_one_entry_sync(env):
    env.cycle().on_cancel()
    assert len(_entry_syncs(env.queue)) == 1


def test_draft_save_queues_no_sync(env):
    doc = env.cycle()
    for hook in ("validate", "before_save", "on_update"):
        if hasattr(doc, hook):
            getattr(doc, hook)()
    assert env.queue == []


def test_two_calls_in_one_transaction_queue_one_sync_per_table(env):
    env.cycle().on_submit()
    env.cycle("BCYC-ZZC-2026").on_submit()
    env.cycle().on_cancel()
    keys = _keys(env.queue)
    assert keys.count(BEL_KEY) == 1
    assert keys.count(BAI_KEY) == 1


def test_annual_input_sync_still_queued(env):
    env.cycle().on_submit()
    assert BAI_KEY in _keys(env.queue)


# ---- S2-C W2b: entry_count is counted, never typed ----------------------------

def test_entry_count_is_counted_on_save(env):
    env.counts[("Budget Entry", "BCYC-ZZB-2026")] = 3
    env.counts[("Budget Entry", "BCYC-OTHER")] = 9
    doc = env.cycle()
    doc.entry_count = 99
    doc.validate()
    assert doc.entry_count == 3


def test_entry_count_is_fresh_when_the_form_opens(env):
    """Entries change without a Cycle save, so the form counts them on load."""
    env.counts[("Budget Entry", "BCYC-ZZB-2026")] = 2
    doc = env.cycle()
    doc.onload()
    assert doc.entry_count == 2

