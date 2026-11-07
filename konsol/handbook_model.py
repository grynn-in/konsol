"""The Konsol Handbook's rules, with no frappe (konsol#407 phase 3).

Decision (Deepak Pai, 7 Nov 2026, Handbook option C, recorded on #407): the
product guide ships as markdown in the app (``konsol/handbook_pages/*.md``)
and updates on every deploy; a site adds its own pages as ``Konsol Guide
Page`` records, listed in the same Handbook block. Rejected: A (articles only
as records: product text could not be updated without overwriting a site's
edits) and B (shipped HTML only: a site could not add its own pages).

Here: the front-matter of a product page, the order pages are shown in, the
audience filter, the "Start here" tiles, who may read a site page, and the
sanitiser every rendered page passes through. ``konsol.handbook`` holds the
frappe calls.

A product page is a markdown file that opens with front-matter::

    ---
    title: Upload and submit a trial balance
    audience: Entity Accountant
    section: Start here
    order: 10
    ---
    The body, in markdown.

Every key is required, no other key is accepted, and a malformed file is
refused naming the file (``HandbookError``): the guide ships with the app, so
a broken page is a build defect, never a page silently left out.

Audience is a reading aid, not access control: every reader of the Handbook
sees every published page, and the filter only narrows the list.
"""
import os

import nh3

#: Who a page is for. "Everyone" first; then one per role, in the order the
#: close runs. The Konsol Guide Page doctype's Select carries the same list.
AUDIENCES = ("Everyone", "Entity Accountant", "Group Accountant", "Close Lead", "Viewer")

#: The audience each konsol role reads as (install.ROLES' descriptions).
ROLE_AUDIENCE = {
    "Entity Accountant": "Entity Accountant",
    "EPM Analyst": "Group Accountant",
    "EPM Admin": "Close Lead",
    "EPM User": "Viewer",
}

#: The roles that may read the Handbook (get_handbook's only_for, and the
#: roles the block is shown to).
READER_ROLES = ("EPM Admin", "EPM Analyst", "EPM User", "Entity Accountant", "System Manager")

#: The roles that write site pages and read drafts.
EDITOR_ROLES = ("EPM Admin", "System Manager")

#: The section whose product pages are the per-role "Start here" tiles.
START_HERE = "Start here"

#: Where a page comes from, as the block labels it.
KONSOL = "Konsol"
THIS_SITE = "This site"

DOCTYPE = "Konsol Guide Page"

#: The site-page fields ``assemble`` reads.
SITE_FIELDS = ("name", "title", "audience", "section", "sort_order", "published", "body")

_KEYS = ("title", "audience", "section", "order")
_FENCE = "---"


class HandbookError(ValueError):
    """A product page that cannot be read, or a filter that names no audience."""


# --- product pages -------------------------------------------------------------

def parse_page(text, source):
    """One product page: ``{slug, title, audience, section, order, markdown}``.

    ``source`` is the file name; the slug is its stem. Raises HandbookError
    naming ``source`` for anything malformed.
    """
    def refuse(why):
        raise HandbookError("Handbook page %s: %s" % (source, why))

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FENCE:
        refuse("it must open with a front-matter block between --- lines")
    try:
        close = next(i for i in range(1, len(lines)) if lines[i].strip() == _FENCE)
    except StopIteration:
        refuse("its front-matter block is not closed with a --- line")

    meta = {}
    for raw in lines[1:close]:
        line = raw.strip()
        if not line:
            continue
        key, sep, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if not sep:
            refuse("front-matter line %r is not key: value" % line)
        if key not in _KEYS:
            refuse("unknown front-matter key %r; the keys are %s" % (key, ", ".join(_KEYS)))
        if key in meta:
            refuse("front-matter key %r is given twice" % key)
        if not value:
            refuse("front-matter key %r is blank" % key)
        meta[key] = value

    missing = [k for k in _KEYS if k not in meta]
    if missing:
        refuse("front-matter is missing %s" % ", ".join(missing))
    if meta["audience"] not in AUDIENCES:
        refuse("audience %r is not one of %s" % (meta["audience"], ", ".join(AUDIENCES)))
    try:
        order = int(meta["order"])
    except ValueError:
        refuse("order %r is not a whole number" % meta["order"])

    body = "".join(lines[close + 1:])
    if not body.strip():
        refuse("it has no body")
    return {"slug": os.path.splitext(os.path.basename(source))[0], "title": meta["title"],
            "audience": meta["audience"], "section": meta["section"], "order": order,
            "markdown": body.lstrip("\n")}


def load_product_pages(directory):
    """Every ``*.md`` page in ``directory``, ordered by ``order`` then title.

    An empty guide is refused: the app ships one, so none means the files
    did not ship."""
    names = sorted(n for n in os.listdir(directory) if n.endswith(".md"))
    if not names:
        raise HandbookError("Handbook: no pages in %s" % directory)
    pages = []
    for name in names:
        with open(os.path.join(directory, name), encoding="utf-8") as f:
            pages.append(parse_page(f.read(), name))
    return sorted(pages, key=lambda p: (p["order"], p["title"]))


# --- audiences -------------------------------------------------------------------

def my_audiences(roles):
    """The audiences the reader's roles read as, in AUDIENCES order."""
    mine = {ROLE_AUDIENCE[r] for r in roles or () if r in ROLE_AUDIENCE}
    return [a for a in AUDIENCES if a in mine]


