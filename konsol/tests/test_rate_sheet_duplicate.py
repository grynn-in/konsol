"""Duplicate Previous creates a draft Rate Sheet from the previous approved one
(konsol#592 T13), replacing "Copy rates from previous period" (konsol#584).

``group_rates.duplicate_previous(rate_basis, fiscal_year, fiscal_period=None)``
(POST) finds the previous approved sheet of the basis, asks
``rate_sheet_model.plan_duplicate`` for the draft, and inserts it under
DUPLICATE_FLAG. It returns {name, copied_from}, or refuses by name.

group_rates runs against a stub frappe; nothing here needs a site."""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(APP_DIR, "group_rates.py")
DUPLICATE_FLAG = "konsol_duplicating_rate_sheet"


def _sheet(name, basis, fy, fp, docstatus=1, rows=()):
    return {"name": name, "rate_basis": basis, "fiscal_year": fy, "fiscal_period": fp,
            "docstatus": docstatus, "rows": list(rows)}


STAT_ROWS = [{"from_currency": "EUR", "to_currency": "CHF", "quoted_per": "1", "average_quote": 0.94,
              "closing_quote": 0.95, "rate": 0, "erp_average_quote": 0.9, "erp_closing_quote": 0.9,
              "source": "ERP pre-fill"},
             {"from_currency": "JPY", "to_currency": "CHF", "quoted_per": "100", "average_quote": 0.65,
              "closing_quote": 0.66, "rate": 0, "source": "Manual"}]
CONST_ROWS = [{"from_currency": "EUR", "to_currency": "CHF", "quoted_per": "1", "average_quote": 0,
               "closing_quote": 0, "rate": 0.93}]


class Env:
    def __init__(self, sheets, closed=False):
        self.sheets = sheets
        self.closed = closed
        self.events, self.inserted, self.flag_at_insert = [], [], []
        env = self

        frappe = types.ModuleType("frappe")
        for name in ("ValidationError", "PermissionError"):
            setattr(frappe, name, type(name, (Exception,), {}))
        frappe.MandatoryError = type("MandatoryError", (frappe.ValidationError,), {})
        self.frappe = frappe

        class Flags(dict):
            __getattr__ = dict.get

            def __setattr__(self, k, v):
                self[k] = v
        frappe.flags = Flags(in_install=False, in_import=False, in_migrate=False, in_patch=False)

        def throw(msg, exc=None, *a, **k):
            raise (exc or frappe.ValidationError)(msg)

        def sql(query, params=(), *a, **k):
            if "`tabEPM Fiscal Year`" in query:
                assert "LOCK IN SHARE MODE" in query
                env.events.append("year")
                return []
            env.events.append("sheets")
            assert "`tabRate Sheet`" in query
            if "docstatus < 2" in query:
                assert "FOR UPDATE" in query, "the target's keys are read with a locking read"
                basis, year = params
                return [(s["rate_basis"], s["fiscal_year"], s["fiscal_period"]) for s in env.sheets
                        if s["docstatus"] < 2 and (s["rate_basis"], s["fiscal_year"]) == (basis, year)]
            assert "docstatus = 1" in query
            if "fiscal_period <" in query:                # Statutory: the latest approved before (fy, fp)
                basis, y1, y2, fp = params
                found = [s for s in env.sheets if s["docstatus"] == 1 and s["rate_basis"] == basis
                         and (s["fiscal_year"] < y1 or (s["fiscal_year"] == y2 and s["fiscal_period"] < fp))]
                found.sort(key=lambda s: (s["fiscal_year"], s["fiscal_period"]), reverse=True)
            else:                                           # Constant: the latest approved year before
                basis, year = params
                found = [s for s in env.sheets if s["docstatus"] == 1 and s["rate_basis"] == basis
                         and s["fiscal_year"] < year]
                found.sort(key=lambda s: s["fiscal_year"], reverse=True)
            return [(s["name"], s["fiscal_year"], s["fiscal_period"]) for s in found[:1]]

        def get_all(doctype, filters=None, **k):
            assert doctype == "Rate Sheet Row" and filters["parentfield"] == "rates", (doctype, filters)
            return [dict(r) for s in env.sheets if s["name"] == filters["parent"] for r in s["rows"]]

        class Doc:
            def __init__(self, data):
                self.data = data
                self.name = None

            def insert(self):
                env.flag_at_insert.append(bool(frappe.flags.get(DUPLICATE_FLAG)))
                self.name = "RS-NEW"
                env.inserted.append(self.data)
                return self

        frappe.throw, frappe.get_all, frappe.get_doc = throw, get_all, Doc
        frappe.whitelist = lambda *a, **k: (lambda fn: fn)
        frappe.only_for = lambda roles: env.events.append("only_for")
        frappe.db = types.SimpleNamespace(sql=sql)
        frappe._ = lambda s: s

    def call(self, *args, **kwargs):
        """Load group_rates against the stubs and run duplicate_previous. The
        stubs stay in sys.modules for the call, since the function imports
        the year gate when it runs."""
        period_status = types.ModuleType("konsol.period_status")
        period_status.period_dates = lambda fy, fp: (None, None)
        period_status.PeriodNotDeclared = type("PeriodNotDeclared", (self.frappe.ValidationError,), {})

        def assert_open(fy, fp, action="run"):
            self.events.append(("assert_open", fy, fp))
            if self.closed:
                self.frappe.throw(f"Cannot {action}: fiscal period {fp} of FY{fy} is closed.")

        def assert_year_not_closed(fy, action="run"):
            self.events.append(("assert_year_not_closed", fy))
            if self.closed:
                self.frappe.throw(f"Cannot {action}: FY{fy} is closed.")
        period_status.assert_open, period_status.assert_year_not_closed = assert_open, assert_year_not_closed
        saved = {n: sys.modules.get(n) for n in ("frappe", "konsol.period_status")}
        sys.modules["frappe"], sys.modules["konsol.period_status"] = self.frappe, period_status
        try:
            spec = importlib.util.spec_from_file_location("konsol.group_rates", RULES)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.duplicate_previous(*args, **kwargs)
        finally:
            for n, old in saved.items():
                if old is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = old

    def refused(self, *args, **kwargs):
        try:
            self.call(*args, **kwargs)
        except self.frappe.ValidationError as e:
            return str(e)
        return None


