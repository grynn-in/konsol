"""BUD-4 (A3): a Budget Cycle submit or cancel resyncs Budget Annual Input.

Budget Annual Input reaches ClickHouse only when its cycle has docstatus 1
(A2, ``CH_SYNC_FILTERS``), and its sync replaces the whole table. Its rows
change membership when the cycle is submitted or cancelled, not when they are
saved, so the cycle must queue the Annual Input sync: once, after the commit,
on submit and on cancel, and never on a draft save.

The cycle controller is loaded against stubbed frappe. The real
``after_commit_once`` and ``sync_doctype_after_commit`` are lifted from
clickhouse.py by AST and run against a stub after-commit queue, and the real
Budget Annual Input module is loaded, so the once-per-transaction key is the
production one.
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
    frappe.db = types.SimpleNamespace(after_commit=types.SimpleNamespace(_functions=queue, add=queue.append))
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

    for name, mod in {
        "frappe": frappe, "frappe.model": model, "frappe.model.document": docmod,
        "frappe.utils": utils, "konsol.clickhouse": _clickhouse_stub(frappe),
        "konsol.epm.budget_grain": grain,
        "konsol.epm.doctype.scenario.scenario": scenario,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location(
        "konsol.epm.doctype.budget_annual_input.budget_annual_input", BAI_PY)
    bai = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bai)
    monkeypatch.setitem(sys.modules, spec.name, bai)

    spec = importlib.util.spec_from_file_location("_budget_cycle_a3_under_test", CYCLE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def cycle(name="BCYC-ZZB-2026"):
        doc = mod.BudgetCycle(name=name, scenario_id="ZZB", fiscal_year="2026", doctype="Budget Cycle")
        doc.save = lambda *a, **k: pytest.fail("on_submit/on_cancel must not save")
        return doc

    return types.SimpleNamespace(cycle=cycle, queue=queue)


def _bai_syncs(queue):
    return [f for f in queue if getattr(f, "_konsol_key", None) == BAI_KEY]


def test_submit_queues_one_annual_input_sync(env):
    env.cycle().on_submit()
    assert len(_bai_syncs(env.queue)) == 1


def test_cancel_queues_one_annual_input_sync(env):
    env.cycle().on_cancel()
    assert len(_bai_syncs(env.queue)) == 1


def test_draft_save_queues_no_annual_input_sync(env):
    doc = env.cycle()
    for hook in ("validate", "before_save", "on_update"):
        if hasattr(doc, hook):
            getattr(doc, hook)()
    assert _bai_syncs(env.queue) == []


def test_two_calls_in_one_transaction_queue_one(env):
    env.cycle().on_submit()
    env.cycle("BCYC-ZZC-2026").on_submit()
    env.cycle().on_cancel()
    assert len(_bai_syncs(env.queue)) == 1


def test_sheet_push_still_queued_on_submit_and_cancel(env):
    env.cycle().on_submit()
    env.cycle().on_cancel()
    keys = {getattr(f, "_konsol_key", None) for f in env.queue}
    assert ("budget_cycle", "BCYC-ZZB-2026", "lock") in keys
    assert ("budget_cycle", "BCYC-ZZB-2026", "unlock") in keys
