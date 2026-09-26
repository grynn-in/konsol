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
import tempfile

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


def _src(path):
    with open(path) as fh:
        return fh.read()


def _fields(path):
    return {f["fieldname"]: f for f in json.load(open(path))["fields"]}


def _segment(src, start, end):
    """The slice between two markers.

    The end marker is searched for AFTER the start: an earlier occurrence would
    slice the segment backwards into an empty string and every assertion on it
    would pass vacuously. For the same reason `end` is required — a segment
    running to EOF swallows later functions and stops proving anything about
    the one named.
    """
    i = src.index(start)
    j = src.index(end, i + len(start))
    assert j > i
    return src[i:j]


# --- loading assertion_run.py against a stub frappe -------------------------
# The mapping in konsol.assertion_status is frappe-free and unit-tested above.
# The wiring is not, and source-text assertions cannot catch a branch that is
# present but wrong, so the functions that decide a close are exercised here.

def _load(status="Amber", warned=2, warning_names=None, roles=(), manifest=None):
    import importlib.util
    import sys
    import types

    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    saved_doc = types.SimpleNamespace(
        name="AR-1", status=status, warned=warned, signoff_status="Not Signed Off",
        signoff_saved=False, acknowledgement=None, warnings_at_signoff=None,
        override_reason=None, signed_off_by=None, signed_off_at=None,
        fiscal_year=2099, fiscal_period=1,
        # A66: sign_off_close always hands the run's start to the data-change rule.
        started_at=None,
    )
    saved_doc.save = lambda **k: setattr(saved_doc, "signoff_saved", True)

    frappe.throw = throw
    frappe._ = lambda s: types.SimpleNamespace(format=lambda *a: s.format(*a))
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.get_roles = lambda: list(roles)
    frappe.has_permission = lambda *a, **k: True
    frappe.session = types.SimpleNamespace(user="acct@example.com")
    frappe.utils = types.SimpleNamespace(get_bench_path=lambda: "/bench",
                                         now_datetime=lambda: "NOW")
    frappe.db = types.SimpleNamespace(
        get_value=lambda *a, **k: "AR-1", commit=lambda: None, exists=lambda *a, **k: True)
    frappe.get_doc = lambda dt, name=None: saved_doc
    frappe.get_all = lambda dt, **k: list(warning_names or [])
    frappe.publish_realtime = lambda *a, **k: None

    class Document:
        def __init__(self, **f):
            self.__dict__.update(f)
            self._rows = []

        def __getattr__(self, n):
            if n.startswith("__"):
                raise AttributeError(n)
            return None

        def set(self, field, value):
            self._rows = list(value)

        def append(self, field, row):
            self._rows.append(row)

    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = Document

    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    period_status.assert_declared = lambda *a: None
    period_status.OPEN = "Open"
    # A59: trigger_close_run and sign_off_close read the period's status; open here.
    period_status.period_row = lambda fy, fp: {"code": "P%02d" % int(fp), "status": "Open"}

    as_spec = importlib.util.spec_from_file_location(
        "konsol.assertion_status", os.path.join(APP_DIR, "assertion_status.py"))
    assertion_status = importlib.util.module_from_spec(as_spec)
    as_spec.loader.exec_module(assertion_status)

    mods = {"frappe": frappe, "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": doc_mod, "konsol": types.ModuleType("konsol"),
            "konsol.period_status": period_status,
            "konsol.assertion_status": assertion_status}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("assertion_run_under_test", AR_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            sys.modules[n] = old if old is not None else sys.modules.pop(n, None)

    # sign_off_close imports the period gate lazily (konsol#305 A22). The
    # gate is tested in test_close_signoff_wiring.py and
    # test_close_signoff_gate.py; here it is a no-op, installed only for the
    # duration of each call.
    gate = types.ModuleType("konsol.close.signoff_gate")
    gate.assert_can_sign = lambda *a: None
    # A63: no data change recorded, so the run is current.
    gate.data_change = lambda *a: {"data_changed_at": None, "data_changed_by": None,
                                   "data_change": None}
    # A66: sign_off_close decides the data-change refusal through the real,
    # pure signoff_model (loaded by path).
    sm_spec = importlib.util.spec_from_file_location(
        "signoff_model_for_warn_amber", os.path.join(APP_DIR, "close", "signoff_model.py"))
    signoff_model = importlib.util.module_from_spec(sm_spec)
    sm_spec.loader.exec_module(signoff_model)
    close_pkg = types.ModuleType("konsol.close")
    close_pkg.signoff_gate = gate
    close_pkg.signoff_model = signoff_model
    sign_off_close = module.sign_off_close

    def sign_off_with_a_clear_gate(*a, **k):
        names = ("konsol.close", "konsol.close.signoff_gate", "konsol.close.signoff_model")
        before = {n: sys.modules.get(n) for n in names}
        sys.modules.update({"konsol.close": close_pkg, "konsol.close.signoff_gate": gate,
                            "konsol.close.signoff_model": signoff_model})
        try:
            return sign_off_close(*a, **k)
        finally:
            for n, old in before.items():
                if old is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = old

    module.sign_off_close = sign_off_with_a_clear_gate
    return module, frappe, saved_doc, Document


def _results_doc(module, Document, nodes, tmpdir):
    """Run _parse_results over a fabricated target/run_results.json."""
    target = os.path.join(tmpdir, "target")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "run_results.json"), "w") as fh:
        json.dump({"results": nodes}, fh)
    doc = Document()
    module._parse_results(doc, tmpdir)
    assert doc._rows and doc._rows[0].get("assertion") != "run_results.json", \
        "the fixture was not read; the test would be asserting on the error row"
    return doc


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
    """With no manifest, read the observed status rather than the old literal
    "error". It is a best guess, not an invariant — a test with `error_if`
    reports `warn` below its threshold — which is why the manifest wins above."""
    from konsol.assertion_status import severity_of
    assert severity_of("test.p.assert_x.h", {}, "Warn") == "warn"
    assert severity_of("test.p.assert_x.h", {}, "Fail") == "error"
    assert severity_of("test.p.assert_x.h", None, "Warn") == "warn"


