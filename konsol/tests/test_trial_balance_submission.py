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


# whitelist: the controller's module-level endpoints decorate at import time.
_stub("frappe", whitelist=lambda *a, **k: (lambda fn: fn))
_stub("frappe.model")
_stub("frappe.model.document", Document=_Doc)
# __path__ makes the stub a package, so the controller's import of the REAL
# pure konsol.tb_basis_model resolves while the frappe-bound modules below stay stubbed.
_stub("konsol", __path__=[os.path.join(_HERE, "..")])
_stub("konsol.clickhouse", execute=lambda *a, **k: "", ensure_raw_tables=lambda: None)
_stub("konsol.period_status", assert_open=lambda *a, **k: None,
      assert_postable=lambda *a, **k: None)
_ADMIN_CHECKS = []
_stub("konsol.schema_lifecycle", check_epm_admin=lambda: _ADMIN_CHECKS.append(True))

_spec = importlib.util.spec_from_file_location("tbs_under_test", _SRC)
_m = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_m)

GOOD = "main_account,debit,credit\n1010,100.50,0\n2010,0,100.50\n"


def test_parse_good_file():
    rows = _m.parse_tb_csv(GOOD)
    assert len(rows) == 2
    assert rows[0] == {"main_account": "1010", "debit": 100.5,
                       "credit": 0.0, "description": "", "partner_data_area_id": "",
                       "amount_basis": ""}


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


# -- konsolidat#199 (K3): the file may repeat the basis; the claim carries it -------------------

import inspect as _inspect

CLOSING = "Period-end balance"


def test_parse_reads_the_optional_amount_basis_column_and_its_aliases():
    rows = _m.parse_tb_csv("main_account,debit,credit,amount_basis\n1010,5,0,Period-end balance\n2010,0,5,\n")
    assert rows[0]["amount_basis"] == CLOSING
    assert rows[1]["amount_basis"] == ""  # a blank cell is "not given", the form decides
    for alias in ("basis", "Basis", "AMOUNT_BASIS", "amount basis"):
        rows = _m.parse_tb_csv(f"main_account,debit,credit,{alias}\n1010,5,0, period movement \n2010,0,5,x\n")
        assert rows[0]["amount_basis"] == "period movement", alias  # as written; canonical() judges it
        assert rows[1]["amount_basis"] == "x", alias
    # no column at all: every row says ""
    assert all(r["amount_basis"] == "" for r in _m.parse_tb_csv(GOOD))


def test_parse_refuses_two_amount_basis_columns():
    try:
        _m.parse_tb_csv("main_account,debit,credit,basis,amount_basis\n1010,5,0,a,b\n")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "Two amount_basis columns" in str(e)


def test_validate_checks_the_file_basis_against_the_form():
    """The sentences from basis_problems() join the other validation errors in
    the one 'failed validation' throw, so the uploader sees every problem at once."""
    with open(_SRC) as f:
        src = f.read()
    assert "from konsol.tb_basis_model import" in src and "basis_problems" in src
    body = _inspect.getsource(_m.TrialBalanceSubmission.validate)
    assert "basis_problems(self.amount_basis," in body
    # only rows that carry a value are judged; a blank cell means "not given"
    assert 'if r["amount_basis"]' in body or "if r[BASIS]" in body
    assert body.index("basis_problems(") < body.index("if errors:")
    assert body.index("validate_tb_rows(") < body.index("basis_problems(")


def test_the_claim_carries_the_amount_basis():
    """on_submit's control-table INSERT names amount_basis last, after
    claimed_at, and lands the form's value; bronze reads the basis off the claim."""
    sent = []
    _m.execute = lambda sql, *a, **k: sent.append(sql) or ""
    doc = _m.TrialBalanceSubmission()
    doc.batch_id, doc.data_area_id, doc.fiscal_year, doc.fiscal_period = "b1", "ZZA", 2099, 1
    doc.name, doc.row_count, doc.amount_basis = "TBS-1", 2, CLOSING
    doc._parse_file = lambda: []
    doc._ensure_tables = lambda: None
    doc._land_rows = lambda rows: None
    doc.on_submit()
    claim = next(s for s in sent if s.startswith(f"INSERT INTO {_m.CONTROL_TABLE} "))
    assert "fiscal_period, row_count, claimed_at, amount_basis) VALUES" in claim
    assert claim.endswith(f"2, now(), '{CLOSING}')")
    assert "'b1', 'TBS-1', 'ZZA', 2099, 1, 2, now()" in claim


# -- konsolidat#199 (K5): set the basis on batches claimed before it existed ----------------------

