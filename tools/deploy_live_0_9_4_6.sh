#!/usr/bin/env bash
set -euo pipefail
ROOT="/opt/fulfillcore"
CONTAINER="${1:-fulfillcore-api-1}"
cd "$ROOT"
TARGET_ROOT=""
for candidate in /app/app /opt/fulfillcore/app /srv/app; do
  if docker exec "$CONTAINER" sh -lc "test -f '$candidate/main.py' && test -f '$candidate/static/app.js'" >/dev/null 2>&1; then TARGET_ROOT="$candidate"; break; fi
done
[ -n "$TARGET_ROOT" ] || { echo "STOP: live app path not found"; exit 1; }
TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="/tmp/fc_0_9_4_6_backup_$TS"
WORK="/tmp/fc_0_9_4_6_work_$TS"
mkdir -p "$BACKUP/host/app/static" "$BACKUP/container/app/static" "$WORK/app/static"
rollback(){
  set +e
  echo "=== ROLLBACK 0.9.4.6 ==="
  [ -f "$BACKUP/host/app/main.py" ] && cp "$BACKUP/host/app/main.py" "$ROOT/app/main.py"
  [ -f "$BACKUP/host/app/static/app.js" ] && cp "$BACKUP/host/app/static/app.js" "$ROOT/app/static/app.js"
  [ -f "$BACKUP/container/app/main.py" ] && docker cp "$BACKUP/container/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py" >/dev/null 2>&1
  [ -f "$BACKUP/container/app/static/app.js" ] && docker cp "$BACKUP/container/app/static/app.js" "$CONTAINER:$TARGET_ROOT/static/app.js" >/dev/null 2>&1
  docker restart "$CONTAINER" >/dev/null 2>&1 || true
  echo "ROLLBACK COMPLETE. Backup: $BACKUP"
}
trap 'echo "ERROR: 0.9.4.6 deployment failed"; rollback' ERR

echo "[1/7] PRECHECK"
docker ps --format '{{.Names}} {{.Status}}' | grep -E "^${CONTAINER} "
python3 -m py_compile tools/apply_auto_label_wait_0_9_4_6.py tools/smoke_check_0_9_4_6.py

echo "[2/7] BACKUP HOST + LIVE CONTAINER"
cp "$ROOT/app/main.py" "$BACKUP/host/app/main.py"
cp "$ROOT/app/static/app.js" "$BACKUP/host/app/static/app.js"
docker cp "$CONTAINER:$TARGET_ROOT/main.py" "$BACKUP/container/app/main.py" >/dev/null
docker cp "$CONTAINER:$TARGET_ROOT/static/app.js" "$BACKUP/container/app/static/app.js" >/dev/null
cp "$BACKUP/container/app/main.py" "$WORK/app/main.py"
cp "$BACKUP/container/app/static/app.js" "$WORK/app/static/app.js"

echo "[3/7] VERIFY 0.9.4.5 FOUNDATION + PATCH TEMP COPY"
grep -q 'version="0.9.4.5"' "$WORK/app/main.py"
grep -q '0.9.4.5 order card nonblocking WB refresh' "$WORK/app/static/app.js"
python3 tools/apply_auto_label_wait_0_9_4_6.py --root "$WORK"
python3 -m py_compile "$WORK/app/main.py"
if command -v node >/dev/null 2>&1; then node --check "$WORK/app/static/app.js"; fi
python3 tools/smoke_check_0_9_4_6.py --root "$WORK"

echo "[4/7] PUBLISH ONLY main.py + app.js"
cp "$WORK/app/main.py" "$ROOT/app/main.py"
cp "$WORK/app/static/app.js" "$ROOT/app/static/app.js"
docker cp "$WORK/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py" >/dev/null
docker cp "$WORK/app/static/app.js" "$CONTAINER:$TARGET_ROOT/static/app.js" >/dev/null

echo "[5/7] VERIFY SHA + SYNTAX INSIDE CONTAINER"
for f in main.py static/app.js; do
  H="$(sha256sum "$ROOT/app/$f" | awk '{print $1}')"
  C="$(docker exec "$CONTAINER" sha256sum "$TARGET_ROOT/$f" | awk '{print $1}')"
  [ "$H" = "$C" ] || { echo "STOP SHA mismatch $f"; exit 1; }
  echo "OK $f $H"
done
docker exec "$CONTAINER" python3 -m py_compile "$TARGET_ROOT/main.py"
if docker exec "$CONTAINER" sh -lc 'command -v node >/dev/null 2>&1'; then docker exec "$CONTAINER" node --check "$TARGET_ROOT/static/app.js"; fi

echo "[6/7] RESTART API + HEALTH"
docker restart "$CONTAINER" >/dev/null
HEALTH=""
for i in $(seq 1 60); do
  sleep 1
  HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).read().decode())" 2>/dev/null || true)"
  echo "$HEALTH" | grep -q '0.9.4.6' && break
done
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.9.4.6'

echo "[7/7] SHORT SAFETY CHECK"
docker logs --tail 50 "$CONTAINER" 2>&1 | tail -50
trap - ERR
echo "OK: FulfillCore 0.9.4.6 AUTO_LABEL_WAIT_FIX is running"
echo "UNCHANGED: packing transaction, reservation, CHZ worker, Print Agent"
echo "Backup: $BACKUP"
