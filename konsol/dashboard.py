"""
Konsolidat desk dashboard.

Builds a public Frappe Workspace named "Konsolidat" so the desk lands on
konsol-related shortcuts and workflow link cards — organized so every functional
process's doctypes/configs sit together (pipeline, model, reference, budgeting,
allocation, consolidation, assertions, settings).

Called best-effort from ``konsol.install.after_migrate`` so the dashboard is
recreated on every ``bench migrate`` (and therefore survives a clean redeploy).
Everything self-filters to the doctypes actually installed on the site, so it is
safe to run on any branch/phase — links to doctypes that don't exist yet are
simply skipped.

Two-tier naming: the DocType *name* is the singular technical identity; the
*business label* shown on cards/tiles is the plural, user-facing collection name
(``_LABELS``). Keep them aligned when renaming a doctype.
"""
import frappe
import json

WORKSPACE = "Konsolidat"
MODULE = "EPM"

# Business labels: doctype (singular) -> card/tile label (plural / user-facing).
_LABELS = {
    "Connector": "Connectors",
    "Connector Health": "Connector Health",
    "Pipeline": "Pipelines",
    "Pipeline Schedule": "Pipeline Schedules",
    "Pipeline Run": "Pipeline Runs",
    "Build Scope": "Build Scopes",
    "Build Model": "Build Models",
    "Build Approval": "Build Approvals",
    "Dataset": "Datasets",
    "Dimension": "Dimensions",
    "Dimension Mapping": "Dimension Mappings",
    "Measure": "Measures",
    "Reporting Hierarchy": "Reporting Hierarchies",
    "Reporting Hierarchy Member": "Reporting Hierarchy Members",
    "Scenario": "Scenarios",
    "Main Account Category": "Main Account Categories",
    "Cash Flow Category": "Cash Flow Categories",
    "Entity": "Entities",
    "Fiscal Period": "Fiscal Periods",
    "Period Status": "Period Statuses",
    "Budget Cycle": "Budget Cycles",
    "Budget Sheet": "Budget Sheets",
    "Budget Cost Center": "Budget Cost Centers",
    "Spread Profile": "Spread Profiles",
    "Allocation Driver": "Allocation Drivers",
    "Allocation Rule": "Allocation Rules",
    "Allocation Run": "Allocation Runs",
    "Consolidation Group": "Consolidation Groups",
    "Ownership Period": "Ownership Periods",
    "Historical Equity Rate": "Historical Equity Rates",
    "IC Elimination Rule": "IC Elimination Rules",
    "IC Balance": "IC Balances",
    "Consolidation Adjustment": "Consolidation Adjustments",
    "Assertion Run": "Assertion Runs",
    "EPM Settings": "EPM Settings",
}


def _label(dt):
    return _LABELS.get(dt, dt)


# External app shortcuts: (label, url, colour)
_URL_SHORTCUTS = [
    ("Konsol Exec", "/konsol-exec", "Teal"),
]

# Colourful top-row shortcut tiles — one per functional process: (doctype, colour)
_SHORTCUTS = [
    ("Pipeline Run", "Orange"),
    ("Dataset", "Blue"),
    ("Budget Cycle", "Teal"),
    ("Allocation Run", "Yellow"),
    ("Consolidation Group", "Pink"),
    ("Assertion Run", "Purple"),
    ("EPM Settings", "Green"),
]

# Link cards — one per functional process, doctypes grouped with their process.
# Every top-level konsol doctype appears in exactly one card. Child tables are
# excluded (they render inside their parents). Cards render in this order.
_CARDS = [
    ("Pipeline & Ingestion", [
        "Connector", "Connector Health", "Pipeline", "Pipeline Schedule",
        "Pipeline Run", "Build Scope", "Build Model", "Build Approval",
    ]),
    ("Model & Metadata", [
        "Dataset", "Dimension", "Dimension Mapping", "Measure",
        "Reporting Hierarchy", "Reporting Hierarchy Member", "Scenario",
    ]),
    ("Reference Data", [
        "Entity",
        "Main Account Category", "Cash Flow Category", "Fiscal Period",
        "Period Status",
    ]),
    ("Budgeting & Planning", [
        "Budget Cycle", "Budget Sheet", "Budget Cost Center", "Spread Profile",
    ]),
    ("Allocations", [
        "Allocation Driver", "Allocation Rule", "Allocation Run",
    ]),
    ("Consolidation", [
        "Consolidation Group", "Ownership Period", "Historical Equity Rate",
        "IC Elimination Rule", "IC Balance", "Consolidation Adjustment",
    ]),
    ("Assertions", [
        "Assertion Run",
    ]),
    ("Settings", [
        "EPM Settings",
    ]),
]


