"""r145f45: hot-path manual receiving state + sync.

The scanner must never wait for the old normalize/full snapshot path.  This
router provides:
- a lightweight worker receipt state endpoint with a raw draft snapshot;
- acknowledgement reconciliation for browser queue request_ids;
- a set-based batch sync endpoint for up to 500 scans;
- no schema DDL and no catalog/SKU enrichment on the hot path.

Warehouse stock still moves only through the existing exact final commit flow.
"""
from __future__ import annotations

from collections import OrderedDict
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers import r145f19_finance as legacy
from app.routers import r145f30_receiving as fast
from app.routers import r145f41_receiving as r41
from app.services.r145f25_places import scope

router = APIRouter(tags=["r145f45"])


def _s(v, limit=512):
    return str(v or "").strip()[:limit]


def _fold(v):
    return _s(v).casefold()


class State45(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    receipt_id: uuid.UUID | None = None
    request_ids: list[str] = Field(default_factory=list, max_length=1200)
    include_rows: bool = True


class Sync45(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    receipt_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    events: list[r41.BatchEvent] = Field(default_factory=list, max_length=500)


def _worker_receipt_hot(db: Session, payload: State45, request: Request) -> dict:
    """Find the current worker receipt without install_schema/full snapshot."""
    if payload.receipt_id:
        try:
            r = fast._receipt(db, payload.receipt_id, payload.client_id, payload.warehouse_id)
            if _fold(r.get("worker")) == _fold(payload.worker) and str(r.get("status") or "").upper() in {"DRAFT", "POSTING"}:
                return r
        except HTTPException:
            pass

    row = db.execute(text("""
        SELECT * FROM fc_r145f27_manual_receipts
        WHERE client_id=:c AND warehouse_id=:w
          AND lower(COALESCE(worker,''))=lower(:worker)
          AND status IN ('DRAFT','POSTING')
        ORDER BY updated_at DESC,id DESC LIMIT 1
    """), {
        "c": str(payload.client_id), "w": str(payload.warehouse_id), "worker": _s(payload.worker, 255),
    }).mappings().first()
    if row:
        return dict(row)

    # No active receipt: serialize creation only for this worker, re-check,
    # then create a fresh blank directly.  Do NOT call _worker_receipt here:
    # that legacy helper normalizes historical drafts and is exactly the slow
    # path r145f45 removes from interactive receiving.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
            "k": f"fc:r145f45:worker-open:{payload.client_id}:{payload.warehouse_id}:{_fold(payload.worker)}"
        })
        row = db.execute(text("""
            SELECT * FROM fc_r145f27_manual_receipts
            WHERE client_id=:c AND warehouse_id=:w
              AND lower(COALESCE(worker,''))=lower(:worker)
              AND status IN ('DRAFT','POSTING')
            ORDER BY updated_at DESC,id DESC LIMIT 1
        """), {
            "c": str(payload.client_id), "w": str(payload.warehouse_id), "worker": _s(payload.worker, 255),
        }).mappings().first()
        if row:
            return dict(row)
    row = fast._create_fresh_worker_receipt(
        db, payload.client_id, payload.warehouse_id, payload.worker,
        fast.actor(request), reference=payload.reference or "",
    )
    return dict(row)


def _raw_snapshot(db: Session, receipt: dict, include_rows: bool = True) -> dict:
    rows: list[dict] = []
    if include_rows:
        rows = [dict(x) for x in db.execute(text("""
            SELECT * FROM fc_r145f27_manual_lines
            WHERE receipt_id=:r AND quantity>0
            ORDER BY created_at,id
        """), {"r": str(receipt["id"])}).mappings().all()]
    active = [x for x in rows if not x.get("posted_at") and int(x.get("quantity") or 0) > 0]
    return {
        "id": str(receipt["id"]),
        "client_id": str(receipt["client_id"]),
        "warehouse_id": str(receipt["warehouse_id"]),
        "actor": receipt.get("actor") or "",
        "worker": receipt.get("worker") or "",
        "reference": receipt.get("reference") or "",
        "status": receipt.get("status") or "DRAFT",
        "created_at": receipt.get("created_at"),
        "updated_at": receipt.get("updated_at"),
        "completed_at": receipt.get("completed_at"),
        "rows": rows,
        "units": sum(int(x.get("quantity") or 0) for x in active),
        "positions": len(active),
        "saved_on_server": True,
        "compact": True,
        "hot_fast": True,
    }


