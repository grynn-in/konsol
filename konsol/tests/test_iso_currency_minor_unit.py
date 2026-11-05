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
        types.SimpleNamespace(name=c, usd_log10=v, minor_unit=u, minor_unit_declared=0)
        for c, (v, u) in stored.items()]
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


# --- PR #384 review: the full ISO 4217 list, a declared escape, a visible gap ---
#
# The list was 88 codes; ISO 4217 List One has 178. A real currency (JOD, LKR,
# XOF ...) could not be created. Source: SIX Financial Information, ISO 4217
# Maintenance Agency, "List One: Currency, fund and precious metal codes",
# published 2026-09-17 (https://www.six-group.com/dam/download/financial-
# information/data-center/iso-currrency/lists/list-one.xml). Two codes konsol
# already shipped are kept as withdrawn, for history: ANG (withdrawn 2025-03)
# and BGN (2026-01), List Three; their minor unit 2 is List One of 2024-06-25.
# A code List One gives "N.A." (no minor unit: XAU, XDR ...) is listed with
# None and is never seeded: konsol stores no value ISO does not give.

DECLARE = "Minor Unit Not Set by ISO 4217"
NO_UNIT = ["XAG", "XAU", "XBA", "XBB", "XBC", "XBD", "XDR", "XPD", "XPT", "XSU", "XTS", "XUA", "XXX"]


def _rows():
    with open(LIST) as f:
        return json.load(f)


def test_the_list_is_iso_4217_list_one_in_full():
    rows = _rows()
    codes = [r["currency_code"] for r in rows]
    assert len(codes) == len(set(codes)) == 180, "178 List One codes plus 2 withdrawn"
    assert {r["currency_code"]: r["withdrawn"] for r in rows if "withdrawn" in r} == \
        {"ANG": "2025-03", "BGN": "2026-01"}
    units = _listed()
    for code, unit in {"JOD": 3, "JPY": 0, "KWD": 3, "CLF": 4, "USD": 2, "LKR": 2, "NPR": 2,
                       "IQD": 3, "LYD": 3, "TND": 3, "OMR": 3, "XOF": 0, "XAF": 0, "UYW": 4,
                       "GHS": 2, "MUR": 2, "PEN": 2, "IRR": 2, "KHR": 2, "BAM": 2, "MKD": 2,
                       "ISK": 0, "VUV": 0, "ANG": 2, "BGN": 2, "XCG": 2}.items():
        assert units[code] == unit, (code, units.get(code))
    assert sorted(c for c, u in units.items() if u is None) == NO_UNIT


def test_a_code_iso_gives_no_minor_unit_must_be_declared():
    m = _model()
    listed = _listed()
    problem = m.minor_unit_problem("XDR", 0, listed)
    assert problem and "XDR" in problem and DECLARE in problem, problem
    assert m.minor_unit_problem("XDR", 4, listed, declared=True) is None
    assert m.minor_unit_problem("XAU", 0, listed, declared=True) is None, "0 entered on purpose"


def test_an_off_list_code_may_be_declared_with_its_minor_unit():
    """A crypto-asset or internal unit: declared, never defaulted."""
    m = _model()
    listed = _listed()
    problem = m.minor_unit_problem("USDT", 6, listed)
    assert problem and DECLARE in problem, "the refusal names the escape"
    assert m.minor_unit_problem("USDT", 6, listed, declared=True) is None
    assert m.minor_unit_problem("ZZPTS", 0, listed, declared=True) is None


def test_a_declared_minor_unit_must_be_entered_and_plausible():
    m = _model()
    listed = _listed()
    for bad in (None, "", -1, 10, "six"):
        problem = m.minor_unit_problem("USDT", bad, listed, declared=True)
        assert problem and "USDT" in problem, (bad, problem)
    for bad_code in ("usdt", "US DT", "", None):
        assert m.minor_unit_problem(bad_code, 2, listed, declared=True), bad_code


def test_a_code_iso_sets_cannot_be_declared_away():
    """The declaration is for what ISO 4217 does not set: never an override."""
    m = _model()
    listed = _listed()
    problem = m.minor_unit_problem("JPY", 2, listed, declared=True)
    assert problem and "JPY" in problem and "0" in problem and DECLARE in problem, problem
    assert m.minor_unit_problem("JPY", 0, listed, declared=True), "even with the right value"


