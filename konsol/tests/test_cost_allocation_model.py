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


# --- coefficients (K2b) --------------------------------------------------

import random
from decimal import Decimal

_Q = Decimal("1e-12")
_UNIT = Fraction(1, 10 ** 12)


def _hand_steps():
    # Step 1: IT -> HR, P1, P2 by 2:3:5. Step 2: HR -> P1, P2, P3 by 1:1:1.
    steps = [
        M.Step(1, "IT", "Headcount", ["HR", "P1", "P2"]),
        M.Step(2, "HR", "Headcount", ["P1", "P2", "P3"]),
    ]
    w = {
        (1, "HR"): Fraction(1, 5), (1, "P1"): Fraction(3, 10), (1, "P2"): Fraction(1, 2),
        (2, "P1"): Fraction(1, 3), (2, "P2"): Fraction(1, 3), (2, "P3"): Fraction(1, 3),
    }
    return steps, w


def _key(r):
    return (r["origin_member"], r["step"], r["sender_member"], r["receiver_member"])


def test_coefficients_step_down_exact_hand_worked():
    steps, w = _hand_steps()
    rows = {_key(r): r for r in M.step_down_coefficients(steps, w)}
    assert set(rows) == {
        ("IT", 1, "IT", "HR"), ("IT", 1, "IT", "P1"), ("IT", 1, "IT", "P2"),
        ("IT", 2, "HR", "P1"), ("IT", 2, "HR", "P2"), ("IT", 2, "HR", "P3"),
        ("HR", 2, "HR", "P1"), ("HR", 2, "HR", "P2"), ("HR", 2, "HR", "P3"),
    }
    assert rows[("IT", 1, "IT", "HR")]["pool_coefficient"] == 1
    assert rows[("IT", 1, "IT", "HR")]["coefficient"] == Fraction(1, 5)
    assert rows[("IT", 2, "HR", "P3")]["pool_coefficient"] == Fraction(1, 5)
    assert rows[("IT", 2, "HR", "P3")]["coefficient"] == Fraction(1, 15)
    assert rows[("HR", 2, "HR", "P1")]["pool_coefficient"] == 1
    assert rows[("HR", 2, "HR", "P1")]["coefficient"] == Fraction(1, 3)
    assert all(r["driver"] == "Headcount" for r in rows.values())
    assert all(isinstance(r["coefficient"], Fraction) for r in rows.values())


def test_coefficients_step_down_rounded_hand_worked():
    steps, w = _hand_steps()
    rows = {_key(r): r for r in M.coefficient_rows("ZZRUN-1", steps, w, "Step-Down")}
    D = Decimal
    expect = {
        ("IT", 1, "IT", "HR"): (D("1"), D("0.2")),
        ("IT", 1, "IT", "P1"): (D("1"), D("0.3")),
        ("IT", 1, "IT", "P2"): (D("1"), D("0.5")),
        # 0.2 / 3 each: two units left over go to P1, P2 (equal remainders, by name)
        ("IT", 2, "HR", "P1"): (D("0.2"), D("0.066666666667")),
        ("IT", 2, "HR", "P2"): (D("0.2"), D("0.066666666667")),
        ("IT", 2, "HR", "P3"): (D("0.2"), D("0.066666666666")),
        ("HR", 2, "HR", "P1"): (D("1"), D("0.333333333334")),
        ("HR", 2, "HR", "P2"): (D("1"), D("0.333333333333")),
        ("HR", 2, "HR", "P3"): (D("1"), D("0.333333333333")),
    }
    assert set(rows) == set(expect)
    for k, (pool, coef) in expect.items():
        assert rows[k]["pool_coefficient"] == pool, k
        assert rows[k]["coefficient"] == coef, k
        assert rows[k]["run"] == "ZZRUN-1"
        assert rows[k]["coefficient"].as_tuple().exponent == -12
        assert rows[k]["pool_coefficient"].as_tuple().exponent == -12
    final_it = sum(r["coefficient"] for k, r in rows.items() if k[0] == "IT" and k[3] != "HR")
    assert final_it == Decimal("1")


def test_coefficients_row_shape_matches_staging_table():
    steps, w = _hand_steps()
    rows = M.coefficient_rows("ZZRUN-1", steps, w, "Step-Down")
    cols = ["run", "origin_member", "step", "sender_member", "receiver_member",
            "driver", "pool_coefficient", "coefficient"]
    assert all(list(r) == cols for r in rows)


