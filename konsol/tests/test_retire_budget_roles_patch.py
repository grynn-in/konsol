"""The retire_budget_roles patch (budget S2-C, Z7, Q7-c), host-run under a stub frappe.

Every holder of a Budget* role gains Entity Accountant and keeps the old role.
A held role, or one a Budget Layer names as its writer role, stays as site
data; an unheld, unnamed one is deleted. The Manager and Approver holders are
named on the migrate log, because none of them becomes EPM Admin.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "retire_budget_roles.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
INSTALL_PY = os.path.join(APP_DIR, "install.py")

BUDGET_ROLES = ("Budget Submitter", "Budget Controller", "Budget Manager", "Budget Approver")


class _Site:
    def __init__(self, roles=BUDGET_ROLES + ("Entity Accountant",), holders=(), layers=(),
                 custom_perms=()):
        self.roles = set(roles)
        # (user, role) pairs, as Has Role rows with parenttype User
        self.has_role = {(u, r) for r, users in dict(holders).items() for u in users}
        self.layers = dict(layers)            # layer_code -> writer_role
        self.custom_perms = set(custom_perms)  # roles a Custom DocPerm names
        self.deleted = []
        self.granted = []
        self.printed = []

    def stub(self):
        site = self
        frappe = types.ModuleType("frappe")

        class DB:
            def exists(self, doctype, name):
                if doctype == "Role":
                    return name if name in site.roles else None
                if doctype == "Has Role":
                    key = (name["parent"], name["role"])
                    assert name.get("parenttype") == "User", name
                    return "row" if key in site.has_role else None
                if doctype == "Budget Layer":
                    hits = [c for c, r in site.layers.items() if r == name["writer_role"]]
                    return hits[0] if hits else None
                if doctype == "Custom DocPerm":
                    return "cp" if name["role"] in site.custom_perms else None
                raise AssertionError(doctype)

        class User:
            def __init__(self, name):
                self.name = name

            def add_roles(self, *roles):
                for r in roles:
                    if (self.name, r) not in site.has_role:
                        site.has_role.add((self.name, r))
                        site.granted.append((self.name, r))

        def get_all(doctype, filters=None, pluck=None, **kw):
            assert doctype == "Has Role" and pluck == "parent", (doctype, pluck)
            assert filters.get("parenttype") == "User", filters
            return [u for (u, r) in sorted(site.has_role) if r == filters["role"]]

        def get_doc(doctype, name=None):
            if isinstance(doctype, dict):
                assert doctype["doctype"] == "Role"

                class NewRole:
                    def insert(self, **kw):
                        site.roles.add(doctype["role_name"])
                return NewRole()
            assert doctype == "User", doctype
            return User(name)

        def delete_doc(doctype, name, **kw):
            assert doctype == "Role", doctype
            site.roles.discard(name)
            site.deleted.append(name)

        frappe.db = DB()
        frappe.get_all = get_all
        frappe.get_doc = get_doc
        frappe.delete_doc = delete_doc
        frappe.reload_doc = lambda *a, **k: None
        return frappe

    def run(self):
        frappe = self.stub()
        saved = sys.modules.get("frappe")
        sys.modules["frappe"] = frappe
        import builtins
        real_print = builtins.print
        builtins.print = lambda *a, **k: self.printed.append(" ".join(str(x) for x in a))
        try:
            spec = importlib.util.spec_from_file_location("retire_budget_roles_t", PATCH_PY)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            m.execute()
        finally:
            builtins.print = real_print
            if saved is None:
                sys.modules.pop("frappe", None)
            else:
                sys.modules["frappe"] = saved
        return self

    def roles_of(self, user):
        return {r for (u, r) in self.has_role if u == user}


def test_every_holder_gains_entity_accountant_and_keeps_the_old_role():
    s = _Site(holders={"Budget Submitter": ["bea@x"], "Budget Controller": ["carl@x"],
                       "Budget Manager": ["mona@x"], "Budget Approver": ["bruno@x", "mona@x"]}).run()
    for user, old in (("bea@x", "Budget Submitter"), ("carl@x", "Budget Controller"),
                      ("mona@x", "Budget Manager"), ("bruno@x", "Budget Approver")):
        assert {"Entity Accountant", old} <= s.roles_of(user), user
    assert sorted(s.granted) == sorted((u, "Entity Accountant") for u in ("bea@x", "carl@x", "mona@x", "bruno@x"))


def test_nobody_becomes_epm_admin():
    s = _Site(holders={r: ["u@x"] for r in BUDGET_ROLES}).run()
    assert not any(r == "EPM Admin" for (_, r) in s.has_role)


def test_a_held_role_is_kept():
    s = _Site(holders={r: ["u@x"] for r in BUDGET_ROLES}).run()
    assert s.deleted == []
    assert set(BUDGET_ROLES) <= s.roles


def test_an_unheld_role_is_deleted():
    s = _Site(holders={"Budget Approver": ["u@x"]}).run()
    assert sorted(s.deleted) == ["Budget Controller", "Budget Manager", "Budget Submitter"]
    assert "Budget Approver" in s.roles
    assert "Entity Accountant" in s.roles


def test_an_unheld_role_a_budget_layer_names_is_kept():
    s = _Site(layers={"challenge": "Budget Controller", "base": ""}).run()
    assert "Budget Controller" in s.roles
    assert sorted(s.deleted) == ["Budget Approver", "Budget Manager", "Budget Submitter"]


def test_an_unheld_role_a_custom_docperm_names_is_kept():
    s = _Site(custom_perms={"Budget Manager"}).run()
    assert "Budget Manager" in s.roles
    assert "Budget Manager" not in s.deleted


def test_manager_and_approver_holders_are_named_on_the_log():
    s = _Site(holders={"Budget Submitter": ["bea@x"], "Budget Manager": ["mona@x"],
                       "Budget Approver": ["bruno@x"]}).run()
    log = "\n".join(s.printed)
    assert "mona@x" in log and "Budget Manager" in log
    assert "bruno@x" in log and "Budget Approver" in log
    assert "EPM Admin" in log            # says they were not made Close Lead
    assert "bea@x" not in log            # only the two senior roles are listed


def test_missing_roles_are_skipped():
    s = _Site(roles=("Entity Accountant",)).run()
    assert s.deleted == [] and s.granted == []


def test_a_rerun_is_a_no_op():
    s = _Site(holders={"Budget Manager": ["mona@x"], "Budget Submitter": ["bea@x"]},
              layers={"board": "Budget Approver"}).run()
    roles, has_role = set(s.roles), set(s.has_role)
    s.granted.clear()
    s.deleted.clear()
    s.run()
    assert s.granted == [] and s.deleted == []
    assert s.roles == roles and s.has_role == has_role


def test_registered_last_in_patches_txt():
    with open(PATCHES_TXT) as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    assert "konsol.patches.retire_budget_roles" in lines
    # after seed_budget_layers, which reads these roles to set writer roles
    assert lines.index("konsol.patches.retire_budget_roles") > lines.index("konsol.patches.seed_budget_layers")


def test_install_no_longer_creates_budget_roles():
    import ast
    with open(INSTALL_PY) as f:
        tree = ast.parse(f.read())
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "ROLES" for t in n.targets))
    roles = ast.literal_eval(node.value)
    assert not [r for r in roles if r.startswith("Budget ")], roles
    assert "Entity Accountant" in roles
