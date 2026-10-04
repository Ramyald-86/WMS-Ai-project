"""r145f32: deterministic TSD box resolution and background receiving sync."""
from __future__ import annotations

import json
import re
import uuid
from urllib.parse import parse_qs, unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Container, Location, SKU
from app.routers import r145f19_finance as legacy
from app.routers import r145f30_receiving as fast
from app.services.r145f25_places import assert_place, public_code, scope

router = APIRouter(tags=["r145f32"])


def _clean(value) -> str:
    return str(value or "").strip().strip("\x00\ufeff")


def _tokens(raw: str) -> list[str]:
    out: list[str] = []

    def add(value):
        value = _clean(value)
        if not value:
            return
        try:
            value = unquote(value)
        except Exception:
            pass
        value = value.strip().strip('"\'')
        key = value.casefold()
        if value and key not in {x.casefold() for x in out}:
            out.append(value[:512])

    add(raw)
    s = _clean(raw)
    try:
        from app.services.box_intake_rules import normalize_box_scan
        add(normalize_box_scan(s))
    except Exception:
        pass
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            for key in ("barcode", "box_barcode", "container_barcode", "box", "container", "code", "name", "id"):
                add(obj.get(key))
    except Exception:
        pass
    try:
        u = urlparse(s)
        if u.scheme or u.netloc or u.query:
            add(u.path.rsplit("/", 1)[-1])
            q = parse_qs(u.query)
            for key in ("barcode", "box", "container", "code", "id"):
                for value in q.get(key, []):
                    add(value)
    except Exception:
        pass
    for m in re.finditer(r"(?:^|[;|\s])(?:box|container|barcode|code)\s*[:=]\s*([^;|\s]+)", s, re.I):
        add(m.group(1))
    return out


def _ordered(rows):
    # Legacy data can contain duplicate public names. Prefer a placed active box,
    # then use a stable UUID order instead of blocking the operator as ambiguous.
    return sorted(rows, key=lambda x: (getattr(x, "location_id", None) is None, str(getattr(x, "id", ""))))


def _query_exact(db: Session, client_id, warehouse_id, *, token: str, field: str):
    col = getattr(Container, field)
    rows = db.execute(select(Container).where(
        Container.client_id == client_id,
        Container.warehouse_id == warehouse_id,
        Container.status == "ACTIVE",
        func.lower(col) == token.casefold(),
    ).limit(50)).scalars().all()
    return _ordered(rows)


def _find_scoped_box(db: Session, client_id, warehouse_id, raw: str):
    tokens = _tokens(raw)
    for token in tokens:
        # 1. Internal UUID is the strongest possible identity.
        try:
            bid = uuid.UUID(token)
            row = db.execute(select(Container).where(
                Container.id == bid,
                Container.client_id == client_id,
                Container.warehouse_id == warehouse_id,
                Container.status == "ACTIVE",
            ).limit(1)).scalar_one_or_none()
            if row:
                return row, token, tokens, "id", 1
        except Exception:
            pass
        # 2. A scanned QR normally contains the box barcode.  Never mix this with
        #    a name match in one OR query: a barcode can equal another legacy name.
        rows = _query_exact(db, client_id, warehouse_id, token=token, field="barcode")
        if rows:
            return rows[0], token, tokens, "barcode", len(rows)
        # 3. Public name fallback.  Old databases may have duplicate names; choose
        #    deterministically within the already strict client+warehouse scope.
        rows = _query_exact(db, client_id, warehouse_id, token=token, field="name")
        if rows:
            return rows[0], token, tokens, "name", len(rows)
    return None, None, tokens, None, 0


def _scope_mismatch(db: Session, client_id, warehouse_id, tokens: list[str]):
    lowered = [x.casefold() for x in tokens if x]
    if not lowered:
        return None
    cond = or_(func.lower(Container.barcode).in_(lowered), func.lower(Container.name).in_(lowered))
    same_warehouse = db.execute(select(Container).where(
        Container.warehouse_id == warehouse_id,
        Container.client_id != client_id,
        Container.status.in_(["ACTIVE", "DEFECT_ACTIVE"]),
        cond,
    ).limit(1)).scalar_one_or_none()
    if same_warehouse:
        return "Короб существует на этом складе, но принадлежит другому клиенту. Выберите правильного клиента."
    other_warehouse = db.execute(select(Container).where(
        Container.client_id == client_id,
        Container.warehouse_id != warehouse_id,
        Container.status.in_(["ACTIVE", "DEFECT_ACTIVE"]),
        cond,
    ).limit(1)).scalar_one_or_none()
    if other_warehouse:
        return "Короб принадлежит другому складу этого клиента. Переключите склад; данные не перемещены."
    return None


