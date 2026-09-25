"""Sign-off endpoints for the close app (konsol#305 A30, A49, A32; stories 9.1, 9.2, 9.3, 9.5).

``get_signoff(fiscal_year, fiscal_period)`` (GET, every close role; the
Viewer reads the summary, R6) reads the site and passes it through the pure
A21 ``signoff_model.summary``:

- the run: ``assertion_run.latest_close_run`` plus its ``warned`` count, read
  separately (latest_close_run does not return it, and without it the
  acknowledgement total is unknown); the warned names come from
  ``_warned_assertion_names`` when the count is non-zero;
- the gates: ``signoff_gate.sign_off_problems`` (A17). It refuses a
  non-Regular period (P5);
- on-behalf uploads: the period's submitted Trial Balance Submissions, with
  ``uploaded_on_behalf`` "Yes"/"No"/blank (A18) mapped to 1/0/None (blank is
  unknown: uploaded before it was recorded, Problems 16). Any other value is
  refused, not guessed;
- exceptions: the period's submitted TB Exceptions (A08);
- covers notes: ``signoff_model.covers_notes`` over the fiscal year's
  submitted TBs and exceptions up to the period, WITH ``frequencies`` so a
  quarterly entity's quarter-end TB carries its quarter (A41);
- previous periods: the Regular period states from the first close period up
  to the one before the target. With the first close period undeclared there
  is no "previous" to name: the list is empty and the gap says why.

It adds ``can_sign`` (write on Assertion Run, the test ``sign_off_close``
applies), ``can_override`` (``OVERRIDE_ROLES``), and (A49) ``period_status``
(the effective status from ``fiscal_period_rows``) with ``closed_by`` and
``closed_on`` from the period row, so a reloaded closed period shows Closed.

Entity scope is a security boundary. A caller restricted by
``entity_permissions.allowed_entity_codes`` sees only their entities' on-behalf
labels, exceptions and covers notes. Gate entries that list entities are cut
to those entities; the others are counted (``hidden``), never named, and the
message is rebuilt from what may be shown. The gates still block: a hidden
entity is counted, not dropped. Read-only.

``sign(fiscal_year, fiscal_period, acknowledgement, override_reason)`` (POST,
Close Lead) signs the period's latest terminal run through
``assertion_run.sign_off_close``, which holds the write check, the gates (A22)
and the Amber/Red rules. ``sign`` never writes a sign-off field itself.

``declare_tb_exception(entity, fiscal_year, fiscal_period, reason)`` (POST,
Close Lead; A33, stories 9.1, 9.2) inserts and submits a ``TB Exception``
through the document's own ``insert`` and ``submit``, so the A08 controller
and Frappe's permission checks run: ``declared_by`` is set there to the
submitting user, and a duplicate, a closed period, a group entity or an
entity-period with a submitted trial balance is refused there. The endpoint
takes no ``declared_by`` and sends none. Only a blank reason is refused before
insert, so nothing is written for it.
"""
import datetime

import frappe

from konsol import fiscal_calendar
from konsol.close import period_model, signoff_gate, signoff_model
from konsol.close.timefmt import zoned_iso
from konsol.consolidation.doctype.assertion_run.assertion_run import (
    OVERRIDE_ROLES,
    _warned_assertion_names,
    latest_close_run,
    sign_off_close,
)
from konsol.entity_permissions import allowed_entity_codes
from konsol.period_status import PeriodNotDeclared

REGULAR = "Regular"

#: A18's Select, read for A21's summary: 1 labelled, 0 not, None unknown.
_ON_BEHALF = {"Yes": 1, "No": 0, "": None, None: None}


def _period(fiscal_year, fiscal_period):
    try:
        return int(fiscal_year), int(fiscal_period)
    except (TypeError, ValueError):
        frappe.throw("FY%s P%s is not a period: pass the fiscal year and period as whole numbers."
                     % (fiscal_year, fiscal_period))


def _declared_row(rows, key):
    for row in rows:
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            return row
    frappe.throw("FY%d P%02d is not declared: create it in EPM Fiscal Year." % key,
                 PeriodNotDeclared)


