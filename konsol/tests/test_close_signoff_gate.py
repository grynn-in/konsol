"""Sign-off gate readers: konsol/close/signoff_gate.py (konsol#305 A17; story 9.2).

`in_scope_entities`, `sign_off_problems` and `assert_can_sign` read the site
and pass it through the pure A04/A09/A10 models. Loaded against a stub frappe
(pattern: test_assertion_warn_amber.py `_load`). The stub site applies the
filters the module sends (docstatus, status, is_group, effective_date,
"is set", "in"), so a rule enforced by the query is really exercised, not
assumed by the stub.
"""
import ast
import importlib.util
import os
import sys
import types
from datetime import date, datetime, timedelta

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE_PY = os.path.join(APP_DIR, "close", "signoff_gate.py")
PERIOD_MODEL_PY = os.path.join(APP_DIR, "close", "period_model.py")
SIGNOFF_MODEL_PY = os.path.join(APP_DIR, "close", "signoff_model.py")
CLOSE_POLICY_MODEL_PY = os.path.join(APP_DIR, "close", "close_policy_model.py")
SCOPE_MODEL_PY = os.path.join(APP_DIR, "close", "scope_model.py")
STATEMENT_MODEL_PY = os.path.join(APP_DIR, "close", "statement_model.py")
IC_BALANCE_MODEL_PY = os.path.join(APP_DIR, "close", "ic_balance_model.py")

TERMINAL = ("Green", "Amber", "Red", "Error")
#: A63: the time the stub site's clock reads when a data change is recorded.
CHANGED_AT = datetime(2026, 9, 26, 10, 15, 30)
SIGNED = ("Signed Off", "Acknowledged", "Overridden")
QUARTERS = {1: "Q1", 2: "Q1", 3: "Q1", 4: "Q2", 5: "Q2", 6: "Q2",
            7: "Q3", 8: "Q3", 9: "Q3", 10: "Q4", 11: "Q4", 12: "Q4"}


class _D(dict):
    """frappe._dict: item and attribute access."""

    def __getattr__(self, name):
        return self.get(name)


def _month_end(fy, fp):
    nxt = date(fy + (fp == 12), fp % 12 + 1, 1)
    return date.fromordinal(nxt.toordinal() - 1)


def _year(fy, status="Closed", overrides=None, opening=None, closing=None):
    """Regular P01-P12 of ``fy`` (calendar months) plus optional P0/P13."""
    rows = []
    if opening is not None:
        rows.append({"fiscal_year": fy, "fiscal_period": 0, "period_code": "P00",
                     "period_label": "Opening", "period_type": "Opening",
                     "start_date": date(fy, 1, 1), "end_date": date(fy, 1, 1),
                     "quarter": "", "status": opening})
    for fp in range(1, 13):
        rows.append({"fiscal_year": fy, "fiscal_period": fp, "period_code": "P%02d" % fp,
                     "period_label": "P%02d" % fp, "period_type": "Regular",
                     "start_date": date(fy, fp, 1), "end_date": _month_end(fy, fp),
                     "quarter": QUARTERS[fp],
                     "status": (overrides or {}).get(fp, status)})
    if closing is not None:
        rows.append({"fiscal_year": fy, "fiscal_period": 13, "period_code": "P13",
                     "period_label": "Closing", "period_type": "Closing",
                     "start_date": date(fy, 12, 31), "end_date": date(fy, 12, 31),
                     "quarter": "", "status": closing})
    return rows


def _entity(name, frequency="Monthly", status="Active", is_group=0):
    return {"name": name, "reporting_frequency": frequency, "status": status, "is_group": is_group}


def _owner(entity, effective=date(2020, 1, 1), end=None, docstatus=1):
    return {"data_area_id": entity, "effective_date": effective, "end_date": end,
            "docstatus": docstatus}


def _rec(entity, fy, fp, docstatus=1):
    return {"data_area_id": entity, "fiscal_year": fy, "fiscal_period": fp,
            "docstatus": docstatus}


def _run(name, fy, fp, signoff="Signed Off", status="Green", day=1):
    return {"name": name, "fiscal_year": fy, "fiscal_period": fp, "status": status,
            "signoff_status": signoff, "completed_at": datetime(2026, 1, day),
            "creation": datetime(2026, 1, day)}


class _Site:
    """A clean FY2025 P09: first close P07, P07-P08 Closed and signed off,
    P09 Open, ZZA Monthly and ZZB Quarterly (P09 is a quarter-end), both
    with a submitted TB."""

    def __init__(self):
        self.rows = _year(2025, overrides={9: "Open", 10: "Open", 11: "Open", 12: "Open"})
        self.settings = {"first_close_fiscal_year": 2025, "first_close_fiscal_period": 7,
                         "self_approval": "Blocked", "rate_move_threshold": 50,
                         #: N43t: both declared and valid (N41's rule), so N45's
                         #: new read finds them and every existing "no gap"
                         #: assertion still holds. Until N45 nothing reads them.
                         "statement_cta_account": "3300",
                         "statement_result_account": "3100",
                         #: W5-2 (8.4): an amount declared, so the new
                         #: commentary-threshold gap stays out of every
                         #: existing "no gap" assertion.
                         "commentary_threshold_amount": 5000,
                         "commentary_threshold_percent": 0,
                         "commentary_threshold_combine": ""}
        self.records = {
            "Entity": [_entity("ZZA"), _entity("ZZB", "Quarterly")],
            "Ownership Period": [_owner("ZZA"), _owner("ZZB")],
            "Trial Balance Submission": [_rec("ZZA", 2025, 9), _rec("ZZB", 2025, 9)],
            "TB Exception": [],
            "Assertion Run": [_run("RUN-7", 2025, 7), _run("RUN-8", 2025, 8)],
            "Connector": [],
            "Connector Legal Entity": [],
            #: N43t: the two statement accounts named by `self.settings` above,
            #: both Published BS leaves under a heading (N41's rule, S3).
            #: Until N45 nothing reads them.
            "Main Account": [
                {"name": "3300", "is_group": 0, "status": "Published",
                 "statement_section": "Balance Sheet", "account_name": "AOCI — CTA",
                 "parent_account": "3"},
                {"name": "3100", "is_group": 0, "status": "Published",
                 "statement_section": "Balance Sheet", "account_name": "Retained earnings",
                 "parent_account": "3"},
            ],
        }
        self.whitelisted = set()
        self.get_all_calls = []
        self.signed_off_calls = []
        #: (run name, writer in force, fields changed) per Assertion Run save (A31).
        self.saves = []
        #: A63: the period rows' data-change fields, and every write to them.
        self.data_changes = {}
        self.period_writes = []
        #: T04b: (kind, fiscal_year, fiscal_period, reference_doctype,
        #: reference_name, reason) per close_event.record call.
        self.close_events = []
        #: T04b failure path: set to a message to make close_event.record raise.
        self.close_event_fail = None
        #: C18t: the stubbed `konsol.close.ic_api.tolerance_gap`/`signoff_summary`
        #: read these. `ic_calls` records every `signoff_summary` call (C19).
        self.ic_tolerance_gap = None
        #: W5-2: every stubbed statement_api.signoff_commentary call.
        self.commentary_calls = []
        self.ic_summary = {"state": "not_configured",
                           "message": "Intercompany not configured — nothing was checked.",
                           "counts": None, "sent_back_open": None}
        self.ic_calls = []
        #: #305 5.4 (W5-4): the stubbed `konsol.close.ic_balance_api.rule_gap`
        #: returns this; `ic_rule_gap_calls` records each (fy, fp) it is asked.
        self.ic_rule_gap = None
        self.ic_rule_gap_calls = []


def _match(value, cond):
    if isinstance(cond, (list, tuple)):
        op, arg = cond[0], cond[1]
        if op == "in":
            return value in arg
        if op == "is":
            assert arg in ("set", "not set"), cond
            return (value not in (None, "")) == (arg == "set")
        if op == "<=":
            return value is not None and value <= arg
        if op == ">=":
            return value is not None and value >= arg
        if op == "<":
            return value is not None and value < arg
        if op in ("=", "=="):
            return value == arg
        if op in ("!=",):
            return value != arg
        raise AssertionError("stub: unsupported operator %r" % (op,))
    return value == cond


