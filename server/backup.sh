#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
source server/.env.server
set +a
mkdir -p backups
stamp=$(date +%Y%m%d_%H%M%S)
docker compose --env-file server/.env.server -f docker-compose.server.yml exec -T db \
  pg_dump -U "${POSTGRES_USER:-fulfillcore}" -d "${POSTGRES_DB:-fulfillcore}" -Fc > "backups/fulfillcore_${stamp}.dump"
find backups -type f -name 'fulfillcore_*.dump' -mtime +14 -delete
echo "Backup: backups/fulfillcore_${stamp}.dump"
