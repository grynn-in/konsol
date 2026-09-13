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