_TBS_LIST_JS = os.path.join(_DOCTYPE_DIR, "trial_balance_submission_list.js")
ALL_BASES = ("Period movement", "Year-to-date movement", "Period-end balance")


def test_set_amount_basis_is_an_admin_post_endpoint_that_re_claims():
    """Source contract: POST-only, EPM Admin, open period, the basis written
    on the submitted document, and a NEW claim row (ClickHouse has no UPDATE
    worth trusting; ReplacingMergeTree keeps the newest claimed_at)."""
    with open(_SRC) as f:
        src = f.read()
    assert "def set_amount_basis(names, amount_basis)" in src, "set_amount_basis is absent"
    deco_and_def = src[src.index("def set_amount_basis(") - 60:src.index("def set_amount_basis(")]
    assert 'whitelist(methods=["POST"])' in deco_and_def
    body = _inspect.getsource(_m.set_amount_basis)
    assert "check_epm_admin()" in body
    assert "canonical(" in body
    assert "assert_open(" in body and "set the amount basis of a trial balance" in body
    assert '"amount_basis", basis' in body and "update_modified=False" in body
    # the re-claim is the SAME tuple on_submit lands, via the shared helper
    assert "_claim_values(" in body
    claim = _inspect.getsource(_m._claim_values)
    assert "now()" in claim and "_sql_str(basis)" in claim
    insert = _inspect.getsource(_m._claim_insert)
    assert "INSERT INTO {CONTROL_TABLE}" in insert and "amount_basis) VALUES" in insert
    assert "_claim_values(self, self.amount_basis)" in _inspect.getsource(_m.TrialBalanceSubmission.on_submit)
    assert "docstatus" in body and "not submitted" in body
    # the gate runs over every document before any write: a closed period on
    # the third document must not leave the first two re-claimed
    assert body.index("assert_open(") < body.index('"amount_basis", basis')


class _Thrown(RuntimeError):
    """What the stubbed frappe.throw raises; keeps the exception class passed."""

    def __init__(self, msg, exc=None):
        super().__init__(msg)
        self.exc = exc


def _raise(msg, exc=None, *a, **k):
    raise _Thrown(msg, exc)


class _PermissionError(Exception):
    """Stand-in for frappe.PermissionError."""


def _row(name, docstatus=1, **over):
    row = types.SimpleNamespace(name=name, docstatus=docstatus, batch_id="b-" + name,
                                data_area_id="ZZA", fiscal_year=2099, fiscal_period=3, row_count=7)
    row.__dict__.update(over)
    return row


def _wire(rows):
    """Point the stubbed frappe at ``rows`` (what frappe.db.get_value returns per
    name) and record, in order, every MariaDB write and ClickHouse statement.
    Returns (log, gates, reads): log entries are ("set", name, field, value)
    or ("ch", sql); reads are the get_value kwargs."""
    log, gates, reads = [], [], []

    def get_value(doctype, name, fields, **kw):
        reads.append((doctype, name, tuple(fields), kw))
        return rows.get(name)

    def set_value(doctype, name, field, value, **kw):
        log.append(("set", name, field, value, kw))

    _m.execute = lambda sql, *a, **k: log.append(("ch", sql)) or ""
    _m.assert_open = lambda fy, fp, action="run": gates.append((fy, fp, action))
    _m.frappe.db = types.SimpleNamespace(get_value=get_value, set_value=set_value)
    _m.frappe.get_doc = lambda doctype, name: rows[name]  # not for the lock read; see the test
    _m.frappe.throw = _raise
    _m.frappe.PermissionError = _PermissionError
    del _ADMIN_CHECKS[:]
    return log, gates, reads


def _claims(log):
    return [e[1] for e in log if e[0] == "ch" and e[1].startswith(f"INSERT INTO {_m.CONTROL_TABLE} ")]


def test_set_amount_basis_updates_submitted_documents_and_skips_drafts():
    rows = {"TBS-1": _row("TBS-1", batch_id="b1"),
            "TBS-2": _row("TBS-2", docstatus=0, batch_id="b2", data_area_id="ZZB", row_count=1),
            "TBS-3": _row("TBS-3", docstatus=2, batch_id="b3")}
    log, gates, reads = _wire(rows)
    out = _m.set_amount_basis('["TBS-1", "TBS-2", "TBS-3", "TBS-9"]', " period-end BALANCE ")
    assert _ADMIN_CHECKS, "check_epm_admin() was not called"
    assert out["updated"] == 1
    assert [tuple(s) for s in out["skipped"]] == [
        ("TBS-2", "not submitted"), ("TBS-3", "cancelled"), ("TBS-9", "not found")]
    sets = [e for e in log if e[0] == "set"]
    assert [e[1:4] for e in sets] == [("TBS-1", "amount_basis", CLOSING)]
    assert gates == [(2099, 3, "set the amount basis of a trial balance")]
    claims = _claims(log)
    assert len(claims) == 1
    assert "fiscal_period, row_count, claimed_at, amount_basis) VALUES" in claims[0]
    assert "'b1', 'TBS-1', 'ZZA', 2099, 3, 7, now()" in claims[0]
    assert claims[0].endswith(f"now(), '{CLOSING}')")
    assert "b2" not in claims[0] and "b3" not in claims[0]
    # a plain list works too
    log, gates, reads = _wire(rows)
    assert _m.set_amount_basis(["TBS-1"], CLOSING)["updated"] == 1


