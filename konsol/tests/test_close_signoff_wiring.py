"""konsol#305 A22 (story 9.2, #303-3a): the sign-off gates are wired into
`sign_off_close`, and it is POST-only (Problems 10).

`sign_off_close` refuses a run with no period, then calls
`konsol.close.signoff_gate.assert_can_sign(year, period)` before any state
changes. The gate itself (order, completeness, configuration gaps) is tested in
test_close_signoff_gate.py; this file tests only that the sign-off calls it,
with the run's own period, and stops when it raises.

`assertion_run.py` is loaded against a stub frappe (the `_load` of
test_assertion_warn_amber.py:58-133, copied, not imported). The gate is
imported lazily inside `sign_off_close`, so the stub gate is installed in
`sys.modules` only for the duration of each call.
"""
import ast
import contextlib
import datetime
import importlib.util
import json
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AR_PY = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.py")
AR_JSON = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run", "assertion_run.json")


def _get_datetime(value):
    """frappe.utils.get_datetime: a datetime stays, an ISO string is parsed."""
    if isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.fromisoformat(str(value))


class GateBlocked(Exception):
    """What the stub gate raises: stands for "Sign-off blocked"."""


def _load(status="Green", signoff_status="Not Signed Off", fiscal_year=2099, fiscal_period=1,
          gate_raises=False, affected_by=None, period_state="Open",
          completed_at=datetime.datetime(2026, 9, 26, 9, 0), data_change=None,
          started_at=datetime.datetime(2026, 9, 26, 8, 55)):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, **k):
        raise (exc or frappe.ValidationError)(msg)

    saved_doc = types.SimpleNamespace(
        name="AR-1", status=status, warned=0, signoff_status=signoff_status,
        signoff_saved=False, acknowledgement=None, warnings_at_signoff=None,
        override_reason=None, signed_off_by=None, signed_off_at=None,
        fiscal_year=fiscal_year, fiscal_period=fiscal_period, affected_by=affected_by,
        completed_at=completed_at, started_at=started_at,
    )
    saved_doc.save = lambda **k: setattr(saved_doc, "signoff_saved", True)

    frappe.throw = throw
    frappe._ = lambda s: types.SimpleNamespace(format=lambda *a: s.format(*a))
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.get_roles = lambda: ["EPM Admin"]
    frappe.has_permission = lambda *a, **k: True
    frappe.session = types.SimpleNamespace(user="lead@example.com")
    frappe.utils = types.SimpleNamespace(get_bench_path=lambda: "/bench",
                                         now_datetime=lambda: "NOW",
                                         get_datetime=_get_datetime)
    frappe.db = types.SimpleNamespace(
        get_value=lambda *a, **k: "AR-1", commit=lambda: None, exists=lambda *a, **k: True)
    frappe.get_doc = lambda dt, name=None: saved_doc
    frappe.get_all = lambda dt, **k: []
    frappe.publish_realtime = lambda *a, **k: None

    class Document:
        def __init__(self, **f):
            self.__dict__.update(f)

    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = Document

    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    period_status.assert_declared = lambda *a: None
    period_status.OPEN = "Open"
    # A59: sign_off_close reads the period's effective status through period_row.
    period_status.period_row = lambda fy, fp: {
        "fiscal_year": fy, "fiscal_period": fp, "code": "P%02d" % int(fp), "type": "Regular",
        "status": period_state}

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
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old

    calls = []

    def assert_can_sign(fy, fp):
        calls.append((fy, fp))
        if gate_raises:
            raise GateBlocked("Sign-off blocked: Declare the first close period")

    gate = types.ModuleType("konsol.close.signoff_gate")
    gate.assert_can_sign = assert_can_sign
    # A63: the period's last recorded data change (blank: none recorded).
    gate.data_change = lambda fy, fp: dict(data_change or {
        "data_changed_at": None, "data_changed_by": None, "data_change": None})
    return module, frappe, saved_doc, gate, calls


