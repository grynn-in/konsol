"""TDD test for the EPM Fiscal Year Period child doctype and the EPM Fiscal
Year parent doctype (konsol#189).

EPM Fiscal Year Period is the period row inside EPM Fiscal Year: one row per
period in a year (opening, regular, closing, adjustment), carrying its own
dates and its own close/lock status.
"""
import ast
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_JSON = os.path.join(
    APP_DIR, "epm", "doctype", "epm_fiscal_year_period", "epm_fiscal_year_period.json")
YEAR_DOCTYPE_JSON = os.path.join(
    APP_DIR, "epm", "doctype", "epm_fiscal_year", "epm_fiscal_year.json")
SETTINGS_JSON = os.path.join(
    APP_DIR, "pipeline", "doctype", "epm_settings", "epm_settings.json")
YEAR_LIST_JS = os.path.join(
    APP_DIR, "epm", "doctype", "epm_fiscal_year", "epm_fiscal_year_list.js")
HOME_JS = os.path.join(os.path.dirname(APP_DIR), "konsol-exec", "src", "home.js")

FIELD_ORDER = [
    "fiscal_period",
    "period_code",
    "period_label",
    "period_type",
    "quarter",
    "dates_col_break",
    "start_date",
    "end_date",
    "close_section",
    "status",
    "close_col_break",
    "closed_by",
    "closed_on",
]

EXPECTED_FIELDS = {
    "fiscal_period": {
        "fieldtype": "Int",
        "label": "Period",
        "reqd": 1,
        "in_list_view": 1,
        "columns": 1,
        "description": (
            "The period number documents carry. 0 = OPN; 1..n regular; then "
            "closing and adjustment periods. Unique in the year; 0 to 255."),
    },
    "period_code": {
        "fieldtype": "Data",
        "label": "Code",
        "reqd": 1,
        "in_list_view": 1,
        "columns": 1,
        "description": "Short name, e.g. OPN, P01, CLS, P14. Unique in the year.",
    },
    "period_label": {
        "fieldtype": "Data",
        "label": "Label",
        "description": "Display name, e.g. Jan 2025. Blank shows the code.",
    },
    "period_type": {
        "fieldtype": "Select",
        "label": "Type",
        "options": "Opening\nRegular\nClosing\nAdjustment",
        "reqd": 1,
        "in_list_view": 1,
        "columns": 2,
        "description": (
            "Regular periods tile the year. Opening is period 0 on the first "
            "day; Closing and Adjustment come after the last regular period. "
            "Which types take trial balances is set in EPM Settings."),
    },
    "quarter": {
        "fieldtype": "Select",
        "label": "Quarter",
        "options": "\nQ1\nQ2\nQ3\nQ4",
        "depends_on": "eval:doc.period_type == 'Regular'",
        "description": (
            "Quarter for quarterly and half-year reporting (Q1-Q2 = H1). "
            "Generate Periods fills it for Monthly and 4-4-5; 13-period "
            "years leave it blank."),
    },
    "dates_col_break": {
        "fieldtype": "Column Break",
    },
    "start_date": {
        "fieldtype": "Date",
        "label": "Start Date",
        "reqd": 1,
        "in_list_view": 1,
        "columns": 2,
        "description": "Inside the year.",
    },
    "end_date": {
        "fieldtype": "Date",
        "label": "End Date",
        "reqd": 1,
        "in_list_view": 1,
        "columns": 2,
        "description": (
            "On or after Start Date. Opening and Closing periods are one "
            "day: the year's first or last."),
    },
    "close_section": {
        "fieldtype": "Section Break",
        "label": "Close",
    },
    "status": {
        "fieldtype": "Select",
        "label": "Status",
        "options": "Open\nClosed\nLocked",
        "default": "Open",
        "reqd": 1,
        "read_only": 1,
        "in_list_view": 1,
        "columns": 2,
        "description": (
            "Set by Close Period, Lock Period and Reopen Period, or by the "
            "year's actions. Never looser than the year's."),
    },
    "close_col_break": {
        "fieldtype": "Column Break",
    },
    "closed_by": {
        "fieldtype": "Link",
        "label": "Closed By",
        "options": "User",
        "read_only": 1,
        "depends_on": "eval:doc.status != 'Open'",
    },
    "closed_on": {
        "fieldtype": "Datetime",
        "label": "Closed On",
        "read_only": 1,
        "depends_on": "eval:doc.status != 'Open'",
    },
}

