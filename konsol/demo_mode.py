"""Demo mode: a public trial of a shared, hourly-reset site.

A visitor opens `/try` and presses one button; the button is a GET to
`konsol.demo_mode.try_demo`, which logs the visitor in as the demo user
and sends them to the Desk. Nothing is built; the demo user is an ordinary
User (EPM Analyst + Entity Accountant, decided 7 Nov 2026 by Deepak Pai).

Switching it on and off is a site_config key plus two helpers, and this
module is the only code in konsol that reads the key:

    bench --site <site> set-config demo_user demo@example.com   # on (1)
    bench --site <site> execute konsol.demo_mode.switch_on      # on (2): enable the user
    bench --site <site> execute konsol.demo_mode.switch_off     # off (1): disable the user, end its sessions
    bench --site <site> set-config demo_user ""                 # off (2)

Why off is two steps in that order: Frappe v16 caches site_config per
worker for 60 s (`frappe.config.site_cache(ttl=60)`), so a worker may keep
logging visitors in for up to a minute after the key is unset. `decide()`
reads the User's `enabled` from the database on every request, and
`Session.validate_user` refuses a disabled user whatever the key says, so
`switch_off` shuts the door at once; unsetting the key then makes `/try`
answer 404 once the cache turns over. Disabling the user also closes the
in-flight case where a request that was running when its session row was
deleted writes the row back (`Session.update`): its next request is refused.

When the key is unset, `/try` and `try_demo` answer 404, so a site that does
not run a demo shows nothing of the feature. The hourly reset itself lives
in the stack (konsolidat `docker/demo/`), not here.

The rule (who may be logged in, and why not) is `konsol.demo_mode_model.decide`.
"""
import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from konsol.demo_mode_model import OFF_REASON, Decision, decide

#: The site_config key. `bench set-config demo_user <email>` writes it.
CONF_KEY = "demo_user"

#: What decide() needs from the User row.
USER_FIELDS = ["name", "enabled", "user_type"]

#: Where a demo visitor lands (the v16 Desk; a v15 stack has no /desk route).
LANDING = "/desk"

#: try_demo's throttle, per client IP (frappe.rate_limiter.rate_limit; there is no frappe.rate_limit): a GET that writes
#: a session row and a User update, open to Guests, must not be free.
RATE_LIMIT = 10
RATE_SECONDS = 60


def enabled() -> str | None:
    """The demo user named in site_config, or None when demo mode is off."""
    return frappe.conf.get(CONF_KEY) or None


def decision() -> Decision:
    """Read the key, the User row and its roles, and apply the rule.

    Reads nothing when the key is unset: a site without a demo touches no
    user table on a `/try` hit. The roles are read for the User row's name,
    not the operator-typed key, and the allowed Decision carries that name."""
    user = enabled()
    user_row = frappe.db.get_value("User", user, USER_FIELDS, as_dict=True) if user else None
    roles = frappe.get_roles(user_row["name"]) if user_row else []
    return decide(user, user_row, roles)


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=RATE_LIMIT, seconds=RATE_SECONDS)
def try_demo():
    """Log the visitor in as the demo user and redirect to the Desk.

    Throttled to RATE_LIMIT requests per RATE_SECONDS per client IP.

    GET is right here: the session row is written and committed by Frappe's
    own login manager (`Session.start` commits), the same path as
    `/api/method/login`. The login's Activity Log row (`login_feed`) is
    inserted without a commit, and a GET rolls back at the end, so the
    request is asked to commit through `frappe.local.flags.commit`, which
    `app.sync_database` honours (v15 and v16).

    On refusal the answer is a bare 404: whether the site has a demo at all
    is not for a visitor to probe. "Demo mode is off" is not logged (bot hits
    on a site with no demo); a key that names a refused user is an operator
    error and is logged at ERROR, the level that shows by default."""
    d = decision()
    if not d.allowed:
        if d.reason != OFF_REASON:
            frappe.logger("konsol").error("demo mode: the demo_user key names a refused user: %s", d.reason)
        frappe.throw(_("Not found"), frappe.DoesNotExistError)
    frappe.local.login_manager.login_as(d.user)
    frappe.local.flags.commit = True
    frappe.local.response["type"] = "redirect"
    frappe.local.response["location"] = LANDING


def _session_count(user: str) -> int:
    # Plain SQL: `Sessions` is a framework table with no DocType, so the
    # meta-driven frappe.db.count cannot read it (frappe.sessions uses qb).
    return int(frappe.db.sql("select count(*) from `tabSessions` where `user` = %s", user)[0][0])


def end_sessions(user: str | None = None) -> int:
    """End every session of the demo user; returns how many were removed.

    `frappe.sessions.clear_sessions` deletes at most 100 sessions per call
    (`get_sessions_to_clear` limits to 100), so it is called until the user
    has none left. Takes an explicit user so it still works after the key is
    unset:

        bench --site <site> execute konsol.demo_mode.end_sessions --args "['demo@example.com']"
    """
    user = user or enabled()
    if not user:
        return 0
    from frappe.sessions import clear_sessions

    removed = 0
    before = _session_count(user)
    while before:
        clear_sessions(user)
        after = _session_count(user)
        if after >= before:
            raise RuntimeError(
                "demo mode: clear_sessions removed nothing for {0}; {1} session(s) remain".format(user, after)
            )
        removed += before - after
        before = after
    return removed


def _user_row(user: str | None) -> dict:
    user = user or enabled()
    if not user:
        frappe.throw("demo mode: no demo user; pass one, or set the demo_user key first")
    row = frappe.db.get_value("User", user, USER_FIELDS, as_dict=True)
    if not row:
        frappe.throw("demo mode: no such user: {0}".format(user))
    return row


def switch_off(user: str | None = None) -> dict:
    """Shut the demo at once: disable the demo User, then end its sessions.

    Run this BEFORE unsetting the key (see the module docstring for the 60 s
    site_config cache). Returns {"user", "enabled": 0, "sessions_ended"}.

        bench --site <site> execute konsol.demo_mode.switch_off
    """
    name = _user_row(user)["name"]
    frappe.db.set_value("User", name, "enabled", 0)
    # set_value clears this too; said here because Session.validate_user
    # reads get_cached_value("User", ..., "enabled") and must see the change.
    frappe.clear_document_cache("User", name)
    ended = end_sessions(name)
    return {"user": name, "enabled": 0, "sessions_ended": ended}


def switch_on(user: str | None = None) -> dict:
    """Re-enable the demo User after a switch_off. Returns the key as well, so
    the operator sees whether `set-config demo_user` is still to be done.

        bench --site <site> execute konsol.demo_mode.switch_on
    """
    name = _user_row(user)["name"]
    frappe.db.set_value("User", name, "enabled", 1)
    frappe.clear_document_cache("User", name)
    return {"user": name, "enabled": 1, "key": enabled()}