def test_set_amount_basis_refuses_an_unknown_basis_before_touching_anything():
    rows = {"TBS-1": _row("TBS-1")}
    log, gates, reads = _wire(rows)
    try:
        _m.set_amount_basis(["TBS-1"], "balances")
        assert False, "expected a throw"
    except RuntimeError as e:
        assert "balances" in str(e)
        for basis in ALL_BASES:
            assert basis in str(e)
    assert log == [] and gates == [] and reads == []


# -- konsolidat#199 (K8): PR #201 review findings 2, 3, 5, 6 on set_amount_basis -----------------

def test_set_amount_basis_reads_each_document_under_a_row_lock():
    """Finding 2: judge docstatus on a locking read (the pattern
    _check_no_other_submission uses), never on frappe.get_doc: a concurrent
    cancel then waits for this request's commit, or this read sees docstatus 2
    and skips. The lock is what makes 'submitted' true at write time."""
    body = _inspect.getsource(_m.set_amount_basis)
    assert "frappe.db.get_value(" in body and "for_update=True" in body and "as_dict=True" in body
    assert "frappe.get_doc(" not in body
    rows = {"TBS-1": _row("TBS-1")}
    log, gates, reads = _wire(rows)
    _m.frappe.get_doc = lambda *a, **k: (_ for _ in ()).throw(AssertionError("get_doc was called"))
    _m.set_amount_basis(["TBS-1"], CLOSING)
    assert len(reads) == 1
    doctype, name, fields, kw = reads[0]
    assert (doctype, name) == ("Trial Balance Submission", "TBS-1")
    for f in ("docstatus", "fiscal_year", "fiscal_period", "batch_id", "data_area_id", "row_count"):
        assert f in fields, f
    assert kw.get("for_update") is True and kw.get("as_dict") is True


def test_set_amount_basis_gates_each_period_once_before_any_write():
    """Finding 3: three documents in two periods → two gate calls, both before
    the first MariaDB write, none repeated."""
    rows = {"TBS-1": _row("TBS-1", fiscal_period=3), "TBS-2": _row("TBS-2", fiscal_period=4),
            "TBS-3": _row("TBS-3", fiscal_period=3)}
    log, gates, reads = _wire(rows)
    order = []
    _m.assert_open = lambda fy, fp, action="run": (gates.append((fy, fp, action)), order.append("gate"))
    real_set = _m.frappe.db.set_value
    _m.frappe.db.set_value = lambda *a, **k: (order.append("set"), real_set(*a, **k))
    out = _m.set_amount_basis(["TBS-1", "TBS-2", "TBS-3"], CLOSING)
    assert out["updated"] == 3
    assert sorted(gates) == [(2099, 3, "set the amount basis of a trial balance"),
                             (2099, 4, "set the amount basis of a trial balance")]
    assert order == ["gate", "gate", "set", "set", "set"]


def test_set_amount_basis_writes_mariadb_first_then_one_claim_insert_for_all():
    """Finding 5: every MariaDB write happens before the ClickHouse INSERT, and
    there is ONE INSERT carrying every document's tuple — so a ClickHouse
    failure leaves nothing declared in the warehouse while MariaDB rolls back,
    and a success never leaves a half-declared set."""
    rows = {"TBS-1": _row("TBS-1", batch_id="b1"), "TBS-2": _row("TBS-2", batch_id="b2", row_count=9)}
    log, gates, reads = _wire(rows)
    out = _m.set_amount_basis(["TBS-1", "TBS-2"], "Year-to-date movement")
    assert out["updated"] == 2
    kinds = [e[0] for e in log]
    assert kinds == ["set", "set", "ch"], kinds
    assert log[0][1:4] == ("TBS-1", "amount_basis", "Year-to-date movement")
    assert log[0][4].get("update_modified") is False
    claims = _claims(log)
    assert len(claims) == 1
    assert "('b1', 'TBS-1', 'ZZA', 2099, 3, 7, now(), 'Year-to-date movement')" in claims[0]
    assert "('b2', 'TBS-2', 'ZZA', 2099, 3, 9, now(), 'Year-to-date movement')" in claims[0]
    assert claims[0].count("now()") == 2
    body = _inspect.getsource(_m.set_amount_basis)
    assert "ClickHouse" in body and "MariaDB" in body  # the docstring says why this order


