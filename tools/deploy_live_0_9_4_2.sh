#!/usr/bin/env bash
set -euo pipefail
ROOT="/opt/fulfillcore"
CONTAINER="${1:-fulfillcore-api-1}"
cd "$ROOT"

TARGET_ROOT=""
for candidate in /app/app /opt/fulfillcore/app /srv/app; do
  if docker exec "$CONTAINER" sh -lc "test -f '$candidate/main.py' && test -f '$candidate/routers/marketplaces.py'" >/dev/null 2>&1; then
    TARGET_ROOT="$candidate"; break
  fi
done
[ -n "$TARGET_ROOT" ] || { echo "STOP: live app path not found"; exit 1; }

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="/tmp/fc_0_9_4_2_backup_$TS"
WORK="/tmp/fc_0_9_4_2_work_$TS"
mkdir -p "$BACKUP/host/app/routers" "$BACKUP/container/app/routers" "$WORK/app/routers"

rollback(){
  set +e
  echo "=== ROLLBACK 0.9.4.2 ==="
  [ -f "$BACKUP/host/app/main.py" ] && cp "$BACKUP/host/app/main.py" "$ROOT/app/main.py"
  [ -f "$BACKUP/host/app/routers/marketplaces.py" ] && cp "$BACKUP/host/app/routers/marketplaces.py" "$ROOT/app/routers/marketplaces.py"
  [ -f "$BACKUP/container/app/main.py" ] && docker cp "$BACKUP/container/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py" >/dev/null 2>&1
  [ -f "$BACKUP/container/app/routers/marketplaces.py" ] && docker cp "$BACKUP/container/app/routers/marketplaces.py" "$CONTAINER:$TARGET_ROOT/routers/marketplaces.py" >/dev/null 2>&1
  docker restart "$CONTAINER" >/dev/null 2>&1 || true
  echo "ROLLBACK COMPLETE. Backup: $BACKUP"
}
trap 'echo "ERROR: 0.9.4.2 deployment failed"; rollback' ERR

echo "[1/9] Preflight - do not modify anything yet"
docker ps --format '{{.Names}} {{.Status}}' | grep -E "^${CONTAINER} "
docker compose ps db
python3 -m py_compile tools/apply_db_stability_0_9_4_2.py tools/smoke_check_0_9_4_2.py

echo "[2/9] Backup current HOST and current LIVE CONTAINER"
cp app/main.py "$BACKUP/host/app/main.py"
cp app/routers/marketplaces.py "$BACKUP/host/app/routers/marketplaces.py"
docker cp "$CONTAINER:$TARGET_ROOT/main.py" "$BACKUP/container/app/main.py" >/dev/null
docker cp "$CONTAINER:$TARGET_ROOT/routers/marketplaces.py" "$BACKUP/container/app/routers/marketplaces.py" >/dev/null

echo "[3/9] Compare HOST vs CONTAINER before patch"
for f in main.py routers/marketplaces.py; do
  H="$(sha256sum "$ROOT/app/$f" | awk '{print $1}')"
  C="$(sha256sum "$BACKUP/container/app/$f" | awk '{print $1}')"
  echo "$f HOST=$H CONTAINER=$C"
done
# The running container is the source of truth. This preserves any live fixes that were
# applied after the old archive was built and prevents regressions from a stale source ZIP.
cp "$BACKUP/container/app/main.py" "$WORK/app/main.py"
cp "$BACKUP/container/app/routers/marketplaces.py" "$WORK/app/routers/marketplaces.py"

echo "[4/9] Patch a TEMP copy of the actual running code"
python3 tools/apply_db_stability_0_9_4_2.py --root "$WORK"
python3 -m py_compile "$WORK/app/main.py" "$WORK/app/routers/marketplaces.py"
python3 tools/smoke_check_0_9_4_2.py --root "$WORK"

echo "[5/9] Publish patched files to HOST and CONTAINER"
cp "$WORK/app/main.py" app/main.py
cp "$WORK/app/routers/marketplaces.py" app/routers/marketplaces.py
docker cp "$WORK/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py"
docker cp "$WORK/app/routers/marketplaces.py" "$CONTAINER:$TARGET_ROOT/routers/marketplaces.py"

echo "[6/9] Verify HOST == CONTAINER SHA256 after copy"
for f in main.py routers/marketplaces.py; do
  H="$(sha256sum "$ROOT/app/$f" | awk '{print $1}')"
  C="$(docker exec "$CONTAINER" sha256sum "$TARGET_ROOT/$f" | awk '{print $1}')"
  [ "$H" = "$C" ] || { echo "STOP: SHA mismatch $f"; exit 1; }
  echo "OK $f $H"
done

echo "[7/9] Syntax check INSIDE live container before restart"
docker exec "$CONTAINER" python3 -m py_compile "$TARGET_ROOT/main.py" "$TARGET_ROOT/routers/marketplaces.py"

echo "[8/9] Restart API and wait up to 90 seconds for real health"
docker restart "$CONTAINER" >/dev/null
HEALTH=""
for i in $(seq 1 90); do
  sleep 1
  HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).read().decode())" 2>/dev/null || true)"
  echo "$HEALTH" | grep -q '0.9.4.2' && break
done
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.9.4.2'

echo "[9/9] Final markers"
docker exec "$CONTAINER" sh -lc "grep -q '0.9.4.2 DATABASE STABILITY' '$TARGET_ROOT/routers/marketplaces.py' && grep -q 'version=\"0.9.4.2\"' '$TARGET_ROOT/main.py'"
trap - ERR
echo "OK: FulfillCore 0.9.4.2 DATABASE_STABILITY is running"
echo "Backup: $BACKUP"
