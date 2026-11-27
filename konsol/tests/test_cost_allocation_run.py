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
# konsol#269 phase 2b-1 (task C): the Account Set driver's accounts, snapshotted on the run
DRIVER_ACCOUNT = "cost_allocation_run_driver_account"
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
    ("coefficient_row_count", "Int", 0, 1),
    ("values_carried_forward", "Check", 0, 1),
    ("computed_at", "Datetime", 0, 1),
    ("computed_by", "Link", 0, 1),
    ("computed_for_rule_set", "Data", 0, 1),
    ("computed_for_scenario", "Data", 0, 1),
    ("computed_for_fiscal_year", "Int", 0, 1),
    ("computed_for_fiscal_period", "Int", 0, 1),
    ("weights", "Table", 0, 1),
    ("accounts", "Table", 0, 1),
    ("driver_accounts", "Table", 0, 1),
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
    # phase 2b-1 (task C): where the value came from, and the fixed-weight window used
    ("source", "Data"),
    ("effective_from", "Date"),
    ("weight", "Float"),
]

ACCOUNT_FIELDS = [
    ("main_account", "Link"),
    ("declared_account", "Link"),
]

DRIVER_ACCOUNT_FIELDS = [
    ("driver", "Link"),
    ("main_account", "Link"),
    ("value_sign", "Data"),
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
    for folder in (RUN, WEIGHT, ACCOUNT, DRIVER_ACCOUNT):
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
    assert sections == ["Scope", "Computation", "Computed", "Weights", "Accounts", "Driver Accounts"]
    # phase 2b-1 (task C): the Driver Accounts section sits on the Accounts tab,
    # after the receiver account table
    names = [f["fieldname"] for f in doc["fields"]]
    assert names[names.index("accounts") + 1:names.index("accounts") + 3] == [
        "driver_accounts_section", "driver_accounts"]
    assert "Tab Break" not in [f["fieldtype"] for f in doc["fields"][names.index("tab_accounts") + 1:]]


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
    assert fields["driver_accounts"]["options"] == "Cost Allocation Run Driver Account"
    assert fields["driver_accounts"]["label"] == "Driver Accounts"
    assert fields["driver_accounts"].get("description")
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
    names = {WEIGHT: "Cost Allocation Run Weight", ACCOUNT: "Cost Allocation Run Account",
             DRIVER_ACCOUNT: "Cost Allocation Run Driver Account"}
    for folder, expected in ((WEIGHT, WEIGHT_FIELDS), (ACCOUNT, ACCOUNT_FIELDS),
                             (DRIVER_ACCOUNT, DRIVER_ACCOUNT_FIELDS)):
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
    # phase 2b-1 (task C): source and the window are read-only, labelled and explained
    assert fields["source"]["label"] == "Source"
    assert fields["source"]["description"] == (
        "Where the driver value came from: Uploaded Statistic, Fixed Weights or Account Set.")
    assert fields["effective_from"]["label"] == "Effective From"
    assert fields["effective_from"]["description"] == "For Fixed Weights: the start of the window used."


def test_doctype_driver_account_child_links():
    fields = _by_name(DRIVER_ACCOUNT)
    assert fields["driver"]["options"] == "Cost Allocation Driver"
    assert fields["main_account"]["options"] == "Main Account"
    assert fields["value_sign"]["label"] == "Value Sign"


def test_doctype_account_child_links():
    fields = _by_name(ACCOUNT)
    assert fields["main_account"]["options"] == "Main Account"
    assert fields["declared_account"]["options"] == "Main Account"


def test_doctype_child_controllers_define_their_classes():
    for folder, cls in ((RUN, "CostAllocationRun"), (WEIGHT, "CostAllocationRunWeight"),
                        (ACCOUNT, "CostAllocationRunAccount"),
                        (DRIVER_ACCOUNT, "CostAllocationRunDriverAccount")):
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
from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction

RUN_PY = os.path.join(EPM_DOCTYPES, RUN, "cost_allocation_run.py")
RUN_JS = os.path.join(EPM_DOCTYPES, RUN, "cost_allocation_run.js")


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
        self.driver_accounts = [_Row(driver="OLD")]
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
    # konsol#529: before_cancel sets the workflow's docstatus-2 state through
    # this helper; it has its own tests (test_cancelled_workflow_state.py).
    wfs = types.ModuleType("konsol.workflows")
    wfs.set_cancelled_state = lambda doc: None
    stubs = {"konsol.clickhouse": ch, "konsol.period_status": ps, "konsol.workflows": wfs}
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
    # 2b-1 F7: every driver is an Uploaded Statistic unless a test says otherwise
    module._driver_sources = lambda names: {n: _Row(_driver(n)) for n in names}
    module._period_dates = lambda fy, fp: MARCH
    module._query_derived = lambda sql, params: ""
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


def _matches(row, filters):
    """A get_all filter dict as the database applies it: equality, or ["in", [...]]."""
    for key, want in (filters or {}).items():
        if isinstance(want, (list, tuple)) and want and want[0] == "in":
            if row.get(key) not in want[1]:
                return False
        elif row.get(key) != want:
            return False
    return True


class _FakeClickHouse:
    """clickhouse.execute over an in-memory coefficients table: records every
    statement with its params, holds rows per run, and answers the counts."""

    def __init__(self):
        self.calls = []
        self.rows = []            # dicts, as inserted
        self.down = False
        self.fail_on = None       # a statement prefix that raises

    def held(self):
        out = {}
        for r in self.rows:
            out[r["run"]] = out.get(r["run"], 0) + 1
        return out

    def execute(self, sql, params=None, timeout=None):
        self.calls.append((sql, dict(params or {})))
        head = sql.lstrip().split("\n", 1)[0].upper()
        if self.down or (self.fail_on and head.startswith(self.fail_on)):
            raise ConnectionError("clickhouse down")
        if head.startswith("DELETE FROM"):
            self.rows = [r for r in self.rows if r["run"] != params["param_run"]]
        elif head.startswith("INSERT INTO"):
            self.rows.extend(json.loads(line) for line in sql.split("\n")[1:] if line.strip())
        elif head.startswith("SELECT RUN, COUNT()"):
            return "\n".join(f"{r}\t{n}" for r, n in sorted(self.held().items()))
        elif head.startswith("SELECT COUNT()"):
            return str(len(self.rows))
        return ""


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
        # 2b-1 F8: the drivers as the site holds them now, with their fixed
        # weights; the derived query's answer (text, or an exception to raise)
        self.drivers = {"ZZ Head": {"name": "ZZ Head", "source": "Uploaded Statistic",
                                    "dimension": "dim_zz", "value_sign": None}}
        self.fixed_rows = []
        self.derived = ""
        self.derived_calls = []
        self.period_dates_calls = []
        self.sql = []
        self.open_calls = []
        self.closed = set()
        self.declared_calls = []
        self.undeclared = set()
        self.syncs = []
        self.once = []
        self.enqueued = []
        self.events = []
        self.commit_callbacks = []
        self.logged = []
        self.sync_table_calls = []
        self.get_all_data = {}
        self.ch = _FakeClickHouse()
        self.stamps = []
        self.set_values = []
        self.commits = 0


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
        rows = [_Row(r) for r in world.get_all_data.get(doctype, []) if _matches(r, filters)]
        return [r[pluck] for r in rows] if pluck else rows

    def sql(query, values=None, as_dict=False, **k):
        world.sql.append((query, values))
        if "`tabCost Allocation Statistic Value`" in query:
            # the statistic re-read: only the cited statistics, as the IN clause says
            assert "IN %(statistics)s" in query
            return [_Row(r) for r in world.stat_rows if r["statistic"] in values["statistics"]]
        if "`tabCost Allocation Driver Fixed Weight`" in query:
            assert "IN %(drivers)s" in query
            return [_Row(r) for r in world.fixed_rows if r["parent"] in values["drivers"]]
        if "`tabCost Allocation Driver`" in query:
            assert "IN %(drivers)s" in query
            return [_Row(r) for n, r in world.drivers.items() if n in values["drivers"]]
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
    def set_value(doctype, name, field, value, update_modified=True):
        world.set_values.append((doctype, name, field, value, update_modified))
        for r in world.get_all_data.get(doctype, []):
            if r["name"] == name:
                r[field] = value

    def commit():
        world.commits += 1

    fr.db = types.SimpleNamespace(get_value=get_value, sql=sql, set_value=set_value,
                                  commit=commit,
                                  rollback=lambda: world.events.append("rollback"))
    fr.get_all = get_all
    def enqueue(*a, **k):
        world.enqueued.append((a, k))

    fr.enqueue = enqueue
    fr.local = types.SimpleNamespace(site="zz.local")
    fr.db.after_commit = types.SimpleNamespace(add=world.commit_callbacks.append)
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

    def period_dates(year, period):
        world.period_dates_calls.append((year, period))
        return MARCH

    ps.assert_open = assert_open
    ps.assert_declared = assert_declared
    ps.period_dates = period_dates
    module.period_status = ps

    def query_derived(sql, params):
        world.derived_calls.append((sql, params))
        if isinstance(world.derived, Exception):
            raise world.derived
        return world.derived

    module._query_derived = query_derived
    module._descendants = lambda account: list(_TREE.get(account, [account]))
    ch = types.SimpleNamespace()
    ch.sync_doctype_after_commit = lambda *a, **k: world.syncs.append(a)
    def after_commit_once(key, fn):
        # as clickhouse.after_commit_once: one callback per key per transaction
        if all(k != key for k, _fn in world.once):
            world.once.append((key, fn))

    ch.after_commit_once = after_commit_once
    ch.sync_table = lambda table, columns, rows, **k: world.sync_table_calls.append(
        (table, columns, rows, k)) or len(rows)
    ch.execute = world.ch.execute
    ch._stamp_watermark = lambda table, row_count, source_max_modified=None: world.stamps.append(
        (table, row_count, source_max_modified))
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
        w.ch.calls.clear()
        # a submitted run cannot be deleted: after_delete sees a cancelled one
        getattr(_k8_run(m, docstatus=2 if hook == "after_delete" else 1), hook)()
        assert [(s[0], s[1]) for s in w.syncs] == [
            ("Cost Allocation Run", "epm_staging.cost_allocation_runs"),
            ("Cost Allocation Run Account", "epm_staging.cost_allocation_run_accounts"),
            ("Cost Allocation Run Weight", "epm_staging.cost_allocation_run_weights"),
            ("Cost Allocation Run Driver Account",
             "epm_staging.cost_allocation_run_driver_accounts")], hook
        # RF10: the coefficients are one guarded after-commit callback per run
        ((key, _fn),) = w.once
        assert key == ("cost_allocation_coefficients", "CAR-00001"), hook
        # nothing touches ClickHouse, the queue or the commit list in the hook
        assert w.sync_table_calls == [] and w.ch.calls == [], hook
        assert w.enqueued == [] and w.commit_callbacks == [], hook


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
        "statistic", "value_period", "carried_forward", "driver_value",
        "source", "effective_from"]
    assert wt.CH_FIELD_MAP["source"] == "source"
    assert wt.CH_FIELD_MAP["effective_from"] == "effective_from"
    da = cls.CHILD_CONTROLLERS["Cost Allocation Run Driver Account"]
    assert da.CH_TABLE == "epm_staging.cost_allocation_run_driver_accounts"
    assert da.CH_FIELD_MAP == {"parent": "parent", "idx": "idx", "driver": "driver",
                               "main_account": "main_account", "value_sign": "value_sign"}
    assert acc.CH_SYNC_FILTERS == wt.CH_SYNC_FILTERS == da.CH_SYNC_FILTERS == {"docstatus": 1}


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
        {"name": "CAR-00001", "method": "Step-Down", "docstatus": 1,
         "modified": datetime(2026, 11, 13, 9, 0, 0)}]
    w.get_all_data["Cost Allocation Run Weight"] = _stored_weights()
    m = _k8(w)
    written = m.CostAllocationRun.resync_staging(force=True)
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
    m.CostAllocationRun.resync_staging()
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
    m.CostAllocationRun.resync_staging()
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
    for name in ("weights", "accounts", "driver_accounts", "computed_at", "computed_by",
                 "rule_set_modified", "values_carried_forward", "computed_for_rule_set", "computed_for_scenario",
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
             "rule_set_modified", "values_carried_forward", "coefficient_row_count")


