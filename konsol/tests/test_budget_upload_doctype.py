"""Budget Upload doctype (U0, konsol#348 part 2): mirrors Trial Balance Upload.

Shape, the never-defaulted amount basis, the read-only job fields and the
permissions. The check and load (U1, U2) live in konsol/budget_bulk.py; the
controller only derives the loaded count and the form indicator (U2).
"""
import json
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(APP_DIR, "epm", "doctype", "budget_upload")
TBU = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_upload", "trial_balance_upload.json")


def _meta():
    with open(os.path.join(DIR, "budget_upload.json")) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _meta()["fields"]}


def test_files_exist():
    for name in ("__init__.py", "budget_upload.py", "budget_upload.json"):
        assert os.path.exists(os.path.join(DIR, name)), name


def test_identity():
    m = _meta()
    assert m["name"] == "Budget Upload"
    assert m["module"] == "EPM"
    assert m["autoname"] == "format:BUU-{#####}"
    assert m["track_changes"] == 1
    assert not m.get("is_submittable")
    assert not m.get("istable")


def test_field_order_follows_the_part_2_table():
    data = [f["fieldname"] for f in _meta()["fields"]
            if f["fieldtype"] not in ("Tab Break", "Section Break", "Column Break")]
    assert data == ["upload_file", "amount_basis", "status", "group_count", "valid_count", "total_rows",
                    "loaded_count", "failed_count", "error", "report", "report_html"]
    assert _meta()["field_order"] == [f["fieldname"] for f in _meta()["fields"]]


def test_tabs_and_sections():
    m = _meta()
    tabs = [f["label"] for f in m["fields"] if f["fieldtype"] == "Tab Break"]
    sections = [f["label"] for f in m["fields"] if f["fieldtype"] == "Section Break"]
    assert tabs == ["Upload", "Result"]
    assert sections == ["File", "Counts", "Result"]
    assert m["fields"][0]["fieldtype"] == "Tab Break"
    assert sum(1 for f in m["fields"] if f["fieldtype"] == "Column Break") == 2


def test_file_is_required_and_set_once():
    f = _fields()["upload_file"]
    assert f["fieldtype"] == "Attach" and f["reqd"] == 1 and f["set_only_once"] == 1
    assert "layer" in f["description"] and "scenario" in f["description"]


def test_amount_basis_is_never_defaulted():
    """Required unless every row carries amount_basis: the file decides, so the
    check refuses per group (M3); the field is neither reqd nor defaulted."""
    f = _fields()["amount_basis"]
    with open(TBU) as fh:
        tbu = {x["fieldname"]: x for x in json.load(fh)["fields"]}
    assert f["fieldtype"] == "Select"
    # The TB's bases less Period-end balance (Q15-c).
    assert f["options"].replace("\nPeriod-end balance", "") == f["options"]
    assert set(f["options"].split("\n")) < set(tbu["amount_basis"]["options"].split("\n"))
    assert not f.get("reqd") and not f.get("default")
    assert f.get("description")


def test_status_and_counts_are_read_only():
    f = _fields()
    assert f["status"]["options"].split("\n") == ["Draft", "Checked", "Loading", "Loaded", "Partly Loaded", "Failed"]
    assert f["status"]["default"] == "Draft"
    assert f["status"]["label"] == "Job Status"
    for name in ("status", "group_count", "valid_count", "total_rows", "loaded_count", "failed_count",
                 "error", "report"):
        assert f[name].get("read_only") == 1, name
    for name in ("group_count", "valid_count", "total_rows", "loaded_count", "failed_count"):
        assert f[name]["fieldtype"] == "Int", name
    assert f["group_count"]["label"] == "Groups" and f["group_count"].get("description")
    assert f["error"]["fieldtype"] == "Small Text"
    assert f["report"]["fieldtype"] == "Code" and f["report"]["options"] == "JSON"
    assert f["report_html"]["fieldtype"] == "HTML"


def test_permissions():
    perms = {p["role"]: p for p in _meta()["permissions"]}
    sm = perms["System Manager"]
    for k in ("read", "write", "create", "delete"):
        assert sm.get(k) == 1, k
    for role in ("EPM Admin", "EPM Analyst"):
        p = perms[role]
        for k in ("read", "write", "create"):
            assert p.get(k) == 1, (role, k)
        for k in ("submit", "cancel"):
            assert not p.get(k), (role, k)
    # As on Trial Balance Upload: EPM Admin may delete an upload, the analyst may not.
    assert perms["EPM Admin"].get("delete") == 1
    assert not perms["EPM Analyst"].get("delete")
    # Entity Accountant is left out: a multi-entity file has no data_area_id,
    # so Frappe cannot scope it (test_role_access, ENTITY_SCOPED_DOCTYPES).
    assert set(perms) == {"System Manager", "EPM Admin", "EPM Analyst"}


def test_controller_only_derives_the_loaded_count_and_indicator():
    """U2 made loaded_count derived, as on Trial Balance Upload: the controller
    reads it (and the form's status_now) from konsol.budget_bulk.upload_state
    and holds nothing else."""
    with open(os.path.join(DIR, "budget_upload.py")) as f:
        src = f.read()
    assert "class BudgetUpload(Document)" in src
    assert sorted(line.strip() for line in src.splitlines() if line.strip().startswith("def ")) == [
        "def loaded_count(self):", "def onload(self):"]


def test_the_upload_offers_only_the_budget_bases():
    """Q15-c: a Period-end balance needs a declared opening; the form must
    not offer what the check refuses."""
    options = [o for o in _fields()["amount_basis"]["options"].split("\n") if o]
    assert options == ["Period movement", "Year-to-date movement"]
