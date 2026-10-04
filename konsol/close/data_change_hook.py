"""The data-change hook: every submit and cancel of a NUMBER_DRIVING
approval doctype calls ``signoff_gate.record_data_change`` for each period
``data_change_model.changed_periods`` names (konsol#305 S42; #305-W4-4 4c,
AMENDED 4 Oct — Deepak "all ★", #305 issuecomment-5978983396: all 7
``close_policy_model.APPROVAL_DOCTYPES``, IC Balance included).

Wired in ``hooks.py`` as ``doc_events["*"]["on_submit"]`` and, second (after
``cancel_event.record``, so its ``approval_cancelled`` event precedes the
void this hook may write), ``doc_events["*"]["on_cancel"]`` (Frappe runs a
list of handlers per event, in order: ``frappe.append_hook``).

A Consolidation Journal's Reverse needs no separate wiring: this codebase
already records it as that doctype's own ``on_cancel`` ("submit is the
approval, cancel the reversal", consolidation_journal.py's class
docstring), so the generic ``on_cancel`` below covers it like any other
cancel, with the same "<doctype> <name> cancelled" text
(``data_change_model.change_text``, which knows only "approved" and
"cancelled" — no third "reversed" action is needed).

Why a hook and not ``approval_api.approve``: a Desk submit, a workflow
Approve (``apply_workflow`` ends in ``doc.submit()``) and a Business
Combination's derived Ownership Period submit never reach ``approve``
(self_approval.py's docstring); every one runs ``on_submit``.

Every non-NUMBER_DRIVING doctype returns before importing anything else
(self_approval.py's pattern): an unrelated doctype's submit or cancel never
loads ``close_event``, ``signoff_gate`` or ``fiscal_calendar``.

No commit and no try/except: both run in the submit's or cancel's own
transaction (R2b-3 / T02b rule), so a writer that raises stops the approval
or the cancel with it. A system submit/cancel (patch, install, migrate) is
recorded too (W4-E13; R01g precedent); skipped, and logged, only while the
Close Event table does not exist yet (a patch that runs before migrate's
schema sync creates it) — ``record_data_change``'s mark writes one.
"""
from konsol.close import data_change_model


def _record(doc, action):
    if doc.doctype not in data_change_model.NUMBER_DRIVING:
        return
    # Lazy: an unrelated doctype's submit/cancel never loads these (mirrors
    # self_approval.check's own "imported here, not at the top" pattern).
    import frappe

    from konsol import fiscal_calendar
    from konsol.close import close_event, signoff_gate

    if not frappe.db.table_exists("Close Event"):
        frappe.logger().warning(
            "konsol#305 R01g: Close Event table does not exist yet; %s %s's %s data "
            "change is not recorded live." % (doc.doctype, doc.name, action))
        return
    reverse = None
    if doc.doctype == "Consolidation Journal":
        reverse = (int(doc.get("reverse_fiscal_year") or 0),
                   int(doc.get("reverse_fiscal_period") or 0))
    period = close_event.period_of(doc)
    entity = close_event.entity_of(doc)
    text = data_change_model.change_text(doc.doctype, doc.name, action)
    period_rows = fiscal_calendar.fiscal_period_rows()
    for fiscal_year, fiscal_period in data_change_model.changed_periods(
            doc.doctype, period, period_rows, reverse=reverse):
        signoff_gate.record_data_change(
            fiscal_year, fiscal_period, text, frappe.session.user, entity=entity)


def on_submit(doc, method=None):
    _record(doc, "approved")


def on_cancel(doc, method=None):
    _record(doc, "cancelled")
