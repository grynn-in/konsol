"""IC Balance rule coverage and screen rows, pure (konsol#305 story 5.4).

Decision #305-W5-4 (Deepak, 6 Oct 2026): an Analyst may draft an IC Balance
on the Intercompany screen; an IC Balance (draft or approved) whose entity
pair no unrealised-profit IC Elimination Rule matches is a setup gap that
blocks sign-off. Rejected: refusing the balance at save. The rule itself is
configured in Desk; the screen only shows its margin.

Imports nothing from frappe or konsol.

The match mirrors konsolidat ``gold_ic_eliminations.sql``
``unrealized_profit_eliminations``, which raises the elimination
``ending_inventory_from_ic * margin_pct / 100`` only where:

- ``rule_type = 'unrealized_profit'`` and ``margin_pct > 0``;
- ``debit_entity_pattern`` is ``'*'`` or equals the SELLING entity;
- ``credit_entity_pattern`` is ``'*'`` or equals the BUYING entity.

A blank pattern is not a wildcard there (it compares ``''`` with the code),
so it matches nothing here either. IC Elimination Rule is not submittable:
every row the caller reads is live in the warehouse.

- ``matching_rules(balance, rules)``: the rules that eliminate the pair.
- ``rule_gap(balances, rules)``: None, or ONE gap naming every uncovered
  pair once (a balance whose ending inventory is 0 or below is skipped: dbt
  filters ``ending_inventory_from_ic > 0``) (``{"code", "pairs", "entities", "message"}``). ``entities`` lets
  the sign-off and readiness scoping cut other entities' codes.
- ``ambiguous_gap(balances, rules)``: None, or ONE gap naming every pair
  that more than one rule matches, with its ``rule_ids`` (F51b, review
  S2): dbt cross-joins every matching rule, so such a pair is eliminated
  once per rule. Same shape and scoping as ``rule_gap``; it blocks too.
- ``rule_gaps(balances, rules)``: ``[rule_gap, ambiguous_gap]`` without the
  Nones, undeclared first: what the sign-off gate and My work append.
- ``pending_gap(balances, rules)``: None, or ONE gap naming every DRAFT
  balance whose pair has a matching rule and whose ending inventory is above
  0 (#305-S8-1): dbt eliminates only approved balances, so it blocks sign-off
  until the draft is approved or deleted. Pairs carry their draft ``names``.
  It is NOT part of ``rule_gaps``: My work's setup gaps never see it
  (C-S8-2).
- ``balance_rows(balances, rules)``: the screen's rows.
- ``visible(balances, allowed)``: entity scope (either side allowed).
- ``draft_problems(...)``: a draft's own refusals, before any write.
"""

import math

RULE_UNDECLARED = "ic_unrealized_profit_rule_undeclared"
RULE_AMBIGUOUS = "ic_unrealized_profit_rule_ambiguous"
DRAFT_PENDING = "ic_balance_draft_pending"
RULE_TYPE = "unrealized_profit"
WILDCARD = "*"
#: Where the rule is configured (the brief: configuration stays in Desk).
RULES_DESK = "/app/ic-elimination-rule"

_STATUS = {0: "Draft", 1: "Approved"}

_GAP_MESSAGE = (
    "No unrealised-profit IC Elimination Rule (rule type unrealized_profit, margin above 0) "
    "matches %d IC Balance pair%s: %s. Its unrealised profit is not eliminated: declare the "
    "rule in Desk (IC Elimination Rule) before signing off."
)

_AMBIGUOUS_MESSAGE = (
    "%d IC Balance pair%s match%s more than one unrealised-profit IC Elimination Rule: %s. "
    "dbt applies every matching rule, so its unrealised profit is eliminated more than once: "
    "keep one rule per pair in Desk (IC Elimination Rule) before signing off."
)

