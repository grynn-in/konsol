"""S2-T1: Budget Entry, the parent of Budget Entry Line (konsol#348 Q1-b2).

One entry per (cycle, entity, layer, period). Its name is
``digest_name("BENT", [cycle, data_area_id, layer, fiscal_period])``, so the
same key twice is refused. Scenario and year come from the Cycle, never typed.
Row count and debit/credit totals are computed. ``main_account`` plus the
``dim_*`` values is unique within an entry. Every save stamps the header onto
the lines (T0's ``stamp_header``). Not submittable, no sync hook: the Cycle's
submit and cancel sync the lines (T2).

The JSON is read as data; the controller is loaded against a stubbed frappe.
"""
import ast
import importlib.util
import json
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(APP_DIR, "epm", "doctype", "budget_entry")
PY_PATH = os.path.join(DIR, "budget_entry.py")
JSON_PATH = os.path.join(DIR, "budget_entry.json")
LINE_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_entry_line", "budget_entry_line.py")
GRAIN_PY = os.path.join(APP_DIR, "epm", "budget_grain.py")
FISCAL_CALENDAR = os.path.join(APP_DIR, "fiscal_calendar.py")

#: Movements only (Q15-c): a Period-end balance needs a declared opening.
BASES = ["Period movement", "Year-to-date movement"]


def _meta():
    with open(JSON_PATH) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _meta()["fields"]}


# ---- JSON shape -------------------------------------------------------------

def test_files_exist():
    for name in ("__init__.py", "budget_entry.py", "budget_entry.json"):
        assert os.path.exists(os.path.join(DIR, name)), name


def test_identity():
    m = _meta()
    assert m["name"] == "Budget Entry"
    assert m["module"] == "EPM"
    assert m.get("autoname", "") == "", "the controller's autoname sets the name (digest_name)"
    assert m["title_field"] == "data_area_id"
    assert m["search_fields"] == "cycle, data_area_id, layer"
    assert m["track_changes"] == 1
    assert not m.get("is_submittable"), "not submittable: the Cycle approves (Q1-b2)"
    assert not m.get("istable")


def test_header_fields():
    f = _fields()
    for name, ftype, opts in (
            ("cycle", "Link", "Budget Cycle"),
            ("layer", "Link", "Budget Layer"),
            ("data_area_id", "Link", "Entity"),
            ("fiscal_period", "Int", None),
            ("currency", "Link", "ISO Currency"),
            ("amount_basis", "Select", None)):
        assert f[name]["fieldtype"] == ftype, name
        if opts:
            assert f[name]["options"] == opts, name
        assert f[name].get("reqd") == 1, f"{name} is required"
    assert f["scenario_id"]["fieldtype"] == "Link"
    assert f["scenario_id"]["options"] == "Scenario"
    assert f["fiscal_year"]["fieldtype"] == "Int"
    for name in ("scenario_id", "fiscal_year"):
        assert f[name].get("read_only") == 1, f"{name} is fetched, never typed"
        assert f[name].get("fetch_from") == f"cycle.{name}", name


def test_key_fields_cannot_change_after_insert():
    """The name is a digest of the key, so a changed key would leave a name
    that no longer matches it."""
    f = _fields()
    for name in ("cycle", "data_area_id", "layer", "fiscal_period"):
        assert f[name].get("set_only_once") == 1, name


def test_amount_basis_is_never_defaulted():
    f = _fields()["amount_basis"]
    options = f["options"].split("\n")
    assert options[0] == "", "a blank first option: a new entry has no basis until one is chosen"
    assert options[1:] == BASES, "the budget's two bases (Q15-c): no Period-end balance"
    assert not f.get("default")


