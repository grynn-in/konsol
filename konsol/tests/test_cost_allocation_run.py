"""konsol#269 K6: the Cost Allocation Run doctype shape (plan section 2).

Pins the parent (submittable, workflow, autoname, title and search fields,
tabs and sections), its two read-only child tables, the workflow definition
and the INSTALLED entry. The controllers carry no behaviour yet: Compute
(K7) and the submit/cancel gates (K8) come later.
"""
import ast
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EPM_DOCTYPES = os.path.join(APP_DIR, "epm", "doctype")
RUN = "cost_allocation_run"
WEIGHT = "cost_allocation_run_weight"
ACCOUNT = "cost_allocation_run_account"
LAYOUT_TYPES = {"Tab Break", "Section Break", "Column Break"}

# (fieldname, fieldtype, reqd, read_only) for every field that holds data.
RUN_FIELDS = [
    ("rule_set", "Link", 1, 0),
    ("entity", "Link", 0, 1),
    ("scenario", "Link", 1, 0),
    ("fiscal_year", "Int", 1, 0),
    ("fiscal_period", "Int", 1, 0),
    ("status", "Select", 0, 1),
    ("amended_from", "Link", 0, 1),
    ("method", "Select", 0, 1),
    ("allocation_dimension", "Link", 0, 1),
    ("rule_set_modified", "Datetime", 0, 1),
    ("receiver_account_mode", "Select", 0, 1),
    ("allocation_account", "Link", 0, 1),
    ("amounts_after_approval", "Select", 0, 1),
    ("values_carried_forward", "Check", 0, 1),
    ("computed_at", "Datetime", 0, 1),
    ("computed_by", "Link", 0, 1),
    ("computed_for_rule_set", "Data", 0, 1),
    ("computed_for_scenario", "Data", 0, 1),
    ("computed_for_fiscal_year", "Int", 0, 1),
    ("computed_for_fiscal_period", "Int", 0, 1),
    ("weights", "Table", 0, 1),
    ("accounts", "Table", 0, 1),
]

WEIGHT_FIELDS = [
    ("step", "Int"),
    ("sender_member", "Data"),
    ("receiver_member", "Data"),
    ("driver", "Link"),
    ("statistic", "Link"),
    ("value_period", "Int"),
    ("carried_forward", "Check"),
    ("driver_value", "Float"),
    ("weight", "Float"),
]

ACCOUNT_FIELDS = [
    ("main_account", "Link"),
    ("declared_account", "Link"),
]


def _doc(folder):
    with open(os.path.join(EPM_DOCTYPES, folder, folder + ".json")) as f:
        return json.load(f)


def _by_name(folder):
    return {f["fieldname"]: f for f in _doc(folder)["fields"]}


def _workflow():
    with open(os.path.join(EPM_DOCTYPES, RUN, "cost_allocation_run_workflow.json")) as f:
        return json.load(f)


def test_doctype_folders_are_packages_with_json_and_controller():
    for folder in (RUN, WEIGHT, ACCOUNT):
        base = os.path.join(EPM_DOCTYPES, folder)
        for name in ("__init__.py", folder + ".json", folder + ".py",):
            assert os.path.exists(os.path.join(base, name)), f"{folder}/{name} missing"


def test_doctype_header_is_a_submittable_epm_doctype():
    doc = _doc(RUN)
    assert doc["name"] == "Cost Allocation Run"
    assert doc["doctype"] == "DocType"
    assert doc["module"] == "EPM"
    assert doc["is_submittable"] == 1
    assert doc.get("istable", 0) == 0
    assert doc["track_changes"] == 1
    # Own naming series per doctype ("CAR-.#####"); a "format:…{#####}" name
    # shares one empty Series key across doctypes (found live, 14 Nov 2026).
    assert doc["autoname"] == "CAR-.#####"
    assert doc["title_field"] == "rule_set"
    assert doc["search_fields"] == "scenario,fiscal_year,fiscal_period"


def test_doctype_tabs_and_sections_follow_the_plan():
    doc = _doc(RUN)
    tabs = [f["label"] for f in doc["fields"] if f["fieldtype"] == "Tab Break"]
    sections = [f["label"] for f in doc["fields"] if f["fieldtype"] == "Section Break"]
    assert tabs == ["Run", "Weights", "Accounts"]
    assert sections == ["Scope", "Computation", "Computed", "Weights", "Accounts"]


def test_doctype_run_fields_types_required_and_read_only():
    doc = _doc(RUN)
    got = [
        (f["fieldname"], f["fieldtype"], f.get("reqd", 0), f.get("read_only", 0))
        for f in doc["fields"] if f["fieldtype"] not in LAYOUT_TYPES
    ]
    assert got == RUN_FIELDS


def test_doctype_run_link_and_select_options():
    fields = _by_name(RUN)
    assert fields["rule_set"]["options"] == "Cost Allocation Rule Set"
    assert fields["entity"]["options"] == "Entity"
    assert fields["entity"]["fetch_from"] == "rule_set.entity"
    assert fields["scenario"]["options"] == "Scenario"
    assert fields["allocation_dimension"]["options"] == "Dimension"
    assert fields["allocation_account"]["options"] == "Main Account"
    assert fields["computed_by"]["options"] == "User"
    assert fields["amended_from"]["options"] == "Cost Allocation Run"
    assert fields["weights"]["options"] == "Cost Allocation Run Weight"
    assert fields["accounts"]["options"] == "Cost Allocation Run Account"
    assert fields["status"]["options"] == "Draft\nPending Approval\nApproved\nCancelled"
    assert fields["status"]["default"] == "Draft"
    assert fields["method"]["options"] == "Direct\nStep-Down\nReciprocal"
    assert fields["receiver_account_mode"]["options"] == (
        "Sender's Own Accounts\nDeclared Allocation Account")
    assert fields["amounts_after_approval"]["options"] == "Follow Latest Data\nFreeze at Approval"


def test_run_select_options_equal_model_constants():
    """The Run copies the rule set's method and receiver account mode, so its
    options must be the model's constants. A narrower list refuses a valid
    run on save (konsol#269 RF1: Reciprocal was refused)."""
    from konsol import cost_allocation_model as model
    fields = _by_name(RUN)
    assert fields["method"]["options"].split("\n") == list(model.METHODS)
    assert fields["receiver_account_mode"]["options"].split("\n") == list(model.ACCOUNT_MODES)


def test_run_amounts_after_approval_options_equal_rule_set():
    """No model constant exists for this choice, so the Run must offer exactly
    the rule set's options: the copied value is refused on save otherwise."""
    run_options = _by_name(RUN)["amounts_after_approval"]["options"]
    rule_set_options = _by_name("cost_allocation_rule_set")["amounts_after_approval"]["options"]
    assert run_options.split("\n") == rule_set_options.split("\n")


def test_doctype_declared_account_is_shown_only_when_declared():
    fields = _by_name(RUN)
    assert fields["allocation_account"]["depends_on"] == (
        "eval:doc.receiver_account_mode=='Declared Allocation Account'")


def test_doctype_non_obvious_fields_carry_help_text():
    fields = _by_name(RUN)
    for name in ("entity", "fiscal_period", "rule_set_modified", "receiver_account_mode",
                 "allocation_account", "amounts_after_approval", "values_carried_forward"):
        assert fields[name].get("description"), f"{name} has no help text"


def test_doctype_child_tables_are_istable_and_all_read_only():
    names = {WEIGHT: "Cost Allocation Run Weight", ACCOUNT: "Cost Allocation Run Account"}
    for folder, expected in ((WEIGHT, WEIGHT_FIELDS), (ACCOUNT, ACCOUNT_FIELDS)):
        doc = _doc(folder)
        assert doc["name"] == names[folder], folder
        assert doc["istable"] == 1, folder
        assert doc["module"] == "EPM", folder
        fields = doc["fields"]
        assert [(f["fieldname"], f["fieldtype"]) for f in fields] == expected, folder
        assert all(f.get("read_only") == 1 for f in fields), (
            folder, [f["fieldname"] for f in fields if f.get("read_only") != 1])


def test_doctype_weight_child_links_and_precision():
    fields = _by_name(WEIGHT)
    assert fields["driver"]["options"] == "Cost Allocation Driver"
    assert fields["statistic"]["options"] == "Cost Allocation Statistic"
    assert fields["weight"]["precision"] == "9"
    assert fields["driver_value"]["precision"] == "9"


def test_doctype_account_child_links():
    fields = _by_name(ACCOUNT)
    assert fields["main_account"]["options"] == "Main Account"
    assert fields["declared_account"]["options"] == "Main Account"


def test_doctype_child_controllers_define_their_classes():
    for folder, cls in ((RUN, "CostAllocationRun"), (WEIGHT, "CostAllocationRunWeight"),
                        (ACCOUNT, "CostAllocationRunAccount")):
        with open(os.path.join(EPM_DOCTYPES, folder, folder + ".py")) as f:
            names = [n.name for n in ast.parse(f.read()).body if isinstance(n, ast.ClassDef)]
        assert names == [cls], (folder, names)


def test_doctype_workflow_states_and_transitions():
    wf = _workflow()
    assert wf["document_type"] == "Cost Allocation Run"
    assert wf["workflow_state_field"] == "status"
    assert wf["is_active"] == 1
    states = {s["state"]: (int(s["doc_status"]), s["allow_edit"]) for s in wf["states"]}
    assert states == {
        "Draft": (0, "EPM Analyst"),
        "Pending Approval": (0, "EPM Admin"),
        "Approved": (1, "EPM Admin"),
        "Cancelled": (2, "EPM Admin"),
    }
    transitions = [(t["state"], t["action"], t["next_state"], t["allowed"]) for t in wf["transitions"]]
    assert transitions == [
        ("Draft", "Send for Approval", "Pending Approval", "EPM Analyst"),
        ("Pending Approval", "Reject", "Draft", "EPM Admin"),
        ("Pending Approval", "Approve", "Approved", "EPM Admin"),
        ("Approved", "Cancel", "Cancelled", "EPM Admin"),
    ]


def test_doctype_approver_holds_submit_and_cancel():
    perms = {p["role"]: p for p in _doc(RUN)["permissions"]}
    assert perms["EPM Admin"].get("submit") == 1
    assert perms["EPM Admin"].get("cancel") == 1
    assert not perms["EPM Analyst"].get("submit")


