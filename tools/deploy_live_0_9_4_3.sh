#!/usr/bin/env bash
set -euo pipefail
ROOT="/opt/fulfillcore"
CONTAINER="${1:-fulfillcore-api-1}"
cd "$ROOT"
TARGET_ROOT=""
for candidate in /app/app /opt/fulfillcore/app /srv/app; do
  if docker exec "$CONTAINER" sh -lc "test -f '$candidate/main.py' && test -f '$candidate/routers/marketplaces.py' && test -f '$candidate/services/stock_core_v2.py'" >/dev/null 2>&1; then TARGET_ROOT="$candidate"; break; fi
done
[ -n "$TARGET_ROOT" ] || { echo "STOP: live app path not found"; exit 1; }
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="/tmp/fc_0_9_4_3_backup_$TS"
WORK="/tmp/fc_0_9_4_3_work_$TS"
FILES=(main.py routers/marketplaces.py services/stock_core_v2.py services/stock_reservations.py services/runtime_reliability.py static/app.js)
mkdir -p "$BACKUP/host/app/routers" "$BACKUP/host/app/services" "$BACKUP/host/app/static" "$BACKUP/container/app/routers" "$BACKUP/container/app/services" "$BACKUP/container/app/static" "$WORK/app/routers" "$WORK/app/services" "$WORK/app/static"
rollback(){
 set +e; echo "=== ROLLBACK 0.9.4.3 ==="
 for f in "${FILES[@]}"; do
   [ -f "$BACKUP/host/app/$f" ] && cp "$BACKUP/host/app/$f" "$ROOT/app/$f"
   [ -f "$BACKUP/container/app/$f" ] && docker cp "$BACKUP/container/app/$f" "$CONTAINER:$TARGET_ROOT/$f" >/dev/null 2>&1
 done
 docker restart "$CONTAINER" >/dev/null 2>&1 || true
 echo "ROLLBACK COMPLETE. Backup: $BACKUP"
}
trap 'echo "ERROR: 0.9.4.3 deployment failed"; rollback' ERR

echo "[1/10] Preflight - no modifications"
docker ps --format '{{.Names}} {{.Status}}' | grep -E "^${CONTAINER} "
docker compose ps db
python3 -m py_compile tools/apply_packing_stability_0_9_4_3.py tools/smoke_check_0_9_4_3.py

echo "[2/10] Backup current HOST and LIVE CONTAINER"
for f in "${FILES[@]}"; do
  mkdir -p "$(dirname "$BACKUP/host/app/$f")" "$(dirname "$BACKUP/container/app/$f")" "$(dirname "$WORK/app/$f")"
  cp "$ROOT/app/$f" "$BACKUP/host/app/$f"
  docker cp "$CONTAINER:$TARGET_ROOT/$f" "$BACKUP/container/app/$f" >/dev/null
  cp "$BACKUP/container/app/$f" "$WORK/app/$f"
done

echo "[3/10] Verify live 0.9.4.2 foundation"
grep -q '0.9.4.2 DATABASE STABILITY' "$WORK/app/routers/marketplaces.py"
grep -q 'version="0.9.4.2"' "$WORK/app/main.py"

echo "[4/10] Patch TEMP copy of actual running files"
python3 tools/apply_packing_stability_0_9_4_3.py --root "$WORK"
python3 -m py_compile "$WORK/app/main.py" "$WORK/app/routers/marketplaces.py" "$WORK/app/services/stock_core_v2.py" "$WORK/app/services/stock_reservations.py" "$WORK/app/services/runtime_reliability.py"
if command -v node >/dev/null 2>&1; then node --check "$WORK/app/static/app.js"; else echo "WARN: node not on host; JS will be checked in container if available"; fi
python3 tools/smoke_check_0_9_4_3.py --root "$WORK"

echo "[5/10] Publish to HOST and CONTAINER"
for f in "${FILES[@]}"; do cp "$WORK/app/$f" "$ROOT/app/$f"; docker cp "$WORK/app/$f" "$CONTAINER:$TARGET_ROOT/$f" >/dev/null; done

echo "[6/10] Verify HOST == CONTAINER SHA256"
for f in "${FILES[@]}"; do H="$(sha256sum "$ROOT/app/$f"|awk '{print $1}')"; C="$(docker exec "$CONTAINER" sha256sum "$TARGET_ROOT/$f"|awk '{print $1}')"; [ "$H" = "$C" ] || { echo "STOP SHA mismatch $f"; exit 1; }; echo "OK $f $H"; done

echo "[7/10] Syntax inside container before restart"
docker exec "$CONTAINER" python3 -m py_compile "$TARGET_ROOT/main.py" "$TARGET_ROOT/routers/marketplaces.py" "$TARGET_ROOT/services/stock_core_v2.py" "$TARGET_ROOT/services/stock_reservations.py" "$TARGET_ROOT/services/runtime_reliability.py"
if docker exec "$CONTAINER" sh -lc 'command -v node >/dev/null 2>&1'; then docker exec "$CONTAINER" node --check "$TARGET_ROOT/static/app.js"; fi

echo "[8/10] Restart API and wait health"
docker restart "$CONTAINER" >/dev/null
HEALTH=""
for i in $(seq 1 90); do sleep 1; HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).read().decode())" 2>/dev/null || true)"; echo "$HEALTH" | grep -q '0.9.4.3' && break; done
echo "HTTP /health: $HEALTH"; echo "$HEALTH" | grep -q '0.9.4.3'

echo "[9/10] Final live markers"
docker exec "$CONTAINER" sh -lc "grep -q 'PACKING_WRITE_OFF' '$TARGET_ROOT/services/stock_core_v2.py' && grep -q 'FIFO_REASSIGNED_TO_OLDER_ORDER' '$TARGET_ROOT/services/stock_reservations.py' && grep -q 'PACKING LEASE NONBLOCKING' '$TARGET_ROOT/services/runtime_reliability.py' && grep -q 'scan product bounded timeout' '$TARGET_ROOT/static/app.js'"

echo "[10/10] Short log check"
sleep 3
docker logs --tail 40 "$CONTAINER" 2>&1 | tail -40
trap - ERR
echo "OK: FulfillCore 0.9.4.3 PACKING_FIFO_STABILITY is running"
echo "Backup: $BACKUP"
