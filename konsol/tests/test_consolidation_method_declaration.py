"""Consolidation Method Declaration: the method a group DECLARES for an entity
its ownership links reach by more than one path (konsol#117 pair 1, task D;
design comment section 1, rule table section 2; option A decided by Deepak Pai,
7 Nov 2026).

A child table of Consolidation Group (the doctype standard: repeating rows are
a child table of a parent), on the Policy tab beside the group's other
policy, group nodes only. Rows flow to
``epm_staging.consolidation_method_declarations`` after the commit, once per
transaction, through the one write-through mechanism; reconcile_all repairs
the table like every other.
"""
import ast
import contextlib
import datetime
import importlib.util
import json
import os
import sys
import types

import konsol.method_declaration_model as MD

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "consolidation", "doctype")
CHILD_DIR = os.path.join(DOCTYPE_DIR, "consolidation_method_declaration")
CHILD_JSON = os.path.join(CHILD_DIR, "consolidation_method_declaration.json")
CHILD_PY = os.path.join(CHILD_DIR, "consolidation_method_declaration.py")
GROUP_DIR = os.path.join(DOCTYPE_DIR, "consolidation_group")
GROUP_JSON = os.path.join(GROUP_DIR, "consolidation_group.json")
GROUP_PY = os.path.join(GROUP_DIR, "consolidation_group.py")
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")

CHILD = "Consolidation Method Declaration"
TABLE = "epm_staging.consolidation_method_declarations"
KEY = ("consolidation_method_declarations",)
CHILD_MODULE = ("konsol.consolidation.doctype.consolidation_method_declaration"
                ".consolidation_method_declaration")

#: (fieldname, fieldtype, label, reqd, in_list_view, options) — design §1.
FIELDS = (
    ("data_area_id", "Link", "Entity", 1, 1, "Entity"),
    ("consolidation_method", "Select", "Consolidation Method", 1, 1,
     "full\nproportional\nequity\nnone"),
    ("effective_date", "Date", "Effective Date", 1, 1, None),
    ("end_date", "Date", "End Date", 0, 1, None),
    ("reason", "Small Text", "Reason", 1, 1, None),
)

HELP = {
    "data_area_id": (
        "The entity this group reaches by more than one ownership path. A declaration "
        "for an entity reached by one path is reported by the warehouse build, not "
        "refused: it may be made ahead of a link that arrives later."),
    "consolidation_method": (
        "The method the group applies to this entity where its ownership paths do not "
        "decide it. Proportional is refused until it is implemented (konsol#429)."),
    "effective_date": "The first day this method applies.",
    "end_date": "The last day this method applies. Blank means open-ended.",
    "reason": (
        "Why this method and not the one the percentages suggest: name the arrangement, "
        "the holders and the standard, e.g. \"60% held via two members under a joint "
        "arrangement with the co-venturer; joint control, IFRS 11.7\". Who declared it "
        "and when is in the group's change history."),
}

SECTION_HELP = (
    "Read only where the ownership links alone do not decide an entity's method: "
    "several paths from this group reach it and their weakest links disagree, or none "
    "of them is all full and together they sum past 50%. A declaration for an entity "
    "reached by one path is reported by the warehouse build, not refused.")

GROUP_NODE_ONLY = "eval:doc.is_group && !doc.data_area_id"


def _json(path):
    with open(path) as f:
        return json.load(f)


def _fields(meta):
    return {f["fieldname"]: f for f in meta["fields"]}


# --- the child doctype JSON (doctype standard, 14 Sep 2026) -------------------

def test_child_is_a_child_table_in_consolidation_with_no_permissions_of_its_own():
    meta = _json(CHILD_JSON)
    assert meta["name"] == CHILD and meta["doctype"] == "DocType"
    assert meta["module"] == "Consolidation"
    assert meta["istable"] == 1 and meta.get("editable_grid") == 1
    assert not meta.get("issingle") and not meta.get("is_submittable")
    assert meta.get("permissions", []) == []
    assert os.path.isfile(os.path.join(CHILD_DIR, "__init__.py"))


