"""The one approvals queue, across all 7 approval doctypes (konsol#305 A10;
stories 6.2, 6.3, 6.4; R2, R5; #305-W2-10).

``get_queue()`` (GET) returns ``{items, sent_back, counts, hidden,
self_approval, can_approve, waiting}`` and writes nothing. A module-level
``queue_for(user, roles)`` holds the logic, so My work (A12) uses the same
rule.

Pending, per doctype in ``close_policy_model.APPROVAL_DOCTYPES``:

- with an active Workflow: the states at docstatus 0 after the first state
  (the states a document reaches only once it has been sent for approval);
- with none: docstatus 0 (the same fallback ``approval_api.approve`` uses).

Reads are bounded by the 7 doctypes, not by the number of pending documents:
one Workflow lookup (plus one cached doc, only when a workflow is active)
and one ``get_all`` per doctype, one ``self_approval.preparers_for`` and one
rejections read per doctype with pending names, and — only while a journal
is pending — one read of its lines, the Published accounts and the fiscal
period rows.

This file never names the event-log doctype (the one-writer check,
test_close_event_writer.py): rejections are read only through
``close_event.latest_rejections``.
"""
from datetime import date, datetime

import frappe

from konsol import fiscal_calendar
from konsol.close import approvals_model, close_event, close_policy_model, journal_model, self_approval
from konsol.close.timefmt import zoned_iso
from konsol.entity_permissions import allowed_entity_codes

JOURNAL = "Consolidation Journal"
BC = "Business Combination"
BD = "Business Disposal"
GER = "Group Exchange Rate"
OP = "Ownership Period"
HER = "Historical Equity Rate"
IC_BALANCE = "IC Balance"

LINE = "Consolidation Journal Line"
LINE_FIELDS = ["parent", "idx", "data_area_id", "main_account",
               "debit_amount", "credit_amount", "description"]
ACCOUNT_FIELDS = ["name", "account_name", "parent_account", "is_group", "statement_section"]

#: Fields read per doctype, beyond ``name``, ``owner``, ``creation`` and
#: ``modified`` (A08 facts; the journal's ``duration``, ``lines`` and
#: ``effect`` are filled in below, from A01/A02, not read from the doctype).
_FIELDS = {
    JOURNAL: ["fiscal_year", "fiscal_period", "adjustment_type", "description",
              "total_debit", "currency", "reverse_fiscal_year", "reverse_fiscal_period"],
    GER: ["from_currency", "to_currency", "rate_type", "fiscal_year", "fiscal_period",
          "quote_label", "change_reason"],
    HER: ["consolidation_group", "data_area_id", "main_account", "rate_date", "historical_rate"],
    OP: ["consolidation_group", "data_area_id", "effective_date", "end_date",
         "ownership_pct", "consolidation_method"],
    IC_BALANCE: ["selling_entity", "buying_entity", "fiscal_year", "fiscal_period",
                 "ic_sales_amount", "ending_inventory_from_ic"],
    BC: ["consolidation_group", "acquired_entity", "acquisition_date", "share_acquired_pct", "goodwill"],
    BD: ["consolidation_group", "disposed_entity", "disposal_date", "share_disposed_pct", "total_proceeds"],
}

#: Date-typed fields per doctype, ISO-formatted so the response stays
#: JSON-safe (MariaDB hands these back as ``datetime.date``).
_DATE_FIELDS = {
    HER: ("rate_date",),
    OP: ("effective_date", "end_date"),
    BC: ("acquisition_date",),
    BD: ("disposal_date",),
}

def _iso(value):
    """A datetime with the site's UTC offset (mirrors rates_api.py's own
    ``_iso``); a plain date stays a date string. ``None``/``""`` stay
    ``None``."""
    if isinstance(value, datetime):
        return zoned_iso(value, frappe.utils.get_system_timezone())
    if isinstance(value, date):
        return value.isoformat()
    return None if value in (None, "") else value


def _number(value):
    """A Float/Currency column as a JSON-safe float (MariaDB may hand back a
    Decimal); ``None`` stays ``None``."""
    return None if value is None else float(value)


def _accounts():
    """``{code: {"account_name", "heading", "heading_name",
    "statement_section"}}`` of the postable (non-group) Published Main
    Accounts, one read (mirrors journal_api.py's ``_accounts``)."""
    rows = frappe.get_all(
        "Main Account", filters={"status": "Published"}, fields=ACCOUNT_FIELDS,
        limit_page_length=0,
    )
    by_code = {r["name"]: r for r in rows}
    accounts = {}
    for row in rows:
        if row.get("is_group"):
            continue
        heading = row.get("parent_account") or None
        heading_name = by_code.get(heading, {}).get("account_name") if heading else None
        accounts[row["name"]] = {
            "account_name": row.get("account_name"),
            "heading": heading,
            "heading_name": heading_name,
            "statement_section": row.get("statement_section"),
        }
    return accounts


def _line_out(line, accounts):
    return {
        "idx": line.get("idx"),
        "data_area_id": line.get("data_area_id"),
        "main_account": line.get("main_account"),
        "account_name": accounts.get(line.get("main_account"), {}).get("account_name"),
        "debit_amount": _number(line.get("debit_amount")),
        "credit_amount": _number(line.get("credit_amount")),
        "description": line.get("description"),
    }


