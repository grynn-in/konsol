"""Intercompany rules every close surface shares (konsol#305 C01), pure.

No frappe, no konsol import: loaded by path in its tests.

- ``state``: which state the IC read is in. 0 Published Intercompany
  Accounts is "not configured — nothing was checked" (W3 engineering call);
  a Close Settings "none in this group" declaration is "not applicable"
  (#305-W3-7). Nothing here ever turns "no rows" into "reconciled".
- ``mask`` / ``mask_unmatched``: #305-W3-2 option B. A scoped caller sees
  their own side, the difference and the partner's code; the partner's
  amounts are blanked. A display courtesy, not a security control.
- ``counts``: per ``match_status`` (gold_ic_reconciliation.sql:420-425).
"""

STATES = ("not_configured", "not_applicable", "not_built", "error", "checked")

MATCH_STATUSES = ("matched", "within_tolerance", "fx_difference", "over_tolerance")

NOT_CONFIGURED = "Intercompany not configured — nothing was checked."

# #293 wording: the quoted refusal is intercompany_account.py's own
# (allow_ic refusal); a test ties the two together.
SETUP_HELP = (
    NOT_CONFIGURED
    + " No Intercompany Account is Published. Publish each pairing in Intercompany Account."
    " Each account must first allow intercompany in the group chart"
    " (Main Account → Allow Intercompany); otherwise publishing is refused:"
    " \"The group chart has not declared allow_ic on <accounts>."
    " Set Allow Intercompany on those accounts before publishing this pairing.\""
)

NOT_BUILT = "The warehouse has not built the intercompany tables yet — nothing was checked."

NOT_APPLICABLE = ("Intercompany: none in this group (declared in Close Settings) "
                  "— not applicable.")

_DECLARED_NONE_CONFLICT = (
    "Close Settings declares no intercompany in this group, but %d Intercompany Account(s)"
    " are Published: clear the declaration or make them Inactive."
)

_ERROR = ("Intercompany could not be checked: %s. "
          "Rebuild the consolidation, then open this again.")

# Per-side columns of gold_ic_reconciliation that carry the side's amounts
# or currency; blanked for a side the caller may not see (W3-2).
_SIDE_COLUMNS = ("balance", "local", "group_balance", "share", "residual", "currency")

# Pair-level columns that can expose a masked side's amount indirectly (own
# balance + difference = partner balance): blanked whenever either side is
# masked, not per side (review-w3.md S3). ``difference`` itself stays —
# Deepak's open call under W3-2 B.
_PAIR_COLUMNS_MASKED_ON_EITHER_SIDE = ("matched_amount", "net_balance")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def state(published, error=None, declared_none=False):
    """The IC read's state and its message.

    ``published``: count of Published Intercompany Accounts (int >= 0).
    ``error``: None or ``{"not_built": bool, "text": str}`` from the
    warehouse read. ``declared_none``: Close Settings declares no
    intercompany in this group (bool). The declaration and the count are
    decided before any warehouse answer.
    """
    if not _is_int(published) or published < 0:
        raise ValueError("published must be a non-negative int, got %r" % (published,))
    if not isinstance(declared_none, bool):
        raise ValueError("declared_none must be a bool, got %r" % (declared_none,))
    if declared_none:
        if published == 0:
            return {"state": "not_applicable", "message": NOT_APPLICABLE}
        return {"state": "error", "message": _DECLARED_NONE_CONFLICT % published}
    if published == 0:
        return {"state": "not_configured", "message": NOT_CONFIGURED}
    if error:
        if error.get("not_built"):
            return {"state": "not_built", "message": NOT_BUILT}
        return {"state": "error", "message": _ERROR % (error.get("text"),)}
    return {"state": "checked", "message": None}


def _blank_side(row, side):
    for col in _SIDE_COLUMNS:
        row["%s_%s" % (col, side)] = None


def mask(rows, allowed):
    """W3-2 mask of gold_ic_reconciliation rows for a caller.

    ``allowed`` is None (unscoped) or the set of entities the caller may
    see. A row with neither side allowed is dropped and counted. Returns
    ``(rows, hidden)``; the input rows are not changed.
    """
    out = []
    hidden = 0
    for row in rows:
        if allowed is None:
            a_ok = b_ok = True
        else:
            a_ok = row.get("entity_a") in allowed
            b_ok = row.get("entity_b") in allowed
        if not (a_ok or b_ok):
            hidden += 1
            continue
        copy = dict(row)
        copy["masked_a"] = not a_ok
        copy["masked_b"] = not b_ok
        if not a_ok:
            _blank_side(copy, "a")
        if not b_ok:
            _blank_side(copy, "b")
        if not a_ok or not b_ok:
            for col in _PAIR_COLUMNS_MASKED_ON_EITHER_SIDE:
                copy[col] = None
        out.append(copy)
    return out, hidden


