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
