"""Apply Schema — single deliberate action to regenerate all dynamic schema.

Reads all config doctypes (Dimension, Measure, Dataset) and applies:
  1. dbt_project.yml vars regeneration
  2. ClickHouse ALTER TABLE for missing columns, then the declared dim_*
     columns added to the raw trial-balance table (added only — konsol#255)
  3. Budget Line custom field sync
  4. Optional dbt build trigger

Called deliberately, and by the publish path (apply_and_rebuild). Since
konsol#295 that path also runs on a Dimension save that moves it into or out
of Published, or edits a field read here while Published; any other save
does not trigger it.
"""
import json
import re

import frappe

from konsol.clickhouse import execute as ch_execute, get_connection
from konsol.dbt_config import regenerate_vars

_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_SAFE_TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

# The raw trial-balance landing table, and the only column names
# _sync_tb_dimension_columns may put into its DDL: _SAFE_IDENTIFIER's shape
# plus a mandatory dim_ prefix.
#
# \Z, not $: `$` also matches just before a trailing newline, so
# re.match(r"^dim_[a-z0-9_]+$", "dim_x\n") is True and a dimension_name
# carrying a newline — the UI will not accept one, a patch, a fixture, the
# REST API and a data import all will — was interpolated into DDL as-is
# (konsol#255). Every call site uses .fullmatch as well, so neither alone is
# load-bearing. _SAFE_IDENTIFIER and _SAFE_TABLE_NAME above have the same
# `$`; that is pre-existing and filed separately.
_TB_RAW_TABLE = "epm_raw.trial_balance_submissions"
_TB_DIM_PREFIX = "dim_"
#: The journal writes one row per line here (konsol#305 J05); option D gives
#: it a column per Published Dimension ticked in_journal (konsolidat#245).
_JOURNAL_STAGING_TABLE = "epm_staging.consolidation_adjustments"
_SAFE_TB_DIM_COLUMN = re.compile(r"^dim_[a-z0-9_]+\Z")

# ClickHouse type mapping for Cube types
_CH_TYPE_MAP = {
    "string": "String",
    "number": "Float64",
}


_BUDGET_FIELD_SYNC_JOB = "konsol.schema_apply.sync_budget_custom_fields_job"
# MariaDB named lock serialising every Budget Line Custom Field sync
# (_budget_field_sync_lock adds the database name).
_BUDGET_FIELD_SYNC_LOCK = "konsol_budget_field_sync"
_BUDGET_FIELD_SYNC_LOCK_WAIT = 30  # seconds
# The job's GET_LOCK attempts: 3 x 30 s, well inside the short queue's 300 s.
_BUDGET_FIELD_SYNC_JOB_LOCK_ATTEMPTS = 3


# POST only: a GET is rolled back at the end of the request, and the sync's
# inserts commit through updatedb while its deletes do not, so a GET kept the
# one and silently dropped the other (#135 review).
@frappe.whitelist(methods=["POST"])
def apply_schema(run_dbt=False):
    """Read all config doctypes, regenerate everything in one shot.

    The standalone action (Apply Schema, `konsol apply-schema`). It syncs the
    Budget Line Custom Fields inline, as Administrator, and that commits (see
    _sync_budget_custom_fields); nothing else is pending in its transaction.
    A publish calls apply_schema_for_publish instead.

    Args:
        run_dbt: If True, enqueue a background dbt build after schema changes.

    Returns:
        Summary dict of what was applied.

    Raises:
        frappe.PermissionError: If caller lacks EPM Admin role.
    """
    _check_schema_role()
    # Frappe passes form_dict as the kwargs, so over HTTP run_dbt is "0" or
    # "1", and a string "0" is truthy. No form_dict fallback: it turned a
    # caller's cint'd 0 back into "0" and queued a build nobody asked for.
    run_dbt = frappe.utils.cint(run_dbt)
    summary = _apply_schema_steps()

    # 4. Sync Budget Line custom fields (in_budget dimension columns), as
    # Administrator for the same reason as the publish's job (see
    # sync_budget_custom_fields_job): the role is checked above, and the sync
    # takes no input.
    restore_user = _switch_to_administrator()
    try:
        summary["budget_fields_synced"] = _sync_budget_custom_fields()
    except Exception as e:
        summary["errors"].append(f"Budget fields: {str(e)}")
        frappe.log_error("schema_apply: budget fields failed", frappe.get_traceback())
    finally:
        restore_user()

    # 5. Optional dbt build, queued as the caller
    if run_dbt:
        try:
            frappe.enqueue(
                "konsol.tasks.run_dbt_build_async",
                queue="long",
                timeout=600,
            )
            summary["dbt_triggered"] = True
        except Exception as e:
            summary["errors"].append(f"dbt trigger: {str(e)}")

    return summary


