"""api.get_hierarchy_tree: the subtree root is the row with the smallest lft.

The lookup filters only on consolidation_group, which matches the root row and
every member row of the group (330 rows for one group on the live copy). It
took `limit=1` in the database's default order: `modified desc` on Frappe v15,
`creation desc` on v16. Either way the "root" was whichever row sorted first,
so editing one member after the root made the endpoint return only that
member's subtree (konsol#358 review). The root of a nested set is the row with
the smallest lft, so the read must order by `lft asc`.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _root_lookup():
    with open(os.path.join(APP_DIR, "api.py")) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_hierarchy_tree")
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "frappe.get_all" \
                and node.args and getattr(node.args[0], "value", None) == "Consolidation Group" \
                and any(k.arg == "limit" for k in node.keywords):
            return {k.arg: ast.literal_eval(k.value) for k in node.keywords if k.arg in ("order_by", "limit")}
    raise AssertionError("get_hierarchy_tree has no limited Consolidation Group lookup")


def test_root_lookup_orders_by_lft():
    kw = _root_lookup()
    assert kw.get("order_by") == "lft asc", f"root lookup order_by={kw.get('order_by')!r}"
    assert kw.get("limit") == 1
