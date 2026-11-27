"""konsol.group_rates: the rules every group rate is held to (konsol#103).

Decided 13 Sep 2026: one source of truth for FX rates, published by konsol as
TRUE rates; the ERP feed only pre-fills drafts; Closing and Average per
fiscal period, into a group reporting currency, entered as a quote per 1 /
10 / 100 / 1,000 / 10,000 units; a period cannot close without them.

These tests came from test_group_exchange_rate.py when Group Exchange Rate
was retired (konsol#592 T26): the rules module outlived the doctype, and the
rates now live on Rate Sheets (test_rate_sheet*.py hold the controller, the
publish is in test_group_rates_publish, the sheet side of the close gate in
test_rate_sheet_gate). group_rates runs here against a stub frappe; the close
gate itself is EPM Fiscal Year's (test_fiscal_year_actions)."""
import ast
import calendar
import datetime
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(APP_DIR, "group_rates.py")


class Refused(Exception):
    pass


# -- a stub frappe ---------------------------------------------------------------

#: What the ISO Currency fixture says, for the stub (a subset; XAU has none).
REFS = {"USD": 0.0, "EUR": -0.03, "CHF": -0.05, "GBP": -0.1, "JPY": 2.17, "KRW": 3.13, "IDR": 4.21,
        "VND": 4.40, "ARS": 3.1, "SEK": 1.02, "XAU": 0.0}


def _frappe(record, *, refs=None, move_threshold_pct=50, magnitude_factor=None, actuals="ACTUAL",
            sheet_rows=()):
    """``actuals``: the Rate Set(s) flagged Is Actuals (konsol#348); a str, a
    tuple of names, or None for a site that has flagged none. ``sheet_rows``:
    what the read of a period's approved Statutory sheet rows returns, as
    (sheet, supersedes, from, to, quoted_per, average_quote, closing_quote)."""
    frappe = types.ModuleType("frappe")
    flagged = [] if actuals is None else ([actuals] if isinstance(actuals, str) else list(actuals))
    for name in ("ValidationError", "MandatoryError", "DuplicateEntryError", "PermissionError"):
        setattr(frappe, name, type(name, (Exception,), {}))
    refs = REFS if refs is None else refs

    def throw(msg, exc=None, *a, **k):
        record.setdefault("thrown", []).append(getattr(exc, "__name__", None))
        raise Refused(msg)

    def sql(query, params=(), *a, **k):
        record.setdefault("sql", []).append(query)
        if "FROM `tabRate Sheet Row`" in query:
            return [tuple(r) for r in sheet_rows]
        return []

    def get_all(doctype, filters=None, fields=None, **k):
        if doctype == "ISO Currency":
            return [types.SimpleNamespace(name=c, usd_log10=refs[c], get=lambda f, c=c: refs[c])
                    for c in filters["name"][1] if c in refs]
        if doctype == "Rate Set":
            assert filters == {"is_actuals": 1} and k.get("pluck") == "name", (filters, k)
            return list(flagged)
        return []

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.flags = types.SimpleNamespace()
    frappe.flags.get = lambda k, d=None: getattr(frappe.flags, k, d)
    frappe.session = types.SimpleNamespace(user="approver@example.com")
    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(
        sql=sql,
        # Close Settings' rate_move_threshold (konsol#305-D2-9) and
        # magnitude_warning_factor (konsol#179), None when undeclared.
        get_single_value=lambda dt, field: {"rate_move_threshold": move_threshold_pct,
                                            "magnitude_warning_factor": magnitude_factor}[field],
    )
    return frappe


def _default_period_dates(fiscal_year, fiscal_period):
    """The test double's default period_status.period_dates: the old month
    arithmetic (dbt build_date_from_year_period), so a test that doesn't care
    about konsol#189's declared calendar needs no changes. Override
    ``period_dates`` (it is imported by name into konsol.group_rates) for a
    test that does."""
    start = datetime.date(max(int(fiscal_year), 1900), min(max(int(fiscal_period), 1), 12), 1)
    end = datetime.date(start.year, start.month, calendar.monthrange(start.year, start.month)[1])
    return start, end