def apply_schema_for_publish():
    """apply_schema inside a publish's transaction (konsol#135).

    Steps 1-3 as apply_schema. The Budget Line Custom Field sync is queued for
    after the commit instead of run here: a Custom Field insert commits, so it
    committed the publish's save halfway and split it from the build request
    that follows. A publish that rolls back queues nothing.
    """
    _check_schema_role()
    summary = _apply_schema_steps()
    try:
        summary["budget_fields_synced"] = queue_budget_custom_field_sync()
    except Exception as e:
        # Redis unreachable, say. The publish stands; after_migrate, the next
        # publish or a manual apply_schema repairs Budget Line.
        summary["errors"].append(f"Budget fields: {str(e)}")
        frappe.log_error("schema_apply: budget field sync not queued", frappe.get_traceback())
    return summary


def queue_budget_custom_field_sync():
    """Sync Budget Line's Custom Fields in a job enqueued after the commit.

    The sync cannot run inside a transaction that has other work in it:
    CustomField.on_update calls frappe.db.updatedb, which ends with an
    unconditional commit (a DDL change can't be transactional in MariaDB).

    A job, not frappe.db.after_commit.add: the sync's deletes don't commit on
    their own, and an after-commit callback that failed halfway could only
    discard its work with frappe.db.rollback(), which also drops every
    callback queued behind it. The job has its own transaction, which the job
    runner commits, or rolls back and logs. It reads the committed
    dimensions, and the sync is a full diff, so running it twice is harmless.
    """
    # enqueue_after_commit checks Redis now (get_queue), but pushes the job
    # from after_commit.run(). If Redis drops in between, that push raises
    # after the commit: the publish stands, the request errors, and the
    # after-commit callbacks queued behind it are skipped. after_migrate or
    # the next publish repairs Budget Line.
    frappe.enqueue(_BUDGET_FIELD_SYNC_JOB, queue="short", enqueue_after_commit=True)
    return ["queued after commit"]


def sync_budget_custom_fields_job():
    """Job for queue_budget_custom_field_sync. The job runner commits."""
    # As Administrator. The job runs as the user who published, and an EPM
    # Admin may not create Custom Fields (Administrator / System Manager only)
    # or delete one Administrator created (CustomField.on_trash), which the
    # migrate-provisioned dim fields are. The publish already checked the
    # role (_check_schema_role), and the job takes no arguments: it only
    # brings Budget Line in line with the committed dimensions.
    frappe.set_user("Administrator")
    actions = _sync_budget_custom_fields(in_job=True)
    if actions:
        frappe.logger().info(f"Budget Line custom fields synced: {actions}")
    return actions


def _switch_to_administrator():
    """Become Administrator inside a request; returns the function that switches back.

    frappe.set_user mutates the request's session in place (user, and sid and
    data too) and clears form_dict, so switching back with set_user alone
    would leave the session's sid and data wrong for the rest of the request.
    """
    session = frappe.local.session
    saved = {"user": session.user, "sid": session.sid, "data": session.data}
    form_dict = frappe.local.form_dict
    frappe.set_user("Administrator")

    def restore():
        frappe.set_user(saved["user"])   # also resets the permission caches
        session.update(saved)
        frappe.local.form_dict = form_dict

    return restore


def _check_schema_role():
    allowed_roles = {"EPM Admin", "System Manager", "Administrator"}
    if not allowed_roles.intersection(set(frappe.get_roles())):
        frappe.throw("Only EPM Admin users can apply schema changes", frappe.PermissionError)