def test_coefficients_direct():
    steps = [
        M.Step(1, "IT", "Headcount", ["P1", "P2"]),
        M.Step(2, "HR", "Area", ["P1", "P2", "P3"]),
    ]
    w = {(1, "P1"): Fraction(1, 3), (1, "P2"): Fraction(2, 3),
         (2, "P1"): Fraction(1, 7), (2, "P2"): Fraction(2, 7), (2, "P3"): Fraction(4, 7)}
    exact = {_key(r): r for r in M.direct_coefficients(steps, w)}
    assert set(exact) == {("IT", 1, "IT", "P1"), ("IT", 1, "IT", "P2"),
                          ("HR", 2, "HR", "P1"), ("HR", 2, "HR", "P2"), ("HR", 2, "HR", "P3")}
    assert all(r["pool_coefficient"] == 1 for r in exact.values())
    assert exact[("HR", 2, "HR", "P3")]["coefficient"] == Fraction(4, 7)
    assert exact[("HR", 2, "HR", "P3")]["driver"] == "Area"
    rows = {_key(r): r for r in M.coefficient_rows("ZZRUN-2", steps, w, "Direct")}
    assert rows[("IT", 1, "IT", "P1")]["coefficient"] == Decimal("0.333333333333")
    assert rows[("IT", 1, "IT", "P2")]["coefficient"] == Decimal("0.666666666667")
    # 1/7 = .142857142857|142, 2/7 = .285714285714|285, 4/7 = .571428571428|571
    assert rows[("HR", 2, "HR", "P1")]["coefficient"] == Decimal("0.142857142857")
    assert rows[("HR", 2, "HR", "P2")]["coefficient"] == Decimal("0.285714285714")
    assert rows[("HR", 2, "HR", "P3")]["coefficient"] == Decimal("0.571428571429")


def test_coefficients_direct_refuses_receiver_that_sends():
    steps = [M.Step(1, "IT", "H", ["HR"]), M.Step(2, "HR", "H", ["P1"])]
    w = {(1, "HR"): Fraction(1), (2, "P1"): Fraction(1)}
    with raises(M.CostAllocationError):
        M.direct_coefficients(steps, w)


def test_coefficients_step_down_refuses_backward_flow():
    steps = [M.Step(1, "IT", "H", ["P1"]), M.Step(2, "HR", "H", ["IT"])]
    w = {(1, "P1"): Fraction(1), (2, "IT"): Fraction(1)}
    with raises(M.CostAllocationError):
        M.step_down_coefficients(steps, w)


def test_coefficients_missing_weight_refused():
    steps = [M.Step(1, "IT", "H", ["P1", "P2"])]
    with raises(M.CostAllocationError):
        M.direct_coefficients(steps, {(1, "P1"): Fraction(1)})


def test_coefficients_unknown_method_refused():
    steps, w = _hand_steps()
    with raises(M.CostAllocationError):
        M.coefficient_rows("ZZRUN-1", steps, w, "Iterative")


def test_coefficients_unreached_sender_has_no_rows_for_that_origin():
    # IT gives HR zero weight, so origin IT never reaches HR's step.
    steps = [M.Step(1, "IT", "H", ["HR", "P1"]), M.Step(2, "HR", "H", ["P1", "P2"])]
    w = {(1, "HR"): Fraction(0), (1, "P1"): Fraction(1),
         (2, "P1"): Fraction(1, 2), (2, "P2"): Fraction(1, 2)}
    keys = {_key(r) for r in M.step_down_coefficients(steps, w)}
    assert not any(k[0] == "IT" and k[2] == "HR" for k in keys)
    assert ("IT", 1, "IT", "HR") in keys  # the zero-weight hop itself is kept


