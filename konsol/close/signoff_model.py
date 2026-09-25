"""Sign-off gates for the close app, pure (konsol#305 A09, #303-3a).

- ``config_gaps``: configuration that must be declared before any sign-off
  (the first close period, each in-scope entity's reporting frequency), and
  a target that is history.
- ``order_problem``: Pn cannot be signed off while a Regular period from the
  first close period up to Pn-1 is Open, or its sign-off is ``Re-sign Needed``
  (Problems 9). The oldest blocking period is named ("Sign off P8 first").
- ``expected_entities``: which in-scope entities owe a trial balance for a
  Regular period (A10). Monthly entities always; Quarterly entities only at a
  quarter-end, read from the declared ``quarter`` field of EPM Fiscal Year
  Period, never from ``fiscal_period % 3``.
- ``completeness_problem``: every expected entity has a submitted TB or a
  submitted TB Exception (#303-3a point 3). Only ``docstatus == 1`` counts.
- ``covers_notes``: "<entity>: covers P08–P09" when a TB follows a run of
  exceptions in the same fiscal year.

A first close period of ``None``, or with a 0 in either part (Close Settings
Int fields read back as 0 when unset), is undeclared. Nothing is guessed.
Imports nothing from frappe or konsol.
"""

FIRST_CLOSE_UNDECLARED = "first_close_undeclared"
HISTORY_PERIOD = "history_period"
FREQUENCY_UNDECLARED = "frequency_undeclared"
QUARTER_UNDECLARED = "quarter_undeclared"

FREQUENCIES = ("Monthly", "Quarterly")
RE_SIGN_NEEDED = "Re-sign Needed"

_UNDECLARED_MESSAGE = (
    "Declare the first close period in Close Settings (the Close Lead or System Manager) "
    "before signing off."
)


def first_close_key(first_close):
    """``(fiscal_year, fiscal_period)`` or None when undeclared (None, or a 0 in either part)."""
    if first_close is None:
        return None
    fy, fp = int(first_close[0] or 0), int(first_close[1] or 0)
    if fy == 0 or fp == 0:
        return None
    return (fy, fp)


def _key(value):
    return (int(value[0]), int(value[1]))


def _label(key):
    return "FY%d P%02d" % key


def config_gaps(first_close, target, frequencies):
    """Configuration gaps blocking sign-off of ``target``.

    ``frequencies`` maps each in-scope entity to its ``reporting_frequency``.
    Returns a list of ``{"code", "message"}`` (the frequency gap also carries
    ``entities``); ``[]`` when everything is declared. An unknown frequency
    raises ValueError.
    """
    gaps = []
    first = first_close_key(first_close)
    target = _key(target)
    if first is None:
        gaps.append({"code": FIRST_CLOSE_UNDECLARED, "message": _UNDECLARED_MESSAGE})
    elif target < first:
        gaps.append({
            "code": HISTORY_PERIOD,
            "message": (
                "%s is before the first close period %s; it is history (opening balances) "
                "and is not signed off." % (_label(target), _label(first))
            ),
        })
    blank = []
    for entity, frequency in sorted((frequencies or {}).items()):
        if not frequency:
            blank.append(entity)
        elif frequency not in FREQUENCIES:
            raise ValueError(
                "Unknown reporting frequency %r for %s; expected one of %s."
                % (frequency, entity, ", ".join(FREQUENCIES))
            )
    if blank:
        gaps.append({
            "code": FREQUENCY_UNDECLARED,
            "entities": blank,
            "message": (
                "Set the Reporting Frequency (Monthly or Quarterly) on %s before signing off."
                % ", ".join(blank)
            ),
        })
    return gaps


def _blocks(state):
    return state.get("status") == "Open" or state.get("signoff") == RE_SIGN_NEEDED