def test_the_gap_names_every_disagreeing_row():
    m = _model()
    listed = _listed()
    used = {"USD": ["Entity"], "USDT": ["Entity"], "XDR": ["Entity"], "EUR": ["Entity"], "ZZX": ["Entity"]}
    assert m.minor_unit_gaps([("USD", 2, 0), ("USDT", 6, 1)], listed, used)["blocking"] is None
    gap = m.minor_unit_gaps([("USD", 0, 0), ("XDR", 0, 0), ("EUR", 2, 0), ("ZZX", 2, 0)], listed,
                            used)["blocking"]
    assert gap["code"] == "iso_currency_minor_unit_disagrees"
    assert gap["currencies"] == ["USD", "XDR", "ZZX"]
    for code in gap["currencies"]:
        assert code in gap["message"]
    assert "ISO 4217" in gap["message"] and "#330" in gap["message"]


def test_the_doctype_carries_the_declaration():
    with open(os.path.join(_APP, "epm", "doctype", "iso_currency", "iso_currency.json")) as f:
        fields = {d["fieldname"]: d for d in json.load(f)["fields"]}
    field = fields["minor_unit_declared"]
    assert field["fieldtype"] == "Check" and field["label"] == DECLARE
    assert field.get("description")
    with open(CONTROLLER) as f:
        src = f.read()
    assert "minor_unit_declared" in src