def test_child_fields_types_labels_reqd_list_view_options_and_order():
    meta = _json(CHILD_JSON)
    assert [f["fieldname"] for f in meta["fields"]] == [f[0] for f in FIELDS]
    fields = _fields(meta)
    for name, ftype, label, reqd, in_list, options in FIELDS:
        f = fields[name]
        assert f["fieldtype"] == ftype, name
        assert f["label"] == label, name
        assert int(f.get("reqd", 0)) == reqd, name
        assert int(f.get("in_list_view", 0)) == in_list, name
        assert f.get("options") == options, name
        assert "default" not in f, (name, "policy is declared, never defaulted")
        assert not f.get("hidden"), name


def test_child_help_text_on_every_field():
    fields = _fields(_json(CHILD_JSON))
    for name, text in HELP.items():
        assert fields[name].get("description") == text, name
    assert "IFRS 11.7" in fields["reason"]["description"]
    assert "konsol#429" in fields["consolidation_method"]["description"]


# --- the parent: a section on the Policy tab, group nodes only ----------------

def test_group_node_carries_the_declarations_in_a_method_declarations_section():
    meta = _json(GROUP_JSON)
    order = [f["fieldname"] for f in meta["fields"]]
    fields = _fields(meta)
    section = fields["method_declarations_section"]
    assert section["fieldtype"] == "Section Break"
    assert section["label"] == "Method Declarations"
    assert section["description"] == SECTION_HELP
    assert section["depends_on"] == GROUP_NODE_ONLY
    table = fields["method_declarations"]
    assert table["fieldtype"] == "Table"
    assert table["options"] == CHILD
    assert table["label"] == "Method Declarations"
    assert table["depends_on"] == GROUP_NODE_ONLY
    assert not table.get("reqd")
    # on the Policy tab, after the accounts, before the tree bookkeeping
    tab, accounts, lft = (order.index(n) for n in ("tab_policy", "acquisition_accounts_section", "lft"))
    assert tab < accounts < order.index("method_declarations_section") < order.index("method_declarations") < lft
    assert meta["track_changes"] == 1, "who declared and when is the parent's change history"


# --- the pure rule module ------------------------------------------------------

def _row(idx, entity="ZZD", method="equity", start="2024-01-01", end=None):
    return types.SimpleNamespace(idx=idx, data_area_id=entity, consolidation_method=method,
                                 effective_date=start, end_date=end)


def test_no_rows_no_problems_and_a_clean_set_passes():
    assert MD.declaration_problems("ZZGRP", None, []) == []
    rows = [_row(1, end="2024-12-31"), _row(2, start="2025-01-01"),   # adjacent, not overlapping
            _row(3, entity="ZZE", method="full")]                     # another entity, open
    assert MD.declaration_problems("ZZGRP", "", rows) == []


def test_a_declaration_on_an_entity_node_is_refused_by_sentence():
    problems = MD.declaration_problems("ZZGRP", "ZZA", [_row(1)])
    assert problems == [
        "This node carries entity ZZA, and a consolidation method is declared on the "
        "group's node without an entity (the one its reporting currency is read from). "
        "Declare it on the node of group ZZGRP that has no entity."]


def test_overlapping_windows_for_one_entity_are_refused_naming_both_rows():
    # open end overlaps a later start
    problems = MD.declaration_problems("ZZGRP", None, [_row(1), _row(2, method="full", start="2025-01-01")])
    assert problems == [
        "Method declarations for ZZD overlap: row 1 (equity, 2024-01-01 to open) and "
        "row 2 (full, 2025-01-01 to open). One method applies to an entity on any date; "
        "end the earlier declaration the day before the later one starts."]
    # a closed end that reaches the next start
    problems = MD.declaration_problems("ZZGRP", None, [
        _row(1, end="2025-01-01"), _row(2, start="2025-01-01", end="2025-12-31")])
    assert len(problems) == 1 and "2024-01-01 to 2025-01-01" in problems[0], problems
    # reported once per overlapping pair, in date order whatever the row order
    problems = MD.declaration_problems("ZZGRP", None, [
        _row(2, start="2025-01-01"), _row(1, end="2025-06-30")])
    assert len(problems) == 1 and problems[0].startswith(
        "Method declarations for ZZD overlap: row 1 (equity, 2024-01-01 to 2025-06-30) and row 2"), problems


