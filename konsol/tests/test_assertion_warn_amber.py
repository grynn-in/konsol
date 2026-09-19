"""konsol#265 — a dbt warn must reach the operator as a warning, not an error.

Decision (Deepak Pai, 19 Sep 2026): option C. A run with warnings and no
failures is **Amber**, and signing it off requires a typed acknowledgement —
the close record carries what was outstanding and why it was signed anyway.

The mapping lives in `konsol.assertion_status`, which imports no frappe (the
`konsol.build_command` idiom from konsol#195), so it is unit-testable on the
host rather than only assertable as source text.
"""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AR_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run")
AR_PY = os.path.join(AR_DIR, "assertion_run.py")
AR_JSON = os.path.join(AR_DIR, "assertion_run.json")
STEP_JSON = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_step",
                         "assertion_step.json")
RUN_STEP_JSON = os.path.join(APP_DIR, "pipeline", "doctype", "run_step", "run_step.json")
RPT_PY = os.path.join(APP_DIR, "consolidation", "report", "close_assertions",
                      "close_assertions.py")
TASKS_PY = os.path.join(APP_DIR, "tasks.py")
CONTROL_PY = os.path.join(APP_DIR, "control_api.py")


def _src(path):
    with open(path) as fh:
        return fh.read()


def _fields(path):
    return {f["fieldname"]: f for f in json.load(open(path))["fields"]}


def _segment(src, start, end=None):
    i = src.index(start)
    return src[i:src.index(end)] if end else src[i:]


# --- the mapping itself ----------------------------------------------------

def test_warn_maps_to_warn_not_error():
    """The whole defect: `warn` fell through the map's default to `Error`."""
    from konsol.assertion_status import step_status
    assert step_status("warn") == "Warn"
    assert step_status("pass") == "Pass"
    assert step_status("fail") == "Fail"
    assert step_status("error") == "Error"


def test_unknown_dbt_status_still_errors():
    """An unrecognised status must not be silently treated as benign."""
    from konsol.assertion_status import step_status
    assert step_status("teapot") == "Error"
    assert step_status("") == "Error"
    assert step_status(None) == "Error"


def test_rows_are_captured_for_warn_as_well_as_fail():
    """--store-failures wrote the offending rows; a warn must show them too."""
    from konsol.assertion_status import captures_rows
    assert captures_rows("Fail") is True
    assert captures_rows("Warn") is True
    assert captures_rows("Pass") is False
    assert captures_rows("Error") is False


def test_run_status_is_amber_when_only_warnings():
    from konsol.assertion_status import run_status
    assert run_status(passed=10, failed=0, errored=0, warned=0) == "Green"
    assert run_status(passed=9, failed=0, errored=0, warned=1) == "Amber"


def test_a_failure_outranks_a_warning():
    """Amber must never mask a real failure."""
    from konsol.assertion_status import run_status
    assert run_status(passed=1, failed=1, errored=0, warned=5) == "Red"
    assert run_status(passed=1, failed=0, errored=1, warned=5) == "Red"


def test_empty_run_is_red_not_green():
    """Pre-existing rule: a run that asserted nothing is not a pass."""
    from konsol.assertion_status import run_status
    assert run_status(passed=0, failed=0, errored=0, warned=0) == "Red"


def test_severity_comes_from_the_manifest_not_a_literal():
    """`severity` was hardcoded "error" on every step, so the field designed to
    carry the distinction never carried it."""
    from konsol.assertion_status import severity_of
    manifest = {"test.p.assert_x.h": {"config": {"severity": "warn"}},
                "test.p.assert_y.h": {"config": {"severity": "error"}}}
    assert severity_of("test.p.assert_x.h", manifest, "Pass") == "warn"
    assert severity_of("test.p.assert_y.h", manifest, "Pass") == "error"


