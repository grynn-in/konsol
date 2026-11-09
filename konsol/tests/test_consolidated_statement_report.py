"""Consolidated Statement report: the group filter (review of konsol#362).

Every member row of a Consolidation Group carries its root's code, so a
member's document name (typed in, or passed as a route option the Link
field accepts) used to resolve to the root's code and silently return the
whole root group's statement. A member row must be refused by name.
"""
import ast
import importlib.util
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


_spec = importlib.util.spec_from_file_location(
    "statement_report_model_for_report_test", os.path.join(APP_DIR, "close", "statement_report_model.py"))
REAL_MODEL = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(REAL_MODEL)


def _tb(fy, fp):
    return {"fiscal_year": fy, "fiscal_period": fp, "main_account": "4100",
            "adjustment_type": "entity", "amount": 1.0, "null_rows": 0}


def _ok(fy, tb_rows):
    payload = {"state": "ok", "message": None, "statement": {"sections": [], "minor_unit": 2},
               "period": {"fiscal_year": fy, "fiscal_period": 12}}
    return payload, {"tb_rows": tb_rows}


def _execute_with(statement, entity_statement=None):
    """Exec the module's functions against stub frappe/statement_api
    (``statement(fy, fp, group)`` -> ``(payload, reads)``, as
    ``statement_api._statement``) and a row model that records the prior
    payload it was handed (``prior_with_rows`` is the real one)."""
    tree = ast.parse(open(PATH).read())
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    seen = {"calls": []}

    def _statement(fy, fp, group):
        seen["calls"].append((fy, fp, group))
        return statement(fy, fp, group)

    class Model:
        prior_with_rows = staticmethod(REAL_MODEL.prior_with_rows)

        @staticmethod
        def columns(minor_unit=None):
            return ["cols"]

        @staticmethod
        def rows(payload, prior=None, drill_group=None):
            seen["rows"] = prior
            seen["drill_group"] = drill_group
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
    def _entity_statement(fy, fp, entity):
        seen["entity_calls"].append((fy, fp, entity))
        return entity_statement(fy, fp, entity)

    seen["entity_calls"] = []

    def get_statement(*a):
        seen["group_called"] = True
        return _statement(*a)[0]

    api = types.SimpleNamespace(
        get_statement=get_statement, _statement=_statement,
        get_entity_statement=lambda *a: _entity_statement(*a)[0], _entity_statement=_entity_statement)
    env = {"frappe": frappe, "statement_api": api, "statement_report_model": Model}
    exec(compile(ast.Module(body=fns, type_ignores=[]), PATH, "exec"), env)
    return env["execute"], seen


def test_execute_reads_the_prior_year_for_the_same_period_and_group():
    execute, seen = _execute_with(lambda fy, fp, group: _ok(int(fy), [_tb(int(fy), 12)]))
    result = execute({"fiscal_year": "2024", "fiscal_period": 12, "consolidation_group": "ZZG"})
    assert seen["calls"] == [("2024", 12, "ZZG"), (2023, 12, "ZZG")]
    assert seen["rows"] is seen["message"]
    assert seen["rows"]["state"] == "ok" and seen["rows"]["period"]["fiscal_year"] == 2023
    assert result == (["cols"], ["rows"], "msg")


def test_execute_still_renders_when_the_prior_year_read_is_refused():
    """The prior year is not a declared period: the read throws; the report
    renders this year with the refusal as the prior payload's message."""
    def statement(fy, fp, group):
        if fy == 2023:
            raise _ValidationError("FY2023 P12 is not a declared period.")
        return _ok(int(fy), [_tb(int(fy), 12)])

    execute, seen = _execute_with(statement)
    assert execute({"fiscal_year": 2024, "fiscal_period": 12, "consolidation_group": "ZZG"}) \
        == (["cols"], ["rows"], "msg")
    assert seen["rows"]["state"] == "error"
    assert seen["rows"]["message"] == "FY2023 P12 is not a declared period."


def test_an_ok_prior_year_with_no_warehouse_rows_is_not_shown_as_zero():
    """Measured live 5 Nov 2026: FY2022 P12 for ECL_GROUP is state ok with
    no rows at all, every line 0.00. The prior year must be blank with the
    reason, not a column of zeros."""
    execute, seen = _execute_with(
        lambda fy, fp, group: _ok(int(fy), [_tb(int(fy), 12)] if int(fy) == 2023 else []))
    execute({"fiscal_year": 2023, "fiscal_period": 12, "consolidation_group": "ZZG"})
    assert seen["rows"]["state"] != "ok"
    assert "No rows in the warehouse for FY2022" in seen["rows"]["message"]


