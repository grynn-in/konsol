"""Structural tests for Reporting Hierarchy doctypes + seed regeneration."""
import json
import ast
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SEED_COLUMNS = [
    "hierarchy_name", "dimension", "member_code", "member_label",
    "parent_member_code", "is_group", "hierarchy_level", "path",
    "effective_from", "effective_to", "is_default", "status",
]


def _doctype_json(name):
    with open(os.path.join(APP_DIR, "epm", "doctype", name, f"{name}.json")) as f:
        return json.load(f)


def _read(rel):
    with open(os.path.join(APP_DIR, rel)) as f:
        return f.read()


def test_reporting_hierarchy_doctype():
    meta = _doctype_json("reporting_hierarchy")
    assert meta["module"] == "EPM"
    assert meta["autoname"] == "field:hierarchy_name"
    fields = {f["fieldname"] for f in meta["fields"]}
    for f in ["hierarchy_name", "dimension", "label", "status", "is_default"]:
        assert f in fields


def test_reporting_hierarchy_member_doctype():
    meta = _doctype_json("reporting_hierarchy_member")
    assert meta["module"] == "EPM"
    fields = {f["fieldname"] for f in meta["fields"]}
    for f in ["reporting_hierarchy", "parent_member", "member_code", "is_group"]:
        assert f in fields


def test_reporting_hierarchy_member_carries_its_dates():
    """konsol#220: a member row is one dated tranche of its code."""
    meta = _doctype_json("reporting_hierarchy_member")
    by_name = {f["fieldname"]: f for f in meta["fields"]}
    assert "effective_from" in by_name
    assert "effective_to" in by_name
    frm, to = by_name["effective_from"], by_name["effective_to"]
    assert frm["fieldtype"] == "Date"
    assert to["fieldtype"] == "Date"
    assert frm["label"] == "Effective From"
    assert to["label"] == "Effective To"
    assert frm.get("reqd") == 1
    assert not to.get("reqd")
    assert frm.get("in_list_view") == 1
    assert to.get("in_list_view") == 1
    assert frm["description"] == (
        "First day this node, with this parent and label, applies."
    )
    assert to["description"] == (
        "Last day it applies; leave blank while it still applies. A rename, "
        "a move to another parent or an end is a new row with the same Member Code."
    )


def test_header_publish_resyncs_staging_and_reporting_rebuild():
    src = _read(os.path.join("epm", "doctype", "reporting_hierarchy", "reporting_hierarchy.py"))
    # F3: publish re-syncs epm_staging via the computed resync_staging()
    # pattern (rows are flattened, not field-mapped); no CSV seed is written.
    # The publish/unpublish lifecycle itself is the shared one; this doctype
    # supplies the computed _resync() and its own build scope.
    assert "class ReportingHierarchy(GovernedReferenceDocument)" in src
    assert "def resync_staging" in src
    assert "def _resync" in src
    assert 'CH_STAGING_TABLE = "epm_staging.reporting_hierarchies"' in src
    assert "flatten_reporting_hierarchies" in src
    assert "regenerate_reporting_hierarchies_seed" not in src
    assert "BUILD_SCOPE = _REPORTING_BUILD_SCOPE" in src
    assert '_REPORTING_BUILD_SCOPE = "reporting"' in src


def test_resync_staging_returns_what_was_written():
    """It used to return len(data) unconditionally, so a failed ClickHouse
    write still reported a row count and reconcile logged a clean repair with
    no watermark behind it. Return sync_table's own result."""
    src = _read(os.path.join("epm", "doctype", "reporting_hierarchy", "reporting_hierarchy.py"))
    body = src.split("def resync_staging")[1].split("\n    def ")[0]
    assert "return sync_table(" in body
    assert "return len(data)" not in body


def test_staging_columns_match_the_flattened_contract():
    """F3: the columns dbt reads now travel via CH_STAGING_COLUMNS."""
    src = _read(os.path.join("epm", "doctype", "reporting_hierarchy", "reporting_hierarchy.py"))
    for col in SEED_COLUMNS:
        assert f'"{col}"' in src, f"column {col} missing from CH_STAGING_COLUMNS"


def test_flatten_module_exists():
    src = _read("reporting_hierarchy_seed.py")
    assert "def flatten_reporting_hierarchies" in src