def _statutory_env(**kw):
    return Env([_sheet("RS-S-2099-01", "Statutory", 2099, 1, rows=STAT_ROWS),
                _sheet("RS-S-2099-02", "Statutory", 2099, 2, rows=STAT_ROWS[:1]),
                _sheet("RS-S-2099-04", "Statutory", 2099, 4, rows=STAT_ROWS),
                _sheet("RS-S-2098-12", "Statutory", 2098, 12, rows=STAT_ROWS)], **kw)


def test_statutory_duplicates_the_latest_approved_sheet_before_the_period():
    env = _statutory_env()
    out = env.call("Statutory", "2099", "3")
    assert out == {"name": "RS-NEW", "copied_from": "RS-S-2099-02"}
    [data] = env.inserted
    assert (data["doctype"], data["rate_basis"], data["fiscal_year"], data["fiscal_period"]) == (
        "Rate Sheet", "Statutory", 2099, 3)
    assert data["copied_from"] == "RS-S-2099-02" and "RS-S-2099-02" in data["source_note"]
    [row] = data["rates"]
    assert row == {"from_currency": "EUR", "to_currency": "CHF", "quoted_per": "1", "average_quote": 0.94,
                   "closing_quote": 0.95, "source": "Duplicated"}, "quotes and Quoted Per only"
    assert env.flag_at_insert == [True] and not env.frappe.flags.get(DUPLICATE_FLAG)


def test_the_first_period_of_a_year_takes_the_previous_years_last_sheet():
    env = Env([_sheet("RS-S-2098-12", "Statutory", 2098, 12, rows=STAT_ROWS)])
    assert env.call("Statutory", 2099, 1)["copied_from"] == "RS-S-2098-12"
    assert [r["from_currency"] for r in env.inserted[0]["rates"]] == ["EUR", "JPY"]
    assert env.inserted[0]["rates"][1]["quoted_per"] == "100"