@contextlib.contextmanager
def _gate_installed(gate):
    """The stub gate in sys.modules for one call; the real modules after."""
    close = types.ModuleType("konsol.close")
    close.signoff_gate = gate
    names = ("konsol.close", "konsol.close.signoff_gate")
    saved = {n: sys.modules.get(n) for n in names}
    sys.modules.update({"konsol.close": close, "konsol.close.signoff_gate": gate})
    try:
        yield
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _sign(module, gate, **kwargs):
    with _gate_installed(gate):
        return module.sign_off_close("AR-1", **kwargs)


def test_the_gate_is_called_with_the_runs_period():
    module, _, doc, gate, calls = _load(fiscal_year=2099, fiscal_period=7)
    _sign(module, gate)
    assert calls == [(2099, 7)], f"the gate was not called with the run's period: {calls}"
    assert doc.signoff_status == "Signed Off"
    assert doc.signoff_saved is True


def test_a_blocked_gate_stops_the_signoff_before_any_change():
    module, _, doc, gate, calls = _load(gate_raises=True)
    try:
        _sign(module, gate)
        raise AssertionError("a run was signed while the gate refused it")
    except GateBlocked as e:
        assert "Sign-off blocked" in str(e)
    assert calls == [(2099, 1)]
    assert doc.signoff_saved is False, "the run was saved although the gate refused it"
    assert doc.signoff_status == "Not Signed Off"
    assert doc.signed_off_by is None and doc.signed_off_at is None


def test_the_gate_runs_before_the_amber_and_red_branches():
    """A blocked period is refused whatever the run's status: the gate is not
    something an acknowledgement or an override reason gets past."""
    for status, kwargs in (("Amber", {"acknowledgement": "reviewed"}),
                           ("Red", {"override_reason": "known"}),
                           ("Error", {"override_reason": "known"})):
        module, _, doc, gate, calls = _load(status=status, gate_raises=True)
        try:
            _sign(module, gate, **kwargs)
            raise AssertionError(f"a {status} run was signed while the gate refused it")
        except GateBlocked:
            pass
        assert calls == [(2099, 1)], f"{status}: gate not called: {calls}"
        assert doc.signoff_saved is False, f"{status}: saved although the gate refused it"


def test_a_year_only_run_is_refused():
    for empty in (0, None, ""):
        module, frappe, doc, gate, calls = _load(fiscal_period=empty)
        try:
            _sign(module, gate)
            raise AssertionError(f"a run with fiscal_period={empty!r} was signed")
        except frappe.ValidationError as e:
            assert "A sign-off is for a period" in str(e), str(e)
        assert calls == [], "the gate was called for a run with no period"
        assert doc.signoff_saved is False


def test_an_already_signed_run_is_refused_before_the_gate():
    for state in ("Signed Off", "Acknowledged", "Overridden"):
        module, frappe, doc, gate, calls = _load(signoff_status=state)
        try:
            _sign(module, gate)
            raise AssertionError(f"an {state} run was signed again")
        except frappe.ValidationError as e:
            assert "already" in str(e), str(e)
        assert calls == [], f"{state}: the gate was called for an already-signed run"
        assert doc.signoff_saved is False


def test_a_queued_or_running_run_is_refused_before_the_gate():
    for status in ("Queued", "Running"):
        module, frappe, doc, gate, calls = _load(status=status)
        try:
            _sign(module, gate)
            raise AssertionError(f"a {status} run was signed")
        except frappe.ValidationError as e:
            assert "still" in str(e), str(e)
        assert calls == []
        assert doc.signoff_saved is False


def _whitelist_methods(name):
    with open(AR_PY) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "whitelist"):
            for kw in dec.keywords:
                if kw.arg == "methods":
                    return ast.literal_eval(kw.value)
    return None


