"""Konsol Setup page look (Deepak Pai, 7 Nov 2026, picked from a sketch).

Two groups: "Needs you" (partial and not done, open) and "Done (N)" (folded).
One compact <details> row per step, closed on load; its summary is the mark,
the name, the count and an "Open →" link; the detail sentence opens beneath.
A page action toggles every row and both groups ("Expand all" / "Collapse
all"), another refreshes. A progress bar reads "N of M done".

The stylesheet is page-level CSS (konsol_setup.css beside the page, read by
Frappe's Page.load_assets): it loads with this page only and needs no hooks
entry. Frappe leaves the <style> in the document after the page is left, so
every rule is scoped to the page's root class. Colours are variables only.
"""
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE_DIR = os.path.join(APP_DIR, "consolidation", "page", "konsol_setup")


def _read(name):
    with open(os.path.join(PAGE_DIR, name)) as f:
        return f.read()


def _css_rules(css):
    """(selector, body) for each rule, comments removed."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return [(m.group(1).strip(), m.group(2)) for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css)]


def test_two_groups_needs_you_open_done_folded():
    js = _read("konsol_setup.js")
    assert '__("Needs you")' in js and '__("Done")' in js
    assert "ks-group-needs" in js and "ks-group-done" in js
    # Needs you opens on load; Done does not.
    assert re.search(r'group\("ks-group-needs".*,\s*true\)', js), "Needs you must start open"
    assert re.search(r'group\("ks-group-done".*,\s*false\)', js), "Done must start folded"
    assert '__("Setup is complete")' in js


def test_rows_are_details_closed_on_load_with_the_detail_beneath():
    js = _read("konsol_setup.js")
    assert '$("<details' in js and '$("<summary' in js
    assert "ks-step" in js and "ks-detail" in js
    # Only groups are ever opened on load; a row is never built open.
    assert ".attr(\"open\"" not in js and "open=" not in js
    assert re.search(r"\.prop\(\"open\",\s*open\)", js), "the group's open state is set from its argument"
    assert '__("Open →")' in js
    assert ".text(step.label" in js and ".text(step.detail" in js


def test_top_bar_toggle_names_what_it_will_do_and_covers_rows_and_groups():
    js = _read("konsol_setup.js")
    assert '__("Expand all")' in js and '__("Collapse all")' in js
    assert '__("Refresh")' in js
    assert "page.add_button(" in js or "page.set_secondary_action(" in js
    # One query covers rows and both groups.
    assert 'find("details")' in js
    # A row opened by hand changes what the toggle will do.
    assert '"toggle"' in js


def test_progress_bar_reads_n_of_m_done():
    js = _read("konsol_setup.js")
    assert '$("<progress' in js
    assert '__("{0} of {1} done"' in js


def test_no_markup_from_strings_and_no_inline_styles():
    js = _read("konsol_setup.js")
    assert ".html(" not in js and "innerHTML" not in js and "${" not in js
    assert "style=" not in js and ".css(" not in js


def test_stylesheet_is_page_level_scoped_and_hex_free():
    css = _read("konsol_setup.css")
    rules = _css_rules(css)
    assert rules
    for selector, body in rules:
        for part in selector.split(","):
            assert part.strip().startswith(".konsol-setup"), part
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", body), (selector, body)
        assert "rgb" not in body and "hsl" not in body, (selector, body)
        for m in re.finditer(r"border-radius:\s*([^;]+);", body):
            assert m.group(1).strip() in ("2px", "var(--radius)"), (selector, m.group(1))
    assert "max-width: 760px" in css
    assert "var(--konsol-brand-strong" in css
    js = _read("konsol_setup.js")
    assert "konsol-setup" in js and 'addClass("konsol-setup")' in js
