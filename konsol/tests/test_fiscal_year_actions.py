"""EPM Fiscal Year's form actions (konsol#189).

Generate Periods replaces the period table with the rows
konsol.fiscal_patterns_model generates for the year's pattern, then saves.
Only EPM Admin or System Manager may run it, and only on an Open year no
document uses yet.

Close / Lock / Reopen Period each move one period row, as
konsol.fiscal_status_model.transition_problem allows; leaving Open checks the
period's group exchange rates first.

Close / Lock Year move the year and every looser row, all or nothing: one
failing rate check refuses the whole action, naming every failing period.
Reopen Year leaves the rows as they are. Loaded against a stub frappe, as in
test_fiscal_year_controller.py; the stubs stay installed during calls."""
import ast
import contextlib
import copy
import importlib.util
import os
import re
import sys
import types
from datetime import date, datetime

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTROLLER = os.path.join(APP_DIR, "epm", "doctype", "epm_fiscal_year", "epm_fiscal_year.py")
FORM_JS = os.path.join(APP_DIR, "epm", "doctype", "epm_fiscal_year", "epm_fiscal_year.js")
PURE = {
    "konsol.fiscal_structure_model": os.path.join(APP_DIR, "fiscal_structure_model.py"),
    "konsol.fiscal_status_model": os.path.join(APP_DIR, "fiscal_status_model.py"),
    "konsol.fiscal_patterns_model": os.path.join(APP_DIR, "fiscal_patterns_model.py"),
}
#: What the stub frappe.utils.now_datetime() returns.
NOW = datetime(2026, 9, 14, 10, 30)


class Thrown(Exception):
    """frappe.throw was called."""


class PermissionRefused(Exception):
    """frappe.throw was called with frappe.PermissionError."""


def _getdate(v=None):
    if v is None or isinstance(v, date):
        return v
    return date.fromisoformat(str(v))


def _get_datetime(v=None):
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v))


@contextlib.contextmanager
def _load():
    """Yield the controller module with frappe stubbed; the stubs stay
    installed until the block ends, so calls made inside it see them too."""
    class _dict(dict):
        def __getattr__(self, name):
            return self.get(name)

        def __setattr__(self, name, value):
            self[name] = value

    class Document:
        def __init__(self, **kwargs):
            self.flags = _dict()
            self.saves = 0
            self.__dict__.update(kwargs)

        def __getattr__(self, name):   # an unset field reads as None, as in Frappe
            if name.startswith("__"):
                raise AttributeError(name)
            return None

        def get(self, name, default=None):
            return self.__dict__.get(name, default)

        def set(self, name, value):
            self.__dict__[name] = list(value) if isinstance(value, list) else value

        def append(self, name, row):
            child = types.SimpleNamespace(**row)
            self.__dict__.setdefault(name, [])
            if self.__dict__[name] is None:
                self.__dict__[name] = []
            self.__dict__[name].append(child)
            return child

        def get_doc_before_save(self):
            """The saved version, which stands for the database row."""
            return self.__dict__.get("_before_save")

        def reload(self):
            """As Frappe's reload: every field and row comes back from the
            saved version (the database); anything else the document held
            is dropped. flags stay, as in Frappe."""
            frappe.events.append(("reload", self.name))
            saved = self.__dict__.get("_before_save")
            keep = {k: self.__dict__[k] for k in _NOT_FIELDS if k in self.__dict__}
            self.__dict__.clear()
            self.__dict__.update(keep)
            self.__dict__.update(_fields(saved))
            return self

        def save(self, *args, **kwargs):
            """Runs validate(), as Frappe's save does, counts the save and
            writes the document back as the saved version."""
            self.validate()
            self.saves += 1
            self._before_save = type(self)(**_fields(self))
            return self

    _NOT_FIELDS = ("flags", "saves", "_before_save")

    def _fields(doc):
        return {k: copy.deepcopy(v) for k, v in doc.__dict__.items() if k not in _NOT_FIELDS}

    def throw(msg, exc=None, *args, **kwargs):
        raise (exc or Thrown)(msg)

    def whitelist(*args, **kwargs):
        if args and callable(args[0]) and not kwargs:
            return args[0]
        return lambda fn: fn

    mods = {name: types.ModuleType(name) for name in (
        "frappe", "frappe.model", "frappe.model.document", "frappe.utils", "konsol",
        "konsol.fiscal_calendar", "konsol.group_rates")}
    calendar = mods["konsol.fiscal_calendar"]
    calendar.used = set()
    calendar.periods_in_use = lambda fiscal_year: set(calendar.used)
    mods["konsol"].fiscal_calendar = calendar

    rates = mods["konsol.group_rates"]
    rates.calls = []
    rates.fail = None
    rates.fail_for = {}     # a period number -> the refusal for that period only

    def assert_rates_complete(fiscal_year, fiscal_period):
        rates.calls.append((fiscal_year, fiscal_period))
        frappe.events.append(("rates", fiscal_year, fiscal_period))
        if rates.fail:
            raise Thrown(rates.fail)
        if fiscal_period in rates.fail_for:
            raise Thrown(rates.fail_for[fiscal_period])

    rates.assert_rates_complete = assert_rates_complete
    mods["konsol"].group_rates = rates

    frappe = mods["frappe"]
    #: The order of lock queries, reloads and rate checks, as (kind, ...).
    frappe.events = []

    def sql(query, values=None, *args, **kwargs):
        frappe.events.append(("sql", " ".join(query.split()), values))
        return []

    frappe.db = types.SimpleNamespace(sql=sql)
    frappe.session = _dict(user="closer@example.com")
    frappe.roles = ["EPM Admin"]
    frappe.get_roles = lambda *a, **k: list(frappe.roles)
    frappe._ = lambda s: s
    frappe._dict = _dict
    frappe.throw = throw
    frappe.whitelist = whitelist
    frappe.ValidationError = Thrown     # what frappe.throw raises by default
    frappe.PermissionError = PermissionRefused
    frappe.utils = mods["frappe.utils"]
    mods["frappe.model.document"].Document = Document
    mods["frappe.utils"].getdate = _getdate
    mods["frappe.utils"].get_datetime = _get_datetime
    mods["frappe.utils"].cint = lambda v: int(v or 0)
    mods["frappe.utils"].now_datetime = lambda: NOW
    mods["konsol"].__path__ = []

    saved = {name: sys.modules.get(name) for name in (*mods, *PURE)}
    sys.modules.update(mods)
    try:
        for name, path in PURE.items():
            spec = importlib.util.spec_from_file_location(name, path)
            pure = importlib.util.module_from_spec(spec)
            sys.modules[name] = pure
            spec.loader.exec_module(pure)
            setattr(mods["konsol"], name.rsplit(".", 1)[1], pure)

        spec = importlib.util.spec_from_file_location("epm_fiscal_year_actions_under_test", CONTROLLER)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, old in saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old


