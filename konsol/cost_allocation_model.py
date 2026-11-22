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
# `received` +x at the receiver. hop_kind says whose cost the hop carries:
# `own` is the origin's own unit leaving the origin; `pass` is cost a sender
# received and passes on. Direct and Step-Down: `own` at the origin's hop,
# `pass` everywhere else. Reciprocal: cost can come back to the origin, so the
# origin's hop has an `own` row and, if anything came back, a `pass` row too.
# Never identify own cost by sender_member == origin_member.

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


def direct_coefficients(steps, weights):
    """Exact Fraction rows for Direct allocation.

    Each sender is an origin and gives its whole unit pool to its receivers by
    weight; pool_coefficient is 1 and hop_kind is `own` on every row. Refuses a receiver that is a
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
    own step (refused): cost only flows forward. hop_kind is `own` at O's own
    step and `pass` at every later sender.
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


def _sender_names(names):
    return ", ".join(sorted(names))


def reciprocal_coefficients(steps, weights):
    """Exact Fraction rows for Reciprocal allocation.

    For each origin O (every sender), x_S is the share of O's unit that sender
    S holds and passes on, the exact solution of

        x_S = [S == O] + sum over senders T of w(T -> S) * x_T

    over the sender set, by Gaussian elimination in Fraction (no iteration, no
    tolerance). Rows have the step_down_coefficients shape plus hop_kind:
      - at O's own step, per receiver R: `own` with pool 1 and coefficient
        w(O -> R); and, if x_O > 1, `pass` with pool x_O - 1 and coefficient
        (x_O - 1) * w(O -> R) (the cost that came back to O);
      - at every other sender S with x_S > 0, per receiver R: `pass` with pool
        x_S and coefficient x_S * w(S -> R).
    A zero-weight receiver still gets its (zero) row, as in Step-Down.

    Invariants, exact, for every origin O:
      - sum of `own` pool_coefficient == 1 (one own hop, at O);
      - every sender S: outflow (`own` + `pass` coefficients sent by S) ==
        inflow (coefficients S receives) + [S == O];
      - per hop (origin, sender, hop_kind): sum(coefficient) == pool_coefficient;
      - the coefficients reaching final receivers (non-senders) sum to 1.

    Refuses a sender that receives its own allocation, and every set of
    senders with no positive-weight path to a final receiver (their cost could
    never leave, so the system has no solution), naming those senders.
    """
    ordered = sorted(steps, key=lambda s: s.step)
    senders = [st.sender_member for st in ordered]
    index = {m: i for i, m in enumerate(senders)}
    bad = ["%s cannot receive its own allocation (step %s)." % (st.sender_member, st.step)
           for st in ordered if st.sender_member in st.receivers]
    if bad:
        raise CostAllocationError(bad)
    w = {(st.sender_member, r): _weight(weights, st, r) for st in ordered for r in st.receivers}

    exits, changed = set(), True
    while changed:
        changed = False
        for st in ordered:
            s = st.sender_member
            if s not in exits and any(w[(s, r)] > 0 and (r not in index or r in exits)
                                      for r in st.receivers):
                exits.add(s)
                changed = True
    stuck = [m for m in senders if m not in exits]
    if stuck:
        raise CostAllocationError(
            "Reciprocal cost can never reach a final receiver from: %s. "
            "Give at least one of them a receiver that is not a sender." % _sender_names(stuck))

    # (I - A) X = I, A[s][t] = w(t -> s); column o of X is origin o's x.
    n = len(senders)
    mat = [[Fraction(int(i == j)) for j in range(n)] + [Fraction(int(i == k)) for k in range(n)]
           for i in range(n)]
    for (t, s), v in w.items():
        if s in index:
            mat[index[s]][index[t]] -= v
    for c in range(n):
        p = next((r for r in range(c, n) if mat[r][c] != 0), None)
        if p is None:
            raise CostAllocationError(
                "The reciprocal system has no unique solution for: %s." % _sender_names(senders))
        mat[c], mat[p] = mat[p], mat[c]
        piv = mat[c][c]
        mat[c] = [v / piv for v in mat[c]]
        for r in range(n):
            if r != c and mat[r][c] != 0:
                f = mat[r][c]
                mat[r] = [a - f * b for a, b in zip(mat[r], mat[c])]

    rows = []
    for st_o in ordered:
        o = st_o.sender_member
        x = {m: mat[index[m]][n + index[o]] for m in senders}
        for st in ordered:
            xs = x[st.sender_member]
            if st.sender_member == o:
                rows.extend(_hop(o, st, Fraction(1), weights, OWN))
                if xs > 1:
                    rows.extend(_hop(o, st, xs - 1, weights, PASS))
            elif xs > 0:
                rows.extend(_hop(o, st, xs, weights, PASS))
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
    hop_kind is carried through unchanged; an `own` hop has pool exactly 1.

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
            units = _SCALE
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


def round_reciprocal_coefficients(exact_rows):
    """Round reciprocal_coefficients rows to 12 dp, as Decimals, exactly.

    Cost goes round in cycles, so pools cannot be rounded in step order. The
    rows of one origin O are instead rounded together, as a flow, in integer
    units of 1e-12. Nodes: a source; O's own node (`own` rows leave it); one
    `pass` node per sender (every coefficient a sender receives arrives there,
    its `pass` rows leave it); one node per final receiver. Edges: source ->
    own(O) carrying 10^12 units; one edge per row, carrying 10^12 * its exact
    coefficient; final receiver -> source carrying what it receives. The
    exact values balance every node, so this is a circulation.

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
      - the `own` hop's pool is 1 (the source edge is 10^12, never touched);
      - every sender's `own` + `pass` outflow equals what it received from O
        (+1 at O), so a service department nets to exactly 0;
      - the final receivers get exactly 1;
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
    senders = {r["sender_member"] for r in exact_rows}
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
    """{id(row): integer units} for one origin's rows; see round_reciprocal_coefficients."""
    src = ("0 source", "")
    node_in = lambda m: ("2 pass", m) if m in senders else ("3 final", m)
    flow, edge_of = {(src, ("1 own", origin)): Fraction(_SCALE)}, {}
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


def coefficient_rows(run_name, steps, weights, method):
    """Rows for epm_staging.cost_allocation_coefficients, 12-dp Decimals.

    Columns, in order: COEFFICIENT_COLUMNS (run, origin_member, step,
    sender_member, receiver_member, driver, hop_kind, pool_coefficient,
    coefficient). Invariants, every method (the warehouse tests rely on them):
      T5  per (run, origin, sender, hop_kind): sum(coefficient) == pool_coefficient;
      own per origin: one `own` hop, at the origin, pool_coefficient == 1;
      pass per origin and sender S: S's `own` + `pass` outflow == the
          coefficients S received from that origin (+1 at the origin);
      T1  per origin: sum(coefficient) over receivers that are not senders == 1.
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
        exact = direct_coefficients(steps, weights)
    elif method == STEP_DOWN:
        exact = step_down_coefficients(steps, weights)
    elif method == RECIPROCAL:
        exact = reciprocal_coefficients(steps, weights)
    else:
        raise CostAllocationError("Method must be one of %s, not %r." % (", ".join(METHODS), method))
    rounded = round_reciprocal_coefficients(exact) if method == RECIPROCAL \
        else round_coefficients(exact, weights)
    return [{"run": run_name, **{c: r[c] for c in COEFFICIENT_COLUMNS[1:]}} for r in rounded]


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
