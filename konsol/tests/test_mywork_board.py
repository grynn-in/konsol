"""My Work as a board (konsol#419; option C, Deepak Pai, 7 Nov 2026).

The board reads ``mywork_api.get_my_work`` (one source of truth, nothing
stored, no dragging) and lays every item out as a card in Blocking, To do or
Waiting: title, what is wrong, how to fix it in one or two steps, a Go link
to the Desk page, the due date, the owner role and a Handbook page.

Every item here is built by the real pure model (``mywork_model``), the same
calls ``get_my_work`` makes, so a new item kind, or a change to an item's
shape, breaks these tests. An item kind with no fix text is refused: a card
never shows a blank "How to fix".
"""
import ast
import importlib.util
import json
import os
import re

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = _load("test_mywork_board_report_model", os.path.join("close", "mywork_report_model.py"))
MW = _load("test_mywork_board_mywork_model", os.path.join("close", "mywork_model.py"))
HANDBOOK_PAGES = os.path.join(APP_DIR, "handbook_pages")
BLOCK_DIR = os.path.join(APP_DIR, "consolidation", "custom_html_block", "konsol_my_work_board")
WORKSPACE = os.path.join(APP_DIR, "consolidation", "workspace", "my_work", "my_work.json")
SIDEBAR = os.path.join(APP_DIR, "consolidation", "sidebar", "close", "close.json")
API = os.path.join(APP_DIR, "close", "mywork_board_api.py")
MYWORK_API = os.path.join(APP_DIR, "close", "mywork_api.py")
HANDBOOK_JS = os.path.join(APP_DIR, "consolidation", "custom_html_block", "konsol_handbook",
                           "konsol_handbook.js")

FIRST = (2025, 7)
P07, P08, P09, P10 = (2025, 7), (2025, 8), (2025, 9), (2025, 10)
CODES = {P07: "FY2025 P07", P08: "FY2025 P08", P09: "FY2025 P09", P10: "FY2025 P10"}


def _read(path):
    with open(path) as f:
        return f.read()


def _period(code, **over):
    facts = {
        "code": code, "ended": True, "my_missing": [], "missing": [], "checks": "current",
        "failed": 0, "signoff": "Not signed off", "gates_blocked": False, "rates_missing": 0,
        "status": "Open", "unowned": [], "since": "2025-07-31",
        "deadlines": {s: {"due": "2025-08-10", "past": True, "text": "10 Aug 2025"}
                      for s in ("tb", "signoff", "ic")},
    }
    facts.update(over)
    return facts


_POLICY_GAPS = [{"code": c, "message": "Declare it (%s)." % c} for c in (
    "self_approval_undeclared", "rate_move_undeclared", "statement_accounts_undeclared",
    "commentary_threshold_undeclared")] + [
    {"code": "iso_currency_minor_unit_disagrees", "currencies": ["USD"], "message": "USD is wrong."},
    {"code": "iso_currency_minor_unit_unused", "currencies": ["XDR"], "message": "XDR is wrong."},
]


