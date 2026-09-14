"""EPM Fiscal Year's Generate Periods action (konsol#189).

A whitelisted doc method the form button calls: it replaces the period table
with the rows konsol.fiscal_patterns_model generates for the year's pattern,
then saves. Only EPM Admin or System Manager may run it, and only on an Open
year no document uses yet. Loaded against a stub frappe, as in
test_fiscal_year_controller.py; the stubs stay installed during calls."""
import ast
import contextlib
import importlib.util
import os
import sys
import types
from datetime import date, datetime

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTROLLER = os.path.join(APP_DIR, "epm", "doctype", "epm_fiscal_year", "epm_fiscal_year.py")
PURE = {
    "konsol.fiscal_structure_model": os.path.join(APP_DIR, "fiscal_structure_model.py"),
    "konsol.fiscal_status_model": os.path.join(APP_DIR, "fiscal_status_model.py"),
    "konsol.fiscal_patterns_model": os.path.join(APP_DIR, "fiscal_patterns_model.py"),
}


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
            return self.__dict__.get("_before_save")

        def save(self, *args, **kwargs):
            """Runs validate(), as Frappe's save does, and counts the save."""
            self.validate()
            self.saves += 1
            return self

    def throw(msg, exc=None, *args, **kwargs):
        raise (exc or Thrown)(msg)

    def whitelist(*args, **kwargs):
        if args and callable(args[0]) and not kwargs:
            return args[0]
        return lambda fn: fn

    mods = {name: types.ModuleType(name) for name in (
        "frappe", "frappe.model", "frappe.model.document", "frappe.utils", "konsol",
        "konsol.fiscal_calendar")}
    calendar = mods["konsol.fiscal_calendar"]
    calendar.used = set()
    calendar.periods_in_use = lambda fiscal_year: set(calendar.used)
    mods["konsol"].fiscal_calendar = calendar
    frappe = mods["frappe"]
    frappe.roles = ["EPM Admin"]
    frappe.get_roles = lambda *a, **k: list(frappe.roles)
    frappe._ = lambda s: s
    frappe._dict = _dict
    frappe.throw = throw
    frappe.whitelist = whitelist
    frappe.ValidationError = type("ValidationError", (Exception,), {})
    frappe.PermissionError = PermissionRefused
    frappe.utils = mods["frappe.utils"]
    mods["frappe.model.document"].Document = Document
    mods["frappe.utils"].getdate = _getdate
    mods["frappe.utils"].get_datetime = _get_datetime
    mods["frappe.utils"].cint = lambda v: int(v or 0)
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


def test_whitelisted_post_only():
    with open(CONTROLLER) as f:
        tree = ast.parse(f.read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "EPMFiscalYear")
    method = next((n for n in cls.body
                   if isinstance(n, ast.FunctionDef) and n.name == "generate_periods"), None)
    assert method is not None, "EPMFiscalYear has no generate_periods method"
    found = False
    for dec in method.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "whitelist"
                and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "frappe"):
            for kw in dec.keywords:
                if kw.arg == "methods":
                    found = ast.literal_eval(kw.value) == ["POST"]
    assert found, "generate_periods is not @frappe.whitelist(methods=['POST'])"
