#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/fulfillcore"
CONTAINER="${1:-fulfillcore-api-1}"
cd "$ROOT"

TARGET_ROOT=""
for candidate in /app/app /opt/fulfillcore/app /srv/app; do
  if docker exec "$CONTAINER" sh -lc "test -f '$candidate/main.py' && test -f '$candidate/routers/marketplaces.py' && test -f '$candidate/services/stock_reservations.py' && test -f '$candidate/static/app.js'" >/dev/null 2>&1; then
    TARGET_ROOT="$candidate"
    break
  fi
done
[ -n "$TARGET_ROOT" ] || { echo "STOP: live app path not found in $CONTAINER"; exit 1; }

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="/tmp/fc_0_9_4_8b_fifo_backup_$TS"
WORK="/tmp/fc_0_9_4_8b_fifo_work_$TS"
mkdir -p \
  "$BACKUP/host/app/routers" "$BACKUP/host/app/services" "$BACKUP/host/app/static" \
  "$BACKUP/container/app/routers" "$BACKUP/container/app/services" "$BACKUP/container/app/static" \
  "$WORK/app/routers" "$WORK/app/services" "$WORK/app/static" "$WORK/tools"

rollback(){
  set +e
  echo "=== ROLLBACK 0.9.4.8b ==="
  for f in main.py routers/marketplaces.py services/stock_reservations.py static/app.js; do
    [ -f "$BACKUP/host/app/$f" ] && cp "$BACKUP/host/app/$f" "$ROOT/app/$f"
    [ -f "$BACKUP/container/app/$f" ] && docker cp "$BACKUP/container/app/$f" "$CONTAINER:$TARGET_ROOT/$f" >/dev/null 2>&1 || true
  done
  docker restart "$CONTAINER" >/dev/null 2>&1 || true
  echo "ROLLBACK COMPLETE: $BACKUP"
}
trap 'echo "ERROR: 0.9.4.8b deployment failed"; rollback' ERR

echo "[1/10] PRECHECK: do not touch DB/Caddy/Print Agent"
docker ps --format '{{.Names}} {{.Status}}' | grep -E "^${CONTAINER} "
python3 -m py_compile tools/apply_fifo_packing_emergency_0_9_4_8b.py tools/smoke_check_0_9_4_8b.py

echo "[2/10] BACKUP HOST + ACTUAL RUNNING CONTAINER"
for f in main.py routers/marketplaces.py services/stock_reservations.py static/app.js; do
  cp "$ROOT/app/$f" "$BACKUP/host/app/$f"
  docker cp "$CONTAINER:$TARGET_ROOT/$f" "$BACKUP/container/app/$f" >/dev/null
  H="$(sha256sum "$BACKUP/host/app/$f" | awk '{print $1}')"
  C="$(sha256sum "$BACKUP/container/app/$f" | awk '{print $1}')"
  if [ "$H" = "$C" ]; then
    echo "MATCH before: $f $H"
  else
    echo "MISMATCH before: $f"
    echo "  host=$H"
    echo "  live=$C"
    echo "  source of truth for this hotfix: RUNNING CONTAINER"
  fi
done

# Work only from the actual running code. This prevents an old host copy from replacing
# a newer live fix. app.js is not modified; if it differed, align only the host copy to
# the live frontend so HOST and CONTAINER are consistent after deployment.
cp "$BACKUP/container/app/main.py" "$WORK/app/main.py"
cp "$BACKUP/container/app/routers/marketplaces.py" "$WORK/app/routers/marketplaces.py"
cp "$BACKUP/container/app/services/stock_reservations.py" "$WORK/app/services/stock_reservations.py"
cp "$BACKUP/container/app/static/app.js" "$WORK/app/static/app.js"
cp "$BACKUP/container/app/static/app.js" "$ROOT/app/static/app.js"

echo "[3/10] VERIFY CURRENT APPROVED FOUNDATION"
grep -q '0.9.4.8 REV2 SEQUENTIAL CONFIRMED PRINT' "$WORK/app/routers/marketplaces.py"
grep -q '0.9.4.8a ROUTE RESTORE HOTFIX' "$WORK/app/routers/marketplaces.py"
grep -Eq 'version="0\.9\.4\.8(a|b)"' "$WORK/app/main.py"
grep -q 'def consume_order_reservation' "$WORK/app/services/stock_reservations.py"

echo "[4/10] PATCH TEMP COPY ONLY"
python3 tools/apply_fifo_packing_emergency_0_9_4_8b.py --root "$WORK"
python3 -m py_compile "$WORK/app/main.py" "$WORK/app/routers/marketplaces.py" "$WORK/app/services/stock_reservations.py"
cp tools/smoke_check_0_9_4_8b.py "$WORK/tools/smoke_check_0_9_4_8b.py"
python3 "$WORK/tools/smoke_check_0_9_4_8b.py"

