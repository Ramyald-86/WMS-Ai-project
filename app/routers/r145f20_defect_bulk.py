"""r145f20: atomic, scoped bulk write-off from the separate defect ledger only."""
from __future__ import annotations
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.routers.warehouse_ui import scope

router = APIRouter(prefix="/defect-storage/bulk", tags=["Брак · массовое списание"])
log = logging.getLogger(__name__)
MAX_LINES = 50000


class PreviewRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    all_active: bool = False
    line_ids: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_LINES)


class ConfirmRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    preview_id: uuid.UUID
    confirmed: bool = False
    reason: str = Field(default="", max_length=1000)
    performed_by: str = Field(min_length=1, max_length=160)


def install_schema(db: Session):
    # Additive technical snapshots. Existing defect, stock and receipt rows are untouched.
    db.execute(text("""CREATE TABLE IF NOT EXISTS fc_r145f20_defect_bulk (
        id uuid PRIMARY KEY,client_id uuid NOT NULL,warehouse_id uuid NOT NULL,
        status varchar(20) NOT NULL DEFAULT 'PREVIEW',
        lines jsonb NOT NULL,total_qty numeric(18,3) NOT NULL,
        lines_count integer NOT NULL,boxes_count integer NOT NULL DEFAULT 0,
        reason text NOT NULL DEFAULT '',performed_by text NOT NULL DEFAULT '',
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL,completed_at timestamptz NULL
    )"""))
    db.execute(text("""CREATE INDEX IF NOT EXISTS ix_fc_r145f20_defect_bulk_scope
        ON fc_r145f20_defect_bulk(client_id,warehouse_id,status,completed_at DESC)"""))
    db.commit()


def _remaining(row):
    return max(Decimal("0"), Decimal(str(row["quantity"]))-Decimal(str(row["written_off_qty"] or 0)))


def _snapshot(rows):
    if not rows:
        raise HTTPException(409, detail="Активного брака для списания нет. Обновите список.")
    if len(rows) > MAX_LINES:
        raise HTTPException(422, detail="Слишком много позиций. Выполните списание частями.")
    return [{"id": str(r["id"]), "batch_id": str(r["batch_id"]),
             "container_id": str(r["container_id"]) if r.get("container_id") else None,
             "quantity": str(_remaining(r))} for r in rows]


def _check_snapshot(snapshot, current):
    by_id = {str(x["id"]): x for x in current}
    if len(by_id) != len(snapshot):
        raise HTTPException(409, detail="Состав брака изменился. Ничего не списано. Обновите список и подтвердите заново.")
    for expected in snapshot:
        row = by_id.get(expected["id"])
        if (not row or str(row["batch_id"]) != expected["batch_id"]
                or (str(row.get("container_id")) if row.get("container_id") else None) != expected.get("container_id")
                or row.get("deleted_at") or row.get("batch_deleted")
                or _remaining(row) != Decimal(expected["quantity"])
                or _remaining(row) <= 0):
            raise HTTPException(409, detail="Количество или статус выбранного брака изменились. Ничего не списано. Обновите список.")
    return sum((Decimal(x["quantity"]) for x in snapshot), Decimal("0"))


