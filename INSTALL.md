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
# on: name an enabled System User with no privileged role
bench --site <site> set-config demo_user demo@example.com

# off: unset the key, then end the demo user's sessions
bench --site <site> set-config demo_user ""
bench --site <site> execute konsol.demo_mode.end_sessions --args "['demo@example.com']"
```

While it is off, `/try` and `/api/method/konsol.demo_mode.try_demo` answer
404. The button is refused (also 404, with the reason in the konsol log) when
the named user is missing, disabled, not a System User, or holds
Administrator, System Manager or EPM Admin.
