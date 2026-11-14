"""S2-U1 and U2: konsol/budget_bulk.py, the budget check and load (konsol#348).

check_file / check_upload read the attached file, hand the table to
budget_intake.plan, and write the counts and the report on the Budget Upload.
They write no Budget Entry. load re-checks and queues run_load after the
commit; run_load replaces each ready (cycle, entity, layer) slice with one
Budget Entry per period. budget_bulk imports frappe, so it is loaded against
stubs; the plan is a stub that returns a canned report and groups, and the
Budget Entries live in a dict.
"""
import importlib.util
import json
import os
import sys
import types
from collections import namedtuple

import pytest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY_PATH = os.path.join(APP_DIR, "budget_bulk.py")


class Denied(Exception):
    pass


class Thrown(Exception):
    pass


CYCLE = "CYC-BUD-2027"


def _item(entity, layer, ok=True, errors=(), period=1, rows=2):
    return {"scenario": "BUD", "fiscal_year": 2027, "entity": entity, "layer": layer,
            "fiscal_period": period, "rows": rows, "errors": list(errors), "ok": ok and not errors,
            "cycle": CYCLE, "currency": "EUR", "amount_basis": "Period movement"}


def _rows(*accounts):
    return [{"main_account": a, "debit": 100.0 + i, "credit": 0.0, "currency": "EUR", "description": f"d{i}",
             "amount_basis": "", "dim_cost_center": "CC1", "line": i + 2} for i, a in enumerate(accounts)]


def _file(env, slices, refused=()):
    """Set the plan to return one group per (entity, layer, period) in
    `slices`; a key in `refused` comes back with an error."""
    groups, report = {}, []
    for entity, layer, period in slices:
        key = Key("BUD", 2027, entity, layer, period)
        groups[key] = _rows("4000", "5000")
        errors = [f"refused {entity} {layer} P{period}"] if (entity, layer, period) in refused else []
        report.append(_item(entity, layer, errors=errors, period=period))
    env.state.plan_groups, env.state.plan_report = groups, report


class FakeUpload:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.name = "BU-0001"
        self.owner = "ann@example.com"
        self.report = None
        self.saved = 0

    def insert(self):
        self.log.append(("insert", "Budget Upload"))
        return self

    def save(self):
        self.saved += 1
        return self

    def check_permission(self, ptype):
        self.log.append(("check_permission", ptype))


Key = namedtuple("Key", "scenario fiscal_year data_area_id layer fiscal_period")

#: The running test's state, for FakeEntry (set by the env fixture).
_STATE = []


class FakeEntry:
    def __init__(self, store, log, **kw):
        self.__dict__.update(kw)
        self._store, self._log = store, log
        self._second_writer = _STATE[0].second_writer if _STATE else set()
        self.lines = list(kw.get("lines") or [])
        self.name = self.__dict__.get("name") or f"BENT-{kw['cycle']}-{kw['data_area_id']}-{kw['layer']}-{kw['fiscal_period']}"

    def get(self, field, default=None):
        return getattr(self, field, default)

    def set(self, field, value):
        setattr(self, field, list(value))

    def append(self, field, row):
        getattr(self, field).append(dict(row))

    def _validate(self):
        # Budget Entry's validate takes the Cycle lock and refuses a non-draft.
        sys.modules["konsol.epm.doctype.budget_entry.budget_entry"].assert_cycle_draft(self.cycle)
        # ... and refuses a second amount basis in its (cycle, entity, layer)
        # (Q15-c), except from the entries the same write is replacing.
        replacing = sys.modules["frappe"].flags.get("budget_entry_replacing") or ()
        for other in self._store.values():
            if (other.name != self.name and other.name not in replacing
                    and (other.cycle, other.data_area_id, other.layer) == (self.cycle, self.data_area_id, self.layer)
                    and other.amount_basis != self.amount_basis):
                raise Thrown(f"Budget Entry {other.name} holds amount basis {other.amount_basis!r}")

    def _refuse_second_writer(self):
        # Budget Entry's Q16-c check: an Annual Input committed since the job
        # judged the file gives one of this entry's grains.
        if self.data_area_id in self._second_writer:
            intake = sys.modules["konsol.budget_intake"]
            raise intake.SecondWriterError(f"Budget Annual Input BAI-9 already gives account 4000 for "
                                           f"entity {self.data_area_id}")

    def insert(self):
        self._validate()
        self._refuse_second_writer()
        self._log.append(("judged", bool(sys.modules["frappe"].flags.get("budget_entry_judged"))))
        assert self.name not in self._store, "the same key twice"
        self._store[self.name] = self
        self._log.append(("insert", "Budget Entry", self.name))
        return self

    def save(self):
        self._validate()
        self._refuse_second_writer()
        self._log.append(("judged", bool(sys.modules["frappe"].flags.get("budget_entry_judged"))))
        self._log.append(("save", "Budget Entry", self.name))
        return self

    def check_permission(self, ptype):
        self._log.append(("check_permission", "Budget Entry", self.name, ptype))