def test_a_run_that_only_warned_is_amber():
    """The edge the `(passed + warned) == 0` guard decides: nothing passed,
    nothing failed, everything warned."""
    from konsol.assertion_status import run_status
    assert run_status(passed=0, failed=0, errored=0, warned=3) == "Amber"


def test_run_status_is_read_only_so_it_cannot_be_edited_into_amber():
    """Every other field on the sign-off path is read_only; `status` was not,
    so a Red run could be edited to Amber in Desk and signed with a note."""
    assert _fields(AR_JSON)["status"].get("read_only") == 1


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

def test_parse_results_counts_a_warn_and_turns_the_run_amber():
    module, _, _, Document = _load()
    with tempfile.TemporaryDirectory() as tmp:
        doc = _results_doc(module, Document, [
            {"unique_id": "test.p.assert_ok.h", "status": "pass"},
            {"unique_id": "test.p.assert_tb.h", "status": "warn", "failures": 3},
        ], tmp)
    assert (doc.passed, doc.failed, doc.errored, doc.warned) == (1, 0, 0, 1)
    assert doc.total == 2
    assert doc.status == "Amber"


def test_parse_results_ignores_dbt_operations():
    """A real close on 21 Sep 2026 came back Red with "2 errored" and nothing
    failed. The two were dbt's own on-run-start hooks, which appear in
    run_results.json as `operation.…` with status 'success' — a status the map
    does not know, so they fell through the default to Error, exactly the
    defect konsol#265 fixed for `warn`. The run selects test_type:singular;
    operations were never meant to be counted."""
    module, _, _, Document = _load()
    with tempfile.TemporaryDirectory() as tmp:
        doc = _results_doc(module, Document, [
            {"unique_id": "operation.open_epm.open_epm-on-run-start-0",
             "status": "success", "message": "open_epm.on-run-start.0 passed"},
            {"unique_id": "operation.open_epm.open_epm-on-run-start-1",
             "status": "success", "message": "open_epm.on-run-start.1 passed"},
            {"unique_id": "test.p.assert_ok.h", "status": "pass"},
            {"unique_id": "test.p.assert_tb.h", "status": "warn", "failures": 3},
        ], tmp)
    assert doc.total == 2, f"operations were counted: total={doc.total}"
    assert doc.errored == 0, f"a passing hook was counted as an error: {doc.errored}"
    assert (doc.passed, doc.warned) == (1, 1)
    assert doc.status == "Amber", "the close is Red because two hooks passed"
    assert all(r["assertion"].startswith("assert_") for r in doc._rows), \
        [r["assertion"] for r in doc._rows]