def _check_invariants(steps, w, method):
    exact = M.step_down_coefficients(steps, w) if method == "Step-Down" \
        else M.direct_coefficients(steps, w)
    rows = M.coefficient_rows("ZZRUN", steps, w, method)
    assert [_key(r) for r in rows] == [_key(r) for r in exact]
    senders = {s.sender_member for s in steps}
    sender_step = {s.sender_member: s.step for s in steps}
    hops = {}
    for r in rows:
        assert r["coefficient"].as_tuple().exponent == -12
        assert r["pool_coefficient"].as_tuple().exponent == -12
        assert r["coefficient"] >= 0
        assert r["step"] == sender_step[r["sender_member"]]
        hops.setdefault((r["origin_member"], r["step"], r["sender_member"]), []).append(r)
    # T5: per (origin, step, sender) receiver coefficients sum exactly to the pool.
    for hop in hops.values():
        assert len({r["pool_coefficient"] for r in hop}) == 1
        assert sum(r["coefficient"] for r in hop) == hop[0]["pool_coefficient"]
    for origin in {r["origin_member"] for r in rows}:
        mine = [r for r in rows if r["origin_member"] == origin]
        own = [r for r in mine if r["sender_member"] == origin]
        assert own and all(r["pool_coefficient"] == 1 for r in own)
        # An intermediate sender passes on exactly what it received from this origin.
        for (o, st, snd), hop in hops.items():
            if o == origin and snd != origin:
                got = sum(r["coefficient"] for r in mine if r["receiver_member"] == snd)
                assert got == hop[0]["pool_coefficient"]
                assert all(r["step"] < st for r in mine if r["receiver_member"] == snd)
        # T1: the origin's unit ends wholly at non-senders.
        assert sum(r["coefficient"] for r in mine if r["receiver_member"] not in senders) == 1
        # Nothing flows back to a sender after its own step.
        for r in mine:
            if r["receiver_member"] in senders:
                assert sender_step[r["receiver_member"]] > r["step"]
    # Rounding stays within one unit per hop of the exact flow.
    bound = len(hops) * _UNIT
    for r, x in zip(rows, exact):
        assert abs(Fraction(r["coefficient"]) - x["coefficient"]) <= bound
        assert abs(Fraction(r["pool_coefficient"]) - x["pool_coefficient"]) <= bound
    # Deterministic, independent of receiver declaration order.
    shuffled = [M.Step(s.step, s.sender_member, s.driver, list(reversed(s.receivers)))
                for s in reversed(steps)]
    again = M.coefficient_rows("ZZRUN", shuffled, w, method)
    assert sorted(map(_key, again)) == sorted(map(_key, rows))
    by = {_key(r): r for r in rows}
    assert all(by[_key(r)] == r for r in again)


def _random_case(rng, method):
    n_send = rng.randint(1, 5)
    finals = ["F%d" % i for i in range(rng.randint(1, 6))]
    senders = ["S%d" % i for i in range(n_send)]
    steps, w = [], {}
    for i, snd in enumerate(senders):
        pool = list(finals) + (senders[i + 1:] if method == "Step-Down" else [])
        recv = rng.sample(pool, rng.randint(1, len(pool)))
        vals = [rng.choice([0, 1, 2, 3, 7, 11, 13, 0.1, 2.5]) for _ in recv]
        if not any(vals):
            vals[0] = 3
        tot = sum(Fraction(str(v)) for v in vals)
        for m, v in zip(recv, vals):
            w[(i + 1, m)] = Fraction(str(v)) / tot
        steps.append(M.Step(i + 1, snd, "D%d" % (i % 2), recv))
    return steps, w


def test_coefficients_property_step_down_random():
    for seed in range(300):
        steps, w = _random_case(random.Random(seed), "Step-Down")
        _check_invariants(steps, w, "Step-Down")


def test_coefficients_property_direct_random():
    for seed in range(300):
        steps, w = _random_case(random.Random(10_000 + seed), "Direct")
        _check_invariants(steps, w, "Direct")


def test_coefficients_property_long_chain_of_thirds_and_sevenths():
    # Each sender passes everything on in thirds or sevenths to the next sender
    # and finals; nothing terminates in decimal and the error has to stay put.
    for seed in range(20):
        rng = random.Random(500 + seed)
        n = 8
        steps, w = [], {}
        for i in range(n):
            recv = (["S%d" % (i + 1)] if i + 1 < n else []) + ["F0", "F1"]
            if rng.random() < 0.5:
                recv.append("F2")
            d = rng.choice([3, 7])
            vals = [rng.randint(1, d) for _ in recv]
            tot = sum(vals)
            for m, v in zip(recv, vals):
                w[(i + 1, m)] = Fraction(v, tot)
            steps.append(M.Step(i + 1, "S%d" % i, "D", recv))
        _check_invariants(steps, w, "Step-Down")


def test_coefficients_weights_not_summing_to_one_refused():
    steps = [M.Step(1, "IT", "H", ["P1", "P2"])]
    w = {(1, "P1"): Fraction(1, 3), (1, "P2"): Fraction(1, 3)}
    with raises(M.CostAllocationError) as ei:
        M.coefficient_rows("ZZRUN", steps, w, "Direct")
    assert "step 1" in ei.value.errors[0]


# -- K5b: statistic CSV parser -------------------------------------------------

