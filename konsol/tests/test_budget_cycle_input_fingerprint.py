"""S4 K8 (konsol#348, S4-Q5 a, chosen under the standing rule on 15 Nov 2026):
a Budget Cycle carries the fingerprint of the inputs it approved.

``before_submit`` (the workflow's Approve is ``doc.submit()``) stamps the
read-only ``input_fingerprint`` from the pure model (K7) over the rows the
Cycle approves: its Annual Input rows and Entry Lines, the Spread Profile
weights they use, the approved Constant Rate Sheet of its year (konsol#592
D2, Deepak Pai, 22 Nov 2026: replaces BUD-3's per-Cycle Rate Set) and the
approved Historical Equity Rates of the CONSTANT set. Approve is refused
until that sheet exists, read FOR UPDATE. Nothing is saved in ``on_submit``. Option (b),
the pure model alone until BUD-4's Approved Budget, was rejected as a model
nothing calls.

``konsol.epm.budget_fingerprint.check_input_fingerprint`` is a GET read: it
recomputes over the current rows and reports a mismatch with both values; it
decides nothing and writes nothing. An input changed after Approve therefore
reads as a mismatch (BUD-11-3).

Site-free: the controller and the reader run against a stub frappe whose
``get_all`` serves in-memory tables and records what it was asked.
"""
import ast
import functools
import importlib.util
import os
import sys
import types
from datetime import date, datetime

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CYCLE_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_cycle", "budget_cycle.py")
READER_PY = os.path.join(APP_DIR, "epm", "budget_fingerprint.py")
MODEL_PY = os.path.join(APP_DIR, "epm", "budget_fingerprint_model.py")
CLICKHOUSE = os.path.join(APP_DIR, "clickhouse.py")
BAI_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_annual_input", "budget_annual_input.py")
BEL_PY = os.path.join(APP_DIR, "epm", "doctype", "budget_entry_line", "budget_entry_line.py")

GROUP_RATES = os.path.join(APP_DIR, "group_rates.py")

CYCLE = "BCYC-ZZB-2027"


def _constant_rate_set():
    """konsol.group_rates.CONSTANT_RATE_SET, read from the source: group_rates
    imports too much to load under the stub frappe."""
    with open(GROUP_RATES) as f:
        tree = ast.parse(f.read())
    return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", None) == "CONSTANT_RATE_SET")


class _Thrown(Exception):
    pass


def _match(row, filters):
    for field, cond in (filters or {}).items():
        val = row.get(field)
        if isinstance(cond, (list, tuple)):
            op, arg = cond[0], cond[1]
            if op == "in":
                if val not in arg:
                    return False
            else:
                raise AssertionError(f"unsupported filter {field}: {cond!r}")
        elif val != cond:
            return False
    return True


def _clickhouse_stub(frappe):
    with open(CLICKHOUSE) as f:
        tree = ast.parse(f.read())
    wanted = {"after_commit_once", "sync_doctype_after_commit"}
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    ch = types.ModuleType("konsol.clickhouse")
    ch.frappe = frappe
    ch.functools = functools
    ch.date, ch.datetime = date, datetime
    ch.sync_doctype = lambda *a, **k: None
    exec(compile(ast.Module(body=fns, type_ignores=[]), CLICKHOUSE, "exec"), ch.__dict__)
    return ch