def _apply_schema_steps():
    """apply_schema's steps 1-3: dbt vars and ClickHouse DDL. No MariaDB writes
    other than error logs, so no commit."""
    summary = {
        "vars_updated": False,
        "columns_added": [],
        "tb_dimension_columns_synced": [],
        # Pre-seeded like its sibling: when step 2c raises, the key is present
        # and empty rather than missing (PR #324 review, finding 7).
        "journal_dimension_columns_synced": [],
        "budget_dimension_columns_synced": [],
        "facts_created": [],
        "sources_written": [],
        "budget_fields_synced": [],
        "dbt_triggered": False,
        "errors": [],
    }

    # 1. Regenerate dbt_project.yml vars
    try:
        regenerate_vars()
        summary["vars_updated"] = True
    except Exception as e:
        summary["errors"].append(f"dbt vars: {str(e)}")
        frappe.log_error("schema_apply: dbt vars failed", frappe.get_traceback())

    # 2. ClickHouse ALTER TABLE for missing dimension columns
    try:
        summary["columns_added"] = _apply_clickhouse_columns()
    except Exception as e:
        summary["errors"].append(f"ClickHouse DDL: {str(e)}")
        frappe.log_error("schema_apply: CH DDL failed", frappe.get_traceback())

    # 2b. Add the declared dim_* columns to the raw trial-balance table. It
    # only adds: an undeclared column holds uploaded values (konsol#255).
    # ClickHouse DDL, so it lives here with the other DDL and not with the
    # Frappe Custom Field sync. After step 2 deliberately: that step is what
    # guarantees the raw table exists before columns are altered onto it.
    try:
        summary["tb_dimension_columns_synced"] = _sync_tb_dimension_columns()
    except Exception as e:
        summary["errors"].append(f"TB dimension columns: {str(e)}")
        frappe.log_error(
            "schema_apply: TB dimension columns failed", frappe.get_traceback()
        )

    # 2c. The same for the journal's staging table (konsolidat#245 option D).
    # Its own try: a failure here must not hide the trial balance's columns or
    # stop the fact tables, and each reports under its own key.
    try:
        summary["journal_dimension_columns_synced"] = _sync_journal_dimension_columns()
    except Exception as e:
        summary["errors"].append(f"Journal dimension columns: {str(e)}")
        frappe.log_error(
            "schema_apply: journal dimension columns failed", frappe.get_traceback()
        )

    # 2d. The same for the two budget input tables (konsol#287), in its own try
    # for the reason 2c gives.
    try:
        summary["budget_dimension_columns_synced"] = _sync_budget_dimension_columns()
    except Exception as e:
        summary["errors"].append(f"Budget dimension columns: {str(e)}")
        frappe.log_error(
            "schema_apply: budget dimension columns failed", frappe.get_traceback()
        )

    # 3. Create ClickHouse tables + dbt sources for write-back facts
    try:
        created, sources = _apply_fact_tables()
        summary["facts_created"] = created
        summary["sources_written"] = sources
    except Exception as e:
        summary["errors"].append(f"Fact tables: {str(e)}")
        frappe.log_error("schema_apply: fact tables failed", frappe.get_traceback())

    return summary


def _apply_clickhouse_columns():
    """For each Published dimension, ensure column exists on all relevant fact tables.

    Returns list of columns added (as "table.column" strings).
    """
    dimensions = frappe.get_all(
        "Dimension",
        filters={"status": "Published"},
        fields=["dimension_name", "cube_type"],
        limit_page_length=0,
    )
    fact_tables = frappe.get_all(
        "Dataset",
        fields=["fact_name", "clickhouse_table", "dimensions"],
        limit_page_length=0,
    )

    added = []
    for fact in fact_tables:
        if not _SAFE_TABLE_NAME.match(fact.clickhouse_table or ""):
            frappe.log_error(
                f"schema_apply: skipping invalid table name: {fact.clickhouse_table}",
            )
            continue
        fact_dims = set(json.loads(fact.dimensions or "[]"))
        for dim in dimensions:
            if dim.dimension_name not in fact_dims:
                continue
            if not _SAFE_IDENTIFIER.match(dim.dimension_name):
                continue

            ch_type = _CH_TYPE_MAP.get(dim.cube_type or "string", "String")
            default = "''" if ch_type == "String" else "0"
            sql = (
                f"ALTER TABLE {fact.clickhouse_table} "
                f"ADD COLUMN IF NOT EXISTS {dim.dimension_name} {ch_type} "
                f"DEFAULT {default}"
            )
            try:
                ch_execute(sql)
                added.append(f"{fact.clickhouse_table}.{dim.dimension_name}")
            except Exception as e:
                # Log but don't fail the whole operation
                frappe.log_error(
                    f"schema_apply: ALTER TABLE failed for "
                    f"{fact.clickhouse_table}.{dim.dimension_name}",
                    str(e),
                )

    return added


