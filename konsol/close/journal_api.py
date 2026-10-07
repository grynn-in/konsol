"""Consolidation Journal read endpoint for the Adjustments screen (konsol#305
A05; stories 6.1, 6.2; #305-W3-3, W3-4 option A; #305-P21-1; W2-10).

``get_journals(fiscal_year, fiscal_period)`` (GET) returns everything the
screen needs and writes nothing:

- the period's journals (every docstatus: Draft, Pending Approval, Approved
  and Reversed), each with its lines, totals, duration label, status,
  preparer, statement effect and last rejection;
- ``reversing_in`` (story 6.5): the Approved journals of earlier periods
  whose reversal posts into this one, read-only — each with its origin
  period, a "Reverses here from <code>" label, and the reversal posting's
  lines (debit and credit swapped) and effect. One more header read; their
  lines come in the same line read;
- the drafting choices: the group(s) and their entities, the postable
  (Published, non-group) Main Accounts with their heading, and the reversal
  periods a new journal may name;
- what the caller may do: ``can_draft``, ``can_send``, ``can_edit_period``.

Journals are not entity-scoped (#305-P21-1): every caller who holds one of
``JOURNAL_ROLES`` sees every journal, whichever entities its lines name.

amended 3 Oct by the coordinator (E6-P1 option (c)): ``save_journal`` (A06)
and ``send_for_approval`` (A07) admit only ``DRAFT_ROLES``. So ``can_draft``
and ``can_send`` are each also False unless the caller holds one of
``DRAFT_ROLES`` — the screen never offers what those endpoints would refuse.

The number of reads does not depend on the number of journals: one read
each for the period rows, the journal headers, the lines, the accounts, the
groups and the rejections, plus one (or two, when a workflow is installed)
for the workflow, plus the declared journal dimensions (``declared_dimensions``:
one Dimension read and the line doctype's cached meta) and, only when one is
declared, at most three suggestion reads (konsolidat#245 option D).

This file never names the Close Event doctype (the one-writer check,
test_close_event_writer.py): rejections are read through
``close_event.latest_rejections`` only.
"""
import json

import frappe

from konsol import fiscal_calendar
from konsol.close import close_event, journal_model
from konsol.close.timefmt import zoned_iso
from konsol.tb_dimension_model import is_flag_on

import importlib.util as _importlib_util
import os as _os


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

JOURNAL = "Consolidation Journal"
LINE = "Consolidation Journal Line"

#: Who reads the Adjustments screen (#305-P21-1: journals are not entity-scoped).
JOURNAL_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "System Manager")

#: amended 3 Oct by the coordinator (E6-P1 option (c)): only these roles may
#: draft or send a journal. ``save_journal`` (A06) and ``send_for_approval``
#: (A07) admit only these too; a test pins the three literals together.
DRAFT_ROLES = ("EPM Analyst", "System Manager")

JOURNAL_FIELDS = [
    "name", "owner", "creation", "modified", "status", "docstatus",
    "consolidation_group", "adjustment_type", "description", "currency",
    "total_debit", "total_credit", "reverse_fiscal_year", "reverse_fiscal_period",
    "approved_by", "approved_at",
]
LINE_FIELDS = ["parent", "idx", "data_area_id", "main_account",
               "debit_amount", "credit_amount", "description"]
ACCOUNT_FIELDS = ["name", "account_name", "parent_account", "is_group", "statement_section"]
GROUP_FIELDS = ["name", "consolidation_group", "data_area_id", "reporting_currency"]

#: The journal's Adjustment Type Select (consolidation_journal.json).
ADJUSTMENT_TYPES = ("topside", "reclassification")

#: konsolidat#245 option D, CONTRACT (konsol-50, 3 Oct): the gating flag on
#: Dimension for journal lines. Read with ``is_flag_on`` (tb_dimension_model's
#: off-text handling), never ``if doc.in_journal`` — the same convention
#: dimension.py itself uses for ``in_trial_balance``.
JOURNAL_DIM_FLAG = "in_journal"


