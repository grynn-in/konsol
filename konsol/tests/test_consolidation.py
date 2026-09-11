"""TDD tests for Consolidation Group, IC Elimination Rule, Consolidation Adjustment."""
import ast
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _doctype_file(doctype_dir, ext):
    """Locate a doctype file in whichever konsol module owns it.

    Consolidation doctypes live under konsol/consolidation, not konsol/epm —
    resolve dynamically so the tests survive module reorganisation.
    """
    matches = glob.glob(os.path.join(
        APP_DIR, "*", "doctype", doctype_dir, f"{doctype_dir}.{ext}"))
    return matches[0] if matches else None


def _load_json(doctype_dir):
    with open(_doctype_file(doctype_dir, "json")) as f:
        return json.load(f)


def _load_py(doctype_dir):
    with open(_doctype_file(doctype_dir, "py")) as f:
        return f.read()


# --- Consolidation Group ---

def test_consolidation_group_json_exists():
    assert _doctype_file("consolidation_group", "json") is not None


def test_consolidation_group_has_required_fields():
    meta = _load_json("consolidation_group")
    fields = [f["fieldname"] for f in meta["fields"]]
    for f in ["consolidation_group", "data_area_id", "entity_name",
              "reporting_currency"]:
        assert f in fields, f"Missing field: {f}"


def test_consolidation_group_carries_no_ownership():
    """F2: the tree is STRUCTURE. Ownership is temporal — a group's share of a
    subsidiary changes on a date and a doctype field cannot say when — so it
    lives only in Ownership Period. Keeping a copy here is what gave two grains
    with no rule about which won, and a dbt fallback that read a real 0% as
    'unset'."""
    meta = _load_json("consolidation_group")
    fields = [f["fieldname"] for f in meta["fields"]]
    for gone in ("ownership_pct", "consolidation_method"):
        assert gone not in fields, f"{gone} belongs to Ownership Period now"


def test_consolidation_method_options_live_on_ownership_period():
    """F2 moved consolidation_method off Consolidation Group, which left this
    test looping over fields that no longer contain it — a body that never ran
    and therefore always passed. The option list it guards now belongs to
    Ownership Period, and has a fourth value: gold_entity_ownership ranks the
    chain full < proportional < equity < none and takes the weakest link, so an
    unranked option would silently resolve to the strictest."""
    meta = _load_json("ownership_period")
    field = next(f for f in meta["fields"]
                 if f["fieldname"] == "consolidation_method")
    assert field["options"].split("\n") == ["full", "proportional", "equity", "none"]


def test_consolidation_group_ch_sync():
    content = _load_py("consolidation_group")
    assert "sync_doctype" in content
    assert "gold.consolidation_groups" in content


# --- IC Elimination Rule ---

def test_ic_elimination_rule_json_exists():
    assert _doctype_file("ic_elimination_rule", "json") is not None


def test_ic_elimination_rule_has_required_fields():
    meta = _load_json("ic_elimination_rule")
    fields = [f["fieldname"] for f in meta["fields"]]
    for f in ["rule_id", "rule_name", "debit_account", "credit_account"]:
        assert f in fields, f"Missing field: {f}"


def test_ic_elimination_rule_id_unique():
    meta = _load_json("ic_elimination_rule")
    for field in meta["fields"]:
        if field["fieldname"] == "rule_id":
            assert field.get("unique") == 1


def test_ic_elimination_rule_has_entity_patterns():
    meta = _load_json("ic_elimination_rule")
    fields = [f["fieldname"] for f in meta["fields"]]
    assert "debit_entity_pattern" in fields
    assert "credit_entity_pattern" in fields


def test_ic_elimination_rule_ch_sync():
    """konsolidat#146: the legacy epm_gold write-through is gone — it shared a
    ClickHouse relation with a dbt seed, so the CSV and the doctype overwrote
    each other. Every dbt reader moved to the staging table, which is the richer
    one (the legacy map dropped rule_type, margin_pct and asset_account)."""
    content = _load_py("ic_elimination_rule")
    assert "sync_doctype" in content
    assert 'CH_STAGING_TABLE = "epm_staging.ic_elimination_rules"' in content
    assert "epm_gold.ic_elimination_rules" not in content


# --- Consolidation Adjustment ---

def test_consolidation_adjustment_json_exists():
    assert _doctype_file("consolidation_adjustment", "json") is not None