_PENDING_MESSAGE = (
    "%d IC Balance draft%s ha%s a matching unrealised-profit rule but %s not approved: %s. "
    "dbt eliminates only approved balances: approve %s in Approvals (Close Lead), or delete "
    "the draft (Close Lead, in Desk), before signing off."
)


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pattern_matches(pattern, entity):
    return pattern == WILDCARD or (pattern not in (None, "") and pattern == entity)


def matching_rules(balance, rules):
    """The rules dbt applies to ``balance``'s pair, in the order given."""
    out = []
    for rule in rules:
        if rule.get("rule_type") != RULE_TYPE:
            continue
        margin = _number(rule.get("margin_pct"))
        if margin is None or margin <= 0:
            continue
        if not _pattern_matches(rule.get("debit_entity_pattern"), balance.get("selling_entity")):
            continue
        if not _pattern_matches(rule.get("credit_entity_pattern"), balance.get("buying_entity")):
            continue
        out.append(rule)
    return out


def _status(balance):
    docstatus = balance.get("docstatus")
    try:
        docstatus = int(docstatus)
    except (TypeError, ValueError):
        docstatus = None
    if docstatus not in _STATUS:
        raise ValueError("IC Balance %s has docstatus %r: only a draft (0) or an approved (1) "
                         "balance is read here." % (balance.get("name"), balance.get("docstatus")))
    return _STATUS[docstatus]


def _pair(balance):
    return balance.get("selling_entity"), balance.get("buying_entity")


def _nothing_to_eliminate(balance):
    """dbt eliminates only where ``ending_inventory_from_ic > 0`` (F51b,
    review S3). A value that is not a number is NOT nothing: it stays in the
    gap, so an unreadable amount blocks instead of being guessed."""
    number = _number(balance.get("ending_inventory_from_ic"))
    return number is not None and math.isfinite(number) and number <= 0


def rule_gap(balances, rules):
    """None, or the one setup gap naming every uncovered pair once. A
    balance with nothing to eliminate (inventory 0 or below) needs no rule."""
    missing = set()
    for balance in balances:
        _status(balance)
        if _nothing_to_eliminate(balance):
            continue
        if not matching_rules(balance, rules):
            missing.add(_pair(balance))
    if not missing:
        return None
    pairs = sorted(missing)
    text = ", ".join("%s → %s" % p for p in pairs)
    return {
        "code": RULE_UNDECLARED,
        "pairs": [{"selling_entity": s, "buying_entity": b} for s, b in pairs],
        "entities": sorted({e for p in pairs for e in p}),
        "message": _GAP_MESSAGE % (len(pairs), "" if len(pairs) == 1 else "s", text),
    }


def ambiguous_gap(balances, rules):
    """None, or the one setup gap naming every pair more than one rule
    matches, each with its ``rule_ids`` sorted (the read order is the
    database's, so it is not relied on)."""
    ambiguous = {}
    for balance in balances:
        _status(balance)
        if _nothing_to_eliminate(balance):
            continue
        matched = matching_rules(balance, rules)
        if len(matched) > 1:
            ambiguous[_pair(balance)] = sorted(r.get("rule_id") for r in matched)
    if not ambiguous:
        return None
    pairs = sorted(ambiguous)
    text = "; ".join("%s → %s (%s)" % (s, b, ", ".join(ambiguous[(s, b)])) for s, b in pairs)
    one = len(pairs) == 1
    return {
        "code": RULE_AMBIGUOUS,
        "pairs": [{"selling_entity": s, "buying_entity": b, "rule_ids": ambiguous[(s, b)]}
                  for s, b in pairs],
        "entities": sorted({e for p in pairs for e in p}),
        "message": _AMBIGUOUS_MESSAGE % (len(pairs), "" if one else "s", "es" if one else "",
                                         text),
    }


def rule_gaps(balances, rules):
    """Both rule gaps that apply, undeclared first; ``[]`` when none does."""
    return [g for g in (rule_gap(balances, rules), ambiguous_gap(balances, rules)) if g]