def _acks_for_requests(db: Session, payload: State45) -> list[dict]:
    ids = []
    seen = set()
    for value in payload.request_ids[:1200]:
        v = _s(value, 96)
        if v and v not in seen:
            seen.add(v)
            ids.append(v)
    if not ids:
        return []
    params = {"c": str(payload.client_id), "w": str(payload.warehouse_id)}
    holders = []
    for i, req in enumerate(ids):
        key = f"q{i}"
        params[key] = req
        holders.append(f":{key}")
    rows = db.execute(text(
        "SELECT e.request_id,e.receipt_id,e.line_id,r.status "
        "FROM fc_r145f27_manual_events e "
        "JOIN fc_r145f27_manual_receipts r ON r.id=e.receipt_id "
        "WHERE r.client_id=:c AND r.warehouse_id=:w "
        f"AND e.request_id IN ({','.join(holders)}) "
        "ORDER BY e.created_at,e.id"
    ), params).mappings().all()
    out = []
    used = set()
    for row in rows:
        req = _s(row.get("request_id"), 96)
        if not req or req in used:
            continue
        used.add(req)
        out.append({
            "request_id": req,
            "receipt_id": str(row.get("receipt_id") or ""),
            "line_id": str(row.get("line_id") or "") or None,
            "status": str(row.get("status") or ""),
        })
    return out


