"""r145f27: durable manual-receiving drafts.

Every acknowledged scan is saved on the server, while stock is changed only by
one final batch operation. Browser storage is a cache, not the source of truth.
"""
from __future__ import annotations
import uuid
from fastapi import HTTPException
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session
from app.models import Container, Location, SKU
from app.services.r145f25_places import assert_place

DRAFT_DDL = [
    """CREATE TABLE IF NOT EXISTS fc_r145f27_manual_receipts (
      id varchar(36) PRIMARY KEY, client_id varchar(36) NOT NULL, warehouse_id varchar(36) NOT NULL,
      actor varchar(255) NOT NULL DEFAULT '', reference varchar(255) NOT NULL DEFAULT '',
      status varchar(20) NOT NULL DEFAULT 'DRAFT', created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz,
      operation_ids text NOT NULL DEFAULT '')""",
    """CREATE INDEX IF NOT EXISTS ix_fc_r145f27_manual_receipts_scope
      ON fc_r145f27_manual_receipts(client_id,warehouse_id,actor,status,updated_at DESC)""",
    """CREATE TABLE IF NOT EXISTS fc_r145f27_manual_lines (
      id varchar(36) PRIMARY KEY, receipt_id varchar(36) NOT NULL REFERENCES fc_r145f27_manual_receipts(id) ON DELETE CASCADE,
      barcode varchar(255) NOT NULL, quantity integer NOT NULL DEFAULT 1,
      location_code varchar(255) NOT NULL, container_code varchar(255), location_id varchar(36), container_id varchar(36), container_name varchar(255),
      section varchar(80), aisle varchar(80), pallet varchar(120), storage_display varchar(512), duplicated_from varchar(255), product_id varchar(36),
      article varchar(255), size varchar(255), name varchar(512), product_found boolean NOT NULL DEFAULT true,
      posted_at timestamptz, operation_id varchar(36), created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(), CHECK(quantity >= 0))""",
    """CREATE INDEX IF NOT EXISTS ix_fc_r145f27_manual_lines_receipt
      ON fc_r145f27_manual_lines(receipt_id,created_at)""",
    """CREATE TABLE IF NOT EXISTS fc_r145f27_manual_events (
      id varchar(36) PRIMARY KEY, receipt_id varchar(36) NOT NULL REFERENCES fc_r145f27_manual_receipts(id) ON DELETE CASCADE,
      line_id varchar(36), request_id varchar(96) NOT NULL, action varchar(20) NOT NULL,
      barcode varchar(255), delta_qty integer NOT NULL DEFAULT 0, before_qty integer NOT NULL DEFAULT 0,
      after_qty integer NOT NULL DEFAULT 0, location_code varchar(255), container_code varchar(255),
      actor varchar(255), created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(receipt_id,request_id))""",
    """CREATE INDEX IF NOT EXISTS ix_fc_r145f27_manual_events_receipt
      ON fc_r145f27_manual_events(receipt_id,created_at)""",
]

def install_schema(db: Session) -> None:
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SET LOCAL lock_timeout='5s'"))
    for sql in DRAFT_DDL:
        db.execute(text(sql))
    db.commit()

def _s(value, limit=255):
    return str(value or "").strip()[:limit]

def receipt(db: Session, rid, client_id, warehouse_id, lock=False):
    sql = "SELECT * FROM fc_r145f27_manual_receipts WHERE id=:id AND client_id=:c AND warehouse_id=:w"
    if lock and db.bind.dialect.name == "postgresql":
        sql += " FOR UPDATE"
    row = db.execute(text(sql), {"id": str(rid), "c": str(client_id), "w": str(warehouse_id)}).mappings().first()
    if not row:
        raise HTTPException(404, detail="Черновик приёмки не найден на выбранном складе")
    return dict(row)

