"""Consolidation Group carries the Consolidation Policy and the declared
accounts (konsolidat#198, design 1 and 1a).

The group root is where policy is configured, with no defaults: the JSON gets
a Consolidation Policy tab (Framework / Goodwill and Costs / Acquisition
Accounts), `goodwill_method` keeps its name and becomes the NCI Measurement,
and the controller refuses an incomplete policy once the group has a Business
Combination and an account that is not a Published leaf of the chart. Every
policy value and account code travels to `epm_gold.consolidation_groups`
through CH_FIELD_MAP, in the DDL's column order (the DDL itself is pinned in
test_write_through_contract.py).
"""
import ast
import contextlib
import importlib.util
import json
import os
import sys
import types

import konsol.consolidation_policy_model as P

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = os.path.join(APP_DIR, "consolidation", "doctype", "consolidation_group")
JSON_PATH = os.path.join(FOLDER, "consolidation_group.json")
PY_PATH = os.path.join(FOLDER, "consolidation_group.py")

ROOT_ONLY = "eval:doc.is_group"

#: The warehouse columns from `nci_measurement` on, in DDL order.
NEW_COLUMNS = (
    "nci_measurement", "accounting_framework", "framework_note", "goodwill_treatment",
    "goodwill_amortisation_years", "acquisition_costs_treatment", "measurement_period",
    "bargain_purchase",
) + P.ACCOUNT_FIELDS

SELECT_OPTIONS = {
    "accounting_framework": "IFRS\nUS GAAP\nLocal",
    "goodwill_method": "partial\nfull",
    "goodwill_treatment": "Impairment only\nAmortise",
    "acquisition_costs_treatment": "Expense\nCapitalise",
    "measurement_period": "Off\n12 months",
    "bargain_purchase": "Recognise gain\nRefuse",
}


def _meta():
    with open(JSON_PATH) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _meta()["fields"]}


def _source():
    with open(PY_PATH) as f:
        return f.read()


def _body(source, name):
    """The text of one method, up to the next def."""
    return source.split(f"def {name}(")[1].split("\n    def ")[0]


# --- the JSON ---------------------------------------------------------------

def test_goodwill_method_keeps_its_name_and_is_the_nci_measurement():
    """The column and the fieldname stay (dbt reads it); the label says what
    it measures, and there is no default — policy is chosen, not assumed."""
    f = _fields()["goodwill_method"]
    assert f["fieldtype"] == "Select"
    assert f["label"] == P.LABELS["goodwill_method"] == "NCI Measurement"
    assert f["options"] == "partial\nfull"
    assert "default" not in f
    # PR #202 review, finding 9: the description names the option as the
    # Select spells it, so "full" here is the value the user picks.
    assert f["description"] == (
        "Partial: NCI at its share of fair-value net assets. Full: NCI at fair value, "
        "goodwill includes its share. Under US GAAP set NCI Measurement to full.")
    assert "set NCI Measurement to full" in f["description"]
    assert "to Full" not in f["description"]


def test_the_policy_lives_in_its_own_tab_in_three_labelled_sections():
    fields = _meta()["fields"]
    order = [f["fieldname"] for f in fields]
    by_name = {f["fieldname"]: f for f in fields}
    tab = by_name["tab_policy"]
    assert tab["fieldtype"] == "Tab Break" and tab["label"] == "Consolidation Policy"
    tab_at = order.index("tab_policy")
    sections = [(order.index(f["fieldname"]), f["label"]) for f in fields
                if f["fieldtype"] == "Section Break" and order.index(f["fieldname"]) > tab_at]
    assert [label for _i, label in sections] == ["Framework", "Goodwill and Costs", "Acquisition Accounts"]
    framework_at, costs_at, accounts_at = [i for i, _l in sections]
    # each field sits in its section, after the tab
    assert framework_at < order.index("accounting_framework") < order.index("framework_note") < costs_at
    for fn in ("goodwill_treatment", "goodwill_amortisation_years", "acquisition_costs_treatment",
               "measurement_period", "bargain_purchase"):
        assert costs_at < order.index(fn) < accounts_at, fn
    for fn in P.ACCOUNT_FIELDS:
        assert order.index(fn) > accounts_at, fn
    # the settings that stay in the Settings section are not in the tab
    for fn in ("goodwill_method", "ic_difference_account", "ic_difference_tolerance"):
        assert order.index(fn) < tab_at, fn


