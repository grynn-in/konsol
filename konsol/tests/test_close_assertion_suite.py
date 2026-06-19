"""Tests for the Close Assertion sign-off gate + dashboard (PRD §6.10 §4/§5).

Site-free AST/source + JSON-structure checks, matching the repo convention.
"""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CR_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "close_run")
CR_PY = os.path.join(CR_DIR, "close_run.py")
CR_JSON = os.path.join(CR_DIR, "close_run.json")
CR_JS = os.path.join(CR_DIR, "close_run.js")
RPT_DIR = os.path.join(APP_DIR, "consolidation", "report", "close_assertions")


def _funcs(path):
    with open(path) as fh:
        return {n.name for n in ast.walk(ast.parse(fh.read())) if isinstance(n, ast.FunctionDef)}


def _src(path):
    with open(path) as fh:
        return fh.read()


# --- gate: doctype fields --------------------------------------------------

def test_close_run_has_signoff_fields():
    meta = json.load(open(CR_JSON))
    fields = {f["fieldname"]: f for f in meta["fields"]}
    for fn in ("signoff_status", "signed_off_by", "signed_off_at", "override_reason"):
        assert fn in fields, f"missing {fn}"
    opts = fields["signoff_status"]["options"]
    assert "Not Signed Off" in opts and "Signed Off" in opts and "Overridden" in opts
    assert fields["signoff_status"]["read_only"] == 1  # only set via sign_off_close


# --- gate: logic -----------------------------------------------------------

def test_signoff_api_exists():
    fns = _funcs(CR_PY)
    assert {"sign_off_close", "assert_close_signed_off", "latest_close_run"} <= fns


def test_signoff_blocks_red_without_override():
    src = _src(CR_PY)
    # Green signs off; Red/Error requires a reason AND an override role
    assert 'doc.status == "Green"' in src
    assert "OVERRIDE_ROLES" in src and "frappe.get_roles()" in src
    assert "Overridden" in src
    # can't sign off an in-flight run
    assert '("Queued", "Running")' in src or "Queued" in src


def test_signoff_enforces_write_permission():
    """Green path must not be open to any user — write perm is checked before save."""
    src = _src(CR_PY)
    seg = src[src.index("def sign_off_close"):src.index("def assert_close_signed_off")]
    assert 'frappe.has_permission("Close Run", "write"' in seg
    assert "throw=True" in seg


def test_signoff_takes_row_lock():
    """Concurrent sign-offs must serialise (no double sign-off race)."""
    seg = _src(CR_PY)
    seg = seg[seg.index("def sign_off_close"):seg.index("def assert_close_signed_off")]
    assert "for_update=True" in seg


def test_signoff_checks_role_before_reason():
    """Override path checks the role first, then requires a reason."""
    seg = _src(CR_PY)
    seg = seg[seg.index("def sign_off_close"):seg.index("def assert_close_signed_off")]
    assert seg.index("OVERRIDE_ROLES & set(frappe.get_roles())") < seg.index("if not reason")


def test_latest_run_ordered_by_completion_not_creation():
    """A re-run that finishes later must win, so order by completed_at."""
    seg = _src(CR_PY)
    seg = seg[seg.index("def latest_close_run"):seg.index("def _failed_assertion_names")]
    assert "completed_at desc" in seg


def test_status_state_constants_centralised():
    src = _src(CR_PY)
    assert "TERMINAL_STATUSES" in src and "SIGNED_STATES" in src
    # report reuses them rather than re-hardcoding
    rsrc = _src(os.path.join(RPT_DIR, "close_assertions.py"))
    assert "TERMINAL_STATUSES" in rsrc and "SIGNED_STATES" in rsrc


def test_signoff_is_idempotent_guard():
    src = _src(CR_PY)
    assert "doc.signoff_status in SIGNED_STATES" in src


def test_assert_gate_hook_for_approval_chain():
    """assert_close_signed_off raises unless the period's latest run is signed off."""
    src = _src(CR_PY)
    seg = src[src.index("def assert_close_signed_off"):]
    assert "latest_close_run" in seg and "frappe.throw" in seg


# --- gate: client buttons --------------------------------------------------

def test_signoff_buttons_wired():
    src = _src(CR_JS)
    assert "sign_off_close" in src
    assert "Sign Off" in src and "Override" in src


# --- dashboard report ------------------------------------------------------

def test_close_assertions_report_registered():
    meta = json.load(open(os.path.join(RPT_DIR, "close_assertions.json")))
    assert meta["report_type"] == "Script Report"
    assert meta["ref_doctype"] == "Close Run"
    assert meta["report_name"] == "Close Assertions"


def test_close_assertions_report_executes_shape():
    fns = _funcs(os.path.join(RPT_DIR, "close_assertions.py"))
    assert "execute" in fns
    src = _src(os.path.join(RPT_DIR, "close_assertions.py"))
    # per-category board + summary + chart
    assert "_chart" in src and "_summary" in src and "Assertion Result" in src
