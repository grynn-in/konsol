"""#535: a `format:...{#####}` autoname keys its counter on the empty string.

Frappe's `_format_autoname` parses each braced part on its own
(`parse_naming_series([param])`), so the `{#####}` part is numbered by
`getseries("", 5)`: one `tabSeries` row named "" shared by EVERY doctype that
names with a bare hash counter, and the numbers skip. A `PREFIX-.#####`
autoname keys the counter on the prefix instead (the pattern #523 gave Cost
Allocation).

Trial Balance Upload moves to `TBU-.#####` and Budget Upload to `BUU-.#####`.
The patch seeds each prefix's series above its highest existing name, so the
next insert cannot collide.
"""
import json
import os
import re
import sys
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = os.path.join(APP_DIR, "patches", "seed_own_name_series.py")
PATCH_ID = "konsol.patches.seed_own_name_series"
#: doctype folder -> (doctype, prefix)
OWN_SERIES = {
    "trial_balance_upload": ("Trial Balance Upload", "TBU-"),
    "budget_upload": ("Budget Upload", "BUU-"),
}

# Doctypes that still name with `format:` and a hash counter, so still share the
# "" series key. Their names carry fields as well, so they stay unique; only the
# counter is gappy. Remove an entry when its autoname moves to its own series
# with a seeding patch.
KNOWN_SHARED_KEY = {
    "trial_balance_submission",
    "assertion_run",
}


def _autonames():
    found = {}
    for root, _dirs, files in os.walk(APP_DIR):
        if "node_modules" in root:
            continue
        for f in files:
            if f.endswith(".json") and os.path.basename(root) == f[:-5]:
                try:
                    data = json.load(open(os.path.join(root, f)))
                except ValueError:
                    continue
                if isinstance(data, dict) and data.get("doctype") == "DocType":
                    found[f[:-5]] = data.get("autoname") or ""
    return found


def test_each_upload_names_from_its_own_series():
    names = _autonames()
    for folder, (_doctype, prefix) in OWN_SERIES.items():
        assert names[folder] == f"{prefix}.#####", folder


def test_no_other_doctype_shares_the_empty_series_key():
    shared = {n for n, a in _autonames().items() if a.startswith("format:") and re.search(r"\{#+\}", a)}
    assert shared == KNOWN_SHARED_KEY


def test_patch_is_registered_and_the_old_one_is_gone():
    lines = [l.strip() for l in open(os.path.join(APP_DIR, "patches.txt"))]
    assert PATCH_ID in lines
    assert "konsol.patches.seed_trial_balance_upload_series" not in lines
    assert not os.path.exists(os.path.join(APP_DIR, "patches", "seed_trial_balance_upload_series.py"))


def _run_patch(names_by_doctype, series):
    """Run the patch against a stub frappe.db; restore sys.modules after."""
    calls = []

    class DB:
        def table_exists(self, dt):
            return dt in names_by_doctype

        def sql(self, q, params=None, **kw):
            calls.append((q, params))
            m = re.search(r"from `tab([^`]+)`", q)
            table = m.group(1) if m else ""
            low = q.strip().lower()
            if table == "Series" and low.startswith("select"):
                return [(series[params[0]],)] if params[0] in series else []
            if low.startswith("insert"):
                series[params[0]] = params[1]
                return []
            if low.startswith("update"):
                series[params[1]] = params[0]
                return []
            return [(n,) for n in names_by_doctype.get(table, [])]

    stub = types.SimpleNamespace(db=DB())
    saved = sys.modules.get("frappe")
    sys.modules["frappe"] = stub
    try:
        ns = {}
        exec(compile(open(PATCH).read(), PATCH, "exec"), ns)
        ns["execute"]()
    finally:
        if saved is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = saved
    return calls


def test_patch_raises_each_series_to_its_highest_existing_suffix():
    series = {}
    _run_patch({"Trial Balance Upload": ["TBU-00001", "TBU-00012", "TBU-00007"],
                "Budget Upload": ["BUU-00004", "BUU-00009"]}, series)
    assert series == {"TBU-": 12, "BUU-": 9}


def test_patch_never_lowers_a_series_and_is_idempotent():
    series = {"TBU-": 50}
    _run_patch({"Trial Balance Upload": ["TBU-00012"], "Budget Upload": ["BUU-00003"]}, series)
    _run_patch({"Trial Balance Upload": ["TBU-00012"], "Budget Upload": ["BUU-00003"]}, series)
    assert series == {"TBU-": 50, "BUU-": 3}


def test_patch_skips_a_missing_table_and_leaves_no_stub_behind():
    before = sys.modules.get("frappe")
    series = {}
    _run_patch({"Budget Upload": ["BUU-00002"]}, series)
    assert series == {"BUU-": 2}
    assert sys.modules.get("frappe") is before
