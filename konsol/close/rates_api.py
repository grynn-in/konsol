"""Rates endpoints for the close app (konsol#305 E403; stories 4.1, 4.3;
#305-W2-3, W2-10, W2-14; D2-9).

``get_rates(fiscal_year, fiscal_period)`` (GET) returns the period's group
rate grid, as ``rates_model.grid`` assembles it:

- the required (from, to) pairs from the last build
  (``group_rates.translation_needs``, a ClickHouse read). Any failure there is
  reported as ``pairs_error`` and every pair with a rate is listed with
  ``required: None``: it fails closed and visibly (E4-P9);
- the period's drafts and approved Group Exchange Rates, and the previous
  approved rate per key (one read of every earlier approved rate; the model
  picks the latest per key);
- the move flag of each cell under the declared threshold
  (``group_rates.move_threshold``), never a guessed one;
- on each awaiting cell, what the caller may do (``rates_model.approve_mode``)
  judged on the draft's preparers (``self_approval.preparers_for``,
  #305-W2-14), with ``edited_by``: the preparers other than the owner. An
  approved or missing cell carries ``approve: None`` and ``edited_by: None``;
- the policy gaps, and whether the caller may enter or approve;
- ``quoted_per_options``, the Group Exchange Rate ``quoted_per`` field's own
  Select options (E409b): the one source of truth, so the screen never keeps
  a hand-copied list of its own.

Viewers (EPM User) read it and write nothing (#305-W2-10): ``approve_mode``
already says ``not_approver`` for them. The number of reads does not depend
on the number of pairs: 8 MariaDB reads and 1 ClickHouse query.

``save_rate(...)`` (POST, E404) saves one cell: a new Closing or Average draft,
or an edit of a named draft. It names only the grain, the quote, the unit and
the reason, and has no ``**kwargs``: Frappe drops any request key a whitelisted
function does not name (``frappe.get_newargs``), so a forged ``docstatus``,
``source``, ``erp_quote``, ``source_note``, ``owner`` or ``amended_from`` never
reaches it. It goes through ``insert()`` / ``save()`` with no ``ignore_*``
flag, so the doctype's own validate decides (magnitude, digits, group currency,
the move and its reason), and Frappe sets the owner. The Analyst has no submit
on Group Exchange Rate, so the Analyst never approves (R2).

``get_pending()`` (GET, E405) lists every Historical Equity Rate and
Ownership Period draft (docstatus 0; both have no submit for the Analyst, so
a draft always awaits the Close Lead), with the preparer, who else edited it
(``edited_by``, #305-W2-14) and the caller's approve mode. HER is not
period-keyed (E4-P12), so this lists every open draft site-wide. A Viewer
reads it and sees ``not_approver`` on every item (#305-W2-10). The items are
cut to ``entity_permissions.allowed_entity_codes()`` (#305-W2-10, W2-14),
with a ``hidden`` count: a hidden entity's draft never appears in the
response. Approving is the existing ``approval_api.approve``; this endpoint
writes nothing.

``get_ownership()`` (GET, E406) lists, for the period, the entities with a
submitted trial balance but no ownership covering the period's start
(blocking, #305-W2-2) and the Active leaf entities out of scope for
information, using the one scope rule in ``scope_model`` (G01) and never
re-deriving it. A Viewer reads it with ``can_record`` False (#305-W2-10).
Refuses an undeclared period, and a non-Regular one (R01h; the ownership
screen covers Regular periods only, mirroring ``grid_api``'s refusal).
``blocking`` and ``out_of_scope`` are cut to ``allowed_entity_codes()``, with
a combined ``hidden`` count (W2-10, W2-14) and a ``blocking_hidden`` count
(R01h) naming only the hidden blocking entities, so a caller can tell a
hidden gap from a merely-hidden out-of-scope entity. The query is keyed to
the period's start date; moving it to the period's end waits on G09/G04
(W2-16, blocked).
"""
from datetime import date, datetime
from urllib.parse import quote

import frappe

from konsol import fiscal_calendar, group_rates
from konsol.close import close_policy_model, rates_model, scope_model, self_approval
from konsol.close.timefmt import zoned_iso
from konsol.entity_permissions import allowed_entity_codes

#: Who reads the Rates screen (#305-W2-3, W2-10). The Entity Accountant does not.
RATES_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")
GER = "Group Exchange Rate"
DOC_FIELDS = ["name", "from_currency", "to_currency", "rate_type", "quote", "quoted_per",
              "erp_quote", "docstatus", "owner", "modified", "change_reason", "source"]


def _period(fiscal_year, fiscal_period):
    try:
        return int(fiscal_year), int(fiscal_period)
    except (TypeError, ValueError):
        frappe.throw(f"FY{fiscal_year} P{fiscal_period} is not a period: "
                     "pass the fiscal year and period as whole numbers.")


