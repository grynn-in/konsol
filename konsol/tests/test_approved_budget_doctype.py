"""Approved Budget: the declaration that a group's budget for a fiscal year is
the approved one (konsol#348 S5 K1; BUD-4-2, Deepak Pai, 7 Nov 2026).

One declaration per (root Consolidation Group, fiscal year) names the Budget
Cycle and the layer policy (all layers, or one layer) the Consolidated
Statement's Budget columns read. Submittable; submit is the approval and
there is no workflow (#348 S5-Q1 a). The owner and date are the submitted
document's own.

validate refuses, by name: a member group (root groups only); a Cycle that is
not approved, not of a budget scenario or not of the declared year; a layer
with the All-layers policy or none with One-layer; and a second live
declaration (docstatus 0 or 1) for the same (group, year), read FOR UPDATE
(the MariaDB REPEATABLE READ trap; Budget Cycle's _refuse_second_live_cycle
pattern).

Site-free: the JSON is read as a file; the controller runs against a stub
frappe whose db serves in-memory rows and records what it was asked.
"""
import ast
import importlib.util
import json
import os
import sys
import types

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "epm", "doctype", "approved_budget")
PATH = os.path.join(DOCTYPE_DIR, "approved_budget.json")
CONTROLLER = os.path.join(DOCTYPE_DIR, "approved_budget.py")
FLAGS = {"r": "read", "w": "write", "c": "create", "d": "delete", "s": "submit", "x": "cancel", "a": "amend"}

ROOT = "CG-ZZGRP-"
MEMBER = "CG-ZZGRP-ZZNL"
CYCLE = "BCYC-ZZB-2027-abc12345"


# ---- the JSON ---------------------------------------------------------------------------

def _doctype():
    with open(PATH) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _doctype()["fields"]}


def test_is_submittable_tracks_changes_and_has_no_workflow_file():
    d = _doctype()
    assert d["doctype"] == "DocType" and d["name"] == "Approved Budget" and d["module"] == "EPM"
    assert d["is_submittable"] == 1
    assert d["track_changes"] == 1
    assert d["title_field"] == "title"
    assert d["search_fields"].split(",") == ["consolidation_group", "fiscal_year", "budget_cycle"]
    # S5-Q1 (a): submit is the approval, no installed workflow.
    assert not os.path.exists(os.path.join(DOCTYPE_DIR, "approved_budget_workflow.json"))
    assert "workflow_state" not in _fields()


def test_scope_fields_per_the_field_table():
    fields = _fields()
    g = fields["consolidation_group"]
    assert (g["fieldtype"], g["options"], g["reqd"]) == ("Link", "Consolidation Group", 1)
    assert "root" in g["description"].lower(), g["description"]
    fy = fields["fiscal_year"]
    assert (fy["fieldtype"], fy["reqd"]) == ("Int", 1)
    title = fields["title"]
    assert title["fieldtype"] == "Data" and title["read_only"] == 1 and not title.get("reqd")


def test_budget_fields_per_the_field_table():
    fields = _fields()
    c = fields["budget_cycle"]
    assert (c["fieldtype"], c["options"], c["reqd"]) == ("Link", "Budget Cycle", 1)
    assert "approved" in c["description"].lower() and "budget" in c["description"].lower(), c["description"]
    for name in ("scenario_id", "rate_set"):
        f = fields[name]
        assert f["fieldtype"] == "Data" and f["read_only"] == 1, name
        assert f["fetch_from"] == f"budget_cycle.{name}", name
    policy = fields["layer_policy"]
    assert (policy["fieldtype"], policy["reqd"]) == ("Select", 1)
    assert policy["options"] == "All layers\nOne layer"
    assert not policy.get("default"), "declared, not guessed: no default policy"
    layer = fields["layer"]
    assert (layer["fieldtype"], layer["options"]) == ("Link", "Budget Layer")
    assert not layer.get("reqd")
    assert layer["mandatory_depends_on"] == "eval:doc.layer_policy=='One layer'"
    assert layer["depends_on"] == "eval:doc.layer_policy=='One layer'"


