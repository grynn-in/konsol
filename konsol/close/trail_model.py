"""Close Event trail model, pure (konsol#305 W2, #298 story 10.1; amended 2
Oct by #305-W2-8, #305-W2-9).

Turns a period's Close Events into the board's summary (signed off by and
when, the result, closed, locked, and the exception counts) and the events
newest first.

- ``ordered(events)``: the events newest first, by ``(at, name)`` descending.
- ``visible(events, allowed)``: scopes entity events to the caller's allowed
  entities, mirroring ``period_grid_model``'s cut
  (period_grid_model.py:251-253; E2-6). ``allowed is None`` keeps
  everything. Otherwise an event is kept when its ``entity`` is blank
  (group-level: rates, journals, period, year and sign-off events) or is in
  ``allowed``. Returns ``(events, hidden)``.
- ``summary(events)``: the board's summary. Call it on an already-scoped
  list (``visible()``'s first element), never on the raw events, so a count
  cannot reveal a hidden entity's activity beyond the ``hidden`` number
  ``visible()`` already gives.

An unknown ``kind`` raises ValueError naming it; it is never counted or
shown as another kind.

Imports only ``close_event_model`` (for ``KINDS``), loaded as a sibling
(mirrors fiscal_calendar.py:62-73 ``_load_sibling``). Imports no frappe.
"""
import importlib.util
import os

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_APP_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


close_event_model = _load_sibling("konsol_close_trail_close_event_model", "close/close_event_model.py")

_CLOSED_KINDS = ("period_closed", "year_closed")
_LOCKED_KINDS = ("period_locked", "year_locked")
_REOPENED_KINDS = ("period_reopened", "year_reopened")


def ordered(events):
    """``events`` newest first, by ``(at, name)`` descending; a tie on
    ``at`` is broken by ``name`` descending too."""
    return sorted(events, key=lambda e: (e["at"], e["name"]), reverse=True)


def visible(events, allowed):
    """Scope ``events`` to ``allowed`` entities. ``allowed is None`` keeps
    everything, with ``hidden`` 0. Otherwise an event is kept when its
    ``entity`` is blank (group-level) or is in ``allowed``. Returns
    ``(kept_events, hidden_count)``."""
    if allowed is None:
        return list(events), 0
    kept = [e for e in events if not e.get("entity") or e.get("entity") in allowed]
    hidden = len(events) - len(kept)
    return kept, hidden


def _latest(events, kinds):
    candidates = [e for e in events if e["kind"] in kinds]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e["at"])


def _span(events, close_kinds, reopen_kinds):
    """The latest of ``close_kinds``, as ``{"by", "at"}``, unless a later
    event in ``reopen_kinds`` follows it -- then None."""
    latest_close = _latest(events, close_kinds)
    if latest_close is None:
        return None
    latest_reopen = _latest(events, reopen_kinds)
    if latest_reopen is not None and latest_reopen["at"] > latest_close["at"]:
        return None
    return {"by": latest_close.get("actor"), "at": latest_close.get("at")}


def _signoff(events):
    latest_signed = _latest(events, ("signed_off",))
    if latest_signed is None:
        return {"state": "none"}
    # A void (a reopen, a data change) or a reject (#305-W5-1, story 9.4)
    # after the latest signature ends it; the later of the two is the state.
    latest_end = _latest(events, ("signoff_voided", "signoff_rejected"))
    if latest_end is not None and latest_end["at"] > latest_signed["at"]:
        return {
            "state": "voided" if latest_end["kind"] == "signoff_voided" else "rejected",
            "by": latest_end.get("actor"),
            "at": latest_end.get("at"),
            "reason": latest_end.get("reason"),
        }
    detail = latest_signed.get("detail") or {}
    return {
        "state": "signed",
        "by": latest_signed.get("actor"),
        "at": latest_signed.get("at"),
        "result": detail.get("signoff_status"),
        "run_status": detail.get("run_status"),
        "reason": latest_signed.get("reason"),
        "warnings": detail.get("warnings"),
    }


def summary(events):
    """The board's summary dict for ``events``: ``signoff``, ``closed``,
    ``locked`` and ``counts``. Raises ValueError naming an unknown
    ``kind``."""
    events = list(events)
    for event in events:
        if event["kind"] not in close_event_model.KINDS:
            raise ValueError("unknown Close Event kind %r." % (event["kind"],))

    counts = {
        "approvals": 0,
        "self_approvals": 0,
        "rejections": 0,
        "on_behalf_uploads": 0,
        "acknowledgements": 0,
        "overrides": 0,
        "reopenings": 0,
        "recovered": 0,
        "reasons_not_recorded": 0,
        "cancellations": 0,
    }
    for event in events:
        kind = event["kind"]
        detail = event.get("detail") or {}
        if kind in ("approved", "self_approved"):
            counts["approvals"] += 1
        if kind == "self_approved":
            counts["self_approvals"] += 1
        if kind == "rejected":
            counts["rejections"] += 1
        if kind == "tb_submitted" and detail.get("on_behalf") == "Yes":
            counts["on_behalf_uploads"] += 1
        if kind == "signed_off" and detail.get("signoff_status") == "Acknowledged":
            counts["acknowledgements"] += 1
        if kind == "signed_off" and detail.get("signoff_status") == "Overridden":
            counts["overrides"] += 1
        if kind in _REOPENED_KINDS:
            counts["reopenings"] += 1
        if event.get("source") == close_event_model.BACKFILL:
            counts["recovered"] += 1
        if detail.get("reason_not_recorded"):
            counts["reasons_not_recorded"] += 1
        if kind == "approval_cancelled":
            counts["cancellations"] += 1

    return {
        "signoff": _signoff(events),
        "closed": _span(events, _CLOSED_KINDS, _REOPENED_KINDS),
        "locked": _span(events, _LOCKED_KINDS, _REOPENED_KINDS),
        "counts": counts,
    }
