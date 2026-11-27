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
            "driver", "hop_kind", "pool_coefficient", "coefficient"]
    assert list(M.COEFFICIENT_COLUMNS) == cols
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
    # 18 rows: 2 origins x 3 hops (own, pass at the origin, pass at the other) x 3 receivers.
    assert len(rows) == 18

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
    w2 = {**w, (1, "B"): Fraction(1), (1, "F"): Fraction(0)}
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
    # Rounded rows keep their numbers and carry hop_kind (RC2).
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


# -- RC2: 12-dp rounding for Reciprocal ----------------------------------------

def _check_reciprocal_rounded(steps, w, rows):
    """Every RC2 invariant, exactly, in 12-dp Decimal; returns the rows by key."""
    exact = M.reciprocal_coefficients(steps, w)
    assert [_rkey(r) for r in rows] == [_rkey(r) for r in exact]
    senders = {s.sender_member for s in steps}
    step_of = {s.sender_member: s for s in steps}
    hops = {}
    for r, x in zip(rows, exact):
        assert list(r) == list(M.COEFFICIENT_COLUMNS)
        assert r["run"] == "ZZRUN"
        for c in ("origin_member", "step", "sender_member", "receiver_member", "driver", "hop_kind"):
            assert r[c] == x[c], c
        for c in ("coefficient", "pool_coefficient"):
            assert isinstance(r[c], Decimal) and r[c].as_tuple().exponent == -12, c
        assert r["coefficient"] >= 0 and r["pool_coefficient"] >= 0
        # Bound: every coefficient is the exact value rounded down or up, so
        # strictly within one unit of 1e-12.
        assert abs(Fraction(r["coefficient"]) - x["coefficient"]) < _UNIT, (_rkey(r), r, x)
        hops.setdefault((r["origin_member"], r["sender_member"], r["hop_kind"]), []).append((r, x))
    # 2. per hop: one pool, and the coefficients sum exactly to it.
    for (o, s, k), hop in hops.items():
        pools = {r["pool_coefficient"] for r, _ in hop}
        assert len(pools) == 1, (o, s, k, pools)
        assert sum(r["coefficient"] for r, _ in hop) == hop[0][0]["pool_coefficient"]
        # A pool is a sum of its receivers' coefficients: within (receivers) units.
        assert abs(Fraction(hop[0][0]["pool_coefficient"]) - hop[0][1]["pool_coefficient"]) \
            < len(step_of[s].receivers) * _UNIT
    for o in sorted(senders):
        mine = [r for r in rows if r["origin_member"] == o]
        # 1. one own hop, at O, pool exactly 1.
        own = [k for k in hops if k[0] == o and k[2] == "own"]
        assert own == [(o, o, "own")]
        assert hops[(o, o, "own")][0][0]["pool_coefficient"] == Decimal(1)
        # 3. every sender passes on exactly what it received (+1 at the origin).
        for s in senders:
            outflow = sum((r["coefficient"] for r in mine if r["sender_member"] == s), Decimal(0))
            inflow = sum((r["coefficient"] for r in mine if r["receiver_member"] == s), Decimal(0))
            assert outflow == inflow + (1 if s == o else 0), (o, s, outflow, inflow)
            pools = sum((h[0][0]["pool_coefficient"] for (oo, ss, _), h in hops.items()
                         if (oo, ss) == (o, s)), Decimal(0))
            assert pools == outflow
        # 4. final receivers get exactly 1.
        assert sum(r["coefficient"] for r in mine if r["receiver_member"] not in senders) == 1
    return {_rkey(r): r for r in rows}


def test_reciprocal_rounding_worked_example_equals_exact():
    steps, w = _worked_example()
    rows = M.coefficient_rows("ZZRUN", steps, w, "Reciprocal")
    by = _check_reciprocal_rounded(steps, w, rows)
    exact = M.reciprocal_coefficients(steps, w)
    assert len(rows) == 18
    for x in exact:
        r = by[_rkey(x)]
        # The worked example terminates in decimal: rounding changes nothing.
        assert Fraction(r["coefficient"]) == x["coefficient"]
        assert Fraction(r["pool_coefficient"]) == x["pool_coefficient"]
    assert by[("IT", "IT", "pass", "HR")]["pool_coefficient"] == Decimal("0.250000000000")
    assert by[("IT", "HR", "pass", "Sales")]["coefficient"] == Decimal("0.250000000000")
    amount = {"IT": 1000, "HR": 400}
    got = {}
    for r in rows:
        got[r["receiver_member"]] = got.get(r["receiver_member"], 0) + amount[r["origin_member"]] * r["coefficient"]
    assert (got["Sales"], got["Ops"]) == (885, 515)


def test_reciprocal_rounding_cycle_of_thirds_and_sevenths():
    # A -> B 1/3, F1 2/3; B -> A 1/7, C 2/7, F2 4/7; C -> A 1/3, B 1/3, F1 1/3.
    steps = [M.Step(1, "A", "D", ["B", "F1"]), M.Step(2, "B", "D", ["A", "C", "F2"]),
             M.Step(3, "C", "D", ["A", "B", "F1"])]
    w = {(1, "B"): Fraction(1, 3), (1, "F1"): Fraction(2, 3),
         (2, "A"): Fraction(1, 7), (2, "C"): Fraction(2, 7), (2, "F2"): Fraction(4, 7),
         (3, "A"): Fraction(1, 3), (3, "B"): Fraction(1, 3), (3, "F1"): Fraction(1, 3)}
    rows = M.coefficient_rows("ZZRUN", steps, w, "Reciprocal")
    by = _check_reciprocal_rounded(steps, w, rows)
    exact = {_rkey(r): r for r in M.reciprocal_coefficients(steps, w)}
    # Nothing terminates, so rounding must actually happen somewhere.
    assert any(Fraction(r["coefficient"]) != exact[k]["coefficient"] for k, r in by.items())


