"""konsol#180: a trial balance balances exactly in its declared currency's minor unit.

Decided by Deepak Pai, 5 Oct 2026 (option #180-1): debits must equal credits
once sum(debit) and sum(credit) are each rounded to the minor unit of the
trial balance's declared currency (``ISO Currency.minor_unit``; the currency
is declared under konsol#252). JPY compares whole yen, EUR whole cents, KWD
whole fils. There is no tolerance to configure and none to default. Rejected:
#180-2 (a per-group tolerance field), #180-3 (relative with a floor), #180-4
(a per-Entity tolerance field).

The rule is ``konsol/tb_balance_model.py`` (pure, loaded by path here). Every
intake runs it through ``currency_and_balance_problems``: the single Trial
Balance Submission's validate() (Desk, and the bulk load, which inserts one
submission per entity-period), the bulk check (tb_bulk_model.check_group) and
the close app's check_tb / submit_tb. The intake harnesses are the ones
test_tb_currency.py and test_close_tb_api.py already use.
"""
import importlib.util
import inspect
import os
import subprocess
from decimal import Decimal

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
_ROOT = os.path.dirname(_APP)


def _by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _model():
    """konsol/tb_balance_model.py, loaded per test so a missing module is one
    failure per test rather than the whole file."""
    return _by_path("tb_balance_model_under_test", os.path.join(_APP, "tb_balance_model.py"))


def _currency_tests():
    return _by_path("tb_balance_currency_harness", os.path.join(_HERE, "test_tb_currency.py"))


def _api_tests():
    return _by_path("tb_balance_api_harness", os.path.join(_HERE, "test_close_tb_api.py"))


def _rows(*pairs, currency="EUR"):
    """Rows as the parsers give them: one per (debit, credit), numbered from line 2."""
    return [{"main_account": f"{1010 + i}", "debit": d, "credit": c, "currency": currency,
             "line": i + 2} for i, (d, c) in enumerate(pairs)]


# -- the rule (konsol/tb_balance_model.py) -------------------------------------------

def test_an_exact_match_is_accepted():
    m = _model()
    assert m.balance_problems("EUR", 2, _rows((100.5, 0), (0, 100.5))) == []
    assert m.balance_problems("EUR", 2, _rows((1234.56, 0), (0, 1000), (0, 234.56))) == []


def test_a_one_cent_eur_difference_is_refused_naming_it_in_the_currency():
    """It passed before konsol#180: the 0.01 tolerance let one cent through."""
    m = _model()
    problems = m.balance_problems("EUR", 2, _rows((100.01, 0), (0, 100)))
    assert len(problems) == 1, problems
    assert "debits exceed credits by 0.01 EUR" in problems[0], problems
    assert "Debits (100.01 EUR) do not equal credits (100.00 EUR)" in problems[0], problems


def test_the_sentence_says_which_side_is_heavier():
    m = _model()
    (p,) = m.balance_problems("EUR", 2, _rows((1000, 0), (0, 1000.03)))
    assert "credits exceed debits by 0.03 EUR" in p, p
    (p,) = m.balance_problems("EUR", 2, _rows((1000.03, 0), (0, 1000)))
    assert "debits exceed credits by 0.03 EUR" in p, p


def test_jpy_compares_whole_yen_a_0_4_yen_difference_rounds_away():
    m = _model()
    assert m.balance_problems("JPY", 0, _rows((100.4, 0), (0, 100))) == []
    assert m.balance_problems("JPY", 0, _rows((1000, 0), (0, 1000.4))) == []


def test_jpy_a_one_yen_difference_is_refused_in_whole_yen():
    m = _model()
    (p,) = m.balance_problems("JPY", 0, _rows((101, 0), (0, 100)))
    assert "debits exceed credits by 1 JPY" in p, p
    assert "Debits (101 JPY) do not equal credits (100 JPY)" in p, p


