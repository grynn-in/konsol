"""group_rates.publish_rates publishes from approved Rate Sheets (konsol#592 T10).

epm_staging.group_exchange_rates keeps its shape and name. An approved
Statutory sheet publishes Average and Closing per pair at its period under the
actuals set; an approved Constant sheet publishes both types at its one rate
for every published period of its year under CONSTANT; `document` is the sheet
name. The approved sheets are the whole set: Group Exchange Rate is retired
(konsol#592 T26), so a site with no approved sheet publishes an empty table.

group_rates runs against a stub frappe, a stub MariaDB connection and a stub
ClickHouse; nothing here needs a site."""
import datetime
import importlib.util
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(APP_DIR, "group_rates.py")
MAIN = "epm_staging.group_exchange_rates"
SHADOW = MAIN + "__publishing"


# -- fixtures ------------------------------------------------------------------------

def _cal():
    """FY2099: Opening P0, Regular P1..P12, Closing P13."""
    rows = [{"fiscal_year": 2099, "period": 0, "type": "Opening",
             "start_date": datetime.date(2099, 1, 1), "end_date": datetime.date(2099, 1, 1)}]
    for p in range(1, 13):
        rows.append({"fiscal_year": 2099, "period": p, "type": "Regular",
                     "start_date": datetime.date(2099, p, 1), "end_date": datetime.date(2099, p, 28)})
    rows.append({"fiscal_year": 2099, "period": 13, "type": "Closing",
                 "start_date": datetime.date(2099, 12, 31), "end_date": datetime.date(2099, 12, 31)})
    return rows


def _sheet(name, basis, fy, fp=None, docstatus=1, supersedes=None, modified=None):
    return {"name": name, "rate_basis": basis, "fiscal_year": fy, "fiscal_period": fp,
            "docstatus": docstatus, "supersedes": supersedes, "modified": modified}


def _srow(parent, frm, to, avg, cls, per="1"):
    return {"parent": parent, "from_currency": frm, "to_currency": to, "quoted_per": per,
            "average_quote": avg, "closing_quote": cls, "rate": 0}


def _crow(parent, frm, to, rate, per="1"):
    return {"parent": parent, "from_currency": frm, "to_currency": to, "quoted_per": per,
            "average_quote": 0, "closing_quote": 0, "rate": rate}


STAT = _sheet("RS-S-2099-12", "Statutory", 2099, 12, modified=datetime.datetime(2100, 1, 2, 8, 0, 0))
STAT_ROWS = [_srow("RS-S-2099-12", "JPY", "USD", 0.70, 0.71, "100")]
CONST = _sheet("RS-C-2099", "Constant", 2099)
CONST_ROWS = [_crow("RS-C-2099", "EUR", "USD", 1.1)]


# -- the stubs -------------------------------------------------------------------------

