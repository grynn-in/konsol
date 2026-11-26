"""The Konsol home close band as a Custom HTML Block (konsol#610, option B).

Decision: Deepak Pai, 26 Nov 2026, option B. Frappe 16.60.2 draws a Custom
number card's value as text, so the old cards printed raw markup, and its
Custom card API cannot carry text, a route and a per-state colour together.
The six home Number Cards are retired; ``overview_api.get_close_band``
returns data (never markup) and the "Konsol Close Band" block, and the same
renderer inside the Konsol Home Tabs block, draw it. Rejected: A (plain-text
cards, loses colour and link), C (a Number Card island, Frappe 16.60+ only),
D (text and a fixed colour per card, the colour no longer means the state).

- (a) no band text carries markup;
- (b) six items in the old card order, each with a known state, its colour
  and a Desk route;
- (c) an item whose reader raises is "Cannot read: ..." and unknown, and the
  other items still return;
- (d) the workspace carries no Number Card and has the block row, and its
  ``modified`` is later than before this change;
- (e) the block is in ``desk_blocks.BLOCKS`` with its files, and its script
  draws server text as text and links only to ``/desk/`` routes.
"""
import ast
import json
import os
import re
import shutil
import subprocess

from konsol.tests.test_konsol_home_hooks import _frappe, _load, _module

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = os.path.join(APP_DIR, "close", "overview_api.py")
WORKSPACE = os.path.join(APP_DIR, "consolidation", "workspace", "konsol", "konsol.json")
CARDS_DIR = os.path.join(APP_DIR, "consolidation", "number_card")
BLOCK = "Konsol Close Band"
FOLDER = "konsol_close_band"
BLOCK_DIR = os.path.join(APP_DIR, "consolidation", "custom_html_block", FOLDER)
SCRIPT = os.path.join(BLOCK_DIR, FOLDER + ".js")
METHOD = "konsol.close.overview_api.get_close_band"
#: The old cards' labels, in the old workspace's number_cards order.
BAND = ["Close Status", "Trial Balances In", "Waiting for My Approval",
        "IC Pairs over Tolerance", "TB Exceptions Declared", "Warehouse Freshness"]
RETIRED_FOLDERS = ("close_status", "trial_balances_in", "waiting_for_my_approval",
                   "ic_pairs_over_tolerance", "tb_exceptions_declared", "warehouse_freshness")
#: The workspace's modified on main before konsol#610.
BEFORE = "2026-11-22 20:00:00.000000"
COLOURS = {"ok": "green", "warn": "orange", "blocked": "red", "unknown": "gray"}
ROLES = {"EPM Admin", "EPM Analyst", "EPM User", "System Manager"}


def _read(path):
    with open(path) as f:
        return f.read()


# --- the API, against a stub frappe -----------------------------------------------

def _api(current=((2025, 7), None, {"is_signed": False, "checks": "Amber"}), raises=None,
         listed=("A", "B"), allowed=None, has_permission=True):
    """overview_api with every reader stubbed to a normal answer. ``raises``
    maps a reader name to the exception it raises."""
    raises = raises or {}

    def reader(name, value):
        def fn(*a, **k):
            if name in raises:
                raise raises[name]
            return value
        return fn

    frappe = _frappe()
    frappe.session = _module("s", user="lead@example.com")
    frappe.get_roles = lambda user=None: ["EPM User"]

    may_read = has_permission

    def _has_permission(dt, ptype="read", throw=False):
        if not may_read and throw:
            raise frappe.PermissionError("No permission for %s" % dt)
        return may_read

    frappe.has_permission = _has_permission
    frappe.get_list = reader("get_list", list(listed))
    model = _load("overview_model_for_band_test", os.path.join("close", "overview_model.py"), {})
    close = _module(
        "konsol.close", overview_model=model,
        approvals_api=_module("a", queue_for=reader("queue_for", {"waiting": {"count": 2}})),
        freshness_api=_module("f", current_freshness=reader(
            "current_freshness", {"state": "fresh", "as_of": "2026-11-26T10:42:00"})),
        ic_api=_module("i", signoff_summary=reader("signoff_summary", {"state": "not_configured"})),
        period_api=_module("p"), readiness_model=_module("r"),
        signoff_gate=_module("s", trial_balance_progress=reader(
            "trial_balance_progress", {"expected": ["A", "B"], "missing": ["B"]})))
    modules = {
        "frappe": frappe,
        "konsol": _module("konsol", fiscal_calendar=_module("fc"), group_rates=_module("gr")),
        "konsol.close": close,
        "konsol.consolidation": _module("konsol.consolidation"),
        "konsol.consolidation.doctype": _module("konsol.consolidation.doctype"),
        "konsol.consolidation.doctype.assertion_run": _module("x"),
        "konsol.consolidation.doctype.assertion_run.assertion_run": _module(
            "y", latest_close_run=lambda *a: None),
        "konsol.entity_permissions": _module("e", allowed_entity_codes=lambda: allowed),
    }
    api = _load("overview_api_for_band_test", os.path.join("close", "overview_api.py"), modules)

    def _current():
        if "current" in raises:
            raise raises["current"]
        return current
    api._current = _current
    return api, frappe


