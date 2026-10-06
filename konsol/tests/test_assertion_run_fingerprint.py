"""konsol#338 (#338-1, Deepak Pai 6 Oct 2026): a close run carries the
fingerprint of the numbers it checked.

- Assertion Run has ``numbers_fingerprint``, ``fingerprint_as_of`` (the latest
  completed build when the run started) and ``fingerprint_error`` (why there
  is no fingerprint), all read-only and fixed at insert like the run's scope.
- ``trigger_close_run`` stamps them (``fingerprint.stamp_new_run``) before the
  insert.
- ``sign_off_close`` refuses a run with no fingerprint, naming why: no signed
  run is left without one (#338 decision comment), so a later build can
  always tell whether the signed numbers changed.
- The ``signed_off`` Close Event records the fingerprint signed.

``sign_off_close`` runs against test_assertion_warn_amber.py's stub frappe
(its ``_load``, loaded by path).
"""
import ast
import importlib.util
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AR_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run")
AR_PY = os.path.join(AR_DIR, "assertion_run.py")
AR_JSON = os.path.join(AR_DIR, "assertion_run.json")
FINGERPRINT_FIELDS = ("numbers_fingerprint", "fingerprint_as_of", "fingerprint_error")

_spec = importlib.util.spec_from_file_location(
    "warn_amber_for_338", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "test_assertion_warn_amber.py"))
_amber = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_amber)


def _tree():
    with open(AR_PY) as f:
        src = f.read()
    return src, ast.parse(src)


def test_the_run_has_the_three_read_only_fingerprint_fields():
    fields = {f["fieldname"]: f for f in json.load(open(AR_JSON))["fields"]}
    expected = {"numbers_fingerprint": "Data", "fingerprint_as_of": "Datetime",
                "fingerprint_error": "Small Text"}
    for name, fieldtype in expected.items():
        assert name in fields, name
        assert fields[name]["fieldtype"] == fieldtype, (name, fields[name]["fieldtype"])
        assert fields[name].get("read_only") == 1, name
        assert fields[name].get("label") and fields[name].get("description"), name


def test_the_fingerprint_is_fixed_at_insert_like_the_scope():
    _, tree = _tree()
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "SCOPE_FIELDS" for t in n.targets))
    scope = ast.literal_eval(node.value)
    for name in FINGERPRINT_FIELDS:
        assert name in scope, name


def test_trigger_close_run_stamps_the_run_before_inserting_it():
    src, tree = _tree()
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "trigger_close_run")
    body = ast.get_source_segment(src, fn)
    assert "fingerprint.stamp_new_run(doc)" in body
    assert body.index("fingerprint.stamp_new_run(doc)") < body.index("doc.insert(")


def _refused(doc_fields):
    module, frappe, doc, _ = _amber._load(status="Green", warned=0)
    doc.__dict__.update(doc_fields)
    try:
        module.sign_off_close("AR-1")
    except frappe.ValidationError as exc:
        return str(exc), frappe, doc
    raise AssertionError("a run with no fingerprint was signed")


def test_a_run_with_no_fingerprint_cannot_be_signed_and_says_why():
    message, frappe, doc = _refused({"numbers_fingerprint": None,
                                     "fingerprint_error": "ClickHouse said UNKNOWN_TABLE"})
    assert "UNKNOWN_TABLE" in message and "Run the checks again" in message, message
    assert doc.signoff_saved is False and frappe.events == []


def test_a_run_from_before_fingerprints_cannot_be_signed_either():
    message, frappe, doc = _refused({"numbers_fingerprint": None, "fingerprint_error": None})
    assert "no fingerprint" in message and "Run the checks again" in message, message
    assert doc.signoff_saved is False and frappe.events == []


def test_the_signed_off_event_records_the_fingerprint_signed():
    module, frappe, doc, _ = _amber._load(status="Green", warned=0)
    doc.numbers_fingerprint = "v1:" + "a" * 64
    module.sign_off_close("AR-1")
    (event,) = [e for e in frappe.events if isinstance(e, dict)]
    assert event["detail"]["numbers_fingerprint"] == "v1:" + "a" * 64