def mask_unmatched(rows, allowed):
    """Keep gold_ic_unmatched rows whose ``data_area_id`` is allowed."""
    if allowed is None:
        return list(rows), 0
    kept = [r for r in rows if r.get("data_area_id") in allowed]
    return kept, len(rows) - len(kept)


def pair_key(row):
    """The four-key identity of an intercompany pair (E5-P1).

    Works on a gold_ic_reconciliation row and on an ``ic_sent_back``
    event's ``detail`` dict: both carry ``entity_a``, ``account_a``,
    ``entity_b``, ``account_b``.
    """
    return (row["entity_a"], row["account_a"], row["entity_b"], row["account_b"])


_SENT_BACK_KEYS = ("entity_a", "account_a", "entity_b", "account_b")


def sent_back(events):
    """The latest ``ic_sent_back`` event per pair, by ``(at, name)``.

    ``events`` are Close Event dicts with ``detail`` already parsed. A kind
    other than ``ic_sent_back`` raises; a detail missing one of the four
    pair keys raises naming the event (C04 always writes them).
    """
    latest = {}
    for event in events:
        if event.get("kind") != "ic_sent_back":
            raise ValueError(
                "sent_back expects only ic_sent_back events, got %r (%s)"
                % (event.get("kind"), event.get("name")))
        detail = event.get("detail") or {}
        missing = [k for k in _SENT_BACK_KEYS if k not in detail]
        if missing:
            raise ValueError(
                "ic_sent_back event %s detail is missing %s"
                % (event.get("name"), ", ".join(missing)))
        key = pair_key(detail)
        candidate = (event.get("at"), event.get("name"))
        current = latest.get(key)
        if current is None or candidate > (current.get("at"), current.get("name")):
            latest[key] = event
    return latest


def open_fixes(events, rows, error=None):
    """Which sent-back pairs are still open, and why (E5-P2, E5-P4).

    ``rows`` are the current period's gold_ic_reconciliation rows, any
    consolidation group (E5-P1). ``error`` is None, or the warehouse
    error text that makes every open pair ``cannot_check`` (fail closed).
    Returns unmasked fix dicts sorted by pair key.
    """
    by_pair = {}
    for row in rows:
        by_pair.setdefault(pair_key(row), []).append(row)
    fixes = []
    for key, event in sent_back(events).items():
        pair_rows = by_pair.get(key, [])
        if error:
            fix_state = "cannot_check"
        elif not pair_rows:
            fix_state = "not_in_build"
        elif any(r.get("match_status") == "over_tolerance" for r in pair_rows):
            fix_state = "over_tolerance"
        else:
            continue
        entity_a, account_a, entity_b, account_b = key
        fixes.append({
            "entity_a": entity_a, "account_a": account_a,
            "entity_b": entity_b, "account_b": account_b,
            "state": fix_state,
            "groups": [
                {"consolidation_group": r.get("consolidation_group"),
                 "difference": r.get("difference"),
                 "tolerance": r.get("tolerance"),
                 "balance_a": r.get("balance_a"),
                 "balance_b": r.get("balance_b")}
                for r in pair_rows
            ],
            "error": error,
            "sent_by": event.get("actor"),
            "sent_at": event.get("at"),
            "reason": event.get("reason"),
        })
    fixes.sort(key=lambda f: (f["entity_a"], f["account_a"], f["entity_b"], f["account_b"]))
    return fixes


_MATCH_STATUS_ORDER = {"over_tolerance": 0, "fx_difference": 1, "within_tolerance": 2,
                       "matched": 3}