def test_kf3_amended_insert_clears_the_computed_snapshot():
    # Frappe v16's client copy_doc ignores no_copy when amending, so the server clears.
    m = _k8(_World())
    run = _k8_run(m, amended_from="CAR-00001", accounts=[_Row(main_account="6000")],
                  driver_accounts=[_Row(driver="ZZ Head", main_account="4000", value_sign="Reversed")],
                  computed_at=datetime(2026, 11, 12), computed_by="zz@example.com",
                  values_carried_forward=1, coefficient_row_count=7)
    run.set = lambda field, rows: run.__dict__.__setitem__(field, list(rows))
    run.before_insert()
    assert run.weights == [] and run.accounts == [] and run.driver_accounts == []
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
        {"name": "CAR-00001", "method": "Reciprocal", "docstatus": 1,
         "modified": datetime(2026, 11, 13, 9, 0, 0)}]
    w.get_all_data["Cost Allocation Run Weight"] = _recip_stored()
    m = _k8(w)
    m.CostAllocationRun.resync_staging(force=True)
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


# --- RF4: Compute reports an unsolvable loop; no doc-event build trigger -----------

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


# --- RF10: per-run coefficient writes after commit; synchronous reconcile ------
# Deepak Pai, 17 Nov 2026 (konsol PR #541): option B. Rejected: A, keeping the
# background resync job (its marker, lock, timeouts and re-enqueue loop).

STAGING = "epm_staging.cost_allocation_coefficients"
BUILD_JOB = "konsol.tasks.request_consolidation_build"
MODIFIED = datetime(2026, 11, 13, 9, 0, 0)


def _site(w, runs):
    """``runs``: {name: docstatus}, as committed. Every run stores the
    3-sender step-down weights of _stored_weights()."""
    w.get_all_data["Cost Allocation Run"] = [
        {"name": n, "method": "Step-Down", "docstatus": d, "modified": MODIFIED}
        for n, d in runs.items()]
    w.get_all_data["Cost Allocation Run Weight"] = [
        {**r, "parent": n} for n in runs for r in _stored_weights()]


def _rows_for(name):
    """The run's coefficient rows as the model builds them, JSON-shaped."""
    return [{c: (str(v) if isinstance(v, Decimal) else v) for c, v in {**r, "run": name}.items()}
            for r in _expected_rows()]


def _commit(w, m, name, hook, committed, docstatus=None):
    """The hook runs inside the transaction. Then the commit lands the run's
    new state (``committed``: a docstatus, or None for a delete) and the
    after-commit callbacks fire, as Frappe's commit runs them. ``docstatus``
    is the hooked document's own; a deleted run defaults to cancelled (2),
    since a submitted run cannot be deleted."""
    w.once.clear()
    if docstatus is None:
        docstatus = 2 if hook == "after_delete" else 1
    getattr(_k8_run(m, name=name, docstatus=docstatus), hook)()
    runs = [r for r in w.get_all_data.get("Cost Allocation Run", []) if r["name"] != name]
    if committed is not None:
        runs.append({"name": name, "method": "Step-Down", "docstatus": committed,
                     "modified": MODIFIED})
        if not any(r["parent"] == name for r in w.get_all_data.get("Cost Allocation Run Weight", [])):
            w.get_all_data.setdefault("Cost Allocation Run Weight", []).extend(
                {**r, "parent": name} for r in _stored_weights())
    w.get_all_data["Cost Allocation Run"] = runs
    for _key, fn in list(w.once):
        fn()


def _statements(w):
    return [sql.lstrip().split("\n", 1)[0].split(" ")[0].upper() for sql, _p in w.ch.calls]


def _logged_text(w):
    return " ".join(str(x) for a, k in w.logged for x in list(a) + list(k.values()))


def test_rf10_submit_writes_exactly_that_runs_rows_delete_then_insert():
    w = _World()
    _site(w, {"CAR-00002": 1})
    w.ch.rows = _rows_for("CAR-00002") + [{**_rows_for("CAR-00001")[0], "coefficient": "9"}]
    m = _k8(w)
    _commit(w, m, "CAR-00001", "on_submit", committed=1)
    assert _statements(w)[:2] == ["DELETE", "INSERT"]
    (delete, params), (insert, _ip) = w.ch.calls[:2]
    assert delete.startswith(f"DELETE FROM {STAGING} WHERE run = {{run:String}}")
    assert params["param_run"] == "CAR-00001"
    # the run name is a parameter, never formatted into the SQL
    assert "CAR-00001" not in delete and "CAR-00001" not in insert.split("\n", 1)[0]
    assert sorted(r["run"] for r in w.ch.rows if r["run"] == "CAR-00001") == \
        ["CAR-00001"] * len(_expected_rows())
    assert [r for r in w.ch.rows if r["run"] == "CAR-00001"] == _rows_for("CAR-00001")
    assert [r for r in w.ch.rows if r["run"] == "CAR-00002"] == _rows_for("CAR-00002")


def test_rf10_the_delete_is_synchronous():
    w = _World()
    m = _k8(w)
    _commit(w, m, "CAR-00001", "on_cancel", committed=2)
    (delete, _p), = [c for c in w.ch.calls if c[0].startswith("DELETE")]
    assert "lightweight_deletes_sync = 2" in delete