def test_an_unknown_status_on_a_real_test_still_errors():
    """Skipping operations must not weaken the rule that an unrecognised status
    on an actual assertion is an error."""
    module, _, _, Document = _load()
    with tempfile.TemporaryDirectory() as tmp:
        doc = _results_doc(module, Document, [
            {"unique_id": "test.p.assert_x.h", "status": "teapot"},
        ], tmp)
    assert (doc.total, doc.errored) == (1, 1)
    assert doc.status == "Red"


def test_parse_results_keeps_a_failure_red_even_with_warnings():
    module, _, _, Document = _load()
    with tempfile.TemporaryDirectory() as tmp:
        doc = _results_doc(module, Document, [
            {"unique_id": "test.p.assert_tb.h", "status": "warn"},
            {"unique_id": "test.p.assert_bad.h", "status": "fail", "failures": 1},
        ], tmp)
    assert doc.status == "Red"
    assert (doc.failed, doc.warned) == (1, 1)


def test_parse_results_captures_the_failures_table_for_a_warn():
    """The --store-failures rows were written; dropping them for warns is the
    defect that made a warning less informative than an error."""
    module, _, _, Document = _load()
    with tempfile.TemporaryDirectory() as tmp:
        doc = _results_doc(module, Document, [
            {"unique_id": "test.p.assert_tb.h", "status": "warn",
             "relation_name": "`epm_dbt_test__audit`.`assert_tb`"},
        ], tmp)
    row = doc._rows[0]
    assert row["failures_table"] == "epm_dbt_test__audit.assert_tb"


def test_parse_results_writes_the_real_severity_not_a_literal():
    module, _, _, Document = _load()
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "target"), exist_ok=True)
        with open(os.path.join(tmp, "target", "manifest.json"), "w") as fh:
            json.dump({"nodes": {"test.p.assert_tb.h": {"config": {"severity": "warn"}}}}, fh)
        doc = _results_doc(module, Document, [
            {"unique_id": "test.p.assert_tb.h", "status": "pass"},
        ], tmp)
    assert doc._rows[0]["severity"] == "warn", "a passing warn-severity test read as error"


def test_amber_signoff_is_refused_without_an_acknowledgement():
    """C, not B: the acknowledgement is typed, or there is no signature."""
    module, frappe, doc, _ = _load(status="Amber", warned=2,
                                   warning_names=["assert_tb_balances", "assert_fx_sane"])
    try:
        module.sign_off_close("AR-1")
        raise AssertionError("an Amber close signed with no acknowledgement")
    except frappe.ValidationError as e:
        assert "assert_tb_balances" in str(e), "the prompt must name the warnings"
    assert doc.signoff_saved is False


def test_amber_signoff_with_an_acknowledgement_records_both_sides():
    module, _, doc, _ = _load(status="Amber", warned=2,
                              warning_names=["assert_tb_balances", "assert_fx_sane"])
    module.sign_off_close("AR-1", acknowledgement="TB out by 0.02, immaterial")
    assert doc.signoff_status == "Acknowledged"
    assert doc.acknowledgement == "TB out by 0.02, immaterial"          # why
    assert "assert_tb_balances" in doc.warnings_at_signoff              # what
    assert doc.override_reason is None


def test_amber_signoff_does_not_need_the_override_role():
    """Amber is softer than Red. `roles=()` — no EPM Admin anywhere."""
    module, _, doc, _ = _load(status="Amber", warned=1, warning_names=["assert_x"], roles=())
    module.sign_off_close("AR-1", acknowledgement="reviewed")
    assert doc.signoff_status == "Acknowledged"


