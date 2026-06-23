#!/usr/bin/env bash
# Hot-deploy local konsol-exec SPA + control_api into running konsolidat_backend.
#
# Build order:
#   1. yarn build   — Vite compiles React → konsol/public/konsol_exec/
#   2. docker cp    — sync app source + built SPA into container
#   3. bench build  — relink assets + refresh konsol asset manifest
#   4. clear-cache  — evict Redis assets_json so Frappe serves fresh files
set -euo pipefail

KONSOL_SRC="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER="${KONSOL_CONTAINER:-konsolidat_backend}"
SITE="${KONSOL_SITE:-konsolidat.local}"

echo "==> [1/4] yarn build (konsol-exec Vite SPA)"
(cd "$KONSOL_SRC/konsol-exec" && yarn build)

echo "==> [2/4] docker cp → $CONTAINER"
docker cp "$KONSOL_SRC/konsol/." "$CONTAINER:/home/frappe/frappe-bench/apps/konsol/konsol/"
docker cp "$KONSOL_SRC/konsol-exec/." "$CONTAINER:/home/frappe/frappe-bench/apps/konsol/konsol-exec/"
docker cp "$KONSOL_SRC/konsol/public/konsol_exec/." \
	"$CONTAINER:/home/frappe/frappe-bench/apps/konsol/konsol/public/konsol_exec/"

echo "==> [3/4] bench build --app konsol (inside container; non-fatal)"
docker exec -u frappe "$CONTAINER" bash -lc "
	cd /home/frappe/frappe-bench
	bench build --app konsol || echo 'WARN: bench build failed (SPA static assets still deployed)'
"

echo "==> [4/4] clear-cache + evict assets_json + restart"
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