def _every_item():
    """One item of every kind the model (and mywork_api) can emit."""
    items = MW.setup_gap_items({
        "first_close": None, "chart_published": False, "frequency_missing": ["ZZF"],
        "ownership_missing": ["ZZO"], "accountants_without_entities": ["zz@example.com"],
        "policy_gaps": _POLICY_GAPS, "ic_accounts_gap": "Publish an Intercompany Account.",
        "ic_tolerance_gap": {"code": "ic_tolerance_undeclared", "groups": ["ZZG"],
                             "message": "Declare the tolerance."},
        "ic_rule_gaps": [
            {"code": "ic_unrealized_profit_rule_undeclared", "pairs": [["ZZA", "ZZB"]],
             "entities": ["ZZA", "ZZB"], "message": "No rule."},
            {"code": "ic_unrealized_profit_rule_ambiguous", "pairs": [["ZZA", "ZZB"]],
             "entities": ["ZZA", "ZZB"], "message": "Two rules."},
        ],
    })
    lead = {
        P07: _period(CODES[P07], missing=["ZZA"], rates_missing=2, unowned=["ZZB"], failed=1,
                     signoff="Re-sign Needed"),
        P08: _period(CODES[P08], signoff="Signed Off"),
        P09: _period(CODES[P09]),
        P10: _period(CODES[P10], gates_blocked=True),
    }
    items += MW.period_items("close_lead", lead, FIRST)
    items += MW.period_items("group_accountant", {
        P07: _period(CODES[P07], checks="stale", failed=1)}, FIRST)
    entity = {P07: _period(CODES[P07], my_missing=["ZZA"], missing=["ZZA"])}
    items += MW.period_items("entity_accountant", entity, FIRST)
    items += MW.ic_fix_items({P07: [{
        "entity_a": "ZZA", "account_a": "1400", "entity_b": "ZZB", "account_b": "2400",
        "state": "not_in_build", "sent_by": "carol", "sent_at": "2026-10-02T09:00:00",
        "reason": "Check it"}]}, entity, None)
    items.append(MW.approvals_item({"count": 2, "oldest": "2026-10-01T09:00:00+00:00"}))
    rejection = {"reason": "Wrong", "actor": "carol", "at": "2026-10-02T09:00:00"}
    items += MW.sent_back_items([
        {"doctype": d, "name": "ZZ-%d" % n, "kind_label": d, "title": "t", "fiscal_year": None,
         "fiscal_period": None, "rejection": rejection}
        for n, d in enumerate(R.APPROVAL_DOCTYPES)], "close_lead", CODES)
    items += MW.signoff_sent_back_items(
        [{"name": "CE-000000001", "kind": "signoff_rejected", "fiscal_year": 2025,
          "fiscal_period": 7, "actor": "carol", "at": "2026-10-03T09:00:00", "reason": "Recheck",
          "detail": {"preparer": "dave"}}], "dave", "close_lead", CODES, {"carol": "Carol C"})
    # mywork_api._rates_error_item (the API builds it; not importable without frappe).
    items.append({"id": "rates-error:2025-07", "kind": "blocking", "title": "Rates cannot be checked: x",
                  "detail": "d", "owner": "EPM Admin", "action": {"screen": "sign-off"},
                  "period": {"fiscal_year": 2025, "fiscal_period": 7, "code": "FY2025 P07",
                             "since": "2025-07-31"}})
    return MW.rank(items)


def _payload(**over):
    payload = {"items": _every_item(), "counts": {}, "entities_assigned": None}
    payload.update(over)
    return payload


def _emitted_kinds():
    """Every item kind the code can emit, read from the code itself, so a new
    kind added to mywork_model or mywork_api without a fix fails here."""
    model = _read(os.path.join(APP_DIR, "close", "mywork_model.py"))
    api = _read(MYWORK_API)
    # The gap ids are read from the builders too, so a gap missing from GAPS fails.
    built = set(re.findall(r'_item\(\s*"(\w+)"', model))
    built |= set(re.findall(r'\(\s*"(\w+)",\s*(?:"|\n)', model[model.index("_POLICY_GAPS = {"):
                                                           model.index("def _declared")]))
    assert built - {"gap"} <= set(MW.GAPS), built - set(MW.GAPS)
    kinds = {"gap:" + g for g in MW.GAPS}
    kinds |= set(re.findall(r'_period_item\([^,]+, key, facts, "([\w-]+)"', model))
    literal = set(re.findall(r'"id":\s*"([a-z][\w-]*)', model + api))
    assert literal == {"approvals", "ic", "sent-back", "rates-error"}, (
        "a new item id prefix: give it a fix in mywork_report_model.FIXES", literal)
    kinds |= {"approvals", "ic", "rates-error", "sent-back:signoff"}
    kinds |= {"sent-back:" + d for d in R.APPROVAL_DOCTYPES}
    return kinds


# --- the fix table -------------------------------------------------------------

