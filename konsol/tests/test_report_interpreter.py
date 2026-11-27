"""The FORMULAS-mode template interpreter (konsol#548 v1, T5). Pure: no site.

Label rule (Deepak Pai, 27 Nov 2026, Q1): ``{month_abbrev}`` shows the
calendar-month abbreviation of each Regular period's start only when every
Regular period of the year starts in a different calendar month; otherwise
each column shows the period's declared label. A year without Regular periods
is refused, never filled with Jan–Dec.
"""
import datetime
import os

from konsol import report_compiler
from konsol import report_template_model as model

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES = model.load_templates(os.path.join(APP_DIR, "report_templates"))
PNL = TEMPLATES["pnl_monthly"]

ACCOUNTS = [("ZZ4000", "ZZ Sales"), ("ZZ5000", "ZZ Costs")]
SIGN = "debit positive; a negative total is a profit"


def _period(number, code, label, start):
    return {"number": number, "code": code, "label": label, "start_date": start}


JAN_DEC = [_period(m, f"P{m:02d}", f"P{m:02d}", f"2024-{m:02d}-01") for m in range(1, 13)]
APR_MAR = [
    _period(i + 1, f"P{i + 1:02d}", f"P{i + 1:02d}",
            f"{2024 + (3 + i) // 12}-{(3 + i) % 12 + 1:02d}-01")
    for i in range(12)
]
# Thirteen four-week periods from 1 Jan 2024: P13 starts 3 Dec, in P12's month.
THIRTEEN = [
    _period(i + 1, f"P{i + 1:02d}", f"Week {4 * i + 1}",
            (datetime.date(2024, 1, 1) + datetime.timedelta(days=28 * i)).isoformat())
    for i in range(13)
]


def _letters(n, start="B"):
    out = []
    for i in range(n):
        k = ord(start) - ord("A") + i + 1
        s = ""
        while k:
            k, r = divmod(k - 1, 26)
            s = chr(65 + r) + s
        out.append(s)
    return out


def _by_range(spec):
    return {c["range"]: c for c in spec["cells"]}


def _compile(periods, accounts=ACCOUNTS, year=2024, **params):
    return model.compile_formulas(
        PNL,
        dict({"entity": "ZZE", "year": year}, **params),
        {"periods": periods, "accounts": {"lines": list(accounts)}},
    )


def _refusal(fn):
    try:
        fn()
    except ValueError as exc:
        return str(exc)
    return None


# -- formula builder -------------------------------------------------------

def test_builder_four_arguments_when_the_rest_are_defaults():
    assert model.epm_formula("ZZE", 2024, 3, "4000") == '=K.EPM("ZZE", 2024, 3, "4000")'
    assert model.epm_formula("ZZE", 2024, 3, "4000", scenario="actuals",
                             scenario_id="actuals") == '=K.EPM("ZZE", 2024, 3, "4000")'


def test_builder_doubles_quotes_like_today():
    assert model.epm_formula('ZZ "Q" Ltd', "2024", "1", '40"00') == (
        '=K.EPM("ZZ ""Q"" Ltd", 2024, 1, "40""00")'
    )


def test_builder_keeps_empty_middle_arguments_and_drops_trailing_ones():
    assert model.epm_formula("E1", 2026, 3, "4000", dimensions="a=1;b=2") == (
        '=K.EPM("E1", 2026, 3, "4000", , , "a=1;b=2")'
    )
    assert model.epm_formula("E1", 2026, 3, "4000", scenario="budget", scenario_id="BUD",
                             layer="board") == (
        '=K.EPM("E1", 2026, 3, "4000", , "budget", , "BUD", , , "board")'
    )
    assert model.epm_formula("E1", 2026, "Q1", "4000", measure="period_amount") == (
        '=K.EPM("E1", 2026, "Q1", "4000", "period_amount")'
    )


# -- compile_formulas on pnl_monthly ----------------------------------------

def test_jan_dec_compiles_to_todays_cells():
    spec = _compile(JAN_DEC)
    by_range = _by_range(spec)
    assert by_range["B2:M2"]["values"] == [[
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]]
    assert by_range["A3"]["values"] == [["ZZ Sales"]]
    assert by_range["B3:M3"]["formulas"] == [[
        f'=K.EPM("ZZE", 2024, {p}, "ZZ4000")' for p in range(1, 13)]]
    assert by_range["A5"]["values"] == [[f"Net Profit and Loss ({SIGN})"]]
    assert by_range["B5:M5"]["formulas"] == [[f"=SUM({c}3:{c}4)" for c in _letters(12)]]
    assert [spec[k] for k in ("template_id", "mode", "sheet_name", "entity", "year", "scenario_id")] == [
        "pnl_monthly", "formulas", "P&L", "ZZE", 2024, "actuals"]


