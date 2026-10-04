from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.services.runtime_reliability import (
    get_workstation_profile,
    merge_workstation_printer_profile,
    normalize_station_code,
    save_workstation_profile,
)

router = APIRouter(prefix="/system", tags=["Надёжность"])


class WorkstationProfileUpdate(BaseModel):
    client_id: str | None = None
    warehouse_id: str | None = None
    printer_profile: dict[str, Any] | None = Field(default=None)
    scanner_profile: dict[str, Any] | None = Field(default=None)


@router.get("/workstations/{station_code}")
def workstation_profile(station_code: str, db: Session = Depends(get_db)):
    return get_workstation_profile(db, normalize_station_code(station_code))


@router.put("/workstations/{station_code}")
def update_workstation_profile(
    station_code: str,
    payload: WorkstationProfileUpdate,
    db: Session = Depends(get_db),
):
    station = normalize_station_code(station_code)
    if not station:
        raise HTTPException(status_code=400, detail="Не указан код рабочего места")
    return save_workstation_profile(
        db,
        station,
        client_id=payload.client_id,
        warehouse_id=payload.warehouse_id,
        printer_profile=payload.printer_profile,
        scanner_profile=payload.scanner_profile,
    )

agent_router = APIRouter(prefix="/printing/agent", tags=["Print Agent"])


@agent_router.get("/profile")
def print_agent_profile(station_code: str, db: Session = Depends(get_db)):
    """Small profile payload polled by Print Agent 1.2.x.

    Authentication and station binding are enforced by the global middleware before
    this handler executes.
    """
    profile = get_workstation_profile(db, normalize_station_code(station_code))
    return {
        "station_code": profile["station_code"],
        "printer_profile": profile.get("printer_profile") or {},
        "updated_epoch": profile.get("updated_epoch") or 0,
    }


@agent_router.post("/profile")
def update_print_agent_profile(
    station_code: str,
    payload: dict[str, Any],
    db: Session = Depends(get_db),
):
    station = normalize_station_code(station_code)
    incoming = payload.get("printer_profile") if isinstance(payload, dict) else None
    agent_updates: dict[str, Any] = {}
    if isinstance(incoming, dict):
        # Agent-owned capabilities only; browser-owned label geometry may coexist.
        for key in (
            "printer_name", "printer_language", "printer_dpi", "source_png_dpi",
            "label_gap_mm", "label_direction", "label_density", "label_speed",
            "label_x_offset_dots", "label_y_offset_dots", "label_polarity", "pdf_scale_mode",
        ):
            if key in incoming:
                agent_updates[key] = incoming[key]
    return merge_workstation_printer_profile(db, station, agent_updates)
