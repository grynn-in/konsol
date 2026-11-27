"""The task pane's Server field, and its Refresh buttons (konsol#605 B and C).

Decided by Deepak Pai, 27 Nov 2026 (konsol#605): build A, B and the pane
buttons.

B. The pane talks to one server at a time. By default that is the origin the
pane was loaded from, exactly as before; the user may enter another https://
site address (origin only, no path). The choice is kept in the workbook's
own document settings (``konsolServer``, saved with the file), so two open
workbooks can read two sites and switching the pane in one never moves the
other's cells. A workbook with no choice talks to the pane's origin. Tokens
stay in localStorage, one per server: switching server never sends one
site's token to another. ``server.js`` holds these rules; ``functions.js`` and the pane
build every URL from it. ribbon.js makes no request of its own: it goes
through the pane's ``konsolWhoami`` and ``konsolidatSnapshotReport``.

A call another site refuses at the CORS check reaches the page as a failed
fetch; the message then names the fix: ask an admin of that site to add the
pane's origin to Close Settings → Excel Add-in Origins.

C. The pane has Refresh sheet and Refresh workbook buttons that call the
ribbon's own commands (``konsolRibbon.refreshSheet`` / ``refreshWorkbook``),
so they share its busy check, its log lines and its greyed-until-done rule.

server.js and functions.js run under node (excel_addin_harness.mjs); the pane
markup and wiring are read as source.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDIN = os.path.join(APP_DIR, "public", "excel-addin")
HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "excel_addin_harness.mjs")

PANE = "https://pane.example"
OTHER = "https://other.example"
EPM_ARGS = ["ZZE", 2024, 1, "ZZ4000"]


def _read(name):
    with open(os.path.join(ADDIN, name), encoding="utf-8") as f:
        return f.read()


def _run(**payload):
    node = shutil.which("node")
    assert node, "node is required to run the Excel add-in tests"
    payload.setdefault("origin", PANE)
    proc = subprocess.run([node, HARNESS, json.dumps(payload)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# -- origin rules ---------------------------------------------------------------

@pytest.mark.parametrize("text, origin", [
    ("https://other.example", OTHER),
    ("  https://OTHER.example/ ", OTHER),
    ("https://other.example:8443", "https://other.example:8443"),
    ("https://other.example:443", OTHER),
])
def test_a_server_address_is_an_https_origin(text, origin):
    out = _run(ops=[["parseOrigin", text]])
    assert out["ops"] == [{"origin": origin}], out


@pytest.mark.parametrize("text, why", [
    ("http://other.example", "https://"),
    ("other.example", "https://"),
    ("https://other.example/desk", "no path"),
    ("https://other.example/?x=1", "no path"),
    ("https://user@other.example", "no path"),
    ("https://*.example", "wildcard"),
    ("https://", "host"),
])
def test_anything_else_is_refused_with_a_reason(text, why):
    (result,) = _run(ops=[["parseOrigin", text]])["ops"]
    assert "origin" not in result, result
    assert why in result["error"], result


# -- choosing a server ------------------------------------------------------------

SETTING = "konsolServer"


def test_by_default_the_server_is_the_panes_own_origin_and_urls_stay_relative():
    out = _run(ops=[["current"], ["isCrossOrigin"], ["url", "/api/method/x"]])
    assert out["ops"] == [PANE, False, "/api/method/x"], out


def test_a_chosen_server_is_kept_in_the_workbook_not_the_browser():
    out = _run(ops=[["choose", "https://other.example/"], ["current"], ["isCrossOrigin"],
                    ["url", "/api/method/x"]])
    assert out["ops"] == [{"origin": OTHER}, OTHER, True, OTHER + "/api/method/x"], out
    assert out["settings"] == {SETTING: OTHER}, out["settings"]
    assert out["saves"] == 1, "the choice is saved with the workbook (settings.saveAsync)"
    assert not any("server" in k for k in out["storage"]), (
        "a browser-wide key would move every open workbook's cells: %s" % out["storage"])


def test_a_workbook_reads_its_own_server_whatever_another_workbook_chose():
    """Workbook W1 chose OTHER; workbook W2, in the same browser (same
    localStorage, same tokens), has no choice and stays on the pane's origin."""
    storage = {"konsol_token:" + OTHER: "tok-other"}
    w1 = _run(storage=storage, settings={SETTING: OTHER}, ops=[["current"]])
    w2 = _run(storage=w1["storage"], settings={}, ops=[["current"]])
    assert w1["ops"] == [OTHER] and w2["ops"] == [PANE], (w1, w2)


