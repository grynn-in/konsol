"""The Excel add-in manifest is built per site, from its host_name (konsol#605 A).

Decided by Deepak Pai, 27 Nov 2026 (konsol#605): build both A and B.

Before: ``public/excel-addin/manifest.xml`` and ``manifest.demo.xml`` were
byte-identical static files that named one demo server in 11 places, so every
other site needed a hand-edited copy with a new ``<Id>``.

Now: one template, ``konsol/excel_addin_manifest.template.xml``, holds
placeholders, and ``konsol.excel_addin.manifest`` (a GET) renders it for the
site that serves the request:

- every URL is the site's ``host_name`` (scheme included); nothing else but
  Microsoft's office.js domain appears;
- ``<Id>`` is a UUID derived from that address, stable per site and different
  between sites, so Excel never treats two sites as one add-in;
- ``DisplayName`` names the site;
- a site without ``host_name``, or one that is not https, is refused with a
  message that says so; there is no fallback address.

The pure rules live in ``konsol/excel_addin_model.py`` (no frappe).
"""
import importlib.util
import os
import re
import sys
import types
import uuid
import xml.etree.ElementTree as ET

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(_HERE)
ADDIN = os.path.join(APP_DIR, "public", "excel-addin")
TEMPLATE = os.path.join(APP_DIR, "excel_addin_manifest.template.xml")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


model = _load("excel_addin_model_under_test", os.path.join(APP_DIR, "excel_addin_model.py"))


def _template():
    with open(TEMPLATE, encoding="utf-8") as f:
        return f.read()


def _render(base="https://site-a.example"):
    return model.render_manifest(_template(), base)


# -- the static manifests are gone ------------------------------------------

def test_no_static_manifest_names_a_server():
    """The two hard-coded copies are retired: an installable file that names
    one server is the bug (#605). The template is not in public/, so nobody
    can install it unrendered."""
    for name in ("manifest.xml", "manifest.demo.xml"):
        assert not os.path.exists(os.path.join(ADDIN, name)), name
    for name in os.listdir(ADDIN):
        if name.endswith(".xml"):
            pytest.fail("public/excel-addin still ships a manifest: " + name)


def test_the_template_names_no_server():
    src = _template()
    hosts = set(re.findall(r"https?://([^/\"<\s]+)", src))
    assert hosts <= {"schemas.microsoft.com", "www.w3.org", "appsforoffice.microsoft.com"}, hosts
    for placeholder in model.PLACEHOLDERS:
        assert placeholder in src, placeholder


# -- the site's address -------------------------------------------------------

@pytest.mark.parametrize("host_name, base", [
    ("https://site-a.example", "https://site-a.example"),
    ("https://site-a.example/", "https://site-a.example"),
    ("https://Site-A.Example", "https://site-a.example"),
    ("https://site-a.example:8443", "https://site-a.example:8443"),
    ("https://site-a.example:443", "https://site-a.example"),
])
def test_the_base_is_the_host_name_origin(host_name, base):
    assert model.site_base_url(host_name) == base


@pytest.mark.parametrize("host_name, why", [
    (None, "no host_name"),
    ("", "no host_name"),
    ("   ", "no host_name"),
    ("site-a.example", "scheme"),
    ("http://site-a.example", "https"),
    ("https://site-a.example/konsol", "path"),
    ("https://site-a.example?x=1", "path"),
    ("https://user@site-a.example", "address"),
])
def test_a_site_without_a_usable_host_name_is_refused(host_name, why):
    with pytest.raises(ValueError) as e:
        model.site_base_url(host_name)
    assert why in str(e.value), str(e.value)
    assert "set-config host_name" in str(e.value), str(e.value)


def test_the_refusal_never_names_another_server():
    with pytest.raises(ValueError) as e:
        model.site_base_url("")
    assert "konsolidat.com" not in str(e.value)


# -- the rendered manifest ----------------------------------------------------

def _urls(xml):
    root = ET.fromstring(xml)
    found = []
    for el in root.iter():
        for value in list(el.attrib.values()) + [el.text or ""]:
            found += re.findall(r"https?://[^\s\"<]+", value)
    return found


def test_the_rendered_manifest_names_no_host_but_the_sites_own():
    xml = _render("https://site-a.example")
    hosts = {re.match(r"https?://([^/]+)", u).group(1) for u in _urls(xml)}
    assert hosts == {"site-a.example", "appsforoffice.microsoft.com"}, hosts
    assert "konsolidat.com" not in xml
    assert "{{" not in xml and "}}" not in xml


def test_every_url_the_add_in_loads_is_on_the_site():
    xml = _render("https://site-a.example")
    for url in _urls(xml):
        if "appsforoffice.microsoft.com" in url:
            continue
        assert url == "https://site-a.example" or url.startswith("https://site-a.example/"), url
    assert "https://site-a.example/assets/konsol/excel-addin/index.html" in xml
    assert "https://site-a.example/assets/konsol/excel-addin/functions.json" in xml


