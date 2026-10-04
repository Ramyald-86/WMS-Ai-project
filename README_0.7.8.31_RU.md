# FulfillCore 0.7.8.31 — установка обновления

## Что исправлено

- выбор режимов складов WB сохраняется локально даже при временной ошибке API Wildberries;
- упакованный заказ больше не может автоматически вернуться в статус «На сборке»;
- склад FulfillCore переключается безопасно, а остатки, резервы, очередь приёмки, кеш заказов и фильтры разделены по складам;
- столбцы заказов можно менять местами перетаскиванием или стрелками;
- под каждым столбцом добавлен фильтр, по заголовку — сортировка;
- вместо отдельной даты и возраста показывается возраст заказа; активные заказы старше 24 часов подсвечиваются красным, от 12 часов — жёлтым;
- установка, скачивание и защищённое копирование токена Print Agent перенесены в «Настройки печати»;
- инструкция Print Agent больше не требует SSH и пароля сервера.

## 1. Скачайте архив

Скачайте файл:

`FulfillCore_0.7.8.31_Warehouse_Status_Columns_Age_PrintAgent_FIX.zip`

Он должен оказаться в папке `Загрузки` Windows.

## 2. Передайте архив и подключитесь к серверу

Откройте PowerShell на компьютере и выполните:

```powershell
scp "$env:USERPROFILE\Downloads\FulfillCore_0.7.8.31_Warehouse_Status_Columns_Age_PrintAgent_FIX.zip" root@5.129.203.223:/root/
ssh root@5.129.203.223
```

## 3. Сделайте резервную копию

После подключения к серверу выполните:

```bash
cd /opt/fulfillcore

mkdir -p backup_0.7.8.30_before_0.7.8.31/app/routers
mkdir -p backup_0.7.8.30_before_0.7.8.31/app/static
mkdir -p backup_0.7.8.30_before_0.7.8.31/app/downloads

cp app/main.py backup_0.7.8.30_before_0.7.8.31/app/
cp app/routers/marketplaces.py backup_0.7.8.30_before_0.7.8.31/app/routers/
cp app/static/app.js backup_0.7.8.30_before_0.7.8.31/app/static/
cp app/static/print-agent.html backup_0.7.8.30_before_0.7.8.31/app/static/
cp "app/downloads/Инструкция_FulfillCore_Print_Agent_1.1.7_полная.docx" backup_0.7.8.30_before_0.7.8.31/app/downloads/
```

## 4. Распакуйте обновление

```bash
unzip -o /root/FulfillCore_0.7.8.31_Warehouse_Status_Columns_Age_PrintAgent_FIX.zip -d /opt/fulfillcore
```

## 5. Перезапустите API

```bash
cd /opt/fulfillcore

docker compose \
  --env-file server/.env.server \
  -f docker-compose.server.yml \
  -f docker-compose.hotpatch.yml \
  restart api
```

## 6. Проверьте версию

```bash
sleep 10

docker compose \
  --env-file server/.env.server \
  -f docker-compose.server.yml \
  -f docker-compose.hotpatch.yml \
  exec api python -c "from app.main import app; print(app.version)"
```

Ожидаемый результат:

```text
0.7.8.31
```

Дополнительно:

```bash
curl -s https://5-129-203-223.nip.io/health
```

## 7. Обновите страницу на рабочем компьютере

После установки нажмите `Ctrl + F5`. Затем:

1. переключите склад FulfillCore и убедитесь, что показываются его остатки;
2. в WB сохраните режимы складов кнопкой «Сохранить выбор»;
3. откройте таблицу заказов, перетащите столбец и проверьте фильтр под заголовком;
4. откройте «Настройки печати → Установка и подключение» и проверьте кнопку «Показать токен»;
5. отсканируйте товар и ЧЗ: заказ должен остаться «Упаковано», а этикетка — уйти в Print Agent.

## Откат

Если потребуется вернуть предыдущие файлы:

```bash
cd /opt/fulfillcore

cp backup_0.7.8.30_before_0.7.8.31/app/main.py app/main.py
cp backup_0.7.8.30_before_0.7.8.31/app/routers/marketplaces.py app/routers/marketplaces.py
cp backup_0.7.8.30_before_0.7.8.31/app/static/app.js app/static/app.js
cp backup_0.7.8.30_before_0.7.8.31/app/static/print-agent.html app/static/print-agent.html
cp "backup_0.7.8.30_before_0.7.8.31/app/downloads/Инструкция_FulfillCore_Print_Agent_1.1.7_полная.docx" app/downloads/

docker compose \
  --env-file server/.env.server \
  -f docker-compose.server.yml \
  -f docker-compose.hotpatch.yml \
  restart api
```