def test_a_refused_address_changes_nothing():
    out = _run(settings={SETTING: OTHER}, ops=[["choose", "http://third.example"], ["current"]])
    assert "error" in out["ops"][0]
    assert out["ops"][1] == OTHER
    assert out["settings"] == {SETTING: OTHER}
    assert out["saves"] == 0


def test_choosing_the_panes_own_origin_or_blank_goes_back_to_the_default():
    for text in ("", PANE):
        out = _run(settings={SETTING: OTHER}, ops=[["choose", text], ["current"]])
        assert out["ops"][1] == PANE, out
        assert SETTING not in out["settings"], out["settings"]
        assert out["saves"] == 1


def test_a_failed_save_is_reported_not_hidden():
    out = _run(saveFails=True, ops=[["choose", OTHER], ["current"]])
    chosen = out["ops"][0]
    assert chosen["origin"] == OTHER, chosen
    assert "disk full (stub)" in chosen["unsaved"], chosen
    assert out["ops"][1] == OTHER, "the workbook uses the choice until it closes"


def test_without_a_workbook_a_server_cannot_be_chosen():
    out = _run(noOffice=True, ops=[["choose", OTHER], ["current"]])
    assert "workbook" in out["ops"][0]["error"], out["ops"][0]
    assert out["ops"][1] == PANE


def test_a_stored_server_that_is_not_an_origin_is_not_used_and_is_reported():
    out = _run(settings={SETTING: "javascript:alert(1)"}, ops=[["current"], ["notice"]])
    assert out["ops"][0] == PANE
    assert "javascript:alert(1)" in out["ops"][1], out


def test_the_old_browser_wide_choice_is_dropped_and_reported_not_adopted():
    """Before this fix the choice was one localStorage key for every workbook.
    Adopting it into this workbook would guess which workbook it was for."""
    out = _run(storage={"konsol_server": OTHER}, ops=[["current"], ["notice"]])
    assert out["ops"][0] == PANE, out
    assert OTHER in out["ops"][1] and "workbook" in out["ops"][1], out["ops"][1]
    assert "konsol_server" not in out["storage"]
    assert out["settings"] == {}


# -- tokens per server --------------------------------------------------------------

def test_tokens_are_kept_per_server():
    out = _run(ops=[["setToken", "tok-pane"], ["choose", OTHER], ["getToken"],
                    ["setToken", "tok-other"], ["choose", ""], ["getToken"],
                    ["choose", OTHER], ["getToken"]])
    assert out["ops"][2] == "", "site B must not see site A's token"
    assert out["ops"][5] == "tok-pane"
    assert out["ops"][7] == "tok-other"
    assert out["storage"]["konsol_token:" + PANE] == "tok-pane"
    assert out["storage"]["konsol_token:" + OTHER] == "tok-other"


def test_the_old_single_token_moves_to_the_panes_own_origin():
    """Before #605 the pane kept one token under "konsol_token", always issued
    by the pane's own origin. A signed-in user stays signed in there."""
    out = _run(storage={"konsol_token": "old"}, ops=[["getToken"], ["choose", OTHER], ["getToken"]])
    assert out["ops"][0] == "old"
    assert out["ops"][2] == ""
    assert "konsol_token" not in out["storage"]
    assert out["storage"]["konsol_token:" + PANE] == "old"


# -- functions.js -----------------------------------------------------------------------

