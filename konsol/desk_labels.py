"""The one Desk label for each konsol navigation target (Desk IA, 7 Nov 2026).

Every shipped Sidebar link and every Workspace shortcut to a DocType, Report
or Page uses the label written here; a filtered workspace shortcut may add
" waiting". ``konsol/tests/test_desk_navigation.py`` enforces it.

The labels are written out, not computed: ``title()`` and pluralisation get
acronyms (TB, IC, ISO, EPM), singles and uncountables wrong.

Keys are target names: a DocType's name, a Report's name, or a Page's route
name (``konsol-setup``). No DocType shares a name with a Report or Page.
"""

LABELS = {
    # --- Close: trial balances -------------------------------------------
    "Trial Balance Upload": "Trial Balance Uploads",
    "Trial Balance Submission": "Trial Balance Submissions",
    "TB Exception": "TB Exceptions",
    # --- Close: rates ----------------------------------------------------
    "Group Exchange Rate": "Group Exchange Rates",
    "Historical Equity Rate": "Historical Equity Rates",
    # --- Close: intercompany ---------------------------------------------
    "IC Balance": "IC Balances",
    # --- Close: adjustments ----------------------------------------------
    "Consolidation Journal": "Consolidation Journals",
    "Business Combination": "Business Combinations",
    "Business Disposal": "Business Disposals",
    # --- Close: review (reports, then doctypes) --------------------------
    "Period Close": "Period Close",
    "Close Assertions": "Close Assertions",
    "Consolidated Statement": "Consolidated Statement",
    "Statement Drill": "Statement Drill",
    "Statement Commentary": "Statement Commentaries",
    "Assertion Run": "Assertion Runs",
    "Period Status": "Period Statuses",
    "Close Event": "Close Events",
    # --- Close home and My Work ------------------------------------------
    "My Work": "My Work",
    # --- Budget ----------------------------------------------------------
    "Budget Cycle": "Budget Cycles",
    "Budget Sheet": "Budget Sheets",
    "Budget Annual Input": "Budget Annual Inputs",
    "Scenario": "Scenarios",
    "Spread Profile": "Spread Profiles",
    # --- Structure: group ------------------------------------------------
    "Entity": "Entities",
    "Entity Fiscal Calendar": "Entity Fiscal Calendars",
    "Consolidation Group": "Consolidation Groups",
    "Ownership Period": "Ownership Periods",
    # --- Structure: accounts ---------------------------------------------
    "Main Account": "Main Accounts",
    "Main Account Category": "Main Account Categories",
    "Cash Flow Category": "Cash Flow Categories",
    "Reporting Hierarchy": "Reporting Hierarchies",
    # --- Structure: dimensions -------------------------------------------
    "Dimension": "Dimensions",
    "Dimension Value": "Dimension Values",
    "Dimension Mapping": "Dimension Mappings",
    # --- Structure: intercompany -----------------------------------------
    "Intercompany Account": "Intercompany Accounts",
    "IC Elimination Rule": "IC Elimination Rules",
    # --- Structure: calendar ---------------------------------------------
    "EPM Fiscal Year": "EPM Fiscal Years",
    "Fiscal Period": "Fiscal Periods",
    # --- Structure: deals setup, currencies, settings --------------------
    "Fair Value Allocation Profile": "Fair Value Allocation Profiles",
    "ISO Currency": "ISO Currencies",
    "Close Settings": "Close Settings",
    "konsol-setup": "Konsol Setup",
    # --- Data: sources ---------------------------------------------------
    "Connector": "Connectors",
    "Connector Health": "Connector Health",
    # --- Data: pipelines -------------------------------------------------
    "Pipeline": "Pipelines",
    "Pipeline Schedule": "Pipeline Schedules",
    "Pipeline Run": "Pipeline Runs",
    # --- Data: builds ----------------------------------------------------
    "Build Approval": "Build Approvals",
    "Build Scope": "Build Scopes",
    "Build Model": "Build Models",
    # --- Data: model and settings ----------------------------------------
    "Dataset": "Datasets",
    "Measure": "Measures",
    "EPM Settings": "EPM Settings",
    # --- Handbook --------------------------------------------------------
    "Konsol Guide Page": "Konsol Guide Pages",
}

#: The one suffix a filtered workspace shortcut may add to its target's label.
FILTERED_SUFFIX = " waiting"