def _csv_errors(text):
    with raises(M.CostAllocationError) as box:
        M.parse_statistic_csv(text)
    return box.value.errors


def test_parse_csv_good_rows_keep_exact_decimal_text():
    from decimal import Decimal
    rows = M.parse_statistic_csv("fiscal_period,member,value\n1,ZZIT,10.10\n2,ZZHR,0\n")
    assert rows == [
        {"fiscal_period": 1, "member": "ZZIT", "value": Decimal("10.10")},
        {"fiscal_period": 2, "member": "ZZHR", "value": Decimal("0")},
    ]
    assert str(rows[0]["value"]) == "10.10"


def test_parse_csv_header_trimmed_case_insensitive_bom_and_crlf():
    text = "﻿ Fiscal_Period , MEMBER ,Value\r\n3, ZZIT ,1.5\r\n"
    rows = M.parse_statistic_csv(text)
    assert [(r["fiscal_period"], r["member"]) for r in rows] == [(3, "ZZIT")]


def test_parse_csv_header_must_be_exact():
    for head in ("period,member,value", "fiscal_period,member", "fiscal_period,member,value,extra",
                 "member,fiscal_period,value"):
        errs = _csv_errors(head + "\n1,ZZIT,1\n")
        assert any("header" in e.lower() for e in errs), (head, errs)


def test_parse_csv_empty_file_and_header_only_are_refused():
    assert any("empty" in e.lower() for e in _csv_errors(""))
    assert any("empty" in e.lower() for e in _csv_errors("﻿\r\n"))
    assert any("no data rows" in e.lower() for e in _csv_errors("fiscal_period,member,value\n"))


def test_parse_csv_reports_every_bad_row_at_once_with_its_number():
    text = ("fiscal_period,member,value\n"
            "1,ZZIT,10\n"        # row 2 fine
            "x,ZZIT,1\n"         # row 3 period
            "2, ,1\n"            # row 4 member
            "2,ZZHR,abc\n"       # row 5 value
            "2,ZZHR,-1\n"        # row 6 negative
            "2,ZZHR,1e3\n"       # row 7 exponent
            "2,ZZHR\n")          # row 8 short
    errs = _csv_errors(text)
    for n in range(3, 9):
        assert any(("row %d" % n) in e.lower() for e in errs), (n, errs)
    assert not any("row 2" in e.lower() for e in errs)
    assert len(errs) == 6


def test_parse_csv_period_must_be_an_integer():
    errs = _csv_errors("fiscal_period,member,value\n1.5,ZZIT,1\n")
    assert any("fiscal_period" in e for e in errs)


def test_parse_csv_row_cap_is_20000():
    head = "fiscal_period,member,value\n"
    ok = head + "".join("1,M%d,1\n" % i for i in range(20000))
    assert len(M.parse_statistic_csv(ok)) == 20000
    errs = _csv_errors(ok + "1,MX,1\n")
    assert any("20,000" in e for e in errs)


def test_parse_csv_skips_blank_lines_but_counts_them_in_row_numbers():
    errs = _csv_errors("fiscal_period,member,value\n\n1,ZZIT,bad\n")
    assert any("row 3" in e.lower() for e in errs)


# --- KF1 finding 9: a step fits the warehouse UInt16 column ---------------------

def test_validation_step_below_one_is_refused():
    rs = _rs(rules=[{"step": 0, "sender_member": "HR", "driver": "Headcount"},
                    {"step": 2, "sender_member": "IT", "driver": "Headcount"}])
    assert _has(_errors(rs), "between 1 and 65535")


def test_validation_step_above_65535_is_refused_and_65535_is_allowed():
    rs = _rs(rules=[{"step": 1, "sender_member": "HR", "driver": "Headcount"},
                    {"step": 65536, "sender_member": "IT", "driver": "Headcount"}])
    assert _has(_errors(rs), "between 1 and 65535")
    rs = _rs(rules=[{"step": 1, "sender_member": "HR", "driver": "Headcount"},
                    {"step": 65535, "sender_member": "IT", "driver": "Headcount"}],
             receivers=[{"step": 1, "receiver_member": "IT"}, {"step": 1, "receiver_member": "Sales"},
                        {"step": 65535, "receiver_member": "Sales"},
                        {"step": 65535, "receiver_member": "Ops"}])
    assert not _has(_errors(rs), "between 1 and 65535")


# --- RC1: Reciprocal, exact solve, hop_kind (konsol#269 phase 2a) -------------

