"""r145f41: bind local receiving queue to an exact receipt and dedupe retries across receipts.

Goals:
- a scan can never silently jump from a completed receipt into a new receipt;
- request_id is idempotent across all receipts in the same client/warehouse;
- the hot sync path performs no schema DDL and commits one batch once;
- a safe repair removes only exact cross-receipt retry duplicates from the current DRAFT.
"""
from __future__ import annotations

import uuid
from collections import OrderedDict, defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers import r145f30_receiving as fast
from app.routers import r145f19_finance as legacy
from app.services.r145f25_places import scope

router = APIRouter(tags=["r145f41"])


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
    client_created_at: int | None = None


class FastBatch41(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    receipt_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    events: list[BatchEvent] = Field(default_factory=list, max_length=100)
    want_snapshot: bool = True


class Worker41(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)


def _lock(db: Session, key: str) -> None:
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


def _receipt_exact(db: Session, payload: FastBatch41 | Worker41, rid: uuid.UUID):
    row = fast._receipt(db, rid, payload.client_id, payload.warehouse_id, True)
    if _s(row.get("worker")).casefold() != _s(payload.worker).casefold():
        raise HTTPException(409, detail="Эта приёмка принадлежит другому приёмщику")
    status = str(row.get("status") or "").upper()
    if status != "DRAFT":
        raise HTTPException(409, detail={
            "code": "RECEIPT_NOT_DRAFT",
            "message": "Эта приёмка уже закрыта или проводится. Старые локальные сканы в новую приёмку не переносятся.",
            "receipt_id": str(rid),
            "status": status or "UNKNOWN",
        })
    return row


def _events_by_request(db: Session, client_id, warehouse_id, request_ids: list[str]) -> dict[str, list[dict]]:
    if not request_ids:
        return {}
    params = {"c": str(client_id), "w": str(warehouse_id)}
    holders = []
    for i, req in enumerate(request_ids):
        k = f"q{i}"
        params[k] = req
        holders.append(f":{k}")
    rows = db.execute(text(
        "SELECT e.request_id,e.line_id,e.receipt_id,r.status,r.created_at,r.completed_at "
        "FROM fc_r145f27_manual_events e "
        "JOIN fc_r145f27_manual_receipts r ON r.id=e.receipt_id "
        "WHERE r.client_id=:c AND r.warehouse_id=:w "
        f"AND e.request_id IN ({','.join(holders)}) "
        "ORDER BY r.created_at,e.created_at,e.id"
    ), params).mappings().all()
    out: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        out[str(row["request_id"])].append(dict(row))
    return dict(out)


def _line_for_key(db: Session, rid: str, barcode: str, location_code: str, container_code: str | None):
    sql = """SELECT * FROM fc_r145f27_manual_lines
      WHERE receipt_id=:r AND posted_at IS NULL AND quantity>0 AND barcode=:b AND location_code=:l
        AND COALESCE(container_code,'')=COALESCE(:box,'') ORDER BY created_at,id LIMIT 1"""
    if db.bind.dialect.name == "postgresql":
        sql += " FOR UPDATE"
    return db.execute(text(sql), {
        "r": rid, "b": barcode, "l": location_code, "box": container_code,
    }).mappings().first()


@router.post("/r145f41/manual-drafts/sync-batch-fast")
def sync_batch_fast(payload: FastBatch41, request: Request, db: Session = Depends(get_db)):
    """Persist a queue batch only into the exact receipt_id supplied by the browser.

    Unlike r145f39, this route never resolves the destination by worker alone.
    The same request_id found in another receipt is acknowledged as an old retry
    and is not inserted again.  This closes the cross-receipt duplication hole.
    """
    scope(db, payload.client_id, payload.warehouse_id)
    rid = str(payload.receipt_id)
    _lock(db, f"fc:r145f41:receipt:{rid}")
    receipt = _receipt_exact(db, payload, payload.receipt_id)

    ordered: list[BatchEvent] = []
    seen: set[str] = set()
    for ev in payload.events[:100]:
        req = _s(ev.request_id, 96)
        if not req or req in seen:
            continue
        seen.add(req)
        ordered.append(ev)

    by_req = _events_by_request(
        db, payload.client_id, payload.warehouse_id,
        [_s(ev.request_id, 96) for ev in ordered],
    )
    acks: list[dict] = []
    failed: list[dict] = []
    fresh: list[BatchEvent] = []
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
                "request_id": req,
                "line_id": None,
                "duplicate": True,
                "duplicate_other_receipt": True,
                "previous_receipt_id": str(other.get("receipt_id") or ""),
                "previous_status": str(other.get("status") or ""),
            })
            continue
        fresh.append(ev)

    # Validate a physical place once per distinct location/box pair.
    place_cache: dict[tuple[str, str], tuple] = {}
    valid: list[tuple[BatchEvent, object, object]] = []
    for ev in fresh:
        req = _s(ev.request_id, 96)
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
            failed.append({"request_id": req, "status": exc.status_code, "detail": exc.detail})

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
            acks.append({"request_id": req, "line_id": line_id, "duplicate": False})
            running = nxt

    if valid:
        db.execute(text("UPDATE fc_r145f27_manual_receipts SET reference=COALESCE(NULLIF(:ref,''),reference),updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {
            "ref": _s(payload.reference, 255), "id": rid,
        })
    db.commit()

    snapshot = None
    if payload.want_snapshot:
        receipt = fast._receipt(db, payload.receipt_id, payload.client_id, payload.warehouse_id)
        snapshot = fast._compact_snapshot(db, receipt)

    return {
        "ok": True,
        "receipt_id": rid,
        "acks": acks,
        "failed": failed,
        "ignored_previous_receipt": ignored_previous,
        "snapshot": snapshot,
        "single_commit": True,
        "exact_receipt": True,
    }


