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
    assert "_budget_save_cells" in content and "_save_budget_cell" in content
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


def test_budget_cell_save_does_not_validate_layer_itself():
    """The layer is judged by budget_intake against the declared Budget Layers."""
    with open(API_PATH) as f:
        content = f.read()
    assert "VALID_LAYERS" not in content


def test_budget_cell_save_upserts_period():
    """budget_cell_save must upsert a single period+layer, not replace all."""
    with open(API_PATH) as f:
        content = f.read()
    fn = content.split("def _save_budget_cell")[1].split("\ndef ")[0]
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
                                  access_calls=[], clock=0, commits=0, judged_flags=[],
                                  cycle_locks=[], cycle_refusal=None, log=[], hidden=set())

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
        # REPEATABLE READ: a plain read serves the transaction's snapshot, which
        # misses the entries in `hidden` (committed by another session since);
        # a locking read sees them.
        assert doctype == "Budget Entry", doctype
        visible = {n: e for n, e in state.entries.items() if for_update or n not in state.hidden}
        if isinstance(filters, dict):
            found = [e for e in visible.values() if _matches(e, filters)]
            return found[0]["name"] if found else None
        entry = visible.get(filters)
        return entry.get(fieldname or "name") if entry else None

    def get_doc(doctype, name, for_update=False):
        assert doctype == "Budget Entry", doctype
        if name in state.hidden and not for_update:
            raise _Thrown(f"Budget Entry {name} not found")
        stored = state.entries[name]
        doc = _Entry(json.loads(json.dumps(stored)))
        doc["lines"] = [_Row(r) for r in doc.get("lines", [])]
        bind(doc)
        return doc

    def bind(doc):
        def save(*a, **k):
            state.judged_flags.append(bool(frappe.flags.get("budget_entry_judged")))
            doc["modified"] = tick()
            state.entries[doc["name"]] = _Entry(json.loads(json.dumps(doc)))
            state.saves.append(doc["name"])
            return doc

        def insert(*a, **k):
            state.judged_flags.append(bool(frappe.flags.get("budget_entry_judged")))
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
    frappe.flags = {}
    frappe.local = types.SimpleNamespace(response=types.SimpleNamespace(http_status_code=None))
    frappe.request = types.SimpleNamespace(get_data=lambda as_text=True: json.dumps(state.body))
    frappe.get_all = get_all
    frappe.get_list = get_all
    frappe.get_roles = lambda user=None: ["Entity Accountant"]
    frappe.get_doc = get_doc
    frappe.new_doc = new_doc
    savepoints = {}

    def savepoint(name):
        savepoints[name] = (json.loads(json.dumps(state.entries)), list(state.saves), list(state.inserts))

    def rollback(save_point=None):
        if save_point is None:
            return
        entries, saves, inserts = savepoints[save_point]
        state.entries = {k: _Entry(v) for k, v in entries.items()}
        state.saves[:] = saves
        state.inserts[:] = inserts

    frappe.db = types.SimpleNamespace(get_value=get_value, rollback=rollback, savepoint=savepoint,
                                      commit=commit, set_value=forbidden, sql=forbidden)
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
    # The Budget Entry controller's guard, as the writers call it: the Cycle
    # lock (finding 2 makes it fail on demand) and the "already judged" scope.
    entry_mod = types.ModuleType("konsol.epm.doctype.budget_entry.budget_entry")

    class CycleNotDraftError(_Thrown):
        pass

    def assert_cycle_draft(cycle, action="changed"):
        state.cycle_locks.append(cycle)
        state.log.append(("lock cycle", cycle))
        if state.cycle_refusal:
            raise CycleNotDraftError(state.cycle_refusal)

    import contextlib

    @contextlib.contextmanager
    def judged_by_writer():
        was = frappe.flags.get("budget_entry_judged")
        frappe.flags["budget_entry_judged"] = True
        try:
            yield
        finally:
            frappe.flags["budget_entry_judged"] = was
    entry_mod.CycleNotDraftError = CycleNotDraftError
    entry_mod.assert_cycle_draft = assert_cycle_draft
    entry_mod.judged_by_writer = judged_by_writer
    entry_mod.entry_name = lambda cycle, entity, layer, period: f"BENT-{cycle}-{entity}-{layer}-{period}"

    for name, mod in {"frappe": frappe, "frappe.utils": utils, "konsol.clickhouse": clickhouse,
                      "konsol.group_chart": chart, "konsol.period_status": periods,
                      "konsol.entity_permissions": perms,
                      "konsol.epm.doctype.budget_entry_line.budget_entry_line": line_mod,
                      "konsol.epm.doctype.budget_entry.budget_entry": entry_mod}.items():
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

    def call(fn, body):
        state.body = body
        frappe.local.response.http_status_code = None
        return getattr(api, fn)()

    def row(**overrides):
        body = {"scenario_id": "BUD", "data_area_id": "E1", "fiscal_year": 2027,
                "main_account": "6000", "layer": "base", "currency": "EUR",
                "amount_basis": BASIS, "periods": [{"period": 1, "amount": 500}]}
        body.update(overrides)
        return {k: v for k, v in body.items() if v is not ...}

    return types.SimpleNamespace(api=api, state=state, post=post, frappe=frappe, data=data,
                                 save=lambda **o: call("budget_save", row(**o)),
                                 batch=lambda rows: call("budget_save_batch", rows), row=row)


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
    with pytest.raises(_Denied, match="Not permitted to write entity E2"):
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


