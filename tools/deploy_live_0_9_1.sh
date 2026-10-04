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

echo "[1/4] Installing strict identity hotfix..."
docker cp "$ROOT/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py"
docker cp "$ROOT/app/routers/marketplaces.py" "$CONTAINER:$TARGET_ROOT/routers/marketplaces.py"
docker cp "$ROOT/app/services/stock_reservations.py" "$CONTAINER:$TARGET_ROOT/services/stock_reservations.py"

echo "[2/4] Restarting API..."
docker restart "$CONTAINER" >/dev/null
sleep 8

echo "[3/4] Verifying Python and health..."
docker exec "$CONTAINER" python3 -m py_compile "$TARGET_ROOT/main.py" "$TARGET_ROOT/routers/marketplaces.py" "$TARGET_ROOT/services/stock_reservations.py"
HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10).read().decode())")"
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.9.1' || { echo "ERROR: /health is not 0.9.1"; docker logs --tail 120 "$CONTAINER"; exit 1; }

echo "[4/4] Strict identity markers..."
docker exec "$CONTAINER" sh -lc "grep -q 'def _strict_wb_order_sku' '$TARGET_ROOT/routers/marketplaces.py'"
docker exec "$CONTAINER" sh -lc "grep -q 'STRICT_SKU_MISMATCH_REPAIR' '$TARGET_ROOT/services/stock_reservations.py'"
echo "OK: LIVE FulfillCore 0.9.1 strict SKU picking protection is running."
