"""Demo mode: the rule that says whether a visitor may be logged in as the
demo user. Pure: no frappe, no konsol imports. The I/O lives in
konsol/demo_mode.py.

Decided 7 Nov 2026 by Deepak Pai: demo mode is switched on by naming a user
in site_config (`demo_user`), and that user holds EPM Analyst + Entity
Accountant. The rule is an allow-list (PR #421 review): the demo user may
hold those two roles and the ones Frappe gives every System User, and
nothing else, so a mistyped key cannot hand a visitor an Administrator, a
Script Manager or any other role nobody decided on.
"""
from dataclasses import dataclass

#: The roles the demo user holds (Deepak Pai, 7 Nov 2026).
DEMO_ROLES = ("EPM Analyst", "Entity Accountant")

#: Roles Frappe gives every System User; they say nothing about the user.
FRAMEWORK_ROLES = ("All", "Guest", "Desk User")

#: Roles that must never be reachable through the demo button. They would be
#: refused by the allow-list anyway; naming them gives the operator the
#: plainer reason.
PRIVILEGED_ROLES = ("Administrator", "System Manager", "EPM Admin")

#: The only user type the demo button logs in.
REQUIRED_USER_TYPE = "System User"

#: The refusal that means "no demo here": nothing is logged for it.
OFF_REASON = "demo mode is off"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str
    #: The User row's name (canonical case) when allowed, else None. The key
    #: is operator-typed; this is what get_roles and login_as receive.
    user: str | None = None


def _refuse(reason: str) -> Decision:
    return Decision(allowed=False, reason=reason)


def decide(demo_user: str | None, user_row: dict | None, roles: list[str]) -> Decision:
    """Whether the visitor may become `demo_user`.

    :param demo_user: the value of site_config `demo_user` (None or blank = off).
    :param user_row: the User row (`name`, `enabled`, `user_type`), or None when no such user.
    :param roles: the roles that user holds.

    The checks run in this order, and the first refusal wins, so that nothing
    about the user is said while the mode is off.
    """
    if not demo_user or not str(demo_user).strip():
        return _refuse(OFF_REASON)
    if user_row is None:
        return _refuse("no such user")
    if not user_row.get("enabled"):
        return _refuse("user disabled")
    user_type = user_row.get("user_type")
    if user_type != REQUIRED_USER_TYPE:
        return _refuse(
            "user type is {0}, not {1}".format(user_type or "unset", REQUIRED_USER_TYPE)
        )
    privileged = [r for r in roles if r in PRIVILEGED_ROLES]
    if privileged:
        return _refuse("user holds privileged role(s): {0}".format(", ".join(privileged)))
    outside = sorted(set(roles) - set(FRAMEWORK_ROLES) - set(DEMO_ROLES))
    if outside:
        return _refuse("user holds roles outside the demo set: {0}".format(", ".join(outside)))
    return Decision(allowed=True, reason="allowed", user=user_row.get("name") or demo_user)
