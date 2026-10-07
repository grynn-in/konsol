"""The one frappe-bound reader of an ownership change (konsol#305 O54;
story 4.2; C-O4). Not whitelisted: the preview, the save, the draft list and
both approval views call it, and ``ownership_change_model`` decides.

Decisions: #305-4.2-1 (the effect is structural, never an amount);
#305-Q1-1 (Deepak Pai, 7 Oct: approval end-dates the predecessor, a cancel
restores it, a change between two periods is refused); wireframe-4.2.md as
drawn.

- ``context(consolidation_group, entity, fiscal_year, fiscal_period)`` reads
  the node's submitted and draft Ownership Periods (one ``get_all``), the
  fiscal calendar once, and ``signoff_gate.latest_signed_runs()`` keys once.
  It returns ``ownership_change_model.problems`` and ``effect``'s inputs:
  ``entity``, ``effective_date`` (the period's start, C-O2), ``period``,
  ``current``, ``later_exists`` (None or the earliest later submitted
  start), ``pending_exists`` (None or the draft names), ``period_rows`` and
  ``signed_keys``. ``change(ctx, pct, method)`` builds the model's change.
- ``signed_keys`` (O64) maps each signed period's key, in key order, to its
  signature ``{"run", "signed_on", "signed_by_name"}``. R52f (review S5):
  only the signed periods an approval of the change will mark, i.e. those
  inside ``signoff_gate.periods_marked_from(<the change's first period>)``
  (``context``: the period asked; ``effect_for``: the draft's period by
  ``close_event.period_of``'s rule), so the re-sign list is exactly what
  ``record_data_change`` marks. The signatures:
  ``latest_signed_runs`` is read once with ``signed_off_by`` and
  ``signed_off_at``, and the signers' full names in one User read (none when
  nothing is signed). A signer with no full name, no User or a blank
  ``signed_off_by`` gives ``signed_by_name`` None, never the user id: the
  model raises naming the run when it lists that period. Iterating the
  mapping gives the keys, so the callers that pass it on are unchanged.
- ``effect_for(doc)`` is the effect of a saved draft, read from the period
  its ``supersedes`` names. A draft without ``supersedes`` (a Desk draft)
  has no effect: None, never a guessed before/after.

Nothing here guesses: two submitted periods covering the start, a period the
calendar does not hold, or a ``supersedes`` that is not a submitted period
raise ValueError. A group node (blank entity) is matched with
``["is", "not set"]`` (ownership_period.py ``_BLANK``) and named by its group.

``signoff_gate`` is imported inside the call (C-X1).
"""
import datetime as _datetime
import importlib.util as _importlib_util
import os as _os

import frappe

from konsol import fiscal_calendar


def _load_sibling(name):
    """A pure sibling in konsol/close loaded by path, so stub ``konsol.close``
    packages in the host tests never need it."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), name + ".py")
    spec = _importlib_util.spec_from_file_location("konsol_close_ownership_change_" + name, path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


model = _load_sibling("ownership_change_model")
period_name = _load_sibling("period_name").period_name

DOCTYPE = "Ownership Period"
_BLANK = ["is", "not set"]
_FIELDS = ["name", "effective_date", "end_date", "ownership_pct", "consolidation_method",
           "docstatus"]


def _iso(value):
    """An ISO date of a date, datetime or string; blank gives None."""
    if value in (None, ""):
        return None
    if isinstance(value, (_datetime.date, _datetime.datetime)):
        return value.isoformat()[:10]
    return str(value)[:10]


def _as_current(row):
    """The model's ``current``: JSON-safe, dates as ISO strings."""
    return {"name": row["name"], "effective_date": _iso(row["effective_date"]),
            "end_date": _iso(row["end_date"]), "ownership_pct": float(row["ownership_pct"]),
            "consolidation_method": row["consolidation_method"]}


def _node_periods(consolidation_group, entity):
    return frappe.get_all(
        DOCTYPE,
        filters={"consolidation_group": consolidation_group,
                 "data_area_id": entity or _BLANK,
                 "docstatus": ["in", [0, 1]]},
        fields=_FIELDS, order_by="effective_date asc", limit_page_length=0)


def _period_of(effective_date, period_rows):
    """The ``(fiscal_year, fiscal_period)`` an approval of a change starting
    on ``effective_date`` belongs to, by ``close_event.period_of``'s rule:
    the first declared period starting on or after it, a Regular row winning
    a start-date tie, then the lower year. None when no declared period
    starts on or after it (the model then refuses the change)."""
    eff = _iso(effective_date)
    if eff is None:
        return None
    starts = [r for r in period_rows
              if _iso(r.get("start_date")) is not None and _iso(r["start_date"]) >= eff]
    if not starts:
        return None
    row = min(starts, key=lambda r: (_iso(r["start_date"]), r.get("period_type") != "Regular",
                                     int(r["fiscal_year"]), int(r["fiscal_period"])))
    return (int(row["fiscal_year"]), int(row["fiscal_period"]))


