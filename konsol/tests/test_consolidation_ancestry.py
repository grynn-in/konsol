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
import inspect
import os
import sys
import types

import pytest

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
    ch.after_commit_once = ch.sync_doctype_after_commit = lambda *a, **k: None
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


def _rows(nodes=None, links=()):
    cls = _load()
    nodes = nodes if nodes is not None else TREE
    return cls.build_rows(nodes, {n.name: n for n in nodes}, links)


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


def test_a_cycle_in_the_tree_is_refused_by_name_not_spun():
    """parent links are user-editable; a cycle must terminate. konsol#117: it
    terminates by raising, naming the cycle (the tree walk used to stop
    quietly at the repeated node and write a closure that was wrong by
    omission). Task B refuses the cycle at validate; this is the backstop."""
    cyclic = [
        _node("CG-A-", "A", "", "CG-B-"),
        _node("CG-B-", "B", "", "CG-A-"),
        _node("CG-B-E1", "B", "E1", "CG-B-"),
    ]
    with pytest.raises(Exception) as e:
        _rows(cyclic)
    assert type(e.value).__name__ == "OwnershipCycle"
    assert "A → B → A" in str(e.value) or "B → A → B" in str(e.value), str(e.value)


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


# --- konsol#117 pair 1, task C: the closure is a path enumeration over links --
#
# Ownership Period becomes the link (owner_node -> owned node). build_rows walks
# every path from every node along those links and writes one row per (ancestor,
# entity, link on the path), with path_id naming the path. A link row without an
# owner (every row before task G's patch) falls back to the tree parent, so on a
# tree the rows are today's plus the three new columns.
#
# Row: [ancestor_group, entity, link_group, link_entity, link_depth, depth,
#       path (the entity's TREE path, unchanged), path_id, link_owner_group,
#       link_owner_entity]

def _link(group, entity, owner):
    """An Ownership Period row as _links() reads it: the owned node by
    (consolidation_group, data_area_id), the owner by node name."""
    return {"consolidation_group": group, "data_area_id": entity, "owner_node": owner}


# Design comment section 2, shape 1: P->A 80, A->D 60, P->D 20. D sits under A
# in the tree; P's direct 20% is a second link into D.
SHAPE_1 = [
    _node("CG-P-", "P", "", None),
    _node("CG-P-A", "P", "A", "CG-P-"),
    _node("CG-P-D", "P", "D", "CG-P-A"),
]
SHAPE_1_LINKS = [
    _link("P", "A", "CG-P-"),   # P -> A
    _link("P", "D", "CG-P-A"),  # A -> D
    _link("P", "D", "CG-P-"),   # P -> D
]


def test_shape_1_emits_two_paths_to_d_with_distinct_path_ids():
    """From P, two paths reach D: P/A/D (two links) and P/D (one). A's own
    path to D (A/D) keys under (P, D) as well, because A is an entity node
    under P and the row's group is the ancestor's group code, which is how the
    live tree reads (every node there carries the one group code). path_id is what
    tells the paths apart; today's rows could not."""
    _, ancestry = _rows(SHAPE_1, SHAPE_1_LINKS)
    to_d = sorted(tuple(r) for r in ancestry if r[0] == "P" and r[1] == "D")
    assert to_d == [
        ("P", "D", "P", "A", 1, 2, "P/A/D", "P/A/D", "P", ""),
        ("P", "D", "P", "D", 1, 1, "P/A/D", "A/D", "P", "A"),
        ("P", "D", "P", "D", 1, 1, "P/A/D", "P/D", "P", ""),
        ("P", "D", "P", "D", 2, 2, "P/A/D", "P/A/D", "P", "A"),
    ], to_d
    from_p = {r[7] for r in to_d if r[7].startswith("P/")}
    assert from_p == {"P/A/D", "P/D"}, "two paths from P, two path_ids"
    assert {r[7] for r in to_d} == {"P/A/D", "P/D", "A/D"}


def test_shape_1_has_one_path_to_a_and_five_rows_in_all():
    _, ancestry = _rows(SHAPE_1, SHAPE_1_LINKS)
    rows = sorted(tuple(r) for r in ancestry if (r[0], r[1]) != ("P", "D"))
    assert rows == [("P", "A", "P", "A", 1, 1, "P/A", "P/A", "P", "")], rows
    assert len(ancestry) == 5


def test_path_keeps_its_meaning_and_path_id_names_the_path():
    """`path` is the entity's tree path from the root, as before; `path_id` is
    the link path from THIS ancestor, so dbt can sum per path."""
    _, ancestry = _rows(SHAPE_1, SHAPE_1_LINKS)
    assert {r[6] for r in ancestry if r[1] == "D"} == {"P/A/D"}
    assert all(r[7].endswith("/" + r[1]) for r in ancestry)
    assert all(r[7].startswith(r[0] + "/") or r[7].startswith("A/") for r in ancestry)


