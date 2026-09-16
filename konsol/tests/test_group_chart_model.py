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


def test_only_equity_may_be_published_as_historical():
    """konsol#239: konsol does step two of IAS 21 only — it translates a
    functional-currency trial balance into the presentation currency, and the
    historical rate is for equity. Remeasuring books kept in a non-functional
    currency is the entity's own step one, upstream."""
    p = M.publish_problems(leaf(fx_method="historical"))
    assert has(p, "ZZ1000: only an Equity account may be translated at the historical rate (konsol#239)"), p
    assert has(p, "this one is Asset"), p
    # equity is what the historical rate is for
    assert M.publish_problems(leaf(account_type="Equity", normal_balance="Credit",
                                   fx_method="historical")) == []
    # the ordinary case is untouched: an Asset at the closing rate
    assert M.publish_problems(leaf()) == []
    # an untyped leaf is refused for its missing declaration too, but the type is still named
    assert has(M.publish_problems(leaf(account_type="", fx_method="historical")), "this one is untyped")
    # a heading carries no translation method, so the rule does not reach it
    assert M.publish_problems(group(fx_method="historical")) == []


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
    rows = parse(line("ZZ1000", "Cash", kind="Balance sheet", section="BS"),
                 line("ZZ3000", "Capital", kind="Equity", section="BS"),
                 line("ZZ6000", "Draft one renamed", kind="Asset", section="BS"))
    report = M.plan_chart_load(rows, existing)
    assert report["ok"], report["errors"]
    assert report["published_changes"] == [{"main_account": "ZZ1000",
                                            "fields": {"account_type": ["Asset", "Balance sheet"]}}]
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


def test_a_published_account_cannot_be_reloaded_as_historical():
    """konsol#239 reaches the chart file, not just the form: the planner runs
    publish_problems over every row that is (or becomes) Published, so a file
    that declares a non-equity account historical is refused whole. A chart
    re-tagged by hand would otherwise be undone by the next upload."""
    existing = {"ZZ1000": leaf(status="Published")}
    report = M.plan_chart_load(parse(line("ZZ1000", "Cash", kind="Asset", section="BS", fx="historical")), existing)
    assert not report["ok"]
    assert has(report["errors"], "ZZ1000: only an Equity account may be translated at the historical rate "
                                 "(konsol#239)"), report["errors"]
    assert has(report["errors"], "this one is Asset"), report["errors"]
    assert report["writes"] == []
    # the same account at the closing rate loads
    fine = M.plan_chart_load(parse(line("ZZ1000", "Cash", kind="Asset", section="BS", fx="closing")), existing)
    assert fine["ok"], fine["errors"]
    # and equity is what the historical rate is for
    equity = {"ZZ3000": leaf(main_account="ZZ3000", account_name="Capital", account_type="Equity",
                             normal_balance="Credit", status="Published")}
    report = M.plan_chart_load(parse(line("ZZ3000", "Capital", kind="Equity", section="BS", fx="historical",
                                          normal="Credit")), equity)
    assert report["ok"], report["errors"]


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


# -- guardrails: legitimate charts must keep loading (coordinator, 13 Sep 2026) ----------------------

def test_a_child_is_compared_to_its_heading_by_statement_not_by_type():
    """A real chart has an Asset account under an EQUITY heading, and Revenue
    accounts under an OTHER / INTEREST heading: same statement, different type."""
    equity = group(main_account="ZZ3900", account_type="Equity", statement_section=BS)
    other = group(main_account="ZZ7900", account_type="Expense", statement_section=PL)
    assert M.declaration_problems(leaf(parent_account="ZZ3900"), equity) == []
    assert M.declaration_problems(leaf(main_account="ZZ7100", account_type="Revenue", statement_section=PL,
                                       time_balance="flow", fx_method="average", parent_account="ZZ7900"), other) == []
    head = HEAD + ["is_group"]
    rows = parse(line("ZZ3900", "EQUITY", kind="Equity", section="BS") + ["yes"],
                 line("ZZ3100", "Receivable held in equity", kind="Asset", section="BS", parent="ZZ3900") + [""],
                 line("ZZ7900", "OTHER / INTEREST", kind="Expense", section="P&L") + ["yes"],
                 line("ZZ7100", "Interest income", kind="Revenue", section="P&L", parent="ZZ7900") + [""],
                 line("ZZ7200", "Other income", kind="Income", section="P&L", parent="ZZ7900") + [""], head=head)
    report = M.plan_chart_load(rows, {})
    assert report["ok"] and report["errors"] == [] and report["not_ready"] == [], report