def declared_dimensions():
    """``[{"dimension_name", "label"}]``: the dimensions a journal line
    carries — Published Dimension rows gated ``in_journal`` on (konsolidat#245
    CONTRACT, read with ``is_flag_on``), legal names only, and only those whose
    field Consolidation Journal Line already has, ordered by
    ``dimension_name``. ``[]`` when none are declared — callers then behave
    exactly as before option D (#305 D01).

    The one reader: ``get_journals``, ``save_journal`` and
    ``approvals_api.queue_for`` all use it (D06), so the screens that draft a
    line and the one that approves it never disagree about its dimensions.

    - The name rule (konsol-50, 4 Oct): schema_apply gives a ticked
      Dimension whose name fails ``^dim_[a-z0-9_]+\\Z`` no Custom Field, and
      such a name is legal on a Dimension outside the trial balance
      (``business_unit``). It is left out — absent, not an error.
    - The field must exist (D06): the Custom Field sync is queued after the
      commit (konsol#135), so a Dimension can be Published and ticked before
      its field is there, and naming a missing field makes ``frappe.get_all``
      raise. Both rules are ``journal_model.journal_dimension_columns``, the
      same filter consolidation_journal's warehouse resync applies.

    One Dimension read plus the line doctype's (cached) meta.
    """
    rows = frappe.get_all(
        "Dimension", filters={"status": "Published"},
        fields=["dimension_name", "label", JOURNAL_DIM_FLAG],
        order_by="dimension_name asc", limit_page_length=0,
    )
    ticked = {r["dimension_name"]: r for r in rows
              if r.get("dimension_name") and is_flag_on(r.get(JOURNAL_DIM_FLAG))}
    keys = journal_model.journal_dimension_columns(
        list(ticked), frappe.get_meta(LINE).get_valid_columns())
    return [{"dimension_name": k, "label": ticked[k].get("label") or k} for k in keys]


def _dimension_suggestions(dim_names):
    """``{dimension_name: [suggestion, ...]}`` for each name in ``dim_names``:
    Published Reporting Hierarchy Member leaves (``is_group`` 0) under a
    Published Reporting Hierarchy for that dimension, plus Published
    Dimension Mapping ``canonical_value`` for that dimension; deduped,
    sorted. Three reads total (never per dimension, never per line) — none at
    all when ``dim_names`` is empty.

    This list is a convenience only (konsolidat#247: never refuse a typed
    value, never create a member or mapping from one), so it reads no
    effective-dating: every Published leaf is offered, whichever tranche it
    is effective in.
    """
    if not dim_names:
        return {}
    suggestions = {name: set() for name in dim_names}

    hierarchies = frappe.get_all(
        "Reporting Hierarchy", filters={"status": "Published", "dimension": ["in", list(dim_names)]},
        fields=["name", "dimension"], limit_page_length=0,
    )
    dim_by_hierarchy = {h["name"]: h["dimension"] for h in hierarchies}
    if dim_by_hierarchy:
        members = frappe.get_all(
            "Reporting Hierarchy Member",
            filters={"reporting_hierarchy": ["in", list(dim_by_hierarchy)], "is_group": 0},
            fields=["reporting_hierarchy", "member_code"], limit_page_length=0,
        )
        for member in members:
            dim = dim_by_hierarchy.get(member["reporting_hierarchy"])
            code = member.get("member_code")
            if dim in suggestions and code:
                suggestions[dim].add(code)

    mappings = frappe.get_all(
        "Dimension Mapping", filters={"status": "Published", "dimension": ["in", list(dim_names)]},
        fields=["dimension", "canonical_value"], limit_page_length=0,
    )
    for mapping in mappings:
        dim = mapping.get("dimension")
        value = mapping.get("canonical_value")
        if dim in suggestions and value:
            suggestions[dim].add(value)

    return {name: sorted(values) for name, values in suggestions.items()}


def _period_key(fiscal_year, fiscal_period):
    try:
        return int(fiscal_year), int(fiscal_period)
    except (TypeError, ValueError):
        frappe.throw(f"Fiscal year {fiscal_year!r}, period {fiscal_period!r} is not a period: "
                     "pass the fiscal year and period as whole numbers.")