def group_view(rows, events, currencies):
    """Pairs grouped per consolidation group, for the IC screen.

    ``currencies`` is ``{consolidation_group: reporting_currency or None}``.
    Each group's ``tolerance`` and ``ic_difference_account`` must agree
    across its rows (one value per group in dbt); disagreement raises.
    ``tolerance_declared`` is W3-6: 0 is undeclared, so
    ``can_send_back`` is False for every pair in an undeclared group
    (W3-P1), even one that is ``over_tolerance``.
    """
    sb = sent_back(events)
    by_group = {}
    for row in rows:
        by_group.setdefault(row.get("consolidation_group"), []).append(row)
    out = []
    for group, group_rows in by_group.items():
        tolerances = {r.get("tolerance") for r in group_rows}
        if len(tolerances) > 1:
            raise ValueError(
                "consolidation group %r rows disagree on tolerance: %r" % (group, tolerances))
        tolerance = next(iter(tolerances))
        accounts = {r.get("ic_difference_account") for r in group_rows}
        if len(accounts) > 1:
            raise ValueError(
                "consolidation group %r rows disagree on ic_difference_account: %r"
                % (group, accounts))
        ic_difference_account = next(iter(accounts))
        tolerance_declared = float(tolerance or 0) > 0
        pairs = []
        for row in group_rows:
            key = pair_key(row)
            event = sb.get(key)
            copy = dict(row)
            copy["sent_back"] = (
                {"by": event.get("actor"), "at": event.get("at"), "reason": event.get("reason")}
                if event else None)
            copy["can_send_back"] = (row.get("match_status") == "over_tolerance"
                                     and tolerance_declared)
            pairs.append(copy)
        pairs.sort(key=lambda r: (_MATCH_STATUS_ORDER.get(r.get("match_status"), 99),
                                  pair_key(r)))
        out.append({
            "consolidation_group": group,
            "reporting_currency": currencies.get(group),
            "tolerance": tolerance,
            "ic_difference_account": ic_difference_account,
            "tolerance_declared": tolerance_declared,
            "pairs": pairs,
        })
    out.sort(key=lambda g: g["consolidation_group"])
    return out


_SIGNOFF_LINE_MESSAGE = {
    "not_configured": NOT_CONFIGURED,
    "not_applicable": NOT_APPLICABLE,
    "not_built": NOT_BUILT,
}


def signoff_line(state, rows, unmatched, events):
    """The counts-only sign-off summary line (E5-P13: no entity, no amount).

    ``counts`` and ``sent_back_open`` are only populated for ``checked``;
    every other state carries ``None`` for both, never a guessed 0.
    """
    if state not in STATES:
        raise ValueError("unknown intercompany state %r" % (state,))
    if state == "checked":
        c = counts(rows, unmatched)
        sent_back_open = len(open_fixes(events, rows))
        if c["pairs"] == 0:
            message = "0 intercompany pairs in the last build for this period."
        else:
            message = None
        return {"state": state, "message": message, "counts": c,
                "sent_back_open": sent_back_open}
    return {"state": state, "message": _SIGNOFF_LINE_MESSAGE.get(state), "counts": None,
            "sent_back_open": None}


TOLERANCE_UNDECLARED = "ic_tolerance_undeclared"

_TOLERANCE_GAP_MESSAGE = (
    "Declare the intercompany difference tolerance on %d consolidation group(s): %s "
    "(Consolidation Group → Intercompany Difference Tolerance, on the group's own row). "
    "0 is undeclared; for an exact match declare a tiny positive amount such as 0.01."
)


def tolerance_gap(published, declared_none, groups):
    """The W3-6 setup gap: which group nodes have not declared a tolerance.

    ``groups`` are the consolidation group nodes (blank ``data_area_id``,
    W3-P13): ``[{"consolidation_group", "ic_difference_tolerance"}]``.
    None when intercompany is not configured or not applicable (W3-P2):
    there is nothing yet to judge a tolerance against.
    """
    if published == 0 or declared_none:
        return None
    undeclared = []
    for g in groups:
        tolerance = g.get("ic_difference_tolerance")
        value = float(tolerance or 0)
        if value < 0:
            raise ValueError(
                "consolidation group %r has a negative intercompany difference tolerance: %r"
                % (g.get("consolidation_group"), tolerance))
        if value <= 0:
            undeclared.append(g.get("consolidation_group"))
    if not undeclared:
        return None
    undeclared.sort()
    return {
        "code": TOLERANCE_UNDECLARED,
        "groups": undeclared,
        "message": _TOLERANCE_GAP_MESSAGE % (len(undeclared), ", ".join(undeclared)),
    }


def counts(rows, unmatched):
    """Pairs per match_status plus the partnerless count.

    An unknown status raises (never counted as another status).
    """
    out = {"pairs": 0}
    for status in MATCH_STATUSES:
        out[status] = 0
    for row in rows:
        status = row.get("match_status")
        if status not in MATCH_STATUSES:
            raise ValueError("unknown intercompany match_status %r" % (status,))
        out[status] += 1
        out["pairs"] += 1
    out["unmatched"] = len(unmatched)
    return out
