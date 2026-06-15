"""Behavioral tests for Connector Health status derivation.

These import the controller (needs ``frappe`` importable, i.e. the bench env);
under the standalone structural suite they skip cleanly. Run inside the site:
``bench --site <site> run-tests`` or pytest within the bench python.
"""
import datetime

import pytest

pytest.importorskip("frappe")

from konsol.pipeline.doctype.connector_health.connector_health import _derive_status

NOW = datetime.datetime(2024, 6, 15, 12, 0, 0)


def _ago(minutes):
    return NOW - datetime.timedelta(minutes=minutes)


def test_never_synced():
    assert _derive_status("", None, NOW, 1440) == ("Never", 0, "")


def test_first_sync_running_no_completion_is_running_not_never():
    assert _derive_status("Running", None, NOW, 1440)[0] == "Running"


def test_running_fresh():
    status, lag, err = _derive_status("Running", _ago(10), NOW, 1440)
    assert status == "Running" and lag == 10 and err == ""


def test_running_stuck_past_threshold_becomes_stale():
    status, lag, err = _derive_status("Running", _ago(2000), NOW, 1440)
    assert status == "Stale" and lag == 2000 and "stuck" in err.lower()


def test_success_fresh_is_succeeded():
    assert _derive_status("Success", _ago(10), NOW, 1440)[0] == "Succeeded"


def test_partial_maps_to_succeeded():
    assert _derive_status("Partial", _ago(10), NOW, 1440)[0] == "Succeeded"


def test_failed():
    status, _, err = _derive_status("Failed", _ago(10), NOW, 1440)
    assert status == "Failed" and err


def test_stale_when_lag_exceeds_freq():
    status, lag, err = _derive_status("Success", _ago(2000), NOW, 1440)
    assert status == "Stale" and lag == 2000 and "2000" in err


def test_freq_zero_never_stale():
    assert _derive_status("Success", _ago(99999), NOW, 0)[0] == "Succeeded"


def test_freq_none_never_stale():
    assert _derive_status("Success", _ago(99999), NOW, None)[0] == "Succeeded"