def _period_row(key):
    for row in fiscal_calendar.fiscal_period_rows():
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            return row
    frappe.throw("FY%d P%02d is not declared: declare it in EPM Fiscal Year." % key)


def _number(value):
    """A Float column as a JSON-safe float (MariaDB may hand back a Decimal)."""
    return None if value is None else float(value)


def _quoted_per_options():
    """The Group Exchange Rate ``quoted_per`` Select field's own options
    (E409b), so the screen never keeps a hand-copied list of its own."""
    options = frappe.get_meta(GER).get_field("quoted_per").options
    return [line.strip() for line in (options or "").splitlines() if line.strip()]


def _required(fy, fp):
    """(required pairs or None, pairs_error, blockers) — rate_gate's wording."""
    try:
        pairs, groups = group_rates.translation_needs(fy, fp)
    except Exception as e:  # noqa: BLE001 — any failure to read means "can't say"
        names = sorted(group_rates.ch_error_names(e))
        return None, type(e).__name__ + (f" {', '.join(names)}" if names else ""), []
    return set(pairs), None, [f"Consolidation Group {g} has no reporting currency"
                              for g in groups]


def _docs(fy, fp):
    docs = frappe.get_all(
        GER,
        filters={"fiscal_year": fy, "fiscal_period": fp, "docstatus": ["in", [0, 1]]},
        fields=DOC_FIELDS,
        limit_page_length=0,
    )
    out = []
    for doc in docs:
        doc = dict(doc)
        doc["quote"] = _number(doc.get("quote"))
        doc["rate"] = group_rates.true_rate(doc["quote"], doc.get("quoted_per"))
        erp_quote = doc.pop("erp_quote", None)
        doc["erp_rate"] = (group_rates.true_rate(erp_quote, doc.get("quoted_per"))
                           if erp_quote else None)
        out.append(doc)
    return out


def _earlier(fy, fp):
    rows = frappe.db.sql(
        "SELECT name, from_currency, to_currency, rate_type, quote, quoted_per, "
        "fiscal_year, fiscal_period FROM `tabGroup Exchange Rate` "
        "WHERE docstatus = 1 AND (fiscal_year < %s OR (fiscal_year = %s AND fiscal_period < %s))",
        (fy, fy, fp),
        as_dict=True,
    )
    out = []
    for row in rows:
        row = dict(row)
        row["quote"] = _number(row.get("quote"))
        row["rate"] = group_rates.true_rate(row["quote"], row.get("quoted_per"))
        out.append(row)
    return out


@frappe.whitelist(methods=["GET"])
def get_rates(fiscal_year, fiscal_period):
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key = _period(fiscal_year, fiscal_period)
    period = _period_row(key)
    fy, fp = key

    required, pairs_error, blockers = _required(fy, fp)
    docs = _docs(fy, fp)
    earlier = _earlier(fy, fp)
    threshold = group_rates.move_threshold()
    threshold_pct = threshold * 100 if threshold is not None else None
    policy = frappe.db.get_single_value("Close Settings", "self_approval")

    result = rates_model.grid(required, docs, earlier, threshold, group_rates.move_problem)

    cells = [row[k] for row in result["rows"] + result["unrequired"] for k in ("closing", "average")]
    awaiting = [c for c in cells if c["status"] == "awaiting_approval"]
    preparers = self_approval.preparers_for(GER, {c["name"]: c["owner"] for c in awaiting})

    user = frappe.session.user
    roles = frappe.get_roles(user)
    for cell in cells:
        if cell["status"] != "awaiting_approval":
            cell["approve"] = None
            cell["edited_by"] = None
            continue
        cell_preparers = preparers[cell["name"]]
        cell["approve"] = rates_model.approve_mode(
            user, roles, cell_preparers, GER, cell["name"], policy,
            close_policy_model.APPROVER_ROLES, close_policy_model.self_approval_problem)
        cell["edited_by"] = sorted(cell_preparers - {cell["owner"]})

    role_set = set(roles)
    return {
        "period": {"fiscal_year": fy, "fiscal_period": fp,
                   "period_code": period.get("period_code"), "status": period.get("status")},
        "rows": result["rows"],
        "unrequired": result["unrequired"],
        "summary": result["summary"],
        "pairs_error": pairs_error,
        "blockers": blockers,
        "threshold_pct": threshold_pct,
        "policy_gaps": close_policy_model.policy_gaps(policy, threshold_pct or 0),
        "self_approval": policy or None,
        "can_enter": bool(role_set & set(group_rates.PREFILL_ROLES))
        and period.get("status") == "Open",
        "can_approve": bool(role_set & set(close_policy_model.APPROVER_ROLES)),
        "quoted_per_options": _quoted_per_options(),
    }


