"""konsol#371-7: text a user reads names no source file, issue number or raw JSON.

Found by the recorded walk-through on the v16 copy (5 Nov 2026):
- the Trial Balance Upload list description named ``konsol/tb_bulk.py``;
- the upload form showed its Report field as raw JSON;
- Statement Drill said "Not broken down by dimension yet (konsolidat#245)".
"""
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOAD_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_upload")
DRILL_MODEL = os.path.join(APP_DIR, "close", "drill_model.py")

#: A source path or an issue reference, as a developer writes them.
_DEV_TEXT = re.compile(r"(\b[\w/]+\.py\b|\bkonsol(?:idat)?#\d+|\bkonsol/)")


def _upload_meta():
    with open(os.path.join(UPLOAD_DIR, "trial_balance_upload.json")) as f:
        return json.load(f)


def test_the_upload_doctype_description_names_no_source_file():
    meta = _upload_meta()
    assert not _DEV_TEXT.search(meta["description"]), meta["description"]
    for field in meta["fields"]:
        for key in ("label", "description"):
            text = field.get(key) or ""
            assert not _DEV_TEXT.search(text), (field["fieldname"], key, text)


def test_the_upload_shows_its_report_rendered_not_as_json():
    fields = {f["fieldname"]: f for f in _upload_meta()["fields"]}
    assert fields["report"].get("hidden") == 1, "the raw JSON report must not be shown"
    html = fields.get("report_html")
    assert html and html["fieldtype"] == "HTML", "a rendered report field is needed"
    order = _upload_meta()["field_order"]
    assert order.index("report_html") == order.index("report") + 1
    with open(os.path.join(UPLOAD_DIR, "trial_balance_upload.js")) as f:
        js = f.read()
    assert 'get_field("report_html")' in js
    assert "konsol_tb_report_html(" in js[js.index('get_field("report_html")') - 400:]
    # loaded rows and load failures are shown too, not only the check result
    assert "r.loaded" in js and "r.load_error" in js


def test_the_statement_drill_dimension_note_names_no_issue():
    with open(DRILL_MODEL) as f:
        src = f.read()
    line = next(ln for ln in src.splitlines() if ln.startswith("DIMENSIONS_NOTE ="))
    assert not _DEV_TEXT.search(line), line
