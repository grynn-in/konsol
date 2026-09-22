"""The raw trial-balance table carries the declared dimension columns (konsol#255).

`epm_raw.trial_balance_submissions` is created by static DDL that runs once
against an empty volume, so it cannot know which dimensions a customer
declares. Which `dim_*` columns belong on it comes from the Published
`Dimension` records with `in_trial_balance = 1`, and that set changes after
the table exists — so the columns are SYNCED by ALTER, the same shape
`_sync_budget_custom_fields_locked()` uses for Budget Line's Custom Fields:
read the declared set, add what is missing, remove what is no longer declared.

Site-free: schema_apply.py is loaded under a private name with a stub frappe,
a stub konsol.clickhouse and a stub konsol.dbt_config, so every assertion is
on the SQL that WOULD be run.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TABLE = "epm_raw.trial_balance_submissions"

#: The live table today — konsol#255. None of these may ever be dropped.
LIVE_COLUMNS = [
    "batch_id", "data_area_id", "fiscal_year", "fiscal_period", "main_account",
    "debit_amount", "credit_amount", "description", "submission_name",
    "submitted_at", "partner_data_area_id",
]


class _D(dict):
    """frappe.get_all's row: attribute access over a dict."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)


class _Stack:
    """schema_apply loaded with stubs, plus what its stubs recorded."""

    def __init__(self, module, sql, logged, queries):
        self.module = module
        self.sql = sql          # every statement handed to ClickHouse
        self.logged = logged    # (title, message) pairs from frappe.log_error
        self.queries = queries  # (doctype, kwargs) pairs from frappe.get_all

    @property
    def ddl(self):
        """The statements that change the table — what idempotence is about."""
        return [s for s in self.sql if s.lstrip().upper().startswith("ALTER")]


def _load(declared=(), columns=LIVE_COLUMNS):
    """schema_apply.py against a stub frappe and a fake ClickHouse.

    ``declared`` are the Dimension rows frappe.get_all answers with;
    ``columns`` are the columns ClickHouse reports on the raw table.
    """
    sql = []
    logged = []
    queries = []

    def get_all(doctype, **kwargs):
        queries.append((doctype, kwargs))
        if doctype == "Dimension":
            return [_D(d) for d in declared]
        return []

    def log_error(title=None, message=None, **kw):
        logged.append((title, message))

    def execute(statement, params=None):
        sql.append(statement)
        if statement.lstrip().upper().startswith("SELECT"):
            return "\n".join(columns)
        return ""

    fake_frappe = types.ModuleType("frappe")
    fake_frappe.get_all = get_all
    fake_frappe.log_error = log_error
    fake_frappe.get_traceback = lambda: ""
    fake_frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    fake_frappe.logger = lambda: types.SimpleNamespace(
        exception=lambda *a, **k: None)
    fake_utils = types.ModuleType("frappe.utils")
    fake_utils.cint = int
    fake_frappe.utils = fake_utils

    stubs = {
        "frappe": fake_frappe,
        "frappe.utils": fake_utils,
        "konsol.clickhouse": types.SimpleNamespace(
            execute=execute, get_connection=lambda: {}),
        "konsol.dbt_config": types.SimpleNamespace(
            regenerate_vars=lambda *a, **k: None),
    }
    before = set(sys.modules)
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(
            "_host_schema_apply_k255_tb_dims",
            os.path.join(APP_DIR, "schema_apply.py"))
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
    return _Stack(module, sql, logged, queries)


def _sync(declared=(), columns=LIVE_COLUMNS):
    stack = _load(declared, columns)
    actions = stack.module._sync_tb_dimension_columns()
    return stack, actions


def _dim(name):
    return {"dimension_name": name, "label": name, "status": "Published"}


# --- the declared set is the source of truth -------------------------------

def test_declared_set_is_published_dimensions_flagged_in_trial_balance():
    # Not every Dimension: only the Published ones a customer declared for the
    # trial balance. A draft dimension must not reach the landing table.
    stack, _ = _sync([_dim("dim_cost_centre")], LIVE_COLUMNS)
    dimension_queries = [kw for dt, kw in stack.queries if dt == "Dimension"]
    assert len(dimension_queries) == 1, dimension_queries
    assert dimension_queries[0]["filters"] == {
        "in_trial_balance": 1, "status": "Published"}
    assert "dimension_name" in dimension_queries[0]["fields"]


# --- add / drop ------------------------------------------------------------

def test_declared_dimension_missing_from_the_table_is_added():
    stack, actions = _sync([_dim("dim_cost_centre")], LIVE_COLUMNS)
    assert stack.ddl == [
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "dim_cost_centre String DEFAULT ''"
    ]
    assert actions == ["added dim_cost_centre"]


def test_the_added_column_is_a_blank_defaulting_string():
    # A dimension is optional per row, so blank is legal: no NOT NULL, no
    # sentinel. Anything else would reject a trial balance that leaves it out.
    stack, _ = _sync([_dim("dim_project")], LIVE_COLUMNS)
    assert "String DEFAULT ''" in stack.ddl[0]