def _frappe(actuals=("ACTUAL",)):
    frappe = types.ModuleType("frappe")
    for name in ("ValidationError", "MandatoryError", "DuplicateEntryError", "PermissionError"):
        setattr(frappe, name, type(name, (Exception,), {}))

    def throw(msg, exc=None, *a, **k):
        raise (exc or frappe.ValidationError)(msg)

    def get_all(doctype, filters=None, **k):
        if doctype == "Rate Set":
            assert filters == {"is_actuals": 1}
            return list(actuals)
        return []

    frappe.throw = throw
    frappe._ = lambda s: s
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.get_all = get_all
    frappe.flags = types.SimpleNamespace(in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe.conf = types.SimpleNamespace(db_name="zzdb")
    frappe.logger = lambda *a, **k: types.SimpleNamespace(error=lambda *a, **k: None,
                                                          exception=lambda *a, **k: None)
    return frappe


def _load(frappe):
    period_status = types.ModuleType("konsol.period_status")
    period_status.period_dates = lambda fy, fp: (None, None)
    period_status.PeriodNotDeclared = type("PeriodNotDeclared", (frappe.ValidationError,), {})
    period_status.assert_open = lambda *a, **k: None
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
    return module


def _publisher(sheets=(), sheet_rows=(), calendar=None, actuals=("ACTUAL",)):
    """(rules, clickhouse stub, record): the publish's own connection returns
    only what its WHERE asks for, so a draft or a cancelled sheet reaches the
    publish only if the query lets it."""
    record = {"db": [], "ch": [], "stamped": [], "failed": [], "reads": []}
    frappe = _frappe(actuals)
    calendar = _cal() if calendar is None else calendar

    def sql(query, params=(), as_dict=False, **k):
        if "GET_LOCK" in query:
            record["db"].append("lock")
            return [(1,)]
        if "RELEASE_LOCK" in query:
            record["db"].append("release")
            return [(1,)]
        raise AssertionError(f"read on the caller's connection: {query}")
    frappe.db = types.SimpleNamespace(sql=sql)

    class FreshDB:
        def sql(self, query, as_dict=False):
            assert as_dict, query
            record["db"].append("fresh read")
            record["reads"].append(query)
            if "`tabRate Sheet Row`" in query:
                assert re.search(r"\bs\.docstatus = 1\b", query), query
                approved = {s["name"] for s in sheets if s["docstatus"] == 1}
                return [types.SimpleNamespace(**r) for r in sheet_rows if r["parent"] in approved]
            if "`tabRate Sheet`" in query:
                assert "docstatus = 1" in query
                return [types.SimpleNamespace(**s) for s in sheets if s["docstatus"] == 1]
            if "`tabEPM Fiscal Year`" in query:
                return [types.SimpleNamespace(**r) for r in calendar]
            raise AssertionError(f"unexpected read: {query}")

        def close(self):
            record["db"].append("fresh close")

    ch = types.ModuleType("konsol.clickhouse")

    def execute(query, params=None):
        record["ch"].append(query)
        return ""
    ch.execute = execute
    ch._stamp_watermark = lambda table, n, modified=None: record["stamped"].append((table, n, modified))
    ch._record_sync_failure = lambda table, kind, message: record["failed"].append((table, kind, message))
    rules = _load(frappe)
    rules._fresh_db = FreshDB
    return rules, ch, record


def _publish(rules, ch):
    saved = sys.modules.get("konsol.clickhouse")
    sys.modules["konsol.clickhouse"] = ch
    try:
        return rules.publish_rates(force=True)
    finally:
        if saved is None:
            sys.modules.pop("konsol.clickhouse", None)
        else:
            sys.modules["konsol.clickhouse"] = saved


_TUPLE = re.compile(r"\('([^']*)', '([^']*)', (\d+), (\d+), '([^']*)', ([^,]+), '([^']*)', '([^']*)'\)")


def _inserted(record):
    """Every row the publish inserted, as [to, from, fy, fp, type, rate, document, set]."""
    rows = []
    for q in record["ch"]:
        if q.startswith(f"INSERT INTO {SHADOW} "):
            for m in _TUPLE.finditer(q):
                to, frm, fy, fp, rt, rate, doc, rs = m.groups()
                rows.append([to, frm, int(fy), int(fp), rt, float(rate), doc, rs])
    return rows


def _key(row):
    return (row[1], row[0], row[2], row[3], row[4], row[7])


# -- the swap ----------------------------------------------------------------------------------

def test_a_site_with_no_approved_sheet_publishes_an_empty_set():
    """Only the sheets are read: the calendar and the rows are not, and the
    swap still runs, so a cancelled last sheet leaves the warehouse."""
    rules, ch, record = _publisher()
    assert _publish(rules, ch) == 0
    assert len(record["reads"]) == 1 and "`tabRate Sheet`" in record["reads"][0]
    assert _inserted(record) == [] and not any(q.startswith("INSERT") for q in record["ch"])
    assert f"EXCHANGE TABLES {MAIN} AND {SHADOW}" in record["ch"]
    assert record["stamped"] == [(MAIN, 0, None)] and record["failed"] == []


def test_the_publish_still_swaps_the_whole_set_in_one_at_a_time():
    rules, ch, record = _publisher(sheets=[STAT], sheet_rows=STAT_ROWS)
    _publish(rules, ch)
    assert record["db"][0] == "lock" and record["db"][-1] == "release"
    assert record["db"][-2] == "fresh close" and set(record["db"][1:-2]) == {"fresh read"}, \
        "every read is on the publish's own connection, after the lock, closed at once"
    kinds = [q.split(" (")[0] if q.startswith("INSERT") else q for q in record["ch"]]
    assert kinds == [f"DROP TABLE IF EXISTS {SHADOW}", f"CREATE TABLE {SHADOW} AS {MAIN}",
                     f"INSERT INTO {SHADOW}", f"EXCHANGE TABLES {MAIN} AND {SHADOW}",
                     f"DROP TABLE IF EXISTS {SHADOW}"]
    for q in record["reads"]:
        assert "LOCK IN SHARE MODE" not in q and "FOR UPDATE" not in q, q


# -- a site with sheets only -----------------------------------------------------------------

def test_a_sheet_only_site_publishes_statutory_rows_under_the_actuals_set():
    rules, ch, record = _publisher(sheets=[STAT], sheet_rows=STAT_ROWS)
    assert _publish(rules, ch) == 2
    assert _inserted(record) == [
        ["USD", "JPY", 2099, 12, "Average", 0.007, "RS-S-2099-12", "ACTUAL"],
        ["USD", "JPY", 2099, 12, "Closing", 0.0071, "RS-S-2099-12", "ACTUAL"]]
    assert record["stamped"] == [(MAIN, 2, "2100-01-02 08:00:00")]


def test_the_statutory_set_is_whatever_set_is_flagged_actuals():
    rules, ch, record = _publisher(sheets=[STAT], sheet_rows=STAT_ROWS, actuals=("BASE",))
    _publish(rules, ch)
    assert {r[7] for r in _inserted(record)} == {"BASE"}


def test_no_actuals_set_fails_the_publish_visibly_and_keeps_the_old_table():
    rules, ch, record = _publisher(sheets=[STAT], sheet_rows=STAT_ROWS, actuals=())
    assert _publish(rules, ch) is None
    assert [f[:2] for f in record["failed"]] == [(MAIN, "publish_failed")]
    assert "Is Actuals" in record["failed"][0][2]
    assert not any(q.startswith("EXCHANGE") for q in record["ch"]), "the old set stays"


def test_a_constant_sheet_publishes_both_types_for_every_declared_period():
    """Opening P0 and Regular P1..P12; the Closing P13 is read at P12 by the
    warehouse's rate_period_map, so it has no rows of its own."""
    rules, ch, record = _publisher(sheets=[CONST], sheet_rows=CONST_ROWS)
    assert _publish(rules, ch) == 13 * 2
    rows = _inserted(record)
    assert sorted({r[3] for r in rows}) == list(range(0, 13))
    for p in range(0, 13):
        assert sorted(r[4] for r in rows if r[3] == p) == ["Average", "Closing"], p
    assert {(r[0], r[1], r[2], r[5], r[6], r[7]) for r in rows} == {("USD", "EUR", 2099, 1.1, "RS-C-2099", "CONSTANT")}


def test_only_the_calendar_of_the_sheet_year_counts():
    cal = _cal() + [{"fiscal_year": 2100, "period": 1, "type": "Regular",
                     "start_date": datetime.date(2100, 1, 1), "end_date": datetime.date(2100, 1, 31)}]
    rules, ch, record = _publisher(sheets=[CONST], sheet_rows=CONST_ROWS, calendar=cal)
    _publish(rules, ch)
    assert {r[2] for r in _inserted(record)} == {2099}


# -- drafts and cancelled sheets -------------------------------------------------------------

def test_drafts_and_cancelled_sheets_publish_nothing():
    draft = _sheet("RS-S-2099-11", "Statutory", 2099, 11, docstatus=0)
    cancelled = _sheet("RS-C-2099", "Constant", 2099, docstatus=2)
    rows = [_srow("RS-S-2099-11", "JPY", "USD", 0.7, 0.7, "100")] + CONST_ROWS
    rules, ch, record = _publisher(sheets=[draft, cancelled], sheet_rows=rows)
    assert _publish(rules, ch) == 0
    assert _inserted(record) == [], "no sheet is approved"


def test_a_sheet_that_is_not_approved_publishes_nothing_even_if_handed_one():
    rules, _, _ = _publisher()
    draft = dict(STAT, docstatus=0)
    assert rules.sheet_published_rows([draft], STAT_ROWS, _cal(), "ACTUAL") == []


# -- both bases ------------------------------------------------------------------------------

def test_statutory_and_constant_sheets_publish_side_by_side_one_row_per_key():
    rules, ch, record = _publisher(sheets=[STAT, CONST], sheet_rows=STAT_ROWS + CONST_ROWS)
    _publish(rules, ch)
    rows = _inserted(record)
    by_key = {}
    for r in rows:
        assert _key(r) not in by_key, f"one row per key: {_key(r)}"
        by_key[_key(r)] = r
    assert by_key[("JPY", "USD", 2099, 12, "Closing", "ACTUAL")][5:7] == [0.0071, "RS-S-2099-12"]
    assert by_key[("JPY", "USD", 2099, 12, "Average", "ACTUAL")][5:7] == [0.007, "RS-S-2099-12"]
    assert by_key[("EUR", "USD", 2099, 11, "Closing", "CONSTANT")][6] == "RS-C-2099"
    assert len(rows) == 2 + 26   # 2 statutory, 26 constant


def test_a_superseded_sheet_gives_way_to_the_sheet_that_supersedes_it():
    """#312-3: a correction supersedes the approved sheet for its key. Whatever
    the superseded sheet's docstatus, only the newer one publishes."""
    new = _sheet("RS-S-2099-12-2", "Statutory", 2099, 12, supersedes="RS-S-2099-12")
    rows = STAT_ROWS + [_srow("RS-S-2099-12-2", "JPY", "USD", 0.80, 0.81, "100")]
    rules, ch, record = _publisher(sheets=[STAT, new], sheet_rows=rows)
    _publish(rules, ch)
    assert {r[6] for r in _inserted(record)} == {"RS-S-2099-12-2"}
    assert len(_inserted(record)) == 2


def test_two_approved_sheets_for_one_key_fail_the_publish_by_name():
    """Never a silent pick: the controller refuses a second approved sheet for
    a key, so two here is a race or a patch, and the publish says so."""
    twin = _sheet("RS-S-2099-12-1", "Statutory", 2099, 12)
    rows = STAT_ROWS + [_srow("RS-S-2099-12-1", "JPY", "USD", 0.80, 0.81, "100")]
    rules, ch, record = _publisher(sheets=[STAT, twin], sheet_rows=rows)
    assert _publish(rules, ch) is None
    assert [f[:2] for f in record["failed"]] == [(MAIN, "publish_failed")]
    assert "RS-S-2099-12" in record["failed"][0][2] and "RS-S-2099-12-1" in record["failed"][0][2]


def test_an_approved_sheet_the_rules_refuse_fails_the_publish_by_name():
    """A calendar edited after approval (P12 no longer declared): nothing is
    published from a sheet the rules refuse, and the old set stays."""
    cal = [r for r in _cal() if r["period"] != 12]
    rules, ch, record = _publisher(sheets=[STAT], sheet_rows=STAT_ROWS, calendar=cal)
    assert _publish(rules, ch) is None
    assert "RS-S-2099-12" in record["failed"][0][2]
    assert not any(q.startswith("EXCHANGE") for q in record["ch"])


def test_the_publish_reads_only_the_sheets():
    src = open(RULES).read()
    assert "union_rows" not in src and "_APPROVED_SQL" not in src, "the union went with the retired doctype"