def _recip_rs(**kw):
    base = dict(
        method="Reciprocal",
        rules=[{"step": 1, "sender_member": "IT", "driver": "Headcount"},
               {"step": 2, "sender_member": "HR", "driver": "Headcount"}],
        receivers=[{"step": 1, "receiver_member": "HR"}, {"step": 1, "receiver_member": "Sales"},
                   {"step": 2, "receiver_member": "IT"}, {"step": 2, "receiver_member": "Ops"}],
    )
    base.update(kw)
    return _rs(**base)


def test_reciprocal_is_a_method():
    assert M.RECIPROCAL == "Reciprocal"
    assert M.RECIPROCAL in M.METHODS
    assert M.DIRECT in M.METHODS and M.STEP_DOWN in M.METHODS


def test_reciprocal_validation_receivers_may_be_senders_either_way():
    # IT (step 1) sends to HR and HR (step 2) sends back to IT: allowed.
    assert _errors(_recip_rs()) == []


def test_reciprocal_validation_self_receive_refused():
    rs = _recip_rs(receivers=_recip_rs().receivers + [{"step": 2, "receiver_member": "HR"}])
    assert _has(_errors(rs), "HR cannot receive its own")


def test_reciprocal_validation_steps_still_unique():
    rs = _recip_rs(rules=[{"step": 1, "sender_member": "IT", "driver": "Headcount"},
                          {"step": 1, "sender_member": "HR", "driver": "Headcount"}])
    assert _has(_errors(rs), "Step 1 is declared more than once")


def test_reciprocal_validation_does_not_relax_direct_or_step_down():
    assert _has(_errors(_recip_rs(method="Step-Down")), "Step-Down cost only flows to later senders")
    assert _has(_errors(_recip_rs(method="Direct")), "is a receiver and a sender")


def _worked_example():
    # IT 1,000 by tickets HR 5, Sales 3, Ops 2; HR 400 by headcount IT 4, Sales 4, Ops 2.
    steps = [M.Step(1, "IT", "Tickets", ["HR", "Sales", "Ops"]),
             M.Step(2, "HR", "Headcount", ["IT", "Sales", "Ops"])]
    w = M.weights([
        {"step": 1, "receiver_member": "HR", "driver_value": 5},
        {"step": 1, "receiver_member": "Sales", "driver_value": 3},
        {"step": 1, "receiver_member": "Ops", "driver_value": 2},
        {"step": 2, "receiver_member": "IT", "driver_value": 4},
        {"step": 2, "receiver_member": "Sales", "driver_value": 4},
        {"step": 2, "receiver_member": "Ops", "driver_value": 2},
    ])
    return steps, w


def _rkey(r):
    return (r["origin_member"], r["sender_member"], r["hop_kind"], r["receiver_member"])


def test_reciprocal_worked_example_exact():
    steps, w = _worked_example()
    rows = M.reciprocal_coefficients(steps, w)
    by = {_rkey(r): r for r in rows}
    assert len(by) == len(rows)
    a, b = w[(1, "HR")], w[(2, "IT")]          # IT->HR 1/2, HR->IT 2/5
    assert (a, b) == (Fraction(1, 2), Fraction(2, 5))
    # Origin IT: x_IT = 1 + b x_HR, x_HR = a x_IT  =>  x_IT = 1 / (1 - ab)
    xi_it = 1 / (1 - a * b)
    xi_hr = a * xi_it
    # Origin HR: x_HR = 1 + a x_IT, x_IT = b x_HR  =>  x_HR = 1 / (1 - ab)
    xh_hr = 1 / (1 - a * b)
    xh_it = b * xh_hr
    assert (xi_it, xi_hr, xh_hr, xh_it) == (Fraction(5, 4), Fraction(5, 8), Fraction(5, 4), Fraction(1, 2))

    def pool(o, s, k):
        ps = {r["pool_coefficient"] for r in rows
              if (r["origin_member"], r["sender_member"], r["hop_kind"]) == (o, s, k)}
        assert len(ps) == 1, (o, s, k, ps)
        return ps.pop()

    assert pool("IT", "IT", "own") == 1
    assert pool("IT", "IT", "pass") == xi_it - 1
    assert pool("IT", "HR", "pass") == xi_hr
    assert pool("HR", "HR", "own") == 1
    assert pool("HR", "HR", "pass") == xh_hr - 1
    assert pool("HR", "IT", "pass") == xh_it
    assert not any(r["hop_kind"] == "own" and r["sender_member"] != r["origin_member"] for r in rows)
    for r in rows:
        assert r["coefficient"] == r["pool_coefficient"] * w[(r["step"], r["receiver_member"])]
        assert r["step"] == {"IT": 1, "HR": 2}[r["sender_member"]]
        assert r["driver"] == {"IT": "Tickets", "HR": "Headcount"}[r["sender_member"]]
        assert isinstance(r["coefficient"], Fraction)
    # 12 rows: 2 origins x (own + pass at the origin + pass at the other) x 3 receivers.
    assert len(rows) == 12

    amount = {"IT": Fraction(1000), "HR": Fraction(400)}
    sent = {}
    got = {}
    for r in rows:
        x = amount[r["origin_member"]] * r["coefficient"]
        sent[r["sender_member"]] = sent.get(r["sender_member"], 0) + x
        got[r["receiver_member"]] = got.get(r["receiver_member"], 0) + x
    assert sent == {"IT": 1450, "HR": 1125}          # T_IT, T_HR
    assert got["Sales"] == 885 and got["Ops"] == 515
    assert got["HR"] == 725 and got["IT"] == 450
    # Gross lines of the plan.
    line = lambda s, r: sum(amount[x["origin_member"]] * x["coefficient"] for x in rows
                            if x["sender_member"] == s and x["receiver_member"] == r)
    assert [line("IT", m) for m in ("HR", "Sales", "Ops")] == [725, 435, 290]
    assert [line("HR", m) for m in ("IT", "Sales", "Ops")] == [450, 450, 225]
    assert got["Sales"] + got["Ops"] == 1400


