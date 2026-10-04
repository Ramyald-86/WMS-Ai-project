from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import re
import threading
import time
import uuid
from typing import Any, Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session


SYSTEM_LOCATION_CODES = frozenset({"БЕЗ-АДРЕСА", "ПРИЕМКА", "ПРИЕМКА-ПАЛЕТЫ", "UNPLACED"})
_STATION_RE = re.compile(r"[^A-Z0-9_-]+")
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()
_SCHEMA_READY = False
_SCHEMA_GUARD = threading.Lock()

# FC_K9R59_BACKGROUND_DB_PRESSURE_GUARD: non-critical background sweeps share one
# process-local heavy lane. Operator requests and safety-critical NEW-order reservation
# never wait for this lane; broad status/reconcile work simply yields and retries later.
_BACKGROUND_HEAVY_LANE = threading.BoundedSemaphore(1)
_BACKGROUND_STATE_LOCK = threading.Lock()
_BACKGROUND_STATE: dict[str, Any] = {
    "active_name": None,
    "active_since_epoch": None,
    "last_name": None,
    "last_duration_ms": 0,
    "acquired": 0,
    "skipped": 0,
}


@contextlib.contextmanager
def background_heavy_lane(name: str, *, blocking: bool = False, timeout: float = 0.0) -> Iterator[bool]:
    """Serialize broad background DB work without delaying warehouse hot paths.

    The lane is intentionally process-local because both built-in background workers run
    inside the same uvicorn process. A busy lane is not an error: non-critical work yields
    and is retried by its normal cadence.
    """
    if blocking:
        acquired = _BACKGROUND_HEAVY_LANE.acquire(timeout=max(0.0, float(timeout)))
    else:
        acquired = _BACKGROUND_HEAVY_LANE.acquire(blocking=False)
    if not acquired:
        with _BACKGROUND_STATE_LOCK:
            _BACKGROUND_STATE["skipped"] = int(_BACKGROUND_STATE.get("skipped") or 0) + 1
        yield False
        return
    started_mono = time.monotonic()
    with _BACKGROUND_STATE_LOCK:
        _BACKGROUND_STATE["active_name"] = str(name or "background")[:120]
        _BACKGROUND_STATE["active_since_epoch"] = int(time.time())
        _BACKGROUND_STATE["acquired"] = int(_BACKGROUND_STATE.get("acquired") or 0) + 1
    try:
        yield True
    finally:
        duration_ms = int(max(0.0, time.monotonic() - started_mono) * 1000)
        with _BACKGROUND_STATE_LOCK:
            _BACKGROUND_STATE["last_name"] = _BACKGROUND_STATE.get("active_name")
            _BACKGROUND_STATE["last_duration_ms"] = duration_ms
            _BACKGROUND_STATE["active_name"] = None
            _BACKGROUND_STATE["active_since_epoch"] = None
        _BACKGROUND_HEAVY_LANE.release()


def background_lane_status() -> dict[str, Any]:
    with _BACKGROUND_STATE_LOCK:
        return dict(_BACKGROUND_STATE)


def apply_local_db_timeouts(
    db: Session, *, lock_ms: int = 2500, statement_ms: int = 30000, idle_ms: int = 30000
) -> bool:
    """Bound one PostgreSQL background transaction; no global DB setting changes.

    SET LOCAL lasts only until commit/rollback. It prevents an optional maintenance/status
    pass from occupying a pooled connection indefinitely when another warehouse action
    holds a row/table lock.
    """
    bind = db.get_bind()
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
    if dialect != "postgresql":
        return False
    lock_ms = max(250, min(int(lock_ms), 60000))
    statement_ms = max(1000, min(int(statement_ms), 300000))
    idle_ms = max(1000, min(int(idle_ms), 300000))
    db.execute(text(f"SET LOCAL lock_timeout = '{lock_ms}ms'"))
    db.execute(text(f"SET LOCAL statement_timeout = '{statement_ms}ms'"))
    db.execute(text(f"SET LOCAL idle_in_transaction_session_timeout = '{idle_ms}ms'"))
    return True


def normalize_station_code(value: str | None, default: str = "PACK-01") -> str:
    code = _STATION_RE.sub("-", str(value or "").strip().upper()).strip("-_")
    return (code or default)[:64]


def normalize_device_id(value: str | None) -> str | None:
    raw = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip()).strip("-_")
    return raw[:64] or None


