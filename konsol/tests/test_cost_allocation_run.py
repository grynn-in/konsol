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
    assert doc["autoname"] == "format:CAR-{#####}"
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
    assert fields["method"]["options"] == "Direct\nStep-Down"
    assert fields["receiver_account_mode"]["options"] == (
        "Sender's Own Accounts\nDeclared Allocation Account")
    assert fields["amounts_after_approval"]["options"] == "Follow Latest Data\nFreeze at Approval"


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
import sys
import types
from datetime import datetime
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
        self.weights = [_Row(step=99)]   # a stale row that must be replaced
        self.accounts = [_Row(main_account="OLD")]
        self.saved = 0

    def set(self, field, rows):
        setattr(self, field, [_Row(r) for r in rows])

    def save(self):
        self.saved += 1


def _run_module(rule_set=None, stats=None, carry=None, tree=None, user="zz@example.com"):
    from konsol.tests import test_cost_allocation_doctypes as dt  # noqa: F401  (stub helper)
    dt._install_document_stub()
    # the module-level @frappe.whitelist runs at import; give the stub one
    sys.modules["frappe"].whitelist = lambda *a, **k: (lambda fn: fn)
    spec = importlib.util.spec_from_file_location("cost_allocation_run_k7", RUN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.frappe = _FakeFrappe()
    module._load_rule_set = lambda name: rule_set if rule_set is not None else _rule_set()
    module._statistic_values = lambda entity, scenario, fy, period: list(
        stats if stats is not None else _stats())
    module._driver_carry_forward = lambda drivers: dict(carry or {})
    module._descendants = lambda account: list((tree or _TREE)[account])
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