def _table_columns(table):
    """Every column ClickHouse reports on ``table``.

    The tables are created by static DDL (clickhouse._RAW_TABLE_DDL and
    _REFERENCE_TABLE_DDL, and init-db.sql) which runs once against an empty
    volume and cannot know a customer's dimensions, so what is actually on a
    table has to be read back rather than assumed.
    """
    database, _, name = table.partition(".")
    text = ch_execute(
        "SELECT name FROM system.columns "
        f"WHERE database = '{database}' AND table = '{name}'"
    )
    return [line.strip() for line in (text or "").splitlines() if line.strip()]


def _refuse_tb_dim_column(name, where):
    """Log a dimension column name that may not be interpolated into DDL."""
    frappe.log_error(
        "schema_apply: refused a dimension column name",
        f"{name!r} ({where}) is not {_SAFE_TB_DIM_COLUMN.pattern}; "
        "it was never put into SQL.",
    )
    return f"refused {name}"


def _sync_tb_dimension_columns():
    """Add the declared dimension columns to the raw trial-balance table.

    Which dimension columns `epm_raw.trial_balance_submissions` carries is
    per-customer: it is the Published Dimension records with
    in_trial_balance = 1. That set changes after the table exists, so a
    declared column missing from the table is added by ALTER. Idempotent: a
    second run finds nothing to add and emits no DDL.

    **Nothing is ever dropped.** Until 22 September 2026 this also removed the
    columns no longer declared, mirroring _sync_budget_custom_fields_locked().
    A Custom Field is metadata and can be re-created; a column on `epm_raw`
    holds every value the customer uploaded into it, and the whole
    bronze→gold chain rebuilds from that table — so the drop destroyed those
    values permanently, and re-publishing brought the column back empty.
    Dimension.unpublish() reached it (status = "Inactive" ->
    apply_and_rebuild -> apply_schema_for_publish), and so did a rename
    (autoname is field:dimension_name), a delete, unticking in_trial_balance
    and any unrelated Measure or Dataset publish, because apply_and_rebuild is
    the shared publish path. One click, data gone.

    Deepak Pai chose option A, never drop:
    https://github.com/grynn-in/konsol/issues/255#issuecomment-5782540260

    Orphan dim_* columns accumulating is the accepted consequence: they are
    String DEFAULT '' and they hold history that is otherwise unrecoverable.
    Un-declaring stops new values arriving — nothing writes a column the
    declared set no longer names — while the values already accepted stay
    readable. Reclaiming an orphan deliberately (option B: a retire flag, an
    explicit drop with its own confirmation) was considered and deferred; do
    not reintroduce a cleanup, a TTL or a nagging warning here.

    Declared names are still validated against _SAFE_TB_DIM_COLUMN before they
    reach SQL, because they are interpolated; a name that fails is refused and
    logged, never interpolated. Columns read back off the table are validated
    too and simply not counted as present — with no DROP, the worst an
    unparseable one can now cause is a redundant ADD COLUMN IF NOT EXISTS.

    String DEFAULT '': a dimension is optional per row, so blank is legal.

    Returns:
        List of "added <col>" / "refused <name>" strings for the caller to
        log. Never "removed <col>": this function removes nothing.
    """
    return _sync_dimension_columns(_TB_RAW_TABLE, "in_trial_balance")


def _sync_journal_dimension_columns():
    """Add the declared dimension columns to the journal's staging table.

    konsolidat#245 option D (Deepak Pai, 3 Oct 2026): a Consolidation Journal
    line declares its dimension values, so `epm_staging.consolidation_adjustments`
    — the table the journal writes, one row per line (konsol#305 J05) — needs a
    column per Published Dimension ticked in_journal.

    Nothing is dropped here either. Unlike `epm_raw`, this table is re-derivable
    from the Frappe documents (the journal is the source of truth and resync
    rewrites it), so a drop would be recoverable rather than fatal. It still
    does not drop: one mechanism, one behaviour, and an orphan column is
    `String DEFAULT ''` that costs nothing and keeps history readable for a
    dimension someone un-ticked by mistake.
    """
    return _sync_dimension_columns(_JOURNAL_STAGING_TABLE, "in_journal")