def test_red_still_needs_the_override_role():
    """The stronger gate must not have been loosened on the way past."""
    module, frappe, doc, _ = _load(status="Red", warned=0, roles=())
    try:
        module.sign_off_close("AR-1", override_reason="shipping anyway")
        raise AssertionError("a Red close was signed without the override role")
    except frappe.PermissionError:
        pass
    assert doc.signoff_saved is False


def test_an_overridden_red_close_still_records_what_was_warning():
    """A Red close with warnings outstanding: the stronger gate must not end up
    with the weaker record."""
    module, _, doc, _ = _load(status="Red", warned=3,
                              warning_names=["assert_a", "assert_b", "assert_c"],
                              roles=("EPM Admin",))
    module.sign_off_close("AR-1", override_reason="known, fixing next period")
    assert doc.signoff_status == "Overridden"
    assert "assert_a" in doc.warnings_at_signoff


def test_an_acknowledgement_on_a_non_amber_close_is_refused_not_dropped():
    """Storing nothing and returning success is the silent fallback #265 is about."""
    module, frappe, doc, _ = _load(status="Green", warned=0)
    try:
        module.sign_off_close("AR-1", acknowledgement="looks fine")
        raise AssertionError("acknowledgement silently accepted on a Green close")
    except frappe.ValidationError:
        pass
    assert doc.signoff_saved is False


def test_the_warning_record_says_when_it_is_truncated():
    """`warned` is the truthful count; the name list is capped. A record that
    stopped silently at the cap would understate what was outstanding."""
    capped = 50
    module, _, doc, _ = _load(status="Amber", warned=80,
                              warning_names=[f"assert_{i}" for i in range(capped)])
    module.sign_off_close("AR-1", acknowledgement="reviewed in bulk")
    assert "30 more" in doc.warnings_at_signoff, "the record stopped silently at the cap"
    assert "80" in doc.warnings_at_signoff, "the true count is missing"
    assert module.WARNING_NAME_LIMIT == capped


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
    """`warned` in _RUN_FIELDS proves nothing on its own — the field was fetched
    and never displayed. Assert the places that render it."""
    src = _src(RPT_PY)
    assert '"warned"' in _segment(src, "_RUN_FIELDS", "def execute")
    assert "Warn" in _segment(src, "STATUS_ORDER", "def ")
    assert "run.warned" in _segment(src, "def _summary", "if run.signed_off_by")


def test_report_does_not_count_a_warning_as_a_failure():
    seg = _segment(_src(RPT_PY), "def _chart", "def _summary")
    assert '"Warn"' in seg, "a warned row still lands in the Failed/Errored bar"
    assert seg.count("warned[c]") >= 2


def test_report_status_tile_is_not_red_for_amber():
    seg = _segment(_src(RPT_PY), "def _summary", "if run.signed_off_by")
    assert "Amber" in seg


def _amber_branch():
    """The body of the Amber arm of add_signoff_buttons, and nothing else."""
    src = _src(os.path.join(AR_DIR, "assertion_run.js"))
    return _segment(src, 'frm.doc.status === "Amber"', "} else if (frappe.user.has_role")


def test_desk_offers_an_acknowledgement_prompt_on_amber():
    """Without this the feature is unreachable from Desk: the server throws
    'Acknowledgement required' and the form offers nowhere to type one."""
    branch = _amber_branch()
    assert "frappe.prompt" in branch
    assert "acknowledgement" in branch, "sign_off_close is never called with one"
    assert "reqd: 1" in branch, "the note can be skipped, which is option B"


def test_desk_amber_branch_is_not_role_gated():
    """Amber is softer than Red: the prompt must reach the accountant, not only
    an EPM Admin. Asserted on the branch body, not on the whole function —
    the function's first line legitimately mentions Amber and has_role."""
    assert "has_role" not in _amber_branch()


def test_desk_treats_acknowledged_as_signed():
    seg = _segment(_src(os.path.join(AR_DIR, "assertion_run.js")),
                   "const signed =", "if (frm.is_new()")
    assert "Acknowledged" in seg, "an acknowledged close still offers to sign off again"


def test_amber_run_has_a_list_view_indicator():
    """Otherwise every Amber run reads 'Unknown' in the list."""
    src = _src(os.path.join(AR_DIR, "assertion_run_list.js"))
    assert "Amber:" in src