def test_reciprocal_rounding_sender_exits_only_through_other_senders():
    # A sends only to service departments (B, C); B and C exit to F.
    steps = [M.Step(1, "A", "D", ["B", "C"]), M.Step(2, "B", "D", ["A", "F"]),
             M.Step(3, "C", "D", ["B", "F"])]
    w = {(1, "B"): Fraction(1, 3), (1, "C"): Fraction(2, 3),
         (2, "A"): Fraction(1, 7), (2, "F"): Fraction(6, 7),
         (3, "B"): Fraction(1, 3), (3, "F"): Fraction(2, 3)}
    rows = M.coefficient_rows("ZZRUN", steps, w, "Reciprocal")
    by = _check_reciprocal_rounded(steps, w, rows)
    # A has no final receiver, yet it nets to exactly zero for every origin.
    for o in ("A", "B", "C"):
        out = sum(r["coefficient"] for k, r in by.items() if k[0] == o and k[1] == "A")
        inn = sum(r["coefficient"] for k, r in by.items() if k[0] == o and k[3] == "A")
        assert out - inn == (1 if o == "A" else 0)
    # The simplest case: A -> B only; B -> A 1/3, F 2/3.
    steps2 = [M.Step(1, "A", "D", ["B"]), M.Step(2, "B", "D", ["A", "F"])]
    w2 = {(1, "B"): Fraction(1), (2, "A"): Fraction(1, 3), (2, "F"): Fraction(2, 3)}
    by2 = _check_reciprocal_rounded(steps2, w2, M.coefficient_rows("ZZRUN", steps2, w2, "Reciprocal"))
    assert by2[("A", "A", "own", "B")]["coefficient"] == 1
    assert by2[("A", "B", "pass", "F")]["coefficient"] == 1


def test_reciprocal_rounding_zero_weight_receivers_keep_rows():
    steps = [M.Step(1, "A", "D", ["B", "F", "G"]), M.Step(2, "B", "D", ["A", "F"])]
    w = {(1, "B"): Fraction(1, 3), (1, "F"): Fraction(2, 3), (1, "G"): Fraction(0),
         (2, "A"): Fraction(1, 7), (2, "F"): Fraction(6, 7)}
    by = _check_reciprocal_rounded(steps, w, M.coefficient_rows("ZZRUN", steps, w, "Reciprocal"))
    assert by[("A", "A", "own", "G")]["coefficient"] == 0
    assert by[("B", "A", "pass", "G")]["coefficient"] == 0


def _renumber(steps, w, rng):
    """The same rule set declared in another order, with other step numbers."""
    nums = rng.sample(range(1, 500), len(steps))
    remap = {s.step: n for s, n in zip(steps, nums)}
    new = [M.Step(remap[s.step], s.sender_member, s.driver, rng.sample(s.receivers, len(s.receivers)))
           for s in steps]
    rng.shuffle(new)
    return new, {(remap[st], m): v for (st, m), v in w.items()}


def test_reciprocal_rounding_property_random_graphs():
    checked = rounded = 0
    for seed in range(300):
        rng = random.Random(40_000 + seed)
        steps, w = _random_reciprocal(rng)
        senders = {s.sender_member for s in steps}
        if senders != _exits(steps, w):
            continue
        rows = M.coefficient_rows("ZZRUN", steps, w, "Reciprocal")
        by = _check_reciprocal_rounded(steps, w, rows)
        exact = {_rkey(r): r["coefficient"] for r in M.reciprocal_coefficients(steps, w)}
        rounded += any(Fraction(r["coefficient"]) != exact[k] for k, r in by.items())
        # Deterministic, and independent of declaration order and step numbers.
        steps2, w2 = _renumber(steps, w, rng)
        again = M.coefficient_rows("ZZRUN", steps2, w2, "Reciprocal")
        strip = lambda rs: {_rkey(r): {c: v for c, v in r.items() if c != "step"} for r in rs}
        assert strip(again) == strip(rows), seed
        assert M.coefficient_rows("ZZRUN", steps, w, "Reciprocal") == rows
        checked += 1
    assert checked > 100 and rounded > 30, (checked, rounded)


def test_reciprocal_rounding_property_amounts_conserve_to_the_cent():
    for seed in range(60):
        rng = random.Random(50_000 + seed)
        steps, w = _random_reciprocal(rng)
        senders = {s.sender_member for s in steps}
        if senders != _exits(steps, w):
            continue
        amount = {s: Decimal(rng.randint(0, 10 ** 9)) / 100 for s in senders}
        rows = M.coefficient_rows("ZZRUN", steps, w, "Reciprocal")
        net = {}
        for r in rows:
            x = amount[r["origin_member"]] * r["coefficient"]
            net[r["sender_member"]] = net.get(r["sender_member"], 0) - x
            net[r["receiver_member"]] = net.get(r["receiver_member"], 0) + x
        for s in senders:
            assert net.get(s, 0) == -amount[s], (seed, s)
        assert sum(v for m, v in net.items() if m not in senders) == sum(amount.values())


def test_reciprocal_rounding_hop_kind_column_for_every_method():
    steps, w = _hand_steps()
    for method in ("Direct", "Step-Down", "Reciprocal"):
        if method == "Direct":
            steps_m = [M.Step(1, "IT", "H", ["P1", "P2"])]
            w_m = {(1, "P1"): Fraction(1, 3), (1, "P2"): Fraction(2, 3)}
        else:
            steps_m, w_m = steps, w
        rows = M.coefficient_rows("ZZRUN", steps_m, w_m, method)
        assert rows and all(list(r) == list(M.COEFFICIENT_COLUMNS) for r in rows)
        assert M.COEFFICIENT_COLUMNS.index("hop_kind") == M.COEFFICIENT_COLUMNS.index("driver") + 1
        assert M.COEFFICIENT_COLUMNS[M.COEFFICIENT_COLUMNS.index("hop_kind") + 1] == "pool_coefficient"
        assert {r["hop_kind"] for r in rows} <= {"own", "pass"}


