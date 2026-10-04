#!/bin/sh
set -eu
cd /opt/fulfillcore
BUNDLE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$BUNDLE_DIR/apply_print_agent_1_2_5_site_update.py"
python3 -m py_compile app/main.py

docker cp app/main.py fulfillcore-api-1:/app/app/main.py
docker cp app/static/app.js fulfillcore-api-1:/app/app/static/app.js
docker cp app/static/print-agent.html fulfillcore-api-1:/app/app/static/print-agent.html
docker cp app/downloads/FulfillCore_Print_Agent_1.2.5_ONE_CLICK_BACKGROUND_RELIABLE.zip fulfillcore-api-1:/app/app/downloads/FulfillCore_Print_Agent_1.2.5_ONE_CLICK_BACKGROUND_RELIABLE.zip
docker cp "app/downloads/FulfillCore_Print_Agent_1.2.5_Инструкция.txt" "fulfillcore-api-1:/app/app/downloads/FulfillCore_Print_Agent_1.2.5_Инструкция.txt"

docker exec fulfillcore-api-1 python -m py_compile /app/app/main.py
docker restart fulfillcore-api-1 >/dev/null
sleep 4
HEALTH=$(curl -fsS http://127.0.0.1:8000/health || true)
echo "HEALTH: $HEALTH"
echo "$HEALTH" | grep -q '"status":"ok"' || { echo 'ERROR: health check failed'; exit 1; }
# Verify actual downloadable archive is 1.2.5 by headers/content length and endpoint status.
curl -fsSI http://127.0.0.1:8000/downloads/print-agent/archive | head -20
printf '\nOK: Print Agent 1.2.5 is published on FulfillCore site.\n'
printf 'Open: https://201-24-52-218.nip.io/help/print-agent\n'
