"""r145f52 browser-master manual receiving mirror and recovery.

The browser is the operator source of truth while a receipt is being worked on.
This router only mirrors the complete browser snapshot to the existing draft
rows and exposes robust recovery of an unfinished draft. Stock movement still
happens exclusively through the established final commit path.
"""
from __future__ import annotations

import hashlib
import time
import uuid
from collections import OrderedDict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.r145f25_places import scope

router = APIRouter(tags=["r145f52"])
MARKER = "FC_R145F52_BROWSER_MASTER_RECOVERY_CARD"


def _s(v, limit=512):
    return str(v or "").strip()[:limit]


def _fold(v):
    return _s(v).casefold()


def _lock(db: Session, key: str) -> None:
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


class Base52(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    preferred_receipt_id: uuid.UUID | None = None


class Row52(BaseModel):
    id: str | None = Field(default=None, max_length=96)
    barcode: str = Field(min_length=1, max_length=255)
    quantity: int = Field(ge=0, le=1_000_000)
    location_code: str = Field(min_length=1, max_length=255)
    container_code: str | None = Field(default=None, max_length=255)
    container_name: str | None = Field(default=None, max_length=255)
    article: str | None = Field(default=None, max_length=255)
    size: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=512)
    product_found: bool = True
    location_id: str | None = Field(default=None, max_length=36)
    container_id: str | None = Field(default=None, max_length=36)
    section: str | None = Field(default=None, max_length=80)
    aisle: str | None = Field(default=None, max_length=80)
    pallet: str | None = Field(default=None, max_length=120)
    storage_display: str | None = Field(default=None, max_length=512)
    duplicated_from: str | None = Field(default=None, max_length=255)
    product_id: str | None = Field(default=None, max_length=36)


class Reconcile52(Base52):
    browser_revision: int = Field(ge=0, le=2_147_483_647)
    browser_token: str = Field(min_length=1, max_length=96)
    rows: list[Row52] = Field(default_factory=list, max_length=5000)


def _receipt(db: Session, p: Base52, rid) -> dict | None:
    if not rid:
        return None
    row = db.execute(text("""
        SELECT * FROM fc_r145f27_manual_receipts
        WHERE id=:id AND client_id=:c AND warehouse_id=:w
        LIMIT 1
    """), {"id": str(rid), "c": str(p.client_id), "w": str(p.warehouse_id)}).mappings().first()
    return dict(row) if row else None


def _drafts(db: Session, p: Base52) -> list[dict]:
    scope(db, p.client_id, p.warehouse_id)
    rows = db.execute(text("""
        SELECT r.*,
               COALESCE(x.positions,0) AS positions,
               COALESCE(x.units,0) AS units
        FROM fc_r145f27_manual_receipts r
        LEFT JOIN (
          SELECT receipt_id, COUNT(*) FILTER (WHERE quantity>0 AND posted_at IS NULL) AS positions,
                 COALESCE(SUM(quantity) FILTER (WHERE quantity>0 AND posted_at IS NULL),0) AS units
          FROM fc_r145f27_manual_lines
          GROUP BY receipt_id
        ) x ON x.receipt_id=r.id
        WHERE r.client_id=:c AND r.warehouse_id=:w AND r.status='DRAFT'
        ORDER BY r.updated_at DESC, r.created_at DESC, r.id DESC
        LIMIT 20
    """), {"c": str(p.client_id), "w": str(p.warehouse_id)}).mappings().all()
    return [dict(x) for x in rows]


def _current_draft(db: Session, p: Base52) -> dict | None:
    rows = _drafts(db, p)
    worker = _s(p.worker, 255)
    exact = [r for r in rows if _fold(r.get("worker")) == _fold(worker)]
    if exact:
        # Prefer a non-empty exact-worker draft, then newest exact-worker draft.
        nonempty = [r for r in exact if int(r.get("units") or 0) > 0]
        return (nonempty or exact)[0]
    # Recovery fallback: if there is exactly one unfinished draft in this client/warehouse,
    # do not hide it merely because the worker field changed during earlier repairs.
    if len(rows) == 1:
        return rows[0]
    return None


def _ensure_draft(db: Session, p: Base52) -> dict:
    scope(db, p.client_id, p.warehouse_id)
    worker = _s(p.worker, 255)
    if not worker:
        raise HTTPException(422, detail="Укажите имя приёмщика")
    _lock(db, f"fc:r145f52:worker:{p.client_id}:{p.warehouse_id}:{worker.casefold()}")
    preferred = _receipt(db, p, p.preferred_receipt_id)
    if preferred and str(preferred.get("status") or "").upper() == "DRAFT":
        return preferred
    current = _current_draft(db, p)
    if current:
        return current
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
    row = _receipt(db, p, rid)
    if not row:
        raise HTTPException(500, detail="Не удалось создать черновик приёмки")
    return row


