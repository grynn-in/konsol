"""Cost allocation model, pure (konsol#269, K2a).

Plain data in, plain data out. Imports nothing from frappe or konsol.

A rule set has ordered steps. Each step has one sender (a member of the rule
set's dimension), one driver and one or more receivers. Direct: no receiver is
a sender. Step-Down: a receiver at step i may not be a sender at step <= i, so
cost only flows forward. Reciprocal: a receiver may be any other sender, and
the mutual exchange is solved exactly (``reciprocal_coefficients``); step
numbers are then only rule ids.

Driver values are never defaulted. A missing value refuses, in one error that
lists every missing (step, receiver, period), unless the driver declares
carry-forward, in which case the latest earlier period's value is used and the
row is flagged.

A step's basis is Share of Pool (the sender's pool, split by weights) or
Declared Rate (each receiver is charged rate x its driver units; the sender's
over- or under-absorption is reported, never re-spread). Decided Q5 5B (13 Nov
2026) and 2b-2 Q4/Q5/Q6 A (Deepak Pai, 27 Nov 2026, konsol#269): the rate lives
on the Rule Set by step, the variance is its own model per rate sender, and a
rate rule needs Declared Allocation Account mode. Under every method a rate
sender is a sink for cost that reaches it; under Reciprocal the system is
solved over share senders only (plan section 3).
"""
import csv
import io
import math
import re
from collections import deque
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction

DIRECT = "Direct"
STEP_DOWN = "Step-Down"
RECIPROCAL = "Reciprocal"
METHODS = (DIRECT, STEP_DOWN, RECIPROCAL)
#: hop_kind: OWN is the origin's own unit leaving it; PASS is cost a sender
#: received (from the origin, directly or through others) and passes on.
OWN, PASS = "own", "pass"
HOP_KINDS = (OWN, PASS)
OWN_ACCOUNTS = "Sender's Own Accounts"
DECLARED_ACCOUNT = "Declared Allocation Account"
ACCOUNT_MODES = (OWN_ACCOUNTS, DECLARED_ACCOUNT)
#: a step's basis (2b-2): Share of Pool splits the sender's pool by weights;
#: Declared Rate charges each receiver rate x its driver units.
SHARE_OF_POOL = "Share of Pool"
DECLARED_RATE = "Declared Rate"
BASES = (SHARE_OF_POOL, DECLARED_RATE)
#: a declared rate has at most this many decimal places (plan 2b, Rule Rate)
RATE_PLACES = 4


#: the warehouse column is UInt16
MIN_STEP, MAX_STEP = 1, 65535


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
    #: SHARE_OF_POOL or DECLARED_RATE; the default is the only shape that
    #: existed before 2b-2, so a rule without a basis is a share step.
    basis: str = SHARE_OF_POOL
    #: the declared rate per driver unit (exact Fraction) for a DECLARED_RATE
    #: step, already resolved to the run's period by the caller; None otherwise.
    rate: object = None


def _dups(items):
    return [k for k, n in Counter(items).items() if n > 1]


def _rate_error(step, rate):
    """Why ``rate`` is not a usable declared rate for ``step``, or None."""
    if rate is None:
        return "No rate is given for step %s, which charges a declared rate." % step
    try:
        r = _exact(rate)
    except (ValueError, ArithmeticError):
        return "The declared rate for step %s, %r, is not a number." % (step, rate)
    if r <= 0:
        return "The declared rate for step %s must be positive, not %s." % (step, _plain(r))
    if (r * 10 ** RATE_PLACES).denominator != 1:
        return ("The declared rate for step %s, %s, has more than %d decimal places."
                % (step, _plain(r), RATE_PLACES))
    return None


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
    for s in sorted(set(rule_steps)):
        if not (MIN_STEP <= s <= MAX_STEP):
            errs.append("Step %s must be between %d and %d." % (s, MIN_STEP, MAX_STEP))
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
                # RECIPROCAL: any other sender may receive.
    return list(dict.fromkeys(errs))


def assert_valid(rs):
    errs = validate(rs)
    if errs:
        raise CostAllocationError(errs)


