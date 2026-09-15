"""The monthly P&L endpoint feeds the report compiler the chart's accounts (konsol#211).

`build_cell_map` (and its helper `_pnl_accounts`, when present) are lifted from
api.py and run against a stub frappe: an in-memory Main Account table whose
`get_all` honours equality filters and `order_by`. The real, pure
report_compiler of THIS worktree is loaded by path and wrapped so the test sees
exactly which accounts the endpoint passed.
"""
import ast
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API_PATH = os.path.join(APP_DIR, "api.py")
COMPILER_PATH = os.path.join(APP_DIR, "report_compiler.py")

MAIN_ACCOUNTS = [
    # deliberately out of code order
    {"main_account": "ZZ5000", "account_name": "ZZ Costs", "status": "Published",
     "is_group": 0, "statement_section": "Profit and Loss", "chart_of_accounts": "ZZ_CHART_A"},
    {"main_account": "ZZ4000", "account_name": "ZZ Sales", "status": "Published",
     "is_group": 0, "statement_section": "Profit and Loss", "chart_of_accounts": "ZZ_CHART_A"},
    {"main_account": "ZZ4999", "account_name": "ZZ Income group", "status": "Published",
     "is_group": 1, "statement_section": "Profit and Loss", "chart_of_accounts": "ZZ_CHART_A"},
    {"main_account": "ZZ1000", "account_name": "ZZ Cash", "status": "Published",
     "is_group": 0, "statement_section": "Balance Sheet", "chart_of_accounts": "ZZ_CHART_A"},
    {"main_account": "ZZ4500", "account_name": "ZZ Draft income", "status": "Draft",
     "is_group": 0, "statement_section": "Profit and Loss", "chart_of_accounts": "ZZ_CHART_A"},
]


class Refused(Exception):
    pass