def test_approval_fields_per_the_field_table():
    fields = _fields()
    fp = fields["input_fingerprint"]
    assert fp["fieldtype"] == "Data" and fp["read_only"] == 1 and fp["no_copy"] == 1
    assert not fp.get("allow_on_submit"), "copied in before_submit, frozen by the submit"
    assert "Cycle" in fp["description"], fp["description"]
    notes = fields["notes"]
    assert notes["fieldtype"] == "Small Text" and not notes.get("reqd")
    af = fields["amended_from"]
    assert (af["fieldtype"], af["options"]) == ("Link", "Approved Budget")
    assert af["read_only"] == 1 and af["no_copy"] == 1


def test_tabs_and_sections_per_the_field_table():
    order = [f["fieldname"] for f in _doctype()["fields"]]
    kinds = {f["fieldname"]: f["fieldtype"] for f in _doctype()["fields"]}
    labels = {f["fieldname"]: f.get("label") for f in _doctype()["fields"]}
    tabs = [n for n in order if kinds[n] == "Tab Break"]
    assert [labels[t] for t in tabs] == ["Approved Budget"], tabs
    sections = [n for n in order if kinds[n] == "Section Break"]
    assert [labels[s] for s in sections] == ["Scope", "Budget", "Approval"], sections
    scope, budget, approval = (order.index(s) for s in sections)
    for name in ("consolidation_group", "fiscal_year", "title"):
        assert scope < order.index(name) < budget, name
    for name in ("budget_cycle", "scenario_id", "rate_set", "layer_policy", "layer"):
        assert budget < order.index(name) < approval, name
    for name in ("input_fingerprint", "notes", "amended_from"):
        assert order.index(name) > approval, name
    assert [n for n in order if kinds[n] == "Column Break"], "two columns (ERPNext form standard)"


def _perms():
    out = {}
    for p in _doctype()["permissions"]:
        if p.get("permlevel"):
            continue
        out[p["role"]] = "".join(ch for ch, field in FLAGS.items() if p.get(field))
    return out


def test_permissions_per_the_field_table():
    assert _perms() == {
        "System Manager": "rwcdsxa",
        "EPM Admin": "rwcsxa",   # approves (submits), cancels; amend inserts, so create
        "EPM Analyst": "rwc",
        "EPM User": "r",
    }
    assert not [r for r in _perms() if r.startswith("Budget ")]


def test_list_view_shows_the_key_and_the_policy():
    fields = _fields()
    for name in ("consolidation_group", "fiscal_year", "budget_cycle", "layer_policy"):
        assert fields[name].get("in_list_view") == 1, name


# ---- the controller -----------------------------------------------------------------------

class _Thrown(Exception):
    pass


def _match(row, filters):
    for field, cond in (filters or {}).items():
        val = row.get(field)
        if isinstance(cond, (list, tuple)):
            op, arg = cond
            if op == "in":
                if val not in arg:
                    return False
            else:
                raise AssertionError(f"unsupported filter {field}: {cond!r}")
        elif val != cond:
            return False
    return True


def _tables():
    return {
        "Consolidation Group": {
            ROOT: {"consolidation_group": "ZZGRP", "data_area_id": None},
            MEMBER: {"consolidation_group": "ZZGRP", "data_area_id": "ZZNL"},
        },
        "Budget Cycle": {
            CYCLE: {"docstatus": 1, "scenario_id": "ZZB", "scenario_type": "budget", "fiscal_year": 2027,
                    "rate_set": "ZZRS", "input_fingerprint": "v1:deadbeef"},
            "BCYC-ZZB-2027-draft": {"docstatus": 0, "scenario_id": "ZZB", "scenario_type": "budget",
                                    "fiscal_year": 2027, "rate_set": "ZZRS", "input_fingerprint": None},
            "BCYC-ZZB-2027-gone": {"docstatus": 2, "scenario_id": "ZZB", "scenario_type": "budget",
                                   "fiscal_year": 2027, "rate_set": "ZZRS", "input_fingerprint": "v1:old"},
            "BCYC-ZZF-2027-2027_03_31": {"docstatus": 1, "scenario_id": "ZZF", "scenario_type": "forecast",
                                         "fiscal_year": 2027, "rate_set": "ZZRS", "input_fingerprint": "v1:fc"},
            "BCYC-ZZB-2026": {"docstatus": 1, "scenario_id": "ZZB", "scenario_type": "budget",
                              "fiscal_year": 2026, "rate_set": "ZZRS", "input_fingerprint": "v1:y26"},
        },
        "Approved Budget": [],   # rows: {name, consolidation_group, fiscal_year, docstatus}
    }