def order_problem(states, first_close, target):
    """The order gate for signing off ``target``, or None when nothing blocks.

    ``states`` are Regular period states (``key``, ``code``, ``status``,
    ``signoff``). A period in ``[first_close, target)`` blocks when it is Open
    or its sign-off is ``Re-sign Needed``. Raises ValueError when the first
    close period is undeclared: report ``config_gaps`` instead of guessing.
    """
    first = first_close_key(first_close)
    if first is None:
        raise ValueError(
            "%s: the order gate needs a declared first close period." % FIRST_CLOSE_UNDECLARED
        )
    target = _key(target)
    blocking = sorted(
        (_key(s["key"]), s) for s in states or () if first <= _key(s["key"]) < target and _blocks(s)
    )
    if not blocking:
        return None
    oldest = blocking[0][1]
    if oldest.get("status") == "Open":
        message = "Sign off and close %s first" % oldest["code"]
    else:
        message = "Re-sign %s first" % oldest["code"]
    return {
        "blocking": oldest["code"],
        "periods": [s["code"] for _key_, s in blocking],
        "message": message,
    }


# --- A10: expected entities, completeness, covers notes -----------------------

SUBMITTED = 1


def _row_key(row):
    return (int(row["fiscal_year"]), int(row["fiscal_period"]))


def _regular(rows):
    return [r for r in rows or () if r.get("period_type") == "Regular"]


def _is_quarter_end(target_row, rows):
    """True/False, or None when the year's quarters are not fully declared.

    Quarter-end is the Regular row with the highest ``fiscal_period`` among the
    Regular rows of the same fiscal year and the same declared ``quarter``. A
    blank quarter on the target, or on any Regular row of its year, makes the
    answer unknowable: a quarter's last period cannot be named while some of
    its periods are unassigned.
    """
    fy = int(target_row["fiscal_year"])
    year = [r for r in _regular(rows) if int(r["fiscal_year"]) == fy]
    if any(not r.get("quarter") for r in year):
        return None
    quarter = target_row["quarter"]
    last = max(int(r["fiscal_period"]) for r in year if r["quarter"] == quarter)
    return int(target_row["fiscal_period"]) == last


def expected_entities(frequencies, target, rows):
    """Which entities owe a trial balance for the Regular period ``target``.

    ``frequencies`` maps each in-scope entity to its ``reporting_frequency``.
    ``rows`` are period rows (fiscal_calendar.fiscal_period_rows() shape) and
    must hold every Regular row of the target's fiscal year.

    Returns ``{"expected", "not_expected", "frequency_undeclared", "gaps"}``,
    each list sorted. A blank frequency is listed, not guessed (config_gaps
    reports it). A Quarterly entity meeting an undeclared quarter yields one
    ``quarter_undeclared`` gap naming those entities. Raises ValueError for an
    unknown frequency, a target missing from ``rows``, or a non-Regular target
    (only Regular periods are gated, P5).
    """
    target = _key(target)
    matches = [r for r in rows or () if _row_key(r) == target]
    if not matches:
        raise ValueError("%s is not in the calendar rows passed in." % _label(target))
    target_row = matches[0]
    if target_row.get("period_type") != "Regular":
        raise ValueError(
            "%s is a %s period; only Regular periods are gated."
            % (_label(target), target_row.get("period_type") or "blank-type")
        )

    quarter_end = _is_quarter_end(target_row, rows)
    expected, not_expected, undeclared, quarter_unknown = [], [], [], []
    for entity, frequency in sorted((frequencies or {}).items()):
        if not frequency:
            undeclared.append(entity)
        elif frequency == "Monthly":
            expected.append(entity)
        elif frequency == "Quarterly":
            if quarter_end is None:
                quarter_unknown.append(entity)
            elif quarter_end:
                expected.append(entity)
            else:
                not_expected.append(entity)
        else:
            raise ValueError(
                "Unknown reporting frequency %r for %s; expected one of %s."
                % (frequency, entity, ", ".join(FREQUENCIES))
            )

    gaps = []
    if quarter_unknown:
        gaps.append({
            "code": QUARTER_UNDECLARED,
            "entities": quarter_unknown,
            "message": (
                "Declare the Quarter of every Regular period of FY%d in the fiscal year "
                "(%s is quarterly, so %s needs to be known as a quarter-end or not) "
                "before signing off." % (
                    target[0], ", ".join(quarter_unknown), target_row.get("period_code") or _label(target),
                )
            ),
        })
    return {
        "expected": expected,
        "not_expected": not_expected,
        "frequency_undeclared": undeclared,
        "gaps": gaps,
    }