def test_every_kind_the_code_can_emit_has_a_fix():
    assert set(R.FIXES) == _emitted_kinds()


def test_the_fixture_reaches_every_kind():
    assert {R.item_type(i) for i in _every_item()} == set(R.FIXES)


def test_every_fix_is_one_or_two_plain_steps():
    for kind, (steps, _handbook) in R.FIXES.items():
        assert isinstance(steps, tuple) and 1 <= len(steps) <= 2, kind
        for step in steps:
            assert step.strip() and step.endswith("."), (kind, step)
            assert "/close" not in step and "konsol#" not in step, (kind, step)


def test_every_handbook_page_named_exists():
    pages = {os.path.splitext(f)[0] for f in os.listdir(HANDBOOK_PAGES) if f.endswith(".md")}
    for kind, (_steps, handbook) in R.FIXES.items():
        assert handbook is None or handbook in pages, (kind, handbook)


@pytest.mark.parametrize("item_id, kind", [
    ("gap:chart", "gap:chart"),
    ("tb:2025-07:ZZA", "tb"),
    ("checks-run:2025-07", "checks-run"),
    ("signoff-wait:2025-07", "signoff-wait"),
    ("approvals", "approvals"),
    ("ic:2025-07:ZZA:ZZA|1400|ZZB|2400", "ic"),
    ("sent-back:IC Balance:ICB-1", "sent-back:IC Balance"),
    ("sent-back:signoff:2025-07", "sent-back:signoff"),
    ("rates-error:2025-07", "rates-error"),
])
def test_item_type_reads_the_id(item_id, kind):
    assert R.item_type({"id": item_id}) == kind


def test_an_item_with_no_fix_is_refused_never_blank():
    item = {"id": "brand-new:2025-07", "kind": "todo", "title": "t", "owner": "EPM Admin",
            "action": {"desk": "/desk/entity"}}
    with pytest.raises(ValueError):
        R.card(item)
    with pytest.raises(ValueError):
        R.rows({"items": [item]})


# --- the cards -----------------------------------------------------------------

def test_every_card_has_fix_text_and_a_go_or_says_why_not():
    for column in R.board(_payload())["columns"]:
        for card in column["cards"]:
            assert card["fix"] and all(s.strip() for s in card["fix"]), card["id"]
            if card["go"] is None:
                assert card["no_go"] and card["handbook"], card["id"]
            else:
                assert card["go"].startswith("/desk/") and card["no_go"] is None, card["id"]


def test_every_kind_emitted_today_has_a_desk_destination():
    """No item kind is left without a Go: the board shows "no Desk page" only
    for a screen declared None, which no item opens today."""
    for column in R.board(_payload())["columns"]:
        for card in column["cards"]:
            assert card["go"], card["id"]


def test_three_columns_in_order_each_keeping_the_apis_order():
    payload = _payload()
    board = R.board(payload)
    assert [(c["kind"], c["label"]) for c in board["columns"]] == [
        ("blocking", "Blocking"), ("todo", "To do"), ("waiting", "Waiting")]
    for column in board["columns"]:
        want = [i["id"] for i in payload["items"] if i["kind"] == column["kind"]]
        assert [c["id"] for c in column["cards"]] == want
    assert sum(len(c["cards"]) for c in board["columns"]) == len(payload["items"])


def test_a_card_carries_what_is_wrong_due_owner_period_and_handbook():
    cards = {c["id"]: c for col in R.board(_payload())["columns"] for c in col["cards"]}
    tb = cards["tb:2025-07:ZZA"]
    assert tb["title"] == "Upload TB for ZZA"
    assert tb["owner"] == "Entity Accountant" and tb["period"] == "FY2025 P07"
    assert tb["due"] == {"text": "10 Aug 2025", "overdue": True}
    assert tb["go"] == ("/desk/trial-balance-submission?fiscal_year=2025&fiscal_period=7"
                        "&data_area_id=ZZA")
    assert tb["handbook"] == "/desk/konsol-handbook?page=konsol%3Astart-entity-accountant"
    gap = cards["gap:chart"]
    assert gap["detail"] == "Publish the Main Accounts of the group chart."
    assert gap["period"] is None and gap["due"] is None
    assert gap["fix"] == list(R.FIXES["gap:chart"][0])


