"""Konsol home API: who is looking, the fiscal navigator, and one month's close.

Three read-only GET endpoints for konsol-exec's workspace (F7, 12 Sep 2026):

  whoami        roles, job titles and entity scope of the session user
  period_tree   fiscal years with their fourteen periods and close state
  month         one period: the eight-stage lane, the viewer's work queue
                ("mine") and what they wait on ("waiting"), and system health

Only users holding a Konsol role get an answer. Stage counts are group-wide
context; the entity codes inside them and the connector details are only
shown to group roles (Close Lead, Group Accountant, System) or trimmed to the
viewer's own entities. Queue rows are the viewer's own work, filtered by role
and entity scope, and every action says whether this user may take it now,
so the UI never offers a button the server would refuse. The server still
refuses; this only avoids offering.
"""
from __future__ import annotations

import frappe
from frappe.utils import getdate, today

from konsol import home_model as M
from konsol import period_status
from konsol.entity_permissions import allowed_entity_codes, assigned_entities, subtree_codes

KONSOL_ROLES = {role for role, _ in M.TITLES}
CLOSE_LEAD = {"EPM Admin"}
GROUP = {"EPM Analyst"}
WIDE = CLOSE_LEAD | GROUP | {"System Manager"}
TB_OWNER = {"Entity Accountant"}
BUDGET_BASE = {"Entity Accountant", "Budget Submitter"}
REVIEWERS = {"Budget Controller": "challenge", "Budget Manager": "management", "Budget Approver": "board"}


def _roles(user=None):
    user = user or frappe.session.user
    if user == "Administrator":
        # get_roles gives Administrator every role, which would fill its queue
        # with every entity's trial balance and budget sheet. Treat it as the
        # Close Lead who also runs the system.
        return CLOSE_LEAD | GROUP | {"System Manager"}
    return set(frappe.get_roles(user))


def _require_konsol_user(user=None):
    user = user or frappe.session.user
    if user == "Administrator" or _roles(user) & KONSOL_ROLES:
        return
    frappe.throw("Konsol needs an EPM or budget role. Ask your administrator for access.", frappe.PermissionError)


def _desk(doctype, name=None, **query):
    slug = doctype.lower().replace(" ", "-")
    path = f"/app/{slug}/{name}" if name else f"/app/{slug}"
    if query:
        from urllib.parse import urlencode
        path += "?" + urlencode(query)
    return path


def _can(doctype, ptype="read", doc=None):
    try:
        return bool(frappe.has_permission(doctype, ptype, doc=doc))
    except Exception:
        return False


def _action(label, doctype, ptype="read", name=None, doc=None, note=None, **query):
    """A queue button, with whether this user may press it. ``note`` rides
    along on an allowed action the server takes but whose outcome it can't
    complete yet (an approval in a closed period)."""
    if ptype == "create" and name is None:
        name = "new"   # /app/<doctype>/new?field=value prefills the new form
    allowed = _can(doctype, ptype, doc=doc)
    reason = None if allowed else "You don't have permission for this."
    return {"label": label, "href": _desk(doctype, name, **query), "allowed": allowed, "reason": reason,
            "note": note if allowed else None}


def _item(item_id, state, title, detail="", stage=None, who=None, action=None, entity=None):
    return {"id": item_id, "state": state, "title": title, "detail": detail, "stage": stage,
            "who": who, "entity": entity, "action": action}


def _parse_period(fiscal_year, fiscal_period):
    try:
        fy, p = int(fiscal_year), int(fiscal_period)
    except (TypeError, ValueError):
        frappe.throw("fiscal_year and fiscal_period must be numbers", frappe.ValidationError)
    if not (1900 < fy < 3000 and 0 <= p <= 13):
        frappe.throw(f"No such period: FY{fy} period {p}", frappe.ValidationError)
    return fy, p