# Keys checked on every field. Anything not listed in EXPECTED_FIELDS[name]
# must be absent/falsy on the field.
CHECKED_KEYS = (
    "fieldtype", "label", "options", "reqd", "read_only", "default",
    "depends_on", "in_list_view", "columns", "description",
)


def _load():
    with open(DOCTYPE_JSON) as f:
        return json.load(f)


def _load_year():
    with open(YEAR_DOCTYPE_JSON) as f:
        return json.load(f)


def test_period_row_fields():
    meta = _load()

    assert meta["name"] == "EPM Fiscal Year Period"
    assert meta["module"] == "EPM"
    assert meta["istable"] == 1
    assert meta["editable_grid"] == 1

    assert meta["field_order"] == FIELD_ORDER
    assert [f["fieldname"] for f in meta["fields"]] == FIELD_ORDER

    by_name = {f["fieldname"]: f for f in meta["fields"]}
    for fieldname in FIELD_ORDER:
        field = by_name[fieldname]
        expected = EXPECTED_FIELDS[fieldname]
        for key in CHECKED_KEYS:
            want = expected.get(key)
            got = field.get(key)
            if want:
                assert got == want, f"{fieldname}.{key}: want {want!r}, got {got!r}"
            else:
                assert not got, f"{fieldname}.{key}: want falsy, got {got!r}"


# --- EPM Fiscal Year (parent) -----------------------------------------------

YEAR_FIELD_ORDER = [
    "details_tab",
    "year_section",
    "fiscal_year",
    "start_date",
    "year_col_break",
    "end_date",
    "status",
    "calendar_section",
    "period_pattern",
    "calendar_col_break",
    "include_opening_period",
    "include_closing_period",
    "periods_tab",
    "periods_section",
    "periods",
    "closing_tab",
    "closed_section",
    "closed_by",
    "closed_col_break",
    "closed_on",
    "notes_section",
    "closing_note",
]