def test_go_is_the_report_open_path_so_the_board_and_list_agree():
    payload = _payload()
    rows = {r["id"]: r for r in R.rows(payload)}
    for column in R.board(payload)["columns"]:
        for card in column["cards"]:
            assert card["go"] == rows[card["id"]]["open"], card["id"]


def test_each_go_opens_the_page_where_its_fix_is_done():
    """PR #420 review: run checks, re-sign and sign off are Actions in Period
    Close (no run may exist yet to open); a rates error is fixed by a build;
    an accountant with no entity needs a User Permission."""
    cards = {c["id"]: c for col in R.board(_payload())["columns"] for c in col["cards"]}
    period_close = "/desk/query-report/Period%20Close"
    for card_id in ("checks-run:2025-07", "resign:2025-07", "signoff:2025-09"):
        assert cards[card_id]["go"] == period_close, card_id
        assert cards[card_id]["fix"][0].startswith("In Period Close, use Actions"), card_id
    assert cards["rates-error:2025-07"]["go"] == "/desk/build-approval"
    assert "Build Approval" in cards["rates-error:2025-07"]["fix"][0]
    assert cards["gap:accountants"]["go"] == "/desk/user-permission"
    assert "User Permission" in cards["gap:accountants"]["fix"][0]


def test_close_and_waiting_on_gates_open_the_period_close_report():
    """Close period is in the Period Close report's Actions menu, and that
    report shows what blocks a sign-off (Handbook: Approve, check and sign off)."""
    cards = {c["id"]: c for col in R.board(_payload())["columns"] for c in col["cards"]}
    assert cards["close:2025-08"]["go"] == "/desk/query-report/Period%20Close"
    assert cards["signoff-wait:2025-10"]["go"] == "/desk/query-report/Period%20Close"


def test_the_iso_currency_card_opens_the_currencies_named():
    cards = {c["id"]: c for col in R.board(_payload())["columns"] for c in col["cards"]}
    assert cards["gap:iso_currency"]["go"] == (
        "/desk/iso-currency?name=%5B%22in%22%2C+%5B%22USD%22%5D%5D")
    assert cards["gap:iso_currency_unused"]["go"] == (
        "/desk/iso-currency?name=%5B%22in%22%2C+%5B%22XDR%22%5D%5D")


def test_a_screen_with_no_desk_page_says_so_and_links_the_handbook(monkeypatch):
    monkeypatch.setitem(R.FIXES, "zz-nowhere", (("Ask the Close Lead.",), "start-close-lead"))
    card = R.card({"id": "zz-nowhere", "kind": "todo", "title": "t", "owner": "EPM Admin",
                   "action": {"screen": "my-work"}})
    assert card["go"] is None
    assert card["no_go"] and "Handbook" in card["no_go"]
    assert card["handbook"]


def test_a_kind_with_no_desk_page_must_name_a_handbook_page(monkeypatch):
    monkeypatch.setitem(R.FIXES, "zz-nowhere", (("Ask the Close Lead.",), None))
    with pytest.raises(ValueError):
        R.card({"id": "zz-nowhere", "kind": "todo", "title": "t", "owner": "EPM Admin",
                "action": {"screen": "my-work"}})


def test_the_board_says_when_no_entity_is_assigned():
    board = R.board({"items": [], "counts": {}, "entities_assigned": False})
    assert board["message"] == "No entities are assigned to you. Ask the System Manager."
    board = R.board({"items": [], "counts": {}, "entities_assigned": True})
    assert board["message"] == "Your entities are assigned, but none are in scope this period."
    assert R.board(_payload())["message"] is None
    assert R.board(_payload())["list_view"] == "/desk/query-report/My%20Work"


# --- the report keeps the list view ------------------------------------------------

