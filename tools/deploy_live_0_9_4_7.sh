#!/usr/bin/env bash
set -euo pipefail
ROOT="/opt/fulfillcore"
CONTAINER="${1:-fulfillcore-api-1}"
cd "$ROOT"
TARGET_ROOT=""
for candidate in /app/app /opt/fulfillcore/app /srv/app; do
  if docker exec "$CONTAINER" sh -lc "test -f '$candidate/main.py' && test -f '$candidate/routers/marketplaces.py' && test -f '$candidate/static/app.js'" >/dev/null 2>&1; then TARGET_ROOT="$candidate"; break; fi
done
[ -n "$TARGET_ROOT" ] || { echo "STOP: live app path not found"; exit 1; }
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="/tmp/fc_0_9_4_7_backup_$TS"
WORK="/tmp/fc_0_9_4_7_work_$TS"
mkdir -p "$BACKUP/host/app/routers" "$BACKUP/host/app/static" "$BACKUP/container/app/routers" "$BACKUP/container/app/static" "$WORK/app/routers" "$WORK/app/static"
rollback(){
  set +e
  echo "=== ROLLBACK 0.9.4.7 ==="
  for f in main.py routers/marketplaces.py static/app.js; do
    [ -f "$BACKUP/host/app/$f" ] && cp "$BACKUP/host/app/$f" "$ROOT/app/$f"
    [ -f "$BACKUP/container/app/$f" ] && docker cp "$BACKUP/container/app/$f" "$CONTAINER:$TARGET_ROOT/$f" >/dev/null 2>&1
  done
  docker restart "$CONTAINER" >/dev/null 2>&1 || true
  echo "ROLLBACK COMPLETE. Backup: $BACKUP"
}
trap 'echo "ERROR: 0.9.4.7 deployment failed"; rollback' ERR

echo "[1/9] PRECHECK - current system remains untouched"
docker ps --format '{{.Names}} {{.Status}}' | grep -E "^${CONTAINER} "
python3 -m py_compile tools/apply_tx_auto_label_stability_0_9_4_7.py tools/smoke_check_0_9_4_7.py

echo "[2/9] BACKUP HOST + ACTUAL LIVE CONTAINER"
for f in main.py routers/marketplaces.py static/app.js; do
  cp "$ROOT/app/$f" "$BACKUP/host/app/$f"
  docker cp "$CONTAINER:$TARGET_ROOT/$f" "$BACKUP/container/app/$f" >/dev/null
done
cp "$BACKUP/container/app/main.py" "$WORK/app/main.py"
cp "$BACKUP/container/app/routers/marketplaces.py" "$WORK/app/routers/marketplaces.py"
cp "$BACKUP/container/app/static/app.js" "$WORK/app/static/app.js"

echo "[3/9] VERIFY 0.9.4.6 LIVE FOUNDATION"
grep -q 'version="0.9.4.6"' "$WORK/app/main.py"
grep -q '0.9.4.6 AUTO LABEL WAIT FIX' "$WORK/app/static/app.js"
grep -q '0.9.4.5 WB CHZ PRINT STABILITY' "$WORK/app/routers/marketplaces.py"

echo "[4/9] PATCH TEMP COPY ONLY"
python3 tools/apply_tx_auto_label_stability_0_9_4_7.py --root "$WORK"
python3 -m py_compile "$WORK/app/main.py" "$WORK/app/routers/marketplaces.py"
if command -v node >/dev/null 2>&1; then node --check "$WORK/app/static/app.js"; fi
python3 tools/smoke_check_0_9_4_7.py --root "$WORK"

echo "[5/9] PUBLISH ONLY main.py + marketplaces.py + app.js"
cp "$WORK/app/main.py" "$ROOT/app/main.py"
cp "$WORK/app/routers/marketplaces.py" "$ROOT/app/routers/marketplaces.py"
cp "$WORK/app/static/app.js" "$ROOT/app/static/app.js"
docker cp "$WORK/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py" >/dev/null
docker cp "$WORK/app/routers/marketplaces.py" "$CONTAINER:$TARGET_ROOT/routers/marketplaces.py" >/dev/null
docker cp "$WORK/app/static/app.js" "$CONTAINER:$TARGET_ROOT/static/app.js" >/dev/null

echo "[6/9] VERIFY SHA + SYNTAX INSIDE CONTAINER"
for f in main.py routers/marketplaces.py static/app.js; do
  H="$(sha256sum "$ROOT/app/$f" | awk '{print $1}')"
  C="$(docker exec "$CONTAINER" sha256sum "$TARGET_ROOT/$f" | awk '{print $1}')"
  [ "$H" = "$C" ] || { echo "STOP SHA mismatch $f"; exit 1; }
  echo "OK $f $H"
done
docker exec "$CONTAINER" python3 -m py_compile "$TARGET_ROOT/main.py" "$TARGET_ROOT/routers/marketplaces.py"

echo "[7/9] DB SAFETY NET (best effort, no data changes)"
# Prevent any future code path from pinning a DB transaction indefinitely. This only
# changes the timeout for NEW PostgreSQL sessions; no tables/rows are modified.
docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER DATABASE \"$POSTGRES_DB\" SET idle_in_transaction_session_timeout = '\''45s'\'';"' \
  && echo "OK: idle_in_transaction_session_timeout=45s for new sessions" \
  || echo "WARN: DB timeout could not be changed; application fix is still installed"

echo "[8/9] RESTART API + HEALTH"
docker restart "$CONTAINER" >/dev/null
HEALTH=""
for i in $(seq 1 90); do
  sleep 1
  HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).read().decode())" 2>/dev/null || true)"
  echo "$HEALTH" | grep -q '0.9.4.7' && break
done
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.9.4.7'

echo "[9/9] POST-START TRANSACTION CHECK"
sleep 3
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "SELECT count(*) AS idle_in_tx_over_20s FROM pg_stat_activity WHERE datname=current_database() AND state='\''idle in transaction'\'' AND xact_start < now()-interval '\''20 seconds'\'';"' || true
docker logs --tail 60 "$CONTAINER" 2>&1 | tail -60
trap - ERR
echo "OK: FulfillCore 0.9.4.7 DB_TX_AUTO_LABEL_STABILITY is running"
echo "UNCHANGED: packing write-off, Stock Core, FIFO reservation, Print Agent 1.1.7"
echo "FIXED: auto-label WB wait no longer holds DB transaction; commercial auth no longer holds DB across request"
echo "CHZ: complete orders get one safe direct WB SGTIN attempt instead of permanent defer"
echo "Backup: $BACKUP"
