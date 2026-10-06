"""Approvals model, pure (konsol#305 A08, A09; story 6.3; R2, R5; #305-W2-14;
W2-9, W2-10; #305-D2-8; E6-P3, E6-P11).

``queue_items(docs, preparers_by_ref, user, roles, policy, approver_roles,
self_approval_problem, entity_fields, allowed, rejections, modified_by_ref)``
turns the pending documents across the 7 approval doctypes
(close_policy_model.APPROVAL_DOCTYPES) into one queue, oldest first.
Returns ``{"items", "hidden"}``.

- ``docs``: ``{doctype: [row dicts]}``, already filtered to pending by the
  API (A10). Each row carries ``name``, ``owner``, ``created`` (an ISO
  string) and its doctype's own fields. A doctype key outside the 7 raises
  ValueError naming it: it is never shown as another kind.
- ``preparers_by_ref``: ``{(doctype, name): frozenset}``. Keyed by
  ``(doctype, name)``, never by name alone — ``rates_model.pending_items``
  keys by name alone, which can collide across doctypes (#305-W2-14). A
  missing ref raises KeyError: it is never treated as owner-only.
- Each item carries the caller's approve mode, the existing
  ``rates_model.approve_mode``, computed from the injected ``policy``,
  ``approver_roles`` and ``self_approval_problem`` — the self-approval rule
  itself is never copied here (close_policy_model.py:173-220, as E402
  injects it into rates_model).
- ``entity_fields``: ``{doctype: field name or None}`` (close_event.py's
  ``ENTITY_FIELDS``), injected so there is one map of which field scopes a
  doctype's events. An item whose entity is set and not in ``allowed`` is
  hidden and counted; a group-level item (the field is None) is never
  hidden. ``allowed`` None means unrestricted.
- ``rejections``: ``{(doctype, name): {"reason", "actor", "at"}}`` (A03's
  ``close_event.latest_rejections``, re-keyed by the caller). A name with
  no rejected event is absent, never a KeyError. An item whose rejection's
  ``at`` is later than its ``modified`` (``modified_by_ref``) is
  ``sent_back``, and carries that ``rejection``; otherwise ``sent_back`` is
  False and ``rejection`` is None (#305-D2-8, E6-P3: a Comment insert does
  not move ``modified``, so the preparer's next save is what clears it).
- ``modified_by_ref``: ``{(doctype, name): <modified>}``, one entry per
  pending document (every item needs it to decide ``sent_back``). A
  missing ref raises KeyError, the same rule as ``preparers_by_ref``.
- ``waiting_for_me(items)`` returns ``{"count", "oldest"}`` over the items
  the caller may act on now: ``approve.mode`` ``direct`` or ``reason``, and
  not sent back (inline and Desk items count alike, E6-P11: a refused item
  is never counted). ``oldest`` is the earliest ``created`` among them, or
  None when none are counted.

Historical Equity Rate and Ownership Period items are not re-derived here:
``rates_model.pending_items`` (and the ``approve_mode`` it and every other
doctype here call) is loaded as a sibling and reused, mirroring
trail_model.py:26-38 ``_load_sibling``. ``close_policy_model`` is loaded the
same way, for ``APPROVAL_DOCTYPES`` only: the self-approval rule itself
stays **injected**, as ``self_approval_problem``, so this module stays
import-free.

Imports nothing from frappe or konsol.
"""
import importlib.util
import os

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_APP_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rates_model = _load_sibling("konsol_close_approvals_model_rates_model", "close/rates_model.py")
close_policy_model = _load_sibling(
    "konsol_close_approvals_model_close_policy_model", "close/close_policy_model.py")

JOURNAL = "Consolidation Journal"
BC = "Business Combination"
BD = "Business Disposal"
GER = "Group Exchange Rate"
OP = "Ownership Period"
HER = "Historical Equity Rate"
IC_BALANCE = "IC Balance"

#: Business Combination and Business Disposal are approved in the Desk only
#: (the engineering call, konsol#305 W3-1..4 comment): never inline.
_DESK_ONLY = (BC, BD)
_DESK_PATHS = {
    BC: "/app/business-combination/%s",
    BD: "/app/business-disposal/%s",
}