def test_sign_off_close_is_post_only():
    """It writes the signature and commits, so a GET must never reach it
    (Problems 10)."""
    methods = _whitelist_methods("sign_off_close")
    assert methods == ["POST"], f'sign_off_close is not @frappe.whitelist(methods=["POST"]): {methods}'


# --- A27: "Re-sign Needed" (story 9.5, #303 point 4; Problems 9) ------------

RE_SIGN = "Re-sign Needed"
AFFECTED = "FY2099 P01 reopened by lead@example.com on 2026-09-25"


def _ar_fields():
    with open(AR_JSON) as f:
        meta = json.load(f)
    return meta["fields"]


def test_re_sign_needed_is_a_signoff_option():
    fields = {f["fieldname"]: f for f in _ar_fields()}
    options = fields["signoff_status"]["options"].split("\n")
    assert RE_SIGN in options, f"signoff_status has no {RE_SIGN!r} option: {options}"
    # The existing states stay, in their order, ahead of the new one.
    assert options[:4] == ["Not Signed Off", "Signed Off", "Acknowledged", "Overridden"], options


def test_affected_by_is_a_read_only_field_on_the_signoff_tab():
    fields = _ar_fields()
    names = [f["fieldname"] for f in fields]
    assert "affected_by" in names, "Assertion Run has no affected_by field"
    field = fields[names.index("affected_by")]
    assert field["fieldtype"] == "Small Text", field
    assert field.get("read_only") == 1, "affected_by must be read_only: it is set by the system"
    start, end = names.index("tab_signoff"), names.index("tab_timing")
    assert start < names.index("affected_by") < end, "affected_by is not on the Sign-off tab"


def test_re_sign_needed_does_not_count_as_signed():
    module, *_ = _load()
    assert RE_SIGN not in module.SIGNED_STATES, module.SIGNED_STATES
    assert set(module.SIGNED_STATES) == {"Signed Off", "Acknowledged", "Overridden"}


def test_sign_off_close_refuses_a_re_sign_needed_run_and_saves_nothing():
    for status in ("Green", "Amber", "Red"):
        module, frappe, doc, gate, calls = _load(status=status, signoff_status=RE_SIGN,
                                                 affected_by=AFFECTED)
        try:
            _sign(module, gate, acknowledgement="ok" if status == "Amber" else None,
                  override_reason="ok" if status == "Red" else None)
            raise AssertionError(f"a {status} run in {RE_SIGN} was signed again")
        except frappe.ValidationError as e:
            msg = str(e)
        # A63: the refusal names what happened (affected_by), not always a reopen.
        assert msg == ("This run's sign-off no longer counts: %s. Run the checks again, "
                       "then sign off the new run." % AFFECTED), msg
        assert doc.signoff_saved is False, f"{status}: the run was saved"
        assert doc.signoff_status == RE_SIGN
        assert doc.signed_off_by is None and doc.signed_off_at is None
        assert calls == [], f"{status}: the gate was called for a run that must be re-run"


def test_a_not_signed_run_is_still_signed():
    """Failure path: the refusal is for Re-sign Needed only."""
    module, _, doc, gate, calls = _load(signoff_status="Not Signed Off")
    _sign(module, gate)
    assert doc.signoff_status == "Signed Off" and doc.signoff_saved is True


def _run_row(signoff_status):
    row = types.SimpleNamespace(name="AR-1", status="Green", signoff_status=signoff_status,
                                failed=0, errored=0)
    row.get = lambda k: getattr(row, k)
    return row


def test_assert_close_signed_off_refuses_a_re_sign_needed_run():
    module, frappe, *_ = _load()
    frappe.get_all = lambda dt, **k: [] if "pluck" in k else [_run_row(RE_SIGN)]
    try:
        module.assert_close_signed_off(2099, 1)
        raise AssertionError("a Re-sign Needed run passed assert_close_signed_off")
    except frappe.ValidationError as e:
        assert "is not signed off" in str(e), str(e)
    # Failure path: a signed run still passes.
    frappe.get_all = lambda dt, **k: [] if "pluck" in k else [_run_row("Signed Off")]
    assert module.assert_close_signed_off(2099, 1) == "AR-1"


