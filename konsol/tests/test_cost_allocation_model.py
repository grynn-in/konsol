"""Cost allocation model, pure: konsol/cost_allocation_model.py (konsol#269, K2a).

Loaded by path; the module imports nothing from frappe or konsol.
"""
import ast
import importlib.util
import os
from fractions import Fraction

import contextlib

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PATH = os.path.join(APP_DIR, "cost_allocation_model.py")
_spec = importlib.util.spec_from_file_location("cost_allocation_model_under_test", _PATH)
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


@contextlib.contextmanager
def raises(exc):
    box = type("Box", (), {})()
    try:
        yield box
    except exc as e:
        box.value = e
        return
    raise AssertionError("did not raise %s" % exc.__name__)


def _rs(**kw):
    base = dict(
        entity="ZZE",
        dimension="Department",
        method="Step-Down",
        receiver_account_mode="Sender's Own Accounts",
        allocation_account="",
        rules=[
            {"step": 1, "sender_member": "HR", "driver": "Headcount"},
            {"step": 2, "sender_member": "IT", "driver": "Headcount"},
        ],
        receivers=[
            {"step": 1, "receiver_member": "IT"},
            {"step": 1, "receiver_member": "Sales"},
            {"step": 2, "receiver_member": "Sales"},
            {"step": 2, "receiver_member": "Ops"},
        ],
        dimension_members=["HR", "IT", "Sales", "Ops"],
        driver_dimensions={"Headcount": "Department"},
    )
    base.update(kw)
    return M.RuleSet(**base)


def _errors(rs):
    return M.validate(rs)


def _has(errors, text):
    return any(text in e for e in errors)


def test_no_frappe_imports():
    with open(_PATH) as fh:
        tree = ast.parse(fh.read())
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            mods.add((n.module or "").split(".")[0])
    assert not mods & {"frappe", "konsol", "clickhouse_connect"}


# --- validation --------------------------------------------------------

def test_validation_valid_step_down():
    assert _errors(_rs()) == []

def test_validation_valid_direct():
    rs = _rs(
        method="Direct",
        receivers=[
            {"step": 1, "receiver_member": "Sales"},
            {"step": 2, "receiver_member": "Ops"},
        ],
    )
    assert _errors(rs) == []

def test_validation_assert_valid_raises_with_every_error():
    rs = _rs(method="Bogus", dimension_members=["HR"])
    with raises(M.CostAllocationError) as ei:
        M.assert_valid(rs)
    assert len(ei.value.errors) >= 2
    assert str(ei.value).count("\n") >= 1

def test_validation_all_errors_at_once():
    rs = _rs(
        rules=[
            {"step": 1, "sender_member": "HR", "driver": "Headcount"},
            {"step": 1, "sender_member": "HR", "driver": "Headcount"},
        ],
        receivers=[{"step": 1, "receiver_member": "HR"}, {"step": 3, "receiver_member": "Nope"}],
    )
    errs = _errors(rs)
    assert _has(errs, "Step 1 is declared more than once")
    assert _has(errs, "HR is a sender more than once")
    assert _has(errs, "cannot receive its own")
    assert _has(errs, "Step 3 has receivers but no rule")
    assert _has(errs, "Nope is not a member")

def test_validation_unknown_method():
    assert _has(_errors(_rs(method="Bogus")), "Method")

def test_validation_no_rules():
    assert _has(_errors(_rs(rules=[], receivers=[])), "at least one rule")

def test_validation_sender_not_in_dimension():
    rs = _rs(dimension_members=["IT", "Sales", "Ops"])
    assert _has(_errors(rs), "HR is not a member of Department")

def test_validation_receiver_not_in_dimension():
    rs = _rs(dimension_members=["HR", "IT", "Sales"])
    assert _has(_errors(rs), "Ops is not a member of Department")

def test_validation_driver_dimension_must_match():
    rs = _rs(driver_dimensions={"Headcount": "Region"})
    assert _has(_errors(rs), "Headcount")
    assert _has(_errors(rs), "Region")

def test_validation_unknown_driver():
    rs = _rs(driver_dimensions={})
    assert _has(_errors(rs), "Driver Headcount")

def test_validation_step_without_receiver():
    rs = _rs(receivers=[{"step": 1, "receiver_member": "IT"}])
    assert _has(_errors(rs), "Step 2 has no receiver")

def test_validation_receiver_for_undeclared_step():
    rs = _rs(receivers=_rs().receivers + [{"step": 9, "receiver_member": "Ops"}])
    assert _has(_errors(rs), "Step 9 has receivers but no rule")