def test_lines_and_summary():
    f = _fields()
    assert f["lines"]["fieldtype"] == "Table"
    assert f["lines"]["options"] == "Budget Entry Line"
    assert not f["lines"].get("reqd")
    assert f["row_count"]["fieldtype"] == "Int"
    for name in ("total_debit", "total_credit"):
        assert f[name]["fieldtype"] == "Currency", name
        assert f[name]["options"] == "currency", name
    for name in ("row_count", "total_debit", "total_credit", "source", "budget_upload"):
        assert f[name].get("read_only") == 1, f"{name} is set by the system"
    assert f["source"]["fieldtype"] == "Select"
    assert f["source"]["options"].split("\n") == ["", "Upload", "Excel", "Desk"]
    assert f["budget_upload"]["fieldtype"] == "Link"
    assert f["budget_upload"]["options"] == "Budget Upload"


def test_tabs_and_sections():
    fields = _meta()["fields"]
    tabs = [x["label"] for x in fields if x["fieldtype"] == "Tab Break"]
    assert tabs == ["Entry", "Summary"]
    sections = [x["label"] for x in fields if x["fieldtype"] == "Section Break"]
    assert sections == ["Budget", "Amounts", "Lines", "Totals", "Source"]
    assert any(x["fieldtype"] == "Column Break" for x in fields)
    for x in fields:
        if x["fieldtype"] not in ("Column Break",):
            assert x.get("label"), f"{x['fieldname']} needs a label"


def test_permissions():
    perms = {p["role"]: p for p in _meta()["permissions"]}
    assert set(perms) == {"System Manager", "EPM Admin", "EPM Analyst",
                          "Entity Accountant", "EPM User"}
    sm = perms["System Manager"]
    for right in ("read", "write", "create", "delete"):
        assert sm.get(right) == 1, right
    for role in ("EPM Admin", "EPM Analyst", "Entity Accountant"):
        for right in ("read", "write", "create"):
            assert perms[role].get(right) == 1, (role, right)
        assert not perms[role].get("submit")
    user = perms["EPM User"]
    assert user.get("read") == 1
    assert not any(user.get(r) for r in ("write", "create", "delete"))


def test_no_sync_hook():
    """The Cycle's submit and cancel sync the lines (T2); an entry changes
    only under a Draft Cycle, so it never syncs itself. ``on_trash`` is the
    Cycle guard, not a sync."""
    src = open(PY_PATH).read()
    tree = ast.parse(src)
    methods = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert not methods & {"on_update", "on_submit", "on_cancel", "after_delete"}
    assert "sync_doctype" not in src and "queue_sync" not in src


def test_registered_as_period_data():
    """Budget Entry uses its period, as the TB does, so it freezes the fiscal
    calendar; its lines' copy of the period does not count twice."""
    tree = ast.parse(open(FISCAL_CALENDAR).read())
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ("_PERIOD_DATA", "NOT_PERIOD_DATA"):
                    found[t.id] = ast.literal_eval(node.value)
    assert found["_PERIOD_DATA"].get("Budget Entry") is False, "not submittable: every row counts"
    assert "Budget Entry" not in found["NOT_PERIOD_DATA"]
    assert "Budget Entry Line" in found["NOT_PERIOD_DATA"]


# ---- controller against a stubbed frappe ------------------------------------

class Thrown(Exception):
    pass


class Denied(Exception):
    pass


class _Row(types.SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)

    def set(self, key, value):
        setattr(self, key, value)