def test_doctype_is_installed_as_a_workflow():
    with open(os.path.join(APP_DIR, "workflows.py")) as f:
        installed = re.findall(r'"([^"]+)"', f.read().split("INSTALLED = (")[1].split(")")[0])
    assert "Cost Allocation Run" in installed


# --- K7: Compute Weights (stubbed frappe) -------------------------------------

import importlib.util
import inspect
import sys
import types
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
import threading
import time

RUN_PY = os.path.join(EPM_DOCTYPES, RUN, "cost_allocation_run.py")
RUN_JS = os.path.join(EPM_DOCTYPES, RUN, "cost_allocation_run.js")


class _FakeLock:
    """redis-py Lock over a real threading.Lock: acquire() waits up to
    blocking_timeout and returns False on timeout, as redis-py does."""

    def __init__(self, world, name, timeout, blocking_timeout):
        self.world, self.name = world, name
        self.timeout, self.blocking_timeout = timeout, blocking_timeout
        self.inner = world.locks.setdefault(name, threading.Lock())

    def acquire(self):
        self.world.lock_requests.append((self.name, self.timeout, self.blocking_timeout))
        if self.world.lock_never_free:
            return False
        got = self.inner.acquire(timeout=self.blocking_timeout)
        if got:
            self.world.events.append("lock")
        return got

    def release(self):
        self.world.events.append("unlock")
        self.inner.release()


class _FakeRedis:
    """frappe.cache in v16: raw get/set (get returns bytes) and redis-py lock()."""

    def __init__(self, world):
        self.world = world

    def get(self, key):
        value = self.world.redis.get(key)
        return None if value is None else str(value).encode()

    def set(self, key, value, ex=None):
        self.world.redis[key] = value

    def lock(self, name, timeout=None, blocking_timeout=None):
        return _FakeLock(self.world, name, timeout, blocking_timeout)


class _FakeJob:
    def __init__(self, status):
        self.status = status

    def get_status(self, refresh=True):
        return self.status


def _fake_background_jobs(world):
    """frappe.utils.background_jobs.get_job over world.jobs (un-namespaced id ->
    RQ status string); None when RQ has no such job (expired or never made)."""
    bj = types.ModuleType("frappe.utils.background_jobs")
    bj.get_job = lambda job_id: (_FakeJob(world.jobs[job_id]) if job_id in world.jobs else None)
    return bj


class _Refused(Exception):
    pass


class _FakeFrappe:
    """Stands in for frappe: throw, whitelist (records methods), get_doc."""

    def __init__(self, run=None):
        self.run = run

    def whitelist(self, *a, **k):
        return lambda fn: fn

    def throw(self, message, *args, **kwargs):
        raise _Refused(message)

    def get_doc(self, doctype, name):
        return self.run


class _Row(dict):
    __getattr__ = dict.get

    def as_dict(self):
        return dict(self)


class _Run:
    """A draft run as the controller sees it; child tables are lists."""

    def __init__(self, docstatus=0, scenario="ZZ Budget", fiscal_year=2026, fiscal_period=3):
        self.name = "CAR-00001"
        self.docstatus = docstatus
        self.rule_set = "ZZ Rules"
        self.scenario = scenario
        self.fiscal_year = fiscal_year
        self.fiscal_period = fiscal_period
        self.entity = None
        self.status = "Draft"
        self.weights = [_Row(step=99)]   # a stale row that must be replaced
        self.accounts = [_Row(main_account="OLD")]
        self.saved = 0

    def set(self, field, rows):
        setattr(self, field, [_Row(r) for r in rows])

    def save(self):
        self.saved += 1


def _exec_run_module(alias):
    """Exec the Run controller with konsol.clickhouse / konsol.period_status
    stubbed (K8), restoring sys.modules afterwards. Tests rebind the module
    globals they care about."""
    ch = types.ModuleType("konsol.clickhouse")
    ps = types.ModuleType("konsol.period_status")
    stubs = {"konsol.clickhouse": ch, "konsol.period_status": ps}
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location(alias, RUN_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return module


def _run_module(rule_set=None, stats=None, carry=None, tree=None, user="zz@example.com"):
    from konsol.tests import test_cost_allocation_doctypes as dt  # noqa: F401  (stub helper)
    dt._install_document_stub()
    # the module-level @frappe.whitelist runs at import; give the stub one
    sys.modules["frappe"].whitelist = lambda *a, **k: (lambda fn: fn)
    module = _exec_run_module("cost_allocation_run_k7")
    module.frappe = _FakeFrappe()
    module._load_rule_set = lambda name: rule_set if rule_set is not None else _rule_set()
    module._statistic_values = lambda entity, scenario, fy, period: list(
        stats if stats is not None else _stats())
    module._driver_carry_forward = lambda drivers: dict(carry or {})
    module._descendants = lambda account: list((tree or _TREE)[account])
    module._first_draft_state = lambda: "Draft"
    module._now = lambda: datetime(2026, 11, 13, 9, 0, 0)
    module._user = lambda: user
    return module


_TREE = {"6000": ["6000", "6010", "6020"], "7000": ["7000"]}


def _rule_set(**over):
    rs = {
        "name": "ZZ Rules", "status": "Active", "entity": "ZZE",
        "allocation_dimension": "dim_zz", "method": "Step-Down",
        "receiver_account_mode": "Declared Allocation Account",
        "allocation_account": "6900", "amounts_after_approval": "Freeze at Approval",
        "modified": datetime(2026, 11, 12, 8, 0, 0),
        "applies_to_all_scenarios": 0,
        "scenarios": [_Row(scenario="ZZ Budget")],
        "accounts": [_Row(main_account="6000"), _Row(main_account="7000")],
        "rules": [_Row(step=1, sender_member="IT", driver="ZZ Head"),
                  _Row(step=2, sender_member="HR", driver="ZZ Head")],
        "receivers": [_Row(step=1, receiver_member="HR"), _Row(step=1, receiver_member="P1"),
                      _Row(step=2, receiver_member="P1"), _Row(step=2, receiver_member="P2")],
    }
    rs.update(over)
    return types.SimpleNamespace(**rs)


def _stat(statistic, period, member, value, driver="ZZ Head"):
    return {"statistic": statistic, "driver": driver, "fiscal_period": period,
            "member": member, "value": value}


def _stats():
    # period 3 for HR and P1, P2; P1 for step 1 has only period 2 (carried).
    return [_stat("CAS-00001", 3, "HR", 10), _stat("CAS-00001", 3, "P2", 30),
            _stat("CAS-00002", 2, "P1", 20), _stat("CAS-00001", 3, "P1", 20)]


def test_compute_happy_path_snapshots_step_down_run():
    m = _run_module()
    run = _Run()
    m.compute_weights_on(run)
    assert run.entity == "ZZE"
    assert (run.method, run.allocation_dimension) == ("Step-Down", "dim_zz")
    assert run.receiver_account_mode == "Declared Allocation Account"
    assert run.allocation_account == "6900"
    assert run.amounts_after_approval == "Freeze at Approval"
    assert run.rule_set_modified == datetime(2026, 11, 12, 8, 0, 0)
    assert run.computed_at == datetime(2026, 11, 13, 9, 0, 0)
    assert run.computed_by == "zz@example.com"
    assert run.saved == 1
    got = {(w["step"], w["receiver_member"]): w for w in run.weights}
    assert len(run.weights) == 4          # the stale row is gone
    assert got[(1, "HR")]["weight"] == float(Fraction(1, 3)) and got[(1, "P1")]["weight"] == float(Fraction(2, 3))
    assert got[(2, "P1")]["weight"] == 20 / 50 and got[(2, "P2")]["weight"] == 30 / 50
    w = got[(1, "HR")]
    assert (w["sender_member"], w["driver"], w["statistic"], w["value_period"],
            w["carried_forward"], w["driver_value"]) == ("IT", "ZZ Head", "CAS-00001", 3, 0, 10)


def test_compute_step_weights_are_floats_summing_to_one():
    m = _run_module()
    run = _Run()
    m.compute_weights_on(run)
    for step in (1, 2):
        total = sum(w["weight"] for w in run.weights if w["step"] == step)
        assert all(isinstance(w["weight"], float) for w in run.weights)
        assert abs(total - 1) < 1e-12


def test_compute_accounts_expand_a_groups_descendants():
    m = _run_module()
    run = _Run()
    m.compute_weights_on(run)
    rows = [(a["main_account"], a["declared_account"]) for a in run.accounts]
    assert rows == [("6000", "6000"), ("6010", "6000"), ("6020", "6000"), ("7000", "7000")]


def test_compute_carries_forward_and_counts():
    # P1 has no period-3 value for step 1; the driver declares carry-forward.
    stats = [_stat("CAS-00001", 3, "HR", 10), _stat("CAS-00001", 3, "P2", 30),
             _stat("CAS-00002", 2, "P1", 20), _stat("CAS-00001", 3, "P1", 20)]
    stats = [s for s in stats if not (s["member"] == "P1" and s["fiscal_period"] == 3)]
    m = _run_module(stats=stats, carry={"ZZ Head": True})
    run = _Run()
    result = m.compute_weights_on(run)
    carried = [w for w in run.weights if w["carried_forward"]]
    assert {(w["step"], w["receiver_member"]) for w in carried} == {(1, "P1"), (2, "P1")}
    assert all(w["value_period"] == 2 and w["statistic"] == "CAS-00002" for w in carried)
    assert result == {"carried_forward_count": 2, "weights": 4}
    assert run.values_carried_forward == 1


def test_compute_refusal_lists_every_missing_value_and_saves_nothing():
    stats = [_stat("CAS-00001", 3, "HR", 10)]
    m = _run_module(stats=stats)
    run = _Run()
    try:
        m.compute_weights_on(run)
    except _Refused as exc:
        msg = str(exc)
    else:
        raise AssertionError("expected a refusal")
    for expect in ("step 1, P1", "step 2, P1", "step 2, P2"):
        assert expect in msg, msg
    assert run.saved == 0
    assert [w["step"] for w in run.weights] == [99]   # untouched


def test_compute_refuses_a_non_draft_run():
    m = _run_module()
    for ds in (1, 2):
        run = _Run(docstatus=ds)
        try:
            m.compute_weights_on(run)
        except _Refused as exc:
            assert "draft" in str(exc).lower()
        else:
            raise AssertionError("expected a refusal")
        assert run.saved == 0


def test_compute_refuses_an_inactive_rule_set():
    for status in ("Draft", "Inactive"):
        m = _run_module(rule_set=_rule_set(status=status))
        try:
            m.compute_weights_on(_Run())
        except _Refused as exc:
            assert "Active" in str(exc)
        else:
            raise AssertionError("expected a refusal")


def test_compute_refuses_a_scenario_out_of_scope_but_allows_all_scenarios():
    m = _run_module(rule_set=_rule_set(scenarios=[_Row(scenario="Other")]))
    try:
        m.compute_weights_on(_Run())
    except _Refused as exc:
        assert "scenario" in str(exc).lower()
    else:
        raise AssertionError("expected a refusal")
    m = _run_module(rule_set=_rule_set(scenarios=[], applies_to_all_scenarios=1))
    run = _Run()
    m.compute_weights_on(run)
    assert run.saved == 1


def test_compute_method_is_post_only_and_dotted_path_exists():
    with open(RUN_PY) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "compute_weights")
    found = None
    for dec in fn.decorator_list:
        if (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == "whitelist"):
            for kw in dec.keywords:
                if kw.arg == "methods":
                    found = ast.literal_eval(kw.value)
    assert found == ["POST"]