def _find_period(key, period_rows):
    for row in period_rows:
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            return row
    frappe.throw("%s is not a declared period: declare it in EPM Fiscal Year." % period_name(*key))


def _title(description):
    for line in (description or "").splitlines():
        line = line.strip()
        if line:
            return line
    return "(no description)"


def _iso(value):
    if value is None:
        return None
    return zoned_iso(value, frappe.utils.get_system_timezone())


def _number(value):
    """A Float/Currency column as a JSON-safe float (MariaDB may hand back a
    Decimal); None stays None."""
    return None if value is None else float(value)


def _accounts():
    """{code: {"account_name", "heading", "heading_name", "statement_section"}}
    of the postable (non-group) Published Main Accounts, one read (A01, A05
    facts). Do not use ``group_chart.chart_accounts``: its FIELDS has no
    ``parent_account``."""
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


def _groups():
    """``[{"consolidation_group", "reporting_currency", "entities"}]``, one
    read of every Consolidation Group row (a root has a blank
    ``data_area_id``, consolidation_journal.py's ``_BLANK`` pattern)."""
    rows = frappe.get_all("Consolidation Group", fields=GROUP_FIELDS, limit_page_length=0)
    roots = {}
    members = {}
    for row in rows:
        if row.get("data_area_id"):
            members.setdefault(row["consolidation_group"], []).append(row["data_area_id"])
        else:
            roots.setdefault(row["consolidation_group"], row)
    return [
        {
            "consolidation_group": cg,
            "reporting_currency": root.get("reporting_currency"),
            "entities": sorted(members.get(cg, [])),
        }
        for cg, root in roots.items()
    ]


def _workflow_info():
    """``{"installed", "first_state", "send_role"}``. One read for the active
    Workflow's name and state field; a second, only when one exists, for its
    states and transitions (A05 facts: "workflow 1 (plus 1 for its child
    rows when installed)")."""
    row = frappe.db.get_value(
        "Workflow", {"document_type": JOURNAL, "is_active": 1},
        ["name", "workflow_state_field"], as_dict=True,
    )
    if not row or not row.get("name"):
        return {"installed": False, "first_state": "Draft", "send_role": None}
    wf = frappe.get_cached_doc("Workflow", row["name"])
    states = wf.states or []
    first_state = states[0].state if states else "Draft"
    transition = next(
        (t for t in (wf.transitions or [])
         if t.state == first_state and t.action == "Send for Approval"),
        None,
    )
    return {
        "installed": True,
        "first_state": first_state,
        "send_role": transition.allowed if transition else None,
    }


def _line_out(line, accounts, dim_keys=()):
    out = {
        "idx": line.get("idx"),
        "data_area_id": line.get("data_area_id"),
        "main_account": line.get("main_account"),
        "account_name": accounts.get(line.get("main_account"), {}).get("account_name"),
        "debit_amount": _number(line.get("debit_amount")),
        "credit_amount": _number(line.get("credit_amount")),
        "description": line.get("description"),
    }
    # konsolidat#245 option D: a declared dim key is always present on the
    # line (D01 stores '' for a missing/None value); guard with `or ""` too,
    # so a zero-declared-dimensions caller never sees a dim key at all.
    for key in dim_keys:
        out[key] = line.get(key) or ""
    return out


def _reverse(header):
    ry = header.get("reverse_fiscal_year") or 0
    rp = header.get("reverse_fiscal_period") or 0
    if not ry and not rp:
        return None
    return {"fiscal_year": int(ry), "fiscal_period": int(rp)}


