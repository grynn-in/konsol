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

    class Document:
        def get(self, key, default=None):
            return getattr(self, key, default)

        def set(self, key, value):
            setattr(self, key, value)

        def is_new(self):
            return getattr(self, "__islocal", None)

    def throw(msg, exc=None, *a, **k):
        raise (exc or Thrown)(msg)

    sql_calls = []

    def sql(query, values=None, *a, **k):
        sql_calls.append((" ".join(query.split()), values))
        assert "FOR UPDATE" in query and "`tabBudget Cycle`" in query, query
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
                                 judged=judged, intake=intake)


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


@pytest.mark.parametrize("docstatus, word", [(1, "approved"), (2, "cancelled")])
def test_an_entry_under_a_cycle_that_is_not_a_draft_cannot_be_deleted(env, docstatus, word):
    env.cycles["BCYC-1"]["docstatus"] = docstatus
    doc = _entry(env, name="BENT-ME")
    with pytest.raises(Thrown, match=word):
        doc.on_trash()
    assert env.sql and "FOR UPDATE" in env.sql[0][0]


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
