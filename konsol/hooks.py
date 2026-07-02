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
    "Fiscal Period",
    "Dimension",
    "Dimension Mapping",
    "Cash Flow Category",
    "Reporting Hierarchy",
    "Reporting Hierarchy Member",
    "Measure",
    "Fact Table",
    "Scenario Definition",
    "Budget Cycle",
    "Budget Sheet",
    "Consolidation Group",
    "IC Elimination Rule",
    "Allocation Rule",
    "Allocation Driver",
    "Spread Profile",
    "Connector",
    "Build Scope",
    "Build Model",
    "Pipeline",
]

# After migrate — create EPM roles
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
    "cron": {
        # Refresh per-connector sync health (status / lag / entities loaded) and
        # alert operators on the transition into Failed/Stale.
        "*/5 * * * *": [
            "konsol.pipeline.doctype.connector_health.connector_health.refresh_connector_health"
        ],
        # Release Close Runs stuck Queued/Running (e.g. a dead worker) so the
        # concurrency guard can't wedge permanently. Runs every 10 minutes.
        "*/10 * * * *": [
            "konsol.consolidation.doctype.close_run.close_run.reap_stale_close_runs"
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
        "*/15 * * * *": [
            "konsol.orchestrator.reaper.reap_stale_runs"
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
]

doc_events = {
    dt: {
        "on_update": "konsol.tasks.on_consolidation_doc_update",
        "on_submit": "konsol.tasks.on_consolidation_doc_update",
        "on_cancel": "konsol.tasks.on_consolidation_doc_update",
    }
    for dt in _dbt_trigger_doctypes
}