#: Direct and Step-Down coefficients as origin/main rounded them before
#: Reciprocal existed (konsol#269 review finding 4), for _random_case seeds:
#: (origin, step, sender, receiver, pool_coefficient, coefficient).
_MAIN_ROUNDED = [
    ('Step-Down', 41, [
        ('S0', 1, 'S0', 'F1', '1.000000000000', '0.736842105263'),
        ('S0', 1, 'S0', 'S1', '1.000000000000', '0.263157894737'),
        ('S0', 2, 'S1', 'S2', '0.263157894737', '0E-12'),
        ('S0', 2, 'S1', 'F0', '0.263157894737', '0.012531328321'),
        ('S0', 2, 'S1', 'S3', '0.263157894737', '0.250626566416'),
        ('S0', 4, 'S3', 'F2', '0.250626566416', '0.250626566416'),
        ('S1', 2, 'S1', 'S2', '1.000000000000', '0E-12'),
        ('S1', 2, 'S1', 'F0', '1.000000000000', '0.047619047619'),
        ('S1', 2, 'S1', 'S3', '1.000000000000', '0.952380952381'),
        ('S1', 4, 'S3', 'F2', '0.952380952381', '0.952380952381'),
        ('S2', 3, 'S2', 'F2', '1.000000000000', '1.000000000000'),
        ('S2', 3, 'S2', 'F0', '1.000000000000', '0E-12'),
        ('S3', 4, 'S3', 'F2', '1.000000000000', '1.000000000000'),
    ]),
    ('Step-Down', 47, [
        ('S0', 1, 'S0', 'S2', '1.000000000000', '0.611111111111'),
        ('S0', 1, 'S0', 'S1', '1.000000000000', '0.388888888889'),
        ('S0', 2, 'S1', 'S2', '0.388888888889', '0.388888888889'),
        ('S0', 2, 'S1', 'F0', '0.388888888889', '0E-12'),
        ('S0', 3, 'S2', 'F0', '1.000000000000', '1.000000000000'),
        ('S1', 2, 'S1', 'S2', '1.000000000000', '1.000000000000'),
        ('S1', 2, 'S1', 'F0', '1.000000000000', '0E-12'),
        ('S1', 3, 'S2', 'F0', '1.000000000000', '1.000000000000'),
        ('S2', 3, 'S2', 'F0', '1.000000000000', '1.000000000000'),
    ]),
    ('Direct', 10000, [
        ('S0', 1, 'S0', 'F2', '1.000000000000', '1.000000000000'),
        ('S1', 2, 'S1', 'F0', '1.000000000000', '0.161290322581'),
        ('S1', 2, 'S1', 'F2', '1.000000000000', '0.709677419355'),
        ('S1', 2, 'S1', 'F1', '1.000000000000', '0.129032258064'),
        ('S2', 3, 'S2', 'F2', '1.000000000000', '1.000000000000'),
        ('S3', 4, 'S3', 'F1', '1.000000000000', '1.000000000000'),
        ('S4', 5, 'S4', 'F0', '1.000000000000', '0.583333333334'),
        ('S4', 5, 'S4', 'F2', '1.000000000000', '0.208333333333'),
        ('S4', 5, 'S4', 'F1', '1.000000000000', '0.208333333333'),
    ]),
    ('Direct', 10011, [
        ('S0', 1, 'S0', 'F2', '1.000000000000', '0.353697749196'),
        ('S0', 1, 'S0', 'F4', '1.000000000000', '0E-12'),
        ('S0', 1, 'S0', 'F1', '1.000000000000', '0.225080385852'),
        ('S0', 1, 'S0', 'F0', '1.000000000000', '0.003215434084'),
        ('S0', 1, 'S0', 'F3', '1.000000000000', '0.418006430868'),
        ('S1', 2, 'S1', 'F4', '1.000000000000', '0.151515151515'),
        ('S1', 2, 'S1', 'F3', '1.000000000000', '0.424242424242'),
        ('S1', 2, 'S1', 'F1', '1.000000000000', '0.424242424243'),
    ]),
]


def test_reciprocal_rounding_direct_and_step_down_unchanged_apart_from_hop_kind():
    # Pinned from origin/main's cost_allocation_model.py, not from this code.
    for method, seed, expected in _MAIN_ROUNDED:
        steps, w = _random_case(random.Random(seed), method)
        rows = M.coefficient_rows("ZZRUN", steps, w, method)
        got = [(r["origin_member"], r["step"], r["sender_member"], r["receiver_member"],
                r["pool_coefficient"], r["coefficient"]) for r in rows]
        assert got == [(o, st, s, rc, Decimal(p), Decimal(c)) for o, st, s, rc, p, c in expected], \
            (method, seed)
        for r in rows:
            assert r["hop_kind"] == ("own" if r["sender_member"] == r["origin_member"] else "pass")


def test_reciprocal_rounding_round_coefficients_refuses_reciprocal_rows():
    steps, w = _worked_example()
    with raises(ValueError) as ei:
        M.round_coefficients(M.reciprocal_coefficients(steps, w), w)
    assert "round_reciprocal_coefficients" in str(ei.value)


# -- RF3: near-linear reciprocal rounding, identical output -------------------

import math
import time
from collections import deque


def _round_circulation_reference(origin, rows, senders):
    """RC2's _round_circulation, verbatim: the reference RF3 must equal."""
    src = ("0 source", "")
    node_in = lambda m: ("2 pass", m) if m in senders else ("3 final", m)
    flow, edge_of = {(src, ("1 own", origin)): Fraction(M._SCALE)}, {}
    for r in rows:
        tail = ("1 own", origin) if r["hop_kind"] == M.OWN else ("2 pass", r["sender_member"])
        e = (tail, node_in(r["receiver_member"]))
        if e in flow:
            raise M.CostAllocationError("Duplicate coefficient row for origin %s, %s to %s."
                                        % (origin, r["sender_member"], r["receiver_member"]))
        flow[e] = Fraction(r["coefficient"]) * M._SCALE
        edge_of[id(r)] = e
        if e[1][0] == "3 final":
            back = (e[1], src)
            flow[back] = flow.get(back, Fraction(0)) + flow[e]
    frac = {e for e, v in flow.items() if v.denominator != 1}
    while frac:
        start = min(frac)
        adj = {}
        for e in sorted(frac - {start}):
            adj.setdefault(e[0], []).append((e[1], e, 1))
            adj.setdefault(e[1], []).append((e[0], e, -1))
        goal, prev, queue = start[0], {start[1]: None}, deque([start[1]])
        while queue and goal not in prev:
            u = queue.popleft()
            for v, e, d in adj.get(u, ()):
                if v not in prev:
                    prev[v] = (u, e, d)
                    queue.append(v)
        if goal not in prev:
            raise M.CostAllocationError("Reciprocal rounding found unbalanced coefficients "
                                        "for origin %s." % origin)
        cycle, v = [(start, 1)], goal
        while prev[v] is not None:
            u, e, d = prev[v]
            cycle.append((e, d))
            v = u
        up = min((math.ceil(flow[e]) - flow[e]) if d > 0 else (flow[e] - math.floor(flow[e]))
                 for e, d in cycle)
        down = min((flow[e] - math.floor(flow[e])) if d > 0 else (math.ceil(flow[e]) - flow[e])
                   for e, d in cycle)
        t = up if up <= down else -down
        for e, d in cycle:
            flow[e] += t * d
            if flow[e].denominator == 1:
                frac.discard(e)
    return {rid: int(flow[e]) for rid, e in edge_of.items()}


