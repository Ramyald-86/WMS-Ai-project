"""Platform admin API for connecting and managing fulfillments.

Protected by the existing single-profile session middleware (ADMIN_USERNAME).
Does not alter warehouse operational routes.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.fulfillment import (
    ActivateResponse,
    ClientLinkCreate,
    ClientLinkRead,
    ClientLinkUpdate,
    FulfillmentCreate,
    FulfillmentEventRead,
    FulfillmentRead,
    FulfillmentSummary,
    FulfillmentUpdate,
    MarketplaceCredentialAttach,
    MarketplaceCredentialAttachRead,
    PrintStationCreate,
    PrintStationRead,
    PrintStationUpdate,
    PrintTokenReveal,
    ReadinessReport,
    UserCreate,
    UserRead,
    UserUpdate,
    WarehouseLinkCreate,
    WarehouseLinkRead,
    WarehouseLinkUpdate,
)
from app.services import fulfillment_platform as svc

router = APIRouter(prefix="/platform/fulfillments", tags=["Платформа · Фулфилменты"])


def _actor(request: Request) -> str:
    return str(getattr(request.state, "username", None) or "platform-admin")


@router.get("", response_model=list[FulfillmentSummary])
def list_fulfillments(db: Session = Depends(get_db)):
    svc.ensure_default_fulfillment(db)
    db.commit()
    return svc.list_fulfillment_summaries(db)


@router.post("", response_model=FulfillmentRead, status_code=201)
def create_fulfillment(payload: FulfillmentCreate, request: Request, db: Session = Depends(get_db)):
    fc = svc.create_fulfillment(db, payload, _actor(request))
    return svc.fulfillment_to_read(fc)


@router.get("/{fulfillment_id}", response_model=FulfillmentRead)
def get_fulfillment(fulfillment_id: uuid.UUID, db: Session = Depends(get_db)):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.fulfillment_to_read(fc)


@router.patch("/{fulfillment_id}", response_model=FulfillmentRead)
def update_fulfillment(
    fulfillment_id: uuid.UUID,
    payload: FulfillmentUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    fc = svc.update_fulfillment(db, fc, payload, _actor(request))
    return svc.fulfillment_to_read(fc)


@router.get("/{fulfillment_id}/readiness", response_model=ReadinessReport)
def get_readiness(fulfillment_id: uuid.UUID, db: Session = Depends(get_db)):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.readiness_report(db, fc)


@router.post("/{fulfillment_id}/activate", response_model=ActivateResponse)
def activate_fulfillment(fulfillment_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    fc, report = svc.activate_fulfillment(db, fc, _actor(request))
    return ActivateResponse(ok=True, fulfillment=svc.fulfillment_to_read(fc), readiness=report)


@router.post("/{fulfillment_id}/suspend", response_model=FulfillmentRead)
def suspend_fulfillment(fulfillment_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    fc = svc.suspend_fulfillment(db, fc, _actor(request))
    return svc.fulfillment_to_read(fc)


@router.post("/{fulfillment_id}/resume", response_model=FulfillmentRead)
def resume_fulfillment(fulfillment_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    fc = svc.resume_fulfillment(db, fc, _actor(request))
    return svc.fulfillment_to_read(fc)


@router.post("/{fulfillment_id}/archive", response_model=FulfillmentRead)
def archive_fulfillment(fulfillment_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    fc = svc.archive_fulfillment(db, fc, _actor(request))
    return svc.fulfillment_to_read(fc)


@router.get("/{fulfillment_id}/warehouses", response_model=list[WarehouseLinkRead])
def list_warehouses(fulfillment_id: uuid.UUID, db: Session = Depends(get_db)):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.list_warehouses(db, fc)


@router.post("/{fulfillment_id}/warehouses", response_model=WarehouseLinkRead, status_code=201)
def add_warehouse(
    fulfillment_id: uuid.UUID,
    payload: WarehouseLinkCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.add_warehouse(db, fc, payload, _actor(request))


@router.patch("/{fulfillment_id}/warehouses/{link_id}", response_model=WarehouseLinkRead)
def update_warehouse(
    fulfillment_id: uuid.UUID,
    link_id: uuid.UUID,
    payload: WarehouseLinkUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.update_warehouse_link(db, fc, link_id, payload, _actor(request))


@router.delete("/{fulfillment_id}/warehouses/{link_id}", response_model=WarehouseLinkRead)
def deactivate_warehouse(
    fulfillment_id: uuid.UUID,
    link_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.deactivate_warehouse_link(db, fc, link_id, _actor(request))


@router.get("/{fulfillment_id}/clients", response_model=list[ClientLinkRead])
def list_clients(fulfillment_id: uuid.UUID, db: Session = Depends(get_db)):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.list_clients(db, fc)


@router.post("/{fulfillment_id}/clients", response_model=ClientLinkRead, status_code=201)
def add_client(
    fulfillment_id: uuid.UUID,
    payload: ClientLinkCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.add_client(db, fc, payload, _actor(request))


@router.patch("/{fulfillment_id}/clients/{link_id}", response_model=ClientLinkRead)
def update_client(
    fulfillment_id: uuid.UUID,
    link_id: uuid.UUID,
    payload: ClientLinkUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.update_client_link(db, fc, link_id, payload, _actor(request))


@router.post(
    "/{fulfillment_id}/clients/{link_id}/marketplace-credentials",
    response_model=MarketplaceCredentialAttachRead,
    status_code=201,
)
def attach_marketplace_credential(
    fulfillment_id: uuid.UUID,
    link_id: uuid.UUID,
    payload: MarketplaceCredentialAttach,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.attach_marketplace_credential(db, fc, link_id, payload, _actor(request))


@router.get("/{fulfillment_id}/users", response_model=list[UserRead])
def list_users(fulfillment_id: uuid.UUID, db: Session = Depends(get_db)):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.list_users(db, fc)


@router.post("/{fulfillment_id}/users", response_model=UserRead, status_code=201)
def add_user(
    fulfillment_id: uuid.UUID,
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.add_user(db, fc, payload, _actor(request))


@router.patch("/{fulfillment_id}/users/{user_id}", response_model=UserRead)
def update_user(
    fulfillment_id: uuid.UUID,
    user_id: uuid.UUID,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.update_user(db, fc, user_id, payload, _actor(request))


@router.get("/{fulfillment_id}/print-stations", response_model=list[PrintStationRead])
def list_print_stations(fulfillment_id: uuid.UUID, db: Session = Depends(get_db)):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.list_print_stations(db, fc)


@router.post("/{fulfillment_id}/print-stations", response_model=PrintStationRead, status_code=201)
def add_print_station(
    fulfillment_id: uuid.UUID,
    payload: PrintStationCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.add_print_station(db, fc, payload, _actor(request))


@router.patch("/{fulfillment_id}/print-stations/{station_id}", response_model=PrintStationRead)
def update_print_station(
    fulfillment_id: uuid.UUID,
    station_id: uuid.UUID,
    payload: PrintStationUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.update_print_station(db, fc, station_id, payload, _actor(request))


@router.get("/{fulfillment_id}/print-token", response_model=PrintTokenReveal)
def reveal_print_token(
    fulfillment_id: uuid.UUID,
    station_code: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.reveal_print_token(fc, station_code)


@router.post("/{fulfillment_id}/print-token/rotate", response_model=PrintTokenReveal)
def rotate_print_token(
    fulfillment_id: uuid.UUID,
    request: Request,
    station_code: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    token = svc.rotate_print_token(db, fc, _actor(request))
    # Return freshly rotated master or station-bound token without a second decrypt round-trip risk.
    if station_code:
        return svc.reveal_print_token(fc, station_code)
    return {
        "fulfillment_id": fc.id,
        "token": token,
        "station_code": None,
        "station_bound": False,
    }


@router.get("/{fulfillment_id}/events", response_model=list[FulfillmentEventRead])
def list_events(
    fulfillment_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    fc = svc.get_fulfillment_or_404(db, fulfillment_id)
    return svc.list_events(db, fc, limit=limit)
