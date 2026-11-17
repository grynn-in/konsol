"""Current Forecast: the declaration that one approved forecast round is THE
round a forecast scenario reports for a fiscal year (konsol#348 S6 KA1;
S6-Q1 (b), Deepak Pai, 17 Nov 2026).

A forecast (scenario, fiscal year) may hold several approved rounds side by
side (FC-4). Every entity-grain reader (the scenario trial balance, the
Excel flat and hierarchy reads, the cost-allocation pool) needs ONE round,
and FC-3-1 (decided 13 Nov 2026) rejected "latest round wins": so the round
is declared, here, and the warehouse's gold_forecast_current_round reads the
declaration. Undeclared, a year has no forecast rows anywhere, and the
warehouse WARNs naming it; nothing is summed and nothing is guessed.

Submittable; submit is the approval and there is no workflow (the Approved
Budget precedent, #348 S5-Q1 a). validate refuses, by name: a scenario that
is not of type forecast (read under the Scenario share lock); a Cycle that
is not approved, of another scenario or of another year; and a second live
declaration (docstatus 0 or 1) for the (scenario, year), read FOR UPDATE
(the MariaDB REPEATABLE READ trap; Approved Budget's pattern).

Site-free: the JSON is read as a file; the controller runs against a stub
frappe whose db serves in-memory rows and records what it was asked.
"""
import ast
import importlib.util
import json
import os
import sys
import types
from datetime import date

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "epm", "doctype", "current_forecast")
PATH = os.path.join(DOCTYPE_DIR, "current_forecast.json")
CONTROLLER = os.path.join(DOCTYPE_DIR, "current_forecast.py")
FLAGS = {"r": "read", "w": "write", "c": "create", "d": "delete", "s": "submit", "x": "cancel", "a": "amend"}

FORECAST = "ZZFC"
BUDGET = "ZZB"
R1 = "BCYC-ZZFC-2027-2027-03-31"
R2 = "BCYC-ZZFC-2027-2027-06-30"
R2_DRAFT = "BCYC-ZZFC-2027-2027-09-30"
R_GONE = "BCYC-ZZFC-2027-2026-12-31"
R_2028 = "BCYC-ZZFC-2028-2027-06-30"
B_CYCLE = "BCYC-ZZB-2027"


# ---- the JSON ---------------------------------------------------------------------------

def _doctype():
    with open(PATH) as f:
        return json.load(f)


def _fields():
    return {f["fieldname"]: f for f in _doctype()["fields"]}


def test_is_submittable_tracks_changes_and_has_no_workflow_file():
    d = _doctype()
    assert d["doctype"] == "DocType" and d["name"] == "Current Forecast" and d["module"] == "EPM"
    assert d["is_submittable"] == 1
    assert d["track_changes"] == 1
    assert d["title_field"] == "title"
    assert d["search_fields"].split(",") == ["scenario_id", "fiscal_year", "budget_cycle"]
    # S5-Q1 (a) precedent: submit is the approval, no installed workflow.
    assert not os.path.exists(os.path.join(DOCTYPE_DIR, "current_forecast_workflow.json"))
    assert "workflow_state" not in _fields()
    assert not d.get("autoname"), "the controller names it (digest_name, never format:{#####}; #535)"


def test_scope_fields_per_the_field_table():
    fields = _fields()
    s = fields["scenario_id"]
    assert (s["fieldtype"], s["options"], s["reqd"]) == ("Link", "Scenario", 1)
    assert "forecast" in s["description"].lower(), s["description"]
    fy = fields["fiscal_year"]
    assert (fy["fieldtype"], fy["reqd"]) == ("Int", 1)
    title = fields["title"]
    assert title["fieldtype"] == "Data" and title["read_only"] == 1 and not title.get("reqd")


def test_round_fields_per_the_field_table():
    fields = _fields()
    c = fields["budget_cycle"]
    assert (c["fieldtype"], c["options"], c["reqd"]) == ("Link", "Budget Cycle", 1)
    assert "approved" in c["description"].lower(), c["description"]
    for name, kind in (("as_of_date", "Date"), ("horizon_periods", "Int"), ("rate_set", "Data")):
        f = fields[name]
        assert f["fieldtype"] == kind and f["read_only"] == 1, name
        assert f["fetch_from"] == f"budget_cycle.{name}", name
        assert not f.get("reqd"), name


