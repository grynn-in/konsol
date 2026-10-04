"""konsol#252: a trial balance declares its currency, and a mismatch is refused.

Decided by Deepak Pai, 18 Sep 2026: the upload declares its currency, konsol
cross-checks it against the Entity's Functional Currency and refuses the
entity-period on a mismatch, naming both values. Rejected: taking the Entity's
currency silently.

Both intakes are covered here: the single Trial Balance Submission (parse_tb_csv
and validate(), loaded by path under stubbed frappe as in
test_trial_balance_submission.py) and the bulk upload's pure half
(tb_bulk_model: split_table, group_csv, check_group). The bulk check's
frappe-side wiring is in test_tb_bulk_model.py, the close app's check and
submit in test_close_tb_api.py.
"""
import importlib.util
import json
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_APP = os.path.dirname(_HERE)
_CONTROLLER = os.path.join(_APP, "consolidation", "doctype", "trial_balance_submission",
                           "trial_balance_submission.py")
_DOCTYPE_JSON = os.path.join(_APP, "consolidation", "doctype", "trial_balance_submission",
                             "trial_balance_submission.json")


def _by_path(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _model():
    """konsol/tb_currency_model.py, loaded per test so a missing module is one
    failure per test rather than the whole file."""
    return _by_path("tb_currency_model_under_test", os.path.join(_APP, "tb_currency_model.py"))


def _bulk():
    return _by_path("tb_bulk_model_currency_under_test", os.path.join(_APP, "tb_bulk_model.py"))


class _Doc:  # stand-in for frappe.model.document.Document
    pass


def _controller():
    """The Trial Balance Submission controller with frappe and the frappe-bound
    konsol modules stubbed; the pure konsol modules it imports are the real ones."""
    stubs = {
        "frappe": types.ModuleType("frappe"),
        "frappe.model": types.ModuleType("frappe.model"),
        "frappe.model.document": types.ModuleType("frappe.model.document"),
        "konsol": types.ModuleType("konsol"),
        "konsol.clickhouse": types.ModuleType("konsol.clickhouse"),
        "konsol.period_status": types.ModuleType("konsol.period_status"),
        "konsol.tb_dimension": types.ModuleType("konsol.tb_dimension"),
    }
    stubs["frappe"].whitelist = lambda *a, **k: (lambda fn: fn)
    stubs["frappe.model.document"].Document = _Doc
    stubs["konsol"].__path__ = [_APP]
    stubs["konsol.clickhouse"].execute = lambda *a, **k: ""
    stubs["konsol.clickhouse"].ensure_raw_tables = lambda: None
    stubs["konsol.period_status"].assert_open = lambda *a, **k: None
    stubs["konsol.period_status"].assert_postable = lambda *a, **k: None
    stubs["konsol.tb_dimension"].declared_dimensions = lambda: []
    pure = ("konsol.tb_basis_model", "konsol.tb_dimension_model", "konsol.tb_currency_model")
    saved = {n: sys.modules.get(n) for n in list(stubs) + list(pure)}
    for n in pure:
        sys.modules.pop(n, None)
    sys.modules.update(stubs)
    try:
        return _by_path("tbs_currency_under_test", _CONTROLLER)
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def _raises(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError")


# -- the rule (konsol/tb_currency_model.py) ----------------------------------------

def test_a_matching_currency_is_accepted():
    m = _model()
    declared = [(2, "EUR"), (3, "EUR")]
    assert m.currency_problems("ZZA", "EUR", declared) == []
    assert m.declared_currency(declared) == "EUR"


def test_case_and_surrounding_spaces_are_not_significant():
    """ISO Currency stores every code upper case; "eur" names the same currency."""
    m = _model()
    declared = [(2, " eur "), (3, "Eur")]
    assert m.currency_problems("ZZA", "EUR", declared) == []
    assert m.declared_currency(declared) == "EUR"


def test_a_mismatch_is_refused_naming_both_values():
    m = _model()
    problems = m.currency_problems("ZZA", "EUR", [(2, "USD"), (3, "USD")])
    assert len(problems) == 1, problems
    assert "USD" in problems[0] and "EUR" in problems[0] and "ZZA" in problems[0], problems


def test_a_value_that_is_not_the_code_is_not_mapped():
    m = _model()
    problems = m.currency_problems("ZZA", "EUR", [(2, "EURO")])
    assert len(problems) == 1 and "EURO" in problems[0] and "EUR" in problems[0], problems


def test_more_than_one_currency_in_one_entity_period_is_refused():
    m = _model()
    problems = m.currency_problems("ZZA", "EUR", [(2, "EUR"), (3, "USD"), (4, "EUR")])
    assert len(problems) == 1, problems
    assert "more than one currency" in problems[0], problems
    assert "EUR on lines 2, 4" in problems[0] and "USD on line 3" in problems[0], problems
    assert m.declared_currency([(2, "EUR"), (3, "USD")]) == ""


def test_a_blank_currency_cell_is_refused():
    m = _model()
    problems = m.currency_problems("ZZA", "EUR", [(2, "EUR"), (3, " ")])
    assert problems == ["The currency is blank on line 3: every row must give the ISO code of the "
                        "currency its amounts are in."], problems


def test_an_entity_without_a_functional_currency_is_refused_by_name_not_skipped():
    m = _model()
    for blank in (None, "", "  "):
        problems = m.currency_problems("ZZA", blank, [(2, "EUR")])
        assert len(problems) == 1, (blank, problems)
        assert "Entity ZZA has no Functional Currency" in problems[0], problems


def test_without_line_numbers_the_problems_name_no_line():
    """The bulk upload reports per entity-period, without file lines."""
    m = _model()
    problems = m.currency_problems("ZZA", "EUR", [(None, "EUR"), (None, "USD"), (None, "")])
    assert problems[0].startswith("The currency is blank:"), problems
    assert "(EUR; USD)" in problems[1], problems


# -- the single upload: parse_tb_csv ----------------------------------------------------

def test_the_single_upload_refuses_a_file_without_the_currency_column():
    c = _controller()
    msg = _raises(c.parse_tb_csv, "main_account,debit,credit\n1010,5,0\n2010,0,5\n")
    assert "Missing column(s) currency" in msg, msg
    assert "Add a currency column" in msg, msg
    assert "main_account,debit,credit,currency" in msg, msg


def test_the_single_upload_carries_each_rows_currency():
    c = _controller()
    rows = c.parse_tb_csv("main_account,Currency,debit,credit\n1010, EUR ,5,0\n2010,usd,0,5\n")
    assert [r["currency"] for r in rows] == ["EUR", "usd"]


def test_the_single_upload_refuses_two_currency_columns():
    c = _controller()
    msg = _raises(c.parse_tb_csv, "main_account,debit,credit,currency,currency\n1010,5,0,EUR,USD\n")
    assert "Two currency columns" in msg, msg


def test_a_stored_file_from_before_the_column_still_reads():
    """The close screens read back files that landed before konsol#252."""
    c = _controller()
    rows = c.parse_tb_csv("main_account,debit,credit\n1010,5,0\n2010,0,5\n", (), stored=True)
    assert [r["currency"] for r in rows] == ["", ""]


# -- the single upload: validate() ----------------------------------------------------

class _Refused(Exception):
    pass


def _validate(csv_text, functional_currency, entity="ZZA"):
    """Run validate() end to end on a draft for ``entity`` whose file is
    ``csv_text`` and whose Entity's Functional Currency is ``functional_currency``.
    Every gate before the file is stubbed to pass. Returns the document."""
    c = _controller()
    reads = []

    def get_value(doctype, filters=None, fieldname=None, **k):
        reads.append((doctype, filters, fieldname))
        if doctype == "Entity" and fieldname == "functional_currency":
            return functional_currency
        return None   # no other submission, no TB Exception

    def throw(msg, *a, **k):
        raise _Refused(msg)

    c.frappe = types.SimpleNamespace(
        throw=throw, msgprint=lambda *a, **k: None,
        db=types.SimpleNamespace(sql=lambda *a, **k: None, get_value=get_value))
    chart = types.ModuleType("konsol.group_chart")
    chart.chart_accounts = lambda: {"1010": {"is_group": 0, "is_posting": 1},
                                    "2010": {"is_group": 0, "is_posting": 1}}
    saved = sys.modules.get("konsol.group_chart")
    sys.modules["konsol.group_chart"] = chart
    try:
        doc = c.TrialBalanceSubmission()
        doc.name, doc.batch_id, doc.data_area_id = "TBS-ZZ", "b1", entity
        doc.fiscal_year, doc.fiscal_period, doc.amount_basis = 2099, 1, "Period movement"
        doc.currency = None
        doc._check_entity_access = lambda: None
        doc._ic_accounts = lambda: []
        doc._parse_file = lambda: c.parse_tb_csv(csv_text)
        doc.validate()
        doc.reads = reads
        return doc
    finally:
        if saved is None:
            sys.modules.pop("konsol.group_chart", None)
        else:
            sys.modules["konsol.group_chart"] = saved


def _validate_refusal(csv_text, functional_currency, entity="ZZA"):
    try:
        _validate(csv_text, functional_currency, entity)
    except _Refused as e:
        return str(e)
    raise AssertionError("validate() accepted the file")


EUR_FILE = "main_account,debit,credit,currency\n1010,100,0,EUR\n2010,0,100,EUR\n"
USD_FILE = "main_account,debit,credit,currency\n1010,100,0,USD\n2010,0,100,USD\n"
MIXED_FILE = "main_account,debit,credit,currency\n1010,100,0,EUR\n2010,0,100,USD\n"


def test_validate_accepts_a_match_and_keeps_the_declared_currency_on_the_document():
    doc = _validate(EUR_FILE, "EUR")
    assert doc.validation_status == "Valid"
    assert doc.currency == "EUR"
    assert ("Entity", "ZZA", "functional_currency") in doc.reads


def test_validate_refuses_a_mismatch_naming_both_values():
    msg = _validate_refusal(USD_FILE, "EUR")
    assert "The file declares USD but Entity ZZA's Functional Currency is EUR" in msg, msg


def test_validate_refuses_an_entity_without_a_functional_currency():
    msg = _validate_refusal(EUR_FILE, None)
    assert "Entity ZZA has no Functional Currency" in msg, msg


def test_validate_refuses_mixed_currencies():
    msg = _validate_refusal(MIXED_FILE, "EUR")
    assert "more than one currency (EUR on line 2; USD on line 3)" in msg, msg


def test_validate_refuses_a_blank_currency():
    msg = _validate_refusal("main_account,debit,credit,currency\n1010,100,0,EUR\n2010,0,100,\n", "EUR")
    assert "The currency is blank on line 3" in msg, msg


def test_the_declared_currency_is_a_read_only_field_on_the_submission():
    with open(_DOCTYPE_JSON) as f:
        meta = json.load(f)
    field = next((f for f in meta["fields"] if f["fieldname"] == "currency"), None)
    assert field is not None, "Trial Balance Submission has no currency field"
    assert field["fieldtype"] == "Link" and field["options"] == "ISO Currency", field
    assert field.get("read_only") == 1, "the file declares it; nobody types it"
    assert field.get("no_copy") == 1, "an amendment judges its own file"
    assert "currency" in meta["field_order"]
    tb_file = next(f for f in meta["fields"] if f["fieldname"] == "tb_file")
    assert "main_account,debit,credit,currency" in tb_file["description"], tb_file["description"]


# -- the bulk upload: tb_bulk_model -----------------------------------------------------

BULK_HEADER = ["data_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit", "currency"]


def test_the_bulk_upload_refuses_a_file_without_the_currency_column():
    b = _bulk()
    msg = _raises(b.split_table, [BULK_HEADER[:-1], ["ZZA", "2099", "1", "1010", "5", "0"]])
    assert "Missing column(s) currency" in msg, msg
    assert "Add a currency column" in msg, msg


def test_the_bulk_upload_refuses_two_currency_columns():
    b = _bulk()
    msg = _raises(b.split_table, [BULK_HEADER + ["Currency"], ["ZZA", "2099", "1", "1010", "5", "0", "EUR", "EUR"]])
    assert "Two currency columns" in msg, msg


def test_one_bulk_file_carries_a_currency_per_entity():
    b = _bulk()
    groups = b.split_table([BULK_HEADER,
                            ["ZZA", "2099", "1", "1010", "5", "0", "EUR"],
                            ["ZZB", "2099", "1", "1010", "5", "0", "JPY"]])
    assert groups[("ZZA", 2099, 1)][0]["currency"] == "EUR"
    assert groups[("ZZB", 2099, 1)][0]["currency"] == "JPY"


def test_the_generated_single_file_carries_the_currency_through_the_single_parser():
    b, c = _bulk(), _controller()
    rows = b.split_table([BULK_HEADER, ["ZZA", "2099", "1", "1010", "5", "0", "eur"],
                          ["ZZA", "2099", "1", "2010", "0", "5", "eur"]])[("ZZA", 2099, 1)]
    again = c.parse_tb_csv(b.group_csv(rows, source="TBU-ZZ"))
    assert [r["currency"] for r in again] == ["eur", "eur"]


def _group(b, rows, functional_currency):
    return b.check_group(("ZZA", 2099, 1), rows, known_accounts=None, visible=True, leaf=True,
                         period={"code": "P01", "type": "Regular", "status": "Open"},
                         postable_types={"Regular"}, existing=None,
                         validate_rows=lambda rows, **kw: [], functional_currency=functional_currency)


def _rows(*currencies):
    return [{"main_account": "1010", "debit": 5.0, "credit": 0.0, "currency": cur} for cur in currencies]


def test_the_bulk_check_accepts_a_match():
    b = _bulk()
    r = _group(b, _rows("EUR", "eur"), "EUR")
    assert r["ok"] and r["errors"] == [], r


def test_the_bulk_check_refuses_a_mismatch_naming_both_values():
    b = _bulk()
    r = _group(b, _rows("USD", "USD"), "EUR")
    assert not r["ok"], r
    assert any("The file declares USD but Entity ZZA's Functional Currency is EUR" in e for e in r["errors"]), r


def test_the_bulk_check_refuses_mixed_currencies_in_one_entity_period():
    b = _bulk()
    r = _group(b, _rows("EUR", "USD"), "EUR")
    assert not r["ok"] and any("more than one currency (EUR; USD)" in e for e in r["errors"]), r


def test_the_bulk_check_refuses_an_entity_without_a_functional_currency():
    b = _bulk()
    for blank in (None, ""):
        r = _group(b, _rows("EUR"), blank)
        assert not r["ok"] and any("Entity ZZA has no Functional Currency" in e for e in r["errors"]), r


def test_the_bulk_check_has_no_default_functional_currency():
    """No default: a caller that does not read the Entity cannot call it."""
    b = _bulk()
    try:
        b.check_group(("ZZA", 2099, 1), _rows("EUR"), known_accounts=None, visible=True, leaf=True,
                      period={"code": "P01", "type": "Regular", "status": "Open"},
                      postable_types={"Regular"}, existing=None, validate_rows=lambda rows, **kw: [])
    except TypeError as e:
        assert "functional_currency" in str(e), e
    else:
        raise AssertionError("check_group ran without the entity's Functional Currency")


def test_both_intakes_refuse_the_same_file_with_the_same_sentence():
    """One rule (tb_currency_model) behind both intakes: the bulk check's
    refusal and the single submission's refusal say the same thing."""
    b = _bulk()
    m = _model()
    for csv_text, bulk_rows, functional in (
            (USD_FILE, _rows("USD", "USD"), "EUR"),
            (EUR_FILE, _rows("EUR", "EUR"), None)):
        single = _validate_refusal(csv_text, functional)
        bulk = _group(b, bulk_rows, functional)["errors"]
        expected = m.currency_problems("ZZA", functional, [(None, r["currency"]) for r in bulk_rows])
        assert expected and bulk == expected, (bulk, expected)
        for sentence in expected:
            assert sentence in single, (sentence, single)