def test_compute_whitelisted_function_loads_the_run_and_computes():
    m = _run_module()
    run = _Run()
    m.frappe = _FakeFrappe(run)
    m.compute_weights("CAR-00001")
    assert run.saved == 1 and len(run.weights) == 4


def test_compute_button_is_on_the_draft_run_form_and_posts():
    with open(RUN_JS) as f:
        js = f.read()
    assert "Compute Weights" in js
    assert "docstatus === 0" in js or "docstatus == 0" in js
    assert "konsol.epm.doctype.cost_allocation_run.cost_allocation_run.compute_weights" in js
    assert "reload_doc" in js


# --- K8: submit / cancel gates and warehouse sync (stubbed frappe) ---------------

class _Refusal(Exception):
    pass


class _World:
    """In-memory site for the K8 hooks."""

    def __init__(self):
        self.scenarios = {"ZZ Actual": {"scenario_type": "actual", "is_active": 1},
                          "ZZ Budget": {"scenario_type": "budget", "is_active": 1}}
        self.rule_set = {"modified": datetime(2026, 11, 12, 8, 0, 0), "status": "Active",
                         "applies_to_all_scenarios": 0}
        self.rs_scenarios = ["ZZ Actual", "ZZ Budget"]
        self.other_rows = []
        self.stat_rows = [
            {"statistic": "CAS-00001", "docstatus": 1, "fiscal_period": 3, "member": "HR", "value": 10},
            {"statistic": "CAS-00001", "docstatus": 1, "fiscal_period": 3, "member": "P1", "value": 20},
        ]
        self.sql = []
        self.open_calls = []
        self.closed = set()
        self.declared_calls = []
        self.undeclared = set()
        self.syncs = []
        self.once = []
        self.enqueued = []
        self.redis = {}
        self.jobs = {}
        self.locks = {}
        self.lock_requests = []
        self.lock_never_free = False
        self.events = []
        self.commit_callbacks = []
        self.logged = []
        self.sync_table_calls = []
        self.get_all_data = {}


def _k8(world):
    module = _exec_run_module("cost_allocation_run_k8")
    fr = types.SimpleNamespace()

    def throw(msg, *a, **k):
        raise _Refusal(msg)

    def get_value(doctype, name, fields=None, as_dict=False, **k):
        if doctype == "Scenario":
            row = world.scenarios.get(name)
            if row is None or isinstance(fields, str):
                return row[fields] if row is not None else None
            return _Row(row)
        if doctype == "Cost Allocation Rule Set":
            return _Row(world.rule_set) if name == "ZZ Rules" else None
        raise AssertionError(doctype)

    def get_all(doctype, filters=None, fields=None, pluck=None, **k):
        if doctype == "Cost Allocation Rule Set Scenario":
            # as the database would: only the named rule set's scenario rows
            return list(world.rs_scenarios) if (filters or {}).get("parent") == "ZZ Rules" else []
        return [_Row(r) for r in world.get_all_data.get(doctype, [])]

    def sql(query, values=None, as_dict=False, **k):
        world.sql.append((query, values))
        if "`tabCost Allocation Statistic Value`" in query:
            # the statistic re-read: only the cited statistics, as the IN clause says
            assert "IN %(statistics)s" in query
            return [_Row(r) for r in world.stat_rows if r["statistic"] in values["statistics"]]
        # the shared-sender read: each predicate filters only if the SQL has it
        out = []
        for r in world.other_rows:
            ok = True
            for key, col in (("entity", "r.entity"), ("scenario", "r.scenario"),
                             ("fiscal_year", "r.fiscal_year"), ("fiscal_period", "r.fiscal_period")):
                if f"{col} = %({key})s" in query and r.get(key, values[key]) != values[key]:
                    ok = False
            if "r.docstatus = 1" in query and r.get("docstatus", 1) != 1:
                ok = False
            if "r.name != %(name)s" in query and r["name"] == values["name"]:
                ok = False
            if ok:
                out.append(_Row(r))
        return out

    fr.throw = throw
    fr.whitelist = lambda *a, **k: (lambda fn: fn)
    fr.db = types.SimpleNamespace(get_value=get_value, sql=sql,
                                  rollback=lambda: world.events.append("rollback"))
    fr.get_all = get_all
    def enqueue(*a, **k):
        world.enqueued.append((a, k))
        world.jobs[k.get("job_id")] = "queued"     # RQ sets QUEUED at enqueue

    fr.enqueue = enqueue
    fr.local = types.SimpleNamespace(site="zz.local")
    fr.db.after_commit = types.SimpleNamespace(add=world.commit_callbacks.append)
    fr.cache = _FakeRedis(world)
    fr.logger = lambda *a, **k: types.SimpleNamespace(
        info=lambda msg, *x, **y: world.events.append(("log", msg)),
        warning=lambda msg, *x, **y: world.events.append(("warning", msg)),
        error=lambda msg, *x, **y: world.events.append(("error", msg)))
    fr.log_error = lambda *a, **k: world.logged.append((a, k))
    fr._dict = lambda **k: types.SimpleNamespace(**k)
    module.frappe = fr

    ps = types.SimpleNamespace()

    def assert_open(year, period, action="run"):
        world.open_calls.append((year, period))
        if (year, period) in world.closed:
            raise _Refusal(f"period {period} of FY{year} is closed")

    def assert_declared(year, period):
        world.declared_calls.append((year, period))
        if (year, period) in world.undeclared:
            raise _Refusal(f"period {period} of FY{year} is not declared")

    ps.assert_open = assert_open
    ps.assert_declared = assert_declared
    module.period_status = ps
    ch = types.SimpleNamespace()
    ch.sync_doctype_after_commit = lambda *a, **k: world.syncs.append(a)
    ch.after_commit_once = lambda key, fn: world.once.append((key, fn))
    ch.sync_table = lambda table, columns, rows, **k: world.sync_table_calls.append(
        (table, columns, rows, k)) or len(rows)
    module.clickhouse = ch
    return module


def _k8_run(module, scenario="ZZ Actual", **over):
    cls = module.CostAllocationRun
    doc = cls.__new__(cls)
    weights = [_Row(step=1, sender_member="IT", receiver_member="HR", driver="ZZ Head",
                    statistic="CAS-00001", value_period=3, driver_value=10),
               _Row(step=1, sender_member="IT", receiver_member="P1", driver="ZZ Head",
                    statistic="CAS-00001", value_period=3, driver_value=20),
               _Row(step=2, sender_member="HR", receiver_member="P1", driver="ZZ Head",
                    statistic="CAS-00001", value_period=3, driver_value=20)]
    base = dict(name="CAR-00001", doctype="Cost Allocation Run", docstatus=0,
                rule_set="ZZ Rules", entity="ZZE", scenario=scenario, fiscal_year=2026,
                fiscal_period=3, rule_set_modified=datetime(2026, 11, 12, 8, 0, 0),
                method="Step-Down", weights=weights,
                computed_for_rule_set="ZZ Rules", computed_for_scenario=scenario,
                computed_for_fiscal_year=2026, computed_for_fiscal_period=3)
    base.update(over)
    doc.__dict__.update(base)
    doc.get = lambda key, default=None: doc.__dict__.get(key, default)
    return doc


def _refused(fn):
    try:
        fn()
    except _Refusal as exc:
        return str(exc)
    raise AssertionError("expected a refusal")


def test_lifecycle_before_submit_passes_a_clean_actual_run():
    w = _World()
    m = _k8(w)
    _k8_run(m).before_submit()
    assert w.open_calls == [(2026, 3)]


def test_lifecycle_before_submit_refuses_a_closed_period_on_actual():
    w = _World()
    w.closed.add((2026, 3))
    m = _k8(w)
    assert "closed" in _refused(_k8_run(m).before_submit)


def test_lifecycle_before_submit_skips_the_period_gate_for_budget():
    w = _World()
    w.closed.add((2026, 3))
    m = _k8(w)
    _k8_run(m, scenario="ZZ Budget").before_submit()
    assert w.open_calls == []


def test_lifecycle_before_submit_refuses_without_weights():
    w = _World()
    m = _k8(w)
    msg = _refused(_k8_run(m, weights=[]).before_submit)
    assert "Compute Weights" in msg


def test_lifecycle_before_submit_refuses_a_rule_set_edited_since_compute():
    w = _World()
    w.rule_set["modified"] = datetime(2026, 11, 13, 9, 0, 0)
    m = _k8(w)
    assert "recompute" in _refused(_k8_run(m).before_submit).lower()


def test_lifecycle_before_submit_refuses_an_inactive_rule_set():
    w = _World()
    w.rule_set["status"] = "Inactive"
    m = _k8(w)
    assert "Active" in _refused(_k8_run(m).before_submit)


