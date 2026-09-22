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

HEADER = ["data_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit"]


def _raises(fn, *args):
    try:
        fn(*args)
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError")


def test_splits_into_entity_periods_in_file_order():
    table = [HEADER,
             ["AMDE", "2025", "12", "1010", "100", ""],
             ["AMUS", "2025", "12", "1010", "", "5"],
             ["AMDE", "2025", "12", "2010", "", "100"],
             ["AMDE", "2024", "12", "1010", "1", "0"]]
    groups = M.split_table(table)
    assert list(groups) == [("AMDE", 2025, 12), ("AMUS", 2025, 12), ("AMDE", 2024, 12)]
    assert [r["main_account"] for r in groups[("AMDE", 2025, 12)]] == ["1010", "2010"]
    assert groups[("AMUS", 2025, 12)][0] == {"main_account": "1010", "debit": 0.0, "credit": 5.0, "description": "",
                                             "partner_data_area_id": "", "amount_basis": ""}


def test_header_is_forgiving_about_case_spaces_and_aliases():
    table = [["Entity", "Year", "Period", "Account", "Debit", "Credit", "Description"],
             ["AMDE", "2025", "1", "1010", "10.005", "0", "cash"]]
    rows = M.split_table(table)[("AMDE", 2025, 1)]
    assert rows[0]["debit"] == 10.01 or rows[0]["debit"] == 10.0   # rounded to cents like a single upload
    assert rows[0]["description"] == "cash"


def test_excel_cells_numbers_and_blank_lines():
    table = [HEADER, [None] * 6,
             ["AMDE", 2025.0, 12.0, 1010.0, 1234.5, None],
             ["AMDE", 2025, 12, 2010, None, 1234.5, None, None]]   # trailing empty cells are fine
    rows = M.split_table(table)[("AMDE", 2025, 12)]
    assert [r["main_account"] for r in rows] == ["1010", "2010"]
    assert rows[0]["debit"] == 1234.5 and rows[1]["credit"] == 1234.5


def test_structural_problems_are_reported_together_with_line_numbers():
    table = [HEADER,
             ["", "2025", "12", "1010", "1", "0"],
             ["AMDE", "twenty", "12", "1010", "1", "0"],
             ["AMDE", "2025", "12", "1010", "abc", "0"],
             ["AMDE", "2025", "12", "1010", "nan", "0"],
             ["AMDE", "2025", "12", "1010", "1", "0", "extra"]]
    msg = _raises(M.split_table, table)
    for expected in ("Line 2: data_area_id is blank", "Line 3: fiscal_year", "Line 4: debit must be a number",
                     "Line 5: debit must be a finite", "Line 6: more cells"):
        assert expected in msg, (expected, msg)


def test_missing_columns_and_empty_files():
    assert "Missing column(s) credit" in _raises(M.split_table, [HEADER[:-1], ["AMDE", "2025", "1", "1", "1"]])
    assert "empty" in _raises(M.split_table, [])
    assert "no data rows" in _raises(M.split_table, [HEADER])


def test_group_csv_is_the_single_upload_contract():
    rows = [{"main_account": "1010", "debit": 1234.5, "credit": 0.0, "description": "cash, main"}]
    parsed = list(csv.DictReader(io.StringIO(M.group_csv(rows))))
    assert parsed == [{"main_account": "1010", "debit": "1234.50", "credit": "0.00", "description": "cash, main",
                       "partner_data_area_id": ""}]


def _check(**over):
    facts = dict(known_accounts={"1010", "2010"}, visible=True, leaf=True,
                 period={"code": "P12", "type": "Regular", "status": "Open"}, postable_types={"Regular"},
                 existing=None, validate_rows=lambda rows, **kw: [])
    facts.update(over)
    rows = [{"main_account": "1010", "debit": 5.0, "credit": 0.0}, {"main_account": "2010", "debit": 0.0, "credit": 5.0}]
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
                      postable_types={"Regular"}, existing=None, validate_rows=lambda rows, **kw: [])
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
    table = [["\ufeffdata_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit"],
             ["AMDE", "2025", "12", "1010", "1", "0"]]
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
    assert {k: parsed[0][k] for k in ("main_account", "debit", "credit")} == {"main_account": "1010", "debit": "1.00", "credit": "0.00"}
    # two uploads of the same figures produce different files
    assert M.group_csv(rows, source="TBU-00001") != M.group_csv(rows, source="TBU-00002")


# --- konsol#159: the intercompany partner -----------------------------------

def test_the_partner_column_and_its_aliases_are_carried_to_each_row():
    for name in ("partner_data_area_id", "Partner", "partner entity", "counterparty"):
        table = [HEADER + [name],
                 ["ZZA", "2099", "1", "4030", "", "100", "ZZB"],
                 ["ZZA", "2099", "1", "1010", "100", "", ""]]
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

HEADER_ROW = ["data_area_id", "fiscal_year", "fiscal_period", "main_account", "debit", "credit"]