@frappe.whitelist(methods=["GET"])
def get_journals(fiscal_year, fiscal_period):
    frappe.only_for(("EPM Admin", "EPM Analyst", "EPM User", "System Manager"))
    key = _period_key(fiscal_year, fiscal_period)
    period_rows = fiscal_calendar.fiscal_period_rows()
    period = _find_period(key, period_rows)
    fy, fp = key

    headers = frappe.get_all(
        JOURNAL,
        filters={"fiscal_year": fy, "fiscal_period": fp, "docstatus": ["in", [0, 1, 2]]},
        fields=JOURNAL_FIELDS,
        order_by="creation asc",
        limit_page_length=0,
    )
    declared = declared_dimensions()
    dim_keys = tuple(d["dimension_name"] for d in declared)

    # konsol#305 story 6.5: the Approved journals of earlier periods whose
    # reversal posts into this one. Only a submitted journal is in the
    # warehouse (consolidation_journal.resync_staging reads docstatus 1), so
    # only it reverses; a cancelled one's reversal vanishes with it (D2-2).
    reversing_headers = frappe.get_all(
        JOURNAL,
        filters={"reverse_fiscal_year": fy, "reverse_fiscal_period": fp, "docstatus": 1},
        fields=JOURNAL_FIELDS + ["fiscal_year", "fiscal_period"],
        order_by="creation asc",
        limit_page_length=0,
    )

    names = [h["name"] for h in headers] + [h["name"] for h in reversing_headers]
    lines = []
    if names:
        lines = frappe.get_all(
            LINE,
            filters={"parenttype": JOURNAL, "parent": ["in", names]},
            fields=LINE_FIELDS + list(dim_keys),
            order_by="parent asc, idx asc",
            limit_page_length=0,
        )
    lines_by_parent = {}
    for line in lines:
        lines_by_parent.setdefault(line["parent"], []).append(line)

    accounts = _accounts()
    groups = _groups()
    wf = _workflow_info()
    suggestions_by_dim = _dimension_suggestions(dim_keys)
    dimensions = [
        {"key": d["dimension_name"], "label": d["label"],
         "suggestions": suggestions_by_dim.get(d["dimension_name"], [])}
        for d in declared
    ]

    roles = set(frappe.get_roles(frappe.session.user))
    draft_capable = bool(roles & set(DRAFT_ROLES))
    can_draft = bool(frappe.has_permission(JOURNAL, "create")) and draft_capable
    can_send = (
        wf["installed"] and wf["send_role"] is not None
        and wf["send_role"] in roles and draft_capable
    )

    draft_names = [h["name"] for h in headers if int(h["docstatus"]) == 0]
    rejections = close_event.latest_rejections(JOURNAL, draft_names)

    journals = []
    for header in headers:
        header_lines = lines_by_parent.get(header["name"], [])
        last_rejection = None
        if int(header["docstatus"]) == 0:
            rejection = rejections.get(header["name"])
            if rejection:
                last_rejection = {
                    "reason": rejection["reason"],
                    "actor": rejection["actor"],
                    "at": _iso(rejection["at"]),
                }
        journals.append({
            "name": header["name"],
            "title": _title(header.get("description")),
            "description": header.get("description"),
            "adjustment_type": header.get("adjustment_type"),
            "status": header.get("status"),
            "docstatus": int(header["docstatus"]),
            "consolidation_group": header.get("consolidation_group"),
            "currency": header.get("currency"),
            "total_debit": _number(header.get("total_debit")),
            "total_credit": _number(header.get("total_credit")),
            "duration": journal_model.duration_label(
                header.get("reverse_fiscal_year"), header.get("reverse_fiscal_period"), period_rows),
            "reverse": _reverse(header),
            "preparer": header.get("owner"),
            "created": _iso(header.get("creation")),
            "modified": _iso(header.get("modified")),
            "approved_by": header.get("approved_by"),
            "approved_at": _iso(header.get("approved_at")),
            "lines": [_line_out(line, accounts, dim_keys) for line in header_lines],
            "effect": journal_model.statement_effect(header_lines, accounts),
            "last_rejection": last_rejection,
        })

    reversing_in = []
    for header in reversing_headers:
        origin_fy, origin_fp = int(header["fiscal_year"]), int(header["fiscal_period"])
        origin = next((r for r in period_rows
                       if (int(r["fiscal_year"]), int(r["fiscal_period"])) == (origin_fy, origin_fp)),
                      None)
        posting = journal_model.reversal_lines(lines_by_parent.get(header["name"], []))
        reversing_in.append({
            "name": header["name"],
            "title": _title(header.get("description")),
            "description": header.get("description"),
            "adjustment_type": header.get("adjustment_type"),
            "consolidation_group": header.get("consolidation_group"),
            "currency": header.get("currency"),
            "total_debit": _number(header.get("total_credit")),
            "total_credit": _number(header.get("total_debit")),
            "origin": {"fiscal_year": origin_fy, "fiscal_period": origin_fp,
                       "code": origin.get("period_code") if origin else None},
            "label": journal_model.reverses_here_label(origin_fy, origin_fp, period_rows),
            "approved_by": header.get("approved_by"),
            "approved_at": _iso(header.get("approved_at")),
            "lines": [_line_out(line, accounts, dim_keys) for line in posting],
            "effect": journal_model.statement_effect(posting, accounts),
        })

    return {
        "period": {
            "fiscal_year": fy, "fiscal_period": fp,
            "code": period.get("period_code"), "status": period.get("status"),
            "period_type": period.get("period_type"),
        },
        "journals": journals,
        "reversing_in": reversing_in,
        "groups": groups,
        "accounts": accounts,
        "dimensions": dimensions,
        "reversal_choices": journal_model.reversal_choices(fy, fp, period_rows),
        "workflow_installed": wf["installed"],
        "first_state": wf["first_state"],
        "can_draft": can_draft,
        "can_send": can_send,
        "can_edit_period": period.get("status") == "Open",
    }


