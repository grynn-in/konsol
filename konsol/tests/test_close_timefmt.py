"""Timezone-aware ISO 8601: konsol/close/timefmt.py (konsol#305 A16b; stories 0.2, 9.1).

``zoned_iso(naive_dt, tz_name)`` attaches ``tz_name``'s UTC offset to a naive
datetime, so a close API never ships a zone-less time (measured live 25 Sep
2026: ``get_freshness`` returned "2026-09-16T17:47:49.238943" with no zone).
Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os
from datetime import datetime, timezone

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(APP_DIR, "close", "timefmt.py")
_spec = importlib.util.spec_from_file_location("close_timefmt_under_test", MODEL_PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def test_a_naive_datetime_gets_londons_offset():
    dt = datetime(2026, 9, 16, 17, 47, 49)
    assert M.zoned_iso(dt, "Europe/London") == "2026-09-16T17:47:49+01:00"


def test_a_naive_datetime_gets_kolkatas_offset():
    dt = datetime(2026, 9, 16, 17, 47, 49)
    assert M.zoned_iso(dt, "Asia/Kolkata").endswith("+05:30")


def test_none_stays_none():
    assert M.zoned_iso(None, "Europe/London") is None


def test_an_aware_datetime_is_converted_not_relabelled():
    """The instant is preserved: 17:47:49 UTC is 18:47:49 in London (BST, +01:00
    in September), not 17:47:49 stamped with London's offset."""
    dt = datetime(2026, 9, 16, 17, 47, 49, tzinfo=timezone.utc)
    assert M.zoned_iso(dt, "Europe/London") == "2026-09-16T18:47:49+01:00"


def test_module_imports_no_frappe():
    with open(MODEL_PATH) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert not any(a.name.startswith(("frappe", "konsol")) for a in node.names)
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith(("frappe", "konsol"))
