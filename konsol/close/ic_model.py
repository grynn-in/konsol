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
        out.append(copy)
    return out, hidden


def mask_unmatched(rows, allowed):
    """Keep gold_ic_unmatched rows whose ``data_area_id`` is allowed."""
    if allowed is None:
        return list(rows), 0
    kept = [r for r in rows if r.get("data_area_id") in allowed]
    return kept, len(rows) - len(kept)


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
