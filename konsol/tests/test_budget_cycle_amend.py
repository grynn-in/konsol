"""#524: a cancelled (scenario, year) can get a new Cycle (Q19-b).

Before S2-C a Budget Cycle had no ``amended_from`` and was named by a digest
of (scenario, year) alone, so once a Cycle was cancelled its pair could never
get another: the amend raised "Unknown column 'amended_from'" and a fresh
insert collided on the digest name.

Now (W2a adds ``amended_from``) Frappe names an amendment ``<name>-1``. The
Cycle's own autoname does the same for a fresh insert, and for a site whose
amend naming is "Default Naming": the digest when it is free, else the first
free ``<digest>-N``. A pair holds one live Cycle (docstatus 0 or 1) at a time:
a second is refused by name, under the lock budget_intake.lock_cycles takes
(19-b: a cancelled Cycle does not count).

The writers that find "the Cycle of (scenario, year)" look it up by the pair
and docstatus, never by the digest name, so a write after the amendment lands
on it: test_budget_intake.py and test_budget_api.py pin that for the Upload
grouping and the cell save.
"""
import importlib.util
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CYCLE_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.py")
DIGEST = "BCYC-ZZB-2026-0123abcd"


class _Thrown(Exception):
    pass


@pytest.fixture
def env(monkeypatch):
    names = set()
    pair = []          # what lock_cycles returns for the pair
    locked = []        # the (scenario, year) pairs lock_cycles was asked for

    class Document:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def get(self, key, default=None):
            return self.__dict__.get(key, default)

    def throw(msg, exc=None, *a, **k):
        raise _Thrown(msg)

    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.ValidationError = _Thrown
    frappe.DuplicateEntryError = _Thrown
    frappe.flags = types.SimpleNamespace(in_install=False, in_migrate=False)
    frappe.db = types.SimpleNamespace(exists=lambda dt, name: name in names, count=lambda *a, **k: 0)
    model = types.ModuleType("frappe.model")
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document
    grain = types.ModuleType("konsol.epm.budget_grain")
    grain.digest_name = lambda prefix, parts: DIGEST
    scenario = types.ModuleType("konsol.epm.doctype.scenario.scenario")
    scenario.locked_scenario_type = lambda s: "budget"
    intake = types.ModuleType("konsol.budget_intake")

    def lock_cycles(scenario_id, fiscal_year, *, wait=True):
        locked.append((scenario_id, fiscal_year))
        return list(pair)

    intake.lock_cycles = lock_cycles
    for name, mod in {
        "frappe": frappe, "frappe.model": model, "frappe.model.document": docmod,
        "konsol.epm.budget_grain": grain, "konsol.epm.doctype.scenario.scenario": scenario,
        "konsol.budget_intake": intake,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(sys.modules["konsol"], "budget_intake", intake, raising=False)
    spec = importlib.util.spec_from_file_location("_budget_cycle_524_under_test", CYCLE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def cycle(name=None, **kw):
        return mod.BudgetCycle(name=name, scenario_id="ZZB", fiscal_year=2026, doctype="Budget Cycle", **kw)

    return types.SimpleNamespace(cycle=cycle, names=names, pair=pair, locked=locked, frappe=frappe)


def _row(name, docstatus):
    return {"name": name, "docstatus": docstatus, "workflow_state": ""}


# ---- the name ------------------------------------------------------------------

def test_a_first_cycle_takes_the_digest_name(env):
    doc = env.cycle()
    doc.autoname()
    assert doc.name == DIGEST


def test_a_new_cycle_after_a_cancelled_one_takes_the_next_free_suffix(env):
    env.names.add(DIGEST)
    doc = env.cycle()
    doc.autoname()
    assert doc.name == DIGEST + "-1"


def test_the_suffix_skips_every_taken_name(env):
    env.names.update({DIGEST, DIGEST + "-1", DIGEST + "-2"})
    doc = env.cycle()
    doc.autoname()
    assert doc.name == DIGEST + "-3"


def test_an_amendment_under_default_naming_does_not_collide(env):
    """A site may set amend naming to "Default Naming": Frappe then calls
    autoname for the amendment too."""
    env.names.add(DIGEST)
    doc = env.cycle(amended_from=DIGEST)
    doc.autoname()
    assert doc.name == DIGEST + "-1"


# ---- one live Cycle per pair -----------------------------------------------------

def test_amending_a_cancelled_cycle_is_accepted(env):
    env.pair[:] = [_row(DIGEST, 2), _row(DIGEST + "-1", 0)]
    doc = env.cycle(DIGEST + "-1", amended_from=DIGEST)
    doc.validate()
    assert env.locked == [("ZZB", 2026)]


@pytest.mark.parametrize("docstatus, word", [(0, "draft"), (1, "approved")])
def test_a_second_live_cycle_for_the_pair_is_refused_by_name(env, docstatus, word):
    env.pair[:] = [_row(DIGEST, docstatus), _row(DIGEST + "-1", 0)]
    with pytest.raises(_Thrown) as raised:
        env.cycle(DIGEST + "-1").validate()
    assert str(raised.value) == (
        f"Budget Cycle {DIGEST} is already the {word} Cycle of ZZB 2026. A scenario and year hold one "
        "live Cycle: work in it, or cancel it and amend it."), raised.value


def test_the_cycle_itself_does_not_count(env):
    env.pair[:] = [_row(DIGEST, 0)]
    env.cycle(DIGEST).validate()