def snapshot(db: Session, r: dict):
    raw_rows = db.execute(text("""SELECT * FROM fc_r145f27_manual_lines
        WHERE receipt_id=:r AND quantity>0 ORDER BY created_at,id"""), {"r": str(r["id"])}).mappings().all()
    rows = [dict(x) for x in raw_rows]
    # Product-card corrections are read from current SKU data so an edited card is
    # reflected in an already-saved server draft without rewriting scan history.
    barcodes = sorted({str(x.get("barcode") or "").strip() for x in rows if x.get("barcode")})
    current = {}
    if barcodes:
        try:
            client_uuid = uuid.UUID(str(r["client_id"]))
            skus = db.execute(select(SKU).where(SKU.client_id == client_uuid, SKU.barcode.in_(barcodes))).scalars().all()
            for sku in skus:
                current[str(sku.barcode or "").strip()] = sku
        except Exception:
            # Draft readability must not depend on catalog enrichment.
            current = {}
    for row in rows:
        sku = current.get(str(row.get("barcode") or "").strip())
        if sku:
            row["product_id"] = str(sku.id)
            row["article"] = sku.article or sku.sku_code or row.get("article")
            row["size"] = sku.size or row.get("size")
            row["name"] = sku.name or row.get("name")
            row["product_found"] = True
    events = db.execute(text("""SELECT id,line_id,request_id,action,barcode,delta_qty,before_qty,after_qty,
        location_code,container_code,actor,created_at FROM fc_r145f27_manual_events
        WHERE receipt_id=:r ORDER BY created_at,id"""), {"r": str(r["id"])}).mappings().all()
    return {
        "id": str(r["id"]), "client_id": str(r["client_id"]), "warehouse_id": str(r["warehouse_id"]),
        "actor": r["actor"], "reference": r.get("reference") or "", "status": r["status"],
        "created_at": r["created_at"], "updated_at": r["updated_at"], "completed_at": r.get("completed_at"),
        "rows": rows, "events": [dict(x) for x in events],
        "units": sum(int(x["quantity"] or 0) for x in rows if not x.get("posted_at")),
        "saved_on_server": True,
    }

def open_receipt(db: Session, client_id, warehouse_id, actor, requested_id=None, reference=""):
    actor = _s(actor) or "admin"
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {
            "k": f"fc:r145f27:draft:{client_id}:{warehouse_id}:{actor.casefold()}"
        })
    row = db.execute(text("""SELECT * FROM fc_r145f27_manual_receipts
      WHERE client_id=:c AND warehouse_id=:w AND lower(actor)=lower(:a)
        AND status IN ('DRAFT','POSTING') ORDER BY updated_at DESC LIMIT 1"""),
      {"c": str(client_id), "w": str(warehouse_id), "a": actor}).mappings().first()
    if not row:
        rid = str(requested_id or uuid.uuid4())
        db.execute(text("""INSERT INTO fc_r145f27_manual_receipts
          (id,client_id,warehouse_id,actor,reference,status) VALUES(:id,:c,:w,:a,:ref,'DRAFT')"""),
          {"id": rid, "c": str(client_id), "w": str(warehouse_id), "a": actor, "ref": _s(reference)})
        db.commit()
        row = receipt(db, rid, client_id, warehouse_id)
    else:
        row = dict(row)
        if reference and not row.get("reference"):
            db.execute(text("UPDATE fc_r145f27_manual_receipts SET reference=:ref,updated_at=now() WHERE id=:id"),
                       {"ref": _s(reference), "id": str(row["id"])})
            db.commit()
            row = receipt(db, row["id"], client_id, warehouse_id)
    return snapshot(db, row)

def _resolve_location(db: Session, warehouse_id, code):
    raw = _s(code)
    loc = db.execute(select(Location).where(
        Location.warehouse_id == warehouse_id, Location.is_active.is_(True),
        or_(Location.code == raw, Location.barcode == raw)
    )).scalars().first()
    if not loc:
        raise HTTPException(409, detail=f"Место «{raw}» не найдено на выбранном складе")
    return loc

def validate_place(db: Session, client_id, warehouse_id, location_code, container_code=None):
    loc = _resolve_location(db, warehouse_id, location_code)
    assert_place(db, client_id, warehouse_id, loc.id)
    cont = None
    raw_box = _s(container_code)
    if raw_box:
        cont = db.execute(select(Container).where(
            Container.client_id == client_id, Container.warehouse_id == warehouse_id,
            Container.status.in_(["ACTIVE", "DEFECT_ACTIVE"]),
            or_(Container.name == raw_box, Container.barcode == raw_box)
        )).scalars().first()
        if not cont:
            raise HTTPException(409, detail=f"Короб «{raw_box}» не найден у выбранного клиента на этом складе")
        if cont.location_id and cont.location_id != loc.id:
            raise HTTPException(409, detail=f"Короб {cont.name or raw_box} находится в другом месте")
    return loc, cont

