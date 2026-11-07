"""Demo mode: the rule that says whether a visitor may be logged in as the
demo user. Pure: no frappe, no konsol imports. The I/O lives in
konsol/demo_mode.py.

Decided 7 Nov 2026 by Deepak Pai: demo mode is switched on by naming a user
in site_config (`demo_user`), and that user holds EPM Analyst + Entity
Accountant. A user with a privileged role can never be the demo user, so a
mistyped key cannot hand a visitor the Administrator.
"""
from dataclasses import dataclass

#: Roles that must never be reachable through the demo button.
PRIVILEGED_ROLES = ("Administrator", "System Manager", "EPM Admin")

#: The only user type the demo button logs in.
REQUIRED_USER_TYPE = "System User"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def _refuse(reason: str) -> Decision:
    return Decision(allowed=False, reason=reason)


def decide(demo_user: str | None, user_row: dict | None, roles: list[str]) -> Decision:
    """Whether the visitor may become `demo_user`.

    :param demo_user: the value of site_config `demo_user` (None or blank = off).
    :param user_row: the User row (`enabled`, `user_type`), or None when no such user.
    :param roles: the roles that user holds.

    The checks run in this order, and the first refusal wins, so that nothing
    about the user is said while the mode is off.
    """
    if not demo_user or not str(demo_user).strip():
        return _refuse("demo mode is off")
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
    return Decision(allowed=True, reason="allowed")