def _reversal_number(value, label):
    """A reversal year or period from a form post: blank or None is 0 (a
    blank Int reads as 0), anything else must be a whole number."""
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        frappe.throw(f"The reversal {label} {value!r} is not a whole number: "
                     "name the reversal year and period, or leave both blank.")


def _request_lines(lines, dim_keys=()):
    """The request's lines kept to ``journal_model.LINE_KEYS`` plus the
    declared ``dim_keys`` (konsolidat#245 option D); a forged key in a line
    (``docstatus``, ``parent``, ``name``, or an undeclared/illegal dimension
    key) or bad JSON is refused with a sentence, before any document is
    built."""
    if isinstance(lines, str):
        try:
            lines = json.loads(lines)
        except ValueError:
            frappe.throw("The journal's lines are not valid JSON: send a list of lines, "
                         "each with data_area_id, main_account, debit_amount, "
                         "credit_amount and description.")
    rows, problems = journal_model.clean_lines(lines, dim_keys)
    if problems:
        frappe.throw("<br>".join(problems))
    return rows


# Save a new draft, or the named Draft (konsol#305 A06; story 6.1; #305-W3-3
# durations; E6-P14). No ``**kwargs``: Frappe drops request keys this function
# does not name, so a forged status, approver, docstatus, workflow state or
# owner never reaches it. The document goes through ``insert()`` / ``save()``
# with no ignore flag, so the doctype decides balance, entities, accounts and
# the reversal pair, and Frappe's create/write permission still applies.
# amended 3 Oct by the coordinator (E6-P1 option (c)): only ``DRAFT_ROLES``.
@frappe.whitelist(methods=["POST"])
def save_journal(fiscal_year, fiscal_period, consolidation_group, adjustment_type, description,
                 lines, reverse_fiscal_year=None, reverse_fiscal_period=None, name=None):
    frappe.only_for(("EPM Analyst", "System Manager"))
    if adjustment_type not in ADJUSTMENT_TYPES:
        frappe.throw(f"Adjustment Type {adjustment_type} is not a journal type: "
                     f"use one of {', '.join(ADJUSTMENT_TYPES)}.")
    dim_keys = tuple(d["dimension_name"] for d in declared_dimensions())
    rows = _request_lines(lines, dim_keys)
    key = _period_key(fiscal_year, fiscal_period)
    period_rows = fiscal_calendar.fiscal_period_rows()
    period = _find_period(key, period_rows)
    fy, fp = key
    status = period.get("status")
    if status != "Open":
        frappe.throw("%s is %s: a journal is drafted in an open period." % (period_name(fy, fp), status))
    ry = _reversal_number(reverse_fiscal_year, "year")
    rp = _reversal_number(reverse_fiscal_period, "period")
    problem = journal_model.reversal_problem(fy, fp, ry, rp, period_rows)
    if problem:
        frappe.throw(problem)

    header = {
        "consolidation_group": consolidation_group,
        "adjustment_type": adjustment_type,
        "description": description,
        "reverse_fiscal_year": ry,
        "reverse_fiscal_period": rp,
    }
    if not name:
        doc = frappe.get_doc(dict(header, doctype=JOURNAL, fiscal_year=fy, fiscal_period=fp,
                                  lines=rows))
        doc.insert()
    else:
        doc = frappe.get_doc(JOURNAL, name)
        if int(doc.docstatus) != 0:
            frappe.throw(f"{name} is approved; a correction is a new journal or a Reverse.")
        first_state = _workflow_info()["first_state"]
        if doc.status and doc.status != first_state:
            frappe.throw(f"{name} is {doc.status}: it is waiting for approval: the Close Lead "
                         f"rejects it back to {first_state} before it changes.")
        theirs = (int(doc.fiscal_year), int(doc.fiscal_period))
        if theirs != key:
            frappe.throw("%s is in %s, not %s: a journal's period does not change; draft a new "
                         "journal in %s." % (name, period_name(*theirs), period_name(*key),
                                             period_name(*key)))
        for field, value in header.items():
            doc.set(field, value)
        doc.set("lines", [])
        for row in rows:
            doc.append("lines", row)
        doc.save()

    return {
        "name": doc.name,
        "docstatus": int(doc.docstatus),
        "status": doc.status,
        "total_debit": _number(doc.get("total_debit")),
        "total_credit": _number(doc.get("total_credit")),
    }


