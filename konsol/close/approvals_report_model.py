"""Approvals Desk report, pure: ``approvals_api.get_queue``'s payload as report rows.

``rows(payload)`` maps the queue (``approvals_api.queue_for``: ``{items,
sent_back, counts, hidden, self_approval, can_approve, waiting}``) to the
rows of the Desk Script Report "Approvals"
(konsol/consolidation/report/approvals). Nothing here decides who may
approve: each row carries the item's own ``approve`` mode and message,
computed by ``rates_model.approve_mode`` on the server, and the report's
buttons post to ``approval_api.approve`` / ``reject``, whose hooks decide
again. The Close app's Approvals screen (close-ui/src/approvals.js) reads
the same payload the same way:

- ``direct`` (Approve at once) and ``reason`` (Approve with the
  self-approval reason, labelled with the server's own sentence) can be
  acted on here (``can_act`` 1); so can Reject, which always needs a reason.
- ``refused`` and ``not_approver`` show the server's sentence and cannot be
  acted on.
- Business Combination and Business Disposal (``inline`` false) are
  approved in their Desk form only: no action here, whatever their mode.
- A sent-back item follows the waiting ones, carries who sent it back, when
  and why, and cannot be acted on: the preparer acts next.

An unknown approve mode, or a payload without ``waiting``/``hidden``, raises
ValueError: a contract break with the server is never read as "nothing".

Pure: no frappe and no konsol imports.
"""
import importlib.util as _importlib_util
import os as _os
from urllib.parse import quote as _quote


def _load_period_name():
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name_for_approvals_report", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()

WAITING = "Waiting"
SENT_BACK = "Sent back"

#: approve mode -> can it be acted on here (mirrors approvals.js ``canAct``:
#: rates.js APPROVE_KIND ``button``/``reason``).
_ACTIONABLE = {"direct": True, "reason": True, "refused": False, "not_approver": False}


def desk_path(doctype, name):
    """The Desk form of ``doctype`` ``name``: ``/desk/<doctype-slug>/<name>``
    (frappe.utils.get_url_to_form's path, quoted the same way)."""
    return "/desk/%s/%s" % (_quote(doctype.lower().replace(" ", "-")), _quote(str(name), safe=""))


def _since(value):
    """An ISO datetime (``2026-10-01T09:00:00+05:30``, the site's own zone)
    as the Datetime cell ``2026-10-01 09:00:00``; None stays None."""
    if not value:
        return None
    return str(value)[:19].replace("T", " ")


def _period(item):
    fy, fp = item.get("fiscal_year"), item.get("fiscal_period")
    if fy is None or fp is None:
        return None
    return period_name(fy, fp)


def _base(item, status):
    return {
        "status": status,
        "doctype": item["doctype"],
        "name": item["name"],
        "desk": desk_path(item["doctype"], item["name"]),
        "kind_label": item["kind_label"],
        "title": item["title"],
        "detail": item.get("detail"),
        "entity": item.get("entity"),
        "period": _period(item),
        "amount": item.get("total_debit"),
        "currency": item.get("currency") if item.get("total_debit") is not None else None,
        "preparer": item.get("preparer"),
        "waiting_since": _since(item.get("created")),
    }


def _waiting_row(item):
    row = _base(item, WAITING)
    approve = item["approve"]
    mode = approve["mode"]
    if mode not in _ACTIONABLE:
        raise ValueError("Unknown approve mode %r on %s %s." % (mode, item["doctype"], item["name"]))
    message = approve.get("message")
    if not item.get("inline"):
        row.update(approve_mode="desk", approve_message=None, can_act=0,
                   action="Approve or reject in the %s form" % item["doctype"])
        return row
    if mode == "reason" and not message:
        # approvals.js throws here too (U12): never an invented label.
        raise ValueError("A \"reason\" approve item needs the server's own message (%s %s)."
                         % (item["doctype"], item["name"]))
    if mode == "direct":
        action = "Approve or reject"
    elif mode == "reason":
        action = "Approve with a reason: %s" % message
    else:
        action = message or ""
    row.update(approve_mode=mode, approve_message=message, can_act=int(_ACTIONABLE[mode]),
               action=action)
    return row


def _sent_back_row(item):
    row = _base(item, SENT_BACK)
    rejection = item.get("rejection") or {}
    row.update(approve_mode="sent_back", approve_message=None, can_act=0,
               action="Sent back by %s on %s: %s" % (
                   rejection.get("actor"), (rejection.get("at") or "")[:10], rejection.get("reason")))
    return row


def rows(payload):
    """Waiting items in the queue's own order (oldest first), then the sent-back ones."""
    return ([_waiting_row(item) for item in payload.get("items") or ()]
            + [_sent_back_row(item) for item in payload.get("sent_back") or ()])


def message(payload):
    """The report's header: approvals.js ``queueView``'s header, scope note
    and policy line, as plain text lines joined by ``<br>``."""
    waiting = payload.get("waiting")
    if not isinstance(waiting, dict) or not isinstance(waiting.get("count"), int):
        raise ValueError("The approvals queue carries no waiting count ({count, oldest}).")
    hidden = payload.get("hidden")
    if not isinstance(hidden, int):
        raise ValueError("The approvals queue carries no hidden count.")
    count = waiting["count"]
    if count:
        line = "%d waiting for you" % count
        if waiting.get("oldest"):
            line += " · oldest since %s" % str(waiting["oldest"])[:10]
    else:
        line = "Nothing waiting for you"
    lines = [line]
    if hidden > 0:
        lines.append("%d awaiting outside your scope" % hidden)
    if payload.get("can_approve") and payload.get("self_approval"):
        lines.append("Self-approval policy: %s" % payload["self_approval"])
    return "<br>".join(lines)


def columns():
    return [
        {"fieldname": "status", "label": "Status", "fieldtype": "Data", "width": 90},
        {"fieldname": "kind_label", "label": "Type", "fieldtype": "Data", "width": 200},
        {"fieldname": "doctype", "label": "Document Type", "fieldtype": "Link", "options": "DocType",
         "width": 160},
        {"fieldname": "name", "label": "Document", "fieldtype": "Dynamic Link", "options": "doctype",
         "width": 150},
        {"fieldname": "title", "label": "Title", "fieldtype": "Data", "width": 200},
        {"fieldname": "detail", "label": "Summary", "fieldtype": "Data", "width": 260},
        {"fieldname": "entity", "label": "Entity", "fieldtype": "Data", "width": 90},
        {"fieldname": "period", "label": "Period", "fieldtype": "Data", "width": 100},
        {"fieldname": "amount", "label": "Amount", "fieldtype": "Currency", "options": "currency",
         "width": 120},
        {"fieldname": "currency", "label": "Currency", "fieldtype": "Data", "width": 80},
        {"fieldname": "preparer", "label": "Prepared By", "fieldtype": "Data", "width": 160},
        {"fieldname": "waiting_since", "label": "Waiting Since", "fieldtype": "Datetime", "width": 160},
        {"fieldname": "action", "label": "Your Action", "fieldtype": "Data", "width": 320},
    ]
