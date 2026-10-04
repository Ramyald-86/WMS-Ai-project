"""Pure checks for exact-place receiving reversal. No IO or stock writes."""
from __future__ import annotations
import hashlib
import json
from decimal import Decimal, InvalidOperation

ACTIVE_RESERVATIONS = ("SALE_RESERVED", "ACTIVE", "CONSUMING", "CANCELLING", "PACKING_WRITE_OFF")

def qty(value):
    try:
        n = Decimal(str(value if value is not None else "0"))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Некорректное количество в учёте")
    if not n.is_finite() or n < 0:
        raise ValueError("Количество должно быть конечным и неотрицательным")
    return n

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()

def check_bucket(row):
    """Conservative: never borrow from another box, FBS, reservation or pick."""
    issues = []
    need, physical = qty(row["quantity"]), qty(row["physical"])
    main, fbs, reserved = (qty(row.get(k)) for k in ("main", "fbs", "mirror_reserved"))
    if not need:
        issues.append("В исходной приёмке нет положительного количества")
    if not row.get("location_active"):
        issues.append("Исходный адрес удалён или неактивен")
    if not row.get("sku_active"):
        issues.append("Товар неактивен или не принадлежит выбранному клиенту")
    if row.get("container_id") and not row.get("container_valid"):
        issues.append("Короб перемещён, неактивен или принадлежит другому клиенту")
    if not row.get("mirror_found"):
        issues.append("Нет подтверждённого разделения остатка Основной/FBS")
    elif main + fbs + reserved != physical:
        issues.append("Физический остаток и разделение Основной/FBS расходятся")
    if physical < need:
        issues.append("В исходном месте осталось меньше, чем было принято")
    if main < need:
        issues.append("Недостаточно товара Основного склада в исходном месте")
    if fbs > 0:
        issues.append("В месте есть товар FBS: сначала остановите продажу и переведите его на Основной склад")
    if reserved > 0 or qty(row.get("reservation_qty")) > 0:
        issues.append("Есть резерв или неподтверждённое списание по заказам")
    if qty(row.get("active_order_floor")) > 0:
        issues.append("Есть новые заказы или заказы на сборке по этому товару: автоматическая отмена заблокирована")
    if qty(row.get("pick_qty")) > 0 or qty(row.get("external_pick_qty")) > 0:
        issues.append("Товар включён в незавершённую сборку")
    if row.get("outgoing_after_receipt"):
        issues.append("После приёмки было движение из этого места; автоматическая отмена небезопасна")
    if row.get("transferred"):
        issues.append("По строкам приёмки зафиксирован перевод в FBS")
    return issues