def _submitted(records):
    """Records with ``docstatus == 1``; a missing docstatus raises KeyError."""
    return [r for r in records or () if int(r["docstatus"]) == SUBMITTED]


def completeness_problem(expected, submitted, excepted):
    """Missing trial balances for the target period, or None.

    ``submitted`` (Trial Balance Submission) and ``excepted`` (TB Exception)
    are the target period's records, each with ``data_area_id`` and
    ``docstatus``. Only ``docstatus == 1`` counts: a draft or cancelled record
    covers nothing.
    """
    covered = {r["data_area_id"] for r in _submitted(submitted)}
    covered |= {r["data_area_id"] for r in _submitted(excepted)}
    missing = sorted(set(expected or ()) - covered)
    if not missing:
        return None
    return {
        "missing": missing,
        "message": "No trial balance from %s. Upload %s or declare an exception." % (
            ", ".join(missing), "it" if len(missing) == 1 else "them",
        ),
    }


def covers_notes(target, rows, submitted, excepted):
    """Notes like "ZZA: covers P08–P09", one per entity whose TB in ``target``
    follows a run of exceptions.

    Walks back over the Regular rows of the target's fiscal year: each earlier
    period with a submitted exception and no submitted TB joins the run; the
    run stops at the first period without one. It never crosses a fiscal year
    (the year-end needs its own trial balance). Records carry
    ``data_area_id``, ``fiscal_year``, ``fiscal_period`` and ``docstatus``;
    only ``docstatus == 1`` counts. Sorted by entity.
    """
    target = _key(target)
    codes = {}
    earlier = []
    for r in _regular(rows):
        key = _row_key(r)
        codes[key] = r.get("period_code") or "P%02d" % key[1]
        if key[0] == target[0] and key < target:
            earlier.append(key)
    earlier.sort(reverse=True)

    tbs = {(r["data_area_id"], _row_key(r)) for r in _submitted(submitted)}
    exceptions = {(r["data_area_id"], _row_key(r)) for r in _submitted(excepted)}

    notes = []
    for entity in sorted({e for e, k in tbs if k == target}):
        first = None
        for key in earlier:
            if (entity, key) in exceptions and (entity, key) not in tbs:
                first = key
            else:
                break
        if first is not None:
            notes.append("%s: covers %s\u2013%s" % (entity, codes[first], codes.get(target, "P%02d" % target[1])))
    return notes


# --- A21: the sign-off summary (story 9.1) -----------------------------------

RUN_STATUSES = ("Queued", "Running", "Green", "Amber", "Red", "Error")
SIGNED_STATES = ("Signed Off", "Acknowledged", "Overridden")  # assertion_run.SIGNED_STATES
SIGNOFF_STATES = ("Not Signed Off",) + SIGNED_STATES + (RE_SIGN_NEEDED,)

_LABELS = {
    "run_checks": "Run the checks",
    "rerun": "Run the checks again",
    "sign": "Sign off",
    "acknowledge": "Acknowledge the warnings and sign off",
    "override": "Override the failed checks and sign off",
}
_RED_LABEL = "Checks failed: only the Close Lead can override them with a reason"


def _gate_messages(problems):
    messages = [g["message"] for g in problems["config_gaps"]]
    for part in ("order", "completeness"):
        if problems[part]:
            messages.append(problems[part]["message"])
    return messages


def _blocked_label(problems):
    """The button text for the first gate that blocks, or None."""
    if problems["config_gaps"]:
        return problems["config_gaps"][0]["message"]
    if problems["order"]:
        return "Sign off %s first" % problems["order"]["blocking"]
    if problems["completeness"]:
        return problems["completeness"]["message"]
    return None


def _checks(run):
    if run is None:
        return {"run": None, "status": None, "signoff_status": None, "failed": None, "errored": None}
    return {
        "run": run["name"],
        "status": run["status"],
        "signoff_status": run["signoff_status"],
        # Absent means the caller did not read it: unknown, never 0.
        "failed": run.get("failed"),
        "errored": run.get("errored"),
    }


