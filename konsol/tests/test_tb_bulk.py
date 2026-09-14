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
UPLOAD_JSON = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_upload", "trial_balance_upload.json")

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
