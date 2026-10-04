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
docker cp "$ROOT/app/static/commercial-login.html" "$CONTAINER:$TARGET_ROOT/static/commercial-login.html"
docker cp "$ROOT/app/static/commercial-admin.html" "$CONTAINER:$TARGET_ROOT/static/commercial-admin.html"
docker exec "$CONTAINER" sh -lc "mkdir -p '$TARGET_ROOT/downloads'"
docker cp "$ROOT/app/downloads/FulfillCore_Print_Agent_1.2.3_RELIABLE_WATCHDOG.zip" "$CONTAINER:$TARGET_ROOT/downloads/FulfillCore_Print_Agent_1.2.3_RELIABLE_WATCHDOG.zip"
docker cp "$ROOT/app/downloads/FulfillCore_Print_Agent_1.2.3_Инструкция.txt" "$CONTAINER:$TARGET_ROOT/downloads/FulfillCore_Print_Agent_1.2.3_Инструкция.txt"
docker cp "$ROOT/app/routers/commercial.py" "$CONTAINER:$TARGET_ROOT/routers/commercial.py"
docker cp "$ROOT/app/routers/receiving.py" "$CONTAINER:$TARGET_ROOT/routers/receiving.py"
docker cp "$ROOT/app/routers/marketplaces.py" "$CONTAINER:$TARGET_ROOT/routers/marketplaces.py"
docker cp "$ROOT/app/services/stock_reservations.py" "$CONTAINER:$TARGET_ROOT/services/stock_reservations.py"
docker cp "$ROOT/app/services/structured_receiving.py" "$CONTAINER:$TARGET_ROOT/services/structured_receiving.py"

docker restart "$CONTAINER" >/dev/null
sleep 6

echo "Version:"
docker exec "$CONTAINER" sh -lc "grep -o 'version=\"0.8.1\"' '$TARGET_ROOT/main.py' | head -1"
echo "Commercial router:"
docker exec "$CONTAINER" sh -lc "grep -c 'fc_fulfillment_tenants' '$TARGET_ROOT/routers/commercial.py'"
echo "Commercial UI:"
docker exec "$CONTAINER" sh -lc "test -f '$TARGET_ROOT/static/commercial-admin.html' && echo OK"

HEALTH="$(curl -fsS --max-time 10 http://127.0.0.1:8000/health || true)"
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.8.1' || { echo "ERROR: /health is not 0.8.1"; docker logs --tail 100 "$CONTAINER"; exit 1; }
echo "OK: LIVE FulfillCore 0.8.1 is running."
