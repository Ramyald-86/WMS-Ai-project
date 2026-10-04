"""r145f31: robust existing-box resolver for TSD receiving.

The legacy TSD open-place endpoint remains available for locations.  This
endpoint resolves existing boxes explicitly by scoped container id/barcode/name
(including common QR wrappers) and opens exactly that box without creating or
moving anything.
"""
from __future__ import annotations

import json
import re
import uuid
from urllib.parse import parse_qs, unquote, urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Container, Location
from app.routers import r145f19_finance as legacy
from app.services.r145f25_places import assert_place, public_code, scope

router = APIRouter(tags=["r145f31"])


class OpenPlace31(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str = Field(min_length=1, max_length=255)
    code: str = Field(min_length=1, max_length=1024)
    target_type: str = Field(default="AUTO", max_length=32)


def _clean(value) -> str:
    return str(value or "").strip().strip("\x00\ufeff")


def _tokens(raw: str) -> list[str]:
    """Return ordered, exact-match candidates from scanner/QR payloads."""
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
        if value and value.casefold() not in {x.casefold() for x in out}:
            out.append(value[:512])

    add(raw)
    s = _clean(raw)

    # Reuse the installed normalizer when present. Older labels sometimes carry
    # a scanner wrapper that the desktop receiving flow already understands.
    try:
        from app.services.box_intake_rules import normalize_box_scan
        add(normalize_box_scan(s))
    except Exception:
        pass

    # JSON QR payloads used by some older label generators.
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            for key in ("barcode", "box_barcode", "container_barcode", "box", "container", "code", "name", "id"):
                add(obj.get(key))
    except Exception:
        pass

    # URL-style QR payloads and query parameters.
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

    # Common textual wrappers: BOX:..., BOX=..., container:...
    for m in re.finditer(r"(?:^|[;|\s])(?:box|container|barcode|code)\s*[:=]\s*([^;|\s]+)", s, re.I):
        add(m.group(1))

    return out


def _rows_for_token(db: Session, client_id, warehouse_id, token: str):
    conditions = [func.lower(Container.barcode) == token.casefold(), func.lower(Container.name) == token.casefold()]
    try:
        conditions.append(Container.id == uuid.UUID(token))
    except Exception:
        pass
    return db.execute(select(Container).where(
        Container.client_id == client_id,
        Container.warehouse_id == warehouse_id,
        Container.status == "ACTIVE",
        or_(*conditions),
    ).limit(4)).scalars().all()


def _find_scoped_box(db: Session, client_id, warehouse_id, raw: str):
    tokens = _tokens(raw)
    ambiguous = []
    for token in tokens:
        rows = _rows_for_token(db, client_id, warehouse_id, token)
        unique = {str(x.id): x for x in rows}
        if len(unique) == 1:
            return next(iter(unique.values())), token, tokens
        if len(unique) > 1:
            ambiguous.extend(unique.values())
    if ambiguous:
        raise HTTPException(409, detail="Найдено несколько коробов с таким обозначением. Отсканируйте QR конкретного короба.")
    return None, None, tokens


def _scope_mismatch(db: Session, client_id, warehouse_id, tokens: list[str]):
    lowered = [x.casefold() for x in tokens if x]
    if not lowered:
        return None
    same_warehouse = db.execute(select(Container).where(
        Container.warehouse_id == warehouse_id,
        Container.client_id != client_id,
        Container.status.in_(["ACTIVE", "DEFECT_ACTIVE"]),
        or_(func.lower(Container.barcode).in_(lowered), func.lower(Container.name).in_(lowered)),
    ).limit(1)).scalar_one_or_none()
    if same_warehouse:
        return "Короб существует на этом складе, но принадлежит другому клиенту. Выберите правильного клиента."
    other_warehouse = db.execute(select(Container).where(
        Container.client_id == client_id,
        Container.warehouse_id != warehouse_id,
        Container.status.in_(["ACTIVE", "DEFECT_ACTIVE"]),
        or_(func.lower(Container.barcode).in_(lowered), func.lower(Container.name).in_(lowered)),
    ).limit(1)).scalar_one_or_none()
    if other_warehouse:
        return "Короб принадлежит другому складу этого клиента. Переключите склад; данные не перемещены."
    return None


def _open_box_direct(db: Session, payload: OpenPlace31, box: Container):
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

    # Serialize receipt/place switching for a worker so two quick scanner events
    # cannot create two OPEN places.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
            "k": f"fc:r145f31:tsd-place:{payload.client_id}:{payload.warehouse_id}:{payload.worker.casefold()}"
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
        return {"ok": True, "receipt": public, "place": place, "resolved_box": True,
                "box_id": str(box.id), "box_name": box.name or box.barcode}

    # Switching place is explicit: close the previous OPEN place but keep every
    # already scanned line linked to it.
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
    place = next((x for x in public.get("places", []) if str(x.get("id")) == pid), None)
    if not place:
        place = {
            "id": pid, "target_kind": "BOX", "target_code": target_code,
            "location_id": str(loc.id), "container_id": str(box.id), "status": "OPEN",
            "display_name": box.name or box.barcode,
            "location_display": public_code(loc),
        }
    return {"ok": True, "receipt": public, "place": place, "resolved_box": True,
            "box_id": str(box.id), "box_name": box.name or box.barcode}


@router.post("/r145f31/tsd/receipts/open-place")
def open_place(payload: OpenPlace31, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    legacy.install_schema(db)
    box, matched, tokens = _find_scoped_box(db, payload.client_id, payload.warehouse_id, payload.code)
    if box:
        return _open_box_direct(db, payload, box)

    target = _clean(payload.target_type).upper() or "AUTO"
    mismatch = _scope_mismatch(db, payload.client_id, payload.warehouse_id, tokens)
    if mismatch:
        raise HTTPException(409, detail=mismatch)
    if target == "BOX":
        raise HTTPException(404, detail="Существующий короб не найден у выбранного клиента на выбранном складе")

    # No box matched. Preserve legacy AUTO/LOCATION behaviour for cells/pallets,
    # but keep the r145f25 scope guard before opening the place.
    old_payload = legacy.TsdPlaceOpen(
        client_id=payload.client_id, warehouse_id=payload.warehouse_id,
        worker=payload.worker, reference=payload.reference, code=payload.code,
        target_type=payload.target_type,
    )
    resolved = legacy._resolve_place(db, payload.client_id, payload.warehouse_id, payload.code, payload.target_type)
    assert_place(db, payload.client_id, payload.warehouse_id, resolved["location_id"])
    return legacy.tsd_open_place(old_payload, db)


@router.get("/r145f31/tsd/box-resolver-check")
def resolver_check(client_id: uuid.UUID, warehouse_id: uuid.UUID, code: str, db: Session = Depends(get_db)):
    """Read-only diagnostic used by the installer and support."""
    scope(db, client_id, warehouse_id)
    box, matched, tokens = _find_scoped_box(db, client_id, warehouse_id, code)
    if not box:
        return {"ok": True, "found": False, "tokens": tokens[:8]}
    return {"ok": True, "found": True, "box_id": str(box.id), "box_name": box.name or box.barcode,
            "matched": matched, "location_id": str(box.location_id) if box.location_id else None}
