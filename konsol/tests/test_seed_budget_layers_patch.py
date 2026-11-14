"""The seed_budget_layers patch (budget S2, L2), host-run under a stub frappe.

Existing sites get the four layers; a fresh install marks every patch done
without running it, so a fresh site stays empty (Q8-a).
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "seed_budget_layers.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")


class _Site:
    def __init__(self, roles=(), holders=(), layers=(), disabled=()):
        self.roles = set(roles)
        self.disabled = set(disabled)
        self.calls = []
        self.holders = {r: list(u) for r, u in dict(holders).items()}
        self.layers = {l["layer_code"]: dict(l) for l in layers}
        self.inserted = []

    def stub(self):
        site = self
        frappe = types.ModuleType("frappe")

        class DB:
            def exists(self, doctype, name):
                site.calls.append(("exists", doctype))
                if doctype == "Role":
                    return name if name in site.roles else None
                return name if name in site.layers else None

            def count(self, doctype, filters):
                assert doctype == "User", doctype
                users = filters["name"][1]
                return sum(1 for u in users if u not in site.disabled
                           and filters.get("enabled") == 1)

        class Doc:
            def __init__(self, d):
                self.d = d

            def insert(self, **kw):
                site.layers[self.d["layer_code"]] = self.d
                site.inserted.append(self.d)

        def get_all(doctype, filters=None, pluck=None, **kw):
            assert doctype == "Has Role" and pluck == "parent"
            return list(site.holders.get(filters["role"], []))

        frappe.db = DB()
        frappe.get_doc = lambda d: Doc(d)
        frappe.get_all = get_all
        frappe.reload_doc = lambda *a: site.calls.append(("reload_doc",) + a)
        return frappe

    def run(self):
        frappe = self.stub()
        saved = sys.modules.get("frappe")
        sys.modules["frappe"] = frappe
        try:
            spec = importlib.util.spec_from_file_location("seed_budget_layers_t", PATCH_PY)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            m.execute()
        finally:
            if saved is None:
                sys.modules.pop("frappe", None)
            else:
                sys.modules["frappe"] = saved
        return self


ALL_ROLES = ("Budget Controller", "Budget Manager", "Budget Approver")


def test_inserts_four_then_none_on_rerun():
    s = _Site(roles=ALL_ROLES, holders={r: ["u@x"] for r in ALL_ROLES}).run()
    assert sorted(s.layers) == ["base", "board", "challenge", "management"]
    assert len(s.inserted) == 4
    s.inserted.clear()
    s.run()
    assert s.inserted == []
    assert len(s.layers) == 4


def test_writer_roles_where_held():
    s = _Site(roles=ALL_ROLES, holders={r: ["u@x"] for r in ALL_ROLES}).run()
    got = {c: l.get("writer_role") or "" for c, l in s.layers.items()}
    assert got == {"base": "", "challenge": "Budget Controller",
                   "management": "Budget Manager", "board": "Budget Approver"}


def test_role_without_holders_or_record_leaves_writer_role_blank():
    s = _Site(roles=("Budget Controller", "Budget Manager"),
              holders={"Budget Controller": ["u@x"]}).run()
    assert s.layers["challenge"].get("writer_role") == "Budget Controller"
    assert not s.layers["management"].get("writer_role")   # role exists, no holder
    assert not s.layers["board"].get("writer_role")        # no such role


def test_existing_layer_untouched():
    s = _Site(roles=ALL_ROLES, holders={r: ["u@x"] for r in ALL_ROLES},
              layers=[{"layer_code": "board", "label": "Mine", "writer_role": ""}]).run()
    assert s.layers["board"] == {"layer_code": "board", "label": "Mine", "writer_role": ""}
    assert len(s.inserted) == 3


def test_registered_in_patches_txt():
    with open(PATCHES_TXT) as f:
        assert "konsol.patches.seed_budget_layers" in f.read()


def test_reloads_budget_layer_before_touching_it():
    """patches.txt has no sections, so every patch runs pre-model-sync: the
    Budget Layer table does not exist yet on an existing site's first migrate."""
    s = _Site(roles=ALL_ROLES, holders={r: ["u@x"] for r in ALL_ROLES}).run()
    assert s.calls[0] == ("reload_doc", "epm", "doctype", "budget_layer")


def test_only_enabled_users_hold_a_role():
    s = _Site(roles=ALL_ROLES, holders={r: ["gone@x"] for r in ALL_ROLES},
              disabled=("gone@x",)).run()
    assert not any(l.get("writer_role") for l in s.layers.values())