#: E6-P15: the sentence ``send_for_approval`` refuses with on a site that has
#: no active journal workflow (live on 3 Oct).
NO_WORKFLOW = ("The journal workflow is not installed on this site (migrate installs it); "
               "the Close Lead approves a draft directly from Approvals.")


# Send the named Draft for approval (konsol#305 A07; story 6.1; R2; E6-P15).
# The signature is exactly ``(name)``: a forged action, status or docstatus
# never reaches it. Frappe's ``apply_workflow`` reloads the document, checks
# the caller holds the transition's role, and saves it, so the doctype's
# validate re-checks the balance; its refusal propagates uncaught. Sending is
# not an approval and writes no event: self-approval is judged at Approve.
# amended 3 Oct by the coordinator (E6-P1 option (c)): only ``DRAFT_ROLES``.
@frappe.whitelist(methods=["POST"])
def send_for_approval(name):
    frappe.only_for(("EPM Analyst", "System Manager"))
    wf = _workflow_info()
    if not wf["installed"]:
        frappe.throw(NO_WORKFLOW)
    doc = frappe.get_doc(JOURNAL, name)
    if int(doc.docstatus) != 0:
        frappe.throw(f"{name} is {doc.status or 'submitted'}: an approved or reversed journal "
                     "is not sent again; a correction is a new journal or a Reverse.")
    first_state = wf["first_state"]
    if doc.status and doc.status != first_state:
        frappe.throw(f"{name} is {doc.status}: it was already sent; the Close Lead approves "
                     f"it or rejects it back to {first_state}.")
    key = (int(doc.fiscal_year), int(doc.fiscal_period))
    period = _find_period(key, fiscal_calendar.fiscal_period_rows())
    status = period.get("status")
    if status != "Open":
        frappe.throw("%s is %s: a journal is sent for approval in an open period."
                     % (period_name(*key), status))

    from frappe.model.workflow import apply_workflow

    doc = apply_workflow(doc, "Send for Approval") or frappe.get_doc(JOURNAL, name)
    return {"name": doc.name, "status": doc.status}
