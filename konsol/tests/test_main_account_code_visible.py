"""Main Account shows its code (Deepak Pai, 5 Nov 2026: "the account number are
not editable? Also not shown in the tree view").

- Frappe hides a document's naming field once it is saved (form.js: autoname
  "field:<x>" and not new -> toggle_display(x, false)), so the Account Code
  vanished from the form. The code is the warehouse join key and cannot be
  renamed (its own help says so), so the form shows it again, read-only.
- The tree showed "Cash and cash equivalents (1000)" with the code muted after
  the name; accountants read the code first: "1000 · Cash and cash equivalents".
"""
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DT = os.path.join(APP_DIR, "epm", "doctype", "main_account")


def _js(name):
    with open(os.path.join(DT, name)) as f:
        return f.read()


def test_the_form_shows_the_code_read_only_once_saved():
    js = _js("main_account.js")
    assert 'toggle_display("main_account", true)' in js
    assert 'set_df_property("main_account", "read_only", 1)' in js
    # Only a saved account: a new one must still take its code (#378 review).
    guard = js[js.index("if (!frm.is_new())"):js.index('set_df_property("main_account", "read_only", 1)')]
    assert 'toggle_display("main_account", true)' in guard


def test_the_tree_labels_a_node_code_first():
    js = _js("main_account_tree.js")
    assert "get_label(node)" in js
    assert "node.label" in js and "node.title" in js
    # The label is inserted as HTML: both parts are escaped (#378 review).
    assert "esc(node.label)" in js and "esc(node.title)" in js
    assert "escape_html" in js
