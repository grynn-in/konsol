"""The Konsol Handbook (konsol#407 phase 3; Handbook option C, Deepak Pai, 7 Nov 2026).

The product guide ships as markdown files in the app (konsol/handbook_pages/)
and updates on every deploy; a site adds its own pages as Konsol Guide Page
records. ``konsol.handbook.get_handbook`` returns both, and the "Konsol
Handbook" Custom HTML Block on the Konsol workspace shows them.

These tests pin the rules (konsol/handbook_model.py, no frappe) and the
shipped files. The live behaviour (the doctype, the hooks, the block) is
verified on the test stack.
"""
import ast
import json
import os

import pytest

from konsol import handbook_model as M

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGES = os.path.join(APP_DIR, "handbook_pages")
WORKSPACE = os.path.join(APP_DIR, "consolidation", "workspace", "konsol", "konsol.json")
BLOCK_DIR = os.path.join(APP_DIR, "consolidation", "custom_html_block", "konsol_handbook")
DOCTYPE_JSON = os.path.join(APP_DIR, "consolidation", "doctype", "konsol_guide_page",
                            "konsol_guide_page.json")
API = os.path.join(APP_DIR, "handbook.py")


def _read(path):
    with open(path) as f:
        return f.read()


def _page(title="A page", audience="Everyone", section="Concepts", order="10", body="Body."):
    return ("---\ntitle: %s\naudience: %s\nsection: %s\norder: %s\n---\n%s\n"
            % (title, audience, section, order, body))


# --- front-matter ------------------------------------------------------------

def test_a_page_parses_its_front_matter_and_body():
    page = M.parse_page(_page(title="Rates", audience="Group Accountant", order="20",
                              body="# Rates\n\nText."), "rates.md")
    assert page == {"slug": "rates", "title": "Rates", "audience": "Group Accountant",
                    "section": "Concepts", "order": 20, "markdown": "# Rates\n\nText.\n"}


@pytest.mark.parametrize("text, why", [
    ("title: x\n", "front-matter"),                                    # no opening ---
    ("---\ntitle: x\naudience: Everyone\nsection: S\norder: 1\n", "front-matter"),  # unclosed
    (_page().replace("order: 10\n", ""), "order"),                     # missing key
    (_page().replace("order: 10", "order: ten"), "order"),             # not an integer
    (_page(audience="Auditor"), "audience"),                           # undeclared audience
    (_page().replace("---\ntitle", "---\ncolour: red\ntitle"), "colour"),  # unknown key
    (_page().replace("---\ntitle", "---\ntitle: twice\ntitle"), "title"),  # duplicate key
    (_page(title=""), "title"),                                        # blank value
    (_page().replace("audience: Everyone", "audience Everyone"), "key: value"),  # no colon
    (_page(body=""), "body"),                                          # nothing to read
])
def test_a_malformed_page_is_refused_naming_the_file(text, why):
    with pytest.raises(M.HandbookError) as e:
        M.parse_page(text, "bad.md")
    assert "bad.md" in str(e.value) and why in str(e.value), str(e.value)


def test_pages_are_ordered_by_order_then_title(tmp_path):
    for name, title, order in (("c.md", "Zeta", 20), ("a.md", "Beta", 10),
                               ("b.md", "Alpha", 20)):
        (tmp_path / name).write_text(_page(title=title, order=str(order)))
    (tmp_path / "notes.txt").write_text("not a page")
    pages = M.load_product_pages(str(tmp_path))
    assert [p["title"] for p in pages] == ["Beta", "Alpha", "Zeta"]


def test_an_empty_guide_is_refused_not_shown_as_nothing(tmp_path):
    with pytest.raises(M.HandbookError):
        M.load_product_pages(str(tmp_path))


# --- audiences ---------------------------------------------------------------

def _pages():
    return [{"id": "konsol:%s" % a, "title": a, "audience": a, "section": "S", "html": ""}
            for a in M.AUDIENCES]


