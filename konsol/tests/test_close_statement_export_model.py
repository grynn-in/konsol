"""Statement export model, pure: konsol/close/statement_export_model.py
(konsol#305 story 8.5; decision #305-W5-3, 6 Oct: a downloaded .xlsx with
the legend, the Provisional/Signed label, the commentary and a drill sheet;
an Excel add-in function was rejected).

Every expectation is read from the REAL golden payloads the statement
endpoints produce (``close_statement_payload.json`` from ``get_statement``,
``close_drill_payload.json`` from ``get_drill``), never a hand-built dict.
The workbook is opened back with openpyxl and its cells are compared with
those payloads.

The label texts, the section columns and the "not loaded" wording are
duplicated from close-ui's numbers.js (two languages, one screen and one
file). The drift tests below read numbers.js's source and fail when the two
disagree.
"""
import ast
import copy
import importlib.util
import io
import json
import os
import re

import pytest
from openpyxl import load_workbook

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(APP_DIR)
FIXTURE = os.path.join(APP_DIR, "tests", "fixtures", "close_statement_payload.json")
FIXTURE_DRILL = os.path.join(APP_DIR, "tests", "fixtures", "close_drill_payload.json")
NUMBERS_JS = os.path.join(REPO_DIR, "close-ui", "src", "numbers.js")

