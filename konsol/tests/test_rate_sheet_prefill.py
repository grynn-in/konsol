"""Pre-fill from ERP fills a draft Statutory Rate Sheet (konsol#592 T12).

``group_rates.prefill_from_erp(fiscal_year, fiscal_period)`` keeps its
whitelisted POST signature. It creates the period's draft Statutory sheet if
there is none, or fills the missing rows of the draft there is. It never
touches an approved sheet: a correction supersedes it. Each row carries
Source "ERP pre-fill" and its ERP quotes, written under PREFILL_FLAG.

group_rates runs against a stub frappe and a stub sheet; nothing here needs a
site."""
import ast
import importlib.util
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(APP_DIR, "group_rates.py")
CONTROLLER = os.path.join(APP_DIR, "consolidation", "doctype", "rate_sheet", "rate_sheet.py")

ROWS = [("d365_fo", "Closing", "EUR", "CHF", 0.95, "2099-03-31"),
        ("d365_fo", "Average", "EUR", "CHF", 0.94, "2099-03-01"),
        ("d365_fo", "Closing", "JPY", "CHF", 0.0066, "2099-03-31"),
        ("d365_fo", "Average", "JPY", "CHF", 0.0065, "2099-03-01")]


class Refused(Exception):
    pass


class Env:
    """The stub site: sheets by name, the queries, the calls, the flag at each write."""

    def __init__(self, sheets=(), refuse=None):
        self.sheets = {s["name"]: s for s in sheets}
        self.refuse = refuse or {}
        self.events, self.queries, self.written, self.flags_seen = [], [], [], []
        env = self

        frappe = types.ModuleType("frappe")
        for name in ("ValidationError", "MandatoryError", "PermissionError"):
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
            env.queries.append(query)
            env.events.append("year" if "`tabEPM Fiscal Year`" in query else "sheets")
            if "`tabRate Sheet`" in query:
                assert "FOR UPDATE" in query, "the sheets are read with a locking read"
                fy, fp = params
                return [(s["name"], s["docstatus"], s.get("status")) for s in env.sheets.values()
                        if s["docstatus"] < 2 and (s["fiscal_year"], s["fiscal_period"]) == (fy, fp)]
            return []

        class Sheet:
            def __init__(self, data):
                self.__dict__.update(data)
                self.rates = [dict(r) for r in data.get("rates", [])]
                self.name = data.get("name")

            def get(self, k, default=None):
                return getattr(self, k, default)

            def append(self, field, row):
                getattr(self, field).append(dict(row))

            def validate(self):
                env.flags_seen.append(("validate", bool(frappe.flags.get(PREFILL_FLAG))))
                row = self.rates[-1]
                why = env.refuse.get((row["from_currency"], row["to_currency"]))
                if why:
                    raise frappe.ValidationError(why)

            def insert(self):
                self.validate()
                self.name = self.name or f"RS-S-{self.fiscal_year}-{int(self.fiscal_period):02d}"
                env.flags_seen.append(("insert", bool(frappe.flags.get(PREFILL_FLAG))))
                env.written.append(("insert", self.name, [dict(r) for r in self.rates], self.source_note))
                env.sheets[self.name] = {"name": self.name, "docstatus": 0, "fiscal_year": self.fiscal_year,
                                         "fiscal_period": self.fiscal_period, "rates": self.rates}
                return self

            def save(self):
                self.validate()
                env.flags_seen.append(("save", bool(frappe.flags.get(PREFILL_FLAG))))
                env.written.append(("save", self.name, [dict(r) for r in self.rates], self.source_note))
                return self

            def submit(self):
                raise AssertionError("the pre-fill never submits")

        def get_doc(arg, name=None):
            if isinstance(arg, dict):
                assert arg["doctype"] == "Rate Sheet" and arg["rate_basis"] == "Statutory", arg
                return Sheet(arg)
            assert arg == "Rate Sheet"
            return Sheet(env.sheets[name])

        frappe.throw, frappe.get_doc, frappe.whitelist = throw, get_doc, (lambda *a, **k: (lambda fn: fn))
        frappe.only_for = lambda roles: env.events.append(("only_for", roles))
        frappe.clear_last_message = lambda: None
        frappe.db = types.SimpleNamespace(sql=sql)
        frappe._ = lambda s: s

    def rules(self, **overrides):
        period_status = types.ModuleType("konsol.period_status")
        period_status.period_dates = lambda fy, fp: ("START", "END")
        period_status.PeriodNotDeclared = type("PeriodNotDeclared", (self.frappe.ValidationError,), {})
        period_status.assert_open = lambda *a, **k: None
        saved = {n: sys.modules.get(n) for n in ("frappe", "konsol.period_status")}
        sys.modules["frappe"], sys.modules["konsol.period_status"] = self.frappe, period_status
        try:
            spec = importlib.util.spec_from_file_location("konsol.group_rates", RULES)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            for n, old in saved.items():
                if old is None:
                    sys.modules.pop(n, None)
                else:
                    sys.modules[n] = old
        module.required_pairs = lambda fy, fp: {("EUR", "CHF"), ("JPY", "CHF")}
        module.erp_quote_rows = lambda as_of: [r for r in ROWS if (r[1] == "Closing") == (as_of == "END")]
        module.usd_references = lambda codes: {}
        module.magnitude_problem = lambda *a: None
        module.magnitude_warning = lambda *a: None
        module.magnitude_warning_factor = lambda: 3
        for k, v in overrides.items():
            setattr(module, k, v)
        return module


