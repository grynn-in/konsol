"""Statement Drill report: execute() (konsol/consolidation/report/statement_drill).

The group filter is resolved by Consolidated Statement's own ``_group_code``
(imported, never copied: a member row is refused by name), the breakdown is
``statement_api.get_drill``'s, and the entity rows' Trial Balance
Submissions are read for the period the drill names.
"""
import ast
import os
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(APP_DIR, "consolidation", "report", "statement_drill", "statement_drill.py")


class Refused(Exception):
    pass


def _tree():
    return ast.parse(open(PATH).read())


def test_group_code_is_imported_from_consolidated_statement_not_copied():
    tree = _tree()
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)
               and n.module == "konsol.consolidation.report.consolidated_statement.consolidated_statement"]
    assert any(a.name == "_group_code" for n in imports for a in n.names)
    assert not any(isinstance(n, ast.FunctionDef) and n.name == "_group_code" for n in tree.body)


def _execute_with(drill_payload, submissions=()):
    # The module's functions and constants, without its imports (stubbed below).
    fns = [n for n in _tree().body if isinstance(n, (ast.FunctionDef, ast.Assign))]
    seen = {}

    def get_drill(fy, fp, group, heading):
        seen["get_drill"] = (fy, fp, group, heading)
        return drill_payload

    def get_entity_drill(fy, fp, entity, heading):
        seen["get_entity_drill"] = (fy, fp, entity, heading)
        return drill_payload

    def get_value(doctype, name, field=None, *a, **k):
        seen.setdefault("get_value", []).append((doctype, name, field))
        return "JPY" if doctype == "Entity" else "USD"

    def get_all(doctype, filters=None, fields=None, **kwargs):
        seen.setdefault("get_all", []).append((doctype, filters))
        return list(submissions)

    class Model:
        @staticmethod
        def columns(minor_unit=None):
            return ["cols"]

        @staticmethod
        def rows(payload, currency, tbs=()):
            seen["rows"] = (payload, currency, list(tbs))
            return ["rows"]

        @staticmethod
        def message(payload, tbs=()):
            return "msg"

    def throw(msg, *a, **k):
        raise Refused(msg)

    class _Dict(dict):
        __getattr__ = dict.get

    frappe = types.SimpleNamespace(
        _dict=_Dict, throw=throw,
        db=types.SimpleNamespace(get_value=get_value),
        get_all=get_all)
    env = {"frappe": frappe, "statement_api": types.SimpleNamespace(
               get_drill=get_drill, get_entity_drill=get_entity_drill),
           "statement_drill_report_model": Model,
           "_group_code": lambda value: {"CG-ZZG-": "ZZG"}.get(value, value)}
    exec(compile(ast.Module(body=fns, type_ignores=[]), PATH, "exec"), env)
    return env["execute"], seen


_OK = {"state": "ok", "message": None, "period": {"fiscal_year": 2024, "fiscal_period": 12},
       "drill": {"rows": [
           {"layer": "entity", "entity": "ZZA",
            "source": {"kind": "tb", "entity": "ZZA", "fiscal_year": 2024, "fiscal_period": 12}},
           {"layer": "cta", "entity": None,
            "source": {"kind": "tb", "entity": None, "fiscal_year": 2024, "fiscal_period": 12}},
       ]}}

_FILTERS = {"fiscal_year": 2024, "fiscal_period": 12, "consolidation_group": "CG-ZZG-", "heading": "1"}


def test_execute_drills_the_group_code_and_heading():
    execute, seen = _execute_with(_OK)
    assert execute(dict(_FILTERS)) == (["cols"], ["rows"], "msg")
    assert seen["get_drill"] == (2024, 12, "ZZG", "1")
    assert seen["rows"][1] == "USD"


def test_execute_reads_the_submitted_trial_balances_of_the_entity_rows_only():
    tbs = [{"name": "TBS-ZZA-2024-P12-001", "data_area_id": "ZZA",
            "fiscal_year": 2024, "fiscal_period": 12}]
    execute, seen = _execute_with(_OK, tbs)
    execute(dict(_FILTERS))
    (doctype, filters), = seen["get_all"]
    assert doctype == "Trial Balance Submission"
    assert filters["docstatus"] == 1
    assert filters["data_area_id"] == ["in", ["ZZA"]]
    assert (filters["fiscal_year"], filters["fiscal_period"]) == (2024, 12)
    assert seen["rows"][2] == tbs