def test_same_origin_formulas_behave_as_before():
    out = _run(storage={"konsol_token:" + PANE: "tok-pane"}, fn="EPM", args=EPM_ARGS)
    (req,) = out["requests"]
    assert req["url"] == "/api/method/konsol.api.epm_batch"
    assert req["headers"]["X-Konsolidat-Token"] == "tok-pane"
    assert req["credentials"] == "omit"
    assert out["result"] == 42


def test_same_origin_without_a_token_still_sends_the_cookie():
    out = _run(fn="EPM", args=EPM_ARGS)
    (req,) = out["requests"]
    assert "X-Konsolidat-Token" not in req["headers"]
    assert req["credentials"] == "include"


def test_formulas_go_to_the_workbooks_server_with_its_own_token():
    out = _run(settings={SETTING: OTHER},
               storage={"konsol_token:" + PANE: "tok-pane", "konsol_token:" + OTHER: "tok-other"},
               fn="EPM", args=EPM_ARGS)
    (req,) = out["requests"]
    assert req["url"] == OTHER + "/api/method/konsol.api.epm_batch"
    assert req["headers"]["X-Konsolidat-Token"] == "tok-other"
    assert req["credentials"] == "omit"


def test_a_cross_origin_formula_never_sends_another_sites_token_or_cookies():
    out = _run(settings={SETTING: OTHER}, storage={"konsol_token:" + PANE: "tok-pane"},
               fn="EPM", args=EPM_ARGS, status=403, reply={})
    (req,) = out["requests"]
    assert "X-Konsolidat-Token" not in req["headers"], req
    assert req["credentials"] == "omit", "no cookies cross-origin: the site sends no Allow-Credentials"


def test_epmsave_goes_to_the_chosen_server():
    out = _run(settings={SETTING: OTHER}, storage={"konsol_token:" + OTHER: "tok-other"},
               fn="EPMSAVE",
               args=[100, "ZZE", 2024, 1, "ZZ6100", "ZZPLAN", "base", None, None, None, "EUR", "Movement"])
    (req,) = out["requests"]
    assert req["url"] == OTHER + "/api/method/konsol.api.budget_cell_save"
    assert req["headers"]["X-Konsolidat-Token"] == "tok-other"


def test_a_refused_cross_origin_call_names_the_fix():
    out = _run(settings={SETTING: OTHER}, storage={"konsol_token:" + OTHER: "tok-other"},
               fn="EPM", args=EPM_ARGS, fetchFails=True)
    msg = out["error"]["message"]
    assert out["error"]["code"] == "#N/A"
    assert OTHER in msg and PANE in msg, msg
    assert "Close Settings" in msg and "Excel Add-in Origins" in msg, msg


def test_the_unreachable_message_names_both_sites():
    out = _run(settings={SETTING: OTHER}, ops=[["unreachableMessage"]])
    (msg,) = out["ops"]
    assert msg.startswith("Could not reach " + OTHER), msg
    assert "ask an admin of " + OTHER + " to add " + PANE + " to Close Settings → Excel Add-in Origins" in msg


def test_functions_js_has_no_url_or_token_key_of_its_own():
    """Only its fallback for an old cached page (no server.js) names the
    token key that page wrote."""
    src = _read("functions.js")
    start = src.index("function sameOriginServer(")
    end = src.index("\n}\n", start)
    assert '"konsol_token"' not in src[:start] + src[end:]
    assert "server().url(" in src and "server().getToken(" in src


def test_functions_js_without_server_js_reads_the_panes_own_origin_and_warns():
    """An old cached index.html loads the new functions.js but no server.js:
    the cells still read the pane's own site, as that page did, never throw a
    ReferenceError, and the console says why."""
    out = _run(skip=["server.js"], storage={"konsol_token": "old"}, fn="EPM", args=EPM_ARGS)
    assert out["error"] is None, out
    (req,) = out["requests"]
    assert req["url"] == "/api/method/konsol.api.epm_batch"
    assert req["headers"]["X-Konsolidat-Token"] == "old"
    assert out["result"] == 42
    assert any("server.js" in w for w in out["warnings"]), out["warnings"]