def test_apr_mar_year_labels_apr_to_mar():
    spec = _compile(APR_MAR)
    assert _by_range(spec)["B2:M2"]["values"] == [[
        "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"]]
    assert _by_range(spec)["B3:M3"]["formulas"][0][0] == '=K.EPM("ZZE", 2024, 1, "ZZ4000")'


def test_thirteen_periods_give_thirteen_columns_with_declared_labels():
    spec = _compile(THIRTEEN, accounts=ACCOUNTS[:1])
    by_range = _by_range(spec)
    assert by_range["B2:N2"]["values"] == [[p["label"] for p in THIRTEEN]]
    assert by_range["B3:N3"]["formulas"] == [[
        f'=K.EPM("ZZE", 2024, {n}, "ZZ4000")' for n in range(1, 14)]]
    assert by_range["B4:N4"]["formulas"] == [[f"=SUM({c}3:{c}3)" for c in _letters(13)]]
    assert spec["report"]["area"] == "A1:N4"


def test_two_periods_starting_in_one_month_use_declared_labels():
    # A 4-4-5 year from 1 Jan 2024: P01 starts 1 Jan and P02 starts 29 Jan,
    # so two Regular periods start in January.
    starts = ["2024-01-01", "2024-01-29", "2024-02-26", "2024-04-01", "2024-04-29",
              "2024-05-27", "2024-07-01", "2024-07-29", "2024-08-26", "2024-09-30",
              "2024-10-28", "2024-11-25"]
    periods = [_period(i + 1, f"P{i + 1:02d}", f"M{i + 1}", s) for i, s in enumerate(starts)]
    periods[4]["label"] = "P05"  # a period without a declared label: the code
    labels = _by_range(_compile(periods))["B2:M2"]["values"][0]
    assert labels == ["M1", "M2", "M3", "M4", "P05", "M6", "M7", "M8", "M9", "M10", "M11", "M12"]


def test_a_period_without_a_start_date_uses_declared_labels():
    periods = [dict(p) for p in JAN_DEC]
    periods[0]["start_date"] = None
    assert _by_range(_compile(periods))["B2:M2"]["values"][0] == [p["code"] for p in JAN_DEC]


def test_columns_follow_period_number_order():
    shuffled = list(reversed(JAN_DEC))
    assert _by_range(_compile(shuffled))["B2:M2"]["values"][0][:2] == ["Jan", "Feb"]


def test_no_regular_periods_is_refused_never_jan_dec():
    for periods in (None, []):
        msg = _refusal(lambda: _compile(periods))
        assert msg is not None and msg.startswith("No Regular periods are declared for FY2024"), msg
        msg = _refusal(lambda: report_compiler.build_cell_map(
            "pnl_monthly", "ZZE", 2024, accounts=ACCOUNTS, periods=periods))
        assert msg is not None and msg.startswith("No Regular periods are declared for FY2024"), msg


def test_a_non_regular_period_is_refused():
    periods = [_period(0, "OPN", "Opening", "2024-01-01")] + JAN_DEC
    periods[0]["period_type"] = "Opening"
    msg = _refusal(lambda: _compile(periods))
    assert msg is not None and "OPN" in msg and "Regular" in msg, msg


def test_no_rows_uses_the_template_text():
    for accounts in ([],):
        assert _refusal(lambda: _compile(JAN_DEC, accounts=accounts)) == PNL["errors"]["no_rows"]
    msg = _refusal(lambda: model.compile_formulas(
        PNL, {"entity": "ZZE", "year": 2024}, {"periods": JAN_DEC}))
    assert msg == PNL["errors"]["no_rows"]


def test_several_charts_uses_the_template_text_with_sorted_charts():
    msg = _refusal(lambda: model.compile_formulas(
        PNL, {"entity": "ZZE", "year": 2024},
        {"periods": JAN_DEC, "accounts": {"lines": ACCOUNTS},
         "charts": {"lines": ["Zeta", "Alpha"]}}))
    assert msg == (
        "The monthly P&L reads one chart, and Published Profit and Loss accounts exist "
        "in charts Alpha, Zeta. Make one chart Inactive or Draft."
    )