def _tables():
    """A budget Cycle's inputs on a site with Rate Sheets of two bases and
    two years, in every state."""
    sheet = lambda name, basis="Constant", fy=2027, docstatus=1, supersedes=None: {  # noqa: E731
        "name": name, "rate_basis": basis, "fiscal_year": fy, "docstatus": docstatus, "supersedes": supersedes}
    row = lambda parent, rate, idx=1, frm="EUR": {  # noqa: E731
        "parent": parent, "parenttype": "Rate Sheet", "parentfield": "rates", "idx": idx,
        "from_currency": frm, "to_currency": "USD", "quoted_per": 1, "rate": rate}
    return {
        "Budget Cycle": [
            {"name": CYCLE, "scenario_id": "ZZB", "scenario_type": "budget", "fiscal_year": 2027,
             "docstatus": 1, "input_fingerprint": None},
            {"name": "BCYC-ZZB-2026", "scenario_id": "ZZB", "scenario_type": "budget", "fiscal_year": 2026,
             "docstatus": 0, "input_fingerprint": None},
        ],
        "Budget Annual Input": [
            {"name": "BAI-1", "cycle": CYCLE, "data_area_id": "ZZNL", "main_account": "4000", "layer": "base",
             "currency": "EUR", "annual_amount": 1200, "spread_profile_id": "EVEN"},
            {"name": "BAI-2", "cycle": "BCYC-ZZB-2026", "data_area_id": "ZZNL", "main_account": "4000",
             "layer": "base", "currency": "EUR", "annual_amount": 999, "spread_profile_id": "SEASONAL"},
        ],
        "Budget Entry Line": [
            {"name": "BEL-1", "cycle": CYCLE, "data_area_id": "ZZNL", "fiscal_year": 2027, "fiscal_period": 1,
             "main_account": "5000", "layer": "base", "currency": "EUR", "amount_basis": "movement",
             "debit": 100, "credit": 0},
            {"name": "BEL-2", "cycle": "BCYC-ZZB-2026", "data_area_id": "ZZNL", "fiscal_year": 2026,
             "fiscal_period": 1, "main_account": "5000", "layer": "base", "currency": "EUR",
             "amount_basis": "movement", "debit": 7, "credit": 0},
        ],
        "Spread Profile": [{"profile_id": "EVEN", "fiscal_period": p, "weight": 1 / 12} for p in range(1, 13)]
        + [{"profile_id": "SEASONAL", "fiscal_period": 1, "weight": 1.0}],
        "Rate Sheet": [
            sheet("RS-C-2027"),                                     # the Cycle's
            sheet("RS-C-2026", fy=2026),                            # another year
            sheet("RS-S-2027-01", basis="Statutory"),               # another basis
            sheet("RS-C-2027-1", docstatus=2),                      # cancelled
            sheet("RS-C-2027-3", docstatus=0, supersedes="RS-C-2027"),  # a draft correction
        ],
        "Rate Sheet Row": [
            row("RS-C-2027", "1.08"), row("RS-C-2027", "1.30", idx=2, frm="GBP"),
            row("RS-C-2026", "1.07"), row("RS-S-2027-01", "1.20"), row("RS-C-2027-1", "1.01"),
            row("RS-C-2027-3", "1.09"),
        ],
        "Ownership Period": [
            {"name": "OP-1", "consolidation_group": "ZZGRP", "data_area_id": "ZZNL", "owner_group": "ZZGRP",
             "owner_data_area_id": "", "ownership_pct": 80, "consolidation_method": "full",
             "effective_date": date(2024, 1, 1), "end_date": date(9999, 12, 31),
             "acquisition_date": date(2024, 1, 1), "is_first_acquisition": 1, "is_disposal": 0,
             "disposal_date": date(9999, 12, 31), "supersedes": None, "superseded_end_date": None,
             "docstatus": 1},
            {"name": "OP-2", "consolidation_group": "ZZGRP", "data_area_id": "ZZXX", "owner_group": "ZZGRP",
             "owner_data_area_id": "", "ownership_pct": 50, "consolidation_method": "full",
             "effective_date": date(2024, 1, 1), "end_date": date(9999, 12, 31), "docstatus": 1},
            {"name": "OP-3", "consolidation_group": "ZZGRP", "data_area_id": "ZZNL", "owner_group": "ZZGRP",
             "owner_data_area_id": "", "ownership_pct": 10, "consolidation_method": "full",
             "effective_date": date(2026, 1, 1), "end_date": date(9999, 12, 31), "docstatus": 0},
        ],
        "Historical Equity Rate": [
            {"consolidation_group": "ZZGRP", "data_area_id": "ZZNL", "main_account": "3000",
             "rate_date": date(2024, 1, 1), "to_currency": "USD", "historical_rate": "1.05",
             "rate_set": "CONSTANT", "docstatus": 1},
            {"consolidation_group": "ZZGRP", "data_area_id": "ZZNL", "main_account": "3000",
             "rate_date": date(2024, 1, 1), "to_currency": "USD", "historical_rate": "1.25",
             "rate_set": "ACTUAL", "docstatus": 1},
            {"consolidation_group": "ZZGRP", "data_area_id": "ZZNL", "main_account": "3000",
             "rate_date": date(2025, 1, 1), "to_currency": "USD", "historical_rate": "1.06",
             "rate_set": "CONSTANT", "docstatus": 2},
        ],
    }