@frappe.whitelist(methods=["GET"])
def whoami():
    user = frappe.session.user
    _require_konsol_user(user)
    roles = _roles(user)
    allowed = allowed_entity_codes(user)
    full_name = frappe.utils.get_fullname(user) or user
    titles = M.job_titles(roles) or ["Viewer"]
    return {
        "user": user,
        "full_name": full_name,
        "initials": M.initials(full_name),
        "roles": sorted(roles & KONSOL_ROLES),
        "titles": titles,
        "title": titles[0],
        # None = every entity; a list = only these (possibly empty)
        "entities": None if allowed is None else sorted(allowed),
        "can": {
            "approve": bool(roles & (CLOSE_LEAD | {"System Manager"})),
            "system": "System Manager" in roles,
        },
    }


@frappe.whitelist(methods=["GET"])
def period_tree():
    _require_konsol_user()
    now = getdate(today())
    current = now.year
    years = {current - 1, current, current + 1}

    statuses = {}
    for r in frappe.get_all("Period Status", fields=["fiscal_year", "fiscal_period", "status"],
                            limit_page_length=0):
        try:
            key = (int(r.fiscal_year), int(r.fiscal_period))
        except (TypeError, ValueError):
            continue
        statuses[key] = r.status
        years.add(key[0])

    cycles = {}
    for c in frappe.get_all("Budget Cycle", filters={"docstatus": ["<", 2]},
                            fields=["name", "fiscal_year", "status", "deadline"], order_by="creation desc"):
        if c.fiscal_year:
            cycles.setdefault(int(c.fiscal_year), c)
            years.add(int(c.fiscal_year))

    periods = sorted(int(p) for p in frappe.get_all("Fiscal Period", pluck="fiscal_period")
                     if p is not None and 0 <= int(p) <= 13) or list(range(14))

    out = []
    for fy in sorted(years, reverse=True):
        rows = []
        for p in periods:
            status = statuses.get((fy, p), period_status.OPEN)
            start = M.period_start(fy, p)
            rows.append({"fiscal_period": p, "code": M.period_code(p), "label": M.period_label(fy, p),
                         "status": status, "state": M.period_state(status, start, now)})
        cycle = cycles.get(fy)
        out.append({
            "fiscal_year": fy,
            "label": f"FY{fy}",
            "kind": M.year_kind(fy, current),
            "periods": rows,
            "budget": ({"name": cycle.name, "status": cycle.status,
                        "deadline": str(cycle.deadline) if cycle.deadline else None} if cycle else None),
        })
    return {"years": out, "current": {"fiscal_year": current, "fiscal_period": now.month}}


