"""Apply Schema — single deliberate action to regenerate all dynamic schema.

Reads all config doctypes (Dimension, Measure, Dataset) and applies:
  1. dbt_project.yml vars regeneration
  2. ClickHouse ALTER TABLE for missing columns
  3. Budget Line custom field sync
  4. Optional dbt build trigger

Called deliberately — NOT triggered automatically on individual saves.
"""
import json
import re

import frappe

from konsol.clickhouse import execute as ch_execute, get_connection
from konsol.dbt_config import regenerate_vars

_SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]*$")
_SAFE_TABLE_NAME = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

# ClickHouse type mapping for Cube types
_CH_TYPE_MAP = {
    "string": "String",
    "number": "Float64",
}


_BUDGET_FIELD_SYNC_JOB = "konsol.schema_apply.sync_budget_custom_fields_job"
# MariaDB named lock serialising every Budget Line Custom Field sync.
_BUDGET_FIELD_SYNC_LOCK = "konsol_budget_field_sync"
_BUDGET_FIELD_SYNC_LOCK_WAIT = 30  # seconds


# POST only: a GET is rolled back at the end of the request, and the sync's
# inserts commit through updatedb while its deletes do not, so a GET kept the
# one and silently dropped the other (#135 review).
@frappe.whitelist(methods=["POST"])
def apply_schema(run_dbt=False):
    """Read all config doctypes, regenerate everything in one shot.

    The standalone action (Apply Schema, `konsol apply-schema`). It syncs the
    Budget Line Custom Fields inline, and that commits (see
    queue_budget_custom_field_sync); nothing else is pending in its
    transaction. A publish calls apply_schema_for_publish instead.

    Args:
        run_dbt: If True, enqueue a background dbt build after schema changes.

    Returns:
        Summary dict of what was applied.

    Raises:
        frappe.PermissionError: If caller lacks EPM Admin role.
    """
    _check_schema_role()
    summary = _apply_schema_steps()

    # 4. Sync Budget Line custom fields (in_budget dimension columns)
    try:
        summary["budget_fields_synced"] = _sync_budget_custom_fields()
    except Exception as e:
        summary["errors"].append(f"Budget fields: {str(e)}")
        frappe.log_error("schema_apply: budget fields failed", frappe.get_traceback())

    # 5. Optional dbt build
    if run_dbt or frappe.form_dict.get("run_dbt"):
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
    actions = _sync_budget_custom_fields()
    if actions:
        frappe.logger().info(f"Budget Line custom fields synced: {actions}")
    return actions


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


def _sync_budget_custom_fields():
    """Ensure Budget Line has Custom Fields for all in_budget Published dimensions.

    The wide budget lines carry the dimension columns (account + dims + 12
    months); the dims are provisioned here as Custom Fields. Adds missing fields,
    removes orphaned ones. Returns list of field actions taken.

    Commits whenever it adds a field (CustomField.on_update -> updatedb), so
    never call it inside a transaction with other work in it (#135).

    Serialised by a MariaDB named lock: a publish's job and an inline sync
    (apply_schema, after_migrate) could otherwise both find a field missing
    and both insert it. The lock belongs to the session, so updatedb's commit
    does not release it. A sync that cannot get it within the wait is logged
    and skipped; the holder, or the next publish or migrate, does the work.
    """
    got = frappe.db.sql(
        "SELECT GET_LOCK(%s, %s)", (_BUDGET_FIELD_SYNC_LOCK, _BUDGET_FIELD_SYNC_LOCK_WAIT))
    if not got or got[0][0] != 1:  # 0: timed out; NULL: error
        frappe.log_error(
            "schema_apply: budget field sync skipped",
            f"GET_LOCK('{_BUDGET_FIELD_SYNC_LOCK}') returned {got!r}: another sync held it.")
        return []
    try:
        return _sync_budget_custom_fields_locked()
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (_BUDGET_FIELD_SYNC_LOCK,))


def _sync_budget_custom_fields_locked():
    # Locking reads. Under REPEATABLE READ a plain read reuses the snapshot
    # from the transaction's first read, taken before this session waited for
    # the lock: it would miss what the previous holder committed (a field it
    # removed, a dimension republished since) and undo it. A locking read
    # sees the latest committed rows.
    budget_dims = frappe.db.sql(
        """SELECT dimension_name, label FROM `tabDimension`
           WHERE in_budget = 1 AND status = 'Published' LOCK IN SHARE MODE""",
        as_dict=True,
    )
    wanted = {d.dimension_name for d in budget_dims}
    label_map = {d.dimension_name: d.label for d in budget_dims}

    # Existing custom fields for Budget Line that are dimension fields
    existing = frappe.db.sql(
        """SELECT name, fieldname FROM `tabCustom Field`
           WHERE dt = %s AND fieldname LIKE %s LOCK IN SHARE MODE""",
        ("Budget Line", "dim_%"),
        as_dict=True,
    )
    existing_names = {cf.fieldname for cf in existing}

    actions = []

    # Add missing
    for dim_name in sorted(wanted - existing_names):
        cf = frappe.new_doc("Custom Field")
        cf.dt = "Budget Line"
        cf.fieldname = dim_name
        cf.fieldtype = "Data"
        cf.label = label_map.get(dim_name, dim_name)
        cf.insert_after = "main_account"
        try:
            cf.insert()
        except frappe.DuplicateEntryError:
            continue  # created outside this sync meanwhile: the goal is met
        actions.append(f"added {dim_name}")

    # Remove orphaned
    for cf in existing:
        if cf.fieldname not in wanted:
            frappe.delete_doc("Custom Field", cf.name)
            actions.append(f"removed {cf.fieldname}")

    return actions
