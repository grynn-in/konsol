"""TDD — B2 concurrency hardening (#64 + #67 + #70).

The single-flight guard (#67) and the cancel-race transitions (#67/#69) were
already implemented; this module adds the *behavioral* host coverage the B2 PRD
asks for (the prior coverage was source/AST-only) and drives the new #70 reaper
**heartbeat**:

- ``reaper.run_heartbeat`` — pure: which timestamp best reflects a run's last
  real progress (the orchestrator-owned ``heartbeat_at`` over the incidental
  ``modified`` over ``started_at``).
- :class:`run.FrappeSink` stamps ``heartbeat_at`` on every step start/result, so
  staleness reflects genuine pipeline progress, not unrelated bookkeeping writes.
- ``reaper.reap_stale_runs`` keys staleness off the heartbeat and marks a wedged
  run ``Failed`` (frappe mocked — the cross-worker DB behavior needs a bench).
- ``api._assert_no_active_run`` refuses a second run once the first's row is
  visible (the TOCTOU window itself is closed by ``single_flight_lock`` GET_LOCK
  + read-view commit, which needs a bench to verify).
"""
import sys
from datetime import datetime, timedelta

import pytest

from konsol.orchestrator import reaper, run
from konsol.orchestrator.handlers import StepResult
from konsol.orchestrator.state import Status


# ---- run_heartbeat (pure) -----------------------------------------------

def test_run_heartbeat_prefers_heartbeat_at():
    rec = {"heartbeat_at": "2026-06-29 12:00:00", "modified": "2026-06-29 11:00:00",
           "started_at": "2026-06-29 10:00:00"}
    assert reaper.run_heartbeat(rec) == "2026-06-29 12:00:00"


def test_run_heartbeat_falls_back_to_modified():
    # legacy runs (predating the heartbeat field) have no heartbeat_at — staleness
    # must still work off modified so the reaper stays backward compatible.
    rec = {"heartbeat_at": None, "modified": "2026-06-29 11:00:00",
           "started_at": "2026-06-29 10:00:00"}
    assert reaper.run_heartbeat(rec) == "2026-06-29 11:00:00"


def test_run_heartbeat_falls_back_to_started_at():
    rec = {"heartbeat_at": None, "modified": None, "started_at": "2026-06-29 10:00:00"}
    assert reaper.run_heartbeat(rec) == "2026-06-29 10:00:00"


def test_run_heartbeat_none_when_nothing_set():
    assert reaper.run_heartbeat({}) is None


def test_run_heartbeat_reads_doc_attributes():
    class Doc:
        heartbeat_at = "2026-06-29 12:00:00"
        modified = None
        started_at = None
    assert reaper.run_heartbeat(Doc()) == "2026-06-29 12:00:00"


# ---- FrappeSink stamps the heartbeat ------------------------------------

def test_sink_stamps_heartbeat_on_step_start():
    doc = {}
    sink = run.FrappeSink(doc, now=lambda: "2026-06-29 12:00:00")
    step = type("S", (), {"id": "a", "type": "t"})()
    sink.on_step_start(step)
    assert doc.get("heartbeat_at") == "2026-06-29 12:00:00"


def test_sink_advances_heartbeat_on_step_result():
    ticks = iter(["2026-06-29 12:00:00", "2026-06-29 12:00:05"])
    doc = {}
    sink = run.FrappeSink(doc, now=lambda: next(ticks))
    step = type("S", (), {"id": "a", "type": "t"})()
    sink.on_step_start(step)
    assert doc.get("heartbeat_at") == "2026-06-29 12:00:00"
    sink.on_step_result(step, StepResult(ok=True, rows=1, log="ok"))
    # the heartbeat advanced as the step made progress
    assert doc.get("heartbeat_at") == "2026-06-29 12:00:05"


# ---- reaper.reap_stale_runs (frappe mocked) -----------------------------

class _FakeDB:
    def __init__(self, runs):
        self._runs = runs  # name -> dict
        self.committed = False

    def get_value(self, doctype, name, field):
        return self._runs.get(name, {}).get(field)

    def set_value(self, doctype, name, values, update_modified=True):
        self._runs.setdefault(name, {}).update(values)

    def commit(self):
        self.committed = True


class _FakeUtils:
    def __init__(self, now):
        self._now = now

    def now_datetime(self):
        return self._now


class _FakeLogger:
    def info(self, *a, **k):
        pass