def _context(fy, p, start):
    """Every row the month needs, read once."""
    leaves = {e.name: e.entity_name for e in frappe.get_all(
        "Entity", filters={"is_group": 0, "status": "Active"}, fields=["name", "entity_name"],
        limit_page_length=0)}
    covered = set()
    for o in frappe.get_all("Ownership Period",
                            filters={"docstatus": 1, "effective_date": ["<=", start], "data_area_id": ["is", "set"]},
                            fields=["data_area_id", "end_date"], limit_page_length=0):
        if not o.end_date or getdate(o.end_date) >= start:
            covered.add(o.data_area_id)
    in_close = set(leaves) & covered

    connectors = frappe.get_all("Connector", fields=["name", "connector_name", "enabled", "last_sync_status",
                                                     "last_sync_at"], limit_page_length=0)
    live = [c.name for c in connectors if c.enabled]
    fed = set(frappe.get_all("Connector Legal Entity",
                             filters={"parenttype": "Connector", "parent": ["in", live]},
                             pluck="entity_id")) if live else set()

    period = {"fiscal_year": fy, "fiscal_period": p}
    tbs = frappe.get_all("Trial Balance Submission", filters={**period, "docstatus": ["<", 2]},
                         fields=["name", "data_area_id", "docstatus", "validation_status"], limit_page_length=0)
    adjustments = frappe.get_all("Consolidation Adjustment", filters={**period, "docstatus": ["<", 2]},
                                 fields=["name", "status", "data_area_id", "adjustment_type", "debit_amount",
                                         "credit_amount", "owner"], limit_page_length=0)
    builds = frappe.get_all("Build Approval", filters={"build_scope": ["in", ["consolidation", "full"]]},
                            fields=["name", "workflow_state", "requested_by", "creation", "completed_at"],
                            order_by="creation desc", limit=1)
    runs = frappe.get_all("Assertion Run", filters=period,
                          fields=["name", "status", "passed", "failed", "total", "signoff_status"],
                          order_by="creation desc", limit=1)
    return {
        "leaves": leaves,
        "in_close": in_close,
        "uncovered": set(leaves) - covered,
        "connectors": connectors,
        "expected_tb": in_close - fed,
        "via_connector": in_close & fed,
        "tbs": tbs,
        "ownership_drafts": frappe.get_all("Ownership Period", filters={"docstatus": 0},
                                           fields=["name", "data_area_id"], limit_page_length=0),
        "rate_drafts": frappe.get_all("Historical Equity Rate", filters={"docstatus": 0},
                                      fields=["name", "data_area_id"], limit_page_length=0),
        "ic": frappe.get_all("IC Balance", filters={**period, "docstatus": ["<", 2]},
                             fields=["name", "docstatus", "selling_entity", "buying_entity"], limit_page_length=0),
        "adjustments": adjustments,
        "build": builds[0] if builds else None,
        "pending_builds": frappe.get_all("Build Approval", filters={"workflow_state": "Pending Review"},
                                         fields=["name", "build_scope", "risk_level", "requested_by", "creation"],
                                         order_by="creation", limit_page_length=0),
        "assertion": runs[0] if runs else None,
        "allocation_drafts": frappe.get_all("Allocation Run", filters={**period, "docstatus": 0},
                                            fields=["name"], limit_page_length=0),
    }


def _stages(ctx, status, tracked_build):
    tbs = ctx["tbs"]
    by_status = {}
    for a in ctx["adjustments"]:
        by_status[a.status] = by_status.get(a.status, 0) + 1
    assertions = M.assertions_stage(ctx["assertion"])
    return [
        M.source_stage(ctx["connectors"]),
        M.tb_stage(ctx["expected_tb"], {t.data_area_id for t in tbs if t.docstatus == 1},
                   {t.data_area_id for t in tbs if t.docstatus == 0}, ctx["via_connector"]),
        M.ownership_stage(ctx["uncovered"], len(ctx["ownership_drafts"]), len(ctx["rate_drafts"])),
        M.ic_stage(sum(1 for i in ctx["ic"] if i.docstatus == 1), sum(1 for i in ctx["ic"] if i.docstatus == 0)),
        M.adjustments_stage(by_status),
        M.consolidate_stage(ctx["build"], tracked=tracked_build),
        assertions,
        M.signoff_stage(status, assertions["state"]),
    ]


def _money(a):
    return f"{max(a.debit_amount or 0, a.credit_amount or 0):,.2f}"