#: Historical Equity Rate and Ownership Period items come from
#: rates_model.pending_items unchanged (title, detail unchanged); they only
#: gain a kind_label here. Their own title already carries the entity and
#: date, unlike the other 5 kinds, so no second ref is added.
_RATE_KIND_LABELS = {
    HER: "Historical equity rate",
    OP: "Ownership period",
}


def _fy_p(fiscal_year, fiscal_period):
    return "FY%s P%02d" % (fiscal_year, int(fiscal_period))


def _journal_title(description):
    """The first non-blank line of ``description``, else "(no description)"
    (E6-P5). Mirrors journal_api.py's ``_title``."""
    for line in (description or "").splitlines():
        line = line.strip()
        if line:
            return line
    return "(no description)"


def _kind_label_and_title(doctype, doc):
    """``(kind_label, title)`` for one of the 5 doctypes not covered by
    ``rates_model.pending_items`` — the subset of ``_shape`` that never
    touches a journal's ``lines``, ``effect`` or ``duration`` (A21:
    ``sent_back_items`` calls this directly, since ``sent_back_for`` never
    fetches those three — it needs only a label and a period, not the
    posting detail).

    IC Balance, Business Combination and Business Disposal have no field
    that gives a title of their own (unlike the journal's description or
    the Group Exchange Rate's quote_label): their title is their
    kind_label, which already names their identifying ref.
    """
    if doctype == JOURNAL:
        return "Adjustment · %s" % doc["name"], _journal_title(doc.get("description"))
    if doctype == GER:
        kind_label = "Group rate · %s→%s %s" % (
            doc["from_currency"], doc["to_currency"], doc["rate_type"])
        return kind_label, doc["quote_label"]
    if doctype == IC_BALANCE:
        kind_label = "IC balance · %s → %s" % (doc["selling_entity"], doc["buying_entity"])
        return kind_label, kind_label
    if doctype == BC:
        kind_label = "Business combination · %s" % doc["acquired_entity"]
        return kind_label, kind_label
    if doctype == BD:
        kind_label = "Business disposal · %s" % doc["disposed_entity"]
        return kind_label, kind_label
    raise ValueError("%r is not one of the 7 approval doctypes." % (doctype,))


def _shape(doctype, doc):
    """``(kind_label, title, detail, extra)`` for one of the 5 doctypes not
    covered by ``rates_model.pending_items``. ``extra`` holds the keys a
    kind carries beyond the common ones (the journal's lines/effect/
    total_debit/currency, plus fiscal_year/fiscal_period/consolidation_group,
    #305-W3-4/W41, so the screen can read the statement for that period and
    group).
    """
    kind_label, title = _kind_label_and_title(doctype, doc)
    if doctype == JOURNAL:
        detail = "%s · %s · %s" % (
            doc["adjustment_type"].capitalize(),
            _fy_p(doc["fiscal_year"], doc["fiscal_period"]),
            doc["duration"],
        )
        extra = {
            "lines": doc["lines"],
            "effect": doc["effect"],
            "total_debit": doc["total_debit"],
            "currency": doc["currency"],
            "fiscal_year": doc["fiscal_year"],
            "fiscal_period": doc["fiscal_period"],
            "consolidation_group": doc["consolidation_group"],
            # D06 (konsolidat#245 option D): [{key, label}], [] when none
            # are declared; each line carries its value per key.
            "dimensions": doc["dimensions"],
        }
        return kind_label, title, detail, extra
    if doctype == GER:
        detail = _fy_p(doc["fiscal_year"], doc["fiscal_period"])
        if doc.get("change_reason"):
            detail += " · %s" % doc["change_reason"]
        return kind_label, title, detail, {}
    if doctype == IC_BALANCE:
        detail = "%s · IC sales %.2f" % (
            _fy_p(doc["fiscal_year"], doc["fiscal_period"]), doc["ic_sales_amount"])
        return kind_label, kind_label, detail, {}
    if doctype == BC:
        detail = "%s · %g%% from %s" % (
            doc["consolidation_group"], doc["share_acquired_pct"], doc["acquisition_date"])
        return kind_label, kind_label, detail, {}
    if doctype == BD:
        detail = "%s · %g%% from %s" % (
            doc["consolidation_group"], doc["share_disposed_pct"], doc["disposal_date"])
        return kind_label, kind_label, detail, {}
    raise ValueError("%r is not one of the 7 approval doctypes." % (doctype,))


