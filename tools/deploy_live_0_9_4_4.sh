#!/usr/bin/env bash
set -euo pipefail
ROOT="/opt/fulfillcore"
CONTAINER="${1:-fulfillcore-api-1}"
cd "$ROOT"
TARGET_ROOT=""
for candidate in /app/app /opt/fulfillcore/app /srv/app; do
  if docker exec "$CONTAINER" sh -lc "test -f '$candidate/main.py' && test -f '$candidate/routers/marketplaces.py' && test -f '$candidate/services/stock_reservations.py'" >/dev/null 2>&1; then TARGET_ROOT="$candidate"; break; fi
done
[ -n "$TARGET_ROOT" ] || { echo "STOP: live app path not found"; exit 1; }
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="/tmp/fc_0_9_4_4_backup_$TS"
WORK="/tmp/fc_0_9_4_4_work_$TS"
FILES=(main.py routers/marketplaces.py services/stock_reservations.py services/stock_core_v2.py services/runtime_reliability.py static/app.js)
mkdir -p "$BACKUP/host/app/routers" "$BACKUP/host/app/services" "$BACKUP/host/app/static" "$BACKUP/container/app/routers" "$BACKUP/container/app/services" "$BACKUP/container/app/static" "$WORK/app/routers" "$WORK/app/services" "$WORK/app/static"
rollback(){
 set +e; echo "=== ROLLBACK 0.9.4.4 ==="
 for f in "${FILES[@]}"; do
   [ -f "$BACKUP/host/app/$f" ] && cp "$BACKUP/host/app/$f" "$ROOT/app/$f"
   [ -f "$BACKUP/container/app/$f" ] && docker cp "$BACKUP/container/app/$f" "$CONTAINER:$TARGET_ROOT/$f" >/dev/null 2>&1
 done
 docker restart "$CONTAINER" >/dev/null 2>&1 || true
 echo "ROLLBACK COMPLETE. Backup: $BACKUP"
}
trap 'echo "ERROR: 0.9.4.4 deployment failed"; rollback' ERR

echo "[1/11] PRECHECK - no changes"
docker ps --format '{{.Names}} {{.Status}}' | grep -E "^${CONTAINER} "
docker compose ps db
python3 -m py_compile tools/apply_packing_transaction_fix_0_9_4_4.py tools/smoke_check_0_9_4_4.py

echo "[2/11] BACKUP actual HOST + LIVE CONTAINER"
for f in "${FILES[@]}"; do
 mkdir -p "$(dirname "$BACKUP/host/app/$f")" "$(dirname "$BACKUP/container/app/$f")" "$(dirname "$WORK/app/$f")"
 cp "$ROOT/app/$f" "$BACKUP/host/app/$f"
 docker cp "$CONTAINER:$TARGET_ROOT/$f" "$BACKUP/container/app/$f" >/dev/null
 cp "$BACKUP/container/app/$f" "$WORK/app/$f"
done

echo "[3/11] VERIFY 0.9.4.3 live foundation"
grep -q 'version="0.9.4.3"' "$WORK/app/main.py"
grep -q '0.9.4.2 DATABASE STABILITY' "$WORK/app/routers/marketplaces.py"
grep -q 'FIFO_REASSIGNED_TO_OLDER_ORDER' "$WORK/app/services/stock_reservations.py"

echo "[4/11] DB snapshot before change"
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "SELECT state,count(*) FROM pg_stat_activity WHERE datname=current_database() GROUP BY state ORDER BY state;"' || true

echo "[5/11] PATCH TEMP copy only"
python3 tools/apply_packing_transaction_fix_0_9_4_4.py --root "$WORK"
python3 -m py_compile "$WORK/app/main.py" "$WORK/app/routers/marketplaces.py" "$WORK/app/services/stock_reservations.py" "$WORK/app/services/stock_core_v2.py" "$WORK/app/services/runtime_reliability.py"
if command -v node >/dev/null 2>&1; then node --check "$WORK/app/static/app.js"; fi
python3 tools/smoke_check_0_9_4_4.py --root "$WORK"

echo "[6/11] PUBLISH exact tested files to HOST + CONTAINER"
for f in "${FILES[@]}"; do cp "$WORK/app/$f" "$ROOT/app/$f"; docker cp "$WORK/app/$f" "$CONTAINER:$TARGET_ROOT/$f" >/dev/null; done

echo "[7/11] VERIFY HOST == CONTAINER SHA256"
for f in "${FILES[@]}"; do
 H="$(sha256sum "$ROOT/app/$f"|awk '{print $1}')"
 C="$(docker exec "$CONTAINER" sha256sum "$TARGET_ROOT/$f"|awk '{print $1}')"
 [ "$H" = "$C" ] || { echo "STOP SHA mismatch $f"; exit 1; }
 echo "OK $f $H"
done

echo "[8/11] SYNTAX inside container BEFORE restart"
docker exec "$CONTAINER" python3 -m py_compile "$TARGET_ROOT/main.py" "$TARGET_ROOT/routers/marketplaces.py" "$TARGET_ROOT/services/stock_reservations.py" "$TARGET_ROOT/services/stock_core_v2.py" "$TARGET_ROOT/services/runtime_reliability.py"
if docker exec "$CONTAINER" sh -lc 'command -v node >/dev/null 2>&1'; then docker exec "$CONTAINER" node --check "$TARGET_ROOT/static/app.js"; fi

echo "[9/11] RESTART API and wait for exact 0.9.4.4 health"
docker restart "$CONTAINER" >/dev/null
HEALTH=""
for i in $(seq 1 90); do
 sleep 1
 HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).read().decode())" 2>/dev/null || true)"
 echo "$HEALTH" | grep -q '0.9.4.4' && break
done
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.9.4.4'

echo "[10/11] LIVE markers + stale transaction check"
docker exec "$CONTAINER" sh -lc "grep -q 'LOCAL_FIRST_BACKGROUND_WB_0_9_4_4' '$TARGET_ROOT/routers/marketplaces.py' && grep -q 'PACKING_RECOVER_FREE_STOCK_0_9_4_4' '$TARGET_ROOT/services/stock_reservations.py'"
sleep 3
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "SELECT count(*) AS idle_tx_over_30s FROM pg_stat_activity WHERE datname=current_database() AND state=\x27idle in transaction\x27 AND xact_start < now()-interval \x2730 seconds\x27;"' || true

echo "[11/11] SHORT LOG CHECK"
docker logs --tail 60 "$CONTAINER" 2>&1 | tail -60
trap - ERR
echo "OK: FulfillCore 0.9.4.4 PACKING_TRANSACTION_FIX is running"
echo "Backup: $BACKUP"