def _rules_module(frappe, overrides=None):
    """konsol.group_rates, loaded by path against ``frappe``. konsol.period_status
    is stubbed too, so the module's ``from konsol.period_status import
    period_dates, PeriodNotDeclared`` resolves; override ``period_dates`` in
    ``overrides`` for a konsol#189 test (it becomes the module's own global,
    so period_start/period_end read the override on every call)."""
    period_status = types.ModuleType("konsol.period_status")
    period_status.period_dates = _default_period_dates
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    period_status.assert_open = lambda *a, **k: None   # konsol#584's copy; a test overrides it
    saved = {n: sys.modules.get(n) for n in ("frappe", "konsol.period_status")}
    sys.modules["frappe"] = frappe
    sys.modules["konsol.period_status"] = period_status
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
    for k, v in (overrides or {}).items():
        setattr(module, k, v)
    return module


def _refused(fn, *a):
    try:
        fn(*a)
    except Refused:
        return True
    return False


def _ddl():
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        tree = ast.parse(f.read())
    return {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
            and getattr(n.targets[0], "id", "") in ("_REFERENCE_TABLE_DDL", "_RAW_TABLE_DDL", "_ADDED_COLUMNS")}


def test_the_staging_ddl_is_the_true_rate():
    """Keep in step with konsolidat's clickhouse/init-db.sql, character for
    character. konsol#348: rate_set is the LAST column, added to an existing
    table by _ADDED_COLUMNS (the table shipped before it), so a fresh and an
    upgraded table agree; the publish's CREATE TABLE shadow AS live copies
    the live shape, so the live table must have the column before the first
    publish (ensure_reference_tables runs before reconcile_all)."""
    body = _ddl()["_REFERENCE_TABLE_DDL"]["epm_staging.group_exchange_rates"]
    assert body == (
        "(to_currency String, from_currency String, fiscal_year UInt16, "
        "fiscal_period UInt8, rate_type String, rate Float64, document String, rate_set String) "
        "ENGINE = MergeTree ORDER BY (to_currency, from_currency, fiscal_year, fiscal_period, rate_type)")
    cols = [c.strip().split()[0] for c in body[1:body.index(")")].split(",")]
    with open(os.path.join(APP_DIR, "consolidation", "doctype", "rate_sheet", "rate_sheet.py")) as f:
        sheet = next(n for n in ast.parse(f.read()).body if isinstance(n, ast.ClassDef))
    staged = {t.id: ast.literal_eval(n.value) for n in sheet.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name)}
    assert staged["CH_STAGING_TABLE"] == "epm_staging.group_exchange_rates"
    assert cols == staged["CH_STAGING_COLUMNS"], "Rate Sheet publishes the table's own columns"
    assert tuple(cols) == _rules().PUBLISH_COLUMNS
    assert _ddl()["_ADDED_COLUMNS"]["epm_staging.group_exchange_rates"] == [("rate_set", "String")]


def test_the_currencies_ddl_carries_the_reference():
    consts = _ddl()
    # identical to konsolidat's clickhouse/init-db.sql; NaN = never synced
    assert consts["_REFERENCE_TABLE_DDL"]["epm_gold.currencies"] == (
        "(currency_code String, currency_name String, symbol String, minor_unit UInt8, "
        "usd_log10 Float64 DEFAULT nan) "
        "ENGINE = MergeTree ORDER BY currency_code")
    # a table that already shipped gets the column on the next migrate, at the
    # end of its CREATE so a fresh table and an upgraded one agree
    added = consts["_ADDED_COLUMNS"]
    assert added["epm_gold.currencies"] == [("usd_log10", "Float64 DEFAULT nan")]
    # _ADDED_COLUMNS also carries raw tables (konsol#159), created by ensure_raw_tables
    ddl = {**consts["_REFERENCE_TABLE_DDL"], **consts["_RAW_TABLE_DDL"]}
    for table, cols in added.items():
        body = ddl[table]
        assert body[:body.index(") ENGINE")].endswith(", ".join(f"{c} {t}" for c, t in cols)), table
    sql = []
    m = _clickhouse_module(lambda s, params=None: sql.append(s) or "")
    m.ensure_reference_tables()
    stmt = "ALTER TABLE epm_gold.currencies ADD COLUMN IF NOT EXISTS usd_log10 Float64 DEFAULT nan"
    assert stmt in sql and sql.index(stmt) > sql.index(
        "CREATE TABLE IF NOT EXISTS epm_gold.currencies " + consts["_REFERENCE_TABLE_DDL"]["epm_gold.currencies"])


