"""Historical Equity Rate says which currency to which (5 Nov 2026).

Deepak Pai, 5 Nov 2026, on demo.konsolidat.com: the Historical Equity Rate
form "doesnt show rate what to what". The rate is the entity's functional
currency -> the group's reporting currency, read as 1 From = rate To: the
warehouse multiplies the entity's local amount by it
(konsolidat dbt gold_consolidated_trial_balance, `translation_rate`: "local x
translation_rate"), the same direction as the group's closing and average
rates. The form now shows both currencies as virtual (computed, never stored)
fields, so there is no second copy that could disagree with Entity or
Consolidation Group.
"""
import ast
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HER_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "historical_equity_rate")
HER_JSON = os.path.join(HER_DIR, "historical_equity_rate.json")
HER_PY = os.path.join(HER_DIR, "historical_equity_rate.py")
HER_JS = os.path.join(HER_DIR, "historical_equity_rate.js")


def _fields():
    with open(HER_JSON) as f:
        return {d["fieldname"]: d for d in json.load(f)["fields"]}


def test_both_currencies_are_virtual_read_only_iso_currency_fields():
    fields = _fields()
    for name, label in (("from_currency", "From Currency"), ("to_currency", "To Currency")):
        df = fields[name]
        assert df["fieldtype"] == "Link" and df["options"] == "ISO Currency", name
        assert df.get("is_virtual") == 1 and df.get("read_only") == 1, name
        assert df["label"] == label


def test_the_rate_label_states_the_direction():
    df = _fields()["historical_rate"]
    assert "1 From = rate To" in df["label"], df["label"]
    assert "multiplies" in df["description"]


def test_the_controller_computes_them_as_properties():
    tree = ast.parse(open(HER_PY).read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HistoricalEquityRate")
    props = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)
             and any(ast.unparse(d) == "property" for d in n.decorator_list)}
    assert {"from_currency", "to_currency"} <= props, props


def _controller(values):
    """The real controller class under a stub frappe whose db.get_value answers
    from ``values``: {(doctype, frozen filters or name, field): value}."""
    calls = []
    frappe = types.ModuleType("frappe")

    def get_value(doctype, filters, fieldname=None, **k):
        key = (doctype, json.dumps(filters, sort_keys=True) if isinstance(filters, dict) else filters, fieldname)
        calls.append(key)
        return values.get(key)

    frappe.db = types.SimpleNamespace(get_value=get_value)
    frappe.throw = lambda *a, **k: (_ for _ in ()).throw(Exception(a[0]))
    frappe.ValidationError = Exception
    document = types.ModuleType("frappe.model.document")

    class Document:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    document.Document = Document
    model = types.ModuleType("frappe.model")
    stubs = {
        "frappe": frappe, "frappe.model": model, "frappe.model.document": document,
        "konsol": types.ModuleType("konsol"),
        "konsol.clickhouse": types.SimpleNamespace(sync_doctype_after_commit=lambda *a, **k: None),
        "konsol.period_status": types.SimpleNamespace(assert_open_between=None, first_period_affected=None),
        "konsol.epm": types.ModuleType("konsol.epm"),
        "konsol.epm.budget_grain": types.SimpleNamespace(digest_name=lambda *a: "HER-ZZ"),
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("her_controller_under_test", HER_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod.HistoricalEquityRate, calls


ROOT = json.dumps({"consolidation_group": "ZZ_GROUP", "data_area_id": ["is", "not set"]}, sort_keys=True)


def test_from_is_the_entitys_functional_currency_and_to_is_the_group_roots_reporting_currency():
    HER, calls = _controller({
        ("Entity", "ZZNL", "functional_currency"): "EUR",
        ("Consolidation Group", ROOT, "reporting_currency"): "USD",
    })
    doc = HER(consolidation_group="ZZ_GROUP", data_area_id="ZZNL")
    assert (doc.from_currency, doc.to_currency) == ("EUR", "USD")
    # The root node (no entity) carries the group's reporting currency, as in
    # consolidation_journal._root.
    assert ("Consolidation Group", ROOT, "reporting_currency") in calls


def test_a_blank_key_reads_nothing_and_shows_nothing():
    HER, calls = _controller({})
    doc = HER(consolidation_group=None, data_area_id=None)
    assert (doc.from_currency, doc.to_currency) == (None, None)
    assert calls == []


def test_frappe_writing_the_value_does_not_break_the_property():
    """Frappe loads a doc by updating __dict__; the class property still wins."""
    HER, _ = _controller({("Entity", "ZZNL", "functional_currency"): "EUR"})
    doc = HER(data_area_id="ZZNL", consolidation_group=None, from_currency="XXX")
    assert doc.from_currency == "EUR"


def test_the_form_heads_the_record_with_the_pair():
    js = open(HER_JS).read()
    assert "from_currency" in js and "to_currency" in js
    assert "set_intro" in js


def test_a_same_currency_rate_is_said_to_be_unused():
    """#373 review: when From equals To the warehouse translates at 1.0 and never
    reads this rate (gold_consolidated_trial_balance: the same-currency case
    comes before the historical one), so the intro must not claim
    "1 USD = 0.92 USD"."""
    js = open(HER_JS).read()
    assert "d.from_currency === d.to_currency" in js
    branch = js[js.index("d.from_currency === d.to_currency"):]
    assert "not used" in branch.split("return")[0]