def _compiler():
    spec = importlib.util.spec_from_file_location("konsol_report_compiler_under_test", COMPILER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stub_frappe(rows, calls):
    def get_all(doctype, filters=None, fields=None, order_by=None, **kw):
        calls.append({"doctype": doctype, "filters": filters, "fields": fields, "order_by": order_by})
        assert doctype == "Main Account"
        out = [r for r in rows if all(r.get(k) == v for k, v in (filters or {}).items())]
        if order_by:
            key = order_by.split()[0].strip("`")
            out.sort(key=lambda r: r[key], reverse=order_by.lower().endswith("desc"))
        return [types.SimpleNamespace(**{f: r.get(f) for f in fields}) for r in out]

    def throw(msg, exc=None, *a, **k):
        raise Refused(msg)

    fr = types.SimpleNamespace()
    fr.whitelist = lambda *a, **k: (lambda f: f)
    fr.get_all = get_all
    fr.throw = throw
    fr.ValidationError = Refused
    fr._dict = dict
    return fr


def _run(body, rows=MAIN_ACCOUNTS, calls=None):
    src = open(API_PATH).read()
    tree = ast.parse(src)
    wanted = {"build_cell_map", "_pnl_accounts"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert any(n.name == "build_cell_map" for n in nodes), "api.py has no build_cell_map"

    real = _compiler()
    passed = []

    def recording_build_cell_map(*a, **k):
        passed.append((a, k))
        return real.build_cell_map(*a, **k)

    stub = types.ModuleType("konsol.report_compiler")
    stub.build_cell_map = recording_build_cell_map
    stub.list_templates = real.list_templates

    calls = [] if calls is None else calls
    ns = {
        "frappe": _stub_frappe(rows, calls),
        "_get_json_body": lambda: dict(body),
        "_assert_entity_access": lambda entity: None,
    }
    saved = sys.modules.get("konsol.report_compiler")
    sys.modules["konsol.report_compiler"] = stub
    try:
        exec(compile(ast.Module(body=nodes, type_ignores=[]), API_PATH, "exec"), ns)
        result = ns["build_cell_map"]()
    finally:
        if saved is None:
            sys.modules.pop("konsol.report_compiler", None)
        else:
            sys.modules["konsol.report_compiler"] = saved
    return result, passed, calls


def _accounts_passed(passed):
    (args, kwargs), = passed
    if "accounts" in kwargs:
        return kwargs["accounts"]
    return args[4] if len(args) > 4 else None


def test_pnl_monthly_gets_only_published_pnl_leaves_in_code_order():
    result, passed, calls = _run({"template_id": "pnl_monthly", "entity": "ZZE", "year": 2026})
    assert [tuple(a) for a in _accounts_passed(passed)] == [
        ("ZZ4000", "ZZ Sales"),
        ("ZZ5000", "ZZ Costs"),
    ]
    captions = [c["values"][0][0] for c in result["cells"]
                if c["range"] in ("A3", "A4") and "values" in c]
    assert captions == ["ZZ Sales", "ZZ Costs"]
    (call,) = calls
    assert call["filters"] == {
        "status": "Published", "is_group": 0, "statement_section": "Profit and Loss",
    }
    assert "main_account" in call["fields"] and "account_name" in call["fields"]
    assert call["order_by"].split()[0].strip("`") == "main_account"


def test_caption_falls_back_to_the_code_when_the_name_is_blank():
    rows = [{"main_account": "ZZ4100", "account_name": "", "status": "Published",
             "is_group": 0, "statement_section": "Profit and Loss", "chart_of_accounts": "ZZ_CHART_A"}]
    _, passed, _ = _run({"template_id": "pnl_monthly", "entity": "ZZE", "year": 2026}, rows=rows)
    assert [tuple(a) for a in _accounts_passed(passed)] == [("ZZ4100", "ZZ4100")]


def test_chart_without_published_pnl_accounts_is_refused_with_the_compiler_message():
    rows = [r for r in MAIN_ACCOUNTS if r["statement_section"] != "Profit and Loss"]
    try:
        _run({"template_id": "pnl_monthly", "entity": "ZZE", "year": 2026}, rows=rows)
    except Refused as exc:
        assert "no Published Profit and Loss accounts" in str(exc)
    else:
        raise AssertionError("expected the endpoint to refuse")


def test_other_templates_do_not_read_the_chart():
    calls = []
    try:
        _run({"template_id": "zz_unknown", "entity": "ZZE", "year": 2026}, calls=calls)
    except Refused as exc:
        assert "Unknown template_id" in str(exc)
    else:
        raise AssertionError("expected the endpoint to refuse an unknown template")
    assert calls == []


def test_pnl_leaves_in_one_chart_are_passed_unchanged_and_the_chart_is_read():
    _, passed, calls = _run({"template_id": "pnl_monthly", "entity": "ZZE", "year": 2026})
    assert [tuple(a) for a in _accounts_passed(passed)] == [
        ("ZZ4000", "ZZ Sales"),
        ("ZZ5000", "ZZ Costs"),
    ]
    (call,) = calls
    assert "chart_of_accounts" in call["fields"]


def test_pnl_leaves_in_two_charts_are_refused_naming_both():
    rows = [dict(r) for r in MAIN_ACCOUNTS]
    rows.append({"main_account": "ZZ4200", "account_name": "ZZ Other sales", "status": "Published",
                 "is_group": 0, "statement_section": "Profit and Loss", "chart_of_accounts": "ZZ_CHART_B"})
    try:
        _run({"template_id": "pnl_monthly", "entity": "ZZE", "year": 2026}, rows=rows)
    except Refused as exc:
        msg = str(exc)
        assert msg == (
            "The monthly P&L reads one chart, and Published Profit and Loss accounts exist in "
            "charts ZZ_CHART_A, ZZ_CHART_B. Make one chart Inactive or Draft."
        ), msg
    else:
        raise AssertionError("expected the endpoint to refuse P&L leaves from two charts")