@pytest.mark.parametrize("audience, expect", [
    (None, list(M.AUDIENCES)),
    ("Everyone", ["Everyone"]),
    ("Entity Accountant", ["Everyone", "Entity Accountant"]),
    ("Close Lead", ["Everyone", "Close Lead"]),
])
def test_the_audience_filter_keeps_that_audience_and_everyone(audience, expect):
    assert [p["audience"] for p in M.for_audience(_pages(), audience)] == expect


def test_an_undeclared_audience_filter_is_refused():
    with pytest.raises(M.HandbookError):
        M.for_audience(_pages(), "Auditor")


def test_my_audiences_follow_the_roles():
    assert M.my_audiences(["EPM Analyst", "Entity Accountant", "Guest"]) == [
        "Entity Accountant", "Group Accountant"]
    assert M.my_audiences(["EPM Admin"]) == ["Close Lead"]
    assert M.my_audiences(["EPM User"]) == ["Viewer"]
    assert M.my_audiences(["System Manager"]) == []


# --- site pages: published and readable --------------------------------------

@pytest.mark.parametrize("roles, published, allowed", [
    (["EPM Admin"], 0, True),
    (["System Manager"], 0, True),
    (["EPM Admin"], 1, True),
    (["EPM User"], 1, True),
    (["Entity Accountant"], 1, True),
    (["EPM Analyst"], 0, False),
    (["EPM User"], 0, False),
    (["Entity Accountant"], None, False),
])
def test_a_reader_sees_a_site_page_only_once_published(roles, published, allowed):
    assert M.may_read_site_page(roles, published) is allowed


def test_the_list_condition_hides_drafts_from_readers_only():
    assert M.site_page_condition(["EPM Admin"]) == ""
    assert M.site_page_condition(["System Manager", "EPM User"]) == ""
    assert M.site_page_condition(["EPM User"]) == "`tabKonsol Guide Page`.`published` = 1"
    assert M.site_page_condition([]) == "`tabKonsol Guide Page`.`published` = 1"


def _site_row(**change):
    row = {"name": "KGP-00001", "title": "Our close calendar", "audience": "Everyone",
           "section": "This site", "sort_order": 0, "published": 1, "body": "Day 3."}
    row.update(change)
    return row


def test_assemble_marks_origin_and_never_shows_a_draft():
    product = [{"slug": "a", "title": "A", "audience": "Everyone", "section": "Start here",
                "order": 10, "markdown": "a"}]
    out = M.assemble(product, [_site_row(), _site_row(name="KGP-00002", published=0)],
                     roles=["EPM User"], audience=None, render=lambda md: "<p>%s</p>" % md)
    assert [(p["id"], p["origin"]) for p in out["pages"]] == [
        ("konsol:a", M.KONSOL), ("site:KGP-00001", M.THIS_SITE)]
    assert out["my_audiences"] == ["Viewer"]
    assert out["audiences"] == list(M.AUDIENCES)
    assert out["problems"] == []


def test_assemble_reports_a_site_page_it_cannot_show():
    out = M.assemble([], [_site_row(audience="Auditor"), _site_row(name="KGP-2", body="x")],
                     roles=["EPM Admin"], audience=None, render=lambda md: None)
    assert [p["id"] for p in out["pages"]] == []
    assert len(out["problems"]) == 2
    assert "KGP-00001" in out["problems"][0] and "Auditor" in out["problems"][0]
    assert "KGP-2" in out["problems"][1]


def test_start_here_has_one_tile_per_role_and_marks_mine():
    product = [{"slug": "s-%d" % i, "title": "Start: %s" % a, "audience": a,
                "section": M.START_HERE, "order": i, "markdown": "x"}
               for i, a in enumerate(M.AUDIENCES[1:])]
    out = M.assemble(product, [], roles=["EPM Analyst"], audience="Viewer",
                     render=lambda md: md)
    tiles = out["start_here"]
    assert [t["audience"] for t in tiles] == list(M.AUDIENCES[1:])
    assert [t["mine"] for t in tiles] == [False, True, False, False]
    # the tiles do not follow the audience filter; the pages do
    assert {p["audience"] for p in out["pages"]} == {"Viewer"}


# --- sanitisation ------------------------------------------------------------