def _iso(value):
    if isinstance(value, datetime.datetime):
        return zoned_iso(value, frappe.utils.get_system_timezone())
    if isinstance(value, datetime.date):
        return value.isoformat()
    return None if value in (None, "") else str(value)


def _run(key):
    """The latest terminal run with its ``warned`` count, or None."""
    run = latest_close_run(*key)
    if not run:
        return None
    run = dict(run)
    run["warned"] = frappe.db.get_value("Assertion Run", run["name"], "warned")
    return run


def _frequencies(key):
    """``{entity: reporting_frequency}`` for the period's in-scope entities."""
    scope = signoff_gate.in_scope_entities(*key)
    if not scope:
        return {}
    return {
        e["name"]: e["reporting_frequency"] or ""
        for e in frappe.get_all("Entity", filters={"name": ["in", scope]},
                                fields=["name", "reporting_frequency"], limit_page_length=0)
    }


def _year_records(doctype, key, fields):
    """Submitted records of the fiscal year up to and including the period."""
    return frappe.get_all(
        doctype,
        filters={"fiscal_year": key[0], "fiscal_period": ["<=", key[1]], "docstatus": 1},
        fields=["data_area_id", "fiscal_year", "fiscal_period", "docstatus"] + fields,
        limit_page_length=0,
    )


def _on_behalf_flag(record):
    value = record.get("uploaded_on_behalf")
    if value not in _ON_BEHALF:
        frappe.throw("Trial balance %s has Uploaded on Behalf %r; expected Yes, No or blank."
                     % (record.get("name") or record["data_area_id"], value))
    return _ON_BEHALF[value]


def _previous(rows, key, first):
    if first is None:
        return []
    regular = [r for r in rows if r.get("period_type") == REGULAR]
    # _latest_runs: one query for every period's latest terminal run (A17).
    states = period_model.period_states(regular, signoff_gate._latest_runs(), first, ())["states"]
    return [s for s in states if first <= tuple(s["key"]) < key]


def _names(visible, hidden):
    parts = list(visible)
    if hidden:
        parts.append("%d %s outside your scope" % (hidden, "entity" if hidden == 1 else "entities"))
    return ", ".join(parts)


def _scoped_gap(gap, allowed, fiscal_year):
    entities = gap.get("entities")
    if entities is None:
        return gap
    mine = [e for e in entities if e in allowed]
    hidden = len(entities) - len(mine)
    if not hidden:
        return gap
    names = _names(mine, hidden)
    if gap["code"] == signoff_model.FREQUENCY_UNDECLARED:
        message = ("Set the Reporting Frequency (Monthly or Quarterly) on %s before signing off."
                   % names)
    elif gap["code"] == signoff_model.QUARTER_UNDECLARED:
        message = ("Declare the Quarter of every Regular period of FY%d in the fiscal year "
                   "before signing off (quarterly: %s)." % (fiscal_year, names))
    else:
        message = "%s (%s)." % (gap["code"], names)
    return dict(gap, entities=mine, hidden=hidden, message=message)


def _scoped(problems, allowed, key):
    """``problems`` with other entities' codes replaced by a count."""
    if allowed is None:
        return problems
    gaps = [_scoped_gap(g, allowed, key[0]) for g in problems["config_gaps"]]
    completeness = problems["completeness"]
    if completeness:
        mine = [e for e in completeness["missing"] if e in allowed]
        hidden = len(completeness["missing"]) - len(mine)
        if hidden:
            count = len(mine) + hidden
            completeness = dict(
                completeness, missing=mine, hidden=hidden,
                message="No trial balance from %s. Upload %s or declare an exception." % (
                    _names(mine, hidden), "it" if count == 1 else "them"),
            )
    return {"config_gaps": gaps, "order": problems["order"], "completeness": completeness}


def _closed(key):
    return frappe.db.get_value(
        "EPM Fiscal Year Period",
        {"parent": str(key[0]), "parenttype": "EPM Fiscal Year", "parentfield": "periods",
         "fiscal_period": key[1]},
        ["closed_by", "closed_on"], as_dict=True,
    ) or {}