def _dense_reciprocal(rng, n_send, n_final, density=0.3):
    """n_send senders, each to every final and to ~density of the other senders."""
    senders = ["S%02d" % i for i in range(n_send)]
    finals = ["F%03d" % i for i in range(n_final)]
    steps, w = [], {}
    for i, s in enumerate(senders):
        recv = [r for r in senders + finals if r != s and (r in finals or rng.random() < density)]
        vals = [rng.randint(1, 97) for _ in recv]
        tot = sum(vals)
        for r, v in zip(recv, vals):
            w[(10 * (i + 1), r)] = Fraction(v, tot)
        steps.append(M.Step(10 * (i + 1), s, "D", recv))
    return steps, w


def _assert_rounding_equals_reference(exact):
    senders = {r["sender_member"] for r in exact}
    by_origin = {}
    for r in exact:
        by_origin.setdefault(r["origin_member"], []).append(r)
    for o, rows in by_origin.items():
        assert M._round_circulation(o, rows, senders) == _round_circulation_reference(o, rows, senders), o


def test_reciprocal_rounding_equals_rc2_reference_random_graphs():
    checked = 0
    for seed in range(300):
        steps, w = _random_reciprocal(random.Random(40_000 + seed))
        if {s.sender_member for s in steps} != _exits(steps, w):
            continue
        _assert_rounding_equals_reference(M.reciprocal_coefficients(steps, w))
        checked += 1
    assert checked > 100, checked


def test_reciprocal_rounding_equals_rc2_reference_dense_graphs():
    # Long cycles through many pass and final nodes, where the cycle chosen matters.
    for seed in range(4):
        rng = random.Random(60_000 + seed)
        steps, w = _dense_reciprocal(rng, rng.randint(4, 8), rng.randint(10, 30), density=0.5)
        _assert_rounding_equals_reference(M.reciprocal_coefficients(steps, w))


def test_reciprocal_rounding_performance_20_senders_200_finals():
    # Performance guard (review finding 2): RC2 took ~160 s here; bound is
    # generous for CI. The exact solve is excluded; only the rounding is timed.
    steps, w = _dense_reciprocal(random.Random(3), 20, 200)
    exact = M.reciprocal_coefficients(steps, w)
    assert len(exact) > 80_000, len(exact)
    t = time.perf_counter()
    rows = M.round_reciprocal_coefficients(exact)
    took = time.perf_counter() - t
    assert len(rows) == len(exact)
    assert took < 15, "rounding %d rows took %.1f s" % (len(exact), took)


# -- 2b-2 R3: declared rates (Direct and Step-Down) ---------------------------
#
# A step has basis Share of Pool (the sender's pool split by weights) or
# Declared Rate (receivers charged rate x driver units; konsol#269, Q5 5B).
# For a rate origin, coefficients are in DRIVER UNITS: the own hop's pool is
# the step's total units and each receiver's coefficient is its units; the
# warehouse multiplies by origin_amount = the rate. A later share sender passes
# rate-origin units on by its own weights. Step-Down: a rate sender's outflow
# is fixed, so cost it receives from other origins stops there (a sink) and
# ends in its absorption variance (plan section 3, decided 27 Nov 2026).

import hashlib

_SHARE = "Share of Pool"
_RATE = "Declared Rate"


def _rate_example():
    """Plan section 3: IT rate sender (pool 1,000, 50 per ticket; tickets HR 5,
    Sales 8, Ops 6), then HR share sender (pool 400) by headcount 5:3."""
    steps = [
        M.Step(1, "IT", "Tickets", ["HR", "Sales", "Ops"], basis=_RATE, rate=Fraction(50)),
        M.Step(2, "HR", "Headcount", ["Sales", "Ops"]),
    ]
    rows = [
        {"step": 1, "receiver_member": "HR", "driver_value": 5},
        {"step": 1, "receiver_member": "Sales", "driver_value": 8},
        {"step": 1, "receiver_member": "Ops", "driver_value": 6},
        {"step": 2, "receiver_member": "Sales", "driver_value": 5},
        {"step": 2, "receiver_member": "Ops", "driver_value": 3},
    ]
    return steps, M.weights(rows), M.units(rows)


def _apply(rows, steps, pools):
    """What dbt does: x = origin_amount * coefficient, sent -x, received +x.
    origin_amount is the rate for a rate origin and the pool for a share origin."""
    by_sender = {st.sender_member: st for st in steps}
    net, sent, received = {}, {}, {}
    for r in rows:
        o = by_sender[r["origin_member"]]
        # the warehouse holds the rate as a 4-dp Decimal: exact for a declared rate
        amount = (Decimal(o.rate.numerator) / Decimal(o.rate.denominator) if o.basis == _RATE
                  else pools[o.sender_member])
        x = amount * r["coefficient"]
        sent[r["sender_member"]] = sent.get(r["sender_member"], Decimal(0)) + x
        received[r["receiver_member"]] = received.get(r["receiver_member"], Decimal(0)) + x
    for m in set(sent) | set(received):
        net[m] = received.get(m, Decimal(0)) - sent.get(m, Decimal(0))
    return net, sent, received