@pytest.fixture
def env(monkeypatch):
    log = []
    state = types.SimpleNamespace(can_create=True, table=[["h"], ["r"]], plan_report=None,
                                  plan_groups=None, plan_raises=None, plan_calls=[], doc=None,
                                  entries={}, enqueued=[], commits=0, rollbacks=0, job_running=False,
                                  denied_accounts=set(), fail_on=set(), deleted=[],
                                  plan_lines=[], approve_at_commit=None, cycle_approved=False,
                                  cycle_state="approved", hidden=set(), after_plan=None, existing_reads=[],
                                  second_writer=set())

    _STATE[:] = [state]

    def get_doc(arg, name=None, for_update=False):
        if isinstance(arg, dict):
            if arg.get("doctype") == "Budget Entry":
                for k in ("cycle", "data_area_id", "layer", "fiscal_period"):
                    assert arg.get(k) not in (None, ""), k
                return FakeEntry(state.entries, log, **{k: v for k, v in arg.items() if k != "doctype"})
            doc = FakeUpload(**{k: v for k, v in arg.items()})
        elif arg == "Budget Entry":
            return state.entries[name]
        else:
            doc = state.doc
        doc.log = log
        state.doc = doc
        return doc

    def _match(entry, filters):
        for k, v in (filters or {}).items():
            got = getattr(entry, k, None)
            if isinstance(v, list) and v[0] == "in":
                if got not in v[1]:
                    return False
            elif got != v:
                return False
        return True

    def get_all(doctype, filters=None, fields=None, pluck=None, **kw):
        assert doctype == "Budget Entry", doctype
        assert kw.get("order_by"), "every list read states its order"
        rows = sorted((e for e in state.entries.values() if _match(e, filters) and e.name not in state.hidden),
                      key=lambda e: e.name)
        if pluck:
            return [getattr(e, pluck) for e in rows]
        return [types.SimpleNamespace(**{f: getattr(e, f) for f in fields}) for e in rows]

    def delete_doc(doctype, name, **kw):
        assert doctype == "Budget Entry"
        state.deleted.append((name, kw))
        log.append(("delete", doctype, name))
        del state.entries[name]

    frappe = types.ModuleType("frappe")

    def throw(msg, exc=None, *a, **k):
        raise (exc or Thrown)(msg)

    frappe.throw = throw
    frappe.PermissionError = Denied
    frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})
    frappe.whitelist = lambda **kw: (lambda fn: setattr(fn, "_whitelist", kw) or fn)
    frappe.get_doc = get_doc
    frappe.get_all = get_all
    frappe.delete_doc = delete_doc
    frappe.has_permission = lambda doctype, ptype=None, **k: state.can_create
    frappe.enqueue = lambda method, **kw: state.enqueued.append((method, kw))
    frappe.clear_messages = lambda: None
    frappe.flags = {}
    frappe.log_error = lambda *a, **k: log.append(("log_error", k.get("title")))
    snapshot = {}

    def commit():
        state.commits += 1
        state.hidden.clear()   # a new snapshot sees what others committed
        if state.approve_at_commit == state.commits:
            # Another session submits the Cycle; it commits here.
            state.cycle_approved = True
        snapshot.clear()
        snapshot.update({n: (e, list(e.lines)) for n, e in state.entries.items()})

    def rollback():
        state.rollbacks += 1
        state.entries.clear()
        for n, (e, lines) in snapshot.items():
            e.lines = list(lines)
            state.entries[n] = e

    def set_value(doctype, name, values, update_modified=True):
        assert doctype == "Budget Upload" and name == state.doc.name
        state.doc.__dict__.update(values)

    def get_values(doctype, filters, fields, as_dict=False, order_by=None, for_update=False, **kw):
        # REPEATABLE READ: a plain read misses `hidden` (committed by another
        # session since this transaction's snapshot); a locking read sees it.
        assert doctype == "Budget Entry" and as_dict and order_by, (doctype, as_dict, order_by)
        state.existing_reads.append(for_update)
        rows = sorted((e for e in state.entries.values() if _match(e, filters)
                       and (for_update or e.name not in state.hidden)), key=lambda e: e.name)
        return [types.SimpleNamespace(**{f: getattr(e, f) for f in fields}) for e in rows]

    frappe.db = types.SimpleNamespace(commit=commit, rollback=rollback, set_value=set_value,
                                      is_deadlocked=lambda e: False, get_values=get_values)
    utils = types.ModuleType("frappe.utils")
    utils.cint = lambda v: int(v or 0)
    utils.strip_html = lambda s: s
    frappe.utils = utils
    jobs = types.ModuleType("frappe.utils.background_jobs")
    jobs.is_job_enqueued = lambda job_id: state.job_running

    tb = types.ModuleType("konsol.tb_bulk")
    tb._read_table = lambda url: state.table

    intake = types.ModuleType("konsol.budget_intake")

    def plan(table, *, form_basis, name_lines):
        state.plan_calls.append((table, form_basis))
        state.plan_lines.append(name_lines)
        if state.plan_raises:
            raise state.plan_raises
        report = [dict(r, errors=list(r["errors"])) for r in state.plan_report]
        if state.after_plan:
            state.after_plan()
        groups = state.plan_groups or {}
        return {"groups": groups, "report": report, "ok": all(r["ok"] for r in report)}
    intake.plan = plan
    intake.SecondWriterError = type("SecondWriterError", (Thrown,), {})

    api = types.ModuleType("konsol.api")

    def access(data):
        if data.get("main_account") in state.denied_accounts:
            raise Denied(f"No access to account {data['main_account']}")
        if data.get("main_account") in state.fail_on:
            raise RuntimeError(f"boom on {data['main_account']}")
    api._assert_budget_write_access = access

    # The Budget Entry controller's guard, as the load calls it.
    import contextlib
    entry_mod = types.ModuleType("konsol.epm.doctype.budget_entry.budget_entry")
    entry_mod.CycleNotDraftError = type("CycleNotDraftError", (Thrown,), {})

    def assert_cycle_draft(cycle, action="changed", missing_ok=False):
        log.append(("lock cycle", cycle))
        if state.cycle_approved:
            raise entry_mod.CycleNotDraftError(f"Budget Cycle {cycle} is {state.cycle_state}: a Budget Entry is "
                                               f"{action} only while its Cycle is a draft.")
    entry_mod.assert_cycle_draft = assert_cycle_draft

    @contextlib.contextmanager
    def judged_by_writer():
        was = frappe.flags.get("budget_entry_judged")
        frappe.flags["budget_entry_judged"] = True
        try:
            yield
        finally:
            frappe.flags["budget_entry_judged"] = was
    entry_mod.judged_by_writer = judged_by_writer

    @contextlib.contextmanager
    def replacing_entries(names):
        was = frappe.flags.get("budget_entry_replacing")
        frappe.flags["budget_entry_replacing"] = frozenset(names)
        try:
            yield
        finally:
            frappe.flags["budget_entry_replacing"] = was
    entry_mod.replacing_entries = replacing_entries

    timeouts = types.ModuleType("rq.timeouts")
    timeouts.BaseTimeoutException = type("BaseTimeoutException", (Exception,), {})

    for name, mod in {"frappe": frappe, "frappe.utils": utils, "frappe.utils.background_jobs": jobs,
                      "konsol.tb_bulk": tb, "konsol.budget_intake": intake, "konsol.api": api,
                      "konsol.epm.doctype.budget_entry.budget_entry": entry_mod,
                      "rq": types.ModuleType("rq"), "rq.timeouts": timeouts}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    spec = importlib.util.spec_from_file_location("_budget_bulk_under_test", PY_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return types.SimpleNamespace(mod=mod, state=state, log=log, frappe=frappe)


def test_the_endpoints_are_post_only(env):
    assert env.mod.check_file._whitelist == {"methods": ["POST"]}
    assert env.mod.check_upload._whitelist == {"methods": ["POST"]}


def test_check_file_writes_counts_and_report_and_names_the_refusal(env):
    refusal = "Layer board takes rows only from holders of the Budget Approver role"
    env.state.plan_report = [_item("E1", "base"), _item("E2", "board", errors=[refusal]),
                             _item("E1", "board")]
    out = env.mod.check_file("/files/b.csv", "Period movement")
    doc = env.state.doc
    assert (doc.status, doc.group_count, doc.valid_count, doc.total_rows, doc.error) == \
        ("Checked", 3, 2, 6, "")
    assert doc.failed_count == 0 and out["loaded_count"] == 0
    stored = json.loads(doc.report)
    assert [r["ok"] for r in stored] == [True, False, True]
    assert stored[1]["errors"] == [refusal]
    assert out["report"] == stored and out["valid_count"] == 2 and out["status"] == "Checked"
    assert env.state.plan_calls == [(env.state.table, "Period movement")]


def test_check_file_reports_an_access_denial_and_raises_nothing(env):
    # S2-X3: the API raises a PermissionError for a denial; a CSV check keeps
    # it in the report, beside every other refusal of the file.
    denial = "Line 3: No access to account 9999"
    item = dict(_item("E2", "base", errors=[denial]), denials=[denial])
    env.state.plan_report = [_item("E1", "base"), item]
    out = env.mod.check_file("/files/b.csv", "Period movement")
    stored = json.loads(env.state.doc.report)
    assert env.state.doc.status == "Checked"
    assert stored[1]["errors"] == [denial] and not stored[1]["ok"]
    assert out["valid_count"] == 1


def test_the_file_report_names_its_lines(env):
    # S2-X3: a file's refusals keep their line numbers; the check and the
    # load both ask the intake for them.
    env.state.plan_report = [_item("E1", "base")]
    env.mod.check_file("/files/b.csv", "Period movement")
    assert env.state.plan_lines == [True]


def test_check_file_writes_no_budget_entry(env):
    env.state.plan_report = [_item("E1", "base")]
    env.mod.check_file("/files/b.csv")
    assert env.log == [("insert", "Budget Upload")]
    assert env.state.plan_calls[0][1] == ""


def test_a_structural_problem_fails_the_upload_with_the_message(env):
    env.state.plan_raises = ValueError("Missing column: scenario")
    env.mod.check_file("/files/b.csv")
    doc = env.state.doc
    assert (doc.status, doc.error, doc.report) == ("Failed", "Missing column: scenario", "[]")
    assert doc.group_count == doc.valid_count == doc.total_rows == 0


def test_a_user_who_may_not_create_uploads_is_refused(env):
    env.state.can_create = False
    with pytest.raises(Denied):
        env.mod.check_file("/files/b.csv")
    assert env.log == []


def test_check_upload_checks_a_saved_draft_in_place(env):
    env.state.plan_report = [_item("E1", "base")]
    env.state.doc = FakeUpload(status="Draft", upload_file="/files/b.csv", amount_basis="")
    env.mod.check_upload("BU-0001", amount_basis="Year-to-date movement")
    assert env.state.doc.amount_basis == "Year-to-date movement"
    assert env.state.doc.status == "Checked"
    assert ("check_permission", "write") in env.log
    assert env.state.plan_calls[0][1] == "Year-to-date movement"


def test_check_upload_refuses_a_loading_or_loaded_upload(env):
    env.state.doc = FakeUpload(status="Loaded", upload_file="/files/b.csv", amount_basis="")
    with pytest.raises(Thrown):
        env.mod.check_upload("BU-0001")


# ── U2: load ───────────────────────────────────────────────────────────────

FOUR = [(e, l, p) for e in ("E1", "E2") for l in ("base", "board") for p in (1, 2)]


def _checked(env, status="Checked"):
    env.state.doc = FakeUpload(status=status, upload_file="/files/b.csv", amount_basis="Period movement",
                               report=None, failed_count=0, error="", group_count=0, valid_count=0,
                               total_rows=0)
    env.state.doc.log = env.log
    return env.state.doc


def test_load_is_post_only(env):
    assert env.mod.load._whitelist == {"methods": ["POST"]}


def test_load_queues_the_job_after_commit_with_a_deduplicated_job_id(env):
    _file(env, FOUR)
    doc = _checked(env)
    out = env.mod.load(doc.name)
    assert doc.status == "Loading" and out["refused"] is None
    [(method, kw)] = env.state.enqueued
    assert method == "konsol.budget_bulk.run_load"
    assert kw["enqueue_after_commit"] is True and kw["deduplicate"] is True
    assert kw["job_id"] == f"konsol-budget-upload::{doc.name}"
    assert kw["upload"] == doc.name
    # Only the job's own argument is a job kwarg (CLAUDE.md: enqueue's own
    # parameters cannot be).
    reserved = {"method", "queue", "timeout", "event", "is_async", "job_name", "now", "enqueue_after_commit",
                "on_success", "on_failure", "at_front", "job_id", "deduplicate"}
    assert set(kw) - reserved == {"upload", "expected"}
    assert sorted(map(tuple, kw["expected"])) == sorted(("BUD", 2027, e, l, p) for e, l, p in FOUR)
    assert env.state.entries == {}, "load itself writes no Budget Entry"


def test_load_refuses_a_file_with_problems_unless_told_to_skip_them(env):
    _file(env, FOUR, refused={("E2", "board", 1)})
    doc = _checked(env)
    out = env.mod.load(doc.name)
    assert out["refused"] and out["refused_kind"] == "problems" and env.state.enqueued == []
    out = env.mod.load(doc.name, skip_invalid=1)
    assert out["refused"] is None and len(env.state.enqueued) == 1


def test_load_refuses_when_nothing_is_ready(env):
    _file(env, [("E1", "base", 1)], refused={("E1", "base", 1)})
    doc = _checked(env)
    out = env.mod.load(doc.name, skip_invalid=1)
    assert out["refused"] and env.state.enqueued == []


def test_load_refuses_while_the_job_runs_and_once_loaded(env):
    _file(env, FOUR)
    doc = _checked(env, status="Loading")
    env.state.job_running = True
    with pytest.raises(Thrown):
        env.mod.load(doc.name)
    doc.status = "Loaded"
    with pytest.raises(Thrown):
        env.mod.load(doc.name)
    assert env.state.enqueued == []


def test_load_needs_write_on_the_upload(env):
    _file(env, FOUR)
    doc = _checked(env)
    env.mod.load(doc.name)
    assert ("check_permission", "write") in env.log


def test_a_refused_period_holds_back_its_whole_slice(env):
    """A slice (cycle, entity, layer) is replaced whole, so one refused period
    stops the slice's other periods too, and the report says why."""
    _file(env, FOUR, refused={("E2", "board", 1)})
    out = env.mod.check_file("/files/b.csv", "Period movement")
    held = [r for r in out["report"] if (r["entity"], r["layer"], r["fiscal_period"]) == ("E2", "board", 2)][0]
    assert held["ok"] is False
    assert any("E2" in e and "board" in e and "period 1" in e for e in held["errors"])
    assert out["valid_count"] == 6


def _job(env):
    """Run the last queued load job with the kwargs it was queued with."""
    _method, kw = env.state.enqueued[-1]
    reserved = {"queue", "timeout", "job_id", "deduplicate", "enqueue_after_commit"}
    env.mod.run_load(**{k: v for k, v in kw.items() if k not in reserved})


def _run(env, slices, refused=()):
    _file(env, slices, refused)
    doc = env.state.doc if env.state.doc and env.state.doc.status == "Checked" else _checked(env)
    env.mod.load(doc.name, skip_invalid=1)
    _job(env)
    return doc


def test_loading_a_refused_file_writes_nothing(env):
    _file(env, [("E1", "base", 1)], refused={("E1", "base", 1)})
    doc = _checked(env)
    doc.status = "Loading"
    env.mod.run_load(doc.name, expected=[])
    assert env.state.entries == {}
    assert doc.status == "Failed"


def test_a_load_writes_one_entry_per_cycle_entity_layer_period(env):
    doc = _run(env, FOUR)
    assert len(env.state.entries) == 8
    for e in env.state.entries.values():
        assert e.cycle == CYCLE and e.source == "Upload" and e.budget_upload == doc.name
        assert e.currency == "EUR" and e.amount_basis == "Period movement"
        assert [line["main_account"] for line in e.lines] == ["4000", "5000"]
        assert e.lines[0] == {"main_account": "4000", "debit": 100.0, "credit": 0.0, "description": "d0",
                              "dim_cost_center": "CC1"}
    assert doc.status == "Loaded" and doc.failed_count == 0
    assert env.mod.upload_state(doc)["loaded_count"] == 8
    assert all(r.get("loaded") for r in json.loads(doc.report))


def test_a_reload_replaces_and_never_doubles(env):
    doc = _run(env, FOUR)
    names = set(env.state.entries)
    doc2 = _checked(env)
    doc2.name = "BU-0002"
    _run(env, FOUR)
    assert set(env.state.entries) == names
    assert all(len(e.lines) == 2 for e in env.state.entries.values())
    assert all(e.budget_upload == "BU-0002" for e in env.state.entries.values())
    assert doc2.status == "Loaded"


def test_a_reload_removes_the_slice_periods_absent_from_the_file_only(env):
    _run(env, FOUR)
    _checked(env).name = "BU-0002"
    # E1 base now holds period 1 only; every other slice keeps both periods.
    smaller = [k for k in FOUR if k != ("E1", "base", 2)]
    doc = _run(env, smaller)
    periods = sorted((e.data_area_id, e.layer, e.fiscal_period) for e in env.state.entries.values())
    assert periods == sorted(smaller)
    assert [d[0] for d in env.state.deleted] == [f"BENT-{CYCLE}-E1-base-2"]
    assert doc.status == "Loaded"


def test_a_slice_outside_the_file_is_untouched(env):
    _run(env, FOUR)
    _checked(env).name = "BU-0002"
    _run(env, [("E1", "base", 1), ("E1", "base", 2)])
    assert len(env.state.entries) == 8 and env.state.deleted == []


def test_a_refused_slice_keeps_its_old_entries(env):
    _run(env, FOUR)
    before = {n: list(e.lines) for n, e in env.state.entries.items()}
    _checked(env).name = "BU-0002"
    doc = _run(env, [("E1", "base", 1)], refused={("E1", "base", 1)})
    assert {n: e.lines for n, e in env.state.entries.items()} == before
    assert doc.status == "Failed"


def test_an_existing_line_the_user_may_not_write_fails_the_slice_and_writes_nothing(env):
    _run(env, FOUR)
    env.state.entries[f"BENT-{CYCLE}-E1-base-1"].lines.append({"main_account": "9999", "debit": 1.0})
    env.state.denied_accounts = {"9999"}
    env.frappe.db.commit()
    _checked(env).name = "BU-0002"
    doc = _run(env, FOUR)
    e = env.state.entries[f"BENT-{CYCLE}-E1-base-1"]
    assert e.budget_upload == "BU-0001" and len(e.lines) == 3
    assert env.state.entries[f"BENT-{CYCLE}-E1-base-2"].budget_upload == "BU-0001"
    assert doc.status == "Partly Loaded" and doc.failed_count == 2
    failed = [r for r in json.loads(doc.report) if r.get("load_error")]
    assert {(r["entity"], r["layer"]) for r in failed} == {("E1", "base")}
    assert "9999" in failed[0]["load_error"]


def test_a_slice_that_raises_is_rolled_back_and_the_rest_load(env):
    env.state.fail_on = set()
    _file(env, FOUR)
    doc = _checked(env)
    env.mod.load(doc.name)
    # The second slice's lines blow up on the access re-check of an existing
    # line: seed one under E1 board so the replace reaches it.
    env.state.entries["X"] = FakeEntry(env.state.entries, env.log, name="X", cycle=CYCLE, data_area_id="E1",
                                       layer="board", fiscal_period=2, budget_upload="",
                                       lines=[{"main_account": "BOOM"}])
    env.state.fail_on = {"BOOM"}
    env.frappe.db.commit()
    _job(env)
    assert doc.status == "Partly Loaded" and doc.failed_count == 2
    assert env.state.rollbacks >= 1
    assert "X" in env.state.entries and env.state.entries["X"].budget_upload == ""
    assert len([e for e in env.state.entries.values() if e.budget_upload == doc.name]) == 6


def test_each_slice_commits_on_its_own(env):
    _run(env, FOUR)
    assert env.state.commits >= 4


def test_loaded_count_is_derived_from_the_entries_that_still_name_the_upload(env):
    doc = _run(env, FOUR)
    env.state.entries[f"BENT-{CYCLE}-E2-board-2"].budget_upload = "BU-0009"
    del env.state.entries[f"BENT-{CYCLE}-E2-board-1"]
    state = env.mod.upload_state(doc)
    assert state["loaded_count"] == 6 and state["status"] == "Partly Loaded"
    assert doc.status == "Loaded", "the stored job status is never rewritten"
    for e in list(env.state.entries):
        env.state.entries[e].budget_upload = "BU-0009"
    assert env.mod.upload_state(doc)["status"] == "Superseded"


def test_the_payload_carries_the_derived_count(env):
    doc = _run(env, FOUR)
    env.state.entries[f"BENT-{CYCLE}-E2-board-2"].budget_upload = "BU-0009"
    assert env.mod.get_upload(doc.name)["loaded_count"] == 7


def test_loaded_count_is_virtual_on_the_doctype():
    with open(os.path.join(APP_DIR, "epm", "doctype", "budget_upload", "budget_upload.json")) as f:
        fields = {x["fieldname"]: x for x in json.load(f)["fields"]}
    assert fields["loaded_count"].get("is_virtual") == 1
    assert "Derived" in fields["loaded_count"].get("description", "")
    with open(os.path.join(APP_DIR, "epm", "doctype", "budget_upload", "budget_upload.py")) as f:
        src = f.read()
    assert "def loaded_count" in src and "status_now" in src


# ── review fix 1: the load tells each entry it has judged the rows ─────────

def test_the_load_saves_every_entry_as_judged_and_only_there(env):
    _run(env, FOUR)
    _checked(env).name = "BU-0002"
    _run(env, FOUR)
    flags = [entry[1] for entry in env.log if entry[0] == "judged"]
    assert len(flags) == 16 and all(flags), flags
    assert not env.frappe.flags.get("budget_entry_judged")


# ── review fix 2: a Cycle approved during the load ─────────────────────────

def test_a_slice_written_after_the_cycle_is_approved_is_refused_and_the_load_goes_on(env):
    """The Cycle is submitted (in another session) after the first slice
    commits. Every later slice is refused by the entry's Cycle guard: it is
    reported as refused, not failed, and the job carries on to the end."""
    env.state.approve_at_commit = 2   # the first slice's commit, then its progress save
    doc = _run(env, FOUR)
    report = json.loads(doc.report)
    first = [r for r in report if (r["entity"], r["layer"]) == ("E1", "base")]
    rest = [r for r in report if (r["entity"], r["layer"]) != ("E1", "base")]
    assert all(r.get("loaded") for r in first)
    assert len(rest) == 6
    for r in rest:
        assert not r.get("loaded") and not r.get("load_error") and r["ok"] is False, r
        assert any("during the load" in e and "approved" in e and CYCLE in e for e in r["errors"]), r["errors"]
    assert doc.failed_count == 0
    assert doc.status == "Partly Loaded"
    assert {e.data_area_id + e.layer for e in env.state.entries.values()} == {"E1base"}


def test_the_load_locks_the_cycle_before_it_touches_an_entry(env):
    # One lock order for every writer (Cycle, then entries) so a load and a
    # cell write of the same Cycle cannot deadlock on each other.
    _run(env, FOUR)
    _checked(env).name = "BU-0002"
    env.log.clear()
    _run(env, [("E1", "base", 1)])   # deletes E1 base P2, saves P1
    touched = [i for i, e in enumerate(env.log) if e[0] in ("delete", "save", "insert", "judged")]
    locks = [i for i, e in enumerate(env.log) if e[0] == "lock cycle"]
    assert locks and locks[0] < touched[0], env.log


# ── review fix 5: the outcome counts what the load was asked to load ────────

def test_a_group_refused_at_job_time_makes_the_load_partly_loaded(env):
    """Ready when the load was asked for, refused when the job judged the
    file again (a role taken away): the upload is not Loaded."""
    _file(env, FOUR)
    doc = _checked(env)
    env.mod.load(doc.name)
    assert doc.valid_count == 8
    _file(env, FOUR, refused={("E2", "board", 1)})
    _job(env)
    assert len(env.state.entries) == 6
    assert doc.status == "Partly Loaded"


def test_groups_the_user_chose_to_skip_do_not_spoil_loaded(env):
    doc = _run(env, FOUR, refused={("E2", "board", 1)})
    assert len(env.state.entries) == 6
    assert doc.status == "Loaded"


# ── re-review 2: the load sees what was committed after it judged the file ──

def test_an_entry_committed_after_the_plan_is_seen_by_the_first_slice(env):
    _file(env, [("E1", "base", 1)])
    doc = _checked(env)
    env.mod.load(doc.name)

    def another_session_commits():
        # E1 base P3, written by a cell while the job judged the file.
        env.state.entries["LATE"] = FakeEntry(env.state.entries, env.log, name="LATE", cycle=CYCLE,
                                              data_area_id="E1", layer="base", fiscal_period=3,
                                              budget_upload="", lines=[{"main_account": "4000"}])
        env.state.hidden.add("LATE")
    env.state.after_plan = another_session_commits
    _job(env)
    assert "LATE" not in env.state.entries, "P3 is absent from the file, so the slice replaces it"
    assert env.state.existing_reads and all(env.state.existing_reads), "the slice reads FOR UPDATE"


# ── re-review 3: a Cycle cancelled during the load is named cancelled ───────

def test_a_cycle_cancelled_during_the_load_is_not_called_approved(env):
    env.state.approve_at_commit, env.state.cycle_state = 2, "cancelled"
    doc = _run(env, FOUR)
    refused = [r for r in json.loads(doc.report) if not r["ok"]]
    assert refused
    for r in refused:
        assert any("cancelled" in e and "during the load" in e for e in r["errors"]), r["errors"]
        assert not any("approved during the load" in e for e in r["errors"]), r["errors"]


# ── re-review 4: the outcome compares the groups, not their counts ─────────

def test_the_outcome_compares_the_ready_groups_not_their_number(env):
    """At load, E2 board is refused (6 ready); at job time E2 board is ready
    and E1 base refused (6 ready again). Same count, different groups: E1 base
    was promised and not loaded."""
    _file(env, FOUR, refused={("E2", "board", 1)})
    doc = _checked(env)
    env.mod.load(doc.name, skip_invalid=1)
    _file(env, FOUR, refused={("E1", "base", 1)})
    _job(env)
    assert doc.status == "Partly Loaded"


# ---- Q15-c: a reload may change a slice's basis, since it replaces the slice whole ----

def test_a_reload_with_another_basis_replaces_the_whole_slice(env):
    _run(env, FOUR)
    _checked(env).name = "BU-0002"
    _file(env, FOUR)
    for item in env.state.plan_report:
        item["amount_basis"] = "Year-to-date movement"
    doc = env.state.doc if env.state.doc and env.state.doc.status == "Checked" else _checked(env)
    env.mod.load(doc.name, skip_invalid=1)
    _job(env)
    assert doc.status == "Loaded", json.loads(doc.report)
    assert {e.amount_basis for e in env.state.entries.values()} == {"Year-to-date movement"}
    assert not env.frappe.flags.get("budget_entry_replacing"), "the flag is put back"


def test_a_slice_left_out_of_the_reload_keeps_its_basis_rule(env):
    """Only the slice being replaced is exempt: entries of another layer are
    not in its replacing set."""
    _run(env, [("E1", "base", 1)])
    env.state.entries[f"BENT-{CYCLE}-E1-base-1"].amount_basis = "Year-to-date movement"
    _checked(env).name = "BU-0002"
    doc = _run(env, [("E1", "board", 1)])
    assert doc.status == "Loaded", json.loads(doc.report)


# ── PR #522 review 1: the slice's locking read runs on the Budget Entry index ──

def test_the_slice_read_filters_on_the_budget_entry_lock_index(env):
    seen = []
    real = env.frappe.db.get_values

    def get_values(doctype, filters, fields, **kw):
        if kw.get("for_update"):
            seen.append(dict(filters))
        return real(doctype, filters, fields, **kw)
    env.frappe.db.get_values = get_values
    _run(env, [("E1", "base", 1)])
    assert seen
    for filters in seen:
        assert {"cycle", "data_area_id", "layer"} <= set(filters), filters
        assert not any(isinstance(filters[c], list) for c in ("cycle", "data_area_id", "layer")), filters


# ── PR #522 review 4: a Q16-c refusal at load time is refused, not failed ──────

def test_an_annual_input_saved_during_the_load_refuses_its_slice_and_the_load_goes_on(env):
    """An Annual Input on one of E2's grains is committed after the job judged
    the file: Budget Entry's one-writer check refuses E2's slices. They are
    reported as refused (as a Cycle approved mid-load is), not as failed, and
    the rest load."""
    env.state.second_writer = {"E2"}
    doc = _run(env, FOUR)
    report = json.loads(doc.report)
    e2 = [r for r in report if r["entity"] == "E2"]
    assert e2
    for r in e2:
        assert not r.get("loaded") and not r.get("load_error") and r["ok"] is False, r
        assert any("BAI-9" in e and "Not loaded" in e for e in r["errors"]), r["errors"]
    assert doc.failed_count == 0
    assert all(r.get("loaded") for r in report if r["entity"] == "E1")
    assert doc.status == "Partly Loaded"
