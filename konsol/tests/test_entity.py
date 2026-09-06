"""Entity — the entity master (architecture review F1).

Until this doctype existed, an entity was a bare string: ``data_area_id`` as a
Data field on six doctypes, with no validation, nothing to link to, nowhere to
record what an entity *is*, and nothing for a Frappe User Permission to point
at — which is why entity-level access control had to be hand-rolled in api.py
and defaults to unrestricted.

Structural tests. The behavioural counterpart is test_entity_bench.py.
"""
import ast
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _doctype_file(doctype_dir, ext):
    matches = glob.glob(os.path.join(
        APP_DIR, "*", "doctype", doctype_dir, f"{doctype_dir}.{ext}"))
    return matches[0] if matches else None


def _json(doctype_dir):
    with open(_doctype_file(doctype_dir, "json")) as f:
        return json.load(f)


def _fields(doctype_dir):
    return {f["fieldname"]: f for f in _json("entity").get("fields", [])}


# ---- the doctype ---------------------------------------------------------

def test_entity_doctype_exists():
    assert _doctype_file("entity", "json") is not None


def test_entity_is_a_tree():
    d = _json("entity")
    assert d["is_tree"] == 1
    assert d["nsm_parent_field"] == "parent_entity"
    assert d["sort_field"] == "lft", "tree views read in nested-set order"


def test_named_by_its_code():
    """The name IS the code, so links read as AMDE rather than a hash."""
    assert _json("entity")["autoname"] == "field:entity_code"


def test_entity_code_is_unique():
    f = _fields("entity")
    assert f["entity_code"]["unique"] == 1
    assert f["entity_code"]["reqd"] == 1


def test_carries_what_an_entity_actually_is():
    """The attributes that had nowhere to live while an entity was a string."""
    f = _fields("entity")
    for name in ("functional_currency", "country", "erp_source", "status", "is_group"):
        assert name in f, f"missing {name}"
    assert f["status"]["options"].split("\n") == ["Active", "Dormant", "Disposed"]


def test_parent_is_the_management_tree_not_ownership():
    """Legal ownership is a DAG and belongs in ownership records, not here."""
    f = _fields("entity")
    assert f["parent_entity"]["fieldtype"] == "Link"
    assert f["parent_entity"]["options"] == "Entity"
    assert "ownership" in f["parent_entity"]["description"].lower()


def test_nested_set_columns_present():
    f = _fields("entity")
    for col in ("lft", "rgt", "old_parent"):
        assert col in f and f[col]["hidden"] == 1


def test_readable_by_every_epm_role():
    """Entity is reference data — everything downstream joins to it."""
    roles = {p["role"]: p for p in _json("entity")["permissions"]}
    for role in ("System Manager", "EPM Admin", "EPM Analyst", "EPM User"):
        assert roles.get(role, {}).get("read") == 1


# ---- the controller ------------------------------------------------------

def _controller():
    with open(_doctype_file("entity", "py")) as f:
        return f.read()


def test_controller_parses_and_extends_nested_set():
    src = _controller()
    ast.parse(src)
    assert "NestedSet" in src


def test_code_is_normalised():
    """It is a join key — case or whitespace drift breaks joins silently."""
    src = _controller()
    assert "strip()" in src and "upper()" in src


def test_guards_self_parenting_and_leaf_parents():
    src = _controller()
    assert "_guard_self_parent" in src
    assert "_guard_leaf_parenting" in src


# ---- the backfill --------------------------------------------------------

def _patch():
    with open(os.path.join(APP_DIR, "patches",
                           "backfill_entities_from_consolidation_group.py")) as f:
        return f.read()


def test_backfill_is_registered():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        assert "backfill_entities_from_consolidation_group" in f.read()


def test_backfill_reloads_the_doctype_first():
    """Patches run pre_model_sync, so without reload_doc the table does not
    exist yet and the backfill is a silent no-op that still records itself as
    run — never firing again."""
    src = _patch()
    assert "reload_doc" in src
    assert src.index("reload_doc") < src.index('table_exists("Entity")')


def test_backfill_does_not_copy_reporting_currency():
    """On a leaf, reporting_currency holds the GROUP's currency, not the
    entity's functional currency. Copying it would put plausible, wrong data
    into the field FX translation is computed from."""
    src = _patch()
    assert "functional_currency" not in src.split('"""')[2], (
        "the backfill must not populate functional_currency"
    )


def test_backfill_is_idempotent():
    src = _patch()
    assert 'frappe.db.exists("Entity"' in src


def test_backfill_preserves_the_tree():
    src = _patch()
    assert "parent_entity" in src
    assert "order_by=\"lft asc\"" in src, "parents must be created before children"