def test_rate_basis_constants_and_step_defaults():
    assert M.SHARE_OF_POOL == _SHARE and M.DECLARED_RATE == _RATE
    assert M.BASES == (_SHARE, _RATE)
    st = M.Step(1, "IT", "H", ["P1"])
    assert st.basis == _SHARE and st.rate is None


def test_rate_build_steps_carries_basis_and_rate_defaulting_to_share():
    steps = M.build_steps(
        [{"step": 2, "sender_member": "HR", "driver": "H"},
         {"step": 1, "sender_member": "IT", "driver": "T", "basis": _RATE, "rate": Decimal("50.25")}],
        [{"step": 1, "receiver_member": "HR"}, {"step": 2, "receiver_member": "P1"}])
    assert [(s.step, s.basis, s.rate) for s in steps] == [(1, _RATE, Fraction("50.25")), (2, _SHARE, None)]


def test_rate_units_are_exact_driver_values():
    rows = [{"step": 1, "receiver_member": "A", "driver_value": 0.1},
            {"step": 1, "receiver_member": "B", "driver_value": "2.000000001"},
            {"step": 1, "receiver_member": "C", "driver_value": 0}]
    u = M.units(rows)
    assert u == {(1, "A"): Fraction(1, 10), (1, "B"): Fraction("2.000000001"), (1, "C"): 0}
    with raises(M.CostAllocationError) as ei:
        M.units([{"step": 3, "receiver_member": "A", "driver_value": -1}])
    assert "step 3" in ei.value.errors[0] and "negative" in ei.value.errors[0]


def test_rate_worked_example_exact_coefficients():
    steps, w, u = _rate_example()
    rows = {_key(r): r for r in M.step_down_coefficients(steps, w, u)}
    assert set(rows) == {
        ("IT", 1, "IT", "HR"), ("IT", 1, "IT", "Sales"), ("IT", 1, "IT", "Ops"),
        ("IT", 2, "HR", "Sales"), ("IT", 2, "HR", "Ops"),
        ("HR", 2, "HR", "Sales"), ("HR", 2, "HR", "Ops"),
    }
    # the rate origin's own hop is in driver units: pool 19 tickets, 5/8/6 each
    for recv, q in (("HR", 5), ("Sales", 8), ("Ops", 6)):
        r = rows[("IT", 1, "IT", recv)]
        assert r["hop_kind"] == "own" and r["pool_coefficient"] == 19 and r["coefficient"] == q
    # HR passes the 5 tickets it received on by headcount 5:3
    assert rows[("IT", 2, "HR", "Sales")]["hop_kind"] == "pass"
    assert rows[("IT", 2, "HR", "Sales")]["pool_coefficient"] == 5
    assert rows[("IT", 2, "HR", "Sales")]["coefficient"] == Fraction(25, 8)
    assert rows[("IT", 2, "HR", "Ops")]["coefficient"] == Fraction(15, 8)
    # HR's own share origin is unchanged: pool 1, split 5/8 and 3/8
    assert rows[("HR", 2, "HR", "Sales")]["pool_coefficient"] == 1
    assert rows[("HR", 2, "HR", "Sales")]["coefficient"] == Fraction(5, 8)
    assert rows[("HR", 2, "HR", "Ops")]["coefficient"] == Fraction(3, 8)


def test_rate_worked_example_end_to_end_reproduces_every_number():
    steps, w, u = _rate_example()
    rows = M.coefficient_rows("ZZRUN-R", steps, w, "Step-Down", u)
    assert all(list(r) == list(M.COEFFICIENT_COLUMNS) for r in rows)
    by = {_key(r): r for r in rows}
    D = Decimal
    assert by[("IT", 1, "IT", "HR")]["pool_coefficient"] == D("19.000000000000")
    assert by[("IT", 1, "IT", "HR")]["coefficient"] == D("5.000000000000")
    assert by[("IT", 2, "HR", "Sales")]["coefficient"] == D("3.125000000000")
    assert by[("IT", 2, "HR", "Ops")]["coefficient"] == D("1.875000000000")
    assert all(r["coefficient"].as_tuple().exponent == -12 for r in rows)
    assert all(r["pool_coefficient"].as_tuple().exponent == -12 for r in rows)
    pools = {"IT": D("1000"), "HR": D("400")}
    net, sent, received = _apply(rows, steps, pools)
    # IT charges HR 250, Sales 400, Ops 300 = 950
    it_own = {r["receiver_member"]: D(50) * r["coefficient"] for r in rows
              if r["origin_member"] == "IT" and r["hop_kind"] == "own"}
    assert it_own == {"HR": D(250), "Sales": D(400), "Ops": D(300)}
    assert sent["IT"] == D(950)
    # HR holds 400 + 250 = 650 and splits 5/8, 3/8
    assert received["HR"] == D(250)
    assert sent["HR"] == D(650)
    # IT-origin units through HR: Sales 3.125 x 50 = 156.25, Ops 1.875 x 50 = 93.75
    via_hr = {r["receiver_member"]: D(50) * r["coefficient"] for r in rows
              if r["origin_member"] == "IT" and r["sender_member"] == "HR"}
    assert via_hr == {"Sales": D("156.25"), "Ops": D("93.75")}
    assert net["Sales"] == D("806.25") and net["Ops"] == D("543.75")
    variance = M.absorption({"IT": pools["IT"]}, {"IT": received.get("IT", 0)}, {"IT": sent["IT"]})
    assert variance == {"IT": 50}
    assert Fraction(net["Sales"] + net["Ops"]) + variance["IT"] == 1400


