"""TDD test for the EPM Fiscal Year Period child doctype (konsol#189).

EPM Fiscal Year Period is the period row inside EPM Fiscal Year: one row per
period in a year (opening, regular, closing, adjustment), carrying its own
dates and its own close/lock status.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_JSON = os.path.join(
    APP_DIR, "epm", "doctype", "epm_fiscal_year_period", "epm_fiscal_year_period.json")

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
