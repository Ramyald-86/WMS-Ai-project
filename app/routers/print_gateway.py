"""Print Gateway HTTP API (TZ §7.3)."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.auth import read_session_token
from app.core.config import settings
from app.core.database import get_db
from app.services.print_gateway import (
    LABEL_TYPES,
    PRINTER_KINDS,
    bind_mobile_station,
    create_mobile_station,
    enqueue_via_gateway,
    install_print_gateway_schema,
    resolve_device_station,
    resolve_route,
    sync_gateway_job_status,
)
from app.services.z_event import emit_z_event

router = APIRouter(prefix="/printing/gateway", tags=["Print Gateway"])


def _session(request: Request) -> dict[str, Any]:
    if getattr(request.state, "username", None):
        return {
            "username": request.state.username,
            "principal": getattr(request.state, "auth_principal", None),
            "role": getattr(request.state, "auth_role", None),
            "user_id": getattr(request.state, "auth_user_id", None),
            "fulfillment_id": getattr(request.state, "auth_fulfillment_id", None),
        }
    session = read_session_token(request.cookies.get("fc_session"), settings.app_secret_key)
    if not session:
        raise HTTPException(status_code=401, detail="Требуется вход")
    return {
        "username": session.get("u"),
        "principal": session.get("t") or "platform",
        "role": session.get("role"),
        "user_id": session.get("uid"),
        "fulfillment_id": session.get("fid"),
    }


@router.post("/install-schema")
def api_install(request: Request, db: Session = Depends(get_db)):
    _session(request)
    return install_print_gateway_schema(db)


@router.get("/meta")
def api_meta(request: Request, db: Session = Depends(get_db)):
    _session(request)
    install_print_gateway_schema(db)
    return {"label_types": list(LABEL_TYPES), "printer_kinds": list(PRINTER_KINDS)}


# ── resolve / enqueue ───────────────────────────────────────────


class ResolveIn(BaseModel):
    label_type: str = "GENERIC"
    workstation_code: str | None = None
    zone_code: str | None = None
    warehouse_id: str | None = None
    preferred_station: str | None = None
    device_id: str | None = None


@router.post("/resolve")
def api_resolve(payload: ResolveIn, request: Request, db: Session = Depends(get_db)):
    _session(request)
    preferred = payload.preferred_station
    if payload.device_id:
        mobile = resolve_device_station(db, payload.device_id)
        if mobile:
            preferred = mobile
    return resolve_route(
        db,
        label_type=payload.label_type,
        workstation_code=payload.workstation_code,
        zone_code=payload.zone_code,
        warehouse_id=payload.warehouse_id,
        preferred_station=preferred,
    )


class EnqueueIn(BaseModel):
    label_type: str = "GENERIC"
    file_name: str = "label.bin"
    content_base64: str | None = None
    content_type: str = "application/pdf"
    business_id: str | None = None
    object_type: str | None = None
    object_id: str | None = None
    client_id: str | None = None
    warehouse_id: str | None = None
    workstation_code: str | None = None
    zone_code: str | None = None
    preferred_station: str | None = None
    device_id: str | None = None
    force_reprint: bool = False
    reason: str | None = None


@router.post("/enqueue")
def api_enqueue(payload: EnqueueIn, request: Request, db: Session = Depends(get_db)):
    session = _session(request)
    preferred = payload.preferred_station
    if payload.device_id:
        mobile = resolve_device_station(db, payload.device_id)
        if mobile:
            preferred = mobile
    raw = b""
    if payload.content_base64:
        try:
            raw = base64.b64decode(payload.content_base64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Некорректный content_base64") from exc
    if not raw and not payload.force_reprint:
        raise HTTPException(status_code=400, detail="Нужен content_base64 или force_reprint с business_id")
    try:
        result = enqueue_via_gateway(
            db,
            payload=raw,
            file_name=payload.file_name,
            label_type=payload.label_type,
            business_id=payload.business_id,
            client_id=uuid.UUID(payload.client_id) if payload.client_id else None,
            object_type=payload.object_type,
            object_id=payload.object_id,
            warehouse_id=payload.warehouse_id,
            workstation_code=payload.workstation_code,
            zone_code=payload.zone_code,
            preferred_station=preferred,
            content_type=payload.content_type,
            actor=session.get("username"),
            reason=payload.reason,
            force_reprint=payload.force_reprint,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    emit_z_event(
        db,
        event_type="Print.GatewayEnqueued" if not payload.force_reprint else "Print.Reprinted",
        warehouse_id=payload.warehouse_id,
        employee_id=session.get("user_id"),
        workstation_id=result.get("station_code"),
        reason=payload.reason,
        after=result,
        commit=True,
    )
    return result


class ReprintIn(BaseModel):
    business_id: str
    label_type: str = "GENERIC"
    reason: str = Field(min_length=3, max_length=500)
    preferred_station: str | None = None
    device_id: str | None = None


@router.post("/reprint")
def api_reprint(payload: ReprintIn, request: Request, db: Session = Depends(get_db)):
    """Reprint same business identifier — never creates a new barcode/HU."""
    session = _session(request)
    preferred = payload.preferred_station
    if payload.device_id:
        mobile = resolve_device_station(db, payload.device_id)
        if mobile:
            preferred = mobile
    try:
        result = enqueue_via_gateway(
            db,
            payload=b"",
            file_name="reprint.bin",
            label_type=payload.label_type,
            business_id=payload.business_id,
            preferred_station=preferred,
            actor=session.get("username"),
            reason=payload.reason,
            force_reprint=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    emit_z_event(
        db,
        event_type="Print.Reprinted",
        employee_id=session.get("user_id"),
        workstation_id=result.get("station_code"),
        reason=payload.reason,
        after={"business_id": payload.business_id, **result},
        commit=True,
    )
    return result


@router.get("/jobs")
def api_jobs(
    request: Request,
    db: Session = Depends(get_db),
    business_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    _session(request)
    install_print_gateway_schema(db)
    params: dict[str, Any] = {"limit": limit}
    clause = ""
    if business_id:
        clause = "WHERE business_id = :b"
        params["b"] = business_id
    rows = db.execute(
        text(
            f"""
            SELECT id::text, print_job_id::text, business_id, label_type, template_code,
                   printer_code, station_code, status, is_reprint, reason, actor, created_at
            FROM print_gateway_jobs
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
        if item.get("print_job_id"):
            sync_gateway_job_status(db, item["print_job_id"])
        items.append(item)
    return {"items": items}


