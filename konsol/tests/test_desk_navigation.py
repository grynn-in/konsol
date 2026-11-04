"""The Konsol Desk navigation shipped for Frappe v16 (demo prep, 5 Nov 2026).

konsol ships a Sidebar (konsol/consolidation/sidebar/konsol), a home Workspace (konsol/consolidation/workspace/konsol)
and its Number Cards. Every link in them names a DocType, Report or Workspace
by string, and nothing in Frappe refuses a dead one at import: the item just
renders and fails when clicked. These tests fail instead.

Two traps this pins:
- a Sidebar or Workspace file is re-imported on migrate only when its
  ``modified`` changes, so an edit without a new timestamp silently does not
  ship (seen on the v16 copy: the Home row was missing until it was bumped);
- the desktop tile's route must be the shipped workspace.
"""
import ast
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return json.load(f)


def _doctypes():
    names = set()
    for path in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.json")):
        with open(path) as f:
            try:
                d = json.load(f)
            except ValueError:
                continue
        if isinstance(d, dict) and d.get("doctype") == "DocType":
            names.add(d["name"])
    return names


def _reports():
    names = set()
    for path in glob.glob(os.path.join(APP_DIR, "*", "report", "*", "*.json")):
        with open(path) as f:
            d = json.load(f)
        if d.get("doctype") == "Report":
            names.add(d["name"])
    return names


SIDEBAR = "consolidation/sidebar/konsol/konsol.json"
WORKSPACE = "consolidation/workspace/konsol/konsol.json"


def test_every_sidebar_link_resolves():
    doctypes, reports = _doctypes(), _reports()
    workspace = _load(WORKSPACE)["name"]
    for item in _load(SIDEBAR)["items"]:
        if item["type"] != "Link":
            continue
        kind, target = item["link_type"], item["link_to"]
        if kind == "DocType":
            assert target in doctypes, f"sidebar links missing DocType {target!r}"
        elif kind == "Report":
            assert target in reports, f"sidebar links missing Report {target!r}"
        elif kind == "Workspace":
            assert target == workspace, f"sidebar links unknown Workspace {target!r}"
        else:
            raise AssertionError(f"unexpected link_type {kind!r} for {target!r}")


def test_sidebar_claims_its_doctypes_and_starts_at_home():
    """``is_default_module`` is what routes a doctype's pages to this sidebar
    (frappe/boot.py build_entity_module_map); Home comes first."""
    items = _load(SIDEBAR)["items"]
    assert items[0]["link_type"] == "Workspace" and items[0]["label"] == "Home"
    for item in items:
        if item.get("link_type") == "DocType":
            assert item["is_default_module"] == 1, item["link_to"]


def test_no_dock_ships():
    """konsol has one sidebar, so a dock item only repeated the app logo as a
    second K in the rail (Deepak Pai, 5 Nov 2026: "it looks stupid"); with no
    dock the rail disappears and the logo shows once."""
    assert not os.path.exists(os.path.join(APP_DIR, "dock"))


def test_workspace_shortcuts_and_cards_resolve():
    doctypes, reports = _doctypes(), _reports()
    ws = _load(WORKSPACE)
    for s in ws["shortcuts"]:
        pool = doctypes if s["type"] == "DocType" else reports
        assert s["link_to"] in pool, f"workspace shortcut to missing {s['type']} {s['link_to']!r}"
    blocks = json.loads(ws["content"])
    shortcut_names = {s["label"] for s in ws["shortcuts"]}
    card_names = {c["number_card_name"] for c in ws["number_cards"]}
    for b in blocks:
        if b["type"] == "shortcut":
            assert b["data"]["shortcut_name"] in shortcut_names, b
        if b["type"] == "number_card":
            assert b["data"]["number_card_name"] in card_names, b
    for name in card_names:
        slug = name.lower().replace(" ", "_")
        card = _load(f"consolidation/number_card/{slug}/{slug}.json")
        assert card["name"] == name and card["is_standard"] == 1
        assert card["document_type"] in doctypes, card["document_type"]
        for flt in json.loads(card["filters_json"]):
            assert flt[0] == card["document_type"], flt


def test_desktop_tile_opens_the_workspace():
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    screen = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                  and getattr(n.targets[0], "id", None) == "add_to_apps_screen")
    workspace = _load(WORKSPACE)["name"].lower()
    assert screen[0]["route"] == f"/desk/{workspace}"
