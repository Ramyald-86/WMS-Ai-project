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
[ -f "$ROOT/files/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip" ] || { echo "STOP: Print Agent archive missing in $ROOT/files"; exit 1; }
[ -f "$ROOT/files/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE_INSTRUCTION_RU.txt" ] || { echo "STOP: Print Agent instruction missing (ASCII filename)"; exit 1; }
[ -f "$ROOT/files/print-agent.html" ] || { echo "STOP: print-agent.html missing"; exit 1; }

TS="$(date +%Y%m%d_%H%M%S)"
BACKUP="/tmp/fc_0_9_4_5_backup_$TS"
WORK="/tmp/fc_0_9_4_5_work_$TS"
CODE_FILES=(main.py routers/marketplaces.py static/app.js)
mkdir -p "$BACKUP/host/app/routers" "$BACKUP/host/app/static" "$BACKUP/container/app/routers" "$BACKUP/container/app/static" "$WORK/app/routers" "$WORK/app/static"

rollback(){
  set +e
  echo "=== ROLLBACK 0.9.4.5 ==="
  for f in "${CODE_FILES[@]}"; do
    [ -f "$BACKUP/host/app/$f" ] && cp "$BACKUP/host/app/$f" "$ROOT/app/$f"
    [ -f "$BACKUP/container/app/$f" ] && docker cp "$BACKUP/container/app/$f" "$CONTAINER:$TARGET_ROOT/$f" >/dev/null 2>&1
  done
  if [ -f "$BACKUP/host/print-agent.html" ]; then cp "$BACKUP/host/print-agent.html" "$ROOT/app/static/print-agent.html"; fi
  if [ -f "$BACKUP/container/print-agent.html" ]; then docker cp "$BACKUP/container/print-agent.html" "$CONTAINER:$TARGET_ROOT/static/print-agent.html" >/dev/null 2>&1; fi
  docker restart "$CONTAINER" >/dev/null 2>&1 || true
  echo "ROLLBACK COMPLETE. Backup: $BACKUP"
}
trap 'echo "ERROR: 0.9.4.5 deployment failed"; rollback' ERR

echo "[1/10] PRECHECK"
docker ps --format '{{.Names}} {{.Status}}' | grep -E "^${CONTAINER} "
python3 -m py_compile tools/apply_integration_stability_0_9_4_5.py tools/smoke_check_0_9_4_5.py

echo "[2/10] BACKUP actual HOST + LIVE CONTAINER"
[ -f "$ROOT/.env" ] && cp "$ROOT/.env" "$BACKUP/host/.env.before_0_9_4_5" || true
for f in "${CODE_FILES[@]}"; do
  mkdir -p "$(dirname "$BACKUP/host/app/$f")" "$(dirname "$BACKUP/container/app/$f")" "$(dirname "$WORK/app/$f")"
  cp "$ROOT/app/$f" "$BACKUP/host/app/$f"
  docker cp "$CONTAINER:$TARGET_ROOT/$f" "$BACKUP/container/app/$f" >/dev/null
  cp "$BACKUP/container/app/$f" "$WORK/app/$f"
done
[ -f "$ROOT/app/static/print-agent.html" ] && cp "$ROOT/app/static/print-agent.html" "$BACKUP/host/print-agent.html" || true
docker cp "$CONTAINER:$TARGET_ROOT/static/print-agent.html" "$BACKUP/container/print-agent.html" >/dev/null 2>&1 || true

# Persist the currently working live Print Agent master token into .env without printing it.
# This prevents a later docker-compose recreate from silently changing/losing auth and
# sending every packing PC back into a 401 retry storm.
LIVE_PRINT_TOKEN="$(docker exec "$CONTAINER" sh -lc 'printf %s "$PRINT_AGENT_TOKEN"' 2>/dev/null || true)"
if [ -n "$LIVE_PRINT_TOKEN" ]; then
  export LIVE_PRINT_TOKEN
  python3 - <<'PYTOKEN'
from pathlib import Path
import os
p=Path('/opt/fulfillcore/.env')
token=os.environ.get('LIVE_PRINT_TOKEN','')
if not token:
    raise SystemExit(0)
lines=p.read_text(encoding='utf-8').splitlines() if p.exists() else []
out=[]; found=False
for line in lines:
    if line.startswith('PRINT_AGENT_TOKEN='):
        out.append('PRINT_AGENT_TOKEN='+token); found=True
    else:
        out.append(line)
if not found:
    out.append('PRINT_AGENT_TOKEN='+token)
