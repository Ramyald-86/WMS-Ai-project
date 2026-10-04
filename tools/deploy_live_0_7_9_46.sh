#!/usr/bin/env bash
set -euo pipefail

ROOT="/opt/fulfillcore"
CONTAINER="${1:-fulfillcore-api-1}"

cd "$ROOT"

echo "[1/6] Rebuilding API container..."
docker compose \
  --env-file server/.env.server \
  -f docker-compose.server.yml \
  -f docker-compose.hotpatch.yml \
  up -d --build api

echo "[2/6] Waiting for container..."
for i in $(seq 1 30); do
  if docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null | grep -q true; then
    break
  fi
  sleep 1
done

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "ERROR: container $CONTAINER not found"
  exit 1
fi

echo "[3/6] Detecting application path inside live container..."
TARGET_ROOT=""
for candidate in /app/app /opt/fulfillcore/app /srv/app; do
  if docker exec "$CONTAINER" sh -lc "test -f '$candidate/main.py'" >/dev/null 2>&1; then
    TARGET_ROOT="$candidate"
    break
  fi
done
if [ -z "$TARGET_ROOT" ]; then
  echo "ERROR: could not find app/main.py inside $CONTAINER"
  echo "Run: docker exec $CONTAINER sh -lc 'pwd; find / -maxdepth 4 -path \"*/app/main.py\" 2>/dev/null | head'"
  exit 1
fi
echo "Live app path: $TARGET_ROOT"

echo "[4/6] Copying hotfix files into the LIVE container..."
docker cp "$ROOT/app/main.py" "$CONTAINER:$TARGET_ROOT/main.py"
docker cp "$ROOT/app/static/app.js" "$CONTAINER:$TARGET_ROOT/static/app.js"
docker cp "$ROOT/app/routers/receiving.py" "$CONTAINER:$TARGET_ROOT/routers/receiving.py"
docker cp "$ROOT/app/services/structured_receiving.py" "$CONTAINER:$TARGET_ROOT/services/structured_receiving.py"

echo "[5/6] Restarting live API..."
docker restart "$CONTAINER" >/dev/null
sleep 4

echo "[6/6] Verifying LIVE container, not host files..."
LIVE_VERSION="$(docker exec "$CONTAINER" sh -lc "grep -o 'version=\"0.7.9.[0-9]*\"' '$TARGET_ROOT/main.py' | head -1" || true)"
LIVE_MARKER="$(docker exec "$CONTAINER" sh -lc "grep -c 'FC_BUILD_07946' '$TARGET_ROOT/static/app.js'" || true)"
OLD_LIST="$(docker exec "$CONTAINER" sh -lc "grep -c 'Показать список WB FBS' '$TARGET_ROOT/static/app.js'" || true)"
BOX_FIX="$(docker exec "$CONTAINER" sh -lc "grep -c 'FCBOX-' '$TARGET_ROOT/routers/receiving.py'" || true)"

echo "Live version source: $LIVE_VERSION"
echo "JS build marker count: $LIVE_MARKER"
echo "Old huge-list UI marker count: $OLD_LIST"
echo "Any-box-format fix marker count: $BOX_FIX"

if [ "$LIVE_VERSION" != 'version="0.7.9.46"' ]; then
  echo "ERROR: live container version is not 0.7.9.46"
  exit 1
fi
if [ "${LIVE_MARKER:-0}" -lt 1 ]; then
  echo "ERROR: live app.js is not the 0.7.9.46 build"
  exit 1
fi
if [ "${OLD_LIST:-0}" -ne 0 ]; then
  echo "ERROR: old structured WB FBS list UI is still present in live app.js"
  exit 1
fi
if [ "${BOX_FIX:-0}" -lt 1 ]; then
  echo "ERROR: arbitrary-box-format fix is missing in live receiving.py"
  exit 1
fi

echo
echo "OK: LIVE FulfillCore container is actually running 0.7.9.46"
echo "Now open the site and press Ctrl+F5."