def test_lifecycle_before_submit_refuses_a_scenario_out_of_scope():
    w = _World()
    w.rs_scenarios = ["ZZ Budget"]
    m = _k8(w)
    assert "scope" in _refused(_k8_run(m).before_submit)
    w.rule_set["applies_to_all_scenarios"] = 1
    _k8_run(m).before_submit()


def test_lifecycle_before_submit_refuses_an_inactive_scenario():
    w = _World()
    w.scenarios["ZZ Actual"]["is_active"] = 0
    m = _k8(w)
    assert "active" in _refused(_k8_run(m).before_submit).lower()


def test_lifecycle_before_submit_refuses_a_shared_sender_and_locks_the_rows():
    w = _World()
    w.other_rows = [{"name": "CAR-00009", "sender_member": "HR"}]
    m = _k8(w)
    msg = _refused(_k8_run(m).before_submit)
    assert "CAR-00009" in msg and "HR" in msg
    query, values = w.sql[-1]
    assert query.rstrip().upper().endswith("FOR UPDATE")
    assert "docstatus = 1" in query and "name != %(name)s" in query
    for key in ("entity", "scenario", "fiscal_year", "fiscal_period"):
        assert key in values


def test_lifecycle_before_submit_allows_a_run_with_other_senders():
    w = _World()
    w.other_rows = [{"name": "CAR-00009", "sender_member": "OPS"}]
    m = _k8(w)
    _k8_run(m).before_submit()
    sender_reads = [q for q, _v in w.sql if "`tabCost Allocation Run Weight`" in q]
    assert len(sender_reads) == 1 and sender_reads[0].rstrip().upper().endswith("FOR UPDATE")


def test_lifecycle_before_cancel_is_gated_on_actual_only():
    w = _World()
    w.closed.add((2026, 3))
    m = _k8(w)
    assert "closed" in _refused(_k8_run(m, docstatus=1).before_cancel)
    w.open_calls.clear()
    _k8_run(m, scenario="ZZ Budget", docstatus=1).before_cancel()
    assert w.open_calls == []


def test_lifecycle_sync_is_queued_after_commit_and_nothing_runs_in_the_hook():
    w = _World()
    m = _k8(w)
    for hook in ("on_submit", "on_cancel", "after_delete"):
        w.syncs.clear()
        w.once.clear()
        w.enqueued.clear()
        w.jobs.clear()                 # no resync job on the site before this hook
        getattr(_k8_run(m, docstatus=1), hook)()
        assert [(s[0], s[1]) for s in w.syncs] == [
            ("Cost Allocation Run", "epm_staging.cost_allocation_runs"),
            ("Cost Allocation Run Account", "epm_staging.cost_allocation_run_accounts"),
            ("Cost Allocation Run Weight", "epm_staging.cost_allocation_run_weights")], hook
        assert w.once == [], hook      # RF4: the coefficients are a job, not an after-commit callback
        assert w.sync_table_calls == [], hook
        # RF7: nothing is enqueued inside the transaction; one after-commit
        # callback decides, after the commit, whether to enqueue
        assert w.enqueued == [], hook
        assert len(w.commit_callbacks) == 1, hook
        _with_rq(w, w.commit_callbacks.pop())
        (args, kw), = w.enqueued
        assert args == ("konsol.epm.doctype.cost_allocation_run.cost_allocation_run."
                        "resync_coefficients_job",), hook
        assert kw["run"] == "CAR-00001", hook


def test_lifecycle_no_sync_on_a_draft_save_or_validate():
    with open(RUN_PY) as f:
        tree = ast.parse(f.read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CostAllocationRun")
    hooks = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    assert not hooks & {"on_update", "after_insert", "on_trash"}


def test_lifecycle_maps_and_filters():
    w = _World()
    m = _k8(w)
    cls = m.CostAllocationRun
    assert cls.CH_TABLE == "epm_staging.cost_allocation_runs"
    assert cls.CH_STAGING_TABLE == "epm_staging.cost_allocation_coefficients"
    assert cls.CH_FIELD_MAP == {
        "name": "name", "rule_set": "rule_set", "method": "method",
        "allocation_dimension": "allocation_dimension",
        "receiver_account_mode": "receiver_account_mode",
        "allocation_account": "allocation_account",
        "amounts_after_approval": "amounts_after_approval",
        "data_area_id": "entity", "scenario_id": "scenario",
        "fiscal_year": "fiscal_year", "fiscal_period": "fiscal_period",
        "computed_at": "computed_at"}
    acc = cls.CHILD_CONTROLLERS["Cost Allocation Run Account"]
    assert acc.CH_TABLE == "epm_staging.cost_allocation_run_accounts"
    assert acc.CH_FIELD_MAP == {"parent": "parent", "idx": "idx",
                                "main_account": "main_account",
                                "declared_account": "declared_account"}
    wt = cls.CHILD_CONTROLLERS["Cost Allocation Run Weight"]
    assert wt.CH_TABLE == "epm_staging.cost_allocation_run_weights"
    assert list(wt.CH_FIELD_MAP) == [
        "parent", "idx", "step", "sender_member", "receiver_member", "driver",
        "statistic", "value_period", "carried_forward", "driver_value"]
    assert acc.CH_SYNC_FILTERS == wt.CH_SYNC_FILTERS == {"docstatus": 1}


def test_lifecycle_no_commit_and_no_save_in_the_hooks():
    with open(RUN_PY) as f:
        src = f.read()
    assert "db.commit" not in src
    assert src.count(".save(") == 1   # only compute_weights_on's, on a draft


def test_lifecycle_is_not_an_approval_doctype():
    path = os.path.join(APP_DIR, "close", "close_policy_model.py")
    with open(path) as f:
        src = f.read()
    block = src.split("APPROVAL_DOCTYPES = (")[1].split(")")[0]
    assert "Cost Allocation Run" not in block


# A 3-sender step-down. Driver values chosen so the exact weights are not
# binary floats: IT -> HR 0.1, P1 0.2, P2 0.3 (1/6, 1/3, 1/2); HR -> P1 1, P2 3.
def _stored_weights():
    rows = [
        (1, "IT", "HR", 0.1), (1, "IT", "P1", 0.2), (1, "IT", "P2", 0.3),
        (2, "HR", "P1", 1.0), (2, "HR", "P2", 3.0),
    ]
    return [{"parent": "CAR-00001", "idx": i + 1, "step": s, "sender_member": snd,
             "receiver_member": rcv, "driver": "ZZ Head", "driver_value": v,
             # the float weight is deliberately lossy: it must not be used
             "weight": float(Fraction(str(v)) / sum(Fraction(str(x[3])) for x in rows if x[0] == s))}
            for i, (s, snd, rcv, v) in enumerate(rows)]


def _expected_rows():
    from konsol import cost_allocation_model as model
    steps = model.build_steps(
        [{"step": 1, "sender_member": "IT", "driver": "ZZ Head"},
         {"step": 2, "sender_member": "HR", "driver": "ZZ Head"}],
        [{"step": 1, "receiver_member": "HR"}, {"step": 1, "receiver_member": "P1"},
         {"step": 1, "receiver_member": "P2"}, {"step": 2, "receiver_member": "P1"},
         {"step": 2, "receiver_member": "P2"}])
    exact = {(1, "HR"): Fraction(1, 6), (1, "P1"): Fraction(1, 3), (1, "P2"): Fraction(1, 2),
             (2, "P1"): Fraction(1, 4), (2, "P2"): Fraction(3, 4)}
    return model.coefficient_rows("CAR-00001", steps, exact, "Step-Down")


def test_lifecycle_coefficient_resync_rebuilds_exact_rows_from_stored_driver_values():
    w = _World()
    w.get_all_data["Cost Allocation Run"] = [
        {"name": "CAR-00001", "method": "Step-Down", "modified": datetime(2026, 11, 13, 9, 0, 0)}]
    w.get_all_data["Cost Allocation Run Weight"] = _stored_weights()
    m = _k8(w)
    written = m.CostAllocationRun._write_coefficients(force=True)
    (table, columns, rows, kw), = w.sync_table_calls
    assert table == "epm_staging.cost_allocation_coefficients"
    assert columns == ["run", "origin_member", "step", "sender_member", "receiver_member",
                       "driver", "hop_kind", "pool_coefficient", "coefficient"]
    assert kw["force"] is True and written == len(rows)
    expected = [[r[c] for c in columns] for r in _expected_rows()]
    assert rows == expected
    by = {(r[1], r[2], r[4]): r for r in rows}
    # IT's pool reaches P1 directly (1/3) and through HR (1/6 * 1/4): exact
    assert by[("IT", 2, "P1")][7] == Decimal("0.166666666667")
    # every hop's receivers sum exactly to the hop's pool coefficient
    hops = {}
    for r in rows:
        hops.setdefault((r[1], r[2], r[3]), [r[7], Decimal(0)])[1] += r[8]
    assert all(pool == total for pool, total in hops.values())


def test_lifecycle_coefficient_resync_with_no_submitted_runs_empties_the_table():
    w = _World()
    m = _k8(w)
    m.CostAllocationRun._write_coefficients()
    (table, columns, rows, kw), = w.sync_table_calls
    assert rows == [] and kw["force"] is False


def test_lifecycle_coefficient_resync_reads_submitted_runs_only():
    w = _World()
    seen = []
    m = _k8(w)
    orig = m.frappe.get_all

    def spy(doctype, filters=None, **k):
        seen.append((doctype, filters))
        return orig(doctype, filters=filters, **k)

    m.frappe.get_all = spy
    m.CostAllocationRun._write_coefficients()
    assert ("Cost Allocation Run", {"docstatus": 1}) in seen


# --- K12: Preview Sender Pools (stubbed frappe) ---------------------------------

_DECLARED = ["dim_zz", "dim_other"]


class _PreviewRun(_Run):
    def __init__(self, with_weights=True, **kw):
        super().__init__(**kw)
        self.entity = "ZZE"
        self.allocation_dimension = "dim_zz"
        self.weights = ([_Row(step=1, sender_member="IT"), _Row(step=1, sender_member="IT"),
                         _Row(step=2, sender_member="HR")] if with_weights else [])
        self.accounts = [_Row(main_account="6000"), _Row(main_account="6010")]

    def check_permission(self, *a, **k):
        pass


def _preview_module(run=None, rows=None):
    m = _run_module()
    m.frappe = _FakeFrappe(run)
    m._declared_dimensions = lambda: list(_DECLARED)
    seen = {}

    def query(sql, params):
        seen["sql"], seen["params"] = sql, params
        return list(rows or [])
    m._query_sender_pools = query
    m.seen = seen
    return m


def _build(m, **over):
    args = dict(dimension="dim_zz", declared=_DECLARED, scenario="ZZ Actual", entity="ZZE",
                fiscal_year=2026, fiscal_period=3, accounts=["6000", "6010"], senders=["IT", "HR"])
    args.update(over)
    return m.build_sender_pool_query(**args)


def test_preview_sql_is_parameterised_and_holds_no_values():
    m = _preview_module()
    sql, params = _build(m, scenario="ZZ'; DROP TABLE x; --", entity="ZZE", accounts=["6000"], senders=["IT"])
    for value in ("ZZ'; DROP", "ZZE", "6000", "IT", "2026"):
        assert value not in sql
    assert "{" in sql and all(k.startswith("param_") for k in params)
    assert "DROP" in " ".join(str(v) for v in params.values())


def test_preview_filters_cover_scenario_entity_period_accounts_and_senders():
    m = _preview_module()
    sql, params = _build(m)
    assert "epm_gold.gold_scenario_trial_balance" in sql
    for col in ("scenario_id", "data_area_id", "fiscal_year", "fiscal_period", "main_account"):
        assert col in sql
    assert "`dim_zz` IN" in sql and "GROUP BY" in sql
    vals = " ".join(str(v) for v in params.values())
    for v in ("ZZ Actual", "ZZE", "2026", "3", "6000", "6010", "IT", "HR"):
        assert v in vals


def test_preview_identifier_is_validated_against_declared_dimensions():
    m = _preview_module()
    for bad in ("dim_missing", "dim_zz`; DROP", ""):
        try:
            _build(m, dimension=bad)
        except (_Refused, ValueError, Exception) as exc:
            assert "dimension" in str(exc).lower()
        else:
            raise AssertionError("undeclared dimension accepted: " + bad)


def test_preview_identifier_that_is_declared_but_not_an_identifier_is_refused():
    m = _preview_module()
    try:
        _build(m, dimension="a b`", declared=["a b`"])
    except Exception as exc:
        assert "dimension" in str(exc).lower()
    else:
        raise AssertionError("unsafe identifier accepted")


def test_preview_returns_pool_per_sender_and_total():
    run = _PreviewRun()
    m = _preview_module(run, rows=[("IT", "100.50"), ("HR", "40")])
    out = m.preview_sender_pools("CAR-00001")
    assert [(p["sender_member"], p["amount"]) for p in out["pools"]] == [("IT", 100.5), ("HR", 40.0)]
    assert out["total"] == 140.5 and out["has_rows"] is True
    assert m.seen["params"]["param_senders"] in ("['IT','HR']", "['HR','IT']")


def test_preview_with_no_warehouse_rows_returns_zeros_not_an_error():
    m = _preview_module(_PreviewRun(), rows=[])
    out = m.preview_sender_pools("CAR-00001")
    assert [(p["sender_member"], p["amount"]) for p in out["pools"]] == [("IT", 0), ("HR", 0)]
    assert out["total"] == 0 and out["has_rows"] is False


def test_preview_refuses_a_run_without_weights():
    m = _preview_module(_PreviewRun(with_weights=False))
    try:
        m.preview_sender_pools("CAR-00001")
    except _Refused as exc:
        assert "compute" in str(exc).lower()
    else:
        raise AssertionError("run without weights accepted")
    assert "sql" not in m.seen


def test_preview_method_is_get_and_writes_nothing():
    with open(RUN_PY) as f:
        tree = ast.parse(f.read())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "preview_sender_pools")
    methods = None
    for dec in fn.decorator_list:
        if isinstance(dec, ast.Call):
            for kw in dec.keywords:
                if kw.arg == "methods":
                    methods = ast.literal_eval(kw.value)
    assert methods == ["GET"]
    run = _PreviewRun()
    m = _preview_module(run, rows=[("IT", "1")])
    m.preview_sender_pools("CAR-00001")
    assert run.saved == 0
    body = ast.dump(fn)
    for writer in ("save", "insert", "db_set", "commit", "enqueue"):
        assert writer not in body