def add_scan(db: Session, rid, client_id, warehouse_id, actor, payload: dict):
    r = receipt(db, rid, client_id, warehouse_id, True)
    if r["status"] != "DRAFT":
        raise HTTPException(409, detail="Черновик уже проводится или завершён")
    request_id = _s(payload.get("request_id"), 96)
    if not request_id:
        raise HTTPException(422, detail="Не передан идентификатор скана")
    if db.execute(text("SELECT 1 FROM fc_r145f27_manual_events WHERE receipt_id=:r AND request_id=:q"),
                  {"r": str(rid), "q": request_id}).first():
        db.rollback()
        return snapshot(db, receipt(db, rid, client_id, warehouse_id))
    barcode = _s(payload.get("barcode")); qty = int(payload.get("quantity") or 1)
    if not barcode or qty < 1 or qty > 100000:
        raise HTTPException(422, detail="Проверьте ШК и количество")
    location_code = _s(payload.get("location_code")); container_code = _s(payload.get("container_code")) or None
    validate_place(db, client_id, warehouse_id, location_code, container_code)
    line = db.execute(text("""SELECT * FROM fc_r145f27_manual_lines
       WHERE receipt_id=:r AND posted_at IS NULL AND quantity>0 AND barcode=:b AND location_code=:l
         AND COALESCE(container_code,'')=COALESCE(:box,'') ORDER BY created_at LIMIT 1"""),
       {"r": str(rid), "b": barcode, "l": location_code, "box": container_code}).mappings().first()
    before = int(line["quantity"] or 0) if line else 0; after = before + qty
    values = {"q": after, "a": _s(payload.get("article")) or None, "z": _s(payload.get("size")) or None,
              "n": _s(payload.get("name"), 512) or None, "pf": bool(payload.get("product_found", True)),
              "lid": _s(payload.get("location_id"),36) or None, "cid": _s(payload.get("container_id"),36) or None,
              "cname": _s(payload.get("container_name")) or None, "sec": _s(payload.get("section"),80) or None,
              "aisle": _s(payload.get("aisle"),80) or None, "pal": _s(payload.get("pallet"),120) or None,
              "disp": _s(payload.get("storage_display"),512) or None, "dup": _s(payload.get("duplicated_from")) or None,
              "prod": _s(payload.get("product_id"),36) or None}
    if line:
        lid = str(line["id"])
        db.execute(text("""UPDATE fc_r145f27_manual_lines SET quantity=:q,article=:a,size=:z,name=:n,product_found=:pf,location_id=:lid,container_id=:cid,container_name=:cname,section=:sec,aisle=:aisle,pallet=:pal,storage_display=:disp,duplicated_from=:dup,product_id=:prod,updated_at=now() WHERE id=:id"""), {**values, "id": lid})
    else:
        lid = str(uuid.uuid4())
        db.execute(text("""INSERT INTO fc_r145f27_manual_lines
          (id,receipt_id,barcode,quantity,location_code,container_code,location_id,container_id,container_name,section,aisle,pallet,storage_display,duplicated_from,product_id,article,size,name,product_found)
          VALUES(:id,:r,:b,:q,:l,:box,:lid,:cid,:cname,:sec,:aisle,:pal,:disp,:dup,:prod,:a,:z,:n,:pf)"""),
          {**values, "id": lid, "r": str(rid), "b": barcode, "l": location_code, "box": container_code})
    db.execute(text("""INSERT INTO fc_r145f27_manual_events
      (id,receipt_id,line_id,request_id,action,barcode,delta_qty,before_qty,after_qty,location_code,container_code,actor)
      VALUES(:id,:r,:line,:req,:act,:b,:delta,:before,:after,:l,:box,:actor)"""), {
        "id": str(uuid.uuid4()), "r": str(rid), "line": lid, "req": request_id,
        "act": "MIGRATE" if payload.get("migrate") else "SCAN", "b": barcode,
        "delta": qty, "before": before, "after": after, "l": location_code, "box": container_code,
        "actor": _s(actor),
    })
    db.execute(text("UPDATE fc_r145f27_manual_receipts SET updated_at=now() WHERE id=:id"), {"id": str(rid)})
    db.commit()
    return snapshot(db, receipt(db, rid, client_id, warehouse_id))