def test_reciprocal_closed_loop_refused_naming_both():
    steps = [M.Step(1, "A", "D", ["B"]), M.Step(2, "B", "D", ["A"])]
    w = {(1, "B"): Fraction(1), (2, "A"): Fraction(1)}
    with raises(M.CostAllocationError) as ei:
        M.reciprocal_coefficients(steps, w)
    msg = str(ei.value)
    assert "A" in msg and "B" in msg and "final receiver" in msg


def test_reciprocal_closed_loop_with_unrelated_exit_names_only_the_loop():
    # C exits to F; A <-> B never exit.
    steps = [M.Step(1, "A", "D", ["B"]), M.Step(2, "B", "D", ["A"]), M.Step(3, "C", "D", ["A", "F"])]
    w = {(1, "B"): Fraction(1), (2, "A"): Fraction(1),
         (3, "A"): Fraction(1, 2), (3, "F"): Fraction(1, 2)}
    with raises(M.CostAllocationError) as ei:
        M.reciprocal_coefficients(steps, w)
    msg = str(ei.value)
    assert "A, B" in msg and "C" not in msg.split(":")[-1]


def test_reciprocal_zero_weight_exit_is_no_exit():
    # B's only route to a final receiver has weight 0.
    steps = [M.Step(1, "A", "D", ["B", "F"]), M.Step(2, "B", "D", ["A", "F"])]
    w = {(1, "B"): Fraction(1, 2), (1, "F"): Fraction(1, 2),
         (2, "A"): Fraction(1), (2, "F"): Fraction(0)}
    rows = M.reciprocal_coefficients(steps, w)   # B exits through A: accepted
    _check_reciprocal(steps, w, rows)
    w2 = dict(w, **{(1, "B"): Fraction(1), (1, "F"): Fraction(0)})
    with raises(M.CostAllocationError) as ei:
        M.reciprocal_coefficients(steps, w2)
    assert "A, B" in str(ei.value)


def test_reciprocal_only_exit_through_another_department_accepted():
    # A sends only to B; B sends to A and to the final receiver F.
    steps = [M.Step(1, "A", "D", ["B"]), M.Step(2, "B", "D", ["A", "F"])]
    w = {(1, "B"): Fraction(1), (2, "A"): Fraction(1, 3), (2, "F"): Fraction(2, 3)}
    rows = M.reciprocal_coefficients(steps, w)
    _check_reciprocal(steps, w, rows)
    # Origin A: x_A = 1 + x_B/3, x_B = x_A  =>  x_A = x_B = 3/2.
    pools = {(r["origin_member"], r["sender_member"], r["hop_kind"]): r["pool_coefficient"] for r in rows}
    assert pools[("A", "A", "own")] == 1
    assert pools[("A", "A", "pass")] == Fraction(1, 2)
    assert pools[("A", "B", "pass")] == Fraction(3, 2)


