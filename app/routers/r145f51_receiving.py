"""r145f51 browser-first manual receiving mirror.

The browser draft is the operator source of truth. This router mirrors the complete
browser snapshot to the existing manual receiving tables in one transaction.
No stock movement happens here; the normal final commit remains authoritative.
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

router = APIRouter(tags=["r145f51"])


def _s(v, limit=512):
    return str(v or "").strip()[:limit]


def _fold(v):
    return _s(v).casefold()


def _lock(db: Session, key: str) -> None:
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


class Base51(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    preferred_receipt_id: uuid.UUID | None = None


class Row51(BaseModel):
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


class Reconcile51(Base51):
    browser_revision: int = Field(ge=0, le=2_147_483_647)
    browser_token: str = Field(min_length=1, max_length=96)
    rows: list[Row51] = Field(default_factory=list, max_length=5000)


def _receipt(db: Session, p: Base51, rid) -> dict | None:
    if not rid:
        return None
    row = db.execute(text("""
        SELECT * FROM fc_r145f27_manual_receipts
        WHERE id=:id AND client_id=:c AND warehouse_id=:w
        LIMIT 1
    """), {"id": str(rid), "c": str(p.client_id), "w": str(p.warehouse_id)}).mappings().first()
    return dict(row) if row else None


def _current_draft(db: Session, p: Base51) -> dict | None:
    scope(db, p.client_id, p.warehouse_id)
    worker = _s(p.worker, 255)
    if not worker:
        raise HTTPException(422, detail="Укажите имя приёмщика")
    row = db.execute(text("""
        SELECT * FROM fc_r145f27_manual_receipts
        WHERE client_id=:c AND warehouse_id=:w
          AND lower(COALESCE(worker,''))=lower(:worker)
          AND status='DRAFT'
        ORDER BY updated_at DESC, created_at DESC, id DESC
        LIMIT 1
    """), {"c": str(p.client_id), "w": str(p.warehouse_id), "worker": worker}).mappings().first()
    return dict(row) if row else None


def _ensure_draft(db: Session, p: Base51) -> dict:
    scope(db, p.client_id, p.warehouse_id)
    worker = _s(p.worker, 255)
    if not worker:
        raise HTTPException(422, detail="Укажите имя приёмщика")
    _lock(db, f"fc:r145f51:worker:{p.client_id}:{p.warehouse_id}:{worker.casefold()}")
    preferred = _receipt(db, p, p.preferred_receipt_id)
    if preferred and _fold(preferred.get("worker")) == _fold(worker) and str(preferred.get("status") or "").upper() == "DRAFT":
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


def _snapshot(db: Session, p: Base51, rid) -> dict:
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
        "saved_on_server": True, "compact": True, "r145f51": True,
    }


def _key(barcode: str, location: str, box: str | None) -> tuple[str, str, str]:
    return (_s(barcode, 255), _s(location, 255), _s(box, 255))


def _req_id(token: str, revision: int, key: tuple[str, str, str], after: int) -> str:
    raw = "|".join(key) + f"|{after}"
    h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"fc51-{_s(token,32)}-{revision}-{h}"[:96]


@router.post("/r145f51/manual-drafts/current")
def current(payload: Base51, db: Session = Depends(get_db)):
    started = time.perf_counter()
    rec = _current_draft(db, payload)
    snap = _snapshot(db, payload, rec["id"]) if rec else None
    return {"ok": True, "snapshot": snap, "server_elapsed_ms": int((time.perf_counter()-started)*1000), "marker": "FC_R145F51_CURRENT"}


@router.post("/r145f51/manual-drafts/{rid}/status")
def status(rid: uuid.UUID, payload: Base51, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    rec = _receipt(db, payload, rid)
    if not rec:
        return {"ok": True, "exists": False, "status": "MISSING", "marker": "FC_R145F51_STATUS"}
    return {"ok": True, "exists": True, "status": str(rec.get("status") or ""), "completed_at": rec.get("completed_at"), "marker": "FC_R145F51_STATUS"}


@router.post("/r145f51/manual-drafts/reconcile")
def reconcile(payload: Reconcile51, db: Session = Depends(get_db)):
    started = time.perf_counter()
    rec = _ensure_draft(db, payload)
    rid = str(rec["id"])
    _lock(db, f"fc:r145f51:receipt:{rid}")
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
            # Keep the newest non-empty metadata.
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
    event_rows = []
    touched_ids: set[str] = set()
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
            line_id = str(row["id"]); touched_ids.add(line_id)
            if before != after or any([
                vals["a"] and vals["a"] != row.get("article"), vals["z"] and vals["z"] != row.get("size"),
                vals["n"] and vals["n"] != row.get("name"), vals["cname"] and vals["cname"] != row.get("container_name")
            ]):
                db.execute(text("""UPDATE fc_r145f27_manual_lines SET quantity=:q,
                  article=COALESCE(:a,article),size=COALESCE(:z,size),name=COALESCE(:n,name),product_found=:pf,
                  location_id=COALESCE(:lid,location_id),container_id=COALESCE(:cid,container_id),
                  container_name=COALESCE(:cname,container_name),section=COALESCE(:sec,section),
                  aisle=COALESCE(:aisle,aisle),pallet=COALESCE(:pal,pallet),storage_display=COALESCE(:disp,storage_display),
                  duplicated_from=COALESCE(:dup,duplicated_from),product_id=COALESCE(:prod,product_id),updated_at=CURRENT_TIMESTAMP
                  WHERE id=:id AND receipt_id=:r"""), {**vals, "id": line_id, "r": rid})
                changed += 1
        else:
            line_id = str(uuid.uuid4()); touched_ids.add(line_id)
            db.execute(text("""INSERT INTO fc_r145f27_manual_lines
              (id,receipt_id,barcode,quantity,location_code,container_code,location_id,container_id,container_name,
               section,aisle,pallet,storage_display,duplicated_from,product_id,article,size,name,product_found)
              VALUES(:id,:r,:b,:q,:l,:box,:lid,:cid,:cname,:sec,:aisle,:pal,:disp,:dup,:prod,:a,:z,:n,:pf)"""),
              {**vals, "id": line_id, "r": rid, "b": k[0], "l": k[1], "box": k[2] or None})
            changed += 1
        # Any duplicate unposted lines for the same physical key are neutralized.
        for duplicate in candidates[1:]:
            did = str(duplicate["id"]); touched_ids.add(did)
            if int(duplicate.get("quantity") or 0) != 0:
                db.execute(text("UPDATE fc_r145f27_manual_lines SET quantity=0,updated_at=CURRENT_TIMESTAMP WHERE id=:id AND receipt_id=:r"), {"id": did, "r": rid})
                changed += 1
        if before != after:
            event_rows.append({
                "id": str(uuid.uuid4()), "r": rid, "line": line_id,
                "req": _req_id(payload.browser_token, payload.browser_revision, k, after),
                "b": k[0], "delta": after-before, "before": before, "after": after,
                "l": k[1], "box": k[2] or None, "actor": _s(payload.worker,255),
            })

    # Rows absent from the browser draft are removed from the active draft on the server.
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
            event_rows.append({
                "id": str(uuid.uuid4()), "r": rid, "line": line_id,
                "req": _req_id(payload.browser_token, payload.browser_revision, k, 0),
                "b": k[0], "delta": -before, "before": before, "after": 0,
                "l": k[1], "box": k[2] or None, "actor": _s(payload.worker,255),
            })

    # Audit changes, but make retries of the same browser revision idempotent.
    for ev in event_rows:
        exists = db.execute(text("SELECT 1 FROM fc_r145f27_manual_events WHERE receipt_id=:r AND request_id=:q LIMIT 1"), {"r": rid, "q": ev["req"]}).first()
        if exists:
            continue
        db.execute(text("""INSERT INTO fc_r145f27_manual_events
          (id,receipt_id,line_id,request_id,action,barcode,delta_qty,before_qty,after_qty,location_code,container_code,actor)
          VALUES(:id,:r,:line,:req,'ADJUST',:b,:delta,:before,:after,:l,:box,:actor)"""), ev)

    db.execute(text("""UPDATE fc_r145f27_manual_receipts
      SET reference=COALESCE(NULLIF(:ref,''),reference),updated_at=CURRENT_TIMESTAMP WHERE id=:id"""),
      {"ref": _s(payload.reference,255), "id": rid})
    db.commit()
    snap = _snapshot(db, payload, rid)
    return {
        "ok": True, "target_receipt_id": rid, "browser_revision": payload.browser_revision,
        "changed_lines": changed, "snapshot": snap, "single_commit": True,
        "server_elapsed_ms": int((time.perf_counter()-started)*1000), "marker": "FC_R145F51_RECONCILE",
    }
