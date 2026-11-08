"""The scenario_type copies on the planning doctypes cannot go stale (#443).

Decided by Deepak Pai, 7 Nov 2026, on #443 (option B; rejected A, C, D):
Budget Cycle, Budget Sheet and Budget Annual Input carry a read-only copy of
their Scenario's type so the Planning sidebar can list budgets and forecasts
apart. Frappe re-copies a fetched field only when the document itself is saved,
and never on a submitted cycle, so a Scenario whose type changed afterwards
would leave every copy wrong, and a forecast cycle listed as a budget.

Two guards:

1. ``Scenario.validate`` refuses a change of ``scenario_type`` while any Budget
   Cycle or Budget Annual Input references the scenario, and names the count
   (a Budget Sheet references it through its cycle, so the cycles count it).
2. The patch ``backfill_planning_scenario_type`` fills the copy on rows that
   existed before the field did.

Both run here against a stub frappe that models a site.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENARIO_PY = os.path.join(APP_DIR, "epm", "doctype", "scenario", "scenario.py")
PATCH_PY = os.path.join(APP_DIR, "patches", "backfill_planning_scenario_type.py")
DOTTED = "konsol.patches.backfill_planning_scenario_type"


class _ValidationError(Exception):
    pass


class _Site:
    """Rows by doctype: {doctype: {name: {field: value}}}."""

    def __init__(self, rows):
        self.rows = {dt: {n: dict(r) for n, r in by.items()} for dt, by in rows.items()}
        self.set_values = []
        self.reloaded = []
        self.sql_log = []

    def frappe(self):
        site = self
        frappe = types.ModuleType("frappe")
        frappe.ValidationError = _ValidationError
        frappe.flags = types.SimpleNamespace()

        def _match(row, filters):
            return all(row.get(k) == v for k, v in (filters or {}).items())

        def count(doctype, filters=None):
            return sum(1 for r in site.rows.get(doctype, {}).values() if _match(r, filters))

        def get_value(doctype, name, field, *a, **k):
            return site.rows.get(doctype, {}).get(name, {}).get(field)

        def get_all(doctype, filters=None, fields=None, pluck=None, **k):
            out = []
            for n, r in site.rows.get(doctype, {}).items():
                if not _match(r, filters):
                    continue
                d = {"name": n, **r}
                out.append(d[pluck] if pluck else d)
            return out

        def set_value(doctype, name, field, value, update_modified=True):
            assert update_modified is False, "a backfill must not touch modified"
            site.set_values.append((doctype, name, field, value))
            site.rows[doctype][name][field] = value

        def table_exists(doctype):
            return doctype in site.rows

        def throw(msg, exc=None, title=None):
            raise (exc or _ValidationError)(msg)

        def reload_doc(module, kind, name):
            site.reloaded.append(name)

        def sql(query, values=None, *a, **k):
            """Locking reads only: the Scenario row, or a COUNT of one copy doctype."""
            site.sql_log.append(" ".join(query.split()))
            import re as _re
            m = _re.search(r"COUNT\(\*\) FROM `tab([^`]+)` WHERE scenario_id = %s", query)
            if m:
                return [[count(m.group(1), {"scenario_id": values[0]})]]
            if "FROM `tabScenario`" in query:
                row = site.rows.get("Scenario", {}).get(values[0])
                return [[row.get("scenario_type")]] if row else []
            raise AssertionError("unexpected SQL: " + query)

        frappe.db = types.SimpleNamespace(count=count, get_value=get_value, sql=sql,
                                          set_value=set_value, table_exists=table_exists)
        frappe.get_all = get_all
        frappe.throw = throw
        frappe.reload_doc = reload_doc
        frappe._ = lambda s: s
        return frappe


def _with_stubs(site, fn):
    names = ["frappe", "frappe.model", "frappe.model.document", "konsol", "konsol.clickhouse"]
    saved = {n: sys.modules.get(n) for n in names}
    fr = site.frappe()
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")

    class Document:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def get(self, k):
            return self.__dict__.get(k)

    document.Document = Document
    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    ch = types.ModuleType("konsol.clickhouse")
    ch.sync_doctype_after_commit = lambda *a, **k: None
    ch.after_commit_once = lambda *a, **k: None
    sys.modules.update({"frappe": fr, "frappe.model": model, "frappe.model.document": document,
                        "konsol": konsol, "konsol.clickhouse": ch})
    try:
        return fn(Document)
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m


def _load(path, alias):
    spec = importlib.util.spec_from_file_location(alias, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- 1. Scenario refuses a type change while referenced -------------------------

def _validate(site, before, after, is_new=False):
    def run(Document):
        mod = _load(SCENARIO_PY, "_scenario_under_test")
        doc = mod.Scenario(name="FC", scenario_id="FC", scenario_type=after)
        doc.is_new = lambda: is_new
        doc.get_doc_before_save = lambda: None if is_new else types.SimpleNamespace(
            scenario_type=before, get=lambda k: before if k == "scenario_type" else None)
        doc.has_value_changed = lambda f: (not is_new) and f == "scenario_type" and before != after
        doc.validate()
    return _with_stubs(site, run)


def _referenced_site():
    return _Site({
        "Scenario": {"FC": {"scenario_type": "forecast"}},
        "Budget Cycle": {"C1": {"scenario_id": "FC"}, "C2": {"scenario_id": "FC"},
                         "C3": {"scenario_id": "BU"}},
        "Budget Annual Input": {"A1": {"scenario_id": "FC"}},
        "Budget Sheet": {},
    })


def test_a_referenced_scenario_cannot_change_type_and_the_count_is_named():
    try:
        _validate(_referenced_site(), "forecast", "budget")
    except _ValidationError as e:
        msg = str(e)
        assert "2 Budget Cycle" in msg and "1 Budget Annual Input" in msg, msg
    else:
        raise AssertionError("a referenced scenario changed its type")


def test_the_check_reads_under_locks():
    """Review of #460: a plain count under REPEATABLE READ can miss a cycle
    committed meanwhile (CLAUDE.md: a check-then-write needs a locking read).
    The scenario row is locked first, so a cycle copying its type waits; each
    count is a locking read, which sees the latest committed rows."""
    site = _referenced_site()
    site.rows["Budget Cycle"] = {}
    site.rows["Budget Annual Input"] = {}
    _validate(site, "forecast", "budget")
    assert site.sql_log[0].startswith("SELECT scenario_type FROM `tabScenario` WHERE name = %s FOR UPDATE"), site.sql_log
    counts = [q for q in site.sql_log if q.startswith("SELECT COUNT(*)")]
    assert len(counts) == 2 and all(q.endswith("FOR UPDATE") for q in counts), counts


def test_a_copy_is_taken_under_a_share_lock_on_the_scenario():
    """The other side of the same race: a cycle or annual input copies the type
    from a share-locked read of the Scenario, so it waits for a type change in
    flight and then copies the committed type."""
    def run(Document):
        mod = _load(SCENARIO_PY, "_scenario_lock_under_test")
        return mod.locked_scenario_type("FC")
    site = _referenced_site()
    assert _with_stubs(site, run) == "forecast"
    assert site.sql_log == ["SELECT scenario_type FROM `tabScenario` WHERE name = %s LOCK IN SHARE MODE"]
    for rel in (("budget_cycle", "budget_cycle.py"), ("budget_annual_input", "budget_annual_input.py")):
        with open(os.path.join(APP_DIR, "epm", "doctype", *rel)) as f:
            src = f.read()
        assert "self.scenario_type = locked_scenario_type(self.scenario_id)" in src, rel


def test_a_cycle_keeps_the_scenario_it_was_made_for():
    """Review of #460: a draft cycle moved to another Scenario left its sheets
    with the old copy. The cycle's name is a digest of its scenario
    (BudgetCycle.autoname), so the scenario is set once."""
    import json
    with open(os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.json")) as f:
        field = next(x for x in json.load(f)["fields"] if x["fieldname"] == "scenario_id")
    assert field.get("set_only_once") == 1, field


def test_an_unreferenced_scenario_may_change_type():
    site = _referenced_site()
    site.rows["Budget Cycle"] = {}
    site.rows["Budget Annual Input"] = {}
    _validate(site, "forecast", "budget")


def test_an_unchanged_type_or_a_new_scenario_passes():
    _validate(_referenced_site(), "forecast", "forecast")
    _validate(_referenced_site(), None, "budget", is_new=True)


# --- 2. the backfill patch --------------------------------------------------------

def _patch(site):
    return _with_stubs(site, lambda _D: _load(PATCH_PY, "_backfill_under_test").execute())


def test_the_patch_is_registered():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        assert f.read().split().count(DOTTED) == 1


def test_the_backfill_copies_each_rows_scenario_type():
    site = _Site({
        "Scenario": {"BU": {"scenario_type": "budget"}, "FC": {"scenario_type": "forecast"}},
        "Budget Cycle": {"C1": {"scenario_id": "BU"}, "C2": {"scenario_id": "FC", "scenario_type": "forecast"}},
        "Budget Sheet": {"S1": {"cycle": "C1"}, "S2": {"cycle": "C2", "scenario_type": "budget"}},
        "Budget Annual Input": {"A1": {"scenario_id": "FC"}},
    })
    _patch(site)
    assert site.rows["Budget Cycle"]["C1"]["scenario_type"] == "budget"
    assert site.rows["Budget Cycle"]["C2"]["scenario_type"] == "forecast"
    assert site.rows["Budget Sheet"]["S1"]["scenario_type"] == "budget"
    assert site.rows["Budget Sheet"]["S2"]["scenario_type"] == "forecast"  # a wrong copy is fixed
    assert site.rows["Budget Annual Input"]["A1"]["scenario_type"] == "forecast"
    # Only rows that differ are written.
    assert ("Budget Cycle", "C2", "scenario_type", "forecast") not in site.set_values
    # The column exists before it is written: migrate runs every konsol patch
    # before the model sync (patches.txt has no [post_model_sync] section).
    assert set(site.reloaded) == {"budget_cycle", "budget_sheet", "budget_annual_input"}


def test_the_backfill_on_a_site_without_rows_writes_nothing():
    site = _Site({"Scenario": {}, "Budget Cycle": {}, "Budget Sheet": {}, "Budget Annual Input": {}})
    _patch(site)
    assert site.set_values == []
