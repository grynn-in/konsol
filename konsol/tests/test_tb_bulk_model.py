"""Bulk trial balance files (konsol/tb_bulk_model.py): split one file into
entity-periods, and never accept what a single upload would refuse."""
import ast
import csv
import importlib.util
import io
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("tb_bulk_model", os.path.join(APP_DIR, "tb_bulk_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

TB_BULK_PATH = os.path.join(APP_DIR, "tb_bulk.py")

HEADER = ["data_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit", "currency"]


def _raises(fn, *args):
    try:
        fn(*args)
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError")


def test_splits_into_entity_periods_in_file_order():
    table = [HEADER,
             ["AMDE", "2025", "12", "1010", "100", "", "EUR"],
             ["AMUS", "2025", "12", "1010", "", "5", "EUR"],
             ["AMDE", "2025", "12", "2010", "", "100", "EUR"],
             ["AMDE", "2024", "12", "1010", "1", "0", "EUR"]]
    groups = M.split_table(table)
    assert list(groups) == [("AMDE", 2025, 12), ("AMUS", 2025, 12), ("AMDE", 2024, 12)]
    assert [r["main_account"] for r in groups[("AMDE", 2025, 12)]] == ["1010", "2010"]
    assert groups[("AMUS", 2025, 12)][0] == {"main_account": "1010", "debit": 0.0, "credit": 5.0, "currency": "EUR",
                                             "description": "", "partner_data_area_id": "", "amount_basis": "",
                                             "line": 3}


def test_header_is_forgiving_about_case_spaces_and_aliases():
    table = [["Entity", "Year", "Period", "Account", "Debit", "Credit", "Currency", "Description"],
             ["AMDE", "2025", "1", "1010", "10.005", "0", "EUR", "cash"]]
    rows = M.split_table(table)[("AMDE", 2025, 1)]
    # konsol#180-5: read exactly, never rounded; the extra place is refused
    # by check_group once the currency is known
    assert str(rows[0]["debit"]) == "10.005"
    assert rows[0]["description"] == "cash"


def test_excel_cells_numbers_and_blank_lines():
    table = [HEADER, [None] * 6,
             ["AMDE", 2025.0, 12.0, 1010.0, 1234.5, None, "EUR"],
             ["AMDE", 2025, 12, 2010, None, 1234.5, "EUR", None, None]]   # trailing empty cells are fine
    rows = M.split_table(table)[("AMDE", 2025, 12)]
    assert [r["main_account"] for r in rows] == ["1010", "2010"]
    assert rows[0]["debit"] == 1234.5 and rows[1]["credit"] == 1234.5


def test_structural_problems_are_reported_together_with_line_numbers():
    table = [HEADER,
             ["", "2025", "12", "1010", "1", "0", "EUR"],
             ["AMDE", "twenty", "12", "1010", "1", "0", "EUR"],
             ["AMDE", "2025", "12", "1010", "abc", "0", "EUR"],
             ["AMDE", "2025", "12", "1010", "nan", "0", "EUR"],
             ["AMDE", "2025", "12", "1010", "1", "0", "EUR", "extra"]]
    msg = _raises(M.split_table, table)
    for expected in ("Line 2: data_area_id is blank", "Line 3: fiscal_year", "Line 4: debit must be a number",
                     "Line 5: debit must be a finite", "Line 6: more cells"):
        assert expected in msg, (expected, msg)


def test_missing_columns_and_empty_files():
    no_credit = [h for h in HEADER if h != "credit"]
    assert "Missing column(s) credit" in _raises(M.split_table, [no_credit, ["AMDE", "2025", "1", "1", "1", "EUR"]])
    assert "empty" in _raises(M.split_table, [])
    assert "no data rows" in _raises(M.split_table, [HEADER])


def test_group_csv_is_the_single_upload_contract():
    rows = [{"main_account": "1010", "debit": 1234.5, "credit": 0.0, "currency": "EUR", "description": "cash, main"}]
    parsed = list(csv.DictReader(io.StringIO(M.group_csv(rows))))
    # exactly as read (konsol#180-5), no longer padded or rounded to 2 places
    assert parsed == [{"main_account": "1010", "debit": "1234.5", "credit": "0.0", "currency": "EUR",
                       "description": "cash, main", "partner_data_area_id": ""}]


def _check(**over):
    facts = dict(known_accounts={"1010", "2010"}, visible=True, leaf=True,
                 period={"code": "P12", "type": "Regular", "status": "Open"}, postable_types={"Regular"},
                 existing=None, validate_rows=lambda rows, **kw: [], functional_currency="EUR",
                 minor_unit=2)
    facts.update(over)
    rows = [{"main_account": "1010", "debit": 5.0, "credit": 0.0, "currency": "EUR"},
            {"main_account": "2010", "debit": 0.0, "credit": 5.0, "currency": "EUR"}]
    return M.check_group(("AMDE", 2025, 12), rows, **facts)


def test_a_clean_group_is_ready():
    r = _check()
    assert r["ok"] and r["rows"] == 2 and r["total_debit"] == 5.0 and r["total_credit"] == 5.0


def test_every_single_upload_rule_applies():
    assert "no access" in _check(visible=False)["errors"][0]
    assert "is a group" in _check(leaf=False)["errors"][0]
    assert "already submitted" in _check(existing="TBS-00001")["errors"][0]
    # the single-submission validator's verdict is carried through as-is
    r = _check(validate_rows=lambda rows, **kw: ["Debits (5.00) do not equal credits"])
    assert not r["ok"] and r["errors"] == ["Debits (5.00) do not equal credits"]


def test_closed_period_refused():
    r = _check(period={"code": "P12", "type": "Regular", "status": "Closed"})
    assert "is closed" in r["errors"][0]


def test_undeclared_period_refused():
    r = M.check_group(("AMDE", 2025, 14), [], known_accounts=set(), visible=True, leaf=True, period=None,
                      postable_types={"Regular"}, existing=None, validate_rows=lambda rows, **kw: [],
                      functional_currency="EUR", minor_unit=2)
    assert r["errors"][0] == "FY2025 P14 is not declared"


def test_unpostable_type_refused():
    r = _check(period={"code": "CLS", "type": "Closing", "status": "Open"}, postable_types={"Regular"})
    assert "does not take trial balances on this site" in r["errors"][0]


def test_outcome():
    assert M.outcome(3, 0, 3) == "Loaded"
    assert M.outcome(2, 1, 3) == "Partly Loaded"
    assert M.outcome(0, 3, 3) == "Failed"
    assert M.outcome(0, 0, 0) == "Failed"


def test_a_byte_order_mark_before_the_header_is_ignored():
    table = [["\ufeffdata_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit", "currency"],
             ["AMDE", "2025", "12", "1010", "1", "0", "EUR"]]
    assert list(M.split_table(table)) == [("AMDE", 2025, 12)]


def test_loaded_rows_are_carried_forward_and_never_loaded_again():
    previous = [{"entity": "AMDE", "fiscal_year": 2025, "fiscal_period": 12, "loaded": "TBS-1"},
                {"entity": "AMUS", "fiscal_year": 2025, "fiscal_period": 12, "load_error": "boom"}]
    fresh = [
        {"entity": "AMDE", "fiscal_year": 2025, "fiscal_period": 12, "ok": False,
         "errors": ["TBS-1 is already submitted"], "existing": "TBS-1"},
        {"entity": "AMUS", "fiscal_year": 2025, "fiscal_period": 12, "ok": True, "errors": [], "existing": None},
        # submitted by someone else in between: a real problem, not ours
        {"entity": "AMHQ", "fiscal_year": 2025, "fiscal_period": 12, "ok": False,
         "errors": ["TBS-9 is already submitted"], "existing": "TBS-9"},
    ]
    out = M.merge_loaded(fresh, previous)
    assert out[0]["loaded"] == "TBS-1" and out[0]["ok"] and out[0]["errors"] == []
    assert "loaded" not in out[1] and out[1]["ok"]
    assert "loaded" not in out[2] and not out[2]["ok"]


def test_the_report_names_the_existing_submission():
    assert _check(existing="TBS-7")["existing"] == "TBS-7"


def test_a_row_loaded_by_a_stopped_run_is_recognised_by_its_file():
    fresh = [{"entity": "AMDE", "fiscal_year": 2025, "fiscal_period": 12, "ok": False, "errors": ["already"],
              "existing": "TBS-5", "existing_file": "/private/files/TBU-00009-AMDE-2025-P12.csv"}]
    assert M.merge_loaded(fresh, [], "TBU-00009")[0]["loaded"] == "TBS-5"
    # another upload's file is not ours
    assert "loaded" not in M.merge_loaded(fresh, [], "TBU-00010")[0]


def test_a_loaded_row_since_cancelled_is_a_problem_not_a_reload():
    previous = [{"entity": "AMDE", "fiscal_year": 2025, "fiscal_period": 12, "loaded": "TBS-1"}]
    fresh = [{"entity": "AMDE", "fiscal_year": 2025, "fiscal_period": 12, "ok": True, "errors": [], "existing": None}]
    out = M.merge_loaded(fresh, previous, "TBU-00001")[0]
    assert not out["ok"] and "since been cancelled" in out["errors"][0] and "loaded" not in out


def test_generated_files_name_their_upload_and_still_parse_as_a_single_upload():
    rows = [{"main_account": "1010", "debit": 1.0, "credit": 0.0, "description": ""}]
    text = M.group_csv(rows, source="TBU-00042")
    parsed = list(csv.DictReader(io.StringIO(text)))
    assert parsed[0]["source_upload"] == "TBU-00042"
    # exactly as read (konsol#180-5), no longer padded or rounded to 2 places
    assert {k: parsed[0][k] for k in ("main_account", "debit", "credit")} == {"main_account": "1010", "debit": "1.0", "credit": "0.0"}
    # two uploads of the same figures produce different files
    assert M.group_csv(rows, source="TBU-00001") != M.group_csv(rows, source="TBU-00002")


# --- konsol#159: the intercompany partner -----------------------------------

def test_the_partner_column_and_its_aliases_are_carried_to_each_row():
    for name in ("partner_data_area_id", "Partner", "partner entity", "counterparty"):
        table = [HEADER + [name],
                 ["ZZA", "2099", "1", "4030", "", "100", "EUR", "ZZB"],
                 ["ZZA", "2099", "1", "1010", "100", "", "EUR", ""]]
        rows = M.split_table(table)[("ZZA", 2099, 1)]
        assert [r["partner_data_area_id"] for r in rows] == ["ZZB", ""], name
    assert "Two partner columns" in _raises(M.split_table, [HEADER + ["partner", "counterparty"]])


def test_group_csv_carries_the_partner_to_the_single_upload():
    rows = [{"main_account": "4030", "debit": 0.0, "credit": 100.0, "description": "", "partner_data_area_id": "ZZB"}]
    parsed = list(csv.DictReader(io.StringIO(M.group_csv(rows, source="TBU-1"))))
    assert parsed[0]["partner_data_area_id"] == "ZZB"


def test_check_group_hands_the_partner_facts_to_the_single_validator_and_reports_warnings():
    seen = {}

    def validate(rows, **kw):
        seen.update(kw)
        return []

    r = _check(validate_rows=validate, known_entities={"ZZA", "ZZB"},
               warnings=["1 intercompany row without a partner"], partnerless_ic_rows=1)
    assert seen == {"known_accounts": {"1010", "2010"}, "entity": "AMDE", "known_entities": {"ZZA", "ZZB"}}
    # a warning never stops the load
    assert r["ok"] and r["warnings"] == ["1 intercompany row without a partner"] and r["partnerless_ic_rows"] == 1
    assert _check()["warnings"] == [] and _check()["partnerless_ic_rows"] == 0


# --- konsol#189 task 20: tb_bulk._check uses the real declared-period lookup ---
# tb_bulk.py imports frappe and several konsol modules at the top level; every
# one of them is stubbed here (the pattern test_chart_upload.py established),
# so _check can be exercised without a live site. The stubs are installed only
# around the module load and the call to _check, then restored, so they never
# leak into other test files sharing this process (scripts/run-host-tests.py).

HEADER_ROW = ["data_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit", "currency"]


#: konsol#180: each ISO Currency's minor_unit, as a site holds it.
MINOR_UNITS = {"EUR": 2, "USD": 2, "JPY": 0, "KWD": 3}


def _load_tb_bulk(*, entities, postable, period_lookup, currencies=None, visible=None, minor_units=None):
    """Load konsol/tb_bulk.py with every non-model import stubbed.

    `period_lookup` maps (year, period) -> a period fact dict; a pair absent
    from it is undeclared, so the stand-in period_status.period_row raises
    PeriodNotDeclared for it, exactly as the real one does. `postable` is
    what postable_types() returns. `currencies` maps an entity to its
    Functional Currency ('' for none); an entity absent from it has EUR.
    `minor_units` maps a currency to its ISO Currency.minor_unit (MINOR_UNITS
    unless a test says otherwise; None stands for a blank one); a currency
    absent from it has no ISO Currency row. Returns (module, calls), where calls
    counts how many times postable_types() was called.
    """
    frappe = types.ModuleType("frappe")
    frappe_utils = types.ModuleType("frappe.utils")
    frappe_utils.cint = lambda v: int(v or 0)
    frappe_utils.strip_html = lambda v: v
    frappe.utils = frappe_utils
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.has_permission = lambda *a, **k: True
    frappe.PermissionError = type("PermissionError", (Exception,), {})

    def get_list(doctype, filters=None, pluck=None, limit_page_length=None):
        assert doctype == "Entity"
        # `visible`: the entities the uploader's scope lets get_list return
        # (every one unless a test narrows it).
        return list(entities if visible is None else visible)

    def get_all(doctype, filters=None, fields=None, pluck=None, limit_page_length=None):
        if doctype == "Entity" and fields:
            # konsol#252: each entity's Functional Currency; EUR unless a test says otherwise.
            return [types.SimpleNamespace(name=e, functional_currency=(currencies or {}).get(e, "EUR"))
                    for e in entities]
        if doctype == "Entity":
            return list(entities)
        if doctype == "Trial Balance Submission":
            return []
        if doctype == "ISO Currency":
            # konsol#180: each currency's minor unit, as a site holds it. The
            # filter is honoured, as get_all does: only the named currencies.
            units = MINOR_UNITS if minor_units is None else minor_units
            assert set(filters or {}) == {"name"} and filters["name"][0] == "in", filters
            return [types.SimpleNamespace(name=c, minor_unit=units[c])
                    for c in filters["name"][1] if c in units]
        raise AssertionError(doctype)

    frappe.get_list, frappe.get_all = get_list, get_all

    period_status = types.ModuleType("konsol.period_status")

    class PeriodNotDeclared(Exception):
        pass

    def period_row(fiscal_year, fiscal_period):
        fact = period_lookup.get((int(fiscal_year), int(fiscal_period)))
        if fact is None:
            raise PeriodNotDeclared(f"FY{fiscal_year} P{fiscal_period} is not declared")
        return fact

    calls = {"postable_types": 0}

    def postable_types_fn():
        calls["postable_types"] += 1
        return set(postable)

    period_status.PeriodNotDeclared = PeriodNotDeclared
    period_status.period_row = period_row
    period_status.postable_types = postable_types_fn

    clickhouse = types.ModuleType("konsol.clickhouse")
    clickhouse.execute = lambda *a, **k: ""

    tbs_mod = types.ModuleType("konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission")
    tbs_mod.CONTROL_TABLE = "tb_control"
    tbs_mod._sql_str = lambda s: s
    tbs_mod.partnerless_ic_accounts = lambda rows, ic: []
    tbs_mod.partnerless_warning = lambda n: f"{n} without a partner"
    tbs_mod.validate_tb_rows = lambda rows, **kw: []

    entity_permissions = types.ModuleType("konsol.entity_permissions")
    entity_permissions.allowed_entity_codes = lambda user=None: None

    group_chart = types.ModuleType("konsol.group_chart")
    group_chart.chart_accounts = lambda: {}

    # konsol#255: _check now reads the site's declared dimensions before it
    # splits the file. That reader binds frappe, so it is stubbed; these tests
    # are about periods, and a site that declares no dimension is the case
    # they describe.
    tb_dimension = types.ModuleType("konsol.tb_dimension")
    tb_dimension.declared_dimensions = lambda: []

    ica_mod = types.ModuleType("konsol.consolidation.doctype.intercompany_account.intercompany_account")
    ica_mod.intercompany_accounts = lambda: []

    konsol_pkg = types.ModuleType("konsol")
    konsol_pkg.tb_bulk_model = M
    konsol_pkg.period_status = period_status

    stubs = {
        "frappe": frappe, "frappe.utils": frappe_utils,
        "konsol": konsol_pkg, "konsol.tb_bulk_model": M,
        "konsol.period_status": period_status, "konsol.clickhouse": clickhouse,
        "konsol.consolidation.doctype.trial_balance_submission.trial_balance_submission": tbs_mod,
        "konsol.entity_permissions": entity_permissions, "konsol.group_chart": group_chart,
        "konsol.tb_dimension": tb_dimension,
        "konsol.consolidation.doctype.intercompany_account.intercompany_account": ica_mod,
    }
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("tb_bulk_under_test", TB_BULK_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return mod, calls


def test_bulk_check_uses_declared_period_facts():
    table = [HEADER_ROW,
             ["AMDE", "2025", "14", "1010", "5", "0", "EUR"],
             ["AMDE", "2025", "14", "2010", "0", "5", "EUR"],
             ["AMDE", "2025", "13", "1010", "5", "0", "EUR"],
             ["AMDE", "2025", "13", "2010", "0", "5", "EUR"],
             ["AMDE", "2025", "3", "1010", "5", "0", "EUR"],
             ["AMDE", "2025", "3", "2010", "0", "5", "EUR"]]
    # P14 declared as Adjustment; P13 absent (undeclared); P03 declared Regular.
    period_lookup = {
        (2025, 14): {"code": "P14", "type": "Adjustment", "status": "Open"},
        (2025, 3): {"code": "P03", "type": "Regular", "status": "Open"},
    }

    mod, calls = _load_tb_bulk(entities=["AMDE"], postable={"Regular"}, period_lookup=period_lookup)
    _, report = mod._check(table, PERIOD)
    by_period = {r["fiscal_period"]: r for r in report}

    # P14: declared, but Adjustment is not postable on this site.
    assert not by_period[14]["ok"]
    assert "does not take trial balances on this site" in by_period[14]["errors"][0]
    # P13: not declared at all.
    assert not by_period[13]["ok"]
    assert by_period[13]["errors"][0] == "FY2025 P13 is not declared"
    # P03: declared Regular and open.
    assert by_period[3]["ok"]
    # postable_types() is read once per file, not once per group.
    assert calls["postable_types"] == 1

    # With Adjustment postable on this site, the same P14 group now passes.
    mod2, _ = _load_tb_bulk(entities=["AMDE"], postable={"Regular", "Adjustment"}, period_lookup=period_lookup)
    _, report2 = mod2._check(table, PERIOD)
    assert {r["fiscal_period"]: r["ok"] for r in report2}[14]


# --- konsolidat#199: every entity-period carries its amount basis -------------

PERIOD, YTD, CLOSING = "Period movement", "Year-to-date movement", "Period-end balance"


def test_the_basis_column_and_its_aliases_are_carried_to_each_row():
    for name in ("amount_basis", "Basis", "Amount Basis"):
        table = [HEADER + [name],
                 ["ZZA", "2099", "1", "1010", "100", "", "EUR", "period-end balance"],
                 ["ZZA", "2099", "1", "2010", "", "100", "EUR", " PERIOD-END BALANCE "]]
        rows = M.split_table(table)[("ZZA", 2099, 1)]
        assert [r["amount_basis"] for r in rows] == [CLOSING, CLOSING], name
    assert "Two amount_basis columns" in _raises(M.split_table, [HEADER + ["basis", "amount_basis"]])


def test_without_the_column_rows_carry_no_basis():
    rows = M.split_table([HEADER, ["ZZA", "2099", "1", "1010", "1", "0", "EUR"]])[("ZZA", 2099, 1)]
    assert rows[0]["amount_basis"] == ""
    assert M.group_basis(rows) == ""


def test_each_group_carries_its_own_basis_and_a_blank_cell_is_not_given():
    table = [HEADER + ["amount_basis"],
             ["ZZA", "2099", "1", "1010", "1", "0", "EUR", YTD],
             ["ZZA", "2099", "1", "2010", "0", "1", "EUR", ""],          # blank: not given, so no conflict
             ["ZZB", "2099", "1", "1010", "1", "0", "EUR", PERIOD],
             ["ZZC", "2099", "1", "1010", "1", "0", "EUR", ""]]
    groups = M.split_table(table)
    assert M.group_basis(groups[("ZZA", 2099, 1)]) == YTD
    assert M.group_basis(groups[("ZZB", 2099, 1)]) == PERIOD
    assert M.group_basis(groups[("ZZC", 2099, 1)]) == ""


def test_mixed_bases_in_one_entity_period_are_a_line_error_naming_both():
    table = [HEADER + ["amount_basis"],
             ["ZZA", "2099", "1", "1010", "1", "0", "EUR", PERIOD],
             ["ZZA", "2099", "1", "2010", "0", "1", "EUR", CLOSING],
             ["ZZB", "2099", "1", "1010", "1", "0", "EUR", CLOSING]]     # another entity may differ
    msg = _raises(M.split_table, table)
    assert "Line 3" in msg and PERIOD in msg and CLOSING in msg
    assert "Line 4" not in msg


def test_an_unknown_basis_value_is_a_line_error():
    table = [HEADER + ["amount_basis"], ["ZZA", "2099", "1", "1010", "1", "0", "EUR", "balances"]]
    msg = _raises(M.split_table, table)
    assert "Line 2" in msg and "balances" in msg and PERIOD in msg


def test_resolve_basis_prefers_the_file_then_the_form_and_refuses_neither():
    assert M.resolve_basis(CLOSING, PERIOD) == (CLOSING, None)
    assert M.resolve_basis("", " period movement ") == (PERIOD, None)
    assert M.resolve_basis(None, YTD) == (YTD, None)
    basis, problem = M.resolve_basis("", "")
    assert basis is None and "Amount Basis" in problem and PERIOD in problem and CLOSING in problem
    basis, problem = M.resolve_basis("", "balances")
    assert basis is None and "balances" in problem


def test_group_csv_carries_the_basis_to_the_single_upload_only_when_given():
    rows = [{"main_account": "1010", "debit": 1.0, "credit": 0.0, "description": "", "amount_basis": CLOSING}]
    parsed = list(csv.DictReader(io.StringIO(M.group_csv(rows, source="TBU-1"))))
    assert parsed[0]["amount_basis"] == CLOSING
    bare = [{"main_account": "1010", "debit": 1.0, "credit": 0.0, "description": "", "amount_basis": ""}]
    assert "amount_basis" not in M.group_csv(bare, source="TBU-1").splitlines()[0]


def test_bulk_check_refuses_an_entity_period_without_a_basis_and_carries_the_resolved_one():
    table = [HEADER_ROW + ["amount_basis"],
             ["AMDE", "2025", "3", "1010", "5", "0", "EUR", CLOSING],
             ["AMDE", "2025", "3", "2010", "0", "5", "EUR", ""],
             ["AMUS", "2025", "3", "1010", "5", "0", "EUR", ""],
             ["AMUS", "2025", "3", "2010", "0", "5", "EUR", ""]]
    period_lookup = {(2025, 3): {"code": "P03", "type": "Regular", "status": "Open"}}
    mod, _ = _load_tb_bulk(entities=["AMDE", "AMUS"], postable={"Regular"}, period_lookup=period_lookup)

    # no basis on the upload: only the group without its own column is refused
    _, report = mod._check(table, "")
    by_entity = {r["entity"]: r for r in report}
    assert by_entity["AMDE"]["ok"] and by_entity["AMDE"]["amount_basis"] == CLOSING
    assert not by_entity["AMUS"]["ok"] and "Amount Basis" in by_entity["AMUS"]["errors"][0]
    assert by_entity["AMUS"]["amount_basis"] == ""

    # the upload's basis fills in; the file's own value still wins
    _, report = mod._check(table, PERIOD)
    by_entity = {r["entity"]: r for r in report}
    assert by_entity["AMDE"]["ok"] and by_entity["AMDE"]["amount_basis"] == CLOSING
    assert by_entity["AMUS"]["ok"] and by_entity["AMUS"]["amount_basis"] == PERIOD


def test_temp_helper_is_gone():
    tree = ast.parse(open(TB_BULK_PATH).read(), TB_BULK_PATH)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert "_temp_period_fact" not in names
    assert "_TEMP_POSTABLE_TYPES" not in names
    assert "TEMP until konsol#189 task 20" not in open(TB_BULK_PATH).read()


# ---------------------------------------------------------------------------
# konsol#255: an unrecognised header is refused, not silently dropped.
#
# split_table checked only that the six required columns were PRESENT. Every
# other column was never read, so a file carrying cost centres loaded cleanly
# and arrived with the cost centres gone -- the loader dropping data without
# saying so, which is konsol#247 broken in the intake itself.
# ---------------------------------------------------------------------------

def test_an_unrecognised_header_is_refused_by_name():
    table = [HEADER + ["dim_cost_center"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "CC100"]]
    msg = _raises(M.split_table, table)
    assert "dim_cost_center" in msg, msg


def test_the_refusal_names_every_unrecognised_header_at_once():
    """A file is fixed in one pass, like the line errors above."""
    table = [HEADER + ["cost centre", "Region", "notes"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "CC1", "EMEA", "x"]]
    msg = _raises(M.split_table, table)
    for expected in ("cost_centre", "region", "notes"):
        assert expected in msg, (expected, msg)


def test_the_refusal_says_what_is_accepted():
    table = [HEADER + ["nonsense"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "x"]]
    msg = _raises(M.split_table, table)
    assert "main_account" in msg and "debit" in msg, msg


def test_every_documented_header_still_loads():
    """The full accepted set, including the aliases, stays accepted."""
    table = [["Entity", "Year", "Period", "Account", "Debit", "Credit", "Currency",
              "Description", "Counterparty", "Amount Basis"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "cash", "AMUS",
              "Period movement"]]
    rows = M.split_table(table)[("AMDE", 2025, 12)]
    assert rows[0]["partner_data_area_id"] == "AMUS"
    assert rows[0]["description"] == "cash"


def test_a_blank_trailing_header_is_not_an_unknown_column():
    """Excel writes a trailing comma; an empty header name is not a column."""
    table = [HEADER + [""],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", ""]]
    rows = M.split_table(table)[("AMDE", 2025, 12)]
    assert rows[0]["main_account"] == "1010"


# ---------------------------------------------------------------------------
# konsol#255: a DECLARED dim_* column is accepted and its values are carried.
#
# The refusal above is the right default -- an unrecognised column is never
# dropped in silence -- but a site that has declared dim_cost_center and ticked
# in_trial_balance on it must be able to send that column. split_table takes
# the declared dimensions as an argument and stays pure; the query that finds
# them lives at the call site.
# ---------------------------------------------------------------------------

def declared(name, status="Published", in_trial_balance=1):
    return {"dimension_name": name, "status": status,
            "in_trial_balance": in_trial_balance}


def test_a_declared_dimension_column_is_accepted_and_its_value_lands():
    table = [HEADER + ["dim_cost_center", "dim_department"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "CC100", "D7"]]
    rows = M.split_table(table, [declared("dim_cost_center"),
                                 declared("dim_department")])[("AMDE", 2025, 12)]
    assert rows[0]["dim_cost_center"] == "CC100"
    assert rows[0]["dim_department"] == "D7"


def test_a_blank_dimension_cell_is_legal_and_lands_as_empty():
    """A dimension is optional per row: blank is a value, not a refusal."""
    table = [HEADER + ["dim_cost_center"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", ""],
             ["AMDE", "2025", "12", "2010", "0", "100", "EUR", "CC100"]]
    rows = M.split_table(table, [declared("dim_cost_center")])[("AMDE", 2025, 12)]
    assert rows[0]["dim_cost_center"] == ""
    assert rows[1]["dim_cost_center"] == "CC100"


def test_an_undeclared_dimension_column_is_refused_as_undeclared():
    table = [HEADER + ["dim_widget"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "W1"]]
    msg = _raises(M.split_table, table, [declared("dim_cost_center")])
    assert "dim_widget" in msg, msg
    assert "not declared" in msg.lower(), msg
    assert "Unrecognised column" not in msg, msg


def test_a_flag_off_dimension_column_is_refused_saying_the_flag_is_off():
    table = [HEADER + ["dim_project"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "P1"]]
    msg = _raises(M.split_table, table,
                  [declared("dim_project", in_trial_balance=0)])
    assert "dim_project" in msg, msg
    assert "in_trial_balance" in msg, msg
    assert "Unrecognised column" not in msg, msg


def test_a_draft_dimension_column_is_refused_as_not_published():
    table = [HEADER + ["dim_cost_center"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "CC1"]]
    msg = _raises(M.split_table, table,
                  [declared("dim_cost_center", status="Draft")])
    assert "dim_cost_center" in msg, msg
    assert "not published" in msg.lower(), msg
    assert "Draft" in msg, msg


def test_a_bad_dimension_header_and_a_bad_ordinary_header_are_reported_together():
    """One pass fixes the file: both kinds of problem in the one refusal."""
    table = [HEADER + ["dim_widget", "notes"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "W1", "x"]]
    msg = _raises(M.split_table, table, [declared("dim_cost_center")])
    assert "dim_widget" in msg, msg
    assert "notes" in msg, msg
    assert "Unrecognised column" in msg, msg


def test_group_csv_writes_the_dimension_columns_the_rows_carry():
    table = [HEADER + ["dim_cost_center", "dim_department"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "CC100", ""]]
    rows = M.split_table(table, [declared("dim_cost_center"),
                                 declared("dim_department")])[("AMDE", 2025, 12)]
    text = M.group_csv(rows)
    header = next(csv.reader(io.StringIO(text)))
    assert header[:6] == ["main_account", "debit", "credit", "currency", "description",
                          "partner_data_area_id"]
    assert header[-2:] == ["dim_cost_center", "dim_department"], header
    out = list(csv.DictReader(io.StringIO(text)))
    assert out[0]["dim_cost_center"] == "CC100"
    assert out[0]["dim_department"] == ""


def test_group_csv_writes_no_dimension_columns_when_the_rows_carry_none():
    table = [HEADER, ["AMDE", "2025", "12", "1010", "100", "0", "EUR"]]
    rows = M.split_table(table)[("AMDE", 2025, 12)]
    header = next(csv.reader(io.StringIO(M.group_csv(rows))))
    assert header == ["main_account", "debit", "credit", "currency", "description",
                      "partner_data_area_id"]


def test_without_declared_dimensions_a_dim_column_is_still_refused():
    """Every existing caller passes nothing: a site declaring no dimensions
    carries no dim_* column, and the refusal still names the header."""
    table = [HEADER + ["dim_cost_center"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "CC100"]]
    msg = _raises(M.split_table, table)
    assert "dim_cost_center" in msg, msg
    rows = M.split_table([HEADER, ["AMDE", "2025", "12", "1010", "100", "0", "EUR"]])
    assert rows[("AMDE", 2025, 12)][0] == {
        "main_account": "1010", "debit": 100.0, "credit": 0.0, "currency": "EUR",
        "description": "", "partner_data_area_id": "", "amount_basis": "", "line": 2}


# ---------------------------------------------------------------------------
# konsol#255: a REPEATED dim_* column is refused, not silently halved.
#
# Measured on this branch before the fix: a header naming dim_cost_center twice
# was ACCEPTED and the second column's value vanished --
#
#   header: ...,dim_cost_center,dim_cost_center   row: ...,CC100,CC999
#   -> {'main_account': '1010', ..., 'dim_cost_center': 'CC100'}   CC999 gone
#
# because the header is resolved to a column index with names.index(n), which
# takes the first occurrence. partner_data_area_id and amount_basis already had
# a "keep one" guard for exactly this; dimensions were added without it.
# ---------------------------------------------------------------------------

def test_a_repeated_dimension_column_is_refused_naming_the_dimension():
    table = [HEADER + ["dim_cost_center", "dim_cost_center"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "CC100", "CC999"]]
    msg = _raises(M.split_table, table, [declared("dim_cost_center")])
    assert "dim_cost_center" in msg, msg
    assert "keep one" in msg, msg


def test_two_repeated_dimensions_are_both_named_in_one_refusal():
    """A file is fixed in one pass: every repeated dimension is named."""
    table = [HEADER + ["dim_cost_center", "dim_department",
                       "dim_cost_center", "dim_department"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "CC1", "D1", "CC2", "D2"]]
    msg = _raises(M.split_table, table, [declared("dim_cost_center"),
                                         declared("dim_department")])
    assert "dim_cost_center" in msg, msg
    assert "dim_department" in msg, msg


def test_two_different_dimensions_each_appearing_once_still_load():
    table = [HEADER + ["dim_cost_center", "dim_department"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "CC100", "D7"]]
    rows = M.split_table(table, [declared("dim_cost_center"),
                                 declared("dim_department")])[("AMDE", 2025, 12)]
    assert rows[0]["dim_cost_center"] == "CC100"
    assert rows[0]["dim_department"] == "D7"


def test_a_repeated_undeclared_dimension_column_is_refused_as_undeclared():
    """The undeclared refusal comes first and stands alone: the reader is told
    to declare the dimension, not confusingly told both things at once."""
    table = [HEADER + ["dim_widget", "dim_widget"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "W1", "W2"]]
    msg = _raises(M.split_table, table, [declared("dim_cost_center")])
    assert "dim_widget" in msg, msg
    assert "not declared" in msg.lower(), msg
    assert "keep one" not in msg, msg


def test_the_partner_keep_one_refusal_is_unchanged():
    table = [HEADER + ["partner_data_area_id", "partner"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "AMUS", "AMUK"]]
    msg = _raises(M.split_table, table)
    assert "Two partner columns" in msg, msg


def test_the_amount_basis_keep_one_refusal_is_unchanged():
    table = [HEADER + ["amount_basis", "basis"],
             ["AMDE", "2025", "12", "1010", "100", "0", "EUR", "Actual", "Actual"]]
    msg = _raises(M.split_table, table)
    assert "Two amount_basis columns" in msg, msg


# --- konsol#252: each entity-period's currency against its entity's -------------

def test_bulk_check_reads_each_entitys_functional_currency_and_refuses_per_entity_period():
    """One file, three entities: a match loads, a mismatch and an entity with no
    Functional Currency are refused on their own report rows, naming the values."""
    table = [HEADER_ROW,
             ["ZZA", "2099", "1", "1010", "5", "0", "EUR"],
             ["ZZA", "2099", "1", "2010", "0", "5", "EUR"],
             ["ZZB", "2099", "1", "1010", "5", "0", "EUR"],
             ["ZZB", "2099", "1", "2010", "0", "5", "EUR"],
             ["ZZC", "2099", "1", "1010", "5", "0", "EUR"],
             ["ZZC", "2099", "1", "2010", "0", "5", "EUR"]]
    period_lookup = {(2099, 1): {"code": "P01", "type": "Regular", "status": "Open"}}
    mod, _ = _load_tb_bulk(entities=["ZZA", "ZZB", "ZZC"], postable={"Regular"},
                           period_lookup=period_lookup,
                           currencies={"ZZA": "EUR", "ZZB": "JPY", "ZZC": ""})
    _, report = mod._check(table, PERIOD)
    by_entity = {r["entity"]: r for r in report}
    assert by_entity["ZZA"]["ok"], by_entity["ZZA"]
    assert not by_entity["ZZB"]["ok"]
    assert any("The file declares EUR but Entity ZZB's Functional Currency is JPY" in e
               for e in by_entity["ZZB"]["errors"]), by_entity["ZZB"]
    assert not by_entity["ZZC"]["ok"]
    assert any("Entity ZZC has no Functional Currency" in e for e in by_entity["ZZC"]["errors"]), by_entity["ZZC"]


def test_bulk_check_refuses_mixed_currencies_within_one_entity_period():
    table = [HEADER_ROW,
             ["ZZA", "2099", "1", "1010", "5", "0", "EUR"],
             ["ZZA", "2099", "1", "2010", "0", "5", "USD"]]
    period_lookup = {(2099, 1): {"code": "P01", "type": "Regular", "status": "Open"}}
    mod, _ = _load_tb_bulk(entities=["ZZA"], postable={"Regular"}, period_lookup=period_lookup,
                           currencies={"ZZA": "EUR"})
    _, report = mod._check(table, PERIOD)
    assert not report[0]["ok"] and any("more than one currency" in e for e in report[0]["errors"]), report


# --- PR #328 review L1: the currency rule never speaks for an entity the
# uploader cannot see. "does not exist, or you have no access" is ambiguous on
# purpose; a currency sentence would say the entity exists and what its
# Functional Currency is (or that it has none).

def test_check_group_says_nothing_about_the_currency_of_an_entity_it_cannot_see():
    for functional in ("EUR", "", None):
        r = _check(visible=False, functional_currency=functional,
                   validate_rows=lambda rows, **kw: [])
        assert r["errors"] == ["Entity AMDE does not exist, or you have no access to it"], (functional, r)


def test_bulk_check_names_no_currency_for_an_out_of_scope_entity():
    table = [HEADER_ROW,
             ["ZZB", "2099", "1", "1010", "5", "0", "USD"],
             ["ZZB", "2099", "1", "2010", "0", "5", "USD"],
             ["ZZC", "2099", "1", "1010", "5", "0", "USD"],
             ["ZZC", "2099", "1", "2010", "0", "5", "USD"]]
    period_lookup = {(2099, 1): {"code": "P01", "type": "Regular", "status": "Open"}}
    mod, _ = _load_tb_bulk(entities=["ZZB", "ZZC"], visible=[], postable={"Regular"},
                           period_lookup=period_lookup, currencies={"ZZB": "EUR", "ZZC": ""})
    _, report = mod._check(table, PERIOD)
    for r in report:
        assert not r["ok"], r
        assert not any("urrency" in e for e in r["errors"]), r


# --- PR #328 review L2: a bulk refusal names the file lines, as a single
# upload does, so a blank or mixed currency in a big file can be found.

def test_split_table_carries_each_rows_file_line():
    rows = M.split_table([HEADER, [None] * 7,
                          ["AMDE", "2025", "12", "1010", "5", "0", "EUR"],
                          ["AMDE", "2025", "12", "2010", "0", "5", "EUR"]])[("AMDE", 2025, 12)]
    assert [r["line"] for r in rows] == [3, 4]


def test_bulk_check_names_the_lines_of_a_blank_or_mixed_currency():
    table = [HEADER_ROW,
             ["ZZA", "2099", "1", "1010", "5", "0", "EUR"],
             ["ZZA", "2099", "1", "2010", "0", "5", "USD"],
             ["ZZA", "2099", "1", "3010", "0", "0", ""]]
    period_lookup = {(2099, 1): {"code": "P01", "type": "Regular", "status": "Open"}}
    mod, _ = _load_tb_bulk(entities=["ZZA"], postable={"Regular"}, period_lookup=period_lookup,
                           currencies={"ZZA": "EUR"})
    _, report = mod._check(table, PERIOD)
    errors = report[0]["errors"]
    assert any("The currency is blank on line 4" in e for e in errors), errors
    assert any("(EUR on line 2; USD on line 3)" in e for e in errors), errors


# --- konsol#180: the bulk check reads each currency's minor unit -----------------

def _bulk_balance_report(table, currencies, minor_units=None):
    period_lookup = {(2099, 1): {"code": "P01", "type": "Regular", "status": "Open"}}
    entities = sorted({row[0] for row in table[1:]})
    mod, _ = _load_tb_bulk(entities=entities, postable={"Regular"}, period_lookup=period_lookup,
                           currencies=currencies, minor_units=minor_units)
    _, report = mod._check(table, PERIOD)
    return {r["entity"]: r for r in report}


def test_bulk_check_judges_each_entity_in_its_own_minor_unit():
    """tb_bulk._check reads each currency's minor unit. JPY has 0: a fraction
    of a yen is refused by its line (konsol#180-5; this was "0.4 yen off is
    accepted" under #180-1, which rounded totals), 1 yen off is refused as an
    imbalance. KWD has 3: 0.005 + 0.005 against 0.010 is exact. EUR loads."""
    table = [HEADER_ROW,
             ["ZZH", "2099", "1", "1010", "1000.4", "0", "JPY"],
             ["ZZH", "2099", "1", "2010", "0", "1000", "JPY"],
             ["ZZJ", "2099", "1", "1010", "1000", "0", "JPY"],
             ["ZZJ", "2099", "1", "2010", "0", "1000", "JPY"],
             ["ZZK", "2099", "1", "1010", "1001", "0", "JPY"],
             ["ZZK", "2099", "1", "2010", "0", "1000", "JPY"],
             ["ZZW", "2099", "1", "1010", "0.005", "0", "KWD"],
             ["ZZW", "2099", "1", "2010", "0.005", "0", "KWD"],
             ["ZZW", "2099", "1", "3010", "0", "0.010", "KWD"],
             ["ZZE", "2099", "1", "1010", "100", "0", "EUR"],
             ["ZZE", "2099", "1", "2010", "0", "100", "EUR"]]
    by = _bulk_balance_report(table, {"ZZH": "JPY", "ZZJ": "JPY", "ZZK": "JPY", "ZZW": "KWD", "ZZE": "EUR"})
    assert not by["ZZH"]["ok"], by["ZZH"]
    assert "Line 2: debit 1000.4 has 1 decimal place; JPY has 0." in by["ZZH"]["errors"], by["ZZH"]
    assert by["ZZJ"]["ok"] and by["ZZJ"]["errors"] == [], by["ZZJ"]
    assert by["ZZW"]["ok"] and by["ZZW"]["errors"] == [], by["ZZW"]
    assert by["ZZE"]["ok"], by["ZZE"]
    assert not by["ZZK"]["ok"], by["ZZK"]
    assert any("debits exceed credits by 1 JPY" in e for e in by["ZZK"]["errors"]), by["ZZK"]


def test_bulk_check_refuses_an_entity_whose_currency_has_no_minor_unit():
    table = [HEADER_ROW,
             ["ZZB", "2099", "1", "1010", "100", "0", "XTS"],
             ["ZZB", "2099", "1", "2010", "0", "100", "XTS"],
             ["ZZE", "2099", "1", "1010", "100", "0", "EUR"],
             ["ZZE", "2099", "1", "2010", "0", "100", "EUR"]]
    for units in ({**MINOR_UNITS, "XTS": None}, dict(MINOR_UNITS)):   # blank, and no row at all
        by = _bulk_balance_report(table, {"ZZB": "XTS", "ZZE": "EUR"}, units)
        assert by["ZZE"]["ok"], by["ZZE"]
        assert not by["ZZB"]["ok"], by["ZZB"]
        assert any("ISO Currency XTS has no Minor Unit" in e for e in by["ZZB"]["errors"]), by["ZZB"]


def test_bulk_check_takes_huge_amounts_without_raising():
    """konsol#180 review: 1e26 or more must be judged, not crash the check."""
    table = [HEADER_ROW,
             ["ZZE", "2099", "1", "1010", "1e26", "0", "EUR"],
             ["ZZE", "2099", "1", "2010", "0", "1e26", "EUR"],
             ["ZZF", "2099", "1", "1010", "2e26", "0", "EUR"],
             ["ZZF", "2099", "1", "2010", "0", "1e26", "EUR"]]
    by = _bulk_balance_report(table, {"ZZE": "EUR", "ZZF": "EUR"})
    assert by["ZZE"]["ok"], by["ZZE"]
    assert not by["ZZF"]["ok"] and any("debits exceed credits by" in e for e in by["ZZF"]["errors"]), by["ZZF"]