def _snapshot(db: Session, p: Base52, rid) -> dict:
    rec = _receipt(db, p, rid)
    if not rec:
        raise HTTPException(404, detail="Черновик приёмки не найден")
    rows = [dict(x) for x in db.execute(text("""
        SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND quantity>0 AND posted_at IS NULL
        ORDER BY created_at,id
    """), {"r": str(rid)}).mappings().all()]
    return {
        "id": str(rec["id"]), "client_id": str(rec["client_id"]), "warehouse_id": str(rec["warehouse_id"]),
        "actor": rec.get("actor") or "", "worker": rec.get("worker") or "", "reference": rec.get("reference") or "",
        "status": rec.get("status") or "DRAFT", "created_at": rec.get("created_at"), "updated_at": rec.get("updated_at"),
        "completed_at": rec.get("completed_at"), "rows": rows,
        "units": sum(int(x.get("quantity") or 0) for x in rows), "positions": len(rows),
        "saved_on_server": True, "compact": True, "r145f52": True,
    }


def _key(barcode: str, location: str, box: str | None) -> tuple[str, str, str]:
    return (_s(barcode, 255), _s(location, 255), _s(box, 255))


def _req_id(token: str, revision: int, key: tuple[str, str, str], after: int) -> str:
    raw = "|".join(key) + f"|{after}"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"fc52-{_s(token,32)}-{revision}-{h}"[:96]


@router.post("/r145f52/manual-drafts/current")
def current(payload: Base52, db: Session = Depends(get_db)):
    started = time.perf_counter()
    drafts = _drafts(db, payload)
    rec = _current_draft(db, payload)
    snap = _snapshot(db, payload, rec["id"]) if rec else None
    active = [{
        "id": str(r["id"]), "worker": r.get("worker") or "", "reference": r.get("reference") or "",
        "positions": int(r.get("positions") or 0), "units": int(r.get("units") or 0),
        "updated_at": r.get("updated_at"), "created_at": r.get("created_at"),
    } for r in drafts]
    return {"ok": True, "snapshot": snap, "active": active,
            "server_elapsed_ms": int((time.perf_counter()-started)*1000), "marker": MARKER}


