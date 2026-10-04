#!/bin/sh
set -eu

cd /opt/fulfillcore

API_CONTAINER="${API_CONTAINER:-fulfillcore-api-1}"
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP="/tmp/fc_0_9_4_1_chz_backup_${STAMP}"
mkdir -p "$BACKUP"

rollback() {
  echo "ROLLBACK..."
  if [ -f "$BACKUP/marketplaces.py" ]; then
    cp "$BACKUP/marketplaces.py" app/routers/marketplaces.py || true
    docker cp "$BACKUP/marketplaces.py" "$API_CONTAINER:/app/app/routers/marketplaces.py" || true
    docker compose restart api || true
  fi
}
trap rollback INT TERM HUP

echo "[1/8] Backup current marketplaces.py"
cp app/routers/marketplaces.py "$BACKUP/marketplaces.py"
docker cp "$API_CONTAINER:/app/app/routers/marketplaces.py" "$BACKUP/marketplaces.container.py" >/dev/null 2>&1 || true

echo "[2/8] Check current syntax"
python3 -m py_compile app/routers/marketplaces.py

echo "[3/8] Apply 0.9.4.1 patch on HOST"
python3 tools/apply_chz_cancel_release_0_9_4_1.py
python3 -m py_compile app/routers/marketplaces.py
python3 tools/smoke_check_0_9_4_1.py

echo "[4/8] Copy patched router + backfill into running container"
docker cp app/routers/marketplaces.py "$API_CONTAINER:/app/app/routers/marketplaces.py"
docker cp tools/backfill_cancelled_chz_0_9_4_1.py "$API_CONTAINER:/tmp/backfill_cancelled_chz_0_9_4_1.py"

echo "[5/8] Validate inside container BEFORE restart"
docker exec "$API_CONTAINER" python -m py_compile /app/app/routers/marketplaces.py

echo "[6/8] Restart API"
docker compose restart api

echo "[7/8] Wait for health"
i=0
while [ "$i" -lt 30 ]; do
  if curl -fsS http://127.0.0.1:8000/health >/tmp/fc_health_0941.txt 2>/dev/null; then
    cat /tmp/fc_health_0941.txt
    break
  fi
  i=$((i+1))
  sleep 2
done
if [ "$i" -ge 30 ]; then
  echo "STOP: API health did not recover"
  rollback
  exit 2
fi

echo "[8/8] Release CHZ from already-cancelled orders"
docker exec "$API_CONTAINER" python /tmp/backfill_cancelled_chz_0_9_4_1.py

touch VERSION_HOTFIX_0.9.4.1_CHZ_CANCEL_RELEASE
trap - INT TERM HUP
echo "OK: FulfillCore 0.9.4.1 CHZ_CANCEL_RELEASE is installed"
echo "Backup: $BACKUP"
