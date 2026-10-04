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
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="/tmp/fc_0_9_4_backup_$TS"
FILES=(main.py routers/receiving.py routers/marketplaces.py services/stock_reservations.py static/app.js)
rollback(){
  echo "ROLLBACK: restoring previous container files..."
  for f in "${FILES[@]}"; do docker exec "$CONTAINER" sh -lc "test -f '$BACKUP/$f' && cp '$BACKUP/$f' '$TARGET_ROOT/$f' || true"; done
  docker restart "$CONTAINER" >/dev/null || true
}
trap 'echo "ERROR: 0.9.4 deployment failed"; rollback' ERR

echo "[1/7] Host syntax checks..."
python3 -m py_compile app/main.py app/routers/receiving.py app/routers/marketplaces.py app/services/stock_reservations.py
python3 tools/smoke_check_0_9_4.py
docker run --rm -v "$ROOT/app/static:/src:ro" node:22-alpine node --check /src/app.js

echo "[2/7] Backing up live container..."
docker exec "$CONTAINER" sh -lc "mkdir -p '$BACKUP/routers' '$BACKUP/services' '$BACKUP/static'; cp '$TARGET_ROOT/main.py' '$BACKUP/main.py'; cp '$TARGET_ROOT/routers/receiving.py' '$BACKUP/routers/receiving.py'; cp '$TARGET_ROOT/routers/marketplaces.py' '$BACKUP/routers/marketplaces.py'; cp '$TARGET_ROOT/services/stock_reservations.py' '$BACKUP/services/stock_reservations.py'; cp '$TARGET_ROOT/static/app.js' '$BACKUP/static/app.js'"

echo "[3/7] Copying 0.9.4 files..."
for f in "${FILES[@]}"; do docker cp "$ROOT/app/$f" "$CONTAINER:$TARGET_ROOT/$f"; done

echo "[4/7] Verifying HOST == CONTAINER SHA256..."
for f in "${FILES[@]}"; do
  H="$(sha256sum "$ROOT/app/$f" | awk '{print $1}')"
  C="$(docker exec "$CONTAINER" sha256sum "$TARGET_ROOT/$f" | awk '{print $1}')"
  [ "$H" = "$C" ] || { echo "ERROR hash mismatch: $f HOST=$H CONTAINER=$C"; exit 1; }
  echo "OK $f $H"
done

echo "[5/7] Container syntax checks before restart..."
docker exec "$CONTAINER" python3 -m py_compile "$TARGET_ROOT/main.py" "$TARGET_ROOT/routers/receiving.py" "$TARGET_ROOT/routers/marketplaces.py" "$TARGET_ROOT/services/stock_reservations.py"

echo "[6/7] Restarting API and waiting for 0.9.4 health..."
docker restart "$CONTAINER" >/dev/null
HEALTH=""
for i in $(seq 1 45); do
  sleep 1
  HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).read().decode())" 2>/dev/null || true)"
  echo "$HEALTH" | grep -q '0.9.4' && break
done
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.9.4'

echo "[7/7] Final markers..."
docker exec "$CONTAINER" sh -lc "grep -q 'def _wb_publish_amounts_partial' '$TARGET_ROOT/routers/receiving.py' && grep -q 'def wb_shortage_xlsx' '$TARGET_ROOT/routers/marketplaces.py' && grep -q 'installTopTableScrolls' '$TARGET_ROOT/static/app.js'"
trap - ERR
echo "OK: FulfillCore 0.9.4 is running. Backup: $BACKUP"
