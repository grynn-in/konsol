"""konsol#305 story 2.4: the one deadline reader (D55). Not whitelisted.

``period_deadlines(keys, today)`` reads the Close Settings deadline rules and
holidays ONCE (two ``get_all`` reads of the child tables) and the fiscal
calendar once, however many periods are asked for, and returns
``{(fiscal_year, fiscal_period): deadline_model.period_deadlines(...)}``.

- Only Regular periods carry deadlines (C-D4): a non-Regular key is absent,
  as is a key the calendar does not hold.
- No rule declared → every step reads "No due date declared" (deadline_model);
  nothing is guessed.
- A period asked for with no end date throws, naming it (the mywork_api A53
  precedent): a configuration problem, never today's date.
- Stored data the save would have refused (two rules on one Valid From), or a
  due date that cannot be computed, throws deadline_model's sentence.
- ``as_payload(steps)`` is the JSON-safe form of one period's steps: ``due``
  as an ISO string or None, plus ``past`` and ``text``.

"Overdue" (past AND the step still open) is decided by each caller (C-D4).
Callers import this module lazily (C-X1).
"""
import datetime as _datetime
import importlib.util as _importlib_util
import os as _os

import frappe

from konsol import fiscal_calendar


def _load_sibling(name):
    """A pure sibling in konsol/close loaded by path, as close_settings.py
    loads deadline_model and period_name: reachable under the host tests'
    stub ``konsol.close`` packages."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), name + ".py")
    spec = _importlib_util.spec_from_file_location("konsol_close_deadlines_" + name, path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deadline_model = _load_sibling("deadline_model")
period_name = _load_sibling("period_name").period_name

REGULAR = "Regular"
SETTINGS = "Close Settings"
RULE_DOCTYPE = "Close Deadline Rule"
HOLIDAY_DOCTYPE = "Close Holiday"
RULE_FIELDS = ["valid_from", *deadline_model.WEEKDAYS,
               *(deadline_model.OFFSET_FIELD[step] for step in deadline_model.STEPS)]


def _as_date(value):
    """A Date value as a ``datetime.date`` (close_settings.py ``_as_date``):
    the database may hand back a date, a datetime or an ISO string. Blank
    passes through."""
    if isinstance(value, _datetime.datetime):
        return value.date()
    if isinstance(value, str) and value:
        return _datetime.date.fromisoformat(value[:10])
    return value


def _child_rows(doctype, parentfield, fields):
    return frappe.get_all(
        doctype,
        filters={"parent": SETTINGS, "parenttype": SETTINGS, "parentfield": parentfield},
        fields=fields,
        order_by="idx asc",
        limit_page_length=0,
    )


def period_deadlines(keys, today):
    """``{(fy, fp): {step: {"due": date|None, "past": bool, "text": str}}}``
    for the Regular periods among ``keys``. Three reads in all."""
    wanted = {(int(fy), int(fp)) for fy, fp in keys}
    if not wanted:
        return {}
    today = _as_date(today)
    rules = []
    for row in _child_rows(RULE_DOCTYPE, "deadline_rules", RULE_FIELDS):
        rule = dict(row)
        rule["valid_from"] = _as_date(rule.get("valid_from"))
        rules.append(rule)
    holidays = {_as_date(row.get("holiday_date"))
                for row in _child_rows(HOLIDAY_DOCTYPE, "close_holidays", ["holiday_date"])}

    out = {}
    for row in fiscal_calendar.fiscal_period_rows():
        key = (int(row["fiscal_year"]), int(row["fiscal_period"]))
        if key not in wanted or row.get("period_type") != REGULAR:
            continue
        end_date = _as_date(row.get("end_date"))
        if not end_date:
            frappe.throw("%s has no end date: fix its row in EPM Fiscal Year." % period_name(*key))
        try:
            out[key] = deadline_model.period_deadlines(rules, holidays, end_date, today)
        except ValueError as err:
            frappe.throw(str(err))
    return out


def as_payload(steps):
    """One period's steps, JSON-safe. A missing or unknown step raises."""
    unknown = sorted(set(steps) - set(deadline_model.STEPS))
    if unknown:
        raise ValueError("Unknown deadline step(s): %s" % ", ".join(unknown))
    out = {}
    for step in deadline_model.STEPS:
        value = steps[step]
        due = value["due"]
        out[step] = {"due": due.isoformat() if due is not None else None,
                     "past": bool(value["past"]), "text": value["text"]}
    return out