def test_proportional_is_refused_the_way_ownership_period_refuses_it():
    """#432's guard on Ownership Period, with the same sentence, until konsol#117
    pair 3 implements proportional consolidation."""
    problems = MD.declaration_problems("ZZGRP", None, [_row(1, method="proportional")])
    assert problems == ["Row 1 (ZZD): " + MD.PROPORTIONAL_REFUSAL]
    assert MD.PROPORTIONAL_REFUSAL == (
        "Proportional consolidation is not implemented (konsol#429): the warehouse would "
        "consolidate this declaration as full with NCI, which is not proportional "
        "consolidation. Use full with NCI, or equity.")


def test_an_end_before_the_start_and_an_unknown_method_are_refused():
    problems = MD.declaration_problems("ZZGRP", None, [_row(1, start="2025-01-01", end="2024-12-31")])
    assert problems == ["Row 1 (ZZD): End Date 2024-12-31 is before Effective Date 2025-01-01."]
    problems = MD.declaration_problems("ZZGRP", None, [_row(1, method="pooling")])
    assert problems == ["Row 1 (ZZD): 'pooling' is not a consolidation method (full, proportional, equity, none)."]


def test_dates_may_arrive_as_dates_or_iso_strings():
    rows = [_row(1, start=datetime.date(2024, 1, 1), end=datetime.date(2024, 12, 31)),
            _row(2, start="2024-06-01")]
    problems = MD.declaration_problems("ZZGRP", None, rows)
    assert len(problems) == 1 and "row 1 (equity, 2024-01-01 to 2024-12-31)" in problems[0]


def test_the_select_offers_exactly_the_rule_modules_methods():
    fields = _fields(_json(CHILD_JSON))
    assert fields["consolidation_method"]["options"].split("\n") == list(MD.METHODS)


# --- loaders: the controllers against a stub frappe ----------------------------

class Refused(Exception):
    pass


def _stub_frappe(logger=None):
    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(
        in_install=False, in_import=False, in_migrate=False, in_patch=False)

    def throw(msg, *a, **k):
        raise Refused(msg)
    frappe.throw = throw
    frappe.msgprint = lambda *a, **k: None
    frappe.publish_realtime = lambda *a, **k: None
    frappe._messages = []
    log = logger or types.SimpleNamespace(
        warning=lambda m, *a, **k: frappe._messages.append(str(m)),
        error=lambda m, *a, **k: frappe._messages.append(str(m)),
        info=lambda m, *a, **k: frappe._messages.append(str(m)),
        exception=lambda m, *a, **k: frappe._messages.append(str(m)))
    frappe.logger = lambda: log
    frappe.log_error = lambda *a, **k: None
    frappe.db = types.SimpleNamespace(exists=lambda *a, **k: None,
                                      table_exists=lambda *a, **k: False,
                                      count=lambda *a, **k: 0)
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = type("Document", (), {})
    base_document = types.ModuleType("frappe.model.base_document")
    base_document.get_controller = lambda dt: frappe._controllers[dt]
    frappe._controllers = {}
    model.document, model.base_document = document, base_document
    frappe.model = model
    utils = types.ModuleType("frappe.utils")
    nested = types.ModuleType("frappe.utils.nestedset")
    nested.NestedSet = type("NestedSet", (document.Document,), {
        "on_update": lambda self, *a, **k: None,
        "on_trash": lambda self, *a, **k: None,
    })
    utils.nestedset = nested
    return frappe, {"frappe": frappe, "frappe.model": model, "frappe.model.document": document,
                    "frappe.model.base_document": base_document, "frappe.utils": utils,
                    "frappe.utils.nestedset": nested}


