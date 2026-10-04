"""r136: local Product Base label printing through the existing Print Agent queue.

This router is deliberately separate from r97_local.py so the working local catalogue
and stock logic are not rewritten. It only reads an existing SKU and creates print jobs.
"""
from __future__ import annotations

import io
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import SKU
from app.services.printing import queue_print_job
from app.services.receiving_labels import HARD_LABEL_HEIGHT_MM, HARD_LABEL_WIDTH_MM, product_agent_png

router = APIRouter(prefix="/warehouse-ui/catalog", tags=["Локальная печать товаров"])


class LocalProductLabelRequest(BaseModel):
    client_id: uuid.UUID
    sku_id: uuid.UUID
    count: int = Field(default=1, ge=1, le=200)


class LocalProductLabelQueueRequest(LocalProductLabelRequest):
    print_station: str = Field(min_length=1, max_length=80)


def _sku_for_label(db: Session, client_id: uuid.UUID, sku_id: uuid.UUID) -> SKU:
    sku = db.get(SKU, sku_id)
    if not sku or sku.client_id != client_id or not bool(getattr(sku, "is_active", True)):
        raise HTTPException(status_code=404, detail="Товар не найден в локальной Базе товаров")
    if not str(getattr(sku, "barcode", "") or "").strip():
        raise HTTPException(status_code=422, detail="У товара нет штрихкода")
    return sku


def _label_png(sku: SKU) -> bytes:
    try:
        return product_agent_png(
            str(sku.barcode or ""),
            article=str(sku.article or ""),
            size=str(sku.size or ""),
            name=str(sku.name or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _safe_code(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or ""))[:80]
    return cleaned or "product"


@router.post("/local-label/queue")
def queue_local_product_label(payload: LocalProductLabelQueueRequest, db: Session = Depends(get_db)):
    """Queue repeatable local SKU labels. No WB credential/API is involved."""
    sku = _sku_for_label(db, payload.client_id, payload.sku_id)
    station = str(payload.print_station or "").strip().upper()
    if not station:
        raise HTTPException(status_code=422, detail="Укажите рабочее место печати")
    image = _label_png(sku)
    jobs = []
    for copy_no in range(1, int(payload.count) + 1):
        job = queue_print_job(
            db,
            station_code=station,
            payload=image,
            file_name=f"FulfillCore-product-{_safe_code(sku.barcode)}-{copy_no:03d}.png",
            document_type="LOCAL_PRODUCT_BARCODE_58X40",
            client_id=payload.client_id,
            object_type="SKU",
            object_id=str(sku.id),
            content_type="image/png",
        )
        jobs.append(job)
    db.commit()
    return {
        "ok": True,
        "print_station": station,
        "labels_queued": len(jobs),
        "sku_id": str(sku.id),
        "barcode": str(sku.barcode or ""),
        "article": str(sku.article or ""),
        "size": str(sku.size or ""),
        "width_mm": HARD_LABEL_WIDTH_MM,
        "height_mm": HARD_LABEL_HEIGHT_MM,
        "print_job_ids": [str(job.id) for job in jobs],
        "wb_api_called": False,
    }


@router.post("/local-label.pdf")
def local_product_label_pdf(payload: LocalProductLabelRequest, db: Session = Depends(get_db)):
    """Browser fallback using the very same 58x40 raster sent to Print Agent."""
    sku = _sku_for_label(db, payload.client_id, payload.sku_id)
    image = _label_png(sku)
    out = io.BytesIO()
    page_w, page_h = HARD_LABEL_WIDTH_MM * mm, HARD_LABEL_HEIGHT_MM * mm
    pdf = canvas.Canvas(out, pagesize=(page_w, page_h))
    reader = ImageReader(io.BytesIO(image))
    for _ in range(int(payload.count)):
        pdf.drawImage(reader, 0, 0, width=page_w, height=page_h, preserveAspectRatio=False, mask="auto")
        pdf.showPage()
    pdf.save()
    return Response(
        content=out.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="FulfillCore-product-{_safe_code(sku.barcode)}-58x40.pdf"',
            "X-FulfillCore-Label-Count": str(payload.count),
            "X-FulfillCore-Label-Size": "58x40mm",
        },
    )