def build_steps(rules, receivers):
    """Join rules and receivers into Steps ordered by step number.

    A rule may carry ``basis`` (default Share of Pool) and, for Declared Rate,
    ``rate``: the rate the caller resolved for the run's period (the Rule Set
    holds rates by step with validity windows; picking the window is the
    caller's job). The rate is kept exact (Fraction of its decimal text).
    """
    by_step = {}
    for rc in receivers:
        by_step.setdefault(rc["step"], []).append(rc["receiver_member"])
    return [
        Step(r["step"], r["sender_member"], r["driver"], list(by_step.get(r["step"], [])),
             basis=r.get("basis", SHARE_OF_POOL),
             rate=None if r.get("rate") is None else _exact(r["rate"]))
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


def units(rows):
    """{(step, receiver_member): Fraction}: the exact driver values themselves.

    The same rows as weights(). A Declared Rate step charges rate x units, so
    its coefficients are in these units, not in shares. Refuses a negative
    value; a step whose units are all zero is refused where it is used (a rate
    step by the coefficient functions, a share step by weights()).
    """
    out, errs = {}, []
    for r in rows:
        v = _exact(r["driver_value"])
        if v < 0:
            errs.append("Driver value for step %s, %s is negative." % (r["step"], r["receiver_member"]))
        out[(r["step"], r["receiver_member"])] = v
    if errs:
        raise CostAllocationError(errs)
    return out


# --- coefficients (K2b) ------------------------------------------------------
#
# A coefficient row says: of ORIGIN's pool, SENDER holds pool_coefficient at
# its step and gives `coefficient` of it to RECEIVER. dbt multiplies:
# x = origin_amount * coefficient, `sent` -x at the sender, `received` +x at
# the receiver. For a Share of Pool origin the pool is 1 unit and
# origin_amount is the origin's pool amount. For a Declared Rate origin (2b-2)
# the coefficients are in DRIVER UNITS: the own hop's pool is the step's total
# units, each receiver's coefficient is its units, and origin_amount is the
# rate, so x = rate x units exactly. A share sender passes rate-origin units
# on by its own weights, like any other cost. A rate sender's outflow is fixed
# by its rate, so cost it receives stops there, whatever its origin (under
# Reciprocal its own units that come back included): it emits no `pass` rows
# and the amount ends in its absorption variance (absorption()). hop_kind says
# whose cost the hop carries: `own` is the
# origin's own pool leaving the origin; `pass` is cost a sender received and
# passes on. Direct and Step-Down: `own` at the origin's hop, `pass` everywhere
# else. Reciprocal: cost can come back to the origin, so the origin's hop has
# an `own` row and, if anything came back, a `pass` row too. Never identify
# own cost by sender_member == origin_member.

COEFFICIENT_PLACES = 12
_SCALE = 10 ** COEFFICIENT_PLACES
_QUANTUM = Decimal(1).scaleb(-COEFFICIENT_PLACES)
COEFFICIENT_COLUMNS = ("run", "origin_member", "step", "sender_member", "receiver_member",
                       "driver", "hop_kind", "pool_coefficient", "coefficient")


def _weight(weights, st, recv):
    try:
        return weights[(st.step, recv)]
    except KeyError:
        raise CostAllocationError("No weight for step %s, receiver %s." % (st.step, recv))


def _hop(origin, st, pool, weights, hop_kind=None):
    if hop_kind is None:
        hop_kind = OWN if st.sender_member == origin else PASS
    return [{
        "origin_member": origin, "step": st.step, "sender_member": st.sender_member,
        "receiver_member": recv, "driver": st.driver, "hop_kind": hop_kind,
        "pool_coefficient": pool, "coefficient": pool * _weight(weights, st, recv),
    } for recv in st.receivers]


def own_pools(steps, units=None):
    """{sender_member: Fraction}: the pool each step's own hop carries.

    1 for a Share of Pool step. For a Declared Rate step, the step's total
    driver units from ``units`` ({(step, receiver): value}), so that each
    receiver's coefficient (pool x weight) is its own units. Refuses, all at
    once: an unknown basis; a rate step with no units, a receiver without a
    value, a negative value, or all values zero (a rate charges nothing); a
    missing, non-positive or over-precise rate (_rate_error).
    """
    errs, out = [], {}
    for st in sorted(steps, key=lambda s: s.step):
        if st.basis == SHARE_OF_POOL:
            out[st.sender_member] = Fraction(1)
            continue
        if st.basis != DECLARED_RATE:
            errs.append("Basis for step %s must be one of %s, not %r." % (st.step, ", ".join(BASES), st.basis))
            continue
        bad = _rate_error(st.step, st.rate)
        if bad:
            errs.append(bad)
        if units is None:
            errs.append("No driver units were given for step %s, which charges a declared rate." % st.step)
            continue
        missing = [r for r in st.receivers if (st.step, r) not in units]
        if missing:
            errs.append("Missing driver units for step %s: %s." % (st.step, ", ".join(missing)))
            continue
        vals = {r: _exact(units[(st.step, r)]) for r in st.receivers}
        negative = [r for r, v in vals.items() if v < 0]
        if negative:
            errs.append("Driver units for step %s are negative: %s." % (st.step, ", ".join(negative)))
            continue
        total = sum(vals.values(), Fraction(0))
        if total == 0:
            errs.append("All driver units for step %s are zero; a declared rate would charge nothing." % st.step)
            continue
        out[st.sender_member] = total
    if errs:
        raise CostAllocationError(errs)
    return out


def direct_coefficients(steps, weights, units=None):
    """Exact Fraction rows for Direct allocation.

    Each sender is an origin and gives its whole pool to its receivers by
    weight; hop_kind is `own` on every row. pool_coefficient is 1 for a Share
    of Pool step and the step's total driver units for a Declared Rate step
    (own_pools), whose receiver coefficients are then their units. Refuses a
    receiver that is a sender (Direct cost never passes through a second
    sender).
    """
    senders = {st.sender_member for st in steps}
    bad = ["%s receives at step %s but is a sender; Direct allocation does not allow it."
           % (r, st.step) for st in steps for r in st.receivers if r in senders]
    if bad:
        raise CostAllocationError(bad)
    pools = own_pools(steps, units)
    rows = []
    for st in sorted(steps, key=lambda s: s.step):
        rows.extend(_hop(st.sender_member, st, pools[st.sender_member], weights))
    return rows


def step_down_coefficients(steps, weights, units=None):
    """Exact Fraction rows for Step-Down allocation.

    In step order, each sender passes on its own pool plus whatever it received
    from earlier steps. Per origin O (every sender): O holds its own pool at
    its own step (1, or its total driver units for a Declared Rate step; see
    own_pools); a later Share of Pool sender S holds the sum of the
    coefficients it received from O at earlier steps, and gives that times its
    weights. A later Declared Rate sender holds what it received from O but
    emits no rows for it: its outflow is fixed by its rate, so O's cost stops
    there (a sink, reported in its absorption variance). A sender that holds
    nothing of O emits no rows for O. A sender receives nothing at or after
    its own step (refused): cost only flows forward. hop_kind is `own` at O's
    own step and `pass` at every later sender.
    """
    ordered = sorted(steps, key=lambda s: s.step)
    sender_step = {st.sender_member: st.step for st in ordered}
    bad = ["%s receives at step %s but sends at step %s; Step-Down cost only flows forward."
           % (r, st.step, sender_step[r])
           for st in ordered for r in st.receivers
           if r in sender_step and sender_step[r] <= st.step]
    if bad:
        raise CostAllocationError(bad)
    pools = own_pools(ordered, units)
    rows = []
    for i, origin_step in enumerate(ordered):
        origin = origin_step.sender_member
        held = {origin: pools[origin]}
        for st in ordered[i:]:
            pool = held.get(st.sender_member, Fraction(0))
            if pool == 0:
                continue
            if st.basis == DECLARED_RATE and st.sender_member != origin:
                continue  # a rate sender is a sink for every other origin
            for r in _hop(origin, st, pool, weights):
                held[r["receiver_member"]] = held.get(r["receiver_member"], Fraction(0)) + r["coefficient"]
                rows.append(r)
    return rows


def _sender_names(names):
    return ", ".join(sorted(names))


def reciprocal_coefficients(steps, weights, units=None):
    """Exact Fraction rows for Reciprocal allocation.

    The linear system is solved over the SHARE OF POOL senders only. A
    Declared Rate sender (2b-2 R4, plan section 3) is outside it: its outflow
    is its own hop in units (pool the step's total units, receiver
    coefficients their units; own_pools), and it is a sink for everything
    that reaches it, from any origin, its own returning units included. It
    emits no `pass` rows; what it receives ends in its absorption variance.

    For each origin O (every sender), x_S is the amount of O's own pool that
    share sender S holds and passes on, the exact solution of

        x_S = b_S + sum over share senders T of w(T -> S) * x_T

    where b_S is [S == O] for a share origin and O's own-hop coefficient to S
    (its units) for a rate origin, by Gauss-Jordan elimination in Fraction (no
    iteration, no tolerance). Rows have the step_down_coefficients shape plus
    hop_kind:
      - at O's own step, per receiver R: `own` with pool p (1, or the total
        units) and coefficient p * w(O -> R); and, for a share origin with
        x_O > 1, `pass` with pool x_O - 1 (the cost that came back to O);
      - at every other share sender S with x_S > 0, per receiver R: `pass`
        with pool x_S and coefficient x_S * w(S -> R);
      - nothing at a rate sender other than O.
    A zero-weight receiver still gets its (zero) row, as in Step-Down.

    Invariants, exact, for every origin O:
      - one `own` hop, at O, pool_coefficient == O's own pool;
      - every share sender S: outflow (`own` + `pass` coefficients sent by S)
        == inflow (coefficients S receives) + [S == O];
      - per hop (origin, sender, hop_kind): sum(coefficient) == pool_coefficient;
      - the coefficients reaching sinks (non-senders and rate senders) sum to
        O's own pool.
    With no rate step the rows are those of the share-only solve, unchanged
    (test_rate_reciprocal_share_only_output_unchanged pins them).

    Refuses a sender that receives its own allocation; every set of share
    senders with no positive-weight path to a sink (their cost could never
    leave, so the system has no solution), naming those senders; and, through
    own_pools, every rate-step problem (basis, rate, units) at once.
    """
    ordered = sorted(steps, key=lambda s: s.step)
    bad = ["%s cannot receive its own allocation (step %s)." % (st.sender_member, st.step)
           for st in ordered if st.sender_member in st.receivers]
    if bad:
        raise CostAllocationError(bad)
    pools = own_pools(ordered, units)
    share = [st.sender_member for st in ordered if st.basis == SHARE_OF_POOL]
    index = {m: i for i, m in enumerate(share)}
    w = {(st.sender_member, r): _weight(weights, st, r) for st in ordered for r in st.receivers}

    exits, changed = set(), True
    while changed:
        changed = False
        for st in ordered:
            s = st.sender_member
            if s in index and s not in exits and any(w[(s, r)] > 0 and (r not in index or r in exits)
                                                     for r in st.receivers):
                exits.add(s)
                changed = True
    stuck = [m for m in share if m not in exits]
    if stuck:
        raise CostAllocationError(
            "Reciprocal cost can never reach a final receiver from: %s. "
            "Give at least one of them a receiver that is not a %s sender."
            % (_sender_names(stuck), SHARE_OF_POOL))

    # (I - A) X = B over the share senders, A[s][t] = w(t -> s); column j of B
    # (and of X) is origin ordered[j]: e_o for a share origin, the own-hop
    # coefficients to the share senders for a rate origin.
    n = len(share)

    def b(i, st_o):
        if st_o.basis == SHARE_OF_POOL:
            return Fraction(int(i == index[st_o.sender_member]))
        return pools[st_o.sender_member] * w.get((st_o.sender_member, share[i]), Fraction(0))

    mat = [[Fraction(int(i == j)) for j in range(n)] + [b(i, st_o) for st_o in ordered]
           for i in range(n)]
    for (t, s), v in w.items():
        if s in index and t in index:
            mat[index[s]][index[t]] -= v
    for c in range(n):
        p = next((r for r in range(c, n) if mat[r][c] != 0), None)
        if p is None:
            raise CostAllocationError(
                "The reciprocal system has no unique solution for: %s." % _sender_names(share))
        mat[c], mat[p] = mat[p], mat[c]
        piv = mat[c][c]
        mat[c] = [v / piv for v in mat[c]]
        for r in range(n):
            if r != c and mat[r][c] != 0:
                f = mat[r][c]
                mat[r] = [a - f * b for a, b in zip(mat[r], mat[c])]

    rows = []
    for j, st_o in enumerate(ordered):
        o = st_o.sender_member
        x = {m: mat[index[m]][n + j] for m in share}
        for st in ordered:
            s = st.sender_member
            if s == o:
                rows.extend(_hop(o, st, pools[o], weights, OWN))
                if s in index and x[s] > 1:
                    rows.extend(_hop(o, st, x[s] - 1, weights, PASS))
            elif s in index and x[s] > 0:
                rows.extend(_hop(o, st, x[s], weights, PASS))
            # a rate sender other than o is a sink: no rows
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
      - at the origin's own hop the pool is exactly the exact row's pool: 1
        for a Share of Pool origin, the total driver units for a Declared
        Rate origin (refused if that has more than 12 decimal places, so the
        unit count is an integer; driver values carry at most 9);
      - at a later sender the pool is the exact sum of the ROUNDED coefficients
        it received from that origin (so its `received` and `sent` lines net to
        zero in dbt);
      - each hop's pool is split over its receivers by largest remainder of
        pool * weight, so the receiver coefficients sum exactly to the pool.
    Hence, per origin, the rounded coefficients reaching its sinks sum to
    exactly its own pool. Each rounded value is within (number of hops) *
    1e-12 of exact; a rate origin's own coefficients are exact (units with
    <= 12 places). hop_kind is carried through unchanged.

    Direct and Step-Down only: cost never returns to its origin, so pools can
    be rounded in step order. A `pass` hop at the origin (Reciprocal) raises
    ValueError; those rows go through round_reciprocal_coefficients.
    """
    out, held, pending, hop = [], {}, [], None

    def flush():
        if not pending:
            return
        r0 = pending[0]
        o = r0["origin_member"]
        if r0["hop_kind"] == OWN:
            scaled = Fraction(r0["pool_coefficient"]) * _SCALE
            if scaled.denominator != 1:
                raise CostAllocationError(
                    "The own pool of origin %s (%s) has more than %d decimal places and cannot "
                    "be rounded exactly." % (o, _plain(Fraction(r0["pool_coefficient"])), COEFFICIENT_PLACES))
            units = int(scaled)
        elif r0["sender_member"] == o:
            raise ValueError("Cost returns to origin %s (Reciprocal); "
                             "use round_reciprocal_coefficients." % o)
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
        key = (r["origin_member"], r["step"], r["sender_member"], r["hop_kind"])
        if key != hop:
            flush()
            hop = key
        pending.append(r)
    flush()
    return out


def _units(v):
    return Decimal(v).scaleb(-COEFFICIENT_PLACES).quantize(_QUANTUM)


def round_reciprocal_coefficients(exact_rows, rate_senders=()):
    """Round reciprocal_coefficients rows to 12 dp, as Decimals, exactly.

    Cost goes round in cycles, so pools cannot be rounded in step order. The
    rows of one origin O are instead rounded together, as a flow, in integer
    units of 1e-12. Nodes: a source; O's own node (`own` rows leave it); one
    `pass` node per share sender (every coefficient a share sender receives
    arrives there, its `pass` rows leave it); one sink node per final receiver
    and per Declared Rate sender named in ``rate_senders`` (2b-2 R4: cost
    stops there, O's own included). Edges: source -> own(O) carrying O's own
    pool in units (10^12 for a share origin; the total driver units x 10^12
    for a rate origin, refused if that is not whole, which units of <= 12
    places never are); one edge per row, carrying 10^12 * its exact
    coefficient; sink -> source carrying what it receives. The exact values
    balance every node, so this is a circulation. A member of ``rate_senders``
    with a `pass` row is refused: the rows contradict the basis.

    Rounding: while some edge is fractional, take the smallest fractional
    edge (by node names), close a cycle of fractional edges through it (a
    breadth-first search, neighbours in name order) and push flow round the
    cycle until one of its edges is a whole number, in whichever direction
    needs the smaller push (forward on a tie). Such a cycle always exists: a
    node's fractional edges cannot number exactly one (its whole-number edges
    and its balance are integers), and a fractional edge cannot be a bridge
    of the fractional graph (the flow across the cut it alone would cross is
    zero, so it would be whole). A push keeps every node balanced and never
    moves an edge past an integer, so each edge stops at floor or ceil of its
    exact value. Hence, exactly, per origin:
      - the `own` hop's pool is O's own pool (the source edge, never touched);
        a rate origin's own coefficients are whole units, so they are exact;
      - every share sender's `own` + `pass` outflow equals what it received
        from O (+1 at O), so a service department nets to exactly 0;
      - the sinks get exactly O's own pool;
      - per hop (origin, sender, hop_kind), pool_coefficient is defined as the
        sum of its coefficients;
      - no coefficient is negative, and each is strictly within 1e-12 of the
        exact Fraction (equal when the exact value has <= 12 places); a pool
        is within (its receiver count) x 1e-12.
    A sender whose only exits are other service departments needs no special
    case: there is no per-sender remainder, only balanced nodes, and its
    node is balanced like any other. Deterministic: only member names, kinds
    and exact values are used, never step numbers or declaration order.
    Rows keep their order and every column; zero-weight rows stay at zero.
    """
    rate_senders = set(rate_senders)
    passing = sorted({r["sender_member"] for r in exact_rows
                      if r["hop_kind"] == PASS and r["sender_member"] in rate_senders})
    if passing:
        raise CostAllocationError("Declared Rate senders pass cost on in these rows, which they cannot: %s."
                                  % ", ".join(passing))
    senders = {r["sender_member"] for r in exact_rows} - rate_senders
    by_origin = {}
    for r in exact_rows:
        by_origin.setdefault(r["origin_member"], []).append(r)
    units = {}
    for o, rows in by_origin.items():
        units.update(_round_circulation(o, rows, senders))
    pools = {}
    for r in exact_rows:
        k = (r["origin_member"], r["sender_member"], r["hop_kind"])
        pools[k] = pools.get(k, 0) + units[id(r)]
    return [dict(r, pool_coefficient=_units(pools[(r["origin_member"], r["sender_member"], r["hop_kind"])]),
                 coefficient=_units(units[id(r)])) for r in exact_rows]


def _round_circulation(origin, rows, senders):
    """{id(row): integer units} for one origin's rows; see round_reciprocal_coefficients.

    ``senders`` are the members that pass cost on (share senders); any other
    receiver is a sink node.
    """
    src = ("0 source", "")
    node_in = lambda m: ("2 pass", m) if m in senders else ("3 final", m)
    own_pool = Fraction(next(r["pool_coefficient"] for r in rows if r["hop_kind"] == OWN)) * _SCALE
    if own_pool.denominator != 1:
        raise CostAllocationError(
            "The own pool of origin %s (%s) has more than %d decimal places and cannot be rounded "
            "exactly." % (origin, _plain(own_pool / _SCALE), COEFFICIENT_PLACES))
    flow, edge_of = {(src, ("1 own", origin)): own_pool}, {}
    for r in rows:
        tail = ("1 own", origin) if r["hop_kind"] == OWN else ("2 pass", r["sender_member"])
        e = (tail, node_in(r["receiver_member"]))
        if e in flow:
            raise CostAllocationError("Duplicate coefficient row for origin %s, %s to %s."
                                      % (origin, r["sender_member"], r["receiver_member"]))
        flow[e] = Fraction(r["coefficient"]) * _SCALE
        edge_of[id(r)] = e
        if e[1][0] == "3 final":
            back = (e[1], src)
            flow[back] = flow.get(back, Fraction(0)) + flow[e]
    # RF3: the same cycle as RC2, without RC2's work. RC2 re-sorted every
    # fractional edge and ran a fresh breadth-first search each pass. Here the
    # adjacency is built once: edges only ever leave the fractional set, so the
    # name order of the fractional edges, and of each node's incident edges,
    # never changes; edges and nodes are numbered in that order, and an edge
    # that has become whole is dropped from a node's list when the list is next
    # read. The search visits nodes in RC2's queue order and stops at the first
    # node it reaches that is adjacent to the goal. RC2's search would give the
    # goal that node as its parent: no node reached earlier is adjacent to the
    # goal, and the nodes of one level are reached in queue order. The edge is
    # the lowest-numbered one between them, the first in that node's list. No
    # other parent on the path depends on what RC2 found after that point, so
    # the cycle and the output are identical
    # (test_reciprocal_rounding_equals_rc2_reference_*).
    order = sorted(e for e, v in flow.items() if v.denominator != 1)
    node_id = {}
    for e in order:
        node_id.setdefault(e[0], len(node_id))
        node_id.setdefault(e[1], len(node_id))
    tails = [node_id[e[0]] for e in order]
    heads = [node_id[e[1]] for e in order]
    # Flows as integer numerators over one common denominator: the same
    # exact arithmetic as Fractions, without their cost.
    den = math.lcm(*(flow[e].denominator for e in order)) if order else 1
    val = [flow[e].numerator * (den // flow[e].denominator) for e in order]
    live = bytearray(b"\x01") * len(order)
    adj = [[] for _ in node_id]
    for i in range(len(order)):
        adj[tails[i]].append((heads[i], i, 1))
        adj[heads[i]].append((tails[i], i, -1))

    def live_list(u):
        near = adj[u]
        ok = [x for x in near if live[x[1]]]
        if len(ok) != len(near):
            adj[u] = ok
        return ok

    seen = [-1] * len(node_id)   # pass number in which a node was reached
    prev = [None] * len(node_id)
    first, left, stamp = 0, len(order), 0
    while left:
        while not live[first]:
            first += 1
        start = first  # the smallest fractional edge by node names
        stamp += 1
        # Path from start's head back to its tail over the other fractional edges.
        goal, h = tails[start], heads[start]
        into_goal = {}  # neighbour -> (lowest edge to the goal, direction from the neighbour)
        for v, i, d in live_list(goal):
            if i != start and v not in into_goal:
                into_goal[v] = (i, -d)
        seen[h], prev[h] = stamp, None
        queue, found = deque([h]), h if h in into_goal else None
        while queue and found is None:
            u = queue.popleft()
            for v, i, d in live_list(u):
                if i != start and seen[v] != stamp:
                    seen[v], prev[v] = stamp, (u, i, d)
                    if v in into_goal:
                        found = v
                        break
                    queue.append(v)
        if found is None:  # impossible for a balanced flow; never round silently
            raise CostAllocationError("Reciprocal rounding found unbalanced coefficients "
                                      "for origin %s." % origin)
        prev[goal] = (found,) + into_goal[found]
        cycle, v = [(start, 1)], goal
        while prev[v] is not None:
            u, i, d = prev[v]
            cycle.append((i, d))
            v = u
        # Every edge on the cycle is fractional: 0 < val % den < den.
        up = min((den - val[i] % den) if d > 0 else (val[i] % den) for i, d in cycle)
        down = min((val[i] % den) if d > 0 else (den - val[i] % den) for i, d in cycle)
        t = up if up <= down else -down
        for i, d in cycle:
            val[i] += t * d
            if val[i] % den == 0:
                live[i] = 0
                left -= 1
    for e, v in zip(order, val):
        flow[e] = Fraction(v // den)
    return {rid: int(flow[e]) for rid, e in edge_of.items()}


def coefficient_rows(run_name, steps, weights, method, units=None):
    """Rows for epm_staging.cost_allocation_coefficients, 12-dp Decimals.

    ``weights`` ({(step, receiver): Fraction}, each step summing to 1) are
    needed for every step; ``units`` ({(step, receiver): value}, the driver
    values themselves, see units()) only for Declared Rate steps.

    Columns, in order: COEFFICIENT_COLUMNS (run, origin_member, step,
    sender_member, receiver_member, driver, hop_kind, pool_coefficient,
    coefficient). Invariants, every method (the warehouse tests rely on them):
      T5  per (run, origin, sender, hop_kind): sum(coefficient) == pool_coefficient;
      own per origin: one `own` hop, at the origin, pool_coefficient == the
          origin's own pool (1 for Share of Pool, total driver units for
          Declared Rate);
      pass per origin and share sender S: S's `own` + `pass` outflow == the
          coefficients S received from that origin (+ own pool at the origin);
          a Declared Rate sender passes nothing on, its own returning units
          included (Reciprocal);
      T1  per origin: sum(coefficient) over receivers that are sinks (not
          senders, or Declared Rate senders) == the origin's own pool.
    All exact in Decimal, so origin_amount * coefficient conserves to the cent.
    Direct and Step-Down round hop by hop in step order (round_coefficients);
    Reciprocal rounds each origin as one flow (round_reciprocal_coefficients).
    """
    errs = []
    for st in steps:
        ws = [_weight(weights, st, r) for r in st.receivers]
        if any(x < 0 for x in ws) or sum(ws, Fraction(0)) != 1:
            errs.append("Weights for step %s must be non-negative and sum to exactly 1." % st.step)
    if errs:
        raise CostAllocationError(errs)
    if method == DIRECT:
        exact = direct_coefficients(steps, weights, units)
    elif method == STEP_DOWN:
        exact = step_down_coefficients(steps, weights, units)
    elif method == RECIPROCAL:
        exact = reciprocal_coefficients(steps, weights, units)
    else:
        raise CostAllocationError("Method must be one of %s, not %r." % (", ".join(METHODS), method))
    if method == RECIPROCAL:
        rounded = round_reciprocal_coefficients(
            exact, rate_senders={st.sender_member for st in steps if st.basis == DECLARED_RATE})
    else:
        rounded = round_coefficients(exact, weights)
    return [{"run": run_name, **{c: r[c] for c in COEFFICIENT_COLUMNS[1:]}} for r in rounded]


def absorption(pool_by_sender, received_by_sender, charged_by_sender):
    """{rate sender: Fraction variance}, exact: variance = pool + received - charged.

    pool is the sender's own cost, received what was allocated to it (other
    origins' cost and, under Reciprocal, its own units that came back; it all
    stops there), charged rate x units sent. Positive is under-absorbed
    (cost left on the sender), negative over-absorbed. Keys of received and
    charged must be rate senders named in pool_by_sender; a sender absent from
    received or charged has 0 of it (nothing was allocated or charged, which
    the coefficient rows establish). Decided Q5 A (27 Nov 2026): reported per
    sender, never re-spread.
    """
    unknown = sorted((set(received_by_sender) | set(charged_by_sender)) - set(pool_by_sender))
    if unknown:
        raise CostAllocationError("Received or charged amounts for senders with no pool: %s."
                                  % ", ".join(unknown))
    return {s: _exact(pool) + _exact(received_by_sender.get(s, 0)) - _exact(charged_by_sender.get(s, 0))
            for s, pool in pool_by_sender.items()}


# -- statistic CSV (K5b) -------------------------------------------------------

STATISTIC_HEADER = ("fiscal_period", "member", "value")
STATISTIC_MAX_ROWS = 20000
_DECIMAL = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)")
_INTEGER = re.compile(r"[+-]?\d+")


def parse_statistic_csv(text):
    """Parse a statistic file into [{fiscal_period, member, value}], all or
    nothing: every bad row is reported at once, by its row number (the header
    is row 1). `value` is a Decimal built from the exact text, so 10.10 stays
    10.10. A UTF-8 byte-order mark and CRLF line ends are accepted."""
    text = text.lstrip("\ufeff")
    if not text.strip():
        raise CostAllocationError("The file is empty.")
    lines = list(csv.reader(io.StringIO(text, newline="")))
    header = tuple(c.strip().lower() for c in lines[0])
    if header != STATISTIC_HEADER:
        raise CostAllocationError(
            "The header must be exactly fiscal_period,member,value; "
            f"found {','.join(c.strip() for c in lines[0])}.")
    errors, rows, seen = [], [], 0
    for number, cells in enumerate(lines[1:], start=2):
        if not any(c.strip() for c in cells):
            continue
        seen += 1
        if seen > STATISTIC_MAX_ROWS:
            errors.append(
                f"More than {STATISTIC_MAX_ROWS:,} data rows (from row {number}); "
                "split the statistic by entity, scenario or year.")
            break
        if len(cells) != 3:
            errors.append(f"Row {number}: expected 3 columns, found {len(cells)}.")
            continue
        period, member, value = (c.strip() for c in cells)
        problems = []
        if not _INTEGER.fullmatch(period):
            problems.append(f"fiscal_period {period!r} is not a whole number")
        if not member:
            problems.append("member is blank")
        if not _DECIMAL.fullmatch(value):
            problems.append(f"value {value!r} is not a decimal number")
        elif Decimal(value) < 0:
            problems.append(f"value {value} is negative")
        if problems:
            errors.append(f"Row {number}: " + "; ".join(problems) + ".")
            continue
        rows.append({"fiscal_period": int(period), "member": member, "value": Decimal(value)})
    if errors:
        raise CostAllocationError(errors)
    if not rows:
        raise CostAllocationError("The file has a header but no data rows.")
    return rows


# --- driver sources (2b-1) ---------------------------------------------------
#
# A driver's values come from one of three sources: an uploaded statistic
# (resolve_driver_values), fixed weights with validity windows, or an account
# set derived from the scenario trial balance. Every source ends as the same
# {member: value} rows that weights() normalises, so the three methods and the
# warehouse do not care which source filled them.

UPLOADED = "Uploaded Statistic"
FIXED = "Fixed Weights"
ACCOUNT_SET = "Account Set"
SOURCES = (UPLOADED, FIXED, ACCOUNT_SET)
AS_BOOKED = "As Booked"
REVERSED = "Reversed"
VALUE_SIGNS = (AS_BOOKED, REVERSED)
#: the scenario trial balance names its dimension columns dim_<name>; the same
#: guard as schema_apply (\Z, not $: "$" matches before a trailing newline).
_DIM_COLUMN = re.compile(r"^dim_[a-z0-9_]+\Z")
DERIVED_TABLE = "epm_gold.gold_scenario_trial_balance"


def fixed_windows(rows, receivers, period_start, period_end):
    """{receiver: row} — the fixed-weight row whose window covers the WHOLE
    period [period_start, period_end], per receiver.

    rows: {member, value, effective_from, effective_to}; effective_to None is
    open-ended. Dates are datetime.date. Decided Q1 A (Deepak Pai, 27 Nov
    2026, konsol#269): a window must cover the whole period, and a window that
    starts or ends inside the period is a change inside the period, which
    refuses even when another window covers; blending by days was rejected.

    Raises one CostAllocationError listing every receiver (sorted) with no
    covering window, with a window changing inside the period, or with more
    than one covering window. Rows of members that do not receive are ignored.
    """
    if period_start > period_end:
        raise CostAllocationError("Period start %s is after its end %s." % (period_start, period_end))
    by_member = {}
    for r in rows:
        by_member.setdefault(r["member"], []).append(r)
    errs, out = [], {}
    for m in sorted(set(receivers)):
        covering, inside = [], []
        for r in by_member.get(m, ()):
            start, end = r["effective_from"], r.get("effective_to")
            if period_start < start <= period_end:
                inside.append(start)
            if end is not None and period_start <= end < period_end:
                inside.append(end)
            if start <= period_start and (end is None or end >= period_end):
                covering.append(r)
        if inside:
            errs.append("%s: a fixed weight window changes inside the period %s to %s (on %s); "
                        "a window must cover the whole period."
                        % (m, period_start, period_end, ", ".join(str(d) for d in sorted(inside))))
        elif not covering:
            errs.append("%s: no fixed weight covers the period %s to %s." % (m, period_start, period_end))
        elif len(covering) > 1:
            errs.append("%s: more than one fixed weight window covers the period %s to %s (from %s)."
                        % (m, period_start, period_end,
                           ", ".join(str(r["effective_from"]) for r in covering)))
        else:
            out[m] = covering[0]
    if errs:
        raise CostAllocationError(errs)
    return out


def fixed_values(rows, receivers, period_start, period_end):
    """{receiver: Fraction} from the fixed-weight window covering the period;
    see fixed_windows for the rules and refusals. Values feed weights()."""
    return {m: _exact(r["value"]) for m, r in fixed_windows(rows, receivers, period_start, period_end).items()}


def derived_values(sums, receivers, sign):
    """{receiver: Fraction} from warehouse sums {member: (total, row_count)}.

    A receiver absent from sums, or with no rows, is missing; a total of 0
    over rows is a real zero. sign is As Booked or Reversed (Reversed negates,
    so a credit balance such as revenue drives positively). Decided Q2 A
    (Deepak Pai, 27 Nov 2026, konsol#269): a value negative after the sign
    refuses, naming the member and the value; taking the absolute value was
    rejected. One CostAllocationError lists every problem, receivers sorted.
    """
    if sign not in VALUE_SIGNS:
        raise CostAllocationError("Value sign must be %s, not %r." % (" or ".join(VALUE_SIGNS), sign))
    missing, negative, out = [], [], {}
    for m in sorted(set(receivers)):
        total, n = sums.get(m, (0, 0))
        if not n:
            missing.append(m)
            continue
        v = _exact(total)
        if sign == REVERSED:
            v = -v
        if v < 0:
            negative.append("%s is negative (%s %s)." % (m, _plain(v), sign))
            continue
        out[m] = v
    errs = []
    if missing:
        errs.append("Missing derived values: no trial balance rows for %s." % ", ".join(missing))
    errs.extend(negative)
    if errs:
        raise CostAllocationError(errs)
    return out


def _plain(fraction):
    """A Fraction as a plain decimal string for messages (exact: inputs came from decimals)."""
    d = Decimal(fraction.numerator) / Decimal(fraction.denominator)
    s = format(d, "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def array_literal(values):
    """A ClickHouse Array(String) literal for an HTTP query parameter."""
    return "[" + ",".join("'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'" for v in values) + "]"


def derived_query(entity, scenario, fiscal_year, fiscal_period, dimension_column, members, accounts):
    """(sql, params) summing the scenario trial balance per receiver member.

    One row per member found: ``member, total, row_count`` (TabSeparated), to
    feed derived_values as {member: (total, row_count)}. Reads
    gold_scenario_trial_balance for the run's scenario, entity, year and
    period, the same columns as the sender-pool query (scenario_id,
    data_area_id, fiscal_year, fiscal_period, main_account, amount).

    ``accounts`` must already be expanded to descendants: the caller resolves
    each group account's tree, this function takes the flat list as given.
    Every value travels as a ClickHouse query parameter; the one interpolated
    identifier, dimension_column, is refused unless it matches dim_[a-z0-9_]+.
    Empty members or accounts refuse (the query could only report everything
    missing).
    """
    errs = []
    if not isinstance(dimension_column, str) or not _DIM_COLUMN.match(dimension_column):
        errs.append("Dimension column %r is not a dim_ identifier (dim_ followed by lower-case "
                    "letters, digits or underscores)." % (dimension_column,))
    if not members:
        errs.append("No receiver members to derive values for.")
    if not accounts:
        errs.append("No accounts to derive values from.")
    if errs:
        raise CostAllocationError(errs)
    sql = (
        f"SELECT `{dimension_column}` AS member, sum(amount) AS total, count() AS row_count "
        f"FROM {DERIVED_TABLE} "
        "WHERE scenario_id = {scenario:String} AND data_area_id = {entity:String} "
        "AND fiscal_year = {fiscal_year:UInt16} AND fiscal_period = {fiscal_period:UInt8} "
        "AND main_account IN {accounts:Array(String)} "
        f"AND `{dimension_column}` IN {{members:Array(String)}} "
        f"GROUP BY `{dimension_column}` ORDER BY member FORMAT TabSeparated"
    )
    params = {
        "param_scenario": scenario, "param_entity": entity,
        "param_fiscal_year": str(fiscal_year), "param_fiscal_period": str(fiscal_period),
        "param_members": array_literal(members), "param_accounts": array_literal(accounts),
    }
    return sql, params


# --- declared rates: the basis of a step and the rates on the rule set --------
# konsol#269 phase 2b-2 (Q5 = 5B, Deepak Pai 13 Nov 2026; Q1, Q4 and Q6 = A,
# 27 Nov 2026). A Share of Pool step splits its sender's whole pool; a Declared
# Rate step charges rate x driver units and reports the difference as absorption
# variance. Rates live on the Rule Set by step, in dated windows that may not
# overlap; a Declared Rate step is refused unless receiver accounts are a
# Declared Allocation Account, because the charge needs an account to credit
# the sender on. Validation only: the coefficient functions above are untouched.

# SHARE_OF_POOL, DECLARED_RATE, BASES and RATE_PLACES are defined at the top.


def _as_date(value):
    """A date from what Frappe hands a Date field (a date, a datetime or an
    ISO string); None when blank."""
    import datetime

    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def _rate_window(row):
    return "%s–%s" % (row.get("effective_from") or "?", row.get("effective_to") or "open")


def _plain_number(value):
    text = str(value)
    return text[:-2] if text.endswith(".0") else text


def _decimal_places(value):
    exponent = Decimal(str(value)).as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def rate_errors(rules, rates, receiver_account_mode):
    """Every problem with the rules' bases and the rate rows, as messages
    ([] when none). Pure: ``rules`` are {step, sender_member, basis} dicts,
    ``rates`` are {step, rate, effective_from, effective_to} dicts.

    A rule without a basis counts as Share of Pool, the field's default (rows
    written before 2b-2 carry none until the patch stamps them).
    """
    errs = []
    basis_by_step, sender_by_step = {}, {}
    for r in rules:
        step = r["step"]
        basis = r.get("basis") or SHARE_OF_POOL
        sender_by_step.setdefault(step, r.get("sender_member"))
        basis_by_step.setdefault(step, basis)
        who = "Step %s (%s)" % (step, r.get("sender_member"))
        if basis not in BASES:
            errs.append("%s: Basis must be one of %s, not %r." % (who, ", ".join(BASES), basis))
            continue
        if basis != DECLARED_RATE:
            continue
        if receiver_account_mode != DECLARED_ACCOUNT:
            errs.append(
                "%s is Declared Rate, but receiver accounts are %s; the charge needs an account "
                "to credit the sender on. Set Receiver Account Mode to %s."
                % (who, OWN_ACCOUNTS, DECLARED_ACCOUNT))
        if not any(x["step"] == step for x in rates):
            errs.append("%s is Declared Rate but has no rate; add at least one Rates row for the step."
                        % who)

    for x in rates:
        step = x["step"]
        where = "Rates row for step %s (%s)" % (step, _rate_window(x))
        if step not in basis_by_step:
            errs.append("%s: step %s has no rule." % (where, step))
        elif basis_by_step[step] == SHARE_OF_POOL:
            errs.append("%s: step %s (%s) is Share of Pool; a rate applies only to a Declared Rate step."
                        % (where, step, sender_by_step[step]))
        rate = x.get("rate")
        if rate in (None, "") or float(rate) <= 0:
            errs.append("%s: Rate must be greater than 0, not %s." % (where, _plain_number(rate or 0)))
        elif _decimal_places(rate) > RATE_PLACES:
            errs.append("%s: Rate %s has more than %d decimal places."
                        % (where, _plain_number(rate), RATE_PLACES))
        frm, to = _as_date(x.get("effective_from")), _as_date(x.get("effective_to"))
        if frm is None:
            errs.append("%s: Effective From is required." % where)
        elif to is not None and to < frm:
            errs.append("%s: Effective To is before Effective From." % where)

    by_step = {}
    for x in rates:
        if _as_date(x.get("effective_from")) is not None:
            by_step.setdefault(x["step"], []).append(x)
    for step in sorted(by_step, key=str):
        mine = sorted(by_step[step], key=lambda x: _as_date(x["effective_from"]))
        for earlier, later in zip(mine, mine[1:]):
            end = _as_date(earlier.get("effective_to"))
            if end is None or end >= _as_date(later["effective_from"]):
                errs.append("Step %s: overlapping rate windows %s and %s."
                            % (step, _rate_window(earlier), _rate_window(later)))
    return list(dict.fromkeys(errs))


# -- 2b-2 R5: the rate for a run period -----------------------------------------
#
# Compute picks, per Declared Rate step, the one Rates row whose window covers
# the whole run period (Q1 A, Deepak Pai, 27 Nov 2026, konsol#269: a window
# must cover the whole period and a change inside a period refuses; blending by
# days was rejected). The same rule as fixed_windows, for one step's rows.


def rate_for_period(rates, step, period_start, period_end):
    """The Rates row of ``step`` whose window covers the WHOLE period
    [period_start, period_end]: {step, rate, effective_from, effective_to},
    with the dates as datetime.date (effective_to None is open-ended) and the
    rate as given. Rows of other steps are ignored. Dates may be dates,
    datetimes or ISO strings (_as_date).

    Raises one CostAllocationError: the step has no covering window, a window
    starts or ends inside the period (a change inside the period, which
    refuses even when another window covers), or more than one window covers.
    """
    start, end = _as_date(period_start), _as_date(period_end)
    if start > end:
        raise CostAllocationError("Period start %s is after its end %s." % (start, end))
    covering, inside = [], []
    for r in rates:
        if r["step"] != step:
            continue
        frm, to = _as_date(r.get("effective_from")), _as_date(r.get("effective_to"))
        if frm is None:
            continue   # the rule set refuses this row at its own save (rate_errors)
        if start < frm <= end:
            inside.append(frm)
        if to is not None and start <= to < end:
            inside.append(to)
        if frm <= start and (to is None or to >= end):
            covering.append({"step": step, "rate": r.get("rate"), "effective_from": frm, "effective_to": to})
    if inside:
        raise CostAllocationError(
            "Step %s: the declared rate changes inside the period %s to %s (on %s); "
            "a rate window must cover the whole period."
            % (step, start, end, ", ".join(str(d) for d in sorted(inside))))
    if not covering:
        raise CostAllocationError("Step %s: no declared rate covers the period %s to %s." % (step, start, end))
    if len(covering) > 1:
        raise CostAllocationError(
            "Step %s: more than one rate window covers the period %s to %s (from %s)."
            % (step, start, end, ", ".join(str(r["effective_from"]) for r in covering)))
    return covering[0]
