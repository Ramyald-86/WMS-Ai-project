# FulfillCore 0.7.8.29 — скачивание и инструкция Print Agent на сайте

Точечный патч поверх версии `0.7.8.28`. Логика упаковки, ЧЗ, статусов заказов и печати не изменялась.

## Что добавлено

1. Защищённая страница `/help/print-agent` с краткой установкой Print Agent.
2. Прямое скачивание актуального архива `FulfillCore Print Agent 1.1.7` после входа в FulfillCore.
3. Прямое скачивание полной Word-инструкции.
4. Кнопка «Установка и скачивание Print Agent» в настройках печати.
5. На странице показаны версия, дата и SHA-256 архива.

## Установка обновления FulfillCore

### 1. Скачайте архив обновления

Скачайте файл:

`FulfillCore_0.7.8.29_Print_Agent_Download_Page.zip`

Обычно он попадёт в папку `Загрузки` Windows.

### 2. Передайте архив на сервер и подключитесь по SSH

Откройте PowerShell на компьютере и выполните:

```powershell
scp "$env:USERPROFILE\Downloads\FulfillCore_0.7.8.29_Print_Agent_Download_Page.zip" root@5.129.203.223:/root/
ssh root@5.129.203.223
```

### 3. Сделайте резервную копию изменяемых файлов

```bash
cd /opt/fulfillcore

mkdir -p backup_0.7.8.28_before_0.7.8.29/app/static

cp app/main.py backup_0.7.8.28_before_0.7.8.29/app/main.py
cp app/static/app.js backup_0.7.8.28_before_0.7.8.29/app/static/app.js
```

### 4. Распакуйте обновление

```bash
unzip -o /root/FulfillCore_0.7.8.29_Print_Agent_Download_Page.zip -d /opt/fulfillcore
```

### 5. Перезапустите API

```bash
docker compose \
  --env-file server/.env.server \
  -f docker-compose.server.yml \
  -f docker-compose.hotpatch.yml \
  restart api
```

### 6. Проверьте версию

```bash
sleep 10

curl -s https://5-129-203-223.nip.io/health
```

Ожидаемый ответ содержит:

```json
{"status":"ok","version":"0.7.8.29"}
```

## Проверка страницы

1. Войдите в FulfillCore.
2. Откройте «Настройки печати».
3. Нажмите «Установка и скачивание Print Agent».
4. Проверьте скачивание ZIP и Word-инструкции.

Прямая страница:

`https://5-129-203-223.nip.io/help/print-agent`

## Безопасность

- Страница и оба файла доступны только после входа в FulfillCore.
- `PRINT_AGENT_TOKEN` не встроен ни в ZIP, ни в Word-инструкцию, ни в HTML-страницу.
- В архиве опубликован исходный Print Agent 1.1.7 без изменения его рабочих файлов.
