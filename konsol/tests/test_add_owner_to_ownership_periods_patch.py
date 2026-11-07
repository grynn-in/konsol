"""The add_owner_to_ownership_periods patch (konsol#117 pair 1, design §9), host-run.

Ownership Period gained its owner node (task A). Every period written before
that has a blank ``owner_node``; the design's upgrade path fills it with the
owned node's tree parent, the one default the doctype itself pre-fills, and
derives ``owner_group``, ``owner_data_area_id`` and ``owner_label`` the way
the controller's ``_resolve_owner`` does. Names stay (design §1).

``plan`` is pure and tested without a site; ``execute`` runs under a stub
frappe that records every call. A period whose owned node is missing or is a
root stops the migrate naming the period: guessing an owner would invent a
link. Submitted rows are written with ``frappe.db.set_value``, never saved.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "add_owner_to_ownership_periods.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
CONTROLLER_PY = os.path.join(APP_DIR, "consolidation", "doctype", "ownership_period", "ownership_period.py")
CONTROLLER_MODULE = "konsol.consolidation.doctype.ownership_period.ownership_period"
MODULE = "konsol.patches.add_owner_to_ownership_periods"


class _Dict(dict):
    """frappe._dict: a dict with attribute read, which is what get_all returns."""
    __getattr__ = dict.get


class _Stopped(Exception):
    pass


def _with_stubs(stubs, fn):
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        return fn()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _controller():
    """The REAL controller module, loaded by path against a minimal frappe,
    so the patch derives the owner columns with the controller's own
    function rather than a copy of it."""
    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace()
    frappe.db = types.SimpleNamespace()
    utils = types.ModuleType("frappe.utils")
    utils.getdate = lambda v=None: v
    document_mod = types.ModuleType("frappe.model.document")
    document_mod.Document = type("Document", (), {})
    clickhouse = types.ModuleType("konsol.clickhouse")
    clickhouse.sync_doctype_after_commit = lambda *a, **k: None
    period_status = types.ModuleType("konsol.period_status")
    period_status.assert_open_between = lambda *a, **k: None
    period_status.first_period_affected = lambda *a, **k: None
    konsol = types.ModuleType("konsol")
    konsol.__path__ = [APP_DIR]   # the real pure konsol.fiscal_structure_model imports through the stub
    stubs = {
        "frappe": frappe, "frappe.utils": utils,
        "frappe.model": types.ModuleType("frappe.model"), "frappe.model.document": document_mod,
        "konsol": konsol, "konsol.clickhouse": clickhouse,
        "konsol.period_status": period_status,
    }

    def load():
        spec = importlib.util.spec_from_file_location("ownership_period_for_patch_test", CONTROLLER_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _with_stubs(stubs, load)


class _Site:
    """An in-memory site: the periods (any docstatus) and the tree nodes."""

    def __init__(self, periods=(), nodes=(), tables=("Ownership Period", "Consolidation Group")):
        self.periods = [dict(p) for p in periods]
        self.nodes = [dict(n) for n in nodes]
        self.tables = set(tables)
        self.calls = []

    def module(self):
        site = self
        frappe = types.ModuleType("frappe")

        def table_exists(doctype):
            site.calls.append(("table_exists", doctype))
            return doctype in site.tables

        def reload_doc(module, kind, name):
            site.calls.append(("reload_doc", module, kind, name))

        def get_all(doctype, filters=None, fields=None, **k):
            site.calls.append(("get_all", doctype, filters, fields))
            if doctype == "Ownership Period":
                return [_Dict(p) for p in site.periods]
            if doctype == "Consolidation Group":
                return [_Dict(n) for n in site.nodes]
            raise AssertionError(doctype)

        def set_value(doctype, name, field, value=None, update_modified=True, **k):
            site.calls.append(("set_value", doctype, name, field, value, update_modified))
            values = field if isinstance(field, dict) else {field: value}
            for p in site.periods:
                if p["name"] == name:
                    p.update(values)

        def throw(msg, exc=None, **k):
            raise _Stopped(msg)

        frappe.db = types.SimpleNamespace(table_exists=table_exists, set_value=set_value)
        frappe.get_all = get_all
        frappe.reload_doc = reload_doc
        frappe.throw = throw
        frappe.ValidationError = _Stopped
        return frappe


def _load(site=None):
    """Import the patch under ``site``'s stub frappe, with the controller
    module stubbed by the real one (so ``owner_fields`` is the controller's)."""
    site = site or _Site()
    controller = _controller()
    stubs = {
        "frappe": site.module(),
        "konsol": types.ModuleType("konsol"),
        "konsol.consolidation": types.ModuleType("konsol.consolidation"),
        "konsol.consolidation.doctype": types.ModuleType("konsol.consolidation.doctype"),
        "konsol.consolidation.doctype.ownership_period": types.ModuleType("konsol.consolidation.doctype.ownership_period"),
        CONTROLLER_MODULE: controller,
    }

    def load():
        spec = importlib.util.spec_from_file_location("_add_owner_under_test", PATCH_PY)
        patch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(patch)
        return patch

    return _with_stubs(stubs, load)


# A small tree: root group ZZG > entity ZZHOLD > sub-group ZZSUB > entity ZZOP.
ROOT = {"name": "CG-ZZG-", "consolidation_group": "ZZG", "data_area_id": None, "parent_consolidation_group": None}
HOLD = {"name": "CG-ZZG-ZZHOLD", "consolidation_group": "ZZG", "data_area_id": "ZZHOLD", "parent_consolidation_group": "CG-ZZG-"}
SUB = {"name": "CG-ZZSUB-", "consolidation_group": "ZZSUB", "data_area_id": None, "parent_consolidation_group": "CG-ZZG-ZZHOLD"}
LEAF = {"name": "CG-ZZSUB-ZZOP", "consolidation_group": "ZZSUB", "data_area_id": "ZZOP", "parent_consolidation_group": "CG-ZZSUB-"}
NODES = [ROOT, HOLD, SUB, LEAF]


def _period(name, group="ZZSUB", entity="ZZOP", docstatus=1, owner_node=None, **fields):
    row = {"name": name, "consolidation_group": group, "data_area_id": entity, "docstatus": docstatus,
           "owner_node": owner_node, "owner_group": None, "owner_data_area_id": None, "owner_label": None}
    row.update(fields)
    return row


def _nodes(nodes=NODES):
    return [_Dict(n) for n in nodes]


# --- plan: pure --------------------------------------------------------------

def test_a_blank_owner_is_the_owned_nodes_tree_parent_with_the_derived_columns():
    """ZZOP's parent is the sub-group ZZSUB (a group node): owner_group is its
    code, owner_data_area_id blank, owner_label its group code."""
    patch = _load()
    updates, problems = patch.plan([_period("OP-1")], _nodes())
    assert problems == []
    assert updates == [("OP-1", {"owner_node": "CG-ZZSUB-", "owner_group": "ZZSUB",
                                 "owner_data_area_id": None, "owner_label": "ZZSUB"})]


def test_an_entity_parent_gives_its_entity_code_as_owner_label():
    patch = _load()
    updates, _ = patch.plan([_period("OP-2", group="ZZSUB", entity=None)], _nodes())
    assert updates == [("OP-2", {"owner_node": "CG-ZZG-ZZHOLD", "owner_group": "ZZG",
                                 "owner_data_area_id": "ZZHOLD", "owner_label": "ZZHOLD"})]


def test_every_docstatus_is_planned():
    patch = _load()
    rows = [_period("OP-d", docstatus=0), _period("OP-s", docstatus=1), _period("OP-c", docstatus=2)]
    updates, problems = patch.plan(rows, _nodes())
    assert problems == [] and [u[0] for u in updates] == ["OP-d", "OP-s", "OP-c"]


def test_a_filled_owner_is_left_alone():
    patch = _load()
    filled = _period("OP-f", owner_node="CG-ZZG-ZZHOLD", owner_group="ZZG",
                     owner_data_area_id="ZZHOLD", owner_label="ZZHOLD")
    updates, problems = patch.plan([filled, _period("OP-b")], _nodes())
    assert problems == [] and [u[0] for u in updates] == ["OP-b"]


def test_a_second_plan_over_the_updated_rows_plans_nothing():
    patch = _load()
    rows = [_period("OP-1"), _period("OP-2", group="ZZSUB", entity=None)]
    updates, _ = patch.plan(rows, _nodes())
    assert len(updates) == 2
    by_name = {r["name"]: r for r in rows}
    for name, values in updates:
        by_name[name].update(values)
    assert patch.plan(rows, _nodes()) == ([], [])


def test_a_period_on_a_root_node_is_refused_by_name_not_guessed():
    patch = _load()
    updates, problems = patch.plan([_period("OP-root", group="ZZG", entity=None), _period("OP-ok")], _nodes())
    assert [u[0] for u in updates] == ["OP-ok"]
    assert len(problems) == 1 and problems[0][0] == "OP-root"
    assert "CG-ZZG-" in problems[0][1] and "root" in problems[0][1].lower(), problems


def test_a_period_on_a_missing_node_is_refused_by_name():
    patch = _load()
    updates, problems = patch.plan([_period("OP-gone", group="ZZSUB", entity="ZZNOPE")], _nodes())
    assert updates == [] and len(problems) == 1 and problems[0][0] == "OP-gone"
    assert "ZZSUB" in problems[0][1] and "ZZNOPE" in problems[0][1], problems


def test_a_parent_that_is_not_a_node_is_refused_by_name():
    """The tree names a parent that no longer exists: the owner columns
    cannot be derived, so the period is reported, not written half-filled."""
    patch = _load()
    orphan = dict(LEAF, parent_consolidation_group="CG-ZZSUB-GONE")
    updates, problems = patch.plan([_period("OP-orphan")], _nodes([ROOT, HOLD, orphan]))
    assert updates == [] and len(problems) == 1 and problems[0][0] == "OP-orphan"
    assert "CG-ZZSUB-GONE" in problems[0][1], problems


def test_the_owner_columns_come_from_the_controllers_own_function():
    """Reuse, not a copy: the patch imports ``owner_fields`` from the
    controller, and ``_resolve_owner`` derives its three fields through it,
    so the two cannot drift."""
    src = open(PATCH_PY).read()
    assert f"from {CONTROLLER_MODULE} import owner_fields" in src, "import the controller's derivation"
    controller_src = open(CONTROLLER_PY).read()
    body = controller_src.split("def _resolve_owner")[1].split("\n    def ")[0]
    assert "owner_fields(" in body, "_resolve_owner must use owner_fields too"
    controller = _controller()
    assert controller.owner_fields(_Dict(HOLD)) == {"owner_group": "ZZG", "owner_data_area_id": "ZZHOLD",
                                                    "owner_label": "ZZHOLD"}
    assert controller.owner_fields(_Dict(SUB)) == {"owner_group": "ZZSUB", "owner_data_area_id": None,
                                                   "owner_label": "ZZSUB"}


# --- execute: under a stub site ------------------------------------------------

def test_execute_reloads_the_doctype_then_sets_every_blank_row_without_saving():
    site = _Site(periods=[_period("OP-1"), _period("OP-c", docstatus=2),
                          _period("OP-f", owner_node="CG-ZZG-ZZHOLD", owner_group="ZZG",
                                  owner_data_area_id="ZZHOLD", owner_label="ZZHOLD")],
                 nodes=NODES)
    patch = _load(site)
    patch.execute()
    kinds = [c[0] for c in site.calls]
    assert kinds.index("reload_doc") < kinds.index("get_all"), "the columns must exist first"
    assert ("reload_doc", "consolidation", "doctype", "ownership_period") in site.calls
    sets = [c for c in site.calls if c[0] == "set_value"]
    assert [(c[2], c[3], c[5]) for c in sets] == [
        ("OP-1", {"owner_node": "CG-ZZSUB-", "owner_group": "ZZSUB", "owner_data_area_id": None,
                  "owner_label": "ZZSUB"}, False),
        ("OP-c", {"owner_node": "CG-ZZSUB-", "owner_group": "ZZSUB", "owner_data_area_id": None,
                  "owner_label": "ZZSUB"}, False),
    ]
    assert ".save(" not in open(PATCH_PY).read(), "never save() a submitted row"
    assert "rename_doc" not in open(PATCH_PY).read(), "names stay (design §1)"


def test_execute_reads_periods_of_every_docstatus():
    site = _Site(periods=[_period("OP-1")], nodes=NODES)
    patch = _load(site)
    patch.execute()
    reads = [c for c in site.calls if c[0] == "get_all" and c[1] == "Ownership Period"]
    assert len(reads) == 1
    filters = reads[0][2] or {}
    assert "docstatus" not in str(filters), filters


def test_execute_is_idempotent():
    site = _Site(periods=[_period("OP-1"), _period("OP-2", group="ZZSUB", entity=None)], nodes=NODES)
    patch = _load(site)
    patch.execute()
    assert len([c for c in site.calls if c[0] == "set_value"]) == 2
    site.calls.clear()
    patch.execute()
    assert not [c for c in site.calls if c[0] == "set_value"], site.calls


def test_execute_stops_naming_the_period_before_any_write():
    site = _Site(periods=[_period("OP-ok"), _period("OP-root", group="ZZG", entity=None)], nodes=NODES)
    patch = _load(site)
    try:
        patch.execute()
    except _Stopped as e:
        assert "OP-root" in str(e) and "OP-ok" not in str(e), e
    else:
        raise AssertionError("a period on a root node must stop the migrate")
    assert not [c for c in site.calls if c[0] == "set_value"], "nothing written when any row fails"


def test_execute_is_a_no_op_without_the_table():
    site = _Site(tables=())
    patch = _load(site)
    patch.execute()
    assert [c[0] for c in site.calls] == ["table_exists"]


def test_the_patch_is_registered_once():
    with open(PATCHES_TXT) as f:
        lines = [line.strip() for line in f]
    assert lines.count(MODULE) == 1, lines
