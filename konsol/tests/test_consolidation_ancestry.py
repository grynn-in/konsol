"""Host tests for the consolidation tree walk (F2).

Consolidation correctness now rests on this arithmetic: the link closure decides
which groups an entity's numbers reach, and dbt multiplies each link's dated
percentage along it. Before F2 the tree was stored and never traversed —
GROUP_CORP's consolidated result held its own two entities and none of
GROUP_EMEA's, at any percentage — so these pin the shape that fixed it.

ConsolidationGroup.build_rows is pure by design (no ClickHouse, no frappe calls)
precisely so it can be exercised here without a site.
"""
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(APP_DIR, "consolidation", "doctype", "consolidation_group",
                   "consolidation_group.py")


_STUBBED = ("frappe", "frappe.utils", "frappe.utils.nestedset",
            "konsol", "konsol.clickhouse")


def _load():
    """Import the controller with frappe and konsol.clickhouse stubbed.

    The stubs are removed again afterwards. A stub left in sys.modules under the
    real package name makes every LATER test file import the stub instead of the
    module it wanted — the runner then counts those as missing dependencies and
    skips them, so the suite shrinks silently (it went 750 -> 630 across 15
    files while this was unguarded). build_rows is pure, so the loaded module
    needs nothing from sys.modules once it has been executed.
    """
    saved = {k: sys.modules.get(k) for k in _STUBBED}

    frappe = sys.modules.get("frappe") or types.ModuleType("frappe")
    nested = types.ModuleType("frappe.utils.nestedset")
    nested.NestedSet = type("NestedSet", (), {})
    utils = types.ModuleType("frappe.utils")
    utils.nestedset = nested
    ch = types.ModuleType("konsol.clickhouse")
    ch.sync_doctype = ch.sync_table = lambda *a, **k: None
    sys.modules.update({
        "frappe": frappe,
        "frappe.utils": utils,
        "frappe.utils.nestedset": nested,
        "konsol": types.ModuleType("konsol"),
        "konsol.clickhouse": ch,
    })
    try:
        spec = importlib.util.spec_from_file_location(
            "consolidation_group_under_test", SRC)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.ConsolidationGroup
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class _Node(dict):
    """Stands in for a frappe._dict row (attribute access, None for missing)."""
    __getattr__ = dict.get


def _node(name, group, entity, parent):
    return _Node(name=name, consolidation_group=group, data_area_id=entity,
                 parent_consolidation_group=parent)


# GROUP_CORP
#   ├── USMF, JPMF          (entities)
#   └── GROUP_EMEA          (sub-group)
#         └── DEMF, GBMF    (entities)
TREE = [
    _node("CG-GROUP_CORP-", "GROUP_CORP", "", None),
    _node("CG-GROUP_CORP-USMF", "GROUP_CORP", "USMF", "CG-GROUP_CORP-"),
    _node("CG-GROUP_CORP-JPMF", "GROUP_CORP", "JPMF", "CG-GROUP_CORP-"),
    _node("CG-GROUP_EMEA-", "GROUP_EMEA", "", "CG-GROUP_CORP-"),
    _node("CG-GROUP_EMEA-DEMF", "GROUP_EMEA", "DEMF", "CG-GROUP_EMEA-"),
    _node("CG-GROUP_EMEA-GBMF", "GROUP_EMEA", "GBMF", "CG-GROUP_EMEA-"),
]


def _rows(nodes=None):
    cls = _load()
    nodes = nodes if nodes is not None else TREE
    return cls.build_rows(nodes, {n.name: n for n in nodes})


def _ancestry_as_set(ancestry):
    """(ancestor, entity, link_group, link_entity, link_depth, depth)."""
    return {tuple(r[:6]) for r in ancestry}


# --- the closure ------------------------------------------------------------

def test_an_entity_reaches_every_ancestor_group_not_just_its_parent():
    """The F2 defect in one assertion: GROUP_CORP's consolidated result used to
    contain USMF and JPMF only, because nothing ever walked past the immediate
    parent."""
    _, ancestry = _rows()
    reached = {(r[0], r[1]) for r in ancestry}
    assert ("GROUP_EMEA", "DEMF") in reached
    assert ("GROUP_CORP", "DEMF") in reached, "a sub-group's entity must reach the parent"
    assert ("GROUP_CORP", "GBMF") in reached


