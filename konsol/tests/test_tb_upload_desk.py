"""Trial Balance Upload in the native Desk: Check file, then Load (5 Nov 2026).

The demo runs in the Frappe Desk, not the Close app (Deepak Pai, 5 Nov 2026).
The Desk form saved the attached file and did nothing more: the check
(``tb_bulk.check_file``) and the load (``tb_bulk.load``) were called only by
the Close app (close/tb_api.py). The form now has both buttons:

- **Check file** posts ``tb_bulk.check_upload(name, amount_basis)``, which
  checks the saved upload in place (``check_file`` creates a new one from a
  file URL, so the Desk cannot use it);
- **Load** posts ``tb_bulk.load(name, skip_invalid)``, the same endpoint the
  Close app uses, so the load job and its refusals are the product's own.

Source-text tests, in the style of test_tb_bulk.py: tb_bulk.py imports frappe.
"""
import ast
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TB_BULK_PATH = os.path.join(APP_DIR, "tb_bulk.py")
UPLOAD_JS = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_upload",
                         "trial_balance_upload.js")


def _read(path):
    with open(path) as f:
        return f.read()


def _function(name):
    src = _read(TB_BULK_PATH)
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node, ast.get_source_segment(src, node)
    raise AssertionError("tb_bulk.py has no %s" % name)


def _whitelist_methods(fn):
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call) and ast.unparse(dec.func) == "frappe.whitelist":
            for kw in dec.keywords:
                if kw.arg == "methods":
                    return [e.value for e in kw.value.elts]
            return []
    return None


def test_check_upload_checks_the_saved_upload_in_place():
    fn, text = _function("check_upload")
    assert _whitelist_methods(fn) == ["POST"]  # it writes the report onto the upload
    assert "_require_loader()" in text
    assert 'check_permission("write")' in text
    assert "_record_check(doc)" in text
    assert "_payload(doc)" in text
    # A loading or loaded upload is never re-checked over its own outcome.
    assert '"Draft", "Checked", "Failed"' in text


def test_the_form_calls_check_and_load_by_post():
    js = _read(UPLOAD_JS)
    methods = re.findall(r"""method:\s*["']([\w.]+)["']""", js)
    assert sorted(set(methods)) == ["konsol.tb_bulk.check_upload", "konsol.tb_bulk.load"], methods
    assert js.count('type: "POST"') == js.count("frappe.call(")
    for name in ("check_upload", "load"):
        fn, _ = _function(name)
        assert _whitelist_methods(fn) == ["POST"], name


def test_the_form_offers_load_only_after_a_check():
    js = _read(UPLOAD_JS)
    assert 'frm.doc.status === "Checked"' in js
    assert "skip_invalid" in js


def test_the_form_resumes_a_partly_loaded_failed_or_stalled_upload():
    """Review of #370 finding 5: tb_bulk.load resumes these; the form must offer it."""
    js = _read(UPLOAD_JS)
    assert '"Partly Loaded"' in js and '"Failed"' in js
    assert "stalled" in js
    assert "Resume load" in js