@router.post("/r145f52/manual-drafts/{rid}/status")
def status(rid: uuid.UUID, payload: Base52, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    rec = _receipt(db, payload, rid)
    if not rec:
        return {"ok": True, "exists": False, "status": "MISSING", "marker": MARKER}
    return {"ok": True, "exists": True, "status": str(rec.get("status") or ""),
            "completed_at": rec.get("completed_at"), "marker": MARKER}


@router.post("/r145f52/manual-drafts/reconcile")
def reconcile(payload: Reconcile52, db: Session = Depends(get_db)):
    started = time.perf_counter()
    rec = _ensure_draft(db, payload)
    rid = str(rec["id"])
    _lock(db, f"fc:r145f52:receipt:{rid}")
    rec = _receipt(db, payload, rid)
    if not rec or str(rec.get("status") or "").upper() != "DRAFT":
        raise HTTPException(409, detail="Приёмка уже завершена")

    desired: OrderedDict[tuple[str, str, str], dict] = OrderedDict()
    for row in payload.rows:
        if row.quantity <= 0:
            continue
        k = _key(row.barcode, row.location_code, row.container_code or row.container_name)
        if not k[0] or not k[1]:
            continue
        row_data = row.model_dump() if hasattr(row, "model_dump") else row.dict()
        if k not in desired:
            desired[k] = row_data
        else:
            desired[k]["quantity"] = int(desired[k].get("quantity") or 0) + int(row.quantity or 0)
            for f, v in row_data.items():
                if f != "quantity" and v not in (None, ""):
                    desired[k][f] = v

    existing_rows = [dict(x) for x in db.execute(text("""
        SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND posted_at IS NULL
        ORDER BY created_at,id
    """), {"r": rid}).mappings().all()]
    existing: dict[tuple[str, str, str], list[dict]] = {}
    for row in existing_rows:
        existing.setdefault(_key(row.get("barcode"), row.get("location_code"), row.get("container_code")), []).append(row)

    changed = 0
    events = []
    for k, want in desired.items():
        candidates = existing.get(k) or []
        row = candidates[0] if candidates else None
        before = int(row.get("quantity") or 0) if row else 0
        after = int(want.get("quantity") or 0)
        vals = {
            "q": after, "a": _s(want.get("article"),255) or None, "z": _s(want.get("size"),255) or None,
            "n": _s(want.get("name"),512) or None, "pf": bool(want.get("product_found", True)),
            "lid": _s(want.get("location_id"),36) or None, "cid": _s(want.get("container_id"),36) or None,
            "cname": _s(want.get("container_name"),255) or (k[2] or None), "sec": _s(want.get("section"),80) or None,
            "aisle": _s(want.get("aisle"),80) or None, "pal": _s(want.get("pallet"),120) or None,
            "disp": _s(want.get("storage_display"),512) or None, "dup": _s(want.get("duplicated_from"),255) or None,
            "prod": _s(want.get("product_id"),36) or None,
        }
        if row:
            line_id = str(row["id"])
            db.execute(text("""UPDATE fc_r145f27_manual_lines SET quantity=:q,
              article=COALESCE(:a,article),size=COALESCE(:z,size),name=COALESCE(:n,name),product_found=:pf,
              location_id=COALESCE(:lid,location_id),container_id=COALESCE(:cid,container_id),
              container_name=COALESCE(:cname,container_name),section=COALESCE(:sec,section),
              aisle=COALESCE(:aisle,aisle),pallet=COALESCE(:pal,pallet),storage_display=COALESCE(:disp,storage_display),
              duplicated_from=COALESCE(:dup,duplicated_from),product_id=COALESCE(:prod,product_id),updated_at=CURRENT_TIMESTAMP
              WHERE id=:id AND receipt_id=:r"""), {**vals, "id": line_id, "r": rid})
            if before != after:
                changed += 1
        else:
            line_id = str(uuid.uuid4())
            db.execute(text("""INSERT INTO fc_r145f27_manual_lines
              (id,receipt_id,barcode,quantity,location_code,container_code,location_id,container_id,container_name,
               section,aisle,pallet,storage_display,duplicated_from,product_id,article,size,name,product_found)
              VALUES(:id,:r,:b,:q,:l,:box,:lid,:cid,:cname,:sec,:aisle,:pal,:disp,:dup,:prod,:a,:z,:n,:pf)"""),
              {**vals, "id": line_id, "r": rid, "b": k[0], "l": k[1], "box": k[2] or None})
            changed += 1
        for duplicate in candidates[1:]:
            did = str(duplicate["id"])
            if int(duplicate.get("quantity") or 0) != 0:
                db.execute(text("UPDATE fc_r145f27_manual_lines SET quantity=0,updated_at=CURRENT_TIMESTAMP WHERE id=:id AND receipt_id=:r"), {"id": did, "r": rid})
                changed += 1
        if before != after:
            events.append({"id": str(uuid.uuid4()), "r": rid, "line": line_id,
                           "req": _req_id(payload.browser_token, payload.browser_revision, k, after),
                           "b": k[0], "delta": after-before, "before": before, "after": after,
                           "l": k[1], "box": k[2] or None, "actor": _s(payload.worker,255)})

    for k, candidates in existing.items():
        if k in desired:
            continue
        for row in candidates:
            before = int(row.get("quantity") or 0)
            if before <= 0:
                continue
            line_id = str(row["id"])
            db.execute(text("UPDATE fc_r145f27_manual_lines SET quantity=0,updated_at=CURRENT_TIMESTAMP WHERE id=:id AND receipt_id=:r"), {"id": line_id, "r": rid})
            changed += 1
            events.append({"id": str(uuid.uuid4()), "r": rid, "line": line_id,
                           "req": _req_id(payload.browser_token, payload.browser_revision, k, 0),
                           "b": k[0], "delta": -before, "before": before, "after": 0,
                           "l": k[1], "box": k[2] or None, "actor": _s(payload.worker,255)})

    for ev in events:
        exists = db.execute(text("SELECT 1 FROM fc_r145f27_manual_events WHERE receipt_id=:r AND request_id=:q LIMIT 1"), {"r": rid, "q": ev["req"]}).first()
        if not exists:
            db.execute(text("""INSERT INTO fc_r145f27_manual_events
              (id,receipt_id,line_id,request_id,action,barcode,delta_qty,before_qty,after_qty,location_code,container_code,actor)
              VALUES(:id,:r,:line,:req,'ADJUST',:b,:delta,:before,:after,:l,:box,:actor)"""), ev)

    db.execute(text("""UPDATE fc_r145f27_manual_receipts
      SET reference=COALESCE(NULLIF(:ref,''),reference),worker=COALESCE(NULLIF(:worker,''),worker),updated_at=CURRENT_TIMESTAMP
      WHERE id=:id"""), {"ref": _s(payload.reference,255), "worker": _s(payload.worker,255), "id": rid})
    db.commit()
    snap = _snapshot(db, payload, rid)
    return {"ok": True, "target_receipt_id": rid, "browser_revision": payload.browser_revision,
            "changed_lines": changed, "snapshot": snap, "single_commit": True,
            "server_elapsed_ms": int((time.perf_counter()-started)*1000), "marker": MARKER}