def test_band_text_is_data_not_markup():
    """(a) No band item's text carries markup: the block escapes, the server
    never builds HTML. ``overview_model.card`` (the HTML builder) is gone."""
    api, _ = _api()
    for item in api.get_close_band():
        assert "<" not in item["text"] and "indicator-pill" not in item["text"], item
    model = _load("overview_model_no_card", os.path.join("close", "overview_model.py"), {})
    assert not hasattr(model, "card")


def test_band_is_six_items_in_card_order_with_state_colour_and_desk_route():
    """(b)"""
    api, _ = _api()
    band = api.get_close_band()
    assert [i["label"] for i in band] == BAND
    for item in band:
        assert set(item) == {"label", "text", "state", "colour", "route"}, item
        assert item["state"] in COLOURS, item
        assert item["colour"] == COLOURS[item["state"]], item
        assert item["route"].startswith("/desk/"), item
    by = {i["label"]: i for i in band}
    assert by["Close Status"]["text"] == "FY2025 P07 · Amber"
    assert by["Close Status"]["state"] == "warn"
    assert by["Trial Balances In"]["text"] == "1 / 2"
    assert by["Trial Balances In"]["route"] == (
        "/desk/trial-balance-submission?fiscal_year=2025&fiscal_period=7")
    assert (by["Waiting for My Approval"]["text"], by["Waiting for My Approval"]["state"]) == (
        "2", "warn")
    assert by["IC Pairs over Tolerance"]["text"] == "Not configured"
    assert by["TB Exceptions Declared"]["text"] == "2"
    assert by["Warehouse Freshness"]["text"] == "Fresh · 2026-11-26 10:42"


def test_a_raising_item_says_cannot_read_and_the_rest_still_return():
    """(c) A ValueError, a permission error or any other exception in one
    item's reader is that item's "Cannot read: ..." (unknown, gray, its
    default route); the other five are unaffected."""
    for exc in (ValueError("unknown freshness state 'Purple'"), RuntimeError("boom")):
        api, _ = _api(raises={"current_freshness": exc})
        band = api.get_close_band()
        assert [i["label"] for i in band] == BAND
        bad = band[-1]
        assert bad["text"] == "Cannot read: %s" % exc, bad
        assert (bad["state"], bad["colour"]) == ("unknown", "gray")
        assert bad["route"] == "/desk/build-approval"
        assert all(i["state"] != "unknown" for i in band[:-1]), band
    api, _ = _api(has_permission=False)
    by = {i["label"]: i for i in api.get_close_band()}
    tb = by["TB Exceptions Declared"]
    assert tb["text"].startswith("Cannot read: ") and tb["state"] == "unknown", tb
    assert by["Warehouse Freshness"]["state"] == "ok"


def test_an_exception_with_no_message_is_named_by_its_class():
    api, _ = _api(raises={"queue_for": KeyError()})
    item = api.get_close_band()[2]
    assert item["text"] == "Cannot read: KeyError" and item["state"] == "unknown"


def test_a_failing_current_period_fails_only_the_period_items_and_is_read_once():
    calls = []
    api, frappe = _api()
    def boom():
        calls.append(1)
        raise frappe.ValidationError("FY2025 P13 is not a Regular period of the calendar.")
    api._current = boom
    band = api.get_close_band()
    assert len(calls) == 1
    period_items = ("Close Status", "Trial Balances In", "IC Pairs over Tolerance",
                    "TB Exceptions Declared")
    for item in band:
        if item["label"] in period_items:
            assert item["text"] == ("Cannot read: FY2025 P13 is not a Regular period of the "
                                    "calendar."), item
            assert item["state"] == "unknown"
        else:
            assert item["state"] != "unknown", item