def _process_lock(key: str) -> threading.Lock:
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.Lock())


# FC_WB_ORDER_PROCESS_LOCK_09415G3_20260903
@contextlib.contextmanager
def process_lock(key: str) -> Iterator[None]:
    """Serialize short in-process critical sections without consuming a DB connection.

    FulfillCore runs one uvicorn process in the production compose file. The background
    WB worker and sync HTTP endpoints therefore share this lock. Callers must acquire it
    only after WB network I/O and release it immediately after commit/rollback.
    """
    lock = _process_lock(f"process:{key}")
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


@contextlib.contextmanager
def distributed_lock(db: Session, key: str, *, blocking: bool = True) -> Iterator[bool]:
    """Session-level PostgreSQL advisory lock with an in-process fallback.

    Session-level advisory locks survive commits, which is important around WB API calls.
    They are always released in ``finally`` before the SQLAlchemy connection returns to
    the pool. Non-PostgreSQL development environments keep the previous process-local
    behaviour rather than failing application startup.
    """
    bind = db.get_bind()
    dialect = str(getattr(getattr(bind, "dialect", None), "name", "") or "").lower()
    lock_key = f"fulfillcore:{key}"
    if dialect == "postgresql":
        # IMPORTANT: keep a dedicated physical connection for the lifetime of the
        # session-level advisory lock. A SQLAlchemy Session may return its normal
        # connection to the pool on commit(), so locking through ``db.execute`` and
        # unlocking after a commit can otherwise target two different PostgreSQL
        # sessions and leak the original lock into the pool.
        fn = "pg_advisory_lock" if blocking else "pg_try_advisory_lock"
        engine = getattr(bind, "engine", bind)
        with engine.connect() as lock_conn:
            acquired = lock_conn.execute(text(f"SELECT {fn}(hashtext(:key))"), {"key": lock_key}).scalar()
            acquired = True if blocking else bool(acquired)
            try:
                yield acquired
            finally:
                if acquired:
                    try:
                        lock_conn.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": lock_key})
                    except Exception:
                        pass
        return

    lock = _process_lock(lock_key)
    acquired = lock.acquire(blocking=blocking)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()