def _roles(roles):
    sys.modules["frappe"].roles = list(roles)


def _in_use(periods):
    sys.modules["konsol.fiscal_calendar"].used = set(periods)


def _old_rows():
    """A hand-entered table the action replaces: P01 only, wrong dates."""
    return [types.SimpleNamespace(fiscal_period=1, period_code="P01", period_label="Old",
                                  period_type="Regular", start_date="2025-01-01",
                                  end_date="2025-06-30", status="Open")]


def _year(module, status="Open", pattern="Monthly (12)", rows=None):
    """A saved 2025 year; its saved version has the same fields and rows."""
    def make():
        return module.EPMFiscalYear(
            doctype="EPM Fiscal Year", name="2025", fiscal_year=2025,
            start_date="2025-01-01", end_date="2025-12-31", status=status,
            period_pattern=pattern, include_opening_period=1, include_closing_period=1,
            periods=rows if rows is not None else _old_rows())
    doc = make()
    doc._before_save = make()
    return doc


def _generate(doc, exc=Thrown):
    """The message of the refusal `exc`, or None when generate_periods passes."""
    try:
        doc.generate_periods()
    except exc as e:
        return str(e)
    return None


def test_generate_replaces_rows_for_open_unused_year():
    with _load() as module:
        doc = _year(module)
        assert _generate(doc) is None
        codes = [r.period_code for r in doc.periods]
        assert codes == ["OPN"] + [f"P{m:02d}" for m in range(1, 13)] + ["CLS"], codes
        assert [r.fiscal_period for r in doc.periods] == list(range(0, 14))
        p01, p12 = doc.periods[1], doc.periods[12]
        assert str(p01.start_date) == "2025-01-01" and str(p01.end_date) == "2025-01-31"
        assert str(p12.start_date) == "2025-12-01" and str(p12.end_date) == "2025-12-31"
        assert p01.period_label == "Jan 2025" and p01.quarter == "Q1"
        assert p12.quarter == "Q4"
        assert doc.periods[0].period_type == "Opening"
        assert doc.periods[-1].period_type == "Closing"
        assert all(r.status == "Open" for r in doc.periods)
        assert doc.saves == 1, "the generated table was not saved"


