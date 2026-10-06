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
# Budget Annual Input: the field map and the grain follow its dimension fields
# --------------------------------------------------------------------------
def _controller(fields):
    """The Budget Annual Input controller whose doctype meta has ``fields``
    (a set, mutable by the caller) besides its standard ones."""
    exists_calls = []

    class Document:
        def get(self, key):
            return getattr(self, key, None)

    standard = ["scenario_id", "data_area_id", "fiscal_year", "main_account",
                "annual_amount", "spread_profile_id", "submitted_by"]
    class Meta:
        @property
        def fields(self):
            return [types.SimpleNamespace(fieldname=f) for f in [*standard, *sorted(fields)]]

    meta = Meta()
    fake = types.ModuleType("frappe")
    fake.get_meta = lambda doctype: meta
    fake.get_all = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no registry query: the field set is read off meta"))
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
         "konsol.clickhouse": types.SimpleNamespace(sync_doctype_after_commit=lambda *a: None)})
    return module, exists_calls


def test_the_field_map_carries_the_doctypes_dimension_fields():
    """reconcile_all reads CH_FIELD_MAP off the class, so it is checked there."""
    module, _ = _controller({"dim_region", "dim_product"})
    field_map = module.BudgetAnnualInput.CH_FIELD_MAP
    assert field_map == {**module.FIXED_FIELD_MAP, "dim_product": "dim_product",
                         "dim_region": "dim_region"}, field_map


def test_the_field_map_follows_the_fields_when_the_sync_changes_them():
    fields = {"dim_region"}
    module, _ = _controller(fields)
    assert "dim_product" not in module.BudgetAnnualInput.CH_FIELD_MAP
    fields.add("dim_product")   # the Custom Field sync provisioned it
    assert "dim_product" in module.BudgetAnnualInput.CH_FIELD_MAP


def test_no_fixed_dimension_is_left_in_the_field_map():
    module, _ = _controller(set())
    assert module.BudgetAnnualInput.CH_FIELD_MAP == module.FIXED_FIELD_MAP
    assert not [k for k in module.FIXED_FIELD_MAP if k.startswith("dim_")]


def test_the_unique_grain_includes_the_dimension_fields():
    """Two rows differing only by a budget dimension are different budget lines."""
    module, exists_calls = _controller({"dim_region"})
    doc = module.BudgetAnnualInput()
    doc.__dict__.update(name="BAI-1", scenario_id="BUD", data_area_id="ZZA",
                        fiscal_year=2026, main_account="ZZ1000", dim_region="EMEA")
    doc._validate_unique_grain()
    assert exists_calls[-1]["dim_region"] == "EMEA", exists_calls


def test_a_blank_dimension_in_the_grain_matches_null_too():
    """A dimension field added after rows exist is NULL on every one of them.
    Filtered as '', the check missed the row it duplicates, and
    gold_spread_budget (no dedup) doubled the annual budget."""
    module, exists_calls = _controller({"dim_region"})
    doc = module.BudgetAnnualInput()
    doc.__dict__.update(name="BAI-1", scenario_id="BUD", data_area_id="ZZA",
                        fiscal_year=2026, main_account="ZZ1000", dim_region=None)
    doc._validate_unique_grain()
    assert exists_calls[-1]["dim_region"] == ["is", "not set"], exists_calls


# --------------------------------------------------------------------------
# The columns are added where the tables are created
# --------------------------------------------------------------------------
def _ensure_reference_tables(sync):
    """Run clickhouse.ensure_reference_tables with ``sync`` standing in for
    schema_apply._sync_budget_dimension_columns; return the SQL it ran."""
    sql = []
    fake = types.ModuleType("frappe")
    fake.logger = lambda: types.SimpleNamespace(warning=lambda *a, **k: None,
                                                info=lambda *a, **k: None)
    fake.flags = types.SimpleNamespace(in_install=False, in_import=False,
                                       in_migrate=False, in_patch=False)
    schema_apply = types.ModuleType("konsol.schema_apply")
    schema_apply._sync_budget_dimension_columns = lambda: sync(list(sql))
    module = _load_isolated(os.path.join(APP_DIR, "clickhouse.py"), "_host_clickhouse_k287",
                            {"frappe": fake})
    module.execute = lambda statement, params=None: sql.append(statement) or ""
    saved = sys.modules.get("konsol.schema_apply")
    sys.modules["konsol.schema_apply"] = schema_apply
    try:
        module.ensure_reference_tables()
    finally:
        if saved is None:
            sys.modules.pop("konsol.schema_apply", None)
        else:
            sys.modules["konsol.schema_apply"] = saved
    return sql