class OpenPlace32(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=1024)
    target_type: str = Field(default="AUTO", max_length=32)


def _open_box_direct(db: Session, payload: OpenPlace32, box: Container):
    legacy.install_schema(db)
    if not box.location_id:
        raise HTTPException(409, detail=f"У короба {box.name or box.barcode} не указан адрес. Сначала разместите короб.")
    loc = db.execute(select(Location).where(
        Location.id == box.location_id,
        Location.warehouse_id == payload.warehouse_id,
        Location.is_active.is_(True),
    )).scalar_one_or_none()
    if not loc:
        raise HTTPException(409, detail="Адрес короба не найден или отключён на выбранном складе")
    assert_place(db, payload.client_id, payload.warehouse_id, loc.id)
    ctype = str(getattr(box, "container_type", "") or "").upper()
    if ctype in {"DEFECT_BOX", "TRANSIT_BOX", "FBO_BOX"}:
        raise HTTPException(409, detail="Этот короб относится к специальному контуру хранения и не открывается в обычной приёмке")
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
            "k": f"fc:r145f32:tsd-place:{payload.client_id}:{payload.warehouse_id}:{payload.worker.casefold()}"
        })
    receipt = legacy._active_receipt(db, payload.client_id, payload.warehouse_id, payload.worker)
    if not receipt:
        rid = str(uuid.uuid4())
        db.execute(text("""INSERT INTO fc_r145f19_tsd_receipts
            (id,client_id,warehouse_id,worker,reference,status)
            VALUES(:id,:c,:w,:worker,:ref,'DRAFT')"""), {
            "id": rid, "c": str(payload.client_id), "w": str(payload.warehouse_id),
            "worker": _clean(payload.worker)[:160], "ref": _clean(payload.reference)[:200],
        })
        db.flush()
        receipt = legacy._active_receipt(db, payload.client_id, payload.warehouse_id, payload.worker)
    if not receipt:
        raise HTTPException(500, detail="Не удалось открыть серверный черновик приёмки")
    existing = db.execute(text("""SELECT * FROM fc_r145f19_tsd_places
        WHERE receipt_id=:r AND status='OPEN' ORDER BY created_at DESC LIMIT 1"""),
        {"r": str(receipt["id"])}).mappings().first()
    if existing and str(existing.get("container_id") or "") == str(box.id):
        public = legacy._receipt_public(db, receipt)
        place = next((x for x in public.get("places", []) if str(x.get("id")) == str(existing["id"])), dict(existing))
        return public, place
    db.execute(text("UPDATE fc_r145f19_tsd_places SET status='CLOSED' WHERE receipt_id=:r AND status='OPEN'"),
               {"r": str(receipt["id"])})
    pid = str(uuid.uuid4())
    target_code = _clean(box.barcode or box.name or str(box.id))[:160]
    db.execute(text("""INSERT INTO fc_r145f19_tsd_places
        (id,receipt_id,target_kind,target_code,location_id,container_id,status)
        VALUES(:id,:r,'BOX',:code,:loc,:box,'OPEN')"""), {
        "id": pid, "r": str(receipt["id"]), "code": target_code,
        "loc": str(loc.id), "box": str(box.id),
    })
    db.commit()
    current = legacy._active_receipt(db, payload.client_id, payload.warehouse_id, payload.worker)
    public = legacy._receipt_public(db, current)
    place = next((x for x in public.get("places", []) if str(x.get("id")) == pid), None) or {
        "id": pid, "target_kind": "BOX", "target_code": target_code,
        "location_id": str(loc.id), "container_id": str(box.id), "status": "OPEN",
        "display_name": box.name or box.barcode, "location_display": public_code(loc),
    }
    return public, place


