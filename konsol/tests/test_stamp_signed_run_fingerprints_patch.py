"""konsol#338 upgrade patch: stamp the signed runs that predate fingerprints.

The coordinator's call on the #338 decision comment: an already-signed run is
stamped with the fingerprint of the current warehouse. Any input change since
it was signed has already voided it (A63, #305-W4-4), so the current numbers
are the signed numbers, and no signed run is left without a fingerprint.

Loaded by path against a stub frappe and a stub ``konsol.close.fingerprint``.
"""
import importlib.util
import os
import sys
import types
from datetime import datetime

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH_PY = os.path.join(APP_DIR, "patches", "stamp_signed_run_fingerprints.py")
PATCHES_TXT = os.path.join(APP_DIR, "patches.txt")
SIGNED = ("Signed Off", "Acknowledged", "Overridden")
BUILD_AT = datetime(2026, 10, 6, 9, 0, 0)


class _D(dict):
    def __getattr__(self, name):
        return self.get(name)


class _Site:
    def __init__(self, runs, fail=None):
        self.runs = {r["name"]: dict(r) for r in runs}
        self.fail = fail
        self.reads = []
        self.reloads = []
        self.writes = []


def _run(name, fp, signoff="Signed Off", fingerprint=None, fy=2025):
    return {"name": name, "fiscal_year": fy, "fiscal_period": fp, "signoff_status": signoff,
            "numbers_fingerprint": fingerprint}


def _execute(site):
    frappe = types.ModuleType("frappe")

    def get_all(doctype, filters=None, fields=None, **k):
        assert doctype == "Assertion Run"
        out = []
        for r in site.runs.values():
            if r["signoff_status"] not in filters["signoff_status"][1]:
                continue
            if r["numbers_fingerprint"]:
                continue
            if not r["fiscal_period"]:
                continue
            out.append(_D({f: r[f] for f in fields}))
        assert filters["numbers_fingerprint"] == ["is", "not set"]
        assert filters["fiscal_period"] == [">", 0]
        return out

    def set_value(doctype, name, values, update_modified=True):
        assert update_modified is False
        site.writes.append((name, dict(values)))
        site.runs[name].update(values)

    frappe.get_all = get_all
    frappe.db = types.SimpleNamespace(set_value=set_value)
    frappe.reload_doc = lambda *a: site.reloads.append(a)

    fingerprint = types.ModuleType("konsol.close.fingerprint")

    def fingerprints(periods):
        site.reads.append(sorted(periods))
        if site.fail:
            raise RuntimeError(site.fail)
        return {p: "v1:%d-%d" % p for p in periods}

    fingerprint.fingerprints = fingerprints
    fingerprint.latest_build_at = lambda: BUILD_AT
    ar = types.ModuleType("konsol.consolidation.doctype.assertion_run.assertion_run")
    ar.SIGNED_STATES = SIGNED
    stubs = {"frappe": frappe, "konsol.close.fingerprint": fingerprint,
             "konsol.consolidation.doctype.assertion_run.assertion_run": ar}
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("stamp_patch_under_test", PATCH_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.execute()
    finally:
        for n, old in saved.items():
            if old is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = old


def test_signed_runs_without_a_fingerprint_are_stamped_from_one_read():
    site = _Site([_run("R1", 3), _run("R2", 7, "Acknowledged"), _run("R3", 7, "Overridden"),
                  _run("R4", 5, "Not Signed Off"), _run("R5", 6, "Re-sign Needed"),
                  _run("R6", 8, fingerprint="v1:kept")])
    _execute(site)
    assert site.reloads == [("consolidation", "doctype", "assertion_run")]
    assert site.reads == [[(2025, 3), (2025, 7)]]
    assert sorted(n for n, _ in site.writes) == ["R1", "R2", "R3"]
    assert site.runs["R1"]["numbers_fingerprint"] == "v1:2025-3"
    assert site.runs["R2"]["numbers_fingerprint"] == "v1:2025-7"
    assert all(v["fingerprint_as_of"] == BUILD_AT for _, v in site.writes)
    assert site.runs["R4"]["numbers_fingerprint"] is None
    assert site.runs["R5"]["numbers_fingerprint"] is None
    assert site.runs["R6"]["numbers_fingerprint"] == "v1:kept"


def test_a_second_run_changes_nothing():
    site = _Site([_run("R1", 3)])
    _execute(site)
    site.writes.clear()
    _execute(site)
    assert site.writes == [] and len(site.reads) == 1


def test_no_signed_run_reads_nothing():
    site = _Site([_run("R4", 5, "Not Signed Off")])
    _execute(site)
    assert site.reads == [] and site.writes == []


def test_an_unreadable_warehouse_fails_the_patch_rather_than_leave_runs_unstamped():
    site = _Site([_run("R1", 3)], fail="(UNKNOWN_TABLE)")
    try:
        _execute(site)
    except RuntimeError as exc:
        assert "UNKNOWN_TABLE" in str(exc)
    else:
        raise AssertionError("the patch passed with signed runs left unstamped")
    assert site.writes == []


def test_the_patch_is_listed_once():
    with open(PATCHES_TXT) as f:
        lines = [line.strip() for line in f]
    assert lines.count("konsol.patches.stamp_signed_run_fingerprints") == 1