@pytest.fixture
def env(monkeypatch):
    tables = _tables()
    asked = []

    class Document:
        def __init__(self, **kw):
            self.flags = types.SimpleNamespace(name_set=False)
            self.__dict__.update(kw)

        def get(self, key, default=None):
            return self.__dict__.get(key, default)

        def set_new_name(self, *a, **k):
            pass

    def get_value(doctype, name, fieldname=None, as_dict=False, **k):
        asked.append(("get_value", doctype, name))
        row = tables[doctype].get(name) if isinstance(tables[doctype], dict) else None
        if row is None:
            return None
        if isinstance(fieldname, (list, tuple)):
            picked = {f: row.get(f) for f in fieldname}
            return types.SimpleNamespace(**picked) if as_dict else tuple(picked.values())
        return row.get(fieldname)

    def get_values(doctype, filters=None, fieldname=None, as_dict=False, order_by=None, for_update=False, **k):
        asked.append(("get_values", doctype, dict(filters or {}), {"for_update": for_update, "order_by": order_by}))
        rows = [r for r in tables[doctype] if _match(r, filters)]
        if order_by:
            rows.sort(key=lambda r: r["name"])
        return [types.SimpleNamespace(**{f: r.get(f) for f in fieldname}) for r in rows]

    def throw(msg, exc=None, *a, **k):
        raise _Thrown(msg)

    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.ValidationError = _Thrown
    frappe.flags = types.SimpleNamespace(in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe.db = types.SimpleNamespace(
        get_value=get_value, get_values=get_values,
        exists=lambda dt, name: any(r["name"] == name for r in tables["Approved Budget"]),
    )
    model = types.ModuleType("frappe.model")
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document
    grain = types.ModuleType("konsol.epm.budget_grain")
    grain.digest_name = lambda prefix, parts: prefix + "-" + "-".join(str(p) for p in parts)
    for name, mod in {"frappe": frappe, "frappe.model": model, "frappe.model.document": docmod,
                      "konsol.epm.budget_grain": grain}.items():
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("_approved_budget_under_test", CONTROLLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def doc(**overrides):
        row = {"doctype": "Approved Budget", "name": None, "consolidation_group": ROOT, "fiscal_year": 2027,
               "budget_cycle": CYCLE, "layer_policy": "All layers", "layer": None, "docstatus": 0}
        row.update(overrides)
        d = mod.ApprovedBudget(**row)
        d.save = lambda *a, **k: pytest.fail("a hook must not save")
        return d

    return types.SimpleNamespace(doc=doc, tables=tables, asked=asked, mod=mod, frappe=frappe)


def test_a_valid_declaration_copies_the_cycles_scenario_and_rate_set_and_titles_itself(env):
    d = env.doc()
    d.validate()
    assert d.scenario_id == "ZZB" and d.rate_set == "ZZRS"
    assert d.title == "ZZGRP FY2027"


def test_a_member_group_is_refused_by_name(env):
    with pytest.raises(_Thrown, match=r"CG-ZZGRP-ZZNL.*member.*root"):
        env.doc(consolidation_group=MEMBER).validate()


def test_a_draft_cycle_is_refused_by_name(env):
    with pytest.raises(_Thrown, match=r"BCYC-ZZB-2027-draft.*approved"):
        env.doc(budget_cycle="BCYC-ZZB-2027-draft").validate()


def test_a_cancelled_cycle_is_refused_by_name(env):
    with pytest.raises(_Thrown, match=r"BCYC-ZZB-2027-gone"):
        env.doc(budget_cycle="BCYC-ZZB-2027-gone").validate()


def test_a_forecast_cycle_is_refused_by_name(env):
    """BUD-4: the declaration names a Cycle of a budget scenario; the forecast
    (latest-estimate) read is S6."""
    with pytest.raises(_Thrown, match=r"BCYC-ZZF-2027-2027_03_31.*forecast"):
        env.doc(budget_cycle="BCYC-ZZF-2027-2027_03_31").validate()


def test_a_cycle_of_another_year_is_refused_by_name(env):
    with pytest.raises(_Thrown, match=r"BCYC-ZZB-2026.*2026.*2027"):
        env.doc(budget_cycle="BCYC-ZZB-2026").validate()


def test_one_layer_needs_the_layer_and_all_layers_refuses_one(env):
    with pytest.raises(_Thrown, match=r"One layer"):
        env.doc(layer_policy="One layer", layer=None).validate()
    d = env.doc(layer_policy="One layer", layer="base")
    d.validate()
    assert d.layer == "base"
    # Never cleared silently: the user says which they meant.
    with pytest.raises(_Thrown, match=r"All layers"):
        env.doc(layer_policy="All layers", layer="base").validate()


def test_a_second_live_declaration_for_the_group_and_year_is_refused_by_name(env):
    env.tables["Approved Budget"].append(
        {"name": "APB-CG-ZZGRP--2027", "consolidation_group": ROOT, "fiscal_year": 2027, "docstatus": 1})
    with pytest.raises(_Thrown, match=r"APB-CG-ZZGRP--2027.*approved"):
        env.doc(name="APB-CG-ZZGRP--2027-1").validate()
    env.tables["Approved Budget"][0]["docstatus"] = 0
    with pytest.raises(_Thrown, match=r"APB-CG-ZZGRP--2027.*draft"):
        env.doc(name="APB-CG-ZZGRP--2027-1").validate()


def test_a_cancelled_declaration_and_the_document_itself_do_not_count(env):
    env.tables["Approved Budget"].append(
        {"name": "APB-CG-ZZGRP--2027", "consolidation_group": ROOT, "fiscal_year": 2027, "docstatus": 2})
    env.doc(name="APB-CG-ZZGRP--2027-1").validate()
    env.tables["Approved Budget"].append(
        {"name": "APB-CG-ZZGRP--2027-1", "consolidation_group": ROOT, "fiscal_year": 2027, "docstatus": 0})
    env.doc(name="APB-CG-ZZGRP--2027-1").validate()  # its own row on a re-save


def test_the_live_check_is_a_locking_read_on_group_year_and_docstatus(env):
    env.doc().validate()
    read = next(a for a in env.asked if a[0] == "get_values" and a[1] == "Approved Budget")
    assert read[2] == {"consolidation_group": ROOT, "fiscal_year": 2027, "docstatus": ["in", [0, 1]]}
    assert read[3]["for_update"] is True, "REPEATABLE READ: a plain read misses a row committed after the snapshot"
    assert read[3]["order_by"], "the refusal names a deterministic row"


def test_for_update_is_literal_in_the_controller():
    """As the Budget Cycle tests pin it: the lock is in the source, not a stub's doing."""
    with open(CONTROLLER) as f:
        tree = ast.parse(f.read())
    calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
             and any(k.arg == "for_update" and getattr(k.value, "value", None) is True for k in c.keywords)]
    assert calls, "no frappe.db.get_values(..., for_update=True) in approved_budget.py"


def test_the_live_check_is_skipped_during_install_and_migrate(env):
    env.frappe.flags.in_migrate = True
    env.tables["Approved Budget"].append(
        {"name": "APB-CG-ZZGRP--2027", "consolidation_group": ROOT, "fiscal_year": 2027, "docstatus": 1})
    env.doc(name="other").validate()


def test_autoname_is_the_digest_or_the_first_free_suffix(env):
    d = env.doc()
    d.autoname()
    assert d.name == "APB-CG-ZZGRP--2027"
    env.tables["Approved Budget"].append({"name": "APB-CG-ZZGRP--2027", "docstatus": 2})
    env.tables["Approved Budget"].append({"name": "APB-CG-ZZGRP--2027-1", "docstatus": 2})
    d = env.doc()
    d.autoname()
    assert d.name == "APB-CG-ZZGRP--2027-2"


def test_the_controller_never_commits_or_saves_in_a_hook():
    with open(CONTROLLER) as f:
        src = f.read()
    for forbidden in ("db.commit", "self.save(", "self.submit("):
        assert forbidden not in src, forbidden
