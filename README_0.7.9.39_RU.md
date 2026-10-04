# FulfillCore 0.7.9.42

Точечный hotfix FBS-остатков поверх 0.7.9.38.

- Исправлена ошибка `WBClient object has no attribute warehouses`.
- Проверка `warehouseId` теперь работает через официальный `GET /api/v3/warehouses` даже на старом WBClient.
- Если метод `warehouses()` присутствует, он остаётся совместимым.
- Отправка остатков по `chrtId` и read-after-write проверка из 0.7.9.38 сохранены.
- Локальный остаток FulfillCore при ошибке WB не изменяется.