def test_rate_direct_step_in_units():
    steps = [M.Step(1, "IT", "Tickets", ["P1", "P2"], basis=_RATE, rate=Fraction("12.5")),
             M.Step(2, "HR", "Headcount", ["P1", "P2"])]
    rows = [{"step": 1, "receiver_member": "P1", "driver_value": "0.5"},
            {"step": 1, "receiver_member": "P2", "driver_value": "1.25"},
            {"step": 2, "receiver_member": "P1", "driver_value": 1},
            {"step": 2, "receiver_member": "P2", "driver_value": 1}]
    w, u = M.weights(rows), M.units(rows)
    exact = {_key(r): r for r in M.direct_coefficients(steps, w, u)}
    assert exact[("IT", 1, "IT", "P1")]["pool_coefficient"] == Fraction("1.75")
    assert exact[("IT", 1, "IT", "P1")]["coefficient"] == Fraction("0.5")
    assert exact[("IT", 1, "IT", "P2")]["coefficient"] == Fraction("1.25")
    assert exact[("HR", 2, "HR", "P1")]["pool_coefficient"] == 1
    assert all(r["hop_kind"] == "own" for r in exact.values())
    out = {_key(r): r for r in M.coefficient_rows("ZZRUN", steps, w, "Direct", u)}
    assert out[("IT", 1, "IT", "P1")]["pool_coefficient"] == Decimal("1.750000000000")
    assert out[("IT", 1, "IT", "P2")]["coefficient"] == Decimal("1.250000000000")
    assert out[("HR", 2, "HR", "P1")]["coefficient"] == Decimal("0.500000000000")
    # charged = rate x units, exact
    assert sum(Decimal("12.5") * r["coefficient"] for k, r in out.items() if k[0] == "IT") == Decimal("21.875")


def test_rate_sender_is_a_sink_for_other_origins_in_step_down():
    # HR (share) sends to IT (rate sender at step 2) and Sales; IT charges Sales and Ops.
    steps = [M.Step(1, "HR", "Headcount", ["IT", "Sales"]),
             M.Step(2, "IT", "Tickets", ["Sales", "Ops"], basis=_RATE, rate=Fraction(10))]
    rows = [{"step": 1, "receiver_member": "IT", "driver_value": 1},
            {"step": 1, "receiver_member": "Sales", "driver_value": 3},
            {"step": 2, "receiver_member": "Sales", "driver_value": 2},
            {"step": 2, "receiver_member": "Ops", "driver_value": 4}]
    w, u = M.weights(rows), M.units(rows)
    exact = M.step_down_coefficients(steps, w, u)
    keys = {_key(r) for r in exact}
    # HR's cost that reaches IT stops there: no pass rows for origin HR at IT
    assert keys == {("HR", 1, "HR", "IT"), ("HR", 1, "HR", "Sales"),
                    ("IT", 2, "IT", "Sales"), ("IT", 2, "IT", "Ops")}
    assert all(r["hop_kind"] == "own" for r in exact)
    out = M.coefficient_rows("ZZRUN", steps, w, "Step-Down", u)
    net, sent, received = _apply(out, steps, {"HR": Decimal(400), "IT": Decimal(100)})
    assert received["IT"] == Decimal(100) and sent["IT"] == Decimal(60)
    assert net == {"HR": Decimal(-400), "IT": Decimal(40), "Sales": Decimal(320), "Ops": Decimal(40)}
    # variance = pool + received - charged: 100 + 100 - 60 = 140 (over-absorbed cost stays at IT)
    assert M.absorption({"IT": 100}, {"IT": received["IT"]}, {"IT": sent["IT"]}) == {"IT": 140}
    assert net["Sales"] + net["Ops"] + 140 == Decimal(500)


def test_rate_share_sender_passes_rate_origin_units_by_its_weights():
    steps = [M.Step(1, "IT", "Tickets", ["HR", "P1"], basis=_RATE, rate=Fraction(7)),
             M.Step(2, "HR", "Headcount", ["P1", "P2", "P3"])]
    rows = [{"step": 1, "receiver_member": "HR", "driver_value": 1},
            {"step": 1, "receiver_member": "P1", "driver_value": 2},
            {"step": 2, "receiver_member": "P1", "driver_value": 1},
            {"step": 2, "receiver_member": "P2", "driver_value": 1},
            {"step": 2, "receiver_member": "P3", "driver_value": 1}]
    w, u = M.weights(rows), M.units(rows)
    exact = {_key(r): r for r in M.step_down_coefficients(steps, w, u)}
    for p in ("P1", "P2", "P3"):
        r = exact[("IT", 2, "HR", p)]
        assert r["hop_kind"] == "pass" and r["pool_coefficient"] == 1 and r["coefficient"] == Fraction(1, 3)
    out = {_key(r): r for r in M.coefficient_rows("ZZRUN", steps, w, "Step-Down", u)}
    # 1 unit in thirds rounds exactly to 1 unit out; the leftover unit goes to P1 by name
    passed = [out[("IT", 2, "HR", p)]["coefficient"] for p in ("P1", "P2", "P3")]
    assert passed == [Decimal("0.333333333334"), Decimal("0.333333333333"), Decimal("0.333333333333")]
    assert sum(passed) == out[("IT", 2, "HR", "P1")]["pool_coefficient"] == Decimal("1.000000000000")


def test_rate_rounding_sums_exact_in_units_and_in_money():
    # units with 9 decimals; the own pool is their exact sum and the receiver
    # coefficients are exact, so rate x units carries no rounding at all
    steps = [M.Step(1, "IT", "T", ["A", "B", "C", "HR"], basis=_RATE, rate=Fraction("0.0001")),
             M.Step(2, "HR", "H", ["A", "B", "C"])]
    rows = [{"step": 1, "receiver_member": "A", "driver_value": "0.333333333"},
            {"step": 1, "receiver_member": "B", "driver_value": "0.000000001"},
            {"step": 1, "receiver_member": "C", "driver_value": "123456.789012345"},
            {"step": 1, "receiver_member": "HR", "driver_value": "7"},
            {"step": 2, "receiver_member": "A", "driver_value": 1},
            {"step": 2, "receiver_member": "B", "driver_value": 1},
            {"step": 2, "receiver_member": "C", "driver_value": 5}]
    w, u = M.weights(rows), M.units(rows)
    out = M.coefficient_rows("ZZRUN", steps, w, "Step-Down", u)
    own = [r for r in out if r["origin_member"] == "IT" and r["hop_kind"] == "own"]
    total = Decimal("0.333333333") + Decimal("0.000000001") + Decimal("123456.789012345") + 7
    assert {r["pool_coefficient"] for r in own} == {total.quantize(_Q)}
    assert sum(r["coefficient"] for r in own) == total.quantize(_Q)
    assert {r["receiver_member"]: r["coefficient"] for r in own} == {
        "A": Decimal("0.333333333000"), "B": Decimal("0.000000001000"),
        "C": Decimal("123456.789012345000"), "HR": Decimal("7.000000000000")}
    # the 7 units HR received are split 1:1:5 exactly
    via = [r for r in out if r["origin_member"] == "IT" and r["sender_member"] == "HR"]
    assert {r["pool_coefficient"] for r in via} == {Decimal("7.000000000000")}
    assert sum(r["coefficient"] for r in via) == Decimal("7.000000000000")
    assert all(abs(Fraction(r["coefficient"]) - Fraction(7) * w[(2, r["receiver_member"])]) <= _UNIT for r in via)
    # money: everything IT charged ends at the finals, to the unit
    sinks = sum(r["coefficient"] for r in out if r["origin_member"] == "IT" and r["receiver_member"] != "HR")
    assert sinks == total.quantize(_Q)