p.write_text('\n'.join(out)+'\n',encoding='utf-8')
print('OK: live PRINT_AGENT_TOKEN persisted to .env (value hidden)')
PYTOKEN
  unset LIVE_PRINT_TOKEN
else
  echo "WARN: live PRINT_AGENT_TOKEN is empty; deployment will not invent a new token"
fi

echo "[3/10] VERIFY current 0.9.4.4 foundation"
grep -q 'version="0.9.4.4"' "$WORK/app/main.py"
grep -q '0.9.4.4 PACKING TRANSACTION FIX' "$WORK/app/routers/marketplaces.py"

echo "[4/10] PATCH TEMP COPY ONLY"
python3 tools/apply_integration_stability_0_9_4_5.py --root "$WORK"
python3 -m py_compile "$WORK/app/main.py" "$WORK/app/routers/marketplaces.py"
if command -v node >/dev/null 2>&1; then node --check "$WORK/app/static/app.js"; fi
python3 tools/smoke_check_0_9_4_5.py --root "$WORK"

echo "[5/10] PUBLISH tested code + SAFE Print Agent download"
for f in "${CODE_FILES[@]}"; do
  cp "$WORK/app/$f" "$ROOT/app/$f"
  docker cp "$WORK/app/$f" "$CONTAINER:$TARGET_ROOT/$f" >/dev/null
done
mkdir -p "$ROOT/app/downloads" "$ROOT/app/static"
cp "$ROOT/files/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip" "$ROOT/app/downloads/"
cp "$ROOT/files/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE_INSTRUCTION_RU.txt" "$ROOT/app/downloads/"
cp "$ROOT/files/print-agent.html" "$ROOT/app/static/print-agent.html"
docker exec "$CONTAINER" sh -lc "mkdir -p '$TARGET_ROOT/downloads' '$TARGET_ROOT/static'"
docker cp "$ROOT/files/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip" "$CONTAINER:$TARGET_ROOT/downloads/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip" >/dev/null
docker cp "$ROOT/files/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE_INSTRUCTION_RU.txt" "$CONTAINER:$TARGET_ROOT/downloads/FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE_INSTRUCTION_RU.txt" >/dev/null
docker cp "$ROOT/files/print-agent.html" "$CONTAINER:$TARGET_ROOT/static/print-agent.html" >/dev/null

echo "[6/10] VERIFY HOST == CONTAINER code SHA256"
for f in "${CODE_FILES[@]}"; do
  H="$(sha256sum "$ROOT/app/$f" | awk '{print $1}')"
  C="$(docker exec "$CONTAINER" sha256sum "$TARGET_ROOT/$f" | awk '{print $1}')"
  [ "$H" = "$C" ] || { echo "STOP SHA mismatch $f"; exit 1; }
  echo "OK $f $H"
done

echo "[7/10] SYNTAX inside container BEFORE restart"
docker exec "$CONTAINER" python3 -m py_compile "$TARGET_ROOT/main.py" "$TARGET_ROOT/routers/marketplaces.py"
if docker exec "$CONTAINER" sh -lc 'command -v node >/dev/null 2>&1'; then docker exec "$CONTAINER" node --check "$TARGET_ROOT/static/app.js"; fi

echo "[8/10] RESTART API and wait for 0.9.4.5"
docker restart "$CONTAINER" >/dev/null
HEALTH=""
for i in $(seq 1 90); do
  sleep 1
  HEALTH="$(docker exec "$CONTAINER" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).read().decode())" 2>/dev/null || true)"
  echo "$HEALTH" | grep -q '0.9.4.5' && break
done
echo "HTTP /health: $HEALTH"
echo "$HEALTH" | grep -q '0.9.4.5'

echo "[9/10] DOWNLOAD + DB transaction sanity"
curl -fsS -o /tmp/fc_agent_test.zip http://127.0.0.1:8000/downloads/print-agent/archive
[ -s /tmp/fc_agent_test.zip ]
ls -lh /tmp/fc_agent_test.zip
docker compose exec -T db sh -lc 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -P pager=off -c "SELECT count(*) AS idle_tx_over_30s FROM pg_stat_activity WHERE datname=current_database() AND state=\x27idle in transaction\x27 AND xact_start < now()-interval \x2730 seconds\x27;"' || true

echo "[10/10] SHORT LOG CHECK"
docker logs --tail 80 "$CONTAINER" 2>&1 | tail -80
trap - ERR
echo "OK: FulfillCore 0.9.4.5 WB_CHZ_PRINT_STABILITY is running"
echo "Print Agent download: /downloads/print-agent/archive"
echo "Backup: $BACKUP"