def test_approval_fields_per_the_field_table():
    fields = _fields()
    fp = fields["input_fingerprint"]
    assert fp["fieldtype"] == "Data" and fp["read_only"] == 1 and fp["no_copy"] == 1
    assert not fp.get("allow_on_submit"), "copied in before_submit, frozen by the submit"
    by = fields["approved_by"]
    assert (by["fieldtype"], by["options"], by["read_only"], by["no_copy"]) == ("Link", "User", 1, 1)
    assert not by.get("allow_on_submit") and not by.get("reqd")
    on = fields["approved_on"]
    assert (on["fieldtype"], on["read_only"], on["no_copy"]) == ("Datetime", 1, 1)
    assert not on.get("allow_on_submit") and not on.get("reqd")
    notes = fields["notes"]
    assert notes["fieldtype"] == "Small Text" and not notes.get("reqd")
    af = fields["amended_from"]
    assert (af["fieldtype"], af["options"]) == ("Link", "Current Forecast")
    assert af["read_only"] == 1 and af["no_copy"] == 1


def test_tabs_and_sections_per_the_field_table():
    order = [f["fieldname"] for f in _doctype()["fields"]]
    kinds = {f["fieldname"]: f["fieldtype"] for f in _doctype()["fields"]}
    labels = {f["fieldname"]: f.get("label") for f in _doctype()["fields"]}
    tabs = [n for n in order if kinds[n] == "Tab Break"]
    assert [labels[t] for t in tabs] == ["Current Forecast"], tabs
    sections = [n for n in order if kinds[n] == "Section Break"]
    assert [labels[s] for s in sections] == ["Scope", "Round", "Approval"], sections
    scope, rnd, approval = (order.index(s) for s in sections)
    for name in ("scenario_id", "fiscal_year", "title"):
        assert scope < order.index(name) < rnd, name
    for name in ("budget_cycle", "as_of_date", "horizon_periods", "rate_set"):
        assert rnd < order.index(name) < approval, name
    for name in ("input_fingerprint", "approved_by", "approved_on", "notes", "amended_from"):
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


def test_list_view_shows_the_key():
    fields = _fields()
    for name in ("scenario_id", "fiscal_year", "budget_cycle", "as_of_date"):
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


def _cycle(docstatus, scenario_id, scenario_type, fiscal_year, as_of, horizon, fingerprint):
    return {"docstatus": docstatus, "scenario_id": scenario_id, "scenario_type": scenario_type,
            "fiscal_year": fiscal_year, "as_of_date": as_of, "horizon_periods": horizon,
            "rate_set": "ZZRS", "input_fingerprint": fingerprint}