def test_missing_required_and_unknown_params_are_refused():
    assert "entity" in _refusal(lambda: model.compile_formulas(
        PNL, {"year": 2024}, {"periods": JAN_DEC, "accounts": {"lines": ACCOUNTS}}))
    assert "colour" in _refusal(lambda: model.compile_formulas(
        PNL, {"entity": "ZZE", "year": 2024, "colour": "red"},
        {"periods": JAN_DEC, "accounts": {"lines": ACCOUNTS}}))


def test_report_key_names_template_version_params_and_area():
    spec = _compile(JAN_DEC, scenario_id="BUD")
    assert spec["report"] == {
        "template_id": "pnl_monthly",
        "template_version": 1,
        "params": {"entity": "ZZE", "year": 2024, "scenario_id": "BUD"},
        "area": "A1:M5",
    }
    assert spec["scenario_id"] == "BUD"
    assert "formats" not in spec


def test_facade_report_key_matches_its_arguments():
    spec = report_compiler.build_cell_map("pnl_monthly", "ZZE", "2026", "BUD",
                                          accounts=ACCOUNTS[:1], periods=JAN_DEC)
    assert spec["year"] == 2026
    assert spec["report"]["params"] == {"entity": "ZZE", "year": 2026, "scenario_id": "BUD"}
    assert spec["report"]["area"] == "A1:M4"


def test_facade_refuses_a_snapshot_template_for_formulas():
    msg = _refusal(lambda: report_compiler.build_cell_map(
        "trial_balance_long", "ZZE", 2024, periods=JAN_DEC))
    assert msg == "Unknown template_id for formulas: 'trial_balance_long'"


# -- T10: row blocks and scope kinds (pure fixtures) -------------------------

TWO = JAN_DEC[:2]  # columns B:C
ENTITY_YEAR = [{"name": "entity", "type": "entity", "required": True},
               {"name": "year", "type": "fiscal_year", "required": True}]
ALL_KINDS = {"entity": "actuals", "group": "consolidated", "node": "actuals"}


def _tpl(blocks, params=None, read=None, header=None, validate=True):
    t = {
        "id": "zz_rows", "version": 1, "order": 1, "title": "ZZ Rows", "mode": "formulas",
        "sheet_name": "ZZ", "params": params or ENTITY_YEAR,
        "series": {"act": {"caption": "Actual", "read": read or {"entity": "actuals"}}},
        "columns": {"start_col": "B", "header_rows": [2],
                    "groups": [{"over": "regular_periods", "label": "{month_abbrev}", "series": ["act"]}]},
        "rows": {"start_row": 3, "blocks": blocks},
    }
    if header:
        t["header"] = header
    if validate:
        assert model.validate(t, "zz_rows") == []
    return t


def _run(t, params=None, **ctx):
    return model.compile_formulas(t, params or {"entity": "ZZE", "year": 2024},
                                  dict({"periods": TWO}, **ctx))


def _epm(*args):
    return model.epm_formula(*args)


TREE = [
    {"code": "ZZ4", "caption": "ZZ Revenue", "depth": 0, "is_group": True, "parent": None},
    {"code": "ZZ41", "caption": "ZZ Product", "depth": 1, "is_group": True, "parent": "ZZ4"},
    {"code": "ZZ4101", "caption": "ZZ Widgets", "depth": 2, "is_group": False, "parent": "ZZ41"},
    {"code": "ZZ4102", "caption": "ZZ Gadgets", "depth": 2, "is_group": False, "parent": "ZZ41"},
    {"code": "ZZ42", "caption": "ZZ Services", "depth": 1, "is_group": False, "parent": "ZZ4"},
]
TREE_BLOCKS = [
    {"id": "rev", "type": "account_tree", "caption": "{account.name}"},
    {"type": "total", "of": ["rev"], "caption": "Total"},
]