def test_severity_falls_back_to_the_observed_status():
    """No manifest (unreadable/absent) must not mislabel a warn as an error:
    a `Warn` status can only come from a warn-severity test."""
    from konsol.assertion_status import severity_of
    assert severity_of("test.p.assert_x.h", {}, "Warn") == "warn"
    assert severity_of("test.p.assert_x.h", {}, "Fail") == "error"
    assert severity_of("test.p.assert_x.h", None, "Warn") == "warn"


def test_module_imports_no_frappe():
    """Same contract as konsol.build_command — keeps it host-testable."""
    import konsol.assertion_status as m
    tree = ast.parse(_src(m.__file__))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith("frappe") for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("frappe")


# --- the doctypes ----------------------------------------------------------

def test_assertion_step_can_hold_a_warn():
    opts = _fields(STEP_JSON)["status"]["options"]
    assert "Warn" in opts.split("\n")


def test_assertion_run_can_be_amber_and_counts_warnings():
    fields = _fields(AR_JSON)
    assert "Amber" in fields["status"]["options"].split("\n")
    assert "warned" in fields, "no counter for warnings"
    assert fields["warned"]["fieldtype"] == "Int"
    assert fields["warned"]["read_only"] == 1


def test_signoff_records_the_acknowledgement_and_what_was_outstanding():
    fields = _fields(AR_JSON)
    assert "Acknowledged" in fields["signoff_status"]["options"].split("\n")
    assert "acknowledgement" in fields, "nowhere to store why they signed anyway"
    assert fields["acknowledgement"]["read_only"] == 1
    assert "warnings_at_signoff" in fields, "nowhere to store what was outstanding"
    assert fields["warnings_at_signoff"]["read_only"] == 1


def test_run_step_can_show_a_warning():
    """tasks.py left a warned test's Run Step at Pending forever."""
    opts = _fields(RUN_STEP_JSON)["status"]["options"]
    assert "Warning" in opts.split("\n")


# --- the consumers ---------------------------------------------------------

def test_parse_results_uses_the_shared_mapping():
    src = _src(AR_PY)
    assert "from konsol.assertion_status import" in src
    seg = _segment(src, "def _parse_results")
    assert '"severity": "error"' not in seg, "severity is still hardcoded"
    assert "warned" in seg, "warnings are not counted separately"


def test_amber_signoff_requires_an_acknowledgement_but_not_a_role():
    """C, not B: the acknowledgement is typed. And Amber is softer than Red —
    it must not demand the EPM Admin override role."""
    seg = _segment(_src(AR_PY), "def sign_off_close", "def assert_close_signed_off")
    assert "Amber" in seg
    assert "Acknowledged" in seg
    amber = seg.index("Amber")
    role = seg.index("OVERRIDE_ROLES & set(frappe.get_roles())")
    assert amber < role, "Amber must be handled before the Red override gate"


def test_acknowledged_counts_as_signed_off():
    src = _src(AR_PY)
    assert '"Acknowledged"' in _segment(src, "SIGNED_STATES", "def latest_close_run")


def test_amber_is_a_terminal_status():
    """Otherwise latest_close_run can never see an Amber run."""
    src = _src(AR_PY)
    assert "Amber" in _segment(src, "TERMINAL_STATUSES", "SIGNED_STATES")


def test_pipeline_status_map_handles_warn():
    seg = _segment(_src(TASKS_PY), "status_map = {", "doc.set(\"steps\", [])")
    assert '"warn"' in seg


def test_warned_step_counts_as_done():
    """`done` drives progress_pct; a warned step must not understate it."""
    seg = _segment(_src(TASKS_PY), "st = status_map.get", "doc.progress_pct")
    assert "Warning" in seg


def test_report_surfaces_the_warning_count():
    src = _src(RPT_PY)
    assert '"warned"' in src
    assert "Warn" in _segment(src, "STATUS_ORDER")


def test_amber_close_still_counts_as_completed_today():
    """A close signed over warnings finished; it must not vanish from the
    operator's 'done today' count."""
    seg = _segment(_src(CONTROL_PY), "def _completed_today")
    assert "Amber" in seg
