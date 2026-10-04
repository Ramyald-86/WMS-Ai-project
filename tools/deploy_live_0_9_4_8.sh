#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/fulfillcore"
CONTAINER="${1:-fulfillcore-api-1}"
cd "$ROOT"

TARGET_ROOT=""
for candidate in /app/app /opt/fulfillcore/app /srv/app; do
  if docker exec "$CONTAINER" sh -lc "test -f '$candidate/main.py' && test -f '$candidate/routers/marketplaces.py' && test -f '$candidate/static/app.js' && test -f '$candidate/routers/printing.py'" >/dev/null 2>&1; then
    TARGET_ROOT="$candidate"
    break
  fi
done
[ -n "$TARGET_ROOT" ] || { echo "STOP: live app path not found inside $CONTAINER"; exit 1; }

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="/tmp/fc_0_9_4_8_backup_$TS"
WORK="/tmp/fc_0_9_4_8_work_$TS"
REPORT="$BACKUP/diagnostics_before.txt"
FILES=(
  main.py
  routers/marketplaces.py
  static/app.js
  core/config.py
  core/database.py
  services/db_pool_tuning.py
  routers/printing.py
  services/runtime_reliability.py
)

for base in "$BACKUP/host/app" "$BACKUP/container/app" "$WORK/app"; do
  mkdir -p "$base/routers" "$base/static" "$base/core" "$base/services"
done
mkdir -p "$BACKUP/diffs"

rollback(){
  set +e
  echo "=== ROLLBACK 0.9.4.8 ==="
  for f in "${FILES[@]}"; do
    if [ -f "$BACKUP/host/app/$f" ]; then
      mkdir -p "$(dirname "$ROOT/app/$f")"
      cp "$BACKUP/host/app/$f" "$ROOT/app/$f"
    fi
    if [ -f "$BACKUP/container/app/$f" ]; then
      docker cp "$BACKUP/container/app/$f" "$CONTAINER:$TARGET_ROOT/$f" >/dev/null 2>&1 || true
    fi
  done
  docker restart "$CONTAINER" >/dev/null 2>&1 || true
  echo "ROLLBACK COMPLETE. Backup: $BACKUP"
}
trap 'echo "ERROR: 0.9.4.8 deployment failed"; rollback' ERR

echo "[1/11] PRECHECK + NON-DESTRUCTIVE INCIDENT REPORT"
docker ps --format '{{.Names}} {{.Status}}' | grep -E "^${CONTAINER} "
{
  echo "FulfillCore 0.9.4.8 pre-deploy diagnostics $TS"
  echo "=== API VERSION ==="
  docker exec "$CONTAINER" grep -m1 -E 'version="0\.9\.4\.[0-9]+"' "$TARGET_ROOT/main.py" || true
  echo
  echo "=== DB POOL / RELIABILITY LOG ==="
  docker logs "$CONTAINER" 2>&1 | grep -E 'DB pool tuning|startup db_pool|reliability schema warning|PendingRollback|QueuePool|IdleInTransactionSessionTimeout' | tail -120 || true
  echo
  echo "=== PG ACTIVITY BY STATE ==="
  docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "SELECT state, count(*), max(now()-xact_start) AS longest FROM pg_stat_activity WHERE datname=current_database() GROUP BY state ORDER BY state;"' || true
  echo
  echo "=== MARKETPLACE EVENT TABLE ==="
  docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "\\d marketplace_order_events"' || true
  echo
  echo "=== PACKED WITHOUT WRITE-OFF EVENT ==="
  docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "SELECT count(*) AS orders_and_units, min(o.packed_at) AS first_packed_at, max(o.packed_at) AS last_packed_at FROM marketplace_orders o WHERE upper(coalesce(o.local_status::text, '\''\''))='\''PACKED'\'' AND upper(coalesce(o.provider::text, '\''\''))='\''WB'\'' AND NOT EXISTS (SELECT 1 FROM marketplace_order_events e WHERE e.marketplace_order_id=o.id AND e.event_type='\''PICKING_RESERVATION_CONSUMED'\'');"' || true
  echo
  echo "=== PRINT JOB STATUS LAST 7 DAYS ==="
  docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "SELECT status, count(*) FROM print_jobs WHERE document_type='\''WB_ORDER_LABEL'\'' AND created_at > now()-interval '\''7 days'\'' GROUP BY status ORDER BY status;"' || true
  echo
  echo "=== 0.9.4.7 CANCELLED LABELS ==="
  docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "SELECT count(*) AS cancelled_0947, min(created_at) AS first_at, max(created_at) AS last_at FROM print_jobs WHERE status='\''CANCELLED'\'' AND error_message LIKE '\''Отменено 0.9.4.7%'\'';"' || true
} | tee "$REPORT"

