"""konsol#269 phase 2b-1: the Cost Allocation Driver form keeps Source and its
source-specific fields consistent.

The server refuses a Fixed Weights table, an Account Set table or a Value Sign
filled for a source that does not read them, while the form hides those fields
with depends_on. Without a form script a user who switches Source after filling
rows is refused about fields they cannot see. The script asks before it clears
them and restores the previous source when the user declines; the Values tab
shows only for the two sources that have values on the form.

Reads the JS and JSON as text, like test_budget_upload_js.py.
"""
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER_DIR = os.path.join(APP_DIR, "epm", "doctype", "cost_allocation_driver")
JS_PATH = os.path.join(DRIVER_DIR, "cost_allocation_driver.js")
JSON_PATH = os.path.join(DRIVER_DIR, "cost_allocation_driver.json")

HIDDEN_FIELDS = ("fixed_weights", "accounts", "value_sign")


def _js():
    with open(JS_PATH) as f:
        return f.read()


def _source_handler():
    """The body of the `source(frm)` handler, up to the next top-level handler."""
    content = _js()
    m = re.search(r"^\tsource\(frm\)\s*\{(.*?)^\t\},?\s*$", content, re.S | re.M)
    assert m, "no `source(frm) {` handler in the form script"
    return m.group(1)


def test_js_file_exists():
    assert os.path.exists(JS_PATH)


def test_js_binds_cost_allocation_driver_form():
    assert 'frappe.ui.form.on("Cost Allocation Driver"' in _js()


def test_js_has_a_source_change_handler():
    assert _source_handler()


def test_source_handler_names_every_field_the_server_refuses_for_another_source():
    content = _js()
    for fieldname in HIDDEN_FIELDS:
        assert re.search(r'[."]%s\b' % fieldname, content), fieldname
    # the handler decides from the loss list and clears through one helper
    body = _source_handler()
    assert "konsol_ca_driver_losses(frm, next)" in body
    assert "konsol_ca_driver_clear_hidden(frm, next)" in body


def test_source_handler_asks_before_it_clears():
    body = _source_handler()
    assert "frappe.confirm(" in body
    # the question names what is lost and asks to continue
    assert "Continue?" in _js()
    assert "removes the" in _js()


def test_source_handler_clears_tables_and_value_sign_on_yes():
    content = _js()
    assert re.search(r'frm\.clear_table\("fixed_weights"\)', content)
    assert re.search(r'frm\.clear_table\("accounts"\)', content)
    assert re.search(r'frm\.refresh_field\("fixed_weights"\)', content)
    assert re.search(r'frm\.refresh_field\("accounts"\)', content)
    assert re.search(r'set_value\("value_sign",\s*null\)', content)


def test_source_handler_restores_the_previous_source_on_no():
    content = _js()
    assert "frm.__ca_prev_source" in content
    # the previous source is remembered when the form loads and after a save
    for handler in ("onload", "refresh"):
        m = re.search(r"^\t%s\(frm\)\s*\{(.*?)^\t\}" % handler, content, re.S | re.M)
        assert m, handler
        assert "__ca_prev_source" in m.group(1), handler
    # the no-branch writes the previous source back
    assert re.search(r'set_value\("source",\s*frm\.__ca_prev_source\)', content)


def test_source_handler_does_not_ask_when_nothing_is_lost():
    """Switching with empty tables and no value sign asks nothing: a confirm
    with nothing to lose is noise. The handler must test for data first."""
    body = _source_handler()
    assert re.search(r"if \(!losses\.length\)", body)
    content = _js()
    assert re.search(r"\(frm\.doc\.fixed_weights \|\| \[\]\)\.length", content)
    assert re.search(r"\(frm\.doc\.accounts \|\| \[\]\)\.length", content)
    assert "frm.doc.value_sign" in content


def test_js_uses_no_browser_dialogs():
    content = _js()
    assert not re.search(r"(?<![\w.])(alert|confirm|prompt)\(", content)


def test_values_tab_shows_only_for_sources_with_values_on_the_form():
    with open(JSON_PATH) as f:
        fields = json.load(f)["fields"]
    tab = next(f for f in fields if f["fieldtype"] == "Tab Break" and f["label"] == "Values")
    assert tab.get("depends_on") == "eval:doc.source=='Fixed Weights' || doc.source=='Account Set'"
