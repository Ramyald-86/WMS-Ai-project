"""Print Gateway (TZ §7.3) — routing façade over existing PrintJob / Print Agent.

WMS creates the business identifier and payload. Gateway only selects
template/printer/queue and never mints a new barcode/HU id on printer failure.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.printing import queue_print_job

_SCHEMA_READY = False

PRINTER_KINDS = ("LAN", "WIFI", "USB_EDGE", "MOBILE")
LABEL_TYPES = (
    "WB_ORDER",
    "WB_PRODUCT",
    "WB_SUPPLY",
    "BOX",
    "LOCATION",
    "HU",
    "GENERIC",
    "TEST",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def install_print_gateway_schema(db: Session) -> dict[str, Any]:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return {"ok": True, "cached": True}

    # Serialize concurrent workers (uvicorn --workers N) installing DDL.
    try:
        db.execute(text("SELECT pg_advisory_xact_lock(87231073)"))
    except Exception:
        pass

    statements = [
        """
        CREATE TABLE IF NOT EXISTS print_gateway_edges (
            id UUID PRIMARY KEY,
            warehouse_id UUID NULL,
            code VARCHAR(64) NOT NULL UNIQUE,
            name VARCHAR(255) NULL,
            host VARCHAR(255) NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            notes TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS print_gateway_printers (
            id UUID PRIMARY KEY,
            code VARCHAR(64) NOT NULL UNIQUE,
            name VARCHAR(255) NOT NULL,
            kind VARCHAR(32) NOT NULL DEFAULT 'LAN',
            station_code VARCHAR(64) NOT NULL,
            warehouse_id UUID NULL,
            zone_code VARCHAR(64) NULL,
            edge_id UUID NULL REFERENCES print_gateway_edges(id) ON DELETE SET NULL,
            capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS print_gateway_templates (
            id UUID PRIMARY KEY,
            code VARCHAR(64) NOT NULL UNIQUE,
            label_type VARCHAR(64) NOT NULL,
            name VARCHAR(255) NOT NULL,
            width_mm INT NOT NULL DEFAULT 58,
            height_mm INT NOT NULL DEFAULT 40,
            meta JSONB NOT NULL DEFAULT '{}'::jsonb,
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS print_gateway_routes (
            id UUID PRIMARY KEY,
            priority INT NOT NULL DEFAULT 100,
            warehouse_id UUID NULL,
            workstation_code VARCHAR(64) NULL,
            zone_code VARCHAR(64) NULL,
            label_type VARCHAR(64) NULL,
            printer_id UUID NOT NULL REFERENCES print_gateway_printers(id) ON DELETE CASCADE,
            template_id UUID NULL REFERENCES print_gateway_templates(id) ON DELETE SET NULL,
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS print_gateway_jobs (
            id UUID PRIMARY KEY,
            print_job_id UUID NULL,
            business_id VARCHAR(190) NULL,
            label_type VARCHAR(64) NOT NULL,
            template_code VARCHAR(64) NULL,
            printer_code VARCHAR(64) NULL,
            station_code VARCHAR(64) NOT NULL,
            warehouse_id UUID NULL,
            workstation_code VARCHAR(64) NULL,
            zone_code VARCHAR(64) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'QUEUED',
            is_reprint BOOLEAN NOT NULL DEFAULT FALSE,
            reprint_of UUID NULL,
            reason TEXT NULL,
            actor VARCHAR(120) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_pgw_jobs_business ON print_gateway_jobs(business_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_pgw_jobs_print_job ON print_gateway_jobs(print_job_id)",
        "CREATE INDEX IF NOT EXISTS ix_pgw_routes_priority ON print_gateway_routes(priority, is_active)",
        """
        CREATE TABLE IF NOT EXISTS print_gateway_mobile_stations (
            id UUID PRIMARY KEY,
            printer_qr VARCHAR(120) NOT NULL UNIQUE,
            printer_id UUID NULL REFERENCES print_gateway_printers(id) ON DELETE SET NULL,
            station_code VARCHAR(64) NOT NULL,
            printer_name VARCHAR(255) NULL,
            warehouse_id UUID NULL,
            bound_device_id VARCHAR(120) NULL,
            bound_employee_id UUID NULL,
            bound_until TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
    ]
    try:
        for sql in statements:
            db.execute(text(sql))
    except Exception as exc:
        # Concurrent worker may have created the same enum/table types first.
        db.rollback()
        msg = str(exc).lower()
        if "unique" not in msg and "already exists" not in msg and "duplicate" not in msg:
            raise
        try:
            db.execute(text("SELECT pg_advisory_xact_lock(87231073)"))
            for sql in statements:
                db.execute(text(sql))
        except Exception:
            db.rollback()
            # Tables likely already present from the other worker.
            pass

    # Seed default templates
    defaults = [
        ("TPL_WB_ORDER", "WB_ORDER", "Этикетка заказа WB", 58, 40),
        ("TPL_WB_PRODUCT", "WB_PRODUCT", "ШК товара", 58, 40),
        ("TPL_BOX", "BOX", "Этикетка короба", 58, 40),
        ("TPL_HU", "HU", "Этикетка грузового места", 58, 40),
        ("TPL_LOCATION", "LOCATION", "Этикетка ячейки", 58, 40),
        ("TPL_GENERIC", "GENERIC", "Универсальная этикетка", 58, 40),
    ]
    for code, label_type, name, w, h in defaults:
        db.execute(
            text(
                """
                INSERT INTO print_gateway_templates(id, code, label_type, name, width_mm, height_mm)
                VALUES (CAST(:id AS uuid), :code, :lt, :name, :w, :h)
                ON CONFLICT (code) DO NOTHING
                """
            ),
            {"id": str(uuid.uuid4()), "code": code, "lt": label_type, "name": name, "w": w, "h": h},
        )

    # Seed default LAN printer mapped to PACK-01 if none
    exists = db.execute(text("SELECT 1 FROM print_gateway_printers LIMIT 1")).scalar()
    if not exists:
        pid = str(uuid.uuid4())
        db.execute(
            text(
                """
                INSERT INTO print_gateway_printers(
                    id, code, name, kind, station_code, capabilities, is_active, is_default
                ) VALUES (
                    CAST(:id AS uuid), 'PACK-01', 'Упаковочный стол PACK-01', 'LAN', 'PACK-01',
                    CAST(:cap AS jsonb), TRUE, TRUE
                )
                """
            ),
            {"id": pid, "cap": json.dumps({"labels": True, "agent": True})},
        )
        # Default catch-all route
        for lt in LABEL_TYPES:
            db.execute(
                text(
                    """
                    INSERT INTO print_gateway_routes(
                        id, priority, label_type, printer_id, is_active
                    ) VALUES (
                        CAST(:id AS uuid), 900, :lt, CAST(:pid AS uuid), TRUE
                    )
                    """
                ),
                {"id": str(uuid.uuid4()), "lt": lt, "pid": pid},
            )

    # Seed warehouse edge placeholder
    edge = db.execute(text("SELECT 1 FROM print_gateway_edges LIMIT 1")).scalar()
    if not edge:
        db.execute(
            text(
                """
                INSERT INTO print_gateway_edges(id, code, name, notes, is_active)
                VALUES (
                    CAST(:id AS uuid), 'EDGE-WH1', 'Edge Print Server склада',
                    'USB-принтеры подключаются сюда, а не агентом на каждом ТСД', TRUE
                )
                """
            ),
            {"id": str(uuid.uuid4())},
        )

    db.commit()
    _SCHEMA_READY = True
    return {"ok": True, "cached": False}


def resolve_route(
    db: Session,
    *,
    label_type: str,
    workstation_code: str | None = None,
    zone_code: str | None = None,
    warehouse_id: str | None = None,
    preferred_station: str | None = None,
) -> dict[str, Any]:
    """Pick printer/template by workstation → zone → label_type priority."""
    install_print_gateway_schema(db)
    lt = (label_type or "GENERIC").strip().upper()
    ws = (workstation_code or "").strip().upper() or None
    zone = (zone_code or "").strip().upper() or None
    pref = (preferred_station or "").strip().upper() or None

    # Explicit preferred station still wins if registered / known
    if pref:
        row = db.execute(
            text(
                """
                SELECT p.id::text, p.code, p.name, p.kind, p.station_code, p.edge_id::text,
                       t.code AS template_code, t.width_mm, t.height_mm
                FROM print_gateway_printers p
                LEFT JOIN print_gateway_templates t
                  ON t.label_type = :lt AND t.is_active = TRUE
                WHERE p.is_active = TRUE AND p.station_code = :st
                ORDER BY p.is_default DESC, t.code NULLS LAST
                LIMIT 1
                """
            ),
            {"lt": lt, "st": pref},
        ).mappings().first()
        if row:
            return {
                "printer_id": row["id"],
                "printer_code": row["code"],
                "printer_name": row["name"],
                "kind": row["kind"],
                "station_code": row["station_code"],
                "edge_id": row["edge_id"],
                "template_code": row["template_code"] or f"TPL_{lt}",
                "width_mm": row["width_mm"] or 58,
                "height_mm": row["height_mm"] or 40,
                "matched_by": "preferred_station",
            }

    # Mobile bind override: device temporarily linked to a mobile printer
    # (caller may also pass workstation as device session station)

    row = db.execute(
        text(
            """
            SELECT r.id::text AS route_id, r.priority,
                   p.id::text AS printer_id, p.code AS printer_code, p.name AS printer_name,
                   p.kind, p.station_code, p.edge_id::text AS edge_id,
                   t.code AS template_code, t.width_mm, t.height_mm
            FROM print_gateway_routes r
            JOIN print_gateway_printers p ON p.id = r.printer_id AND p.is_active = TRUE
            LEFT JOIN print_gateway_templates t ON t.id = r.template_id AND t.is_active = TRUE
            WHERE r.is_active = TRUE
              AND (r.label_type IS NULL OR r.label_type = :lt)
              AND (r.workstation_code IS NULL OR r.workstation_code = :ws)
              AND (r.zone_code IS NULL OR r.zone_code = :zone)
              AND (
                    CAST(:wh AS text) IS NULL OR r.warehouse_id IS NULL
                    OR r.warehouse_id::text = CAST(:wh AS text)
                  )
            ORDER BY
              CASE WHEN r.workstation_code IS NOT NULL THEN 0 ELSE 1 END,
              CASE WHEN r.zone_code IS NOT NULL THEN 0 ELSE 1 END,
              CASE WHEN r.label_type IS NOT NULL THEN 0 ELSE 1 END,
              r.priority ASC
            LIMIT 1
            """
        ),
        {"lt": lt, "ws": ws, "zone": zone, "wh": warehouse_id},
    ).mappings().first()

    if not row:
        # Fallback: default printer
        row = db.execute(
            text(
                """
                SELECT p.id::text AS printer_id, p.code AS printer_code, p.name AS printer_name,
                       p.kind, p.station_code, p.edge_id::text AS edge_id,
                       t.code AS template_code, t.width_mm, t.height_mm
                FROM print_gateway_printers p
                LEFT JOIN print_gateway_templates t
                  ON t.label_type = :lt AND t.is_active = TRUE
                WHERE p.is_active = TRUE
                ORDER BY p.is_default DESC, p.code
                LIMIT 1
                """
            ),
            {"lt": lt},
        ).mappings().first()

    if not row:
        return {
            "printer_id": None,
            "printer_code": pref or "PACK-01",
            "printer_name": "Fallback station",
            "kind": "LAN",
            "station_code": pref or "PACK-01",
            "edge_id": None,
            "template_code": f"TPL_{lt}",
            "width_mm": 58,
            "height_mm": 40,
            "matched_by": "hard_fallback",
        }

    return {
        "printer_id": row.get("printer_id"),
        "printer_code": row["printer_code"],
        "printer_name": row["printer_name"],
        "kind": row["kind"],
        "station_code": row["station_code"],
        "edge_id": row.get("edge_id"),
        "template_code": row.get("template_code") or f"TPL_{lt}",
        "width_mm": row.get("width_mm") or 58,
        "height_mm": row.get("height_mm") or 40,
        "matched_by": "route" if row.get("route_id") else "default_printer",
        "route_id": row.get("route_id"),
        "priority": row.get("priority"),
    }


def enqueue_via_gateway(
    db: Session,
    *,
    payload: bytes,
    file_name: str,
    label_type: str,
    business_id: str | None = None,
    client_id: uuid.UUID | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    warehouse_id: str | None = None,
    workstation_code: str | None = None,
    zone_code: str | None = None,
    preferred_station: str | None = None,
    content_type: str = "application/pdf",
    actor: str | None = None,
    reason: str | None = None,
    force_reprint: bool = False,
) -> dict[str, Any]:
    """Create PrintJob through Gateway routing.

    If ``business_id`` was already printed and ``force_reprint`` is set, reprints the
    **same** business identifier (no new barcode/HU). Printer failures never mint IDs.
    """
    install_print_gateway_schema(db)
    lt = (label_type or "GENERIC").strip().upper()
    bid = (business_id or object_id or "").strip() or None

    previous = None
    if bid and force_reprint:
        previous = db.execute(
            text(
                """
                SELECT id::text, print_job_id::text, station_code, template_code, printer_code
                FROM print_gateway_jobs
                WHERE business_id = :b AND label_type = :lt
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {"b": bid, "lt": lt},
        ).mappings().first()

    route = resolve_route(
        db,
        label_type=lt,
        workstation_code=workstation_code,
        zone_code=zone_code,
        warehouse_id=warehouse_id,
        preferred_station=preferred_station or (previous["station_code"] if previous else None),
    )

    # On reprint without new payload, reuse bytes from prior PrintJob if available
    use_payload = payload
    use_name = file_name
    use_ctype = content_type
    if force_reprint and previous and previous.get("print_job_id") and (not payload or payload == b""):
        prev_job = db.execute(
            text("SELECT payload, file_name, content_type FROM print_jobs WHERE id = CAST(:id AS uuid)"),
            {"id": previous["print_job_id"]},
        ).mappings().first()
        if prev_job and prev_job.get("payload"):
            use_payload = bytes(prev_job["payload"])
            use_name = prev_job.get("file_name") or file_name
            use_ctype = prev_job.get("content_type") or content_type

    if not use_payload:
        raise ValueError("Пустой payload печати")

    job = queue_print_job(
        db,
        station_code=route["station_code"],
        payload=use_payload,
        file_name=use_name,
        document_type=lt,
        client_id=client_id,
        object_type=object_type or "gateway",
        object_id=bid or object_id,
        content_type=use_ctype,
    )

    gid = uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO print_gateway_jobs(
                id, print_job_id, business_id, label_type, template_code, printer_code,
                station_code, warehouse_id, workstation_code, zone_code, status,
                is_reprint, reprint_of, reason, actor
            ) VALUES (
                CAST(:id AS uuid), CAST(:pj AS uuid), :bid, :lt, :tpl, :pc,
                :st, CAST(:wh AS uuid), :ws, :zone, 'QUEUED',
                :rep, CAST(:rof AS uuid), :reason, :actor
            )
            """
        ),
        {
            "id": str(gid),
            "pj": str(job.id),
            "bid": bid,
            "lt": lt,
            "tpl": route.get("template_code"),
            "pc": route.get("printer_code"),
            "st": route["station_code"],
            "wh": warehouse_id,
            "ws": workstation_code,
            "zone": zone_code,
            "rep": bool(force_reprint),
            "rof": previous["id"] if previous else None,
            "reason": reason,
            "actor": actor,
        },
    )
    db.commit()
    return {
        "gateway_job_id": str(gid),
        "print_job_id": str(job.id),
        "business_id": bid,
        "station_code": route["station_code"],
        "printer_code": route.get("printer_code"),
        "template_code": route.get("template_code"),
        "label_type": lt,
        "is_reprint": bool(force_reprint),
        "matched_by": route.get("matched_by"),
        "status": "QUEUED",
        "message": "Print Gateway поставил задание в очередь Print Agent. Новый business id не создавался.",
    }


def sync_gateway_job_status(db: Session, print_job_id: str) -> None:
    install_print_gateway_schema(db)
    row = db.execute(
        text("SELECT status FROM print_jobs WHERE id = CAST(:id AS uuid)"),
        {"id": print_job_id},
    ).scalar()
    if not row:
        return
    mapped = {"QUEUED": "QUEUED", "CLAIMED": "PRINTING", "PRINTED": "SUCCESS", "FAILED": "ERROR"}.get(str(row), str(row))
    db.execute(
        text(
            """
            UPDATE print_gateway_jobs
            SET status = :st, updated_at = NOW()
            WHERE print_job_id = CAST(:id AS uuid)
            """
        ),
        {"st": mapped, "id": print_job_id},
    )
    db.commit()


def create_mobile_station(
    db: Session,
    *,
    station_code: str,
    printer_name: str | None = None,
    warehouse_id: str | None = None,
    printer_id: str | None = None,
) -> dict[str, Any]:
    install_print_gateway_schema(db)
    qr = f"PRINT-{secrets.token_hex(5).upper()}"
    mid = str(uuid.uuid4())
    code = station_code.strip().upper()
    # Ensure printer registry entry for mobile cart
    if not printer_id:
        existing = db.execute(
            text("SELECT id::text FROM print_gateway_printers WHERE station_code = :c LIMIT 1"),
            {"c": code},
        ).scalar()
        if existing:
            printer_id = existing
        else:
            printer_id = str(uuid.uuid4())
            db.execute(
                text(
                    """
                    INSERT INTO print_gateway_printers(
                        id, code, name, kind, station_code, warehouse_id, is_active
                    ) VALUES (
                        CAST(:id AS uuid), :code, :name, 'MOBILE', :st, CAST(:wh AS uuid), TRUE
                    )
                    """
                ),
                {
                    "id": printer_id,
                    "code": f"MOBILE-{code}",
                    "name": printer_name or f"Мобильный принтер {code}",
                    "st": code,
                    "wh": warehouse_id,
                },
            )
    db.execute(
        text(
            """
            INSERT INTO print_gateway_mobile_stations(
                id, printer_qr, printer_id, station_code, printer_name, warehouse_id
            ) VALUES (
                CAST(:id AS uuid), :qr, CAST(:pid AS uuid), :st, :name, CAST(:wh AS uuid)
            )
            """
        ),
        {
            "id": mid,
            "qr": qr,
            "pid": printer_id,
            "st": code,
            "name": printer_name,
            "wh": warehouse_id,
        },
    )
    db.commit()
    return {
        "id": mid,
        "printer_qr": qr,
        "qr_payload": f"ZIPLINE-PRINT:{qr}",
        "station_code": code,
        "printer_id": printer_id,
        "printer_name": printer_name,
    }


def bind_mobile_station(
    db: Session,
    *,
    printer_qr: str,
    device_id: str,
    employee_id: str | None = None,
    minutes: int = 120,
) -> dict[str, Any]:
    install_print_gateway_schema(db)
    raw = str(printer_qr or "").strip()
    if raw.upper().startswith("ZIPLINE-PRINT:"):
        raw = raw.split(":", 1)[1].strip()
    row = db.execute(
        text(
            """
            SELECT id::text, station_code, printer_name, printer_id::text
            FROM print_gateway_mobile_stations
            WHERE printer_qr = :qr
            """
        ),
        {"qr": raw},
    ).mappings().first()
    # Fallback to legacy zipline_mobile_print_links
    if not row:
        try:
            legacy = db.execute(
                text("SELECT id::text, station_code, printer_name FROM zipline_mobile_print_links WHERE printer_qr = :qr"),
                {"qr": raw},
            ).mappings().first()
        except Exception:
            legacy = None
        if legacy:
            until = _utcnow() + timedelta(minutes=minutes)
            db.execute(
                text(
                    """
                    UPDATE zipline_mobile_print_links
                    SET bound_device_id = :dev, bound_employee_id = CAST(:emp AS uuid), bound_until = :until
                    WHERE printer_qr = :qr
                    """
                ),
                {"dev": device_id, "emp": employee_id, "until": until, "qr": raw},
            )
            db.commit()
            return {
                "ok": True,
                "station_code": legacy["station_code"],
                "printer_name": legacy.get("printer_name"),
                "bound_until": until.isoformat(),
                "source": "legacy",
            }
        raise ValueError("Принтер по QR не найден")

    until = _utcnow() + timedelta(minutes=minutes)
    db.execute(
        text(
            """
            UPDATE print_gateway_mobile_stations
            SET bound_device_id = :dev, bound_employee_id = CAST(:emp AS uuid), bound_until = :until
            WHERE printer_qr = :qr
            """
        ),
        {"dev": device_id, "emp": employee_id, "until": until, "qr": raw},
    )
    db.commit()
    return {
        "ok": True,
        "station_code": row["station_code"],
        "printer_name": row.get("printer_name"),
        "printer_id": row.get("printer_id"),
        "bound_until": until.isoformat(),
        "source": "gateway",
    }


def resolve_device_station(db: Session, device_id: str | None) -> str | None:
    """Return station temporarily bound to a TSD/device via Mobile Print Station."""
    if not device_id:
        return None
    install_print_gateway_schema(db)
    row = db.execute(
        text(
            """
            SELECT station_code FROM print_gateway_mobile_stations
            WHERE bound_device_id = :d AND bound_until IS NOT NULL AND bound_until > NOW()
            ORDER BY bound_until DESC
            LIMIT 1
            """
        ),
        {"d": device_id},
    ).scalar()
    if row:
        return str(row)
    try:
        legacy = db.execute(
            text(
                """
                SELECT station_code FROM zipline_mobile_print_links
                WHERE bound_device_id = :d AND bound_until IS NOT NULL AND bound_until > NOW()
                ORDER BY bound_until DESC LIMIT 1
                """
            ),
            {"d": device_id},
        ).scalar()
        return str(legacy) if legacy else None
    except Exception:
        return None
