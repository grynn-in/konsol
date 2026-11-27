"""Cross-origin calls to the add-in endpoints, from an explicit list (konsol#605 B).

Decided by Deepak Pai, 27 Nov 2026 (konsol#605): one add-in install may reach
any site. The pane's Server field points it at another site; that site lets
the add-in's origin in only if Close Settings → Excel Add-in Origins lists it,
one exact origin per line.

The rules:

- CORS headers only on the endpoints the add-in calls, and only when the
  request's Origin exactly matches a listed origin. Never ``*``.
- Never ``Access-Control-Allow-Credentials``: the add-in sends its token in
  ``X-Konsolidat-Token`` and no cookies.
- A preflight (OPTIONS) gets the methods and the three headers the add-in
  sends. Frappe answers OPTIONS with an empty 200 and runs after_request
  hooks on it (frappe/app.py, v16.55.2), so the hook is where the headers go.
- Frappe's own ``allow_cors`` is not reused: its set_cors_headers always adds
  ``Access-Control-Allow-Credentials: true``, and it runs after the
  after_request hooks, so nothing could take that header off again. On the
  add-in endpoints the hook sets ``frappe.local.allow_cors = None``, which
  switches Frappe's mechanism off there, so the Close Settings list is the one
  source of truth for them.
- An unlisted origin gets no CORS headers (``Vary: Origin`` only), and a
  same-origin call behaves as before.
"""
import ast
import importlib.util
import json
import os
import re
import sys
import types

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(_HERE)
ADDIN = os.path.join(APP_DIR, "public", "excel-addin")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


model = _load("excel_addin_model_for_cors", os.path.join(APP_DIR, "excel_addin_model.py"))

LISTED = ("https://pane.example", "https://other.example:8443")
EPM = "/api/method/konsol.api.epm_batch"


# -- one origin ---------------------------------------------------------------

@pytest.mark.parametrize("text, origin", [
    ("https://pane.example", "https://pane.example"),
    ("  https://pane.example  ", "https://pane.example"),
    ("https://pane.example/", "https://pane.example"),
    ("https://PANE.Example", "https://pane.example"),
    ("https://pane.example:8443", "https://pane.example:8443"),
    ("https://pane.example:443", "https://pane.example"),
])
def test_an_origin_is_normalised(text, origin):
    assert model.parse_origin(text) == origin


@pytest.mark.parametrize("text, why", [
    ("", "empty"),
    ("*", "wildcard"),
    ("https://*.example", "wildcard"),
    ("http://pane.example", "https://"),
    ("pane.example", "https://"),
    ("https://pane.example/path", "no path"),
    ("https://pane.example/?q=1", "no path"),
    ("https://pane.example#x", "no path"),
    ("https://user@pane.example", "no path"),
    ("https://pane.example:99999", "port"),
    ("https://", "host"),
    ("https://pane example", "host"),
])
def test_anything_but_an_https_origin_is_refused(text, why):
    with pytest.raises(ValueError) as e:
        model.parse_origin(text)
    assert why in str(e.value), str(e.value)


# -- the list -----------------------------------------------------------------

def test_the_list_is_one_origin_per_line_blank_lines_skipped():
    text = "https://pane.example\n\n  https://OTHER.example:8443/ \r\n"
    assert model.parse_origin_list(text) == LISTED


def test_a_blank_list_allows_nothing():
    assert model.parse_origin_list(None) == ()
    assert model.parse_origin_list("") == ()


def test_a_bad_line_is_refused_by_its_number():
    with pytest.raises(ValueError) as e:
        model.parse_origin_list("https://pane.example\nhttps://x.example/addin")
    assert "Line 2" in str(e.value) and "https://x.example/addin" in str(e.value), str(e.value)


def test_a_repeated_origin_is_refused():
    with pytest.raises(ValueError) as e:
        model.parse_origin_list("https://pane.example\nhttps://PANE.example/")
    assert "twice" in str(e.value), str(e.value)


# -- the headers --------------------------------------------------------------