YEAR_EXPECTED_FIELDS = {
    "details_tab": {
        "fieldtype": "Tab Break",
        "label": "Details",
    },
    "year_section": {
        "fieldtype": "Section Break",
        "label": "Year",
    },
    "fiscal_year": {
        "fieldtype": "Int",
        "label": "Fiscal Year",
        "reqd": 1,
        "bold": 1,
        "unique": 1,
        "set_only_once": 1,
        "in_standard_filter": 1,
        "in_list_view": 1,
        "description": (
            "The year number every document carries in Fiscal Year, e.g. "
            "2025. It names the record and cannot change. Must be 1970 to "
            "2148 (the warehouse's date range)."),
    },
    "start_date": {
        "fieldtype": "Date",
        "label": "Start Date",
        "reqd": 1,
        "in_list_view": 1,
        "description": "First day of the fiscal year.",
    },
    "year_col_break": {
        "fieldtype": "Column Break",
    },
    "end_date": {
        "fieldtype": "Date",
        "label": "End Date",
        "reqd": 1,
        "in_list_view": 1,
        "description": "Last day of the fiscal year; after Start Date.",
    },
    "status": {
        "fieldtype": "Select",
        "label": "Status",
        "options": "Open\nClosed\nLocked",
        "default": "Open",
        "reqd": 1,
        "read_only": 1,
        "in_standard_filter": 1,
        "in_list_view": 1,
        "description": (
            "Set by Close Year, Lock Year and Reopen Year. A period is open "
            "only while it and its year are both Open."),
    },
    "calendar_section": {
        "fieldtype": "Section Break",
        "label": "Calendar",
    },
    "period_pattern": {
        "fieldtype": "Select",
        "label": "Period Pattern",
        "options": "Monthly (12)\n13 Periods (4 Weeks)\n4-4-5\nCustom",
        "default": "Monthly (12)",
        "reqd": 1,
        "description": (
            "What Generate Periods builds. Custom: enter the periods by "
            "hand. 13 Periods and 4-4-5 need a 364- or 371-day year."),
    },
    "calendar_col_break": {
        "fieldtype": "Column Break",
    },
    "include_opening_period": {
        "fieldtype": "Check",
        "label": "Include Opening Period",
        "default": "1",
        "depends_on": "eval:doc.period_pattern != 'Custom'",
        "description": (
            "Generate Periods adds OPN (period 0) on the first day of the "
            "year."),
    },
    "include_closing_period": {
        "fieldtype": "Check",
        "label": "Include Closing Period",
        "default": "1",
        "depends_on": "eval:doc.period_pattern != 'Custom'",
        "description": (
            "Generate Periods adds CLS after the last regular period, on "
            "the last day of the year."),
    },
    "periods_tab": {
        "fieldtype": "Tab Break",
        "label": "Periods",
    },
    "periods_section": {
        "fieldtype": "Section Break",
        "label": "Periods",
    },
    "periods": {
        "fieldtype": "Table",
        "label": "Periods",
        "options": "EPM Fiscal Year Period",
        "reqd": 1,
        "description": (
            "Built by Generate Periods, then editable while the year is "
            "Open. A period that documents use cannot be removed, "
            "renumbered or re-dated."),
    },
    "closing_tab": {
        "fieldtype": "Tab Break",
        "label": "Closing",
    },
    "closed_section": {
        "fieldtype": "Section Break",
        "label": "Closed",
        "depends_on": "eval:doc.status != 'Open'",
    },
    "closed_by": {
        "fieldtype": "Link",
        "label": "Closed By",
        "options": "User",
        "read_only": 1,
        "description": "Who closed or locked the year.",
    },
    "closed_col_break": {
        "fieldtype": "Column Break",
    },
    "closed_on": {
        "fieldtype": "Datetime",
        "label": "Closed On",
        "read_only": 1,
    },
    "notes_section": {
        "fieldtype": "Section Break",
        "label": "Notes",
    },
    "closing_note": {
        "fieldtype": "Small Text",
        "label": "Closing Note",
        "description": (
            "Why the year was closed, locked or reopened. The reopen "
            "actions append the reason given."),
    },
}

# Superset of CHECKED_KEYS: the parent form also declares bold/unique/
# set_only_once/in_standard_filter, none of which the child row uses.
YEAR_CHECKED_KEYS = CHECKED_KEYS + ("in_standard_filter", "bold", "unique", "set_only_once")

YEAR_PERMISSIONS = {
    "System Manager": {"read": 1, "write": 1, "create": 1, "delete": 1, "report": 1, "export": 1, "print": 1},
    "EPM Admin": {"read": 1, "write": 1, "create": 1, "report": 1, "export": 1, "print": 1},
    "EPM Analyst": {"read": 1, "report": 1, "export": 1, "print": 1},
    "EPM User": {"read": 1, "report": 1, "print": 1},
    "Entity Accountant": {"read": 1, "report": 1, "print": 1},
}

PERMISSION_FLAGS = ("read", "write", "create", "delete", "submit", "cancel", "report", "export", "print")


def test_year_form_layout():
    meta = _load_year()

    assert meta["name"] == "EPM Fiscal Year"
    assert meta["module"] == "EPM"
    assert meta["istable"] == 0

    assert meta["field_order"] == YEAR_FIELD_ORDER
    assert [f["fieldname"] for f in meta["fields"]] == YEAR_FIELD_ORDER

    by_name = {f["fieldname"]: f for f in meta["fields"]}
    for fieldname in YEAR_FIELD_ORDER:
        field = by_name[fieldname]
        expected = YEAR_EXPECTED_FIELDS[fieldname]
        for key in YEAR_CHECKED_KEYS:
            want = expected.get(key)
            got = field.get(key)
            if want:
                assert got == want, f"{fieldname}.{key}: want {want!r}, got {got!r}"
            else:
                assert not got, f"{fieldname}.{key}: want falsy, got {got!r}"


