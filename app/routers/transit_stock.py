"""Transit warehouse API: supplier custody, history, search and explicit stock transfers.

FC_R145F18_TRANSIT_REPAIR_API
"""
import io
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.routers.warehouse_ui import scope
from app.services import transit_stock as service

router=APIRouter(prefix="/transit",tags=["Транзитный склад"])


def actor(request:Request):
    value=getattr(request.state,"username",None)
    if not value:raise HTTPException(401,detail="Войдите в WMS")
    return str(value)


class Context(BaseModel):
    client_id:uuid.UUID
    warehouse_id:uuid.UUID


class CreateBatch(Context):
    number:str=Field(default="",max_length=160)
    # partner stays optional for backward compatibility with pre-r145f6 clients.
    # New UI uses supplier + lot_number and asks destination only at departure.
    partner:str=Field(default="",max_length=200)
    supplier:str=Field(default="",max_length=200)
    lot_number:str=Field(default="",max_length=160)


class CreateBoxes(Context):
    count:int=Field(default=1,ge=1,le=100,strict=True)


class AttachExistingBox(Context):
    code:str=Field(min_length=1,max_length=255)


class Scan(Context):
    event_id:uuid.UUID
    barcode:str=Field(min_length=1,max_length=80)


class Manual(Context):
    event_id:uuid.UUID
    article:str=Field(min_length=1,max_length=120)
    size:str=Field(min_length=1,max_length=80)
    name:str=Field(default="",max_length=200)


class Qty(Context):
    event_id:uuid.UUID
    quantity:int=Field(ge=0,le=1000000,strict=True)


class Action(Context):
    event_id:uuid.UUID
    destination:str=Field(default="",max_length=200)
    note:str=Field(default="",max_length=500)


class TransferIn(Context):
    event_id:uuid.UUID
    source:Literal["MAIN","FBS"]
    barcode:str=Field(min_length=1,max_length=80)
    quantity:int=Field(default=1,ge=1,le=1000000,strict=True)


class TransferOut(Context):
    event_id:uuid.UUID
    target:Literal["MAIN","FBS"]
    quantity:int=Field(default=1,ge=1,le=1000000,strict=True)


@router.get("/batches")
def batches(client_id:uuid.UUID,warehouse_id:uuid.UUID,status:str|None=None,q:str="",supplier_id:uuid.UUID|None=None,limit:int=100,db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id)
    return {"rows":service.list_batches(db,client_id,warehouse_id,limit,status,search=q,supplier_id=supplier_id)}


@router.get("/suppliers")
def suppliers(client_id:uuid.UUID,warehouse_id:uuid.UUID,q:str="",limit:int=200,db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id)
    return {"rows":service.list_suppliers(db,client_id,warehouse_id,q,limit)}


@router.get("/suppliers/{supplier_id}")
def supplier_detail(supplier_id:uuid.UUID,client_id:uuid.UUID,warehouse_id:uuid.UUID,db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id)
    return service.supplier_detail(db,supplier_id,client_id,warehouse_id)