@router.post("/preview")
def preview(payload: PreviewRequest, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    ids = sorted({str(x) for x in payload.line_ids})
    if payload.all_active and ids:
        raise HTTPException(422, detail="Выберите весь брак или конкретные позиции, но не оба режима сразу.")
    if not payload.all_active and not ids:
        raise HTTPException(422, detail="Выберите позиции брака.")
    args = {"c": str(payload.client_id), "w": str(payload.warehouse_id)}
    sql = """SELECT l.id,l.batch_id,l.container_id,l.quantity,l.written_off_qty
        FROM fc_pvz_batches b JOIN fc_pvz_lines l ON l.batch_id=b.id
        WHERE b.client_id=:c AND b.warehouse_id=:w AND b.kind='DEFECT'
        AND b.deleted_at IS NULL AND l.deleted_at IS NULL
        AND l.quantity>l.written_off_qty"""
    if ids:
        sql += " AND l.id IN :ids"
        args["ids"] = ids
    stmt = text(sql+" ORDER BY b.id,l.id LIMIT 50001")
    if ids:
        stmt = stmt.bindparams(bindparam("ids", expanding=True))
    rows = db.execute(stmt, args).mappings().all()
    if ids and {str(r["id"]) for r in rows} != set(ids):
        raise HTTPException(409, detail="Часть выбранных позиций уже изменена, удалена или не относится к этому клиенту и складу. Обновите список.")
    frozen = _snapshot(rows)
    total = sum((Decimal(x["quantity"]) for x in frozen), Decimal("0"))
    boxes = len({x["container_id"] for x in frozen if x["container_id"]})
    pid = uuid.uuid4()
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    db.execute(text("""INSERT INTO fc_r145f20_defect_bulk
        (id,client_id,warehouse_id,lines,total_qty,lines_count,boxes_count,expires_at)
        VALUES(:id,:c,:w,CAST(:lines AS jsonb),:qty,:count,:boxes,:expires)"""),
        {**args, "id":str(pid), "lines":json.dumps(frozen), "qty":total,
         "count":len(frozen), "boxes":boxes, "expires":expires})
    db.commit()
    return {"preview_id":str(pid), "quantity":str(total), "lines_count":len(frozen),
            "boxes_count":boxes, "expires_at":expires}


@router.post("/writeoff")
def writeoff(payload: ConfirmRequest, db: Session = Depends(get_db)):
    scope(db, payload.client_id, payload.warehouse_id)
    if not payload.performed_by.strip():
        raise HTTPException(422, detail="Укажите сотрудника")
    if not payload.confirmed:
        raise HTTPException(422, detail="Требуется подтверждение списания.")
    args = {"id":str(payload.preview_id), "c":str(payload.client_id), "w":str(payload.warehouse_id)}
    try:
        db.execute(text("SET LOCAL lock_timeout = '8s'"))
        db.execute(text("SET LOCAL statement_timeout = '90s'"))
        operation = db.execute(text("""SELECT * FROM fc_r145f20_defect_bulk
            WHERE id=:id AND client_id=:c AND warehouse_id=:w FOR UPDATE"""), args).mappings().first()
        if not operation:
            raise HTTPException(404, detail="Подтверждение списания не найдено для этого клиента и склада.")
        if operation["status"] == "DONE":
            result = {"ok":True, "replayed":True, "operation_id":args["id"],
                      "written_off":str(operation["total_qty"]), "lines_count":operation["lines_count"]}
            db.commit()
            return result
        if operation["status"] != "PREVIEW" or operation["expires_at"] < datetime.now(timezone.utc):
            raise HTTPException(409, detail="Подтверждение устарело. Обновите список и подтвердите заново.")
        frozen = operation["lines"]
        if isinstance(frozen, str):
            frozen = json.loads(frozen)
        ids = sorted(x["id"] for x in frozen)
        batches = sorted({x["batch_id"] for x in frozen})
        # Same deterministic batch locks as per-batch operations, before locking rows.
        db.execute(text("""SELECT id FROM fc_pvz_batches
            WHERE client_id=:c AND warehouse_id=:w AND kind='DEFECT' AND id IN :batches
            ORDER BY id FOR UPDATE""").bindparams(bindparam("batches", expanding=True)),
            {**args,"batches":batches}).all()
        current = db.execute(text("""SELECT l.id,l.batch_id,l.container_id,l.quantity,l.written_off_qty,
            l.deleted_at,b.deleted_at AS batch_deleted
            FROM fc_pvz_lines l JOIN fc_pvz_batches b ON b.id=l.batch_id
            WHERE b.client_id=:c AND b.warehouse_id=:w AND b.kind='DEFECT' AND l.id IN :ids
            ORDER BY l.id FOR UPDATE OF l""").bindparams(bindparam("ids", expanding=True)),
            {**args,"ids":ids}).mappings().all()
        total = _check_snapshot(frozen, current)
        db.execute(text("UPDATE fc_pvz_lines SET written_off_qty=quantity WHERE id=:line_id"),
                   [{"line_id":x["id"]} for x in frozen])
        audit = text("""INSERT INTO fc_defect_actions
            (id,client_id,warehouse_id,batch_id,line_id,container_id,action,quantity,reason,performed_by)
            VALUES(:id,:c,:w,:b,:line,:box,'WRITE_OFF_BULK',:qty,:reason,:actor)""")
        reason = payload.reason.strip() or "Массовое списание брака"
        db.execute(audit,[{"id":str(uuid.uuid4()),"c":args["c"],"w":args["w"],
            "b":x["batch_id"],"line":x["id"],"box":x["container_id"],
            "qty":Decimal(x["quantity"]),"reason":reason+" · "+args["id"],
            "actor":payload.performed_by.strip()} for x in frozen])
        db.execute(text("""UPDATE fc_pvz_batches AS b SET status =
            CASE WHEN EXISTS (
                SELECT 1 FROM fc_pvz_lines l WHERE l.batch_id=b.id
                AND l.deleted_at IS NULL AND l.quantity>l.written_off_qty
            ) THEN 'COMPLETED' ELSE 'WRITTEN_OFF' END
            WHERE b.client_id=:c AND b.warehouse_id=:w AND b.kind='DEFECT' AND b.id IN :batches
            """).bindparams(bindparam("batches", expanding=True)),
            {**args,"batches":batches})
        db.execute(text("""UPDATE fc_r145f20_defect_bulk
            SET status='DONE',completed_at=now(),reason=:reason,performed_by=:actor
            WHERE id=:id"""),{**args,"reason":reason,"actor":payload.performed_by.strip()})
        db.commit()
        return {"ok":True,"replayed":False,"operation_id":args["id"],
                "written_off":str(total),"lines_count":len(frozen)}
    except HTTPException:
        db.rollback()
        raise
    except SQLAlchemyError:
        db.rollback()
        log.exception("Defect bulk writeoff rolled back: %s", args["id"])
        raise HTTPException(409, detail="Списание не завершено: позиции заняты другой операцией или возникла ошибка базы. Обновите состояние и повторите проверку.")


@router.get("/history")
def history(client_id: uuid.UUID, warehouse_id: uuid.UUID, db: Session = Depends(get_db)):
    scope(db, client_id, warehouse_id)
    rows = db.execute(text("""SELECT id,completed_at,total_qty,lines_count,boxes_count,performed_by,reason
        FROM fc_r145f20_defect_bulk WHERE client_id=:c AND warehouse_id=:w AND status='DONE'
        ORDER BY completed_at DESC LIMIT 200"""),
        {"c":str(client_id),"w":str(warehouse_id)}).mappings().all()
    return {"rows":[dict(x) for x in rows], "limit":200}
