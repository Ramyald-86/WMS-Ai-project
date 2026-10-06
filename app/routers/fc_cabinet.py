"""Self-service API for an authenticated fulfillment admin (tenant host).

Platform `/platform/*` stays platform-admin only. FC_ADMIN uses these `/fc/me/*`
routes after logging in on their subdomain.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.fulfillment import (
    PrintStationCreate,
    PrintStationRead,
    PrintStationUpdate,
    ReadinessReport,
    UserCreate,
    UserRead,
    UserUpdate,
    WarehouseLinkCreate,
    WarehouseLinkRead,
    WarehouseLinkUpdate,
)
from app.services import fulfillment_platform as svc
from app.services.tenant_scope import SCOPE_KINDS

router = APIRouter(prefix="/fc/me", tags=["Кабинет фулфилмента"])


class CabinetSummary(BaseModel):
    fulfillment_id: uuid.UUID
    code: str
    name: str
    status: str
    subdomain: str | None
    role: str
    username: str
    warehouses_count: int
    clients_count: int
    users_count: int
    print_stations_count: int


def _require_fc_admin(request: Request, db: Session):
    if getattr(request.state, "auth_principal", None) != "fc":
        raise HTTPException(status_code=403, detail="Нужна сессия сотрудника фулфилмента")
    role = str(getattr(request.state, "auth_role", "") or "")
    if role not in {"FC_ADMIN"}:
        raise HTTPException(status_code=403, detail="Доступен только роли FC_ADMIN")
    fid = getattr(request.state, "auth_fulfillment_id", None) or getattr(request.state, "fulfillment_id", None)
    if not fid:
        raise HTTPException(status_code=403, detail="Контекст фулфилмента не определён")
    resolution = getattr(request.state, "tenant_resolution", None)
    if not resolution or resolution.kind not in SCOPE_KINDS:
        raise HTTPException(status_code=403, detail="Кабинет FC доступен только на хосте фулфилмента")
    fc = svc.get_fulfillment_or_404(db, uuid.UUID(str(fid)))
    if str(fc.id) != str(resolution.fulfillment_id):
        raise HTTPException(status_code=403, detail="Сессия не совпадает с хостом фулфилмента")
    return fc


@router.get("/summary", response_model=CabinetSummary)
def summary(request: Request, db: Session = Depends(get_db)):
    fc = _require_fc_admin(request, db)
    rows = svc.list_fulfillment_summaries(db)
    row = next((r for r in rows if r["id"] == fc.id), None)
    return CabinetSummary(
        fulfillment_id=fc.id,
        code=fc.code,
        name=fc.name,
        status=fc.status,
        subdomain=fc.subdomain,
        role=str(getattr(request.state, "auth_role", "")),
        username=str(getattr(request.state, "username", "")),
        warehouses_count=int((row or {}).get("warehouses_count") or 0),
        clients_count=int((row or {}).get("clients_count") or 0),
        users_count=int((row or {}).get("users_count") or 0),
        print_stations_count=int((row or {}).get("print_stations_count") or 0),
    )


@router.get("/readiness", response_model=ReadinessReport)
def readiness(request: Request, db: Session = Depends(get_db)):
    fc = _require_fc_admin(request, db)
    return svc.readiness_report(db, fc)


@router.get("/warehouses", response_model=list[WarehouseLinkRead])
def warehouses(request: Request, db: Session = Depends(get_db)):
    fc = _require_fc_admin(request, db)
    return svc.list_warehouses(db, fc)


@router.post("/warehouses", response_model=WarehouseLinkRead, status_code=201)
def add_warehouse(payload: WarehouseLinkCreate, request: Request, db: Session = Depends(get_db)):
    fc = _require_fc_admin(request, db)
    actor = str(getattr(request.state, "username", "") or "fc-admin")
    return svc.add_warehouse(db, fc, payload, actor)


@router.patch("/warehouses/{link_id}", response_model=WarehouseLinkRead)
def patch_warehouse(
    link_id: uuid.UUID,
    payload: WarehouseLinkUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = _require_fc_admin(request, db)
    actor = str(getattr(request.state, "username", "") or "fc-admin")
    return svc.update_warehouse_link(db, fc, link_id, payload, actor)


@router.get("/users", response_model=list[UserRead])
def users(request: Request, db: Session = Depends(get_db)):
    fc = _require_fc_admin(request, db)
    return svc.list_users(db, fc)


@router.post("/users", response_model=UserRead, status_code=201)
def add_user(payload: UserCreate, request: Request, db: Session = Depends(get_db)):
    fc = _require_fc_admin(request, db)
    actor = str(getattr(request.state, "username", "") or "fc-admin")
    return svc.add_user(db, fc, payload, actor)


@router.patch("/users/{user_id}", response_model=UserRead)
def patch_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = _require_fc_admin(request, db)
    actor = str(getattr(request.state, "username", "") or "fc-admin")
    return svc.update_user(db, fc, user_id, payload, actor)


@router.get("/print-stations", response_model=list[PrintStationRead])
def print_stations(request: Request, db: Session = Depends(get_db)):
    fc = _require_fc_admin(request, db)
    return svc.list_print_stations(db, fc)


@router.post("/print-stations", response_model=PrintStationRead, status_code=201)
def add_print_station(payload: PrintStationCreate, request: Request, db: Session = Depends(get_db)):
    fc = _require_fc_admin(request, db)
    actor = str(getattr(request.state, "username", "") or "fc-admin")
    return svc.add_print_station(db, fc, payload, actor)


@router.patch("/print-stations/{station_id}", response_model=PrintStationRead)
def patch_print_station(
    station_id: uuid.UUID,
    payload: PrintStationUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = _require_fc_admin(request, db)
    actor = str(getattr(request.state, "username", "") or "fc-admin")
    return svc.update_print_station(db, fc, station_id, payload, actor)


class ClientCreateBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    is_pilot: bool = False


@router.get("/clients")
def clients(request: Request, db: Session = Depends(get_db)):
    fc = _require_fc_admin(request, db)
    return svc.list_clients(db, fc)


@router.post("/clients", status_code=201)
def add_client(payload: ClientCreateBody, request: Request, db: Session = Depends(get_db)):
    from app.schemas.fulfillment import ClientLinkCreate

    fc = _require_fc_admin(request, db)
    actor = str(getattr(request.state, "username", "") or "fc-admin")
    return svc.add_client(
        db,
        fc,
        ClientLinkCreate(code=payload.code, name=payload.name, is_pilot=payload.is_pilot),
        actor,
    )