def test_the_budget_columns_are_synced_once_the_tables_exist():
    """reconcile_all calls ensure_reference_tables before it rewrites Budget
    Annual Input. On a fresh site that is where the two tables are created, so
    the declared columns must be added there, after the CREATEs and before any
    write names them."""
    seen = []
    _ensure_reference_tables(seen.append)
    assert len(seen) == 1, "synced exactly once"
    created_before = " ".join(seen[0])
    for table in BUDGET_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {table} " in created_before, table


def test_a_failing_budget_column_sync_does_not_fail_the_bootstrap():
    def boom(_sql):
        raise RuntimeError("clickhouse down")

    sql = _ensure_reference_tables(boom)
    assert any("epm_raw" in s for s in sql), "the raw tables still bootstrap after it"


# --------------------------------------------------------------------------
# The patch that retires the two fixed columns, per site
# --------------------------------------------------------------------------
PATCH = os.path.join(APP_DIR, "patches", "retire_budget_annual_input_fixed_dimensions.py")


def _run_patch(columns, declared=(), fail=()):
    """Run the patch on a table with ``columns`` ({name: rows holding a value}),
    a site declaring ``declared`` in_budget, and DDL failing for ``fail``.
    Returns (columns left, DDL run, warnings)."""
    ddl, warnings = [], []

    def has_column(doctype, column):
        assert doctype == "Budget Annual Input", doctype
        return column in columns

    def exists(doctype, filters):
        assert doctype == "Dimension" and filters["in_budget"] == 1, filters
        assert filters["status"] == "Published", filters
        return filters["dimension_name"] in declared

    def sql(query, *a, **k):
        column = query.split("`")[3]
        return [[columns[column]]]

    def sql_ddl(query):
        column = query.split("`")[3]
        if column in fail:
            raise RuntimeError("ddl refused")
        ddl.append(query)
        columns.pop(column)

    fake = types.ModuleType("frappe")
    fake.db = types.SimpleNamespace(has_column=has_column, exists=exists, sql=sql, sql_ddl=sql_ddl)
    fake.logger = lambda: types.SimpleNamespace(
        warning=lambda msg, **k: warnings.append(msg), info=lambda *a, **k: None)
    module = _load_isolated(PATCH, "_host_retire_bai_dims_k287", {"frappe": fake})
    module.execute()
    return columns, ddl, warnings


def test_the_patch_is_registered():
    with open(os.path.join(APP_DIR, "patches.txt"), encoding="utf-8") as fh:
        assert "konsol.patches.retire_budget_annual_input_fixed_dimensions" in fh.read().split()


def test_an_empty_retired_column_is_dropped():
    left, ddl, warnings = _run_patch({"dim_cost_center": 0, "dim_department": 0})
    assert left == {} and len(ddl) == 2 and not warnings, (left, ddl, warnings)


def test_a_retired_column_the_site_declares_is_kept_for_its_custom_field():
    """The Custom Field sync creates the field of the same name over it, so the
    values are read again unchanged."""
    left, ddl, warnings = _run_patch({"dim_cost_center": 5, "dim_department": 0},
                                     declared={"dim_cost_center"})
    assert "dim_cost_center" in left and not warnings, (left, warnings)
    assert [q for q in ddl if "dim_cost_center" in q] == []


def test_a_retired_column_holding_values_is_kept_and_reported():
    """Never destroyed: the values reach no number (dbt selected only declared
    dimensions), and declaring the dimension brings them back."""
    left, ddl, warnings = _run_patch({"dim_cost_center": 3, "dim_department": 0})
    assert "dim_cost_center" in left, left
    assert "dim_department" not in left, "the empty one is still dropped"
    assert len(warnings) == 1 and "3 row(s)" in warnings[0], warnings


def test_the_patch_is_a_no_op_when_the_columns_are_gone():
    left, ddl, warnings = _run_patch({})
    assert (left, ddl, warnings) == ({}, [], [])


def test_a_failing_drop_is_logged_and_the_other_column_still_retires():
    left, ddl, warnings = _run_patch({"dim_cost_center": 0, "dim_department": 0},
                                     fail={"dim_cost_center"})
    assert "dim_cost_center" in left and "dim_department" not in left, left
    assert any("left in place" in w for w in warnings), warnings