def _clickhouse_module(execute):
    """konsol.clickhouse, loaded by path against stubs, with ``execute`` replaced."""
    mods = {n: types.ModuleType(n) for n in ("frappe", "requests", "requests.exceptions")}
    mods["frappe"].logger = lambda *a, **k: types.SimpleNamespace(warning=lambda *a, **k: None,
                                                                  error=lambda *a, **k: None)
    mods["frappe"].flags = types.SimpleNamespace()
    mods["requests"].exceptions = mods["requests.exceptions"]
    for name in ("ConnectionError", "Timeout", "HTTPError"):
        setattr(mods["requests.exceptions"], name, type(name, (Exception,), {}))
    saved = {n: sys.modules.get(n) for n in mods}
    sys.modules.update(mods)
    try:
        spec = importlib.util.spec_from_file_location("ch_under_test", os.path.join(APP_DIR, "clickhouse.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old
    module.execute = execute
    return module


def _rules(**overrides):
    return _rules_module(_frappe({}), overrides)


def test_a_quote_per_a_unit():
    """Decided 13 Sep 2026: quote per 1 / 10 / 100 / 1,000 / 10,000 units of the
    from-currency; the direction never flips; the true rate is quote / per."""
    r = _rules()
    assert r.QUOTED_PER == (1, 10, 100, 1000, 10000) and r.MIN_SIGNIFICANT_DIGITS == 6
    # divided in decimal: the nearest double to the exact quotient, not 0.006606999999999999
    assert r.true_rate(0.6607, "100") == 0.006607 and r.true_rate(0.394, 10000) == 0.0000394
    assert r.true_rate(0.9478, "1") == 0.9478 and r.true_rate("0.660700000", "100") == 0.006607
    assert r.true_rate(0, "100") == 0.0
    assert [r.significant_digits(q) for q in (0.0066, 0.66, 0.0000394, 150, 0.0001)] == [7, 9, 5, 12, 6]
    # the pre-fill and the adoption: the smallest unit that puts the quote at 0.1 or more
    assert r.choose_quoted_per(0.9478) == (0.9478, 1)
    assert r.choose_quoted_per(0.006607) == (0.6607, 100), "JPY -> USD per 100"
    assert r.choose_quoted_per(0.000741) == (0.741, 1000), "KRW per 1,000"
    assert r.choose_quoted_per(0.0000394) == (0.394, 10000), "VND per 10,000"
    assert r.choose_quoted_per(1 / 150) == (0.666666667, 100)
    assert r.choose_quoted_per(0.000001) == (0.01, 10000), "the largest unit when none reaches 0.1"
    assert r.quote_label(0.6607, "100", "JPY", "USD") == "0.6607 USD per 100 JPY"
    assert r.quote_label(0.394, 10000, "VND", "USD") == "0.394 USD per 10,000 VND"
    assert r.quote_label(0.9478, "1", "EUR", "CHF") == "0.9478 CHF per EUR"
    src = open(RULES).read()
    assert "inverse" not in src.replace("inverse of", ""), "nothing is inverted to store it"


CASES = os.path.join(APP_DIR, "tests", "fx_magnitude_cases.json")


def test_the_shared_case_table():
    """The same cases, rates, references and outcomes as konsolidat #176's
    dbt_project/tests/assert_fx_magnitude_cases.sql (fx_magnitude_problem):
    the two implementations of the rule cannot drift apart."""
    r = _rules()
    with open(CASES) as f:
        cases = json.load(f)["cases"]
    assert len(cases) == 13
    got = []
    for c in cases:
        refs = {c["from"]: r.usd_reference(c["from"], float(c["from_log10"])),
                c["to"]: r.usd_reference(c["to"], float(c["to_log10"]))}
        got.append(r.magnitude_verdict(c["from"], c["to"], float(c["rate"]), refs)[0])
    assert got == [c["expected"] for c in cases], [
        (c["note"], c["expected"], g) for c, g in zip(cases, got) if g != c["expected"]]


def test_one_definition_of_the_no_reference_rule():
    """konsol.fx_reference holds it; group_rates and currency_references use it."""
    import importlib as _importlib

    rule = _importlib.import_module("konsol.fx_reference")
    r = _rules()
    assert r.usd_reference is rule.usd_reference and r.REFERENCE_CURRENCY == rule.REFERENCE_CURRENCY
    for path in (RULES, os.path.join(APP_DIR, "currency_references.py")):
        tree = ast.parse(open(path).read())
        defined = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)} | {
            t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
        assert not defined & {"usd_reference", "is_unset", "REFERENCE_CURRENCY"}, (path, defined)


def test_the_magnitude_guard_reads_iso_currency():
    """One rule and one home: ISO Currency.usd_log10, read for the two currencies."""
    src = open(RULES).read()
    assert "WIDE_BAND" not in src and "TIGHT_BOUNDS" not in src and "WIDE_BOUNDS" not in src
    asked = []
    frappe = _frappe({})
    lookup = frappe.get_all
    frappe.get_all = lambda doctype, **k: asked.append((doctype, k["filters"], k["fields"])) or lookup(doctype, **k)
    r = _rules_module(frappe)
    assert (r.REFERENCE_FIELD, r.REFERENCE_CURRENCY, r.MAGNITUDE_TOLERANCE_DECADES) == ("usd_log10", "USD", 1.0)
    assert r.magnitude_verdict("JPY", "USD", 0.006607) == ("ok", None)
    assert asked == [("ISO Currency", {"name": ["in", ["JPY", "USD"]]}, ["name", "usd_log10"])]
    verdict, why = r.magnitude_verdict("EUR", "USD", 108.9)
    assert verdict == "implausible" and "x above" in why
    assert r.magnitude_problem("EUR", "USD", 1.08) is None and r.magnitude_problem("EUR", "USD", 108.9) == why
    # the edge: 10x from the reference passes, just over it doesn't
    assert r.magnitude_problem("USD", "SEK", 10 ** 1.02 * 9.9) is None
    assert r.magnitude_problem("USD", "SEK", 10 ** 1.02 * 10.1)


def test_the_magnitude_warning_names_the_quoted_per_that_fits():
    """konsol#179 (from test_group_exchange_rate, dropped with it by T26): a
    quote past the declared factor but within the hard 10x names the Quoted
    Per that brings it in line, or says no listed unit does."""
    r = _rules()
    assert r.magnitude_problem("JPY", "USD", 0.06607, REFS) is None, "under 10x: the hard guard passes it"
    msg = r.magnitude_warning("JPY", "USD", 0.6607, 10, 3.0, REFS)
    assert msg and "about 9.8x above" in msg and "Magnitude Warning Factor (3x" in msg, msg
    assert "0.6607 USD per 100 JPY" in msg and "Reason for Change" in msg, msg
    # below: EUR -> USD quoted per 10 where 1 was meant (0.997 decades, passes the hard guard)
    assert r.magnitude_problem("EUR", "USD", 0.108, REFS) is None
    msg = r.magnitude_warning("EUR", "USD", 1.08, 10, 3.0, REFS)
    assert msg and "below" in msg and "1.08 USD per EUR" in msg, msg
    # 10x too low quoted per 1: no listed unit (per 0.1) fits, so the quote is named
    msg = r.magnitude_warning("CHF", "USD", 0.1135, 1, 3.0, REFS)
    assert msg and "Quoted Per" in msg and "10x too low" in msg, msg
    # a gap no unit explains (4x: per 10 would leave it 2.5x below) names no unit
    msg = r.magnitude_warning("EUR", "CHF", 3.8, 1, 2.0, REFS)
    assert msg and "No Quoted Per brings it in line" in msg, msg


def test_the_magnitude_warning_is_quiet_in_line_undeclared_or_refused():
    r = _rules()
    assert r.magnitude_warning("JPY", "USD", 0.6607, 100, 3.0, REFS) is None, "in line"
    assert r.magnitude_warning("JPY", "USD", 0.6607, 10, None, REFS) is None, "undeclared: the caller reports"
    assert r.magnitude_warning("JPY", "USD", 0.6607, 10, 12.0, REFS) is None, "within the factor"
    assert r.magnitude_warning("JPY", "USD", 0.6607, 1, 3.0, REFS) is None, "100x: the hard guard's"
    assert r.magnitude_warning("XAU", "USD", 2100, 1, 3.0, dict(REFS, XAU=None)) is None, "no reference: hard"


def test_the_move_rule():
    """The threshold is declared in Close Settings, not coded (konsol#305-D2-9):
    ``move_problem`` takes it as a fraction, and there is no coded default."""
    r = _rules()
    # today's four cases, unchanged, with the old default made explicit
    assert r.move_problem(1.0, (1.4, "P11"), None, threshold=0.5) is None, "-29%"
    assert "-55%" in r.move_problem(0.45, (1.0, "P11"), None, threshold=0.5)
    assert "+60%" in r.move_problem(1.6, None, 1.0, threshold=0.5)
    assert r.move_problem(1.6, None, None, threshold=0.5) is None, "no previous rate, no quote: nothing to compare"
    # a different declared threshold changes what needs a reason
    assert r.move_problem(1.0, (1.4, "P11"), None, threshold=0.3) is None, "-29% is still under 30%"
    problem = r.move_problem(1.4, (1.0, "P11"), None, threshold=0.3)
    assert problem and "over 30%" in problem, problem
    # undeclared (threshold=None): a reference to compare against exists, so the
    # gap names Close Settings rather than guessing a value
    # a small declared threshold is stated as declared, not rounded to "0%"
    small = r.move_problem(1.01, (1.0, "P11"), None, threshold=0.005)
    assert small and "over 0.5%" in small, small
    undeclared = r.move_problem(1.6, (1.0, "P11"), None, threshold=None)
    assert undeclared and "Close Settings" in undeclared, undeclared
    assert r.move_problem(1.6, None, None, threshold=None) is None, \
        "nothing to compare against: no gap either, even undeclared"
    assert not hasattr(r, "MOVE_NEEDS_REASON"), "the threshold is declared, not coded"


def test_every_iso_currency_has_a_reference():
    with open(os.path.join(APP_DIR, "reference_data", "iso_currencies.json")) as f:
        rows = json.load(f)
    by_code = {r["currency_code"]: r.get("usd_log10") for r in rows}
    assert len(by_code) == 180, "ISO 4217 List One in full, and ANG, BGN withdrawn (PR #384 review)"
    assert len(rows) == len(by_code), "no code is listed twice"
    # konsol#176: a hyperinflationary currency cannot have one reference that
    # serves every period, so the bolívar waits for that issue's per-period work.
    # PR #384 review: VES is an ISO code, so it is listed, with NO reference:
    # a VES rate is refused as "no_reference" until a site sets one.
    assert by_code["VES"] is None, "VES waits for konsol#176; do not seed it with a single reference"
    r = _rules()
    # konsol#190's 88 keep their references; a code added from List One has
    # none until a site sets it (refused as "no_reference", naming the field).
    assert len([c for c, v in by_code.items() if r.usd_reference(c, v) is not None]) == 88
    with open(os.path.join(APP_DIR, "epm", "doctype", "iso_currency", "iso_currency.json")) as f:
        field = next(x for x in json.load(f)["fields"] if x["fieldname"] == "usd_log10")
    assert field["fieldtype"] == "Float" and "0.001" in field["description"]
    with open(os.path.join(APP_DIR, "epm", "doctype", "iso_currency", "iso_currency.py")) as f:
        assert '"usd_log10": "usd_log10"' in f.read(), "written through to epm_gold.currencies"


def test_the_period_date_is_the_warehouse_one():
    r = _rules()
    assert str(r.period_start(2024, 3)) == "2024-03-01"
    assert str(r.period_start(2024, 0)) == "2024-01-01"
    assert str(r.period_start(2024, 13)) == "2024-12-01"


def test_period_dates_come_from_the_declared_calendar():
    """konsol#189: a 13-period year's P02 is a declared 28-day window, not
    the calendar month of February. period_start/period_end read exactly
    what period_status.period_dates says, never invent one."""
    declared = {(2024, 2): (datetime.date(2024, 1, 29), datetime.date(2024, 2, 25))}
    r = _rules_module(_frappe({}), {"period_dates": lambda fy, fp: declared[(fy, fp)]})
    assert (str(r.period_start(2024, 2)), str(r.period_end(2024, 2))) == ("2024-01-29", "2024-02-25")
    assert r.period_end(2024, 2) != datetime.date(2024, 2, 29), "not Feb's calendar month"


def test_undeclared_period_has_no_dates():
    """An undeclared period raises PeriodNotDeclared; no date is invented."""
    r = _rules_module(_frappe({}))

    def undeclared(fy, fp):
        raise r.PeriodNotDeclared(f"FY{fy} has no period {fp}: it has not been declared.")
    r.period_dates = undeclared
    for fn in (r.period_start, r.period_end):
        try:
            fn(2099, 14)
        except r.PeriodNotDeclared as e:
            assert "FY2099 has no period 14" in str(e)
        else:
            raise AssertionError("an undeclared period must raise PeriodNotDeclared")


ROWS = [
    ("d365_fo", "Closing", "EUR", "CHF", 0.9478, "2024-03-01"),
    ("d365_fo", "Default", "EUR", "CHF", 0.945, "2024-03-01"),
    ("d365_fo", "Closing", "USD", "JPY", 150.0, "2024-03-01"),
    ("d365_fo", "Closing", "EUR", "USD", 1.08, "2024-03-01"),
    ("d365_fo", "Closing", "GBP", "USD", 1.27, "2024-03-01"),
    ("d365_fo", "Default", "GBP", "CHF", 1.11, "2024-03-01"),
    ("erpnext", "Closing", "EUR", "CHF", 0.9480, "2024-03-01"),
]


def test_quotes_direct_inverse_and_a_derived_cross():
    r = _rules()
    [q] = [q for q in r.resolve_quotes(ROWS, "EUR", "CHF", "Closing") if q["source"] == "d365_fo"]
    assert (q["rate"], q["how"], q["erp_type"]) == (0.9478, "direct", "Closing")
    [q] = r.resolve_quotes(ROWS, "JPY", "USD", "Closing")
    assert q["how"] == "inverse of USD→JPY" and q["rate"] == 1 / 150.0, "full precision until it is stored"
    [q] = r.resolve_quotes(ROWS, "GBP", "EUR", "Closing")
    assert q["how"] == "cross via USD" and abs(q["rate"] - 1.27 / 1.08) < 1e-12


def test_average_falls_back_to_the_erp_default_type_and_says_so():
    r = _rules()
    [q] = r.resolve_quotes(ROWS[:2], "EUR", "CHF", "Average")
    assert (q["erp_type"], q["rate"]) == ("Default", 0.945)
    assert r.resolve_quotes(ROWS, "EUR", "SEK", "Closing") == []


def test_two_erp_sources_are_both_shown_and_a_disagreement_is_flagged():
    r = _rules()
    quotes = r.resolve_quotes(ROWS, "EUR", "CHF", "Closing")
    assert [q["source"] for q in quotes] == ["d365_fo", "erpnext"]
    note = r.describe_quotes(quotes, 2024, 3, "EUR", "CHF")
    assert note.startswith("ERP SOURCES DISAGREE") and "0.9478 CHF per EUR" in note and "0.948 CHF per EUR" in note


def _sheet_rows(approved):
    """The approved Statutory sheet's rows for ``approved`` (from, to,
    rate_type) keys: a row per pair, with the quotes its keys ask for."""
    pairs = sorted({(f, t) for f, t, _ in approved})
    return [("RS-S-2024-03", None, f, t, "1", 0.9 if (f, t, "Average") in approved else 0,
             0.9 if (f, t, "Closing") in approved else 0) for f, t in pairs]


def _gate(pairs=None, approved=(), error=None, built=True, groups=(), entities=(), record=None):
    record = {} if record is None else record
    frappe = _frappe(record, sheet_rows=_sheet_rows(approved))

    def gaps(fy, fp):
        # (pairs, groups with no reporting currency, entities with no functional currency; konsol#311)
        if error:
            raise error
        return set(pairs or ()), list(groups), list(entities)

    def ledgers():
        if isinstance(built, Exception):
            raise built
        return built
    return _rules_module(frappe, {"translation_gaps": gaps, "ledgers_built": ledgers})


def test_the_close_gate():
    pairs = {("EUR", "CHF"), ("JPY", "USD")}
    full = [(f, t, rt) for f, t in pairs for rt in ("Closing", "Average")]
    assert not _refused(_gate(pairs, full).assert_rates_complete, 2024, 3)
    r = _gate(pairs, full[:-1])
    try:
        r.assert_rates_complete(2024, 3)
    except Refused as e:
        assert "no approved group exchange rate for" in str(e) and str(e).count("→") == 1
    else:
        raise AssertionError("a missing rate must refuse the close")
    assert not _refused(_gate(set()).assert_rates_complete, 2024, 3), "no ledgers, nothing owed"
    # a group the period translates into has no reporting currency: the warehouse
    # guard refuses it, so the gate does, with every rate approved
    r = _gate(pairs, full, groups=["ZZ_NOCURRENCY"])
    assert r.rate_gate(2024, 3) == ([], None, ["Consolidation Group ZZ_NOCURRENCY has no reporting currency"])
    try:
        r.assert_rates_complete(2024, 3)
    except Refused as e:
        assert "Consolidation Group ZZ_NOCURRENCY has no reporting currency" in str(e)
    else:
        raise AssertionError("a group with no reporting currency must refuse the close")
    # konsol#311: an entity with ledger rows and no functional currency is named,
    # never skipped, with every rate approved
    r = _gate(pairs, full, entities=["ZZ_NOFC"])
    assert r.rate_gate(2024, 3) == ([], None, ["Entity ZZ_NOFC has no functional currency"])
    try:
        r.assert_rates_complete(2024, 3)
    except Refused as e:
        assert "Entity ZZ_NOFC has no functional currency" in str(e), str(e)
        assert "Set each entity's Functional Currency" in str(e), str(e)
    else:
        raise AssertionError("an entity with no functional currency must refuse the close")
    r = _gate(pairs, full, groups=["ZZ_NOCURRENCY"], entities=["ZZ_NOFC"])
    assert r.rate_gate(2024, 3)[2] == ["Consolidation Group ZZ_NOCURRENCY has no reporting currency",
                                       "Entity ZZ_NOFC has no functional currency"]


def test_rate_gate_lock_only_for_close():
    """konsol#189 62h: rate_gate is also called for the read-only My work
    screen on every view; LOCK IN SHARE MODE there would take share locks on
    rate rows and can block rate saves for the request's duration. Only the
    close gate (assert_rates_complete -> rate_gate(..., lock=True)) needs the
    lock (PR #191 re-review finding 4): a plain read can return this
    transaction's REPEATABLE READ snapshot, missing a rate cancelled while a
    close waited on the year lock."""
    pairs = {("EUR", "CHF")}
    full = [(f, t, rt) for f, t in pairs for rt in ("Closing", "Average")]
    record = {}
    r = _gate(pairs, full, record=record)

    def sheet_reads():
        return [q for q in record.get("sql", []) if "tabRate Sheet Row" in q]
    r.rate_gate(2024, 3)
    assert sheet_reads(), "expected a read of the approved sheet rows"
    assert not any("LOCK IN SHARE MODE" in q for q in sheet_reads()), \
        "rate_gate's default (close.mywork_api's call) must not take a row lock: " + repr(sheet_reads())
    record["sql"] = []
    assert not _refused(r.assert_rates_complete, 2024, 3)
    assert sheet_reads() and all("LOCK IN SHARE MODE" in q for q in sheet_reads()), \
        "assert_rates_complete's close path must lock: " + repr(sheet_reads())


def test_the_close_gate_fails_closed_when_the_warehouse_cannot_answer():
    assert _refused(_gate(error=ConnectionError("down")).assert_rates_complete, 2024, 3)
    unknown = Exception("Code: 60. DB::Exception: Table epm_gold.gold_trial_balance does not exist. "
                        "(UNKNOWN_TABLE) (version 24.8.4.13)")
    no_db = Exception("Code: 81. DB::Exception: Database epm_gold does not exist. (UNKNOWN_DATABASE)")
    assert not _refused(_gate(error=unknown, built=False).assert_rates_complete, 2024, 3), "never built"
    assert not _refused(_gate(error=no_db, built=False).assert_rates_complete, 2024, 3), "never built"
    # a built warehouse missing a table the pairs need (gold_entity_ownership) can't answer
    missing_ownership = Exception("Code: 60. DB::Exception: Table epm_gold.gold_entity_ownership does "
                                  "not exist. (UNKNOWN_TABLE)")
    assert _refused(_gate(error=missing_ownership, built=True).assert_rates_complete, 2024, 3)
    assert _refused(_gate(error=unknown, built=ConnectionError("down")).assert_rates_complete, 2024, 3)
    # the token, not a code substring: Code 600-609 is not UNKNOWN_TABLE
    other = Exception("Code: 605. DB::Exception: something else. (SOME_OTHER_ERROR)")
    assert _refused(_gate(error=other, built=False).assert_rates_complete, 2024, 3)
    assert _rules().ch_error_names(unknown) == {"UNKNOWN_TABLE"}
    assert _gate(error=ConnectionError("down")).rate_gate(2024, 3) == (None, "ConnectionError", [])


def test_one_answer_for_what_translation_needs():
    """konsol#311 (F13): translation_gaps is the one read of the period's
    translation needs. The two-part translation_needs (which dropped the
    entities with no functional currency) is gone, so no caller can read an
    answer that leaves them out."""
    r = _rules()
    assert hasattr(r, "translation_gaps")
    assert not hasattr(r, "translation_needs"), "translation_needs must be removed (F13)"


def test_the_gate_sorts_pairs_from_groups_with_no_currency():
    """The SQL runs against ClickHouse in test_group_rates_bench (equity
    excluded, a group without a currency named, the pre-fill's fallback
    filtered); here, what the gate makes of the rows, and the bound period."""
    calls = []
    # (entity currency, group currency, group, entity named only when its currency is blank)
    rows = [("IDR", "USD", "GROUP_CORP", ""), ("VND", "USD", "GROUP_CORP", ""), ("EUR", "", "ZZ_NOCURRENCY", ""),
            ("GBP", "", "ZZ_NOCURRENCY", ""), ("KRW", "", "ZZ_OTHER", ""),
            # konsol#311: no functional currency, into a group with one and into one without
            ("", "USD", "GROUP_CORP", "ZZ_NOFC"), ("", "", "ZZ_NOCURRENCY", "ZZ_NOFC2")]
    r = _rules_module(_frappe({}), {"_ch_rows": lambda sql, params=None: calls.append(params) or rows})
    assert r.translation_gaps(2024, 3) == ({("IDR", "USD"), ("VND", "USD")}, ["ZZ_NOCURRENCY", "ZZ_OTHER"],
                                           ["ZZ_NOFC", "ZZ_NOFC2"])
    assert r.required_pairs(2024, 3) == {("IDR", "USD"), ("VND", "USD")}
    missing, error, blockers = r.rate_gate(2024, 3)
    assert error is None
    assert blockers == ["Consolidation Group ZZ_NOCURRENCY has no reporting currency",
                        "Consolidation Group ZZ_OTHER has no reporting currency",
                        "Entity ZZ_NOFC has no functional currency",
                        "Entity ZZ_NOFC2 has no functional currency"], blockers
    assert {(f, t) for f, t, _ in missing} == {("IDR", "USD"), ("VND", "USD")}, missing
    calls.clear()
    r._ch_rows = lambda sql, params=None: calls.append(params) or [("VND", "USD")]
    assert r.tree_pairs(2099, 13) == {("VND", "USD")}
    assert calls == [{"fy": 2099, "fp": 13}]


def test_the_gate_reads_existence_before_passing():
    sql = []
    r = _rules_module(_frappe({}), {"_ch_rows": lambda s, params=None: sql.append(s) or [[0]]})
    assert r.ledgers_built() is False and sql == ["EXISTS TABLE epm_gold.gold_trial_balance"]


def test_closing_a_period_checks_its_group_rates():
    """konsol#189: periods close on EPM Fiscal Year's rows, so the gate is there
    (behaviour in test_fiscal_year_actions.test_closing_a_period_checks_its_group_rates);
    the retired Period Status controller no longer gates anything."""
    with open(os.path.join(APP_DIR, "epm", "doctype", "epm_fiscal_year", "epm_fiscal_year.py")) as f:
        assert "group_rates.assert_rates_complete(" in f.read()
    with open(os.path.join(APP_DIR, "epm", "doctype", "period_status", "period_status.py")) as f:
        assert "assert_rates_complete" not in f.read(), "Period Status is retired; it gates nothing"


def test_actuals_readers_select_the_flagged_set_and_refuse_when_none_is_flagged():
    record = {}
    r = _rules_module(_frappe(record, sheet_rows=_sheet_rows([("EUR", "CHF", "Closing")])))
    assert r.actuals_rate_set() == "ACTUAL"
    assert r._approved_keys(2024, 3) == {("EUR", "CHF", "Closing")}
    assert r._approved_keys(2024, 3, rate_set="BUDGET_2027") == set(), "a sheet is an actuals rate only"
    # none flagged: nothing is assumed, and the close is refused by name
    r = _rules_module(_frappe({}, actuals=None))
    assert _refused(r.actuals_rate_set)
    missing, error, blockers = r.rate_gate(2024, 3)
    assert missing is None and "Is Actuals" in error and blockers == [], (missing, error, blockers)
    try:
        r.assert_rates_complete(2024, 3)
    except Refused as e:
        assert "Is Actuals" in str(e) and "Rate Set" in str(e), str(e)
    else:
        raise AssertionError("a close with no actuals set must be refused")
    # two flagged (a patch or a race got past the controller): refuse, naming both
    r = _rules_module(_frappe({}, actuals=("ACTUAL", "ZZ_OTHER")))
    try:
        r.actuals_rate_set()
    except Refused as e:
        assert "ACTUAL" in str(e) and "ZZ_OTHER" in str(e), str(e)
    else:
        raise AssertionError("two flagged sets must be refused")

