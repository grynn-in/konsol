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

# Copied from tb_read_api.py:53-58; the drift guard below keeps the two in step.
RECEIVED = "Received"
EXCEPTION_DECLARED = "Exception declared"
NOT_EXPECTED = "Not expected this period"
MISSING = "Missing"
FREQUENCY_NOT_DECLARED = "Frequency not declared"
QUARTER_NOT_DECLARED = "Quarter not declared"
NOT_CONSOLIDATED = "Not consolidated: no ownership"  # new here (W2-2)

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