def _queue(fy, p, ctx, stages, status, user):
    roles = _roles(user)
    lead = bool(roles & CLOSE_LEAD)
    group = bool(roles & GROUP)
    system = "System Manager" in roles
    label = M.period_label(fy, p)
    period_open = status == period_status.OPEN
    # In a closed period, disable only what the server refuses and annotate
    # what it allows but can't complete (M.closed_period, #149). Each queue
    # link still opens its desk form, so it stays enabled with a note that the
    # approval or submit will be refused: an adjustment, IC balance or
    # allocation run can still be rejected, edited or deleted there, and a
    # trial balance draft only deleted, by a viewer with the delete right (the
    # note says who). The trial balance upload, which the server refuses, is
    # not offered in a closed period.
    closed = None if period_open else f"{label} is {status.lower()}."
    by_id = {s["id"]: s for s in stages}
    mine, waiting = [], []

    if lead:
        for b in ctx["pending_builds"]:
            mine.append(_item(f"build:{b.name}", "paused", f"Approve {b.build_scope} build",
                              f"{(b.risk_level or '').capitalize()} risk · requested by "
                              f"{frappe.utils.get_fullname(b.requested_by) if b.requested_by else 'the system'}",
                              stage=6, action=_action("Review", "Build Approval", "write", b.name)))
        for a in ctx["adjustments"]:
            if a.status == "Pending Approval":
                mine.append(_item(f"adj:{a.name}", "paused", f"Approve {a.adjustment_type} adjustment",
                                  f"{a.data_area_id} · {_money(a)}", stage=5, entity=a.data_area_id,
                                  who=frappe.utils.get_fullname(a.owner),
                                  action=_action("Review", "Consolidation Adjustment", "submit", a.name,
                                                 **M.closed_period("Consolidation Adjustment", closed, "approve"))))
        for o in ctx["ownership_drafts"]:
            mine.append(_item(f"own:{o.name}", "incomplete", "Approve ownership change", o.data_area_id or "",
                              stage=3, entity=o.data_area_id,
                              action=_action("Review", "Ownership Period", "submit", o.name)))
        for r in ctx["allocation_drafts"]:
            mine.append(_item(f"alloc:{r.name}", "incomplete", "Approve allocation run", r.name, stage=5,
                              action=_action("Review", "Allocation Run", "submit", r.name,
                                             **M.closed_period("Allocation Run", closed, "approve"))))
        a = by_id["assertions"]
        if a["state"] == "error":
            mine.append(_item("assertions", "error", "Close assertions failed", a["summary"], stage=7,
                              action=_action("Open results", "Assertion Run", "read", a.get("run"))))
        s = by_id["signoff"]
        may_close = _can("Period Status", "write")
        if s["state"] == "ready":
            mine.append(_item("signoff", "ready", f"Sign off {label}", "Locks the period against further change",
                              stage=8, action={"label": "Sign off", "step": "signoff", "allowed": may_close,
                                               "reason": None if may_close else "You don't have permission for this."}))
        elif s["state"] == "waiting" and period_open:
            mine.append(_item("signoff", "waiting", f"Sign off {label}", "Locks the period against further change",
                              stage=8, action={"label": "Sign off", "step": "signoff", "allowed": False,
                                               "reason": "Opens when close assertions are green."}))

    if group:
        for a in ctx["adjustments"]:
            if a.status == "Draft":
                mine.append(_item(f"adj:{a.name}", "incomplete", f"Draft {a.adjustment_type} adjustment",
                                  f"{a.data_area_id} · {_money(a)}", stage=5, entity=a.data_area_id,
                                  action=_action("Send for approval", "Consolidation Adjustment", "write", a.name,
                                                 **M.closed_period("Consolidation Adjustment", closed, "be approved"))))
            elif a.status == "Pending Approval" and not lead:
                waiting.append(_item(f"adj:{a.name}", "waiting", f"{a.adjustment_type.capitalize()} adjustment",
                                     f"{a.data_area_id} · {_money(a)}", stage=5, who="Close Lead",
                                     entity=a.data_area_id))
        for e in sorted(ctx["uncovered"]):
            mine.append(_item(f"owner:{e}", "incomplete", f"Ownership missing for {e}",
                              "No ownership period covers this month", stage=3, entity=e,
                              action=_action("Add ownership", "Ownership Period", "create", None,
                                             data_area_id=e)))
        for r in ctx["rate_drafts"]:
            mine.append(_item(f"rate:{r.name}", "incomplete", "Submit historical rate", r.data_area_id or "",
                              stage=3, entity=r.data_area_id,
                              action=_action("Open", "Historical Equity Rate", "submit", r.name)))
        for i in ctx["ic"]:
            if i.docstatus == 0:
                mine.append(_item(f"ic:{i.name}", "incomplete", "Submit intercompany balance",
                                  f"{i.selling_entity} and {i.buying_entity}", stage=4,
                                  action=_action("Open", "IC Balance", "submit", i.name,
                                                 **M.closed_period("IC Balance", closed))))

    if lead or group:
        missing = by_id["trial_balances"].get("missing") or []
        if missing:
            waiting.append(_item("tb:missing", "incomplete", f"Trial balances · {len(missing)} missing",
                                 ", ".join(missing), stage=2, who="Entity Accountants",
                                 action=_action("Open list", "Trial Balance Submission", "read")))

    # Entity rows only for entities the user is explicitly scoped to. An
    # unscoped user (a group role, or a Budget Submitter with no assignment)
    # works from the group view; listing every entity here would be noise
    # at best and, for a submitter, a list of entities they cannot touch.
    # Built from the explicit assignment, not allowed_entity_codes: that
    # returns "everything" for a System Manager, who may still hold an entity.
    scoped = set() if user == "Administrator" else subtree_codes(assigned_entities(user))

    if roles & TB_OWNER:
        by_entity = {}
        for t in ctx["tbs"]:
            by_entity.setdefault(t.data_area_id, []).append(t)
        for e in sorted(ctx["expected_tb"] & scoped):
            docs = by_entity.get(e, [])
            done = next((t for t in docs if t.docstatus == 1), None)
            draft = next((t for t in docs if t.docstatus == 0), None)
            name = ctx["leaves"].get(e) or e
            if done:
                mine.append(_item(f"tb:{e}", "done", f"Trial balance · {e}", f"{name} · submitted", stage=2,
                                  entity=e, action=_action("View", "Trial Balance Submission", "read", done.name)))
            elif draft:
                # In a closed period the draft can only be deleted: say so only
                # to a viewer who may (an Entity Accountant may not).
                can_delete = bool(closed) and _can("Trial Balance Submission", "delete", doc=draft.name)
                mine.append(_item(f"tb:{e}", "incomplete", f"Trial balance · {e}", f"{name} · draft, not submitted",
                                  stage=2, entity=e,
                                  action=_action("Submit", "Trial Balance Submission", "submit", draft.name,
                                                 **M.closed_period("Trial Balance Submission", closed,
                                                                   can_delete=can_delete))))
            elif period_open:
                mine.append(_item(f"tb:{e}", "incomplete", f"Trial balance · {e}", f"{name} · not uploaded",
                                  stage=2, entity=e,
                                  action=_action("Upload trial balance", "Trial Balance Submission", "create", None,
                                                 data_area_id=e, fiscal_year=fy, fiscal_period=p)))

    reviewer_layers = [layer for role, layer in REVIEWERS.items() if role in roles]
    base_entities = sorted(set(ctx["leaves"]) & scoped) if roles & BUDGET_BASE else []
    if reviewer_layers or base_entities:
        cycles = frappe.get_all("Budget Cycle", filters={"docstatus": 0, "status": "Open"},
                                fields=["name", "fiscal_year", "deadline"], order_by="fiscal_year")
        sheets = {}
        if cycles and base_entities:
            for s in frappe.get_all("Budget Sheet",
                                    filters={"cycle": ["in", [c.name for c in cycles]], "layer": "base",
                                             "data_area_id": ["in", base_entities]},
                                    fields=["name", "cycle", "data_area_id", "annual_total"], limit_page_length=0):
                sheets[(s.cycle, s.data_area_id)] = s
        for c in cycles:
            due = f"due {c.deadline}" if c.deadline else ""
            for layer in reviewer_layers:
                mine.append(_item(f"budget:{c.name}:{layer}", "incomplete", f"Budget FY{c.fiscal_year} · {layer} round",
                                  due, action=_action("Open cycle", "Budget Cycle", "read", c.name)))
            for e in base_entities:
                sheet = sheets.get((c.name, e))
                detail = f"{sheet.annual_total:,.2f} so far" if sheet and sheet.annual_total else "Not started"
                mine.append(_item(f"budget:{c.name}:{e}", "incomplete", f"Budget FY{c.fiscal_year} · {e} base",
                                  " · ".join(x for x in (detail, due) if x), entity=e,
                                  action=(_action("Open", "Budget Sheet", "write", sheet.name) if sheet else
                                          _action("Start", "Budget Sheet", "create", None, cycle=c.name,
                                                  data_area_id=e, layer="base"))))

    if system:
        for c in ctx["connectors"]:
            if c.enabled and c.last_sync_status == "Failed":
                mine.append(_item(f"conn:{c.name}", "error", f"Connector {c.connector_name or c.name} failed",
                                  f"Last attempt {c.last_sync_at}" if c.last_sync_at else "", stage=1,
                                  action=_action("Open connector", "Connector", "read", c.name)))
        unassigned = _entity_accountants_without_entities()
        if unassigned:
            mine.append(_item("access:unassigned", "incomplete",
                              f"{len(unassigned)} Entity Accountant{'s' if len(unassigned) > 1 else ''} with no entity",
                              ", ".join(unassigned[:5]), action=_action("Assign entities", "User Permission",
                                                                        "create", None, allow="Entity")))

    return {"mine": M.ordered(mine), "waiting": M.ordered(waiting)}