#: The two budget input tables, written by Budget Annual Input and Budget
#: Sheet and read by gold_spread_budget through get_budget_dimensions().
_BUDGET_INPUT_TABLES = ("epm_gold.budget_annual_input", "epm_gold.budget_monthly_input")


def _sync_budget_dimension_columns():
    """Add the declared budget dimension columns to both budget input tables.

    konsol#287: the tables are created naming no dimension, and the Published
    Dimensions ticked in_budget decide their dim_* columns, as in_trial_balance
    does for the raw trial balance. Both tables get the same set: a budget
    entered at annual grain is spread to monthly, so a dimension only one of
    them has would be lost in the spread. Nothing is dropped, for the reason on
    _sync_tb_dimension_columns. Each action names its table.
    """
    return [f"{table}: {action}"
            for table in _BUDGET_INPUT_TABLES
            for action in _sync_dimension_columns(table, "in_budget")]


def _sync_dimension_columns(table, flag):
    """One body for every per-site dimension column set (konsolidat#245).

    ``table`` is the ClickHouse table, ``flag`` the Dimension Check that
    declares membership. Adds what is declared and missing; **never drops**.
    Names are validated against _SAFE_TB_DIM_COLUMN before they reach SQL,
    because they are interpolated; one that fails is refused and logged, never
    put into a statement. Idempotent.

    The reasoning for never dropping is on _sync_tb_dimension_columns and on
    konsol#255; do not add a cleanup here.
    """
    # ``table`` is interpolated into DDL, so it is checked like every other
    # interpolated name in this module (_apply_fact_tables does the same for a
    # Dataset's clickhouse_table). Both callers pass a module constant today;
    # this refuses the day one does not, rather than relying on that.
    if not _SAFE_TABLE_NAME.fullmatch(table or ""):
        frappe.log_error(
            "schema_apply: refused a dimension-column table name",
            f"{table!r} is not {_SAFE_TABLE_NAME.pattern}; no DDL was run.",
        )
        return [f"refused table {table}"]
    declared = frappe.get_all(
        "Dimension",
        filters={flag: 1, "status": "Published"},
        fields=["dimension_name"],
        limit_page_length=0,
    )

    actions = []
    wanted = set()
    for dim in declared:
        name = dim.dimension_name or ""
        if not _SAFE_TB_DIM_COLUMN.fullmatch(name):
            actions.append(_refuse_tb_dim_column(name, "declared on Dimension"))
            continue
        wanted.add(name)

    existing = set()
    for column in _table_columns(table):
        if not column.startswith(_TB_DIM_PREFIX):
            continue  # a real column: not a dimension column
        if not _SAFE_TB_DIM_COLUMN.fullmatch(column):
            actions.append(_refuse_tb_dim_column(column, f"on {table}"))
            continue
        existing.add(column)

    for name in sorted(wanted - existing):
        ch_execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            f"{name} String DEFAULT ''"
        )
        actions.append(f"added {name}")

    # No second loop over `existing - wanted`: those columns still hold what
    # was written into them (konsol#255, Deepak Pai's option A).
    return actions