_PATH = os.path.join(APP_DIR, "close", "statement_export_model.py")
_spec = importlib.util.spec_from_file_location("statement_export_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _payload():
    with open(FIXTURE) as fh:
        return json.load(fh)


def _drills():
    """The golden drill (heading "1"), keyed by heading as the API passes it,
    plus an empty drill for every other statement heading (the model needs
    one per heading; their rows are not under test here — the API test
    drives the real drill_model for all four)."""
    with open(FIXTURE_DRILL) as fh:
        drill = json.load(fh)["drill"]
    out = {drill["heading"]: drill}
    for code, name, section in (("4", "COST OF SALES", "Profit and Loss"),
                                ("2", "LIABILITIES", "Balance Sheet"),
                                ("3", "EQUITY", "Balance Sheet")):
        out[code] = {"heading": code, "heading_name": name, "section": section,
                     "rows": [], "total": 0.0, "dimensions_note": drill["dimensions_note"]}
    return out


def _book(payload=None, drills=None):
    data = M.workbook(payload or _payload(), _drills() if drills is None else drills)
    assert isinstance(data, bytes)
    return load_workbook(io.BytesIO(data))


def _rows(sheet):
    return [list(r) for r in sheet.iter_rows(values_only=True)]


def _row_starting(rows, first, second=None):
    for row in rows:
        if row[0] == first and (second is None or row[1] == second):
            return row
    raise AssertionError("no row starting %r %r in %r" % (first, second, rows))


def _section_block(rows, section):
    """The rows from ``section``'s title row to the next blank row."""
    start = next(i for i, r in enumerate(rows) if r[0] == section)
    out = []
    for row in rows[start + 1:]:
        if all(v is None for v in row):
            break
        out.append(row)
    return out


# --- sheet 1: the statement ---------------------------------------------------

def test_sheets_are_statement_drill_and_journals():
    book = _book()
    assert book.sheetnames == ["Statement", "Drill", "Journals"]


def test_header_label_and_legend_come_from_the_payload():
    payload = _payload()
    rows = _rows(_book()["Statement"])
    assert rows[0][0] == "Numbers · FY2025 P07 · G1 · USD"
    assert rows[1][0] == "Provisional"
    assert rows[2][0] == payload["statement"]["legend"]


def test_entity_basis_header_names_basis_entity_and_currency():
    payload = _payload()
    payload.update({"basis": "Entity", "entity": "ZZA", "entity_name": "Zz Entity",
                    "consolidation_group": None, "reporting_currency": "EUR"})
    rows = _rows(_book(payload)["Statement"])
    assert rows[0][0] == "Numbers · FY2025 P07 · Basis: Entity · ZZA Zz Entity · EUR"
    assert rows[1][0] == "Provisional"
    assert rows[2][0] == payload["statement"]["legend"]


@pytest.mark.parametrize("state,text", [
    ("signed", "Signed"),
    ("provisional", "Provisional"),
    ("resign_needed", "Re-sign needed — numbers changed after signing"),
])
def test_label_follows_the_signoff_state(state, text):
    payload = _payload()
    payload["signoff"] = {"state": state, "run": "AR-1"}
    assert _rows(_book(payload)["Statement"])[1][0] == text


def test_an_unknown_signoff_state_raises():
    payload = _payload()
    payload["signoff"] = {"state": "maybe", "run": None}
    with pytest.raises(ValueError, match="maybe"):
        M.workbook(payload, _drills())


def test_notes_carry_the_comparison_note_and_not_included_text():
    rows = _rows(_book()["Statement"])
    firsts = [r[0] for r in rows]
    assert "No rows in the warehouse for FY2025 P06" in firsts
    assert "1 entity in scope is not in these numbers: ZZB" in firsts


def test_the_declared_accounts_gap_is_written_when_present():
    payload = _payload()
    payload["gap"] = {"message": "Declare the CTA account in Close Settings."}
    firsts = [r[0] for r in _rows(_book(payload)["Statement"])]
    assert "Declare the CTA account in Close Settings." in firsts


def test_each_section_has_the_screens_columns():
    rows = _rows(_book()["Statement"])
    pl = _section_block(rows, "Profit and Loss")
    bs = _section_block(rows, "Balance Sheet")
    assert pl[0][:8] == ["Line", "Code", "This period", "Comparison", "Variance",
                         "Year to date", "Commentary", "By"]
    assert bs[0][:7] == ["Line", "Code", "This period", "Comparison", "Variance",
                         "Commentary", "By"]


def test_every_heading_amount_equals_the_payload_line():
    payload = _payload()
    rows = _rows(_book()["Statement"])
    for section in payload["statement"]["sections"]:
        block = _section_block(rows, section["section"])
        for line in section["lines"]:
            if line["kind"] != "heading":
                continue
            row = _row_starting(block, line["heading_name"], line["heading"])
            assert row[2] == line["current"], line["heading"]
            # comparison_note is set and comparison is None: "not loaded",
            # never 0 (numbers.js comparisonCell).
            assert row[3] == "not loaded"
            assert row[4] == "not loaded"
            if section["section"] == "Profit and Loss":
                assert row[5] == line["ytd"]


def test_amounts_are_numbers_with_a_bracket_format():
    sheet = _book()["Statement"]
    for row in sheet.iter_rows():
        if row[0].value == "ASSETS":
            assert isinstance(row[2].value, float)
            assert row[2].number_format == M.amount_format(2)
            assert "(" in M.amount_format(2)
            return
    raise AssertionError("no ASSETS row")


def test_includes_are_indented_rows_after_their_heading():
    rows = _rows(_book()["Statement"])
    bs = _section_block(rows, "Balance Sheet")
    labels = [r[0] for r in bs]
    i = labels.index("EQUITY")
    assert labels[i + 1] == "    of which currency translation (CTA)"
    assert bs[i + 1][2] == 5.07
    assert labels[i + 2] == "    of which current-year result"
    assert bs[i + 2][2] == 241.43


def test_net_result_and_residual_rows_are_written():
    rows = _rows(_book()["Statement"])
    pl = _section_block(rows, "Profit and Loss")
    net = _row_starting(pl, "Net result")
    assert net[2] == 241.43 and net[5] == 241.43
    bs = _section_block(rows, "Balance Sheet")
    residual = _row_starting(bs, "Unmatched residual")
    assert residual[2] == 0  # -0.0 written as 0
    assert residual[3] is None  # the residual line carries only "current"


def test_a_non_zero_residual_writes_its_explained_and_unexplained_rows():
    payload = _payload()
    residual = payload["statement"]["sections"][1]["lines"][-1]
    residual.update({"current": 12.5, "explained": [
        {"label": "CTA not placed (declare the CTA account)", "amount": 10.0}],
        "unexplained": 2.5})
    bs = _section_block(_rows(_book(payload)["Statement"]), "Balance Sheet")
    assert _row_starting(bs, "    CTA not placed (declare the CTA account)")[2] == 10.0
    assert _row_starting(bs, "    Unexplained")[2] == 2.5


def test_heading_commentary_text_and_byline_are_on_the_heading_row():
    payload = _payload()
    entry = payload["commentary"]["4"]
    pl = _section_block(_rows(_book()["Statement"]), "Profit and Loss")
    row = _row_starting(pl, "COST OF SALES", "4")
    assert row[6] == entry["text"]
    assert row[7] == "Zz Analyst · 2025-08-01 10:00 +01:00"
    bs = _section_block(_rows(_book()["Statement"]), "Balance Sheet")
    assert _row_starting(bs, "ASSETS", "1")[5] is None


def test_a_cleared_commentary_is_not_written():
    payload = _payload()
    payload["commentary"]["4"]["text"] = ""
    pl = _section_block(_rows(_book(payload)["Statement"]), "Profit and Loss")
    row = _row_starting(pl, "COST OF SALES", "4")
    assert row[6] is None and row[7] is None


def test_a_null_amount_without_a_comparison_note_raises_never_zero():
    payload = _payload()
    payload["statement"]["periods"]["comparison_note"] = None
    with pytest.raises(ValueError, match="never written as 0"):
        M.workbook(payload, _drills())


def test_a_non_ok_payload_is_refused_with_its_own_message():
    payload = _payload()
    payload.update({"state": "setup_gap", "message": "statement_heading_side_undeclared: 2",
                    "statement": None})
    with pytest.raises(ValueError, match="statement_heading_side_undeclared: 2"):
        M.workbook(payload, {})


def test_an_unknown_line_kind_raises():
    payload = _payload()
    payload["statement"]["sections"][0]["lines"].append({"kind": "mystery", "current": 1.0})
    with pytest.raises(ValueError, match="mystery"):
        M.workbook(payload, _drills())


# --- sheet 2: the drill -------------------------------------------------------

def test_drill_sheet_has_one_line_per_account_and_sums_to_the_total():
    drill = _drills()["1"]
    rows = _rows(_book()["Drill"])
    header = rows[1]
    assert header == ["Section", "Heading", "Heading name", "Layer", "Entity",
                      "Account", "Account name", "Amount"]
    assert drill["dimensions_note"] in rows[0][0]
    body = [r for r in rows[2:] if r[1] == "1" and r[3] != "Total"]
    expected = []
    for d in drill["rows"]:
        layer = d["label"] or "Entity"
        if d["accounts"]:
            for a in d["accounts"]:
                expected.append([drill["section"], "1", "ASSETS", layer, d["entity"],
                                 a["main_account"], a["account_name"], a["amount"]])
        else:
            expected.append([drill["section"], "1", "ASSETS", layer, d["entity"],
                             None, None, d["amount"]])
    assert body == expected
    total = _row_starting([r[3:] for r in rows if r[1] == "1"], "Total")
    assert total[4] == drill["total"]
    assert round(sum(r[7] for r in body), 2) == drill["total"]


def test_drill_sheet_follows_statement_heading_order():
    rows = _rows(_book()["Drill"])
    order = []
    for r in rows[2:]:
        if r[1] and r[1] not in order:
            order.append(r[1])
    assert order == ["4", "1", "2", "3"]


def test_a_statement_heading_with_no_drill_raises():
    drills = _drills()
    del drills["3"]
    with pytest.raises(ValueError, match="3 has no drill"):
        M.workbook(_payload(), drills)


def test_journals_sheet_lists_the_topside_journals():
    drill = _drills()["1"]
    topside = next(r for r in drill["rows"] if r["label"] == "Top-side journals")
    journal = topside["journals"][0]
    rows = _rows(_book()["Journals"])
    assert rows[1] == ["Heading", "Heading name", "Journal", "Description", "Amount",
                       "Posted by", "Approved by", "Basis"]
    assert rows[2] == ["1", "ASSETS", journal["journal_id"], journal["description"],
                       journal["amount"], journal["posted_by"], journal["approved_by"],
                       topside["journals_basis"]]


# --- drift: the texts shared with close-ui's numbers.js -----------------------

def _js():
    with open(NUMBERS_JS) as fh:
        return fh.read()


def test_signoff_labels_match_numbers_js():
    js = _js()
    block = js[js.index("const SIGNOFF_LABEL"):]
    block = block[:block.index("};")]
    found = dict(re.findall(r'(\w+):\s*\{\s*text:\s*"([^"]+)"', block))
    assert found == M.SIGNOFF_LABEL


def test_columns_match_numbers_js():
    js = _js()
    block = js[js.index("const COLUMNS"):]
    block = block[:block.index("};")]
    pl = re.search(r"\[PL\]:\s*(\[[^\]]*\])", block).group(1)
    bs = re.search(r"\[BS\]:\s*(\[[^\]]*\])", block).group(1)
    assert ast.literal_eval(pl) == M.COLUMNS["Profit and Loss"]
    assert ast.literal_eval(bs) == M.COLUMNS["Balance Sheet"]


def test_not_loaded_matches_numbers_js():
    assert '"%s"' % M.NOT_LOADED in _js()


def test_module_imports_no_frappe_and_no_konsol():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith(("frappe", "konsol")) for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("frappe", "konsol"))