@router.post("/r145f45/manual-drafts/state-fast")
def state_fast(payload: State45, request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    scope(db, payload.client_id, payload.warehouse_id)
    receipt = _worker_receipt_hot(db, payload, request)
    acks = _acks_for_requests(db, payload)
    snap = _raw_snapshot(db, receipt, payload.include_rows)
    return {
        "ok": True,
        "receipt_id": str(receipt["id"]),
        "snapshot": snap,
        "acked": acks,
        "server_elapsed_ms": int(round((time.perf_counter() - started) * 1000)),
        "marker": "FC_R145F45_FAST_STATE",
    }


def _line_maps(rows):
    by_ids = {}
    by_codes = {}
    for row in rows:
        barcode = _s(row.get("barcode"), 255)
        lid = _s(row.get("location_id"), 36)
        cid = _s(row.get("container_id"), 36)
        loc_code = _fold(row.get("location_code"))
        aliases = {_fold(row.get("container_code")), _fold(row.get("container_name"))} - {""}
        if barcode and lid:
            by_ids.setdefault((barcode, lid, cid), row)
        if barcode and loc_code:
            if aliases:
                for alias in aliases:
                    by_codes.setdefault((barcode, loc_code, alias), row)
            else:
                by_codes.setdefault((barcode, loc_code, ""), row)
    return by_ids, by_codes


@router.post("/r145f45/manual-drafts/sync-batch-hot")
def sync_batch_hot(payload: Sync45, request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    scope(db, payload.client_id, payload.warehouse_id)
    rid = str(payload.receipt_id)

    # The row lock serializes writers to this exact receipt without blocking all
    # other workers in the same warehouse.
    receipt = fast._receipt(db, payload.receipt_id, payload.client_id, payload.warehouse_id, True)
    if _fold(receipt.get("worker")) != _fold(payload.worker):
        raise HTTPException(409, detail="Эта приёмка принадлежит другому приёмщику")
    status = str(receipt.get("status") or "").upper()
    if status != "DRAFT":
        raise HTTPException(409, detail={
            "code": "RECEIPT_NOT_DRAFT",
            "message": "Эта приёмка уже закрыта или проводится. Локальные сканы не переносятся в другую приёмку автоматически.",
            "receipt_id": rid,
            "status": status or "UNKNOWN",
        })

    ordered = []
    seen = set()
    for ev in payload.events[:500]:
        req = _s(ev.request_id, 96)
        if not req or req in seen:
            continue
        seen.add(req)
        ordered.append(ev)

    by_req = r41._events_by_request(
        db, payload.client_id, payload.warehouse_id,
        [_s(ev.request_id, 96) for ev in ordered],
    )
    acks = []
    failed = []
    fresh = []
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

    place_cache = {}
    valid = []
    for ev in fresh:
        req = _s(ev.request_id, 96)
        loc_code = _s(ev.location_code, 255)
        box_code = _s(ev.container_code, 255) or None
        pkey = (_fold(loc_code), _fold(box_code))
        try:
            if pkey not in place_cache:
                place_cache[pkey] = legacy.validate_place(
                    db, payload.client_id, payload.warehouse_id, loc_code, box_code
                )
            loc, cont = place_cache[pkey]
            valid.append((ev, loc, cont))
        except HTTPException as exc:
            failed.append({"request_id": req, "status": exc.status_code, "detail": exc.detail})

    sql = """SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND posted_at IS NULL AND quantity>0
        ORDER BY created_at,id"""
    if db.bind.dialect.name == "postgresql":
        sql += " FOR UPDATE"
    current_rows = [dict(x) for x in db.execute(text(sql), {"r": rid}).mappings().all()]
    by_ids, by_codes = _line_maps(current_rows)

    groups = OrderedDict()
    for ev, loc, cont in valid:
        barcode = _s(ev.barcode, 255)
        loc_id = str(getattr(loc, "id", "") or "")
        cont_id = str(getattr(cont, "id", "") or "") if cont else ""
        groups.setdefault((barcode, loc_id, cont_id), []).append((ev, loc, cont))

    updates = []
    inserts = []
    event_inserts = []
    meta_rows = []
    line_summaries = []
    accepted_units = 0

    for (barcode, loc_id, cont_id), entries in groups.items():
        last, loc, cont = entries[-1]
        canonical_loc = _s(getattr(loc, "code", None), 255) or _s(last.location_code, 255)
        canonical_box = None
        if cont:
            canonical_box = _s(getattr(cont, "barcode", None), 255) or _s(getattr(cont, "name", None), 255) or _s(last.container_code, 255) or None

        line = by_ids.get((barcode, loc_id, cont_id))
        if not line:
            aliases = [canonical_box, getattr(cont, "name", None) if cont else None,
                       getattr(cont, "barcode", None) if cont else None,
                       last.container_code, last.container_name]
            for alias in aliases:
                line = by_codes.get((barcode, _fold(canonical_loc), _fold(alias)))
                if line:
                    break

        before = int(line.get("quantity") or 0) if line else 0
        total_delta = sum(int(ev.quantity or 1) for ev, _loc, _cont in entries)
        accepted_units += total_delta
        after = before + total_delta
        line_id = str(line.get("id")) if line else str(uuid.uuid4())
        cname = (_s(getattr(cont, "name", None), 255) if cont else None) or canonical_box
        values = {
            "id": line_id, "r": rid, "b": barcode, "q": after,
            "l": canonical_loc, "box": canonical_box,
            "lid": loc_id or (_s(last.location_id, 36) or None),
            "cid": cont_id or None, "cname": cname,
            "a": _s(last.article, 255) or None, "z": _s(last.size, 255) or None,
            "n": _s(last.name, 512) or None, "pf": bool(last.product_found),
            "sec": _s(last.section, 80) or None, "aisle": _s(last.aisle, 80) or None,
            "pal": _s(last.pallet, 120) or None, "disp": _s(last.storage_display, 512) or None,
            "dup": _s(last.duplicated_from, 255) or None, "prod": _s(last.product_id, 36) or None,
        }
        (updates if line else inserts).append(values)

        running = before
        for ev, _loc, _cont in entries:
            delta = int(ev.quantity or 1)
            nxt = running + delta
            req = _s(ev.request_id, 96)
            event_inserts.append({
                "id": str(uuid.uuid4()), "r": rid, "line": line_id, "req": req,
                "b": barcode, "delta": delta, "before": running, "after": nxt,
                "l": canonical_loc, "box": canonical_box, "actor": _s(payload.worker, 255),
            })
            meta_rows.append({
                "request_id": req, "receipt_id": rid,
                "client_created_at": int(ev.client_created_at) if ev.client_created_at is not None else None,
            })
            acks.append({"request_id": req, "line_id": line_id, "duplicate": False})
            running = nxt

        line_summaries.append({
            "line_id": line_id, "barcode": barcode, "quantity": after,
            "location_code": canonical_loc, "container_code": canonical_box,
            "container_id": cont_id or None,
        })

    if updates:
        db.execute(text("""UPDATE fc_r145f27_manual_lines SET
            quantity=:q,location_code=:l,container_code=:box,location_id=:lid,
            container_id=:cid,container_name=:cname,article=:a,size=:z,name=:n,
            product_found=:pf,section=:sec,aisle=:aisle,pallet=:pal,
            storage_display=:disp,duplicated_from=:dup,product_id=:prod,
            updated_at=CURRENT_TIMESTAMP WHERE id=:id AND receipt_id=:r"""), updates)
    if inserts:
        db.execute(text("""INSERT INTO fc_r145f27_manual_lines
            (id,receipt_id,barcode,quantity,location_code,container_code,location_id,container_id,container_name,
             section,aisle,pallet,storage_display,duplicated_from,product_id,article,size,name,product_found)
            VALUES(:id,:r,:b,:q,:l,:box,:lid,:cid,:cname,:sec,:aisle,:pal,:disp,:dup,:prod,:a,:z,:n,:pf)"""), inserts)
    if event_inserts:
        db.execute(text("""INSERT INTO fc_r145f27_manual_events
            (id,receipt_id,line_id,request_id,action,barcode,delta_qty,before_qty,after_qty,location_code,container_code,actor)
            VALUES(:id,:r,:line,:req,'SCAN',:b,:delta,:before,:after,:l,:box,:actor)"""), event_inserts)
    if meta_rows:
        # Receipt split metadata is useful but must never turn a saved scan into
        # an error.  SAVEPOINT keeps the hot data transaction intact if an old
        # installation lacks the auxiliary table for any reason.
        try:
            with db.begin_nested():
                db.execute(text("""INSERT INTO fc_r145f42_event_meta(request_id,receipt_id,client_created_at)
                    VALUES(:request_id,:receipt_id,:client_created_at)
                    ON CONFLICT(request_id) DO UPDATE SET
                      receipt_id=EXCLUDED.receipt_id,
                      client_created_at=COALESCE(fc_r145f42_event_meta.client_created_at,EXCLUDED.client_created_at)"""), meta_rows)
        except Exception:
            pass
    if valid:
        db.execute(text("""UPDATE fc_r145f27_manual_receipts
            SET reference=COALESCE(NULLIF(:ref,''),reference),updated_at=CURRENT_TIMESTAMP
            WHERE id=:id"""), {"ref": _s(payload.reference, 255), "id": rid})
    db.commit()

    return {
        "ok": True,
        "receipt_id": rid,
        "acks": acks,
        "failed": failed,
        "ignored_previous_receipt": ignored_previous,
        "lines": line_summaries,
        "accepted_units": accepted_units,
        "batch_events": len(ordered),
        "fresh_events": len(valid),
        "single_commit": True,
        "set_based": True,
        "exact_receipt": True,
        "server_elapsed_ms": int(round((time.perf_counter() - started) * 1000)),
        "marker": "FC_R145F45_HOT_SYNC",
    }