def test_consolidation_adjustment_has_required_fields():
    meta = _load_json("consolidation_adjustment")
    fields = [f["fieldname"] for f in meta["fields"]]
    for f in ["consolidation_group", "adjustment_type", "journal_id", "data_area_id",
              "fiscal_year", "fiscal_period", "main_account", "debit_amount", "credit_amount"]:
        assert f in fields, f"Missing field: {f}"


def test_consolidation_adjustment_types():
    meta = _load_json("consolidation_adjustment")
    for field in meta["fields"]:
        if field["fieldname"] == "adjustment_type":
            options = field["options"].split("\n")
            assert "topside" in options
            assert "reclassification" in options


def test_consolidation_adjustment_has_posted_by():
    meta = _load_json("consolidation_adjustment")
    fields = [f["fieldname"] for f in meta["fields"]]
    assert "posted_by" in fields


def test_consolidation_adjustment_ch_sync():
    """konsolidat#146: the legacy epm_gold write-through is gone. It carried no
    `status` column and the dbt model labelled everything it read from that
    relation 'Approved' unconditionally, so the approval workflow only ever held
    because the model preferred staging whenever staging was non-empty."""
    content = _load_py("consolidation_adjustment")
    assert "sync_doctype" in content
    assert 'CH_STAGING_TABLE = "epm_staging.consolidation_adjustments"' in content
    assert "epm_gold.consolidation_adjustments" not in content
    staging = content.split("CH_STAGING_FIELD_MAP")[1].split("}")[0]
    assert '"status"' in staging, "the workflow status must reach the warehouse"


def test_all_consolidation_doctypes_module_consolidation():
    for dt in ["consolidation_group", "ic_elimination_rule", "consolidation_adjustment"]:
        meta = _load_json(dt)
        assert meta["module"] == "Consolidation", f"{dt} not in Consolidation module"


# --- F2: Ownership Period is the only ownership grain ----------------------

def _ownership_period_src():
    with open(os.path.join(
            APP_DIR, "consolidation", "doctype", "ownership_period",
            "ownership_period.py")) as f:
        return f.read()


def _code_only(src):
    """Source with every string literal blanked.

    These files explain in prose the very names they removed ("ownership_pct is
    gone", "`or 100` is the bug"), so a plain substring check on the file reads
    the docstring and reports the opposite of the truth.
    """
    import ast

    out = src
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value:
            out = out.replace(node.value, "")
    return out


def test_ownership_period_allows_a_group_node():
    """A sub-group is ownable too — GROUP_CORP's share of GROUP_EMEA is a link
    in every chain below it, and without a period for that node the whole chain
    is unresolvable."""
    meta = _load_json("ownership_period")
    field = next(f for f in meta["fields"] if f["fieldname"] == "data_area_id")
    assert not field.get("reqd"), "a group node has no entity code"


def test_ownership_period_validates_its_node_exists():
    """Ownership is the only grain now, so a period naming a node that does not
    exist is read by nothing — the silent failure this doctype exists to
    remove."""
    src = _ownership_period_src()
    assert "_validate_node_exists" in src
    lookup = src.split("def _node")[1].split("\n    def ")[0]
    assert "Consolidation Group" in lookup
    # a blank Link is NULL: {"data_area_id": ""} would match no group node
    assert "_BLANK" in lookup
    # nobody owns the top of a hierarchy
    guard = src.split("def _validate_node_exists")[1].split("\n    def ")[0]
    assert "parent_consolidation_group" in guard


def test_ownership_period_rejects_dates_clickhouse_would_clamp():
    """ClickHouse Date holds 1970-01-01..2149-06-06 and clamps silently, so the
    document and the warehouse would disagree about when a period starts."""
    src = _ownership_period_src()
    assert "_validate_dates_representable" in src
    assert '_CH_DATE_MIN = "1970-01-01"' in src
    assert '_CH_DATE_MAX = "2149-06-06"' in src


def test_ownership_period_zero_is_a_real_percentage():
    """0% means 0%. Treating it as 'unset' is the falsy-zero bug F2 removes."""
    src = _ownership_period_src()
    body = src.split("def _validate_pct_range")[1].split("\n    def ")[0]
    assert "0 <= float(self.ownership_pct) <= 100" in body


def test_end_date_blank_means_open_not_9999():
    """The old 9999-12-31 default is unrepresentable in ClickHouse; blank says
    the same thing and survives the round trip."""
    meta = _load_json("ownership_period")
    field = next(f for f in meta["fields"] if f["fieldname"] == "end_date")
    assert not field.get("default"), "blank means open"
    assert "_LEGACY_OPEN" in _ownership_period_src(), "old rows must still be editable"