def test_a_listed_origin_gets_the_headers_without_credentials():
    h = model.cors_headers(EPM, "POST", "https://pane.example", LISTED)
    assert h["Access-Control-Allow-Origin"] == "https://pane.example"
    assert h["Vary"] == "Origin"
    assert "Access-Control-Allow-Credentials" not in h
    assert "Access-Control-Allow-Methods" not in h  # preflight only


def test_a_preflight_names_the_methods_and_headers_the_add_in_sends():
    h = model.cors_headers(EPM, "OPTIONS", "https://pane.example", LISTED)
    assert h["Access-Control-Allow-Origin"] == "https://pane.example"
    assert set(h["Access-Control-Allow-Methods"].replace(" ", "").split(",")) == {"GET", "POST", "OPTIONS"}
    assert {x.strip().lower() for x in h["Access-Control-Allow-Headers"].split(",")} == {
        "accept", "content-type", "x-konsolidat-token"}
    assert int(h["Access-Control-Max-Age"]) > 0
    assert "Access-Control-Allow-Credentials" not in h


@pytest.mark.parametrize("origin", [
    "https://unlisted.example",
    "https://pane.example.evil.example",
    "https://pane.example:8443",
    "http://pane.example",
    "null",
])
def test_an_unlisted_origin_gets_no_cors_headers(origin):
    h = model.cors_headers(EPM, "OPTIONS", origin, LISTED)
    assert h == {"Vary": "Origin"}, h


def test_the_match_is_exact_not_case_folded_from_the_request():
    """Browsers send Origin in lower case; a different spelling is a different
    origin. The list is normalised on save, the request is compared as sent."""
    h = model.cors_headers(EPM, "POST", "https://PANE.example", LISTED)
    assert "Access-Control-Allow-Origin" not in h


def test_no_origin_header_no_cors_headers():
    assert model.cors_headers(EPM, "POST", None, LISTED) == {}
    assert model.cors_headers(EPM, "POST", "", LISTED) == {}


@pytest.mark.parametrize("path", [
    "/api/method/konsol.api.get_handbook",
    "/api/resource/User",
    "/api/method/frappe.auth.get_logged_user",
    "/api/method/konsol.api.epm_batch/extra",
    "/api/v3/method/konsol.api.epm_batch",
    "/api/v2/method/konsol.api/epm_batch",
    "/api/v2/method/Close Settings/epm_batch",
    "/xapi/method/konsol.api.epm_batch",
    "/desk",
])
def test_other_paths_never_get_cors_headers(path):
    assert model.cors_headers(path, "OPTIONS", "https://pane.example", LISTED) == {}
    assert not model.is_addin_path(path)


#: Frappe v16 routes the same RPC under three prefixes (frappe/api/__init__.py:
#: /api, /api/v1 and /api/v2, each with /method/<method>).
PREFIXES = ("/api/method/", "/api/v1/method/", "/api/v2/method/")


@pytest.mark.parametrize("prefix", PREFIXES)
def test_every_api_prefix_of_an_add_in_method_gets_the_headers(prefix):
    path = prefix + "konsol.api.epm_batch"
    assert model.is_addin_path(path), path
    h = model.cors_headers(path, "OPTIONS", "https://pane.example", LISTED)
    assert h["Access-Control-Allow-Origin"] == "https://pane.example", (path, h)
    assert "Access-Control-Allow-Credentials" not in h


@pytest.mark.parametrize("prefix", PREFIXES)
def test_the_hook_switches_frappes_cors_off_on_every_prefix(prefix):
    headers, fr, _ = _hook("https://pane.example", path=prefix + "konsol.api.excel_addin_auth")
    assert headers["Access-Control-Allow-Origin"] == "https://pane.example"
    assert fr.local.allow_cors is None, prefix


_PATHS = frozenset("/api/method/" + m for m in model._ADDIN_METHODS)


def test_never_a_wildcard():
    for method in ("GET", "POST", "OPTIONS"):
        for path in _PATHS:
            h = model.cors_headers(path, method, "https://pane.example", LISTED)
            assert "*" not in json.dumps(h), h