def _install(stubs):
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    return saved


def _restore(saved):
    for k, v in saved.items():
        if v is None:
            sys.modules.pop(k, None)
        else:
            sys.modules[k] = v


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_clickhouse(frappe, stubs):
    saved = _install(stubs)
    try:
        return _load("konsol_clickhouse_for_declarations", CH_PATH)
    finally:
        _restore(saved)


@contextlib.contextmanager
def _child():
    """The child controller against a stub frappe and the REAL clickhouse
    module (loaded against the same stub). The stubs stay installed for the
    block: clickhouse._controller imports frappe.model.base_document at call
    time (the trap test_write_through_contract.py names)."""
    frappe, stubs = _stub_frappe()
    ch = _load_clickhouse(frappe, stubs)
    stubs["konsol.clickhouse"] = ch
    saved = _install(stubs)
    try:
        yield _load("cmd_under_test", CHILD_PY), frappe, ch
    finally:
        _restore(saved)


def _load_group(ch_stub):
    """The parent controller against a stub frappe; ``ch_stub`` stands in for
    konsol.clickhouse (both the parent's imports and the child's)."""
    frappe, stubs = _stub_frappe()
    stubs["konsol.clickhouse"] = ch_stub
    # The stubs stay installed while the test runs (the parent imports the
    # child controller lazily, in _queue_sync); _unload restores them.
    saved = _install(stubs)
    mod = _load("cg_declarations_under_test", GROUP_PY)
    mod._saved = saved
    return mod, frappe


def _unload(mod):
    sys.modules.pop(CHILD_MODULE, None)   # the parent's lazy import, if it ran
    _restore(mod._saved)


def _ch_stub():
    ch = types.ModuleType("konsol.clickhouse")
    ch.queued, ch.synced = [], []
    ch.after_commit_once = lambda key, fn: ch.queued.append((key, fn))
    ch.sync_doctype_after_commit = lambda dt, table, fm: ch.synced.append((dt, table))
    ch.sync_table = lambda *a, **k: None
    ch.resolve_sync_filters = lambda dt: None
    return ch


def _node(mod, group="ZZGRP", entity="", is_group=1, rows=()):
    d = mod.ConsolidationGroup()
    d.doctype = "Consolidation Group"
    d.name = f"CG-{group}-{entity}"
    d.consolidation_group, d.data_area_id, d.is_group = group, entity, is_group
    d.reporting_currency = "EUR"
    d.ic_difference_account, d.ic_difference_tolerance = "", 0
    d.parent_consolidation_group = None
    d.method_declarations = list(rows)
    d.get = lambda key, default=None: getattr(d, key, default)
    d.get_doc_before_save = lambda: None
    return d


def _refusal(fn):
    try:
        fn()
    except Refused as e:
        return str(e)
    raise AssertionError("not refused")


# --- the parent controller refuses what the rule module names ------------------

def test_validate_refuses_declarations_on_an_entity_node():
    mod, frappe = _load_group(_ch_stub())
    try:
        msg = _refusal(_node(mod, entity="ZZA", is_group=1, rows=[_row(1)]).validate)
        assert msg.startswith("This node carries entity ZZA, and a consolidation method is declared")
        # and a group node with the same rows passes
        _node(mod, rows=[_row(1)]).validate()
    finally:
        _unload(mod)


def test_validate_refuses_overlaps_and_proportional_joining_every_problem():
    mod, frappe = _load_group(_ch_stub())
    try:
        msg = _refusal(_node(mod, rows=[_row(1, method="proportional"), _row(2, start="2025-01-01")]).validate)
        assert "konsol#429" in msg
        assert "Method declarations for ZZD overlap" in msg
        assert "<br>" in msg, "every problem at once, like the policy check"
    finally:
        _unload(mod)