def test_the_payload_is_not_mutated():
    payload = _payload()
    before = copy.deepcopy(payload)
    M.workbook(payload, _drills())
    assert payload == before


def test_account_lines_that_round_away_from_their_row_get_a_rounding_line():
    """Measured live 6 Oct (FY2025 P07 ECL_GROUP): each account amount is
    rounded on its own, so an entity row's account lines drifted up to
    0.14 from the row amount and the Drill sheet did not foot to the
    heading total. A rounding line per such row makes the column sum to
    the total, and says so."""
    drills = _drills()
    entity_row = drills["1"]["rows"][0]
    entity_row["accounts"] = [
        {"main_account": "1110", "account_name": "Cash", "amount": 150.01},
        {"main_account": "1120", "account_name": "Receivables", "amount": 150.0},
    ]  # row amount stays 300.0
    rows = _rows(_book(drills=drills)["Drill"])
    body = [r for r in rows[2:] if r[1] == "1" and r[3] != "Total"]
    rounding = [r for r in body if r[6] == "Rounding (accounts to row)"]
    assert len(rounding) == 1
    assert rounding[0][4] == entity_row["entity"]
    assert rounding[0][7] == -0.01
    assert round(sum(r[7] for r in body), 2) == drills["1"]["total"]


# --- review-w5 S1: free text is never written as a formula --------------------

