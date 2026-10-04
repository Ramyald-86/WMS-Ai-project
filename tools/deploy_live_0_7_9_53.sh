#!/usr/bin/env bash
set -euo pipefail
ROOT="/opt/fulfillcore"
CONTAINER="${1:-fulfillcore-api-1}"
cd "$ROOT"

TARGET_ROOT=""
for candidate in /app/app /opt/fulfillcore/app /srv/app; do
  if docker exec "$CONTAINER" sh -lc "test -f '$candidate/main.py'" >/dev/null 2>&1; then TARGET_ROOT="$candidate"; break; fi
done
[ -n "$TARGET_ROOT" ] || { echo "ERROR: live app path not found"; exit 1; }

docker cp "$ROOT/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py"
docker cp "$ROOT/app/static/app.js" "$CONTAINER:$TARGET_ROOT/static/app.js"
docker cp "$ROOT/app/routers/receiving.py" "$CONTAINER:$TARGET_ROOT/routers/receiving.py"
docker cp "$ROOT/app/services/structured_receiving.py" "$CONTAINER:$TARGET_ROOT/services/structured_receiving.py"
docker restart "$CONTAINER" >/dev/null
sleep 5

echo "Version:"
docker exec "$CONTAINER" sh -lc "grep -o 'version=\"0.7.9.[0-9]*\"' '$TARGET_ROOT/main.py' | head -1"
echo "Build marker:"
docker exec "$CONTAINER" sh -lc "grep -c 'FC_BUILD_07953' '$TARGET_ROOT/static/app.js'"
echo "Errors Excel marker:"
docker exec "$CONTAINER" sh -lc "grep -c 'errors.xlsx' '$TARGET_ROOT/routers/receiving.py'"
echo "Manual WB button marker:"
docker exec "$CONTAINER" sh -lc "grep -c 'Принять на остатки WB FBS' '$TARGET_ROOT/static/app.js'"

HEALTH="$(curl -fsS --max-time 10 http://127.0.0.1:8000/health || true)"
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.7.9.53' || { echo "ERROR: /health is not 0.7.9.53"; exit 1; }
echo "OK: LIVE FulfillCore 0.7.9.53 manual WB-after-receiving flow is running."