@pytest.mark.parametrize("dirty, gone", [
    ("<p>x</p><script>alert(1)</script>", ["<script", "alert(1)"]),
    ('<img src="x" onerror="alert(1)">', ["onerror"]),
    ('<a href="javascript:alert(1)">x</a>', ["javascript:"]),
    ('<p onclick="alert(1)" style="position:fixed">x</p>', ["onclick", "style"]),
    ("<iframe src='https://example.com'></iframe>", ["<iframe"]),
    ("<style>body{display:none}</style><p>x</p>", ["<style", "display:none"]),
    ('<svg onload="alert(1)"><circle/></svg>', ["<svg", "onload"]),
    ('<form action="/api/method/logout"><input name="x"></form>', ["<form", "<input"]),
])
def test_sanitise_strips_script_and_event_attributes(dirty, gone):
    clean = M.sanitize(dirty)
    for bad in gone:
        assert bad not in clean, (bad, clean)


def test_sanitise_drops_every_class_so_a_page_cannot_reach_desk_css():
    """Review of #412: desk.bundle.css is linked into the block's shadow
    root, so a class such as position-fixed could lay a page over the Desk."""
    clean = M.sanitize('<code class="position-fixed w-100 vh-100">x</code>'
                       '<table class="table"><tr><td>d</td></tr></table>')
    assert "class=" not in clean, clean


@pytest.mark.parametrize("src, kept", [
    ("https://tracker.example.com/p.png", False),
    ("//tracker.example.com/p.png", False),
    ("/api/method/frappe.auth.get_logged_user", False),
    ("/files/close-calendar.png", True),
    ("/private/files/close-calendar.png", True),
])
def test_an_image_loads_only_from_this_sites_files(src, kept):
    clean = M.sanitize('<img src="%s" alt="calendar">' % src)
    assert ('src="%s"' % src in clean) is kept, clean
    assert 'alt="calendar"' in clean


def test_sanitise_keeps_what_markdown_writes():
    html = ('<h2 id="rates">Rates</h2><p><strong>a</strong> <em>b</em> <code>c</code></p>'
            '<ul><li>x</li></ul><table><thead><tr><th>h</th></tr>'
            '</thead><tbody><tr><td>d</td></tr></tbody></table><pre><code>K.EPM</code></pre>'
            '<a href="/desk/konsol-setup">Setup</a> <a href="https://example.com">x</a>')
    clean = M.sanitize(html)
    for keep in ('<h2 id="rates">', "<strong>a</strong>", "<table", "<td>d</td>",
                 '<a href="/desk/konsol-setup"', '<a href="https://example.com"', "<pre><code>"):
        assert keep in clean, (keep, clean)


def test_assemble_sanitises_every_page():
    product = [{"slug": "a", "title": "A", "audience": "Everyone", "section": "S", "order": 1,
                "markdown": "x"}]
    out = M.assemble(product, [_site_row()], roles=["EPM Admin"], audience=None,
                     render=lambda md: '<p onclick="x()">ok</p><script>bad()</script>')
    for page in out["pages"]:
        assert "onclick" not in page["html"] and "<script" not in page["html"], page


# --- the shipped guide ---------------------------------------------------------

def test_the_shipped_guide_loads_and_has_a_start_page_per_role():
    pages = M.load_product_pages(PAGES)
    start = {p["audience"] for p in pages if p["section"] == M.START_HERE}
    assert start == set(M.AUDIENCES) - {"Everyone"}, start
    assert len({p["slug"] for p in pages}) == len(pages)


def test_the_shipped_guide_names_no_customer():
    """Generic examples only (E1, ZZ01, "Group"): the repo is public."""
    text = " ".join(_read(os.path.join(PAGES, f)) for f in os.listdir(PAGES)).lower()
    for word in ("d365", "dynamics", "dataareaid"):
        assert word not in text, word


# --- the doctype ---------------------------------------------------------------

def _doctype():
    return json.loads(_read(DOCTYPE_JSON))