def test_generate_refused_when_in_use():
    with _load() as module:
        _in_use(range(1, 13))
        doc = _year(module)
        msg = _generate(doc)
        assert msg is not None, "periods were regenerated on a year documents use"
        assert "documents use 12 of its periods" in msg, msg
        assert "generate only on an unused year" in msg, msg
        assert doc.saves == 0
        assert [r.period_code for r in doc.periods] == ["P01"]


def test_generate_refused_when_closed():
    with _load() as module:
        rows = _old_rows()
        rows[0].status = "Closed"
        doc = _year(module, status="Closed", rows=rows)
        msg = _generate(doc)
        assert msg is not None, "periods were regenerated on a Closed year"
        assert "FY2025 is Closed" in msg, msg
        assert doc.saves == 0


def test_generate_refused_for_analyst():
    with _load() as module:
        _roles(["EPM Analyst"])
        doc = _year(module)
        msg = _generate(doc, PermissionRefused)
        assert msg is not None, "an EPM Analyst generated periods"
        assert doc.saves == 0

        _roles(["System Manager"])
        assert _generate(_year(module)) is None


def test_generate_custom_is_refused_clearly():
    with _load() as module:
        doc = _year(module, pattern="Custom")
        msg = _generate(doc)
        assert msg is not None, "a Custom year was generated"
        assert "Custom periods are entered by hand" in msg, msg
        assert doc.saves == 0
        assert [r.period_code for r in doc.periods] == ["P01"]


