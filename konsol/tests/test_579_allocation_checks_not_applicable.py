"""konsol#579 option B (chosen under the standing rule, 21 Nov 2026; rejected:
tests that guard themselves, because a skipped check reads as Pass; and
creating the gold tables empty, a second DDL source that also breaks the
isolated scope).

The allocation checks read gold_cost_allocation(_frozen), which only the
isolated allocation build creates. While no Cost Allocation Run is approved
there is nothing to check: the check run excludes them and records why. Once
any run is approved they run, so a missing table is still Red.

run_close_assertions is compiled alone against a stub frappe, as in
test_single_flight_gaps.py.
"""
import ast
import contextlib
import json
import os
import types

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AR_DIR = os.path.join(APP_DIR, "consolidation", "doctype", "assertion_run")
AR_PY = os.path.join(AR_DIR, "assertion_run.py")
AR_JSON = os.path.join(AR_DIR, "assertion_run.json")
NOTE = "Allocation checks not applicable: no approved Cost Allocation Run."


def _functions(*names):
    tree = ast.parse(open(AR_PY).read(), AR_PY)
    found = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert sorted(f.name for f in found) == sorted(names)
    return found


class _Doc(dict):
    __getattr__ = dict.get

    def __setattr__(self, k, v):
        self[k] = v

    def save(self, **k):
        pass

    def reload(self):
        pass


def _job(approved_runs, extra_lines=()):
    ev = {"argv": [], "counts": [], "emitted": [], "checkpoints": []}
    doc = _Doc(name="ZZ-AR-1", status="Queued", started_at=None)
    frappe = types.SimpleNamespace()

    def count(doctype, filters=None):
        ev["counts"].append((doctype, filters))
        return approved_runs

    def set_value(doctype, name, field, value, **k):
        if field == "log":
            ev["checkpoints"].append(value)

    frappe.db = types.SimpleNamespace(get_value=lambda *a, **k: "Queued", commit=lambda: None,
                                      set_value=set_value, count=count)
    frappe.get_doc = lambda doctype, name: doc
    frappe.get_single = lambda doctype: types.SimpleNamespace(dbt_project_path="/zz")
    frappe.utils = types.SimpleNamespace(now_datetime=lambda: None)
    frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *a: None,
                                                          warning=lambda *a: None)

    class _Proc:
        def __init__(self):
            self.stdout = iter(line + "\n" for line in extra_lines)

        def wait(self, timeout=None):
            raise RuntimeError("zz: no dbt here")

    def popen(argv, **k):
        ev["argv"] = list(argv)
        if extra_lines:
            return _Proc()
        raise RuntimeError("zz: no dbt here")

    ns = {
        "frappe": frappe,
        "subprocess": types.SimpleNamespace(Popen=popen, PIPE=-1, STDOUT=-2,
                                            TimeoutExpired=TimeoutError),
        "writing": lambda *a: contextlib.nullcontext(),
        "WORKER_WRITER": "worker",
        "_emit": lambda name, **k: ev["emitted"].append(k.get("line")),
        "_dbt_bin": lambda: "dbt",
        "_parse_results": lambda doc, path: None,
    }
    fns = _functions("run_close_assertions", "_not_applicable_checks", "_log_text")
    exec(compile(ast.Module(body=fns, type_ignores=[]), AR_PY, "exec"), ns)
    ns["run_close_assertions"]("ZZ-AR-1")
    return doc, ev


def test_no_approved_run_excludes_the_allocation_checks_and_says_so():
    doc, ev = _job(0)
    assert ev["counts"] == [("Cost Allocation Run", {"docstatus": 1})]
    argv = ev["argv"]
    assert "--exclude" in argv and argv[argv.index("--exclude") + 1] == "tag:domain:allocation"
    assert doc.not_applicable == NOTE
    assert doc.log.splitlines()[0] == NOTE
    assert NOTE in ev["emitted"]


def test_an_approved_run_keeps_the_allocation_checks():
    doc, ev = _job(1)
    assert "--exclude" not in ev["argv"]
    assert "--select" in ev["argv"] and "test_type:singular" in ev["argv"]
    assert not doc.not_applicable
    assert NOTE not in (doc.log or "")


def test_the_field_is_a_read_only_result_field():
    fields = {f["fieldname"]: f for f in json.load(open(AR_JSON))["fields"]}
    f = fields["not_applicable"]
    assert f["fieldtype"] == "Small Text" and f.get("read_only") == 1
    assert f["label"] == "Checks Not Applicable"
    src = open(AR_PY).read()
    tree = ast.parse(src)
    consts = {t.id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
              for t in n.targets if isinstance(t, ast.Name) and t.id in
              ("RESULT_FIELDS", "NEW_RUN_BLANK_FIELDS")}
    assert "not_applicable" in consts["RESULT_FIELDS"]
    assert "not_applicable" in consts["NEW_RUN_BLANK_FIELDS"]


def test_the_note_survives_a_log_longer_than_the_cap():
    """Tester CA-579: the log keeps its last 20000 characters, so on a real
    suite the note at the top was cut on every run."""
    doc, ev = _job(0, extra_lines=["x" * 99] * 400)        # ~40000 characters of dbt output
    assert doc.log.splitlines()[0] == NOTE
    assert len(doc.log) <= 20000
    assert doc.log.endswith("x" * 99 + "\nERROR: zz: no dbt here")
    assert ev["checkpoints"] and all(c.splitlines()[0] == NOTE and len(c) <= 20000
                                     for c in ev["checkpoints"])
