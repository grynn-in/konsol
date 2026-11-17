"""Budget S6 KC3 (konsol#348 FC-4-2): the Forecast Accuracy Desk report.

FC-4-2 (13 Nov 2026): a materialised accuracy model, every approved round x
closed period x lag, signed and absolute error, against current (restated)
actuals. konsolidat's ``gold_forecast_accuracy`` (WC2) holds it in entity
currency; this report lays it out, every round side by side (the Excel read
takes the declared round only).

The report:
- opens with ``frappe.only_for`` on a literal role tuple and writes nothing
  (it runs on a GET);
- reads ClickHouse once, through ``ch_read.rows``, with every filter bound as
  a query parameter;
- never names an entity outside the user's scope (``allowed_entity_codes``):
  a scoped user's read is bound to their entities, a filter naming another is
  refused without echoing it, and no row of another entity is shown;
- shows "n/a" where the error % has no actual to divide by.

Host-run under a stub frappe and a fake ch_read.
"""
import ast
import importlib.util
import json
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(APP_DIR, "epm", "report", "forecast_accuracy")
REPORT_PY = os.path.join(REPORT_DIR, "forecast_accuracy.py")
REPORT_JSON = os.path.join(REPORT_DIR, "forecast_accuracy.json")
REPORT_JS = os.path.join(REPORT_DIR, "forecast_accuracy.js")
SIDEBAR = os.path.join(APP_DIR, "epm", "sidebar", "planning", "planning.json")
WORKSPACE = os.path.join(APP_DIR, "epm", "workspace", "planning", "planning.json")

NAME = "Forecast Accuracy"
ROLES = {"EPM Admin", "EPM Analyst", "EPM User", "Entity Accountant"}

#: The model's rows as ch_read returns them (JSON: numbers may be strings).
WAREHOUSE = [
    {"scenario_id": "ZZ_FC", "cycle": "ZZ-R2", "as_of_date": "2026-06-30", "fiscal_year": 2026,
     "fiscal_period": 7, "lag": 1, "data_area_id": "ZZ01", "main_account": "ZZ4000", "currency": "EUR",
     "forecast_amount": "-110", "actual_amount": "-100", "forecast_error": "-10",
     "forecast_abs_error": "10"},
    {"scenario_id": "ZZ_FC", "cycle": "ZZ-R2", "as_of_date": "2026-06-30", "fiscal_year": 2026,
     "fiscal_period": 8, "lag": 2, "data_area_id": "ZZ01", "main_account": "ZZ6000", "currency": "EUR",
     "forecast_amount": "40", "actual_amount": "0", "forecast_error": "40",
     "forecast_abs_error": "40"},
    {"scenario_id": "ZZ_FC", "cycle": "ZZ-R2", "as_of_date": "2026-06-30", "fiscal_year": 2026,
     "fiscal_period": 7, "lag": 1, "data_area_id": "ZZ02", "main_account": "ZZ4000", "currency": "USD",
     "forecast_amount": "-55", "actual_amount": "-50", "forecast_error": "-5",
     "forecast_abs_error": "5"},
]


class _Dict(dict):
    def __getattr__(self, k):
        return self.get(k)


class _Denied(Exception):
    pass