def for_audience(pages, audience):
    """``pages`` for ``audience``: its own pages and Everyone's. None keeps
    every page; "Everyone" keeps Everyone's only. An undeclared audience is
    refused, never read as no filter."""
    if audience is None:
        return list(pages)
    if audience not in AUDIENCES:
        raise HandbookError("%r is not an audience: use one of %s" % (audience, ", ".join(AUDIENCES)))
    keep = {"Everyone", audience}
    return [p for p in pages if p["audience"] in keep]


# --- site pages ---------------------------------------------------------------------

def is_editor(roles):
    return bool(set(roles or ()) & set(EDITOR_ROLES))


def may_read_site_page(roles, published):
    """has_permission for one Konsol Guide Page: an editor reads every page;
    anyone else a published one only. Always a bool (Frappe v16 allows on
    exactly True)."""
    return True if is_editor(roles) else bool(published)


def site_page_condition(roles):
    """permission_query_conditions for Konsol Guide Page: nothing for an
    editor; published pages only for anyone else."""
    return "" if is_editor(roles) else "`tab%s`.`published` = 1" % DOCTYPE


# --- sanitising ------------------------------------------------------------------

#: What markdown writes, and nothing that runs, loads a frame, submits or
#: restyles the page. ``style`` attributes and elements, ``svg``, forms and
#: every ``on*`` attribute are dropped; ``script`` and ``style`` lose their
#: content too (nh3's default clean_content_tags). No element keeps a
#: ``class``: Frappe links desk.bundle.css into the block's shadow root, so a
#: class such as ``position-fixed`` would let a page cover the Desk (review
#: of #412). An image loads only from this site's files (``_IMAGE_SOURCES``),
#: so a page cannot track its readers or send a request as them.
_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "div", "dl", "dt", "em",
    "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "kbd", "li", "ol", "p", "pre",
    "s", "span", "strong", "sub", "sup", "table", "tbody", "td", "th", "thead", "tr", "ul",
}
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_ATTRIBUTES = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
    "td": {"align"},
    "th": {"align"},
    **{h: {"id"} for h in _HEADINGS},
}
_URL_SCHEMES = {"http", "https", "mailto"}
_IMAGE_SOURCES = ("/files/", "/private/files/")


def _attribute(tag, attribute, value):
    """nh3's attribute_filter: an image source outside this site's files is
    dropped (the image keeps its alt text)."""
    if tag == "img" and attribute == "src" and not value.startswith(_IMAGE_SOURCES):
        return None
    return value


def sanitize(html):
    """``html`` with everything outside the allowlist removed (nh3, the
    sanitiser Frappe v16's own sanitize_html uses, with a narrower list).
    Links keep http, https, mailto and relative URLs and open with
    rel="noopener noreferrer"; images load from /files/ or /private/files/."""
    return nh3.clean(html or "", tags=_TAGS, attributes=_ATTRIBUTES,
                     url_schemes=_URL_SCHEMES, strip_comments=True,
                     link_rel="noopener noreferrer", attribute_filter=_attribute)


# --- the handbook ---------------------------------------------------------------------

def _rendered(markdown, render):
    html = render(markdown)
    return None if html is None else sanitize(str(html))


def assemble(product, site, roles, audience, render, can_edit=False):
    """What get_handbook returns.

    ``product``: ``load_product_pages``. ``site``: Konsol Guide Page rows
    (SITE_FIELDS) the caller may read; an unpublished row is dropped here
    too, whatever the caller passed. ``render``: markdown to HTML (Frappe's
    md_to_html, which returns None when it cannot render). Every page's HTML
    is sanitised. A site page with an undeclared audience, or one that does
    not render, is left out and named in ``problems``; a product page that
    does not render raises HandbookError (it ships with the app).
    ``can_edit``: the caller may write site pages; each site page then
    carries its Desk route.
    """
    problems = []
    pages = []
    for p in product:
        html = _rendered(p["markdown"], render)
        if html is None:
            raise HandbookError("Handbook page %s.md could not be rendered" % p["slug"])
        pages.append({"id": "konsol:%s" % p["slug"], "origin": KONSOL, "title": p["title"],
                      "audience": p["audience"], "section": p["section"], "html": html})

    rows = sorted((r for r in site if r.get("published")),
                  key=lambda r: (int(r.get("sort_order") or 0), r.get("title") or ""))
    for r in rows:
        if r.get("audience") not in AUDIENCES:
            problems.append("Site page %s (%s) has audience %r, which is not one of %s; it is "
                            "not shown." % (r["name"], r.get("title"), r.get("audience"),
                                            ", ".join(AUDIENCES)))
            continue
        html = _rendered(r.get("body") or "", render)
        if html is None:
            problems.append("Site page %s (%s) could not be rendered; it is not shown."
                            % (r["name"], r.get("title")))
            continue
        page = {"id": "site:%s" % r["name"], "origin": THIS_SITE, "title": r.get("title"),
                "audience": r["audience"], "section": r.get("section"), "html": html}
        if can_edit:
            page["edit_route"] = "/desk/konsol-guide-page/%s" % r["name"]
        pages.append(page)

    mine = my_audiences(roles)
    tiles = [{"id": p["id"], "title": p["title"], "audience": p["audience"],
              "mine": p["audience"] in mine}
             for p in pages if p["origin"] == KONSOL and p["section"] == START_HERE
             and p["audience"] != "Everyone"]
    tiles.sort(key=lambda t: AUDIENCES.index(t["audience"]))

    return {"audiences": list(AUDIENCES), "audience": audience, "my_audiences": mine,
            "start_here": tiles, "pages": for_audience(pages, audience),
            "problems": problems, "can_edit": bool(can_edit)}