def test_validate_calls_the_declaration_check_once_from_validate():
    with open(GROUP_PY) as f:
        src = f.read()
    body = src.split("    def validate(self):")[1].split("\n    def ")[0]
    assert "self._validate_method_declarations()" in body


# --- the sync: after the commit, once per transaction, the one mechanism -------

def test_the_child_declares_a_computed_staging_table_and_no_gold_table():
    with _child() as (mod, frappe, ch):
        cls = mod.ConsolidationMethodDeclaration
        assert cls.CH_STAGING_TABLE == TABLE
        assert callable(getattr(cls, "resync_staging", None))
        # one table, one writer: no CH_TABLE, so reconcile does not sync it twice
        assert not getattr(cls, "CH_TABLE", None) and not getattr(cls, "CH_FIELD_MAP", None)


def _ddl_columns(table):
    with open(CH_PATH) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == "_REFERENCE_TABLE_DDL"):
            ddl = ast.literal_eval(node.value)[table]
            break
    else:
        raise AssertionError("_REFERENCE_TABLE_DDL not found")
    inside = ddl[ddl.index("(") + 1:ddl.rindex(") ENGINE")]
    return [part.strip() for part in inside.split(", ")]


def test_the_staging_columns_are_the_ddls_in_order_and_a_blank_end_is_the_default():
    """The DDL is task C's, byte-identical to konsolidat's init-db.sql. Live,
    ClickHouse's Date clamps that DEFAULT to 2149-06-06 (measured 7 Nov 2026,
    the same open end epm_staging.ownership_periods holds); dbt reads an open
    end as ``end_date >= period_date``, so the clamp changes no number."""
    with _child() as (mod, frappe, ch):
        columns = _ddl_columns(TABLE)
        assert mod.ConsolidationMethodDeclaration.CH_STAGING_COLUMNS == [
            c.split()[0] for c in columns if not c.startswith("updated_at")]
        assert "end_date Date DEFAULT '9999-12-31'" in columns, columns


def test_build_rows_maps_each_declaration_to_its_nodes_group_code():
    with _child() as (mod, frappe, ch):
        decls = [
            {"parent": "CG-ZZGRP-", "idx": 1, "data_area_id": "ZZD", "consolidation_method": "equity",
             "effective_date": datetime.date(2024, 1, 1), "end_date": None, "reason": "joint control"},
            {"parent": "CG-ZZGRP-", "idx": 2, "data_area_id": "ZZE", "consolidation_method": "full",
             "effective_date": datetime.date(2024, 1, 1), "end_date": datetime.date(2024, 12, 31), "reason": "control"},
            # a row whose node is gone (cannot happen: child rows die with the
            # parent) is skipped, not written under a blank group
            {"parent": "CG-GONE-", "idx": 1, "data_area_id": "ZZF", "consolidation_method": "none",
             "effective_date": datetime.date(2024, 1, 1), "end_date": None, "reason": "x"},
        ]
        rows = mod.ConsolidationMethodDeclaration.build_rows(decls, {"CG-ZZGRP-": "ZZGRP"})
        assert rows == [
            ["ZZGRP", "ZZD", "equity", datetime.date(2024, 1, 1), None, "joint control"],
            ["ZZGRP", "ZZE", "full", datetime.date(2024, 1, 1), datetime.date(2024, 12, 31), "control"],
        ]