def test_kwd_compares_whole_fils_at_three_decimals():
    m = _model()
    assert m.balance_problems("KWD", 3, _rows((100.001, 0), (0, 100.001))) == []
    (p,) = m.balance_problems("KWD", 3, _rows((100.001, 0), (0, 100)))
    assert "debits exceed credits by 0.001 KWD" in p, p
    # below a fil rounds away, as below a yen does for JPY
    assert m.balance_problems("KWD", 3, _rows((100.0004, 0), (0, 100))) == []


def test_each_total_is_rounded_half_up_before_the_compare():
    """The decision rounds sum(debit) and sum(credit) each, not the difference:
    100.5 yen is 101 and 100.4 yen is 100, so they differ by 1 JPY."""
    m = _model()
    (p,) = m.balance_problems("JPY", 0, _rows((100.5, 0), (0, 100.4)))
    assert "debits exceed credits by 1 JPY" in p, p


def test_a_blank_minor_unit_is_refused_by_name_never_defaulted():
    m = _model()
    for blank in (None, ""):
        problems = m.balance_problems("EUR", blank, _rows((100, 0), (0, 100)))
        assert len(problems) == 1, (blank, problems)
        assert "ISO Currency EUR has no Minor Unit" in problems[0], problems
    # an unbalanced file says the same: the balance cannot be judged at all
    (p,) = m.balance_problems("EUR", None, _rows((100.01, 0), (0, 100)))
    assert "has no Minor Unit" in p and "exceed" not in p, p


def test_a_minor_unit_that_is_not_a_whole_number_of_places_is_refused():
    m = _model()
    for bad in (-1, 2.5, "2", True):
        (p,) = m.balance_problems("EUR", bad, _rows((100, 0), (0, 100)))
        assert "ISO Currency EUR" in p and "Minor Unit" in p, (bad, p)


def test_there_is_no_default_minor_unit_and_no_tolerance_parameter():
    m = _model()
    for fn in (m.balance_problems, m.currency_and_balance_problems):
        params = inspect.signature(fn).parameters
        assert "minor_unit" in params and params["minor_unit"].default is inspect.Parameter.empty, fn
        assert not any("tolerance" in p for p in params), fn


def _float_total(amounts):
    """A running float total, as a ``+=`` loop (or a float column's SUM) adds.
    Not ``sum()``: Python 3.12 compensates float sums, which hides the error."""
    total = 0.0
    for a in amounts:
        total += a
    return total


def test_float_accumulation_does_not_make_a_false_difference():
    """Ten 0.1s are 1.00 exactly; added up in floats they are 0.9999999999999999.
    Ten 0.05 yen are 0.50, which rounds half up to 1 yen; added up in floats
    they are 0.49999999999999994, which rounds to 0 and would refuse a
    balanced file."""
    m = _model()
    tenths = [(0.1, 0)] * 10 + [(0, 1.0)]
    assert _float_total(d for d, _ in tenths) != 1.0           # the float pitfall is real
    assert m.balance_problems("EUR", 2, _rows(*tenths)) == []
    nickels = [(0.05, 0)] * 10 + [(0, 1.0)]
    assert _float_total(d for d, _ in nickels) < 0.5            # so half up rounds it to 0
    assert m.balance_problems("JPY", 0, _rows(*nickels)) == []
    many = [(0.1, 0)] * 1000 + [(0, 100.0)]
    assert m.balance_problems("EUR", 2, _rows(*many)) == []


def test_a_float_amount_is_taken_at_its_repr_not_its_binary_value():
    """0.15 and 0.35 are 0.50 as written: half a yen, which rounds up to 1. As
    exact binary values they are 0.1499999… and 0.3499999…, which sum below
    0.5 and round to 0, refusing a file that balances."""
    m = _model()
    assert Decimal(0.15) + Decimal(0.35) < Decimal("0.5")    # the binary trap is real
    assert m.balance_problems("JPY", 0, _rows((0.15, 0), (0.35, 0), (0, 1.0))) == []


