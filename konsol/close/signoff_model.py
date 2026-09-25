"""Sign-off gates for the close app, pure (konsol#305 A09, #303-3a).

- ``config_gaps``: configuration that must be declared before any sign-off
  (the first close period, each in-scope entity's reporting frequency), and
  a target that is history.
- ``order_problem``: Pn cannot be signed off while a Regular period from the
  first close period up to Pn-1 is Open, or its sign-off is ``Re-sign Needed``
  (Problems 9). The oldest blocking period is named ("Sign off P8 first").

A first close period of ``None``, or with a 0 in either part (Close Settings
Int fields read back as 0 when unset), is undeclared. Nothing is guessed.
Imports nothing from frappe or konsol.
"""

FIRST_CLOSE_UNDECLARED = "first_close_undeclared"
HISTORY_PERIOD = "history_period"
FREQUENCY_UNDECLARED = "frequency_undeclared"

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