def _assert_whitelisted_post(name):
    with open(CONTROLLER) as f:
        tree = ast.parse(f.read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "EPMFiscalYear")
    method = next((n for n in cls.body
                   if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert method is not None, f"EPMFiscalYear has no {name} method"
    found = False
    for dec in method.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "whitelist"
                and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "frappe"):
            for kw in dec.keywords:
                if kw.arg == "methods":
                    found = ast.literal_eval(kw.value) == ["POST"]
    assert found, f"{name} is not @frappe.whitelist(methods=['POST'])"


def test_whitelisted_post_only():
    _assert_whitelisted_post("generate_periods")


#: The whitelisted POST doc methods the form's buttons must all reach
#: (konsol#189): Generate Periods plus Close/Lock/Reopen Period and Year.
REQUIRED_FORM_METHODS = frozenset({
    "generate_periods", "close_period", "lock_period", "reopen_period",
    "close_year", "lock_year", "reopen_year",
})


def test_form_buttons_call_whitelisted_post_methods():
    """Every `frm.call({method: "..."})` in the form script names a real
    EPMFiscalYear method decorated @frappe.whitelist(methods=["POST"]), and
    the 7 status/generate actions are all wired up from the JS."""
    with open(FORM_JS) as f:
        js = f.read()

    called = set(re.findall(r'method:\s*"(\w+)"', js))
    assert called, "no frm.call({method: \"...\"}) found in the form script"

    for name in called:
        _assert_whitelisted_post(name)

    missing = REQUIRED_FORM_METHODS - called
    assert not missing, f"form script does not call: {sorted(missing)}"


# -- Close / Lock / Reopen Period ---------------------------------------------------


def _rates():
    return sys.modules["konsol.group_rates"]


def _valid_year(module, status="Open", row_status=None, closing_note=None):
    """A saved 2025 Monthly year with a valid generated table (OPN, P01..P12,
    CLS). `row_status` maps a period number to its status; the rest take the
    year's status. The saved version is built separately, so changing a row
    of the document leaves the saved row as it was."""
    fpm = sys.modules["konsol.fiscal_patterns_model"]
    row_status = row_status or {}

    def rows():
        out = []
        for r in fpm.generate_periods("Monthly (12)", date(2025, 1, 1), date(2025, 12, 31)):
            closed = row_status.get(r["period"], status) != "Open"
            out.append(types.SimpleNamespace(
                fiscal_period=r["period"], period_code=r["code"], period_label=r["label"],
                period_type=r["type"], start_date=r["start_date"], end_date=r["end_date"],
                quarter=r["quarter"], status=row_status.get(r["period"], status),
                closed_by="earlier@example.com" if closed else None,
                closed_on=datetime(2026, 1, 5, 9, 0) if closed else None))
        return out

    def make():
        return module.EPMFiscalYear(
            doctype="EPM Fiscal Year", name="2025", fiscal_year=2025,
            start_date="2025-01-01", end_date="2025-12-31", status=status,
            period_pattern="Monthly (12)", include_opening_period=1, include_closing_period=1,
            closing_note=closing_note, periods=rows())
    doc = make()
    doc._before_save = make()
    return doc


def _row(doc, period):
    return next(r for r in doc.periods if r.fiscal_period == period)


def _act(call, exc=Thrown):
    """(result, None) when `call` passes, else (None, the refusal's message)."""
    try:
        return call(), None
    except exc as e:
        return None, str(e)


def test_closing_a_period_checks_its_group_rates():
    with _load() as module:
        doc = _valid_year(module)
        result, err = _act(lambda: doc.close_period(3))
        assert err is None, err
        assert _rates().calls == [(2025, 3)], _rates().calls
        assert _row(doc, 3).status == "Closed"
        assert doc.saves == 1

        # Closed -> Locked has already passed the gate: not checked again.
        result, err = _act(lambda: doc.lock_period(3))
        assert err is None, err
        assert _rates().calls == [(2025, 3)], _rates().calls
        assert _row(doc, 3).status == "Locked"
        assert doc.saves == 2

        # Open -> Locked leaves Open, so it is checked.
        result, err = _act(lambda: doc.lock_period(4))
        assert err is None, err
        assert _rates().calls == [(2025, 3), (2025, 4)], _rates().calls

        # A gate failure refuses the close: nothing changes, nothing is saved.
        _rates().fail = "no approved group exchange rate for EUR → USD Closing"
        doc = _valid_year(module)
        result, err = _act(lambda: doc.close_period(5))
        assert err is not None and "no approved group exchange rate" in err, err
        assert _row(doc, 5).status == "Open"
        assert _row(doc, 5).closed_by is None and _row(doc, 5).closed_on is None
        assert doc.saves == 0


def test_close_stamps_and_notes():
    with _load() as module:
        doc = _valid_year(module, closing_note="FY opened.")
        result, err = _act(lambda: doc.close_period("3", note="Accruals booked"))
        assert err is None, err
        row = _row(doc, 3)
        assert row.status == "Closed"
        assert row.closed_by == "closer@example.com"
        assert row.closed_on == NOW
        assert doc.flags.konsol_status_action, "the save did not run as a status action"
        assert doc.closing_note.startswith("FY opened."), doc.closing_note
        last = doc.closing_note.splitlines()[-1]
        assert "P03" in last and "2026-09-14" in last and "Accruals booked" in last, last
        assert result["period_code"] == "P03" and result["status"] == "Closed", result
        # The year itself is untouched.
        assert doc.status == "Open" and doc.closed_by is None and doc.closed_on is None

        # No note: nothing appended.
        before_note = doc.closing_note
        result, err = _act(lambda: doc.lock_period(4))
        assert err is None, err
        assert doc.closing_note == before_note, doc.closing_note
        assert _row(doc, 4).closed_by == "closer@example.com"

        # An unknown period is named.
        saves = doc.saves
        result, err = _act(lambda: doc.close_period(99))
        assert err is not None and "99" in err, err
        assert doc.saves == saves


def test_reopen_needs_reason_and_open_year():
    with _load() as module:
        doc = _valid_year(module, row_status={3: "Closed"})
        for blank in (None, "", "   "):
            result, err = _act(lambda: doc.reopen_period(3, blank))
            assert err is not None, f"reopened with reason {blank!r}"
        assert _row(doc, 3).status == "Closed" and doc.saves == 0

        result, err = _act(lambda: doc.reopen_period(3, "Late invoice from supplier"))
        assert err is None, err
        row = _row(doc, 3)
        assert row.status == "Open"
        assert row.closed_by is None and row.closed_on is None
        last = doc.closing_note.splitlines()[-1]
        assert "P03" in last and "2026-09-14" in last and "Late invoice from supplier" in last, last
        assert result["period_code"] == "P03" and result["status"] == "Open", result
        assert doc.saves == 1
        assert _rates().calls == [], "reopening checked group rates"

        # A Closed year refuses reopening any of its periods.
        doc = _valid_year(module, status="Closed")
        result, err = _act(lambda: doc.reopen_period(3, "Late invoice"))
        assert err is not None and "Reopen the year first" in err, err
        assert _row(doc, 3).status == "Closed" and doc.saves == 0


def test_locked_reopen_needs_system_manager():
    with _load() as module:
        doc = _valid_year(module, row_status={3: "Locked"})
        result, err = _act(lambda: doc.reopen_period(3, "Audit adjustment"), PermissionRefused)
        assert err is not None, "an EPM Admin reopened a Locked period"
        assert "System Manager" in err, err
        assert _row(doc, 3).status == "Locked" and doc.saves == 0

        _roles(["System Manager"])
        result, err = _act(lambda: doc.reopen_period(3, "Audit adjustment"))
        assert err is None, err
        assert _row(doc, 3).status == "Open"
        assert _row(doc, 3).closed_by is None and _row(doc, 3).closed_on is None
        assert doc.saves == 1


def test_analyst_cannot_close():
    with _load() as module:
        _roles(["EPM Analyst"])
        doc = _valid_year(module, row_status={4: "Closed"})
        for call in (lambda: doc.close_period(3), lambda: doc.lock_period(3),
                     lambda: doc.lock_period(4), lambda: doc.reopen_period(4, "Because")):
            result, err = _act(call, PermissionRefused)
            assert err is not None, "an EPM Analyst changed a period's status"
        assert _row(doc, 3).status == "Open" and _row(doc, 4).status == "Closed"
        assert doc.saves == 0
        assert _rates().calls == [], "the rate gate ran for a refused Analyst"


def test_actions_are_whitelisted_post():
    for name in ("close_period", "lock_period", "reopen_period",
                 "close_year", "lock_year", "reopen_year"):
        _assert_whitelisted_post(name)


# -- Close / Lock / Reopen Year -----------------------------------------------------

ALL_PERIODS = list(range(0, 14))    # OPN, P01..P12, CLS


def _snapshot(doc):
    """Each row's (code, status, closed_by, closed_on)."""
    return [(r.period_code, r.status, r.closed_by, r.closed_on) for r in doc.periods]


def test_close_year_closes_rows():
    with _load() as module:
        doc = _valid_year(module, row_status={3: "Closed", 4: "Locked"}, closing_note="FY opened.")
        untouched = {3: (_row(doc, 3).closed_by, _row(doc, 3).closed_on),
                     4: (_row(doc, 4).closed_by, _row(doc, 4).closed_on)}
        result, err = _act(lambda: doc.close_year(note="Year-end close"))
        assert err is None, err

        moved = [p for p in ALL_PERIODS if p not in (3, 4)]
        assert _rates().calls == [(2025, p) for p in moved], _rates().calls
        for p in moved:
            row = _row(doc, p)
            assert row.status == "Closed", (p, row.status)
            assert row.closed_by == "closer@example.com" and row.closed_on == NOW, p
        # Rows already at least Closed keep their status and stamp.
        assert _row(doc, 3).status == "Closed" and _row(doc, 4).status == "Locked"
        for p, stamp in untouched.items():
            assert (_row(doc, p).closed_by, _row(doc, p).closed_on) == stamp, p

        assert doc.status == "Closed"
        assert doc.closed_by == "closer@example.com" and doc.closed_on == NOW
        assert doc.flags.konsol_status_action, "the save did not run as a status action"
        assert doc.saves == 1
        assert doc.closing_note.startswith("FY opened."), doc.closing_note
        last = doc.closing_note.splitlines()[-1]
        assert "FY2025" in last and "2026-09-14" in last and "Year-end close" in last, last
        assert result["status"] == "Closed", result

        # Same status: refused, like the period actions.
        result, err = _act(lambda: doc.close_year())
        assert err is not None and "already Closed" in err, err
        assert doc.saves == 1


def test_close_year_all_or_nothing_names_failures():
    with _load() as module:
        _rates().fail_for = {
            5: "Cannot close fiscal period 5 of FY2025: no approved group exchange rate for EUR → USD Closing",
            9: "Cannot close fiscal period 9 of FY2025: group EU has no reporting currency",
        }
        doc = _valid_year(module, closing_note="FY opened.")
        before = _snapshot(doc)
        result, err = _act(lambda: doc.close_year(note="Year-end close"))
        assert err is not None, "the year closed with two periods failing the rate check"
        for needle in ("P05", "P09", "EUR → USD Closing", "no reporting currency"):
            assert needle in err, (needle, err)
        # Every moving row was checked, so every failure is named at once.
        assert _rates().calls == [(2025, p) for p in ALL_PERIODS], _rates().calls
        assert doc.saves == 0
        assert _snapshot(doc) == before, "rows changed although the close was refused"
        assert doc.status == "Open" and doc.closed_by is None and doc.closed_on is None
        assert doc.closing_note == "FY opened.", doc.closing_note


def test_lock_year_locks_closed_rows_without_rate_check():
    with _load() as module:
        doc = _valid_year(module, status="Closed")
        result, err = _act(lambda: doc.lock_year())
        assert err is None, err
        assert _rates().calls == [], "Closed rows were rate-checked again on Lock Year"
        for r in doc.periods:
            assert r.status == "Locked", (r.period_code, r.status)
            assert r.closed_by == "closer@example.com" and r.closed_on == NOW, r.period_code
        assert doc.status == "Locked" and doc.closed_by == "closer@example.com"
        assert doc.closed_on == NOW and doc.saves == 1
        assert result["status"] == "Locked", result

        # From an Open year: the Open rows are checked, the Closed one is not.
        doc = _valid_year(module, row_status={3: "Closed"})
        _rates().calls.clear()
        result, err = _act(lambda: doc.lock_year())
        assert err is None, err
        assert _rates().calls == [(2025, p) for p in ALL_PERIODS if p != 3], _rates().calls
        assert all(r.status == "Locked" for r in doc.periods)


def test_reopen_year_keeps_rows():
    with _load() as module:
        doc = _valid_year(module, status="Closed", row_status={4: "Locked"})
        before = _snapshot(doc)
        for blank in (None, "", "   "):
            result, err = _act(lambda: doc.reopen_year(blank))
            assert err is not None, f"reopened the year with reason {blank!r}"
        assert doc.status == "Closed" and doc.saves == 0

        result, err = _act(lambda: doc.reopen_year("Auditor adjustment to FY2025"))
        assert err is None, err
        assert doc.status == "Open"
        assert doc.closed_by is None and doc.closed_on is None
        assert _snapshot(doc) == before, "Reopen Year changed the period rows"
        last = doc.closing_note.splitlines()[-1]
        assert "FY2025" in last and "2026-09-14" in last and "Auditor adjustment" in last, last
        assert doc.saves == 1 and doc.flags.konsol_status_action
        assert _rates().calls == [], "reopening checked group rates"
        assert result["status"] == "Open", result

        result, err = _act(lambda: doc.reopen_year("Again"))
        assert err is not None and "already Open" in err, err
        assert doc.saves == 1


def test_reopen_locked_year_sm_only():
    with _load() as module:
        doc = _valid_year(module, status="Locked")
        result, err = _act(lambda: doc.reopen_year("Restatement"), PermissionRefused)
        assert err is not None, "an EPM Admin reopened a Locked year"
        assert "System Manager" in err, err
        assert doc.status == "Locked" and doc.saves == 0

        _roles(["EPM Analyst"])
        result, err = _act(lambda: doc.close_year(), PermissionRefused)
        assert err is not None, "an EPM Analyst changed the year's status"

        _roles(["System Manager"])
        result, err = _act(lambda: doc.reopen_year("Restatement"))
        assert err is None, err
        assert doc.status == "Open" and doc.saves == 1
        assert all(r.status == "Locked" for r in doc.periods), "Reopen Year reopened rows"


# -- The actions act on the saved year, not the client's copy (PR #191 review 1) --------

FORGER = "forger@example.com"


def _forge(doc, status):
    """What a crafted run_doc_method `docs` can carry: every status field of
    the year and its rows set to `status`, stamped by someone else."""
    doc.status, doc.closed_by, doc.closed_on = status, FORGER, NOW
    for r in doc.periods:
        r.status, r.closed_by, r.closed_on = status, FORGER, NOW


def _saved_values(doc):
    """Every status field of the saved version (the database)."""
    saved = doc.get_doc_before_save()
    return [(saved.status, saved.closed_by, saved.closed_on)] + [
        (r.status, r.closed_by, r.closed_on) for r in saved.periods]


def test_actions_ignore_client_copy():
    with _load() as module:
        # The database holds a Locked year; the client says Open everywhere.
        doc = _valid_year(module, status="Locked")
        before = _saved_values(doc)
        _forge(doc, "Open")
        result, err = _act(lambda: doc.close_period(1), (Thrown, PermissionRefused))
        # Locked -> Closed needs System Manager: refused, on the saved status.
        assert err is not None, "close_period acted on the client's Open period"
        assert _saved_values(doc) == before, "the client's status edits were saved"

        # An Open year: the action acts on the saved rows; the client's
        # Locked year and forged stamps are never saved.
        doc = _valid_year(module)
        _forge(doc, "Locked")
        result, err = _act(lambda: doc.close_period(3))
        assert err is None, err
        saved = doc.get_doc_before_save()
        assert saved.status == "Open" and saved.closed_by is None and saved.closed_on is None
        for r in saved.periods:
            if r.fiscal_period == 3:
                assert (r.status, r.closed_by, r.closed_on) == ("Closed", "closer@example.com", NOW)
            else:
                assert (r.status, r.closed_by, r.closed_on) == ("Open", None, None), r.period_code
        assert FORGER not in repr(_saved_values(doc))

        # Year actions too: the client's Open copy of a Locked year can't be closed.
        doc = _valid_year(module, status="Locked")
        before = _saved_values(doc)
        _forge(doc, "Open")
        result, err = _act(lambda: doc.close_year(), (Thrown, PermissionRefused))
        assert err is not None, "close_year acted on the client's Open year"
        assert _saved_values(doc) == before


def test_generate_uses_saved_year():
    """Generate Periods reads the pattern and status from the saved year."""
    with _load() as module:
        doc = _year(module)
        doc.period_pattern = "Custom"       # client-side edit, never saved
        doc.status = "Locked"
        assert _generate(doc) is None
        assert [r.period_code for r in doc.get_doc_before_save().periods][:2] == ["OPN", "P01"]
        assert doc.get_doc_before_save().status == "Open"


def _first(events, kind, needle=""):
    return next(i for i, e in enumerate(events) if e[0] == kind and needle in str(e))


def test_rate_gate_runs_after_lock():
    with _load() as module:
        events = sys.modules["frappe"].events
        for call in (lambda d: d.close_period(3), lambda d: d.lock_period(3),
                     lambda d: d.close_year(), lambda d: d.lock_year()):
            doc = _valid_year(module)
            events.clear()
            result, err = _act(lambda: call(doc))
            assert err is None, err
            lock = next((i for i, e in enumerate(events) if e[0] == "sql"
                         and "`tabEPM Fiscal Year`" in e[1] and "FOR UPDATE" in e[1]), None)
            assert lock is not None, f"no FOR UPDATE on the year row: {events}"
            assert events[lock][2] in ("2025", ("2025",)), events[lock]
            reload_at = _first(events, "reload")
            rates_at = _first(events, "rates")
            assert lock < reload_at < rates_at, events


# -- The form buttons are role-shown, mirroring the server (PR #191 review finding 10) --------


def _balanced(text, open_idx, open_ch, close_ch):
    """Index of the char that closes the bracket opened at `open_idx`."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == open_ch:
            depth += 1
        elif text[i] == close_ch:
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"no matching {close_ch!r} for {open_ch!r} at {open_idx}")


#: Every button label the form offers, in the order they appear in the file.
BUTTON_LABELS = (
    "Generate Periods", "Close Year", "Lock Year", "Reopen Year",
    "Close Period", "Lock Period", "Reopen Period",
)

#: Labels whose server-side role set depends on the row/year's own current
#: status (Reopen can leave Closed, needing EPM Admin or System Manager, or
#: leave Locked, needing System Manager alone) rather than one fixed set.
DUAL_BRANCH_LABELS = ("Reopen Year", "Reopen Period")

_ROLE_DEF_RE = re.compile(
    r'const\s+\w+\s*=\s*(?:frappe\.user\.has_role\([^)]*\)|\[[^\]]*\])\s*;'
)


def _button_guards(js):
    """{label: guard_text} for every `if (...) { ...add_custom_button(label)... }`
    block in `js`, in file order. `guard_text` is that if's own condition plus
    whatever sits between it and the previous matched block (a button whose
    role check lives in a `.filter(...)` feeding the if, not in the if itself,
    e.g. Reopen Period). Role/has_role *definitions* are stripped out, so only
    usages remain."""
    guards = {}
    prev_end = 0
    for m in re.finditer(r'if\s*\(', js):
        start = m.end() - 1
        try:
            cond_end = _balanced(js, start, "(", ")")
            brace = js.index("{", cond_end)
            body_end = _balanced(js, brace, "{", "}")
        except ValueError:
            continue
        body = js[brace:body_end + 1]
        label = next((l for l in BUTTON_LABELS if f'__("{l}")' in body), None)
        if label is None:
            continue
        between = _ROLE_DEF_RE.sub("", js[prev_end:start])
        cond_text = js[start + 1:cond_end]
        guards[label] = between + " " + cond_text
        prev_end = body_end + 1
    return guards


def _role_vars(js):
    """{var_name: frozenset(roles)} for every `const X = [...]` role array and
    `const y = frappe.user.has_role(<expr>)` boolean, resolving a reference to
    one of those arrays as well as an inline string or array literal."""
    arrays = {}
    for m in re.finditer(r'const\s+(\w+)\s*=\s*\[([^\]]*)\]\s*;', js):
        roles = frozenset(re.findall(r'"([^"]+)"', m.group(2)))
        if roles:
            arrays[m.group(1)] = roles

    bools = {}
    for m in re.finditer(r'const\s+(\w+)\s*=\s*frappe\.user\.has_role\(([^)]*)\)\s*;', js):
        arg = m.group(2).strip()
        bools[m.group(1)] = arrays.get(arg, frozenset(re.findall(r'"([^"]+)"', arg)))
    return bools


def _roles_used_in(text, role_vars):
    return {name: roles for name, roles in role_vars.items()
            if re.search(rf'\b{re.escape(name)}\b', text)}


def test_buttons_role_gated():
    """Every status/generate button is shown only to a role the server would
    actually accept for that action (PR #191 review finding 10): the JS's
    has_role checks must name the same roles as the controller's
    _GENERATE_ROLES and fiscal_status_model's transition role matrix."""
    with _load() as module:
        generate_roles = frozenset(module._GENERATE_ROLES)
        fsm = sys.modules["konsol.fiscal_status_model"]
        transitions = fsm._TRANSITIONS

        lock_from_open = frozenset(transitions[(fsm.OPEN, fsm.LOCKED)][0])
        lock_from_closed = frozenset(transitions[(fsm.CLOSED, fsm.LOCKED)][0])
        assert lock_from_open == lock_from_closed, (
            "Lock Year/Period shows one button regardless of the row's current "
            "status, so the server must require the same roles from either")

        expected_single = {
            "Generate Periods": generate_roles,
            "Close Year": frozenset(transitions[(fsm.OPEN, fsm.CLOSED)][0]),
            "Lock Year": lock_from_open,
            "Close Period": frozenset(transitions[(fsm.OPEN, fsm.CLOSED)][0]),
            "Lock Period": lock_from_open,
        }
        expected_dual = {
            "Closed": frozenset(transitions[(fsm.CLOSED, fsm.OPEN)][0]),
            "Locked": frozenset(transitions[(fsm.LOCKED, fsm.OPEN)][0]),
        }

    with open(FORM_JS) as f:
        js = f.read()

    guards = _button_guards(js)
    missing = set(BUTTON_LABELS) - set(guards)
    assert not missing, f"no if-guard found for: {sorted(missing)}"
    role_vars = _role_vars(js)
    assert role_vars, "no frappe.user.has_role(...) check found in the form script"

    for label, expected in expected_single.items():
        used = _roles_used_in(guards[label], role_vars)
        assert used, f"{label!r} button is not guarded by any has_role check"
        assert len(used) == 1, f"{label!r} button's guard is ambiguous: {used}"
        (_, roles), = used.items()
        assert roles == expected, f"{label!r} needs {sorted(expected)}, JS checks {sorted(roles)}"

    for label in DUAL_BRANCH_LABELS:
        branches = guards[label].split("||")
        assert len(branches) == 2, (
            f"{label!r} button's guard should have one branch per source "
            f"status (Closed, Locked): {guards[label]!r}")
        seen = set()
        for branch in branches:
            markers = [s for s in ("Closed", "Locked") if f'"{s}"' in branch]
            assert len(markers) == 1, f"{label!r} branch names {markers}: {branch!r}"
            status = markers[0]
            assert status not in seen, f"{label!r} covers {status} twice"
            seen.add(status)

            used = _roles_used_in(branch, role_vars)
            assert used, f"{label!r}'s {status} branch has no has_role check: {branch!r}"
            assert len(used) == 1, f"{label!r}'s {status} branch is ambiguous: {used}"
            (_, roles), = used.items()
            expected = expected_dual[status]
            assert roles == expected, (
                f"{label!r}'s {status} branch needs {sorted(expected)}, "
                f"JS checks {sorted(roles)}")
        assert seen == {"Closed", "Locked"}, f"{label!r} guard covers {seen}, not both statuses"


def test_status_action_flag_permits_only_declared_changes():
    """Under the action flag, validate accepts only the status changes the
    action declared; any other status difference is refused."""
    with _load() as module:
        doc = _valid_year(module)
        p03 = _row(doc, 3)
        p03.status, p03.closed_by, p03.closed_on = "Closed", "closer@example.com", NOW
        declared = {"rows": {"P03": module._status_values(p03)}}

        doc.flags.konsol_status_action = dict(declared)
        doc.save()                           # exactly the declared change: accepted

        doc = _valid_year(module)
        for r in (_row(doc, 3), _row(doc, 5)):
            r.status, r.closed_by, r.closed_on = "Closed", "closer@example.com", NOW
        doc.flags.konsol_status_action = dict(declared)
        result, err = _act(doc.save, PermissionRefused)
        assert err is not None and "P05" in err and "P03" not in err, err

        doc = _valid_year(module)
        doc.status, doc.closed_by, doc.closed_on = "Closed", FORGER, NOW
        doc.flags.konsol_status_action = True    # a bare flag declares nothing
        result, err = _act(doc.save, PermissionRefused)
        assert err is not None, "a bare action flag let a status edit through"

        doc = _valid_year(module)
        _row(doc, 3).status, _row(doc, 3).closed_by = "Closed", FORGER    # not the declared stamp
        _row(doc, 3).closed_on = NOW
        doc.flags.konsol_status_action = dict(declared)
        result, err = _act(doc.save, PermissionRefused)
        assert err is not None and "P03" in err, err
