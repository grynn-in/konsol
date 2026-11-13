"""S2-T0: Budget Entry Line writes through to ``epm_raw.budget_entries``.

Budget Entry Line is a child table of Budget Entry (Q1-b2). Each raw row needs
its entry's header (cycle, scenario, layer, entity, year, period, currency,
basis) beside the line's own account and amounts. ``sync_doctype`` reads the
child with ``frappe.get_all``, and on Frappe v16 a child query cannot select
or filter on its parent's fields (``parent.cycle`` is refused, and
```tabBudget Entry`.cycle`` joins the parent the wrong way round: measured on
16.55.2, 13 Nov 2026). So the line carries a read-only copy of the header,
stamped from the entry by ``stamp_header``; nothing is typed twice.

BUD-4: a line reaches the warehouse only when its Budget Cycle is approved
(docstatus 1). The filter goes through the line's own ``cycle`` Link
(``cycle.docstatus``), which Frappe resolves with a join, so there is no list
of names and no empty-list trap: no approved cycle matches nothing.

These tests load the controller and clickhouse.py against stubbed frappe.
"""
import importlib.util
import json
import os
import sys
import types

import pytest

try:
    import requests  # noqa: F401 — clickhouse.py imports it at module level
except ModuleNotFoundError:  # host runner without requests — runner skips
    raise ImportError("needs requests")

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCTYPE_DIR = os.path.join(APP_DIR, "epm", "doctype", "budget_entry_line")
PY_PATH = os.path.join(DOCTYPE_DIR, "budget_entry_line.py")
JSON_PATH = os.path.join(DOCTYPE_DIR, "budget_entry_line.json")
CH_PATH = os.path.join(APP_DIR, "clickhouse.py")

DOCTYPE = "Budget Entry Line"

#: epm_raw.budget_entries' fixed columns (S2-R1, Q2-c, Q3-a), before the
#: site's dim_* columns, which schema_apply adds by ALTER.
RAW_COLUMNS = [
    "entry_name", "cycle", "scenario_id", "layer", "data_area_id",
    "fiscal_year", "fiscal_period", "main_account", "debit", "credit",
    "currency", "amount_basis",
]

HEADER = ["cycle", "scenario_id", "layer", "data_area_id", "fiscal_year",
          "fiscal_period", "currency", "amount_basis"]


# --- stubbed frappe ----------------------------------------------------------

def _resolve(row, field, tables):
    """A field of the row, following one ``link.target`` hop as v16 does
    (a LEFT JOIN: an absent target reads as None)."""
    if "." not in field:
        return row.get(field)
    link, target = field.split(".", 1)
    linked = next((r for r in tables["Budget Cycle"]
                   if link == "cycle" and r["name"] == row.get("cycle")), None)
    return linked.get(target) if linked else None


def _match(row, filters, tables):
    for field, cond in (filters or {}).items():
        val = _resolve(row, field, tables)
        if isinstance(cond, (list, tuple)):
            op, arg = cond[0], cond[1]
            if op == "in":
                if val not in arg:
                    return False
            elif op == "is" and arg == "not set":
                if val not in (None, ""):
                    return False
            else:
                raise AssertionError(f"unsupported filter {field}: {cond!r}")
        elif val != cond:
            return False
    return True


class _Field:
    def __init__(self, fieldname, fieldtype="Data"):
        self.fieldname = fieldname
        self.fieldtype = fieldtype


@pytest.fixture
def env(monkeypatch):
    tables = {"Budget Cycle": [], DOCTYPE: []}
    meta_fields = [_Field(f["fieldname"], f["fieldtype"])
                   for f in json.load(open(JSON_PATH))["fields"]]
    queued = []

    class Document:
        pass

    frappe = types.ModuleType("frappe")
    frappe.flags = types.SimpleNamespace(
        in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe.get_meta = lambda dt: types.SimpleNamespace(fields=meta_fields, is_submittable=0)

    def get_all(doctype, filters=None, fields=None, pluck=None, **kw):
        if doctype == "Module Def":
            return ["EPM"]
        if doctype == "DocType":
            return [DOCTYPE]
        return [{f: r.get(f) for f in (fields or ["name"])}
                for r in tables[doctype] if _match(r, filters, tables)]

    frappe.get_all = get_all
    frappe.logger = lambda: types.SimpleNamespace(
        warning=lambda *a, **k: None, info=lambda *a, **k: None,
        exception=lambda *a, **k: None)

    model = types.ModuleType("frappe.model")
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document
    base_document = types.ModuleType("frappe.model.base_document")
    model.base_document = base_document
    frappe.model = model

    ch_stub = types.ModuleType("konsol.clickhouse")
    ch_stub.sync_doctype_after_commit = lambda *a: queued.append(a)
    for name, mod in {"frappe": frappe, "frappe.model": model,
                      "frappe.model.document": docmod,
                      "frappe.model.base_document": base_document,
                      "konsol.clickhouse": ch_stub}.items():
        monkeypatch.setitem(sys.modules, name, mod)

    spec = importlib.util.spec_from_file_location("_bel_under_test", PY_PATH)
    ctl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ctl)
    base_document.get_controller = lambda dt: {DOCTYPE: ctl.BudgetEntryLine}[dt]

    spec = importlib.util.spec_from_file_location("_bel_ch_under_test", CH_PATH)
    ch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ch)
    return types.SimpleNamespace(ctl=ctl, ch=ch, tables=tables, queued=queued,
                                 meta_fields=meta_fields)


