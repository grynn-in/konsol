"""U3: the Budget Upload form script posts Check and Load to the budget bulk
endpoints. Reads the JS as text, like test_pipeline_run_js.py."""
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS_PATH = os.path.join(APP_DIR, "epm", "doctype", "budget_upload", "budget_upload.js")


def _js():
    with open(JS_PATH) as f:
        return f.read()


def test_js_file_exists():
    assert os.path.exists(JS_PATH)


def test_js_binds_budget_upload_form():
    assert 'frappe.ui.form.on("Budget Upload"' in _js()


def test_js_calls_check_upload_by_post():
    content = _js()
    assert re.search(
        r'method:\s*"konsol\.budget_bulk\.check_upload",\s*type:\s*"POST"', content
    )


def test_js_calls_load_by_post():
    content = _js()
    assert re.search(
        r'method:\s*"konsol\.budget_bulk\.load",\s*type:\s*"POST"', content
    )


def test_js_reads_status_now_indicator():
    assert "status_now" in _js()


def test_js_has_no_trial_balance_endpoints():
    content = _js()
    assert "tb_bulk" not in content
    assert "Trial Balance" not in content


def test_amount_basis_is_never_prefilled():
    """Q2-c (#348): the budget basis is never defaulted. The TB form pre-fills it
    from EPM Settings; the budget form must not, so the uploader states it."""
    with open(JS_PATH) as f:
        src = f.read()
    assert "default_amount_basis" not in src
    assert 'set_value("amount_basis"' not in src