def test_reciprocal_self_receive_refused_in_solve():
    steps = [M.Step(1, "A", "D", ["A", "F"])]
    w = {(1, "A"): Fraction(1, 2), (1, "F"): Fraction(1, 2)}
    with raises(M.CostAllocationError) as ei:
        M.reciprocal_coefficients(steps, w)
    assert "A cannot receive its own" in str(ei.value)


def test_reciprocal_no_pass_row_when_nothing_comes_back():
    # Reciprocal with no service-to-service flow equals Direct, all own.
    steps = [M.Step(1, "A", "D", ["F1", "F2"]), M.Step(2, "B", "D", ["F2"])]
    w = {(1, "F1"): Fraction(1, 3), (1, "F2"): Fraction(2, 3), (2, "F2"): Fraction(1)}
    rows = M.reciprocal_coefficients(steps, w)
    assert all(r["hop_kind"] == "own" for r in rows)
    strip = lambda rs: sorted((_key(r), r["pool_coefficient"], r["coefficient"]) for r in rs)
    assert strip(rows) == strip(M.direct_coefficients(steps, w))


def test_reciprocal_coefficient_rows_not_rounded_until_rc2():
    steps, w = _worked_example()
    with raises(NotImplementedError) as ei:
        M.coefficient_rows("ZZRUN", steps, w, "Reciprocal")
    assert "RC2" in str(ei.value)


def test_reciprocal_hop_kind_direct_all_own():
    steps = [M.Step(1, "IT", "H", ["P1", "P2"]), M.Step(2, "HR", "H", ["P1"])]
    w = {(1, "P1"): Fraction(1, 3), (1, "P2"): Fraction(2, 3), (2, "P1"): Fraction(1)}
    rows = M.direct_coefficients(steps, w)
    assert rows and all(r["hop_kind"] == "own" for r in rows)


def test_reciprocal_hop_kind_step_down_own_then_pass_numbers_unchanged():
    steps, w = _hand_steps()
    rows = M.step_down_coefficients(steps, w)
    for r in rows:
        assert r["hop_kind"] == ("own" if r["sender_member"] == r["origin_member"] else "pass")
    assert {r["hop_kind"] for r in rows} == {"own", "pass"}
    by = {_key(r): r for r in rows}
    assert by[("IT", 2, "HR", "P3")]["coefficient"] == Fraction(1, 15)
    assert by[("IT", 2, "HR", "P3")]["pool_coefficient"] == Fraction(1, 5)
    # Rounded rows keep their numbers; the staging columns are unchanged until RC2/RC4.
    rounded = {_key(r): r for r in M.coefficient_rows("ZZRUN-1", steps, w, "Step-Down")}
    assert rounded[("IT", 2, "HR", "P3")]["coefficient"] == Decimal("0.066666666666")
    rr = M.round_coefficients(rows, w)
    assert [r["hop_kind"] for r in rr] == [r["hop_kind"] for r in rows]


def _check_reciprocal(steps, w, rows):
    """Every RC1 invariant, exactly, for every origin."""
    senders = {s.sender_member for s in steps}
    step_of = {s.sender_member: s for s in steps}
    origins = sorted({r["origin_member"] for r in rows})
    assert origins == sorted(senders)
    hops = {}
    for r in rows:
        assert r["hop_kind"] in ("own", "pass")
        assert r["pool_coefficient"] > 0
        assert r["coefficient"] >= 0
        st = step_of[r["sender_member"]]
        assert (r["step"], r["driver"]) == (st.step, st.driver)
        assert r["coefficient"] == r["pool_coefficient"] * w[(st.step, r["receiver_member"])]
        if r["hop_kind"] == "own":
            assert r["sender_member"] == r["origin_member"]
        hops.setdefault((r["origin_member"], r["sender_member"], r["hop_kind"]), []).append(r)
    for (o, s, k), hop in hops.items():
        assert len({r["pool_coefficient"] for r in hop}) == 1
        assert sorted(r["receiver_member"] for r in hop) == sorted(step_of[s].receivers)
        assert sum(r["coefficient"] for r in hop) == hop[0]["pool_coefficient"]
    for o in origins:
        mine = [r for r in rows if r["origin_member"] == o]
        own = {(r["sender_member"], r["receiver_member"]) for r in mine if r["hop_kind"] == "own"}
        assert own == {(o, m) for m in step_of[o].receivers}
        assert hops[(o, o, "own")][0]["pool_coefficient"] == 1
        for s in senders:
            outflow = sum(r["coefficient"] for r in mine if r["sender_member"] == s)
            inflow = sum(r["coefficient"] for r in mine if r["receiver_member"] == s)
            assert outflow == inflow + (1 if s == o else 0), (o, s)
            out_pool = sum(h[0]["pool_coefficient"] for (oo, ss, _), h in hops.items()
                           if (oo, ss) == (o, s))
            assert out_pool == outflow
        assert sum(r["coefficient"] for r in mine if r["receiver_member"] not in senders) == 1


