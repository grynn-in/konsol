"""Close Event rules, pure (konsol#305 W2, #298 story 10.1, #305-W2-1).

- ``KINDS``/``SOURCES``: the closed vocabulary a Close Event's ``kind`` and
  ``source`` fields are drawn from.
- ``event_problems``: the one rule every writer's event dict must meet before
  it may be inserted -- a shape check, not a permission check. ``[]`` means
  the event may be written.
- ``approval_kind``: "self_approved" vs "approved", shared by the live
  approval hook (T02b) and the backfill (T06a) so the rule exists once
  (E10-P3).
- ``detail_json``: the kind-specific facts serialised for the Code field.

Imports nothing from frappe or konsol.
"""
import json

LIVE = "live"
BACKFILL = "backfill"
SOURCES = (LIVE, BACKFILL)

KINDS = (
    "approved", "self_approved", "rejected", "approval_cancelled",
    "period_closed", "period_locked", "period_reopened",
    "year_closed", "year_locked", "year_reopened",
    "signed_off", "signoff_voided",
    "tb_submitted", "tb_cancelled",
    "tb_exception_declared", "tb_exception_cancelled",
)
YEAR_KINDS = ("year_closed", "year_locked", "year_reopened")
REASON_REQUIRED = (
    "self_approved", "rejected", "period_reopened", "year_reopened",
    "tb_exception_declared", "signoff_voided",
)
#: assertion_run.SIGNED_STATES that carry text (assertion_run.py:408-411, 593-620).
SIGNOFF_NEEDS_REASON = ("Acknowledged", "Overridden")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def event_problems(event):
    """Every reason ``event`` (a dict of the Close Event's field names, with
    ``detail`` a dict or None) may not be inserted, one sentence per problem
    naming the field. ``[]`` means the event may be written.
    """
    problems = []
    event = event or {}
    kind = event.get("kind")
    source = event.get("source")

    if kind not in KINDS:
        problems.append("kind %r is not one of the declared event kinds." % (kind,))
    if source not in SOURCES:
        problems.append("source %r is not one of %s." % (source, ", ".join(SOURCES)))

    fiscal_year = event.get("fiscal_year")
    if not (_is_int(fiscal_year) and fiscal_year > 0):
        problems.append("fiscal_year must be a positive integer.")

    fiscal_period = event.get("fiscal_period")
    if not (_is_int(fiscal_period) and fiscal_period >= 0):
        problems.append("fiscal_period must be a non-negative integer.")
    elif kind in YEAR_KINDS:
        if fiscal_period != 0:
            problems.append(
                "fiscal_period must be 0 for a year_* kind (it covers the whole year)."
            )
    elif kind in KINDS and fiscal_period == 0:
        problems.append("fiscal_period must not be 0 for a non-year kind.")

    if not event.get("actor"):
        problems.append("actor must not be blank.")

    if not event.get("at"):
        problems.append("at is required.")

    reference_doctype = event.get("reference_doctype")
    reference_name = event.get("reference_name")
    if bool(reference_doctype) != bool(reference_name):
        problems.append(
            "reference_doctype and reference_name must both be given, or neither."
        )

    detail = event.get("detail")
    if detail is not None and not isinstance(detail, dict):
        problems.append("detail must be a dict or None.")
    else:
        try:
            json.dumps(detail)
        except TypeError:
            problems.append("detail must be JSON-serialisable.")

    detail_dict = detail if isinstance(detail, dict) else {}
    if not event.get("reason"):
        needs_reason = kind in REASON_REQUIRED or (
            kind == "signed_off" and detail_dict.get("signoff_status") in SIGNOFF_NEEDS_REASON
        )
        if (
            needs_reason
            and source == BACKFILL
            and detail_dict.get("reason_not_recorded") is True
        ):
            needs_reason = False
        if needs_reason:
            problems.append("reason is required for %s." % kind)

    return problems


def approval_kind(preparers, approver):
    """"self_approved" when ``approver`` is in ``preparers`` (the document's
    owner, plus everyone who edited the draft; see
    close_policy_model.preparers), "approved" otherwise.

    ``preparers`` must support membership by item, not by substring: a plain
    ``str`` raises TypeError so a caller cannot pass a single id and get a
    silent substring match.
    """
    if isinstance(preparers, str):
        raise TypeError("preparers must be a set of ids, not a string")
    return "self_approved" if approver in preparers else "approved"


def detail_json(detail):
    """``detail`` serialised for the Code field, or None for a blank detail
    (None or ``{}``)."""
    if not detail:
        return None
    return json.dumps(detail, sort_keys=True)