def test_year_props():
    meta = _load_year()

    assert meta["autoname"] == "format:{fiscal_year}"
    assert meta["naming_rule"] == "Expression"
    assert not meta.get("allow_rename")
    assert meta["title_field"] == "fiscal_year"
    assert meta["search_fields"] == "status,period_pattern"
    assert meta["sort_field"] == "fiscal_year"
    assert meta["sort_order"] == "DESC"
    assert meta["track_changes"] == 1
    assert not meta.get("is_submittable")

    by_role = {p["role"]: p for p in meta["permissions"]}
    assert set(by_role) == set(YEAR_PERMISSIONS)
    for role, expected in YEAR_PERMISSIONS.items():
        got = by_role[role]
        for flag in PERMISSION_FLAGS:
            want = expected.get(flag, 0)
            assert bool(got.get(flag)) == bool(want), f"{role}.{flag}: want {want!r}, got {got.get(flag)!r}"


# --- EPM Settings: trial balance postable period types (konsol#189) --------

TB_SECTION_DESCRIPTION = (
    "Regular periods always take trial balances. Tick a period type to let "
    "trial balances post to it too (konsol#189).")

TB_CHECK_FIELDS = (
    "tb_accepts_opening",
    "tb_accepts_closing",
    "tb_accepts_adjustment",
)


def test_postable_type_settings():
    with open(SETTINGS_JSON) as f:
        settings = json.load(f)

    by_name = {f["fieldname"]: f for f in settings["fields"]}

    for fieldname in TB_CHECK_FIELDS:
        assert fieldname in by_name, f"Missing field: {fieldname}"
        field = by_name[fieldname]
        assert field["fieldtype"] == "Check"
        assert field["default"] == "0"

    section = by_name["tb_periods_section"]
    assert section["fieldtype"] == "Section Break"
    assert section["label"] == "Trial Balance Periods"
    assert section["description"] == TB_SECTION_DESCRIPTION

    tab = by_name["tab_close"]
    assert tab["fieldtype"] == "Tab Break"
    assert tab["label"] == "Close"

    field_order = settings["field_order"]
    expected_tail = ["tab_close", "tb_periods_section"] + list(TB_CHECK_FIELDS)
    assert field_order[-5:] == expected_tail


# --- EPM Fiscal Year: list view indicator colour matches home theme --------

FISCAL_YEAR_STATUSES = ("Open", "Closed", "Locked")


def _status_colors(js_source):
    """Pull {status: "colour"} pairs for our three statuses out of a JS file.

    Regex, not a JS parser — good enough to compare literal colour strings
    between konsol's list view and konsol-exec's home theme.
    """
    colors = {}
    for status in FISCAL_YEAR_STATUSES:
        m = re.search(status + r'\s*:\s*(?:__\()?"(\w+)"', js_source)
        assert m, f"No colour found for status {status!r}"
        colors[status] = m.group(1)
    return colors


def test_list_indicator_matches_period_theme():
    with open(YEAR_LIST_JS) as f:
        list_js = f.read()
    assert 'frappe.listview_settings["EPM Fiscal Year"]' in list_js
    assert "get_indicator" in list_js

    with open(HOME_JS) as f:
        home_js = f.read()
    assert "PERIOD_THEME" in home_js

    list_colors = _status_colors(list_js)
    theme_colors = _status_colors(home_js)

    for status in FISCAL_YEAR_STATUSES:
        assert list_colors[status] == theme_colors[status], (
            f"{status}: list view uses {list_colors[status]!r}, "
            f"PERIOD_THEME uses {theme_colors[status]!r}")


# --- on the desk (konsol#189, modelled on test_main_account.py) -------------

def _literal(path, name):
    with open(path) as f:
        tree = ast.parse(f.read())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == name)


def test_fiscal_year_on_the_desk():
    labels = _literal(os.path.join(APP_DIR, "dashboard.py"), "_LABELS")
    assert labels["EPM Fiscal Year"] == "Fiscal Year"
    cards = dict(_literal(os.path.join(APP_DIR, "dashboard.py"), "_CARDS"))
    assert "EPM Fiscal Year" in cards["Reference Data"]
    with open(os.path.join(APP_DIR, "dashboard.py")) as f:
        refresh = f.read().split("def _workspace_needs_refresh")[1].split("\ndef ")[0]
    assert '"EPM Fiscal Year" not in' in refresh, "existing sites must rebuild the card once"