def test_the_link_owner_is_the_node_the_link_leaves():
    _, ancestry = _rows(SHAPE_1, SHAPE_1_LINKS)
    owners = {(r[7], r[4]): (r[8], r[9]) for r in ancestry}
    assert owners[("P/A/D", 1)] == ("P", "")    # P owns A
    assert owners[("P/A/D", 2)] == ("P", "A")   # A owns D
    assert owners[("P/D", 1)] == ("P", "")      # P owns D directly
    assert owners[("A/D", 1)] == ("P", "A")


# --- the tree fallback: today's rows plus the new columns ---------------------

def test_without_links_a_tree_yields_exactly_todays_rows():
    """Task A adds owner_node and task G fills it; until then every link row is
    blank and the tree parent is the owner, so the closure is unchanged."""
    _, ancestry = _rows()
    assert len(ancestry) == 8  # USMF 1, JPMF 1, DEMF 1+2, GBMF 1+2
    assert sorted(tuple(r) for r in ancestry if r[1] == "DEMF") == [
        ("GROUP_CORP", "DEMF", "GROUP_EMEA", "", 1, 2,
         "GROUP_CORP/GROUP_EMEA/DEMF", "GROUP_CORP/GROUP_EMEA/DEMF", "GROUP_CORP", ""),
        ("GROUP_CORP", "DEMF", "GROUP_EMEA", "DEMF", 2, 2,
         "GROUP_CORP/GROUP_EMEA/DEMF", "GROUP_CORP/GROUP_EMEA/DEMF", "GROUP_EMEA", ""),
        ("GROUP_EMEA", "DEMF", "GROUP_EMEA", "DEMF", 1, 1,
         "GROUP_CORP/GROUP_EMEA/DEMF", "GROUP_EMEA/DEMF", "GROUP_EMEA", ""),
    ]


def test_a_link_without_an_owner_falls_back_to_the_tree_parent():
    """A row with owner_node blank or missing (before task A's field exists,
    or before task G's patch fills it) is the tree link, not no link."""
    blank = [_link("GROUP_EMEA", "DEMF", None),
             {"consolidation_group": "GROUP_EMEA", "data_area_id": "GBMF"}]
    _, with_blank = _rows(TREE, blank)
    _, without = _rows()
    assert sorted(map(tuple, with_blank)) == sorted(map(tuple, without))


def test_a_declared_owner_replaces_the_tree_parent():
    """DEMF owned by USMF (a sibling branch), not by GROUP_EMEA: the closure
    follows the link. The tree still places DEMF under GROUP_EMEA for
    presentation (`path`), but no path reaches DEMF from GROUP_EMEA; dbt's
    assert_tree_node_reached_by_links reports that."""
    _, ancestry = _rows(TREE, [_link("GROUP_EMEA", "DEMF", "CG-GROUP_CORP-USMF")])
    demf = sorted(tuple(r) for r in ancestry if r[1] == "DEMF")
    assert demf == [
        ("GROUP_CORP", "DEMF", "GROUP_CORP", "USMF", 1, 2,
         "GROUP_CORP/GROUP_EMEA/DEMF", "GROUP_CORP/USMF/DEMF", "GROUP_CORP", ""),
        ("GROUP_CORP", "DEMF", "GROUP_EMEA", "DEMF", 1, 1,
         "GROUP_CORP/GROUP_EMEA/DEMF", "USMF/DEMF", "GROUP_CORP", "USMF"),
        ("GROUP_CORP", "DEMF", "GROUP_EMEA", "DEMF", 2, 2,
         "GROUP_CORP/GROUP_EMEA/DEMF", "GROUP_CORP/USMF/DEMF", "GROUP_CORP", "USMF"),
    ], demf
    assert not [r for r in demf if r[7].startswith("GROUP_EMEA/")], "no path from the tree parent"


def test_the_same_edge_declared_twice_is_one_link():
    """Two periods on one (owner, owned) edge are two tranches of one link,
    not two paths: the closure is undated (dbt resolves the dates)."""
    twice = SHAPE_1_LINKS + [_link("P", "D", "CG-P-")]
    _, once_rows = _rows(SHAPE_1, SHAPE_1_LINKS)
    _, twice_rows = _rows(SHAPE_1, twice)
    assert sorted(map(tuple, twice_rows)) == sorted(map(tuple, once_rows))


def test_a_link_naming_an_unknown_node_is_ignored_not_fatal():
    unknown = SHAPE_1_LINKS + [_link("P", "ZZNOPE", "CG-P-"), _link("P", "D", "CG-ZZ-")]
    _, rows = _rows(SHAPE_1, unknown)
    _, expected = _rows(SHAPE_1, SHAPE_1_LINKS)
    assert sorted(map(tuple, rows)) == sorted(map(tuple, expected))


# --- cycles ------------------------------------------------------------------

