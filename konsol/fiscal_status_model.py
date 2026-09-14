"""Fiscal year/period status precedence, pure: no frappe import.

A period's effective status is the stricter of its own status and the
status of the fiscal year it belongs to.
"""

OPEN = "Open"
CLOSED = "Closed"
LOCKED = "Locked"

_RANK = {OPEN: 0, CLOSED: 1, LOCKED: 2}


def effective_status(year_status, row_status):
    for status in (year_status, row_status):
        if status not in _RANK:
            raise ValueError(f"unknown fiscal status {status!r}")
    return year_status if _RANK[year_status] >= _RANK[row_status] else row_status


def row_problems(year_status, rows, previous_codes):
    """Error strings for `rows` against their fiscal year's `year_status`
    and, unless `previous_codes` is None (a brand-new year), against the
    codes saved for the year before this change.

    A row may never be looser than its year (Open < Closed < Locked): a
    Closed year refuses an Open row, a Locked year refuses anything but
    Locked. And once a year is Closed or Locked, no row with a code that
    wasn't already saved may be added.

    A year or row status that isn't exactly one of Open/Closed/Locked
    (blank included: Frappe keeps a "" a REST save sends, rather than
    filling in the field default) is reported as an ordinary problem
    naming the row's code, not raised — the caller collects every
    problem and refuses the save with one clear message.
    """
    problems = []

    year_valid = year_status in _RANK
    if not year_valid:
        problems.append(
            f"Fiscal year status {year_status!r} is not Open, Closed or Locked."
        )

    for row in rows:
        code = row["code"]
        status = row["status"]
        if status not in _RANK:
            problems.append(
                f"Period {code} has status {status!r}; it must be Open, Closed or Locked."
            )
            continue

        if not year_valid:
            continue

        if _RANK[status] < _RANK[year_status]:
            problems.append(
                f"Period {code} is {status} but the fiscal year is {year_status}; "
                f"it cannot be looser than its year."
            )

        if (
            previous_codes is not None
            and code not in previous_codes
            and year_status in (CLOSED, LOCKED)
        ):
            problems.append(
                f"Period {code} cannot be added to a {year_status} fiscal year."
            )

    return problems


ADMIN = "EPM Admin"
SYSTEM_MANAGER = "System Manager"

#: (current, new) -> (roles allowed to make the change, a verb phrase for the
#: reason when none of them are held). Any transition not listed here, other
#: than same -> same, is a no-op that is always allowed.
_TRANSITIONS = {
    (OPEN, CLOSED): ((ADMIN, SYSTEM_MANAGER), "Closing an Open period"),
    (OPEN, LOCKED): ((ADMIN, SYSTEM_MANAGER), "Locking an Open period"),
    (CLOSED, LOCKED): ((ADMIN, SYSTEM_MANAGER), "Locking a Closed period"),
    (CLOSED, OPEN): ((ADMIN, SYSTEM_MANAGER), "Reopening a Closed period"),
    (LOCKED, OPEN): ((SYSTEM_MANAGER,), "Reopening a Locked period"),
    (LOCKED, CLOSED): ((SYSTEM_MANAGER,), "Closing a Locked period"),
}


def transition_problem(current, new, roles):
    """None if `roles` may move a period from `current` to `new`, else the
    reason it cannot: which transition, and the role that would allow it.

    EPM Analyst, and any role not named above, may never change a status
    (a same -> same no-op is always fine, for every role). Closed and Locked
    may only be reached, or left, by EPM Admin or System Manager, and only
    System Manager may touch a Locked period at all.
    """
    for status in (current, new):
        if status not in _RANK:
            raise ValueError(f"unknown fiscal status {status!r}")

    if current == new:
        return None

    allowed_roles, verb_phrase = _TRANSITIONS[(current, new)]
    if set(roles) & set(allowed_roles):
        return None

    if len(allowed_roles) == 1:
        needed = f"the {allowed_roles[0]} role"
    else:
        needed = " or ".join(allowed_roles) + " role"
    return f"{verb_phrase} needs {needed}."
