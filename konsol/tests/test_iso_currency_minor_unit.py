"""konsol#330: ISO Currency.minor_unit is the shipped ISO 4217 list's, never a default.

Decided by Deepak Pai, 11 Oct 2026 (option #330-1): the shipped ISO 4217 list
(konsol/reference_data/iso_currencies.json) is the authority for
``ISO Currency.minor_unit``. A row whose code is on the list must carry the
list's minor unit, and a code not on the list is refused (ISO 4217 is a closed
list). Rejected: #330-2 (a required Select with no default) and #330-3 (as is).

The defect: the column is ``int NOT NULL DEFAULT 0``, so a currency added with
Minor Unit left blank was stored as 0 and judged in whole units by every reader
(trial-balance balance, konsol#180). The rule is ``konsol/iso_currency_model.py``
(pure, loaded by path here); the ISO Currency controller refuses on save, and
the seed reports a stored row that disagrees, so an existing site sees it.
"""
import ast
import importlib.util
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
MODEL = os.path.join(_APP, "iso_currency_model.py")
LIST = os.path.join(_APP, "reference_data", "iso_currencies.json")
CONTROLLER = os.path.join(_APP, "epm", "doctype", "iso_currency", "iso_currency.py")


def _model():
    spec = importlib.util.spec_from_file_location("iso_currency_model_under_test", MODEL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _listed():
    with open(LIST) as f:
        return {r["currency_code"]: r["minor_unit"] for r in json.load(f)}


def test_a_listed_code_with_its_list_minor_unit_is_accepted():
    m = _model()
    listed = _listed()
    for code, unit in (("USD", 2), ("JPY", 0), ("KWD", 3), ("BHD", 3), ("VND", 0)):
        assert m.minor_unit_problem(code, unit, listed) is None, code


def test_a_blank_minor_unit_is_refused_not_read_as_zero():
    """The defect: blank was stored as 0. For a 2- or 3-decimal currency that
    0 disagrees with the list and is refused, naming the list's value."""
    m = _model()
    listed = _listed()
    for blank in (None, "", 0):
        problem = m.minor_unit_problem("KWD", blank, listed)
        assert problem and "KWD" in problem and "3" in problem, (blank, problem)
    assert "2" in m.minor_unit_problem("EUR", None, listed)


def test_a_minor_unit_that_disagrees_with_the_list_is_refused():
    m = _model()
    listed = _listed()
    problem = m.minor_unit_problem("JPY", 2, listed)
    assert problem and "JPY" in problem and "0" in problem
    assert m.minor_unit_problem("USD", 3, listed)
    assert m.minor_unit_problem("USD", "2", listed) is None, "a form sends the Int as text"
    assert m.minor_unit_problem("USD", "two", listed), "not a whole number"


def test_a_code_not_on_the_list_is_refused():
    m = _model()
    listed = _listed()
    for code in ("ZZX", "usd", "", None):
        problem = m.minor_unit_problem(code, 2, listed)
        assert problem and "ISO 4217" in problem, (code, problem)


def test_the_model_is_pure():
    with open(MODEL) as f:
        tree = ast.parse(f.read())
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not imported & {"frappe", "konsol"}, imported


def test_the_controller_refuses_on_save():
    """validate() runs the rule against the shipped list and throws."""
    with open(CONTROLLER) as f:
        tree = ast.parse(f.read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ISOCurrency")
    validate = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "validate")
    src = ast.unparse(validate)
    assert "minor_unit_problem" in src and "frappe.throw" in src, src


def test_the_seed_reports_a_stored_row_that_disagrees():
    """An existing site keeps its rows (the seed never overwrites), but a row
    that the rule would refuse is named at migrate, never silently kept."""
    import sys
    import types

    path = os.path.join(_APP, "currency_references.py")
    frappe = types.ModuleType("frappe")
    stored = {"USD": (0.0, 2), "KWD": (-0.51, 0), "ZZX": (1.0, 2)}
    frappe.get_all = lambda doctype, fields=None, **k: [
        types.SimpleNamespace(name=c, usd_log10=v, minor_unit=u) for c, (v, u) in stored.items()]
    frappe.db = types.SimpleNamespace(set_value=lambda *a, **k: None)

    class Doc(dict):
        def insert(self, **k):
            pass
    frappe.get_doc = Doc
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("currency_references_330", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.seed_iso_currencies()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    assert sorted(out["refused"]) == ["KWD", "ZZX"], out