def test_framework_fields():
    fields = _fields()
    fw = fields["accounting_framework"]
    assert fw["fieldtype"] == "Select" and fw["options"] == SELECT_OPTIONS["accounting_framework"]
    assert fw["depends_on"] == ROOT_ONLY
    note = fields["framework_note"]
    assert note["fieldtype"] == "Small Text"
    assert note["mandatory_depends_on"] == 'eval:doc.accounting_framework=="Local"'
    assert note["depends_on"] == ROOT_ONLY


def test_goodwill_and_costs_fields():
    fields = _fields()
    for fn in ("goodwill_treatment", "acquisition_costs_treatment", "measurement_period", "bargain_purchase"):
        assert fields[fn]["fieldtype"] == "Select", fn
        assert fields[fn]["options"] == SELECT_OPTIONS[fn], fn
        assert fields[fn]["depends_on"] == ROOT_ONLY, fn
    years = fields["goodwill_amortisation_years"]
    assert years["fieldtype"] == "Int"
    assert years["mandatory_depends_on"] == 'eval:doc.goodwill_treatment=="Amortise"'
    assert years["depends_on"] == ROOT_ONLY


def test_select_options_are_exactly_the_rule_modules():
    """The UI and the rule module agree on every allowed value, so a value the
    form offers is never one validate() refuses."""
    fields = _fields()
    allowed = {
        "accounting_framework": P.FRAMEWORKS, "goodwill_method": P.NCI,
        "goodwill_treatment": P.GOODWILL_TREATMENTS, "acquisition_costs_treatment": P.COST_TREATMENTS,
        "measurement_period": P.MEASUREMENT_PERIODS, "bargain_purchase": P.BARGAIN,
    }
    for fn, options in allowed.items():
        assert fields[fn]["options"].split("\n") == list(options), fn


def test_the_nine_declared_accounts_link_to_the_chart():
    fields = _fields()
    for fn in P.ACCOUNT_FIELDS:
        f = fields[fn]
        assert f["fieldtype"] == "Link" and f["options"] == "Main Account", fn
        assert f["label"] == P.LABELS[fn], fn
        assert f.get("description"), f"{fn}: say what is posted there"
        assert f["depends_on"] == ROOT_ONLY, fn


def test_the_settlement_account_is_labelled_for_all_deal_cash():
    """The group settles ALL deal cash on `disposal_proceeds_account`
    (disposal proceeds in, acquisition costs out), so the form calls it the
    Deal Settlement Account. The fieldname and warehouse column keep their
    spelling: the DDL is pinned in both repos."""
    f = _fields()["disposal_proceeds_account"]
    assert f["label"] == "Deal Settlement Account" == P.LABELS["disposal_proceeds_account"]
    assert f["description"] == (
        "Where the group settles deal cash: disposal proceeds are debited here "
        "and acquisition costs credited here.")


def test_labels_match_the_rule_module_and_nothing_is_required_or_defaulted():
    """The sentences validate() throws name the fields by these labels. Nothing
    is `reqd`: the policy is required once the group has a deal, which the
    controller enforces; and nothing has a default."""
    fields = _fields()
    for fn in P.POLICY_FIELDS + P.ACCOUNT_FIELDS:
        assert fields[fn]["label"] == P.LABELS[fn], fn
        assert not fields[fn].get("reqd"), fn
        assert "default" not in fields[fn], fn


# --- the controller ---------------------------------------------------------

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
             "frappe.utils.nestedset": nested, "konsol.clickhouse": ch}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("cg_policy_under_test", PY_PATH)
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