@router.get("/suppliers/{supplier_id}/export.xlsx")
def supplier_export(supplier_id:uuid.UUID,client_id:uuid.UUID,warehouse_id:uuid.UUID,db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id)
    raw=service.supplier_export_xlsx(db,supplier_id,client_id,warehouse_id)
    return StreamingResponse(io.BytesIO(raw),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":'attachment; filename="transit_supplier.xlsx"'})


@router.post("/batches")
def create_batch(payload:CreateBatch,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.create_batch(db,payload.client_id,payload.warehouse_id,payload.number,payload.partner,actor(request),payload.supplier,payload.lot_number);db.commit();return result
    except Exception:db.rollback();raise


@router.get("/batches/{batch_id}")
def batch_detail(batch_id:uuid.UUID,client_id:uuid.UUID,warehouse_id:uuid.UUID,db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id)
    return service.batch_public(db,service._batch(db,batch_id,client_id,warehouse_id),include_items=True)


@router.post("/batches/{batch_id}/boxes")
def create_boxes(batch_id:uuid.UUID,payload:CreateBoxes,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.create_boxes(db,batch_id,payload.client_id,payload.warehouse_id,payload.count,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.post("/batches/{batch_id}/boxes/attach")
def attach_existing_box(batch_id:uuid.UUID,payload:AttachExistingBox,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.attach_existing_box(db,batch_id,payload.client_id,payload.warehouse_id,payload.code,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.get("/boxes/lookup")
def lookup_box(code:str,client_id:uuid.UUID,warehouse_id:uuid.UUID,db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id)
    box=service._box(db,barcode=code,client_id=client_id,warehouse_id=warehouse_id)
    result=service.box_public(db,box);result.update({"batch_number":box["batch_number"],"partner":box["partner"],"batch_status":box["batch_status"]});return result


@router.delete("/boxes/{box_id}")
def delete_empty_box(box_id:uuid.UUID,payload:Action,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.delete_empty_box(db,box_id,payload.client_id,payload.warehouse_id,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.post("/boxes/{box_id}/scan")
def scan(box_id:uuid.UUID,payload:Scan,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.scan_product(db,box_id,payload.client_id,payload.warehouse_id,payload.barcode,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.post("/boxes/{box_id}/manual")
def manual(box_id:uuid.UUID,payload:Manual,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.manual_product(db,box_id,payload.client_id,payload.warehouse_id,payload.article,payload.size,payload.name,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.patch("/boxes/{box_id}/items/{item_id}")
def set_quantity(box_id:uuid.UUID,item_id:uuid.UUID,payload:Qty,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.set_item_quantity(db,box_id,item_id,payload.client_id,payload.warehouse_id,payload.quantity,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.delete("/boxes/{box_id}/items/{item_id}")
def admin_remove_item(box_id:uuid.UUID,item_id:uuid.UUID,payload:Action,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.admin_remove_item(db,box_id,item_id,payload.client_id,payload.warehouse_id,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.post("/batches/{batch_id}/already-absent")
def acknowledge_already_absent(batch_id:uuid.UUID,payload:Action,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.acknowledge_already_absent(db,batch_id,payload.client_id,payload.warehouse_id,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise




@router.post("/boxes/{box_id}/transfer-in")
def transfer_inventory_to_transit(box_id:uuid.UUID,payload:TransferIn,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.transfer_inventory_to_transit(db,box_id,payload.client_id,payload.warehouse_id,payload.source,payload.barcode,payload.quantity,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.post("/boxes/{box_id}/items/{item_id}/transfer-out")
def transfer_transit_to_inventory(box_id:uuid.UUID,item_id:uuid.UUID,payload:TransferOut,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.transfer_transit_to_inventory(db,box_id,item_id,payload.client_id,payload.warehouse_id,payload.target,payload.quantity,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.post("/boxes/{box_id}/close")
def close_box(box_id:uuid.UUID,payload:Action,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.set_box_status(db,box_id,payload.client_id,payload.warehouse_id,True,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.post("/boxes/{box_id}/reopen")
def reopen_box(box_id:uuid.UUID,payload:Action,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.set_box_status(db,box_id,payload.client_id,payload.warehouse_id,False,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.post("/boxes/{box_id}/replace")
def replace_box(box_id:uuid.UUID,payload:Action,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.replace_box(db,box_id,payload.client_id,payload.warehouse_id,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.get("/boxes/{box_id}/label.pdf")
def box_label(box_id:uuid.UUID,client_id:uuid.UUID,warehouse_id:uuid.UUID,db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id);raw=service.box_label_pdf(db,box_id,client_id,warehouse_id)
    return StreamingResponse(io.BytesIO(raw),media_type="application/pdf",headers={"Content-Disposition":'inline; filename="transit_box_qr.pdf"'})


@router.post("/batches/{batch_id}/send")
def send_batch(batch_id:uuid.UUID,payload:Action,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.send_batch(db,batch_id,payload.client_id,payload.warehouse_id,payload.event_id,actor(request),payload.destination,payload.note);db.commit();return result
    except Exception:db.rollback();raise


@router.post("/batches/{batch_id}/writeoff")
def writeoff_sent_batch(batch_id:uuid.UUID,payload:Action,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.writeoff_sent_batch(db,batch_id,payload.client_id,payload.warehouse_id,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.post("/batches/{batch_id}/cancel")
def cancel_batch(batch_id:uuid.UUID,payload:Action,request:Request,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:
        result=service.cancel_batch(db,batch_id,payload.client_id,payload.warehouse_id,payload.event_id,actor(request));db.commit();return result
    except Exception:db.rollback();raise


@router.get("/batches/{batch_id}/labels.pdf")
def labels(batch_id:uuid.UUID,client_id:uuid.UUID,warehouse_id:uuid.UUID,db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id);raw=service.box_labels_pdf(db,batch_id,client_id,warehouse_id)
    return StreamingResponse(io.BytesIO(raw),media_type="application/pdf",headers={"Content-Disposition":'inline; filename="transit_boxes.pdf"'})


@router.get("/batches/{batch_id}/export.xlsx")
def export(batch_id:uuid.UUID,client_id:uuid.UUID,warehouse_id:uuid.UUID,db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id);raw=service.export_xlsx(db,batch_id,client_id,warehouse_id)
    return StreamingResponse(io.BytesIO(raw),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":'attachment; filename="transit_manifest.xlsx"'})
