"""The group chart's rules (konsol#182), pure: konsol/group_chart_model.py.

The Main Account form and the chart file both decide here, so
these are the rules for both. Loaded by path; the module imports nothing
from frappe or konsol.
"""
import importlib.util
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("gcm_under_test", os.path.join(APP_DIR, "group_chart_model.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)

PL, BS = "Profit and Loss", "Balance Sheet"


def leaf(**kw):
    row = {"main_account": "ZZ1000", "account_name": "Cash", "chart_of_accounts": "ZZCOA", "is_group": 0,
           "account_type": "Asset", "statement_section": BS, "normal_balance": "Debit",
           "time_balance": "balance", "fx_method": "closing", "is_posting": 1, "allow_ic": 0,
           "status": "Draft"}
    row.update(kw)
    return row


def group(**kw):
    row = {"main_account": "ZZ9000", "account_name": "Heading", "chart_of_accounts": "ZZCOA", "is_group": 1,
           "is_posting": 0, "allow_ic": 0, "status": "Published"}
    row.update(kw)
    return row


def has(problems, needle):
    return any(needle in p for p in problems)


# -- declarations -----------------------------------------------------------------------

def test_a_complete_leaf_has_no_problems():
    assert M.declaration_problems(leaf()) == []
    assert M.publish_problems(leaf()) == []


def test_pnl_historical_refused():
    p = M.declaration_problems(leaf(account_type="Revenue", statement_section=PL, time_balance="flow",
                                    fx_method="historical"))
    assert has(p, "not translated at a historical rate"), p


def test_bs_average_refused():
    p = M.declaration_problems(leaf(fx_method="average"))
    assert has(p, "not translated at the average rate"), p


def test_pnl_closing_allowed():
    """IAS 29: a hyperinflationary entity's P&L is translated at the closing rate."""
    assert M.declaration_problems(leaf(account_type="Revenue", statement_section=PL, time_balance="flow",
                                       fx_method="closing")) == []
    # and a Balance Sheet account may be historical (equity)
    assert M.declaration_problems(leaf(account_type="Equity", fx_method="historical")) == []


def test_type_section_mismatch_refused():
    for kind in ("Asset", "Liability", "Equity", "Balance sheet"):
        p = M.declaration_problems(leaf(account_type=kind, statement_section=PL, time_balance="flow",
                                        fx_method="average"))
        assert has(p, "belongs on the Balance Sheet"), (kind, p)
    for kind in ("Revenue", "Expense", "Profit and loss"):
        p = M.declaration_problems(leaf(account_type=kind))
        assert has(p, "belongs on the Profit and Loss"), (kind, p)
        assert M.declaration_problems(leaf(account_type=kind, statement_section=PL, time_balance="flow",
                                           fx_method="average")) == []


def test_time_balance_mismatch_refused():
    assert has(M.declaration_problems(leaf(time_balance="flow")), "time_balance is balance")
    p = M.declaration_problems(leaf(account_type="Expense", statement_section=PL, time_balance="balance",
                                    fx_method="average"))
    assert has(p, "time_balance is flow"), p


def test_values_outside_the_vocabularies_refused():
    for field, bad in (("account_type", "Assets"), ("statement_section", "P&L"), ("fx_method", "spot"),
                       ("normal_balance", "Dr"), ("time_balance", "Flow"), ("cf_category", "Other")):
        assert has(M.declaration_problems(leaf(**{field: bad})), f"{field} {bad!r} is not one of"), field


def test_group_cannot_post_or_allow_ic():
    assert M.declaration_problems(group()) == []
    assert has(M.declaration_problems(group(is_posting=1)), "clear is_posting")
    assert has(M.declaration_problems(group(allow_ic=1)), "clear allow_ic")
    # a heading needs no translation method, and is never translated
    assert M.publish_problems(group()) == []
    assert M.apply_defaults(group(statement_section=BS)).get("fx_method") is None


def test_parent_must_be_group_in_same_chart():
    child = leaf(parent_account="ZZ9000")
    assert M.declaration_problems(child, group()) == []
    assert has(M.declaration_problems(child, group(is_group=0)), "is not a heading")
    assert has(M.declaration_problems(child, group(chart_of_accounts="OTHER")), "is in chart 'OTHER'")
    assert has(M.declaration_problems(child, None), "parent ZZ9000 does not exist")
    assert has(M.declaration_problems(leaf(parent_account="ZZ1000")), "cannot be its own parent")


def test_child_section_matches_parent():
    child = leaf(parent_account="ZZ9000")
    assert M.declaration_problems(child, group(statement_section=BS)) == []
    assert has(M.declaration_problems(child, group(statement_section=PL)), "its parent ZZ9000 is on the")
    # a parent that declares no section constrains nothing
    assert M.declaration_problems(child, group(statement_section="")) == []


def test_publish_needs_every_declaration():
    for field in M.LEAF_DECLARATIONS:
        p = M.publish_problems(leaf(**{field: ""}))
        assert has(p, f"cannot be published without {field}"), field
    for field in M.GROUP_DECLARATIONS:
        assert has(M.publish_problems(group(**{field: ""})), f"without {field}"), field
    assert M.publish_problems(group(account_type="", statement_section="", fx_method="")) == []
    # a parent must be Published first
    child = leaf(parent_account="ZZ9000")
    assert M.publish_problems(child, group()) == []
    assert has(M.publish_problems(child, group(status="Draft")), "publish its parent ZZ9000 first")
    assert has(M.publish_problems(child, None), "publish its parent")


def test_defaults():
    bare = leaf(normal_balance="", time_balance="", fx_method="")
    assert {k: M.apply_defaults(bare)[k] for k in ("normal_balance", "time_balance", "fx_method")} == {
        "normal_balance": "Debit", "time_balance": "balance", "fx_method": "closing"}
    equity = M.apply_defaults(leaf(account_type="Equity", normal_balance="", fx_method=""))
    assert (equity["normal_balance"], equity["fx_method"]) == ("Credit", "historical")
    revenue = M.apply_defaults(leaf(account_type="Revenue", statement_section=PL, normal_balance="",
                                    time_balance="", fx_method=""))
    assert (revenue["normal_balance"], revenue["time_balance"], revenue["fx_method"]) == ("Credit", "flow", "average")
    for kind in ("Expense", "Profit and loss"):
        assert M.apply_defaults(leaf(account_type=kind, statement_section=PL, normal_balance=""))[
            "normal_balance"] == "Debit"
    # a declared value is never overwritten, and the input is not mutated
    kept = leaf(account_type="Revenue", statement_section=PL, time_balance="flow", fx_method="closing")
    assert M.apply_defaults(kept)["fx_method"] == "closing"
    assert bare["fx_method"] == ""


# -- the chart file ------------------------------------------------------------------------

HEAD = ["main_account", "account_name", "account_type", "statement_section", "sub_section", "normal_balance",
        "time_balance", "fx_method", "cf_category", "cf_line_item", "is_posting", "allow_ic", "parent_account",
        "chart_of_accounts"]


def line(code, name, kind="", section="", fx="", parent="", posting="", allow_ic="", normal="", time=""):
    return [code, name, kind, section, "", normal, time, fx, "", "", posting, allow_ic, parent, "ZZCOA"]


def parse(*rows, head=HEAD):
    return M.parse_chart_table([head, *rows])


def refused(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError as e:
        return str(e)
    raise AssertionError("not refused")


def test_header_aliases():
    rows = M.parse_chart_table([
        ["﻿Code", "Name", "Parent", "COA", "Section", "Translation Method", "Account Type"],
        ["ZZ9000", "Heading", "", "ZZCOA", "BS", "", ""],
        ["ZZ1000", "Cash", "ZZ9000", "ZZCOA", "bs", "close", "asset"],
    ])
    assert rows[1]["main_account"] == "ZZ1000" and rows[1]["account_name"] == "Cash"
    assert (rows[1]["parent_account"], rows[1]["chart_of_accounts"]) == ("ZZ9000", "ZZCOA")
    assert (rows[1]["statement_section"], rows[1]["fx_method"], rows[1]["account_type"]) == (BS, "closing", "Asset")
    assert M.parse_chart_table([["Account", "account name", "chart"], ["ZZ1", "X", "ZZCOA"]])[0]["main_account"] == "ZZ1"
    # unknown or missing columns are refused, named
    assert "unknown column(s) colour" in refused(M.parse_chart_table, [["code", "name", "coa", "colour"]])
    assert "missing column(s) chart_of_accounts" in refused(M.parse_chart_table, [["code", "name"]])


def test_value_aliases():
    cases = {
        "statement_section": {"p&l": PL, "PL": PL, "pnl": PL, "Income Statement": PL, "bs": BS,
                              "balance sheet": BS},
        "fx_method": {"close": "closing", "CR": "closing", "closing rate": "closing", "avg": "average",
                      "ar": "average", "historic": "historical", "hist": "historical", "HR": "historical"},
        "normal_balance": {"dr": "Debit", "D": "Debit", "cr": "Credit", "c": "Credit", "credit": "Credit"},
        "time_balance": {"period": "flow", "movement": "flow", "PIT": "balance", "point in time": "balance"},
        "account_type": {"income": "Revenue", "Sales": "Revenue", "cogs": "Expense",
                         "cost of goods sold": "Expense", "expenses": "Expense", "capital": "Equity",
                         "stockholders equity": "Equity", "Stockholders' Equity": "Equity",
                         "liability": "Liability", "balance SHEET": "Balance sheet"},
    }
    for field, pairs in cases.items():
        for raw, want in pairs.items():
            assert M.canonical(field, raw) == want, (field, raw)
    assert M.canonical("fx_method", "spot") is None
    assert M.canonical("fx_method", "  ") == ""


def test_booleans():
    for raw in ("y", "Yes", "TRUE", "1", "x", 1, 1.0, True):
        assert M.boolean(raw) == 1, raw
    for raw in ("n", "No", "false", "0", 0, False):
        assert M.boolean(raw) == 0, raw
    assert M.boolean("") is None and M.boolean(None) is None
    rows = parse(line("ZZ9000", "Heading"), line("ZZ1000", "Cash", parent="ZZ9000"),
                 line("ZZ2000", "Loan", allow_ic="yes", posting="no"))
    by = {r["main_account"]: r for r in rows}
    # blank is_posting is the default: a heading is not posted to, a leaf is
    assert (by["ZZ9000"]["is_posting"], by["ZZ1000"]["is_posting"], by["ZZ2000"]["is_posting"]) == (0, 1, 0)
    assert (by["ZZ1000"]["allow_ic"], by["ZZ2000"]["allow_ic"]) == (0, 1)
    assert "is_posting 'maybe' is not yes or no" in refused(parse, line("ZZ1", "X", posting="maybe"))


def test_duplicate_codes():
    msg = refused(parse, line("ZZ1000", "Cash"), line("ZZ2000", "Loan"), line("ZZ1000", "Cash again"))
    assert "ZZ1000 appears on lines 2, 4" in msg, msg


def test_one_chart_code_per_file():
    other = line("ZZ2000", "Loan")
    other[-1] = "OTHER"
    assert "One chart per file: this file names OTHER, ZZCOA" in refused(parse, line("ZZ1000", "Cash"), other)


def test_is_group_inferred():
    rows = parse(line("ZZ1000", "Cash", parent="ZZ9000"), line("ZZ9000", "Heading"), line("ZZ2000", "Loan"))
    assert {r["main_account"]: r["is_group"] for r in rows} == {"ZZ1000": 0, "ZZ9000": 1, "ZZ2000": 0}
    head = HEAD + ["is_group"]
    # with the column, a row with accounts under it must say it is a heading
    msg = refused(parse, line("ZZ1000", "Cash", parent="ZZ9000") + [""], line("ZZ9000", "Heading") + ["no"],
                  head=head)
    assert "ZZ9000 has accounts under it, so it must be a heading" in msg, msg
    # a heading with nothing under it in this file is fine; blank is inferred
    rows = parse(line("ZZ8000", "Empty heading") + ["yes"], line("ZZ1000", "Cash") + [""], head=head)
    assert [r["is_group"] for r in rows] == [1, 0]


def test_every_problem_in_one_pass():
    msg = refused(parse,
                  line("ZZ1000", "", fx="spot"),
                  line("ZZ2000", "Loan", posting="perhaps"),
                  line("", "Nameless"),
                  line("ZZ2000", "Loan again"),
                  line("ZZ3000", "Capital", kind="Shares"))
    for needle in ("Line 2: fx_method 'spot'", "Line 2: account_name is blank", "Line 3: is_posting 'perhaps'",
                   "Line 4: main_account is blank", "ZZ2000 appears on lines 3, 5", "Line 6: account_type 'Shares'"):
        assert needle in msg, (needle, msg)
    # plan_chart_load reports every declaration problem too, not the first
    rows = parse(line("ZZ1000", "Cash", kind="Asset", section="P&L"),
                 line("ZZ4000", "Sales", kind="Revenue", section="PL", fx="historical"))
    report = M.plan_chart_load(rows, {})
    assert not report["ok"] and report["writes"] == []
    assert has(report["errors"], "ZZ1000: an account of type Asset belongs on the Balance Sheet")
    assert has(report["errors"], "ZZ4000: a Profit and Loss account is not translated at a historical rate")


def test_parents_before_children():
    rows = parse(line("ZZ1100", "Petty cash", kind="Asset", section="BS", parent="ZZ1000"),
                 line("ZZ1000", "Cash heading", parent="ZZ9000"),
                 line("ZZ9000", "Assets"))
    report = M.plan_chart_load(rows, {})
    assert report["ok"], report["errors"]
    assert [w[1] for w in report["writes"]] == ["ZZ9000", "ZZ1000", "ZZ1100"]
    assert all(w[0] == "insert" for w in report["writes"])
    assert M.topological(rows) == ["ZZ9000", "ZZ1000", "ZZ1100"]
    assert report["insert"] == ["ZZ9000", "ZZ1000", "ZZ1100"]


def test_parent_cycle():
    rows = parse(line("ZZ9000", "A", parent="ZZ9100"), line("ZZ9100", "B", parent="ZZ9000"))
    assert "in a circle" in refused(M.topological, rows)
    report = M.plan_chart_load(rows, {})
    assert not report["ok"] and has(report["errors"], "in a circle"), report["errors"]
    assert sum("circle" in e for e in report["errors"]) == 1
    # a circle through an account konsol already holds
    existing = {"ZZ9200": group(main_account="ZZ9200", parent_account="ZZ9300")}
    report = M.plan_chart_load(parse(line("ZZ9300", "C", parent="ZZ9200") + ["yes"],
                                     head=HEAD + ["is_group"]), existing)
    assert has(report["errors"], "ZZ9300 -> ZZ9200 -> ZZ9300"), report["errors"]


def test_unknown_parent():
    report = M.plan_chart_load(parse(line("ZZ1000", "Cash", parent="ZZ404")), {})
    assert has(report["errors"], "ZZ1000: parent ZZ404 does not exist")
    # a parent konsol already holds is not unknown
    report = M.plan_chart_load(parse(line("ZZ1000", "Cash", kind="Asset", section="BS", parent="ZZ9000")),
                               {"ZZ9000": group()})
    assert report["ok"], report["errors"]


def test_never_deletes_by_omission():
    existing = {"ZZ5000": leaf(main_account="ZZ5000", status="Published"),
                "ZZ6000": leaf(main_account="ZZ6000", status="Draft"),
                "OT1000": leaf(main_account="OT1000", chart_of_accounts="OTHER")}
    report = M.plan_chart_load(parse(line("ZZ1000", "Cash", kind="Asset", section="BS")), existing)
    assert report["ok"]
    assert report["not_in_file"] == ["ZZ5000", "ZZ6000"]   # this chart's only
    assert {w[0] for w in report["writes"]} == {"insert"} and [w[1] for w in report["writes"]] == ["ZZ1000"]


def test_published_changes_listed():
    existing = {"ZZ1000": leaf(status="Published"),
                "ZZ3000": leaf(main_account="ZZ3000", account_name="Capital", account_type="Equity",
                               normal_balance="Credit", fx_method="historical", status="Published"),
                "ZZ6000": leaf(main_account="ZZ6000", account_name="Draft one", status="Draft")}
    rows = parse(line("ZZ1000", "Cash", kind="Asset", section="BS", fx="historical"),
                 line("ZZ3000", "Capital", kind="Equity", section="BS"),
                 line("ZZ6000", "Draft one renamed", kind="Asset", section="BS"))
    report = M.plan_chart_load(rows, existing)
    assert report["ok"], report["errors"]
    assert report["published_changes"] == [{"main_account": "ZZ1000", "fields": {"fx_method": ["closing", "historical"]}}]
    assert report["unchanged"] == ["ZZ3000"]   # blank fx_method still defaults to historical for Equity
    assert report["update"] == ["ZZ6000"]
    writes = {w[1]: w for w in report["writes"]}
    assert writes["ZZ1000"][0] == "update" and "status" not in writes["ZZ1000"][2]   # stays Published
    # a change that would leave a Published account unpublishable is refused
    report = M.plan_chart_load(parse(line("ZZ1000", "Cash", kind="", section="")), existing)
    assert has(report["errors"], "ZZ1000 cannot be published without account_type"), report["errors"]


def test_a_published_account_cannot_move_under_a_new_draft_heading():
    existing = {"ZZ1000": leaf(status="Published")}
    rows = parse(line("ZZ9000", "New heading"), line("ZZ1000", "Cash", kind="Asset", section="BS", parent="ZZ9000"))
    report = M.plan_chart_load(rows, existing)
    assert has(report["errors"], "ZZ1000: publish its parent ZZ9000 first"), report["errors"]


def test_drafts_not_ready_are_listed_not_refused():
    rows = parse(line("ZZ9000", "Heading"), line("ZZ1000", "Cash", parent="ZZ9000"))
    report = M.plan_chart_load(rows, {})
    assert report["ok"]
    assert report["not_ready"] == [{"main_account": "ZZ1000", "problems": [
        "ZZ1000 cannot be published without account_type, statement_section, normal_balance, time_balance, fx_method"]}]


# -- review of #183 ----------------------------------------------------------------------------

def test_moving_an_account_with_children_to_another_chart_is_refused():
    existing = {"ZZ9000": group(), "ZZ1000": leaf(parent_account="ZZ9000", status="Published")}
    moved = line("ZZ9000", "Heading") + ["yes"]
    moved[13] = "OTHER"
    report = M.plan_chart_load(parse(moved, head=HEAD + ["is_group"]), existing)
    assert has(report["errors"], "ZZ1000: parent ZZ9000 is in chart 'OTHER', not 'ZZCOA'"), report["errors"]
    # a childless account may move
    existing = {"ZZ9000": group()}
    assert M.plan_chart_load(parse(moved, head=HEAD + ["is_group"]), existing)["ok"]


def test_a_heading_changing_statement_rechecks_accounts_not_in_the_file():
    existing = {"ZZ9000": group(statement_section=BS), "ZZ1000": leaf(parent_account="ZZ9000", status="Published")}
    row = line("ZZ9000", "Heading", section="P&L") + ["yes"]
    report = M.plan_chart_load(parse(row, head=HEAD + ["is_group"]), existing)
    assert has(report["errors"], "ZZ1000 is on the Balance Sheet but its parent ZZ9000 is on the Profit and Loss")
    # turning the heading into a leaf would strand them too
    row = line("ZZ9000", "Heading", kind="Asset", section="BS") + ["no"]
    report = M.plan_chart_load(parse(row, head=HEAD + ["is_group"]), existing)
    assert has(report["errors"], "ZZ1000: parent ZZ9000 is not a heading"), report["errors"]


def test_a_leaf_turned_heading_without_an_is_posting_column_gets_the_heading_default():
    head = [h for h in HEAD if h != "is_posting"]
    cells = lambda code, name, parent="": [code, name, "", "", "", "", "", "", "", "", "", parent, "ZZCOA"]
    existing = {"ZZ9000": leaf(main_account="ZZ9000", status="Draft")}
    report = M.plan_chart_load(M.parse_chart_table([head, cells("ZZ9000", "Now a heading"),
                                                    cells("ZZ1000", "Cash", parent="ZZ9000")]), existing)
    assert report["ok"], report["errors"]
    writes = {w[1]: w[2] for w in report["writes"]}
    assert (writes["ZZ9000"]["is_group"], writes["ZZ9000"]["is_posting"]) == (1, 0)
    # and back: a heading turned into a leaf is posted to again
    existing = {"ZZ9000": group(status="Draft")}
    report = M.plan_chart_load(M.parse_chart_table([head + ["is_group"], cells("ZZ9000", "Now a leaf") + ["no"]]),
                               existing)
    assert {w[1]: w[2] for w in report["writes"]}["ZZ9000"]["is_posting"] == 1


def test_in_use_problems():
    assert M.in_use_problems("ZZ1000") == []
    p = M.in_use_problems("ZZ1000", postings=[("ZZ1000", "ZZOP", 2026, 2), ("ZZ1000", "ZZOP", 2026, 1),
                                              ("ZZ1000", "ZZOP", 2026, 1)])
    assert len(p) == 1 and "(ZZ1000: ZZOP FY2026 P01, ZZOP FY2026 P02)" in p[0]
    assert "balance sheet would stop balancing" in p[0] and "reclassify the account instead" in p[0]
    p = M.in_use_problems("ZZ9000", postings=[("ZZ1100", "ZZOP", 2026, 3)], intercompany=["ICA-ZZ1000"],
                          difference_groups=["CG-ZZGRP-"], heading=True)
    assert len(p) == 3 and all(x.startswith("ZZ9000 (a heading: the accounts under it)") for x in p)
    many = [("ZZ1000", "ZZOP", 2026, n) for n in range(1, 13)]
    assert "P10, …" in M.in_use_problems("ZZ1000", postings=many)[0]
