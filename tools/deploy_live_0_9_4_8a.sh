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
BACKUP="/tmp/fc_0_9_4_8a_route_backup_$TS"
WORK="/tmp/fc_0_9_4_8a_route_work_$TS"
mkdir -p "$BACKUP/host/app/routers" "$BACKUP/container/app/routers" "$WORK/app/routers" "$WORK/tools"

rollback(){
  set +e
  echo "=== ROLLBACK 0.9.4.8a ==="
  [ -f "$BACKUP/host/app/main.py" ] && cp "$BACKUP/host/app/main.py" "$ROOT/app/main.py"
  [ -f "$BACKUP/host/app/routers/marketplaces.py" ] && cp "$BACKUP/host/app/routers/marketplaces.py" "$ROOT/app/routers/marketplaces.py"
  [ -f "$BACKUP/container/app/main.py" ] && docker cp "$BACKUP/container/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py" >/dev/null 2>&1 || true
  [ -f "$BACKUP/container/app/routers/marketplaces.py" ] && docker cp "$BACKUP/container/app/routers/marketplaces.py" "$CONTAINER:$TARGET_ROOT/routers/marketplaces.py" >/dev/null 2>&1 || true
  docker restart "$CONTAINER" >/dev/null 2>&1 || true
  echo "ROLLBACK COMPLETE: $BACKUP"
}
trap 'echo "ERROR: route restore failed"; rollback' ERR

echo "[1/7] BACKUP CURRENT LIVE FILES"
cp "$ROOT/app/main.py" "$BACKUP/host/app/main.py"
cp "$ROOT/app/routers/marketplaces.py" "$BACKUP/host/app/routers/marketplaces.py"
docker cp "$CONTAINER:$TARGET_ROOT/main.py" "$BACKUP/container/app/main.py" >/dev/null
docker cp "$CONTAINER:$TARGET_ROOT/routers/marketplaces.py" "$BACKUP/container/app/routers/marketplaces.py" >/dev/null
cp "$BACKUP/container/app/main.py" "$WORK/app/main.py"
cp "$BACKUP/container/app/routers/marketplaces.py" "$WORK/app/routers/marketplaces.py"
if [ -f "$ROOT/tools/apply_rev2_0_9_4_8.py" ]; then cp "$ROOT/tools/apply_rev2_0_9_4_8.py" "$WORK/tools/apply_rev2_0_9_4_8.py"; fi

echo "[2/7] APPLY ONLY ROUTE RESTORE"
python3 tools/apply_route_restore_0_9_4_8a.py --root "$WORK"
python3 tools/smoke_check_0_9_4_8a.py --root "$WORK"
python3 -m py_compile "$WORK/app/main.py" "$WORK/app/routers/marketplaces.py"

echo "[3/7] SHOW EXACT ROUTES BEFORE PUBLISH"
python3 - "$WORK/app/routers/marketplaces.py" <<'PY'
import ast,sys
s=open(sys.argv[1],encoding='utf-8').read(); t=ast.parse(s)
for n in ast.walk(t):
    if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in {'wb_scan_product','wb_scan_chz','wb_packing_progress','wb_queue_order_label'}:
        for d in n.decorator_list:
            if isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and d.args and isinstance(d.args[0],ast.Constant):
                print(n.name, d.func.attr.upper(), d.args[0].value)
PY

echo "[4/7] PUBLISH TWO APPLICATION FILES ONLY"
cp "$WORK/app/main.py" "$ROOT/app/main.py"
cp "$WORK/app/routers/marketplaces.py" "$ROOT/app/routers/marketplaces.py"
docker cp "$WORK/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py" >/dev/null
docker cp "$WORK/app/routers/marketplaces.py" "$CONTAINER:$TARGET_ROOT/routers/marketplaces.py" >/dev/null
if [ -f "$WORK/tools/apply_rev2_0_9_4_8.py" ]; then cp "$WORK/tools/apply_rev2_0_9_4_8.py" "$ROOT/tools/apply_rev2_0_9_4_8.py"; fi

echo "[5/7] VERIFY SYNTAX INSIDE CONTAINER"
docker exec "$CONTAINER" python3 -m py_compile "$TARGET_ROOT/main.py" "$TARGET_ROOT/routers/marketplaces.py"

echo "[6/7] RESTART API + HEALTH"
docker restart "$CONTAINER" >/dev/null
HEALTH=""
for i in $(seq 1 60); do
  sleep 1
  HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).read().decode())" 2>/dev/null || true)"
  echo "$HEALTH" | grep -q '0.9.4.8a' && break
done
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.9.4.8a'

echo "[7/7] VERIFY ROUTES REGISTERED BY FASTAPI"
docker exec "$CONTAINER" python3 - <<'PY'
from app.main import app
wanted={
 ('POST','/marketplaces/wb/packing/scan-product'),
 ('POST','/marketplaces/wb/packing/scan-chz'),
 ('GET','/marketplaces/wb/packing/progress'),
 ('POST','/marketplaces/wb/orders/{order_id}/label/queue'),
}
actual=set()
for r in app.routes:
    for m in getattr(r,'methods',set()) or set(): actual.add((m,getattr(r,'path','')))
missing=sorted(wanted-actual)
print('missing_routes=', missing)
if missing: raise SystemExit(2)
print('PASS: critical packing routes registered')
PY

trap - ERR
echo "OK: FulfillCore 0.9.4.8a ROUTE_RESTORE is running"
echo "FIXED: scan-product + scan-chz + progress + label queue route audit"
echo "UNCHANGED: WB matching logic, CHZ logic, reservations, stock write-off, Print Agent"
echo "Backup: $BACKUP"