#: Add-in endpoints the pane does not call yet: konsol#548 v1 T14 (the report
#: catalog and its generated form) calls them. They are listed in
#: ADDIN_ENDPOINTS ahead of the pane so one site can serve another site's
#: pane as soon as T14 ships. When the pane calls one, the test below fails
#: until it is taken off this list.
RESERVED_FOR_T14 = frozenset("/api/method/konsol.api." + m for m in (
    "list_report_templates", "report_catalog", "report_choices"))


def test_the_endpoints_are_exactly_the_ones_the_add_in_calls():
    called = set()
    for name in ("functions.js", "index.html", "ribbon.js", "server.js", "report.js"):
        with open(os.path.join(ADDIN, name), encoding="utf-8") as f:
            called |= set(re.findall(r"/api/method/[A-Za-z0-9_.]+", f.read()))
    assert RESERVED_FOR_T14 <= set(_PATHS), sorted(RESERVED_FOR_T14 - set(_PATHS))
    assert not called & RESERVED_FOR_T14, (
        f"the pane now calls {sorted(called & RESERVED_FOR_T14)}: take them off RESERVED_FOR_T14")
    assert called | RESERVED_FOR_T14 == set(_PATHS), (called | RESERVED_FOR_T14) ^ set(_PATHS)


# -- the after_request hook (frappe stubbed) ----------------------------------

class _Headers(dict):
    pass


class _Vary:
    """werkzeug's ``Response.vary``: a header set whose ``add`` appends to the
    Vary header and keeps what is already there (werkzeug HeaderSet)."""

    def __init__(self, headers):
        self._headers = headers

    def add(self, value):
        items = [v.strip() for v in self._headers.get("Vary", "").split(",") if v.strip()]
        if value.lower() not in (v.lower() for v in items):
            items.append(value)
        self._headers["Vary"] = ", ".join(items)


class _Response:
    def __init__(self, headers=None):
        self.headers = _Headers(headers or {})
        self.vary = _Vary(self.headers)


def _hook(origins_text, path=EPM, method="POST", origin="https://pane.example", response_headers=None):
    fr = types.ModuleType("frappe")
    fr.local = types.SimpleNamespace()
    settings = types.SimpleNamespace(excel_addin_origins=origins_text)
    fr.get_cached_doc = lambda doctype: settings if doctype == "Close Settings" else None
    fr.whitelist = lambda **kw: (lambda fn: fn)
    errors = []
    fr.log_error = lambda *a, **k: errors.append((a, k))
    fr.utils = types.SimpleNamespace(escape_html=lambda s: s)
    saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils", "konsol", "konsol.excel_addin_model")}
    sys.modules["frappe"] = fr
    sys.modules["frappe.utils"] = fr.utils
    pkg = types.ModuleType("konsol")
    pkg.__path__ = [APP_DIR]
    sys.modules["konsol"] = pkg
    sys.modules["konsol.excel_addin_model"] = model
    try:
        mod = _load("excel_addin_glue_for_cors", os.path.join(APP_DIR, "excel_addin.py"))
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    mod.frappe = fr
    request = types.SimpleNamespace(path=path, method=method,
                                    headers={"Origin": origin} if origin else {})
    response = _Response(response_headers)
    mod.add_cors_headers(response=response, request=request)
    return response.headers, fr, errors


def test_the_hook_answers_a_listed_preflight():
    headers, fr, _ = _hook("https://pane.example", method="OPTIONS")
    assert headers["Access-Control-Allow-Origin"] == "https://pane.example"
    assert "Access-Control-Allow-Credentials" not in headers
    assert fr.local.allow_cors is None, "Frappe's own allow_cors must be off on add-in paths"


def test_the_hook_gives_an_unlisted_origin_nothing():
    headers, fr, _ = _hook("https://other.example")
    assert dict(headers) == {"Vary": "Origin"}
    assert fr.local.allow_cors is None


def test_the_hook_leaves_other_paths_and_frappes_cors_alone():
    headers, fr, _ = _hook("https://pane.example", path="/api/method/konsol.api.get_handbook")
    assert dict(headers) == {}
    assert not hasattr(fr.local, "allow_cors")


