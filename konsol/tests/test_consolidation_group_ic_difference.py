"""Consolidation Group's intercompany-difference settings (konsol#159).

Executes _validate_ic_difference against stubbed frappe/konsol modules:
- the group-node definition konsol#172 uses (is_group, or no entity);
- the account must be in the group chart (#173 review, A3), checked only
  when it changes;
- the account may not itself be intercompany;
- a negative tolerance is refused.
"""
import contextlib
import importlib.util
import os
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(APP_DIR, "consolidation", "doctype", "consolidation_group", "consolidation_group.py")


class Refused(Exception):
    pass


def _load():
    frappe = types.ModuleType("frappe")

    def throw(msg, *a, **k):
        raise Refused(msg)

    frappe.throw = throw
    nested = types.ModuleType("frappe.utils.nestedset")
    nested.NestedSet = type("NestedSet", (), {})
    ch = types.ModuleType("konsol.clickhouse")
    ch.after_commit_once = ch.sync_doctype_after_commit = ch.sync_table = lambda *a, **k: None
    stubs = {"frappe": frappe, "frappe.utils": types.ModuleType("frappe.utils"),
             "frappe.utils.nestedset": nested, "konsol": types.ModuleType("konsol"), "konsol.clickhouse": ch}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("cg_under_test", PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


M = _load()


@contextlib.contextmanager
def _facts(ic=(), chart=None):
    """The two modules the validator imports lazily. chart=None: reading the
    chart is a failure (it must not be read)."""
    reads = []
    ica = types.ModuleType("ica_stub")
    ica.intercompany_accounts = lambda: set(ic)
    tb = types.ModuleType("tb_bulk_stub")

    def chart_accounts():
        reads.append(1)
        assert chart is not None, "the chart was read although the account did not change"
        return set(chart)

    tb._chart_accounts = chart_accounts
    names = {"konsol.consolidation.doctype.intercompany_account.intercompany_account": ica, "konsol.tb_bulk": tb}
    saved = {k: sys.modules.get(k) for k in names}
    sys.modules.update(names)
    try:
        yield reads
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _node(is_group, data_area_id, account="", tolerance=0, before_account=None):
    d = M.ConsolidationGroup()
    d.is_group, d.data_area_id = is_group, data_area_id
    d.ic_difference_account, d.ic_difference_tolerance = account, tolerance
    d.get_doc_before_save = lambda: (None if before_account is None
                                     else types.SimpleNamespace(ic_difference_account=before_account))
    return d


def _refused(d, needle):
    try:
        d._validate_ic_difference()
    except Refused as e:
        assert needle in str(e), str(e)
        return
    raise AssertionError(f"not refused: {needle}")


def test_a_leaf_books_no_differences():
    with _facts(chart={"2100"}):
        _refused(_node(0, "ZZA", "2100"), "Only a group node")
        _refused(_node(0, "ZZA", "", tolerance=5), "Only a group node")
        _node(0, "ZZA")._validate_ic_difference()   # nothing set: fine


def test_settings_live_on_the_group_node_without_an_entity():
    """A group node (konsol#172's _is_group_node: is_group, or no entity)
    carries them, but only on the row with no entity: gold_ic_reconciliation
    reads data_area_id = '' only, so on a node that also carries an entity
    they would be ignored silently (#173 re-review)."""
    with _facts(chart={"2100"}) as reads:
        _node(1, "", " 2100 ", tolerance=5)._validate_ic_difference()
        _node(0, "", "2100")._validate_ic_difference()
        assert len(reads) == 2
        _refused(_node(1, "ZZX", "2100"), "carries entity ZZX")
        _refused(_node(1, "ZZX", "", tolerance=5), "carries entity ZZX")
        _node(1, "ZZX")._validate_ic_difference()   # nothing set: fine


def test_the_account_must_be_in_the_group_chart_when_it_changes():
    with _facts(chart={"2100"}):
        _refused(_node(1, "", "9999"), "9999 is not in the group chart")
        _refused(_node(1, "", "9999", before_account="2100"), "not in the group chart")
    # unchanged: the chart (ClickHouse) is not read, so an outage blocks nothing
    with _facts(chart=None) as reads:
        _node(1, "", "2100", before_account="2100")._validate_ic_difference()
        assert reads == []


def test_an_intercompany_account_or_a_negative_tolerance_is_refused():
    with _facts(ic={"4030", "5030"}, chart={"2100", "4030"}):
        _refused(_node(1, "", "4030"), "is an Intercompany Account")
        _refused(_node(1, "", "2100", tolerance=-1), "cannot be negative")