_TRAIL_PATH = os.path.join(APP_DIR, "close", "trail_model.py")
_trail_spec = importlib.util.spec_from_file_location("trail_model_for_export_test", _TRAIL_PATH)
_TRAIL = importlib.util.module_from_spec(_trail_spec)
_trail_spec.loader.exec_module(_TRAIL)


def _poisoned(lead):
    """The golden payload and drills with ``lead`` at the start of every
    free-text column the export writes: commentary text and author,
    heading/line/"of which" labels, the legend, the gap message, the drill's
    layer label, entity, account and account name, and each journal's id,
    description, posted/approved by and basis."""
    payload, drills = _payload(), _drills()
    payload["statement"]["legend"] = lead + "legend"
    payload["gap"] = {"message": lead + "gap"}
    payload["commentary"]["4"]["text"] = lead + 'HYPERLINK("http://evil","Click")'
    payload["commentary"]["4"]["by"] = lead + "author"
    for section in payload["statement"]["sections"]:
        for line in section["lines"]:
            if line.get("heading_name"):
                line["heading_name"] = lead + line["heading_name"]
            if line.get("label"):
                line["label"] = lead + line["label"]
            for include in line.get("includes") or []:
                include["label"] = lead + include["label"]
    drill = drills["1"]
    drill["heading_name"] = lead + drill["heading_name"]
    for row in drill["rows"]:
        if row.get("label"):
            row["label"] = lead + row["label"]
        if row.get("entity"):
            row["entity"] = lead + row["entity"]
        if row.get("journals_basis"):
            row["journals_basis"] = lead + row["journals_basis"]
        for account in row.get("accounts") or []:
            account["main_account"] = lead + account["main_account"]
            account["account_name"] = lead + (account.get("account_name") or "name")
        for journal in row.get("journals") or []:
            for key in ("journal_id", "description", "posted_by", "approved_by"):
                journal[key] = lead + journal[key]
    return payload, drills


@pytest.mark.parametrize("lead", list(_TRAIL._FORMULA_START), ids=repr)
def test_no_free_text_cell_is_written_as_a_formula(lead):
    """review-w5 S1 (must-fix): openpyxl stores a string starting with "="
    as a formula, so a commentary ``=HYPERLINK(...)`` (or a journal
    description, account name or user) became a live formula in the Close
    Lead's export. Every string cell is text, for every leading character
    trail_model's CSV guards (``_FORMULA_START``)."""
    payload, drills = _poisoned(lead)
    data = M.workbook(payload, drills)
    book = load_workbook(io.BytesIO(data))
    poisoned = 0
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                assert cell.data_type != "f", (sheet.title, cell.coordinate, cell.value)
                if isinstance(cell.value, str) and cell.value.endswith(
                        ('HYPERLINK("http://evil","Click")', "Reclass intercompany loan")):
                    poisoned += 1
    # The raw sheet XML carries no formula element at all.
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if name.startswith("xl/worksheets/"):
                assert b"<f>" not in z.read(name) and b"<f " not in z.read(name), name
    assert poisoned == 2  # the commentary and the journal description were written


