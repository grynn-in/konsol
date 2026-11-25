"""#534: Dimension.allocation_role is retired.

Nothing read it after the allocation feature left (#264); cost allocation
declares its dimension by a Link on the Rule Set. The column must be dropped
by a patch, because Frappe never drops a column removed from DocType JSON.
"""
import json
import os

KONSOL = os.path.join(os.path.dirname(__file__), "..")
DIMENSION_JSON = os.path.join(KONSOL, "epm", "doctype", "dimension", "dimension.json")
PATCH = os.path.join(KONSOL, "patches", "drop_dimension_allocation_role.py")
PATCHES_TXT = os.path.join(KONSOL, "patches.txt")
WORD = "allocation" + "_role"
SELF = os.path.abspath(__file__)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_dimension_json_has_no_allocation_role():
    doc = json.loads(_read(DIMENSION_JSON))
    assert WORD not in [f["fieldname"] for f in doc["fields"]]
    assert WORD not in doc.get("field_order", [])


def test_no_source_file_mentions_it():
    hits = []
    for root, dirs, files in os.walk(KONSOL):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "__pycache__", ".git")]
        for name in files:
            if not name.endswith((".py", ".js", ".json")):
                continue
            path = os.path.abspath(os.path.join(root, name))
            if path in (SELF, os.path.abspath(PATCH)):
                continue
            if WORD in _read(path):
                hits.append(os.path.relpath(path, KONSOL))
    assert not hits, hits


def test_patch_exists_listed_and_guarded():
    assert os.path.exists(PATCH)
    assert "konsol.patches.drop_dimension_allocation_role" in _read(PATCHES_TXT).split()
    src = _read(PATCH)
    assert "has_column" in src and "DROP COLUMN" in src and WORD in src


def test_workflows_docstring_does_not_name_deleted_file():
    assert "allocation_run_workflow" not in _read(os.path.join(KONSOL, "workflows.py"))
