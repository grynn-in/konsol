"""The cancel event (konsol#305 T02d, #305-W2-8), wired as
``doc_events["*"]["on_cancel"]`` in hooks.py.

Every completed cancel of a ``close_policy_model.APPROVAL_DOCTYPES`` document
records one ``approval_cancelled`` Close Event: a GER, Ownership Period,
Historical Equity Rate or IC Balance cancel, a journal Reverse, and a Business
Combination or Business Disposal cancel.

Why ``on_cancel``: Frappe runs a doc_events hook after the controller's own
method of the same name (frappe/model/document.py ``Document.hook``), and
``on_cancel`` after ``before_cancel`` and the docstatus write. Every approval
doctype refuses a cancel in its ``before_cancel``, so a refused cancel never
reaches this hook and records nothing. Their ``on_cancel`` side effects are
after-commit syncs, so the event is in the cancel's own MariaDB transaction,
before anything leaves it. No commit and no try/except here: a writer that
raises stops the cancel, and a cancel refused later in the same save (Frappe's
back-link check) rolls the event back with it.

A Business Combination cancel cancels its derived Ownership Period under the
``from_business_combination`` flag, so it records two events, the BC's and the
OP's (``detail.exempt == "derived"``). A cancel exempt as ``"system"`` (a
patch, install or migrate) is not recorded live: the backfill (T06b) recovers
it from its Version (E10-P10). ``period_of`` refusing (no declared period,
#305-W2-5) refuses the cancel.
"""
from konsol.close import close_event, close_policy_model, self_approval


def record(doc, method=None):
    if doc.doctype not in close_policy_model.APPROVAL_DOCTYPES:
        return
    exempt = self_approval._exempt(doc)
    if exempt == "system":
        # E10-P10: not recorded live; the backfill recovers it from the Version.
        return
    fiscal_year, fiscal_period = close_event.period_of(doc)
    close_event.record(
        "approval_cancelled", fiscal_year, fiscal_period, doc.doctype, doc.name,
        entity=close_event.entity_of(doc),
        detail={"preparer": doc.owner, "exempt": exempt})
