import io
import uuid
from datetime import date
from fastapi import APIRouter,Depends,File,Form,UploadFile,HTTPException
from fastapi.responses import Response,StreamingResponse
from pydantic import BaseModel,Field
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.routers.warehouse_ui import scope
from app.services import receiving_backup as backup
from app.services.local_catalog import catalog

router=APIRouter(prefix='/receiving/native',tags=['Единая приёмка'])

class Draft(BaseModel):
    client_id:uuid.UUID
    warehouse_id:uuid.UUID
    data:dict

@router.post('/draft/backup.xlsx')
def export_draft(payload:Draft,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    try:data=backup.normalize(payload.data,payload.client_id,payload.warehouse_id)
    except ValueError as e:raise HTTPException(422,detail=str(e))
    return Response(backup.dump(data),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="FulfillCore_receiving_draft.xlsx"'})

@router.post('/draft/restore')
def restore_draft(client_id:uuid.UUID=Form(...),warehouse_id:uuid.UUID=Form(...),file:UploadFile=File(...),db:Session=Depends(get_db)):
    scope(db,client_id,warehouse_id)
    try:return backup.restore(file.file.read(8*1024*1024+1),client_id,warehouse_id)
    except ValueError as e:raise HTTPException(422,detail=str(e))

class Labels(BaseModel):
    client_id:uuid.UUID
    warehouse_id:uuid.UUID
    kind:str='BOX'
    code:str=Field(default='',max_length=120)
    batch:str=Field(default='P1',max_length=40)
    start:int=Field(default=1,ge=1,le=999999)
    count:int=Field(default=1,ge=1,le=200)
    label_date:date=Field(default_factory=date.today)

from app.services.receiving_labels import make_labels

@router.post('/labels.pdf')
def labels(payload:Labels,db:Session=Depends(get_db)):
    scope(db,payload.client_id,payload.warehouse_id)
    product=None
    if payload.kind=='PRODUCT':
        from app.services.local_catalog_rules import barcode
        try:code=barcode(payload.code)
        except ValueError as e:raise HTTPException(422,detail=str(e))
        rows=catalog(db,payload.client_id,code)
        if rows and not rows[0].get('wb_conflict') and not rows[0].get('duplicate_sku'):product=rows[0]
    try:pdf=make_labels(payload,product)
    except ValueError as e:raise HTTPException(422,detail=str(e))
    return Response(pdf,media_type='application/pdf',headers={'Content-Disposition':'inline; filename="FulfillCore_labels_58x40.pdf"'})