@pytest.fixture
def env(monkeypatch):
    cycles = {"BCYC-1": {"scenario_id": "budget", "fiscal_year": 2027, "docstatus": 0}}
    entries = {}  # name -> key dict
    dims = ["dim_cost_center", "dim_project"]

    locks = []   # lock acquisition, in order

    class Document:
        def check_if_latest(self):
            # Frappe's: load_doc_before_save reads the entry FOR UPDATE.
            locks.append(("entry", self.name))

        def get_doc_before_save(self):
            return getattr(self, "_before", None)

        def get(self, key, default=None):
            return getattr(self, key, default)

        def set(self, key, value):
            setattr(self, key, value)

        def is_new(self):
            return getattr(self, "__islocal", None)

    def throw(msg, exc=None, *a, **k):
        raise (exc or Thrown)(msg)

    sql_calls = []

    held_elsewhere = set()   # Cycles another session has locked

    def sql(query, values=None, *a, **k):
        sql_calls.append((" ".join(query.split()), values))
        assert "FOR UPDATE" in query and "`tabBudget Cycle`" in query, query
        if values[0] in held_elsewhere:
            if "NOWAIT" in query:
                raise frappe.QueryTimeoutError("Lock wait timeout exceeded")
            raise AssertionError("waited on a Cycle lock while holding the entry's")
        locks.append(("cycle", values[0]))
        row = cycles.get(values[0])
        return [(row["docstatus"],)] if row else []

    class _Flags(dict):
        def __getattr__(self, key):
            return self.get(key)

        def __setattr__(self, key, value):
            self[key] = value

    def get_value(doctype, name, fields=None, *a, **k):
        if doctype == "Budget Cycle":
            row = cycles.get(name)
            if row is None:
                return None
            if isinstance(fields, (list, tuple)):
                return tuple(row[f] for f in fields)
            return row[fields]
        if doctype == "Budget Entry":
            assert isinstance(name, dict)
            for n, key in entries.items():
                if all((key.get(f) == v) for f, v in name.items() if f != "name") and (
                        "name" not in name or n != name["name"][1]):
                    return n
            return None
        raise AssertionError(doctype)

    frappe = types.ModuleType("frappe")
    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.ValidationError = Thrown
    frappe.DuplicateEntryError = Thrown
    frappe.PermissionError = Denied
    frappe.QueryTimeoutError = type("QueryTimeoutError", (Exception,), {})
    frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
    frappe.flags = _Flags()
    frappe.db = types.SimpleNamespace(get_value=get_value, sql=sql)
    frappe.get_meta = lambda dt: types.SimpleNamespace(fields=[
        types.SimpleNamespace(fieldname=d, fieldtype="Data") for d in dims])
    frappe.bold = lambda s: str(s)

    model = types.ModuleType("frappe.model")
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document
    ch = types.ModuleType("konsol.clickhouse")
    ch.sync_doctype_after_commit = lambda *a: None
    intake = types.ModuleType("konsol.budget_intake")
    judged = []

    def judge_one(table, *, form_basis):
        judged.append((table, form_basis))
        if intake.refuse:
            frappe.throw(*intake.refuse)
        return {}, []
    intake.judge_one = judge_one
    intake.refuse = None
    removed = []

    def assert_may_remove(entity, layer, rows):
        removed.append((entity, layer, [dict(r) for r in rows]))
        if intake.refuse_removal:
            frappe.throw(intake.refuse_removal, Denied)
    intake.assert_may_remove = assert_may_remove
    intake.refuse_removal = None
    basis_checks = []

    def assert_one_basis(cycle, entity, layer, basis, *, name, replacing=()):
        locks.append(("basis check", cycle))
        basis_checks.append((cycle, entity, layer, basis, name, set(replacing)))
        if intake.refuse_basis:
            frappe.throw(intake.refuse_basis, Thrown)
    intake.assert_one_basis = assert_one_basis
    ytd_checks = []

    def assert_ytd_complete(cycle, entity, layer, basis, period, *, name, replacing=()):
        locks.append(("ytd check", cycle))
        ytd_checks.append((cycle, entity, layer, basis, period, name, set(replacing)))
    intake.assert_ytd_complete = assert_ytd_complete
    delete_checks = []

    def assert_ytd_survives_delete(cycle, entity, layer, basis, *, name):
        delete_checks.append((cycle, entity, layer, basis, name))
        if intake.refuse_delete:
            frappe.throw(intake.refuse_delete, Thrown)
    intake.assert_ytd_survives_delete = assert_ytd_survives_delete
    intake.refuse_delete = None
    intake.refuse_basis = None
    grain_checks = []

    def assert_no_annual_input(scenario_id, fiscal_year, entity, layer, lines, *, entry, period):
        locks.append(("grain check", scenario_id))
        grain_checks.append((scenario_id, fiscal_year, entity, layer, [dict(r) for r in lines], entry, period))
        if intake.refuse_grain:
            frappe.throw(intake.refuse_grain, Thrown)
    intake.assert_no_annual_input = assert_no_annual_input
    intake.refuse_grain = None
    for name, mod in {"frappe": frappe, "frappe.model": model,
                      "frappe.model.document": docmod,
                      "konsol.clickhouse": ch, "konsol.budget_intake": intake}.items():
        monkeypatch.setitem(sys.modules, name, mod)

    loaded = {}
    for modname, path in (("konsol.epm.budget_grain", GRAIN_PY),
                          ("konsol.epm.doctype.budget_entry_line.budget_entry_line", LINE_PY)):
        spec = importlib.util.spec_from_file_location(modname, path)
        mod = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, modname, mod)
        spec.loader.exec_module(mod)
        loaded[modname] = mod
    spec = importlib.util.spec_from_file_location("_budget_entry_under_test", PY_PATH)
    ctl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ctl)
    return types.SimpleNamespace(ctl=ctl, cycles=cycles, entries=entries, frappe=frappe,
                                 grain=loaded["konsol.epm.budget_grain"], sql=sql_calls,
                                 judged=judged, intake=intake, locks=locks, removed=removed,
                                 held_elsewhere=held_elsewhere, basis_checks=basis_checks, ytd_checks=ytd_checks, delete_checks=delete_checks,
                                 grain_checks=grain_checks)


