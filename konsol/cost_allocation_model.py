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
