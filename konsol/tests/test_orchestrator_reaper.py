"""TDD — stale-run reaper (#67 fix 2). The timestamp-math decision is pure and
unit-tested here; the frappe-bound sweep (``reap_stale_runs``) needs a bench."""
from datetime import datetime, timedelta

from konsol.orchestrator import reaper


def test_module_imports_without_frappe():
    assert callable(reaper.is_run_stale)
    assert callable(reaper.reap_stale_runs)


def test_timeout_is_generous():
    # must comfortably exceed the longest expected single dbt step so a healthy
    # long-running step is never falsely reaped.
    assert reaper.STALE_RUN_TIMEOUT_MINUTES >= 60


def test_fresh_run_is_not_stale():
    now = datetime(2026, 6, 29, 12, 0, 0)
    modified = now - timedelta(minutes=5)
    assert reaper.is_run_stale(modified, now) is False


def test_old_run_is_stale():
    now = datetime(2026, 6, 29, 12, 0, 0)
    modified = now - timedelta(minutes=reaper.STALE_RUN_TIMEOUT_MINUTES + 1)
    assert reaper.is_run_stale(modified, now) is True


def test_exactly_at_timeout_is_not_stale():
    now = datetime(2026, 6, 29, 12, 0, 0)
    modified = now - timedelta(minutes=reaper.STALE_RUN_TIMEOUT_MINUTES)
    # strictly-greater-than: at exactly the timeout we are not yet stale
    assert reaper.is_run_stale(modified, now) is False


def test_accepts_frappe_datetime_strings():
    now = "2026-06-29 12:00:00"
    modified = "2026-06-29 09:00:00"  # 3h old > 120m
    assert reaper.is_run_stale(modified, now) is True


def test_accepts_microsecond_strings():
    now = "2026-06-29 12:00:00.123456"
    modified = "2026-06-29 11:59:00.000000"
    assert reaper.is_run_stale(modified, now) is False


def test_unparseable_or_missing_is_not_stale():
    now = datetime(2026, 6, 29, 12, 0, 0)
    assert reaper.is_run_stale(None, now) is False
    assert reaper.is_run_stale("not-a-date", now) is False
    assert reaper.is_run_stale(now, None) is False


def test_custom_timeout_respected():
    now = datetime(2026, 6, 29, 12, 0, 0)
    modified = now - timedelta(minutes=30)
    assert reaper.is_run_stale(modified, now, timeout_minutes=20) is True
    assert reaper.is_run_stale(modified, now, timeout_minutes=60) is False
