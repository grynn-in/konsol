"""Period grid model, part 1, pure (konsol#305 E202a; stories 2.2, 2.3; W2-2, W2-4).

Pure functions for the grid's row set and its Ownership and Trial balance
cells. Loaded by path in its tests; imports nothing from frappe or konsol.
``scope_model`` and ``signoff_model`` (themselves frappe-free) are loaded by
sibling path, mirroring tb_view_model.py:25-29.

- ``grid_scope(entities, ownership_rows, start_date, tb_entities)``: the
  grid's row set — ``in_scope`` (covered Active leaves), ``unowned`` (the
  #289 set: a submitted TB with no covering ownership), and ``covering``
  (each entity's covering Ownership Period rows, for ``ownership_cell``). A
  covered leaf that is not Active (Dormant, Disposed) is neither in scope
  nor unowned even with a submitted TB, because it is covered (E2-3, a known
  gap in the #289 interface, not fixed here).
- ``ownership_cell(covering_rows, period_code)``: the Ownership column cell.
- ``tb_cell(status, tb)``: the Trial balance column cell for one status word.
- ``tb_status(entity, tbs, exceptions, expected, unowned)``: which status
  word applies to one entity. The four status inputs mirror the precedence
  at tb_read_api.py:181-192 (``expected`` is
  ``signoff_model.expected_entities(...)``'s result); the fifth,
  ``unowned``, is new here (W2-2): an entity in it gets ``NOT_CONSOLIDATED``
  whatever else holds. tb_read_api.py's own precedence never sees an
  unowned entity (it is not in scope, tb_read_api.py:98), so this module
  adds the parameter rather than guessing at an unstated fifth case.
"""
import importlib.util
import os

import importlib.util as _importlib_util
import os as _os


def _load_period_name():
    """konsol/close/period_name.py loaded by path (konsol#305 review-w5): the
    one "FY2025 P07" format, reachable even under the host tests' stub
    ``konsol.close`` package."""
    path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "period_name.py")
    spec = _importlib_util.spec_from_file_location("konsol_close_period_name", path)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.period_name


period_name = _load_period_name()

