# FulfillCore 0.7.5.2 — точная очередь упаковки WB

Критическое исправление логики статусов FBS:

- Закрытая поставка WB, `supplierStatus=complete` и транспортные статусы WB **не переводят заказ в «Доставляется» без ЧЗ**.
- Этикетка заказа WB сама по себе ничего не доказывает: WB может присвоить её автоматически.
- Если **нет ЧЗ**, заказ остаётся **«Новый»**, кроме случаев, когда сотрудник FulfillCore сам перевёл его в **«На сборке»** / сформировал локальный лист сборки.
- **«На сборке»** больше не берётся из `supplierStatus=confirm`; это только внутренний статус FulfillCore.
- **ЧЗ + этикетка**, обнаруженные в WB без внутреннего сканирования FulfillCore → **«Доставляется»**.
- Внутри FulfillCore: скан товара + ЧЗ + этикетка → **«Упаковано»**; после создания отгрузки в FulfillCore → **«Доставляется»**.
- Реальные конечные статусы WB `sold` и отмена остаются конечными и не возвращаются в очередь упаковки.

После установки обязательно выполнить принудительную синхронизацию WB, чтобы пересчитать уже сохранённые строки.

# FulfillCore 0.7.5.1 — уточнение логики ЧЗ + этикетки

Патч ставится поверх 0.7.5. Новых миграций БД нет.

## Новое правило статусов

- если ЧЗ + этикетка WB были сделаны **в FulfillCore** (есть локальный факт сканирования товара `product_scanned_at`) -> `Упаковано`;
- если ЧЗ + этикетка уже есть в WB, но они были сделаны **не в FulfillCore** -> `Доставляется`;
- `supplierStatus=complete` и транспортные статусы WB -> `Доставляется`;
- `wbStatus=sold` -> `Получен`;
- отменённые WB -> `Отменён`;
- `supplierStatus=confirm` без полного комплекта ЧЗ+этикетка -> `На сборке`;
- `supplierStatus=new` -> `Новый`.

То есть чужая WMS больше не будет создавать ложное `Упаковано` в FulfillCore.

## Установка

На Windows:

```powershell
scp "$env:USERPROFILE\Downloads\FulfillCore_0.7.5.1_WB_External_Packed_Is_Delivery_PATCH.zip" root@5.129.203.223:/root/
ssh root@5.129.203.223
```

На сервере:

```bash
cd /opt/fulfillcore
unzip -o /root/FulfillCore_0.7.5.1_WB_External_Packed_Is_Delivery_PATCH.zip -d /opt/fulfillcore
docker compose --env-file server/.env.server -f docker-compose.server.yml up -d --build --force-recreate api
sleep 20
docker compose --env-file server/.env.server -f docker-compose.server.yml ps
```

После `healthy` сделать `Ctrl+F5` и один раз нажать `Синхронизировать WB`.
