"""The ISO list konsol ships and its magnitude references (konsol#103, review of #174).

usd_log10 used to ride in the ISO Currency fixture. Frappe force re-imports
konsol/fixtures/ on every migrate, deleting and re-inserting every row, so a
site's own value was reverted each time. The list is now seeded: a code a site
lacks is inserted, a value is filled only where it is unset, and a value that
is set is never touched. The seed runs in the adoption patch, after_migrate
and after_sync. The module runs here against a stub frappe."""
import ast
import importlib.util
import json
import math
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULE = os.path.join(APP_DIR, "currency_references.py")
CASES = os.path.join(APP_DIR, "tests", "fx_magnitude_cases.json")


def _module(rows=()):
    """konsol.currency_references against a stub frappe holding ``rows``
    ({code: usd_log10}); returns (module, record of writes)."""
    record = {"set": [], "inserted": []}
    frappe = types.ModuleType("frappe")
    frappe.get_all = lambda doctype, fields=None, **k: [
        types.SimpleNamespace(name=c, usd_log10=v) for c, v in dict(rows).items()]
    frappe.db = types.SimpleNamespace(
        set_value=lambda dt, name, field, value, update_modified=True:
            record["set"].append((name, field, value, update_modified)))

    class Doc(dict):
        def insert(self, **k):
            record["inserted"].append(dict(self))
    frappe.get_doc = Doc
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("currency_references_under_test", MODULE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return module, record


def test_the_shipped_list():
    m, _ = _module()
    rows = m.reference_rows()
    by_code = {r["currency_code"]: r for r in rows}
    assert len(rows) == len(by_code) == 69, "the seed's 66 ISO codes and three dollar pegs"
    assert all(set(m.FIELDS) <= set(r) for r in rows)
    unset = sorted(c for c, r in by_code.items() if m.is_unset(c, r["usd_log10"]))
    assert not unset, unset
    assert all(-5 <= r["usd_log10"] <= 10 for r in rows)
    for code, value in {"USD": 0, "EUR": -0.03, "JPY": 2.17, "KRW": 3.13, "IDR": 4.21, "VND": 4.40}.items():
        assert abs(by_code[code]["usd_log10"] - value) < 0.005, code
    # pegged 1:1 to the dollar: truly 0, which reads as unset, so a negligible 0.001
    for code in ("PAB", "BSD", "BMD"):
        assert by_code[code]["usd_log10"] == 0.001 and by_code[code]["minor_unit"] == 2, code
    assert (by_code["JPY"]["minor_unit"], by_code["KWD"]["minor_unit"]) == (0, 3)


def test_unset_is_the_warehouse_rule():
    """One rule (konsol.fx_reference), checked against the shared case table's
    expectations: a case with a valid rate is "no_reference" exactly when one
    of its currencies' references is unset. NULL, NaN, or 0 for any currency
    but USD."""
    from konsol import fx_reference as rule

    m, _ = _module()
    assert m.is_unset is rule.is_unset and m.REFERENCE_CURRENCY == rule.REFERENCE_CURRENCY == "USD"
    with open(CASES) as f:
        cases = json.load(f)["cases"]
    checked = 0
    for case in cases:
        refs = ((case["from"], float(case["from_log10"])), (case["to"], float(case["to_log10"])))
        for code, value in refs:
            assert rule.is_unset(code, value) == (rule.usd_reference(code, value) is None), (code, value)
        rate = float(case["rate"])
        if math.isfinite(rate) and rate > 0:   # invalid is checked before references
            assert any(rule.is_unset(c, v) for c, v in refs) == (case["expected"] == "no_reference"), case["note"]
            checked += 1
    assert checked == 10
    assert rule.is_unset("EUR", None) and rule.is_unset("EUR", "") and not rule.is_unset("USD", 0.0)
    assert not rule.is_unset("PAB", 0.001) and rule.usd_reference("EUR", "-0.03") == -0.03


def test_a_value_that_is_set_is_never_overwritten():
    """A site's own usd_log10 survives: the seed fills only what is unset."""
    m, record = _module({"EUR": -0.04, "USD": 0.0, "JPY": 0.0, "IDR": None, "VND": float("nan")})
    out = m.seed_iso_currencies()
    filled = {name: value for name, field, value, _ in record["set"]}
    assert "EUR" not in filled, "the site's edit"
    assert "USD" not in filled, "USD's 0 is set"
    assert filled == {"JPY": 2.17, "IDR": 4.21, "VND": 4.4}
    assert all(field == "usd_log10" and not bump for _, field, _, bump in record["set"]), \
        "a seed is not an edit: modified is left alone"
    assert sorted(out["filled"]) == ["IDR", "JPY", "VND"]
    # every other shipped code is missing from this stub site, so it is inserted, whole
    inserted = {d["currency_code"]: d for d in record["inserted"]}
    assert len(inserted) == 69 - 5 and "PAB" in inserted and "EUR" not in inserted
    assert inserted["PAB"]["usd_log10"] == 0.001 and inserted["PAB"]["doctype"] == "ISO Currency"
    # a second run changes nothing more than the first left to do
    m2, record2 = _module({"EUR": -0.04, "JPY": 2.17})
    m2.seed_iso_currencies(rows=[r for r in m2.reference_rows() if r["currency_code"] in ("EUR", "JPY")])
    assert record2["set"] == [] and record2["inserted"] == []


def test_iso_currency_is_seeded_not_a_fixture():
    """A fixture is force re-imported on every migrate (Frappe's import_doc
    passes force=True), which is what reverted a site's usd_log10."""
    assert not os.path.exists(os.path.join(APP_DIR, "fixtures", "iso_currency.json"))
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    fixtures = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                    and getattr(n.targets[0], "id", "") == "fixtures")
    assert "ISO Currency" not in fixtures


def _calls(fn_name):
    with open(os.path.join(APP_DIR, "install.py")) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == fn_name)
    return [ast.unparse(s.value.func) for s in fn.body if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)]


def test_the_seed_runs_before_the_warehouse_is_filled():
    """after_migrate seeds before its reconcile publishes epm_gold.currencies;
    a fresh install (after_sync) seeds before it queues its reconcile."""
    calls = _calls("after_migrate")
    assert calls.index("_seed_iso_currencies") < calls.index("_reconcile_clickhouse")
    calls = _calls("after_sync")
    assert calls.index("_seed_iso_currencies") < calls.index("enqueue_reconcile_after_commit")
