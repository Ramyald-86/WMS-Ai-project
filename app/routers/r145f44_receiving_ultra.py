"""r145f44: strict-box + set-based ultra-fast manual receiving queue sync.

This route preserves r145f41's exact receipt binding and cross-receipt request-id
idempotency, but removes the per-position SELECT/UPDATE/INSERT round trips from
the hot path.  It validates each distinct physical place once, locks the current
receipt once, loads active draft lines once, then executes line/event mutations
in batches and commits once.

No warehouse stock is posted here.  Stock still moves only during the existing
exact commit flow.
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
from app.routers import r145f41_receiving as prev
from app.services.r145f25_places import scope

router = APIRouter(tags=["r145f44"])


def _s(v, limit=512):
    return str(v or "").strip()[:limit]


def _fold(v):
    return _s(v).casefold()


class FastBatch44(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    receipt_id: uuid.UUID
    worker: str = Field(min_length=1, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    events: list[prev.BatchEvent] = Field(default_factory=list, max_length=500)
    want_snapshot: bool = False


def _lock(db: Session, key: str) -> None:
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})


def _line_maps(rows):
    """Build strict lookup maps. Explicit box/location codes never borrow identity
    from another explicit code merely because a stale container_id matches.
    """
    by_ids = {}
    by_codes = {}
    for row in rows:
        b = _s(row.get("barcode"), 255)
        lid = _s(row.get("location_id"), 36)
        cid = _s(row.get("container_id"), 36)
        lc = _fold(row.get("location_code"))
        aliases = {_fold(row.get("container_code")), _fold(row.get("container_name"))} - {""}
        if b and lid:
            by_ids.setdefault((b, lid, cid), row)
        if b and lc:
            if aliases:
                for alias in aliases: by_codes.setdefault((b, lc, alias), row)
            else:
                by_codes.setdefault((b, lc, ""), row)
    return by_ids, by_codes


@router.post("/r145f44/manual-drafts/sync-batch-ultra")
def sync_batch_ultra(payload: FastBatch44, request: Request, db: Session = Depends(get_db)):
    started = time.perf_counter()
    scope(db, payload.client_id, payload.warehouse_id)
    rid = str(payload.receipt_id)

    # One scope lock closes the tiny race where the same request id could be
    # retried against two different receipts at exactly the same moment.
    _lock(db, f"fc:r145f44:scope:{payload.client_id}:{payload.warehouse_id}")
    _lock(db, f"fc:r145f44:receipt:{rid}")
    receipt = prev._receipt_exact(db, payload, payload.receipt_id)

    ordered = []
    seen = set()
    for ev in payload.events[:500]:
        req = _s(ev.request_id, 96)
        if not req or req in seen:
            continue
        seen.add(req)
        ordered.append(ev)

    by_req = prev._events_by_request(
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

    # Resolve each physical place once. Typical 100+ scan batches have only a
    # handful of distinct boxes, so this remains bounded and keeps all existing
    # client/location safety validation.
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

    # Lock/load all active lines exactly once, not once per SKU/box.
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
        # Canonical identity comes from the validated physical objects, never
        # from an asynchronously stale browser container_id.
        key = (barcode, loc_id, cont_id)
        groups.setdefault(key, []).append((ev, loc, cont))

    updates = []
    inserts = []
    event_inserts = []
    line_summaries = []

    for (barcode, loc_id, cont_id), entries in groups.items():
        last, loc, cont = entries[-1]
        canonical_loc = _s(getattr(loc, "code", None), 255) or _s(last.location_code, 255)
        canonical_box = None
        if cont:
            canonical_box = _s(getattr(cont, "barcode", None), 255) or _s(getattr(cont, "name", None), 255) or _s(last.container_code, 255) or None
        id_key = (barcode, loc_id, cont_id)
        line = by_ids.get(id_key)
        if not line:
            aliases=[canonical_box, getattr(cont, "name", None) if cont else None, getattr(cont, "barcode", None) if cont else None, last.container_code, last.container_name]
            for alias in aliases:
                line=by_codes.get((barcode, _fold(canonical_loc), _fold(alias)))
                if line: break
        before = int(line.get("quantity") or 0) if line else 0
        total_delta = sum(int(ev.quantity or 1) for ev, _loc, _cont in entries)
        after = before + total_delta
        line_id = str(line.get("id")) if line else str(uuid.uuid4())
        cname = (_s(getattr(cont, "name", None), 255) if cont else None) or canonical_box
        values = {
            "id": line_id,
            "r": rid,
            "b": barcode,
            "q": after,
            "l": canonical_loc,
            "box": canonical_box,
            "lid": loc_id or (_s(last.location_id, 36) or None),
            "cid": cont_id or None,
            "cname": cname,
            "a": _s(last.article, 255) or None,
            "z": _s(last.size, 255) or None,
            "n": _s(last.name, 512) or None,
            "pf": bool(last.product_found),
            "sec": _s(last.section, 80) or None,
            "aisle": _s(last.aisle, 80) or None,
            "pal": _s(last.pallet, 120) or None,
            "disp": _s(last.storage_display, 512) or None,
            "dup": _s(last.duplicated_from, 255) or None,
            "prod": _s(last.product_id, 36) or None,
        }
        if line:
            updates.append(values)
        else:
            inserts.append(values)

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
    if valid:
        db.execute(text("""UPDATE fc_r145f27_manual_receipts
            SET reference=COALESCE(NULLIF(:ref,''),reference),updated_at=CURRENT_TIMESTAMP
            WHERE id=:id"""), {"ref": _s(payload.reference, 255), "id": rid})
    db.commit()

    snapshot = None
    if payload.want_snapshot:
        receipt = fast._receipt(db, payload.receipt_id, payload.client_id, payload.warehouse_id)
        snapshot = fast._compact_snapshot(db, receipt)

    elapsed_ms = int(round((time.perf_counter() - started) * 1000))
    return {
        "ok": True,
        "receipt_id": rid,
        "acks": acks,
        "failed": failed,
        "ignored_previous_receipt": ignored_previous,
        "snapshot": snapshot,
        "lines": line_summaries,
        "batch_events": len(ordered),
        "fresh_events": len(valid),
        "single_commit": True,
        "set_based": True,
        "exact_receipt": True,
        "server_elapsed_ms": elapsed_ms,
        "marker": "FC_R145F44_ULTRA_SET_SYNC",
    }