def test_the_guide_page_doctype_follows_the_design_standard():
    dt = _doctype()
    assert dt["name"] == "Konsol Guide Page" and dt["module"] == "Consolidation"
    assert dt["title_field"] == "title" and dt["track_changes"] == 1
    fields = {f["fieldname"]: f for f in dt["fields"]}
    assert fields["audience"]["fieldtype"] == "Select"
    assert fields["audience"]["options"].split("\n") == list(M.AUDIENCES)
    assert fields["body"]["fieldtype"] == "Markdown Editor"
    assert fields["published"]["fieldtype"] == "Check"
    assert fields["sort_order"]["fieldtype"] == "Int"
    for name in ("title", "audience", "section", "body"):
        assert fields[name].get("reqd") == 1, name
    assert any(f["fieldtype"] == "Tab Break" for f in dt["fields"])
    for f in dt["fields"]:
        if f["fieldtype"] not in ("Column Break",):
            assert f.get("label"), f["fieldname"]


def test_the_guide_page_permissions():
    perms = {p["role"]: p for p in _doctype()["permissions"]}
    for role in M.EDITOR_ROLES:
        assert perms[role].get("write") == 1 and perms[role].get("create") == 1, role
    for role in ("EPM Analyst", "EPM User", "Entity Accountant"):
        assert perms[role].get("read") == 1, role
        assert not perms[role].get("write") and not perms[role].get("create"), role
    hooks = _read(os.path.join(APP_DIR, "hooks.py"))
    assert '"Konsol Guide Page"] = "konsol.handbook.guide_page_conditions"' in hooks
    assert '"Konsol Guide Page"] = "konsol.handbook.has_guide_page_permission"' in hooks


# --- the method ----------------------------------------------------------------

def test_get_handbook_is_a_gated_get_that_never_bypasses_permissions():
    source = _read(API)
    tree = ast.parse(source)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_handbook")
    dec = next(d for d in fn.decorator_list if isinstance(d, ast.Call))
    assert [ast.literal_eval(k.value) for k in dec.keywords if k.arg == "methods"] == [["GET"]]
    assert "only_for(M.READER_ROLES)" in source
    assert "ignore_permissions" not in source and "frappe.get_all(" not in source
    assert "frappe.get_list(" in source and '"published": 1' in source
    assert "md_to_html" in source


# --- the block and the workspace -------------------------------------------------