def _entry(env, lines=(), **kw):
    doc = env.ctl.BudgetEntry()
    header = dict(name=None, cycle="BCYC-1", data_area_id="E1", layer="base",
                  fiscal_period=3, currency="EUR", amount_basis="Period movement",
                  scenario_id=None, fiscal_year=None)
    header.update(kw)
    for k, v in header.items():
        setattr(doc, k, v)
    setattr(doc, "__islocal", 1 if kw.get("name") is None else None)
    doc.lines = [_Row(idx=i + 1, **line) for i, line in enumerate(lines)]
    return doc


def test_autoname_is_the_digest_of_the_key(env):
    doc = _entry(env)
    doc.autoname()
    assert doc.name == env.grain.digest_name("BENT", ["BCYC-1", "E1", "base", 3])


def test_scenario_and_year_come_from_the_cycle(env):
    doc = _entry(env, scenario_id="typed", fiscal_year=1999)
    doc.validate()
    assert (doc.scenario_id, doc.fiscal_year) == ("budget", 2027)


def test_missing_cycle_is_refused_by_name(env):
    doc = _entry(env, cycle="BCYC-NONE")
    with pytest.raises(Thrown, match="BCYC-NONE"):
        doc.validate()


def test_same_key_twice_is_refused(env):
    env.entries["BENT-OTHER"] = dict(cycle="BCYC-1", data_area_id="E1", layer="base",
                                     fiscal_period=3)
    doc = _entry(env)
    with pytest.raises(Thrown, match="BENT-OTHER"):
        doc.validate()


def test_same_key_twice_is_refused_after_autoname(env):
    """Frappe runs autoname before validate, so a new entry already carries the
    digest name the existing one holds: it must not be taken for itself."""
    doc = _entry(env)
    doc.autoname()
    env.entries[doc.name] = dict(cycle="BCYC-1", data_area_id="E1", layer="base",
                                 fiscal_period=3)
    with pytest.raises(Thrown, match=doc.name):
        doc.validate()


def test_other_period_is_accepted(env):
    env.entries["BENT-OTHER"] = dict(cycle="BCYC-1", data_area_id="E1", layer="base",
                                     fiscal_period=4)
    _entry(env).validate()


def test_resave_of_itself_is_accepted(env):
    env.entries["BENT-ME"] = dict(cycle="BCYC-1", data_area_id="E1", layer="base",
                                  fiscal_period=3)
    _entry(env, name="BENT-ME").validate()


