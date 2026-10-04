"""r145f22: bounded cell reads and exact-place, all-or-nothing receipt cancellation."""
from __future__ import annotations
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import text, bindparam
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.r145f22_rules import qty, fingerprint, check_bucket

router = APIRouter(prefix="/r145f22", tags=["Ячейка · отмена приёмки"])
log = logging.getLogger(__name__)
MAX_OPERATIONS = 200
MAX_LINES = 5000

def install_schema(db):
    """Add audit only. No backfill, no changes to balances or historical rows."""
    db.execute(text("""CREATE TABLE IF NOT EXISTS fc_r145f22_receipt_cancels (
      source_operation_id uuid PRIMARY KEY,
      client_id uuid NOT NULL, warehouse_id uuid NOT NULL, batch_id uuid NOT NULL,
      reversal_operation_id uuid NOT NULL, quantity numeric(18,3) NOT NULL,
      actor text NOT NULL, reason text NOT NULL, snapshot jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    )"""))
    db.execute(text("""CREATE INDEX IF NOT EXISTS ix_fc_r145f22_cancel_scope
       ON fc_r145f22_receipt_cancels(client_id,warehouse_id,created_at DESC)"""))
    db.commit()

def _scope(db, c, w):
    if not db.execute(text("SELECT 1 FROM clients WHERE id=:c"), {"c":str(c)}).scalar():
        raise HTTPException(404, detail="Клиент не найден")
    if not db.execute(text("SELECT 1 FROM warehouses WHERE id=:w"), {"w":str(w)}).scalar():
        raise HTTPException(404, detail="Склад не найден")

def _actor(request):
    # Main middleware authenticates fc_session; never accept an actor from the body.
    name = getattr(request.state, "username", None)
    if not name:
        raise HTTPException(403, detail="Для отмены нужен вход администратора")
    return str(name)