def test_the_block_writes_only_sanitised_html():
    js = _read(os.path.join(BLOCK_DIR, "konsol_handbook.js"))
    assert js.count("innerHTML") == 1 and "body.innerHTML = page.html" in js
    for sink in ("outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert sink not in js, sink
    assert "konsol.handbook.get_handbook" in js
    assert "<script" not in _read(os.path.join(BLOCK_DIR, "konsol_handbook.html")).lower()


def test_the_home_page_does_not_show_the_handbook():
    """Deepak Pai, 7 Nov 2026: "dont show handbook on .../desk/konsol". The
    Handbook lives on its own workspace, opened from the left menu."""
    ws = json.loads(_read(WORKSPACE))
    assert "Konsol Handbook" not in [b["custom_block_name"] for b in ws["custom_blocks"]]
    content = json.loads(ws["content"])
    assert "Konsol Handbook" not in [b["data"].get("custom_block_name") for b in content]
    assert "Konsol Close Checklist" in [b["data"].get("custom_block_name") for b in content]
    assert ws["modified"] >= "2026-11-07 20:00:00", ws["modified"]


STRUCTURE_WORKSPACE = os.path.join(APP_DIR, "structure", "workspace", "structure",
                                   "structure.json")


def test_the_setup_shortcut_is_a_page_link_shown_only_to_its_roles():
    """A Page shortcut is shown only to a user the Page admits
    (frappe.desk.utils.is_item_allowed); konsol-setup admits EPM Admin and
    System Manager."""
    # Desk IA (7 Nov 2026): the shortcut moved from the Close home to Structure.
    ws = json.loads(_read(STRUCTURE_WORKSPACE))
    (sc,) = [s for s in ws["shortcuts"] if s["label"] == "Konsol Setup"]
    assert (sc["type"], sc["link_to"]) == ("Page", "konsol-setup")
    assert any(b["type"] == "shortcut" and b["data"]["shortcut_name"] == "Konsol Setup"
               for b in json.loads(ws["content"]))
    page = json.loads(_read(os.path.join(APP_DIR, "consolidation", "page", "konsol_setup",
                                         "konsol_setup.json")))
    assert {r["role"] for r in page["roles"]} == {"EPM Admin", "System Manager"}


def test_desk_blocks_ships_the_handbook_to_its_readers():
    source = _read(os.path.join(APP_DIR, "desk_blocks.py"))
    tree = ast.parse(source)
    blocks = next(ast.literal_eval(n.value) for n in tree.body
                  if isinstance(n, ast.Assign) and n.targets[0].id == "BLOCKS")
    (entry,) = [b for b in blocks if b[0] == "Konsol Handbook"]
    assert entry[1] == "konsol_handbook"
    assert set(entry[2]) == set(M.READER_ROLES)


HANDBOOK_WORKSPACE = os.path.join(APP_DIR, "consolidation", "workspace", "konsol_handbook",
                                  "konsol_handbook.json")


def test_the_handbook_has_its_own_workspace_holding_the_block_and_guide_pages():
    """The left-menu Handbook row opens this page; a user may make it their
    Default Workspace (User > Workspace), which is then where /desk lands."""
    ws = json.loads(_read(HANDBOOK_WORKSPACE))
    assert (ws["doctype"], ws["name"], ws["label"], ws["title"]) == (
        "Workspace", "Konsol Handbook", "Konsol Handbook", "Konsol Handbook")
    assert ws["public"] == 1 and ws["standard"] == 1 and ws["module"] == "Consolidation"
    assert ws["roles"] == [] and not ws["for_user"]
    assert [b["custom_block_name"] for b in ws["custom_blocks"]] == ["Konsol Handbook"]
    content = json.loads(ws["content"])
    # Desk IA (7 Nov 2026): below the block, one link to the site's own pages.
    assert [(b["type"], b["data"].get("custom_block_name") or b["data"].get("shortcut_name"),
             b["data"]["col"]) for b in content] == [
        ("custom_block", "Konsol Handbook", 12), ("shortcut", "Konsol Guide Pages", 4)]
    assert [(s["type"], s["link_to"]) for s in ws["shortcuts"]] == [("DocType", "Konsol Guide Page")]


def test_every_page_row_shows_an_explicit_open_close_control():
    """Deepak Pai, 7 Nov 2026: "the handbook doesnt have a visible button to
    open/close". A flex <summary> loses the browser's disclosure triangle, so
    the block draws its own chevron and an Open/Close label, and offers
    Expand all / Collapse all."""
    js = _read(os.path.join(BLOCK_DIR, "konsol_handbook.js"))
    css = _read(os.path.join(BLOCK_DIR, "konsol_handbook.css"))
    for needle in ('"kh-chevron"', '"kh-toggle"', '"Expand all"', '"Collapse all"',
                   'setAttribute("aria-expanded"'):
        assert needle in js, needle
    assert ".kh-chevron" in css and ".kh-page[open] .kh-chevron" in css
    assert "list-style: none" in css and "::-webkit-details-marker" in css


def test_title_buttons_and_search_share_one_line_with_a_separator():
    """Deepak Pai, 7 Nov 2026: "put konsol handbook, expand all/collapse all
    buttons and search in the same line and put a separator". The tools never
    wrap on a wide screen; only a narrow one may stack them."""
    js = _read(os.path.join(BLOCK_DIR, "konsol_handbook.js"))
    css = _read(os.path.join(BLOCK_DIR, "konsol_handbook.css"))
    assert 'el("span", "kh-sep")' in js
    order = [js.index(s) for s in ('"Expand all"', 'el("span", "kh-sep")', "tools.append(search)")]
    assert order == sorted(order), order
    head = css[css.index(".kh-head {"):]
    head = head[:head.index("}")]
    tools = css[css.index(".kh-tools {"):]
    tools = tools[:tools.index("}")]
    assert "flex-wrap: nowrap" in head and "flex-wrap: nowrap" in tools
    # Wrap on the block's width (a container query), never the window's.
    assert ".kh-sep {" in css and "@container (max-width:" in css and "@media (max-width:" not in css
    assert "container-type: inline-size" in css and "min-width: 120px" in css