def test_an_undeclared_dim_column_on_the_table_is_dropped():
    stack, actions = _sync([], LIVE_COLUMNS + ["dim_retired"])
    assert stack.ddl == [
        f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS dim_retired"]
    assert actions == ["removed dim_retired"]


def test_an_add_and_a_drop_at_once():
    stack, actions = _sync([_dim("dim_project")], LIVE_COLUMNS + ["dim_retired"])
    assert stack.ddl == [
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "dim_project String DEFAULT ''",
        f"ALTER TABLE {TABLE} DROP COLUMN IF EXISTS dim_retired",
    ]
    assert actions == ["added dim_project", "removed dim_retired"]


def test_a_declared_column_already_on_the_table_is_left_alone():
    stack, actions = _sync(
        [_dim("dim_project")], LIVE_COLUMNS + ["dim_project"])
    assert stack.ddl == []
    assert actions == []


def test_nothing_declared_and_no_dim_columns_runs_no_ddl():
    stack, actions = _sync([], LIVE_COLUMNS)
    assert stack.ddl == []
    assert actions == []


def test_running_it_twice_is_idempotent():
    # The second run sees the table the first run produced and must do nothing.
    declared = [_dim("dim_project")]
    first, first_actions = _sync(declared, LIVE_COLUMNS)
    assert first_actions == ["added dim_project"]
    second, second_actions = _sync(declared, LIVE_COLUMNS + ["dim_project"])
    assert second.ddl == []
    assert second_actions == []


# --- only ever dim_* -------------------------------------------------------

def test_a_real_column_is_never_dropped():
    # The drop path may not reach batch_id, main_account, debit_amount or any
    # other column the submission writes — whatever is declared.
    stack, actions = _sync([_dim("dim_project")], LIVE_COLUMNS)
    for column in LIVE_COLUMNS:
        assert f"DROP COLUMN IF EXISTS {column}" not in " ".join(stack.sql)
    assert not [a for a in actions if a.startswith("removed ")]


def test_no_column_is_dropped_when_nothing_is_declared():
    # The empty declared set is the dangerous one: a naive "drop everything
    # not declared" would empty the table's schema.
    stack, actions = _sync([], LIVE_COLUMNS)
    assert stack.ddl == []
    assert actions == []


def test_a_lookalike_column_is_not_a_dim_column():
    # "dimension_code" and "DIM_UPPER" are not dim_* columns: neither is
    # dropped, whatever the declared set says.
    stack, _ = _sync([], LIVE_COLUMNS + ["dimension_code", "DIM_UPPER"])
    joined = " ".join(stack.sql)
    assert "dimension_code" not in joined
    assert "DIM_UPPER" not in joined


# --- every name is validated before it reaches SQL -------------------------

BAD_NAMES = [
    "dim_x; DROP TABLE y",
    "DIM_UPPER",
    "nodim",
    "dim_",
    "dim_x'",
    "dim_x--",
    "dim x",
    "",
]


def test_a_malformed_declared_name_never_reaches_sql():
    for name in BAD_NAMES:
        stack, actions = _sync([_dim(name)], LIVE_COLUMNS)
        assert stack.ddl == [], f"{name!r} produced DDL: {stack.ddl}"
        assert actions == [f"refused {name}"], (name, actions)


def test_a_refused_declared_name_is_logged_not_swallowed():
    # No silent fallback: a name that is refused is visible.
    stack, actions = _sync([_dim("dim_x; DROP TABLE y")], LIVE_COLUMNS)
    assert actions == ["refused dim_x; DROP TABLE y"]
    assert stack.logged, "a refused dimension name must be logged"


def test_a_malformed_dim_column_on_the_table_is_never_dropped():
    # The table side is interpolated too, so it is validated too.
    stack, actions = _sync([], LIVE_COLUMNS + ["dim_x; DROP TABLE y"])
    assert stack.ddl == []
    assert actions == ["refused dim_x; DROP TABLE y"]
    assert stack.logged


def test_a_good_name_still_syncs_beside_a_refused_one():
    # One bad declaration must not stop the rest of the sync.
    stack, actions = _sync(
        [_dim("nodim"), _dim("dim_project")], LIVE_COLUMNS)
    assert stack.ddl == [
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "dim_project String DEFAULT ''"
    ]
    assert sorted(actions) == ["added dim_project", "refused nodim"]


def test_the_validator_requires_the_dim_prefix_and_lower_snake_case():
    module = _load().module
    pattern = module._SAFE_TB_DIM_COLUMN
    for name in BAD_NAMES:
        assert not pattern.match(name), f"{name!r} must be refused"
    for name in ("dim_project", "dim_cost_centre", "dim_a1", "dim__x"):
        assert pattern.match(name), f"{name!r} must be accepted"


# --- the shape the caller logs --------------------------------------------

def test_the_return_shape_matches_the_budget_field_sync():
    # _sync_budget_custom_fields_locked() returns a list of "added X" /
    # "removed X" strings; the caller logs them the same way.
    stack, actions = _sync([_dim("dim_project")], LIVE_COLUMNS + ["dim_retired"])
    assert isinstance(actions, list)
    assert all(isinstance(a, str) for a in actions)
    assert all(a.split(" ", 1)[0] in ("added", "removed", "refused")
               for a in actions), actions
