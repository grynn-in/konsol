"""Approvals model, pure (konsol#305 A08; story 6.3; R2, R5; #305-W2-14;
W2-9, W2-10).

``queue_items(docs, preparers_by_ref, user, roles, policy, approver_roles,
self_approval_problem, entity_fields, allowed)`` turns the pending documents
across the 7 approval doctypes (close_policy_model.APPROVAL_DOCTYPES) into
one queue, oldest first. Returns ``{"items", "hidden"}``.

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


def _shape(doctype, doc):
    """``(kind_label, title, detail, extra)`` for one of the 5 doctypes not
    covered by ``rates_model.pending_items``. ``extra`` holds the keys a
    kind carries beyond the common ones (the journal's lines/effect/
    total_debit/currency).

    IC Balance, Business Combination and Business Disposal have no field
    that gives a title of their own (unlike the journal's description or
    the Group Exchange Rate's quote_label): their title is their
    kind_label, which already names their identifying ref.
    """
    if doctype == JOURNAL:
        kind_label = "Adjustment · %s" % doc["name"]
        title = _journal_title(doc.get("description"))
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
        }
        return kind_label, title, detail, extra
    if doctype == GER:
        kind_label = "Group rate · %s→%s %s" % (
            doc["from_currency"], doc["to_currency"], doc["rate_type"])
        title = doc["quote_label"]
        detail = _fy_p(doc["fiscal_year"], doc["fiscal_period"])
        if doc.get("change_reason"):
            detail += " · %s" % doc["change_reason"]
        return kind_label, title, detail, {}
    if doctype == IC_BALANCE:
        kind_label = "IC balance · %s → %s" % (doc["selling_entity"], doc["buying_entity"])
        detail = "%s · IC sales %.2f" % (
            _fy_p(doc["fiscal_year"], doc["fiscal_period"]), doc["ic_sales_amount"])
        return kind_label, kind_label, detail, {}
    if doctype == BC:
        kind_label = "Business combination · %s" % doc["acquired_entity"]
        detail = "%s · %g%% from %s" % (
            doc["consolidation_group"], doc["share_acquired_pct"], doc["acquisition_date"])
        return kind_label, kind_label, detail, {}
    if doctype == BD:
        kind_label = "Business disposal · %s" % doc["disposed_entity"]
        detail = "%s · %g%% from %s" % (
            doc["consolidation_group"], doc["share_disposed_pct"], doc["disposal_date"])
        return kind_label, kind_label, detail, {}
    raise ValueError("%r is not one of the 7 approval doctypes." % (doctype,))


def queue_items(docs, preparers_by_ref, user, roles, policy, approver_roles,
                 self_approval_problem, entity_fields, allowed):
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

    items.sort(key=lambda item: item["created"])

    if allowed is None:
        return {"items": items, "hidden": 0}
    visible = [item for item in items if not item["entity"] or item["entity"] in allowed]
    hidden = len(items) - len(visible)
    return {"items": visible, "hidden": hidden}
