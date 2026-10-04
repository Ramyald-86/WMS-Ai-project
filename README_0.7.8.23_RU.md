# FulfillCore 0.7.8.23 — WB CHZ GS / Validation Diagnostic FIX

Точечный патч поверх 0.7.8.22. Печать, поставки, остатки и остальные рабочие модули не изменяются.

## Что исправлено

1. Сохраняется FNC1/GS-разделитель DataMatrix.
   - USB-HID сканер часто передаёт ASCII 29 как Ctrl+].
   - Браузер обычно не вставляет Ctrl+] в текстовое поле.
   - FulfillCore теперь перехватывает Ctrl+] в поле ЧЗ и вставляет настоящий ASCII GS (0x1D).

2. Добавлена безопасная реконструкция GS, если браузер его всё же потерял.
   - Только для однозначной структуры 01 + GTIN + 21 + serial + 91xxxx + 92 + crypto.
   - Никаких догадок при неоднозначной структуре.

3. Убрано преждевременное автоотправление длинного DataMatrix.
   - Было: 180 мс после последнего символа.
   - Стало: 650 мс.
   - Enter от сканера по-прежнему отправляет код мгновенно.

4. FulfillCore теперь читает `metaDetails[].decision` из WB после записи ЧЗ.
   В интерфейсе показывается настоящая причина отказа WB, например:
   - sgtinNoGS — нет GS-разделителя;
   - sgtinInvalidFormat — неверный формат;
   - sgtinInvalidPattern — неверная структура;
   - sgtinNotFound — код не найден в Честном знаке;
   - sgtinApplied — не введён в оборот;
   - pending — WB ещё проверяет код.

5. В журнал пишется безопасная диагностика, без полного криптохвоста:
   - длина кода;
   - количество GS;
   - наличие AI 01/21/91/92;
   - решение проверки WB.

## Почему это важно

WB требует передавать GS-разделитель как Unicode-escaped `\\u001D` в JSON. В Python внутри строки должен быть настоящий ASCII 29. `httpx` сам корректно сериализует такой символ в JSON, поэтому вручную заменять его на буквальную строку `\\u001D` перед отправкой нельзя.

## Установка

```bash
cd /opt/fulfillcore
mkdir -p backup_0.7.8.22_before_0.7.8.23
cp app/main.py backup_0.7.8.22_before_0.7.8.23/
cp app/routers/marketplaces.py backup_0.7.8.22_before_0.7.8.23/
cp app/static/app.js backup_0.7.8.22_before_0.7.8.23/
unzip -o /root/FulfillCore_0.7.8.23_CHZ_GS_Validation_FIX.zip -d /opt/fulfillcore
docker compose --env-file server/.env.server -f docker-compose.server.yml -f docker-compose.hotpatch.yml restart api
```

После перезапуска выполнить Ctrl+F5.
