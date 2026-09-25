"""TDD tests for Schema Publish/Unpublish lifecycle on Dimension and Measure."""
import ast
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_json(doctype_dir):
    path = os.path.join(APP_DIR, "epm", "doctype", doctype_dir, f"{doctype_dir}.json")
    with open(path) as f:
        return json.load(f)


def _load_py(doctype_dir):
    path = os.path.join(APP_DIR, "epm", "doctype", doctype_dir, f"{doctype_dir}.py")
    with open(path) as f:
        return f.read()


def _get_field(meta, fieldname):
    for f in meta["fields"]:
        if f.get("fieldname") == fieldname:
            return f
    return None


def _get_class_methods(source):
    tree = ast.parse(source)
    methods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    methods.append(item.name)
    return methods


def _get_whitelisted_methods(source):
    """Return method names decorated with @frappe.whitelist()."""
    tree = ast.parse(source)
    whitelisted = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    for dec in item.decorator_list:
                        dec_str = ast.dump(dec)
                        if "whitelist" in dec_str:
                            whitelisted.append(item.name)
    return whitelisted


# ---------------------------------------------------------------------------
# Dimension JSON — status field
# ---------------------------------------------------------------------------

def test_dimension_has_status_field():
    meta = _load_json("dimension")
    field = _get_field(meta, "status")
    assert field is not None, "Dimension missing 'status' field"


def test_dimension_status_options():
    meta = _load_json("dimension")
    field = _get_field(meta, "status")
    options = field["options"].split("\n")
    assert "Draft" in options
    assert "Published" in options
    assert "Inactive" in options


def test_dimension_status_default_draft():
    meta = _load_json("dimension")
    field = _get_field(meta, "status")
    assert field["default"] == "Draft"


def test_dimension_status_in_list_view():
    meta = _load_json("dimension")
    field = _get_field(meta, "status")
    assert field.get("in_list_view") == 1


# ---------------------------------------------------------------------------
# Measure JSON — status field
# ---------------------------------------------------------------------------

def test_measure_has_status_field():
    meta = _load_json("measure")
    field = _get_field(meta, "status")
    assert field is not None, "Measure missing 'status' field"


def test_measure_status_options():
    meta = _load_json("measure")
    field = _get_field(meta, "status")
    options = field["options"].split("\n")
    assert "Draft" in options
    assert "Published" in options
    assert "Inactive" in options


def test_measure_status_default_draft():
    meta = _load_json("measure")
    field = _get_field(meta, "status")
    assert field["default"] == "Draft"


def test_measure_status_in_list_view():
    meta = _load_json("measure")
    field = _get_field(meta, "status")
    assert field.get("in_list_view") == 1


# ---------------------------------------------------------------------------
# Shared schema_lifecycle.py
# ---------------------------------------------------------------------------

def _load_lifecycle():
    path = os.path.join(APP_DIR, "schema_lifecycle.py")
    with open(path) as f:
        return f.read()


def test_lifecycle_module_exists():
    path = os.path.join(APP_DIR, "schema_lifecycle.py")
    assert os.path.exists(path), "schema_lifecycle.py not found"


def test_lifecycle_calls_apply_schema():
    content = _load_lifecycle()
    assert "apply_schema" in content


def test_lifecycle_requests_governed_build():
    # Publish now routes through the governed Build Approval (full scope)
    # rather than firing a direct dbt build / bare Pipeline Run.
    content = _load_lifecycle()
    assert "Build Approval" in content
    assert "pbr.build_scope" in content


def test_lifecycle_checks_epm_admin():
    content = _load_lifecycle()
    assert "EPM Admin" in content


def test_lifecycle_role_set_includes_administrator():
    content = _load_lifecycle()
    assert "Administrator" in content


# ---------------------------------------------------------------------------
# Dimension Python — publish/unpublish methods
# ---------------------------------------------------------------------------

def test_dimension_has_publish():
    methods = _get_class_methods(_load_py("dimension"))
    assert "publish" in methods


def test_dimension_has_unpublish():
    methods = _get_class_methods(_load_py("dimension"))
    assert "unpublish" in methods


def test_dimension_publish_whitelisted():
    wl = _get_whitelisted_methods(_load_py("dimension"))
    assert "publish" in wl


def test_dimension_unpublish_whitelisted():
    wl = _get_whitelisted_methods(_load_py("dimension"))
    assert "unpublish" in wl


def test_dimension_imports_lifecycle():
    content = _load_py("dimension")
    assert "from konsol.schema_lifecycle import" in content


# test_dimension_no_on_update was deleted with konsol#295. It pinned "saves are
# side-effect-free", which Deepak Pai reversed on 25 Sep 2026: a Dimension that
# ends up Published gets its column however it got there, so the controller now
# has an on_update. test_dimension_published_gets_column.py tests what it does.


# ---------------------------------------------------------------------------
# Measure Python — publish/unpublish methods
# ---------------------------------------------------------------------------

def test_measure_has_publish():
    methods = _get_class_methods(_load_py("measure"))
    assert "publish" in methods


