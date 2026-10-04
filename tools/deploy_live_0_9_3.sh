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
echo "[1/4] Installing 0.9.3 immediate FBS reservation hotfix..."
docker cp "$ROOT/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py"
docker cp "$ROOT/app/routers/marketplaces.py" "$CONTAINER:$TARGET_ROOT/routers/marketplaces.py"
docker cp "$ROOT/app/services/stock_reservations.py" "$CONTAINER:$TARGET_ROOT/services/stock_reservations.py"
docker cp "$ROOT/app/static/app.js" "$CONTAINER:$TARGET_ROOT/static/app.js"
echo "[2/4] Restarting API..."
docker restart "$CONTAINER" >/dev/null
echo "[3/4] Waiting for health..."
HEALTH=""
for i in $(seq 1 40); do
  sleep 1
  HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).read().decode())" 2>/dev/null || true)"
  if echo "$HEALTH" | grep -q '0.9.3'; then break; fi
done
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.9.3' || { echo "ERROR: /health is not 0.9.3"; docker logs --tail 120 "$CONTAINER"; exit 1; }
echo "[4/4] Verifying immediate reservation markers..."
docker exec "$CONTAINER" python3 -m py_compile "$TARGET_ROOT/main.py" "$TARGET_ROOT/routers/marketplaces.py" "$TARGET_ROOT/services/stock_reservations.py"
docker exec "$CONTAINER" sh -lc "grep -q '0.9.3_EXACT_SKU_IMMEDIATE_RESERVE' '$TARGET_ROOT/routers/marketplaces.py'"
docker exec "$CONTAINER" sh -lc "grep -q 'WB_CANCELLED_BEFORE_PICKING' '$TARGET_ROOT/routers/marketplaces.py'"
docker exec "$CONTAINER" sh -lc "grep -q 'installed agents send POST /result without' '$TARGET_ROOT/main.py'"
echo "OK: LIVE FulfillCore 0.9.3 immediate exact-SKU FBS reservation is running."
