app_name = "konsol"
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
    "Measure",
    "Fact Table",
    "Scenario Definition",
    "Consolidation Group",
    "IC Elimination Rule",
    "Allocation Rule",
    "Spread Profile",
    "Connector",
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

# Scheduled Tasks
# ---------------

# scheduler_events = {
#     "cron": {
#         "0 6 * * *": [
#             "konsol.tasks.run_pipeline"
#         ]
#     }
# }

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
