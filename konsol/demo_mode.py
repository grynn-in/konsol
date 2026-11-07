"""Demo mode: a public trial of a shared, hourly-reset site.

A visitor opens `/try` and presses one button; the button is a GET to
`konsol.demo_mode.try_demo`, which logs the visitor in as the demo user
and sends them to the Desk. Nothing is built; the demo user is an ordinary
User (EPM Analyst + Entity Accountant, decided 7 Nov 2026 by Deepak Pai).

Switching it on and off is a site_config key, and this module is the only
code in konsol that reads it:

    bench --site <site> set-config demo_user demo@example.com   # on
    bench --site <site> set-config demo_user ""                 # off
    bench --site <site> execute konsol.demo_mode.end_sessions   # log the demo user out

When the key is unset, `/try` and `try_demo` answer 404, so a site that does
not run a demo shows nothing of the feature. The hourly reset itself lives
in the stack (konsolidat `docker/demo/`), not here.

The rule (who may be logged in, and why not) is `konsol.demo_mode_model.decide`.
"""
import frappe
from frappe import _

from konsol.demo_mode_model import Decision, decide

#: The site_config key. `bench set-config demo_user <email>` writes it.
CONF_KEY = "demo_user"

#: What decide() needs from the User row.
USER_FIELDS = ["name", "enabled", "user_type"]

#: Where a demo visitor lands (the v16 Desk).
LANDING = "/desk"


def enabled() -> str | None:
    """The demo user named in site_config, or None when demo mode is off."""
    return frappe.conf.get(CONF_KEY) or None


def decision() -> Decision:
    """Read the key, the User row and its roles, and apply the rule.

    Reads nothing when the key is unset: a site without a demo touches no
    user table on a `/try` hit."""
    user = enabled()
    user_row = frappe.db.get_value("User", user, USER_FIELDS, as_dict=True) if user else None
    roles = frappe.get_roles(user) if user_row else []
    return decide(user, user_row, roles)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def try_demo():
    """Log the visitor in as the demo user and redirect to the Desk.

    GET is right here: the session row is written and committed by Frappe's
    own login manager (`Session.start` commits), the same path as
    `/api/method/login`, so the request-level GET rollback does not undo it.

    On refusal the answer is a bare 404: whether the site has a demo at all
    is not for a visitor to probe. The reason goes to the konsol log."""
    d = decision()
    if not d.allowed:
        frappe.logger("konsol").info("demo mode refused: %s", d.reason)
        frappe.throw(_("Not found"), frappe.DoesNotExistError)
    frappe.local.login_manager.login_as(enabled())
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = LANDING


def end_sessions(user: str | None = None) -> int:
    """End every session of the demo user; the off switch's helper.

    Returns how many `tabSessions` rows the user had before, for the switch's
    output. Takes an explicit user so it still works after the key is unset:

        bench --site <site> execute konsol.demo_mode.end_sessions --args "['demo@example.com']"
    """
    user = user or enabled()
    if not user:
        return 0
    # Plain SQL: `Sessions` is a framework table with no DocType, so the
    # meta-driven frappe.db.count cannot read it (frappe.sessions uses qb).
    count = int(frappe.db.sql("select count(*) from `tabSessions` where `user` = %s", user)[0][0])
    from frappe.sessions import clear_sessions

    clear_sessions(user)
    return count