def test_ch_field_map_follows_the_ddl_column_order():
    """CH_FIELD_MAP is {warehouse column: doctype field}, in the DDL's order;
    the write-through sends values positionally."""
    items = list(M.ConsolidationGroup.CH_FIELD_MAP.items())
    assert items[:6] == [
        ("consolidation_group", "consolidation_group"), ("data_area_id", "data_area_id"),
        ("entity_name", "entity_name"), ("reporting_currency", "reporting_currency"),
        ("ic_difference_account", "ic_difference_account"),
        ("ic_difference_tolerance", "ic_difference_tolerance"),
    ]
    # konsol#226: the column nci_measurement is read from goodwill_method.
    assert items[6] == ("nci_measurement", "goodwill_method")
    assert items[7:] == [(c, c) for c in NEW_COLUMNS[1:]]
    assert [c for c, _f in items[6:]] == list(NEW_COLUMNS)


def _ddl_columns(table):
    """The column names of one `_REFERENCE_TABLE_DDL` table, read from
    clickhouse.py's source (the dict is a literal; importing the module needs
    frappe and requests)."""
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == "_REFERENCE_TABLE_DDL"):
            ddl = ast.literal_eval(node.value)[table]
            break
    else:
        raise AssertionError("_REFERENCE_TABLE_DDL not found in clickhouse.py")
    inside = ddl[ddl.index("(") + 1:ddl.rindex(") ENGINE")]
    return [part.split()[0] for part in inside.split(",")]


def test_field_map_reads_real_fields_into_real_columns():
    """konsol#226: the sync SELECTs each VALUE from MariaDB and writes it to
    its KEY in ClickHouse, so every value is a doctype field and every key a
    warehouse column. The reversed nci_measurement pair made every sync fail
    with "Unknown column 'nci_measurement'", and reconcile_all swallowed it."""
    fields = set(_fields())
    columns = _ddl_columns(M.ConsolidationGroup.CH_TABLE)
    assert M.ConsolidationGroup.CH_TABLE == "epm_gold.consolidation_groups"
    assert "nci_measurement" in columns and "goodwill_method" in fields
    field_map = M.ConsolidationGroup.CH_FIELD_MAP
    not_fields = [v for v in field_map.values() if v not in fields]
    assert not not_fields, f"CH_FIELD_MAP values that are not doctype fields: {not_fields}"
    not_columns = [k for k in field_map if k not in columns]
    assert not not_columns, f"CH_FIELD_MAP keys that are not warehouse columns: {not_columns}"


def test_validate_runs_the_policy_check_guarded_on_the_deal_table():
    """P3 creates Business Combination later; until then (and on a stack mid
    migrate) the table may not exist, so has_deals is read only when it does."""
    src = _source()
    assert "self._validate_policy()" in _body(src, "validate")
    body = _body(src, "_validate_policy")
    assert "policy_problems(" in body
    assert 'table_exists("Business Combination")' in body
    assert '"Business Combination"' in body and '"docstatus": ["<", 2]' in body


def test_a_disposal_is_a_deal_too_behind_the_same_guard():
    """PR #202 review, finding 8: a group whose only deal is a Business
    Disposal needs its policy (the disposal posts to the declared accounts)
    just as one with a Business Combination does, and the table is read only
    when it exists, for the same migrate reason."""
    body = _body(_source(), "_validate_policy")
    assert 'table_exists("Business Disposal")' in body
    assert '"Business Disposal"' in body


@contextlib.contextmanager
def _facts(deal_table=True, deals=(), accounts=None, disposal_table=True, disposals=()):
    """What the check reads: whether the deal tables exist, which groups have
    a live Business Combination (`deals`) or Business Disposal (`disposals`),
    and the chart (`accounts`: code → (status, is_group); None means the
    chart must not be read)."""
    reads = []
    tables = {"Business Combination": deal_table, "Business Disposal": disposal_table}
    live = {"Business Combination": ("BC", deals), "Business Disposal": ("BD", disposals)}

    def table_exists(name):
        return tables.get(name, True)

    def exists(doctype, filters=None):
        assert doctype in live, doctype
        assert tables[doctype], f"{doctype} was read although its table does not exist"
        assert filters["docstatus"] == ["<", 2], filters
        prefix, groups = live[doctype]
        return f"{prefix}-{filters['consolidation_group']}" if filters["consolidation_group"] in groups else None

    def get_value(doctype, name, fields):
        assert doctype == "Main Account" and accounts is not None, "the chart was read"
        reads.append(name)
        return accounts.get(name)

    M.frappe.db = types.SimpleNamespace(table_exists=table_exists, exists=exists, get_value=get_value)
    yield reads


