FulfillCore 0.9.4.8a ROUTE RESTORE HOTFIX

Причина ошибки "Not Found" после установки 0.9.4.8 REV2 найдена точно.
В apply_rev2_0_9_4_8.py функция replace_function удаляла декоратор FastAPI вместе с функцией,
а затем возвращала тело функции без декоратора. Из-за этого пропали HTTP-маршруты:
  POST /marketplaces/wb/packing/scan-product
  POST /marketplaces/wb/orders/{order_id}/label/queue

Поэтому "Not Found" означал не "товар отсутствует", а "endpoint отсутствует".

0.9.4.8a:
- восстанавливает маршруты scan-product и label/queue;
- проверяет scan-chz и packing/progress;
- исправляет сам apply_rev2_0_9_4_8.py, чтобы повторная установка больше не удаляла декораторы;
- НЕ меняет поиск товара, резервы, FIFO, Stock Core, ЧЗ и Print Agent.
