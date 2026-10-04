"""r145f50: stable fast manual receiving persistence.

Hot path rules:
- draft scans are persisted without SKU/place enrichment; final commit remains authoritative;
- one request writes up to 500 queued scans with one DB commit;
- request_id is de-duplicated across receipts in the same client/warehouse;
- manual quantity/remove is an exact server-side update and returns the authoritative snapshot;
- no stock movement happens in this router.
"""
from __future__ import annotations

import time
import uuid
from collections import OrderedDict, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.r145f25_places import scope

router = APIRouter(tags=["r145f50"])


def _s(v, limit=512):
    return str(v or "").strip()[:limit]


def _fold(v):
    return _s(v).casefold()


def _lock(db: Session, key: str) -> None:
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


class ScanEvent50(BaseModel):
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
    client_created_at: int | None = None


class Base50(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    preferred_receipt_id: uuid.UUID | None = None


class Sync50(Base50):
    events: list[ScanEvent50] = Field(default_factory=list, max_length=500)


class Edit50(Base50):
    request_id: str = Field(min_length=1, max_length=96)
    line_id: str | None = Field(default=None, max_length=96)
    barcode: str | None = Field(default=None, max_length=255)
    location_code: str | None = Field(default=None, max_length=255)
    container_code: str | None = Field(default=None, max_length=255)
    quantity: int = Field(ge=0, le=1000000)


def _receipt_row(db: Session, p: Base50, rid) -> dict | None:
    if not rid:
        return None
    row = db.execute(text("""
        SELECT * FROM fc_r145f27_manual_receipts
        WHERE id=:id AND client_id=:c AND warehouse_id=:w
        LIMIT 1
    """), {"id": str(rid), "c": str(p.client_id), "w": str(p.warehouse_id)}).mappings().first()
    return dict(row) if row else None


def _ensure_draft(db: Session, p: Base50) -> dict:
    scope(db, p.client_id, p.warehouse_id)
    worker = _s(p.worker, 255)
    if not worker:
        raise HTTPException(422, detail="Укажите имя приёмщика")
    _lock(db, f"fc:r145f50:worker:{p.client_id}:{p.warehouse_id}:{worker.casefold()}")

    preferred = _receipt_row(db, p, p.preferred_receipt_id)
    if preferred:
        if _fold(preferred.get("worker")) == _fold(worker) and str(preferred.get("status") or "").upper() == "DRAFT":
            return preferred
        # An explicitly bound local queue must never be silently merged into another old draft.
        # If its receipt is already closed, create a fresh receipt below.
        if p.preferred_receipt_id:
            preferred = None
            row = None
        else:
            row = None
    else:
        row = None

    if not p.preferred_receipt_id:
        row = db.execute(text("""
        SELECT * FROM fc_r145f27_manual_receipts
        WHERE client_id=:c AND warehouse_id=:w
          AND lower(COALESCE(worker,''))=lower(:worker)
          AND status='DRAFT'
        ORDER BY updated_at DESC, created_at DESC, id DESC
        LIMIT 1
    """), {"c": str(p.client_id), "w": str(p.warehouse_id), "worker": worker}).mappings().first()
        if row:
            return dict(row)

    rid = str(uuid.uuid4())
    db.execute(text("""
        INSERT INTO fc_r145f27_manual_receipts
          (id,client_id,warehouse_id,actor,worker,reference,status)
        VALUES(:id,:c,:w,:actor,:worker,:ref,'DRAFT')
    """), {
        "id": rid, "c": str(p.client_id), "w": str(p.warehouse_id),
        "actor": worker or "admin", "worker": worker, "ref": _s(p.reference, 255) or None,
    })
    db.flush()
    created = _receipt_row(db, p, rid)
    if not created:
        raise HTTPException(500, detail="Не удалось создать черновик приёмки")
    return created


def _exact_draft(db: Session, p: Base50, rid) -> dict:
    row = _receipt_row(db, p, rid)
    if not row:
        raise HTTPException(404, detail="Черновик приёмки не найден")
    if _fold(row.get("worker")) != _fold(p.worker):
        raise HTTPException(409, detail="Эта приёмка принадлежит другому приёмщику")
    status = str(row.get("status") or "").upper()
    if status != "DRAFT":
        raise HTTPException(409, detail={"code": "RECEIPT_NOT_DRAFT", "message": "Приёмка уже завершена", "status": status})
    return row


def _events_by_request(db: Session, p: Base50, request_ids: list[str]) -> dict[str, list[dict]]:
    if not request_ids:
        return {}
    params = {"c": str(p.client_id), "w": str(p.warehouse_id)}
    holders = []
    for i, req in enumerate(request_ids):
        k = f"q{i}"
        params[k] = req
        holders.append(f":{k}")
    rows = db.execute(text(
        "SELECT e.request_id,e.line_id,e.receipt_id,r.status "
        "FROM fc_r145f27_manual_events e "
        "JOIN fc_r145f27_manual_receipts r ON r.id=e.receipt_id "
        "WHERE r.client_id=:c AND r.warehouse_id=:w "
        f"AND e.request_id IN ({','.join(holders)}) "
        "ORDER BY e.created_at,e.id"
    ), params).mappings().all()
    out: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        out[str(row["request_id"])].append(dict(row))
    return dict(out)


def _line_by_key(db: Session, rid: str, barcode: str, location_code: str, container_code: str | None):
    sql = """SELECT * FROM fc_r145f27_manual_lines
      WHERE receipt_id=:r AND posted_at IS NULL AND barcode=:b AND location_code=:l
        AND COALESCE(container_code,'')=COALESCE(:box,'')
      ORDER BY created_at,id LIMIT 1"""
    if db.bind.dialect.name == "postgresql":
        sql += " FOR UPDATE"
    return db.execute(text(sql), {"r": rid, "b": barcode, "l": location_code, "box": container_code}).mappings().first()


def _snapshot(db: Session, p: Base50, rid) -> dict:
    receipt = _exact_draft(db, p, rid)
    rows = [dict(x) for x in db.execute(text("""
        SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND quantity>0 AND posted_at IS NULL
        ORDER BY created_at,id
    """), {"r": str(rid)}).mappings().all()]
    return {
        "id": str(receipt["id"]), "client_id": str(receipt["client_id"]), "warehouse_id": str(receipt["warehouse_id"]),
        "actor": receipt.get("actor") or "", "worker": receipt.get("worker") or "", "reference": receipt.get("reference") or "",
        "status": receipt.get("status") or "DRAFT", "created_at": receipt.get("created_at"), "updated_at": receipt.get("updated_at"),
        "completed_at": receipt.get("completed_at"), "rows": rows,
        "units": sum(int(x.get("quantity") or 0) for x in rows), "positions": len(rows),
        "saved_on_server": True, "compact": True, "r145f50": True,
    }


@router.post("/r145f50/manual-drafts/sync")
def sync_fast(payload: Sync50, request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    target = _ensure_draft(db, payload)
    rid = str(target["id"])
    _lock(db, f"fc:r145f50:receipt:{rid}")
    _exact_draft(db, payload, rid)

    ordered: list[ScanEvent50] = []
    seen: set[str] = set()
    for ev in payload.events[:500]:
        req = _s(ev.request_id, 96)
        if not req or req in seen:
            continue
        seen.add(req)
        ordered.append(ev)

    by_req = _events_by_request(db, payload, [_s(ev.request_id, 96) for ev in ordered])
    acks: list[dict] = []
    failed: list[dict] = []
    fresh: list[ScanEvent50] = []
    ignored_previous = 0
    for ev in ordered:
        req = _s(ev.request_id, 96)
        matches = by_req.get(req, [])
        here = next((x for x in matches if str(x.get("receipt_id")) == rid), None)
        if here:
            acks.append({"request_id": req, "line_id": str(here.get("line_id") or "") or None, "duplicate": True})
            continue
        other = next((x for x in matches if str(x.get("receipt_id")) != rid), None)
        if other:
            ignored_previous += 1
            acks.append({
                "request_id": req, "line_id": None, "duplicate": True,
                "duplicate_other_receipt": True, "previous_receipt_id": str(other.get("receipt_id") or ""),
            })
            continue
        fresh.append(ev)

    groups: OrderedDict[tuple[str, str, str], list[ScanEvent50]] = OrderedDict()
    for ev in fresh:
        req = _s(ev.request_id, 96)
        barcode = _s(ev.barcode, 255)
        loc = _s(ev.location_code, 255)
        box = _s(ev.container_code, 255)
        if not barcode or not loc:
            failed.append({"request_id": req, "status": 422, "detail": "Не указан ШК или место приёмки"})
            continue
        groups.setdefault((barcode, loc, box), []).append(ev)

    event_rows: list[dict] = []
    for (barcode, location_code, box_key), entries in groups.items():
        container_code = box_key or None
        line = _line_by_key(db, rid, barcode, location_code, container_code)
        before = int(line["quantity"] or 0) if line else 0
        delta = sum(max(1, int(ev.quantity or 1)) for ev in entries)
        after = before + delta
        last = entries[-1]
        vals = {
            "q": after, "a": _s(last.article, 255) or None, "z": _s(last.size, 255) or None,
            "n": _s(last.name, 512) or None, "pf": bool(last.product_found),
            "lid": _s(last.location_id, 36) or None, "cid": _s(last.container_id, 36) or None,
            "cname": _s(last.container_name, 255) or container_code,
            "sec": _s(last.section, 80) or None, "aisle": _s(last.aisle, 80) or None,
            "pal": _s(last.pallet, 120) or None, "disp": _s(last.storage_display, 512) or None,
            "dup": _s(last.duplicated_from, 255) or None, "prod": _s(last.product_id, 36) or None,
        }
        if line:
            line_id = str(line["id"])
            db.execute(text("""UPDATE fc_r145f27_manual_lines SET quantity=:q,
              article=COALESCE(:a,article),size=COALESCE(:z,size),name=COALESCE(:n,name),product_found=:pf,
              location_id=COALESCE(:lid,location_id),container_id=COALESCE(:cid,container_id),
              container_name=COALESCE(:cname,container_name),section=COALESCE(:sec,section),
              aisle=COALESCE(:aisle,aisle),pallet=COALESCE(:pal,pallet),storage_display=COALESCE(:disp,storage_display),
              duplicated_from=COALESCE(:dup,duplicated_from),product_id=COALESCE(:prod,product_id),updated_at=CURRENT_TIMESTAMP
              WHERE id=:id"""), {**vals, "id": line_id})
        else:
            line_id = str(uuid.uuid4())
            db.execute(text("""INSERT INTO fc_r145f27_manual_lines
              (id,receipt_id,barcode,quantity,location_code,container_code,location_id,container_id,container_name,
               section,aisle,pallet,storage_display,duplicated_from,product_id,article,size,name,product_found)
              VALUES(:id,:r,:b,:q,:l,:box,:lid,:cid,:cname,:sec,:aisle,:pal,:disp,:dup,:prod,:a,:z,:n,:pf)"""),
              {**vals, "id": line_id, "r": rid, "b": barcode, "l": location_code, "box": container_code})

        running = before
        for ev in entries:
            d = max(1, int(ev.quantity or 1))
            nxt = running + d
            req = _s(ev.request_id, 96)
            event_rows.append({
                "id": str(uuid.uuid4()), "r": rid, "line": line_id, "req": req, "b": barcode,
                "delta": d, "before": running, "after": nxt, "l": location_code,
                "box": container_code, "actor": _s(payload.worker, 255),
            })
            acks.append({"request_id": req, "line_id": line_id, "duplicate": False})
            running = nxt

    if event_rows:
        db.execute(text("""INSERT INTO fc_r145f27_manual_events
          (id,receipt_id,line_id,request_id,action,barcode,delta_qty,before_qty,after_qty,location_code,container_code,actor)
          VALUES(:id,:r,:line,:req,'SCAN',:b,:delta,:before,:after,:l,:box,:actor)"""), event_rows)
    db.execute(text("""UPDATE fc_r145f27_manual_receipts
      SET reference=COALESCE(NULLIF(:ref,''),reference),updated_at=CURRENT_TIMESTAMP WHERE id=:id"""),
      {"ref": _s(payload.reference, 255), "id": rid})
    db.commit()
    return {
        "ok": True, "target_receipt_id": rid, "acks": acks, "failed": failed,
        "ignored_previous_receipt": ignored_previous, "single_commit": True,
        "server_elapsed_ms": int(round((time.perf_counter() - started) * 1000)),
        "marker": "FC_R145F50_SYNC",
    }


@router.post("/r145f50/manual-drafts/{rid}/snapshot")
def snapshot_fast(rid: uuid.UUID, payload: Base50, request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    scope(db, payload.client_id, payload.warehouse_id)
    snap = _snapshot(db, payload, rid)
    return {"ok": True, "snapshot": snap, "server_elapsed_ms": int(round((time.perf_counter() - started) * 1000)), "marker": "FC_R145F50_SNAPSHOT"}


@router.post("/r145f50/manual-drafts/{rid}/line-set")
def line_set(rid: uuid.UUID, payload: Edit50, request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    scope(db, payload.client_id, payload.warehouse_id)
    _lock(db, f"fc:r145f50:receipt:{rid}")
    _exact_draft(db, payload, rid)

    existing = db.execute(text("""SELECT 1 FROM fc_r145f27_manual_events
      WHERE receipt_id=:r AND request_id=:req LIMIT 1"""), {"r": str(rid), "req": _s(payload.request_id, 96)}).first()
    if not existing:
        row = None
        if payload.line_id:
            row = db.execute(text("""SELECT * FROM fc_r145f27_manual_lines
              WHERE receipt_id=:r AND CAST(id AS text)=:id AND posted_at IS NULL LIMIT 1"""),
              {"r": str(rid), "id": _s(payload.line_id, 96)}).mappings().first()
        if not row and payload.barcode and payload.location_code:
            row = _line_by_key(db, str(rid), _s(payload.barcode, 255), _s(payload.location_code, 255), _s(payload.container_code, 255) or None)
        if not row:
            raise HTTPException(404, detail="Позиция не найдена на сервере. Дождитесь сохранения очереди и повторите.")
        before = max(0, int(row.get("quantity") or 0))
        after = max(0, int(payload.quantity or 0))
        line_id = str(row["id"])
        db.execute(text("UPDATE fc_r145f27_manual_lines SET quantity=:q,updated_at=CURRENT_TIMESTAMP WHERE id=:id AND receipt_id=:r"),
                   {"q": after, "id": line_id, "r": str(rid)})
        db.execute(text("""INSERT INTO fc_r145f27_manual_events
          (id,receipt_id,line_id,request_id,action,barcode,delta_qty,before_qty,after_qty,location_code,container_code,actor)
          VALUES(:id,:r,:line,:req,:action,:b,:delta,:before,:after,:l,:box,:actor)"""), {
            "id": str(uuid.uuid4()), "r": str(rid), "line": line_id, "req": _s(payload.request_id, 96),
            "action": "REMOVE" if after == 0 else "ADJUST", "b": _s(row.get("barcode"), 255),
            "delta": after - before, "before": before, "after": after,
            "l": _s(row.get("location_code"), 255), "box": _s(row.get("container_code"), 255) or None,
            "actor": _s(payload.worker, 255),
        })
        db.execute(text("UPDATE fc_r145f27_manual_receipts SET updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {"id": str(rid)})
        db.commit()

    snap = _snapshot(db, payload, rid)
    return {"ok": True, "snapshot": snap, "server_elapsed_ms": int(round((time.perf_counter() - started) * 1000)), "marker": "FC_R145F50_LINE_SET"}