def test_measure_has_unpublish():
    methods = _get_class_methods(_load_py("measure"))
    assert "unpublish" in methods


def test_measure_publish_whitelisted():
    wl = _get_whitelisted_methods(_load_py("measure"))
    assert "publish" in wl


def test_measure_unpublish_whitelisted():
    wl = _get_whitelisted_methods(_load_py("measure"))
    assert "unpublish" in wl


def test_measure_imports_lifecycle():
    content = _load_py("measure")
    assert "from konsol.schema_lifecycle import" in content


def test_measure_no_on_update():
    methods = _get_class_methods(_load_py("measure"))
    assert "on_update" not in methods


# ---------------------------------------------------------------------------
# dbt_config.py — Published filter
# ---------------------------------------------------------------------------

def _extract_function(content, func_name):
    """Extract the source lines of a top-level function by name."""
    tree = ast.parse(content)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            lines = content.splitlines()
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    return ""


def test_dbt_config_dimensions_filter_published():
    path = os.path.join(APP_DIR, "dbt_config.py")
    with open(path) as f:
        content = f.read()
    func = _extract_function(content, "_build_dimensions_vars")
    assert '"Published"' in func, "_build_dimensions_vars missing Published filter"


def test_dbt_config_measures_filter_published():
    path = os.path.join(APP_DIR, "dbt_config.py")
    with open(path) as f:
        content = f.read()
    func = _extract_function(content, "_build_measures_vars")
    assert '"Published"' in func, "_build_measures_vars missing Published filter"


# ---------------------------------------------------------------------------
# schema_apply.py — Published filter
# ---------------------------------------------------------------------------

def test_schema_apply_columns_filter_published():
    path = os.path.join(APP_DIR, "schema_apply.py")
    with open(path) as f:
        content = f.read()
    func = _extract_function(content, "_apply_clickhouse_columns")
    assert '"Published"' in func, "_apply_clickhouse_columns missing Published filter"


def test_schema_apply_budget_fields_filter_published():
    path = os.path.join(APP_DIR, "schema_apply.py")
    with open(path) as f:
        content = f.read()
    # The reads moved behind the sync's named lock (#135 review).
    func = _extract_function(content, "_sync_budget_custom_fields_locked")
    assert '"Published"' in func, "_sync_budget_custom_fields_locked missing Published filter"


# ---------------------------------------------------------------------------
# JS files — custom buttons
# ---------------------------------------------------------------------------

def test_dimension_js_exists():
    path = os.path.join(APP_DIR, "epm", "doctype", "dimension", "dimension.js")
    assert os.path.exists(path), "dimension.js not found"


def test_dimension_js_has_publish_button():
    path = os.path.join(APP_DIR, "epm", "doctype", "dimension", "dimension.js")
    with open(path) as f:
        content = f.read()
    assert "Publish" in content


def test_dimension_js_has_unpublish_button():
    path = os.path.join(APP_DIR, "epm", "doctype", "dimension", "dimension.js")
    with open(path) as f:
        content = f.read()
    assert "Unpublish" in content


def test_measure_js_exists():
    path = os.path.join(APP_DIR, "epm", "doctype", "measure", "measure.js")
    assert os.path.exists(path), "measure.js not found"


def test_measure_js_has_publish_button():
    path = os.path.join(APP_DIR, "epm", "doctype", "measure", "measure.js")
    with open(path) as f:
        content = f.read()
    assert "Publish" in content


def test_measure_js_has_unpublish_button():
    path = os.path.join(APP_DIR, "epm", "doctype", "measure", "measure.js")
    with open(path) as f:
        content = f.read()
    assert "Unpublish" in content


# ---------------------------------------------------------------------------
# Fixtures — status=Published
# ---------------------------------------------------------------------------

def test_konsol_ships_no_dimensions():
    """Decided 17 September 2026: konsol does not ship a starter set of
    Dimensions — the three it shipped encoded one customer's vocabulary and
    could not be retired, because fixtures re-published them every migrate.
    A site declares its own. This replaces the two tests that asserted the
    shipped dimensions existed and were Published."""
    from konsol.tests.shipped import ships
    assert not ships("dimension.json"), "konsol ships dimensions again"


def test_measure_file_ships():
    from konsol.tests.shipped import ships
    assert ships("measure.json"), "measure.json not found in fixtures/ or defaults/"


def test_measure_records_published():
    """Still Published — seeding create-if-missing (konsol#230) changes who owns
    a row, not what a fresh site starts with."""
    from konsol.tests.shipped import shipped
    for rec in shipped("measure.json"):
        assert rec.get("status") == "Published", \
            f"Shipped record {rec.get('name', '?')} not Published"


# ---------------------------------------------------------------------------
# Permissions — EPM Admin role
# ---------------------------------------------------------------------------

def test_dimension_has_epm_admin_permission():
    meta = _load_json("dimension")
    roles = [p["role"] for p in meta["permissions"]]
    assert "EPM Admin" in roles


def test_measure_has_epm_admin_permission():
    meta = _load_json("measure")
    roles = [p["role"] for p in meta["permissions"]]
    assert "EPM Admin" in roles
