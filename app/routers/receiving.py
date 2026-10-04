import io
import json
import hashlib
import re
import time
import uuid
import threading
from contextlib import ExitStack
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from decimal import Decimal
from typing import Any, Literal

from fastapi import Request, APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response, StreamingResponse
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from pydantic import BaseModel, Field
from sqlalchemy import and_, bindparam, func, or_, select, text
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_secret
from app.core.database import get_db
from app.models import (
    Container,
    InventoryBalance,
    InventoryOperation,
    InventoryOperationLine,
    Location,
    MarketplaceCredential,
    MarketplaceProduct,
    SKU,
)
from app.schemas.inventory import OperationCreate, StockLine
from app.services.inventory import execute_operation, move_entire_container
from app.services.runtime_reliability import distributed_lock, ensure_runtime_schema
from app.services.marketplaces import WBClient
from app.services.product_identity import normalize_product_barcode, resolved_sku_identity, wb_product_identity, wb_product_identity_map
from app.services.printing import queue_print_job
from app.services.stock_core_v2 import (
    active_order_floor_for_identity,
    canonical_stock_identity_lock_key,
    canonical_stock_identity_sku_ids,
    effective_fbs_free_for_sku,
    enqueue_wb_stock_for_sku,
    stock_truth_for_skus,
    reconcile_stock_bucket,
    move_bucket,
    set_wb_sales_paused,
    wb_sales_paused,
)
from app.services.stock_reservations import active_reserved_totals, sale_reserved_total
from app.services.container_lifecycle import archive_consumed_empty_containers
from app.services.stock_actions import release_stale_fbs_holds
from app.services.structured_receiving import (
    STRUCTURED_HEADERS,
    natural_key as structured_natural_key,
    parse_structured_workbook,
    structured_location_barcode,
    structured_location_code,
    structured_location_name,
    structured_section_code,
    structured_section_name,
    structured_rows_summary,
)

router = APIRouter(prefix="/receiving", tags=["Приёмка"])
_FBS_TRACKING_READY = False

# FC_K9R39_EXTERNAL_PICK_PROGRESS: progress is intentionally process-local and
# informational only. The write-off transaction remains the sole stock truth.
# r39 runs the heavy import as a synchronous FastAPI endpoint (thread-pool), so
# the event loop stays responsive and this progress endpoint can be polled while
# WB catalogue/order/stock requests are in flight.
_EXTERNAL_PICK_PROGRESS_LOCK = threading.Lock()
_EXTERNAL_PICK_PROGRESS: dict[str, dict] = {}

def _external_pick_progress_id(value: str | None) -> str:
    value = str(value or '').strip()
    return value if re.fullmatch(r'[A-Za-z0-9._:-]{8,80}', value) else ''

def _external_pick_progress_set(progress_id: str | None, *, stage: str, percent: int, message: str, **extra) -> None:
    pid = _external_pick_progress_id(progress_id)
    if not pid:
        return
    now = time.time()
    with _EXTERNAL_PICK_PROGRESS_LOCK:
        row = dict(_EXTERNAL_PICK_PROGRESS.get(pid) or {})
        row.setdefault('started_at_epoch', now)
        row.update({
            'progress_id': pid,
            'stage': str(stage),
            'percent': max(0, min(100, int(percent))),
            'message': str(message),
            'updated_at_epoch': now,
        })
        row.update(extra)
        _EXTERNAL_PICK_PROGRESS[pid] = row
        # Keep this tiny diagnostic cache bounded even after many daily uploads.
        if len(_EXTERNAL_PICK_PROGRESS) > 200:
            cutoff = now - 6 * 3600
            for key, item in list(_EXTERNAL_PICK_PROGRESS.items()):
                if float(item.get('updated_at_epoch') or 0) < cutoff:
                    _EXTERNAL_PICK_PROGRESS.pop(key, None)

def _external_pick_progress_get(progress_id: str) -> dict | None:
    pid = _external_pick_progress_id(progress_id)
    if not pid:
        return None
    with _EXTERNAL_PICK_PROGRESS_LOCK:
        row = dict(_EXTERNAL_PICK_PROGRESS.get(pid) or {})
    if not row:
        return None
    row['elapsed_seconds'] = max(0, int(time.time() - float(row.get('started_at_epoch') or time.time())))
    return row


# FC_K9R46_WB_BULK_PROGRESS: operator-visible progress for large inventory->WB jobs.
_INVENTORY_WB_PROGRESS_LOCK = threading.Lock()
_INVENTORY_WB_PROGRESS: dict[str, dict] = {}

def _inventory_wb_progress_id(value: str | None) -> str:
    value = str(value or '').strip()
    return value if re.fullmatch(r'[A-Za-z0-9._:-]{8,80}', value) else ''

def _inventory_wb_progress_set(progress_id: str | None, *, stage: str, percent: int, message: str, **extra) -> None:
    pid = _inventory_wb_progress_id(progress_id)
    if not pid:
        return
    now = time.time()
    with _INVENTORY_WB_PROGRESS_LOCK:
        row = dict(_INVENTORY_WB_PROGRESS.get(pid) or {})
        row.setdefault('started_at_epoch', now)
        row.update({
            'progress_id': pid, 'stage': str(stage),
            'percent': max(0, min(100, int(percent))),
            'message': str(message), 'updated_at_epoch': now,
        })
        row.update(extra)
        _INVENTORY_WB_PROGRESS[pid] = row
        if len(_INVENTORY_WB_PROGRESS) > 300:
            cutoff = now - 6 * 3600
            for key, item in list(_INVENTORY_WB_PROGRESS.items()):
                if float(item.get('updated_at_epoch') or 0) < cutoff:
                    _INVENTORY_WB_PROGRESS.pop(key, None)

def _inventory_wb_progress_get(progress_id: str) -> dict | None:
    pid = _inventory_wb_progress_id(progress_id)
    if not pid:
        return None
    with _INVENTORY_WB_PROGRESS_LOCK:
        row = dict(_INVENTORY_WB_PROGRESS.get(pid) or {})
    if not row:
        return None
    row['elapsed_seconds'] = max(0, int(time.time() - float(row.get('started_at_epoch') or time.time())))
    return row

# FC_K9R46_WB_SIZE_RUNTIME_CACHE: a fresh full size/chrt row learned from WB is reused
# by the worker and subsequent live-stock reads. This is only an acceleration cache;
# MarketplaceProduct + live WB remain the source of truth and correctness never depends on it.
_WB_SIZE_RUNTIME_CACHE_LOCK = threading.Lock()
_WB_SIZE_RUNTIME_CACHE: dict[tuple[str, str], dict[str, Any]] = {}

def _wb_runtime_size_cache_put(client_id: uuid.UUID | str, rows: list[dict] | None) -> int:
    cid = str(client_id)
    now = time.time()
    stored = 0
    with _WB_SIZE_RUNTIME_CACHE_LOCK:
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            raw = row.get('chrtID') or row.get('chrtId') or row.get('chrt_id')
            try:
                chrt_id = int(raw)
            except (TypeError, ValueError):
                continue
            siblings = sorted({normalize_product_barcode(str(x))[0] for x in (row.get('skus') or []) if str(x or '').strip()})
            siblings = [x for x in siblings if x]
            if not siblings:
                continue
            cached = {'chrtID': chrt_id, 'skus': siblings, '_cached_at_epoch': now}
            for code in siblings:
                _WB_SIZE_RUNTIME_CACHE[(cid, code)] = cached
                stored += 1
        if len(_WB_SIZE_RUNTIME_CACHE) > 100000:
            cutoff = now - 12 * 3600
            for key, item in list(_WB_SIZE_RUNTIME_CACHE.items()):
                if float(item.get('_cached_at_epoch') or 0) < cutoff:
                    _WB_SIZE_RUNTIME_CACHE.pop(key, None)
    return stored

def _wb_runtime_size_cache_rows(client_id: uuid.UUID | str, barcodes: list[str] | set[str]) -> list[dict]:
    cid = str(client_id)
    wanted = {normalize_product_barcode(str(x))[0] for x in barcodes if str(x or '').strip()}
    by_chrt: dict[int, dict] = {}
    with _WB_SIZE_RUNTIME_CACHE_LOCK:
        for code in wanted:
            row = _WB_SIZE_RUNTIME_CACHE.get((cid, code))
            if not row:
                continue
            chrt_id = int(row['chrtID'])
            by_chrt[chrt_id] = {'chrtID': chrt_id, 'skus': list(row.get('skus') or [])}
    return list(by_chrt.values())


class ManualReceiveRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    barcode: str
    quantity: Decimal = Field(gt=0)
    location_code: str
    container_code: str | None = None
    mode: Literal["ADD", "SET"] = "ADD"
    performed_by: str | None = None
    reference: str | None = None
    wb_warehouse_id: int | None = None
    stock_class: Literal["FBS", "MAIN"] = "FBS"


class ManualReceiveBatchLine(BaseModel):
    barcode: str = Field(min_length=1, max_length=255)
    quantity: int = Field(gt=0, le=1_000_000)
    location_code: str = Field(min_length=1, max_length=255)
    container_code: str | None = Field(default=None, max_length=255)


class ManualReceiveBatchRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    idempotency_key: str = Field(min_length=1, max_length=128)
    lines: list[ManualReceiveBatchLine] = Field(min_length=1, max_length=1000)
    performed_by: str | None = Field(default=None, max_length=255)
    reference: str | None = Field(default=None, max_length=255)
    wb_warehouse_id: int | None = None
    internal_only: bool = False
    stock_class: Literal["FBS", "MAIN"] = "FBS"


class ManualUnmatchedExportLine(BaseModel):
    barcode: str = Field(min_length=1, max_length=255)
    quantity: int = Field(gt=0, le=1_000_000)
    article: str | None = Field(default=None, max_length=255)
    size: str | None = Field(default=None, max_length=255)
    name: str | None = Field(default=None, max_length=512)
    location_code: str | None = Field(default=None, max_length=255)
    container_code: str | None = Field(default=None, max_length=255)
    storage_display: str | None = Field(default=None, max_length=512)


class ManualUnmatchedExportRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    reference: str | None = Field(default=None, max_length=255)
    lines: list[ManualUnmatchedExportLine] = Field(min_length=1, max_length=1000)


class ManualStorageBoxEnsureRequest(BaseModel):
    row_on_pallet: int | None = Field(default=None, ge=1, le=999)
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    section: str | None = Field(default=None, max_length=80)
    aisle: str | None = Field(default=None, max_length=80)
    pallet: str | None = Field(default=None, max_length=80)
    box_value: str = Field(min_length=1, max_length=255)
    box_mode: Literal["SCAN", "NAME"] = "SCAN"
    force_move: bool = False


class ManualBoxLabelQueueRequest(BaseModel):
    request_id: uuid.UUID | None = None
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    print_station: str = Field(min_length=1, max_length=64)
    width_mm: float = Field(default=58, ge=25, le=120)
    height_mm: float = Field(default=40, ge=25, le=120)


class ManualStorageBoxBulkCreateRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    first_box: str = Field(min_length=1, max_length=120)
    count: int = Field(ge=1, le=1000)


class ManualBoxLabelsRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    container_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    width_mm: float = Field(default=58, ge=25, le=120)
    height_mm: float = Field(default=40, ge=25, le=120)
    show_date: bool = False
    label_date: str | None = Field(default=None, max_length=10)


class ManualBoxLabelsQueueRequest(ManualBoxLabelsRequest):
    print_station: str = Field(min_length=1, max_length=64)


class ManualBoxesBulkMoveRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    destination_location_id: uuid.UUID
    container_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    request_id: uuid.UUID
    performed_by: str | None = Field(default=None, max_length=255)


class ManualLooseBoxLabelsRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    first_box: str = Field(min_length=1, max_length=120)
    count: int = Field(ge=1, le=1000)
    width_mm: float = Field(default=58, ge=25, le=120)
    height_mm: float = Field(default=40, ge=25, le=120)
    show_date: bool = False
    label_date: str | None = Field(default=None, max_length=10)


class ManualLooseBoxLabelsQueueRequest(ManualLooseBoxLabelsRequest):
    print_station: str = Field(min_length=1, max_length=64)


class PendingWbLine(BaseModel):
    barcode: str
    quantity: int = Field(ge=0)
    mode: Literal["ADD", "SET"] = "ADD"
    idempotency_key: str | None = Field(default=None, max_length=255)


class SyncReceivingToWbRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    wb_warehouse_id: int | None = None
    lines: list[PendingWbLine]


class PublishWbStockRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    barcode: str = Field(min_length=1, max_length=255)
    wb_warehouse_id: int | None = None


class InventoryAdjustAndPublishRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    barcode: str = Field(min_length=1, max_length=255)
    location_id: uuid.UUID
    container_id: uuid.UUID | None = None
    delta: Decimal
    performed_by: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=500)
    wb_warehouse_id: int | None = None


class InventoryBulkRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    action: Literal["DELETE_LOCAL", "PUBLISH_WB", "ADD_TO_WB_FBS", "PAUSE_WB", "RESUME_WB", "DELETE_FREE_AND_PAUSE", "DELETE_FBS_KEEP_RESERVES", "MOVE_FBS_TO_MAIN_AND_PAUSE"]
    barcodes: list[str] = Field(default_factory=list, max_length=10000)
    all_inventory: bool = False
    confirm_text: str | None = Field(default=None, max_length=64)
    wb_warehouse_id: int | None = None
    performed_by: str | None = Field(default=None, max_length=255)
    progress_id: str | None = Field(default=None, max_length=80)


class FbsRemovalPlanRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    barcodes: list[str] = Field(default_factory=list, max_length=10000)
    all_inventory: bool = False


class InventoryBulkUndoRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    wb_warehouse_id: int | None = None
    performed_by: str | None = Field(default=None, max_length=255)


class ExternalPickCancelRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    wb_warehouse_id: int | None = None
    performed_by: str | None = Field(default=None, max_length=255)


class InventoryControlStatusRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    barcodes: list[str] = Field(default_factory=list, min_length=1, max_length=10000)


class WbOversellGuardRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    wb_warehouse_id: int | None = None
    observed: dict[str, int] = Field(default_factory=dict)


class WbLiveStocksRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    barcodes: list[str] = Field(default_factory=list, min_length=1, max_length=10000)
    wb_warehouse_id: int | None = None


class CancelReceivingRequest(BaseModel):
    sync_wb: bool = False
    wb_warehouse_id: int | None = None


def _wb_pending_key(scope: str, barcode: str) -> str:
    clean_scope = re.sub(r"[^0-9A-Za-z_.:-]+", "-", str(scope or "receiving"))[:80]
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"fulfillcore-wb-publish:{clean_scope}:{barcode}").hex
    return f"{clean_scope}:{digest}"[:255]


def _active_wb_credential(db: Session, client_id: uuid.UUID) -> MarketplaceCredential | None:
    return db.execute(
        select(MarketplaceCredential)
        .where(
            MarketplaceCredential.client_id == client_id,
            MarketplaceCredential.provider == "WB",
            MarketplaceCredential.is_active.is_(True),
        )
        .order_by(MarketplaceCredential.updated_at.desc())
    ).scalars().first()


def _work_wb_warehouse_id(
    cred: MarketplaceCredential,
    requested: int | None,
    physical_warehouse_id: uuid.UUID | str | None = None,
) -> int | None:
    if requested:
        return int(requested)
    raw = cred.warehouse_settings or {}
    settings = raw
    if physical_warehouse_id is not None and isinstance(raw, dict):
        scopes = raw.get("__fc_physical_warehouses__")
        settings = (scopes or {}).get(str(physical_warehouse_id), {}) if isinstance(scopes, dict) else {}
    for key, value in settings.items():
        if str(key).startswith("__"):
            continue
        mode = str((value or {}).get("mode") or value or "").upper()
        if mode == "WORK":
            try:
                return int(key)
            except (TypeError, ValueError):
                continue
    return None




def _wb_cached_seller_warehouse(
    cred: MarketplaceCredential,
    warehouse_id: int,
    physical_warehouse_id: uuid.UUID | str | None,
) -> dict | None:
    """Use the saved physical->seller warehouse mapping without WB API I/O."""
    raw = cred.warehouse_settings or {}
    settings = raw if isinstance(raw, dict) else {}
    if physical_warehouse_id is not None and isinstance(settings, dict):
        scopes = settings.get("__fc_physical_warehouses__")
        if isinstance(scopes, dict):
            scoped = scopes.get(str(physical_warehouse_id))
            if isinstance(scoped, dict):
                settings = scoped
    row = settings.get(str(int(warehouse_id))) if isinstance(settings, dict) else None
    return dict(row) if isinstance(row, dict) else None


def _wb_validate_seller_warehouse(
    client: WBClient,
    warehouse_id: int,
    *,
    cred: MarketplaceCredential | None = None,
    physical_warehouse_id: uuid.UUID | str | None = None,
) -> dict:
    """Prefer local saved warehouse truth; live GET is a compatibility fallback only."""
    if cred is not None:
        cached = _wb_cached_seller_warehouse(cred, warehouse_id, physical_warehouse_id)
        if cached is not None:
            mode = str(cached.get("mode") or "HIDDEN").upper()
            if mode != "WORK":
                raise RuntimeError(f"Склад WB {warehouse_id} не назначен как «Работа» для этого физического склада")
            return {"id": int(warehouse_id), **cached, "source": "local_cache"}
    try:
        response = client._get("/api/v3/warehouses")
        data = response.json()
        if isinstance(data, list):
            rows = data
        elif isinstance(data, dict):
            rows = data.get("warehouses") or data.get("result") or data.get("data") or []
        else:
            rows = []
    except Exception as exc:
        raise RuntimeError(
            f"Сохранённая привязка склада WB {warehouse_id} не найдена и WB не удалось проверить. "
            "Откройте настройки складов WB и обновите список."
        ) from exc
    for row in rows or []:
        raw_id = row.get("id") or row.get("warehouseId")
        try:
            seller_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if seller_id == int(warehouse_id):
            return row
    raise RuntimeError(
        f"warehouseId={warehouse_id} не найден среди складов продавца WB. "
        "Обновите сохранённый список складов WB и назначьте нужный склад в режим «Работа»."
    )


def _wb_fresh_chrt_id(client: WBClient, barcode: str) -> int:
    code = normalize_product_barcode(barcode)[0]
    value = _wb_fresh_chrt_map(client, {code}).get(code)
    if value is None:
        raise RuntimeError(f"Barcode {code} was not found in the current WB catalogue")
    return int(value)


def _wb_fresh_size_rows(client: WBClient, barcodes: list[str] | set[str]) -> list[dict]:
    """Resolve complete current WB size rows for selected seller barcodes.

    The important part is returning every sibling SKU of the matching chrtId, not merely
    code->chrt. FulfillCore needs that complete size identity to sum local stock correctly.
    """
    wanted = {normalize_product_barcode(code)[0] for code in barcodes if str(code or "").strip()}
    wanted.discard("")
    if not wanted:
        return []

    by_chrt: dict[int, set[str]] = {}

    def collect(cards):
        for card in cards or []:
            for size in card.get("sizes") or []:
                if not isinstance(size, dict):
                    continue
                raw_chrt = size.get("chrtID") or size.get("chrtId") or size.get("chrt_id")
                try:
                    chrt_id = int(raw_chrt)
                except (TypeError, ValueError):
                    continue
                row_codes = {
                    normalize_product_barcode(str(raw_code))[0]
                    for raw_code in (size.get("skus") or [])
                    if str(raw_code or "").strip()
                }
                row_codes.discard("")
                if not (wanted & row_codes):
                    continue
                by_chrt.setdefault(chrt_id, set()).update(row_codes)

    if len(wanted) <= 8:
        resolved: set[str] = set()
        for code in sorted(wanted):
            collect(client.content_cards(text_search=code, limit_total=100))
            resolved = {c for values in by_chrt.values() for c in values}
            if wanted <= resolved:
                break
    else:
        collect(client.content_cards(limit_total=20000))

    return [
        {"chrtID": int(chrt_id), "skus": sorted(codes)}
        for chrt_id, codes in sorted(by_chrt.items())
    ]


def _wb_fresh_chrt_map(client: WBClient, barcodes: list[str] | set[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in _wb_fresh_size_rows(client, barcodes):
        chrt_id = int(row["chrtID"])
        for code in row.get("skus") or []:
            result[str(code)] = chrt_id
    wanted = {normalize_product_barcode(code)[0] for code in barcodes if str(code or "").strip()}
    return {code: result[code] for code in wanted if code in result}


def _wb_chrt_map(
    db: Session,
    *,
    client_id: uuid.UUID,
    barcodes: list[str],
) -> dict[str, int]:
    """Resolve seller barcodes to chrtId, preferring live rows learned this runtime.

    The runtime cache prevents a large bulk confirmation from rescanning the full WB
    catalogue immediately after the worker has already resolved the same sizes live.
    """
    wanted = {normalize_product_barcode(code)[0] for code in barcodes if str(code or '').strip()}
    wanted.discard('')
    if not wanted:
        return {}
    result: dict[str, int] = {}
    runtime_rows = _wb_runtime_size_cache_rows(client_id, wanted)
    for row in runtime_rows:
        chrt_id = int(row['chrtID'])
        for raw_code in row.get('skus') or []:
            code = normalize_product_barcode(str(raw_code))[0]
            if code in wanted:
                result[code] = chrt_id
    missing = wanted - set(result)
    if not missing:
        return result
    size_rows = db.execute(
        select(MarketplaceProduct.sizes).where(
            MarketplaceProduct.client_id == client_id,
            MarketplaceProduct.provider == "WB",
        )
    ).scalars().all()
    for sizes in size_rows:
        for row in sizes or []:
            if not isinstance(row, dict):
                continue
            raw_chrt = row.get("chrtID") or row.get("chrtId") or row.get("chrt_id")
            try:
                chrt_id = int(raw_chrt)
            except (TypeError, ValueError):
                continue
            row_codes = {normalize_product_barcode(str(x))[0] for x in (row.get('skus') or []) if str(x or '').strip()}
            if not (missing & row_codes):
                continue
            _wb_runtime_size_cache_put(client_id, [{'chrtID': chrt_id, 'skus': sorted(row_codes)}])
            for code in row_codes:
                if code in missing:
                    result[code] = chrt_id
    return result


def _wb_chrt_id(db: Session, *, client_id: uuid.UUID, barcode: str) -> int:
    code = normalize_product_barcode(barcode)[0]
    identity = wb_product_identity(db, client_id, code)
    raw = (identity or {}).get("chrt_id")
    if raw is None:
        # Fallback to a catalogue-wide resolver in case an older cached product row has
        # the barcode only inside sizes[] and not in the flattened barcodes projection.
        raw = _wb_chrt_map(db, client_id=client_id, barcodes=[code]).get(code)
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise RuntimeError(
            f"WB-карточка найдена по ШК {code}, но у размера нет chrtId. "
            "Обновите товары WB в FulfillCore и повторите отправку остатка."
        )


def _wb_read_chrt_stocks(client: WBClient, warehouse_id: int, chrt_ids: list[int]) -> dict[int, int]:
    result = {}
    for start in range(0, len(chrt_ids), 1000):
        chunk = [int(x) for x in chrt_ids[start:start + 1000]]
        data = client._post_read(f"/api/v3/stocks/{warehouse_id}", {"chrtIds": chunk}).json()
        rows = data.get("stocks") if isinstance(data, dict) else data
        if not isinstance(rows,list):raise RuntimeError('WB вернул некорректный ответ об остатках')
        for row in rows:
            if not isinstance(row,dict):raise RuntimeError('WB вернул некорректную строку остатка')
            raw = row.get("chrtId") or row.get("chrtID") or row.get("chrt_id")
            try:
                chrt_id = int(raw)
            except (TypeError, ValueError):
                continue
            if chrt_id in chunk:
                try:
                    result[chrt_id] = max(0, int(row.get("amount") or 0))
                except (TypeError, ValueError):
                    raise RuntimeError('WB вернул некорректное количество')
        missing=set(chunk)-set(result)
        if missing:raise RuntimeError('WB не вернул остаток для chrtId: '+', '.join(map(str,sorted(missing)[:12])))
    return result


def _wb_write_chrt_stocks(client: WBClient, warehouse_id: int, amounts: dict[int, int]) -> None:
    items = [{"chrtId": int(chrt_id), "amount": max(0, int(amount))} for chrt_id, amount in amounts.items()]
    for start in range(0, len(items), 1000):
        client._put(f"/api/v3/stocks/{warehouse_id}", {"stocks": items[start:start + 1000]})


def _wb_write_and_verify_chrt_stocks(
    client: WBClient,
    warehouse_id: int,
    amounts: dict[int, int],
    *,
    attempts: int = 3,
) -> dict[int, int]:
    """Publish absolute FBS amounts with read-after-write reconciliation.

    A transport timeout during PUT is ambiguous: WB may already have applied the write.
    Therefore FulfillCore never blindly repeats it. It first reads the current WB values,
    then retries only the still-mismatched subset. The same verification also protects us
    from a nominally successful response that did not actually change stock.
    """
    wanted = {int(cid): max(0, int(qty)) for cid, qty in amounts.items()}
    if not wanted:
        return {}
    try:
        _wb_write_chrt_stocks(client, warehouse_id, wanted)
    except HTTPException as exc:
        # 502/504 around a write is an unknown outcome. Reconcile below before any retry.
        if int(getattr(exc, "status_code", 0) or 0) not in {502, 503, 504}:
            raise

    observed: dict[int, int] = {}
    for attempt in range(max(1, attempts)):
        if attempt:
            time.sleep(0.30 * attempt)
        observed = _wb_read_chrt_stocks(client, warehouse_id, list(wanted))
        mismatched = {cid: qty for cid, qty in wanted.items() if int(observed.get(cid, 0)) != int(qty)}
        if not mismatched:
            return observed
        if attempt + 1 >= attempts:
            break
        try:
            _wb_write_chrt_stocks(client, warehouse_id, mismatched)
        except HTTPException as exc:
            if int(getattr(exc, "status_code", 0) or 0) not in {502, 503, 504}:
                raise
            # Unknown outcome again: the next loop starts with a read, never a blind PUT.
            continue
    details = ", ".join(
        f"chrtId {cid}: отправлено {qty}, WB вернул {observed.get(cid, 0)}"
        for cid, qty in list(wanted.items())[:12]
        if int(observed.get(cid, 0)) != int(qty)
    )
    raise RuntimeError(f"WB не подтвердил остаток после записи: {details or 'значения не совпали'}")


def _sync_wb_stock(
    db: Session,
    *,
    client_id: uuid.UUID,
    physical_warehouse_id: uuid.UUID,
    wb_warehouse_id: int | None,
    barcode: str,
    amount: int,
) -> int:
    # FC_G5A8K4_WB_HARD_SELLABLE_CAP
    normalized = normalize_product_barcode(barcode)[0]
    sku = db.execute(select(SKU).where(SKU.client_id == client_id, SKU.barcode == normalized)).scalars().first()
    if not sku:
        raise RuntimeError(f"Нельзя публиковать WB: локальный SKU для ШК {normalized or barcode} не найден")
    cred = _active_wb_credential(db, client_id)
    if not cred:
        raise RuntimeError("Подключение WB для выбранного клиента не настроено")
    warehouse_id = _work_wb_warehouse_id(cred, wb_warehouse_id, physical_warehouse_id)
    if not warehouse_id:
        raise RuntimeError("Для выбранного физического склада не назначен рабочий склад WB")
    # FC_K9R31_SYNC_CHRT_TARGET: even synchronous local adjustments must publish the
    # complete WB size target, not the free quantity of the barcode that was edited.
    from app.services.stock_outbox_worker import _wb_size_targets
    size_targets, _siblings, chrt_by_code = _wb_size_targets(
        db, client_id=str(client_id), warehouse_id=str(physical_warehouse_id),
        external_wh=str(warehouse_id), codes=[normalized],
    )
    canonical_free = size_targets.get(normalized)
    if canonical_free is None or chrt_by_code.get(normalized) is None:
        raise RuntimeError(
            f"Нельзя публиковать WB: для ШК {normalized} не определён полный chrtId/размер. Обновите карточки WB."
        )
    amount = max(0, int(canonical_free))
    client = WBClient(decrypt_secret(cred.encrypted_api_key))
    _wb_validate_seller_warehouse(client, int(warehouse_id), cred=cred, physical_warehouse_id=physical_warehouse_id)
    chrt_id = _wb_chrt_id(db, client_id=client_id, barcode=barcode)
    try:
        _wb_write_and_verify_chrt_stocks(client, int(warehouse_id), {chrt_id: max(0, int(amount))})
    except Exception as first_exc:
        # A cached card can contain an obsolete size ID after WB card edits. Resolve the
        # exact seller barcode against the live content catalogue and retry once.
        fresh_rows = _wb_fresh_size_rows(client, [normalized])
        fresh_map = _wb_fresh_chrt_map(client, [normalized]) if not fresh_rows else {
            str(code): int(row.get("chrtID"))
            for row in fresh_rows
            for code in (row.get("skus") or [])
            if row.get("chrtID") is not None
        }
        fresh_chrt_id = fresh_map.get(normalized)
        if fresh_chrt_id is None or fresh_chrt_id == chrt_id:
            raise RuntimeError(
                f"WB отклонил остаток: warehouseId={warehouse_id}, ШК={normalize_product_barcode(barcode)[0]}, "
                f"chrtId={chrt_id}. {first_exc}"
            ) from first_exc
        # FC_K9R31_SYNC_FRESH_CHRT_RETARGET: if WB moved the barcode to another size,
        # recompute the complete sibling-size target before retrying. Reusing the old
        # amount can overwrite the new chrt with a target calculated for another size.
        fresh_targets, _fresh_siblings, fresh_chrts = _wb_size_targets(
            db, client_id=str(client_id), warehouse_id=str(physical_warehouse_id),
            external_wh=str(warehouse_id), codes=[normalized], extra_size_rows=fresh_rows,
        )
        fresh_amount = fresh_targets.get(normalized)
        if fresh_amount is None or fresh_chrts.get(normalized) != int(fresh_chrt_id):
            raise RuntimeError(
                f"WB карточка ШК {normalized} изменилась, но полную цель нового chrtId вычислить не удалось"
            ) from first_exc
        _wb_write_and_verify_chrt_stocks(client, int(warehouse_id), {int(fresh_chrt_id): max(0, int(fresh_amount))})
    return int(warehouse_id)


def _wb_current_stock(
    db: Session,
    *,
    client_id: uuid.UUID,
    physical_warehouse_id: uuid.UUID,
    wb_warehouse_id: int | None,
    barcode: str,
) -> int | None:
    cred = _active_wb_credential(db, client_id)
    if not cred:
        return None
    warehouse_id = _work_wb_warehouse_id(cred, wb_warehouse_id, physical_warehouse_id)
    if not warehouse_id:
        return None
    client = WBClient(decrypt_secret(cred.encrypted_api_key))
    try:
        chrt_id = _wb_chrt_id(db, client_id=client_id, barcode=barcode)
        return int(_wb_read_chrt_stocks(client, int(warehouse_id), [chrt_id]).get(chrt_id, 0))
    except Exception:
        return None

def _ensure_fbs_tracking_table(db: Session) -> None:
    """Reliability tables are migrated once at API startup; keep a lazy fallback."""
    global _FBS_TRACKING_READY
    if _FBS_TRACKING_READY:
        return
    ensure_runtime_schema(db)
    _FBS_TRACKING_READY = True


def _record_fbs_publication(
    db: Session,
    *,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    barcode: str,
    wb_warehouse_id: int | None,
    amount: int | None,
    status: str,
    error: str | None = None,
) -> None:
    _ensure_fbs_tracking_table(db)
    db.execute(text("""
        INSERT INTO fc_wb_stock_publications(
            client_id, warehouse_id, barcode, wb_warehouse_id,
            published_amount, status, last_error, updated_at
        ) VALUES (
            :client_id, :warehouse_id, :barcode, :wb_warehouse_id,
            :amount, :status, :error, NOW()
        )
        ON CONFLICT (client_id, warehouse_id, barcode) DO UPDATE SET
            wb_warehouse_id=EXCLUDED.wb_warehouse_id,
            published_amount=EXCLUDED.published_amount,
            status=EXCLUDED.status,
            last_error=EXCLUDED.last_error,
            updated_at=NOW()
    """), {
        "client_id": str(client_id),
        "warehouse_id": str(warehouse_id),
        "barcode": str(barcode),
        "wb_warehouse_id": wb_warehouse_id,
        "amount": amount,
        "status": status,
        "error": error,
    })
    db.commit()


def _wb_publication_events(db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, keys: set[str]) -> dict[str, dict]:
    clean = {str(key).strip() for key in keys if str(key or "").strip()}
    if not clean:
        return {}
    ensure_runtime_schema(db)
    stmt = text("""
        SELECT idempotency_key, barcode, wb_warehouse_id, mode, requested_quantity,
               resolved_amount, status, last_error, created_at, updated_at
        FROM fc_wb_stock_publication_events
        WHERE client_id=:client_id AND warehouse_id=:warehouse_id
          AND idempotency_key IN :keys
    """).bindparams(bindparam("keys", expanding=True))
    rows = db.execute(stmt, {
        "client_id": str(client_id), "warehouse_id": str(warehouse_id), "keys": sorted(clean),
    }).mappings().all()
    return {str(row["idempotency_key"]): dict(row) for row in rows}


def _upsert_wb_publication_events(db: Session, rows: list[dict], *, commit: bool = True) -> None:
    if not rows:
        return
    ensure_runtime_schema(db)
    db.execute(text("""
        INSERT INTO fc_wb_stock_publication_events(
            client_id, warehouse_id, idempotency_key, barcode, wb_warehouse_id,
            mode, requested_quantity, resolved_amount, status, last_error, created_at, updated_at
        ) VALUES (
            :client_id, :warehouse_id, :idempotency_key, :barcode, :wb_warehouse_id,
            :mode, :requested_quantity, :resolved_amount, :status, :last_error, NOW(), NOW()
        )
        ON CONFLICT (client_id, warehouse_id, idempotency_key) DO UPDATE SET
            barcode=EXCLUDED.barcode,
            wb_warehouse_id=EXCLUDED.wb_warehouse_id,
            mode=EXCLUDED.mode,
            requested_quantity=EXCLUDED.requested_quantity,
            resolved_amount=COALESCE(EXCLUDED.resolved_amount, fc_wb_stock_publication_events.resolved_amount),
            status=EXCLUDED.status,
            last_error=EXCLUDED.last_error,
            updated_at=NOW()
    """), rows)
    if commit:
        db.commit()


def _record_fbs_publications_batch(db: Session, rows: list[dict]) -> None:
    """Persist a whole WB publication result set with one DB round-trip/commit."""
    if not rows:
        return
    _ensure_fbs_tracking_table(db)
    db.execute(text("""
        INSERT INTO fc_wb_stock_publications(
            client_id, warehouse_id, barcode, wb_warehouse_id,
            published_amount, status, last_error, updated_at
        ) VALUES (
            :client_id, :warehouse_id, :barcode, :wb_warehouse_id,
            :amount, :status, :error, NOW()
        )
        ON CONFLICT (client_id, warehouse_id, barcode) DO UPDATE SET
            wb_warehouse_id=EXCLUDED.wb_warehouse_id,
            published_amount=EXCLUDED.published_amount,
            status=EXCLUDED.status,
            last_error=EXCLUDED.last_error,
            updated_at=NOW()
    """), rows)
    db.commit()


def _ensure_location(db: Session, warehouse_id: uuid.UUID, code: str) -> Location:
    clean = str(code or "").strip()
    if not clean:
        clean = "БЕЗ-АДРЕСА"
    loc = db.execute(select(Location).where(
        Location.warehouse_id == warehouse_id,
        or_(Location.code == clean, Location.barcode == clean),
    )).scalars().first()
    if loc:
        if not loc.is_active:
            loc.is_active = True
        return loc
    loc = Location(warehouse_id=warehouse_id, parent_id=None, code=clean, barcode=clean, name=clean, location_type="CELL", is_active=True)
    db.add(loc)
    db.flush()
    return loc


# FC_BOX_WAREHOUSE_SCOPE_09415D_20260903
def _fc_box_storage_barcode(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID, code: str) -> str:
    """Return a globally safe technical barcode while the visible box code stays warehouse-local.

    Existing installations may have a global UNIQUE constraint on Container.barcode.
    Operators, however, need to reuse human box codes (001, 002, etc.) on another
    physical warehouse.  We keep the visible/scannable code in Container.name and
    generate an internal FCBOX barcode only when the raw barcode is already occupied.
    """
    clean = str(code or "").strip()
    if not clean:
        return clean
    occupied = db.execute(select(Container.id).where(Container.barcode == clean).limit(1)).scalar_one_or_none()
    if not occupied:
        return clean
    candidate = "FCBOX-" + uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"fulfillcore-box:{client_id}:{warehouse_id}:{clean}",
    ).hex[:24].upper()
    collision = db.execute(select(Container.id).where(Container.barcode == candidate).limit(1)).scalar_one_or_none()
    if collision:
        candidate = "FCBOX-" + uuid.uuid4().hex[:24].upper()
    return candidate

def _ensure_container(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID, location_id: uuid.UUID, code: str | None) -> Container | None:
    clean = str(code or "").strip()
    if not clean:
        return None
    matches = db.execute(select(Container).where(
        Container.client_id == client_id,
        Container.warehouse_id == warehouse_id,
        Container.status == "ACTIVE",
        or_(Container.barcode == clean, getattr(Container, "name", Container.barcode) == clean),
    )).scalars().all()
    if len(matches) > 1:
        raise HTTPException(status_code=409, detail=f"Активный короб {clean} найден несколько раз. Исправьте дубли в разделе «Короба».")
    cont = matches[0] if matches else None
    if cont:
        if cont.location_id is None:
            cont.location_id = location_id
        return cont
    storage_barcode = _fc_box_storage_barcode(db, client_id, warehouse_id, clean)
    kwargs = dict(client_id=client_id, warehouse_id=warehouse_id, location_id=location_id, barcode=storage_barcode, container_type="BOX", status="ACTIVE")
    if hasattr(Container, "name"):
        kwargs["name"] = clean
    cont = Container(**kwargs)
    db.add(cont)
    db.flush()
    return cont


def _manual_storage_token(value: str | None, *, numeric_width: int | None = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if numeric_width and re.fullmatch(r"\d+", raw):
        raw = raw.zfill(numeric_width)
    token = re.sub(r"\s+", "-", raw.upper())
    token = re.sub(r"[^0-9A-ZА-ЯЁ_-]", "", token)
    return re.sub(r"-{2,}", "-", token).strip("-_")


def _manual_storage_location(
    db: Session,
    *,
    warehouse_id: uuid.UUID,
    section: str | None,
    aisle: str | None,
    pallet: str | None,
) -> tuple[Location, dict]:
    """Create/reuse only the structural levels that the operator actually uses."""
    section_raw = str(section or "").strip()
    aisle_raw = str(aisle or "").strip()
    pallet_raw = str(pallet or "").strip()

    # FC_G5A8K4_LOCATION_QR_ALIAS_TRUTH
    # Location labels deliberately encode a stable technical FCLOC-* barcode while
    # printing the human code (PAL-02/PP-014/etc.) above it. A scan must resolve that
    # barcode back to the existing Location, never create PAL-FCLOC-....
    if pallet_raw:
        upper_value = pallet_raw.upper()
        scanned_loc = db.execute(select(Location).where(
            Location.warehouse_id == warehouse_id,
            or_(
                func.upper(Location.code) == upper_value,
                func.upper(Location.barcode) == upper_value,
            ),
        )).scalars().first()
        if scanned_loc:
            location_type = str(scanned_loc.location_type or "").upper()
            if location_type not in {"PALLET", "PALLET_PLACE"}:
                raise HTTPException(
                    status_code=409,
                    detail=f"QR {pallet_raw} относится к адресу {scanned_loc.code} типа {location_type or 'неизвестно'}, а поле ожидает палету",
                )
            if not scanned_loc.is_active:
                scanned_loc.is_active = True
            code = str(scanned_loc.code or pallet_raw)
            pallet_display = code.split("-PAL-", 1)[-1] if "-PAL-" in code else (code[4:] if code.upper().startswith("PAL-") else code)
            return scanned_loc, {
                "section": "", "aisle": "", "pallet": pallet_display,
                "display": str(scanned_loc.name or scanned_loc.code),
                "resolved_location_code": code, "resolved_from_qr": True,
            }
        if upper_value.startswith("FCLOC-"):
            raise HTTPException(
                status_code=404,
                detail=f"QR адреса {pallet_raw} не найден на выбранном складе. Новый палет по техническому QR не создан.",
            )

    section_token = _manual_storage_token(section_raw)
    aisle_token = _manual_storage_token(aisle_raw)
    pallet_token = _manual_storage_token(pallet_raw, numeric_width=3)

    section_loc = None
    if section_raw:
        section_code = f"SEC-{section_token}"
        section_name = f"Секция {section_raw}"
        section_loc = db.execute(select(Location).where(
            Location.warehouse_id == warehouse_id,
            or_(Location.code == section_code, and_(Location.name == section_name, Location.location_type == "SECTION")),
        )).scalars().first()
        if not section_loc:
            section_loc = Location(
                warehouse_id=warehouse_id, parent_id=None, code=section_code, barcode=section_code,
                name=section_name, location_type="SECTION", is_active=True,
            )
            db.add(section_loc)
            db.flush()
        elif not section_loc.is_active:
            section_loc.is_active = True

    if pallet_raw:
        parts = []
        name_parts = []
        if section_raw:
            parts.append(f"SEC-{section_token}")
            name_parts.append(f"Секция {section_raw}")
        if aisle_raw:
            parts.append(f"AISLE-{aisle_token}")
            name_parts.append(f"Проход {aisle_raw}")
        pallet_code = pallet_token if pallet_token.upper().startswith("PAL-") else f"PAL-{pallet_token}"
        parts.append(pallet_code)
        name_parts.append(f"Палета {pallet_raw}")
        code = "-".join(parts)
        name = " · ".join(name_parts)
        location_type = "PALLET"
    elif aisle_raw:
        parts = []
        name_parts = []
        if section_raw:
            parts.append(f"SEC-{section_token}")
            name_parts.append(f"Секция {section_raw}")
        parts.append(f"AISLE-{aisle_token}")
        name_parts.append(f"Проход {aisle_raw}")
        code = "-".join(parts)
        name = " · ".join(name_parts)
        location_type = "AISLE"
    elif section_loc:
        return section_loc, {"section": section_raw, "aisle": "", "pallet": "", "display": section_loc.name or section_loc.code}
    else:
        loc = _ensure_location(db, warehouse_id, "ПРИЕМКА")
        return loc, {"section": "", "aisle": "", "pallet": "", "display": "Только короб"}

    loc = db.execute(select(Location).where(
        Location.warehouse_id == warehouse_id,
        or_(Location.code == code, and_(Location.name == name, Location.location_type == location_type)),
    )).scalars().first()
    if not loc:
        loc = Location(
            warehouse_id=warehouse_id, parent_id=section_loc.id if section_loc else None,
            code=code, barcode=code, name=name, location_type=location_type, is_active=True,
        )
        db.add(loc)
        db.flush()
    else:
        if not loc.is_active:
            loc.is_active = True
        if section_loc and loc.parent_id is None:
            loc.parent_id = section_loc.id
    return loc, {"section": section_raw, "aisle": aisle_raw, "pallet": pallet_raw, "display": name}


def _box_label_footer(visible: str, show_date: bool = False, label_date: str | None = None) -> str:
    if not show_date:
        return visible
    raw = str(label_date or "").strip()
    if raw:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").strftime("%d.%m.%Y")
        except ValueError:
            raise HTTPException(status_code=422, detail="Некорректная дата этикетки")
    return datetime.now().astimezone().strftime("%d.%m.%Y")


def _draw_manual_box_label_value(c: canvas.Canvas, visible: str, page_w: float, page_h: float, footer: str | None = None) -> None:
    visible = str(visible or "КОРОБ").strip() or "КОРОБ"
    code = visible
    bold_font = "Helvetica-Bold"
    regular_font = "Helvetica"
    bold_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    regular_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    try:
        if bold_path.exists():
            if "FCBoxBold" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("FCBoxBold", str(bold_path)))
            bold_font = "FCBoxBold"
        if regular_path.exists():
            if "FCBoxRegular" not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont("FCBoxRegular", str(regular_path)))
            regular_font = "FCBoxRegular"
    except Exception:
        pass
    size = 13
    while size > 7 and pdfmetrics.stringWidth(visible, bold_font, size) > page_w - 6 * mm:
        size -= 0.5
    c.setFont(bold_font, size)
    c.drawCentredString(page_w / 2, page_h - 6.6 * mm, visible[:80])
    drawing = createBarcodeDrawing("QR", value=code, width=24 * mm, height=24 * mm)
    drawing.drawOn(c, (page_w - drawing.width) / 2, 5.2 * mm)
    footer = str(footer if footer is not None else visible)
    footer_size = 7 if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", footer) else 6
    while footer_size > 4 and pdfmetrics.stringWidth(footer, regular_font, footer_size) > page_w - 4 * mm:
        footer_size -= 0.5
    c.setFont(regular_font, footer_size)
    c.drawCentredString(page_w / 2, 2.2 * mm, footer[:100])


def _manual_box_labels_pdf(containers: list[Container], width_mm: float = 58, height_mm: float = 40, show_date: bool = False, label_date: str | None = None) -> bytes:
    output = BytesIO()
    page_w, page_h = width_mm * mm, height_mm * mm
    c = canvas.Canvas(output, pagesize=(page_w, page_h))
    for container in containers:
        visible = str(getattr(container, "name", None) or container.barcode or "КОРОБ")
        _draw_manual_box_label_value(c, visible, page_w, page_h, _box_label_footer(visible, show_date, label_date))
        c.showPage()
    c.save()
    return output.getvalue()


def _manual_box_codes_pdf(codes: list[str], width_mm: float = 58, height_mm: float = 40, show_date: bool = False, label_date: str | None = None) -> bytes:
    output = BytesIO()
    page_w, page_h = width_mm * mm, height_mm * mm
    c = canvas.Canvas(output, pagesize=(page_w, page_h))
    for code in codes:
        _draw_manual_box_label_value(c, code, page_w, page_h, _box_label_footer(code, show_date, label_date))
        c.showPage()
    c.save()
    return output.getvalue()


def _manual_box_label_pdf(container: Container, width_mm: float = 58, height_mm: float = 40, show_date: bool = False, label_date: str | None = None) -> bytes:
    return _manual_box_labels_pdf([container], width_mm, height_mm, show_date, label_date)


@router.post("/manual/storage/box")
def ensure_manual_storage_box(payload: ManualStorageBoxEnsureRequest, request: Request, db: Session = Depends(get_db)):
    """Activate one current receiving box with an optional section/aisle/pallet hierarchy."""
    from app.services.warehouse_availability import lock_scope
    from app.services.box_routes import set_box_route, enrich
    lock_scope(db,payload.client_id,payload.warehouse_id)
    value = str(payload.box_value or "").strip()
    if payload.box_mode == "SCAN":
        from app.services.box_intake_rules import normalize_box_scan
        value = normalize_box_scan(value)
    if not value:
        raise HTTPException(status_code=400, detail="Укажите или отсканируйте короб")
    location, structure = _manual_storage_location(
        db, warehouse_id=payload.warehouse_id, section=payload.section, aisle=payload.aisle, pallet=payload.pallet,
    )
    active_matches = db.execute(select(Container).where(
        Container.client_id == payload.client_id,
        Container.warehouse_id == payload.warehouse_id,
        Container.status == "ACTIVE",
        or_(Container.barcode == value, getattr(Container, "name", Container.barcode) == value),
    ).with_for_update()).scalars().all()
    if len(active_matches) > 1:
        raise HTTPException(status_code=409, detail=f"Активный короб {value} найден несколько раз. Исправьте дубли в разделе «Короба».")
    existing = active_matches[0] if active_matches else None
    created = False
    generated_qr = False
    if existing:
        if existing.location_id and existing.location_id != location.id:
            box_quantity = Decimal(db.execute(select(func.coalesce(func.sum(InventoryBalance.quantity), 0)).where(
                InventoryBalance.client_id == payload.client_id,
                InventoryBalance.warehouse_id == payload.warehouse_id,
                InventoryBalance.container_id == existing.id,
            )).scalar_one() or 0)
            old = db.get(Location, existing.location_id)
            if box_quantity > 0:
                raise HTTPException(status_code=409, detail=(
                    f"Короб {getattr(existing, 'name', None) or existing.barcode} уже содержит товар и находится: "
                    f"{(old.name if old else '') or (old.code if old else 'другой адрес')}. "
                    "Чтобы перенести заполненный короб, используйте раздел «Перемещение»."
                ))
            if not payload.force_move:
                raise HTTPException(status_code=409, detail={
                    "code": "BOX_OTHER_LOCATION",
                    "message": f"Пустой короб {getattr(existing, 'name', None) or existing.barcode} уже находится: {(old.name if old else '') or (old.code if old else 'другой адрес')}. Переместить в выбранное место?",
                })
        existing.location_id = location.id
        existing.status = "ACTIVE"
        container = existing
    else:
        created = True
        if payload.box_mode == "NAME":
            box_barcode = _fc_box_storage_barcode(db, payload.client_id, payload.warehouse_id, value)
            box_name = value
            generated_qr = True
        else:
            box_barcode = _fc_box_storage_barcode(db, payload.client_id, payload.warehouse_id, value)
            box_name = value
        kwargs = dict(
            client_id=payload.client_id, warehouse_id=payload.warehouse_id, location_id=location.id,
            barcode=box_barcode, container_type="BOX", status="ACTIVE",
        )
        if hasattr(Container, "name"):
            kwargs["name"] = box_name
        container = Container(**kwargs)
        db.add(container)
        db.flush()
    if payload.row_on_pallet is not None:
        set_box_route(db,container_id=container.id,client_id=payload.client_id,warehouse_id=payload.warehouse_id,
            row_on_pallet=payload.row_on_pallet,actor=getattr(request.state,"username",None))
    db.flush()
    route=enrich(db,payload.client_id,payload.warehouse_id,[{"location_id":str(location.id),"container_id":str(container.id)}])[0]
    db.commit()
    return {
        "row_on_pallet":route["route_row"],"row_position":route["route_position"],"route_label":route["route_label"],
        "ok": True, "created": created, "generated_qr": generated_qr,
        "container_id": str(container.id), "container_name": str(getattr(container, "name", None) or container.barcode),
        "container_barcode": str(container.barcode), "location_id": str(location.id),
        "location_code": str(location.code), "location_name": str(location.name or location.code),
        **structure,
    }


def _bulk_box_names(first_box: str, count: int) -> list[str]:
    from app.services.box_intake_rules import box_code
    try:
        first = box_code(first_box)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    match = re.match(r"^(.*?)(\d+)$", first)
    if not match:
        raise HTTPException(status_code=422, detail="В первом коробе нужен номер в конце, например BOX_SAVASH_001")
    prefix, digits = match.groups()
    start = int(digits)
    width = len(digits)
    return [f"{prefix}{str(start + offset).zfill(width)}" for offset in range(count)]


def _scoped_containers(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID, ids: list[uuid.UUID]) -> list[Container]:
    rows = db.execute(select(Container).where(
        Container.client_id == client_id, Container.warehouse_id == warehouse_id, Container.id.in_(ids)
    )).scalars().all()
    by_id = {str(x.id): x for x in rows}
    ordered = [by_id.get(str(x)) for x in ids]
    if any(x is None for x in ordered):
        raise HTTPException(status_code=404, detail="Один или несколько коробов не найдены на выбранном складе")
    return ordered


@router.post("/manual/storage/boxes/bulk")
def create_manual_storage_boxes_bulk(payload: ManualStorageBoxBulkCreateRequest, db: Session = Depends(get_db)):
    """Create a visible numbered box range in virtual ПРИЕМКА; existing active names are reused."""
    from app.services.warehouse_availability import lock_scope
    lock_scope(db, payload.client_id, payload.warehouse_id)
    names = _bulk_box_names(payload.first_box, payload.count)
    staging = _ensure_location(db, payload.warehouse_id, "ПРИЕМКА")
    existing = db.execute(select(Container).where(
        Container.client_id == payload.client_id, Container.warehouse_id == payload.warehouse_id,
        or_(Container.name.in_(names), Container.barcode.in_(names))
    ).with_for_update()).scalars().all()
    by_visible: dict[str, Container] = {}
    archived_visible: set[str] = set()
    for row in existing:
        keys = {str(getattr(row, "name", None) or ""), str(row.barcode or "")}
        for name in names:
            if name not in keys:
                continue
            if str(row.status or "").upper() == "ACTIVE":
                if name in by_visible and by_visible[name].id != row.id:
                    raise HTTPException(status_code=409, detail=f"Активный короб {name} найден несколько раз. Исправьте дубли в разделе «Короба».")
                by_visible[name] = row
            else:
                archived_visible.add(name)
    # Archived rows are historical records only. Reusing the same human box name
    # creates a NEW active container with a new technical barcode and new UUID.
    created = 0
    recreated = 0
    rows = []
    for name in names:
        container = by_visible.get(name)
        was_created = False
        if container is None:
            kwargs = dict(
                client_id=payload.client_id, warehouse_id=payload.warehouse_id, location_id=staging.id,
                barcode=_fc_box_storage_barcode(db, payload.client_id, payload.warehouse_id, name),
                container_type="BOX", status="ACTIVE",
            )
            if hasattr(Container, "name"):
                kwargs["name"] = name
            container = Container(**kwargs)
            db.add(container); db.flush(); created += 1; was_created = True
            if name in archived_visible:
                recreated += 1
        rows.append({
            "container_id": str(container.id), "name": str(getattr(container, "name", None) or container.barcode),
            "barcode": str(container.barcode), "created": was_created,
            "location_id": str(container.location_id) if container.location_id else None,
            "unplaced": bool(container.location_id == staging.id),
        })
    db.commit()
    return {
        "ok": True, "first_box": names[0], "last_box": names[-1], "count": len(rows),
        "created": created, "recreated_from_archive": recreated, "existing": len(rows) - created, "rows": rows,
    }


def _r130_bucket_key(client_id: uuid.UUID, warehouse_id: uuid.UUID, sku_id: uuid.UUID, location_id: uuid.UUID, container_id: uuid.UUID | None) -> str:
    return f"{client_id}:{warehouse_id}:{sku_id}:{location_id}:{container_id or 'NONE'}"


def _r130_reservation_sensitive_boxes(
    db: Session,
    *,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    containers: list[Container],
    balances_by_container: dict[str, list[InventoryBalance]],
) -> set[str]:
    """Return boxes that must use the legacy reservation-safe move path.

    The common case (no live reservation rows touching the selected boxes/location+SKU)
    can be moved in one transaction. Any ambiguous/live reservation keeps the old
    conservative per-box routine, so speed never weakens reservation safety.
    """
    if not containers:
        return set()
    try:
        if not db.execute(text("SELECT to_regclass('fc_stock_reservations')")).scalar():
            return set()
    except Exception:
        return {str(c.id) for c in containers}

    container_ids = [str(c.id) for c in containers]
    location_ids = list({str(c.location_id) for c in containers if c.location_id})
    if not location_ids:
        return set()
    stmt = text("""
        SELECT source_container_id, source_location_id, sku_id
        FROM fc_stock_reservations
        WHERE client_id=:client AND warehouse_id=:warehouse
          AND status IN ('SALE_RESERVED','ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
          AND (CAST(source_container_id AS varchar) IN :container_ids OR CAST(source_location_id AS varchar) IN :location_ids)
    """).bindparams(bindparam("container_ids", expanding=True), bindparam("location_ids", expanding=True))
    rows = db.execute(stmt, {
        "client": str(client_id), "warehouse": str(warehouse_id),
        "container_ids": container_ids, "location_ids": location_ids,
    }).mappings().all()
    sensitive: set[str] = set()
    by_location_sku: dict[tuple[str, str], set[str]] = {}
    for container in containers:
        key = str(container.id)
        for balance in balances_by_container.get(key, []):
            by_location_sku.setdefault((str(container.location_id or ''), str(balance.sku_id)), set()).add(key)
    for row in rows:
        exact = str(row.get("source_container_id") or "").strip()
        if exact and exact in container_ids:
            sensitive.add(exact)
            continue
        if not exact:
            sensitive.update(by_location_sku.get((str(row.get("source_location_id") or ""), str(row.get("sku_id") or "")), set()))
    return sensitive


@router.post("/manual/boxes/bulk-move")
def manual_boxes_bulk_move(payload: ManualBoxesBulkMoveRequest, db: Session = Depends(get_db)):
    """Fast mass move with one transaction for ordinary boxes and safe fallback.

    r130 does not loop the expensive whole-container routine for every normal box.
    Empty boxes and stocked boxes without live reservation rows are migrated together.
    Only reservation-sensitive boxes use the legacy reservation-safe function.
    """
    destination = db.get(Location, payload.destination_location_id)
    if not destination or destination.warehouse_id != payload.warehouse_id or not destination.is_active:
        raise HTTPException(status_code=400, detail="Адрес назначения недоступен")
    if str(getattr(destination, "location_type", "") or "").upper() == "SECTION":
        raise HTTPException(status_code=422, detail="Выберите палету, палетоместо или ячейку, а не секцию")

    ids = list(dict.fromkeys(payload.container_ids))
    if not ids:
        return {"ok": True, "requested": 0, "moved": 0, "already": 0, "failed": 0, "results": []}

    # One advisory guard for the fast transaction. It uses the same namespace as
    # picking-list stock mutations, so a fast UI never races a live pick allocation.
    dialect = str(getattr(getattr(db.get_bind(), "dialect", None), "name", "") or "").lower()
    if dialect == "postgresql":
        db.execute(text("SET LOCAL lock_timeout = '2500ms'"))
        acquired = bool(db.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"), {
            "key": f"fulfillcore:picking-stock:{payload.client_id}:{payload.warehouse_id}"
        }).scalar())
        if not acquired:
            db.rollback()
            raise HTTPException(status_code=409, detail="Сейчас меняется резерв/сборочный лист. Повторите перемещение через несколько секунд.")

    containers = db.execute(select(Container).where(
        Container.client_id == payload.client_id,
        Container.warehouse_id == payload.warehouse_id,
        Container.id.in_(ids),
    ).with_for_update()).scalars().all()
    by_id = {str(row.id): row for row in containers}

    balances = db.execute(select(InventoryBalance).where(
        InventoryBalance.client_id == payload.client_id,
        InventoryBalance.warehouse_id == payload.warehouse_id,
        InventoryBalance.container_id.in_(ids),
        InventoryBalance.quantity > 0,
    ).order_by(InventoryBalance.container_id.asc(), InventoryBalance.sku_id.asc()).with_for_update()).scalars().all()
    balances_by_container: dict[str, list[InventoryBalance]] = {}
    for balance in balances:
        balances_by_container.setdefault(str(balance.container_id), []).append(balance)

    active_stocked = [c for c in containers if str(c.status or '').upper() == 'ACTIVE' and balances_by_container.get(str(c.id)) and c.location_id != payload.destination_location_id]
    sensitive = _r130_reservation_sensitive_boxes(
        db, client_id=payload.client_id, warehouse_id=payload.warehouse_id,
        containers=active_stocked, balances_by_container=balances_by_container,
    )

    result_by_id: dict[str, dict] = {}
    fast_stocked: list[Container] = []
    slow_stocked: list[tuple[uuid.UUID, str]] = []
    empty_changed: list[Container] = []
    for container_id in ids:
        key = str(container_id)
        container = by_id.get(key)
        if container is None:
            result_by_id[key] = {"container_id": key, "ok": False, "error": "Короб не найден на выбранном складе"}
            continue
        public_code = str(getattr(container, "name", None) or container.barcode or key)
        if str(container.status or "").upper() != "ACTIVE":
            result_by_id[key] = {"container_id": key, "container": public_code, "ok": False, "error": "Короб удалён или находится в архиве"}
            continue
        if container.location_id == payload.destination_location_id:
            result_by_id[key] = {"container_id": key, "container": public_code, "ok": True, "already": True}
            continue
        if not balances_by_container.get(key):
            container.location_id = payload.destination_location_id
            empty_changed.append(container)
            result_by_id[key] = {"container_id": key, "container": public_code, "ok": True, "empty": True, "fast": True}
        elif key in sensitive:
            slow_stocked.append((container.id, public_code))
        else:
            fast_stocked.append(container)

    # Batch physical balances + history for all reservation-free stocked boxes.
    if fast_stocked:
        fast_ids = {str(c.id) for c in fast_stocked}
        destination_keys = []
        for container in fast_stocked:
            for balance in balances_by_container.get(str(container.id), []):
                destination_keys.append(_r130_bucket_key(payload.client_id, payload.warehouse_id, balance.sku_id, payload.destination_location_id, container.id))
        existing_dest = {}
        if destination_keys:
            rows = db.execute(select(InventoryBalance).where(InventoryBalance.bucket_key.in_(destination_keys)).with_for_update()).scalars().all()
            existing_dest = {str(row.bucket_key): row for row in rows}

        operations: dict[str, InventoryOperation] = {}
        for container in fast_stocked:
            key = str(container.id)
            op = InventoryOperation(
                client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                operation_type="MOVE_CONTAINER",
                idempotency_key=f"BULK_BOX_MOVE:{payload.request_id}:{container.id}",
                performed_by=payload.performed_by, reference=None,
                comment="Массовое перемещение выбранных коробов · FAST_BATCH_R130",
            )
            db.add(op); operations[key] = op
        db.flush()

        new_balances: list[InventoryBalance] = []
        history_lines: list[InventoryOperationLine] = []
        for container in fast_stocked:
            key = str(container.id)
            old_location_id = container.location_id
            for source in balances_by_container.get(key, []):
                qty = Decimal(str(source.quantity or 0))
                if qty <= 0:
                    continue
                dest_key = _r130_bucket_key(payload.client_id, payload.warehouse_id, source.sku_id, payload.destination_location_id, container.id)
                dest = existing_dest.get(dest_key)
                if dest is None:
                    dest = InventoryBalance(
                        bucket_key=dest_key, client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                        sku_id=source.sku_id, location_id=payload.destination_location_id,
                        container_id=container.id, quantity=Decimal(0), version=1,
                    )
                    db.add(dest); new_balances.append(dest); existing_dest[dest_key] = dest
                dest.quantity = Decimal(str(dest.quantity or 0)) + qty
                dest.version = int(dest.version or 0) + 1
                source.quantity = Decimal(0)
                source.version = int(source.version or 0) + 1
                history_lines.append(InventoryOperationLine(
                    operation_id=operations[key].id, sku_id=source.sku_id, quantity=qty,
                    source_location_id=old_location_id, source_container_id=container.id,
                    destination_location_id=payload.destination_location_id, destination_container_id=container.id,
                    reason="MOVE_CONTAINER",
                ))
            container.location_id = payload.destination_location_id
            result_by_id[key] = {"container_id": key, "container": str(getattr(container, "name", None) or container.barcode or key), "ok": True, "fast": True, "operation_id": str(operations[key].id)}
        if history_lines:
            db.add_all(history_lines)

    # One commit makes all empty + ordinary stocked boxes visible immediately.
    if empty_changed or fast_stocked:
        db.commit()
    else:
        db.rollback()  # release the batch advisory lock before legacy per-box calls

    # Only the uncommon reservation-sensitive boxes take the legacy safe path.
    for container_id, public_code in slow_stocked:
        key = str(container_id)
        try:
            operation = move_entire_container(
                db, client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                container_id=container_id, destination_location_id=payload.destination_location_id,
                idempotency_key=f"BULK_BOX_MOVE:{payload.request_id}:{container_id}",
                performed_by=payload.performed_by, reference=None,
                comment="Массовое перемещение выбранных коробов",
            )
            result_by_id[key] = {"container_id": key, "container": public_code, "ok": True, "operation_id": str(operation.id), "safe_path": True}
        except HTTPException as exc:
            detail = exc.detail.get("message") if isinstance(exc.detail, dict) else exc.detail
            result_by_id[key] = {"container_id": key, "container": public_code, "ok": False, "error": str(detail or "Перемещение запрещено")}
        except Exception as exc:
            db.rollback()
            result_by_id[key] = {"container_id": key, "container": public_code, "ok": False, "error": str(exc or "Ошибка перемещения")}

    results = [result_by_id[str(container_id)] for container_id in ids]
    moved = sum(1 for row in results if row.get("ok") and not row.get("already"))
    already = sum(1 for row in results if row.get("ok") and row.get("already"))
    failed = sum(1 for row in results if not row.get("ok"))
    fast_moved = sum(1 for row in results if row.get("ok") and row.get("fast"))
    return {
        "ok": failed == 0, "destination_location_id": str(destination.id),
        "destination_code": str(destination.code or destination.name or destination.id),
        "requested": len(ids), "moved": moved, "already": already, "failed": failed,
        "fast_moved": fast_moved, "safe_path_moved": max(0, moved - fast_moved),
        "results": results,
    }


@router.post("/manual/boxes/labels.pdf")
def manual_box_labels_pdf(payload: ManualBoxLabelsRequest, db: Session = Depends(get_db)):
    rows = _scoped_containers(db, payload.client_id, payload.warehouse_id, payload.container_ids)
    content = _manual_box_labels_pdf(rows, payload.width_mm, payload.height_mm, payload.show_date, payload.label_date)
    return Response(content=content, media_type="application/pdf", headers={
        "Content-Disposition": 'inline; filename="FulfillCore-box-labels.pdf"',
        "X-FulfillCore-Label-Count": str(len(rows)),
    })


@router.post("/manual/boxes/labels/queue")
def queue_manual_box_labels(payload: ManualBoxLabelsQueueRequest, db: Session = Depends(get_db)):
    from app.services.receiving_labels import box_agent_png
    rows = _scoped_containers(db, payload.client_id, payload.warehouse_id, payload.container_ids)
    station = payload.print_station.strip().upper()
    if not station:
        raise HTTPException(status_code=422, detail="Укажите рабочее место печати")
    jobs = []
    for container in rows:
        visible = str(getattr(container, "name", None) or container.barcode)
        try:
            content = box_agent_png(visible, payload.width_mm, payload.height_mm, footer_text=_box_label_footer(visible, payload.show_date, payload.label_date))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        job = queue_print_job(db, station_code=station, payload=content,
            file_name=f"FulfillCore-box-{container.id}.png", document_type="BOX_QR_LABEL",
            client_id=payload.client_id, object_type="CONTAINER", object_id=str(container.id), content_type="image/png")
        jobs.append(job)
    db.commit()
    return {"ok": True, "labels_queued": len(jobs), "print_station": station, "print_job_ids": [str(x.id) for x in jobs]}


@router.post("/manual/box-labels-only/labels.pdf")
def manual_loose_box_labels_pdf(payload: ManualLooseBoxLabelsRequest):
    # Label-only mode deliberately does not create Container rows.
    codes = _bulk_box_names(payload.first_box, payload.count)
    content = _manual_box_codes_pdf(codes, payload.width_mm, payload.height_mm, payload.show_date, payload.label_date)
    return Response(content=content, media_type="application/pdf", headers={
        "Content-Disposition": 'inline; filename="FulfillCore-box-codes.pdf"',
        "X-FulfillCore-Label-Count": str(len(codes)),
        "X-FulfillCore-Database-Write": "none",
    })


@router.post("/manual/box-labels-only/labels/queue")
def queue_manual_loose_box_labels(payload: ManualLooseBoxLabelsQueueRequest, db: Session = Depends(get_db)):
    from app.services.receiving_labels import box_agent_png
    codes = _bulk_box_names(payload.first_box, payload.count)
    station = payload.print_station.strip().upper()
    if not station:
        raise HTTPException(status_code=422, detail="Укажите рабочее место печати")
    jobs = []
    for code in codes:
        content = box_agent_png(code, payload.width_mm, payload.height_mm, footer_text=_box_label_footer(code, payload.show_date, payload.label_date))
        job = queue_print_job(
            db, station_code=station, payload=content,
            file_name=f"FulfillCore-box-code-{re.sub(r'[^A-Za-z0-9._-]+', '_', code)[:80]}.png",
            document_type="BOX_QR_LABEL_ONLY", client_id=payload.client_id,
            object_type="BOX_LABEL_ONLY", object_id=code, content_type="image/png",
        )
        jobs.append(job)
    db.commit()
    return {
        "ok": True, "labels_queued": len(jobs), "print_station": station,
        "first_box": codes[0], "last_box": codes[-1], "count": len(codes),
        "database_boxes_created": 0, "print_job_ids": [str(x.id) for x in jobs],
    }


@router.get("/manual/boxes/{container_id}/label.pdf")
def manual_box_label_pdf(
    container_id: uuid.UUID, client_id: uuid.UUID, warehouse_id: uuid.UUID,
    width_mm: float = Query(default=58, ge=25, le=120), height_mm: float = Query(default=40, ge=25, le=120),
    db: Session = Depends(get_db),
):
    container = db.get(Container, container_id)
    if not container or container.client_id != client_id or container.warehouse_id != warehouse_id:
        raise HTTPException(status_code=404, detail="Короб не найден")
    content = _manual_box_label_pdf(container, width_mm, height_mm)
    return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="FulfillCore-box-label.pdf"'})


def _box_print_status(job):
    return {"ok":True,"print_job_id":str(job.id),"print_station":job.station_code,
            "status":job.status,"error":job.error_message}


@router.post("/manual/boxes/{container_id}/label/queue")
def queue_manual_box_label(container_id: uuid.UUID, payload: ManualBoxLabelQueueRequest, db: Session = Depends(get_db)):
    from app.models import PrintJob
    from app.services.receiving_labels import box_agent_png
    container = db.get(Container, container_id)
    if not container or container.client_id != payload.client_id or container.warehouse_id != payload.warehouse_id:
        raise HTTPException(status_code=404, detail="Короб не найден")
    station=payload.print_station.strip().upper()
    if not station:raise HTTPException(422,detail="Укажите рабочее место печати")
    if payload.request_id:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:k))'),{'k':'fc:box-label:'+str(payload.request_id)})
        old=db.get(PrintJob,payload.request_id)
        if old:
            if old.client_id!=payload.client_id or old.object_id!=str(container_id) or old.document_type!='BOX_QR_LABEL' or old.station_code!=station:
                raise HTTPException(409,detail="Идентификатор печати уже используется")
            return _box_print_status(old)
    try:content=box_agent_png(container.name or container.barcode,payload.width_mm,payload.height_mm)
    except ValueError as e:raise HTTPException(422,detail=str(e))
    job = queue_print_job(db, station_code=station, payload=content,
        file_name=f"FulfillCore-box-{container.id}.png", document_type="BOX_QR_LABEL",
        client_id=payload.client_id, object_type="CONTAINER", object_id=str(container.id), content_type="image/png")
    if payload.request_id:job.id=payload.request_id
    db.commit()
    return _box_print_status(job)


@router.get("/manual/boxes/{container_id}/label/jobs/{job_id}")
def manual_box_print_status(container_id:uuid.UUID,job_id:uuid.UUID,client_id:uuid.UUID,warehouse_id:uuid.UUID,db:Session=Depends(get_db)):
    from app.models import PrintJob
    container=db.get(Container,container_id);job=db.get(PrintJob,job_id)
    if not container or container.client_id!=client_id or container.warehouse_id!=warehouse_id or not job or job.client_id!=client_id or job.object_id!=str(container_id) or job.document_type!='BOX_QR_LABEL':
        raise HTTPException(404,detail='Задание печати не найдено')
    return _box_print_status(job)


def _find_or_create_sku(db: Session, client_id: uuid.UUID, barcode: str, article: str | None = None, size: str | None = None) -> SKU:
    barcode, _duplicated_from = normalize_product_barcode(barcode)
    if not barcode:
        raise HTTPException(status_code=400, detail="Пустой ШК товара")
    sku = db.execute(select(SKU).where(SKU.client_id == client_id, SKU.barcode == barcode)).scalars().first()
    product = wb_product_identity(db, client_id, barcode)
    supplied_article = str(article or "").strip() or None
    supplied_size = str(size or "").strip() or None
    if sku:
        # WB card + exact barcode is authoritative for display fields. This repairs
        # placeholder rows created by an earlier immediate scan without touching stock.
        if product:
            sku.article = product.get("article") or sku.article
            sku.name = product.get("name") or sku.name
            sku.size = product.get("size") or supplied_size or sku.size
        else:
            if supplied_article and (not sku.article or sku.article in {sku.barcode, sku.sku_code}):
                sku.article = supplied_article
            if supplied_article and (not sku.name or sku.name in {sku.barcode, sku.sku_code, sku.article}):
                sku.name = supplied_article
            if supplied_size and not sku.size:
                sku.size = supplied_size
        sku.is_active = True
        db.flush()
        return sku
    resolved_article = (product or {}).get("article") or supplied_article or barcode
    resolved_name = (product or {}).get("name") or resolved_article
    resolved_size = (product or {}).get("size") or supplied_size
    sku = SKU(
        client_id=client_id,
        sku_code=f"WB-{barcode}",
        article=resolved_article,
        name=resolved_name,
        size=resolved_size,
        barcode=barcode,
        unit="PCS",
        is_active=True,
    )
    db.add(sku)
    db.flush()
    return sku

def _warehouse_qty(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID, sku_id: uuid.UUID) -> Decimal:
    value = db.execute(select(func.coalesce(func.sum(InventoryBalance.quantity), 0)).where(
        InventoryBalance.client_id == client_id,
        InventoryBalance.warehouse_id == warehouse_id,
        InventoryBalance.sku_id == sku_id,
    )).scalar_one()
    return Decimal(value or 0)

def _warehouse_barcode_qty(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID, barcode: str) -> Decimal:
    """Count every legacy SKU row for one barcode when publishing an absolute WB stock."""
    value = db.execute(
        select(func.coalesce(func.sum(InventoryBalance.quantity), 0))
        .join(SKU, SKU.id == InventoryBalance.sku_id)
        .where(
            InventoryBalance.client_id == client_id,
            InventoryBalance.warehouse_id == warehouse_id,
            SKU.client_id == client_id,
            SKU.barcode == barcode,
        )
    ).scalar_one()
    return Decimal(value or 0)


@router.get("/product-preview")
def receiving_product_preview(
    client_id: uuid.UUID,
    barcode: str = Query(..., min_length=1, max_length=255),
    db: Session = Depends(get_db),
):
    """Resolve a scanned barcode without writing stock or creating an SKU."""
    normalized, duplicated_from = normalize_product_barcode(barcode)
    if not normalized:
        raise HTTPException(status_code=400, detail="Пустой ШК товара")
    sku = db.execute(
        select(SKU).where(SKU.client_id == client_id, SKU.barcode == normalized)
    ).scalars().first()
    product = wb_product_identity(db, client_id, normalized)
    identity = resolved_sku_identity(db, client_id, sku) if sku else {
        "sku_id": None,
        "sku_code": None,
        "raw_barcode": normalized,
        "barcode": normalized,
        "barcode_was_duplicated": False,
        "article": (product or {}).get("article"),
        "name": (product or {}).get("name"),
        "size": (product or {}).get("size"),
        "brand": (product or {}).get("brand"),
        "product_id": (product or {}).get("product_id"),
        "nm_id": (product or {}).get("nm_id"),
    }
    identity.update({
        "found": bool(sku or product),
        # A placeholder SKU may exist after local-only acceptance. Keep WB card
        # availability separate so the receiving draft can continue to mark the
        # line as unresolved until the marketplace card really appears.
        "wb_found": bool(product),
        "barcode": normalized,
        "duplicated_from": duplicated_from,
    })
    return identity


def _ensure_inventory_bulk_actions_table(db: Session) -> None:
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS fc_inventory_bulk_actions (
            id uuid PRIMARY KEY,
            client_id uuid NOT NULL,
            warehouse_id uuid NOT NULL,
            action_type varchar(32) NOT NULL,
            payload jsonb NOT NULL,
            status varchar(16) NOT NULL DEFAULT 'ACTIVE',
            created_at timestamptz NOT NULL DEFAULT NOW(),
            undone_at timestamptz NULL
        )
    """))
    db.execute(text("""
        CREATE INDEX IF NOT EXISTS ix_fc_inventory_bulk_actions_context
        ON fc_inventory_bulk_actions(client_id, warehouse_id, status, created_at DESC)
    """))
    db.commit()


def _inventory_bulk_record(db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, action_type: str, payload: dict) -> uuid.UUID:
    _ensure_inventory_bulk_actions_table(db)
    action_id = uuid.uuid4()
    db.execute(text("""
        INSERT INTO fc_inventory_bulk_actions(id, client_id, warehouse_id, action_type, payload, status, created_at)
        VALUES (:id, :client_id, :warehouse_id, :action_type, CAST(:payload AS jsonb), 'ACTIVE', NOW())
    """), {
        "id": str(action_id), "client_id": str(client_id), "warehouse_id": str(warehouse_id),
        "action_type": action_type, "payload": json.dumps(payload, ensure_ascii=False),
    })
    db.commit()
    return action_id


def _inventory_bulk_last(db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID):
    _ensure_inventory_bulk_actions_table(db)
    return db.execute(text("""
        SELECT id, action_type, payload, status, created_at
        FROM fc_inventory_bulk_actions
        WHERE client_id=:client_id AND warehouse_id=:warehouse_id AND status='ACTIVE'
        ORDER BY created_at DESC
        LIMIT 1
    """), {"client_id": str(client_id), "warehouse_id": str(warehouse_id)}).mappings().first()



def _wake_wb_stock_outbox() -> None:
    try:
        from app.services.stock_outbox_worker import wake_stock_outbox_worker
        wake_stock_outbox_worker()
    except Exception:
        pass


def _wb_internal_product_codes(db: Session, *, client_id: uuid.UUID, codes: list[str] | set[str]) -> set[str]:
    """Return technical box/location codes that must never be sent to WB stock API."""
    wanted = {normalize_product_barcode(code)[0] for code in codes if str(code or "").strip()}
    blocked = {code for code in wanted if code.upper().startswith(("BOX_", "FCBOX_", "FCLOC-"))}
    if not wanted:
        return blocked
    try:
        with db.begin_nested():
            values = db.execute(select(Container.barcode).where(
                Container.client_id == client_id, Container.barcode.in_(list(wanted))
            )).scalars().all()
        blocked.update(str(value or "").strip() for value in values if str(value or "").strip())
    except Exception:
        pass
    return blocked


def _supersede_wb_scope_rows(
    db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, codes: set[str], reason: str
) -> int:
    """Cancel older sibling-SKU commands before a new operator truth is queued.

    Existing DB status values are preserved for compatibility; API/UI project the
    SUPERSEDED marker as a dedicated non-error state.
    """
    normalized = sorted({normalize_product_barcode(code)[0] for code in codes if str(code or "").strip()})
    if not normalized:
        return 0
    stmt = text("""
        UPDATE fc_marketplace_stock_outbox_v2
        SET status='FAILED',
            last_error=:error,
            next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '3650 days'
        WHERE CAST(client_id AS varchar)=:client
          AND CAST(warehouse_id AS varchar)=:warehouse
          AND provider='WB' AND product_key IN :codes
          AND status IN ('PENDING','FAILED','PROCESSING')
    """).bindparams(bindparam('codes', expanding=True))
    result = db.execute(stmt, {
        'client': str(client_id), 'warehouse': str(warehouse_id), 'codes': normalized,
        'error': ('SUPERSEDED: ' + str(reason or 'newer WB command'))[:900],
    })
    return int(result.rowcount or 0)


def _enqueue_wb_absolute_target(
    db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, sku: SKU,
    credential_id: uuid.UUID, external_warehouse_id: int, barcode: str, desired: int, reason: str,
) -> int:
    """Queue an already-computed chrt-level absolute target without re-deriving per-SKU free stock."""
    code = normalize_product_barcode(barcode)[0]
    target = max(0, int(desired))
    if not code:
        raise RuntimeError('У SKU нет штрихкода для публикации WB')
    db.execute(text("""
        INSERT INTO fc_marketplace_stock_outbox_v2(
            id,client_id,warehouse_id,sku_id,provider,credential_id,
            external_warehouse_id,product_key,desired_amount,reason,dedupe_key
        ) VALUES(:id,:client,:warehouse,:sku,'WB',:credential,:external_wh,:product_key,:desired,:reason,:dedupe)
        ON CONFLICT(dedupe_key) DO UPDATE SET
            credential_id=EXCLUDED.credential_id,external_warehouse_id=EXCLUDED.external_warehouse_id,
            product_key=EXCLUDED.product_key,desired_amount=EXCLUDED.desired_amount,reason=EXCLUDED.reason,
            status='PENDING',attempts=0,next_attempt_at=CURRENT_TIMESTAMP,last_error=NULL,updated_at=CURRENT_TIMESTAMP
    """), {
        'id': str(uuid.uuid4()), 'client': str(client_id), 'warehouse': str(warehouse_id),
        'sku': str(sku.id), 'credential': str(credential_id), 'external_wh': str(int(external_warehouse_id)),
        'product_key': code, 'desired': target, 'reason': str(reason or 'INVENTORY_CONTROL')[:80],
        'dedupe': f'WB:{client_id}:{warehouse_id}:{sku.id}',
    })
    return target


def _wb_bulk_context(db: Session, *, client_id: uuid.UUID, physical_warehouse_id: uuid.UUID, requested_wb_id: int | None):
    cred = _active_wb_credential(db, client_id)
    if not cred:
        raise HTTPException(status_code=409, detail="Подключение WB для выбранного клиента не настроено")
    warehouse_id = _work_wb_warehouse_id(cred, requested_wb_id, physical_warehouse_id)
    if not warehouse_id:
        raise HTTPException(status_code=409, detail="Для выбранного физического склада не назначен рабочий склад WB")
    client = WBClient(decrypt_secret(cred.encrypted_api_key))
    _wb_validate_seller_warehouse(client, int(warehouse_id), cred=cred, physical_warehouse_id=physical_warehouse_id)
    return client, int(warehouse_id)


def _wb_get_stocks_bulk(
    db: Session,
    *,
    client_id: uuid.UUID,
    client: WBClient,
    warehouse_id: int,
    barcodes: list[str],
) -> dict[str, int]:
    codes = [normalize_product_barcode(code)[0] for code in barcodes]
    chrt_map = _wb_chrt_map(db, client_id=client_id, barcodes=codes)
    missing = [code for code in codes if code and code not in chrt_map]
    if missing:
        sample = ", ".join(missing[:15])
        raise RuntimeError(f"Для ШК не найден chrtId в карточках WB: {sample}")
    observed = _wb_read_chrt_stocks(client, warehouse_id, list(dict.fromkeys(chrt_map.values())))
    return {code: int(observed.get(chrt_map[code], 0)) for code in codes if code}


def _wb_set_stocks_bulk(
    db: Session,
    *,
    physical_warehouse_id: uuid.UUID,
    client_id: uuid.UUID,
    client: WBClient,
    warehouse_id: int,
    amounts: dict[str, int],
    release_read_tx: bool = False,
    chrt_map_override: dict[str, int] | None = None,
) -> None:
    # Only the outbox opts in: its session has no warehouse writes to commit.
    if release_read_tx and (db.new or db.dirty or db.deleted):
        raise RuntimeError("Outbox session contains pending changes before WB I/O")
    normalized = {normalize_product_barcode(code)[0]: max(0, int(amount)) for code, amount in amounts.items()}
    # Recheck durable sale policy at the last DB boundary before network I/O.
    from app.services.wb_sales_control import global_paused, paused_ids
    if any(normalized.values()):
        identities=db.execute(select(SKU.id,SKU.barcode).where(SKU.client_id==client_id,SKU.barcode.in_(list(normalized)))).all()
        stopped=paused_ids(db,client_id,physical_warehouse_id,[x.id for x in identities])
        allowed={str(x.barcode) for x in identities if str(x.id) not in stopped}
        for row in identities:
            if str(row.id) in stopped:normalized[str(row.barcode)]=0
        if global_paused(db,client_id,physical_warehouse_id):
            normalized={code:amount if code in allowed else 0 for code,amount in normalized.items()}
    chrt_map = _wb_chrt_map(db, client_id=client_id, barcodes=list(normalized))
    # FC_K9R32_REUSE_FRESH_CHRT_MAP: the worker may already have recovered the complete
    # current WB size catalogue in one request. Reuse that exact map here instead of
    # issuing one content-card lookup per uncached seller barcode.
    for code, raw_chrt in (chrt_map_override or {}).items():
        normalized_code = normalize_product_barcode(code)[0]
        try:
            chrt_map[normalized_code] = int(raw_chrt)
        except (TypeError, ValueError):
            continue
    if release_read_tx:
        db.rollback()  # release PostgreSQL before fresh-card lookups, PUT and verification

    missing = [code for code in normalized if code and code not in chrt_map]
    if missing:
        for code in list(missing):
            try:
                chrt_map[code] = _wb_fresh_chrt_id(client, code)
            except Exception:
                pass
        missing = [code for code in normalized if code and code not in chrt_map]
    if missing:
        sample = ", ".join(missing[:15])
        raise RuntimeError(f"ШК не найдены в актуальных карточках WB: {sample}")
    chrt_amounts: dict[int, int] = {}
    for code, amount in normalized.items():
        chrt_id = chrt_map[code]
        existing = chrt_amounts.get(chrt_id)
        if existing is not None and existing != amount:
            raise RuntimeError(f"Несколько ШК одного chrtId {chrt_id} имеют разные локальные остатки")
        chrt_amounts[chrt_id] = amount
    observed=_wb_write_and_verify_chrt_stocks(client, warehouse_id, chrt_amounts)
    return {code:int(observed[chrt_map[code]]) for code in normalized}


# FC_K9R60_DIRECT_WB_ZERO: operator PAUSE is a foreground safety command, not merely a
# background wish. Local pause truth is committed first; then the exact WB size/chrtId
# scope is written to zero and read-after-write verified without holding a warehouse DB
# transaction. If WB is temporarily unavailable the durable outbox remains PENDING and
# retries automatically, so this fast path can never make the control less reliable.
def _inventory_pause_zero_direct(
    db: Session,
    *,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    wb_warehouse_id: int,
    wb_token: str,
    scope_codes: set[str],
    scope_sku_ids: list[str],
) -> dict[str, Any]:
    codes = sorted({normalize_product_barcode(code)[0] for code in scope_codes if str(code or '').strip()})
    if not codes:
        return {'confirmed': True, 'codes': [], 'error': None}
    client = WBClient(wb_token)
    lock_key = f'wb-stock-publish:{client_id}:{warehouse_id}:{int(wb_warehouse_id)}'
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with distributed_lock(db, lock_key, blocking=False) as got_lock:
                if not got_lock:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    time.sleep(0.15)
                    continue
                _wb_set_stocks_bulk(
                    db, physical_warehouse_id=warehouse_id, client_id=client_id, client=client, warehouse_id=int(wb_warehouse_id),
                    amounts={code: 0 for code in codes}, release_read_tx=True,
                )
                still_paused = True
                clean_sku_ids = sorted({str(x) for x in scope_sku_ids if str(x or '').strip()})
                if clean_sku_ids:
                    pause_stmt = text('''
                        SELECT COUNT(*) FROM fc_wb_sales_pauses
                        WHERE CAST(client_id AS varchar)=:client
                          AND CAST(warehouse_id AS varchar)=:warehouse
                          AND paused=TRUE AND CAST(sku_id AS varchar) IN :ids
                    ''').bindparams(bindparam('ids', expanding=True))
                    active_pause_count = int(db.execute(pause_stmt, {
                        'client': str(client_id), 'warehouse': str(warehouse_id), 'ids': clean_sku_ids,
                    }).scalar() or 0)
                    still_paused = active_pause_count == len(clean_sku_ids)
                db.execute(text('''
                    UPDATE fc_marketplace_stock_outbox_v2
                    SET desired_amount=0,status='DONE',attempts=0,next_attempt_at=CURRENT_TIMESTAMP,
                        last_error=NULL,reason='PAUSE_WB_DIRECT_CONFIRMED'
                    WHERE CAST(client_id AS varchar)=:client
                      AND CAST(warehouse_id AS varchar)=:warehouse
                      AND provider='WB' AND product_key IN :codes
                      AND upper(COALESCE(reason,'')) IN ('PAUSE_WB','DELETE_FREE_AND_PAUSE','PAUSE_WB_DIRECT_CONFIRMED')
                      AND COALESCE(last_error,'') NOT LIKE 'SUPERSEDED:%'
                ''').bindparams(bindparam('codes', expanding=True)), {
                    'client': str(client_id), 'warehouse': str(warehouse_id), 'codes': codes,
                })
                db.execute(text('''
                    INSERT INTO fc_wb_stock_publications(
                        client_id,warehouse_id,barcode,wb_warehouse_id,published_amount,status,last_error,updated_at
                    ) VALUES(:client,:warehouse,:barcode,:wb,0,'PUBLISHED',NULL,NOW())
                    ON CONFLICT(client_id,warehouse_id,barcode) DO UPDATE SET
                        wb_warehouse_id=EXCLUDED.wb_warehouse_id,published_amount=0,status='PUBLISHED',
                        last_error=NULL,updated_at=NOW()
                '''), [
                    {'client': str(client_id), 'warehouse': str(warehouse_id), 'barcode': code, 'wb': int(wb_warehouse_id)}
                    for code in codes
                ])
                db.commit()
                if not still_paused:
                    return {
                        'confirmed': False, 'codes': codes,
                        'error': 'A newer resume command replaced this stop-sales generation; zero was verified but the newer command remains queued',
                    }
                return {'confirmed': True, 'codes': codes, 'error': None}
        return {
            'confirmed': False, 'codes': codes,
            'error': 'WB publish scope is busy; durable zero queue retained for automatic retry',
        }
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        return {'confirmed': False, 'codes': codes, 'error': str(exc)[:900]}
    finally:
        try:
            client.close()
        except Exception:
            pass


# FC_K9R78_FBS_FORCE_REMOVE: clear exactly selected FBS stock without touching MAIN.
def _r78_fbs_scope_skus(
    db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID,
    barcodes: list[str], all_inventory: bool,
) -> list[SKU]:
    if not all_inventory:
        if not barcodes:
            return []
        # r87: the inventory screen is canonical/alias-aware. One visible WB row may
        # aggregate several historical local SKU rows with the same exact barcode, and
        # a WB size may expose more than one seller barcode. The selected-delete path
        # must therefore expand the matched local SKU rows to the same canonical stock
        # identity used by /inventory/catalog; otherwise the UI can show FBS stock while
        # the delete endpoint locks a zero-quantity alias and appears to do nothing.
        seeds = db.execute(select(SKU).where(
            SKU.client_id == client_id, SKU.barcode.in_(barcodes)
        ).order_by(SKU.barcode, SKU.created_at)).scalars().all()
        expanded_ids: set[uuid.UUID] = set()
        for sku in seeds:
            for value in canonical_stock_identity_sku_ids(db, client_id=client_id, sku_id=sku.id):
                try:
                    expanded_ids.add(uuid.UUID(str(value)))
                except (TypeError, ValueError):
                    pass
        if not expanded_ids:
            return seeds
        return db.execute(select(SKU).where(
            SKU.client_id == client_id, SKU.id.in_(sorted(expanded_ids, key=str))
        ).order_by(SKU.barcode, SKU.created_at)).scalars().all()
    ids = db.execute(text('''
        SELECT DISTINCT CAST(b.sku_id AS varchar)
        FROM inventory_balances b
        LEFT JOIN fc_stocks_v2 sv
          ON CAST(sv.client_id AS varchar)=CAST(b.client_id AS varchar)
         AND CAST(sv.warehouse_id AS varchar)=CAST(b.warehouse_id AS varchar)
         AND CAST(sv.sku_id AS varchar)=CAST(b.sku_id AS varchar)
         AND CAST(sv.location_id AS varchar)=CAST(b.location_id AS varchar)
         AND sv.container_key=COALESCE(CAST(b.container_id AS varchar),'')
        WHERE b.client_id=:client AND b.warehouse_id=:warehouse AND b.quantity>0
          AND b.quantity>GREATEST(0,COALESCE(sv.storage_only,0))
    '''), {'client': str(client_id), 'warehouse': str(warehouse_id)}).scalars().all()
    uuids=[]
    for value in ids:
        try:
            uuids.append(uuid.UUID(str(value)))
        except (TypeError, ValueError):
            pass
    if not uuids:
        return []
    return db.execute(select(SKU).where(
        SKU.client_id == client_id, SKU.id.in_(uuids)
    ).order_by(SKU.barcode, SKU.created_at)).scalars().all()


def _r78_fbs_bucket_snapshot(db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, sku_ids: list[uuid.UUID]) -> list[dict]:
    if not sku_ids:
        return []
    stmt=text('''
        SELECT CAST(b.sku_id AS varchar) AS sku_id, CAST(b.location_id AS varchar) AS location_id,
               COALESCE(CAST(b.container_id AS varchar),'') AS container_id,
               b.quantity AS physical, COALESCE(sv.storage_only,0) AS storage_only,
               s.barcode,s.article,s.size,l.code AS location_code,
               COALESCE(c.name,c.barcode,'') AS container_code
        FROM inventory_balances b
        JOIN skus s ON s.id=b.sku_id
        JOIN locations l ON l.id=b.location_id
        LEFT JOIN containers c ON c.id=b.container_id
        LEFT JOIN fc_stocks_v2 sv
          ON CAST(sv.client_id AS varchar)=CAST(b.client_id AS varchar)
         AND CAST(sv.warehouse_id AS varchar)=CAST(b.warehouse_id AS varchar)
         AND CAST(sv.sku_id AS varchar)=CAST(b.sku_id AS varchar)
         AND CAST(sv.location_id AS varchar)=CAST(b.location_id AS varchar)
         AND sv.container_key=COALESCE(CAST(b.container_id AS varchar),'')
        WHERE b.client_id=:client AND b.warehouse_id=:warehouse AND b.quantity>0
          AND b.sku_id IN :ids
        ORDER BY l.code,COALESCE(c.name,c.barcode,''),s.article,s.size,s.barcode
    ''').bindparams(bindparam('ids', expanding=True))
    rows=[]
    for row in db.execute(stmt, {'client':str(client_id),'warehouse':str(warehouse_id),'ids':sku_ids}).mappings():
        physical=max(Decimal(0),Decimal(str(row.get('physical') or 0)))
        storage=min(physical,max(Decimal(0),Decimal(str(row.get('storage_only') or 0))))
        fbs=max(Decimal(0),physical-storage)
        if fbs<=0:
            continue
        rows.append({**dict(row),'physical':physical,'storage_only':storage,'fbs_quantity':fbs})
    return rows


# FC_K9R79_FBS_CLEAN_RESET: write the stock-classification mirror explicitly.
# A destructive FBS reset must never use generic negative-delta reconciliation because
# that routine intentionally consumes STORAGE_ONLY first. Here the caller already knows
# the exact physical/storage split and can preserve MAIN deterministically.
# FC_R145F42_FAST_FBS_RESET: one-statement mirror writes and batched refresh.
def _r145f42_mirror_sql():
    return text('''INSERT INTO fc_stocks_v2(client_id,warehouse_id,sku_id,location_id,container_key,
        fbs_available,storage_only,reserved,physical_snapshot)
      VALUES(:client,:warehouse,:sku,:location,:container,:fbs,:storage,:reserved,:physical)
      ON CONFLICT(client_id,warehouse_id,sku_id,location_id,container_key) DO UPDATE SET
        fbs_available=EXCLUDED.fbs_available,storage_only=EXCLUDED.storage_only,reserved=EXCLUDED.reserved,
        physical_snapshot=EXCLUDED.physical_snapshot,version=fc_stocks_v2.version+1,updated_at=CURRENT_TIMESTAMP''')


def _r79_write_stock_mirror(
    db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID,
    sku_id: uuid.UUID, location_id: uuid.UUID, container_id: uuid.UUID | None,
    physical: Decimal, storage_only: Decimal, fbs_available: Decimal = Decimal(0),
    reserved: Decimal = Decimal(0),
) -> None:
    physical=max(Decimal(0),Decimal(str(physical or 0)))
    storage_only=min(physical,max(Decimal(0),Decimal(str(storage_only or 0))))
    reserved=min(max(Decimal(0),Decimal(str(reserved or 0))),max(Decimal(0),physical-storage_only))
    fbs_available=min(max(Decimal(0),Decimal(str(fbs_available or 0))),max(Decimal(0),physical-storage_only-reserved))
    db.execute(_r145f42_mirror_sql(),{
        'client':str(client_id),'warehouse':str(warehouse_id),'sku':str(sku_id),
        'location':str(location_id),'container':str(container_id) if container_id else '',
        'fbs':fbs_available,'storage':storage_only,'reserved':reserved,'physical':physical,
    })


def _r145f42_write_stock_mirrors_bulk(db: Session, rows: list[dict]) -> None:
    if rows:
        db.execute(_r145f42_mirror_sql(), rows)


def _r145f42_refresh_zero_mirrors(db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, sku_ids: list[uuid.UUID]) -> None:
    ids=[str(x) for x in sku_ids]
    if not ids:return
    stmt=text('''UPDATE fc_stocks_v2 sv
      SET fbs_available=0,storage_only=GREATEST(COALESCE(b.quantity,0),0),reserved=0,
          physical_snapshot=GREATEST(COALESCE(b.quantity,0),0),version=sv.version+1,updated_at=CURRENT_TIMESTAMP
      FROM inventory_balances b
      WHERE CAST(b.client_id AS varchar)=:client AND CAST(b.warehouse_id AS varchar)=:warehouse
        AND CAST(b.sku_id AS varchar) IN :ids
        AND CAST(sv.client_id AS varchar)=CAST(b.client_id AS varchar)
        AND CAST(sv.warehouse_id AS varchar)=CAST(b.warehouse_id AS varchar)
        AND CAST(sv.sku_id AS varchar)=CAST(b.sku_id AS varchar)
        AND CAST(sv.location_id AS varchar)=CAST(b.location_id AS varchar)
        AND sv.container_key=COALESCE(CAST(b.container_id AS varchar),'')''').bindparams(bindparam('ids',expanding=True))
    db.execute(stmt,{'client':str(client_id),'warehouse':str(warehouse_id),'ids':ids})
    missing=text('''UPDATE fc_stocks_v2 sv
      SET fbs_available=0,storage_only=0,reserved=0,physical_snapshot=0,
          version=sv.version+1,updated_at=CURRENT_TIMESTAMP
      WHERE CAST(sv.client_id AS varchar)=:client AND CAST(sv.warehouse_id AS varchar)=:warehouse
        AND CAST(sv.sku_id AS varchar) IN :ids
        AND NOT EXISTS (SELECT 1 FROM inventory_balances b
          WHERE CAST(b.client_id AS varchar)=CAST(sv.client_id AS varchar)
            AND CAST(b.warehouse_id AS varchar)=CAST(sv.warehouse_id AS varchar)
            AND CAST(b.sku_id AS varchar)=CAST(sv.sku_id AS varchar)
            AND CAST(b.location_id AS varchar)=CAST(sv.location_id AS varchar)
            AND COALESCE(CAST(b.container_id AS varchar),'')=sv.container_key)''').bindparams(bindparam('ids',expanding=True))
    db.execute(missing,{'client':str(client_id),'warehouse':str(warehouse_id),'ids':ids})


def _r79_detach_reserves_and_holds(
    db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID,
    sku_ids: list[uuid.UUID], note_prefix: str,
) -> dict[str, Decimal | int]:
    # Detach ordinary order promises, but never cut through an in-flight write-off.
    # SALE_RESERVED/ACTIVE remain logical reservations; transient write-off states block.
    if not sku_ids:
        return {'detached_qty':Decimal(0),'detached_rows':0,'released_external_qty':Decimal(0),'released_external_rows':0}
    ids=[str(x) for x in sku_ids]
    release_stale_fbs_holds(db,client_id,warehouse_id,ids)
    reserve_stmt=text('''
        SELECT id,quantity,status,source_location_id,source_container_id
        FROM fc_stock_reservations
        WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id IN :ids
          AND status IN ('SALE_RESERVED','ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
        FOR UPDATE
    ''').bindparams(bindparam('ids', expanding=True))
    locked_rows=[dict(x) for x in db.execute(reserve_stmt,{'client':str(client_id),'warehouse':str(warehouse_id),'ids':ids}).mappings().all()]
    busy=[x for x in locked_rows if str(x.get('status') or '').upper() in {'CONSUMING','CANCELLING','PACKING_WRITE_OFF'}]
    if busy:
        statuses=', '.join(sorted({str(x.get('status') or '') for x in busy}))
        raise HTTPException(409,detail=f'Сейчас идёт упаковка/отмена по выбранному товару ({statuses}). Ничего не изменено. Повторите операцию после завершения текущего действия.')
    rows=[x for x in locked_rows if str(x.get('status') or '').upper() in {'SALE_RESERVED','ACTIVE'}]
    detached_qty=sum((max(Decimal(0),Decimal(str(row.get('quantity') or 0))) for row in rows),Decimal(0))
    detached_rows=len(rows)
    if rows:
        row_ids=[str(row['id']) for row in rows]
        db.execute(text('''
            UPDATE fc_stock_reservations
            SET source_location_id=NULL,source_container_id=NULL,updated_at=CURRENT_TIMESTAMP,
                note=CONCAT(CAST(:prefix AS TEXT),':',COALESCE(CAST(source_location_id AS TEXT),''),'/',
                            COALESCE(CAST(source_container_id AS TEXT),''),'; ',COALESCE(note,''))
            WHERE id IN :row_ids AND status IN ('SALE_RESERVED','ACTIVE')
        ''').bindparams(bindparam('row_ids',expanding=True)),{'row_ids':row_ids,'prefix':note_prefix})

    released_external_qty=Decimal(0)
    released_external_rows=0
    table_exists=db.execute(text("SELECT to_regclass('fc_external_pick_allocations')")).scalar()
    if table_exists:
        ext_stmt=text('''
            SELECT id,quantity FROM fc_external_pick_allocations
            WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id IN :ids AND status='ACTIVE'
            FOR UPDATE
        ''').bindparams(bindparam('ids', expanding=True))
        ext=[dict(x) for x in db.execute(ext_stmt,{'client':str(client_id),'warehouse':str(warehouse_id),'ids':ids}).mappings().all()]
        if ext:
            released_external_qty=sum((max(Decimal(0),Decimal(str(x.get('quantity') or 0))) for x in ext),Decimal(0))
            released_external_rows=len(ext)
            ext_ids=[str(x['id']) for x in ext]
            db.execute(text('''
                UPDATE fc_external_pick_allocations
                SET status='RESET_RELEASED'
                WHERE id IN :ids AND status='ACTIVE'
            ''').bindparams(bindparam('ids', expanding=True)),{'ids':ext_ids})
    return {
        'detached_qty':detached_qty,'detached_rows':detached_rows,
        'released_external_qty':released_external_qty,'released_external_rows':released_external_rows,
    }


def _r88_error_text(exc: Exception) -> str:
    """Human-readable transactional error; never collapse an empty detail list to just ``[]``."""
    detail=getattr(exc,'detail',None)
    if detail not in (None,'',[],{}):
        if isinstance(detail,(dict,list,tuple)):
            try:return json.dumps(detail,ensure_ascii=False,default=str)
            except Exception:pass
        raw=str(detail).strip()
        if raw:return raw
    raw=str(exc).strip()
    return raw or exc.__class__.__name__


def _r88_route_map(db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, container_ids) -> dict[str, dict]:
    """Current pallet-row metadata for boxes used by the FBS control/picking sheet."""
    ids=sorted({str(x) for x in container_ids or [] if str(x or '').strip()})
    if not ids:return {}
    try:
        with db.begin_nested():
            if not db.execute(text("SELECT to_regclass('public.fc_box_routes')")).scalar():return {}
            stmt=text('''SELECT container_id,row_on_pallet,row_position FROM fc_box_routes
                WHERE client_id=:c AND warehouse_id=:w AND container_id IN :ids''').bindparams(bindparam('ids',expanding=True))
            return {str(r['container_id']):dict(r) for r in db.execute(stmt,{
                'c':str(client_id),'w':str(warehouse_id),'ids':ids,
            }).mappings().all()}
    except Exception:
        # Row is useful route metadata, never a reason to block a stock reset.
        return {}


def _r88_fbs_reset_operations(
    db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, performed_by: str | None, prepared: list[tuple],
) -> list[dict]:
    """Write a large FBS reset in bounded ledger operations, still inside one outer transaction.

    Production warehouses can have thousands of address buckets behind a few hundred visible
    products.  Sending every bucket through one ``execute_operation`` call made the whole reset
    depend on an oversized aggregate validation path.  r88 uses bounded savepoint batches and,
    if one aggregate batch is rejected, bisects it until either the exact bucket is identified or
    every line is posted.  The caller still owns the outer transaction, so any terminal failure
    rolls all posted batches back together.
    """
    MAX_LINES=120
    if not prepared:return []

    def post(part: list[tuple], label: str) -> list[dict]:
        lines=[StockLine(
            sku_id=current.sku_id,quantity=fbs,
            source_location_id=current.location_id,source_container_id=current.container_id,
            reason='FBS_CLEAN_RESET_TO_ZERO',
        ) for current,_storage,fbs in part]
        reference=f"FBS-CLEAN-RESET-{uuid.uuid4()}"
        try:
            with db.begin_nested():
                operation=execute_operation(db,OperationCreate(
                    client_id=client_id,warehouse_id=warehouse_id,
                    operation_type='ADJUSTMENT',idempotency_key=str(uuid.uuid4()),
                    performed_by=performed_by,reference=reference,
                    comment='Полное обнуление выбранного WB FBS. MAIN сохранён; действующие резервы заказов оставлены логически.',
                    lines=lines,
                ),commit=False)
                db.flush()
            return [{'id':str(getattr(operation,'id','') or ''),'part':part,'reference':reference}]
        except Exception as exc:
            if len(part)>1:
                mid=max(1,len(part)//2)
                return post(part[:mid],label+'A')+post(part[mid:],label+'B')
            current,_storage,fbs=part[0]
            raise RuntimeError(
                f"физическое списание FBS, пакет {label}: SKU {current.sku_id}, "
                f"ячейка {current.location_id}, короб {current.container_id or '-'}, количество {fbs}: {_r88_error_text(exc)}"
            ) from exc

    result=[]
    for ix in range(0,len(prepared),MAX_LINES):
        result.extend(post(prepared[ix:ix+MAX_LINES],str(ix//MAX_LINES+1)))
    return result


@router.post('/inventory-bulk/fbs-remove-plan.xlsx')
def fbs_remove_plan(payload: FbsRemovalPlanRequest, db: Session = Depends(get_db)):
    scope_codes=[]
    for raw in payload.barcodes:
        code=normalize_product_barcode(raw)[0]
        if code and code not in scope_codes:
            scope_codes.append(code)
    if not payload.all_inventory and not scope_codes:
        raise HTTPException(status_code=400, detail='Отметьте хотя бы один товар')
    skus=_r78_fbs_scope_skus(db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,barcodes=scope_codes,all_inventory=payload.all_inventory)
    if not skus:
        raise HTTPException(status_code=409, detail='В выбранной области нет физического остатка WB FBS')
    sku_ids=[x.id for x in skus]
    buckets=_r78_fbs_bucket_snapshot(db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,sku_ids=sku_ids)
    if not buckets:
        raise HTTPException(status_code=409, detail='В выбранной области уже нет физического остатка WB FBS')

    reservation_stmt=text('''
        SELECT r.id,r.order_id,CAST(r.sku_id AS varchar) AS sku_id,r.quantity,r.status,r.note,
               CAST(r.source_location_id AS varchar) AS source_location_id,
               COALESCE(CAST(r.source_container_id AS varchar),'') AS source_container_id,
               COALESCE(o.local_status,'') AS order_status,
               COALESCE(CAST(o.external_order_num AS varchar),o.external_order_id,'') AS order_number,
               s.barcode,s.article,s.size,COALESCE(l.code,'') AS location_code,
               COALESCE(c.name,c.barcode,'') AS container_code
        FROM fc_stock_reservations r
        JOIN skus s ON CAST(s.id AS varchar)=r.sku_id
        LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
        LEFT JOIN locations l ON CAST(l.id AS varchar)=r.source_location_id
        LEFT JOIN containers c ON CAST(c.id AS varchar)=r.source_container_id
        WHERE r.client_id=:client AND r.warehouse_id=:warehouse AND r.sku_id IN :ids
          AND r.status IN ('SALE_RESERVED','ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
        ORDER BY COALESCE(l.code,''),COALESCE(c.name,c.barcode,''),s.article,s.size,order_number
    ''').bindparams(bindparam('ids', expanding=True))
    reservations=[dict(x) for x in db.execute(reservation_stmt,{
        'client':str(payload.client_id),'warehouse':str(payload.warehouse_id),'ids':[str(x) for x in sku_ids]
    }).mappings().all()]
    route_map=_r88_route_map(
        db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,
        container_ids=[x.get('container_id') for x in buckets]+[x.get('source_container_id') for x in reservations],
    )

    wb=Workbook()
    ws=wb.active
    ws.title='Резерв перед изменением FBS'
    ws.append(['Заказ WB','Статус заказа','Артикул','Размер','ШК','Количество','Ячейка','Ряд','Короб','Резерв','После списания FBS'])
    for row in reservations:
        order_status=str(row.get('order_status') or '').upper()
        live_order=order_status in {'NEW','PICKING'}
        if live_order and row.get('source_location_id'):
            after='Резерв сохранится; привязка к старой ячейке будет снята'
        elif live_order:
            after='Резерв сохранится без изменения'
        else:
            after='Резерв сохранится логически; адресная привязка будет снята перед операцией'
        route=route_map.get(str(row.get('source_container_id') or ''),{})
        ws.append([
            row.get('order_number') or row.get('order_id') or '',order_status,row.get('article') or '',row.get('size') or '',row.get('barcode') or '',
            float(Decimal(str(row.get('quantity') or 0))),row.get('location_code') or '',route.get('row_on_pallet') or '',
            row.get('container_code') or '',row.get('status') or '',after,
        ])
    if not reservations:
        ws.append(['—','','','','',0,'','','','','По выбранным позициям активных резервов нет'])
    ws.freeze_panes='A2'
    ws.auto_filter.ref=f'A1:K{max(1,ws.max_row)}'

    ws2=wb.create_sheet('Текущий FBS')
    ws2.append(['Артикул','Размер','ШК','Ячейка','Ряд','Короб','FBS сейчас','Основной склад сейчас','Физически до операции'])
    for row in buckets:
        route=route_map.get(str(row.get('container_id') or ''),{})
        ws2.append([row.get('article') or '',row.get('size') or '',row.get('barcode') or '',row.get('location_code') or '',
                    route.get('row_on_pallet') or '',row.get('container_code') or '',float(row['fbs_quantity']),float(row['storage_only']),float(row['physical'])])
    ws2.freeze_panes='A2'
    ws2.auto_filter.ref=f'A1:I{max(1,ws2.max_row)}'

    info=wb.create_sheet('Памятка')
    info.append(['Контрольный лист перед изменением WB FBS'])
    info.append(['Сформирован',datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')])
    info.append(['Позиций FBS',len(skus)])
    info.append(['FBS единиц в выбранной области',float(sum((x['fbs_quantity'] for x in buckets),Decimal(0)))])
    info.append(['Резервов в листе, ед.',float(sum((Decimal(str(x.get('quantity') or 0)) for x in reservations),Decimal(0)))])
    info.append(['Важно','Файл сохраняется ДО изменения FBS. При «Обнулить FBS» выбранный FBS удаляется физически, MAIN остаётся. При «В Основной + стоп WB» физический товар остаётся и переводится в MAIN. Действующие резервы заказов не отменяются: старая адресная привязка снимается.'])
    for sheet,widths in ((ws,[22,16,20,14,22,12,18,10,22,18,56]),(ws2,[20,14,22,18,10,22,16,22,20]),(info,[28,90])):
        for i,width in enumerate(widths,1):
            sheet.column_dimensions[get_column_letter(i)].width=width
        for cell in sheet[1]:
            cell.font=Font(bold=True)
    out=io.BytesIO()
    wb.save(out)
    out.seek(0)
    stamp=datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    filename=f'FBS_control_before_change_{stamp}.xlsx'
    return StreamingResponse(out,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={
        'Content-Disposition':f'attachment; filename="{filename}"','X-FulfillCore-FBS-Positions':str(len(skus))
    })


@router.get("/inventory-bulk/progress/{progress_id}")
def inventory_bulk_progress(progress_id: str):
    row = _inventory_wb_progress_get(progress_id)
    if not row:
        raise HTTPException(status_code=404, detail="Прогресс массовой WB-операции пока не найден")
    return row


@router.post("/inventory-bulk")
def inventory_bulk_action(payload: InventoryBulkRequest, db: Session = Depends(get_db)):
    from app.services.wb_sales_control import global_paused, set_global
    if payload.action in {'PAUSE_WB','RESUME_WB','DELETE_FREE_AND_PAUSE','DELETE_FBS_KEEP_RESERVES','MOVE_FBS_TO_MAIN_AND_PAUSE'}:
        from app.services.stock_actions import locks
        locks(db,str(payload.client_id),str(payload.warehouse_id),[])
    raw_codes = [normalize_product_barcode(code)[0] for code in payload.barcodes]
    barcodes = list(dict.fromkeys(code for code in raw_codes if code))
    is_wb_publish = payload.action in {"PUBLISH_WB", "ADD_TO_WB_FBS"}
    if is_wb_publish:
        _inventory_wb_progress_set(payload.progress_id, stage="RECEIVED", percent=2, message="Команда принята. Подготавливаем выбранные товары…", requested_positions=len(barcodes))

    local_control = payload.action in {"PAUSE_WB", "RESUME_WB", "DELETE_FREE_AND_PAUSE", "DELETE_FBS_KEEP_RESERVES", "MOVE_FBS_TO_MAIN_AND_PAUSE"}
    if local_control:
        if payload.all_inventory and payload.action == "DELETE_FREE_AND_PAUSE" and str(payload.confirm_text or "").strip().upper() != "УДАЛИТЬ СВОБОДНЫЕ":
            raise HTTPException(status_code=400, detail="Для удаления всех свободных остатков требуется подтверждение «УДАЛИТЬ СВОБОДНЫЕ»")
        if payload.all_inventory and payload.action == "DELETE_FBS_KEEP_RESERVES" and str(payload.confirm_text or "").strip().upper() != "ОБНУЛИТЬ FBS":
            raise HTTPException(status_code=400, detail="Для полного обнуления WB FBS требуется подтверждение «ОБНУЛИТЬ FBS»")
        if payload.all_inventory and payload.action == "MOVE_FBS_TO_MAIN_AND_PAUSE" and str(payload.confirm_text or "").strip().upper() != "ВСЕ В ОСНОВНОЙ":
            raise HTTPException(status_code=400, detail="Для перевода всего FBS в Основной требуется подтверждение «ВСЕ В ОСНОВНОЙ»")
        if not payload.all_inventory and not barcodes:
            raise HTTPException(status_code=400, detail="Отметьте хотя бы один товар")

        # IMPORTANT: these buttons are a LOCAL control-plane action. They must never wait
        # for WB. The durable outbox publishes the absolute target in the background.
        cred = _active_wb_credential(db, payload.client_id)
        if not cred:
            raise HTTPException(status_code=409, detail="Подключение WB для выбранного клиента не настроено")
        selected_wb_id = _work_wb_warehouse_id(cred, payload.wb_warehouse_id, payload.warehouse_id)
        if not selected_wb_id:
            raise HTTPException(status_code=409, detail="Для выбранного физического склада не назначен рабочий склад WB")
        # FC_K9R60_PAUSE_CREDENTIAL_SNAPSHOT: keep only a scalar token across the local
        # commit/direct WB zero phase; never rely on an expired ORM credential object.
        from app.services.wb_sale_status import resolve
        checked_cred, checked_wb = resolve(db,payload.client_id,payload.warehouse_id)
        if int(selected_wb_id)!=checked_wb:
            raise HTTPException(409,detail='Выбранный склад WB отличается от рабочего склада этого физического склада')
        wb_token = decrypt_secret(cred.encrypted_api_key)

        sku_stmt = select(SKU).where(SKU.client_id == payload.client_id, SKU.is_active.is_(True))
        if payload.action in {'DELETE_FBS_KEEP_RESERVES','MOVE_FBS_TO_MAIN_AND_PAUSE'}:
            skus = _r78_fbs_scope_skus(
                db, client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                barcodes=barcodes, all_inventory=payload.all_inventory,
            )
            sku_stmt = None
        elif not payload.all_inventory:
            sku_stmt = sku_stmt.where(SKU.barcode.in_(barcodes))
        elif payload.action in {'PAUSE_WB','RESUME_WB'}:
            sku_stmt=select(SKU).where(SKU.client_id==payload.client_id)
            set_global(db,payload.client_id,payload.warehouse_id,payload.action=='PAUSE_WB',payload.performed_by)
            # Include saved WB cards with zero local stock as well as future local SKUs.
            cards = db.execute(select(MarketplaceProduct.sizes).where(
                MarketplaceProduct.client_id==payload.client_id, MarketplaceProduct.provider=='WB')).scalars().all()
            local_codes=set(db.execute(select(SKU.barcode).where(SKU.client_id==payload.client_id)).scalars())
            for sizes in cards:
                for size in sizes or []:
                    for code in (size.get('skus') or []) if isinstance(size,dict) else []:
                        code=str(code or '').strip()
                        if code and code not in local_codes:
                            _find_or_create_sku(db,payload.client_id,code)
                            local_codes.add(code)
            db.flush()
        if sku_stmt is not None:
            skus = db.execute(sku_stmt.order_by(SKU.barcode, SKU.created_at)).scalars().all()
        if not payload.all_inventory and payload.action not in {'DELETE_FBS_KEEP_RESERVES','MOVE_FBS_TO_MAIN_AND_PAUSE'}:
            # Stop/resume must also work for a WB card whose physical local stock is zero.
            # Create only the missing local identity row; this does not create stock.
            found_codes={str(sku.barcode or '') for sku in skus}
            for code in barcodes:
                if code in found_codes:
                    continue
                identity=wb_product_identity(db,payload.client_id,code)
                if identity:
                    sku=_find_or_create_sku(db,payload.client_id,code,article=identity.get('article'),size=identity.get('size'))
                    skus.append(sku);found_codes.add(code)
        if not skus:
            if payload.all_inventory and payload.action in {'PAUSE_WB','RESUME_WB'}:
                db.commit()
                return {'ok':True,'action':payload.action,'positions':0,'wb_targets':{},'global_paused':payload.action=='PAUSE_WB','message':'Общая настройка сохранена; пока нет товаров для публикации WB.'}
            raise HTTPException(status_code=409, detail="По выбранным ШК нет карточек WB/локальных SKU. Обновите карточки WB и повторите.")

        # FC_K9R34_OPERATOR_CHRT_CONTROL: WB stock is a chrtId/size value. A stop/resume
        # on one seller barcode therefore controls every locally known sibling barcode of
        # that size, and explicitly supersedes any older zero/upward command for siblings.
        from app.services.stock_outbox_worker import _wb_size_targets
        control_codes = {str(sku.barcode or sku.sku_code or '').strip() for sku in skus if str(sku.barcode or sku.sku_code or '').strip()}
        _targets0, control_siblings, _chrts0 = _wb_size_targets(
            db, client_id=str(payload.client_id), warehouse_id=str(payload.warehouse_id),
            external_wh=str(selected_wb_id), codes=list(control_codes), mapping_only=True,
        )
        scope_codes = set(control_codes)
        for code in list(control_codes):
            scope_codes.update(control_siblings.get(code, {code}))
        blocked_codes = _wb_internal_product_codes(db, client_id=payload.client_id, codes=scope_codes)
        scope_codes.difference_update(blocked_codes)
        control_skus = db.execute(select(SKU).where(
            SKU.client_id == payload.client_id, SKU.barcode.in_(sorted(scope_codes))
        )).scalars().all() if scope_codes else []
        if not control_skus:
            control_skus = [sku for sku in skus if str(sku.barcode or sku.sku_code or '').strip() not in blocked_codes]
        if payload.action=='RESUME_WB' and not payload.all_inventory:
            from app.services.wb_sales_control import resume_selected
            resume_selected(db,payload.client_id,payload.warehouse_id,[s.id for s in control_skus],payload.performed_by)
        paused = payload.action in {"PAUSE_WB", "DELETE_FREE_AND_PAUSE", "MOVE_FBS_TO_MAIN_AND_PAUSE"}
        # Clean FBS reset is inventory-only: it must not silently create a permanent sales pause.
        # MOVE_FBS_TO_MAIN_AND_PAUSE intentionally does both operations atomically.
        if payload.action != "DELETE_FBS_KEEP_RESERVES":
            for sku in control_skus:
                set_wb_sales_paused(
                    db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku_id=sku.id,
                    paused=paused, performed_by=payload.performed_by,
                    reason=("Перевод FBS в Основной + стоп продаж WB" if payload.action == "MOVE_FBS_TO_MAIN_AND_PAUSE" else ("Удаление свободного остатка + стоп продаж WB" if payload.action == "DELETE_FREE_AND_PAUSE" else ("Ручная остановка продаж WB" if paused else "Возобновление продаж WB"))),
                )
        _supersede_wb_scope_rows(
            db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, codes=scope_codes,
            reason=f"{payload.action} replaced older sibling/chrt command",
        )

        deleted_units = Decimal("0")
        deleted_buckets = 0
        preserved_sale = Decimal("0")
        preserved_active = Decimal("0")
        shortages = Decimal("0")
        operation_id = None
        operation_ids: list[str] = []
        fbs_reset_ops: list[dict] = []
        metadata_warnings: list[str] = []
        moved_units = Decimal("0")
        released_external_qty = Decimal("0")
        receipt_released_qty = Decimal("0")
        auto_archived_boxes = 0
        fbs_zero_verified = False
        local_fbs_committed = False

        if payload.action == "DELETE_FREE_AND_PAUSE":
            sku_ids = [sku.id for sku in skus]
            active_map = active_reserved_totals(
                db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku_ids=sku_ids
            )
            # FC_K9R37_DELETE_PRESERVES_EXTERNAL_PICK: uploaded external picking holds are
            # just as unavailable as order reservations. A "delete free" operation must
            # never physically remove units already promised to an external FBS sheet.
            external_pick_map: dict[tuple[str,str,str], Decimal] = {}
            if sku_ids:
                try:
                    ext_stmt = text("""
                        SELECT sku_id,location_id,COALESCE(container_id,'') AS container_key,COALESCE(SUM(quantity),0) AS quantity
                        FROM fc_external_pick_allocations
                        WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id IN :ids AND status='ACTIVE'
                        GROUP BY sku_id,location_id,COALESCE(container_id,'')
                    """).bindparams(bindparam('ids', expanding=True))
                    for ext in db.execute(ext_stmt, {'client':str(payload.client_id),'warehouse':str(payload.warehouse_id),'ids':[str(x) for x in sku_ids]}).mappings():
                        external_pick_map[(str(ext['sku_id']),str(ext['location_id']),str(ext['container_key'] or ''))] = max(Decimal(0), Decimal(str(ext['quantity'] or 0)))
                except Exception:
                    external_pick_map = {}
            balance_rows = db.execute(
                select(InventoryBalance).where(
                    InventoryBalance.client_id == payload.client_id,
                    InventoryBalance.warehouse_id == payload.warehouse_id,
                    InventoryBalance.sku_id.in_(sku_ids),
                    InventoryBalance.quantity > 0,
                ).order_by(InventoryBalance.sku_id, InventoryBalance.updated_at, InventoryBalance.id).with_for_update()
            ).scalars().all()
            by_sku: dict[uuid.UUID, list[InventoryBalance]] = {}
            for balance in balance_rows:
                by_sku.setdefault(balance.sku_id, []).append(balance)

            lines: list[StockLine] = []
            affected_buckets: list[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]] = []
            for sku in skus:
                buckets = by_sku.get(sku.id, [])
                physical = sum((Decimal(b.quantity or 0) for b in buckets), Decimal("0"))
                sale_qty = sale_reserved_total(
                    db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku_id=sku.id
                )
                active_qty = sum(
                    (qty for (sid, _loc, _cont), qty in active_map.items() if sid == str(sku.id)),
                    Decimal("0"),
                )
                preserved_sale += sale_qty
                preserved_active += active_qty
                external_qty = sum((qty for (sid, _loc, _cont), qty in external_pick_map.items() if sid == str(sku.id)), Decimal('0'))
                required = active_qty + sale_qty + external_qty
                if required > physical:
                    shortages += required - physical
                remaining_delete = max(Decimal("0"), physical - min(physical, required))
                if remaining_delete <= 0:
                    continue
                for balance in buckets:
                    if remaining_delete <= 0:
                        break
                    key = (str(sku.id), str(balance.location_id), str(balance.container_id or ""))
                    bucket_reserved = Decimal(active_map.get(key, Decimal("0")))
                    bucket_external = Decimal(external_pick_map.get(key, Decimal("0")))
                    bucket_free = max(Decimal("0"), Decimal(balance.quantity or 0) - bucket_reserved - bucket_external)
                    take = min(bucket_free, remaining_delete)
                    if take <= 0:
                        continue
                    lines.append(StockLine(
                        sku_id=sku.id, quantity=take,
                        source_location_id=balance.location_id, source_container_id=balance.container_id,
                        reason="DELETE_FREE_KEEP_EXISTING_ORDERS",
                    ))
                    affected_buckets.append((sku.id, balance.location_id, balance.container_id))
                    deleted_units += take
                    deleted_buckets += 1
                    remaining_delete -= take

            if lines:
                reference = f"FREE-STOCK-DELETE-{uuid.uuid4()}"
                try:
                    operation = execute_operation(db, OperationCreate(
                        client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                        operation_type="ADJUSTMENT", idempotency_key=str(uuid.uuid4()),
                        performed_by=payload.performed_by, reference=reference,
                        comment="Удаление только свободного остатка; резервы действующих WB-заказов сохранены",
                        lines=lines,
                    ), commit=False)
                    operation_id = str(getattr(operation, "id", "") or "")
                    for sku_id, location_id, container_id in affected_buckets:
                        reconcile_stock_bucket(
                            db, client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                            sku_id=sku_id, location_id=location_id, container_id=container_id,
                            preferred_bucket="FBS_AVAILABLE", lock=True,
                        )
                except Exception as exc:
                    db.rollback()
                    message = str(getattr(exc, "detail", exc))
                    raise HTTPException(status_code=409, detail=f"Свободный остаток не удалён. Резервы не изменены: {message}") from exc

        if payload.action in {"DELETE_FBS_KEEP_RESERVES", "MOVE_FBS_TO_MAIN_AND_PAUSE"}:
            sku_ids=[sku.id for sku in skus]
            mode_move_to_main=payload.action == "MOVE_FBS_TO_MAIN_AND_PAUSE"
            note_prefix='FBS_TO_MAIN' if mode_move_to_main else 'FBS_CLEAN_RESET'
            # Lock order reservations first (the same direction used by packing), then
            # physical balances. This avoids a balance->reservation lock inversion while
            # still making the reset all-or-nothing.
            try:
                reserve_result=_r79_detach_reserves_and_holds(
                    db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,
                    sku_ids=sku_ids,note_prefix=note_prefix,
                )
                detached_qty=Decimal(str(reserve_result.get('detached_qty') or 0))
                released_external_qty=Decimal(str(reserve_result.get('released_external_qty') or 0))

                locked={}
                if sku_ids:
                    for bal in db.execute(select(InventoryBalance).where(
                        InventoryBalance.client_id==payload.client_id,
                        InventoryBalance.warehouse_id==payload.warehouse_id,
                        InventoryBalance.sku_id.in_(sku_ids),
                        InventoryBalance.quantity>0,
                    ).with_for_update()).scalars().all():
                        locked[(str(bal.sku_id),str(bal.location_id),str(bal.container_id or ''))]=bal

                bucket_rows=_r78_fbs_bucket_snapshot(
                    db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,sku_ids=sku_ids,
                )
                # Canonicalize selected buckets after detaching reservations. This converts any
                # former technical RESERVED share back into the selected FBS amount while preserving
                # the pre-existing MAIN/storage share exactly.
                prepared=[]
                canonical_rows=[]
                for row in bucket_rows:
                    key=(str(row['sku_id']),str(row['location_id']),str(row.get('container_id') or ''))
                    current=locked.get(key)
                    if not current:
                        continue
                    physical=max(Decimal(0),Decimal(str(current.quantity or 0)))
                    storage=min(physical,max(Decimal(0),Decimal(str(row.get('storage_only') or 0))))
                    fbs=max(Decimal(0),physical-storage)
                    if fbs<=0:
                        continue
                    prepared.append((current,storage,fbs))
                    if not mode_move_to_main:
                        canonical_rows.append({
                            'client':str(payload.client_id),'warehouse':str(payload.warehouse_id),'sku':str(current.sku_id),
                            'location':str(current.location_id),'container':str(current.container_id) if current.container_id else '',
                            'fbs':fbs,'storage':storage,'reserved':Decimal(0),'physical':physical,
                        })
                if canonical_rows:
                    _r145f42_write_stock_mirrors_bulk(db,canonical_rows)

                moved_units=Decimal(0)
                if mode_move_to_main:
                    # Physical stock stays in the same cell/box; set all prepared mirrors to MAIN in one batch.
                    move_rows=[]
                    for current,_storage,fbs in prepared:
                        physical=max(Decimal(0),Decimal(str(current.quantity or 0)))
                        move_rows.append({
                            'client':str(payload.client_id),'warehouse':str(payload.warehouse_id),'sku':str(current.sku_id),
                            'location':str(current.location_id),'container':str(current.container_id) if current.container_id else '',
                            'fbs':Decimal(0),'storage':physical,'reserved':Decimal(0),'physical':physical,
                        })
                        moved_units += fbs
                    _r145f42_write_stock_mirrors_bulk(db,move_rows)
                    deleted_buckets += len(prepared)
                else:
                    affected=[]
                    for current,storage,fbs in prepared:
                        affected.append((current.sku_id,current.location_id,current.container_id,storage))
                        deleted_units += fbs
                        deleted_buckets += 1
                    if prepared:
                        fbs_reset_ops=_r88_fbs_reset_operations(
                            db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,
                            performed_by=payload.performed_by,prepared=prepared,
                        )
                        operation_ids=[str(x.get('id') or '') for x in fbs_reset_ops if str(x.get('id') or '')]
                        operation_id=operation_ids[0] if operation_ids else None
                    # Never call generic reconcile after this negative adjustment: it would consume
                    # STORAGE_ONLY first. Refresh every selected mirror from physical truth in two set-based SQL statements.
                    if affected:
                        _r145f42_refresh_zero_mirrors(
                            db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,sku_ids=sku_ids,
                        )

                # Hard post-condition: selected/all FBS must be exactly zero before commit.
                remaining_fbs=_r78_fbs_bucket_snapshot(
                    db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,sku_ids=sku_ids,
                )
                if remaining_fbs:
                    left=sum((Decimal(str(x.get('fbs_quantity') or 0)) for x in remaining_fbs),Decimal(0))
                    raise HTTPException(409,detail=f'Операция отменена: контроль FBS=0 не пройден, осталось {left} шт. Локальные данные не изменены.')
                fbs_zero_verified=True
                # r80: a clean FBS reset releases the current receipt->FBS allocation.
                # Destructive reset turns it into a controlled reload credit, so the same
                # receipt can be loaded again after the operator intentionally returned FBS
                # to zero. Moving FBS to MAIN only clears the current FBS allocation.
                from app.services import receipt_fbs as receipt_fbs_service
                released_buckets=[{
                    'sku_id':str(current.sku_id),'location_id':str(current.location_id),
                    'container_id':str(current.container_id) if current.container_id else None,
                    'quantity':fbs,
                } for current,_storage,fbs in prepared]

                # Receipt reload bookkeeping must never roll a successfully verified physical
                # FBS reset back to the old stock. Try the exact reset-line ledger first; if a
                # historical receipt marker is malformed, fall back to the exact buckets from
                # this request. Both paths are isolated in SAVEPOINTs and can be repaired later
                # from the durable FBS-CLEAN-RESET ledger.
                if mode_move_to_main:
                    try:
                        with db.begin_nested():
                            receipt_release=receipt_fbs_service.release_for_fbs_zero(
                                db,payload.client_id,payload.warehouse_id,sku_ids,
                                reloadable=False,reset_operation_id=None,released_buckets=released_buckets,
                            )
                            receipt_released_qty += Decimal(str(receipt_release.get('quantity') or 0))
                    except Exception as exc:
                        metadata_warnings.append('история приёмки/FBS: '+_r88_error_text(exc))
                else:
                    for op in fbs_reset_ops:
                        op_id=str(op.get('id') or '')
                        op_buckets=[{
                            'sku_id':str(current.sku_id),'location_id':str(current.location_id),
                            'container_id':str(current.container_id) if current.container_id else None,
                            'quantity':fbs,
                        } for current,_storage,fbs in (op.get('part') or [])]
                        try:
                            with db.begin_nested():
                                receipt_release=receipt_fbs_service.release_for_fbs_zero(
                                    db,payload.client_id,payload.warehouse_id,sku_ids,
                                    reloadable=True,reset_operation_id=op_id,released_buckets=op_buckets,
                                )
                                receipt_released_qty += Decimal(str(receipt_release.get('quantity') or 0))
                        except Exception as primary_exc:
                            try:
                                with db.begin_nested():
                                    receipt_release=receipt_fbs_service.release_for_fbs_zero(
                                        db,payload.client_id,payload.warehouse_id,sku_ids,
                                        reloadable=True,reset_operation_id=None,released_buckets=op_buckets,
                                    )
                                    receipt_released_qty += Decimal(str(receipt_release.get('quantity') or 0))
                            except Exception as fallback_exc:
                                metadata_warnings.append(
                                    'история приёмки/FBS: '+_r88_error_text(primary_exc)+'; резервный путь: '+_r88_error_text(fallback_exc)
                                )

                # A destructive clean reset can leave a previously stocked box physically
                # empty. Box archival is secondary metadata too: a stale legacy box row may
                # not restore the FBS quantity after the hard FBS=0 check has passed.
                auto_archived_boxes=0
                if not mode_move_to_main:
                    try:
                        with db.begin_nested():
                            cleanup=archive_consumed_empty_containers(
                                db,payload.client_id,payload.warehouse_id,
                                [current.container_id for current,_storage,_fbs in prepared if current.container_id],
                                actor=payload.performed_by,reason='FBS_CLEAN_RESET_EMPTY_BOX',
                            )
                            auto_archived_boxes=int(cleanup.get('count',0))
                    except Exception as exc:
                        metadata_warnings.append('архив пустых коробов: '+_r88_error_text(exc))
                preserved_active=detached_qty
                shortages=Decimal(0)
                # r88: once physical FBS=0 is verified, commit that warehouse truth before
                # any WB queue bookkeeping. A broken/stale WB card must never resurrect the
                # local FBS quantity by rolling the stock transaction back.
                db.commit()
                local_fbs_committed=True
            except HTTPException:
                db.rollback()
                raise
            except Exception as exc:
                db.rollback()
                message=_r88_error_text(exc)
                raise HTTPException(status_code=409,detail=f'Изменение FBS не выполнено; физический остаток не изменён: {message}') from exc
        # FC_K9R60_CONTROL_SCOPE_QUEUE_ALL_SIBLINGS: pause/resume controls a WB size/chrtId,
        # therefore every locally known sibling SKU in that live size receives the same new
        # generation. This prevents an old sibling RESUME/PUBLISH row from surviving beside
        # a newly paused barcode and later becoming the newest chrt command.
        targets: dict[str, int] = {}
        queue_skus = control_skus if control_skus else skus
        if payload.action == "DELETE_FBS_KEEP_RESERVES":
            # r127: physical/local FBS reset is the foreground contract.  One durable
            # background publication job replaces a slow per-SKU target calculation here.
            # The stock-recovery worker resolves the current WB mapping/targets after this
            # request has already returned to the operator.
            from app.services import stock_recovery
            stock_recovery.install_schema(db)
            deferred_ids=sorted({str(sku.id) for sku in queue_skus if str(sku.barcode or sku.sku_code or '').strip() not in blocked_codes})
            if deferred_ids:
                deferred_payload={
                    'client_id':str(payload.client_id),'warehouse_id':str(payload.warehouse_id),
                    'sku_ids':deferred_ids,'requested':int(selected_wb_id),
                }
                deferred_key=f"MANUAL:{deferred_payload['client_id']}:{deferred_payload['warehouse_id']}:{','.join(deferred_ids)}"
                db.execute(text('''INSERT INTO fc_stock_publish_retry(job_key,kind,payload)
                  VALUES(:key,'MANUAL',CAST(:payload AS jsonb)) ON CONFLICT(job_key) DO UPDATE
                  SET payload=EXCLUDED.payload,next_attempt_at=now(),last_error=NULL,updated_at=now()'''),
                  {'key':deferred_key,'payload':json.dumps(deferred_payload)})
            db.commit()
        else:
            for sku in queue_skus:
                code = str(sku.barcode or sku.sku_code or '').strip()
                if code in blocked_codes:
                    continue
                try:
                    if local_fbs_committed:
                        with db.begin_nested():
                            targets[str(sku.barcode or sku.sku_code or sku.id)] = enqueue_wb_stock_for_sku(
                                db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku=sku,
                                credential_id=cred.id, external_warehouse_id=selected_wb_id,
                                reason=payload.action,
                            )
                    else:
                        targets[str(sku.barcode or sku.sku_code or sku.id)] = enqueue_wb_stock_for_sku(
                            db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku=sku,
                            credential_id=cred.id, external_warehouse_id=selected_wb_id,
                            reason=payload.action,
                        )
                except Exception as exc:
                    if not local_fbs_committed:
                        raise
                    metadata_warnings.append(f"очередь WB для {code or sku.id}: {_r88_error_text(exc)}")
            db.commit()

        direct_zero = {'confirmed': False, 'codes': [], 'error': None}
        if paused and payload.action not in {"DELETE_FBS_KEEP_RESERVES","MOVE_FBS_TO_MAIN_AND_PAUSE"}:
            direct_zero = _inventory_pause_zero_direct(
                db, client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                wb_warehouse_id=int(selected_wb_id), wb_token=wb_token, scope_codes=scope_codes,
                scope_sku_ids=[str(sku.id) for sku in queue_skus],
            )
        _wake_wb_stock_outbox()
        direct_ok = bool(direct_zero.get('confirmed')) if paused else False
        if payload.action == "DELETE_FBS_KEEP_RESERVES":
            message = "FBS полностью обнулён локально: выбранный FBS удалён, MAIN не затронут. Резервы заказов сохранены логически. Приёмки, из которых этот FBS был загружен, снова доступны для повторной загрузки удалённого количества. Актуальный остаток WB поставлен в очередь без ожидания сети."
            if metadata_warnings:
                message += " Физическое удаление уже сохранено; есть служебные предупреждения: " + " | ".join(metadata_warnings[:3])
        elif payload.action == "MOVE_FBS_TO_MAIN_AND_PAUSE":
            message = "Выбранный FBS полностью переведён в Основной склад без физического списания. Продажи WB остановлены, цель WB=0 поставлена в очередь. Резервы заказов сохранены логически; при повторном переводе приёмка возьмёт товар из Основного склада."
        elif payload.action == "DELETE_FREE_AND_PAUSE":
            message = (
                "WB подтвердил остаток 0 сразу. Свободный локальный остаток удалён; резервы действующих заказов сохранены."
                if direct_ok else
                "Локальная блокировка включена, цель WB=0 сохранена в приоритетной надёжной очереди. Свободный остаток удалён; резервы действующих заказов сохранены."
            )
        elif paused:
            message = (
                "WB подтвердил остаток 0 сразу. Новые продажи остановлены; существующие заказы и резервы сохранены."
                if direct_ok else
                "Локальная блокировка включена, цель WB=0 сохранена в приоритетной надёжной очереди. Существующие заказы и резервы сохранены."
            )
        else:
            message = "Локальная блокировка снята; актуальный свободный остаток поставлен в очередь WB. Ожидается подтверждение маркетплейса."
        return {
            "ok": True, "action": payload.action, "positions": len(skus),
            "controlled_positions": len(queue_skus),
            "deleted_units": float(deleted_units), "deleted_buckets": deleted_buckets,
            "sale_reserved_kept": float(preserved_sale), "active_reserved_kept": float(preserved_active),
            "reservation_shortage": float(shortages),
            "detached_reserve_units": float(preserved_active if payload.action in {"DELETE_FBS_KEEP_RESERVES","MOVE_FBS_TO_MAIN_AND_PAUSE"} else 0),
            "released_external_hold_units": float(released_external_qty if payload.action in {"DELETE_FBS_KEEP_RESERVES","MOVE_FBS_TO_MAIN_AND_PAUSE"} else 0),
            "receipt_allocation_released_units": float(receipt_released_qty if payload.action in {"DELETE_FBS_KEEP_RESERVES","MOVE_FBS_TO_MAIN_AND_PAUSE"} else 0),
            "receipt_reload_credit_units": float(receipt_released_qty if payload.action == "DELETE_FBS_KEEP_RESERVES" else 0),
            "auto_archived_empty_boxes": int(auto_archived_boxes),
            "moved_to_main_units": float(moved_units if payload.action == "MOVE_FBS_TO_MAIN_AND_PAUSE" else 0),
            "protected_fbs_units": 0.0 if payload.action in {"DELETE_FBS_KEEP_RESERVES","MOVE_FBS_TO_MAIN_AND_PAUSE"} else float(shortages),
            "fbs_zero_verified": bool(fbs_zero_verified) if payload.action in {"DELETE_FBS_KEEP_RESERVES","MOVE_FBS_TO_MAIN_AND_PAUSE"} else False,
            "wb_targets": targets,
            "wb_warehouse_id": int(selected_wb_id), "operation_id": operation_id,
            "operation_ids": operation_ids, "metadata_warnings": metadata_warnings,
            "wb_direct_confirmed": direct_ok,
            "wb_direct_zero_codes": list(direct_zero.get('codes') or []),
            "wb_direct_error": direct_zero.get('error'),
            "message": message,
        }

    if payload.action == "DELETE_LOCAL":
        # Legacy destructive action is retained only for undo/backward compatibility.
        # The UI no longer calls it; normal warehouse operation uses DELETE_FREE_AND_PAUSE.
        if payload.all_inventory and str(payload.confirm_text or "").strip().upper() != "УДАЛИТЬ ВСЕ":
            raise HTTPException(status_code=400, detail="Для удаления всех остатков требуется подтверждение «УДАЛИТЬ ВСЕ»")
        if not payload.all_inventory and not barcodes:
            raise HTTPException(status_code=400, detail="Отметьте хотя бы один товар")

        query = (
            select(InventoryBalance, SKU)
            .join(SKU, SKU.id == InventoryBalance.sku_id)
            .where(
                InventoryBalance.client_id == payload.client_id,
                InventoryBalance.warehouse_id == payload.warehouse_id,
                InventoryBalance.quantity > 0,
                SKU.client_id == payload.client_id,
            )
        )
        if not payload.all_inventory:
            query = query.where(SKU.barcode.in_(barcodes))
        rows = db.execute(query.with_for_update()).all()
        if not rows and not payload.all_inventory:
            candidates = db.execute(
                select(InventoryBalance, SKU)
                .join(SKU, SKU.id == InventoryBalance.sku_id)
                .where(
                    InventoryBalance.client_id == payload.client_id,
                    InventoryBalance.warehouse_id == payload.warehouse_id,
                    InventoryBalance.quantity > 0,
                    SKU.client_id == payload.client_id,
                )
                .with_for_update()
            ).all()
            wanted = set(barcodes)
            rows = [
                (balance, sku) for balance, sku in candidates
                if normalize_product_barcode(str(sku.barcode or sku.sku_code or ""))[0] in wanted
            ]
        if not rows:
            raise HTTPException(status_code=409, detail="По выбранным товарам нет локальных остатков. Обновите страницу и выберите товар заново.")
        if len(rows) > 20000:
            raise HTTPException(status_code=409, detail="Слишком много складских позиций для одной операции. Удалите остатки частями.")

        snapshot = []
        lines = []
        affected_barcodes = set()
        total_units = Decimal("0")
        for balance, sku in rows:
            qty = Decimal(balance.quantity or 0)
            if qty <= 0:
                continue
            code = str(sku.barcode or sku.sku_code or "").strip()
            snapshot.append({
                "sku_id": str(sku.id), "barcode": code,
                "location_id": str(balance.location_id),
                "container_id": str(balance.container_id) if balance.container_id else None,
                "quantity": str(qty),
            })
            lines.append(StockLine(
                sku_id=sku.id, quantity=qty,
                source_location_id=balance.location_id,
                source_container_id=balance.container_id,
                reason="BULK_INVENTORY_DELETE",
            ))
            affected_barcodes.add(code)
            total_units += qty

        reference = f"BULK-DELETE-{uuid.uuid4()}"
        try:
            operation = execute_operation(db, OperationCreate(
                client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                operation_type="ADJUSTMENT", idempotency_key=str(uuid.uuid4()),
                performed_by=payload.performed_by, reference=reference,
                comment="Массовое удаление остатков из раздела Остатки",
                lines=lines,
            ))
        except Exception as exc:
            db.rollback()
            message = str(getattr(exc, "detail", exc))
            raise HTTPException(status_code=409, detail=f"Удаление не выполнено. Остатки не изменены: {message}") from exc

        cleanup = archive_consumed_empty_containers(
            db, payload.client_id, payload.warehouse_id,
            [row.get("container_id") for row in snapshot if row.get("container_id")],
            actor=payload.performed_by, reason="BULK_LOCAL_DELETE_EMPTY_BOX",
        )
        action_id = _inventory_bulk_record(
            db, client_id=payload.client_id, warehouse_id=payload.warehouse_id,
            action_type="DELETE_LOCAL",
            payload={"snapshot": snapshot, "operation_id": str(getattr(operation, "id", "")), "reference": reference,
                     "archived_empty_boxes": int(cleanup.get("count", 0))},
        )
        return {
            "ok": True, "action_id": str(action_id), "action": "DELETE_LOCAL",
            "positions": len(affected_barcodes), "buckets": len(snapshot), "units": float(total_units),
            "undo_available": True,
        }

    # FC_G5A8H_BULK_PUBLISH_ALL_SAFE
    # PUBLISH_WB is asynchronous: queue every selected/all-in-stock SKU independently.
    # One broken WB card must not roll back hundreds of valid stock targets.
    if not payload.all_inventory and not barcodes:
        raise HTTPException(status_code=400, detail="Отметьте хотя бы один товар")
    cred = _active_wb_credential(db, payload.client_id)
    if not cred:
        raise HTTPException(status_code=409, detail="Подключение WB для выбранного клиента не настроено")
    selected_wb_id = _work_wb_warehouse_id(cred, payload.wb_warehouse_id, payload.warehouse_id)
    if not selected_wb_id:
        raise HTTPException(status_code=409, detail="Для выбранного физического склада не назначен рабочий склад WB")

    if payload.all_inventory:
        skus = db.execute(
            select(SKU).where(
                SKU.client_id == payload.client_id,
                SKU.is_active.is_(True),
                SKU.id.in_(
                    select(InventoryBalance.sku_id).where(
                        InventoryBalance.client_id == payload.client_id,
                        InventoryBalance.warehouse_id == payload.warehouse_id,
                        InventoryBalance.quantity > 0,
                    )
                ),
            ).order_by(SKU.barcode, SKU.created_at)
        ).scalars().all()
    else:
        skus = db.execute(select(SKU).where(
            SKU.client_id == payload.client_id, SKU.is_active.is_(True), SKU.barcode.in_(barcodes)
        )).scalars().all()
        found_codes={str(sku.barcode or '') for sku in skus}
        for code in barcodes:
            if code in found_codes:
                continue
            identity=wb_product_identity(db,payload.client_id,code)
            if identity:
                sku=_find_or_create_sku(db,payload.client_id,code,article=identity.get('article'),size=identity.get('size'))
                skus.append(sku);found_codes.add(code)
    if not skus:
        _inventory_wb_progress_set(payload.progress_id, stage="ERROR", percent=0, message="По выбранным ШК нет карточек WB/локальных SKU")
        raise HTTPException(status_code=409, detail="По выбранным ШК нет карточек WB/локальных SKU")
    _inventory_wb_progress_set(payload.progress_id, stage="CATALOG", percent=10, message=f"Товары найдены: {len(skus)} поз. Определяем WB-размеры/chrtId…", positions=len(skus))

    # FC_K9R31_BULK_CHRT_TARGETS
    # WB inventory identity is chrtId (size), not an individual seller barcode. Compute
    # one canonical target across every sibling barcode in each WB size before queueing.
    # FC_K9R32_QUEUE_UNCACHED_WB_CODES: a stale/incomplete local MarketplaceProduct cache
    # must NOT make a real WB seller barcode disappear from mass publish. The HTTP click
    # stays network-free: unresolved codes are queued as wake-up rows and the single
    # durable worker resolves the complete live WB size before ANY stock write.
    # FC_K9R34_MANUAL_PUBLISH_IS_LATEST_TRUTH: pressing "Update WB" is an explicit
    # resume/current-truth command. It clears an older DELETE/PAUSE for the whole known
    # chrt sibling set and supersedes older sibling outbox work before new rows are queued.
    internal_codes = _wb_internal_product_codes(
        db, client_id=payload.client_id,
        codes=[str(sku.barcode or sku.sku_code or '').strip() for sku in skus],
    )
    skus = [sku for sku in skus if str(sku.barcode or sku.sku_code or '').strip() not in internal_codes]
    if not skus:
        raise HTTPException(status_code=409, detail="Выбранные значения являются внутренними ШК коробов/ячеек и не могут публиковаться в WB")
    sku_ids = [sku.id for sku in skus]
    from app.services.stock_outbox_worker import _wb_size_targets
    input_codes = [str(sku.barcode or sku.sku_code or '').strip() for sku in skus]
    _pre_targets, size_siblings, _pre_chrts = _wb_size_targets(
        db, client_id=str(payload.client_id), warehouse_id=str(payload.warehouse_id),
        external_wh=str(selected_wb_id), codes=input_codes,
    )
    pre_mapped = sum(1 for code in input_codes if _pre_chrts.get(code) is not None)
    _inventory_wb_progress_set(payload.progress_id, stage="SIZE_SCOPE", percent=18, message=f"Локально определено chrtId: {pre_mapped}/{len(input_codes)}. Собираем полный размер и снимаем старые STOP…", mapped=pre_mapped, total=len(input_codes))
    operator_scope_codes = set(input_codes)
    for code in input_codes:
        operator_scope_codes.update(size_siblings.get(code, {code}))
    operator_scope_codes.difference_update(_wb_internal_product_codes(db, client_id=payload.client_id, codes=operator_scope_codes))
    scope_skus = db.execute(select(SKU).where(
        SKU.client_id == payload.client_id, SKU.is_active.is_(True), SKU.barcode.in_(sorted(operator_scope_codes))
    )).scalars().all() if operator_scope_codes else []

    # FC_K9R35_STORAGE_TO_FBS_EXPLICIT: STORAGE_ONLY is a deliberate local classification.
    # Normal "Update WB" must never silently make it sellable.  The dedicated operator
    # action converts all STORAGE_ONLY units for the selected WB size/sibling scope into
    # FBS_AVAILABLE, without changing InventoryBalance, live reservations or order state.
    converted_storage_units = Decimal("0")
    converted_storage_buckets = 0
    if payload.action == "ADD_TO_WB_FBS":
        truth_seed = scope_skus or skus
        truth_map = stock_truth_for_skus(
            db, client_id=payload.client_id, warehouse_id=payload.warehouse_id,
            sku_ids=[x.id for x in truth_seed], wb_warehouse_id=selected_wb_id, include_movement=False,
        ) if truth_seed else {}
        identity_ids: set[str] = set()
        for scope_sku in truth_seed:
            truth = truth_map.get(str(scope_sku.id), {})
            identity_ids.update(str(x) for x in (truth.get("identity_sku_ids") or [str(scope_sku.id)]))
        balance_rows = []
        if identity_ids:
            uuid_ids = []
            for value in sorted(identity_ids):
                try:
                    uuid_ids.append(uuid.UUID(str(value)))
                except Exception:
                    pass
            if uuid_ids:
                balance_rows = db.execute(select(InventoryBalance).where(
                    InventoryBalance.client_id == payload.client_id,
                    InventoryBalance.warehouse_id == payload.warehouse_id,
                    InventoryBalance.sku_id.in_(uuid_ids), InventoryBalance.quantity > 0,
                ).order_by(InventoryBalance.sku_id, InventoryBalance.location_id, InventoryBalance.container_id)).scalars().all()
        total_buckets = len(balance_rows)
        _inventory_wb_progress_set(payload.progress_id, stage="CONVERT_STORAGE", percent=22, message=f"Переводим «Не для WB FBS» в продаваемый FBS: 0/{total_buckets} мест…", processed=0, total=total_buckets)
        for index, balance in enumerate(balance_rows, 1):
            # FC_K9R46_STORAGE_FAST_MOVE: reconcile once under row lock, then change only
            # the classification columns directly. Calling move_bucket here used to run a
            # second full reconcile for every cell and made 1000+ position jobs needlessly slow.
            state = reconcile_stock_bucket(
                db, client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                sku_id=balance.sku_id, location_id=balance.location_id, container_id=balance.container_id,
                preferred_bucket="FBS_AVAILABLE", lock=True,
            )
            storage_qty = max(Decimal("0"), Decimal(state.get("storage_only") or 0))
            if storage_qty > 0:
                params = {
                    'client': str(payload.client_id), 'warehouse': str(payload.warehouse_id),
                    'sku': str(balance.sku_id), 'location': str(balance.location_id),
                    'container': str(balance.container_id) if balance.container_id else '', 'qty': storage_qty,
                }
                db.execute(text("""
                    UPDATE fc_stocks_v2
                    SET fbs_available=fbs_available+:qty, storage_only=GREATEST(0,storage_only-:qty),
                        version=version+1, updated_at=CURRENT_TIMESTAMP
                    WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
                      AND location_id=:location AND container_key=:container
                """), params)
                converted_storage_units += storage_qty
                converted_storage_buckets += 1
            if index == total_buckets or index % 25 == 0:
                pct = 22 + int(18 * index / max(1, total_buckets))
                _inventory_wb_progress_set(payload.progress_id, stage="CONVERT_STORAGE", percent=pct, message=f"Переводим «Не для WB FBS»: {index}/{total_buckets} мест · {float(converted_storage_units):g} шт.", processed=index, total=total_buckets, converted_units=float(converted_storage_units))
        if converted_storage_units <= 0:
            db.rollback()
            raise HTTPException(status_code=409, detail="У выбранных товаров уже нет остатка в категории «Не для WB FBS». Обновите остатки и повторите.")

    _inventory_wb_progress_set(payload.progress_id, stage="TARGETS", percent=43, message="Рассчитываем остатки с учётом остановки продаж…")
    _supersede_wb_scope_rows(
        db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, codes=operator_scope_codes,
        reason="MANUAL_PUBLISH_WB is the newer chrt truth",
    )
    size_targets, _size_siblings, chrt_by_code = _wb_size_targets(
        db, client_id=str(payload.client_id), warehouse_id=str(payload.warehouse_id),
        external_wh=str(selected_wb_id), codes=input_codes,
    )
    resolved_targets = sum(1 for code in input_codes if chrt_by_code.get(code) is not None and size_targets.get(code) is not None)
    _inventory_wb_progress_set(payload.progress_id, stage="TARGETS", percent=55, message=f"Рассчитаны актуальные цели WB: {resolved_targets}/{len(input_codes)} поз. Формируем надёжную очередь…", resolved=resolved_targets, total=len(input_codes))
    from app.services.wb_sales_control import paused_ids as sale_pauses
    paused_ids = sale_pauses(db,payload.client_id,payload.warehouse_id,sku_ids)

    after: dict[str, int] = {}
    errors: list[dict] = [
        {"barcode": code, "error": "Внутренний ШК короба/ячейки исключён из очереди WB"}
        for code in sorted(internal_codes)
    ]
    mapping_pending: list[str] = []
    queue_params: list[dict] = []
    base_reason = ("ADD_TO_WB_FBS" if payload.action == "ADD_TO_WB_FBS" else ("MANUAL_PUBLISH_WB_ALL" if payload.all_inventory else "MANUAL_PUBLISH_WB"))
    for sku in skus:
        code = str(sku.barcode or sku.sku_code or "").strip()
        if not code:
            errors.append({"barcode": "", "error": f"SKU {sku.id}: нет штрихкода"})
            continue
        desired = size_targets.get(code)
        chrt_id = chrt_by_code.get(code)
        unresolved_mapping = desired is None or chrt_id is None
        if unresolved_mapping:
            # IMPORTANT: 0 is only a local queue wake-up hint. It is never sent to WB as
            # the size target. _publish_rows_with_isolation() first loads the current full
            # WB size row, recomputes the canonical chrt target, then writes that target.
            desired = 0
            mapping_pending.append(code)
            reason = "MANUAL_PUBLISH_WB_RESOLVE_CHRT"
        else:
            desired = max(0, int(desired))
            after[code] = desired
            reason = base_reason
        if str(sku.id) in paused_ids:
            desired = 0
            reason = base_reason
        queue_params.append({
            "id": str(uuid.uuid4()),
            "client": str(payload.client_id),
            "warehouse": str(payload.warehouse_id),
            "sku": str(sku.id),
            "credential": str(cred.id),
            "external_wh": str(selected_wb_id),
            "product_key": code,
            "desired": max(0, int(desired)),
            "reason": reason,
            "dedupe": f"WB:{payload.client_id}:{payload.warehouse_id}:{sku.id}",
        })

    # FC_K9R30_BULK_TX_SAFE_QUEUE_WRITE
    # Keep the fast precomputed truth from r29, but make the durable queue write resilient.
    # A driver/type issue in one executemany chunk must neither abort the whole transaction
    # nor turn one bad SKU into an Internal Server Error for all 500+ positions.
    queue_stmt = text("""
        INSERT INTO fc_marketplace_stock_outbox_v2(
            id,client_id,warehouse_id,sku_id,provider,credential_id,
            external_warehouse_id,product_key,desired_amount,reason,dedupe_key
        ) VALUES(
            :id,:client,:warehouse,:sku,'WB',:credential,
            :external_wh,:product_key,:desired,:reason,:dedupe
        )
        ON CONFLICT(dedupe_key) DO UPDATE SET
            credential_id=EXCLUDED.credential_id,
            external_warehouse_id=EXCLUDED.external_warehouse_id,
            product_key=EXCLUDED.product_key,
            desired_amount=EXCLUDED.desired_amount,
            reason=EXCLUDED.reason,status='PENDING',attempts=0,
            next_attempt_at=CURRENT_TIMESTAMP,last_error=NULL,updated_at=CURRENT_TIMESTAMP
    """)
    queued_count = 0
    chunk_size = 100
    for offset in range(0, len(queue_params), chunk_size):
        chunk = queue_params[offset:offset + chunk_size]
        pct = 60 + int(15 * min(len(queue_params), offset + len(chunk)) / max(1, len(queue_params)))
        _inventory_wb_progress_set(payload.progress_id, stage="QUEUE", percent=pct, message=f"Сохраняем команды WB: {min(len(queue_params), offset + len(chunk))}/{len(queue_params)} поз.…", queued=queued_count, total=len(queue_params))
        try:
            with db.begin_nested():
                db.execute(queue_stmt, chunk)
            queued_count += len(chunk)
            continue
        except Exception:
            # Retry only the failed chunk row-by-row under SAVEPOINTs. Healthy positions
            # continue to the durable queue and the response reports exact failed barcodes.
            pass
        for params in chunk:
            try:
                with db.begin_nested():
                    db.execute(queue_stmt, params)
                queued_count += 1
            except Exception as exc:
                errors.append({
                    "barcode": str(params.get("product_key") or ""),
                    "error": f"Очередь WB: {str(getattr(exc, 'detail', exc))[:240]}",
                })
    db.commit()
    _wake_wb_stock_outbox()
    _inventory_wb_progress_set(payload.progress_id, stage="QUEUED", percent=78, message=f"Очередь готова: {queued_count}/{len(queue_params)} поз. Worker отправляет остатки в WB пакетами…", queued=queued_count, total=len(queue_params), mapping_pending=len(mapping_pending), errors=len(errors))
    return {
        "ok": True, "action": ("ADD_TO_WB_FBS" if payload.action == "ADD_TO_WB_FBS" else "PUBLISH_WB"), "positions": queued_count,
        "requested_positions": len(skus), "errors": errors,
        "converted_storage_units": float(converted_storage_units),
        "converted_storage_buckets": int(converted_storage_buckets),
        "wb_warehouse_id": int(selected_wb_id), "targets": after,
        "mapping_pending": mapping_pending, "mapping_pending_count": len(mapping_pending),
        "queued": True, "batch_queue": True, "all_inventory": bool(payload.all_inventory), "undo_available": False,
        "message": (
            (f"В WB FBS переведено из категории «Не для WB FBS»: {float(converted_storage_units):g} шт. в {converted_storage_buckets} местах. " if payload.action == "ADD_TO_WB_FBS" else "")
            + f"В очередь WB быстро поставлено {len(queue_params)} поз.; ошибок очереди {len(errors)}; "
            + f"актуальная привязка chrtId будет восстановлена worker для {len(mapping_pending)} поз. "
            + "Отправка и подтверждение WB продолжаются в фоне и не держат браузерный запрос."
        ),
    }


@router.post("/inventory-bulk/undo-last")
def undo_last_inventory_bulk_action(payload: InventoryBulkUndoRequest, db: Session = Depends(get_db)):
    row = _inventory_bulk_last(db, client_id=payload.client_id, warehouse_id=payload.warehouse_id)
    if not row:
        raise HTTPException(status_code=404, detail="Нет массовой операции, которую можно отменить")
    action_id = str(row["id"])
    action_type = str(row["action_type"])
    data = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"] or "{}")

    if action_type == "DELETE_LOCAL":
        snapshot = data.get("snapshot") or []
        if not snapshot:
            raise HTTPException(status_code=409, detail="У операции нет снимка остатков для восстановления")
        lines = []
        restored = Decimal("0")
        for item in snapshot:
            sku_id = uuid.UUID(str(item["sku_id"]))
            location_id = uuid.UUID(str(item["location_id"]))
            container_id = uuid.UUID(str(item["container_id"])) if item.get("container_id") else None
            qty = Decimal(str(item["quantity"]))
            if qty <= 0:
                continue
            # Do not silently recreate deleted layout objects: the operator must see
            # that a rollback is no longer safe.
            if db.get(Location, location_id) is None:
                raise HTTPException(status_code=409, detail="Отмена невозможна: одна из исходных ячеек уже удалена")
            if container_id:
                restore_container = db.get(Container, container_id)
                if restore_container is None:
                    raise HTTPException(status_code=409, detail="Отмена невозможна: один из исходных коробов уже удалён")
                # r85 automatically archives a consumed empty box. Undo of the stock
                # deletion restores that same historical box instead of creating a new one.
                if str(restore_container.status or "").upper() == "ARCHIVED":
                    restore_container.status = "ACTIVE"
            lines.append(StockLine(
                sku_id=sku_id, quantity=qty,
                destination_location_id=location_id, destination_container_id=container_id,
                reason=f"UNDO_BULK_INVENTORY_DELETE:{action_id}",
            ))
            restored += qty
        execute_operation(db, OperationCreate(
            client_id=payload.client_id, warehouse_id=payload.warehouse_id,
            operation_type="ADJUSTMENT", idempotency_key=str(uuid.uuid4()),
            performed_by=payload.performed_by, reference=f"UNDO-{action_id}",
            comment="Отмена массового удаления остатков",
            lines=lines,
        ))
        result = {"restored_units": float(restored), "restored_buckets": len(lines)}
    elif action_type == "PUBLISH_WB":
        before = {str(k): max(0, int(v or 0)) for k, v in (data.get("before") or {}).items()}
        if not before:
            raise HTTPException(status_code=409, detail="У операции нет снимка WB-остатков для восстановления")
        client, selected_wb_id = _wb_bulk_context(
            db, client_id=payload.client_id, physical_warehouse_id=payload.warehouse_id,
            requested_wb_id=int(data.get("wb_warehouse_id") or payload.wb_warehouse_id or 0) or None,
        )
        try:
            _wb_set_stocks_bulk(db, physical_warehouse_id=payload.warehouse_id, client_id=payload.client_id, client=client, warehouse_id=selected_wb_id, amounts=before)
            for code, amount in before.items():
                _record_fbs_publication(
                    db, client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                    barcode=code, wb_warehouse_id=selected_wb_id, amount=amount, status="PUBLISHED",
                )
        except Exception as exc:
            db.rollback()
            raise HTTPException(status_code=502, detail=f"Не удалось вернуть предыдущие остатки WB: {exc}") from exc
        result = {"restored_wb_positions": len(before), "wb_warehouse_id": selected_wb_id}
    else:
        raise HTTPException(status_code=409, detail=f"Неизвестный тип операции {action_type}")

    db.execute(text("""
        UPDATE fc_inventory_bulk_actions
        SET status='UNDONE', undone_at=NOW()
        WHERE id=:id AND status='ACTIVE'
    """), {"id": action_id})
    db.commit()
    return {"ok": True, "action_id": action_id, "undone_action": action_type, **result}


@router.get("/fbs-status")
def fbs_stock_status(
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    _ensure_fbs_tracking_table(db)
    rows = db.execute(text("""
        SELECT barcode, wb_warehouse_id, published_amount, status, last_error, updated_at
        FROM fc_wb_stock_publications
        WHERE client_id=:client_id AND warehouse_id=:warehouse_id
        ORDER BY updated_at DESC
    """), {"client_id": str(client_id), "warehouse_id": str(warehouse_id)}).mappings().all()
    return {"rows": [dict(row) for row in rows]}


@router.post("/inventory-control-status")
def inventory_control_status(payload: InventoryControlStatusRequest, db: Session = Depends(get_db)):
    """Local, fast status of pause/resume publication commands. No WB network I/O."""
    codes=list(dict.fromkeys(normalize_product_barcode(code)[0] for code in payload.barcodes if str(code or '').strip()))
    if not codes:
        return {"rows": []}
    skus=db.execute(select(SKU).where(SKU.client_id==payload.client_id, SKU.barcode.in_(codes))).scalars().all()
    by_code={str(sku.barcode):sku for sku in skus if sku.barcode}
    sku_ids=[str(sku.id) for sku in skus]
    outbox={}
    if sku_ids:
        stmt=text("""
            SELECT sku_id,desired_amount,status,last_error,updated_at,reason,external_warehouse_id
            FROM fc_marketplace_stock_outbox_v2
            WHERE client_id=:client AND warehouse_id=:warehouse AND provider='WB' AND sku_id IN :ids
        """).bindparams(bindparam('ids', expanding=True))
        try:
            outbox={str(row['sku_id']):dict(row) for row in db.execute(stmt,{
                'client':str(payload.client_id),'warehouse':str(payload.warehouse_id),'ids':sku_ids
            }).mappings().all()}
        except Exception:
            outbox={}
    publications={}
    stmt=text("""
        SELECT barcode,wb_warehouse_id,published_amount,status,last_error,updated_at
        FROM fc_wb_stock_publications
        WHERE client_id=:client AND warehouse_id=:warehouse AND barcode IN :codes
    """).bindparams(bindparam('codes', expanding=True))
    try:
        publications={str(row['barcode']):dict(row) for row in db.execute(stmt,{
            'client':str(payload.client_id),'warehouse':str(payload.warehouse_id),'codes':codes
        }).mappings().all()}
    except Exception:
        publications={}
    # FC_K9R46_CONTROL_STATUS_BATCH: one pause lookup for the whole progress poll, not
    # one SELECT per barcode. A 1300-row progress card can now poll without hammering DB.
    from app.services.wb_sales_control import paused_ids as effective_pauses
    paused_ids=effective_pauses(db,payload.client_id,payload.warehouse_id,sku_ids)
    rows=[]
    for code in codes:
        sku=by_code.get(code)
        queue=outbox.get(str(sku.id)) if sku else None
        pub=publications.get(code)
        queue_error = str(queue.get('last_error') or '') if queue else ''
        queue_status = str(queue.get('status') or '') if queue else None
        queue_claim = None
        if queue_error.startswith('SUPERSEDED:'):
            queue_status = 'SUPERSEDED'
        # CLAIM:<token> is a lease/processing marker, never a user-facing WB error.
        if str(queue_status or '').upper() == 'PROCESSING' and queue_error.startswith('CLAIM:'):
            queue_claim = queue_error
            queue_error = ''
        rows.append({
            'barcode':code,
            'sku_id':str(sku.id) if sku else None,
            'paused':bool(sku and str(sku.id) in paused_ids),
            'desired_amount':int(queue.get('desired_amount') or 0) if queue else None,
            'queue_status':queue_status,
            'queue_error':queue_error or None,
            'queue_claim':queue_claim,
            'queue_updated_at':queue.get('updated_at') if queue else None,
            'queue_reason':queue.get('reason') if queue else None,
            'published_amount':int(pub.get('published_amount') or 0) if pub and pub.get('published_amount') is not None else None,
            'publication_status':str(pub.get('status') or '') if pub else None,
            'publication_error':pub.get('last_error') if pub else None,
            'publication_updated_at':pub.get('updated_at') if pub else None,
            'wb_warehouse_id':int((queue or {}).get('external_warehouse_id') or (pub or {}).get('wb_warehouse_id') or 0) or None,
        })
    return {'rows':rows,'checked_at':datetime.now(timezone.utc).isoformat()}


@router.post("/wb-live-stocks")
def wb_live_stocks(payload: WbLiveStocksRequest, db: Session = Depends(get_db)):
    """Read live WB stock and canonical FulfillCore target by unique chrtId.

    WB returns one amount per size/chrtId. The response still contains barcode rows for UI
    lookup, but `summary` is deduplicated by chrtId so its totals are directly comparable
    with a WB stock export and are not affected by the current Inventory table filter.
    """
    codes = list(dict.fromkeys(
        normalize_product_barcode(code)[0]
        for code in payload.barcodes if str(code or '').strip()
    ))
    if not codes:
        return {
            'rows': [], 'missing': [], 'unresolved_targets': [],
            'summary': {'chrt_count': 0, 'actual_total': 0, 'target_total': 0, 'delta_total': 0},
            'checked_at': datetime.now(timezone.utc).isoformat(),
        }
    cred = _active_wb_credential(db, payload.client_id)
    if not cred:
        raise HTTPException(status_code=409, detail='Подключение WB для выбранного клиента не настроено')
    selected_wb_id = _work_wb_warehouse_id(cred, payload.wb_warehouse_id, payload.warehouse_id)
    if not selected_wb_id:
        raise HTTPException(status_code=409, detail='Для выбранного физического склада не назначен рабочий склад WB')

    cached_map = _wb_chrt_map(db, client_id=payload.client_id, barcodes=codes)
    missing_cache = [code for code in codes if code not in cached_map]
    token = decrypt_secret(cred.encrypted_api_key)
    # Release the short catalogue read before any WB HTTP. No warehouse write exists here.
    db.rollback()
    client = WBClient(token)
    fresh_rows: list[dict] = []
    try:
        if missing_cache:
            fresh_rows = _wb_fresh_size_rows(client, missing_cache)
            _wb_runtime_size_cache_put(payload.client_id, fresh_rows)
        from app.services.stock_outbox_worker import _wb_size_targets
        size_targets, siblings, chrt_by_code = _wb_size_targets(
            db, client_id=str(payload.client_id), warehouse_id=str(payload.warehouse_id),
            external_wh=str(selected_wb_id), codes=codes, extra_size_rows=fresh_rows,
        )
        unique_ids = sorted({int(cid) for cid in chrt_by_code.values() if cid is not None})
        db.rollback()
        actual = _wb_read_chrt_stocks(client, int(selected_wb_id), unique_ids)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'Не удалось получить фактические остатки WB: {exc}') from exc
    finally:
        client.close()

    missing = [code for code in codes if chrt_by_code.get(code) is None]
    unresolved_targets = [code for code in codes if chrt_by_code.get(code) is not None and size_targets.get(code) is None]
    rows = []
    summary_by_chrt: dict[int, dict] = {}
    conflicts: list[int] = []
    for code in codes:
        chrt_id = chrt_by_code.get(code)
        if chrt_id is None:
            continue
        target = size_targets.get(code)
        amount = int(actual.get(int(chrt_id), 0))
        rows.append({
            'barcode': code, 'chrt_id': int(chrt_id), 'amount': amount,
            'target': int(target) if target is not None else None,
            'siblings': sorted(siblings.get(code, {code})),
        })
        entry = summary_by_chrt.setdefault(int(chrt_id), {
            'chrt_id': int(chrt_id), 'amount': amount, 'target': target, 'barcodes': set(),
        })
        entry['barcodes'].update(siblings.get(code, {code}))
        if target is not None:
            if entry.get('target') is not None and int(entry['target']) != int(target):
                conflicts.append(int(chrt_id))
            entry['target'] = int(target)

    unique_rows = []
    actual_total = target_total = 0
    mapped_target_count = 0
    for chrt_id, entry in sorted(summary_by_chrt.items()):
        amount = int(entry.get('amount') or 0)
        target = entry.get('target')
        actual_total += amount
        if target is not None:
            target_total += int(target)
            mapped_target_count += 1
        unique_rows.append({
            'chrt_id': int(chrt_id), 'barcodes': sorted(entry.get('barcodes') or []),
            'amount': amount, 'target': int(target) if target is not None else None,
            'delta': (int(target) - amount) if target is not None else None,
        })

    return {
        'rows': rows,
        'missing': missing,
        'unresolved_targets': unresolved_targets,
        'wb_warehouse_id': int(selected_wb_id),
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'summary': {
            'chrt_count': len(summary_by_chrt),
            'mapped_target_count': mapped_target_count,
            'actual_total': actual_total,
            'target_total': target_total,
            'delta_total': target_total - actual_total,
            'missing_barcodes': len(missing),
            'unresolved_targets': len(unresolved_targets),
            'target_conflicts': len(set(conflicts)),
            'rows': unique_rows,
        },
    }


@router.post("/inventory-adjust-and-publish")
def inventory_adjust_and_publish(payload: InventoryAdjustAndPublishRequest, db: Session = Depends(get_db)):
    """Change local stock and publish only the canonical sellable WB amount.

    The local movement is staged first inside the same DB transaction, then sellable is
    recomputed as physical - storage_only - every live reservation. The per-SKU sale
    lock prevents a new reservation from being created between that calculation and
    the WB write. A failed WB write rolls the staged local movement back.
    """
    barcode, _ = normalize_product_barcode(payload.barcode)
    sku = db.execute(select(SKU).where(
        SKU.client_id == payload.client_id, SKU.barcode == barcode,
    )).scalars().first()
    if not sku:
        product = wb_product_identity(db, payload.client_id, barcode)
        if not product:
            raise HTTPException(status_code=404, detail="Товар с таким ШК не найден у выбранного клиента")
        sku = _find_or_create_sku(
            db, payload.client_id, barcode, article=product.get("article"), size=product.get("size"),
        )

    location = db.get(Location, payload.location_id)
    if not location or location.warehouse_id != payload.warehouse_id:
        raise HTTPException(status_code=409, detail="Выбранная ячейка относится к другому складу или удалена")
    container = None
    if payload.container_id:
        container = db.get(Container, payload.container_id)
        if not container or container.client_id != payload.client_id or container.warehouse_id != payload.warehouse_id:
            raise HTTPException(status_code=409, detail="Выбранный короб относится к другому клиенту/складу или удалён")
        if container.location_id != payload.location_id:
            raise HTTPException(status_code=409, detail="Выбранный короб сейчас находится в другой ячейке")

    delta = Decimal(payload.delta)
    if delta == 0:
        raise HTTPException(status_code=400, detail="Количество изменения не может быть 0")

    lock_key = f"sale-reserve:{payload.client_id}:{payload.warehouse_id}:{sku.id}"
    with distributed_lock(db, lock_key, blocking=True) as acquired:
        if not acquired:
            raise HTTPException(status_code=409, detail="Остаток товара сейчас резервируется заказом. Повторите операцию.")
        before_physical = Decimal(_warehouse_barcode_qty(db, payload.client_id, payload.warehouse_id, barcode))
        before_sellable = max(0, int(effective_fbs_free_for_sku(
            db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku_id=sku.id,
            wb_warehouse_id=payload.wb_warehouse_id,
        )))
        if wb_sales_paused(db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku_id=sku.id):
            before_sellable = 0

        if delta < 0:
            bucket_qty = db.scalar(select(func.coalesce(func.sum(InventoryBalance.quantity), 0)).where(
                InventoryBalance.client_id == payload.client_id,
                InventoryBalance.warehouse_id == payload.warehouse_id,
                InventoryBalance.sku_id == sku.id,
                InventoryBalance.location_id == payload.location_id,
                InventoryBalance.container_id == payload.container_id if payload.container_id else InventoryBalance.container_id.is_(None),
            ))
            if Decimal(bucket_qty or 0) < -delta:
                raise HTTPException(status_code=409, detail=f"В выбранном месте недостаточно товара: доступно {bucket_qty or 0}, требуется {-delta}")

        line = StockLine(
            sku_id=sku.id, quantity=abs(delta),
            source_location_id=payload.location_id if delta < 0 else None,
            source_container_id=payload.container_id if delta < 0 else None,
            destination_location_id=payload.location_id if delta > 0 else None,
            destination_container_id=payload.container_id if delta > 0 else None,
            reason=payload.reason or ("Ручное списание из Остатков" if delta < 0 else "Ручное добавление из Остатков"),
        )
        try:
            execute_operation(db, OperationCreate(
                client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                operation_type="ADJUSTMENT", idempotency_key=str(uuid.uuid4()),
                performed_by=payload.performed_by, reference="WB_FBS_ATOMIC",
                comment="Атомарное изменение FulfillCore + WB FBS с защитой резерва", lines=[line],
            ), commit=False)
            after_physical = Decimal(_warehouse_barcode_qty(db, payload.client_id, payload.warehouse_id, barcode))
            after_sellable = max(0, int(effective_fbs_free_for_sku(
                db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku_id=sku.id
            )))
            if wb_sales_paused(db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku_id=sku.id):
                after_sellable = 0
        except Exception:
            db.rollback()
            raise

        selected_wb_id = None
        wb_written = False
        try:
            selected_wb_id = _sync_wb_stock(
                db, client_id=payload.client_id, physical_warehouse_id=payload.warehouse_id,
                wb_warehouse_id=payload.wb_warehouse_id, barcode=barcode, amount=after_sellable,
            )
            wb_written = True
            _record_fbs_publication(
                db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, barcode=barcode,
                wb_warehouse_id=selected_wb_id, amount=after_sellable, status="PUBLISHED",
            )
            if delta < 0 and payload.container_id:
                archive_consumed_empty_containers(
                    db, payload.client_id, payload.warehouse_id, [payload.container_id],
                    actor=payload.performed_by, reason="ATOMIC_ADJUST_EMPTY_BOX",
                )
                db.commit()
        except Exception as exc:
            db.rollback()
            rollback_error = None
            if wb_written:
                try:
                    _sync_wb_stock(
                        db, client_id=payload.client_id, physical_warehouse_id=payload.warehouse_id,
                        wb_warehouse_id=selected_wb_id or payload.wb_warehouse_id, barcode=barcode, amount=before_sellable,
                    )
                except Exception as restore_exc:
                    rollback_error = str(restore_exc)
            message = str(getattr(exc, "detail", exc))[:500]
            try:
                _record_fbs_publication(
                    db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, barcode=barcode,
                    wb_warehouse_id=selected_wb_id or payload.wb_warehouse_id, amount=None, status="ERROR", error=message,
                )
            except Exception:
                db.rollback()
            detail = f"WB/локальное изменение не завершено: {message}"
            if rollback_error:
                detail += f". ВНИМАНИЕ: не удалось вернуть прежний безопасный остаток WB: {rollback_error}"
            raise HTTPException(status_code=502, detail=detail) from exc

    return {
        "ok": True, "barcode": barcode,
        "before": int(before_physical), "after": int(after_physical),
        "before_sellable": int(before_sellable), "after_sellable": int(after_sellable),
        "wb_warehouse_id": selected_wb_id, "status": "PUBLISHED", "atomic": True, "reserve_safe": True,
    }


@router.post("/wb-oversell-guard")
def wb_oversell_guard(payload: WbOversellGuardRequest, db: Session = Depends(get_db)):
    """Queue ONLY chrt-level downward corrections when WB is above canonical size free stock."""
    if not payload.observed:
        return {"ok": True, "queued": 0, "rows": []}
    if len(payload.observed) > 5000:
        raise HTTPException(status_code=400, detail="Слишком много позиций для одной проверки WB")
    cred = _active_wb_credential(db, payload.client_id)
    if not cred:
        raise HTTPException(status_code=409, detail="Подключение WB для выбранного клиента не настроено")
    selected_wb_id = _work_wb_warehouse_id(cred, payload.wb_warehouse_id, payload.warehouse_id)
    if not selected_wb_id:
        raise HTTPException(status_code=409, detail="Для выбранного физического склада не назначен рабочий склад WB")

    normalized = {}
    for raw, qty in payload.observed.items():
        code = normalize_product_barcode(str(raw or ""))[0]
        if code:
            normalized[code] = max(0, int(qty or 0))
    if not normalized:
        return {"ok": True, "queued": 0, "rows": []}

    skus = db.execute(select(SKU).where(
        SKU.client_id == payload.client_id, SKU.barcode.in_(list(normalized))
    )).scalars().all()
    sku_by_code = {str(sku.barcode or '').strip(): sku for sku in skus if str(sku.barcode or '').strip()}
    for code in normalized:
        if code in sku_by_code:
            continue
        identity = wb_product_identity(db, payload.client_id, code)
        if identity:
            sku = _find_or_create_sku(db, payload.client_id, code, article=identity.get('article'), size=identity.get('size'))
            sku_by_code[code] = sku

    from app.services.stock_outbox_worker import _wb_size_targets
    size_targets, _siblings, chrt_by_code = _wb_size_targets(
        db, client_id=str(payload.client_id), warehouse_id=str(payload.warehouse_id),
        external_wh=str(selected_wb_id), codes=list(normalized),
    )
    rows = []
    processed_chrt: set[int] = set()
    for code, observed in normalized.items():
        chrt_id = chrt_by_code.get(code)
        target = size_targets.get(code)
        sku = sku_by_code.get(code)
        if chrt_id is None or target is None or sku is None:
            rows.append({"barcode": code, "wb_actual": observed, "queued": False, "error": "Нет надёжного chrtId/цели размера WB"})
            continue
        chrt_id = int(chrt_id)
        if chrt_id in processed_chrt:
            continue
        processed_chrt.add(chrt_id)
        target = max(0, int(target))
        if observed <= target:
            continue
        _enqueue_wb_absolute_target(
            db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku=sku,
            credential_id=cred.id, external_warehouse_id=int(selected_wb_id), barcode=code,
            desired=target, reason="AUTO_OVERSELL_DOWN_CHRT_GUARD",
        )
        rows.append({"barcode": code, "chrt_id": chrt_id, "wb_actual": observed, "safe_target": target, "queued": True})
    queued = sum(1 for row in rows if row.get('queued'))
    if queued:
        db.commit()
        _wake_wb_stock_outbox()
    else:
        db.rollback()
    return {"ok": True, "queued": queued, "rows": rows, "wb_warehouse_id": int(selected_wb_id)}



@router.post("/wb-drift-repair")
def wb_drift_repair(payload: WbOversellGuardRequest, db: Session = Depends(get_db)):
    """Queue two-way drift using WB size/chrtId truth, never one-barcode free stock."""
    if not payload.observed:
        return {"ok": True, "queued": 0, "queued_down": 0, "queued_up": 0, "waiting_up": 0, "rows": []}
    if len(payload.observed) > 5000:
        raise HTTPException(status_code=400, detail="Слишком много позиций для одной сверки WB")
    cred = _active_wb_credential(db, payload.client_id)
    if not cred:
        raise HTTPException(status_code=409, detail="Подключение WB для выбранного клиента не настроено")
    selected_wb_id = _work_wb_warehouse_id(cred, payload.wb_warehouse_id, payload.warehouse_id)
    if not selected_wb_id:
        raise HTTPException(status_code=409, detail="Для выбранного физического склада не назначен рабочий склад WB")

    normalized: dict[str, int] = {}
    for raw, qty in payload.observed.items():
        code = normalize_product_barcode(str(raw or ""))[0]
        if code:
            normalized[code] = max(0, int(qty or 0))
    if not normalized:
        return {"ok": True, "queued": 0, "queued_down": 0, "queued_up": 0, "waiting_up": 0, "rows": []}

    skus = db.execute(select(SKU).where(
        SKU.client_id == payload.client_id, SKU.barcode.in_(list(normalized))
    )).scalars().all()
    sku_by_code = {str(sku.barcode or '').strip(): sku for sku in skus if str(sku.barcode or '').strip()}
    for code in normalized:
        if code in sku_by_code:
            continue
        identity = wb_product_identity(db, payload.client_id, code)
        if identity:
            sku = _find_or_create_sku(db, payload.client_id, code, article=identity.get('article'), size=identity.get('size'))
            sku_by_code[code] = sku

    # FC_K9R31_CHRT_DRIFT_REPAIR: one WB amount belongs to the entire size/chrtId.
    from app.services.stock_outbox_worker import _wb_size_targets
    size_targets, _siblings, chrt_by_code = _wb_size_targets(
        db, client_id=str(payload.client_id), warehouse_id=str(payload.warehouse_id),
        external_wh=str(selected_wb_id), codes=list(normalized),
    )
    rows = []
    queued_down = queued_up = 0
    processed_chrt: set[int] = set()
    for code, actual in normalized.items():
        chrt_id = chrt_by_code.get(code)
        target = size_targets.get(code)
        sku = sku_by_code.get(code)
        if chrt_id is None or target is None or sku is None:
            rows.append({
                'barcode': code, 'wb_actual': actual, 'queued': False,
                'error': 'Нет надёжного chrtId/цели размера WB; автоматическая коррекция пропущена',
            })
            continue
        chrt_id = int(chrt_id)
        if chrt_id in processed_chrt:
            continue
        processed_chrt.add(chrt_id)
        target = max(0, int(target))
        if int(actual) == target:
            continue
        direction = 'DOWN' if int(actual) > target else 'UP'
        _enqueue_wb_absolute_target(
            db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku=sku,
            credential_id=cred.id, external_warehouse_id=int(selected_wb_id), barcode=code,
            desired=target, reason=f'AUTO_WB_CHRT_DRIFT_{direction}',
        )
        if direction == 'DOWN':
            queued_down += 1
        else:
            queued_up += 1
        rows.append({
            'barcode': code, 'chrt_id': chrt_id, 'wb_actual': int(actual),
            'safe_target': target, 'direction': direction, 'queued': True,
        })

    if queued_down + queued_up:
        db.commit()
        _wake_wb_stock_outbox()
    else:
        db.rollback()
    return {
        'ok': True, 'queued': queued_down + queued_up, 'queued_down': queued_down,
        'queued_up': queued_up, 'waiting_up': 0, 'rows': rows,
        'wb_warehouse_id': int(selected_wb_id), 'upward_safe': 'OUTBOX_WAITS_FOR_POST_QUEUE_NEW_SYNC',
    }


@router.post("/publish-wb")
def publish_local_stock_to_wb(payload: PublishWbStockRequest, db: Session = Depends(get_db)):
    """Queue the current canonical chrtId target after an explicit operator resume.

    FC_K9R32_SINGLE_QUEUE_UNCACHED_WB_CODE is retained as a compatibility marker;
    r44/r45 supersede its old zero-wake behavior with exact live resolve before enqueue.

    FC_K9R44_EXACT_WB_RESOLVE_BEFORE_QUEUE remains cumulative: live chrtId is resolved
    before queue and the legacy reason marker MANUAL_PUBLISH_CHRT_EXACT_RESOLVE is not
    used as a zero sentinel. Остаток FulfillCore не изменён и нулевая команда в WB не отправлялась.

    FC_K9R45_MANUAL_RESUME_BEFORE_TARGET: the row-level «Обновить» button is an
    explicit operator instruction to make the current FBS sellable truth publishable.
    r44 resolved an uncached chrtId before enqueue, but it still calculated the first
    target while an older fc_wb_sales_pauses row could be TRUE. That turned a perfectly
    sellable row (for example 24 free) into a misleading target=0. The worker could later
    clear that pause, but the UI and queue generation already carried zero and some rows
    never converged promptly.

    r45 always resolves the selected seller barcode against the current WB size, clears
    only the already-existing local pause state for that exact live size, supersedes old
    sibling commands, recomputes canonical free stock in the same DB transaction, and
    only then enqueues the real absolute target. STORAGE_ONLY and NEW/PICKING/accounting
    holds remain part of the canonical calculation and are not changed here.
    """
    barcode, _duplicated_from = normalize_product_barcode(payload.barcode)
    sku = db.execute(select(SKU).where(
        SKU.client_id == payload.client_id,
        SKU.barcode == barcode,
    )).scalars().first()
    if not sku:
        product = wb_product_identity(db, payload.client_id, barcode)
        if not product:
            raise HTTPException(status_code=404, detail="Товар с таким ШК не найден у выбранного клиента")
        sku = _find_or_create_sku(
            db, payload.client_id, barcode, article=product.get("article"), size=product.get("size")
        )

    cred = _active_wb_credential(db, payload.client_id)
    if not cred:
        raise HTTPException(status_code=409, detail="Подключение WB для выбранного клиента не настроено")
    selected_wb_id = _work_wb_warehouse_id(cred, payload.wb_warehouse_id, payload.warehouse_id)
    if not selected_wb_id:
        raise HTTPException(status_code=409, detail="Для выбранного физического склада не назначен рабочий склад WB")

    # Manual publish is intentionally an exact one-size action. Always refresh this one
    # seller barcode so an obsolete cached size cannot keep an old pause/zero scope alive.
    token = decrypt_secret(cred.encrypted_api_key)
    wb_client = WBClient(token)
    try:
        fresh_rows = _wb_fresh_size_rows(wb_client, [barcode])
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось проверить текущую карточку WB для ШК {barcode}: {str(exc)[:300]}",
        ) from exc
    finally:
        wb_client.close()

    if not fresh_rows:
        raise HTTPException(
            status_code=409,
            detail=(f"WB не нашёл ШК {barcode} в текущих карточках или не вернул chrtId размера. "
                    "Остаток FulfillCore не изменён и команда WB не создавалась."),
        )

    from app.services.stock_outbox_worker import _wb_size_targets

    # First resolve the complete live size solely to obtain the exact sibling scope.
    size_targets, sibling_map, chrt_by_code = _wb_size_targets(
        db, client_id=str(payload.client_id), warehouse_id=str(payload.warehouse_id),
        external_wh=str(selected_wb_id), codes=[barcode], extra_size_rows=fresh_rows,
    )
    chrt_id = chrt_by_code.get(barcode)
    if chrt_id is None:
        raise HTTPException(
            status_code=409,
            detail=f"Для ШК {barcode} WB вернул карточку, но не удалось определить chrtId размера. Команда не создана.",
        )

    sibling_codes = {normalize_product_barcode(code)[0] for code in sibling_map.get(barcode, {barcode})}
    sibling_codes.discard("")
    sibling_codes.add(barcode)

    # Publication never implies permission to resume sales.
    paused_before = 0
    # Park every older command for any barcode of this same live WB size. This closes the
    # old DELETE_FREE_AND_PAUSE=0 race before the new non-zero generation is committed.
    _supersede_wb_scope_rows(
        db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, codes=sibling_codes,
        reason=f"MANUAL_PUBLISH_CHRT chrtId {int(chrt_id)} replaced older sibling/chrt command",
    )

    # Recompute only after the old pause is gone. This is the value the operator sees and
    # the exact desired_amount persisted in the new queue generation.
    size_targets, sibling_map, chrt_by_code = _wb_size_targets(
        db, client_id=str(payload.client_id), warehouse_id=str(payload.warehouse_id),
        external_wh=str(selected_wb_id), codes=[barcode], extra_size_rows=fresh_rows,
    )
    target = size_targets.get(barcode)
    chrt_id = chrt_by_code.get(barcode)
    if target is None or chrt_id is None:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Не удалось рассчитать безопасную цель WB для ШК {barcode}. Изменения отменены.",
        )

    queued_target = max(0, int(target))
    reason = "MANUAL_PUBLISH_CHRT_REAL_TARGET"
    _enqueue_wb_absolute_target(
        db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku=sku,
        credential_id=cred.id, external_warehouse_id=int(selected_wb_id), barcode=barcode,
        desired=queued_target, reason=reason,
    )
    db.commit()
    _wake_wb_stock_outbox()
    return {
        "ok": True, "barcode": barcode, "after": queued_target,
        "target_resolving": False, "chrt_id": int(chrt_id),
        "wb_warehouse_id": int(selected_wb_id), "status": "QUEUED",
        "paused_skus_cleared": int(paused_before),
        "sibling_barcodes": len(sibling_codes),
        "message": (f"chrtId {int(chrt_id)} определён; стоп продаж учтён; "
                    f"целевая продажа {queued_target} шт. поставлена в очередь WB"),
    }


@router.post("/manual")
def manual_receive(payload: ManualReceiveRequest, db: Session = Depends(get_db)):
    sku = _find_or_create_sku(db, payload.client_id, payload.barcode)
    loc = _ensure_location(db, payload.warehouse_id, payload.location_code)
    cont = _ensure_container(db, payload.client_id, payload.warehouse_id, loc.id, payload.container_code)
    qty = Decimal(payload.quantity)

    if payload.mode == "ADD":
        execute_operation(db, OperationCreate(
            client_id=payload.client_id, warehouse_id=payload.warehouse_id, operation_type="RECEIVING", idempotency_key=str(uuid.uuid4()),
            performed_by=payload.performed_by, reference=payload.reference, comment=f"Ручная приёмка · ДОБАВИТЬ · ЗОНА:{payload.stock_class}",
            lines=[StockLine(sku_id=sku.id, quantity=qty, destination_location_id=loc.id, destination_container_id=cont.id if cont else None, reason="MANUAL_ADD")],
        ))
    else:
        _set_local_total(db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku=sku, target=qty, loc=loc, cont=cont, performed_by=payload.performed_by, reference=payload.reference)

    # FC_K9R62_WAREHOUSE_SPLIT: physical stock is one truth; MAIN/FBS is only
    # classification inside that physical bucket. Receiving directly to MAIN must never
    # silently become saleable merely because the inventory screen is opened later.
    reconcile_stock_bucket(
        db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku_id=sku.id,
        location_id=loc.id, container_id=cont.id if cont else None,
        preferred_bucket="STORAGE_ONLY" if payload.stock_class == "MAIN" else "FBS_AVAILABLE", lock=True,
    )
    db.commit()
    normalized_barcode = str(sku.barcode or payload.barcode)
    local_total = int(_warehouse_barcode_qty(db, payload.client_id, payload.warehouse_id, normalized_barcode))
    publish = payload.stock_class == "FBS"
    return {
        "ok": True, "mode": payload.mode, "barcode": normalized_barcode,
        "local_total": float(local_total), "stock_class": payload.stock_class,
        "wb_pending": publish,
        "pending_line": ({"barcode": normalized_barcode, "quantity": local_total, "mode": "SET"} if publish else None),
    }


@router.post("/manual/batch")
def manual_receive_batch(payload: ManualReceiveBatchRequest, db: Session = Depends(get_db)):
    """Commit selected manual-receiving rows as one atomic local stock operation.

    ``internal_only`` is intentionally local-only: unresolved WB barcodes are
    accepted into the virtual ПРИЕМКА location, never published to WB, and can
    later be physically placed after the WB card appears.
    """
    stock_lines: list[StockLine] = []
    sku_by_barcode: dict[str, SKU] = {}
    total_quantity = 0
    internal_location = _ensure_location(db, payload.warehouse_id, "ПРИЕМКА") if payload.internal_only else None

    normalized_codes: set[str] = set()
    for row in payload.lines:
        code, _duplicated = normalize_product_barcode(row.barcode)
        if code:
            normalized_codes.add(code)

    sku_cache: dict[str, SKU] = {}
    if normalized_codes:
        for code in sorted(normalized_codes):
            db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'), {'key':f'fc:local-sku:{payload.client_id}:{code}'})
        existing_skus = db.execute(select(SKU).where(
            SKU.client_id == payload.client_id, SKU.barcode.in_(list(normalized_codes))
        )).scalars().all()
        sku_cache = {str(item.barcode or ""): item for item in existing_skus if item.barcode}
        identity_map = wb_product_identity_map(db, payload.client_id, normalized_codes)
        new_skus: list[SKU] = []
        for code in sorted(normalized_codes):
            product = identity_map.get(code)
            sku = sku_cache.get(code)
            if sku is not None:
                if product:
                    if str(sku.sku_code or "").startswith("LOCAL-"):
                        sku.article = sku.article or product.get("article")
                        sku.size = sku.size or product.get("size")
                    else:
                        sku.article = product.get("article") or sku.article
                        sku.size = product.get("size") or sku.size
                    sku.name = product.get("name") or sku.name
                sku.is_active = True
                continue
            resolved_article = (product or {}).get("article") or code
            resolved_name = (product or {}).get("name") or resolved_article
            sku = SKU(
                id=uuid.uuid4(), client_id=payload.client_id, sku_code=f"WB-{code}",
                article=resolved_article, name=resolved_name, size=(product or {}).get("size"),
                barcode=code, unit="PCS", is_active=True,
            )
            sku_cache[code] = sku
            new_skus.append(sku)
        if new_skus:
            db.add_all(new_skus)

    location_cache: dict[str, Location] = {}
    container_cache: dict[str, Container] = {}
    if not payload.internal_only:
        location_codes = {str(row.location_code or "").strip() for row in payload.lines if str(row.location_code or "").strip()}
        if location_codes:
            existing_locations = db.execute(select(Location).where(
                Location.warehouse_id == payload.warehouse_id,
                or_(Location.code.in_(list(location_codes)), Location.barcode.in_(list(location_codes))),
            )).scalars().all()
            for loc in existing_locations:
                for key in (str(loc.code or "").strip(), str(loc.barcode or "").strip()):
                    if key in location_codes and key not in location_cache:
                        location_cache[key] = loc
            for code in sorted(location_codes):
                if code in location_cache:
                    if not location_cache[code].is_active:
                        location_cache[code].is_active = True
                    continue
                loc = Location(
                    id=uuid.uuid4(), warehouse_id=payload.warehouse_id, parent_id=None,
                    code=code, barcode=code, name=code, location_type="CELL", is_active=True,
                )
                location_cache[code] = loc
                db.add(loc)

        container_codes = {str(row.container_code or "").strip() for row in payload.lines if str(row.container_code or "").strip()}
        if container_codes:
            existing_containers = db.execute(select(Container).where(
                Container.client_id == payload.client_id,
                Container.warehouse_id == payload.warehouse_id,
                or_(Container.barcode.in_(list(container_codes)), Container.name.in_(list(container_codes))),
            )).scalars().all()
            for cont in existing_containers:
                for key in (str(cont.barcode or "").strip(), str(cont.name or "").strip()):
                    if key in container_codes and key not in container_cache:
                        container_cache[key] = cont
            first_location_by_container: dict[str, uuid.UUID] = {}
            for row in payload.lines:
                container_code = str(row.container_code or "").strip()
                location_code = str(row.location_code or "").strip()
                if container_code and location_code and location_code in location_cache:
                    first_location_by_container.setdefault(container_code, location_cache[location_code].id)
            for code in sorted(container_codes):
                if code in container_cache:
                    continue
                cont = Container(
                    id=uuid.uuid4(), client_id=payload.client_id, warehouse_id=payload.warehouse_id,
                    location_id=first_location_by_container.get(code),
                    barcode=_fc_box_storage_barcode(db, payload.client_id, payload.warehouse_id, code), name=code,
                    container_type="BOX", status="ACTIVE",
                )
                container_cache[code] = cont
                db.add(cont)
    db.flush()

    for row in payload.lines:
        barcode, _duplicated_from = normalize_product_barcode(row.barcode)
        location_code = str(row.location_code or "").strip()
        if not barcode:
            raise HTTPException(status_code=400, detail="В каждой строке нужен ШК товара")
        if not payload.internal_only and not location_code:
            raise HTTPException(status_code=400, detail="В каждой строке нужны ШК товара и ячейка")
        sku = sku_cache.get(barcode)
        if sku is None:
            sku = _find_or_create_sku(db, payload.client_id, barcode)
            sku_cache[barcode] = sku
        sku_by_barcode[str(sku.barcode or barcode)] = sku
        if payload.internal_only:
            location = internal_location
            container = None
            reason = f"MANUAL_UNMATCHED_INTERNAL;SOURCE={location_code or '-'};BOX={str(row.container_code or '').strip() or '-'}"
        else:
            location = location_cache.get(location_code)
            if location is None:
                location = _ensure_location(db, payload.warehouse_id, location_code)
                location_cache[location_code] = location
            container_code = str(row.container_code or "").strip()
            container = container_cache.get(container_code) if container_code else None
            if container is not None:
                if container.warehouse_id != payload.warehouse_id:
                    raise HTTPException(status_code=409, detail=f"Короб {container_code} относится к другому складу")
                if str(container.status or "").upper() != "ACTIVE":
                    container.status = "ACTIVE"
                    container.location_id = location.id
                if container.location_id is None:
                    container.location_id = location.id
                elif container.location_id != location.id:
                    raise HTTPException(status_code=409, detail=f"Короб {container_code} находится в другой ячейке. Сначала переместите/откройте короб в нужном месте.")
            elif container_code:
                container = _ensure_container(db, payload.client_id, payload.warehouse_id, location.id, container_code)
                if container is not None:
                    container_cache[container_code] = container
            reason = "MANUAL_BATCH_ADD"
        quantity = int(row.quantity)
        total_quantity += quantity
        stock_lines.append(StockLine(
            sku_id=sku.id,
            quantity=Decimal(quantity),
            destination_location_id=location.id,
            destination_container_id=container.id if container else None,
            reason=reason,
        ))

    # Idempotency is safe only when the repeated request is the SAME batch. Older UI
    # versions accidentally reused one manual draft id after a successful commit, which
    # could make a different next batch look successful while execute_operation no-op'ed.
    existing_operation = db.execute(select(InventoryOperation).where(
        InventoryOperation.client_id == payload.client_id,
        InventoryOperation.idempotency_key == payload.idempotency_key,
    )).scalar_one_or_none()
    if existing_operation is not None:
        existing_lines = db.execute(select(InventoryOperationLine).where(
            InventoryOperationLine.operation_id == existing_operation.id
        )).scalars().all()
        def _sig(line):
            return (
                str(line.sku_id), str(Decimal(line.quantity)),
                str(line.destination_location_id or ""), str(line.destination_container_id or ""),
            )
        requested_signature = sorted(_sig(line) for line in stock_lines)
        existing_signature = sorted(_sig(line) for line in existing_lines if line.destination_location_id is not None)
        if requested_signature != existing_signature:
            raise HTTPException(
                status_code=409,
                detail="Ключ этой приёмки уже использован для другого состава товара. Это техническая защита, отдельного черновика нет. Остатки не изменены. Нажмите Ctrl+F5 и повторите — текущий список товаров сохранится.",
            )

    operation = execute_operation(db, OperationCreate(
        client_id=payload.client_id,
        warehouse_id=payload.warehouse_id,
        operation_type="RECEIVING",
        idempotency_key=payload.idempotency_key,
        performed_by=payload.performed_by,
        reference=payload.reference,
        comment=(
            f"Ручная приёмка · ВНУТРЕННИЙ УЧЁТ НЕ НАЙДЕНО В WB · {len(stock_lines)} строк · ПРИЕМКА · ЗОНА:MAIN"
            if payload.internal_only
            else f"Ручная приёмка · ПАКЕТ · {len(stock_lines)} строк · ЗОНА:{payload.stock_class}"
        ),
        lines=stock_lines,
    ), commit=False)

    # Never tell the operator «добавлено», until the exact location/container buckets
    # can be read back in the same transaction. This catches wrong-box/context bugs
    # before success is shown and keeps the local stock update atomic.
    expected_buckets: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None], Decimal] = {}
    for line in stock_lines:
        key=(line.sku_id, line.destination_location_id, line.destination_container_id)
        expected_buckets[key]=expected_buckets.get(key, Decimal("0"))+Decimal(line.quantity)
    placement_rows = db.execute(
        select(InventoryBalance, SKU, Location, Container)
        .join(SKU, SKU.id == InventoryBalance.sku_id)
        .join(Location, Location.id == InventoryBalance.location_id)
        .outerjoin(Container, Container.id == InventoryBalance.container_id)
        .where(
            InventoryBalance.client_id == payload.client_id,
            InventoryBalance.warehouse_id == payload.warehouse_id,
            InventoryBalance.sku_id.in_(list({k[0] for k in expected_buckets})),
            InventoryBalance.location_id.in_(list({k[1] for k in expected_buckets})),
            InventoryBalance.quantity > 0,
        )
    ).all()
    balances={(bal.sku_id, bal.location_id, bal.container_id): (bal, sku, loc, cont) for bal, sku, loc, cont in placement_rows}
    missing=[]
    for key,added in expected_buckets.items():
        row=balances.get(key)
        if row is None or Decimal(row[0].quantity or 0) < added:
            missing.append(str(key))
    if missing:
        db.rollback()
        raise HTTPException(status_code=500, detail="Приёмка не подтверждена контрольным чтением остатков. Изменения отменены; повторите операцию.")
    preferred_bucket = "STORAGE_ONLY" if (payload.internal_only or payload.stock_class == "MAIN") else "FBS_AVAILABLE"
    for sku_id, location_id, container_id in expected_buckets:
        reconcile_stock_bucket(
            db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku_id=sku_id,
            location_id=location_id, container_id=container_id, preferred_bucket=preferred_bucket, lock=True,
        )
    db.commit()

    placements=[]
    for (sku_id, location_id, container_id), added in expected_buckets.items():
        bal, sku, loc, cont = balances[(sku_id, location_id, container_id)]
        placements.append({
            "sku_id": str(sku_id), "barcode": str(sku.barcode or ""),
            "location_id": str(location_id), "location_code": str(loc.code or ""),
            "container_id": str(container_id) if container_id else None,
            "container_code": str((cont.name or cont.barcode) if cont else ""),
            "quantity_added": float(added), "balance_after": float(Decimal(bal.quantity or 0)),
        })

    pending_lines = []
    if not payload.internal_only and payload.stock_class == "FBS" and sku_by_barcode:
        codes = list(sku_by_barcode)
        total_rows = db.execute(
            select(SKU.barcode, func.coalesce(func.sum(InventoryBalance.quantity), 0))
            .join(InventoryBalance, InventoryBalance.sku_id == SKU.id)
            .where(
                SKU.client_id == payload.client_id,
                SKU.barcode.in_(codes),
                InventoryBalance.client_id == payload.client_id,
                InventoryBalance.warehouse_id == payload.warehouse_id,
            )
            .group_by(SKU.barcode)
        ).all()
        totals = {str(code): int(Decimal(quantity or 0)) for code, quantity in total_rows if code}
        for barcode in codes:
            pending_lines.append({
                "barcode": barcode, "quantity": totals.get(barcode, 0), "mode": "SET",
                "idempotency_key": _wb_pending_key(f"manual:{payload.idempotency_key}", barcode),
            })

    return {
        "ok": True,
        "operation_id": str(operation.id),
        "rows_received": len(stock_lines),
        "quantity_received": total_quantity,
        "internal_only": bool(payload.internal_only),
        "stock_class": "MAIN" if payload.internal_only else payload.stock_class,
        "destination": "ПРИЕМКА" if payload.internal_only else ("Основной склад" if payload.stock_class == "MAIN" else "FBS"),
        "wb_pending": bool(pending_lines),
        "pending_lines": pending_lines,
        "placements": placements,
        "stock_verified": True,
    }


@router.post("/manual/unmatched-export.xlsx")
def export_manual_unmatched(payload: ManualUnmatchedExportRequest):
    """Excel handoff for manual-receiving lines whose WB card is unavailable."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Не найдено в WB"
    ws.append([
        "ШК", "Артикул", "Размер", "Название", "Количество",
        "Исходная ячейка", "Исходный короб", "Место / короб", "Причина",
    ])
    for row in payload.lines:
        ws.append([
            row.barcode, row.article or "", row.size or "", row.name or "", int(row.quantity),
            row.location_code or "", row.container_code or "", row.storage_display or "",
            "Карточка по ШК не найдена в локальном каталоге WB",
        ])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:I{max(1, ws.max_row)}"
    widths = [22, 18, 16, 42, 12, 20, 28, 48, 52]
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + idx)].width = width
    for row in ws.iter_rows(min_row=2):
        row[0].number_format = "@"
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="manual_receiving_unmatched_wb.xlsx"'},
    )


def _rows_from_workbook(content: bytes) -> list[list]:
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = []
    for row in ws.iter_rows(values_only=True):
        vals = list(row)
        if not any(v not in (None, "") for v in vals):
            continue
        rows.append(vals)
    return rows


def _looks_header(row: list) -> bool:
    joined = " ".join(str(x or "").lower() for x in row[:5])
    return any(x in joined for x in ["штрих", "шк", "колич", "артик", "размер", "ячей", "короб"])


def _external_pick_natural(value: str | None) -> tuple:
    parts = re.split(r"(\d+)", str(value or ""))
    return tuple((0, int(part)) if part.isdigit() else (1, part.casefold()) for part in parts if part != "")


def _external_pick_task_text(value) -> str:
    if value is None:
        return ''
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text_value = str(value).strip()
    if text_value.endswith('.0'):
        try:
            return str(int(Decimal(text_value)))
        except Exception:
            pass
    return text_value


def _external_pick_parse_rows(content: bytes, task_number_override: str | None = None) -> tuple[str, list[dict], int]:
    # FC_K9R38_TASK_NUMBER_PARSE: task number is mandatory business identity.
    """Parse r38 task template: task number + barcode/quantity rows.

    The task number is a durable business id and is required. Duplicate product
    barcodes are summed before any stock mutation so one task can never write the
    same requested line twice merely because Excel contains repeated rows.
    """
    try:
        rows = _rows_from_workbook(content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Не удалось прочитать Excel: {exc}") from exc
    source_rows = len(rows)
    if not rows:
        raise HTTPException(status_code=400, detail='Файл пустой')
    if len(rows) > 20010:
        raise HTTPException(status_code=400, detail='В одном файле допускается не более 20 000 товарных строк')

    task_number = _external_pick_task_text(task_number_override) if task_number_override else ''
    task_row_index = -1 if task_number else None
    # Accept the task-number label in any of the first columns, not only A1/B1.
    # r106: the operator may instead enter the business number in «Справочники».
    # Operators often export or copy worksheets with one descriptive column before it.
    if not task_number:
        for idx, row in enumerate(rows[:12]):
            values = list(row)
            for col, raw in enumerate(values):
                label = str(raw or '').strip().casefold().replace('ё', 'е')
                if not (('номер' in label or '№' in label) and ('сбороч' in label or 'задан' in label)):
                    continue
                inline = ''
                if ':' in str(raw or ''):
                    inline = _external_pick_task_text(str(raw).split(':', 1)[1])
                next_value = _external_pick_task_text(values[col + 1]) if col + 1 < len(values) else ''
                task_number = next_value or inline
                task_row_index = idx
                break
            if task_number:
                break
    if not task_number:
        raise HTTPException(status_code=400, detail='Укажите «Номер сборочного задания» рядом с заголовком в верхней части Excel')
    if len(task_number) > 255:
        raise HTTPException(status_code=400, detail='Номер сборочного задания слишком длинный (максимум 255 символов)')
    if task_number.strip().upper() in {'УКАЖИТЕ НОМЕР','УКАЖИТЕ_НОМЕР','НОМЕР'}:
        raise HTTPException(status_code=400, detail='Заполните реальный номер сборочного задания')

    def header_token(value):
        return ''.join(ch for ch in str(value or '').strip().casefold().replace('ё', 'е') if ch.isalnum())

    barcode_aliases = {header_token(x) for x in (
        'ШК', 'ШК товара', 'Штрихкод', 'Штрихкод товара', 'Barcode', 'EAN', 'EAN13', 'GTIN'
    )}
    quantity_aliases = {header_token(x) for x in (
        'Количество', 'Кол-во', 'Кол во', 'Колво', 'Количество шт', 'Количество, шт', 'Qty', 'Quantity'
    )}
    header_idx = None
    barcode_col = None
    quantity_col = None
    # Detect the two required columns anywhere in the same header row. Extra columns
    # before/between/after them are allowed and are ignored.
    for idx, row in enumerate(rows[:40]):
        if task_row_index is not None and idx <= task_row_index:
            continue
        found_barcode = found_quantity = None
        for col, raw in enumerate(row):
            token = header_token(raw)
            is_barcode = token in barcode_aliases or token.startswith('шк') or 'штрихкод' in token or token.startswith('barcode')
            is_quantity = token in quantity_aliases or token.startswith('колич') or token.startswith('колво') or token.startswith('qty') or token.startswith('quantity')
            if is_barcode and found_barcode is None:
                found_barcode = col
            if is_quantity and found_quantity is None:
                found_quantity = col
        if found_barcode is not None and found_quantity is not None and found_barcode != found_quantity:
            header_idx, barcode_col, quantity_col = idx, found_barcode, found_quantity
            break
    if header_idx is None:
        raise HTTPException(
            status_code=400,
            detail='В Excel не найдены заголовки «ШК товара» и «Количество» в одной строке. Они могут находиться в любых столбцах; лишние столбцы допустимы.'
        )

    data_rows = rows[header_idx + 1:]
    if not data_rows:
        raise HTTPException(status_code=400, detail='В файле нет строк с товаром')
    grouped: dict[str, dict] = {}
    errors: list[str] = []
    total = 0
    max_col = max(int(barcode_col), int(quantity_col))
    for offset, row in enumerate(data_rows, start=header_idx + 2):
        values = list(row) + [None] * (max_col + 1)
        barcode_raw = values[int(barcode_col)]
        raw_qty = values[int(quantity_col)]
        if barcode_raw in (None, '') and raw_qty in (None, ''):
            continue
        barcode, _duplicated = normalize_product_barcode(str(barcode_raw or '').strip())
        if not barcode:
            errors.append(f'строка {offset}: пустой ШК')
            continue
        if _wb_internal_product_codes_placeholder(barcode):
            errors.append(f'строка {offset}: {barcode} похож на внутренний ШК короба/ячейки')
            continue
        try:
            qty_decimal = Decimal(str(raw_qty).replace(',', '.'))
        except Exception:
            errors.append(f'строка {offset}: количество {raw_qty!r} не является числом')
            continue
        if qty_decimal <= 0 or qty_decimal != qty_decimal.to_integral_value():
            errors.append(f'строка {offset}: количество должно быть целым числом больше 0')
            continue
        qty = int(qty_decimal)
        if qty > 1_000_000:
            errors.append(f'строка {offset}: количество слишком большое ({qty})')
            continue
        total += qty
        if total > 2_000_000:
            raise HTTPException(status_code=400, detail='Суммарное количество в одном файле не может превышать 2 000 000 шт.')
        entry = grouped.setdefault(barcode, {'barcode':barcode,'quantity':0,'row_number':offset,'source_rows':[]})
        entry['quantity'] += qty
        entry['source_rows'].append(offset)
    if errors:
        detail = '; '.join(errors[:20])
        if len(errors) > 20:
            detail += f'; ещё ошибок: {len(errors)-20}'
        raise HTTPException(status_code=400, detail=detail)
    if not grouped:
        raise HTTPException(status_code=400, detail='После проверки в файле не осталось валидных строк')
    return task_number, list(grouped.values()), source_rows

def _wb_internal_product_codes_placeholder(code: str) -> bool:
    value = str(code or '').strip().upper()
    return value.startswith(('BOX_', 'FCBOX_', 'FCLOC-'))


def _external_pick_bucket_maps(
    db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, sku_ids: list[uuid.UUID]
) -> tuple[dict, dict, dict]:
    if not sku_ids:
        return {}, {}, {}
    ids = [str(x) for x in sku_ids]
    reserve_stmt = text("""
        SELECT CAST(r.sku_id AS varchar) AS sku_id,CAST(r.source_location_id AS varchar) AS location_id,
          COALESCE(CAST(r.source_container_id AS varchar),'') AS container_key,
          COALESCE(SUM(CASE WHEN o.local_status IN ('NEW','PICKING') THEN r.quantity ELSE 0 END),0) AS reserved,
          COALESCE(SUM(CASE WHEN COALESCE(o.local_status,'') NOT IN ('NEW','PICKING')
            AND NOT (r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL) THEN r.quantity ELSE 0 END),0) AS accounting_pending
        FROM fc_stock_reservations r
        LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
        LEFT JOIN inventory_operations io
          ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar) AND COALESCE(io.status,'COMPLETED')='COMPLETED'
         AND io.idempotency_key=('stock-core-v2-pack-' || CAST(o.id AS varchar) || '-' || CAST(r.id AS varchar))
        WHERE CAST(r.client_id AS varchar)=:client AND CAST(r.warehouse_id AS varchar)=:warehouse
          AND CAST(r.sku_id AS varchar) IN :ids AND r.source_location_id IS NOT NULL
          AND r.status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
        GROUP BY CAST(r.sku_id AS varchar),CAST(r.source_location_id AS varchar),COALESCE(CAST(r.source_container_id AS varchar),'')
    """).bindparams(bindparam('ids', expanding=True))
    reserve_map: dict[tuple[str,str,str], tuple[Decimal,Decimal]] = {}
    try:
        for row in db.execute(reserve_stmt, {'client':str(client_id),'warehouse':str(warehouse_id),'ids':ids}).mappings():
            reserve_map[(str(row['sku_id']),str(row['location_id']),str(row['container_key'] or ''))] = (
                max(Decimal(0), Decimal(str(row['reserved'] or 0))),
                max(Decimal(0), Decimal(str(row['accounting_pending'] or 0))),
            )
    except Exception:
        reserve_map = {}

    storage_map: dict[tuple[str,str,str], Decimal] = {}
    try:
        stmt = text("""
            SELECT CAST(sku_id AS varchar) AS sku_id,CAST(location_id AS varchar) AS location_id,
              COALESCE(container_key,'') AS container_key,COALESCE(SUM(storage_only),0) AS storage_only
            FROM fc_stocks_v2
            WHERE CAST(client_id AS varchar)=:client AND CAST(warehouse_id AS varchar)=:warehouse
              AND CAST(sku_id AS varchar) IN :ids
            GROUP BY CAST(sku_id AS varchar),CAST(location_id AS varchar),COALESCE(container_key,'')
        """).bindparams(bindparam('ids', expanding=True))
        for row in db.execute(stmt, {'client':str(client_id),'warehouse':str(warehouse_id),'ids':ids}).mappings():
            storage_map[(str(row['sku_id']),str(row['location_id']),str(row['container_key'] or ''))] = max(Decimal(0), Decimal(str(row['storage_only'] or 0)))
    except Exception:
        storage_map = {}

    external_map: dict[tuple[str,str,str], Decimal] = {}
    try:
        stmt = text("""
            SELECT sku_id,location_id,COALESCE(container_id,'') AS container_key,COALESCE(SUM(quantity),0) AS quantity
            FROM fc_external_pick_allocations
            WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id IN :ids AND status='ACTIVE'
            GROUP BY sku_id,location_id,COALESCE(container_id,'')
        """).bindparams(bindparam('ids', expanding=True))
        for row in db.execute(stmt, {'client':str(client_id),'warehouse':str(warehouse_id),'ids':ids}).mappings():
            external_map[(str(row['sku_id']),str(row['location_id']),str(row['container_key'] or ''))] = max(Decimal(0), Decimal(str(row['quantity'] or 0)))
    except Exception:
        external_map = {}
    return reserve_map, storage_map, external_map


def _external_pick_cached_size_map(
    db: Session, *, client_id: uuid.UUID, barcodes: list[str] | set[str]
) -> tuple[dict[str, set[str]], dict[str, int]]:
    """Resolve all uploaded barcodes to cached WB size siblings in one catalogue scan.

    This is intentionally mapping-only: unlike the WB publication target helper it does
    not calculate stock and does not honor a sales-pause as zero availability. External
    warehouse picking must still be able to reserve real free stock while WB sales are
    paused.
    """
    wanted = {normalize_product_barcode(str(code))[0] for code in (barcodes or []) if str(code or '').strip()}
    wanted.discard('')
    if not wanted:
        return {}, {}
    code_chrt: dict[str, int] = {}
    chrt_codes: dict[int, set[str]] = {}
    size_rows = db.execute(select(MarketplaceProduct.sizes).where(
        MarketplaceProduct.client_id == client_id, MarketplaceProduct.provider == 'WB'
    )).scalars().all()
    for sizes in size_rows:
        for row in sizes or []:
            if not isinstance(row, dict):
                continue
            raw_chrt = row.get('chrtID') or row.get('chrtId') or row.get('chrt_id')
            try:
                chrt_id = int(raw_chrt)
            except (TypeError, ValueError):
                continue
            row_codes = {
                normalize_product_barcode(str(raw))[0]
                for raw in (row.get('skus') or []) if str(raw or '').strip()
            }
            row_codes.discard('')
            if not row_codes:
                continue
            chrt_codes.setdefault(chrt_id, set()).update(row_codes)
            for code in row_codes:
                code_chrt[code] = chrt_id
    siblings: dict[str, set[str]] = {}
    chrts: dict[str, int] = {}
    for code in wanted:
        chrt_id = code_chrt.get(code)
        if chrt_id is None:
            continue
        chrts[code] = chrt_id
        siblings[code] = set(chrt_codes.get(chrt_id) or {code})
    return siblings, chrts


def _external_pick_identity_candidates(
    db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, wb_warehouse_id: int, barcode: str,
    sibling_codes_override: set[str] | None = None, chrt_id_override: int | None = None,
    identity_override: dict | None = None,
) -> tuple[list[SKU], set[str], int | None]:
    """Resolve one uploaded seller barcode to safe local aliases of that WB size."""
    sibling_codes = {str(x).strip() for x in (sibling_codes_override or {barcode}) if str(x or '').strip()}
    sibling_codes.add(barcode)
    identity = identity_override if identity_override is not None else (wb_product_identity(db, client_id, barcode) or {})
    stmt = select(SKU).where(SKU.client_id == client_id, SKU.is_active.is_(True))
    conditions = [SKU.barcode.in_(sorted(sibling_codes))]
    article = str((identity or {}).get('article') or '').strip()
    size_name = str((identity or {}).get('size') or '').strip()
    if article and size_name:
        # The WB identity makes this article+size broadening safe and catches historical
        # local alias rows whose old barcode is no longer present in current sizes[].
        conditions.append(and_(SKU.article == article, SKU.size == size_name))
    skus = db.execute(stmt.where(or_(*conditions)).order_by(SKU.barcode, SKU.created_at)).scalars().all()
    raw_chrt = chrt_id_override if chrt_id_override is not None else (identity or {}).get('chrt_id')
    try:
        chrt_id = int(raw_chrt)
    except (TypeError, ValueError):
        chrt_id = None
    return skus, sibling_codes, chrt_id


def _external_pick_current_size_free(
    db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, wb_warehouse_id: int,
    skus: list[SKU], sibling_codes: set[str],
) -> int:
    """Fresh canonical free for the candidate WB size, ignoring only WB sales-pause.

    The calculation mirrors final WB chrt truth (physical - storage - NEW/PICKING -
    terminal hold) but deliberately does not force zero when
    sales are paused. Clearing the order-floor cache makes a long uploaded list observe
    orders that arrived while earlier rows were being allocated.
    """
    if not skus:
        return 0
    try:
        selected = int(wb_warehouse_id)
    except (TypeError, ValueError):
        selected = None
    db.info.pop(('fc_active_order_floor', str(client_id), str(warehouse_id), selected), None)
    truth = stock_truth_for_skus(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_ids=[sku.id for sku in skus],
        wb_warehouse_id=wb_warehouse_id, include_movement=False,
    )
    truth_groups: dict[tuple[str, ...], dict] = {}
    for sku in skus:
        state = truth.get(str(sku.id), {})
        identity = tuple(sorted(str(x) for x in (state.get('identity_sku_ids') or [str(sku.id)])))
        truth_groups.setdefault(identity, state)
    states = list(truth_groups.values())
    def total(name: str) -> Decimal:
        return sum((Decimal(str(state.get(name) or 0)) for state in states), Decimal(0))
    physical = total('physical')
    storage = min(physical, max(Decimal(0), total('storage_only')))
    ledger_sale = total('ledger_sale_reserved')
    ledger_picking = total('ledger_picking_reserved')
    accounting_pending = total('accounting_pending')
    floor = active_order_floor_for_identity(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_ids=[sku.id for sku in skus],
        barcodes=set(sibling_codes), wb_warehouse_id=wb_warehouse_id,
    )
    sale_reserved = max(ledger_sale, Decimal(str(floor.get('new') or 0)))
    picking_reserved = max(ledger_picking, Decimal(str(floor.get('picking') or 0)))
    free = max(Decimal(0), physical - storage - sale_reserved - picking_reserved - accounting_pending)
    return max(0, int(free))

# FC_K9R38_FREE_ONLY_DIRECT_WRITEOFF: new external tasks physically ship only the residual
# stock after STORAGE_ONLY + NEW/PICKING + accounting-pending; no r37 ACTIVE hold is created.
def _external_pick_writeoff_one(
    db: Session, *, batch_id: str, task_number: str, request_row: dict, client_id: uuid.UUID, warehouse_id: uuid.UUID,
    wb_warehouse_id: int, sibling_codes: set[str] | None = None, chrt_id_hint: int | None = None,
    identity_hint: dict | None = None, performed_by: str | None = None,
) -> dict:
    """Write off only genuinely free physical units for one uploaded barcode.

    Caller holds the canonical sale-reserve advisory locks for every candidate identity.
    NEW/PICKING reservations and accounting-pending units are never changed or consumed.
    The physical write-off and its inventory history stay uncommitted until the batch WB
    absolute targets have been written and verified.
    """
    barcode = str(request_row['barcode'])
    requested = int(request_row['quantity'])
    skus, resolved_siblings, chrt_id = _external_pick_identity_candidates(
        db, client_id=client_id, warehouse_id=warehouse_id, wb_warehouse_id=wb_warehouse_id, barcode=barcode,
        sibling_codes_override=sibling_codes, chrt_id_override=chrt_id_hint, identity_override=identity_hint,
    )
    request_id = str(uuid.uuid4())
    identity = identity_hint if identity_hint is not None else (wb_product_identity(db, client_id, barcode) or {})
    if not skus:
        db.execute(text("""
            INSERT INTO fc_external_pick_requests(id,batch_id,row_number,barcode,requested_quantity,allocated_quantity,shortage_quantity,article,size_name,chrt_id,note)
            VALUES(:id,:batch,:row,:barcode,:requested,0,:requested,:article,:size,:chrt,:note)
        """), {'id':request_id,'batch':batch_id,'row':int(request_row['row_number']),'barcode':barcode,'requested':requested,
                'article':identity.get('article'),'size':identity.get('size'),'chrt':chrt_id,
                'note':'Свободный локальный остаток по этому ШК/размеру не найден'})
        return {'requested':requested,'allocated':0,'shortage':requested,'barcode':barcode,'chrt_id':chrt_id,
                'siblings':set(resolved_siblings or {barcode}),'representative_sku_id':None}

    # Re-resolve under the already-held canonical identity locks.
    skus, resolved_siblings, chrt_id = _external_pick_identity_candidates(
        db, client_id=client_id, warehouse_id=warehouse_id, wb_warehouse_id=wb_warehouse_id, barcode=barcode,
        sibling_codes_override=sibling_codes, chrt_id_override=chrt_id_hint, identity_override=identity_hint,
    )
    sku_ids = [sku.id for sku in skus]
    available_global = _external_pick_current_size_free(
        db, client_id=client_id, warehouse_id=warehouse_id, wb_warehouse_id=wb_warehouse_id,
        skus=skus, sibling_codes=resolved_siblings,
    )
    requested_take = min(requested, max(0, int(available_global)))
    balance_rows = db.execute(select(InventoryBalance).where(
        InventoryBalance.client_id == client_id,
        InventoryBalance.warehouse_id == warehouse_id,
        InventoryBalance.sku_id.in_(sku_ids),
        InventoryBalance.location_id.is_not(None),
        InventoryBalance.quantity > 0,
    ).with_for_update()).scalars().all() if sku_ids else []
    loc_ids = {row.location_id for row in balance_rows if row.location_id}
    cont_ids = {row.container_id for row in balance_rows if row.container_id}
    loc_map = {str(row.id):row for row in db.execute(select(Location).where(Location.id.in_(list(loc_ids)))).scalars().all()} if loc_ids else {}
    cont_map = {str(row.id):row for row in db.execute(select(Container).where(Container.id.in_(list(cont_ids)))).scalars().all()} if cont_ids else {}
    reserve_map, storage_map, _legacy_external_map = _external_pick_bucket_maps(
        db, client_id=client_id, warehouse_id=warehouse_id, sku_ids=sku_ids
    )
    bucket_rows = []
    for balance in balance_rows:
        sid = str(balance.sku_id); lid = str(balance.location_id); cid = str(balance.container_id) if balance.container_id else ''
        physical = max(Decimal(0), Decimal(str(balance.quantity or 0)))
        reserved, pending = reserve_map.get((sid,lid,cid),(Decimal(0),Decimal(0)))
        storage = min(physical, max(Decimal(0), storage_map.get((sid,lid,cid),Decimal(0))))
        # r38 DIRECT WRITE-OFF: reservations NEW/PICKING + terminal pending + storage-only
        # remain untouched. Only the exact residual physical units may be shipped.
        free = max(Decimal(0), physical - storage - reserved - pending)
        if free <= 0:
            continue
        loc = loc_map.get(lid); cont = cont_map.get(cid) if cid else None
        bucket_rows.append((
            _external_pick_natural(getattr(loc,'code',None)),
            _external_pick_natural(getattr(cont,'name',None) or getattr(cont,'barcode',None)),
            balance, free,
        ))
    bucket_rows.sort(key=lambda x:(x[0],x[1],str(x[2].sku_id)))

    remaining = requested_take
    planned: list[tuple[InventoryBalance,int]] = []
    for _loc_sort, _box_sort, balance, free in bucket_rows:
        if remaining <= 0:
            break
        take = min(int(free), remaining)
        if take <= 0:
            continue
        planned.append((balance,take)); remaining -= take
    allocated = sum(take for _balance,take in planned)
    shortage = max(0, requested - allocated)
    representative = skus[0]
    note = None
    if allocated <= 0:
        note = 'Свободного FBS остатка нет; резервы заказов не тронуты'
    elif shortage > 0:
        note = f'Списано только {allocated} из {requested}: свободного остатка больше нет'

    if allocated > 0:
        writeoff_lines = [StockLine(
            sku_id=balance.sku_id, quantity=Decimal(take),
            source_location_id=balance.location_id, source_container_id=balance.container_id,
            reason=f'EXTERNAL_PICK_TASK_WRITE_OFF:{task_number}',
        ) for balance,take in planned]
        execute_operation(db, OperationCreate(
            client_id=client_id, warehouse_id=warehouse_id,
            operation_type='SHIPPING',
            idempotency_key=f'external-pick-task:{batch_id}:{barcode}'[:128],
            performed_by=(performed_by or None),
            reference=f'Сборочное задание №{task_number}',
            comment=f'Списано по сборочному листу №{task_number}. Резервы NEW/PICKING не изменялись.',
            lines=writeoff_lines,
        ), commit=False)

        # FC_K9R38_DIRECT_WRITEOFF_CLASSIFICATION_MIRROR: SHIPPING already changed
        # InventoryBalance.  Rewrite only the technical mirror for the exact buckets
        # that were consumed, preserving STORAGE_ONLY and the live reservation ledger.
        # Do NOT call reconcile_stock_bucket here: its generic negative-delta recovery
        # consumes STORAGE_ONLY before FBS, which would reclassify warehouse-only stock.
        for balance,take in planned:
            sid = str(balance.sku_id)
            lid = str(balance.location_id)
            cid = str(balance.container_id) if balance.container_id else ''
            reserved, pending = reserve_map.get((sid,lid,cid),(Decimal(0),Decimal(0)))
            live_hold = max(Decimal(0), reserved) + max(Decimal(0), pending)
            new_physical = max(Decimal(0), Decimal(str(balance.quantity or 0)))
            storage = min(new_physical, max(Decimal(0), storage_map.get((sid,lid,cid),Decimal(0))))
            # Because `take` was capped by physical-storage-reserved-pending, these
            # protected categories fit entirely inside the new physical balance.
            technical_reserved = min(max(Decimal(0), new_physical - storage), live_hold)
            fbs_after = max(Decimal(0), new_physical - storage - technical_reserved)
            mirror_params = {
                'client':str(client_id),'warehouse':str(warehouse_id),'sku':sid,'location':lid,'container':cid,
                'fbs':fbs_after,'storage':storage,'reserved':technical_reserved,'physical':new_physical,
            }
            existing_mirror = db.execute(text(
                "SELECT 1 FROM fc_stocks_v2 WHERE client_id=:client AND warehouse_id=:warehouse "
                "AND sku_id=:sku AND location_id=:location AND container_key=:container FOR UPDATE"
            ), mirror_params).first()
            if existing_mirror:
                db.execute(text("""
                    UPDATE fc_stocks_v2
                    SET fbs_available=:fbs, storage_only=:storage, reserved=:reserved,
                        physical_snapshot=:physical, version=version+1, updated_at=CURRENT_TIMESTAMP
                    WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
                      AND location_id=:location AND container_key=:container
                """), mirror_params)
            else:
                db.execute(text("""
                    INSERT INTO fc_stocks_v2(
                        client_id,warehouse_id,sku_id,location_id,container_key,
                        fbs_available,storage_only,reserved,physical_snapshot
                    ) VALUES(:client,:warehouse,:sku,:location,:container,:fbs,:storage,:reserved,:physical)
                """), mirror_params)

        for balance,take in planned:
            db.execute(text("""
                INSERT INTO fc_external_pick_allocations(
                    id,batch_id,request_id,client_id,warehouse_id,sku_id,barcode,location_id,container_id,quantity,status
                ) VALUES(:id,:batch,:request,:client,:warehouse,:sku,:barcode,:location,:container,:quantity,'WRITTEN_OFF')
            """), {
                'id':str(uuid.uuid4()),'batch':batch_id,'request':request_id,'client':str(client_id),'warehouse':str(warehouse_id),
                'sku':str(balance.sku_id),'barcode':barcode,'location':str(balance.location_id),
                'container':str(balance.container_id) if balance.container_id else None,'quantity':take,
            })
        db.flush()
        archive_consumed_empty_containers(
            db,client_id,warehouse_id,
            [balance.container_id for balance,_take in planned if balance.container_id],
            actor=performed_by,reason='EXTERNAL_PICK_WRITE_OFF_EMPTY_BOX',
        )

    db.execute(text("""
        INSERT INTO fc_external_pick_requests(id,batch_id,row_number,barcode,requested_quantity,allocated_quantity,shortage_quantity,article,size_name,chrt_id,note)
        VALUES(:id,:batch,:row,:barcode,:requested,:allocated,:shortage,:article,:size,:chrt,:note)
    """), {'id':request_id,'batch':batch_id,'row':int(request_row['row_number']),'barcode':barcode,'requested':requested,
            'allocated':allocated,'shortage':shortage,'article':identity.get('article') or getattr(representative,'article',None),
            'size':identity.get('size') or getattr(representative,'size',None),'chrt':chrt_id,'note':note})
    db.flush()
    return {'requested':requested,'allocated':allocated,'shortage':shortage,'barcode':barcode,'chrt_id':chrt_id,
            'siblings':set(resolved_siblings or {barcode}),'representative_sku_id':str(representative.id)}

def _external_pick_workbook(db: Session, *, batch_id: str, client_id: uuid.UUID, warehouse_id: uuid.UUID) -> Workbook:
    batch = db.execute(text("""
        SELECT * FROM fc_external_pick_batches
        WHERE id=:id AND client_id=:client AND warehouse_id=:warehouse
    """), {'id':batch_id,'client':str(client_id),'warehouse':str(warehouse_id)}).mappings().first()
    if not batch:
        raise HTTPException(status_code=404, detail='Внешний сборочный лист не найден')
    requests = db.execute(text("""
        SELECT * FROM fc_external_pick_requests WHERE batch_id=:batch ORDER BY row_number,barcode
    """), {'batch':batch_id}).mappings().all()
    allocations = db.execute(text("""
        SELECT a.*,s.article,s.size AS sku_size,s.barcode AS stock_barcode,
               l.code AS location_code,c.name AS container_name,c.barcode AS container_barcode
        FROM fc_external_pick_allocations a
        JOIN skus s ON CAST(s.id AS varchar)=a.sku_id
        JOIN locations l ON CAST(l.id AS varchar)=a.location_id
        LEFT JOIN containers c ON CAST(c.id AS varchar)=a.container_id
        WHERE a.batch_id=:batch AND a.status='WRITTEN_OFF'
    """), {'batch':batch_id}).mappings().all()
    from app.services.box_routes import freeze_rows
    from app.services.picking_route import route_key
    allocations=freeze_rows(db,"FBS",batch_id,client_id,warehouse_id,allocations)
    request_by_id = {str(row['id']):row for row in requests}

    wb = Workbook()
    ws = wb.active
    # FC_K9R38_OUTPUT_ACTUAL_WRITEOFF: output rows come only from WRITTEN_OFF allocations.
    ws.title = 'Сборочное задание FBS'
    headers = ['Стеллаж','Этаж','Ячейка','Адрес','Короб','Артикул','Размер','ШК из файла','ШК в базе','Количество','Секция','Ряд','Место в ряду']
    ws.append(headers)
    # FC_K9R37_EXTERNAL_PICK_XLSX_GROUPED: one practical pick row per
    # product/size + exact cell/box, even when the same WB size is represented
    # by several local alias barcodes. This keeps the warehouse sheet compact.
    grouped_allocations: dict[tuple[str,str,str,str,str], dict] = {}
    for row in allocations:
        req = request_by_id.get(str(row.get('request_id')), {})
        address = str(row.get('location_code') or '')
        box = str(row.get('container_name') or row.get('container_barcode') or '')
        article = str(row.get('article') or '')
        size_name = str(row.get('sku_size') or req.get('size_name') or '')
        chrt_key = str(req.get('chrt_id') or '')
        source_barcode = str(req.get('barcode') or row.get('barcode') or '').strip()
        # Group different barcode aliases of one WB size together, but never merge
        # distinct chrtId values that happen to share article/size/address.
        key = (address, box, article, size_name, chrt_key)
        item = grouped_allocations.setdefault(key, {'quantity': 0, 'stock_barcodes': set(), 'source_barcodes': set(), 'route':dict(row)})
        item['quantity'] += int(Decimal(str(row.get('quantity') or 0)))
        if source_barcode:
            item['source_barcodes'].add(source_barcode)
        stock_barcode = str(row.get('stock_barcode') or '').strip()
        if stock_barcode:
            item['stock_barcodes'].add(stock_barcode)
    for (address, box, article, size_name, chrt_key), item in sorted(
        grouped_allocations.items(),
        key=lambda pair: (route_key(pair[1]['route']),pair[0][2],pair[0][3],pair[0][4]),
    ):
        parts = [x for x in re.split(r'[-/]', address) if x != '']
        rack = parts[0] if len(parts) >= 1 else ''
        floor = parts[1] if len(parts) >= 2 else ''
        cell = parts[2] if len(parts) >= 3 else ''
        ws.append([
            rack, floor, cell, address, box, article, size_name, ', '.join(sorted(item['source_barcodes'])),
            ', '.join(sorted(item['stock_barcodes'])), int(item['quantity']),
            item['route'].get('route_section') or '',item['route'].get('route_row') or '',item['route'].get('route_position') or '',
        ])

    ws2 = wb.create_sheet('Не хватило')
    ws2.append(['ШК','Артикул','Размер','Запрошено','Списано','Не хватило','Причина'])
    for row in requests:
        shortage = int(Decimal(str(row.get('shortage_quantity') or 0)))
        if shortage <= 0:
            continue
        ws2.append([row.get('barcode') or '',row.get('article') or '',row.get('size_name') or '',
                    int(Decimal(str(row.get('requested_quantity') or 0))),int(Decimal(str(row.get('allocated_quantity') or 0))),
                    shortage,row.get('note') or 'Недостаточно свободного FBS остатка'])

    ws_failed = wb.create_sheet('Не обработано WB')
    ws_failed.append(['ШК','Артикул','Размер','Запрошено','Не списано из-за WB','chrtId','Причина'])
    for row in requests:
        failed = int(Decimal(str(row.get('failed_quantity') or 0)))
        if failed <= 0:
            continue
        ws_failed.append([row.get('barcode') or '',row.get('article') or '',row.get('size_name') or '',
                          int(Decimal(str(row.get('requested_quantity') or 0))),failed,row.get('chrt_id') or '',
                          row.get('note') or 'WB не подтвердил синхронное списание; локальный остаток не изменён'])

    ws3 = wb.create_sheet('Исходный запрос')
    ws3.append(['ШК','Артикул','Размер','Запрошено','Списано','Не хватило','Не обработано WB','Статус позиции','chrtId','Комментарий'])
    for row in requests:
        requested_q=int(Decimal(str(row.get('requested_quantity') or 0))); allocated_q=int(Decimal(str(row.get('allocated_quantity') or 0)))
        shortage_q=int(Decimal(str(row.get('shortage_quantity') or 0))); failed_q=int(Decimal(str(row.get('failed_quantity') or 0)))
        if failed_q>0: position_status='Не обработано из-за WB/проверки'
        elif allocated_q>=requested_q and requested_q>0: position_status='Списано полностью'
        elif allocated_q>0: position_status='Списано частично'
        else: position_status='Нет свободного остатка'
        ws3.append([row.get('barcode') or '',row.get('article') or '',row.get('size_name') or '',
                    requested_q,allocated_q,shortage_q,failed_q,position_status,row.get('chrt_id') or '',row.get('note') or ''])

    ws4 = wb.create_sheet('Итоги')
    ws4.append(['Показатель','Значение'])
    ws4.append(['Номер сборочного задания',batch.get('task_number') or ''])
    ws4.append(['ID операции',batch_id])
    ws4.append(['Статус',batch.get('status') or ''])
    ws4.append(['Исходный файл',batch.get('source_filename') or ''])
    ws4.append(['Позиций в файле',int(batch.get('positions_total') or len(requests))])
    ws4.append(['Позиций с итоговым статусом',len(requests)])
    ws4.append(['Запрошено, шт.',int(Decimal(str(batch.get('requested_units') or 0)))])
    ws4.append(['Фактически списано, шт.',int(Decimal(str(batch.get('allocated_units') or 0)))])
    ws4.append(['Не хватило, шт.',int(Decimal(str(batch.get('shortage_units') or 0)))])
    ws4.append(['Не обработано из-за WB, шт.',int(Decimal(str(batch.get('failed_units') or 0)))])
    ws4.append(['WB синхронизация',batch.get('wb_sync_status') or ''])
    ws4.append(['Ошибка/примечание WB',batch.get('wb_sync_error') or ''])
    ws4.append(['Правило','Каждая позиция фиксируется отдельно только после подтверждения WB. Ошибка одного chrtId не откатывает уже успешно подтверждённые позиции. Резервы NEW/PICKING не меняются.'])

    header_fill = PatternFill('solid', fgColor='D9EAD3')
    for sheet in wb.worksheets:
        sheet.freeze_panes = 'A2'
        if sheet.max_column:
            sheet.auto_filter.ref = f'A1:{get_column_letter(sheet.max_column)}{max(1,sheet.max_row)}'
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(vertical='center', wrap_text=True)
        for row in sheet.iter_rows():
            for cell in row:
                cell.alignment = Alignment(vertical='top', wrap_text=True)
    widths = [12,10,12,24,24,18,16,22,22,12,16,10,14]
    for idx,width in enumerate(widths,1): ws.column_dimensions[get_column_letter(idx)].width=width
    for col,width in {'A':22,'B':18,'C':16,'D':14,'E':14,'F':14,'G':52}.items(): ws2.column_dimensions[col].width=width
    for col,width in {'A':22,'B':18,'C':16,'D':14,'E':18,'F':16,'G':58}.items(): ws_failed.column_dimensions[col].width=width
    for col,width in {'A':22,'B':18,'C':16,'D':14,'E':14,'F':14,'G':18,'H':28,'I':16,'J':58}.items(): ws3.column_dimensions[col].width=width
    ws4.column_dimensions['A'].width=34; ws4.column_dimensions['B'].width=90
    for sheet in (ws,ws2,ws_failed,ws3):
        for row in sheet.iter_rows(min_row=2):
            if sheet is ws:
                row[7].number_format='@'; row[8].number_format='@'
            else:
                row[0].number_format='@'
    return wb


@router.get('/external-picking/progress/{progress_id}')
def external_pick_progress(progress_id: str):
    row = _external_pick_progress_get(progress_id)
    if not row:
        return {'ok': True, 'stage': 'WAITING', 'percent': 0, 'message': 'Ожидаем запуск обработки файла', 'elapsed_seconds': 0}
    return {'ok': True, **row}


@router.get('/external-picking/template.xlsx')
def external_pick_template():
    wb = Workbook(); ws = wb.active; ws.title = 'Сборочное задание FBS'
    ws['A1'] = 'Номер сборочного задания'; ws['B1'] = ''
    ws.append([])
    ws.append(['ШК','Количество'])
    ws.freeze_panes='A4'; ws.auto_filter.ref='A3:B3'; ws.column_dimensions['A'].width=28; ws.column_dimensions['B'].width=20
    for cell in ws[1]: cell.font=Font(bold=True); cell.fill=PatternFill('solid',fgColor='FFF2CC')
    for cell in ws[3]: cell.font=Font(bold=True); cell.fill=PatternFill('solid',fgColor='D9EAD3')
    ws['B1'].number_format='@'
    for row in range(4,20004): ws.cell(row=row,column=1).number_format='@'
    out=io.BytesIO(); wb.save(out); out.seek(0)
    return StreamingResponse(out,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':'attachment; filename="FulfillCore_FBS_pick_task_template.xlsx"'})


def _external_pick_safe_filename(value: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+','_',str(value or '').strip())
    cleaned = re.sub(r'\s+','_',cleaned).strip('._')
    return cleaned[:80] or 'task'


@router.post('/external-picking/import')
def external_pick_import(
    client_id: uuid.UUID = Form(...), warehouse_id: uuid.UUID = Form(...), wb_warehouse_id: int | None = Form(None),
    performed_by: str | None = Form(None), progress_id: str | None = Form(None),
    file: UploadFile = File(...), db: Session = Depends(get_db),
):
    """Physically write off free stock and synchronously lower WB absolute targets.

    r38 synchronization guarantee: the inventory transaction is committed only after WB
    has accepted and read-after-write confirmed every affected chrtId target. If WB cannot
    confirm, the local transaction is rolled back and no picking sheet is issued.
    """
    _external_pick_progress_set(progress_id, stage='RECEIVED', percent=2, message='Файл принят сервером. Проверяем шаблон и номер задания…')
    ensure_runtime_schema(db)
    cred = _active_wb_credential(db, client_id)
    if not cred:
        raise HTTPException(status_code=409, detail='Подключение WB для выбранного клиента не настроено')
    selected_wb_id = _work_wb_warehouse_id(cred, wb_warehouse_id, warehouse_id)
    if not selected_wb_id:
        raise HTTPException(status_code=409, detail='Для выбранного физического склада не назначен рабочий склад WB')
    filename = str(file.filename or '')
    if filename and not filename.lower().endswith('.xlsx'):
        raise HTTPException(status_code=400, detail='Загрузите заполненный шаблон Excel .xlsx')
    content = file.file.read()
    source_sha256 = hashlib.sha256(content).hexdigest()
    task_number, rows, source_rows = _external_pick_parse_rows(content)
    requested_preview = sum(int(row['quantity']) for row in rows)
    _external_pick_progress_set(
        progress_id, stage='PARSED', percent=8,
        message=f'Шаблон проверен: задание №{task_number}, {source_rows} строк, {len(rows)} уникальных ШК, {requested_preview} шт.',
        task_number=task_number, source_rows=source_rows, positions=len(rows), requested_units=requested_preview,
    )

    # FC_K9R38_DUPLICATE_TASK_GUARD: task number is unique per client/warehouse forever;
    # the same bytes are idempotent and a different file under that number is rejected.
    existing = db.execute(text("""
        SELECT id,task_number,source_sha256,status,requested_units,allocated_units,shortage_units,wb_sync_status,created_at
        FROM fc_external_pick_batches
        WHERE client_id=:client AND warehouse_id=:warehouse AND task_number=:task
        ORDER BY created_at DESC LIMIT 1
    """), {'client':str(client_id),'warehouse':str(warehouse_id),'task':task_number}).mappings().first()
    if existing:
        if str(existing.get('source_sha256') or '') == source_sha256:
            _external_pick_progress_set(progress_id, stage='DONE', percent=100, message=f'Задание №{task_number} уже обработано. Повторного списания нет; готовим существующий результат.', task_number=task_number)
            return {'ok':True,'duplicate':True,'batch_id':str(existing['id']),'task_number':task_number,'status':existing.get('status'),
                    'requested_units':int(Decimal(str(existing.get('requested_units') or 0))),
                    'allocated_units':int(Decimal(str(existing.get('allocated_units') or 0))),
                    'shortage_units':int(Decimal(str(existing.get('shortage_units') or 0))),
                    'wb_sync_status':existing.get('wb_sync_status') or 'UNKNOWN',
                    'message':'Это сборочное задание уже обработано. Повторного списания не было; скачивается существующий результат.'}
        raise HTTPException(status_code=409, detail=f'Сборочное задание №{task_number} уже существует с другим файлом. Повторное списание запрещено.')

    requested_codes = [row['barcode'] for row in rows]
    _external_pick_progress_set(progress_id, stage='CATALOG', percent=12, message=f'Проверяем {len(rows)} уникальных ШК и актуальные карточки WB…', task_number=task_number)
    internal = _wb_internal_product_codes(db, client_id=client_id, codes=requested_codes)
    if internal:
        raise HTTPException(status_code=400, detail='В файле есть внутренние ШК коробов/ячеек: ' + ', '.join(sorted(internal)[:10]))

    wb_client = WBClient(decrypt_secret(cred.encrypted_api_key))
    # FC_K9R40_EXTERNAL_PICK_CACHE_FIRST_MAP: large uploaded tasks must not download the
    # whole WB catalogue merely because they contain >8 barcodes. FulfillCore already
    # keeps complete WB size/chrt sibling sets locally. Resolve that cache first and ask
    # WB only for the genuinely unresolved uploaded codes.
    _external_pick_progress_set(progress_id, stage='CACHE_MAP', percent=16, message=f'Сопоставляем {len(requested_codes)} ШК по локальной карте карточек FulfillCore…', task_number=task_number)
    sibling_map, chrt_map = _external_pick_cached_size_map(db, client_id=client_id, barcodes=requested_codes)
    unresolved_codes = [code for code in requested_codes if code not in chrt_map]
    fresh_size_rows: list[dict] = []
    cached_count = len(requested_codes) - len(unresolved_codes)
    if unresolved_codes:
        # Keep live lookups targeted while the unresolved tail is small. Calling
        # _wb_fresh_size_rows with >8 codes intentionally performs a complete catalogue
        # scan, so feed it chunks of <=8 here and expose progress between chunks.
        if len(unresolved_codes) <= 32:
            chunks = [unresolved_codes[i:i+8] for i in range(0, len(unresolved_codes), 8)]
            for idx, chunk in enumerate(chunks, 1):
                pct = 17 + int(5 * (idx - 1) / max(1, len(chunks)))
                _external_pick_progress_set(
                    progress_id, stage='WB_CATALOG', percent=pct,
                    message=f'Локально найдено {cached_count}/{len(requested_codes)} ШК. Уточняем в WB только отсутствующие: пакет {idx}/{len(chunks)} ({len(unresolved_codes)} ШК)…',
                    task_number=task_number, cached_positions=cached_count, unresolved_positions=len(unresolved_codes),
                )
                fresh_size_rows.extend(_wb_fresh_size_rows(wb_client, chunk))
        else:
            _external_pick_progress_set(
                progress_id, stage='WB_CATALOG', percent=18,
                message=f'Локально найдено {cached_count}/{len(requested_codes)} ШК; {len(unresolved_codes)} отсутствуют в карте. Выполняем один полный каталог WB только для восстановления этих привязок…',
                task_number=task_number, cached_positions=cached_count, unresolved_positions=len(unresolved_codes),
            )
            fresh_size_rows = _wb_fresh_size_rows(wb_client, unresolved_codes)
        for size_row in fresh_size_rows:
            try: cid = int(size_row.get('chrtID') or size_row.get('chrtId') or size_row.get('chrt_id'))
            except Exception: continue
            codes = {normalize_product_barcode(str(x))[0] for x in (size_row.get('skus') or []) if str(x or '').strip()}
            codes.discard('')
            for code in codes:
                if code in requested_codes:
                    chrt_map[code] = cid; sibling_map[code] = set(codes)
    _external_pick_progress_set(
        progress_id, stage='WB_CATALOG', percent=23,
        message=f'Карта размеров готова: {len(chrt_map)}/{len(requested_codes)} ШК имеют chrtId. Продолжаем без полного повторного скана известных карточек.',
        task_number=task_number, mapped_positions=len(chrt_map), positions=len(requested_codes),
    )
    identity_map = wb_product_identity_map(db, client_id, set(requested_codes)) if requested_codes else {}

    _external_pick_progress_set(progress_id, stage='MATCHING', percent=24, message='Сопоставляем ШК с товаром FulfillCore и готовим безопасные блокировки остатков…', task_number=task_number)
    # Collect all canonical identity locks first. Order reservation uses these exact locks,
    # therefore NEW/PICKING cannot consume the same free units while this task is written off.
    lock_keys: set[str] = set()
    for row in rows:
        candidates, _siblings, _chrt = _external_pick_identity_candidates(
            db, client_id=client_id, warehouse_id=warehouse_id, wb_warehouse_id=int(selected_wb_id), barcode=row['barcode'],
            sibling_codes_override=sibling_map.get(row['barcode']), chrt_id_override=chrt_map.get(row['barcode']),
            identity_override=identity_map.get(row['barcode']),
        )
        for sku in candidates:
            lock_keys.add(canonical_stock_identity_lock_key(db, client_id=client_id, sku_id=sku.id))
    db.rollback()  # release catalogue read transaction before the protected write phase

    # FC_K9R38_FRESH_NEW_PREFLIGHT: before any destructive warehouse change, refresh
    # THIS exact physical+seller WB scope and reserve newly arrived orders. If that
    # safety read fails, abort with zero local writeoff rather than consuming stock
    # that WB may already have sold but FulfillCore has not reserved yet.
    _external_pick_progress_set(progress_id, stage='NEW_ORDERS', percent=30, message='Проверяем свежие новые заказы WB перед списанием. Резервы Новые/На сборке не трогаем…', task_number=task_number)
    from app.services.wb_sale_reservation_worker import sync_new_for_scope
    preflight = sync_new_for_scope(str(cred.id), str(warehouse_id), str(int(selected_wb_id)))
    if not int((preflight or {}).get('sync_ok') or 0):
        _external_pick_progress_set(progress_id, stage='ERROR', percent=30, message='Не удалось подтвердить свежие новые заказы WB. Локальный остаток не изменён.', task_number=task_number)
        raise HTTPException(
            status_code=503,
            detail='Не удалось подтвердить свежие новые заказы WB перед списанием. Локальный остаток не изменён; повторите загрузку позже.',
        )

    batch_id = str(uuid.uuid4())
    requested_total = sum(int(row['quantity']) for row in rows)
    results: list[dict] = []
    affected_sku_ids: set[str] = set()
    affected_codes: set[str] = set()
    affected_siblings: set[str] = set()

    _external_pick_progress_set(progress_id, stage='WRITE_OFF', percent=35, message=f'Начинаем расчёт и списание только свободного остатка: 0/{len(rows)} поз.', task_number=task_number, current=0, total=len(rows))
    try:
        with ExitStack() as locks:
            for key in sorted(lock_keys):
                locks.enter_context(distributed_lock(db, f'sale-reserve:{client_id}:{warehouse_id}:{key}', blocking=True))

            inserted = db.execute(text("""
                INSERT INTO fc_external_pick_batches(
                    id,client_id,warehouse_id,wb_warehouse_id,task_number,source_filename,source_sha256,performed_by,status,
                    requested_units,allocated_units,shortage_units,source_rows,wb_sync_status
                ) VALUES(:id,:client,:warehouse,:wb_wh,:task,:filename,:sha,:by,'PROCESSING',:requested,0,:requested,:source_rows,'PREPARING')
                ON CONFLICT DO NOTHING RETURNING id
            """), {'id':batch_id,'client':str(client_id),'warehouse':str(warehouse_id),'wb_wh':int(selected_wb_id),'task':task_number,
                    'filename':filename[:512],'sha':source_sha256,'by':(performed_by or None),'requested':requested_total,'source_rows':source_rows,'positions':len(rows)}).scalar_one_or_none()
            if not inserted:
                db.rollback()
                raise HTTPException(status_code=409, detail=f'Сборочное задание №{task_number} уже обрабатывается. Повторное списание заблокировано.')

            allocated_total = 0; shortage_total = 0
            for row_index, row in enumerate(rows, start=1):
                result = _external_pick_writeoff_one(
                    db, batch_id=batch_id, task_number=task_number, request_row=row, client_id=client_id, warehouse_id=warehouse_id,
                    wb_warehouse_id=int(selected_wb_id), sibling_codes=sibling_map.get(row['barcode']),
                    chrt_id_hint=chrt_map.get(row['barcode']), identity_hint=identity_map.get(row['barcode']),
                    performed_by=performed_by,
                )
                results.append(result)
                allocated_total += int(result['allocated']); shortage_total += int(result['shortage'])
                if row_index == 1 or row_index == len(rows) or row_index % 5 == 0:
                    pct = 35 + int(35 * row_index / max(1, len(rows)))
                    _external_pick_progress_set(
                        progress_id, stage='WRITE_OFF', percent=pct,
                        message=f'Рассчитываем и списываем свободный остаток: {row_index}/{len(rows)} поз. · подготовлено {allocated_total} шт. · нехватка {shortage_total} шт.',
                        task_number=task_number, current=row_index, total=len(rows), allocated_units=allocated_total, shortage_units=shortage_total,
                    )
                if int(result['allocated']) > 0 and result.get('representative_sku_id'):
                    affected_sku_ids.add(str(result['representative_sku_id']))
                    affected_codes.add(str(result['barcode']))
                    affected_siblings.update(str(x) for x in (result.get('siblings') or {result['barcode']}) if str(x or '').strip())

            db.flush()
            if allocated_total <= 0:
                db.execute(text("""
                    UPDATE fc_external_pick_batches
                    SET allocated_units=0,shortage_units=:shortage,status='NO_STOCK',wb_sync_status='NOT_REQUIRED',wb_synced_at=CURRENT_TIMESTAMP
                    WHERE id=:id
                """), {'shortage':shortage_total,'id':batch_id})
                db.commit()
                _external_pick_progress_set(progress_id, stage='DONE', percent=100, message=f'Задание №{task_number} обработано: свободного остатка для списания нет.', task_number=task_number, allocated_units=0, shortage_units=shortage_total)
                return {'ok':True,'batch_id':batch_id,'task_number':task_number,'status':'NO_STOCK','requested_units':requested_total,
                        'allocated_units':0,'shortage_units':shortage_total,'wb_sync_status':'NOT_REQUIRED','wb_synced_positions':0}

            # Calculate the FINAL absolute WB size targets from the same uncommitted physical
            # state that will be committed. This includes all untouched NEW/PICKING reserves.
            _external_pick_progress_set(progress_id, stage='TARGETS', percent=72, message=f'Списание рассчитано: {allocated_total} шт. Формируем новые абсолютные цели WB…', task_number=task_number, allocated_units=allocated_total, shortage_units=shortage_total)
            from app.services.stock_outbox_worker import _wb_size_targets
            targets, sibling_truth, chrt_by_code = _wb_size_targets(
                db, client_id=str(client_id), warehouse_id=str(warehouse_id), external_wh=str(int(selected_wb_id)),
                codes=sorted(affected_codes), extra_size_rows=fresh_size_rows,
            )
            sync_by_chrt: dict[int, dict] = {}
            for result in results:
                if int(result.get('allocated') or 0) <= 0:
                    continue
                code = str(result['barcode'])
                raw_chrt = chrt_by_code.get(code) or result.get('chrt_id')
                if raw_chrt is None or code not in targets:
                    raise RuntimeError(f'Не удалось определить подтверждаемую цель WB/chrtId для ШК {code}; локальное списание отменено')
                chrt_id = int(raw_chrt); target = max(0,int(targets[code]))
                previous = sync_by_chrt.get(chrt_id)
                if previous and int(previous['target']) != target:
                    raise RuntimeError(f'chrtId {chrt_id} получил две разные конечные цели WB')
                sync_by_chrt.setdefault(chrt_id, {'code':code,'target':target,'sku_id':str(result['representative_sku_id'])})

            # FC_K9R38_WB_PUBLISH_SCOPE_LOCK: sale-reserve locks are already held.
            # Take the same WB scope lock used by the outbox only for the final publish
            # phase. If an older worker is already writing, let it finish first; if it
            # was merely claimed, our committed newer generation makes it fail its r34
            # claim/latest checks after this scope lock is released.
            _external_pick_progress_set(progress_id, stage='WB_LOCK', percent=78, message=f'Ожидаем безопасное окно обновления WB для {len(sync_by_chrt)} размеров/chrtId…', task_number=task_number, wb_positions=len(sync_by_chrt))
            locks.enter_context(distributed_lock(
                db, f'wb-stock-publish:{client_id}:{warehouse_id}:{int(selected_wb_id)}', blocking=True
            ))

            # FC_K9R38_WB_NEVER_RAISE_DURING_WRITEOFF: this flow is a physical
            # deduction. Even after the fresh NEW-order preflight, an order may reach WB
            # in the tiny interval before this publish lock. WB may therefore already be
            # lower than our still-unaware local canonical target. Never raise WB as part
            # of a write-off: cap the synchronous publish to the current WB fact. A normal
            # r27/r36 guarded reconcile may raise later only after a newer NEW-order sync.
            _external_pick_progress_set(progress_id, stage='WB_READ', percent=82, message='Читаем текущий факт WB перед уменьшением остатков…', task_number=task_number, wb_positions=len(sync_by_chrt))
            current_wb_by_chrt = _wb_read_chrt_stocks(
                wb_client, int(selected_wb_id), sorted(sync_by_chrt)
            )
            for chrt_id,item in sync_by_chrt.items():
                item['local_target'] = int(item['target'])
                item['wb_before'] = max(0, int(current_wb_by_chrt.get(chrt_id, 0)))
                item['publish_target'] = min(int(item['local_target']), int(item['wb_before']))
                item['safe_capped'] = int(item['publish_target']) < int(item['local_target'])

            # r34 single-latest truth: supersede every older sibling command, insert one
            # newest durable generation per chrt, then perform the actual WB write BEFORE
            # the inventory transaction may commit.
            _supersede_wb_scope_rows(
                db, client_id=client_id, warehouse_id=warehouse_id,
                codes=set(affected_siblings) | set(affected_codes),
                reason=f'Сборочное задание №{task_number}: синхронное физическое списание',
            )
            for chrt_id,item in sync_by_chrt.items():
                sku = db.get(SKU, uuid.UUID(item['sku_id']))
                if not sku:
                    raise RuntimeError(f'Локальный SKU для chrtId {chrt_id} исчез до синхронизации')
                _enqueue_wb_absolute_target(
                    db, client_id=client_id, warehouse_id=warehouse_id, sku=sku,
                    credential_id=cred.id, external_warehouse_id=int(selected_wb_id), barcode=item['code'], desired=item['publish_target'],
                    reason=('EXTERNAL_PICK_WRITE_OFF_SYNC_SAFE_CAP' if item['safe_capped'] else 'EXTERNAL_PICK_WRITE_OFF_SYNC'),
                )
            db.flush()

            amounts = {item['code']:int(item['publish_target']) for item in sync_by_chrt.values()}
            override = {item['code']:int(chrt_id) for chrt_id,item in sync_by_chrt.items()}
            # FC_K9R38_WB_SYNC_BEFORE_COMMIT: the WB absolute size target must be accepted
            # and read-after-write confirmed while local SHIPPING is still uncommitted.
            _external_pick_progress_set(progress_id, stage='WB_WRITE', percent=88, message=f'Отправляем и подтверждаем новые остатки WB: {len(sync_by_chrt)} размеров/chrtId…', task_number=task_number, wb_positions=len(sync_by_chrt))
            _wb_set_stocks_bulk(
                db, physical_warehouse_id=warehouse_id, client_id=client_id, client=wb_client, warehouse_id=int(selected_wb_id), amounts=amounts,
                release_read_tx=False, chrt_map_override=override,
            )

            _external_pick_progress_set(progress_id, stage='WB_CONFIRMED', percent=94, message='WB подтвердил безопасные остатки. Фиксируем складское списание и историю…', task_number=task_number, wb_positions=len(sync_by_chrt))
            publication_rows = []
            for chrt_id,item in sync_by_chrt.items():
                dedupe = f"WB:{client_id}:{warehouse_id}:{item['sku_id']}"
                if item['safe_capped']:
                    # Keep the higher canonical local target pending, but force the normal
                    # upward guard to obtain a NEW sync newer than this command first.
                    db.execute(text("""
                        UPDATE fc_marketplace_stock_outbox_v2
                        SET status='PENDING',desired_amount=:desired,reason='EXTERNAL_PICK_POST_SYNC_RECONCILE',attempts=0,
                            last_error='WAITING_NEW_SYNC: WB уже ниже локальной цели во время списания; повышение запрещено до свежей проверки заказов',
                            next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '2 seconds',updated_at=CURRENT_TIMESTAMP
                        WHERE dedupe_key=:dedupe
                    """), {'desired':int(item['local_target']),'dedupe':dedupe})
                else:
                    db.execute(text("""
                        UPDATE fc_marketplace_stock_outbox_v2
                        SET status='DONE',desired_amount=:desired,reason='EXTERNAL_PICK_WRITE_OFF_SYNC',attempts=0,
                            last_error=NULL,next_attempt_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                        WHERE dedupe_key=:dedupe
                    """), {'desired':int(item['publish_target']),'dedupe':dedupe})
                for sibling in sorted(sibling_truth.get(item['code'], {item['code']})):
                    publication_rows.append({'client_id':str(client_id),'warehouse_id':str(warehouse_id),'barcode':sibling,
                                             'wb_warehouse_id':int(selected_wb_id),'amount':int(item['publish_target']),'status':'PUBLISHED','error':None})
            if publication_rows:
                _ensure_fbs_tracking_table(db)
                db.execute(text("""
                    INSERT INTO fc_wb_stock_publications(client_id,warehouse_id,barcode,wb_warehouse_id,published_amount,status,last_error,updated_at)
                    VALUES(:client_id,:warehouse_id,:barcode,:wb_warehouse_id,:amount,:status,:error,NOW())
                    ON CONFLICT(client_id,warehouse_id,barcode) DO UPDATE SET
                      wb_warehouse_id=EXCLUDED.wb_warehouse_id,published_amount=EXCLUDED.published_amount,
                      status=EXCLUDED.status,last_error=EXCLUDED.last_error,updated_at=NOW()
                """), publication_rows)

            status_value = 'PARTIAL' if shortage_total > 0 else 'COMPLETED'
            db.execute(text("""
                UPDATE fc_external_pick_batches
                SET allocated_units=:allocated,shortage_units=:shortage,status=:status,
                    wb_sync_status='CONFIRMED',wb_sync_error=NULL,wb_synced_at=CURRENT_TIMESTAMP
                WHERE id=:id
            """), {'allocated':allocated_total,'shortage':shortage_total,'status':status_value,'id':batch_id})
            _external_pick_progress_set(progress_id, stage='COMMIT', percent=98, message='Фиксируем операцию в базе и готовим сборочный лист…', task_number=task_number, allocated_units=allocated_total, shortage_units=shortage_total)
            db.commit()
            _external_pick_progress_set(progress_id, stage='DONE', percent=100, message=f'Готово: списано {allocated_total} шт., не хватило {shortage_total} шт. Формируем Excel.', task_number=task_number, batch_id=batch_id, allocated_units=allocated_total, shortage_units=shortage_total)
            if any(bool(item.get('safe_capped')) for item in sync_by_chrt.values()):
                _wake_wb_stock_outbox()
    except HTTPException as exc:
        db.rollback()
        _external_pick_progress_set(progress_id, stage='ERROR', percent=0, message=f'Операция остановлена: {getattr(exc, "detail", str(exc))}', task_number=task_number)
        raise
    except Exception as exc:
        db.rollback()
        _external_pick_progress_set(progress_id, stage='ERROR', percent=0, message=f'Ошибка синхронного списания/WB: {exc}', task_number=task_number)
        # WB may have accepted a subset before a verification failure. Local write-off is
        # rolled back, so queue the current untouched local truth as a safe reconciliation.
        queued_restore = 0
        try:
            for raw_id in sorted(affected_sku_ids):
                sku = db.get(SKU, uuid.UUID(raw_id))
                if not sku: continue
                enqueue_wb_stock_for_sku(
                    db, client_id=client_id, warehouse_id=warehouse_id, sku=sku,
                    credential_id=cred.id, external_warehouse_id=int(selected_wb_id), reason='EXTERNAL_PICK_SYNC_ROLLBACK',
                ); queued_restore += 1
            db.commit()
            if queued_restore: _wake_wb_stock_outbox()
        except Exception:
            db.rollback()
        raise HTTPException(
            status_code=502,
            detail=f'WB не подтвердил синхронное списание. Локальный остаток НЕ списан и сборочный лист не создан. {exc}',
        ) from exc

    return {'ok':True,'batch_id':batch_id,'task_number':task_number,'status':('PARTIAL' if shortage_total>0 else 'COMPLETED'),
            'source_rows':source_rows,'positions':len(rows),'positions_processed':positions_processed,'requested_units':requested_total,'allocated_units':allocated_total,
            'shortage_units':shortage_total,'wb_sync_status':'CONFIRMED','wb_synced_positions':len(sync_by_chrt)}




# FC_K9R41_EXTERNAL_PICK_PARTIAL_COMMIT: large FBS pick tasks are committed per
# uploaded barcode/size only after WB confirms that exact lowered target. One bad
# chrtId therefore cannot roll back hundreds of already valid warehouse write-offs.
def _external_pick_failed_request(
    db: Session, *, batch_id: str, request_row: dict, client_id: uuid.UUID,
    barcode: str, failed_quantity: int, shortage_quantity: int, chrt_id: int | None,
    identity: dict | None, note: str,
) -> None:
    info = identity or wb_product_identity(db, client_id, barcode) or {}
    db.execute(text("""
        INSERT INTO fc_external_pick_requests(
            id,batch_id,row_number,barcode,requested_quantity,allocated_quantity,
            shortage_quantity,failed_quantity,article,size_name,chrt_id,note
        ) VALUES(:id,:batch,:row,:barcode,:requested,0,:shortage,:failed,:article,:size,:chrt,:note)
    """), {
        'id':str(uuid.uuid4()), 'batch':batch_id, 'row':int(request_row['row_number']),
        'barcode':barcode, 'requested':int(request_row['quantity']),
        'shortage':max(0,int(shortage_quantity)), 'failed':max(0,int(failed_quantity)),
        'article':info.get('article'), 'size':info.get('size'), 'chrt':chrt_id,
        'note':str(note or '')[:2000],
    })


def _external_pick_sync_one_row(
    db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, selected_wb_id: int,
    credential_id: uuid.UUID, wb_client: WBClient, task_number: str, result: dict,
    fresh_size_rows: list[dict],
) -> tuple[int, bool]:
    """Synchronously publish one successfully planned row and keep local commit outside.

    Returns (published_amount, needs_later_upward_reconcile). Local SHIPPING is still
    uncommitted while the WB PUT/read-after-write verification happens.
    """
    code = str(result.get('barcode') or '').strip()
    rep_id = str(result.get('representative_sku_id') or '')
    if not code or not rep_id:
        raise RuntimeError('Не удалось определить локальный SKU для синхронного списания')

    from app.services.stock_outbox_worker import _wb_size_targets
    targets, sibling_truth, chrt_by_code = _wb_size_targets(
        db, client_id=str(client_id), warehouse_id=str(warehouse_id), external_wh=str(int(selected_wb_id)),
        codes=[code], extra_size_rows=fresh_size_rows,
    )
    raw_chrt = chrt_by_code.get(code) or result.get('chrt_id')
    if raw_chrt is None or code not in targets:
        raise RuntimeError(f'Не удалось определить подтверждаемую цель WB/chrtId для ШК {code}')
    chrt_id = int(raw_chrt)
    local_target = max(0,int(targets[code]))

    # FC_K9R51_EXTERNAL_PICK_ROW_TX_TIMEOUT_GUARD: this transaction intentionally stays
    # uncommitted until WB read/PUT/read-after-write succeeds. Extend only this transaction's
    # PostgreSQL idle guard; SET LOCAL resets automatically at row commit/rollback.
    db.execute(text("SET LOCAL idle_in_transaction_session_timeout = '180s'"))

    with distributed_lock(db, f'wb-stock-publish:{client_id}:{warehouse_id}:{int(selected_wb_id)}', blocking=True):
        wb_before = max(0,int(_wb_read_chrt_stocks(wb_client, int(selected_wb_id), [chrt_id]).get(chrt_id,0)))
        publish_target = min(local_target, wb_before)  # write-off flow never raises WB
        safe_capped = publish_target < local_target
        siblings = set(str(x) for x in (result.get('siblings') or {code}) if str(x or '').strip())
        _supersede_wb_scope_rows(
            db, client_id=client_id, warehouse_id=warehouse_id, codes=siblings | {code},
            reason=f'Сборочное задание №{task_number}: построчное синхронное списание',
        )
        sku = db.get(SKU, uuid.UUID(rep_id))
        if not sku:
            raise RuntimeError(f'Локальный SKU для ШК {code} исчез до синхронизации')
        _enqueue_wb_absolute_target(
            db, client_id=client_id, warehouse_id=warehouse_id, sku=sku,
            credential_id=credential_id, external_warehouse_id=int(selected_wb_id), barcode=code,
            desired=publish_target,
            reason=('EXTERNAL_PICK_ROW_SYNC_SAFE_CAP' if safe_capped else 'EXTERNAL_PICK_ROW_SYNC'),
        )
        db.flush()
        _wb_set_stocks_bulk(
            db, physical_warehouse_id=warehouse_id, client_id=client_id, client=wb_client, warehouse_id=int(selected_wb_id),
            amounts={code:publish_target}, release_read_tx=False, chrt_map_override={code:chrt_id},
        )
        dedupe = f"WB:{client_id}:{warehouse_id}:{rep_id}"
        if safe_capped:
            db.execute(text("""
                UPDATE fc_marketplace_stock_outbox_v2
                SET status='PENDING',desired_amount=:desired,reason='EXTERNAL_PICK_POST_SYNC_RECONCILE',attempts=0,
                    last_error='WAITING_NEW_SYNC: WB уже ниже локальной цели во время списания; повышение запрещено до свежей проверки заказов',
                    next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '2 seconds',updated_at=CURRENT_TIMESTAMP
                WHERE dedupe_key=:dedupe
            """), {'desired':local_target,'dedupe':dedupe})
        else:
            db.execute(text("""
                UPDATE fc_marketplace_stock_outbox_v2
                SET status='DONE',desired_amount=:desired,reason='EXTERNAL_PICK_ROW_SYNC',attempts=0,
                    last_error=NULL,next_attempt_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                WHERE dedupe_key=:dedupe
            """), {'desired':publish_target,'dedupe':dedupe})

        publication_rows=[]
        for sibling in sorted(sibling_truth.get(code, {code})):
            publication_rows.append({'client_id':str(client_id),'warehouse_id':str(warehouse_id),'barcode':sibling,
                                     'wb_warehouse_id':int(selected_wb_id),'amount':publish_target,'status':'PUBLISHED','error':None})
        if publication_rows:
            _ensure_fbs_tracking_table(db)
            db.execute(text("""
                INSERT INTO fc_wb_stock_publications(client_id,warehouse_id,barcode,wb_warehouse_id,published_amount,status,last_error,updated_at)
                VALUES(:client_id,:warehouse_id,:barcode,:wb_warehouse_id,:amount,:status,:error,NOW())
                ON CONFLICT(client_id,warehouse_id,barcode) DO UPDATE SET
                  wb_warehouse_id=EXCLUDED.wb_warehouse_id,published_amount=EXCLUDED.published_amount,
                  status=EXCLUDED.status,last_error=EXCLUDED.last_error,updated_at=NOW()
            """), publication_rows)
    return publish_target, safe_capped


# FC_K9R52_EXTERNAL_PICK_BATCH_SYNC: publish a bounded group of already-planned local
# write-offs with one WB stock read + one batched PUT/read-after-write verification.
# The caller keeps the local SHIPPING transaction uncommitted until this returns.
def _external_pick_sync_batch(
    db: Session, *, client_id: uuid.UUID, warehouse_id: uuid.UUID, selected_wb_id: int,
    credential_id: uuid.UUID, wb_client: WBClient, task_number: str, results: list[dict],
    fresh_size_rows: list[dict],
) -> tuple[int, bool]:
    active = [row for row in results if int(row.get('allocated') or 0) > 0]
    if not active:
        return 0, False
    codes = list(dict.fromkeys(str(row.get('barcode') or '').strip() for row in active if str(row.get('barcode') or '').strip()))
    from app.services.stock_outbox_worker import _wb_size_targets
    targets, sibling_truth, chrt_by_code = _wb_size_targets(
        db, client_id=str(client_id), warehouse_id=str(warehouse_id), external_wh=str(int(selected_wb_id)),
        codes=codes, extra_size_rows=fresh_size_rows,
    )

    by_chrt: dict[int, dict[str, Any]] = {}
    prepared: list[dict[str, Any]] = []
    all_scope_codes: set[str] = set()
    for result in active:
        code = str(result.get('barcode') or '').strip()
        rep_id = str(result.get('representative_sku_id') or '')
        raw_chrt = chrt_by_code.get(code) or result.get('chrt_id')
        if not code or not rep_id or raw_chrt is None or code not in targets:
            raise RuntimeError(f'Batch WB target is unresolved for barcode {code or "?"}')
        chrt_id = int(raw_chrt)
        local_target = max(0, int(targets[code]))
        previous = by_chrt.get(chrt_id)
        if previous is not None and int(previous['local_target']) != local_target:
            raise RuntimeError(f'Conflicting local targets for chrtId {chrt_id}')
        by_chrt[chrt_id] = {'local_target': local_target}
        siblings = set(str(x) for x in sibling_truth.get(code, result.get('siblings') or {code}) if str(x or '').strip())
        siblings.add(code)
        all_scope_codes.update(siblings)
        prepared.append({
            'result': result, 'code': code, 'rep_id': rep_id, 'chrt_id': chrt_id,
            'local_target': local_target, 'siblings': siblings,
        })

    # This is the one intentional transaction that spans WB I/O. It is bounded to a small
    # chunk and protected from PostgreSQL's normal idle-in-transaction timeout.
    db.execute(text("SET LOCAL idle_in_transaction_session_timeout = '180s'"))
    with distributed_lock(db, f'wb-stock-publish:{client_id}:{warehouse_id}:{int(selected_wb_id)}', blocking=True):
        wb_before = _wb_read_chrt_stocks(wb_client, int(selected_wb_id), sorted(by_chrt))
        publish_by_chrt: dict[int, int] = {}
        for chrt_id, meta in by_chrt.items():
            before = max(0, int(wb_before.get(chrt_id, 0)))
            publish_by_chrt[chrt_id] = min(int(meta['local_target']), before)

        _supersede_wb_scope_rows(
            db, client_id=client_id, warehouse_id=warehouse_id, codes=all_scope_codes,
            reason=f'External pick task {task_number}: batched synchronous write-off',
        )

        safe_capped_any = False
        queued: dict[str, dict[str, Any]] = {}
        for item in prepared:
            publish_target = int(publish_by_chrt[item['chrt_id']])
            safe_capped = publish_target < int(item['local_target'])
            safe_capped_any = safe_capped_any or safe_capped
            # One outbox row per local SKU is enough even if aliases repeat inside the chunk.
            queued[item['rep_id']] = {**item, 'publish_target': publish_target, 'safe_capped': safe_capped}
        for item in queued.values():
            sku = db.get(SKU, uuid.UUID(item['rep_id']))
            if not sku:
                raise RuntimeError(f'Local SKU disappeared before batch WB sync: {item["code"]}')
            _enqueue_wb_absolute_target(
                db, client_id=client_id, warehouse_id=warehouse_id, sku=sku,
                credential_id=credential_id, external_warehouse_id=int(selected_wb_id), barcode=item['code'],
                desired=int(item['publish_target']),
                reason=('EXTERNAL_PICK_BATCH_SYNC_SAFE_CAP' if item['safe_capped'] else 'EXTERNAL_PICK_BATCH_SYNC'),
            )
        db.flush()

        _wb_write_and_verify_chrt_stocks(wb_client, int(selected_wb_id), publish_by_chrt)

        publication_rows: list[dict[str, Any]] = []
        publication_seen: set[str] = set()
        for item in queued.values():
            dedupe = f"WB:{client_id}:{warehouse_id}:{item['rep_id']}"
            if item['safe_capped']:
                db.execute(text("""
                    UPDATE fc_marketplace_stock_outbox_v2
                    SET status='PENDING',desired_amount=:desired,reason='EXTERNAL_PICK_POST_SYNC_RECONCILE',attempts=0,
                        last_error='WAITING_NEW_SYNC: WB below local target during batch write-off; upward publish is deferred',
                        next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '2 seconds',updated_at=CURRENT_TIMESTAMP
                    WHERE dedupe_key=:dedupe
                """), {'desired': int(item['local_target']), 'dedupe': dedupe})
            else:
                db.execute(text("""
                    UPDATE fc_marketplace_stock_outbox_v2
                    SET status='DONE',desired_amount=:desired,reason='EXTERNAL_PICK_BATCH_SYNC',attempts=0,
                        last_error=NULL,next_attempt_at=CURRENT_TIMESTAMP,updated_at=CURRENT_TIMESTAMP
                    WHERE dedupe_key=:dedupe
                """), {'desired': int(item['publish_target']), 'dedupe': dedupe})
            for sibling in sorted(item['siblings']):
                if sibling in publication_seen:
                    continue
                publication_seen.add(sibling)
                publication_rows.append({
                    'client_id': str(client_id), 'warehouse_id': str(warehouse_id), 'barcode': sibling,
                    'wb_warehouse_id': int(selected_wb_id), 'amount': int(item['publish_target']),
                    'status': 'PUBLISHED', 'error': None,
                })
        if publication_rows:
            _ensure_fbs_tracking_table(db)
            db.execute(text("""
                INSERT INTO fc_wb_stock_publications(client_id,warehouse_id,barcode,wb_warehouse_id,published_amount,status,last_error,updated_at)
                VALUES(:client_id,:warehouse_id,:barcode,:wb_warehouse_id,:amount,:status,:error,NOW())
                ON CONFLICT(client_id,warehouse_id,barcode) DO UPDATE SET
                  wb_warehouse_id=EXCLUDED.wb_warehouse_id,published_amount=EXCLUDED.published_amount,
                  status=EXCLUDED.status,last_error=EXCLUDED.last_error,updated_at=NOW()
            """), publication_rows)
    return len(publish_by_chrt), safe_capped_any


@router.post('/external-picking/import-safe')
def external_pick_import_safe(
    client_id: uuid.UUID = Form(...), warehouse_id: uuid.UUID = Form(...), wb_warehouse_id: int | None = Form(None),
    performed_by: str | None = Form(None), progress_id: str | None = Form(None), task_number_override: str | None = Form(None),
    file: UploadFile = File(...), db: Session = Depends(get_db),
):
    """Durable partial FBS write-off.

    Every row is an independent safety transaction: current free stock is planned,
    WB is synchronously lowered/read-after-write verified, then and only then local
    SHIPPING/history is committed. A failed WB size stays physically untouched and is
    reported in the result workbook instead of rolling back the whole task.
    """
    _external_pick_progress_set(progress_id,stage='RECEIVED',percent=2,message='Файл принят сервером. Проверяем шаблон и номер задания…')
    ensure_runtime_schema(db)
    cred=_active_wb_credential(db,client_id)
    if not cred:
        raise HTTPException(status_code=409,detail='Подключение WB для выбранного клиента не настроено')
    # FC_K9R51_EXTERNAL_PICK_DB_IDLE_GUARD: snapshot ORM credential fields before any commit/rollback.
    # SQLAlchemy may expire ORM state after rollback; reading it during slow WB I/O can autobegin
    # an otherwise idle transaction that PostgreSQL terminates before the next SQL statement.
    credential_id=uuid.UUID(str(cred.id))
    credential_api_key=str(cred.encrypted_api_key)
    selected_wb_id=_work_wb_warehouse_id(cred,wb_warehouse_id,warehouse_id)
    if not selected_wb_id:
        raise HTTPException(status_code=409,detail='Для выбранного физического склада не назначен рабочий склад WB')
    filename=str(file.filename or '')
    if filename and not filename.lower().endswith('.xlsx'):
        raise HTTPException(status_code=400,detail='Загрузите заполненный шаблон Excel .xlsx')
    content=file.file.read(); source_sha256=hashlib.sha256(content).hexdigest()
    task_number,rows,source_rows=_external_pick_parse_rows(content, task_number_override=task_number_override)
    requested_total=sum(int(row['quantity']) for row in rows)
    _external_pick_progress_set(progress_id,stage='PARSED',percent=7,
        message=f'Шаблон проверен: задание №{task_number}, {source_rows} строк, {len(rows)} уникальных ШК, {requested_total} шт.',
        task_number=task_number,positions=len(rows),requested_units=requested_total)

    existing=db.execute(text("""
        SELECT id,source_sha256,status,requested_units,allocated_units,shortage_units,failed_units,wb_sync_status,wb_sync_error,positions_total,updated_at
        FROM fc_external_pick_batches
        WHERE client_id=:client AND warehouse_id=:warehouse AND task_number=:task
        ORDER BY created_at DESC LIMIT 1
    """),{'client':str(client_id),'warehouse':str(warehouse_id),'task':task_number}).mappings().first()
    if existing:
        if str(existing.get('source_sha256') or '') != source_sha256:
            # Excel may rewrite workbook metadata/ZIP bytes when the operator merely opens
            # and saves the same logical task. r42 therefore permits resume when every
            # already-durable barcode request still matches the current aggregated input
            # and the requested total is unchanged. Different business content is blocked.
            current_qty={str(row['barcode']):int(row['quantity']) for row in rows}
            prior_rows=db.execute(text("SELECT barcode,requested_quantity FROM fc_external_pick_requests WHERE batch_id=:batch"),{'batch':str(existing['id'])}).all()
            logical_same=(int(Decimal(str(existing.get('requested_units') or 0)))==requested_total and all(
                current_qty.get(str(code))==int(Decimal(str(qty or 0))) for code,qty in prior_rows
            ))
            if not logical_same:
                raise HTTPException(status_code=409,detail=f'Сборочное задание №{task_number} уже существует с другим содержимым. Повторное списание запрещено.')
            db.execute(text("UPDATE fc_external_pick_batches SET source_sha256=:sha,source_filename=:filename,updated_at=CURRENT_TIMESTAMP WHERE id=:id"),
                       {'sha':source_sha256,'filename':filename[:512],'id':str(existing['id'])}); db.commit()
        status=str(existing.get('status') or '').upper()
        batch_id=str(existing['id'])
        if status == 'CANCELLED':
            raise HTTPException(status_code=409,detail=f'Сборочное задание №{task_number} уже отменено. Для нового списания укажите новый номер задания.')
        done_count=int(db.execute(text("SELECT COUNT(*) FROM fc_external_pick_requests WHERE batch_id=:batch"),{'batch':batch_id}).scalar() or 0)
        if status in {'COMPLETED','PARTIAL','NO_STOCK','FAILED_WB'} and done_count >= len(rows):
            _external_pick_progress_set(progress_id,stage='DONE',percent=100,
                message=f'Задание №{task_number} уже завершено: обработано {done_count}/{len(rows)} позиций. Повторного списания нет.',
                task_number=task_number,batch_id=batch_id,current=done_count,total=len(rows))
            return {'ok':True,'duplicate':True,'batch_id':batch_id,'task_number':task_number,'status':status,
                    'requested_units':int(Decimal(str(existing.get('requested_units') or 0))),
                    'allocated_units':int(Decimal(str(existing.get('allocated_units') or 0))),
                    'shortage_units':int(Decimal(str(existing.get('shortage_units') or 0))),
                    'failed_units':int(Decimal(str(existing.get('failed_units') or 0))),
                    'positions_total':len(rows),'positions_processed':done_count,
                    'wb_sync_status':existing.get('wb_sync_status') or 'UNKNOWN'}
        # r42: an interrupted PROCESSING/PARTIAL task is resumed only for barcodes that
        # do not yet have a durable request outcome. Already WRITTEN_OFF rows are never repeated.
        db.execute(text("""
            UPDATE fc_external_pick_batches SET status='PROCESSING',requested_units=:requested,positions_total=:positions,
                wb_sync_status='PREPARING',wb_sync_error=NULL,updated_at=CURRENT_TIMESTAMP
            WHERE id=:id
        """),{'requested':requested_total,'positions':len(rows),'id':batch_id}); db.commit()
    else:
        batch_id=str(uuid.uuid4())
        db.execute(text("""
            INSERT INTO fc_external_pick_batches(
                id,client_id,warehouse_id,wb_warehouse_id,task_number,source_filename,source_sha256,performed_by,status,
                requested_units,allocated_units,shortage_units,failed_units,source_rows,positions_total,wb_sync_status,updated_at
            ) VALUES(:id,:client,:warehouse,:wb_wh,:task,:filename,:sha,:by,'PROCESSING',:requested,0,0,0,:source_rows,:positions,'PREPARING',CURRENT_TIMESTAMP)
        """),{'id':batch_id,'client':str(client_id),'warehouse':str(warehouse_id),'wb_wh':int(selected_wb_id),
               'task':task_number,'filename':filename[:512],'sha':source_sha256,'by':performed_by or None,
               'requested':requested_total,'source_rows':source_rows,'positions':len(rows)})
        db.commit()  # durable task identity exists before any slow WB preparation

    processed_codes={str(x) for x in db.execute(text("SELECT barcode FROM fc_external_pick_requests WHERE batch_id=:batch"),{'batch':batch_id}).scalars().all()}
    rows_to_process=[row for row in rows if str(row['barcode']) not in processed_codes]
    requested_codes=[row['barcode'] for row in rows]
    wb_client=WBClient(decrypt_secret(credential_api_key))
    try:
        _external_pick_progress_set(progress_id,stage='CACHE_MAP',percent=12,
            message=f'Сопоставляем {len(requested_codes)} ШК по локальной карте WB…',task_number=task_number,batch_id=batch_id)
        internal=_wb_internal_product_codes(db,client_id=client_id,codes=requested_codes)
        if internal:
            raise HTTPException(status_code=400,detail='В файле есть внутренние ШК коробов/ячеек: '+', '.join(sorted(internal)[:10]))
        sibling_map,chrt_map=_external_pick_cached_size_map(db,client_id=client_id,barcodes=requested_codes)
        # FC_K9R51_EXTERNAL_PICK_DB_IDLE_GUARD: cached mapping above is read-only. Release that
        # transaction before any live WB catalogue HTTP so PostgreSQL never sees DB-idle network wait.
        db.rollback()
        unresolved=[code for code in requested_codes if code not in chrt_map]
        fresh_size_rows=[]
        if unresolved:
            chunks=[unresolved[i:i+8] for i in range(0,len(unresolved),8)] if len(unresolved)<=32 else [unresolved]
            for idx,chunk in enumerate(chunks,1):
                pct=13+int(8*idx/max(1,len(chunks)))
                _external_pick_progress_set(progress_id,stage='WB_CATALOG',percent=pct,
                    message=f'Уточняем отсутствующие карточки WB: пакет {idx}/{len(chunks)}, осталось {len(unresolved)} ШК…',task_number=task_number,batch_id=batch_id)
                fresh_size_rows.extend(_wb_fresh_size_rows(wb_client,chunk))
            for size_row in fresh_size_rows:
                try: cid=int(size_row.get('chrtID') or size_row.get('chrtId') or size_row.get('chrt_id'))
                except Exception: continue
                codes={normalize_product_barcode(str(x))[0] for x in (size_row.get('skus') or []) if str(x or '').strip()}; codes.discard('')
                for code in codes:
                    if code in requested_codes:
                        chrt_map[code]=cid; sibling_map[code]=set(codes)
        identity_map=wb_product_identity_map(db,client_id,set(requested_codes)) if requested_codes else {}
        db.rollback()

        _external_pick_progress_set(progress_id,stage='NEW_ORDERS',percent=23,
            message='Проверяем свежие новые заказы WB перед первым списанием…',task_number=task_number,batch_id=batch_id)
        from app.services.wb_sale_reservation_worker import sync_new_for_scope
        def fresh_new_ok() -> bool:
            for attempt in range(1,4):
                try:
                    result_sync=sync_new_for_scope(str(credential_id),str(warehouse_id),str(int(selected_wb_id)))
                    if int((result_sync or {}).get('sync_ok') or 0):
                        return True
                except Exception:
                    pass
                if attempt < 3:
                    time.sleep(1.0 * attempt)
            return False
        # The endpoint DB session must be transaction-free during the separate NEW-order WB sync.
        # All values used by fresh_new_ok are scalar snapshots, not expired ORM attributes.
        db.rollback()
        sync_safe=fresh_new_ok()
        # FC_K9R54_EXTERNAL_PICK_TIME_GUARD: refresh NEW-order safety by elapsed time,
        # not by an arbitrary number of spreadsheet rows. A fast 25-row batch no longer
        # performs redundant WB NEW-order calls every 50 positions; a slow task is still
        # guaranteed a fresh safety check at least every 30 seconds.
        last_fresh_new_sync_mono = time.monotonic() if sync_safe else 0.0
        # Resume counters from durable rows, never from an interrupted in-memory pass.
        totals=db.execute(text("""SELECT COALESCE(SUM(allocated_quantity),0),COALESCE(SUM(shortage_quantity),0),COALESCE(SUM(failed_quantity),0) FROM fc_external_pick_requests WHERE batch_id=:batch"""),{'batch':batch_id}).first()
        allocated_total=int(Decimal(str(totals[0] or 0))); shortage_total=int(Decimal(str(totals[1] or 0))); failed_total=int(Decimal(str(totals[2] or 0)))
        error_notes=[]; safe_capped_any=False

        # FC_K9R52_EXTERNAL_PICK_BATCH_SYNC: 25 rows are planned under their canonical
        # sale-reserve locks, then all affected WB chrtIds are read/written/verified in one
        # bounded request group. If the batch path has any ambiguity, roll the local draft
        # back and fall back to the proven r51 exact-row path for just that chunk.
        batch_size=25

        def process_one_fallback(row: dict, global_pos: int) -> None:
            nonlocal allocated_total, shortage_total, failed_total, safe_capped_any
            result=None; code=str(row['barcode']); identity=identity_map.get(code) or {}
            try:
                candidates,_sib,_cid=_external_pick_identity_candidates(
                    db,client_id=client_id,warehouse_id=warehouse_id,wb_warehouse_id=int(selected_wb_id),barcode=code,
                    sibling_codes_override=sibling_map.get(code),chrt_id_override=chrt_map.get(code),identity_override=identity)
                row_lock_keys=sorted({canonical_stock_identity_lock_key(db,client_id=client_id,sku_id=sku.id) for sku in candidates})
                db.rollback()
                with ExitStack() as row_locks:
                    for key in row_lock_keys:
                        row_locks.enter_context(distributed_lock(db,f'sale-reserve:{client_id}:{warehouse_id}:{key}',blocking=True))
                    result=_external_pick_writeoff_one(
                        db,batch_id=batch_id,task_number=task_number,request_row=row,client_id=client_id,warehouse_id=warehouse_id,
                        wb_warehouse_id=int(selected_wb_id),sibling_codes=sibling_map.get(code),chrt_id_hint=chrt_map.get(code),
                        identity_hint=identity,performed_by=performed_by)
                    if int(result.get('allocated') or 0)>0:
                        _external_pick_progress_set(progress_id,stage='WB_WRITE',percent=min(94,25+int(68*global_pos/max(1,len(rows)))),
                            message=f'Позиция {global_pos}/{len(rows)}: точечная проверка WB...',
                            task_number=task_number,batch_id=batch_id,current=global_pos,total=len(rows),allocated_units=allocated_total)
                        _published,safe_capped=_external_pick_sync_one_row(
                            db,client_id=client_id,warehouse_id=warehouse_id,selected_wb_id=int(selected_wb_id),credential_id=credential_id,
                            wb_client=wb_client,task_number=task_number,result=result,fresh_size_rows=fresh_size_rows)
                        safe_capped_any=safe_capped_any or safe_capped
                    row_alloc=int(result.get('allocated') or 0); row_short=int(result.get('shortage') or 0)
                    db.execute(text("""
                        UPDATE fc_external_pick_batches
                        SET allocated_units=allocated_units+:allocated,shortage_units=shortage_units+:shortage,
                            wb_sync_status='PROCESSING',updated_at=CURRENT_TIMESTAMP
                        WHERE id=:id
                    """),{'allocated':row_alloc,'shortage':row_short,'id':batch_id})
                    db.commit()  # exact row: WB confirmed first, then local SHIPPING commits
                allocated_total+=row_alloc; shortage_total+=row_short
            except Exception as exc:
                row_error=str(getattr(exc,'detail',exc)); db.rollback()
                failed_qty=int((result or {}).get('allocated') or row.get('quantity') or 0)
                shortage_qty=int((result or {}).get('shortage') or 0)
                failed_total+=max(0,failed_qty); shortage_total+=max(0,shortage_qty)
                rep_id=str((result or {}).get('representative_sku_id') or '')
                if rep_id:
                    try:
                        sku=db.get(SKU,uuid.UUID(rep_id))
                        if sku:
                            enqueue_wb_stock_for_sku(db,client_id=client_id,warehouse_id=warehouse_id,sku=sku,
                                credential_id=credential_id,external_warehouse_id=int(selected_wb_id),reason='EXTERNAL_PICK_ROW_SYNC_ROLLBACK')
                            db.commit(); _wake_wb_stock_outbox()
                    except Exception:
                        db.rollback()
                _external_pick_failed_request(db,batch_id=batch_id,request_row=row,client_id=client_id,barcode=code,
                    failed_quantity=failed_qty,shortage_quantity=shortage_qty,chrt_id=(result or {}).get('chrt_id') or chrt_map.get(code),
                    identity=identity,note=f'WB fallback failed: {row_error}')
                db.execute(text("""
                    UPDATE fc_external_pick_batches
                    SET shortage_units=shortage_units+:shortage,failed_units=failed_units+:failed,
                        wb_sync_status='PARTIAL',wb_sync_error=:err
                    WHERE id=:id
                """),{'shortage':shortage_qty,'failed':failed_qty,'err':row_error[:2000],'id':batch_id})
                db.commit(); error_notes.append(f'{code}: {row_error}')

        for chunk_start in range(0,len(rows_to_process),batch_size):
            chunk=rows_to_process[chunk_start:chunk_start+batch_size]
            now_mono=time.monotonic()
            refresh_new_due = bool(
                chunk_start>0 and (
                    (sync_safe and now_mono-last_fresh_new_sync_mono >= 30.0)
                    or ((not sync_safe) and chunk_start%50==0)
                )
            )
            if refresh_new_due:
                current_done=len(processed_codes)+chunk_start
                _external_pick_progress_set(progress_id,stage='NEW_ORDERS',percent=min(88,25+int(62*current_done/max(1,len(rows)))),
                    message=f'Проверяем свежие заказы WB: {current_done}/{len(rows)} поз....',task_number=task_number,batch_id=batch_id)
                sync_safe=fresh_new_ok()
                if sync_safe:
                    last_fresh_new_sync_mono=time.monotonic()

            if not sync_safe:
                for offset,row in enumerate(chunk,1):
                    code=str(row['barcode']); identity=identity_map.get(code) or {}; qty=int(row['quantity'])
                    _external_pick_failed_request(db,batch_id=batch_id,request_row=row,client_id=client_id,barcode=code,
                        failed_quantity=qty,shortage_quantity=0,chrt_id=chrt_map.get(code),identity=identity,
                        note='Fresh WB NEW-order sync was not confirmed; local stock is untouched')
                    failed_total+=qty; error_notes.append(f'{code}: NEW sync unavailable')
                    db.execute(text("""UPDATE fc_external_pick_batches SET failed_units=:failed,allocated_units=:allocated,shortage_units=:shortage,wb_sync_status='PARTIAL',updated_at=CURRENT_TIMESTAMP WHERE id=:id"""),
                        {'failed':failed_total,'allocated':allocated_total,'shortage':shortage_total,'id':batch_id})
                    db.commit()
                continue

            batch_results=[]
            try:
                lock_keys:set[str]=set()
                for row in chunk:
                    code=str(row['barcode']); identity=identity_map.get(code) or {}
                    candidates,_sib,_cid=_external_pick_identity_candidates(
                        db,client_id=client_id,warehouse_id=warehouse_id,wb_warehouse_id=int(selected_wb_id),barcode=code,
                        sibling_codes_override=sibling_map.get(code),chrt_id_override=chrt_map.get(code),identity_override=identity)
                    lock_keys.update(canonical_stock_identity_lock_key(db,client_id=client_id,sku_id=sku.id) for sku in candidates)
                db.rollback()
                with ExitStack() as batch_locks:
                    for key in sorted(lock_keys):
                        batch_locks.enter_context(distributed_lock(db,f'sale-reserve:{client_id}:{warehouse_id}:{key}',blocking=True))
                    for row in chunk:
                        code=str(row['barcode']); identity=identity_map.get(code) or {}
                        batch_results.append(_external_pick_writeoff_one(
                            db,batch_id=batch_id,task_number=task_number,request_row=row,client_id=client_id,warehouse_id=warehouse_id,
                            wb_warehouse_id=int(selected_wb_id),sibling_codes=sibling_map.get(code),chrt_id_hint=chrt_map.get(code),
                            identity_hint=identity,performed_by=performed_by))
                    allocated_rows=[r for r in batch_results if int(r.get('allocated') or 0)>0]
                    if allocated_rows:
                        current_end=len(processed_codes)+chunk_start+len(chunk)
                        _external_pick_progress_set(progress_id,stage='WB_BATCH',percent=min(94,25+int(68*current_end/max(1,len(rows)))),
                            message=f'Пакет {chunk_start//batch_size+1}: подтверждаем в WB {len(allocated_rows)} позиций одним пакетом...',
                            task_number=task_number,batch_id=batch_id,current=current_end,total=len(rows),allocated_units=allocated_total)
                        _published_count,batch_safe_capped=_external_pick_sync_batch(
                            db,client_id=client_id,warehouse_id=warehouse_id,selected_wb_id=int(selected_wb_id),credential_id=credential_id,
                            wb_client=wb_client,task_number=task_number,results=allocated_rows,fresh_size_rows=fresh_size_rows)
                        safe_capped_any=safe_capped_any or batch_safe_capped
                    chunk_alloc=sum(int(r.get('allocated') or 0) for r in batch_results)
                    chunk_short=sum(int(r.get('shortage') or 0) for r in batch_results)
                    db.execute(text("""
                        UPDATE fc_external_pick_batches
                        SET allocated_units=allocated_units+:allocated,shortage_units=shortage_units+:shortage,
                            wb_sync_status='PROCESSING',updated_at=CURRENT_TIMESTAMP
                        WHERE id=:id
                    """),{'allocated':chunk_alloc,'shortage':chunk_short,'id':batch_id})
                    db.commit()  # bounded batch: WB confirmed first, then local SHIPPING commits
                allocated_total+=chunk_alloc; shortage_total+=chunk_short
            except Exception as batch_exc:
                db.rollback()
                current_end=len(processed_codes)+chunk_start+len(chunk)
                _external_pick_progress_set(progress_id,stage='WB_BATCH_FALLBACK',percent=min(94,25+int(68*current_end/max(1,len(rows)))),
                    message=f'WB не подтвердил пакет однозначно; безопасно проверяем {len(chunk)} позиций по одной...',
                    task_number=task_number,batch_id=batch_id,current=len(processed_codes)+chunk_start,total=len(rows),
                    batch_error=str(getattr(batch_exc,'detail',batch_exc))[:500])
                for offset,row in enumerate(chunk,1):
                    process_one_fallback(row,len(processed_codes)+chunk_start+offset)

            current_done=len(processed_codes)+chunk_start+len(chunk)
            pct=min(96,25+int(70*current_done/max(1,len(rows))))
            _external_pick_progress_set(progress_id,stage='WRITE_OFF',percent=pct,
                message=f'Обработано {current_done}/{len(rows)} поз. · списано {allocated_total} шт. · не хватило {shortage_total} · не обработано WB {failed_total}.',
                task_number=task_number,batch_id=batch_id,current=current_done,total=len(rows),allocated_units=allocated_total,
                shortage_units=shortage_total,failed_units=failed_total)

        final_totals=db.execute(text("""SELECT COUNT(*),COALESCE(SUM(allocated_quantity),0),COALESCE(SUM(shortage_quantity),0),COALESCE(SUM(failed_quantity),0) FROM fc_external_pick_requests WHERE batch_id=:batch"""),{'batch':batch_id}).first()
        positions_processed=int(final_totals[0] or 0); allocated_total=int(Decimal(str(final_totals[1] or 0))); shortage_total=int(Decimal(str(final_totals[2] or 0))); failed_total=int(Decimal(str(final_totals[3] or 0)))
        if positions_processed < len(rows):
            db.execute(text("""UPDATE fc_external_pick_batches SET status='PROCESSING',allocated_units=:a,shortage_units=:s,failed_units=:f,positions_total=:p,wb_sync_status='PROCESSING',wb_sync_error=:err,updated_at=CURRENT_TIMESTAMP WHERE id=:id"""),
                {'a':allocated_total,'s':shortage_total,'f':failed_total,'p':len(rows),'err':f'Обработано {positions_processed} из {len(rows)} позиций; повторите загрузку того же файла для безопасного продолжения','id':batch_id})
            db.commit()
            raise HTTPException(status_code=503,detail=f'Задание №{task_number} обработано не полностью: {positions_processed}/{len(rows)} позиций. Повторите тот же файл — уже обработанные позиции не спишутся повторно.')

        if allocated_total<=0 and failed_total>0:
            status_value='FAILED_WB'; wb_status='FAILED'
        elif allocated_total<=0:
            status_value='NO_STOCK'; wb_status='NOT_REQUIRED'
        elif failed_total>0 or shortage_total>0:
            status_value='PARTIAL'; wb_status='PARTIAL' if failed_total>0 else 'CONFIRMED'
        else:
            status_value='COMPLETED'; wb_status='CONFIRMED'
        error_summary=' | '.join(error_notes[:8])[:4000] if error_notes else None
        db.execute(text("""
            UPDATE fc_external_pick_batches
            SET allocated_units=:allocated,shortage_units=:shortage,failed_units=:failed,status=:status,
                wb_sync_status=:wb_status,wb_sync_error=:wb_error,wb_synced_at=CURRENT_TIMESTAMP,positions_total=:positions,updated_at=CURRENT_TIMESTAMP
            WHERE id=:id
        """),{'allocated':allocated_total,'shortage':shortage_total,'failed':failed_total,'status':status_value,
               'wb_status':wb_status,'wb_error':error_summary,'positions':len(rows),'id':batch_id})
        db.commit()
        if safe_capped_any: _wake_wb_stock_outbox()
        _external_pick_progress_set(progress_id,stage='DONE',percent=100,
            message=f'Задание №{task_number} завершено: списано {allocated_total} шт., не хватило {shortage_total}, не обработано из-за WB {failed_total} шт. Формируем Excel.',
            task_number=task_number,batch_id=batch_id,allocated_units=allocated_total,shortage_units=shortage_total,failed_units=failed_total)
        return {'ok':True,'batch_id':batch_id,'task_number':task_number,'status':status_value,'source_rows':source_rows,
                'positions':len(rows),'positions_processed':positions_processed,'requested_units':requested_total,'allocated_units':allocated_total,
                'shortage_units':shortage_total,'failed_units':failed_total,'wb_sync_status':wb_status,
                'wb_sync_error':error_summary}
    except HTTPException as exc:
        db.rollback()
        try:
            current_alloc=int(Decimal(str(db.execute(text('SELECT allocated_units FROM fc_external_pick_batches WHERE id=:id'),{'id':batch_id}).scalar() or 0)))
            db.execute(text("""
                UPDATE fc_external_pick_batches
                SET status=:status,wb_sync_status=:wb_status,wb_sync_error=:err
                WHERE id=:id
            """),{'status':('PARTIAL' if current_alloc>0 else 'FAILED_SETUP'),'wb_status':('PARTIAL' if current_alloc>0 else 'FAILED'),
                   'err':str(getattr(exc,'detail',exc))[:4000],'id':batch_id}); db.commit()
        except Exception: db.rollback()
        _external_pick_progress_set(progress_id,stage='ERROR',percent=0,
            message=f'Операция остановлена: {getattr(exc,"detail",str(exc))}',task_number=task_number,batch_id=batch_id)
        raise
    except Exception as exc:
        db.rollback()
        try:
            current_alloc=int(Decimal(str(db.execute(text('SELECT allocated_units FROM fc_external_pick_batches WHERE id=:id'),{'id':batch_id}).scalar() or 0)))
            db.execute(text("""
                UPDATE fc_external_pick_batches
                SET status=:status,wb_sync_status=:wb_status,wb_sync_error=:err
                WHERE id=:id
            """),{'status':('PARTIAL' if current_alloc>0 else 'FAILED_SETUP'),'wb_status':('PARTIAL' if current_alloc>0 else 'FAILED'),
                   'err':str(exc)[:4000],'id':batch_id}); db.commit()
        except Exception: db.rollback()
        _external_pick_progress_set(progress_id,stage='ERROR',percent=0,
            message=f'Ошибка подготовки задания: {exc}',task_number=task_number,batch_id=batch_id)
        raise HTTPException(status_code=502,detail=f'Не удалось подготовить сборочное задание. Ничего не списано на незавершённом этапе. {exc}') from exc


@router.get('/external-picking/{batch_id}/export.xlsx')
def external_pick_export(
    batch_id: str, client_id: uuid.UUID = Query(...), warehouse_id: uuid.UUID = Query(...), db: Session = Depends(get_db),
):
    ensure_runtime_schema(db)
    state_row=db.execute(text("""SELECT status,positions_total FROM fc_external_pick_batches WHERE id=:id AND client_id=:client AND warehouse_id=:warehouse"""),{'id':batch_id,'client':str(client_id),'warehouse':str(warehouse_id)}).mappings().first()
    if not state_row:
        raise HTTPException(status_code=404,detail='Сборочное задание не найдено')
    processed=int(db.execute(text("SELECT COUNT(*) FROM fc_external_pick_requests WHERE batch_id=:id"),{'id':batch_id}).scalar() or 0)
    total=int(state_row.get('positions_total') or 0)
    if str(state_row.get('status') or '').upper()=='PROCESSING' or (total>0 and processed<total):
        raise HTTPException(status_code=409,detail=f'Сборочное задание ещё формируется: обработано {processed}/{total or "?"} позиций. Промежуточный Excel не выдаётся.')
    wb = _external_pick_workbook(db,batch_id=batch_id,client_id=client_id,warehouse_id=warehouse_id)
    db.commit()  # Persist the append-only route snapshot; quantities are already posted.
    out=io.BytesIO(); wb.save(out); out.seek(0)
    return StreamingResponse(out,media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',headers={'Content-Disposition':f'attachment; filename="FBS_pick_task_{batch_id[:8]}.xlsx"'})


# FC_K9R38_NO_AUTO_CANCEL: original r38 automatic rollback was forbidden; r42 adds only an explicit audited reversal.
@router.post('/external-picking/{batch_id}/cancel')
def external_pick_cancel(batch_id: str, payload: ExternalPickCancelRequest, db: Session = Depends(get_db)):
    """Return only actually WRITTEN_OFF units of one external FBS task.

    The exact original location/container is restored as FBS_AVAILABLE. NEW/PICKING
    reservations are not released or consumed. WB is then reconciled asynchronously
    through the guarded SINGLE LATEST TRUTH outbox (an upward restore still requires
    fresh NEW-order truth before publication).
    """
    ensure_runtime_schema(db)
    cred=_active_wb_credential(db,payload.client_id)
    if not cred:
        raise HTTPException(status_code=409,detail='Подключение WB для выбранного клиента не настроено')
    batch=db.execute(text("""SELECT * FROM fc_external_pick_batches WHERE id=:id AND client_id=:client AND warehouse_id=:warehouse FOR UPDATE"""),
                     {'id':batch_id,'client':str(payload.client_id),'warehouse':str(payload.warehouse_id)}).mappings().first()
    if not batch:
        raise HTTPException(status_code=404,detail='Сборочное задание не найдено')
    if str(batch.get('status') or '').upper()=='CANCELLED':
        return {'ok':True,'already_cancelled':True,'batch_id':batch_id,'task_number':batch.get('task_number'),'restored_units':0}
    allocs=db.execute(text("""
        SELECT * FROM fc_external_pick_allocations
        WHERE batch_id=:batch AND client_id=:client AND warehouse_id=:warehouse AND status='WRITTEN_OFF'
        ORDER BY created_at,id
        FOR UPDATE
    """),{'batch':batch_id,'client':str(payload.client_id),'warehouse':str(payload.warehouse_id)}).mappings().all()
    if not allocs:
        raise HTTPException(status_code=409,detail=f'В задании №{batch.get("task_number") or "—"} нет фактически списанных единиц для возврата')
    lines=[]; affected=[]
    for row in allocs:
        try:
            sku_id=uuid.UUID(str(row['sku_id'])); location_id=uuid.UUID(str(row['location_id']))
            container_id=uuid.UUID(str(row['container_id'])) if row.get('container_id') else None
        except Exception as exc:
            raise HTTPException(status_code=409,detail=f'Не удалось восстановить исходную ячейку списания: {exc}')
        qty=Decimal(str(row.get('quantity') or 0))
        if qty<=0: continue
        lines.append(StockLine(sku_id=sku_id,quantity=qty,destination_location_id=location_id,destination_container_id=container_id,
                               reason=f'EXTERNAL_PICK_TASK_CANCEL:{batch.get("task_number") or batch_id}'))
        affected.append((sku_id,location_id,container_id,qty))
    if not lines:
        raise HTTPException(status_code=409,detail='Нет положительного списанного количества для возврата')
    task=str(batch.get('task_number') or batch_id)
    execute_operation(db,OperationCreate(client_id=payload.client_id,warehouse_id=payload.warehouse_id,operation_type='RECEIVING',
        idempotency_key=f'external-pick-cancel:{batch_id}'[:128],performed_by=payload.performed_by or None,
        reference=f'Отмена сборочного задания №{task}',comment=f'Возврат на остатки по отмене сборочного листа №{task}. Резервы NEW/PICKING не изменялись.',lines=lines),commit=False)
    # Restore category mirror only in the exact original buckets; positive delta goes to FBS.
    for sku_id,location_id,container_id,_qty in affected:
        reconcile_stock_bucket(db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,sku_id=sku_id,
                               location_id=location_id,container_id=container_id,preferred_bucket='FBS_AVAILABLE',lock=True)
    db.execute(text("""UPDATE fc_external_pick_allocations SET status='RETURNED',cancelled_at=CURRENT_TIMESTAMP WHERE batch_id=:batch AND status='WRITTEN_OFF'"""),{'batch':batch_id})
    restored=sum(int(qty) for *_rest,qty in affected)
    db.execute(text("""UPDATE fc_external_pick_batches SET status='CANCELLED',cancelled_at=CURRENT_TIMESTAMP,wb_sync_status='PENDING_RESTORE',
                       wb_sync_error=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=:id"""),{'id':batch_id})
    selected_wb_id=_work_wb_warehouse_id(cred,payload.wb_warehouse_id,payload.warehouse_id)
    for sku_id in sorted({x[0] for x in affected},key=str):
        sku=db.get(SKU,sku_id)
        if sku:
            enqueue_wb_stock_for_sku(db,client_id=payload.client_id,warehouse_id=payload.warehouse_id,sku=sku,
                                     credential_id=cred.id,external_warehouse_id=selected_wb_id,reason='EXTERNAL_PICK_CANCEL_RESTORE')
    db.commit(); _wake_wb_stock_outbox()
    return {'ok':True,'batch_id':batch_id,'task_number':task,'restored_units':restored,'wb_sync_status':'PENDING_RESTORE'}


@router.post('/external-picking/cancel-last')
def external_pick_cancel_last(payload: ExternalPickCancelRequest, db: Session = Depends(get_db)):
    ensure_runtime_schema(db)
    batch_id=db.execute(text("""
        SELECT b.id FROM fc_external_pick_batches b
        WHERE b.client_id=:client AND b.warehouse_id=:warehouse AND b.status<>'CANCELLED'
          AND EXISTS(SELECT 1 FROM fc_external_pick_allocations a WHERE a.batch_id=b.id AND a.status='WRITTEN_OFF')
        ORDER BY b.created_at DESC LIMIT 1
    """),{'client':str(payload.client_id),'warehouse':str(payload.warehouse_id)}).scalar()
    if not batch_id:
        raise HTTPException(status_code=409,detail='Нет сформированного сборочного задания FBS, которое можно отменить')
    return external_pick_cancel(str(batch_id),payload,db)


def _template_workbook(kind: str) -> tuple[Workbook, str]:
    """Create the official receiving template used by FulfillCore imports.

    Product identity is always the exact WB barcode. Article/size/box names are
    descriptive and may contain arbitrary text. Barcode cells are formatted as text
    so Excel never converts them to scientific notation or trims leading zeroes.
    """
    key = str(kind or "").strip().upper()
    wb = Workbook()
    ws = wb.active
    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False

    if key == "BULK_BOX5":
        ws.title = "Приёмка с коробами"
        headers = ["Короб", "ШК товара", "Количество", "Артикул", "Размер"]
        example = ["BOX-001", "2043110552746", 10, "030179", "3XL-4XL"]
        filename = "FulfillCore_шаблон_приемка_короба_5_колонок.xlsx"
    elif key == "BULK_SIMPLE2":
        ws.title = "Приёмка без адреса"
        headers = ["ШК товара", "Количество"]
        example = ["2043110552746", 10]
        filename = "FulfillCore_шаблон_приемка_2_колонки.xlsx"
    elif key == "PALLET_LAYOUT5":
        ws.title = "Товар по коробам"
        headers = ["Короб", "ШК товара", "Количество", "Артикул", "Размер"]
        example = ["BOX-001", "2043110552746", 10, "030179", "3XL-4XL"]
        filename = "FulfillCore_шаблон_палетная_приемка_товар.xlsx"
    elif key == "STRUCTURED":
        ws.title = "Адресное хранение"
        headers = list(STRUCTURED_HEADERS)
        example_map = {
            "Секция": "С3", "Ряд": 1, "Паллета": "П40", "Лимит": 16, "Короб": "BOX-001",
            "Партия": "Поставка 937", "Источник": "WB", "Артикул": "030179",
            "Размер": "3XL-4XL", "ШК товара": "2043110552746", "Остаток": 10,
        }
        example = [example_map.get(h, "") for h in headers]
        filename = "FulfillCore_шаблон_структурная_приемка.xlsx"
    else:
        raise HTTPException(status_code=404, detail="Неизвестный шаблон приёмки")

    from openpyxl.styles import Font, PatternFill, Alignment
    header_fill = PatternFill("solid", fgColor="EDE9FE")
    input_fill = PatternFill("solid", fgColor="EFF6FF")
    note_fill = PatternFill("solid", fgColor="FFF7ED")
    for col, value in enumerate(headers, 1):
        cell = ws.cell(1, col, value)
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.cell(2, col, example[col - 1])
        ws.cell(2, col).fill = input_fill
        width = max(14, min(34, len(str(value)) + 6))
        ws.column_dimensions[cell.column_letter].width = width
    for idx, header in enumerate(headers, 1):
        if "ШК" in header or "штрих" in header.lower():
            for row in range(2, 5002):
                ws.cell(row, idx).number_format = "@"
    ws.auto_filter.ref = f"A1:{ws.cell(1, len(headers)).column_letter}2"
    ws.row_dimensions[1].height = 30
    ws.row_dimensions[2].height = 24

    note_row = 4
    ws.cell(note_row, 1, "Правило FulfillCore")
    ws.cell(note_row, 1).font = Font(bold=True)
    ws.cell(note_row, 1).fill = note_fill
    ws.cell(note_row + 1, 1, "ШК товара — главный идентификатор. Он должен ТОЧНО совпадать с ШК размера в карточке WB выбранного клиента. Названия коробов, артикул и размер могут быть любыми; FulfillCore сверяет WB по ШК.")
    ws.merge_cells(start_row=note_row + 1, start_column=1, end_row=note_row + 2, end_column=len(headers))
    ws.cell(note_row + 1, 1).alignment = Alignment(wrap_text=True, vertical="top")
    ws.cell(note_row + 1, 1).fill = note_fill
    return wb, filename


@router.get("/templates/{kind}.xlsx")
def receiving_template(kind: str):
    wb, filename = _template_workbook(kind)
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    # HTTP headers are latin-1 encoded by ASGI servers. A raw Cyrillic filename in
    # Content-Disposition therefore raises UnicodeEncodeError and returns HTTP 500.
    # Keep an ASCII fallback plus RFC 5987 UTF-8 filename so Chrome/Edge download the
    # template with its readable Russian name without crashing the endpoint.
    from urllib.parse import quote
    safe_ascii = f"FulfillCore_receiving_{str(kind or 'template').strip().upper()}.xlsx"
    content_disposition = (
        f'attachment; filename="{safe_ascii}"; '
        f"filename*=UTF-8''{quote(filename)}"
    )
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": content_disposition},
    )


def _strict_wb_receiving_errors(db: Session, client_id: uuid.UUID, barcodes: list[tuple[int, str]]) -> list[str]:
    """Validate exact WB barcode identity before an Excel receipt changes stock.

    When the client has no WB credential we keep legacy local-only receiving available.
    With WB connected, an Excel receipt must not silently create a local-only SKU.
    """
    if not _active_wb_credential(db, client_id):
        return []
    normalized_rows: list[tuple[int, str]] = []
    wanted: set[str] = set()
    for row_number, raw in barcodes:
        normalized, _duplicated = normalize_product_barcode(raw)
        if not normalized:
            continue
        normalized_rows.append((row_number, normalized))
        wanted.add(normalized)
    identities = wb_product_identity_map(db, client_id, wanted) if wanted else {}
    return [
        f"строка {row_number}: ШК {normalized} не найден в карточках WB выбранного клиента"
        for row_number, normalized in normalized_rows
        if normalized not in identities
    ]


@router.post("/bulk")
async def bulk_receive(
    client_id: uuid.UUID = Form(...),
    warehouse_id: uuid.UUID = Form(...),
    format: Literal["ADDRESS5", "NOADDRESS2"] = Form(...),
    performed_by: str | None = Form(None),
    reference: str | None = Form(None),
    wb_warehouse_id: int | None = Form(None),
    stock_class: Literal["FBS", "MAIN"] = Form("FBS"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    rows = _rows_from_workbook(content)
    if rows and _looks_header(rows[0]):
        rows = rows[1:]
    if not rows:
        raise HTTPException(status_code=400, detail="В файле нет строк")

    # Preflight the whole workbook before the first stock write. This prevents a
    # half-import where some rows are local-only merely because their WB barcode
    # was missing/malformed. Article/size/box values are descriptive; WB identity
    # is determined only by the exact product barcode.
    wb_barcode_rows: list[tuple[int, str]] = []
    for idx, row in enumerate(rows, start=1):
        raw = (list(row) + [None] * 5)[1 if format == "ADDRESS5" else 0]
        code, _dup = normalize_product_barcode(str(raw or "").strip())
        if code:
            wb_barcode_rows.append((idx, code))
    # WB catalogue matching must never block physical warehouse receiving.
    # Keep mismatches as warnings for the FBS step, but accept valid local rows.
    wb_preflight_errors = _strict_wb_receiving_errors(db, client_id, wb_barcode_rows)

    imported = 0
    errors = []
    wb_changes: dict[str, int] = {}
    stock_lines: list[StockLine] = []
    batch_id = uuid.uuid4().hex[:12]
    created_locations = 0
    created_containers = 0
    default_loc = _ensure_location(db, warehouse_id, "БЕЗ-АДРЕСА" if format == "NOADDRESS2" else "ПРИЕМКА")

    # Resolve reusable warehouse entities in sets before processing individual rows.
    # This removes thousands of SELECT/flush round-trips from large Excel receipts.
    place_codes: set[str] = set()
    barcode_meta: dict[str, tuple[str | None, str | None]] = {}
    for raw_row in rows:
        values = list(raw_row) + [None] * 5
        try:
            if format == "ADDRESS5":
                place = str(values[0] or "").strip()
                raw_barcode = str(values[1] or "").strip()
                row_quantity = Decimal(str(values[2] or "0"))
                article_value = str(values[3] or "").strip() or None
                size_value = str(values[4] or "").strip() or None
                if not place:
                    continue
            else:
                place = ""
                raw_barcode = str(values[0] or "").strip()
                row_quantity = Decimal(str(values[1] or "0"))
                article_value = None
                size_value = None
        except Exception:
            continue
        code, _duplicated = normalize_product_barcode(raw_barcode)
        if not code or row_quantity <= 0:
            continue
        if place:
            place_codes.add(place)
        if code not in barcode_meta:
            barcode_meta[code] = (article_value, size_value)

    container_cache: dict[str, Container] = {}
    new_containers: list[Container] = []
    if place_codes:
        existing_containers = db.execute(select(Container).where(
            Container.client_id == client_id,
            Container.warehouse_id == warehouse_id,
            or_(Container.barcode.in_(list(place_codes)), Container.name.in_(list(place_codes))),
        )).scalars().all()
        for cont in existing_containers:
            for key in (str(cont.barcode or "").strip(), str(cont.name or "").strip()):
                if key in place_codes and key not in container_cache:
                    container_cache[key] = cont
        for code in sorted(place_codes):
            if code in container_cache:
                continue
            cont = Container(
                id=uuid.uuid4(), client_id=client_id, warehouse_id=warehouse_id,
                location_id=default_loc.id,
                barcode=_fc_box_storage_barcode(db, client_id, warehouse_id, code), name=code,
                container_type="BOX", status="ACTIVE",
            )
            container_cache[code] = cont
            new_containers.append(cont)
        if new_containers:
            db.add_all(new_containers)
    created_containers = len(new_containers)

    location_ids = {
        cont.location_id for cont in container_cache.values()
        if cont.location_id is not None and cont.warehouse_id == warehouse_id
    }
    location_cache = {default_loc.id: default_loc}
    if location_ids:
        location_cache.update({
            loc.id: loc for loc in db.execute(
                select(Location).where(Location.id.in_(list(location_ids)))
            ).scalars().all()
        })

    wanted_barcodes = set(barcode_meta)
    sku_cache: dict[str, SKU] = {}
    if wanted_barcodes:
        existing_skus = db.execute(select(SKU).where(
            SKU.client_id == client_id, SKU.barcode.in_(list(wanted_barcodes))
        )).scalars().all()
        sku_cache = {str(row.barcode or ""): row for row in existing_skus if row.barcode}
        identity_map = wb_product_identity_map(db, client_id, wanted_barcodes)
        new_skus: list[SKU] = []
        for code in sorted(wanted_barcodes):
            supplied_article, supplied_size = barcode_meta.get(code, (None, None))
            product = identity_map.get(code)
            sku = sku_cache.get(code)
            if sku is not None:
                if product:
                    sku.article = product.get("article") or sku.article
                    sku.name = product.get("name") or sku.name
                    sku.size = product.get("size") or supplied_size or sku.size
                else:
                    if supplied_article and (not sku.article or sku.article in {sku.barcode, sku.sku_code}):
                        sku.article = supplied_article
                    if supplied_article and (not sku.name or sku.name in {sku.barcode, sku.sku_code, sku.article}):
                        sku.name = supplied_article
                    if supplied_size and not sku.size:
                        sku.size = supplied_size
                sku.is_active = True
                continue
            resolved_article = (product or {}).get("article") or supplied_article or code
            resolved_name = (product or {}).get("name") or resolved_article
            resolved_size = (product or {}).get("size") or supplied_size
            sku = SKU(
                id=uuid.uuid4(), client_id=client_id, sku_code=f"WB-{code}",
                article=resolved_article, name=resolved_name, size=resolved_size,
                barcode=code, unit="PCS", is_active=True,
            )
            sku_cache[code] = sku
            new_skus.append(sku)
        if new_skus:
            db.add_all(new_skus)
    db.flush()

    for idx, row in enumerate(rows, start=1):
        try:
            if format == "ADDRESS5":
                place, barcode, qty, article, size = (list(row) + [None] * 5)[:5]
                place = str(place or "").strip()
                if not place:
                    raise ValueError("нет короба в первом столбце")
                barcode = str(barcode or "").strip()
                qty = Decimal(str(qty or "0"))
                if not barcode or qty <= 0:
                    raise ValueError("некорректный ШК или количество")

                # The first ADDRESS5 column is a physical box, not an address guess.
                # Older releases silently treated values matching an existing cell as
                # direct placement. The box then disappeared from the picking list and
                # could not be renamed or moved later. Always create/reuse the box and
                # keep it in its current address (new boxes start in ПРИЕМКА).
                cont = container_cache.get(place)
                if cont is None:
                    cont = _ensure_container(db, client_id, warehouse_id, default_loc.id, place)
                    if cont is not None:
                        container_cache[place] = cont
                if cont is None:
                    raise ValueError("не удалось создать короб")
                if cont.warehouse_id != warehouse_id:
                    _ensure_container(db, client_id, warehouse_id, default_loc.id, place)
                if str(cont.status or "").upper() != "ACTIVE":
                    cont.status = "ACTIVE"
                    cont.location_id = default_loc.id
                if cont.location_id is None:
                    cont.location_id = default_loc.id
                loc = location_cache.get(cont.location_id) or default_loc
                if loc.warehouse_id != warehouse_id:
                    cont.location_id = default_loc.id
                    loc = default_loc
            else:
                barcode, qty = (list(row) + [None] * 2)[:2]
                article = size = None
                loc, cont = default_loc, None
            barcode = str(barcode or "").strip()
            qty = Decimal(str(qty or "0"))
            if not barcode or qty <= 0:
                raise ValueError("некорректный ШК или количество")
            normalized_code, _duplicated = normalize_product_barcode(barcode)
            sku = sku_cache.get(normalized_code)
            if sku is None:
                sku = _find_or_create_sku(db, client_id, barcode, str(article or "").strip() or None, str(size or "").strip() or None)
                sku_cache[normalized_code] = sku
            stock_lines.append(StockLine(
                sku_id=sku.id,
                quantity=qty,
                destination_location_id=loc.id,
                destination_container_id=cont.id if cont else None,
                reason="BULK_ADD",
            ))
            normalized_barcode = str(sku.barcode or barcode)
            wb_changes[normalized_barcode] = wb_changes.get(normalized_barcode, 0) + int(qty)
            imported += 1
        except Exception as exc:
            errors.append({"row": idx, "error": str(getattr(exc, "detail", exc))[:250]})

    # Commit all valid Excel rows as ONE warehouse operation instead of thousands
    # of individual transactions. This is dramatically faster and prevents the UI
    # from appearing frozen on large receiving files.
    operation = None
    if stock_lines:
        try:
            operation = execute_operation(db, OperationCreate(
                client_id=client_id,
                warehouse_id=warehouse_id,
                operation_type="RECEIVING_IMPORT",
                idempotency_key=f"bulk-receive-{batch_id}",
                performed_by=performed_by,
                reference=reference,
                comment=f"Массовая приёмка {format} · ДОБАВИТЬ · BATCH:{batch_id} · ЗОНА:{stock_class}",
                lines=stock_lines,
            ))
        except Exception:
            db.rollback()
            raise

    # FC_K9R62_WAREHOUSE_SPLIT: MAIN rows remain physical but are explicitly
    # STORAGE_ONLY. FBS keeps the existing saleable classification.
    if stock_lines and stock_class == "MAIN":
        seen_buckets = set()
        for line in stock_lines:
            key = (line.sku_id, line.destination_location_id, line.destination_container_id)
            if key in seen_buckets:
                continue
            seen_buckets.add(key)
            reconcile_stock_bucket(
                db, client_id=client_id, warehouse_id=warehouse_id, sku_id=line.sku_id,
                location_id=line.destination_location_id, container_id=line.destination_container_id,
                preferred_bucket="STORAGE_ONLY", lock=True,
            )
        db.commit()

    # Массовая приёмка сначала меняет только локальный склад; WB — отдельное действие.
    pending_lines = [
        {
            "barcode": barcode, "quantity": int(added), "mode": "ADD",
            "idempotency_key": _wb_pending_key(f"bulk:{batch_id}", barcode),
        }
        for barcode, added in wb_changes.items()
    ]
    if stock_class != "FBS":
        pending_lines = []
    return {
        "ok": True, "format": format, "stock_class": stock_class, "rows_imported": imported, "rows_failed": len(errors), "errors": errors[:30],
        "warnings": wb_preflight_errors[:100],
        "warning_count": len(wb_preflight_errors),
        "created_locations": created_locations, "created_containers": created_containers,
        "batch_id": batch_id,
        "operation_id": str(getattr(operation, "id", "") or "") or None,
        "wb_pending": bool(pending_lines), "pending_lines": pending_lines,
    }



@router.get("/bulk/{batch_id}/errors.xlsx")
def bulk_receiving_errors_excel(
    batch_id: str,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Download an Excel report with WB/FBS problems for one completed bulk receipt."""
    token = str(batch_id or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{8,32}", token):
        raise HTTPException(status_code=400, detail="Некорректный номер массовой приёмки")

    operations = db.execute(
        select(InventoryOperation).where(
            InventoryOperation.client_id == client_id,
            InventoryOperation.warehouse_id == warehouse_id,
            InventoryOperation.operation_type == "RECEIVING_IMPORT",
            InventoryOperation.comment.ilike(f"%BATCH:{token}%"),
        ).order_by(InventoryOperation.created_at.asc())
    ).scalars().all()
    if not operations:
        raise HTTPException(status_code=404, detail="Массовая приёмка не найдена")

    sku_ids: set[uuid.UUID] = set()
    for op in operations:
        for line in db.execute(select(InventoryOperationLine).where(
            InventoryOperationLine.operation_id == op.id
        )).scalars().all():
            sku_ids.add(line.sku_id)

    wb = Workbook()
    ws = wb.active
    ws.title = "Ошибки WB FBS"
    headers = ["ШК", "Артикул", "Размер", "Локальный остаток", "WB FBS", "Статус", "Причина"]
    ws.append(headers)

    for sku_id in sku_ids:
        sku = db.get(SKU, sku_id)
        if not sku:
            continue
        barcode = str(sku.barcode or sku.sku_code or "").strip()
        local_qty = int(_warehouse_barcode_qty(db, client_id, warehouse_id, barcode) or 0)

        marker = db.execute(text("""
            SELECT status, published_amount, last_error
            FROM fc_wb_stock_publications  /* FC_BULK_ERRORS_EXCEL_TABLE_FIX_09415G2_20260903 */
            WHERE client_id=:client_id
              AND warehouse_id=:warehouse_id
              AND barcode=:barcode
            ORDER BY updated_at DESC NULLS LAST
            LIMIT 1
        """), {
            "client_id": str(client_id),
            "warehouse_id": str(warehouse_id),
            "barcode": barcode,
        }).mappings().first()

        status = str((marker or {}).get("status") or "NOT_SENT")
        published = int((marker or {}).get("published_amount") or 0)
        error = str((marker or {}).get("last_error") or "")
        if status == "PUBLISHED" and published == local_qty:
            continue

        if not error:
            if status == "NOT_SENT":
                error = "Остаток ещё не отправлялся на WB FBS"
            elif status == "PUBLISHED" and published != local_qty:
                error = f"На WB {published} шт., на складе FulfillCore {local_qty} шт."
            else:
                error = "Остаток WB FBS не подтверждён"

        ws.append([
            barcode,
            str(sku.article or sku.sku_code or ""),
            str(sku.size or ""),
            local_qty,
            published,
            status,
            error,
        ])

    if ws.max_row == 1:
        ws.append(["", "", "", "", "", "OK", "Ошибок WB FBS по этой приёмке не найдено"])

    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    widths = [20, 20, 16, 18, 14, 18, 70]
    for idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = width

    out_io = io.BytesIO()
    wb.save(out_io)
    out_io.seek(0)
    filename = f"FulfillCore_bulk_{token}_errors.xlsx"
    return StreamingResponse(
        out_io,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )



# FC_K9R47_BULK_RECEIVING_DETAILS_XLSX: the Operations page has always rendered a
# "Состав в Excel" button for Excel receipts, but older backends exposed only
# /detail JSON and /errors.xlsx. Keep the UI contract explicit and export the
# immutable receiving composition + original address/box for both ACTIVE and
# CANCELLED batches. This endpoint is read-only and does not reconcile stock/WB.
@router.get("/bulk/{batch_id}/details.xlsx")
def bulk_receiving_details_excel(
    batch_id: str,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    token = str(batch_id or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{8,32}", token):
        raise HTTPException(status_code=400, detail="Некорректный номер массовой приёмки")

    operations = db.execute(
        select(InventoryOperation).where(
            InventoryOperation.client_id == client_id,
            InventoryOperation.warehouse_id == warehouse_id,
            InventoryOperation.operation_type == "RECEIVING_IMPORT",
            InventoryOperation.comment.ilike(f"%BATCH:{token}%"),
        ).order_by(InventoryOperation.created_at.asc())
    ).scalars().all()
    if not operations:
        raise HTTPException(status_code=404, detail="Массовая приёмка не найдена")

    operation_ids = [op.id for op in operations]
    lines = db.execute(
        select(InventoryOperationLine).where(
            InventoryOperationLine.operation_id.in_(operation_ids)
        )
    ).scalars().all() if operation_ids else []

    op_by_id = {str(op.id): op for op in operations}
    sku_ids = {line.sku_id for line in lines if line.sku_id}
    loc_ids = {
        value for line in lines
        for value in (line.source_location_id, line.destination_location_id)
        if value is not None
    }
    cont_ids = {
        value for line in lines
        for value in (line.source_container_id, line.destination_container_id)
        if value is not None
    }
    sku_map = {
        str(row.id): row
        for row in db.execute(select(SKU).where(SKU.id.in_(list(sku_ids)))).scalars().all()
    } if sku_ids else {}
    loc_map = {
        str(row.id): row
        for row in db.execute(select(Location).where(Location.id.in_(list(loc_ids)))).scalars().all()
    } if loc_ids else {}
    cont_map = {
        str(row.id): row
        for row in db.execute(select(Container).where(Container.id.in_(list(cont_ids)))).scalars().all()
    } if cont_ids else {}

    def _qty(value):
        number = Decimal(str(value or 0))
        return int(number) if number == number.to_integral_value() else float(number)

    def _loc_label(value):
        if value is None:
            return ""
        row = loc_map.get(str(value))
        return str(getattr(row, "code", None) or getattr(row, "name", None) or value)

    def _cont_label(value):
        if value is None:
            return ""
        row = cont_map.get(str(value))
        return str(getattr(row, "name", None) or getattr(row, "barcode", None) or value)

    grouped: dict[tuple, dict] = {}
    historical_units = Decimal("0")
    current_units = Decimal("0")
    for line in lines:
        op = op_by_id.get(str(line.operation_id))
        if op is None:
            continue
        sku = sku_map.get(str(line.sku_id))
        qty = Decimal(str(line.quantity or 0))
        historical_units += qty
        active = str(op.status or "COMPLETED").upper() != "CANCELLED"
        if active:
            current_units += qty
        # Receiving destination is the original placement. Fall back to source only
        # for legacy rows whose direction was stored differently.
        location_id = line.destination_location_id or line.source_location_id
        container_id = line.destination_container_id or line.source_container_id
        barcode = str(getattr(sku, "barcode", None) or getattr(sku, "sku_code", None) or "")
        article = str(getattr(sku, "article", None) or "")
        size = str(getattr(sku, "size", None) or "")
        name = str(getattr(sku, "name", None) or "")
        status = "Активна" if active else "Отменена"
        key = (barcode, article, size, name, str(location_id or ""), str(container_id or ""), status)
        item = grouped.setdefault(key, {
            "barcode": barcode,
            "article": article,
            "size": size,
            "name": name,
            "location": _loc_label(location_id),
            "container": _cont_label(container_id),
            "status": status,
            "historical": Decimal("0"),
            "current": Decimal("0"),
        })
        item["historical"] += qty
        if active:
            item["current"] += qty

    wb = Workbook()
    ws = wb.active
    ws.title = "Состав приёмки"
    headers = [
        "ШК", "Артикул", "Размер", "Товар", "Принято, шт.", "Сейчас учтено, шт.",
        "Ячейка при приёмке", "Короб при приёмке", "Статус приёмки",
    ]
    ws.append(headers)
    for item in sorted(
        grouped.values(),
        key=lambda x: (x["location"], x["container"], x["article"], x["size"], x["barcode"]),
    ):
        ws.append([
            item["barcode"], item["article"], item["size"], item["name"],
            _qty(item["historical"]), _qty(item["current"]), item["location"],
            item["container"], item["status"],
        ])
    if ws.max_row == 1:
        ws.append(["", "", "", "", 0, 0, "", "", "В приёмке нет строк"])

    raw = wb.create_sheet("Исходные движения")
    raw_headers = [
        "Дата", "Операция", "Статус", "ШК", "Артикул", "Размер", "Товар",
        "Количество", "Откуда", "Короб откуда", "Куда", "Короб куда",
        "Сотрудник", "Основание", "ID операции",
    ]
    raw.append(raw_headers)
    for line in sorted(
        lines,
        key=lambda x: (
            str(getattr(op_by_id.get(str(x.operation_id)), "created_at", "") or ""),
            str(getattr(x, "id", "")),
        ),
    ):
        op = op_by_id.get(str(line.operation_id))
        sku = sku_map.get(str(line.sku_id))
        if op is None:
            continue
        active = str(op.status or "COMPLETED").upper() != "CANCELLED"
        created = getattr(op, "created_at", None)
        if created is not None and getattr(created, "tzinfo", None):
            created = created.replace(tzinfo=None)
        raw.append([
            created,
            "Приёмка Excel",
            "Активна" if active else "Отменена",
            str(getattr(sku, "barcode", None) or getattr(sku, "sku_code", None) or ""),
            str(getattr(sku, "article", None) or ""),
            str(getattr(sku, "size", None) or ""),
            str(getattr(sku, "name", None) or ""),
            _qty(line.quantity),
            _loc_label(line.source_location_id),
            _cont_label(line.source_container_id),
            _loc_label(line.destination_location_id),
            _cont_label(line.destination_container_id),
            str(op.performed_by or ""),
            str(op.reference or op.comment or ""),
            str(op.id),
        ])

    summary = wb.create_sheet("Итоги")
    active_ops = [op for op in operations if str(op.status or "COMPLETED").upper() != "CANCELLED"]
    first = operations[0]
    first_created = getattr(first, "created_at", None)
    if first_created is not None and getattr(first_created, "tzinfo", None):
        first_created = first_created.replace(tzinfo=None)
    summary_rows = [
        ("Batch ID", token),
        ("Статус", "Активна" if active_ops else "Отменена"),
        ("Операций", len(operations)),
        ("Строк состава", len(grouped)),
        ("Принято исторически, шт.", _qty(historical_units)),
        ("Сейчас учтено как принято, шт.", _qty(current_units)),
        ("Сотрудник", str(first.performed_by or "")),
        ("Основание", str(first.reference or first.comment or "")),
        ("Дата первой операции", first_created),
    ]
    for key, value in summary_rows:
        summary.append([key, value])

    header_fill = PatternFill("solid", fgColor="E2E8F0")
    for sheet in (ws, raw):
        for cell in sheet[1]:
            cell.font = Font(bold=True)
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for row in sheet.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
    for cell in summary["A"]:
        cell.font = Font(bold=True)
    widths = [22, 20, 14, 34, 16, 19, 22, 24, 18]
    for idx, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(idx)].width = width
    raw_widths = [20, 18, 16, 22, 20, 14, 34, 14, 20, 22, 20, 22, 18, 44, 38]
    for idx, width in enumerate(raw_widths, 1):
        raw.column_dimensions[get_column_letter(idx)].width = width
    summary.column_dimensions["A"].width = 34
    summary.column_dimensions["B"].width = 70

    out_io = io.BytesIO()
    wb.save(out_io)
    out_io.seek(0)
    # Keep the header ASCII-safe; app.js already supplies the Russian download name.
    filename = f"FulfillCore_bulk_{token}_details.xlsx"
    return StreamingResponse(
        out_io,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/bulk/{batch_id}/detail")
def bulk_receiving_detail(
    batch_id: str,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    token = str(batch_id or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{8,32}", token):
        raise HTTPException(status_code=400, detail="Некорректный номер массовой приёмки")

    operations = db.execute(
        select(InventoryOperation).where(
            InventoryOperation.client_id == client_id,
            InventoryOperation.warehouse_id == warehouse_id,
            InventoryOperation.operation_type == "RECEIVING_IMPORT",
            InventoryOperation.comment.ilike(f"%BATCH:{token}%"),
        ).order_by(InventoryOperation.created_at.asc())
    ).scalars().all()
    if not operations:
        raise HTTPException(status_code=404, detail="Массовая приёмка не найдена")

    by_barcode: dict[str, int] = {}
    for op in operations:
        for line in db.execute(select(InventoryOperationLine).where(
            InventoryOperationLine.operation_id == op.id
        )).scalars().all():
            sku = db.get(SKU, line.sku_id)
            if not sku:
                continue
            barcode = str(sku.barcode or sku.sku_code or "").strip()
            if not barcode:
                continue
            by_barcode[barcode] = by_barcode.get(barcode, 0) + int(line.quantity or 0)

    return {
        "batch_id": token,
        "pending_lines": [
            {
                "barcode": barcode, "quantity": qty, "mode": "ADD",
                "idempotency_key": _wb_pending_key(f"bulk:{token}", barcode),
            }
            for barcode, qty in by_barcode.items()
        ],
    }


@router.get("/bulk/recent")
def recent_bulk_receiving_batches(
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """Return recent Excel receiving batches so accidental duplicate imports can be undone."""
    limit = max(1, min(int(limit or 20), 100))
    rows = db.execute(
        select(InventoryOperation).where(
            InventoryOperation.client_id == client_id,
            InventoryOperation.warehouse_id == warehouse_id,
            InventoryOperation.operation_type == "RECEIVING_IMPORT",
            InventoryOperation.comment.ilike("%BATCH:%"),
        ).order_by(InventoryOperation.created_at.desc()).limit(20000)
    ).scalars().all()

    grouped: dict[str, dict] = {}
    for op in rows:
        comment = str(op.comment or "")
        match = re.search(r"BATCH:([0-9a-fA-F]{8,32})", comment)
        if not match:
            continue
        batch_id = match.group(1)
        item = grouped.setdefault(batch_id, {
            "batch_id": batch_id,
            "created_at": op.created_at,
            "reference": op.reference,
            "performed_by": op.performed_by,
            "status": "CANCELLED",
            "operations": 0,
            "active_operations": 0,
            # rows/units are what is still counted as an accepted receipt now.
            "rows": 0,
            "units": 0.0,
            # historical_* keeps the immutable fact that the receipt once existed.
            "historical_rows": 0,
            "historical_units": 0.0,
            "cancelled_rows": 0,
            "cancelled_units": 0.0,
        })
        item["operations"] += 1
        active = str(op.status or "COMPLETED").upper() != "CANCELLED"
        if active:
            item["active_operations"] += 1
            item["status"] = "ACTIVE"

        lines = db.execute(select(InventoryOperationLine).where(
            InventoryOperationLine.operation_id == op.id
        )).scalars().all()
        line_units = sum(float(line.quantity or 0) for line in lines)
        item["historical_rows"] += len(lines)
        item["historical_units"] += line_units
        if active:
            item["rows"] += len(lines)
            item["units"] += line_units
        else:
            item["cancelled_rows"] += len(lines)
            item["cancelled_units"] += line_units

        if op.created_at and (not item["created_at"] or op.created_at > item["created_at"]):
            item["created_at"] = op.created_at
        if not item.get("reference") and op.reference:
            item["reference"] = op.reference

    result = sorted(
        grouped.values(),
        key=lambda x: x.get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:limit]
    for item in result:
        if item.get("created_at"):
            item["created_at"] = item["created_at"].isoformat()
    return {"rows": result}


@router.post("/bulk/{batch_id}/cancel")
# FC_RECEIVING_CANCEL_STRICT_SCOPE_09415G1_20260903
def cancel_bulk_receiving_batch(
    batch_id: str,
    payload: CancelReceivingRequest,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    token = str(batch_id or "").strip()
    if not re.fullmatch(r"[0-9a-fA-F]{8,32}", token):
        raise HTTPException(status_code=400, detail="Некорректный номер массовой приёмки")
    operations = db.execute(
        select(InventoryOperation).where(
            InventoryOperation.client_id == client_id,
            InventoryOperation.warehouse_id == warehouse_id,
            InventoryOperation.operation_type == "RECEIVING_IMPORT",
            InventoryOperation.comment.ilike(f"%BATCH:{token}%"),
        ).order_by(InventoryOperation.created_at.asc())
    ).scalars().all()
    if not operations:
        raise HTTPException(status_code=404, detail="Массовая приёмка не найдена")
    if any(str(op.status or "").upper() == "CANCELLED" for op in operations):
        active = [op for op in operations if str(op.status or "").upper() != "CANCELLED"]
    else:
        active = operations
    if not active:
        return {"ok": True, "already_cancelled": True, "batch_id": token, "cancelled_operations": 0, "affected_stocks": [], "wb_results": []}

    all_skus: set[uuid.UUID] = set()
    try:
        for operation in active:
            result = _reverse_receiving_operation(db, operation, commit=False)
            all_skus.update(result.pop("sku_ids", []))
        db.commit()
    except Exception:
        db.rollback()
        raise

    first = active[0]
    affected_stocks = _local_stock_rows(
        db, client_id=first.client_id, warehouse_id=first.warehouse_id, sku_ids=all_skus
    )
    wb_results = []
    if payload.sync_wb and all_skus:
        wb_results = _publish_skus_after_cancellation(
            db, client_id=first.client_id, warehouse_id=first.warehouse_id,
            sku_ids=all_skus, wb_warehouse_id=payload.wb_warehouse_id,
        )
    return {
        "ok": True, "batch_id": token, "cancelled_operations": len(active),
        "affected_stocks": affected_stocks, "wb_requested": payload.sync_wb,
        "wb_results": wb_results, "wb_failed": sum(1 for row in wb_results if not row.get("ok")),
    }


@router.post("/sync-wb")
def sync_receiving_to_wb(payload: SyncReceivingToWbRequest, db: Session = Depends(get_db)):
    """Publish receiving deltas to WB as one resumable, idempotent batch.

    ADD stays semantically correct for FBS (it preserves stock already consumed by WB orders),
    but a deterministic ``idempotency_key`` turns it into a resumable transaction: before the
    external PUT we persist the absolute target as IN_PROGRESS. If the process/browser loses
    the response, the next call continues toward that stored target instead of adding twice.
    """
    if not payload.lines:
        raise HTTPException(status_code=400, detail="Нет приёмки для отправки в WB")

    results: list[dict | None] = [None] * len(payload.lines)
    entries: list[dict] = []
    for index, line in enumerate(payload.lines):
        barcode = normalize_product_barcode(str(line.barcode or "").strip())[0]
        if not barcode:
            results[index] = {"barcode": "", "mode": line.mode, "ok": False, "error": "Пустой ШК"}
            continue
        entries.append({
            "index": index,
            "barcode": barcode,
            "mode": str(line.mode or "ADD").upper(),
            "quantity": int(line.quantity),
            "key": str(line.idempotency_key or "").strip() or None,
        })

    event_keys = {entry["key"] for entry in entries if entry["key"]}
    existing_events = _wb_publication_events(
        db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, keys=event_keys,
    ) if event_keys else {}

    sequences: dict[str, list[dict]] = {}
    resume_entries: dict[str, list[dict]] = {}
    resume_targets: dict[str, int] = {}
    resume_conflicts: set[str] = set()
    for entry in entries:
        event = existing_events.get(entry["key"]) if entry["key"] else None
        event_status = str((event or {}).get("status") or "").upper()
        if event_status == "PUBLISHED":
            results[entry["index"]] = {
                "barcode": entry["barcode"], "mode": entry["mode"], "ok": True,
                "after": (event or {}).get("resolved_amount"), "already_applied": True,
                "idempotency_key": entry["key"],
            }
            continue
        if event_status in {"IN_PROGRESS", "ERROR"} and (event or {}).get("resolved_amount") is not None:
            event_barcode = normalize_product_barcode(str((event or {}).get("barcode") or ""))[0]
            if event_barcode and event_barcode != entry["barcode"]:
                results[entry["index"]] = {
                    "barcode": entry["barcode"], "mode": entry["mode"], "ok": False,
                    "error": "Ключ публикации уже относится к другому ШК; операция остановлена",
                    "idempotency_key": entry["key"],
                }
                continue
            target = max(0, int((event or {}).get("resolved_amount") or 0))
            old_target = resume_targets.setdefault(entry["barcode"], target)
            if old_target != target:
                resume_conflicts.add(entry["barcode"])
            resume_entries.setdefault(entry["barcode"], []).append(entry)
            continue
        sequences.setdefault(entry["barcode"], []).append(entry)

    publication_rows: list[dict] = []
    event_updates: list[dict] = []
    selected_wb_id: int | None = None
    client: WBClient | None = None
    target_by_code: dict[str, int] = {}
    before_by_code: dict[str, int | None] = {}

    active_entries_by_code: dict[str, list[dict]] = {}
    for code, rows in resume_entries.items():
        active_entries_by_code.setdefault(code, []).extend(rows)
    for code, rows in sequences.items():
        active_entries_by_code.setdefault(code, []).extend(rows)

    def event_row(entry: dict, *, status: str, resolved_amount: int | None, error: str | None) -> dict | None:
        if not entry.get("key"):
            return None
        return {
            "client_id": str(payload.client_id), "warehouse_id": str(payload.warehouse_id),
            "idempotency_key": entry["key"], "barcode": entry["barcode"],
            "wb_warehouse_id": selected_wb_id or payload.wb_warehouse_id,
            "mode": entry["mode"], "requested_quantity": int(entry["quantity"]),
            "resolved_amount": resolved_amount, "status": status, "last_error": error,
        }

    def mark_error(code: str, message: str, *, resumable_target: int | None = None) -> None:
        clean = str(message or "Ошибка WB")[:300]
        for entry in active_entries_by_code.get(code, []):
            if results[entry["index"]] is None:
                results[entry["index"]] = {
                    "barcode": code, "mode": entry["mode"], "ok": False, "error": clean,
                    "idempotency_key": entry.get("key"), "resumable": resumable_target is not None,
                }
            row = event_row(
                entry,
                status="IN_PROGRESS" if resumable_target is not None else "ERROR",
                resolved_amount=resumable_target,
                error=clean,
            )
            if row:
                event_updates.append(row)
        publication_rows.append({
            "client_id": str(payload.client_id), "warehouse_id": str(payload.warehouse_id),
            "barcode": code, "wb_warehouse_id": selected_wb_id or payload.wb_warehouse_id,
            "amount": None, "status": "ERROR", "error": clean,
        })

    for code in sorted(resume_conflicts):
        mark_error(code, "Для одного ШК найдены разные незавершённые цели WB; автоматический повтор остановлен", resumable_target=resume_targets.get(code))
        active_entries_by_code.pop(code, None)
        sequences.pop(code, None)
        resume_entries.pop(code, None)
        resume_targets.pop(code, None)

    try:
        if active_entries_by_code:
            cred = _active_wb_credential(db, payload.client_id)
            if not cred:
                raise RuntimeError("Подключение WB для выбранного клиента не настроено")
            selected_wb_id = _work_wb_warehouse_id(cred, payload.wb_warehouse_id, payload.warehouse_id)
            if not selected_wb_id:
                raise RuntimeError("Для выбранного физического склада не назначен рабочий склад WB")
            client = WBClient(decrypt_secret(cred.encrypted_api_key))
            _wb_validate_seller_warehouse(client, int(selected_wb_id), cred=cred, physical_warehouse_id=payload.warehouse_id)

            all_codes = list(active_entries_by_code)
            chrt_map = _wb_chrt_map(db, client_id=payload.client_id, barcodes=all_codes)
            missing = [code for code in all_codes if code not in chrt_map]
            if missing:
                chrt_map.update(_wb_fresh_chrt_map(client, set(missing)))
            for code in [code for code in all_codes if code not in chrt_map]:
                mark_error(code, "ШК не найден в карточках WB или у размера нет chrtId", resumable_target=resume_targets.get(code))
                active_entries_by_code.pop(code, None)

            ready_codes = list(active_entries_by_code)

            def build_targets(active_map: dict[str, int], codes: list[str]):
                base_needed = []
                for code in codes:
                    if code in resume_targets:
                        continue
                    seq = sequences.get(code, [])
                    if not any(entry["mode"] == "SET" for entry in seq):
                        base_needed.append(code)
                base_by_chrt = _wb_read_chrt_stocks(
                    client, int(selected_wb_id), sorted({int(active_map[code]) for code in base_needed})
                ) if base_needed else {}
                before: dict[str, int | None] = {}
                targets: dict[str, int] = {}
                for code in codes:
                    seq = sequences.get(code, [])
                    if code in resume_targets:
                        value = int(resume_targets[code])
                        before[code] = None
                        tail = seq
                    else:
                        last_set = -1
                        for pos, entry in enumerate(seq):
                            if entry["mode"] == "SET":
                                last_set = pos
                        if last_set >= 0:
                            value = int(seq[last_set]["quantity"])
                            before[code] = None
                            tail = seq[last_set + 1:]
                        else:
                            base = int(base_by_chrt.get(int(active_map[code]), 0))
                            value = base
                            before[code] = base
                            tail = seq
                    for entry in tail:
                        if entry["mode"] == "SET":
                            value = int(entry["quantity"])
                        else:
                            value += int(entry["quantity"])
                    targets[code] = max(0, int(value))
                return before, targets

            before_by_code, target_by_code = build_targets(chrt_map, ready_codes) if ready_codes else ({}, {})

            # FC_G5A8K4_WB_HARD_SELLABLE_CAP: callers may send physical totals or resume an
            # older checkpoint. Never advertise more than the current canonical sellable
            # amount. This is intentionally a one-way cap: a stale request may under-publish,
            # but can never put an already reserved unit back on sale.
            safe_caps: dict[str, int] = {}
            for code in ready_codes:
                sku = db.execute(select(SKU).where(
                    SKU.client_id == payload.client_id, SKU.barcode == code,
                )).scalars().first()
                if not sku:
                    mark_error(code, "Локальный SKU для ШК не найден; публикация выше 0 запрещена")
                    continue
                cap = max(0, int(effective_fbs_free_for_sku(
                    db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku_id=sku.id,
                    wb_warehouse_id=selected_wb_id,
                )))
                if wb_sales_paused(db, client_id=payload.client_id, warehouse_id=payload.warehouse_id, sku_id=sku.id):
                    cap = 0
                safe_caps[code] = cap
                target_by_code[code] = min(max(0, int(target_by_code.get(code, 0))), cap)
            ready_codes = [code for code in ready_codes if code in safe_caps]

            # Protect against two seller barcodes resolving to the same WB size but asking
            # for different amounts. Publishing either would make the other silently wrong.
            chrt_targets: dict[int, int] = {}
            chrt_codes: dict[int, list[str]] = {}
            conflicted: set[str] = set()
            for code in ready_codes:
                cid = int(chrt_map[code])
                chrt_codes.setdefault(cid, []).append(code)
                qty = int(target_by_code[code])
                if cid in chrt_targets and chrt_targets[cid] != qty:
                    conflicted.update(chrt_codes[cid])
                else:
                    chrt_targets[cid] = qty
            for code in sorted(conflicted):
                mark_error(code, "Несколько ШК одного chrtId требуют разные остатки; публикация остановлена для защиты данных", resumable_target=resume_targets.get(code))
            publish_codes = [code for code in ready_codes if code not in conflicted]
            chrt_targets = {int(chrt_map[code]): int(target_by_code[code]) for code in publish_codes}

            # Durable checkpoint BEFORE the external write. A retry can now resume toward
            # this absolute target even if the process dies after WB accepted the PUT.
            in_progress_rows = []
            for code in publish_codes:
                for entry in active_entries_by_code[code]:
                    row = event_row(entry, status="IN_PROGRESS", resolved_amount=int(target_by_code[code]), error=None)
                    if row:
                        in_progress_rows.append(row)
            _upsert_wb_publication_events(db, in_progress_rows, commit=True)

            if chrt_targets:
                try:
                    _wb_write_and_verify_chrt_stocks(client, int(selected_wb_id), chrt_targets)
                except Exception as first_exc:
                    # One recovery scan for the complete failed batch. If chrtId changed,
                    # recompute ADD bases, persist the new checkpoint, then retry safely.
                    live_map = _wb_fresh_chrt_map(client, set(publish_codes))
                    if not live_map or any(code not in live_map for code in publish_codes):
                        raise first_exc
                    refreshed_before, refreshed_targets = build_targets(live_map, publish_codes)
                    for code in publish_codes:
                        refreshed_targets[code] = min(
                            max(0, int(refreshed_targets.get(code, 0))),
                            max(0, int(safe_caps.get(code, 0))),
                        )
                    refreshed_chrt: dict[int, int] = {}
                    for code in publish_codes:
                        cid = int(live_map[code])
                        qty = int(refreshed_targets[code])
                        if cid in refreshed_chrt and refreshed_chrt[cid] != qty:
                            raise RuntimeError("После обновления карточек WB несколько ШК одного chrtId требуют разные остатки")
                        refreshed_chrt[cid] = qty
                    refreshed_checkpoint = []
                    for code in publish_codes:
                        for entry in active_entries_by_code[code]:
                            row = event_row(entry, status="IN_PROGRESS", resolved_amount=int(refreshed_targets[code]), error=None)
                            if row:
                                refreshed_checkpoint.append(row)
                    _upsert_wb_publication_events(db, refreshed_checkpoint, commit=True)
                    _wb_write_and_verify_chrt_stocks(client, int(selected_wb_id), refreshed_chrt)
                    before_by_code.update(refreshed_before)
                    target_by_code.update(refreshed_targets)

            success_events = []
            for code in publish_codes:
                before = before_by_code.get(code)
                after = int(target_by_code[code])
                for entry in active_entries_by_code[code]:
                    results[entry["index"]] = {
                        "barcode": code, "mode": entry["mode"], "before": before, "after": after,
                        "ok": True, "batched": True, "idempotency_key": entry.get("key"),
                    }
                    row = event_row(entry, status="PUBLISHED", resolved_amount=after, error=None)
                    if row:
                        success_events.append(row)
                publication_rows.append({
                    "client_id": str(payload.client_id), "warehouse_id": str(payload.warehouse_id),
                    "barcode": code, "wb_warehouse_id": int(selected_wb_id), "amount": after,
                    "status": "PUBLISHED", "error": None,
                })
            _upsert_wb_publication_events(db, success_events, commit=False)
    except Exception as exc:
        message = str(getattr(exc, "detail", exc))[:300]
        for code in list(active_entries_by_code):
            if all(results[entry["index"]] is not None for entry in active_entries_by_code[code]):
                continue
            # If a target was checkpointed, preserve IN_PROGRESS so retry resumes that
            # absolute amount. Otherwise the operation never reached the external-write phase.
            mark_error(code, message, resumable_target=target_by_code.get(code, resume_targets.get(code)))
    finally:
        if client is not None:
            client.close()

    # Persist terminal/current event states and the user-facing per-barcode publication state.
    if event_updates:
        _upsert_wb_publication_events(db, event_updates, commit=False)
    publication_by_barcode = {str(row["barcode"]): row for row in publication_rows if row.get("barcode")}
    _record_fbs_publications_batch(db, list(publication_by_barcode.values()))

    for index, item in enumerate(results):
        if item is None:
            line = payload.lines[index]
            results[index] = {
                "barcode": normalize_product_barcode(str(line.barcode or ""))[0],
                "mode": line.mode, "ok": False, "error": "Неизвестная ошибка публикации WB",
                "idempotency_key": line.idempotency_key,
            }
    synced = sum(1 for item in results if item and item.get("ok"))
    failed = len(results) - synced
    return {"ok": failed == 0, "synced": synced, "failed": failed, "results": results, "batched": True}




# FC_K9R22_RECEIVING_CANCEL_HELPERS_RESTORE
# Restored from the last known-working receiving cancellation flow (0.7.9.52).
# These helpers are intentionally kept beside the cancellation endpoints so a
# cumulative router replacement cannot leave the UI pointing at undefined names.
def _receiving_operation_allowed(operation: InventoryOperation) -> bool:
    kind = str(operation.operation_type or "").upper()
    comment = str(operation.comment or "").lower()
    return kind in {"RECEIVING", "RECEIVING_IMPORT"} or (kind == "ADJUSTMENT" and "приём" in comment)


def _reverse_receiving_operation(
    db: Session,
    operation: InventoryOperation,
    *,
    commit: bool = True,
) -> dict:
    if str(operation.status or "").upper() == "CANCELLED":
        return {"already_cancelled": True, "operation_id": str(operation.id), "sku_ids": []}
    if not _receiving_operation_allowed(operation):
        raise HTTPException(status_code=409, detail="Отменять этой кнопкой можно только приёмку или импорт приёмки")

    original_lines = db.execute(select(InventoryOperationLine).where(
        InventoryOperationLine.operation_id == operation.id,
    )).scalars().all()
    if not original_lines:
        raise HTTPException(status_code=409, detail="В приёмке нет строк для отмены")

    reverse_lines: list[StockLine] = []
    available_by_balance: dict[uuid.UUID, Decimal] = {}
    balances_by_sku: dict[uuid.UUID, list[InventoryBalance]] = {}
    affected_skus: set[uuid.UUID] = set()

    for line in original_lines:
        affected_skus.add(line.sku_id)
        quantity = Decimal(line.quantity or 0)
        if quantity <= 0:
            continue

        # A receiving line added stock. Remove the same quantity from current stock,
        # preferring its original bucket but allowing stock moved to another box/cell.
        # This keeps cancellation useful after internal placement while still blocking
        # it when the warehouse no longer contains enough units (e.g. already shipped).
        if line.destination_location_id is not None and line.source_location_id is None:
            if line.sku_id not in balances_by_sku:
                rows = db.execute(select(InventoryBalance).where(
                    InventoryBalance.client_id == operation.client_id,
                    InventoryBalance.warehouse_id == operation.warehouse_id,
                    InventoryBalance.sku_id == line.sku_id,
                    InventoryBalance.quantity > 0,
                ).with_for_update()).scalars().all()
                rows.sort(key=lambda row: (
                    0 if row.location_id == line.destination_location_id and row.container_id == line.destination_container_id else 1,
                    str(row.updated_at or ""),
                ))
                balances_by_sku[line.sku_id] = rows
                for row in rows:
                    available_by_balance.setdefault(row.id, Decimal(row.quantity or 0))

            need = quantity
            for balance in balances_by_sku[line.sku_id]:
                available = available_by_balance.get(balance.id, Decimal("0"))
                take = min(available, need)
                if take <= 0:
                    continue
                reverse_lines.append(StockLine(
                    sku_id=line.sku_id,
                    quantity=take,
                    source_location_id=balance.location_id,
                    source_container_id=balance.container_id,
                    reason=f"CANCEL_RECEIVING:{operation.id}",
                ))
                available_by_balance[balance.id] = available - take
                need -= take
                if need <= 0:
                    break
            if need > 0:
                available_total = quantity - need
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Приёмку нельзя отменить: по товару доступно только {available_total}, "
                        f"а нужно списать {quantity}. Возможно, товар уже отгружен."
                    ),
                )
            continue

        # A SET/receiving adjustment may have reduced stock. Cancellation restores it
        # to the former address; archived/missing boxes are not revived automatically.
        if line.source_location_id is not None and line.destination_location_id is None:
            destination_container_id = line.source_container_id
            if destination_container_id:
                container = db.get(Container, destination_container_id)
                if not container or str(container.status or "").upper() != "ACTIVE":
                    destination_container_id = None
            reverse_lines.append(StockLine(
                sku_id=line.sku_id,
                quantity=quantity,
                destination_location_id=line.source_location_id,
                destination_container_id=destination_container_id,
                reason=f"CANCEL_RECEIVING:{operation.id}",
            ))
            continue

        raise HTTPException(status_code=409, detail="Эту смешанную складскую операцию нельзя отменить как приёмку")

    if not reverse_lines:
        raise HTTPException(status_code=409, detail="В приёмке нет количества для отмены")

    reversal = execute_operation(db, OperationCreate(
        client_id=operation.client_id,
        warehouse_id=operation.warehouse_id,
        operation_type="ADJUSTMENT",
        idempotency_key=f"cancel-receiving-{operation.id}",
        performed_by="FulfillCore",
        reference=f"Отмена приёмки {operation.id}",
        comment=f"Обратная корректировка; исходная операция {operation.id} сохранена в истории",
        lines=reverse_lines,
    ), commit=False)
    archive_consumed_empty_containers(
        db,operation.client_id,operation.warehouse_id,
        [line.source_container_id for line in reverse_lines if line.source_container_id],
        actor="FulfillCore",reason="CANCEL_RECEIVING_EMPTY_BOX",
    )
    operation.status = "CANCELLED"
    if commit:
        db.commit()
        db.refresh(reversal)
    else:
        db.flush()
    return {
        "already_cancelled": False,
        "operation_id": str(operation.id),
        "reversal_operation_id": str(reversal.id),
        "sku_ids": list(affected_skus),
    }


def _publish_skus_after_cancellation(
    db: Session,
    *,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    sku_ids: set[uuid.UUID],
    wb_warehouse_id: int | None,
) -> list[dict]:
    results = []
    for sku_id in sorted(sku_ids, key=str):
        sku = db.get(SKU, sku_id)
        barcode = str(getattr(sku, "barcode", "") or "").strip()
        if not barcode:
            results.append({"sku_id": str(sku_id), "ok": False, "error": "У товара нет ШК для WB"})
            continue
        amount = max(0, int(_warehouse_qty(db, client_id, warehouse_id, sku_id)))
        try:
            selected_wb_id = _sync_wb_stock(
                db,
                client_id=client_id,
                physical_warehouse_id=warehouse_id,
                wb_warehouse_id=wb_warehouse_id,
                barcode=barcode,
                amount=amount,
            )
            _record_fbs_publication(
                db,
                client_id=client_id,
                warehouse_id=warehouse_id,
                barcode=barcode,
                wb_warehouse_id=selected_wb_id,
                amount=amount,
                status="PUBLISHED",
            )
            results.append({"barcode": barcode, "amount": amount, "ok": True})
        except Exception as exc:
            message = str(getattr(exc, "detail", exc))[:300]
            _record_fbs_publication(
                db,
                client_id=client_id,
                warehouse_id=warehouse_id,
                barcode=barcode,
                wb_warehouse_id=wb_warehouse_id,
                amount=None,
                status="ERROR",
                error=message,
            )
            results.append({"barcode": barcode, "amount": amount, "ok": False, "error": message})
    return results


def _local_stock_rows(
    db: Session,
    *,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    sku_ids: set[uuid.UUID],
) -> list[dict]:
    rows = []
    for sku_id in sorted(sku_ids, key=str):
        sku = db.get(SKU, sku_id)
        barcode = str(getattr(sku, "barcode", "") or "").strip()
        if barcode:
            rows.append({
                "barcode": barcode,
                "amount": max(0, int(_warehouse_qty(db, client_id, warehouse_id, sku_id))),
            })
    return rows


@router.post("/operations/{operation_id}/cancel")
def cancel_receiving_operation(
    operation_id: uuid.UUID,
    payload: CancelReceivingRequest,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    operation = db.execute(select(InventoryOperation).where(
        InventoryOperation.id == operation_id,
        InventoryOperation.client_id == client_id,
        InventoryOperation.warehouse_id == warehouse_id,
    )).scalars().first()
    if not operation:
        raise HTTPException(status_code=404, detail="Приёмка не найдена на выбранном складе")
    result = _reverse_receiving_operation(db, operation)
    sku_ids = set(result.pop("sku_ids", []))
    affected_stocks = _local_stock_rows(
        db,
        client_id=operation.client_id,
        warehouse_id=operation.warehouse_id,
        sku_ids=sku_ids,
    )
    wb_results = []
    if payload.sync_wb and sku_ids:
        wb_results = _publish_skus_after_cancellation(
            db,
            client_id=operation.client_id,
            warehouse_id=operation.warehouse_id,
            sku_ids=sku_ids,
            wb_warehouse_id=payload.wb_warehouse_id,
        )
    return {
        "ok": True,
        **result,
        "wb_requested": payload.sync_wb,
        "affected_stocks": affected_stocks,
        "wb_results": wb_results,
        "wb_failed": sum(1 for row in wb_results if not row.get("ok")),
    }


# ---------------- Палетная приёмка: палета -> короб -> товар ----------------
class PalletReceiptCreateRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    name: str | None = None
    reference: str | None = None


class PalletAddRequest(BaseModel):
    pallet_code: str


class PalletBoxAddRequest(BaseModel):
    pallet_code: str
    box_code: str
    force: bool = False


class PalletCloseRequest(BaseModel):
    pallet_code: str


def _ensure_pallet_receiving_tables(db: Session) -> None:
    """Schema is installed on application startup, not on every warehouse request."""
    ensure_runtime_schema(db)


def _receipt_box_container(
    db: Session,
    *,
    receipt: dict,
    box_code: str,
) -> tuple[Container, bool]:
    """Create the real empty warehouse box at scan time.

    The receipt table remains the document/audit structure. ``Container`` is the
    operational box shown in storage, picking lists and movement screens before any
    product is loaded into it.
    """
    client_id = uuid.UUID(str(receipt["client_id"]))
    warehouse_id = uuid.UUID(str(receipt["warehouse_id"]))
    staging = _ensure_location(db, warehouse_id, "ПРИЕМКА-ПАЛЕТЫ")
    existing = db.execute(select(Container).where(
        Container.client_id == client_id,
        Container.warehouse_id == warehouse_id,
        or_(Container.barcode == box_code, getattr(Container, "name", Container.barcode) == box_code),
    ).with_for_update()).scalars().first()
    if existing:
        quantity = db.execute(select(func.coalesce(func.sum(InventoryBalance.quantity), 0)).where(
            InventoryBalance.client_id == client_id,
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.container_id == existing.id,
        )).scalar_one()
        if Decimal(quantity or 0) > 0:
            raise HTTPException(
                status_code=409,
                detail=f"Короб {box_code} уже содержит товар. Повторно принимать его как пустой нельзя.",
            )
        if str(existing.status or "").upper() != "ACTIVE":
            existing.status = "ACTIVE"
            existing.location_id = staging.id
        elif existing.location_id is None:
            existing.location_id = staging.id
        return existing, False

    container = _ensure_container(db, client_id, warehouse_id, staging.id, box_code)
    if container is None:
        raise HTTPException(status_code=500, detail=f"Не удалось создать короб {box_code}")
    return container, True


def _archive_receipt_box_container(db: Session, container_id: str | None, created: bool) -> None:
    """Archive only an empty container created by this receipt."""
    if not container_id or not created:
        return
    try:
        parsed_id = uuid.UUID(str(container_id))
    except (TypeError, ValueError):
        return
    container = db.get(Container, parsed_id)
    if not container:
        return
    quantity = db.execute(select(func.coalesce(func.sum(InventoryBalance.quantity), 0)).where(
        InventoryBalance.container_id == container.id,
    )).scalar_one()
    if Decimal(quantity or 0) > 0:
        raise HTTPException(status_code=409, detail=f"Короб {container.name or container.barcode} уже содержит товар")
    location = db.get(Location, container.location_id) if container.location_id else None
    if location and str(location.code or "").strip().upper() != "ПРИЕМКА-ПАЛЕТЫ":
        # The operator has already deliberately placed this empty box in storage.
        # Removing the receipt row must not make that warehouse box disappear.
        return
    container.status = "ARCHIVED"


def _get_pallet_receipt(db: Session, receipt_id: str) -> dict:
    _ensure_pallet_receiving_tables(db)
    row = db.execute(text("""
        SELECT id, client_id, warehouse_id, name, reference, status, created_at
        FROM fc_pallet_receipts WHERE id=:id
    """), {"id": str(receipt_id)}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Палетная приёмка не найдена")
    return dict(row)


@router.post("/pallet-receipts")
def create_pallet_receipt(payload: PalletReceiptCreateRequest, db: Session = Depends(get_db)):
    _ensure_pallet_receiving_tables(db)
    rid = str(uuid.uuid4())
    name = str(payload.name or "").strip() or f"Приёмка {rid[:8]}"
    db.execute(text("""
        INSERT INTO fc_pallet_receipts(id, client_id, warehouse_id, name, reference, status)
        VALUES (:id,:client_id,:warehouse_id,:name,:reference,'OPEN')
    """), {"id": rid, "client_id": str(payload.client_id), "warehouse_id": str(payload.warehouse_id), "name": name, "reference": str(payload.reference or "").strip() or None})
    db.commit()
    return {"ok": True, "id": rid, "name": name, "status": "OPEN"}


@router.get("/pallet-receipts")
def list_pallet_receipts(client_id: uuid.UUID, warehouse_id: uuid.UUID, db: Session = Depends(get_db)):
    _ensure_pallet_receiving_tables(db)
    rows = db.execute(text("""
        SELECT r.id, r.name, r.reference, r.status, r.created_at,
               COUNT(DISTINCT p.pallet_code) AS pallet_count,
               COUNT(DISTINCT b.box_code) AS box_count
        FROM fc_pallet_receipts r
        LEFT JOIN fc_pallet_receipt_pallets p ON p.receipt_id=r.id
        LEFT JOIN fc_pallet_receipt_boxes b ON b.receipt_id=r.id
        WHERE r.client_id=:client_id AND r.warehouse_id=:warehouse_id
        GROUP BY r.id, r.name, r.reference, r.status, r.created_at
        ORDER BY r.created_at DESC
        LIMIT 100
    """), {"client_id": str(client_id), "warehouse_id": str(warehouse_id)}).mappings().all()
    return {"items": [dict(x) for x in rows]}




@router.delete("/pallet-receipts/{receipt_id}")
def delete_pallet_receipt(
    receipt_id: str,
    force: bool = False,
    reverse_stock: bool = False,
    sync_wb: bool = False,
    wb_warehouse_id: int | None = None,
    db: Session = Depends(get_db),
):
    r = _get_pallet_receipt(db, receipt_id)
    if r["status"] == "CANCELLED":
        return {"ok": True, "id": receipt_id, "already_cancelled": True, "history_preserved": True}
    if r["status"] == "LOADED" and not reverse_stock:
        raise HTTPException(status_code=409, detail={
            "code": "RECEIPT_HAS_STOCK",
            "message": (
                "В эту приёмку уже загружен товар. Для отмены нужно создать обратную "
                "корректировку остатков; исходная приёмка останется в истории со статусом «Отменена»."
            ),
        })

    affected_skus: set[uuid.UUID] = set()
    reversed_operations = []
    if r["status"] == "LOADED":
        client_id = uuid.UUID(str(r["client_id"]))
        warehouse_id = uuid.UUID(str(r["warehouse_id"]))
        box_codes = [str(value) for value in db.execute(text(
            "SELECT box_code FROM fc_pallet_receipt_boxes WHERE receipt_id=:receipt_id"
        ), {"receipt_id": receipt_id}).scalars().all()]
        container_ids = []
        if box_codes:
            container_ids = list(db.execute(select(Container.id).where(
                Container.client_id == client_id,
                Container.warehouse_id == warehouse_id,
                Container.barcode.in_(box_codes),
            )).scalars().all())

        exact_reason = f"PALLET_LAYOUT:{receipt_id}"
        legacy_clause = and_(
            InventoryOperationLine.reason == "PALLET_LAYOUT",
            InventoryOperation.created_at >= r["created_at"],
        )
        if container_ids:
            legacy_clause = and_(legacy_clause, InventoryOperationLine.destination_container_id.in_(container_ids))
        reference_values = [str(value).strip() for value in (r.get("reference"), r.get("name")) if str(value or "").strip()]
        if reference_values:
            legacy_clause = and_(legacy_clause, InventoryOperation.reference.in_(reference_values))

        operations = db.execute(
            select(InventoryOperation)
            .join(InventoryOperationLine, InventoryOperationLine.operation_id == InventoryOperation.id)
            .where(
                InventoryOperation.client_id == client_id,
                InventoryOperation.warehouse_id == warehouse_id,
                InventoryOperation.operation_type == "RECEIVING_IMPORT",
                InventoryOperation.status == "COMPLETED",
                or_(InventoryOperationLine.reason == exact_reason, legacy_clause),
            )
            .order_by(InventoryOperation.created_at.desc())
        ).scalars().unique().all()
        if not operations:
            raise HTTPException(
                status_code=409,
                detail="Не удалось однозначно найти строки этой приёмки. Отмените её строки в разделе «Операции».",
            )
        try:
            for operation in operations:
                reversed_result = _reverse_receiving_operation(db, operation, commit=False)
                affected_skus.update(reversed_result.pop("sku_ids", []))
                reversed_operations.append(reversed_result)
        except Exception:
            # A pallet receipt is one business document: either every stock line is
            # reversed, or none is. Never leave a half-cancelled receipt.
            db.rollback()
            raise

    receipt_box_rows = db.execute(text("""
        SELECT container_id, container_created
        FROM fc_pallet_receipt_boxes
        WHERE receipt_id=:receipt_id
    """), {"receipt_id": receipt_id}).mappings().all()
    for box_row in receipt_box_rows:
        _archive_receipt_box_container(db, box_row.get("container_id"), bool(box_row.get("container_created")))

    db.execute(text("UPDATE fc_pallet_receipts SET status='CANCELLED' WHERE id=:r"), {"r": receipt_id})
    db.commit()
    wb_results = []
    if sync_wb and affected_skus:
        wb_results = _publish_skus_after_cancellation(
            db,
            client_id=uuid.UUID(str(r["client_id"])),
            warehouse_id=uuid.UUID(str(r["warehouse_id"])),
            sku_ids=affected_skus,
            wb_warehouse_id=wb_warehouse_id,
        )
    return {
        "ok": True,
        "id": receipt_id,
        "status": "CANCELLED",
        "history_preserved": True,
        "reversed_operations": reversed_operations,
        "wb_requested": sync_wb,
        "affected_stocks": _local_stock_rows(
            db,
            client_id=uuid.UUID(str(r["client_id"])),
            warehouse_id=uuid.UUID(str(r["warehouse_id"])),
            sku_ids=affected_skus,
        ),
        "wb_results": wb_results,
        "wb_failed": sum(1 for row in wb_results if not row.get("ok")),
    }


@router.delete("/pallet-receipts/{receipt_id}/pallets/{pallet_code}")
def delete_receipt_pallet(receipt_id: str, pallet_code: str, db: Session = Depends(get_db)):
    r = _get_pallet_receipt(db, receipt_id)
    if r["status"] != "OPEN":
        raise HTTPException(status_code=409, detail="Удалять палету можно только до загрузки товарной раскладки")
    pallet = db.execute(text("SELECT 1 FROM fc_pallet_receipt_pallets WHERE receipt_id=:r AND pallet_code=:p"), {"r": receipt_id, "p": pallet_code}).first()
    if not pallet:
        raise HTTPException(status_code=404, detail="Палета не найдена")
    box_rows = db.execute(text("""
        SELECT container_id, container_created
        FROM fc_pallet_receipt_boxes
        WHERE receipt_id=:r AND pallet_code=:p
    """), {"r": receipt_id, "p": pallet_code}).mappings().all()
    for box_row in box_rows:
        _archive_receipt_box_container(db, box_row.get("container_id"), bool(box_row.get("container_created")))
    db.execute(text("DELETE FROM fc_pallet_receipt_boxes WHERE receipt_id=:r AND pallet_code=:p"), {"r": receipt_id, "p": pallet_code})
    db.execute(text("DELETE FROM fc_pallet_receipt_pallets WHERE receipt_id=:r AND pallet_code=:p"), {"r": receipt_id, "p": pallet_code})
    db.commit()
    return {"ok": True, "pallet_code": pallet_code}


@router.post("/pallet-receipts/{receipt_id}/pallets")
def add_receipt_pallet(receipt_id: str, payload: PalletAddRequest, db: Session = Depends(get_db)):
    r = _get_pallet_receipt(db, receipt_id)
    if r["status"] not in ("OPEN", "LOADED"):
        raise HTTPException(status_code=409, detail="Приёмка закрыта")
    code = str(payload.pallet_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Пустой код палеты")
    if str(db.get_bind().dialect.name).lower() == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"pallet:{receipt_id}:{code}"})
    db.execute(text("""
        INSERT INTO fc_pallet_receipt_pallets(id,receipt_id,pallet_code)
        VALUES (:id,:r,:p)
        ON CONFLICT (receipt_id, pallet_code) DO NOTHING
    """), {"id": str(uuid.uuid4()), "r": receipt_id, "p": code})
    db.commit()
    return {"ok": True, "pallet_code": code}


@router.post("/pallet-receipts/{receipt_id}/boxes")
def add_receipt_box(receipt_id: str, payload: PalletBoxAddRequest, db: Session = Depends(get_db)):
    r = _get_pallet_receipt(db, receipt_id)
    if r["status"] not in ("OPEN", "LOADED"):
        raise HTTPException(status_code=409, detail="Приёмка закрыта")
    pallet = str(payload.pallet_code or "").strip()
    box = str(payload.box_code or "").strip()
    if not pallet or not box:
        raise HTTPException(status_code=400, detail="Нужны код палеты и код короба")
    # Serialize the same physical box across all receiving terminals. The lock lives
    # only until this transaction commits, so two scanners cannot both pass the
    # SELECT-before-INSERT window for the same box.
    if str(db.get_bind().dialect.name).lower() == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {
            "key": f"pallet-box:{r['client_id']}:{r['warehouse_id']}:{box}"
        })
    p = db.execute(text("SELECT 1 FROM fc_pallet_receipt_pallets WHERE receipt_id=:r AND pallet_code=:p"), {"r": receipt_id, "p": pallet}).first()
    if not p:
        raise HTTPException(status_code=409, detail=f"Палета {pallet} сначала должна быть принята")
    closed = db.execute(text("SELECT closed_at FROM fc_pallet_receipt_pallets WHERE receipt_id=:r AND pallet_code=:p"), {"r": receipt_id, "p": pallet}).scalar_one_or_none()
    if closed:
        raise HTTPException(status_code=409, detail=f"Палета {pallet} уже закрыта. Начните следующую палету.")
    other_receipt = db.execute(text("""
        SELECT r.id, r.name, r.status, b.pallet_code
        FROM fc_pallet_receipt_boxes b
        JOIN fc_pallet_receipts r ON r.id=b.receipt_id
        WHERE b.box_code=:box_code
          AND b.receipt_id<>:receipt_id
          AND r.client_id=:client_id
          AND r.warehouse_id=:warehouse_id
          AND r.status IN ('OPEN','LOADED')
        ORDER BY r.created_at DESC
        LIMIT 1
    """), {
        "box_code": box,
        "receipt_id": receipt_id,
        "client_id": str(r["client_id"]),
        "warehouse_id": str(r["warehouse_id"]),
    }).mappings().first()
    if other_receipt:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Короб {box} уже относится к приёмке «{other_receipt.get('name') or other_receipt.get('id')}» "
                f"на палете {other_receipt.get('pallet_code')}. Сначала завершите или отмените ту приёмку."
            ),
        )
    existing = db.execute(text("""
        SELECT pallet_code, container_id, container_created
        FROM fc_pallet_receipt_boxes
        WHERE receipt_id=:r AND box_code=:b
    """), {"r": receipt_id, "b": box}).mappings().first()
    if existing and not payload.force:
        where = f"на палете {existing['pallet_code']}"
        raise HTTPException(status_code=409, detail={"code":"DUPLICATE_BOX","message":f"Короб {box} уже принят {where}. Вы уверены, что хотите принять этот короб?","box_code":box,"existing_pallet":existing["pallet_code"],"target_pallet":pallet})
    if existing:
        # Force means confirmation, not a second physical identity: move/re-affirm the same box.
        db.execute(text("UPDATE fc_pallet_receipt_boxes SET pallet_code=:p WHERE receipt_id=:r AND box_code=:b"), {"r":receipt_id,"p":pallet,"b":box})
        container_id = existing.get("container_id")
        container_created = bool(existing.get("container_created"))
        if not container_id:
            container, container_created = _receipt_box_container(db, receipt=r, box_code=box)
            container_id = str(container.id)
            db.execute(text("""
                UPDATE fc_pallet_receipt_boxes
                SET container_id=:container_id, container_created=:container_created
                WHERE receipt_id=:receipt_id AND box_code=:box_code
            """), {
                "container_id": container_id,
                "container_created": container_created,
                "receipt_id": receipt_id,
                "box_code": box,
            })
    else:
        container, container_created = _receipt_box_container(db, receipt=r, box_code=box)
        container_id = str(container.id)
        db.execute(text("""
            INSERT INTO fc_pallet_receipt_boxes(
                id,receipt_id,pallet_code,box_code,container_id,container_created
            ) VALUES (:id,:r,:p,:b,:container_id,:container_created)
        """), {
            "id": str(uuid.uuid4()), "r": receipt_id, "p": pallet, "b": box,
            "container_id": container_id, "container_created": container_created,
        })
    count = int(db.execute(text("SELECT COUNT(*) FROM fc_pallet_receipt_boxes WHERE receipt_id=:r AND pallet_code=:p"), {"r":receipt_id,"p":pallet}).scalar_one() or 0)
    auto_closed = False
    if count >= 16:
        db.execute(text("UPDATE fc_pallet_receipt_pallets SET closed_at=COALESCE(closed_at,NOW()) WHERE receipt_id=:r AND pallet_code=:p"), {"r":receipt_id,"p":pallet})
        auto_closed = True
    db.commit()
    return {
        "ok": True,
        "pallet_code": pallet,
        "box_code": box,
        "container_id": container_id,
        "container_created": container_created,
        "box_count": count,
        "pallet_closed": auto_closed,
    }


@router.delete("/pallet-receipts/{receipt_id}/boxes/{box_code}")
def delete_receipt_box(receipt_id: str, box_code: str, db: Session = Depends(get_db)):
    r = _get_pallet_receipt(db, receipt_id)
    if r["status"] != "OPEN":
        raise HTTPException(status_code=409, detail="Удалять отсканированные короба можно до загрузки товарной раскладки")
    row = db.execute(text("""
        SELECT pallet_code, container_id, container_created
        FROM fc_pallet_receipt_boxes
        WHERE receipt_id=:r AND box_code=:b
    """), {"r":receipt_id,"b":box_code}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Короб в этой приёмке не найден")
    _archive_receipt_box_container(db, row.get("container_id"), bool(row.get("container_created")))
    db.execute(text("DELETE FROM fc_pallet_receipt_boxes WHERE receipt_id=:r AND box_code=:b"), {"r":receipt_id,"b":box_code})
    db.commit()
    return {"ok":True,"box_code":box_code,"pallet_code":row.get("pallet_code")}


@router.post("/pallet-receipts/{receipt_id}/pallets/close")
def close_receipt_pallet(receipt_id: str, payload: PalletCloseRequest, db: Session = Depends(get_db)):
    r = _get_pallet_receipt(db, receipt_id)
    if r["status"] != "OPEN":
        raise HTTPException(status_code=409, detail="Приёмка уже закрыта для сканирования")
    pallet = str(payload.pallet_code or "").strip()
    exists = db.execute(text("SELECT 1 FROM fc_pallet_receipt_pallets WHERE receipt_id=:r AND pallet_code=:p"), {"r":receipt_id,"p":pallet}).first()
    if not exists:
        raise HTTPException(status_code=404, detail="Палета не найдена")
    db.execute(text("UPDATE fc_pallet_receipt_pallets SET closed_at=COALESCE(closed_at,NOW()) WHERE receipt_id=:r AND pallet_code=:p"), {"r":receipt_id,"p":pallet})
    db.commit()
    return {"ok":True,"pallet_code":pallet}


@router.get("/pallet-receipts/{receipt_id}")
def pallet_receipt_detail(receipt_id: str, db: Session = Depends(get_db)):
    r = _get_pallet_receipt(db, receipt_id)
    pallets = [dict(x) for x in db.execute(text("""
        SELECT p.pallet_code, p.created_at, p.closed_at, COUNT(b.box_code) AS box_count
        FROM fc_pallet_receipt_pallets p
        LEFT JOIN fc_pallet_receipt_boxes b ON b.receipt_id=p.receipt_id AND b.pallet_code=p.pallet_code
        WHERE p.receipt_id=:r GROUP BY p.pallet_code,p.created_at,p.closed_at ORDER BY p.created_at,p.pallet_code
    """), {"r": receipt_id}).mappings().all()]
    boxes = [dict(x) for x in db.execute(text("""
        SELECT pallet_code, box_code, container_id, container_created, created_at FROM fc_pallet_receipt_boxes
        WHERE receipt_id=:r ORDER BY pallet_code,created_at,box_code
    """), {"r": receipt_id}).mappings().all()]
    # Количество товара в коробе считаем из обычных складских остатков FulfillCore.
    client_uuid = uuid.UUID(r["client_id"])
    warehouse_uuid = uuid.UUID(r["warehouse_id"])
    qty_by_box = {}
    container_ids = []
    for item in boxes:
        try:
            if item.get("container_id"):
                container_ids.append(uuid.UUID(str(item["container_id"])))
        except (TypeError, ValueError):
            pass
    legacy_codes = [str(item["box_code"]) for item in boxes if not item.get("container_id")]
    if legacy_codes:
        legacy_containers = db.execute(select(Container).where(
            Container.client_id == client_uuid,
            Container.warehouse_id == warehouse_uuid,
            Container.barcode.in_(legacy_codes),
        )).scalars().all()
        legacy_by_code = {str(container.barcode): container for container in legacy_containers}
        for item in boxes:
            if item.get("container_id"):
                continue
            container = legacy_by_code.get(str(item["box_code"]))
            if container:
                item["container_id"] = str(container.id)
                container_ids.append(container.id)
    if container_ids:
        containers = db.execute(select(Container).where(
            Container.client_id == client_uuid,
            Container.warehouse_id == warehouse_uuid,
            Container.id.in_(container_ids),
        )).scalars().all()
        if containers:
            vals = db.execute(select(InventoryBalance.container_id, func.coalesce(func.sum(InventoryBalance.quantity), 0)).where(
                InventoryBalance.client_id == client_uuid,
                InventoryBalance.warehouse_id == warehouse_uuid,
                InventoryBalance.container_id.in_([c.id for c in containers]),
            ).group_by(InventoryBalance.container_id)).all()
            for cid, qty in vals:
                qty_by_box[str(cid)] = float(qty or 0)
    for b in boxes:
        b["quantity"] = qty_by_box.get(str(b.get("container_id") or ""), 0)
    return {"receipt": r, "pallets": pallets, "boxes": boxes}




@router.get("/pallet-receipts/{receipt_id}/export.xlsx")
def export_pallet_receipt(receipt_id: str, db: Session = Depends(get_db)):
    r = _get_pallet_receipt(db, receipt_id)
    pallets = [dict(x) for x in db.execute(text("""
        SELECT pallet_code, created_at, closed_at
        FROM fc_pallet_receipt_pallets WHERE receipt_id=:r ORDER BY created_at,pallet_code
    """), {"r": receipt_id}).mappings().all()]
    boxes = [dict(x) for x in db.execute(text("""
        SELECT pallet_code, box_code, container_id, created_at
        FROM fc_pallet_receipt_boxes WHERE receipt_id=:r ORDER BY created_at,box_code
    """), {"r": receipt_id}).mappings().all()]
    # Quantity by physical box from current local stock.
    client_uuid = uuid.UUID(r["client_id"]); warehouse_uuid = uuid.UUID(r["warehouse_id"])
    qty_by_box = {}
    container_ids=[]
    for item in boxes:
        try:
            if item.get("container_id"): container_ids.append(uuid.UUID(str(item["container_id"])))
        except (TypeError,ValueError):
            pass
    legacy_codes=[str(item["box_code"]) for item in boxes if not item.get("container_id")]
    if legacy_codes:
        legacy=db.execute(select(Container).where(Container.client_id==client_uuid,Container.warehouse_id==warehouse_uuid,Container.barcode.in_(legacy_codes))).scalars().all()
        legacy_by_code={str(c.barcode):c for c in legacy}
        for item in boxes:
            if item.get("container_id"): continue
            c=legacy_by_code.get(str(item["box_code"]))
            if c: item["container_id"]=str(c.id);container_ids.append(c.id)
    if container_ids:
        containers=db.execute(select(Container).where(Container.id.in_(container_ids))).scalars().all()
        id_to_code = {c.id:str(c.id) for c in containers}
        if id_to_code:
            vals = db.execute(select(InventoryBalance.container_id, func.coalesce(func.sum(InventoryBalance.quantity),0)).where(
                InventoryBalance.client_id==client_uuid, InventoryBalance.warehouse_id==warehouse_uuid, InventoryBalance.container_id.in_(list(id_to_code))
            ).group_by(InventoryBalance.container_id)).all()
            qty_by_box = {str(cid): float(qty or 0) for cid,qty in vals}
    pallet_closed = {str(x["pallet_code"]): bool(x.get("closed_at")) for x in pallets}
    wb=Workbook(); ws=wb.active; ws.title="Палеты и короба"
    ws.append(["Приёмка", r.get("name") or "", "Создана", r.get("created_at"), "Статус", r.get("status")])
    ws.append(["Комментарий", r.get("reference") or ""]); ws.append([])
    ws.append(["№", "Палета", "Короб", "Товаров в коробе, шт.", "Палета закрыта", "Короб отсканирован"] )
    idx=1
    for b in boxes:
        code=str(b["box_code"]); pal=str(b["pallet_code"]); ws.append([idx,pal,code,qty_by_box.get(str(b.get("container_id") or ""),0),"Да" if pallet_closed.get(pal) else "Нет",b.get("created_at")]); idx+=1
    if not boxes:
        for pal in pallets:
            ws.append([idx,str(pal["pallet_code"]),"",0,"Да" if pal.get("closed_at") else "Нет",pal.get("created_at")]); idx+=1
    ws.freeze_panes="A5"
    widths={"A":7,"B":22,"C":42,"D":22,"E":18,"F":22}
    for col,w in widths.items(): ws.column_dimensions[col].width=w
    out=io.BytesIO(); wb.save(out); out.seek(0)
    safe=(r.get("name") or "receipt").replace('"','').replace('/','_').replace('\\','_')
    return StreamingResponse(out, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f'attachment; filename="pallet_receipt_{receipt_id}.xlsx"'})


@router.post("/pallet-receipts/{receipt_id}/layout")
async def upload_pallet_receipt_layout(
    receipt_id: str,
    performed_by: str | None = Form(None),
    stock_class: Literal["FBS", "MAIN"] = Form("FBS"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Atomically load an Excel box layout into local stock.

    Historical builds committed every Excel row separately. A late validation/network error
    could therefore leave a half-loaded receipt and a retry could duplicate the first rows.
    The new path validates the complete file, resolves boxes/SKUs in sets and commits one
    idempotent inventory operation plus the receipt status in a single transaction.
    """
    r = _get_pallet_receipt(db, receipt_id)
    client_id = uuid.UUID(r["client_id"])
    warehouse_id = uuid.UUID(r["warehouse_id"])
    operation_key = f"pallet-layout-{receipt_id}"

    existing_operation = db.execute(select(InventoryOperation).where(
        InventoryOperation.client_id == client_id,
        InventoryOperation.idempotency_key == operation_key,
    )).scalars().first()
    if existing_operation is not None:
        if str(r.get("status") or "").upper() != "LOADED":
            db.execute(text("UPDATE fc_pallet_receipts SET status='LOADED' WHERE id=:r"), {"r": receipt_id})
            db.commit()
        recovered = db.execute(
            select(SKU.barcode, func.sum(InventoryOperationLine.quantity))
            .join(InventoryOperationLine, InventoryOperationLine.sku_id == SKU.id)
            .where(InventoryOperationLine.operation_id == existing_operation.id)
            .group_by(SKU.barcode)
        ).all()
        loaded_stock_class = "MAIN" if "ЗОНА:MAIN" in str(existing_operation.comment or "").upper() else "FBS"
        pending_lines = [
            {
                "barcode": str(barcode), "quantity": int(Decimal(quantity or 0)), "mode": "ADD",
                "idempotency_key": _wb_pending_key(f"pallet:{receipt_id}", str(barcode)),
            }
            for barcode, quantity in recovered
            if loaded_stock_class == "FBS" and barcode and Decimal(quantity or 0) > 0
        ]
        return {
            "ok": True, "already_loaded": True, "rows_imported": 0, "rows_failed": 0,
            "errors": [], "boxes_filled": 0, "pending_lines": pending_lines,
            "wb_pending": bool(pending_lines), "operation_id": str(existing_operation.id),
            "stock_class": loaded_stock_class,
            "note": "Локальная палетная приёмка уже была проведена; повторное списание не выполнено.",
        }

    if str(r.get("status") or "").upper() == "LOADED":
        # Legacy receipt without the new deterministic operation key. Do not fabricate ADD
        # publication items because we cannot prove whether an old browser already sent them.
        return {
            "ok": True, "already_loaded": True, "legacy_loaded": True,
            "rows_imported": 0, "rows_failed": 0, "errors": [], "boxes_filled": 0,
            "pending_lines": [], "wb_pending": False,
            "note": "Приёмка была проведена старой версией. Остатки повторно не изменены.",
        }
    if str(r.get("status") or "").upper() != "OPEN":
        raise HTTPException(status_code=409, detail="Приёмка закрыта")

    content = await file.read()
    rows = _rows_from_workbook(content)
    if rows and _looks_header(rows[0]):
        rows = rows[1:]
    if not rows:
        raise HTTPException(status_code=400, detail="В файле нет строк")

    box_rows = db.execute(text("""
        SELECT box_code,pallet_code,container_id
        FROM fc_pallet_receipt_boxes
        WHERE receipt_id=:r
    """), {"r": receipt_id}).mappings().all()
    box_records = {str(row["box_code"]): dict(row) for row in box_rows}
    if not box_records:
        raise HTTPException(status_code=409, detail="В приёмке ещё нет коробов")

    parsed: list[dict] = []
    errors: list[dict] = []
    wb_barcode_rows: list[tuple[int, str]] = []
    for idx, raw_row in enumerate(rows, start=1):
        try:
            box, raw_barcode, raw_qty, article, size = (list(raw_row) + [None] * 5)[:5]
            box = str(box or "").strip()
            barcode = normalize_product_barcode(str(raw_barcode or "").strip())[0]
            qty = Decimal(str(raw_qty or "0"))
            if box not in box_records:
                raise ValueError(f"короб {box or '—'} не принят в эту палетную приёмку")
            if not barcode or qty <= 0:
                raise ValueError("некорректный ШК или количество")
            parsed.append({
                "row": idx, "box": box, "barcode": barcode, "quantity": qty,
                "article": str(article or "").strip() or None,
                "size": str(size or "").strip() or None,
            })
            wb_barcode_rows.append((idx, barcode))
        except Exception as exc:
            errors.append({"row": idx, "error": str(getattr(exc, "detail", exc))[:250]})
    if errors:
        return {
            "ok": False, "rows_imported": 0, "rows_failed": len(errors), "errors": errors[:100],
            "boxes_filled": 0, "pending_lines": [], "wb_pending": False,
            "note": "Файл не проведён: сначала исправьте ошибки. Остатки не изменены.",
        }

    wb_preflight_errors = _strict_wb_receiving_errors(db, client_id, wb_barcode_rows)
    if wb_preflight_errors:
        raise HTTPException(
            status_code=409,
            detail="Раскладка не проведена: " + "; ".join(wb_preflight_errors[:12]) +
                (f"; ещё ошибок: {len(wb_preflight_errors)-12}" if len(wb_preflight_errors) > 12 else ""),
        )

    # Resolve all box containers with a single main query. Scanning normally creates these
    # beforehand; the fallback only repairs a legacy row missing container_id.
    loc = _ensure_location(db, warehouse_id, "ПРИЕМКА-ПАЛЕТЫ")
    container_ids: set[uuid.UUID] = set()
    for record in box_records.values():
        try:
            if record.get("container_id"):
                container_ids.add(uuid.UUID(str(record["container_id"])))
        except (TypeError, ValueError):
            pass
    containers = db.execute(select(Container).where(Container.id.in_(list(container_ids)))).scalars().all() if container_ids else []
    container_by_id = {container.id: container for container in containers}
    container_by_box: dict[str, Container] = {}
    for box, record in box_records.items():
        cont = None
        try:
            cid = uuid.UUID(str(record.get("container_id"))) if record.get("container_id") else None
            cont = container_by_id.get(cid) if cid else None
        except (TypeError, ValueError):
            cont = None
        if cont is None:
            cont = _ensure_container(db, client_id, warehouse_id, loc.id, box)
            if cont is not None:
                db.execute(text("""
                    UPDATE fc_pallet_receipt_boxes SET container_id=:container_id
                    WHERE receipt_id=:receipt_id AND box_code=:box_code
                """), {"container_id": str(cont.id), "receipt_id": receipt_id, "box_code": box})
        if cont is None or cont.client_id != client_id or cont.warehouse_id != warehouse_id:
            db.rollback()
            raise HTTPException(status_code=409, detail=f"Короб {box} не найден на выбранном складе")
        container_by_box[box] = cont

    location_ids = {cont.location_id for cont in container_by_box.values() if cont.location_id is not None}
    location_rows = db.execute(select(Location).where(Location.id.in_(list(location_ids)))).scalars().all() if location_ids else []
    location_by_id = {location.id: location for location in location_rows}
    destination_by_box: dict[str, Location] = {}
    for box, cont in container_by_box.items():
        destination = location_by_id.get(cont.location_id) if cont.location_id else None
        if destination is None or destination.warehouse_id != warehouse_id or not destination.is_active:
            destination = loc
            cont.location_id = loc.id
        destination_by_box[box] = destination

    # Resolve all SKU identities in sets. This mirrors the structured receiving fast path.
    barcode_meta: dict[str, dict] = {}
    for item in parsed:
        barcode_meta.setdefault(item["barcode"], item)
    barcodes = list(barcode_meta)
    sku_cache: dict[str, SKU] = {}
    sku_code_candidates: dict[str, SKU] = {}
    for start_index in range(0, len(barcodes), 3000):
        chunk = barcodes[start_index:start_index + 3000]
        found = db.execute(select(SKU).where(
            SKU.client_id == client_id,
            or_(SKU.barcode.in_(chunk), SKU.sku_code.in_([f"WB-{code}" for code in chunk])),
        )).scalars().all()
        for sku in found:
            if str(sku.barcode or "").strip() in barcode_meta:
                sku_cache.setdefault(str(sku.barcode).strip(), sku)
            sku_code_candidates.setdefault(str(sku.sku_code or ""), sku)
    identities = wb_product_identity_map(db, client_id, set(barcodes)) if barcodes else {}
    new_skus: list[SKU] = []
    for barcode in barcodes:
        item = barcode_meta[barcode]
        sku = sku_cache.get(barcode)
        if sku is None:
            legacy = sku_code_candidates.get(f"WB-{barcode}")
            if legacy is not None:
                legacy_barcode = str(legacy.barcode or "").strip()
                if legacy_barcode and legacy_barcode != barcode:
                    db.rollback()
                    raise HTTPException(status_code=409, detail=f"SKU {legacy.sku_code} уже связан с другим ШК {legacy_barcode}")
                legacy.barcode = barcode
                sku = legacy
        product = identities.get(barcode)
        if sku is None:
            resolved_article = (product or {}).get("article") or item.get("article") or barcode
            sku = SKU(
                id=uuid.uuid4(), client_id=client_id, sku_code=f"WB-{barcode}",
                article=resolved_article, name=(product or {}).get("name") or resolved_article,
                size=(product or {}).get("size") or item.get("size"), barcode=barcode,
                unit="PCS", is_active=True,
            )
            new_skus.append(sku)
        else:
            if product:
                sku.article = product.get("article") or sku.article
                sku.name = product.get("name") or sku.name
                sku.size = product.get("size") or item.get("size") or sku.size
            else:
                sku.article = sku.article or item.get("article") or barcode
                sku.name = sku.name or sku.article
                sku.size = sku.size or item.get("size")
            sku.is_active = True
        sku_cache[barcode] = sku
    if new_skus:
        db.add_all(new_skus)
    db.flush()

    stock_lines: list[StockLine] = []
    pending: dict[str, int] = {}
    touched_boxes: set[str] = set()
    for item in parsed:
        sku = sku_cache[item["barcode"]]
        cont = container_by_box[item["box"]]
        destination = destination_by_box[item["box"]]
        stock_lines.append(StockLine(
            sku_id=sku.id, quantity=item["quantity"], destination_location_id=destination.id,
            destination_container_id=cont.id, reason=f"PALLET_LAYOUT:{receipt_id}",
        ))
        pending[item["barcode"]] = pending.get(item["barcode"], 0) + int(item["quantity"])
        touched_boxes.add(item["box"])

    try:
        operation = execute_operation(db, OperationCreate(
            client_id=client_id, warehouse_id=warehouse_id, operation_type="RECEIVING_IMPORT",
            idempotency_key=operation_key, performed_by=performed_by,
            reference=r.get("reference") or r.get("name"),
            comment=f"Палетная приёмка · {len(touched_boxes)} коробов · {len(stock_lines)} строк · ЗОНА:{stock_class}",
            lines=stock_lines,
        ), commit=False)
        # Make the local stock contour explicit. FBS is saleable locally immediately; MAIN
        # is storage-only. This avoids an intermediate MAIN -> FBS transfer.
        seen_buckets = set()
        for line in stock_lines:
            key = (line.sku_id, line.destination_location_id, line.destination_container_id)
            if key in seen_buckets:
                continue
            seen_buckets.add(key)
            reconcile_stock_bucket(
                db, client_id=client_id, warehouse_id=warehouse_id, sku_id=line.sku_id,
                location_id=line.destination_location_id, container_id=line.destination_container_id,
                preferred_bucket="FBS_AVAILABLE" if stock_class == "FBS" else "STORAGE_ONLY", lock=True,
            )
        db.execute(text("UPDATE fc_pallet_receipts SET status='LOADED' WHERE id=:r"), {"r": receipt_id})
        db.commit()
    except Exception:
        db.rollback()
        raise

    pending_lines = [
        {
            "barcode": barcode, "quantity": quantity, "mode": "ADD",
            "idempotency_key": _wb_pending_key(f"pallet:{receipt_id}", barcode),
        }
        for barcode, quantity in pending.items()
    ] if stock_class == "FBS" else []
    return {
        "ok": True, "rows_imported": len(stock_lines), "rows_failed": 0, "errors": [],
        "boxes_filled": len(touched_boxes), "pending_lines": pending_lines,
        "wb_pending": bool(pending_lines), "operation_id": str(operation.id),
        "stock_class": stock_class,
        "note": (
            "Товар атомарно принят сразу в FBS. WB публикуется отдельным идемпотентным этапом."
            if stock_class == "FBS" else
            "Товар атомарно принят на Основной склад; WB не менялся."
        ),
    }


# ---------------- Структурная Excel-приёмка: секция -> палета -> короб -> товар ----------------
class StructuredReceiptCommitRequest(BaseModel):
    # stock_class is the local warehouse destination. publish_to_fbs is kept for
    # backward compatibility with r82/r83 browsers, where it also selected FBS.
    stock_class: Literal['MAIN','FBS'] | None = None
    publish_to_fbs: bool = False
    wb_warehouse_id: int | None = None


def _structured_receipt_row_to_dict(row: dict) -> dict:
    quantity = row.get("quantity")
    try:
        quantity_value = Decimal(str(quantity)) if quantity is not None else None
    except Exception:
        quantity_value = None
    parse_errors = [x.strip() for x in str(row.get("parse_error_text") or "").split("\n") if x.strip()]
    return {
        "row_number": int(row.get("row_number") or 0),
        "section": str(row.get("section_code") or "").strip(),
        "row_on_pallet": row.get("row_on_pallet"),
        "pallet": str(row.get("pallet_code") or "").strip(),
        "pallet_limit": int(row["pallet_limit"]) if row.get("pallet_limit") is not None else None,
        "box": str(row.get("box_code") or "").strip(),
        "batch": str(row.get("batch_name") or "").strip(),
        "source": str(row.get("source_name") or "").strip(),
        "accepted_at": str(row.get("accepted_at_text") or "").strip(),
        "days": str(row.get("days_text") or "").strip(),
        "volume_m3": str(row.get("volume_m3_text") or "").strip(),
        "accrued_rub": str(row.get("accrued_rub_text") or "").strip(),
        "article": str(row.get("article") or "").strip(),
        "size": str(row.get("size_name") or "").strip(),
        "barcode": str(row.get("barcode") or "").strip(),
        "quantity": quantity_value,
        "is_empty_box": bool(row.get("is_empty_box")),
        "parse_errors": parse_errors,
        "errors": list(parse_errors),
        "warnings": [x.strip() for x in str(row.get("warning_text") or "").split("\n") if x.strip()],
    }


def _structured_box_candidates(
    db: Session,
    box_codes: set[str],
    *,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
) -> list[Container]:
    """Find boxes only inside the current client/warehouse.

    A human box name from Excel is not a global identifier. Different fulfillment
    clients may legitimately use the same box name, so another client's box must
    never block receiving.
    """
    if not box_codes:
        return []
    name_column = getattr(Container, "name", Container.barcode)
    return db.execute(select(Container).where(
        Container.client_id == client_id,
        Container.warehouse_id == warehouse_id,
        or_(Container.barcode.in_(list(box_codes)), name_column.in_(list(box_codes))),
    )).scalars().all()


def _structured_current_validation(
    db: Session,
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    rows: list[dict],
) -> dict:
    """Apply live warehouse conflicts without changing stock.

    File-only errors are preserved in ``parse_errors``. Existing empty boxes may be
    reused/moved; boxes containing stock are blocked so a repeated Excel can never
    silently double the warehouse balance.
    """
    wb_connected = bool(_active_wb_credential(db, client_id))
    requested_wb_barcodes: set[str] = set()
    if wb_connected:
        for row in rows:
            if row.get("is_empty_box") or (row.get("quantity") or Decimal("0")) <= 0:
                continue
            normalized, _dup = normalize_product_barcode(str(row.get("barcode") or "").strip())
            if normalized:
                requested_wb_barcodes.add(normalized)
    wb_match_cache = wb_product_identity_map(db, client_id, requested_wb_barcodes) if requested_wb_barcodes else {}

    for row in rows:
        row["errors"] = list(dict.fromkeys(row.get("parse_errors") or []))
        row["warnings"] = list(dict.fromkeys(row.get("warnings") or []))
        if wb_connected and not row.get("is_empty_box") and (row.get("quantity") or Decimal("0")) > 0:
            raw_barcode = str(row.get("barcode") or "").strip()
            normalized, _dup = normalize_product_barcode(raw_barcode)
            if normalized and normalized not in wb_match_cache:
                # WB catalogue availability must never block physical receiving.
                # The warehouse intake is authoritative for what physically arrived;
                # WB FBS publication is a separate integration step.
                row["warnings"].append(
                    f"ШК {normalized} не найден в карточках WB выбранного клиента — "
                    "на склад FulfillCore товар будет принят; если этот ШК является дополнительным ШК карточки WB, он тоже считается допустимым"
                )

    rows_by_box: dict[str, list[dict]] = {}
    rows_by_pallet: dict[tuple[str, str], list[dict]] = {}
    box_target: dict[str, tuple[str, str]] = {}
    pallet_limits: dict[tuple[str, str], int | None] = {}
    for row in rows:
        box = str(row.get("box") or "").strip()
        key = (str(row.get("section") or "").strip(), str(row.get("pallet") or "").strip())
        if box:
            rows_by_box.setdefault(box, []).append(row)
            box_target.setdefault(box, key)
        rows_by_pallet.setdefault(key, []).append(row)
        if row.get("pallet_limit") is not None:
            pallet_limits[key] = int(row["pallet_limit"])

    box_codes = set(rows_by_box)
    containers = _structured_box_candidates(
        db, box_codes, client_id=client_id, warehouse_id=warehouse_id
    )
    container_ids = [c.id for c in containers]
    quantity_by_container: dict[uuid.UUID, Decimal] = {}
    if container_ids:
        qty_rows = db.execute(select(
            InventoryBalance.container_id,
            func.coalesce(func.sum(InventoryBalance.quantity), 0),
        ).where(InventoryBalance.container_id.in_(container_ids)).group_by(InventoryBalance.container_id)).all()
        quantity_by_container = {cid: Decimal(qty or 0) for cid, qty in qty_rows}

    containers_by_code: dict[str, list[Container]] = {}
    for container in containers:
        values = {str(container.barcode or "").strip()}
        if hasattr(container, "name"):
            values.add(str(getattr(container, "name", "") or "").strip())
        for value in values:
            if value in box_codes:
                containers_by_code.setdefault(value, []).append(container)

    live_box: dict[str, Container] = {}
    existing_empty_boxes = 0
    for box, box_rows in rows_by_box.items():
        matches = containers_by_code.get(box, [])
        same = matches
        if len(same) > 1:
            message = f"по коду короба {box} найдено несколько активных записей; требуется ручная проверка"
            for row in box_rows:
                row["errors"].append(message)
            continue
        if same:
            container = same[0]
            live_box[box] = container
            current_qty = quantity_by_container.get(container.id, Decimal("0"))
            if current_qty > 0:
                message = f"короб {box} уже содержит {current_qty} шт. на складе; повторная загрузка заблокирована"
                for row in box_rows:
                    row["errors"].append(message)
            else:
                existing_empty_boxes += 1
                for row in box_rows:
                    row["warnings"].append(f"короб {box} уже есть пустым — будет использован и перемещён в место из Excel")

    pallet_codes = {
        key: structured_location_code(*key)
        for key in rows_by_pallet
        if key[0] and key[1]
    }
    section_codes = {
        section: structured_section_code(section)
        for section, _pallet in rows_by_pallet
        if section
    }
    requested_codes = list(pallet_codes.values()) + list(section_codes.values())
    location_rows = []
    if requested_codes:
        location_rows = db.execute(select(Location).where(
            Location.warehouse_id == warehouse_id,
            Location.code.in_(requested_codes),
        )).scalars().all()
    location_by_code = {str(loc.code): loc for loc in location_rows}
    for section, code in section_codes.items():
        loc = location_by_code.get(code)
        if loc and str(loc.location_type or "").upper() != "SECTION":
            message = f"код секции {code} уже существует как {loc.location_type or 'другой тип'}"
            for key, section_rows in rows_by_pallet.items():
                if key[0] == section:
                    for row in section_rows:
                        row["errors"].append(message)
    for key, code in pallet_codes.items():
        loc = location_by_code.get(code)
        if loc and str(loc.location_type or "").upper() != "PALLET":
            message = f"адрес {code} уже существует как {loc.location_type or 'другой тип'}, а Excel требует палету"
            for row in rows_by_pallet.get(key, []):
                row["errors"].append(message)

    # Pallet capacity includes boxes already present in the target location. Existing
    # empty boxes from this file are counted only once if they are already there.
    target_location_ids = [
        loc.id for loc in location_rows
        if str(loc.location_type or "").upper() == "PALLET" and str(loc.code or "") in set(pallet_codes.values())
    ]
    existing_boxes_by_location: dict[uuid.UUID, set[str]] = {}
    if target_location_ids:
        existing_containers = db.execute(select(Container).where(
            Container.client_id == client_id,
            Container.warehouse_id == warehouse_id,
            Container.location_id.in_(target_location_ids),
            Container.status == "ACTIVE",
        )).scalars().all()
        for container in existing_containers:
            existing_boxes_by_location.setdefault(container.location_id, set()).add(str(container.barcode or getattr(container, "name", "") or container.id))

    for key, pallet_rows in rows_by_pallet.items():
        limit = pallet_limits.get(key)
        if limit is None:
            continue
        imported_boxes = {str(row.get("box") or "").strip() for row in pallet_rows if str(row.get("box") or "").strip()}
        code = pallet_codes.get(key)
        loc = location_by_code.get(code) if code else None
        current_codes = set(existing_boxes_by_location.get(loc.id, set())) if loc else set()
        already_here = {
            box for box in imported_boxes
            if box in live_box and loc is not None and live_box[box].location_id == loc.id
        }
        projected = len(current_codes) + len(imported_boxes - already_here)
        if projected > limit:
            message = (
                f"секция {key[0]} / палета {key[1]} после приёмки будет содержать "
                f"{projected} коробов при лимите {limit}"
            )
            for row in pallet_rows:
                row["errors"].append(message)

    for row in rows:
        row["errors"] = list(dict.fromkeys(x for x in row.get("errors", []) if x))
        row["warnings"] = list(dict.fromkeys(x for x in row.get("warnings", []) if x))

    summary = structured_rows_summary(rows)
    summary["existing_empty_boxes"] = existing_empty_boxes
    return summary


def _structured_store_validation(db: Session, receipt_id: str, rows: list[dict], summary: dict, *, status: str | None = None) -> None:
    params = []
    for row in rows:
        params.append({
            "receipt_id": receipt_id,
            "row_number": int(row["row_number"]),
            "row_on_pallet": row.get("row_on_pallet"),
            "error_text": "\n".join(row.get("errors") or []) or None,
            "warning_text": "\n".join(row.get("warnings") or []) or None,
            "validation_status": "ERROR" if row.get("errors") else "VALID",
        })
    if params:
        db.execute(text("""
            UPDATE fc_structured_receipt_rows
            SET error_text=:error_text, warning_text=:warning_text, validation_status=:validation_status
            WHERE receipt_id=:receipt_id AND row_number=:row_number
        """), params)
    fields = {
        "receipt_id": receipt_id,
        "error_count": int(summary.get("error_count") or 0),
        "warning_count": int(summary.get("warning_count") or 0),
        "status": status or ("NEEDS_FIX" if int(summary.get("error_count") or 0) else "READY"),
    }
    db.execute(text("""
        UPDATE fc_structured_receipts
        SET error_count=:error_count, warning_count=:warning_count, status=:status
        WHERE id=:receipt_id
    """), fields)


def _structured_receipt_record(db: Session, receipt_id: str) -> dict:
    ensure_runtime_schema(db)
    row = db.execute(text("""
        SELECT * FROM fc_structured_receipts WHERE id=:id
    """), {"id": receipt_id}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Структурная приёмка не найдена")
    return dict(row)


def _structured_receipt_rows(db: Session, receipt_id: str) -> list[dict]:
    return [dict(row) for row in db.execute(text("""
        SELECT r.*,side.row_on_pallet FROM fc_structured_receipt_rows r
        LEFT JOIN fc_structured_row_routes side ON side.receipt_row_id=r.id
        WHERE r.receipt_id=:id ORDER BY r.row_number
    """), {"id": receipt_id}).mappings().all()]


def _structured_receipt_detail(db: Session, receipt_id: str) -> dict:
    receipt = _structured_receipt_record(db, receipt_id)
    raw_rows = _structured_receipt_rows(db, receipt_id)
    sections: dict[str, dict] = {}
    box_map: dict[tuple[str, str, str], dict] = {}
    errors = []
    warnings = []
    for row in raw_rows:
        section = str(row.get("section_code") or "")
        pallet = str(row.get("pallet_code") or "")
        box = str(row.get("box_code") or "")
        section_item = sections.setdefault(section, {"section": section, "pallets": {}})
        pallet_item = section_item["pallets"].setdefault(pallet, {
            "pallet": pallet,
            "limit": row.get("pallet_limit"),
            "location_code": structured_location_code(section, pallet) if section and pallet else "",
            "boxes": [],
        })
        key = (section, pallet, box)
        box_item = box_map.get(key)
        if box_item is None:
            box_item = {
                "box": box,
                "row_on_pallet": row.get("row_on_pallet"),
                "batch": str(row.get("batch_name") or ""),
                "source": str(row.get("source_name") or ""),
                "accepted_at": str(row.get("accepted_at_text") or ""),
                "quantity": 0.0,
                "product_rows": 0,
                "empty": True,
                "has_error": False,
                "has_warning": False,
                "items": [],
            }
            box_map[key] = box_item
            pallet_item["boxes"].append(box_item)
        if row.get("barcode") or row.get("article") or row.get("size_name"):
            box_item["product_rows"] += 1
            box_item["empty"] = False
            box_item["items"].append({
                "row": int(row.get("row_number") or 0),
                "article": str(row.get("article") or ""),
                "size": str(row.get("size_name") or ""),
                "barcode": str(row.get("barcode") or ""),
                "quantity": float(row.get("quantity") or 0),
                "status": str(row.get("validation_status") or ""),
                "error": str(row.get("error_text") or ""),
                "warning": str(row.get("warning_text") or ""),
            })
        if row.get("quantity") is not None:
            box_item["quantity"] += float(row.get("quantity") or 0)
        if row.get("validation_status") == "ERROR" or row.get("error_text"):
            box_item["has_error"] = True
        if row.get("warning_text"):
            box_item["has_warning"] = True
        if row.get("error_text") and len(errors) < 200:
            errors.append({"row": int(row.get("row_number") or 0), "box": box, "error": str(row.get("error_text"))})
        if row.get("warning_text") and len(warnings) < 100:
            warnings.append({"row": int(row.get("row_number") or 0), "box": box, "warning": str(row.get("warning_text"))})

    section_list = []
    for section_item in sections.values():
        pallets = list(section_item["pallets"].values())
        for pallet_item in pallets:
            pallet_item["boxes"].sort(key=lambda item: (item.get("row_on_pallet") or 1000, structured_natural_key(item["box"])))
            pallet_item["rows_on_pallet"] = sorted({int(item["row_on_pallet"]) for item in pallet_item["boxes"] if item.get("row_on_pallet") is not None})
            pallet_item["box_count"] = len(pallet_item["boxes"])
            pallet_item["quantity"] = sum(float(item["quantity"] or 0) for item in pallet_item["boxes"])
        pallets.sort(key=lambda item: structured_natural_key(item["pallet"]))
        section_item["pallets"] = pallets
        section_item["pallet_count"] = len(pallets)
        section_item["box_count"] = sum(item["box_count"] for item in pallets)
        section_item["quantity"] = sum(item["quantity"] for item in pallets)
        section_list.append(section_item)
    section_list.sort(key=lambda item: structured_natural_key(item["section"]))

    serialized_receipt = dict(receipt)
    policy = db.execute(text('SELECT stock_class FROM fc_structured_receipt_policy WHERE receipt_id=:id'), {'id': receipt_id}).scalar()
    serialized_receipt['stock_class'] = str(policy or '') or None
    for key in ["created_at", "committed_at"]:
        if serialized_receipt.get(key) is not None:
            serialized_receipt[key] = str(serialized_receipt[key])
    serialized_receipt["quantity_total"] = float(serialized_receipt.get("quantity_total") or 0)
    return {
        "receipt": serialized_receipt,
        "sections": section_list,
        "errors": errors,
        "warnings": warnings,
    }


@router.post("/structured-receipts")
async def create_structured_receipt(
    client_id: uuid.UUID = Form(...),
    warehouse_id: uuid.UUID = Form(...),
    name: str | None = Form(None),
    reference: str | None = Form(None),
    performed_by: str | None = Form(None),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    ensure_runtime_schema(db)
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Excel-файл больше 20 МБ. Разделите приёмку на несколько файлов.")
    try:
        rows, parsed_summary = parse_structured_workbook(content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    live_summary = _structured_current_validation(db, client_id, warehouse_id, rows)
    summary = {**parsed_summary, **live_summary}
    receipt_id = str(uuid.uuid4())
    status = "NEEDS_FIX" if int(summary.get("error_count") or 0) else "READY"
    db.execute(text("""
        INSERT INTO fc_structured_receipts(
            id,client_id,warehouse_id,name,reference,source_filename,source_sheet,status,performed_by,
            section_count,pallet_count,box_count,row_count,product_row_count,zero_product_row_count,empty_box_count,quantity_total,error_count,warning_count
        ) VALUES (
            :id,:client_id,:warehouse_id,:name,:reference,:source_filename,:source_sheet,:status,:performed_by,
            :section_count,:pallet_count,:box_count,:row_count,:product_row_count,:zero_product_row_count,:empty_box_count,:quantity_total,:error_count,:warning_count
        )
    """), {
        "id": receipt_id,
        "client_id": str(client_id),
        "warehouse_id": str(warehouse_id),
        "name": (name or "").strip() or None,
        "reference": (reference or "").strip() or None,
        "source_filename": str(file.filename or "structured_receiving.xlsx")[:512],
        "source_sheet": str(summary.get("sheet_name") or "")[:255] or None,
        "status": status,
        "performed_by": (performed_by or "").strip() or None,
        "section_count": int(summary.get("section_count") or 0),
        "pallet_count": int(summary.get("pallet_count") or 0),
        "box_count": int(summary.get("box_count") or 0),
        "row_count": int(summary.get("row_count") or 0),
        "product_row_count": int(summary.get("product_row_count") or 0),
        "zero_product_row_count": int(summary.get("zero_product_row_count") or 0),
        "empty_box_count": int(summary.get("empty_box_count") or 0),
        "quantity_total": Decimal(str(summary.get("quantity_total") or 0)),
        "error_count": int(summary.get("error_count") or 0),
        "warning_count": int(summary.get("warning_count") or 0),
    })
    insert_rows = []
    for row in rows:
        insert_rows.append({
            "id": str(uuid.uuid4()),
            "receipt_id": receipt_id,
            "row_number": int(row["row_number"]),
            "row_on_pallet": row.get("row_on_pallet"),
            "section_code": row.get("section") or None,
            "pallet_code": row.get("pallet") or None,
            "pallet_limit": row.get("pallet_limit"),
            "box_code": row.get("box") or None,
            "batch_name": row.get("batch") or None,
            "source_name": row.get("source") or None,
            "accepted_at_text": row.get("accepted_at") or None,
            "days_text": row.get("days") or None,
            "volume_m3_text": row.get("volume_m3") or None,
            "accrued_rub_text": row.get("accrued_rub") or None,
            "article": row.get("article") or None,
            "size_name": row.get("size") or None,
            "barcode": row.get("barcode") or None,
            "quantity": row.get("quantity"),
            "is_empty_box": bool(row.get("is_empty_box")),
            "parse_error_text": "\n".join(row.get("parse_errors") or []) or None,
            "error_text": "\n".join(row.get("errors") or []) or None,
            "warning_text": "\n".join(row.get("warnings") or []) or None,
            "validation_status": "ERROR" if row.get("errors") else "VALID",
        })
    db.execute(text("""
        INSERT INTO fc_structured_receipt_rows(
            id,receipt_id,row_number,section_code,pallet_code,pallet_limit,box_code,batch_name,source_name,
            accepted_at_text,days_text,volume_m3_text,accrued_rub_text,article,size_name,barcode,quantity,is_empty_box,
            parse_error_text,error_text,warning_text,validation_status
        ) VALUES (
            :id,:receipt_id,:row_number,:section_code,:pallet_code,:pallet_limit,:box_code,:batch_name,:source_name,
            :accepted_at_text,:days_text,:volume_m3_text,:accrued_rub_text,:article,:size_name,:barcode,:quantity,:is_empty_box,
            :parse_error_text,:error_text,:warning_text,:validation_status
        )
    """), insert_rows)
    route_rows=[{'id':r['id'],'side':r['row_on_pallet']} for r in insert_rows if r.get('row_on_pallet')]
    if route_rows:
        db.execute(text('INSERT INTO fc_structured_row_routes(receipt_row_id,row_on_pallet) VALUES(:id,:side)'),route_rows)
    db.commit()
    detail = _structured_receipt_detail(db, receipt_id)
    detail["summary"] = summary
    return detail


@router.get("/structured-receipts")
def list_structured_receipts(
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    ensure_runtime_schema(db)
    rows = db.execute(text("""
        SELECT id,name,reference,source_filename,status,performed_by,section_count,pallet_count,box_count,row_count,
               product_row_count,zero_product_row_count,empty_box_count,quantity_total,error_count,warning_count,created_at,committed_at
        FROM fc_structured_receipts
        WHERE client_id=:client_id AND warehouse_id=:warehouse_id
          AND NOT EXISTS (SELECT 1 FROM fc_structured_receipt_archives a WHERE a.receipt_id=fc_structured_receipts.id)
        ORDER BY created_at DESC
        LIMIT :limit
    """), {"client_id": str(client_id), "warehouse_id": str(warehouse_id), "limit": int(limit)}).mappings().all()
    result = []
    for row in rows:
        item = dict(row)
        item["quantity_total"] = float(item.get("quantity_total") or 0)
        item["created_at"] = str(item.get("created_at") or "")
        item["committed_at"] = str(item.get("committed_at") or "") if item.get("committed_at") else None
        result.append(item)
    return {"items": result}


@router.get("/structured-receipts/{receipt_id}")
def get_structured_receipt(receipt_id: str, db: Session = Depends(get_db)):
    return _structured_receipt_detail(db, receipt_id)


@router.get("/structured-receipts/{receipt_id}/fbs-candidates")
def structured_receipt_fbs_candidates(receipt_id: str, db: Session = Depends(get_db)):
    receipt = _structured_receipt_record(db, receipt_id)
    status = str(receipt.get("status") or "").upper()
    if status not in {"COMMITTED", "CANCELLED"}:
        raise HTTPException(status_code=409, detail="Сначала добавьте приёмку на склад")
    client_id = uuid.UUID(str(receipt["client_id"]))
    warehouse_id = uuid.UUID(str(receipt["warehouse_id"]))
    _ensure_fbs_tracking_table(db)
    raw_rows = _structured_receipt_rows(db, receipt_id)
    grouped: dict[str, dict] = {}
    for row in raw_rows:
        barcode = str(row.get("barcode") or "").strip()
        qty = Decimal(row.get("quantity") or 0)
        if not barcode or qty <= 0:
            continue
        normalized, _duplicated_from = normalize_product_barcode(barcode)
        key = normalized or barcode
        item = grouped.setdefault(key, {
            "barcode": key,
            "article": str(row.get("article") or ""),
            "size": str(row.get("size_name") or ""),
            "receipt_quantity": Decimal("0"),
        })
        item["receipt_quantity"] += qty

    publications = {
        str(row["barcode"]): dict(row)
        for row in db.execute(text("""
            SELECT barcode, wb_warehouse_id, published_amount, status, last_error, updated_at
            FROM fc_wb_stock_publications
            WHERE client_id=:client_id AND warehouse_id=:warehouse_id
        """), {"client_id": str(client_id), "warehouse_id": str(warehouse_id)}).mappings().all()
    }
    result = []
    for barcode, item in sorted(grouped.items(), key=lambda pair: (pair[1].get("article") or "", pair[1].get("size") or "", pair[0])):
        sku = db.execute(select(SKU).where(
            SKU.client_id == client_id,
            SKU.barcode == barcode,
        )).scalars().first()
        product = wb_product_identity(db, client_id, barcode)
        publication = publications.get(barcode) or {}
        local_amount = max(0, int(_warehouse_barcode_qty(db, client_id, warehouse_id, barcode)))
        sellable_amount = max(0, int(effective_fbs_free_for_sku(
            db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku.id
        ))) if sku else 0
        if sku and wb_sales_paused(db, client_id=client_id, warehouse_id=warehouse_id, sku_id=sku.id):
            sellable_amount = 0
        published_raw = publication.get("published_amount")
        published_amount = int(published_raw) if published_raw is not None else 0
        result.append({
            "barcode": barcode,
            "article": str((product or {}).get("article") or getattr(sku, "article", "") or item.get("article") or ""),
            "size": str((product or {}).get("size") or getattr(sku, "size", "") or item.get("size") or ""),
            "name": str((product or {}).get("name") or getattr(sku, "name", "") or ""),
            "receipt_quantity": int(item["receipt_quantity"]),
            "warehouse_quantity": local_amount,
            "sellable_quantity": sellable_amount,
            "published_amount": published_amount,
            "wb_found": bool(product),
            "publication_status": str(publication.get("status") or "NOT_PUBLISHED"),
            "last_error": str(publication.get("last_error") or ""),
            "needs_update": sellable_amount != published_amount,
        })
    return {
        "receipt_id": receipt_id,
        "receipt_status": status,
        "items": result,
        "selectable": sum(1 for item in result if item["wb_found"]),
        "needs_update": sum(1 for item in result if item["wb_found"] and item["needs_update"]),
    }


@router.post("/structured-receipts/{receipt_id}/cancel")
def cancel_structured_receipt(
    receipt_id: str,
    payload: CancelReceivingRequest,
    db: Session = Depends(get_db),
):
    receipt = _structured_receipt_record(db, receipt_id)
    status = str(receipt.get("status") or "").upper()
    if status == "CANCELLED":
        return {"ok": True, "already_cancelled": True, "receipt_id": receipt_id, "affected_stocks": [], "wb_results": []}
    if status != "COMMITTED":
        raise HTTPException(status_code=409, detail="Черновик удаляется обычной кнопкой удаления")
    operation_id = receipt.get("committed_operation_id")
    if not operation_id:
        raise HTTPException(status_code=409, detail="У принятой приёмки не найдена складская операция. Отмена остановлена без изменений.")
    try:
        operation = db.get(InventoryOperation, uuid.UUID(str(operation_id)))
    except (TypeError, ValueError):
        operation = None
    if not operation:
        raise HTTPException(status_code=404, detail="Складская операция этой приёмки не найдена")

    try:
        reverse = _reverse_receiving_operation(db, operation, commit=False)
        sku_ids = set(reverse.pop("sku_ids", []))
        db.execute(text("""
            UPDATE fc_structured_receipts
            SET status='CANCELLED'
            WHERE id=:id
        """), {"id": receipt_id})
        db.execute(text("""
            UPDATE fc_structured_receipt_rows
            SET validation_status='CANCELLED'
            WHERE receipt_id=:id
        """), {"id": receipt_id})
        db.commit()
    except Exception:
        db.rollback()
        raise

    affected_stocks = _local_stock_rows(
        db, client_id=operation.client_id, warehouse_id=operation.warehouse_id, sku_ids=sku_ids
    )
    wb_results = []
    if payload.sync_wb and sku_ids:
        wb_results = _publish_skus_after_cancellation(
            db, client_id=operation.client_id, warehouse_id=operation.warehouse_id,
            sku_ids=sku_ids, wb_warehouse_id=payload.wb_warehouse_id,
        )
    detail = _structured_receipt_detail(db, receipt_id)
    detail.update({
        "ok": True,
        "receipt_id": receipt_id,
        "affected_stocks": affected_stocks,
        "wb_requested": payload.sync_wb,
        "wb_results": wb_results,
        "wb_failed": sum(1 for row in wb_results if not row.get("ok")),
        **reverse,
    })
    return detail


@router.delete("/structured-receipts/{receipt_id}")
def delete_structured_receipt(receipt_id: str, db: Session = Depends(get_db)):
    """Remove a structured receipt card without reversing current warehouse stock.

    Drafts have never changed inventory, so they are deleted normally. A committed or
    already-cancelled receipt may have been moved, shipped, reset or transferred since
    receiving; reversing the original receipt from this list can therefore subtract stock
    that no longer exists. For those records DELETE only hides the receipt card by writing
    an archive marker. The original receipt rows and inventory operation remain available
    for audit/history, while balances and reservations stay untouched.
    """
    receipt = _structured_receipt_record(db, receipt_id)
    status = str(receipt.get("status") or "").upper()
    # Older structured receipts can carry a legacy status label even though they already
    # own a committed inventory operation.  The operation id is the authoritative sign
    # that stock was posted; such a receipt must only be archived from this UI, never
    # physically deleted or reversed.
    posted = bool(receipt.get("committed_operation_id")) or status in {"COMMITTED", "CANCELLED", "COMPLETED", "LOADED"}
    if posted:
        inserted = db.execute(text("""
            INSERT INTO fc_structured_receipt_archives(receipt_id) VALUES(:id)
            ON CONFLICT(receipt_id) DO NOTHING
            RETURNING receipt_id
        """), {"id": receipt_id}).scalar_one_or_none()
        db.commit()
        return {
            "ok": True, "deleted": receipt_id, "archived": True,
            "stock_changed": False, "already_archived": inserted is None,
            "operation_id": str(receipt.get("committed_operation_id") or "") or None,
        }

    # READY / NEEDS_FIX are drafts: they never changed stock. Remove only their
    # temporary validation rows and the receipt card.
    db.execute(text("DELETE FROM fc_structured_row_routes WHERE receipt_row_id IN (SELECT id FROM fc_structured_receipt_rows WHERE receipt_id=:id)"), {"id": receipt_id})
    db.execute(text("DELETE FROM fc_structured_receipt_archives WHERE receipt_id=:id"), {"id": receipt_id})
    db.execute(text("DELETE FROM fc_structured_receipt_rows WHERE receipt_id=:id"), {"id": receipt_id})
    db.execute(text("DELETE FROM fc_structured_receipts WHERE id=:id"), {"id": receipt_id})
    db.commit()
    return {"ok": True, "deleted": receipt_id, "archived": False, "stock_changed": False}


@router.get("/structured-receipts/{receipt_id}/unloaded.xlsx")
def export_structured_unloaded(receipt_id: str, db: Session = Depends(get_db)):
    """Excel report of rows that did not reach local stock and/or WB FBS.

    The first sheet intentionally uses the structured receiving headers so it can be
    uploaded back into FulfillCore as a recovery receiving file after corrections.
    """
    receipt = _structured_receipt_record(db, receipt_id)
    client_id = uuid.UUID(str(receipt["client_id"]))
    warehouse_id = uuid.UUID(str(receipt["warehouse_id"]))
    rows = _structured_receipt_rows(db, receipt_id)
    _ensure_fbs_tracking_table(db)

    wb = Workbook()
    ws_local = wb.active
    ws_local.title = "Адресное хранение"
    local_headers = list(STRUCTURED_HEADERS) + ["Статус строки", "Причина, почему не принято"]
    ws_local.append(local_headers)
    local_units = Decimal("0")
    local_rows = 0
    for row in rows:
        qty = Decimal(row.get("quantity") or 0)
        status = str(row.get("validation_status") or "").upper()
        if qty <= 0 or status == "COMMITTED":
            continue
        local_rows += 1
        local_units += qty
        ws_local.append([
            row.get("section_code") or "", row.get("row_on_pallet"), row.get("pallet_code") or "", row.get("pallet_limit"),
            row.get("box_code") or "", row.get("batch_name") or "", row.get("source_name") or "",
            row.get("accepted_at_text") or "", row.get("days_text") or "", row.get("volume_m3_text") or "",
            row.get("accrued_rub_text") or "", row.get("article") or "", row.get("size_name") or "",
            row.get("barcode") or "", row.get("quantity"), status or "НЕ ПРОВЕДЕНО",
            row.get("error_text") or row.get("warning_text") or "Строка не была проведена",
        ])
    ws_local.freeze_panes = "A2"
    ws_local.auto_filter.ref = f"A1:Q{max(1, ws_local.max_row)}"
    for row in ws_local.iter_rows(min_row=2):
        row[4].number_format = "@"
        row[13].number_format = "@"

    publications = {
        str(row["barcode"]): dict(row)
        for row in db.execute(text("""
            SELECT barcode, wb_warehouse_id, published_amount, status, last_error, updated_at
            FROM fc_wb_stock_publications
            WHERE client_id=:client_id AND warehouse_id=:warehouse_id
        """), {"client_id": str(client_id), "warehouse_id": str(warehouse_id)}).mappings().all()
    }

    committed_by_barcode: dict[str, dict] = {}
    for row in rows:
        if str(row.get("validation_status") or "").upper() != "COMMITTED":
            continue
        qty = Decimal(row.get("quantity") or 0)
        barcode = str(row.get("barcode") or "").strip()
        if qty <= 0 or not barcode:
            continue
        normalized, _ = normalize_product_barcode(barcode)
        code = normalized or barcode
        item = committed_by_barcode.setdefault(code, {
            "barcode": code, "article": str(row.get("article") or ""),
            "size": str(row.get("size_name") or ""), "receipt_quantity": Decimal("0"),
            "boxes": set(), "pallets": set(), "sections": set(),
        })
        item["receipt_quantity"] += qty
        if row.get("box_code"): item["boxes"].add(str(row.get("box_code")))
        if row.get("pallet_code"): item["pallets"].add(str(row.get("pallet_code")))
        if row.get("section_code"): item["sections"].add(str(row.get("section_code")))

    ws_wb = wb.create_sheet("Не на WB FBS")
    ws_wb.append([
        "Артикул", "Размер", "ШК", "В этой приёмке, шт.", "На складе FulfillCore, шт.",
        "WB FBS, шт.", "Не хватает на WB, шт.", "Секция", "Палета", "Короб",
        "Статус WB", "Причина / ошибка WB",
    ])
    wb_units = 0
    wb_positions = 0
    for barcode, item in sorted(committed_by_barcode.items(), key=lambda pair: (pair[1]["article"], pair[1]["size"], pair[0])):
        local_amount = max(0, int(_warehouse_barcode_qty(db, client_id, warehouse_id, barcode)))
        publication = publications.get(barcode) or {}
        published_raw = publication.get("published_amount")
        published = max(0, int(published_raw)) if published_raw is not None else 0
        product = wb_product_identity(db, client_id, barcode)
        needs = max(0, local_amount - published)
        pub_status = str(publication.get("status") or "NOT_PUBLISHED")
        last_error = str(publication.get("last_error") or "")
        if needs <= 0 and bool(product) and pub_status == "PUBLISHED":
            continue
        wb_positions += 1
        wb_units += needs
        reason = last_error
        if not product:
            reason = reason or "ШК не найден в карточках WB выбранного клиента"
        elif pub_status != "PUBLISHED":
            reason = reason or "Остаток ещё не подтверждён WB"
        elif needs > 0:
            reason = reason or "Остаток WB меньше локального остатка FulfillCore"
        ws_wb.append([
            str((product or {}).get("article") or item["article"] or ""),
            str((product or {}).get("size") or item["size"] or ""), barcode, int(item["receipt_quantity"]),
            local_amount, published, needs, ", ".join(sorted(item["sections"])),
            ", ".join(sorted(item["pallets"])), ", ".join(sorted(item["boxes"])),
            pub_status, reason,
        ])
    ws_wb.freeze_panes = "A2"
    ws_wb.auto_filter.ref = f"A1:L{max(1, ws_wb.max_row)}"
    for row in ws_wb.iter_rows(min_row=2):
        row[2].number_format = "@"

    ws_sum = wb.create_sheet("Сводка")
    ws_sum.append(["Показатель", "Значение"])
    ws_sum.append(["По файлу, шт.", float(receipt.get("quantity_total") or 0)])
    ws_sum.append(["Не попало на локальный склад, строк", local_rows])
    ws_sum.append(["Не попало на локальный склад, шт.", float(local_units)])
    ws_sum.append(["Проблемных позиций WB FBS", wb_positions])
    ws_sum.append(["Текущая разница локальный склад - WB FBS, шт.", wb_units])
    ws_sum.append(["Примечание", "Лист «Адресное хранение» можно исправить и загрузить как новую структурную приёмку."])

    widths_local = [10,10,9,38,18,18,22,8,12,14,16,22,20,12,18,70]
    for idx, width in enumerate(widths_local, 1):
        ws_local.column_dimensions[chr(64+idx)].width = width
    widths_wb = [18,14,20,18,22,15,18,14,14,38,18,70]
    for idx, width in enumerate(widths_wb, 1):
        ws_wb.column_dimensions[chr(64+idx)].width = width
    ws_sum.column_dimensions["A"].width = 48
    ws_sum.column_dimensions["B"].width = 70

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="unloaded_{receipt_id}.xlsx"'},
    )


@router.get("/structured-receipts/{receipt_id}/export.xlsx")
def export_structured_receipt(receipt_id: str, db: Session = Depends(get_db)):
    receipt = _structured_receipt_record(db, receipt_id)
    rows = _structured_receipt_rows(db, receipt_id)
    wb = Workbook()
    ws = wb.active
    ws.title = "Проверка приёмки"
    headers = list(STRUCTURED_HEADERS) + ["Статус строки", "Ошибка", "Предупреждение"]
    ws.append(headers)
    for row in rows:
        ws.append([
            row.get("section_code") or "",
            row.get("row_on_pallet"),
            row.get("pallet_code") or "",
            row.get("pallet_limit"),
            row.get("box_code") or "",
            row.get("batch_name") or "",
            row.get("source_name") or "",
            row.get("accepted_at_text") or "",
            row.get("days_text") or "",
            row.get("volume_m3_text") or "",
            row.get("accrued_rub_text") or "",
            row.get("article") or "",
            row.get("size_name") or "",
            row.get("barcode") or "",
            row.get("quantity"),
            row.get("validation_status") or "",
            row.get("error_text") or "",
            row.get("warning_text") or "",
        ])
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:R{max(1, ws.max_row)}"
    widths = [10, 9, 10, 9, 38, 18, 18, 22, 8, 12, 14, 16, 22, 20, 12, 16, 55, 55]
    for index, width in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + index)].width = width
    for row in ws.iter_rows(min_row=2):
        row[4].number_format = "@"
        row[13].number_format = "@"
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="structured_receipt_{receipt_id}.xlsx"'},
    )


@router.post("/structured-receipts/{receipt_id}/commit")
def commit_structured_receipt(
    receipt_id: str,
    payload: StructuredReceiptCommitRequest,
    db: Session = Depends(get_db),
):
    ensure_runtime_schema(db)
    with distributed_lock(db, f"structured-receipt:{receipt_id}", blocking=False) as acquired:
        if not acquired:
            raise HTTPException(status_code=409, detail="Эта приёмка уже проводится на другом рабочем месте")
        receipt = _structured_receipt_record(db, receipt_id)
        if str(receipt.get('status') or '').upper()=='CANCELLED':
            raise HTTPException(409,detail='Приёмка отменена')
        client_id = uuid.UUID(str(receipt["client_id"]))
        warehouse_id = uuid.UUID(str(receipt["warehouse_id"]))
        # Record classification with the receipt; retries must use the original choice.
        policy=db.execute(text('SELECT stock_class FROM fc_structured_receipt_policy WHERE receipt_id=:id'),{'id':receipt_id}).scalar()
        requested_stock_class = str(payload.stock_class or ('FBS' if payload.publish_to_fbs else 'MAIN')).upper()
        if requested_stock_class not in ('MAIN','FBS'):
            raise HTTPException(status_code=422, detail='Куда принять: выберите Основной склад или FBS')
        # Once a receipt is committed, its local classification is immutable. A retry
        # reuses the stored policy so a browser refresh can never flip MAIN <-> FBS.
        receipt_fbs=(policy=='FBS') if policy else (requested_stock_class=='FBS')
        if str(receipt.get("status") or "").upper() == "COMMITTED":
            # The local warehouse transaction may be complete while WB publication is
            # still pending (another workstation/browser can open this receipt later).
            # Reconstruct the same deterministic publication items; the server-side event
            # ledger makes already-published items a no-op.
            existing_operation = db.execute(select(InventoryOperation).where(
                InventoryOperation.client_id == client_id,
                InventoryOperation.idempotency_key == f"structured-receipt-{receipt_id}",
            )).scalars().first()
            pending_lines = []
            if existing_operation is not None:
                recovered_rows = db.execute(
                    select(SKU.barcode, func.sum(InventoryOperationLine.quantity))
                    .join(InventoryOperationLine, InventoryOperationLine.sku_id == SKU.id)
                    .where(InventoryOperationLine.operation_id == existing_operation.id)
                    .group_by(SKU.barcode)
                ).all()
                pending_lines = [
                    {
                        "barcode": str(barcode), "quantity": int(Decimal(quantity or 0)), "mode": "ADD",
                        "idempotency_key": _wb_pending_key(f"structured:{receipt_id}", str(barcode)),
                    }
                    for barcode, quantity in recovered_rows
                    if barcode and Decimal(quantity or 0) > 0
                ]
            detail = _structured_receipt_detail(db, receipt_id)
            detail.update({
                "ok": True, "already_committed": True,
                "pending_lines": pending_lines if receipt_fbs else [], "wb_pending": bool(pending_lines) and receipt_fbs,
            })
            return detail

        # Recovery for the historical two-commit implementation: if the inventory
        # operation already exists, stock was committed successfully even if the browser
        # lost the response before the receipt row was marked COMMITTED. Never add it twice.
        existing_operation = db.execute(select(InventoryOperation).where(
            InventoryOperation.client_id == client_id,
            InventoryOperation.idempotency_key == f"structured-receipt-{receipt_id}",
        )).scalars().first()
        if existing_operation is not None:
            db.execute(text("""
                UPDATE fc_structured_receipts
                SET status='COMMITTED', committed_at=COALESCE(committed_at, NOW()), committed_operation_id=:operation_id
                WHERE id=:id
            """), {"id": receipt_id, "operation_id": str(existing_operation.id)})
            db.execute(text("""
                UPDATE fc_structured_receipt_rows
                SET validation_status='COMMITTED'
                WHERE receipt_id=:id AND validation_status <> 'ERROR'
            """), {"id": receipt_id})
            db.commit()
            # In the old flow WB publication happened only *after* this API response. If
            # the browser lost the response, WB could never have received these lines, so
            # reconstruct them from the immutable operation history and let the caller
            # safely resume the external publication step.
            recovered_pending_rows = db.execute(
                select(SKU.barcode, func.sum(InventoryOperationLine.quantity))
                .join(InventoryOperationLine, InventoryOperationLine.sku_id == SKU.id)
                .where(InventoryOperationLine.operation_id == existing_operation.id)
                .group_by(SKU.barcode)
            ).all()
            recovered_pending = [
                {
                    "barcode": str(barcode), "quantity": int(Decimal(quantity or 0)), "mode": "ADD",
                    "idempotency_key": _wb_pending_key(f"structured:{receipt_id}", str(barcode)),
                }
                for barcode, quantity in recovered_pending_rows
                if barcode and Decimal(quantity or 0) > 0
            ]
            detail = _structured_receipt_detail(db, receipt_id)
            detail.update({
                "ok": True, "already_committed": True, "recovered_after_lost_response": True,
                "pending_lines": recovered_pending if receipt_fbs else [], "wb_pending": bool(recovered_pending) and receipt_fbs,
            })
            return detail

        stored = _structured_receipt_rows(db, receipt_id)
        rows = [_structured_receipt_row_to_dict(row) for row in stored]
        summary = _structured_current_validation(db, client_id, warehouse_id, rows)
        _structured_store_validation(db, receipt_id, rows, summary)
        skipped_error_rows = [row for row in rows if row.get("errors")]
        valid_rows = [row for row in rows if not row.get("errors")]
        if not valid_rows:
            db.commit()
            raise HTTPException(
                status_code=409,
                detail=f"Нет ни одной строки, которую можно оприходовать. Ошибок: {len(skipped_error_rows)}.",
            )
        # A bad product row must not block the whole warehouse intake. Commit every
        # currently valid row and keep invalid rows marked ERROR for later correction.
        rows = valid_rows

        # Create/reuse SECTION and PALLET nodes in bulk. The old loop executed one
        # SELECT + FLUSH per section/pallet; large receipts could spend minutes here.
        pallet_keys = sorted(
            {(row["section"], row["pallet"]) for row in rows if row["section"] and row["pallet"]},
            key=lambda pair: (structured_natural_key(pair[0]), structured_natural_key(pair[1])),
        )
        section_values = sorted({section for section, _ in pallet_keys}, key=structured_natural_key)
        section_codes = {section: structured_section_code(section) for section in section_values}
        pallet_codes = {(section, pallet): structured_location_code(section, pallet) for section, pallet in pallet_keys}
        requested_location_codes = list(section_codes.values()) + list(pallet_codes.values())
        existing_locations = db.execute(
            select(Location).where(
                Location.warehouse_id == warehouse_id,
                Location.code.in_(requested_location_codes),
            ).with_for_update()
        ).scalars().all() if requested_location_codes else []
        location_by_code = {str(loc.code): loc for loc in existing_locations}

        section_map: dict[str, Location] = {}
        created_sections = 0
        new_locations: list[Location] = []
        for section in section_values:
            section_code = section_codes[section]
            section_loc = location_by_code.get(section_code)
            if section_loc is not None:
                if str(section_loc.location_type or "").upper() != "SECTION":
                    raise HTTPException(status_code=409, detail=f"Код секции {section_code} уже занят адресом другого типа")
                section_loc.is_active = True
                section_loc.name = structured_section_name(section)
                if not section_loc.barcode:
                    section_loc.barcode = structured_location_barcode(warehouse_id, section_code)
            else:
                section_loc = Location(
                    id=uuid.uuid4(),
                    warehouse_id=warehouse_id,
                    parent_id=None,
                    code=section_code,
                    barcode=structured_location_barcode(warehouse_id, section_code),
                    name=structured_section_name(section),
                    location_type="SECTION",
                    is_active=True,
                )
                new_locations.append(section_loc)
                created_sections += 1
            section_map[section] = section_loc

        if new_locations:
            db.add_all(new_locations)
            db.flush()
            new_locations = []
        location_map: dict[tuple[str, str], Location] = {}
        created_locations = 0
        for section, pallet in pallet_keys:
            code = pallet_codes[(section, pallet)]
            loc = location_by_code.get(code)
            if loc is not None:
                if str(loc.location_type or "").upper() != "PALLET":
                    raise HTTPException(status_code=409, detail=f"Адрес {code} уже существует не как палета")
                loc.is_active = True
                loc.parent_id = section_map[section].id
                loc.name = structured_location_name(section, pallet)
                if not loc.barcode:
                    loc.barcode = structured_location_barcode(warehouse_id, code)
            else:
                loc = Location(
                    id=uuid.uuid4(),
                    warehouse_id=warehouse_id,
                    parent_id=section_map[section].id,
                    code=code,
                    barcode=structured_location_barcode(warehouse_id, code),
                    name=structured_location_name(section, pallet),
                    location_type="PALLET",
                    is_active=True,
                )
                new_locations.append(loc)
                created_locations += 1
            location_map[(section, pallet)] = loc
        if new_locations:
            db.add_all(new_locations)
            db.flush()

        # One physical box code is unique inside this import. Reuse empty boxes and create
        # all missing boxes in-memory, then flush once instead of once per box.
        box_target: dict[str, tuple[str, str]] = {}
        for row in rows:
            if row["box"]:
                box_target.setdefault(row["box"], (row["section"], row["pallet"]))
        existing = _structured_box_candidates(
            db, set(box_target), client_id=client_id, warehouse_id=warehouse_id
        )
        existing_by_code: dict[str, Container] = {}
        for container in existing:
            if container.client_id != client_id or container.warehouse_id != warehouse_id:
                continue
            for value in {str(container.barcode or "").strip(), str(getattr(container, "name", "") or "").strip()}:
                if value in box_target:
                    existing_by_code[value] = container

        container_map: dict[str, Container] = {}
        created_containers = 0
        reused_containers = 0
        new_containers: list[Container] = []
        for box, target in sorted(box_target.items(), key=lambda item: structured_natural_key(item[0])):
            loc = location_map[target]
            cont = existing_by_code.get(box)
            if cont:
                cont.status = "ACTIVE"
                cont.location_id = loc.id
                reused_containers += 1
            else:
                internal_box_barcode = (
                    "FCBOX-" + uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"fulfillcore-box:{client_id}:{warehouse_id}:{box}",
                    ).hex[:24].upper()
                )
                kwargs = dict(
                    id=uuid.uuid4(),
                    client_id=client_id,
                    warehouse_id=warehouse_id,
                    location_id=loc.id,
                    barcode=box,
                    container_type="BOX",
                    status="ACTIVE",
                )
                if hasattr(Container, "name"):
                    kwargs["name"] = box
                cont = Container(**kwargs)
                new_containers.append(cont)
                created_containers += 1
            container_map[box] = cont
        if new_containers:
            db.add_all(new_containers)

        db.flush()
        from app.services import box_routes
        imported_sides={r['box']:r['row_on_pallet'] for r in rows if r.get('row_on_pallet')}
        for box_code, cont in container_map.items():
            side=imported_sides.get(box_code)
            if side is not None:
                box_routes.set_box_route(db,container_id=cont.id,client_id=client_id,warehouse_id=warehouse_id,
                    row_on_pallet=int(side),actor=receipt.get('performed_by'),reason='Структурная приёмка '+receipt_id)

        # Batch-resolve SKUs and cached WB identities. This removes two SQL queries per
        # unique barcode from the hot receiving path.
        barcode_meta: dict[str, dict] = {}
        for row in rows:
            qty = row.get("quantity")
            raw_barcode = str(row.get("barcode") or "").strip()
            if row.get("is_empty_box") or qty is None or qty <= 0 or not raw_barcode:
                continue
            normalized, _duplicated_from = normalize_product_barcode(raw_barcode)
            if normalized:
                barcode_meta.setdefault(normalized, row)
        barcode_list = list(barcode_meta)
        sku_cache: dict[str, SKU] = {}
        sku_code_candidates: dict[str, SKU] = {}
        for start_index in range(0, len(barcode_list), 3000):
            chunk = barcode_list[start_index:start_index + 3000]
            chunk_codes = [f"WB-{code}" for code in chunk]
            found_skus = db.execute(select(SKU).where(
                SKU.client_id == client_id,
                or_(SKU.barcode.in_(chunk), SKU.sku_code.in_(chunk_codes)),
            )).scalars().all()
            for sku in found_skus:
                if sku.barcode and str(sku.barcode).strip() in barcode_meta:
                    sku_cache.setdefault(str(sku.barcode).strip(), sku)
                sku_code_candidates.setdefault(str(sku.sku_code or ""), sku)

        wb_identities = wb_product_identity_map(db, client_id, set(barcode_list)) if barcode_list else {}
        new_skus: list[SKU] = []
        for barcode in barcode_list:
            row = barcode_meta[barcode]
            sku = sku_cache.get(barcode)
            if sku is None:
                legacy = sku_code_candidates.get(f"WB-{barcode}")
                if legacy is not None:
                    legacy_barcode = str(legacy.barcode or "").strip()
                    if legacy_barcode and legacy_barcode != barcode:
                        raise HTTPException(
                            status_code=409,
                            detail=f"SKU {legacy.sku_code} уже связан с другим ШК {legacy_barcode}; строка {row.get('row_number')}",
                        )
                    legacy.barcode = barcode
                    sku = legacy
            product = wb_identities.get(barcode)
            supplied_article = str(row.get("article") or "").strip() or None
            supplied_size = str(row.get("size") or "").strip() or None
            if sku is not None:
                if product:
                    sku.article = product.get("article") or sku.article
                    sku.name = product.get("name") or sku.name
                    sku.size = product.get("size") or supplied_size or sku.size
                else:
                    if supplied_article and (not sku.article or sku.article in {sku.barcode, sku.sku_code}):
                        sku.article = supplied_article
                    if supplied_article and (not sku.name or sku.name in {sku.barcode, sku.sku_code, sku.article}):
                        sku.name = supplied_article
                    if supplied_size and not sku.size:
                        sku.size = supplied_size
                sku.is_active = True
            else:
                resolved_article = (product or {}).get("article") or supplied_article or barcode
                resolved_name = (product or {}).get("name") or resolved_article
                resolved_size = (product or {}).get("size") or supplied_size
                sku = SKU(
                    id=uuid.uuid4(),
                    client_id=client_id,
                    sku_code=f"WB-{barcode}",
                    article=resolved_article,
                    name=resolved_name,
                    size=resolved_size,
                    barcode=barcode,
                    unit="PCS",
                    is_active=True,
                )
                new_skus.append(sku)
            sku_cache[barcode] = sku
        if new_skus:
            db.add_all(new_skus)

        # Flush all new hierarchy/box/SKU rows in one batch so the bulk inventory path can
        # validate them by ID inside the same transaction.
        db.flush()

        stock_lines: list[StockLine] = []
        pending: dict[str, int] = {}
        total_quantity = Decimal("0")
        for row in rows:
            qty = row.get("quantity")
            barcode = str(row.get("barcode") or "").strip()
            if row.get("is_empty_box") or qty is None or qty <= 0 or not barcode:
                continue
            normalized, _duplicated_from = normalize_product_barcode(barcode)
            cache_key = normalized or barcode
            sku = sku_cache.get(cache_key)
            if sku is None:
                # All SKUs were resolved in the batch above. Falling back to the old
                # per-row lookup here would re-introduce thousands of SQL round-trips.
                raise HTTPException(
                    status_code=409,
                    detail=f"Не удалось подготовить SKU для ШК {barcode}; строка {row.get('row_number')}",
                )
            cont = container_map[row["box"]]
            loc = location_map[(row["section"], row["pallet"])]
            stock_lines.append(StockLine(
                sku_id=sku.id,
                quantity=Decimal(qty),
                destination_location_id=loc.id,
                destination_container_id=cont.id,
                reason=f"STRUCTURED_RECEIPT:{receipt_id}",
            ))
            normalized_barcode = str(sku.barcode or barcode)
            pending[normalized_barcode] = pending.get(normalized_barcode, 0) + int(Decimal(qty))
            total_quantity += Decimal(qty)

        from app.services import stock_actions as stock
        additions={}
        for line in stock_lines:
            key=(str(line.sku_id),str(line.destination_location_id),str(line.destination_container_id))
            additions[key]=additions.get(key,Decimal(0))+Decimal(line.quantity)
        stock.locks(db,str(client_id),str(warehouse_id),[k[0] for k in additions])
        before={key:stock.bucket(db,client_id,warehouse_id,*key) for key in sorted(additions)}
        operation = None
        if stock_lines:
            operation = execute_operation(db, OperationCreate(
                client_id=client_id,
                warehouse_id=warehouse_id,
                operation_type="RECEIVING_IMPORT",
                idempotency_key=f"structured-receipt-{receipt_id}",
                performed_by=str(receipt.get("performed_by") or "").strip() or None,
                reference=str(receipt.get("reference") or receipt.get("name") or "").strip() or None,
                comment=(
                    f"Структурная Excel-приёмка · {receipt.get('section_count', 0)} секц. · "
                    f"{receipt.get('pallet_count', 0)} палет · {receipt.get('box_count', 0)} коробов"
                ),
                lines=stock_lines,
            ), commit=False)

        for key,qty in additions.items():
            prior=before[key]
            actual=stock.bucket(db,client_id,warehouse_id,*key)
            if actual['physical']!=prior['physical']+qty:
                raise HTTPException(409,detail='Приёмка: количество изменилось одновременно. Операция отменена')
            stock.save_mirror(db,client_id,warehouse_id,prior,actual['physical'],prior['main']+(0 if receipt_fbs else qty))
        db.execute(text("INSERT INTO fc_structured_receipt_policy(receipt_id,stock_class) VALUES(:id,:class) ON CONFLICT(receipt_id) DO NOTHING"),
            {'id':receipt_id,'class':'FBS' if receipt_fbs else 'MAIN'})
        operation_id = str(getattr(operation, "id", "") or "") or None
        db.execute(text("""
            UPDATE fc_structured_receipts
            SET status='COMMITTED', committed_at=NOW(), committed_operation_id=:operation_id, error_count=:error_count
            WHERE id=:id
        """), {"id": receipt_id, "operation_id": operation_id, "error_count": len(skipped_error_rows)})
        db.execute(text("""
            UPDATE fc_structured_receipt_rows
            SET validation_status='COMMITTED'
            WHERE receipt_id=:id AND validation_status <> 'ERROR'
        """), {"id": receipt_id})
        db.commit()

        pending_lines = [
            {
                "barcode": barcode, "quantity": quantity, "mode": "ADD",
                "idempotency_key": _wb_pending_key(f"structured:{receipt_id}", barcode),
            }
            for barcode, quantity in pending.items()
        ]
        detail = _structured_receipt_detail(db, receipt_id)
        detail.update({
            "ok": True,
            "created_sections": created_sections,
            "created_locations": created_locations,
            "created_containers": created_containers,
            "reused_containers": reused_containers,
            "rows_received": len(stock_lines),
            "quantity_received": float(total_quantity),
            "skipped_error_rows": len(skipped_error_rows),
            "skipped_rows": [
                {
                    "row": int(row.get("row_number") or 0),
                    "barcode": str(row.get("barcode") or ""),
                    "error": "; ".join(row.get("errors") or []),
                }
                for row in skipped_error_rows[:100]
            ],
            "pending_lines": pending_lines if receipt_fbs else [],
            "wb_pending": bool(pending_lines) and receipt_fbs,
            "stock_class": "FBS" if receipt_fbs else "MAIN",
            "note": (
                "Корректные строки добавлены в локальный склад FulfillCore. "
                f"Пропущено строк с ошибками: {len(skipped_error_rows)}. WB/Ozon не менялись автоматически."
            ),
        })
        return detail


@router.get('/sales-stop')
def sales_stop(client_id: uuid.UUID, warehouse_id: uuid.UUID, db: Session=Depends(get_db)):
    from app.services.wb_sales_control import global_paused
    return {'global_paused':global_paused(db,client_id,warehouse_id)}