def test_lift_patch_is_registered_and_idempotent():
    """The tree's ownership has to reach Ownership Period before the fields are
    dropped, or every entity consolidates at nothing."""
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        assert "konsol.patches.lift_ownership_to_ownership_period" in f.read()
    with open(os.path.join(
            APP_DIR, "patches", "lift_ownership_to_ownership_period.py")) as f:
        src = f.read()
    # runs pre_model_sync, so the old column is still there to read
    assert 'has_column("Consolidation Group", "ownership_pct")' in src
    assert "frappe.db.sql" in src, "the ORM no longer declares these fields"
    assert "_has_period" in src, "must not double-create on a second migrate"
    # a root has no owner, and a missing percentage is reported, never defaulted
    assert "parent_consolidation_group" in src
    assert "or 100" not in _code_only(src)


def test_lift_patch_drops_the_columns_it_lifted():
    """Frappe never drops a column whose field left the DocType JSON, so the old
    percentages would sit in MariaDB indefinitely — invisible to the ORM,
    readable by raw SQL, and different from a fresh install. The drop is
    conditional: a node that could not be lifted keeps its data recoverable."""
    with open(os.path.join(
            APP_DIR, "patches", "lift_ownership_to_ownership_period.py")) as f:
        src = f.read()
    assert "_drop_lifted_columns" in src
    body = src.split("def _drop_lifted_columns")[1]
    assert "drop column" in body
    for column in ("ownership_pct", "consolidation_method"):
        assert column in body, column
    # the post-condition must return BEFORE dropping
    main = src.split("def execute")[1].split("def _drop_lifted_columns")[0]
    assert main.index("if unlifted:") < main.index("_drop_lifted_columns()")
    assert "return" in main.split("if unlifted:")[1].split("_drop_lifted_columns()")[0]
    # and it must be a real check, not a flag set during the loop
    assert "not _has_period(n)" in main


def test_an_entity_belongs_to_exactly_one_node():
    """Two nodes for one entity used to mean each fed its own group. Since F2 it
    means every shared ancestor gets a chain for the same entity twice, and
    gold_entity_ownership merges the two chains' links into one product."""
    src = _load_py("consolidation_group")
    assert "_validate_entity_in_one_node" in src
    body = src.split("def _validate_entity_in_one_node")[1].split("\n    def ")[0]
    assert '"data_area_id": self.data_area_id' in body
    assert "frappe.throw" in body


def test_a_node_without_ownership_says_so():
    """ownership_pct was reqd on the node, so a link could never lack a
    percentage. A period cannot be required here (the node must exist first), so
    the gap needs a signal at save time rather than only a failed dbt build."""
    src = _load_py("consolidation_group")
    assert "_warn_if_no_ownership_period" in src
    body = src.split("def _warn_if_no_ownership_period")[1].split("\n    def ")[0]
    assert "Ownership Period" in body
    assert "msgprint" in body
    assert "parent_consolidation_group" in body, "a root needs no period"
    assert "in_migrate" in body, "must stay quiet during migrate and fixture import"


def test_resync_reports_a_failed_hierarchy_write():
    """reconcile_all records ONE entry per controller, so returning only the
    ancestry's result would report a clean reconcile on a migrate where the
    hierarchy write was refused."""
    src = _load_py("consolidation_group")
    body = src.split("def resync_staging")[1].split("\n    def ")[0]
    assert "wrote_hierarchy" in body and "wrote_ancestry" in body
    assert "if wrote_hierarchy is None:" in body


def test_ownership_is_not_shipped_as_a_fixture():
    """Fixture sync force-deletes and reinserts every shipped name on every
    migrate, bypassing the submitted-document guard — so shipping ownership
    reverts a user's edit (proven live: 80% -> 65% -> 80% after one migrate) and
    undoes the patch's lifted figures.

    The file must be OUT of konsol/fixtures/, not merely off the `fixtures`
    hook: import_fixtures() imports every .json in that directory whatever the
    hook says (the hook is read only when exporting). Removing the hook entry
    alone was tried and the edit was still reverted.
    """
    assert not os.path.exists(
        os.path.join(APP_DIR, "fixtures", "ownership_period.json")), \
        "everything in fixtures/ is force-reimported on every migrate"
    assert os.path.exists(
        os.path.join(APP_DIR, "demo_data", "ownership_period.json"))

    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        hooks = f.read()
    fixtures = hooks.split("fixtures = [")[1].split("]")[0]
    entries = [line.strip() for line in fixtures.splitlines()
               if line.strip() and not line.strip().startswith("#")]
    assert '"Ownership Period",' not in entries, entries

    with open(os.path.join(APP_DIR, "install.py")) as f:
        install = f.read()
    body = install.split("def _bootstrap_ownership_periods")[1].split("\ndef ")[0]
    assert 'frappe.db.count("Ownership Period")' in body, "only when there are none"
    assert '"demo_data"' in body