@router.post("/r145f32/tsd/receipts/open-place")
def open_place(payload: OpenPlace32, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    legacy.install_schema(db)
    box, matched, tokens, matched_by, duplicate_count = _find_scoped_box(db, payload.client_id, payload.warehouse_id, payload.code)
    if box:
        receipt, place = _open_box_direct(db, payload, box)
        return {"ok": True, "receipt": receipt, "place": place, "resolved_box": True,
                "box_id": str(box.id), "box_name": box.name or box.barcode,
                "matched": matched, "matched_by": matched_by, "duplicate_count": duplicate_count}
    mismatch = _scope_mismatch(db, payload.client_id, payload.warehouse_id, tokens)
    if mismatch:
        raise HTTPException(409, detail=mismatch)
    target = _clean(payload.target_type).upper() or "AUTO"
    if target == "BOX":
        raise HTTPException(404, detail="Существующий короб не найден у выбранного клиента на выбранном складе")
    old_payload = legacy.TsdPlaceOpen(
        client_id=payload.client_id, warehouse_id=payload.warehouse_id,
        worker=payload.worker, reference=payload.reference, code=payload.code,
        target_type=payload.target_type,
    )
    resolved = legacy._resolve_place(db, payload.client_id, payload.warehouse_id, payload.code, payload.target_type)
    assert_place(db, payload.client_id, payload.warehouse_id, resolved["location_id"])
    return legacy.tsd_open_place(old_payload, db)


class ManualBatchEvent(BaseModel):
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


class ManualBatch(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    events: list[ManualBatchEvent] = Field(default_factory=list, max_length=100)


@router.post("/r145f32/manual-drafts/sync-batch")
def sync_manual_batch(payload: ManualBatch, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    fast.install_schema(db)
    receipt, _ = fast._worker_receipt(db, payload.client_id, payload.warehouse_id, payload.worker,
                                      fast.actor(request), reference=payload.reference or "")
    rid = uuid.UUID(str(receipt["id"]))
    acks = []
    failed = []
    for ev in payload.events[:100]:
        scan = fast.FastScan(client_id=payload.client_id, warehouse_id=payload.warehouse_id,
            worker=payload.worker, request_id=ev.request_id, barcode=ev.barcode, quantity=ev.quantity,
            location_code=ev.location_code, container_code=ev.container_code, article=ev.article,
            size=ev.size, name=ev.name, product_found=ev.product_found, location_id=ev.location_id,
            container_id=ev.container_id, container_name=ev.container_name, section=ev.section,
            aisle=ev.aisle, pallet=ev.pallet, storage_display=ev.storage_display,
            duplicated_from=ev.duplicated_from, product_id=ev.product_id)
        try:
            result = fast.scan_fast(rid, scan, request, db)
            acks.append({"request_id": ev.request_id, "result": result})
        except HTTPException as exc:
            failed.append({"request_id": ev.request_id, "status": exc.status_code, "detail": exc.detail})
        except Exception as exc:
            failed.append({"request_id": ev.request_id, "status": 500, "detail": str(exc)})
    current = fast._receipt(db, rid, payload.client_id, payload.warehouse_id)
    return {"ok": not failed, "receipt_id": str(rid), "acks": acks, "failed": failed,
            "snapshot": fast._compact_snapshot(db, current)}


def _install_tsd_event_schema(db: Session):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("""CREATE TABLE IF NOT EXISTS fc_r145f32_tsd_events(
          request_id varchar(96) PRIMARY KEY,
          receipt_id uuid NOT NULL,
          line_id uuid,
          barcode varchar(120) NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now())"""))
    else:
        db.execute(text("""CREATE TABLE IF NOT EXISTS fc_r145f32_tsd_events(
          request_id varchar(96) PRIMARY KEY,
          receipt_id varchar(36) NOT NULL,
          line_id varchar(36),
          barcode varchar(120) NOT NULL,
          created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP)"""))
    db.commit()


class TsdBatchEvent(BaseModel):
    request_id: str = Field(min_length=1, max_length=96)
    barcode: str = Field(min_length=1, max_length=255)
    place_code: str = Field(min_length=1, max_length=1024)
    target_type: str = Field(default="AUTO", max_length=32)


class TsdBatch(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str = Field(min_length=1, max_length=255)
    events: list[TsdBatchEvent] = Field(default_factory=list, max_length=100)


@router.post("/r145f32/tsd/sync-batch")
def sync_tsd_batch(payload: TsdBatch, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    legacy.install_schema(db)
    _install_tsd_event_schema(db)
    acks = []
    failed = []
    for ev in payload.events[:100]:
        existing = db.execute(text("SELECT request_id FROM fc_r145f32_tsd_events WHERE request_id=:q"),
                              {"q": ev.request_id}).first()
        if existing:
            acks.append({"request_id": ev.request_id, "duplicate": True})
            continue
        op = OpenPlace32(client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                         worker=payload.worker, reference=payload.reference,
                         code=ev.place_code, target_type=ev.target_type)
        try:
            opened = open_place(op, db)
            receipt = legacy._active_receipt(db, payload.client_id, payload.warehouse_id, payload.worker)
            if not receipt:
                raise HTTPException(409, detail="Серверный черновик приёмки не найден")
            place = db.execute(text("SELECT * FROM fc_r145f19_tsd_places WHERE receipt_id=:r AND status='OPEN' ORDER BY created_at DESC LIMIT 1"),
                               {"r": str(receipt["id"])}).mappings().first()
            if not place:
                raise HTTPException(409, detail="Короб не открыт")
            barcode = legacy._normalize_scan(ev.barcode)
            skus = db.execute(select(SKU).where(SKU.client_id == payload.client_id, SKU.barcode == barcode)).scalars().all()
            if len(skus) > 1:
                raise HTTPException(409, detail="ШК привязан к нескольким SKU")
            if not skus:
                raise HTTPException(404, detail={"code": "UNKNOWN_PRODUCT", "barcode": barcode,
                                                 "message": "Новый товар. Укажите артикул, размер, название и категорию."})
            sku = skus[0]
            line_id = str(uuid.uuid4())
            db.execute(text("""INSERT INTO fc_r145f19_tsd_lines
              (id,receipt_id,place_id,sku_id,barcode,article,size,name,quantity)
              VALUES(:id,:r,:p,:s,:b,:a,:z,:n,1)"""), {
                "id": line_id, "r": str(receipt["id"]), "p": str(place["id"]), "s": str(sku.id),
                "b": sku.barcode or barcode, "a": sku.article or "", "z": sku.size or "", "n": sku.name or "",
            })
            db.execute(text("INSERT INTO fc_r145f32_tsd_events(request_id,receipt_id,line_id,barcode) VALUES(:q,:r,:l,:b)"),
                       {"q": ev.request_id, "r": str(receipt["id"]), "l": line_id, "b": barcode})
            db.commit()
            acks.append({"request_id": ev.request_id, "line_id": line_id,
                         "box_name": opened.get("box_name") or opened.get("place", {}).get("display_name")})
        except HTTPException as exc:
            db.rollback()
            failed.append({"request_id": ev.request_id, "status": exc.status_code, "detail": exc.detail})
        except Exception as exc:
            db.rollback()
            failed.append({"request_id": ev.request_id, "status": 500, "detail": str(exc)})
    receipt = legacy._active_receipt(db, payload.client_id, payload.warehouse_id, payload.worker)
    return {"ok": not failed, "acks": acks, "failed": failed,
            "receipt": legacy._receipt_public(db, receipt) if receipt else None}


@router.get("/r145f32/tsd/box-resolver-check")
def resolver_check(client_id: uuid.UUID, warehouse_id: uuid.UUID, code: str, db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id)
    box, matched, tokens, matched_by, duplicate_count = _find_scoped_box(db, client_id, warehouse_id, code)
    if not box:
        return {"ok": True, "found": False, "tokens": tokens[:8]}
    return {"ok": True, "found": True, "box_id": str(box.id), "box_name": box.name or box.barcode,
            "matched": matched, "matched_by": matched_by, "duplicate_count": duplicate_count,
            "location_id": str(box.location_id) if box.location_id else None}
