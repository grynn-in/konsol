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


def test_consolidation_group_method_options():
    meta = _load_json("consolidation_group")
    for field in meta["fields"]:
        if field["fieldname"] == "consolidation_method":
            options = field["options"].split("\n")
            assert "full" in options
            assert "proportional" in options
            assert "equity" in options


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
    content = _load_py("ic_elimination_rule")
    assert "sync_doctype" in content
    assert "gold.ic_elimination_rules" in content


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
    content = _load_py("consolidation_adjustment")
    assert "sync_doctype" in content
    assert "gold.consolidation_adjustments" in content


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