def _dt(name):
    return bool(frappe.db.exists("DocType", name))


def _workspace_needs_refresh():
    """True when an older workspace layout should be rebuilt into the current one."""
    if not frappe.db.exists("Workspace", WORKSPACE):
        return False
    ws = frappe.get_doc("Workspace", WORKSPACE)
    shortcut_labels = {s.label for s in (ws.shortcuts or [])}
    card_labels = {l.label for l in (ws.links or []) if l.type == "Card Break"}
    if "Konsol Exec" not in shortcut_labels:
        return True
    if "Konsol Control" in shortcut_labels:
        return True
    # Any pre-redesign card label present → rebuild into the 8-card process layout.
    if {"Budget", "EPM Registry", "EPM Models", "Data Pipeline", "CTA Drivers", "Close"} & card_labels:
        return True
    # New-layout signature card absent (but doctypes exist) → rebuild.
    if _dt("Dataset") and "Model & Metadata" not in card_labels:
        return True
    if ws.number_cards or ws.charts:
        return True
    content = json.loads(ws.content or "[]")
    for block in content:
        if block.get("type") in ("number_card", "chart"):
            return True
        if block.get("type") == "header" and "Overview" in (block.get("data") or {}).get("text", ""):
            return True
    return False


def setup_workspace(force=False):
    """Create the Konsolidat workspace if missing.

    Idempotent and self-filtering. ``force=True`` rebuilds the workspace even if
    it already exists (used for manual refreshes). When the layout changes, an
    existing workspace is rebuilt once so desk users see updates without a manual
    refresh.
    """
    if not _dt("Dataset"):
        # konsol doctypes not migrated yet — nothing to build.
        return

    if (force or _workspace_needs_refresh()) and frappe.db.exists("Workspace", WORKSPACE):
        frappe.delete_doc("Workspace", WORKSPACE, force=True, ignore_permissions=True)

    if not frappe.db.exists("Workspace", WORKSPACE):
        _create_workspace()
        frappe.logger().info(f"Created desk workspace: {WORKSPACE}")


def _create_workspace():
    url_shortcuts = list(_URL_SHORTCUTS)
    shortcuts = [s for s in _SHORTCUTS if _dt(s[0])]

    cards = []
    for label, doctypes in _CARDS:
        keep = [d for d in doctypes if _dt(d)]
        if keep:
            cards.append((label, keep))

    links = []
    for label, doctypes in cards:
        links.append({
            "label": label, "type": "Card Break", "link_count": len(doctypes),
            "hidden": 0, "is_query_report": 0, "onboard": 0,
        })
        for d in doctypes:
            links.append({
                "label": _label(d), "link_to": d, "link_type": "DocType", "type": "Link",
                "link_count": 0, "hidden": 0, "is_query_report": 0, "onboard": 0,
            })

    content = _build_content(url_shortcuts, shortcuts, cards)

    ws_shortcuts = [
        {"label": l, "type": "URL", "url": u, "color": c}
        for l, u, c in url_shortcuts
    ]
    ws_shortcuts += [
        {"label": _label(dt), "link_to": dt, "type": "DocType", "color": c, "doc_view": "List"}
        for dt, c in shortcuts
    ]

    frappe.get_doc({
        "doctype": "Workspace",
        "name": WORKSPACE,
        "title": WORKSPACE,
        "label": WORKSPACE,
        "module": MODULE,
        "icon": "tool",
        "public": 1,
        "is_hidden": 0,
        "sequence_id": 0.0,  # appears first in the sidebar -> default landing page
        "content": json.dumps(content),
        "shortcuts": ws_shortcuts,
        "links": links,
    }).insert(ignore_permissions=True)


def _build_content(url_shortcuts, shortcuts, cards):
    counter = {"i": 0}

    def cid():
        counter["i"] += 1
        return "kons%05d" % counter["i"]

    def header(text):
        return {"id": cid(), "type": "header",
                "data": {"text": f'<span class="h4"><b>{text}</b></span>', "col": 12}}

    content = [header("Konsolidat — EPM Platform")]
    for label, _u, _c in url_shortcuts:
        content.append({"id": cid(), "type": "shortcut",
                        "data": {"shortcut_name": label, "col": 3}})
    for dt, _c in shortcuts:
        content.append({"id": cid(), "type": "shortcut",
                        "data": {"shortcut_name": _label(dt), "col": 3}})
    content.append({"id": cid(), "type": "spacer", "data": {"col": 12}})
    content.append(header("Processes"))
    for label, _ in cards:
        content.append({"id": cid(), "type": "card",
                        "data": {"card_name": label, "col": 4}})
    return content