def test_a_formula_commentary_reads_back_as_the_same_text():
    payload, drills = _poisoned("=")
    rows = _rows(_book(payload, drills)["Statement"])
    texts = [v for r in rows for v in r if isinstance(v, str)]
    assert '=HYPERLINK("http://evil","Click")' in texts
    assert "=legend" in texts and "=gap" in texts
    journals = [v for r in _rows(_book(payload, drills)["Journals"]) for v in r]
    assert "=Reclass intercompany loan" in journals
    assert "=alice@example.com" in journals and "=bob@example.com" in journals


# --- review-w5 S4: the title row carries the fiscal year ----------------------

def test_header_names_the_fiscal_year_even_when_the_period_code_does_not():
    """review-w5 S4: live ``period_code`` is "P07" (measured 6 Oct), so a
    header built from it could not tell FY2024 P07 from FY2025 P07. Built
    from fiscal_year/fiscal_period, the same as the API's filename."""
    payload = _payload()
    payload["period"]["code"] = "P07"
    assert _rows(_book(payload)["Statement"])[0][0] == "Numbers · FY2025 P07 · G1 · USD"
    payload["period"]["fiscal_year"] = 2024
    assert _rows(_book(payload)["Statement"])[0][0] == "Numbers · FY2024 P07 · G1 · USD"


# --- PR #386 review: the export is in the reporting currency's minor unit -----
#
# The .xlsx quantized every drill line to cents and formatted every amount
# "#,##0.00", whatever the statement's ``minor_unit`` (konsol#354): a BHD
# drill (3 places) did not foot, and a JPY statement showed ".00".


def _in_places(minor_unit):
    payload, drills = _payload(), _drills()
    payload["statement"]["minor_unit"] = minor_unit
    for d in drills.values():
        d["minor_unit"] = minor_unit
    return payload, drills


@pytest.mark.parametrize("minor_unit,fmt", [
    (3, "#,##0.000;(#,##0.000);0.000"),
    (0, "#,##0;(#,##0);0"),
    (2, "#,##0.00;(#,##0.00);0.00"),
])
def test_amount_format_follows_the_minor_unit(minor_unit, fmt):
    payload, drills = _in_places(minor_unit)
    book = _book(payload, drills)
    formats = {c.number_format for ws in book for row in ws.iter_rows() for c in row
               if isinstance(c.value, float)}
    assert formats == {fmt}, formats
    assert M.amount_format(minor_unit) == fmt


def test_a_three_place_drill_foots_to_its_total():
    payload, drills = _in_places(3)
    entity_row = drills["1"]["rows"][0]
    entity_row["accounts"] = [
        {"main_account": "1110", "account_name": "Cash", "amount": 150.001},
        {"main_account": "1120", "account_name": "Receivables", "amount": 150.0},
    ]  # row amount stays 300.0
    rows = _rows(_book(payload, drills)["Drill"])
    body = [r for r in rows[2:] if r[1] == "1" and r[3] != "Total"]
    rounding = [r for r in body if r[6] == "Rounding (accounts to row)"]
    assert len(rounding) == 1 and rounding[0][7] == -0.001, rounding
    assert round(sum(r[7] for r in body), 3) == drills["1"]["total"]


def test_a_zero_place_drill_foots_to_its_total():
    payload, drills = _in_places(0)
    entity_row = drills["1"]["rows"][0]
    entity_row["accounts"] = [
        {"main_account": "1110", "account_name": "Cash", "amount": 151.0},
        {"main_account": "1120", "account_name": "Receivables", "amount": 150.0},
    ]  # row amount stays 300.0
    rows = _rows(_book(payload, drills)["Drill"])
    body = [r for r in rows[2:] if r[1] == "1" and r[3] != "Total"]
    rounding = [r for r in body if r[6] == "Rounding (accounts to row)"]
    assert len(rounding) == 1 and rounding[0][7] == -1.0, rounding
    assert sum(r[7] for r in body) == drills["1"]["total"]


@pytest.mark.parametrize("minor_unit", [None, "2", -1, True])
def test_a_statement_with_no_minor_unit_is_refused_never_read_as_cents(minor_unit):
    payload, drills = _in_places(minor_unit)
    if minor_unit is None:
        del payload["statement"]["minor_unit"]
    with pytest.raises(ValueError, match="minor unit"):
        M.workbook(payload, drills)


# --- the budget columns, note and Budget drill sheet (konsol#348 S5 K11) ------

def _load_by_path(name, *parts):
    spec = importlib.util.spec_from_file_location(name, os.path.join(APP_DIR, *parts))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_F = _load_by_path("close_statement_model_fixtures_k11", "tests", "test_close_statement_model.py")
_SM = _F.M
_REPORT = _load_by_path("statement_report_model_k11", "close", "statement_report_model.py")
_DM = _load_by_path("drill_model_k11", "close", "drill_model.py")

