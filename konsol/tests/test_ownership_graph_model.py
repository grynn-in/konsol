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
    import ast
    tree = ast.parse(open(MODEL).read())
    imported = [n.names[0].name if isinstance(n, ast.Import) else n.module
                for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert imported == [], imported


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


# --- when a link's end is an exit (konsolidat#335 option A, review F6) -------
#
# The warehouse (konsolidat macros/ownership_exits.sql) posts an exit when the
# entity is no longer held, through a live method, the day after the end, and
# no full Business Disposal ended the link. konsol refuses the end on the same
# rule, not on whether some successor names this period in ``supersedes``.

import datetime as _dt

_END = _dt.date(2025, 6, 30)


def _ending(method="full", is_disposal=0, end=_END):
    return _Row(end_date=end, consolidation_method=method, is_disposal=is_disposal)


def _held(start, end=None, method="full"):
    return _Row(effective_date=start, end_date=end, consolidation_method=method)


def _sale(date=_END, retained=0.0):
    return _Row(disposal_date=date, retained_interest_pct=retained)


def test_an_end_nothing_follows_is_an_exit():
    assert _model().end_is_exit(_ending(), [], []) is True


def test_an_open_ended_link_is_not_an_exit():
    assert _model().end_is_exit(_ending(end=None), [], []) is False


def test_a_an_owner_transfer_is_not_an_exit():
    """(a) A's link ends, B's starts the next day: the entity stays held."""
    assert _model().end_is_exit(_ending(), [_held(_dt.date(2025, 7, 1))], []) is False


def test_b_a_contiguous_period_without_supersedes_is_not_an_exit():
    """(b) the rule reads dates, never ``supersedes``: an open-ended or a
    later-ending period effective the day after keeps the entity."""
    m = _model()
    assert m.end_is_exit(_ending(), [_held(_dt.date(2025, 7, 1), _dt.date(2026, 6, 30))], []) is False
    # a period that started earlier and runs on past the end holds it too
    assert m.end_is_exit(_ending(), [_held(_dt.date(2024, 1, 1))], []) is False


def test_c_another_path_keeping_the_entity_is_not_an_exit():
    """(c) one owner's link ends while another owner's equity link runs on."""
    assert _model().end_is_exit(_ending(), [_held(_dt.date(2023, 1, 1), method="equity")], []) is False


def test_d_a_link_with_method_none_ending_is_not_an_exit():
    """(d) the entity was not live through it, so nothing is derecognised."""
    assert _model().end_is_exit(_ending(method="none"), [], []) is False


def test_e_a_successor_with_method_none_is_an_exit():
    """(e) deemed loss of control: held, but no longer live."""
    assert _model().end_is_exit(_ending(), [_held(_dt.date(2025, 7, 1), method="none")], []) is True


def test_f_a_gap_or_an_earlier_end_is_an_exit():
    """(f) whatever a successor is named (an amendment renames it), only its
    dates count: one starting two days later, or ending on the same day, does
    not hold the entity the day after."""
    m = _model()
    assert m.end_is_exit(_ending(), [_held(_dt.date(2025, 7, 2))], []) is True
    assert m.end_is_exit(_ending(), [_held(_dt.date(2024, 1, 1), _END)], []) is True


def test_g_is_disposal_without_a_full_business_disposal_is_an_exit():
    """(g) the warehouse treats a link as ended by a sale only when it is
    flagged is_disposal AND a full Business Disposal (retained 0) of the
    entity is dated on its end."""
    m = _model()
    assert m.end_is_exit(_ending(is_disposal=1), [], []) is True
    assert m.end_is_exit(_ending(is_disposal=1), [], [_sale(retained=20.0)]) is True
    assert m.end_is_exit(_ending(is_disposal=1), [], [_sale(date=_dt.date(2025, 6, 29))]) is True
    assert m.end_is_exit(_ending(is_disposal=0), [], [_sale()]) is True
    assert m.end_is_exit(_ending(is_disposal=1), [], [_sale()]) is False
    assert m.end_is_exit(_ending(is_disposal=1), [], [_sale(retained=None)]) is False


# --- a re-entry is an acquisition (konsolidat#335 review F4, X14) ------------
#
# After the entity left the group (an exit journal, or a full Business
# Disposal) nothing brings its balances back, so a live period that starts
# after a gap is an acquisition (IFRS 3) and needs a submitted Business
# Combination dated on its start.

def _new(start=_dt.date(2026, 1, 1), method="full"):
    return _Row(effective_date=start, consolidation_method=method)


def _gone(end=_END, is_disposal=0, method="full"):
    return _Row(effective_date=_dt.date(2023, 1, 1), end_date=end,
                consolidation_method=method, is_disposal=is_disposal)


def test_x14_a_re_entry_after_an_exit_without_a_combination_is_refused():
    assert _model().reentry_after_exit(_new(), [_gone()], [], []) == (_END, True)


def test_x14_a_combination_dated_on_the_start_allows_it():
    assert _model().reentry_after_exit(_new(), [_gone()], [], [_dt.date(2026, 1, 1)]) is None
    # one dated on another day does not
    assert _model().reentry_after_exit(_new(), [_gone()], [], [_dt.date(2025, 12, 31)]) == (_END, True)


def test_x14_a_contiguous_successor_is_no_re_entry():
    m = _model()
    assert m.reentry_after_exit(_new(_dt.date(2025, 7, 1)), [_gone()], [], []) is None
    # another path still holds the entity the day before
    held = _Row(effective_date=_dt.date(2023, 1, 1), end_date=None, consolidation_method="equity", is_disposal=0)
    assert m.reentry_after_exit(_new(), [_gone(), held], [], []) is None


def test_x14_a_first_entry_or_a_non_live_period_is_no_re_entry():
    m = _model()
    assert m.reentry_after_exit(_new(), [], [], []) is None
    assert m.reentry_after_exit(_new(), [_gone(method="none")], [], []) is None
    assert m.reentry_after_exit(_new(method="none"), [_gone()], [], []) is None


def test_x14_a_re_entry_after_a_full_disposal_is_also_an_acquisition():
    """Decided consistently: the entity was sold, buying it again is an
    acquisition. The answer says it was a disposal, not an exit."""
    assert _model().reentry_after_exit(
        _new(), [_gone(is_disposal=1)], [_sale()], []) == (_END, False)
