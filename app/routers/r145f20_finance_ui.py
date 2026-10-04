"""r145f20: UI helpers only. No stock, receiving, printing or WB mutations.

Existing r145f19 report calculations and individual tariff API remain unchanged.
Bulk writes share one transaction and use the existing tariff table.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers.r145f19_finance import SERVICE_LABELS

router = APIRouter(prefix="/finance", tags=["Finance UI r145f20"])
# FC_R145F20_FINANCE_UI_ATOMIC_TARIFFS

class BulkTariffUpsert(BaseModel):
    client_id: uuid.UUID
    service_code: str = Field(min_length=1, max_length=48)
    categories: list[str]
    rate: Decimal
    unit: str = Field(default="шт", max_length=32)
    note: str = Field(default="", max_length=500)


def _validated_categories(payload: BulkTariffUpsert) -> tuple[str, list[str]]:
    service = payload.service_code.strip().upper()
    if service not in SERVICE_LABELS:
        raise HTTPException(422, detail="Неизвестная услуга")
    if not payload.categories or len(payload.categories) > 500:
        raise HTTPException(422, detail="Выберите от 1 до 500 категорий")
    categories = list(dict.fromkeys(c.strip() for c in payload.categories))
    if any(not c or len(c) > 160 for c in categories):
        raise HTTPException(422, detail="Название категории должно содержать от 1 до 160 символов")
    if "*" in categories and len(categories) != 1:
        raise HTTPException(422, detail="Общий тариф нельзя смешивать с выбранными категориями")
    if service == "STORAGE_BOX_DAY" and categories != ["*"]:
        raise HTTPException(422, detail="Хранение рассчитывается по общему тарифу за коробо-день")
    # Match the existing numeric(18,4) database column; reject NaN/Infinity.
    if (not payload.rate.is_finite() or payload.rate < 0
            or payload.rate > Decimal("99999999999999.9999")
            or payload.rate != payload.rate.quantize(Decimal("0.0001"))):
        raise HTTPException(422, detail="Укажите неотрицательный тариф, до 4 знаков после запятой")
    if not payload.unit.strip():
        raise HTTPException(422, detail="Укажите единицу тарифа")
    return service, categories


@router.post("/tariffs/bulk")
def bulk_tariffs(payload: BulkTariffUpsert, db: Session = Depends(get_db)):
    service, categories = _validated_categories(payload)
    sql = text("""INSERT INTO fc_finance_tariffs
        (id,client_id,service_code,category,rate,unit,note)
        VALUES (:id,:c,:s,:cat,:rate,:unit,:note)
        ON CONFLICT(client_id,service_code,category) DO UPDATE SET
        rate=excluded.rate,unit=excluded.unit,note=excluded.note,updated_at=now()""")
    try:
        # Sorted keys reduce lock-order differences between simultaneous editors.
        for category in sorted(categories):
            db.execute(sql, {
                "id": str(uuid.uuid4()), "c": str(payload.client_id),
                "s": service, "cat": category, "rate": payload.rate,
                "unit": payload.unit.strip(), "note": payload.note.strip(),
            })
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"ok": True, "saved": len(categories), "categories": categories,
            "client_id": str(payload.client_id), "service_code": service}


@router.get("/category-options")
def category_options(client_id: uuid.UUID, q: str = Query(default="", max_length=200),
                     db: Session = Depends(get_db)):
    """Return categories/counts only, not the full SKU catalogue.
    Optional search matches category, article or barcode. Tariff-only categories
    remain selectable. WB synchronization is still the existing explicit action.
    """
    query = q.strip()
    # Parameterized LIKE; user-supplied percent/underscore are treated literally.
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    args = {"c": str(client_id), "q": query, "pattern": pattern}
    rows = db.execute(text("""
        SELECT category, COUNT(*) AS products
        FROM fc_product_categories
        WHERE client_id=:c AND btrim(category)<>''
          AND (:q='' OR category ILIKE :pattern ESCAPE '\\'
                     OR article ILIKE :pattern ESCAPE '\\'
                     OR barcode ILIKE :pattern ESCAPE '\\')
        GROUP BY category ORDER BY category
    """), args).mappings().all()
    result = {str(r["category"]): int(r["products"]) for r in rows}
    tariff_rows = db.execute(text("""
        SELECT DISTINCT category FROM fc_finance_tariffs
        WHERE client_id=:c AND category<>'*' AND btrim(category)<>''
          AND (:q='' OR category ILIKE :pattern ESCAPE '\\')
    """), args).mappings().all()
    for row in tariff_rows:
        result.setdefault(str(row["category"]), 0)
    if not query or query.casefold() in "Без категории".casefold():
        result.setdefault("Без категории", 0)
    result.pop("*", None)
    return {"client_id": str(client_id), "rows": [
        {"category": key, "products": value}
        for key, value in sorted(result.items(), key=lambda item: item[0].casefold())
    ]}
