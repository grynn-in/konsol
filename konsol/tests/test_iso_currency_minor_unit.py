"""konsol#330, option #330-1 (Deepak Pai, 11 Oct 2026): the shipped ISO 4217
list is the authority for ``ISO Currency.minor_unit``.

``tabISO Currency.minor_unit`` is ``int NOT NULL DEFAULT 0``, so a currency
added with Minor Unit left blank was stored as 0 and judged in whole units by
the trial-balance balance check (#180-1). Now a row whose code is on the list
must carry the list's minor unit, and a code that is not on it is refused:
ISO 4217 is a closed list. The list is ``konsol/reference_data/iso_currencies.json``,
the one file the seed reads (konsol.currency_references), never a second copy.
"""
import importlib
import importlib.util
import json
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST_FILE = os.path.join(APP_DIR, "reference_data", "iso_currencies.json")
CONTROLLER = os.path.join(APP_DIR, "epm", "doctype", "iso_currency", "iso_currency.py")
SEED = os.path.join(APP_DIR, "currency_references.py")


def _model():
    sys.modules.pop("konsol.iso_currency_model", None)
    return importlib.import_module("konsol.iso_currency_model")


def _shipped():
    with open(LIST_FILE) as f:
        return {r["currency_code"]: r["minor_unit"] for r in json.load(f)}


class _Thrown(Exception):
    pass


class _Doc:
    doctype = "ISO Currency"

    def __init__(self, *args, **fields):
        data = args[0] if args else fields
        for k, v in dict(data).items():
            setattr(self, k, v)


def _frappe_stub(record):
    frappe = types.ModuleType("frappe")

    def throw(msg, exc=None):
        raise _Thrown(msg)
    frappe.throw = throw
    frappe.ValidationError = _Thrown
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = _Doc
    clickhouse = types.ModuleType("konsol.clickhouse")
    clickhouse.sync_doctype_after_commit = lambda *a, **k: record.setdefault("synced", []).append(a)
    return {"frappe": frappe, "frappe.model": model, "frappe.model.document": document,
            "konsol.clickhouse": clickhouse}


def _load(path, name, stubs):
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _controller():
    return _load(CONTROLLER, "iso_currency_under_test", _frappe_stub({})).ISOCurrency


def _validate(code, minor_unit, usd_log10=0.5):
    """Run ISOCurrency.validate on a row; returns the refusal, or None."""
    doc = _controller()(currency_code=code, currency_name=code, minor_unit=minor_unit, usd_log10=usd_log10)
    try:
        doc.validate()
    except _Thrown as e:
        return str(e)
    return None


# -- the rule, pure ---------------------------------------------------------------

def test_the_list_is_read_from_the_one_shipped_file():
    m = _model()
    assert os.path.realpath(m.REFERENCE_FILE) == os.path.realpath(LIST_FILE)
    assert m.shipped_minor_units() == _shipped()
    assert len(m.shipped_minor_units()) == 88


def test_every_shipped_row_passes_its_own_rule():
    m = _model()
    for code, unit in _shipped().items():
        assert m.minor_unit_problems(code, unit) == [], code


def test_a_minor_unit_that_disagrees_with_the_list_is_refused_naming_both_values():
    m = _model()
    (p,) = m.minor_unit_problems("EUR", 0)
    assert "EUR" in p and "Minor Unit 0" in p and "ISO 4217 gives EUR 2" in p, p
    (p,) = m.minor_unit_problems("KWD", 2)
    assert "Minor Unit 2" in p and "ISO 4217 gives KWD 3" in p, p
    (p,) = m.minor_unit_problems("JPY", 2)
    assert "Minor Unit 2" in p and "ISO 4217 gives JPY 0" in p, p


