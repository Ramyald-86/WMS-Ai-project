# FulfillCore 0.7.8.24 — WB CHZ Acceptance Guard FIX

Точечный патч поверх 0.7.8.23. Печать, поставки, остатки и остальные рабочие модули не переписывались.

## Что исправлено

1. Все операции WB для одного заказа теперь используют единый числовой `orderId`:
   - добавление заказа в поставку;
   - чтение статуса;
   - запись SGTIN;
   - обратная проверка метаданных;
   - получение этикетки и удаление ЧЗ.

2. Устранено ложное подтверждение ЧЗ при фоновой синхронизации.
   - WB может вернуть сам SGTIN в `metaDetails`, но одновременно вернуть отказной или ожидающий `decision`.
   - Наличие значения больше не считается подтверждением само по себе.
   - Отметка «ожидает WB» снимается только при разрешающем статусе проверки.

3. Заказ с локально сохранённым, но не подтверждённым WB кодом больше не может автоматически перейти в «Упаковано» или «Доставляется» из-за наличия старой этикетки WB.

4. Сохранены исправления 0.7.8.23:
   - перехват GS/FNC1 от USB-HID сканера;
   - передача настоящего ASCII GS (`0x1D`, в JSON — `\u001D`);
   - безопасное восстановление GS для однозначного шаблона `01/21/91/92`;
   - показ фактического `metaDetails[].decision` от WB.

## Установка

```bash
cd /opt/fulfillcore
mkdir -p backup_0.7.8.23_before_0.7.8.24
cp app/main.py backup_0.7.8.23_before_0.7.8.24/
cp app/routers/marketplaces.py backup_0.7.8.23_before_0.7.8.24/
cp app/static/app.js backup_0.7.8.23_before_0.7.8.24/
unzip -o /root/FulfillCore_0.7.8.24_CHZ_Acceptance_Guard_FIX.zip -d /opt/fulfillcore
docker compose --env-file server/.env.server -f docker-compose.server.yml -f docker-compose.hotpatch.yml restart api
```

После перезапуска выполните `Ctrl+F5`.

Проверка версии:

```bash
docker compose --env-file server/.env.server -f docker-compose.server.yml -f docker-compose.hotpatch.yml exec api python -c "from app.main import app; print(app.version)"
```

Ожидается `0.7.8.24`.