def test_execute_hands_the_chosen_group_filter_to_the_heading_drill_links():
    execute, seen = _execute_with(lambda fy, fp, group: _ok(int(fy), [_tb(int(fy), 12)]))
    execute({"fiscal_year": 2024, "fiscal_period": 12, "consolidation_group": "CG-ZZG-"})
    assert seen["drill_group"] == "CG-ZZG-"




# --- Basis: Entity (konsol#495 row 4) --------------------------------------

def test_entity_basis_reads_the_entity_statement_and_its_prior_year():
    execute, seen = _execute_with(
        lambda *a: (_ for _ in ()).throw(AssertionError("group statement read on entity basis")),
        lambda fy, fp, entity: _ok(int(fy), [_tb(int(fy), 12)]))
    result = execute({"fiscal_year": "2024", "fiscal_period": 12, "basis": "Entity", "entity": "ZZA"})
    assert seen["entity_calls"] == [("2024", 12, "ZZA"), (2023, 12, "ZZA")]
    assert seen["calls"] == [] and not seen.get("group_called")
    assert seen["drill_group"] is None
    assert seen["rows"]["state"] == "ok" and seen["rows"]["period"]["fiscal_year"] == 2023
    assert result == (["cols"], ["rows"], "msg")


def test_entity_basis_without_an_entity_is_refused():
    execute, seen = _execute_with(lambda *a: _ok(2024, []), lambda *a: _ok(2024, []))
    try:
        execute({"fiscal_year": 2024, "fiscal_period": 12, "basis": "Entity"})
    except Refused as e:
        assert "entity" in str(e).lower()
    else:
        raise AssertionError("entity basis without an entity was not refused")
    assert seen["entity_calls"] == [] and seen["calls"] == []


def test_entity_basis_prior_year_refusal_does_not_fail_the_report():
    def entity_statement(fy, fp, entity):
        if fy == 2023:
            raise _ValidationError("FY2023 P12 is not a declared period.")
        return _ok(int(fy), [_tb(int(fy), 12)])

    execute, seen = _execute_with(lambda *a: _ok(2024, []), entity_statement)
    execute({"fiscal_year": 2024, "fiscal_period": 12, "basis": "Entity", "entity": "ZZA"})
    assert seen["rows"]["state"] == "error"


def _js():
    return open(PATH.replace(".py", ".js")).read()


def _filter(fieldname):
    source = _js()
    start = source.index('fieldname: "%s"' % fieldname)
    return source[start:source.index("\t\t},", start)]


def test_js_basis_and_entity_filters():
    basis = _filter("basis")
    assert "Group\\nEntity" in basis and 'default: "Group"' in basis
    entity = _filter("entity")
    assert 'options: "Entity"' in entity
    assert 'depends_on: \'eval:doc.basis=="Entity"\'' in entity
    assert 'mandatory_depends_on: \'eval:doc.basis=="Entity"\'' in entity
    assert "is_group: 0" in entity


def test_js_consolidation_group_is_hidden_on_the_entity_basis():
    group = _filter("consolidation_group")
    assert 'depends_on: \'eval:doc.basis!="Entity"\'' in group


def test_js_drill_url_uses_the_entity_when_the_row_carries_one():
    source = _js()
    start = source.index("function drill_url")
    body = source[start:source.index("\n}\n", start)]
    assert "data.drill_entity" in body
    assert 'params.set("basis", "Entity")' in body
    assert 'params.set("entity", data.drill_entity)' in body
    assert 'params.set("consolidation_group", data.drill_group || "")' in body
    assert "heading: data.code" in body


def test_js_heading_link_shows_for_an_entity_row_too():
    source = _js()
    assert "data.code && (data.drill_group || data.drill_entity)" in source


def _export_body():
    source = _js()
    start = source.index("function export_to_excel")
    return source[start:source.index("\n}\n", start)]


def test_js_export_button_posts_all_five_filters():
    body = _export_body()
    assert "open_url_post(frappe.request.url" in body
    assert 'cmd: "konsol.close.statement_api.export_statement"' in body
    assert "get_filter_value(name)" in body
    for name in ("fiscal_year", "fiscal_period", "basis", "consolidation_group", "entity"):
        assert '%s: value("%s")' % (name, name) in body, name


def test_js_export_button_is_added_and_removed_in_after_datatable_render():
    source = _js()
    hook = source[source.index("after_datatable_render"):]
    assert "page.remove_inner_button(EXPORT_TO_EXCEL)" in hook
    assert "page.add_inner_button(EXPORT_TO_EXCEL, export_to_excel)" in hook
    assert "has_statement()" in hook