PREFILL_FLAG = "konsol_prefilling_rate_sheet"


def _sheet(name="RS-S-2099-03", docstatus=0, status="Draft", rates=()):
    return {"name": name, "docstatus": docstatus, "status": status, "fiscal_year": 2099, "fiscal_period": 3,
            "rates": list(rates)}


def _row(f, t, avg, cls, per="1"):
    return {"from_currency": f, "to_currency": t, "average_quote": avg, "closing_quote": cls,
            "quoted_per": per, "source": "ERP pre-fill"}


def _rows(env):
    return {(r["from_currency"], r["to_currency"]): r for r in env.written[-1][2]}


def test_no_sheet_creates_the_draft_statutory_sheet_with_a_row_per_pair():
    env = Env()
    out = env.rules().prefill_from_erp("2099", "3")
    assert [w[:2] for w in env.written] == [("insert", "RS-S-2099-03")]
    rows = _rows(env)
    eur, jpy = rows[("EUR", "CHF")], rows[("JPY", "CHF")]
    assert (eur["average_quote"], eur["closing_quote"], eur["quoted_per"]) == (0.94, 0.95, "1")
    # the pre-fill picks one Quoted Per for the row: JPY's 0.0065 and 0.0066 keep their digits per 100
    assert (jpy["average_quote"], jpy["closing_quote"], jpy["quoted_per"]) == (0.65, 0.66, "100")
    for row in rows.values():
        assert row["source"] == "ERP pre-fill"
    assert (eur["erp_average_quote"], eur["erp_closing_quote"]) == (0.94, 0.95)
    assert (jpy["erp_average_quote"], jpy["erp_closing_quote"]) == (0.65, 0.66)
    assert out["sheet"] == "RS-S-2099-03" and out["sheet_created"] is True
    assert sorted(out["created"]) == ["EUR → CHF", "JPY → CHF"]
    assert out["existing"] == out["no_quote"] == out["refused"] == out["warned"] == []
    assert env.written[0][3], "the sheet says where it came from"


def test_the_flag_is_set_for_every_write_and_cleared_after():
    env = Env()
    env.rules().prefill_from_erp(2099, 3)
    assert env.flags_seen and all(on for _, on in env.flags_seen), env.flags_seen
    assert not env.frappe.flags.get(PREFILL_FLAG)
    env = Env()
    rules = env.rules(erp_quote_rows=lambda as_of: 1 / 0)
    try:
        rules.prefill_from_erp(2099, 3)
    except ZeroDivisionError:
        pass
    assert not env.frappe.flags.get(PREFILL_FLAG)


def test_an_existing_draft_gets_its_missing_rows_only():
    mine = _row("EUR", "CHF", 0.9, 0.91)
    mine["source"] = "Manual"
    env = Env([_sheet(rates=[mine])])
    out = env.rules().prefill_from_erp(2099, 3)
    assert [w[:2] for w in env.written] == [("save", "RS-S-2099-03")]
    rows = env.written[-1][2]
    assert [(r["from_currency"], r["average_quote"], r["source"]) for r in rows] == [
        ("EUR", 0.9, "Manual"), ("JPY", 0.65, "ERP pre-fill")], "the person's row is left alone"
    assert out["sheet_created"] is False and out["created"] == ["JPY → CHF"]
    assert out["existing"] == ["EUR → CHF"]


def test_a_draft_with_every_pair_is_not_saved_again():
    env = Env([_sheet(rates=[_row("EUR", "CHF", 0.9, 0.91), _row("JPY", "CHF", 0.6, 0.7, "100")])])
    out = env.rules().prefill_from_erp(2099, 3)
    assert env.written == [] and out["created"] == [] and sorted(out["existing"]) == ["EUR → CHF", "JPY → CHF"]


def test_an_approved_sheet_is_never_touched():
    env = Env([_sheet(docstatus=1, status="Approved", rates=[_row("EUR", "CHF", 0.9, 0.91)])])
    rules = env.rules()
    try:
        rules.prefill_from_erp(2099, 3)
    except env.frappe.ValidationError as e:
        assert "RS-S-2099-03" in str(e) and "supersede" in str(e).lower(), str(e)
    else:
        raise AssertionError("an approved sheet must refuse the pre-fill")
    assert env.written == []


