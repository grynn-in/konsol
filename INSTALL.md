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