def test_nothing_else_ships_transactional_data_as_a_fixture():
    """A guard for the next person: anything dropped into konsol/fixtures/ is
    force-reimported on every migrate, so a doctype users edit does not belong
    there. These are the submittable ones."""
    import json

    fixtures_dir = os.path.join(APP_DIR, "fixtures")
    shipped = set()
    for name in os.listdir(fixtures_dir):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(fixtures_dir, name)) as f:
            rows = json.load(f)
        shipped.update(r.get("doctype") for r in rows if isinstance(r, dict))
    for doctype in ("Ownership Period", "Trial Balance Submission",
                    "Allocation Run", "Historical Equity Rate", "IC Balance",
                    "Consolidation Adjustment"):
        assert doctype not in shipped, (
            f"{doctype} is submittable transactional data; a fixture would "
            f"force-delete and reinsert it on every migrate")


def test_hierarchy_query_api_reads_ownership_from_periods():
    """get_hierarchy_tree is whitelisted and selected ownership_pct /
    consolidation_method straight off Consolidation Group. F2 drops those
    columns, so the endpoint would have thrown MySQL 1054 — on every fresh
    install, where they never existed at all."""
    with open(os.path.join(APP_DIR, "api.py")) as f:
        src = f.read()
    body = src.split("def get_hierarchy_tree")[1].split("\ndef ")[0]
    assert '"ownership_pct",' not in body.split("fields=[")[1].split("]")[0]
    assert '"consolidation_method",' not in body.split("fields=[")[1].split("]")[0]
    assert "_ownership_as_of" in body
    resolver = src.split("def _ownership_as_of")[1].split("\ndef ")[0]
    assert '"docstatus": 1' in resolver, "draft and cancelled periods are not ownership"
    assert "end_date" in resolver, "an expired period is not today's ownership"


def test_ownership_is_seeded_before_the_clickhouse_reconcile():
    """A document saved during migrate never reaches ClickHouse — sync_table
    no-ops while frappe.flags.in_migrate is set. reconcile_all is the one call
    that passes force=True, so anything seeded after it stays in Frappe only.
    Caught by assert_ownership_chain_complete failing on 108 rows."""
    with open(os.path.join(APP_DIR, "install.py")) as f:
        src = f.read()
    after = src.split("def after_migrate")[1].split("\ndef ")[0]
    # code lines only — a comment above the seeding call names the reconcile
    calls = [line.strip() for line in after.splitlines()
             if line.strip() and not line.strip().startswith("#")]
    assert calls.index("_bootstrap_ownership_periods()") < calls.index("_reconcile_clickhouse()")


def test_ic_rules_the_demo_ledger_can_actually_fire_are_shipped():
    """konsolidat#146: the deleted seed carried IC_004 and IC_005, which the
    doctype did not.

    They are not surplus — they are the only two whose accounts exist in the
    demo ledger (4030/5030 and 1100/2010 have trial-balance rows; 1300, 4000,
    5000, 8100 and 3200 do not). And they never fired, because the dbt model
    read the seed only when the staging table happened to be empty. Deleting the
    seed without shipping them would have made that dormancy permanent;
    shipping them turned gold_ic_eliminations from 0 rows into 272.
    """
    import json

    with open(os.path.join(APP_DIR, "fixtures", "ic_elimination_rule.json")) as f:
        rules = {r["rule_id"]: r for r in json.load(f)}
    for rule_id, debit, credit in (("IC_004", "4030", "5030"),
                                   ("IC_005", "1100", "2010")):
        assert rule_id in rules, f"{rule_id} was only ever in the deleted seed"
        assert rules[rule_id]["debit_account"] == debit
        assert rules[rule_id]["credit_account"] == credit
        assert rules[rule_id]["rule_type"] == "balance"