def _apply_fact_tables():
    """Create ClickHouse table + dbt source for each Published, generates_source fact.

    Derived gold facts (generates_source=0) point at dbt-built tables and are
    skipped — only write-back facts (statistical / sub-ledger) are materialised
    here. Idempotent: CREATE TABLE IF NOT EXISTS + source upsert by name.

    Returns (facts_created, sources_written) — lists of table / source names.
    """
    facts = frappe.get_all(
        "Dataset",
        filters={"status": "Published", "generates_source": 1},
        fields=["fact_name", "label", "clickhouse_table", "dbt_model", "measures",
                "dimensions", "extra_columns"],
        limit_page_length=0,
    )
    if not facts:
        return [], []

    dim_types = {
        d.dimension_name: (d.cube_type or "string")
        for d in frappe.get_all(
            "Dimension", fields=["dimension_name", "cube_type"], limit_page_length=0
        )
    }

    created = []
    sources = []
    for fact in facts:
        if not _SAFE_TABLE_NAME.match(fact.clickhouse_table or ""):
            frappe.log_error(
                f"schema_apply: skipping invalid fact table name: {fact.clickhouse_table}",
            )
            continue

        cols = []
        for dim in json.loads(fact.dimensions or "[]"):
            if _SAFE_IDENTIFIER.match(dim):
                ch_type = _CH_TYPE_MAP.get(dim_types.get(dim, "string"), "String")
                cols.append(f"{dim} {ch_type}")
        for measure in json.loads(fact.measures or "[]"):
            if _SAFE_IDENTIFIER.match(measure):
                cols.append(f"{measure} Float64")
        for col in json.loads(fact.extra_columns or "[]"):
            name = col.get("name", "")
            if _SAFE_IDENTIFIER.match(name):
                ch_type = _CH_TYPE_MAP.get(col.get("ch_type", ""), col.get("ch_type", "String"))
                cols.append(f"{name} {ch_type}")
        # Standard grain + audit columns present on every write-back fact
        cols += [
            "data_area_id String",
            "fiscal_year UInt16",
            "fiscal_period UInt8",
            "updated_at DateTime DEFAULT now()",
        ]

        ddl = (
            f"CREATE TABLE IF NOT EXISTS {fact.clickhouse_table} "
            f"({', '.join(cols)}) "
            f"ENGINE = MergeTree ORDER BY (data_area_id, fiscal_year, fiscal_period)"
        )
        try:
            ch_execute(ddl)
            created.append(fact.clickhouse_table)
        except Exception as e:
            frappe.log_error(
                f"schema_apply: CREATE TABLE failed for {fact.clickhouse_table}",
                str(e),
            )
            continue

        try:
            if _upsert_dbt_source(fact):
                sources.append(fact.dbt_model or fact.fact_name)
        except Exception as e:
            frappe.log_error(
                f"schema_apply: dbt source upsert failed for {fact.fact_name}",
                str(e),
            )

    return created, sources


def _upsert_dbt_source(fact):
    """Add the fact's table to the epm_staging source in _staging__sources.yml.

    Returns True if the file was modified, False if the entry already existed or
    the dbt project / sources file is on another host (skipped, mirrors
    dbt_config.regenerate_vars).
    """
    import yaml

    settings = frappe.get_single("EPM Settings")
    base = settings.dbt_project_path or "/home/pd/open_epm/dbt_project"
    path = f"{base}/models/staging/_staging__sources.yml"

    # Table name without the schema prefix (epm_staging.fact_x -> fact_x)
    table_name = (fact.clickhouse_table or "").split(".")[-1]
    if not _SAFE_IDENTIFIER.match(table_name):
        return False

    try:
        with open(path) as f:
            doc = yaml.safe_load(f) or {}
    except FileNotFoundError:
        frappe.logger().warning(
            f"_staging__sources.yml not found at {path} — skipping dbt source upsert."
        )
        return False

    for source in doc.get("sources", []):
        if source.get("name") != "epm_staging":
            continue
        tables = source.setdefault("tables", [])
        if any(t.get("name") == table_name for t in tables):
            return False  # already present — idempotent
        tables.append({
            "name": table_name,
            "description": f"{fact.label or fact.fact_name} — write-back fact registered via Dataset",
            "loaded_at_field": "updated_at",
        })
        with open(path, "w") as f:
            yaml.dump(doc, f, default_flow_style=False, sort_keys=False,
                      allow_unicode=True)
        return True

    return False


