"""Cost allocation model, pure (konsol#269, K2a).

Plain data in, plain data out. Imports nothing from frappe or konsol.

A rule set has ordered steps. Each step has one sender (a member of the rule
set's dimension), one driver and one or more receivers. Direct: no receiver is
a sender. Step-Down: a receiver at step i may not be a sender at step <= i, so
cost only flows forward.

Driver values are never defaulted. A missing value refuses, in one error that
lists every missing (step, receiver, period), unless the driver declares
carry-forward, in which case the latest earlier period's value is used and the
row is flagged.
"""
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

DIRECT = "Direct"
STEP_DOWN = "Step-Down"
METHODS = (DIRECT, STEP_DOWN)
OWN_ACCOUNTS = "Sender's Own Accounts"
DECLARED_ACCOUNT = "Declared Allocation Account"
ACCOUNT_MODES = (OWN_ACCOUNTS, DECLARED_ACCOUNT)


class CostAllocationError(Exception):
    """Carries every problem found, not just the first."""

    def __init__(self, errors):
        if isinstance(errors, str):
            errors = [errors]
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


@dataclass
class RuleSet:
    entity: str
    dimension: str
    method: str
    receiver_account_mode: str
    allocation_account: str = ""
    rules: list = field(default_factory=list)       # {step, sender_member, driver}
    receivers: list = field(default_factory=list)   # {step, receiver_member}
    dimension_members: object = field(default_factory=set)
    driver_dimensions: dict = field(default_factory=dict)  # driver -> dimension


@dataclass
class Step:
    step: int
    sender_member: str
    driver: str
    receivers: list


def _dups(items):
    return [k for k, n in Counter(items).items() if n > 1]


def validate(rs):
    """Return every error as a list of messages ([] when the rule set is valid)."""
    errs = []
    members = set(rs.dimension_members or ())
    if rs.method not in METHODS:
        errs.append("Method must be one of %s, not %r." % (", ".join(METHODS), rs.method))
    if rs.receiver_account_mode not in ACCOUNT_MODES:
        errs.append("Receiver account mode must be one of %s, not %r."
                    % (", ".join(ACCOUNT_MODES), rs.receiver_account_mode))
    elif rs.receiver_account_mode == DECLARED_ACCOUNT and not rs.allocation_account:
        errs.append("Allocation Account is required when receiver accounts are %s." % DECLARED_ACCOUNT)
    elif rs.receiver_account_mode == OWN_ACCOUNTS and rs.allocation_account:
        errs.append("Allocation Account is set but receiver accounts are %s; it would do nothing."
                    % OWN_ACCOUNTS)
    if not rs.rules:
        errs.append("A rule set needs at least one rule.")

    rule_steps = [r["step"] for r in rs.rules]
    for s in _dups(rule_steps):
        errs.append("Step %s is declared more than once." % s)
    for m in _dups([r["sender_member"] for r in rs.rules]):
        errs.append("%s is a sender more than once." % m)

    sender_step = {}
    for r in rs.rules:
        sender_step.setdefault(r["sender_member"], r["step"])
        if r["sender_member"] not in members:
            errs.append("%s is not a member of %s." % (r["sender_member"], rs.dimension))
        drv = r["driver"]
        if drv not in rs.driver_dimensions:
            errs.append("Driver %s (step %s) is not a known driver." % (drv, r["step"]))
        elif rs.driver_dimensions[drv] != rs.dimension:
            errs.append("Driver %s is on dimension %s, not %s."
                        % (drv, rs.driver_dimensions[drv], rs.dimension))

    declared = set(rule_steps)
    by_step = {}
    for rc in rs.receivers:
        by_step.setdefault(rc["step"], []).append(rc["receiver_member"])
    for s in sorted(by_step):
        if s not in declared:
            errs.append("Step %s has receivers but no rule." % s)
    for m in dict.fromkeys(rc["receiver_member"] for rc in rs.receivers):
        if m not in members:
            errs.append("%s is not a member of %s." % (m, rs.dimension))
    for r in rs.rules:
        s = r["step"]
        recvs = by_step.get(s, [])
        if not recvs:
            errs.append("Step %s has no receiver." % s)
        for m in _dups(recvs):
            errs.append("%s is a receiver of step %s more than once." % (m, s))
        for m in dict.fromkeys(recvs):
            if m == r["sender_member"]:
                errs.append("%s cannot receive its own allocation (step %s)." % (m, s))
            elif m in sender_step:
                if rs.method == DIRECT:
                    errs.append("%s is a receiver and a sender; Direct allocation does not allow it (step %s)."
                                % (m, s))
                elif rs.method == STEP_DOWN and sender_step[m] <= s:
                    errs.append("%s receives at step %s but is a sender at step %s; "
                                "Step-Down cost only flows to later senders."
                                % (m, s, sender_step[m]))
    return list(dict.fromkeys(errs))