@router.post("/r145f41/manual-drafts/{rid}/repair-cross-receipt")
def repair_cross_receipt(rid: uuid.UUID, payload: Worker41, request: Request, db: Session = Depends(get_db)):
    """Remove only provable retry duplicates already mixed into the current DRAFT.

    A current SCAN event is considered provably duplicated only when the exact
    same request_id exists in an older/finished different receipt.  Lines that
    have manual ADJUST/REMOVE activity are left untouched for administrator review.
    No warehouse stock operation is created here; only the unposted draft is repaired.
    """
    scope(db, payload.client_id, payload.warehouse_id)
    _lock(db, f"fc:r145f41:repair:{rid}")
    receipt = _receipt_exact(db, payload, rid)

    rows = db.execute(text("""
        SELECT e.id,e.line_id,e.request_id,COALESCE(e.delta_qty,0) AS delta_qty
        FROM fc_r145f27_manual_events e
        WHERE e.receipt_id=:rid AND e.action='SCAN' AND e.line_id IS NOT NULL
          AND EXISTS (
            SELECT 1
            FROM fc_r145f27_manual_events olde
            JOIN fc_r145f27_manual_receipts oldr ON oldr.id=olde.receipt_id
            WHERE olde.request_id=e.request_id AND olde.receipt_id<>:rid
              AND oldr.client_id=:c AND oldr.warehouse_id=:w
              AND (oldr.completed_at IS NOT NULL OR oldr.created_at < :cur_created)
          )
        ORDER BY e.created_at,e.id
    """), {
        "rid": str(rid), "c": str(payload.client_id), "w": str(payload.warehouse_id),
        "cur_created": receipt.get("created_at"),
    }).mappings().all()

    if not rows:
        return {
            "ok": True, "removed_events": 0, "removed_units": 0, "skipped_manual_lines": 0,
            "snapshot": fast._compact_snapshot(db, receipt),
        }

    line_ids = sorted({str(r["line_id"]) for r in rows if r.get("line_id")})
    manual_lines: set[str] = set()
    if line_ids:
        params = {"rid": str(rid)}
        holders = []
        for i, lid in enumerate(line_ids):
            k = f"l{i}"
            params[k] = lid
            holders.append(f":{k}")
        manual = db.execute(text(
            "SELECT DISTINCT line_id FROM fc_r145f27_manual_events "
            "WHERE receipt_id=:rid AND action<>'SCAN' "
            f"AND line_id IN ({','.join(holders)})"
        ), params).all()
        manual_lines = {str(x[0]) for x in manual}

    safe_rows = [r for r in rows if str(r.get("line_id")) not in manual_lines]
    by_line: dict[str, int] = defaultdict(int)
    event_ids: list[str] = []
    removed_units = 0
    for r in safe_rows:
        delta = max(0, int(r.get("delta_qty") or 0))
        by_line[str(r["line_id"])] += delta
        removed_units += delta
        event_ids.append(str(r["id"]))

    for line_id, delta in by_line.items():
        sql = "SELECT quantity FROM fc_r145f27_manual_lines WHERE id=:id AND receipt_id=:rid AND posted_at IS NULL"
        if db.bind.dialect.name == "postgresql":
            sql += " FOR UPDATE"
        current = db.execute(text(sql), {"id": line_id, "rid": str(rid)}).scalar()
        if current is None:
            continue
        new_qty = max(0, int(current or 0) - int(delta))
        db.execute(text("UPDATE fc_r145f27_manual_lines SET quantity=:q,updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {
            "q": new_qty, "id": line_id,
        })

    if event_ids:
        params = {}
        holders = []
        for i, eid in enumerate(event_ids):
            k = f"e{i}"
            params[k] = eid
            holders.append(f":{k}")
        db.execute(text(f"DELETE FROM fc_r145f27_manual_events WHERE id IN ({','.join(holders)})"), params)
        db.execute(text("UPDATE fc_r145f27_manual_receipts SET updated_at=CURRENT_TIMESTAMP WHERE id=:id"), {"id": str(rid)})
        db.commit()

    receipt = fast._receipt(db, rid, payload.client_id, payload.warehouse_id)
    return {
        "ok": True,
        "removed_events": len(event_ids),
        "removed_units": removed_units,
        "skipped_manual_lines": len(manual_lines),
        "snapshot": fast._compact_snapshot(db, receipt),
    }