def test_a_non_ok_drill_reads_no_trial_balance():
    execute, seen = _execute_with({"state": "error", "message": "x", "drill": None})
    execute(dict(_FILTERS))
    assert "get_all" not in seen


@pytest.mark.parametrize("missing", ["fiscal_year", "fiscal_period", "consolidation_group", "heading"])
def test_a_missing_filter_is_refused(missing):
    execute, _ = _execute_with(_OK)
    filters = dict(_FILTERS)
    del filters[missing]
    with pytest.raises(Refused):
        execute(filters)


_ENTITY_FILTERS = {"fiscal_year": 2024, "fiscal_period": 12, "basis": "Entity", "entity": "ZZA",
                   "heading": "1"}


def test_entity_basis_drills_the_entity_in_its_functional_currency():
    execute, seen = _execute_with(_OK)
    assert execute(dict(_ENTITY_FILTERS)) == (["cols"], ["rows"], "msg")
    assert seen["get_entity_drill"] == (2024, 12, "ZZA", "1")
    assert "get_drill" not in seen
    assert seen["rows"][1] == "JPY"
    assert ("Entity", "ZZA", "functional_currency") in seen["get_value"]


def test_entity_basis_without_an_entity_is_refused_and_needs_no_group():
    execute, _ = _execute_with(_OK)
    filters = dict(_ENTITY_FILTERS)
    del filters["entity"]
    with pytest.raises(Refused, match="entity"):
        execute(filters)


def test_group_basis_still_needs_a_group():
    execute, _ = _execute_with(_OK)
    filters = dict(_FILTERS)
    del filters["consolidation_group"]
    filters["basis"] = "Group"
    with pytest.raises(Refused, match="consolidation group"):
        execute(filters)


def _filter(fieldname):
    source = open(PATH.replace(".py", ".js")).read()
    start = source.index('fieldname: "%s"' % fieldname)
    return source[start:source.index("\t\t},", start)]


def test_js_basis_and_entity_filters():
    basis = _filter("basis")
    assert "Group\\nEntity" in basis and 'default: "Group"' in basis
    entity = _filter("entity")
    assert 'options: "Entity"' in entity
    assert 'depends_on: \'eval:doc.basis=="Entity"\'' in entity
    assert "is_group: 0" in entity


def _basis_change(start, new):
    from konsol.tests.test_desk_report_js_calls import run_basis_change
    return run_basis_change("statement_drill/statement_drill.js", "Statement Drill", start, new)


def test_js_no_filter_uses_mandatory_depends_on():
    # Frappe's query report checks only reqd, never mandatory_depends_on.
    assert "mandatory_depends_on" not in open(PATH.replace(".py", ".js")).read()


def _loaded(basis):
    from konsol.tests.test_desk_report_js_calls import run_onload
    return run_onload("statement_drill/statement_drill.js", "Statement Drill", basis)


def test_js_group_filter_has_no_static_reqd():
    # A static reqd blocks a link opened on the Entity basis ("Please set
    # filters"); onload sets it from the basis the link carries.
    group = _filter("consolidation_group")
    assert "reqd" not in group


def test_js_opened_on_the_group_basis_requires_the_group_not_the_entity():
    got = _loaded("Group")
    assert got["hasOnload"]
    assert got["after"]["consolidation_group"] and not got["after"]["entity"]
    assert "refresh" not in got["calls"], "onload must not refresh: Frappe does"


def test_js_opened_on_the_entity_basis_requires_the_entity_not_the_group():
    got = _loaded("Entity")
    assert got["hasOnload"]
    assert got["after"]["entity"] and not got["after"]["consolidation_group"]
    assert "refresh" not in got["calls"], "onload must not refresh: Frappe does"


def test_js_switching_basis_to_entity_swaps_which_filter_is_required():
    got = _basis_change("Group", "Entity")
    assert got["after"]["entity"] and not got["after"]["consolidation_group"]
    assert got["calls"][-1] == "refresh", got["calls"]


def test_js_switching_basis_back_to_group_swaps_them_back():
    got = _basis_change("Entity", "Group")
    assert got["after"]["consolidation_group"] and not got["after"]["entity"]
    assert got["calls"][-1] == "refresh", got["calls"]