def test_the_chain_holds_every_link_between_ancestor_and_entity():
    """DEMF reaches GROUP_CORP through two links — GROUP_CORP's share of
    GROUP_EMEA, then GROUP_EMEA's share of DEMF. dbt multiplies exactly these."""
    _, ancestry = _rows()
    chain = sorted(r for r in _ancestry_as_set(ancestry)
                   if r[0] == "GROUP_CORP" and r[1] == "DEMF")
    assert chain == [
        ("GROUP_CORP", "DEMF", "GROUP_EMEA", "", 1, 2),
        ("GROUP_CORP", "DEMF", "GROUP_EMEA", "DEMF", 2, 2),
    ], chain


def test_a_direct_child_has_a_single_link():
    _, ancestry = _rows()
    chain = [r for r in _ancestry_as_set(ancestry)
             if r[0] == "GROUP_CORP" and r[1] == "USMF"]
    assert chain == [("GROUP_CORP", "USMF", "GROUP_CORP", "USMF", 1, 1)]


def test_group_nodes_are_destinations_never_contributors():
    """A roll-up node has no numbers of its own; only entities contribute."""
    _, ancestry = _rows()
    assert all(r[1] for r in ancestry), "a row with a blank entity is a group contributing"


def test_the_entitys_own_link_is_the_deepest_one():
    """gold_entity_ownership reads direct ownership as the link where
    link_depth = depth, so that link must be the entity's own node."""
    _, ancestry = _rows()
    for anc, entity, link_group, link_entity, link_depth, depth in _ancestry_as_set(ancestry):
        if link_depth == depth:
            assert link_entity == entity, (anc, entity, link_entity)


def test_a_root_only_tree_produces_no_ancestry():
    _, ancestry = _rows([_node("CG-SOLO-", "SOLO", "", None)])
    assert ancestry == []


def test_a_cycle_does_not_hang_the_walk():
    """parent links are user-editable; a cycle must terminate, not spin."""
    cyclic = [
        _node("CG-A-", "A", "", "CG-B-"),
        _node("CG-B-", "B", "", "CG-A-"),
        _node("CG-B-E1", "B", "E1", "CG-B-"),
    ]
    hierarchy, ancestry = _rows(cyclic)
    assert hierarchy and ancestry


# --- the path ---------------------------------------------------------------

def test_path_does_not_repeat_the_parent_group():
    """A leaf's consolidation_group IS its parent's code, so using it for both
    the node and its label produced GROUP_CORP/GROUP_EMEA/GROUP_EMEA/DEMF."""
    hierarchy, _ = _rows()
    paths = {r[4] for r in hierarchy}
    assert "GROUP_CORP/GROUP_EMEA/DEMF" in paths
    assert not any("GROUP_EMEA/GROUP_EMEA" in p for p in paths), paths


def test_hierarchy_level_counts_ancestors():
    hierarchy, _ = _rows()
    level = {(r[0], r[1]): r[3] for r in hierarchy}
    assert level[("GROUP_CORP", "")] == 1
    assert level[("GROUP_EMEA", "")] == 2
    assert level[("GROUP_EMEA", "DEMF")] == 3


# --- the contract the warehouse depends on ----------------------------------

def test_no_ownership_column_is_written_anywhere():
    """F2: the tree is structure. An ownership figure written here could not
    expire, and `d.ownership_pct or 100` turned a real 0% into 100%."""
    import ast
    tree = ast.parse(open(SRC).read())
    # code only — the module docstring names the fields it removed
    code = ast.unparse(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            code = code.replace(node.value, "")
    assert "ownership_pct" not in code
    assert "effective_ownership" not in code


def test_column_lists_match_the_rows_built():
    cls = _load()
    hierarchy, ancestry = _rows()
    assert all(len(r) == len(cls.CH_HIERARCHY_COLUMNS) for r in hierarchy)
    assert all(len(r) == len(cls.CH_STAGING_COLUMNS) for r in ancestry)
    assert cls.CH_STAGING_TABLE == "epm_staging.consolidation_ancestry"
    assert cls.CH_HIERARCHY_TABLE == "epm_staging.consolidation_hierarchy"