def assert_valid(rs):
    errs = validate(rs)
    if errs:
        raise CostAllocationError(errs)


def build_steps(rules, receivers):
    """Join rules and receivers into Steps ordered by step number."""
    by_step = {}
    for rc in receivers:
        by_step.setdefault(rc["step"], []).append(rc["receiver_member"])
    return [
        Step(r["step"], r["sender_member"], r["driver"], list(by_step.get(r["step"], [])))
        for r in sorted(rules, key=lambda r: r["step"])
    ]


def resolve_driver_values(steps, period, values, carry_forward_by_driver):
    """One row per step x receiver for ``period``.

    values: {(driver, period, member): number}, already limited to the run's
    scenario. carry_forward_by_driver: {driver: bool}; an undeclared driver is
    treated as not carrying (the run refuses, nothing is assumed).

    Rows: step, sender_member, receiver_member, driver, driver_value,
    value_period, carried_forward. Raises one CostAllocationError listing
    every (step, receiver, period) with no value.
    """
    rows, missing = [], []
    for st in steps:
        carry = bool(carry_forward_by_driver.get(st.driver))
        for recv in st.receivers:
            v = values.get((st.driver, period, recv))
            used, carried = period, False
            if v is None and carry:
                earlier = [p for (d, p, m), x in values.items()
                           if d == st.driver and m == recv and x is not None and p < period]
                if earlier:
                    used, carried = max(earlier), True
                    v = values[(st.driver, used, recv)]
            if v is None:
                missing.append("step %s, %s, period %s (driver %s)" % (st.step, recv, period, st.driver))
                continue
            rows.append({
                "step": st.step, "sender_member": st.sender_member,
                "receiver_member": recv, "driver": st.driver,
                "driver_value": v, "value_period": used, "carried_forward": carried,
            })
    if missing:
        raise CostAllocationError(["Missing driver values: " + "; ".join(missing) + "."])
    return rows


def _exact(v):
    # str() so 0.1 is the decimal 0.1, not the binary float.
    return Fraction(str(v))


def weights(rows):
    """{(step, receiver_member): Fraction}; each step sums to exactly 1.

    Refuses every step whose values are all zero, and any negative value.
    """
    totals, vals, errs = {}, {}, []
    for r in rows:
        v = _exact(r["driver_value"])
        if v < 0:
            errs.append("Driver value for step %s, %s is negative." % (r["step"], r["receiver_member"]))
        vals[(r["step"], r["receiver_member"])] = v
        totals[r["step"]] = totals.get(r["step"], Fraction(0)) + v
    for s in sorted(totals):
        if totals[s] == 0:
            errs.append("All driver values for step %s are zero; there is nothing to weight by." % s)
    if errs:
        raise CostAllocationError(errs)
    return {(s, m): v / totals[s] for (s, m), v in vals.items()}


# --- coefficients (K2b) ------------------------------------------------------
#
# A coefficient row says: of one unit of ORIGIN's pool, SENDER holds
# pool_coefficient at its step and gives `coefficient` of it to RECEIVER.
# dbt multiplies: x = origin_amount * coefficient, `sent` -x at the sender,
# `received` +x at the receiver.

COEFFICIENT_PLACES = 12
_SCALE = 10 ** COEFFICIENT_PLACES
_QUANTUM = Decimal(1).scaleb(-COEFFICIENT_PLACES)
COEFFICIENT_COLUMNS = ("run", "origin_member", "step", "sender_member", "receiver_member",
                       "driver", "pool_coefficient", "coefficient")


def _weight(weights, st, recv):
    try:
        return weights[(st.step, recv)]
    except KeyError:
        raise CostAllocationError("No weight for step %s, receiver %s." % (st.step, recv))


def _hop(origin, st, pool, weights):
    return [{
        "origin_member": origin, "step": st.step, "sender_member": st.sender_member,
        "receiver_member": recv, "driver": st.driver,
        "pool_coefficient": pool, "coefficient": pool * _weight(weights, st, recv),
    } for recv in st.receivers]


def direct_coefficients(steps, weights):
    """Exact Fraction rows for Direct allocation.

    Each sender is an origin and gives its whole unit pool to its receivers by
    weight; pool_coefficient is 1 on every row. Refuses a receiver that is a
    sender (Direct cost never passes through a second sender).
    """
    senders = {st.sender_member for st in steps}
    bad = ["%s receives at step %s but is a sender; Direct allocation does not allow it."
           % (r, st.step) for st in steps for r in st.receivers if r in senders]
    if bad:
        raise CostAllocationError(bad)
    rows = []
    for st in sorted(steps, key=lambda s: s.step):
        rows.extend(_hop(st.sender_member, st, Fraction(1), weights))
    return rows


