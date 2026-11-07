# Konsol — Installation

## Prerequisites
- Frappe bench at `/home/pd/frappe-bench` with working frappe source
- Redis, MariaDB/Postgres running

## Install Steps

```bash
cd /home/pd/frappe-bench

# If bench commands work (frappe source is present):
bench new-site epm.local --db-name epm --admin-password admin
bench --site epm.local install-app konsol
bench migrate

# If bench is broken (namespace-only frappe), restore frappe source first:
cd apps/frappe && git checkout develop && cd ../..
bench --site epm.local install-app konsol
bench migrate
```

## Verify
- Navigate to `http://localhost:8000/app/epm-settings` — should show settings form
- Navigate to `http://localhost:8000/app/pipeline-run` — should show list with "New Pipeline Run" button

## Demo mode

A public trial of a shared site: a visitor opens `/try`, presses one button
and is logged in as the demo user, who holds EPM Analyst + Entity Accountant
(decided 7 Nov 2026 by Deepak Pai). The site is reset every hour by the
stack (konsolidat `docker/demo/`), not by konsol.

It is off by default. One site_config key switches it, and only
`konsol/demo_mode.py` reads the key:

```bash
# on: name an enabled System User whose roles are exactly
# EPM Analyst + Entity Accountant (plus what Frappe gives every System User)
bench --site <site> set-config demo_user demo@example.com
bench --site <site> execute konsol.demo_mode.switch_on      # re-enables the user after an earlier switch_off

# off, in this order
bench --site <site> execute konsol.demo_mode.switch_off     # disables the user, ends all its sessions
bench --site <site> set-config demo_user ""
```

Why `switch_off` comes first: Frappe v16 caches site_config per worker for
60 seconds, so after the key is unset a worker can keep logging visitors in
for up to a minute. The decision reads the User's `enabled` flag from the
database on every request, and Frappe refuses a disabled user at login, so
`switch_off` stops new logins at once; the key then turns `/try` into a 404
once the cache turns over. Disabling does not end sessions that already
exist (a resumed session never re-reads `enabled`): `switch_off` ends them
with two `end_sessions` passes, the second for a request that was in flight
during the first. (The ext stack's `demo.sh` also sends gunicorn a HUP, which
ends in-flight requests and closes the cache window sooner.)
`switch_off` prints `{"user", "enabled": 0, "sessions_ended"}`;
`switch_on` prints `{"user", "enabled": 1, "key"}`. Both take an explicit
user as `--args "['demo@example.com']"` when the key is already unset.
`konsol.demo_mode.end_sessions` ends the sessions alone; it loops past
Frappe's 100-sessions-per-call limit and returns the number removed (bench
execute prints nothing when that number is 0).

While it is off, `/try` and `/api/method/konsol.demo_mode.try_demo` answer
404 and log nothing. The button is also refused with 404 when the named
user is missing, disabled, not a System User, or holds a role outside the
demo set (Administrator, System Manager, EPM Admin, Script Manager, any
other); that is an operator error and is logged at ERROR in the konsol log
(`logs/konsol.log`), the level that shows by default. `try_demo` is
rate-limited to 10 requests per 60 seconds per client IP.

The demo lands on `/desk`, the Frappe v16 Desk. A stack still on v15 has no
`/desk` route (its Desk is `/app`): run the demo on a v16 stack.