def test_duplicate_line_is_refused(env):
    doc = _entry(env, lines=[
        dict(main_account="4000", debit=0, credit=100, dim_cost_center="CC1", dim_project=None),
        dict(main_account="5000", debit=50, credit=0, dim_cost_center="CC1", dim_project=None),
        dict(main_account="4000", debit=0, credit=7, dim_cost_center="CC1", dim_project=""),
    ])
    with pytest.raises(Thrown) as exc:
        doc.validate()
    msg = str(exc.value)
    assert "4000" in msg and "1" in msg and "3" in msg


def test_same_account_other_dimension_is_accepted(env):
    doc = _entry(env, lines=[
        dict(main_account="4000", debit=0, credit=100, dim_cost_center="CC1"),
        dict(main_account="4000", debit=0, credit=100, dim_cost_center="CC2"),
    ])
    doc.validate()


def test_totals_computed(env):
    doc = _entry(env, lines=[
        dict(main_account="4000", debit=None, credit=100.25),
        dict(main_account="5000", debit=60.5, credit=0),
        dict(main_account="6000", debit=10, credit=None),
    ])
    doc.row_count, doc.total_debit, doc.total_credit = 99, 99, 99
    doc.validate()
    assert doc.row_count == 3
    assert doc.total_debit == pytest.approx(70.5)
    assert doc.total_credit == pytest.approx(100.25), "no balance rule: unequal totals stand"


def test_empty_entry_totals_zero(env):
    doc = _entry(env)
    doc.validate()
    assert (doc.row_count, doc.total_debit, doc.total_credit) == (0, 0, 0)


def test_lines_carry_the_header(env):
    doc = _entry(env, lines=[dict(main_account="4000", debit=1, credit=0)])
    doc.validate()
    line = doc.lines[0]
    assert (line.cycle, line.scenario_id, line.layer, line.data_area_id, line.fiscal_year,
            line.fiscal_period, line.currency, line.amount_basis) == (
        "BCYC-1", "budget", "base", "E1", 2027, 3, "EUR", "Period movement")


# ---- review fix 1: the entry guards itself ----------------------------------------

@pytest.mark.parametrize("docstatus, word", [(1, "approved"), (2, "cancelled")])
def test_an_entry_under_a_cycle_that_is_not_a_draft_is_refused(env, docstatus, word):
    env.cycles["BCYC-1"]["docstatus"] = docstatus
    doc = _entry(env, lines=[dict(main_account="4000", debit=1, credit=0)])
    with pytest.raises(Thrown) as raised:
        doc.validate()
    assert "BCYC-1" in str(raised.value) and word in str(raised.value)


def test_validate_reads_the_cycle_with_a_locking_read(env):
    _entry(env).validate()
    assert env.sql and env.sql[0] == (
        "SELECT docstatus FROM `tabBudget Cycle` WHERE name=%s FOR UPDATE", ("BCYC-1",))


def test_an_entry_under_an_approved_cycle_cannot_be_deleted(env):
    env.cycles["BCYC-1"]["docstatus"] = 1
    doc = _entry(env, name="BENT-ME")
    with pytest.raises(Thrown, match="approved"):
        doc.on_trash()
    assert env.sql and "FOR UPDATE" in env.sql[0][0]


def test_an_entry_under_a_cancelled_cycle_may_be_deleted(env):
    # Re-review 3: a cancelled Cycle's entries are not in the warehouse and
    # would otherwise be undeletable forever.
    env.cycles["BCYC-1"]["docstatus"] = 2
    _entry(env, name="BENT-ME").on_trash()


def test_an_entry_under_a_draft_cycle_may_be_deleted(env):
    _entry(env, name="BENT-ME").on_trash()


