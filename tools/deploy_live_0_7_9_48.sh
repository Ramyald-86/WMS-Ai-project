#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/fulfillcore"
CONTAINER="${1:-fulfillcore-api-1}"

cd "$ROOT"

echo "[1/5] Copying hotfix files to host..."
test -f app/main.py
test -f app/static/app.js
test -f app/routers/receiving.py
test -f app/services/structured_receiving.py

echo "[2/5] Detecting live application path..."
TARGET_ROOT=""
for candidate in /app/app /opt/fulfillcore/app /srv/app; do
  if docker exec "$CONTAINER" sh -lc "test -f '$candidate/main.py'" >/dev/null 2>&1; then
    TARGET_ROOT="$candidate"
    break
  fi
done

if [ -z "$TARGET_ROOT" ]; then
  echo "ERROR: live app path not found inside $CONTAINER"
  exit 1
fi
echo "Live app path: $TARGET_ROOT"

echo "[3/5] Installing files directly into LIVE container..."
docker cp "$ROOT/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py"
docker cp "$ROOT/app/static/app.js" "$CONTAINER:$TARGET_ROOT/static/app.js"
docker cp "$ROOT/app/routers/receiving.py" "$CONTAINER:$TARGET_ROOT/routers/receiving.py"
docker cp "$ROOT/app/services/structured_receiving.py" "$CONTAINER:$TARGET_ROOT/services/structured_receiving.py"

echo "[4/5] Restarting API..."
docker restart "$CONTAINER" >/dev/null
sleep 5

echo "[5/5] Verifying the LIVE service..."
echo "Container file:"
docker exec "$CONTAINER" sh -lc "grep -o 'version=\"0.7.9.[0-9]*\"' '$TARGET_ROOT/main.py' | head -1"

echo "Frontend marker:"
docker exec "$CONTAINER" sh -lc "grep -c 'FC_BUILD_07948' '$TARGET_ROOT/static/app.js'"

echo "Old huge list marker:"
docker exec "$CONTAINER" sh -lc "grep -c 'Показать список WB FBS' '$TARGET_ROOT/static/app.js'"

echo "HTTP /health:"
HEALTH="$(curl -fsS --max-time 10 http://127.0.0.1:8000/health)"
echo "$HEALTH"

echo "$HEALTH" | grep -q '0.7.9.48' || {
  echo "ERROR: live HTTP health is not 0.7.9.48"
  exit 1
}

echo
echo "OK: LIVE FulfillCore is running 0.7.9.48"