def test_tasks_reporting_scope():
    src = _read("tasks.py")
    assert '"reporting": "+tag:domain:reporting"' in src
    assert '"reporting"' in src or "'reporting'" in src


def test_api_get_reporting_hierarchy_tree():
    src = _read("api.py")
    assert "def get_reporting_hierarchy_tree" in src


def test_build_models_include_hierarchy_v2():
    models = json.load(open(os.path.join(APP_DIR, "fixtures", "build_model.json")))
    names = {m["model_name"] for m in models}
    for model in (
        "gold_tb_at_hierarchy_node",
        "gold_budget_at_hierarchy_node",
        "gold_variance_at_hierarchy_node",
    ):
        assert model in names


def test_reporting_hierarchy_is_not_a_fixture():
    """MGMT_DEMO was the Contoso demo's management tree. Hierarchies are built
    per site, and a fixture would force-reimport the demo's over them."""
    src = _read("hooks.py")
    node = next(n for n in ast.parse(src).body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "fixtures" for t in n.targets))
    entries = [f'"{e if isinstance(e, str) else e.get("dt")}",' for e in ast.literal_eval(node.value)]
    for doctype in ("Reporting Hierarchy", "Reporting Hierarchy Member"):
        assert f'"{doctype}",' not in entries, doctype


def test_reporting_hierarchy_seed_unit():
    from konsol.reporting_hierarchy_seed import _ancestor_chain

    members = {
        "root": type("M", (), {"parent_member": None})(),
        "child": type("M", (), {"parent_member": "root"})(),
    }
    chain = _ancestor_chain(members["child"], members)
    assert chain == ["root"]

# --- konsol#220 row R3: dated tranches of a member code -------------------

class _Refused(Exception):
    pass


def _dated_controller(rows):
    """Load the member controller against a stub frappe whose table is
    ``rows`` (dicts with name, reporting_hierarchy, member_code,
    parent_member, effective_from, effective_to). Loaded under a private
    module name so the real module in sys.modules is never replaced."""
    import importlib.util
    import sys
    import types

    table = {r["name"]: r for r in rows}

    def _match(row, filters):
        for key, want in (filters or {}).items():
            if isinstance(want, list) and want[0] == "!=":
                if row.get(key) == want[1]:
                    return False
            elif row.get(key) != want:
                return False
        return True

    def get_all(doctype, filters=None, fields=None, pluck=None, **_kw):
        hits = [r for r in table.values() if _match(r, filters)]
        if pluck:
            return [r.get(pluck) for r in hits]
        names = fields or ["name"]
        return [types.SimpleNamespace(**{f: r.get(f) for f in names}) for r in hits]

    def exists(doctype, filters):
        hits = [r["name"] for r in table.values() if _match(r, filters)]
        return hits[0] if hits else None

    def get_value(doctype, name, field):
        row = table.get(name)
        return row.get(field) if row else None

    def throw(msg, *a, **k):
        raise _Refused(msg)

    document = types.ModuleType("frappe.model.document")
    document.Document = object
    fake = types.ModuleType("frappe")
    fake.db = types.SimpleNamespace(exists=exists, get_value=get_value)
    fake.get_all = get_all
    fake.throw = throw
    fake.scrub = lambda s: s.strip().lower().replace(" ", "_")
    mods = {"frappe": fake, "frappe.model": types.ModuleType("frappe.model"),
            "frappe.model.document": document}
    path = os.path.join(APP_DIR, "epm", "doctype", "reporting_hierarchy_member",
                        "reporting_hierarchy_member.py")
    spec = importlib.util.spec_from_file_location("_stub_rh_member_dated", path)
    mod = importlib.util.module_from_spec(spec)
    saved = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    try:
        spec.loader.exec_module(mod)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    mod.frappe = fake
    return mod


def _row(name, code, frm, to=None, parent=None, is_group=1):
    return {"name": name, "reporting_hierarchy": "ZZ_MGMT", "member_code": code,
            "member_label": code, "is_group": is_group, "parent_member": parent,
            "effective_from": frm, "effective_to": to}