def _sync_budget_custom_fields(in_job=False):
    """Ensure Budget Line has Custom Fields for all in_budget Published dimensions.

    The wide budget lines carry the dimension columns (account + dims + 12
    months); the dims are provisioned here as Custom Fields. Adds missing fields,
    removes orphaned ones. Returns list of field actions taken.

    Commits: once it holds the lock, when it is done, and whenever it adds a
    field (CustomField.on_update -> updatedb). So never call it inside a
    transaction with other work in it (#135). Its callers: the publish's job
    (a fresh job), apply_schema (only its steps' error logs are pending), and
    after_migrate and the reshape patch (migrate commits around them anyway).

    Serialised by a MariaDB named lock: a publish's job and an inline sync
    (apply_schema, after_migrate) could otherwise both find a field missing
    and both insert it. The lock belongs to the session, so updatedb's commit
    does not release it, and it is released only after the sync's last
    commit, so the next holder sees all of its work (a delete does not commit
    on its own).

    A sync that cannot get the lock in time: the job (in_job) tries
    _BUDGET_FIELD_SYNC_JOB_LOCK_ATTEMPTS times, then raises a plain error,
    which the job runner logs and commits. Skipping there could drop a
    publish committed after the holder read. Not RetryBackgroundJobError:
    Frappe v15's retry path ends every retried job in AttributeError and
    loses its Error Log. An inline caller logs and skips, and Budget Line
    waits for the next publish, apply_schema or migrate.

    On an error it rolls back before releasing the lock. After its first
    commit only its own work can be pending (earlier deletes, a half-inserted
    row), and a caller that catches the error and commits would otherwise
    land it outside the lock. An ALTER commits implicitly, so a field whose
    DDL already ran stays.
    """
    lock = _budget_field_sync_lock()
    got = None
    for _ in range(_BUDGET_FIELD_SYNC_JOB_LOCK_ATTEMPTS if in_job else 1):
        got = frappe.db.sql("SELECT GET_LOCK(%s, %s)", (lock, _BUDGET_FIELD_SYNC_LOCK_WAIT))
        if got and got[0][0] == 1:  # 0: timed out; NULL: error
            break
    else:
        msg = f"GET_LOCK('{lock}') returned {got!r}: another Budget Line field sync holds it."
        if in_job:
            raise TimeoutError(msg)
        frappe.log_error("schema_apply: budget field sync skipped", msg)
        return []
    try:
        # A fresh snapshot, taken after the lock: every read below, Frappe's
        # own included (CustomField.validate's get_meta, delete_doc's
        # get_doc), must see what the previous holder committed.
        frappe.db.commit()
        actions = _sync_budget_custom_fields_locked()
        # Before the release: the deletes are still pending, and the next
        # holder must see them.
        frappe.db.commit()
        return actions
    except BaseException:
        try:
            frappe.db.rollback()
        except Exception:
            frappe.logger().exception("schema_apply: rollback after a failed budget field sync failed")
        raise
    finally:
        try:
            frappe.db.sql("SELECT RELEASE_LOCK(%s)", (lock,))
        except Exception:
            # A dropped connection took the lock with it. Never hide the
            # sync's own error behind this one.
            frappe.logger().exception(f"schema_apply: RELEASE_LOCK('{lock}') failed")


def _budget_field_sync_lock():
    """The named lock, per database: GET_LOCK names are global to the server."""
    return f"{_BUDGET_FIELD_SYNC_LOCK}:{frappe.conf.db_name}"[:64]


def _created_elsewhere(cf):
    """Whether a failed insert of ``cf`` met a field committed outside this sync.

    That field fails CustomField.validate ("already exists", a
    ValidationError) or db_insert (DuplicateEntryError), and it meets the
    goal. Rolls back first: a field committed after this transaction's
    snapshot is invisible to it. That is safe here because the adds run
    before any delete and each successful add has committed (updatedb), so
    only this failed add's own work is pending.

    Ours must not pass as success: a row with our creation stamp is ours,
    failing after the insert (updatedb's DDL). In a migrate or patch Frappe
    stamps creation only in db_insert, so no stamp means ours was never
    written, and any row there is someone else's.
    """
    frappe.db.rollback()
    filters = {"dt": cf.dt, "fieldname": cf.fieldname}
    creation = getattr(cf, "creation", None)
    if creation:
        filters["creation"] = ("!=", creation)
    return bool(frappe.db.exists("Custom Field", filters))