def _signed_keys(first_key):
    """{(fy, fp): {"run", "signed_on", "signed_by_name"}}, in key order
    (O64), of each signed period an approval of a change in ``first_key``
    will mark Re-sign Needed: only the keys inside
    ``signoff_gate.periods_marked_from(*first_key)`` (R52f, review S5), the
    rule ``record_data_change`` marks by. ``first_key`` None (no declared
    period holds the change) keeps none. One signed-run read and one User
    read; the rule is read only when something is signed."""
    from konsol.close import signoff_gate

    runs = {(int(fy), int(fp)): r for (fy, fp), r in
            signoff_gate.latest_signed_runs(("signed_off_by", "signed_off_at")).items()}
    if runs:
        marked = (set(signoff_gate.periods_marked_from(*first_key))
                  if first_key is not None else set())
        runs = {k: r for k, r in runs.items() if k in marked}
    signers = sorted({r.get("signed_off_by") for r in runs.values() if r.get("signed_off_by")})
    names = {}
    if signers:
        names = {u["name"]: u.get("full_name") or None for u in frappe.get_all(
            "User", filters={"name": ["in", signers]}, fields=["name", "full_name"],
            limit_page_length=0)}
    out = {}
    for key in sorted(runs):
        run = runs[key]
        by = run.get("signed_off_by")
        out[key] = {"run": run.get("name"), "signed_on": _iso(run.get("signed_off_at")),
                    "signed_by_name": names.get(by) if by else None}
    return out


def context(consolidation_group, entity, fiscal_year, fiscal_period):
    key = (int(fiscal_year), int(fiscal_period))
    period_rows = fiscal_calendar.fiscal_period_rows()
    period = next((r for r in period_rows
                   if (int(r["fiscal_year"]), int(r["fiscal_period"])) == key), None)
    if period is None:
        raise ValueError("%s is not in the fiscal calendar." % period_name(*key))
    start = _iso(period.get("start_date"))
    if start is None:
        raise ValueError("%s has no start date in the fiscal calendar." % period_name(*key))

    rows = _node_periods(consolidation_group, entity)
    submitted = [r for r in rows if int(r["docstatus"] or 0) == 1]
    covering = [r for r in submitted
                if _iso(r["effective_date"]) <= start
                and (_iso(r["end_date"]) is None or _iso(r["end_date"]) >= start)]
    if len(covering) > 1:
        raise ValueError(
            "%s has %d approved Ownership Periods covering %s (%s): the data is corrupt; "
            "cancel the wrong one in Desk." % (entity or consolidation_group, len(covering),
                                                start, ", ".join(sorted(r["name"] for r in covering))))
    later = sorted(_iso(r["effective_date"]) for r in submitted
                   if _iso(r["effective_date"]) > start)
    drafts = sorted(r["name"] for r in rows if int(r["docstatus"] or 0) == 0)
    return {
        "entity": entity or consolidation_group,
        "effective_date": start,
        "period": period,
        "current": _as_current(covering[0]) if covering else None,
        "later_exists": later[0] if later else None,
        "pending_exists": ", ".join(drafts) if drafts else None,
        "period_rows": period_rows,
        "signed_keys": _signed_keys(key),
    }


def change(ctx, ownership_pct, consolidation_method):
    """The model's ``change`` for the context's node and period."""
    return {"entity": ctx["entity"], "effective_date": ctx["effective_date"],
            "ownership_pct": ownership_pct, "consolidation_method": consolidation_method}


def effect_for(doc):
    """The structural effect of the saved draft ``doc`` if it is approved.

    None for a draft without ``supersedes``. Raises ValueError for a document
    that is not a draft, or a ``supersedes`` that is not a submitted period;
    ``ownership_change_model.effect`` raises for a draft it cannot describe."""
    if int(doc.get("docstatus") or 0) != 0:
        raise ValueError("%s is not a draft: the effect is previewed only before approval."
                         % doc.get("name"))
    supersedes = doc.get("supersedes")
    if not supersedes:
        return None
    found = frappe.get_all(DOCTYPE, filters={"name": supersedes}, fields=_FIELDS,
                           limit_page_length=0)
    if not found or int(found[0]["docstatus"] or 0) != 1:
        raise ValueError("%s supersedes %s, which is not an approved Ownership Period."
                         % (doc.get("name"), supersedes))
    the_change = {"entity": doc.get("data_area_id") or doc.get("consolidation_group"),
                  "effective_date": _iso(doc.get("effective_date")),
                  "ownership_pct": doc.get("ownership_pct"),
                  "consolidation_method": doc.get("consolidation_method")}
    period_rows = fiscal_calendar.fiscal_period_rows()
    return model.effect(the_change, _as_current(found[0]), period_rows,
                        _signed_keys(_period_of(the_change["effective_date"], period_rows)))