@frappe.whitelist(methods=["GET"])
def get_signoff(fiscal_year, fiscal_period):
    """The period's sign-off summary (A21's shape) plus ``can_sign``,
    ``can_override``, ``period_status``, ``closed_by`` and ``closed_on``.

    Read-only. Refuses an undeclared period (PeriodNotDeclared) and a
    non-Regular one (only Regular periods are signed off, P5).
    """
    # A literal: the endpoint contract test reads it.
    frappe.only_for(("EPM Admin", "EPM Analyst", "Entity Accountant", "EPM User", "System Manager"))
    key = _period(fiscal_year, fiscal_period)
    rows = fiscal_calendar.fiscal_period_rows()
    row = _declared_row(rows, key)
    problems = signoff_gate.sign_off_problems(*key)

    allowed = allowed_entity_codes()

    def visible(records):
        return [r for r in records if allowed is None or r["data_area_id"] in allowed]

    tbs = visible(_year_records("Trial Balance Submission", key,
                                ["name", "owner", "uploaded_on_behalf"]))
    exceptions = visible(_year_records("TB Exception", key, ["reason", "declared_by"]))
    in_period = lambda r: (int(r["fiscal_year"]), int(r["fiscal_period"])) == key  # noqa: E731

    run = _run(key)
    warned_names = _warned_assertion_names(run["name"]) if run and run.get("warned") else []
    on_behalf = [{"data_area_id": r["data_area_id"], "owner": r["owner"],
                  "uploaded_on_behalf": _on_behalf_flag(r)} for r in tbs if in_period(r)]
    covers = signoff_model.covers_notes(key, rows, tbs, exceptions,
                                        frequencies=_frequencies(key))
    roles = set(frappe.get_roles())
    can_override = bool(OVERRIDE_ROLES & roles)

    result = signoff_model.summary(
        run, warned_names, on_behalf,
        [r for r in exceptions if in_period(r)],
        covers,
        _previous(rows, key, signoff_gate._first_close()),
        _scoped(problems, allowed, key),
        can_override,
    )
    closed = _closed(key)
    result.update({
        "can_sign": bool(frappe.has_permission("Assertion Run", "write")),
        "can_override": can_override,
        "period_status": row["status"],
        "closed_by": closed.get("closed_by") or None,
        "closed_on": _iso(closed.get("closed_on")),
    })
    return result


@frappe.whitelist(methods=["POST"])
def sign(fiscal_year, fiscal_period, acknowledgement=None, override_reason=None):
    """Sign off the period's latest terminal run (A32, story 9.2).

    Everything that decides whether the signature lands is in
    ``sign_off_close``: write permission, the gates (A22), the Amber
    acknowledgement and the Red override. Its refusals pass through unchanged.
    """
    # A literal: the endpoint contract test reads it.
    frappe.only_for(("EPM Admin", "System Manager"))
    key = _period(fiscal_year, fiscal_period)
    row = _declared_row(fiscal_calendar.fiscal_period_rows(), key)
    run = latest_close_run(*key)
    if not run:
        frappe.throw("Run the checks for FY%d %s first." % (key[0], row["period_code"]))
    return sign_off_close(run["name"], override_reason=override_reason,
                          acknowledgement=acknowledgement)


@frappe.whitelist(methods=["POST"])
def declare_tb_exception(entity, fiscal_year, fiscal_period, reason):
    """Declare "no trial balance for ``entity`` in the period, because ``reason``"
    (A33). Returns the new TB Exception's name.

    Every rule is the A08 controller's; its refusals pass through unchanged.
    """
    # A literal: the endpoint contract test reads it.
    frappe.only_for(("EPM Admin", "System Manager"))
    key = _period(fiscal_year, fiscal_period)
    if not (reason or "").strip():
        frappe.throw("Give the reason %s has no trial balance for FY%d P%02d." % (
            entity, key[0], key[1]))
    doc = frappe.get_doc({
        "doctype": "TB Exception",
        "data_area_id": entity,
        "fiscal_year": key[0],
        "fiscal_period": key[1],
        "reason": reason,
    })
    doc.insert()
    doc.submit()
    return doc.name
