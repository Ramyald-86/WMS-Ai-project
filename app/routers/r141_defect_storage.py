from __future__ import annotations

import io
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from openpyxl import Workbook
from pydantic import BaseModel, Field
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Container, Location, SKU
from app.routers.warehouse_ui import scope
from app.services import pvz as pvz_service
from app.services.printing import queue_print_job
from app.services.receiving_labels import HARD_LABEL_HEIGHT_MM, HARD_LABEL_WIDTH_MM, box_agent_png
from app.services.excel_safety import append_safe, workbook_bytes

router = APIRouter(prefix="/defect-storage", tags=["Брак · отдельное хранение"])

DEFECT_LOCATION_CODE = "FC-DEFECT-STORAGE"
DEFECT_BOX_TYPE = "DEFECT_BOX"
DEFECT_ACTIVE = "DEFECT_ACTIVE"
DEFECT_ARCHIVED = "DEFECT_ARCHIVED"
_SCHEMA_READY = False


class DefectBoxCreate(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    performed_by: str = Field(default="Администратор", max_length=160)
    print_station: str | None = Field(default=None, max_length=80)
    custom_name: str | None = Field(default=None, max_length=160)


class DefectReceive(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    barcode: str = Field(default="", max_length=120)
    article: str = Field(min_length=1, max_length=180)
    size: str = Field(default="", max_length=100)
    name: str = Field(default="", max_length=240)
    quantity: int = Field(ge=1, le=100000)
    defect_reason: str = Field(min_length=1, max_length=1000)
    reference: str = Field(default="", max_length=300)
    comment: str = Field(default="", max_length=1000)
    container_code: str = Field(default="", max_length=255)
    performed_by: str = Field(default="Администратор", max_length=160)


class QtyAction(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    quantity: Decimal | None = Field(default=None, gt=0)
    reason: str = Field(default="", max_length=1000)
    performed_by: str = Field(default="Администратор", max_length=160)


class ScopedAction(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    reason: str = Field(default="", max_length=1000)
    performed_by: str = Field(default="Администратор", max_length=160)


class PrintRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    print_station: str = Field(min_length=1, max_length=80)


def install_schema(db: Session) -> None:
    """Additive only. Defect stock is deliberately outside physical inventory buckets."""
    global _SCHEMA_READY
    pvz_service.install_schema(db)
    db.execute(text("ALTER TABLE fc_pvz_lines ADD COLUMN IF NOT EXISTS written_off_qty NUMERIC(18,3) NOT NULL DEFAULT 0"))
    db.execute(text("ALTER TABLE fc_pvz_lines ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL"))
    db.execute(text("ALTER TABLE fc_pvz_lines ADD COLUMN IF NOT EXISTS deleted_by TEXT NOT NULL DEFAULT ''"))
    db.execute(text("ALTER TABLE fc_pvz_batches ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL"))
    db.execute(text("ALTER TABLE fc_pvz_batches ADD COLUMN IF NOT EXISTS deleted_by TEXT NOT NULL DEFAULT ''"))
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS fc_defect_actions (
          id UUID PRIMARY KEY,
          client_id UUID NOT NULL,
          warehouse_id UUID NOT NULL,
          batch_id UUID NULL,
          line_id UUID NULL,
          container_id UUID NULL,
          action VARCHAR(32) NOT NULL,
          quantity NUMERIC(18,3) NOT NULL DEFAULT 0,
          reason TEXT NOT NULL DEFAULT '',
          performed_by TEXT NOT NULL DEFAULT '',
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_defect_actions_scope ON fc_defect_actions(client_id,warehouse_id,created_at DESC)"))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_pvz_defect_active ON fc_pvz_lines(batch_id,container_id,deleted_at)"))
    db.flush()
    _SCHEMA_READY = True


def _ensure_schema(db: Session) -> None:
    if not _SCHEMA_READY:
        install_schema(db)


def _audit(db: Session, *, client_id, warehouse_id, action: str, quantity=0, batch_id=None, line_id=None, container_id=None, reason="", performed_by=""):
    db.execute(text("""INSERT INTO fc_defect_actions(id,client_id,warehouse_id,batch_id,line_id,container_id,action,quantity,reason,performed_by)
      VALUES(:id,:c,:w,:b,:l,:box,:a,:q,:r,:p)"""), {
        "id": str(uuid.uuid4()), "c": str(client_id), "w": str(warehouse_id),
        "b": str(batch_id) if batch_id else None, "l": str(line_id) if line_id else None,
        "box": str(container_id) if container_id else None, "a": action,
        "q": Decimal(str(quantity or 0)), "r": str(reason or "").strip(), "p": str(performed_by or "").strip(),
    })


def _defect_location(db: Session, warehouse_id: uuid.UUID) -> Location:
    loc = db.execute(select(Location).where(Location.warehouse_id == warehouse_id, Location.code == DEFECT_LOCATION_CODE)).scalars().first()
    if loc:
        # Internal technical address stays inactive so normal MAIN/FBS receiving never offers it.
        if loc.is_active:
            loc.is_active = False
        return loc
    loc = Location(
        warehouse_id=warehouse_id, parent_id=None, code=DEFECT_LOCATION_CODE,
        barcode=DEFECT_LOCATION_CODE, name="Брак · отдельное хранение",
        location_type="DEFECT", is_active=False,
    )
    db.add(loc); db.flush(); return loc


def _box_display(box: Container | None) -> str:
    if not box:
        return ""
    return str(getattr(box, "name", None) or box.barcode or "").strip()


def _get_defect_box(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID, code: str, *, required=True) -> Container | None:
    clean = str(code or "").strip()
    if not clean:
        if required:
            raise HTTPException(422, detail="Укажите или создайте короб брака")
        return None
    box = db.execute(select(Container).where(
        Container.client_id == client_id,
        Container.warehouse_id == warehouse_id,
        or_(Container.barcode == clean, Container.name == clean),
    )).scalars().first()
    if not box:
        if required:
            raise HTTPException(404, detail="Короб брака не найден. Создайте QR-короб в разделе «Брак» или в приёмке ПВЗ")
        return None
    if str(box.container_type or "").upper() != DEFECT_BOX_TYPE or str(box.status or "").upper() != DEFECT_ACTIVE:
        raise HTTPException(409, detail="Это не активный короб брака. Обычный складской короб для брака использовать нельзя")
    return box


def _clean_custom_box_name(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    # Preserve the operator's visible name, but reject scanner/control garbage and collapse whitespace.
    raw = re.sub(r"[\x00-\x1f\x7f]", "", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        raise HTTPException(422, detail="Название короба пустое")
    if len(raw) > 160:
        raise HTTPException(422, detail="Название короба слишком длинное: максимум 160 символов")
    return raw


def _existing_named_box(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID, custom_name: str) -> Container | None:
    key = custom_name.casefold()
    rows = db.execute(select(Container).where(
        Container.client_id == client_id,
        Container.warehouse_id == warehouse_id,
        or_(func.lower(Container.name) == key, func.lower(Container.barcode) == key),
    )).scalars().all()
    if not rows:
        return None
    box = rows[0]
    if str(box.container_type or "").upper() != DEFECT_BOX_TYPE:
        raise HTTPException(409, detail=f"Название «{custom_name}» уже занято обычным складским коробом")
    if str(box.status or "").upper() != DEFECT_ACTIVE:
        raise HTTPException(409, detail=f"Короб «{custom_name}» уже существует, но закрыт. Используйте другое название")
    return box


def _create_defect_box(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID, actor: str, custom_name: str | None = None) -> tuple[Container, bool]:
    loc = _defect_location(db, warehouse_id)
    requested = _clean_custom_box_name(custom_name)
    if requested:
        lock_key = f"fc:defect-box-custom:{client_id}:{warehouse_id}:{requested.casefold()}"
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": lock_key})
        existing = _existing_named_box(db, client_id, warehouse_id, requested)
        if existing:
            return existing, False
        # Keep an internal unique barcode in DB. The QR printed for a custom box contains the exact operator name.
        barcode = "FCBRK-CUSTOM-" + uuid.uuid4().hex[:16].upper()
        box = Container(
            client_id=client_id, warehouse_id=warehouse_id, location_id=loc.id,
            barcode=barcode, name=requested, container_type=DEFECT_BOX_TYPE, status=DEFECT_ACTIVE,
        )
        db.add(box); db.flush()
        _audit(db, client_id=client_id, warehouse_id=warehouse_id, action="CREATE_BOX_CUSTOM", container_id=box.id, reason=requested, performed_by=actor)
        return box, True

    day = datetime.now(timezone.utc).strftime("%d%m%y")
    lock_key = f"fc:defect-box:{client_id}:{warehouse_id}:{day}"
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": lock_key})
    rows = db.execute(select(Container.name).where(
        Container.client_id == client_id,
        Container.warehouse_id == warehouse_id,
        Container.container_type == DEFECT_BOX_TYPE,
        or_(Container.name.like(f"BRAK-{day}-%"), Container.name.like(f"БРАК-{day}-%")),
    )).scalars().all()
    seq = 1
    rx = re.compile(rf"^(?:BRAK|БРАК)-{re.escape(day)}-(\d+)$", re.I)
    found = []
    for name in rows:
        m = rx.match(str(name or ""))
        if m:
            found.append(int(m.group(1)))
    if found:
        seq = max(found) + 1
    name = f"BRAK-{day}-{seq:03d}"
    barcode = "FCBRK-" + uuid.uuid4().hex[:20].upper()
    box = Container(
        client_id=client_id, warehouse_id=warehouse_id, location_id=loc.id,
        barcode=barcode, name=name, container_type=DEFECT_BOX_TYPE, status=DEFECT_ACTIVE,
    )
    db.add(box); db.flush()
    _audit(db, client_id=client_id, warehouse_id=warehouse_id, action="CREATE_BOX", container_id=box.id, performed_by=actor)
    return box, True


def _box_qr_payload(box: Container) -> str:
    barcode = str(box.barcode or "").strip()
    if barcode.upper().startswith("FCBRK-CUSTOM-"):
        return _box_display(box)
    return barcode


def _queue_box_label(db: Session, box: Container, station: str):
    code = _box_qr_payload(box)
    display = _box_display(box)
    image = box_agent_png(code, display_name=display, footer_text="BRAK")
    job = queue_print_job(
        db, station_code=str(station or "").strip().upper(), payload=image,
        file_name=f"FulfillCore-defect-box-{box.id}.png", document_type="DEFECT_BOX_QR_58X40",
        client_id=box.client_id, object_type="CONTAINER", object_id=str(box.id), content_type="image/png",
    )
    return job


def _label_pdf(box: Container) -> bytes:
    image = box_agent_png(_box_qr_payload(box), display_name=_box_display(box), footer_text="BRAK")
    out = io.BytesIO(); page_w, page_h = HARD_LABEL_WIDTH_MM * mm, HARD_LABEL_HEIGHT_MM * mm
    pdf = canvas.Canvas(out, pagesize=(page_w, page_h))
    pdf.drawImage(ImageReader(io.BytesIO(image)), 0, 0, width=page_w, height=page_h, preserveAspectRatio=False, mask="auto")
    pdf.showPage(); pdf.save(); return out.getvalue()


def _sku_for_defect(db: Session, client_id: uuid.UUID, barcode: str, article: str, size: str) -> uuid.UUID | None:
    bc = str(barcode or "").strip(); art = str(article or "").strip(); sz = str(size or "").strip()
    q = select(SKU).where(SKU.client_id == client_id, SKU.is_active == True)  # noqa: E712
    sku = None
    if bc:
        sku = db.execute(q.where(SKU.barcode == bc)).scalars().first()
    if not sku and art:
        candidates = db.execute(q.where(func.lower(SKU.article) == art.lower())).scalars().all()
        if sz:
            exact = [x for x in candidates if str(x.size or "").strip().casefold() == sz.casefold()]
            sku = exact[0] if exact else None
        elif len(candidates) == 1:
            sku = candidates[0]
    return sku.id if sku else None


def _refresh_batch_status(db: Session, batch_id: uuid.UUID) -> None:
    remaining = db.execute(text("""SELECT COALESCE(SUM(GREATEST(quantity-written_off_qty,0)),0)
      FROM fc_pvz_lines WHERE batch_id=:b AND deleted_at IS NULL"""), {"b": str(batch_id)}).scalar()
    status = "COMPLETED" if Decimal(str(remaining or 0)) > 0 else "WRITTEN_OFF"
    db.execute(text("UPDATE fc_pvz_batches SET status=:s WHERE id=:b AND deleted_at IS NULL"), {"s": status, "b": str(batch_id)})


@router.post("/boxes")
def create_box(payload: DefectBoxCreate, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id); _ensure_schema(db)
    box, created = _create_defect_box(db, payload.client_id, payload.warehouse_id, payload.performed_by, payload.custom_name)
    job = None
    station = str(payload.print_station or "").strip().upper()
    if station:
        job = _queue_box_label(db, box, station)
    db.commit()
    return {
        "ok": True, "box_id": str(box.id), "name": _box_display(box), "barcode": str(box.barcode),
        "qr_value": _box_qr_payload(box), "created": created,
        "location": DEFECT_LOCATION_CODE, "print_station": station or None,
        "print_job_id": str(job.id) if job else None, "label_size": "58x40",
    }


@router.post("/boxes/{box_id}/label/queue")
def queue_box(box_id: uuid.UUID, payload: PrintRequest, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id); _ensure_schema(db)
    box = db.get(Container, box_id)
    if not box or box.client_id != payload.client_id or box.warehouse_id != payload.warehouse_id:
        raise HTTPException(404, detail="Короб брака не найден")
    if str(box.container_type or "").upper() != DEFECT_BOX_TYPE:
        raise HTTPException(409, detail="Это не короб брака")
    job = _queue_box_label(db, box, payload.print_station); db.commit()
    return {"ok": True, "name": _box_display(box), "print_station": job.station_code, "print_job_id": str(job.id), "label_size": "58x40"}


@router.get("/boxes/{box_id}/label.pdf")
def box_label_pdf(box_id: uuid.UUID, client_id: uuid.UUID, warehouse_id: uuid.UUID, db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id); _ensure_schema(db)
    box = db.get(Container, box_id)
    if not box or box.client_id != client_id or box.warehouse_id != warehouse_id or str(box.container_type or "").upper() != DEFECT_BOX_TYPE:
        raise HTTPException(404, detail="Короб брака не найден")
    return Response(_label_pdf(box), media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="FulfillCore-defect-box-58x40.pdf"'})


@router.post("/receive")
def receive_defect(payload: DefectReceive, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id); _ensure_schema(db)
    box = _get_defect_box(db, payload.client_id, payload.warehouse_id, payload.container_code, required=True)
    sku_id = _sku_for_defect(db, payload.client_id, payload.barcode, payload.article, payload.size)
    batch_id = uuid.uuid4(); line_id = uuid.uuid4()
    db.execute(text("""INSERT INTO fc_pvz_batches(id,client_id,warehouse_id,kind,status,reference,performed_by)
      VALUES(:id,:c,:w,'DEFECT','COMPLETED',:ref,:actor)"""), {
        "id": str(batch_id), "c": str(payload.client_id), "w": str(payload.warehouse_id),
        "ref": payload.reference.strip(), "actor": payload.performed_by.strip(),
    })
    db.execute(text("""INSERT INTO fc_pvz_lines(id,batch_id,sku_id,barcode,article,size,name,quantity,container_id,defect_reason,comment,written_off_qty)
      VALUES(:id,:b,:s,:bc,:a,:z,:n,:q,:box,:reason,:comment,0)"""), {
        "id": str(line_id), "b": str(batch_id), "s": str(sku_id) if sku_id else None,
        "bc": payload.barcode.strip(), "a": payload.article.strip(), "z": payload.size.strip(),
        "n": payload.name.strip(), "q": Decimal(payload.quantity), "box": str(box.id) if box else None,
        "reason": payload.defect_reason.strip(), "comment": payload.comment.strip(),
    })
    _audit(db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, action="RECEIVE", quantity=payload.quantity,
           batch_id=batch_id, line_id=line_id, container_id=box.id if box else None,
           reason=payload.defect_reason, performed_by=payload.performed_by)
    db.commit()
    return {"ok": True, "batch_id": str(batch_id), "line_id": str(line_id), "quantity": payload.quantity,
            "container": _box_display(box) if box else "Без короба",
            "message": "Брак принят в отдельное хранение и НЕ добавлен в MAIN/FBS"}


@router.get("")
def storage(client_id: uuid.UUID, warehouse_id: uuid.UUID, db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id); _ensure_schema(db)
    boxes = db.execute(select(Container).where(
        Container.client_id == client_id, Container.warehouse_id == warehouse_id,
        Container.container_type == DEFECT_BOX_TYPE, Container.status == DEFECT_ACTIVE,
    ).order_by(Container.created_at.desc())).scalars().all()
    rows = [dict(r) for r in db.execute(text("""SELECT CAST(l.id AS text) line_id,CAST(b.id AS text) batch_id,b.reference,b.performed_by,b.created_at,b.status,
      l.barcode,l.article,l.size,l.name,l.quantity,l.written_off_qty,l.defect_reason,l.comment,
      CAST(l.container_id AS text) container_id,COALESCE(c.name,c.barcode,'') container_name,COALESCE(c.barcode,'') container_barcode
      FROM fc_pvz_batches b JOIN fc_pvz_lines l ON l.batch_id=b.id
      LEFT JOIN containers c ON c.id=l.container_id
      WHERE CAST(b.client_id AS text)=:c AND CAST(b.warehouse_id AS text)=:w AND b.kind='DEFECT'
        AND b.deleted_at IS NULL AND l.deleted_at IS NULL
      ORDER BY b.created_at DESC,l.created_at DESC"""), {"c": str(client_id), "w": str(warehouse_id)}).mappings().all()]
    active=[]
    for r in rows:
        qty=Decimal(str(r.get("quantity") or 0)); wo=Decimal(str(r.get("written_off_qty") or 0)); rem=max(Decimal("0"),qty-wo)
        r["quantity"]=float(qty); r["written_off_qty"]=float(wo); r["remaining_qty"]=float(rem)
        if rem>0: active.append(r)
    by_box={str(b.id): {"id":str(b.id),"name":_box_display(b),"barcode":str(b.barcode or ""),"units":0.0,"lines":[],"created_at":b.created_at} for b in boxes}
    unboxed=[]
    for r in active:
        bid=str(r.get("container_id") or "")
        if bid and bid in by_box:
            by_box[bid]["lines"].append(r); by_box[bid]["units"] += float(r["remaining_qty"])
        else: unboxed.append(r)
    batch_map={}
    for r in active:
        b=batch_map.setdefault(r["batch_id"],{"id":r["batch_id"],"reference":r.get("reference") or "","performed_by":r.get("performed_by") or "","created_at":r.get("created_at"),"units":0.0,"lines":0,"boxes":set()})
        b["units"] += float(r["remaining_qty"]); b["lines"] += 1
        if r.get("container_name"): b["boxes"].add(r["container_name"])
    batches=[]
    for b in batch_map.values(): b["boxes"]=sorted(b["boxes"]); batches.append(b)
    batches.sort(key=lambda x: x.get("created_at") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    box_rows=[]
    for x in by_box.values():
        x["lines_count"]=len(x["lines"]); box_rows.append(x)
    return {"summary":{"units":sum(float(x["remaining_qty"]) for x in active),"boxes":sum(1 for x in box_rows if x["units"]>0),"open_boxes":len(box_rows),"unboxed_units":sum(float(x["remaining_qty"]) for x in unboxed),"batches":len(batches)},
            "boxes":box_rows,"unboxed":unboxed,"batches":batches}


def _line_scope_row(db, line_id, client_id, warehouse_id, lock=False):
    suffix=" FOR UPDATE" if lock else ""
    row=db.execute(text("""SELECT l.id,l.batch_id,l.container_id,l.quantity,l.written_off_qty,l.deleted_at,b.deleted_at batch_deleted
      FROM fc_pvz_lines l JOIN fc_pvz_batches b ON b.id=l.batch_id
      WHERE l.id=:id AND CAST(b.client_id AS text)=:c AND CAST(b.warehouse_id AS text)=:w AND b.kind='DEFECT'"""+suffix),
      {"id":str(line_id),"c":str(client_id),"w":str(warehouse_id)}).mappings().first()
    if not row: raise HTTPException(404,detail="Позиция брака не найдена")
    if row["deleted_at"] or row["batch_deleted"]: raise HTTPException(409,detail="Эта запись уже удалена")
    return row


@router.post("/lines/{line_id}/writeoff")
def writeoff_line(line_id: uuid.UUID, payload: QtyAction, db: Session = Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id); _ensure_schema(db)
    row=_line_scope_row(db,line_id,payload.client_id,payload.warehouse_id,True)
    qty=Decimal(str(row["quantity"])); done=Decimal(str(row["written_off_qty"] or 0)); remaining=max(Decimal("0"),qty-done)
    if remaining<=0: raise HTTPException(409,detail="Позиция уже полностью списана из хранения брака")
    take=Decimal(str(payload.quantity)) if payload.quantity is not None else remaining
    if take>remaining: raise HTTPException(422,detail=f"В хранении брака осталось только {remaining} шт.")
    db.execute(text("UPDATE fc_pvz_lines SET written_off_qty=written_off_qty+:q WHERE id=:id"),{"q":take,"id":str(line_id)})
    _audit(db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,action="WRITE_OFF_LINE",quantity=take,batch_id=row["batch_id"],line_id=line_id,container_id=row["container_id"],reason=payload.reason,performed_by=payload.performed_by)
    _refresh_batch_status(db,uuid.UUID(str(row["batch_id"]))); db.commit()
    return {"ok":True,"written_off":float(take),"remaining":float(remaining-take)}


@router.post("/lines/{line_id}/delete")
def delete_line(line_id: uuid.UUID, payload: ScopedAction, db: Session = Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id); _ensure_schema(db)
    row=_line_scope_row(db,line_id,payload.client_id,payload.warehouse_id,True)
    remaining=max(Decimal("0"),Decimal(str(row["quantity"]))-Decimal(str(row["written_off_qty"] or 0)))
    db.execute(text("UPDATE fc_pvz_lines SET deleted_at=now(),deleted_by=:p WHERE id=:id"),{"p":payload.performed_by,"id":str(line_id)})
    _audit(db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,action="DELETE_LINE",quantity=remaining,batch_id=row["batch_id"],line_id=line_id,container_id=row["container_id"],reason=payload.reason,performed_by=payload.performed_by)
    _refresh_batch_status(db,uuid.UUID(str(row["batch_id"]))); db.commit(); return {"ok":True}


@router.post("/batches/{batch_id}/writeoff")
def writeoff_batch(batch_id: uuid.UUID, payload: ScopedAction, db: Session = Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id); _ensure_schema(db)
    batch=db.execute(text("SELECT id,deleted_at FROM fc_pvz_batches WHERE id=:b AND client_id=:c AND warehouse_id=:w AND kind='DEFECT' FOR UPDATE"),{"b":str(batch_id),"c":str(payload.client_id),"w":str(payload.warehouse_id)}).mappings().first()
    if not batch: raise HTTPException(404,detail="Партия брака не найдена")
    if batch["deleted_at"]: raise HTTPException(409,detail="Партия уже удалена")
    total=Decimal(str(db.execute(text("SELECT COALESCE(SUM(GREATEST(quantity-written_off_qty,0)),0) FROM fc_pvz_lines WHERE batch_id=:b AND deleted_at IS NULL"),{"b":str(batch_id)}).scalar() or 0))
    if total<=0: raise HTTPException(409,detail="В этой партии уже нет товара на хранении")
    db.execute(text("UPDATE fc_pvz_lines SET written_off_qty=quantity WHERE batch_id=:b AND deleted_at IS NULL"),{"b":str(batch_id)})
    db.execute(text("UPDATE fc_pvz_batches SET status='WRITTEN_OFF' WHERE id=:b"),{"b":str(batch_id)})
    _audit(db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,action="WRITE_OFF_BATCH",quantity=total,batch_id=batch_id,reason=payload.reason,performed_by=payload.performed_by)
    db.commit(); return {"ok":True,"written_off":float(total)}


@router.post("/batches/{batch_id}/delete")
def delete_batch(batch_id: uuid.UUID, payload: ScopedAction, db: Session = Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id); _ensure_schema(db)
    batch=db.execute(text("SELECT id,deleted_at FROM fc_pvz_batches WHERE id=:b AND client_id=:c AND warehouse_id=:w AND kind='DEFECT' FOR UPDATE"),{"b":str(batch_id),"c":str(payload.client_id),"w":str(payload.warehouse_id)}).mappings().first()
    if not batch: raise HTTPException(404,detail="Партия брака не найдена")
    if batch["deleted_at"]: raise HTTPException(409,detail="Партия уже удалена")
    total=Decimal(str(db.execute(text("SELECT COALESCE(SUM(GREATEST(quantity-written_off_qty,0)),0) FROM fc_pvz_lines WHERE batch_id=:b AND deleted_at IS NULL"),{"b":str(batch_id)}).scalar() or 0))
    db.execute(text("UPDATE fc_pvz_lines SET deleted_at=now(),deleted_by=:p WHERE batch_id=:b AND deleted_at IS NULL"),{"p":payload.performed_by,"b":str(batch_id)})
    db.execute(text("UPDATE fc_pvz_batches SET status='DELETED',deleted_at=now(),deleted_by=:p WHERE id=:b"),{"p":payload.performed_by,"b":str(batch_id)})
    _audit(db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,action="DELETE_BATCH",quantity=total,batch_id=batch_id,reason=payload.reason,performed_by=payload.performed_by)
    db.commit(); return {"ok":True,"removed_from_defect_storage":float(total)}


@router.post("/boxes/{box_id}/archive")
def archive_box(box_id: uuid.UUID, payload: ScopedAction, db: Session = Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id); _ensure_schema(db)
    box=db.get(Container,box_id)
    if not box or box.client_id!=payload.client_id or box.warehouse_id!=payload.warehouse_id or str(box.container_type or '').upper()!=DEFECT_BOX_TYPE:
        raise HTTPException(404,detail="Короб брака не найден")
    remaining=Decimal(str(db.execute(text("""SELECT COALESCE(SUM(GREATEST(l.quantity-l.written_off_qty,0)),0)
      FROM fc_pvz_lines l JOIN fc_pvz_batches b ON b.id=l.batch_id
      WHERE l.container_id=:box AND l.deleted_at IS NULL AND b.deleted_at IS NULL"""),{"box":str(box_id)}).scalar() or 0))
    if remaining>0: raise HTTPException(409,detail="Сначала спишите или удалите товар из этого короба брака")
    box.status=DEFECT_ARCHIVED
    _audit(db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,action="ARCHIVE_BOX",container_id=box_id,reason=payload.reason,performed_by=payload.performed_by)
    db.commit(); return {"ok":True}


@router.get("/export.xlsx")
def export_xlsx(client_id: uuid.UUID, warehouse_id: uuid.UUID, db: Session = Depends(get_db)):
    scope(db,client_id,warehouse_id); _ensure_schema(db)
    rows=db.execute(text("""SELECT b.created_at,b.reference,b.performed_by,COALESCE(c.name,c.barcode,'Без короба') container,
      l.barcode,l.article,l.size,l.name,l.quantity,l.written_off_qty,GREATEST(l.quantity-l.written_off_qty,0) remaining,l.defect_reason,l.comment,b.status
      FROM fc_pvz_batches b JOIN fc_pvz_lines l ON l.batch_id=b.id LEFT JOIN containers c ON c.id=l.container_id
      WHERE b.client_id=:c AND b.warehouse_id=:w AND b.kind='DEFECT' AND b.deleted_at IS NULL AND l.deleted_at IS NULL
      ORDER BY b.created_at DESC"""),{"c":str(client_id),"w":str(warehouse_id)}).all()
    wb=Workbook(); ws=wb.active; ws.title="Брак · хранение"
    ws.append(["Дата","Партия / основание","Сотрудник","Короб","ШК","Артикул","Размер","Товар","Принято","Списано","Осталось","Причина брака","Комментарий","Статус"])
    for row in rows: append_safe(ws, row)
    for col in ws.columns:
        ws.column_dimensions[col[0].column_letter].width=min(42,max(12,max(len(str(x.value or '')) for x in col)+2))
    data=workbook_bytes(wb)
    return Response(data,media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":'attachment; filename="FulfillCore_defect_storage.xlsx"'})