def test_the_hook_adds_origin_to_vary_and_keeps_what_was_there():
    """Overwriting Vary would drop e.g. Accept-Encoding set by an earlier
    layer, and a cache could then serve a compressed body to a client that
    cannot take it (konsol#618 review)."""
    headers, _, _ = _hook("https://pane.example", response_headers={"Vary": "Accept-Encoding"})
    assert [v.strip() for v in headers["Vary"].split(",")] == ["Accept-Encoding", "Origin"], headers
    headers, _, _ = _hook("https://other.example", response_headers={"Vary": "Accept-Encoding"})
    assert [v.strip() for v in headers["Vary"].split(",")] == ["Accept-Encoding", "Origin"], headers


def test_the_hook_does_not_assign_the_vary_header():
    src = open(os.path.join(APP_DIR, "excel_addin.py")).read()
    assert 'headers["Vary"]' not in src and "vary.add(" in src


def test_a_stored_list_that_no_longer_parses_allows_nothing_and_is_logged():
    headers, _, errors = _hook("https://pane.example/bad-path")
    assert "Access-Control-Allow-Origin" not in headers
    assert errors, "a list that does not parse must be logged, not ignored"


def test_hooks_wire_the_cors_hook_after_request():
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    values = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name):
            try:
                values[n.targets[0].id] = ast.literal_eval(n.value)
            except ValueError:
                pass
    assert "konsol.excel_addin.add_cors_headers" in values["after_request"], values["after_request"]


def test_the_cors_hook_runs_first_after_request():
    """Frappe runs the after_request hooks in one try block (frappe/app.py,
    v16): a hook that raises skips every later one. Run first, the CORS hook
    has always set allow_cors = None before anything can raise, so Frappe's
    credentialed CORS can never reach an add-in path."""
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    (after,) = [ast.literal_eval(n.value) for n in tree.body
                if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id == "after_request"]
    assert after[0] == "konsol.excel_addin.add_cors_headers", after


# -- Close Settings -----------------------------------------------------------

_CS_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "close_settings")


def _fields():
    with open(os.path.join(_CS_DIR, "close_settings.json")) as f:
        doc = json.load(f)
    return doc, {fl["fieldname"]: fl for fl in doc["fields"]}


def test_close_settings_has_the_origins_field_on_an_excel_add_in_tab():
    doc, by_name = _fields()
    order = doc["field_order"]
    field = by_name["excel_addin_origins"]
    assert field["fieldtype"] == "Small Text"
    assert field["label"] == "Excel Add-in Origins"
    assert not field.get("reqd") and "default" not in field
    assert "one" in field["description"] and "https://" in field["description"]
    assert by_name["excel_addin_tab"]["fieldtype"] == "Tab Break"
    assert by_name["excel_addin_tab"]["label"] == "Excel Add-in"
    assert order.index("excel_addin_tab") < order.index("excel_addin_origins")


def test_the_origins_help_says_a_listed_origin_is_trusted_with_tokens():
    _, by_name = _fields()
    help_text = by_name["excel_addin_origins"]["description"]
    assert "every script" in help_text and "token" in help_text, help_text


def test_only_a_system_manager_may_change_the_origins():
    """Listing an origin trusts every script on it with this site's add-in
    tokens: a System Manager decision (permlevel 1), which EPM Admin may read."""
    doc, by_name = _fields()
    assert by_name["excel_addin_origins"].get("permlevel") == 1
    level1 = {p["role"]: p for p in doc["permissions"] if p.get("permlevel") == 1}
    assert set(level1) == {"System Manager", "EPM Admin"}, level1
    assert level1["System Manager"].get("read") and level1["System Manager"].get("write")
    assert level1["EPM Admin"].get("read") and not level1["EPM Admin"].get("write")
    # Every other Close Settings field stays at level 0, as before.
    others = [f["fieldname"] for f in doc["fields"] if f.get("permlevel") and f["fieldname"] != "excel_addin_origins"]
    assert others == [], others


def test_the_controller_validates_and_normalises_the_origins():
    src = open(os.path.join(_CS_DIR, "close_settings.py")).read()
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CloseSettings")
    validate = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "validate")
    calls = [c.func.attr for c in ast.walk(validate) if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)]
    assert "validate_excel_addin_origins" in calls
    assert "parse_origin_list" in src