_BUDGET_SENTENCE = ("Approved budget for ZZ_GROUP FY2025: Budget Cycle BCYC-ZZB-2025-abc12345, all "
                    "layers (base, board), Rate Set ZZRS (Approved Budget APB-CG-ZZ_GROUP--2025-0f0f0f0f, "
                    "approved by lead@example.com on 2026-11-17).")
_BUDGET_LABELS = ["Budget (before eliminations)", "Variance to budget", "Budget year to date"]


def _budget(state="ok", message=_BUDGET_SENTENCE, rows=None):
    """FCB shape: revenue budget 1,000, cash budget 300, plug +700."""
    rows = rows if rows is not None else [
        _F._row("4100", -1000.0, adjustment_type="entity"),
        _F._row("1110", 300.0, adjustment_type="entity"),
        _F._row("UNBALANCED", 700.0, adjustment_type="unbalanced"),
    ]
    stmt = (_SM.statement(rows, _F._s_accounts(), _F._S_CALENDAR, _F._S_KEY, _F._s_declared(),
                          minor_unit=2) if state == "ok" else None)
    return {"state": state, "message": message, "statement": stmt,
            "meta": {"cycle": "BCYC-ZZB-2025-abc12345", "layers": ["base", "board"],
                     "rate_set": "ZZRS", "scenario_id": "ZZB",
                     "declaration": "APB-CG-ZZ_GROUP--2025-0f0f0f0f"}}


def _bline(budget, section, **match):
    lines = budget["statement"]["sections"][section]["lines"]
    found = [ln for ln in lines if all(ln.get(k) == v for k, v in match.items())]
    assert len(found) == 1, match
    return found[0]


def _budget_book(budget, budget_drills=None, payload=None):
    data = M.workbook(payload or _payload(), _drills(), budget, budget_drills)
    return load_workbook(io.BytesIO(data))


def _budget_drills(budget):
    """The golden drill with rates, shaped by drill_model.with_rates."""
    with open(FIXTURE_DRILL) as fh:
        drill = json.load(fh)["drill"]
    rate_rows = [
        {"data_area_id": e, "main_account": "1110", "fx_method": "closing",
         "translation_rate": 1.1, "rate_set": "ZZRS"} for e in ("ZZA", "ZZB", "ZZC")]
    rate_rows.append({"data_area_id": "ZZA", "main_account": "1110", "fx_method": "average",
                      "translation_rate": 1.05, "rate_set": "ZZRS2"})
    _DM.with_rates(drill, rate_rows, "BCYC-ZZB-2025-abc12345")
    return {"1": drill}


def test_budget_columns_are_their_own_constant_and_the_actuals_columns_are_untouched():
    assert M.BUDGET_COLUMNS == {"Profit and Loss": _BUDGET_LABELS, "Balance Sheet": _BUDGET_LABELS}
    assert M.COLUMNS["Profit and Loss"] == ["This period", "Comparison", "Variance", "Year to date"]
    assert M.COLUMNS["Balance Sheet"] == ["This period", "Comparison", "Variance"]
    assert M.BUDGET_COLUMNS["Profit and Loss"][0] == _REPORT.BUDGET_COLUMN_LABEL


def test_the_note_texts_equal_the_report_models():
    assert M.BUDGET_LIMITS == _REPORT.BUDGET_LIMITS
    assert M.BUDGET_BS_BASIS == _REPORT.BUDGET_BS_BASIS


def test_header_carries_the_three_budget_columns_after_the_actuals_columns():
    rows = _rows(_budget_book(_budget())["Statement"])
    pl = _section_block(rows, "Profit and Loss")
    bs = _section_block(rows, "Balance Sheet")
    assert pl[0][:11] == ["Line", "Code", "This period", "Comparison", "Variance", "Year to date",
                          *_BUDGET_LABELS, "Commentary", "By"]
    assert bs[0][:10] == ["Line", "Code", "This period", "Comparison", "Variance",
                          *_BUDGET_LABELS, "Commentary", "By"]


def test_a_pl_line_has_budget_variance_to_budget_and_budget_ytd():
    payload, budget = _payload(), _budget()
    pl = _section_block(_rows(_budget_book(budget, payload=payload)["Statement"]), "Profit and Loss")
    row = _row_starting(pl, "COST OF SALES", "4")
    actual = next(ln for ln in payload["statement"]["sections"][0]["lines"] if ln.get("heading") == "4")
    bline = _bline(budget, 0, heading="4")
    assert row[6] == bline["current"] == 1000.0
    assert row[7] == round(actual["current"] - bline["current"], 2)
    assert row[8] == bline["ytd"]
    net = _row_starting(pl, "Net result")
    assert net[6] == _bline(budget, 0, kind="net_result")["current"]
    assert net[7] == round(241.43 - net[6], 2)