def test_the_report_has_how_to_fix_and_open_second():
    columns = R.columns()
    assert [c["fieldname"] for c in columns][:2] == ["kind", "open"]
    (fix,) = [c for c in columns if c["fieldname"] == "fix"]
    assert fix["label"] == "How to fix"
    rows = {r["id"]: r for r in R.rows(_payload())}
    assert rows["gap:chart"]["fix"] == " ".join(R.FIXES["gap:chart"][0])


def test_the_report_links_back_to_the_board():
    js = _read(os.path.join(APP_DIR, "consolidation", "report", "my_work", "my_work.js"))
    # The "My Work" workspace, at /desk/my-work.
    assert 'frappe.set_route("my-work")' in js and "Board view" in js


# --- the method -------------------------------------------------------------------

def test_get_board_is_a_get_that_reads_get_my_work_and_nothing_else():
    source = _read(API)
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_board")
    dec = next(d for d in fn.decorator_list if isinstance(d, ast.Call))
    assert ast.unparse(dec.func) == "frappe.whitelist"
    assert [ast.literal_eval(k.value) for k in dec.keywords if k.arg == "methods"] == [["GET"]]
    body = ast.unparse(fn)
    # get_my_work's own frappe.only_for and entity scope apply: same roles, same items.
    assert "mywork_api.get_my_work()" in body
    # The board's own gate, opened first, admits exactly get_my_work's roles.
    gate = fn.body[1].value
    assert ast.unparse(gate.func) == "frappe.only_for"
    roles = ast.literal_eval(gate.args[0])
    api = ast.parse(_read(MYWORK_API))
    mine = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "get_my_work")
    only_for = next(n for n in ast.walk(mine) if isinstance(n, ast.Call)
                    and ast.unparse(n.func) == "frappe.only_for")
    assert set(roles) == set(ast.literal_eval(only_for.args[0]))
    assert "mywork_report_model.board(" in body
    for bypass in ("ignore_permissions", "set_user", "frappe.get_all(", "frappe.db."):
        assert bypass not in source, bypass


# --- the block, the workspace and the sidebar ----------------------------------------

def test_the_block_writes_server_strings_as_text_only():
    js = _read(os.path.join(BLOCK_DIR, "konsol_my_work_board.js"))
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(",
                 "new Function", "jQuery", "$("):
        assert sink not in js, sink
    assert '"konsol.close.mywork_board_api.get_board"' in js and '"GET"' in js
    assert "root_element" in js
    assert "card.fix.length" not in js, "guard a missing fix before reading it"
    # Every href the block sets is checked to be a Desk path first.
    assert js.count(".href =") == 1 and "const href = desk(path);" in js
    assert 'startsWith("/desk/") ? path : null' in js
    html = _read(os.path.join(BLOCK_DIR, "konsol_my_work_board.html")).lower()
    assert "<script" not in html and " on" not in html


def test_the_block_shows_each_card_field_and_a_list_view_link():
    js = _read(os.path.join(BLOCK_DIR, "konsol_my_work_board.js"))
    for needle in ("card.title", "card.detail", "card.fix", "card.go", "card.no_go", "card.due",
                   "card.owner", "card.handbook", "board.list_view", '"List view"', '"Go"',
                   '"How to fix"'):
        assert needle in js, needle


def test_the_block_uses_desk_theme_colours_only():
    css = _read(os.path.join(BLOCK_DIR, "konsol_my_work_board.css"))
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "use Desk theme variables, not hex"
    assert "rgb(" not in css
    assert "var(--primary)" in css and "var(--card-bg)" in css
    assert "@container" in css and "container-type: inline-size" in css