def _load(site):
    frappe = types.ModuleType("frappe")
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def throw(msg, exc=None, title=None, **k):
        err = (exc or frappe.ValidationError)(msg)
        err.title = title
        raise err

    def whitelist(*a, **k):
        def deco(fn):
            site.whitelisted.add(fn.__name__)
            return fn
        return deco

    def get_all(doctype, filters=None, fields=None, order_by=None, pluck=None, **k):
        site.get_all_calls.append((doctype, dict(filters or {})))
        assert doctype in site.records, "stub: unexpected doctype %s" % doctype
        rows = [r for r in site.records[doctype]
                if all(_match(r.get(f), c) for f, c in (filters or {}).items())]
        if order_by:
            assert order_by.startswith("completed_at desc"), order_by
            rows = sorted(rows, key=lambda r: (r.get("completed_at"), r.get("creation")), reverse=True)
        if pluck:
            return [r.get(pluck) for r in rows]
        return [_D({f: r.get(f) for f in (fields or ["name"])}) for r in rows]

    def get_single_value(doctype, field):
        assert doctype == "Close Settings", doctype
        return site.settings.get(field)

    frappe.throw = throw
    frappe.whitelist = whitelist
    frappe.get_all = get_all
    frappe._ = lambda s: s
    frappe._dict = _D
    def get_value(doctype, filters, fieldname, as_dict=False, **k):
        # A63: the EPM Fiscal Year Period row, looked up the way signoff_api's
        # _closed does (the year is named by its fiscal_year).
        assert doctype == "EPM Fiscal Year Period", doctype
        assert filters.get("parenttype") == "EPM Fiscal Year", filters
        assert filters.get("parentfield") == "periods", filters
        key = (int(filters["parent"]), int(filters["fiscal_period"]))
        row = next((r for r in site.rows
                    if (r["fiscal_year"], r["fiscal_period"]) == key), None)
        if row is None:
            return None
        rec = _D(name="ROW-%d-%d" % key, period_type=row["period_type"],
                 **site.data_changes.get(key, {}))
        if isinstance(fieldname, (list, tuple)):
            return _D({f: rec.get(f) for f in fieldname}) if as_dict else tuple(
                rec.get(f) for f in fieldname)
        return rec.get(fieldname)

    def set_value(doctype, name, values, value=None, update_modified=True, **k):
        assert doctype == "EPM Fiscal Year Period", doctype
        assert isinstance(values, dict), values
        site.period_writes.append((name, dict(values), update_modified))
        fy, fp = (int(x) for x in name.split("-")[1:])
        site.data_changes.setdefault((fy, fp), {}).update(values)

    frappe.db = types.SimpleNamespace(get_single_value=get_single_value,
                                      get_value=get_value, set_value=set_value)
    frappe.session = types.SimpleNamespace(user="zz@example.com")

    def _by_path(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    konsol = types.ModuleType("konsol")
    close = types.ModuleType("konsol.close")
    period_model = _by_path("konsol.close.period_model", PERIOD_MODEL_PY)
    signoff_model = _by_path("konsol.close.signoff_model", SIGNOFF_MODEL_PY)
    close_policy_model = _by_path("konsol.close.close_policy_model", CLOSE_POLICY_MODEL_PY)
    scope_model = _by_path("konsol.close.scope_model", SCOPE_MODEL_PY)
    statement_model = _by_path("konsol.close.statement_model", STATEMENT_MODEL_PY)
    _real_covered = scope_model.covered

    def _covered_spy(rows, start_date):
        site.__dict__.setdefault("scope_calls", []).append(start_date)
        return _real_covered(rows, start_date)

    scope_model.covered = _covered_spy
    close.period_model, close.signoff_model = period_model, signoff_model
    close.close_policy_model = close_policy_model
    close.scope_model = scope_model
    close.statement_model = statement_model

    # T04b: a stub Close Event writer (T02a's konsol/close/close_event.py),
    # so the gate's lazy `from konsol.close import close_event` resolves.
    close_event = types.ModuleType("konsol.close.close_event")

    def _record_event(kind, fiscal_year, fiscal_period, reference_doctype=None,
                       reference_name=None, reason=None, detail=None, entity=None):
        if site.close_event_fail:
            raise RuntimeError(site.close_event_fail)
        site.close_events.append(
            (kind, fiscal_year, fiscal_period, reference_doctype, reference_name, reason, entity))

    close_event.record = _record_event
    close.close_event = close_event

    # C18t: a stub `konsol.close.ic_api`, so `from konsol.close import ic_api`
    # (C19 on) resolves to this rather than the real module, which would
    # otherwise run against this fake frappe.
    ic_api = types.ModuleType("konsol.close.ic_api")

    def signoff_summary(fiscal_year, fiscal_period):
        site.ic_calls.append((fiscal_year, fiscal_period))
        return dict(site.ic_summary)

    ic_api.tolerance_gap = lambda: site.ic_tolerance_gap
    ic_api.signoff_summary = signoff_summary
    close.ic_api = ic_api

    # #305 5.4: a stub `konsol.close.ic_balance_api` (imported lazily by the
    # gate), so the real module never runs against this fake frappe.
    ic_balance_api = types.ModuleType("konsol.close.ic_balance_api")

    def rule_gap(fiscal_year, fiscal_period):
        site.ic_rule_gap_calls.append((fiscal_year, fiscal_period))
        return site.ic_rule_gap

    ic_balance_api.rule_gap = rule_gap
    close.ic_balance_api = ic_balance_api

    # W5-2 (8.4): a stub `konsol.close.statement_api`, so signoff_gate's lazy
    # `from konsol.close import statement_api` resolves here (the real module
    # reads ClickHouse). Records each signoff_commentary call.
    statement_api = types.ModuleType("konsol.close.statement_api")

    def signoff_commentary(fiscal_year, fiscal_period):
        site.commentary_calls.append((fiscal_year, fiscal_period))
        return {"state": "checked", "threshold": None, "message": None, "groups": [],
                "required_missing": 0}

    statement_api.signoff_commentary = signoff_commentary
    close.statement_api = statement_api

    calendar = types.ModuleType("konsol.fiscal_calendar")
    calendar.fiscal_period_rows = lambda: [dict(r) for r in site.rows]
    period_status = types.ModuleType("konsol.period_status")
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    run_mod = types.ModuleType("konsol.consolidation.doctype.assertion_run.assertion_run")
    run_mod.TERMINAL_STATUSES = TERMINAL

    def assert_close_signed_off(fiscal_year, fiscal_period):
        # Stands for assertion_run.assert_close_signed_off (A23 wires it): the
        # latest terminal run must be signed. Records each call.
        site.signed_off_calls.append((fiscal_year, fiscal_period))
        runs = sorted((r for r in site.records["Assertion Run"]
                       if (r["fiscal_year"], r["fiscal_period"]) == (fiscal_year, fiscal_period)
                       and r["status"] in TERMINAL),
                      key=lambda r: (r["completed_at"], r["creation"]), reverse=True)
        if not runs or runs[0]["signoff_status"] not in SIGNED:
            throw("Close %s-%s is not signed off." % (fiscal_year, fiscal_period),
                  title="Close sign-off required")
        return runs[0]["name"]

    run_mod.assert_close_signed_off = assert_close_signed_off

    # A31: the sign-off writer (assertion_run.writing, A48). The stub run's
    # save is refused unless the sign-off writer is in force for that run, as
    # _refuse_unflagged_changes refuses a signoff_status change.
    run_mod.SIGNED_STATES = SIGNED
    run_mod.SIGNOFF_WRITER = "sign-off"
    run_mod.RE_SIGN_NEEDED = "Re-sign Needed"
    writer = []

    import contextlib

    @contextlib.contextmanager
    def writing(name_of_writer, run_name):
        writer.append((name_of_writer, run_name))
        try:
            yield
        finally:
            writer.pop()

    run_mod.writing = writing

    class _RunDoc:
        def __init__(self, record):
            self._record = record
            self.__dict__.update(record)

        def save(self, ignore_permissions=False):
            changed = {f: getattr(self, f) for f in ("signoff_status", "affected_by")
                       if getattr(self, f, None) != self._record.get(f)}
            active = writer[-1] if writer else None
            if changed and active != ("sign-off", self.name):
                throw("Assertion Run %s: signoff_status can only be changed by Sign off."
                      % self.name)
            site.saves.append((self.name, active, changed, ignore_permissions))
            self._record.update(changed)

    def get_doc(doctype, name):
        assert doctype == "Assertion Run", doctype
        for r in site.records["Assertion Run"]:
            if r["name"] == name:
                return _RunDoc(r)
        raise AssertionError("stub: no Assertion Run %s" % name)

    frappe.get_doc = get_doc
    frappe.utils = types.SimpleNamespace(nowdate=lambda: "2026-09-25",
                                         now_datetime=lambda: CHANGED_AT)
    konsol.close, konsol.fiscal_calendar, konsol.period_status = close, calendar, period_status

    mods = {"frappe": frappe, "konsol": konsol, "konsol.close": close,
            "konsol.close.period_model": period_model,
            "konsol.close.signoff_model": signoff_model,
            "konsol.close.close_policy_model": close_policy_model,
            "konsol.close.scope_model": scope_model,
            "konsol.close.statement_model": statement_model,
            "konsol.close.close_event": close_event,
            "konsol.close.ic_api": ic_api,
            "konsol.close.ic_balance_api": ic_balance_api,
            "konsol.close.statement_api": statement_api,
            "konsol.fiscal_calendar": calendar, "konsol.period_status": period_status,
            "konsol.consolidation": types.ModuleType("konsol.consolidation"),
            "konsol.consolidation.doctype": types.ModuleType("konsol.consolidation.doctype"),
            "konsol.consolidation.doctype.assertion_run":
                types.ModuleType("konsol.consolidation.doctype.assertion_run"),
            "konsol.consolidation.doctype.assertion_run.assertion_run": run_mod}
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("close_signoff_gate_under_test", GATE_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    return module, frappe, mods, period_status


def _call(site, fn, *args):
    """Run with the stub modules installed (lazy imports resolve to the stubs)."""
    module, frappe, mods, _ps = _load(site)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        return getattr(module, fn)(*args)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _blocked(site, fy=2025, fp=9):
    """assert_can_sign must raise; returns the exception."""
    with pytest.raises(Exception) as info:
        _call(site, "assert_can_sign", fy, fp)
    err = info.value
    assert type(err).__name__ == "ValidationError", type(err)
    assert err.title == "Sign-off blocked", err.title
    return str(err)


# --- a clean period -----------------------------------------------------------

def test_clean_period_has_no_problems_and_can_be_signed():
    site = _Site()
    assert _call(site, "sign_off_problems", 2025, 9) == {
        "config_gaps": [], "order": None, "completeness": None}
    assert _call(site, "assert_can_sign", 2025, 9) is None


def test_in_scope_entities_are_active_leaves_with_covering_ownership():
    site = _Site()
    site.records["Entity"] += [_entity("ZZG", is_group=1), _entity("ZZI", status="Inactive"),
                               _entity("ZZN")]  # ZZN: no ownership period
    site.records["Ownership Period"] += [_owner("ZZG"), _owner("ZZI")]
    assert _call(site, "in_scope_entities", 2025, 9) == ["ZZA", "ZZB"]


# --- G02: the scope computation goes through scope_model, once -----------------

def test_scope_is_computed_by_scope_model():
    site = _Site()
    assert _call(site, "in_scope_entities", 2025, 9) == ["ZZA", "ZZB"]
    assert site.scope_calls == [date(2025, 9, 1)], site.scope_calls


def test_the_inline_rule_is_gone():
    with open(GATE_PY) as f:
        source = f.read()
    assert "end >= start" not in source, "signoff_gate keeps its own copy of the coverage rule"


# --- failure paths: each raises, one message naming every fix -----------------

def test_undeclared_first_close_blocks_and_skips_the_order_gate():
    # Close Settings Int fields read back as 0 when unset (coordinator note, A06),
    # or None when never saved. Both are undeclared; the order gate is skipped
    # (order_problem raises on an undeclared first close, coordinator note A09).
    for settings in ({"first_close_fiscal_year": 0, "first_close_fiscal_period": 0},
                     {"first_close_fiscal_year": 2025, "first_close_fiscal_period": 0},
                     {}):
        site = _Site()
        site.settings = settings
        site.rows = _year(2025, status="Open")  # every period Open: (0,0) would land on P01
        problems = _call(site, "sign_off_problems", 2025, 9)
        # P05: these settings dicts never declare the two policies either, so
        # policy_gaps adds both codes after the first-close gap. N45: nor the
        # two statement accounts, so the statement gap follows them. W5-2:
        # nor the commentary threshold, so its gap follows the statement gap.
        assert [g["code"] for g in problems["config_gaps"]] == [
            "first_close_undeclared", "self_approval_undeclared", "rate_move_undeclared",
            "statement_accounts_undeclared", "commentary_threshold_undeclared",
        ], settings
        assert problems["order"] is None, settings
        message = _blocked(site)
        assert "Declare the first close period in Close Settings" in message, message


def test_an_open_earlier_period_blocks():
    site = _Site()
    site.rows = _year(2025, overrides={7: "Open", 9: "Open"})
    site.records["Assertion Run"] = [_run("RUN-8", 2025, 8)]
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert problems["order"]["message"] == "Sign off and close P07 first"
    assert "Sign off and close P07 first" in _blocked(site)


def test_order_gate_reads_the_latest_terminal_run_per_period():
    # P08: signed long ago, then re-signing became needed; a later Running run
    # is not terminal and does not count.
    site = _Site()
    site.records["Assertion Run"] = [
        _run("RUN-7", 2025, 7),
        _run("RUN-8a", 2025, 8, day=1),
        _run("RUN-8b", 2025, 8, signoff="Re-sign Needed", day=5),
        _run("RUN-8c", 2025, 8, signoff="Signed Off", status="Running", day=9),
    ]
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert problems["order"]["message"] == "Re-sign P08 first"
    assert "Re-sign P08 first" in _blocked(site)


def test_history_before_first_close_does_not_block():
    site = _Site()
    site.rows = _year(2025, overrides={1: "Open", 2: "Open", 9: "Open"})
    assert _call(site, "sign_off_problems", 2025, 9)["order"] is None


def test_open_closing_and_opening_periods_of_the_previous_year_do_not_block_p1():
    # P5: the gates apply to Regular periods only. FY2025 P13 (Closing) and
    # FY2026 P00 (Opening) are Open; FY2026 P01 can still be signed off.
    site = _Site()
    site.rows = (_year(2025, closing="Open", opening="Open")
                 + _year(2026, status="Open", opening="Open"))
    site.records["Assertion Run"] = [_run("RUN-%d" % fp, 2025, fp) for fp in range(7, 13)]
    site.records["Trial Balance Submission"] = [_rec("ZZA", 2026, 1)]
    problems = _call(site, "sign_off_problems", 2026, 1)
    assert problems == {"config_gaps": [], "order": None, "completeness": None}, problems
    assert _call(site, "assert_can_sign", 2026, 1) is None


def test_a_missing_tb_blocks_and_names_the_entity():
    site = _Site()
    site.records["Trial Balance Submission"] = [_rec("ZZA", 2025, 9), _rec("ZZB", 2025, 9, docstatus=0)]
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert problems["completeness"]["missing"] == ["ZZB"]
    message = _blocked(site)
    assert "No trial balance from ZZB. Upload it or declare an exception." in message, message


def test_a_submitted_tb_exception_covers_the_missing_tb_but_a_draft_does_not():
    site = _Site()
    site.records["Trial Balance Submission"] = [_rec("ZZA", 2025, 9)]
    site.records["TB Exception"] = [_rec("ZZB", 2025, 9, docstatus=0)]
    assert _call(site, "sign_off_problems", 2025, 9)["completeness"]["missing"] == ["ZZB"]
    site.records["TB Exception"] = [_rec("ZZB", 2025, 9)]
    assert _call(site, "sign_off_problems", 2025, 9)["completeness"] is None


def test_other_periods_records_do_not_count():
    site = _Site()
    site.records["Trial Balance Submission"] = [_rec("ZZA", 2025, 9), _rec("ZZB", 2025, 8),
                                                _rec("ZZB", 2024, 9)]
    assert _call(site, "sign_off_problems", 2025, 9)["completeness"]["missing"] == ["ZZB"]


def test_a_quarterly_entity_owes_nothing_before_quarter_end():
    site = _Site()
    site.rows = _year(2025, overrides={8: "Open", 9: "Open"})
    site.records["Assertion Run"] = [_run("RUN-7", 2025, 7)]
    site.records["Trial Balance Submission"] = [_rec("ZZA", 2025, 8)]
    assert _call(site, "sign_off_problems", 2025, 8) == {
        "config_gaps": [], "order": None, "completeness": None}


def test_a_blank_frequency_blocks_and_names_the_entity():
    site = _Site()
    site.records["Entity"] = [_entity("ZZA"), _entity("ZZB", "")]
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert [g["code"] for g in problems["config_gaps"]] == ["frequency_undeclared"]
    message = _blocked(site)
    assert "Set the Reporting Frequency (Monthly or Quarterly) on ZZB" in message, message


# --- P05: the two policy gaps (#305-D2-3, #305-D2-9) ---------------------------

def test_both_policies_declared_add_no_gap():
    site = _Site()  # self_approval="Blocked", rate_move_threshold=50
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert [g["code"] for g in problems["config_gaps"]] == []
    assert _call(site, "assert_can_sign", 2025, 9) is None


def test_undeclared_self_approval_blocks_sign_off():
    site = _Site()
    site.settings["self_approval"] = ""
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert [g["code"] for g in problems["config_gaps"]] == ["self_approval_undeclared"]
    message = _blocked(site)
    assert "Declare the self-approval policy" in message, message


def test_undeclared_rate_move_threshold_blocks_sign_off():
    site = _Site()
    site.settings["rate_move_threshold"] = 0  # Frappe Percent reads back 0 when unset
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert [g["code"] for g in problems["config_gaps"]] == ["rate_move_undeclared"]
    message = _blocked(site)
    assert "Declare the rate move threshold" in message, message


def test_one_message_lists_every_problem():
    site = _Site()
    site.settings = {"first_close_fiscal_year": 2025, "first_close_fiscal_period": 7,
                     "self_approval": "Blocked", "rate_move_threshold": 50}
    site.rows = _year(2025, overrides={7: "Open", 9: "Open"})
    site.records["Assertion Run"] = []
    site.records["Entity"] = [_entity("ZZA"), _entity("ZZB", "Quarterly"), _entity("ZZC", None)]
    site.records["Ownership Period"] += [_owner("ZZC")]
    site.records["Trial Balance Submission"] = [_rec("ZZA", 2025, 9)]
    message = _blocked(site)
    for part in ("Set the Reporting Frequency", "on ZZC", "Sign off and close P07 first",
                 "No trial balance from ZZB"):
        assert part in message, (part, message)


# --- scope --------------------------------------------------------------------

def test_an_entity_whose_ownership_ended_before_the_period_is_out_of_scope():
    site = _Site()
    site.records["Entity"] += [_entity("ZZX")]
    site.records["Ownership Period"] += [_owner("ZZX", end=date(2025, 8, 31))]
    assert "ZZX" not in _call(site, "in_scope_entities", 2025, 9)
    # and it owes no TB: P09 is still clean
    assert _call(site, "sign_off_problems", 2025, 9)["completeness"] is None
    # ownership ending on the period start still covers it
    site.records["Ownership Period"][-1] = _owner("ZZX", end=date(2025, 9, 1))
    assert "ZZX" in _call(site, "in_scope_entities", 2025, 9)


def test_ownership_starting_after_the_period_start_or_unsubmitted_is_out_of_scope():
    site = _Site()
    site.records["Entity"] += [_entity("ZZL"), _entity("ZZD")]
    site.records["Ownership Period"] += [_owner("ZZL", effective=date(2025, 9, 2)),
                                         _owner("ZZD", docstatus=0)]
    assert _call(site, "in_scope_entities", 2025, 9) == ["ZZA", "ZZB"]


def test_a_connector_fed_entity_is_still_in_scope():
    # Problems 7 / konsolidat#221: connector-fed entities are not exempt.
    site = _Site()
    site.records["Connector"] = [{"name": "ZZCONN", "enabled": 1}]
    site.records["Connector Legal Entity"] = [{"parent": "ZZCONN", "parenttype": "Connector",
                                               "entity_id": "ZZB"}]
    site.records["Trial Balance Submission"] = [_rec("ZZA", 2025, 9)]
    assert "ZZB" in _call(site, "in_scope_entities", 2025, 9)
    assert _call(site, "sign_off_problems", 2025, 9)["completeness"]["missing"] == ["ZZB"]


# --- the target period --------------------------------------------------------

def test_an_undeclared_target_raises_period_not_declared():
    site = _Site()
    module, frappe, mods, period_status = _load(site)
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        with pytest.raises(period_status.PeriodNotDeclared) as info:
            module.sign_off_problems(2031, 4)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    assert "EPM Fiscal Year" in str(info.value)


def test_a_non_regular_target_is_refused():
    site = _Site()
    site.rows = _year(2025, closing="Open", overrides={9: "Open"})
    with pytest.raises(Exception) as info:
        _call(site, "sign_off_problems", 2025, 13)
    assert "only Regular periods are signed off" in str(info.value), str(info.value)


# --- assert_period_closable (konsol#305 A23; story 9.3) -----------------------

def _closable(site, fy, fp, period_type="Regular"):
    """(result, None) when closable, else (None, the exception)."""
    try:
        return _call(site, "assert_period_closable", fy, fp, period_type), None
    except Exception as e:  # noqa: BLE001 - the stub's ValidationError
        return None, e


def test_closing_an_unsigned_regular_period_is_refused():
    site = _Site()   # P09: no run at all
    result, err = _closable(site, 2025, 9)
    assert err is not None and "not signed off" in str(err), err
    assert site.signed_off_calls == [(2025, 9)], site.signed_off_calls

    site = _Site()
    site.records["Assertion Run"].append(_run("RUN-9", 2025, 9, signoff="Not Signed Off"))
    result, err = _closable(site, 2025, 9)
    assert err is not None, "an unsigned run let the period close"

    # Re-sign Needed does not count (P9).
    site = _Site()
    site.records["Assertion Run"].append(_run("RUN-9", 2025, 9, signoff="Re-sign Needed"))
    result, err = _closable(site, 2025, 9)
    assert err is not None, "a Re-sign Needed run let the period close"


def test_closing_a_signed_regular_period_passes():
    site = _Site()
    site.records["Assertion Run"].append(_run("RUN-9", 2025, 9))
    result, err = _closable(site, 2025, 9)
    assert err is None, err
    # The first close period itself is gated, not history.
    site = _Site()
    result, err = _closable(site, 2025, 7)
    assert err is None, err
    assert site.signed_off_calls == [(2025, 7)], site.signed_off_calls


def test_a_history_period_closes_without_a_signoff():
    site = _Site()   # first close P07; P06 and FY2024 are history, no runs
    for fy, fp in ((2025, 6), (2025, 1), (2024, 12)):
        result, err = _closable(site, fy, fp)
        assert err is None, (fy, fp, err)
    assert site.signed_off_calls == [], site.signed_off_calls


def test_non_regular_periods_close_without_a_signoff():
    site = _Site()
    for fp, kind in ((13, "Closing"), (0, "Opening"), (14, "Adjustment")):
        result, err = _closable(site, 2025, fp, kind)
        assert err is None, (kind, err)
    assert site.signed_off_calls == [], site.signed_off_calls


def test_an_undeclared_first_close_refuses_the_close():
    for settings in ({"first_close_fiscal_year": 0, "first_close_fiscal_period": 0},
                     {"first_close_fiscal_year": 2025, "first_close_fiscal_period": 0},
                     {}):
        site = _Site()
        site.settings = settings
        site.records["Assertion Run"].append(_run("RUN-9", 2025, 9))
        result, err = _closable(site, 2025, 9)
        assert err is not None, ("closed with first close undeclared", settings)
        assert "Declare the first close period" in str(err), str(err)
        assert "Close Settings" in str(err), str(err)
        assert site.signed_off_calls == [], settings


# --- shape --------------------------------------------------------------------

def test_module_has_no_whitelisted_functions():
    with open(GATE_PY) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                assert "whitelist" not in ast.unparse(target), node.name
    site = _Site()
    _call(site, "sign_off_problems", 2025, 9)
    assert site.whitelisted == set()


# --- A31/A57: reopening marks the period and later signed periods "Re-sign Needed"

def _reopen_site():
    """FY2025 first close P07; P07-P09 signed, P10 run unsigned, P11 no run."""
    site = _Site()
    site.records["Assertion Run"] = [
        _run("RUN-5", 2025, 5),          # history (before the first close)
        _run("RUN-7", 2025, 7),
        _run("RUN-8", 2025, 8, signoff="Acknowledged", status="Amber"),
        _run("RUN-9", 2025, 9, signoff="Overridden", status="Red"),
        _run("RUN-10", 2025, 10, signoff="Not Signed Off"),
    ]
    return site


def _mark(site, fy, fp, code, reason="Late supplier invoice", user="zz-lead@example.com"):
    return _call(site, "mark_resign_needed_on_reopen", fy, fp, code, reason, user)


def _run_rec(site, name):
    return next(r for r in site.records["Assertion Run"] if r["name"] == name)


def test_reopening_marks_the_reopened_period_and_every_later_signed_regular_period():
    site = _reopen_site()
    marked = _mark(site, 2025, 7, "P07")
    assert sorted(marked) == ["RUN-7", "RUN-8", "RUN-9"], marked
    for name in ("RUN-7", "RUN-8", "RUN-9"):
        rec = _run_rec(site, name)
        assert rec["signoff_status"] == "Re-sign Needed", rec
        text = rec["affected_by"]
        assert text == ("FY2025 P07 reopened on 2026-09-25 by zz-lead@example.com: "
                        "Late supplier invoice"), text
    # The unsigned P10 and the history P05 are untouched.
    assert _run_rec(site, "RUN-10")["signoff_status"] == "Not Signed Off"
    assert _run_rec(site, "RUN-5")["signoff_status"] == "Signed Off"


def test_a_reopened_period_cannot_close_on_its_old_signature():
    # A57 (review #1): reopen P07 with P07 and P08 signed -> both latest runs
    # are Re-sign Needed, and closing P07 is refused until it is re-signed.
    site = _reopen_site()
    _mark(site, 2025, 7, "P07")
    for name in ("RUN-7", "RUN-8"):
        assert _run_rec(site, name)["signoff_status"] == "Re-sign Needed", name
    result, err = _closable(site, 2025, 7)
    assert err is not None and "not signed off" in str(err), (result, err)
    # Re-signed on a fresh run: the close goes through.
    site.records["Assertion Run"].append(_run("RUN-7-B", 2025, 7, day=9))
    result, err = _closable(site, 2025, 7)
    assert err is None, err
    assert result == "RUN-7-B", result


def test_the_function_is_named_for_what_it_marks():
    # It marks the reopened period too, so "later" in the name would lie.
    module = _load(_Site())[0]
    assert hasattr(module, "mark_resign_needed_on_reopen"), "renamed function missing"
    assert not hasattr(module, "mark_later_resign_needed"), "old name still exported"


def test_the_mark_is_saved_through_the_signoff_writer():
    site = _reopen_site()
    _mark(site, 2025, 7, "P07")
    assert sorted(s[0] for s in site.saves) == ["RUN-7", "RUN-8", "RUN-9"], site.saves
    for name, active, changed, ignore in site.saves:
        assert active == ("sign-off", name), (name, active)
        assert set(changed) == {"signoff_status", "affected_by"}, changed
        assert ignore is True, "the Close Lead reopening may not own the run"


def test_only_the_latest_signed_run_of_a_period_is_marked():
    site = _reopen_site()
    site.records["Assertion Run"].append(_run("RUN-8-OLD", 2025, 8, day=1))
    _run_rec(site, "RUN-8")["completed_at"] = datetime(2026, 1, 5)
    marked = _mark(site, 2025, 7, "P07")
    assert sorted(marked) == ["RUN-7", "RUN-8", "RUN-9"], marked
    assert _run_rec(site, "RUN-8-OLD")["signoff_status"] == "Signed Off"


def test_a_run_already_needing_re_sign_is_left_alone():
    site = _reopen_site()
    _run_rec(site, "RUN-8")["signoff_status"] = "Re-sign Needed"
    _run_rec(site, "RUN-8")["affected_by"] = "FY2025 P07 reopened earlier"
    marked = _mark(site, 2025, 7, "P07")
    assert marked == ["RUN-7", "RUN-9"], marked
    assert _run_rec(site, "RUN-8")["affected_by"] == "FY2025 P07 reopened earlier"


def test_reopening_across_the_year_boundary_marks_the_next_year():
    site = _reopen_site()
    site.rows = _year(2025, status="Closed") + _year(2026, status="Open")
    site.records["Assertion Run"].append(_run("RUN-26-1", 2026, 1))
    marked = _mark(site, 2025, 12, "P12")
    assert marked == ["RUN-26-1"], marked
    assert _run_rec(site, "RUN-26-1")["signoff_status"] == "Re-sign Needed"
    assert "FY2025 P12 reopened" in _run_rec(site, "RUN-26-1")["affected_by"]


def test_history_periods_before_the_first_close_are_never_marked():
    site = _reopen_site()
    site.records["Assertion Run"].append(_run("RUN-6", 2025, 6))
    site.records["Assertion Run"].append(_run("RUN-3", 2025, 3))
    marked = _mark(site, 2025, 3, "P03")
    assert sorted(marked) == ["RUN-7", "RUN-8", "RUN-9"], marked
    # Reopening a history period does not mark it (it was never gated).
    for name in ("RUN-3", "RUN-5", "RUN-6"):
        assert _run_rec(site, name)["signoff_status"] == "Signed Off", name


def test_non_regular_later_periods_are_not_marked():
    site = _reopen_site()
    site.rows = _year(2025, closing="Closed")
    site.records["Assertion Run"].append(_run("RUN-13", 2025, 13))
    marked = _mark(site, 2025, 7, "P07")
    assert "RUN-13" not in marked, marked
    assert _run_rec(site, "RUN-13")["signoff_status"] == "Signed Off"


def test_an_undeclared_first_close_marks_every_later_signed_period():
    # No history can be told apart without a declared first close, so the
    # mark errs toward re-signing: every later signed Regular run is marked.
    site = _reopen_site()
    site.settings = {}
    site.records["Assertion Run"].append(_run("RUN-3", 2025, 3))
    marked = _mark(site, 2025, 3, "P03")
    assert sorted(marked) == ["RUN-3", "RUN-5", "RUN-7", "RUN-8", "RUN-9"], marked


def test_nothing_later_signed_marks_nothing_and_saves_nothing():
    site = _reopen_site()
    marked = _mark(site, 2025, 11, "P11")
    assert marked == [] and site.saves == [], (marked, site.saves)


# --- E10-P6a (konsol#305 T04b): a voided sign-off writes its event ----------

def test_reopening_records_a_signoff_voided_event_per_marked_run():
    site = _reopen_site()
    marked = _mark(site, 2025, 7, "P07")
    assert sorted(marked) == ["RUN-7", "RUN-8", "RUN-9"], marked
    assert sorted(e[4] for e in site.close_events) == sorted(marked), site.close_events
    for kind, fy, fp, ref_dt, ref_name, reason, entity in site.close_events:
        assert kind == "signoff_voided", kind
        assert fy == 2025, fy
        assert ref_dt == "Assertion Run", ref_dt
        assert ref_name in marked, ref_name
        assert "reopened" in reason, reason
        # S1/E2-6: a reopen names no entity; the void stays group-visible.
        assert entity is None, entity
    # Each event names the run's own period, not just the reopened period.
    by_name = {e[4]: e for e in site.close_events}
    assert by_name["RUN-9"][2] == 9, by_name["RUN-9"]


def test_a_close_event_failure_propagates_from_a_reopen():
    site = _reopen_site()
    site.close_event_fail = "writer down"
    with pytest.raises(RuntimeError) as info:
        _mark(site, 2025, 7, "P07")
    assert "writer down" in str(info.value)


# --- A63 (#305-R2b-3): a signature covers only the data its run checked ------

PERIOD_JSON = os.path.join(APP_DIR, "epm", "doctype", "epm_fiscal_year_period",
                           "epm_fiscal_year_period.json")
CHANGE_FIELDS = ("data_changed_at", "data_changed_by", "data_change")
CHANGED_TEXT = "TB TBS-ZZA-2025-8 cancelled"
CHANGED_BY = "zz-acct@example.com"


def _record(site, fy, fp, text=CHANGED_TEXT, user=CHANGED_BY):
    return _call(site, "record_data_change", fy, fp, text, user)


def test_a_data_change_is_recorded_on_the_period_row():
    site = _Site()
    _record(site, 2025, 8)
    # The changed period's own row is written first, with the real text
    # (R41k adds further, carried writes to later rows -- see below).
    assert site.period_writes[0] == (
        "ROW-2025-8", {"data_changed_at": CHANGED_AT, "data_changed_by": CHANGED_BY,
                        "data_change": CHANGED_TEXT}, False), site.period_writes


def test_a_data_change_marks_the_periods_signed_run_re_sign_needed_with_the_text():
    site = _Site()
    marked = _record(site, 2025, 8)
    assert marked == ["RUN-8"], marked
    rec = _run_rec(site, "RUN-8")
    assert rec["signoff_status"] == "Re-sign Needed", rec
    assert rec["affected_by"] == (
        "TB TBS-ZZA-2025-8 cancelled at 2026-09-26 10:15:30 by zz-acct@example.com"), rec
    # Only that period: the earlier signed P07 keeps its signature.
    assert _run_rec(site, "RUN-7")["signoff_status"] == "Signed Off"
    # Through the sign-off writer, never a raw field write.
    assert [(s[0], s[1]) for s in site.saves] == [("RUN-8", ("sign-off", "RUN-8"))], site.saves
    assert site.saves[0][3] is True, "the uploader need not own the run"


def test_a_data_change_also_marks_a_later_signed_period():
    """AMENDED 4 Oct (#305-W4-4, Deepak "all ★", #305 issuecomment-5978983396):
    a cumulative balance sheet means a data change also voids every LATER
    signed period's signature, not only the changed period's own — for every
    existing record_data_change caller (a TB submit/cancel too, not only
    S42's new hook). A TB change in P07 marks the already-signed P09."""
    site = _Site()
    site.records["Assertion Run"].append(_run("RUN-9", 2025, 9))
    marked = _record(site, 2025, 7, text="TB TBS-ZZA-2025-7 cancelled")
    assert sorted(marked) == ["RUN-7", "RUN-8", "RUN-9"], marked
    for name in ("RUN-7", "RUN-8", "RUN-9"):
        assert _run_rec(site, name)["signoff_status"] == "Re-sign Needed", name
    # One Close Event per period marked (T04b), not one combined event.
    assert sorted(e[4] for e in site.close_events) == ["RUN-7", "RUN-8", "RUN-9"], site.close_events
    for kind, fy, fp, ref_dt, ref_name, reason, entity in site.close_events:
        assert kind == "signoff_voided", kind
        assert ref_dt == "Assertion Run", ref_dt
        assert "TB TBS-ZZA-2025-7 cancelled" in reason, reason


def test_a_data_change_does_not_mark_an_earlier_signed_period():
    """The earlier period's signature covered data that has not changed."""
    site = _Site()
    marked = _record(site, 2025, 8)
    assert "RUN-7" not in marked, marked
    assert _run_rec(site, "RUN-7")["signoff_status"] == "Signed Off"


def test_only_the_latest_signed_run_of_the_period_is_marked():
    site = _Site()
    site.records["Assertion Run"].append(_run("RUN-8-OLD", 2025, 8, day=1))
    _run_rec(site, "RUN-8")["completed_at"] = datetime(2026, 1, 5)
    assert _record(site, 2025, 8) == ["RUN-8"]
    assert _run_rec(site, "RUN-8-OLD")["signoff_status"] == "Signed Off"


def test_an_unsigned_period_records_the_change_and_marks_nothing():
    site = _Site()
    site.records["Assertion Run"].append(_run("RUN-9", 2025, 9, signoff="Not Signed Off"))
    assert _record(site, 2025, 9) == []
    assert site.saves == []
    assert site.data_changes[(2025, 9)]["data_change"] == CHANGED_TEXT
    assert _run_rec(site, "RUN-9")["signoff_status"] == "Not Signed Off"


def test_a_history_period_change_is_recorded_but_marks_nothing():
    site = _Site()   # first close P07
    site.records["Assertion Run"].append(_run("RUN-5", 2025, 5))
    assert _record(site, 2025, 5) == []
    assert site.saves == []
    assert _run_rec(site, "RUN-5")["signoff_status"] == "Signed Off"
    assert site.data_changes[(2025, 5)]["data_changed_by"] == CHANGED_BY
    # T04b failure path: nothing marked (before the first close) -> no event.
    assert site.close_events == []


def test_a_data_change_records_a_signoff_voided_event():
    site = _Site()
    marked = _record(site, 2025, 8)
    assert marked == ["RUN-8"], marked
    assert len(site.close_events) == 1, site.close_events
    kind, fy, fp, ref_dt, ref_name, reason, entity = site.close_events[0]
    assert (kind, fy, fp, ref_dt, ref_name) == (
        "signoff_voided", 2025, 8, "Assertion Run", "RUN-8"), site.close_events
    assert CHANGED_TEXT in reason, reason
    # No caller named an entity here: the void stays group-visible.
    assert entity is None, entity


# --- S1, E2-6: a TB-triggered void carries its entity -----------------------

def test_a_data_change_names_its_entity_in_the_voided_event():
    """record_data_change accepts entity and passes it to the signoff_voided
    event, so trail scoping can hide a void whose reason names a hidden TB."""
    site = _Site()
    marked = _call(site, "record_data_change", 2025, 8, CHANGED_TEXT, CHANGED_BY, "ZZA")
    assert marked == ["RUN-8"], marked
    assert len(site.close_events) == 1, site.close_events
    kind, fy, fp, ref_dt, ref_name, reason, entity = site.close_events[0]
    assert (kind, fy, fp, ref_dt, ref_name) == (
        "signoff_voided", 2025, 8, "Assertion Run", "RUN-8"), site.close_events
    assert entity == "ZZA", site.close_events


def test_a_close_event_failure_propagates_from_a_data_change():
    site = _Site()
    site.close_event_fail = "writer down"
    with pytest.raises(RuntimeError) as info:
        _record(site, 2025, 8)
    assert "writer down" in str(info.value)
    # The run's own save already happened in this (the caller's own)
    # transaction; only the event failed to write.
    assert site.saves and site.saves[0][0] == "RUN-8", site.saves


def test_a_non_regular_period_change_is_recorded_but_marks_nothing():
    site = _Site()
    site.rows = _year(2025, overrides={9: "Open"}, closing="Open")
    site.records["Assertion Run"].append(_run("RUN-13", 2025, 13))
    assert _record(site, 2025, 13) == []
    assert site.saves == []
    assert _run_rec(site, "RUN-13")["signoff_status"] == "Signed Off"
    assert site.data_changes[(2025, 13)]["data_change"] == CHANGED_TEXT


def test_an_undeclared_period_is_refused_and_nothing_is_written():
    site = _Site()
    with pytest.raises(Exception) as info:
        _record(site, 2031, 4)
    assert type(info.value).__name__ == "PeriodNotDeclared", type(info.value)
    assert "FY2031 P04 is not declared" in str(info.value), str(info.value)
    assert site.period_writes == [] and site.saves == []


def test_data_change_reads_the_three_fields_and_blank_is_none():
    site = _Site()
    assert _call(site, "data_change", 2025, 9) == {
        "data_changed_at": None, "data_changed_by": None, "data_change": None}
    _record(site, 2025, 9)
    assert _call(site, "data_change", 2025, 9) == {
        "data_changed_at": CHANGED_AT, "data_changed_by": CHANGED_BY,
        "data_change": CHANGED_TEXT}


# --- #305 R41b (review-w4-server.md S2, coordinator call (a)): the hook ----
# calls the REAL record_data_change exactly once, and that real call does
# the whole later-period stamping, across >= 5 declared periods. The hook's
# own stub-level "how many times did I call the stub" coverage lives in
# test_close_data_change_hook.py; this loads the REAL data_change_hook.py
# wired to the REAL signoff_gate.py (this file's own `_load`, unmodified),
# so the double-marking S2 found -- hidden by a sparse-period, stubbed
# record_data_change -- cannot hide here.

HOOK_PY = os.path.join(APP_DIR, "close", "data_change_hook.py")
DATA_CHANGE_MODEL_PY = os.path.join(APP_DIR, "close", "data_change_model.py")


def _by_path_r41b(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _hook_on_real_gate(site, period, entity=None):
    """The REAL data_change_hook, wired to the REAL signoff_gate (this
    file's `_load`) and the REAL data_change_model (S41, pure). The only
    stand-in is a counting wrapper AROUND the real record_data_change
    (``_covered_spy``'s pattern, above) -- every call it counts still runs
    the unmodified real function; this is not a stub of record_data_change,
    it is a spy on it. ``close_event.period_of``/``entity_of`` are given
    fixed answers (as every other hook test here does), since this file's
    own close_event stub carries only ``.record``.

    Returns ``(on_submit, on_cancel, calls, site)``, where ``calls`` is the
    list of real record_data_change invocations
    (``[((fiscal_year, fiscal_period, text, user), {"entity": entity}), ...]``).
    """
    gate_module, frappe, mods, _ps = _load(site)
    frappe.db.table_exists = lambda dt: True
    frappe.logger = lambda: types.SimpleNamespace(
        warning=lambda *a, **k: None, info=lambda *a, **k: None)

    calls = []
    real_record_data_change = gate_module.record_data_change

    def counting_record_data_change(*a, **k):
        calls.append((a, k))
        return real_record_data_change(*a, **k)

    close_event = mods["konsol.close.close_event"]
    close_event.period_of = lambda doc: period
    close_event.entity_of = lambda doc: entity

    data_change_model = _by_path_r41b("data_change_model_for_r41b", DATA_CHANGE_MODEL_PY)

    mods2 = dict(mods)
    mods2["konsol.close.signoff_gate"] = types.SimpleNamespace(
        record_data_change=counting_record_data_change)
    mods2["konsol.close.data_change_model"] = data_change_model
    close_pkg = mods2["konsol.close"]
    close_pkg.signoff_gate = mods2["konsol.close.signoff_gate"]
    close_pkg.data_change_model = data_change_model

    saved = {n: sys.modules.get(n) for n in mods2}
    sys.modules.update(mods2)
    try:
        hook = _by_path_r41b("data_change_hook_for_r41b", HOOK_PY)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old

    # on_submit/on_cancel import frappe/fiscal_calendar/close_event/
    # signoff_gate lazily, at call time (data_change_hook.py's own
    # docstring): re-apply the stubs around every call, then restore.
    real_on_submit, real_on_cancel = hook.on_submit, hook.on_cancel

    def on_submit(doc, method=None):
        now = {n: sys.modules.get(n) for n in mods2}
        sys.modules.update(mods2)
        try:
            return real_on_submit(doc, method)
        finally:
            for n, old in now.items():
                if old is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = old

    def on_cancel(doc, method=None):
        now = {n: sys.modules.get(n) for n in mods2}
        sys.modules.update(mods2)
        try:
            return real_on_cancel(doc, method)
        finally:
            for n, old in now.items():
                if old is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = old

    return on_submit, on_cancel, calls, site


class _HookDoc(dict):
    """A Frappe document has every field; ``.get`` reads one back."""

    def __getattr__(self, name):
        return self.get(name)


def _hook_doc(doctype, name, **fields):
    return _HookDoc(doctype=doctype, name=name, **fields)


def _five_period_site():
    """FY2025 first close P07 (``_Site``'s default), with FIVE later-or-equal
    Regular periods carrying a signed run: P07-P11 (P12 has none)."""
    site = _Site()
    for name, fp in (("RUN-9", 9), ("RUN-10", 10), ("RUN-11", 11)):
        site.records["Assertion Run"].append(_run(name, 2025, fp))
    return site


def test_the_hook_calls_the_real_record_data_change_exactly_once():
    """S2 Effect 1: the hook used to call record_data_change once PER period
    changed_periods named. Across 5 signed later-or-equal periods (P07-P11)
    it must still call the real record_data_change exactly once."""
    site = _five_period_site()
    on_submit, _on_cancel, calls, site = _hook_on_real_gate(site, period=(2025, 7))
    on_submit(_hook_doc("Group Exchange Rate", "GER-1"))
    assert len(calls) == 1, calls
    args, kwargs = calls[0]
    assert args[:2] == (2025, 7), args
    assert args[2] == "Group Exchange Rate GER-1 approved", args
    assert kwargs == {"entity": None}, kwargs


def test_the_one_real_call_stamps_every_later_signed_period_with_one_void_event_each():
    """S2 Effect 3 (and the coordinator's call (a)): the single real call
    marks the changed period AND every later signed Regular period -- P07
    through P11, five periods -- "Re-sign Needed", one signoff_voided Close
    Event per marked run, not one combined event and not a second hook
    call's worth of duplicates."""
    site = _five_period_site()
    on_submit, _on_cancel, calls, site = _hook_on_real_gate(site, period=(2025, 7))
    on_submit(_hook_doc("Group Exchange Rate", "GER-1"))
    assert len(calls) == 1, calls
    for name in ("RUN-7", "RUN-8", "RUN-9", "RUN-10", "RUN-11"):
        rec = _run_rec(site, name)
        assert rec["signoff_status"] == "Re-sign Needed", (name, rec)
        assert "Group Exchange Rate GER-1 approved" in rec["affected_by"], (name, rec)
    assert sorted(e[4] for e in site.close_events) == [
        "RUN-10", "RUN-11", "RUN-7", "RUN-8", "RUN-9"], site.close_events
    assert len(site.close_events) == 5, site.close_events
    for kind, fy, fp, ref_dt, ref_name, reason, entity in site.close_events:
        assert kind == "signoff_voided", kind
        assert ref_dt == "Assertion Run", ref_dt
        assert "Group Exchange Rate GER-1 approved" in reason, reason


def test_the_changed_periods_own_row_gets_the_real_text_and_later_rows_carry_it():
    """record_data_change's docstring (AMENDED #305 R41k, review-w4-server.md
    S2 effect 3): only P07's row gets the REAL data_change text -- the later
    periods' balances move as a consequence, through their own query, not
    because their own data changed -- but every later declared Regular
    period's row (P08-P12, including P12 which carries no signed run at
    all) now carries P07's change, so each one's OWN data_change_problem
    (A66) check can see it."""
    site = _five_period_site()
    on_submit, _on_cancel, calls, site = _hook_on_real_gate(site, period=(2025, 7))
    on_submit(_hook_doc("Group Exchange Rate", "GER-1"))
    assert set(site.data_changes) == {(2025, p) for p in range(7, 13)}, site.data_changes
    assert site.data_changes[(2025, 7)]["data_change"] == (
        "Group Exchange Rate GER-1 approved"), site.data_changes
    for fp in range(8, 13):
        assert site.data_changes[(2025, fp)]["data_change"] == (
            "Balance carried from FY2025 P07: Group Exchange Rate GER-1 approved"), (
            fp, site.data_changes)


def test_a_reversing_journals_single_call_still_stamps_the_later_period():
    """A Consolidation Journal reversing P07's entry into P08 still calls
    record_data_change once (for P07 -- the earlier of the two), and that
    one real call marks P08 too."""
    site = _Site()
    on_submit, _on_cancel, calls, site = _hook_on_real_gate(site, period=(2025, 7))
    doc = _hook_doc("Consolidation Journal", "CJ-1",
                     reverse_fiscal_year=2025, reverse_fiscal_period=8)
    on_submit(doc)
    assert len(calls) == 1, calls
    assert calls[0][0][:2] == (2025, 7), calls
    for name in ("RUN-7", "RUN-8"):
        assert _run_rec(site, name)["signoff_status"] == "Re-sign Needed", name


def test_on_cancel_also_calls_the_real_record_data_change_exactly_once():
    site = _five_period_site()
    _on_submit, on_cancel, calls, site = _hook_on_real_gate(site, period=(2025, 7))
    on_cancel(_hook_doc("Group Exchange Rate", "GER-1"))
    assert len(calls) == 1, calls
    assert calls[0][0][2] == "Group Exchange Rate GER-1 cancelled", calls


# --- #305 R41k (review-w4-server.md S2 effect 3; coordinator call (a),  ----
# completes S2): record_data_change carries the change onto every LATER
# Regular period's OWN row too -- not only voiding its already-signed run
# (_mark_latest_signed, above, only ever touches signed runs). An UNSIGNED
# later period's own data_change_problem (A66) check -- the rule
# `_action` applies before offering "run_checks"/"rerun" -- needs its OWN
# row's data_change fields to see an earlier period's change; voiding a
# run that was never signed in the first place does nothing for it.

def _pure_signoff_model():
    """signoff_model.py imports nothing from frappe (its own header
    comment): load it once by path -- the same file `_load` loads for the
    stubbed gate -- to call `data_change_problem` (A66) directly, with no
    stubbing needed."""
    spec = importlib.util.spec_from_file_location(
        "signoff_model_for_r41k", SIGNOFF_MODEL_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_data_change_carries_onto_every_later_regular_period_row():
    """P07 change -> P08-P12 each carry it (``_Site``'s default declares
    P01-P12; P12 has no Assertion Run at all, let alone a signed one, yet
    still carries the text: the stamp follows every declared later Regular
    period, not only the signed ones ``_mark_latest_signed`` reaches)."""
    site = _Site()
    site.records["Assertion Run"].append(_run("RUN-9", 2025, 9, signoff="Not Signed Off"))
    text = "TB TBS-ZZA-2025-7 cancelled"
    marked = _record(site, 2025, 7, text=text)
    assert "RUN-9" not in marked, marked  # unsigned: never in _mark_latest_signed's reach
    carried = "Balance carried from FY2025 P07: %s" % text
    for fp in range(8, 13):
        dc = _call(site, "data_change", 2025, fp)
        assert dc == {"data_changed_at": CHANGED_AT, "data_changed_by": CHANGED_BY,
                      "data_change": carried}, (fp, dc)
    # P07's own row keeps the real text, never the carried wording.
    assert _call(site, "data_change", 2025, 7)["data_change"] == text


def test_an_unsigned_later_period_with_a_stale_run_is_blocked_by_a66():
    """The carried stamp above feeds straight into signoff_model's own A66
    rule: a P09 check run that STARTED before the carried change is
    blocked, naming the carried text -- even though P09 was never signed,
    so _mark_latest_signed never touched it. A run that started AFTER the
    change is not blocked."""
    site = _Site()
    text = "TB TBS-ZZA-2025-7 cancelled"
    _record(site, 2025, 7, text=text)
    change = _call(site, "data_change", 2025, 9)
    data_change_problem = _pure_signoff_model().data_change_problem
    problem = data_change_problem(CHANGED_AT - timedelta(minutes=5), change)
    assert problem is not None, change
    assert problem["code"] == "started_before_change", problem
    assert "Balance carried from FY2025 P07" in problem["what"], problem
    assert text in problem["what"], problem
    assert data_change_problem(CHANGED_AT + timedelta(minutes=5), change) is None


def test_a_later_periods_own_newer_change_is_never_overwritten_by_a_carried_one():
    """record_data_change's docstring: write the carried text only when it
    is NEWER than the row's own data_changed_at -- a later period's own,
    newer change (its own data, not merely a carried balance) survives an
    earlier period's carried stamp. A period with no change of its own
    still carries it."""
    site = _Site()
    newer_at = CHANGED_AT + timedelta(days=1)
    own_change = {"data_changed_at": newer_at, "data_changed_by": "other@example.com",
                  "data_change": "TB TBS-ZZB-2025-10 cancelled"}
    site.data_changes[(2025, 10)] = dict(own_change)
    _record(site, 2025, 7, text="TB TBS-ZZA-2025-7 cancelled")
    assert _call(site, "data_change", 2025, 10) == own_change, (
        _call(site, "data_change", 2025, 10))
    assert _call(site, "data_change", 2025, 11)["data_change"] == (
        "Balance carried from FY2025 P07: TB TBS-ZZA-2025-7 cancelled")


def test_the_period_json_has_the_three_read_only_fields_in_the_close_section():
    import json
    with open(PERIOD_JSON) as f:
        meta = json.load(f)
    fields = {f["fieldname"]: f for f in meta["fields"]}
    types_ = {"data_changed_at": ("Datetime", None), "data_changed_by": ("Link", "User"),
              "data_change": ("Small Text", None)}
    for name, (fieldtype, options) in types_.items():
        assert name in fields, "EPM Fiscal Year Period has no %s" % name
        assert fields[name]["fieldtype"] == fieldtype, fields[name]
        assert fields[name].get("options") == options, fields[name]
        assert fields[name].get("read_only") == 1, "%s must be read_only" % name
    order = meta["field_order"]
    assert order[order.index("closed_on") + 1:order.index("closed_on") + 4] == list(
        CHANGE_FIELDS), order
    assert [f["fieldname"] for f in meta["fields"]] == order, "fields not in field_order order"


# --- E205b (#289, #305-W2-2): a submitted TB with no ownership blocks sign-off --

def _unowned_site(ownership=None, tb=None):
    """_Site plus ZZX: an Active leaf with ``ownership`` (None: none at all)
    and ``tb`` (default: a submitted P09 TB)."""
    site = _Site()
    site.records["Entity"] += [_entity("ZZX")]
    if ownership is not None:
        site.records["Ownership Period"] += [ownership]
    site.records["Trial Balance Submission"] += [tb if tb is not None else _rec("ZZX", 2025, 9)]
    return site


def _unowned_gaps(problems):
    return [g for g in problems["config_gaps"] if g["code"] == "tb_without_ownership"]


def test_a_submitted_tb_with_no_ownership_is_a_config_gap():
    site = _unowned_site()
    problems = _call(site, "sign_off_problems", 2025, 9)
    gaps = _unowned_gaps(problems)
    assert len(gaps) == 1, problems["config_gaps"]
    assert gaps[0]["entities"] == ["ZZX"], gaps[0]
    # ZZX is not in scope, so ZZA/ZZB completeness is unchanged
    assert problems["completeness"] is None, problems["completeness"]


def test_a_submitted_tb_with_no_ownership_blocks_the_sign_off():
    message = _blocked(_unowned_site())
    for part in ("ZZX", "Ownership Period"):
        assert part in message, (part, message)


def test_ownership_ended_before_the_period_start_is_the_gap():
    site = _unowned_site(ownership=_owner("ZZX", end=date(2025, 8, 31)))
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert [g["entities"] for g in _unowned_gaps(problems)] == [["ZZX"]], problems["config_gaps"]
    assert problems["completeness"] is None, problems["completeness"]


def test_the_gap_follows_the_policy_gaps():
    site = _unowned_site()
    site.settings["self_approval"] = ""
    codes = [g["code"] for g in _call(site, "sign_off_problems", 2025, 9)["config_gaps"]]
    assert codes == ["self_approval_undeclared", "tb_without_ownership"], codes


def test_a_draft_tb_with_no_ownership_is_no_gap():
    site = _unowned_site(tb=_rec("ZZX", 2025, 9, docstatus=0))
    assert _call(site, "sign_off_problems", 2025, 9) == {
        "config_gaps": [], "order": None, "completeness": None}


def test_a_tb_for_another_period_is_no_gap():
    site = _unowned_site(tb=_rec("ZZX", 2025, 8))
    assert _call(site, "sign_off_problems", 2025, 9) == {
        "config_gaps": [], "order": None, "completeness": None}


def test_covered_ownership_is_no_gap_and_completeness_is_the_usual_rule():
    site = _unowned_site(ownership=_owner("ZZX"))
    assert _call(site, "sign_off_problems", 2025, 9) == {
        "config_gaps": [], "order": None, "completeness": None}
    # in scope with no TB: it is missing, not unowned
    site.records["Trial Balance Submission"].pop()
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert problems["config_gaps"] == [], problems["config_gaps"]
    assert problems["completeness"]["missing"] == ["ZZX"], problems["completeness"]


def test_the_period_trial_balances_are_read_once():
    site = _unowned_site()
    _call(site, "sign_off_problems", 2025, 9)
    tb_reads = [c for c in site.get_all_calls if c[0] == "Trial Balance Submission"]
    assert len(tb_reads) == 1, tb_reads


# --- C18t: the loader carries a stub konsol.close.ic_api, for C19 ----------


def test_the_ic_api_stub_is_installed():
    site = _Site()
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert not any(g["code"] == "ic_tolerance_undeclared" for g in problems["config_gaps"]), \
        problems["config_gaps"]


# --- C19: the undeclared tolerance blocks sign-off; intercompany(fy, fp) ----

def test_an_undeclared_tolerance_blocks_sign_off():
    site = _Site()
    site.ic_tolerance_gap = {
        "code": "ic_tolerance_undeclared", "groups": ["ZZG"],
        "message": "Declare the intercompany difference tolerance for ZZG.",
    }
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert [g["code"] for g in problems["config_gaps"]] == ["ic_tolerance_undeclared"]
    message = _blocked(site)
    assert "Declare the intercompany difference tolerance for ZZG." in message, message


def test_the_tolerance_gap_follows_both_policy_gaps():
    site = _Site()
    site.settings["self_approval"] = ""
    site.ic_tolerance_gap = {
        "code": "ic_tolerance_undeclared", "groups": ["ZZG"], "message": "<m>",
    }
    codes = [g["code"] for g in _call(site, "sign_off_problems", 2025, 9)["config_gaps"]]
    assert codes == ["self_approval_undeclared", "ic_tolerance_undeclared"], codes


def test_no_tolerance_gap_leaves_the_gate_as_before():
    site = _Site()
    assert site.ic_tolerance_gap is None
    assert _call(site, "sign_off_problems", 2025, 9) == {
        "config_gaps": [], "order": None, "completeness": None}


def test_sign_off_problems_never_reads_the_warehouse():
    # Failure path: a gate check costs no ClickHouse read — sign_off_problems
    # only calls tolerance_gap() (MariaDB via C05), never signoff_summary.
    site = _Site()
    site.ic_tolerance_gap = {"code": "ic_tolerance_undeclared", "groups": ["ZZG"], "message": "<m>"}
    _call(site, "sign_off_problems", 2025, 9)
    assert site.ic_calls == [], site.ic_calls


def test_intercompany_returns_the_stubbed_summary_and_records_the_call():
    site = _Site()
    site.ic_summary = {"state": "checked", "message": None,
                       "counts": {"pairs": 3, "over_tolerance": 1}, "sent_back_open": 1}
    result = _call(site, "intercompany", 2025, 10)
    assert result == site.ic_summary
    assert site.ic_calls == [(2025, 10)], site.ic_calls


# --- N43t: the stub site declares the two statement accounts, for N45 -------


def test_statement_accounts_are_declared_on_the_stub_site():
    site = _Site()
    assert site.settings["statement_cta_account"] == "3300"
    assert site.settings["statement_result_account"] == "3100"
    accounts = {row["name"]: row for row in site.records["Main Account"]}
    assert set(accounts) == {"3300", "3100"}
    for code in ("3300", "3100"):
        assert accounts[code]["is_group"] == 0, code
        assert accounts[code]["status"] == "Published", code
        assert accounts[code]["statement_section"] == "Balance Sheet", code


# --- N45: an undeclared or invalid statement account is a setup gap --------


def test_undeclared_statement_accounts_block_sign_off():
    site = _Site()
    site.settings["statement_cta_account"] = ""
    site.settings["statement_result_account"] = ""
    problems = _call(site, "sign_off_problems", 2025, 9)
    codes = [g["code"] for g in problems["config_gaps"]]
    assert codes == ["statement_accounts_undeclared"], codes
    # N45b: the Main Account read now always runs once (not "only when a
    # code is set") because the BS heading-side check needs the whole
    # chart whatever the CTA/result state is.
    main_account_reads = [c for c in site.get_all_calls if c[0] == "Main Account"]
    assert len(main_account_reads) == 1, main_account_reads
    message = _blocked(site)
    assert "Declare the CTA account in Close Settings" in message, message
    assert "Declare the current-year result account in Close Settings" in message, message


def test_declared_valid_statement_accounts_are_no_gap_and_read_main_account_once():
    site = _Site()
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert not any(g["code"] == "statement_accounts_undeclared" for g in problems["config_gaps"]), \
        problems["config_gaps"]
    main_account_reads = [c for c in site.get_all_calls if c[0] == "Main Account"]
    assert len(main_account_reads) == 1, main_account_reads


def test_an_invalid_statement_account_is_a_gap_too():
    # N41's rule, not "set means declared": a heading cannot hold the CTA
    # even though it is set.
    site = _Site()
    site.records["Main Account"] = [
        {"name": "3300", "is_group": 1, "status": "Published",
         "statement_section": "Balance Sheet", "account_name": "EQUITY"},
        {"name": "3100", "is_group": 0, "status": "Published",
         "statement_section": "Balance Sheet", "account_name": "Retained earnings",
         "parent_account": "3300"},
    ]
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert [g["code"] for g in problems["config_gaps"]] == ["statement_accounts_undeclared"], \
        problems["config_gaps"]
    message = _blocked(site)
    assert "3300 cannot hold the CTA: it is a heading." in message, message


def test_the_statement_gap_follows_the_policy_gaps_and_precedes_the_tolerance_gap():
    site = _Site()
    site.settings["self_approval"] = ""
    site.settings["statement_cta_account"] = ""
    site.settings["statement_result_account"] = ""
    site.ic_tolerance_gap = {
        "code": "ic_tolerance_undeclared", "groups": ["ZZG"], "message": "<m>",
    }
    codes = [g["code"] for g in _call(site, "sign_off_problems", 2025, 9)["config_gaps"]]
    assert codes == ["self_approval_undeclared", "statement_accounts_undeclared",
                      "ic_tolerance_undeclared"], codes


# --- N45b: an undeclared BS heading side is part of the statement setup gap --


def _bs_heading(code, name, side=None):
    row = {"name": code, "is_group": 1, "status": "Published",
           "statement_section": "Balance Sheet", "account_name": name}
    if side is not None:
        row["normal_balance"] = side
    return row


def _pl_heading(code, name, side=None):
    row = {"name": code, "is_group": 1, "status": "Published",
           "statement_section": "Profit and Loss", "account_name": name}
    if side is not None:
        row["normal_balance"] = side
    return row


def test_undeclared_bs_heading_sides_are_part_of_the_statement_gap():
    # Mirrors live: ASSETS/LIABILITIES/EQUITY all blank (coordinator call
    # W4-E22, #305-W4-2 2a-ii) — same rule as statement_model._bs_heading_sides,
    # never re-derived here.
    site = _Site()
    site.records["Main Account"] += [
        _bs_heading("1000", "ASSETS"),
        _bs_heading("2000", "LIABILITIES"),
        _bs_heading("3000", "EQUITY"),
    ]
    problems = _call(site, "sign_off_problems", 2025, 9)
    codes = [g["code"] for g in problems["config_gaps"]]
    assert codes == ["statement_accounts_undeclared"], codes
    message = _blocked(site)
    assert "statement_heading_side_undeclared" in message, message
    assert "1000" in message and "2000" in message and "3000" in message, message


def test_only_the_undeclared_bs_heading_sides_are_named():
    site = _Site()
    site.records["Main Account"] += [
        _bs_heading("1000", "ASSETS", side="Debit"),
        _bs_heading("2000", "LIABILITIES"),
        _bs_heading("3000", "EQUITY"),
    ]
    message = _blocked(site)
    assert "2000" in message and "3000" in message, message
    assert "1000" not in message, message


def test_pl_headings_are_never_checked_for_a_side():
    site = _Site()
    site.records["Main Account"] += [_pl_heading("9000", "INCOME")]
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert not any(g["code"] == "statement_accounts_undeclared" for g in problems["config_gaps"]), \
        problems["config_gaps"]


def test_declared_bs_heading_sides_are_no_gap():
    site = _Site()
    site.records["Main Account"] += [
        _bs_heading("1000", "ASSETS", side="Debit"),
        _bs_heading("2000", "LIABILITIES", side="Credit"),
        _bs_heading("3000", "EQUITY", side="Credit"),
    ]
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert not any(g["code"] == "statement_accounts_undeclared" for g in problems["config_gaps"]), \
        problems["config_gaps"]
    main_account_reads = [c for c in site.get_all_calls if c[0] == "Main Account"]
    assert len(main_account_reads) == 1, main_account_reads


def test_a_heading_side_problem_joins_an_existing_statement_accounts_problem():
    # Both problems land in the SAME gap (one statement setup gap, not two).
    site = _Site()
    site.settings["statement_cta_account"] = ""
    site.records["Main Account"] += [_bs_heading("2000", "LIABILITIES")]
    problems = _call(site, "sign_off_problems", 2025, 9)
    codes = [g["code"] for g in problems["config_gaps"]]
    assert codes == ["statement_accounts_undeclared"], codes
    message = _blocked(site)
    assert "Declare the CTA account in Close Settings" in message, message
    assert "statement_heading_side_undeclared" in message and "2000" in message, message


# --- #305 5.4 (W5-4): an IC Balance with no unrealised-profit rule blocks -----

def _real_rule_gap(balances, rules=()):
    """The real producer (ic_balance_model.rule_gap), loaded by path."""
    spec = importlib.util.spec_from_file_location("ic_balance_model_for_gate", IC_BALANCE_MODEL_PY)
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    return model.rule_gap(list(balances), list(rules))


_ZZ_BALANCE = {"name": "ICB-ZZA-ZZB-2025-P9", "selling_entity": "ZZA", "buying_entity": "ZZB",
               "fiscal_year": 2025, "fiscal_period": 9, "ic_sales_amount": 100.0,
               "ending_inventory_from_ic": 40.0, "docstatus": 0}


def test_a_draft_ic_balance_with_no_rule_blocks_sign_off_naming_the_pair():
    site = _Site()
    site.ic_rule_gap = _real_rule_gap([_ZZ_BALANCE])
    problems = _call(site, "sign_off_problems", 2025, 9)
    assert [g["code"] for g in problems["config_gaps"]] == ["ic_unrealized_profit_rule_undeclared"]
    assert site.ic_rule_gap_calls == [(2025, 9)]
    message = _blocked(site)
    assert "ZZA → ZZB" in message, message


def test_the_rule_gap_follows_the_tolerance_gap():
    site = _Site()
    site.ic_tolerance_gap = {"code": "ic_tolerance_undeclared", "groups": ["ZZG"], "message": "<m>"}
    site.ic_rule_gap = _real_rule_gap([_ZZ_BALANCE])
    codes = [g["code"] for g in _call(site, "sign_off_problems", 2025, 9)["config_gaps"]]
    assert codes == ["ic_tolerance_undeclared", "ic_unrealized_profit_rule_undeclared"], codes


def test_a_covered_pair_leaves_the_gate_clear():
    site = _Site()
    rule = {"rule_id": "R", "rule_type": "unrealized_profit", "margin_pct": 10,
            "debit_entity_pattern": "*", "credit_entity_pattern": "*"}
    site.ic_rule_gap = _real_rule_gap([_ZZ_BALANCE], [rule])
    assert site.ic_rule_gap is None
    assert _call(site, "sign_off_problems", 2025, 9) == {
        "config_gaps": [], "order": None, "completeness": None}
    assert site.ic_rule_gap_calls == [(2025, 9)]


# --- W5-2 (story 8.4): the commentary threshold -----------------------------


def test_an_undeclared_commentary_threshold_blocks_sign_off_after_the_statement_gap():
    site = _Site()
    site.settings["commentary_threshold_amount"] = 0
    site.settings["statement_cta_account"] = ""
    site.ic_tolerance_gap = {"code": "ic_tolerance_undeclared", "groups": ["ZZG"], "message": "<m>"}
    codes = [g["code"] for g in _call(site, "sign_off_problems", 2025, 9)["config_gaps"]]
    assert codes == ["statement_accounts_undeclared", "commentary_threshold_undeclared",
                     "ic_tolerance_undeclared"], codes
    message = _blocked(site)
    assert "Declare the commentary threshold in Close Settings" in message, message


def test_both_threshold_values_without_a_rule_is_the_gap():
    site = _Site()
    site.settings["commentary_threshold_percent"] = 10
    codes = [g["code"] for g in _call(site, "sign_off_problems", 2025, 9)["config_gaps"]]
    assert codes == ["commentary_threshold_undeclared"], codes


def test_a_declared_threshold_is_no_gap_and_reads_no_statement():
    site = _Site()
    assert _call(site, "sign_off_problems", 2025, 9)["config_gaps"] == []
    assert site.commentary_calls == []


def test_commentary_threshold_reads_close_settings_through_close_policy_model():
    site = _Site()
    site.settings.update(commentary_threshold_amount=5000, commentary_threshold_percent=10,
                         commentary_threshold_combine="Both are exceeded")
    assert _call(site, "commentary_threshold") == {
        "threshold": {"amount": 5000.0, "percent": 10.0, "combine": "Both are exceeded"},
        "gap": None}


def test_commentary_gap_is_the_threshold_gap_or_none():
    site = _Site()
    assert _call(site, "commentary_gap") is None
    site.settings["commentary_threshold_amount"] = 0
    assert _call(site, "commentary_gap")["code"] == "commentary_threshold_undeclared"


def test_commentary_delegates_to_statement_api_signoff_commentary():
    site = _Site()
    result = _call(site, "commentary", 2025, 10)
    assert result["state"] == "checked"
    assert site.commentary_calls == [(2025, 10)]


# --- merge of 8.4 and 5.4: the full gap order --------------------------------


def test_statement_commentary_tolerance_and_rule_gaps_keep_their_order():
    site = _Site()
    site.settings["commentary_threshold_amount"] = 0
    site.settings["statement_cta_account"] = ""
    site.ic_tolerance_gap = {"code": "ic_tolerance_undeclared", "groups": ["ZZG"], "message": "<m>"}
    site.ic_rule_gap = _real_rule_gap([_ZZ_BALANCE])
    codes = [g["code"] for g in _call(site, "sign_off_problems", 2025, 9)["config_gaps"]]
    assert codes == ["statement_accounts_undeclared", "commentary_threshold_undeclared",
                     "ic_tolerance_undeclared", "ic_unrealized_profit_rule_undeclared"], codes
