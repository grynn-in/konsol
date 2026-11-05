"""My Work Desk report, pure: ``mywork_api.get_my_work``'s payload as report rows.

``rows(payload)`` maps My work's items (``mywork_model``: setup gaps, period
items, the approvals item, sent-back drafts and sign-offs, IC fixes) to the
rows of the Desk Script Report "My Work"
(konsol/consolidation/report/my_work). The items, their kinds, titles and
order are the API's; nothing is recomputed. Rows are grouped blocking, then
to do, then waiting, keeping the API's own order (``mywork_model.rank``)
within each kind.

The Desk is the target UI, so each row's "Open" is a Desk path, never a
Close-app (``/close``) one:

- an item whose ``action`` is ``{"desk": path}`` (setup gaps, unowned
  trial balances, a sent-back IC Balance or deal) opens that path. A path
  outside ``/desk/`` raises: it is never shown;
- a sent-back draft (id ``sent-back:<doctype>:<name>``, one of the approval
  doctypes) opens its own Desk form, the nearest place to fix it;
- any other ``{"screen": ...}`` opens ``SCREEN_DESTINATIONS[screen]``,
  filtered to the item's period (and entity) when it has one. A screen
  declared ``None`` has no Desk counterpart and shows no link. An
  undeclared screen raises ValueError: never a guessed link.

Pure: no frappe and no konsol imports.
"""
from urllib.parse import quote as _quote
from urllib.parse import urlencode as _urlencode

KINDS = ("blocking", "todo", "waiting")
KIND_LABELS = {"blocking": "Blocking", "todo": "To do", "waiting": "Waiting"}

#: The 7 approval doctypes (close_policy_model.APPROVAL_DOCTYPES; literals,
#: since this module is pure; the test holds them equal).
APPROVAL_DOCTYPES = (
    "Consolidation Journal", "Business Combination", "Business Disposal", "Group Exchange Rate",
    "Ownership Period", "Historical Equity Rate", "IC Balance",
)

#: Close-app screen (mywork_api.SCREENS) -> ``(desk path, period-filtered)``,
#: or None where the Desk has no counterpart. Each is the Desk page where the
#: screen's work is done:
#: - trial-balances: the submissions list (an entity item adds data_area_id);
#: - rates: the group exchange rates (Rates missing is a missing group rate);
#: - checks and sign-off: the period's Assertion Runs (Run Suite and Sign Off
#:   are buttons on the run's form);
#: - numbers: the Consolidated Statement report;
#: - approvals: the Approvals report;
#: - adjustments, intercompany, audit-trail: the journals, IC balances and
#:   Close Events lists;
#: - period: the Period Status list;
#: - my-work: this report itself, so no link.
SCREEN_DESTINATIONS = {
    "my-work": None,
    "approvals": ("/desk/query-report/Approvals", False),
    "period": ("/desk/period-status", True),
    "trial-balances": ("/desk/trial-balance-submission", True),
    "intercompany": ("/desk/ic-balance", True),
    "rates": ("/desk/group-exchange-rate", True),
    "adjustments": ("/desk/consolidation-journal", True),
    "checks": ("/desk/assertion-run", True),
    "numbers": ("/desk/query-report/" + _quote("Consolidated Statement"), True),
    "sign-off": ("/desk/assertion-run", True),
    "audit-trail": ("/desk/close-event", True),
}

_SENT_BACK_PREFIX = "sent-back:"


def _form(doctype, name):
    return "/desk/%s/%s" % (_quote(doctype.lower().replace(" ", "-")), _quote(str(name), safe=""))


def _sent_back_form(item_id):
    """The Desk form for a sent-back draft's id, or None for any other id
    (the sign-off's ``sent-back:signoff:<period>`` names no document)."""
    if not item_id.startswith(_SENT_BACK_PREFIX):
        return None
    doctype, sep, name = item_id[len(_SENT_BACK_PREFIX):].partition(":")
    if not sep or doctype not in APPROVAL_DOCTYPES:
        return None
    return _form(doctype, name)


def open_path(item):
    """The Desk path an item opens, or None when the Desk has no counterpart."""
    action = item.get("action") or {}
    if action.get("desk"):
        path = action["desk"]
        if not path.startswith("/desk/"):
            raise ValueError("My work item %s opens %r, which is not a Desk path." % (item["id"], path))
        return path
    form = _sent_back_form(item["id"])
    if form:
        return form
    screen = action.get("screen")
    if screen not in SCREEN_DESTINATIONS:
        raise ValueError("My work item %s opens screen %r, which has no declared Desk destination."
                         % (item["id"], screen))
    destination = SCREEN_DESTINATIONS[screen]
    if destination is None:
        return None
    path, by_period = destination
    query = []
    period = item.get("period")
    if by_period and period:
        query += [("fiscal_year", int(period["fiscal_year"])),
                  ("fiscal_period", int(period["fiscal_period"]))]
    if screen == "trial-balances" and action.get("entity"):
        query.append(("data_area_id", action["entity"]))
    return path + ("?" + _urlencode(query) if query else "")


def _due(item):
    due = item.get("due")
    if not due:
        return None
    text = due.get("text")
    return "%s (overdue)" % text if due.get("overdue") and text else text


def _row(item):
    kind = item.get("kind")
    if kind not in KIND_LABELS:
        raise ValueError("My work item %s has unknown kind %r." % (item.get("id"), kind))
    period = item.get("period")
    return {
        "id": item["id"],
        "kind": KIND_LABELS[kind],
        "title": item["title"],
        "message": item.get("detail"),
        "owner": item.get("owner"),
        "period": period["code"] if period else None,
        "since": (period or {}).get("since") if period else item.get("since"),
        "due": _due(item),
        "open": open_path(item),
    }


def rows(payload):
    """Blocking, then to do, then waiting; the API's own order within a kind."""
    out = [_row(item) for item in payload.get("items") or ()]
    order = [KIND_LABELS[k] for k in KINDS]
    return sorted(out, key=lambda row: order.index(row["kind"]))


def columns():
    return [
        {"fieldname": "kind", "label": "Kind", "fieldtype": "Data", "width": 90},
        {"fieldname": "title", "label": "Title", "fieldtype": "Data", "width": 260},
        {"fieldname": "message", "label": "Message", "fieldtype": "Data", "width": 360},
        {"fieldname": "owner", "label": "Owner Role", "fieldtype": "Data", "width": 130},
        {"fieldname": "period", "label": "Period", "fieldtype": "Data", "width": 100},
        {"fieldname": "since", "label": "Since", "fieldtype": "Date", "width": 100},
        {"fieldname": "due", "label": "Due", "fieldtype": "Data", "width": 160},
        {"fieldname": "open", "label": "Open", "fieldtype": "Data", "width": 90},
    ]