def test_preview_button_is_on_the_draft_form_and_shows_a_table():
    with open(RUN_JS) as f:
        js = f.read()
    assert "Preview Sender Pools" in js
    assert "konsol.epm.doctype.cost_allocation_run.cost_allocation_run.preview_sender_pools" in js
    assert "frappe.ui.Dialog" in js or "frappe.msgprint" in js
    assert "has_rows" in js


# --- KF1 -------------------------------------------------------------------------

def test_kf1_other_scope_runs_do_not_clash_and_the_filters_are_in_the_sql():
    # finding 10: the stub applies the SQL's own predicates, so a dropped
    # predicate lets these foreign-scope rows through and the test fails.
    w = _World()
    for key, other in (("scenario", "ZZ Budget"), ("fiscal_year", 2025), ("fiscal_period", 4),
                       ("entity", "ZZOTHER")):
        w.other_rows = [{"name": "CAR-00009", "sender_member": "HR", key: other}]
        _k8_run(_k8(w), scenario="ZZ Actual").before_submit()
    w.other_rows = [{"name": "CAR-00009", "sender_member": "HR", "docstatus": 2}]
    _k8_run(_k8(w)).before_submit()


def test_kf1_rule_set_scope_is_read_for_the_runs_own_rule_set():
    w = _World()
    w.rs_scenarios = ["ZZ Budget"]
    assert "scope" in _refused(_k8_run(_k8(w), rule_set="ZZ Rules").before_submit)
    # another rule set name finds nothing (get_value returns None): refused, not passed
    assert _refused(_k8_run(_k8(w), rule_set="ZZ Other").before_submit)


def test_kf1_doctype_weights_accounts_and_computed_fields_are_no_copy():
    fields = _by_name(RUN)
    for name in ("weights", "accounts", "computed_at", "computed_by", "rule_set_modified",
                 "values_carried_forward", "computed_for_rule_set", "computed_for_scenario",
                 "computed_for_fiscal_year", "computed_for_fiscal_period"):
        assert fields[name].get("no_copy") == 1, f"{name} is copied by Duplicate"


def test_kf1_compute_snapshots_what_the_weights_were_computed_for():
    m = _run_module()
    run = _Run(scenario="ZZ Budget", fiscal_year=2026, fiscal_period=3)
    m.compute_weights_on(run)
    assert (run.computed_for_rule_set, run.computed_for_scenario,
            run.computed_for_fiscal_year, run.computed_for_fiscal_period) == (
                "ZZ Rules", "ZZ Budget", 2026, 3)


def test_kf1_before_submit_refuses_weights_computed_for_another_scope():
    for field, other in (("rule_set", "ZZ Rules2"), ("scenario", "ZZ Budget"),
                         ("fiscal_year", 2027), ("fiscal_period", 4)):
        w = _World()
        m = _k8(w)
        run = _k8_run(m, scenario="ZZ Actual")
        run.__dict__["computed_for_" + field] = other
        msg = _refused(run.before_submit)
        assert "recompute" in msg.lower() and field.replace("_", " ") in msg.lower(), (field, msg)


def test_kf1_before_submit_refuses_a_run_never_computed_for_a_scope():
    w = _World()
    run = _k8_run(_k8(w), computed_for_rule_set=None, computed_for_fiscal_year=0)
    assert "recompute" in _refused(run.before_submit).lower()


def test_kf1_before_submit_refuses_a_cancelled_statistic_and_locks_the_rows():
    w = _World()
    w.stat_rows[0]["docstatus"] = 2
    msg = _refused(_k8_run(_k8(w)).before_submit)
    assert "CAS-00001" in msg and "HR" in msg
    query, _values = next(x for x in w.sql if "`tabCost Allocation Statistic Value`" in x[0])
    assert query.rstrip().upper().endswith("FOR UPDATE")


def test_kf1_before_submit_refuses_a_statistic_value_that_changed():
    w = _World()
    w.stat_rows[1]["value"] = 25
    msg = _refused(_k8_run(_k8(w)).before_submit)
    assert "CAS-00001" in msg and "P1" in msg


def test_kf1_before_submit_refuses_a_statistic_value_that_is_gone():
    w = _World()
    w.stat_rows = [r for r in w.stat_rows if r["member"] != "HR"]
    msg = _refused(_k8_run(_k8(w)).before_submit)
    assert "CAS-00001" in msg and "HR" in msg


def test_kf1_before_submit_lists_every_stale_statistic_row():
    w = _World()
    w.stat_rows[0]["docstatus"] = 2
    w.stat_rows[1]["value"] = 25
    msg = _refused(_k8_run(_k8(w)).before_submit)
    assert "HR" in msg and "P1" in msg


def test_kf1_before_submit_passes_when_the_statistic_values_still_match():
    w = _World()
    w.stat_rows[0]["value"] = 10.0
    _k8_run(_k8(w)).before_submit()


def test_kf1_before_submit_declares_the_period_on_every_scenario():
    for scenario in ("ZZ Actual", "ZZ Budget"):
        w = _World()
        _k8_run(_k8(w), scenario=scenario).before_submit()
        assert w.declared_calls == [(2026, 3)], scenario
    w = _World()
    w.undeclared.add((2026, 13))
    run = _k8_run(_k8(w), scenario="ZZ Budget", fiscal_period=13)
    assert "not declared" in _refused(run.before_submit)


def test_kf1_submit_period_exception_cites_its_decision():
    # The actual-only submit gate was decided on konsol#269 (14 Nov 2026).
    with open(RUN_PY) as f:
        text = f.read()
    assert "14 Nov 2026, konsol#269 comment 6874924763" in text


def test_kf1_before_submit_refuses_a_run_whose_coefficients_cannot_be_built():
    # finding 7: all driver values zero -> the coefficient build refuses; the
    # submit must refuse, not queue a sync that fails for every run.
    w = _World()
    m = _k8(w)
    run = _k8_run(m)
    for row in run.weights:
        row["driver_value"] = 0
    w.stat_rows = [dict(r, value=0) for r in w.stat_rows]
    msg = _refused(run.before_submit)
    assert "zero" in msg.lower() and "CAR-00001" in msg


