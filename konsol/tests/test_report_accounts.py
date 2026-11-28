"""The monthly P&L reads the chart's accounts (konsol#211), now in report_service.

konsol#548 v1 T9 moved `_pnl_accounts` from api.py into konsol/report_service.py
(api.py keeps the name as an alias). The service is loaded by path under the
stub frappe of test_report_service.py: an in-memory Main Account table whose
`get_all` honours equality filters and `order_by`. The interpreter is wrapped
so the test sees exactly which accounts the service passed. The calendar comes
from report_choices.periods (the stub's Jan-Dec), not from a stopgap here.
"""
from konsol.tests import test_report_service as harness

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


Refused = harness.Refused


def _run(body, rows=MAIN_ACCOUNTS, calls=None):
    site = harness._site(accounts=[dict(r) for r in rows], entities=["ZZE"], years={2026})
    svc = harness._service(site)
    passed = []
    real = svc.compile_formulas

    def recording_compile(template, params, ctx):
        passed.append(ctx)
        return real(template, params, ctx)

    svc.compile_formulas = recording_compile
    try:
        return svc.build(*svc.request(body, svc.LEGACY_BUILD_KEYS)), passed, \
            [c for c in site["calls"] if c["doctype"] == "Main Account"]
    finally:
        if calls is not None:
            calls.extend(c for c in site["calls"] if c["doctype"] == "Main Account")


def _accounts_passed(passed):
    (ctx,) = passed
    return ctx["accounts"]["lines"]


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
