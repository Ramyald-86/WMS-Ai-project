from __future__ import annotations

import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers.warehouse_ui import scope

router = APIRouter(prefix="/storage", tags=["Хранение"])

# FC_R145F14_STORAGE_CELLS_AS_BOXES
# Automatic storage truth:
#   * count ACTIVE physical boxes that currently contain product in MAIN/FBS;
#   * count occupied physical cells/pallet locations with direct stock (container_id IS NULL)
#     as one storage box-equivalent each;
#   * count only ACTIVE transit boxes that currently contain transit product;
#   * never count empty/archived/defect/FBO/virtual receiving places in automatic storage;
#   * manual PVZ boxes without barcode stay in a separate counter and never affect stock.
# The endpoint is read-only for inventory quantities.


class ManualBoxCreate(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    name: str = Field(default="Неразобранные короба ПВЗ", min_length=1, max_length=200)
    box_count: int = Field(default=1, ge=1, le=100000, strict=True)
    note: str = Field(default="", max_length=1000)
    performed_by: str = Field(default="Администратор", max_length=160)


class ManualBoxAdjust(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    delta: int = Field(ge=-100000, le=100000, strict=True)
    reason: str = Field(default="", max_length=1000)
    performed_by: str = Field(default="Администратор", max_length=160)


def install_schema(db: Session) -> None:
    db.execute(text("""
      CREATE TABLE IF NOT EXISTS fc_storage_manual_boxes (
        id uuid PRIMARY KEY,
        client_id uuid NOT NULL,
        warehouse_id uuid NOT NULL,
        name varchar(200) NOT NULL,
        box_count integer NOT NULL CHECK(box_count >= 0),
        note text NOT NULL DEFAULT '',
        status varchar(16) NOT NULL DEFAULT 'ACTIVE',
        performed_by varchar(160) NOT NULL DEFAULT '',
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
      )
    """))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_storage_manual_boxes_scope ON fc_storage_manual_boxes(client_id,warehouse_id,status,updated_at DESC)"))
    db.execute(text("""
      CREATE TABLE IF NOT EXISTS fc_storage_manual_audit (
        id uuid PRIMARY KEY,
        manual_box_id uuid NOT NULL,
        client_id uuid NOT NULL,
        warehouse_id uuid NOT NULL,
        delta integer NOT NULL,
        box_count_after integer NOT NULL,
        reason text NOT NULL DEFAULT '',
        performed_by varchar(160) NOT NULL DEFAULT '',
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """))
    db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_storage_manual_audit_box ON fc_storage_manual_audit(manual_box_id,created_at DESC)"))
    if db.execute(text("SELECT to_regclass('public.fc_transit_boxes')")).scalar():
        db.execute(text("ALTER TABLE fc_transit_boxes ADD COLUMN IF NOT EXISTS source_container_id varchar(36) NULL"))
        db.execute(text("CREATE INDEX IF NOT EXISTS ix_fc_transit_boxes_source_container ON fc_transit_boxes(source_container_id)"))
    # Keep schema setup independent from later read queries. A malformed optional source
    # must never roll back the manual-storage tables and turn the page into a 500 loop.
    db.commit()


def _exists(db: Session, table: str) -> bool:
    return bool(db.execute(text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}).scalar())


def _actor(request: Request, fallback: str = "Администратор") -> str:
    return str(getattr(request.state, "username", None) or fallback or "Администратор")[:160]


def _clean_text(v) -> str:
    return " ".join(str(v or "").split())


def _d(v) -> Decimal:
    try:
        return Decimal(str(v or 0))
    except Exception:
        return Decimal("0")


def _stock_main_by_direct_location(db: Session, client_id, warehouse_id) -> tuple[dict[str, Decimal], bool]:
    """Return STORAGE_ONLY quantity for stock placed directly in a location.

    Rows that have a real container_key belong to a physical box and are intentionally
    excluded here, otherwise the same stock would be counted both as a box and as a cell.
    """
    if not _exists(db, "fc_stocks_v2"):
        return {}, False
    rows = db.execute(text("""
      SELECT
        COALESCE(NULLIF(to_jsonb(s)->>'location_id',''), '') AS location_id,
        COALESCE(SUM(
          CASE
            WHEN COALESCE(to_jsonb(s)->>'storage_only','') ~ '^-?[0-9]+([.][0-9]+)?$'
              THEN (to_jsonb(s)->>'storage_only')::numeric
            ELSE 0
          END
        ),0) AS main_units
      FROM fc_stocks_v2 s
      WHERE CAST(s.client_id AS text)=:c AND CAST(s.warehouse_id AS text)=:w
        AND COALESCE(NULLIF(to_jsonb(s)->>'location_id',''),'') <> ''
        AND UPPER(COALESCE(NULLIF(to_jsonb(s)->>'container_key',''),'__NO_CONTAINER__'))
            IN ('__NO_CONTAINER__','NONE','NULL')
      GROUP BY COALESCE(NULLIF(to_jsonb(s)->>'location_id',''), '')
    """), {"c": str(client_id), "w": str(warehouse_id)}).mappings().all()
    out: dict[str, Decimal] = {}
    for row in rows:
        key = str(row.get("location_id") or "").strip()
        if key:
            out[key] = max(Decimal("0"), _d(row.get("main_units")))
    return out, True


def _is_real_storage_location_sql(alias: str = "l") -> str:
    # SQL fragment only; location fields are read through to_jsonb for compatibility
    # with older installations where optional metadata columns differ.
    return f"""
      UPPER(COALESCE(to_jsonb({alias})->>'status','ACTIVE')) NOT IN ('ARCHIVED','DELETED','REMOVED')
      AND UPPER(COALESCE(to_jsonb({alias})->>'location_type','')) NOT IN ('VIRTUAL','RECEIVING','DEFECT','DEFECT_STORAGE')
      AND UPPER(COALESCE(to_jsonb({alias})->>'code','')) NOT IN ('FC-DEFECT-STORAGE','ПРИЕМКА','RECEIVING')
      AND UPPER(COALESCE(to_jsonb({alias})->>'code','')) NOT LIKE '%ПРИЕМКА%'
    """


def _stock_main_by_container(db: Session, client_id, warehouse_id) -> tuple[dict[str, Decimal], bool]:
    """Return STORAGE_ONLY quantity by physical container if fc_stocks_v2 is available.

    The mirror evolved over time, therefore container key and storage_only are read
    through to_jsonb(record). Missing optional fields become NULL instead of causing a
    SQL compilation error. If the mirror is unavailable, caller still lists the box as
    'Основной / FBS' rather than failing the whole Storage page.
    """
    if not _exists(db, "fc_stocks_v2"):
        return {}, False
    rows = db.execute(text("""
      SELECT
        COALESCE(NULLIF(to_jsonb(s)->>'container_key',''), NULLIF(to_jsonb(s)->>'container_id',''), '') AS container_key,
        COALESCE(SUM(
          CASE
            WHEN COALESCE(to_jsonb(s)->>'storage_only','') ~ '^-?[0-9]+([.][0-9]+)?$'
              THEN (to_jsonb(s)->>'storage_only')::numeric
            ELSE 0
          END
        ),0) AS main_units
      FROM fc_stocks_v2 s
      WHERE CAST(s.client_id AS text)=:c AND CAST(s.warehouse_id AS text)=:w
      GROUP BY COALESCE(NULLIF(to_jsonb(s)->>'container_key',''), NULLIF(to_jsonb(s)->>'container_id',''), '')
    """), {"c": str(client_id), "w": str(warehouse_id)}).mappings().all()
    out: dict[str, Decimal] = {}
    for row in rows:
        key = str(row.get("container_key") or "").strip()
        if key and key not in {"__NO_CONTAINER__", "NONE", "NULL"}:
            out[key] = max(Decimal("0"), _d(row.get("main_units")))
    return out, True


def _physical_rows(db: Session, client_id, warehouse_id) -> list[dict]:
    """Physical MAIN/FBS boxes only, one row per real container, quantity > 0."""
    main_map, mirror_available = _stock_main_by_container(db, client_id, warehouse_id)
    rows = db.execute(text("""
      WITH box_qty AS (
        SELECT container_id,COALESCE(SUM(quantity),0) AS units
        FROM inventory_balances
        WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w
          AND container_id IS NOT NULL AND quantity>0
        GROUP BY container_id
        HAVING COALESCE(SUM(quantity),0)>0
      )
      SELECT
        CAST(c.id AS text) AS id,
        COALESCE(NULLIF(to_jsonb(c)->>'name',''),NULLIF(to_jsonb(c)->>'barcode',''),CAST(c.id AS text)) AS code,
        COALESCE(to_jsonb(c)->>'barcode','') AS barcode,
        COALESCE(to_jsonb(c)->>'container_type','') AS container_type,
        COALESCE(to_jsonb(c)->>'status','ACTIVE') AS status,
        CAST(c.location_id AS text) AS location_id,
        COALESCE(NULLIF(to_jsonb(l)->>'code',''),'Без адреса') AS location_code,
        COALESCE(to_jsonb(l)->>'location_type','') AS location_type,
        q.units
      FROM containers c
      JOIN box_qty q ON q.container_id=c.id
      LEFT JOIN locations l ON l.id=c.location_id
      WHERE CAST(c.client_id AS text)=:c AND CAST(c.warehouse_id AS text)=:w
        AND UPPER(COALESCE(to_jsonb(c)->>'status','ACTIVE')) NOT IN ('ARCHIVED','DELETED','DEFECT_ARCHIVED','REMOVED')
        AND UPPER(COALESCE(to_jsonb(c)->>'container_type','')) <> 'DEFECT_BOX'
        AND UPPER(COALESCE(to_jsonb(c)->>'status','')) <> 'DEFECT_ACTIVE'
        AND UPPER(COALESCE(to_jsonb(l)->>'code','')) <> 'FC-DEFECT-STORAGE'
      ORDER BY COALESCE(to_jsonb(l)->>'code',''),COALESCE(NULLIF(to_jsonb(c)->>'name',''),to_jsonb(c)->>'barcode','')
    """), {"c": str(client_id), "w": str(warehouse_id)}).mappings().all()

    out: list[dict] = []
    for r0 in rows:
        r = dict(r0)
        physical = max(Decimal("0"), _d(r.get("units")))
        if physical <= 0:
            continue
        cid = str(r["id"])
        if mirror_available and cid in main_map:
            main = min(physical, max(Decimal("0"), main_map.get(cid, Decimal("0"))))
            fbs = max(Decimal("0"), physical - main)
            if main > 0 and fbs > 0:
                source = "Основной + FBS"
            elif main > 0:
                source = "Основной склад"
            else:
                source = "FBS"
            split_known = True
        else:
            main = Decimal("0")
            fbs = Decimal("0")
            source = "Основной / FBS"
            split_known = False
        detail = str(r.get("location_type") or "Ячейка / палета")
        if split_known:
            detail += f" · MAIN: {float(main):g} · FBS: {float(fbs):g}"
        out.append({
            "key": "container:" + cid,
            "kind": "container",
            "container_id": cid,
            "source": source,
            "code": r.get("code") or r.get("barcode") or cid,
            "barcode": r.get("barcode") or "",
            "location": r.get("location_code") or "Без адреса",
            "detail": detail,
            "box_count": 1,
            "units": float(physical),
            "main_units": float(main),
            "fbs_units": float(fbs),
            "split_known": split_known,
            "status": r.get("status") or "ACTIVE",
            "manual": False,
            "system_active": True,
        })
    return out


def _direct_location_rows(db: Session, client_id, warehouse_id) -> list[dict]:
    """Direct MAIN/FBS stock in cells/pallets without a container.

    One occupied physical location is one storage box-equivalent. This is the stock that
    r145f13 omitted because it filtered inventory_balances to container_id IS NOT NULL.
    """
    main_map, mirror_available = _stock_main_by_direct_location(db, client_id, warehouse_id)
    real_filter = _is_real_storage_location_sql('l')
    rows = db.execute(text(f"""
      WITH cell_qty AS (
        SELECT location_id,COALESCE(SUM(quantity),0) AS units
        FROM inventory_balances
        WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w
          AND container_id IS NULL AND location_id IS NOT NULL AND quantity>0
        GROUP BY location_id
        HAVING COALESCE(SUM(quantity),0)>0
      )
      SELECT
        CAST(l.id AS text) AS id,
        COALESCE(NULLIF(to_jsonb(l)->>'code',''),CAST(l.id AS text)) AS code,
        COALESCE(to_jsonb(l)->>'location_type','Ячейка') AS location_type,
        COALESCE(to_jsonb(l)->>'status','ACTIVE') AS status,
        q.units
      FROM locations l
      JOIN cell_qty q ON q.location_id=l.id
      WHERE CAST(l.warehouse_id AS text)=:w
        AND {real_filter}
      ORDER BY COALESCE(to_jsonb(l)->>'code',''),CAST(l.id AS text)
    """), {"c": str(client_id), "w": str(warehouse_id)}).mappings().all()

    out: list[dict] = []
    for r0 in rows:
        r = dict(r0)
        physical = max(Decimal("0"), _d(r.get("units")))
        if physical <= 0:
            continue
        lid = str(r["id"])
        if mirror_available and lid in main_map:
            main = min(physical, max(Decimal("0"), main_map.get(lid, Decimal("0"))))
            fbs = max(Decimal("0"), physical - main)
            if main > 0 and fbs > 0:
                source = "Основной + FBS"
            elif main > 0:
                source = "Основной склад"
            else:
                source = "FBS"
            split_known = True
        else:
            main = Decimal("0")
            fbs = Decimal("0")
            source = "Основной / FBS"
            split_known = False
        loc_type = str(r.get("location_type") or "Ячейка")
        out.append({
            "key": "location:" + lid,
            "kind": "location",
            "location_id": lid,
            "source": source,
            "code": r.get("code") or lid,
            "barcode": "",
            "location": r.get("code") or lid,
            "detail": f"{loc_type} · товар лежит напрямую без короба · считаем как 1 коробо-место",
            "box_count": 1,
            "units": float(physical),
            "main_units": float(main),
            "fbs_units": float(fbs),
            "split_known": split_known,
            "status": r.get("status") or "ACTIVE",
            "manual": False,
            "system_active": True,
            "storage_equivalent": "CELL_AS_BOX",
        })
    return out


def _inventory_positive_units(db: Session, client_id, warehouse_id) -> Decimal:
    """All positive inventory_balances units in the selected warehouse.

    Used only as a reconciliation hint. It does not change stock and may be greater than
    storage units when stock is still in virtual receiving/other non-storage locations.
    """
    val = db.execute(text("""
      SELECT COALESCE(SUM(quantity),0)
      FROM inventory_balances
      WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w AND quantity>0
    """), {"c": str(client_id), "w": str(warehouse_id)}).scalar()
    return max(Decimal("0"), _d(val))


def _transit_rows(db: Session, client_id, warehouse_id) -> list[dict]:
    if not all(_exists(db, t) for t in ("fc_transit_boxes", "fc_transit_batches", "fc_transit_box_items")):
        return []
    rows = db.execute(text("""
      WITH transit_qty AS (
        SELECT box_id,COALESCE(SUM(quantity),0) AS units
        FROM fc_transit_box_items
        WHERE quantity>0
        GROUP BY box_id
        HAVING COALESCE(SUM(quantity),0)>0
      )
      SELECT
        CAST(b.id AS text) AS id,
        COALESCE(to_jsonb(b)->>'barcode','') AS barcode,
        COALESCE(to_jsonb(b)->>'status','OPEN') AS status,
        COALESCE(to_jsonb(b)->>'source_container_id','') AS source_container_id,
        COALESCE(NULLIF(to_jsonb(a)->>'lot_number',''),NULLIF(to_jsonb(a)->>'number',''),'—') AS lot_number,
        q.units
      FROM fc_transit_boxes b
      JOIN fc_transit_batches a ON a.id=b.batch_id
      JOIN transit_qty q ON q.box_id=b.id
      WHERE CAST(a.client_id AS text)=:c AND CAST(a.warehouse_id AS text)=:w
        AND UPPER(COALESCE(to_jsonb(a)->>'status','OPEN'))='OPEN'
        AND UPPER(COALESCE(to_jsonb(b)->>'status','OPEN')) IN ('OPEN','CLOSED','ACTIVE')
      ORDER BY COALESCE(to_jsonb(a)->>'updated_at',to_jsonb(a)->>'created_at','') DESC,
               COALESCE(to_jsonb(b)->>'created_at',''),CAST(b.id AS text)
    """), {"c": str(client_id), "w": str(warehouse_id)}).mappings().all()
    out = []
    for r in rows:
        units = max(Decimal("0"), _d(r.get("units")))
        if units <= 0:
            continue
        out.append({
            "key": "transit:" + str(r["id"]),
            "kind": "transit",
            "transit_box_id": str(r["id"]),
            "source_container_id": str(r.get("source_container_id") or ""),
            "source": "Транзитный склад",
            "code": r.get("barcode") or str(r["id"]),
            "barcode": r.get("barcode") or "",
            "location": "Партия: " + str(r.get("lot_number") or "—"),
            "detail": "Активный транзитный короб с товаром",
            "box_count": 1,
            "units": float(units),
            "main_units": 0,
            "fbs_units": 0,
            "split_known": True,
            "status": r.get("status") or "OPEN",
            "manual": False,
            "system_active": True,
        })
    return out


def _manual_rows(db: Session, client_id, warehouse_id) -> list[dict]:
    rows = db.execute(text("""
      SELECT id,name,box_count,note,status,performed_by,created_at,updated_at
      FROM fc_storage_manual_boxes
      WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w AND status='ACTIVE' AND box_count>0
      ORDER BY updated_at DESC
    """), {"c": str(client_id), "w": str(warehouse_id)}).mappings().all()
    return [{
        "key": "manual:" + str(r["id"]),
        "kind": "manual",
        "manual_id": str(r["id"]),
        "source": "ПВЗ · неразобранные",
        "code": r.get("name") or "Неразобранные короба ПВЗ",
        "barcode": "",
        "location": "Без ШК / без раскладки",
        "detail": r.get("note") or "Количество коробов уменьшается вручную",
        "box_count": int(r.get("box_count") or 0),
        "units": 0,
        "main_units": 0,
        "fbs_units": 0,
        "status": "ACTIVE",
        "manual": True,
        "system_active": False,
        "performed_by": r.get("performed_by") or "",
        "updated_at": str(r.get("updated_at") or ""),
    } for r in rows]


def overview(db: Session, client_id, warehouse_id, q: str = "", source: str = "") -> dict:
    install_schema(db)

    # MAIN/FBS storage consists of two real physical forms:
    #   1) product inside a registered container;
    #   2) product placed directly in a cell/pallet without container_id.
    physical = _physical_rows(db, client_id, warehouse_id)
    direct_locations = _direct_location_rows(db, client_id, warehouse_id)
    rows = list(physical) + list(direct_locations)
    by_container = {r.get("container_id"): r for r in rows if r.get("container_id")}
    for t in _transit_rows(db, client_id, warehouse_id):
        cid = t.get("source_container_id")
        if cid and cid in by_container:
            # Existing physical box attached to transit must be counted once.
            p = by_container[cid]
            p.update({
                "source": "Транзитный склад",
                "location": t["location"],
                "detail": t["detail"],
                "units": t["units"],
                "main_units": 0,
                "fbs_units": 0,
                "split_known": True,
                "transit_box_id": t.get("transit_box_id"),
            })
        else:
            rows.append(t)

    # Unsorted PVZ boxes are intentionally separate from automatic storage truth.
    rows.extend(_manual_rows(db, client_id, warehouse_id))

    # Reconciliation is calculated before UI filtering so the top cards always describe
    # the selected warehouse, not only the current text filter.
    all_system_rows = [r for r in rows if r.get("system_active")]
    main_fbs_storage_units = sum(_d(r.get("units")) for r in all_system_rows if r.get("source") != "Транзитный склад")
    inventory_positive_units = _inventory_positive_units(db, client_id, warehouse_id)
    outside_storage_units = max(Decimal("0"), inventory_positive_units - main_fbs_storage_units)

    needle = _clean_text(q).casefold()
    source = _clean_text(source)
    if needle:
        rows = [r for r in rows if needle in " ".join(str(r.get(k) or "") for k in ("source", "code", "barcode", "location", "detail")).casefold()]
    if source and source != "ALL":
        rows = [r for r in rows if r.get("source") == source]

    system_rows = [r for r in rows if r.get("system_active")]
    manual_rows = [r for r in rows if r.get("manual")]
    physical_box_rows = [r for r in system_rows if r.get("kind") in ("container", "transit")]
    direct_cell_rows = [r for r in system_rows if r.get("kind") == "location"]
    summary = {
        # Backward-compatible aliases: one occupied direct cell is deliberately one
        # box-equivalent for storage accounting.
        "boxes": sum(int(r.get("box_count") or 0) for r in system_rows),
        "units": sum(float(r.get("units") or 0) for r in system_rows),
        "system_boxes": sum(int(r.get("box_count") or 0) for r in system_rows),
        "storage_places": sum(int(r.get("box_count") or 0) for r in system_rows),
        "physical_boxes": sum(int(r.get("box_count") or 0) for r in physical_box_rows),
        "direct_cells": sum(int(r.get("box_count") or 0) for r in direct_cell_rows),
        "manual_boxes": sum(int(r.get("box_count") or 0) for r in manual_rows),
        "main_boxes": sum(1 for r in system_rows if float(r.get("main_units") or 0) > 0),
        "fbs_boxes": sum(1 for r in system_rows if float(r.get("fbs_units") or 0) > 0),
        "transit_boxes": sum(1 for r in system_rows if r.get("source") == "Транзитный склад"),
        "mixed_boxes": sum(1 for r in system_rows if r.get("source") == "Основной + FBS"),
        "inventory_positive_units": float(inventory_positive_units),
        "main_fbs_storage_units": float(main_fbs_storage_units),
        "outside_storage_units": float(outside_storage_units),
        "sources": {},
    }
    for r in rows:
        src = r.get("source") or "Другое"
        summary["sources"][src] = summary["sources"].get(src, 0) + int(r.get("box_count") or 0)
    return {"rows": rows, "summary": summary}


@router.get("/overview")
def storage_overview(client_id: uuid.UUID, warehouse_id: uuid.UUID, q: str = "", source: str = "", db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id)
    try:
        return overview(db, client_id, warehouse_id, q, source)
    except HTTPException:
        raise
    except Exception as exc:
        # Do not return an opaque 500 on this operational screen. Keep server logs useful
        # while giving the operator a stable, human-readable error.
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Не удалось посчитать активные короба хранения: {type(exc).__name__}: {exc}") from exc


@router.post("/manual-boxes")
def create_manual_box(payload: ManualBoxCreate, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    install_schema(db)
    row_id = uuid.uuid4()
    actor = _actor(request, payload.performed_by)
    db.execute(text("""INSERT INTO fc_storage_manual_boxes(id,client_id,warehouse_id,name,box_count,note,performed_by)
      VALUES(:id,:c,:w,:n,:q,:note,:p)"""), {
        "id": str(row_id), "c": str(payload.client_id), "w": str(payload.warehouse_id),
        "n": _clean_text(payload.name)[:200], "q": payload.box_count,
        "note": str(payload.note or "")[:1000], "p": actor,
    })
    db.execute(text("""INSERT INTO fc_storage_manual_audit(id,manual_box_id,client_id,warehouse_id,delta,box_count_after,reason,performed_by)
      VALUES(:id,:b,:c,:w,:d,:after,:r,:p)"""), {
        "id": str(uuid.uuid4()), "b": str(row_id), "c": str(payload.client_id), "w": str(payload.warehouse_id),
        "d": payload.box_count, "after": payload.box_count, "r": "Добавлено на хранение", "p": actor,
    })
    db.commit()
    return {"ok": True, "id": str(row_id), "box_count": payload.box_count}


@router.post("/manual-boxes/{manual_id}/adjust")
def adjust_manual_box(manual_id: uuid.UUID, payload: ManualBoxAdjust, request: Request, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    install_schema(db)
    if payload.delta == 0:
        raise HTTPException(422, detail="Изменение количества не может быть нулевым")
    row = db.execute(text("""SELECT * FROM fc_storage_manual_boxes
      WHERE id=:id AND CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w FOR UPDATE"""), {
        "id": str(manual_id), "c": str(payload.client_id), "w": str(payload.warehouse_id),
    }).mappings().first()
    if not row:
        raise HTTPException(404, detail="Запись хранения не найдена")
    if row["status"] != "ACTIVE":
        raise HTTPException(409, detail="Эта запись уже снята с хранения")
    after = max(0, int(row["box_count"]) + int(payload.delta))
    actor = _actor(request, payload.performed_by)
    status = "REMOVED" if after == 0 else "ACTIVE"
    db.execute(text("UPDATE fc_storage_manual_boxes SET box_count=:q,status=:s,updated_at=now(),performed_by=:p WHERE id=:id"), {
        "q": after, "s": status, "p": actor, "id": str(manual_id),
    })
    db.execute(text("""INSERT INTO fc_storage_manual_audit(id,manual_box_id,client_id,warehouse_id,delta,box_count_after,reason,performed_by)
      VALUES(:id,:b,:c,:w,:d,:after,:r,:p)"""), {
        "id": str(uuid.uuid4()), "b": str(manual_id), "c": str(payload.client_id), "w": str(payload.warehouse_id),
        "d": int(payload.delta), "after": after, "r": str(payload.reason or "")[:1000], "p": actor,
    })
    db.commit()
    return {"ok": True, "box_count": after, "removed": after == 0}


@router.delete("/manual-boxes/{manual_id}")
def remove_manual_box(manual_id: uuid.UUID, client_id: uuid.UUID, warehouse_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id)
    install_schema(db)
    row = db.execute(text("SELECT * FROM fc_storage_manual_boxes WHERE id=:id AND CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w FOR UPDATE"), {
        "id": str(manual_id), "c": str(client_id), "w": str(warehouse_id),
    }).mappings().first()
    if not row:
        raise HTTPException(404, detail="Запись хранения не найдена")
    actor = _actor(request)
    db.execute(text("UPDATE fc_storage_manual_boxes SET box_count=0,status='REMOVED',updated_at=now(),performed_by=:p WHERE id=:id"), {
        "p": actor, "id": str(manual_id),
    })
    db.execute(text("""INSERT INTO fc_storage_manual_audit(id,manual_box_id,client_id,warehouse_id,delta,box_count_after,reason,performed_by)
      VALUES(:id,:b,:c,:w,:d,0,'Убрано с хранения',:p)"""), {
        "id": str(uuid.uuid4()), "b": str(manual_id), "c": str(client_id), "w": str(warehouse_id),
        "d": -int(row["box_count"] or 0), "p": actor,
    })
    db.commit()
    return {"ok": True, "removed": True}
