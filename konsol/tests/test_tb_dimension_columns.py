"""The raw trial-balance table carries the declared dimension columns (konsol#255).

`epm_raw.trial_balance_submissions` is created by static DDL that runs once
against an empty volume, so it cannot know which dimensions a customer
declares. Which `dim_*` columns belong on it comes from the Published
`Dimension` records with `in_trial_balance = 1`, and that set changes after
the table exists — so a declared column missing from the table is ADDED by
ALTER.

**Never dropped.** Until 22 September 2026 this sync also removed the columns
that were no longer declared, mirroring
`_sync_budget_custom_fields_locked()`. A Custom Field is metadata; a column on
`epm_raw` holds every value the customer ever uploaded, and the whole
bronze→gold chain rebuilds from it. One Unpublish click — or a rename, a
delete, an untick, or ANY unrelated Measure/Dataset publish, all of which
reach the shared `apply_and_rebuild` path — therefore destroyed the data
permanently, and re-publishing brought the column back empty. Deepak Pai chose
option A, never drop:
https://github.com/grynn-in/konsol/issues/255#issuecomment-5782540260

So the tests below that once asserted a DROP now assert its absence. Orphan
`dim_*` columns accumulating is the accepted consequence: they are
`String DEFAULT ''` and they hold history that is otherwise unrecoverable.
Un-declaring stops new values arriving; values already accepted stay.

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


def _columns_after(stack, columns):
    """The table's columns once the emitted DDL has been replayed onto them.

    The fake ClickHouse only records statements, so "the column is still
    there" has to be applied rather than assumed: ADD appends, DROP removes.
    A test asserting survival then fails if a DROP is ever reintroduced.
    """
    surviving = list(columns)
    for statement in stack.ddl:
        words = statement.split()
        if "ADD" in words:
            name = words[words.index("EXISTS") + 1]
            if name not in surviving:
                surviving.append(name)
        elif "DROP" in words:
            name = words[words.index("EXISTS") + 1]
            surviving = [c for c in surviving if c != name]
    return surviving


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


# --- add, and never drop ---------------------------------------------------

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


def test_an_undeclared_dim_column_on_the_table_is_never_dropped():
    # It holds uploaded values. Un-declaring stops new ones arriving; it must
    # not touch the ones already accepted, so there is no DDL at all.
    stack, actions = _sync([], LIVE_COLUMNS + ["dim_retired"])
    assert stack.ddl == []
    assert actions == []
    assert "DROP COLUMN" not in " ".join(stack.sql)


def test_unpublishing_the_last_dimension_leaves_its_column_on_the_table():
    # The full Unpublish shape: Dimension.unpublish() sets status =
    # "Inactive", so the declared set goes {dim_cost_center} -> {} while
    # dim_cost_center is on the table. That one click used to run
    # ALTER TABLE ... DROP COLUMN dim_cost_center and destroy every uploaded
    # value in it — epm_raw is what the whole bronze→gold chain rebuilds from.
    columns = LIVE_COLUMNS + ["dim_cost_center"]
    before, _ = _sync([_dim("dim_cost_center")], columns)
    assert before.ddl == []          # published and present: nothing to do

    stack, actions = _sync([], columns)     # unpublished
    assert stack.ddl == [], stack.ddl
    assert actions == []
    assert "dim_cost_center" in _columns_after(stack, columns)


def test_a_rename_adds_the_new_column_and_leaves_the_old_one():
    # autoname is field:dimension_name, so a rename is a new name to Frappe:
    # declared {dim_new} while dim_old is on the table. Only the ADD may run —
    # dim_old still holds every value uploaded under the old name.
    columns = LIVE_COLUMNS + ["dim_old"]
    stack, actions = _sync([_dim("dim_new")], columns)
    assert stack.ddl == [
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "dim_new String DEFAULT ''"
    ]
    assert actions == ["added dim_new"]
    assert "dim_old" in _columns_after(stack, columns)


def test_no_sync_ever_reports_a_removal():
    # The verb is gone from the vocabulary, whatever the declared set and
    # whatever is on the table. A caller logging these can no longer print a
    # removal that did not happen.
    cases = [
        ((), LIVE_COLUMNS),
        ((), LIVE_COLUMNS + ["dim_retired"]),
        ([_dim("dim_new")], LIVE_COLUMNS + ["dim_old"]),
        ([_dim("dim_a"), _dim("dim_b")], LIVE_COLUMNS + ["dim_c", "dim_d"]),
        ([_dim("nodim")], LIVE_COLUMNS + ["dim_retired"]),
    ]
    for declared, columns in cases:
        stack, actions = _sync(declared, columns)
        assert not [a for a in actions if a.startswith("removed")], (
            declared, columns, actions)
        assert "DROP COLUMN" not in " ".join(stack.sql), (declared, columns)


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

def test_a_real_column_is_never_touched():
    # Nothing on the table but the one declared ADD, and the prefix guard that
    # makes that true is asserted here, not assumed.
    #
    # This test used to run on declared=[dim_project], columns=LIVE_COLUMNS,
    # where `existing` is empty — the set it iterated had nothing in it, so it
    # passed both with the drop path reinstated and with the
    # startswith(_TB_DIM_PREFIX) guard deleted. The fixture now carries the
    # three shapes that make the guard observable:
    #   dim_retired    — an undeclared dim_* column: a reinstated drop names it
    #   dimension_code — a lookalike: it is NOT dim_*, so a loosened prefix
    #                    ("dim" instead of "dim_") would refuse and log it
    #   DIM_UPPER      — the same, in the other direction
    # and every real column is checked for a refusal and a log entry, not only
    # for a DROP: with the prefix guard gone, batch_id and main_account are
    # validated as dimension names, fail, and are refused into the caller's
    # log. That is the defect this test names.
    columns = LIVE_COLUMNS + ["dim_retired", "dimension_code", "DIM_UPPER"]
    stack, actions = _sync([_dim("dim_project")], columns)

    # The complete DDL, so a drop of anything cannot hide beside the add.
    assert stack.ddl == [
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "dim_project String DEFAULT ''"
    ], stack.ddl
    assert actions == ["added dim_project"], actions
    assert stack.logged == [], stack.logged

    joined = " ".join(stack.sql)
    for column in LIVE_COLUMNS + ["dimension_code", "DIM_UPPER"]:
        assert f"DROP COLUMN IF EXISTS {column}" not in joined, column
        assert not [a for a in actions if column in a], (column, actions)
        assert not [m for m in stack.logged if column in str(m)], column
        assert column in _columns_after(stack, columns), column
    assert "dim_retired" in _columns_after(stack, columns)


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
    # Whitespace. Python's `$` matches before a trailing newline, so
    # `re.match(r"^dim_[a-z0-9_]+$", "dim_x\n")` is True and the validator
    # used to wave "dim_x\n" straight into DDL. The UI will not let anyone
    # type a dimension_name like this; a patch, a fixture, the REST API and a
    # data import all can (konsol#255).
    "dim_x\n",
    "dim_x\r\n",
    "dim_x ",
    " dim_x",
    "dim_x\t",
    "dim_x\nDROP TABLE y",
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


#: Names a customer may legitimately give a dimension. Lower snake case with
#: digits allowed: dim_entity2 and dim_fy2024 are ordinary names, and a sync
#: that refuses one silently keeps that dimension out of the trial balance.
GOOD_NAMES = ["dim_project", "dim_cost_centre", "dim_a1", "dim_entity2",
              "dim_fy2024", "dim_a_b_c"]


def test_every_lower_snake_case_dim_name_is_accepted_by_the_sync():
    # Through the SYNC, not the constant. This test used to assert
    # _SAFE_TB_DIM_COLUMN's own behaviour — a restatement of the pattern that
    # could not fail for any change to how the sync USES it. Making the sync
    # stricter than the constant (refusing every name carrying a digit) left
    # the whole file green, so dim_fy2024 could be silently refused with
    # nothing to show for it.
    #
    # The negative direction is covered by
    # test_a_malformed_declared_name_never_reaches_sql over BAD_NAMES, also
    # through the sync; this is the positive half it lacked. `dim__x`, which
    # the old constant test blessed and nothing else in the system agrees is a
    # name, is deliberately not here.
    for name in GOOD_NAMES:
        stack, actions = _sync([_dim(name)], LIVE_COLUMNS)
        assert actions == [f"added {name}"], (name, actions)
        assert stack.ddl == [
            f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
            f"{name} String DEFAULT ''"
        ], (name, stack.ddl)
        assert stack.logged == [], (name, stack.logged)
        # And already on the table it is recognised, not re-added: the same
        # name has to pass validation on the table side too.
        again, again_actions = _sync([_dim(name)], LIVE_COLUMNS + [name])
        assert again.ddl == [], (name, again.ddl)
        assert again_actions == [], (name, again_actions)


def test_the_validator_is_end_anchored_so_a_match_caller_cannot_reopen_it():
    # The one claim about _SAFE_TB_DIM_COLUMN that no behaviour test can
    # reach, kept when the rest of the old constant test went. Both call sites
    # use .fullmatch, which makes \Z redundant TODAY — so swapping \Z for `$`
    # re-arms the trailing-newline hole (konsol#255) for the next caller that
    # reaches for .match, and MEASURED, every other test in this file stays
    # green while it does: with r"^dim_[a-zA-Z0-9_]+$" the file passed 26/26.
    # This is about the pattern's anchoring, not its alphabet; the alphabet is
    # asserted through the sync, above and in BAD_NAMES.
    pattern = _load().module._SAFE_TB_DIM_COLUMN
    for name in BAD_NAMES:
        assert not pattern.match(name), (
            f"{name!r} must be refused by .match too — the pattern must end "
            "with \\Z, not $")


def test_a_trailing_newline_name_never_reaches_sql():
    # The one BAD_NAMES entry that used to pass: `$` matches before a final
    # newline, so "dim_cost_center\n" was interpolated and ClickHouse got a
    # mangled column name.
    stack, actions = _sync([_dim("dim_cost_center\n")], LIVE_COLUMNS)
    assert stack.ddl == [], stack.ddl
    assert actions == ["refused dim_cost_center\n"]
    assert stack.logged


# --- the shape the caller logs --------------------------------------------
#
# test_the_return_shape_is_a_list_of_added_and_refused_strings was deleted on
# 22 September 2026. It asserted that every action's first word was one of the
# two literals the function writes three lines away, and its comment claimed
# the shape "matches _sync_budget_custom_fields_locked()" while never looking
# at that function — changing the budget sync's verb to "created" left it
# green. What it actually held is held twice over and on purpose:
#   - the exact list, for a declared name beside a refused one:
#     test_a_good_name_still_syncs_beside_a_refused_one
#   - no "removed X", over five declared/table combinations including the
#     orphan it used: test_no_sync_ever_reports_a_removal
#   - the list reaching the caller: the _apply_schema_steps tests below.
#
# --- the sync is actually reachable from Apply Schema ----------------------
# A sync nothing calls is not a feature. These assert on the CALL SITE:
# _apply_schema_steps() is the shared body of both public entry points
# (apply_schema and apply_schema_for_publish), so the sync belongs there.

def _recorded(stack, raises=None):
    """Patch the ClickHouse DDL steps to record their order of execution.

    Returns the list the calls are appended to. ``raises`` is the name of the
    step that should blow up, to check a failure is reported and not fatal.
    """
    calls = []
    module = stack.module

    def step(name, result):
        def run():
            calls.append(name)
            if raises == name:
                raise RuntimeError("boom")
            return result
        return run

    module._apply_clickhouse_columns = step(
        "_apply_clickhouse_columns", ["epm.f.dim_x"])
    module._sync_tb_dimension_columns = step(
        "_sync_tb_dimension_columns", ["added dim_project"])
    real_facts = module._apply_fact_tables

    def facts():
        calls.append("_apply_fact_tables")
        return real_facts()

    module._apply_fact_tables = facts
    return calls


def test_apply_schema_steps_calls_the_dimension_column_sync():
    stack = _load()
    calls = _recorded(stack)
    summary = stack.module._apply_schema_steps()
    assert "_sync_tb_dimension_columns" in calls, calls
    assert summary["tb_dimension_columns_synced"] == ["added dim_project"]


def test_the_dimension_column_sync_runs_after_the_clickhouse_ddl():
    # Ordering is the point, not co-occurrence: _apply_clickhouse_columns is
    # what guarantees the raw table exists before columns are altered onto it.
    stack = _load()
    calls = _recorded(stack)
    stack.module._apply_schema_steps()
    assert calls.index("_sync_tb_dimension_columns") > calls.index(
        "_apply_clickhouse_columns"), calls


def test_a_failing_dimension_column_sync_is_reported_not_fatal():
    # One failing step must not abort Apply Schema: the error is carried in
    # the summary and the remaining steps still run.
    stack = _load()
    calls = _recorded(stack, raises="_sync_tb_dimension_columns")
    summary = stack.module._apply_schema_steps()
    assert "TB dimension columns: boom" in summary["errors"], summary["errors"]
    assert "_apply_fact_tables" in calls, calls
    assert summary["vars_updated"] is True
    assert summary["columns_added"] == ["epm.f.dim_x"]
    assert summary["tb_dimension_columns_synced"] == []
    assert stack.logged, "a failing step must be logged"


def test_the_key_is_an_empty_list_when_there_is_nothing_to_sync():
    # Unpatched, with nothing declared and no dim_* columns on the table: the
    # key is present and empty, never missing.
    stack = _load(declared=(), columns=LIVE_COLUMNS)
    summary = stack.module._apply_schema_steps()
    assert summary["tb_dimension_columns_synced"] == []
    assert summary["errors"] == [], summary["errors"]


def test_a_real_sync_result_reaches_the_summary_through_apply_schema_steps():
    # End to end through the step body: a declared dimension missing from the
    # table is added, and the action shows up under the new key.
    stack = _load(declared=[_dim("dim_project")], columns=LIVE_COLUMNS)
    summary = stack.module._apply_schema_steps()
    assert summary["tb_dimension_columns_synced"] == ["added dim_project"]
    assert stack.ddl == [
        f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS "
        "dim_project String DEFAULT ''"
    ]


def test_the_standalone_apply_schema_carries_the_key():
    # Both public entry points go through _apply_schema_steps, so both sync.
    stack = _load(declared=[_dim("dim_project")], columns=LIVE_COLUMNS)
    module = stack.module
    module._check_schema_role = lambda: None
    module._switch_to_administrator = lambda: (lambda: None)
    module._sync_budget_custom_fields = lambda *a, **k: []
    summary = module.apply_schema()
    assert summary["tb_dimension_columns_synced"] == ["added dim_project"]


def test_the_publish_path_carries_the_key():
    stack = _load(declared=[_dim("dim_project")], columns=LIVE_COLUMNS)
    module = stack.module
    module._check_schema_role = lambda: None
    module.queue_budget_custom_field_sync = lambda: ["queued after commit"]
    summary = module.apply_schema_for_publish()
    assert summary["tb_dimension_columns_synced"] == ["added dim_project"]