def test_kf1_compute_requires_status_draft():
    m = _run_module()
    for status in ("Pending Approval", "Approved", "Cancelled"):
        run = _Run()
        run.status = status
        try:
            m.compute_weights_on(run)
        except _Refused as exc:
            assert "Draft" in str(exc)
        else:
            raise AssertionError("expected a refusal in " + status)
        assert run.saved == 0


def test_kf1_compute_button_only_in_draft_status():
    with open(RUN_JS) as f:
        js = f.read()
    assert 'frm.doc.status === "Draft"' in js


# --- KF3 ----------------------------------------------------------------------

_COMPUTED = ("computed_at", "computed_by", "computed_for_rule_set", "computed_for_scenario",
             "computed_for_fiscal_year", "computed_for_fiscal_period",
             "rule_set_modified", "values_carried_forward")


def test_kf3_amended_insert_clears_the_computed_snapshot():
    # Frappe v16's client copy_doc ignores no_copy when amending, so the server clears.
    m = _k8(_World())
    run = _k8_run(m, amended_from="CAR-00001", accounts=[_Row(main_account="6000")],
                  computed_at=datetime(2026, 11, 12), computed_by="zz@example.com",
                  values_carried_forward=1)
    run.set = lambda field, rows: run.__dict__.__setitem__(field, list(rows))
    run.before_insert()
    assert run.weights == [] and run.accounts == []
    for name in _COMPUTED:
        assert not run.__dict__.get(name), name


def test_kf3_plain_insert_keeps_its_fields():
    m = _k8(_World())
    run = _k8_run(m, amended_from=None)
    run.set = lambda field, rows: run.__dict__.__setitem__(field, list(rows))
    run.before_insert()
    assert len(run.weights) == 3 and run.computed_for_rule_set == "ZZ Rules"


def test_kf3_compute_checks_draft_against_the_workflows_first_draft_state():
    m = _run_module()
    m._first_draft_state = lambda: "Intake"
    ok = _Run()
    ok.status = "Intake"
    m.compute_weights_on(ok)
    assert ok.saved == 1
    renamed = _Run()
    renamed.status = "Draft"
    try:
        m.compute_weights_on(renamed)
    except _Refused as exc:
        assert "Intake" in str(exc)
    else:
        raise AssertionError("expected a refusal")


def test_kf3_compute_without_a_workflow_checks_docstatus_only():
    m = _run_module()
    m._first_draft_state = lambda: None
    run = _Run()
    run.status = "Anything"
    m.compute_weights_on(run)
    assert run.saved == 1
    try:
        m.compute_weights_on(_Run(docstatus=1))
    except _Refused:
        pass
    else:
        raise AssertionError("expected a refusal")


# --- RC4 (konsol#269 phase 2a): Reciprocal through Compute, submit and the sync ----

def _recip_rule_set():
    return _rule_set(
        method="Reciprocal",
        rules=[_Row(step=1, sender_member="IT", driver="Tickets"),
               _Row(step=2, sender_member="HR", driver="Headcount")],
        receivers=[_Row(step=1, receiver_member="HR"), _Row(step=1, receiver_member="Sales"),
                   _Row(step=1, receiver_member="Ops"), _Row(step=2, receiver_member="IT"),
                   _Row(step=2, receiver_member="Sales"), _Row(step=2, receiver_member="Ops")])


def _recip_stats():
    return [_stat("CAS-00001", 3, "HR", 5, "Tickets"), _stat("CAS-00001", 3, "Sales", 3, "Tickets"),
            _stat("CAS-00001", 3, "Ops", 2, "Tickets"), _stat("CAS-00002", 3, "IT", 4, "Headcount"),
            _stat("CAS-00002", 3, "Sales", 4, "Headcount"), _stat("CAS-00002", 3, "Ops", 2, "Headcount")]


def _recip_stored():
    rows = [(1, "IT", "HR", 5, "Tickets"), (1, "IT", "Sales", 3, "Tickets"), (1, "IT", "Ops", 2, "Tickets"),
            (2, "HR", "IT", 4, "Headcount"), (2, "HR", "Sales", 4, "Headcount"),
            (2, "HR", "Ops", 2, "Headcount")]
    return [{"parent": "CAR-00001", "idx": i + 1, "step": s, "sender_member": snd,
             "receiver_member": rcv, "driver": drv, "driver_value": v}
            for i, (s, snd, rcv, v, drv) in enumerate(rows)]


def test_rc4_compute_snapshots_reciprocal_and_the_weights_build_exact_rows():
    from konsol import cost_allocation_model as model
    m = _run_module(rule_set=_recip_rule_set(), stats=_recip_stats())
    run = _Run()
    m.compute_weights_on(run)
    assert run.method == "Reciprocal" and run.saved == 1
    stored = [{**w, "driver_value": w["driver_value"]} for w in run.weights]
    rows = m._coefficient_rows("CAR-00001", "Reciprocal", stored)
    assert rows and {r["hop_kind"] for r in rows} == {"own", "pass"}


def test_rc4_coefficient_resync_for_reciprocal_carries_hop_kind_and_the_models_rows():
    from konsol import cost_allocation_model as model
    w = _World()
    w.get_all_data["Cost Allocation Run"] = [
        {"name": "CAR-00001", "method": "Reciprocal", "modified": datetime(2026, 11, 13, 9, 0, 0)}]
    w.get_all_data["Cost Allocation Run Weight"] = _recip_stored()
    m = _k8(w)
    m.CostAllocationRun._write_coefficients(force=True)
    (table, columns, rows, kw), = w.sync_table_calls
    assert columns == list(model.COEFFICIENT_COLUMNS) and "hop_kind" in columns
    steps = model.build_steps(
        [{"step": 1, "sender_member": "IT", "driver": "Tickets"},
         {"step": 2, "sender_member": "HR", "driver": "Headcount"}],
        [{"step": 1, "receiver_member": r} for r in ("HR", "Sales", "Ops")]
        + [{"step": 2, "receiver_member": r} for r in ("IT", "Sales", "Ops")])
    exact = {(1, "HR"): Fraction(5, 10), (1, "Sales"): Fraction(3, 10), (1, "Ops"): Fraction(2, 10),
             (2, "IT"): Fraction(4, 10), (2, "Sales"): Fraction(4, 10), (2, "Ops"): Fraction(2, 10)}
    expected = model.coefficient_rows("CAR-00001", steps, exact, "Reciprocal")
    assert rows == [[r[c] for c in columns] for r in expected]
    kinds = {r[columns.index("hop_kind")] for r in rows}
    assert kinds == {"own", "pass"}


def test_rc4_submit_accepts_a_reciprocal_run_and_refuses_a_closed_loop():
    w = _World()
    w.stat_rows = [{"statistic": r["statistic"], "docstatus": 1, "fiscal_period": 3,
                    "member": r["member"], "value": r["value"]} for r in _recip_stats()]
    stored = [_Row(step=x["step"], sender_member=x["sender_member"], receiver_member=x["receiver_member"],
                   driver=x["driver"], statistic="CAS-00001" if x["step"] == 1 else "CAS-00002",
                   value_period=3, driver_value=x["driver_value"]) for x in _recip_stored()]
    _k8_run(_k8(w), method="Reciprocal", weights=stored).before_submit()
    loop = [_Row(step=1, sender_member="IT", receiver_member="HR", driver="Tickets",
                 statistic="CAS-00001", value_period=3, driver_value=5),
            _Row(step=2, sender_member="HR", receiver_member="IT", driver="Headcount",
                 statistic="CAS-00002", value_period=3, driver_value=4)]
    w2 = _World()
    w2.stat_rows = [{"statistic": "CAS-00001", "docstatus": 1, "fiscal_period": 3,
                     "member": "HR", "value": 5},
                    {"statistic": "CAS-00002", "docstatus": 1, "fiscal_period": 3,
                     "member": "IT", "value": 4}]
    msg = _refused(_k8_run(_k8(w2), method="Reciprocal", weights=loop).before_submit)
    assert "IT" in msg and "HR" in msg and "final receiver" in msg


# --- RF4: coefficients resync as a job; Compute reports an unsolvable loop ----------

def _job_tasks_stub(calls):
    tasks = types.ModuleType("konsol.tasks")
    tasks.request_consolidation_build = lambda doctype, name, trigger_method: calls.append(
        (doctype, name, trigger_method))
    return tasks


def test_rf4_job_resyncs_then_requests_the_allocation_build():
    w = _World()
    m = _k8(w)
    order = []
    m.CostAllocationRun._write_coefficients = classmethod(lambda cls, force=False: order.append("resync"))
    tasks = _job_tasks_stub(order)
    saved = sys.modules.get("konsol.tasks")
    sys.modules["konsol.tasks"] = tasks
    try:
        m.resync_coefficients_job(run="CAR-00001")
    finally:
        if saved is None:
            sys.modules.pop("konsol.tasks", None)
        else:
            sys.modules["konsol.tasks"] = saved
    assert order == ["resync", ("Cost Allocation Run", "CAR-00001", "coefficients_resync")]


def test_rf4_a_failing_job_raises_logs_and_requests_no_build():
    w = _World()
    m = _k8(w)
    calls = []

    def boom(cls, force=False):
        raise RuntimeError("clickhouse down")

    m.CostAllocationRun._write_coefficients = classmethod(boom)
    saved = sys.modules.get("konsol.tasks")
    sys.modules["konsol.tasks"] = _job_tasks_stub(calls)
    try:
        try:
            m.resync_coefficients_job(run="CAR-00001")
        except RuntimeError as exc:
            assert "clickhouse down" in str(exc)
        else:
            raise AssertionError("the job swallowed the failure")
    finally:
        if saved is None:
            sys.modules.pop("konsol.tasks", None)
        else:
            sys.modules["konsol.tasks"] = saved
    assert calls == [] and len(w.logged) == 1


def test_rf4_the_run_trigger_is_not_a_doc_event_any_more():
    """The job requests the allocation build after the resync; a doc-event
    trigger would race it (the build could read stale coefficients)."""
    with open(os.path.join(os.path.dirname(RUN_PY), "..", "..", "..", "hooks.py")) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "_dbt_trigger_doctypes" for t in node.targets):
            names = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
            assert "Cost Allocation Run" not in names
            return
    raise AssertionError("_dbt_trigger_doctypes not found")


