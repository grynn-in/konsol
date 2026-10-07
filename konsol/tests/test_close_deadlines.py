"""Deadline reader: konsol/close/deadlines.py (konsol#305 D55; story 2.4;
C-D2, C-D4).

``period_deadlines(keys, today)`` reads the Close Settings deadline rules and
holidays ONCE (two ``get_all`` reads) and the fiscal calendar once, and
returns ``{(fy, fp): deadline_model.period_deadlines(...)}`` for the Regular
keys asked for. A non-Regular key is absent (C-D4). ``as_payload`` is the
JSON-safe form of one period's steps.

Loaded against a stub frappe and a stub ``konsol.fiscal_calendar``. The
expected values come from the REAL ``deadline_model.py``, loaded by path.
"""
import importlib.util
import json
import os
import sys
import types
from datetime import date

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSE_DIR = os.path.join(APP_DIR, "close")
READER_PY = os.path.join(CLOSE_DIR, "deadlines.py")

PARENT_FILTERS = {"parent": "Close Settings", "parenttype": "Close Settings"}


def _load_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODEL = _load_path("deadline_model_for_deadlines_test", os.path.join(CLOSE_DIR, "deadline_model.py"))

MON_FRI = {"monday": 1, "tuesday": 1, "wednesday": 1, "thursday": 1, "friday": 1,
           "saturday": 0, "sunday": 0}


def _rule(valid_from, tb=5, ic=7, journals=9, signoff=12, week=None):
    rule = {"valid_from": valid_from, "tb_due_days": tb, "ic_due_days": ic,
            "journals_due_days": journals, "signoff_due_days": signoff}
    rule.update(week if week is not None else MON_FRI)
    return rule


def _period(fy, fp, end_date, period_type="Regular", status="Open"):
    return {"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
            "period_label": "P%02d" % fp, "period_type": period_type,
            "start_date": None, "end_date": end_date, "quarter": "", "status": status}


class _Throw(Exception):
    pass


class _Site:
    def __init__(self):
        self.rules = []
        self.holidays = []
        self.periods = [
            _period(2025, 6, date(2025, 6, 30)),
            _period(2025, 7, date(2025, 7, 31)),
            _period(2025, 8, date(2025, 8, 31)),
            _period(2025, 13, date(2025, 12, 31), period_type="Closing"),
        ]
        self.reads = []          # ("get_all", doctype, kwargs) and ("calendar",)
        self.whitelisted = []


def _load(site):
    frappe = types.ModuleType("frappe")

    def get_all(doctype, filters=None, fields=None, limit_page_length=None, **kw):
        site.reads.append(("get_all", doctype, {"filters": filters, "fields": fields,
                                                "limit_page_length": limit_page_length, **kw}))
        source = {"Close Deadline Rule": site.rules, "Close Holiday": site.holidays}[doctype]
        return [{f: row.get(f) for f in fields} for row in source]

    def throw(msg, *a, **k):
        raise _Throw(msg)

    def whitelist(*a, **k):
        site.whitelisted.append((a, k))
        return lambda fn: fn

    frappe.get_all = get_all
    frappe.throw = throw
    frappe.whitelist = whitelist

    konsol = types.ModuleType("konsol")
    konsol.__path__ = []
    fiscal_calendar = types.ModuleType("konsol.fiscal_calendar")

    def fiscal_period_rows():
        site.reads.append(("calendar",))
        return [dict(row) for row in site.periods]

    fiscal_calendar.fiscal_period_rows = fiscal_period_rows
    konsol.fiscal_calendar = fiscal_calendar

    stubs = {"frappe": frappe, "konsol": konsol, "konsol.fiscal_calendar": fiscal_calendar}
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        return _load_path("deadlines_under_test", READER_PY)
    finally:
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


TODAY = date(2025, 8, 20)


def _declared_site():
    site = _Site()
    site.rules = [_rule(date(2025, 1, 1)), _rule(date(2025, 8, 1), tb=3, ic=0, journals=None)]
    site.holidays = [{"holiday_date": date(2025, 8, 4)}]
    return site


def test_three_keys_read_settings_twice_and_calendar_once():
    site = _declared_site()
    reader = _load(site)
    out = reader.period_deadlines([(2025, 7), (2025, 8), (2025, 13)], TODAY)

    assert [r[0] for r in site.reads].count("get_all") == 2
    assert [r[0] for r in site.reads].count("calendar") == 1
    assert len(site.reads) == 3

    # A Closing key is absent (C-D4); a period not asked for is absent.
    assert sorted(out) == [(2025, 7), (2025, 8)]

    holidays = {date(2025, 8, 4)}
    for key, end in (((2025, 7), date(2025, 7, 31)), ((2025, 8), date(2025, 8, 31))):
        assert out[key] == MODEL.period_deadlines(site.rules, holidays, end, TODAY)
    # The real producer's numbers: P07 under the first rule, past the holiday.
    assert out[(2025, 7)]["tb"] == {"due": date(2025, 8, 8), "past": True, "text": "Due 2025-08-08"}
    # P08 under the second rule: ic 0 and journals blank are undeclared.
    assert out[(2025, 8)]["ic"]["text"] == MODEL.UNDECLARED
    assert out[(2025, 8)]["journals"]["text"] == MODEL.UNDECLARED
    assert out[(2025, 8)]["tb"]["due"] == date(2025, 9, 3)