def test_a_bs_line_has_the_budget_and_no_variance_and_no_budget_ytd():
    """S5 F1: a budgeted movement against an actual balance is not a variance."""
    budget = _budget()
    bs = _section_block(_rows(_budget_book(budget)["Statement"]), "Balance Sheet")
    row = _row_starting(bs, "ASSETS", "1")
    assert row[5] == _bline(budget, 1, heading="1")["current"] == 300.0
    assert row[6] is None and row[7] is None


def test_the_residual_takes_the_budget_but_no_variance():
    budget = _budget()
    bs = _section_block(_rows(_budget_book(budget)["Statement"]), "Balance Sheet")
    residual = _row_starting(bs, "Unmatched residual")
    assert residual[5] == _bline(budget, 1, kind="residual")["current"]
    assert residual[6] is None


def test_the_budgets_unbalanced_line_precedes_the_residual_with_blank_actuals():
    budget = _budget()
    bs = _section_block(_rows(_budget_book(budget)["Statement"]), "Balance Sheet")
    label = _bline(budget, 1, kind="unbalanced")["label"]
    index = next(i for i, r in enumerate(bs) if r[0] == label)
    assert bs[index + 1][0] == "Unmatched residual"
    row = bs[index]
    assert row[2] is None and row[3] is None and row[4] is None
    assert row[5] == 700.0 and row[6] is None and row[7] is None


def test_a_line_absent_from_the_budget_is_blank_never_zero():
    budget = _budget()
    lines = budget["statement"]["sections"][0]["lines"]
    lines[:] = [ln for ln in lines if ln.get("heading") != "4"]
    pl = _section_block(_rows(_budget_book(budget)["Statement"]), "Profit and Loss")
    row = _row_starting(pl, "COST OF SALES", "4")
    assert row[6] is None and row[7] is None and row[8] is None


def test_without_a_budget_there_are_no_budget_columns_and_no_note():
    rows = _rows(_book()["Statement"])
    assert not any(isinstance(c, str) and "udget" in c for r in rows for c in r)


def test_without_a_budget_the_two_argument_call_has_no_budget_drill_sheet():
    assert _book().sheetnames == ["Statement", "Drill", "Journals"]


def test_an_ok_budget_note_names_the_cycle_the_limits_and_the_bs_basis():
    rows = _rows(_budget_book(_budget())["Statement"])
    notes = [r[0] for r in rows if r[0] and r[0].startswith("Approved budget")]
    assert len(notes) == 1
    assert "BCYC-ZZB-2025-abc12345" in notes[0]
    assert "before intercompany eliminations" in notes[0]
    assert "no variance to budget on the balance sheet" in notes[0]


def test_a_non_ok_budget_renders_the_actuals_layout_and_its_sentence_as_the_note():
    """BUD-4-2 (Deepak Pai, 7 Nov 2026): a visible "no approved budget"
    state, never a blank column (PR #554 review D1). A non-ok budget adds
    the state's sentence as a note and nothing else: the sections keep the
    actuals' columns, Commentary and By in the same cells as without a
    budget, and no budget label appears anywhere."""
    budget = _budget(state="no_approved_budget", message="No approved budget for FY2025.")
    book = _budget_book(budget)
    rows = _rows(book["Statement"])
    assert "Budget columns not shown: No approved budget for FY2025." in [r[0] for r in rows]
    plain = _rows(_book()["Statement"])
    for section in ("Profit and Loss", "Balance Sheet"):
        assert _section_block(rows, section) == _section_block(plain, section), section
    pl = _section_block(rows, "Profit and Loss")
    assert pl[0][:8] == ["Line", "Code", "This period", "Comparison", "Variance", "Year to date",
                         "Commentary", "By"]
    assert _row_starting(pl, "COST OF SALES", "4")[6] == _payload()["commentary"]["4"]["text"]
    assert not any(isinstance(c, str) and c in _BUDGET_LABELS for r in rows for c in r)
    assert book.sheetnames == ["Statement", "Drill", "Journals"]


