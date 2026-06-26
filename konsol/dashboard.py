"""
Konsolidat desk dashboard.

Builds a public Frappe Workspace named "Konsolidat" so the desk lands on
konsol-related shortcuts and workflow link cards (budget, registry, pipeline,
allocation, consolidation) instead of the default Frappe workspaces.

Called best-effort from ``konsol.install.after_migrate`` so the dashboard is
recreated on every ``bench migrate`` (and therefore survives a clean redeploy).
Everything self-filters to the doctypes actually installed on the site, so it is
safe to run on any branch/phase — links to doctypes that don't exist yet are
simply skipped.
"""
import frappe
import json

WORKSPACE = "Konsolidat"
MODULE = "EPM"

# External app shortcuts: (label, url, colour)
_URL_SHORTCUTS = [
    ("Konsol Exec", "/konsol-exec", "Teal"),
]

# Colourful top-row shortcut tiles: (doctype, colour) — ordered by user workflow
_SHORTCUTS = [
    ("Budget Cycle", "Teal"),
    ("Fact Table", "Blue"),
    ("Measure", "Cyan"),
    ("Connector", "Orange"),
    ("Pipeline Build Request", "Purple"),
    ("Allocation Rule", "Yellow"),
    ("Consolidation Group", "Pink"),
    ("EPM Settings", "Green"),
]

# Link cards grouped by konsol workflow: Budget → Registry → Pipeline → Allocation → Consolidation
_CARDS = [
    ("Budget", [
        "Scenario Definition", "Budget Cycle", "Budget Sheet",
        "Fiscal Period", "Spread Profile",
    ]),
    ("EPM Registry", [
        "Fact Table", "Measure", "Dimension",
        "Dimension Mapping", "Reporting Hierarchy", "Reporting Hierarchy Member",
        "EPM Settings",
    ]),
    ("Data Pipeline", [
        "Connector", "Connector Health", "Pipeline Build Request", "Pipeline Run",
        "Build Domain", "Gold Model",
    ]),
    ("Allocation", [
        "Allocation Rule", "Allocation Driver", "Allocation Run",
    ]),
    ("Consolidation", [
        "Consolidation Group", "Ownership Period", "Historical Equity Rate",
        "Consolidation Adjustment", "IC Balance", "IC Elimination Rule", "Close Run",
    ]),
    # CTA drivers: the configurable inputs that move the Currency Translation
    # Adjustment plug (gold_fx_revaluation). Grouped for quick access when
    # investigating why CTA changed for a group/period. Doctypes may also appear
    # in other cards — workspace links are just shortcuts.
    ("CTA Drivers", [
        "Consolidation Group", "Ownership Period", "Historical Equity Rate",
        "Reporting Hierarchy",
    ]),
]


def _dt(name):
    return bool(frappe.db.exists("DocType", name))


def _workspace_needs_refresh():
    """True when an older workspace layout should be rebuilt."""
    if not frappe.db.exists("Workspace", WORKSPACE):
        return False
    ws = frappe.get_doc("Workspace", WORKSPACE)
    shortcut_labels = {s.label for s in (ws.shortcuts or [])}
    doctype_shortcuts = {s.link_to for s in (ws.shortcuts or []) if s.type == "DocType"}
    card_labels = {l.label for l in (ws.links or []) if l.type == "Card Break"}
    if "Konsol Exec" not in shortcut_labels:
        return True
    if "Konsol Control" in shortcut_labels:
        return True
    if _dt("Budget Cycle") and "Budget Cycle" not in doctype_shortcuts:
        return True
    if "EPM Models" in card_labels:
        return True
    if _dt("Historical Equity Rate") and "CTA Drivers" not in card_labels:
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
    if not _dt("Fact Table"):
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
                "label": d, "link_to": d, "link_type": "DocType", "type": "Link",
                "link_count": 0, "hidden": 0, "is_query_report": 0, "onboard": 0,
            })

    content = _build_content(url_shortcuts, shortcuts, cards)

    ws_shortcuts = [
        {"label": l, "type": "URL", "url": u, "color": c}
        for l, u, c in url_shortcuts
    ]
    ws_shortcuts += [
        {"label": l, "link_to": l, "type": "DocType", "color": c, "doc_view": "List"}
        for l, c in shortcuts
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
    for label, _, _c in url_shortcuts:
        content.append({"id": cid(), "type": "shortcut",
                        "data": {"shortcut_name": label, "col": 3}})
    for label, _ in shortcuts:
        content.append({"id": cid(), "type": "shortcut",
                        "data": {"shortcut_name": label, "col": 3}})
    content.append({"id": cid(), "type": "spacer", "data": {"col": 12}})
    content.append(header("Workflows"))
    for label, _ in cards:
        content.append({"id": cid(), "type": "card",
                        "data": {"card_name": label, "col": 4}})
    return content