@pytest.mark.parametrize("flag", ["in_install", "in_migrate"])
def test_install_and_migrate_skip_the_cycle_check(env, flag):
    env.cycles["BCYC-1"]["docstatus"] = 1
    setattr(env.frappe.flags, flag, True)
    _entry(env).validate()
    _entry(env, name="BENT-ME").on_trash()
    assert env.sql == []


def test_validate_judges_the_lines_through_the_intake(env):
    doc = _entry(env, lines=[
        dict(main_account="4000", debit=100.5, credit=0, dim_cost_center="CC1", description="x"),
        dict(main_account="5000", debit=0, credit=7, dim_cost_center=None)])
    doc.validate()
    ((table, basis),) = env.judged
    assert basis == "Period movement"
    header, *rows = table
    got = [dict(zip(header, r)) for r in rows]
    assert [(r["scenario"], r["layer"], r["data_area_id"], r["fiscal_year"], r["fiscal_period"],
             r["main_account"], r["debit"], r["credit"], r["currency"], r["dim_cost_center"])
            for r in got] == [
        ("budget", "base", "E1", 2027, 3, "4000", "100.5", "0.0", "EUR", "CC1"),
        ("budget", "base", "E1", 2027, 3, "5000", "0.0", "7.0", "EUR", "")]
    assert "dim_project" not in header, "a dimension no line uses is not a column"


def test_an_access_denial_from_the_intake_refuses_the_save(env):
    env.intake.refuse = ("Not permitted to write entity E1", Denied)
    doc = _entry(env, lines=[dict(main_account="4000", debit=1, credit=0)])
    with pytest.raises(Denied):
        doc.validate()


def test_a_writer_that_judged_its_rows_is_not_judged_twice(env):
    doc = _entry(env, lines=[dict(main_account="4000", debit=1, credit=0)])
    with env.ctl.judged_by_writer():
        doc.validate()
    assert env.judged == []
    assert env.sql, "the Cycle lock is taken whoever writes"
    assert not env.frappe.flags.get("budget_entry_judged"), "the flag is put back"


# ---- review fix 3: a writer finds an entry by its name ------------------------------

def test_entry_name_is_the_autoname(env):
    # The cell path looks an entry up by this name with a locking read on the
    # primary key, so it must be the name autoname gives.
    doc = _entry(env)
    doc.autoname()
    assert env.ctl.entry_name("BCYC-1", "E1", "base", 3) == doc.name


# ---- review fix 8: the layer says what it is ----------------------------------------

def test_layer_has_help_text():
    assert _fields()["layer"].get("description"), "a non-obvious field carries help text"


def test_a_stale_save_is_a_409():
    # Review fix 4: api raises it; it lives here so api imports no frappe
    # exception class at import time.
    src = open(PY_PATH).read()
    assert "class BudgetConflictError(frappe.ValidationError)" in src and "http_status_code = 409" in src


# ---- re-review 1: the Cycle is locked before the entry ------------------------------

def test_a_save_locks_the_cycle_before_frappe_locks_the_entry(env):
    doc = _entry(env, name="BENT-ME")
    doc.check_if_latest()
    assert env.locks == [("cycle", "BCYC-1"), ("entry", "BENT-ME")]


def test_a_save_under_an_approved_cycle_is_refused_before_the_entry_is_locked(env):
    env.cycles["BCYC-1"]["docstatus"] = 1
    doc = _entry(env, name="BENT-ME")
    with pytest.raises(Thrown, match="approved"):
        doc.check_if_latest()
    assert ("entry", "BENT-ME") not in env.locks


def test_install_and_migrate_skip_the_cycle_lock_in_check_if_latest(env):
    env.frappe.flags.in_migrate = True
    _entry(env, name="BENT-ME").check_if_latest()
    assert env.locks == [("entry", "BENT-ME")]


def test_a_delete_does_not_wait_on_the_cycle_while_holding_the_entry(env):
    """Frappe locks the entry (NOWAIT) before on_trash, and no hook runs
    earlier, so the delete's Cycle check must not wait: a writer holding the
    Cycle and waiting on this entry would deadlock with it."""
    env.held_elsewhere.add("BCYC-1")
    doc = _entry(env, name="BENT-ME")
    with pytest.raises(env.frappe.QueryTimeoutError, match="BCYC-1"):
        doc.on_trash()
    assert "NOWAIT" in env.sql[-1][0]


