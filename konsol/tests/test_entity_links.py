"""data_area_id is a Link to Entity (architecture review F1, second increment).

Six doctypes carried the entity as a bare Data string. Every one was already
*labelled* "Entity" — the intent was never in doubt, only the enforcement. They
are Links now, which buys referential integrity, a picker instead of free text,
and the thing that mattered most: a target for Frappe User Permissions, so
entity-level access stops being hand-rolled in api.py.

The fieldname stays `data_area_id`. It is the warehouse join key, it appears
214 times across konsol and 412 across the dbt project, and renaming it would
buy nothing — an Entity is named by its code, so the stored value is byte-for-
byte what it always was. Only the type moved.
"""
import glob
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

REFERRING_DOCTYPES = {
    "Consolidation Group",
    "Ownership Period",
    "Budget Sheet",
    "Consolidation Adjustment",
    "Historical Equity Rate",
    "Allocation Driver",
}


def _doctypes_with_data_area():
    found = {}
    for path in glob.glob(os.path.join(APP_DIR, "*", "doctype", "*", "*.json")):
        try:
            with open(path) as f:
                d = json.load(f)
        except Exception:
            continue
        if d.get("doctype") != "DocType":
            continue
        for field in d.get("fields", []):
            if field.get("fieldname") == "data_area_id":
                found[d["name"]] = field
    return found


def test_every_data_area_field_is_a_link_to_entity():
    fields = _doctypes_with_data_area()
    assert fields, "no doctype carries data_area_id — did the fieldname change?"
    for doctype, field in sorted(fields.items()):
        assert field["fieldtype"] == "Link", f"{doctype}.data_area_id is still {field['fieldtype']}"
        assert field["options"] == "Entity", f"{doctype}.data_area_id points at {field.get('options')!r}"


def test_the_expected_doctypes_are_covered():
    """A new doctype carrying an entity should join this list deliberately."""
    assert set(_doctypes_with_data_area()) == REFERRING_DOCTYPES


def test_fieldname_is_unchanged():
    """It is the warehouse join key. Renaming it would touch 214 references in
    konsol and 412 in the dbt project, and change nothing that matters."""
    for doctype, field in _doctypes_with_data_area().items():
        assert field["fieldname"] == "data_area_id"


def test_required_flags_are_preserved():
    """Consolidation Group's roll-up nodes have no entity; the rest require one."""
    fields = _doctypes_with_data_area()
    assert fields["Consolidation Group"].get("reqd", 0) == 0
    for doctype in REFERRING_DOCTYPES - {"Consolidation Group"}:
        assert fields[doctype].get("reqd") == 1, f"{doctype}.data_area_id should stay required"


# ---- the safety patch ----------------------------------------------------

def _patch_src():
    with open(os.path.join(APP_DIR, "patches", "ensure_entities_for_data_areas.py")) as f:
        return f.read()


def test_orphan_sweep_is_registered_after_the_backfill():
    with open(os.path.join(APP_DIR, "patches.txt")) as f:
        lines = [l.strip() for l in f if l.strip()]
    backfill = lines.index("konsol.patches.backfill_entities_from_consolidation_group")
    sweep = lines.index("konsol.patches.ensure_entities_for_data_areas")
    assert backfill < sweep, "the tree backfill must run before the orphan sweep"


def test_orphan_sweep_covers_every_referring_doctype():
    src = _patch_src()
    for doctype in REFERRING_DOCTYPES:
        assert f'"{doctype}"' in src, f"{doctype} missing from the sweep"


def test_orphan_sweep_reloads_the_doctype_first():
    """Patches run pre_model_sync; without this the sweep is a silent no-op
    that still records itself as run."""
    src = _patch_src()
    assert src.index("reload_doc") < src.index('table_exists("Entity")')


def test_orphan_sweep_does_not_invent_structure():
    """Placeholders land at the root where they are visible, rather than under
    a plausible-looking parent that nobody chose."""
    src = _patch_src()
    body = src.split('"""', 2)[2]
    assert "parent_entity" not in body
    assert "functional_currency" not in body
