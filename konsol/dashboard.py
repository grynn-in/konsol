"""
Konsolidat desk dashboard.

Builds a public Frappe Workspace named "Konsolidat" so the desk lands on
konsol-related links (models, pipeline, allocation, consolidation) instead of
the default Frappe workspaces, together with a few overview number cards and
charts.

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

# Colourful top-row shortcut tiles: (doctype, colour)
_SHORTCUTS = [
    ("EPM Settings", "Green"),
    ("Fact Table", "Blue"),
    ("Measure", "Cyan"),
    ("Connector", "Orange"),
    ("Pipeline Build Request", "Purple"),
    ("Allocation Rule", "Yellow"),
    ("Consolidation Group", "Pink"),
]

# Link cards grouped by module: (card label, [doctypes])
_CARDS = [
    ("EPM Models", [
        "Fact Table", "Measure", "Dimension", "Scenario Definition",
        "Budget Input", "Fiscal Period", "Spread Profile",
        "Dimension Mapping", "EPM Settings",
    ]),
    ("Data Pipeline", [
        "Connector", "Pipeline Build Request", "Pipeline Run",
        "Build Domain", "Gold Model",
    ]),
    ("Allocation", [
        "Allocation Rule", "Allocation Driver", "Allocation Run",
    ]),
    ("Consolidation", [
        "Consolidation Group", "Consolidation Adjustment", "IC Balance",
        "IC Elimination Rule", "Ownership Period", "Historical Equity Rate",
    ]),
]

# Overview number cards: (label, doctype, colour)
_NUMBER_CARDS = [
    ("Fact Tables", "Fact Table", "#449CF0"),
    ("Measures", "Measure", "#29CD42"),
    ("Dimensions", "Dimension", "#FFC107"),
    ("Connectors", "Connector", "#FF8C00"),
    ("Pipeline Runs", "Pipeline Run", "#7C4DFF"),
]

# Group-by charts: (name, doctype, group_by_field, display_type)
_CHARTS = [
    ("Fact Tables by Source Type", "Fact Table", "source_type", "Donut"),
    ("Measures by Type", "Measure", "cube_type", "Donut"),
]


def _dt(name):
    return bool(frappe.db.exists("DocType", name))


def _field(doctype, fieldname):
    return frappe.get_meta(doctype).has_field(fieldname)


def setup_workspace(force=False):
    """Create the Konsolidat workspace + overview cards/charts if missing.

    Idempotent and self-filtering. ``force=True`` rebuilds the workspace even if
    it already exists (used for manual refreshes; the after_migrate path leaves
    an existing workspace untouched so user customisations are preserved).
    """
    if not _dt("Fact Table"):
        # konsol doctypes not migrated yet — nothing to build.
        return

    created_cards = _ensure_number_cards()
    created_charts = _ensure_charts()

    if force and frappe.db.exists("Workspace", WORKSPACE):
        frappe.delete_doc("Workspace", WORKSPACE, force=True, ignore_permissions=True)

    if not frappe.db.exists("Workspace", WORKSPACE):
        _create_workspace(created_cards, created_charts)
        frappe.logger().info(f"Created desk workspace: {WORKSPACE}")


def _ensure_number_cards():
    names = []
    for label, doctype, color in _NUMBER_CARDS:
        if not _dt(doctype):
            continue
        names.append(label)
        if frappe.db.exists("Number Card", label):
            continue
        frappe.get_doc({
            "doctype": "Number Card",
            "name": label,
            "label": label,
            "type": "Document Type",
            "document_type": doctype,
            "function": "Count",
            "filters_json": "[]",
            "is_public": 1,
            "show_percentage_stats": 1,
            "stats_time_interval": "Daily",
            "color": color,
        }).insert(ignore_permissions=True)
    return names


def _ensure_charts():
    names = []
    for name, doctype, group_by, display in _CHARTS:
        if not _dt(doctype) or not _field(doctype, group_by):
            continue
        names.append(name)
        if frappe.db.exists("Dashboard Chart", name):
            continue
        frappe.get_doc({
            "doctype": "Dashboard Chart",
            "name": name,
            "chart_name": name,
            "chart_type": "Group By",
            "document_type": doctype,
            "group_by_type": "Count",
            "group_by_based_on": group_by,
            "type": display,
            "timeseries": 0,
            "filters_json": "[]",
            "is_public": 1,
            "number_of_groups": 0,
        }).insert(ignore_permissions=True)
    return names


def _create_workspace(card_names, chart_names):
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

    content = _build_content(shortcuts, cards, card_names, chart_names)

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
        "shortcuts": shortcuts and [
            {"label": l, "link_to": l, "type": "DocType", "color": c, "doc_view": "List"}
            for l, c in shortcuts
        ],
        "links": links,
        "charts": [{"chart_name": n, "label": n} for n in chart_names],
        "number_cards": [{"number_card_name": n, "label": n} for n in card_names],
    }).insert(ignore_permissions=True)


def _build_content(shortcuts, cards, card_names, chart_names):
    counter = {"i": 0}

    def cid():
        counter["i"] += 1
        return "kons%05d" % counter["i"]

    def header(text):
        return {"id": cid(), "type": "header",
                "data": {"text": f'<span class="h4"><b>{text}</b></span>', "col": 12}}

    content = [header("Konsolidat — EPM Platform")]
    for label, _ in shortcuts:
        content.append({"id": cid(), "type": "shortcut",
                        "data": {"shortcut_name": label, "col": 3}})
    content.append({"id": cid(), "type": "spacer", "data": {"col": 12}})

    if card_names:
        content.append(header("Overview"))
        for name in card_names:
            content.append({"id": cid(), "type": "number_card",
                            "data": {"number_card_name": name, "col": 3}})
    for name in chart_names:
        content.append({"id": cid(), "type": "chart",
                        "data": {"chart_name": name, "col": 6}})

    content.append({"id": cid(), "type": "spacer", "data": {"col": 12}})
    content.append(header("Models &amp; Masters"))
    for label, _ in cards:
        content.append({"id": cid(), "type": "card",
                        "data": {"card_name": label, "col": 4}})
    return content
