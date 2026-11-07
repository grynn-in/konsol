"""The ownership graph's edge rule, pure (konsol#117 pair 1; PR #459 re-review
findings A–C).

``ConsolidationGroup.build_rows`` and ``OwnershipPeriod._validate_no_cycle``
both walked the same graph but each built its edges itself, with different
subtleties: ``build_rows`` dropped a node's tree edge only when a submitted
link named the node AND that link's owner was set and existed, while the
validator dropped it for any submitted row naming the node. One function,
``ownership_graph_model.ownership_edges``, now builds the adjacency both read,
so a ring the reconcile would raise on is the ring validate refuses, and
``path_between`` is the walk the validator runs over it in memory.
"""
import importlib.util
import os

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(APP_DIR, "ownership_graph_model.py")


def _model():
    spec = importlib.util.spec_from_file_location("ownership_graph_model_under_test", MODEL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Row(dict):
    """frappe._dict: a dict with attribute read (what get_all returns)."""
    __getattr__ = dict.get


def _node(name, group, entity, parent):
    return _Row(name=name, consolidation_group=group, data_area_id=entity,
                parent_consolidation_group=parent)


def _link(group, entity, owner, **over):
    return _Row(consolidation_group=group, data_area_id=entity, owner_node=owner, **over)


#: G (root) > S (sub-group) > T (entity); G > E (entity).
G, S, T, E = "CG-G-", "CG-S-", "CG-S-T", "CG-G-E"
TREE = [
    _node(G, "G", None, None),
    _node(S, "S", None, G),
    _node(T, "S", "T", S),
    _node(E, "G", "E", G),
]


def test_the_module_is_pure():
    src = open(MODEL).read()
    assert "import frappe" not in src and "clickhouse" not in src.lower()


def test_without_links_every_node_is_owned_by_its_tree_parent():
    edges = _model().ownership_edges(TREE)
    assert edges == {G: [S, E], S: [T], T: [], E: []}


def test_a_link_replaces_the_tree_edge_into_the_node_it_names():
    edges = _model().ownership_edges(TREE, [_link("G", "E", T)])
    assert edges == {G: [S], S: [T], T: [E], E: []}, "E is owned by T now, not by G"


def test_two_links_into_one_node_are_two_owners():
    edges = _model().ownership_edges(TREE, [_link("G", "E", T), _link("G", "E", S)])
    assert edges[T] == [E] and edges[S] == [T, E] and edges[G] == [S]


def test_two_periods_on_one_edge_are_one_link():
    once = _model().ownership_edges(TREE, [_link("G", "E", T)])
    twice = _model().ownership_edges(TREE, [_link("G", "E", T), _link("G", "E", T)])
    assert once == twice


def test_a_link_with_a_blank_or_dangling_owner_keeps_the_tree_edge():
    """What ``build_rows`` always did (a row before task G's patch, or whose
    owner node is gone): the node stays its tree parent's. The validator used
    to drop the tree edge for any submitted row naming the node (finding B)."""
    model = _model()
    plain = model.ownership_edges(TREE)
    for link in (_link("S", "T", None), _link("S", "T", ""), {"consolidation_group": "S", "data_area_id": "T"},
                 _link("S", "T", "CG-GONE-")):
        assert model.ownership_edges(TREE, [link]) == plain, link


def test_a_link_naming_an_unknown_node_is_ignored_not_fatal():
    model = _model()
    assert model.ownership_edges(TREE, [_link("S", "NOPE", G), _link("ZZ", None, G)]) == model.ownership_edges(TREE)


def test_a_blank_entity_is_the_group_node_whatever_its_spelling():
    model = _model()
    expect = model.ownership_edges(TREE, [_link("S", None, E)])
    assert expect[E] == [S] and expect[G] == [E], expect
    assert model.ownership_edges(TREE, [_link("S", "", E)]) == expect


def test_a_tentative_link_adds_its_edge_without_replacing_the_tree_edge():
    """A draft is not yet a link ``build_rows`` reads, so the tree edge into
    the node it names stands; but the draft's own edge is walked, so a ring it
    would close is refused before it is submitted (test_ownership_period pins
    the two cases)."""
    model = _model()
    edges = model.ownership_edges(TREE, tentative=[_link("G", "E", T)])
    assert edges[T] == [E], "the draft's edge is there"
    assert edges[G] == [S, E], "and so is the tree edge into E"
    # a tentative link with a blank or dangling owner adds nothing
    assert model.ownership_edges(TREE, tentative=[_link("G", "E", None), _link("G", "E", "CG-GONE-")]) \
        == model.ownership_edges(TREE)
    # the same edge twice is one edge
    assert model.ownership_edges(TREE, [_link("G", "E", T)], tentative=[_link("G", "E", T)])[T] == [E]


def test_children_are_listed_in_node_order_so_build_rows_enumerates_as_before():
    nodes = list(reversed(TREE))
    assert _model().ownership_edges(nodes)[G] == [E, S]


def test_path_between_names_the_nodes_after_the_start_on_a_path_to_the_target():
    model = _model()
    edges = model.ownership_edges(TREE, [_link("G", "E", T)])   # G > S > T > E
    assert model.path_between(edges, S, E) == [T, E]
    assert model.path_between(edges, G, E) == [S, T, E]
    assert model.path_between(edges, E, G) is None, "no edge leaves E"
    assert model.path_between(edges, T, T) is None, "a node does not reach itself without a ring"


def test_path_between_does_not_spin_on_a_graph_that_already_holds_a_ring():
    model = _model()
    edges = model.ownership_edges(TREE, [_link("G", "E", T), _link("S", None, E)])   # S > T > E > S
    assert model.path_between(edges, S, S) == [T, E, S]
    assert model.path_between(edges, S, G) is None


def test_path_between_tolerates_a_start_outside_the_adjacency():
    assert _model().path_between({}, "CG-NOPE-", G) is None


if __name__ == "__main__":
    pytest.main([__file__])