def test_account_tree_headings_sum_their_direct_children():
    by = _by_range(_run(_tpl(TREE_BLOCKS), trees={"rev": TREE}))
    # Captions as given: the schema has no indent option.
    assert [by[f"A{r}"]["values"][0][0] for r in range(3, 9)] == [
        "ZZ Revenue", "ZZ Product", "ZZ Widgets", "ZZ Gadgets", "ZZ Services", "Total"]
    # Row 3 (ZZ4): children rows 4 (ZZ41) and 7 (ZZ42), not contiguous.
    assert by["B3:C3"]["formulas"] == [["=SUM(B4,B7)", "=SUM(C4,C7)"]]
    # Row 4 (ZZ41): children rows 5 and 6, contiguous.
    assert by["B4:C4"]["formulas"] == [["=SUM(B5:B6)", "=SUM(C5:C6)"]]
    assert by["B5:C5"]["formulas"] == [[_epm("ZZE", 2024, 1, "ZZ4101"), _epm("ZZE", 2024, 2, "ZZ4101")]]
    assert by["B7:C7"]["formulas"] == [[_epm("ZZE", 2024, 1, "ZZ42"), _epm("ZZE", 2024, 2, "ZZ42")]]


def test_total_over_a_tree_sums_leaf_rows_never_headings():
    by = _by_range(_run(_tpl(TREE_BLOCKS), trees={"rev": TREE}))
    assert by["B8:C8"]["formulas"] == [["=SUM(B5:B7)", "=SUM(C5:C7)"]]


def test_account_tree_heading_without_children_is_refused():
    tree = TREE[:1] + [dict(TREE[4], is_group=True)]
    msg = _refusal(lambda: _run(_tpl(TREE_BLOCKS), trees={"rev": tree}))
    assert msg is not None and "ZZ42" in msg and "no child rows" in msg, msg


def test_account_tree_child_before_its_parent_is_refused():
    tree = [TREE[2], TREE[1]]
    msg = _refusal(lambda: _run(_tpl(TREE_BLOCKS), trees={"rev": tree}))
    assert msg is not None and "ZZ4101" in msg and "ZZ41" in msg, msg


def test_account_tree_empty_is_no_rows():
    msg = _refusal(lambda: _run(_tpl(TREE_BLOCKS), trees={"rev": []}))
    assert msg == "ZZ Rows: the report has no rows."


def test_account_list_reads_in_the_chosen_order():
    params = ENTITY_YEAR + [{"name": "picks", "type": "account_list"}]
    t = _tpl([{"id": "l", "type": "account_list", "param": "picks", "caption": "{account.code} {account.name}"},
              {"type": "total", "of": ["l"], "caption": "Total"}], params=params)
    by = _by_range(_run(t, account_lists={"l": [("ZZ5", "ZZ Costs"), ("ZZ4", "ZZ Sales")]}))
    assert by["A3"]["values"] == [["ZZ5 ZZ Costs"]]
    assert by["A4"]["values"] == [["ZZ4 ZZ Sales"]]
    assert by["B4:C4"]["formulas"] == [[_epm("ZZE", 2024, 1, "ZZ4"), _epm("ZZE", 2024, 2, "ZZ4")]]
    assert by["B5:C5"]["formulas"] == [["=SUM(B3:B4)", "=SUM(C3:C4)"]]


NODE_PARAMS = ENTITY_YEAR + [{"name": "hier", "type": "hierarchy"},
                             {"name": "acct", "type": "account"}]
NODES = [
    {"code": "ZZG", "label": "ZZ Group", "depth": 0, "is_group": True},
    {"code": "ZZA", "label": "ZZ A", "depth": 1, "is_group": False},
    {"code": "ZZB", "label": "ZZ B", "depth": 1, "is_group": False},
]
NODE_BLOCKS = [{"id": "n", "type": "hierarchy_nodes", "hierarchy": "hier", "account": "acct",
                "caption": "{node.label}"},
               {"type": "total", "of": ["n"], "caption": "Total"}]


def _nodes(read):
    return _run(_tpl(NODE_BLOCKS, params=NODE_PARAMS, read=read),
                {"entity": "ZZE", "year": 2024, "hier": "ZZH", "acct": "ZZ4000"},
                nodes={"n": NODES})


def test_hierarchy_group_node_is_read_not_summed():
    by = _by_range(_nodes({"entity": "actuals", "node": "actuals"}))
    assert by["A3"]["values"] == [["ZZ Group"]]
    assert by["B3:C3"]["formulas"] == [[
        '=K.EPM("ALL", 2024, 1, "ZZ4000", , , , , "ZZH", "ZZG")',
        '=K.EPM("ALL", 2024, 2, "ZZ4000", , , , , "ZZH", "ZZG")']]
    assert by["B4:C4"]["formulas"][0][0] == '=K.EPM("ALL", 2024, 1, "ZZ4000", , , , , "ZZH", "ZZA")'
    # The total sums the leaf rows only, never the group node (it holds them).
    assert by["B6:C6"]["formulas"] == [["=SUM(B4:B5)", "=SUM(C4:C5)"]]