# ---- re-review 6a: a line removed on the Desk is access-checked --------------------

def _before(env, doc, lines):
    before = _entry(env, name=doc.name, lines=lines)
    for i, line in enumerate(before.lines):
        line.name = f"row-{i}"
    doc._before = before
    return before


def test_a_removed_line_is_judged_for_access_and_writer_role(env):
    doc = _entry(env, name="BENT-ME", lines=[dict(main_account="4000", debit=1, credit=0)])
    doc.lines[0].name = "row-0"
    _before(env, doc, [dict(main_account="4000", debit=1, credit=0),
                       dict(main_account="9999", debit=5, credit=0, dim_cost_center="CC9")])
    doc.validate()
    ((entity, layer, rows),) = env.removed
    assert (entity, layer) == ("E1", "base")
    assert [(r["main_account"], r.get("dim_cost_center")) for r in rows] == [("9999", "CC9")]


def test_removing_a_line_the_user_may_not_write_is_refused(env):
    env.intake.refuse_removal = "No access to account 9999"
    doc = _entry(env, name="BENT-ME", lines=[])
    _before(env, doc, [dict(main_account="9999", debit=5, credit=0)])
    with pytest.raises(Denied, match="9999"):
        doc.validate()


def test_a_writer_that_judged_its_rows_skips_the_removal_check(env):
    doc = _entry(env, name="BENT-ME", lines=[])
    _before(env, doc, [dict(main_account="9999", debit=5, credit=0)])
    with env.ctl.judged_by_writer():
        doc.validate()
    assert env.removed == []


# ---- Q15-c: one amount basis per (cycle, entity, layer), whoever writes ----------

def test_validate_runs_the_one_basis_check_after_the_cycle_lock(env):
    doc = _entry(env, name="BENT-ME", amount_basis="Year-to-date movement")
    doc.validate()
    assert env.basis_checks == [("BCYC-1", "E1", "base", "Year-to-date movement", "BENT-ME", set())]
    assert env.locks.index(("cycle", "BCYC-1")) < env.locks.index(("basis check", "BCYC-1"))


def test_a_writer_that_judged_its_rows_still_gets_the_basis_check(env):
    # The Excel cell and the bulk load save inside judged_by_writer: the basis
    # rule spans entries, so no writer's own judgement covers it.
    doc = _entry(env, lines=[dict(main_account="4000", debit=1, credit=0)])
    with env.ctl.judged_by_writer():
        doc.validate()
    assert len(env.basis_checks) == 1


def test_a_second_basis_refuses_the_save(env):
    env.intake.refuse_basis = "Budget Entry BENT-P1 (period 1) holds amount basis 'Year-to-date movement'"
    with pytest.raises(Thrown, match="BENT-P1"):
        _entry(env).validate()


def test_entries_being_replaced_are_passed_to_the_basis_check(env):
    with env.ctl.replacing_entries({"BENT-A", "BENT-B"}):
        _entry(env, name="BENT-A").validate()
    assert env.basis_checks[0][5] == {"BENT-A", "BENT-B"}
    assert not env.frappe.flags.get("budget_entry_replacing"), "the flag is put back"


def test_validate_checks_ytd_completeness_after_the_basis_check(env):
    doc = _entry(env, name="BENT-ME", amount_basis="Year-to-date movement", fiscal_period=7)
    doc.validate()
    assert env.ytd_checks == [("BCYC-1", "E1", "base", "Year-to-date movement", 7, "BENT-ME", set())]
    assert env.locks.index(("basis check", "BCYC-1")) < env.locks.index(("ytd check", "BCYC-1"))


