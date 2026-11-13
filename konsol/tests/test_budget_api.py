"""TDD tests for Budget Save API endpoints."""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")


def test_budget_save_endpoint_exists():
    """Must have a budget_save function."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "budget_save" in func_names


def test_budget_save_batch_endpoint_exists():
    """Must have a budget_save_batch function."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "budget_save_batch" in func_names


def test_budget_save_validates_fields():
    """Must validate required fields."""
    with open(API_PATH) as f:
        content = f.read()
    assert "_validate_budget_fields" in content
    assert "scenario_id" in content
    assert "data_area_id" in content


def test_budget_save_upserts():
    """Must upsert (create or update) the sheet/line based on composite key."""
    with open(API_PATH) as f:
        content = f.read()
    assert "_upsert_budget_line" in content
    # Must check for existing doc before creating
    assert "get_all" in content or "get_list" in content


def test_budget_save_is_whitelisted():
    """budget_save must be whitelisted."""
    with open(API_PATH) as f:
        content = f.read()
    # Check that there's a whitelist decorator near budget_save
    assert "whitelist" in content


def test_budget_save_is_post_only():
    """budget_save must only accept POST."""
    with open(API_PATH) as f:
        content = f.read()
    # Check for methods=["POST"] near budget_save
    assert 'methods=["POST"]' in content


# --- budget_cell_save API ---

def test_budget_cell_save_endpoint_exists():
    """Must have a budget_cell_save function."""
    with open(API_PATH) as f:
        tree = ast.parse(f.read())
    func_names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "budget_cell_save" in func_names


def test_budget_cell_save_validates_layer():
    """budget_cell_save must validate layer against allowed values."""
    with open(API_PATH) as f:
        content = f.read()
    assert "VALID_LAYERS" in content
    assert "base" in content
    assert "challenge" in content


def test_budget_cell_save_upserts_period():
    """budget_cell_save must upsert a single period+layer, not replace all."""
    with open(API_PATH) as f:
        content = f.read()
    fn = content.split("def budget_cell_save")[1].split("\ndef ")[0]
    # Must check for existing period+layer row
    assert "fiscal_period" in fn
    assert "layer" in fn


# --- S2-X1: budget_cell_save writes one Budget Entry Line through budget_intake
#
# api.py is loaded against a stubbed frappe whose rows are plain dicts, with
# the real budget_intake and budget_bulk_model judging the cell. Only the
# facts budget_intake reads off other konsol modules (chart, periods, bypass
# roles) and the access check are stubbed.

import importlib.util
import json
import sys
import types

import pytest

INTAKE_PATH = os.path.join(APP_DIR, "budget_intake.py")
BASIS = "Period movement"


class _Thrown(Exception):
    pass


class _Denied(Exception):
    pass


class _Duplicate(Exception):
    pass


def _matches(row, filters):
    for field, cond in (filters or {}).items():
        value = row.get(field)
        if isinstance(cond, (list, tuple)):
            op, arg = cond
            if op == "in" and value not in arg:
                return False
            if op != "in":
                raise AssertionError(f"stub does not know operator {op}")
        elif value != cond:
            return False
    return True