def test_hierarchy_nodes_without_a_node_read_word_is_refused():
    msg = _refusal(lambda: _nodes({"entity": "actuals"}))
    assert msg is not None and "'act'" in msg and "'node'" in msg, msg


def test_heading_and_blank_rows():
    t = _tpl([{"type": "heading", "caption": "ZZ Income {title}"},
              {"type": "blank"},
              {"id": "l", "type": "accounts", "filter": {}, "caption": "{account.name}"}])
    spec = _run(t, accounts={"l": [("ZZ4", "ZZ Sales")]})
    by = _by_range(spec)
    assert by["A3"]["values"] == [["ZZ Income ZZ Rows"]]
    assert not [c for c in spec["cells"] if c["range"].endswith("4") and c["range"][0] in "ABC"]
    assert by["A5"]["values"] == [["ZZ Sales"]]


SCOPE_PARAMS = [{"name": "scope", "type": "scope", "kinds": ["entity", "group", "node"], "required": True},
                {"name": "year", "type": "fiscal_year", "required": True}]
SCOPE_BLOCKS = [{"id": "l", "type": "accounts", "filter": {}, "caption": "{account.name}"}]
SCOPE_HEADER = [{"at": "A1", "text": "{scope.kind}: {scope.code} {scope.name}"}]


def _scope(value, read=ALL_KINDS, kinds=None, validate=True, **ctx):
    params = [dict(SCOPE_PARAMS[0], kinds=kinds or SCOPE_PARAMS[0]["kinds"]), SCOPE_PARAMS[1]]
    t = _tpl(SCOPE_BLOCKS, params=params, read=read, header=SCOPE_HEADER, validate=validate)
    return _by_range(_run(t, {"scope": value, "year": 2024},
                          **dict({"accounts": {"l": [("ZZ4", "ZZ Sales")]}, "scope_name": "ZZ Name"}, **ctx)))


def test_scope_entity_reads_the_entity_word():
    by = _scope({"kind": "entity", "code": "ZZE"})
    assert by["B3:C3"]["formulas"][0][0] == '=K.EPM("ZZE", 2024, 1, "ZZ4")'
    assert by["A1"]["values"] == [["entity: ZZE ZZ Name"]]


def test_scope_group_reads_the_group_with_its_dataset_word():
    by = _scope({"kind": "group", "code": "ZZG"})
    assert by["B3:C3"]["formulas"][0][0] == '=K.EPM("ZZG", 2024, 1, "ZZ4", , "consolidated")'
    assert by["A1"]["values"] == [["group: ZZG ZZ Name"]]


def test_scope_node_reads_all_entities_in_hierarchy_mode():
    by = _scope({"kind": "node", "code": "ZZN", "hierarchy": "ZZH"})
    assert by["B3:C3"]["formulas"][0][0] == '=K.EPM("ALL", 2024, 1, "ZZ4", , , , , "ZZH", "ZZN")'


def test_scope_kind_missing_its_read_word_is_refused_by_name():
    msg = _refusal(lambda: _scope({"kind": "group", "code": "ZZG"}, read={"entity": "actuals"},
                                  kinds=["entity", "group"], validate=False))
    assert msg is not None and "'act'" in msg and "'group'" in msg and "read word" in msg, msg


def test_scope_kind_the_param_does_not_allow_is_refused():
    msg = _refusal(lambda: _scope({"kind": "node", "code": "ZZN"}, kinds=["entity", "group"]))
    assert msg is not None and "'node'" in msg and "scope" in msg, msg


def test_scope_name_token_without_ctx_is_refused():
    msg = _refusal(lambda: _scope({"kind": "entity", "code": "ZZE"}, scope_name=None))
    assert msg is not None and "scope.name" in msg, msg


def test_plain_group_param_reads_the_group_word():
    params = [{"name": "group", "type": "group", "required": True},
              {"name": "year", "type": "fiscal_year", "required": True}]
    t = _tpl(SCOPE_BLOCKS, params=params, read={"group": "consolidated"})
    by = _by_range(_run(t, {"group": "ZZG", "year": 2024}, accounts={"l": [("ZZ4", "ZZ Sales")]}))
    assert by["B3:C3"]["formulas"][0][0] == '=K.EPM("ZZG", 2024, 1, "ZZ4", , "consolidated")'


# -- T11: columns (series, scenario id, quarters, fy, ytd, formula, sign, formats)

