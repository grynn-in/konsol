"""TDD — Scheduling (PRD-14).

Two pieces under test, both host-runnable (no bench):

1. The pure 5-field cron matcher ``konsol.orchestrator.cron.is_due`` —
   ``*``, ``*/n``, ``a-b``, ``a,b,c`` and bare literals on
   minute/hour/dom/month/dow, plus a ``last_run`` guard that prevents a
   double-fire within the same minute.
2. The ``Pipeline Schedule`` doctype JSON field set.
3. ``hooks.py`` wires ``run_due_schedules`` into ``scheduler_events``.

The frappe-bound ``run_due_schedules`` is guarded with ``importorskip``.
"""
import datetime
import json
import os

import pytest

from konsol.orchestrator import cron

_HERE = os.path.dirname(__file__)


def _dt(minute=0, hour=0, day=1, month=1, year=2026):
    return datetime.datetime(year, month, day, hour, minute)


# --- cron.py is frappe-free at top level ------------------------------------


def test_cron_module_has_no_top_level_frappe_import():
    path = os.path.abspath(os.path.join(_HERE, "..", "orchestrator", "cron.py"))
    with open(path) as fh:
        src = fh.read()
    # Only flag *top-level* (unindented) imports; function-local imports are fine.
    for line in src.splitlines():
        assert not line.startswith("import frappe"), "no top-level frappe import"
        assert not line.startswith("from frappe"), "no top-level frappe import"


# --- wildcard / step ---------------------------------------------------------


def test_every_minute_always_due():
    assert cron.is_due("* * * * *", _dt(minute=37, hour=13)) is True


def test_step_minute_true_at_0_and_10():
    assert cron.is_due("*/10 * * * *", _dt(minute=0)) is True
    assert cron.is_due("*/10 * * * *", _dt(minute=10)) is True


def test_step_minute_false_at_7():
    assert cron.is_due("*/10 * * * *", _dt(minute=7)) is False


def test_step_minute_30_due_at_30():
    assert cron.is_due("*/30 * * * *", _dt(minute=30)) is True
    assert cron.is_due("*/30 * * * *", _dt(minute=31)) is False


# --- literals ----------------------------------------------------------------


def test_literal_minute():
    assert cron.is_due("5 * * * *", _dt(minute=5)) is True
    assert cron.is_due("5 * * * *", _dt(minute=6)) is False


def test_specific_hour():
    assert cron.is_due("0 9 * * *", _dt(minute=0, hour=9)) is True
    assert cron.is_due("0 9 * * *", _dt(minute=0, hour=10)) is False


def test_specific_dom():
    # 1st of the month at 00:00
    assert cron.is_due("0 0 1 * *", _dt(minute=0, hour=0, day=1)) is True
    assert cron.is_due("0 0 1 * *", _dt(minute=0, hour=0, day=2)) is False


def test_specific_month():
    assert cron.is_due("0 0 1 6 *", _dt(day=1, month=6)) is True
    assert cron.is_due("0 0 1 6 *", _dt(day=1, month=7)) is False


def test_specific_dow():
    # 2026-06-29 is a Monday (weekday()==0 → cron dow 1)
    monday = _dt(minute=0, hour=0, day=29, month=6)
    assert monday.weekday() == 0
    assert cron.is_due("0 0 * * 1", monday) is True
    assert cron.is_due("0 0 * * 2", monday) is False


def test_dow_sunday_zero():
    # 2026-06-28 is a Sunday → cron dow 0 (and also 7 accepted)
    sunday = _dt(minute=0, hour=0, day=28, month=6)
    assert sunday.weekday() == 6
    assert cron.is_due("0 0 * * 0", sunday) is True
    assert cron.is_due("0 0 * * 7", sunday) is True


# --- list / range ------------------------------------------------------------


def test_list_minutes():
    assert cron.is_due("0,15,30,45 * * * *", _dt(minute=15)) is True
    assert cron.is_due("0,15,30,45 * * * *", _dt(minute=20)) is False