# --- S2-X2: budget_save and budget_save_batch take the same path -------------

def test_budget_save_writes_one_entry_per_period(cell):
    out = cell.save(periods=[{"period": 1, "amount": 500}, {"period": 2, "amount": -70}])
    entries = sorted(cell.state.entries.values(), key=lambda e: e["fiscal_period"])
    assert [e["fiscal_period"] for e in entries] == [1, 2]
    assert [(float(e["lines"][0]["debit"]), float(e["lines"][0]["credit"])) for e in entries] == [
        (500.0, 0.0), (0.0, 70.0)]
    assert out["entries"] == [e["name"] for e in entries] and out["name"] == entries[0]["name"]


def test_budget_save_refuses_an_undeclared_period_and_a_missing_layer(cell):
    with pytest.raises(_Thrown, match="period"):
        cell.save(periods=[{"period": 14, "amount": 1}])
    with pytest.raises(_Thrown):
        cell.save(layer=...)
    assert not cell.state.entries


def test_budget_save_takes_the_layer_of_the_period(cell):
    cell.data["Budget Layer"].append({"name": "board", "writer_role": None, "sort_order": 2})
    cell.save(layer=..., periods=[{"period": 1, "amount": 5, "layer": "board"}])
    (entry,) = cell.state.entries.values()
    assert entry["layer"] == "board"


def test_budget_save_never_creates_a_cycle_or_defaults_currency(cell):
    with pytest.raises(_Thrown):
        cell.save(fiscal_year=2030)
    with pytest.raises(_Thrown, match="currency"):
        cell.save(currency=...)
    assert not cell.state.entries


def test_batch_saves_every_row(cell):
    out = cell.batch([cell.row(), cell.row(main_account="4000")])
    assert [r["index"] for r in out["results"]] == [0, 1]
    (entry,) = cell.state.entries.values()
    assert sorted(l["main_account"] for l in entry["lines"]) == ["4000", "6000"]
    assert "errors" not in out


def test_one_refused_row_refuses_the_batch_and_names_the_row(cell):
    rows = [cell.row(), cell.row(periods=[{"period": 14, "amount": 1}])]
    with pytest.raises(_Thrown, match=r"Row 2"):
        cell.batch(rows)
    assert not cell.state.entries


def test_an_ungranted_entity_refuses_the_batch_with_a_permission_error(cell):
    cell.state.denied.add("E2")
    with pytest.raises(_Denied, match=r"Row 2.*E2"):
        cell.batch([cell.row(), cell.row(data_area_id="E2")])
    assert not cell.state.entries


def test_a_batch_must_be_a_list(cell):
    with pytest.raises(_Thrown, match="array"):
        cell.batch({"scenario_id": "BUD"})


def test_the_sheet_path_is_gone_from_api():
    with open(API_PATH) as f:
        src = f.read()
    for gone in ("def _get_or_create_sheet", "def _upsert_budget_line", "Budget Sheet"):
        assert gone not in src, gone


# --- S2-X3: an access denial stays a PermissionError (HTTP 403, not 417) -------

def test_budget_save_raises_a_permission_error_for_a_denial(cell):
    cell.state.denied.add("E2")
    with pytest.raises(_Denied, match="Not permitted to write entity E2"):
        cell.save(data_area_id="E2")
    assert not cell.state.entries


def test_a_denial_wins_over_other_problems_of_the_same_cell(cell):
    # A user who may not write the entity learns that first; the cell's other
    # problems are not theirs to fix.
    cell.state.denied.add("E2")
    with pytest.raises(_Denied) as raised:
        cell.post(data_area_id="E2", fiscal_period=14)
    assert "not declared" not in str(raised.value)
    assert not cell.state.entries


def test_a_cell_refused_for_another_reason_is_still_a_validation_error(cell):
    with pytest.raises(_Thrown):
        cell.post(fiscal_period=14)


# --- S2-X3: messages read right for each writer ----------------------------------

def test_a_cell_refusal_names_no_file_line(cell):
    with pytest.raises(_Thrown) as raised:
        cell.post(main_account="9999")
    assert "9999" in str(raised.value)
    assert "Line" not in str(raised.value), str(raised.value)


def test_a_cell_denial_names_no_file_line(cell):
    cell.state.denied.add("E2")
    with pytest.raises(_Denied) as raised:
        cell.post(data_area_id="E2")
    assert str(raised.value) == "Not permitted to write entity E2"