def test_every_leaf_allowed_intercompany_loads_with_no_error_or_warning():
    """A real chart has allow_ic=1 on every postable account. Only a heading may not allow it."""
    rows = parse(line("ZZ9000", "Heading"),
                 *[line(f"ZZ1{n}00", f"Asset {n}", kind="Asset", section="BS", parent="ZZ9000", allow_ic="yes")
                   for n in range(5)],
                 line("ZZ4000", "Sales", kind="Revenue", section="P&L", parent="ZZ9000", allow_ic="yes"),
                 line("ZZ2000", "Payable", kind="Liability", section="BS", parent="ZZ9000", allow_ic="1"))
    assert all(r["allow_ic"] == 1 for r in rows if r["main_account"] != "ZZ9000")
    assert next(r for r in rows if r["main_account"] == "ZZ9000")["allow_ic"] == 0   # the default
    report = M.plan_chart_load(rows, {})
    assert report["ok"] and report["errors"] == [] and report["not_ready"] == [], report
    assert set(report) <= {"chart_of_accounts", "rows", "insert", "update", "published_changes", "inactive",
                           "unchanged", "not_ready", "not_in_file", "errors", "ok", "writes"}   # no warnings key
    for r in rows:
        assert M.declaration_problems(M.apply_defaults(r), None if not r.get("parent_account") else group()) == []


# -- the cash-flow mapping (konsol#196): the chart is the source ----------------------------------

def cf_leaf(**kw):
    return leaf(**{"cf_category": "Operating", "cf_line_item": "Cash and equivalents", **kw})


def test_cash_flow_mapping_heading_is_none():
    assert M.cash_flow_mapping(group(cf_category="Operating", cf_line_item="Heading line")) is None
    assert M.cash_flow_mapping(group(is_group="1", statement_section=BS, cf_category="Operating",
                                     cf_line_item="Heading line")) is None


def test_cash_flow_mapping_pnl_leaf_is_none():
    row = cf_leaf(account_type="Revenue", statement_section=PL, time_balance="flow", fx_method="average")
    assert M.cash_flow_mapping(row) is None
    # and a leaf that declares no statement at all
    assert M.cash_flow_mapping(cf_leaf(statement_section="")) is None


def test_cash_flow_mapping_blank_cf_field_is_none():
    assert M.cash_flow_mapping(cf_leaf(cf_line_item="")) is None
    assert M.cash_flow_mapping(cf_leaf(cf_line_item="   ")) is None
    assert M.cash_flow_mapping(cf_leaf(cf_line_item=None)) is None
    assert M.cash_flow_mapping(cf_leaf(cf_category="")) is None
    assert M.cash_flow_mapping(leaf()) is None   # no cf fields at all


def test_cash_flow_mapping_bs_leaf_maps_the_four_fields():
    m = M.cash_flow_mapping(cf_leaf())
    assert m == {"main_account": "ZZ1000", "cf_category": "Operating",
                 "cf_line_item": "Cash and equivalents", "is_cash": 0}
    assert set(m) == {"main_account", "cf_category", "cf_line_item", "is_cash"}


def test_cash_flow_mapping_is_cash_flag():
    assert M.cash_flow_mapping(cf_leaf())["is_cash"] == 0
    for raw in (None, "", "0", 0, False):
        assert M.cash_flow_mapping(cf_leaf(is_cash=raw))["is_cash"] == 0, raw
    for raw in ("1", 1, True):
        assert M.cash_flow_mapping(cf_leaf(is_cash=raw))["is_cash"] == 1, raw


def test_cash_flow_mapping_normalises_text():
    m = M.cash_flow_mapping(cf_leaf(main_account="ZZ1000 ", cf_category=" Operating", cf_line_item="Cash "))
    assert m == {"main_account": "ZZ1000", "cf_category": "Operating", "cf_line_item": "Cash", "is_cash": 0}
    # Excel hands back 1000.0 for a code typed 1000
    assert M.cash_flow_mapping(cf_leaf(main_account=1000.0))["main_account"] == "1000"



# -- the chart declares its retained-earnings account (konsolidat#199) --------------------------

def re_leaf(**kw):
    """A flagged Equity leaf on the Balance Sheet: the shape the flag needs."""
    row = leaf(main_account="ZZ3000", account_name="Retained earnings", account_type="Equity",
               normal_balance="Credit", fx_method="historical", is_retained_earnings=1, status="Published")
    row.update(kw)
    return row


def test_the_retained_earnings_account_is_an_equity_leaf_on_the_balance_sheet():
    assert M.declaration_problems(re_leaf()) == []
    assert "is_retained_earnings" in M.OPTIONAL and "is_retained_earnings" in M.CHECK_FIELDS
    assert "is_retained_earnings" in M.DECLARED_FIELDS
    # a heading is never posted to, so nothing can be closed into it
    problems = M.declaration_problems(group(is_retained_earnings=1))
    assert has(problems, "ZZ9000 is a heading (is_group), so it cannot be the retained earnings account")
    assert len(problems) == 1
    # a Profit and Loss account: one sentence for the statement, one for the type
    problems = M.declaration_problems(re_leaf(account_type="Revenue", statement_section=PL, time_balance="flow",
                                              fx_method="average"))
    assert has(problems, "ZZ3000: the retained earnings account belongs on the Balance Sheet, not the Profit and Loss")
    assert has(problems, "ZZ3000: the retained earnings account is Equity, not Revenue")
    assert len(problems) == 2
    # a Balance Sheet account that is not Equity
    problems = M.declaration_problems(re_leaf(account_type="Asset", normal_balance="Debit", fx_method="closing"))
    assert problems == ["ZZ3000: the retained earnings account is Equity, not Asset"]
    # the flag off: none of this applies
    assert M.declaration_problems(group(is_retained_earnings=0)) == []
    assert M.declaration_problems(leaf(is_retained_earnings="0")) == []