def _sync_budget_custom_fields_locked():
    """Both dimension-carrying line tables, under the one lock already held.

    Plain reads: _sync_budget_custom_fields committed after taking the lock, so
    the snapshot is fresh. Consolidation Journal Line joined Budget Line here
    for konsolidat#245 option D — one lock rather than a second named lock,
    because both write Custom Fields and an ALTER from either commits.

    EVERY ADD RUNS BEFORE ANY DELETE, across both tables. _created_elsewhere
    rolls back to discard a failed insert's own pending work, and its docstring
    states the premise that makes that safe: "the adds run before any delete and
    each successful add has committed (updatedb), so only this failed add's own
    work is pending". Running one table fully and then the other broke it (PR
    #324 review, finding 3) — a Custom Field delete is pure DML with no
    updatedb, so a failed add on the SECOND table rolled back the FIRST table's
    uncommitted deletes while still reporting them as removed.
    """
    tables = (("Budget Line", "in_budget", "main_account"),
              # konsol#287: the annual half of the budget carries the same
              # declared dimensions instead of two fixed fields.
              ("Budget Annual Input", "in_budget", "main_account"),
              ("Consolidation Journal Line", "in_journal", "main_account"))
    plans = [_plan_dimension_custom_fields(dt, flag) for dt, flag, _ in tables]
    actions = [r for plan in plans for r in plan[3]]
    for (dt, _flag, insert_after), plan in zip(tables, plans):
        actions += _add_dimension_custom_fields(dt, insert_after, plan)
    for (dt, _flag, _after), plan in zip(tables, plans):
        actions += _remove_orphan_dimension_custom_fields(dt, plan)
    return actions


def _plan_dimension_custom_fields(dt, flag):
    """What ``dt`` should have: ``(wanted, label_map, existing)``. Reads only.

    Split from the add and remove phases so every add across every table runs
    before any delete — see _sync_budget_custom_fields_locked (PR #324 review,
    finding 3). One body for both callers, Budget Line's ``in_budget`` and
    Consolidation Journal Line's ``in_journal`` (konsolidat#245 option D),
    because two copies would drift and the drift is silent in the worst
    direction: a dimension that is declared and has nowhere to land.

    Callers hold the named lock; the reads are plain because the caller
    committed after taking it.
    """
    dims = frappe.get_all(
        "Dimension",
        filters={flag: 1, "status": "Published"},
        fields=["dimension_name", "label"],
        limit_page_length=0,
    )
    # Only names the sync can see again. ``existing`` below is queried
    # ``fieldname like "dim_%"``, so a Custom Field named outside that shape
    # could never be found as existing: every later run would try to recreate
    # it, and un-ticking the flag could never remove it. A non-dim_ name is
    # legal on a Dimension outside the trial balance, so this is reachable
    # (PR #324 re-review, finding 6). Refused and reported, not silent.
    wanted, label_map, refused = set(), {}, []
    for d in dims:
        name = d.dimension_name or ""
        if not _SAFE_TB_DIM_COLUMN.fullmatch(name):
            refused.append(_refuse_tb_dim_column(name, f"declared for {dt}"))
            continue
        wanted.add(name)
        label_map[name] = d.label
    existing = frappe.get_all(
        "Custom Field",
        filters={"dt": dt, "fieldname": ("like", "dim_%")},
        fields=["name", "fieldname"],
        limit_page_length=0,
    )
    return wanted, label_map, existing, refused


def _add_dimension_custom_fields(dt, insert_after, plan):
    """Insert the Custom Fields ``dt`` is missing. Each insert commits
    (CustomField.on_update -> frappe.db.updatedb)."""
    wanted, label_map, existing, _refused = plan
    existing_names = {cf.fieldname for cf in existing}
    actions = []
    for dim_name in sorted(wanted - existing_names):
        cf = frappe.new_doc("Custom Field")
        cf.dt = dt
        cf.fieldname = dim_name
        cf.fieldtype = "Data"
        cf.label = label_map.get(dim_name, dim_name)
        cf.insert_after = insert_after
        try:
            cf.insert()
        except Exception:
            if _created_elsewhere(cf):
                continue  # the goal is met
            raise
        actions.append(f"added {dim_name}")
    return actions


def _remove_orphan_dimension_custom_fields(dt, plan):
    """Delete ``dt``'s Custom Fields the declared set no longer names.

    The Frappe field only: the warehouse column is never dropped, so values
    already written stay readable and a dimension unticked by mistake loses no
    history (konsol#255, Deepak Pai's option A).
    """
    wanted, _label_map, existing, _refused = plan
    actions = []
    for cf in existing:
        if cf.fieldname not in wanted:
            frappe.delete_doc("Custom Field", cf.name)
            actions.append(f"removed {cf.fieldname}")
    return actions