def test_resync_staging_reads_through_resolve_sync_filters_and_writes_default_for_a_blank_end():
    """The real clickhouse.sync_table, so what reaches the INSERT is measured:
    a blank end_date is the DEFAULT keyword, the open end the DDL declares."""
    with _child() as (mod, frappe, ch):
        cls = mod.ConsolidationMethodDeclaration
        frappe._controllers[CHILD] = cls
        frappe.get_meta = lambda dt: types.SimpleNamespace(is_submittable=0)
        asked = []

        def get_all(doctype, filters=None, fields=None, pluck=None, limit_page_length=None, **kw):
            asked.append((doctype, filters))
            if doctype == CHILD:
                return [types.SimpleNamespace(**{
                    "parent": "CG-ZZGRP-", "idx": 1, "data_area_id": "ZZD", "consolidation_method": "equity",
                    "effective_date": datetime.date(2024, 1, 1), "end_date": None,
                    "reason": "O'Brien JV", "modified": datetime.datetime(2026, 11, 7, 9, 0, 0)}).__dict__]
            if doctype == "Consolidation Group":
                return [{"name": "CG-ZZGRP-", "consolidation_group": "ZZGRP"}]
            raise AssertionError(doctype)
        frappe.get_all = get_all
        sql = []
        ch.execute = lambda s, params=None: sql.append(s) or ""

        written = cls.resync_staging(force=True)

        assert written == 1
        assert (CHILD, None) in asked, asked   # resolve_sync_filters: not submittable, no declaration -> every row
        inserts = [s for s in sql if s.startswith(f"INSERT INTO {TABLE}_sync_tmp")]
        assert inserts == [
            f"INSERT INTO {TABLE}_sync_tmp (consolidation_group, data_area_id, consolidation_method, "
            "effective_date, end_date, reason) VALUES ('ZZGRP', 'ZZD', 'equity', '2024-01-01', DEFAULT, 'O\\'Brien JV')"]
        assert f"EXCHANGE TABLES {TABLE} AND {TABLE}_sync_tmp" in sql
        # and a filter the controller declared would be the one asked for:
        # the mechanism decides which rows belong, not the call site
        cls.CH_SYNC_FILTERS = {"parenttype": "Consolidation Group"}
        asked.clear()
        cls.resync_staging(force=True)
        assert asked[0] == (CHILD, {"parenttype": "Consolidation Group"}), asked


def test_the_parent_queues_the_declaration_sync_after_the_commit_on_update_and_delete():
    """Through after_commit_once with one key, so a bulk save queues one sync
    per transaction and a rolled-back save publishes nothing (konsol#124)."""
    ch = _ch_stub()
    mod, frappe = _load_group(ch)
    try:
        d = _node(mod, rows=[_row(1)])
        d.on_update()
        keys = [k for k, _fn in ch.queued]
        assert KEY in keys, keys
        fn = dict(ch.queued)[KEY]
        assert fn.__name__ == "resync_staging" and fn.__self__.__name__ == "ConsolidationMethodDeclaration"
        assert ("Consolidation Group", "epm_gold.consolidation_groups") in ch.synced
        ch.queued.clear()
        d.after_delete()
        assert KEY in [k for k, _fn in ch.queued], "a deleted node's declarations leave the warehouse"
    finally:
        _unload(mod)


def test_the_parent_never_syncs_declarations_from_on_trash():
    with open(GROUP_PY) as f:
        src = f.read()
    body = src.split("    def on_trash(self):")[1].split("\n    def ")[0]
    assert "consolidation_method_declarations" not in body and "_queue_sync" not in body


def test_reconcile_all_covers_the_declarations_through_the_child_controller():
    """Discovery finds the child (CH_STAGING_TABLE + resync_staging) and the
    reconcile loop records what it wrote under the table's name."""
    with _child() as (mod, frappe, ch):
        cls = mod.ConsolidationMethodDeclaration
        frappe._controllers = {CHILD: cls}
        frappe.get_meta = lambda dt: types.SimpleNamespace(is_submittable=0)

        def get_all(doctype, filters=None, fields=None, pluck=None, limit_page_length=None, **kw):
            if doctype == "Module Def":
                return ["Consolidation"]
            if doctype == "DocType":
                return [CHILD]
            return []
        frappe.get_all = get_all
        ch.execute = lambda s, params=None: ""
        assert ch._write_through_doctypes() == [CHILD]
        assert ch._reconcile_tables() == {TABLE: 0}