def test_no_current_period_shows_the_reason():
    api, _ = _api(current=(None, "Declare the first close.", None))
    by = {i["label"]: i for i in api.get_close_band()}
    assert by["Close Status"]["text"] == "Declare the first close."
    assert by["Close Status"]["state"] == "unknown"
    assert by["Close Status"]["route"] == "/desk/query-report/Period%20Close"


def test_tb_exceptions_counts_only_the_callers_entities():
    api, _ = _api(listed=["A", "B", "C"], allowed={"A"})
    assert api.get_close_band()[4]["text"] == "1"
    api, _ = _api(listed=["A", "B", "C"], allowed=None)
    assert api.get_close_band()[4]["text"] == "3"


def _whitelisted():
    out = {}
    for fn in ast.parse(_read(API)).body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for dec in fn.decorator_list:
            if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "whitelist"):
                methods = next(ast.literal_eval(k.value) for k in dec.keywords if k.arg == "methods")
                roles = None
                for node in ast.walk(fn):
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                            and node.func.attr == "only_for"):
                        roles = node.args[0]
                out[fn.name] = (methods, roles)
    return out


def test_the_band_method_is_a_gated_get_and_the_card_methods_are_gone():
    methods = _whitelisted()
    assert set(methods) == {"get_close_band", "get_checklist"}, sorted(methods)
    assert methods["get_close_band"][0] == ["GET"]
    assert methods["get_close_band"][1] is not None, "get_close_band must call frappe.only_for"


# --- (d) the workspace ------------------------------------------------------------

def test_the_workspace_has_no_number_card_and_has_the_band_block():
    ws = json.loads(_read(WORKSPACE))
    assert ws["number_cards"] == []
    content = json.loads(ws["content"])
    assert not [b for b in content if b["type"] == "number_card"]
    assert BLOCK in [b["custom_block_name"] for b in ws["custom_blocks"]]
    assert ws["modified"] > BEFORE, ws["modified"]


def test_the_six_card_folders_are_gone_and_a_patch_deletes_the_cards():
    for folder in RETIRED_FOLDERS:
        assert not os.path.exists(os.path.join(CARDS_DIR, folder)), folder
    patches = _read(os.path.join(APP_DIR, "patches.txt")).split()
    assert "konsol.patches.retire_home_number_cards" in patches
    source = _read(os.path.join(APP_DIR, "patches", "retire_home_number_cards.py"))
    for name in BAND:
        assert '"%s"' % name in source, name


def test_no_shipped_workspace_names_a_retired_card():
    for path in _glob_workspaces():
        ws = json.loads(_read(path))
        names = {c["number_card_name"] for c in ws.get("number_cards") or []}
        names |= {b["data"].get("number_card_name") for b in json.loads(ws["content"])
                  if b["type"] == "number_card"}
        assert not names & set(BAND), (path, names & set(BAND))


def _glob_workspaces():
    import glob
    return glob.glob(os.path.join(APP_DIR, "*", "workspace", "*", "*.json"))


# --- (e) the block -----------------------------------------------------------------

def test_the_block_is_registered_with_the_methods_roles_and_its_files_exist():
    import ast as _ast
    tree = _ast.parse(_read(os.path.join(APP_DIR, "desk_blocks.py")))
    blocks = next(_ast.literal_eval(n.value) for n in tree.body
                  if isinstance(n, _ast.Assign) and getattr(n.targets[0], "id", "") == "BLOCKS")
    entry = next((b for b in blocks if b[0] == BLOCK), None)
    assert entry is not None, [b[0] for b in blocks]
    assert entry[1] == FOLDER and set(entry[2]) == ROLES, entry
    roles = _whitelisted()["get_close_band"][1]
    assert set(_ast.literal_eval(roles)) == ROLES
    for ext in ("html", "js", "css"):
        assert os.path.isfile(os.path.join(BLOCK_DIR, "%s.%s" % (FOLDER, ext))), ext
    assert "<script" not in _read(os.path.join(BLOCK_DIR, FOLDER + ".html")).lower()


def test_the_block_never_parses_server_text_as_html():
    js = _read(SCRIPT)
    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", ".html(", "document.write",
                 "eval(", "new Function"):
        assert sink not in js, sink
    assert "textContent" in js and 'startsWith("/desk/")' in js
    assert METHOD in js


