#!/usr/bin/env bash
# Hot-deploy local konsol-exec SPA + control_api into running konsolidat_backend.
#
# Build order:
#   1. docker cp    — sync app source into container
#   2. bench build  — yarn build (Vite SPA) + relink assets + refresh manifest
#   3. clear-cache  — evict Redis assets_json so Frappe serves fresh files
set -euo pipefail

KONSOL_SRC="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER="${KONSOL_CONTAINER:-konsolidat_backend}"
SITE="${KONSOL_SITE:-konsolidat.local}"

echo "==> [1/3] docker cp → $CONTAINER"
docker cp "$KONSOL_SRC/konsol/." "$CONTAINER:/home/frappe/frappe-bench/apps/konsol/konsol/"
docker cp "$KONSOL_SRC/konsol-exec/." "$CONTAINER:/home/frappe/frappe-bench/apps/konsol/konsol-exec/"
docker cp "$KONSOL_SRC/package.json" "$CONTAINER:/home/frappe/frappe-bench/apps/konsol/package.json"

echo "==> [2/3] bench build --app konsol (runs yarn build → Vite SPA)"
docker exec -u frappe "$CONTAINER" bash -lc "
	cd /home/frappe/frappe-bench
	bench build --app konsol
"

echo "==> [3/3] clear-cache + evict assets_json + restart"
docker exec -u frappe "$CONTAINER" bash -lc "
	cd /home/frappe/frappe-bench
	bench --site $SITE clear-cache
	./env/bin/python - <<'PY' || true
import os
import redis
host = os.environ.get('REDIS_CACHE_HOST', 'redis_cache')
redis.Redis(host=host, port=6379, socket_timeout=2).delete('assets_json')
print('assets_json evicted')
PY
	bench restart
"

echo "==> Done. Open https://$SITE/konsol-exec (hard refresh: Cmd+Shift+R)"