def _exits(steps, w):
    """Senders with a positive-weight path to a non-sender (independent of the model)."""
    senders = {s.sender_member for s in steps}
    ok, changed = set(), True
    while changed:
        changed = False
        for st in steps:
            if st.sender_member in ok:
                continue
            if any(w[(st.step, r)] > 0 and (r not in senders or r in ok) for r in st.receivers):
                ok.add(st.sender_member)
                changed = True
    return ok


def _random_reciprocal(rng):
    senders = ["S%d" % i for i in range(rng.randint(1, 6))]
    finals = ["F%d" % i for i in range(rng.randint(1, 4))]
    steps, w = [], {}
    numbers = rng.sample(range(1, 200), len(senders))
    for snd, n in zip(senders, numbers):
        pool = [m for m in senders + finals if m != snd]
        if rng.random() < 0.3:
            pool = [m for m in senders if m != snd] or pool  # no direct exit
        recv = rng.sample(pool, rng.randint(1, len(pool)))
        vals = [rng.choice([0, 1, 2, 3, 7, 11, 0.1, 2.5]) for _ in recv]
        if not any(vals):
            vals[0] = 1
        tot = sum(Fraction(str(v)) for v in vals)
        for m, v in zip(recv, vals):
            w[(n, m)] = Fraction(str(v)) / tot
        steps.append(M.Step(n, snd, "D%d" % (n % 3), recv))
    return steps, w


def test_reciprocal_property_random_graphs():
    accepted = refused = 0
    for seed in range(400):
        steps, w = _random_reciprocal(random.Random(20_000 + seed))
        senders = {s.sender_member for s in steps}
        stuck = sorted(senders - _exits(steps, w))
        if stuck:
            with raises(M.CostAllocationError) as ei:
                M.reciprocal_coefficients(steps, w)
            assert ", ".join(stuck) in str(ei.value), (seed, stuck, str(ei.value))
            refused += 1
            continue
        rows = M.reciprocal_coefficients(steps, w)
        _check_reciprocal(steps, w, rows)
        # The pools solve the defining system exactly.
        for o in senders:
            pools = {(r["sender_member"], r["hop_kind"]): r["pool_coefficient"]
                     for r in rows if r["origin_member"] == o}
            x = {s: pools.get((s, "own"), 0) + pools.get((s, "pass"), 0) for s in senders}
            for s in senders:
                back = sum(x[st.sender_member] * w[(st.step, s)] for st in steps if s in st.receivers)
                assert x[s] == (1 if s == o else 0) + back, (seed, o, s)
        # Deterministic: independent of step and receiver declaration order.
        shuffled = [M.Step(s.step, s.sender_member, s.driver, list(reversed(s.receivers)))
                    for s in reversed(steps)]
        again = M.reciprocal_coefficients(shuffled, w)
        assert sorted(map(_rkey, again)) == sorted(map(_rkey, rows))
        assert {_rkey(r): r for r in again} == {_rkey(r): r for r in rows}
        accepted += 1
    assert accepted > 100 and refused > 20, (accepted, refused)


def test_reciprocal_property_amounts_satisfy_totals_equation():
    # T_s = own_s + sum_t w(t->s) T_t, with T_s = sum_o amount_o * x_s^o.
    for seed in range(100):
        rng = random.Random(30_000 + seed)
        steps, w = _random_reciprocal(rng)
        senders = sorted({s.sender_member for s in steps})
        if senders != sorted(_exits(steps, w)):
            continue
        amount = {s: Fraction(rng.randint(0, 10 ** 6), 100) for s in senders}
        rows = M.reciprocal_coefficients(steps, w)
        T = {s: sum(amount[r["origin_member"]] * r["coefficient"] for r in rows
                    if r["sender_member"] == s) for s in senders}
        for s in senders:
            back = sum(w[(st.step, s)] * T[st.sender_member] for st in steps if s in st.receivers)
            assert T[s] == amount[s] + back
        finals = sum(amount[r["origin_member"]] * r["coefficient"] for r in rows
                     if r["receiver_member"] not in senders)
        assert finals == sum(amount.values())
