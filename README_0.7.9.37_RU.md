# FulfillCore 0.7.9.42

Точечный hotfix скачивания Excel-шаблонов приёмки.

- Исправлен HTTP 500 / Internal Server Error при скачивании шаблонов с русским именем файла.
- Content-Disposition теперь использует ASCII fallback + RFC 5987 UTF-8 filename*.
- Форматы BULK_BOX5, BULK_SIMPLE2, PALLET_LAYOUT5 и STRUCTURED не менялись.
- Логика WB FBS chrtId из 0.7.9.36 сохранена без изменений.
