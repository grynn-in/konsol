"""Host tests for the Trial Balance Submission CSV parser and validator.

parse_tb_csv / validate_tb_rows are deliberately pure so the whole validation
surface runs here without a site. The module is loaded by file path with
stubbed frappe/konsol imports (the pattern test_budget_grain.py established) —
the pure functions under test never call into them. A naive AST-extraction
loader was tried first and silently dropped this whole file from the suite the
day a new import was added; module stubbing fails loudly instead.
"""
import importlib.util
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(
    _HERE, "..", "consolidation", "doctype",
    "trial_balance_submission", "trial_balance_submission.py",
)


def _stub(name, **attrs):
    if name not in sys.modules:
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        sys.modules[name] = mod


class _Doc:  # stand-in for frappe.model.document.Document
    pass


_stub("frappe")
_stub("frappe.model")
_stub("frappe.model.document", Document=_Doc)
_stub("konsol")
_stub("konsol.clickhouse", execute=lambda *a, **k: "", ensure_raw_tables=lambda: None)
_stub("konsol.period_status", assert_open=lambda *a, **k: None,
      assert_postable=lambda *a, **k: None)

_spec = importlib.util.spec_from_file_location("tbs_under_test", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

GOOD = "main_account,debit,credit\n1010,100.50,0\n2010,0,100.50\n"


def test_parse_good_file():
    rows = _m.parse_tb_csv(GOOD)
    assert len(rows) == 2
    assert rows[0] == {"main_account": "1010", "debit": 100.5,
                       "credit": 0.0, "description": "", "partner_data_area_id": ""}


def test_parse_accepts_description_and_case_insensitive_header():
    rows = _m.parse_tb_csv(
        "Main_Account,DEBIT,Credit,Description\n1010,5,0,Cash\n2010,0,5,AP\n")
    assert rows[0]["description"] == "Cash"


def test_parse_rejects_missing_columns():
    try:
        _m.parse_tb_csv("account,dr,cr\n1010,1,0\n")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "main_account" in str(e)


def test_parse_rejects_non_numeric_amount():
    try:
        _m.parse_tb_csv("main_account,debit,credit\n1010,abc,0\n")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Line 2" in str(e)


def test_parse_rejects_blank_account_and_empty_file():
    for bad in ("main_account,debit,credit\n,1,0\n",
                "",
                "main_account,debit,credit\n"):
        try:
            _m.parse_tb_csv(bad)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass


def test_parse_treats_blank_amounts_as_zero():
    rows = _m.parse_tb_csv("main_account,debit,credit\n1010,,\n2010,0,0\n")
    assert rows[0]["debit"] == 0.0 and rows[0]["credit"] == 0.0


def _rows(*triples):
    return [{"main_account": a, "debit": d, "credit": c, "description": ""}
            for a, d, c in triples]


def test_validate_balanced_clean():
    assert _m.validate_tb_rows(_rows(("1010", 10, 0), ("2010", 0, 10))) == []


def test_validate_flags_imbalance():
    errs = _m.validate_tb_rows(_rows(("1010", 10, 0), ("2010", 0, 9)))
    assert any("do not equal" in e for e in errs)


def test_validate_tolerates_rounding_within_tolerance():
    assert _m.validate_tb_rows(
        _rows(("1010", 10.004, 0), ("2010", 0, 10.0))) == []


def test_validate_flags_duplicates():
    errs = _m.validate_tb_rows(
        _rows(("1010", 5, 0), ("1010", 5, 0), ("2010", 0, 10)))
    assert any("Duplicate" in e and "1010" in e for e in errs)


def test_validate_flags_negative_amounts():
    errs = _m.validate_tb_rows(_rows(("1010", -10, 0), ("2010", 0, -10)))
    assert any("Negative" in e for e in errs)


def test_validate_flags_unknown_accounts_only_when_chart_given():
    rows = _rows(("1010", 10, 0), ("9999", 0, 10))
    assert _m.validate_tb_rows(rows) == []  # no chart -> skipped
    errs = _m.validate_tb_rows(rows, known_accounts={"1010", "2010"})
    assert any("9999" in e for e in errs)
    assert not any("1010" in e for e in errs if "chart" in e)


def test_validate_collects_multiple_errors():
    errs = _m.validate_tb_rows(
        _rows(("1010", 5, 0), ("1010", -1, 0)), known_accounts={"2010"})
    assert len(errs) >= 3  # duplicate + negative + unknown (+ imbalance)


def test_sql_str_escapes_quotes_and_backslashes():
    assert _m._sql_str("O'Brien\\x") == "O\\'Brien\\\\x"


def test_parse_rejects_nan_and_inf():
    for bad in ("nan", "inf", "-inf"):
        try:
            _m.parse_tb_csv(f"main_account,debit,credit\n1010,{bad},0\n")
            assert False, f"expected ValueError for {bad}"
        except ValueError as e:
            assert "finite" in str(e)


def test_parse_rejects_surplus_cells():
    try:
        _m.parse_tb_csv("main_account,debit,credit\n1010,1,0,stray,extra\n")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "more cells" in str(e)


def test_parse_rounds_to_cents_so_stored_equals_validated():
    rows = _m.parse_tb_csv("main_account,debit,credit\n1010,10.005,0\n2010,0,10.004\n")
    assert rows[0]["debit"] == 10.0 or rows[0]["debit"] == 10.01  # banker's rounding either way
    assert rows[1]["credit"] == 10.0
    # the point: balance is judged on the ROUNDED values — the same numbers
    # the warehouse will store — so post-rounding drift past the tolerance
    # fails here, not later in a dbt test
    errs = _m.validate_tb_rows(_m.parse_tb_csv(
        "main_account,debit,credit\n1010,10.019,0\n2010,0,10.001\n"))
    assert any("do not equal" in e for e in errs)  # 10.02 vs 10.00 -> 0.02 > 0.01


# --- konsol#159: the intercompany partner -----------------------------------

IC = "main_account,debit,credit,description,partner_data_area_id\n"


def test_parse_reads_the_partner_and_its_aliases():
    rows = _m.parse_tb_csv(IC + "4030,0,100,IC sales,ZZB\n1010,100,0,,\n")
    assert rows[0]["partner_data_area_id"] == "ZZB"
    assert rows[1]["partner_data_area_id"] == ""
    for alias in ("partner", "Partner_Entity", "PARTNER_ID", "counterparty"):
        rows = _m.parse_tb_csv(f"main_account,debit,credit,{alias}\n4030,0,5, ZZB \n1010,5,0,\n")
        assert rows[0]["partner_data_area_id"] == "ZZB", alias


def test_parse_refuses_two_partner_columns():
    try:
        _m.parse_tb_csv("main_account,debit,credit,partner,partner_data_area_id\n4030,0,5,ZZB,ZZB\n")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Two partner columns" in str(e)


def _prow(account, debit, credit, partner=""):
    return {"main_account": account, "debit": debit, "credit": credit,
            "description": "", "partner_data_area_id": partner}


def test_one_account_may_carry_several_partners_but_not_one_partner_twice():
    rows = [_prow("4030", 0, 60, "ZZB"), _prow("4030", 0, 40, "ZZC"), _prow("1010", 100, 0)]
    assert _m.validate_tb_rows(rows) == []
    errs = _m.validate_tb_rows(rows + [_prow("4030", 0, 1, "ZZB"), _prow("1010", 1, 0, "")])
    dup = next(e for e in errs if "Duplicate" in e)
    assert "4030 (partner ZZB)" in dup and "1010" in dup and "ZZC" not in dup


def test_a_partner_equal_to_the_entity_is_refused():
    rows = [_prow("4030", 0, 10, "zza"), _prow("1010", 10, 0)]
    errs = _m.validate_tb_rows(rows, entity="ZZA", known_entities={"ZZA", "ZZB"})
    assert len(errs) == 1 and "entity itself (ZZA)" in errs[0] and "4030" in errs[0]


def test_an_unknown_partner_is_refused_and_a_case_slip_is_named():
    rows = [_prow("4030", 0, 10, "ZZX"), _prow("5030", 5, 0, "zzb"), _prow("1010", 5, 0)]
    errs = _m.validate_tb_rows(rows, entity="ZZA", known_entities={"ZZA", "ZZB"})
    assert len(errs) == 1
    assert "ZZX" in errs[0] and "zzb (did you mean ZZB?)" in errs[0]
    # no entity list: the partner is not checked against Entity
    assert _m.validate_tb_rows(rows, entity="ZZA") == []


def test_the_partner_is_optional():
    """Decision 2: a row on an intercompany account without a partner is valid;
    it is warned about, never refused."""
    rows = [_prow("4030", 0, 10), _prow("1010", 10, 0)]
    assert _m.validate_tb_rows(rows, entity="ZZA", known_entities={"ZZA", "ZZB"}) == []
    assert _m.partnerless_ic_accounts(rows, {"4030", "5030"}) == ["4030"]
    assert _m.partnerless_ic_accounts([_prow("4030", 0, 10, "ZZB")], {"4030"}) == []
    assert _m.partnerless_ic_accounts(rows, set()) == []
    msg = _m.partnerless_warning(["4030"])
    assert "1 intercompany row without a partner" in msg and "never eliminated" in msg
    assert "2 intercompany rows" in _m.partnerless_warning(["4030", "5030"])
    assert _m.partnerless_warning([]) == ""


def test_the_partner_lands_in_the_raw_table():
    sent = []
    _m.execute = lambda sql, *a, **k: sent.append(sql) or ""
    doc = _m.TrialBalanceSubmission()
    doc.batch_id, doc.data_area_id, doc.fiscal_year, doc.fiscal_period, doc.name = "b1", "ZZA", 2099, 1, "TBS-1"
    doc._land_rows([_prow("4030", 0, 10, "ZZB"), _prow("1010", 10, 0)])
    assert len(sent) == 1
    assert "submitted_at, partner_data_area_id) VALUES" in sent[0]
    assert "now(), 'ZZB')" in sent[0] and "now(), '')" in sent[0]


# -- konsol#189: validate refuses a period that is undeclared, or not postable ------------------

class _Refused(Exception):
    """frappe.throw, or a period_status gate, refused."""


class _PeriodNotDeclared(_Refused):
    """Stand-in for konsol.period_status.PeriodNotDeclared."""


class _ReachedOpenCheck(Exception):
    """validate got past the postable check to assert_open."""


def _validate_period(fiscal_period, assert_postable):
    """Run validate() on a draft in FY2099 ``fiscal_period`` with
    ``assert_postable`` standing in for the real gate. Stops at assert_open.
    Returns the gates reached, in order."""
    reached = []

    def postable(fy, fp):
        reached.append(("postable", fy, fp))
        assert_postable(fy, fp)

    def open_(fy, fp, action="run"):
        reached.append(("open", fy, fp))
        raise _ReachedOpenCheck

    def throw(msg, *a, **k):
        raise _Refused(msg)

    names = ("assert_postable", "assert_open", "frappe")
    saved = {n: getattr(_m, n) for n in names if hasattr(_m, n)}
    _m.assert_postable, _m.assert_open = postable, open_
    _m.frappe = types.SimpleNamespace(throw=throw)
    try:
        doc = _m.TrialBalanceSubmission()
        doc.batch_id, doc.data_area_id, doc.fiscal_year, doc.fiscal_period = "b1", "ZZA", 2099, fiscal_period
        doc._check_entity_access = lambda: None
        try:
            doc.validate()
        except _ReachedOpenCheck:
            pass
    finally:
        for n in names:
            if n in saved:
                setattr(_m, n, saved[n])
            else:
                delattr(_m, n)
    return reached


def test_undeclared_period_refused():
    def undeclared(fy, fp):
        raise _PeriodNotDeclared(f"FY{fy} has no period {fp}.")
    try:
        _validate_period(5, undeclared)
        assert False, "validate allowed an undeclared period"
    except _PeriodNotDeclared as e:
        assert str(e) == "FY2099 has no period 5."


def test_closing_period_refused_unless_ticked():
    def gate(ticked):
        def postable(fy, fp):
            if not ticked:
                raise _Refused("P13 (Closing) does not take trial balances on this site. "
                               "Tick it in EPM Settings → Close to allow it.")
        return postable
    try:
        _validate_period(13, gate(ticked=False))
        assert False, "validate allowed a Closing period the site does not post to"
    except _Refused as e:
        assert "P13 (Closing) does not take trial balances" in str(e)
    assert _validate_period(13, gate(ticked=True)) == [("postable", 2099, 13), ("open", 2099, 13)]


def test_period_13_no_longer_refused_by_range_when_declared_postable():
    """The period check is the declared calendar, not a hard-coded 1..12:
    a declared, postable P13 goes on to the open check."""
    reached = _validate_period(13, lambda fy, fp: None)
    assert reached == [("postable", 2099, 13), ("open", 2099, 13)]


# -- konsol#182: what the group chart says about a trial balance's accounts -----------------------

def _chart_rows(*codes):
    return [{"main_account": c, "debit": 1.0 if i == 0 else 0.0, "credit": 0.0 if i == 0 else 1.0}
            for i, c in enumerate(codes)][:2] + [{"main_account": c, "debit": 0.0, "credit": 0.0} for c in codes[2:]]


def _account(is_group=0, is_posting=1):
    return {"is_group": is_group, "is_posting": is_posting}


def test_no_published_chart_is_one_refusal_not_every_account():
    errors = _m.validate_tb_rows(_chart_rows("ZZ1000", "ZZ4000"), chart={})
    assert errors == ["No group chart is published yet: upload and publish one (Main Account) "
                      "before submitting trial balances"]


def test_posting_to_a_heading_is_refused_with_its_reason():
    chart = {"ZZ1000": _account(), "ZZ9000": _account(is_group=1, is_posting=0)}
    errors = _m.validate_tb_rows(_chart_rows("ZZ1000", "ZZ9000"), chart=chart)
    assert errors == ["ZZ9000 is a heading in the group chart; post to the accounts under it."]


def test_posting_to_a_closed_account_is_refused_with_its_reason():
    chart = {"ZZ1000": _account(), "ZZ2000": _account(is_posting=0)}
    errors = _m.validate_tb_rows(_chart_rows("ZZ1000", "ZZ2000"), chart=chart)
    assert len(errors) == 1 and "ZZ2000" in errors[0] and "is_posting is off" in errors[0]


def test_an_account_outside_the_chart_is_listed():
    errors = _m.validate_tb_rows(_chart_rows("ZZ1000", "ZZ7777"), chart={"ZZ1000": _account()})
    assert errors == ["Account(s) not in the group chart: ZZ7777"]
    assert _m.validate_tb_rows(_chart_rows("ZZ1000", "ZZ4000"), chart={"ZZ1000": _account(), "ZZ4000": _account()}) == []
    # known_accounts still works on its own (the bulk model's tests call it so)
    assert _m.validate_tb_rows(_chart_rows("ZZ1000", "ZZ7777"), known_accounts={"ZZ1000"}) == [
        "Account(s) not in the group chart: ZZ7777"]


def test_single_and_bulk_uploads_pass_the_chart():
    import ast as _ast
    with open(_SRC) as f:
        src = f.read()
    assert "validate_tb_rows(rows, chart=chart_accounts()," in src
    with open(os.path.join(_HERE, "..", "tb_bulk.py")) as f:
        bulk = f.read()
    assert "functools.partial(validate_tb_rows, chart=chart)" in bulk and "validate_rows=validate_rows," in bulk
    _ast.parse(bulk)


# -- konsolidat#199: the submission declares its amount basis ----------------------------------

import json as _json

_DOCTYPE_DIR = os.path.dirname(_SRC)
_TBS_JSON = os.path.join(_DOCTYPE_DIR, "trial_balance_submission.json")
_TBS_JS = os.path.join(_DOCTYPE_DIR, "trial_balance_submission.js")
_SETTINGS_JSON = os.path.join(
    _HERE, "..", "pipeline", "doctype", "epm_settings", "epm_settings.json")

BASIS_OPTIONS = "Period movement\nYear-to-date movement\nPeriod-end balance"


def _load(path):
    with open(path) as f:
        return _json.load(f)


def _field(meta, fieldname):
    return next((f for f in meta["fields"] if f["fieldname"] == fieldname), None)


def test_submission_has_a_required_amount_basis_select():
    meta = _load(_TBS_JSON)
    f = _field(meta, "amount_basis")
    assert f is not None, "Trial Balance Submission has no amount_basis field"
    assert f["fieldtype"] == "Select"
    assert f["options"] == BASIS_OPTIONS  # the three bases, exact strings, in order
    assert f["label"] == "Amount Basis"
    assert f.get("reqd") == 1
    assert f.get("allow_on_submit") == 1  # K5 sets it on submitted batches
    assert f.get("in_list_view") == 1
    assert f.get("in_standard_filter") == 1
    assert "default" not in f  # no default that guesses; EPM Settings only pre-fills
    assert "double-counts" in f.get("description", "")


def test_amount_basis_sits_right_after_fiscal_period():
    meta = _load(_TBS_JSON)
    order = meta["field_order"]
    assert "amount_basis" in order, "amount_basis is absent from field_order"
    assert order.index("amount_basis") == order.index("fiscal_period") + 1
    names = [f["fieldname"] for f in meta["fields"]]
    assert names.index("amount_basis") == names.index("fiscal_period") + 1
    assert order == names  # field_order and fields must agree


def test_epm_settings_default_amount_basis_pre_fills_only():
    meta = _load(_SETTINGS_JSON)
    f = _field(meta, "default_amount_basis")
    assert f is not None, "EPM Settings has no default_amount_basis field"
    assert f["fieldtype"] == "Select"
    assert f["options"] == BASIS_OPTIONS
    assert f["label"] == "Default Amount Basis"
    assert not f.get("reqd")
    assert "default" not in f
    assert "Pre-fills" in f.get("description", "")
    assert "never applied silently" in f.get("description", "")


def test_default_amount_basis_lives_in_the_trial_balance_periods_section():
    meta = _load(_SETTINGS_JSON)
    names = [f["fieldname"] for f in meta["fields"]]
    assert "default_amount_basis" in names, "default_amount_basis is absent from EPM Settings"
    start = names.index("tb_periods_section")
    breaks = {"Section Break", "Tab Break"}
    end = next((i for i in range(start + 1, len(names))
                if meta["fields"][i]["fieldtype"] in breaks), len(names))
    assert start < names.index("default_amount_basis") < end
    assert "default_amount_basis" in meta["field_order"]


def test_form_js_pre_fills_the_basis_from_epm_settings():
    assert os.path.exists(_TBS_JS), "trial_balance_submission.js is missing"
    with open(_TBS_JS) as f:
        js = f.read()
    assert 'frappe.ui.form.on("Trial Balance Submission"' in js
    assert "onload" in js
    assert "default_amount_basis" in js
    assert 'frappe.db.get_single_value("EPM Settings", "default_amount_basis")' in js
    assert "frm.is_new()" in js and "amount_basis" in js
    assert "set_value" in js
