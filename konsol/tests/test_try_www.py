"""The `/try` web page (demo mode D3): one button into
`konsol.demo_mode.try_demo` while demo mode is on; Frappe's 404 when it is
off or the demo user is refused.

Source-level and stub-rendered, mirroring test_close_www.py: no frappe boot
on the host runner, so the controller is loaded against a stub `frappe` and a
stub `konsol.demo_mode` whose `decision()` is set per test.
"""
import importlib.util
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WWW_DIR = os.path.join(APP_DIR, "www")
PY_PATH = os.path.join(WWW_DIR, "try.py")
HTML_PATH = os.path.join(WWW_DIR, "try.html")
TRY_URL = "/api/method/konsol.demo_mode.try_demo"


def _read(path):
    with open(path) as f:
        return f.read()


# ---- files exist (red: they are missing) --------------------------------

def test_controller_file_exists():
    assert os.path.exists(PY_PATH)


def test_template_file_exists():
    assert os.path.exists(HTML_PATH)


# ---- source -----------------------------------------------------------------

def test_controller_is_not_cached():
    assert re.search(r"^no_cache = 1$", _read(PY_PATH), re.M)


def test_controller_refuses_with_frappes_not_found_page():
    # PageDoesNotExistError is what website/serve.py turns into the 404 page.
    assert "raise frappe.PageDoesNotExistError" in _read(PY_PATH)


def test_controller_does_not_read_the_key_itself():
    # One place reads frappe.conf.demo_user: konsol/demo_mode.py.
    assert "demo_user" not in _read(PY_PATH)
    assert "frappe.conf" not in _read(PY_PATH)


def test_template_links_the_endpoint_once_and_says_the_site_resets():
    html = _read(HTML_PATH)
    assert html.count("{{ try_url }}") == 1
    assert "Konsol" in html
    lower = html.lower()
    assert "hour" in lower
    assert "shared" in lower


# ---- stub-rendered ----------------------------------------------------------------

def _load_controller(allowed):
    frappe = types.ModuleType("frappe")
    frappe.PageDoesNotExistError = type("PageDoesNotExistError", (Exception,), {})
    frappe.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
    frappe.session = types.SimpleNamespace(user="Guest")
    frappe.local = types.SimpleNamespace(flags=types.SimpleNamespace())

    demo_mode = types.ModuleType("konsol.demo_mode")
    demo_mode.calls = []
    demo_mode.decision = lambda: demo_mode.calls.append("decision") or types.SimpleNamespace(
        allowed=allowed, reason="allowed" if allowed else "demo mode is off"
    )

    import konsol  # the real package; only its demo_mode attribute is swapped

    saved_modules = {k: sys.modules.get(k) for k in ("frappe", "konsol.demo_mode")}
    saved_attr = getattr(konsol, "demo_mode", None)
    sys.modules["frappe"] = frappe
    sys.modules["konsol.demo_mode"] = demo_mode
    konsol.demo_mode = demo_mode
    try:
        spec = importlib.util.spec_from_file_location("try_www_under_test", PY_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for k, v in saved_modules.items():
            if v is not None:
                sys.modules[k] = v
            else:
                sys.modules.pop(k, None)
        if saved_attr is not None:
            konsol.demo_mode = saved_attr
        else:
            try:
                del konsol.demo_mode
            except AttributeError:
                pass
    return module, frappe, demo_mode


def _render(context):
    html = _read(HTML_PATH)

    def sub(m):
        return str(getattr(context, m.group(1)))

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", sub, html)


def test_on_renders_one_button_to_the_endpoint():
    module, _frappe, demo_mode = _load_controller(allowed=True)
    context = types.SimpleNamespace()
    module.get_context(context)
    assert demo_mode.calls == ["decision"]
    html = _render(context)
    hrefs = re.findall(r'href="([^"]+)"', html)
    assert hrefs.count(TRY_URL) == 1, hrefs
    assert context.no_cache == 1


def test_off_raises_the_not_found_page():
    module, frappe, _demo_mode = _load_controller(allowed=False)
    try:
        module.get_context(types.SimpleNamespace())
    except frappe.PageDoesNotExistError:
        return
    raise AssertionError("/try rendered while demo mode is off")


def test_off_sets_nothing_on_the_context_before_refusing():
    module, frappe, _demo_mode = _load_controller(allowed=False)
    context = types.SimpleNamespace()
    try:
        module.get_context(context)
    except frappe.PageDoesNotExistError:
        pass
    assert vars(context) == {}
