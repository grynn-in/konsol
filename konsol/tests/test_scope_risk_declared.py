"""Every build scope declares its risk; an unknown scope is refused (konsol#301).

Decision of 11 October 2026, Deepak Pai, on konsol#301 (option #301-1):
``SCOPE_RISK`` declares ``reporting: low`` (it rebuilds only the
management-hierarchy rollups; no consolidated figure changes), and an unknown
build scope is refused, never defaulted to high. Rejected: #301-2 (reporting
is high) and #301-3 (keep the silent default).

Before this, ``BuildApproval.before_save`` read
``SCOPE_RISK.get(self.build_scope, "high")``: a Reporting Hierarchy publish
queued a ``reporting`` build that fell through to ``high`` with no warning.
"""
import ast
import importlib.util
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(_HERE)
BA_DIR = os.path.join(APP_DIR, "pipeline", "doctype", "build_approval")
BA_PY = os.path.join(BA_DIR, "build_approval.py")
BA_JSON = os.path.join(BA_DIR, "build_approval.json")

# The stub site of test_build_approval_request, loaded by path so this file
# reuses it rather than restating it.
_spec = importlib.util.spec_from_file_location(
    "build_approval_request_harness_301", os.path.join(_HERE, "test_build_approval_request.py"))
_harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_harness)


def _scope_risk():
    with open(BA_PY) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "SCOPE_RISK" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("SCOPE_RISK not found")


def _scope_options():
    with open(BA_JSON) as f:
        field = next(fl for fl in json.load(f)["fields"] if fl["fieldname"] == "build_scope")
    return [o for o in field["options"].split("\n") if o]


def test_every_build_scope_option_declares_its_risk():
    missing = [s for s in _scope_options() if s not in _scope_risk()]
    assert not missing, f"build scopes with no declared risk: {missing}"


def test_reporting_is_declared_low():
    assert _scope_risk()["reporting"] == "low"


def test_a_reporting_build_is_low_risk_and_approves_itself():
    site = _harness._Site()
    row = site.insert("reporting")
    assert row["risk_level"] == "low", row["risk_level"]
    assert row["workflow_state"] == "Approved", row["workflow_state"]


def test_an_unknown_scope_is_refused_not_defaulted_to_high():
    for workflow in (True, False):
        site = _harness._Site(workflow=workflow)
        try:
            site.insert("zz_no_such_scope")
        except site.frappe.ValidationError as exc:
            assert "zz_no_such_scope" in str(exc), str(exc)
        else:
            raise AssertionError(f"an unknown scope was accepted (workflow={workflow})")
        assert "ZZ-BA-0001" not in site.rows


def test_a_missing_scope_is_refused():
    site = _harness._Site()
    try:
        site.insert(None)
    except site.frappe.ValidationError as exc:
        assert "None" in str(exc), str(exc)
    else:
        raise AssertionError("a build with no scope was accepted")
    assert "ZZ-BA-0001" not in site.rows


def _scopes_callers_pass():
    """The scopes konsol's own code can put on a Build Approval: the
    build_scope fixture, and the literals its callers pass (DOCTYPE_BUILD_MAP's
    "scope", every BUILD_SCOPE constant, schema_lifecycle's publish scope)."""
    with open(os.path.join(APP_DIR, "fixtures", "build_scope.json")) as f:
        fixture = {row["name"] for row in json.load(f)}
    produced = set()
    pattern = re.compile(r'(?:"scope":\s*|BUILD_SCOPE\s*=\s*)"([a-z_]+)"')
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in ("tests", "node_modules", "__pycache__")]
        for fn in files:
            if fn.endswith(".py"):
                with open(os.path.join(root, fn)) as f:
                    produced |= set(pattern.findall(f.read()))
    return fixture, produced


def test_the_caller_scan_finds_the_scopes():
    """A guard on the scan below: if it found nothing it would check nothing."""
    fixture, produced = _scopes_callers_pass()
    assert "reporting" in fixture, fixture
    assert {"full", "chart", "consolidation", "reporting"} <= produced, produced


def test_every_scope_a_caller_passes_is_an_option_with_a_declared_risk():
    fixture, produced = _scopes_callers_pass()
    scopes = fixture | produced
    assert scopes <= set(_scope_options()), scopes - set(_scope_options())
    assert scopes <= set(_scope_risk()), scopes - set(_scope_risk())