def test_range_hours():
    assert cron.is_due("0 9-17 * * *", _dt(minute=0, hour=12)) is True
    assert cron.is_due("0 9-17 * * *", _dt(minute=0, hour=8)) is False
    assert cron.is_due("0 9-17 * * *", _dt(minute=0, hour=17)) is True


def test_combined_list_and_range():
    expr = "0,30 9-17 * * 1-5"
    # Wednesday 2026-07-01 at 09:30 → due
    wed = _dt(minute=30, hour=9, day=1, month=7)
    assert wed.weekday() == 2
    assert cron.is_due(expr, wed) is True
    # Saturday 2026-07-04 at 09:30 → not due (dow out of 1-5)
    sat = _dt(minute=30, hour=9, day=4, month=7)
    assert sat.weekday() == 5
    assert cron.is_due(expr, sat) is False


# --- last_run guard ----------------------------------------------------------


def test_last_run_blocks_same_minute_refire():
    now = _dt(minute=10, hour=0)
    last = _dt(minute=10, hour=0)
    assert cron.is_due("*/10 * * * *", now, last_run=last) is False


def test_last_run_in_previous_minute_allows_fire():
    now = _dt(minute=10, hour=0)
    last = _dt(minute=9, hour=0)
    assert cron.is_due("*/10 * * * *", now, last_run=last) is True


def test_last_run_none_allows_fire():
    assert cron.is_due("*/10 * * * *", _dt(minute=0), last_run=None) is True


# --- invalid expression ------------------------------------------------------


def test_wrong_field_count_raises():
    with pytest.raises(ValueError):
        cron.is_due("* * * *", _dt())


# --- Pipeline Schedule doctype JSON -----------------------------------------


def _schedule_doc():
    path = os.path.abspath(
        os.path.join(
            _HERE, "..", "pipeline", "doctype", "pipeline_schedule",
            "pipeline_schedule.json",
        )
    )
    with open(path) as fh:
        return json.load(fh)


def _fields_by_name(doc):
    return {f["fieldname"]: f for f in doc["fields"]}


def test_schedule_doctype_loads():
    doc = _schedule_doc()
    assert doc["name"] == "Pipeline Schedule"
    assert doc["module"] == "Pipeline"
    assert doc.get("istable", 0) == 0


def test_schedule_autoname():
    doc = _schedule_doc()
    assert doc.get("autoname") == "field:schedule_name"


def test_schedule_fields_present():
    fields = _fields_by_name(_schedule_doc())
    for name in (
        "schedule_name",
        "pipeline_definition",
        "cron",
        "params",
        "enabled",
        "last_run",
        "next_run",
    ):
        assert name in fields, f"missing Pipeline Schedule field {name!r}"


def test_schedule_field_types():
    fields = _fields_by_name(_schedule_doc())
    assert fields["schedule_name"]["fieldtype"] == "Data"
    assert fields["pipeline_definition"]["fieldtype"] == "Link"
    assert fields["pipeline_definition"]["options"] == "Pipeline Definition"
    assert fields["cron"]["fieldtype"] == "Data"
    assert fields["params"]["fieldtype"] in {"Code", "JSON", "Small Text"}
    assert fields["enabled"]["fieldtype"] == "Check"
    assert fields["last_run"]["fieldtype"] == "Datetime"
    assert fields["next_run"]["fieldtype"] == "Datetime"


def test_schedule_next_run_read_only():
    fields = _fields_by_name(_schedule_doc())
    assert fields["next_run"].get("read_only") == 1


# --- hooks wiring ------------------------------------------------------------


def test_hooks_reference_run_due_schedules():
    hooks_path = os.path.abspath(os.path.join(_HERE, "..", "hooks.py"))
    with open(hooks_path) as fh:
        text = fh.read()
    assert "konsol.orchestrator.cron.run_due_schedules" in text


# --- frappe-bound entrypoint (guarded) --------------------------------------


def test_run_due_schedules_callable():
    pytest.importorskip("frappe")
    assert callable(cron.run_due_schedules)
