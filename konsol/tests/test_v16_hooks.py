"""Frappe v16 hook contracts. First: a has_permission hook must return exactly ``True`` to allow.

v16 no longer reads ``None`` or a truthy non-``True`` value as "allowed"
(https://github.com/frappe/frappe/wiki/Migrating-to-version-16, Permissions).
A hook that returned, say, the allowed set or ``None`` would deny every read
after the upgrade without any error. These tests pin every konsol
has_permission hook to a real ``bool`` on both the allow and the deny path.
"""
import ast
import json
import os
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HOOKS = {
    "konsol.entity_permissions.has_entity_permission",
    "konsol.entity_permissions.has_entity_doc_permission",
    "konsol.entity_permissions.has_close_event_permission",
    "konsol.tb_bulk.has_upload_permission",
    "konsol.budget_bulk.has_upload_permission",
    "konsol.handbook.has_guide_page_permission",
}


def _src(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return f.read()


def _load_functions(rel, names, env):
    """Exec only the named top-level functions of a module, against stubs."""
    tree = ast.parse(_src(rel))
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in body} == set(names), f"{rel} is missing one of {names}"
    exec(compile(ast.Module(body=body, type_ignores=[]), rel, "exec"), env)
    return env


def test_every_has_permission_hook_is_covered_here():
    """A new hook added to hooks.py must be added to HOOKS and tested."""
    tree = ast.parse(_src("hooks.py"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value.startswith("konsol.") and ".has_" in node.value:
            found.add(node.value)
    assert found == HOOKS, f"has_permission hooks changed: {sorted(found ^ HOOKS)}"


def _entity_env(allowed):
    env = {"allowed_entity_codes": lambda user=None: allowed}
    return _load_functions(
        "entity_permissions.py",
        ["may_see_entity", "has_entity_permission", "has_entity_doc_permission",
         "has_close_event_permission"],
        env,
    )


def test_entity_hooks_return_exact_bools():
    doc = types.SimpleNamespace(data_area_id="ZZA", name="ZZA", entity="ZZA")
    blank = types.SimpleNamespace(data_area_id=None, name=None, entity=None)
    hooks = ["has_entity_permission", "has_entity_doc_permission", "has_close_event_permission"]
    cases = [
        (None, doc, True),          # unrestricted user
        ({"ZZA"}, doc, True),       # entity in scope
        ({"ZZB"}, doc, False),      # entity out of scope
        (set(), doc, False),        # empty grant denies
        ({"ZZB"}, blank, True),     # group-level row, no entity
    ]
    for allowed, d, expected in cases:
        env = _entity_env(allowed)
        for name in hooks:
            got = env[name](d, "u@example.com")
            assert got is expected, f"{name} allowed={allowed!r} returned {got!r}"


def test_upload_hook_returns_exact_bools():
    frappe_stub = types.SimpleNamespace(session=types.SimpleNamespace(user="u@example.com"))
    report = json.dumps([{"entity": "ZZA"}, {"entity": "ZZB"}])
    cases = [
        (None, "x@example.com", True),            # unrestricted
        ({"ZZA"}, "u@example.com", True),         # owner
        ({"ZZA", "ZZB"}, "x@example.com", True),  # sees every entity in it
        ({"ZZA"}, "x@example.com", False),        # misses one entity
    ]
    # The budget upload holds every entity's figures too (review fix 8).
    for module in ("tb_bulk.py", "budget_bulk.py"):
        for allowed, owner, expected in cases:
            env = {"json": json, "frappe": frappe_stub,
                   "allowed_entity_codes": lambda user=None, a=allowed: a}
            _load_functions(module, ["has_upload_permission"], env)
            doc = types.SimpleNamespace(owner=owner, report=report)
            got = env["has_upload_permission"](doc, "u@example.com")
            assert got is expected, f"{module} allowed={allowed!r} owner={owner} returned {got!r}"


def test_budget_upload_is_owner_scoped_like_the_trial_balance_upload():
    src = _src("hooks.py")
    assert 'has_permission["Budget Upload"] = "konsol.budget_bulk.has_upload_permission"' in src
    assert 'permission_query_conditions["Budget Upload"] = "konsol.budget_bulk.upload_conditions"' in src


def test_guide_page_hook_returns_exact_bools():
    """konsol#407 phase 3: an editor reads every Konsol Guide Page, anyone
    else a published one only."""
    from konsol import handbook_model
    cases = [
        (["EPM Admin"], 0, True),
        (["System Manager"], None, True),
        (["EPM User"], 1, True),
        (["EPM User"], 0, False),
        (["Entity Accountant"], None, False),
    ]
    for roles, published, expected in cases:
        env = {"M": handbook_model,
               "frappe": types.SimpleNamespace(get_roles=lambda user=None, r=roles: r)}
        _load_functions("handbook.py", ["has_guide_page_permission"], env)
        got = env["has_guide_page_permission"]({"published": published}, "u@example.com")
        assert got is expected, f"roles={roles} published={published!r} returned {got!r}"


def test_apps_screen_hook_points_at_a_shipped_logo_and_the_close_app():
    """v16 builds the desktop from add_to_apps_screen; without it konsol has
    no desktop icon. The logo must be a file the app ships."""
    tree = ast.parse(_src("hooks.py"))
    value = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and getattr(n.targets[0], "id", None) == "add_to_apps_screen")
    assert len(value) == 1
    entry = value[0]
    assert entry["name"] == "konsol" and entry["route"] == "/desk/konsol"
    assert entry["logo"].startswith("/assets/konsol/")
    # The ?v= cache-buster is not part of the file name (test_konsol_logo.py).
    shipped = os.path.join(APP_DIR, "public", entry["logo"].split("?")[0][len("/assets/konsol/"):])
    assert os.path.isfile(shipped), f"logo {entry['logo']} is not in konsol/public"