def _reason(change_reason):
    """The Reason for Change, stripped; blank is no reason."""
    return (str(change_reason).strip() or None) if change_reason is not None else None


def _grain_text(from_currency, to_currency, rate_type, fy, fp):
    return f"{rate_type} {from_currency} → {to_currency} FY{fy} P{fp:02d}"


@frappe.whitelist(methods=["POST"])
def save_rate(fiscal_year, fiscal_period, from_currency, to_currency, rate_type, quote,
              quoted_per, change_reason=None, name=None):
    frappe.only_for(("EPM Analyst", "EPM Admin", "System Manager"))
    if rate_type not in group_rates.RATE_TYPES:
        frappe.throw(f"Rate Type {rate_type} is not a group rate type: "
                     f"use one of {', '.join(group_rates.RATE_TYPES)}.")
    key = _period(fiscal_year, fiscal_period)
    period = _period_row(key)
    fy, fp = key
    status = period.get("status")
    if status != "Open":
        frappe.throw(f"FY{fy} P{fp:02d} is {status}: group rates lock when their period "
                     "closes (reopen it first).")
    reason = _reason(change_reason)

    if not name:
        grain = {"to_currency": to_currency, "from_currency": from_currency,
                 "rate_type": rate_type, "fiscal_year": fy, "fiscal_period": fp}
        existing = frappe.get_all(GER, filters=dict(grain, docstatus=["in", [0, 1]]),
                                  fields=["name", "docstatus"])
        approved = [r["name"] for r in existing if r["docstatus"] == 1]
        if approved:
            frappe.throw(
                f"{approved[0]} is already the approved {rate_type} rate {from_currency} → "
                f"{to_currency} for FY{fy} P{fp:02d}; a change is a cancel and an amendment "
                "with a Reason for Change (Desk; E4-P7).")
        if existing:
            frappe.throw(f"{existing[0]['name']} is already a draft for this rate: edit it.")
        doc = frappe.get_doc({"doctype": GER, "to_currency": to_currency,
                              "from_currency": from_currency, "rate_type": rate_type,
                              "fiscal_year": fy, "fiscal_period": fp, "quote": quote,
                              "quoted_per": quoted_per, "change_reason": reason})
        doc.insert()
    else:
        doc = frappe.get_doc(GER, name)
        if doc.docstatus != 0:
            frappe.throw(
                f"{name} is {'approved' if doc.docstatus == 1 else 'cancelled'}, not a draft: "
                "a change is a cancel and an amendment with a Reason for Change (Desk; E4-P7).")
        theirs = (doc.from_currency, doc.to_currency, doc.rate_type,
                  int(doc.fiscal_year), int(doc.fiscal_period))
        asked = (from_currency, to_currency, rate_type, fy, fp)
        if theirs != asked:
            frappe.throw(f"{name} is the {_grain_text(*theirs)} rate, not "
                         f"{_grain_text(*asked)}: save it under its own key.")
        doc.quote = quote
        doc.quoted_per = quoted_per
        doc.change_reason = reason
        doc.save()

    return {"name": doc.name, "docstatus": doc.docstatus, "quote_label": doc.get("quote_label"),
            "source": doc.get("source")}


HER = "Historical Equity Rate"
OP = "Ownership Period"
HER_FIELDS = ["name", "consolidation_group", "data_area_id", "main_account", "rate_date",
              "historical_rate", "owner", "creation"]
OP_FIELDS = ["name", "consolidation_group", "data_area_id", "effective_date", "end_date",
             "ownership_pct", "consolidation_method", "owner", "creation"]


def _iso(value):
    """A datetime with the site's UTC offset (A55: Frappe stores it naive in
    the system time zone); a plain date stays a date."""
    if isinstance(value, datetime):
        return zoned_iso(value, frappe.utils.get_system_timezone())
    if isinstance(value, date):
        return value.isoformat()
    return None if value in (None, "") else str(value)


def _visible(docs, allowed):
    """``(visible, hidden_count)``. A draft is visible when its entity
    (``data_area_id``) is in ``allowed``, or ``allowed`` is None (#305-W2-10,
    W2-14; mirrors ``period_grid_model.period_grid``'s cut)."""
    if allowed is None:
        return docs, 0
    visible = [d for d in docs if d["data_area_id"] in allowed]
    return visible, len(docs) - len(visible)


def _drafts(doctype, fields):
    return frappe.get_all(doctype, filters={"docstatus": 0}, fields=fields,
                          order_by="creation asc", limit_page_length=0)