echo "[2/11] BACKUP HOST + ACTUAL LIVE CONTAINER"
for f in "${FILES[@]}"; do
  test -f "$ROOT/app/$f" || { echo "STOP: host file missing $ROOT/app/$f"; exit 1; }
  docker exec "$CONTAINER" test -f "$TARGET_ROOT/$f" || { echo "STOP: container file missing $TARGET_ROOT/$f"; exit 1; }
  cp "$ROOT/app/$f" "$BACKUP/host/app/$f"
  docker cp "$CONTAINER:$TARGET_ROOT/$f" "$BACKUP/container/app/$f" >/dev/null
  cp "$BACKUP/container/app/$f" "$WORK/app/$f"
done

CURRENT_VERSION="$(grep -oE 'version="0\.9\.4\.[0-9]+"' "$WORK/app/main.py" | head -1 || true)"
echo "Live foundation: ${CURRENT_VERSION:-unknown}"
case "$CURRENT_VERSION" in
  'version="0.9.4.7"'|'version="0.9.4.8"') ;;
  *) echo "STOP: expected live 0.9.4.7 (or already-installed 0.9.4.8), got ${CURRENT_VERSION:-unknown}"; exit 1;;
esac

python3 -m py_compile \
  tools/apply_strict_chz_print_gate_0_9_4_8.py \
  tools/apply_rev2_0_9_4_8.py \
  tools/apply_platform_stability_0_9_4_8.py \
  tools/smoke_check_0_9_4_8.py

echo "[3/11] APPLY STRICT SEQUENTIAL CHZ -> LABEL -> PRINT ACK GATE TO TEMP COPY"
python3 tools/apply_strict_chz_print_gate_0_9_4_8.py --root "$WORK"
python3 tools/apply_rev2_0_9_4_8.py --root "$WORK"
python3 tools/apply_platform_stability_0_9_4_8.py --root "$WORK"

echo "[4/11] SYNTAX + ACCEPTANCE CHECKS BEFORE PUBLISH"
python3 -m py_compile \
  "$WORK/app/main.py" \
  "$WORK/app/routers/marketplaces.py" \
  "$WORK/app/core/config.py" \
  "$WORK/app/core/database.py" \
  "$WORK/app/services/db_pool_tuning.py" \
  "$WORK/app/routers/printing.py" \
  "$WORK/app/services/runtime_reliability.py"
if command -v node >/dev/null 2>&1; then node --check "$WORK/app/static/app.js"; fi
python3 tools/smoke_check_0_9_4_8.py --root "$WORK"

echo "[5/11] SAVE ACTUAL LIVE DIFFS"
for f in "${FILES[@]}"; do
  safe="${f//\//__}"
  diff -u "$BACKUP/container/app/$f" "$WORK/app/$f" > "$BACKUP/diffs/$safe.diff" || true
done

echo "[6/11] ADDITIVE PRINT-QUEUE INDEX"
docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "CREATE INDEX IF NOT EXISTS ix_print_jobs_station_status_created_at ON print_jobs (station_code, status, created_at);"'

# Keep the database-level safety net from 0.9.4.7. It affects new sessions only.
docker compose exec -T db sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "ALTER DATABASE \"$POSTGRES_DB\" SET idle_in_transaction_session_timeout = '\''45s'\'';"' >/dev/null

echo "[7/11] PUBLISH ONLY THE REVIEWED FILES"
for f in "${FILES[@]}"; do
  cp "$WORK/app/$f" "$ROOT/app/$f"
  docker cp "$WORK/app/$f" "$CONTAINER:$TARGET_ROOT/$f" >/dev/null