def test_huge_amounts_are_judged_exactly_never_raised():
    """konsol#180 review: at 1e26 and above, rounding to the minor unit needs
    more than the 28 digits of Decimal's default context. It must still be
    judged exactly, never raise, and lose no cent."""
    m = _model()
    assert m.balance_problems("EUR", 2, _rows((1e26, 0), (0, 1e26))) == []
    assert m.balance_problems("EUR", 2, _rows((1.7e308, 0), (0, 1.7e308))) == []
    # written out, not big + 0.01: that sum would itself round at 28 digits
    (p,) = m.balance_problems("EUR", 2, _rows((Decimal("100000000000000000000000000.01"), 0),
                                              (0, Decimal("100000000000000000000000000"))))
    assert "debits exceed credits by 0.01 EUR" in p, p
    (p,) = m.balance_problems("EUR", 2, _rows((2e26, 0), (0, 1e26)))
    assert "debits exceed credits by 100,000,000,000,000,000,000,000,000.00 EUR" in p, p
    (p,) = m.balance_problems("KWD", 3, _rows((1.7e308, 0), (0, 1e308)))
    assert "debits exceed credits by" in p and "KWD" in p, p


def test_a_minor_unit_too_large_to_compute_is_refused_by_name():
    """Rounding to a million decimal places would build a million-digit number;
    it is refused naming the currency, not attempted and not raised."""
    m = _model()
    (p,) = m.balance_problems("EUR", 10 ** 6, _rows((100, 0), (0, 100)))
    assert "EUR" in p and "1000000 decimal places" in p, p


def test_decimal_amounts_are_taken_as_they_are():
    m = _model()
    assert m.balance_problems("EUR", 2, _rows((Decimal("0.10"), 0), (0, Decimal("0.1")))) == []


def test_the_currency_is_judged_before_the_balance():
    """A file with a currency problem reports that, not a misleading balance error."""
    m = _model()
    unbalanced_usd = _rows((100.01, 0), (0, 100), currency="USD")
    problems = m.currency_and_balance_problems("ZZA", "EUR", 2, unbalanced_usd)
    assert len(problems) == 1, problems
    assert "The file declares USD but Entity ZZA's Functional Currency is EUR" in problems[0], problems
    # no Functional Currency, so no minor unit either: one refusal, the currency's
    problems = m.currency_and_balance_problems("ZZA", None, None, _rows((100.01, 0), (0, 100)))
    assert len(problems) == 1 and "Entity ZZA has no Functional Currency" in problems[0], problems
    # mixed currencies: the balance is not judged
    mixed = _rows((100.01, 0), (0, 100))
    mixed[1]["currency"] = "USD"
    problems = m.currency_and_balance_problems("ZZA", "EUR", 2, mixed)
    assert len(problems) == 1 and "more than one currency" in problems[0], problems


def test_with_a_valid_currency_the_balance_is_judged():
    m = _model()
    assert m.currency_and_balance_problems("ZZA", "EUR", 2, _rows((100, 0), (0, 100))) == []
    (p,) = m.currency_and_balance_problems("ZZA", "eur", 2, _rows((100, 0), (0, 100.01), currency=" eur "))
    assert "credits exceed debits by 0.01 EUR" in p, p


def test_the_module_imports_no_frappe():
    with open(os.path.join(_APP, "tb_balance_model.py")) as f:
        text = f.read()
    assert "import frappe" not in text and "from frappe" not in text


def test_no_balance_tolerance_is_left_in_the_app():
    """Both BALANCE_TOLERANCE constants are deleted, with every importer."""
    out = subprocess.run(
        ["git", "grep", "-n", "-i", "BALANCE_TOLERANCE\\|balance tolerance", "--", "konsol",
         ":!konsol/tests/test_tb_balance.py"],
        cwd=_ROOT, capture_output=True, text=True)
    assert out.stdout == "", out.stdout


def test_check_rows_and_validate_tb_rows_take_no_tolerance():
    c = _currency_tests()._controller()
    for fn in (c.check_rows, c.validate_tb_rows):
        assert not any("tolerance" in p for p in inspect.signature(fn).parameters), fn


# -- the intakes ---------------------------------------------------------------------