def test_validation_duplicate_step():
    rs = _rs(rules=_rs().rules + [{"step": 2, "sender_member": "Ops", "driver": "Headcount"}])
    assert _has(_errors(rs), "Step 2 is declared more than once")

def test_validation_duplicate_sender():
    rs = _rs(rules=[
        {"step": 1, "sender_member": "HR", "driver": "Headcount"},
        {"step": 2, "sender_member": "HR", "driver": "Headcount"},
    ])
    assert _has(_errors(rs), "HR is a sender more than once")

def test_validation_duplicate_receiver_in_step():
    rs = _rs(receivers=_rs().receivers + [{"step": 1, "receiver_member": "Sales"}])
    assert _has(_errors(rs), "Sales is a receiver of step 1 more than once")

def test_validation_self_receive():
    rs = _rs(receivers=_rs().receivers + [{"step": 1, "receiver_member": "HR"}])
    assert _has(_errors(rs), "HR cannot receive its own")

def test_validation_direct_receiver_cannot_be_sender():
    rs = _rs(method="Direct")  # step 1 receivers include IT, a sender at step 2
    assert _has(_errors(rs), "IT is a receiver and a sender")

def test_validation_step_down_receiver_sender_at_earlier_step():
    rs = _rs(receivers=_rs().receivers + [{"step": 2, "receiver_member": "HR"}])
    assert _has(_errors(rs), "HR receives at step 2 but is a sender at step 1")

def test_validation_step_down_receiver_sender_at_same_step_is_self_receive():
    rs = _rs(receivers=_rs().receivers + [{"step": 2, "receiver_member": "IT"}])
    assert _has(_errors(rs), "IT cannot receive its own")

def test_validation_step_down_receiver_sender_at_later_step_allowed():
    # IT receives at step 1 and sends at step 2
    assert _errors(_rs()) == []

def test_validation_declared_account_requires_account():
    rs = _rs(receiver_account_mode="Declared Allocation Account", allocation_account="")
    assert _has(_errors(rs), "Allocation Account is required")

def test_validation_declared_account_ok():
    rs = _rs(receiver_account_mode="Declared Allocation Account", allocation_account="6999")
    assert _errors(rs) == []

def test_validation_account_without_declared_mode_refused():
    rs = _rs(allocation_account="6999")
    assert _has(_errors(rs), "Allocation Account is set")

def test_validation_unknown_account_mode():
    assert _has(_errors(_rs(receiver_account_mode="X")), "Receiver account mode")


# --- values ------------------------------------------------------------

def _steps():
    return M.build_steps(_rs().rules, _rs().receivers)


def test_values_build_steps_sorted_with_receivers():
    steps = _steps()
    assert [s.step for s in steps] == [1, 2]
    assert steps[0].sender_member == "HR"
    assert steps[0].receivers == ["IT", "Sales"]
    assert steps[1].receivers == ["Sales", "Ops"]

def _values(period=3):
    return {
        ("Headcount", period, m): v
        for m, v in {"IT": 4, "Sales": 6, "Ops": 10}.items()
    }

def test_values_resolves_every_step_receiver():
    rows = M.resolve_driver_values(_steps(), 3, _values(), {"Headcount": False})
    got = {(r["step"], r["receiver_member"]): r for r in rows}
    assert set(got) == {(1, "IT"), (1, "Sales"), (2, "Sales"), (2, "Ops")}
    r = got[(1, "IT")]
    assert r["driver_value"] == 4
    assert r["value_period"] == 3
    assert r["carried_forward"] is False
    assert r["sender_member"] == "HR"
    assert r["driver"] == "Headcount"

def test_values_missing_lists_every_one():
    vals = {("Headcount", 3, "IT"): 4}
    with raises(M.CostAllocationError) as ei:
        M.resolve_driver_values(_steps(), 3, vals, {"Headcount": False})
    errs = ei.value.errors
    assert len(errs) == 1  # one error listing all
    for expect in ("step 1, Sales, period 3", "step 2, Sales, period 3", "step 2, Ops, period 3"):
        assert expect in errs[0]
    assert "IT, period" not in errs[0]

def test_values_zero_is_a_value_not_missing():
    vals = _values()
    vals[("Headcount", 3, "IT")] = 0
    rows = M.resolve_driver_values(_steps(), 3, vals, {"Headcount": False})
    assert [r for r in rows if r["receiver_member"] == "IT"][0]["driver_value"] == 0