@pytest.fixture
def env(monkeypatch):
    tables = _tables()
    asked = []
    queue = []
    allowed = [True]

    class Document:
        def __init__(self, **kw):
            self.__dict__.update(kw)

        def get(self, key, default=None):
            return self.__dict__.get(key, default)

    def get_all(doctype, filters=None, fields=None, limit_page_length=None, order_by=None, **k):
        asked.append((doctype, dict(filters or {})))
        rows = [r for r in tables.get(doctype, []) if _match(r, filters)]
        return [types.SimpleNamespace(**r) if False else _Row(r, fields) for r in rows]

    class _Row(dict):
        def __init__(self, row, fields):
            super().__init__({f: row.get(f) for f in fields} if fields else row)

        def __getattr__(self, key):
            try:
                return self[key]
            except KeyError:
                raise AttributeError(key)

    locked = []

    def get_values(doctype, filters=None, fieldname=None, as_dict=False, order_by=None, for_update=False,
                   **k):
        """frappe.db.get_values: the reader's locking read when ``for_update``."""
        asked.append((doctype, dict(filters or {})))
        if for_update:
            locked.append(doctype)
        fields = [fieldname] if isinstance(fieldname, str) else list(fieldname)
        rows = [r for r in tables.get(doctype, []) if _match(r, filters)]
        key = (order_by or "name asc").split()[0]
        rows.sort(key=lambda r: r.get(key) if r.get(key) is not None else "")
        return [_Row(r, fields) for r in rows]

    def throw(msg, exc=None, *a, **k):
        raise _Thrown(msg)

    frappe = types.ModuleType("frappe")
    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.ValidationError = _Thrown
    frappe.PermissionError = _Thrown
    frappe.whitelist = lambda *a, **k: (lambda fn: fn)
    frappe.session = types.SimpleNamespace(user="Administrator")
    frappe.flags = types.SimpleNamespace(in_install=False, in_import=False, in_migrate=False, in_patch=False)
    frappe.db = types.SimpleNamespace(after_commit=types.SimpleNamespace(_functions=queue, add=queue.append),
                                      count=lambda *a, **k: 0, exists=lambda dt, name: False,
                                      get_values=get_values)
    frappe.get_all = get_all
    frappe.get_meta = lambda dt: types.SimpleNamespace(fields=[])
    frappe.has_permission = lambda doctype, ptype="read", doc=None, **k: allowed[0]
    docs = {}
    frappe.get_doc = lambda doctype, name: docs[name]
    model = types.ModuleType("frappe.model")
    docmod = types.ModuleType("frappe.model.document")
    docmod.Document = Document
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = lambda: "2026-11-15 00:00:00"
    grain = types.ModuleType("konsol.epm.budget_grain")
    grain.digest_name = lambda prefix, parts: prefix + "-" + "-".join(str(p) for p in parts)
    scenario = types.ModuleType("konsol.epm.doctype.scenario.scenario")
    scenario.locked_scenario_type = lambda s: "budget"
    intake = types.ModuleType("konsol.budget_intake")
    intake.lock_cycles = lambda *a, **k: []
    monkeypatch.setattr(sys.modules["konsol"], "budget_intake", intake, raising=False)
    group_rates = types.ModuleType("konsol.group_rates")
    group_rates.CONSTANT_RATE_SET = _constant_rate_set()
    monkeypatch.setattr(sys.modules["konsol"], "group_rates", group_rates, raising=False)

    for name, mod in {
        "frappe": frappe, "frappe.model": model, "frappe.model.document": docmod,
        "frappe.utils": utils, "konsol.clickhouse": _clickhouse_stub(frappe),
        "konsol.epm.budget_grain": grain,
        "konsol.epm.doctype.scenario.scenario": scenario,
        "konsol.budget_intake": intake,
        "konsol.group_rates": group_rates,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    for modname, path in (("konsol.epm.doctype.budget_annual_input.budget_annual_input", BAI_PY),
                          ("konsol.epm.doctype.budget_entry_line.budget_entry_line", BEL_PY),
                          ("konsol.epm.budget_fingerprint_model", MODEL_PY),
                          ("konsol.epm.budget_fingerprint", READER_PY)):
        spec = importlib.util.spec_from_file_location(modname, path)
        m = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, modname, m)
        spec.loader.exec_module(m)
    monkeypatch.setattr(sys.modules["konsol.epm"], "budget_fingerprint_model",
                        sys.modules["konsol.epm.budget_fingerprint_model"], raising=False)
    monkeypatch.setattr(sys.modules["konsol.epm"], "budget_fingerprint",
                        sys.modules["konsol.epm.budget_fingerprint"], raising=False)

    spec = importlib.util.spec_from_file_location("_budget_cycle_k8_under_test", CYCLE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    def cycle(name=CYCLE):
        row = next(r for r in tables["Budget Cycle"] if r["name"] == name)
        doc = mod.BudgetCycle(doctype="Budget Cycle", **row)
        doc.save = lambda *a, **k: pytest.fail("before_submit/on_submit must not save")
        docs[name] = doc
        return doc

    return types.SimpleNamespace(cycle=cycle, tables=tables, asked=asked, queue=queue, allowed=allowed,
                                 locked=locked,
                                 reader=sys.modules["konsol.epm.budget_fingerprint"],
                                 model=sys.modules["konsol.epm.budget_fingerprint_model"])


# ---- the stamp ---------------------------------------------------------------------

def test_approve_stamps_the_fingerprint_of_the_inputs_without_a_save(env):
    doc = env.cycle()
    assert not doc.get("input_fingerprint")
    doc.before_submit()
    assert doc.input_fingerprint == env.reader.compute(doc)
    assert doc.input_fingerprint.startswith(env.model.VERSION + ":")
    doc.on_submit()  # queues syncs; never saves (doc.save fails the test)


def test_a_draft_save_stamps_nothing(env):
    doc = env.cycle()
    doc.validate()
    doc.on_update() if hasattr(doc, "on_update") else None
    assert not doc.get("input_fingerprint")


# ---- what is read --------------------------------------------------------------------

def test_the_reader_takes_the_cycles_rows_and_its_years_approved_constant_sheet(env):
    doc = env.cycle()
    inputs = env.reader.read_inputs(doc)
    assert "rate_set" not in inputs and "exchange_rates" not in inputs
    assert [s["name"] for s in inputs["rate_sheets"]] == ["RS-C-2027"], (
        "the approved Constant sheet of the Cycle's year only")
    assert [(r["from_currency"], str(r["rate"])) for r in inputs["rate_sheets"][0]["rows"]] == [
        ("EUR", "1.08"), ("GBP", "1.30")]
    assert [r["name"] for r in inputs["annual_inputs"]] == ["BAI-1"]
    assert [r["name"] for r in inputs["entry_lines"]] == ["BEL-1"]
    assert {r["profile_id"] for r in inputs["weights"]} == {"EVEN"}, "only the profiles the annual rows use"
    assert [str(r["historical_rate"]) for r in inputs["historical_rates"]] == ["1.05"], (
        "approved tranches of the CONSTANT set only")
    sheets = next(f for dt, f in env.asked if dt == "Rate Sheet")
    assert sheets == {"docstatus": 1, "rate_basis": "Constant", "fiscal_year": 2027}
    her = next(f for dt, f in env.asked if dt == "Historical Equity Rate")
    assert her == {"docstatus": 1, "rate_set": "CONSTANT"}
    assert not [dt for dt, f in env.asked if dt == "Group Exchange Rate"]
    assert env.locked == [], "a plain read outside the approval"
    for dt in ("Budget Annual Input", "Budget Entry Line"):
        assert next(f for d, f in env.asked if d == dt) == {"cycle": CYCLE}, dt


def test_no_profile_in_use_asks_for_no_weights(env):
    """Never ``["in", []]``: that filter matches nothing in a way Frappe has
    read as everything."""
    env.tables["Budget Annual Input"] = []
    env.reader.read_inputs(env.cycle())
    assert not [f for dt, f in env.asked if dt == "Spread Profile"]


# ---- the check -------------------------------------------------------------------------

def test_the_check_matches_right_after_approve(env):
    doc = env.cycle()
    doc.before_submit()
    out = env.reader.check_input_fingerprint(CYCLE)
    assert out["matches"] is True
    assert out["stored"] == out["current"] == doc.input_fingerprint
    assert out["cycle"] == CYCLE


def test_an_annual_amount_changed_after_approve_is_reported_as_a_mismatch(env):
    doc = env.cycle()
    doc.before_submit()
    stored = doc.input_fingerprint
    env.tables["Budget Annual Input"][0]["annual_amount"] = 1300
    out = env.reader.check_input_fingerprint(CYCLE)
    assert out["matches"] is False
    assert out["stored"] == stored and out["current"] != stored
    assert "changed" in out["message"].lower() and CYCLE in out["message"]


def test_another_years_or_bases_sheet_changed_after_approve_is_no_mismatch(env):
    doc = env.cycle()
    doc.before_submit()
    rows = env.tables["Rate Sheet Row"]
    rows[2]["rate"] = "1.70"   # RS-C-2026
    rows[3]["rate"] = "1.70"   # RS-S-2027-01
    rows[5]["rate"] = "1.70"   # the draft correction
    assert env.reader.check_input_fingerprint(CYCLE)["matches"] is True


def test_the_constant_sheet_superseded_after_approve_is_a_mismatch_even_at_the_same_rates(env):
    """konsol#592: the correction's approval cancels the sheet it supersedes
    in the same transaction. The stamp then reads stale, rates unchanged."""
    doc = env.cycle()
    doc.before_submit()
    env.tables["Rate Sheet"][0]["docstatus"] = 2
    env.tables["Rate Sheet"].append({"name": "RS-C-2027-2", "rate_basis": "Constant", "fiscal_year": 2027,
                                     "docstatus": 1, "supersedes": "RS-C-2027"})
    env.tables["Rate Sheet Row"] += [dict(r, parent="RS-C-2027-2") for r in env.tables["Rate Sheet Row"][:2]]
    out = env.reader.check_input_fingerprint(CYCLE)
    assert out["matches"] is False
    assert "Constant" in out["message"]


# ---- the approval needs the year's Constant sheet (konsol#592 D2) -------------------

def test_approve_is_refused_without_an_approved_constant_sheet_for_the_year(env):
    env.tables["Rate Sheet"][0]["docstatus"] = 2   # RS-C-2027 cancelled: FY2027 has none
    doc = env.cycle()
    with pytest.raises(_Thrown) as raised:
        doc.before_submit()
    assert "approve the Constant rate sheet RS-C-2027 first (konsol#592)" in str(raised.value)
    assert not doc.get("input_fingerprint"), "refused before the stamp"


def test_approve_reads_the_constant_sheet_with_a_locking_read(env):
    doc = env.cycle()
    doc.before_submit()
    assert "Rate Sheet" in env.locked
    assert doc.input_fingerprint == env.reader.compute(doc)


def test_a_draft_cycle_without_a_sheet_is_saved_and_checked(env):
    """Only the approval needs the sheet: a draft is worked on before the
    year's rates are agreed."""
    env.tables["Rate Sheet"][0]["docstatus"] = 0
    doc = env.cycle()
    doc.validate()
    out = env.reader.check_input_fingerprint(CYCLE)
    assert out["current"].startswith(env.model.VERSION + ":")


def test_a_cycle_not_yet_approved_has_nothing_to_match(env):
    env.cycle("BCYC-ZZB-2026")
    out = env.reader.check_input_fingerprint("BCYC-ZZB-2026")
    assert out["matches"] is None and out["stored"] in (None, "")
    assert out["current"].startswith(env.model.VERSION + ":")
    assert "not approved" in out["message"].lower()


def test_the_check_needs_read_permission_on_the_cycle(env):
    env.cycle()
    env.allowed[0] = False
    with pytest.raises(_Thrown):
        env.reader.check_input_fingerprint(CYCLE)


def test_the_check_is_a_get_that_writes_nothing():
    with open(READER_PY) as f:
        src = f.read()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check_input_fingerprint")
    decorators = [ast.get_source_segment(src, d) for d in fn.decorator_list]
    assert decorators == ['frappe.whitelist(methods=["GET"])'], decorators
    for forbidden in ("set_value", ".save(", ".submit(", "db.sql(", "db.commit", "insert("):
        assert forbidden not in src, forbidden


# ---- ownership (konsol#552, Deepak Pai, 18 Nov 2026) ----------------------------------

def test_the_reader_takes_the_approved_links_of_the_cycles_entities(env):
    inputs = env.reader.read_inputs(env.cycle())
    assert [r["name"] for r in inputs["ownership_links"]] == ["OP-1", "OP-2"], "every submitted link"
    asked = [f for dt, f in env.asked if dt == "Ownership Period"]
    assert asked and all(f["docstatus"] == 1 for f in asked)


def test_an_ownership_exit_recorded_after_approve_is_a_mismatch(env):
    doc = env.cycle()
    doc.before_submit()
    assert env.reader.check_input_fingerprint(CYCLE)["matches"] is True
    env.tables["Ownership Period"][1]["end_date"] = date(2027, 3, 31)   # ZZXX (a holding, say) exits
    assert env.reader.check_input_fingerprint(CYCLE)["matches"] is False
    doc.before_submit()
    env.tables["Ownership Period"][0]["end_date"] = date(2027, 3, 31)   # ZZNL exits
    assert env.reader.check_input_fingerprint(CYCLE)["matches"] is False