def test_a_closed_or_locked_period_is_refused_before_the_gate():
    """A59: signing a run on a Closed or Locked period is refused, whatever the
    run's status, before the gate runs and before anything is saved."""
    for state in ("Closed", "Locked"):
        for status, kwargs in (("Green", {}), ("Amber", {"acknowledgement": "reviewed"}),
                               ("Red", {"override_reason": "known"})):
            module, frappe, doc, gate, calls = _load(status=status, fiscal_year=2099,
                                                     fiscal_period=6, period_state=state)
            try:
                _sign(module, gate, **kwargs)
                raise AssertionError("a %s run was signed on a %s period" % (status, state))
            except frappe.ValidationError as e:
                assert "FY2099 P06 is %s; reopen it to sign off" % state in str(e), str(e)
            assert calls == [], "%s/%s: the gate ran for a closed period" % (state, status)
            assert doc.signoff_saved is False
            assert doc.signoff_status == "Not Signed Off"


# --- A63 (#305-R2b-3): a signature covers only the data its run checked ------

CHANGED_AT = datetime.datetime(2026, 9, 26, 10, 15, 30)
CHANGE = {"data_changed_at": CHANGED_AT, "data_changed_by": "zz-acct@example.com",
          "data_change": "TB TBS-ZZOP-2099-P1-905 cancelled"}
# A65: the refusal starts with a stable prefix the sign-off machine matches
# (close-ui/src/machines/signoffMachine.js DATA_CHANGED_REFUSAL).
STALE = ("Re-run the checks before signing: TB TBS-ZZOP-2099-P1-905 cancelled at "
         "2026-09-26 10:15:30 by zz-acct@example.com, after these checks started.")
NO_START = ("Re-run the checks before signing: TB TBS-ZZOP-2099-P1-905 cancelled at "
            "2026-09-26 10:15:30 by zz-acct@example.com, and this run has no start time, "
            "so it cannot show it started after that change.")
SIGNOFF_MACHINE = os.path.join(os.path.dirname(APP_DIR), "close-ui", "src", "machines",
                               "signoffMachine.js")


def _refused(module, frappe, doc, gate, calls, expected, what, **kwargs):
    try:
        _sign(module, gate, **kwargs)
        raise AssertionError(what + " was signed")
    except frappe.ValidationError as e:
        assert str(e) == expected, str(e)
    assert doc.signoff_saved is False, what
    assert doc.signoff_status == "Not Signed Off", what
    assert doc.signed_off_by is None and doc.signed_off_at is None, what
    assert doc.acknowledgement is None and doc.override_reason is None, what
    assert calls == [], "%s: the gate ran for a run that must be re-run" % what


def test_a_run_that_started_and_completed_before_the_data_changed_is_refused():
    for status, kwargs in (("Green", {}), ("Amber", {"acknowledgement": "reviewed"}),
                           ("Red", {"override_reason": "known"})):
        loaded = _load(status=status, started_at=datetime.datetime(2026, 9, 26, 8, 55),
                       completed_at=datetime.datetime(2026, 9, 26, 9, 0), data_change=CHANGE)
        _refused(*loaded, STALE, "%s: a run older than the data change" % status, **kwargs)


def test_a_run_started_before_the_change_and_completed_after_it_is_refused():
    """A65 hole 1: the change landed WHILE the run executed, so the run may
    not have seen it, although it completed after it."""
    loaded = _load(started_at=datetime.datetime(2026, 9, 26, 10, 15),
                   completed_at=datetime.datetime(2026, 9, 26, 10, 20), data_change=CHANGE)
    _refused(*loaded, STALE, "a run that was executing when the data changed")


