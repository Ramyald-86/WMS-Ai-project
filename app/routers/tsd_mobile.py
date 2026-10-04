"""Android distribution and a small facade over the current stock action service.

No embedded credentials, independent stock ledger or second receiving workflow.
"""
import hashlib
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Container, Location
from app.routers.warehouse_ui import scope
from app.routers.stock_actions import Apply, apply as apply_stock
from app.services.box_intake_rules import box_code
from app.services.local_catalog_rules import barcode as normalize_barcode

router = APIRouter(tags=['Приложение ТСД'])
ROOT = Path(__file__).resolve().parents[1]
APK_NAME = 'FulfillCore_TSD_1.2.0.apk'
APK = ROOT / 'downloads' / APK_NAME


@router.get('/tsd/install', include_in_schema=False)
def install_page():
    return FileResponse(ROOT / 'static/tsd-install.html')


@router.get('/tsd/login', include_in_schema=False)
def login_page():
    return FileResponse(ROOT / 'static/tsd-login.html')


@router.get('/downloads/tsd/android.apk', include_in_schema=False)
def download_apk():
    if not APK.is_file():
        raise HTTPException(503, detail='APK отсутствует: обновление сервера установлено не полностью')
    return FileResponse(APK, filename=APK_NAME, media_type='application/vnd.android.package-archive',
                        headers={'X-Content-Type-Options': 'nosniff'})


@lru_cache(maxsize=4)
def _apk_hash(path, size, mtime):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@router.get('/tsd/app-info', include_in_schema=False)
def app_info():
    if not APK.is_file():
        raise HTTPException(503, detail='APK ещё не установлен')
    stat = APK.stat()
    return {'version': '1.2.0', 'version_code': 7, 'wms_version': 'r96',
            'min_android': '6.0', 'package': 'ru.fulfillcore.tsd',
            'url': '/downloads/tsd/android.apk', 'filename': APK_NAME,
            'size': stat.st_size, 'sha256': _apk_hash(str(APK), stat.st_size, stat.st_mtime_ns)}


@router.get('/tsd/install-qr.png', include_in_schema=False)
def install_qr(request: Request):
    from app.services.receiving_labels import box_agent_png
    address = str(request.base_url).rstrip('/') + '/tsd/install'
    if len(address) > 120:
        raise HTTPException(400, detail='Слишком длинный адрес сервера')
    return Response(box_agent_png(address), media_type='image/png')


def resolve_box(db, client_id, warehouse_id, raw):
    value = str(raw or '').strip()
    try:
        candidates = {value.lower(), box_code(value).lower()}
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    rows = db.execute(select(Container).where(
        Container.client_id == client_id, Container.warehouse_id == warehouse_id,
        Container.status == 'ACTIVE',
        or_(func.lower(Container.name).in_(candidates), func.lower(Container.barcode).in_(candidates)),
    )).scalars().all()
    if len(rows) != 1:
        raise HTTPException(409 if rows else 404, detail='Найдено несколько коробов' if rows else 'Активный короб не найден на выбранном складе')
    box = rows[0]
    loc = db.get(Location, box.location_id) if box.location_id else None
    if not loc or not loc.is_active or loc.warehouse_id != warehouse_id:
        raise HTTPException(409, detail='Короб необходимо разместить в ячейке или на палете')
    return box, loc


class Move(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    request_id: uuid.UUID
    picker: str = Field(min_length=1, max_length=120)
    source_container: str = Field(min_length=1, max_length=160)
    destination_container: str = Field(min_length=1, max_length=160)
    product_barcode: str = Field(min_length=1, max_length=160)
    quantity: int = Field(ge=1, le=100000)
    stock_class: Literal['MAIN', 'FBS']


@router.post('/tsd/mobile/move')
def move(payload: Move, request: Request, db: Session = Depends(get_db)):
    from app.models import SKU
    from app.services.local_catalog import catalog
    scope(db, payload.client_id, payload.warehouse_id)
    if not payload.picker.strip():
        raise HTTPException(400, detail='Укажите имя сотрудника')
    source, source_loc = resolve_box(db, payload.client_id, payload.warehouse_id, payload.source_container)
    target, target_loc = resolve_box(db, payload.client_id, payload.warehouse_id, payload.destination_container)
    if source.id == target.id:
        raise HTTPException(409, detail='Исходный и конечный короб совпадают')
    try:
        code = normalize_barcode(payload.product_barcode)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    products = catalog(db, payload.client_id, code)
    exact = [p for p in products if str(p.get('barcode', '')) == code]
    if len(exact) != 1 or exact[0].get('duplicate_sku') or exact[0].get('wb_conflict'):
        raise HTTPException(409, detail='ШК не найден однозначно в справочнике клиента')
    ids = exact[0].get('sku_ids') or [exact[0].get('sku_id')]
    if len(ids) != 1 or not ids[0]:
        raise HTTPException(409, detail='Проверьте привязку товара в справочнике')
    sku = db.get(SKU, uuid.UUID(str(ids[0])))
    if not sku or sku.client_id != payload.client_id:
        raise HTTPException(404, detail='Товар не найден')
    result = apply_stock(Apply(
        client_id=payload.client_id, warehouse_id=payload.warehouse_id,
        request_id=payload.request_id, action='MOVE', stock_class=payload.stock_class,
        sku_ids=[sku.id], location_id=source_loc.id, container_id=source.id,
        destination_location_id=target_loc.id, destination_container_id=target.id,
        quantity=payload.quantity, reason='Перемещение ТСД · ' + payload.picker.strip(),
    ), request, db)
    # Global warehouse history must show the warehouse worker, not only the WMS login.
    if result.get('operation_id'):
        db.execute(text('''UPDATE inventory_operations SET performed_by=:picker,
            comment=COALESCE(comment,'') || :suffix WHERE CAST(id AS text)=:id'''),
            {'picker':payload.picker.strip(),'suffix':' · ТСД; учётная запись: '+str(getattr(request.state,'username',None) or '—'),'id':str(result['operation_id'])})
        db.commit()
    return {**result, 'article': sku.article, 'size': sku.size, 'quantity': payload.quantity,
            'source': source.name or source.barcode, 'destination': target.name or target.barcode,
            'source_address': source_loc.code, 'destination_address': target_loc.code}
