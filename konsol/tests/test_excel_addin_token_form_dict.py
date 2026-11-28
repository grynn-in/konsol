"""An add-in token request keeps its query args (28 Nov 2026).

Found live: every token GET to konsol.api.report_choices?kind=entities returned
500 "report_choices() missing 1 required positional argument: 'kind'"; with a
session cookie it returned 200.

Frappe builds form_dict (frappe/app.py make_form_dict) BEFORE the before_request
hooks run, and frappe.set_user() resets frappe.local.form_dict = _dict()
(frappe/__init__.py, 16.55.2). apply_excel_token_auth calls set_user, so the
query args were wiped. The hook must put form_dict back.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(form_dict):
    fr = types.ModuleType("frappe")
    fr.local = types.SimpleNamespace(form_dict=dict(form_dict))
    fr.session = types.SimpleNamespace(user="Guest")
    fr.get_request_header = lambda name: "tok"

    class Cache:
        def get_value(self, key):
            return {"user": "a@example.com"}

    fr.cache = lambda: Cache()

    def set_user(user):  # like frappe.set_user: resets form_dict
        fr.session.user = user
        fr.local.form_dict = {}

    fr.set_user = set_user
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = fr
    try:
        spec = importlib.util.spec_from_file_location(
            "excel_addin_auth_under_test", os.path.join(APP_DIR, "excel_addin_auth.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.apply_excel_token_auth()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return fr


def test_token_request_keeps_its_query_args():
    fr = _run({"kind": "entities"})
    assert fr.session.user == "a@example.com"
    assert fr.local.form_dict == {"kind": "entities"}


def test_token_request_with_no_args_stays_empty():
    assert _run({}).local.form_dict == {}