def test_a_run_started_exactly_at_the_change_is_refused():
    loaded = _load(started_at=CHANGED_AT, completed_at=datetime.datetime(2026, 9, 26, 10, 20),
                   data_change=CHANGE)
    _refused(*loaded, STALE, "a run that started at the same instant as the change")


def test_a_run_started_after_the_data_changed_is_signed():
    module, _, doc, gate, calls = _load(
        started_at=datetime.datetime(2026, 9, 26, 10, 16),
        completed_at=datetime.datetime(2026, 9, 26, 10, 20), data_change=CHANGE)
    _sign(module, gate)
    assert doc.signoff_status == "Signed Off" and doc.signoff_saved is True
    assert calls == [(2099, 1)]


def test_a_terminal_run_with_no_start_time_is_refused_while_a_change_is_recorded():
    for blank in (None, ""):
        loaded = _load(started_at=blank, completed_at=datetime.datetime(2026, 9, 26, 10, 20),
                       data_change=CHANGE)
        _refused(*loaded, NO_START, "a run with started_at %r" % (blank,))


def test_a_run_started_as_an_iso_string_is_compared_as_a_time():
    loaded = _load(started_at="2026-09-26 09:00:00", completed_at="2026-09-26 10:20:00",
                   data_change=dict(CHANGE, data_changed_at="2026-09-26 10:15:30"))
    _refused(*loaded, STALE, "a string started_at older than the change")


def _machine_prefix():
    with open(SIGNOFF_MACHINE) as f:
        src = f.read()
    found = re.search(r'export const DATA_CHANGED_REFUSAL = "([^"]+)";', src)
    assert found, "signoffMachine.js exports no DATA_CHANGED_REFUSAL string"
    return found.group(1)


def test_the_data_change_refusal_starts_with_the_machines_prefix():
    prefix = _machine_prefix()
    module, *_ = _load()
    assert module.DATA_CHANGED_REFUSAL == prefix, (module.DATA_CHANGED_REFUSAL, prefix)
    for expected in (STALE, NO_START):
        assert expected.startswith(prefix), expected
    # The real refusals, not only the pinned text.
    for kwargs, what in (({"started_at": datetime.datetime(2026, 9, 26, 9, 0)}, "stale"),
                         ({"started_at": None}, "no start")):
        module, frappe, doc, gate, _ = _load(data_change=CHANGE, **kwargs)
        try:
            _sign(module, gate)
            raise AssertionError(what + ": signed")
        except frappe.ValidationError as e:
            assert str(e).startswith(prefix), (what, str(e))


def test_a_blank_data_changed_at_never_refuses():
    for blank in (None, ""):
        module, _, doc, gate, calls = _load(
            completed_at=datetime.datetime(2020, 1, 1),
            data_change={"data_changed_at": blank, "data_changed_by": None, "data_change": None})
        _sign(module, gate)
        assert doc.signoff_status == "Signed Off", blank
        assert doc.signoff_saved is True, blank


def test_a_re_sign_needed_refusal_shows_what_changed_the_data():
    text = "TB TBS-ZZOP-2099-P1-905 cancelled at 2026-09-26 10:15:30 by zz-acct@example.com"
    module, frappe, doc, gate, calls = _load(signoff_status=RE_SIGN, affected_by=text)
    try:
        _sign(module, gate)
        raise AssertionError("a Re-sign Needed run was signed")
    except frappe.ValidationError as e:
        msg = str(e)
    assert text in msg, msg
    assert "reopened" not in msg, "the refusal still says a period was reopened: " + msg
    assert doc.signoff_saved is False


# --- A63: the five hook points record the change -------------------------------

TBS_PY = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_submission",
                      "trial_balance_submission.py")
TBX_PY = os.path.join(APP_DIR, "consolidation", "doctype", "tb_exception", "tb_exception.py")
UPLOADER = "zz-acct@example.com"