def _acknowledgements(run, warned_names):
    names = list(warned_names or ())
    total = None if run is None else run.get("warned")
    unlisted = None if total is None else max(int(total) - len(names), 0)
    return {"names": names, "total": None if total is None else int(total), "unlisted": unlisted}


def _on_behalf(rows):
    """Labels for submitted TBs uploaded on behalf (R4). ``uploaded_on_behalf``
    must be present: 1 labelled, 0 omitted, None unknown (uploaded before the
    flag existed, Problems 16) and shown as unknown."""
    labels, unknown = [], []
    for r in sorted(rows or (), key=lambda r: (r["data_area_id"], r["owner"])):
        flag = r["uploaded_on_behalf"]
        if flag is None:
            unknown.append("%s: by %s; on-behalf not recorded (uploaded before it was tracked)"
                           % (r["data_area_id"], r["owner"]))
        elif int(flag):
            labels.append("by %s for %s" % (r["owner"], r["data_area_id"]))
    return {"labels": labels, "unknown": unknown}


def _action(run, problems, can_override):
    if run is not None:
        if run["status"] not in RUN_STATUSES:
            raise ValueError("Unknown Assertion Run status %r; expected one of %s."
                             % (run["status"], ", ".join(RUN_STATUSES)))
        if run["signoff_status"] not in SIGNOFF_STATES:
            raise ValueError("Unknown sign-off status %r; expected one of %s."
                             % (run["signoff_status"], ", ".join(SIGNOFF_STATES)))
        if run["signoff_status"] in SIGNED_STATES:
            return "signed", run["signoff_status"]
    blocked = _blocked_label(problems)
    if blocked:
        return "blocked", blocked
    if run is None:
        return "run_checks", _LABELS["run_checks"]
    if run["signoff_status"] == RE_SIGN_NEEDED:
        return "rerun", _LABELS["rerun"]
    status = run["status"]
    if status in ("Queued", "Running"):
        return "wait", "The checks are %s; wait for them to finish" % status.lower()
    if status == "Green":
        return "sign", _LABELS["sign"]
    if status == "Amber":
        return "acknowledge", _LABELS["acknowledge"]
    if can_override:
        return "override", _LABELS["override"]
    return "blocked", _RED_LABEL


def summary(run, warned_names, on_behalf, exceptions, covers, previous, problems, can_override):
    """The sign-off summary of story 9.1 and the next action.

    - ``run``: the latest terminal Assertion Run (``name``, ``status``,
      ``signoff_status``; optional ``failed``, ``errored``, ``warned``) or None.
      An absent count is reported as None (unknown), never 0.
    - ``warned_names``: the warned assertions (capped by the reader).
    - ``on_behalf``: submitted TBs (``data_area_id``, ``owner``,
      ``uploaded_on_behalf`` of 1, 0 or None for unknown).
    - ``exceptions``: submitted TB Exceptions (``data_area_id``, ``reason``,
      ``declared_by``).
    - ``covers``: ``covers_notes`` output. ``previous``: period states
      (``key``, ``code``, ``status``, ``signoff``).
    - ``problems``: ``signoff_gate.sign_off_problems`` output.

    ``action`` is one of signed, blocked, run_checks, rerun, wait, sign,
    acknowledge, override. A signed run stays signed; otherwise any gate blocks
    (configuration first, then order, then completeness). An unknown run or
    sign-off status raises ValueError.
    """
    action, label = _action(run, problems, can_override)
    return {
        "action": action,
        "label": label,
        "gates": {
            "config_gaps": list(problems["config_gaps"]),
            "order": problems["order"],
            "completeness": problems["completeness"],
            "messages": _gate_messages(problems),
        },
        "checks": _checks(run),
        "acknowledgements": _acknowledgements(run, warned_names),
        "on_behalf": _on_behalf(on_behalf),
        "exceptions": [
            {"entity": e["data_area_id"], "reason": e["reason"], "declared_by": e["declared_by"]}
            for e in sorted(exceptions or (), key=lambda e: e["data_area_id"])
        ],
        "covers": list(covers or ()),
        "previous": [
            {"code": p["code"], "status": p["status"], "signoff": p["signoff"]}
            for p in sorted(previous or (), key=lambda p: _key(p["key"]))
        ],
    }
