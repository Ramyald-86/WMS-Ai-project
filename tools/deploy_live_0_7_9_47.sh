#!/usr/bin/env bash
set -euo pipefail
ROOT="/opt/fulfillcore"
CONTAINER="${1:-fulfillcore-api-1}"
cd "$ROOT"

docker compose \
  --env-file server/.env.server \
  -f docker-compose.server.yml \
  -f docker-compose.hotpatch.yml \
  up -d --build api

for i in $(seq 1 30); do
  docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true && break
  sleep 1
done

TARGET_ROOT=""
for candidate in /app/app /opt/fulfillcore/app /srv/app; do
  if docker exec "$CONTAINER" sh -lc "test -f '$candidate/main.py'" >/dev/null 2>&1; then
    TARGET_ROOT="$candidate"
    break
  fi
done
[ -n "$TARGET_ROOT" ] || { echo "ERROR: live app path not found"; exit 1; }

docker cp "$ROOT/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py"
docker cp "$ROOT/app/static/app.js" "$CONTAINER:$TARGET_ROOT/static/app.js"
docker cp "$ROOT/app/routers/receiving.py" "$CONTAINER:$TARGET_ROOT/routers/receiving.py"
docker cp "$ROOT/app/services/structured_receiving.py" "$CONTAINER:$TARGET_ROOT/services/structured_receiving.py"

docker restart "$CONTAINER" >/dev/null
sleep 5

echo "=== LIVE FILE CHECK ==="
docker exec "$CONTAINER" sh -lc "grep -o 'version=\"0.7.9.[0-9]*\"' '$TARGET_ROOT/main.py' | head -1"
docker exec "$CONTAINER" sh -lc "grep -c 'FC_BUILD_07947' '$TARGET_ROOT/static/app.js'"
docker exec "$CONTAINER" sh -lc "grep -c 'Показать список WB FBS' '$TARGET_ROOT/static/app.js'"

echo "=== LIVE HTTP HEALTH CHECK ==="
HEALTH="$(curl -fsS --max-time 10 http://127.0.0.1:8000/health || true)"
echo "$HEALTH"
echo "$HEALTH" | grep -q '0.7.9.47' || {
  echo "ERROR: /health still does not return 0.7.9.47"
  docker logs --tail 80 "$CONTAINER"
  exit 1
}

echo "OK: LIVE HTTP /health returns 0.7.9.47"