def test_constant_duplicates_the_previous_year_and_has_no_period():
    env = Env([_sheet("RS-C-2097", "Constant", 2097, None, rows=CONST_ROWS),
               _sheet("RS-C-2098", "Constant", 2098, None, rows=CONST_ROWS),
               _sheet("RS-S-2098-12", "Statutory", 2098, 12, rows=STAT_ROWS)])
    out = env.call("Constant", 2099)
    assert out == {"name": "RS-NEW", "copied_from": "RS-C-2098"}
    [data] = env.inserted
    assert data["rate_basis"] == "Constant" and data["fiscal_period"] is None
    assert data["rates"] == [{"from_currency": "EUR", "to_currency": "CHF", "quoted_per": "1", "rate": 0.93,
                              "source": "Duplicated"}]
    assert ("assert_year_not_closed", 2099) in env.events


def test_statutory_needs_the_period_open():
    env = _statutory_env()
    env.call("Statutory", 2099, 3)
    assert ("assert_open", 2099, 3) in env.events
    env = _statutory_env(closed=True)
    assert "closed" in env.refused("Statutory", 2099, 3) and env.inserted == []
    env = Env([_sheet("RS-C-2098", "Constant", 2098, None, rows=CONST_ROWS)], closed=True)
    assert "closed" in env.refused("Constant", 2099) and env.inserted == []


def test_no_previous_approved_sheet_is_a_refusal_by_name():
    env = Env([_sheet("RS-S-2099-02", "Statutory", 2099, 2, docstatus=0, rows=STAT_ROWS),
               _sheet("RS-S-2099-05", "Statutory", 2099, 5, rows=STAT_ROWS)])
    why = env.refused("Statutory", 2099, 3)
    assert why and "FY2099 P3" in why and "approved" in why and env.inserted == [], why
    env = Env([_sheet("RS-C-2099", "Constant", 2099, None, rows=CONST_ROWS)])
    assert "FY2099 Constant" in env.refused("Constant", 2099) and env.inserted == []


def test_a_key_that_has_a_sheet_is_refused_and_a_correction_supersedes_instead():
    for existing in (_sheet("RS-S-2099-03", "Statutory", 2099, 3, docstatus=0),
                     _sheet("RS-S-2099-03", "Statutory", 2099, 3, docstatus=1)):
        env = Env([_sheet("RS-S-2099-02", "Statutory", 2099, 2, rows=STAT_ROWS), existing])
        why = env.refused("Statutory", 2099, 3)
        assert why and "supersede" in why and env.inserted == [], why
    # a cancelled sheet no longer holds the key
    env = Env([_sheet("RS-S-2099-02", "Statutory", 2099, 2, rows=STAT_ROWS),
               _sheet("RS-S-2099-03", "Statutory", 2099, 3, docstatus=2)])
    assert env.call("Statutory", 2099, 3)["copied_from"] == "RS-S-2099-02"


def test_a_bad_request_is_refused_before_anything_is_read():
    env = _statutory_env()
    assert "Statutory or Constant" in env.refused("Budget", 2099, 3)
    assert env.refused("Statutory", 2099) and env.refused("Statutory", 2099, None)
    assert "no Fiscal Period" in env.refused("Constant", 2099, 3)
    assert env.inserted == []


def test_the_year_is_locked_before_the_sheets_are_read_and_the_roles_first():
    env = _statutory_env()
    env.call("Statutory", 2099, 3)
    names = [e for e in env.events if isinstance(e, str)]
    assert names[0] == "only_for" and names[1] == "year" and set(names[2:]) == {"sheets"}, names


def test_the_signature_and_the_retirement_of_copy():
    src = open(RULES).read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "duplicate_previous")
    assert [ast.unparse(d) for d in fn.decorator_list] == ["frappe.whitelist(methods=['POST'])"]
    assert [a.arg for a in fn.args.args] == ["rate_basis", "fiscal_year", "fiscal_period"]
    assert [ast.unparse(d) for d in fn.args.defaults] == ["None"]
    body = ast.unparse(fn)
    assert "frappe.only_for(PREFILL_ROLES)" in body and "plan_duplicate" in body and ".submit(" not in body
    # konsol#584's copy is replaced, not kept beside it
    assert "copy_from_previous_period" not in src and "rate_copy_model" not in src
    assert not os.path.exists(os.path.join(APP_DIR, "rate_copy_model.py"))
    assert not os.path.exists(os.path.join(APP_DIR, "tests", "test_rate_copy_model.py"))
    # the GER list script went with its last button (konsol#592 T14)
    path = os.path.join(APP_DIR, "consolidation", "doctype", "group_exchange_rate", "group_exchange_rate_list.js")
    assert not os.path.exists(path)