def test_reads_are_the_close_settings_child_tables_unpaged():
    site = _declared_site()
    reader = _load(site)
    reader.period_deadlines([(2025, 7)], TODAY)
    reads = {r[1]: r[2] for r in site.reads if r[0] == "get_all"}
    assert set(reads) == {"Close Deadline Rule", "Close Holiday"}
    assert reads["Close Deadline Rule"]["filters"] == dict(PARENT_FILTERS, parentfield="deadline_rules")
    assert reads["Close Holiday"]["filters"] == dict(PARENT_FILTERS, parentfield="close_holidays")
    for kwargs in reads.values():
        assert kwargs["limit_page_length"] == 0
    rule_fields = set(reads["Close Deadline Rule"]["fields"])
    assert {"valid_from", *MODEL.WEEKDAYS, *MODEL.OFFSET_FIELD.values()} <= rule_fields
    assert "holiday_date" in reads["Close Holiday"]["fields"]


def test_no_rules_every_step_is_undeclared():
    site = _Site()
    reader = _load(site)
    out = reader.period_deadlines([(2025, 6), (2025, 7), (2025, 8)], TODAY)
    assert sorted(out) == [(2025, 6), (2025, 7), (2025, 8)]
    for steps in out.values():
        assert sorted(steps) == sorted(MODEL.STEPS)
        for step in MODEL.STEPS:
            assert steps[step] == {"due": None, "past": False, "text": "No due date declared"}


def test_string_dates_from_the_database_read_as_dates():
    site = _declared_site()
    site.rules = [dict(r, valid_from=r["valid_from"].isoformat()) for r in site.rules]
    site.holidays = [{"holiday_date": "2025-08-04"}]
    reader = _load(site)
    out = reader.period_deadlines([(2025, 7)], "2025-08-20")
    assert out[(2025, 7)]["tb"] == {"due": date(2025, 8, 8), "past": True, "text": "Due 2025-08-08"}


def test_period_with_no_end_date_throws_naming_it():
    site = _declared_site()
    site.periods[1]["end_date"] = None
    reader = _load(site)
    with pytest.raises(_Throw) as err:
        reader.period_deadlines([(2025, 7)], TODAY)
    assert str(err.value) == "FY2025 P07 has no end date: fix its row in EPM Fiscal Year."


def test_period_with_no_end_date_not_asked_for_does_not_throw():
    site = _declared_site()
    site.periods[0]["end_date"] = None
    reader = _load(site)
    assert sorted(reader.period_deadlines([(2025, 7)], TODAY)) == [(2025, 7)]


def test_stored_duplicate_rule_is_refused_with_the_sentence():
    site = _Site()
    site.rules = [_rule(date(2025, 1, 1)), _rule(date(2025, 1, 1), tb=2)]
    reader = _load(site)
    with pytest.raises(_Throw) as err:
        reader.period_deadlines([(2025, 7)], TODAY)
    assert str(err.value) == "Two deadline rules start on 2025-01-01: keep one."


def test_no_keys_reads_nothing():
    site = _declared_site()
    reader = _load(site)
    assert reader.period_deadlines([], TODAY) == {}
    assert site.reads == []


def test_as_payload_is_json_safe():
    site = _declared_site()
    reader = _load(site)
    out = reader.period_deadlines([(2025, 8)], TODAY)
    payload = reader.as_payload(out[(2025, 8)])
    assert payload["tb"] == {"due": "2025-09-03", "past": False, "text": "Due 2025-09-03"}
    assert payload["ic"] == {"due": None, "past": False, "text": "No due date declared"}
    assert sorted(payload) == sorted(MODEL.STEPS)
    assert json.loads(json.dumps(payload)) == payload


def test_as_payload_refuses_a_missing_or_unknown_step():
    site = _declared_site()
    reader = _load(site)
    steps = reader.period_deadlines([(2025, 8)], TODAY)[(2025, 8)]
    missing = {k: v for k, v in steps.items() if k != "signoff"}
    with pytest.raises((KeyError, ValueError)):
        reader.as_payload(missing)
    with pytest.raises(ValueError):
        reader.as_payload(dict(steps, review={"due": None, "past": False, "text": "x"}))


def test_reader_is_not_whitelisted():
    site = _Site()
    _load(site)
    assert site.whitelisted == []
    with open(READER_PY) as fh:
        assert "frappe.whitelist" not in fh.read()