def test_rate_zero_total_units_refused_naming_the_step():
    steps = [M.Step(1, "IT", "T", ["P1", "P2"], basis=_RATE, rate=Fraction(5))]
    w = {(1, "P1"): Fraction(1), (1, "P2"): Fraction(0)}  # weights cannot even be built from zeros
    u = {(1, "P1"): Fraction(0), (1, "P2"): Fraction(0)}
    for fn in (M.direct_coefficients, M.step_down_coefficients):
        with raises(M.CostAllocationError) as ei:
            fn(steps, w, u)
        assert "step 1" in ei.value.errors[0] and "zero" in ei.value.errors[0].lower()
    # and weights() itself refuses an all-zero step, as before
    with raises(M.CostAllocationError):
        M.weights([{"step": 1, "receiver_member": "P1", "driver_value": 0},
                   {"step": 1, "receiver_member": "P2", "driver_value": 0}])


def test_rate_missing_units_or_bad_rate_refused_all_at_once():
    steps = [M.Step(1, "IT", "T", ["P1", "P2"], basis=_RATE, rate=Fraction(5)),
             M.Step(2, "HR", "T", ["P1"], basis=_RATE, rate=Fraction(0)),
             M.Step(3, "FM", "T", ["P1"], basis=_RATE, rate=Fraction("1.00001")),
             M.Step(4, "QA", "T", ["P1"], basis=_RATE, rate=None),
             M.Step(5, "XX", "T", ["P1"], basis="Per Head")]
    w = {(s, r): Fraction(1) for s in range(1, 6) for r in ("P1",)}
    w[(1, "P1")], w[(1, "P2")] = Fraction(1, 2), Fraction(1, 2)
    u = {(1, "P1"): Fraction(1), (2, "P1"): Fraction(1), (3, "P1"): Fraction(1),
         (4, "P1"): Fraction(1), (5, "P1"): Fraction(1)}
    with raises(M.CostAllocationError) as ei:
        M.direct_coefficients(steps, w, u)
    text = "\n".join(ei.value.errors)
    assert "step 1" in text and "P2" in text            # missing units
    assert "step 2" in text and "positive" in text      # rate 0
    assert "step 3" in text and "4 decimal" in text     # 5 dp
    assert "step 4" in text and "rate" in text          # no rate
    assert "step 5" in text and "Per Head" in text      # unknown basis
    # no units at all for a rate step is one clear refusal
    with raises(M.CostAllocationError) as ei:
        M.direct_coefficients(steps[:1], w)
    assert "step 1" in ei.value.errors[0] and "units" in ei.value.errors[0]


def test_rate_reciprocal_refused_for_now():
    steps = [M.Step(1, "IT", "T", ["HR", "P1"], basis=_RATE, rate=Fraction(5)),
             M.Step(2, "HR", "H", ["IT", "P1"])]
    w = {(1, "HR"): Fraction(1, 2), (1, "P1"): Fraction(1, 2),
         (2, "IT"): Fraction(1, 2), (2, "P1"): Fraction(1, 2)}
    u = {(1, "HR"): Fraction(1), (1, "P1"): Fraction(1)}
    for call in (lambda: M.reciprocal_coefficients(steps, w, u),
                 lambda: M.coefficient_rows("ZZRUN", steps, w, "Reciprocal", u)):
        with raises(M.CostAllocationError) as ei:
            call()
        assert "Declared Rate with the Reciprocal method arrives in a later task" in ei.value.errors[0]
        assert "step 1" in ei.value.errors[0]


def test_rate_validation_rules():
    rate_rules = [{"step": 1, "sender_member": "HR", "driver": "Headcount", "basis": _RATE},
                  {"step": 2, "sender_member": "IT", "driver": "Headcount"}]
    # Q6 A: a rate rule needs Declared Allocation Account mode
    errs = _errors(_rs(rules=rate_rules))
    assert _has(errs, "step 1") and _has(errs, "Declared Allocation Account")
    assert not _errors(_rs(rules=rate_rules, receiver_account_mode="Declared Allocation Account",
                           allocation_account="6900"))
    # Reciprocal + rate refused at validation too
    errs = _errors(_rs(rules=rate_rules, method="Reciprocal",
                       receiver_account_mode="Declared Allocation Account", allocation_account="6900"))
    assert _has(errs, "Declared Rate with the Reciprocal method arrives in a later task")
    # unknown basis
    errs = _errors(_rs(rules=[dict(rate_rules[0], basis="Per Head"), rate_rules[1]]))
    assert _has(errs, "Per Head") and _has(errs, "Share of Pool, Declared Rate")
    # a rate given on the rule is checked: > 0 and at most 4 decimals
    for bad, word in ((0, "positive"), (-1, "positive"), ("1.00001", "4 decimal")):
        errs = _errors(_rs(rules=[dict(rate_rules[0], rate=bad), rate_rules[1]],
                           receiver_account_mode="Declared Allocation Account", allocation_account="6900"))
        assert _has(errs, "step 1") and _has(errs, word), (bad, errs)
    # share rules are untouched by any of this
    assert not _errors(_rs())


