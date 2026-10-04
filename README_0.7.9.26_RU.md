# FulfillCore 0.7.9.26 — диагностика времени передачи ЧЗ

Версия продолжает 0.7.9.25 и не меняет рабочую бизнес-логику упаковки/ЧЗ.

Добавлено измерение времени критического пути `WB /packing/scan-chz`:
- `db_precheck_ms` — локальная подготовка/проверки;
- `local_save_ms` — сохранение ЧЗ в PostgreSQL;
- `wb_status_ms` — запрос/обновление статуса заказа WB;
- `wb_confirm_ms` — подготовка заказа WB к confirm, если она понадобилась;
- `wb_set_sgtin_ms` — непосредственная передача SGTIN/ЧЗ в WB;
- `wb_meta_ms` — чтение метаданных WB, если оно выполнялось;
- `wb_reshipment_ms` — проверка повторной отгрузки для закрытой поставки;
- `wb_total_ms` — весь участок общения с WB;
- `finalize_ms` — локальное завершение упаковки;
- `total_ms` — полное время обработки скана ЧЗ на API.

После каждого обработанного ЧЗ сервер пишет строку `FC_CHZ_TIMING` в лог API и сохраняет последний замер в истории заказа (`CHZ_TIMING`). Ответ scan-chz также содержит `chz_timing`.

Для диагностики после 5–10 сканов:

```bash
cd /opt/fulfillcore
docker compose --env-file server/.env.server -f docker-compose.server.yml -f docker-compose.hotpatch.yml logs --since=15m api 2>&1 | grep FC_CHZ_TIMING
```

Если основное время в `wb_set_sgtin_ms`/`wb_total_ms`, задержка находится на участке сервер ↔ WB. Если `local_save_ms`/`db_precheck_ms` велики — причина локальная (БД/API). Если все серверные метрики малы, а физическая этикетка выходит поздно — отдельно проверяется очередь печати/Print Agent.