def _seed(stored):
    import sys
    import types

    path = os.path.join(_APP, "currency_references.py")
    frappe = types.ModuleType("frappe")
    inserted = []
    frappe.get_all = lambda doctype, fields=None, **k: [
        types.SimpleNamespace(name=c, usd_log10=v, minor_unit=u, minor_unit_declared=d)
        for c, (v, u, d) in stored.items()]
    frappe.db = types.SimpleNamespace(set_value=lambda *a, **k: None)

    class Doc(dict):
        def insert(self, **k):
            inserted.append(self["currency_code"])
    frappe.get_doc = Doc
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("currency_references_330b", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.seed_iso_currencies()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return out, inserted


def test_the_seed_never_stores_a_minor_unit_iso_does_not_give():
    out, inserted = _seed({"USD": (0.0, 2, 0)})
    assert not set(inserted) & set(NO_UNIT), "an N.A. code would be stored as 0"
    assert len(inserted) == 180 - 13 - 1
    assert "JOD" in inserted and "BGN" in inserted


def test_the_seed_judges_a_declared_row_as_declared():
    out, _ = _seed({"USD": (0.0, 2, 0), "XDR": (-0.13, 0, 0), "XAU": (-3.4, 0, 1),
                    "USDT": (0.0, 6, 1), "JPY": (2.17, 0, 1)})
    assert out["refused"] == ["JPY", "XDR"], out["refused"]


def test_the_migrate_records_a_disagreeing_row_in_the_error_log():
    """print() is seen by whoever runs the migrate; the Error Log by everyone."""
    with open(os.path.join(_APP, "install.py")) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_seed_iso_currencies")
    src = ast.unparse(fn)
    assert "out['refused']" in src or 'out["refused"]' in src
    calls = [ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "frappe.log_error"]
    assert any("#330" in c and "refused" in c for c in calls), calls


# --- PR #384 re-review: only a USED disagreeing row blocks -------------------
#
# The old shipped list gave XDR minor unit 0, so every existing site holds an
# unticked XDR=0 row, which the new list refuses. Blocking sign-off on every
# disagreeing row would stop every site from signing off after migrate, over a
# currency nothing uses. Principle (Deepak Pai): refuse what makes a number
# wrong (a used row blocks); report what makes it unexplained (an unused row is
# a non-blocking My work item and an Error Log entry); never silently default.

_USED_SOURCES = {
    ("Entity", "functional_currency"),
    ("Consolidation Group", "reporting_currency"),
    ("Group Exchange Rate", "from_currency"),
    ("Group Exchange Rate", "to_currency"),
    ("Trial Balance Submission", "currency"),
    ("Consolidation Journal", "currency"),
    ("Business Combination", "consideration_currency"),
    ("Business Combination Consideration", "currency"),
    ("Business Combination Cost", "currency"),
    ("Business Disposal", "proceeds_currency"),
    ("Business Disposal Proceeds", "currency"),
}


def test_an_unused_disagreeing_row_is_reported_not_blocking():
    m = _model()
    listed = _listed()
    rows = [("USD", 0, 0), ("XDR", 0, 0), ("EUR", 2, 0)]
    gaps = m.minor_unit_gaps(rows, listed, {"USD": ["Entity"], "EUR": ["Entity"]})
    blocking, reported = gaps["blocking"], gaps["reported"]
    assert blocking["code"] == "iso_currency_minor_unit_disagrees"
    assert blocking["currencies"] == ["USD"]
    assert "blocks sign-off" in blocking["message"] and "USD" in blocking["message"]
    assert "Entity" in blocking["message"], "names where it is used"
    assert "XDR" not in blocking["message"]
    assert reported["code"] == "iso_currency_minor_unit_unused"
    assert reported["currencies"] == ["XDR"]
    assert "does not block sign-off" in reported["message"] and "XDR" in reported["message"]
    assert "USD" not in reported["message"]


def test_no_disagreeing_row_is_no_gap_either_way():
    m = _model()
    gaps = m.minor_unit_gaps([("USD", 2, 0)], _listed(), {"USD": ["Entity"]})
    assert gaps == {"blocking": None, "reported": None}


def test_every_disagreeing_row_unused_blocks_nothing():
    m = _model()
    gaps = m.minor_unit_gaps([("XDR", 0, 0), ("ZZX", 2, 0)], _listed(), {})
    assert gaps["blocking"] is None
    assert gaps["reported"]["currencies"] == ["XDR", "ZZX"]


def _references(stored, used):
    """currency_references loaded against a stub frappe. ``used``:
    {(doctype, field): [codes]}; records every get_all's filters."""
    import sys
    import types

    calls = []
    frappe = types.ModuleType("frappe")

    def get_all(doctype, fields=None, filters=None, pluck=None, **k):
        calls.append((doctype, fields, filters, pluck))
        if doctype == "ISO Currency":
            return [types.SimpleNamespace(name=c, usd_log10=0.0, minor_unit=u, minor_unit_declared=d)
                    for c, (u, d) in stored.items()]
        field = pluck or (fields[0] if fields else None)
        return [types.SimpleNamespace(**{field: c}) if not pluck else c
                for c in used.get((doctype, field), [])]
    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(set_value=lambda *a, **k: None)
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = frappe
    try:
        spec = importlib.util.spec_from_file_location("currency_references_330c",
                                                      os.path.join(_APP, "currency_references.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    module.frappe = frappe
    return module, calls


def test_the_old_xdr_zero_row_does_not_block_a_site_that_does_not_use_it():
    """The migrate case: every site holds XDR=0 unticked from the old list."""
    refs, _ = _references({"USD": (2, 0), "XDR": (0, 0)}, {("Entity", "functional_currency"): ["USD"]})
    assert refs.minor_unit_gap() is None
    gaps = refs.minor_unit_gaps()
    assert gaps["blocking"] is None and gaps["reported"]["currencies"] == ["XDR"]


def test_each_use_of_a_disagreeing_currency_blocks():
    for source in sorted(_USED_SOURCES):
        refs, _ = _references({"XDR": (0, 0)}, {source: ["XDR"]})
        gap = refs.minor_unit_gap()
        assert gap is not None and gap["currencies"] == ["XDR"], source
        assert refs.minor_unit_gaps()["reported"] is None, source


def test_used_reads_roots_and_submitted_documents_only():
    refs, calls = _references({"USD": (2, 0)}, {})
    refs.used_currencies()
    seen = {d: f for d, _, f, _ in calls if d != "ISO Currency"}
    # A group's reporting currency is read from its node with no entity
    # (consolidation_journal._root), not from a node with no parent.
    assert seen["Consolidation Group"] == {"data_area_id": ["is", "not set"]}
    for doctype in ("Group Exchange Rate", "Trial Balance Submission", "Consolidation Journal",
                    "Business Combination", "Business Disposal"):
        assert seen[doctype] == {"docstatus": 1}, (doctype, seen[doctype])
    # A child row counts only under a submitted parent of its own doctype.
    for child, parent in (("Business Combination Consideration", "Business Combination"),
                          ("Business Combination Cost", "Business Combination"),
                          ("Business Disposal Proceeds", "Business Disposal")):
        assert seen[child] == {"docstatus": 1, "parenttype": parent}, (child, seen[child])
    assert {(d) for d, _, _, _ in calls} >= {d for d, _ in _USED_SOURCES}


def test_the_migrate_logs_blocking_and_reported_rows_apart():
    """The Error Log says which rows block and which only are reported."""
    with open(os.path.join(_APP, "install.py")) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_seed_iso_currencies")
    src = ast.unparse(fn)
    assert "minor_unit_gaps" in src
    calls = [ast.unparse(n) for n in ast.walk(fn) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "frappe.log_error"]
    assert any("blocking" in c for c in calls) and any("reported" in c for c in calls), calls