class _Hooks:
    """Records every call the hook points make, in order, across the stub
    signoff_gate (``record``) and ClickHouse (``ch``)."""

    def __init__(self, raise_on_record=False):
        self.log = []
        self.raise_on_record = raise_on_record

    def gate(self):
        gate = types.ModuleType("konsol.close.signoff_gate")

        def record_data_change(fiscal_year, fiscal_period, text, user):
            self.log.append(("record", fiscal_year, fiscal_period, text, user))
            if self.raise_on_record:
                raise RuntimeError("record refused")
            return []

        gate.record_data_change = record_data_change
        return gate


def _hook_modules(hooks):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, *a, **k):
        raise (exc or frappe.ValidationError)(msg)

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.session = types.SimpleNamespace(user=UPLOADER)

    class Document:
        def __init__(self, **f):
            self.__dict__.update(f)

    doc_mod = types.ModuleType("frappe.model.document")
    doc_mod.Document = Document
    konsol = types.ModuleType("konsol")
    konsol.__path__ = [APP_DIR]   # the real pure konsol.tb_basis_model resolves
    clickhouse = types.ModuleType("konsol.clickhouse")
    clickhouse.execute = lambda sql, *a, **k: hooks.log.append(("ch", sql)) or ""
    clickhouse.ensure_raw_tables = lambda: None
    period_status = types.ModuleType("konsol.period_status")
    for name in ("assert_open", "assert_postable", "assert_declared"):
        setattr(period_status, name, lambda *a, **k: None)
    lifecycle = types.ModuleType("konsol.schema_lifecycle")
    lifecycle.check_epm_admin = lambda: None
    close = types.ModuleType("konsol.close")
    gate = hooks.gate()
    close.signoff_gate = gate
    return {"frappe": frappe, "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": doc_mod, "konsol": konsol,
            "konsol.clickhouse": clickhouse, "konsol.period_status": period_status,
            "konsol.schema_lifecycle": lifecycle, "konsol.close": close,
            "konsol.close.signoff_gate": gate}