def test_a_batch_refusal_names_the_row_once(cell):
    with pytest.raises(_Thrown) as raised:
        cell.batch([cell.row(), cell.row(main_account="9999")])
    message = str(raised.value)
    assert message.startswith("Row 2: "), message
    assert "Line" not in message, message
    assert "9999" in message, message


# --- review fix 1: the cell path tells the entry it has judged the row ------------

def test_the_cell_path_saves_the_entry_as_judged_and_only_there(cell):
    # The cell is judged once by the intake; Budget Entry's validate must not
    # judge it again (nor judge the other users' lines the entry holds).
    cell.post(amount=100)
    cell.post(amount=200)
    assert cell.state.judged_flags == [True, True]
    assert not cell.frappe.flags.get("budget_entry_judged")


# --- review fix 2: the cell path locks the Cycle first ---------------------------

def test_the_cell_path_locks_the_cycle_before_the_entry(cell):
    cell.post(amount=100)
    cell.post(amount=200)
    assert cell.state.cycle_locks == ["BCYC-BUD-2027", "BCYC-BUD-2027"]


def test_a_cell_written_after_the_cycle_is_approved_is_refused(cell):
    # The intake's own Cycle check reads a snapshot; the locking read sees the
    # submit another session committed since.
    cell.post(amount=100)
    cell.state.cycle_refusal = "Budget Cycle BCYC-BUD-2027 is approved"
    with pytest.raises(_Thrown, match="BCYC-BUD-2027 is approved"):
        cell.post(amount=200)
    with pytest.raises(_Thrown, match="approved"):
        cell.post(amount=5, fiscal_period=2)
    (entry,) = cell.state.entries.values()
    assert float(entry["lines"][0]["debit"]) == 100.0


# --- review fix 3: the create race finds the other session's entry -----------------

def test_an_entry_another_session_committed_is_found_and_updated(cell):
    # Another session created the entry after this transaction's snapshot: a
    # plain lookup misses it, the insert hits the duplicate name, and the
    # retry must find it with a locking read and update it.
    cell.post(amount=100)
    (name,) = cell.state.entries
    cell.state.hidden = {name}
    out = cell.post(amount=70, main_account="4000")
    assert out["status"] == "ok" and out["name"] == name
    got = sorted((l["main_account"], float(l["debit"])) for l in cell.state.entries[name]["lines"])
    assert got == [("4000", 70.0), ("6000", 100.0)]


# --- review fix 4: a 409 conflict is never counted as a save -----------------------

def _stale(cell):
    first = cell.post(amount=100)
    cell.post(amount=200)
    return first["modified"]


def test_budget_save_raises_a_409_when_a_period_is_stale(cell):
    stale = _stale(cell)
    with pytest.raises(cell.api.BudgetConflictError) as raised:
        cell.save(periods=[{"period": 1, "amount": 9, "base_modified": stale}])
    assert raised.value.http_status_code == 409
    assert "BENT-BCYC-BUD-2027-E1-base-1" in str(raised.value)
    (line,) = _only_entry(cell)["lines"]
    assert float(line["debit"]) == 200.0


def test_a_batch_with_a_stale_period_rolls_back_whole_and_raises_the_409(cell):
    stale = _stale(cell)
    before = json.loads(json.dumps(cell.state.entries))
    rows = [cell.row(main_account="4000"),
            cell.row(periods=[{"period": 2, "amount": 3},
                              {"period": 1, "amount": 9, "base_modified": stale}])]
    with pytest.raises(cell.api.BudgetConflictError) as raised:
        cell.batch(rows)
    assert raised.value.http_status_code == 409
    assert str(raised.value).startswith("Row 2: ")
    assert json.loads(json.dumps(cell.state.entries)) == before, "nothing of the batch stands"


# --- review fix 7a: a layer's writer role refuses with a 403 ----------------------

def test_a_cell_in_a_layer_whose_writer_role_the_user_lacks_is_a_permission_error(cell):
    cell.data["Budget Layer"].append({"name": "board", "writer_role": "Budget Approver", "sort_order": 2})
    with pytest.raises(_Denied, match="Budget Approver"):
        cell.post(layer="board")
    assert not cell.state.entries


# --- review fix 7b: a cell is not told about a file --------------------------------

def test_a_cell_in_the_wrong_currency_is_not_told_about_a_file(cell):
    with pytest.raises(_Thrown) as raised:
        cell.post(currency="USD")
    message = str(raised.value)
    assert "USD" in message and "EUR" in message
    assert "file" not in message.lower(), message


def test_a_cell_without_a_basis_is_not_told_about_a_file_or_upload(cell):
    with pytest.raises(_Thrown) as raised:
        cell.post(amount_basis=...)
    message = str(raised.value)
    assert "Amount Basis is required" in message
    assert "file" not in message.lower() and "upload" not in message.lower(), message