# ── printers / templates / routes / edges ───────────────────────


class PrinterIn(BaseModel):
    code: str
    name: str
    kind: Literal["LAN", "WIFI", "USB_EDGE", "MOBILE"] = "LAN"
    station_code: str
    warehouse_id: str | None = None
    zone_code: str | None = None
    edge_id: str | None = None
    is_default: bool = False
    capabilities: dict[str, Any] | None = None


@router.get("/printers")
def api_list_printers(request: Request, db: Session = Depends(get_db)):
    _session(request)
    install_print_gateway_schema(db)
    rows = db.execute(
        text(
            """
            SELECT id::text, code, name, kind, station_code, warehouse_id::text, zone_code,
                   edge_id::text, capabilities, is_active, is_default
            FROM print_gateway_printers
            ORDER BY is_default DESC, code
            """
        )
    ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("/printers")
def api_upsert_printer(payload: PrinterIn, request: Request, db: Session = Depends(get_db)):
    _session(request)
    install_print_gateway_schema(db)
    code = payload.code.strip().upper()
    station = payload.station_code.strip().upper()
    existing = db.execute(
        text("SELECT id::text FROM print_gateway_printers WHERE code = :c"),
        {"c": code},
    ).scalar()
    if existing:
        db.execute(
            text(
                """
                UPDATE print_gateway_printers SET
                    name = :name, kind = :kind, station_code = :st,
                    warehouse_id = CAST(:wh AS uuid), zone_code = :zone,
                    edge_id = CAST(:edge AS uuid), is_default = :def,
                    capabilities = CAST(:cap AS jsonb), is_active = TRUE
                WHERE code = :c
                """
            ),
            {
                "name": payload.name,
                "kind": payload.kind,
                "st": station,
                "wh": payload.warehouse_id,
                "zone": payload.zone_code,
                "edge": payload.edge_id,
                "def": payload.is_default,
                "cap": json.dumps(payload.capabilities or {}),
                "c": code,
            },
        )
        pid = existing
    else:
        pid = str(uuid.uuid4())
        db.execute(
            text(
                """
                INSERT INTO print_gateway_printers(
                    id, code, name, kind, station_code, warehouse_id, zone_code,
                    edge_id, capabilities, is_active, is_default
                ) VALUES (
                    CAST(:id AS uuid), :code, :name, :kind, :st, CAST(:wh AS uuid), :zone,
                    CAST(:edge AS uuid), CAST(:cap AS jsonb), TRUE, :def
                )
                """
            ),
            {
                "id": pid,
                "code": code,
                "name": payload.name,
                "kind": payload.kind,
                "st": station,
                "wh": payload.warehouse_id,
                "zone": payload.zone_code,
                "edge": payload.edge_id,
                "cap": json.dumps(payload.capabilities or {}),
                "def": payload.is_default,
            },
        )
    if payload.is_default:
        db.execute(
            text("UPDATE print_gateway_printers SET is_default = FALSE WHERE id <> CAST(:id AS uuid)"),
            {"id": pid},
        )
    db.commit()
    return {"id": pid, "code": code, "station_code": station}


@router.get("/templates")
def api_templates(request: Request, db: Session = Depends(get_db)):
    _session(request)
    install_print_gateway_schema(db)
    rows = db.execute(
        text(
            """
            SELECT id::text, code, label_type, name, width_mm, height_mm, is_active
            FROM print_gateway_templates ORDER BY label_type, code
            """
        )
    ).mappings().all()
    return {"items": [dict(r) for r in rows]}


class RouteIn(BaseModel):
    priority: int = 100
    warehouse_id: str | None = None
    workstation_code: str | None = None
    zone_code: str | None = None
    label_type: str | None = None
    printer_id: str
    template_id: str | None = None


@router.get("/routes")
def api_routes(request: Request, db: Session = Depends(get_db)):
    _session(request)
    install_print_gateway_schema(db)
    rows = db.execute(
        text(
            """
            SELECT r.id::text, r.priority, r.warehouse_id::text, r.workstation_code, r.zone_code,
                   r.label_type, r.printer_id::text, p.code AS printer_code, p.station_code,
                   r.template_id::text, t.code AS template_code, r.is_active
            FROM print_gateway_routes r
            JOIN print_gateway_printers p ON p.id = r.printer_id
            LEFT JOIN print_gateway_templates t ON t.id = r.template_id
            ORDER BY r.priority, r.label_type NULLS LAST
            """
        )
    ).mappings().all()
    return {"items": [dict(r) for r in rows]}


@router.post("/routes")
def api_add_route(payload: RouteIn, request: Request, db: Session = Depends(get_db)):
    _session(request)
    install_print_gateway_schema(db)
    rid = str(uuid.uuid4())
    db.execute(
        text(
            """
            INSERT INTO print_gateway_routes(
                id, priority, warehouse_id, workstation_code, zone_code, label_type,
                printer_id, template_id, is_active
            ) VALUES (
                CAST(:id AS uuid), :pr, CAST(:wh AS uuid), :ws, :zone, :lt,
                CAST(:pid AS uuid), CAST(:tid AS uuid), TRUE
            )
            """
        ),
        {
            "id": rid,
            "pr": payload.priority,
            "wh": payload.warehouse_id,
            "ws": (payload.workstation_code or "").strip().upper() or None,
            "zone": (payload.zone_code or "").strip().upper() or None,
            "lt": (payload.label_type or "").strip().upper() or None,
            "pid": payload.printer_id,
            "tid": payload.template_id,
        },
    )
    db.commit()
    return {"id": rid}


@router.get("/edges")
def api_edges(request: Request, db: Session = Depends(get_db)):
    _session(request)
    install_print_gateway_schema(db)
    rows = db.execute(
        text(
            """
            SELECT id::text, warehouse_id::text, code, name, host, is_active, notes
            FROM print_gateway_edges ORDER BY code
            """
        )
    ).mappings().all()
    return {"items": [dict(r) for r in rows]}


class EdgeIn(BaseModel):
    code: str
    name: str | None = None
    host: str | None = None
    warehouse_id: str | None = None
    notes: str | None = None


@router.post("/edges")
def api_upsert_edge(payload: EdgeIn, request: Request, db: Session = Depends(get_db)):
    _session(request)
    install_print_gateway_schema(db)
    code = payload.code.strip().upper()
    existing = db.execute(
        text("SELECT id::text FROM print_gateway_edges WHERE code = :c"), {"c": code}
    ).scalar()
    if existing:
        db.execute(
            text(
                """
                UPDATE print_gateway_edges
                SET name = :name, host = :host, warehouse_id = CAST(:wh AS uuid),
                    notes = :notes, is_active = TRUE
                WHERE code = :c
                """
            ),
            {
                "name": payload.name,
                "host": payload.host,
                "wh": payload.warehouse_id,
                "notes": payload.notes,
                "c": code,
            },
        )
        eid = existing
    else:
        eid = str(uuid.uuid4())
        db.execute(
            text(
                """
                INSERT INTO print_gateway_edges(id, warehouse_id, code, name, host, notes, is_active)
                VALUES (CAST(:id AS uuid), CAST(:wh AS uuid), :code, :name, :host, :notes, TRUE)
                """
            ),
            {
                "id": eid,
                "wh": payload.warehouse_id,
                "code": code,
                "name": payload.name,
                "host": payload.host,
                "notes": payload.notes,
            },
        )
    db.commit()
    return {"id": eid, "code": code}


# ── Mobile Print Station ────────────────────────────────────────


class MobileCreateIn(BaseModel):
    station_code: str
    printer_name: str | None = None
    warehouse_id: str | None = None


@router.post("/mobile-station")
def api_mobile_create(payload: MobileCreateIn, request: Request, db: Session = Depends(get_db)):
    _session(request)
    result = create_mobile_station(
        db,
        station_code=payload.station_code,
        printer_name=payload.printer_name,
        warehouse_id=payload.warehouse_id,
    )
    return result


class MobileBindIn(BaseModel):
    printer_qr: str
    device_id: str
    minutes: int = Field(default=120, ge=5, le=24 * 60)


@router.post("/mobile-station/bind")
def api_mobile_bind(payload: MobileBindIn, request: Request, db: Session = Depends(get_db)):
    session = _session(request)
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
        employee_id=session.get("user_id"),
        device_id=payload.device_id,
        workstation_id=result.get("station_code"),
        after=result,
        commit=True,
    )
    return result


@router.get("/mobile-station/for-device")
def api_mobile_for_device(
    request: Request,
    db: Session = Depends(get_db),
    device_id: str = Query(min_length=1),
):
    _session(request)
    station = resolve_device_station(db, device_id)
    return {"device_id": device_id, "station_code": station, "bound": bool(station)}


@router.get("/mobile-stations")
def api_list_mobile(request: Request, db: Session = Depends(get_db)):
    _session(request)
    install_print_gateway_schema(db)
    rows = db.execute(
        text(
            """
            SELECT id::text, printer_qr, station_code, printer_name, warehouse_id::text,
                   bound_device_id, bound_until, created_at
            FROM print_gateway_mobile_stations
            ORDER BY created_at DESC
            LIMIT 100
            """
        )
    ).mappings().all()
    items = []
    for r in rows:
        item = dict(r)
        for k in ("bound_until", "created_at"):
            if item.get(k):
                item[k] = item[k].isoformat()
        items.append(item)
    return {"items": items}