def test_a_cycle_through_links_is_refused_naming_it():
    """Design comment section 3: task B refuses the cycle at validate; this is
    the build-time backstop for one that slipped in through a patch."""
    nodes = [
        _node("CG-R-", "R", "", None),
        _node("CG-R-X", "R", "X", "CG-R-"),
        _node("CG-R-Y", "R", "Y", "CG-R-X"),
    ]
    links = [_link("R", "X", "CG-R-"), _link("R", "Y", "CG-R-X"), _link("R", "X", "CG-R-Y")]
    with pytest.raises(Exception) as e:
        _rows(nodes, links)
    assert type(e.value).__name__ == "OwnershipCycle"
    assert "X → Y → X" in str(e.value), str(e.value)
    assert "konsol#117" in str(e.value)


def test_a_self_link_is_the_one_node_cycle():
    nodes = [_node("CG-R-", "R", "", None), _node("CG-R-X", "R", "X", "CG-R-")]
    links = [_link("R", "X", "CG-R-"), _link("R", "X", "CG-R-X")]
    with pytest.raises(Exception) as e:
        _rows(nodes, links)
    assert "X → X" in str(e.value), str(e.value)


# --- what resync_staging feeds the walk ------------------------------------

def test_resync_reads_submitted_links_and_tolerates_a_stack_without_the_owner_field():
    """_links(): submitted Ownership Periods only (a draft is not yet a link;
    a cancelled one is gone), and nothing at all on a stack where task A's
    owner_node column does not exist yet, so build_rows falls back to the tree."""
    cls = _load()
    src = inspect.getsource(cls._links)
    assert 'has_column("Ownership Period", "owner_node")' in src
    assert '"docstatus": 1' in src
    assert '"owner_node"' in src
    resync = inspect.getsource(cls.resync_staging)
    assert "cls._links()" in resync
    assert "cls.build_rows(docs, by_name, links)" in resync


# --- one group code, many nodes: a path starts where its code does ------------
# The live tree (konsol#117 task H, 7 Nov 2026): 330 nodes all carrying the ONE
# group code; 27 entity-carrying nodes have children. A row's group is the
# ancestor's CODE, so a path from US_ECL and a path from the root both key under
# (ECL_GROUP, entity). The macro sums per path_id within a key: two starts with
# one code made eff(ECL_GROUP, E) = 2..5 and failed the build
# (assert_effective_ownership_in_range FAIL 119). Only the node that defines a
# code — the root, or a node whose tree parent carries a different code — starts
# paths; a node sharing its parent's code is a stop on them, never a start.

LIVE_SHAPE = [
    _node("CG-G-", "G", "", None),
    _node("CG-G-S1", "G", "S1", "CG-G-"),
    _node("CG-G-S2", "G", "S2", "CG-G-S1"),
    _node("CG-G-E", "G", "E", "CG-G-S2"),
]


def test_a_node_sharing_its_parents_code_starts_no_path_of_its_own():
    _, ancestry = _rows(LIVE_SHAPE)
    to_e = sorted(tuple(r) for r in ancestry if r[1] == "E")
    assert {r[0] for r in to_e} == {"G"}
    assert {r[7] for r in to_e} == {"G/S1/S2/E"}, "one path to E under the one code"
    assert [r[4:6] for r in to_e] == [(1, 3), (2, 3), (3, 3)], to_e
    # S1 1 + S2 2 + E 3: today's walk added S1's and S2's own chains (10 rows).
    assert len(ancestry) == 6, len(ancestry)
    assert {r[0] for r in ancestry} == {"G"}


def test_a_second_link_within_one_code_is_a_second_path_from_the_code_node():
    """S1 also holds E directly: two paths from G to E (G/S1/S2/E and G/S1/E),
    none starting at S1 or S2."""
    links = [_link("G", "S1", "CG-G-"), _link("G", "S2", "CG-G-S1"),
             _link("G", "E", "CG-G-S2"), _link("G", "E", "CG-G-S1")]
    _, ancestry = _rows(LIVE_SHAPE, links)
    ids = {r[7] for r in ancestry if r[1] == "E"}
    assert ids == {"G/S1/S2/E", "G/S1/E"}, ids
    assert all(r[7].startswith("G/") for r in ancestry), sorted({r[7] for r in ancestry})


def test_a_node_with_its_own_code_still_starts_paths():
    """GROUP_EMEA's code differs from GROUP_CORP's, so (GROUP_EMEA, DEMF) rows stay."""
    _, ancestry = _rows()
    assert {(r[0], r[1]) for r in ancestry} == {
        ("GROUP_CORP", "USMF"), ("GROUP_CORP", "JPMF"),
        ("GROUP_CORP", "DEMF"), ("GROUP_CORP", "GBMF"),
        ("GROUP_EMEA", "DEMF"), ("GROUP_EMEA", "GBMF"),
    }