def test_a_correction_draft_is_filled_and_the_approved_sheet_beside_it_is_left_alone():
    approved = _sheet(docstatus=1, status="Approved", rates=[_row("EUR", "CHF", 0.9, 0.91)])
    correction = _sheet(name="RS-S-2099-03-2", rates=[_row("EUR", "CHF", 0.92, 0.93)])
    env = Env([approved, correction])
    env.rules().prefill_from_erp(2099, 3)
    assert [w[:2] for w in env.written] == [("save", "RS-S-2099-03-2")]


def test_a_draft_pending_approval_is_not_edited_under_the_approver():
    env = Env([_sheet(status="Pending Approval")])
    try:
        env.rules().prefill_from_erp(2099, 3)
    except env.frappe.ValidationError as e:
        assert "Pending Approval" in str(e), str(e)
    else:
        raise AssertionError("a sheet in review must not change")
    assert env.written == []


def test_a_pair_missing_a_quote_is_named_and_skipped():
    rows = [r for r in ROWS if not (r[2] == "JPY" and r[1] == "Average")]
    env = Env()
    out = env.rules(erp_quote_rows=lambda as_of: [r for r in rows if (r[1] == "Closing") == (as_of == "END")]
                    ).prefill_from_erp(2099, 3)
    assert out["no_quote"] == ["JPY → CHF Average"] and out["created"] == ["EUR → CHF"]
    assert list(_rows(env)) == [("EUR", "CHF")]


def test_no_quote_at_all_writes_nothing():
    env = Env()
    out = env.rules(erp_quote_rows=lambda as_of: []).prefill_from_erp(2099, 3)
    assert env.written == [] and out["sheet"] is None and len(out["no_quote"]) == 4


def test_the_magnitude_guard_refuses_a_pair_by_name():
    env = Env()
    out = env.rules(magnitude_problem=lambda f, t, rate, refs=None: "100x off" if f == "JPY" else None
                    ).prefill_from_erp(2099, 3)
    assert out["created"] == ["EUR → CHF"]
    assert sorted(r.split(":")[0] for r in out["refused"]) == ["JPY → CHF Average", "JPY → CHF Closing"]
    assert all("100x off" in r for r in out["refused"])


def test_a_row_the_sheet_refuses_is_reported_and_the_rest_are_kept():
    env = Env(refuse={("JPY", "CHF"): "moves 40%. Say why (Reason for Change)"})
    out = env.rules().prefill_from_erp(2099, 3)
    assert out["created"] == ["EUR → CHF"] and list(_rows(env)) == [("EUR", "CHF")]
    assert out["refused"] == ["JPY → CHF: moves 40%. Say why (Reason for Change)"]


def test_a_quote_past_the_warning_factor_is_drafted_and_listed():
    env = Env()
    out = env.rules(magnitude_warning=lambda f, t, q, per, factor, refs=None: "5x off" if f == "EUR" else None
                    ).prefill_from_erp(2099, 3)
    assert sorted(out["created"]) == ["EUR → CHF", "JPY → CHF"]
    assert out["warned"] and all(w.startswith("EUR → CHF") and "5x off" in w for w in out["warned"])


def test_disagreeing_erp_sources_are_listed():
    rows = ROWS + [("erpnext", "Closing", "EUR", "CHF", 0.97, "2099-03-31")]
    env = Env()
    out = env.rules(erp_quote_rows=lambda as_of: [r for r in rows if (r[1] == "Closing") == (as_of == "END")]
                    ).prefill_from_erp(2099, 3)
    assert any("ERP SOURCES DISAGREE" in w and w.startswith("EUR → CHF") for w in out["warned"]), out["warned"]


def test_the_year_is_locked_before_the_sheets_are_read():
    env = Env()
    env.rules().prefill_from_erp(2099, 3)
    names = [e for e in env.events if isinstance(e, str)]
    assert names[:2] == ["year", "sheets"], names
    assert any("LOCK IN SHARE MODE" in q for q in env.queries if "`tabEPM Fiscal Year`" in q)
    assert env.events[0][0] == "only_for", "the roles are checked first"


def test_the_signature_roles_and_flag_names_are_unchanged():
    src = open(RULES).read()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "prefill_from_erp")
    assert [ast.unparse(d) for d in fn.decorator_list] == ["frappe.whitelist(methods=['POST'])"]
    assert [a.arg for a in fn.args.args] == ["fiscal_year", "fiscal_period"]
    body = ast.unparse(fn)
    assert "frappe.only_for(PREFILL_ROLES)" in body and ".submit(" not in body
    env = Env()
    assert env.rules().PREFILL_ROLES == ("EPM Analyst", "EPM Admin", "System Manager")
    # group_rates cannot import the controller (frappe.model), so the flag names are pinned here
    controller = open(CONTROLLER).read()
    for const in ("PREFILL_FLAG", "DUPLICATE_FLAG"):
        theirs = re.search(rf'^{const} = "([^"]+)"', controller, re.M).group(1)
        ours = re.search(rf'^{const} = "([^"]+)"', src, re.M).group(1)
        assert theirs == ours, const
