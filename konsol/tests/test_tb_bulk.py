"""Bulk trial balance upload (konsol/tb_bulk.py, konsolidat#199): every
submission it creates declares its amount basis, resolved per entity-period
from the file's own column or the upload's Amount Basis, never guessed.

tb_bulk.py imports frappe, so these are source-text tests: the function text
is sliced and asserted. The behaviour of `_check` is exercised with stubs in
test_tb_bulk_model.py.
"""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TB_BULK_PATH = os.path.join(APP_DIR, "tb_bulk.py")
UPLOAD_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_upload")
UPLOAD_JSON = os.path.join(UPLOAD_DIR, "trial_balance_upload.json")
UPLOAD_JS = os.path.join(UPLOAD_DIR, "trial_balance_upload.js")

BASES = ("Period movement", "Year-to-date movement", "Period-end balance")


def _source():
    with open(TB_BULK_PATH) as f:
        return f.read()


def _function_text(name):
    src = _source()
    tree = ast.parse(src, TB_BULK_PATH)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(f"{name} not found in tb_bulk.py")


def test_each_created_submission_declares_its_amount_basis():
    text = _function_text("_load_one")
    assert '"amount_basis"' in text, "the submission dict must carry amount_basis"
    assert "basis" in text.split("def _load_one", 1)[1].split(")", 1)[0], "_load_one takes the basis"


def test_the_check_resolves_the_basis_per_entity_period_and_refuses_none():
    text = _function_text("_check")
    assert "resolve_basis(" in text
    assert "group_basis(" in text
    assert 'item["amount_basis"]' in text
    # the problem lands on that group's report row, not on the whole file
    assert 'item["errors"]' in text


def test_the_load_job_resolves_the_basis_before_creating_each_submission():
    text = _function_text("run_load")
    assert "resolve_basis(" in text
    assert "_load_one(" in text and "basis" in text.split("_load_one(", 1)[1].split(")", 1)[0]


def test_the_check_reads_the_uploads_basis():
    assert "doc.amount_basis" in _function_text("_record_check")
    assert "amount_basis" in _function_text("check_file")
    assert "amount_basis" in _function_text("load")


def test_the_upload_doctype_has_the_amount_basis_field():
    with open(UPLOAD_JSON) as f:
        doctype = json.load(f)
    fields = {f["fieldname"]: f for f in doctype["fields"]}
    field = fields["amount_basis"]
    assert field["fieldtype"] == "Select"
    assert field["options"].split("\n") == list(BASES)
    assert field["label"] == "Amount Basis"
    assert not field.get("reqd"), "the file may carry the basis itself"
    assert "amount_basis column" in field["description"]
    assert "amount_basis" in doctype["field_order"]


def test_the_upload_page_learns_the_site_default_and_the_three_bases():
    # konsol-exec's upload page offers the Amount Basis before the check; it
    # takes the site default and the exact basis strings from the server so
    # the page never carries its own copy of them. (`upload_conditions` is
    # the permission-query hook and must stay a SQL string, so this is a
    # separate GET endpoint.)
    text = _function_text("upload_options")
    assert "default_amount_basis" in text, "the EPM Settings default rides along"
    assert "AMOUNT_BASES" in text and "amount_bases" in text, "the three bases come from tb_basis_model"
    assert 'get_single_value("EPM Settings", "default_amount_basis")' in text
    src = _source()
    assert '@frappe.whitelist(methods=["GET"])\ndef upload_options(' in src, "a read-only endpoint"


def test_the_upload_form_prefills_the_site_default_on_new_documents_only():
    # EPM Settings' Default Amount Basis promises to pre-fill Trial Balance
    # Uploads too; the form script copies it onto NEW documents, visibly.
    assert os.path.exists(UPLOAD_JS), "trial_balance_upload.js is missing beside the doctype"
    with open(UPLOAD_JS) as f:
        text = f.read()
    assert 'frappe.ui.form.on("Trial Balance Upload"' in text
    assert "default_amount_basis" in text
    assert "frappe.db.get_single_value" in text
    assert "is_new()" in text, "existing uploads are never touched"
    assert 'set_value("amount_basis"' in text