echo "[5/10] SHOW EXACT PACKING RULES BEFORE PUBLISH"
python3 - "$WORK/app/routers/marketplaces.py" "$WORK/app/services/stock_reservations.py" <<'PY'
import ast,sys
mp=open(sys.argv[1],encoding='utf-8').read(); rs=open(sys.argv[2],encoding='utf-8').read()
def fsrc(src,name):
    t=ast.parse(src); lines=src.splitlines()
    for n in ast.walk(t):
        if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name:
            return '\n'.join(lines[n.lineno-1:n.end_lineno])
    raise SystemExit('missing '+name)
scan=fsrc(mp,'wb_scan_product')
print('FIFO NEW+PICKING:', 'MarketplaceOrder.local_status.in_(["NEW", "PICKING"])' in scan)
print('Reservation is best-effort:', 'PACKING_FIFO_WITHOUT_RESERVATION' in scan)
print('No old hard reserve error:', 'Резерв по заказу не найден. Обратитесь к старшему смены.' not in scan)
print('Print ACK gate:', 'WB_PACKED_AFTER_PRINT_ACK", require=False' in fsrc(mp,'_finalize_strict_pack_from_job'))
print('Packing-time reservation helper:', 'PACKING_FIFO_AUTO_RESERVE_0_9_4_8b' in fsrc(rs,'ensure_packing_reservation_for_scan'))
PY

echo "[6/10] PUBLISH ONLY 3 PYTHON FILES; FRONTEND LEFT BYTE-FOR-BYTE AS LIVE"
cp "$WORK/app/main.py" "$ROOT/app/main.py"
cp "$WORK/app/routers/marketplaces.py" "$ROOT/app/routers/marketplaces.py"
cp "$WORK/app/services/stock_reservations.py" "$ROOT/app/services/stock_reservations.py"
docker cp "$WORK/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py" >/dev/null
docker cp "$WORK/app/routers/marketplaces.py" "$CONTAINER:$TARGET_ROOT/routers/marketplaces.py" >/dev/null
docker cp "$WORK/app/services/stock_reservations.py" "$CONTAINER:$TARGET_ROOT/services/stock_reservations.py" >/dev/null

echo "[7/10] VERIFY HOST == CONTAINER + SYNTAX"
for f in main.py routers/marketplaces.py services/stock_reservations.py static/app.js; do
  H="$(sha256sum "$ROOT/app/$f" | awk '{print $1}')"
  C="$(docker exec "$CONTAINER" sha256sum "$TARGET_ROOT/$f" | awk '{print $1}')"
  [ "$H" = "$C" ] || { echo "STOP SHA mismatch after publish: $f"; exit 1; }
  echo "OK SHA $f $H"
done
docker exec "$CONTAINER" python3 -m py_compile "$TARGET_ROOT/main.py" "$TARGET_ROOT/routers/marketplaces.py" "$TARGET_ROOT/services/stock_reservations.py"

echo "[8/10] RESTART API ONLY + HEALTH"
docker restart "$CONTAINER" >/dev/null
HEALTH=""
for i in $(seq 1 60); do
  sleep 1
  HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).read().decode())" 2>/dev/null || true)"
  echo "$HEALTH" | grep -q '0.9.4.8b' && break
done
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.9.4.8b'

echo "[9/10] VERIFY CRITICAL ROUTES REGISTERED"
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
    for m in getattr(r,'methods',set()) or set():
        actual.add((m,getattr(r,'path','')))
missing=sorted(wanted-actual)
print('missing_routes=', missing)
if missing: raise SystemExit(2)
print('PASS: critical packing routes registered')
PY

echo "[10/10] POST-START READ-ONLY CHECK"
docker logs --tail 80 "$CONTAINER" 2>&1 | grep -E 'Traceback|DeadlockDetected|IdleInTransaction|FC_PACK|ERROR' | tail -40 || true

trap - ERR
echo "OK: FulfillCore 0.9.4.8b FIFO_PACKING_EMERGENCY is running"
echo "FIFO: oldest eligible NEW/PICKING order for scanned SKU wins"
echo "RESERVE: follows selected FIFO order; missing reserve no longer blocks packing"
echo "CHZ/PRINT: unchanged strict sequence; next scan only after physical print ACK"
echo "PRINT AGENT: unchanged (approved 1.1.7 remains in use)"
echo "RESTARTED: API only"
echo "Backup: $BACKUP"
