app_name = "konsol"
import konsol.excel_addin_cookies  # noqa: F401 — Excel Online iframe cookies

before_request = ["konsol.excel_addin_auth.apply_excel_token_auth"]
after_request = ["konsol.excel_addin_cookies.add_partitioned_cookie_headers"]
app_title = "Konsol"
app_publisher = "Open EPM"
app_description = "EPM Pipeline Control — Airbyte extract + dbt transform"
app_email = "dev@openepm.local"
app_license = "MIT"

# Fixtures — demo data loaded on install/migrate
# ------------------------------------------------
fixtures = [
    # Reference data only: definitions the app needs on any site. Everything
    # in konsol/fixtures/ is force-reimported on EVERY migrate. import_fixtures
    # reads the directory, not this list; the list only matters for export.
    # So a file there must never hold data a site edits, or a demo company.
    #
    # The Contoso/Alpine demo (Consolidation Group, Allocation Rule/Driver,
    # Budget Cycle/Sheet, IC Elimination Rule, Dimension Mapping, Cash Flow
    # Category, Entity Fiscal Calendar, Reporting Hierarchy, the dated
    # Scenarios, and demo_data/'s ownership and annual budget) was removed on
    # 12 Sep 2026. Real data arrives through connectors and Trial Balance
    # Submission.
    "Fiscal Period",
    "Dimension",
    "Measure",
    "Dataset",
    "Scenario",
    "ISO Currency",
    "Spread Profile",
    "Build Scope",
    "Build Model",
    "Pipeline",
]

# After migrate — create EPM roles
# Fresh installs never run after_migrate, so the role and workflow installers
# are here too. Roles first: a workflow transition links to its role.
after_install = ["konsol.install.create_roles", "konsol.workflows.install_workflows"]
after_migrate = ["konsol.install.after_migrate"]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/konsol/css/konsol.css"
# app_include_js = "/assets/konsol/js/konsol.js"

# include js, css files in header of web template
# web_include_css = "/assets/konsol/css/konsol.css"
# web_include_js = "/assets/konsol/js/konsol.js"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# Standalone Konsol Exec SPA (Vite build → public/konsol_exec/)
website_route_rules = [
	{"from_route": "/konsol-exec/<path:app_path>", "to_route": "konsol-exec"},
]

# Scheduled Tasks
# ---------------

scheduler_events = {
    "daily": [
        # F8: rows landed by a submission that crashed before claiming its
        # batch are invisible to bronze forever; age them out.
        "konsol.consolidation.doctype.trial_balance_submission"
        ".trial_balance_submission.reap_unclaimed_submissions",
    ],
    "cron": {
        # Refresh per-connector sync health (status / lag / entities loaded) and
        # alert operators on the transition into Failed/Stale.
        "*/5 * * * *": [
            "konsol.pipeline.doctype.connector_health.connector_health.refresh_connector_health"
        ],
        # Release Assertion Runs stuck Queued/Running (e.g. a dead worker) so the
        # concurrency guard can't wedge permanently. Runs every 10 minutes.
        "*/10 * * * *": [
            "konsol.consolidation.doctype.assertion_run.assertion_run.reap_stale_close_runs"
        ],
        # Orchestrator scheduling: evaluate enabled Pipeline Schedules every
        # minute and start due pipeline runs (PRD-14).
        "* * * * *": [
            "konsol.orchestrator.cron.run_due_schedules"
        ],
        # Release orchestrator Pipeline Runs stuck in an active state (e.g. a
        # dead worker) so the single-flight guard can't wedge permanently (#67).
        # Runs every 15 minutes; the staleness timeout itself is generous
        # (STALE_RUN_TIMEOUT_MINUTES) so a long dbt step is never falsely reaped.
        # Also release Build Approvals stuck Approved (job lost) or Running
        # (worker died): the build debounce counts both as in flight, so one
        # stuck row blocks every auto-build for its scope (#125).
        "*/15 * * * *": [
            "konsol.orchestrator.reaper.reap_stale_runs",
            "konsol.orchestrator.reaper.reap_stale_build_approvals",
        ],
    }
}

# ---------------------------------------------------------------------------
# Auto-trigger dbt build after consolidation/allocation doc saves
# ---------------------------------------------------------------------------
# After a user saves any of these doctypes (which sync to ClickHouse staging),
# a debounced dbt build is enqueued to refresh the gold models.

_dbt_trigger_doctypes = [
    "Consolidation Group",
    "Consolidation Adjustment",
    "Ownership Period",
    "Historical Equity Rate",
    "IC Elimination Rule",
    "IC Balance",
    "Allocation Rule",
    "Allocation Driver",
    "Allocation Run",
    # F8: a submitted TB must reach gold, and a CANCELLED one must leave it.
    # Caveat: the debounce counts a Running build as pending, so a request that
    # arrives after a running build has read its inputs is absorbed; tracked
    # as its own issue.
    # Safe since konsol#126: the trigger queues a job after the commit, so it
    # can no longer commit docstatus=1 before on_submit has claimed the rows.
    "Trial Balance Submission",
    # NOT "Entity" (konsol#110). Its build is requested from the controller,
    # and only when a field the warehouse reads changes — listing it here would
    # ask an EPM Admin to approve a consolidation rebuild for a renamed
    # country. DOCTYPE_BUILD_MAP still carries its scope.
]

# queue_consolidation_build, NOT on_consolidation_doc_update: that one commits,
# and from a document hook the commit landed mid-transaction. On submit it
# committed docstatus=1 before on_submit ran (konsol#126). The queue runs it as a
# job after the commit.
doc_events = {
    dt: {
        "on_update": "konsol.tasks.queue_consolidation_build",
        "on_submit": "konsol.tasks.queue_consolidation_build",
        "on_cancel": "konsol.tasks.queue_consolidation_build",
    }
    for dt in _dbt_trigger_doctypes
}

# ---------------------------------------------------------------------------
# Entity-scoped access (#91)
# ---------------------------------------------------------------------------
# Every doctype carrying `data_area_id` is filtered to the entities a user's
# User Permissions grant, expanded down the Entity tree so an assignment to a
# region carries its subsidiaries. Without these hooks desk list views were
# never entity-filtered at all — only the explicit checks in api.py were, and
# only where someone remembered to call them.

from konsol.entity_permissions import ENTITY_SCOPED_DOCTYPES as _ENTITY_SCOPED

permission_query_conditions = {
    dt: f"konsol.entity_permissions.{dt.lower().replace(' ', '_')}_conditions"
    for dt in _ENTITY_SCOPED
}
permission_query_conditions["Entity"] = "konsol.entity_permissions.entity_conditions"

# Query conditions cover lists and reports; has_permission covers opening a
# single document, which they do not.
has_permission = {
    dt: "konsol.entity_permissions.has_entity_permission" for dt in _ENTITY_SCOPED
}
has_permission["Entity"] = "konsol.entity_permissions.has_entity_doc_permission"