EXACT = "main_account,debit,credit,currency\n1010,100,0,EUR\n2010,0,100,EUR\n"
CENT_OFF = "main_account,debit,credit,currency\n1010,100.01,0,EUR\n2010,0,100,EUR\n"
JPY_04 = "main_account,debit,credit,currency\n1010,100.4,0,JPY\n2010,0,100,JPY\n"
JPY_1 = "main_account,debit,credit,currency\n1010,101,0,JPY\n2010,0,100,JPY\n"
USD_OFF = "main_account,debit,credit,currency\n1010,100.01,0,USD\n2010,0,100,USD\n"

UNITS = {"EUR": 2, "USD": 2, "JPY": 0, "KWD": 3}


def test_desk_validate_accepts_an_exact_file_and_reads_the_minor_unit():
    t = _currency_tests()
    doc = t._validate(EXACT, "EUR", minor_units=UNITS)
    assert doc.validation_status == "Valid"
    assert ("ISO Currency", "EUR", "minor_unit") in doc.reads, doc.reads


def test_desk_validate_refuses_a_one_cent_eur_difference():
    t = _currency_tests()
    msg = t._validate_refusal(CENT_OFF, "EUR", minor_units=UNITS)
    assert "debits exceed credits by 0.01 EUR" in msg, msg


def test_desk_validate_refuses_a_blank_minor_unit():
    t = _currency_tests()
    msg = t._validate_refusal(EXACT, "EUR", minor_units={"EUR": None})
    assert "ISO Currency EUR has no Minor Unit" in msg, msg


def test_desk_validate_reports_the_currency_not_the_balance():
    t = _currency_tests()
    msg = t._validate_refusal(USD_OFF, "EUR", minor_units=UNITS)
    assert "The file declares USD but Entity ZZA's Functional Currency is EUR" in msg, msg
    assert "do not equal" not in msg and "exceed" not in msg, msg


def _bulk_table(csv_text, entity="ZZA"):
    lines = [line.split(",") for line in csv_text.strip().splitlines()]
    header = ["data_area_id", "fiscal_year", "fiscal_period"] + lines[0]
    return [header] + [[entity, "2099", "1"] + line for line in lines[1:]]


def _bulk_errors(csv_text, functional, minor_units):
    t = _currency_tests()
    b, c = t._bulk(), t._controller()
    ((key, rows),) = b.split_table(_bulk_table(csv_text)).items()
    report = b.check_group(key, rows, known_accounts=None, visible=True, leaf=True,
                           period={"code": "P01", "type": "Regular", "status": "Open"},
                           postable_types={"Regular"}, existing=None,
                           validate_rows=c.validate_tb_rows,
                           functional_currency=functional,
                           minor_unit=minor_units.get(functional))
    return report["errors"]


def test_the_bulk_check_refuses_a_one_cent_eur_difference():
    errors = _bulk_errors(CENT_OFF, "EUR", UNITS)
    assert any("debits exceed credits by 0.01 EUR" in e for e in errors), errors
    assert _bulk_errors(EXACT, "EUR", UNITS) == []


def test_the_bulk_check_has_no_default_minor_unit():
    t = _currency_tests()
    b = t._bulk()
    try:
        b.check_group(("ZZA", 2099, 1), [], known_accounts=None, visible=True, leaf=True,
                      period={"code": "P01", "type": "Regular", "status": "Open"},
                      postable_types={"Regular"}, existing=None, validate_rows=lambda rows, **kw: [],
                      functional_currency="EUR")
    except TypeError as e:
        assert "minor_unit" in str(e), e
    else:
        raise AssertionError("check_group ran without the currency's minor unit")


def _close_check(csv_text, functional, minor_units):
    a = _api_tests()
    site = a._Site(currencies={"ZZOP": functional}, minor_units=minor_units)
    result, _, _ = a._check(site, content=csv_text)
    assert site.log == [], site.log
    return result