def pending_gap(balances, rules):
    """None, or the one blocking gap naming every draft that has a matching
    rule and something to eliminate (an unreadable inventory counts: it is
    never guessed to be nothing). Names are sorted within each pair."""
    pending = {}
    for balance in balances:
        if _status(balance) != "Draft":
            continue
        if _nothing_to_eliminate(balance):
            continue
        if matching_rules(balance, rules):
            pending.setdefault(_pair(balance), []).append(balance.get("name"))
    if not pending:
        return None
    pairs = sorted(pending)
    names = {p: sorted(pending[p]) for p in pairs}
    count = sum(len(n) for n in names.values())
    text = ", ".join("%s → %s (%s)" % (s, b, ", ".join(names[(s, b)])) for s, b in pairs)
    one = count == 1
    return {
        "code": DRAFT_PENDING,
        "pairs": [{"selling_entity": s, "buying_entity": b, "names": names[(s, b)]}
                  for s, b in pairs],
        "entities": sorted({e for p in pairs for e in p}),
        "message": _PENDING_MESSAGE % (count, "" if one else "s", "s" if one else "ve",
                                       "is" if one else "are", text, "it" if one else "them"),
    }


def balance_rows(balances, rules):
    """One row per balance, ordered by pair then name, with its status, its
    amounts as numbers and the margins of the rules that match it."""
    rows = []
    for balance in balances:
        matched = matching_rules(balance, rules)
        rows.append({
            "name": balance.get("name"),
            "selling_entity": balance.get("selling_entity"),
            "buying_entity": balance.get("buying_entity"),
            "fiscal_year": balance.get("fiscal_year"),
            "fiscal_period": balance.get("fiscal_period"),
            "ic_sales_amount": _number(balance.get("ic_sales_amount")),
            "ending_inventory_from_ic": _number(balance.get("ending_inventory_from_ic")),
            "status": _status(balance),
            "rules": [{"rule_id": r.get("rule_id"), "rule_name": r.get("rule_name"),
                       "margin_pct": _number(r.get("margin_pct"))} for r in matched],
            "missing_rule": not matched and not _nothing_to_eliminate(balance),
            "ambiguous_rule": len(matched) > 1 and not _nothing_to_eliminate(balance),
        })
    rows.sort(key=lambda r: (r["selling_entity"] or "", r["buying_entity"] or "", r["name"] or ""))
    return rows


def visible(balances, allowed):
    """``(shown, hidden_count)``: a balance is shown when either entity is
    in ``allowed``; ``None`` means unrestricted (send_back's rule, C04)."""
    if allowed is None:
        return list(balances), 0
    shown = [b for b in balances
             if b.get("selling_entity") in allowed or b.get("buying_entity") in allowed]
    return shown, len(balances) - len(shown)


def _amount_problem(value, label):
    number = _number(value)
    # NaN and infinity are not amounts: "nan" < 0 is False (F51b, review S7).
    if value in (None, "") or number is None or not math.isfinite(number):
        return "The %s must be a number." % label
    if number < 0:
        return "The %s cannot be negative." % label
    return None


def draft_problems(selling_entity, buying_entity, ic_sales_amount, ending_inventory_from_ic,
                   entities):
    """A draft's refusals as sentences; ``[]`` when it may be saved.
    ``entities`` is the set of known entity codes."""
    problems = []
    for label, code in (("selling entity", selling_entity), ("buying entity", buying_entity)):
        if not code:
            problems.append("Name the %s." % label)
        elif code not in entities:
            problems.append("%s is not an entity: name the %s by its entity code."
                            % (code, label))
    if selling_entity and selling_entity == buying_entity:
        problems.append("The selling and buying entity are the same entity: an IC Balance is "
                        "between two entities.")
    for value, label in ((ic_sales_amount, "IC sales amount"),
                         (ending_inventory_from_ic, "ending inventory from IC")):
        problem = _amount_problem(value, label)
        if problem:
            problems.append(problem)
    return problems