def _pending_rows(doctype):
    """The pending rows of ``doctype``: with an active Workflow, the states
    at docstatus 0 after the first state; with none, docstatus 0 (the same
    fallback ``approval_api.approve`` uses). One Workflow lookup, plus one
    cached doc only when a workflow is active, plus one ``get_all``."""
    wf_row = frappe.db.get_value(
        "Workflow", {"document_type": doctype, "is_active": 1},
        ["name", "workflow_state_field"], as_dict=True,
    )
    fields = ["name", "owner", "creation", "modified"] + _FIELDS[doctype]
    if not wf_row or not wf_row.get("name"):
        filters = {"docstatus": 0}
    else:
        wf = frappe.get_cached_doc("Workflow", wf_row["name"])
        states = [s.state for s in (wf.states or [])]
        pending_states = states[1:]
        if not pending_states:
            return []
        filters = {"docstatus": 0, wf_row["workflow_state_field"]: ["in", pending_states]}
    rows = frappe.get_all(doctype, filters=filters, fields=fields,
                          order_by="creation asc", limit_page_length=0)
    return [dict(r) for r in rows]


def queue_for(user, roles):
    """``{items, sent_back, counts, hidden, self_approval, can_approve,
    waiting}`` for ``user`` holding ``roles``. Writes nothing. Shared with
    My work (A12) so both read the same rule."""
    policy = frappe.db.get_single_value("Close Settings", "self_approval")
    allowed = allowed_entity_codes(user)

    docs = {}
    modified_by_ref = {}
    preparers_by_ref = {}
    rejections = {}
    period_rows = None
    accounts = None

    for doctype in close_policy_model.APPROVAL_DOCTYPES:
        rows = _pending_rows(doctype)
        names = [row["name"] for row in rows]

        lines_by_parent = {}
        if doctype == JOURNAL and names:
            period_rows = fiscal_calendar.fiscal_period_rows()
            accounts = _accounts()
            lines = frappe.get_all(
                LINE, filters={"parenttype": JOURNAL, "parent": ["in", names]},
                fields=LINE_FIELDS, order_by="parent asc, idx asc", limit_page_length=0,
            )
            for line in lines:
                lines_by_parent.setdefault(line["parent"], []).append(line)

        preparers = self_approval.preparers_for(doctype, {row["name"]: row["owner"] for row in rows})
        for name, preparer_set in preparers.items():
            preparers_by_ref[(doctype, name)] = preparer_set

        if names:
            for name, rejection in close_event.latest_rejections(doctype, names).items():
                rejections[(doctype, name)] = rejection

        date_fields = _DATE_FIELDS.get(doctype, ())
        doc_rows = []
        for row in rows:
            name = row["name"]
            modified_by_ref[(doctype, name)] = row["modified"]
            doc = {key: value for key, value in row.items() if key != "modified"}
            doc["created"] = _iso(doc.pop("creation", None))
            for field in date_fields:
                doc[field] = _iso(doc.get(field))
            if doctype == JOURNAL:
                header_lines = lines_by_parent.get(name, [])
                doc["lines"] = [_line_out(line, accounts) for line in header_lines]
                doc["effect"] = journal_model.statement_effect(header_lines, accounts)
                doc["duration"] = journal_model.duration_label(
                    doc.get("reverse_fiscal_year"), doc.get("reverse_fiscal_period"), period_rows)
                doc["total_debit"] = _number(doc.get("total_debit"))
            doc_rows.append(doc)
        docs[doctype] = doc_rows

    result = approvals_model.queue_items(
        docs, preparers_by_ref, user, roles, policy, close_policy_model.APPROVER_ROLES,
        close_policy_model.self_approval_problem, close_event.ENTITY_FIELDS, allowed,
        rejections, modified_by_ref,
    )

    items = result["items"]
    for item in items:
        if item.get("rejection"):
            item["rejection"] = dict(item["rejection"], at=_iso(item["rejection"]["at"]))

    counts = {doctype: 0 for doctype in close_policy_model.APPROVAL_DOCTYPES}
    for item in items:
        counts[item["doctype"]] += 1

    return {
        "items": [item for item in items if not item["sent_back"]],
        "sent_back": [item for item in items if item["sent_back"]],
        "counts": counts,
        "hidden": result["hidden"],
        "self_approval": policy or None,
        "can_approve": bool(set(roles) & set(close_policy_model.APPROVER_ROLES)),
        "waiting": approvals_model.waiting_for_me(items),
    }


def sent_back_for(user):
    """The caller's own docstatus-0 drafts across the 7 approval doctypes
    whose newest ``rejected`` event is later than their own ``modified``
    (A21; E6-P12, #305-D2-8): the preparer's "sent back" My work item.
    Module-level, not whitelisted — My work (A22) calls it.

    The preparer is the document's ``owner`` (A04 records
    ``{"preparer": doc.owner}`` on the event). W2-14's editors are not
    included here: the owner drafted it, and an editor who is not the
    owner is the Close Lead, never the preparer.

    Reads are bounded by the 7 doctypes: one ``get_all`` per doctype
    (``owner`` + ``docstatus 0``), and one ``close_event.latest_rejections``
    per doctype that has pending names (A03; empty names reads nothing) —
    at most 14 reads.
    """
    docs = {}
    rejections = {}
    for doctype in close_policy_model.APPROVAL_DOCTYPES:
        fields = ["name", "modified"] + _FIELDS[doctype]
        rows = frappe.get_all(
            doctype, filters={"owner": user, "docstatus": 0}, fields=fields,
            limit_page_length=0,
        )
        doc_rows = [dict(r) for r in rows]
        names = [row["name"] for row in doc_rows]
        if names:
            for name, rejection in close_event.latest_rejections(doctype, names).items():
                rejections[(doctype, name)] = rejection
        docs[doctype] = doc_rows

    items = approvals_model.sent_back_items(docs, rejections)
    for item in items:
        item["rejection"] = dict(item["rejection"], at=_iso(item["rejection"]["at"]))
    return items


@frappe.whitelist(methods=["GET"])
def get_queue():
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    return queue_for(frappe.session.user, frappe.get_roles(frappe.session.user))
