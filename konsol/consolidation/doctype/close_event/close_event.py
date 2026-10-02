"""Close Event — the close audit trail (konsol#305 T01b, #298 story 10.1,
#305-W2-1).

Append-only:
- an insert is accepted only inside ``writing()``, which only the writer
  ``konsol.close.close_event`` enters; a request cannot carry it;
- a saved row is never changed;
- no row is ever deleted, by anyone. ``on_trash`` runs even for
  Administrator and ``ignore_permissions``, so this refusal covers them too
  (E10-P2, decision #305-W2-6, Deepak Pai 2 Oct 2026). Removing an event
  takes raw SQL by a database admin: immutable at app level, not
  tamper-proof.
- ``frappe.delete_doc(..., ignore_on_trash=True)`` skips ``on_trash``
  (apps/frappe/frappe/model/delete_doc.py:125-128), so ``after_delete`` also
  refuses, unconditionally, with the same sentence (konsol#305 T01c,
  #305-W2-6). ``after_delete`` runs after the row is already gone from the
  table, so this refusal works only by making the request raise, which rolls
  the transaction back and restores the row; it is not a second check before
  the delete happens. A script that catches the error and commits anyway
  still deletes the row, and so does raw SQL by a database admin: that gap
  is recorded and accepted (W2-P2), not closed by this row.

The writer is held in a context variable, as assertion_run.py does for its
writers. This module imports nothing from ``konsol.close``, so the writer can
import it without a cycle.
"""
import contextlib
import contextvars

import frappe
from frappe.model.document import Document

_writer = contextvars.ContextVar("close_event_writer", default=False)


@contextlib.contextmanager
def writing():
    """Mark inserts inside the block as made by the audit trail's writer."""
    token = _writer.set(True)
    try:
        yield
    finally:
        _writer.reset(token)


def active():
    """True inside ``writing()``."""
    return _writer.get()


class CloseEvent(Document):

    def before_insert(self):
        if not active():
            frappe.throw(
                "A Close Event is written only by konsol.close.close_event (the audit trail's "
                "one writer); it cannot be created by hand.", frappe.PermissionError)

    def validate(self):
        if not self.is_new():
            frappe.throw("A Close Event cannot be changed; the audit trail is append-only.",
                         frappe.PermissionError)

    def on_trash(self):
        frappe.throw("A Close Event cannot be deleted; the audit trail is append-only.",
                     frappe.PermissionError)

    def after_delete(self):
        frappe.throw("A Close Event cannot be deleted; the audit trail is append-only.",
                     frappe.PermissionError)