def test_an_ok_budget_adds_the_three_columns_and_shifts_commentary_three_cells_right():
    """The other half of D1: the columns appear only when the state is ok."""
    rows = _rows(_budget_book(_budget())["Statement"])
    pl = _section_block(rows, "Profit and Loss")
    assert pl[0][6:11] == _BUDGET_LABELS + ["Commentary", "By"]
    assert _row_starting(pl, "COST OF SALES", "4")[9] == _payload()["commentary"]["4"]["text"]


def test_a_budget_drill_sheet_exists_only_when_budget_drills_are_given():
    budget = _budget()
    book = _budget_book(budget, _budget_drills(budget))
    assert book.sheetnames == ["Statement", "Drill", "Journals", "Budget drill"]
    assert _budget_book(budget).sheetnames == ["Statement", "Drill", "Journals"]


def test_budget_drill_sheet_has_rate_and_rate_set_columns_per_entity_account():
    budget = _budget()
    rows = _rows(_budget_book(budget, _budget_drills(budget))["Budget drill"])
    header = next(r for r in rows if r[0] == "Section")
    # PR #554 review m6: the DECLARED method (the chart's, at build time), not the applied one.
    assert header[:8] == M.DRILL_HEADER and header[8:10] == ["Declared method and rate", "Rate Set"]
    note = rows[0][0]
    assert "declared" in note.lower() and "applied" not in note.lower(), note
    zza = next(r for r in rows if r[4] == "ZZA" and r[5] == "1110")
    assert zza[8] == "average 1.05; closing 1.1" and zza[9] == "ZZRS2; ZZRS"  # in the order of the rates
    zzb = next(r for r in rows if r[4] == "ZZB" and r[5] == "1110")
    assert zzb[8] == "closing 1.1" and zzb[9] == "ZZRS"
    cta = next(r for r in rows if r[3] == "Currency translation (CTA)")
    assert cta[8] is None and cta[9] is None


def _budget_with_lines_the_actuals_lack():
    """PR #554 review M1 (test_statement_report_model's fixture): a P&L leaf
    with no heading (-50) and a code outside the chart (120) that the golden
    actuals do not carry; the plug closes the entity's local imbalance."""
    accounts = _F._s_accounts()
    accounts["4900"] = {"account_name": "Other income", "parent_account": None, "is_group": False,
                        "statement_section": _SM.PL, "lft": 15}
    rows = [_F._row("4100", -1000.0, adjustment_type="entity"),
            _F._row("4900", -50.0, adjustment_type="entity"),
            _F._row("9999", 120.0, adjustment_type="entity"),
            _F._row("1110", 300.0, adjustment_type="entity"),
            _F._row("UNBALANCED", 630.0, adjustment_type="unbalanced")]
    stmt = _SM.statement(rows, accounts, _F._S_CALENDAR, _F._S_KEY, _F._s_declared(), minor_unit=2)
    return {"state": "ok", "message": _BUDGET_SENTENCE, "statement": stmt,
            "meta": _budget()["meta"]}


def test_budget_only_no_heading_and_not_in_chart_lines_are_written_with_blank_actuals():
    """PR #554 review M1: every non-heading budget line the actuals lack is
    written where the actuals' line would stand, actuals blank, so the
    Budget column foots on the sheet (Net result = headings + No heading)."""
    rows = _rows(_budget_book(_budget_with_lines_the_actuals_lack())["Statement"])
    pl = _section_block(rows, "Profit and Loss")
    labels = [r[0] for r in pl]
    assert labels.index("COST OF SALES") < labels.index("No heading") < labels.index("Net result")
    row = _row_starting(pl, "No heading")
    assert row[1] is None and row[2:6] == [None] * 4
    assert row[6] == 50.0 and row[7] is None and row[8] == 50.0
    net = _row_starting(pl, "Net result")
    assert net[6] == 1050.0 == _row_starting(pl, "COST OF SALES", "4")[6] + row[6]
    bs = _section_block(rows, "Balance Sheet")
    labels = [r[0] for r in bs]
    plug = "Unbalanced budget (no balancing entry)"
    assert labels.index("EQUITY") < labels.index("Not in the group chart (9999)") < labels.index(plug) \
        < labels.index("Unmatched residual")
    row = _row_starting(bs, "Not in the group chart (9999)")
    assert row[1] is None and row[2:5] == [None] * 3
    assert row[5] == 120.0 and row[6] is None and row[7] is None
    assert _row_starting(bs, plug)[5] == 630.0
    # The golden actuals carry no such lines: without the budget, none is written.
    plain = [r[0] for r in _rows(_book()["Statement"])]
    assert "No heading" not in plain and not any(isinstance(v, str) and v.startswith("Not in the group chart")
                                                 for v in plain)
