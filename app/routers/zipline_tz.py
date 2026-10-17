"""ZIPLINE OS TZ completion API — fills partial gaps from Master TZ v1.3."""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import create_session_token, hash_password, read_session_token
from app.core.config import settings
from app.core.database import get_db
from app.models.fulfillment import FulfillmentUser, USER_ROLES
from app.services.z_event import emit_z_event, install_z_event_schema, list_z_events
from app.services.zipline_permissions import (
    USER_ROLE_TEMPLATES,
    has_permission,
    modules_for_permissions,
    permissions_for_role,
    role_catalog,
)
from app.services.zipline_tz_core import (
    BUSINESS_PURPOSES,
    CARGO_TYPES,
    DELIVERY_METHODS,
    PHYSICAL_TYPES,
    RETURN_STATES,
    detect_scan_kind,
    ensure_user_permissions_defaults,
    get_setting,
    hash_pin,
    install_zipline_tz_schema,
    recommend_putaway,
    set_setting,
    sla_risk_band,
    subscription_fee,
    verify_pin,
)

router = APIRouter(prefix="/zipline", tags=["ZIPLINE TZ"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _require_session(request: Request) -> dict[str, Any]:
    if getattr(request.state, "username", None):
        return {
            "username": request.state.username,
            "principal": getattr(request.state, "auth_principal", None),
            "role": getattr(request.state, "auth_role", None),
            "fulfillment_id": getattr(request.state, "auth_fulfillment_id", None),
            "user_id": getattr(request.state, "auth_user_id", None),
        }
    session = read_session_token(request.cookies.get("fc_session"), settings.app_secret_key)
    if not session:
        raise HTTPException(status_code=401, detail="Требуется вход")
    return {
        "username": session.get("u"),
        "principal": session.get("t") or "platform",
        "role": session.get("role") or ("PLATFORM_ADMIN" if session.get("t") != "fc" else None),
        "fulfillment_id": session.get("fid"),
        "user_id": session.get("uid"),
    }


def _user_permissions(db: Session, session: dict[str, Any]) -> list[str]:
    role = session.get("role")
    if role == "PLATFORM_ADMIN" or session.get("principal") == "platform":
        return permissions_for_role("PLATFORM_ADMIN")
    user_id = session.get("user_id")
    if not user_id:
        return permissions_for_role(role)
    install_zipline_tz_schema(db)
    row = db.execute(
        text("SELECT role, permissions FROM fulfillment_users WHERE id = CAST(:id AS uuid)"),
        {"id": user_id},
    ).mappings().first()
    if not row:
        return permissions_for_role(role)
    return permissions_for_role(row.get("role"), row.get("permissions") or None)


def _require_perm(db: Session, session: dict[str, Any], action: str) -> list[str]:
    perms = _user_permissions(db, session)
    if not has_permission(perms, action):
        raise HTTPException(status_code=403, detail=f"Недостаточно прав: {action}")
    return perms


# ── bootstrap / me ──────────────────────────────────────────────


@router.post("/install-schema")
def api_install_schema(request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    if session.get("role") not in {"PLATFORM_ADMIN", "FC_ADMIN"} and session.get("principal") != "platform":
        raise HTTPException(status_code=403, detail="Только администратор")
    z = install_z_event_schema(db)
    tz = install_zipline_tz_schema(db)
    backfilled = ensure_user_permissions_defaults(db)
    return {"ok": True, "z_event": True, "tz": tz, "permissions_backfilled": backfilled}


@router.get("/me")
def api_me(request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    install_zipline_tz_schema(db)
    perms = _user_permissions(db, session)
    return {
        **session,
        "permissions": perms,
        "modules": modules_for_permissions(perms),
        "subscription_fee": subscription_fee(db) if session.get("role") in {"PLATFORM_ADMIN", "FC_ADMIN"} or session.get("principal") == "platform" else None,
    }


@router.get("/roles")
def api_roles(request: Request, db: Session = Depends(get_db)):
    _require_session(request)
    install_zipline_tz_schema(db)
    return {"roles": role_catalog(), "legacy_compatible": list(USER_ROLES), "templates": list(USER_ROLE_TEMPLATES)}


class PermissionsUpdate(BaseModel):
    permissions: list[str] = Field(default_factory=list)
    modules: list[str] | None = None
    pin: str | None = Field(default=None, min_length=4, max_length=8)


@router.patch("/users/{user_id}/permissions")
def api_set_user_permissions(
    user_id: uuid.UUID,
    payload: PermissionsUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    session = _require_session(request)
    _require_perm(db, session, "ROLE_MANAGE")
    install_zipline_tz_schema(db)
    user = db.get(FulfillmentUser, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    perms = permissions_for_role(user.role, payload.permissions)
    db.execute(
        text(
            """
            UPDATE fulfillment_users
            SET permissions = CAST(:p AS jsonb),
                modules = CAST(:m AS jsonb),
                pin_hash = COALESCE(:pin, pin_hash),
                updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {
            "p": json.dumps(perms),
            "m": json.dumps(payload.modules or modules_for_permissions(perms)),
            "pin": hash_pin(payload.pin) if payload.pin else None,
            "id": str(user_id),
        },
    )
    emit_z_event(
        db,
        event_type="Role.PermissionsUpdated",
        tenant_id=str(user.fulfillment_id),
        employee_id=session.get("user_id"),
        before={"permissions": user.__dict__.get("permissions")},
        after={"permissions": perms, "modules": payload.modules},
        reason="admin_update",
        commit=True,
    )
    return {"ok": True, "user_id": str(user_id), "permissions": perms}


# ── subscription fee ────────────────────────────────────────────


class SubscriptionFeeIn(BaseModel):
    amount: int = Field(ge=0, le=10_000_000)
    currency: str = "RUB"


@router.get("/platform/subscription-fee")
def api_get_subscription_fee(request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    if session.get("principal") != "platform" and session.get("role") != "PLATFORM_ADMIN":
        raise HTTPException(status_code=403, detail="Только Super Admin")
    return subscription_fee(db)


@router.put("/platform/subscription-fee")
def api_set_subscription_fee(payload: SubscriptionFeeIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    if session.get("principal") != "platform" and session.get("role") != "PLATFORM_ADMIN":
        raise HTTPException(status_code=403, detail="Только Super Admin")
    value = {"amount": int(payload.amount), "currency": payload.currency or "RUB"}
    set_setting(db, "partner_subscription_fee_rub", value, updated_by=session.get("username"))
    emit_z_event(
        db,
        event_type="Platform.SubscriptionFeeUpdated",
        actor_type="platform",
        after=value,
        reason="super_admin",
        commit=True,
    )
    return value


# ── Z-Event ─────────────────────────────────────────────────────


class ZEventIn(BaseModel):
    event_type: str
    warehouse_id: str | None = None
    client_id: str | None = None
    order_id: str | None = None
    shipment_id: str | None = None
    hu_id: str | None = None
    sku_id: str | None = None
    barcode: str | None = None
    chz: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    reason: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] | None = None


@router.post("/events")
def api_emit_event(payload: ZEventIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    result = emit_z_event(
        db,
        event_type=payload.event_type,
        tenant_id=session.get("fulfillment_id"),
        warehouse_id=payload.warehouse_id,
        client_id=payload.client_id,
        employee_id=session.get("user_id"),
        order_id=payload.order_id,
        shipment_id=payload.shipment_id,
        hu_id=payload.hu_id,
        sku_id=payload.sku_id,
        barcode=payload.barcode,
        chz=payload.chz,
        before=payload.before,
        after=payload.after,
        reason=payload.reason,
        idempotency_key=payload.idempotency_key,
        payload=payload.payload,
        commit=True,
    )
    return result


@router.get("/events")
def api_list_events(
    request: Request,
    db: Session = Depends(get_db),
    event_type: str | None = None,
    order_id: str | None = None,
    hu_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    session = _require_session(request)
    _require_perm(db, session, "VIEW_HISTORY")
    return {"items": list_z_events(db, event_type=event_type, order_id=order_id, hu_id=hu_id, limit=limit)}


# ── scan detect / putaway / SLA ─────────────────────────────────


class ScanIn(BaseModel):
    code: str


@router.post("/scan/detect")
def api_scan_detect(payload: ScanIn, request: Request):
    _require_session(request)
    kind = detect_scan_kind(payload.code)
    # DataMatrix scanned in product barcode field → route to CHZ step
    if kind["kind"] == "CHZ":
        kind["route"] = "CHZ_STEP"
        kind["message"] = "Обнаружен DataMatrix — переводим на шаг Честный ЗНАК"
    else:
        kind["route"] = "PRODUCT_OR_HU"
    return kind


class PutawayRecommendIn(BaseModel):
    warehouse_id: str | None = None
    client_id: str | None = None
    prefer_direct: bool = True


@router.post("/putaway/recommend")
def api_putaway_recommend(payload: PutawayRecommendIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "PUTAWAY")
    return recommend_putaway(
        db,
        warehouse_id=payload.warehouse_id,
        client_id=payload.client_id,
        prefer_direct=payload.prefer_direct,
    )


@router.get("/sla/risk")
def api_sla_risk(
    request: Request,
    age_hours: float = Query(ge=0),
    sla_hours: float = Query(default=24, ge=1),
):
    _require_session(request)
    return sla_risk_band(age_hours, sla_hours)


# ── inbound request ─────────────────────────────────────────────


class InboundCreate(BaseModel):
    client_id: str
    warehouse_id: str | None = None
    delivery_method: Literal["CLIENT_SELF", "ZIPLINE_PICKUP"]
    cargo_types: list[str] = Field(default_factory=list)
    volume_m3: float | None = None
    weight_kg: float | None = None
    delivery_date: str | None = None
    time_window: str | None = None
    contact: str | None = None
    access_notes: str | None = None
    marking_status: str = "UNMARKED"
    estimated_price: float | None = None
    lines: list[dict[str, Any]] = Field(default_factory=list)


@router.post("/inbound/requests")
def api_create_inbound(payload: InboundCreate, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    install_zipline_tz_schema(db)
    if payload.delivery_method not in DELIVERY_METHODS:
        raise HTTPException(status_code=400, detail="Способ доставки: CLIENT_SELF или ZIPLINE_PICKUP")
    cargo = [c for c in payload.cargo_types if c in CARGO_TYPES]
    if not cargo:
        raise HTTPException(status_code=400, detail="Укажите тип грузовых мест")
    if payload.volume_m3 is None or payload.weight_kg is None:
        raise HTTPException(status_code=400, detail="Объём и вес обязательны")
    rid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO zipline_inbound_requests(
                id, tenant_id, client_id, warehouse_id, delivery_method, cargo_types,
                volume_m3, weight_kg, delivery_date, time_window, contact, access_notes,
                marking_status, estimated_price, status, lines, created_by
            ) VALUES (
                CAST(:id AS uuid), CAST(:tenant_id AS uuid), CAST(:client_id AS uuid),
                CAST(:warehouse_id AS uuid), :delivery_method, CAST(:cargo AS jsonb),
                :volume_m3, :weight_kg, CAST(:delivery_date AS date), :time_window, :contact,
                :access_notes, :marking_status, :estimated_price, 'SUBMITTED',
                CAST(:lines AS jsonb), :created_by
            )
            """
        ),
        {
            "id": str(rid),
            "tenant_id": session.get("fulfillment_id"),
            "client_id": payload.client_id,
            "warehouse_id": payload.warehouse_id,
            "delivery_method": payload.delivery_method,
            "cargo": json.dumps(cargo),
            "volume_m3": payload.volume_m3,
            "weight_kg": payload.weight_kg,
            "delivery_date": payload.delivery_date,
            "time_window": payload.time_window,
            "contact": payload.contact,
            "access_notes": payload.access_notes,
            "marking_status": payload.marking_status,
            "estimated_price": payload.estimated_price,
            "lines": json.dumps(payload.lines, ensure_ascii=False),
            "created_by": session.get("username"),
        },
    )
    emit_z_event(
        db,
        event_type="Inbound.RequestCreated",
        tenant_id=session.get("fulfillment_id"),
        client_id=payload.client_id,
        warehouse_id=payload.warehouse_id,
        employee_id=session.get("user_id"),
        after={"id": str(rid), "delivery_method": payload.delivery_method},
        commit=True,
    )
    return {"id": str(rid), "status": "SUBMITTED"}


@router.get("/inbound/requests")
def api_list_inbound(
    request: Request,
    db: Session = Depends(get_db),
    client_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    _require_session(request)
    install_zipline_tz_schema(db)
    params: dict[str, Any] = {"limit": limit}
    clause = ""
    if client_id:
        clause = "WHERE client_id = CAST(:client_id AS uuid)"
        params["client_id"] = client_id
    rows = db.execute(
        text(
            f"""
            SELECT id::text, client_id::text, warehouse_id::text, delivery_method, cargo_types,
                   volume_m3, weight_kg, delivery_date::text, time_window, contact, marking_status,
                   estimated_price, status, lines, created_at
            FROM zipline_inbound_requests
            {clause}
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    items = []
    for r in rows:
        item = dict(r)
        if item.get("created_at"):
            item["created_at"] = item["created_at"].isoformat()
        for k in ("volume_m3", "weight_kg", "estimated_price"):
            if item.get(k) is not None:
                item[k] = float(item[k])
        items.append(item)
    return {"items": items}


# ── buffer routing ──────────────────────────────────────────────


class BufferScanIn(BaseModel):
    barcode: str
    warehouse_id: str | None = None
    client_id: str | None = None
    inbound_request_id: str | None = None
    physical_type: str | None = None
    business_purpose: str | None = None


@router.post("/buffer/scan")
def api_buffer_scan(payload: BufferScanIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "RECEIVE_SCAN")
    install_zipline_tz_schema(db)
    detected = detect_scan_kind(payload.barcode)
    physical = payload.physical_type or (
        "PALLET" if detected["kind"] == "HU" and payload.barcode.upper().startswith("PLT") else "CARTON"
    )
    if physical not in PHYSICAL_TYPES:
        raise HTTPException(status_code=400, detail="Некорректный physical_type")
    purpose = payload.business_purpose or "GENERAL_STORAGE"
    if purpose not in BUSINESS_PURPOSES:
        raise HTTPException(status_code=400, detail="Некорректный business_purpose")
    route_label = f"{physical}/{purpose}"
    bid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO zipline_buffer_units(
                id, inbound_request_id, warehouse_id, client_id, barcode,
                physical_type, business_purpose, route_label, status
            ) VALUES (
                CAST(:id AS uuid), CAST(:inbound AS uuid), CAST(:wh AS uuid), CAST(:client AS uuid),
                :barcode, :physical, :purpose, :route, 'IN_BUFFER'
            )
            """
        ),
        {
            "id": str(bid),
            "inbound": payload.inbound_request_id,
            "wh": payload.warehouse_id,
            "client": payload.client_id,
            "barcode": payload.barcode.strip(),
            "physical": physical,
            "purpose": purpose,
            "route": route_label,
        },
    )
    emit_z_event(
        db,
        event_type="Buffer.UnitRouted",
        warehouse_id=payload.warehouse_id,
        client_id=payload.client_id,
        hu_id=payload.barcode,
        after={"physical_type": physical, "business_purpose": purpose, "route_label": route_label},
        commit=True,
    )
    return {
        "id": str(bid),
        "physical_type": physical,
        "business_purpose": purpose,
        "route_label": route_label,
        "print_route_label": True,
        "detect": detected,
    }


# ── returns QC / defect pending ─────────────────────────────────


class ReturnReceiveIn(BaseModel):
    warehouse_id: str | None = None
    client_id: str | None = None
    order_id: str | None = None
    shipment_id: str | None = None
    sku_id: str | None = None
    barcode: str | None = None
    origin: str | None = None
    reason: str | None = None
    photo_url: str | None = None
    video_url: str | None = None


@router.post("/returns/receive")
def api_return_receive(payload: ReturnReceiveIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "RECEIVE_START")
    install_zipline_tz_schema(db)
    rid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO zipline_return_qc(
                id, warehouse_id, client_id, order_id, shipment_id, sku_id, barcode,
                origin, reason, photo_url, video_url, state
            ) VALUES (
                CAST(:id AS uuid), CAST(:wh AS uuid), CAST(:client AS uuid), CAST(:order_id AS uuid),
                CAST(:shipment_id AS uuid), CAST(:sku_id AS uuid), :barcode, :origin, :reason,
                :photo_url, :video_url, 'RETURN_RECEIVED'
            )
            """
        ),
        {
            "id": str(rid),
            "wh": payload.warehouse_id,
            "client": payload.client_id,
            "order_id": payload.order_id,
            "shipment_id": payload.shipment_id,
            "sku_id": payload.sku_id,
            "barcode": payload.barcode,
            "origin": payload.origin,
            "reason": payload.reason,
            "photo_url": payload.photo_url,
            "video_url": payload.video_url,
        },
    )
    emit_z_event(db, event_type="Return.Received", order_id=payload.order_id, sku_id=payload.sku_id, after={"state": "RETURN_RECEIVED"}, commit=True)
    return {"id": str(rid), "state": "RETURN_RECEIVED"}


class ReturnDecideIn(BaseModel):
    state: Literal["QC_OK", "REPACK_REQUIRED", "DEFECT", "UNKNOWN", "RETURN_TO_CLIENT"]


@router.post("/returns/{return_id}/decide")
def api_return_decide(
    return_id: uuid.UUID,
    payload: ReturnDecideIn,
    request: Request,
    db: Session = Depends(get_db),
):
    session = _require_session(request)
    _require_perm(db, session, "OVERRIDE_VARIANCE")
    install_zipline_tz_schema(db)
    if payload.state not in RETURN_STATES:
        raise HTTPException(status_code=400, detail="Некорректный state")
    final = "AVAILABLE" if payload.state == "QC_OK" else (
        "QUARANTINE" if payload.state == "UNKNOWN" else payload.state
    )
    row = db.execute(
        text("SELECT state FROM zipline_return_qc WHERE id = CAST(:id AS uuid)"),
        {"id": str(return_id)},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Возврат не найден")
    db.execute(
        text(
            """
            UPDATE zipline_return_qc
            SET state = :state, decided_by = :by, updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"state": final, "by": session.get("username"), "id": str(return_id)},
    )
    emit_z_event(
        db,
        event_type="Return.QCDecided",
        before={"state": row["state"]},
        after={"state": final},
        reason=payload.state,
        employee_id=session.get("user_id"),
        commit=True,
    )
    return {"id": str(return_id), "state": final, "available_for_sale": final == "AVAILABLE"}


class DefectFastIn(BaseModel):
    warehouse_id: str | None = None
    client_id: str | None = None
    sku_id: str | None = None
    barcode: str | None = None
    short_reason: str = Field(min_length=1, max_length=255)


@router.post("/defects/pending")
def api_defect_pending(payload: DefectFastIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "RECEIVE_SCAN")
    install_zipline_tz_schema(db)
    did = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO zipline_defect_reviews(
                id, warehouse_id, client_id, sku_id, barcode, short_reason, state, created_by
            ) VALUES (
                CAST(:id AS uuid), CAST(:wh AS uuid), CAST(:client AS uuid), CAST(:sku AS uuid),
                :barcode, :reason, 'DEFECT_PENDING_REVIEW', :by
            )
            """
        ),
        {
            "id": str(did),
            "wh": payload.warehouse_id,
            "client": payload.client_id,
            "sku": payload.sku_id,
            "barcode": payload.barcode,
            "reason": payload.short_reason,
            "by": session.get("username"),
        },
    )
    emit_z_event(
        db,
        event_type="Defect.PendingReview",
        after={"id": str(did), "state": "DEFECT_PENDING_REVIEW"},
        reason=payload.short_reason,
        commit=True,
    )
    return {"id": str(did), "state": "DEFECT_PENDING_REVIEW", "blocked_for_sale": True}


class DefectReviewIn(BaseModel):
    disposition: Literal["REWORK", "RETURN_TO_CLIENT", "TO_DISPOSE", "DISPOSE"]
    detail_reason: str | None = None
    evidence: dict[str, Any] | None = None


@router.post("/defects/{defect_id}/review")
def api_defect_review(
    defect_id: uuid.UUID,
    payload: DefectReviewIn,
    request: Request,
    db: Session = Depends(get_db),
):
    session = _require_session(request)
    _require_perm(db, session, "OVERRIDE_VARIANCE")
    install_zipline_tz_schema(db)
    state = "DEFECT_LOCKED" if payload.disposition != "DISPOSE" else "DISPOSED"
    db.execute(
        text(
            """
            UPDATE zipline_defect_reviews
            SET state = :state, disposition = :disp, detail_reason = :detail,
                evidence = CAST(:ev AS jsonb), reviewed_by = :by, updated_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {
            "state": state,
            "disp": payload.disposition,
            "detail": payload.detail_reason,
            "ev": json.dumps(payload.evidence or {}),
            "by": session.get("username"),
            "id": str(defect_id),
        },
    )
    emit_z_event(
        db,
        event_type="Defect.Reviewed",
        after={"state": state, "disposition": payload.disposition},
        reason=payload.detail_reason,
        commit=True,
    )
    return {"id": str(defect_id), "state": state, "disposition": payload.disposition}


# ── packing HU / stations / weight ──────────────────────────────


class PackAssignIn(BaseModel):
    order_id: str
    warehouse_id: str | None = None
    preferred_station: str | None = None


@router.post("/packing/assign-station")
def api_assign_station(payload: PackAssignIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "PACK_START")
    install_zipline_tz_schema(db)
    station = None
    if payload.preferred_station:
        station = db.execute(
            text(
                """
                SELECT station_code, capabilities FROM zipline_pack_stations
                WHERE station_code = :c AND is_active = TRUE
                """
            ),
            {"c": payload.preferred_station},
        ).mappings().first()
    if not station:
        station = db.execute(
            text(
                """
                SELECT station_code, capabilities FROM zipline_pack_stations
                WHERE is_active = TRUE
                ORDER BY (busy_order_id IS NOT NULL), last_assigned_at NULLS FIRST
                LIMIT 1
                """
            )
        ).mappings().first()
    if not station:
        raise HTTPException(status_code=409, detail="Нет свободных PACK-столов")
    db.execute(
        text(
            """
            UPDATE zipline_pack_stations
            SET busy_order_id = CAST(:oid AS uuid), last_assigned_at = NOW()
            WHERE station_code = :c
            """
        ),
        {"oid": payload.order_id, "c": station["station_code"]},
    )
    emit_z_event(
        db,
        event_type="Packing.StationAssigned",
        order_id=payload.order_id,
        workstation_id=station["station_code"],
        after={"station": station["station_code"], "order_id": payload.order_id},
        commit=True,
    )
    return {
        "station_code": station["station_code"],
        "order_id": payload.order_id,
        "capabilities": station["capabilities"],
    }


class PackReassignIn(BaseModel):
    order_id: str
    station_code: str
    reason: str = Field(min_length=3, max_length=500)


@router.post("/packing/reassign-station")
def api_reassign_station(payload: PackReassignIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "OVERRIDE_VARIANCE")
    install_zipline_tz_schema(db)
    db.execute(
        text(
            """
            UPDATE zipline_pack_stations SET busy_order_id = NULL
            WHERE busy_order_id = CAST(:oid AS uuid)
            """
        ),
        {"oid": payload.order_id},
    )
    db.execute(
        text(
            """
            UPDATE zipline_pack_stations
            SET busy_order_id = CAST(:oid AS uuid), last_assigned_at = NOW()
            WHERE station_code = :c AND is_active = TRUE
            """
        ),
        {"oid": payload.order_id, "c": payload.station_code},
    )
    emit_z_event(
        db,
        event_type="Task.Reassigned",
        order_id=payload.order_id,
        workstation_id=payload.station_code,
        reason=payload.reason,
        employee_id=session.get("user_id"),
        commit=True,
    )
    return {"ok": True, "station_code": payload.station_code, "reason": payload.reason}


class HuCreateIn(BaseModel):
    order_id: str
    warehouse_id: str | None = None
    client_id: str | None = None
    place_index: int = 1
    place_total: int = 1
    packing_station: str | None = None
    shipping_barcode: str | None = None


@router.post("/packing/hu")
def api_create_hu(payload: HuCreateIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "PACK_START")
    install_zipline_tz_schema(db)
    if payload.shipping_barcode:
        clash = db.execute(
            text(
                """
                SELECT hu_code FROM zipline_shipping_hu
                WHERE shipping_barcode = :b
                  AND status IN ('OPEN','CLOSED','STAGED','LOADED')
                LIMIT 1
                """
            ),
            {"b": payload.shipping_barcode},
        ).scalar()
        if clash:
            raise HTTPException(
                status_code=409,
                detail=f"Shipping barcode уже назначен активному HU {clash}. 1 короб = 1 HU = 1 уникальная этикетка.",
            )
    hid = uuid.uuid4()
    hu_code = f"HU-{secrets.token_hex(4).upper()}"
    db.execute(
        text(
            """
            INSERT INTO zipline_shipping_hu(
                id, order_id, warehouse_id, client_id, hu_code, shipping_barcode,
                place_index, place_total, status, packing_station
            ) VALUES (
                CAST(:id AS uuid), CAST(:oid AS uuid), CAST(:wh AS uuid), CAST(:client AS uuid),
                :hu, :sb, :pi, :pt, 'OPEN', :station
            )
            """
        ),
        {
            "id": str(hid),
            "oid": payload.order_id,
            "wh": payload.warehouse_id,
            "client": payload.client_id,
            "hu": hu_code,
            "sb": payload.shipping_barcode,
            "pi": payload.place_index,
            "pt": payload.place_total,
            "station": payload.packing_station,
        },
    )
    emit_z_event(db, event_type="HU.Created", order_id=payload.order_id, hu_id=hu_code, commit=True)
    return {
        "id": str(hid),
        "hu_code": hu_code,
        "shipping_barcode": payload.shipping_barcode,
        "place_index": payload.place_index,
        "place_total": payload.place_total,
        "status": "OPEN",
    }


class HuCloseIn(BaseModel):
    weight_kg: float | None = None
    weight_required: bool = False
    contents: list[dict[str, Any]] | None = None


@router.post("/packing/hu/{hu_id}/close")
def api_close_hu(hu_id: uuid.UUID, payload: HuCloseIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "PACK_SCAN")
    install_zipline_tz_schema(db)
    row = db.execute(
        text("SELECT hu_code, status, shipping_barcode FROM zipline_shipping_hu WHERE id = CAST(:id AS uuid)"),
        {"id": str(hu_id)},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="HU не найден")
    if row["status"] != "OPEN":
        raise HTTPException(status_code=409, detail=f"HU уже в статусе {row['status']}")
    policy = get_setting(db, "packing_weight_required") or {"WEIGHT_REQUIRED": False}
    weight_required = bool(payload.weight_required or policy.get("WEIGHT_REQUIRED"))
    if weight_required and payload.weight_kg is None:
        raise HTTPException(status_code=409, detail="WEIGHT_REQUIRED: укажите вес перед закрытием места")
    if not row.get("shipping_barcode"):
        raise HTTPException(
            status_code=409,
            detail="Нельзя закрыть HU без уникальной shipping-этикетки (1 короб = 1 HU = 1 label)",
        )
    db.execute(
        text(
            """
            UPDATE zipline_shipping_hu
            SET status = 'CLOSED', weight_kg = :w, contents = CAST(:c AS jsonb), closed_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {
            "w": payload.weight_kg,
            "c": json.dumps(payload.contents or []),
            "id": str(hu_id),
        },
    )
    emit_z_event(db, event_type="HU.Closed", hu_id=row["hu_code"], after={"weight_kg": payload.weight_kg}, commit=True)
    return {"id": str(hu_id), "hu_code": row["hu_code"], "status": "CLOSED", "weight_required": weight_required}


class HuBindLabelIn(BaseModel):
    shipping_barcode: str = Field(min_length=1, max_length=190)


@router.post("/packing/hu/{hu_id}/bind-label")
def api_bind_hu_label(hu_id: uuid.UUID, payload: HuBindLabelIn, request: Request, db: Session = Depends(get_db)):
    """Bind unique shipping label to HU — enforces 1 box = 1 HU = 1 label."""
    session = _require_session(request)
    _require_perm(db, session, "PACK_SCAN")
    install_zipline_tz_schema(db)
    barcode = payload.shipping_barcode.strip()
    row = db.execute(
        text("SELECT id::text, hu_code, status, shipping_barcode FROM zipline_shipping_hu WHERE id = CAST(:id AS uuid)"),
        {"id": str(hu_id)},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="HU не найден")
    if row["status"] != "OPEN":
        raise HTTPException(status_code=409, detail=f"HU уже в статусе {row['status']}")
    if row.get("shipping_barcode") and row["shipping_barcode"] != barcode:
        raise HTTPException(status_code=409, detail="У HU уже другая этикетка — новый barcode не назначается")
    clash = db.execute(
        text(
            """
            SELECT hu_code FROM zipline_shipping_hu
            WHERE shipping_barcode = :b
              AND id <> CAST(:id AS uuid)
              AND status IN ('OPEN','CLOSED','STAGED','LOADED')
            LIMIT 1
            """
        ),
        {"b": barcode, "id": str(hu_id)},
    ).scalar()
    if clash:
        raise HTTPException(
            status_code=409,
            detail=f"Этикетка уже на HU {clash}. 1 короб = 1 HU = 1 уникальная этикетка.",
        )
    db.execute(
        text("UPDATE zipline_shipping_hu SET shipping_barcode = :b WHERE id = CAST(:id AS uuid)"),
        {"b": barcode, "id": str(hu_id)},
    )
    emit_z_event(
        db,
        event_type="HU.LabelBound",
        hu_id=row["hu_code"],
        barcode=barcode,
        employee_id=session.get("user_id"),
        commit=True,
    )
    return {"id": str(hu_id), "hu_code": row["hu_code"], "shipping_barcode": barcode}


@router.get("/packing/hu")
def api_list_hus(
    request: Request,
    db: Session = Depends(get_db),
    order_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    _require_session(request)
    install_zipline_tz_schema(db)
    clauses = ["1=1"]
    params: dict[str, Any] = {"limit": limit}
    if order_id:
        clauses.append("order_id = CAST(:order_id AS uuid)")
        params["order_id"] = order_id
    if status:
        clauses.append("status = :status")
        params["status"] = status
    rows = db.execute(
        text(
            f"""
            SELECT id::text, order_id::text, warehouse_id::text, client_id::text, hu_code,
                   shipping_barcode, place_index, place_total, status, weight_kg,
                   packing_station, created_at, closed_at
            FROM zipline_shipping_hu
            WHERE {' AND '.join(clauses)}
            ORDER BY place_index, created_at
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()
    items = []
    for r in rows:
        item = dict(r)
        if item.get("weight_kg") is not None:
            item["weight_kg"] = float(item["weight_kg"])
        for k in ("created_at", "closed_at"):
            if item.get(k):
                item[k] = item[k].isoformat()
        items.append(item)
    return {"items": items}


class PackCompleteIn(BaseModel):
    order_id: str
    declared_places: int = Field(ge=1)


@router.post("/packing/complete")
def api_pack_complete(payload: PackCompleteIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "PACK_SCAN")
    install_zipline_tz_schema(db)
    rows = db.execute(
        text(
            """
            SELECT id::text, hu_code, shipping_barcode, status
            FROM zipline_shipping_hu
            WHERE order_id = CAST(:oid AS uuid)
            ORDER BY place_index
            """
        ),
        {"oid": payload.order_id},
    ).mappings().all()
    closed = [r for r in rows if r["status"] == "CLOSED"]
    labels = {r["shipping_barcode"] for r in closed if r.get("shipping_barcode")}
    if len(closed) != payload.declared_places:
        raise HTTPException(
            status_code=409,
            detail=f"declared_places={payload.declared_places}, closed_HU={len(closed)} — должны совпадать",
        )
    if labels and len(labels) != len(closed):
        raise HTTPException(
            status_code=409,
            detail="Количество уникальных shipping labels должно равняться числу закрытых HU",
        )
    open_hus = [r for r in rows if r["status"] == "OPEN"]
    if open_hus:
        raise HTTPException(status_code=409, detail="Есть незакрытые HU")
    emit_z_event(
        db,
        event_type="Packing.Completed",
        order_id=payload.order_id,
        after={"places": len(closed), "labels": len(labels)},
        commit=True,
    )
    return {"ok": True, "order_id": payload.order_id, "closed_hu_count": len(closed), "unique_labels": len(labels)}


@router.get("/packing/weight-policy")
def api_weight_policy(request: Request, db: Session = Depends(get_db)):
    _require_session(request)
    install_zipline_tz_schema(db)
    policy = get_setting(db, "packing_weight_required") or {"WEIGHT_REQUIRED": False}
    return policy


class WeightPolicyIn(BaseModel):
    WEIGHT_REQUIRED: bool = False


@router.put("/packing/weight-policy")
def api_set_weight_policy(payload: WeightPolicyIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "ROLE_MANAGE")
    value = {"WEIGHT_REQUIRED": bool(payload.WEIGHT_REQUIRED)}
    set_setting(db, "packing_weight_required", value, updated_by=session.get("username"))
    return value


# ── staging / loading ───────────────────────────────────────────


class ShipmentFromPackedIn(BaseModel):
    hu_ids: list[str] = Field(default_factory=list)
    warehouse_id: str | None = None
    channel: str = "INTERNAL"


@router.post("/outbound/from-packed")
def api_shipment_from_packed(payload: ShipmentFromPackedIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "SHIP_CREATE")
    install_zipline_tz_schema(db)
    if not payload.hu_ids:
        raise HTTPException(status_code=400, detail="Нет HU для отгрузки")
    sid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO zipline_shipments(id, warehouse_id, channel, status, created_by)
            VALUES (CAST(:id AS uuid), CAST(:wh AS uuid), :channel, 'READY', :by)
            """
        ),
        {
            "id": str(sid),
            "wh": payload.warehouse_id,
            "channel": payload.channel,
            "by": session.get("username"),
        },
    )
    attached = 0
    for hu in payload.hu_ids:
        key = str(hu or "").strip()
        if not key:
            continue
        # Compare as text to avoid psycopg binding uuid vs varchar on one param.
        row = db.execute(
            text(
                """
                SELECT id::text, status FROM zipline_shipping_hu
                WHERE id::text = :key OR hu_code = :key
                """
            ),
            {"key": key},
        ).mappings().first()
        if not row:
            continue
        if row["status"] not in {"CLOSED", "STAGED"}:
            raise HTTPException(status_code=409, detail=f"HU {key} не PACKED/CLOSED")
        already = db.execute(
            text("SELECT 1 FROM zipline_shipment_hus WHERE hu_id = CAST(:hid AS uuid) LIMIT 1"),
            {"hid": row["id"]},
        ).scalar()
        if already:
            continue
        db.execute(
            text(
                """
                INSERT INTO zipline_shipment_hus(shipment_id, hu_id)
                VALUES (CAST(:sid AS uuid), CAST(:hid AS uuid))
                """
            ),
            {"sid": str(sid), "hid": row["id"]},
        )
        attached += 1
    if attached == 0:
        db.execute(text("DELETE FROM zipline_shipments WHERE id = CAST(:id AS uuid)"), {"id": str(sid)})
        db.commit()
        raise HTTPException(status_code=409, detail="Нет подходящих CLOSED HU (уже в shipment?)")
    emit_z_event(db, event_type="Shipment.Created", shipment_id=str(sid), after={"hu_count": attached}, commit=True)
    return {"shipment_id": str(sid), "hu_count": attached, "status": "READY", "marketplace_status": None}


class StageIn(BaseModel):
    hu_code: str
    staging_location: str


@router.post("/outbound/{shipment_id}/stage")
def api_stage_hu(shipment_id: uuid.UUID, payload: StageIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "SHIP_LOAD")
    install_zipline_tz_schema(db)
    hu = db.execute(
        text("SELECT id::text, status FROM zipline_shipping_hu WHERE hu_code = :c"),
        {"c": payload.hu_code},
    ).mappings().first()
    if not hu:
        raise HTTPException(status_code=404, detail="HU не найден")
    link = db.execute(
        text(
            """
            SELECT 1 FROM zipline_shipment_hus
            WHERE shipment_id = CAST(:sid AS uuid) AND hu_id = CAST(:hid AS uuid)
            """
        ),
        {"sid": str(shipment_id), "hid": hu["id"]},
    ).scalar()
    if not link:
        raise HTTPException(status_code=409, detail="⛔ НЕ ЭТА ПОСТАВКА")
    db.execute(
        text(
            """
            UPDATE zipline_shipment_hus
            SET staged_at = NOW(), staging_location = :loc
            WHERE shipment_id = CAST(:sid AS uuid) AND hu_id = CAST(:hid AS uuid)
            """
        ),
        {"loc": payload.staging_location, "sid": str(shipment_id), "hid": hu["id"]},
    )
    db.execute(
        text("UPDATE zipline_shipping_hu SET status = 'STAGED' WHERE id = CAST(:id AS uuid)"),
        {"id": hu["id"]},
    )
    emit_z_event(db, event_type="Shipment.HUStaged", shipment_id=str(shipment_id), hu_id=payload.hu_code, commit=True)
    return {"ok": True, "hu_code": payload.hu_code, "staging_location": payload.staging_location}


class LoadIn(BaseModel):
    hu_code: str


@router.post("/outbound/{shipment_id}/load")
def api_load_hu(shipment_id: uuid.UUID, payload: LoadIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "SHIP_LOAD")
    install_zipline_tz_schema(db)
    hu = db.execute(
        text("SELECT id::text, status FROM zipline_shipping_hu WHERE hu_code = :c"),
        {"c": payload.hu_code},
    ).mappings().first()
    if not hu:
        raise HTTPException(status_code=404, detail="HU не найден")
    link = db.execute(
        text(
            """
            SELECT load_scan_count, loaded_at FROM zipline_shipment_hus
            WHERE shipment_id = CAST(:sid AS uuid) AND hu_id = CAST(:hid AS uuid)
            """
        ),
        {"sid": str(shipment_id), "hid": hu["id"]},
    ).mappings().first()
    if not link:
        raise HTTPException(status_code=409, detail="⛔ НЕ ЭТА ПОСТАВКА")
    if link["loaded_at"] is not None or int(link["load_scan_count"] or 0) > 0:
        raise HTTPException(status_code=409, detail="⛔ УЖЕ ЗАГРУЖЕН")
    db.execute(
        text(
            """
            UPDATE zipline_shipment_hus
            SET loaded_at = NOW(), load_scan_count = load_scan_count + 1
            WHERE shipment_id = CAST(:sid AS uuid) AND hu_id = CAST(:hid AS uuid)
            """
        ),
        {"sid": str(shipment_id), "hid": hu["id"]},
    )
    db.execute(
        text("UPDATE zipline_shipping_hu SET status = 'LOADED' WHERE id = CAST(:id AS uuid)"),
        {"id": hu["id"]},
    )
    emit_z_event(db, event_type="Shipment.HULoaded", shipment_id=str(shipment_id), hu_id=payload.hu_code, commit=True)
    return {"ok": True, "hu_code": payload.hu_code, "signal": "green", "beep": True, "auto_next": True}


class CloseVarianceIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.get("/outbound/{shipment_id}")
def api_get_shipment(shipment_id: uuid.UUID, request: Request, db: Session = Depends(get_db)):
    _require_session(request)
    install_zipline_tz_schema(db)
    ship = db.execute(
        text(
            """
            SELECT id::text, warehouse_id::text, channel, status, marketplace_status,
                   created_at, completed_at, created_by
            FROM zipline_shipments WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": str(shipment_id)},
    ).mappings().first()
    if not ship:
        raise HTTPException(status_code=404, detail="Отгрузка не найдена")
    hus = db.execute(
        text(
            """
            SELECT h.id::text, h.hu_code, h.shipping_barcode, h.status, h.place_index, h.place_total,
                   h.order_id::text, sh.staging_location, sh.staged_at, sh.loaded_at, sh.load_scan_count
            FROM zipline_shipment_hus sh
            JOIN zipline_shipping_hu h ON h.id = sh.hu_id
            WHERE sh.shipment_id = CAST(:sid AS uuid)
            ORDER BY h.place_index, h.hu_code
            """
        ),
        {"sid": str(shipment_id)},
    ).mappings().all()
    items = []
    loaded = 0
    for r in hus:
        item = dict(r)
        for k in ("staged_at", "loaded_at"):
            if item.get(k):
                item[k] = item[k].isoformat()
        if item.get("loaded_at"):
            loaded += 1
        items.append(item)
    out = dict(ship)
    for k in ("created_at", "completed_at"):
        if out.get(k):
            out[k] = out[k].isoformat()
    out["hus"] = items
    out["hu_total"] = len(items)
    out["hu_loaded"] = loaded
    out["hu_missing"] = [x["hu_code"] for x in items if not x.get("loaded_at")]
    return out


@router.get("/outbound/active")
def api_list_active_shipments(request: Request, db: Session = Depends(get_db), limit: int = Query(default=20, ge=1, le=100)):
    _require_session(request)
    install_zipline_tz_schema(db)
    rows = db.execute(
        text(
            """
            SELECT id::text, warehouse_id::text, channel, status, marketplace_status, created_at, created_by
            FROM zipline_shipments
            WHERE status IN ('DRAFT','READY','STAGED','LOADING')
            ORDER BY created_at DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).mappings().all()
    items = []
    for r in rows:
        item = dict(r)
        if item.get("created_at"):
            item["created_at"] = item["created_at"].isoformat()
        items.append(item)
    return {"items": items}


@router.post("/outbound/{shipment_id}/close")
def api_close_shipment(
    shipment_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    payload: CloseVarianceIn | None = None,
):
    session = _require_session(request)
    install_zipline_tz_schema(db)
    variance_reason = (payload.reason if payload else None) or None
    if variance_reason:
        variance_reason = str(variance_reason).strip() or None
    missing = db.execute(
        text(
            """
            SELECT h.hu_code
            FROM zipline_shipment_hus sh
            JOIN zipline_shipping_hu h ON h.id = sh.hu_id
            WHERE sh.shipment_id = CAST(:sid AS uuid) AND sh.loaded_at IS NULL
            """
        ),
        {"sid": str(shipment_id)},
    ).scalars().all()
    if missing:
        if not variance_reason:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Нельзя закрыть: есть незагруженные HU",
                    "missing_hu": list(missing),
                },
            )
        _require_perm(db, session, "OVERRIDE_VARIANCE")
        emit_z_event(
            db,
            event_type="Shipment.OverrideClosed",
            shipment_id=str(shipment_id),
            reason=variance_reason,
            after={"missing_hu": list(missing)},
            commit=False,
        )
    else:
        _require_perm(db, session, "SHIP_LOAD")
    db.execute(
        text(
            """
            UPDATE zipline_shipments
            SET status = 'COMPLETED', completed_at = NOW()
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"id": str(shipment_id)},
    )
    emit_z_event(db, event_type="Shipment.Completed", shipment_id=str(shipment_id), commit=True)
    return {"ok": True, "status": "COMPLETED", "variance": bool(missing), "reason": variance_reason}


# ── Marking Hub ─────────────────────────────────────────────────


class MarkingUploadIn(BaseModel):
    client_id: str | None = None
    sku_id: str | None = None
    article: str | None = None
    codes: list[str] = Field(default_factory=list)


@router.post("/marking/pool")
def api_marking_upload(payload: MarkingUploadIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "INTEGRATION_DIAG")
    install_zipline_tz_schema(db)
    added = 0
    for code in payload.codes:
        code = str(code).strip()
        if not code:
            continue
        result = db.execute(
            text(
                """
                INSERT INTO zipline_marking_codes(id, client_id, sku_id, article, code, source, workflow_status)
                VALUES (CAST(:id AS uuid), CAST(:client AS uuid), CAST(:sku AS uuid), :article, :code, 'POOL', 'AVAILABLE')
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "client": payload.client_id,
                "sku": payload.sku_id,
                "article": payload.article,
                "code": code,
            },
        )
        if result.rowcount:
            added += 1
    db.commit()
    return {"added": added}


class MarkingBindIn(BaseModel):
    code: str
    order_id: str | None = None
    hu_id: str | None = None
    sku_id: str | None = None


@router.post("/marking/bind")
def api_marking_bind(payload: MarkingBindIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "PACK_SCAN")
    install_zipline_tz_schema(db)
    row = db.execute(
        text("SELECT id::text, workflow_status, order_id::text, sku_id::text FROM zipline_marking_codes WHERE code = :c"),
        {"c": payload.code.strip()},
    ).mappings().first()
    if row:
        if row["workflow_status"] in {"USED", "BOUND_TO_ORDER"} and row.get("order_id") and payload.order_id and row["order_id"] != payload.order_id:
            raise HTTPException(status_code=409, detail="Код уже использован в другом заказе")
        if row["workflow_status"] == "USED":
            raise HTTPException(status_code=409, detail="Код уже использован")
        # recovery same order/HU
        if row["workflow_status"] in {"SCANNED", "BOUND_TO_ORDER"} and row.get("order_id") == payload.order_id:
            return {"status": "ALREADY_BOUND", "action": "REPRINT", "code": payload.code}
    else:
        db.execute(
            text(
                """
                INSERT INTO zipline_marking_codes(id, sku_id, code, source, workflow_status, order_id, hu_id, scanned_at)
                VALUES (CAST(:id AS uuid), CAST(:sku AS uuid), :code, 'SCAN', 'SCANNED', CAST(:oid AS uuid), CAST(:hu AS uuid), NOW())
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "sku": payload.sku_id,
                "code": payload.code.strip(),
                "oid": payload.order_id,
                "hu": None,
            },
        )
        db.commit()
        return {"status": "SCANNED", "code": payload.code}
    db.execute(
        text(
            """
            UPDATE zipline_marking_codes
            SET workflow_status = 'BOUND_TO_ORDER', order_id = CAST(:oid AS uuid),
                hu_id = CAST(:hu AS uuid), scanned_at = NOW()
            WHERE code = :c
            """
        ),
        {"oid": payload.order_id, "hu": None, "c": payload.code.strip()},
    )
    emit_z_event(db, event_type="Marking.Bound", order_id=payload.order_id, chz=payload.code, commit=True)
    return {"status": "BOUND_TO_ORDER", "code": payload.code}


# ── Mobile Print Station ────────────────────────────────────────


class MobilePrintCreateIn(BaseModel):
    station_code: str
    printer_name: str | None = None


@router.post("/print/mobile-station")
def api_mobile_print_create(payload: MobilePrintCreateIn, request: Request, db: Session = Depends(get_db)):
    """Create Mobile Print Station QR — delegates to Print Gateway (§7.3)."""
    session = _require_session(request)
    _require_perm(db, session, "PRINT_LABEL")
    from app.services.print_gateway import create_mobile_station

    return create_mobile_station(
        db,
        station_code=payload.station_code,
        printer_name=payload.printer_name,
    )


class MobilePrintBindIn(BaseModel):
    printer_qr: str
    device_id: str
    minutes: int = Field(default=120, ge=5, le=24 * 60)


@router.post("/print/mobile-station/bind")
def api_mobile_print_bind(payload: MobilePrintBindIn, request: Request, db: Session = Depends(get_db)):
    """Bind TSD/device to mobile printer QR — Print Gateway façade."""
    session = _require_session(request)
    from app.services.print_gateway import bind_mobile_station

    try:
        result = bind_mobile_station(
            db,
            printer_qr=payload.printer_qr,
            device_id=payload.device_id,
            employee_id=session.get("user_id"),
            minutes=payload.minutes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    emit_z_event(
        db,
        event_type="Print.MobileStationBound",
        workstation_id=result.get("station_code"),
        device_id=payload.device_id,
        after=result,
        commit=True,
    )
    return result


# ── TSD QR + PIN ────────────────────────────────────────────────


class TsdPinSetIn(BaseModel):
    pin: str = Field(min_length=4, max_length=8)


@router.post("/tsd/pin")
def api_set_own_pin(payload: TsdPinSetIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    if not session.get("user_id"):
        raise HTTPException(status_code=400, detail="PIN доступен только сотруднику FC")
    install_zipline_tz_schema(db)
    db.execute(
        text("UPDATE fulfillment_users SET pin_hash = :p WHERE id = CAST(:id AS uuid)"),
        {"p": hash_pin(payload.pin), "id": session["user_id"]},
    )
    db.commit()
    return {"ok": True}


@router.get("/tsd/qr-token")
def api_tsd_qr_token(request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    if not session.get("user_id") or not session.get("fulfillment_id"):
        raise HTTPException(status_code=400, detail="Нужен пользователь FC")
    install_zipline_tz_schema(db)
    token = secrets.token_urlsafe(24)
    expires = _utcnow() + timedelta(hours=12)
    db.execute(
        text(
            """
            INSERT INTO zipline_tsd_pin_tokens(token, user_id, fulfillment_id, expires_at)
            VALUES (:t, CAST(:u AS uuid), CAST(:f AS uuid), :e)
            """
        ),
        {"t": token, "u": session["user_id"], "f": session["fulfillment_id"], "e": expires},
    )
    db.commit()
    return {
        "token": token,
        "expires_at": expires.isoformat(),
        "qr_payload": f"ZIPLINE-TSD:{token}",
        "login_hint": "Отсканируйте QR на ТСД и введите PIN",
    }


class TsdQrPinLogin(BaseModel):
    token: str
    pin: str = Field(min_length=4, max_length=8)
    device_id: str | None = None


@router.post("/tsd/login-qr-pin")
def api_tsd_login_qr_pin(payload: TsdQrPinLogin, request: Request, db: Session = Depends(get_db)):
    from fastapi.responses import JSONResponse

    install_zipline_tz_schema(db)
    row = db.execute(
        text(
            """
            SELECT t.user_id::text, t.fulfillment_id::text, t.expires_at,
                   u.username, u.role, u.pin_hash, u.permissions, u.is_active
            FROM zipline_tsd_pin_tokens t
            JOIN fulfillment_users u ON u.id = t.user_id
            WHERE t.token = :t
            """
        ),
        {"t": payload.token.strip()},
    ).mappings().first()
    if not row or not row["is_active"]:
        raise HTTPException(status_code=401, detail="Неверный или просроченный QR")
    if row["expires_at"] < _utcnow():
        raise HTTPException(status_code=401, detail="QR истёк — получите новый")
    if not verify_pin(payload.pin, row["pin_hash"]):
        raise HTTPException(status_code=401, detail="Неверный PIN")
    token = create_session_token(
        row["username"],
        settings.app_secret_key,
        settings.session_ttl_hours,
        principal="fc",
        fulfillment_id=row["fulfillment_id"],
        role=row["role"],
        user_id=row["user_id"],
    )
    emit_z_event(
        db,
        event_type="TSD.LoginQrPin",
        tenant_id=row["fulfillment_id"],
        employee_id=row["user_id"],
        device_id=payload.device_id,
        commit=True,
    )
    response = JSONResponse(
        {
            "ok": True,
            "username": row["username"],
            "role": row["role"],
            "fulfillment_id": row["fulfillment_id"],
            "user_id": row["user_id"],
            "permissions": permissions_for_role(row["role"], row.get("permissions")),
        }
    )
    response.set_cookie(
        "fc_session",
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.secure_cookie,
        samesite="lax",
        path="/",
    )
    return response


# ── Helper / Operation Guide ────────────────────────────────────


@router.get("/helper/guides")
def api_helper_guides(request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    install_zipline_tz_schema(db)
    role = (session.get("role") or "").upper()
    rows = db.execute(text("SELECT id, title, steps, roles, module_id FROM zipline_operation_guides ORDER BY title")).mappings().all()
    items = []
    for r in rows:
        roles = r["roles"] or []
        if roles and role and role not in roles and role not in {"FC_ADMIN", "PLATFORM_ADMIN", "SUPERVISOR"}:
            continue
        items.append(
            {
                "id": r["id"],
                "title": r["title"],
                "module_id": r["module_id"],
                "steps": r["steps"],
            }
        )
    return {"items": items}


@router.post("/helper/start/{guide_id}")
def api_helper_start(guide_id: str, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    install_zipline_tz_schema(db)
    row = db.execute(
        text("SELECT id, title, steps, module_id FROM zipline_operation_guides WHERE id = :id"),
        {"id": guide_id},
    ).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Сценарий не найден")
    emit_z_event(
        db,
        event_type="Help.GuideStarted",
        employee_id=session.get("user_id"),
        after={"guide_id": guide_id, "title": row["title"]},
        commit=True,
    )
    return {
        "guide_id": row["id"],
        "title": row["title"],
        "module_id": row["module_id"],
        "steps": row["steps"],
        "active": True,
    }


# ── Finance opening + billing charge ────────────────────────────


class OpeningSnapshotIn(BaseModel):
    snapshot: dict[str, Any]
    tenant_id: str | None = None


@router.post("/finance/opening")
def api_finance_opening(payload: OpeningSnapshotIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "FINANCE_CLOSE")
    install_zipline_tz_schema(db)
    existing = db.execute(
        text("SELECT id::text FROM zipline_finance_opening WHERE locked = TRUE LIMIT 1")
    ).scalar()
    if existing:
        raise HTTPException(status_code=409, detail="Opening snapshot уже зафиксирован — правки только отдельными corrections")
    oid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO zipline_finance_opening(id, tenant_id, snapshot, created_by, locked)
            VALUES (CAST(:id AS uuid), CAST(:t AS uuid), CAST(:s AS jsonb), :by, TRUE)
            """
        ),
        {
            "id": str(oid),
            "t": payload.tenant_id or session.get("fulfillment_id"),
            "s": json.dumps(payload.snapshot, ensure_ascii=False),
            "by": session.get("username"),
        },
    )
    emit_z_event(db, event_type="Finance.OpeningSnapshot", after={"id": str(oid)}, commit=True)
    return {"id": str(oid), "locked": True}


class BillingChargeIn(BaseModel):
    client_id: str | None = None
    source_event_id: str | None = None
    tariff_version: str = "v1"
    amount: float
    category: str
    evidence: dict[str, Any] | None = None


@router.post("/finance/charges")
def api_billing_charge(payload: BillingChargeIn, request: Request, db: Session = Depends(get_db)):
    session = _require_session(request)
    _require_perm(db, session, "FINANCE_CLOSE")
    install_zipline_tz_schema(db)
    cid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO zipline_billing_charges(
                id, tenant_id, client_id, source_event_id, tariff_version, amount, category, evidence
            ) VALUES (
                CAST(:id AS uuid), CAST(:t AS uuid), CAST(:c AS uuid), CAST(:e AS uuid),
                :tv, :amount, :cat, CAST(:ev AS jsonb)
            )
            """
        ),
        {
            "id": str(cid),
            "t": session.get("fulfillment_id"),
            "c": payload.client_id,
            "e": payload.source_event_id,
            "tv": payload.tariff_version,
            "amount": payload.amount,
            "cat": payload.category,
            "ev": json.dumps(payload.evidence or {}),
        },
    )
    emit_z_event(
        db,
        event_type="Billing.ChargePosted",
        client_id=payload.client_id,
        after={"id": str(cid), "amount": payload.amount, "tariff_version": payload.tariff_version},
        commit=True,
    )
    return {"id": str(cid), "status": "POSTED"}


@router.get("/finance/cashflow-horizon")
def api_cashflow_horizon(request: Request, db: Session = Depends(get_db)):
    """13-week cashflow scaffold from opening + posted charges."""
    session = _require_session(request)
    _require_perm(db, session, "FINANCE_VIEW")
    install_zipline_tz_schema(db)
    opening = db.execute(
        text("SELECT snapshot FROM zipline_finance_opening ORDER BY created_at DESC LIMIT 1")
    ).scalar() or {}
    charges = db.execute(
        text("SELECT COALESCE(SUM(amount),0) FROM zipline_billing_charges WHERE status = 'POSTED'")
    ).scalar() or 0
    bank = float((opening or {}).get("bank_cash") or (opening or {}).get("bank") or 0)
    weekly = []
    cash = bank + float(charges)
    for week in range(1, 14):
        # deterministic scaffold: linear burn if permanent expenses set
        burn = float((opening or {}).get("monthly_fixed_expenses") or 0) / 4.3
        cash = cash - burn
        weekly.append({"week": week, "projected_cash": round(cash, 2), "gap": cash < 0})
    first_gap = next((w for w in weekly if w["gap"]), None)
    return {
        "horizon_weeks": 13,
        "opening_bank": bank,
        "posted_charges": float(charges),
        "weeks": weekly,
        "first_cash_gap": first_gap,
        "note": "Прогноз, не бухгалтерская проводка",
    }
