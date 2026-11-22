"""Every place currency_references counts as "using" a currency is a stored
column. used_currencies() reads them with get_all(pluck=field); a virtual
field there raises "Unknown column" and breaks sign-off and migrate on every
site. Historical Equity Rate's from_currency is virtual (#373) and derives
from Entity.functional_currency, which is counted already (PR #384 review,
5 Nov 2026); its to_currency is stored since konsol#374 and is counted."""
import ast
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _uses():
    tree = ast.parse(open(os.path.join(APP_DIR, "currency_references.py")).read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "_USES":
            out = []
            for elt in node.value.elts:
                out.append((ast.literal_eval(elt.elts[0]), ast.literal_eval(elt.elts[1])))
            return out
    raise AssertionError("_USES not found")


def _field(doctype, fieldname):
    folder = doctype.lower().replace(" ", "_")
    for path in glob.glob(os.path.join(APP_DIR, "*", "doctype", folder, folder + ".json")):
        for f in json.load(open(path))["fields"]:
            if f["fieldname"] == fieldname:
                return f
    return None


def test_every_currency_use_is_a_stored_currency_link():
    for doctype, fieldname in _uses():
        df = _field(doctype, fieldname)
        assert df is not None, f"{doctype}.{fieldname} does not exist"
        assert not df.get("is_virtual"), f"{doctype}.{fieldname} is virtual: get_all cannot read it"
        # Consolidation Journal links Frappe's Currency doctype; its codes are
        # the same ISO codes, so it is a use all the same.
        assert df.get("options") in ("ISO Currency", "Currency"), f"{doctype}.{fieldname} is not a currency link"


def _use_filters():
    """(doctype, field) -> the filter dict of each _USES row; _SUBMITTED is
    {"docstatus": 1}."""
    tree = ast.parse(open(os.path.join(APP_DIR, "currency_references.py")).read())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "_USES":
            for elt in node.value.elts:
                flt = elt.elts[2]
                if isinstance(flt, ast.Name):
                    assert flt.id == "_SUBMITTED", flt.id
                    value = {"docstatus": 1}
                else:
                    value = ast.literal_eval(flt) if not isinstance(flt, ast.Constant) else flt.value
                out[(ast.literal_eval(elt.elts[0]), ast.literal_eval(elt.elts[1]))] = value
    return out


def test_rate_sheet_rows_count_under_a_submitted_sheet():
    """A Rate Sheet's currencies are counted through its child rows, once the
    sheet is submitted: a draft sheet's rows use no currency (T15)."""
    uses = _use_filters()
    for field in ("from_currency", "to_currency"):
        key = ("Rate Sheet Row", field)
        assert key in uses, key
        assert uses[key] == {"docstatus": 1, "parenttype": "Rate Sheet"}, uses[key]
        assert _field("Rate Sheet Row", field)["options"] == "ISO Currency"