class _Row(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value

    def set(self, name, value):
        self[name] = value


class _Entry(_Row):
    """A Budget Entry as the stub stores it: a dict with `lines`."""

    def append(self, table, values):
        row = _Row(values)
        self.setdefault(table, []).append(row)
        return row

    def update(self, values):
        dict.update(self, values)


@pytest.fixture
def cell(monkeypatch):
    data = {
        "Scenario": [{"name": "BUD", "scenario_type": "budget", "is_active": 1}],
        "Budget Layer": [{"name": "base", "writer_role": None, "sort_order": 1}],
        "Budget Cycle": [
            {"name": "BCYC-BUD-2027", "scenario_id": "BUD", "fiscal_year": 2027, "docstatus": 0},
        ],
        "Entity": [{"name": "E1", "functional_currency": "EUR"},
                   {"name": "E2", "functional_currency": "EUR"}],
        "ISO Currency": [{"name": "EUR", "minor_unit": 2}],
        "Dimension": [{"dimension_name": "dim_cost_center", "status": "Published", "in_budget": 1,
                       "in_trial_balance": 1}],
    }
    state = types.SimpleNamespace(entries={}, saves=[], inserts=[], denied=set(), body=None,
                                  access_calls=[], clock=0, commits=0)

    def get_all(doctype, filters=None, fields=None, order_by=None, pluck=None, limit_page_length=None,
                limit=None, **kw):
        rows = [r for r in data[doctype] if _matches(r, filters)]
        if pluck:
            return [r[pluck] for r in rows]
        if fields:
            rows = [{f: r.get(f) for f in fields} for r in rows]
        return [_Row(r) for r in rows]

    def tick():
        state.clock += 1
        return f"2027-01-01 00:00:{state.clock:02d}"

    def get_value(doctype, filters, fieldname=None, for_update=False, **kw):
        assert doctype == "Budget Entry", doctype
        if isinstance(filters, dict):
            found = [e for e in state.entries.values() if _matches(e, filters)]
            return found[0]["name"] if found else None
        entry = state.entries.get(filters)
        return entry.get(fieldname or "name") if entry else None

    def get_doc(doctype, name):
        assert doctype == "Budget Entry", doctype
        stored = state.entries[name]
        doc = _Entry(json.loads(json.dumps(stored)))
        doc["lines"] = [_Row(r) for r in doc.get("lines", [])]
        bind(doc)
        return doc

    def bind(doc):
        def save(*a, **k):
            doc["modified"] = tick()
            state.entries[doc["name"]] = _Entry(json.loads(json.dumps(doc)))
            state.saves.append(doc["name"])
            return doc

        def insert(*a, **k):
            name = "BENT-" + "-".join(str(doc.get(f)) for f in
                                       ("cycle", "data_area_id", "layer", "fiscal_period"))
            if name in state.entries:
                raise _Duplicate(name)
            doc["name"] = name
            doc["modified"] = tick()
            state.entries[name] = _Entry(json.loads(json.dumps(doc)))
            state.inserts.append(name)
            return doc
        doc.__dict__["save"] = save
        doc.__dict__["insert"] = insert

    def new_doc(doctype):
        assert doctype == "Budget Entry", f"budget_cell_save must not create a {doctype}"
        doc = _Entry(doctype="Budget Entry", lines=[])
        bind(doc)
        return doc

    def forbidden(*a, **k):
        raise AssertionError("budget_cell_save must not commit")

    def commit():
        state.commits += 1

    frappe = types.ModuleType("frappe")
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.throw = lambda msg, exc=None, *a, **k: (_ for _ in ()).throw((exc or _Thrown)(msg))
    frappe._ = lambda s: s
    frappe.ValidationError = _Thrown
    frappe.PermissionError = _Denied
    frappe.exceptions = types.SimpleNamespace(DuplicateEntryError=_Duplicate)
    frappe.DuplicateEntryError = _Duplicate
    frappe.session = types.SimpleNamespace(user="ann@example.com")
    frappe.local = types.SimpleNamespace(response=types.SimpleNamespace(http_status_code=None))
    frappe.request = types.SimpleNamespace(get_data=lambda as_text=True: json.dumps(state.body))
    frappe.get_all = get_all
    frappe.get_list = get_all
    frappe.get_roles = lambda user=None: ["Entity Accountant"]
    frappe.get_doc = get_doc
    frappe.new_doc = new_doc
    frappe.db = types.SimpleNamespace(get_value=get_value, rollback=lambda: None, commit=commit,
                                      set_value=forbidden, sql=forbidden)
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: None
    frappe.utils = utils

    chart = types.ModuleType("konsol.group_chart")
    chart.chart_accounts = lambda: {"4000": {"is_group": 0, "is_posting": 1},
                                    "6000": {"is_group": 0, "is_posting": 1}}
    periods = types.ModuleType("konsol.period_status")

    class PeriodNotDeclared(Exception):
        pass

    def period_row(year, period):
        # A 13-period calendar: P13 is a declared Regular period.
        if int(year) in (2027, 2030) and 1 <= int(period) <= 13:
            return {"code": f"P{int(period):02d}", "type": "Regular", "status": "Open"}
        raise PeriodNotDeclared(f"{year} P{period} not declared")
    periods.PeriodNotDeclared = PeriodNotDeclared
    periods.period_row = period_row
    perms = types.ModuleType("konsol.entity_permissions")
    perms.BYPASS_ROLES = frozenset({"System Manager", "Administrator"})
    line_mod = types.ModuleType("konsol.epm.doctype.budget_entry_line.budget_entry_line")
    line_mod.budget_dimension_fields = lambda: ["dim_cost_center"]
    clickhouse = types.SimpleNamespace(connection_url=lambda s: "", get_connection=lambda: {})

    for name, mod in {"frappe": frappe, "frappe.utils": utils, "konsol.clickhouse": clickhouse,
                      "konsol.group_chart": chart, "konsol.period_status": periods,
                      "konsol.entity_permissions": perms,
                      "konsol.epm.doctype.budget_entry_line.budget_entry_line": line_mod}.items():
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("_budget_intake_x1", INTAKE_PATH)
    intake = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(intake)
    monkeypatch.setitem(sys.modules, "konsol.budget_intake", intake)

    spec = importlib.util.spec_from_file_location("_api_x1", API_PATH)
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    monkeypatch.setitem(sys.modules, "konsol.api", api)

    def access(row):
        state.access_calls.append(dict(row))
        if row.get("data_area_id") in state.denied:
            raise _Denied(f"Not permitted to write entity {row.get('data_area_id')}")
    api._assert_budget_write_access = access

    def post(**overrides):
        body = {"scenario_id": "BUD", "data_area_id": "E1", "fiscal_year": 2027,
                "main_account": "6000", "fiscal_period": 1, "amount": 500, "layer": "base",
                "currency": "EUR", "amount_basis": BASIS,
                "dimensions": {"dim_cost_center": "CC1"}}
        body.update(overrides)
        state.body = {k: v for k, v in body.items() if v is not ...}
        frappe.local.response.http_status_code = None
        return api.budget_cell_save()

    return types.SimpleNamespace(api=api, state=state, post=post, frappe=frappe, data=data)


def _only_entry(cell):
    (entry,) = cell.state.entries.values()
    return entry


def test_cell_save_stores_a_positive_amount_as_debit(cell):
    out = cell.post(amount=500)
    assert out["status"] == "ok", out
    entry = _only_entry(cell)
    assert (entry["cycle"], entry["data_area_id"], entry["layer"], entry["fiscal_period"]) == (
        "BCYC-BUD-2027", "E1", "base", 1)
    (line,) = entry["lines"]
    assert (line["main_account"], line["dim_cost_center"]) == ("6000", "CC1")
    assert (float(line["debit"]), float(line["credit"])) == (500.0, 0.0)


def test_cell_save_stores_a_negative_amount_as_credit(cell):
    cell.post(amount=-500)
    (line,) = _only_entry(cell)["lines"]
    assert (float(line["debit"]), float(line["credit"])) == (0.0, 500.0)


def test_cell_save_accepts_p13_on_a_13_period_calendar(cell):
    out = cell.post(fiscal_period=13)
    assert out["status"] == "ok", out
    assert _only_entry(cell)["fiscal_period"] == 13


def test_cell_save_refuses_an_undeclared_period(cell):
    with pytest.raises(_Thrown, match="not declared"):
        cell.post(fiscal_period=14)
    assert not cell.state.entries


@pytest.mark.parametrize("layer", ["", ...])
def test_cell_save_refuses_a_blank_layer_and_defaults_none(cell, layer):
    with pytest.raises(_Thrown, match="layer"):
        cell.post(layer=layer)
    assert not cell.state.entries


def test_cell_save_refuses_a_missing_cycle_and_creates_none(cell):
    with pytest.raises(_Thrown, match="No Budget Cycle for scenario BUD, fiscal year 2030"):
        cell.post(fiscal_year=2030)
    assert not cell.state.entries
    assert [c["name"] for c in cell.data["Budget Cycle"]] == ["BCYC-BUD-2027"]


def test_cell_save_goes_through_the_access_check(cell):
    cell.state.denied = {"E2"}
    with pytest.raises(Exception, match="Not permitted to write entity E2"):
        cell.post(data_area_id="E2")
    assert not cell.state.entries
    assert cell.state.access_calls and cell.state.access_calls[-1]["main_account"] == "6000"


@pytest.mark.parametrize("field", ["currency", "amount_basis"])
def test_cell_save_defaults_neither_currency_nor_basis(cell, field):
    with pytest.raises(_Thrown):
        cell.post(**{field: ...})
    assert not cell.state.entries


def test_a_second_cell_updates_its_line_and_a_new_account_adds_one(cell):
    cell.post(amount=500)
    cell.post(amount=-200)
    cell.post(amount=70, main_account="4000")
    cell.post(amount=10, dimensions={"dim_cost_center": "CC2"})
    entry = _only_entry(cell)
    got = sorted((r["main_account"], r["dim_cost_center"], float(r["debit"]), float(r["credit"]))
                 for r in entry["lines"])
    assert got == [("4000", "CC1", 70.0, 0.0), ("6000", "CC1", 0.0, 200.0), ("6000", "CC2", 10.0, 0.0)]
    assert len(cell.state.inserts) == 1


def test_another_period_is_another_entry(cell):
    cell.post(fiscal_period=1)
    cell.post(fiscal_period=2)
    assert sorted(e["fiscal_period"] for e in cell.state.entries.values()) == [1, 2]


def test_cell_save_returns_the_entry_and_its_modified(cell):
    out = cell.post(amount=-500)
    entry = _only_entry(cell)
    assert out == {"status": "ok", "name": entry["name"], "value": -500,
                   "modified": entry["modified"]}


def test_a_stale_base_modified_is_a_409_conflict_and_saves_nothing(cell):
    first = cell.post(amount=-500)
    cell.post(amount=300)
    saves = list(cell.state.saves)
    out = cell.post(amount=999, base_modified=first["modified"])
    assert cell.frappe.local.response.http_status_code == 409
    assert out["status"] == "conflict"
    assert out["current_amount"] == 300
    assert out["your_amount"] == 999
    assert out["current_modified"] == _only_entry(cell)["modified"]
    assert cell.state.saves == saves


def test_a_current_base_modified_saves(cell):
    first = cell.post(amount=100)
    out = cell.post(amount=200, base_modified=first["modified"])
    assert out["status"] == "ok"


def test_a_cell_on_a_new_account_in_a_stale_entry_is_a_conflict_with_no_current_amount(cell):
    first = cell.post(amount=100)
    cell.post(amount=200)
    out = cell.post(amount=5, main_account="4000", base_modified=first["modified"])
    assert out["status"] == "conflict" and out["current_amount"] is None


def test_a_cell_whose_basis_differs_from_its_entry_is_refused(cell):
    cell.post(amount=100)
    with pytest.raises(_Thrown, match="Year-to-date movement"):
        cell.post(amount=200, amount_basis="Year-to-date movement")
    (line,) = _only_entry(cell)["lines"]
    assert float(line["debit"]) == 100.0


def test_cell_save_never_commits(cell):
    cell.post(amount=100)
    cell.post(amount=200)
    assert cell.state.commits == 0


def test_cell_save_is_post_only_and_has_no_clamp_or_auto_create():
    with open(API_PATH) as f:
        src = f.read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "budget_cell_save")
    decorators = [ast.get_source_segment(src, d) for d in fn.decorator_list]
    assert decorators == ['frappe.whitelist(methods=["POST"])'], decorators
    body = ast.get_source_segment(src, fn)
    for gone in ("fiscal_period must be 1-12", "normalize_layer", "_resolve_budget_cycle",
                 "Budget Sheet", "frappe.db.commit"):
        assert gone not in body, gone
    assert "budget_intake" in body