def set_quantity(db: Session, rid, line_id, client_id, warehouse_id, actor, payload: dict):
    r = receipt(db, rid, client_id, warehouse_id, True)
    if r["status"] != "DRAFT": raise HTTPException(409, detail="Черновик уже проводится или завершён")
    request_id = _s(payload.get("request_id"), 96); qty = int(payload.get("quantity") or 0)
    if not request_id or qty < 1: raise HTTPException(422, detail="Количество должно быть не меньше 1")
    if db.execute(text("SELECT 1 FROM fc_r145f27_manual_events WHERE receipt_id=:r AND request_id=:q"),
                  {"r": str(rid), "q": request_id}).first():
        db.rollback(); return snapshot(db, receipt(db, rid, client_id, warehouse_id))
    sql = "SELECT * FROM fc_r145f27_manual_lines WHERE id=:id AND receipt_id=:r AND posted_at IS NULL"
    if db.bind.dialect.name == "postgresql": sql += " FOR UPDATE"
    line = db.execute(text(sql), {"id": str(line_id), "r": str(rid)}).mappings().first()
    if not line: raise HTTPException(404, detail="Строка черновика не найдена")
    before = int(line["quantity"] or 0)
    db.execute(text("UPDATE fc_r145f27_manual_lines SET quantity=:q,updated_at=now() WHERE id=:id"), {"q": qty, "id": str(line_id)})
    db.execute(text("""INSERT INTO fc_r145f27_manual_events
      (id,receipt_id,line_id,request_id,action,barcode,delta_qty,before_qty,after_qty,location_code,container_code,actor)
      VALUES(:id,:r,:line,:req,'ADJUST',:b,:delta,:before,:after,:l,:box,:actor)"""), {
        "id": str(uuid.uuid4()), "r": str(rid), "line": str(line_id), "req": request_id,
        "b": line["barcode"], "delta": qty-before, "before": before, "after": qty,
        "l": line["location_code"], "box": line["container_code"], "actor": _s(actor),
    })
    db.execute(text("UPDATE fc_r145f27_manual_receipts SET updated_at=now() WHERE id=:id"), {"id": str(rid)})
    db.commit(); return snapshot(db, receipt(db, rid, client_id, warehouse_id))

def remove_line(db: Session, rid, line_id, client_id, warehouse_id, actor, request_id):
    r = receipt(db, rid, client_id, warehouse_id, True)
    if r["status"] != "DRAFT": raise HTTPException(409, detail="Черновик уже проводится или завершён")
    request_id = _s(request_id, 96)
    if db.execute(text("SELECT 1 FROM fc_r145f27_manual_events WHERE receipt_id=:r AND request_id=:q"),
                  {"r": str(rid), "q": request_id}).first():
        db.rollback(); return snapshot(db, receipt(db, rid, client_id, warehouse_id))
    sql = "SELECT * FROM fc_r145f27_manual_lines WHERE id=:id AND receipt_id=:r AND posted_at IS NULL"
    if db.bind.dialect.name == "postgresql": sql += " FOR UPDATE"
    line = db.execute(text(sql), {"id": str(line_id), "r": str(rid)}).mappings().first()
    if not line: raise HTTPException(404, detail="Строка черновика не найдена")
    before = int(line["quantity"] or 0)
    db.execute(text("UPDATE fc_r145f27_manual_lines SET quantity=0,updated_at=now() WHERE id=:id"), {"id": str(line_id)})
    db.execute(text("""INSERT INTO fc_r145f27_manual_events
      (id,receipt_id,line_id,request_id,action,barcode,delta_qty,before_qty,after_qty,location_code,container_code,actor)
      VALUES(:id,:r,:line,:req,'REMOVE',:b,:delta,:before,0,:l,:box,:actor)"""), {
        "id": str(uuid.uuid4()), "r": str(rid), "line": str(line_id), "req": request_id,
        "b": line["barcode"], "delta": -before, "before": before,
        "l": line["location_code"], "box": line["container_code"], "actor": _s(actor),
    })
    db.execute(text("UPDATE fc_r145f27_manual_receipts SET updated_at=now() WHERE id=:id"), {"id": str(rid)})
    db.commit(); return snapshot(db, receipt(db, rid, client_id, warehouse_id))

def cancel(db: Session, rid, client_id, warehouse_id):
    r = receipt(db, rid, client_id, warehouse_id, True)
    if r["status"] == "COMPLETED":
        raise HTTPException(409, detail="Проведённую приёмку отменяйте через историю приёмок")
    db.execute(text("UPDATE fc_r145f27_manual_receipts SET status='CANCELLED',updated_at=now(),completed_at=now() WHERE id=:id"), {"id": str(rid)})
    db.commit(); return {"ok": True, "id": str(rid)}