def test_rf10_cancel_deletes_the_runs_rows_and_inserts_nothing():
    w = _World()
    _site(w, {"CAR-00002": 1})
    w.ch.rows = _rows_for("CAR-00001") + _rows_for("CAR-00002")
    m = _k8(w)
    _commit(w, m, "CAR-00001", "on_cancel", committed=2)
    assert "INSERT" not in _statements(w)
    assert {r["run"] for r in w.ch.rows} == {"CAR-00002"}
    assert w.ch.calls[0][1]["param_run"] == "CAR-00001"


def test_rf10_delete_deletes_the_runs_rows():
    w = _World()
    _site(w, {"CAR-00002": 1})
    w.ch.rows = _rows_for("CAR-00001") + _rows_for("CAR-00002")
    m = _k8(w)
    _commit(w, m, "CAR-00001", "after_delete", committed=None)
    assert "INSERT" not in _statements(w)
    assert {r["run"] for r in w.ch.rows} == {"CAR-00002"}


def test_rf10_the_write_is_after_commit_once_per_transaction_and_reads_committed_state():
    w = _World()
    m = _k8(w)
    reads = []
    orig = m.frappe.get_all
    m.frappe.get_all = lambda doctype, **k: reads.append(doctype) or orig(doctype, **k)
    run = _k8_run(m, docstatus=1)
    run.on_submit()
    run.on_submit()
    run.on_cancel()
    # inside the transaction: nothing read, nothing written
    assert reads == [] and w.ch.calls == []
    assert [k for k, _fn in w.once] == [("cost_allocation_coefficients", "CAR-00001")]
    # Frappe's commit: COMMIT, START TRANSACTION, then the callbacks, so the
    # callback's first read opens a snapshot holding this commit
    _site(w, {"CAR-00001": 1})
    w.once[0][1]()
    assert "Cost Allocation Run" in reads
    assert "rollback" not in w.events
    assert {r["run"] for r in w.ch.rows} == {"CAR-00001"}


def test_rf10_success_requests_the_build_naming_the_run_after_the_write():
    w = _World()
    m = _k8(w)
    at_enqueue = []
    m.frappe.enqueue = lambda *a, **k: (at_enqueue.append(_statements(w)), w.enqueued.append((a, k)))
    _commit(w, m, "CAR-00001", "on_submit", committed=1)
    (args, kw), = w.enqueued
    assert args == (BUILD_JOB,)
    assert kw["doctype"] == "Cost Allocation Run" and kw["name"] == "CAR-00001"
    assert kw["trigger_method"] == "coefficients_sync"
    # already after the commit: no enqueue_after_commit, and no dedupe that
    # would drop a request behind a started job
    assert "enqueue_after_commit" not in kw and "deduplicate" not in kw
    assert at_enqueue[0][:2] == ["DELETE", "INSERT"]


def test_rf10_cancel_and_delete_request_the_build_too():
    for hook, committed in (("on_cancel", 2), ("after_delete", None)):
        w = _World()
        m = _k8(w)
        _commit(w, m, "CAR-00001", hook, committed=committed)
        (args, kw), = w.enqueued
        assert args == (BUILD_JOB,) and kw["name"] == "CAR-00001", hook


def test_rf10_stamps_the_watermark_with_the_tables_row_count():
    w = _World()
    _site(w, {"CAR-00002": 1})
    w.ch.rows = _rows_for("CAR-00002")
    m = _k8(w)
    _commit(w, m, "CAR-00001", "on_submit", committed=1)
    assert w.stamps == [(STAGING, 2 * len(_expected_rows()), "2026-11-13 09:00:00")]


def test_rf10_a_failure_does_not_raise_logs_the_run_and_requests_no_build():
    for fail_on in ("DELETE", "INSERT", None):
        w = _World()
        m = _k8(w)
        w.ch.fail_on = fail_on
        w.ch.down = fail_on is None
        _commit(w, m, "CAR-00001", "on_submit", committed=1)   # raises nothing
        assert w.enqueued == [], fail_on
        assert w.stamps == [], fail_on
        (a, k), = w.logged
        assert k.get("defer_insert") is True, fail_on
        assert "CAR-00001" in _logged_text(w), fail_on
        assert any(e[0] == "error" and "CAR-00001" in e[1]
                   for e in w.events if isinstance(e, tuple)), fail_on


def test_rf10_a_failed_build_request_does_not_raise_and_is_logged():
    w = _World()
    m = _k8(w)

    def down(*a, **k):
        raise ConnectionError("redis down")

    m.frappe.enqueue = down
    _commit(w, m, "CAR-00001", "on_submit", committed=1)
    assert {r["run"] for r in w.ch.rows} == {"CAR-00001"}
    assert "CAR-00001" in _logged_text(w)


def test_rf10_reconcile_rebuilds_synchronously_with_every_submitted_run():
    w = _World()
    _site(w, {"CAR-00001": 1, "CAR-00002": 1, "CAR-00003": 2})
    m = _k8(w)
    written = m.CostAllocationRun.resync_staging(force=True)
    (table, columns, rows, kw), = w.sync_table_calls
    # sync_table is the writer that stamps the reconcile run id on the watermark
    assert table == STAGING and kw["force"] is True
    assert written == len(rows) == 2 * len(_expected_rows())
    assert {r[0] for r in rows} == {"CAR-00001", "CAR-00002"}
    # synchronous: no job, no callback, no build request
    assert w.enqueued == [] and w.commit_callbacks == [] and w.once == []