class _FakeFrappe:
    """Minimal stand-in so the frappe-bound reaper / guard run on the host."""

    class ValidationError(Exception):
        pass

    def __init__(self, runs, now=None):
        # runs: list of dicts, each with name/status/heartbeat_at/modified/...
        self._rows = runs
        self._by_name = {r["name"]: r for r in runs}
        self.db = _FakeDB(self._by_name)
        self.utils = _FakeUtils(now)
        self._logger = _FakeLogger()

    def get_all(self, doctype, filters=None, fields=None, limit=None, **kw):
        states = None
        if filters and "status" in filters:
            states = filters["status"][1]
        out = [dict(r) for r in self._rows if states is None or r.get("status") in states]
        return out[:limit] if limit else out

    def logger(self):
        return self._logger

    def throw(self, msg, exc=None):
        raise (exc or self.ValidationError)(msg)


def _install_frappe(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "frappe", fake)


def test_reap_skips_run_with_fresh_heartbeat(monkeypatch):
    now = datetime(2026, 6, 29, 12, 0, 0)
    fresh = now - timedelta(minutes=5)
    runs = [{"name": "PIPE-1", "status": "Running", "heartbeat_at": fresh,
             "modified": now - timedelta(hours=10)}]  # modified is old; heartbeat is fresh
    fake = _FakeFrappe(runs, now=now)
    _install_frappe(monkeypatch, fake)

    reaped = reaper.reap_stale_runs()
    # the healthy long-running step heartbeats; an old `modified` must NOT reap it.
    assert reaped == []
    assert fake._by_name["PIPE-1"].get("status") in (None, "Running")


def test_reap_marks_run_with_stale_heartbeat_failed(monkeypatch):
    now = datetime(2026, 6, 29, 12, 0, 0)
    stale = now - timedelta(minutes=reaper.STALE_RUN_TIMEOUT_MINUTES + 1)
    runs = [{"name": "PIPE-1", "status": "Running", "heartbeat_at": stale,
             "modified": now, "error_log": ""}]  # modified fresh, but no real progress
    fake = _FakeFrappe(runs, now=now)
    _install_frappe(monkeypatch, fake)

    reaped = reaper.reap_stale_runs()
    assert reaped == ["PIPE-1"]
    assert fake._by_name["PIPE-1"]["status"] == "Failed"
    assert fake.db.committed is True


def test_reap_falls_back_to_modified_for_legacy_run(monkeypatch):
    # a run with no heartbeat_at (predates #70) is judged on modified.
    now = datetime(2026, 6, 29, 12, 0, 0)
    stale = now - timedelta(minutes=reaper.STALE_RUN_TIMEOUT_MINUTES + 1)
    runs = [{"name": "PIPE-1", "status": "Running", "heartbeat_at": None,
             "modified": stale, "error_log": ""}]
    fake = _FakeFrappe(runs, now=now)
    _install_frappe(monkeypatch, fake)

    assert reaper.reap_stale_runs() == ["PIPE-1"]
    assert fake._by_name["PIPE-1"]["status"] == "Failed"


# ---- single-flight refusal (behavioral; #64a / #67) ---------------------

def test_assert_no_active_run_passes_when_none_active(monkeypatch):
    from konsol.orchestrator import api

    fake = _FakeFrappe([], now=datetime(2026, 6, 29, 12, 0, 0))
    _install_frappe(monkeypatch, fake)
    # no active run -> no raise
    api._assert_no_active_run()


def test_assert_no_active_run_refuses_second_concurrent_start(monkeypatch):
    # TOCTOU: once the first start has committed its row, a near-simultaneous
    # second start (which the single-flight lock serialises AFTER the first) must
    # see the active row and be refused — no two active runs against one dbt dir.
    from konsol.orchestrator import api

    rows = []
    fake = _FakeFrappe(rows, now=datetime(2026, 6, 29, 12, 0, 0))
    _install_frappe(monkeypatch, fake)

    # first caller: nothing active yet -> passes the guard, then inserts its run
    api._assert_no_active_run()
    rows.append({"name": "PIPE-1", "status": "Running"})

    # second caller now observes the first's row -> refused
    with pytest.raises(fake.ValidationError):
        api._assert_no_active_run()


def test_assert_no_active_run_ignores_terminal_runs(monkeypatch):
    from konsol.orchestrator import api

    rows = [{"name": "PIPE-1", "status": "Completed"},
            {"name": "PIPE-2", "status": "Cancelled"},
            {"name": "PIPE-3", "status": "Failed"}]
    fake = _FakeFrappe(rows, now=datetime(2026, 6, 29, 12, 0, 0))
    _install_frappe(monkeypatch, fake)
    # only ACTIVE_RUN_STATES block a new run; terminal runs do not
    api._assert_no_active_run()
