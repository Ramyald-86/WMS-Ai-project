"""Fast local article lookup for PVZ receiving.

This endpoint intentionally avoids the card-oriented /catalog/local-products path.
That catalog builds WB-card maps and can scan tens of thousands of SKU rows, which
is appropriate for the full Product Base page but too expensive for a scanner UI.
PVZ needs only article -> size/barcode/name, so this route hits the indexed SKU
identity table directly and never calls marketplace APIs.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import SKU


router = APIRouter(prefix="/pvz", tags=["PVZ fast local lookup"])


def _serialize(row) -> dict:
    return {
        "id": str(row["id"]),
        "article": str(row["article"] or ""),
        "size": str(row["size"] or ""),
        "barcode": str(row["barcode"] or ""),
        "name": str(row["name"] or ""),
        "sku_code": str(row["sku_code"] or ""),
        "source": "LOCAL" if str(row["sku_code"] or "").startswith("LOCAL-") else "SKU",
    }


def _run(db: Session, stmt, limit: int):
    rows = db.execute(stmt.limit(limit + 1)).mappings().all()
    truncated = len(rows) > limit
    rows = rows[:limit]
    seen = set()
    result = []
    for row in rows:
        code = str(row["barcode"] or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        result.append(_serialize(row))
    return result, truncated


@router.get("/article-search-fast")
def article_search_fast(
    client_id: uuid.UUID,
    article: str = Query(min_length=1, max_length=128),
    limit: int = Query(default=80, ge=1, le=200),
    db: Session = Depends(get_db),
):
    """Return local SKU identities by article using the existing SKU indexes.

    Search order:
    1. exact article (fast path; uses client/article composite index),
    2. case-insensitive exact fallback,
    3. prefix fallback for type-ahead when at least 3 chars are entered.

    No WB/Ozon API is called and no stock/reservation data is touched.
    """
    started = time.perf_counter()
    value = str(article or "").strip()
    if not value:
        return {"rows": [], "mode": "empty", "truncated": False, "elapsed_ms": 0, "source": "LOCAL_SKU_INDEX"}

    cols = (SKU.id, SKU.article, SKU.size, SKU.barcode, SKU.name, SKU.sku_code)
    base = (
        select(*cols)
        .where(
            SKU.client_id == client_id,
            SKU.is_active.is_(True),
            SKU.barcode.is_not(None),
            SKU.barcode != "",
            SKU.article.is_not(None),
        )
        .order_by(SKU.size.asc().nulls_last(), SKU.barcode.asc())
    )

    rows, truncated = _run(db, base.where(SKU.article == value), limit)
    mode = "exact"

    if not rows:
        rows, truncated = _run(db, base.where(func.lower(SKU.article) == value.casefold()), limit)
        mode = "iexact"

    if not rows and len(value) >= 3:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows, truncated = _run(db, base.where(SKU.article.like(escaped + "%", escape="\\")), limit)
        mode = "prefix"

    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    return {
        "rows": rows,
        "mode": mode,
        "truncated": truncated,
        "elapsed_ms": elapsed_ms,
        "source": "LOCAL_SKU_INDEX",
        "wb_api_called": False,
    }