def _entity_accountants_without_entities():
    holders = set(frappe.get_all("Has Role", filters={"parenttype": "User", "role": "Entity Accountant"},
                                 pluck="parent"))
    if not holders:
        return []
    enabled = set(frappe.get_all("User", filters={"name": ["in", list(holders)], "enabled": 1}, pluck="name"))
    assigned = set(frappe.get_all("User Permission", filters={"allow": "Entity", "user": ["in", list(enabled)]},
                                  pluck="user")) if enabled else set()
    return sorted(enabled - assigned)


def _health(ctx, wide):
    from konsol.control_api import _worker_healthy
    build = ctx["build"]
    return {
        "worker": _worker_healthy(),
        # Connector names and build ids are operating detail for group roles.
        "connectors": [{"name": c.name, "label": c.connector_name or c.name, "status": c.last_sync_status,
                        "at": str(c.last_sync_at) if c.last_sync_at else None}
                       for c in ctx["connectors"] if c.enabled] if wide else [],
        "last_build": ({"name": build.name, "state": build.workflow_state,
                        "at": str(build.completed_at or build.creation)} if build and wide else None),
    }


@frappe.whitelist(methods=["GET"])
def month(fiscal_year, fiscal_period):
    user = frappe.session.user
    _require_konsol_user(user)
    fy, p = _parse_period(fiscal_year, fiscal_period)
    now = getdate(today())
    start = M.period_start(fy, p)
    status = period_status.get_status(fy, p)
    closed = frappe.db.get_value("Period Status", {"fiscal_year": str(fy), "fiscal_period": p},
                                 ["closed_by", "closed_on"], as_dict=True) or {}
    ctx = _context(fy, p, start)
    # Builds carry no period. Every open period shows the latest one, saying
    # so; a closed period's lane does not borrow a later build.
    stages = _stages(ctx, status, tracked_build=status == period_status.OPEN)
    queue = _queue(fy, p, ctx, stages, status, user)

    wide = bool(_roles(user) & WIDE)
    if not wide:
        # Counts stay; the entity codes behind them are trimmed to the viewer's
        # own entities (None = the viewer may already read every entity).
        allowed = allowed_entity_codes(user)
        if allowed is not None:
            for s in stages:
                if "missing" in s:
                    s["missing"] = [e for e in s["missing"] if e in allowed]

    return {
        "period": {
            "fiscal_year": fy, "fiscal_period": p, "code": M.period_code(p), "label": M.period_label(fy, p),
            "status": status, "state": M.period_state(status, start, now),
            "closed_by": frappe.utils.get_fullname(closed.get("closed_by")) if closed.get("closed_by") else None,
            "closed_on": str(closed.get("closed_on")) if closed.get("closed_on") else None,
            "entities_in_close": len(ctx["in_close"]),
        },
        "stages": stages,
        "mine": queue["mine"],
        "waiting": queue["waiting"],
        "health": _health(ctx, wide),
    }