def test_exactly_one_published_retained_earnings_account_per_chart():
    one = re_leaf()
    assert M.retained_earnings_problems([one]) == []
    assert M.retained_earnings_problems([one, leaf(status="Published")]) == []
    two = re_leaf(main_account="ZZ3100")
    problems = M.retained_earnings_problems([one, two, leaf(status="Published")])
    assert len(problems) == 1 and "ZZ3000" in problems[0] and "ZZ3100" in problems[0] and "ZZCOA" in problems[0]
    assert "exactly one" in problems[0]
    # a Draft or Inactive flagged row is not in the chart yet
    assert M.retained_earnings_problems([one, re_leaf(main_account="ZZ3100", status="Draft")]) == []
    assert M.retained_earnings_problems([one, re_leaf(main_account="ZZ3100", status="Inactive")]) == []
    # another chart may have its own
    assert M.retained_earnings_problems([one, re_leaf(main_account="ZZ3100", chart_of_accounts="ZZCOB")]) == []
    # the same row seen twice is one account
    assert M.retained_earnings_problems([one, dict(one)]) == []
    # two charts each with two: one sentence per chart
    problems = M.retained_earnings_problems([one, two, re_leaf(main_account="ZZ3200", chart_of_accounts="ZZCOB"),
                                             re_leaf(main_account="ZZ3300", chart_of_accounts="ZZCOB")])
    assert len(problems) == 2


def test_the_chart_file_carries_the_retained_earnings_flag():
    rows = M.parse_chart_table([
        ["code", "name", "coa", "account_type", "section", "is_retained_earnings"],
        ["ZZ3000", "Retained earnings", "ZZCOA", "Equity", "BS", "yes"],
        ["ZZ1000", "Cash", "ZZCOA", "Asset", "BS", ""],
    ])
    by = {r["main_account"]: r for r in rows}
    assert (by["ZZ3000"]["is_retained_earnings"], by["ZZ1000"]["is_retained_earnings"]) == (1, 0)
    # the alias, and a value that is not yes or no
    rows = M.parse_chart_table([["code", "name", "coa", "Retained Earnings"], ["ZZ3000", "RE", "ZZCOA", "x"]])
    assert rows[0]["is_retained_earnings"] == 1
    assert "is_retained_earnings 'maybe' is not yes or no" in refused(
        M.parse_chart_table, [["code", "name", "coa", "is_retained_earnings"], ["ZZ3000", "RE", "ZZCOA", "maybe"]])
    # without the column, nothing is said about it
    assert "is_retained_earnings" not in M.parse_chart_table([["code", "name", "coa"], ["ZZ1", "X", "ZZCOA"]])[0]


# -- the chart upload's plan checks the retained-earnings rule over the whole file --------------

RE_HEAD = HEAD + ["is_retained_earnings"]


def re_line(code, name="Retained earnings", flagged="yes"):
    """A file line for an Equity leaf on the Balance Sheet, flagged unless told otherwise."""
    return line(code, name, kind="Equity", section="BS") + [flagged]


def test_a_file_flagging_two_retained_earnings_accounts_in_one_chart_is_refused():
    rows = parse(re_line("ZZ3000"), re_line("ZZ3100", "Reserves"), head=RE_HEAD)
    report = M.plan_chart_load(rows, {})
    assert not report["ok"] and report["writes"] == []
    hits = [e for e in report["errors"] if "retained earnings account" in e]
    assert len(hits) == 1 and "ZZ3000" in hits[0] and "ZZ3100" in hits[0] and "ZZCOA" in hits[0], report["errors"]


def test_a_file_flagging_a_second_account_beside_a_published_one_is_refused():
    existing = {"ZZ3000": re_leaf()}   # Published, flagged, same chart
    report = M.plan_chart_load(parse(re_line("ZZ3100", "Reserves"), head=RE_HEAD), existing)
    assert not report["ok"], report
    assert has(report["errors"], "ZZ3000") and has(report["errors"], "ZZ3100"), report["errors"]
    # in another chart it is that chart's own
    other = {"OT3000": re_leaf(main_account="OT3000", chart_of_accounts="OTHER")}
    report = M.plan_chart_load(parse(re_line("ZZ3100", "Reserves"), head=RE_HEAD), other)
    assert report["ok"], report["errors"]


def test_a_file_redeclaring_the_published_retained_earnings_account_is_fine():
    existing = {"ZZ3000": re_leaf()}
    report = M.plan_chart_load(parse(re_line("ZZ3000"), head=RE_HEAD), existing)
    assert report["ok"], report["errors"]
    assert report["unchanged"] == ["ZZ3000"]


def test_one_flagged_account_and_none_existing_is_fine():
    report = M.plan_chart_load(parse(re_line("ZZ3000"), line("ZZ1000", "Cash", kind="Asset", section="BS") + [""],
                                     head=RE_HEAD), {})
    assert report["ok"], report["errors"]
    assert report["insert"] == ["ZZ3000", "ZZ1000"]