_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_sibling(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(_APP_DIR, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


scope_model = _load_sibling("konsol_close_period_grid_scope_model", "close/scope_model.py")
signoff_model = _load_sibling("konsol_close_period_grid_signoff_model", "close/signoff_model.py")

OK = "ok"
BLOCKING = "blocking"
NONE = "none"
TONES = (OK, BLOCKING, NONE)

ACTIVE = "Active"

# --- grid row set --------------------------------------------------------


def grid_scope(entities, ownership_rows, start_date, tb_entities):
    """``{"in_scope", "unowned", "covering"}`` for the grid's row set.

    ``entities`` are non-group Entity dicts (``name``, ``status``, ...); a
    leaf is ``status == "Active"`` (the same filter as signoff_gate.py:98).
    """
    leaves = {e["name"] for e in entities if e.get("status") == ACTIVE}
    covered = scope_model.covered(ownership_rows, start_date)
    in_scope = scope_model.in_scope(leaves, covered)
    unowned = scope_model.uncovered_with_tb(tb_entities, covered)
    covering = {}
    for row in ownership_rows:
        if scope_model.covers(row, start_date):
            covering.setdefault(row["data_area_id"], []).append(row)
    return {"in_scope": in_scope, "unowned": unowned, "covering": covering}


# --- Ownership cell --------------------------------------------------------

CONSOLIDATION_METHODS = ("full", "proportional", "equity", "none")


def _pct(value):
    """``80.0`` -> ``"80"``, ``62.5`` -> ``"62.5"``: no trailing zeros."""
    text = ("%f" % value).rstrip("0").rstrip(".")
    return text or "0"


def _check_method(method):
    if method not in CONSOLIDATION_METHODS:
        raise ValueError(
            "Unknown consolidation method %r: expected one of %s"
            % (method, ", ".join(CONSOLIDATION_METHODS)))


def _ownership_label(row):
    _check_method(row["consolidation_method"])
    return "%s · %s%%" % (row["consolidation_method"].title(), _pct(row["ownership_pct"]))


def ownership_cell(covering_rows, period_code):
    """The grid's Ownership cell for one entity's covering Ownership Period rows."""
    if not covering_rows:
        return {"tone": BLOCKING, "label": "None for %s" % period_code}
    for row in covering_rows:
        _check_method(row["consolidation_method"])
    if len(covering_rows) == 1:
        label = _ownership_label(covering_rows[0])
    else:
        label = "; ".join(sorted(
            "%s: %s" % (row["consolidation_group"], _ownership_label(row))
            for row in covering_rows
        ))
    return {"tone": NONE, "label": label}


# --- Trial balance cell ------------------------------------------------------

# Copied from tb_read_api.py:60-66; the drift guard below keeps the two in step.
RECEIVED = "Received"
EXCEPTION_DECLARED = "Exception declared"
NOT_EXPECTED = "Not expected this period"
MISSING = "Missing"
FREQUENCY_NOT_DECLARED = "Frequency not declared"
QUARTER_NOT_DECLARED = "Quarter not declared"
NOT_CONSOLIDATED = "Not consolidated: no ownership for this period"

_TB_TONE = {
    RECEIVED: OK,
    EXCEPTION_DECLARED: NONE,
    NOT_EXPECTED: NONE,
    MISSING: BLOCKING,
    FREQUENCY_NOT_DECLARED: BLOCKING,
    QUARTER_NOT_DECLARED: BLOCKING,
    NOT_CONSOLIDATED: BLOCKING,
}


def tb_cell(status, tb):
    """The grid's Trial balance cell for one entity's status word.

    ``tb`` is the entity's Trial Balance Submission record (``owner``,
    ``uploaded_on_behalf``, ...), or falsy when ``status`` is not
    ``RECEIVED``.
    """
    if status not in _TB_TONE:
        raise ValueError(
            "Unknown trial balance status %r: expected one of %s"
            % (status, ", ".join(_TB_TONE)))
    label = status
    if status == RECEIVED and tb and tb.get("uploaded_on_behalf") == "Yes":
        label = "Received · on behalf by %s" % tb.get("owner")
    return {"tone": _TB_TONE[status], "label": label}


RATE_TYPE = "Closing"

#: "worst tone wins" (E202b rule 6): blocking beats none beats ok.
_TONE_RANK = {BLOCKING: 0, NONE: 1, OK: 2}


def _rate_target_cell(currency, target, rates):
    """(tone, label) for one target currency, rule 5."""
    missing = rates.get("missing")
    if missing is not None and (currency, target, RATE_TYPE) in missing:
        if (currency, target) in (rates.get("drafts") or ()):
            return BLOCKING, "Awaiting approval"
        return BLOCKING, "Missing"
    if (currency, target) in (rates.get("approved") or ()):
        return OK, "Approved"
    return NONE, "Not needed (last build)"


def rate_cell(currency, rates):
    """The grid's Closing-rate cell for one entity's functional ``currency``.

    ``rates`` = ``{"group_currencies", "missing", "error", "approved",
    "drafts"}`` (period_grid_model.py facts, E202b). Rules, first match wins:
    a warehouse ``error``; a blank ``currency``; no ``group_currencies``; no
    target other than ``currency`` itself ("Group currency"); otherwise the
    worst of each target's own cell (rule 5), joined ``"<to>: <label>"`` when
    there is more than one target.
    """
    error = rates.get("error")
    if error:
        return {"tone": BLOCKING, "label": "Cannot check: %s" % error}
    if not currency:
        return {"tone": BLOCKING, "label": "Currency not declared"}
    group_currencies = rates.get("group_currencies") or set()
    if not group_currencies:
        return {"tone": BLOCKING, "label": "No group reporting currency"}
    targets = sorted(t for t in group_currencies if t != currency)
    if not targets:
        return {"tone": NONE, "label": "Group currency"}
    cells = [(target, _rate_target_cell(currency, target, rates)) for target in targets]
    tone = min((cell[0] for _, cell in cells), key=lambda t: _TONE_RANK[t])
    if len(cells) == 1:
        label = cells[0][1][1]
    else:
        label = "; ".join("%s: %s" % (target, label) for target, (_, label) in cells)
    return {"tone": tone, "label": label}


# --- the assembled grid ------------------------------------------------------


def _key(target):
    return (int(target[0]), int(target[1]))


def _target_row(rows, key):
    for row in rows or ():
        if (int(row["fiscal_year"]), int(row["fiscal_period"])) == key:
            return row
    raise ValueError("%s is not in the calendar rows passed in." % period_name(*key))


def period_grid(target, rows, entities, ownership_rows, tbs, exceptions, rates, allowed):
    """The period grid: one row per in-scope or unowned entity, its three
    cells (Ownership, Trial balance, Closing rate — no IC column and no
    Checks column, W2-4), and the counts the endpoint sends.

    ``target`` = ``(fy, fp)``; ``rows`` = ``fiscal_period_rows()``, from
    which ``start_date`` and ``period_code`` come. ``tbs`` and ``exceptions``
    are ``{entity: record}`` (tb_read_api.py's ``_records`` shape, mirrored
    by ``tb_status``'s contract). ``allowed`` is the caller's permitted
    entity codes (``None`` = all, E2-6); the returned ``rows``, and
    ``counts``, describe the allowed set only — a hidden entity's code,
    problem or otherwise, never appears in the result.
    """
    key = _key(target)
    target_row = _target_row(rows, key)
    start_date = target_row["start_date"]
    period_code = target_row["period_code"]

    scope = grid_scope(entities, ownership_rows, start_date, set(tbs))
    in_scope, unowned, covering = scope["in_scope"], scope["unowned"], scope["covering"]

    by_code = {e["name"]: e for e in entities}
    frequencies = {code: by_code[code].get("reporting_frequency") for code in in_scope}
    expected = signoff_model.expected_entities(frequencies, key, rows)

    all_rows = []
    for code in sorted(in_scope | unowned):
        record = by_code.get(code) or {}
        currency = record.get("functional_currency")
        ownership = ownership_cell(covering.get(code, []), period_code)
        status = tb_status(code, tbs, exceptions, expected, unowned)
        tb = tb_cell(status, tbs.get(code))
        rate = rate_cell(currency, rates)
        problem = BLOCKING in (ownership["tone"], tb["tone"], rate["tone"])
        all_rows.append({
            "entity": code,
            "name": record.get("entity_name") or code,
            "currency": currency,
            "in_scope": code in in_scope,
            "problem": problem,
            "ownership": ownership,
            "tb": tb,
            "rate": rate,
        })

    visible = all_rows if allowed is None else [r for r in all_rows if r["entity"] in allowed]
    hidden = len(all_rows) - len(visible)
    problems = sum(1 for row in visible if row["problem"])
    return {
        "rows": visible,
        "counts": {"rows": len(visible), "problems": problems, "hidden": hidden},
        "rates_error": rates.get("error"),
    }


def tb_status(entity, tbs, exceptions, expected, unowned):
    """Which of the 7 status words applies to ``entity``.

    Mirrors the precedence at tb_read_api.py:181-192. ``tbs`` and
    ``exceptions`` are ``{entity: record}`` (tb_read_api.py's ``_records``
    shape). ``expected`` is ``signoff_model.expected_entities(...)``'s
    result. ``unowned`` (the #289 set, W2-2) takes precedence over
    everything else.
    """
    if entity in unowned:
        return NOT_CONSOLIDATED
    if entity in tbs:
        return RECEIVED
    if entity in exceptions:
        return EXCEPTION_DECLARED
    if entity in (expected.get("frequency_undeclared") or ()):
        return FREQUENCY_NOT_DECLARED
    quarter_unknown = {
        e for g in (expected.get("gaps") or ())
        if g.get("code") == signoff_model.QUARTER_UNDECLARED
        for e in g.get("entities") or ()
    }
    if entity in quarter_unknown:
        return QUARTER_NOT_DECLARED
    if entity in (expected.get("not_expected") or ()):
        return NOT_EXPECTED
    return MISSING
