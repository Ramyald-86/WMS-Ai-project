FulfillCore 0.7.8.12 — точечный dependency hotfix

Причина: app/routers/marketplaces.py импортирует supply_box_label_png,
а на сервере app/services/raster_labels.py оказался более старой версии без этой функции.

Патч содержит ТОЛЬКО app/services/raster_labels.py.
Он не меняет существующие функции product_barcode_label_png, wb_sticker_exact_png и print_calibration_png;
добавляет только supply_box_label_png, который уже использовался модулем этикеток поставки.

После установки версия приложения остается 0.7.8.12.