def _exists(db, name):
    return bool(db.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name":"public."+name}).scalar())

def _query(db, sql, args, expanding=None):
    stmt = text(sql)
    if expanding:
        stmt = stmt.bindparams(*(bindparam(k, expanding=True) for k in expanding))
    return db.execute(stmt, args)

def cell_contents(db, c, w, location_id):
    """Only the selected address and local SKU metadata. No WB, repair or history scan."""
    _scope(db, c, w)
    db.execute(text("SET LOCAL statement_timeout='10000ms'"))
    location = db.execute(text("""SELECT id,warehouse_id,code,name,barcode,location_type
       FROM locations WHERE id=:loc AND warehouse_id=:w AND is_active=TRUE"""),
       {"loc":str(location_id),"w":str(w)}).mappings().first()
    if not location:
        raise HTTPException(404, detail="Адрес не найден на выбранном складе")
    rows = db.execute(text("""SELECT b.id balance_id,b.sku_id,b.location_id,b.container_id,b.quantity,
       s.article,s.sku_code,s.size,s.name,s.barcode,
       COALESCE(to_jsonb(s)->>'product_id','') product_id,
       COALESCE(NULLIF(cn.name,''),NULLIF(cn.barcode,''),'Короб без названия') container,
       cn.status container_status,
       (cn.id IS NULL OR cn.location_id=b.location_id) container_location_matches
       FROM inventory_balances b
       JOIN skus s ON s.id=b.sku_id AND s.client_id=b.client_id
       LEFT JOIN containers cn ON cn.id=b.container_id AND cn.client_id=b.client_id AND cn.warehouse_id=b.warehouse_id
       WHERE b.client_id=:c AND b.warehouse_id=:w AND b.location_id=:loc AND b.quantity>0
       ORDER BY cn.name,cn.barcode,s.article,s.size,s.barcode,b.id LIMIT 20001"""),
       {"c":str(c),"w":str(w),"loc":str(location_id)}).mappings().all()
    if len(rows)>20000:
        raise HTTPException(413, detail="В адресе более 20 000 строк. Используйте выгрузку остатков; данные не обрезаны.")
    items=[]
    for raw in rows:
        r=dict(raw)
        for key in ("balance_id","sku_id","location_id","container_id"):
            r[key]=str(r[key]) if r[key] is not None else None
        r["quantity"]=str(qty(r["quantity"]))
        r["article"]=r["article"] or r["sku_code"] or ""
        r["name"]=r["name"] or r["article"]
        r["container"]=r["container"] if r["container_id"] else ""
        # Unknown availability is deliberately NOT encoded as 0 or full quantity.
        r["availability_checked"]=False
        items.append(r)
    return {"location":{k:str(v) if isinstance(v,uuid.UUID) else v for k,v in dict(location).items()},
        "client_id":str(c),"warehouse_id":str(w),"items":items,
        "summary":{"quantity":str(sum((qty(x["quantity"]) for x in items),Decimal(0))),
          "sku_count":len({x["sku_id"] for x in items}),
          "container_count":len({x["container_id"] for x in items if x["container_id"]})},
        "observed_at":datetime.now(timezone.utc).isoformat(),"source":"physical_local",
        "availability_checked":False}

@router.get("/locations/{location_id}/contents")
def get_cell(location_id:uuid.UUID,client_id:uuid.UUID,warehouse_id:uuid.UUID,db:Session=Depends(get_db)):
    try:
        return cell_contents(db,client_id,warehouse_id,location_id)
    except SQLAlchemyError:
        db.rollback()
        log.exception("R145F22_CELL_READ_FAILED")
        raise HTTPException(503,detail="Не удалось прочитать этот адрес. Данные не заменены нулями. Повторите загрузку.")

class CancelPreview(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    operation_ids: list[uuid.UUID] = Field(min_length=1,max_length=MAX_OPERATIONS)

class CancelConfirm(CancelPreview):
    fingerprint: str = Field(min_length=64,max_length=64)
    confirmed: bool = False
    reason: str = Field(min_length=3,max_length=500)

def _ids(payload):
    ids=sorted({str(x) for x in payload.operation_ids})
    if len(ids)!=len(payload.operation_ids):
        raise HTTPException(422,detail="Одна приёмка указана дважды")
    return ids

def _operations(db,c,w,ids,lock=False):
    sql="""SELECT o.id,o.status,o.operation_type,o.reference,o.performed_by,o.created_at,o.idempotency_key
        FROM inventory_operations o WHERE o.client_id=:c AND o.warehouse_id=:w
        AND o.id IN :ids ORDER BY o.id"""
    if lock:sql+=" FOR UPDATE"
    rows=_query(db,sql,{"c":str(c),"w":str(w),"ids":ids},["ids"]).mappings().all()
    if {str(x["id"]) for x in rows}!=set(ids):
        raise HTTPException(404,detail="Часть приёмок не найдена у выбранного клиента на этом складе")
    return [dict(x) for x in rows]

def _plan(db,c,w,ids,lock=False):
    ops=_operations(db,c,w,ids,lock)
    cancelled=[x for x in ops if str(x["status"]).upper()=="CANCELLED"]
    if cancelled:
        if not _exists(db,"fc_r145f22_receipt_cancels"):
            raise HTTPException(409, detail="Приёмка уже отменена. Повторное списание запрещено.")
        audits=_query(db,"""SELECT a.source_operation_id,a.reversal_operation_id,a.quantity,a.reason,a.actor,
            COALESCE(o.status,'') reversal_status
            FROM fc_r145f22_receipt_cancels a
            LEFT JOIN inventory_operations o ON o.id=a.reversal_operation_id
            WHERE a.client_id=:c AND a.warehouse_id=:w AND a.source_operation_id IN :ids""",
            {"c":str(c),"w":str(w),"ids":ids},["ids"]).mappings().all()
        if len(cancelled)==len(ops) and len(audits)==len(ops) and all(x["reversal_status"]=="COMPLETED" for x in audits):
            return {"already_cancelled":True,"can_cancel":False,"operation_ids":ids,
                "quantity":str(sum((qty(x["quantity"]) for x in audits),Decimal(0))),
                "reversal_operation_ids":[str(x["reversal_operation_id"]) for x in audits],
                "operations":[_public_op(x) for x in ops],"buckets":[],"fingerprint":""}
        raise HTTPException(409,detail="Часть приёмки уже отменена другим способом. Обновите историю и выберите оставшиеся операции.")
    if any(str(x["operation_type"]).upper() not in ("RECEIVING","RECEIVING_IMPORT") or
           str(x["status"]).upper()!="COMPLETED" for x in ops):
        raise HTTPException(409,detail="Здесь можно отменить только проведённую приёмку. Черновик редактируется в приёмке.")
    old_reversal=_query(db,"""SELECT id FROM inventory_operations WHERE client_id=:c AND warehouse_id=:w
        AND idempotency_key IN :keys LIMIT 1""",
        {"c":str(c),"w":str(w),"keys":["cancel-receiving-"+x for x in ids]},["keys"]).scalar()
    if old_reversal:
        raise HTTPException(409,detail="Обратная операция уже существует. Повторное списание заблокировано.")
    lines=_query(db,"""SELECT id,operation_id,sku_id,quantity,source_location_id,source_container_id,
        destination_location_id,destination_container_id FROM inventory_operation_lines
        WHERE operation_id IN :ids ORDER BY operation_id,id LIMIT 5001""",{"ids":ids},["ids"]).mappings().all()
    if len(lines)>MAX_LINES or not lines:
        raise HTTPException(409,detail="Приёмка пуста или содержит более 5000 строк; отмена не выполнена.")
    if {str(x["operation_id"]) for x in lines}!=set(ids):
        raise HTTPException(409,detail="В одной из выбранных приёмок нет строк")
    by_op={str(x["id"]):x for x in ops}
    buckets={}
    for line in lines:
        if line["source_location_id"] is not None or line["source_container_id"] is not None or line["destination_location_id"] is None or qty(line["quantity"])<=0:
            raise HTTPException(409,detail="Смешанную операцию нельзя отменять как обычную приёмку.")
        key=(str(line["sku_id"]),str(line["destination_location_id"]),str(line["destination_container_id"] or ""))
        b=buckets.setdefault(key,{"sku_id":key[0],"location_id":key[1],"container_id":key[2] or None,
            "quantity":Decimal(0),"parts":[],"since":by_op[str(line["operation_id"])]["created_at"]})
        b["quantity"]+=qty(line["quantity"])
        b["since"]=min(b["since"],by_op[str(line["operation_id"])]["created_at"])
        b["parts"].append({"line_id":str(line["id"]),"operation_id":str(line["operation_id"]),"quantity":str(qty(line["quantity"]))})
    if lock:
        box_ids=sorted({k[2] for k in buckets if k[2]})
        if box_ids:
            _query(db,"SELECT id FROM containers WHERE id IN :ids ORDER BY id FOR UPDATE",{"ids":box_ids},["ids"]).all()
    from app.services.stock_core_v2 import canonical_stock_identity_sku_ids, active_order_floor_for_sku
    identity_cache={}
    demand_cache={}
    output=[]
    for key,b in sorted(buckets.items()):
        p={"c":str(c),"w":str(w),"s":key[0],"loc":key[1],"box":key[2],"since":b["since"],"ids":ids}
        place=db.execute(text("""SELECT l.code,l.is_active location_active,
           s.article,s.size,s.barcode,s.is_active sku_active
           FROM locations l CROSS JOIN skus s
           WHERE l.id=:loc AND l.warehouse_id=:w AND s.id=:s AND s.client_id=:c"""),p).mappings().first()
        b.update(dict(place) if place else {"location_active":False,"sku_active":False})
        b["location"]=b.pop("code",key[1]); b["container"]=""
        b["container_valid"]=True
        if key[2]:
            box=db.execute(text("""SELECT name,barcode,status,location_id FROM containers
               WHERE CAST(id AS text)=:box AND client_id=:c AND warehouse_id=:w"""),p).mappings().first()
            b["container_valid"]=bool(box and box["status"]=="ACTIVE" and str(box["location_id"])==key[1])
            b["container"]=(box["name"] or box["barcode"]) if box else "Короб не найден"
        bal=db.execute(text("""SELECT id,quantity,version FROM inventory_balances
          WHERE client_id=:c AND warehouse_id=:w AND sku_id=:s AND location_id=:loc
          AND COALESCE(CAST(container_id AS text),'')=:box"""+(" FOR UPDATE" if lock else "")),p).mappings().all()
        if len(bal)>1:
            raise HTTPException(409,detail="Дублирующий остаток в адресе. Нужна сверка, отмена остановлена.")
        bal=bal[0] if bal else None
        mirror=db.execute(text("""SELECT storage_only,fbs_available,reserved,version FROM fc_stocks_v2
          WHERE client_id=:c AND warehouse_id=:w AND sku_id=:s AND location_id=:loc
          AND container_key=:box"""+(" FOR UPDATE" if lock else "")),p).mappings().first()
        b.update(physical=qty(bal["quantity"] if bal else 0),balance_id=str(bal["id"]) if bal else None,
          balance_version=int(bal["version"]) if bal else 0,mirror_found=mirror is not None,
          main=qty(mirror["storage_only"] if mirror else 0),fbs=qty(mirror["fbs_available"] if mirror else 0),
          mirror_reserved=qty(mirror["reserved"] if mirror else 0),mirror_version=int(mirror["version"]) if mirror else 0)
        # Canonical aliases and active unbound orders must not escape the check.
        if key[0] not in identity_cache:
            identity_cache[key[0]]=sorted(str(x) for x in canonical_stock_identity_sku_ids(
                db,client_id=c,sku_id=key[0]))
            demand_cache[key[0]]=qty(active_order_floor_for_sku(
                db,client_id=c,warehouse_id=w,sku_id=key[0]).get("total",0))
        p["aliases"]=identity_cache[key[0]]
        b["identity_ids"]=p["aliases"]
        b["active_order_floor"]=demand_cache[key[0]]
        # Block exact-address reservations AND unbound promises for this identity.
        b["reservation_qty"]=qty(_query(db,"""SELECT COALESCE(SUM(quantity),0) FROM fc_stock_reservations
          WHERE client_id=:c AND warehouse_id=:w AND sku_id IN :aliases
          AND status IN ('SALE_RESERVED','ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
          AND (NULLIF(CAST(source_location_id AS text),'') IS NULL OR
             (CAST(source_location_id AS text)=:loc AND COALESCE(CAST(source_container_id AS text),'')=:box))""",p,["aliases"]).scalar())
        b["pick_qty"]=qty(_query(db,"""SELECT COALESCE(SUM(GREATEST(l.allocated_qty,l.scan_qty+l.manual_qty+l.missing_qty)),0)
          FROM fc_wh_pick_lines l JOIN fc_wh_pick_tasks t ON t.id=l.task_id
          WHERE t.client_id=:c AND t.warehouse_id=:w AND l.sku_id IN :aliases AND l.location_id=:loc
          AND COALESCE(l.container_id,'')=:box AND t.status IN ('DRAFT','ASSIGNED','IN_PROGRESS','READY_FOR_ADMIN')""",p,["aliases"]).scalar())
        b["external_pick_qty"]=qty(_query(db,"""SELECT COALESCE(SUM(quantity),0) FROM fc_external_pick_allocations
          WHERE client_id=:c AND warehouse_id=:w AND sku_id IN :aliases AND location_id=:loc
          AND COALESCE(container_id,'')=:box AND status='ACTIVE'""",p,["aliases"]).scalar())
        outgoing=_query(db,"""SELECT CAST(o.id AS text) id FROM inventory_operation_lines l
          JOIN inventory_operations o ON o.id=l.operation_id
          WHERE o.client_id=:c AND o.warehouse_id=:w AND l.sku_id=:s
            AND l.source_location_id=:loc AND COALESCE(CAST(l.source_container_id AS text),'')=:box
            AND o.created_at>=:since AND o.id NOT IN :ids
            AND COALESCE(o.status,'COMPLETED') IN ('COMPLETED','CANCELLED')
          ORDER BY o.created_at DESC,o.id LIMIT 1""",p,["ids"]).scalar()
        b["outgoing_after_receipt"]=str(outgoing or "")
        transferred=_query(db,"""SELECT 1 FROM fc_wh_receipt_fbs f
          WHERE f.line_id IN :lines AND (COALESCE(f.quantity,0)>0 OR COALESCE(f.reloadable_quantity,0)>0) LIMIT 1""",
          {"lines":[x["line_id"] for x in b["parts"]]},["lines"]).scalar()
        b["transferred"]=bool(transferred)
        b["issues"]=check_bucket(b)
        output.append(b)
    public={"already_cancelled":False,"operation_ids":ids,"operations":[_public_op(x) for x in ops],
       "buckets":output,"quantity":str(sum((b["quantity"] for b in output),Decimal(0))),
       "can_cancel":all(not b["issues"] for b in output)}
    public["fingerprint"]=fingerprint(public)
    return public

def _public_op(op):
    return {k:str(v) if isinstance(v,uuid.UUID) else v for k,v in op.items()}

def preview_core(db,payload):
    _scope(db,payload.client_id,payload.warehouse_id)
    db.execute(text("SET LOCAL statement_timeout='45000ms'"))
    return _plan(db,payload.client_id,payload.warehouse_id,_ids(payload))

@router.post("/receipts/cancel/preview")
def cancel_preview(payload:CancelPreview,request:Request,db:Session=Depends(get_db)):
    _actor(request)
    try:
        return preview_core(db,payload)
    except SQLAlchemyError:
        db.rollback();log.exception("R145F22_CANCEL_PREVIEW_FAILED")
        raise HTTPException(503,detail="Проверка приёмки не завершена. Ничего не изменено. Повторите позже.")
    except ValueError as e:
        db.rollback();raise HTTPException(409,detail=str(e))

def _update_documents(db,c,w,op):
    """Keep document rows and scan timestamps; only mark the linked document cancelled."""
    args={"c":str(c),"w":str(w),"op":str(op["id"])}
    if _exists(db,"fc_structured_receipts"):
        db.execute(text("""UPDATE fc_structured_receipts SET status='CANCELLED'
          WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w
          AND CAST(committed_operation_id AS text)=:op"""),args)
    if _exists(db,"fc_pvz_batches"):
        db.execute(text("""UPDATE fc_pvz_batches SET status='CANCELLED'
          WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w
          AND CAST(stock_operation_id AS text)=:op"""),args)
    if _exists(db,"fc_pvz_receiving_sessions"):
        db.execute(text("""UPDATE fc_pvz_receiving_sessions SET status='CANCELLED',cancelled_at=now()
          WHERE CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w
          AND CAST(stock_operation_id AS text)=:op"""),args)
    idem=str(op["idempotency_key"] or "")
    if idem.startswith("R145F19:TSD:") and _exists(db,"fc_r145f19_tsd_receipts"):
        rid=str(uuid.UUID(idem.split(":",2)[2]))
        db.execute(text("""UPDATE fc_r145f19_tsd_receipts SET status='CANCELLED'
          WHERE id=:rid AND CAST(client_id AS text)=:c AND CAST(warehouse_id AS text)=:w"""),{**args,"rid":rid})

def confirm_core(db,payload,actor):
    import json
    from app.services.stock_actions import locks
    from app.services.inventory import execute_operation
    from app.schemas.inventory import OperationCreate, StockLine
    c,w=payload.client_id,payload.warehouse_id
    ids=_ids(payload)
    if not payload.confirmed or len(payload.reason.strip())<3:
        raise HTTPException(422,detail="Укажите причину и подтвердите отмену")
    try:
        _scope(db,c,w)
        db.execute(text("SET LOCAL statement_timeout='90000ms'"))
        # Scope and shared stock locks first, then source documents, boxes and balances.
        _operations(db,c,w,ids)
        sku_ids=_query(db,"SELECT DISTINCT sku_id FROM inventory_operation_lines WHERE operation_id IN :ids",
          {"ids":ids},["ids"]).scalars().all()
        locks(db,str(c),str(w),sorted(str(x) for x in sku_ids))
        plan=_plan(db,c,w,ids,lock=True)
        if plan["already_cancelled"]:
            db.rollback()
            return {"ok":True,"replayed":True,**plan}
        if not plan["can_cancel"]:
            raise HTTPException(409,detail={"message":"Отмена заблокирована. Остатки не изменены.","buckets":jsonable_encoder(plan["buckets"])})
        if plan["fingerprint"]!=payload.fingerprint:
            raise HTTPException(409,detail="Данные изменились после проверки. Ничего не списано; нажмите «Проверить заново».")
        batch_id=str(uuid.uuid4())
        reverse_ids=[]
        for op in plan["operations"]:
            lines=[]
            for b in plan["buckets"]:
                q=sum((qty(x["quantity"]) for x in b["parts"] if x["operation_id"]==str(op["id"])),Decimal(0))
                if q:
                    lines.append(StockLine(sku_id=uuid.UUID(b["sku_id"]),quantity=q,
                      source_location_id=uuid.UUID(b["location_id"]),
                      source_container_id=uuid.UUID(b["container_id"]) if b["container_id"] else None,
                      reason="CANCEL_RECEIVING:"+str(op["id"])))
            rev=execute_operation(db,OperationCreate(client_id=c,warehouse_id=w,operation_type="ADJUSTMENT",
              idempotency_key="cancel-receiving-"+str(op["id"]),performed_by=actor,
              reference=("Отмена: "+str(op["reference"] or op["id"]))[:255],
              comment="Отмена ошибочной приёмки; "+payload.reason.strip(),
              lines=lines),commit=False)
            reverse_ids.append(str(rev.id))
            db.execute(text("UPDATE inventory_operations SET status='CANCELLED' WHERE id=:id AND client_id=:c AND warehouse_id=:w"),
              {"id":str(op["id"]),"c":str(c),"w":str(w)})
            _update_documents(db,c,w,op)
            audit_buckets=[]
            for b in plan["buckets"]:
                parts=[x for x in b["parts"] if x["operation_id"]==str(op["id"])]
                if parts:
                    audit_buckets.append({**b,"parts":parts,
                        "quantity":str(sum((qty(x["quantity"]) for x in parts),Decimal(0))),
                        "batch_quantity":str(b["quantity"])})
            audit_snapshot={"operation":op,"buckets":audit_buckets,"selection_fingerprint":plan["fingerprint"],
                "batch_id":batch_id,"quantity":str(sum((x.quantity for x in lines),Decimal(0)))}
            db.execute(text("""INSERT INTO fc_r145f22_receipt_cancels
              (source_operation_id,client_id,warehouse_id,batch_id,reversal_operation_id,quantity,actor,reason,snapshot)
              VALUES(:op,:c,:w,:batch,:rev,:q,:actor,:reason,CAST(:snapshot AS jsonb))"""),
              {"op":str(op["id"]),"c":str(c),"w":str(w),"batch":batch_id,"rev":str(rev.id),
               "q":sum((x.quantity for x in lines),Decimal(0)),"actor":actor,
               "reason":payload.reason.strip(),"snapshot":json.dumps(audit_snapshot,default=str,ensure_ascii=False)})
        # Exact mirror deltas, not lazy reclassification and never reducing a reserve.
        for b in plan["buckets"]:
            p={"c":str(c),"w":str(w),"s":b["sku_id"],"loc":b["location_id"],"box":b["container_id"] or "",
               "q":qty(b["quantity"]),"remaining":qty(b["physical"])-qty(b["quantity"]),"version":b["mirror_version"]}
            changed=db.execute(text("""UPDATE fc_stocks_v2
               SET storage_only=storage_only-:q,physical_snapshot=:remaining,version=version+1,updated_at=now()
               WHERE client_id=:c AND warehouse_id=:w AND sku_id=:s AND location_id=:loc AND container_key=:box
                 AND version=:version AND storage_only>=:q AND fbs_available=0 AND reserved=0"""),p)
            if changed.rowcount!=1:
                raise HTTPException(409,detail="Классификация остатка изменилась. Вся отмена откачена.")
        db.commit()
        return {"ok":True,"replayed":False,"operation_ids":ids,"reversal_operation_ids":reverse_ids,
                "quantity":plan["quantity"],"batch_id":batch_id}
    except Exception:
        db.rollback()
        raise

@router.post("/receipts/cancel/confirm")
def cancel_confirm(payload:CancelConfirm,request:Request,db:Session=Depends(get_db)):
    actor=_actor(request)
    try:
        return confirm_core(db,payload,actor)
    except SQLAlchemyError:
        log.exception("R145F22_CANCEL_CONFIRM_FAILED")
        raise HTTPException(503,detail="Отмена не подтверждена. Обновите историю и повторите проверку той же приёмки; повторное списание защищено.")
    except ValueError as e:
        raise HTTPException(409,detail=str(e))