def test_rf4_compute_refuses_a_reciprocal_loop_whose_exit_edges_have_zero_statistics():
    rs = _rule_set(
        method="Reciprocal",
        rules=[_Row(step=1, sender_member="IT", driver="ZZ Head"),
               _Row(step=2, sender_member="HR", driver="ZZ Head")],
        receivers=[_Row(step=1, receiver_member="HR"), _Row(step=1, receiver_member="P1"),
                   _Row(step=2, receiver_member="IT"), _Row(step=2, receiver_member="P1")])
    stats = [_stat("CAS-00001", 3, "HR", 10), _stat("CAS-00001", 3, "IT", 10),
             _stat("CAS-00001", 3, "P1", 0)]
    m = _run_module(rule_set=rs, stats=stats)
    run = _Run()
    try:
        m.compute_weights_on(run)
    except _Refused as exc:
        assert str(exc)
    else:
        raise AssertionError("Compute accepted a loop whose cost can never leave")
    assert run.saved == 0


# --- RF7: after-commit enqueue, one job at a time, fresh read --------------------

JOB = "konsol.epm.doctype.cost_allocation_run.cost_allocation_run.resync_coefficients_job"
#: frappe.enqueue's own parameters (Frappe v16 background_jobs.enqueue), which
#: can never reach the job as kwargs
ENQUEUE_OWN = {"method", "queue", "timeout", "event", "is_async", "async", "job_name", "now",
               "enqueue_after_commit", "on_success", "on_failure", "at_front", "job_id",
               "deduplicate", "at_front_when_starved"}


def _with_rq(w, fn):
    saved = sys.modules.get("frappe.utils.background_jobs")
    sys.modules["frappe.utils.background_jobs"] = _fake_background_jobs(w)
    try:
        return fn()
    finally:
        if saved is None:
            sys.modules.pop("frappe.utils.background_jobs", None)
        else:
            sys.modules["frappe.utils.background_jobs"] = saved


def _commit(w, m, name, hook="on_submit"):
    """The hook runs, then the transaction commits and its callbacks fire."""
    w.commit_callbacks.clear()
    getattr(_k8_run(m, name=name, docstatus=1), hook)()
    for callback in list(w.commit_callbacks):
        _with_rq(w, callback)


def test_rf7_no_job_on_the_site_enqueues_one_after_the_commit():
    w = _World()
    m = _k8(w)
    _commit(w, m, "CAR-00001")
    (args, kw), = w.enqueued
    assert args == (JOB,) and kw["run"] == "CAR-00001" and kw["queue"] == "long"


def test_rf7_a_queued_job_is_reused_because_it_has_not_read_yet():
    w = _World()
    m = _k8(w)
    _commit(w, m, "CAR-00001")
    _commit(w, m, "CAR-00002")             # job 1 still QUEUED: it reads after this commit
    assert len(w.enqueued) == 1


def test_rf7_rc8b_race_a_started_job_does_not_cover_a_later_commit():
    """RC8b: run B committed ~0.86 s after its on_submit while job A ran, and
    B was lost. A STARTED job may have read already, so B gets its own job."""
    w = _World()
    m = _k8(w)
    _commit(w, m, "CAR-00009")
    first = w.enqueued[0][1]["job_id"]
    w.jobs[first] = "started"              # the worker picked job A up
    _commit(w, m, "CAR-00011")             # B commits while A runs
    assert len(w.enqueued) == 2
    second = w.enqueued[1][1]["job_id"]
    assert second != first                 # never the id of the STARTED job
    assert w.enqueued[1][1]["run"] == "CAR-00011"


def test_rf7_a_finished_failed_or_expired_job_does_not_cover_a_commit():
    for status in ("finished", "failed", "stopped", "canceled", None):
        w = _World()
        m = _k8(w)
        _commit(w, m, "CAR-00001")
        first = w.enqueued[0][1]["job_id"]
        if status is None:
            del w.jobs[first]              # RQ result TTL expired
        else:
            w.jobs[first] = status
        _commit(w, m, "CAR-00002")
        assert len(w.enqueued) == 2, status


def test_rf7_cancel_and_delete_take_the_same_path():
    for hook in ("on_cancel", "after_delete"):
        w = _World()
        m = _k8(w)
        _commit(w, m, "CAR-00003", hook=hook)
        assert [kw["run"] for _a, kw in w.enqueued] == ["CAR-00003"], hook


def test_rf7_enqueue_kwargs_avoid_enqueues_own_parameters_and_its_dedupe():
    w = _World()
    m = _k8(w)
    _commit(w, m, "CAR-00001")
    (_args, kw), = w.enqueued
    job_kwargs = set(kw) - {"queue", "job_id", "timeout"}
    assert job_kwargs == {"run"}
    assert not job_kwargs & ENQUEUE_OWN
    assert "deduplicate" not in kw and "enqueue_after_commit" not in kw
    assert "run" in inspect.signature(m.resync_coefficients_job).parameters


def test_rf7_the_generation_counter_is_gone():
    with open(RUN_PY) as f:
        src = f.read()
    for gone in ("_bump_generation", "_generation", "MAX_RESYNC_PASSES", "deduplicate=True"):
        assert gone not in src, gone


def _job(w, m, run="CAR-00001", resync=None, calls=None):
    calls = [] if calls is None else calls

    def default(cls, force=False):
        w.events.append(("resync", force))
        return 7

    m.CostAllocationRun._write_coefficients = classmethod(resync or default)
    tasks = _job_tasks_stub(calls)
    tasks.request_consolidation_build = lambda doctype, name, trigger_method: (
        calls.append((doctype, name, trigger_method)),
        w.events.append(("build", name)))
    saved = sys.modules.get("konsol.tasks")
    sys.modules["konsol.tasks"] = tasks
    try:
        m.resync_coefficients_job(run=run)
    finally:
        if saved is None:
            sys.modules.pop("konsol.tasks", None)
        else:
            sys.modules["konsol.tasks"] = saved
    return calls


def test_rf7_job_locks_rolls_back_resyncs_forced_then_requests_the_build_for_the_run():
    w = _World()
    m = _k8(w)
    calls = _job(w, m, run="CAR-00011")
    steps = [e for e in w.events if not (isinstance(e, tuple) and e[0] == "log")]
    assert steps == ["lock", "rollback", ("resync", True), "unlock", ("build", "CAR-00011")]
    assert calls == [("Cost Allocation Run", "CAR-00011", "coefficients_resync")]
    (name, timeout, wait), = w.lock_requests
    assert name.endswith("zz.local") and timeout and wait


def test_rf7_job_logs_one_line_with_run_rows_and_duration():
    w = _World()
    m = _k8(w)
    _job(w, m, run="CAR-00011")
    logs = [e[1] for e in w.events if isinstance(e, tuple) and e[0] == "log"]
    assert len(logs) == 1
    assert "CAR-00011" in logs[0] and "7 rows" in logs[0] and re.search(r"\d+\.\d+ ?s", logs[0])


def test_rf7_a_clickhouse_failure_raises_logs_and_requests_no_build():
    w = _World()
    m = _k8(w)

    def boom(cls, force=False):
        assert force is True
        raise RuntimeError("clickhouse down")

    calls = []
    try:
        _job(w, m, resync=boom, calls=calls)
    except RuntimeError as exc:
        assert "clickhouse down" in str(exc)
    else:
        raise AssertionError("the job swallowed the failure")
    assert calls == [] and len(w.logged) == 1
    assert w.events[-1] == "unlock"        # the lock is released on failure


def test_rf7_two_jobs_serialise_on_the_lock():
    w = _World()
    m = _k8(w)
    release_a = threading.Event()
    entered = []

    def resync(cls, force=False):
        me = threading.current_thread().name
        entered.append(("in", me))
        if me == "A":
            release_a.wait(5)
        entered.append(("out", me))
        return 1

    m.CostAllocationRun._write_coefficients = classmethod(resync)
    saved = sys.modules.get("konsol.tasks")
    sys.modules["konsol.tasks"] = _job_tasks_stub([])
    try:
        a = threading.Thread(target=m.resync_coefficients_job, kwargs={"run": "CAR-1"}, name="A",
                             daemon=True)
        b = threading.Thread(target=m.resync_coefficients_job, kwargs={"run": "CAR-2"}, name="B",
                             daemon=True)
        a.start()
        deadline = time.monotonic() + 5
        while ("in", "A") not in entered:
            assert time.monotonic() < deadline, "job A never reached the resync"
            time.sleep(0.005)
        b.start()
        time.sleep(0.1)
        held = ("in", "B") not in entered   # B waits on the lock while A holds it
        release_a.set()
        a.join(5)
        b.join(5)
    finally:
        if saved is None:
            sys.modules.pop("konsol.tasks", None)
        else:
            sys.modules["konsol.tasks"] = saved
    assert held
    assert entered == [("in", "A"), ("out", "A"), ("in", "B"), ("out", "B")]


def test_rf7_job_without_a_run_names_the_latest_submitted_or_cancelled_run():
    w = _World()
    m = _k8(w)
    seen = {}

    def get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, **k):
        seen.update(doctype=doctype, filters=filters, order_by=order_by, limit=k.get("limit"))
        return ["CAR-00007"]

    m.frappe.get_all = get_all
    calls = _job(w, m, run=None)
    assert calls == [("Cost Allocation Run", "CAR-00007", "coefficients_resync")]
    assert seen["filters"] == {"docstatus": ["in", [1, 2]]}
    assert seen["order_by"] == "modified desc" and seen["limit"] == 1


# --- RF9: one locked writer, guarded callback, timeouts, hourly safety net ------

def _stub_writer(w, m, rows=7):
    def write(cls, force=False):
        w.events.append(("resync", force))
        return rows

    m.CostAllocationRun._write_coefficients = classmethod(write)


def _run_job(w, m, calls, **kwargs):
    tasks = _job_tasks_stub(calls)
    saved = sys.modules.get("konsol.tasks")
    sys.modules["konsol.tasks"] = tasks
    try:
        return _with_rq(w, lambda: m.resync_coefficients_job(**kwargs))
    finally:
        if saved is None:
            sys.modules.pop("konsol.tasks", None)
        else:
            sys.modules["konsol.tasks"] = saved