def test_desk_blocks_ships_the_board_to_the_roles_get_my_work_admits():
    source = _read(os.path.join(APP_DIR, "desk_blocks.py"))
    tree = ast.parse(source)
    blocks = next(ast.literal_eval(n.value) for n in tree.body
                  if isinstance(n, ast.Assign) and n.targets[0].id == "BLOCKS")
    (entry,) = [b for b in blocks if b[0] == "Konsol My Work Board"]
    assert entry[1] == "konsol_my_work_board"
    api = ast.parse(_read(MYWORK_API))
    fn = next(n for n in api.body if isinstance(n, ast.FunctionDef) and n.name == "get_my_work")
    only_for = next(n for n in ast.walk(fn) if isinstance(n, ast.Call)
                    and ast.unparse(n.func) == "frappe.only_for")
    assert set(entry[2]) == set(ast.literal_eval(only_for.args[0]))


def test_the_my_work_workspace_holds_only_the_board():
    ws = json.loads(_read(WORKSPACE))
    assert (ws["doctype"], ws["name"], ws["label"], ws["title"]) == (
        "Workspace", "My Work", "My Work", "My Work")
    assert ws["public"] == 1 and ws["standard"] == 1 and ws["module"] == "Consolidation"
    # PR #420 review: shown only to the roles the board admits.
    assert {r["role"] for r in ws["roles"]} == set(dict((b[0], b[2]) for b in ast.literal_eval(
        next(n.value for n in ast.parse(_read(os.path.join(APP_DIR, "desk_blocks.py"))).body
             if isinstance(n, ast.Assign) and n.targets[0].id == "BLOCKS")))["Konsol My Work Board"])
    assert not ws["for_user"]
    assert [b["custom_block_name"] for b in ws["custom_blocks"]] == ["Konsol My Work Board"]
    content = json.loads(ws["content"])
    assert [(b["type"], b["data"].get("custom_block_name"), b["data"]["col"]) for b in content] == [
        ("custom_block", "Konsol My Work Board", 12)]


def test_the_sidebar_my_work_row_opens_the_board():
    sidebar = json.loads(_read(SIDEBAR))
    (row,) = [i for i in sidebar["items"] if i.get("label") == "My Work"]
    assert (row["link_type"], row["link_to"]) == ("Workspace", "My Work")
    # A Sidebar re-imports on migrate only when ``modified`` changes.
    assert sidebar["modified"] >= "2026-11-07 22:00:00", sidebar["modified"]


def test_the_handbook_opens_the_page_a_link_names():
    """A card's Handbook link is /desk/konsol-handbook?page=<id>: the Handbook
    block opens that page when it loads."""
    js = _read(HANDBOOK_JS)
    assert 'URLSearchParams(window.location.search).get("page")' in js
    assert "openPage(" in js


def test_cards_show_only_their_heading_until_opened():
    """Deepak Pai, 7 Nov 2026: "on the my work -> to do -> just show the
    headings with a collapsible link and collapse all /expand all toggle
    button on the top bar". Each card is a <details> whose <summary> is the
    heading (title and period); everything else sits inside, closed on load.
    One top-bar button opens or closes every card and names what it will do."""
    js = _read(os.path.join(BLOCK_DIR, "konsol_my_work_board.js"))
    assert 'el("details", "kmw-card kmw-" + card.kind)' in js
    assert 'el("summary", "kmw-card-head")' in js
    assert ".open = true" not in js.split("function cardNode", 1)[1].split("function columnNode", 1)[0], \
        "a card starts closed"
    for needle in ('"Expand all"', '"Collapse all"', 'querySelectorAll("details.kmw-card")'):
        assert needle in js, needle
    css = _read(os.path.join(BLOCK_DIR, "konsol_my_work_board.css"))
    assert "summary.kmw-card-head" in css and "cursor: pointer" in css


def test_corners_are_nearly_square():
    """Deepak Pai, 7 Nov 2026: "reduce rounded edges...looks too appy...like it
    less rounded". Every radius on the board is 2px at most."""
    css = _read(os.path.join(BLOCK_DIR, "konsol_my_work_board.css"))
    radii = re.findall(r"border-radius:\s*([^;]+);", css)
    assert radii, "no radius found"
    for value in radii:
        assert re.fullmatch(r"[0-2]px", value.strip()), value
