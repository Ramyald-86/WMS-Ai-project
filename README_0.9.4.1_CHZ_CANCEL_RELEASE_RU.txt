FULFILLCORE 0.9.4.1 — CHZ CANCEL RELEASE

Назначение
----------
Точечное обновление поверх рабочей 0.9.4.

Исправляет только жизненный цикл ЧЗ при отмене заказа:
- когда заказ переходит в отменённые, сохранённый chz_code отвязывается от заказа;
- код снова можно использовать для другого заказа;
- в историю пишется событие CHZ_RELEASED_ON_CANCEL;
- логика остатков, резервов, сборочного листа, упаковки и передачи ЧЗ в WB не переписывается;
- после установки выполняется разовый backfill уже отменённых заказов с зависшим ЧЗ.

Установка
---------
1. Скопировать ZIP на сервер в /root/
2. Распаковать содержимое в /opt/fulfillcore с заменой папки tools:
   cd /opt/fulfillcore
   python3 - <<'PY'
import zipfile
p='/root/FulfillCore_0.9.4.1_CHZ_CANCEL_RELEASE.zip'
with zipfile.ZipFile(p) as z:
    z.extractall('/opt/fulfillcore')
print('OK extracted')
PY

3. Проверка/установка:
   cd /opt/fulfillcore
   chmod +x tools/deploy_live_0_9_4_1.sh
   sh tools/deploy_live_0_9_4_1.sh

Успешный финал:
OK: FulfillCore 0.9.4.1 CHZ_CANCEL_RELEASE is installed

Проверка после установки
------------------------
- открыть ранее отменённый заказ с ЧЗ: поле ЧЗ должно быть пустым;
- новый отменённый заказ после синхронизации WB также должен освобождать ЧЗ;
- в истории заказа должно появляться CHZ_RELEASED_ON_CANCEL;
- этот же DataMatrix должен разрешаться для сканирования в другой заказ.

Откат
-----
Deploy создаёт backup в /tmp/fc_0_9_4_1_chz_backup_ДАТА_ВРЕМЯ.
При ошибке до завершения health-check скрипт выполняет rollback автоматически.

Важно
-----
Обновление не меняет Print Agent и не меняет текущий токен Print Agent.