THREE = [dict(p, quarter="Q1") for p in JAN_DEC[:3]]  # columns per series: P01..P03
COL_SERIES = {
    "act": {"caption": "Actual", "read": {"entity": "actuals", "group": "consolidated"}},
    "bud": {"caption": "Budget {budget}", "read": {"entity": "budget", "group": "budget"},
            "scenario_id": "{budget}"},
    "var": {"caption": "Variance (Actual - Budget)", "formula": "act - bud"},
}
COL_GROUPS = [
    {"over": "regular_periods", "label": "{month_abbrev}", "series": ["act", "bud", "var"]},
    {"over": "ytd", "label": "YTD {month_abbrev}", "series": ["act", "bud", "var"], "through": "period"},
    {"over": "fy", "label": "FY {year}", "series": ["act", "bud", "var"]},
]


def _col_params(scope_type="entity"):
    return [{"name": scope_type, "type": scope_type, "required": True},
            {"name": "year", "type": "fiscal_year", "required": True},
            {"name": "period", "type": "period", "depends_on": ["year"]},
            {"name": "budget", "type": "scenario", "scenario_type": "budget",
             "depends_on": ["year"], "default": "rule"}]


def _cols_tpl(groups=None, header_rows=(2, 3), series=None, scope_type="entity", sign=None,
              number_format=None, bold=None, validate=True, extra_params=()):
    t = {
        "id": "zz_cols", "version": 3, "order": 1, "title": "ZZ Cols", "mode": "formulas",
        "sheet_name": "ZZ", "params": _col_params(scope_type) + list(extra_params),
        "series": series or COL_SERIES,
        "columns": {"start_col": "B", "header_rows": list(header_rows),
                    "groups": groups or COL_GROUPS},
        "rows": {"start_row": 4, "blocks": [
            {"id": "l", "type": "accounts", "filter": {}, "caption": "{account.name}"},
            {"id": "net", "type": "total", "of": ["l"], "caption": "Net {sign.sentence}"},
        ]},
    }
    if sign is not None:
        t["sign"] = sign
    if number_format is not None:
        t["number_format"] = number_format
    if bold is not None:
        t["rows"]["blocks"][1]["bold"] = bold
    if validate:
        assert model.validate(t, "zz_cols") == []
    return t


COL_LINES = [("ZZ4", "ZZ Sales"), ("ZZ5", "ZZ Costs")]


def _cols(t, periods=THREE, scope=("entity", "ZZE"), **params):
    p = dict({scope[0]: scope[1], "year": 2024, "period": 2, "budget": "ZZBUD"}, **params)
    return model.compile_formulas(t, p, {"periods": periods, "accounts": {"l": list(COL_LINES)}})


def test_several_series_lay_out_period_major_with_two_header_rows():
    by = _by_range(_cols(_cols_tpl()))
    # Row 2: the group label, repeated on every column of the period/group.
    assert by["B2:P2"]["values"] == [[
        "Jan", "Jan", "Jan", "Feb", "Feb", "Feb", "Mar", "Mar", "Mar",
        "YTD Feb", "YTD Feb", "YTD Feb", "FY 2024", "FY 2024", "FY 2024"]]
    # Row 3: the series caption.
    assert by["B3:P3"]["values"] == [[
        "Actual", "Budget ZZBUD", "Variance (Actual - Budget)"] * 5]


def test_entity_actual_budget_variance_ytd_fy_cells():
    by = _by_range(_cols(_cols_tpl()))
    row = by["B4:P4"]["formulas"][0]
    assert row[0:3] == ['=K.EPM("ZZE", 2024, 1, "ZZ4")',
                        '=K.EPM("ZZE", 2024, 1, "ZZ4", , "budget", , "ZZBUD")',
                        "=B4-C4"]
    assert row[3:6] == ['=K.EPM("ZZE", 2024, 2, "ZZ4")',
                        '=K.EPM("ZZE", 2024, 2, "ZZ4", , "budget", , "ZZBUD")',
                        "=E4-F4"]
    assert row[8] == "=H4-I4"
    # YTD through P02: the same series' period cells P01..P02 of this row.
    assert row[9:12] == ["=SUM(B4,E4)", "=SUM(C4,F4)", "=K4-L4"]
    assert row[12:15] == ['=K.EPM("ZZE", 2024, "FY", "ZZ4")',
                          '=K.EPM("ZZE", 2024, "FY", "ZZ4", , "budget", , "ZZBUD")',
                          "=N4-O4"]
    # The total sums every column, the variance and YTD columns included.
    assert by["B6:P6"]["formulas"] == [[f"=SUM({c}4:{c}5)" for c in _letters(15)]]