def test_values_carry_forward_uses_latest_earlier_period():
    vals = {
        ("Headcount", 1, "Sales"): 1,
        ("Headcount", 2, "Sales"): 7,
        ("Headcount", 3, "IT"): 4,
        ("Headcount", 3, "Ops"): 10,
        ("Headcount", 4, "Sales"): 99,  # later period: never used
    }
    rows = M.resolve_driver_values(_steps(), 3, vals, {"Headcount": True})
    sales = [r for r in rows if r["receiver_member"] == "Sales"]
    assert len(sales) == 2
    for r in sales:
        assert r["driver_value"] == 7
        assert r["value_period"] == 2
        assert r["carried_forward"] is True
    it = [r for r in rows if r["receiver_member"] == "IT"][0]
    assert it["carried_forward"] is False and it["value_period"] == 3

def test_values_carry_forward_off_refuses_even_if_earlier_exists():
    vals = dict(_values())
    vals[("Headcount", 2, "Sales")] = 7
    del vals[("Headcount", 3, "Ops")]
    with raises(M.CostAllocationError):
        M.resolve_driver_values(_steps(), 3, vals, {"Headcount": False})

def test_values_carry_forward_on_but_nothing_earlier_refuses():
    vals = {("Headcount", 3, "IT"): 4, ("Headcount", 3, "Ops"): 10}
    with raises(M.CostAllocationError) as ei:
        M.resolve_driver_values(_steps(), 3, vals, {"Headcount": True})
    assert "Sales" in ei.value.errors[0]

def test_values_undeclared_carry_forward_is_refused_not_defaulted():
    vals = _values()
    del vals[("Headcount", 3, "Sales")]
    vals[("Headcount", 2, "Sales")] = 7
    with raises(M.CostAllocationError) as ei:
        M.resolve_driver_values(_steps(), 3, vals, {})
    assert "Headcount" in ei.value.errors[0]

def test_values_none_value_is_missing():
    vals = _values()
    vals[("Headcount", 3, "IT")] = None
    with raises(M.CostAllocationError):
        M.resolve_driver_values(_steps(), 3, vals, {"Headcount": False})


# --- weights -----------------------------------------------------------

def _rows(vals):
    steps = _steps()
    values = {}
    for (step, recv), v in vals.items():
        values[("Headcount", 1, recv)] = v
    return M.resolve_driver_values(steps, 1, values, {"Headcount": False})

def test_weights_weights_are_exact_fractions_summing_to_one():
    rows = _rows({(1, "IT"): 1, (1, "Sales"): 2, (2, "Ops"): 3})
    # IT 1, Sales 2, Ops 3 serve both steps
    w = M.weights(rows)
    assert w[(1, "IT")] == Fraction(1, 3)
    assert w[(1, "Sales")] == Fraction(2, 3)
    assert w[(2, "Sales")] == Fraction(2, 5)
    assert w[(2, "Ops")] == Fraction(3, 5)
    for step in (1, 2):
        assert sum(v for (s, _), v in w.items() if s == step) == 1
    assert all(isinstance(v, Fraction) for v in w.values())

def test_weights_one_receiver_weight_is_one():
    rows = [{"step": 1, "receiver_member": "A", "driver_value": 5}]
    assert M.weights(rows) == {(1, "A"): Fraction(1)}

def test_weights_zero_receiver_gets_zero_weight():
    rows = [
        {"step": 1, "receiver_member": "A", "driver_value": 0},
        {"step": 1, "receiver_member": "B", "driver_value": 4},
    ]
    w = M.weights(rows)
    assert w[(1, "A")] == 0 and w[(1, "B")] == 1

def test_weights_float_values_are_exact_decimals():
    rows = [
        {"step": 1, "receiver_member": "A", "driver_value": 0.1},
        {"step": 1, "receiver_member": "B", "driver_value": 0.2},
    ]
    w = M.weights(rows)
    assert w[(1, "A")] == Fraction(1, 3)

def test_weights_all_zero_step_refused_naming_the_step():
    rows = [
        {"step": 1, "receiver_member": "A", "driver_value": 1},
        {"step": 2, "receiver_member": "A", "driver_value": 0},
        {"step": 2, "receiver_member": "B", "driver_value": 0},
    ]
    with raises(M.CostAllocationError) as ei:
        M.weights(rows)
    assert len(ei.value.errors) == 1 and "step 2" in ei.value.errors[0]

def test_weights_every_all_zero_step_listed():
    rows = [
        {"step": 1, "receiver_member": "A", "driver_value": 0},
        {"step": 2, "receiver_member": "B", "driver_value": 0},
    ]
    with raises(M.CostAllocationError) as ei:
        M.weights(rows)
    assert len(ei.value.errors) == 2

def test_weights_negative_value_refused():
    rows = [
        {"step": 1, "receiver_member": "A", "driver_value": -1},
        {"step": 1, "receiver_member": "B", "driver_value": 3},
    ]
    with raises(M.CostAllocationError):
        M.weights(rows)