done

echo "[8/11] VERIFY SHA + PYTHON SYNTAX INSIDE CONTAINER"
for f in "${FILES[@]}"; do
  H="$(sha256sum "$ROOT/app/$f" | awk '{print $1}')"
  C="$(docker exec "$CONTAINER" sha256sum "$TARGET_ROOT/$f" | awk '{print $1}')"
  [ "$H" = "$C" ] || { echo "STOP SHA mismatch $f"; exit 1; }
  echo "OK $f $H"
done
docker exec "$CONTAINER" python3 -m py_compile \
  "$TARGET_ROOT/main.py" \
  "$TARGET_ROOT/routers/marketplaces.py" \
  "$TARGET_ROOT/core/config.py" \
  "$TARGET_ROOT/core/database.py" \
  "$TARGET_ROOT/services/db_pool_tuning.py" \
  "$TARGET_ROOT/routers/printing.py" \
  "$TARGET_ROOT/services/runtime_reliability.py"

echo "[9/11] RESTART API ONLY + HEALTH"
docker restart "$CONTAINER" >/dev/null
HEALTH=""
for i in $(seq 1 90); do
  sleep 1
  HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).read().decode())" 2>/dev/null || true)"
  echo "$HEALTH" | grep -q '0.9.4.8' && break
done
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.9.4.8'

echo "[10/11] VERIFY REAL POOL CONFIG + TRANSACTIONS"
docker exec "$CONTAINER" python3 - <<'PY'
from app.core.config import settings
from app.core.database import engine
pool=engine.pool
print({
    "configured_pool_size": settings.db_pool_size,
    "configured_max_overflow": settings.db_max_overflow,
    "configured_timeout": settings.db_pool_timeout,
    "configured_recycle": settings.db_pool_recycle,
    "runtime_size": pool.size() if hasattr(pool, "size") else None,
    "runtime_status": pool.status() if hasattr(pool, "status") else None,
})
PY
sleep 3
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "SELECT count(*) AS idle_in_tx_over_20s FROM pg_stat_activity WHERE datname=current_database() AND state='\''idle in transaction'\'' AND xact_start < now()-interval '\''20 seconds'\'';"'

echo "[11/11] RECENT LOG + PRINT AGENT AUTH SUMMARY"
# Refresh only the downloadable server copy. Installed Windows agents are NOT touched.
if [ -f "$ROOT/assets/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip" ]; then
  mkdir -p "$ROOT/app/downloads"
  cp "$ROOT/assets/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip" "$ROOT/app/downloads/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip"
  cp "$ROOT/assets/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE_INSTRUCTION_RU.txt" "$ROOT/app/downloads/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE_INSTRUCTION_RU.txt"
  docker cp "$ROOT/app/downloads/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip" "$CONTAINER:$TARGET_ROOT/downloads/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip" >/dev/null
  docker cp "$ROOT/app/downloads/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE_INSTRUCTION_RU.txt" "$CONTAINER:$TARGET_ROOT/downloads/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE_INSTRUCTION_RU.txt" >/dev/null
  echo "Site download refreshed: Print Agent 1.1.7 SAFE SIMPLE"
fi
docker logs --tail 120 "$CONTAINER" 2>&1 | tail -120
BAD_401="$(docker logs --since=2m "$CONTAINER" 2>&1 | grep -E '/printing/agent/.*(PACK-0|PACK-10).*401 Unauthorized' | wc -l | tr -d ' ' || true)"
echo "Old/invalid Print Agent 401 requests in last 2m: ${BAD_401:-0}"

trap - ERR
echo "OK: FulfillCore 0.9.4.8 REV2 SEQUENTIAL_CONFIRMED_PRINT is running"
echo "FLOW: product -> CHZ -> WB confirmation -> WB label -> Print Agent ACK -> stock write-off -> PACKED -> next scan"
echo "Print Agent protocol/version unchanged: 1.1.7 SAFE SIMPLE"
echo "Pre-deploy report: $REPORT"
echo "Actual live diffs: $BACKUP/diffs"
echo "Rollback backup: $BACKUP"