def queue_items(docs, preparers_by_ref, user, roles, policy, approver_roles,
                 self_approval_problem, entity_fields, allowed, rejections,
                 modified_by_ref):
    for doctype in docs:
        if doctype not in close_policy_model.APPROVAL_DOCTYPES:
            raise ValueError("%r is not one of the 7 approval doctypes." % (doctype,))

    def _entity(doctype, doc):
        field = entity_fields.get(doctype)
        return doc.get(field) if field else None

    her_docs = docs.get(HER, [])
    op_docs = docs.get(OP, [])
    her_by_name = {d["name"]: d for d in her_docs}
    op_by_name = {d["name"]: d for d in op_docs}

    preparers_by_name = {}
    for (doctype, name), preparer_set in preparers_by_ref.items():
        if doctype in (HER, OP):
            preparers_by_name[name] = preparer_set

    items = list(rates_model.pending_items(
        her_docs, op_docs, preparers_by_name, user, roles, policy,
        approver_roles, self_approval_problem,
    ))
    for item in items:
        doc = her_by_name[item["name"]] if item["doctype"] == HER else op_by_name[item["name"]]
        item["kind_label"] = _RATE_KIND_LABELS[item["doctype"]]
        item["inline"] = True
        item["desk"] = None
        item["entity"] = _entity(item["doctype"], doc)

    for doctype, rows in docs.items():
        if doctype in (HER, OP):
            continue
        for doc in rows:
            name = doc["name"]
            preparers = preparers_by_ref[(doctype, name)]
            mode = rates_model.approve_mode(
                user, roles, preparers, doctype, name, policy, approver_roles,
                self_approval_problem,
            )
            kind_label, title, detail, extra = _shape(doctype, doc)
            item = {
                "doctype": doctype,
                "name": name,
                "kind_label": kind_label,
                "title": title,
                "detail": detail,
                "preparer": doc["owner"],
                "edited_by": sorted(preparers - {doc["owner"]}),
                "created": doc["created"],
                "approve": mode,
                "inline": doctype not in _DESK_ONLY,
                "desk": _DESK_PATHS[doctype] % name if doctype in _DESK_ONLY else None,
                "entity": _entity(doctype, doc),
            }
            item.update(extra)
            items.append(item)

    for item in items:
        ref = (item["doctype"], item["name"])
        modified = modified_by_ref[ref]
        rejection = rejections.get(ref)
        sent_back = is_sent_back(rejection, modified)
        item["sent_back"] = sent_back
        item["rejection"] = rejection if sent_back else None

    items.sort(key=lambda item: item["created"])

    if allowed is None:
        return {"items": items, "hidden": 0}
    visible = [item for item in items if not item["entity"] or item["entity"] in allowed]
    hidden = len(items) - len(visible)
    return {"items": visible, "hidden": hidden}


def is_sent_back(rejection, modified):
    """The one rule for "sent back" (A09, A21; #305-D2-8, E6-P3): a Comment
    insert does not touch ``modified`` (frappe comment.py:192-193), so a
    document is sent back when its newest ``rejected`` event's ``at`` is
    later than its own ``modified`` — the preparer's next save moves it
    past the rejection. ``queue_items`` and ``sent_back_items`` both call
    this; the comparison is never copied inline a second time.

    ``rejection`` is None (never rejected) -> False. Otherwise both
    ``rejection["at"]`` and ``modified`` must be given: either missing
    raises ValueError, never guessed.
    """
    if rejection is None:
        return False
    at = rejection.get("at")
    if at is None or modified is None:
        raise ValueError(
            "is_sent_back needs both rejection['at'] and modified; got at=%r modified=%r"
            % (at, modified))
    return at > modified