def test_group_actual_budget_variance_reads_the_group_words():
    spec = _cols(_cols_tpl(scope_type="group"), scope=("group", "ZZG"))
    row = _by_range(spec)["B5:P5"]["formulas"][0]
    assert row[0] == '=K.EPM("ZZG", 2024, 1, "ZZ5", , "consolidated")'
    assert row[1] == '=K.EPM("ZZG", 2024, 1, "ZZ5", , "budget", , "ZZBUD")'
    assert row[2] == "=B5-C5"
    assert row[9:12] == ["=SUM(B5,E5)", "=SUM(C5,F5)", "=K5-L5"]
    assert row[13] == '=K.EPM("ZZG", 2024, "FY", "ZZ5", , "budget", , "ZZBUD")'


def test_ytd_through_the_first_period_and_by_code():
    row = _by_range(_cols(_cols_tpl(), period="P01"))["B4:P4"]["formulas"][0]
    assert row[9:12] == ["=SUM(B4)", "=SUM(C4)", "=K4-L4"]
    row = _by_range(_cols(_cols_tpl(), period=3))["B4:P4"]["formulas"][0]
    assert row[9] == "=SUM(B4,E4,H4)"


def test_ytd_without_through_or_outside_the_year_is_refused():
    msg = _refusal(lambda: _cols(_cols_tpl(), period=None))
    assert msg is not None and "'period'" in msg and "YTD" in msg, msg
    msg = _refusal(lambda: _cols(_cols_tpl(), period=9))
    assert msg is not None and "9" in msg and "Regular" in msg, msg


def test_ytd_series_only_beside_actual_sums_contiguous_cells():
    groups = [{"over": "regular_periods", "label": "{month_abbrev}", "series": ["act"]},
              {"over": "ytd", "label": "YTD", "series": ["act"], "through": "period"}]
    by = _by_range(_cols(_cols_tpl(groups=groups, header_rows=[2]), period=3))
    assert by["B2:E2"]["values"] == [["Jan", "Feb", "Mar", "YTD"]]
    assert by["B4:E4"]["formulas"][0][3] == "=SUM(B4:D4)"


def test_scenario_id_missing_for_a_scenario_read_is_refused():
    msg = _refusal(lambda: _cols(_cols_tpl(), budget=None))
    assert msg is not None and "'bud'" in msg and "'budget'" in msg, msg


def test_quarters_read_q_tokens_for_quarters_with_regular_periods():
    six = [dict(p, quarter="Q1" if p["number"] <= 3 else "Q2") for p in JAN_DEC[:6]]
    groups = [{"over": "quarters", "label": "{label}", "series": ["act"]}]
    by = _by_range(_cols(_cols_tpl(groups=groups, header_rows=[2]), periods=six))
    assert by["B2:C2"]["values"] == [["Q1", "Q2"]]
    assert by["B4:C4"]["formulas"] == [['=K.EPM("ZZE", 2024, "Q1", "ZZ4")',
                                        '=K.EPM("ZZE", 2024, "Q2", "ZZ4")']]


def test_quarters_need_every_regular_period_to_declare_its_quarter():
    periods = [dict(p) for p in THREE]
    periods[2]["quarter"] = None
    groups = [{"over": "quarters", "label": "{label}", "series": ["act"]}]
    msg = _refusal(lambda: _cols(_cols_tpl(groups=groups, header_rows=[2]), periods=periods))
    assert msg is not None and "P03" in msg and "quarter" in msg, msg


def test_one_header_row_with_several_series_needs_the_series_caption_token():
    groups = [{"over": "regular_periods", "label": "{month_abbrev} {series.caption}",
               "series": ["act", "bud"]}]
    by = _by_range(_cols(_cols_tpl(groups=groups, header_rows=[2])))
    assert by["B2:G2"]["values"] == [[
        "Jan Actual", "Jan Budget ZZBUD", "Feb Actual", "Feb Budget ZZBUD",
        "Mar Actual", "Mar Budget ZZBUD"]]
    groups[0]["label"] = "{month_abbrev}"
    msg = _refusal(lambda: _cols(_cols_tpl(groups=groups, header_rows=[2], validate=False)))
    assert msg is not None and "series.caption" in msg and "one header row" in msg, msg