def _load_tb_bulk(*, entities, postable, period_lookup):
    """Load konsol/tb_bulk.py with every non-model import stubbed.

    `period_lookup` maps (year, period) -> a period fact dict; a pair absent
    from it is undeclared, so the stand-in period_status.period_row raises
    PeriodNotDeclared for it, exactly as the real one does. `postable` is
    what postable_types() returns. Returns (module, calls), where calls
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
        return list(entities)

    def get_all(doctype, filters=None, fields=None, pluck=None, limit_page_length=None):
        if doctype == "Entity":
            return list(entities)
        if doctype == "Trial Balance Submission":
            return []
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
             ["AMDE", "2025", "14", "1010", "5", "0"],
             ["AMDE", "2025", "14", "2010", "0", "5"],
             ["AMDE", "2025", "13", "1010", "5", "0"],
             ["AMDE", "2025", "13", "2010", "0", "5"],
             ["AMDE", "2025", "3", "1010", "5", "0"],
             ["AMDE", "2025", "3", "2010", "0", "5"]]
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
                 ["ZZA", "2099", "1", "1010", "100", "", "period-end balance"],
                 ["ZZA", "2099", "1", "2010", "", "100", " PERIOD-END BALANCE "]]
        rows = M.split_table(table)[("ZZA", 2099, 1)]
        assert [r["amount_basis"] for r in rows] == [CLOSING, CLOSING], name
    assert "Two amount_basis columns" in _raises(M.split_table, [HEADER + ["basis", "amount_basis"]])


def test_without_the_column_rows_carry_no_basis():
    rows = M.split_table([HEADER, ["ZZA", "2099", "1", "1010", "1", "0"]])[("ZZA", 2099, 1)]
    assert rows[0]["amount_basis"] == ""
    assert M.group_basis(rows) == ""


def test_each_group_carries_its_own_basis_and_a_blank_cell_is_not_given():
    table = [HEADER + ["amount_basis"],
             ["ZZA", "2099", "1", "1010", "1", "0", YTD],
             ["ZZA", "2099", "1", "2010", "0", "1", ""],          # blank: not given, so no conflict
             ["ZZB", "2099", "1", "1010", "1", "0", PERIOD],
             ["ZZC", "2099", "1", "1010", "1", "0", ""]]
    groups = M.split_table(table)
    assert M.group_basis(groups[("ZZA", 2099, 1)]) == YTD
    assert M.group_basis(groups[("ZZB", 2099, 1)]) == PERIOD
    assert M.group_basis(groups[("ZZC", 2099, 1)]) == ""


def test_mixed_bases_in_one_entity_period_are_a_line_error_naming_both():
    table = [HEADER + ["amount_basis"],
             ["ZZA", "2099", "1", "1010", "1", "0", PERIOD],
             ["ZZA", "2099", "1", "2010", "0", "1", CLOSING],
             ["ZZB", "2099", "1", "1010", "1", "0", CLOSING]]     # another entity may differ
    msg = _raises(M.split_table, table)
    assert "Line 3" in msg and PERIOD in msg and CLOSING in msg
    assert "Line 4" not in msg


def test_an_unknown_basis_value_is_a_line_error():
    table = [HEADER + ["amount_basis"], ["ZZA", "2099", "1", "1010", "1", "0", "balances"]]
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
             ["AMDE", "2025", "3", "1010", "5", "0", CLOSING],
             ["AMDE", "2025", "3", "2010", "0", "5", ""],
             ["AMUS", "2025", "3", "1010", "5", "0", ""],
             ["AMUS", "2025", "3", "2010", "0", "5", ""]]
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
             ["AMDE", "2025", "12", "1010", "100", "0", "CC100"]]
    msg = _raises(M.split_table, table)
    assert "dim_cost_center" in msg, msg


def test_the_refusal_names_every_unrecognised_header_at_once():
    """A file is fixed in one pass, like the line errors above."""
    table = [HEADER + ["cost centre", "Region", "notes"],
             ["AMDE", "2025", "12", "1010", "100", "0", "CC1", "EMEA", "x"]]
    msg = _raises(M.split_table, table)
    for expected in ("cost_centre", "region", "notes"):
        assert expected in msg, (expected, msg)


def test_the_refusal_says_what_is_accepted():
    table = [HEADER + ["nonsense"],
             ["AMDE", "2025", "12", "1010", "100", "0", "x"]]
    msg = _raises(M.split_table, table)
    assert "main_account" in msg and "debit" in msg, msg


def test_every_documented_header_still_loads():
    """The full accepted set, including the aliases, stays accepted."""
    table = [["Entity", "Year", "Period", "Account", "Debit", "Credit",
              "Description", "Counterparty", "Amount Basis"],
             ["AMDE", "2025", "12", "1010", "100", "0", "cash", "AMUS",
              "Period movement"]]
    rows = M.split_table(table)[("AMDE", 2025, 12)]
    assert rows[0]["partner_data_area_id"] == "AMUS"
    assert rows[0]["description"] == "cash"


def test_a_blank_trailing_header_is_not_an_unknown_column():
    """Excel writes a trailing comma; an empty header name is not a column."""
    table = [HEADER + [""],
             ["AMDE", "2025", "12", "1010", "100", "0", ""]]
    rows = M.split_table(table)[("AMDE", 2025, 12)]
    assert rows[0]["main_account"] == "1010"