def test_a_blank_minor_unit_is_refused_even_where_the_list_says_zero():
    """Refuse, never default (konsol#247): blank is not read as the list's value."""
    m = _model()
    for blank in (None, ""):
        (p,) = m.minor_unit_problems("EUR", blank)
        assert "no Minor Unit" in p and "ISO 4217 gives EUR 2" in p, p
        (p,) = m.minor_unit_problems("JPY", blank)
        assert "no Minor Unit" in p and "ISO 4217 gives JPY 0" in p, p


def test_a_code_not_on_the_list_is_refused():
    m = _model()
    (p,) = m.minor_unit_problems("XYZ", 2)
    assert "XYZ" in p and "not on the ISO 4217 list konsol ships" in p, p
    (p,) = m.minor_unit_problems("eur", 2)
    assert "eur" in p and "not on the ISO 4217 list" in p, p


def test_a_whole_number_given_as_text_is_read_as_that_number():
    m = _model()
    assert m.minor_unit_problems("EUR", "2") == []
    (p,) = m.minor_unit_problems("EUR", "3")
    assert "Minor Unit 3" in p, p
    (p,) = m.minor_unit_problems("EUR", True)
    assert "ISO 4217 gives EUR 2" in p, p
    # a checkbox's True is not the whole number 1, even where the list says 1
    (p,) = m.minor_unit_problems("ZZZ", True, shipped={"ZZZ": 1})
    assert "has Minor Unit True" in p and "ISO 4217 gives ZZZ 1" in p, p


def test_the_module_imports_no_frappe():
    with open(os.path.join(APP_DIR, "iso_currency_model.py")) as f:
        text = f.read()
    assert "import frappe" not in text and "from frappe" not in text


def test_the_seed_reads_the_same_list_not_a_second_copy():
    """currency_references takes its file and rows from the pure module."""
    m = _model()
    seed = _load(SEED, "currency_references_under_test_330", {"frappe": types.ModuleType("frappe")})
    assert seed.REFERENCE_FILE == m.REFERENCE_FILE
    assert seed.reference_rows is m.reference_rows


# -- the doctype ------------------------------------------------------------------

def test_the_doctype_refuses_a_mismatch():
    msg = _validate("EUR", 0)
    assert msg and "Minor Unit 0" in msg and "ISO 4217 gives EUR 2" in msg, msg


def test_the_doctype_refuses_a_code_not_on_the_list():
    msg = _validate("XYZ", 2)
    assert msg and "not on the ISO 4217 list" in msg, msg


def test_the_doctype_accepts_the_lists_value():
    assert _validate("JPY", 0, usd_log10=2.17) is None
    assert _validate("KWD", 3, usd_log10=-0.51) is None


def test_the_usd_reference_check_still_runs():
    msg = _validate("EUR", 2, usd_log10=50)
    assert msg and "USD Reference (log10)" in msg, msg


def test_the_seed_inserts_every_shipped_row_through_the_doctype_rule():
    """Migrate and install seed the list through doc.insert, which runs
    validate: on an empty site all 88 rows must pass it, so the rule can never
    fail a migrate or an install."""
    record = {"inserted": [], "refused": []}
    stubs = _frappe_stub(record)
    ISOCurrency = _load(CONTROLLER, "iso_currency_under_test_seed", dict(stubs)).ISOCurrency

    class Seeded(ISOCurrency):
        def insert(self, **k):
            try:
                self.validate()
            except _Thrown as e:
                record["refused"].append((self.currency_code, str(e)))
                return
            record["inserted"].append(self.currency_code)

    frappe = stubs["frappe"]
    frappe.get_all = lambda *a, **k: []
    frappe.get_doc = lambda data: Seeded(data)
    frappe.db = types.SimpleNamespace(set_value=lambda *a, **k: None)
    seed = _load(SEED, "currency_references_under_test_330_seed", {"frappe": frappe})
    out = seed.seed_iso_currencies()
    assert record["refused"] == [], record["refused"][:3]
    assert len(record["inserted"]) == len(out["inserted"]) == 88
