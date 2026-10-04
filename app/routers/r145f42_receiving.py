"""r145f42: safe receipt separation/recovery for manual receiving.

The route keeps the r145f41 exact-receipt queue contract and adds one bounded
repair step for a DRAFT that accidentally spans more than one work day.  It
never posts warehouse stock: it only rearranges unposted draft rows/events.
A stronger overlap repair is exposed separately and requires an explicit UI
confirmation because it uses a previous COMPLETED receipt as evidence.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers import r145f30_receiving as fast
from app.routers import r145f41_receiving as prev
from app.services.r145f25_places import scope

router = APIRouter(tags=["r145f42"])
MSK = ZoneInfo("Europe/Moscow")


def _s(v, limit=512):
    return str(v or "").strip()[:limit]


def _aware(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        d = v
    else:
        raw = str(v).strip().replace("Z", "+00:00")
        try:
            d = datetime.fromisoformat(raw)
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _today_start_utc():
    now = datetime.now(MSK)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _is_today(v, start):
    d = _aware(v)
    return bool(d and d >= start)


def _lock(db: Session, key: str):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


def _ensure_schema(db: Session):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("""CREATE TABLE IF NOT EXISTS fc_r145f42_receipt_repairs(
            receipt_id varchar(64) NOT NULL,
            previous_receipt_id varchar(64) NOT NULL,
            repair_kind varchar(64) NOT NULL,
            removed_units integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(receipt_id,previous_receipt_id,repair_kind)
        )"""))
    else:
        db.execute(text("""CREATE TABLE IF NOT EXISTS fc_r145f42_receipt_repairs(
            receipt_id varchar(64) NOT NULL,
            previous_receipt_id varchar(64) NOT NULL,
            repair_kind varchar(64) NOT NULL,
            removed_units integer NOT NULL DEFAULT 0,
            created_at timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(receipt_id,previous_receipt_id,repair_kind)
        )"""))
    db.flush()


class Prepare42(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    receipt_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    today_local_count: int = Field(default=0, ge=0, le=1000000)


class SplitOverlap42(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    previous_receipt_id: uuid.UUID


def _receipt_exact(db: Session, p: Prepare42 | SplitOverlap42, rid: uuid.UUID):
    r = fast._receipt(db, rid, p.client_id, p.warehouse_id, True)
    if _s(r.get("worker")).casefold() != _s(p.worker).casefold():
        raise HTTPException(409, detail="Эта приёмка принадлежит другому приёмщику")
    if str(r.get("status") or "").upper() != "DRAFT":
        raise HTTPException(409, detail="Текущая приёмка уже закрыта или проводится")
    return r


def _active_lines(db: Session, rid: str):
    return [dict(x) for x in db.execute(text("""SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND posted_at IS NULL AND quantity>0 ORDER BY created_at,id"""),
        {"r": rid}).mappings().all()]


def _scan_events(db: Session, rid: str):
    return [dict(x) for x in db.execute(text("""SELECT id,line_id,request_id,action,delta_qty,created_at
        FROM fc_r145f27_manual_events WHERE receipt_id=:r AND action='SCAN' AND line_id IS NOT NULL
        ORDER BY created_at,id"""), {"r": rid}).mappings().all()]


def _manual_line_ids(db: Session, rid: str):
    return {str(x[0]) for x in db.execute(text("""SELECT DISTINCT line_id FROM fc_r145f27_manual_events
        WHERE receipt_id=:r AND action<>'SCAN' AND line_id IS NOT NULL"""), {"r": rid}).all()}


def _day_plan(db: Session, rid: str):
    start = _today_start_utc()
    lines = _active_lines(db, rid)
    events = _scan_events(db, rid)
    manual = _manual_line_ids(db, rid)
    by_line = defaultdict(list)
    for e in events:
        by_line[str(e.get("line_id"))].append(e)
    plans = []
    old_total = today_total = 0
    for line in lines:
        lid = str(line["id"])
        qty = max(0, int(line.get("quantity") or 0))
        es = by_line.get(lid, [])
        old_ev = today_ev = 0
        today_ids = []
        for e in es:
            delta = max(0, int(e.get("delta_qty") or 0))
            if _is_today(e.get("created_at"), start):
                today_ev += delta
                today_ids.append(str(e["id"]))
            else:
                old_ev += delta
        total_ev = old_ev + today_ev
        residual = max(0, qty - total_ev)
        line_today = _is_today(line.get("created_at"), start)
        if lid in manual:
            # A manually edited line is indivisible without operator review.
            today_qty, old_qty = ((qty, 0) if line_today else (0, qty))
            today_ids = [str(e["id"]) for e in es] if line_today else []
        elif line_today:
            old_qty = min(qty, old_ev)
            today_qty = max(0, qty - old_qty)
        else:
            old_qty = min(qty, old_ev + residual)
            today_qty = max(0, qty - old_qty)
            today_qty = min(today_qty, today_ev)
        old_total += old_qty
        today_total += today_qty
        plans.append({"line": line, "old_qty": old_qty, "today_qty": today_qty, "today_event_ids": today_ids})
    return start, plans, old_total, today_total, len(manual)


def _copy_today_to_fresh(db: Session, receipt: dict, payload: Prepare42, request: Request, plans, reason: str):
    old_rid = str(receipt["id"])
    # Keep the split atomic.  The older helper commits while creating a fresh
    # receipt; doing that here could leave the old draft cancelled if a later
    # copy failed.  Create the replacement inside the same transaction instead.
    fast._cancel_worker_receipt(db, old_rid)
    new_rid = str(uuid.uuid4())
    db.execute(text("""INSERT INTO fc_r145f27_manual_receipts
        (id,client_id,warehouse_id,actor,worker,reference,status)
        VALUES(:id,:c,:w,:actor,:worker,:ref,'DRAFT')"""), {
        "id": new_rid, "c": str(payload.client_id), "w": str(payload.warehouse_id),
        "actor": _s(fast.actor(request)), "worker": _s(payload.worker),
        "ref": _s(payload.reference or receipt.get("reference") or ""),
    })
    db.flush()
    moved = 0
    moved_positions = 0
    for p in plans:
        qty = max(0, int(p.get("today_qty") or 0))
        if qty <= 0:
            continue
        row = p["line"]
        new_line = str(uuid.uuid4())
        db.execute(text("""INSERT INTO fc_r145f27_manual_lines
          (id,receipt_id,barcode,quantity,location_code,container_code,location_id,container_id,container_name,
           section,aisle,pallet,storage_display,duplicated_from,product_id,article,size,name,product_found)
          VALUES(:id,:r,:b,:q,:l,:box,:lid,:cid,:cname,:sec,:aisle,:pal,:disp,:dup,:prod,:a,:z,:n,:pf)"""), {
            "id": new_line, "r": new_rid, "b": row.get("barcode"), "q": qty,
            "l": row.get("location_code"), "box": row.get("container_code"),
            "lid": row.get("location_id"), "cid": row.get("container_id"), "cname": row.get("container_name"),
            "sec": row.get("section"), "aisle": row.get("aisle"), "pal": row.get("pallet"),
            "disp": row.get("storage_display"), "dup": row.get("duplicated_from"), "prod": row.get("product_id"),
            "a": row.get("article"), "z": row.get("size"), "n": row.get("name"),
            "pf": bool(row.get("product_found", True)),
        })
        db.execute(text("UPDATE fc_r145f27_manual_lines SET quantity=:q,updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {
            "q": max(0, int(p.get("old_qty") or 0)), "id": str(row["id"]),
        })
        ids = list(p.get("today_event_ids") or [])
        if ids:
            params = {"r": new_rid, "line": new_line}
            holders = []
            for i, eid in enumerate(ids):
                k = f"e{i}"; params[k] = eid; holders.append(f":{k}")
            db.execute(text(f"UPDATE fc_r145f27_manual_events SET receipt_id=:r,line_id=:line WHERE id IN ({','.join(holders)})"), params)
        moved += qty
        moved_positions += 1
    db.execute(text("UPDATE fc_r145f27_manual_receipts SET updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {"id": new_rid})
    db.commit()
    fresh = fast._receipt(db, new_rid, payload.client_id, payload.warehouse_id)
    return {
        "ok": True, "split": True, "reason": reason,
        "old_receipt_id": old_rid, "new_receipt_id": new_rid,
        "moved_units": moved, "moved_positions": moved_positions,
        "snapshot": fast._compact_snapshot(db, fresh),
    }


def _previous_overlap(db: Session, current: dict, worker: str):
    prev_r = db.execute(text("""SELECT * FROM fc_r145f27_manual_receipts
        WHERE client_id=:c AND warehouse_id=:w AND id<>:id
          AND lower(COALESCE(worker,''))=lower(:worker) AND status='COMPLETED'
        ORDER BY completed_at DESC,updated_at DESC LIMIT 1"""), {
        "c": str(current["client_id"]), "w": str(current["warehouse_id"]),
        "id": str(current["id"]), "worker": _s(worker),
    }).mappings().first()
    if not prev_r:
        return None
    current_rows = _active_lines(db, str(current["id"]))
    if not current_rows:
        return None
    old_rows = db.execute(text("""SELECT barcode,location_code,COALESCE(container_code,'') box,SUM(quantity) qty
        FROM fc_r145f27_manual_lines WHERE receipt_id=:r AND quantity>0
        GROUP BY barcode,location_code,COALESCE(container_code,'')"""), {"r": str(prev_r["id"])}).mappings().all()
    old = {(str(x.get("barcode") or ""), str(x.get("location_code") or ""), str(x.get("box") or "")): max(0, int(x.get("qty") or 0)) for x in old_rows}
    total = overlap = positions = 0
    for row in current_rows:
        qty = max(0, int(row.get("quantity") or 0)); total += qty
        key = (str(row.get("barcode") or ""), str(row.get("location_code") or ""), str(row.get("container_code") or ""))
        take = min(qty, old.get(key, 0))
        if take > 0:
            overlap += take; positions += 1
    ratio = (overlap / total) if total else 0.0
    candidate = bool(overlap >= 10 and positions >= 2 and ratio >= 0.35)
    return {
        "candidate": candidate, "previous_receipt_id": str(prev_r["id"]),
        "previous_completed_at": prev_r.get("completed_at"), "overlap_units": overlap,
        "overlap_positions": positions, "current_units": total, "ratio": ratio,
    }


@router.post("/r145f42/manual-drafts/{rid}/prepare-current")
def prepare_current(rid: uuid.UUID, payload: Prepare42, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    fast.install_schema(db)
    _ensure_schema(db)
    _lock(db, f"fc:r145f42:prepare:{rid}")
    receipt = _receipt_exact(db, payload, rid)

    # Exact request-id duplicates are always safe to remove first.
    try:
        rep = prev.repair_cross_receipt(rid, prev.Worker41(
            client_id=payload.client_id, warehouse_id=payload.warehouse_id, worker=payload.worker
        ), request, db)
        receipt = fast._receipt(db, rid, payload.client_id, payload.warehouse_id, True)
        exact_removed = int(rep.get("removed_units") or 0)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        receipt = _receipt_exact(db, payload, rid)
        exact_removed = 0

    start, plans, old_units, today_units, manual_lines = _day_plan(db, str(rid))
    receipt_created = _aware(receipt.get("created_at"))
    old_receipt = bool(receipt_created and receipt_created < start)

    if old_units > 0 and today_units > 0:
        out = _copy_today_to_fresh(db, receipt, payload, request, plans, "MIXED_DAYS")
        out.update({"old_units": old_units, "today_units": today_units, "exact_removed_units": exact_removed, "manual_lines": manual_lines})
        return out

    if old_units > 0 and today_units == 0 and old_receipt and payload.today_local_count > 0:
        # The server draft is entirely from a previous day while the browser has
        # today's local scans.  Close the stale draft and give those local scans a
        # brand-new receipt id before they can be uploaded.
        empty_plans = []
        out = _copy_today_to_fresh(db, receipt, payload, request, empty_plans, "OLD_DRAFT_PLUS_TODAY_LOCAL")
        out.update({"old_units": old_units, "today_units": 0, "exact_removed_units": exact_removed, "manual_lines": manual_lines})
        return out

    current = fast._receipt(db, rid, payload.client_id, payload.warehouse_id)
    snap = fast._compact_snapshot(db, current)
    overlap = _previous_overlap(db, current, payload.worker)
    return {
        "ok": True, "split": False, "snapshot": snap,
        "old_units": old_units, "today_units": today_units,
        "exact_removed_units": exact_removed, "manual_lines": manual_lines,
        "overlap": overlap,
    }


@router.post("/r145f42/manual-drafts/{rid}/split-overlap")
def split_overlap(rid: uuid.UUID, payload: SplitOverlap42, request: Request, db: Session = Depends(get_db)):
    """Explicit recovery when a finished receipt was replayed into the current DRAFT.

    The UI asks the operator for confirmation.  Only quantities that match the
    exact barcode + address + box of the selected previous COMPLETED receipt are
    subtracted.  No stock operation is made because those units are already in
    the previous completed receipt.
    """
    scope(db, payload.client_id, payload.warehouse_id)
    fast.install_schema(db)
    _ensure_schema(db)
    _lock(db, f"fc:r145f42:overlap:{rid}")
    current = _receipt_exact(db, payload, rid)
    old = fast._receipt(db, payload.previous_receipt_id, payload.client_id, payload.warehouse_id, False)
    if str(old.get("status") or "").upper() != "COMPLETED":
        raise HTTPException(409, detail="Предыдущая приёмка не имеет статуса COMPLETED")
    if _s(old.get("worker")).casefold() != _s(payload.worker).casefold():
        raise HTTPException(409, detail="Предыдущая приёмка относится к другому приёмщику")
    marker = db.execute(text("""SELECT removed_units FROM fc_r145f42_receipt_repairs
        WHERE receipt_id=:r AND previous_receipt_id=:p AND repair_kind='OVERLAP'"""), {
        "r": str(rid), "p": str(payload.previous_receipt_id),
    }).first()
    if marker:
        return {"ok": True, "already_repaired": True, "removed_units": int(marker[0] or 0), "snapshot": fast._compact_snapshot(db, current)}

    old_rows = db.execute(text("""SELECT barcode,location_code,COALESCE(container_code,'') box,SUM(quantity) qty
        FROM fc_r145f27_manual_lines WHERE receipt_id=:r AND quantity>0
        GROUP BY barcode,location_code,COALESCE(container_code,'')"""), {"r": str(payload.previous_receipt_id)}).mappings().all()
    remaining = {(str(x.get("barcode") or ""), str(x.get("location_code") or ""), str(x.get("box") or "")): max(0, int(x.get("qty") or 0)) for x in old_rows}
    removed = positions = 0
    for row in _active_lines(db, str(rid)):
        key = (str(row.get("barcode") or ""), str(row.get("location_code") or ""), str(row.get("container_code") or ""))
        avail = remaining.get(key, 0)
        if avail <= 0:
            continue
        qty = max(0, int(row.get("quantity") or 0))
        take = min(qty, avail)
        if take <= 0:
            continue
        db.execute(text("UPDATE fc_r145f27_manual_lines SET quantity=:q,updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {
            "q": qty - take, "id": str(row["id"]),
        })
        remaining[key] = avail - take
        removed += take; positions += 1
    if removed <= 0:
        raise HTTPException(409, detail="Совпадающая часть уже отсутствует в текущем черновике")
    db.execute(text("""INSERT INTO fc_r145f42_receipt_repairs(receipt_id,previous_receipt_id,repair_kind,removed_units)
        VALUES(:r,:p,'OVERLAP',:q)"""), {"r": str(rid), "p": str(payload.previous_receipt_id), "q": removed})
    db.execute(text("UPDATE fc_r145f27_manual_receipts SET updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {"id": str(rid)})
    db.commit()
    current = fast._receipt(db, rid, payload.client_id, payload.warehouse_id)
    return {
        "ok": True, "removed_units": removed, "removed_positions": positions,
        "previous_receipt_id": str(payload.previous_receipt_id),
        "snapshot": fast._compact_snapshot(db, current),
    }