def test_the_id_is_stable_per_site_and_differs_between_sites():
    a1 = ET.fromstring(_render("https://site-a.example"))
    a2 = ET.fromstring(_render("https://site-a.example"))
    b = ET.fromstring(_render("https://site-b.example"))
    ns = {"o": "http://schemas.microsoft.com/office/appforoffice/1.1"}
    id_a1 = a1.find("o:Id", ns).text
    assert id_a1 == a2.find("o:Id", ns).text
    assert id_a1 != b.find("o:Id", ns).text
    assert str(uuid.UUID(id_a1)) == id_a1
    assert id_a1 == model.manifest_id("https://site-a.example")


def test_the_display_name_names_the_site():
    root = ET.fromstring(_render("https://site-a.example"))
    ns = {"o": "http://schemas.microsoft.com/office/appforoffice/1.1"}
    assert root.find("o:DisplayName", ns).get("DefaultValue") == "Konsolidat (site-a.example)"


def test_the_rendered_manifest_keeps_the_templates_version():
    version = re.search(r"<Version>([^<]+)</Version>", _template()).group(1)
    assert "<Version>%s</Version>" % version in _render()


def test_a_template_missing_a_placeholder_is_refused():
    with pytest.raises(ValueError):
        model.render_manifest(_template().replace("{{ADDIN_ID}}", "x"), "https://site-a.example")


@pytest.mark.parametrize("site, name", [
    ("konsolidat.local", "konsolidat-konsolidat.local.xml"),
    ("site a/../x", "konsolidat-site-a-..-x.xml"),
])
def test_the_download_is_named_after_the_site(site, name):
    assert model.manifest_filename(site) == name


# -- the endpoint (frappe stubbed) -------------------------------------------

class _Response(dict):
    def __getattr__(self, k):
        return self.get(k)

    def __setattr__(self, k, v):
        self[k] = v


def _glue(host_name, site="konsolidat.local"):
    """konsol/excel_addin.py with frappe stubbed: returns (module, frappe)."""
    pages = []
    fr = types.ModuleType("frappe")
    fr.conf = {"host_name": host_name} if host_name is not None else {}
    fr.local = types.SimpleNamespace(site=site)
    fr.response = _Response()
    fr.whitelist = lambda **kw: (lambda fn: setattr(fn, "_whitelist", kw) or fn)
    fr.respond_as_web_page = lambda title, html, **kw: pages.append((title, html, kw))
    fr.utils = types.SimpleNamespace(escape_html=lambda s: s.replace("<", "&lt;").replace(">", "&gt;"))
    fr.pages = pages
    saved = {k: sys.modules.get(k) for k in ("frappe", "frappe.utils", "konsol", "konsol.excel_addin_model")}
    sys.modules["frappe"] = fr
    sys.modules["frappe.utils"] = fr.utils
    pkg = types.ModuleType("konsol")
    pkg.__path__ = [APP_DIR]
    sys.modules["konsol"] = pkg
    sys.modules["konsol.excel_addin_model"] = model
    try:
        mod = _load("excel_addin_glue_under_test", os.path.join(APP_DIR, "excel_addin.py"))
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    mod.frappe = fr
    return mod, fr


def test_the_endpoint_is_a_guest_get():
    mod, _ = _glue("https://site-a.example")
    assert mod.manifest._whitelist == {"allow_guest": True, "methods": ["GET"]}


def test_the_endpoint_downloads_the_sites_manifest():
    mod, fr = _glue("https://site-a.example", site="site-a.local")
    mod.manifest()
    assert fr.response["type"] == "download"
    assert fr.response["filename"] == "konsolidat-site-a.local.xml"
    assert fr.response["content_type"] == "application/xml"
    xml = fr.response["filecontent"]
    assert "https://site-a.example/assets/konsol/excel-addin/index.html" in xml
    assert "konsolidat.com" not in xml
    assert not fr.pages


def test_the_endpoint_refuses_a_site_without_host_name():
    mod, fr = _glue(None)
    mod.manifest()
    assert "filecontent" not in fr.response
    ((title, html, kw),) = fr.pages
    assert "host_name" in html and "set-config host_name" in html, html
    assert kw.get("http_status_code") == 409, kw
    assert "konsolidat.com" not in html


# -- the Desk link ------------------------------------------------------------

def test_close_settings_links_the_download():
    import json
    path = os.path.join(APP_DIR, "consolidation", "doctype", "close_settings", "close_settings.json")
    with open(path) as f:
        by_name = {fl["fieldname"]: fl for fl in json.load(f)["fields"]}
    link = by_name["excel_addin_download"]
    assert link["fieldtype"] == "HTML"
    assert 'href="/api/method/konsol.excel_addin.manifest"' in link["options"]
    assert "Download Excel add-in" in link["options"]


def test_the_handbook_links_the_download():
    with open(os.path.join(APP_DIR, "handbook_pages", "excel-addin.md")) as f:
        page = f.read()
    assert "(/api/method/konsol.excel_addin.manifest)" in page
    assert "/assets/konsol/excel-addin/manifest.xml" not in page
    for word in ("wef", "Windows", "centralized", "HTTPS"):
        assert word in page, word