def _cycle(env, name, docstatus):
    env.tables["Budget Cycle"].append({"name": name, "docstatus": docstatus})


def _line(env, name, cycle):
    env.tables[DOCTYPE].append({"name": name, "parent": f"BENT-{name}", "cycle": cycle})


def _synced(env):
    filters = env.ch.resolve_sync_filters(DOCTYPE)
    # resolve_sync_filters treats a falsy declaration as none and syncs every
    # row; the declaration must never be empty.
    assert filters, "an empty CH_SYNC_FILTERS falls through to every row"
    return sorted(r["name"] for r in env.tables[DOCTYPE]
                  if _match(r, filters, env.tables))


# --- discovery and the sync gate ---------------------------------------------

def test_reconcile_discovers_it(env):
    assert env.ch._write_through_doctypes() == [DOCTYPE]
    assert env.ctl.BudgetEntryLine.CH_TABLE == "epm_raw.budget_entries"


def test_line_under_an_approved_cycle_syncs(env):
    _cycle(env, "BC-ZZ-1", 1)
    _line(env, "ok", "BC-ZZ-1")
    assert _synced(env) == ["ok"]


@pytest.mark.parametrize("docstatus", [0, 2])
def test_line_under_a_draft_or_cancelled_cycle_is_excluded(env, docstatus):
    _cycle(env, "BC-ZZ-1", docstatus)
    _line(env, "held", "BC-ZZ-1")
    assert _synced(env) == []


def test_line_whose_cycle_is_absent_is_excluded(env):
    _line(env, "orphan", "BC-ZZ-GONE")
    _line(env, "blank", None)
    assert _synced(env) == []


def test_filter_goes_through_the_cycle_link_not_a_list_of_names(env):
    """No approved cycle matches nothing, and the filter is never ``in []``."""
    _cycle(env, "BC-ZZ-1", 0)
    _line(env, "a", "BC-ZZ-1")
    filters = env.ch.resolve_sync_filters(DOCTYPE)
    assert filters == {"cycle.docstatus": 1}
    assert _synced(env) == []


def test_only_the_approved_cycles_lines_sync(env):
    _cycle(env, "BC-ZZ-A", 1)
    _cycle(env, "BC-ZZ-D", 0)
    _line(env, "a", "BC-ZZ-A")
    _line(env, "d", "BC-ZZ-D")
    assert _synced(env) == ["a"]


# --- the field map -----------------------------------------------------------

def test_field_map_carries_the_raw_columns(env):
    fmap = env.ctl.BudgetEntryLine.CH_FIELD_MAP
    assert list(fmap)[:len(RAW_COLUMNS)] == RAW_COLUMNS
    assert fmap["entry_name"] == "parent"
    names = {f.fieldname for f in env.meta_fields}
    for col, field in fmap.items():
        assert field == "parent" or field in names, f"{col} -> {field} is not a field"


def test_field_map_adds_the_sites_budget_dimensions(env):
    env.meta_fields.extend([_Field("dim_region"), _Field("dim_brand"),
                            _Field("dim_section", "Section Break")])
    fmap = env.ctl.BudgetEntryLine.CH_FIELD_MAP
    assert fmap["dim_brand"] == "dim_brand" and fmap["dim_region"] == "dim_region"
    assert "dim_section" not in fmap
    assert list(fmap)[len(RAW_COLUMNS):] == ["dim_brand", "dim_region"]


# --- the header copy ---------------------------------------------------------

def test_stamp_header_copies_the_entry_onto_every_line(env):
    lines = [types.SimpleNamespace(), types.SimpleNamespace()]
    for ln in lines:
        ln.set = lambda f, v, ln=ln: setattr(ln, f, v)
    header = {f: f"v-{f}" for f in HEADER}
    entry = types.SimpleNamespace(get=lambda f: lines if f == "lines" else header.get(f))
    env.ctl.stamp_header(entry)
    for ln in lines:
        assert {f: getattr(ln, f) for f in HEADER} == header


def test_queue_sync_names_the_doctype_and_table(env):
    env.ctl.queue_sync()
    assert len(env.queued) == 1
    assert env.queued[0][:2] == (DOCTYPE, "epm_raw.budget_entries")


# --- the doctype JSON ----------------------------------------------------------

def _fields():
    return {f["fieldname"]: f for f in json.load(open(JSON_PATH))["fields"]}


def test_json_is_a_child_table():
    doc = json.load(open(JSON_PATH))
    assert doc["name"] == DOCTYPE and doc["istable"] == 1 and doc["module"] == "EPM"


def test_json_line_fields():
    f = _fields()
    assert f["main_account"]["fieldtype"] == "Link"
    assert f["main_account"]["options"] == "Main Account"
    assert f["main_account"]["reqd"] == 1
    for amt in ("debit", "credit"):
        assert f[amt]["fieldtype"] == "Currency"
        assert f[amt]["options"] == "currency"
        assert not f[amt].get("reqd")
    assert f["description"]["fieldtype"] == "Data"


def test_json_header_copy_is_read_only_and_hidden():
    f = _fields()
    for name in HEADER:
        assert f[name].get("read_only") == 1, name
        assert f[name].get("hidden") == 1, name
    assert f["cycle"]["fieldtype"] == "Link"
    assert f["cycle"]["options"] == "Budget Cycle"
