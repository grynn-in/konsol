"""The one chart reader (konsol#182): konsol/group_chart.py.

The group chart is the Published Main Accounts in MariaDB and nothing else
(decided 13 Sep 2026: konsol defines the shape; no ERP fallback). The wiped-site
bug this fixes: every caller queried the warehouse's ERP-derived chart itself,
and on a warehouse that had never built, UNKNOWN_TABLE was reported as
"ClickHouse is unreachable" with no chart to fall back to.

group_chart.py runs here, loaded by path against a stub frappe; group_chart_model
is the real module. No konsol.clickhouse stub is installed, so the module
loading at all proves it does not import the warehouse client.
"""
import ast
import glob
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GC_PATH = os.path.join(APP_DIR, "group_chart.py")
PL, BS = "Profit and Loss", "Balance Sheet"


class _Row(dict):
    __getattr__ = dict.get


def load(accounts=(), table=True):
    """group_chart against ``accounts`` (Main Account rows)."""
    frappe = types.ModuleType("frappe")
    frappe.get_all_filters = []

    def get_all(doctype, filters=None, fields=None, limit_page_length=None, **kw):
        assert doctype == "Main Account"
        assert table, "read a table that does not exist"
        frappe.get_all_filters.append(filters)
        return [_Row({f: r.get(f) for f in fields}) for r in accounts
                if all(r.get(k) == v for k, v in (filters or {}).items())]

    frappe.get_all = get_all
    frappe.get_list_calls = []
    frappe.get_list = lambda *a, **k: frappe.get_list_calls.append((a, k)) or []
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.db = types.SimpleNamespace(table_exists=lambda name: table)
    spec = importlib.util.spec_from_file_location("gcm_for_group_chart", os.path.join(APP_DIR, "group_chart_model.py"))
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    konsol = types.ModuleType("konsol")
    konsol.group_chart_model = model
    stubs = {"frappe": frappe, "konsol": konsol, "konsol.group_chart_model": model}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("group_chart_under_test", GC_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def account(code, status="Published", is_group=0, **kw):
    row = {"main_account": code, "account_name": f"Account {code}", "account_type": "Asset",
           "statement_section": BS, "normal_balance": "Debit", "fx_method": "closing", "is_group": is_group,
           "is_posting": 0 if is_group else 1, "is_suspended": 0, "allow_ic": 0, "main_account_category": None,
           "status": status, "sub_section": "not handed out"}
    row.update(kw)
    return row


def test_the_chart_is_the_published_main_accounts():
    gc = load([account("ZZ1000"), account("ZZ4000", account_type="Revenue", statement_section=PL, allow_ic=1)])
    chart = gc.chart_accounts()
    assert set(chart) == {"ZZ1000", "ZZ4000"}
    assert chart["ZZ4000"] == {
        "main_account": "ZZ4000", "account_name": "Account ZZ4000", "account_type": "Revenue",
        "statement_section": PL, "normal_balance": "Debit", "fx_method": "closing", "is_group": 0,
        "is_posting": 1, "is_suspended": 0, "allow_ic": 1, "main_account_category": ""}
    assert gc.chart_codes() == {"ZZ1000", "ZZ4000"}


def test_an_undeclared_account_is_not_in_the_chart():
    """No ERP fallback (13 Sep 2026): what konsol does not hold, the chart does not have."""
    gc = load([account("ZZ1000")])
    assert "ZZ7777" not in gc.chart_codes()
    assert gc.chart_codes() == {"ZZ1000"}


def test_draft_and_inactive_are_not_members():
    gc = load([account("ZZ1000"), account("ZZ2000", status="Draft"), account("ZZ3000", status="Inactive")])
    assert gc.chart_codes() == {"ZZ1000"}
    assert gc.frappe.get_all_filters == [{"status": "Published"}]


def test_group_nodes_are_not_posting_members():
    gc = load([account("ZZ9000", is_group=1), account("ZZ1000", parent_account="ZZ9000")])
    chart = gc.chart_accounts()
    assert chart["ZZ9000"]["is_group"] == 1   # in the chart, as a heading
    assert gc.posting_codes(chart) == {"ZZ1000"} == gc.chart_codes()


def test_a_site_without_the_doctype_has_an_empty_chart():
    """A hot-copied release before migrate: nothing is in the chart, so a trial
    balance is refused, never accepted unvalidated."""
    gc = load([account("ZZ1000")], table=False)
    assert gc.chart_accounts() == {} and gc.chart_codes() == set()


def test_no_warehouse_is_read():
    """The wiped-site bug cannot come back: the reader imports nothing that
    talks to ClickHouse, and names no warehouse table."""
    with open(GC_PATH) as f:
        tree = ast.parse(f.read())
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert imported == {"frappe", "konsol"}, imported
    assert not any(s for s in _live_strings(tree) if "epm_" in s or "SELECT" in s.upper())


def test_get_chart_applies_permissions():
    gc = load()
    gc.get_chart("ZZCOA")
    (args, kwargs), = gc.frappe.get_list_calls
    assert args == ("Main Account",) and kwargs["filters"] == {"chart_of_accounts": "ZZCOA"}
    assert kwargs["order_by"] == "lft asc"


def _live_strings(tree):
    """String constants that are code, not documentation: docstrings and bare
    string statements are dropped, so a comment explaining a removal never
    counts as a use."""
    docs = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        for stmt in body if isinstance(body, list) else []:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str):
                docs.add(id(stmt.value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


#: The only files that may still read the warehouse's ERP-derived chart, and why.
ALLOWED = {
    "api.py": "the budget category map; moves to the group chart in a later PR (#182 PR5)",
    os.path.join("epm", "doctype", "main_account_category", "main_account_category.py"):
        "the category permission target; moves to the group chart in a later PR (#182 PR5)",
}
CALLERS = ("tb_bulk.py",
           os.path.join("consolidation", "doctype", "trial_balance_submission", "trial_balance_submission.py"),
           os.path.join("consolidation", "doctype", "consolidation_group", "consolidation_group.py"),
           os.path.join("consolidation", "doctype", "intercompany_account", "intercompany_account.py"))


def test_no_caller_queries_silver_main_accounts_directly():
    offenders, defined = [], []
    for path in glob.glob(os.path.join(APP_DIR, "**", "*.py"), recursive=True):
        rel = os.path.relpath(path, APP_DIR)
        if rel.startswith("tests" + os.sep):
            continue
        with open(path) as f:
            tree = ast.parse(f.read())
        # the table, schema-qualified: a query must name it so. The bare model
        # name is a dbt selector (tasks.SCOPE_SELECTOR["chart"]), not a read.
        if rel not in ALLOWED and any("epm_silver.silver_main_accounts" in s for s in _live_strings(tree)):
            offenders.append(rel)
        defined += [f"{rel}:{n.name}" for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "_chart_accounts"]
    assert offenders == [], offenders
    assert defined == [], defined
    for rel in ALLOWED:
        assert os.path.exists(os.path.join(APP_DIR, rel)), f"stale allowance: {rel}"
    for rel in CALLERS:   # every membership check asks the one reader
        with open(os.path.join(APP_DIR, rel)) as f:
            calls = [n for n in ast.walk(ast.parse(f.read()))
                     if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "chart_codes"]
        assert calls, rel
