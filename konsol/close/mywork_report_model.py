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

konsol#419 (option C, Deepak Pai, 7 Nov 2026): the same payload is also laid
out as a board (``board``): three columns, Blocking, To do and Waiting, of
cards. Each card carries what is wrong (the item's detail), how to fix it
(``FIXES``: one or two plain steps per item kind), Go (``open_path``, so the
board and the list never disagree), the due date, the owner role and a
Handbook page. An item kind with no fix raises ValueError: a card never shows
a blank "How to fix". A card with no Desk page says so and links the Handbook.
Nothing is stored and nothing is dragged: a card moves when the work is done.

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
#: - approvals: the Konsol home, whose "Waiting for approval" shortcuts
#:   open each list filtered to what is waiting (approvals are done in the
#:   record's Actions menu since 5 Nov 2026; the Approvals report is gone);
#: - adjustments, intercompany, audit-trail: the journals, IC balances and
#:   Close Events lists;
#: - period: the Period Status list;
#: - my-work: this report itself, so no link.
SCREEN_DESTINATIONS = {
    "my-work": None,
    "approvals": ("/desk/konsol", False),
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

#: konsol#419: an item kind whose work is done somewhere other than its
#: screen's destination. Close period is in the Period Close report's Actions
#: menu, and that report also shows what blocks a sign-off (Handbook: Approve,
#: check and sign off the period). The report has no filters: its Actions
#: ask for the period.
KIND_DESTINATIONS = {
    "close": "/desk/query-report/" + _quote("Period Close"),
    "signoff-wait": "/desk/query-report/" + _quote("Period Close"),
}

LIST_VIEW = "/desk/query-report/" + _quote("My Work")
HANDBOOK = "/desk/konsol-handbook"

#: konsol#419: how to fix each item kind (``item_type``), in one or two plain
#: steps, and the Handbook page (konsol/handbook_pages/<slug>.md) that explains
#: it, or None. Every kind mywork_model and mywork_api can emit is here; the
#: test reads the kinds from their code, so a new kind without a fix fails.
#: The steps name the Desk page Go opens.
_LEAD, _GROUP, _ENTITY, _IC, _FX = ("start-close-lead", "start-group-accountant",
                                    "start-entity-accountant", "intercompany", "fx-translation")
_SENT_BACK_FIX = ("Open the draft and read the reason it was sent back.",
                  "Correct it and send it for approval again.")
FIXES = {
    # setup gaps (mywork_model.GAPS)
    "gap:first_close": (("In Close Settings, set First Close Fiscal Year and First Close Fiscal "
                         "Period, and save.",), _LEAD),
    "gap:self_approval": (("In Close Settings, choose Self-Approval: Blocked, or Allowed with "
                           "reason, and save.",), _LEAD),
    "gap:rate_move": (("In Close Settings, enter Rate Move Needing a Reason, and save.",), _FX),
    "gap:statement_accounts": (("In Close Settings, set the statement accounts the message "
                                "names, and save.",), _LEAD),
    "gap:commentary_threshold": (("In Close Settings, enter Commentary Threshold (Amount), "
                                  "(Percent), or both, and save.",), _LEAD),
    "gap:iso_currency": (("Open each currency listed and set Minor Unit to the ISO 4217 value "
                          "the message gives.",
                          "Save it. Sign-off is blocked until then.",), None),
    "gap:iso_currency_unused": (("Open each currency listed and set Minor Unit to the ISO 4217 "
                                 "value the message gives, then save.",
                                 "This does not block sign-off until something uses the "
                                 "currency.",), None),
    "gap:chart": (("Open Main Account and publish the group chart's accounts.",), _LEAD),
    "gap:ic_accounts": (("Open Intercompany Account and publish each pair of intercompany "
                         "accounts.",
                         "Each account must first have Allow Intercompany ticked on its Main "
                         "Account.",), _IC),
    "gap:ic_tolerance": (("Open each consolidation group named and enter its Intercompany "
                          "Difference Tolerance, then save.",), _IC),
    "gap:ic_rule": (("Open IC Elimination Rule and add an unrealised-profit rule that matches "
                     "each IC Balance pair named.",), _IC),
    "gap:ic_rule_ambiguous": (("Open IC Elimination Rule and narrow the rules so that each IC "
                               "Balance pair named matches only one.",), _IC),
    "gap:frequency": (("Open each entity listed and set its Reporting Frequency, then save.",),
                      _LEAD),
    "gap:ownership": (("Add an Ownership Period for each entity named, covering the start of "
                       "the periods named.",
                       "Send it for approval; the Close Lead approves it.",), _GROUP),
    "gap:accountants": (("Give each user listed a User Permission with Allow = Entity for the "
                         "entities they upload.",), _ENTITY),
    # period items (mywork_model.period_items)
    "tb": (("Add a Trial Balance Submission for the entity and period, and load the file.",
            "Check it and submit it."), _ENTITY),
    "rates": (("Add the missing Closing and Average Group Exchange Rates for the period.",
               "Send them for approval; the Close Lead approves them."), _GROUP),
    "checks-run": (("Open the period's latest Assertion Run and click Run Suite.",), _LEAD),
    "checks-failing": (("Open the period's latest Assertion Run and read each failing check.",
                        "Fix what each one names, then click Run Suite again."), _LEAD),
    "tbs-waiting": (("Nothing to do yourself: each entity's accountant submits its trial "
                     "balance.",
                     "Open the list to see which are in and remind the others."), _LEAD),
    "resign": (("Something changed after the sign-off: open the period's latest Assertion Run "
                "and click Run Suite.",
                "When the checks finish, sign off again."), _LEAD),
    "unowned": (("Add an Ownership Period covering the period for each entity named.",
                 "Send it for approval, then approve it."), _GROUP),
    "close": (("In Period Close, use Actions, then Close period.",), _LEAD),
    "checks-waiting": (("Nothing to do yourself until the checks pass: a Group Accountant runs "
                        "and fixes them.",
                        "Open the period's runs to follow them."), _LEAD),
    "signoff": (("Open the period's latest Assertion Run and click Sign Off (or Acknowledge "
                 "& Sign Off when it has warnings).",), _LEAD),
    "signoff-wait": (("Nothing to do yourself yet: an earlier period, or a sign-off gate, must "
                      "clear first.",
                      "Period Close shows what blocks the sign-off."), _LEAD),
    "rates-error": (("Request a consolidation build (Build Approval) and wait for it to "
                     "complete.",
                     "Then open My Work again; the rates are checked once the warehouse "
                     "answers."), _GROUP),
    # mywork_model.approvals_item, ic_fix_items, sent-back items
    "approvals": (("Open each waiting record and use Actions, then Approve or Reject.",), _LEAD),
    "ic": (("Correct the intercompany rows on your trial balance for the period, or agree the "
            "figure with the partner.",
            "Submit the corrected trial balance."), _IC),
    "sent-back:signoff": (("Fix what the reason names and run the checks again.",
                           "The Close Lead then signs the period off."), _LEAD),
    "sent-back:Consolidation Journal": (_SENT_BACK_FIX, _GROUP),
    "sent-back:Business Combination": (_SENT_BACK_FIX, _GROUP),
    "sent-back:Business Disposal": (_SENT_BACK_FIX, _GROUP),
    "sent-back:Group Exchange Rate": (_SENT_BACK_FIX, _GROUP),
    "sent-back:Ownership Period": (_SENT_BACK_FIX, _GROUP),
    "sent-back:Historical Equity Rate": (_SENT_BACK_FIX, _FX),
    "sent-back:IC Balance": (_SENT_BACK_FIX, _IC),
}

NO_DESK_PAGE = "There is no Desk page for this. The Handbook page explains what to do."

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


def item_type(item):
    """The kind of work an item is, read from its id: ``gap:<name>``,
    ``sent-back:<doctype>`` (``sent-back:signoff`` for a sign-off), or the
    id's first part (``tb``, ``checks-run``, ``approvals``, ``ic`` ...)."""
    item_id = str(item["id"])
    if item_id.startswith("gap:"):
        return item_id
    if item_id.startswith(_SENT_BACK_PREFIX):
        return _SENT_BACK_PREFIX + item_id[len(_SENT_BACK_PREFIX):].partition(":")[0]
    return item_id.partition(":")[0]


def fix_for(item):
    """``(steps, handbook slug or None)`` for an item; ValueError for a kind
    with no fix (never a blank "How to fix")."""
    kind = item_type(item)
    if kind not in FIXES:
        raise ValueError("My work item %s is of kind %r, which has no fix text "
                         "(mywork_report_model.FIXES)." % (item["id"], kind))
    return FIXES[kind]


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
    if item_type(item) in KIND_DESTINATIONS:
        return KIND_DESTINATIONS[item_type(item)]
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


def _kind_label(item):
    kind = item.get("kind")
    if kind not in KIND_LABELS:
        raise ValueError("My work item %s has unknown kind %r." % (item.get("id"), kind))
    return KIND_LABELS[kind]


def _row(item):
    label = _kind_label(item)
    period = item.get("period")
    return {
        "id": item["id"],
        "kind": label,
        "title": item["title"],
        "message": item.get("detail"),
        "fix": " ".join(fix_for(item)[0]),
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
        {"fieldname": "open", "label": "Open", "fieldtype": "Data", "width": 70},
        {"fieldname": "title", "label": "Title", "fieldtype": "Data", "width": 260},
        {"fieldname": "message", "label": "Message", "fieldtype": "Data", "width": 320},
        {"fieldname": "fix", "label": "How to fix", "fieldtype": "Data", "width": 360},
        {"fieldname": "owner", "label": "Owner Role", "fieldtype": "Data", "width": 130},
        {"fieldname": "period", "label": "Period", "fieldtype": "Data", "width": 100},
        {"fieldname": "since", "label": "Since", "fieldtype": "Date", "width": 100},
        {"fieldname": "due", "label": "Due", "fieldtype": "Data", "width": 160},
    ]


def empty_message(payload, shown):
    """The Close app's own sentences (MyWork.vue, A54): only the Entity
    Accountant persona carries ``entities_assigned`` (None for the others)."""
    if payload.get("entities_assigned") is False:
        return "No entities are assigned to you. Ask the System Manager."
    if payload.get("entities_assigned") is True and not shown:
        return "Your entities are assigned, but none are in scope this period."
    return None


# --- konsol#419: the board -------------------------------------------------------

def _handbook_url(slug):
    return "%s?%s" % (HANDBOOK, _urlencode({"page": "konsol:%s" % slug})) if slug else None


def card(item):
    """One board card. ValueError for an item kind with no fix, and for one
    with neither a Desk page nor a Handbook page."""
    _kind_label(item)
    steps, slug = fix_for(item)
    go = open_path(item)
    if go is None and slug is None:
        raise ValueError("My work item %s has no Desk page and no Handbook page: "
                         "give its kind one in mywork_report_model.FIXES." % item["id"])
    due = item.get("due")
    period = item.get("period")
    return {
        "id": item["id"],
        "kind": item["kind"],
        "type": item_type(item),
        "title": item["title"],
        "detail": item.get("detail"),
        "fix": list(steps),
        "go": go,
        "no_go": None if go else NO_DESK_PAGE,
        "owner": item.get("owner"),
        "period": period["code"] if period else None,
        "due": {"text": due.get("text"), "overdue": bool(due.get("overdue"))} if due else None,
        "handbook": _handbook_url(slug),
    }


def board(payload):
    """``{columns: [{kind, label, cards}], message, list_view}``: Blocking, To
    do, Waiting, each in the API's own order."""
    cards = [card(item) for item in payload.get("items") or ()]
    columns = [{"kind": k, "label": KIND_LABELS[k], "cards": [c for c in cards if c["kind"] == k]}
               for k in KINDS]
    return {"columns": columns, "message": empty_message(payload, cards), "list_view": LIST_VIEW}