def test_a_whole_slice_replacement_skips_the_ytd_check(env):
    # A Budget Upload's plan judged the file's every period of the slice.
    with env.ctl.replacing_entries(set()):
        _entry(env, name="BENT-ME", amount_basis="Year-to-date movement").validate()
    assert env.ytd_checks == []


@pytest.mark.parametrize("flag", ["in_install", "in_migrate"])
def test_install_and_migrate_skip_the_basis_check(env, flag):
    setattr(env.frappe.flags, flag, True)
    _entry(env).validate()
    assert env.basis_checks == []


# ---- Q16-c: a grain takes its numbers from one writer -------------------------------

def test_validate_checks_the_lines_against_annual_inputs_after_the_cycle_lock(env):
    doc = _entry(env, name="BENT-ME", lines=[
        dict(main_account="4000", debit=1, credit=0, dim_cost_center="CC1", description="x"),
        dict(main_account="5000", debit=0, credit=2, dim_cost_center=None)])
    doc.validate()
    ((scenario, year, entity, layer, lines, entry, period),) = env.grain_checks
    assert (scenario, year, entity, layer, entry, period) == ("budget", 2027, "E1", "base", "BENT-ME", 3)
    assert lines == [{"main_account": "4000", "dim_cost_center": "CC1", "dim_project": ""},
                     {"main_account": "5000", "dim_cost_center": "", "dim_project": ""}]
    assert env.locks.index(("cycle", "BCYC-1")) < env.locks.index(("grain check", "budget"))


def test_a_writer_that_judged_its_rows_still_gets_the_grain_check(env):
    doc = _entry(env, lines=[dict(main_account="4000", debit=1, credit=0)])
    with env.ctl.judged_by_writer():
        doc.validate()
    assert len(env.grain_checks) == 1


def test_a_line_on_an_annual_input_grain_refuses_the_save(env):
    env.intake.refuse_grain = "Budget Annual Input BAI-1 already gives account 4000"
    with pytest.raises(Thrown, match="BAI-1"):
        _entry(env, lines=[dict(main_account="4000", debit=1, credit=0)]).validate()


@pytest.mark.parametrize("flag", ["in_install", "in_migrate"])
def test_install_and_migrate_skip_the_grain_check(env, flag):
    setattr(env.frappe.flags, flag, True)
    _entry(env, lines=[dict(main_account="4000", debit=1, credit=0)]).validate()
    assert env.grain_checks == []


# --- Q21-a: a delete may not leave a Year-to-date gap; a batch is judged at the end ---

YTD = "Year-to-date movement"


def test_deleting_a_ytd_entry_asks_the_intake_whether_the_slice_survives(env):
    _entry(env, name="BENT-ME", amount_basis=YTD).on_trash()
    assert env.delete_checks == [("BCYC-1", "E1", "base", YTD, "BENT-ME")]


def test_a_delete_that_opens_a_gap_is_refused(env):
    env.intake.refuse_delete = "gap"
    with pytest.raises(Thrown, match="gap"):
        _entry(env, name="BENT-ME", amount_basis=YTD).on_trash()


def test_a_whole_slice_replacement_may_delete_periods(env):
    env.intake.refuse_delete = "gap"
    with env.ctl.replacing_entries({"BENT-ME"}):
        _entry(env, name="BENT-ME", amount_basis=YTD).on_trash()
    assert env.delete_checks == []


def test_a_write_judged_at_the_end_skips_the_per_entry_ytd_check(env):
    with env.ctl.ytd_judged_at_end():
        _entry(env, name="BENT-ME", amount_basis=YTD, fiscal_period=3).validate()
    assert env.ytd_checks == []
    assert not env.frappe.flags.get("budget_entry_ytd_at_end"), "the flag is put back"


def test_the_flag_is_put_back_when_the_write_raises(env):
    with pytest.raises(RuntimeError):
        with env.ctl.ytd_judged_at_end():
            raise RuntimeError("x")
    assert not env.frappe.flags.get("budget_entry_ytd_at_end")