def _save(existing, new):
    """Run validate() on ``new`` against ``existing`` rows; the refusal
    message, or None when it is accepted."""
    mod = _dated_controller(existing)
    doc = mod.ReportingHierarchyMember.__new__(mod.ReportingHierarchyMember)
    doc.__dict__.update(new)
    try:
        doc.validate()
    except _Refused as e:
        return str(e)
    return None


def test_member_to_before_from_is_refused():
    err = _save([], _row("n1", "ZZ_A", "2025-01-01", "2024-12-31"))
    assert err == "Effective To is before Effective From."


def test_member_same_day_window_is_accepted():
    assert _save([], _row("n1", "ZZ_A", "2025-01-01", "2025-01-01")) is None


def test_overlapping_tranches_of_one_code_are_refused():
    old = _row("old1", "ZZ_A", "2017-01-01", "2025-06-30")
    err = _save([old], _row("n1", "ZZ_A", "2025-01-01"))
    assert err is not None
    assert "Member code 'ZZ_A' already has a row covering" in err
    assert "2017-01-01" in err and "2025-06-30" in err
    assert "(old1)" in err
    assert "end the old row the day before the new one starts" in err


def test_open_tranche_overlaps_any_later_one():
    old = _row("old1", "ZZ_A", "2017-01-01")  # open end = 2999-12-31
    err = _save([old], _row("n1", "ZZ_A", "2030-01-01", "2030-12-31"))
    assert err is not None and "(old1)" in err


def test_adjacent_tranches_of_one_code_are_accepted():
    """A rename: ZZ_A ends 2024-12-31, a new row of ZZ_A starts 2025-01-01."""
    old = _row("old1", "ZZ_A", "2017-01-01", "2024-12-31")
    assert _save([old], _row("n1", "ZZ_A", "2025-01-01")) is None


def test_resaving_a_tranche_does_not_overlap_itself():
    row = _row("old1", "ZZ_A", "2017-01-01")
    assert _save([row], dict(row, member_label="ZZ A renamed")) is None


def test_same_code_in_another_hierarchy_is_not_an_overlap():
    other = dict(_row("x1", "ZZ_A", "2017-01-01"), reporting_hierarchy="ZZ_OTHER")
    assert _save([other], _row("n1", "ZZ_A", "2017-01-01")) is None


def test_child_inside_its_parent_window_is_accepted():
    parent = _row("p1", "ZZ_E", "2017-01-01", "2024-12-31")
    child = _row("c1", "ZZ_EX", "2017-01-01", "2024-12-31", parent="p1", is_group=0)
    assert _save([parent], child) is None


def test_child_outliving_its_parent_is_refused_naming_the_gap():
    parent = _row("p1", "ZZ_E", "2017-01-01", "2024-12-31")
    child = _row("c1", "ZZ_EX", "2017-01-01", None, parent="p1", is_group=0)
    err = _save([parent], child)
    assert err is not None
    assert err.startswith("ZZ_EX applies from 2017-01-01 to ")
    assert "but its parent ZZ_E does not cover 2025-01-01" in err


def test_child_starting_before_its_parent_is_refused_naming_the_gap():
    parent = _row("p1", "ZZ_E", "2018-01-01")
    child = _row("c1", "ZZ_EX", "2017-01-01", "2019-12-31", parent="p1", is_group=0)
    err = _save([parent], child)
    assert err is not None
    assert "but its parent ZZ_E does not cover 2017-01-01 to 2017-12-31" in err


def test_parent_covered_by_two_adjacent_tranches_is_accepted():
    """The parent was renamed in 2025: two rows of ZZ_E together cover the
    child's window, whichever of them the child links."""
    p_old = _row("p1", "ZZ_E", "2017-01-01", "2024-12-31")
    p_new = _row("p2", "ZZ_E", "2025-01-01")
    child = _row("c1", "ZZ_EX", "2017-01-01", None, parent="p1", is_group=0)
    assert _save([p_old, p_new], child) is None


def test_parent_tranches_with_a_hole_are_refused():
    p_old = _row("p1", "ZZ_E", "2017-01-01", "2020-12-31")
    p_new = _row("p2", "ZZ_E", "2022-01-01")
    child = _row("c1", "ZZ_EX", "2017-01-01", None, parent="p2", is_group=0)
    err = _save([p_old, p_new], child)
    assert err is not None
    assert "does not cover 2021-01-01 to 2021-12-31" in err