def test_set_amount_basis_claims_in_batches_of_a_thousand():
    rows = {f"TBS-{i}": _row(f"TBS-{i}") for i in range(1001)}
    log, gates, reads = _wire(rows)
    assert _m.set_amount_basis(list(rows), CLOSING)["updated"] == 1001
    claims = _claims(log)
    assert len(claims) == 2
    assert claims[0].count("now()") == 1000 and claims[1].count("now()") == 1
    assert len(gates) == 1


def test_set_amount_basis_refuses_names_that_are_not_a_list():
    """Finding 6: a JSON object, a bare word or invalid JSON is one clear
    sentence, not a KeyError or a lookup of the object's keys."""
    rows = {"TBS-1": _row("TBS-1")}
    for bad in ('{"TBS-1": 1}', "TBS-1", "not json", "42", 42, None):
        log, gates, reads = _wire(rows)
        try:
            _m.set_amount_basis(bad, CLOSING)
            assert False, f"expected a throw for {bad!r}"
        except RuntimeError as e:
            assert str(e) == "names must be a JSON list of Trial Balance Submission names", bad
        assert log == [] and gates == [] and reads == []


def test_set_amount_basis_names_the_action_when_permission_is_refused():
    """A scoped user sees 'EPM Admin only: Set Amount Basis', a PermissionError,
    not a generic refusal from the admin check."""
    lifecycle = sys.modules["konsol.schema_lifecycle"]
    rows = {"TBS-1": _row("TBS-1")}
    log, gates, reads = _wire(rows)
    saved = lifecycle.check_epm_admin

    def refuse():
        raise _m.frappe.PermissionError("Not permitted")
    lifecycle.check_epm_admin = refuse
    try:
        _m.set_amount_basis(["TBS-1"], CLOSING)
        assert False, "expected a throw"
    except _Thrown as e:
        assert str(e) == "EPM Admin only: Set Amount Basis"
        assert e.exc is _m.frappe.PermissionError
    finally:
        lifecycle.check_epm_admin = saved
    assert log == [] and gates == [] and reads == []


def test_list_view_offers_set_amount_basis():
    assert os.path.exists(_TBS_LIST_JS), "trial_balance_submission_list.js is missing"
    with open(_TBS_LIST_JS) as f:
        js = f.read()
    assert 'frappe.listview_settings["Trial Balance Submission"]' in js
    assert "add_actions_menu_item" in js
    assert "get_checked_items(true)" in js
    assert "frappe.prompt" in js
    assert ".set_amount_basis" in js
    for basis in ALL_BASES:
        assert basis in js, basis
    assert "show_alert" in js and "refresh" in js


# ---------------------------------------------------------------------------
# konsol#255: the single upload has the same silent drop as the bulk one.
#
# parse_tb_csv checked only that _REQUIRED_COLUMNS were present, so any other
# column was never read and its values vanished without a word. Both intakes
# refuse now, or the bulk path would be stricter than the single one it feeds.
# ---------------------------------------------------------------------------

def test_parse_refuses_an_unrecognised_column_by_name():
    try:
        _m.parse_tb_csv("main_account,debit,credit,dim_cost_center\n1010,5,0,CC1\n")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "dim_cost_center" in str(e), str(e)


def test_parse_names_every_unrecognised_column_at_once():
    try:
        _m.parse_tb_csv("main_account,debit,credit,Region,notes\n1010,5,0,EMEA,x\n")
        assert False, "expected ValueError"
    except ValueError as e:
        msg = str(e)
        assert "region" in msg and "notes" in msg, msg


def test_parse_still_accepts_source_upload():
    """group_csv writes source_upload into the file it generates for each
    entity-period, so the single parser must go on accepting and ignoring it —
    refusing it would break the bulk path feeding its own output back in."""
    rows = _m.parse_tb_csv(
        "main_account,debit,credit,description,partner_data_area_id,source_upload\n"
        "1010,5,0,,,ZZ-UPLOAD\n")
    assert rows[0]["main_account"] == "1010"
    assert "source_upload" not in rows[0]


def test_parse_still_accepts_every_documented_column():
    rows = _m.parse_tb_csv(
        "Main_Account,Debit,Credit,Description,Counterparty,Amount Basis\n"
        "1010,5,0,Cash,AMUS,Period movement\n")
    assert rows[0]["partner_data_area_id"] == "AMUS"
    assert rows[0]["description"] == "Cash"