class _Site:
    def __init__(self, allowed=None, rows=WAREHOUSE, fail=None):
        self.allowed = allowed  # None = unrestricted
        self.rows = rows
        self.fail = fail
        self.reads = []
        self.only_for = []
        self.writes = []

    def run(self, filters=None):
        site = self
        frappe = types.ModuleType("frappe")
        frappe._dict = lambda d=None: _Dict(d or {})
        frappe.PermissionError = _Denied

        def only_for(roles, message=False):
            site.only_for.append(roles)

        def throw(msg, exc=Exception, *a, **k):
            raise (exc if isinstance(exc, type) else Exception)(msg)

        def forbidden(*a, **k):
            site.writes.append(a)
            raise AssertionError("the report writes nothing")

        frappe.only_for = only_for
        frappe.throw = throw
        frappe.db = types.SimpleNamespace(set_value=forbidden, sql=forbidden, commit=forbidden)
        frappe.whitelist = lambda *a, **k: (lambda f: f)

        def rows(sql, params=None, settings=None, timeout=None):
            site.reads.append((sql, dict(params or {})))
            if site.fail:
                raise site.fail
            out = list(site.rows)
            ents = params.get("entities")
            if ents is not None:
                keep = set(re.findall(r"'([^']*)'", ents))
                out = [r for r in out if r["data_area_id"] in keep]
            for key, col in (("entity", "data_area_id"), ("scenario", "scenario_id"),
                             ("cycle", "cycle"), ("lag", "lag")):
                if key in params:
                    out = [r for r in out if str(r[col]) == str(params[key])]
            # the SELECT's aliases (the report sums the dimensions away)
            alias = {"as_of_date": "as_of", "forecast_amount": "fc", "actual_amount": "act",
                     "forecast_error": "err", "forecast_abs_error": "abs_err"}
            return [{alias.get(k, k): v for k, v in r.items()} for r in out]

        ch_read = types.ModuleType("konsol.close.ch_read")
        ch_read.rows = rows
        ch_read.not_built = lambda e: "UNKNOWN_TABLE" in str(e)
        perms = types.ModuleType("konsol.entity_permissions")
        perms.allowed_entity_codes = lambda user=None: (None if site.allowed is None else set(site.allowed))

        import konsol
        import konsol.close
        names = ("frappe", "konsol.close.ch_read", "konsol.entity_permissions")
        saved = {k: sys.modules.get(k) for k in names}
        saved_attrs = (getattr(konsol.close, "ch_read", None), getattr(konsol, "entity_permissions", None))
        sys.modules.update({"frappe": frappe, "konsol.close.ch_read": ch_read,
                            "konsol.entity_permissions": perms})
        konsol.close.ch_read = ch_read
        konsol.entity_permissions = perms
        try:
            spec = importlib.util.spec_from_file_location("forecast_accuracy_t", REPORT_PY)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            out = m.execute(filters)
        finally:
            for k, v in saved.items():
                if v is None:
                    sys.modules.pop(k, None)
                else:
                    sys.modules[k] = v
            for mod, attr, v in ((konsol.close, "ch_read", saved_attrs[0]),
                                 (konsol, "entity_permissions", saved_attrs[1])):
                if v is None:
                    if hasattr(mod, attr):
                        delattr(mod, attr)
                else:
                    setattr(mod, attr, v)
        columns, data = out[0], out[1]
        return columns, data, (out[2] if len(out) > 2 else None)


def test_rows_with_error_and_na_for_a_zero_actual():
    site = _Site()
    columns, data, _ = site.run({})
    assert [(r["cycle"], r["period"], r["lag"], r["data_area_id"], r["main_account"]) for r in data] == [
        ("ZZ-R2", "FY2026 P7", 1, "ZZ01", "ZZ4000"),
        ("ZZ-R2", "FY2026 P8", 2, "ZZ01", "ZZ6000"),
        ("ZZ-R2", "FY2026 P7", 1, "ZZ02", "ZZ4000"),
    ]
    first, zero = data[0], data[1]
    assert (first["forecast_amount"], first["actual_amount"], first["forecast_error"],
            first["forecast_abs_error"]) == (-110.0, -100.0, -10.0, 10.0)
    assert first["forecast_error_pct"] == "-10.00%"  # error / |actual| x 100
    assert zero["forecast_error_pct"] == "n/a"
    assert first["as_of_date"] == "2026-06-30" and first["currency"] == "EUR"
    fields = [c["fieldname"] for c in columns]
    assert fields == ["scenario_id", "cycle", "as_of_date", "period", "lag", "data_area_id",
                      "main_account", "currency", "forecast_amount", "actual_amount",
                      "forecast_error", "forecast_abs_error", "forecast_error_pct"]
    assert site.writes == []


def test_one_read_through_ch_read_with_bound_filters():
    site = _Site()
    site.run({"scenario_id": "ZZ_FC", "cycle": "ZZ-R2", "entity": "ZZ01", "lag": 1})
    (sql, params) = site.reads[0]
    assert len(site.reads) == 1
    assert "FROM epm_gold.gold_forecast_accuracy " in sql
    for holder in ("{scenario:String}", "{cycle:String}", "{entity:String}", "{lag:UInt16}"):
        assert holder in sql, holder
    assert params == {"scenario": "ZZ_FC", "cycle": "ZZ-R2", "entity": "ZZ01", "lag": 1}
    assert "ZZ_FC" not in sql and "ZZ01" not in sql  # bound, never interpolated
    assert "ORDER BY" in sql


def test_lag_zero_is_a_filter_not_blank():
    site = _Site()
    site.run({"lag": 0})
    assert site.reads[0][1].get("lag") == 0


