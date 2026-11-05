"""cli_api keeps only what no other entry point offers (Deepak Pai, 5 Nov 2026:
"slim cli_api to the five").

frappectl, a generic Frappe client, already reaches documents (CRUD) and
whitelisted document methods (Dimension/Measure/Dataset publish and unpublish,
Connector test_extract_connection / test_writeback_connection /
provision_airbyte). What only cli_api offers: applying the schema (dbt vars,
ClickHouse DDL, budget fields) and its status, and the whole configuration as
one bundle (export, diff, apply). konsol-cli, the dedicated client, is frozen.
"""
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEPT = {
    "apply_schema_api": ["POST"],
    "get_schema_status_api": None,
    "export_config_api": None,
    "diff_config_api": None,
    "apply_config_api": ["POST"],
}


def _whitelisted():
    with open(os.path.join(APP_DIR, "cli_api.py")) as f:
        tree = ast.parse(f.read())
    out = {}
    for fn in tree.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for d in fn.decorator_list:
            if isinstance(d, ast.Call) and ast.unparse(d.func) == "frappe.whitelist":
                methods = next((ast.literal_eval(k.value) for k in d.keywords if k.arg == "methods"), None)
                out[fn.name] = methods
            elif ast.unparse(d) == "frappe.whitelist":
                out[fn.name] = None
    return out


def test_cli_api_exposes_exactly_the_five():
    assert _whitelisted() == KEPT


def test_what_was_dropped_is_still_reachable_as_document_methods():
    """The dropped wrappers' work stays reachable: these document methods are
    whitelisted, so frappectl (or the Desk) calls them directly."""
    for rel, names in (
        ("epm/doctype/dimension/dimension.py", ("publish", "unpublish")),
        ("epm/doctype/measure/measure.py", ("publish", "unpublish")),
        ("governed_reference.py", ("publish", "unpublish")),
        ("pipeline/doctype/connector/connector.py",
         ("test_extract_connection", "test_writeback_connection", "provision_airbyte")),
    ):
        src = open(os.path.join(APP_DIR, rel)).read()
        tree = ast.parse(src)
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                found[node.name] = any("whitelist" in ast.unparse(d) for d in node.decorator_list)
        for n in names:
            assert found.get(n), f"{rel}: {n} is not a whitelisted method"