def sent_back_items(docs, rejections):
    """The caller's own pending documents whose newest rejection is later
    than their own ``modified`` (A21; E6-P12, #305-D2-8): the preparer's
    "sent back" My work item.

    ``docs`` is ``{doctype: [rows with name, modified and the A08
    fields]}``; ``rejections`` is ``{(doctype, name): {"reason", "actor",
    "at"}}`` (close_event.latest_rejections, A03, re-keyed by the caller).
    A doctype outside the 7 raises ValueError naming it — the same
    contract as ``queue_items``. A row with no ``modified`` raises
    ValueError naming the document: never guessed.

    Returns ``[{"doctype", "name", "kind_label", "title", "fiscal_year",
    "fiscal_period", "rejection"}]``, oldest rejection first. ``fiscal_year``
    / ``fiscal_period`` come from the row for the journal, Group Exchange
    Rate and IC Balance; they are None for Historical Equity Rate (not
    period-keyed, E4-P12), Ownership Period, Business Combination and
    Business Disposal (date-keyed).

    Labels and titles reuse ``_shape`` and ``_RATE_KIND_LABELS`` (A08's own
    code) rather than a copy. Historical Equity Rate and Ownership Period
    titles come from ``rates_model.pending_items``, called with ``roles``
    ``()`` so ``approve_mode`` short-circuits to ``not_approver`` before it
    ever touches preparers or the self-approval policy — this function
    never computes an approve mode of its own.
    """
    for doctype in docs:
        if doctype not in close_policy_model.APPROVAL_DOCTYPES:
            raise ValueError("%r is not one of the 7 approval doctypes." % (doctype,))

    her_rows = [dict(d, owner=None, created=d["modified"]) for d in docs.get(HER, [])]
    op_rows = [dict(d, owner=None, created=d["modified"]) for d in docs.get(OP, [])]
    titles = {}
    if her_rows or op_rows:
        preparers_by_name = {d["name"]: frozenset() for d in her_rows + op_rows}
        rate_items = rates_model.pending_items(
            her_rows, op_rows, preparers_by_name, None, (), None,
            close_policy_model.APPROVER_ROLES, close_policy_model.self_approval_problem,
        )
        titles = {(item["doctype"], item["name"]): item["title"] for item in rate_items}

    period_keyed = (JOURNAL, GER, IC_BALANCE)

    out = []
    for doctype, rows in docs.items():
        for doc in rows:
            name = doc["name"]
            modified = doc.get("modified")
            if modified is None:
                raise ValueError("%s %s has no modified." % (doctype, name))
            ref = (doctype, name)
            rejection = rejections.get(ref)
            if not is_sent_back(rejection, modified):
                continue
            if doctype in (HER, OP):
                kind_label = _RATE_KIND_LABELS[doctype]
                title = titles[ref]
            else:
                kind_label, title = _kind_label_and_title(doctype, doc)
            if doctype in period_keyed:
                fiscal_year, fiscal_period = doc["fiscal_year"], doc["fiscal_period"]
            else:
                fiscal_year = fiscal_period = None
            out.append({
                "doctype": doctype,
                "name": name,
                "kind_label": kind_label,
                "title": title,
                "fiscal_year": fiscal_year,
                "fiscal_period": fiscal_period,
                "rejection": rejection,
            })

    out.sort(key=lambda item: item["rejection"]["at"])
    return out


def waiting_for_me(items):
    """The items the caller may act on now (story 6.2, 6.3; E6-P11):
    ``approve.mode`` ``direct`` or ``reason``, and not ``sent_back`` —
    inline and Desk items count alike. A refused or ``not_approver`` item
    is never counted. Returns ``{"count", "oldest"}``; ``oldest`` is the
    earliest ``created`` among the counted items, or None when none are
    counted."""
    counted = [item for item in items
               if item["approve"]["mode"] in ("direct", "reason") and not item["sent_back"]]
    if not counted:
        return {"count": 0, "oldest": None}
    return {"count": len(counted), "oldest": min(item["created"] for item in counted)}