def install_runtime_schema(db: Session) -> None:
    """Install additive reliability tables once per process.

    No existing warehouse tables are dropped or rewritten. These statements are safe to
    run repeatedly and are intentionally kept compatible with the PostgreSQL deployment.
    The receiving patch tables are also created here so requests no longer have to run DDL
    on the warehouse hot path.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_GUARD:
        if _SCHEMA_READY:
            return
        statements = [
            """
            CREATE TABLE IF NOT EXISTS fc_runtime_state (
                state_key VARCHAR(255) PRIMARY KEY,
                value_text TEXT NULL,
                updated_epoch BIGINT NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS fc_workstation_profiles (
                station_code VARCHAR(64) PRIMARY KEY,
                client_id VARCHAR(36) NULL,
                warehouse_id VARCHAR(36) NULL,
                printer_profile TEXT NULL,
                scanner_profile TEXT NULL,
                updated_epoch BIGINT NOT NULL DEFAULT 0
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS fc_packing_leases (
                order_id VARCHAR(36) PRIMARY KEY,
                station_code VARCHAR(64) NOT NULL,
                device_id VARCHAR(64) NULL,
                credential_id VARCHAR(36) NOT NULL,
                warehouse_id VARCHAR(36) NULL,
                barcode VARCHAR(255) NULL,
                leased_until_epoch BIGINT NOT NULL,
                updated_epoch BIGINT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_fc_packing_leases_credential_barcode ON fc_packing_leases(credential_id, barcode)",
            "CREATE INDEX IF NOT EXISTS ix_fc_packing_leases_until ON fc_packing_leases(leased_until_epoch)",
            "ALTER TABLE fc_packing_leases ADD COLUMN IF NOT EXISTS device_id VARCHAR(64) NULL",
            """
            CREATE TABLE IF NOT EXISTS fc_wb_stock_publications (
                client_id VARCHAR(36) NOT NULL,
                warehouse_id VARCHAR(36) NOT NULL,
                barcode VARCHAR(255) NOT NULL,
                wb_warehouse_id BIGINT NULL,
                published_amount INTEGER NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
                last_error TEXT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (client_id, warehouse_id, barcode)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS fc_pallet_receipts (
                id VARCHAR(36) PRIMARY KEY,
                client_id VARCHAR(36) NOT NULL,
                warehouse_id VARCHAR(36) NOT NULL,
                name VARCHAR(255),
                reference VARCHAR(255),
                status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS fc_pallet_receipt_pallets (
                id VARCHAR(36) PRIMARY KEY,
                receipt_id VARCHAR(36) NOT NULL,
                pallet_code VARCHAR(255) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                closed_at TIMESTAMPTZ NULL,
                UNIQUE(receipt_id, pallet_code)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS fc_pallet_receipt_boxes (
                id VARCHAR(36) PRIMARY KEY,
                receipt_id VARCHAR(36) NOT NULL,
                pallet_code VARCHAR(255) NOT NULL,
                box_code VARCHAR(255) NOT NULL,
                container_id VARCHAR(36) NULL,
                container_created BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                UNIQUE(receipt_id, box_code)
            )
            """,
            "ALTER TABLE fc_pallet_receipt_pallets ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ NULL",
            "ALTER TABLE fc_pallet_receipt_boxes ADD COLUMN IF NOT EXISTS container_id VARCHAR(36) NULL",
            "ALTER TABLE fc_pallet_receipt_boxes ADD COLUMN IF NOT EXISTS container_created BOOLEAN NOT NULL DEFAULT FALSE",
            """
            CREATE TABLE IF NOT EXISTS fc_structured_receipts (
                id VARCHAR(36) PRIMARY KEY,
                client_id VARCHAR(36) NOT NULL,
                warehouse_id VARCHAR(36) NOT NULL,
                name VARCHAR(255) NULL,
                reference VARCHAR(255) NULL,
                source_filename VARCHAR(512) NULL,
                source_sheet VARCHAR(255) NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'READY',
                performed_by VARCHAR(255) NULL,
                section_count INTEGER NOT NULL DEFAULT 0,
                pallet_count INTEGER NOT NULL DEFAULT 0,
                box_count INTEGER NOT NULL DEFAULT 0,
                row_count INTEGER NOT NULL DEFAULT 0,
                product_row_count INTEGER NOT NULL DEFAULT 0,
                zero_product_row_count INTEGER NOT NULL DEFAULT 0,
                empty_box_count INTEGER NOT NULL DEFAULT 0,
                quantity_total NUMERIC(18,3) NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                warning_count INTEGER NOT NULL DEFAULT 0,
                committed_operation_id VARCHAR(36) NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                committed_at TIMESTAMPTZ NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_fc_structured_receipts_scope ON fc_structured_receipts(client_id, warehouse_id, created_at DESC)",
            """
            CREATE TABLE IF NOT EXISTS fc_structured_receipt_rows (
                id VARCHAR(36) PRIMARY KEY,
                receipt_id VARCHAR(36) NOT NULL,
                row_number INTEGER NOT NULL,
                section_code VARCHAR(128) NULL,
                pallet_code VARCHAR(128) NULL,
                pallet_limit INTEGER NULL,
                box_code VARCHAR(255) NULL,
                batch_name VARCHAR(255) NULL,
                source_name VARCHAR(255) NULL,
                accepted_at_text TEXT NULL,
                days_text VARCHAR(64) NULL,
                volume_m3_text VARCHAR(64) NULL,
                accrued_rub_text VARCHAR(64) NULL,
                article VARCHAR(255) NULL,
                size_name VARCHAR(255) NULL,
                barcode VARCHAR(255) NULL,
                quantity NUMERIC(18,3) NULL,
                is_empty_box BOOLEAN NOT NULL DEFAULT FALSE,
                parse_error_text TEXT NULL,
                error_text TEXT NULL,
                warning_text TEXT NULL,
                validation_status VARCHAR(32) NOT NULL DEFAULT 'VALID',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_fc_structured_rows_receipt ON fc_structured_receipt_rows(receipt_id, row_number)",
            "CREATE INDEX IF NOT EXISTS ix_fc_structured_rows_box ON fc_structured_receipt_rows(receipt_id, box_code)",
            "CREATE INDEX IF NOT EXISTS ix_fc_structured_rows_barcode ON fc_structured_receipt_rows(receipt_id, barcode)",
            # FC_K9R38_EXTERNAL_PICK_SCHEMA: uploaded FBS picking tasks are durable write-off
            # documents. r38 stores exact source buckets for the generated sheet/history;
            # new tasks use WRITTEN_OFF allocations and never create sellability holds.
            """
            CREATE TABLE IF NOT EXISTS fc_external_pick_batches (
                id VARCHAR(36) PRIMARY KEY,
                client_id VARCHAR(36) NOT NULL,
                warehouse_id VARCHAR(36) NOT NULL,
                wb_warehouse_id BIGINT NULL,
                task_number VARCHAR(255) NULL,
                source_filename VARCHAR(512) NULL,
                source_sha256 VARCHAR(64) NULL,
                performed_by VARCHAR(255) NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
                requested_units NUMERIC(18,3) NOT NULL DEFAULT 0,
                allocated_units NUMERIC(18,3) NOT NULL DEFAULT 0,
                shortage_units NUMERIC(18,3) NOT NULL DEFAULT 0,
                source_rows INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                cancelled_at TIMESTAMPTZ NULL,
                wb_sync_status VARCHAR(32) NULL,
                wb_sync_error TEXT NULL,
                wb_synced_at TIMESTAMPTZ NULL,
                failed_units NUMERIC(18,3) NOT NULL DEFAULT 0,
                positions_total INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "ALTER TABLE fc_external_pick_batches ADD COLUMN IF NOT EXISTS source_sha256 VARCHAR(64) NULL",
            "ALTER TABLE fc_external_pick_batches ADD COLUMN IF NOT EXISTS task_number VARCHAR(255) NULL",
            "ALTER TABLE fc_external_pick_batches ADD COLUMN IF NOT EXISTS wb_sync_status VARCHAR(32) NULL",
            "ALTER TABLE fc_external_pick_batches ADD COLUMN IF NOT EXISTS wb_sync_error TEXT NULL",
            "ALTER TABLE fc_external_pick_batches ADD COLUMN IF NOT EXISTS wb_synced_at TIMESTAMPTZ NULL",
            "ALTER TABLE fc_external_pick_batches ADD COLUMN IF NOT EXISTS failed_units NUMERIC(18,3) NOT NULL DEFAULT 0",
            "ALTER TABLE fc_external_pick_batches ADD COLUMN IF NOT EXISTS positions_total INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE fc_external_pick_batches ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "CREATE INDEX IF NOT EXISTS ix_fc_external_pick_batches_scope ON fc_external_pick_batches(client_id, warehouse_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_fc_external_pick_batches_hash ON fc_external_pick_batches(client_id, warehouse_id, source_sha256, status)",
            # FC_K9R38_PICK_TASK_NUMBER_IDEMPOTENCY: a warehouse task number is a durable
            # business id. Re-uploading the same task can never create a second write-off.
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_fc_external_pick_batches_task_number ON fc_external_pick_batches(client_id, warehouse_id, task_number) WHERE task_number IS NOT NULL",
            # FC_K9R37_EXTERNAL_PICK_DUPLICATE_DB_GUARD: the UI hash check is friendly,
            # while this partial unique index closes the simultaneous double-upload race.
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_fc_external_pick_batches_active_hash ON fc_external_pick_batches(client_id, warehouse_id, source_sha256) WHERE source_sha256 IS NOT NULL AND status='ACTIVE'",
            """
            CREATE TABLE IF NOT EXISTS fc_external_pick_requests (
                id VARCHAR(36) PRIMARY KEY,
                batch_id VARCHAR(36) NOT NULL,
                row_number INTEGER NOT NULL,
                barcode VARCHAR(255) NOT NULL,
                requested_quantity NUMERIC(18,3) NOT NULL,
                allocated_quantity NUMERIC(18,3) NOT NULL DEFAULT 0,
                shortage_quantity NUMERIC(18,3) NOT NULL DEFAULT 0,
                article VARCHAR(255) NULL,
                size_name VARCHAR(255) NULL,
                chrt_id BIGINT NULL,
                note TEXT NULL,
                failed_quantity NUMERIC(18,3) NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            "ALTER TABLE fc_external_pick_requests ADD COLUMN IF NOT EXISTS failed_quantity NUMERIC(18,3) NOT NULL DEFAULT 0",
            "CREATE INDEX IF NOT EXISTS ix_fc_external_pick_requests_batch ON fc_external_pick_requests(batch_id, row_number)",
            "CREATE INDEX IF NOT EXISTS ix_fc_external_pick_requests_barcode ON fc_external_pick_requests(batch_id, barcode)",
            """
            CREATE TABLE IF NOT EXISTS fc_external_pick_allocations (
                id VARCHAR(36) PRIMARY KEY,
                batch_id VARCHAR(36) NOT NULL,
                request_id VARCHAR(36) NOT NULL,
                client_id VARCHAR(36) NOT NULL,
                warehouse_id VARCHAR(36) NOT NULL,
                sku_id VARCHAR(36) NOT NULL,
                barcode VARCHAR(255) NOT NULL,
                location_id VARCHAR(36) NOT NULL,
                container_id VARCHAR(36) NULL,
                quantity NUMERIC(18,3) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                cancelled_at TIMESTAMPTZ NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_fc_external_pick_alloc_scope ON fc_external_pick_allocations(client_id, warehouse_id, sku_id, status)",
            "CREATE INDEX IF NOT EXISTS ix_fc_external_pick_alloc_bucket ON fc_external_pick_allocations(client_id, warehouse_id, sku_id, location_id, container_id, status)",
            "CREATE INDEX IF NOT EXISTS ix_fc_external_pick_alloc_batch ON fc_external_pick_allocations(batch_id, status)",
            """
            CREATE TABLE IF NOT EXISTS fc_wb_supply_registry (
                client_id VARCHAR(36) NOT NULL,
                cabinet_name VARCHAR(255) NOT NULL DEFAULT '',
                supply_id VARCHAR(64) NOT NULL,
                credential_id VARCHAR(36) NULL,
                physical_warehouse_id VARCHAR(36) NULL,
                wb_warehouse_id BIGINT NULL,
                supply_name VARCHAR(255) NULL,
                done BOOLEAN NULL DEFAULT FALSE,
                order_count INTEGER NULL DEFAULT 0,
                source VARCHAR(32) NULL DEFAULT 'WB',
                raw_json TEXT NULL,
                last_error TEXT NULL,
                created_at_text TEXT NULL,
                closed_at_text TEXT NULL,
                last_live_sync_epoch BIGINT NOT NULL DEFAULT 0,
                updated_epoch BIGINT NOT NULL DEFAULT 0,
                PRIMARY KEY (client_id, cabinet_name, supply_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_fc_wb_supply_registry_scope ON fc_wb_supply_registry(client_id, cabinet_name, physical_warehouse_id, done, updated_epoch DESC)",
            "CREATE INDEX IF NOT EXISTS ix_fc_wb_supply_registry_wb_wh ON fc_wb_supply_registry(client_id, cabinet_name, wb_warehouse_id, done)",
        ]
        try:
            for statement in statements:
                db.execute(text(statement))
            db.commit()
            _SCHEMA_READY = True
        except Exception:
            db.rollback()
            raise


def ensure_runtime_schema(db: Session) -> None:
    if not _SCHEMA_READY:
        install_runtime_schema(db)


def claim_runtime_interval(db: Session, key: str, interval_seconds: float, *, force: bool = False) -> bool:
    """Atomically claim a shared rate-limit slot across all API workers."""
    ensure_runtime_schema(db)
    now = int(time.time())
    state_key = f"interval:{key}"
    with distributed_lock(db, f"interval:{key}", blocking=True):
        row = db.execute(
            text("SELECT updated_epoch FROM fc_runtime_state WHERE state_key=:key"),
            {"key": state_key},
        ).first()
        last = int(row[0] or 0) if row else 0
        if not force and last and (now - last) < max(1, int(interval_seconds)):
            return False
        db.execute(
            text("""
                INSERT INTO fc_runtime_state(state_key, value_text, updated_epoch)
                VALUES (:key, NULL, :now)
                ON CONFLICT (state_key) DO UPDATE SET updated_epoch=EXCLUDED.updated_epoch
            """),
            {"key": state_key, "now": now},
        )
        db.commit()
        return True


def station_token(master_token: str, station_code: str) -> str:
    master = str(master_token or "").encode("utf-8")
    station = normalize_station_code(station_code).encode("utf-8")
    return hmac.new(master, b"fulfillcore-print-agent:v1:" + station, hashlib.sha256).hexdigest()


def verify_station_token(master_token: str, station_code: str, supplied: str) -> bool:
    if not master_token or not supplied:
        return False
    expected = station_token(master_token, station_code)
    return hmac.compare_digest(expected, str(supplied).strip())


def _loads_dict(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def get_workstation_profile(db: Session, station_code: str) -> dict[str, Any]:
    ensure_runtime_schema(db)
    station = normalize_station_code(station_code)
    row = db.execute(
        text("""
            SELECT station_code, client_id, warehouse_id, printer_profile, scanner_profile, updated_epoch
            FROM fc_workstation_profiles WHERE station_code=:station
        """),
        {"station": station},
    ).mappings().first()
    if not row:
        return {
            "station_code": station,
            "client_id": None,
            "warehouse_id": None,
            "printer_profile": {},
            "scanner_profile": {},
            "updated_epoch": 0,
        }
    return {
        "station_code": str(row["station_code"]),
        "client_id": row.get("client_id"),
        "warehouse_id": row.get("warehouse_id"),
        "printer_profile": _loads_dict(row.get("printer_profile")),
        "scanner_profile": _loads_dict(row.get("scanner_profile")),
        "updated_epoch": int(row.get("updated_epoch") or 0),
    }


def _save_workstation_profile_unlocked(
    db: Session,
    station_code: str,
    *,
    client_id: str | uuid.UUID | None = None,
    warehouse_id: str | uuid.UUID | None = None,
    printer_profile: dict[str, Any] | None = None,
    scanner_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    station = normalize_station_code(station_code)
    now = int(time.time())
    current = get_workstation_profile(db, station)
    printer = current["printer_profile"] if printer_profile is None else dict(printer_profile)
    scanner = current["scanner_profile"] if scanner_profile is None else dict(scanner_profile)
    cid = current.get("client_id") if client_id is None else str(client_id or "") or None
    wid = current.get("warehouse_id") if warehouse_id is None else str(warehouse_id or "") or None
    db.execute(
        text("""
            INSERT INTO fc_workstation_profiles(
                station_code, client_id, warehouse_id, printer_profile, scanner_profile, updated_epoch
            ) VALUES (:station, :client_id, :warehouse_id, :printer, :scanner, :updated)
            ON CONFLICT (station_code) DO UPDATE SET
                client_id=EXCLUDED.client_id,
                warehouse_id=EXCLUDED.warehouse_id,
                printer_profile=EXCLUDED.printer_profile,
                scanner_profile=EXCLUDED.scanner_profile,
                updated_epoch=EXCLUDED.updated_epoch
        """),
        {
            "station": station,
            "client_id": cid,
            "warehouse_id": wid,
            "printer": json.dumps(printer, ensure_ascii=False, separators=(",", ":")),
            "scanner": json.dumps(scanner, ensure_ascii=False, separators=(",", ":")),
            "updated": now,
        },
    )
    db.commit()
    return get_workstation_profile(db, station)


def save_workstation_profile(
    db: Session,
    station_code: str,
    *,
    client_id: str | uuid.UUID | None = None,
    warehouse_id: str | uuid.UUID | None = None,
    printer_profile: dict[str, Any] | None = None,
    scanner_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize full browser profile updates with agent capability updates."""
    ensure_runtime_schema(db)
    station = normalize_station_code(station_code)
    with distributed_lock(db, f"workstation-profile:{station}", blocking=True):
        return _save_workstation_profile_unlocked(
            db, station, client_id=client_id, warehouse_id=warehouse_id,
            printer_profile=printer_profile, scanner_profile=scanner_profile,
        )


def merge_workstation_printer_profile(
    db: Session, station_code: str, updates: dict[str, Any]
) -> dict[str, Any]:
    """Atomically merge Print Agent-owned capabilities without erasing browser geometry."""
    ensure_runtime_schema(db)
    station = normalize_station_code(station_code)
    with distributed_lock(db, f"workstation-profile:{station}", blocking=True):
        current = get_workstation_profile(db, station)
        printer = dict(current.get("printer_profile") or {})
        printer.update(dict(updates or {}))
        return _save_workstation_profile_unlocked(db, station, printer_profile=printer)


def acquire_packing_lease(
    db: Session,
    *,
    order_id: str | uuid.UUID,
    station_code: str | None,
    device_id: str | None = None,
    credential_id: str | uuid.UUID,
    warehouse_id: str | uuid.UUID | None,
    barcode: str,
    ttl_seconds: int = 240,
) -> bool:
    """Reserve one physical order for one packing station.

    Older clients that do not send a station code remain backward compatible: they do
    not participate in leases. New FulfillCore UI always sends its PACK station.
    """
    if not station_code:
        return True
    ensure_runtime_schema(db)
    station = normalize_station_code(station_code)
    device = normalize_device_id(device_id)
    oid = str(order_id)
    now = int(time.time())
    until = now + max(60, min(int(ttl_seconds), 900))
    with distributed_lock(db, f"packing-order:{oid}", blocking=True):
        db.execute(text("DELETE FROM fc_packing_leases WHERE order_id=:order_id AND leased_until_epoch < :now"), {"order_id": oid, "now": now})
        row = db.execute(
            text("SELECT station_code, device_id, leased_until_epoch FROM fc_packing_leases WHERE order_id=:order_id"),
            {"order_id": oid},
        ).first()
        if row:
            same_station = str(row[0]) == station
            row_device = normalize_device_id(row[1])
            same_device = (not row_device or not device or row_device == device)
            if (not same_station or not same_device) and int(row[2] or 0) >= now:
                db.commit()
                return False
        db.execute(
            text("""
                INSERT INTO fc_packing_leases(
                    order_id, station_code, device_id, credential_id, warehouse_id, barcode,
                    leased_until_epoch, updated_epoch
                ) VALUES (:order_id,:station,:device_id,:credential_id,:warehouse_id,:barcode,:until,:now)
                ON CONFLICT (order_id) DO UPDATE SET
                    station_code=EXCLUDED.station_code,
                    device_id=EXCLUDED.device_id,
                    credential_id=EXCLUDED.credential_id,
                    warehouse_id=EXCLUDED.warehouse_id,
                    barcode=EXCLUDED.barcode,
                    leased_until_epoch=EXCLUDED.leased_until_epoch,
                    updated_epoch=EXCLUDED.updated_epoch
            """),
            {
                "order_id": oid,
                "station": station,
                "device_id": device,
                "credential_id": str(credential_id),
                "warehouse_id": str(warehouse_id) if warehouse_id else None,
                "barcode": str(barcode or "")[:255],
                "until": until,
                "now": now,
            },
        )
        db.commit()
        return True


def assert_or_renew_packing_lease(
    db: Session,
    *,
    order_id: str | uuid.UUID,
    station_code: str | None,
    device_id: str | None = None,
    ttl_seconds: int = 240,
) -> bool:
    if not station_code:
        return True
    ensure_runtime_schema(db)
    station = normalize_station_code(station_code)
    device = normalize_device_id(device_id)
    now = int(time.time())
    until = now + max(60, min(int(ttl_seconds), 900))
    oid = str(order_id)
    with distributed_lock(db, f"packing-order:{oid}", blocking=True):
        row = db.execute(
            text("SELECT station_code, device_id, leased_until_epoch FROM fc_packing_leases WHERE order_id=:order_id"),
            {"order_id": oid},
        ).first()
        # No row can exist for orders that were opened before 0.7.9.0 or by a legacy
        # browser. Keep that path compatible; every new UI scan creates a row first.
        if not row:
            return True
        same_station = str(row[0]) == station
        row_device = normalize_device_id(row[1])
        same_device = (not row_device or not device or row_device == device)
        if (not same_station or not same_device) and int(row[2] or 0) >= now:
            return False
        db.execute(
            text("UPDATE fc_packing_leases SET station_code=:station, device_id=:device_id, leased_until_epoch=:until, updated_epoch=:now WHERE order_id=:order_id"),
            {"station": station, "device_id": device, "until": until, "now": now, "order_id": oid},
        )
        db.commit()
        return True


def release_packing_lease(
    db: Session, order_id: str | uuid.UUID, station_code: str | None = None, device_id: str | None = None
) -> None:
    ensure_runtime_schema(db)
    params: dict[str, Any] = {"order_id": str(order_id)}
    sql = "DELETE FROM fc_packing_leases WHERE order_id=:order_id"
    if station_code:
        sql += " AND station_code=:station"
        params["station"] = normalize_station_code(station_code)
    device = normalize_device_id(device_id)
    if device:
        # Legacy rows without device_id may still be released by their matching station.
        sql += " AND (device_id=:device_id OR device_id IS NULL)"
        params["device_id"] = device
    db.execute(text(sql), params)
