"""The Konsol home workspace's close band and period close checklist (konsol#407 phase 1).

Source checks, no site: the band's Custom number cards call whitelisted
methods of konsol/close/overview_api.py; the checklist is a Custom HTML
Block the app installs on every migrate; the four Document Type cards the
band replaces are retired (their reports stay reachable through the
workspace's shortcuts). The live behaviour is verified on the test stack.
"""
import ast
import glob
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE = os.path.join(APP_DIR, "consolidation", "workspace", "konsol", "konsol.json")
CARDS_DIR = os.path.join(APP_DIR, "consolidation", "number_card")
API = os.path.join(APP_DIR, "close", "overview_api.py")
BLOCK_DIR = os.path.join(APP_DIR, "consolidation", "custom_html_block", "konsol_close_checklist")
BLOCK = "Konsol Close Checklist"
RETIRED = ("Trial Balances Submitted", "Rates Awaiting Approval",
           "Equity Rates Awaiting Approval", "Journals Awaiting Approval")
BAND = ("Close Status", "Trial Balances In", "Waiting for My Approval",
        "IC Pairs over Tolerance", "TB Exceptions Declared", "Warehouse Freshness")
#: The roles every overview method admits (grid_api.get_readiness's and
#: approvals_api.get_queue's set: no Entity Accountant, E2-7).
ROLES = {"EPM Admin", "EPM Analyst", "EPM User", "System Manager"}


def _read(path):
    with open(path) as f:
        return f.read()


def _ws():
    return json.loads(_read(WORKSPACE))


def _slug(name):
    return name.lower().replace(" ", "_")


def _card(name):
    return json.loads(_read(os.path.join(CARDS_DIR, _slug(name), _slug(name) + ".json")))


def _whitelisted():
    tree = ast.parse(_read(API))
    out = {}
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for dec in fn.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "whitelist"):
                methods = next(ast.literal_eval(k.value) for k in dec.keywords if k.arg == "methods")
                out[fn.name] = (methods, fn)
    return out


def _roles_of(fn):
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "only_for"):
            return set(ast.literal_eval(node.args[0]))
    return None


def test_the_band_is_six_custom_cards_on_the_workspace():
    ws = _ws()
    names = [c["number_card_name"] for c in ws["number_cards"]]
    assert names == list(BAND), names
    blocks = [b["data"]["number_card_name"] for b in json.loads(ws["content"])
              if b["type"] == "number_card"]
    assert blocks == list(BAND), blocks


def test_each_band_card_calls_a_gated_overview_method():
    """The number card widget calls a Custom card's method with frappe.xcall,
    which POSTs; each method is read-only and opens with only_for."""
    methods = _whitelisted()
    for name in BAND:
        card = _card(name)
        assert card["type"] == "Custom" and card["is_standard"] == 1, name
        assert card["module"] == "Consolidation", name
        assert json.loads(card["filters_json"]) == [], name
        module, _, fn = card["method"].rpartition(".")
        assert module == "konsol.close.overview_api", card["method"]
        assert fn in methods, card["method"]
        assert methods[fn][0] == ["POST"], (fn, methods[fn][0])
        assert _roles_of(methods[fn][1]) == ROLES, fn


def test_each_band_card_is_hidden_from_a_role_the_method_refuses():
    """Frappe shows a Custom card only to a user who can read its
    document_type (number_card.has_permission), so each names a doctype the
    Entity Accountant cannot read: the card is not shown, rather than shown
    and failing."""
    for name in BAND:
        dt = _card(name)["document_type"]
        (path,) = glob.glob(os.path.join(APP_DIR, "*", "doctype", _slug(dt), _slug(dt) + ".json"))
        readers = {p["role"] for p in json.loads(_read(path))["permissions"] if p.get("read")}
        assert "Entity Accountant" not in readers, (name, dt)
        assert ROLES <= readers | {"System Manager"}, (name, dt, readers)


def test_the_retired_cards_are_gone_and_a_patch_deletes_them():
    ws = _ws()
    assert not set(RETIRED) & {c["number_card_name"] for c in ws["number_cards"]}
    for name in RETIRED:
        assert not os.path.exists(os.path.join(CARDS_DIR, _slug(name))), name
    patches = _read(os.path.join(APP_DIR, "patches.txt")).split()
    assert "konsol.patches.retire_approval_number_cards" in patches
    source = _read(os.path.join(APP_DIR, "patches", "retire_approval_number_cards.py"))
    for name in RETIRED:
        assert repr(name) in source or '"%s"' % name in source, name


def test_the_retired_cards_reports_stay_reachable_through_shortcuts():
    targets = {(s["type"], s["link_to"]) for s in _ws()["shortcuts"]}
    for need in [("DocType", "Trial Balance Submission"), ("DocType", "Group Exchange Rate"),
                 ("DocType", "Historical Equity Rate"), ("DocType", "Consolidation Journal")]:
        assert need in targets, need


def test_the_workspace_was_bumped():
    """A shipped Workspace re-imports on migrate only when its ``modified``
    changes; this edit is dated 7 Nov 2026."""
    assert _ws()["modified"] >= "2026-11-07", _ws()["modified"]
    for name in BAND:
        assert _card(name)["modified"] >= "2026-11-07", name


def test_the_checklist_block_is_on_the_workspace_and_installed():
    ws = _ws()
    assert [b["custom_block_name"] for b in ws["custom_blocks"]] == [BLOCK]
    blocks = [b for b in json.loads(ws["content"]) if b["type"] == "custom_block"]
    assert [b["data"]["custom_block_name"] for b in blocks] == [BLOCK]
    for ext in ("html", "js", "css"):
        assert os.path.isfile(os.path.join(BLOCK_DIR, "konsol_close_checklist." + ext)), ext
    install = _read(os.path.join(APP_DIR, "install.py"))
    hooks = _read(os.path.join(APP_DIR, "hooks.py"))
    assert "install_custom_blocks" in install
    assert "konsol.desk_blocks.install_custom_blocks" in hooks  # after_install too


def test_the_block_renders_server_text_as_text():
    """No server string is ever parsed as HTML: the script builds nodes and
    sets textContent, and links only to a /desk/ route."""
    js = _read(os.path.join(BLOCK_DIR, "konsol_close_checklist.js"))
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", ".html(", "document.write",
                 "eval(", "new Function"):
        assert sink not in js, sink
    assert "textContent" in js
    assert 'startsWith("/desk/")' in js
    assert "konsol.close.overview_api.get_checklist" in js
    html = _read(os.path.join(BLOCK_DIR, "konsol_close_checklist.html"))
    assert "<script" not in html.lower()


def test_the_checklist_method_is_a_gated_get():
    methods = _whitelisted()
    assert methods["get_checklist"][0] == ["GET"]
    assert _roles_of(methods["get_checklist"][1]) == ROLES


def test_overview_api_never_bypasses_permissions():
    source = _read(API)
    assert "ignore_permissions" not in source
    assert "frappe.get_all(" not in source  # get_list applies the caller's permissions
    assert re.search(r"frappe\.has_permission\(", source)
    assert "frappe.get_list(" in source
