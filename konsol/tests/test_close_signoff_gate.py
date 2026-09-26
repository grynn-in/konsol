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
from datetime import date, datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE_PY = os.path.join(APP_DIR, "close", "signoff_gate.py")
PERIOD_MODEL_PY = os.path.join(APP_DIR, "close", "period_model.py")
SIGNOFF_MODEL_PY = os.path.join(APP_DIR, "close", "signoff_model.py")

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
        self.settings = {"first_close_fiscal_year": 2025, "first_close_fiscal_period": 7}
        self.records = {
            "Entity": [_entity("ZZA"), _entity("ZZB", "Quarterly")],
            "Ownership Period": [_owner("ZZA"), _owner("ZZB")],
            "Trial Balance Submission": [_rec("ZZA", 2025, 9), _rec("ZZB", 2025, 9)],
            "TB Exception": [],
            "Assertion Run": [_run("RUN-7", 2025, 7), _run("RUN-8", 2025, 8)],
            "Connector": [],
            "Connector Legal Entity": [],
        }
        self.whitelisted = set()
        self.get_all_calls = []
        self.signed_off_calls = []
        #: (run name, writer in force, fields changed) per Assertion Run save (A31).
        self.saves = []
        #: A63: the period rows' data-change fields, and every write to them.
        self.data_changes = {}
        self.period_writes = []


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
    close.period_model, close.signoff_model = period_model, signoff_model
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
        assert [g["code"] for g in problems["config_gaps"]] == ["first_close_undeclared"], settings
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


def test_one_message_lists_every_problem():
    site = _Site()
    site.settings = {"first_close_fiscal_year": 2025, "first_close_fiscal_period": 7}
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
    assert site.period_writes == [
        ("ROW-2025-8", {"data_changed_at": CHANGED_AT, "data_changed_by": CHANGED_BY,
                        "data_change": CHANGED_TEXT}, False)], site.period_writes


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
