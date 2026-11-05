"""Consolidated Statement report: the group filter (review of konsol#362).

Every member row of a Consolidation Group carries its root's code, so a
member's document name (typed in, or passed as a route option the Link
field accepts) used to resolve to the root's code and silently return the
whole root group's statement. A member row must be refused by name.
"""
import ast
import os
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(APP_DIR, "consolidation", "report", "consolidated_statement", "consolidated_statement.py")


class Refused(Exception):
    pass


def _group_code_with(rows):
    """Exec only _group_code against a stub frappe whose get_value reads ``rows``."""
    tree = ast.parse(open(PATH).read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_group_code")

    def get_value(doctype, name, fields, as_dict=False):
        row = rows.get(name)
        if row is None:
            return None
        if isinstance(fields, (list, tuple)):
            return types.SimpleNamespace(**row) if as_dict else tuple(row[f] for f in fields)
        return row[fields]

    def throw(msg, *a, **k):
        raise Refused(msg)

    frappe = types.SimpleNamespace(db=types.SimpleNamespace(get_value=get_value), throw=throw, _=lambda s: s)
    env = {"frappe": frappe}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), PATH, "exec"), env)
    return env["_group_code"]


ROWS = {
    "CG-ZZG-": {"consolidation_group": "ZZG", "data_area_id": None},
    "CG-ZZG-ZZA": {"consolidation_group": "ZZG", "data_area_id": "ZZA"},
}


def test_root_document_name_resolves_to_its_code():
    assert _group_code_with(ROWS)("CG-ZZG-") == "ZZG"


def test_bare_code_is_passed_on():
    assert _group_code_with(ROWS)("ZZG") == "ZZG"


def test_blank_is_none():
    assert _group_code_with(ROWS)(None) is None


def test_member_row_is_refused_not_resolved_to_its_root():
    try:
        _group_code_with(ROWS)("CG-ZZG-ZZA")
    except Refused as e:
        assert "CG-ZZG-ZZA" in str(e) and "root" in str(e)
    else:
        raise AssertionError("a member row resolved to its root's statement")


# --- Prior year: execute() reads the prior year and never fails on it -----

class _ValidationError(Exception):
    pass


def _execute_with(get_statement):
    """Exec the module's functions against stub frappe/statement_api and a
    stub row model that records the prior payload it was handed."""
    tree = ast.parse(open(PATH).read())
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    seen = {}

    class Model:
        @staticmethod
        def columns():
            return ["cols"]

        @staticmethod
        def rows(payload, prior=None):
            seen["rows"] = prior
            return ["rows"]

        @staticmethod
        def message(payload, prior=None):
            seen["message"] = prior
            return "msg"

    def throw(msg, *a, **k):
        raise Refused(msg)

    class _Dict(dict):
        __getattr__ = dict.get

    frappe = types.SimpleNamespace(
        _dict=_Dict, throw=throw, ValidationError=_ValidationError,
        clear_last_message=lambda: None,
        db=types.SimpleNamespace(get_value=lambda *a, **k: None))
    env = {"frappe": frappe, "statement_api": types.SimpleNamespace(get_statement=get_statement),
           "statement_report_model": Model}
    exec(compile(ast.Module(body=fns, type_ignores=[]), PATH, "exec"), env)
    return env["execute"], seen


def test_execute_reads_the_prior_year_for_the_same_period_and_group():
    calls = []

    def get_statement(fy, fp, group):
        calls.append((fy, fp, group))
        return {"state": "ok", "fy": fy}

    execute, seen = _execute_with(get_statement)
    result = execute({"fiscal_year": "2024", "fiscal_period": 12, "consolidation_group": "ZZG"})
    assert calls == [("2024", 12, "ZZG"), (2023, 12, "ZZG")]
    assert seen["rows"] == seen["message"] == {"state": "ok", "fy": 2023}
    assert result == (["cols"], ["rows"], "msg")


def test_execute_still_renders_when_the_prior_year_read_is_refused():
    """FY2022 not declared: get_statement throws for the prior year; the
    report renders this year with the refusal as the prior payload's message."""
    def get_statement(fy, fp, group):
        if fy == 2023:
            raise _ValidationError("FY2023 P12 is not a declared period.")
        return {"state": "ok"}

    execute, seen = _execute_with(get_statement)
    assert execute({"fiscal_year": 2024, "fiscal_period": 12, "consolidation_group": "ZZG"}) \
        == (["cols"], ["rows"], "msg")
    assert seen["rows"]["state"] == "error"
    assert seen["rows"]["message"] == "FY2023 P12 is not a declared period."