def _log_lines(w):
    return [e[1] for e in w.events if isinstance(e, tuple) and e[0] == "log"]


def test_rf9_reconcile_entry_never_writes_and_enqueues_the_locked_job():
    """R2: reconcile_all calls resync_staging inside after_migrate, whose
    transaction still holds uncommitted work (roles, seeds, custom fields), so
    it may neither roll back nor wait on the lock. It enqueues the job."""
    w = _World()
    m = _k8(w)
    result = _with_rq(w, lambda: m.CostAllocationRun.resync_staging(force=True))
    assert w.sync_table_calls == [] and "rollback" not in w.events
    (args, kw), = w.enqueued
    assert args == (JOB,) and kw["run"] is None and kw["request_build"] is False
    assert result == "queued"
    # reconcile's job does not stand in for a submit's: no marker, so a later
    # commit enqueues its own job, which requests the build
    assert w.redis == {}


def test_rf9_reconcile_reuses_a_queued_job():
    w = _World()
    m = _k8(w)
    _commit(w, m, "CAR-00001")
    assert _with_rq(w, lambda: m.CostAllocationRun.resync_staging(force=True)) == "queued"
    assert len(w.enqueued) == 1


def test_rf9_reconcile_and_the_job_take_the_same_lock():
    w = _World()
    m = _k8(w)
    _stub_writer(w, m)
    _commit(w, m, "CAR-00001")
    w.jobs[w.enqueued[0][1]["job_id"]] = "started"   # else reconcile reuses it
    _with_rq(w, lambda: m.CostAllocationRun.resync_staging(force=True))
    w.jobs.clear()
    for _args, kw in list(w.enqueued):
        job_kwargs = {k: v for k, v in kw.items() if k not in ("queue", "job_id", "timeout")}
        _run_job(w, m, [], **job_kwargs)
    names = {name for name, _t, _b in w.lock_requests}
    assert len(w.lock_requests) == 2 and len(names) == 1
    assert w.events.count(("resync", True)) == 2


def test_rf9_reconcile_job_requests_no_build():
    w = _World()
    m = _k8(w)
    _stub_writer(w, m)
    calls = []
    _run_job(w, m, calls, run=None, request_build=False)
    assert calls == [] and ("resync", True) in w.events


def test_rf9_the_writer_is_called_only_under_the_lock():
    """No caller may write cost_allocation_coefficients without the lock:
    sync_table is called by _write_coefficients alone, and _write_coefficients
    by _locked_resync alone, after the lock and the rollback."""
    with open(RUN_PY) as f:
        tree = ast.parse(f.read())
    callers = {"sync_table": set(), "_write_coefficients": set()}

    def walk(node, owner):
        for child in ast.iter_child_nodes(node):
            name = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = child.name
            if isinstance(child, ast.Call):
                called = getattr(child.func, "attr", None) or getattr(child.func, "id", None)
                if called in callers:
                    callers[called].add(owner)
            walk(child, name)

    walk(tree, "<module>")
    assert callers["sync_table"] == {"_write_coefficients"}, callers
    assert callers["_write_coefficients"] == {"_locked_resync"}, callers
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_locked_resync")
    src = ast.unparse(fn)
    assert src.index(".acquire(") < src.index("rollback(") < src.index("_write_coefficients(")


def test_rf9_the_callback_with_redis_down_logs_and_does_not_raise():
    for broken in ("enqueue", "cache"):
        w = _World()
        m = _k8(w)

        def down(*a, **k):
            raise ConnectionError("redis down")

        if broken == "enqueue":
            m.frappe.enqueue = down
        else:
            m.frappe.cache = types.SimpleNamespace(get=down, set=down, lock=down)
        assert _with_rq(w, lambda: m._queue_coefficient_resync("CAR-00042")) is None, broken
        (args, kw), = w.logged
        text = " ".join(str(x) for x in list(args) + list(kw.values()))
        assert "CAR-00042" in text, broken
        # after the commit an inserted Error Log would never be committed
        assert kw.get("defer_insert") is True, broken
        assert any(e[0] == "error" and "CAR-00042" in e[1]
                   for e in w.events if isinstance(e, tuple)), broken


def test_rf9_a_commit_survives_a_failing_callback():
    w = _World()
    m = _k8(w)
    m.frappe.enqueue = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("redis down"))
    _commit(w, m, "CAR-00001")              # raises nothing out of the commit
    assert len(w.logged) == 1


def test_rf9_timeout_inequalities():
    """R4: a waiter that waited its full RESYNC_WAIT_SECONDS still has
    RESYNC_RUN_SECONDS to run before RQ kills it; the lock outlives any live
    holder (RQ kills a job at RESYNC_JOB_TIMEOUT after it started, and a
    job holds the lock only after it started). A waiter cannot also outwait a
    dead holder's lock (that would need WAIT >= LOCK >= JOB >= WAIT + RUN), so
    a waiter that times out re-enqueues instead: the chain of waiters always
    outlasts the dead holder's lock."""
    m = _k8(_World())
    assert m.RESYNC_RUN_SECONDS > 0
    assert m.RESYNC_JOB_TIMEOUT >= m.RESYNC_WAIT_SECONDS + m.RESYNC_RUN_SECONDS
    assert m.RESYNC_LOCK_SECONDS >= m.RESYNC_JOB_TIMEOUT


def test_rf9_the_enqueue_and_the_lock_use_the_declared_timeouts():
    w = _World()
    m = _k8(w)
    _stub_writer(w, m)
    _commit(w, m, "CAR-00001")
    (_args, kw), = w.enqueued
    assert kw["timeout"] == m.RESYNC_JOB_TIMEOUT
    _run_job(w, m, [], run="CAR-00001")
    (_name, ttl, wait), = w.lock_requests
    assert ttl == m.RESYNC_LOCK_SECONDS and wait == m.RESYNC_WAIT_SECONDS


def test_rf9_a_lock_timeout_reenqueues_and_reads_nothing():
    w = _World()
    m = _k8(w)
    _stub_writer(w, m)
    _commit(w, m, "CAR-00005")
    first = w.enqueued[0][1]["job_id"]
    w.jobs[first] = "started"               # this job is the one now running
    w.lock_never_free = True
    calls = []
    _run_job(w, m, calls, run="CAR-00005")  # does not raise
    assert len(w.enqueued) == 2
    second = w.enqueued[1][1]
    assert second["job_id"] != first and second["run"] == "CAR-00005"
    assert second.get("request_build", True) is True
    assert w.redis[m._queued_marker_key()] == second["job_id"]
    assert calls == [] and "rollback" not in w.events and ("resync", True) not in w.events
    (args, kw), = w.logged
    assert "CAR-00005" in " ".join(str(x) for x in list(args) + list(kw.values()))


def test_rf9_a_job_without_any_run_requests_no_build_and_logs_one_line():
    """R6: run=None (reconcile, by hand, or the safety net with only extra
    rows) and no submitted or cancelled run: no build request."""
    w = _World()
    m = _k8(w)
    _stub_writer(w, m, rows=0)
    m.frappe.get_all = lambda *a, **k: []
    calls = []
    _run_job(w, m, calls, run=None)
    assert calls == []
    (line,) = _log_lines(w)
    assert "no build" in line.lower()


# the hourly safety net (R1b-d)

def _staging(m, runs):
    m.clickhouse.execute = lambda sql, *a, **k: "\n".join(runs)


def _submitted(m, names):
    def get_all(doctype, filters=None, pluck=None, **k):
        assert doctype == "Cost Allocation Run" and filters == {"docstatus": 1}
        return list(names)

    m.frappe.get_all = get_all


def test_rf9_safety_net_does_nothing_on_a_match():
    w = _World()
    m = _k8(w)
    _submitted(m, ["CAR-00001", "CAR-00002"])
    _staging(m, ["CAR-00002", "CAR-00001"])
    _with_rq(w, m.check_coefficients_staging)
    assert w.enqueued == [] and w.logged == [] and w.redis == {} and w.sync_table_calls == []


def test_rf9_safety_net_enqueues_and_logs_a_missing_run():
    w = _World()
    m = _k8(w)
    _submitted(m, ["CAR-00001", "CAR-00003", "CAR-00002"])
    _staging(m, ["CAR-00001"])
    _with_rq(w, m.check_coefficients_staging)
    (args, kw), = w.enqueued
    assert args == (JOB,) and kw["run"] == "CAR-00002" and kw.get("request_build", True)
    (largs, lkw), = w.logged
    text = " ".join(str(x) for x in list(largs) + list(lkw.values()))
    assert "CAR-00002" in text and "CAR-00003" in text
    assert "\n" not in lkw.get("message", text)


def test_rf9_safety_net_enqueues_for_an_extra_cancelled_run():
    w = _World()
    m = _k8(w)
    _submitted(m, ["CAR-00001"])
    _staging(m, ["CAR-00001", "CAR-00004"])
    _with_rq(w, m.check_coefficients_staging)
    (args, kw), = w.enqueued
    assert args == (JOB,) and kw["run"] is None
    (largs, lkw), = w.logged
    assert "CAR-00004" in " ".join(str(x) for x in list(largs) + list(lkw.values()))


def test_rf9_safety_net_waits_for_a_resync_already_in_flight():
    """A commit whose job is queued or running shows as a mismatch until the
    job writes; that is not a loss. The next hour checks again."""
    for status in ("queued", "started"):
        w = _World()
        m = _k8(w)
        _commit(w, m, "CAR-00002")
        w.jobs[w.enqueued[0][1]["job_id"]] = status
        _submitted(m, ["CAR-00001", "CAR-00002"])
        _staging(m, ["CAR-00001"])
        _with_rq(w, m.check_coefficients_staging)
        assert len(w.enqueued) == 1 and w.logged == [], status


def test_rf9_safety_net_is_scheduled_hourly():
    with open(os.path.join(os.path.dirname(RUN_PY), "..", "..", "..", "hooks.py")) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "scheduler_events" for t in node.targets):
            events = ast.literal_eval(node.value)
            assert ("konsol.epm.doctype.cost_allocation_run.cost_allocation_run"
                    ".check_coefficients_staging") in events.get("hourly", [])
            return
    raise AssertionError("scheduler_events not found")
