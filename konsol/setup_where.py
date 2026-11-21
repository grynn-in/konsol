"""Where each setup step is done in the Desk, and what to do there. Pure.

Deepak Pai, 21 Nov 2026, looking at the Home page of an empty site: "can you
also write in the messages where to go and set up everything?"

One source for that text. The Konsol Setup page (``setup_model``), the Home
cards and checklist (``close/overview_model``), the first-close messages
(``close/period_model``, ``close/signoff_model``, ``close/signoff_gate``) and
My Work's fixes (``close/mywork_report_model``) quote it; the pure modules
load this file by path, and tests pin every quote to it.

A path is what a user clicks in the v16 Desk: the Dock module (Close,
Planning, Structure, Data), then the sidebar link, then a tab or button.
``ROUTES`` is the Desk route that path opens. Imports nothing.
"""

#: step key -> (path, what to do there).
WHERE = {
    "fiscal_calendar": (
        "Structure → EPM Fiscal Years → + Add",
        "enter Fiscal Year, Start Date, End Date and Period Pattern, then click Generate Periods"),
    "first_close": (
        "Structure → Close Settings → Starting Point tab",
        "set First Close Fiscal Year and First Close Fiscal Period, then save"),
    "consolidation_group": (
        "Structure → Consolidation Groups → + Add",
        "add the group's own node with Entity left blank and a Reporting Currency"),
    "statement_accounts": (
        "Structure → Close Settings → Statement Accounts tab",
        "set the accounts the message names, then save"),
    "entities": (
        "Structure → Entities → + Add",
        "set Functional Currency and Reporting Frequency, then add the entity to its "
        "Consolidation Group"),
    "chart_of_accounts": (
        "Structure → Main Accounts",
        "add the group chart's accounts, then open each one and click Publish"),
    "dimensions": (
        "Structure → Dimensions → + Add",
        "declare each dimension the site uses and publish it"),
    "currencies": (
        "Structure → ISO Currencies",
        "open each currency the message names and set Minor Unit"),
    "intercompany": (
        "Structure → Intercompany Accounts → + Add",
        "publish each pair of intercompany accounts, or declare none in Close Settings → "
        "Starting Point tab → Intercompany Declaration"),
    "users": (
        "the User list (System Manager only)",
        "give each user a role, and give each Entity Accountant a User Permission on Entity"),
    "trial_balance": (
        "Close → Trial Balance Submissions → + Add",
        "choose Entity, Fiscal Year and Fiscal Period, attach the Trial Balance CSV, save, "
        "then submit"),
    "rates": (
        "Close → Group Exchange Rates → + Add",
        "add the Closing and Average rates for the period, then send them for approval"),
    "warehouse": (
        "Data → Build Approvals",
        "submitting a trial balance requests a build; the Close Lead approves it there"),
}

#: step key -> the Desk route its path opens.
ROUTES = {
    "fiscal_calendar": "/desk/epm-fiscal-year",
    "first_close": "/desk/close-settings",
    "consolidation_group": "/desk/consolidation-group",
    "statement_accounts": "/desk/close-settings",
    "entities": "/desk/entity",
    "chart_of_accounts": "/desk/main-account",
    "dimensions": "/desk/dimension",
    "currencies": "/desk/iso-currency",
    "intercompany": "/desk/intercompany-account",
    "users": "/desk/user",
    "trial_balance": "/desk/trial-balance-submission",
    "rates": "/desk/group-exchange-rate",
    "warehouse": "/desk/build-approval",
}

#: The Konsol Setup page: every step, done or not (EPM Admin, System Manager).
SETUP_PAGE = "/desk/konsol-setup"


def path(key):
    """"Structure → EPM Fiscal Years → + Add"."""
    return WHERE[key][0]


def where(key):
    """"Go to <path>: <what to do>." One sentence."""
    place, todo = WHERE[key]
    return "Go to %s: %s." % (place, todo)


def route(key):
    return ROUTES[key]
