"""konsol#305 D53 — child doctypes "Close Deadline Rule" and "Close Holiday".

Decision #305-2.4-1 (Deepak Pai, 6 Oct 2026): group-wide working-day offsets
per step, a declared working week (no Mon–Fri default) and declared holidays,
effective-dated, show-only. The field tables are plan-w5b.md §4b and §4c.

JSON-only test (the pattern of test_close_event_doctype.py): the doctypes'
JSON is read from disk; nothing imports frappe.
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT_ROOT = os.path.join(APP_DIR, "consolidation", "doctype")
RULE_DIR = os.path.join(DT_ROOT, "close_deadline_rule")
HOLIDAY_DIR = os.path.join(DT_ROOT, "close_holiday")
FIXTURES_DIR = os.path.join(APP_DIR, "fixtures")

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# (fieldname, fieldtype, label, reqd, in_list_view) — plan-w5b.md §4b
RULE_FIELDS = [
    ("valid_from", "Date", "Valid From", 1, 1),
    ("change_reason", "Small Text", "Change Reason", 1, 0),
    ("working_week_section", "Section Break", "Working Week", 0, 0),
    ("monday", "Check", "Monday", 0, 0),
    ("tuesday", "Check", "Tuesday", 0, 0),
    ("wednesday", "Check", "Wednesday", 0, 0),
    ("working_week_cb1", "Column Break", None, 0, 0),
    ("thursday", "Check", "Thursday", 0, 0),
    ("friday", "Check", "Friday", 0, 0),
    ("working_week_cb2", "Column Break", None, 0, 0),
    ("saturday", "Check", "Saturday", 0, 0),
    ("sunday", "Check", "Sunday", 0, 0),
    ("offsets_section", "Section Break", "Working Days After Period End", 0, 0),
    ("tb_due_days", "Int", "Trial Balances", 0, 1),
    ("ic_due_days", "Int", "Intercompany", 0, 1),
    ("journals_due_days", "Int", "Journals", 0, 1),
    ("signoff_due_days", "Int", "Sign-off", 0, 1),
]

# plan-w5b.md §4c
HOLIDAY_FIELDS = [
    ("holiday_date", "Date", "Holiday Date", 1, 1),
    ("description", "Data", "Description", 0, 1),
]

# D51's OFFSET_FIELD, and the step labels of plan-w5b.md §4b
OFFSET_FIELD = {
    "tb": "tb_due_days",
    "ic": "ic_due_days",
    "journals": "journals_due_days",
    "signoff": "signoff_due_days",
}
STEP_LABEL = {
    "tb": "Trial Balances",
    "ic": "Intercompany",
    "journals": "Journals",
    "signoff": "Sign-off",
}

HELP = {
    "valid_from": "First period end date this rule governs.",
    "change_reason": "Why this rule starts on this date (shown in the history).",
    "tb_due_days": "Blank or 0: no due date for this step.",
}


def _meta(dt_dir, name):
    with open(os.path.join(dt_dir, name + ".json")) as f:
        return json.load(f)


def _rule():
    return _meta(RULE_DIR, "close_deadline_rule")


def _holiday():
    return _meta(HOLIDAY_DIR, "close_holiday")


def _check_fields(meta, expected):
    got = [
        (f["fieldname"], f["fieldtype"], f.get("label"), int(f.get("reqd") or 0),
         int(f.get("in_list_view") or 0))
        for f in meta["fields"]
    ]
    assert got == expected, got
    assert meta["field_order"] == [e[0] for e in expected], meta["field_order"]


# --- header: a child table in module Consolidation -------------------------

def test_both_are_child_tables_in_consolidation():
    for meta, name in ((_rule(), "Close Deadline Rule"), (_holiday(), "Close Holiday")):
        assert meta["name"] == name
        assert meta["doctype"] == "DocType"
        assert meta["module"] == "Consolidation"
        assert meta["istable"] == 1
        assert not meta.get("issingle")
        assert not meta.get("is_submittable")
        assert meta.get("permissions", []) == []


def test_package_files_exist_and_controllers_are_empty_documents():
    for dt_dir, stem, cls in (
        (RULE_DIR, "close_deadline_rule", "CloseDeadlineRule"),
        (HOLIDAY_DIR, "close_holiday", "CloseHoliday"),
    ):
        assert os.path.isfile(os.path.join(dt_dir, "__init__.py"))
        with open(os.path.join(dt_dir, stem + ".py")) as f:
            src = f.read()
        assert "class %s(Document):" % cls in src
        # not synced to ClickHouse: no warehouse mapping on the controller
        for marker in ("CH_TABLE", "CH_FIELD_MAP", "CH_SYNC_FILTERS"):
            assert marker not in src, (stem, marker)


# --- Close Deadline Rule (plan §4b) ----------------------------------------

def test_rule_fields_types_labels_reqd_list_view_and_order():
    _check_fields(_rule(), RULE_FIELDS)


def test_rule_help_texts():
    fields = {f["fieldname"]: f for f in _rule()["fields"]}
    for name, text in HELP.items():
        assert fields[name].get("description") == text, name


def test_rule_column_breaks_after_wednesday_and_friday():
    order = _rule()["field_order"]
    assert order[order.index("wednesday") + 1] == "working_week_cb1"
    assert order[order.index("friday") + 1] == "working_week_cb2"


def test_no_weekday_has_a_default_no_mon_fri_assumption():
    fields = {f["fieldname"]: f for f in _rule()["fields"]}
    for day in WEEKDAYS:
        assert fields[day]["fieldtype"] == "Check", day
        assert "default" not in fields[day], day
        assert not fields[day].get("reqd"), day


def test_no_field_in_either_doctype_has_a_default():
    for meta in (_rule(), _holiday()):
        for f in meta["fields"]:
            assert "default" not in f, (meta["name"], f["fieldname"], f.get("default"))


def test_offset_fields_match_the_step_labels():
    fields = {f["fieldname"]: f for f in _rule()["fields"]}
    for step, fieldname in OFFSET_FIELD.items():
        assert fields[fieldname]["fieldtype"] == "Int", fieldname
        assert fields[fieldname]["label"] == STEP_LABEL[step], fieldname
        # an Int unset reads 0, so an offset can never be reqd (C-D3)
        assert not fields[fieldname].get("reqd"), fieldname


# --- Close Holiday (plan §4c) ----------------------------------------------

def test_holiday_fields_types_labels_reqd_list_view_and_order():
    _check_fields(_holiday(), HOLIDAY_FIELDS)


# --- not reference data -----------------------------------------------------

def test_neither_is_a_fixture():
    for fn in os.listdir(FIXTURES_DIR):
        path = os.path.join(FIXTURES_DIR, fn)
        if not os.path.isfile(path):
            continue
        with open(path, errors="replace") as f:
            src = f.read()
        assert "Close Deadline Rule" not in src, fn
        assert "Close Holiday" not in src, fn