@frappe.whitelist(methods=["GET"])
def get_pending():
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    her_all = _drafts(HER, HER_FIELDS)
    ops_all = _drafts(OP, OP_FIELDS)
    allowed = allowed_entity_codes()
    her_visible, her_hidden = _visible(her_all, allowed)
    ops_visible, ops_hidden = _visible(ops_all, allowed)

    her = []
    for doc in her_visible:
        doc = dict(doc)
        doc["created"] = _iso(doc.pop("creation", None))
        doc["rate_date"] = _iso(doc.get("rate_date"))
        her.append(doc)
    ops = []
    for doc in ops_visible:
        doc = dict(doc)
        doc["created"] = _iso(doc.pop("creation", None))
        doc["effective_date"] = _iso(doc.get("effective_date"))
        doc["end_date"] = _iso(doc.get("end_date"))
        ops.append(doc)

    policy = frappe.db.get_single_value("Close Settings", "self_approval")

    preparers_by_name = {}
    preparers_by_name.update(
        self_approval.preparers_for(HER, {d["name"]: d["owner"] for d in her}))
    preparers_by_name.update(
        self_approval.preparers_for(OP, {d["name"]: d["owner"] for d in ops}))

    user = frappe.session.user
    roles = frappe.get_roles(user)
    items = rates_model.pending_items(
        her, ops, preparers_by_name, user, roles, policy,
        close_policy_model.APPROVER_ROLES, close_policy_model.self_approval_problem)

    return {
        "items": items,
        "counts": {HER: len(her), OP: len(ops), "hidden": her_hidden + ops_hidden},
        "self_approval": policy or None,
        "can_approve": bool(set(roles) & set(close_policy_model.APPROVER_ROLES)),
    }


def _ownership_date(value):
    """Mirror ``signoff_gate._date`` / ``mywork_api._date``: a period's
    ``start_date`` may arrive as a ``date``, a ``datetime`` or an ISO string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return None


def _cut(codes, allowed, key=lambda c: c):
    """``(visible, hidden_count)``: ``codes`` cut to ``allowed`` (#305-W2-10,
    W2-14; mirrors ``period_grid_model``'s cut, E2-6). ``allowed`` is None
    for an unrestricted caller."""
    if allowed is None:
        return list(codes), 0
    visible = [c for c in codes if key(c) in allowed]
    return visible, len(codes) - len(visible)


#: get_ownership (R01h): wording mirrors grid_api._regular_row.
OWNERSHIP_REGULAR_ONLY = (
    "FY%d P%02d is a %s period; the ownership screen covers Regular periods only: "
    "pick a Regular period."
)


@frappe.whitelist(methods=["GET"])
def get_ownership(fiscal_year, fiscal_period):
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key = _period(fiscal_year, fiscal_period)
    period = _period_row(key)
    if period.get("period_type") != "Regular":
        frappe.throw(
            OWNERSHIP_REGULAR_ONLY
            % (key[0], key[1], period.get("period_type") or "blank-type")
        )
    fy, fp = key
    start = _ownership_date(period.get("start_date"))

    leaves = frappe.get_all("Entity", filters={"is_group": 0, "status": "Active"},
                            pluck="name", limit_page_length=0)
    rows = frappe.get_all(
        "Ownership Period",
        filters={"docstatus": 1, "effective_date": ["<=", start], "data_area_id": ["is", "set"]},
        fields=["data_area_id", "effective_date", "end_date"], limit_page_length=0)
    tbs = frappe.get_all(
        "Trial Balance Submission",
        filters={"fiscal_year": fy, "fiscal_period": fp, "docstatus": 1},
        pluck="data_area_id", limit_page_length=0)

    cov = scope_model.covered(rows, start)
    blocking_all = sorted(scope_model.uncovered_with_tb(tbs, cov))
    scope = scope_model.in_scope(leaves, cov)
    out_of_scope_all = sorted(set(leaves) - scope - set(blocking_all))

    allowed = allowed_entity_codes()
    blocking_visible, blocking_hidden = _cut(blocking_all, allowed)
    out_of_scope_visible, out_of_scope_hidden = _cut(out_of_scope_all, allowed)
    in_scope_count = len(scope) if allowed is None else len(scope & allowed)

    start_iso = start.isoformat() if start else None
    blocking = [
        {"entity": e,
         "message": (
             f"{e} has a submitted trial balance for FY{fy} P{fp:02d} but no approved "
             f"ownership period covering {start_iso}: it is not consolidated. Record its "
             "ownership, or cancel the trial balance (#305-W2-2)."),
         "desk": "/app/ownership-period/new?data_area_id=" + quote(e)}
        for e in blocking_visible
    ]

    return {
        "period": {"fiscal_year": fy, "fiscal_period": fp},
        "start_date": start_iso,
        "blocking": blocking,
        "out_of_scope": out_of_scope_visible,
        "in_scope_count": in_scope_count,
        "can_record": bool(frappe.has_permission("Ownership Period", "create")),
        "hidden": blocking_hidden + out_of_scope_hidden,
        "blocking_hidden": blocking_hidden,
    }
