"""r145f39: fast one-transaction receiving sync + robust manual draft line edits."""
from __future__ import annotations

import uuid
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers import r145f19_finance as legacy
from app.routers import r145f30_receiving as fast
from app.services import r145f27_receiving as draft_service
from app.services.r145f25_places import scope

router = APIRouter(tags=["r145f39"])


def _s(v, limit=512):
    return str(v or "").strip()[:limit]


class BatchEvent(BaseModel):
    request_id: str = Field(min_length=1, max_length=96)
    barcode: str = Field(min_length=1, max_length=255)
    quantity: int = Field(default=1, ge=1, le=100000)
    location_code: str = Field(min_length=1, max_length=255)
    container_code: str | None = Field(default=None, max_length=255)
    article: str | None = None
    size: str | None = None
    name: str | None = None
    product_found: bool = True
    location_id: str | None = None
    container_id: str | None = None
    container_name: str | None = None
    section: str | None = None
    aisle: str | None = None
    pallet: str | None = None
    storage_display: str | None = None
    duplicated_from: str | None = None
    product_id: str | None = None


class FastBatch(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    events: list[BatchEvent] = Field(default_factory=list, max_length=100)
    want_snapshot: bool = True


def _in_existing(db: Session, rid: str, request_ids: list[str]) -> dict[str, str | None]:
    if not request_ids:
        return {}
    params = {"r": rid}
    names = []
    for i, q in enumerate(request_ids):
        k = f"q{i}"
        params[k] = q
        names.append(f":{k}")
    rows = db.execute(text(
        "SELECT request_id,line_id FROM fc_r145f27_manual_events "
        f"WHERE receipt_id=:r AND request_id IN ({','.join(names)})"
    ), params).mappings().all()
    return {str(x["request_id"]): (str(x["line_id"]) if x.get("line_id") else None) for x in rows}


def _line_for_key(db: Session, rid: str, barcode: str, location_code: str, container_code: str | None):
    sql = """SELECT * FROM fc_r145f27_manual_lines
      WHERE receipt_id=:r AND posted_at IS NULL AND quantity>0 AND barcode=:b AND location_code=:l
        AND COALESCE(container_code,'')=COALESCE(:box,'') ORDER BY created_at,id LIMIT 1"""
    if db.bind.dialect.name == "postgresql":
        sql += " FOR UPDATE"
    return db.execute(text(sql), {
        "r": rid, "b": barcode, "l": location_code, "box": container_code,
    }).mappings().first()


@router.post("/r145f39/manual-drafts/sync-batch-fast")
def sync_batch_fast(payload: FastBatch, request: Request, db: Session = Depends(get_db)):
    """Persist up to 100 scan events in one transaction/commit.

    The old r145f32 route called scan_fast once per event. scan_fast commits and
    recalculates stats for every unit, which made a 20-50 scan queue need many
    round trips inside PostgreSQL. This endpoint validates each distinct place,
    aggregates same-line increments, writes all idempotency events, then commits
    once and calculates one final snapshot.
    """
    scope(db, payload.client_id, payload.warehouse_id)
    fast.install_schema(db)
    receipt, _ = fast._worker_receipt(
        db, payload.client_id, payload.warehouse_id, payload.worker,
        fast.actor(request), reference=payload.reference or "",
    )
    rid = str(receipt["id"])
    if str(receipt.get("status") or "") != "DRAFT":
        raise HTTPException(409, detail="Черновик уже проводится или завершён")

    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
            "k": f"fc:r145f39:fast-sync:{payload.client_id}:{payload.warehouse_id}:{payload.worker.casefold()}"
        })

    ordered: list[BatchEvent] = []
    seen: set[str] = set()
    for ev in payload.events[:100]:
        q = _s(ev.request_id, 96)
        if not q or q in seen:
            continue
        seen.add(q)
        ordered.append(ev)

    existing = _in_existing(db, rid, [_s(x.request_id, 96) for x in ordered])
    acks = [{"request_id": q, "line_id": line_id, "duplicate": True}
            for q, line_id in existing.items()]
    failed: list[dict] = []

    # Validate each distinct physical place once, not once per scanned unit.
    place_cache: dict[tuple[str, str], tuple] = {}
    valid: list[tuple[BatchEvent, object, object]] = []
    for ev in ordered:
        q = _s(ev.request_id, 96)
        if q in existing:
            continue
        barcode = _s(ev.barcode, 255)
        location_code = _s(ev.location_code, 255)
        container_code = _s(ev.container_code, 255) or None
        key = (location_code, container_code or "")
        try:
            if key not in place_cache:
                place_cache[key] = legacy.validate_place(
                    db, payload.client_id, payload.warehouse_id, location_code, container_code
                )
            loc, cont = place_cache[key]
            valid.append((ev, loc, cont))
        except HTTPException as exc:
            failed.append({"request_id": q, "status": exc.status_code, "detail": exc.detail})

    # Preserve first-seen order while aggregating increments for each draft line.
    groups: OrderedDict[tuple[str, str, str], list[tuple[BatchEvent, object, object]]] = OrderedDict()
    for ev, loc, cont in valid:
        key = (_s(ev.barcode, 255), _s(ev.location_code, 255), _s(ev.container_code, 255))
        groups.setdefault(key, []).append((ev, loc, cont))

    for (barcode, location_code, box_key), entries in groups.items():
        container_code = box_key or None
        line = _line_for_key(db, rid, barcode, location_code, container_code)
        before = int(line["quantity"] or 0) if line else 0
        total_delta = sum(int(ev.quantity or 1) for ev, _loc, _cont in entries)
        after = before + total_delta
        last, loc, cont = entries[-1]
        values = {
            "q": after,
            "a": _s(last.article, 255) or None,
            "z": _s(last.size, 255) or None,
            "n": _s(last.name, 512) or None,
            "pf": bool(last.product_found),
            "lid": str(getattr(loc, "id", None) or _s(last.location_id, 36) or "") or None,
            "cid": str(getattr(cont, "id", None) or _s(last.container_id, 36) or "") or None,
            "cname": _s(last.container_name, 255) or (_s(getattr(cont, "name", None), 255) if cont else None)
                     or (_s(getattr(cont, "barcode", None), 255) if cont else None),
            "sec": _s(last.section, 80) or None,
            "aisle": _s(last.aisle, 80) or None,
            "pal": _s(last.pallet, 120) or None,
            "disp": _s(last.storage_display, 512) or None,
            "dup": _s(last.duplicated_from, 255) or None,
            "prod": _s(last.product_id, 36) or None,
        }
        if line:
            line_id = str(line["id"])
            db.execute(text("""UPDATE fc_r145f27_manual_lines SET quantity=:q,article=:a,size=:z,name=:n,
                product_found=:pf,location_id=:lid,container_id=:cid,container_name=:cname,section=:sec,
                aisle=:aisle,pallet=:pal,storage_display=:disp,duplicated_from=:dup,product_id=:prod,
                updated_at=CURRENT_TIMESTAMP WHERE id=:id"""), {**values, "id": line_id})
        else:
            line_id = str(uuid.uuid4())
            db.execute(text("""INSERT INTO fc_r145f27_manual_lines
              (id,receipt_id,barcode,quantity,location_code,container_code,location_id,container_id,container_name,
               section,aisle,pallet,storage_display,duplicated_from,product_id,article,size,name,product_found)
              VALUES(:id,:r,:b,:q,:l,:box,:lid,:cid,:cname,:sec,:aisle,:pal,:disp,:dup,:prod,:a,:z,:n,:pf)"""),
              {**values, "id": line_id, "r": rid, "b": barcode, "l": location_code, "box": container_code})

        running = before
        for ev, _loc, _cont in entries:
            delta = int(ev.quantity or 1)
            nxt = running + delta
            req = _s(ev.request_id, 96)
            db.execute(text("""INSERT INTO fc_r145f27_manual_events
              (id,receipt_id,line_id,request_id,action,barcode,delta_qty,before_qty,after_qty,location_code,container_code,actor)
              VALUES(:id,:r,:line,:req,'SCAN',:b,:delta,:before,:after,:l,:box,:actor)"""), {
                "id": str(uuid.uuid4()), "r": rid, "line": line_id, "req": req,
                "b": barcode, "delta": delta, "before": running, "after": nxt,
                "l": location_code, "box": container_code, "actor": _s(payload.worker, 255),
            })
            acks.append({"request_id": req, "line_id": line_id})
            running = nxt

    if valid:
        db.execute(text("UPDATE fc_r145f27_manual_receipts SET updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {"id": rid})
    db.commit()

    stats = db.execute(text("""SELECT COALESCE(SUM(quantity),0) AS units,COUNT(*) AS positions
        FROM fc_r145f27_manual_lines WHERE receipt_id=:r AND quantity>0 AND posted_at IS NULL"""),
        {"r": rid}).mappings().first()
    snapshot = None
    if payload.want_snapshot:
        current = fast._receipt(db, uuid.UUID(rid), payload.client_id, payload.warehouse_id)
        snapshot = fast._compact_snapshot(db, current)
    return {
        "ok": not failed,
        "receipt_id": rid,
        "acks": acks,
        "failed": failed,
        "units": int(stats.get("units") or 0),
        "positions": int(stats.get("positions") or 0),
        "snapshot": snapshot,
        "single_commit": True,
    }


class LineSelector(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    request_id: str = Field(min_length=1, max_length=96)
    line_id: str | None = Field(default=None, max_length=160)
    barcode: str | None = Field(default=None, max_length=255)
    location_code: str | None = Field(default=None, max_length=255)
    container_code: str | None = Field(default=None, max_length=255)
    quantity: int | None = Field(default=None, ge=1, le=100000)


def _resolve_line_id(db: Session, rid: uuid.UUID, payload: LineSelector) -> uuid.UUID:
    raw = _s(payload.line_id, 160)
    if raw:
        try:
            parsed = uuid.UUID(raw)
            row = db.execute(text("SELECT id FROM fc_r145f27_manual_lines WHERE id=:id AND receipt_id=:r AND posted_at IS NULL"),
                             {"id": str(parsed), "r": str(rid)}).first()
            if row:
                return parsed
        except Exception:
            pass
    barcode = _s(payload.barcode, 255)
    if not barcode:
        raise HTTPException(422, detail="Не удалось определить строку товара. Обновите приёмку и повторите.")
    sql = """SELECT id FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND posted_at IS NULL AND quantity>0 AND barcode=:b"""
    params = {"r": str(rid), "b": barcode}
    if _s(payload.location_code, 255):
        sql += " AND location_code=:l"
        params["l"] = _s(payload.location_code, 255)
    # When a location is known, an omitted/null box means the no-box row.
    # This avoids selecting a same-barcode row from another box at that location.
    if payload.container_code is not None or _s(payload.location_code, 255):
        sql += " AND COALESCE(container_code,'')=COALESCE(:box,'')"
        params["box"] = _s(payload.container_code, 255) or None
    sql += " ORDER BY created_at,id LIMIT 2"
    rows = db.execute(text(sql), params).all()
    if not rows:
        raise HTTPException(404, detail="Строка товара ещё не подтверждена сервером. Подождите синхронизацию и повторите.")
    if len(rows) > 1 and not _s(payload.location_code, 255):
        raise HTTPException(409, detail="Найдено несколько строк этого ШК. Обновите приёмку и измените нужную строку ещё раз.")
    return uuid.UUID(str(rows[0][0]))


@router.post("/r145f39/manual-drafts/{rid}/adjust-line")
def adjust_line(rid: uuid.UUID, payload: LineSelector, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    fast.install_schema(db)
    r = fast._receipt(db, rid, payload.client_id, payload.warehouse_id, True)
    if _s(r.get("worker")).casefold() != _s(payload.worker).casefold():
        raise HTTPException(409, detail="Эта приёмка принадлежит другому приёмщику")
    line_id = _resolve_line_id(db, rid, payload)
    qty = int(payload.quantity or 0)
    if qty < 1:
        raise HTTPException(422, detail="Количество должно быть не меньше 1")
    return draft_service.set_quantity(db, rid, line_id, payload.client_id, payload.warehouse_id,
                                      fast.actor(request), {"request_id": payload.request_id, "quantity": qty})


@router.post("/r145f39/manual-drafts/{rid}/remove-line")
def remove_line(rid: uuid.UUID, payload: LineSelector, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    fast.install_schema(db)
    r = fast._receipt(db, rid, payload.client_id, payload.warehouse_id, True)
    if _s(r.get("worker")).casefold() != _s(payload.worker).casefold():
        raise HTTPException(409, detail="Эта приёмка принадлежит другому приёмщику")
    line_id = _resolve_line_id(db, rid, payload)
    return draft_service.remove_line(db, rid, line_id, payload.client_id, payload.warehouse_id,
                                     fast.actor(request), payload.request_id)