def _close_submit_error(csv_text, functional, minor_units):
    """The submit's refusal, or None when it went through."""
    a = _api_tests()
    site = a._SubmitSite(currencies={"ZZOP": functional}, minor_units=minor_units)
    try:
        a._submit(site, content=csv_text)
    except Exception as e:   # noqa: BLE001 - the message is compared
        assert site.log == [], site.log
        return str(e)
    return None


def test_the_close_check_refuses_a_one_cent_eur_difference_and_writes_nothing():
    result = _close_check(CENT_OFF, "EUR", UNITS)
    assert result["ok"] is False, result
    assert any("debits exceed credits by 0.01 EUR" in p for p in result["file_problems"]), result


def test_the_close_submit_refuses_a_one_cent_eur_difference_before_writing():
    msg = _close_submit_error(CENT_OFF, "EUR", UNITS)
    assert msg and "debits exceed credits by 0.01 EUR" in msg, msg


def test_all_intakes_agree():
    """Desk validate (and so the bulk load), the bulk check, the close check and
    the close submit accept and refuse the same files with the same sentence,
    the one tb_balance_model gives."""
    m = _model()
    t = _currency_tests()
    cases = [
        (EXACT, "EUR", UNITS),
        (CENT_OFF, "EUR", UNITS),
        ("main_account,debit,credit,currency\n1010,100,0,EUR\n2010,0,100.01,EUR\n", "EUR", UNITS),
        (JPY_04, "JPY", UNITS),
        (JPY_1, "JPY", UNITS),
        (EXACT, "EUR", {"EUR": None}),
        (USD_OFF, "EUR", UNITS),
    ]
    for csv_text, functional, units in cases:
        rows = t._controller().parse_tb_csv(csv_text)
        expected = m.currency_and_balance_problems("ZZA", functional, units.get(functional), rows)
        try:
            t._validate(csv_text, functional, minor_units=units)
            desk = ""
        except t._Refused as e:
            desk = str(e)
        bulk = _bulk_errors(csv_text, functional, units)
        check = _close_check(csv_text, functional, units)
        submit = _close_submit_error(csv_text, functional, units) or ""
        label = (csv_text, functional, units)
        if not expected:
            assert desk == "" and bulk == [] and check["ok"] and submit == "", (label, desk, bulk, check, submit)
            continue
        assert bulk == expected, (label, bulk, expected)
        assert not check["ok"], (label, check)
        for sentence in expected:
            # the close app names its own entity (ZZOP); the sentences are otherwise the same
            close_sentence = sentence.replace("ZZA", "ZZOP")
            assert sentence in desk, (label, sentence, desk)
            assert close_sentence in check["file_problems"], (label, close_sentence, check["file_problems"])
            assert close_sentence in submit, (label, close_sentence, submit)


HUGE_OK = "main_account,debit,credit,currency\n1010,1e26,0,EUR\n2010,0,1e26,EUR\n"
HUGE_OFF = "main_account,debit,credit,currency\n1010,2e26,0,EUR\n2010,0,1e26,EUR\n"


def test_huge_amounts_never_raise_on_any_intake():
    """konsol#180 review: 1e26 crashed the quantize. Every intake now judges it:
    a balanced file is accepted and an unbalanced one refused by sentence."""
    t = _currency_tests()
    assert t._validate(HUGE_OK, "EUR", minor_units=UNITS).validation_status == "Valid"
    msg = t._validate_refusal(HUGE_OFF, "EUR", minor_units=UNITS)
    assert "debits exceed credits by" in msg, msg
    assert _bulk_errors(HUGE_OK, "EUR", UNITS) == []
    assert any("debits exceed credits by" in e for e in _bulk_errors(HUGE_OFF, "EUR", UNITS))
    assert _close_check(HUGE_OK, "EUR", UNITS)["ok"] is True
    check = _close_check(HUGE_OFF, "EUR", UNITS)
    assert not check["ok"] and any("debits exceed credits by" in p for p in check["file_problems"]), check
    assert _close_submit_error(HUGE_OK, "EUR", UNITS) is None
    msg = _close_submit_error(HUGE_OFF, "EUR", UNITS)
    assert msg and "debits exceed credits by" in msg, msg
