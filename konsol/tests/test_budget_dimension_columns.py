"""The budget input tables carry the site's declared budget dimensions (konsol#287).

`epm_gold.budget_annual_input` and `epm_gold.budget_monthly_input` were created
with `dim_cost_center` and `dim_department` as literal columns: two customers'
dimensions in shipped DDL, after the decision that Dimension ships nothing. A
site budgeting by `dim_region` then had Budget Sheet write a column the table
did not have, and the write-through failed. Budget Annual Input had the same
two names as fixed fields.

Now the tables name no dimension. The Published Dimensions ticked `in_budget`
decide their `dim_*` columns, added by `_sync_budget_dimension_columns`, and
Budget Annual Input carries them as Custom Fields, as Budget Line does.

Site-free: schema_apply.py, the controller and install.py are loaded against
stubs, so every assertion is on what WOULD be run.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUDGET_TABLES = ("epm_gold.budget_annual_input", "epm_gold.budget_monthly_input")
#: What the tables carry once created, before any sync: no dimension column.
BASE_COLUMNS = ["scenario_id", "data_area_id", "fiscal_year", "main_account"]


class _D(dict):
    __getattr__ = dict.get


def _load_isolated(path, name, stubs):
    """Exec ``path`` as module ``name`` with ``stubs`` in sys.modules, then put
    sys.modules back as it was."""
    before = set(sys.modules)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for key in set(sys.modules) - before:
            if key.split(".")[0] in ("frappe", "konsol"):
                del sys.modules[key]
        for key, mod in saved.items():
            if mod is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = mod
    return module


# --------------------------------------------------------------------------
# schema_apply._sync_budget_dimension_columns
# --------------------------------------------------------------------------
def _schema_apply(declared, columns):
    """schema_apply against a fake ClickHouse whose tables have ``columns``
    (table -> list), replaying each ALTER onto them. ``declared`` maps a
    Dimension flag to the names Published with it ticked."""
    sql, queries = [], []

    def get_all(doctype, **kwargs):
        queries.append((doctype, kwargs))
        if doctype != "Dimension":
            return []
        flags = [k for k in (kwargs.get("filters") or {}) if k != "status"]
        return [_D(dimension_name=n) for f in flags for n in declared.get(f, ())]

    def execute(statement, params=None):
        sql.append(statement)
        text = " ".join(statement.split())
        if text.startswith("SELECT name FROM system.columns"):
            database = text.split("database = '")[1].split("'")[0]
            table = text.split("table = '")[1].split("'")[0]
            return "\n".join(columns.get(f"{database}.{table}", []))
        if text.startswith("ALTER TABLE"):
            table = text.split()[2]
            columns.setdefault(table, []).append(text.split("IF NOT EXISTS ")[1].split()[0])
        return ""

    fake = types.ModuleType("frappe")
    fake.get_all = get_all
    fake.log_error = lambda *a, **k: None
    fake.get_traceback = lambda: ""
    fake.whitelist = lambda *a, **k: (lambda fn: fn)
    fake.logger = lambda: types.SimpleNamespace(exception=lambda *a, **k: None)
    utils = types.ModuleType("frappe.utils")
    utils.cint = int
    fake.utils = utils
    module = _load_isolated(
        os.path.join(APP_DIR, "schema_apply.py"), "_host_schema_apply_k287",
        {"frappe": fake, "frappe.utils": utils,
         "konsol.clickhouse": types.SimpleNamespace(execute=execute, get_connection=lambda: {}),
         "konsol.dbt_config": types.SimpleNamespace(regenerate_vars=lambda *a, **k: None)})
    return module, sql, queries


def _alters(sql):
    return [" ".join(s.split()) for s in sql if s.lstrip().upper().startswith("ALTER")]


def test_a_declared_budget_dimension_is_added_to_both_budget_tables():
    columns = {t: list(BASE_COLUMNS) for t in BUDGET_TABLES}
    module, sql, _ = _schema_apply({"in_budget": ["dim_region"]}, columns)
    actions = module._sync_budget_dimension_columns()
    assert actions == [f"{t}: added dim_region" for t in BUDGET_TABLES], actions
    assert _alters(sql) == [
        f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS dim_region String DEFAULT ''"
        for t in BUDGET_TABLES]


def test_only_in_budget_dimensions_reach_the_budget_tables():
    """The trial balance's and the journal's dimensions are their own sets."""
    columns = {t: list(BASE_COLUMNS) for t in BUDGET_TABLES}
    module, _, queries = _schema_apply(
        {"in_trial_balance": ["dim_tb"], "in_journal": ["dim_j"]}, columns)
    assert module._sync_budget_dimension_columns() == []
    flags = {k for d, kw in queries if d == "Dimension" for k in kw["filters"]}
    assert flags == {"in_budget", "status"}, flags


def test_an_undeclared_budget_column_is_never_dropped():
    """An existing site's tables keep dim_cost_center and dim_department, with
    the budget values written into them, whatever the site declares now."""
    columns = {t: [*BASE_COLUMNS, "dim_cost_center", "dim_department"] for t in BUDGET_TABLES}
    module, sql, _ = _schema_apply({"in_budget": ["dim_region"]}, columns)
    module._sync_budget_dimension_columns()
    assert not [s for s in _alters(sql) if "DROP" in s], sql
    for t in BUDGET_TABLES:
        assert {"dim_cost_center", "dim_department", "dim_region"} <= set(columns[t])


def test_the_budget_column_sync_is_idempotent():
    columns = {t: list(BASE_COLUMNS) for t in BUDGET_TABLES}
    module, sql, _ = _schema_apply({"in_budget": ["dim_region"]}, columns)
    module._sync_budget_dimension_columns()
    first = len(_alters(sql))
    assert module._sync_budget_dimension_columns() == []
    assert len(_alters(sql)) == first


def test_apply_schema_runs_the_budget_column_sync_and_reports_it():
    columns = {t: list(BASE_COLUMNS) for t in BUDGET_TABLES}
    module, _, _ = _schema_apply({"in_budget": ["dim_region"]}, columns)
    summary = module._apply_schema_steps()
    assert summary["budget_dimension_columns_synced"] == [
        f"{t}: added dim_region" for t in BUDGET_TABLES], summary
    assert not summary["errors"], summary["errors"]


def test_a_failing_budget_column_sync_is_reported_not_fatal():
    module, _, _ = _schema_apply({}, {})

    def boom():
        raise RuntimeError("boom")

    module._sync_budget_dimension_columns = boom
    summary = module._apply_schema_steps()
    assert "Budget dimension columns: boom" in summary["errors"], summary["errors"]
    assert summary["budget_dimension_columns_synced"] == []
    assert summary["vars_updated"] is True


# --------------------------------------------------------------------------
# Budget Annual Input: the field map and the grain follow the declared set
# --------------------------------------------------------------------------
def _controller(declared, provisioned):
    """The Budget Annual Input controller against a site declaring ``declared``
    in_budget, whose doctype has Custom Fields for ``provisioned``."""
    exists_calls = []

    class Document:
        def get(self, key):
            return getattr(self, key, None)

    meta = types.SimpleNamespace(has_field=lambda f: f in provisioned)
    fake = types.ModuleType("frappe")
    fake.get_meta = lambda doctype: meta
    fake.db = types.SimpleNamespace(exists=lambda dt, f: exists_calls.append(f) or None)
    fake.throw = lambda msg, *a, **k: (_ for _ in ()).throw(ValueError(msg))
    fake.flags = types.SimpleNamespace(in_install=False, in_migrate=False, in_import=False)
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = Document
    module = _load_isolated(
        os.path.join(APP_DIR, "epm", "doctype", "budget_annual_input", "budget_annual_input.py"),
        "_host_budget_annual_input_k287",
        {"frappe": fake, "frappe.model": model, "frappe.model.document": document,
         "konsol.clickhouse": types.SimpleNamespace(sync_doctype_after_commit=lambda *a: None),
         "konsol.epm.budget_grain": types.SimpleNamespace(
             budget_dimension_names=lambda: sorted(declared))})
    return module, exists_calls


def test_the_field_map_carries_the_declared_budget_dimensions():
    """reconcile_all reads CH_FIELD_MAP off the class, so it is checked there."""
    module, _ = _controller({"dim_region", "dim_product"}, {"dim_region", "dim_product"})
    field_map = module.BudgetAnnualInput.CH_FIELD_MAP
    assert field_map == {**module.FIXED_FIELD_MAP, "dim_product": "dim_product",
                         "dim_region": "dim_region"}, field_map


def test_the_field_map_follows_the_declared_set_when_it_changes():
    declared = {"dim_region"}
    module, _ = _controller(declared, {"dim_region", "dim_product"})
    assert "dim_product" not in module.BudgetAnnualInput.CH_FIELD_MAP
    declared.add("dim_product")
    assert "dim_product" in module.BudgetAnnualInput.CH_FIELD_MAP


def test_a_declared_dimension_without_its_field_yet_is_left_out():
    """Named, every query on the doctype would fail on a missing column."""
    module, _ = _controller({"dim_region", "dim_product"}, {"dim_region"})
    assert "dim_product" not in module.BudgetAnnualInput.CH_FIELD_MAP
    assert "dim_region" in module.BudgetAnnualInput.CH_FIELD_MAP


def test_no_fixed_dimension_is_left_in_the_field_map():
    module, _ = _controller(set(), set())
    assert not [k for k in module.BudgetAnnualInput.CH_FIELD_MAP if k.startswith("dim_")]


def test_the_unique_grain_includes_the_declared_dimensions():
    """Two rows differing only by a budget dimension are different budget lines."""
    module, exists_calls = _controller({"dim_region"}, {"dim_region"})
    doc = module.BudgetAnnualInput()
    doc.__dict__.update(name="BAI-1", scenario_id="BUD", data_area_id="ZZA",
                        fiscal_year=2026, main_account="ZZ1000", dim_region="")
    doc._validate_unique_grain()
    assert exists_calls[-1]["dim_region"] == "", exists_calls
    doc.dim_region = "EMEA"
    doc._validate_unique_grain()
    assert exists_calls[-1]["dim_region"] == "EMEA", exists_calls


# --------------------------------------------------------------------------
# after_migrate: before the reconcile, and again after it
# --------------------------------------------------------------------------
def _after_migrate_calls():
    with open(os.path.join(APP_DIR, "install.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "after_migrate")
    return [n.value.func.id for n in fn.body
            if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name)]


def test_migrate_syncs_the_budget_columns_after_the_reconcile_creates_the_tables():
    """On a fresh site the reconcile is what creates the two tables
    (ensure_reference_tables), so a sync that only ran before it found nothing
    to alter and the first budget write named a column the table lacked."""
    calls = _after_migrate_calls()
    reconcile = calls.index("_reconcile_clickhouse")
    assert "_sync_budget_dimension_columns" in calls[reconcile + 1:], calls


def test_migrate_also_syncs_them_before_the_reconcile_rewrites_the_rows():
    """The reconcile rewrites Budget Annual Input's rows, naming the declared
    columns; on an existing site they must be there first."""
    calls = _after_migrate_calls()
    reconcile = calls.index("_reconcile_clickhouse")
    before = calls[:reconcile]
    assert "_sync_budget_line_custom_fields" in before, calls
    with open(os.path.join(APP_DIR, "install.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
              and n.name == "_sync_budget_line_custom_fields")
    called = {n.func.id for n in ast.walk(fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "_sync_budget_dimension_columns" in called, called
