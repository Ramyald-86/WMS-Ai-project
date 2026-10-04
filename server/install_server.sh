#!/usr/bin/env bash
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Запустите: sudo bash server/install_server.sh"
  exit 1
fi

cd "$(dirname "$0")/.."

if ! command -v docker >/dev/null 2>&1; then
  apt-get update
  apt-get install -y ca-certificates curl openssl python3
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
else
  apt-get update >/dev/null 2>&1 || true
  apt-get install -y openssl python3 >/dev/null 2>&1 || true
fi

if ! docker compose version >/dev/null 2>&1; then
  apt-get install -y docker-compose-plugin
fi

read -rp "Домен FulfillCore (например wms.mysklad.ru): " DOMAIN
read -rp "Логин первого профиля [admin]: " ADMIN_USERNAME
ADMIN_USERNAME=${ADMIN_USERNAME:-admin}
read -rsp "Пароль FulfillCore: " ADMIN_PASSWORD; echo
read -rsp "Повторите пароль: " ADMIN_PASSWORD_2; echo
[ -n "$ADMIN_PASSWORD" ] || { echo "Пароль пустой"; exit 1; }
[ "$ADMIN_PASSWORD" = "$ADMIN_PASSWORD_2" ] || { echo "Пароли не совпадают"; exit 1; }

POSTGRES_PASSWORD=$(openssl rand -hex 24)
APP_SECRET_KEY=$(openssl rand -hex 48)
PRINT_AGENT_TOKEN=$(openssl rand -hex 32)
ADMIN_PASSWORD_HASH=$(ADMIN_PASSWORD="$ADMIN_PASSWORD" python3 - <<'PY'
import base64, hashlib, os
p=os.environ['ADMIN_PASSWORD'].encode()
salt=os.urandom(16); iterations=310000
b=lambda x: base64.urlsafe_b64encode(x).rstrip(b'=').decode()
d=hashlib.pbkdf2_hmac('sha256',p,salt,iterations)
print(f"pbkdf2_sha256${iterations}${b(salt)}${b(d)}")
PY
)
unset ADMIN_PASSWORD ADMIN_PASSWORD_2

cat > server/.env.server <<EOF
DOMAIN=$DOMAIN
POSTGRES_DB=fulfillcore
POSTGRES_USER=fulfillcore
POSTGRES_PASSWORD=$POSTGRES_PASSWORD
APP_SECRET_KEY=$APP_SECRET_KEY
ADMIN_USERNAME=$ADMIN_USERNAME
ADMIN_PASSWORD_HASH=$ADMIN_PASSWORD_HASH
SESSION_TTL_HOURS=12
PRINT_AGENT_TOKEN=$PRINT_AGENT_TOKEN
EOF
chmod 600 server/.env.server

docker compose --env-file server/.env.server -f docker-compose.server.yml up -d --build

mkdir -p backups
chmod +x server/backup.sh
CRON_LINE="17 3 * * * cd $(pwd) && bash server/backup.sh >> backups/backup.log 2>&1"
(crontab -l 2>/dev/null | grep -v 'server/backup.sh' || true; echo "$CRON_LINE") | crontab -

echo
echo "========================================"
echo "FulfillCore запущен"
echo "Адрес: https://$DOMAIN"
echo "Логин: $ADMIN_USERNAME"
echo "PRINT_AGENT_TOKEN (сохраните для компьютеров с принтером):"
echo "$PRINT_AGENT_TOKEN"
echo "========================================"
echo "HTTPS заработает после того, как DNS домена будет направлен на IP этого сервера."