HARNESS = r"""
const fs = require("fs");
const [script, cfgJson] = process.argv.slice(1);
const cfg = JSON.parse(cfgJson);
function node(tag) {
  return { __node: true, tag, className: "", children: [], textContent: "", href: "", attrs: {},
    setAttribute(k, v) { this.attrs[k] = v; },
    replaceChildren(...c) { this.children = c; },
    append(...c) { c.forEach((x) => this.children.push(x)); },
    appendChild(c) { this.children.push(c); return c; },
    querySelector(sel) { return sel === ".konsol-close-band" ? box : null; } };
}
const box = node("section");
const calls = [];
global.document = { createElement: (t) => node(t) };
global.root_element = node("root");
global.frappe = {
  xcall: (...a) => { calls.push(a);
    return cfg.reject ? Promise.reject(new Error(cfg.reject)) : Promise.resolve(cfg.payload); },
};
eval(fs.readFileSync(script, "utf8"));
function walk(n, f) { f(n); (n.children || []).forEach((c) => walk(c, f)); }
const has = (x, c) => (x.className || "").split(" ").includes(c);
setImmediate(() => setImmediate(() => {
  const items = [], errors = [];
  walk(box, (x) => {
    if (has(x, "kb-error")) errors.push(x.textContent);
    if (has(x, "kb-item")) {
      const out = { tag: x.tag, href: x.href || null, label: null, text: null, pill: null };
      walk(x, (y) => {
        if (has(y, "kb-label")) out.label = y.textContent;
        if (has(y, "kb-value")) { out.text = y.textContent; out.pill = y.className; }
      });
      items.push(out);
    }
  });
  console.log(JSON.stringify({ calls, items, errors }));
}));
"""


def _run(cfg):
    node = shutil.which("node")
    assert node, "node is required to run the close band script"
    r = subprocess.run([node, "-e", HARNESS, SCRIPT, json.dumps(cfg)], capture_output=True,
                       text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _item(label, text, state, route):
    return {"label": label, "text": text, "state": state, "colour": COLOURS.get(state, "x"),
            "route": route}


def test_the_block_draws_each_item_as_a_linked_pill_and_text_as_text():
    payload = [_item("Close Status", '<img src=x onerror="alert(1)">', "blocked",
                     "/desk/query-report/Period%20Close"),
               _item("Warehouse Freshness", "Fresh", "ok", "https://evil.example/x")]
    out = _run({"payload": payload})
    assert out["calls"] == [[METHOD, {}, "GET"]]
    assert out["errors"] == []
    first, second = out["items"]
    assert first["tag"] == "a" and first["href"] == "/desk/query-report/Period%20Close"
    assert first["label"] == "Close Status"
    assert first["text"] == '<img src=x onerror="alert(1)">'  # as text, never parsed
    assert "indicator-pill" in first["pill"] and "red" in first["pill"].split()
    # a route that is not /desk/ is not linked
    assert second["tag"] != "a" and second["href"] is None
    assert "green" in second["pill"].split()


def test_the_block_refuses_an_unknown_state_or_colour():
    out = _run({"payload": [_item("X", "t", "purple", "/desk/x")]})
    assert out["items"] == [] and len(out["errors"]) == 1 and "purple" in out["errors"][0]
    bad = dict(_item("X", "t", "ok", "/desk/x"), colour="red")
    out = _run({"payload": [bad]})
    assert out["items"] == [] and len(out["errors"]) == 1


def test_the_block_shows_a_failed_call_as_an_error_line():
    out = _run({"reject": "<b>no</b>"})
    assert out["errors"] == ["The close band could not be read: <b>no</b>"]
    out = _run({"payload": {"not": "a list"}})
    assert len(out["errors"]) == 1


def test_overview_api_never_bypasses_permissions():
    source = _read(API)
    assert "ignore_permissions" not in source
    assert "frappe.get_all(" not in source
    assert re.search(r"frappe\.has_permission\(", source)


def test_the_patch_deletes_only_the_apps_own_standard_cards():
    stored = {
        "Close Status": _module("r", module="Consolidation", is_standard=1),
        "Trial Balances In": _module("r", module="Consolidation", is_standard=0),  # a site's own
        "Warehouse Freshness": _module("r", module="Custom", is_standard=1),
    }
    deleted = []
    frappe = _frappe()
    frappe.db = _module("db", get_value=lambda dt, name, fields, as_dict=False: stored.get(name))
    frappe.delete_doc = lambda dt, name, **k: deleted.append((dt, name, k))
    patch = _load("retire_home_number_cards_under_test",
                  os.path.join("patches", "retire_home_number_cards.py"), {"frappe": frappe})
    patch.execute()
    assert deleted == [("Number Card", "Close Status", {"force": True, "ignore_permissions": True})]