def test_opens_with_a_literal_only_for_and_writes_nothing():
    with open(REPORT_PY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "execute")
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    first = body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    assert ast.unparse(first.value.func) == "frappe.only_for"
    roles = ast.literal_eval(first.value.args[0])
    assert set(roles) == ROLES | {"System Manager"}
    with open(REPORT_PY, encoding="utf-8") as fh:
        source = fh.read()
    for write in (".insert(", ".save(", ".submit(", ".cancel(", "db.set_value", "db.commit",
                  "frappe.enqueue", "delete_doc", "db.sql"):
        assert write not in source, write
    site = _Site()
    site.run({})
    assert site.only_for and set(site.only_for[0]) == ROLES | {"System Manager"}


# ── an Entity Accountant's scope ────────────────────────────────────────────

def test_a_scoped_user_reads_only_their_entities():
    site = _Site(allowed={"ZZ01"})
    _, data, message = site.run({})
    assert {r["data_area_id"] for r in data} == {"ZZ01"}
    (sql, params) = site.reads[0]
    assert "data_area_id IN {entities:Array(String)}" in sql
    assert params["entities"] == "['ZZ01']"
    assert "ZZ02" not in json.dumps([data, message, sql, params], default=str)


def test_a_scoped_user_naming_another_entity_is_refused_without_naming_it():
    site = _Site(allowed={"ZZ01"})
    try:
        site.run({"entity": "ZZ02"})
    except _Denied as e:
        assert "ZZ02" not in str(e), str(e)
    else:
        raise AssertionError("refused")
    assert site.reads == []


def test_a_user_with_no_entity_reads_nothing():
    site = _Site(allowed=set())
    _, data, message = site.run({})
    assert data == [] and site.reads == []
    assert message and "no entity" in message.lower()


# ── states ──────────────────────────────────────────────────────────────────

def test_not_built_is_a_message_not_an_empty_table():
    site = _Site(fail=Exception("Code: 60. DB::Exception: (UNKNOWN_TABLE)"))
    _, data, message = site.run({})
    assert data == []
    assert message and "not been built" in message


def test_any_other_failure_is_raised_not_read_as_no_rows():
    site = _Site(fail=Exception("Code: 241. (MEMORY_LIMIT_EXCEEDED)"))
    try:
        site.run({})
    except Exception as e:  # noqa: BLE001
        assert "could not be read" in str(e)
    else:
        raise AssertionError("raised")


def test_no_rows_says_why():
    _, data, message = _Site(rows=[]).run({})
    assert data == []
    assert message and "closed period" in message


# ── the Desk wiring ─────────────────────────────────────────────────────────

def test_report_json():
    with open(REPORT_JSON, encoding="utf-8") as fh:
        r = json.load(fh)
    assert r["doctype"] == "Report" and r["name"] == r["report_name"] == NAME
    assert r["report_type"] == "Script Report" and r["is_standard"] == "Yes"
    assert r["module"] == "EPM"
    assert r["ref_doctype"] == "Budget Cycle"
    assert {x["role"] for x in r["roles"]} == ROLES


def test_report_js_registers_the_filters():
    with open(REPORT_JS, encoding="utf-8") as fh:
        js = fh.read()
    assert f'frappe.query_reports["{NAME}"]' in js
    for field in ('"scenario_id"', '"cycle"', '"entity"', '"lag"'):
        assert field in js, field
    assert '"Budget Cycle"' in js and '"Entity"' in js and '"Scenario"' in js
    assert "frappe.call" not in js and "frappe.xcall" not in js


def test_linked_from_the_forecast_section_of_the_sidebar_and_workspace():
    with open(SIDEBAR, encoding="utf-8") as fh:
        sidebar = json.load(fh)
    links = [(i.get("link_type"), i.get("link_to")) for i in sidebar["items"]]
    assert ("Report", NAME) in links
    assert links.index(("Report", NAME)) == links.index(("Report", "Forecast Horizon Coverage")) + 1
    assert sidebar["modified"] > "2026-11-17 21:00:00.000000"
    with open(WORKSPACE, encoding="utf-8") as fh:
        ws = json.load(fh)
    assert any(s["type"] == "Report" and s["link_to"] == NAME and s["label"] == NAME for s in ws["shortcuts"])
    assert NAME in ws["content"]
    assert ws["modified"] > "2026-11-17 21:00:00.000000"