@contextlib.contextmanager
def _installed(mods):
    saved = {n: sys.modules.get(n) for n in list(mods) + ["konsol.tb_basis_model"]}
    sys.modules.update(mods)
    sys.modules.pop("konsol.tb_basis_model", None)
    try:
        yield
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _hook_module(path, hooks):
    mods = _hook_modules(hooks)
    with _installed(mods):
        spec = importlib.util.spec_from_file_location("a63_hook_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module, mods


def _tbs(module, **over):
    doc = module.TrialBalanceSubmission(
        name="TBS-ZZOP-2099-P1-905", batch_id="b905", data_area_id="ZZOP", fiscal_year=2099,
        fiscal_period=1, row_count=2, amount_basis="Period movement")
    doc.__dict__.update(over)
    doc._parse_file = lambda: []
    doc._ensure_tables = lambda: None
    doc._land_rows = lambda rows: None
    return doc


def _records(hooks):
    return [e[1:] for e in hooks.log if e[0] == "record"]


def test_tb_submit_records_the_change_before_anything_reaches_clickhouse():
    hooks = _Hooks()
    module, mods = _hook_module(TBS_PY, hooks)
    with _installed(mods):
        _tbs(module).on_submit()
    assert _records(hooks) == [(2099, 1, "TB TBS-ZZOP-2099-P1-905 submitted", UPLOADER)], hooks.log
    assert hooks.log[0][0] == "record", "ClickHouse was written before the change was recorded"
    assert any(e[0] == "ch" for e in hooks.log), "the claim was not written"


def test_tb_cancel_records_the_change_before_the_claim_is_deleted():
    hooks = _Hooks()
    module, mods = _hook_module(TBS_PY, hooks)
    with _installed(mods):
        _tbs(module, fiscal_period=7).on_cancel()
    assert _records(hooks) == [(2099, 7, "TB TBS-ZZOP-2099-P1-905 cancelled", UPLOADER)], hooks.log
    assert [e[0] for e in hooks.log] == ["record", "ch"], hooks.log


def test_a_refused_record_leaves_clickhouse_untouched():
    """Failure path: ClickHouse has no transaction, so the change is recorded
    first and a refusal there stops the claim (or its delete) from happening."""
    for method in ("on_submit", "on_cancel"):
        hooks = _Hooks(raise_on_record=True)
        module, mods = _hook_module(TBS_PY, hooks)
        with _installed(mods):
            try:
                getattr(_tbs(module), method)()
                raise AssertionError("%s went on after the record was refused" % method)
            except RuntimeError as e:
                assert "record refused" in str(e)
        assert [e for e in hooks.log if e[0] == "ch"] == [], (method, hooks.log)


def _tbx(module, **over):
    doc = module.TBException(name="TBX-00905", docstatus=1, data_area_id="ZZOP",
                             fiscal_year=2099, fiscal_period=3, reason="Dormant")
    doc.__dict__.update(over)
    return doc


def test_tb_exception_submit_and_cancel_record_the_change():
    hooks = _Hooks()
    module, mods = _hook_module(TBX_PY, hooks)
    with _installed(mods):
        _tbx(module).on_submit()
        _tbx(module, docstatus=2).on_cancel()
    assert _records(hooks) == [
        (2099, 3, "TB Exception TBX-00905 submitted", UPLOADER),
        (2099, 3, "TB Exception TBX-00905 cancelled", UPLOADER)], hooks.log


def _amount_rows():
    def row(name, fp, docstatus=1):
        return types.SimpleNamespace(name=name, docstatus=docstatus, batch_id="b-" + name,
                                     data_area_id="ZZOP", fiscal_year=2099, fiscal_period=fp,
                                     row_count=2)
    return {"TBS-1": row("TBS-1", 3), "TBS-2": row("TBS-2", 4), "TBS-3": row("TBS-3", 3),
            "TBS-4": row("TBS-4", 5, docstatus=2)}


def test_set_amount_basis_records_one_change_per_period_before_the_claim():
    hooks = _Hooks()
    module, mods = _hook_module(TBS_PY, hooks)
    rows = _amount_rows()
    mods["frappe"].db = types.SimpleNamespace(
        get_value=lambda dt, name, fields, **k: rows.get(name),
        set_value=lambda dt, name, field, value, **k: hooks.log.append(("set", name)))
    with _installed(mods):
        out = module.set_amount_basis(["TBS-1", "TBS-2", "TBS-3", "TBS-4"],
                                      "Period-end balance")
    assert out["updated"] == 3, out
    assert _records(hooks) == [
        (2099, 3, "Amount basis of TB TBS-1, TBS-3 set to Period-end balance", UPLOADER),
        (2099, 4, "Amount basis of TB TBS-2 set to Period-end balance", UPLOADER)], hooks.log
    kinds = [e[0] for e in hooks.log]
    assert kinds == ["set", "set", "set", "record", "record", "ch"], kinds


def test_set_amount_basis_with_nothing_updated_records_nothing():
    hooks = _Hooks()
    module, mods = _hook_module(TBS_PY, hooks)
    rows = _amount_rows()
    mods["frappe"].db = types.SimpleNamespace(
        get_value=lambda dt, name, fields, **k: rows.get(name),
        set_value=lambda *a, **k: hooks.log.append(("set",)))
    with _installed(mods):
        out = module.set_amount_basis(["TBS-4", "TBS-9"], "Period-end balance")
    assert out["updated"] == 0
    assert hooks.log == [], hooks.log


# --- A65: the affected_by description names every cause ----------------------

def test_affected_by_description_names_reopen_and_data_change():
    field = {f["fieldname"]: f for f in _ar_fields()}["affected_by"]
    assert field.get("description") == (
        "Why this run's sign-off no longer counts: a reopen of this or an earlier period, "
        "or a data change after the run (#305-R2b-3)."), field.get("description")