def test_rate_absorption_is_exact_and_names_unknown_senders():
    v = M.absorption({"IT": "1000", "FM": Decimal("250.10")},
                     {"IT": 0, "FM": "0.1"},
                     {"IT": 950, "FM": Decimal("300.2")})
    assert v == {"IT": Fraction(50), "FM": Fraction(-50)}
    assert all(isinstance(x, Fraction) for x in v.values())
    # a sender with nothing received or charged still gets its variance (= pool)
    assert M.absorption({"IT": 10}, {}, {}) == {"IT": 10}
    with raises(M.CostAllocationError) as ei:
        M.absorption({"IT": 10}, {"HR": 1}, {"QA": 2})
    assert "HR" in ei.value.errors[0] and "QA" in ei.value.errors[0]


def _check_rate_invariants(steps, w, u, method):
    exact = (M.step_down_coefficients if method == "Step-Down" else M.direct_coefficients)(steps, w, u)
    rows = M.coefficient_rows("ZZRUN", steps, w, method, u)
    assert [_key(r) for r in rows] == [_key(r) for r in exact]
    by_sender = {s.sender_member: s for s in steps}
    rate_senders = {m for m, s in by_sender.items() if s.basis == _RATE}
    own_pool = {m: (sum(u[(s.step, r)] for r in s.receivers) if s.basis == _RATE else Fraction(1))
                for m, s in by_sender.items()}
    hops = {}
    for r in rows:
        assert r["coefficient"] >= 0
        assert r["coefficient"].as_tuple().exponent == -12
        hops.setdefault((r["origin_member"], r["step"], r["sender_member"], r["hop_kind"]), []).append(r)
    for hop in hops.values():
        assert len({r["pool_coefficient"] for r in hop}) == 1
        assert sum(r["coefficient"] for r in hop) == hop[0]["pool_coefficient"]
    for origin in {r["origin_member"] for r in rows}:
        mine = [r for r in rows if r["origin_member"] == origin]
        own = [r for r in mine if r["hop_kind"] == "own"]
        assert own and all(r["sender_member"] == origin for r in own)
        assert {Fraction(r["pool_coefficient"]) for r in own} == {own_pool[origin]}
        # a rate sender other than the origin never passes this origin's cost on
        assert not any(r["sender_member"] in rate_senders and r["sender_member"] != origin for r in mine)
        # a share sender passes on exactly what it received
        for (o, st, snd, kind), hop in hops.items():
            if o == origin and snd != origin:
                got = sum(r["coefficient"] for r in mine if r["receiver_member"] == snd)
                assert got == hop[0]["pool_coefficient"]
        # generalised T1: what reaches sinks (non-senders and other rate senders) is the own pool
        sinks = sum(r["coefficient"] for r in mine
                    if r["receiver_member"] not in by_sender or r["receiver_member"] in rate_senders)
        assert Fraction(sinks) == own_pool[origin]
    bound = len(hops) * _UNIT
    for r, x in zip(rows, exact):
        assert abs(Fraction(r["coefficient"]) - x["coefficient"]) <= bound


def _random_rate_case(rng, method):
    n_send = rng.randint(1, 5)
    finals = ["F%d" % i for i in range(rng.randint(1, 6))]
    senders = ["S%d" % i for i in range(n_send)]
    steps, rows = [], []
    for i, snd in enumerate(senders):
        pool = list(finals) + (senders[i + 1:] if method == "Step-Down" else [])
        recv = rng.sample(pool, rng.randint(1, len(pool)))
        vals = [rng.choice([0, 1, 2, 3, 7, 11, 13, "0.1", "2.5", "0.000000001", "12345.678901234"])
                for _ in recv]
        if not any(Fraction(str(v)) for v in vals):
            vals[0] = 3
        rows.extend({"step": i + 1, "receiver_member": m, "driver_value": v} for m, v in zip(recv, vals))
        if rng.random() < 0.5:
            steps.append(M.Step(i + 1, snd, "D%d" % (i % 2), recv, basis=_RATE,
                                rate=Fraction(rng.choice(["0.0001", "50", "12.3456", "9999.99"]))))
        else:
            steps.append(M.Step(i + 1, snd, "D%d" % (i % 2), recv))
    return steps, M.weights(rows), M.units(rows)


def test_rate_property_random_mixed_steps():
    seen_rate = seen_sink = False
    for seed in range(300):
        for method, base in (("Step-Down", 20_000), ("Direct", 30_000)):
            steps, w, u = _random_rate_case(random.Random(base + seed), method)
            rate = {s.sender_member for s in steps if s.basis == _RATE}
            seen_rate |= bool(rate)
            seen_sink |= any(r in rate for s in steps for r in s.receivers)
            _check_rate_invariants(steps, w, u, method)
    assert seen_rate and seen_sink


#: sha256 over every coefficient row of _random_case seeds 0..299 (Step-Down)
#: and 10000..10299 (Direct), computed on the model BEFORE declared rates
#: existed (commit effd913e). Share-only output is byte-identical or this fails.
_SHARE_ONLY_DIGEST = {
    "Step-Down": "08f93ae4990b0f9e8d7eeab5d0952e1eafc10ca2232b4acd1aae2c9a086c01ed",
    "Direct": "146233d1a30719dc3fcfac3f8f1adc1ee66a61179223dc5d54c8f5f22c72aa21",
}


def test_rate_property_share_only_output_unchanged():
    for method, base in (("Step-Down", 0), ("Direct", 10_000)):
        h = hashlib.sha256()
        for seed in range(300):
            steps, w = _random_case(random.Random(base + seed), method)
            rows = M.coefficient_rows("ZZRUN", steps, w, method)
            for r in rows:
                h.update(repr([str(r[c]) for c in M.COEFFICIENT_COLUMNS]).encode())
            # explicit Share of Pool basis and unused units change nothing
            explicit = [M.Step(s.step, s.sender_member, s.driver, s.receivers, basis=_SHARE) for s in steps]
            u = {k: Fraction(1) for k in w}
            assert M.coefficient_rows("ZZRUN", explicit, w, method, u) == rows
        assert h.hexdigest() == _SHARE_ONLY_DIGEST[method], method