def test_functions_js_without_server_js_names_the_reload_when_unreachable():
    out = _run(skip=["server.js"], fn="EPM", args=EPM_ARGS, fetchFails=True)
    assert out["error"]["code"] == "#N/A", out
    assert "Could not reach" in out["error"]["message"], out


# -- the pane ----------------------------------------------------------------------------

def test_the_page_loads_server_js_before_functions_js_and_ribbon_js():
    src = _read("index.html")
    server = src.index('<script src="server.js"></script>')
    assert server < src.index('<script src="functions.js"></script>') < src.index('<script src="ribbon.js"></script>')


def test_the_pane_has_a_server_field_and_shows_the_connected_server():
    src = _read("index.html")
    assert re.search(r'<input id="server-url"[^>]*type="url"', src), "no Server field"
    assert 'id="server-btn"' in src
    assert 'id="server-summary"' in src
    assert "konsolServer.choose(" in src
    assert "konsolServer.current()" in src


def test_the_pane_builds_every_request_from_the_chosen_server():
    src = _read("index.html")
    assert "window.location.origin" not in src, "the pane must ask konsolServer, not its own origin"
    assert "konsolServer.url(" in src
    assert '"konsol_token"' not in src
    # Every fetch of an API path goes through konsolServer.url.
    for m in re.finditer(r"fetch\(([^,]+),", src):
        arg = m.group(1).strip()
        if arg == '"functions.js"':
            continue  # the pane's own code: its deploy time, from where it loaded
        assert "konsolServer.url(" in arg, arg


def test_sign_in_and_out_use_the_chosen_servers_token():
    src = _read("index.html")
    assert "konsolServer.getToken()" in src and "konsolServer.setToken(" in src
    assert not re.search(r"localStorage\.(get|set|remove)Item\(\s*TOKEN_KEY", src)


def test_a_failed_fetch_in_the_pane_names_the_fix():
    src = _read("index.html")
    assert src.count("konsolServer.unreachableMessage()") >= 2, "api() and login() must both name the fix"


def test_pane_buttons_call_the_ribbon_commands():
    src = _read("index.html")
    for btn, cmd in (("refresh-sheet-btn", "refreshSheet"), ("refresh-workbook-btn", "refreshWorkbook")):
        assert 'id="%s"' % btn in src, btn
        assert re.search(r'getElementById\("%s"\)\.addEventListener\("click"' % btn, src), btn
        assert 'runRibbonCommand("%s")' % cmd in src, cmd
    body = src[src.index("function runRibbonCommand("):]
    body = body[:body.index("\n      function ", 1)]
    assert "window.konsolRibbon[name]" in body
    assert "completed:" in body, "the buttons stay greyed until the command calls event.completed()"
    assert "setRefreshButtons(true)" in body and "setRefreshButtons(false)" in body
    grey = src[src.index("function setRefreshButtons("):]
    grey = grey[:grey.index("\n      function ", 1)]
    assert grey.count(".disabled = disabled") == 2, grey


def test_pane_buttons_have_the_ribbon_labels():
    src = _read("index.html")
    assert re.search(r'id="refresh-sheet-btn"[^>]*>Refresh sheet<', src)
    assert re.search(r'id="refresh-workbook-btn"[^>]*>Refresh workbook<', src)


def test_the_pane_keeps_the_choice_per_workbook_and_reports_a_failed_save():
    src = _read("index.html")
    body = src[src.index("function chooseServer("):]
    body = body[:body.index("\n      function ", 1)]
    assert "konsolServer.choose(serverInput.value).then(" in body, body
    assert "unsaved" in body, "a choice Excel could not save with the workbook must be reported"
    assert 'localStorage.getItem("konsol_server")' not in src
    hint = src[src.index("function showServer("):]
    hint = hint[:hint.index("\n      function ", 1)]
    assert "workbook" in hint, "the pane must say the choice belongs to this workbook"