def step_down_coefficients(steps, weights):
    """Exact Fraction rows for Step-Down allocation.

    In step order, each sender passes on its own pool plus whatever it received
    from earlier steps. Per origin O (every sender): O holds 1 at its own step;
    a later sender S holds the sum of the coefficients it received from O at
    earlier steps, and gives that times its weights. A sender that holds
    nothing of O emits no rows for O. A sender receives nothing at or after its
    own step (refused): cost only flows forward.
    """
    ordered = sorted(steps, key=lambda s: s.step)
    sender_step = {st.sender_member: st.step for st in ordered}
    bad = ["%s receives at step %s but sends at step %s; Step-Down cost only flows forward."
           % (r, st.step, sender_step[r])
           for st in ordered for r in st.receivers
           if r in sender_step and sender_step[r] <= st.step]
    if bad:
        raise CostAllocationError(bad)
    rows = []
    for i, origin_step in enumerate(ordered):
        origin = origin_step.sender_member
        held = {origin: Fraction(1)}
        for st in ordered[i:]:
            pool = held.get(st.sender_member, Fraction(0))
            if pool == 0:
                continue
            for r in _hop(origin, st, pool, weights):
                held[r["receiver_member"]] = held.get(r["receiver_member"], Fraction(0)) + r["coefficient"]
                rows.append(r)
    return rows


def _split(units, hop_rows, weights_by_receiver):
    """Largest remainder: integer units per receiver summing exactly to ``units``.

    Targets are units * weight. Leftover units go to the largest fractional
    remainders, ties to the receiver name that sorts first.
    """
    targets = {r["receiver_member"]: units * weights_by_receiver[r["receiver_member"]]
               for r in hop_rows}
    floors = {m: t.numerator // t.denominator for m, t in targets.items()}
    left = units - sum(floors.values())
    for m in sorted(targets, key=lambda m: (-(targets[m] - floors[m]), m))[:left]:
        floors[m] += 1
    return floors


def round_coefficients(exact_rows, weights):
    """Round exact rows to 12 dp, as Decimals, conserving money exactly.

    Works per origin in step order on integer units of 1e-12:
      - at the origin's own hop the pool is exactly 1;
      - at a later sender the pool is the exact sum of the ROUNDED coefficients
        it received from that origin (so its `received` and `sent` lines net to
        zero in dbt);
      - each hop's pool is split over its receivers by largest remainder of
        pool * weight, so the receiver coefficients sum exactly to the pool.
    Hence, per origin, the rounded coefficients reaching non-senders sum to
    exactly 1. Each rounded value is within (number of hops) * 1e-12 of exact.
    """
    out, held, pending, hop = [], {}, [], None

    def flush():
        if not pending:
            return
        r0 = pending[0]
        o = r0["origin_member"]
        if r0["sender_member"] == o:
            units = _SCALE
        else:
            units = held.get((o, r0["sender_member"]), 0)
        w = {r["receiver_member"]: weights[(r["step"], r["receiver_member"])] for r in pending}
        split = _split(units, pending, w)
        pool = Decimal(units).scaleb(-COEFFICIENT_PLACES).quantize(_QUANTUM)
        for r in pending:
            u = split[r["receiver_member"]]
            held[(o, r["receiver_member"])] = held.get((o, r["receiver_member"]), 0) + u
            out.append(dict(r, pool_coefficient=pool,
                            coefficient=Decimal(u).scaleb(-COEFFICIENT_PLACES).quantize(_QUANTUM)))
        pending.clear()

    for r in exact_rows:
        key = (r["origin_member"], r["step"], r["sender_member"])
        if key != hop:
            flush()
            hop = key
        pending.append(r)
    flush()
    return out


def coefficient_rows(run_name, steps, weights, method):
    """Rows for epm_staging.cost_allocation_coefficients, 12-dp Decimals.

    Columns, in order: run, origin_member, step, sender_member,
    receiver_member, driver, pool_coefficient, coefficient. Invariants (the
    warehouse tests rely on them):
      T5  per (run, origin, step, sender): sum(coefficient) == pool_coefficient;
      own per origin: pool_coefficient == 1 at the origin's own step;
      pass per origin and later sender S: pool_coefficient at S == sum of the
          coefficients S received from that origin (all at earlier steps);
      T1  per origin: sum(coefficient) over receivers that are not senders == 1.
    All exact in Decimal, so origin_amount * coefficient conserves to the cent.
    """
    errs = []
    for st in steps:
        ws = [_weight(weights, st, r) for r in st.receivers]
        if any(x < 0 for x in ws) or sum(ws, Fraction(0)) != 1:
            errs.append("Weights for step %s must be non-negative and sum to exactly 1." % st.step)
    if errs:
        raise CostAllocationError(errs)
    if method == DIRECT:
        exact = direct_coefficients(steps, weights)
    elif method == STEP_DOWN:
        exact = step_down_coefficients(steps, weights)
    else:
        raise CostAllocationError("Method must be one of %s, not %r." % (", ".join(METHODS), method))
    return [{"run": run_name, **{c: r[c] for c in COEFFICIENT_COLUMNS[1:]}}
            for r in round_coefficients(exact, weights)]
