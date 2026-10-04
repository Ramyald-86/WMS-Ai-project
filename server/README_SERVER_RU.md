# FulfillCore 0.6 — постоянный сервер

Эта конфигурация рассчитана на один постоянно работающий Linux VPS. После установки сотрудники открывают обычный адрес вида `https://wms.company.ru`, видят страницу входа и работают через браузер. Docker на рабочих компьютерах не нужен.

## Рекомендуемая конфигурация для текущего MVP
- Ubuntu 24.04 LTS x64.
- 2 vCPU.
- 4 ГБ RAM.
- 50–60+ ГБ SSD/NVMe.
- Публичный IPv4.
- Домен или поддомен, направленный A-записью на IP VPS.

## Что работает на сервере
- FulfillCore / FastAPI.
- PostgreSQL 16.
- Caddy с автоматическим HTTPS.
- Автоперезапуск контейнеров после перезагрузки.
- Health checks.
- Ежедневный локальный backup PostgreSQL в 03:17, хранение 14 дней.
- Один профиль по логину/паролю.

Важно: один VPS — это постоянный 24/7 сервер, но не полноценная multi-node отказоустойчивость. Поломка всего VPS может дать простой. Для следующего уровня добавляются внешний backup, managed/replicated PostgreSQL и второй узел.

## Быстрая установка

1. Арендуйте VPS и установите Ubuntu 24.04.
2. Создайте DNS A-запись, например `wms.company.ru → ПУБЛИЧНЫЙ_IP_VPS`.
3. Скопируйте папку FulfillCore на сервер, например в `/opt/fulfillcore`.
4. В папке проекта выполните:

```bash
sudo bash server/install_server.sh
```

5. Скрипт попросит:
   - домен;
   - логин первого профиля;
   - пароль.

Секрет PostgreSQL, ключ приложения и токен Print Agent будут сгенерированы автоматически.

6. Откройте `https://ваш-домен`.

## Смена пароля

Сгенерируйте новый hash:

```bash
python3 server/create_password_hash.py
```

Замените `ADMIN_PASSWORD_HASH` в `server/.env.server`, затем:

```bash
docker compose --env-file server/.env.server -f docker-compose.server.yml up -d
```

## Print Agent

В `server/.env.server` есть `PRINT_AGENT_TOKEN`. На упаковочном компьютере укажите его в `print_agent/config.json` в поле `agent_token`. Пароль пользователя FulfillCore агенту не нужен.

## Backup

`server/install_server.sh` добавляет ежедневный локальный backup. Это защищает от большинства ошибок в данных, но не от потери всего VPS. До начала серьёзной эксплуатации настройте копирование backup-файлов во внешнее S3/NAS/другое хранилище.