def test_formula_series_needs_its_siblings_in_the_same_group():
    groups = [{"over": "regular_periods", "label": "{month_abbrev}", "series": ["act", "var"]}]
    msg = _refusal(lambda: _cols(_cols_tpl(groups=groups, validate=False)))
    assert msg is not None and "'var'" in msg and "'bud'" in msg, msg


def test_formula_series_keeps_parentheses_and_numbers():
    series = dict(COL_SERIES, pct={"caption": "%", "formula": "(act - bud) / bud * 100"})
    groups = [{"over": "fy", "label": "FY", "series": ["act", "bud", "pct"]}]
    by = _by_range(_cols(_cols_tpl(groups=groups, series=series)))
    assert by["B4:D4"]["formulas"][0][2] == "=(B4-C4)/C4*100"


def test_credit_positive_wraps_every_read():
    t = _cols_tpl(sign={"convention": "credit_positive", "sentence": "credit positive"})
    spec = _cols(t)
    by = _by_range(spec)
    row = by["B4:P4"]["formulas"][0]
    assert row[0] == '=-K.EPM("ZZE", 2024, 1, "ZZ4")'
    assert row[1] == '=-K.EPM("ZZE", 2024, 1, "ZZ4", , "budget", , "ZZBUD")'
    assert row[2] == "=B4-C4"
    assert row[9] == "=SUM(B4,E4)"
    assert row[12] == '=-K.EPM("ZZE", 2024, "FY", "ZZ4")'
    assert by["A6"]["values"] == [["Net credit positive"]]


def test_sign_param_picks_the_convention_and_sentence():
    choice = {"name": "sign", "type": "choice", "choices": ["debit_positive", "credit_positive"],
              "default": "debit_positive"}
    sign = {"param": "sign", "sentences": {"debit_positive": "debits up", "credit_positive": "credits up"}}
    t = _cols_tpl(sign=sign, extra_params=[choice])
    by = _by_range(_cols(t))
    assert by["B4:P4"]["formulas"][0][0] == '=K.EPM("ZZE", 2024, 1, "ZZ4")'
    assert by["A6"]["values"] == [["Net debits up"]]
    by = _by_range(_cols(t, sign="credit_positive"))
    assert by["B4:P4"]["formulas"][0][0] == '=-K.EPM("ZZE", 2024, 1, "ZZ4")'
    assert by["A6"]["values"] == [["Net credits up"]]


def test_formats_number_format_per_series_columns_and_bold_total():
    nf = {"default": "#,##0", "var": "#,##0;[Red]-#,##0"}
    spec = _cols(_cols_tpl(number_format=nf, bold=True))
    red = "#,##0;[Red]-#,##0"
    expected = []
    for first, var in (("B", "D"), ("E", "G"), ("H", "J"), ("K", "M"), ("N", "P")):
        second = chr(ord(first) + 1)
        expected.append({"range": f"{first}4:{second}6", "number_format": "#,##0"})
        expected.append({"range": f"{var}4:{var}6", "number_format": red})
    expected.append({"range": "A6:P6", "bold": True})
    assert spec["formats"] == expected
    assert spec["report"]["area"] == "A2:P6"
    assert spec["report"]["template_id"] == "zz_cols" and spec["report"]["template_version"] == 3


def test_formats_only_where_declared():
    nf = {"var": "0.0"}
    spec = _cols(_cols_tpl(number_format=nf))
    assert [f["range"] for f in spec["formats"]] == ["D4:D6", "G4:G6", "J4:J6", "M4:M6", "P4:P6"]
    assert "formats" not in _cols(_cols_tpl())
    assert "formats" not in _cols(_cols_tpl(bold=False))


def test_tree_heading_sums_apply_to_every_column():
    t = _cols_tpl(groups=[{"over": "fy", "label": "FY", "series": ["act", "bud", "var"]}])
    t["rows"]["blocks"] = [{"id": "rev", "type": "account_tree", "caption": "{account.name}"}]
    spec = model.compile_formulas(t, {"entity": "ZZE", "year": 2024, "budget": "ZZBUD"},
                                  {"periods": THREE, "trees": {"rev": TREE}})
    by = _by_range(spec)
    assert by["B4:D4"]["formulas"] == [["=SUM(B5,B8)", "=SUM(C5,C8)", "=SUM(D5,D8)"]]
    assert by["B6:D6"]["formulas"][0][2] == "=B6-C6"