IFRS_PARTIAL = {
    "accounting_framework": "IFRS",
    "goodwill_method": "partial",
    "goodwill_treatment": "Impairment only",
    "acquisition_costs_treatment": "Expense",
    "measurement_period": "Off",
    "bargain_purchase": "Recognise gain",
}


def _root(is_group=1, data_area_id="", **values):
    d = M.ConsolidationGroup()
    d.consolidation_group = "ZZGRP"
    d.is_group, d.data_area_id = is_group, data_area_id
    for k, v in values.items():
        setattr(d, k, v)
    return d


def _refused(d, needle):
    try:
        d._validate_policy()
    except Refused as e:
        assert needle in str(e), str(e)
        return
    raise AssertionError(f"not refused: {needle}")


def test_an_empty_policy_is_fine_until_the_group_has_a_deal():
    with _facts(deals=()):
        _root()._validate_policy()
    with _facts(deal_table=False):
        _root()._validate_policy()
    with _facts(deals={"ZZGRP"}):
        _refused(_root(), "Consolidation Policy: Accounting Framework is required once the group has a Business Combination")


def test_a_group_with_only_a_disposal_needs_its_policy_too():
    """Finding 8: `has_deals` counts Business Disposal rows as well."""
    with _facts(deals=(), disposals={"ZZGRP"}):
        _refused(_root(), "Consolidation Policy: Accounting Framework is required")
    # another group's disposal is not this group's deal
    with _facts(deals=(), disposals={"ZZOTHER"}):
        _root()._validate_policy()
    # the disposal table may not exist yet (mid migrate): not read, not a deal
    with _facts(deals=(), disposal_table=False, disposals={"ZZGRP"}):
        _root()._validate_policy()
    # neither table yet
    with _facts(deal_table=False, disposal_table=False, deals={"ZZGRP"}, disposals={"ZZGRP"}):
        _root()._validate_policy()
    # a complete policy passes whichever kind of deal the group has
    with _facts(deals=(), disposals={"ZZGRP"}, accounts=None):
        _root(**IFRS_PARTIAL)._validate_policy()


def test_the_framework_constrains_the_choices():
    with _facts():
        _refused(_root(**dict(IFRS_PARTIAL, accounting_framework="US GAAP")),
                 "US GAAP measures non-controlling interest at fair value")
        _refused(_root(**dict(IFRS_PARTIAL, goodwill_treatment="Amortise")), "IFRS does not amortise goodwill")
        _refused(_root(**dict(IFRS_PARTIAL, accounting_framework="Local")), "must carry a Framework Note")
        _root(**dict(IFRS_PARTIAL, accounting_framework="US GAAP", goodwill_method="full"))._validate_policy()


def test_a_set_account_must_be_a_published_leaf_of_the_chart():
    chart = {"1800": ("Published", 0), "1000": ("Published", 1), "1900": ("Draft", 0)}
    with _facts(accounts=chart) as reads:
        _root(**IFRS_PARTIAL, goodwill_account="1800")._validate_policy()
        assert reads == ["1800"]
        _refused(_root(**IFRS_PARTIAL, goodwill_account="1000"),
                 'Goodwill Account "1000" is not a Published leaf')
        _refused(_root(**IFRS_PARTIAL, nci_account="1900"),
                 'Non-controlling Interest Account "1900" is not a Published leaf')
        _refused(_root(**IFRS_PARTIAL, investment_account="9999"),
                 'Investment in Subsidiaries Account "9999" is not a Published leaf')
    # blank accounts are not looked up: they become required only per deal
    with _facts(accounts=None):
        _root(**IFRS_PARTIAL)._validate_policy()


def test_only_a_group_node_carries_a_policy():
    """The fields are shown on group nodes only (depends_on is_group); an
    entity leaf is not checked, whatever the deal table says."""
    with _facts(deals={"ZZGRP"}, accounts=None):
        _root(is_group=0, data_area_id="ZZA")._validate_policy()