def _tables():
    return {
        "Scenario": {FORECAST: "forecast", BUDGET: "budget"},   # name -> scenario_type
        "Budget Cycle": {
            R1: _cycle(1, FORECAST, "forecast", 2027, date(2027, 3, 31), 9, "v1:r1"),
            R2: _cycle(1, FORECAST, "forecast", 2027, date(2027, 6, 30), 6, "v1:r2"),
            R2_DRAFT: _cycle(0, FORECAST, "forecast", 2027, date(2027, 9, 30), 3, None),
            R_GONE: _cycle(2, FORECAST, "forecast", 2027, date(2026, 12, 31), 12, "v1:old"),
            R_2028: _cycle(1, FORECAST, "forecast", 2028, date(2027, 6, 30), 6, "v1:r2y2"),
            B_CYCLE: _cycle(1, BUDGET, "budget", 2027, None, 0, "v1:b"),
        },
        "Current Forecast": [],   # rows: {name, scenario_id, fiscal_year, docstatus}
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
        asked.append(("get_value", doctype, name, {"for_update": k.get("for_update", False)}))
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
    frappe.session = types.SimpleNamespace(user="lead@example.com")
    utils = types.ModuleType("frappe.utils")
    frappe.utils = utils
    frappe.db = types.SimpleNamespace(
        get_value=get_value, get_values=get_values,
        exists=lambda dt, name: any(r["name"] == name for r in tables["Current Forecast"]),
    )
    model = types.ModuleType("frappe.model")
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document
    grain = types.ModuleType("konsol.epm.budget_grain")
    grain.digest_name = lambda prefix, parts: prefix + "-" + "-".join(str(p) for p in parts)
    # The Scenario's type under its share lock (review of #460), recorded.
    type_reads = []

    def locked_scenario_type(scenario):
        type_reads.append(scenario)
        return tables["Scenario"].get(scenario)

    scenario = types.ModuleType("konsol.epm.doctype.scenario.scenario")
    scenario.locked_scenario_type = locked_scenario_type
    added_indexes = []
    frappe.db.add_index = lambda dt, fields, index_name=None: added_indexes.append((dt, list(fields), index_name))
    for name, mod in {"frappe": frappe, "frappe.utils": utils, "frappe.model": model,
                      "frappe.model.document": docmod,
                      "konsol.epm.budget_grain": grain,
                      "konsol.epm.doctype.scenario.scenario": scenario}.items():
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("_current_forecast_under_test", CONTROLLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def doc(**overrides):
        row = {"doctype": "Current Forecast", "name": None, "scenario_id": FORECAST, "fiscal_year": 2027,
               "budget_cycle": R2, "docstatus": 0}
        row.update(overrides)
        d = mod.CurrentForecast(**row)
        d.save = lambda *a, **k: pytest.fail("a hook must not save")
        return d

    return types.SimpleNamespace(doc=doc, tables=tables, asked=asked, mod=mod, frappe=frappe,
                                 type_reads=type_reads, added_indexes=added_indexes)


def test_a_valid_declaration_copies_the_round_and_titles_itself(env):
    d = env.doc()
    d.validate()
    assert d.as_of_date == date(2027, 6, 30) and d.horizon_periods == 6 and d.rate_set == "ZZRS"
    assert d.title == "ZZFC FY2027"
    assert env.type_reads == [FORECAST], "the scenario's type is read under its share lock"


def test_a_budget_scenario_is_refused_by_name(env):
    """S6-Q1 (b): the declaration is a forecast's. A budget scenario has one
    Cycle per year by rule and nothing to declare; Approved Budget is its
    declaration."""
    with pytest.raises(_Thrown, match=r"ZZB.*budget.*forecast"):
        env.doc(scenario_id=BUDGET, budget_cycle=B_CYCLE).validate()


def test_an_unknown_scenario_is_refused_by_name(env):
    with pytest.raises(_Thrown, match=r"ZZNONE"):
        env.doc(scenario_id="ZZNONE").validate()


def test_a_draft_cycle_is_refused_by_name(env):
    with pytest.raises(_Thrown, match=rf"{R2_DRAFT}.*draft"):
        env.doc(budget_cycle=R2_DRAFT).validate()


def test_a_cancelled_cycle_is_refused_by_name(env):
    with pytest.raises(_Thrown, match=rf"{R_GONE}.*cancelled"):
        env.doc(budget_cycle=R_GONE).validate()


def test_a_missing_cycle_is_refused_by_name(env):
    with pytest.raises(_Thrown, match=r"BCYC-NONE"):
        env.doc(budget_cycle="BCYC-NONE").validate()


def test_a_cycle_of_another_scenario_is_refused_by_name(env):
    with pytest.raises(_Thrown, match=rf"{B_CYCLE}.*ZZB.*ZZFC"):
        env.doc(budget_cycle=B_CYCLE).validate()


def test_a_cycle_of_another_year_is_refused_by_name(env):
    """Q24 a: a round crossing the year end is one Cycle per fiscal year; the
    declaration for FY2027 names the FY2027 Cycle, not its FY2028 sibling."""
    with pytest.raises(_Thrown, match=rf"{R_2028}.*2028.*2027"):
        env.doc(budget_cycle=R_2028).validate()


def test_a_second_live_declaration_for_the_scenario_and_year_is_refused_by_name(env):
    env.tables["Current Forecast"].append(
        {"name": "CFC-ZZFC-2027", "scenario_id": FORECAST, "fiscal_year": 2027, "docstatus": 1})
    with pytest.raises(_Thrown, match=r"CFC-ZZFC-2027.*approved"):
        env.doc(name="CFC-ZZFC-2027-1").validate()
    env.tables["Current Forecast"][0]["docstatus"] = 0
    with pytest.raises(_Thrown, match=r"CFC-ZZFC-2027.*draft"):
        env.doc(name="CFC-ZZFC-2027-1").validate()


def test_a_cancelled_declaration_and_the_document_itself_do_not_count(env):
    env.tables["Current Forecast"].append(
        {"name": "CFC-ZZFC-2027", "scenario_id": FORECAST, "fiscal_year": 2027, "docstatus": 2})
    env.doc(name="CFC-ZZFC-2027-1").validate()
    env.tables["Current Forecast"].append(
        {"name": "CFC-ZZFC-2027-1", "scenario_id": FORECAST, "fiscal_year": 2027, "docstatus": 0})
    env.doc(name="CFC-ZZFC-2027-1").validate()  # its own row on a re-save


def test_another_years_declaration_does_not_count(env):
    env.tables["Current Forecast"].append(
        {"name": "CFC-ZZFC-2028", "scenario_id": FORECAST, "fiscal_year": 2028, "docstatus": 1})
    env.doc().validate()


def test_the_live_check_is_a_locking_read_on_scenario_year_and_docstatus(env):
    env.doc().validate()
    read = next(a for a in env.asked if a[0] == "get_values" and a[1] == "Current Forecast")
    assert read[2] == {"scenario_id": FORECAST, "fiscal_year": 2027, "docstatus": ["in", [0, 1]]}
    assert read[3]["for_update"] is True, "REPEATABLE READ: a plain read misses a row committed after the snapshot"
    assert read[3]["order_by"], "the refusal names a deterministic row"


def test_for_update_is_literal_in_the_controller():
    """As the Approved Budget tests pin it: the lock is in the source, not a stub's doing."""
    with open(CONTROLLER) as f:
        tree = ast.parse(f.read())
    calls = [c for c in ast.walk(tree) if isinstance(c, ast.Call)
             and any(k.arg == "for_update" and getattr(k.value, "value", None) is True for k in c.keywords)]
    assert calls, "no frappe.db.get_values(..., for_update=True) in current_forecast.py"


def test_the_live_check_is_skipped_during_install_and_migrate(env):
    env.frappe.flags.in_migrate = True
    env.tables["Current Forecast"].append(
        {"name": "CFC-ZZFC-2027", "scenario_id": FORECAST, "fiscal_year": 2027, "docstatus": 1})
    env.doc(name="other").validate()


def test_autoname_is_the_digest_or_the_first_free_suffix(env):
    d = env.doc()
    d.autoname()
    assert d.name == "CFC-ZZFC-2027"
    env.tables["Current Forecast"].append({"name": "CFC-ZZFC-2027", "docstatus": 2})
    env.tables["Current Forecast"].append({"name": "CFC-ZZFC-2027-1", "docstatus": 2})
    d = env.doc()
    d.autoname()
    assert d.name == "CFC-ZZFC-2027-2"


def test_set_new_name_renames_an_amendment_to_the_first_free_suffix(env):
    """Frappe v16 names an amendment ``<base>-<n+1>`` before the controller runs
    (budget_cycle.py's trace); autoname names it again as the first free suffix."""
    env.tables["Current Forecast"].append({"name": "CFC-ZZFC-2027", "docstatus": 2})
    d = env.doc(name="CFC-ZZFC-2027-1", amended_from="CFC-ZZFC-2027")
    d.set_new_name()
    assert d.name == "CFC-ZZFC-2027-1"
    d.flags.name_set = True
    d.name = "KEEP"
    d.set_new_name()
    assert d.name == "KEEP", "a document already named is left alone"


def test_the_controller_never_commits_or_saves_in_a_hook():
    with open(CONTROLLER) as f:
        src = f.read()
    for forbidden in ("db.commit", "self.save(", "self.submit("):
        assert forbidden not in src, forbidden


def test_on_doctype_update_adds_the_live_declaration_index(env):
    """The FOR UPDATE read in validate filters on (scenario, year, docstatus);
    without an index it scans and next-key-locks the table (PR #522 review)."""
    env.mod.on_doctype_update()
    assert env.added_indexes == [("Current Forecast", ["scenario_id", "fiscal_year", "docstatus"],
                                  "current_forecast_live")]
