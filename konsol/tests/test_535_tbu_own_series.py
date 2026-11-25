"""#535: a `format:...{#####}` autoname keys its counter on the empty string.

Frappe's `_format_autoname` parses each braced part on its own
(`parse_naming_series([param])`), so the `{#####}` part is numbered by
`getseries("", 5)`: one `tabSeries` row named "" shared by EVERY doctype that
names with a bare hash counter, and the numbers skip. A `PREFIX-.#####`
autoname keys the counter on the prefix instead (the pattern #523 gave Cost
Allocation).

Trial Balance Upload moves to `TBU-.#####`. The patch seeds the `TBU-` series
above the highest existing `TBU-nnnnn` name, so the next insert cannot collide.
"""
import json
import os
import re
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TBU_JSON = os.path.join(APP_DIR, "consolidation", "doctype", "trial_balance_upload", "trial_balance_upload.json")
PATCH = os.path.join(APP_DIR, "patches", "seed_trial_balance_upload_series.py")
PATCH_ID = "konsol.patches.seed_trial_balance_upload_series"

# Doctypes that still name with `format:` and a hash counter, so still share the
# "" series key (konsol#535 candidates, not fixed here). Remove an entry when its
# autoname moves to a `PREFIX-.#####` series with its own seeding patch.
KNOWN_SHARED_KEY = {
    "budget_upload",
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


def test_tbu_autoname_is_its_own_series():
    assert json.load(open(TBU_JSON))["autoname"] == "TBU-.#####"


def test_no_new_doctype_shares_the_empty_series_key():
    shared = {n for n, a in _autonames().items() if a.startswith("format:") and re.search(r"\{#+\}", a)}
    assert shared == KNOWN_SHARED_KEY


def test_patch_is_registered():
    lines = [l.strip() for l in open(os.path.join(APP_DIR, "patches.txt"))]
    assert PATCH_ID in lines


def _run_patch(names, series):
    src = open(PATCH).read()
    ns = {}
    exec(compile(src, PATCH, "exec"), ns)
    calls = []

    class DB:
        def table_exists(self, dt):
            return True

        def sql(self, q, params=None, **kw):
            calls.append((q, params))
            if "from `tabTrial Balance Upload`" in q:
                return [(n,) for n in names]
            if "from `tabSeries`" in q:
                return [(series["TBU-"],)] if "TBU-" in series else []
            if q.strip().lower().startswith(("update", "insert")):
                series["TBU-"] = params[0] if q.strip().lower().startswith("update") else params[1]
            return []

    ns["frappe"] = types.SimpleNamespace(db=DB())
    ns["execute"]()
    return calls


def test_patch_raises_series_to_highest_existing_suffix():
    series = {}
    _run_patch(["TBU-00001", "TBU-00012", "TBU-00007"], series)
    assert series["TBU-"] == 12


def test_patch_never_lowers_the_series_and_is_idempotent():
    series = {"TBU-": 50}
    _run_patch(["TBU-00012"], series)
    assert series["TBU-"] == 50
    series = {}
    _run_patch(["TBU-00003"], series)
    _run_patch(["TBU-00003"], series)
    assert series["TBU-"] == 3