def test_rf10_reconcile_stamps_the_reconcile_run_id():
    """reconcile_all records resync_staging's row count, and its writer is
    sync_table, which stamps _reconcile_run_id: no 'queued' result is left."""
    with open(os.path.join(APP_DIR, "clickhouse.py")) as f:
        src = f.read()
    loop = src.split("def _reconcile_tables():")[1].split("\ndef ")[0]
    assert "cls.resync_staging(force=True)" in loop
    assert "queue" not in loop.lower()
    with open(RUN_PY) as f:
        tree = ast.parse(f.read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CostAllocationRun")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "resync_staging")
    calls = {ast.unparse(c.func) for c in ast.walk(fn) if isinstance(c, ast.Call)}
    assert "clickhouse.sync_table" in calls
    assert not any("enqueue" in c for c in calls)


# the hourly safety net (F4, F5)

def _staged(w, counts):
    """Put ``counts`` ({run: rows}) in staging."""
    w.ch.rows = [r for run, n in counts.items() for r in _rows_for(run)[:n]]


def test_rf10_safety_net_does_nothing_on_a_match():
    w = _World()
    _site(w, {"CAR-00001": 1, "CAR-00002": 1, "CAR-00003": 2})
    n = len(_expected_rows())
    _staged(w, {"CAR-00001": n, "CAR-00002": n})
    m = _k8(w)
    m.check_coefficients_staging()
    assert _statements(w) == ["SELECT"]
    assert "GROUP BY run" in w.ch.calls[0][0]
    assert w.enqueued == [] and w.logged == [] and w.stamps == [] and w.sync_table_calls == []


def test_rf10_safety_net_repairs_a_missing_run_and_requests_its_build():
    w = _World()
    _site(w, {"CAR-00001": 1, "CAR-00002": 1})
    _staged(w, {"CAR-00001": len(_expected_rows())})
    m = _k8(w)
    m.check_coefficients_staging()
    assert [r for r in w.ch.rows if r["run"] == "CAR-00002"] == _rows_for("CAR-00002")
    (args, kw), = w.enqueued
    assert args == (BUILD_JOB,) and kw["name"] == "CAR-00002"
    (a, k), = w.logged
    assert "CAR-00002" in _logged_text(w) and "CAR-00001" not in _logged_text(w)
    assert "\n" not in k.get("message", "")


def test_rf10_safety_net_deletes_an_extra_run_and_its_build_names_the_run():
    w = _World()
    _site(w, {"CAR-00001": 1, "CAR-00004": 2})
    n = len(_expected_rows())
    _staged(w, {"CAR-00001": n, "CAR-00004": n})
    m = _k8(w)
    m.check_coefficients_staging()
    assert {r["run"] for r in w.ch.rows} == {"CAR-00001"}
    assert "INSERT" not in _statements(w)
    (args, kw), = w.enqueued
    assert args == (BUILD_JOB,) and kw["name"] == "CAR-00004"     # F4
    assert "CAR-00004" in _logged_text(w)


def test_rf10_safety_net_repairs_a_run_with_the_wrong_row_count():
    w = _World()
    _site(w, {"CAR-00001": 1, "CAR-00002": 1})
    n = len(_expected_rows())
    _staged(w, {"CAR-00001": n, "CAR-00002": n - 1})                # F5
    m = _k8(w)
    m.check_coefficients_staging()
    assert [r for r in w.ch.rows if r["run"] == "CAR-00002"] == _rows_for("CAR-00002")
    (args, kw), = w.enqueued
    assert kw["name"] == "CAR-00002"
    assert len(w.logged) == 1 and "CAR-00002" in _logged_text(w)


def test_rf10_safety_net_with_clickhouse_down_only_warns():
    w = _World()
    _site(w, {"CAR-00001": 1})
    w.ch.down = True
    m = _k8(w)
    m.check_coefficients_staging()
    assert w.logged == [] and w.enqueued == [] and w.stamps == []
    assert any(e[0] == "warning" for e in w.events if isinstance(e, tuple))


def test_rf10_safety_net_is_scheduled_hourly():
    with open(os.path.join(APP_DIR, "hooks.py")) as f:
        tree = ast.parse(f.read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "scheduler_events" for t in node.targets):
            events = ast.literal_eval(node.value)
            assert ("konsol.epm.doctype.cost_allocation_run.cost_allocation_run"
                    ".check_coefficients_staging") in events.get("hourly", [])
            return
    raise AssertionError("scheduler_events not found")


REMOVED = ("resync_coefficients_job", "_locked_resync", "_queue_coefficient_resync",
           "_queued_marker_key", "_resync_lock_key", "_marker_status", "_job_status",
           "_latest_run", "_write_coefficients", "_NO_LOCK", "RESYNC_JOB",
           "RESYNC_WAIT_SECONDS", "RESYNC_RUN_SECONDS", "RESYNC_JOB_TIMEOUT",
           "RESYNC_LOCK_SECONDS", "RESYNC_MARKER_SECONDS",
           "cost_allocation_coefficients_queued", "cost_allocation_coefficients_resync")


def test_rf10_the_job_marker_and_lock_are_gone_and_nothing_references_them():
    m = _k8(_World())
    for name in REMOVED:
        assert not hasattr(m, name), name
        assert not hasattr(m.CostAllocationRun, name), name
    this = os.path.abspath(__file__)
    found = []
    for root, _dirs, files in os.walk(APP_DIR):
        for f in files:
            path = os.path.join(root, f)
            if not f.endswith((".py", ".json", ".js", ".txt")) or os.path.abspath(path) == this:
                continue
            with open(path, encoding="utf-8", errors="ignore") as fh:
                text = fh.read()
            for name in REMOVED:
                if re.search(rf"\b{re.escape(name)}\b", text):
                    found.append((os.path.relpath(path, APP_DIR), name))
    assert found == []


# --- RF11: the hourly check is SQL-only and re-checks before it repairs; a
# successful write always requests its build (PR #541, RF10 re-review F1-F5).

def _site_counted(w, runs, count=None):
    """As _site, with each run's stored coefficient_row_count. ``runs``:
    {name: docstatus}; ``count``: the stored count (default: the model's)."""
    _site(w, runs)
    n = len(_expected_rows()) if count is None else count
    for r in w.get_all_data["Cost Allocation Run"]:
        r["coefficient_row_count"] = n if r["docstatus"] == 1 else 0


def _count_model_calls(m):
    calls = []
    orig = m._coefficient_rows

    def spy(run_name, method, stored):
        calls.append(run_name)
        return orig(run_name, method, stored)

    m._coefficient_rows = spy
    return calls


def test_rf11_doctype_coefficient_row_count_is_a_read_only_no_copy_computation_field():
    doc = _doc(RUN)
    fields = _by_name(RUN)
    f = fields["coefficient_row_count"]
    assert f["fieldtype"] == "Int" and f["label"] == "Coefficient Rows"
    assert f.get("read_only") == 1 and f.get("no_copy") == 1
    assert f["description"] == ("Set on approval: the number of coefficient rows this run "
                                "writes to the warehouse.")
    names = [x["fieldname"] for x in doc["fields"]]
    at = names.index("coefficient_row_count")
    assert names.index("computation_section") < at < names.index("computed_section")


def test_rf11_before_submit_stores_the_coefficient_row_count():
    w = _World()
    m = _k8(w)
    run = _k8_run(m)
    run.before_submit()
    built = m._coefficient_rows("CAR-00001", "Step-Down", m._rows(run.weights))
    assert built and run.coefficient_row_count == len(built)


def test_rf11_check_uses_the_stored_count_without_calling_the_model():
    w = _World()
    _site_counted(w, {"CAR-00001": 1, "CAR-00002": 1, "CAR-00003": 2})
    n = len(_expected_rows())
    _staged(w, {"CAR-00001": n, "CAR-00002": n})
    m = _k8(w)
    model_calls = _count_model_calls(m)
    reads = []
    orig = m.frappe.get_all
    m.frappe.get_all = lambda doctype, **k: reads.append((doctype, k)) or orig(doctype, **k)
    m.check_coefficients_staging()
    assert model_calls == []
    (doctype, k), = reads
    assert doctype == "Cost Allocation Run" and k["filters"] == {"docstatus": 1}
    assert "coefficient_row_count" in k["fields"]
    assert _statements(w) == ["SELECT"]
    assert w.logged == [] and w.enqueued == [] and "rollback" not in w.events


def test_rf11_a_run_without_a_stored_count_falls_back_to_the_model():
    for stored in (None, 0):
        w = _World()
        _site_counted(w, {"CAR-00001": 1, "CAR-00002": 1})
        w.get_all_data["Cost Allocation Run"][0]["coefficient_row_count"] = stored
        n = len(_expected_rows())
        _staged(w, {"CAR-00001": n, "CAR-00002": n})
        m = _k8(w)
        model_calls = _count_model_calls(m)
        m.check_coefficients_staging()
        assert model_calls == ["CAR-00001"], stored
        assert _statements(w) == ["SELECT"], stored
        assert w.logged == [] and w.enqueued == [], stored


def test_rf11_a_model_rejected_run_is_logged_and_the_check_continues():
    w = _World()
    _site_counted(w, {"CAR-00001": 1, "CAR-00002": 1})
    w.get_all_data["Cost Allocation Run"][0]["coefficient_row_count"] = None
    _staged(w, {})
    m = _k8(w)
    orig = m._coefficient_rows

    def rows(run_name, method, stored):
        if run_name == "CAR-00001":
            raise m.model.CostAllocationError("ZZ every driver value is zero")
        return orig(run_name, method, stored)

    m._coefficient_rows = rows
    m.check_coefficients_staging()                       # raises nothing
    # the other run was still repaired
    assert {r["run"] for r in w.ch.rows} == {"CAR-00002"}
    (args, kw), = w.enqueued
    assert kw["name"] == "CAR-00002"
    (a, k), = w.logged
    text = _logged_text(w)
    assert "not repaired: CAR-00001: " in text and "ZZ every driver value is zero" in text


def test_rf11_the_world_changes_mid_check_and_nothing_is_repaired_from_the_stale_read():
    """The first read is a REPEATABLE READ snapshot. Between it and the repair,
    CAR-00002 is approved (its callback writes its rows) and CAR-00003 is
    cancelled (its callback deletes them). Neither may be touched; CAR-00004,
    still missing after the re-check, is repaired."""
    w = _World()
    n = len(_expected_rows())
    _site_counted(w, {"CAR-00001": 1, "CAR-00002": 0, "CAR-00003": 1, "CAR-00004": 1})
    _staged(w, {"CAR-00001": n, "CAR-00002": n})
    m = _k8(w)

    def rollback():
        w.events.append("rollback")
        for r in w.get_all_data["Cost Allocation Run"]:
            if r["name"] == "CAR-00002":
                r["docstatus"], r["coefficient_row_count"] = 1, n
            if r["name"] == "CAR-00003":
                r["docstatus"] = 2

    m.frappe.db.rollback = rollback
    before = [dict(r) for r in w.ch.rows if r["run"] in ("CAR-00002", "CAR-00003")]
    m.check_coefficients_staging()
    assert "rollback" in w.events
    touched = [p.get("param_run") for sql, p in w.ch.calls if sql.startswith("DELETE")]
    assert touched == ["CAR-00004"]
    assert [r for r in w.ch.rows if r["run"] in ("CAR-00002", "CAR-00003")] == before
    assert [r for r in w.ch.rows if r["run"] == "CAR-00004"] == _rows_for("CAR-00004")
    assert [kw["name"] for _a, kw in w.enqueued] == ["CAR-00004"]
    text = _logged_text(w)
    assert "CAR-00004" in text and "CAR-00002" not in text and "CAR-00003" not in text


def test_rf11_above_the_threshold_the_table_is_rebuilt_once():
    m0 = _k8(_World())
    assert m0.COEFFICIENT_REBUILD_THRESHOLD == 20
    w = _World()
    _site_counted(w, {f"CAR-{i:05d}": 1 for i in range(1, 22)})        # 21 runs
    _staged(w, {})
    m = _k8(w)
    m.check_coefficients_staging()
    (table, _cols, rows, kw), = w.sync_table_calls
    assert table == STAGING and kw["force"] is True
    assert len({r[0] for r in rows}) == 21
    assert "DELETE" not in _statements(w) and "INSERT" not in _statements(w)
    assert len(w.logged) == 1 and len(w.enqueued) == 1
    (args, _kw), = w.enqueued
    assert args == (BUILD_JOB,)


def test_rf11_at_the_threshold_runs_are_repaired_one_by_one():
    w = _World()
    _site_counted(w, {f"CAR-{i:05d}": 1 for i in range(1, 21)})        # 20 runs
    _staged(w, {})
    m = _k8(w)
    m.check_coefficients_staging()
    assert w.sync_table_calls == []
    assert len(w.enqueued) == 20 and len(w.logged) == 1


def test_rf11_a_failed_count_after_a_write_still_requests_the_build():
    w = _World()
    m = _k8(w)
    w.ch.fail_on = "SELECT COUNT()"
    _commit(w, m, "CAR-00001", "on_submit", committed=1)
    assert {r["run"] for r in w.ch.rows} == {"CAR-00001"}
    (args, kw), = w.enqueued
    assert args == (BUILD_JOB,) and kw["name"] == "CAR-00001"
    assert w.stamps == []
    assert any(e[0] == "warning" and "CAR-00001" in e[1]
               for e in w.events if isinstance(e, tuple))


def test_rf11_a_failed_watermark_stamp_after_a_write_still_requests_the_build():
    w = _World()
    m = _k8(w)

    def stamp(*a, **k):
        raise ConnectionError("clickhouse down")

    m.clickhouse._stamp_watermark = stamp
    _commit(w, m, "CAR-00001", "on_submit", committed=1)
    (args, kw), = w.enqueued
    assert kw["name"] == "CAR-00001"
    assert any(e[0] == "warning" for e in w.events if isinstance(e, tuple))


def test_rf11_a_failed_build_request_is_logged_with_the_call_to_run_by_hand():
    w = _World()
    m = _k8(w)

    def down(*a, **k):
        raise ConnectionError("redis down")

    m.frappe.enqueue = down
    _commit(w, m, "CAR-00001", "on_submit", committed=1)
    assert ('konsol.tasks.request_consolidation_build("Cost Allocation Run", "CAR-00001", '
            '"coefficients_sync")') in _logged_text(w)


def test_rf11_deleting_a_draft_does_nothing():
    w = _World()
    _site(w, {"CAR-00002": 1})
    w.ch.rows = _rows_for("CAR-00002")
    m = _k8(w)
    _commit(w, m, "CAR-00001", "after_delete", committed=None, docstatus=0)
    assert w.once == [] and w.syncs == []
    assert w.ch.calls == [] and w.enqueued == []


def test_rf11_deleting_a_cancelled_run_deletes_its_rows_and_requests_the_build():
    w = _World()
    _site(w, {"CAR-00002": 1})
    w.ch.rows = _rows_for("CAR-00001") + _rows_for("CAR-00002")
    m = _k8(w)
    _commit(w, m, "CAR-00001", "after_delete", committed=None, docstatus=2)
    assert {r["run"] for r in w.ch.rows} == {"CAR-00002"}
    assert [kw["name"] for _a, kw in w.enqueued] == ["CAR-00001"]


def test_rf11_the_race_comment_states_the_doubling_and_the_dbt_guard():
    with open(RUN_PY) as f:
        src = f.read()
    assert "last write wins" not in src
    assert "assert_cost_allocation_coefficients_close" in src
    assert "double" in src


# --- RF12: stored counts self-correct; a failed rebuild falls back to per-run
# repair; the F4 comment names the right writers (PR #541, RF11 re-review R1-R3).

def _count_set(w, run, n):
    return ("Cost Allocation Run", run, "coefficient_row_count", n, False)


def test_rf12_a_stale_stored_count_is_repaired_once_then_corrected():
    w = _World()
    n = len(_expected_rows())
    _site_counted(w, {"CAR-00001": 1, "CAR-00002": 1}, count=n + 3)     # stale
    _staged(w, {"CAR-00001": n, "CAR-00002": n})
    m = _k8(w)
    m.check_coefficients_staging()
    assert len(w.enqueued) == 2                                  # both repaired
    assert w.set_values == [_count_set(w, "CAR-00001", n), _count_set(w, "CAR-00002", n)]
    assert f"stored count {n + 3} -> {n}" in _logged_text(w)
    calls, enq, logged = len(w.ch.calls), len(w.enqueued), len(w.logged)
    m.check_coefficients_staging()                               # the second check does nothing
    assert [sql.split()[0] for sql, _p in w.ch.calls[calls:]] == ["SELECT"]
    assert len(w.enqueued) == enq and len(w.logged) == logged
    assert len(w.set_values) == 2


def test_rf12_a_legacy_run_with_a_stored_zero_gets_its_count_written_back():
    w = _World()
    n = len(_expected_rows())
    _site_counted(w, {"CAR-00001": 1, "CAR-00002": 1})
    w.get_all_data["Cost Allocation Run"][0]["coefficient_row_count"] = 0
    _staged(w, {"CAR-00001": n, "CAR-00002": n})
    m = _k8(w)
    model_calls = _count_model_calls(m)
    m.check_coefficients_staging()
    assert model_calls == ["CAR-00001"]
    assert w.set_values == [_count_set(w, "CAR-00001", n)]
    assert w.enqueued == []                                      # nothing was repaired
    m.check_coefficients_staging()                               # the model path ran once
    assert model_calls == ["CAR-00001"] and len(w.set_values) == 1


def test_rf13_a_legacy_count_is_stored_even_when_another_run_is_out_of_step():
    """Tester CA-1 bug 1: a mismatch on another run discarded the first-read
    correction of a matching legacy run, so its model path ran every hour."""
    w = _World()
    n = len(_expected_rows())
    _site_counted(w, {"CAR-00001": 1, "CAR-00002": 1})
    w.get_all_data["Cost Allocation Run"][0]["coefficient_row_count"] = 0   # legacy
    _staged(w, {"CAR-00001": n, "CAR-00002": n - 2})                         # 2 out of step
    m = _k8(w)
    model_calls = _count_model_calls(m)
    m.check_coefficients_staging()
    assert _count_set(w, "CAR-00001", n) in w.set_values
    calls = len(model_calls)
    m.check_coefficients_staging()                               # the model path ran once
    assert len(model_calls) == calls


def test_rf12_after_a_rebuild_the_counts_are_backfilled_from_the_rows_written():
    w = _World()
    n = len(_expected_rows())
    _site_counted(w, {f"CAR-{i:05d}": 1 for i in range(1, 22)}, count=1)   # all stale
    _staged(w, {})
    m = _k8(w)
    orig = m.clickhouse.sync_table

    def writing(table, columns, rows, **k):
        w.ch.rows = [dict(zip(columns, r)) for r in rows]
        return orig(table, columns, rows, **k)

    m.clickhouse.sync_table = writing
    m.check_coefficients_staging()
    assert len(w.sync_table_calls) == 1
    assert sorted(w.set_values) == sorted(_count_set(w, f"CAR-{i:05d}", n) for i in range(1, 22))


def test_rf12_a_failed_rebuild_repairs_run_by_run_and_logs_the_rejected_run():
    w = _World()
    _site_counted(w, {f"CAR-{i:05d}": 1 for i in range(1, 22)})        # 21 runs
    _staged(w, {})
    m = _k8(w)
    orig = m._coefficient_rows

    def rows(run_name, method, stored):
        if run_name == "CAR-00007":
            raise m.model.CostAllocationError("ZZ every driver value is zero")
        return orig(run_name, method, stored)

    m._coefficient_rows = rows
    m.check_coefficients_staging()                               # raises nothing
    assert w.sync_table_calls == []                              # the rebuild failed
    assert {r["run"] for r in w.ch.rows} == {f"CAR-{i:05d}" for i in range(1, 22)} - {"CAR-00007"}
    assert sorted(kw["name"] for _a, kw in w.enqueued) == sorted(
        f"CAR-{i:05d}" for i in range(1, 22) if i != 7)
    text = _logged_text(w)
    assert "not repaired: CAR-00007: " in text and "ZZ every driver value is zero" in text
    assert "rebuild failed" in text


def test_rf12_the_race_comment_names_the_callback_and_the_hourly_repair():
    with open(RUN_PY) as f:
        src = f.read()
    flat = " ".join(line.lstrip("# ") for line in src.splitlines())
    assert "two writers that both read the run as submitted: the after-commit callback and the hourly repair" in flat
    assert "db.commit" not in src
    assert "(a submit's callback and a fast\n#   cancel's in another request, or a callback and the hourly repair)" not in src


# --- 2b-1 F7/F8: driver sources at Compute, re-checked at submit ------------------
#
# konsol#269, Phase 2b plan section 1; A on Q1–Q7 (Deepak Pai, 27 Nov 2026).
# Every source fills the same weight rows. Fixed and derived values are
# snapshotted at Compute and re-read at approval with a locking read (fixed)
# or the same warehouse query (derived); any difference refuses the submit.

MARCH = (date(2026, 3, 1), date(2026, 3, 31))
UPLOADED, FIXED_W, ACCOUNT_SET = "Uploaded Statistic", "Fixed Weights", "Account Set"
DRIVER_PY = os.path.join(EPM_DOCTYPES, "cost_allocation_driver", "cost_allocation_driver.py")


def _driver(name, source=UPLOADED, dimension="dim_zz", value_sign=None, fixed_weights=(), accounts=()):
    """A driver as _driver_sources returns it."""
    return {"name": name, "source": source, "dimension": dimension, "value_sign": value_sign,
            "fixed_weights": [_Row(r) for r in fixed_weights],
            "accounts": [_Row(main_account=a) for a in accounts]}


def _fixed(member, value, effective_from, effective_to=None):
    return {"member": member, "value": value, "effective_from": effective_from,
            "effective_to": effective_to}


def _sources_module(drivers, derived="", **kw):
    """_run_module plus the F7 reads: driver sources, period dates, the derived query."""
    m = _run_module(**kw)
    by_name = {d["name"]: d for d in drivers}
    m._driver_sources = lambda names: {n: _Row(by_name[n]) for n in names if n in by_name}
    m.period_calls, m.derived_calls = [], []

    def period_dates(fy, fp):
        m.period_calls.append((fy, fp))
        return MARCH

    def query(sql, params):
        m.derived_calls.append((sql, params))
        if isinstance(derived, Exception):
            raise derived
        return derived

    m._period_dates = period_dates
    m._query_derived = query
    return m


def _k7_refused(fn):
    try:
        fn()
    except _Refused as exc:
        return str(exc)
    raise AssertionError("expected a refusal")


def _mixed_rule_set(second_driver):
    """Step 1 on the uploaded statistic, step 2 on ``second_driver``."""
    return _rule_set(rules=[_Row(step=1, sender_member="IT", driver="ZZ Head"),
                            _Row(step=2, sender_member="HR", driver=second_driver)])


def _fixed_driver():
    return _driver("ZZ Fixed", FIXED_W, fixed_weights=[
        _fixed("P1", 2, date(2025, 1, 1), date(2025, 12, 31)),    # expired
        _fixed("P1", 20, date(2026, 1, 1)),                        # covers March, open-ended
        _fixed("P2", 30, date(2025, 6, 1), date(2026, 12, 31)),    # covers March
        _fixed("HR", 99, date(2026, 1, 1))])                       # not a step-2 receiver: ignored


def _rev(sign="Reversed", accounts=("6000", "7000")):
    return _driver("ZZ Rev", ACCOUNT_SET, value_sign=sign, accounts=accounts)


def test_f7_source_constants_live_in_the_model_and_the_driver_imports_them():
    from konsol import cost_allocation_model as model
    assert model.SOURCES == (UPLOADED, FIXED_W, ACCOUNT_SET)
    assert (model.UPLOADED, model.FIXED, model.ACCOUNT_SET) == model.SOURCES
    with open(DRIVER_PY) as f:
        tree = ast.parse(f.read())
    imported = {a.name for n in tree.body if isinstance(n, ast.ImportFrom)
                and n.module == "konsol.cost_allocation_model" for a in n.names}
    assert {"UPLOADED", "FIXED", "ACCOUNT_SET", "SOURCES"} <= imported, imported
    assigned = [t.id for n in tree.body if isinstance(n, ast.Assign)
                for t in n.targets if isinstance(t, ast.Name)]
    assert not {"UPLOADED", "FIXED", "ACCOUNT_SET", "SOURCES"} & set(assigned), assigned


# -- F7: Uploaded Statistic unchanged ---------------------------------------------

def test_f7_statistic_runs_are_unchanged_and_say_where_the_values_came_from():
    m = _run_module()
    run = _Run()
    m.compute_weights_on(run)
    assert len(run.weights) == 4
    assert all(w["source"] == UPLOADED and w["effective_from"] is None for w in run.weights)
    assert all(w["statistic"] for w in run.weights)
    assert run.driver_accounts == []      # the stale row is gone; no Account Set driver
    assert run.saved == 1


def test_f7_statistic_runs_still_carry_forward():
    stats = [s for s in _stats() if not (s["member"] == "P1" and s["fiscal_period"] == 3)]
    m = _run_module(stats=stats, carry={"ZZ Head": True})
    run = _Run()
    assert m.compute_weights_on(run) == {"carried_forward_count": 2, "weights": 4}
    assert all(w["source"] == UPLOADED for w in run.weights)


# -- F7: Fixed Weights -------------------------------------------------------------

def test_f7_fixed_weights_fill_the_rows_from_the_window_covering_the_run_period():
    m = _sources_module([_driver("ZZ Head"), _fixed_driver()], rule_set=_mixed_rule_set("ZZ Fixed"))
    run = _Run()
    m.compute_weights_on(run)
    assert m.period_calls == [(2026, 3)]
    assert [(w["step"], w["receiver_member"]) for w in run.weights] == [
        (1, "HR"), (1, "P1"), (2, "P1"), (2, "P2")]            # step order kept
    got = {(w["step"], w["receiver_member"]): w for w in run.weights}
    p1, p2 = got[(2, "P1")], got[(2, "P2")]
    assert (p1["source"], p1["effective_from"], p1["driver_value"], p1["weight"]) == (
        FIXED_W, date(2026, 1, 1), 20, 0.4)
    assert (p2["source"], p2["effective_from"], p2["driver_value"], p2["weight"]) == (
        FIXED_W, date(2025, 6, 1), 30, 0.6)
    for w in (p1, p2):
        assert (w["statistic"], w["value_period"], w["carried_forward"]) == (None, 3, 0)
        assert (w["sender_member"], w["driver"]) == ("HR", "ZZ Fixed")
    hr = got[(1, "HR")]
    assert (hr["source"], hr["statistic"], hr["effective_from"]) == (UPLOADED, "CAS-00001", None)
    assert run.driver_accounts == []
    assert run.values_carried_forward == 0
    assert run.saved == 1


def test_f7_fixed_weights_take_the_period_dates_as_declared_not_by_month_arithmetic():
    m = _sources_module([_driver("ZZ Head"), _fixed_driver()], rule_set=_mixed_rule_set("ZZ Fixed"))
    m._period_dates = lambda fy, fp: (date(2026, 3, 1), date(2027, 2, 28))   # a long period
    msg = _k7_refused(lambda: m.compute_weights_on(_Run()))
    # P2's window ends 2026-12-31, inside that period
    assert "P2" in msg and "inside the period" in msg and "2026-12-31" in msg, msg


def test_f7_fixed_weight_refusals_join_the_statistic_ones_in_one_prefixed_throw():
    fixed = _driver("ZZ Fixed", FIXED_W, fixed_weights=[_fixed("P1", 20, date(2026, 3, 15))])
    stats = [_stat("CAS-00001", 3, "HR", 10)]     # step 1 P1 missing
    m = _sources_module([_driver("ZZ Head"), fixed], rule_set=_mixed_rule_set("ZZ Fixed"), stats=stats)
    run = _Run()
    msg = _k7_refused(lambda: m.compute_weights_on(run))
    assert msg.startswith("Cost Allocation Run: "), msg
    assert "step 1, P1" in msg, msg
    assert "P1" in msg and "inside the period" in msg and "2026-03-15" in msg, msg
    assert "P2: no fixed weight covers" in msg, msg
    assert run.saved == 0
    assert [w["step"] for w in run.weights] == [99]
    assert [r["driver"] for r in run.driver_accounts] == ["OLD"]


def test_f7_fixed_weights_accept_iso_strings_for_the_window_dates():
    fixed = _driver("ZZ Fixed", FIXED_W, fixed_weights=[
        _fixed("P1", 20, "2026-01-01"), _fixed("P2", 30, "2025-06-01", "2026-12-31 00:00:00")])
    m = _sources_module([_driver("ZZ Head"), fixed], rule_set=_mixed_rule_set("ZZ Fixed"))
    run = _Run()
    m.compute_weights_on(run)
    got = {(w["step"], w["receiver_member"]): w for w in run.weights}
    assert got[(2, "P1")]["effective_from"] == date(2026, 1, 1)
    assert got[(2, "P2")]["weight"] == 0.6


# -- F7: Account Set ---------------------------------------------------------------

def test_f7_account_set_derives_from_the_warehouse_and_snapshots_the_declared_accounts():
    m = _sources_module([_driver("ZZ Head"), _rev()], rule_set=_mixed_rule_set("ZZ Rev"),
                        derived="P1\t-20.5\t3\nP2\t-30\t2")
    run = _Run()
    m.compute_weights_on(run)
    assert m.period_calls == []            # no fixed driver: the period dates are not read
    [(sql, params)] = m.derived_calls
    assert sql.startswith("SELECT `dim_zz` AS member, sum(amount) AS total, count() AS row_count "
                          "FROM epm_gold.gold_scenario_trial_balance ")
    assert params == {
        "param_scenario": "ZZ Budget", "param_entity": "ZZE",
        "param_fiscal_year": "2026", "param_fiscal_period": "3",
        "param_members": "['P1','P2']",
        "param_accounts": "['6000','6010','6020','7000']"}      # descendants, once each
    got = {(w["step"], w["receiver_member"]): w for w in run.weights}
    p1, p2 = got[(2, "P1")], got[(2, "P2")]
    assert (p1["source"], p1["driver_value"], p1["statistic"], p1["effective_from"],
            p1["value_period"], p1["carried_forward"]) == (ACCOUNT_SET, 20.5, None, None, 3, 0)
    assert p2["driver_value"] == 30
    assert abs(p1["weight"] - 20.5 / 50.5) < 1e-12 and abs(p2["weight"] - 30 / 50.5) < 1e-12
    assert [(r["driver"], r["main_account"], r["value_sign"]) for r in run.driver_accounts] == [
        ("ZZ Rev", "6000", "Reversed"), ("ZZ Rev", "7000", "Reversed")]   # declared, not expanded
    assert run.saved == 1


def test_f7_account_set_uses_the_drivers_own_dimension_column():
    rev = _rev()
    rev["dimension"] = "dim_other"
    m = _sources_module([_driver("ZZ Head"), rev], rule_set=_mixed_rule_set("ZZ Rev"),
                        derived="P1\t-20\t3\nP2\t-30\t2")
    m.compute_weights_on(_Run())
    assert m.derived_calls[0][0].startswith("SELECT `dim_other` AS member")


def test_f7_account_set_runs_one_query_per_driver_over_every_step_that_uses_it():
    rs = _rule_set(rules=[_Row(step=1, sender_member="IT", driver="ZZ Rev"),
                          _Row(step=2, sender_member="HR", driver="ZZ Rev")])
    m = _sources_module([_rev("As Booked")], rule_set=rs, derived="HR\t10\t1\nP1\t20\t1\nP2\t30\t1")
    run = _Run()
    m.compute_weights_on(run)
    [(sql, params)] = m.derived_calls
    assert params["param_members"] == "['HR','P1','P2']"
    got = {(w["step"], w["receiver_member"]): w["weight"] for w in run.weights}
    assert got == {(1, "HR"): 10 / 30, (1, "P1"): 20 / 30, (2, "P1"): 0.4, (2, "P2"): 0.6}
    assert [(r["main_account"], r["value_sign"]) for r in run.driver_accounts] == [
        ("6000", "As Booked"), ("7000", "As Booked")]


def test_f7_account_set_zero_with_rows_is_a_real_zero():
    m = _sources_module([_driver("ZZ Head"), _rev()], rule_set=_mixed_rule_set("ZZ Rev"),
                        derived="P1\t0\t3\nP2\t-30\t2")
    run = _Run()
    m.compute_weights_on(run)
    got = {(w["step"], w["receiver_member"]): w["weight"] for w in run.weights}
    assert (got[(2, "P1")], got[(2, "P2")]) == (0.0, 1.0)


def test_f7_account_set_clickhouse_failure_refuses_compute_naming_the_driver():
    m = _sources_module([_driver("ZZ Head"), _rev()], rule_set=_mixed_rule_set("ZZ Rev"),
                        derived=ConnectionError("clickhouse down"))
    run = _Run()
    msg = _k7_refused(lambda: m.compute_weights_on(run))
    assert msg.startswith("Cost Allocation Run: "), msg
    assert "ZZ Rev" in msg and "clickhouse down" in msg and "trial balance" in msg, msg
    assert run.saved == 0 and [w["step"] for w in run.weights] == [99]


def test_f7_account_set_missing_and_negative_values_refuse_in_one_list():
    # P2 has no rows; P1 is a debit, so Reversed makes it negative
    m = _sources_module([_driver("ZZ Head"), _rev()], rule_set=_mixed_rule_set("ZZ Rev"),
                        derived="P1\t20\t3")
    run = _Run()
    msg = _k7_refused(lambda: m.compute_weights_on(run))
    assert msg.startswith("Cost Allocation Run: "), msg
    assert "P2" in msg and "missing" in msg.lower() and "P1 is negative" in msg, msg
    assert run.saved == 0


def test_f7_a_driver_with_an_unknown_source_or_none_at_all_refuses():
    m = _sources_module([_driver("ZZ Head", source="Weird")])
    msg = _k7_refused(lambda: m.compute_weights_on(_Run()))
    assert "ZZ Head" in msg and "Weird" in msg, msg
    m = _sources_module([])
    msg = _k7_refused(lambda: m.compute_weights_on(_Run()))
    assert "ZZ Head" in msg and "exist" in msg, msg


def test_f7_parse_sums_reads_member_total_and_row_count():
    m = _run_module()
    assert m.parse_sums("P1\t-20.5\t3\nP2\t0\t0\n\n") == {
        "P1": (Decimal("-20.5"), 3), "P2": (Decimal("0"), 0)}
    assert m.parse_sums("") == {}


def test_f7_the_reads_go_through_clickhouse_execute_and_period_status_period_dates():
    m = _run_module()
    calls = []
    m.clickhouse = types.SimpleNamespace(
        execute=lambda sql, params=None, timeout=None: calls.append((sql, params)) or "P1\t1\t1")
    assert m._query_derived("SELECT 1", {"a": "b"}) == "P1\t1\t1"
    assert calls == [("SELECT 1", {"a": "b"})]
    m.period_status = types.SimpleNamespace(period_dates=lambda fy, fp: (fy, fp))
    assert m._period_dates(2026, 3) == (2026, 3)


def test_f7_driver_sources_reads_the_header_and_both_child_tables():
    m = _run_module()
    data = {
        "Cost Allocation Driver": [
            {"name": "ZZ Fixed", "source": FIXED_W, "dimension": "dim_zz", "value_sign": None},
            {"name": "ZZ Rev", "source": ACCOUNT_SET, "dimension": "dim_zz", "value_sign": "Reversed"},
            {"name": "ZZ Other", "source": UPLOADED, "dimension": "dim_zz", "value_sign": None}],
        "Cost Allocation Driver Fixed Weight": [
            {"parent": "ZZ Fixed", "member": "P1", "value": 20, "effective_from": date(2026, 1, 1),
             "effective_to": None},
            {"parent": "ZZ Other", "member": "P1", "value": 1, "effective_from": date(2026, 1, 1),
             "effective_to": None}],
        "Cost Allocation Driver Account": [{"parent": "ZZ Rev", "main_account": "6000"}],
    }
    calls = []

    def get_all(doctype, filters=None, fields=None, **k):
        calls.append((doctype, filters, k))
        return [_Row(r) for r in data.get(doctype, []) if _matches(r, filters)]

    m.frappe.get_all = get_all
    out = m._driver_sources(["ZZ Fixed", "ZZ Rev"])
    assert set(out) == {"ZZ Fixed", "ZZ Rev"}
    assert (out["ZZ Fixed"]["source"], out["ZZ Fixed"]["dimension"]) == (FIXED_W, "dim_zz")
    assert [(r["member"], r["value"], r["effective_from"]) for r in out["ZZ Fixed"]["fixed_weights"]] == [
        ("P1", 20, date(2026, 1, 1))]
    assert out["ZZ Fixed"]["accounts"] == []
    assert [r["main_account"] for r in out["ZZ Rev"]["accounts"]] == ["6000"]
    assert out["ZZ Rev"]["fixed_weights"] == [] and out["ZZ Rev"]["value_sign"] == "Reversed"
    children = [c for c in calls if c[0] != "Cost Allocation Driver"]
    assert len(children) == 2
    assert all(c[2].get("parent_doctype") == "Cost Allocation Driver" for c in children)
    assert all(c[1]["parent"] == ["in", ["ZZ Fixed", "ZZ Rev"]] for c in children)
    assert m._driver_sources([]) == {}


# -- F8: the stale check at submit -----------------------------------------------

def _fixed_world():
    w = _World()
    w.drivers["ZZ Fixed"] = {"name": "ZZ Fixed", "source": FIXED_W, "dimension": "dim_zz",
                             "value_sign": None}
    w.fixed_rows = [
        {"parent": "ZZ Fixed", "member": "P1", "value": 20, "effective_from": date(2026, 1, 1),
         "effective_to": None},
        {"parent": "ZZ Fixed", "member": "P2", "value": 30, "effective_from": date(2025, 6, 1),
         "effective_to": date(2026, 12, 31)},
        {"parent": "ZZ Fixed", "member": "P1", "value": 2, "effective_from": date(2025, 1, 1),
         "effective_to": date(2025, 12, 31)},
    ]
    return w


def _fixed_run(m, **over):
    """Step 1 on the statistic (as _k8_run), step 2 on ZZ Fixed as Compute wrote it."""
    rows = [_Row(step=2, sender_member="HR", receiver_member="P1", driver="ZZ Fixed", statistic=None,
                 source=FIXED_W, effective_from=date(2026, 1, 1), value_period=3, driver_value=20),
            _Row(step=2, sender_member="HR", receiver_member="P2", driver="ZZ Fixed", statistic=None,
                 source=FIXED_W, effective_from=date(2025, 6, 1), value_period=3, driver_value=30)]
    return _k8_run(m, weights=_k8_run(m).weights[:2] + rows, **over)


def test_f8_a_fixed_weight_edited_after_compute_refuses_submit():
    w = _fixed_world()
    w.fixed_rows[0]["value"] = 25
    msg = _refused(_fixed_run(_k8(w)).before_submit)
    assert "ZZ Fixed" in msg and "P1" in msg, msg
    assert "20" in msg and "25" in msg, msg
    assert "recompute" in msg.lower(), msg
    assert "P2" not in msg, msg


def test_f8_unchanged_fixed_weights_pass_and_are_read_for_update():
    w = _fixed_world()
    _fixed_run(_k8(w)).before_submit()
    header = next(q for q, _v in w.sql if "`tabCost Allocation Driver`" in q)
    child = next(q for q, _v in w.sql if "`tabCost Allocation Driver Fixed Weight`" in q)
    assert header.rstrip().upper().endswith("FOR UPDATE"), header
    assert child.rstrip().upper().endswith("FOR UPDATE"), child
    assert "parenttype = 'Cost Allocation Driver'" in child
    assert w.period_dates_calls == [(2026, 3)]
    assert w.derived_calls == []


def test_f8_a_fixed_window_that_moved_refuses_even_with_the_same_value():
    w = _fixed_world()
    w.fixed_rows[0]["effective_from"] = date(2026, 2, 1)
    msg = _refused(_fixed_run(_k8(w)).before_submit)
    assert "P1" in msg and "2026-01-01" in msg and "2026-02-01" in msg, msg


def test_f8_a_fixed_window_that_no_longer_resolves_refuses_with_the_models_reason():
    w = _fixed_world()
    del w.fixed_rows[0]              # P1 keeps only its expired window
    msg = _refused(_fixed_run(_k8(w)).before_submit)
    assert "ZZ Fixed" in msg and "P1: no fixed weight covers" in msg, msg
    w = _fixed_world()
    w.fixed_rows[0]["effective_to"] = date(2026, 3, 15)   # now changes inside March
    msg = _refused(_fixed_run(_k8(w)).before_submit)
    assert "P1" in msg and "inside the period" in msg, msg


def test_f8_a_fixed_weight_value_still_matching_at_nine_places_passes():
    w = _fixed_world()
    w.fixed_rows[0]["value"] = 20.0000000001
    _fixed_run(_k8(w)).before_submit()


def test_f8_a_driver_whose_source_changed_since_compute_refuses():
    w = _fixed_world()
    w.drivers["ZZ Fixed"]["source"] = ACCOUNT_SET
    msg = _refused(_fixed_run(_k8(w)).before_submit)
    assert "ZZ Fixed" in msg and "source" in msg.lower(), msg
    assert FIXED_W in msg and ACCOUNT_SET in msg, msg
    # a statistic driver (rows written before 2b-1 carry no source) now Fixed Weights
    w = _World()
    w.drivers["ZZ Head"]["source"] = FIXED_W
    msg = _refused(_k8_run(_k8(w)).before_submit)
    assert "ZZ Head" in msg and "source" in msg.lower() and UPLOADED in msg, msg


def test_f8_a_driver_that_no_longer_exists_refuses():
    w = _World()
    w.drivers = {}
    msg = _refused(_k8_run(_k8(w)).before_submit)
    assert "ZZ Head" in msg and "recompute" in msg.lower(), msg


def test_f8_statistic_only_runs_check_the_source_but_read_no_fixed_weights_or_warehouse():
    w = _World()
    _k8_run(_k8(w)).before_submit()
    assert any("`tabCost Allocation Driver`" in q for q, _v in w.sql)
    assert not any("Fixed Weight" in q for q, _v in w.sql)
    assert w.derived_calls == [] and w.period_dates_calls == []


def _derived_world(text="P1\t-20.5\t3\nP2\t-30\t2"):
    w = _World()
    w.drivers["ZZ Rev"] = {"name": "ZZ Rev", "source": ACCOUNT_SET, "dimension": "dim_zz",
                           "value_sign": "Reversed"}
    w.get_all_data["Cost Allocation Driver Account"] = [
        {"parent": "ZZ Rev", "main_account": "6000"}, {"parent": "ZZ Rev", "main_account": "7000"}]
    w.derived = text
    return w


def _derived_run(m, **over):
    rows = [_Row(step=2, sender_member="HR", receiver_member="P1", driver="ZZ Rev", statistic=None,
                 source=ACCOUNT_SET, effective_from=None, value_period=3, driver_value=20.5),
            _Row(step=2, sender_member="HR", receiver_member="P2", driver="ZZ Rev", statistic=None,
                 source=ACCOUNT_SET, effective_from=None, value_period=3, driver_value=30)]
    snapshot = [_Row(driver="ZZ Rev", main_account="6000", value_sign="Reversed"),
                _Row(driver="ZZ Rev", main_account="7000", value_sign="Reversed")]
    over.setdefault("driver_accounts", snapshot)
    return _k8_run(m, weights=_k8_run(m).weights[:2] + rows, **over)


def test_f8_unchanged_derived_values_pass_and_the_query_is_the_one_compute_ran():
    w = _derived_world()
    _derived_run(_k8(w)).before_submit()
    [(sql, params)] = w.derived_calls
    assert sql.startswith("SELECT `dim_zz` AS member, sum(amount) AS total, count() AS row_count ")
    assert params["param_accounts"] == "['6000','6010','6020','7000']"
    assert params["param_members"] == "['P1','P2']"
    assert (params["param_scenario"], params["param_entity"], params["param_fiscal_year"],
            params["param_fiscal_period"]) == ("ZZ Actual", "ZZE", "2026", "3")
    assert w.period_dates_calls == []


def test_f8_a_derived_value_that_moved_refuses():
    w = _derived_world("P1\t-22\t3\nP2\t-30\t2")
    msg = _refused(_derived_run(_k8(w)).before_submit)
    assert "ZZ Rev" in msg and "P1" in msg and "20.5" in msg and "22" in msg, msg
    assert "recompute" in msg.lower() and "P2" not in msg, msg


def test_f8_a_derived_member_now_missing_or_negative_refuses():
    msg = _refused(_derived_run(_k8(_derived_world("P1\t-20.5\t3"))).before_submit)
    assert "ZZ Rev" in msg and "P2" in msg and "missing" in msg.lower(), msg
    msg = _refused(_derived_run(_k8(_derived_world("P1\t5\t3\nP2\t-30\t2"))).before_submit)
    assert "P1 is negative" in msg, msg


def test_f8_a_drivers_accounts_or_sign_changed_since_compute_refuses_before_querying():
    w = _derived_world()
    w.get_all_data["Cost Allocation Driver Account"].pop()
    msg = _refused(_derived_run(_k8(w)).before_submit)
    assert "ZZ Rev" in msg and "accounts" in msg.lower() and "recompute" in msg.lower(), msg
    assert w.derived_calls == []
    w = _derived_world()
    w.drivers["ZZ Rev"]["value_sign"] = "As Booked"
    msg = _refused(_derived_run(_k8(w)).before_submit)
    assert "ZZ Rev" in msg and "sign" in msg.lower(), msg
    assert w.derived_calls == []


def test_f8_clickhouse_down_at_submit_refuses_rather_than_passing():
    w = _derived_world(ConnectionError("clickhouse down"))
    msg = _refused(_derived_run(_k8(w)).before_submit)
    assert "ZZ Rev" in msg and "clickhouse down" in msg and "trial balance" in msg, msg


def test_f8_every_stale_driver_row_is_listed_in_one_refusal():
    w = _fixed_world()
    w.fixed_rows[0]["value"] = 25
    w.fixed_rows[1]["value"] = 35
    msg = _refused(_fixed_run(_k8(w)).before_submit)
    assert msg.startswith("Cost Allocation Run: "), msg
    assert "P1" in msg and "25" in msg and "P2" in msg and "35" in msg, msg
    assert "statistic" not in msg.lower(), msg    # the driver check, not the statistic one


def test_f8_stale_driver_values_runs_beside_stale_statistics_before_the_coefficients():
    with open(RUN_PY) as f:
        src = f.read()
    body = src[src.index("def before_submit"):src.index("def _refuse_stale_snapshot")]
    assert (body.index("self._refuse_stale_statistics(")
            < body.index("self._refuse_stale_driver_values(")
            < body.index("self._refuse_unbuildable_coefficients("))
