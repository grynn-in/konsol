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
writes nothing. Each visible Ownership Period item carries ``effect`` (O57;
story 4.2, C-O4): ``ownership_change.effect_for(doc)``, the structural
before/after the approval would make, or None for a draft without
``supersedes`` (a Desk "Record ownership" draft). That costs, per visible OP
draft with ``supersedes``, one read of its predecessor, one fiscal calendar
read and one ``signoff_gate.latest_signed_runs()``; a Desk draft costs none.
Pending OP drafts are few (live, 7 Oct: 0). A draft whose effect cannot be
read (a predecessor no longer approved) carries ``effect`` None and
``effect_error``, a sentence naming the draft, never a guessed effect; the
other items are built as normal (R52i, review S2). ``effect_error`` is None
on every other OP item.

``get_ownership()`` (GET, E406) lists, for the period, the entities with a
submitted trial balance but no ownership covering the period's start
(blocking, #305-W2-2) and the Active leaf entities out of scope for
information, using the one scope rule in ``scope_model`` (G01) and never
re-deriving it. A Viewer reads it with ``can_record`` False (#305-W2-10).
``can_change`` (R53e; #305-R52-4, U10e) is true exactly when
``save_ownership_change`` would admit the caller (``OWNERSHIP_SAVE_ROLES``,
and at least one Open Regular period); ``can_record`` stays the Desk
"Record ownership" link's test.
Refuses an undeclared period, and a non-Regular one (R01h; the ownership
screen covers Regular periods only, mirroring ``grid_api``'s refusal).
``blocking`` and ``out_of_scope`` are cut to ``allowed_entity_codes()``, with
a combined ``hidden`` count (W2-10, W2-14) and a ``blocking_hidden`` count
(R01h) naming only the hidden blocking entities, so a caller can tell a
hidden gap from a merely-hidden out-of-scope entity. The query is keyed to
the period's start date; moving it to the period's end waits on G09/G04
(W2-16, blocked). For a caller who may change (``can_change``) it also
carries ``change`` (O63): the ownership change form's choices, the entities
with their node's group and the Open Regular periods; ``None`` otherwise.

``preview_ownership_change(...)`` (GET, O55; story 4.2, #305-4.2-1,
#305-Q1-1) returns a proposed ownership change's refusals and structural
effect as data, read once through ``ownership_change`` and decided by
``ownership_change_model`` (C-O4). It refuses an entity the caller cannot see
before any read, lists a non-Regular period as a problem, and writes nothing.
"""
import importlib.util as _importlib_util
import os as _os
from datetime import date, datetime
from urllib.parse import quote

import frappe

from konsol import fiscal_calendar, group_rates
from konsol.close import close_policy_model, rates_model, scope_model, self_approval
from konsol.close.timefmt import zoned_iso
from konsol.entity_permissions import allowed_entity_codes


def _load_period_name():
    """konsol/close/period_name.py loaded by path (konsol#305 review-w5): the
    one "FY2025 P07" format, reachable even under the host tests' stub
    ``konsol.close`` package."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()


#: Who reads the Rates screen (#305-W2-3, W2-10). The Entity Accountant does not.
RATES_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")
GER = "Group Exchange Rate"
DOC_FIELDS = ["name", "from_currency", "to_currency", "rate_type", "quote", "quoted_per",
              "erp_quote", "docstatus", "owner", "modified", "change_reason", "source"]


def _period(fiscal_year, fiscal_period):
    try:
        return int(fiscal_year), int(fiscal_period)
    except (TypeError, ValueError):
        frappe.throw(f"Fiscal year {fiscal_year!r}, period {fiscal_period!r} is not a period: "
                     "pass the fiscal year and period as whole numbers.")


def _period_row(key, rows=None):
    """The declared period row for ``key``; ``rows`` is an already-read
    ``fiscal_period_rows()`` (O63 reads the calendar once), else it is read."""
    for row in (fiscal_calendar.fiscal_period_rows() if rows is None else rows):
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            return row
    frappe.throw("%s is not declared: declare it in EPM Fiscal Year." % period_name(*key))


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
    return f"{rate_type} {from_currency} → {to_currency} {period_name(fy, fp)}"


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
        frappe.throw(f"{period_name(fy, fp)} is {status}: group rates lock when their period "
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
                f"{to_currency} for {period_name(fy, fp)}; a change is a cancel and an amendment "
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
             "ownership_pct", "consolidation_method", "owner", "creation", "docstatus",
             "supersedes"]


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


#: O69: the roles ``save_ownership_change`` admits (its ``frappe.only_for``
#: literal; a test pins the two together). A pending Ownership Period item
#: carries ``edit`` only for a caller holding one of them, so the client never
#: decides editability (``get_ownership.can_record`` is a different test).
#: R53e: ``get_ownership.can_change`` is decided by the same tuple.
OWNERSHIP_SAVE_ROLES = ("EPM Analyst", "EPM Admin", "System Manager")


@frappe.whitelist(methods=["GET"])
def get_pending():
    """The pending Historical Equity Rate and Ownership Period drafts (story
    4.3; E405).

    O69 (story 4.2; wireframe-4.2.md section 1): each Ownership Period item
    carries ``edit`` (``rates_model.op_edit``): the node, the period whose
    first day is the draft's ``effective_date``, its pct and method, which
    O67's Edit loads and sends back with the item's ``name``; None for a
    Desk draft (no ``supersedes``), a caller ``save_ownership_change`` does
    not admit, or a day no single Regular period starts on. The listed
    drafts are already cut to the caller's entities, which is
    ``_name_refusal``'s scope rule, and each is a draft of its own node and
    first day, so the save accepts the ``edit`` it is given. The calendar is
    read once per call, and only when a visible draft has ``supersedes`` and
    the caller may save."""
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    her_all = _drafts(HER, HER_FIELDS)
    ops_all = _drafts(OP, OP_FIELDS)
    allowed = allowed_entity_codes()
    her_visible, her_hidden = _visible(her_all, allowed)
    ops_visible, ops_hidden = _visible(ops_all, allowed)
    user = frappe.session.user
    roles = frappe.get_roles(user)
    starts = None
    if set(roles) & set(OWNERSHIP_SAVE_ROLES) and any(d.get("supersedes") for d in ops_visible):
        starts = rates_model.regular_period_by_start(fiscal_calendar.fiscal_period_rows())

    her = []
    for doc in her_visible:
        doc = dict(doc)
        doc["created"] = _iso(doc.pop("creation", None))
        doc["rate_date"] = _iso(doc.get("rate_date"))
        her.append(doc)
    ops = []
    if ops_visible:
        from konsol.close import ownership_change  # lazy (C-X1)
    for doc in ops_visible:
        doc = dict(doc)
        # O57: the structural effect, read from the draft's ``supersedes``
        # before the dates are formatted; None for a Desk draft. R52i (review
        # S2): a draft whose effect cannot be read is an error on its own
        # item, never a refusal of the whole screen.
        try:
            doc["effect"], doc["effect_error"] = ownership_change.effect_for(doc), None
        except ValueError as e:
            doc["effect"] = None
            doc["effect_error"] = ("The pending ownership change %s cannot be shown: %s. "
                                   "Correct or delete the draft in Desk."
                                   % (doc["name"], str(e).rstrip(".")))
        doc["created"] = _iso(doc.pop("creation", None))
        doc["effective_date"] = _iso(doc.get("effective_date"))
        doc["end_date"] = _iso(doc.get("end_date"))
        doc["edit"] = rates_model.op_edit(doc, starts)
        ops.append(doc)

    policy = frappe.db.get_single_value("Close Settings", "self_approval")

    preparers_by_name = {}
    preparers_by_name.update(
        self_approval.preparers_for(HER, {d["name"]: d["owner"] for d in her}))
    preparers_by_name.update(
        self_approval.preparers_for(OP, {d["name"]: d["owner"] for d in ops}))

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
    "%s is a %s period; the ownership screen covers Regular periods only: "
    "pick a Regular period."
)


@frappe.whitelist(methods=["GET"])
def get_ownership(fiscal_year, fiscal_period):
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key = _period(fiscal_year, fiscal_period)
    calendar = fiscal_calendar.fiscal_period_rows()
    period = _period_row(key, calendar)
    if period.get("period_type") != "Regular":
        frappe.throw(
            OWNERSHIP_REGULAR_ONLY
            % (period_name(*key), period.get("period_type") or "blank-type")
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
             f"{e} has a submitted trial balance for {period_name(fy, fp)} but no approved "
             f"ownership period covering {start_iso}: it is not consolidated. Record its "
             "ownership, or cancel the trial balance (#305-W2-2)."),
         "desk": "/app/ownership-period/new?data_area_id=" + quote(e)}
        for e in blocking_visible
    ]

    can_record = bool(frappe.has_permission("Ownership Period", "create"))
    # R53e (#305-R52-4, U10e): the form follows the save, never ``can_record``
    # (a DocPerm customisation moves that one). True exactly when
    # ``save_ownership_change`` would admit the caller: a role in its literal
    # tuple, and an Open Regular period to start the change in (the save
    # refuses every other period). Decided once; ``change`` follows it.
    periods = _open_regular_periods(calendar)
    can_change = bool(set(frappe.get_roles()) & set(OWNERSHIP_SAVE_ROLES)) and bool(periods)
    return {
        "period": {"fiscal_year": fy, "fiscal_period": fp},
        "start_date": start_iso,
        "blocking": blocking,
        "out_of_scope": out_of_scope_visible,
        "in_scope_count": in_scope_count,
        "can_record": can_record,
        "hidden": blocking_hidden + out_of_scope_hidden,
        "blocking_hidden": blocking_hidden,
        "can_change": can_change,
        "change": _change_choices(periods, allowed) if can_change else None,
    }


def _open_regular_periods(calendar):
    """Every Regular period whose effective status is Open, ``{"fiscal_year",
    "fiscal_period", "label", "start_date"}`` (ISO), in calendar order: the
    periods an ownership change may start in (O63; the save's openness rule,
    ``ownership_change_model.problems`` rule 3). A missing status is never
    read as Open."""
    return [
        {"fiscal_year": int(r["fiscal_year"]), "fiscal_period": int(r["fiscal_period"]),
         "label": period_name(int(r["fiscal_year"]), int(r["fiscal_period"])),
         "start_date": _iso(_ownership_date(r.get("start_date")))}
        for r in sorted(calendar, key=lambda r: (int(r["fiscal_year"]), int(r["fiscal_period"])))
        if r.get("period_type") == "Regular" and r.get("status") == "Open"
    ]


def _change_choices(periods, allowed):
    """O63 (story 4.2; #305-4.2-1; C-O2, C-O3; wireframe-4.2.md section 1):
    the ownership change form's choices, so every choice it offers has a
    server source.

    - ``entities``: one item per node (``consolidation_group``,
      ``data_area_id``) with a submitted Ownership Period that names an
      entity, ``{"entity", "entity_name", "consolidation_group"}``, sorted by
      entity, then group. The group is the node's own: an entity on two nodes
      gives two items, never a guessed one. Only an entity that already has a
      submitted period can be changed (C-O3: a first ownership stays the Desk
      link). Cut to ``allowed`` without adding to ``hidden`` (the payload's
      existing rule already counts the hidden entities). Each item also
      carries ``current`` (R52j, review U4; wireframe section 1, the
      "Currently" line): the node's latest submitted period by
      ``effective_date`` (ties, which overlapping-period validation should
      prevent, go to the higher name), in ``ownership_change._as_current``'s
      shape (ISO dates, ``end_date`` None when open-ended). It comes from the
      same nodes read, with more fields. Once a period is chosen, the
      preview's ``current`` wins (R52r).
    - ``periods``: ``_open_regular_periods`` of the calendar
      ``get_ownership`` already read (R53e: computed once there, since
      ``can_change`` needs it too).

    Costs two reads (the submitted nodes, and the names of the visible
    entities on them), whatever the number of nodes; the name read is skipped
    when no node is visible."""
    from konsol.close import ownership_change  # lazy (C-X1)

    nodes = frappe.get_all(
        OP, filters={"docstatus": 1, "data_area_id": ["is", "set"]},
        fields=["consolidation_group", "data_area_id", "name", "effective_date", "end_date",
                "ownership_pct", "consolidation_method"], limit_page_length=0)
    latest = {}
    for r in nodes:
        node = (r["data_area_id"], r["consolidation_group"])
        held = latest.get(node)
        if held is None or (r["effective_date"], r["name"]) > (held["effective_date"],
                                                               held["name"]):
            latest[node] = r
    pairs = sorted(latest)
    pairs, _hidden = _cut(pairs, allowed, key=lambda p: p[0])
    names = {}
    if pairs:
        names = {r["name"]: r.get("entity_name") for r in frappe.get_all(
            "Entity", filters={"name": ["in", sorted({e for e, _g in pairs})]},
            fields=["name", "entity_name"], limit_page_length=0)}
    entities = [{"entity": e, "entity_name": names.get(e), "consolidation_group": g,
                 "current": ownership_change._as_current(latest[(e, g)])}
                for e, g in pairs]
    return {"entities": entities, "periods": periods}


#: preview_ownership_change (O55): the chosen period must be Regular. An
#: Opening P00 starts on P01's first day, so the model alone would read it as
#: a valid first day and silently describe a P01 change.
OWNERSHIP_CHANGE_REGULAR_ONLY = (
    "%s is the %s period, not a Regular one: an ownership change starts on the "
    "first day of a Regular period; pick a Regular period."
)


def _ownership_preview(fiscal_year, fiscal_period, consolidation_group, entity, ownership_pct,
                       consolidation_method, exclude=None):
    """``(ctx, problems, effect)`` of a proposed ownership change: the one
    reading the preview (O55) and the save (O56) share. The scope check comes
    first, before any read; corrupt data (``ValueError`` from
    ``ownership_change``) is thrown as a sentence. ``effect`` is None while
    any problem stands: the effect of a refused change is never guessed.

    ``exclude`` (O56) is the exact name of the draft being edited:
    ``ownership_change.context`` reports every draft on the node as pending,
    so that one draft is left out of ``pending_exists`` by name equality,
    never by a name pattern. Every other draft on the node still refuses."""
    allowed = allowed_entity_codes()
    if allowed is not None and entity not in allowed:
        frappe.throw(f"You cannot see entity {entity or '(blank)'}: ask an administrator for "
                     "an Entity permission on it, or pick an entity you can see.")
    key = _period(fiscal_year, fiscal_period)
    from konsol.close import ownership_change  # lazy (C-X1)

    try:
        ctx = ownership_change.context(consolidation_group, entity, *key)
    except ValueError as e:
        frappe.throw(str(e))
    # O69: the node's other drafts awaiting approval, as exact names (the
    # data behind the "already awaiting approval" sentence, never parsed
    # back out of it).
    others = sorted(r["name"] for r in ownership_change._node_periods(consolidation_group,
                                                                      entity)
                    if int(r["docstatus"] or 0) == 0 and r["name"] != exclude)
    ctx["pending"] = others
    if exclude:
        ctx["pending_exists"] = ", ".join(others) if others else None
    problems = []
    period_type = ctx["period"].get("period_type")
    if period_type != "Regular":
        problems.append(OWNERSHIP_CHANGE_REGULAR_ONLY
                        % (period_name(*key), period_type or "blank-type"))
    problems.extend(ownership_change.model.problems(
        ownership_change.change(ctx, ownership_pct, consolidation_method), ctx["current"],
        ctx["later_exists"], ctx["pending_exists"], ctx["period_rows"]))
    effect = None
    if not problems:
        try:
            effect = ownership_change.model.effect(
                ownership_change.change(ctx, ownership_pct, consolidation_method),
                ctx["current"], ctx["period_rows"], ctx["signed_keys"])
        except ValueError as e:
            frappe.throw(str(e))
    return ctx, problems, effect


@frappe.whitelist(methods=["GET"])
def preview_ownership_change(fiscal_year, fiscal_period, consolidation_group, entity,
                             ownership_pct, consolidation_method, name=None):
    """O55 (story 4.2; #305-4.2-1, #305-Q1-1; wireframe-4.2.md): the
    refusals and the structural effect of an ownership change starting on the
    chosen period's first day (C-O2), as data, so the form shows them as the
    user types. A Viewer may preview. Writes nothing.

    O66 (wireframe-4.2.md §1, "The Analyst can edit it until it is
    approved"): with ``name``, previews an edit of that draft. ``name`` is the
    same exact-name exclude as O56's save, so the draft being edited is not
    "already awaiting approval"; every other draft on the node still is. A
    ``name`` the save would refuse (not a draft, another node or another
    first day) is the first problem, and there is no effect. A draft of an
    entity the caller cannot see is refused without naming its entity.

    O69: ``pending`` is the sorted list of the node's other drafts awaiting
    approval (after the ``name`` exclude), the exact names the "already
    awaiting approval … edit that draft" problem names; ``[]`` when none.

    Returns ``{"problems": [...], "effect": {...} | None, "current": {...} | None,
    "pending": [...]}``.
    """
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    ctx, problems, effect = _ownership_preview(fiscal_year, fiscal_period, consolidation_group,
                                               entity, ownership_pct, consolidation_method,
                                               exclude=name)
    if name:
        doc = frappe.get_doc(OP, name)
        refusal = _name_refusal(doc, consolidation_group, entity, ctx["effective_date"])
        if refusal:
            problems = [refusal] + problems
            effect = None
    return {"problems": problems, "effect": effect, "current": ctx["current"],
            "pending": ctx["pending"]}


def _edit_refusal(doc, consolidation_group, entity, effective_date):
    """The sentence refusing an edit of ``doc`` through
    ``save_ownership_change``, or None. Only a draft of the same node and the
    same first day is edited: the name carries the effective date (autoname),
    so another period is a new draft, never a silent move."""
    status = int(doc.get("docstatus") or 0)
    if status != 0:
        return "%s is %s: record a new change instead." % (
            doc.name, "approved" if status == 1 else "cancelled")
    theirs = (doc.get("consolidation_group"), doc.get("data_area_id") or None,
              _iso(doc.get("effective_date")))
    asked = (consolidation_group, entity or None, effective_date)
    if theirs != asked:
        return ("%s is the change for %s in %s from %s, not %s in %s from %s: edit a draft "
                "under its own entity and period." % (
                    doc.name, theirs[1] or "the group node", theirs[0], theirs[2],
                    asked[1] or "the group node", asked[0], asked[2]))
    return None


def _name_refusal(doc, consolidation_group, entity, effective_date):
    """The one sentence refusing ``doc`` as the draft named for editing, shared
    by the preview (O66) and the save (O68), or None. A draft of an entity the
    caller cannot see (W2-10, fail closed) is refused first, naming only what
    the caller asked for, so neither its entity, its first day nor its status
    is told; otherwise ``_edit_refusal`` decides."""
    allowed = allowed_entity_codes()
    if allowed is not None and doc.get("data_area_id") not in allowed:
        return ("%s is not a draft you can edit here: pick a draft of %s in %s from %s."
                % (doc.name, entity or "the group node", consolidation_group, effective_date))
    return _edit_refusal(doc, consolidation_group, entity, effective_date)


@frappe.whitelist(methods=["POST"])
def save_ownership_change(fiscal_year, fiscal_period, consolidation_group, entity,
                          ownership_pct, consolidation_method, name=None):
    """O56 (story 4.2; R2: the Analyst drafts, the Close Lead approves;
    #305-4.2-1, #305-Q1-1; wireframe-4.2.md as drawn): save an ownership
    change as a draft Ownership Period starting on the chosen period's first
    day (C-O2), superseding the current period (``supersedes`` its name,
    ``superseded_end_date`` its end date). The draft ends where the current
    period ends, as the preview's effect says (``after.to``).

    With ``name``, edits that draft instead (same node, same first day); the
    draft being edited is left out of the "already awaiting approval" check.
    Every refusal is thrown before the write. ``insert()`` / ``save()`` carry
    no ignore flag, so the Ownership Period controller (O53) and Frappe's
    permissions decide the rest; nothing here submits or commits (the request
    commits). The signature names no ``docstatus``, ``supersedes``,
    ``end_date`` or deal field, so a forged key never reaches the document.

    Returns ``{"name", "docstatus": 0}``.
    """
    frappe.only_for(("EPM Analyst", "EPM Admin", "System Manager"))
    ctx, problems, _effect = _ownership_preview(
        fiscal_year, fiscal_period, consolidation_group, entity, ownership_pct,
        consolidation_method, exclude=name)
    doc = None
    if name:
        doc = frappe.get_doc(OP, name)
        refusal = _name_refusal(doc, consolidation_group, entity, ctx["effective_date"])
        if refusal:
            frappe.throw(refusal)
    if problems:
        frappe.throw(" ".join(problems))

    current = ctx["current"]
    values = {"ownership_pct": float(ownership_pct),
              "consolidation_method": consolidation_method,
              "supersedes": current["name"],
              "superseded_end_date": current["end_date"]}
    if doc is None:
        doc = frappe.get_doc(dict(values, doctype=OP, consolidation_group=consolidation_group,
                                  data_area_id=entity or None,
                                  effective_date=ctx["effective_date"],
                                  end_date=current["end_date"]))
        doc.insert()
    else:
        for field, value in values.items():
            setattr(doc, field, value)
        doc.save()
    return {"name": doc.name, "docstatus": doc.docstatus}
