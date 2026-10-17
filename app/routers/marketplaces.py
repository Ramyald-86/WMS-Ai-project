# FC_REVIEW_20260905_BOUNDED_SYNC_SCOPED_CACHE
import base64
from collections import defaultdict, deque
from contextlib import nullcontext
import html
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from PIL import Image, ImageDraw, ImageFont
from reportlab.graphics.barcode.qr import QrCodeWidget
from sqlalchemy import Text, bindparam, case, cast, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from app.core.crypto import decrypt_secret, encrypt_secret
from app.core.database import get_db
from app.models import (
    Container,
    InventoryBalance,
    Location,
    MarketplaceCredential,
    MarketplaceOrder,
    MarketplaceOrderEvent,
    MarketplacePickingList,
    MarketplaceProduct,
    PrintJob,
    SKU,
)
from app.schemas.inventory import OperationCreate, StockLine
from app.services.inventory import execute_operation

from app.schemas.integrations import (
    MarketplaceCredentialCreate,
    MarketplaceCredentialRead,
    OzonLabelsRequest,
    OzonShipRequest,
    WBAddOrders,
    WBBoxesRequest,
    WBBoxLabelsRequest,
    WBCreateSupply,
    WBDeleteCHZRequest,
    WBOrderLabelsRequest,
    WBScanCHZRequest,
    WBScanProductRequest,
    WBSyncRequest,
    WBPickingListRequest,
    WBQueuePrintRequest,
    WBWarehouseSettingsUpdate,
    WBProductImportRequest,
    WBWorkflowResetRequest,
    WBShipPackedRequest,
    WBMetaRefreshRequest,
)
from app.services.marketplaces import OzonClient, WBClient, shared_ozon_client, shared_wb_client
from app.services.pdf_labels import png_pages_to_pdf, product_barcode_label_pdf, print_calibration_pdf
from app.services.raster_labels import product_barcode_label_png, wb_sticker_exact_png, print_calibration_png, supply_box_label_png
from app.services.picking_lists import allocate_picking_rows, picking_list_xlsx
from app.services.printing import queue_print_job
from app.services.runtime_reliability import (
    SYSTEM_LOCATION_CODES,
    acquire_packing_lease,
    assert_or_renew_packing_lease,
    claim_runtime_interval,
    distributed_lock,
    process_lock,
    normalize_station_code,
    release_packing_lease,
)
from app.services.stock_core_v2 import (
    effective_fbs_free_for_bucket, marketplace_order_age_hours,
    recover_marketplace_packing_write_off, release_or_restore_marketplace_reservation,
)
from app.services.stock_reservations import (
    active_reserved_totals,
    assert_order_reservation_ready_for_packing,
    ensure_packing_reservation_for_scan,
    cancel_order_to_receiving,
    consume_order_reservation,
    finalize_order_stock_for_packing,
    receiving_queue,
    ensure_stock_reservation_schema,
    latest_reservations_for_orders,
    release_order_reservation,
    reservation_row_for_picking,
    reserve_new_orders_sale_first,
    reserve_orders_from_picking_rows,
)
from app.services.wb_supply_registry import (
    list_wb_supply_registry,
    registry_supply,
    set_wb_supply_error,
    upsert_wb_supply,
)

# FC_CHZ_PIPELINE_PINNED_TO_0_9_4_9E_TRANSMIT_PRINT
router = APIRouter(prefix="/marketplaces", tags=["Маркетплейсы"])
# 0.9.4.7 DB TX AUTO LABEL STABILITY
# 0.9.4.4 PACKING TRANSACTION FIX
logger = logging.getLogger(__name__)

def _wb_order_write_lock_key(cred: MarketplaceCredential) -> str:
    """Serialize writes across rotated logical WB credentials of one client.

    Old API-key rows can still be used by background metadata/reservation workers.
    Locking by credential id allowed two writers of the same marketplace_orders rows
    to run concurrently after key rotation.  Client scope closes that deadlock window
    without serializing read-only order tabs.
    """
    return f"wb-order-write-client:{cred.client_id}"


_CHZ_TRACE_RECENT: deque[dict[str, Any]] = deque(maxlen=100)
_CHZ_TRACE_LOCK = threading.Lock()

def _chz_trace_stage(timing: dict[str, Any] | None, stage: str, started: float, *, order: MarketplaceOrder | None = None, outcome: str = "ok") -> float:
    elapsed = round((time.perf_counter() - started) * 1000.0, 1)
    if timing is not None:
        timing[stage] = round(float(timing.get(stage) or 0.0) + elapsed, 1)
        trace_id = str(timing.get("trace_id") or "-")
    else:
        trace_id = "-"
    if elapsed >= 2000.0:
        logger.warning(
            "FC_WB_TRACE_SLOW trace_id=%s order_id=%s stage=%s elapsed_ms=%s outcome=%s supplier_status=%s supply_id=%s",
            trace_id, getattr(order, "id", None), stage, elapsed, outcome,
            getattr(order, "supplier_status", None), getattr(order, "supply_id", None),
        )
    else:
        logger.info(
            "FC_WB_TRACE trace_id=%s order_id=%s stage=%s elapsed_ms=%s outcome=%s",
            trace_id, getattr(order, "id", None), stage, elapsed, outcome,
        )
    return elapsed

def _record_chz_trace_summary(order: MarketplaceOrder, timing: dict[str, Any]) -> None:
    row = {
        "trace_id": timing.get("trace_id"),
        "order_id": str(order.id),
        "total_ms": float(timing.get("total_ms") or 0.0),
        "wb_total_ms": float(timing.get("wb_total_ms") or 0.0),
        "wb_set_sgtin_ms": float(timing.get("wb_set_sgtin_ms") or 0.0),
        "wb_status_ms": float(timing.get("wb_status_ms") or 0.0),
        "wb_meta_ms": float(timing.get("wb_meta_ms") or 0.0),
    }
    with _CHZ_TRACE_LOCK:
        _CHZ_TRACE_RECENT.append(row)
        vals = sorted(float(x.get("wb_total_ms") or 0.0) for x in _CHZ_TRACE_RECENT)
        count = len(vals)
        if not count:
            return
        def pct(p: float) -> float:
            idx = max(0, min(count - 1, int(round((count - 1) * p))))
            return round(vals[idx], 1)
        avg = round(sum(vals) / count, 1)
        slow2 = sum(1 for v in vals if v >= 2000.0)
        slow5 = sum(1 for v in vals if v >= 5000.0)
        maximum = round(max(vals), 1)
    logger.warning(
        "FC_WB_TRACE_SUMMARY samples=%s avg_wb_ms=%s p50_wb_ms=%s p95_wb_ms=%s max_wb_ms=%s over_2s=%s over_5s=%s last_trace_id=%s",
        count, avg, pct(0.50), pct(0.95), maximum, slow2, slow5, timing.get("trace_id") or "-",
    )


def _timing_add_ms(target: dict[str, Any] | None, key: str, started: float) -> None:
    if target is None:
        return
    target[key] = round(float(target.get(key) or 0.0) + (time.perf_counter() - started) * 1000.0, 1)


def _finish_chz_timing(db: Session, order: MarketplaceOrder, timing: dict[str, Any], *, station: str | None, outcome: str) -> dict[str, Any]:
    timing["total_ms"] = round((time.perf_counter() - float(timing.pop("_started"))) * 1000.0, 1)
    timing["outcome"] = outcome
    timing["measured_at"] = _now().isoformat()
    timing["station_code"] = station
    raw = dict(order.raw_data or {})
    raw["_fc_chz_last_timing"] = timing
    order.raw_data = raw
    _event(db, order, "CHZ_TIMING", data=timing)
    logger.warning(
        "FC_CHZ_TIMING trace_id=%s order_id=%s station=%s outcome=%s total_ms=%s db_precheck_ms=%s local_save_ms=%s wb_total_ms=%s wb_set_sgtin_ms=%s wb_confirm_ms=%s wb_status_ms=%s wb_meta_ms=%s wb_reshipment_ms=%s finalize_ms=%s",
        timing.get("trace_id") or "-", order.id, station or "-", outcome, timing.get("total_ms"), timing.get("db_precheck_ms", 0), timing.get("local_save_ms", 0),
        timing.get("wb_total_ms", 0), timing.get("wb_set_sgtin_ms", 0), timing.get("wb_confirm_ms", 0), timing.get("wb_status_ms", 0),
        timing.get("wb_meta_ms", 0), timing.get("wb_reshipment_ms", 0), timing.get("finalize_ms", 0),
    )
    _record_chz_trace_summary(order, timing)
    return timing

WB_STATUS_NAMES = {
    "waiting": "В работе WB",
    "sorted": "Отсортирован WB",
    "sold": "Получен покупателем",
    "canceled": "Отменён",
    "canceled_by_client": "Отменён покупателем при получении",
    "declined_by_client": "Отменён покупателем",
    "defect": "Отменён из-за брака",
    "ready_for_pickup": "Готов к получению в ПВЗ",
    "accepted_by_carrier": "Передан перевозчику",
    "sent_to_carrier": "Отправлен перевозчику",
    "postponed_delivery": "Доставка перенесена",
    "canceled_by_carrier": "Отменён перевозчиком",
}
LOCAL_STATUS_NAMES = {
    "NEW": "Новый",
    "PICKING": "На сборке",
    "PACKED": "Упаковано",
    "SENT": "Доставляется",
    "DELIVERED": "Получен покупателем",
    "CANCELLED": "Отменён",
}
CANCEL_WB_STATUSES = {
    "canceled", "cancelled", "cancel",
    "canceled_by_client", "cancelled_by_client",
    "declined_by_client", "defect",
    "canceled_by_carrier", "cancelled_by_carrier",
    "canceled_by_seller", "cancelled_by_seller",
}
CANCEL_SUPPLIER_STATUSES = {
    "cancel", "cancel_carrier", "canceled", "cancelled",
}

# WB statuses that prove the item has physically left the seller warehouse.
# Supply membership / supplierStatus=confirm|complete / WB sticker / CHZ alone are NOT proof.
# Keep aliases broad because WB has used slightly different names in different feeds.
WB_PHYSICALLY_LEFT_STATUSES = {
    # Official WB state: the order has already been sorted on the WB side.
    # For our warehouse this is sufficient proof that the parcel physically left us.
    "sorted",
    "accepted_by_carrier",
    "sent_to_carrier",
    "ready_for_pickup",
    "postponed_delivery",
    "in_transit",
    "in_delivery",
    "delivering",
    "delivery",
    "on_way",
    "on_the_way",
    "at_pickup_point",
    "arrived_at_pickup_point",
    "return",
    "returned",
    "returning",
    "return_to_seller",
    "return_to_warehouse",
}

# Browser clients can be opened on several warehouse PCs at once. Keep automatic
# WB synchronization globally throttled inside this application process so those
# PCs do not multiply marketplace API traffic. Manual sync may bypass the long
# interval, but still cannot stampede the API.
_WB_SYNC_LOCK = threading.Lock()

# One lightweight WB supply per running API process for orders that must be moved
# from supplierStatus=new to confirm before SGTIN can be attached. WB requires
# confirm for /meta/sgtin. Keeping a small cache avoids creating a supply per scan.
_WB_CHZ_SUPPLY_LOCK = threading.Lock()
_WB_CHZ_SUPPLY_CACHE: dict[tuple[str, str, str], str] = {}
_WB_LAST_SYNC: dict[str, float] = {}
_WB_AUTO_SYNC_INTERVAL = 20.0
_WB_FORCE_SYNC_INTERVAL = 3.0
_WB_LAST_HISTORY_SYNC: dict[str, float] = {}
_WB_HISTORY_SYNC_INTERVAL = 300.0
_WB_LAST_META_SWEEP: dict[str, float] = {}
# FC_K9R54_META_SWEEP_DEDUP: visible rows refresh every minute and the progressive
# workspace reconciler covers the rest. The broad 300-row sweep is therefore a
# five-minute safety pass instead of a duplicate every auto-sync.
_WB_META_SWEEP_INTERVAL = 300.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _credential(db: Session, credential_id: uuid.UUID, provider: str | None = None) -> MarketplaceCredential:
    cred = db.get(MarketplaceCredential, credential_id)
    if not cred or not cred.is_active:
        raise HTTPException(status_code=404, detail="Подключение маркетплейса не найдено")
    if provider and cred.provider != provider:
        raise HTTPException(status_code=400, detail=f"Выбрано подключение {cred.provider}, а требуется {provider}")
    return cred




def _logical_credentials(db: Session, cred: MarketplaceCredential) -> list[MarketplaceCredential]:
    """All DB rows that represent the same visible marketplace cabinet.

    API keys may be disconnected/reconnected over the lifetime of a warehouse.
    Orders and product cards are business history and must remain visible regardless
    of which credential row originally imported them.
    """
    rows = db.execute(select(MarketplaceCredential).where(
        MarketplaceCredential.client_id == cred.client_id,
        MarketplaceCredential.provider == cred.provider,
        MarketplaceCredential.name == cred.name,
    ).order_by(MarketplaceCredential.created_at.asc())).scalars().all()
    if not any(x.id == cred.id for x in rows):
        rows.append(cred)
    return rows


def _logical_credential_ids(db: Session, cred: MarketplaceCredential) -> list[uuid.UUID]:
    return [x.id for x in _logical_credentials(db, cred)]

def _packing_credential_ids_for_scan(db: Session, cred: MarketplaceCredential) -> list[uuid.UUID]:
    """Credential lineage used only by the hot packing scanner.

    Display names are not an order identity. When this client/provider has exactly one
    active WB connection, older disconnected credential rows are treated as historical
    API-key generations of the same working cabinet for scanning only. We deliberately
    do NOT bulk-migrate historical orders here: the one order actually scanned is adopted
    by the current credential, keeping this hot path short and avoiding a large DB write.

    If several WB credentials are active for the same client, fall back to the existing
    same-name lineage so separate seller cabinets are never mixed automatically.
    """
    rows = db.execute(
        select(MarketplaceCredential).where(
            MarketplaceCredential.client_id == cred.client_id,
            MarketplaceCredential.provider == cred.provider,
        ).order_by(MarketplaceCredential.created_at.asc())
    ).scalars().all()
    active = [row for row in rows if bool(row.is_active)]
    if len(active) == 1 and active[0].id == cred.id:
        return [row.id for row in rows]
    ids = [row.id for row in rows if row.id == cred.id or row.name == cred.name]
    return ids or [cred.id]

def _merge_order_history(db: Session, target: MarketplaceOrder, source: MarketplaceOrder) -> None:
    """Merge a historical order from an older disconnected API credential.

    Reconnecting the same WB cabinet must never make already synchronized orders,
    marking codes, stickers or picking history disappear from the warehouse UI.
    """
    scalar_fields = [
        "order_uid", "article", "wb_warehouse_id", "cargo_type", "local_sku_id",
        "supplier_status", "wb_status", "is_cancellable", "supply_id",
        "product_scanned_barcode", "product_scanned_at", "chz_code", "chz_attached_at",
        "label_generated_at", "assembled_at", "picking_list_generated_at",
        "last_picking_list_id", "packed_at", "external_created_at", "last_synced_at",
    ]
    for name in scalar_fields:
        if getattr(target, name, None) in (None, "", [], {}) and getattr(source, name, None) not in (None, "", [], {}):
            setattr(target, name, getattr(source, name))
    if source.barcodes and not target.barcodes:
        target.barcodes = list(source.barcodes)
    if source.required_meta and not target.required_meta:
        target.required_meta = list(source.required_meta)
    if source.optional_meta and not target.optional_meta:
        target.optional_meta = list(source.optional_meta)
    if getattr(source, "manual_status_override", False) and not getattr(target, "manual_status_override", False):
        setattr(target, "manual_status_override", True) if hasattr(target, "manual_status_override") else None
        target.local_status = source.local_status
    elif target.local_status == "NEW" and source.local_status in {"PICKING", "PACKED", "SENT", "DELIVERED", "CANCELLED"}:
        target.local_status = source.local_status
    target.label_print_count = max(int(target.label_print_count or 0), int(source.label_print_count or 0))
    raw = dict(source.raw_data or {})
    raw.update(target.raw_data or {})
    target.raw_data = raw
    for ev in db.execute(select(MarketplaceOrderEvent).where(MarketplaceOrderEvent.marketplace_order_id == source.id)).scalars().all():
        ev.marketplace_order_id = target.id


_CONSOLIDATE_HISTORY_LOCK = threading.Lock()
_CONSOLIDATE_HISTORY_LAST: dict[str, float] = {}
_CONSOLIDATE_HISTORY_INTERVAL = 300.0

def _consolidate_credential_history(db: Session, cred: MarketplaceCredential) -> None:
    """Adopt old credential history, but never rescan it on every browser poll.

    0.9.4.9 SPEED: several workstations request the order board every few seconds. The
    historical merge used to walk old orders/products repeatedly and could dominate DB
    time. One API process now performs it at most once per cabinet every five minutes.
    The first caller owns the maintenance pass; concurrent callers continue immediately.
    """
    cache_key = str(cred.id)
    now_mono = time.monotonic()
    with _CONSOLIDATE_HISTORY_LOCK:
        last = float(_CONSOLIDATE_HISTORY_LAST.get(cache_key) or 0.0)
        if now_mono - last < _CONSOLIDATE_HISTORY_INTERVAL:
            return
        _CONSOLIDATE_HISTORY_LAST[cache_key] = now_mono
    try:
        legacy = [x for x in _logical_credentials(db, cred) if x.id != cred.id]
    except Exception:
        with _CONSOLIDATE_HISTORY_LOCK:
            _CONSOLIDATE_HISTORY_LAST.pop(cache_key, None)
        raise
    if not legacy:
        return
    changed = False
    if not (cred.warehouse_settings or {}):
        source_settings = next((x.warehouse_settings for x in reversed(legacy) if x.warehouse_settings), None)
        if source_settings:
            cred.warehouse_settings = dict(source_settings)
            changed = True
    for old in legacy:
        old_orders = db.execute(select(MarketplaceOrder).where(MarketplaceOrder.credential_id == old.id)).scalars().all()
        for old_order in old_orders:
            current = db.execute(select(MarketplaceOrder).where(
                MarketplaceOrder.credential_id == cred.id,
                MarketplaceOrder.provider == old_order.provider,
                MarketplaceOrder.external_order_id == old_order.external_order_id,
            )).scalars().first()
            if current:
                _merge_order_history(db, current, old_order)
                db.delete(old_order)
            else:
                old_order.credential_id = cred.id
            changed = True
        for plist in db.execute(select(MarketplacePickingList).where(MarketplacePickingList.credential_id == old.id)).scalars().all():
            plist.credential_id = cred.id
            changed = True
        old_products = db.execute(select(MarketplaceProduct).where(MarketplaceProduct.credential_id == old.id)).scalars().all()
        for old_product in old_products:
            current = db.execute(select(MarketplaceProduct).where(
                MarketplaceProduct.credential_id == cred.id,
                MarketplaceProduct.provider == old_product.provider,
                MarketplaceProduct.external_product_id == old_product.external_product_id,
            )).scalars().first()
            if current:
                for name in ("vendor_code", "title", "description", "brand", "subject_name", "local_sku_id", "last_synced_at"):
                    if getattr(current, name, None) in (None, "") and getattr(old_product, name, None) not in (None, ""):
                        setattr(current, name, getattr(old_product, name))
                if not current.photos and old_product.photos:
                    current.photos = old_product.photos
                if not current.sizes and old_product.sizes:
                    current.sizes = old_product.sizes
                if not current.barcodes and old_product.barcodes:
                    current.barcodes = old_product.barcodes
                raw = dict(old_product.raw_data or {})
                raw.update(current.raw_data or {})
                current.raw_data = raw
                db.delete(old_product)
            else:
                old_product.credential_id = cred.id
            changed = True
    if changed:
        db.flush()
        db.commit()


def _wb(db: Session, credential_id: uuid.UUID) -> tuple[MarketplaceCredential, WBClient]:
    cred = _credential(db, credential_id, "WB")
    _consolidate_credential_history(db, cred)
    return cred, shared_wb_client(decrypt_secret(cred.encrypted_api_key))


_WB_SUPPLY_ID_RE = re.compile(r"^WB-GI-\d+$", re.IGNORECASE)


def _local_wb_supply_barcode_png(supply_id: str) -> bytes:
    """Build a WB-supply QR locally without changing the marketplace status.

    WB exposes the official image only after the supply is transferred to delivery,
    although its documented barcode value is the supply ID itself (WB-GI-...). For an
    open supply we encode exactly that value locally, so printing never has to call
    ``deliver`` and never receives the misleading "not linked to delivery" API error.
    """
    value = str(supply_id or "").strip().upper()
    if not _WB_SUPPLY_ID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="Некорректный ID поставки WB")

    widget = QrCodeWidget(value=value, barLevel="M")
    widget.qr.make()
    modules = widget.qr.modules or []
    module_count = int(widget.qr.getModuleCount() or 0)
    if not modules or module_count <= 0:
        raise HTTPException(status_code=500, detail="Не удалось сформировать QR поставки")

    # WB returns supply codes as a 580x400 PNG. Keep the same raster ratio so the
    # existing 58x40 label/PDF/Print Agent paths can consume either image identically.
    canvas_w, canvas_h = 580, 400
    border_modules = 4
    qr_limit = 330
    module_px = max(1, qr_limit // (module_count + border_modules * 2))
    qr_px = (module_count + border_modules * 2) * module_px
    qr_x = (canvas_w - qr_px) // 2
    qr_y = 6
    image = Image.new("L", (canvas_w, canvas_h), color=255)
    draw = ImageDraw.Draw(image)
    for row_idx, row in enumerate(modules):
        for col_idx, dark in enumerate(row):
            if not dark:
                continue
            left = qr_x + (col_idx + border_modules) * module_px
            top = qr_y + (row_idx + border_modules) * module_px
            draw.rectangle((left, top, left + module_px - 1, top + module_px - 1), fill=0)

    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    try:
        font = ImageFont.truetype(str(font_path), 29) if font_path.exists() else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()
    text_box = draw.textbbox((0, 0), value, font=font)
    text_w = text_box[2] - text_box[0]
    text_h = text_box[3] - text_box[1]
    draw.text(((canvas_w - text_w) // 2, canvas_h - text_h - 12), value, fill=0, font=font)

    output = BytesIO()
    image.save(output, format="PNG", dpi=(254, 254), optimize=False)
    return output.getvalue()


def _wb_supply_barcode_png(client: WBClient, supply_id: str, *, official_available: bool) -> bytes:
    """Return official WB QR when available, otherwise the local status-safe image."""
    if official_available:
        try:
            raw = client.supply_barcode_png(supply_id)
            if raw:
                return raw
        except Exception:
            # Printing a known supply ID must stay available during a temporary WB
            # failure as well. No supply/order state is changed by this fallback.
            pass
    return _local_wb_supply_barcode_png(supply_id)

_WB_PHYSICAL_SETTINGS_KEY = "__fc_physical_warehouses__"


def _normalize_wb_settings(raw: Any) -> dict[str, dict[str, Any]]:
    """Return only seller-warehouse rows from a legacy/scoped JSON object."""
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return result
    for key, value in raw.items():
        # Reserved metadata keys must never be rendered as WB warehouses.
        if str(key).startswith("__"):
            continue
        if isinstance(value, str):
            result[str(key)] = {"mode": value}
        elif isinstance(value, dict):
            result[str(key)] = dict(value)
    return result


def _wb_physical_scopes(cred: MarketplaceCredential) -> dict[str, dict[str, Any]]:
    raw = cred.warehouse_settings or {}
    scopes = raw.get(_WB_PHYSICAL_SETTINGS_KEY) if isinstance(raw, dict) else None
    if not isinstance(scopes, dict):
        return {}
    return {str(key): value for key, value in scopes.items() if isinstance(value, dict)}


def _wb_settings(
    cred: MarketplaceCredential,
    physical_warehouse_id: uuid.UUID | str | None = None,
) -> dict[str, dict[str, Any]]:
    """WB seller-warehouse settings for one FulfillCore physical warehouse.

    Older releases stored one flat mapping on the marketplace credential. The new
    reserved container keeps a separate mapping for every physical warehouse. A new
    physical scope starts unconfigured (all WB warehouses hidden) so an old shared
    choice can never silently leak Reutov orders into Mytishchi or vice versa. Callers
    without a physical context receive a safe aggregate so old API clients keep working.
    """
    raw = cred.warehouse_settings or {}
    legacy = _normalize_wb_settings(raw)
    scopes = _wb_physical_scopes(cred)
    if physical_warehouse_id is not None:
        scoped = scopes.get(str(physical_warehouse_id))
        return _normalize_wb_settings(scoped) if scoped is not None else {}

    if not scopes:
        return legacy

    # Backward-compatible aggregate for endpoints that identify an order but do not
    # yet carry the UI physical-warehouse context. WORK wins over WATCH/HIDDEN.
    priority = {"HIDDEN": 0, "WATCH": 1, "WORK": 2}
    aggregate: dict[str, dict[str, Any]] = {}
    for scoped in scopes.values():
        for seller_id, value in _normalize_wb_settings(scoped).items():
            current = aggregate.get(seller_id)
            current_mode = str((current or {}).get("mode") or "HIDDEN").upper()
            next_mode = str(value.get("mode") or "HIDDEN").upper()
            if current is None or priority.get(next_mode, 0) > priority.get(current_mode, 0):
                aggregate[seller_id] = dict(value)
    for seller_id, value in legacy.items():
        aggregate.setdefault(seller_id, dict(value))
    return aggregate


def _wb_has_physical_scope(cred: MarketplaceCredential, physical_warehouse_id: uuid.UUID | str | None) -> bool:
    return physical_warehouse_id is not None and str(physical_warehouse_id) in _wb_physical_scopes(cred)


def _wb_catalog_settings(
    cred: MarketplaceCredential,
    physical_warehouse_id: uuid.UUID | str | None,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Return a displayable cached catalogue without leaking another warehouse's modes.

    A newly selected physical warehouse has no own WORK/WATCH choices yet. It still
    needs to see the known WB seller warehouses when the live WB endpoint is down, but
    every inherited catalogue row must start as HIDDEN until the user saves this scope.
    """
    exact = _wb_settings(cred, physical_warehouse_id)
    if exact:
        configured = physical_warehouse_id is None or _wb_has_physical_scope(cred, physical_warehouse_id)
        return exact, configured

    if physical_warehouse_id is None:
        return {}, False

    aggregate = _wb_settings(cred)
    catalogue: dict[str, dict[str, Any]] = {}
    for seller_id, value in aggregate.items():
        row = dict(value)
        row["mode"] = "HIDDEN"
        catalogue[seller_id] = row
    return catalogue, False


def _wb_saved_warehouse_rows(settings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Render cached seller warehouses and ignore any malformed historical keys."""
    rows: list[dict[str, Any]] = []
    for seller_id, value in settings.items():
        try:
            numeric_id = int(seller_id)
        except (TypeError, ValueError):
            continue
        rows.append({
            "id": numeric_id,
            "name": value.get("name") or f"Склад {seller_id}",
            "office_id": value.get("office_id"),
            "cargo_type": value.get("cargo_type"),
            "delivery_type": value.get("delivery_type", 1),
            "is_deleting": value.get("is_deleting"),
            "is_processing": value.get("is_processing"),
            "mode": str(value.get("mode") or "HIDDEN").upper(),
        })
    rows.sort(key=lambda x: (x["mode"] == "HIDDEN", x["name"].lower()))
    return rows


def _store_wb_settings(
    cred: MarketplaceCredential,
    settings: dict[str, dict[str, Any]],
    physical_warehouse_id: uuid.UUID | str | None = None,
) -> None:
    if physical_warehouse_id is None:
        # Compatibility path for an old cached frontend that has not sent warehouse_id.
        cred.warehouse_settings = settings
        return
    raw = dict(cred.warehouse_settings or {})
    existing_scopes = raw.get(_WB_PHYSICAL_SETTINGS_KEY)
    scopes = dict(existing_scopes) if isinstance(existing_scopes, dict) else {}
    scopes[str(physical_warehouse_id)] = settings
    raw[_WB_PHYSICAL_SETTINGS_KEY] = scopes
    cred.warehouse_settings = raw


def _wb_mode(
    cred: MarketplaceCredential,
    warehouse_id: int | None,
    physical_warehouse_id: uuid.UUID | str | None = None,
) -> str:
    settings = _wb_settings(cred, physical_warehouse_id)
    if not settings:
        # A physical workspace must be configured explicitly. Only legacy callers that
        # do not send the physical warehouse retain the old permissive behaviour.
        return "HIDDEN" if physical_warehouse_id is not None else "WORK"
    if warehouse_id is None:
        return "HIDDEN"
    return str((settings.get(str(warehouse_id)) or {}).get("mode") or "HIDDEN").upper()


def _wb_visible(
    cred: MarketplaceCredential,
    warehouse_id: int | None,
    physical_warehouse_id: uuid.UUID | str | None = None,
) -> bool:
    return _wb_mode(cred, warehouse_id, physical_warehouse_id) in {"WORK", "WATCH"}


def _wb_order_warehouse_values(item: dict | None) -> tuple[int | None, int | None]:
    item = item or {}
    wid = item.get("warehouseId") or item.get("warehouseID") or item.get("wb_warehouse_id")
    office = item.get("officeID") or item.get("officeId") or item.get("office_id")
    try: wid = int(wid) if wid is not None else None
    except (TypeError, ValueError): wid = None
    try: office = int(office) if office is not None else None
    except (TypeError, ValueError): office = None
    return wid, office


def _wb_order_matches_selected_warehouse(cred: MarketplaceCredential, item: dict, selected_id: int) -> bool:
    """Match an FBS order strictly by the seller warehouseId returned by WB.

    `warehouseId` in FBS orders is the seller warehouse ID. `officeId` is the WB office
    currently linked to that seller warehouse and can legitimately change when the seller
    rebinds the warehouse (for example SML -> LRG+ -> another SML). Therefore officeId must
    never be used as an identity fallback: doing so can mix two logical seller warehouses.
    """
    wid, _office = _wb_order_warehouse_values(item)
    return wid is not None and int(wid) == int(selected_id)


def _wb_selected_warehouse_id(
    cred: MarketplaceCredential,
    requested: int | None = None,
    physical_warehouse_id: uuid.UUID | str | None = None,
) -> int | None:
    """Resolve exactly one seller warehouse for the order workspace.

    The UI must never silently mix orders from the WORK warehouse and WATCH warehouses.
    If the browser has no saved selection, choose the first WORK warehouse; WATCH is
    selected only when the user explicitly opens it.
    """
    settings = _wb_settings(cred, physical_warehouse_id)
    if requested is not None and _wb_mode(cred, requested, physical_warehouse_id) in {"WORK", "WATCH"}:
        return int(requested)
    for wid, value in settings.items():
        if str(value.get("mode") or "").upper() == "WORK":
            try:
                return int(wid)
            except (TypeError, ValueError):
                continue
    for wid, value in settings.items():
        if str(value.get("mode") or "").upper() == "WATCH":
            try:
                return int(wid)
            except (TypeError, ValueError):
                continue
    return None


def _reconcile_known_workspace_statuses(db: Session, cred: MarketplaceCredential, wb_warehouse_id: int) -> int:
    """Repair local workflow statuses from facts already persisted in PostgreSQL.

    This is intentionally independent of WB availability. If an order already has a
    marking code and an order sticker, it becomes PACKED immediately (unless a user
    explicitly forced NEW/PICKING). WB `complete` alone never creates SENT.
    """
    logical_ids = _logical_credential_ids(db, cred)
    rows = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.wb_warehouse_id == wb_warehouse_id,
        MarketplaceOrder.local_status.in_(["NEW", "PICKING", "PACKED", "SENT"]),
    )).scalars().all()
    changed = 0
    for order in rows:
        if _repair_legacy_sent_status(db, order):
            changed += 1
        if _reconcile_local_status(db, order, "WORKSPACE_FACT_RECONCILE"):
            changed += 1
    if changed:
        db.commit()
    return changed


def _assert_wb_work(
    cred: MarketplaceCredential,
    order: MarketplaceOrder,
    physical_warehouse_id: uuid.UUID | str | None = None,
) -> None:
    mode = _wb_mode(cred, order.wb_warehouse_id, physical_warehouse_id)
    if mode != "WORK":
        name = (_wb_settings(cred, physical_warehouse_id).get(str(order.wb_warehouse_id)) or {}).get("name") or order.wb_warehouse_id or "неизвестный"
        raise HTTPException(status_code=409, detail=f"Склад WB «{name}» включён только для наблюдения. Рабочие действия по его заказам запрещены.")



def _ozon(db: Session, credential_id: uuid.UUID) -> tuple[MarketplaceCredential, OzonClient]:
    cred = _credential(db, credential_id, "OZON")
    if not cred.external_client_id:
        raise HTTPException(status_code=400, detail="У подключения Ozon не указан Client-Id")
    return cred, shared_ozon_client(cred.external_client_id, decrypt_secret(cred.encrypted_api_key))


def _external_error_text(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("detail") or detail)[:800]
    if detail:
        return str(detail)[:800]
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            data = response.json()
            if isinstance(data, dict):
                return str(data.get("message") or data.get("detail") or data)[:800]
        except Exception:
            pass
        try:
            if response.text:
                return str(response.text)[:800]
        except Exception:
            pass
    return str(exc or "Неизвестная ошибка")[:800]


def _ozon_post_json(client: OzonClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Use the authenticated Ozon transport already configured by the base app."""
    method = getattr(client, "_post", None)
    if not callable(method):
        raise RuntimeError("Установленный модуль Ozon не поддерживает этот метод. Установите накопительное обновление полностью.")
    response = method(path, payload)
    if isinstance(response, dict):
        data = response
    else:
        json_method = getattr(response, "json", None)
        if not callable(json_method):
            raise RuntimeError(f"Ozon вернул неподдерживаемый ответ для {path}")
        data = json_method()
    if not isinstance(data, dict):
        raise RuntimeError(f"Ozon вернул некорректный ответ для {path}")
    return data


def _ozon_delivery_values(posting: dict[str, Any]) -> tuple[int | None, int | None, str | None]:
    delivery = posting.get("delivery_method") if isinstance(posting.get("delivery_method"), dict) else {}
    warehouse = delivery.get("warehouse") if isinstance(delivery.get("warehouse"), dict) else {}

    def as_int(value: Any) -> int | None:
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    delivery_method_id = as_int(posting.get("delivery_method_id") or delivery.get("id"))
    warehouse_id = as_int(
        posting.get("warehouse_id")
        or delivery.get("warehouse_id")
        or warehouse.get("id")
        or warehouse.get("warehouse_id")
    )
    warehouse_name = (
        posting.get("warehouse_name")
        or delivery.get("warehouse_name")
        or warehouse.get("name")
    )
    return delivery_method_id, warehouse_id, str(warehouse_name) if warehouse_name else None


OZON_PACKED_STATUSES = {
    "awaiting_deliver",
    "awaiting_delivery",
    "ready_to_ship",
    "ready_for_shipment",
}


def _ozon_status(posting: dict[str, Any]) -> str:
    return str(posting.get("status") or posting.get("status_alias") or "").strip().lower()


def _ozon_carriage_id(data: dict[str, Any]) -> int | None:
    candidates: list[Any] = [data.get("carriage_id"), data.get("id")]
    result = data.get("result")
    if isinstance(result, dict):
        candidates.extend((result.get("carriage_id"), result.get("id")))
    for value in candidates:
        try:
            if value not in (None, ""):
                return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _stock_for_sku(db: Session, sku: SKU | None, warehouse_id: uuid.UUID | None) -> list[dict]:
    if not warehouse_id or not sku:
        return []
    rows = db.execute(
        select(InventoryBalance, Location, Container)
        .join(Location, Location.id == InventoryBalance.location_id)
        .outerjoin(Container, Container.id == InventoryBalance.container_id)
        .where(
            InventoryBalance.sku_id == sku.id,
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.quantity > 0,
        )
        .order_by(Location.code, Container.name, Container.barcode)
    ).all()
    container_ids = {container.id for _, _, container in rows if container}
    container_totals: dict[uuid.UUID, float] = {}
    if container_ids:
        container_totals = {
            container_id: float(quantity or 0)
            for container_id, quantity in db.execute(
                select(
                    InventoryBalance.container_id,
                    func.coalesce(func.sum(InventoryBalance.quantity), 0),
                )
                .where(
                    InventoryBalance.warehouse_id == warehouse_id,
                    InventoryBalance.container_id.in_(container_ids),
                    InventoryBalance.quantity > 0,
                )
                .group_by(InventoryBalance.container_id)
            ).all()
        }
    reserved_map = active_reserved_totals(
        db, client_id=sku.client_id, warehouse_id=warehouse_id, sku_ids=[sku.id]
    )
    result = []
    for balance, loc, container in rows:
        reserved = float(reserved_map.get((str(sku.id), str(loc.id), str(container.id) if container else ""), 0))
        physical = float(balance.quantity)
        fbs_free = float(effective_fbs_free_for_bucket(
            db, client_id=sku.client_id, warehouse_id=warehouse_id, sku_id=sku.id,
            location_id=loc.id, container_id=container.id if container else None,
        ))
        result.append({
            "location_id": str(loc.id),
            "location": loc.code,
            "location_name": loc.name,
            "location_type": loc.location_type,
            "container_id": str(container.id) if container else None,
            "container": (container.name or container.barcode) if container else None,
            "container_barcode": container.barcode if container else None,
            "container_total_quantity": container_totals.get(container.id, 0.0) if container else 0.0,
            "quantity": physical,
            "reserved_quantity": reserved,
            "free_quantity": max(0.0, fbs_free),
            "fbs_available": fbs_free,
        })
    return result


def _norm_identity(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip()).upper()


def _wb_order_source_barcodes(order: MarketplaceOrder) -> set[str]:
    """Return every barcode carried by the WB order payload itself.

    Older/newer WB payload revisions do not always populate the ORM `barcodes`
    projection identically.  Picking must therefore accept the normalized local
    projection plus the equivalent raw fields, but never a barcode from another
    characteristic merely because the article is the same.
    """
    values: list[Any] = list(order.barcodes or [])
    raw = order.raw_data if isinstance(order.raw_data, dict) else {}
    for key in ("skus", "barcodes", "barcode", "sku"):
        value = raw.get(key)
        if isinstance(value, (list, tuple, set)):
            values.extend(value)
        elif value not in (None, ""):
            values.append(value)
    return {_norm_identity(x) for x in values if _norm_identity(x)}


def _wb_raw_order_size(order: MarketplaceOrder) -> str:
    raw = order.raw_data if isinstance(order.raw_data, dict) else {}
    for key in ("techSize", "wbSize", "size", "sizeName", "size_name"):
        value = str(raw.get(key) or "").strip()
        if value:
            return value
    return ""


def _wb_order_card_size_row(order: MarketplaceOrder, product_card: MarketplaceProduct | None) -> dict[str, Any] | None:
    """Resolve the concrete WB size row without article-only guessing.

    Priority is characteristic id -> any order barcode -> exact human size.  The
    last fallback is safe only when exactly one card row has that exact size.
    """
    if not product_card:
        return None
    raw = order.raw_data or {}
    raw_chrt = raw.get("chrtId") or raw.get("chrtID") or raw.get("chrt_id")
    order_codes = _wb_order_source_barcodes(order)
    rows = [x for x in (product_card.sizes or []) if isinstance(x, dict)]
    if raw_chrt is not None:
        for row in rows:
            if str(row.get("chrtID") or row.get("chrtId") or row.get("chrt_id") or "") == str(raw_chrt):
                return row
    if order_codes:
        for row in rows:
            row_codes = {_norm_identity(x) for x in (row.get("skus") or []) if _norm_identity(x)}
            if order_codes & row_codes:
                return row
    raw_size = _norm_identity(_wb_raw_order_size(order))
    if raw_size:
        matched = [
            row for row in rows
            if _norm_identity(row.get("techSize") or row.get("wbSize") or row.get("size")) == raw_size
        ]
        if len(matched) == 1:
            return matched[0]
    return None


def _wb_order_allowed_barcodes(order: MarketplaceOrder, product_card: MarketplaceProduct | None) -> set[str]:
    """All barcodes that WB says belong to THIS order size.

    One WB size may legitimately have two barcodes. They are aliases of the same
    characteristic and must both be accepted. Barcodes from another size are never
    accepted merely because the seller article is the same.
    """
    result = set(_wb_order_source_barcodes(order))
    row = _wb_order_card_size_row(order, product_card)
    if row:
        result.update({_norm_identity(x) for x in (row.get("skus") or []) if _norm_identity(x)})
    return result


def _wb_order_alias_profile(
    order: MarketplaceOrder, product_cards: list[MarketplaceProduct],
) -> tuple[set[str], str, str]:
    """Merge barcode aliases for one exact WB size across local card generations.

    Priority is strict: chrtId -> direct order barcode -> exact human size.  The helper
    never expands aliases by article alone, so historical cards cannot mix sizes.
    """
    direct = set(_wb_order_source_barcodes(order))
    allowed = set(direct)
    raw = order.raw_data if isinstance(order.raw_data, dict) else {}
    raw_chrt = raw.get("chrtId") or raw.get("chrtID") or raw.get("chrt_id")
    raw_size = str(_wb_raw_order_size(order) or "").strip()

    def row_size(row: dict[str, Any]) -> str:
        return str(row.get("techSize") or row.get("wbSize") or row.get("size") or "").strip()

    def collect(rows: list[dict[str, Any]], mode: str) -> tuple[set[str], str, str]:
        resolved = next((row_size(row) for row in rows if row_size(row)), raw_size)
        for row in rows:
            allowed.update({_norm_identity(x) for x in (row.get("skus") or []) if _norm_identity(x)})
        return allowed, resolved, mode

    if raw_chrt is not None:
        rows: list[dict[str, Any]] = []
        for card in product_cards:
            for row in (card.sizes or []):
                if not isinstance(row, dict):
                    continue
                chrt = row.get("chrtID") or row.get("chrtId") or row.get("chrt_id")
                if str(chrt or "") == str(raw_chrt):
                    rows.append(row)
        if rows:
            return collect(rows, "WB_CHRT_ALIAS_UNION")

    if direct:
        rows = []
        for card in product_cards:
            for row in (card.sizes or []):
                if not isinstance(row, dict):
                    continue
                row_codes = {_norm_identity(x) for x in (row.get("skus") or []) if _norm_identity(x)}
                if direct & row_codes:
                    rows.append(row)
        if rows:
            return collect(rows, "WB_ORDER_BARCODE_ALIAS_UNION")

    raw_size_key = _norm_identity(raw_size)
    if raw_size_key:
        rows = []
        for card in product_cards:
            for row in (card.sizes or []):
                if isinstance(row, dict) and _norm_identity(row_size(row)) == raw_size_key:
                    rows.append(row)
        if rows:
            return collect(rows, "WB_EXACT_SIZE_ALIAS_UNION")

    return allowed, raw_size, "ORDER_IDENTITY_ONLY"


def _strict_wb_order_sku(db: Session, order: MarketplaceOrder) -> SKU | None:
    """Resolve a WB order to one exact local SKU/size.

    Priority:
    1. exact order barcode;
    2. any alternate barcode from the SAME WB chrtId/size;
    3. seller article + exact WB size, only if unique;
    4. article-only only when that article has exactly one local SKU.

    Ambiguity returns None. We deliberately prefer an unallocated picking-list row
    over taking another size from the same article.
    """
    card = _product_card_for_order(db, order)
    allowed_codes = _wb_order_allowed_barcodes(order, card)
    article = _norm_identity(order.article)
    size_name = _norm_identity(_wb_order_size(order, card, None))

    def unique(rows: list[SKU]) -> SKU | None:
        by_id = {str(x.id): x for x in rows}
        return next(iter(by_id.values())) if len(by_id) == 1 else None

    # 1-2. Exact/alias barcode belonging to this concrete WB size.
    if allowed_codes:
        rows = db.execute(select(SKU).where(
            SKU.client_id == order.client_id,
            SKU.barcode.in_(list(allowed_codes)),
        )).scalars().all()
        if article:
            article_rows = [x for x in rows if article in {_norm_identity(x.article), _norm_identity(x.sku_code)}]
            if article_rows:
                rows = article_rows
        if size_name:
            sized = [x for x in rows if _norm_identity(x.size) == size_name]
            if sized:
                rows = sized
        found = unique(rows)
        if found:
            return found

    # 3. Article + exact size is safe only when it identifies one local SKU.
    article_rows: list[SKU] = []
    if article:
        article_rows = db.execute(select(SKU).where(
            SKU.client_id == order.client_id,
            or_(SKU.article == str(order.article or "").strip(), SKU.sku_code == str(order.article or "").strip()),
        )).scalars().all()
        if size_name:
            found = unique([x for x in article_rows if _norm_identity(x.size) == size_name])
            if found:
                return found

    # 4. Only a truly single-SKU article may use article-only fallback.
    if article_rows:
        return unique(article_rows)
    return None


def _repair_strict_wb_sku_links(db: Session, orders: list[MarketplaceOrder], barcode_only: bool = False) -> int:
    """Batch-repair stale WB SKU links without N+1 queries.

    0.9.2 PERF keeps the 0.9.1 strict identity rules, but resolves a whole order
    page/list with a handful of DB queries instead of 2-4 queries per order.
    Safety is unchanged: barcode/chrtId/size first; article-only only for a truly
    single-SKU article. Ambiguity still returns no local SKU.
    """
    wb_orders = [o for o in orders if str(o.provider or '').upper() == 'WB']
    if not wb_orders:
        return 0

    # Product cards: preload all cards referenced by these orders once.
    client_ids = {o.client_id for o in wb_orders if o.client_id}
    nm_ids: set[int] = set()
    articles: set[str] = set()
    for o in wb_orders:
        raw = o.raw_data or {}
        nm = raw.get('nmId') or raw.get('nmID') or raw.get('nm_id')
        try:
            if nm is not None:
                nm_ids.add(int(nm))
        except Exception:
            pass
        if o.article:
            articles.add(str(o.article).strip())

    card_clauses = []
    if nm_ids:
        card_clauses.append(MarketplaceProduct.external_product_id.in_(nm_ids))
    if articles:
        card_clauses.append(MarketplaceProduct.vendor_code.in_(articles))
    cards: list[MarketplaceProduct] = []
    if client_ids and card_clauses:
        cards = db.execute(
            select(MarketplaceProduct).options(load_only(
                MarketplaceProduct.id, MarketplaceProduct.client_id, MarketplaceProduct.external_product_id,
                MarketplaceProduct.vendor_code, MarketplaceProduct.sizes, MarketplaceProduct.updated_at,
                MarketplaceProduct.created_at
            )).where(
                MarketplaceProduct.client_id.in_(client_ids),
                MarketplaceProduct.provider == 'WB',
                or_(*card_clauses),
            ).order_by(MarketplaceProduct.updated_at.desc(), MarketplaceProduct.created_at.desc())
        ).scalars().all()

    card_by_client_nm: dict[tuple[str, int], MarketplaceProduct] = {}
    card_by_client_article: dict[tuple[str, str], MarketplaceProduct] = {}
    for card in cards:
        ckey = str(card.client_id)
        if card.external_product_id is not None:
            card_by_client_nm.setdefault((ckey, int(card.external_product_id)), card)
        if card.vendor_code:
            card_by_client_article.setdefault((ckey, str(card.vendor_code).strip()), card)

    def card_for(order: MarketplaceOrder) -> MarketplaceProduct | None:
        raw = order.raw_data or {}
        nm = raw.get('nmId') or raw.get('nmID') or raw.get('nm_id')
        try:
            nm = int(nm) if nm is not None else None
        except Exception:
            nm = None
        ckey = str(order.client_id)
        return (
            card_by_client_nm.get((ckey, nm)) if nm is not None else None
        ) or card_by_client_article.get((ckey, str(order.article or '').strip()))

    # Build every acceptable barcode and article once, then preload matching SKUs.
    allowed_by_order: dict[uuid.UUID, set[str]] = {}
    size_by_order: dict[uuid.UUID, str] = {}
    all_codes: set[str] = set()
    all_articles: set[str] = set()
    for order in wb_orders:
        card = card_for(order)
        allowed = _wb_order_allowed_barcodes(order, card)
        allowed_by_order[order.id] = allowed
        all_codes.update(allowed)
        size_by_order[order.id] = _norm_identity(_wb_order_size(order, card, None))
        if order.article:
            all_articles.add(str(order.article).strip())

    sku_clauses = []
    if all_codes:
        sku_clauses.append(SKU.barcode.in_(list(all_codes)))
    if all_articles:
        sku_clauses.append(SKU.article.in_(list(all_articles)))
        sku_clauses.append(SKU.sku_code.in_(list(all_articles)))
    sku_rows: list[SKU] = []
    if client_ids and sku_clauses:
        sku_rows = db.execute(
            select(SKU).where(SKU.client_id.in_(client_ids), or_(*sku_clauses))
        ).scalars().all()

    skus_by_client_barcode: dict[tuple[str, str], list[SKU]] = {}
    skus_by_client_article: dict[tuple[str, str], list[SKU]] = {}
    for sku in sku_rows:
        ckey = str(sku.client_id)
        barcode = _norm_identity(sku.barcode)
        if barcode:
            skus_by_client_barcode.setdefault((ckey, barcode), []).append(sku)
        for value in {_norm_identity(sku.article), _norm_identity(sku.sku_code)}:
            if value:
                skus_by_client_article.setdefault((ckey, value), []).append(sku)

    def unique(rows: list[SKU]) -> SKU | None:
        by_id = {str(x.id): x for x in rows}
        return next(iter(by_id.values())) if len(by_id) == 1 else None

    changed = 0
    for order in wb_orders:
        ckey = str(order.client_id)
        article_norm = _norm_identity(order.article)
        size_norm = size_by_order.get(order.id, '')
        allowed = allowed_by_order.get(order.id, set())

        # 1-2. Exact/alias barcode for THIS WB size.
        candidates: list[SKU] = []
        seen: set[str] = set()
        for code in allowed:
            for sku in skus_by_client_barcode.get((ckey, code), []):
                sid = str(sku.id)
                if sid not in seen:
                    seen.add(sid)
                    candidates.append(sku)
        if article_norm:
            article_candidates = [
                x for x in candidates
                if article_norm in {_norm_identity(x.article), _norm_identity(x.sku_code)}
            ]
            if article_candidates:
                candidates = article_candidates
        if size_norm:
            sized = [x for x in candidates if _norm_identity(x.size) == size_norm]
            if sized:
                candidates = sized
        strict = unique(candidates)

        # 3-4. Outside picking-list generation we preserve the historical safe
        # article+size/single-SKU fallback. Picking lists themselves are barcode-led:
        # if WB gives one of several barcodes for the same size, only that size's
        # barcode alias set may bind physical stock.
        article_rows = skus_by_client_article.get((ckey, article_norm), []) if article_norm else []
        if not barcode_only and strict is None and article_rows and size_norm:
            strict = unique([x for x in article_rows if _norm_identity(x.size) == size_norm])
        if not barcode_only and strict is None and article_rows:
            strict = unique(article_rows)

        strict_id = strict.id if strict else None
        if order.local_sku_id != strict_id:
            previous = str(order.local_sku_id or '') or None
            order.local_sku_id = strict_id
            raw = dict(order.raw_data or {})
            raw['_fc_strict_sku_match'] = {
                'previous_sku_id': previous,
                'sku_id': str(strict_id) if strict_id else None,
                'mode': 'EXACT_BARCODE_SIZE_ONLY_BATCH',
                'at': _now().isoformat(),
            }
            order.raw_data = raw
            changed += 1
    if changed:
        db.flush()
    return changed


def _find_wb_sku(db: Session, client_id: uuid.UUID, order: dict) -> SKU | None:
    """Import-time resolver: never choose an arbitrary size by seller article."""
    barcodes = [str(x).strip() for x in (order.get("skus") or order.get("barcodes") or []) if str(x or "").strip()]
    article = str(order.get("article") or "").strip() or None
    if barcodes:
        rows = db.execute(select(SKU).where(
            SKU.client_id == client_id, SKU.barcode.in_(barcodes)
        )).scalars().all()
        by_id = {str(x.id): x for x in rows}
        if len(by_id) == 1:
            return next(iter(by_id.values()))
    if article:
        rows = db.execute(select(SKU).where(
            SKU.client_id == client_id, or_(SKU.article == article, SKU.sku_code == article)
        )).scalars().all()
        by_id = {str(x.id): x for x in rows}
        if len(by_id) == 1:
            return next(iter(by_id.values()))
    return None


def _find_ozon_sku(db: Session, client_id: uuid.UUID, product: dict) -> SKU | None:
    offer = str(product.get("offer_id") or "").strip()
    if not offer:
        return None
    return db.execute(
        select(SKU).where(
            SKU.client_id == client_id,
            or_(SKU.article == offer, SKU.sku_code == offer, SKU.barcode == offer),
        )
    ).scalars().first()


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        text = str(value).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _event(db: Session, order: MarketplaceOrder, event_type: str, value: str | None = None, data: dict | None = None) -> None:
    db.add(MarketplaceOrderEvent(
        marketplace_order_id=order.id,
        event_type=event_type,
        value_text=value,
        data=data or {},
        created_at=_now(),
    ))


def _has_wb_order_sticker(order: MarketplaceOrder) -> bool:
    # A sticker may have been created in another WMS. In that case label_generated_at
    # can be empty locally, while WB already returns partA/partB/barcode.
    return bool(order.label_generated_at or _wb_sticker_data(order))


# FC_CHZ_LABEL_ALWAYS_PACKED_09415G_20260903
def _picking_has_chz_and_label(order: MarketplaceOrder) -> bool:
    """PICKING + saved CHZ + WB order sticker is a completed packing fact.

    NEW never bypasses the warehouse gate. Marketplace cancellation, sold and
    physical transport statuses remain authoritative in _compute_local_status.
    """
    return bool(
        str(order.local_status or "").strip().upper() in {"PICKING", "PACKED"}
        and str(order.chz_code or "").strip()
        and _has_wb_order_sticker(order)
    )


def _raw_status_values(order: MarketplaceOrder) -> set[str]:
    raw = order.raw_data or {}
    keys = ("status", "state", "orderStatus", "order_status", "supplierStatus", "supplier_status", "wbStatus", "wb_status")
    values = set()
    for key in keys:
        value = raw.get(key) if isinstance(raw, dict) else None
        if value is not None:
            values.add(str(value).strip().lower())
    return {v for v in values if v}


def _status_looks_cancelled(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return bool(
        normalized
        and (
            normalized in CANCEL_WB_STATUSES
            or normalized in CANCEL_SUPPLIER_STATUSES
            or normalized.startswith("cancel")
            or "отмен" in normalized
        )
    )


def _wb_order_currently_cancelled(order: MarketplaceOrder) -> bool:
    """Use current WB projections, never a stale historical feed field.

    ``raw_data`` is intentionally merged between WB feeds. A top-level ``status``
    from an old history response can therefore remain there after a newer
    ``/orders/status`` response says that the same order is active. The explicit
    model columns are refreshed by the status endpoint and are authoritative.
    Legacy raw fields are consulted only when both current columns are absent.
    """
    supplier = str(order.supplier_status or "").strip().lower()
    wb = str(order.wb_status or "").strip().lower()
    if supplier or wb:
        return supplier in CANCEL_SUPPLIER_STATUSES or wb in CANCEL_WB_STATUSES
    return any(_status_looks_cancelled(value) for value in _raw_status_values(order))


def _wb_order_hard_cancelled_for_packing(order: MarketplaceOrder) -> bool:
    """Packing blocker based on the order itself, not supplierStatus of a technical supply.

    WB can expose supplierStatus=cancel for a supply while the order is still valid for
    SGTIN/sticker work. We still hard-block real order cancellation/terminal departure.
    """
    wb = str(order.wb_status or "").strip().lower()
    if wb:
        return wb in CANCEL_WB_STATUSES or wb in WB_PHYSICALLY_LEFT_STATUSES or wb == "sold"
    raw = order.raw_data or {}
    if isinstance(raw, dict):
        for key in ("wbStatus", "wb_status", "orderStatus", "order_status", "status", "state"):
            value = str(raw.get(key) or "").strip().lower()
            if value and (value in CANCEL_WB_STATUSES or value in WB_PHYSICALLY_LEFT_STATUSES or value == "sold"):
                return True
    return False


def _compute_local_status(order: MarketplaceOrder) -> str:
    """Project WB state into the warehouse workflow using physical-departure truth.

    Critical warehouse rule (0.7.8.13):
    - The packing queue answers one question: what has NOT physically left our warehouse yet?
    - WB supplierStatus=confirm/complete and simple supply membership do NOT prove physical departure.
      Orders that are merely being assembled in another WMS must remain NEW in FulfillCore.
    - PICKING is FulfillCore-only. Never infer it from WB.
    - PACKED is FulfillCore-only packing evidence (completed print gate or product scan + CHZ).
    - WB transport statuses that prove the parcel is already in the logistics chain map to SENT.
      In particular wbStatus=sorted is already outside our packing queue and must never remain NEW.
    - sold and cancellation outcomes remain authoritative terminal states.
    """
    supplier = str(order.supplier_status or "").lower()
    wb = str(order.wb_status or "").lower()

    # Explicit force is written only by a deliberate operator action on a terminal
    # order. It changes the FulfillCore bucket, never the underlying WB status.
    raw = order.raw_data or {}
    forced_manual = str(raw.get("_fc_manual_status") or "").upper() if isinstance(raw, dict) and raw.get("_fc_force_manual_status") is True else ""
    if forced_manual in {"NEW", "PICKING", "PACKED", "SENT"}:
        return forced_manual

    # Terminal marketplace outcomes are authoritative unless explicitly forced locally.
    if _wb_order_currently_cancelled(order):
        return "CANCELLED"
    if wb == "sold":
        return "DELIVERED"

    # These WB states mean the parcel has physically entered delivery/return logistics.
    if wb in WB_PHYSICALLY_LEFT_STATUSES:
        return "SENT"

    # 0.9.4.15g invariant: an order already in «На сборке» with both CHZ and
    # a WB sticker is packed. This is before persisted PICKING/pending-WB markers,
    # so a later sync cannot push the order back to PICKING.
    if _picking_has_chz_and_label(order):
        return "PACKED"

    # supplierStatus=complete is NOT sufficient evidence of physical shipment. WB can expose
    # complete while an order is still in another WMS assembly flow / linked to a supply.
    # Only an actual WB transport state or an explicit FulfillCore manual-send marker may
    # remove such an order from the active warehouse queue.
    raw = order.raw_data or {}
    # Explicit operator override for the four warehouse workflow states. WB terminal/
    # physical movement facts above still win, so a real cancellation/delivery cannot
    # be hidden by a local click.
    manual_local = str(raw.get("_fc_manual_status") or "").upper() if isinstance(raw, dict) else ""
    if manual_local in {"NEW", "PICKING", "PACKED", "SENT"}:
        return manual_local
    if isinstance(raw, dict) and raw.get("_fc_manual_sent") is True:
        return "SENT"

    # A completed FulfillCore packing operation is an irreversible local fact.
    # The strict print pipeline records stage=DONE only after Print Agent ACK or
    # successful browser-label delivery. Some historical/recovered orders do not
    # retain product_scanned_at, so requiring that timestamp made them jump from
    # PACKED back to PICKING during a later WB feed refresh. The durable DONE marker
    # is equally strong local proof and does not fabricate a missing scan timestamp.
    pack_gate = raw.get("_fc_pack_gate_v1") if isinstance(raw, dict) else None
    pack_gate_done = isinstance(pack_gate, dict) and str(pack_gate.get("stage") or "").upper() == "DONE"
    completed_in_fulfillcore = bool(
        order.packed_at
        and order.chz_code
        and (order.product_scanned_at or pack_gate_done)
    )
    if completed_in_fulfillcore:
        return "PACKED"

    # A pending/rejected CHZ write is only a marketplace synchronization fact.
    # It must never move an order between FulfillCore workflow buckets. Preserve
    # the operator's current local status until WB confirms the code or the
    # operator changes the status manually. Marketplace terminal states above
    # remain authoritative.
    if _has_pending_wb_chz(order) and order.local_status in {"NEW", "PICKING", "PACKED", "SENT"}:
        return order.local_status

    # A locally stored CHZ is not packing proof while WB validation is pending or
    # rejected. The pending marker is cleared only after an allowed WB decision.
    has_pack_facts = bool(order.chz_code and not _has_pending_wb_chz(order) and _has_wb_order_sticker(order))
    packed_in_fulfillcore = bool(order.product_scanned_at and has_pack_facts)

    # If FulfillCore itself packed the item, keep it in PACKED until a real WB movement state
    # appears. A closed WB supply alone must not promote it to SENT.
    if packed_in_fulfillcore:
        return "PACKED"

    # CHZ + WB order sticker that already exist but were NOT created by FulfillCore mean
    # another WMS has completed packing for this order. Per warehouse workflow this order
    # must leave our packing queue and be shown in «Отправлены/Доставляется», rather than NEW.
    if has_pack_facts:
        return "SENT"

    # «На сборке» is local-only. WB confirm is deliberately ignored.
    if getattr(order, "manual_status_override", False) and order.local_status in {"NEW", "PICKING"}:
        return order.local_status
    if order.picking_list_generated_at:
        return "PICKING"

    # Fallback: if WB has not reported a physical departure state and there is no verified
    # packing evidence, the item is still work for our warehouse. This includes confirm/complete.
    return "NEW"


def _move_cancelled_picked_item_to_unplaced(db: Session, order: MarketplaceOrder, previous_status: str) -> None:
    """Apply the physical cancellation rule without changing reconciliation call sites.

    PICKING -> move the picked unit to virtual ПРИЕМКА; never back to the source cell.
    PACKED  -> keep the unit written off as outbound; never auto-receive it.
    """
    try:
        result = cancel_order_to_receiving(db, order, previous_status)
        action = str(result.get("action") or "").upper()
        if action == "KEEP_WRITTEN_OFF":
            _event(db, order, "CANCELLED_AFTER_PACKED_STOCK_KEPT_WRITTEN_OFF", value="PACKED", data={
                "reservation_id": result.get("reservation_id"),
                "operation_id": result.get("operation_id"),
                "rule": "packed_is_outbound",
            })
        elif action == "REVIEW" and previous_status == "PACKED":
            _event(db, order, "CANCELLED_AFTER_PACKED_STOCK_REVIEW_REQUIRED", value="PACKED", data={
                "reason": result.get("reason"),
                "rule": "do_not_auto_receive_packed_cancellation",
            })
        elif result.get("changed"):
            _event(db, order, "CANCELLED_ITEM_TO_VIRTUAL_RECEIVING", value="ПРИЕМКА", data={
                "reservation_id": result.get("reservation_id"),
                "operation_id": result.get("operation_id"),
                "from_reservation_status": result.get("from_status"),
            })
        elif result.get("reason") not in {"not_picked", "already_receiving"}:
            _event(db, order, "CANCEL_TO_RECEIVING_SKIPPED", data={"reason": result.get("reason")})
    except HTTPException as exc:
        # WB cancellation remains authoritative even if a legacy local balance needs manual
        # reconciliation. Never fail the whole marketplace status sweep because of it.
        _event(db, order, "CANCEL_TO_RECEIVING_REVIEW_REQUIRED", data={"detail": str(exc.detail)})

def _consume_picking_reservation_strict(
    db: Session,
    order: MarketplaceOrder,
    reason: str,
    *,
    require: bool = True,
    warehouse_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Write off physical stock only for the new sale-first reservation model.

    Legacy reservations keep the historical audit-only behaviour so upgrading an existing
    warehouse cannot double-write-off old PICKING/PACKED rows. SALE_FIRST_V1 rows are safe:
    order arrival reduced marketplace availability, picking only bound the address, and
    this call is the one physical SHIPPING boundary.
    """
    latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
    if not latest:
        if not require:
            result = finalize_order_stock_for_packing(db, order, warehouse_id=warehouse_id, note=reason)
            result = {**result, "ok": True, "request_reason": reason}
            _event(db, order, "PACK_WITHOUT_RESERVATION_ALLOWED", value=reason, data=result)
            return result
        result = {"changed": False, "ok": False, "reason": "no_reservation", "request_reason": reason}
        _event(db, order, "PACK_RESERVATION_MISSING", value=reason, data=result)
        raise HTTPException(status_code=409, detail="У заказа нет резерва склада. Обновите заказы и сформируйте сборочный лист заново.")
    mode = str(latest.get("reservation_mode") or "LEGACY").upper()
    status = str(latest.get("status") or "").upper()
    if mode != "SALE_FIRST_V1":
        result = {"changed": False, "ok": True, "reason": "legacy_audit_only", "request_reason": reason, "reservation_id": str(latest.get("id") or "")}
        _event(db, order, "PACK_RESERVATION_LEGACY_AUDIT_ONLY", value=reason, data=result)
        return result
    if status == "SALE_RESERVED":
        if not require:
            result = finalize_order_stock_for_packing(db, order, warehouse_id=warehouse_id, note=reason)
            result = {**result, "ok": True, "request_reason": reason}
            _event(db, order, "PACK_SALE_RESERVE_WITHOUT_PICKING_ALLOWED", value=reason, data=result)
            return result
        result = {"changed": False, "ok": False, "reason": "not_allocated_by_picking_list", "request_reason": reason}
        _event(db, order, "PACK_RESERVATION_NOT_ALLOCATED", value=reason, data=result)
        raise HTTPException(status_code=409, detail="Заказ зарезервирован для продажи, но ещё не привязан к ячейке. Сначала сформируйте сборочный лист.")
    if status == "PACKING_WRITE_OFF":
        result = recover_marketplace_packing_write_off(db, order=order, reservation=latest, note=reason)
        result = {**result, "ok": True, "request_reason": reason, "reservation_mode": mode}
        _event(db, order, "PACKING_WRITE_OFF_RECOVERED", value=reason, data=result)
        return result
    result = consume_order_reservation(db, order, note=reason)
    result = {**result, "ok": True, "request_reason": reason, "reservation_mode": mode}
    _event(db, order, "PACK_RESERVATION_PHYSICAL_WRITE_OFF", value=reason, data=result)
    return result


def _consume_picking_reservation_nonblocking(
    db: Session,
    order: MarketplaceOrder,
    reason: str,
    *,
    warehouse_id: uuid.UUID | str | None = None,
) -> dict[str, Any]:
    """Best-effort stock finalization that must never freeze an existing WB order.

    A marketplace order that has physically reached packing remains packable even when
    a legacy reservation row or an inventory operation needs later reconciliation.  A
    nested transaction keeps any accounting failure inside a savepoint, records an
    audit event, and lets the label/status transaction finish without returning HTTP 500.
    """
    try:
        with db.begin_nested():
            return _consume_picking_reservation_strict(
                db,
                order,
                reason,
                require=False,
                warehouse_id=warehouse_id,
            )
    except Exception as exc:
        detail = str(exc.detail if isinstance(exc, HTTPException) else exc)
        logger.exception(
            "FC_PACK_STOCK_FINALIZE_NONBLOCKING order_id=%s reason=%s error=%s",
            order.id,
            reason,
            detail,
        )
        result = {
            "changed": False,
            "ok": True,
            "stock_accounting_pending": True,
            "request_reason": reason,
            "error": detail,
        }
        try:
            _event(db, order, "PACK_STOCK_RECONCILIATION_PENDING", value=reason, data=result)
        except Exception:
            logger.exception(
                "FC_PACK_STOCK_AUDIT_EVENT_FAIL order_id=%s reason=%s",
                order.id,
                reason,
            )
        return result




def _set_manual_sent_marker(order: MarketplaceOrder, enabled: bool) -> None:
    raw = dict(order.raw_data or {})
    if enabled:
        raw["_fc_manual_sent"] = True
    else:
        raw.pop("_fc_manual_sent", None)
    order.raw_data = raw


def _workflow_sent_is_sticky(order: MarketplaceOrder) -> bool:
    """Late print/CHZ callbacks may enrich facts but never demote SENT."""
    raw = order.raw_data or {}
    manual = str(raw.get("_fc_manual_status") or "").upper() if isinstance(raw, dict) else ""
    return bool(
        order.local_status in {"SENT", "DELIVERED", "CANCELLED"}
        or manual == "SENT"
        or (isinstance(raw, dict) and raw.get("_fc_manual_sent") is True)
    )


def _set_manual_workflow_status(order: MarketplaceOrder, status: str | None) -> None:
    """Persist an explicit local workflow status without changing Wildberries.

    Used when the operator packed/shipped the order in WB or another WMS and wants
    FulfillCore to mirror the real warehouse fact. Marketplace terminal/transport
    statuses still override this marker in _compute_local_status().
    """
    raw = dict(order.raw_data or {})
    if status:
        value = str(status).upper()
        if value not in {"NEW", "PICKING", "PACKED", "SENT"}:
            raise ValueError(f"Unsupported local workflow status: {status}")
        raw["_fc_manual_status"] = value
        if value == "SENT":
            raw["_fc_manual_sent"] = True
        else:
            raw.pop("_fc_manual_sent", None)
    else:
        raw.pop("_fc_manual_status", None)
        raw.pop("_fc_manual_sent", None)
    order.raw_data = raw


# FC_K9R58_LOGICAL_SENT_PROPAGATION
def _logical_wb_order_rows_for_numbers(
    db: Session, cred: MarketplaceCredential, order_ids: list[int],
) -> dict[int, list[MarketplaceOrder]]:
    """Return every DB row for the selected visible WB order numbers across API-key rotations.

    The order board intentionally reads the logical credential lineage so reconnecting a WB API
    key does not hide history. A status write must use the same scope; otherwise the current
    credential row can become SENT while an older sibling row for the same WB order remains
    PACKED and immediately reappears on the next page refresh.
    """
    wanted = {int(x) for x in order_ids}
    if not wanted:
        return {}
    logical_ids = _logical_credential_ids(db, cred)
    rows = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        or_(
            MarketplaceOrder.external_order_num.in_(wanted),
            MarketplaceOrder.external_order_id.in_([str(x) for x in wanted]),
        ),
    )).scalars().all()
    grouped: dict[int, list[MarketplaceOrder]] = defaultdict(list)
    for row in rows:
        raw_num = row.external_order_num
        if raw_num is None and str(row.external_order_id or "").isdigit():
            raw_num = int(str(row.external_order_id))
        if raw_num is not None and int(raw_num) in wanted:
            grouped[int(raw_num)].append(row)
    missing = sorted(wanted - set(grouped))
    if missing:
        raise HTTPException(status_code=404, detail=f"Часть выбранных заказов WB не найдена: {missing[:10]}")
    return grouped


def _mark_packed_orders_sent_local(
    db: Session, *, cred: MarketplaceCredential, order_ids: list[int],
    physical_warehouse_id: uuid.UUID | str | None, source: str,
) -> dict[str, Any]:
    """Make PACKED -> SENT a monotonic local fact for every logical credential sibling.

    PACKED is already the physical stock write-off boundary, so this function never consumes
    stock again and never calls WB. It updates every duplicate/history row representing the same
    visible order, writes the durable manual-SENT marker, verifies no non-terminal sibling stayed
    behind, and commits once.
    """
    wanted = sorted({int(x) for x in order_ids})
    grouped = _logical_wb_order_rows_for_numbers(db, cred, wanted)
    changed = 0
    healed_sibling_rows = 0
    already_sent: list[str] = []

    for number in wanted:
        siblings = grouped[number]
        # Prefer the current credential row because it is the row background WB sync writes to.
        canonical = next((o for o in siblings if o.credential_id == cred.id), None)
        if canonical is None:
            canonical = siblings[0]
        _assert_wb_work(cred, canonical, physical_warehouse_id)
        current = str(canonical.local_status or "").upper()
        sibling_states = {str(o.local_status or "").upper() for o in siblings}
        # FC_K9R61_SENT_ELIGIBILITY_FROM_LOGICAL_VISIBLE_FACT:
        # The order board reads the complete logical credential lineage. After an API-key
        # rotation the current credential row may still be NEW/PICKING while an older sibling
        # is the PACKED row the operator actually selected. Treat PACKED on any non-terminal
        # sibling as the business fact, but keep current-credential cancellation authoritative.
        if current == "CANCELLED":
            raise HTTPException(status_code=409, detail=f"Заказ {number} уже отменён WB")
        if current in {"SENT", "DELIVERED"}:
            already_sent.append(str(number))
        elif current == "PACKED" or "PACKED" in sibling_states:
            changed += 1
        elif "SENT" in sibling_states or "DELIVERED" in sibling_states:
            already_sent.append(str(number))
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Заказ {number}: перевод в «Отправленные» разрешён только из «Упаковано»",
            )

        for order in siblings:
            sibling_status = str(order.local_status or "").upper()
            if sibling_status in {"CANCELLED", "DELIVERED"}:
                continue
            _assert_wb_work(cred, order, physical_warehouse_id)
            previous = order.local_status
            _set_manual_workflow_status(order, "SENT")
            order.local_status = "SENT"
            setattr(order, "manual_status_override", True) if hasattr(order, "manual_status_override") else None
            _event(db, order, "WORKFLOW_MANUAL_SENT", value="SENT", data={
                "from": previous,
                "wb_changed": False,
                "fast_local": True,
                "source": source,
                "logical_sibling": order.id != canonical.id,
            })
            if order.id != canonical.id and sibling_status != "SENT":
                healed_sibling_rows += 1

    # Verify inside the same transaction. If any duplicate/history row is still an active
    # warehouse status, fail before COMMIT instead of telling the browser the move succeeded.
    db.flush()
    logical_ids = _logical_credential_ids(db, cred)
    remaining = db.execute(select(
        MarketplaceOrder.external_order_num, MarketplaceOrder.external_order_id, MarketplaceOrder.local_status
    ).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        or_(
            MarketplaceOrder.external_order_num.in_(wanted),
            MarketplaceOrder.external_order_id.in_([str(x) for x in wanted]),
        ),
        MarketplaceOrder.local_status.in_(["NEW", "PICKING", "PACKED"]),
    )).all()
    if remaining:
        sample = [str(num or ext) for num, ext, _ in remaining[:10]]
        raise HTTPException(status_code=409, detail=f"Не все копии заказа перешли в «Отправленные»: {sample}")

    db.commit()
    return {
        "ok": True,
        "changed": changed,
        "already_sent": already_sent,
        "selected": len(wanted),
        "logical_rows": sum(len(v) for v in grouped.values()),
        "healed_sibling_rows": healed_sibling_rows,
        "fast_local": True,
        "verified": len(wanted),
    }


def _refresh_selected_wb_statuses(db: Session, client: WBClient, orders: list[MarketplaceOrder]) -> None:
    """Refresh only the selected orders before a dispatch-sensitive action.

    This is intentionally tiny and isolated from the normal synchronization module.
    It prevents duplicate dispatch if another WMS has already changed an order to
    supplierStatus=complete between the last background sync and the operator click.
    """
    by_num = {o.external_order_num: o for o in orders if o.external_order_num is not None}
    if not by_num:
        return
    try:
        statuses = client.order_statuses(list(by_num))
    except HTTPException as exc:
        if exc.status_code in {429, 502, 503, 504}:
            return
        raise
    for item in statuses:
        raw_id = item.get("id") or item.get("orderId") or item.get("order_id")
        try:
            num = int(raw_id)
        except (TypeError, ValueError):
            continue
        order = by_num.get(num)
        if order:
            _apply_status(db, order, item)


def _detach_chz_from_cancelled_picking_order(db: Session, order: MarketplaceOrder, *, source: str) -> bool:
    """Free a scanned CHZ when WB really cancels an order before PACKED.

    supplierStatus of a technical supply is intentionally ignored here. This runs only
    when normal order reconciliation has already decided the order itself is CANCELLED.
    """
    if str(order.local_status or "").upper() in {"PACKED", "SENT", "DELIVERED"}:
        return False
    old_chz = str(order.chz_code or "")
    if not old_chz:
        return False
    raw = dict(order.raw_data or {})
    raw.pop("_fc_chz_pending_wb", None)
    raw.pop("_fc_auto_wb_label_pending", None)
    raw.pop("_fc_auto_wb_label", None)
    raw.pop(_PACK_GATE_KEY, None)
    raw["_fc_cancelled_chz_detached"] = {"at": _now().isoformat(), "source": source}
    order.raw_data = raw
    order.chz_code = None
    order.chz_attached_at = None
    # Prevent an already queued label from printing after cancellation.
    try:
        jobs = db.execute(select(PrintJob).where(
            PrintJob.object_type == "MARKETPLACE_ORDER",
            PrintJob.object_id == str(order.id),
            PrintJob.document_type == "WB_ORDER_LABEL",
            PrintJob.status.in_(["QUEUED", "CLAIMED", "PROCESSING", "PRINTING", "RUNNING", "BROWSER_READY"]),
        )).scalars().all()
        for job in jobs:
            job.status = "CANCELLED"
            job.error_message = "WB order cancelled before packing; CHZ detached"
    except Exception:
        logger.exception("FC_CANCELLED_CHZ_PRINT_CANCEL_FAIL order_id=%s", order.id)
    _event(db, order, "CHZ_DETACHED_ON_WB_CANCEL", value=old_chz, data={"source": source})
    return True


def _finalize_terminal_stock_if_needed(
    db: Session, order: MarketplaceOrder, target: str, source: str,
) -> dict[str, Any] | None:
    if target not in {"PACKED", "SENT", "DELIVERED"}:
        return None
    latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
    if not latest or str(latest.get("status") or "").upper() not in {
        "SALE_RESERVED", "ACTIVE", "CONSUMING", "PACKING_WRITE_OFF"
    }:
        return None
    result = _consume_picking_reservation_nonblocking(
        db, order, f"AUTO_TERMINAL_{target}_{source}", warehouse_id=latest.get("warehouse_id"),
    )
    _event(db, order, "AUTO_TERMINAL_STOCK_FINALIZE", value=target, data={
        "source": source, "reservation_status": latest.get("status"), **result,
    })
    return result


def _reconcile_local_status(db: Session, order: MarketplaceOrder, source: str) -> bool:
    previous = order.local_status
    target = _compute_local_status(order)
    # Automatic reconciliation may promote PACKED to a terminal/transport state,
    # but it must never demote it back into warehouse work. Explicit manual status
    # changes continue to use their dedicated endpoint.
    if previous == "PACKED" and target in {"NEW", "PICKING"} and order.packed_at:
        return False
    # r42: SENT is a monotonic local warehouse fact. A late metadata/label/CHZ refresh
    # cannot return an already shipped order into any warehouse work bucket.
    if previous == "SENT" and target in {"NEW", "PICKING", "PACKED"}:
        return False
    if target == previous:
        # Historical terminal rows are intentionally not mass-mutated by normal WB sync.
        # They remain unavailable for sale and are shown as accounting_pending until the
        # explicit backup-protected repair tool reconciles them.  New transitions into a
        # terminal state are finalized below at the physical packing boundary.
        return False
    if target == "CANCELLED":
        # A CHZ scanned during packing belongs to no cancelled order. Free it immediately
        # so the physical code can be used for the correct order. Technical supply
        # supplierStatus=cancel is not enough to reach this branch.
        _detach_chz_from_cancelled_picking_order(db, order, source=source)
        # 0.9.3: NEW WB orders may already hold an immediate FBS reservation.
        # A cancellation before physical picking must release it back to FBS_AVAILABLE,
        # not move it to ПРИЕМКА.
        if not order.last_picking_list_id:
            released = release_order_reservation(db, order, note="WB_CANCELLED_BEFORE_PHYSICAL_PICKING")
            if released.get("changed"):
                _event(db, order, "FBS_SALE_RESERVATION_RELEASED", value="CANCELLED", data=released)
        else:
            _move_cancelled_picked_item_to_unplaced(db, order, previous)
    elif target in {"PACKED", "SENT", "DELIVERED"} and previous in {"NEW", "PICKING", "PACKED"}:
        # Picking is a reservation only. Packing/transport is the physical write-off
        # boundary, including automatic WB status reconciliation.
        result = _finalize_terminal_stock_if_needed(db, order, target, source)
        _event(db, order, "TERMINAL_STOCK_FINALIZED", value=target, data={
            "previous": previous, "result": result or {"changed": False, "reason": "no_live_reservation"},
        })
    order.local_status = target
    if target == "PACKED":
        now = _now()
        order.label_generated_at = order.label_generated_at or now
        order.assembled_at = order.assembled_at or now
        order.packed_at = order.packed_at or now
    _event(db, order, "WORKFLOW_STATUS_RECONCILED", value=target, data={"from": previous, "source": source})
    return True


def repair_picking_chz_label_statuses(db: Session) -> int:
    """Repair already-stuck WB PICKING rows without print/WB/stock calls."""
    orders = db.execute(
        select(MarketplaceOrder).where(
            MarketplaceOrder.provider == "WB",
            MarketplaceOrder.local_status == "PICKING",
            MarketplaceOrder.chz_code.is_not(None),
            MarketplaceOrder.chz_code != "",
        )
    ).scalars().all()
    changed = 0
    for order in orders:
        if not _picking_has_chz_and_label(order):
            continue
        if _reconcile_local_status(db, order, "STARTUP_CHZ_LABEL_ALWAYS_PACKED_0_9_4_15G"):
            changed += 1
    if changed:
        db.flush()
    return changed




def repair_manual_sent_regressions(db: Session) -> int:
    """Restore only orders whose latest explicit operator workflow action was SENT.

    r41 could receive a late print/CHZ callback after PACKED->SENT and overwrite the
    local bucket back to PACKED while clearing the marker. Event history keeps the
    operator action durable. A later explicit manual NEW/PICKING/PACKED action wins.
    """
    candidates=db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.provider=="WB", MarketplaceOrder.local_status=="PACKED"
    )).scalars().all()
    if not candidates:
        return 0
    ids=[o.id for o in candidates]
    events=db.execute(select(MarketplaceOrderEvent).where(
        MarketplaceOrderEvent.marketplace_order_id.in_(ids),
        MarketplaceOrderEvent.event_type.in_(["WORKFLOW_MANUAL_SENT","WORKFLOW_MANUAL_STATUS"]),
    ).order_by(MarketplaceOrderEvent.created_at.desc())).scalars().all()
    latest={}
    for ev in events:
        latest.setdefault(ev.marketplace_order_id,ev)
    changed=0
    for order in candidates:
        ev=latest.get(order.id)
        if not ev:
            continue
        value=str(ev.value_text or "").upper()
        if ev.event_type=="WORKFLOW_MANUAL_SENT" or value=="SENT":
            _set_manual_workflow_status(order,"SENT")
            setattr(order,"manual_status_override",True) if hasattr(order,"manual_status_override") else None
            order.local_status="SENT"
            _event(db,order,"R42_MANUAL_SENT_REGRESSION_REPAIRED",value="SENT",data={"source_event":ev.event_type})
            changed+=1
    return changed

def _repair_legacy_sent_status(db: Session, order: MarketplaceOrder) -> bool:
    """FC_K9R58_SENT_MONOTONIC_FINAL: never auto-demote a local SENT warehouse fact.

    Earlier releases used this helper to clean legacy rows that had been promoted to SENT by
    supply progress alone. After the strict PACKED -> SENT endpoints were introduced, running
    that historical cleanup during every WB refresh became dangerous: if a manual marker was
    missing on one duplicated credential-history row, the helper could turn a just-shipped
    order back into PACKED. Terminal marketplace truth may still advance SENT to CANCELLED or
    DELIVERED, but ordinary metadata/label/CHZ refresh can never move it backwards.
    """
    if order.local_status != "SENT":
        return False
    target = _compute_local_status(order)
    if target not in {"CANCELLED", "DELIVERED"}:
        return False
    previous = order.local_status
    order.local_status = target
    _event(db, order, "SENT_TERMINAL_STATUS_RECONCILED", value=target, data={
        "from": previous, "rule": "r58_sent_monotonic_except_authoritative_terminal",
    })
    return True


def _order_lookup(db: Session, order_id: uuid.UUID, credential_id: uuid.UUID | None = None) -> MarketplaceOrder:
    order = db.get(MarketplaceOrder, order_id)
    if not order or order.provider != "WB":
        raise HTTPException(status_code=404, detail="Заказ WB не найден в FulfillCore")
    if credential_id and order.credential_id != credential_id:
        cred = _credential(db, credential_id, "WB")
        if order.credential_id not in set(_logical_credential_ids(db, cred)):
            raise HTTPException(status_code=400, detail="Заказ относится к другому кабинету WB")
    return order


def _wb_order_number(order: MarketplaceOrder) -> int:
    """Return the one numeric WB assembly-order ID used by every marketplace call.

    ``external_order_num`` is the authoritative numeric projection. Older database
    rows may only have the legacy string field, so keep a strictly numeric fallback
    instead of letting different CHZ steps address different WB orders.
    """
    raw = order.external_order_num
    if raw is None:
        raw = order.external_order_id
    try:
        number = int(str(raw).strip())
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=409,
            detail="У заказа отсутствует корректный числовой orderId Wildberries. Выполните синхронизацию заказов WB.",
        )
    if number <= 0:
        raise HTTPException(
            status_code=409,
            detail="У заказа указан некорректный orderId Wildberries. Выполните синхронизацию заказов WB.",
        )
    return number


def _wb_status_row_for_order(rows: list[dict[str, Any]] | None, order: MarketplaceOrder) -> dict[str, Any] | None:
    """Select only the status row for this exact WB assembly order."""
    target = str(_wb_order_number(order))
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    for row in rows:
        raw_id = row.get("id") or row.get("orderId") or row.get("order_id")
        if raw_id is not None and str(raw_id).strip() == target:
            return row
    # A one-order request may exceptionally return a row without its identifier.
    # Accept it only when the identifier is absent, never when it points elsewhere.
    if len(rows) == 1:
        row = rows[0]
        raw_id = row.get("id") or row.get("orderId") or row.get("order_id")
        if raw_id in (None, ""):
            return row
    return None


def _restore_chz_gs_if_unambiguous(value: str) -> tuple[str, bool]:
    """Restore FNC1/GS separators only for the standard Russian marking pattern.

    USB-HID scanners often transmit GS (ASCII 29) as Ctrl+]. Some browsers do not put
    that control character into a text input at all. When the rest of a full GS1 code is
    intact, the standard clothing pattern is unambiguous enough to restore separators:
    01 + 14-digit GTIN + 21 + serial + GS + 91 + 4-char key + GS + 92 + crypto tail.
    We never guess if those anchors do not line up exactly.
    """
    if "\x1d" in value:
        return value, False
    if len(value) < 24 or not value.startswith("01"):
        return value, False
    if len(value) < 18 or not value[2:16].isdigit() or value[16:18] != "21":
        return value, False

    # Full code: AI 91 has four data characters and is immediately followed by AI 92.
    matches: list[int] = []
    for idx in range(19, max(19, len(value) - 7)):
        if value[idx:idx + 2] == "91" and value[idx + 6:idx + 8] == "92":
            serial_len = idx - 18
            if 1 <= serial_len <= 20 and len(value) > idx + 8:
                matches.append(idx)
    if len(matches) == 1:
        idx = matches[0]
        return value[:idx] + "\x1d" + value[idx:idx + 6] + "\x1d" + value[idx + 6:], True

    # Short code without crypto tail: ...21<serial> GS 91<4 chars>.
    short_matches: list[int] = []
    for idx in range(19, max(19, len(value) - 5)):
        if value[idx:idx + 2] == "91" and len(value) == idx + 6:
            serial_len = idx - 18
            if 1 <= serial_len <= 20:
                short_matches.append(idx)
    if len(short_matches) == 1:
        idx = short_matches[0]
        return value[:idx] + "\x1d" + value[idx:], True
    return value, False


def _normalize_chz(raw: str) -> str:
    # Preserve the exact crypto tail. Only normalize scanner framing / GS representations.
    value = str(raw or "").rstrip("\r\n")
    if value.startswith(("]d2", "]D2")):
        value = value[3:]

    # HID scanners inherit the active Windows keyboard layout. If the workstation is
    # accidentally left in Russian, ASCII letters from the marking code arrive as
    # Cyrillic keyboard-position equivalents. GS1/DataMatrix marking payloads are ASCII,
    # so it is safe to restore only characters that are impossible in a valid CHZ.
    ru_keys = "йцукенгшщзхъфывапролджэячсмитьбюё"
    en_keys = "qwertyuiop[]asdfghjkl;'zxcvbnm,.`"
    layout_map = {ord(r): e for r, e in zip(ru_keys, en_keys)}
    layout_map.update({ord(r.upper()): e.upper() for r, e in zip(ru_keys, en_keys)})
    if any(('а' <= ch.lower() <= 'я') or ch in {'ё', 'Ё'} for ch in value):
        value = value.translate(layout_map)
    for marker in ("<GS>", "[GS]", "{GS}", "\\u001D", "\\u001d", "\\x1D", "\\x1d", "␝"):
        value = value.replace(marker, "\x1d")
    value, _ = _restore_chz_gs_if_unambiguous(value)
    if len(value) < 16 or len(value) > 135:
        raise HTTPException(status_code=400, detail="Код маркировки должен содержать от 16 до 135 символов. Отсканируйте DataMatrix целиком.")
    return value


def _chz_diagnostics(value: str) -> dict[str, Any]:
    restored, auto_restored = _restore_chz_gs_if_unambiguous(value)
    return {
        "length": len(value),
        "gs_count": value.count("\x1d"),
        "starts_01": value.startswith("01"),
        "has_ai21": len(value) >= 18 and value[16:18] == "21",
        "has_ai91": "91" in value,
        "has_ai92": "92" in value,
        "gs_recoverable": auto_restored and restored != value,
    }


def _meta_details(item: dict | None) -> list[dict[str, Any]]:
    if not item:
        return []
    details = item.get("metaDetails") or item.get("meta_details") or []
    if isinstance(details, dict):
        details = list(details.values())
    return [x for x in details if isinstance(x, dict)] if isinstance(details, list) else []


def _meta_detail(item: dict | None, key: str) -> dict[str, Any] | None:
    wanted = key.lower()
    for detail in _meta_details(item):
        name = str(detail.get("key") or detail.get("name") or detail.get("type") or detail.get("meta") or "").lower()
        if name == wanted:
            return detail
    return None


def _extract_meta_value(item: dict | None, key: str) -> str | None:
    # Current WB FBS API (2026) returns metaDetails[].value as a string.
    # Keep legacy `meta` parsing as a fallback for older responses/caches.
    detail = _meta_detail(item, key)
    if detail is not None:
        value = detail.get("value")
        if isinstance(value, str):
            return value or None
        if isinstance(value, list):
            return str(value[0]) if value else None
        if value is not None:
            return str(value)

    meta = (item or {}).get("meta") or {}
    if not isinstance(meta, dict):
        return None
    entry = meta.get(key)
    value = entry.get("value") if isinstance(entry, dict) else None
    if isinstance(value, list):
        return str(value[0]) if value else None
    if isinstance(value, str):
        return value or None
    return str(value) if value is not None else None


def _meta_has_sgtin(item: dict | None) -> bool:
    if not item:
        return False
    if _meta_detail(item, "sgtin") is not None:
        return True
    meta = item.get("meta") or {}
    return isinstance(meta, dict) and "sgtin" in meta


def _refresh_sgtin_capability(client: WBClient, order: MarketplaceOrder) -> bool:
    if "sgtin" in set((order.required_meta or []) + (order.optional_meta or [])):
        return True
    order_number = _wb_order_number(order)
    rows = client.order_meta([order_number])
    item = next((x for x in rows if str(x.get("id") or x.get("orderId") or x.get("order_id")) == str(order_number)), None)
    if not _meta_has_sgtin(item):
        return False
    optional = list(order.optional_meta or [])
    if "sgtin" not in optional:
        optional.append("sgtin")
        order.optional_meta = optional
    actual = _extract_meta_value(item, "sgtin")
    if actual and not order.chz_code:
        order.chz_code = actual
        order.chz_attached_at = order.chz_attached_at or _now()
    return True


def _apply_wb_order_meta(db: Session, order: MarketplaceOrder, item: dict | None) -> bool:
    """Synchronize the actual WB marking code into the local order projection.

    Since 30 April 2026 WB no longer guarantees the legacy `meta` object for FBS.
    The authoritative current response is `metaDetails[]` with key/value/decision.
    """
    if not item:
        return False
    changed = False
    has_sgtin = _meta_has_sgtin(item)
    if has_sgtin:
        optional = list(order.optional_meta or [])
        if "sgtin" not in optional:
            optional.append("sgtin")
            order.optional_meta = optional
            changed = True

    if not has_sgtin:
        return changed
    actual = _extract_meta_value(item, "sgtin")
    pending_local_chz = _has_pending_wb_chz(order)
    if actual and order.chz_code and actual == order.chz_code and pending_local_chz:
        state = _wb_sgtin_state(item, order.chz_code)
        if state.get("accepted"):
            gate_raw = dict(order.raw_data or {})
            strict_gate = gate_raw.get("_fc_pack_gate_v1") if isinstance(gate_raw.get("_fc_pack_gate_v1"), dict) else {}
            strict_gate_active = (
                bool(strict_gate)
                and str(strict_gate.get("chz") or "") == str(order.chz_code or "")
                and str(strict_gate.get("stage") or "") != "DONE"
            )
            if strict_gate_active:
                pending = gate_raw.get("_fc_chz_pending_wb") if isinstance(gate_raw.get("_fc_chz_pending_wb"), dict) else {}
                pending = dict(pending)
                pending["wb_verified"] = True
                pending["wb_decision"] = str(state.get("decision") or "") or None
                pending["last_checked_at"] = _now().isoformat()
                pending["reason"] = "WB_VERIFIED_WAITING_PRINT"
                gate_raw["_fc_chz_pending_wb"] = pending
                order.raw_data = gate_raw
                return True
            if _clear_pending_wb_chz(order):
                changed = True
                pending_local_chz = False
                _event(
                    db,
                    order,
                    "CHZ_PENDING_CONFIRMED_FROM_WB",
                    value=actual,
                    data={"decision": state.get("decision")},
                )
        else:
            # WB may echo the submitted SGTIN while its decision is still pending or
            # already rejected. Merely seeing the value is not acceptance and must not
            # promote the order to PACKED during a background metadata sweep.
            decision = str(state.get("decision") or "").strip()
            raw = dict(order.raw_data or {})
            pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
            previous_decision = str(pending.get("wb_decision") or "")
            if decision != previous_decision:
                pending["wb_decision"] = decision or None
                pending["last_checked_at"] = _now().isoformat()
                if decision.lower() in WB_SGTIN_DECISION_MESSAGES and decision.lower() not in {"pending", "required"}:
                    pending["reason"] = "WB_REJECTED_AFTER_SUBMIT"
                raw["_fc_chz_pending_wb"] = pending
                raw["_fc_chz_last_wb_decision"] = decision or None
                order.raw_data = raw
                changed = True
                _event(db, order, "CHZ_PENDING_DECISION_FROM_WB", value=actual, data={"decision": decision or None})
            return changed

    # 0.7.8.21: a CHZ scanned in the warehouse must never disappear merely because
    # WB still returns an empty SGTIN for a complete/closed supply. Earlier builds
    # wrote the pending CHZ locally and then the two-minute metadata reconciliation
    # replaced it with None, which also demoted PACKED back to PICKING. Keep the exact
    # scanner value until WB confirms it or the operator explicitly replaces/deletes it.
    if pending_local_chz and not actual and order.chz_code:
        return changed
    if pending_local_chz and actual and order.chz_code and actual != order.chz_code:
        raw = dict(order.raw_data or {})
        raw["_wb_chz_conflict"] = {
            "local_pending_chz": order.chz_code,
            "wb_chz": actual,
            "detected_at": _now().isoformat(),
        }
        order.raw_data = raw
        _event(db, order, "WB_MARKING_CODE_CONFLICT_PENDING_LOCAL", value=actual, data={"local_pending_chz": order.chz_code})
        return True

    # REV2: while the sequential pack gate owns the scanner CHZ, passive WB metadata
    # projections are never allowed to detach or replace that physical scan. Explicit
    # operator delete/replace and the confirmed-cancel path remain authoritative.
    gate_raw = dict(order.raw_data or {})
    strict_gate = gate_raw.get("_fc_pack_gate_v1") if isinstance(gate_raw.get("_fc_pack_gate_v1"), dict) else {}
    strict_gate_active = (
        bool(strict_gate)
        and str(strict_gate.get("chz") or "") == str(order.chz_code or "")
        and str(strict_gate.get("stage") or "") != "DONE"
    )
    if order.chz_code and (strict_gate_active or not actual):
        return changed

    if actual != order.chz_code:
        # Reconciliation must be idempotent. WB can occasionally return the same
        # SGTIN for more than one local projection of an order. The DB intentionally
        # enforces one CHZ per credential, so never let a background sweep crash the
        # whole transaction with uq_marketplace_order_chz.
        if actual:
            duplicate = db.execute(
                select(MarketplaceOrder).where(
                    MarketplaceOrder.credential_id == order.credential_id,
                    MarketplaceOrder.chz_code == actual,
                    MarketplaceOrder.id != order.id,
                )
            ).scalars().first()
            if duplicate:
                raw = dict(order.raw_data or {})
                raw["_wb_chz_conflict"] = {
                    "chz": actual,
                    "existing_order_id": str(duplicate.id),
                    "existing_external_order_id": duplicate.external_order_id,
                    "detected_at": _now().isoformat(),
                }
                order.raw_data = raw
                _event(
                    db, order, "WB_MARKING_CODE_CONFLICT", value=actual,
                    data={"existing_order_id": str(duplicate.id), "existing_external_order_id": duplicate.external_order_id},
                )
                return changed
        old = order.chz_code
        order.chz_code = actual
        order.chz_attached_at = _now() if actual else None
        changed = True
        _event(db, order, "MARKING_CODE_SYNCED_FROM_WB", value=actual, data={"previous": old})
        if _reconcile_local_status(db, order, "WB_MARKING_SYNC"):
            changed = True
    return changed



def _wb_sticker_data(order: MarketplaceOrder) -> dict[str, Any]:
    raw = order.raw_data or {}
    data = raw.get("_wb_order_sticker") if isinstance(raw, dict) else None
    return data if isinstance(data, dict) else {}


def _apply_wb_order_sticker(db: Session, order: MarketplaceOrder, item: dict | None) -> bool:
    if not item:
        return False
    sticker = {
        "partA": item.get("partA"),
        "partB": item.get("partB"),
        "barcode": item.get("barcode"),
        "syncedAt": _now().isoformat(),
    }
    # Do not store the base64 PNG in JSONB: the print endpoint can request it again.
    if not any(sticker.get(k) not in (None, "") for k in ("partA", "partB", "barcode")):
        return False
    raw = dict(order.raw_data or {})
    previous = raw.get("_wb_order_sticker") if isinstance(raw.get("_wb_order_sticker"), dict) else {}
    comparable_old = {k: previous.get(k) for k in ("partA", "partB", "barcode")}
    comparable_new = {k: sticker.get(k) for k in ("partA", "partB", "barcode")}
    if comparable_old == comparable_new:
        return False
    raw["_wb_order_sticker"] = sticker
    order.raw_data = raw
    order.label_generated_at = order.label_generated_at or _now()
    _reconcile_local_status(db, order, "WB_STICKER_SYNC")
    return True


def _decode_wb_sticker_file(raw_file: Any) -> bytes | None:
    """Decode every WB sticker payload variant without leaking binascii errors as 500."""
    if not raw_file:
        return None
    try:
        encoded = str(raw_file).strip()
        # Official WB responses use plain base64. Some gateways return a data URI and
        # short-lived projections can omit trailing padding, so support both safely.
        if encoded.lower().startswith("data:") and "," in encoded:
            encoded = encoded.split(",", 1)[1]
        encoded = "".join(encoded.split())
        missing_padding = len(encoded) % 4
        if missing_padding:
            encoded += "=" * (4 - missing_padding)
        decoded = base64.b64decode(encoded)
        return decoded if decoded else None
    except Exception:
        # The resilient caller retries a temporarily incomplete WB projection. Bulk
        # printing reports a useful 503 instead of a generic Internal Server Error.
        return None


def _fetch_wb_order_sticker(db: Session, client: WBClient, order: MarketplaceOrder) -> tuple[bytes | None, dict[str, Any] | None]:
    order_number = _wb_order_number(order)
    items = client.order_stickers([order_number])
    item = next(
        (
            x for x in items
            if str(x.get("orderId") or x.get("id") or x.get("order_id") or "") == str(order_number)
        ),
        None,
    )
    # The official response normally contains orderId. During short WB projection
    # windows we have also seen a one-item response without the identifier. Because
    # this request contains exactly one assembly order, accepting that sole item is
    # unambiguous and prevents a valid image from being discarded locally.
    if not item and len(items) == 1:
        item = items[0]
    if not item:
        return None, None
    _apply_wb_order_sticker(db, order, item)
    return _decode_wb_sticker_file(item.get("file")), item


def _wb_order_is_cancelled_for_label(order: MarketplaceOrder) -> bool:
    # local_status can itself be the stale value that this print attempt is about
    # to repair. Only the current WB projections (or legacy raw fallback when both
    # projections are missing) may block a label as cancelled.
    if _wb_order_currently_cancelled(order):
        return True
    return bool(
        order.local_status == "CANCELLED"
        and not str(order.supplier_status or "").strip()
        and not str(order.wb_status or "").strip()
        and not _raw_status_values(order)
    )


def _refresh_exact_wb_status_for_label(db: Session, client: WBClient, order: MarketplaceOrder) -> bool:
    """Best-effort one-order truth refresh used only by label recovery."""
    try:
        rows = client.order_statuses([_wb_order_number(order)])
    except HTTPException:
        return False
    status = _wb_status_row_for_order(rows, order)
    if status is None:
        return False
    _apply_status(db, order, status)
    db.flush()
    return True


def _fetch_wb_order_sticker_resilient(
    db: Session,
    client: WBClient,
    order: MarketplaceOrder,
    *,
    fallback: bytes | None = None,
) -> tuple[bytes | None, dict[str, Any] | None]:
    """Get one printable sticker without failing on WB's first empty projection.

    This helper is deliberately used only by explicit/automatic label printing. It
    does not change packing, CHZ or status rules. Four bounded attempts take at most
    about two seconds of local backoff, which is enough for the common WB delay while
    avoiding the former long metadata polling loop on the packing hot path.
    """
    status_refreshed = False
    # A stale local CANCELLED bucket must be repaired before it can suppress a
    # perfectly valid sticker. Refresh only this order, and only on the recovery
    # path, so normal printing keeps its current speed.
    if order.local_status == "CANCELLED" or _wb_order_currently_cancelled(order):
        status_refreshed = _refresh_exact_wb_status_for_label(db, client, order)
        if not _wb_order_currently_cancelled(order):
            _reconcile_local_status(db, order, "WB_LABEL_ACTIVE_STATUS_REPAIR")
        if status_refreshed or order.local_status != "CANCELLED":
            db.commit()
            db.refresh(order)

    if _wb_order_is_cancelled_for_label(order):
        raise HTTPException(status_code=409, detail="Заказ отменён. Этикетка WB для него не печатается.")

    last_item: dict[str, Any] | None = None
    last_error: HTTPException | None = None
    for pause in (0.0, 0.25, 0.55, 0.90):
        if pause:
            time.sleep(pause)
        try:
            image, item = _fetch_wb_order_sticker(db, client, order)
            if item is not None:
                last_item = item
            if image is not None:
                return image, item or last_item
        except HTTPException as exc:
            last_error = exc
            # Authentication/permission/bad-request failures cannot be repaired by
            # immediately repeating the same call. Rate limits and WB 5xx may heal.
            original_status = _wb_api_error_status(exc)
            if original_status in {400, 401, 402, 403}:
                raise

    # If WB returned an empty sticker projection for an otherwise active order,
    # refresh the exact status once and make one final sticker request. This is a
    # print-only recovery path and does not touch CHZ or packing facts.
    if not status_refreshed:
        status_refreshed = _refresh_exact_wb_status_for_label(db, client, order)
        if status_refreshed:
            db.commit()
            db.refresh(order)
            if _wb_order_is_cancelled_for_label(order):
                raise HTTPException(status_code=409, detail="Заказ отменён. Этикетка WB для него не печатается.")
            try:
                image, item = _fetch_wb_order_sticker(db, client, order)
                if item is not None:
                    last_item = item
                if image is not None:
                    return image, item or last_item
            except HTTPException as exc:
                last_error = exc

    if fallback is not None:
        return fallback, last_item
    if last_error is not None and _wb_api_error_status(last_error) not in {429, 500, 502, 503, 504}:
        raise last_error
    return None, last_item


def _fetch_wb_order_sticker_for_print(
    db: Session, client: WBClient, order: MarketplaceOrder, *, fallback: bytes | None = None
) -> tuple[bytes | None, dict[str, Any] | None]:
    """Fetch the physical sticker from WB after a very short generation settle time.

    Immediately after SGTIN/CHZ acceptance WB can briefly return a sticker image whose
    raster geometry is not yet the final one. The manual reprint a moment later is fine.
    Automatic printing therefore waits only until ~350 ms after CHZ attachment and then
    fetches a fresh copy. Normal/manual reprints are not delayed.
    """
    if order.chz_attached_at is not None:
        try:
            attached = order.chz_attached_at
            if attached.tzinfo is None:
                attached = attached.replace(tzinfo=timezone.utc)
            age = max(0.0, (_now() - attached).total_seconds())
            if age < 0.35:
                time.sleep(0.35 - age)
        except Exception:
            # Printing must never fail only because a legacy timestamp is naive/corrupt.
            pass
    return _fetch_wb_order_sticker_resilient(db, client, order, fallback=fallback)


def _fetch_wb_order_sticker_fast_auto(
    db: Session, client: WBClient, order: MarketplaceOrder
) -> tuple[bytes | None, dict[str, Any] | None]:
    """Fast path used only for automatic Print Agent printing after CHZ.

    The CHZ request already performed the authoritative WB write/verification. Do not
    repeat the old long status-recovery loop before every automatic sticker. WB usually
    exposes the sticker immediately; make a few tightly bounded reads and return quickly
    so the Print Agent queue is populated without a multi-second head-of-line delay.
    Manual/browser printing keeps the more conservative resilient path.
    """
    if _wb_order_is_cancelled_for_label(order):
        raise HTTPException(status_code=409, detail="Заказ отменён. Этикетка WB для него не печатается.")

    # A tiny settle window avoids the transient first raster seen directly after SGTIN,
    # but is much shorter than the former 350 ms + long recovery loop.
    if order.chz_attached_at is not None:
        try:
            attached = order.chz_attached_at
            if attached.tzinfo is None:
                attached = attached.replace(tzinfo=timezone.utc)
            age = max(0.0, (_now() - attached).total_seconds())
            if age < 0.10:
                time.sleep(0.10 - age)
        except Exception:
            pass

    last_item: dict[str, Any] | None = None
    last_error: HTTPException | None = None
    # Maximum local sleep is 0.48 s. The previous automatic path could sleep ~2 s
    # and then make an extra WB status request, causing visible label queues to pile up.
    for pause in (0.0, 0.10, 0.16, 0.22):
        if pause:
            time.sleep(pause)
        try:
            image, item = _fetch_wb_order_sticker(db, client, order)
            if item is not None:
                last_item = item
            if image is not None:
                return image, item or last_item
        except HTTPException as exc:
            last_error = exc
            status = _wb_api_error_status(exc)
            # Cancellation, auth and deterministic request errors must surface at once.
            if status in {400, 401, 402, 403, 409}:
                raise
            # 429/5xx get only the remaining short retries.

    if last_error is not None and _wb_api_error_status(last_error) not in {429, 500, 502, 503, 504}:
        raise last_error
    return None, last_item


def _fetch_wb_order_sticker_stable_after_chz(
    db: Session, client: WBClient, order: MarketplaceOrder
) -> tuple[bytes | None, dict[str, Any] | None]:
    """Fetch a stable WB sticker for automatic post-CHZ printing only.

    The manual button works because it is pressed a little later. Automatic printing
    therefore waits for a short minimum age and requires two identical consecutive
    WB sticker payloads. This avoids printing the transient oversized raster while
    keeping the normal/manual path untouched.
    """
    if order.chz_attached_at is not None:
        try:
            attached = order.chz_attached_at
            if attached.tzinfo is None:
                attached = attached.replace(tzinfo=timezone.utc)
            age = max(0.0, (_now() - attached).total_seconds())
            minimum_age = 0.65
            if age < minimum_age:
                time.sleep(minimum_age - age)
        except Exception:
            pass

    previous: bytes | None = None
    last_item: dict[str, Any] | None = None
    for pause in (0.18, 0.22, 0.30, 0.0):
        image, item = _fetch_wb_order_sticker(db, client, order)
        if item is not None:
            last_item = item
        if image is not None:
            if previous is not None and image == previous:
                return image, item or last_item
            previous = image
        if pause > 0:
            time.sleep(pause)
    return previous, last_item


# FC_WB_DEADLOCK_STABILITY_09415G3_20260903
def _refresh_wb_metadata_batch(
    db: Session,
    client: WBClient,
    orders: list[MarketplaceOrder],
    *,
    order_write_key: str | None = None,
) -> dict[str, int]:
    """Refresh WB metadata without external I/O inside a DB transaction.

    0.9.4.15g3 additionally serializes only the short marketplace_orders write phases
    against the background sale-reservation worker. WB HTTP is deliberately outside the
    lock, so packing and normal UI requests are not held behind marketplace latency.
    """
    by_num = {o.external_order_num: o for o in orders if o.external_order_num is not None}
    if not by_num:
        return {"checked": 0, "changed": 0}
    lock_key = order_write_key or f"wb-order-write-client:{orders[0].client_id}"

    meta_nums = list(by_num)
    db.commit()
    changed = 0
    try:
        meta_rows = client.order_meta(meta_nums)
    except HTTPException as exc:
        if exc.status_code == 429:
            return {"checked": 0, "changed": 0}
        raise

    with process_lock(lock_key):
        for item in meta_rows:
            raw_id = item.get("id") or item.get("orderId") or item.get("order_id")
            try:
                num = int(raw_id)
            except (TypeError, ValueError):
                continue
            order = by_num.get(num)
            if order and _apply_wb_order_meta(db, order, item):
                changed += 1
        db.commit()

    sticker_candidates = [
        o for o in orders
        if o.external_order_num is not None and o.supplier_status in {"confirm", "complete"} and not _wb_sticker_data(o)
    ]
    sticker_by_num = {o.external_order_num: o for o in sticker_candidates if o.external_order_num is not None}
    sticker_nums = list(sticker_by_num)
    db.commit()
    for pos in range(0, len(sticker_nums), 100):
        chunk = sticker_nums[pos:pos + 100]
        if not chunk:
            continue
        try:
            stickers = client.order_stickers(chunk)
        except HTTPException as exc:
            if exc.status_code in {400, 409, 429, 502}:
                stickers = []
            else:
                raise
        with process_lock(lock_key):
            for item in stickers:
                raw_id = item.get("orderId") or item.get("id")
                try:
                    num = int(raw_id)
                except (TypeError, ValueError):
                    continue
                order = sticker_by_num.get(num)
                if order and _apply_wb_order_sticker(db, order, item):
                    changed += 1
            db.commit()

    checked_at = _now().isoformat()
    with process_lock(lock_key):
        for order in orders:
            raw = dict(order.raw_data or {})
            raw["_fc_meta_checked_at"] = checked_at
            order.raw_data = raw
            if _reconcile_local_status(db, order, "WB_META_SWEEP"):
                changed += 1
        db.commit()
    return {"checked": len(orders), "changed": changed}


def _reserved_for_order(db: Session, order: MarketplaceOrder) -> list[dict[str, Any]]:
    """Return the pick allocation from the last printed picking list.

    This is the warehouse's reservation/assignment view: it does not mutate stock, but
    tells the picker which location/container was allocated to this WB order.
    """
    if not order.last_picking_list_id:
        return []
    try:
        list_id = uuid.UUID(str(order.last_picking_list_id))
    except Exception:
        return []
    plist = db.get(MarketplacePickingList, list_id)
    if not plist:
        return []
    key = str(order.external_order_id)
    result: list[dict[str, Any]] = []
    for row in plist.rows or []:
        ids = [str(x) for x in (row.get("order_ids") or [])]
        if key not in ids:
            continue
        result.append({
            "location": row.get("location") or "",
            "container": row.get("container") or "",
            "quantity": 1,
            "status": row.get("status") or "",
            "picking_list_id": str(plist.id),
        })
    return result


def _historic_picking_row(db: Session, order: MarketplaceOrder) -> dict[str, Any] | None:
    """Return an already-bound physical source for a PICKING order.

    g5a2 rule: an existing real address/container binding is immutable and must be
    reused. A historical picking-list row that never received a physical source
    (shortage / empty location) is *not* considered an assignment; such an order is
    eligible for a fresh allocation when the next picking list is generated.
    """
    if not order.last_picking_list_id:
        return None

    # Prefer the durable reservation truth over the old XLSX row. This preserves an
    # already-bound cell/box even if an older picking-list JSON is incomplete.
    try:
        latest = latest_reservations_for_orders(
            db, [str(order.id)], statuses=["ACTIVE", "CONSUMING", "PACKING_WRITE_OFF"]
        )
        reservation = latest.get(str(order.id))
    except Exception:
        reservation = None
    if reservation and reservation.get("source_location_id"):
        try:
            location = db.get(Location, uuid.UUID(str(reservation["source_location_id"])))
        except Exception:
            location = None
        container = None
        if reservation.get("source_container_id"):
            try:
                container = db.get(Container, uuid.UUID(str(reservation["source_container_id"])))
            except Exception:
                container = None
        if location is not None:
            sku = db.get(SKU, order.local_sku_id) if order.local_sku_id else None
            return {
                "location": str(location.code or location.barcode or ""),
                "location_id": str(location.id),
                "location_name": str(getattr(location, "name", "") or ""),
                "location_type": str(getattr(location, "location_type", "") or ""),
                "container": str((getattr(container, "name", None) or getattr(container, "barcode", None) or "") if container else ""),
                "container_id": str(container.id) if container else None,
                "container_barcode": str(getattr(container, "barcode", "") or "") if container else "",
                "container_total_quantity": 0.0,
                "article": str(order.article or getattr(sku, "article", "") or ""),
                "size": str(getattr(sku, "size", "") or ""),
                "barcode": str(getattr(sku, "barcode", "") or ""),
                "name": str(getattr(sku, "name", "") or ""),
                "required_quantity": 1.0,
                "quantity": 1.0,
                "required": 1.0,
                "available_quantity": 1.0,
                "available": 1.0,
                "order_ids": [str(order.external_order_id or "")],
                "status": "OK",
                "under_zero": False,
                "reused_from_picking_list_id": str(order.last_picking_list_id),
                "reused_from_active_reservation": True,
            }

    try:
        plist = db.get(MarketplacePickingList, uuid.UUID(str(order.last_picking_list_id)))
    except Exception:
        return None
    if not plist:
        return None
    order_key = str(order.external_order_id or "")
    for source in plist.rows or []:
        ids = [str(x) for x in (source.get("order_ids") or [])]
        if order_key not in ids:
            continue
        row = dict(source)
        status = str(row.get("status") or "").strip().upper()
        # A shortage row or a row without an actual physical source is not a binding.
        # Returning None here lets the current picking-list transaction find live stock
        # and create the missing address reservation. A physical BOX in ПРИЕМКА is valid
        # because it still has a location plus a concrete container id/name.
        if status != "OK" or not (row.get("location_id") or row.get("location")):
            return None
        row["order_ids"] = [order_key]
        row["required_quantity"] = 1.0
        row["quantity"] = 1.0
        row["required"] = 1.0
        row["available_quantity"] = float(row.get("available_quantity", row.get("available", 1)) or 0)
        row["available"] = row["available_quantity"]
        row["under_zero"] = False
        row["status"] = "OK"
        # Never reuse a historical address created for another size/SKU.
        current_sku = db.get(SKU, order.local_sku_id) if order.local_sku_id else None
        card = _product_card_for_order(db, order)
        allowed_codes = _wb_order_allowed_barcodes(order, card)
        row_code = _norm_identity(row.get("barcode"))
        expected_size = _norm_identity(_wb_order_size(order, card, current_sku))
        row_size = _norm_identity(row.get("size"))
        if allowed_codes and row_code and row_code not in allowed_codes:
            return None
        if expected_size and row_size and row_size != expected_size:
            return None
        row["reused_from_picking_list_id"] = str(plist.id)
        return row
    return None



def _historic_picking_rows_batch(db: Session, orders: list[MarketplaceOrder]) -> dict[str, dict[str, Any]]:
    """Resolve reusable PICKING addresses in batches, without mutating stock or orders.

    Reprinting a list from «На сборке» used to call the per-order historic resolver and
    could execute thousands of tiny queries before taking write locks.  This batch path
    reads durable ACTIVE/CONSUMING/PACKING_WRITE_OFF bindings first and falls back to the
    last stored picking-list JSON in memory.  It is intentionally read-only.
    """
    if not orders:
        return {}
    by_order_id = {str(o.id): o for o in orders}
    result: dict[str, dict[str, Any]] = {}
    ids = list(by_order_id)
    try:
        stmt = text("""
            SELECT DISTINCT ON (r.order_id)
              r.order_id,r.source_location_id,r.source_container_id,
              l.code AS location_code,l.name AS location_name,l.location_type,
              c.name AS container_name,c.barcode AS container_barcode,
              s.article,s.size,s.barcode AS sku_barcode,s.name AS sku_name
            FROM fc_stock_reservations r
            LEFT JOIN locations l ON CAST(l.id AS varchar)=r.source_location_id
            LEFT JOIN containers c ON CAST(c.id AS varchar)=r.source_container_id
            LEFT JOIN skus s ON CAST(s.id AS varchar)=r.sku_id
            WHERE r.order_id IN :ids
              AND r.status IN ('ACTIVE','CONSUMING','PACKING_WRITE_OFF')
              AND r.source_location_id IS NOT NULL
            ORDER BY r.order_id,r.updated_at DESC,r.created_at DESC
        """).bindparams(bindparam('ids', expanding=True))
        for row in db.execute(stmt, {'ids': ids}).mappings():
            order = by_order_id.get(str(row.get('order_id') or ''))
            if not order or str(order.local_status or '').upper() != 'PICKING' or not row.get('location_code'):
                continue
            key = str(order.external_order_id or '')
            result[key] = {
                'location': str(row.get('location_code') or ''),
                'location_id': str(row.get('source_location_id') or ''),
                'location_name': str(row.get('location_name') or ''),
                'location_type': str(row.get('location_type') or ''),
                'container': str(row.get('container_name') or row.get('container_barcode') or ''),
                'container_id': str(row.get('source_container_id')) if row.get('source_container_id') else None,
                'container_barcode': str(row.get('container_barcode') or ''),
                'container_total_quantity': 0.0,
                'article': str(order.article or row.get('article') or ''),
                'size': str(row.get('size') or ''),
                'barcode': str(row.get('sku_barcode') or ''),
                'name': str(row.get('sku_name') or ''),
                'required_quantity': 1.0,'quantity': 1.0,'required': 1.0,
                'available_quantity': 1.0,'available': 1.0,
                'order_ids': [key],'status': 'OK','under_zero': False,
                'reused_from_active_reservation': True,
                'reprint_read_only': True,
            }
    except Exception:
        logger.exception('FC_PICKING_REPRINT_BATCH_RESERVATION_READ_FAILED')

    # Batch-load old list JSON for PICKING orders that have no durable address binding.
    remaining = [o for o in orders if str(o.external_order_id or '') not in result and o.last_picking_list_id]
    list_ids: set[uuid.UUID] = set()
    for order in remaining:
        try: list_ids.add(uuid.UUID(str(order.last_picking_list_id)))
        except Exception: pass
    plist_map = {}
    if list_ids:
        plist_map = {str(x.id): x for x in db.execute(
            select(MarketplacePickingList).where(MarketplacePickingList.id.in_(list_ids))
        ).scalars().all()}
    sku_ids = {o.local_sku_id for o in remaining if o.local_sku_id}
    sku_map = {str(x.id): x for x in db.execute(select(SKU).where(SKU.id.in_(sku_ids))).scalars().all()} if sku_ids else {}
    for order in remaining:
        plist = plist_map.get(str(order.last_picking_list_id or ''))
        if not plist: continue
        order_key = str(order.external_order_id or '')
        sku = sku_map.get(str(order.local_sku_id or ''))
        allowed_codes = {str(x or '').strip() for x in (order.barcodes or []) if str(x or '').strip()}
        if sku and str(sku.barcode or '').strip(): allowed_codes.add(str(sku.barcode).strip())
        expected_size = _norm_identity(getattr(sku, 'size', None)) if sku else ''
        for source in plist.rows or []:
            if order_key not in {str(x) for x in (source.get('order_ids') or [])}: continue
            row = dict(source)
            if str(row.get('status') or '').strip().upper() != 'OK' or not (row.get('location_id') or row.get('location')):
                continue
            row_code = str(row.get('barcode') or '').strip()
            row_size = _norm_identity(row.get('size'))
            if allowed_codes and row_code and row_code not in allowed_codes: continue
            if expected_size and row_size and row_size != expected_size: continue
            row.update({
                'order_ids':[order_key],'required_quantity':1.0,'quantity':1.0,'required':1.0,
                'available_quantity':float(row.get('available_quantity', row.get('available',1)) or 0),
                'available':float(row.get('available_quantity', row.get('available',1)) or 0),
                'under_zero':False,'status':'OK','reused_from_picking_list_id':str(plist.id),
                'reprint_read_only':True,
            })
            result[order_key] = row
            break
    return result


def _readonly_picking_rows(
    db: Session, *, orders: list[MarketplaceOrder], warehouse_id: uuid.UUID | None,
    cred: MarketplaceCredential, now: datetime,
    planned_skus: dict[str, SKU] | None = None,
    planned_meta: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build an Excel allocation without DB writes.

    Preview/reprint must be safe even while WB synchronization is busy.  Existing PICKING
    bindings are reused in one batch.  Orders without a binding are allocated only in the
    in-memory document from currently physical-minus-bound stock; no reservation/status/
    picking-list row is written by this path.
    """
    historic = _historic_picking_rows_batch(db, orders)
    historic_rows = [historic[str(o.external_order_id or '')] for o in orders if str(o.external_order_id or '') in historic]
    remaining = [o for o in orders if str(o.external_order_id or '') not in historic]
    if not remaining:
        return historic_rows

    # FC_K9R9_READONLY_ALIAS_UNION_TRUTH
    # Preview and commit use one physical identity plan.  A write request may pass the
    # already-computed plan here so 500+ orders are not resolved twice inside the same
    # status transaction.  Pure preview still computes the exact same read-only plan.
    if planned_skus is None or planned_meta is None:
        planned_skus, planned_meta = _resolve_picking_sku_plan(
            db, remaining, warehouse_id, cred.client_id
        )
    effective_sku_id_by_order: dict[str, uuid.UUID] = {}
    sku_ids: set[uuid.UUID] = set()
    for order in remaining:
        planned = planned_skus.get(str(order.id))
        effective = planned.id if planned else order.local_sku_id
        if effective:
            effective_sku_id_by_order[str(order.id)] = effective
            sku_ids.add(effective)
    sku_map = {x.id: x for x in db.execute(select(SKU).where(SKU.id.in_(sku_ids))).scalars().all()} if sku_ids else {}
    stock_map: dict[uuid.UUID, list[dict[str, Any]]] = {sid: [] for sid in sku_ids}
    if warehouse_id and sku_ids:
        stock_rows = db.execute(
            select(InventoryBalance, Location, Container)
            .join(Location, Location.id == InventoryBalance.location_id)
            .outerjoin(Container, Container.id == InventoryBalance.container_id)
            .where(
                InventoryBalance.client_id == cred.client_id,
                InventoryBalance.sku_id.in_(sku_ids),
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.quantity > 0,
            )
            .order_by(InventoryBalance.sku_id, Location.code, Container.name, Container.barcode)
        ).all()
        container_ids = {container.id for _balance,_loc,container in stock_rows if container}
        container_totals: dict[uuid.UUID,float] = {}
        if container_ids:
            container_totals = {cid: float(qty or 0) for cid,qty in db.execute(
                select(InventoryBalance.container_id, func.coalesce(func.sum(InventoryBalance.quantity),0))
                .where(InventoryBalance.client_id==cred.client_id,InventoryBalance.warehouse_id==warehouse_id,
                       InventoryBalance.container_id.in_(container_ids),InventoryBalance.quantity>0)
                .group_by(InventoryBalance.container_id)
            ).all()}
        # FC_K9_BATCH_READONLY_BUCKET_FREE
        # Preview/reprint must remain fast for hundreds of orders.  The old helper
        # executed three SQL statements per physical bucket, which could turn a large
        # read-only Excel into thousands of queries and hit the browser timeout.  Load
        # storage-only and live bound holds for every bucket in two aggregate queries.
        storage_by_bucket: dict[tuple[str,str,str], float] = {}
        reserve_by_bucket: dict[tuple[str,str,str], float] = {}
        ids_as_text=[str(x) for x in sku_ids]
        if ids_as_text:
            try:
                storage_stmt=text("""
                    SELECT CAST(sku_id AS varchar) AS sku_id,
                           CAST(location_id AS varchar) AS location_id,
                           COALESCE(container_key,'') AS container_key,
                           COALESCE(SUM(storage_only),0) AS storage_only
                    FROM fc_stocks_v2
                    WHERE client_id=:client AND warehouse_id=:warehouse
                      AND CAST(sku_id AS varchar) IN :ids
                    GROUP BY CAST(sku_id AS varchar),CAST(location_id AS varchar),COALESCE(container_key,'')
                """).bindparams(bindparam('ids',expanding=True))
                for x in db.execute(storage_stmt,{'client':str(cred.client_id),'warehouse':str(warehouse_id),'ids':ids_as_text}).mappings():
                    storage_by_bucket[(str(x['sku_id']),str(x['location_id']),str(x['container_key'] or ''))]=max(0.0,float(x['storage_only'] or 0))
            except Exception:
                logger.exception('FC_PICKING_PREVIEW_STORAGE_BATCH_READ_FAILED')
            try:
                reserve_stmt=text("""
                    SELECT CAST(r.sku_id AS varchar) AS sku_id,
                           r.source_location_id AS location_id,
                           COALESCE(r.source_container_id,'') AS container_key,
                           COALESCE(SUM(CASE
                             WHEN r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL THEN 0
                             ELSE r.quantity END),0) AS reserved
                    FROM fc_stock_reservations r
                    LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
                    LEFT JOIN inventory_operations io
                      ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar) AND COALESCE(io.status,'COMPLETED')='COMPLETED'
                     AND io.idempotency_key=('stock-core-v2-pack-' || CAST(o.id AS varchar) || '-' || CAST(r.id AS varchar))
                    WHERE r.client_id=:client AND r.warehouse_id=:warehouse
                      AND CAST(r.sku_id AS varchar) IN :ids
                      AND r.source_location_id IS NOT NULL
                      AND r.status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
                    GROUP BY CAST(r.sku_id AS varchar),r.source_location_id,COALESCE(r.source_container_id,'')
                """).bindparams(bindparam('ids',expanding=True))
                for x in db.execute(reserve_stmt,{'client':str(cred.client_id),'warehouse':str(warehouse_id),'ids':ids_as_text}).mappings():
                    reserve_by_bucket[(str(x['sku_id']),str(x['location_id']),str(x['container_key'] or ''))]=max(0.0,float(x['reserved'] or 0))
            except Exception:
                logger.exception('FC_PICKING_PREVIEW_RESERVE_BATCH_READ_FAILED')

        for balance,loc,container in stock_rows:
            bucket=(str(balance.sku_id),str(loc.id),str(container.id) if container else '')
            physical=max(0.0,float(balance.quantity or 0))
            storage=min(physical,storage_by_bucket.get(bucket,0.0))
            bound=reserve_by_bucket.get(bucket,0.0)
            free=max(0.0,physical-storage-bound)
            if free<=0: continue
            stock_map.setdefault(balance.sku_id,[]).append({
                'location_id':str(loc.id),'location':loc.code,'location_name':loc.name,
                'location_type':loc.location_type,'container_id':str(container.id) if container else None,
                'container':(container.name or container.barcode) if container else None,
                'container_barcode':container.barcode if container else None,
                'container_total_quantity':container_totals.get(container.id,0.0) if container else 0.0,
                'quantity':free,'available_quantity':free,'free_quantity':free,
            })

    source_orders: list[dict[str,Any]]=[]; shortage_rows: list[dict[str,Any]]=[]
    for order in remaining:
        effective_sku_id = effective_sku_id_by_order.get(str(order.id))
        sku = sku_map.get(effective_sku_id) if effective_sku_id else None
        key=str(order.external_order_id or '')
        stock=[]
        for row in stock_map.get(effective_sku_id,[]) if effective_sku_id else []:
            code=str(row.get('location') or '').strip().upper(); has_box=bool(row.get('container_id') or row.get('container'))
            if code in SYSTEM_LOCATION_CODES and code!='ПРИЕМКА': continue
            clean=dict(row); clean['virtual_receiving_fallback']=code=='ПРИЕМКА' and not has_box
            stock.append(clean)
        real=[x for x in stock if not x.get('virtual_receiving_fallback')]; receiving=[x for x in stock if x.get('virtual_receiving_fallback')]
        free_stock=real+receiving
        # FC_K9R9_PICKING_DISPLAY_RESOLVED_ALIAS_SIZE
        # The plan may have merged barcode aliases across historical local WB-card
        # generations.  Reuse that exact resolved size here instead of asking one newest
        # card again and potentially losing the second valid barcode.
        plan_meta = dict((planned_meta or {}).get(str(order.id)) or {})
        resolved_size = str(plan_meta.get("resolved_size") or "").strip()
        if not resolved_size:
            card = _product_card_for_order(db, order)
            resolved_size = _wb_order_size(order, card, sku)
        source_codes = sorted(_wb_order_source_barcodes(order))
        prepared={
            'order_id':key,'sku_id':str(sku.id) if sku else None,'article':order.article or getattr(sku,'article',None),
            'size':resolved_size or getattr(sku,'size',None),
            'barcode':getattr(sku,'barcode',None) or (source_codes[0] if source_codes else None),
            'name':getattr(sku,'name',None) or order.article,'created_at':(order.external_created_at or order.created_at).isoformat() if (order.external_created_at or order.created_at) else None,
            'age_hours':round(marketplace_order_age_hours(order,now),2),'sla_critical':bool(marketplace_order_age_hours(order,now)>=300),
            'stock':free_stock,
        }
        try:
            from app.services.zipline_tz_core import sla_risk_band
            risk = sla_risk_band(prepared['age_hours'], sla_hours=24.0)
            # Keep legacy 300h critical as RED override for long-lived WB queues.
            if prepared['sla_critical']:
                risk = {**risk, 'risk': 'RED', 'sla_critical': True}
            prepared['sla_risk'] = risk['risk']
            prepared['sla_remaining_hours'] = risk['remaining_hours']
            prepared['sla_hours'] = risk['sla_hours']
        except Exception:
            prepared['sla_risk'] = 'RED' if prepared['sla_critical'] else 'GREEN'
        if free_stock:
            source_orders.append(prepared)
        else:
            shortage_rows.append({
                'location':'','location_id':None,'location_name':'','location_type':'','container':'','container_id':None,
                'container_barcode':'','container_total_quantity':0.0,'article':prepared.get('article') or '',
                'size':prepared.get('size') or '','barcode':prepared.get('barcode') or '','name':prepared.get('name') or '',
                'required_quantity':1.0,'quantity':0.0,'required':1.0,'available_quantity':0.0,'available':0.0,
                'order_ids':[key],'status':'НЕТ НА ОСТАТКАХ' if sku else 'SKU НЕ СОПОСТАВЛЕН','under_zero':True,
                'age_hours':prepared['age_hours'],'max_age_hours':prepared['age_hours'],'sla_critical':prepared['sla_critical'],
                'synthetic_shortage':True,'reprint_read_only':True,
            })
    allocated=allocate_picking_rows(source_orders)
    for row in allocated: row['reprint_read_only']=True
    return historic_rows+allocated+shortage_rows

def _product_card_for_order(db: Session, order: MarketplaceOrder) -> MarketplaceProduct | None:
    """Return the newest local WB card for this product across rotated API keys.

    Picking must use the same local-card truth as the inventory page.  A stale card
    attached to an old/current credential can miss a second barcode of the same chrtId
    and make real stock look unavailable.  Exact nmID wins; article is only a fallback.
    No WB network request is performed here.
    """
    raw = order.raw_data or {}
    nm_id = raw.get("nmId") or raw.get("nmID") or raw.get("nm_id")
    try:
        nm_value = int(nm_id) if nm_id is not None else None
    except (TypeError, ValueError):
        nm_value = None
    base = [
        MarketplaceProduct.client_id == order.client_id,
        MarketplaceProduct.provider == "WB",
    ]
    if nm_value is not None:
        exact = db.execute(
            select(MarketplaceProduct).where(
                *base, MarketplaceProduct.external_product_id == nm_value,
            ).order_by(
                MarketplaceProduct.updated_at.desc(),
                MarketplaceProduct.created_at.desc(),
            ).limit(1)
        ).scalars().first()
        if exact:
            return exact
    article = str(order.article or "").strip()
    if not article:
        return None
    return db.execute(
        select(MarketplaceProduct).where(
            *base, MarketplaceProduct.vendor_code == article,
        ).order_by(
            MarketplaceProduct.updated_at.desc(),
            MarketplaceProduct.created_at.desc(),
        ).limit(1)
    ).scalars().first()


def _wb_order_size(order: MarketplaceOrder, product_card: MarketplaceProduct | None, sku: SKU | None) -> str | None:
    raw = order.raw_data or {}
    raw_chrt = raw.get("chrtId") or raw.get("chrtID") or raw.get("chrt_id")
    order_barcodes = set(_wb_order_source_barcodes(order))
    sizes = [x for x in ((product_card.sizes if product_card else None) or []) if isinstance(x, dict)]
    # The WB order itself points to a concrete characteristic/size through chrtId.
    # Prefer that over a previously linked local SKU because old imports may have
    # linked an article to the wrong size before exact-barcode priority was added.
    if raw_chrt is not None:
        for item in sizes:
            if str(item.get("chrtID") or item.get("chrtId") or "") == str(raw_chrt):
                return str(item.get("techSize") or item.get("wbSize") or "").strip() or None
    if order_barcodes:
        for item in sizes:
            size_codes = {_norm_identity(x) for x in (item.get("skus") or []) if _norm_identity(x)}
            if order_barcodes & size_codes:
                return str(item.get("techSize") or item.get("wbSize") or "").strip() or None
    # Some WB history payloads already contain a human-readable size. Keep it as a
    # fallback until the Content API card has been imported.
    raw_size = _wb_raw_order_size(order)
    if raw_size:
        return raw_size
    if sku and str(sku.size or "").strip():
        return str(sku.size).strip()
    return None


def _product_inventory_summaries(
    db: Session, products: list[MarketplaceProduct], warehouse_id: uuid.UUID | None = None
) -> tuple[dict[uuid.UUID, dict[str, Any]], dict[uuid.UUID, SKU]]:
    if not products:
        return {}, {}
    client_id = products[0].client_id
    vendors = {str(p.vendor_code).strip() for p in products if str(p.vendor_code or "").strip()}
    all_codes = {str(code) for p in products for code in (p.barcodes or []) if code}
    clauses = []
    if vendors:
        clauses.extend([SKU.article.in_(vendors), SKU.sku_code.in_(vendors)])
    if all_codes:
        clauses.append(SKU.barcode.in_(all_codes))
    if not clauses:
        return {p.id: {"quantity": 0.0, "sku_count": 0} for p in products}, {}
    skus = db.execute(select(SKU).where(SKU.client_id == client_id, or_(*clauses))).scalars().all()
    sku_by_id = {sku.id: sku for sku in skus}
    vendor_map: dict[str, set[uuid.UUID]] = {}
    barcode_map: dict[str, set[uuid.UUID]] = {}
    for sku in skus:
        for value in (sku.article, sku.sku_code):
            key = str(value or "").strip()
            if key:
                vendor_map.setdefault(key, set()).add(sku.id)
        if sku.barcode:
            barcode_map.setdefault(str(sku.barcode), set()).add(sku.id)
    product_sku_ids: dict[uuid.UUID, set[uuid.UUID]] = {}
    all_sku_ids: set[uuid.UUID] = set()
    for product in products:
        ids: set[uuid.UUID] = set()
        vendor = str(product.vendor_code or "").strip()
        if vendor:
            ids.update(vendor_map.get(vendor, set()))
        for code in product.barcodes or []:
            ids.update(barcode_map.get(str(code), set()))
        product_sku_ids[product.id] = ids
        all_sku_ids.update(ids)
    qty_by_sku: dict[uuid.UUID, float] = {}
    if all_sku_ids:
        stmt = select(InventoryBalance.sku_id, func.coalesce(func.sum(InventoryBalance.quantity), 0)).where(
            InventoryBalance.client_id == client_id,
            InventoryBalance.sku_id.in_(all_sku_ids),
            InventoryBalance.quantity > 0,
        )
        if warehouse_id is not None:
            stmt = stmt.where(InventoryBalance.warehouse_id == warehouse_id)
        for sku_id, qty in db.execute(stmt.group_by(InventoryBalance.sku_id)).all():
            qty_by_sku[sku_id] = float(qty or 0)
    summaries = {
        product.id: {
            "quantity": sum(qty_by_sku.get(sku_id, 0.0) for sku_id in ids),
            "sku_count": len(ids),
        }
        for product, ids in ((p, product_sku_ids.get(p.id, set())) for p in products)
    }
    return summaries, sku_by_id


def _order_response(db: Session, order: MarketplaceOrder, warehouse_id: uuid.UUID | None = None, cred: MarketplaceCredential | None = None) -> dict:
    sku = db.get(SKU, order.local_sku_id) if order.local_sku_id else None
    stock = _stock_for_sku(db, sku, warehouse_id)
    product_card = _product_card_for_order(db, order)
    order_size = _wb_order_size(order, product_card, sku)
    settings = _wb_settings(cred, warehouse_id) if cred else {}
    wh_info = settings.get(str(order.wb_warehouse_id)) or {}
    sticker = _wb_sticker_data(order)
    return {
        "id": str(order.id),
        "external_order_id": order.external_order_num or order.external_order_id,
        "order_uid": order.order_uid,
        "article": order.article,
        "size": order_size,
        "barcodes": order.barcodes or [],
        "required_meta": order.required_meta or [],
        "optional_meta": order.optional_meta or [],
        "sgtin_supported": "sgtin" in set((order.required_meta or []) + (order.optional_meta or [])),
        "supplier_status": order.supplier_status,
        "wb_status": order.wb_status,
        "wb_status_name": WB_STATUS_NAMES.get(order.wb_status or "", order.wb_status),
        "local_status": order.local_status,
        "local_status_name": LOCAL_STATUS_NAMES.get(order.local_status, order.local_status),
        "manual_status_override": bool(getattr(order, "manual_status_override", False)),
        "is_cancellable": order.is_cancellable,
        "supply_id": order.supply_id,
        "wb_warehouse_id": order.wb_warehouse_id,
        "wb_warehouse_name": wh_info.get("name"),
        "wb_warehouse_mode": _wb_mode(cred, order.wb_warehouse_id, warehouse_id) if cred else None,
        "product_scanned_barcode": order.product_scanned_barcode,
        "product_scanned_at": order.product_scanned_at,
        "chz_attached": bool(order.chz_code),
        "chz_pending_wb": _has_pending_wb_chz(order),
        "chz_pending_reason": ((order.raw_data or {}).get("_fc_chz_pending_wb") or {}).get("reason") if isinstance((order.raw_data or {}).get("_fc_chz_pending_wb"), dict) else None,
        "chz_pending_error": ((order.raw_data or {}).get("_fc_chz_pending_wb") or {}).get("last_error") if isinstance((order.raw_data or {}).get("_fc_chz_pending_wb"), dict) else None,
        "chz_preview": (order.chz_code[:18] + "…" + order.chz_code[-6:]) if order.chz_code and len(order.chz_code) > 28 else order.chz_code,
        "chz_display": order.chz_code.replace("\x1d", "␝") if order.chz_code else None,
        "marking_code": order.chz_code.replace("\x1d", "␝") if order.chz_code else None,
        "order_sticker": {
            "part_a": sticker.get("partA"),
            "part_b": sticker.get("partB"),
            "barcode": sticker.get("barcode"),
        } if sticker else None,
        "order_sticker_available": bool(sticker),
        "label_generated": bool(order.label_generated_at),
        "label_generated_at": order.label_generated_at,
        "label_print_count": order.label_print_count,
        "assembled_at": order.assembled_at,
        "picking_list_generated_at": order.picking_list_generated_at,
        "last_picking_list_id": order.last_picking_list_id,
        "packed_at": order.packed_at,
        "created_at": order.external_created_at or order.created_at,
        "last_synced_at": order.last_synced_at,
        "local_sku": {
            "id": str(sku.id),
            "sku_code": sku.sku_code,
            "article": sku.article,
            "name": sku.name,
            "size": sku.size,
            "barcode": sku.barcode,
        } if sku else None,
        "product_card": {
            "id": str(product_card.id),
            "nm_id": product_card.external_product_id,
            "title": product_card.title,
            "brand": product_card.brand,
            "vendor_code": product_card.vendor_code,
            "size": order_size,
            "photo": ((product_card.photos or [{}])[0].get("big") or (product_card.photos or [{}])[0].get("c516x688") or (product_card.photos or [{}])[0].get("square")) if product_card.photos else None,
        } if product_card else None,
        "stock": stock,
        "reserved": _reserved_for_order(db, order),
    }


def _wb_package_metrics(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Extract package dimensions/weight from WB Content API without binding to one schema revision.

    WB currently exposes package dimensions in centimetres for cards, but older/newer
    payloads have used slightly different key names. Keep this parser tolerant so the
    packing UI can estimate a standard 60x40x40 carton without making an extra WB call.
    """
    raw = raw if isinstance(raw, dict) else {}
    dims = raw.get("dimensions") or raw.get("dimension") or raw.get("packageDimensions") or {}
    if not isinstance(dims, dict):
        dims = {}

    def number(*values):
        for value in values:
            if value in (None, ""):
                continue
            try:
                parsed = float(str(value).replace(",", "."))
                if parsed > 0:
                    return parsed
            except Exception:
                continue
        return None

    length = number(dims.get("length"), dims.get("lengthCm"), raw.get("length"), raw.get("lengthCm"))
    width = number(dims.get("width"), dims.get("widthCm"), raw.get("width"), raw.get("widthCm"))
    height = number(dims.get("height"), dims.get("heightCm"), raw.get("height"), raw.get("heightCm"))
    weight = number(
        dims.get("weightBrutto"), dims.get("weight"), dims.get("weightKg"),
        raw.get("weightBrutto"), raw.get("weight"), raw.get("weightKg"), raw.get("packageWeight"),
    )
    volume_cm3 = (length * width * height) if length and width and height else None
    return {
        "length_cm": length, "width_cm": width, "height_cm": height,
        "weight_kg": weight, "volume_cm3": volume_cm3,
        "complete": bool(volume_cm3),
    }


def _packing_scan_order_response(db: Session, order: MarketplaceOrder, warehouse_id: uuid.UUID | None = None) -> dict:
    """Ultra-small LOCAL-DB response for the product-scan hot path.

    Packing is driven by an existing WB order, never by the current inventory number.
    Do not load product cards or scan every stock bucket here; those are reporting views
    and made a USB scanner wait behind unrelated local queries.
    """
    sku = db.get(SKU, order.local_sku_id) if order.local_sku_id else None
    latest = latest_reservations_for_orders(
        db, [order.id], statuses=["SALE_RESERVED", "ACTIVE", "CONSUMING", "CONSUMED"]
    ).get(str(order.id))
    reserved: list[dict[str, Any]] = []
    if latest:
        location = container = None
        if latest.get("source_location_id"):
            try:
                loc = db.get(Location, uuid.UUID(str(latest["source_location_id"])))
                location = loc.code if loc else None
            except Exception:
                location = None
        if latest.get("source_container_id"):
            try:
                cont = db.get(Container, uuid.UUID(str(latest["source_container_id"])))
                container = (cont.name or cont.barcode) if cont else None
            except Exception:
                container = None
        reserved.append({
            "location": location or ("Резерв склада" if str(latest.get("status") or "").upper() == "SALE_RESERVED" else ""),
            "container": container or "",
            "quantity": float(latest.get("quantity") or 1),
            "status": str(latest.get("status") or ""),
        })
    return {
        "id": str(order.id),
        "external_order_id": order.external_order_num or order.external_order_id,
        "article": order.article,
        "barcodes": order.barcodes or [],
        "local_status": order.local_status,
        "local_sku": {
            "id": str(sku.id), "sku_code": sku.sku_code, "article": sku.article,
            "name": sku.name, "size": sku.size, "barcode": sku.barcode,
        } if sku else None,
        "stock": [],
        "stock_unchecked": True,
        "stock_policy": "ORDER_EXISTS_PACK_ALLOWED",
        "reserved": reserved,
        "package_metrics": {},
        "has_real_picking_list": bool(order.last_picking_list_id),
    }



def _resolve_picking_sku_plan(
    db: Session,
    orders: list[MarketplaceOrder],
    warehouse_id: uuid.UUID | None,
    client_id: uuid.UUID,
) -> tuple[dict[str, SKU], dict[str, dict[str, Any]]]:
    """Resolve a physical SKU for every order without mutating the database.

    This is the common identity source for BOTH preview Excel and the real
    NEW -> PICKING transition.  The inventory page already knows that one WB size can
    expose several barcodes; picking now unions exact-size aliases across local card
    generations and rotated credentials.  Existing SALE_RESERVED sku_id is a strong hint because
    that is the identity that already protected the order from oversell at arrival.

    Only exact order barcode / same WB chrtId aliases / exact article+exact-size are
    accepted.  Article-only guessing is deliberately forbidden.
    """
    if not warehouse_id:
        return {}, {}
    candidates = [
        o for o in orders
        if str(o.provider or "").upper() == "WB" and o.client_id == client_id
    ]
    if not candidates:
        return {}, {}

    # FC_K9R9_WB_ALIAS_UNION
    # Read every local generation for the same WB product.  WB can keep two valid
    # barcodes for one chrtId/size while an older local card contains one alias and the
    # newest card contains the other.  Picking must union aliases for the exact size,
    # never silently discard a physical barcode because a newer card row replaced it.
    nm_ids: set[int] = set()
    articles: set[str] = set()
    for order in candidates:
        raw = order.raw_data if isinstance(order.raw_data, dict) else {}
        nm = raw.get("nmId") or raw.get("nmID") or raw.get("nm_id")
        try:
            if nm is not None:
                nm_ids.add(int(nm))
        except (TypeError, ValueError):
            pass
        article = str(order.article or "").strip()
        if article:
            articles.add(article)
    clauses = []
    if nm_ids:
        clauses.append(MarketplaceProduct.external_product_id.in_(nm_ids))
    if articles:
        clauses.append(MarketplaceProduct.vendor_code.in_(articles))
    cards: list[MarketplaceProduct] = []
    if clauses:
        cards = db.execute(
            select(MarketplaceProduct).options(load_only(
                MarketplaceProduct.id,
                MarketplaceProduct.client_id,
                MarketplaceProduct.external_product_id,
                MarketplaceProduct.vendor_code,
                MarketplaceProduct.sizes,
                MarketplaceProduct.updated_at,
                MarketplaceProduct.created_at,
            )).where(
                MarketplaceProduct.client_id == client_id,
                MarketplaceProduct.provider == "WB",
                or_(*clauses),
            ).order_by(
                MarketplaceProduct.updated_at.desc(),
                MarketplaceProduct.created_at.desc(),
            )
        ).scalars().all()

    cards_by_nm: dict[int, list[MarketplaceProduct]] = defaultdict(list)
    cards_by_article: dict[str, list[MarketplaceProduct]] = defaultdict(list)
    for card in cards:
        if card.external_product_id is not None:
            try:
                cards_by_nm[int(card.external_product_id)].append(card)
            except (TypeError, ValueError):
                pass
        code = _norm_identity(card.vendor_code)
        if code:
            cards_by_article[code].append(card)

    def cards_for(order: MarketplaceOrder) -> list[MarketplaceProduct]:
        raw = order.raw_data if isinstance(order.raw_data, dict) else {}
        nm = raw.get("nmId") or raw.get("nmID") or raw.get("nm_id")
        try:
            nm_value = int(nm) if nm is not None else None
        except (TypeError, ValueError):
            nm_value = None
        if nm_value is not None and cards_by_nm.get(nm_value):
            return cards_by_nm[nm_value]
        return cards_by_article.get(_norm_identity(order.article), [])

    # Only SKUs that really have physical stock in the selected FulfillCore warehouse.
    stocked_rows = db.execute(
        select(SKU, func.coalesce(func.sum(InventoryBalance.quantity), 0).label("physical_qty"))
        .join(InventoryBalance, InventoryBalance.sku_id == SKU.id)
        .where(
            SKU.client_id == client_id,
            InventoryBalance.client_id == client_id,
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.quantity > 0,
        )
        .group_by(SKU.id)
    ).all()
    stocked = [sku for sku, _qty in stocked_rows]
    if not stocked:
        return {}, {}
    physical_by_sku = {str(sku.id): float(qty or 0) for sku, qty in stocked_rows}
    sku_by_id = {str(sku.id): sku for sku in stocked}

    # Batch subtract storage-only and already address-bound live reservations.  Do NOT
    # subtract SALE_RESERVED here: those are precisely the NEW units being assigned now.
    sku_id_text = list(physical_by_sku)
    storage_by_sku: dict[str, float] = {}
    bound_by_sku: dict[str, float] = {}
    if sku_id_text:
        try:
            stmt = text("""
                SELECT CAST(sku_id AS varchar) AS sku_id,
                       COALESCE(SUM(storage_only),0) AS storage_only
                FROM fc_stocks_v2
                WHERE client_id=:client AND warehouse_id=:warehouse
                  AND CAST(sku_id AS varchar) IN :ids
                GROUP BY CAST(sku_id AS varchar)
            """).bindparams(bindparam("ids", expanding=True))
            for row in db.execute(stmt, {
                "client": str(client_id), "warehouse": str(warehouse_id), "ids": sku_id_text,
            }).mappings():
                storage_by_sku[str(row["sku_id"])] = max(0.0, float(row["storage_only"] or 0))
        except Exception:
            logger.exception("FC_PICKING_ALIAS_STORAGE_BATCH_READ_FAILED")
        try:
            stmt = text("""
                SELECT CAST(r.sku_id AS varchar) AS sku_id,
                       COALESCE(SUM(CASE
                         WHEN r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL THEN 0
                         ELSE r.quantity END),0) AS reserved
                FROM fc_stock_reservations r
                LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
                LEFT JOIN inventory_operations io
                  ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar)
                 AND COALESCE(io.status,'COMPLETED')='COMPLETED'
                 AND io.idempotency_key=('stock-core-v2-pack-' || CAST(o.id AS varchar) || '-' || CAST(r.id AS varchar))
                WHERE r.client_id=:client AND r.warehouse_id=:warehouse
                  AND CAST(r.sku_id AS varchar) IN :ids
                  AND r.source_location_id IS NOT NULL
                  AND r.status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
                GROUP BY CAST(r.sku_id AS varchar)
            """).bindparams(bindparam("ids", expanding=True))
            for row in db.execute(stmt, {
                "client": str(client_id), "warehouse": str(warehouse_id), "ids": sku_id_text,
            }).mappings():
                bound_by_sku[str(row["sku_id"])] = max(0.0, float(row["reserved"] or 0))
        except Exception:
            logger.exception("FC_PICKING_ALIAS_RESERVE_BATCH_READ_FAILED")

    remaining_capacity: dict[str, float] = {
        sid: max(0.0, physical - min(physical, storage_by_sku.get(sid, 0.0)) - bound_by_sku.get(sid, 0.0))
        for sid, physical in physical_by_sku.items()
    }
    try:
        latest = latest_reservations_for_orders(db, [str(o.id) for o in candidates])
    except Exception:
        latest = {}

    def identity_match(
        order: MarketplaceOrder, sku: SKU, allowed_codes: set[str], size_name: str,
    ) -> bool:
        barcode = _norm_identity(sku.barcode)
        if barcode and barcode in allowed_codes:
            return True
        article = _norm_identity(order.article)
        return bool(
            article and size_name
            and article in {_norm_identity(sku.article), _norm_identity(sku.sku_code)}
            and _norm_identity(sku.size) == size_name
        )

    def choose(rows: list[SKU], preferred_ids: list[str] | None = None, require_capacity: bool = True) -> SKU | None:
        unique = {str(x.id): x for x in rows}
        if not unique:
            return None
        for preferred_id in preferred_ids or []:
            if preferred_id in unique and (not require_capacity or remaining_capacity.get(preferred_id, 0.0) >= 1.0):
                return unique[preferred_id]
        values = list(unique.values())
        if require_capacity:
            values = [x for x in values if remaining_capacity.get(str(x.id), 0.0) >= 1.0]
        if not values:
            return None
        values.sort(key=lambda x: (
            -remaining_capacity.get(str(x.id), 0.0),
            -physical_by_sku.get(str(x.id), 0.0),
            str(x.id),
        ))
        return values[0]

    plan: dict[str, SKU] = {}
    meta: dict[str, dict[str, Any]] = {}
    # Caller already supplies orders in picking priority order; preserve it exactly.
    for order in candidates:
        direct_codes = set(_wb_order_source_barcodes(order))
        allowed_codes, resolved_size, alias_mode = _wb_order_alias_profile(order, cards_for(order))
        size_name = _norm_identity(resolved_size)
        article_name = _norm_identity(order.article)
        current_id = str(order.local_sku_id or "") or None
        current = sku_by_id.get(current_id or "")
        current_valid = bool(current and identity_match(order, current, allowed_codes, size_name))
        previous = latest.get(str(order.id)) or {}
        previous_status = str(previous.get("status") or "").upper()
        previous_sku_id = str(previous.get("sku_id") or "") or None
        previous_sku = sku_by_id.get(previous_sku_id or "")
        previous_valid = bool(previous_sku and identity_match(order, previous_sku, allowed_codes, size_name))
        has_bound_address = bool(
            previous_status in {"ACTIVE", "CONSUMING", "PACKING_WRITE_OFF"}
            and previous.get("source_location_id")
        )

        exact_rows = [x for x in stocked if _norm_identity(x.barcode) in direct_codes] if direct_codes else []
        # FC_K9R9_TRUST_WB_ALIAS_OVER_STALE_LOCAL_META
        # Exact WB size aliases are stronger identity evidence than local article/size
        # text.  Do not narrow them again: the second physical barcode may have stale or
        # blank local metadata even though WB says it is the same chrtId.
        alias_rows = [x for x in stocked if _norm_identity(x.barcode) in allowed_codes] if allowed_codes else []
        exact_size_rows = [
            x for x in stocked
            if article_name and size_name
            and article_name in {_norm_identity(x.article), _norm_identity(x.sku_code)}
            and _norm_identity(x.size) == size_name
        ]

        preferred = [x for x in (previous_sku_id, current_id) if x]
        candidate: SKU | None = None
        mode = ""
        # Existing address-bound reservation is immutable. Historic-row code normally
        # removes it before this helper, but preserving it here is a final safety net.
        if has_bound_address and previous_valid:
            candidate = previous_sku
            mode = "EXISTING_BOUND_RESERVATION"
        # A valid current link is stable once a write path has persisted the plan.
        # Otherwise the existing SALE_RESERVED sku is the strongest unbound hint.
        if candidate is None and current_valid and remaining_capacity.get(current_id or "", 0.0) >= 1.0:
            candidate = current
            mode = "CURRENT_EXACT_IDENTITY"
        if candidate is None and previous_status == "SALE_RESERVED" and previous_valid and remaining_capacity.get(previous_sku_id or "", 0.0) >= 1.0:
            candidate = previous_sku
            mode = "EXISTING_SALE_RESERVED_SKU"
        if candidate is None:
            candidate = choose(exact_rows, preferred)
            if candidate: mode = "ORDER_BARCODE"
        if candidate is None:
            candidate = choose(alias_rows, preferred)
            if candidate: mode = "WB_SIZE_BARCODE_ALIAS"
        if candidate is None:
            candidate = choose(exact_size_rows, preferred)
            if candidate: mode = "ARTICLE_EXACT_SIZE_POSITIVE_STOCK"

        # Keep a truthful identity for shortage output after capacity is exhausted.
        if candidate is None and previous_valid:
            candidate = previous_sku
            mode = "EXISTING_RESERVE_IDENTITY_NO_FREE_CAPACITY"
        if candidate is None and current_valid:
            candidate = current
            mode = "CURRENT_IDENTITY_NO_FREE_CAPACITY"
        if candidate is None:
            candidate = choose(exact_rows, preferred, require_capacity=False)
            if candidate: mode = "ORDER_BARCODE_NO_FREE_CAPACITY"
        if candidate is None:
            candidate = choose(alias_rows, preferred, require_capacity=False)
            if candidate: mode = "WB_SIZE_ALIAS_NO_FREE_CAPACITY"
        if candidate is None:
            candidate = choose(exact_size_rows, preferred, require_capacity=False)
            if candidate: mode = "ARTICLE_EXACT_SIZE_NO_FREE_CAPACITY"
        if candidate is None:
            continue

        chosen_id = str(candidate.id)
        if remaining_capacity.get(chosen_id, 0.0) >= 1.0:
            remaining_capacity[chosen_id] -= 1.0
        key = str(order.id)
        plan[key] = candidate
        meta[key] = {
            "previous_sku_id": current_id,
            "reservation_sku_id": previous_sku_id,
            "sku_id": chosen_id,
            "mode": mode or "PICKING_ALIAS_PLAN",
            "warehouse_id": str(warehouse_id),
            "resolved_size": resolved_size or str(candidate.size or ""),
            "accepted_barcodes": sorted(allowed_codes),
            "alias_profile": alias_mode,
        }
    return plan, meta


def _repair_unmatched_picking_skus_from_inventory(
    db: Session,
    orders: list[MarketplaceOrder],
    warehouse_id: uuid.UUID | None,
    client_id: uuid.UUID,
    *,
    resolved_plan: dict[str, SKU] | None = None,
    resolved_meta: dict[str, dict[str, Any]] | None = None,
) -> int:
    """Persist the same read-only alias plan used by preview Excel.

    The resolver itself performs no writes, so preview and real picking cannot disagree.
    This wrapper only stores the chosen local_sku_id when the operator actually enters a
    write path.
    """
    if resolved_plan is None or resolved_meta is None:
        plan, meta = _resolve_picking_sku_plan(db, orders, warehouse_id, client_id)
    else:
        plan, meta = resolved_plan, resolved_meta
    changed = 0
    now = _now().isoformat()
    for order in orders:
        candidate = plan.get(str(order.id))
        if not candidate or order.local_sku_id == candidate.id:
            continue
        order.local_sku_id = candidate.id
        raw = dict(order.raw_data or {})
        details = dict(meta.get(str(order.id)) or {})
        details["at"] = now
        raw["_fc_picking_stock_sku_match"] = details
        order.raw_data = raw
        changed += 1
    if changed:
        db.flush()
    return changed


def _orders_response_fast(db: Session, orders: list[MarketplaceOrder], warehouse_id: uuid.UUID | None, cred: MarketplaceCredential) -> list[dict]:
    """Return the order board first paint without stock/picking/card joins.

    FC_K9R53_WB_ORDER_FIRST_PAINT_FAST: the operator must see the persisted order page
    before optional warehouse/card decoration is calculated. This path is read-only and
    uses only the page orders plus one small local-SKU query; no WB network call occurs.
    """
    if not orders:
        return []
    sku_ids = {o.local_sku_id for o in orders if o.local_sku_id}
    skus = db.execute(
        select(SKU).options(load_only(SKU.id, SKU.sku_code, SKU.article, SKU.name, SKU.size, SKU.barcode))
        .where(SKU.id.in_(sku_ids))
    ).scalars().all() if sku_ids else []
    sku_map = {x.id: x for x in skus}
    settings = _wb_settings(cred, warehouse_id)
    result: list[dict[str, Any]] = []
    for order in orders:
        sku = sku_map.get(order.local_sku_id)
        order_size = _wb_order_size(order, None, sku)
        wh_info = settings.get(str(order.wb_warehouse_id)) or {}
        if not settings:
            wb_mode = "HIDDEN" if warehouse_id is not None else "WORK"
        elif order.wb_warehouse_id is None:
            wb_mode = "HIDDEN"
        else:
            wb_mode = str(wh_info.get("mode") or "HIDDEN").upper()
        sticker = _wb_sticker_data(order)
        result.append({
            "id": str(order.id), "external_order_id": order.external_order_num or order.external_order_id, "order_uid": order.order_uid,
            "article": order.article, "size": order_size, "barcodes": order.barcodes or [], "required_meta": order.required_meta or [], "optional_meta": order.optional_meta or [],
            "sgtin_supported": "sgtin" in set((order.required_meta or []) + (order.optional_meta or [])),
            "supplier_status": order.supplier_status, "wb_status": order.wb_status, "wb_status_name": WB_STATUS_NAMES.get(order.wb_status or "", order.wb_status),
            "local_status": order.local_status, "local_status_name": LOCAL_STATUS_NAMES.get(order.local_status, order.local_status), "manual_status_override": bool(getattr(order, "manual_status_override", False)),
            "is_cancellable": order.is_cancellable, "supply_id": order.supply_id, "wb_warehouse_id": order.wb_warehouse_id,
            "wb_warehouse_name": wh_info.get("name"), "wb_warehouse_mode": wb_mode,
            "product_scanned_barcode": order.product_scanned_barcode, "product_scanned_at": order.product_scanned_at,
            "chz_attached": bool(order.chz_code), "chz_pending_wb": _has_pending_wb_chz(order),
            "chz_pending_reason": (((order.raw_data or {}).get("_fc_chz_pending_wb") or {}).get("reason") if isinstance((order.raw_data or {}).get("_fc_chz_pending_wb"), dict) else None),
            "chz_pending_error": (((order.raw_data or {}).get("_fc_chz_pending_wb") or {}).get("last_error") if isinstance((order.raw_data or {}).get("_fc_chz_pending_wb"), dict) else None),
            "chz_preview": (order.chz_code[:18] + "…" + order.chz_code[-6:]) if order.chz_code and len(order.chz_code) > 28 else order.chz_code,
            "chz_display": order.chz_code.replace("\x1d", "␝") if order.chz_code else None, "marking_code": order.chz_code.replace("\x1d", "␝") if order.chz_code else None,
            "order_sticker": {"part_a": sticker.get("partA"), "part_b": sticker.get("partB"), "barcode": sticker.get("barcode")} if sticker else None,
            "order_sticker_available": bool(sticker), "label_generated": bool(order.label_generated_at), "label_generated_at": order.label_generated_at, "label_print_count": order.label_print_count,
            "assembled_at": order.assembled_at, "picking_list_generated_at": order.picking_list_generated_at, "last_picking_list_id": order.last_picking_list_id, "packed_at": order.packed_at,
            "created_at": order.external_created_at or order.created_at, "last_synced_at": order.last_synced_at,
            "local_sku": {"id": str(sku.id), "sku_code": sku.sku_code, "article": sku.article, "name": sku.name, "size": sku.size, "barcode": sku.barcode} if sku else None,
            "product_card": None, "card_loaded": False,
            "stock": [], "stock_loaded": False,
            "reserved": [], "reserved_loaded": False,
        })
    return result


def _orders_response_batch(db: Session, orders: list[MarketplaceOrder], warehouse_id: uuid.UUID | None, cred: MarketplaceCredential, allow_inventory_fallback: bool = False, repair_links: bool = True) -> list[dict]:
    """Serialize an order page without the old N+1 query pattern.

    A 200-row page previously executed hundreds of small SKU/stock/picking-list queries,
    which made status tabs feel frozen. This function preloads those relations once.
    """
    if not orders:
        return []

    # FC_K9R52_ORDER_BOARD_READ_ONLY_FAST_PATH: a status-tab GET must never run SKU
    # repair/reservation writes. Those repairs belong to sync/reconcile paths. Keeping the
    # board read-only removes duplicate card/SKU queries and avoids surprise flushes while
    # the operator is only switching tabs. Other callers keep the historical repair path.
    if repair_links:
        _repair_strict_wb_sku_links(db, orders, barcode_only=allow_inventory_fallback)
        if allow_inventory_fallback and warehouse_id:
            _repair_unmatched_picking_skus_from_inventory(db, orders, warehouse_id, cred.client_id)
            matched_active = [
                o for o in orders
                if o.local_status in {"NEW", "PICKING"} and o.local_sku_id
            ]
            if matched_active:
                reserve_new_orders_sale_first(db, orders=matched_active, warehouse_id=warehouse_id)

    sku_ids = {o.local_sku_id for o in orders if o.local_sku_id}
    skus = db.execute(select(SKU).where(SKU.id.in_(sku_ids))).scalars().all() if sku_ids else []
    sku_map = {x.id: x for x in skus}

    # Stock for every SKU on the current page in one query.
    stock_map: dict[uuid.UUID, list[dict[str, Any]]] = {sid: [] for sid in sku_ids}
    if warehouse_id and sku_ids:
        stock_rows = db.execute(
            select(InventoryBalance, Location, Container)
            .join(Location, Location.id == InventoryBalance.location_id)
            .outerjoin(Container, Container.id == InventoryBalance.container_id)
            .where(
                # 0.7.9.17: picking stock must belong to the selected client as well as
                # the selected physical warehouse. Without this guard, the same SKU used
                # by two clients could make a picking list point at another client's cell.
                InventoryBalance.client_id == cred.client_id,
                InventoryBalance.sku_id.in_(sku_ids),
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.quantity > 0,
            )
            .order_by(InventoryBalance.sku_id, Location.code, Container.name, Container.barcode)
        ).all()
        # FC_K9R7_ORDER_BOARD_BATCH_BUCKET_FREE
        # Order tabs used to execute effective_fbs_free_for_bucket() once per physical
        # bucket (multiple SQL statements each).  Load the exact same storage-only and
        # live reservation truth in two aggregate queries for the entire page.
        storage_by_bucket: dict[tuple[str, str, str], float] = {}
        reserve_by_bucket: dict[tuple[str, str, str], float] = {}
        ids_as_text = [str(x) for x in sku_ids]
        if ids_as_text:
            storage_stmt = text("""
                SELECT CAST(sku_id AS varchar) AS sku_id,
                       CAST(location_id AS varchar) AS location_id,
                       COALESCE(container_key,'') AS container_key,
                       COALESCE(SUM(storage_only),0) AS storage_only
                FROM fc_stocks_v2
                WHERE client_id=:client AND warehouse_id=:warehouse
                  AND CAST(sku_id AS varchar) IN :ids
                GROUP BY CAST(sku_id AS varchar),CAST(location_id AS varchar),COALESCE(container_key,'')
            """).bindparams(bindparam('ids', expanding=True))
            for x in db.execute(storage_stmt, {'client': str(cred.client_id), 'warehouse': str(warehouse_id), 'ids': ids_as_text}).mappings():
                storage_by_bucket[(str(x['sku_id']), str(x['location_id']), str(x['container_key'] or ''))] = max(0.0, float(x['storage_only'] or 0))
            reserve_stmt = text("""
                SELECT CAST(r.sku_id AS varchar) AS sku_id,
                       r.source_location_id AS location_id,
                       COALESCE(r.source_container_id,'') AS container_key,
                       COALESCE(SUM(CASE
                         WHEN r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL THEN 0
                         ELSE r.quantity END),0) AS reserved
                FROM fc_stock_reservations r
                LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
                LEFT JOIN inventory_operations io
                  ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar) AND COALESCE(io.status,'COMPLETED')='COMPLETED'
                 AND io.idempotency_key=('stock-core-v2-pack-' || CAST(o.id AS varchar) || '-' || CAST(r.id AS varchar))
                WHERE r.client_id=:client AND r.warehouse_id=:warehouse
                  AND CAST(r.sku_id AS varchar) IN :ids
                  AND r.source_location_id IS NOT NULL
                  AND r.status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
                GROUP BY CAST(r.sku_id AS varchar),r.source_location_id,COALESCE(r.source_container_id,'')
            """).bindparams(bindparam('ids', expanding=True))
            for x in db.execute(reserve_stmt, {'client': str(cred.client_id), 'warehouse': str(warehouse_id), 'ids': ids_as_text}).mappings():
                reserve_by_bucket[(str(x['sku_id']), str(x['location_id']), str(x['container_key'] or ''))] = max(0.0, float(x['reserved'] or 0))
        container_ids = {container.id for _balance, _loc, container in stock_rows if container}
        container_totals: dict[uuid.UUID, float] = {}
        if container_ids:
            container_totals = {
                container_id: float(quantity or 0)
                for container_id, quantity in db.execute(
                    select(
                        InventoryBalance.container_id,
                        func.coalesce(func.sum(InventoryBalance.quantity), 0),
                    ).where(
                        InventoryBalance.client_id == cred.client_id,
                        InventoryBalance.warehouse_id == warehouse_id,
                        InventoryBalance.container_id.in_(container_ids),
                        InventoryBalance.quantity > 0,
                    ).group_by(InventoryBalance.container_id)
                ).all()
            }
        for balance, loc, container in stock_rows:
            bucket = (str(balance.sku_id), str(loc.id), str(container.id) if container else "")
            physical = max(0.0, float(balance.quantity or 0))
            storage_only = min(physical, storage_by_bucket.get(bucket, 0.0))
            reserved = reserve_by_bucket.get(bucket, 0.0)
            canonical_free = max(0.0, physical - storage_only - reserved)
            stock_map.setdefault(balance.sku_id, []).append({
                "location_id": str(loc.id), "location": loc.code,
                "location_name": loc.name, "location_type": loc.location_type,
                "container_id": str(container.id) if container else None,
                "container": (container.name or container.barcode) if container else None,
                "container_barcode": container.barcode if container else None,
                "container_total_quantity": container_totals.get(container.id, 0.0) if container else 0.0,
                "quantity": physical,
                "reserved_quantity": reserved,
                "free_quantity": max(0.0, canonical_free),
            })

    # Picking-list reservations are also preloaded once.
    plist_ids: set[uuid.UUID] = set()
    for o in orders:
        if not o.last_picking_list_id:
            continue
        try: plist_ids.add(uuid.UUID(str(o.last_picking_list_id)))
        except Exception: pass
    plist_stmt = select(MarketplacePickingList).where(MarketplacePickingList.id.in_(plist_ids)) if plist_ids else None
    if plist_stmt is not None and warehouse_id is not None:
        plist_stmt = plist_stmt.where(MarketplacePickingList.warehouse_id == warehouse_id)
    plists = db.execute(plist_stmt).scalars().all() if plist_stmt is not None else []
    plist_map = {p.id: p for p in plists}

    # Product cards in a small number of queries; map by nmId first and vendor code second.
    nm_ids: set[int] = set(); articles: set[str] = set()
    for o in orders:
        raw=o.raw_data or {}; nm=raw.get("nmId") or raw.get("nmID") or raw.get("nm_id")
        try:
            if nm is not None: nm_ids.add(int(nm))
        except Exception: pass
        if o.article: articles.add(str(o.article))
    clauses=[]
    if nm_ids: clauses.append(MarketplaceProduct.external_product_id.in_(nm_ids))
    if articles: clauses.append(MarketplaceProduct.vendor_code.in_(articles))
    cards=[]
    if clauses:
        cards=db.execute(select(MarketplaceProduct).options(load_only(
            MarketplaceProduct.id, MarketplaceProduct.client_id, MarketplaceProduct.external_product_id,
            MarketplaceProduct.vendor_code, MarketplaceProduct.title, MarketplaceProduct.brand,
            MarketplaceProduct.photos, MarketplaceProduct.sizes, MarketplaceProduct.updated_at,
            MarketplaceProduct.created_at
        )).where(
            MarketplaceProduct.client_id == cred.client_id, MarketplaceProduct.provider == "WB", or_(*clauses)
        ).order_by(MarketplaceProduct.updated_at.desc(), MarketplaceProduct.created_at.desc())).scalars().all()
    card_by_nm={}; card_by_article={}
    for c in cards:
        if c.external_product_id is not None and c.external_product_id not in card_by_nm: card_by_nm[c.external_product_id]=c
        if c.vendor_code and str(c.vendor_code) not in card_by_article: card_by_article[str(c.vendor_code)]=c

    settings=_wb_settings(cred, warehouse_id)
    result=[]
    for order in orders:
        sku=sku_map.get(order.local_sku_id)
        raw=order.raw_data or {}; nm=raw.get("nmId") or raw.get("nmID") or raw.get("nm_id")
        try: nm=int(nm) if nm is not None else None
        except Exception: nm=None
        card=card_by_nm.get(nm) or card_by_article.get(str(order.article or ""))
        order_size=_wb_order_size(order, card, sku)
        wh_info=settings.get(str(order.wb_warehouse_id)) or {}
        sticker=_wb_sticker_data(order)
        reserved=[]
        if order.last_picking_list_id:
            try: plist=plist_map.get(uuid.UUID(str(order.last_picking_list_id)))
            except Exception: plist=None
            if plist:
                key=str(order.external_order_id)
                for row in plist.rows or []:
                    if key in [str(x) for x in (row.get("order_ids") or [])]:
                        reserved.append({"location":row.get("location") or "","container":row.get("container") or "","quantity":1,"status":row.get("status") or "","picking_list_id":str(plist.id)})
        result.append({
            "id":str(order.id),"external_order_id":order.external_order_num or order.external_order_id,"order_uid":order.order_uid,
            "article":order.article,"size":order_size,"barcodes":order.barcodes or [],"required_meta":order.required_meta or [],"optional_meta":order.optional_meta or [],
            "sgtin_supported":"sgtin" in set((order.required_meta or [])+(order.optional_meta or [])),
            "supplier_status":order.supplier_status,"wb_status":order.wb_status,"wb_status_name":WB_STATUS_NAMES.get(order.wb_status or "",order.wb_status),
            "local_status":order.local_status,"local_status_name":LOCAL_STATUS_NAMES.get(order.local_status,order.local_status),"manual_status_override":bool(getattr(order,"manual_status_override",False)),
            "is_cancellable":order.is_cancellable,"supply_id":order.supply_id,"wb_warehouse_id":order.wb_warehouse_id,"wb_warehouse_name":wh_info.get("name"),"wb_warehouse_mode":_wb_mode(cred,order.wb_warehouse_id,warehouse_id),
            "product_scanned_barcode":order.product_scanned_barcode,"product_scanned_at":order.product_scanned_at,
            "chz_attached":bool(order.chz_code),"chz_pending_wb":_has_pending_wb_chz(order),"chz_pending_reason":(((order.raw_data or {}).get("_fc_chz_pending_wb") or {}).get("reason") if isinstance((order.raw_data or {}).get("_fc_chz_pending_wb"),dict) else None),"chz_pending_error":(((order.raw_data or {}).get("_fc_chz_pending_wb") or {}).get("last_error") if isinstance((order.raw_data or {}).get("_fc_chz_pending_wb"),dict) else None),"chz_preview":(order.chz_code[:18]+"…"+order.chz_code[-6:]) if order.chz_code and len(order.chz_code)>28 else order.chz_code,
            "chz_display":order.chz_code.replace("\x1d","␝") if order.chz_code else None,"marking_code":order.chz_code.replace("\x1d","␝") if order.chz_code else None,
            "order_sticker":{"part_a":sticker.get("partA"),"part_b":sticker.get("partB"),"barcode":sticker.get("barcode")} if sticker else None,
            "order_sticker_available":bool(sticker),"label_generated":bool(order.label_generated_at),"label_generated_at":order.label_generated_at,"label_print_count":order.label_print_count,
            "assembled_at":order.assembled_at,"picking_list_generated_at":order.picking_list_generated_at,"last_picking_list_id":order.last_picking_list_id,"packed_at":order.packed_at,
            "created_at":order.external_created_at or order.created_at,"last_synced_at":order.last_synced_at,
            "local_sku":{"id":str(sku.id),"sku_code":sku.sku_code,"article":sku.article,"name":sku.name,"size":sku.size,"barcode":sku.barcode} if sku else None,
            "product_card":{"id":str(card.id),"nm_id":card.external_product_id,"title":card.title,"brand":card.brand,"vendor_code":card.vendor_code,"size":order_size,"photo":((card.photos or [{}])[0].get("big") or (card.photos or [{}])[0].get("c516x688") or (card.photos or [{}])[0].get("square")) if card.photos else None} if card else None,
            "stock":stock_map.get(order.local_sku_id,[]) if order.local_sku_id else [],"reserved":reserved,
        })
    return result


def _flatten_card_barcodes(card: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for size in card.get("sizes") or []:
        if not isinstance(size, dict):
            continue
        for code in size.get("skus") or []:
            value = str(code or "").strip()
            if value and value not in result:
                result.append(value)
    return result


def _wb_card_characteristics(raw: dict[str, Any] | None) -> list[dict[str, str]]:
    """Normalize Content API characteristics without depending on one WB response revision."""
    raw = raw or {}
    source = raw.get("characteristics") or raw.get("characteristicsValues") or raw.get("options") or []
    result: list[dict[str, str]] = []
    if not isinstance(source, list):
        return result
    for item in source:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("charcName") or item.get("characteristicName") or "").strip()
        value = item.get("value")
        if value in (None, ""):
            value = item.get("values")
        if value in (None, ""):
            value = item.get("valueName") or item.get("text")
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(x).strip() for x in value if str(x).strip())
        elif isinstance(value, dict):
            value = value.get("name") or value.get("value") or str(value)
        value = str(value or "").strip()
        if name and value:
            result.append({"name": name, "value": value})
    return result


def _wb_card_characteristic(raw: dict[str, Any] | None, *names: str) -> str | None:
    wanted = [x.casefold().replace("ё", "е") for x in names]
    for row in _wb_card_characteristics(raw):
        key = row["name"].casefold().replace("ё", "е")
        if any(key == w or w in key for w in wanted):
            return row["value"]
    return None


def _wb_product_label_kwargs(product: MarketplaceProduct | None) -> dict[str, Any]:
    if not product:
        return {}
    raw = product.raw_data if isinstance(product.raw_data, dict) else {}
    return {
        "title": product.title,
        "brand": product.brand,
        "composition": _wb_card_characteristic(raw, "состав"),
        "color": _wb_card_characteristic(raw, "цвет"),
        "country": _wb_card_characteristic(raw, "страна производства", "страна изготовитель"),
    }


def _find_local_sku_for_card(db: Session, client_id: uuid.UUID, card: dict[str, Any]) -> SKU | None:
    vendor = str(card.get("vendorCode") or "").strip()
    barcodes = _flatten_card_barcodes(card)
    clauses = []
    if vendor:
        clauses.extend([SKU.article == vendor, SKU.sku_code == vendor])
    if barcodes:
        clauses.append(SKU.barcode.in_(barcodes))
    if not clauses:
        return None
    return db.execute(select(SKU).where(SKU.client_id == client_id, or_(*clauses))).scalars().first()


def _upsert_wb_product(db: Session, cred: MarketplaceCredential, card: dict[str, Any]) -> MarketplaceProduct | None:
    nm_id = card.get("nmID")
    if nm_id is None:
        return None
    product = db.execute(select(MarketplaceProduct).where(
        MarketplaceProduct.credential_id == cred.id,
        MarketplaceProduct.provider == "WB",
        MarketplaceProduct.external_product_id == int(nm_id),
    )).scalars().first()
    if not product:
        product = MarketplaceProduct(
            credential_id=cred.id, client_id=cred.client_id, provider="WB", external_product_id=int(nm_id)
        )
        db.add(product)
    product.vendor_code = str(card.get("vendorCode") or "").strip() or None
    product.title = str(card.get("title") or "").strip() or None
    product.description = str(card.get("description") or "").strip() or None
    product.brand = str(card.get("brand") or "").strip() or None
    product.subject_name = str(card.get("subjectName") or "").strip() or None
    product.photos = card.get("photos") or []
    product.sizes = card.get("sizes") or []
    product.barcodes = _flatten_card_barcodes(card)
    sku = _find_local_sku_for_card(db, cred.client_id, card)
    product.local_sku_id = sku.id if sku else None
    product.last_synced_at = _now()
    product.raw_data = card
    return product


def _product_response(
    product: MarketplaceProduct,
    sku: SKU | None = None,
    inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": str(product.id),
        "nm_id": product.external_product_id,
        "vendor_code": product.vendor_code,
        "title": product.title,
        "description": product.description,
        "brand": product.brand,
        "subject_name": product.subject_name,
        "photos": product.photos or [],
        "sizes": product.sizes or [],
        "barcodes": product.barcodes or [],
        "characteristics": _wb_card_characteristics(product.raw_data if isinstance(product.raw_data, dict) else {}),
        "composition": _wb_card_characteristic(product.raw_data if isinstance(product.raw_data, dict) else {}, "состав"),
        "color": _wb_card_characteristic(product.raw_data if isinstance(product.raw_data, dict) else {}, "цвет"),
        "country": _wb_card_characteristic(product.raw_data if isinstance(product.raw_data, dict) else {}, "страна производства", "страна изготовитель"),
        "last_synced_at": product.last_synced_at,
        "inventory": inventory or {"quantity": 0.0, "sku_count": 0},
        "local_sku": {
            "id": str(sku.id), "sku_code": sku.sku_code, "article": sku.article, "name": sku.name,
            "size": sku.size, "barcode": sku.barcode,
        } if sku else None,
    }


def _upsert_wb_order(db: Session, cred: MarketplaceCredential, data: dict) -> MarketplaceOrder | None:
    raw_id = data.get("id")
    if raw_id is None:
        return None
    external_id = str(raw_id)
    order = db.execute(
        select(MarketplaceOrder).where(
            MarketplaceOrder.credential_id == cred.id,
            MarketplaceOrder.provider == "WB",
            MarketplaceOrder.external_order_id == external_id,
        )
    ).scalars().first()
    is_new = order is None
    if not order:
        order = MarketplaceOrder(
            credential_id=cred.id,
            client_id=cred.client_id,
            provider="WB",
            external_order_id=external_id,
            external_order_num=int(raw_id) if str(raw_id).isdigit() else None,
            local_status="NEW",
        )
        db.add(order)
        db.flush()

    barcodes = [str(x) for x in (data.get("skus") or data.get("barcodes") or order.barcodes or []) if x is not None]
    article = str(data.get("article") or order.article or "").strip() or None
    sku = _find_wb_sku(db, cred.client_id, {"skus": barcodes, "article": article})

    order.external_order_num = int(raw_id) if str(raw_id).isdigit() else order.external_order_num
    order.order_uid = data.get("orderUid") or data.get("order_uid") or order.order_uid
    order.article = article
    order.barcodes = barcodes
    order.required_meta = data.get("requiredMeta") or data.get("required_meta") or order.required_meta or []
    order.optional_meta = data.get("optionalMeta") or data.get("optional_meta") or order.optional_meta or []
    order.wb_warehouse_id = data.get("warehouseId") or data.get("wb_warehouse_id") or order.wb_warehouse_id
    order.cargo_type = data.get("cargoType") or data.get("cargo_type") or order.cargo_type
    order.supply_id = str(data.get("supplyId") or data.get("supply_id") or order.supply_id or "").strip() or None

    # History/new-order feeds may already carry authoritative WB/supplier statuses.
    # Persist them immediately instead of waiting for a later /status batch. This is
    # especially important for cancellations: an order cancelled between status sweeps
    # must disappear from the warehouse NEW queue as soon as history sees it.
    incoming_supplier = data.get("supplierStatus") or data.get("supplier_status")
    incoming_wb = data.get("wbStatus") or data.get("wb_status")
    generic_status = data.get("status") or data.get("state") or data.get("orderStatus") or data.get("order_status")
    if generic_status is not None and ("cancel" in str(generic_status).lower() or "отмен" in str(generic_status).lower()):
        incoming_wb = str(generic_status)
    if incoming_supplier is not None:
        order.supplier_status = str(incoming_supplier).strip().lower() or order.supplier_status
    if incoming_wb is not None:
        order.wb_status = str(incoming_wb).strip().lower() or order.wb_status
    if "isCancellable" in data:
        order.is_cancellable = bool(data.get("isCancellable"))
    elif "is_cancellable" in data:
        order.is_cancellable = bool(data.get("is_cancellable"))

    order.local_sku_id = sku.id if sku else order.local_sku_id
    order.external_created_at = _parse_dt(data.get("createdAt") or data.get("created_at")) or order.external_created_at
    order.last_synced_at = _now()
    merged = dict(order.raw_data or {})
    merged.update(data)
    order.raw_data = merged
    # Reconcile immediately after every upsert. If the feed says the order is cancelled,
    # delivered or already in transport, it must never be left as NEW until another pass.
    _reconcile_local_status(db, order, "WB_ORDER_FEED_UPSERT")
    if is_new:
        _event(db, order, "ORDER_SYNCED_NEW", data={"external_order_id": external_id})
    return order



def _reserve_new_wb_orders_immediately(
    db: Session,
    *,
    cred: MarketplaceCredential,
    physical_warehouse_id: uuid.UUID | None,
    selected_wb_warehouse_id: int,
) -> dict[str, int]:
    """Reserve exact SKU capacity as soon as a NEW WB order reaches FulfillCore.

    No cell/container is selected here. SALE_RESERVED is warehouse-level only; the real
    picking list later binds the same promise to a physical source. This prevents
    overselling during a 1-2 day wait between order arrival and warehouse picking.
    """
    if not physical_warehouse_id:
        return {"created": 0, "kept": 0, "already_consumed": 0, "shortage": 0}
    logical_ids = _logical_credential_ids(db, cred)
    orders = db.execute(
        select(MarketplaceOrder).where(
            MarketplaceOrder.credential_id.in_(logical_ids),
            MarketplaceOrder.provider == "WB",
            MarketplaceOrder.wb_warehouse_id == selected_wb_warehouse_id,
            # g5a8e: heal both NEW and already-PICKING orders. Older orders can reach
            # PICKING without a funded SALE_FIRST reservation after a prior DB rollback,
            # SKU-link repair, or upgrade from the pre-sale-first flow. The reservation
            # service is idempotent, oldest-first and cannot fund more than physical stock.
            MarketplaceOrder.local_status.in_(["NEW", "PICKING"]),
        ).order_by(
            MarketplaceOrder.external_created_at.asc().nullslast(),
            MarketplaceOrder.created_at.asc(),
        )
    ).scalars().all()
    if not orders:
        return {"created": 0, "kept": 0, "already_consumed": 0, "shortage": 0}
    _repair_strict_wb_sku_links(db, orders)
    return reserve_new_orders_sale_first(db, orders=orders, warehouse_id=physical_warehouse_id)



def _apply_status(db: Session, order: MarketplaceOrder, status: dict) -> None:
    supplier = status.get("supplierStatus") or status.get("supplier_status")
    wb = status.get("wbStatus") or status.get("wb_status")
    if supplier is not None:
        order.supplier_status = str(supplier).strip().lower() or order.supplier_status
    if wb is not None:
        order.wb_status = str(wb).strip().lower() or order.wb_status
    if "isCancellable" in status:
        order.is_cancellable = bool(status.get("isCancellable"))
    elif "is_cancellable" in status:
        order.is_cancellable = bool(status.get("is_cancellable"))
    _reconcile_local_status(db, order, "WB_STATUS_SYNC")
    order.last_synced_at = _now()


@router.get("/credentials", response_model=list[MarketplaceCredentialRead])
def list_credentials(client_id: uuid.UUID, db: Session = Depends(get_db)):
    rows = db.execute(
        select(MarketplaceCredential)
        .where(MarketplaceCredential.client_id == client_id, MarketplaceCredential.is_active.is_(True))
        .order_by(MarketplaceCredential.provider, MarketplaceCredential.name)
    ).scalars().all()
    # FC_K9R54_CREDENTIAL_LIST_READ_ONLY: context bootstrap runs after many warehouse
    # actions. Never turn this tiny credential-list read into historical order/product
    # migration. Logical-history reads already span credential generations; actual WB
    # write/sync paths still call _wb(), which retains the consolidation safety path.
    return rows


@router.post("/credentials", response_model=MarketplaceCredentialRead)
def create_credential(payload: MarketplaceCredentialCreate, db: Session = Depends(get_db)):
    name = payload.name.strip()
    # Reconnect the same logical cabinet in-place whenever possible. This keeps the
    # stable credential identity and therefore keeps all previously synchronized
    # orders, products, supplies and picking lists visible after API reconnection.
    old = db.execute(select(MarketplaceCredential).where(
        MarketplaceCredential.client_id == payload.client_id,
        MarketplaceCredential.provider == payload.provider,
        MarketplaceCredential.name == name,
        MarketplaceCredential.is_active.is_(False),
    ).order_by(MarketplaceCredential.updated_at.desc())).scalars().first()
    if old:
        old.external_client_id = payload.external_client_id.strip() if payload.external_client_id else None
        old.encrypted_api_key = encrypt_secret(payload.api_key.strip())
        old.is_active = True
        old.last_tested_at = None
        old.last_test_ok = None
        old.last_test_message = "API-связь восстановлена"
        db.commit()
        db.refresh(old)
        return old
    obj = MarketplaceCredential(
        client_id=payload.client_id,
        provider=payload.provider,
        name=name,
        external_client_id=payload.external_client_id.strip() if payload.external_client_id else None,
        encrypted_api_key=encrypt_secret(payload.api_key.strip()),
        is_active=True,
    )
    try:
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Активное подключение с таким названием уже существует") from exc


@router.delete("/credentials/{credential_id}")
def delete_credential(credential_id: uuid.UUID, db: Session = Depends(get_db)):
    """Disconnect a marketplace cabinet without deleting warehouse history.

    Orders, picking lists and audit history stay linked to the old credential row,
    while the API secret is wiped and the same visible cabinet name can be used
    again for a new connection.
    """
    cred = _credential(db, credential_id)
    cred.is_active = False
    # Break the actual API link as well: do not retain the old marketplace token.
    cred.encrypted_api_key = encrypt_secret(f"DISCONNECTED:{uuid.uuid4()}")
    cred.last_tested_at = _now()
    cred.last_test_ok = None
    cred.last_test_message = "API-связь отключена пользователем"
    db.commit()
    return {"ok": True, "message": "API-связь отключена. История сохранена."}


@router.post("/credentials/{credential_id}/test")
def test_credential(credential_id: uuid.UUID, db: Session = Depends(get_db)):
    cred = _credential(db, credential_id)
    try:
        if cred.provider == "WB":
            data = WBClient(decrypt_secret(cred.encrypted_api_key)).ping()
            msg = f"Wildberries отвечает: {data}"
        elif cred.provider == "OZON":
            if not cred.external_client_id:
                raise HTTPException(status_code=400, detail="Не указан Client-Id Ozon")
            client = OzonClient(cred.external_client_id, decrypt_secret(cred.encrypted_api_key))
            seller_info = None
            seller_error = None
            try:
                seller_info = _ozon_post_json(client, "/v1/seller/info", {})
            except Exception as exc:
                seller_error = _external_error_text(exc)
            try:
                warehouses = client.warehouses()
                if isinstance(warehouses, dict):
                    warehouses = warehouses.get("result") or warehouses.get("items") or warehouses.get("warehouses") or []
                if not isinstance(warehouses, list):
                    warehouses = []
            except Exception as exc:
                if seller_info is None:
                    raise HTTPException(
                        status_code=502,
                        detail=f"Ozon не подтвердил подключение: {_external_error_text(exc)}",
                    ) from exc
                warehouses = []
                seller_error = _external_error_text(exc)
            company = None
            if isinstance(seller_info, dict):
                source = seller_info.get("result") if isinstance(seller_info.get("result"), dict) else seller_info
                company = source.get("company_name") or source.get("name") or source.get("seller_name")
            msg = f"Ozon подключён{f' · {company}' if company else ''}. Складов продавца: {len(warehouses)}"
            if seller_error and seller_info is not None:
                msg += f". Кабинет доступен, но список складов не получен: {seller_error}"
        else:
            raise HTTPException(status_code=400, detail="Неизвестный маркетплейс")
        cred.last_test_ok = True
        cred.last_test_message = msg[:500]
        cred.last_tested_at = _now()
        db.commit()
        return {"ok": True, "message": msg}
    except HTTPException as exc:
        cred.last_test_ok = False
        cred.last_test_message = str(exc.detail)[:500]
        cred.last_tested_at = _now()
        db.commit()
        raise
    except Exception as exc:
        message = _external_error_text(exc)
        cred.last_test_ok = False
        cred.last_test_message = message[:500]
        cred.last_tested_at = _now()
        db.commit()
        raise HTTPException(status_code=502, detail=f"Не удалось подключиться к Ozon: {message}") from exc


# ---------------- Wildberries FBS ----------------

# FC_WB_WAREHOUSE_REFRESH_DIAGNOSTICS_FIX_20260902
def _wb_seller_warehouses(client: WBClient) -> list[dict[str, Any]]:
    """Read seller warehouses from the official WB endpoint.

    Some cumulative builds do not expose WBClient.warehouses(); calling that missing
    wrapper made manual refresh fall back to the saved list and made diagnostics return
    HTTP 500. Keep this router compatible with those builds by using the transport that
    is present in every supported WBClient version.
    """
    response = client._get("/api/v3/warehouses")
    data = response.json()
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        rows = data.get("warehouses") or data.get("result") or data.get("data") or []
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


@router.get("/wb/warehouses")
def wb_warehouses(
    credential_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    cred, client = _wb(db, credential_id)
    settings, configured = _wb_catalog_settings(cred, warehouse_id)
    scope_source = "physical" if configured and warehouse_id is not None else ("legacy" if warehouse_id is None else "unconfigured")

    # Opening the FBS workspace must be instant. Normally return the already saved
    # warehouse configuration from PostgreSQL; call WB only when the user explicitly
    # presses “Обновить склады WB” or when no configuration exists yet.
    if settings and not refresh:
        return {
            "warehouses": _wb_saved_warehouse_rows(settings),
            "configured": configured,
            "source": "saved" if configured else "saved_catalogue",
            "settings_scope": scope_source,
            "physical_warehouse_id": str(warehouse_id) if warehouse_id else None,
        }

    try:
        live_warehouses = _wb_seller_warehouses(client)
    except Exception:
        cached = _wb_saved_warehouse_rows(settings)
        return {
            "warehouses": cached,
            "configured": configured,
            "source": "saved_fallback",
            "settings_scope": scope_source,
            "physical_warehouse_id": str(warehouse_id) if warehouse_id else None,
            "refresh_failed": True,
            "warning": (
                "Wildberries временно не обновил список складов. Показан сохранённый список; ваш выбор не сброшен."
                if cached else
                "Wildberries временно не ответил, а сохранённого списка складов пока нет. Повторите обновление позже."
            ),
        }

    items = []
    live_ids: set[str] = set()
    for raw in live_warehouses:
        wid = raw.get("id") or raw.get("warehouseId")
        if wid is None:
            continue
        delivery_type = raw.get("deliveryType")
        if delivery_type not in (None, 1):
            continue
        try:
            numeric_id = int(wid)
        except (TypeError, ValueError):
            continue
        live_ids.add(str(wid))
        saved = settings.get(str(wid)) or {}
        items.append({
            "id": numeric_id,
            "name": raw.get("name") or saved.get("name") or f"Склад {wid}",
            "office_id": raw.get("officeId"),
            "cargo_type": raw.get("cargoType"),
            "delivery_type": raw.get("deliveryType"),
            "is_deleting": raw.get("isDeleting"),
            "is_processing": raw.get("isProcessing"),
            "mode": str(saved.get("mode") or "HIDDEN").upper(),
        })
    # WB can temporarily return an incomplete list during maintenance. Never remove
    # already saved rows from the screen merely because one refresh omitted them.
    items.extend(row for row in _wb_saved_warehouse_rows(settings) if str(row["id"]) not in live_ids)
    items.sort(key=lambda x: (x["mode"] == "HIDDEN", x["name"].lower()))
    return {
        "warehouses": items,
        "configured": configured,
        "source": "wb",
        "settings_scope": scope_source,
        "physical_warehouse_id": str(warehouse_id) if warehouse_id else None,
    }


@router.put("/wb/warehouse-settings")
def wb_update_warehouse_settings(
    payload: WBWarehouseSettingsUpdate,
    warehouse_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cred, client = _wb(db, payload.credential_id)
    # A local WORK/WATCH/HIDDEN choice must save even when WB is temporarily
    # unavailable. Previously this external call could turn the save into HTTP 500.
    stored = _wb_settings(cred, warehouse_id)
    catalogue, _ = _wb_catalog_settings(cred, warehouse_id)
    try:
        current = _wb_seller_warehouses(client)
    except Exception:
        current = []
    by_id = {str(x.get("id") or x.get("warehouseId")): x for x in current if (x.get("id") or x.get("warehouseId")) is not None}
    settings: dict[str, dict[str, Any]] = {}
    for item in payload.items:
        raw = by_id.get(str(item.warehouse_id)) or {}
        previous = stored.get(str(item.warehouse_id)) or catalogue.get(str(item.warehouse_id)) or {}
        settings[str(item.warehouse_id)] = {
            "mode": item.mode.upper(),
            "name": raw.get("name") or previous.get("name") or f"Склад {item.warehouse_id}",
            "office_id": raw.get("officeId") if raw.get("officeId") is not None else previous.get("office_id"),
            "cargo_type": raw.get("cargoType") if raw.get("cargoType") is not None else previous.get("cargo_type"),
            "delivery_type": raw.get("deliveryType") if raw.get("deliveryType") is not None else previous.get("delivery_type", 1),
            "is_deleting": raw.get("isDeleting") if raw.get("isDeleting") is not None else previous.get("is_deleting"),
            "is_processing": raw.get("isProcessing") if raw.get("isProcessing") is not None else previous.get("is_processing"),
        }
    if not any(v.get("mode") == "WORK" for v in settings.values()):
        raise HTTPException(status_code=400, detail="Выберите хотя бы один склад WB в режиме «Работа».")
    _store_wb_settings(cred, settings, warehouse_id)
    db.commit()
    return {
        "ok": True,
        "warehouse_settings": settings,
        "settings_scope": "physical" if warehouse_id else "legacy",
        "physical_warehouse_id": str(warehouse_id) if warehouse_id else None,
    }


# FC_K9R6_LOCAL_WB_CARDS_CURSOR_SYNC
_WB_CARDS_CURSOR_KEY = "__fc_wb_cards_cursor_v1__"
_WB_CONTENT_CARDS_URL = "https://content-api.wildberries.ru/content/v2/get/cards/list"


def _wb_cards_cursor_from_settings(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    cursor = raw.get(_WB_CARDS_CURSOR_KEY)
    if not isinstance(cursor, dict):
        return None
    updated_at = str(cursor.get("updatedAt") or "").strip()
    try:
        nm_id = int(cursor.get("nmID"))
    except (TypeError, ValueError):
        return None
    if not updated_at or nm_id <= 0:
        return None
    return {"updatedAt": updated_at, "nmID": nm_id}


def _wb_cards_cursor(db: Session, cred: MarketplaceCredential) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for item in _logical_credentials(db, cred):
        cursor = _wb_cards_cursor_from_settings(item.warehouse_settings or {})
        if cursor:
            candidates.append(cursor)
    if not candidates:
        return None
    return max(candidates, key=lambda x: (str(x.get("updatedAt") or ""), int(x.get("nmID") or 0)))


def _store_wb_cards_cursor(cred: MarketplaceCredential, cursor: dict[str, Any] | None) -> None:
    if not cursor:
        return
    raw = dict(cred.warehouse_settings or {})
    raw[_WB_CARDS_CURSOR_KEY] = {
        "updatedAt": str(cursor.get("updatedAt") or ""),
        "nmID": int(cursor.get("nmID") or 0),
        "savedAt": _now().isoformat(),
    }
    cred.warehouse_settings = raw


def _wb_content_cards_page(token: str, *, cursor: dict[str, Any] | None = None, limit: int = 100) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "sort": {"ascending": True},
        "cursor": {"limit": max(1, min(int(limit), 100))},
        "filter": {"withPhoto": -1},
    }
    if cursor:
        settings["cursor"].update({"updatedAt": str(cursor["updatedAt"]), "nmID": int(cursor["nmID"])})
    body = json.dumps({"settings": settings}, ensure_ascii=False).encode("utf-8")
    req = Request(_WB_CONTENT_CARDS_URL, data=body, method="POST", headers={
        "Authorization": token, "Content-Type": "application/json", "Accept": "application/json"
    })
    try:
        with urlopen(req, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:800]
        except Exception:
            detail = ""
        raise HTTPException(status_code=502, detail=f"WB Content API {exc.code}: {detail or exc.reason}") from exc
    except (URLError, TimeoutError) as exc:
        raise HTTPException(status_code=504, detail=f"WB Content API временно недоступен: {exc}") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="WB Content API вернул неожиданный ответ")
    return payload


def _wb_content_cards_cursor_walk(token: str, *, start_cursor: dict[str, Any] | None, limit_total: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None, bool, int]:
    """Bounded manual cursor walk; ordinary product-card reads never call WB."""
    cards: list[dict[str, Any]] = []
    cursor = dict(start_cursor) if start_cursor else None
    page_count = 0
    truncated = False
    max_cards = max(100, min(int(limit_total), 20000))
    while len(cards) < max_cards:
        payload = _wb_content_cards_page(token, cursor=cursor, limit=100)
        batch = [x for x in (payload.get("cards") or []) if isinstance(x, dict)]
        raw_cursor = payload.get("cursor") if isinstance(payload.get("cursor"), dict) else {}
        try:
            total = int(raw_cursor.get("total") if raw_cursor.get("total") is not None else len(batch))
        except (TypeError, ValueError):
            total = len(batch)
        updated_at = str(raw_cursor.get("updatedAt") or "").strip()
        try:
            nm_id = int(raw_cursor.get("nmID"))
        except (TypeError, ValueError):
            nm_id = 0
        next_cursor = {"updatedAt": updated_at, "nmID": nm_id} if updated_at and nm_id > 0 else None
        if batch:
            cards.extend(batch[: max_cards - len(cards)])
        page_count += 1
        if total < 100 or not batch:
            cursor = next_cursor or cursor
            break
        if next_cursor is None or next_cursor == cursor:
            cursor = next_cursor or cursor
            break
        cursor = next_cursor
        if len(cards) >= max_cards:
            truncated = True
            break
        time.sleep(0.62)
    return cards, cursor, truncated, page_count


def _import_wb_cards(db: Session, cred: MarketplaceCredential, cards: list[dict[str, Any]]) -> tuple[int, int]:
    created = updated = 0
    existing_nm = set(db.execute(select(MarketplaceProduct.external_product_id).where(
        MarketplaceProduct.client_id == cred.client_id, MarketplaceProduct.provider == "WB"
    )).scalars().all())
    for card in cards:
        raw_nm = card.get("nmID") or card.get("nmId") or card.get("nm_id")
        try:
            nm_id = int(raw_nm)
        except (TypeError, ValueError):
            continue
        if _upsert_wb_product(db, cred, card):
            if nm_id in existing_nm:
                updated += 1
            else:
                created += 1
                existing_nm.add(nm_id)
    return created, updated


@router.post("/wb/products/import-changes")
def wb_import_product_changes(
    credential_id: uuid.UUID,
    full: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Explicit manual sync only. Page open/search are local PostgreSQL operations."""
    cred = _credential(db, credential_id, "WB")
    _consolidate_credential_history(db, cred)
    start_cursor = None if full else _wb_cards_cursor(db, cred)
    token = decrypt_secret(cred.encrypted_api_key)
    initial = start_cursor is None
    cards, final_cursor, truncated, pages = _wb_content_cards_cursor_walk(
        token, start_cursor=start_cursor, limit_total=20000 if (full or initial) else 5000
    )
    created, updated = _import_wb_cards(db, cred, cards)
    _store_wb_cards_cursor(cred, final_cursor)
    db.commit()
    return {
        "ok": True, "mode": "full" if (full or initial) else "changes", "initial": initial,
        "created": created, "updated": updated, "imported": created + updated,
        "pages": pages, "truncated": truncated, "cursor": final_cursor, "external_api_calls": pages,
    }


@router.post("/wb/products/import")
def wb_import_products(payload: WBProductImportRequest, db: Session = Depends(get_db)):
    cred, client = _wb(db, payload.credential_id)
    requested = {str(x).strip().lower() for x in payload.articles if str(x).strip()}
    cards: list[dict[str, Any]] = []
    final_cursor = None
    if payload.import_all or not requested:
        cards, final_cursor, _truncated, _pages = _wb_content_cards_cursor_walk(
            decrypt_secret(cred.encrypted_api_key), start_cursor=None, limit_total=20000
        )
    elif len(requested) <= 8:
        for pos, item in enumerate(sorted(requested)):
            cards.extend(client.content_cards(text_search=item, limit_total=100))
            if pos + 1 < len(requested):
                time.sleep(0.62)
    else:
        all_cards = client.content_cards(limit_total=20000)
        for card in all_cards:
            nm = str(card.get("nmID") or "").lower()
            vendor = str(card.get("vendorCode") or "").strip().lower()
            if nm in requested or vendor in requested:
                cards.append(card)
    imported = 0
    matched = set()
    for card in cards:
        nm = str(card.get("nmID") or "").lower()
        vendor = str(card.get("vendorCode") or "").strip().lower()
        if requested and not payload.import_all and nm not in requested and vendor not in requested:
            continue
        if _upsert_wb_product(db, cred, card):
            imported += 1
            if nm in requested:
                matched.add(nm)
            if vendor in requested:
                matched.add(vendor)
    if final_cursor:
        _store_wb_cards_cursor(cred, final_cursor)
    db.commit()
    missing = sorted(requested - matched) if requested else []
    return {"ok": True, "imported": imported, "requested": len(requested), "missing": missing[:100], "cursor_saved": bool(final_cursor)}


# FC_HOT_READ_TX_RELEASE_09415G4_20260903
@router.get("/wb/products")
def wb_products(
    credential_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=20000),
    include_inventory: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    cred = _credential(db, credential_id, "WB")
    # FC_K9R52_WB_PRODUCTS_DB_LIMIT: this endpoint is a local read. Do not run history
    # consolidation here and do not materialize every historical duplicate card before
    # applying limit. Rank one row per nmID in SQL, then hydrate only the visible cards.
    logical_ids = _logical_credential_ids(db, cred)
    base_filters = [
        MarketplaceProduct.credential_id.in_(logical_ids),
        MarketplaceProduct.provider == "WB",
    ]
    filtered = list(base_filters)
    if search and search.strip():
        needle = f"%{search.strip()}%"
        filtered.append(or_(
            cast(MarketplaceProduct.external_product_id, Text).ilike(needle),
            MarketplaceProduct.vendor_code.ilike(needle),
            MarketplaceProduct.title.ilike(needle),
            MarketplaceProduct.brand.ilike(needle),
            MarketplaceProduct.subject_name.ilike(needle),
            cast(MarketplaceProduct.barcodes, Text).ilike(needle),
        ))

    priority = case((MarketplaceProduct.credential_id == cred.id, 0), else_=1)
    ranked = select(
        MarketplaceProduct.id.label("id"),
        MarketplaceProduct.external_product_id.label("nm_id"),
        MarketplaceProduct.vendor_code.label("vendor_code"),
        func.row_number().over(
            partition_by=MarketplaceProduct.external_product_id,
            order_by=(priority.asc(), MarketplaceProduct.updated_at.desc(), MarketplaceProduct.created_at.desc()),
        ).label("fc_rank"),
    ).where(*filtered).subquery()
    id_rows = db.execute(
        select(ranked.c.id, ranked.c.vendor_code, ranked.c.nm_id)
        .where(ranked.c.fc_rank == 1)
        .order_by(func.lower(func.coalesce(ranked.c.vendor_code, "")), ranked.c.nm_id)
        .limit(limit)
    ).all()
    chosen_ids = [row.id for row in id_rows]
    hydrated = db.execute(
        select(MarketplaceProduct).where(MarketplaceProduct.id.in_(chosen_ids))
    ).scalars().all() if chosen_ids else []
    by_id = {p.id: p for p in hydrated}
    products = [by_id[pid] for pid in chosen_ids if pid in by_id]

    total = int(db.execute(
        select(func.count(func.distinct(MarketplaceProduct.external_product_id))).where(*base_filters)
    ).scalar_one() or 0)
    matching = int(db.execute(
        select(func.count(func.distinct(MarketplaceProduct.external_product_id))).where(*filtered)
    ).scalar_one() or 0)
    last_import_at = db.execute(
        select(func.max(MarketplaceProduct.last_synced_at)).where(*base_filters)
    ).scalar_one_or_none()
    # FC_K9R53_WB_PRODUCT_FIRST_PAINT_FAST: card catalogue text/photo is independent
    # from warehouse inventory aggregation. The browser requests inventory separately
    # after the cards are already visible. Existing API callers keep include_inventory=true.
    if include_inventory:
        inventory_map, sku_map = _product_inventory_summaries(db, products, warehouse_id)
    else:
        inventory_map = {p.id: {"quantity": None, "sku_count": None, "loading": True} for p in products}
        direct_ids = {p.local_sku_id for p in products if p.local_sku_id}
        direct_skus = db.execute(select(SKU).where(SKU.client_id == cred.client_id, SKU.id.in_(direct_ids))).scalars().all() if direct_ids else []
        sku_map = {x.id: x for x in direct_skus}
    response = {
        "products": [
            _product_response(
                p,
                sku_map.get(p.local_sku_id) if p.local_sku_id else None,
                inventory_map.get(p.id),
            )
            for p in products
        ],
        "count": len(products),
        "matching": matching,
        "total": total,
        "persisted": True,
        "source": "local_db",
        "external_api_calls": 0,
        "last_import_at": last_import_at,
        "incremental_cursor": _wb_cards_cursor(db, cred),
    }
    db.rollback()
    return response


@router.post("/wb/products/inventory")
def wb_products_inventory(payload: dict[str, Any], db: Session = Depends(get_db)):
    """Return local inventory decoration for already rendered WB cards only."""
    try:
        credential_id = uuid.UUID(str(payload.get("credential_id") or ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="credential_id is required") from exc
    warehouse_id = None
    if payload.get("warehouse_id"):
        try: warehouse_id = uuid.UUID(str(payload.get("warehouse_id")))
        except Exception as exc: raise HTTPException(status_code=400, detail="warehouse_id is invalid") from exc
    product_ids: list[uuid.UUID] = []
    for value in (payload.get("product_ids") or [])[:500]:
        try: product_ids.append(uuid.UUID(str(value)))
        except Exception: continue
    if not product_ids:
        return {"items": [], "persisted": True, "external_api_calls": 0}
    cred = _credential(db, credential_id, "WB")
    logical_ids = _logical_credential_ids(db, cred)
    rows = db.execute(select(MarketplaceProduct).where(
        MarketplaceProduct.id.in_(product_ids), MarketplaceProduct.credential_id.in_(logical_ids), MarketplaceProduct.provider == "WB"
    )).scalars().all()
    by_id = {p.id: p for p in rows}; products = [by_id[x] for x in product_ids if x in by_id]
    inventory_map, sku_map = _product_inventory_summaries(db, products, warehouse_id)
    items = []
    for product in products:
        sku = sku_map.get(product.local_sku_id) if product.local_sku_id else None
        items.append({
            "id": str(product.id), "inventory": inventory_map.get(product.id, {"quantity": 0.0, "sku_count": 0}),
            "local_sku": {"id": str(sku.id), "sku_code": sku.sku_code, "article": sku.article, "name": sku.name, "size": sku.size, "barcode": sku.barcode} if sku else None,
        })
    db.rollback()
    return {"items": items, "persisted": True, "external_api_calls": 0}


@router.get("/wb/products/{product_id}/label.pdf")
def wb_product_card_label_pdf(
    product_id: uuid.UUID, credential_id: uuid.UUID, barcode: str | None = Query(default=None),
    width_mm: float = Query(default=58, ge=25, le=120), height_mm: float = Query(default=40, ge=25, le=120),
    rotate: int = Query(default=90), scale_percent: float = Query(default=100, ge=80, le=108),
    offset_x_mm: float = Query(default=0, ge=-5, le=5), offset_y_mm: float = Query(default=0, ge=-5, le=5),
    db: Session = Depends(get_db)
):
    cred = _credential(db, credential_id, "WB")
    logical_ids = set(_logical_credential_ids(db, cred))
    product = db.get(MarketplaceProduct, product_id)
    if not product or product.credential_id not in logical_ids or product.provider != "WB":
        raise HTTPException(status_code=404, detail="Карточка WB не найдена")
    codes = [str(x) for x in (product.barcodes or []) if x]
    selected = barcode or (codes[0] if codes else None)
    if not selected:
        raise HTTPException(status_code=409, detail="У карточки WB нет штрихкода для печати")
    if barcode and codes and barcode not in codes:
        raise HTTPException(status_code=400, detail="Этот штрихкод не относится к карточке WB")
    size_name = None
    for size in product.sizes or []:
        if selected in [str(x) for x in (size.get("skus") or [])]:
            size_name = size.get("techSize") or size.get("wbSize") or size.get("chrtID")
            break
    pdf = product_barcode_label_pdf(
        selected, product.vendor_code or str(product.external_product_id), str(size_name) if size_name else None,
        width_mm=width_mm, height_mm=height_mm, rotate=rotate, scale_percent=scale_percent,
        offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm, **_wb_product_label_kwargs(product),
    )
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="WB-product-card-{product.external_product_id}.pdf"'})


@router.get("/wb/print-test.pdf")
def wb_print_test_pdf(
    kind: str = Query(default="product"),
    width_mm: float = Query(default=58, ge=25, le=120),
    height_mm: float = Query(default=40, ge=25, le=120),
    rotate: int = Query(default=90),
    scale_percent: float = Query(default=100, ge=80, le=108),
    offset_x_mm: float = Query(default=0, ge=-5, le=5),
    offset_y_mm: float = Query(default=0, ge=-5, le=5),
):
    pdf = print_calibration_pdf(kind=kind, width_mm=width_mm, height_mm=height_mm, rotate=rotate,
                                scale_percent=scale_percent, offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm)
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="FulfillCore-print-test.pdf"'})


@router.post("/wb/print-test/queue")
def wb_queue_print_test(
    payload: WBQueuePrintRequest,
    kind: str = Query(default="product"),
    width_mm: float = Query(default=58, ge=25, le=120),
    height_mm: float = Query(default=40, ge=25, le=120),
    rotate: int = Query(default=90),
    scale_percent: float = Query(default=100, ge=80, le=108),
    offset_x_mm: float = Query(default=0, ge=-5, le=5),
    offset_y_mm: float = Query(default=0, ge=-5, le=5),
    db: Session = Depends(get_db),
):
    cred = _credential(db, payload.credential_id, "WB")
    # Print Agent test uses a raster already built in the XP-420B's native 203-DPI dots.
    # The agent sends this image straight to TSPL; no PDF/driver scaling is involved.
    png = print_calibration_png(kind=kind, width_mm=width_mm, height_mm=height_mm, scale_percent=scale_percent, offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm)
    job = queue_print_job(db, station_code=normalize_station_code(payload.print_station), payload=png,
                          file_name=f"FulfillCore-{kind}-print-test.png", document_type="PRINT_CALIBRATION",
                          client_id=cred.client_id, object_type="PRINT_CALIBRATION", object_id=kind)
    db.commit()
    return {"ok": True, "print_job_id": str(job.id)}


@router.get("/wb/orders/packed-without-writeoff")
def wb_packed_without_writeoff(
    credential_id: uuid.UUID,
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Diagnostic only: PACKED orders without a recorded reservation consumption."""
    _credential(db, credential_id, "WB")
    consumed_exists = (
        select(MarketplaceOrderEvent.id)
        .where(
            MarketplaceOrderEvent.marketplace_order_id == MarketplaceOrder.id,
            MarketplaceOrderEvent.event_type == "PICKING_RESERVATION_CONSUMED",
        )
        .exists()
    )
    rows = db.execute(
        select(MarketplaceOrder)
        .where(
            MarketplaceOrder.credential_id == credential_id,
            MarketplaceOrder.provider == "WB",
            MarketplaceOrder.local_status == "PACKED",
            ~consumed_exists,
        )
        .order_by(MarketplaceOrder.packed_at.desc().nullslast(), MarketplaceOrder.created_at.desc())
        .limit(limit)
    ).scalars().all()
    data = []
    for order in rows:
        data.append({
            "order_id": str(order.id),
            "external_order_id": order.external_order_num or order.external_order_id,
            "sku_id": str(order.local_sku_id) if order.local_sku_id else None,
            "packed_at": order.packed_at.isoformat() if order.packed_at else None,
            "last_picking_list_id": str(order.last_picking_list_id) if order.last_picking_list_id else None,
            "chz_present": bool(order.chz_code),
        })
    packed_dates = [o.packed_at for o in rows if o.packed_at]
    return {
        "count": len(data),
        "total_quantity": len(data),
        "period_from": min(packed_dates).isoformat() if packed_dates else None,
        "period_to": max(packed_dates).isoformat() if packed_dates else None,
        "orders": data,
    }


@router.post("/wb/orders/manual-status")
def wb_manual_workflow_status(payload: dict[str, Any], db: Session = Depends(get_db)):
    """Set NEW/PICKING/PACKED/SENT locally from any non-terminal warehouse state.

    This endpoint never changes WB status or supply membership. It exists for the
    real-world case where an operator packed or shipped the order in WB/another WMS
    and needs FulfillCore to mirror that fact immediately.
    """
    try:
        credential_id = uuid.UUID(str(payload.get("credential_id")))
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный credential_id")
    target = str(payload.get("target_status") or "").strip().upper()
    if target not in {"NEW", "PICKING", "PACKED", "SENT"}:
        raise HTTPException(status_code=400, detail="Доступные статусы: NEW, PICKING, PACKED, SENT")
    try:
        order_ids = [int(x) for x in (payload.get("order_ids") or [])]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Некорректный список заказов")
    if not order_ids:
        raise HTTPException(status_code=400, detail="Не выбраны заказы")

    cred = _credential(db, credential_id, "WB")
    physical_warehouse_id = payload.get("warehouse_id")
    if target == "SENT":
        # FC_K9R58_SENT_MANUAL_STATUS_STRICT_PATH: bulk/detail «Отправлены» uses the same
        # PACKED-only, no-second-writeoff, logical-credential-safe transition as mark-sent.
        result = _mark_packed_orders_sent_local(
            db, cred=cred, order_ids=order_ids, physical_warehouse_id=physical_warehouse_id,
            source="MANUAL_STATUS_SENT",
        )
        result["target_status"] = "SENT"
        return result
    orders = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id == credential_id,
        MarketplaceOrder.external_order_num.in_(order_ids),
    )).scalars().all()
    if len(orders) != len(set(order_ids)):
        raise HTTPException(status_code=404, detail="Часть выбранных заказов WB не найдена")

    changed = 0
    for order in orders:
        _assert_wb_work(cred, order, physical_warehouse_id)
        previous = order.local_status
        if target == "NEW":
            released = release_order_reservation(db, order, note="MANUAL_STATUS_NEW")
            if released.get("changed"):
                _event(db, order, "PICKING_RESERVATION_RELEASED", value="NEW", data=released)
        elif target == "PACKED":
            _consume_picking_reservation_nonblocking(
                db, order, "MANUAL_STATUS_PACKED", warehouse_id=physical_warehouse_id,
            )
        elif target == "SENT" and previous != "PACKED":
            # FC_K9R20_PACKED_TO_SENT_NO_STOCK_REWORK: PACKED is already the physical
            # write-off boundary. Only legacy NEW/PICKING -> SENT needs stock finalization.
            _consume_picking_reservation_nonblocking(
                db, order, "MANUAL_STATUS_SENT", warehouse_id=physical_warehouse_id,
            )
        raw = dict(order.raw_data or {})
        # Explicit operator action may force one of the four FulfillCore workflow
        # buckets even when WB already exposes a terminal status. The WB status stays
        # visible separately; only the local warehouse bucket is overridden.
        raw["_fc_force_manual_status"] = bool(previous in {"CANCELLED", "DELIVERED"})
        order.raw_data = raw
        _set_manual_workflow_status(order, target)
        setattr(order, "manual_status_override", True) if hasattr(order, "manual_status_override") else None
        if target == "NEW":
            order.picking_list_generated_at = None
            order.last_picking_list_id = None
        elif target == "PICKING":
            # Manual status change is workflow-only. Never fabricate a picking-list
            # timestamp or address allocation: stock becomes reserved only when the
            # actual picking-list endpoint creates a MarketplacePickingList.
            if not order.last_picking_list_id:
                order.picking_list_generated_at = None
        elif target == "PACKED":
            order.packed_at = order.packed_at or _now()
        order.local_status = target
        _event(db, order, "WORKFLOW_MANUAL_STATUS", value=target, data={
            "from": previous, "override": True, "wb_changed": False,
        })
        changed += int(previous != target)
    db.commit()
    return {"ok": True, "changed": changed, "target_status": target, "selected": len(orders)}


@router.post("/wb/orders/workflow-reset")
def wb_reset_workflow(
    payload: WBWorkflowResetRequest,
    warehouse_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cred = _credential(db, payload.credential_id, "WB")
    orders = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id == payload.credential_id,
        MarketplaceOrder.external_order_num.in_(payload.order_ids),
    )).scalars().all()
    changed = 0
    for order in orders:
        _assert_wb_work(cred, order, warehouse_id)
        if order.local_status in {"CANCELLED", "DELIVERED"}:
            raise HTTPException(status_code=409, detail=f"Заказ {order.external_order_id}: конечный статус WB нельзя менять вручную.")
        if payload.target_status == "NEW":
            previous = order.local_status
            released = release_order_reservation(db, order, note="WORKFLOW_RESET_NEW")
            if released.get("changed"):
                _event(db, order, "PICKING_RESERVATION_RELEASED", value="NEW", data=released)
            _set_manual_workflow_status(order, "NEW")
            order.picking_list_generated_at = None
            order.last_picking_list_id = None
            order.local_status = "NEW"
            setattr(order, "manual_status_override", True) if hasattr(order, "manual_status_override") else None
            _event(db, order, "WORKFLOW_MANUAL_STATUS", value="NEW", data={"from": previous, "override": True})
            changed += 1
        elif payload.target_status == "PICKING":
            previous = order.local_status
            _set_manual_workflow_status(order, "PICKING")
            # PICKING without a generated FulfillCore list is status-only and must
            # not imply that any cell/container was reserved.
            if not order.last_picking_list_id:
                order.picking_list_generated_at = None
            order.local_status = "PICKING"
            setattr(order, "manual_status_override", True) if hasattr(order, "manual_status_override") else None
            _event(db, order, "WORKFLOW_MANUAL_STATUS", value="PICKING", data={"from": previous, "override": True})
            changed += 1
    db.commit()
    return {"ok": True, "changed": changed, "target_status": payload.target_status}


@router.post("/wb/orders/mark-sent")
def wb_mark_orders_sent(
    payload: WBShipPackedRequest,
    warehouse_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Fast, verified and monotonic local PACKED -> SENT transition.

    FC_K9R58_SENT_STICKY_GUARD: PACKED already proves the warehouse write-off, so this
    endpoint performs no WB network call and no stock consumption. It updates every logical
    credential-history row for the selected WB order number, commits once, and verifies that
    no active sibling remains NEW/PICKING/PACKED.
    """
    cred = _credential(db, payload.credential_id, "WB")
    # FC_K9R20_FAST_PACKED_SENT_STICKY (preserved/cumulative in r58)
    return _mark_packed_orders_sent_local(
        db, cred=cred, order_ids=payload.order_ids, physical_warehouse_id=warehouse_id,
        source="MARK_SENT",
    )


@router.post("/wb/shipments/from-packed")
def wb_ship_from_packed(
    payload: WBShipPackedRequest,
    warehouse_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cred, client = _wb(db, payload.credential_id)
    selected = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id == payload.credential_id,
        MarketplaceOrder.external_order_num.in_(payload.order_ids),
    )).scalars().all()
    if len(selected) != len(set(payload.order_ids)):
        raise HTTPException(status_code=404, detail="Часть выбранных заказов WB не найдена")
    for order in selected:
        _assert_wb_work(cred, order, warehouse_id)

    # Point safety check only for these orders. If another WMS sent one moments ago,
    # supplierStatus=complete turns it into SENT before we call deliver_supply again.
    _refresh_selected_wb_statuses(db, client, selected)
    already_sent = [o.external_order_id for o in selected if o.local_status in {"SENT", "DELIVERED"}]
    selected = [o for o in selected if o.local_status not in {"SENT", "DELIVERED"}]

    for order in selected:
        if order.local_status == "CANCELLED":
            raise HTTPException(status_code=409, detail=f"Заказ {order.external_order_id} уже отменён WB")
        if order.local_status != "PACKED":
            raise HTTPException(status_code=409, detail=f"Заказ {order.external_order_id} ещё не упакован")
        if _has_pending_wb_chz(order):
            raw = order.raw_data or {}
            pending = raw.get("_fc_chz_pending_wb") if isinstance(raw, dict) else {}
            reason = str((pending or {}).get("last_error") or (pending or {}).get("reason") or "ожидает проверки WB")
            raise HTTPException(
                status_code=409,
                detail=f"Заказ {order.external_order_id}: ЧЗ ещё не подтверждён Wildberries ({reason}). Этикетка могла быть напечатана, но отгрузка заблокирована до проверки маркировки.",
            )
        if not order.supply_id:
            raise HTTPException(status_code=409, detail=f"Заказ {order.external_order_id} не привязан к поставке WB")
    supply_ids = sorted({str(o.supply_id) for o in selected if o.supply_id})
    delivered: list[str] = []
    for supply_id in supply_ids:
        all_supply_orders = db.execute(select(MarketplaceOrder).where(
            MarketplaceOrder.credential_id == payload.credential_id,
            MarketplaceOrder.supply_id == supply_id,
        )).scalars().all()
        blocking = [o.external_order_id for o in all_supply_orders if o.local_status not in {"PACKED", "SENT", "DELIVERED", "CANCELLED"}]
        if blocking:
            raise HTTPException(status_code=409, detail=f"Поставка {supply_id} содержит ещё не упакованные заказы: {', '.join(blocking[:10])}")
        chz_blocking = [o.external_order_id for o in all_supply_orders if o.local_status == "PACKED" and _has_pending_wb_chz(o)]
        if chz_blocking:
            raise HTTPException(status_code=409, detail=f"Поставка {supply_id} содержит заказы с ЧЗ, которые WB ещё не подтвердил: {', '.join(chz_blocking[:10])}")
        client.deliver_supply(supply_id)
        for order in all_supply_orders:
            if order.local_status not in {"CANCELLED", "DELIVERED"}:
                order.supplier_status = "complete"
                setattr(order, "manual_status_override", False) if hasattr(order, "manual_status_override") else None
                order.local_status = "SENT"
                _event(db, order, "SUPPLY_DELIVERED_FROM_PACKED", value=supply_id)
        delivered.append(supply_id)
    db.commit()
    return {"ok": True, "delivered_supplies": delivered, "orders": len(selected), "already_sent": already_sent}


_WB_SYNC_INFLIGHT_LOCK = threading.Lock()
_WB_SYNC_INFLIGHT = set()


@router.post("/wb/orders/sync")
def wb_sync_orders(payload: WBSyncRequest, full_meta: bool = False, db: Session = Depends(get_db)):
    key = str(payload.credential_id)
    with _WB_SYNC_INFLIGHT_LOCK:
        if key in _WB_SYNC_INFLIGHT:
            raise HTTPException(status_code=409, detail="Синхронизация этого подключения WB уже выполняется. Дождитесь завершения.")
        if len(_WB_SYNC_INFLIGHT) >= 4:
            raise HTTPException(status_code=503, detail="Сервер выполняет другие синхронизации WB. Повторите позже.", headers={"Retry-After": "5"})
        _WB_SYNC_INFLIGHT.add(key)
    try:
        return _wb_sync_orders_impl(payload, full_meta=full_meta, db=db)
    finally:
        with _WB_SYNC_INFLIGHT_LOCK:
            _WB_SYNC_INFLIGHT.discard(key)


def _wb_sync_orders_impl(payload: WBSyncRequest, full_meta: bool = False, db: Session = Depends(get_db)):
    cred, client = _wb(db, payload.credential_id)
    physical_warehouse_id = getattr(payload, "warehouse_id", None)
    settings = _wb_settings(cred, physical_warehouse_id)
    if not settings:
        raise HTTPException(status_code=409, detail="Сначала настройте склады WB: выберите основной склад «Работа», второй при необходимости «Наблюдение», остальные «Не показывать».")

    selected_wb_warehouse_id = _wb_selected_warehouse_id(cred, payload.wb_warehouse_id, physical_warehouse_id)
    if selected_wb_warehouse_id is None:
        raise HTTPException(status_code=409, detail="Нет выбранного рабочего склада WB.")

    # Multiple warehouse PCs may keep the page open. Throttle by cabinet + selected
    # seller warehouse. A WATCH warehouse must not delay refresh of the WORK warehouse.
    key = f"{cred.id}:{physical_warehouse_id or 'legacy'}:{selected_wb_warehouse_id}"
    now_mono = time.monotonic()
    interval = _WB_FORCE_SYNC_INTERVAL if payload.force else _WB_AUTO_SYNC_INTERVAL
    logical_ids = _logical_credential_ids(db, cred)
    existing_selected = db.execute(select(MarketplaceOrder.id).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.wb_warehouse_id == selected_wb_warehouse_id,
    ).limit(1)).first()
    # 0.7.5.2: do not bulk-promote legacy PACKED rows to SENT. WB can generate a sticker
    # automatically, and a closed supply is not proof that every physical item left. The
    # full reconciliation below decides from CHZ + sticker + FulfillCore scan facts.
    # Shared PostgreSQL-backed throttle: several API workers and warehouse PCs now
    # claim the same slot atomically. This replaces the old process-local Lock/cache
    # without changing the visible sync cadence. Even force-sync keeps the 3 s guard.
    claimed = claim_runtime_interval(db, f"wb-sync:{key}", interval, force=False)
    if not claimed:
        # FC_K9R54_THROTTLED_RECONCILE_DEDUP: several warehouse PCs can hit the shared
        # sync throttle together. The old branch made every losing request scan the full
        # active order workspace. Keep the exact repair logic, but run that safety sweep
        # at most once per five minutes for this cabinet/warehouse scope.
        try:
            local_reconcile_due = claim_runtime_interval(
                db, f"wb-local-reconcile:{key}", 300.0, force=False
            )
        except Exception:
            try: db.rollback()
            except Exception: pass
            local_reconcile_due = False
        if local_reconcile_due:
            _reconcile_known_workspace_statuses(db, cred, selected_wb_warehouse_id)
        counts = _wb_status_counts(db, cred, selected_wb_warehouse_id, physical_warehouse_id)
        return {
            "ok": True,
            "throttled": True,
            "selected_wb_warehouse_id": selected_wb_warehouse_id,
            "total_local": sum(counts.values()),
            "counts": counts,
            "warnings": [],
        }

    # 0.9.4.4: claim/select work above opened a transaction. Close it before WB HTTP.
    db.commit()

    # A synchronization run is scoped to exactly one seller warehouse. This prevents
    # a WORK warehouse board from importing WATCH/other warehouse orders and cuts WB/API
    # traffic substantially.
    visible_ids = {int(selected_wb_warehouse_id)}
    warnings: list[str] = []
    synced = 0
    skipped_hidden = 0
    observed_warehouses: dict[str, int] = {}
    observed_offices: dict[str, int] = {}
    def observe(item: dict):
        wid, office = _wb_order_warehouse_values(item)
        if wid is not None: observed_warehouses[str(wid)] = observed_warehouses.get(str(wid), 0) + 1
        if office is not None: observed_offices[str(office)] = observed_offices.get(str(office), 0) + 1

    # NEW is a cheap feed, but it is not the source of the whole order board. Failure of
    # this endpoint must never prevent history already stored in PostgreSQL from loading.
    try:
        new_orders = client.new_orders()
    except HTTPException as exc:
        new_orders = []
        warnings.append(f"Новые заказы WB временно не обновлены: {exc.detail}")
    # Serialize only the short local write phase. The WB call above already finished.
    with process_lock(_wb_order_write_lock_key(cred)):
        for item in new_orders:
            observe(item)
            if not _wb_order_matches_selected_warehouse(cred, item, selected_wb_warehouse_id):
                skipped_hidden += 1
                continue
            source_wid, _ = _wb_order_warehouse_values(item)
            store_item = item
            if source_wid is not None and source_wid != selected_wb_warehouse_id:
                store_item = dict(item)
                store_item["_fc_source_warehouse_id"] = source_wid
                store_item["warehouseId"] = selected_wb_warehouse_id
            if _upsert_wb_order(db, cred, store_item):
                synced += 1
        # Persist cheap NEW-feed results immediately. A later history/status request may be
        # rate-limited, but already received orders must remain in PostgreSQL.
        db.commit()

    # `/api/v3/orders` is the durable recovery feed for confirm/complete/cancelled/etc.
    # Manual sync always walks the history. Automatic sync does so every five minutes or
    # immediately when the selected warehouse has no local rows.
    history_due = payload.force or (not existing_selected) or (now_mono - _WB_LAST_HISTORY_SYNC.get(key, 0.0) >= _WB_HISTORY_SYNC_INTERVAL)
    backfilled = 0
    history_seen = 0
    if history_due:
        try:
            # 0.7.8.5: import recent orders first and never truncate a busy 30-day cabinet at
            # exactly 100,000 rows. Normal sync focuses on the last 3 days so current WORK/WATCH
            # warehouses appear quickly; explicit “Импортировать данные WB” walks the full period.
            history_days = payload.days if full_meta else min(payload.days, 3)
            history = client.orders_recent_first(days=history_days, limit=1000, window_days=1)
            history_seen = len(history)
            for pos in range(0, len(history), 250):
                with process_lock(_wb_order_write_lock_key(cred)):
                    for item in history[pos:pos + 250]:
                        observe(item)
                        if not _wb_order_matches_selected_warehouse(cred, item, selected_wb_warehouse_id):
                            continue
                        source_wid, _ = _wb_order_warehouse_values(item)
                        store_item = item
                        if source_wid is not None and source_wid != selected_wb_warehouse_id:
                            store_item = dict(item)
                            store_item["_fc_source_warehouse_id"] = source_wid
                            store_item["warehouseId"] = selected_wb_warehouse_id
                        before = db.execute(select(MarketplaceOrder.id).where(
                            MarketplaceOrder.credential_id.in_(logical_ids),
                            MarketplaceOrder.provider == "WB",
                            MarketplaceOrder.external_order_id == str(item.get("id")),
                        )).scalar_one_or_none()
                        order = _upsert_wb_order(db, cred, store_item)
                        if order and before is None:
                            backfilled += 1
                    db.commit()  # each chunk becomes visible before the next one
            _WB_LAST_HISTORY_SYNC[key] = now_mono
        except HTTPException as exc:
            db.rollback()
            warnings.append(f"История заказов WB временно не обновлена: {exc.detail}")

    # Keep a small recent set for expensive metadata/sticker checks, but status repair is
    # deliberately broader. In 0.7.4 only 500 rows were refreshed, so thousands of old
    # sold/cancelled rows still need terminal repair, but confirm/complete are retained only
    # as WB diagnostics: without CHZ they must not remove an order from the local work queue.
    # A forced sync repairs EVERY stored order in WB-supported batches of up to 1000 IDs.
    selected_orders = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.wb_warehouse_id == selected_wb_warehouse_id,
    ).order_by(MarketplaceOrder.external_created_at.desc().nullslast(), MarketplaceOrder.created_at.desc()).limit(700)).scalars().all()

    status_base = select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.wb_warehouse_id == selected_wb_warehouse_id,
    )
    # FC_K9R54_STATUS_ROTATION: warehouse work states remain one-minute fresh, while
    # SENT/terminal history is rotated by least-recently-synced order instead of loading
    # thousands of already-shipped ORM rows on every workstation minute. No row is lost:
    # _apply_status updates last_synced_at, so the least-recently-checked rows move next.
    # Explicit force=True remains a complete full-history repair.
    if payload.force:
        status_candidates = db.execute(status_base.order_by(
            MarketplaceOrder.external_created_at.desc().nullslast(), MarketplaceOrder.created_at.desc()
        )).scalars().all()
    else:
        least_checked = (
            MarketplaceOrder.last_synced_at.asc().nullsfirst(),
            MarketplaceOrder.external_created_at.asc().nullsfirst(),
            MarketplaceOrder.created_at.asc(),
        )
        work_rows = db.execute(status_base.where(or_(
            MarketplaceOrder.local_status.is_(None),
            MarketplaceOrder.local_status.in_(("NEW", "PICKING", "PACKED")),
        )).order_by(*least_checked).limit(2500)).scalars().all()
        sent_rows = db.execute(status_base.where(
            MarketplaceOrder.local_status == "SENT",
        ).order_by(*least_checked).limit(1000)).scalars().all()
        terminal_rows = db.execute(status_base.where(
            MarketplaceOrder.local_status.in_(("DELIVERED", "CANCELLED")),
        ).order_by(*least_checked).limit(250)).scalars().all()
        seen_ids: set[uuid.UUID] = set()
        status_candidates = []
        for order in work_rows + sent_rows + terminal_rows:
            if order.id not in seen_ids:
                seen_ids.add(order.id)
                status_candidates.append(order)

    by_id: dict[str, dict[str, Any]] = {}
    numeric_pairs = [(o.external_order_num, o.external_order_id) for o in status_candidates if o.external_order_num is not None]
    status_batches = 0
    status_requested = 0
    # 0.9.4.4 transaction barrier: numeric_pairs are immutable, WB HTTP needs no DB lock.
    db.commit()
    try:
        for offset in range(0, len(numeric_pairs), 1000):
            batch = numeric_pairs[offset:offset + 1000]
            ids = [x[0] for x in batch]
            if not ids:
                continue
            statuses = client.order_statuses(ids)
            status_batches += 1
            status_requested += len(ids)
            for st in statuses:
                sid = st.get("id") or st.get("orderId") or st.get("order_id")
                if sid is not None:
                    by_id[str(sid)] = st
    except HTTPException as exc:
        warnings.append(f"Статусы WB обновлены частично: {exc.detail}")

    status_updated = 0
    # Worker and browser sync used to flush overlapping rows in opposite orders. Serialize
    # short chunks and commit each one before releasing the lock.
    for pos in range(0, len(status_candidates), 250):
        with process_lock(_wb_order_write_lock_key(cred)):
            for order in status_candidates[pos:pos + 250]:
                st = by_id.get(order.external_order_id)
                before = (order.local_status, order.supplier_status, order.wb_status)
                if st:
                    _apply_status(db, order, st)
                else:
                    _reconcile_local_status(db, order, "LOCAL_FACTS_AFTER_SYNC")
                _repair_legacy_sent_status(db, order)
                after = (order.local_status, order.supplier_status, order.wb_status)
                if after != before:
                    status_updated += 1
            db.commit()

    # Marking/sticker reconciliation is best-effort and bounded to the recent working set.
    for pos in range(0, len(selected_orders), 250):
        with process_lock(_wb_order_write_lock_key(cred)):
            for order in selected_orders[pos:pos + 250]:
                _reconcile_local_status(db, order, "KNOWN_PACKING_FACTS_SWEEP")
            db.commit()

    # No marketplace_orders UPDATE transaction is carried into metadata/sticker WB calls.
    meta_due = payload.force or full_meta or (now_mono - _WB_LAST_META_SWEEP.get(key, 0.0) >= _WB_META_SWEEP_INTERVAL)
    meta_checked = meta_changed = 0
    if meta_due:
        # Normal/background refresh stays deliberately small. The explicit "Импорт данных WB"
        # button uses full_meta=true and walks the entire actionable queue, prioritising NEW
        # and PICKING rows. This gives the warehouse a deterministic way to pull external
        # CHZ + WB stickers when the background sweep is lagging behind another WMS.
        source = status_candidates if full_meta else selected_orders
        candidates = [o for o in source if o.local_status not in {"DELIVERED", "CANCELLED"}]
        def _checked_ts(o):
            dt = _parse_dt((o.raw_data or {}).get("_fc_meta_checked_at"))
            return dt.timestamp() if dt else 0.0
        priority = {"NEW": 0, "PICKING": 1, "PACKED": 2, "SENT": 3}
        # Pending CHZ must be retried before the normal metadata queue so a warehouse-first
        # packed order does not wait behind thousands of NEW rows.
        candidates.sort(key=lambda o: (-1 if _has_pending_wb_chz(o) else priority.get(o.local_status, 9), _checked_ts(o), (o.external_created_at or o.created_at).timestamp() if (o.external_created_at or o.created_at) else 0.0))
        try:
            if full_meta:
                # Keep WB requests bounded. Stop on a throttled batch instead of hammering
                # the marketplace API; already imported rows are committed below.
                for pos in range(0, len(candidates), 250):
                    meta_result = _refresh_wb_metadata_batch(db, client, candidates[pos:pos + 250], order_write_key=_wb_order_write_lock_key(cred))
                    if meta_result["checked"] == 0 and candidates[pos:pos + 250]:
                        warnings.append("Импорт ЧЗ/этикеток WB остановлен по лимиту API. Нажмите импорт ещё раз через минуту.")
                        break
                    meta_checked += meta_result["checked"]
                    meta_changed += meta_result["changed"]
                    db.commit()
                    time.sleep(0.12)
            else:
                meta_result = _refresh_wb_metadata_batch(db, client, candidates[:300], order_write_key=_wb_order_write_lock_key(cred))
                meta_checked, meta_changed = meta_result["checked"], meta_result["changed"]
            _WB_LAST_META_SWEEP[key] = now_mono
        except HTTPException as exc:
            warnings.append(f"Коды маркировки/этикетки обновятся позже: {exc.detail}")

    # 0.9.4.13 SALE-FIRST: NEW orders reserve sellable SKU capacity immediately, but
    # deliberately do not choose a cell/container until the real picking list is created.
    immediate_reservation = {"created": 0, "kept": 0, "already_consumed": 0, "shortage": 0, "mode": "SALE_FIRST_V1"}
    if physical_warehouse_id:
        # 0.9.4.2 DATABASE STABILITY:
        # WB synchronization performs external HTTP calls. PostgreSQL is configured with
        # idle_in_transaction_session_timeout, so a transaction left open while waiting for
        # WB can be terminated by PostgreSQL. Never start immediate reservation on top of
        # such a stale/aborted transaction. Close the preceding sync transaction first and,
        # if PostgreSQL has already invalidated it, rollback so SQLAlchemy can reconnect.
        try:
            db.commit()
        except Exception:
            logger.exception("0.9.4.2 pre-reservation DB transaction was invalid; rolling back")
            try:
                db.rollback()
            except Exception:
                logger.exception("0.9.4.2 rollback after invalid pre-reservation transaction failed")

        try:
            # FC_PICKING_ORDER_FIRST_SERIALIZATION_09415G5A8K3_20260905
            # SKU-link repair can flush marketplace_orders before SALE_FIRST touches stock.
            # Keep that complete DB transaction under the same writer lock used by picking,
            # and do not release it until commit. This removes the order->stock vs
            # stock->order inversion that caused PostgreSQL DeadlockDetected.
            with process_lock(_wb_order_write_lock_key(cred)):
                with distributed_lock(db, f"wb-arrival-reserve:{cred.client_id}:{physical_warehouse_id}", blocking=True):
                    immediate_reservation = _reserve_new_wb_orders_immediately(
                        db,
                        cred=cred,
                        physical_warehouse_id=physical_warehouse_id,
                        selected_wb_warehouse_id=selected_wb_warehouse_id,
                    )
                # Reservation is its own short transaction. Keep commit inside the writer
                # lock so no picking transition can start while these row locks are live.
                db.commit()
        except HTTPException as exc:
            # SQLAlchemy requires rollback after any failed statement/flush before the
            # Session can be used again. One bad reservation must not poison the WB page.
            try:
                db.rollback()
            except Exception:
                logger.exception("0.9.4.2 rollback after reservation HTTP error failed")
            warnings.append(f"Резерв новых FBS-заказов: {exc.detail}")
        except Exception as exc:
            try:
                db.rollback()
            except Exception:
                logger.exception("0.9.4.2 rollback after reservation failure failed")
            logger.exception("0.9.4.2 immediate WB reservation failed safely")
            warnings.append(f"Резерв новых FBS-заказов временно не выполнен: {exc}")
    else:
        db.commit()

    counts = _wb_status_counts(db, cred, selected_wb_warehouse_id, physical_warehouse_id)
    return {
        "ok": True,
        "synced": synced,
        "backfilled": backfilled,
        "history_seen": history_seen,
        "skipped_hidden": skipped_hidden,
        "selected_wb_warehouse_id": selected_wb_warehouse_id,
        "total_local": sum(counts.values()),
        "counts": counts,
        "meta_checked": meta_checked,
        "meta_changed": meta_changed,
        "status_requested": status_requested,
        "status_batches": status_batches,
        "status_updated": status_updated,
        "warnings": warnings,
        "immediate_fbs_reservation": immediate_reservation,
        "physical_warehouse_id": str(physical_warehouse_id) if physical_warehouse_id else None,
        "warehouse_diagnostics": {
            "selected_id": selected_wb_warehouse_id,
            "selected_name": (settings.get(str(selected_wb_warehouse_id)) or {}).get("name"),
            "selected_office_id": (settings.get(str(selected_wb_warehouse_id)) or {}).get("office_id"),
            "observed_warehouse_ids": observed_warehouses,
            "observed_office_ids": observed_offices,
        },
    }


@router.get("/wb/warehouse-diagnostics")
def wb_warehouse_diagnostics(
    credential_id: uuid.UUID,
    wb_warehouse_id: int,
    warehouse_id: uuid.UUID | None = Query(default=None),
    days: int = Query(default=30, ge=1, le=30),
    order_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
):
    cred, client = _wb(db, credential_id)
    settings = _wb_settings(cred, warehouse_id)
    selected = settings.get(str(wb_warehouse_id)) or {}
    new_rows=[]; history=[]; warnings=[]
    live_warehouse=None
    try:
        current_warehouses = _wb_seller_warehouses(client)
        live_warehouse = next((x for x in current_warehouses if int(x.get("id") or x.get("warehouseId") or -1) == int(wb_warehouse_id)), None)
    except Exception as exc:
        warnings.append(f"warehouses: {_external_error_text(exc)}")
    try:
        new_rows=client.new_orders()
    except HTTPException as exc:
        warnings.append(f"new: {exc.detail}")
    # Diagnostics should be fast and representative. Scan the last three days completely
    # rather than the former 30-day / 100k truncated aggregate. Exact order lookup below can
    # search the requested full period independently.
    history_scan_days=min(days, 3)
    try:
        history=client.orders_recent_first(days=history_scan_days, limit=1000, window_days=1)
    except HTTPException as exc:
        warnings.append(f"history: {exc.detail}")

    def summarize(rows):
        wh={}; offices={}; matched=0
        for item in rows:
            wid, office=_wb_order_warehouse_values(item)
            if wid is not None: wh[str(wid)]=wh.get(str(wid),0)+1
            if office is not None: offices[str(office)]=offices.get(str(office),0)+1
            if _wb_order_matches_selected_warehouse(cred,item,wb_warehouse_id): matched+=1
        return {"rows":len(rows),"matched":matched,"warehouse_ids":wh,"office_ids":offices}

    exact=None
    if order_id is not None:
        try:
            item=client.find_order(order_id, days=days)
            if item:
                wid, office=_wb_order_warehouse_values(item)
                exact={
                    "found": True,
                    "id": item.get("id") or item.get("orderId") or item.get("order_id"),
                    "warehouse_id": wid,
                    "office_id": office,
                    "cargo_type": item.get("cargoType"),
                    "supply_id": item.get("supplyId"),
                    "created_at": item.get("createdAt"),
                    "article": item.get("article"),
                    "skus": item.get("skus"),
                    "required_meta": item.get("requiredMeta"),
                    "optional_meta": item.get("optionalMeta"),
                    "matches_selected": _wb_order_matches_selected_warehouse(cred,item,wb_warehouse_id),
                }
            else:
                exact={"found": False, "id": order_id}
            try:
                statuses=client.order_statuses([int(order_id)])
                if exact is None: exact={"found": False, "id": order_id}
                exact["statuses"] = statuses
            except HTTPException as exc:
                warnings.append(f"order status {order_id}: {exc.detail}")
            try:
                meta=client.order_meta([int(order_id)])
                if exact is None: exact={"found": False, "id": order_id}
                exact["meta"] = meta
            except HTTPException as exc:
                warnings.append(f"order meta {order_id}: {exc.detail}")
        except HTTPException as exc:
            warnings.append(f"order {order_id}: {exc.detail}")

    return {
        "selected":{
            "id":wb_warehouse_id,
            "name":selected.get("name"),
            "office_id":selected.get("office_id"),
            "mode":selected.get("mode"),
            "physical_warehouse_id":str(warehouse_id) if warehouse_id else None,
        },
        "live_warehouse": live_warehouse,
        "history_scan_days": history_scan_days,
        "new":summarize(new_rows),
        "history":summarize(history),
        "exact_order": exact,
        "warnings":warnings,
    }


def _normalize_wb_brand_filters(values: list[str] | None) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        value = str(raw or "").strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _wb_brand_article_subquery(client_id: uuid.UUID, brands: list[str]):
    normalized = [x.casefold() for x in _normalize_wb_brand_filters(brands)]
    return select(MarketplaceProduct.vendor_code).where(
        MarketplaceProduct.client_id == client_id,
        MarketplaceProduct.provider == "WB",
        MarketplaceProduct.vendor_code.is_not(None),
        MarketplaceProduct.vendor_code != "",
        func.lower(func.trim(MarketplaceProduct.brand)).in_(normalized),
    )


@router.get("/wb/order-brands")
def wb_order_brands(
    credential_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    wb_warehouse_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Return the selectable brand list for the current physical/WB warehouse.

    The list is based on persisted orders and imported WB product cards. It is not
    limited to the current page or status tab, so the same multi-select works in
    «Новые», «Упакованные» and every other order status.
    """
    cred = _credential(db, credential_id, "WB")
    # FC_K9R52_ORDER_BRANDS_NO_HISTORY_MERGE: switching a status tab is a read-only path.
    # Credential-history consolidation remains in sync/write paths where it belongs.
    selected_wb_warehouse_id = _wb_selected_warehouse_id(cred, wb_warehouse_id, warehouse_id)
    if selected_wb_warehouse_id is None:
        return {"brands": []}
    logical_ids = _logical_credential_ids(db, cred)
    order_articles = select(MarketplaceOrder.article).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.wb_warehouse_id == selected_wb_warehouse_id,
        MarketplaceOrder.article.is_not(None),
        MarketplaceOrder.article != "",
    ).distinct()
    rows = db.execute(select(MarketplaceProduct.brand).where(
        MarketplaceProduct.client_id == cred.client_id,
        MarketplaceProduct.provider == "WB",
        MarketplaceProduct.vendor_code.in_(order_articles),
        MarketplaceProduct.brand.is_not(None),
        MarketplaceProduct.brand != "",
    ).distinct()).scalars().all()
    brands = sorted(_normalize_wb_brand_filters([str(x) for x in rows if x]), key=lambda x: x.casefold())
    db.rollback()
    return {"brands": brands}


@router.get("/wb/orders")
def wb_orders(
    credential_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    wb_warehouse_id: int | None = Query(default=None),
    local_status: str | None = Query(default=None),
    order_number: str | None = Query(default=None),
    article: str | None = Query(default=None),
    location: str | None = Query(default=None),
    supply: str | None = Query(default=None),
    search: str | None = Query(default=None),
    name: str | None = Query(default=None),
    size: str | None = Query(default=None),
    barcode: str | None = Query(default=None),
    chz: str | None = Query(default=None),
    chz_state: str | None = Query(default=None),
    label: str | None = Query(default=None),
    brand: list[str] | None = Query(default=None),
    sort_by: str | None = Query(default=None),
    sort_dir: str = Query(default="asc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
    include_details: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    cred = _credential(db, credential_id, "WB")
    # FC_K9R52_ORDER_BOARD_NO_HISTORY_MERGE: ordinary status/page reads never rewrite credential history.
    selected_wb_warehouse_id = _wb_selected_warehouse_id(cred, wb_warehouse_id, warehouse_id)
    if selected_wb_warehouse_id is None:
        return {
            "orders": [], "count": 0, "total": 0, "page": 1, "page_size": page_size,
            "pages": 1, "counts": {key: 0 for key in LOCAL_STATUS_NAMES},
            "selected_wb_warehouse_id": None, "persisted": True,
        }

    # Order board reads only PostgreSQL. No WB calls and no full-table reconciliation
    # happen here, so status tabs/pages switch immediately. Background/manual sync owns
    # reconciliation.
    logical_ids = _logical_credential_ids(db, cred)
    stmt = select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.wb_warehouse_id == selected_wb_warehouse_id,
    )
    selected_brands = _normalize_wb_brand_filters(brand)
    if selected_brands:
        stmt = stmt.where(MarketplaceOrder.article.in_(_wb_brand_article_subquery(cred.client_id, selected_brands)))
    if local_status:
        stmt = stmt.where(MarketplaceOrder.local_status == local_status.upper())
    if order_number and order_number.strip():
        stmt = stmt.where(MarketplaceOrder.external_order_id.ilike(f"%{order_number.strip()}%"))
    if article and article.strip():
        stmt = stmt.where(MarketplaceOrder.article.ilike(f"%{article.strip()}%"))
    if supply and supply.strip():
        stmt = stmt.where(MarketplaceOrder.supply_id.ilike(f"%{supply.strip()}%"))
    if chz and chz.strip():
        stmt = stmt.where(MarketplaceOrder.chz_code.ilike(f"%{chz.strip()}%"))
    normalized_chz_state = str(chz_state or "").strip().upper()
    if normalized_chz_state == "MISSING":
        stmt = stmt.where(or_(MarketplaceOrder.chz_code.is_(None), MarketplaceOrder.chz_code == ""))
    elif normalized_chz_state == "ASSIGNED":
        stmt = stmt.where(MarketplaceOrder.chz_code.is_not(None), MarketplaceOrder.chz_code != "")
    if label and label.strip():
        stmt = stmt.where(cast(MarketplaceOrder.raw_data, Text).ilike(f"%{label.strip()}%"))
    if name and name.strip():
        needle = f"%{name.strip()}%"
        matching_skus = select(SKU.id).where(SKU.client_id == cred.client_id, SKU.name.ilike(needle))
        matching_articles = select(MarketplaceProduct.vendor_code).where(
            MarketplaceProduct.client_id == cred.client_id,
            MarketplaceProduct.provider == "WB",
            MarketplaceProduct.title.ilike(needle),
            MarketplaceProduct.vendor_code.is_not(None),
        )
        stmt = stmt.where(or_(MarketplaceOrder.local_sku_id.in_(matching_skus), MarketplaceOrder.article.ilike(needle), MarketplaceOrder.article.in_(matching_articles)))
    if size and size.strip():
        needle = f"%{size.strip()}%"
        matching_skus = select(SKU.id).where(SKU.client_id == cred.client_id, SKU.size.ilike(needle))
        matching_articles = select(MarketplaceProduct.vendor_code).where(
            MarketplaceProduct.client_id == cred.client_id,
            MarketplaceProduct.provider == "WB",
            cast(MarketplaceProduct.sizes, Text).ilike(needle),
            MarketplaceProduct.vendor_code.is_not(None),
        )
        stmt = stmt.where(or_(MarketplaceOrder.local_sku_id.in_(matching_skus), cast(MarketplaceOrder.raw_data, Text).ilike(needle), MarketplaceOrder.article.in_(matching_articles)))
    if barcode and barcode.strip():
        needle = f"%{barcode.strip()}%"
        matching_skus = select(SKU.id).where(SKU.client_id == cred.client_id, SKU.barcode.ilike(needle))
        stmt = stmt.where(or_(MarketplaceOrder.local_sku_id.in_(matching_skus), cast(MarketplaceOrder.barcodes, Text).ilike(needle)))
    if search and search.strip():
        needle = f"%{search.strip()}%"
        sku_ids = select(SKU.id).where(
            SKU.client_id == cred.client_id,
            or_(SKU.name.ilike(needle), SKU.article.ilike(needle), SKU.sku_code.ilike(needle), SKU.barcode.ilike(needle)),
        )
        stmt = stmt.where(or_(
            MarketplaceOrder.external_order_id.ilike(needle),
            MarketplaceOrder.order_uid.ilike(needle),
            MarketplaceOrder.article.ilike(needle),
            cast(MarketplaceOrder.barcodes, Text).ilike(needle),
            MarketplaceOrder.local_sku_id.in_(sku_ids),
        ))

    if location and location.strip():
        needle = location.strip().lower()
        matching_order_ids: set[str] = set()
        picking_lists = db.execute(select(MarketplacePickingList).where(
            MarketplacePickingList.credential_id.in_(logical_ids)
        )).scalars().all()
        for plist in picking_lists:
            for row in plist.rows or []:
                hay = " ".join([str(row.get("location") or ""), str(row.get("container") or "")]).lower()
                if needle in hay:
                    matching_order_ids.update(str(x) for x in (row.get("order_ids") or []))
        if not matching_order_ids:
            return {
                "orders": [], "count": 0, "total": 0, "page": 1, "page_size": page_size,
                "pages": 1, "counts": _wb_status_counts(
                    db, cred, selected_wb_warehouse_id, warehouse_id, brands=selected_brands
                ),
                "selected_wb_warehouse_id": selected_wb_warehouse_id, "persisted": True,
            }
        stmt = stmt.where(MarketplaceOrder.external_order_id.in_(matching_order_ids))

    total = db.execute(select(func.count()).select_from(stmt.order_by(None).subquery())).scalar_one()
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)

    sort_columns = {
        "status": MarketplaceOrder.local_status,
        "order": MarketplaceOrder.external_order_num,
        "article": MarketplaceOrder.article,
        "barcode": cast(MarketplaceOrder.barcodes, Text),
        "supply": MarketplaceOrder.supply_id,
        "chz": MarketplaceOrder.chz_code,
        "created": func.coalesce(MarketplaceOrder.external_created_at, MarketplaceOrder.created_at),
    }
    sort_column = sort_columns.get(str(sort_by or ""))
    direction = "desc" if str(sort_dir).lower() == "desc" else "asc"
    if sort_column is not None:
        primary_sort = sort_column.desc().nullslast() if direction == "desc" else sort_column.asc().nullslast()
        ordering = (primary_sort, MarketplaceOrder.external_created_at.asc().nullslast(), MarketplaceOrder.created_at.asc())
    else:
        ordering = (MarketplaceOrder.external_created_at.asc().nullslast(), MarketplaceOrder.created_at.asc())
    paged = stmt.order_by(*ordering).offset((page - 1) * page_size).limit(page_size)
    orders = db.execute(paged).scalars().all()
    # FC_K9R53_WB_ORDER_FIRST_PAINT_SPLIT: browser explicitly asks for the light page,
    # then hydrates card/reservation/stock data with a separate local-only request.
    items = (_orders_response_batch(db, orders, warehouse_id, cred, repair_links=False)
             if include_details else _orders_response_fast(db, orders, warehouse_id, cred))
    response = {
        "orders": items,
        "count": len(items),
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "counts": _wb_status_counts(
            db, cred, selected_wb_warehouse_id, warehouse_id, brands=selected_brands
        ),
        "selected_wb_warehouse_id": selected_wb_warehouse_id,
        "persisted": True,
        "logical_credentials": len(logical_ids),
    }

    db.rollback()
    return response


def _wb_status_counts(
    db: Session,
    cred: MarketplaceCredential,
    wb_warehouse_id: int | None = None,
    physical_warehouse_id: uuid.UUID | str | None = None,
    brands: list[str] | None = None,
) -> dict[str, int]:
    selected_wb_warehouse_id = _wb_selected_warehouse_id(cred, wb_warehouse_id, physical_warehouse_id)
    counts = {key: 0 for key in LOCAL_STATUS_NAMES}
    if selected_wb_warehouse_id is None:
        return counts
    logical_ids = _logical_credential_ids(db, cred)
    stmt = select(MarketplaceOrder.local_status, func.count(MarketplaceOrder.id)).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.wb_warehouse_id == selected_wb_warehouse_id,
    )
    selected_brands = _normalize_wb_brand_filters(brands)
    if selected_brands:
        stmt = stmt.where(MarketplaceOrder.article.in_(_wb_brand_article_subquery(cred.client_id, selected_brands)))
    rows = db.execute(stmt.group_by(MarketplaceOrder.local_status)).all()
    for status, value in rows:
        counts[status] = int(value or 0)
    return counts


@router.post("/wb/orders/enrich")
def wb_orders_enrich(payload: dict[str, Any], db: Session = Depends(get_db)):
    """Hydrate only optional fields for the already visible order page.

    FC_K9R53_WB_ORDER_BACKGROUND_ENRICH: this endpoint is PostgreSQL-only. It never
    synchronizes WB and never changes order/reservation/inventory state.
    """
    try:
        credential_id = uuid.UUID(str(payload.get("credential_id") or ""))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="credential_id is required") from exc
    warehouse_id = None
    if payload.get("warehouse_id"):
        try:
            warehouse_id = uuid.UUID(str(payload.get("warehouse_id")))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="warehouse_id is invalid") from exc
    requested_ids: list[uuid.UUID] = []
    for value in (payload.get("order_ids") or [])[:500]:
        try:
            requested_ids.append(uuid.UUID(str(value)))
        except Exception:
            continue
    fields = {str(x).strip().lower() for x in (payload.get("fields") or [])}
    fields &= {"card", "reserved", "stock"}
    if not requested_ids or not fields:
        return {"details": [], "fields": sorted(fields), "persisted": True}

    cred = _credential(db, credential_id, "WB")
    logical_ids = _logical_credential_ids(db, cred)
    rows = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.id.in_(requested_ids),
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
    )).scalars().all()
    by_id = {o.id: o for o in rows}
    orders = [by_id[x] for x in requested_ids if x in by_id]
    details: dict[uuid.UUID, dict[str, Any]] = {o.id: {"id": str(o.id)} for o in orders}

    if "card" in fields and orders:
        nm_ids: set[int] = set(); articles: set[str] = set()
        for order in orders:
            raw = order.raw_data or {}; nm = raw.get("nmId") or raw.get("nmID") or raw.get("nm_id")
            try:
                if nm is not None: nm_ids.add(int(nm))
            except Exception:
                pass
            if order.article: articles.add(str(order.article))
        clauses = []
        if nm_ids: clauses.append(MarketplaceProduct.external_product_id.in_(nm_ids))
        if articles: clauses.append(MarketplaceProduct.vendor_code.in_(articles))
        cards = db.execute(select(MarketplaceProduct).options(load_only(
            MarketplaceProduct.id, MarketplaceProduct.client_id, MarketplaceProduct.external_product_id,
            MarketplaceProduct.vendor_code, MarketplaceProduct.title, MarketplaceProduct.brand,
            MarketplaceProduct.photos, MarketplaceProduct.sizes, MarketplaceProduct.updated_at, MarketplaceProduct.created_at
        )).where(
            MarketplaceProduct.client_id == cred.client_id, MarketplaceProduct.provider == "WB", or_(*clauses)
        ).order_by(MarketplaceProduct.updated_at.desc(), MarketplaceProduct.created_at.desc())).scalars().all() if clauses else []
        card_by_nm: dict[int, MarketplaceProduct] = {}; card_by_article: dict[str, MarketplaceProduct] = {}
        for card in cards:
            if card.external_product_id is not None and card.external_product_id not in card_by_nm:
                card_by_nm[card.external_product_id] = card
            if card.vendor_code and str(card.vendor_code) not in card_by_article:
                card_by_article[str(card.vendor_code)] = card
        for order in orders:
            raw = order.raw_data or {}; nm = raw.get("nmId") or raw.get("nmID") or raw.get("nm_id")
            try: nm = int(nm) if nm is not None else None
            except Exception: nm = None
            card = card_by_nm.get(nm) or card_by_article.get(str(order.article or ""))
            order_size = _wb_order_size(order, card, None)
            details[order.id]["product_card"] = ({
                "id": str(card.id), "nm_id": card.external_product_id, "title": card.title, "brand": card.brand,
                "vendor_code": card.vendor_code, "size": order_size,
                "photo": ((card.photos or [{}])[0].get("big") or (card.photos or [{}])[0].get("c516x688") or (card.photos or [{}])[0].get("square")) if card.photos else None,
            } if card else None)
            details[order.id]["card_loaded"] = True

    if "reserved" in fields and orders:
        target_external = {str(o.external_order_id): o.id for o in orders}
        reserved_map: dict[uuid.UUID, list[dict[str, Any]]] = {o.id: [] for o in orders}
        plist_ids: set[uuid.UUID] = set()
        for order in orders:
            if not order.last_picking_list_id: continue
            try: plist_ids.add(uuid.UUID(str(order.last_picking_list_id)))
            except Exception: pass
        plist_stmt = select(MarketplacePickingList).where(MarketplacePickingList.id.in_(plist_ids)) if plist_ids else None
        if plist_stmt is not None and warehouse_id is not None:
            plist_stmt = plist_stmt.where(MarketplacePickingList.warehouse_id == warehouse_id)
        plists = db.execute(plist_stmt).scalars().all() if plist_stmt is not None else []
        for plist in plists:
            for row in plist.rows or []:
                for external_id in (row.get("order_ids") or []):
                    local_id = target_external.get(str(external_id))
                    if local_id is not None:
                        reserved_map[local_id].append({
                            "location": row.get("location") or "", "container": row.get("container") or "",
                            "quantity": 1, "status": row.get("status") or "", "picking_list_id": str(plist.id),
                        })
        for order in orders:
            details[order.id]["reserved"] = reserved_map.get(order.id, [])
            details[order.id]["reserved_loaded"] = True

    if "stock" in fields and orders:
        sku_ids = {o.local_sku_id for o in orders if o.local_sku_id}
        stock_map: dict[uuid.UUID, list[dict[str, Any]]] = {sid: [] for sid in sku_ids}
        if warehouse_id and sku_ids:
            stock_rows = db.execute(
                select(InventoryBalance, Location, Container)
                .join(Location, Location.id == InventoryBalance.location_id)
                .outerjoin(Container, Container.id == InventoryBalance.container_id)
                .where(
                    InventoryBalance.client_id == cred.client_id, InventoryBalance.sku_id.in_(sku_ids),
                    InventoryBalance.warehouse_id == warehouse_id, InventoryBalance.quantity > 0,
                )
                .order_by(InventoryBalance.sku_id, Location.code, Container.name, Container.barcode)
            ).all()
            ids_as_text = [str(x) for x in sku_ids]
            storage_by_bucket: dict[tuple[str, str, str], float] = {}
            reserve_by_bucket: dict[tuple[str, str, str], float] = {}
            if ids_as_text:
                storage_stmt = text("""
                    SELECT CAST(sku_id AS varchar) AS sku_id, CAST(location_id AS varchar) AS location_id,
                           COALESCE(container_key,'') AS container_key, COALESCE(SUM(storage_only),0) AS storage_only
                    FROM fc_stocks_v2
                    WHERE client_id=:client AND warehouse_id=:warehouse AND CAST(sku_id AS varchar) IN :ids
                    GROUP BY CAST(sku_id AS varchar),CAST(location_id AS varchar),COALESCE(container_key,'')
                """).bindparams(bindparam('ids', expanding=True))
                for x in db.execute(storage_stmt, {'client': str(cred.client_id), 'warehouse': str(warehouse_id), 'ids': ids_as_text}).mappings():
                    storage_by_bucket[(str(x['sku_id']), str(x['location_id']), str(x['container_key'] or ''))] = max(0.0, float(x['storage_only'] or 0))
                reserve_stmt = text("""
                    SELECT CAST(r.sku_id AS varchar) AS sku_id, r.source_location_id AS location_id,
                           COALESCE(r.source_container_id,'') AS container_key,
                           COALESCE(SUM(CASE WHEN r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL THEN 0 ELSE r.quantity END),0) AS reserved
                    FROM fc_stock_reservations r
                    LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
                    LEFT JOIN inventory_operations io
                      ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar) AND COALESCE(io.status,'COMPLETED')='COMPLETED'
                     AND io.idempotency_key=('stock-core-v2-pack-' || CAST(o.id AS varchar) || '-' || CAST(r.id AS varchar))
                    WHERE r.client_id=:client AND r.warehouse_id=:warehouse AND CAST(r.sku_id AS varchar) IN :ids
                      AND r.source_location_id IS NOT NULL AND r.status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
                    GROUP BY CAST(r.sku_id AS varchar),r.source_location_id,COALESCE(r.source_container_id,'')
                """).bindparams(bindparam('ids', expanding=True))
                for x in db.execute(reserve_stmt, {'client': str(cred.client_id), 'warehouse': str(warehouse_id), 'ids': ids_as_text}).mappings():
                    reserve_by_bucket[(str(x['sku_id']), str(x['location_id']), str(x['container_key'] or ''))] = max(0.0, float(x['reserved'] or 0))
            container_ids = {container.id for _balance, _loc, container in stock_rows if container}
            container_totals: dict[uuid.UUID, float] = {}
            if container_ids:
                container_totals = {
                    cid: float(qty or 0) for cid, qty in db.execute(select(
                        InventoryBalance.container_id, func.coalesce(func.sum(InventoryBalance.quantity), 0)
                    ).where(
                        InventoryBalance.client_id == cred.client_id, InventoryBalance.warehouse_id == warehouse_id,
                        InventoryBalance.container_id.in_(container_ids), InventoryBalance.quantity > 0,
                    ).group_by(InventoryBalance.container_id)).all()
                }
            for balance, loc, container in stock_rows:
                bucket = (str(balance.sku_id), str(loc.id), str(container.id) if container else "")
                physical = max(0.0, float(balance.quantity or 0)); storage_only = min(physical, storage_by_bucket.get(bucket, 0.0)); reserved = reserve_by_bucket.get(bucket, 0.0)
                stock_map.setdefault(balance.sku_id, []).append({
                    "location_id": str(loc.id), "location": loc.code, "location_name": loc.name, "location_type": loc.location_type,
                    "container_id": str(container.id) if container else None, "container": (container.name or container.barcode) if container else None,
                    "container_barcode": container.barcode if container else None, "container_total_quantity": container_totals.get(container.id, 0.0) if container else 0.0,
                    "quantity": physical, "reserved_quantity": reserved, "free_quantity": max(0.0, physical - storage_only - reserved),
                })
        for order in orders:
            details[order.id]["stock"] = stock_map.get(order.local_sku_id, []) if order.local_sku_id else []
            details[order.id]["stock_loaded"] = True

    db.rollback()
    return {"details": [details[o.id] for o in orders], "fields": sorted(fields), "persisted": True, "external_api_calls": 0}


@router.get("/wb/orders/local-summary")
def wb_orders_local_summary(
    credential_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Small diagnostic view of the durable PostgreSQL order projection by WB warehouse."""
    cred = _credential(db, credential_id, "WB")
    _consolidate_credential_history(db, cred)
    logical_ids = _logical_credential_ids(db, cred)
    rows = db.execute(
        select(MarketplaceOrder.wb_warehouse_id, MarketplaceOrder.local_status, func.count(MarketplaceOrder.id))
        .where(MarketplaceOrder.credential_id.in_(logical_ids), MarketplaceOrder.provider == "WB")
        .group_by(MarketplaceOrder.wb_warehouse_id, MarketplaceOrder.local_status)
    ).all()
    settings = _wb_settings(cred, warehouse_id)
    result: dict[str, Any] = {}
    for wid, status, count in rows:
        key = str(wid) if wid is not None else "unknown"
        bucket = result.setdefault(key, {
            "warehouse_id": wid,
            "name": (settings.get(key) or {}).get("name"),
            "mode": (settings.get(key) or {}).get("mode", "HIDDEN"),
            "total": 0,
            "statuses": {name: 0 for name in LOCAL_STATUS_NAMES},
        })
        bucket["statuses"][status] = int(count or 0)
        bucket["total"] += int(count or 0)
    response = {"warehouses": list(result.values()), "total": sum(x["total"] for x in result.values())}
    db.rollback()
    return response


# FC_K9R54_VISIBLE_META_SINGLEFLIGHT: every open workstation refreshes its visible
# order page, but identical pages must not multiply the same WB meta/sticker call.
_VISIBLE_META_REFRESH_LOCK = threading.Lock()
_VISIBLE_META_REFRESH_LAST: dict[tuple[str, tuple[int, ...]], float] = {}
_VISIBLE_META_REFRESH_INTERVAL = 45.0

def _claim_visible_meta_refresh(credential_id: uuid.UUID, order_numbers: list[int]) -> bool:
    key = (str(credential_id), tuple(sorted({int(x) for x in order_numbers})))
    if not key[1]:
        return False
    now_mono = time.monotonic()
    with _VISIBLE_META_REFRESH_LOCK:
        if len(_VISIBLE_META_REFRESH_LAST) > 1000:
            cutoff = now_mono - 300.0
            for old_key, stamp in list(_VISIBLE_META_REFRESH_LAST.items()):
                if float(stamp or 0.0) < cutoff:
                    _VISIBLE_META_REFRESH_LAST.pop(old_key, None)
        last = float(_VISIBLE_META_REFRESH_LAST.get(key) or 0.0)
        if now_mono - last < _VISIBLE_META_REFRESH_INTERVAL:
            return False
        _VISIBLE_META_REFRESH_LAST[key] = now_mono
        return True


@router.post("/wb/orders/meta-refresh")
def wb_refresh_order_meta(payload: WBMetaRefreshRequest, db: Session = Depends(get_db)):
    # Read-only credential resolution here deliberately avoids historical consolidation.
    # The query already spans logical credential generations.
    cred = _credential(db, payload.credential_id, "WB")
    order_numbers = [int(x) for x in payload.order_ids if x is not None]
    if not _claim_visible_meta_refresh(cred.id, order_numbers):
        db.rollback()
        return {"ok": True, "checked": 0, "changed": 0, "deduplicated": True}
    client = shared_wb_client(decrypt_secret(cred.encrypted_api_key))
    logical_ids = _logical_credential_ids(db, cred)
    orders = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.external_order_num.in_(order_numbers),
    )).scalars().all()
    result = _refresh_wb_metadata_batch(db, client, orders, order_write_key=_wb_order_write_lock_key(cred))
    if result["checked"] or result["changed"]:
        db.commit()
    return {"ok": True, **result, "deduplicated": False}


@router.post("/wb/orders/reconcile")
def wb_reconcile_orders(
    credential_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=300, ge=1, le=500),
    db: Session = Depends(get_db),
):
    """Progressively reconcile marking/sticker facts for the whole active WB workspace."""
    cred = _credential(db, credential_id, "WB")
    visible_ids = [int(k) for k, v in _wb_settings(cred, warehouse_id).items() if str(v.get("mode") or "").upper() in {"WORK", "WATCH"}]
    if not visible_ids:
        return {"ok": True, "checked": 0, "changed": 0}
    # Several PCs run this progressive safety refresh. Only one needs to hit WB during
    # the same minute for the same physical scope.
    reconcile_key = f"wb-meta-progress:{cred.id}:{warehouse_id or 'legacy'}"
    try:
        if not claim_runtime_interval(db, reconcile_key, 60.0, force=False):
            db.rollback()
            return {"ok": True, "checked": 0, "changed": 0, "deduplicated": True}
    except Exception:
        try: db.rollback()
        except Exception: pass
    logical_ids = _logical_credential_ids(db, cred)
    base = select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.wb_warehouse_id.in_(visible_ids),
        MarketplaceOrder.local_status.notin_(["DELIVERED", "CANCELLED"]),
    )
    checked_expr = MarketplaceOrder.raw_data.op("->>")("_fc_meta_checked_at")
    # PostgreSQL sorts the lightweight candidate relation and hydrates only the requested
    # batch; the old code loaded every active order + raw_data JSON into Python first.
    batch = db.execute(base.order_by(
        func.coalesce(checked_expr, "").asc(),
        MarketplaceOrder.external_created_at.asc().nullsfirst(),
        MarketplaceOrder.created_at.asc(),
    ).limit(limit)).scalars().all()
    total_active = int(db.execute(select(func.count(MarketplaceOrder.id)).where(
        MarketplaceOrder.credential_id.in_(logical_ids),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.wb_warehouse_id.in_(visible_ids),
        MarketplaceOrder.local_status.notin_(["DELIVERED", "CANCELLED"]),
    )).scalar_one() or 0)
    client = shared_wb_client(decrypt_secret(cred.encrypted_api_key))
    result = _refresh_wb_metadata_batch(db, client, batch, order_write_key=_wb_order_write_lock_key(cred))
    if batch:
        db.commit()
    return {"ok": True, **result, "remaining": max(0, total_active-len(batch)), "deduplicated": False}


@router.get("/wb/orders/new")
def wb_new_orders(
    credential_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    # Backward-compatible endpoint used by v0.3 UI.
    cred, client = _wb(db, credential_id)
    result = []
    incoming = client.new_orders()
    with process_lock(_wb_order_write_lock_key(cred)):
        for raw in incoming:
            wid = raw.get("warehouseId") or raw.get("wb_warehouse_id")
            if not _wb_visible(cred, int(wid) if wid is not None else None, warehouse_id):
                continue
            order = _upsert_wb_order(db, cred, raw)
            if order:
                order.supplier_status = order.supplier_status or "new"
                order.local_status = _compute_local_status(order)
                result.append(_order_response(db, order, warehouse_id, cred))
        db.commit()
    return {"orders": result, "count": len(result)}


@router.post("/wb/packing/scan-product")
def wb_scan_product(
    payload: WBScanProductRequest,
    station_code: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Select an existing WB order from LOCAL PostgreSQL only.

    Inventory quantity is deliberately not an eligibility condition. If WB already gave
    the warehouse an order, the operator must be able to pack it even when accounting
    stock is zero or a sale reservation could not be funded. Marketplace I/O starts only
    in a daemon worker after this local request commits and returns.
    """
    cred = _credential(db, payload.credential_id, "WB")
    barcode = payload.barcode.strip()
    if not barcode:
        raise HTTPException(status_code=400, detail="Пустой штрихкод товара")

    packing_credential_ids = _packing_credential_ids_for_scan(db, cred)
    matching_sku_ids = db.execute(
        select(SKU.id).where(
            SKU.client_id == cred.client_id,
            or_(SKU.barcode == barcode, SKU.article == barcode, SKU.sku_code == barcode),
        )
    ).scalars().all()

    match_clause = MarketplaceOrder.barcodes.contains([barcode])
    if matching_sku_ids:
        match_clause = or_(MarketplaceOrder.local_sku_id.in_(matching_sku_ids), match_clause)
    candidates = db.execute(
        select(MarketplaceOrder).where(
            MarketplaceOrder.credential_id.in_(packing_credential_ids),
            MarketplaceOrder.provider == "WB",
            MarketplaceOrder.local_status.in_(["NEW", "PICKING"]),
            match_clause,
        ).order_by(
            MarketplaceOrder.external_created_at.asc().nullsfirst(),
            MarketplaceOrder.created_at.asc(),
        ).limit(300)
    ).scalars().all()
    candidates = [o for o in candidates if _wb_mode(cred, o.wb_warehouse_id, payload.warehouse_id) == "WORK"]

    # 0.9.4.14a status gate: the packing scanner NEVER promotes NEW orders.
    # An order becomes packable only after the operator/sheet explicitly puts it into
    # FulfillCore PICKING ("На сборке"). NEW matches are retained only to show a clear hint.
    picking_candidates = [o for o in candidates if str(o.local_status or "").upper() == "PICKING"]
    new_candidates = [o for o in candidates if str(o.local_status or "").upper() == "NEW"]

    def _pack_priority(o: MarketplaceOrder):
        raw = dict(o.raw_data or {})
        skipped_at = _parse_dt(raw.get("_fc_pack_skip_at"))
        stage = 0 if o.last_picking_list_id else 1
        created = o.external_created_at or o.created_at or _now()
        return (1 if skipped_at else 0, stage, created, skipped_at or created)
    picking_candidates.sort(key=_pack_priority)

    station = normalize_station_code(station_code) if station_code else None
    matched = None
    matching_but_busy = False
    for order in picking_candidates:
        if acquire_packing_lease(
            db,
            order_id=order.id,
            station_code=station,
            device_id=device_id,
            credential_id=cred.id,
            warehouse_id=payload.warehouse_id,
            barcode=barcode,
        ):
            matched = order
            break
        matching_but_busy = True

    if not matched:
        if matching_but_busy:
            raise HTTPException(
                status_code=409,
                detail="Подходящий заказ «На сборке» уже закреплён за другим упаковочным столом. Повторите сканирование после освобождения заказа.",
            )
        # If there is no packable PICKING order, tell the operator when the scanned SKU
        # physically waits in virtual ПРИЕМКА. This is an informational gate only: packing
        # rules/statuses are unchanged and NEW is never auto-promoted.
        virtual_row = None
        virtual_sku_ids = list(dict.fromkeys([*matching_sku_ids, *[o.local_sku_id for o in candidates if o.local_sku_id]]))
        if virtual_sku_ids and payload.warehouse_id:
            virtual_row = db.execute(
                select(InventoryBalance, SKU, Location)
                .join(SKU, SKU.id == InventoryBalance.sku_id)
                .join(Location, Location.id == InventoryBalance.location_id)
                .where(
                    InventoryBalance.client_id == cred.client_id,
                    InventoryBalance.warehouse_id == payload.warehouse_id,
                    InventoryBalance.sku_id.in_(virtual_sku_ids),
                    InventoryBalance.quantity > 0,
                    or_(Location.code == "ПРИЕМКА", Location.barcode == "ПРИЕМКА"),
                )
                .order_by(InventoryBalance.quantity.desc())
                .limit(1)
            ).first()
        if virtual_row:
            balance, virtual_sku, virtual_location = virtual_row
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ITEM_IN_VIRTUAL_RECEIVING",
                    "message": "Товар сейчас находится в виртуальной ячейке и ещё не готов к упаковке.",
                    "location": str(virtual_location.code or "ПРИЕМКА"),
                    "quantity": float(balance.quantity or 0),
                    "sku_id": str(virtual_sku.id),
                    "article": virtual_sku.article,
                    "size": virtual_sku.size,
                    "barcode": virtual_sku.barcode,
                    "new_order_id": str(new_candidates[0].external_order_id) if new_candidates else None,
                },
            )
        if new_candidates:
            first_new = new_candidates[0]
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Заказ WB №{first_new.external_order_id} найден в «Новых». "
                    "Сначала переведите его в «На сборке» вручную или через сборочный лист, затем сканируйте товар повторно."
                ),
            )
        raise HTTPException(
            status_code=404,
            detail="В локальной базе FulfillCore нет заказа «На сборке» для этого ШК. Дождитесь фоновой синхронизации или проверьте статус заказа.",
        )

    if matched.credential_id != cred.id:
        old_credential_id = matched.credential_id
        matched.credential_id = cred.id
        _event(db, matched, "PACKING_ORDER_ADOPTED_BY_ACTIVE_WB_KEY", data={
            "from_credential_id": str(old_credential_id), "to_credential_id": str(cred.id),
        })
    if not matched.local_sku_id and len(matching_sku_ids) == 1:
        matched.local_sku_id = matching_sku_ids[0]

    reservation = None
    reservation_error = None
    try:
        with db.begin_nested():
            reservation = ensure_packing_reservation_for_scan(
                db, matched, payload.warehouse_id, eligible_credential_ids=packing_credential_ids,
            )
    except Exception as exc:
        reservation_error = str(getattr(exc, "detail", None) or exc)
        logger.warning(
            "FC_PACK_LOCAL_SCAN_RESERVATION_NONBLOCKING order_id=%s barcode=%s error=%s",
            matched.id, barcode, reservation_error,
        )

    if reservation:
        _event(db, matched, "PICKING_RESERVATION_READY", value=str(reservation.get("id") or ""), data={
            "status": reservation.get("status"),
            "source_location_id": reservation.get("source_location_id"),
            "source_container_id": reservation.get("source_container_id"),
            "packing_stock_gate": False,
        })
    else:
        _event(db, matched, "PACKING_WITHOUT_STOCK_RESERVATION_ALLOWED", value=barcode, data={
            "reason": reservation_error or "order_exists_inventory_is_not_a_pack_gate",
            "recorded_stock_may_be_zero": True,
        })

    matched.product_scanned_barcode = barcode
    matched.product_scanned_at = _now()
    # Status was already PICKING before the scan. Manual PICKING and picking-list PICKING
    # are equally valid; the scanner never changes NEW -> PICKING itself.
    _event(db, matched, "PRODUCT_SCANNED_LOCAL_DB", value=barcode, data={
        "station_code": station, "device_id": device_id, "reservation_id": str(reservation.get("id")) if reservation else None,
        "picked_without_list": bool(not matched.last_picking_list_id),
        "inventory_gate": False,
    })
    order_id = matched.id
    order_number = _wb_order_number(matched)
    credential_for_background = matched.credential_id
    supply_id = str(matched.supply_id or "").strip() or None
    supply_name = _wb_chz_supply_name(matched)
    supply_cache_key = (str(matched.credential_id), str(matched.wb_warehouse_id or ""), str(matched.cargo_type or ""))
    db.commit()

    # No marketplace request belongs to the scanner response. Start WB preparation in a
    # daemon using a token copy only after the local transaction is safely committed.
    try:
        _prepare_wb_order_for_fast_pack_background(
            api_token=decrypt_secret(cred.encrypted_api_key),
            order_id=order_id,
            credential_id=credential_for_background,
            order_number=order_number,
            supply_id=supply_id,
            supply_name=supply_name,
            supply_cache_key=supply_cache_key,
        )
    except Exception:
        logger.exception("FC_FAST_PACK_PREP_START_FAIL order_id=%s", order_id)
    return {"ok": True, "order": _packing_scan_order_response(db, matched, payload.warehouse_id), "next": "SCAN_CHZ"}



@router.post("/wb/packing/skip-order")
def wb_skip_packing_order(
    credential_id: uuid.UUID = Query(...),
    order_id: uuid.UUID = Query(...),
    warehouse_id: uuid.UUID | None = Query(default=None),
    station_code: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Move the current packing order to the end of the local queue only.

    No WB status, supply, CHZ or inventory data is changed. The next scan of the
    same product barcode therefore selects the next eligible PICKING order.
    """
    cred = _credential(db, credential_id, "WB")
    order = _order_lookup(db, order_id, credential_id)
    _assert_wb_work(cred, order, warehouse_id)
    if order.local_status != "PICKING":
        raise HTTPException(status_code=409, detail="Пропустить можно только заказ со статусом «На сборке».")
    raw = dict(order.raw_data or {})
    raw["_fc_pack_skip_at"] = _now().isoformat()
    order.raw_data = raw
    order.product_scanned_barcode = None
    order.product_scanned_at = None
    _event(db, order, "PACKING_QUEUE_SKIPPED", value=str(order.external_order_id), data={"station_code": normalize_station_code(station_code) if station_code else None, "device_id": device_id})
    if station_code:
        release_packing_lease(db, order.id, station_code, device_id)
    db.commit()
    return {"ok": True, "order_id": str(order.id), "external_order_id": order.external_order_num or order.external_order_id}


@router.post("/wb/packing/release-order")
def wb_release_packing_order(
    credential_id: uuid.UUID = Query(...),
    order_id: uuid.UUID = Query(...),
    warehouse_id: uuid.UUID | None = Query(default=None),
    station_code: str = Query(...),
    device_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Release only the current station lease without changing the order/queue status."""
    cred = _credential(db, credential_id, "WB")
    order = _order_lookup(db, order_id, credential_id)
    _assert_wb_work(cred, order, warehouse_id)
    release_packing_lease(db, order.id, station_code, device_id)
    db.commit()
    return {"ok": True, "order_id": str(order.id), "station_code": normalize_station_code(station_code)}


def _wb_api_error_status(exc: HTTPException) -> int:
    """Recover the original WB HTTP status from our marketplace wrapper.

    WBClient intentionally exposes marketplace 4xx/5xx errors to the UI as HTTP 502
    while keeping the original WB code in ``detail`` (for example "ошибка API 409").
    CHZ preparation must branch on the WB code, not on the wrapper code.
    """
    if exc.status_code != 502:
        return int(exc.status_code)
    detail = str(exc.detail or "")
    for code in (409, 404, 403, 400, 429, 500, 502, 503):
        if f"API {code}" in detail or f"HTTP {code}" in detail:
            return code
    return int(exc.status_code)


def _wb_add_orders_to_supply(client: WBClient, supply_id: str, order_ids: list[int]) -> None:
    """Use the current public WB route for adding FBS orders to a supply.

    WB moved this operation from ``/api/v3/supplies/{id}/orders`` to
    ``/api/marketplace/v3/supplies/{id}/orders``. Older WBClient builds still
    point to the removed route and now receive a gateway-level 404 path not found.
    Keep the compatibility shim in the router so the cumulative patch does not
    replace the otherwise working marketplace client module.
    """
    sid = str(supply_id or "").strip()
    if not sid:
        raise HTTPException(status_code=400, detail="Не указан ID поставки WB.")
    normalized_ids = [int(order_id) for order_id in order_ids]
    if not normalized_ids:
        return
    patch = getattr(client, "_patch", None)
    if callable(patch):
        patch(f"/api/marketplace/v3/supplies/{sid}/orders", {"orders": normalized_ids})
        return
    # Compatibility with a non-standard WBClient implementation. Standard
    # FulfillCore builds expose _patch; the public method remains a last resort.
    client.add_orders_to_supply(sid, normalized_ids)


def _set_pending_wb_chz(order: MarketplaceOrder, chz: str, *, reason: str, supply_id: str | None = None) -> None:
    """Persist a warehouse-scanned CHZ when WB temporarily refuses the metadata write.

    This is deliberately a local fallback only. It lets the warehouse finish physical
    packing and print the already-existing WB order sticker, while clearly remembering
    that the SGTIN still has to be delivered to WB later. No marketplace status or
    supply membership is forged here.
    """
    raw = dict(order.raw_data or {})
    raw["_fc_chz_pending_wb"] = {
        "chz": chz,
        "reason": reason,
        "supply_id": str(supply_id or order.supply_id or "") or None,
        "saved_at": _now().isoformat(),
    }
    order.raw_data = raw


def _clear_pending_wb_chz(order: MarketplaceOrder) -> bool:
    raw = dict(order.raw_data or {})
    if "_fc_chz_pending_wb" not in raw:
        return False
    raw.pop("_fc_chz_pending_wb", None)
    raw["_fc_chz_last_wb_sync_at"] = _now().isoformat()
    order.raw_data = raw
    return True


def _has_pending_wb_chz(order: MarketplaceOrder) -> bool:
    raw = order.raw_data or {}
    return isinstance(raw, dict) and isinstance(raw.get("_fc_chz_pending_wb"), dict)


def _wb_meta_sgtin_values(item: dict | None) -> list[str]:
    """Return every SGTIN WB currently exposes for one assembly order."""
    if not item:
        return []
    detail = _meta_detail(item, "sgtin")
    if detail is not None:
        value = detail.get("value")
        if isinstance(value, list):
            return [str(x) for x in value if x not in (None, "")]
        if value not in (None, ""):
            return [str(value)]
    meta = item.get("meta") or {}
    if isinstance(meta, dict):
        entry = meta.get("sgtin")
        value = entry.get("value") if isinstance(entry, dict) else None
        if isinstance(value, list):
            return [str(x) for x in value if x not in (None, "")]
        if value not in (None, ""):
            return [str(value)]
    return []


def _wb_order_meta_item(client: WBClient, order: MarketplaceOrder) -> dict | None:
    order_number = _wb_order_number(order)
    rows = client.order_meta([order_number])
    wanted = str(order_number)
    return next((x for x in rows if str(x.get("id") or x.get("orderId") or x.get("order_id")) == wanted), None)


WB_SGTIN_DELIVERY_ALLOWED = {
    # WB API (2026): delivery validation passes when metaDetails.decision is
    # either `filled` or `optional`. Keep legacy success decisions for older
    # cached responses used by FulfillCore.
    "filled", "optional", "deadlineexceeded", "sgtinintroduced", "sgtinsoldb2b",
}

WB_SGTIN_DECISION_MESSAGES = {
    "required": "WB не видит закреплённый ЧЗ у сборочного задания",
    "pending": "WB принял ЧЗ и ещё проверяет его — дождитесь окончания проверки",
    "sgtininvalidformat": "WB отклонил ЧЗ: неверный формат DataMatrix",
    "sgtinnogs": "WB отклонил ЧЗ: в коде нет GS-разделителя (ASCII 29 / \\u001D). Обычно сканер в режиме клавиатуры теряет FNC1/GS",
    "sgtinhasinvalidsymbols": "WB отклонил ЧЗ: в коде есть недопустимые символы или пробелы",
    "sgtinhasnonlatinsymbols": "WB отклонил ЧЗ: в коде есть нелатинские символы",
    "sgtininvalidpattern": "WB отклонил ЧЗ: нарушена GS1-структура кода",
    "sgtinnotfound": "WB отклонил ЧЗ: код не найден в системе Честный знак",
    "sgtinemitted": "WB отклонил ЧЗ: код только эмитирован и ещё не введён в оборот",
    "sgtinapplied": "WB отклонил ЧЗ: не пройдён ввод в оборот",
    "sgtinwrittenoff": "WB отклонил ЧЗ: код списан",
    "sgtinwithdrawn": "WB отклонил ЧЗ: код выбыл из оборота",
    "sgtinretired": "WB отклонил ЧЗ: код выбыл из оборота",
    "sgtindisaggregated": "WB отклонил ЧЗ: код расформирован",
    "sgtindisaggregation": "WB отклонил ЧЗ: код расформирован",
    "sgtinappliednotpaid": "WB отклонил ЧЗ: код не оплачен",
}


def _normalize_sgtin_for_compare(value: str) -> str:
    try:
        return _normalize_chz(value)
    except HTTPException:
        return str(value or "")


def _wb_sgtin_state(item: dict | None, expected: str) -> dict[str, Any]:
    detail = _meta_detail(item, "sgtin")
    decision = ""
    if detail is not None:
        decision = str(detail.get("decision") or "").strip()
    values = _wb_meta_sgtin_values(item)
    wanted = _normalize_sgtin_for_compare(expected)
    attached = any(_normalize_sgtin_for_compare(v) == wanted for v in values)
    dkey = decision.lower()
    # Some older WB responses expose the value but no decision. Exact read-back is enough
    # in that legacy case. In current API, use the decision to distinguish accepted/pending/rejected.
    accepted = attached and (not dkey or dkey in WB_SGTIN_DELIVERY_ALLOWED)
    return {
        "attached": attached,
        "accepted": accepted,
        "decision": decision,
        "values_count": len(values),
    }


# 0.9.4.4 PACKING TRANSACTION FIX
# WB network I/O must never hold a PostgreSQL transaction or block the packing table.
# This helper uses only immutable primitives and the already-created WB client. It does
# not touch SQLAlchemy from the worker thread. Metadata reconciliation confirms the
# submission on a later bounded sync.

# 0.9.4.5 WB CHZ PRINT STABILITY
# Keep WB network I/O outside the packing DB transaction, but do not blindly call
# /meta/sgtin while the assembly order is not in supplierStatus=confirm.
# Official WB API allows SGTIN only in confirm. A 409 counts as 10 requests, so the
# previous blind META_RETRY loop could both slow WB and keep many long HTTP calls alive.
_CHZ_BG_COORD_LOCK = threading.Lock()
_CHZ_BG_INFLIGHT: set[int] = set()
_CHZ_BG_NEXT_AT: dict[int, float] = {}
_CHZ_BG_SLOTS = threading.BoundedSemaphore(8)


def _wb_supplier_status_without_db(client: WBClient, order_number: int) -> str:
    rows = client.order_statuses([int(order_number)])
    if isinstance(rows, dict):
        rows = rows.get("orders") or rows.get("data") or []
    rows = [x for x in (rows or []) if isinstance(x, dict)]
    wanted = str(int(order_number))
    selected = None
    for row in rows:
        raw_id = row.get("id") or row.get("orderId") or row.get("order_id")
        if raw_id is not None and str(raw_id).strip() == wanted:
            selected = row
            break
    if selected is None and len(rows) == 1:
        selected = rows[0]
    if not selected:
        return ""
    return str(selected.get("supplierStatus") or selected.get("supplier_status") or "").strip().lower()


def _submit_wb_chz_background(
    client: WBClient,
    order_number: int,
    chz: str,
    *,
    source: str = "PACKING",
    supply_id: str | None = None,
) -> bool:
    """0.9.4.7 DB-free SGTIN worker with a closed-supply direct attempt.

    PACKING is already committed before this function is called. No SQLAlchemy object is
    captured here. For supplierStatus=complete we restore the earlier proven behaviour:
    try the SGTIN write directly once instead of deferring forever. If WB refuses a closed
    order, keep the local CHZ pending and let reconciliation/operator flow decide later.
    """
    order_number = int(order_number)
    chz = str(chz or "")
    sid = str(supply_id or "").strip() or None
    now_mono = time.monotonic()
    with _CHZ_BG_COORD_LOCK:
        stale_keys = [k for k, until in _CHZ_BG_NEXT_AT.items() if float(until or 0.0) < now_mono - 600.0]
        for stale_key in stale_keys[:2000]:
            _CHZ_BG_NEXT_AT.pop(stale_key, None)
        if len(_CHZ_BG_NEXT_AT) > 5000:
            for stale_key, _until in sorted(_CHZ_BG_NEXT_AT.items(), key=lambda kv: float(kv[1] or 0.0))[:1000]:
                _CHZ_BG_NEXT_AT.pop(stale_key, None)
        if order_number in _CHZ_BG_INFLIGHT:
            logger.info("FC_CHZ_BG_SKIP source=%s order_num=%s reason=inflight", source, order_number)
            return False
        if now_mono < float(_CHZ_BG_NEXT_AT.get(order_number) or 0.0):
            logger.info("FC_CHZ_BG_SKIP source=%s order_num=%s reason=cooldown", source, order_number)
            return False
        _CHZ_BG_INFLIGHT.add(order_number)

    def _run() -> None:
        started = time.perf_counter()
        cooldown = 20.0
        outcome = "unknown"
        acquired = False
        try:
            acquired = _CHZ_BG_SLOTS.acquire(timeout=15.0)
            if not acquired:
                cooldown = 1.0
                outcome = "busy_retry_required"
                return

            supplier = ""
            try:
                supplier = _wb_supplier_status_without_db(client, order_number)
            except Exception as exc:
                logger.warning("FC_CHZ_BG_STATUS source=%s order_num=%s outcome=error error=%s", source, order_number, exc)

            if supplier == "new" and sid:
                try:
                    _wb_add_orders_to_supply(client, sid, [order_number])
                    logger.info("FC_CHZ_BG_CONFIRM source=%s order_num=%s supply_id=%s outcome=patched", source, order_number, sid)
                except Exception as exc:
                    logger.warning("FC_CHZ_BG_CONFIRM source=%s order_num=%s supply_id=%s outcome=error error=%s", source, order_number, sid, exc)
                for pause in (0.25, 0.50, 0.80):
                    time.sleep(pause)
                    try:
                        supplier = _wb_supplier_status_without_db(client, order_number)
                    except Exception:
                        supplier = ""
                    if supplier == "confirm":
                        break

            # 0.9.4.5 deferred COMPLETE forever. Earlier FulfillCore logic had a safe
            # direct SGTIN attempt first; restore only that non-destructive step here.
            if supplier == "complete":
                try:
                    client.set_sgtin(order_number, [chz])
                    outcome = "submitted_complete_direct"
                    cooldown = 60.0
                    logger.info(
                        "FC_CHZ_BG_SUBMIT source=%s order_num=%s outcome=complete_direct_ok elapsed_ms=%.1f",
                        source, order_number, (time.perf_counter()-started)*1000.0,
                    )
                    return
                except HTTPException as exc:
                    code = _wb_api_error_status(exc)
                    if code not in {400, 404, 409}:
                        raise
                    outcome = f"deferred_complete_{code}"
                    cooldown = 180.0
                    logger.warning(
                        "FC_CHZ_BG_DEFER source=%s order_num=%s supplier_status=complete direct_error=%s supply_id=%s",
                        source, order_number, code, sid,
                    )
                    return

            if supplier != "confirm":
                outcome = f"deferred_status_{supplier or 'unknown'}"
                cooldown = 45.0 if supplier in {"new", ""} else 180.0
                logger.warning("FC_CHZ_BG_DEFER source=%s order_num=%s supplier_status=%s supply_id=%s", source, order_number, supplier or "unknown", sid)
                return

            try:
                client.set_sgtin(order_number, [chz])
            except HTTPException as exc:
                code = _wb_api_error_status(exc)
                if code == 409:
                    time.sleep(0.55)
                    try:
                        supplier2 = _wb_supplier_status_without_db(client, order_number)
                    except Exception:
                        supplier2 = ""
                    if supplier2 == "confirm":
                        client.set_sgtin(order_number, [chz])
                    else:
                        outcome = f"deferred_after_409_{supplier2 or 'unknown'}"
                        cooldown = 60.0
                        return
                else:
                    raise

            outcome = "submitted"
            cooldown = 30.0
            logger.info("FC_CHZ_BG_SUBMIT source=%s order_num=%s outcome=ok elapsed_ms=%.1f", source, order_number, (time.perf_counter()-started)*1000.0)
        except Exception as exc:
            outcome = "error"
            cooldown = 30.0
            logger.warning("FC_CHZ_BG_SUBMIT source=%s order_num=%s outcome=error elapsed_ms=%.1f error=%s", source, order_number, (time.perf_counter()-started)*1000.0, exc)
        finally:
            if acquired:
                try:
                    _CHZ_BG_SLOTS.release()
                except Exception:
                    pass
            with _CHZ_BG_COORD_LOCK:
                _CHZ_BG_INFLIGHT.discard(order_number)
                _CHZ_BG_NEXT_AT[order_number] = time.monotonic() + cooldown
            logger.info("FC_CHZ_BG_DONE source=%s order_num=%s outcome=%s cooldown_s=%s", source, order_number, outcome, int(cooldown))

    threading.Thread(target=_run, name=f"fc-chz-{order_number}", daemon=True).start()
    return True




def _wb_verify_sgtin(client: WBClient, order: MarketplaceOrder, chz: str, *, attempts: int = 1) -> dict[str, Any]:
    """Read WB metadata back and return the actual SGTIN validation state.

    0.7.8.22 only compared strings, which hid the most useful WB response: metaDetails.decision.
    Current WB API reports exact marking errors there (including sgtinNoGS). The scanner
    performs one immediate read-back only: WB's asynchronous projection is completed by
    the existing background reconciliation and must not hold up the label printer.
    """
    last: dict[str, Any] = {"attached": False, "accepted": False, "decision": "", "values_count": 0}
    for attempt in range(max(1, attempts)):
        try:
            item = _wb_order_meta_item(client, order)
            last = _wb_sgtin_state(item, chz)
            if last["accepted"]:
                return last
            dkey = str(last.get("decision") or "").lower()
            # Hard validation failures will not heal by polling the same payload.
            if dkey in WB_SGTIN_DECISION_MESSAGES and dkey not in {"pending", "required"}:
                return last
        except HTTPException:
            pass
        if attempt < attempts - 1:
            time.sleep(0.20)
    return last


def _wb_sgtin_failure_detail(state: dict[str, Any], chz: str) -> str:
    decision = str(state.get("decision") or "").strip()
    dkey = decision.lower()
    diag = _chz_diagnostics(chz)
    if dkey in WB_SGTIN_DECISION_MESSAGES:
        base = WB_SGTIN_DECISION_MESSAGES[dkey]
    elif state.get("attached"):
        base = f"WB получил ЧЗ, но проверка пока не разрешает отгрузку (decision={decision or 'не указан'})"
    else:
        base = f"WB ответил на запись ЧЗ, но не вернул этот код в метаданных (decision={decision or 'не указан'})"
    return f"{base}. Диагностика скана: длина={diag['length']}, GS={diag['gs_count']}, AI01={diag['starts_01']}, AI21={diag['has_ai21']}, AI91={diag['has_ai91']}, AI92={diag['has_ai92']}."


def _wb_sgtin_is_waiting(state: dict[str, Any]) -> bool:
    """True when WB accepted the write request but has not produced a final decision yet."""
    dkey = str(state.get("decision") or "").strip().lower()
    # `optional` is explicitly delivery-valid in current WB API. When the exact
    # value has not appeared in the read model yet, treat it as asynchronous
    # projection rather than a rejection.
    return dkey in {"", "pending", "required", "optional"}


def _refresh_live_supplier_status(db: Session, client: WBClient, order: MarketplaceOrder) -> str:
    try:
        rows = client.order_statuses([_wb_order_number(order)])
        status = _wb_status_row_for_order(rows, order)
        if status is not None:
            _apply_status(db, order, status)
            db.flush()
    except HTTPException:
        pass
    return str(order.supplier_status or "").strip().lower()


def _bind_chz_to_wb_verified(db: Session, client: WBClient, order: MarketplaceOrder, chz: str, *, verify_after_submit: bool = True, diagnostics: dict[str, Any] | None = None) -> None:
    """Send SGTIN wherever WB permits it and never forge a supply/status change.

    For an active order, use the normal confirm -> SGTIN -> read-back flow. For a closed
    supply, first try the harmless metadata write against the order as-is. If WB refuses
    it, move the order only when WB explicitly lists it for re-shipment. Otherwise keep
    the closed supply link and the current FulfillCore status untouched.
    """
    supplier = str(order.supplier_status or "").strip().lower()
    # Most packing orders are already confirm/complete in the last WB sync. Avoid an
    # extra network round trip on every scan; refresh only when local status is not
    # sufficient to choose the safe WB path.
    if supplier not in {"confirm", "complete"}:
        _t = time.perf_counter()
        supplier = _refresh_live_supplier_status(db, client, order)
        _timing_add_ms(diagnostics, "wb_status_ms", _t)

    if supplier == "complete":
        # It may already have been accepted by another client or by an earlier delayed
        # request. Exact WB read-back is authoritative and avoids needless supply work.
        try:
            _t = time.perf_counter()
            existing = _wb_sgtin_state(_wb_order_meta_item(client, order), chz)
            _timing_add_ms(diagnostics, "wb_meta_ms", _t)
        except HTTPException:
            existing = {"accepted": False, "decision": ""}
        if existing.get("accepted"):
            _clear_pending_wb_chz(order)
            _event(db, order, "CHZ_ALREADY_VERIFIED_IN_WB", value=chz, data={"supply_id": order.supply_id, "supplier_status": "complete", "decision": existing.get("decision")})
            db.flush()
            return

        # Maximise successful bindings without changing the closed supply: some WB
        # projections can still accept the metadata call. We trust only verified read-back.
        try:
            _event(db, order, "CHZ_WB_CLOSED_SUPPLY_DIRECT_ATTEMPT", value=chz, data={"supply_id": order.supply_id})
            _t = time.perf_counter()
            try:
                client.set_sgtin(_wb_order_number(order), [chz])
            except HTTPException:
                _chz_trace_stage(diagnostics, "wb_set_sgtin_ms", _t, order=order, outcome="http_error")
                raise
            else:
                _chz_trace_stage(diagnostics, "wb_set_sgtin_ms", _t, order=order, outcome="ok")
        except HTTPException as exc:
            wb_error_status = _wb_api_error_status(exc)
            _event(
                db,
                order,
                "CHZ_WB_CLOSED_SUPPLY_DIRECT_REJECTED",
                value=chz,
                data={"supply_id": order.supply_id, "wb_error_status": wb_error_status},
            )
            if wb_error_status not in {400, 404, 409}:
                raise
        else:
            if not verify_after_submit:
                # The WB write itself succeeded. Metadata read-back can lag or hang for
                # tens of seconds and must not block the packing table. Verify later.
                raise HTTPException(status_code=202, detail="ЧЗ отправлен в WB и ожидает фоновой проверки.")
            _t = time.perf_counter()
            direct_state = _wb_verify_sgtin(client, order, chz)
            _timing_add_ms(diagnostics, "wb_meta_ms", _t)
            if direct_state.get("accepted"):
                _clear_pending_wb_chz(order)
                _event(db, order, "CHZ_VERIFIED_IN_WB_CLOSED_SUPPLY", value=chz, data={"supply_id": order.supply_id, "supplier_status": "complete", "decision": direct_state.get("decision")})
                db.flush()
                return
            dkey = str(direct_state.get("decision") or "").strip().lower()
            if dkey and dkey not in {"pending", "required", "optional"}:
                raise HTTPException(status_code=502, detail=_wb_sgtin_failure_detail(direct_state, chz))
            raise HTTPException(status_code=202, detail="ЧЗ отправлен в WB и ожидает фоновой проверки.")

        try:
            _t = time.perf_counter()
            reshipment_ids = _wb_reshipment_order_ids(client)
            _timing_add_ms(diagnostics, "wb_reshipment_ms", _t)
        except HTTPException as exc:
            raise HTTPException(
                status_code=208,
                detail=f"Заказ находится в закрытой поставке. WB не принял ЧЗ напрямую, а проверить повторную отгрузку сейчас не удалось: {exc.detail}",
            )
        order_number = _wb_order_number(order)
        if order_number not in reshipment_ids:
            raise HTTPException(
                status_code=208,
                detail=(
                    "Заказ находится в закрытой поставке: WB не разрешил присвоить ЧЗ и не включил заказ в повторную отгрузку. "
                    "ЧЗ сохранён в FulfillCore для последующей фоновой отправки."
                ),
            )

        _event(db, order, "CHZ_WB_RESHIPMENT_ALLOWED", value=chz, data={"previous_supply_id": order.supply_id})

    if supplier != "confirm":
        _t = time.perf_counter()
        _ensure_wb_confirm_for_chz(db, client, order)
        _timing_add_ms(diagnostics, "wb_confirm_ms", _t)
        supplier = str(order.supplier_status or "").strip().lower()
    if supplier != "confirm":
        raise HTTPException(status_code=409, detail=f"WB не перевёл заказ в confirm (supplierStatus={supplier or 'неизвестен'}).")

    # WB expects the real ASCII GS character in the Python string. httpx/json then serializes
    # it as \u001d in the JSON body, exactly as the WB API requires. Do not pre-escape it twice.
    diag = _chz_diagnostics(chz)
    _event(db, order, "CHZ_WB_SEND_ATTEMPT", data={**diag, "supply_id": order.supply_id, "supplier_status": "confirm"})
    try:
        _t = time.perf_counter()
        try:
            client.set_sgtin(_wb_order_number(order), [chz])
        except HTTPException:
            _chz_trace_stage(diagnostics, "wb_set_sgtin_ms", _t, order=order, outcome="http_error")
            raise
        else:
            _chz_trace_stage(diagnostics, "wb_set_sgtin_ms", _t, order=order, outcome="ok")
    except HTTPException as exc:
        if _wb_api_error_status(exc) != 404:
            raise
        _t = time.perf_counter()
        live_supplier = _refresh_live_supplier_status(db, client, order)
        _timing_add_ms(diagnostics, "wb_status_ms", _t)
        _event(
            db,
            order,
            "CHZ_WB_ORDER_NOT_FOUND_DURING_SEND",
            value=chz,
            data={"supplier_status": live_supplier, "supply_id": order.supply_id},
        )
        if live_supplier == "complete":
            raise HTTPException(
                status_code=208,
                detail=(
                    "WB закрыл поставку во время передачи ЧЗ. Код сохранён в FulfillCore, "
                    "заказ можно упаковать и напечатать; фоновая синхронизация продолжится."
                ),
            )
        raise HTTPException(
            status_code=502,
            detail=(
                f"WB не нашёл сборочное задание orderId={_wb_order_number(order)} при передаче ЧЗ. "
                "Обновите заказы и проверьте, что выбран правильный кабинет WB. "
                f"Исходная ошибка: {exc.detail}"
            ),
        )
    if not verify_after_submit:
        # set_sgtin returned 2xx. Treat that as a successful submission and let the
        # existing background reconciliation perform the authoritative read-back.
        # This removes the slow metadata GET from the scanner's critical path.
        raise HTTPException(status_code=202, detail="ЧЗ отправлен в WB и ожидает фоновой проверки.")

    _t = time.perf_counter()
    state = _wb_verify_sgtin(client, order, chz)
    _timing_add_ms(diagnostics, "wb_meta_ms", _t)
    raw = dict(order.raw_data or {})
    raw["_fc_chz_last_wb_decision"] = str(state.get("decision") or "") or None
    raw["_fc_chz_last_diag"] = {**diag, "attached": bool(state.get("attached")), "accepted": bool(state.get("accepted")), "checked_at": _now().isoformat()}
    order.raw_data = raw
    if not state.get("accepted"):
        if _wb_sgtin_is_waiting(state):
            raise HTTPException(status_code=202, detail="ЧЗ отправлен в WB и ожидает фоновой проверки.")
        raise HTTPException(status_code=502, detail=_wb_sgtin_failure_detail(state, chz))

    _clear_pending_wb_chz(order)
    _event(db, order, "CHZ_VERIFIED_IN_WB", value=chz, data={"supply_id": order.supply_id, "supplier_status": "confirm", "decision": state.get("decision"), **diag})
    db.flush()


def _retry_pending_wb_chz(db: Session, client: WBClient, order: MarketplaceOrder) -> bool:
    """Retry pending CHZ unless the strict packing gate currently owns the order."""
    if not order.chz_code or not _has_pending_wb_chz(order):
        return False
    raw = dict(order.raw_data or {})
    gate = raw.get(_PACK_GATE_KEY) if isinstance(raw.get(_PACK_GATE_KEY), dict) else {}
    if gate and str(gate.get("chz") or "") == str(order.chz_code or "") and str(gate.get("stage") or "") != "DONE":
        # 0.9.4.8: the dedicated DB-free worker owns WB verification + label printing.
        # Generic metadata reconciliation must not race it or promote PACKED early.
        return False
    pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
    last = _parse_dt(pending.get("last_attempt_at")) if pending else None
    retry_after_seconds = 300 if str(pending.get("reason") or "") == "LOCAL_PACKED_CLOSED_SUPPLY" else 60
    if last is not None:
        try:
            if (_now() - last).total_seconds() < retry_after_seconds:
                return False
        except Exception:
            pass
    if isinstance(pending, dict):
        pending["last_attempt_at"] = _now().isoformat()
        raw["_fc_chz_pending_wb"] = pending
        order.raw_data = raw
    try:
        _bind_chz_to_wb_verified(db, client, order, order.chz_code)
    except HTTPException as exc:
        raw = dict(order.raw_data or {})
        pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
        if isinstance(pending, dict):
            pending["last_error"] = str(exc.detail or "")
            pending["last_attempt_at"] = _now().isoformat()
            raw["_fc_chz_pending_wb"] = pending
            order.raw_data = raw
        return False
    if order.product_scanned_at and not _workflow_sent_is_sticky(order):
        _consume_picking_reservation_nonblocking(db, order, "WB_PACKED_BACKGROUND_CHZ_RETRY")
        now = _now()
        order.assembled_at = order.assembled_at or now
        order.packed_at = order.packed_at or now
        order.local_status = "PACKED"
        _event(db, order, "PACKED_AFTER_CHZ_WB_RETRY", value=order.chz_code)
    elif order.product_scanned_at:
        _event(db, order, "LATE_CHZ_STATUS_PRESERVED", value=order.local_status, data={"source":"WB_PACKED_BACKGROUND_CHZ_RETRY"})
    return True



def _wb_reshipment_order_ids(client: WBClient) -> set[int]:
    """Return WB assembly-order IDs currently eligible for re-shipment.

    WB only permits moving an order from a closed supply to an active one when the
    marketplace itself marks that order for re-shipment. Keep this as a direct,
    one-shot check on the CHZ error path so normal scanner latency is unchanged.
    """
    data = client._get("/api/v3/supplies/orders/reshipment").json()
    result: set[int] = set()
    for item in (data.get("orders") or []):
        raw = item.get("orderID") if isinstance(item, dict) else item
        if isinstance(item, dict) and raw is None:
            raw = item.get("orderId") or item.get("id")
        try:
            result.add(int(raw))
        except (TypeError, ValueError):
            continue
    return result


def _wb_chz_supply_name(order: MarketplaceOrder) -> str:
    warehouse = str(order.wb_warehouse_id or "warehouse").strip()
    cargo = str(order.cargo_type or "default").strip().upper()
    return f"FulfillCore CHZ {warehouse} {cargo}"[:120]


def _find_reusable_wb_chz_supply(client: WBClient, preferred_name: str) -> tuple[str | None, bool]:
    """Recover the technical CHZ supply after an API restart instead of creating another."""
    try:
        active = [x for x in client.supplies() if not bool(x.get("done")) and x.get("id")]
    except HTTPException:
        return None, False
    exact = [x for x in active if str(x.get("name") or "").strip() == preferred_name]
    legacy = [x for x in active if str(x.get("name") or "").strip().startswith("FulfillCore CHZ")]
    candidates = exact or legacy
    if not candidates:
        return None, False
    # WB normally returns newest supplies first. Exact deterministic names always win;
    # the legacy fallback lets the first run reuse one of the supplies already on screen.
    return str(candidates[0].get("id") or "").strip() or None, bool(exact)


def _recover_missing_wb_chz_supply(
    db: Session,
    client: WBClient,
    key: tuple[str, str, str],
    preferred_name: str,
    failed_supply_id: str,
) -> str | None:
    """Replace a cached technical supply only when WB no longer lists it as active."""
    failed_id = str(failed_supply_id or "").strip()
    with distributed_lock(db, f"wb-chz-supply:{'|'.join(key)}", blocking=True):
        cached_id = str(_WB_CHZ_SUPPLY_CACHE.get(key) or "").strip()
        if cached_id and cached_id != failed_id:
            return cached_id
        try:
            active = [x for x in client.supplies() if not bool(x.get("done")) and x.get("id")]
        except HTTPException:
            return None
        active_ids = {str(x.get("id") or "").strip() for x in active}
        # A 404 while the supply is still active is not a stale-cache case. It can
        # indicate an order/cabinet mismatch, so do not create duplicate supplies.
        if failed_id and failed_id in active_ids:
            return None
        exact = [
            x
            for x in active
            if str(x.get("name") or "").strip() == preferred_name
            and str(x.get("id") or "").strip() != failed_id
        ]
        if exact:
            replacement_id = str(exact[0].get("id") or "").strip()
        else:
            data = client.create_supply(preferred_name)
            replacement_id = str(data.get("id") or "").strip()
        if not replacement_id:
            return None
        _WB_CHZ_SUPPLY_CACHE[key] = replacement_id
        return replacement_id


def _ensure_wb_confirm_for_chz(db: Session, client: WBClient, order: MarketplaceOrder) -> None:
    """Prepare a WB FBS order for SGTIN without touching FulfillCore's local PICKING state.

    WB accepts SGTIN only in supplierStatus=confirm. Adding an order to an active
    supply is the official operation that moves it to confirm. For a previously closed
    supply we do not second-guess WB with a stale local pre-check: the add-to-supply
    response itself is authoritative and may accept or reject the move.
    """
    if str(order.supplier_status or "").strip().lower() == "confirm":
        return

    previous_supply_id = str(order.supply_id or "").strip() or None

    # A WB supply is restricted by cargo type. Do not reuse one technical supply
    # across SML/LRG orders, otherwise a perfectly valid CHZ scan can fail with 409.
    key = (str(order.credential_id), str(order.wb_warehouse_id or ""), str(order.cargo_type or ""))
    supply_name = _wb_chz_supply_name(order)
    created_now = False
    recovered_exact = False
    with distributed_lock(db, f"wb-chz-supply:{'|'.join(key)}", blocking=True):
        supply_id = _WB_CHZ_SUPPLY_CACHE.get(key)
        if not supply_id:
            supply_id, recovered_exact = _find_reusable_wb_chz_supply(client, supply_name)
        if not supply_id:
            data = client.create_supply(supply_name)
            supply_id = str(data.get("id") or "").strip()
            if not supply_id:
                raise HTTPException(status_code=502, detail="WB не вернул ID единой служебной поставки для ЧЗ.")
            _WB_CHZ_SUPPLY_CACHE[key] = supply_id
            created_now = True
        else:
            _WB_CHZ_SUPPLY_CACHE[key] = supply_id

    try:
        _wb_add_orders_to_supply(client, supply_id, [_wb_order_number(order)])
    except HTTPException as exc:
        # WBClient exposes marketplace errors as HTTP 502 and keeps the original
        # code in detail. Handle a disappeared cached supply separately from an
        # order-specific conflict, and never multiply a supply that WB still lists.
        wb_error_status = _wb_api_error_status(exc)
        if wb_error_status == 404 and not created_now:
            failed_supply_id = supply_id
            replacement_id = _recover_missing_wb_chz_supply(
                db, client, key, supply_name, failed_supply_id
            )
            if not replacement_id or replacement_id == failed_supply_id:
                raise
            supply_id = replacement_id
            _wb_add_orders_to_supply(client, supply_id, [_wb_order_number(order)])
            _event(
                db,
                order,
                "WB_CHZ_STALE_SUPPLY_RECOVERED",
                data={"old_supply_id": failed_supply_id, "new_supply_id": supply_id},
            )
        elif wb_error_status != 409 or created_now:
            raise
        else:
            with distributed_lock(db, f"wb-chz-supply:{'|'.join(key)}", blocking=True):
                recovered_id, recovered_exact = _find_reusable_wb_chz_supply(client, supply_name)
                # Never multiply a deterministic active supply because of an order-specific
                # 409. A different recovered supply is worth one retry; a legacy timestamp
                # supply may be replaced once when its cargo type is incompatible.
                if recovered_exact and recovered_id == supply_id:
                    raise
                if recovered_id and recovered_id != supply_id:
                    _WB_CHZ_SUPPLY_CACHE[key] = recovered_id
                    supply_id = recovered_id
                    try:
                        _wb_add_orders_to_supply(client, supply_id, [_wb_order_number(order)])
                        recovered_id = None
                    except HTTPException as recovered_exc:
                        if _wb_api_error_status(recovered_exc) != 409 or recovered_exact:
                            raise
                    else:
                        recovered_exact = True
                if recovered_exact:
                    # A different active supply accepted the order; no creation is needed.
                    pass
                else:
                    data = client.create_supply(supply_name)
                    fresh_id = str(data.get("id") or "").strip()
                    if not fresh_id:
                        raise HTTPException(status_code=502, detail="WB не вернул ID новой поставки для ЧЗ.")
                    _WB_CHZ_SUPPLY_CACHE[key] = fresh_id
                    supply_id = fresh_id
            if not recovered_exact:
                _wb_add_orders_to_supply(client, supply_id, [_wb_order_number(order)])

    # Change the local supply link only after WB has accepted the move. This prevents
    # an ordinary closed-supply 409 from erasing the real supply in FulfillCore.
    if previous_supply_id and previous_supply_id != supply_id:
        raw = dict(order.raw_data or {})
        raw["_fc_previous_supply_id"] = previous_supply_id
        raw["_fc_previous_supply_moved_at"] = _now().isoformat()
        order.raw_data = raw
    order.supply_id = supply_id

    # Do not assume the move succeeded semantically just because PATCH returned 204.
    # Re-read WB status with a short settle window. PATCH may return before the
    # status projection is visible in /orders/status, especially during busy FBS waves.
    for attempt in range(6):
        try:
            status_rows = client.order_statuses([_wb_order_number(order)])
            live_status = _wb_status_row_for_order(status_rows, order)
            if live_status is not None:
                _apply_status(db, order, live_status)
                db.flush()
        except HTTPException:
            pass
        if str(order.supplier_status or "").strip().lower() == "confirm":
            break
        if attempt < 5:
            time.sleep(0.18)
    if str(order.supplier_status or "").strip().lower() != "confirm":
        raise HTTPException(
            status_code=409,
            detail=f"WB принял запрос на перенос, но заказ не перешёл в confirm (supplierStatus={order.supplier_status or 'неизвестен'}).",
        )
    _event(db, order, "WB_CONFIRM_BEFORE_CHZ", value=supply_id)
    db.flush()


# 0.9.4.8 STRICT CHZ PRINT GATE
# 0.9.4.8 REV2 SEQUENTIAL CONFIRMED PRINT
# 0.9.4.8a ROUTE RESTORE HOTFIX
# 0.9.4.8b FIFO PACKING EMERGENCY HOTFIX
# Warehouse packing is sequential again: CHZ -> WB verification -> WB sticker ->
# Print Agent ACK -> PACKED. Slow WB I/O is still DB-free, so the older blocking
# workflow is restored without reintroducing idle-in-transaction freezes.
_PACK_GATE_KEY = "_fc_pack_gate_v1"
_PACK_GATE_BG_LOCK = threading.Lock()
_PACK_GATE_BG_INFLIGHT: set[tuple[str, str]] = set()
_PACK_GATE_BG_SLOTS = threading.BoundedSemaphore(8)
_PACK_GATE_SUPPLY_LOCK = threading.Lock()


def _pack_gate_marker(order: MarketplaceOrder) -> dict[str, Any]:
    raw = order.raw_data or {}
    marker = raw.get(_PACK_GATE_KEY) if isinstance(raw, dict) else None
    return dict(marker) if isinstance(marker, dict) else {}


def _set_pack_gate_marker(order: MarketplaceOrder, **values: Any) -> dict[str, Any]:
    raw = dict(order.raw_data or {})
    marker = raw.get(_PACK_GATE_KEY) if isinstance(raw.get(_PACK_GATE_KEY), dict) else {}
    marker = dict(marker)
    marker.update(values)
    marker["updated_at"] = _now().isoformat()
    raw[_PACK_GATE_KEY] = marker
    order.raw_data = raw
    return marker


def _pack_gate_db_update(
    order_id: uuid.UUID,
    credential_id: uuid.UUID,
    expected_chz: str,
    *,
    stage: str,
    message: str,
    error: str | None = None,
    wb_decision: str | None = None,
    print_job_id: str | None = None,
    supply_id: str | None = None,
    wb_verified: bool | None = None,
) -> bool:
    gen = get_db()
    db2 = next(gen)
    try:
        order = _order_lookup(db2, order_id, credential_id)
        if str(order.chz_code or "") != str(expected_chz or ""):
            db2.rollback()
            return False
        if supply_id:
            order.supply_id = str(supply_id)
        raw = dict(order.raw_data or {})
        marker = raw.get(_PACK_GATE_KEY) if isinstance(raw.get(_PACK_GATE_KEY), dict) else {}
        marker = dict(marker)
        marker.update({"stage": stage, "message": message, "updated_at": _now().isoformat()})
        if error is not None:
            marker["error"] = error
        elif stage != "ERROR":
            marker.pop("error", None)
        if wb_decision is not None:
            marker["wb_decision"] = wb_decision
        if print_job_id is not None:
            marker["print_job_id"] = print_job_id
        if wb_verified is not None:
            marker["wb_chz_verified"] = bool(wb_verified)
        raw[_PACK_GATE_KEY] = marker

        pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
        pending = dict(pending)
        pending["chz"] = expected_chz
        pending["last_attempt_at"] = _now().isoformat()
        if stage in {"CHZ_VERIFIED", "WAIT_LABEL", "PRINT_QUEUED"} and wb_verified is True:
            pending["reason"] = "WB_VERIFIED_WAITING_PRINT"
            pending["wb_verified"] = True
            pending.pop("last_error", None)
        elif stage in {"WAIT_LABEL", "PRINT_QUEUED"}:
            pending["reason"] = "WB_SUBMITTED_WAITING_VERIFY"
            pending["wb_verified"] = False
        elif stage == "ERROR":
            pending["reason"] = "STRICT_PACK_GATE_ERROR"
            pending["last_error"] = error or message
        raw["_fc_chz_pending_wb"] = pending
        order.raw_data = raw
        db2.commit()
        return True
    except Exception:
        try:
            db2.rollback()
        except Exception:
            pass
        logger.exception("FC_PACK_GATE_DB_UPDATE_FAIL order_id=%s stage=%s", order_id, stage)
        return False
    finally:
        try:
            gen.close()
        except Exception:
            pass


def _wb_sgtin_state_without_db(client: WBClient, order_number: int, expected_chz: str) -> dict[str, Any]:
    rows = client.order_meta([int(order_number)])
    wanted = str(int(order_number))
    item = next(
        (x for x in (rows or []) if str(x.get("id") or x.get("orderId") or x.get("order_id") or "") == wanted),
        None,
    )
    if item is None and len(rows or []) == 1:
        item = (rows or [None])[0]
    return _wb_sgtin_state(item, expected_chz)


# 0.9.4.12 FAST PRINT PREP
# Mature FBS workflows move the selected assembly order into the marketplace's
# assembly-ready state before the marking scan, so the transport sticker can be
# downloaded while the operator is physically scanning the DataMatrix. WB exposes
# stickers only in supplierStatus=confirm/complete, therefore the historical
# "prefetch sticker while still NEW" could not actually warm the label.
_FAST_PACK_PREP_LOCK = threading.Lock()
_FAST_PACK_PREP_INFLIGHT: set[tuple[str, int]] = set()
_FAST_PACK_PREP_CACHE: dict[tuple[str, int], dict[str, Any]] = {}
_FAST_PACK_PREP_SLOTS = threading.BoundedSemaphore(6)
_FAST_PACK_PREP_MAX = 512


def _prune_fast_pack_prep_cache(now_mono: float | None = None) -> None:
    now_mono = time.monotonic() if now_mono is None else now_mono
    stale = [k for k, row in _FAST_PACK_PREP_CACHE.items() if now_mono - float((row or {}).get("at") or 0.0) >= 180.0]
    for key in stale:
        _FAST_PACK_PREP_CACHE.pop(key, None)
    if len(_FAST_PACK_PREP_CACHE) > _FAST_PACK_PREP_MAX:
        victims = sorted(_FAST_PACK_PREP_CACHE, key=lambda k: float((_FAST_PACK_PREP_CACHE.get(k) or {}).get("at") or 0.0))[:-_FAST_PACK_PREP_MAX]
        for key in victims:
            _FAST_PACK_PREP_CACHE.pop(key, None)


def _fast_pack_prep_state(order_number: int, *, credential_id=None) -> dict[str, Any]:
    if credential_id is None:
        return {}
    number = (str(credential_id), int(order_number))
    with _FAST_PACK_PREP_LOCK:
        _prune_fast_pack_prep_cache()
        row = dict(_FAST_PACK_PREP_CACHE.get(number) or {})
        if not row:
            return {}
        if time.monotonic() - float(row.get("at") or 0.0) >= 180.0:
            _FAST_PACK_PREP_CACHE.pop(number, None)
            return {}
        return row


def _cache_fast_pack_prep(order_number: int, *, credential_id=None, supplier: str, supply_id: str | None) -> None:
    if credential_id is None:
        return
    with _FAST_PACK_PREP_LOCK:
        _prune_fast_pack_prep_cache()
        _FAST_PACK_PREP_CACHE[(str(credential_id), int(order_number))] = {
            "supplier": str(supplier or "").strip().lower(),
            "supply_id": str(supply_id or "").strip() or None,
            "at": time.monotonic(),
        }
        _prune_fast_pack_prep_cache()


def _ensure_wb_confirm_without_db(
    client: WBClient,
    *,
    order_number: int,
    supply_id: str | None,
    supply_name: str,
    supply_cache_key: tuple[str, str, str],
) -> tuple[str, str | None]:
    """Return live supplier status + accepted supply id without holding PostgreSQL.

    This is safe to run both from product-scan preparation and the CHZ worker. The
    process-level supply lock prevents two local packing threads from multiplying the
    deterministic technical supply.
    """
    number = int(order_number)
    prep = _fast_pack_prep_state(number, credential_id=supply_cache_key[0])
    cached_supplier = str(prep.get("supplier") or "").strip().lower()
    cached_sid = str(prep.get("supply_id") or "").strip() or None
    if cached_supplier in {"confirm", "complete"}:
        return cached_supplier, cached_sid or (str(supply_id or "").strip() or None)

    supplier = _wb_supplier_status_without_db(client, number)
    sid = str(supply_id or cached_sid or "").strip() or None
    if supplier in {"confirm", "complete"}:
        _cache_fast_pack_prep(number, credential_id=supply_cache_key[0], supplier=supplier, supply_id=sid)
        return supplier, sid
    if supplier != "new":
        return supplier, sid

    with _PACK_GATE_SUPPLY_LOCK:
        sid = sid or (str(_WB_CHZ_SUPPLY_CACHE.get(supply_cache_key) or "").strip() or None)
        if not sid:
            sid, _exact = _find_reusable_wb_chz_supply(client, supply_name)
        if not sid:
            data = client.create_supply(supply_name)
            sid = str(data.get("id") or "").strip() or None
        if sid:
            _WB_CHZ_SUPPLY_CACHE[supply_cache_key] = sid
    if not sid:
        raise HTTPException(status_code=502, detail="WB не вернул активную поставку для перевода заказа в confirm")

    try:
        _wb_add_orders_to_supply(client, sid, [number])
    except HTTPException as exc:
        code = _wb_api_error_status(exc)
        if code not in {404, 409}:
            raise
        # One bounded recovery. Never loop on 409 because WB counts it as an expensive request.
        recovered, _exact = _find_reusable_wb_chz_supply(client, supply_name)
        recovered = str(recovered or "").strip() or None
        if not recovered or recovered == sid:
            data = client.create_supply(supply_name)
            recovered = str(data.get("id") or "").strip() or None
        if not recovered or recovered == sid:
            raise
        sid = recovered
        with _PACK_GATE_SUPPLY_LOCK:
            _WB_CHZ_SUPPLY_CACHE[supply_cache_key] = sid
        _wb_add_orders_to_supply(client, sid, [number])

    # WB normally exposes confirm quickly. Poll only the status projection; the operator
    # is still scanning the physical mark while this runs in the background.
    for pause in (0.03, 0.05, 0.08, 0.12, 0.18, 0.26, 0.38, 0.55, 0.80, 1.10):
        time.sleep(pause)
        try:
            supplier = _wb_supplier_status_without_db(client, number)
        except (HTTPException, TimeoutError, ConnectionError, OSError):
            supplier = ""
        if supplier == "confirm":
            break
    if supplier == "confirm":
        _cache_fast_pack_prep(number, credential_id=supply_cache_key[0], supplier=supplier, supply_id=sid)
    return supplier, sid


def _prepare_wb_order_for_fast_pack_background(
    *,
    api_token: str,
    order_id: uuid.UUID,
    credential_id: uuid.UUID,
    order_number: int,
    supply_id: str | None,
    supply_name: str,
    supply_cache_key: tuple[str, str, str],
) -> bool:
    """Product-scan worker: NEW->confirm, then cache the WB sticker before CHZ scan."""
    number = int(order_number)
    cache_key = (str(credential_id), number)
    prep = _fast_pack_prep_state(number, credential_id=credential_id)
    image, _item = _prefetched_wb_label(number, credential_id=credential_id)
    if str(prep.get("supplier") or "") in {"confirm", "complete"} and image is not None:
        return False
    # FC_K9R50_PREFETCH_SLOT_QUEUE: never make the product-scan HTTP request wait for
    # a WB slot, but also do not silently throw away prefetch when six other workers are
    # momentarily active.  The tiny daemon waits briefly in the background; CHZ remains
    # authoritative and can proceed even if this speculative warm-up times out.
    with _FAST_PACK_PREP_LOCK:
        if cache_key in _FAST_PACK_PREP_INFLIGHT:
            return False
        _FAST_PACK_PREP_INFLIGHT.add(cache_key)

    def _run() -> None:
        acquired = False
        wait_started = time.perf_counter()
        client: WBClient | None = None
        sid = str(supply_id or "").strip() or None
        supplier = ""
        try:
            acquired = _FAST_PACK_PREP_SLOTS.acquire(timeout=3.0)
            if not acquired:
                logger.info("FC_FAST_PREP_SLOT_TIMEOUT order_num=%s wait_ms=%.1f", number, (time.perf_counter()-wait_started)*1000.0)
                return
            logger.info("FC_FAST_PREP_SLOT_ACQUIRED order_num=%s wait_ms=%.1f", number, (time.perf_counter()-wait_started)*1000.0)
            client = WBClient(api_token)
            try:
                supplier, sid = _ensure_wb_confirm_without_db(
                    client, order_number=number, supply_id=sid, supply_name=supply_name,
                    supply_cache_key=supply_cache_key,
                )
            except Exception as exc:
                logger.info("FC_FAST_PREP_CONFIRM_DEFER order_num=%s error=%s", number, exc)
                return
            if supplier not in {"confirm", "complete"}:
                return

            # Now sticker prefetch is meaningful: WB officially exposes stickers only in
            # confirm/complete. Cache the raw sticker while the operator scans DataMatrix.
            image = None
            item = None
            for pause in (0.0, 0.04, 0.07, 0.11, 0.17, 0.25, 0.38, 0.55, 0.80, 1.10, 1.50):
                if pause:
                    time.sleep(pause)
                try:
                    image, item = _wb_sticker_bytes_without_db(client, number)
                except (HTTPException, TimeoutError, ConnectionError, OSError):
                    continue
                if image is not None:
                    with _AUTO_LABEL_PREFETCH_LOCK:
                        _AUTO_LABEL_PREFETCH_CACHE[cache_key] = {"image": image, "item": item, "at": time.monotonic()}
                        _prune_auto_label_prefetch_cache()
                    logger.info("FC_FAST_PREP_LABEL_READY order_num=%s bytes=%s", number, len(image))
                    break

            # Persist only marketplace preparation facts in one short DB transaction.
            gen = get_db()
            db2 = next(gen)
            try:
                order = _order_lookup(db2, order_id, credential_id)
                if sid:
                    order.supply_id = sid
                if supplier:
                    order.supplier_status = supplier
                raw = dict(order.raw_data or {})
                raw["_fc_fast_pack_prepared"] = {
                    "supplier_status": supplier, "supply_id": sid,
                    "label_cached": bool(image is not None), "prepared_at": _now().isoformat(),
                }
                order.raw_data = raw
                _event(db2, order, "FAST_PACK_PREPARED", value=supplier, data={"supply_id": sid, "label_cached": bool(image is not None)})
                db2.commit()
            except Exception:
                try: db2.rollback()
                except Exception: pass
                logger.exception("FC_FAST_PREP_DB_UPDATE_FAIL order_id=%s", order_id)
            finally:
                try: gen.close()
                except Exception: pass
        finally:
            if client is not None:
                try: client.close()
                except Exception: pass
            if acquired:
                try: _FAST_PACK_PREP_SLOTS.release()
                except Exception: pass
            with _FAST_PACK_PREP_LOCK:
                _FAST_PACK_PREP_INFLIGHT.discard(cache_key)

    try:
        threading.Thread(target=_run, name=f"fc-fast-prep-{number}", daemon=True).start()
    except Exception:
        with _FAST_PACK_PREP_LOCK:
            _FAST_PACK_PREP_INFLIGHT.discard(cache_key)
        logger.exception("FC_FAST_PREP_START_FAILED")
        return False
    return True


_CHZ_VERIFY_FAST_LOCK = threading.Lock()
_CHZ_VERIFY_FAST_INFLIGHT: set[tuple[str, str]] = set()


def _schedule_wb_chz_verification_background(
    *,
    client: WBClient,
    order_id: uuid.UUID,
    credential_id: uuid.UUID,
    order_number: int,
    expected_chz: str,
) -> bool:
    """Verify WB metadata projection without holding the physical printer path."""
    key = (str(order_id), str(expected_chz or ""))
    with _CHZ_VERIFY_FAST_LOCK:
        if key in _CHZ_VERIFY_FAST_INFLIGHT:
            return False
        _CHZ_VERIFY_FAST_INFLIGHT.add(key)

    def _persist(state: dict[str, Any] | None, *, timeout: bool = False) -> None:
        gen = get_db()
        db2 = next(gen)
        try:
            order = _order_lookup(db2, order_id, credential_id)
            if str(order.chz_code or "") != str(expected_chz or ""):
                db2.rollback(); return
            raw = dict(order.raw_data or {})
            marker = raw.get(_PACK_GATE_KEY) if isinstance(raw.get(_PACK_GATE_KEY), dict) else {}
            marker = dict(marker)
            pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
            pending = dict(pending)
            if state and state.get("accepted"):
                decision = str(state.get("decision") or "")
                raw.pop("_fc_chz_pending_wb", None)
                marker["wb_chz_verified"] = True
                marker["wb_chz_rejected"] = False
                marker["wb_decision"] = decision
                marker.pop("wb_chz_error", None)
                if str(marker.get("stage") or "") == "DONE":
                    marker["message"] = "ЧЗ подтверждён WB, этикетка напечатана, заказ упакован."
                _event(db2, order, "CHZ_VERIFIED_IN_WB_BACKGROUND_FAST", value=expected_chz, data={"decision": decision})
            elif state:
                decision = str(state.get("decision") or "")
                dkey = decision.lower()
                if dkey in WB_SGTIN_DECISION_MESSAGES and dkey not in {"pending", "required"}:
                    detail = _wb_sgtin_failure_detail(state, expected_chz)
                    pending.update({"chz": expected_chz, "reason": "WB_REJECTED_AFTER_SUBMIT", "last_error": detail, "last_attempt_at": _now().isoformat()})
                    raw["_fc_chz_pending_wb"] = pending
                    marker["wb_chz_verified"] = False
                    marker["wb_chz_rejected"] = True
                    marker["wb_decision"] = decision
                    marker["wb_chz_error"] = detail
                    if str(marker.get("stage") or "") == "DONE":
                        marker["message"] = "Этикетка напечатана, но WB отклонил ЧЗ. Отгрузка заблокирована до исправления."
                    _event(db2, order, "CHZ_REJECTED_IN_WB_BACKGROUND_FAST", value=expected_chz, data={"decision": decision, "detail": detail})
                else:
                    pending.update({"chz": expected_chz, "reason": "WB_SUBMITTED_AWAITING_VERIFY", "last_attempt_at": _now().isoformat()})
                    raw["_fc_chz_pending_wb"] = pending
            elif timeout:
                pending.update({"chz": expected_chz, "reason": "WB_SUBMITTED_AWAITING_VERIFY", "last_attempt_at": _now().isoformat()})
                raw["_fc_chz_pending_wb"] = pending
            marker["updated_at"] = _now().isoformat()
            raw[_PACK_GATE_KEY] = marker
            order.raw_data = raw
            db2.commit()
        except Exception:
            try: db2.rollback()
            except Exception: pass
            logger.exception("FC_FAST_CHZ_VERIFY_DB_FAIL order_id=%s", order_id)
        finally:
            try: gen.close()
            except Exception: pass

    def _run() -> None:
        try:
            last_state: dict[str, Any] | None = None
            for pause in (0.20, 0.35, 0.60, 1.0, 1.8, 3.0, 5.0, 8.0, 13.0, 21.0):
                time.sleep(pause)
                try:
                    state = _wb_sgtin_state_without_db(client, int(order_number), expected_chz)
                except (HTTPException, TimeoutError, ConnectionError, OSError):
                    continue
                except Exception:
                    logger.exception("FC_FAST_CHZ_VERIFY_READ_FAIL order_id=%s", order_id)
                    continue
                last_state = state
                if state.get("accepted"):
                    _persist(state)
                    return
                decision = str(state.get("decision") or "").strip().lower()
                if decision in WB_SGTIN_DECISION_MESSAGES and decision not in {"pending", "required"}:
                    _persist(state)
                    return
            _persist(last_state, timeout=True)
        finally:
            with _CHZ_VERIFY_FAST_LOCK:
                _CHZ_VERIFY_FAST_INFLIGHT.discard(key)

    threading.Thread(target=_run, name=f"fc-chz-verify-{str(order_id)[:8]}", daemon=True).start()
    return True



def _pack_gate_print_outcome(job: PrintJob | None) -> dict[str, Any]:
    if job is None:
        return {"pending": True, "success": False, "failed": False, "status": "MISSING", "error": ""}
    status = str(getattr(job, "status", "") or "").strip().upper()
    error = str(getattr(job, "error_message", "") or "").strip()
    failed_statuses = {"FAILED", "ERROR", "CANCELLED", "CANCELED"}
    success_statuses = {"DONE", "PRINTED", "SUCCESS", "SUCCEEDED", "COMPLETED", "COMPLETE", "OK"}
    pending_statuses = {"", "QUEUED", "PENDING", "PROCESSING", "PRINTING", "RUNNING", "CLAIMED", "IN_PROGRESS"}
    printed_at = (
        getattr(job, "printed_at", None)
        or getattr(job, "completed_at", None)
        or getattr(job, "finished_at", None)
        or getattr(job, "processed_at", None)
    )
    failed = status in failed_statuses
    success = (status in success_statuses) or bool(printed_at and not failed)
    return {
        "pending": not success and not failed,
        "success": bool(success),
        "failed": bool(failed),
        "status": status or ("DONE" if success else "QUEUED" if status in pending_statuses else ""),
        "error": error,
    }


def _finalize_strict_pack_from_job(
    *,
    order_id: uuid.UUID,
    credential_id: uuid.UUID,
    expected_chz: str,
    print_job_id: str,
    station_code: str,
    device_id: str | None,
) -> dict[str, Any]:
    """Commit PACKED exactly once, and only after the Print Agent ACKed success."""
    gen = get_db()
    db2 = next(gen)
    try:
        with distributed_lock(db2, f"strict-pack-finalize:{order_id}", blocking=False) as acquired:
            if not acquired:
                try: db2.rollback()
                except Exception: pass
                return {"pending": True, "done": False, "error": False, "message": "Ждём подтверждение печати…"}

            order = _order_lookup(db2, order_id, credential_id)
            marker = _pack_gate_marker(order)
            if str(marker.get("stage") or "") == "DONE":
                try: db2.rollback()
                except Exception: pass
                return {"pending": False, "done": True, "error": False, "status": str(marker.get("print_status") or "DONE"), "message": str(marker.get("message") or "Этикетка напечатана.")}
            if str(order.chz_code or "") != str(expected_chz or ""):
                try: db2.rollback()
                except Exception: pass
                return {"pending": False, "done": False, "error": True, "message": "ЧЗ заказа изменился во время печати. Упаковка остановлена."}
            if _wb_order_hard_cancelled_for_packing(order):
                msg = "WB отменил заказ до завершения печати. ЧЗ отвязан, упаковка остановлена."
                cancelled_chz = str(order.chz_code or "")
                _detach_chz_from_cancelled_picking_order(db2, order, source="PRINT_ACK_CANCEL")
                _event(db2, order, "STRICT_PACK_CANCELLED_BEFORE_PRINT_ACK", value=cancelled_chz)
                db2.commit()
                return {"pending": False, "done": False, "error": True, "message": msg}

            marker_job_id = str(marker.get("print_job_id") or "").strip()
            if str(marker.get("stage") or "") != "PRINT_QUEUED" or marker_job_id != str(print_job_id or ""):
                try: db2.rollback()
                except Exception: pass
                return {"pending": True, "done": False, "error": False, "message": str(marker.get("message") or "Ждём этикетку…")}

            try:
                job = db2.get(PrintJob, uuid.UUID(str(print_job_id)))
            except Exception:
                job = None
            outcome = _pack_gate_print_outcome(job)
            if outcome.get("failed"):
                msg = outcome.get("error") or f"Print Agent вернул статус {outcome.get('status') or 'FAILED'}."
                _set_pack_gate_marker(order, stage="ERROR", message=msg, error=msg, print_status=outcome.get("status"))
                _event(db2, order, "STRICT_PACK_PRINT_FAILED", value=order.chz_code, data={"print_job_id": print_job_id, "station": station_code, "status": outcome.get("status"), "error": outcome.get("error")})
                db2.commit()
                return {"pending": False, "done": False, "error": True, "status": outcome.get("status"), "message": msg}
            if not outcome.get("success"):
                try: db2.rollback()
                except Exception: pass
                return {"pending": True, "done": False, "error": False, "status": outcome.get("status"), "message": "Этикетка передана в Print Agent. Ждём фактическую печать…"}

            # SALE_FIRST_V1: marketplace availability was reduced when the order arrived,
            # and the picking list only bound the promise to a source address. The physical
            # InventoryBalance leaves that address exactly here, after Print Agent ACK.
            marker_wh = marker.get("physical_warehouse_id")
            try:
                physical_wh = uuid.UUID(str(marker_wh)) if marker_wh else None
            except Exception:
                physical_wh = None
            preserve_sent = _workflow_sent_is_sticky(order)
            if not preserve_sent:
                _consume_picking_reservation_nonblocking(
                    db2, order, "WB_PACKED_AFTER_PRINT_ACK", warehouse_id=physical_wh
                )
            now = _now()
            order.assembled_at = order.assembled_at or now
            order.packed_at = order.packed_at or now
            if not preserve_sent:
                _set_manual_workflow_status(order, None)
                setattr(order, "manual_status_override", False) if hasattr(order, "manual_status_override") else None
                order.local_status = "PACKED"
            else:
                _event(db2, order, "LATE_PRINT_ACK_STATUS_PRESERVED", value=order.local_status)
            order.label_generated_at = order.label_generated_at or now
            order.label_print_count = (order.label_print_count or 0) + 1
            raw = dict(order.raw_data or {})
            marker = raw.get(_PACK_GATE_KEY) if isinstance(raw.get(_PACK_GATE_KEY), dict) else {}
            wb_chz_verified = bool(marker.get("wb_chz_verified"))
            if wb_chz_verified:
                _clear_pending_wb_chz(order)
                raw = dict(order.raw_data or {})
                marker = raw.get(_PACK_GATE_KEY) if isinstance(raw.get(_PACK_GATE_KEY), dict) else {}
            marker = dict(marker)
            marker.update({
                "stage": "DONE",
                "message": ("ЧЗ подтверждён, этикетка напечатана, заказ упакован." if wb_chz_verified else "Этикетка напечатана, заказ упакован. Подтверждение ЧЗ в WB продолжается в фоне."),
                "printed_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "print_status": outcome.get("status") or "DONE",
            })
            marker.pop("error", None)
            raw[_PACK_GATE_KEY] = marker
            raw.pop("_fc_auto_wb_label_pending", None)
            order.raw_data = raw
            release_packing_lease(db2, order.id, station_code, device_id)
            _event(db2, order, "PACKED_AFTER_LABEL_PRINTED", value=order.chz_code, data={
                "print_job_id": print_job_id, "station": station_code, "print_status": outcome.get("status") or "DONE",
            })
            db2.commit()
            logger.info("FC_PACK_GATE_DONE order_id=%s station=%s job_id=%s", order_id, station_code, print_job_id)
            return {"pending": False, "done": True, "error": False, "status": outcome.get("status") or "DONE", "message": ("ЧЗ подтверждён. Этикетка напечатана. Можно сканировать следующий товар." if wb_chz_verified else "Этикетка напечатана. Можно сканировать следующий товар; ЧЗ подтверждается в WB в фоне.")}
    except Exception as exc:
        try: db2.rollback()
        except Exception: pass
        logger.exception("FC_PACK_GATE_FINALIZE_FAIL order_id=%s job_id=%s", order_id, print_job_id)
        return {"pending": True, "done": False, "error": False, "message": f"Печать подтверждается, финализация будет повторена: {exc}"}
    finally:
        try: gen.close()
        except Exception: pass


def _wait_strict_pack_print_ack(
    *,
    order_id: uuid.UUID,
    credential_id: uuid.UUID,
    expected_chz: str,
    print_job_id: str,
    station_code: str,
    device_id: str | None,
    timeout_seconds: float = 180.0,
) -> None:
    started_mono = time.monotonic()
    deadline = started_mono + max(10.0, float(timeout_seconds))
    while time.monotonic() < deadline:
        result = _finalize_strict_pack_from_job(
            order_id=order_id,
            credential_id=credential_id,
            expected_chz=expected_chz,
            print_job_id=print_job_id,
            station_code=station_code,
            device_id=device_id,
        )
        if result.get("done") or result.get("error"):
            return
        # FC_PACK_ACK_ADAPTIVE_POLL_09415G5A8G_20260904
        # 250 ms opened a fresh DB session/lock four times per second for every active
        # packing job, while the browser was polling the same state too. 500 ms keeps
        # the fast path responsive; older jobs back off to 1 s. Stock consumption and
        # FBS publication still happen immediately on the first successful ACK check.
        age = time.monotonic() - started_mono
        time.sleep(0.5 if age < 10.0 else 1.0)
    logger.warning("FC_PACK_GATE_PRINT_ACK_TIMEOUT order_id=%s station=%s job_id=%s", order_id, station_code, print_job_id)


def _schedule_strict_pack_gate(
    *,
    client: WBClient,
    order_id: uuid.UUID,
    credential_id: uuid.UUID,
    order_number: int,
    expected_chz: str,
    supply_id: str | None,
    supply_name: str,
    supply_cache_key: tuple[str, str, str],
    station_code: str,
    device_id: str | None,
    print_mode: str,
    physical_warehouse_id: uuid.UUID | None,
    width_mm: float,
    height_mm: float,
    rotate: int,
    scale_percent: float,
    offset_x_mm: float,
    offset_y_mm: float,
) -> bool:
    key = (str(order_id), str(expected_chz or ""))
    # 0.9.4.14a: keep Print Agent path identical to the proven 0.9.4.9e queue/ACK flow.
    # Browser/manual mode is only an alternate final hand-off and must not leak into
    # the WB verification worker.
    mode = "browser" if str(print_mode or "agent").strip().lower() == "browser" else "agent"
    with _PACK_GATE_BG_LOCK:
        if key in _PACK_GATE_BG_INFLIGHT:
            logger.info("FC_PACK_GATE_SKIP order_id=%s reason=inflight", order_id)
            return False
        _PACK_GATE_BG_INFLIGHT.add(key)

    def _fail(message: str, *, code: str = "WB_ERROR", decision: str | None = None) -> None:
        logger.warning("FC_PACK_GATE_FAIL order_id=%s order_num=%s code=%s message=%s", order_id, order_number, code, message)
        _pack_gate_db_update(
            order_id, credential_id, expected_chz,
            stage="ERROR", message=message, error=message, wb_decision=decision,
        )

    def _run() -> None:
        acquired = False
        started = time.perf_counter()
        try:
            acquired = _PACK_GATE_BG_SLOTS.acquire(timeout=2.0)
            if not acquired:
                _fail("Сервер упаковки занят. Повторите скан ЧЗ через несколько секунд.", code="WORKER_BUSY")
                return

            # 0.9.4.12 low-latency gate. Product scan has already started NEW->confirm
            # and sticker caching in parallel. Do not spend the CHZ critical path on an
            # unconditional metadata read-back: a normal successful PUT /meta/sgtin is
            # enough to release the already-prefetched sticker to the local print queue.
            verified = False
            submitted_to_wb = False
            state: dict[str, Any] = {"accepted": False, "decision": ""}

            prep = _fast_pack_prep_state(int(order_number), credential_id=credential_id)
            prepared_sid = str(prep.get("supply_id") or supply_id or "").strip() or None
            prepared_supplier = str(prep.get("supplier") or "").strip().lower()
            try:
                if prepared_supplier in {"confirm", "complete"}:
                    supplier, sid = prepared_supplier, prepared_sid
                else:
                    supplier, sid = _ensure_wb_confirm_without_db(
                        client, order_number=int(order_number), supply_id=prepared_sid,
                        supply_name=supply_name, supply_cache_key=supply_cache_key,
                    )
            except HTTPException as exc:
                _fail(f"WB не подготовил заказ к передаче ЧЗ: {exc.detail}", code=f"CONFIRM_{_wb_api_error_status(exc)}")
                return
            except Exception as exc:
                _fail(f"Не удалось подготовить заказ WB: {exc}", code="CONFIRM_NETWORK")
                return

            if supplier not in {"confirm", "complete"}:
                _fail(
                    f"WB пока не готов принять ЧЗ (supplierStatus={supplier or 'неизвестен'}). Повторите этот же ЧЗ.",
                    code="SUPPLIER_NOT_READY",
                )
                return
            if sid:
                _pack_gate_db_update(
                    order_id, credential_id, expected_chz,
                    stage="WAIT_WB_CHZ", message="Заказ WB подготовлен. Передаём ЧЗ…", supply_id=sid,
                )

            try:
                client.set_sgtin(int(order_number), [expected_chz])
                submitted_to_wb = True
            except HTTPException as exc:
                code = _wb_api_error_status(exc)
                if code == 409:
                    # A repeated scanner request commonly sees 409 after this exact SGTIN
                    # was already accepted while WB's metadata projection is still behind.
                    # Keep the proven 0.9.4.9e rule: only an explicit reject is fatal;
                    # otherwise continue to the sticker and verify asynchronously.
                    try:
                        state = _wb_sgtin_state_without_db(client, int(order_number), expected_chz)
                    except Exception:
                        state = {"accepted": False, "decision": ""}
                    if state.get("accepted"):
                        verified = True
                        submitted_to_wb = True
                    else:
                        decision = str(state.get("decision") or "").strip()
                        dkey = decision.lower()
                        if dkey in WB_SGTIN_DECISION_MESSAGES and dkey not in {"pending", "required"}:
                            _fail(_wb_sgtin_failure_detail(state, expected_chz), code="SGTIN_REJECTED", decision=decision)
                            return
                        submitted_to_wb = True
                        logger.info(
                            "FC_PACK_FAST_SGTIN_409_CONTINUE order_id=%s supplier=%s decision=%s",
                            order_id,
                            supplier or "unknown",
                            decision or "pending_projection",
                        )
                else:
                    _fail(f"WB не принял ЧЗ: {exc.detail}", code=f"SGTIN_{code}")
                    return
            except Exception as exc:
                _fail(f"Ошибка передачи ЧЗ в WB: {exc}", code="SGTIN_NETWORK")
                return

            # Critical-path rule: after a successful mutation, do not wait for the
            # eventually-consistent metadata projection. Verification continues in a
            # separate background worker; shipment is blocked while it remains pending.
            _pack_gate_db_update(
                order_id, credential_id, expected_chz,
                stage=("CHZ_VERIFIED" if verified else "WAIT_LABEL"),
                message=(
                    "ЧЗ уже подтверждён WB. Печатаем заранее загруженную этикетку…"
                    if verified else
                    "ЧЗ принят API WB. Печатаем этикетку сразу; контроль маркировки продолжается в фоне…"
                ),
                wb_decision=str(state.get("decision") or "") or None,
                wb_verified=bool(verified),
                supply_id=sid,
            )

            image: bytes | None = None
            item: dict[str, Any] | None = None
            last_error: Exception | None = None
            # Reuse the sticker that may already have been fetched while the operator
            # was scanning CHZ / while WB was moving the order into confirm.
            image, item = _prefetched_wb_label(int(order_number), credential_id=credential_id)
            label_prefetch_hit = image is not None
            logger.info(
                "FC_PACK_GATE_LABEL_PREFETCH order_id=%s order_num=%s hit=%s elapsed_ms=%.1f",
                order_id, order_number, label_prefetch_hit, (time.perf_counter()-started)*1000.0,
            )
            _pack_gate_db_update(
                order_id, credential_id, expected_chz,
                stage="WAIT_LABEL", message=("ЧЗ подтверждён. Получаем этикетку WB…" if verified else "Этикетка WB загружается; подтверждение ЧЗ продолжается параллельно…"),
                wb_verified=bool(verified),
            )
            for pause in (() if image is not None else (0.0, 0.05, 0.08, 0.12, 0.18, 0.26, 0.38, 0.55, 0.80, 1.10, 1.50, 2.0, 2.7, 3.6, 4.8, 6.5, 8.5, 11.0)):
                if pause:
                    time.sleep(pause)
                try:
                    image, item = _wb_sticker_bytes_without_db(client, int(order_number))
                    if image is not None:
                        break
                except HTTPException as exc:
                    last_error = exc
                    status = _wb_api_error_status(exc)
                    if status in {400, 401, 402, 403}:
                        break
                except (TimeoutError, ConnectionError, OSError) as exc:
                    last_error = exc
                    continue
                except Exception as exc:
                    logger.exception("FC_PACK_GATE_PROGRAM_ERROR order_id=%s stage=label_wait", order_id)
                    _fail(f"Внутренняя ошибка получения этикетки: {exc}", code="PROGRAM_ERROR")
                    return

            if image is not None:
                logger.info(
                    "FC_PACK_GATE_LABEL_READY order_id=%s order_num=%s prefetched=%s elapsed_ms=%.1f",
                    order_id, order_number, label_prefetch_hit, (time.perf_counter()-started)*1000.0,
                )

            if image is None:
                _fail(
                    ("ЧЗ подтверждён, но WB ещё не выдал этикетку. " if verified else "ЧЗ передан в WB, но этикетка ещё не готова. ") +
                    "Товар не списан и следующий заказ заблокирован. Повторите этот же ЧЗ для продолжения.",
                    code="LABEL_TIMEOUT",
                )
                return

            # Only now touch DB again, very briefly: enqueue exactly one physical label.
            queued_job_id: str | None = None
            gen = get_db()
            db2 = next(gen)
            try:
                order = _order_lookup(db2, order_id, credential_id)
                if str(order.chz_code or "") != str(expected_chz or ""):
                    db2.rollback()
                    return
                if _wb_order_currently_cancelled(order) or str(order.local_status or "").upper() == "CANCELLED":
                    db2.rollback()
                    _fail("WB отменил заказ до получения этикетки. Упаковка остановлена.", code="CANCELLED_BEFORE_LABEL")
                    return
                marker = _pack_gate_marker(order)
                existing_id = str(marker.get("print_job_id") or "").strip()
                if existing_id:
                    try:
                        existing_job = db2.get(PrintJob, uuid.UUID(existing_id))
                    except Exception:
                        existing_job = None
                    if existing_job is not None and str(getattr(existing_job, "status", "") or "").upper() not in {"FAILED", "ERROR", "CANCELLED"}:
                        db2.rollback()
                        return

                station_norm = normalize_station_code(station_code)
                # Reuse an already live job for this same order/station. This makes a
                # repeated browser request idempotent even if the previous response was lost.
                active_jobs = db2.execute(
                    select(PrintJob)
                    .where(
                        PrintJob.object_type == "MARKETPLACE_ORDER",
                        PrintJob.object_id == str(order.id),
                        PrintJob.document_type == "WB_ORDER_LABEL",
                        PrintJob.status.in_(["QUEUED", "CLAIMED", "PROCESSING", "PRINTING", "RUNNING"]),
                    )
                    .order_by(PrintJob.created_at.desc())
                    .limit(10)
                ).scalars().all()
                reusable = next(
                    (j for j in active_jobs if normalize_station_code(getattr(j, "station_code", None)) == station_norm),
                    None,
                )

                if item is not None:
                    _apply_wb_order_sticker(db2, order, item)
                if reusable is not None:
                    job = reusable
                else:
                    png = wb_sticker_exact_png(
                        image, width_mm=width_mm, height_mm=height_mm, rotate=rotate,
                        scale_percent=scale_percent, offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm,
                    )
                    # Important ordering: create the replacement first. If queue_print_job
                    # raises, the transaction rolls back and older QUEUED jobs stay alive.
                    job = queue_print_job(
                        db2, station_code=station_norm, payload=png,
                        file_name=f"WB-order-{order.external_order_id}.png", document_type="WB_ORDER_LABEL",
                        client_id=order.client_id, object_type="MARKETPLACE_ORDER", object_id=str(order.id),
                    )
                    if mode == "browser":
                        # Browser/manual jobs are a local blob hand-off, never visible to Print Agent.
                        job.status = "BROWSER_READY"
                    db2.flush()
                    old_jobs = db2.execute(
                        select(PrintJob).where(
                            PrintJob.object_type == "MARKETPLACE_ORDER",
                            PrintJob.object_id == str(order.id),
                            PrintJob.document_type == "WB_ORDER_LABEL",
                            PrintJob.status == "QUEUED",
                            PrintJob.id != job.id,
                        )
                    ).scalars().all()
                    for old_job in old_jobs:
                        old_job.status = "CANCELLED"
                        old_job.error_message = "Отменено 0.9.4.8 REV2: новое задание уже создано"
                order.label_generated_at = order.label_generated_at or _now()
                raw = dict(order.raw_data or {})
                marker = raw.get(_PACK_GATE_KEY) if isinstance(raw.get(_PACK_GATE_KEY), dict) else {}
                marker = dict(marker)
                marker.update({
                    "stage": ("BROWSER_READY" if mode == "browser" else "PRINT_QUEUED"),
                    "message": ("Этикетка готова. Открываем печать в браузере…" if mode == "browser" else f"Этикетка получена. Печатаем на {station_norm}…"),
                    "print_job_id": str(job.id),
                    "print_mode": mode,
                    "physical_warehouse_id": str(physical_warehouse_id) if physical_warehouse_id else None,
                    "updated_at": _now().isoformat(),
                })
                raw[_PACK_GATE_KEY] = marker
                raw["_fc_auto_wb_label"] = {
                    "chz": order.chz_code,
                    "station": station_norm,
                    "print_job_id": str(job.id),
                    "queued_at": _now().isoformat(),
                }
                pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
                pending = dict(pending)
                pending["reason"] = ("WB_VERIFIED_WAITING_PRINT" if verified else "WB_SUBMITTED_WAITING_PRINT")
                pending["wb_verified"] = bool(verified)
                if verified:
                    pending.pop("last_error", None)
                raw["_fc_chz_pending_wb"] = pending
                order.raw_data = raw
                _event(db2, order, "ORDER_LABEL_QUEUED_STRICT_GATE", data={
                    "print_job_id": str(job.id), "station": station_norm, "wb_chz_verified": bool(verified), "print_mode": mode,
                })
                db2.commit()
                queued_job_id = str(job.id)
                logger.info(
                    "FC_PACK_GATE_PRINT_QUEUED order_id=%s order_num=%s station=%s job_id=%s elapsed_ms=%.1f",
                    order_id, order_number, station_norm, job.id, (time.perf_counter()-started)*1000.0,
                )
            except Exception as exc:
                try:
                    db2.rollback()
                except Exception:
                    pass
                _fail(f"Не удалось поставить этикетку в Print Agent: {exc}", code="QUEUE_PRINT")
            finally:
                try:
                    gen.close()
                except Exception:
                    pass

            # A successful SGTIN mutation releases the printer immediately. The slower
            # WB metadata projection is checked independently and will block shipment if
            # it stays pending/rejected, but it no longer adds seconds before paper moves.
            if queued_job_id and submitted_to_wb and not verified:
                _schedule_wb_chz_verification_background(
                    client=client, order_id=order_id, credential_id=credential_id,
                    order_number=int(order_number), expected_chz=expected_chz,
                )

            # The WB concurrency slot is no longer needed once the sticker is queued.
            # Release it before waiting on the local printer so a disconnected printer
            # cannot slow CHZ/sticker work at the other packing stations.
            if queued_job_id and acquired:
                try:
                    _PACK_GATE_BG_SLOTS.release()
                    acquired = False
                except Exception:
                    pass

            # The physical print wait owns no SQLAlchemy session. A page refresh cannot
            # leave a printed order stuck in PICKING, and a slow printer cannot pin DB.
            if queued_job_id and mode == "agent":
                _wait_strict_pack_print_ack(
                    order_id=order_id, credential_id=credential_id, expected_chz=expected_chz,
                    print_job_id=queued_job_id, station_code=station_code, device_id=device_id,
                )
        finally:
            if acquired:
                try:
                    _PACK_GATE_BG_SLOTS.release()
                except Exception:
                    pass
            with _PACK_GATE_BG_LOCK:
                _PACK_GATE_BG_INFLIGHT.discard(key)

    threading.Thread(target=_run, name=f"fc-pack-gate-{str(order_id)[:8]}", daemon=True).start()
    return True


# FC_K9R50_CHZ_SCAN_LATENCY_GUARD
def _packing_chz_hot_response(order: MarketplaceOrder) -> dict[str, Any]:
    """Small response for the CHZ scanner critical path.

    A successful CHZ scan only needs scalar order/packing state in the browser.  The old
    response called _order_response(), which also recalculated inventory buckets, product
    cards and picking-list reservations after the local CHZ commit.  Under unrelated DB
    load that read-only projection could hold the HTTP response for seconds even though
    the background WB/label worker was already running, making the scanner look frozen.
    """
    marker = _pack_gate_marker(order)
    return {
        "id": str(order.id),
        "external_order_id": order.external_order_num or order.external_order_id,
        "article": order.article,
        "barcodes": order.barcodes or [],
        "local_status": order.local_status,
        "local_status_name": LOCAL_STATUS_NAMES.get(order.local_status, order.local_status),
        "product_scanned_at": order.product_scanned_at,
        "chz_attached": bool(order.chz_code),
        "chz_pending_wb": _has_pending_wb_chz(order),
        "label_generated": bool(order.label_generated_at),
        "label_generated_at": order.label_generated_at,
        "label_print_count": order.label_print_count,
        "packed_at": order.packed_at,
        "supply_id": order.supply_id,
        "pack_stage": marker.get("stage"),
    }


@router.post("/wb/packing/scan-chz")
def wb_scan_chz(payload: WBScanCHZRequest, warehouse_id: uuid.UUID | None = Query(default=None), station_code: str | None = Query(default=None), device_id: str | None = Query(default=None), wb_label_width: float = Query(default=58, ge=25, le=120), wb_label_height: float = Query(default=40, ge=25, le=120), wb_label_rotate: int = Query(default=-1), wb_label_scale: float = Query(default=100, ge=85, le=108), wb_label_offset_x: float = Query(default=0, ge=-5, le=5), wb_label_offset_y: float = Query(default=0, ge=-5, le=5), defer_print: int = Query(default=1, ge=0, le=1), print_mode: str = Query(default="agent", pattern="^(agent|browser)$"), db: Session = Depends(get_db)):
    """Strict sequential packing gate.

    The exact scanner CHZ is persisted first, but stock is NOT consumed and PACKED is
    NOT committed yet. Product scan prepares WB confirm/sticker in parallel; the DB-free
    CHZ worker submits SGTIN and releases the cached sticker to Print Agent immediately.
    Slow WB metadata projection is verified in background and blocks shipment if unresolved.
    PACKED is finalized only after the agent reports successful physical printing.
    """
    scan_started = time.perf_counter()
    _, client = _wb(db, payload.credential_id)
    order = _order_lookup(db, payload.order_id, payload.credential_id)
    cred = _credential(db, payload.credential_id, "WB")
    _assert_wb_work(cred, order, warehouse_id)
    station = normalize_station_code(station_code or payload.print_station or "PACK-01")
    if not assert_or_renew_packing_lease(db, order_id=order.id, station_code=station, device_id=device_id, ttl_seconds=600):
        raise HTTPException(status_code=409, detail="Этот заказ сейчас закреплён за другим упаковочным столом.")
    if not order.product_scanned_at:
        raise HTTPException(status_code=409, detail="Сначала отсканируйте штрихкод товара, затем код маркировки.")

    chz = _normalize_chz(payload.chz)
    if not chz:
        raise HTTPException(status_code=400, detail="Пустой код маркировки")
    if _wb_order_hard_cancelled_for_packing(order):
        raise HTTPException(status_code=409, detail="WB уже показывает заказ как уехавший/завершённый. Код маркировки к нему прикреплять нельзя.")
    if order.chz_code and order.chz_code != chz:
        shown = order.chz_code.replace("\x1d", "␝")
        if len(shown) > 70:
            shown = shown[:42] + "…" + shown[-18:]
        raise HTTPException(status_code=409, detail=f"К этому заказу уже привязан другой ЧЗ: {shown}. Удалите/замените его в карточке заказа.")

    duplicate = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id.in_(_packing_credential_ids_for_scan(db, cred)),
        MarketplaceOrder.chz_code == chz,
        MarketplaceOrder.id != order.id,
    )).scalars().first()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"Этот ЧЗ уже привязан к заказу WB №{duplicate.external_order_id}.")

    marker = _pack_gate_marker(order)
    existing_job_id = str(marker.get("print_job_id") or "").strip()
    if order.chz_code == chz and marker.get("stage") == "DONE":
        return {
            "ok": True, "gate_active": False, "packed": True, "printed": True,
            "order": _packing_chz_hot_response(order),
            "message": "ЧЗ уже подтверждён, этикетка напечатана, заказ упакован.",
        }
    if order.chz_code == chz and marker.get("stage") == "BROWSER_READY" and existing_job_id:
        db.commit()
        return {
            "ok": True, "gate_active": True, "packed": False, "printed": False,
            "browser_ready": True, "order": _packing_chz_hot_response(order),
            "stage": "BROWSER_READY", "print_job_id": existing_job_id,
            "message": marker.get("message") or "Этикетка готова для ручной печати.",
        }

    if order.chz_code == chz and marker.get("stage") == "PRINT_QUEUED" and existing_job_id:
        db.commit()
        return {
            "ok": True, "gate_active": True, "packed": False, "printed": False,
            "order": _packing_chz_hot_response(order),
            "stage": "PRINT_QUEUED", "print_job_id": existing_job_id,
            "message": marker.get("message") or "Этикетка уже передана в Print Agent. Ждём подтверждение печати.",
        }

    current_local = str(order.local_status or "").strip().upper()
    if current_local == "NEW":
        raise HTTPException(
            status_code=409,
            detail="Заказ находится в «Новых». Сначала переведите его в «На сборке» вручную или через сборочный лист. ЧЗ не присвоен.",
        )
    if current_local != "PICKING":
        raise HTTPException(
            status_code=409,
            detail=f"Заказ уже находится в статусе {current_local or 'UNKNOWN'}. Повторная упаковка через сканер запрещена.",
        )

    first_local_save = not bool(order.chz_code)
    order.chz_code = chz
    order.chz_attached_at = order.chz_attached_at or _now()
    _set_pending_wb_chz(order, chz, reason="STRICT_PACK_GATE_WAIT_WB", supply_id=order.supply_id)
    raw = dict(order.raw_data or {})
    raw.pop("_fc_auto_wb_label_pending", None)
    order.raw_data = raw
    _set_pack_gate_marker(
        order,
        chz=chz,
        station=station,
        device_id=str(device_id or ""),
        stage="WAIT_WB_CHZ",
        message="Передаём ЧЗ в Wildberries…",
        print_job_id=None,
        error=None,
        started_at=_now().isoformat(),
        print_mode=("browser" if str(print_mode).lower() == "browser" else "agent"),
        physical_warehouse_id=str(warehouse_id) if warehouse_id else None,
    )
    _event(db, order, "CHZ_SCANNED_LOCAL" if first_local_save else "CHZ_RETRY_SCANNED", value=chz, data={
        "mode": "SELSUP_STYLE_UNIFIED_FAST_PIPELINE_0_9_4_9E", "station_code": station, "device_id": device_id,
    })

    # Intentionally keep local_status=PICKING, reservation ACTIVE and packing lease held.
    # This is the core invariant: an order is not PACKED until the physical print ACK.
    order.local_status = "PICKING"
    order.packed_at = None
    order.assembled_at = None
    order_number = _wb_order_number(order)
    supply_id = str(order.supply_id or "").strip() or None
    supply_name = _wb_chz_supply_name(order)
    supply_cache_key = (str(order.credential_id), str(order.wb_warehouse_id or ""), str(order.cargo_type or ""))
    db.commit()

    # 0.9.4.9e unified packing worker. Unlike the old split PACKING_FAST +
    # AUTO_LABEL workers, this one also owns the NEW -> active supply -> confirm step.
    # Sticker prefetch runs in parallel, so the label is queued at the first moment WB
    # exposes it; slow metadata projection no longer blocks physical printing.
    scheduled = _schedule_strict_pack_gate(
        client=client,
        order_id=order.id,
        credential_id=payload.credential_id,
        order_number=order_number,
        expected_chz=chz,
        supply_id=supply_id,
        supply_name=supply_name,
        supply_cache_key=supply_cache_key,
        station_code=station,
        device_id=device_id,
        print_mode=("browser" if str(print_mode).lower() == "browser" else "agent"),
        physical_warehouse_id=warehouse_id,
        width_mm=wb_label_width,
        height_mm=wb_label_height,
        rotate=wb_label_rotate,
        scale_percent=wb_label_scale,
        offset_x_mm=wb_label_offset_x,
        offset_y_mm=wb_label_offset_y,
    )
    logger.info(
        "FC_CHZ_SCAN_ACCEPTED_LOCAL order_id=%s order_num=%s scheduled=%s elapsed_ms=%.1f",
        order.id, order_number, bool(scheduled), (time.perf_counter()-scan_started)*1000.0,
    )
    return {
        "ok": True,
        "gate_active": True,
        "gate_scheduled": bool(scheduled),
        "chz_background": bool(scheduled),
        "label_background": bool(scheduled),
        "packed": False,
        "printed": False,
        "stage": "WAIT_WB_CHZ",
        "order": _packing_chz_hot_response(order),
        "message": "ЧЗ сохранён. WB-поставка, ЧЗ и этикетка обрабатываются единым быстрым потоком; печать начнётся сразу после появления стикера.",
    }




def _packing_progress_order_snapshot(order: MarketplaceOrder) -> dict[str, Any]:
    """Minimal order projection for the 0.5-1.5 s packing progress hot path.

    The old progress endpoint called _order_response(), which loaded SKU, stock buckets,
    product cards and reservations every poll. The browser merge routine only needs state
    fields here; the full board refresh still provides complete order details.
    """
    marker = _pack_gate_marker(order)
    return {
        "id": str(order.id),
        "external_order_id": order.external_order_num or order.external_order_id,
        "local_status": order.local_status,
        "local_status_name": LOCAL_STATUS_NAMES.get(order.local_status, order.local_status),
        "chz_attached": bool(order.chz_code),
        "chz_pending_wb": _has_pending_wb_chz(order),
        "label_generated": bool(order.label_generated_at),
        "label_generated_at": order.label_generated_at,
        "label_print_count": order.label_print_count,
        "packed_at": order.packed_at,
        "supply_id": order.supply_id,
        "pack_stage": marker.get("stage"),
    }



# FC_PACK_PROGRESS_LOAD_SHIELD_09415G5A8G_20260904
# Browser progress polling and the background print-ACK waiter used to finalize the same
# order concurrently. Keep both recovery paths, but allow the browser to enter the
# DB-heavy finalizer at most once per short window. Order sync and stock publication are
# intentionally untouched.
_PACK_PROGRESS_FINALIZE_LOCK = threading.Lock()
_PACK_PROGRESS_FINALIZE_LAST: dict[str, float] = {}
_PACK_PROGRESS_FINALIZE_MIN_SECONDS = 0.75


def _packing_progress_may_finalize(order_id: uuid.UUID) -> bool:
    key = str(order_id)
    now_mono = time.monotonic()
    with _PACK_PROGRESS_FINALIZE_LOCK:
        last = float(_PACK_PROGRESS_FINALIZE_LAST.get(key, 0.0) or 0.0)
        if now_mono - last < _PACK_PROGRESS_FINALIZE_MIN_SECONDS:
            return False
        _PACK_PROGRESS_FINALIZE_LAST[key] = now_mono
        # Bound the tiny in-process map even on a very long-running API process.
        if len(_PACK_PROGRESS_FINALIZE_LAST) > 4096:
            cutoff = now_mono - 300.0
            stale = [k for k, ts in _PACK_PROGRESS_FINALIZE_LAST.items() if ts < cutoff]
            for stale_key in stale[:2048]:
                _PACK_PROGRESS_FINALIZE_LAST.pop(stale_key, None)
        return True


@router.get("/wb/packing/progress")
def wb_packing_progress(
    credential_id: uuid.UUID,
    order_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    station_code: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cred = _credential(db, credential_id, "WB")
    order = _order_lookup(db, order_id, credential_id)
    _assert_wb_work(cred, order, warehouse_id)
    marker = _pack_gate_marker(order)
    station = normalize_station_code(station_code or marker.get("station") or "PACK-01")
    expected_chz = str(order.chz_code or marker.get("chz") or "")
    stage = str(marker.get("stage") or "WAIT_WB_CHZ")
    print_job_id = str(marker.get("print_job_id") or "").strip()
    message = str(marker.get("message") or "Выполняется упаковка…")

    if stage == "DONE":
        response = {
            "ok": True, "done": True, "printed": True, "packed": True,
            "stage": "DONE", "print_job_id": print_job_id or None,
            "print_status": marker.get("print_status") or "DONE",
            "message": message,
            "order": _packing_progress_order_snapshot(order),
        }
        try:
            if db.in_transaction():
                db.rollback()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        return response
    if stage == "ERROR":
        response = {
            "ok": False, "done": False, "printed": False, "packed": False,
            "error": True, "stage": "ERROR", "print_job_id": print_job_id or None,
            "message": str(marker.get("error") or message or "Ошибка упаковки"),
        }
        try:
            if db.in_transaction():
                db.rollback()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        return response

    if stage == "BROWSER_READY" and print_job_id:
        response = {
            "ok": True, "done": False, "printed": False, "packed": False,
            "browser_ready": True, "stage": "BROWSER_READY", "print_job_id": print_job_id,
            "message": message or "Этикетка готова для ручной печати.",
            "order": _packing_progress_order_snapshot(order),
        }
        try:
            if db.in_transaction():
                db.rollback()
        except Exception:
            try: db.rollback()
            except Exception: pass
        return response

    # The lease was renewed for 240 s at scan-chz. Do not write PostgreSQL every 350 ms
    # from the progress poll; this endpoint is intentionally read-only until print ACK.
    try:
        if db.in_transaction():
            db.rollback()
    except Exception:
        try: db.rollback()
        except Exception: pass

    if print_job_id:
        if _packing_progress_may_finalize(order_id):
            result = _finalize_strict_pack_from_job(
                order_id=order_id,
                credential_id=credential_id,
                expected_chz=expected_chz,
                print_job_id=print_job_id,
                station_code=station,
                device_id=device_id,
            )
        else:
            # The background waiter is already checking the exact same PrintJob. A hot
            # browser poll inside this sub-second window only needs the current marker.
            result = {
                "pending": True, "done": False, "error": False,
                "status": "QUEUED",
                "message": message or "Этикетка передана в Print Agent. Ждём фактическую печать…",
            }
        if result.get("done"):
            order = _order_lookup(db, order_id, credential_id)
            return {
                "ok": True, "done": True, "printed": True, "packed": True,
                "stage": "DONE", "print_job_id": print_job_id,
                "print_status": result.get("status") or "DONE",
                "message": result.get("message") or "ЧЗ подтверждён. Этикетка напечатана. Можно сканировать следующий товар.",
                "order": _packing_progress_order_snapshot(order),
            }
        if result.get("error"):
            return {
                "ok": False, "done": False, "printed": False, "packed": False,
                "error": True, "stage": "ERROR", "print_job_id": print_job_id,
                "print_status": result.get("status"),
                "message": result.get("message") or "Ошибка печати",
            }
        order = _order_lookup(db, order_id, credential_id)
        return {
            "ok": True, "done": False, "printed": False, "packed": False,
            "stage": "PRINT_QUEUED", "print_job_id": print_job_id,
            "print_status": result.get("status"),
            "message": result.get("message") or message,
            "order": _packing_progress_order_snapshot(order),
        }

    # Worker is still talking to WB. Re-read marker only on the next UI poll.
    order = _order_lookup(db, order_id, credential_id)
    return {
        "ok": True, "done": False, "printed": False, "packed": False,
        "stage": stage,
        "print_job_id": None,
        "message": message,
        "order": _packing_progress_order_snapshot(order),
    }


@router.get("/wb/packing/browser-label")
def wb_packing_browser_label(
    credential_id: uuid.UUID,
    order_id: uuid.UUID,
    print_job_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Hand a prepared label to the browser and atomically finish manual packing.

    Browser printing cannot receive a trustworthy OS-level printer ACK. The strongest
    deterministic boundary available is successful delivery of the exact prepared label
    bytes to the authenticated packing page. A retry is idempotent and returns the same
    bytes without a second stock write-off.
    """
    cred = _credential(db, credential_id, "WB")
    order = _order_lookup(db, order_id, credential_id)
    _assert_wb_work(cred, order, warehouse_id)
    marker = _pack_gate_marker(order)
    marker_job = str(marker.get("print_job_id") or "")
    if marker_job != str(print_job_id):
        raise HTTPException(status_code=409, detail="Эта ручная этикетка уже не является активной для заказа")
    job = db.get(PrintJob, print_job_id)
    if not job or job.object_type != "MARKETPLACE_ORDER" or str(job.object_id or "") != str(order.id):
        raise HTTPException(status_code=404, detail="Этикетка ручной печати не найдена")
    stage = str(marker.get("stage") or "")
    if stage not in {"BROWSER_READY", "DONE"}:
        raise HTTPException(status_code=409, detail="Этикетка ещё не готова для ручной печати")

    if stage != "DONE":
        if _wb_order_hard_cancelled_for_packing(order):
            cancelled_chz = str(order.chz_code or "")
            _detach_chz_from_cancelled_picking_order(db, order, source="BROWSER_PRINT_CANCEL")
            _event(db, order, "BROWSER_PRINT_CANCELLED_BEFORE_DELIVERY", value=cancelled_chz)
            db.commit()
            raise HTTPException(status_code=409, detail="WB отменил заказ до ручной печати. Упаковка остановлена.")
        marker_wh = marker.get("physical_warehouse_id")
        try:
            physical_wh = uuid.UUID(str(marker_wh)) if marker_wh else warehouse_id
        except Exception:
            physical_wh = warehouse_id
        preserve_sent = _workflow_sent_is_sticky(order)
        stock_result = ({"changed":False,"reason":"late_callback_sent_preserved"} if preserve_sent else
            _consume_picking_reservation_nonblocking(
                db, order, "WB_PACKED_BROWSER_LABEL_DELIVERED", warehouse_id=physical_wh
            ))
        now = _now()
        order.assembled_at = order.assembled_at or now
        order.packed_at = order.packed_at or now
        if not preserve_sent:
            order.local_status = "PACKED"
            _set_manual_workflow_status(order, None)
        else:
            _event(db, order, "LATE_BROWSER_LABEL_STATUS_PRESERVED", value=order.local_status)
        order.label_generated_at = order.label_generated_at or now
        order.label_print_count = (order.label_print_count or 0) + 1
        raw = dict(order.raw_data or {})
        fresh = raw.get(_PACK_GATE_KEY) if isinstance(raw.get(_PACK_GATE_KEY), dict) else {}
        fresh = dict(fresh)
        fresh.update({
            "stage": "DONE",
            "message": "Этикетка передана браузеру, заказ упакован.",
            "printed_at": now.isoformat(),
            "updated_at": now.isoformat(),
            "print_status": "BROWSER_DELIVERED",
        })
        raw[_PACK_GATE_KEY] = fresh
        raw.pop("_fc_auto_wb_label_pending", None)
        order.raw_data = raw
        job.status = "PRINTED"
        job.completed_at = now
        job.error_message = None
        release_packing_lease(db, order.id, str(marker.get("station") or job.station_code or "PACK-01"), str(marker.get("device_id") or "") or None)
        _event(db, order, "PACKED_AFTER_BROWSER_LABEL_DELIVERED", value=order.chz_code, data={
            "print_job_id": str(job.id), "stock": stock_result,
        })
        db.commit()

    return Response(
        content=bytes(job.payload or b""),
        media_type=(job.content_type or "image/png"),
        headers={"Content-Disposition": f'inline; filename="{job.file_name or "WB-label.png"}"'},
    )


@router.post("/wb/orders/{order_id}/chz/manual")
def wb_manual_chz(order_id: uuid.UUID, payload: dict[str, Any], db: Session = Depends(get_db)):
    """Save/replace a CHZ and verify the exact code in WB before calling it synced."""
    try:
        credential_id = uuid.UUID(str(payload.get("credential_id")))
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный credential_id")
    _, client = _wb(db, credential_id)
    order = _order_lookup(db, order_id, credential_id)
    cred = _credential(db, credential_id, "WB")
    physical_warehouse_id = payload.get("warehouse_id")
    _assert_wb_work(cred, order, physical_warehouse_id)

    chz = _normalize_chz(str(payload.get("chz") or ""))
    replace = bool(payload.get("replace", True))
    previous = order.chz_code
    if previous and previous != chz and not replace:
        raise HTTPException(status_code=409, detail="У заказа уже есть ЧЗ. Для замены подтвердите замену.")

    duplicate = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id == credential_id,
        MarketplaceOrder.chz_code == chz,
        MarketplaceOrder.id != order.id,
    )).scalars().first()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"Этот ЧЗ уже привязан к заказу WB №{duplicate.external_order_id}.")

    order.chz_code = chz
    order.chz_attached_at = _now()
    _set_pending_wb_chz(order, chz, reason="MANUAL_AWAITING_WB_VERIFY", supply_id=order.supply_id)
    if previous and previous != chz:
        _event(db, order, "CHZ_MANUAL_REPLACED_LOCAL", value=chz, data={"previous": previous})
    else:
        _event(db, order, "CHZ_MANUAL_SAVED_LOCAL", value=chz)
    # A pending manual correction changes only CHZ synchronization state. Preserve
    # the current FulfillCore status; the operator can change it separately if needed.
    db.commit()

    try:
        _bind_chz_to_wb_verified(db, client, order, chz)
    except HTTPException as exc:
        raw = dict(order.raw_data or {})
        pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
        if isinstance(pending, dict):
            pending["reason"] = "MANUAL_WB_VERIFY_FAILED"
            pending["last_error"] = str(exc.detail or "")
            pending["last_attempt_at"] = _now().isoformat()
            raw["_fc_chz_pending_wb"] = pending
            order.raw_data = raw
        _event(db, order, "CHZ_MANUAL_PENDING_WB", value=chz, data={"previous": previous, "error": str(exc.detail or "")})
        if exc.status_code in {202, 208}:
            raw = dict(order.raw_data or {})
            pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
            pending["reason"] = "SUBMITTED_AWAITING_WB_VERIFY" if exc.status_code == 202 else "LOCAL_PACKED_CLOSED_SUPPLY"
            raw["_fc_chz_pending_wb"] = pending
            order.raw_data = raw
            if order.product_scanned_at and not _workflow_sent_is_sticky(order):
                _consume_picking_reservation_nonblocking(db, order, "WB_PACKED_MANUAL_CHZ_PENDING")
                now = _now()
                order.assembled_at = order.assembled_at or now
                order.packed_at = order.packed_at or now
                order.local_status = "PACKED"
            _event(db, order, "CHZ_MANUAL_SUBMITTED_TO_WB", value=chz, data={"previous": previous, "background_check": True})
            db.commit()
            return {
                "ok": True,
                "wb_synced": exc.status_code == 202,
                "wb_chz_pending": True,
                "wb_chz_submitted": exc.status_code == 202,
                "wb_chz_local_fallback": exc.status_code == 208,
                "wb_chz_verified": False,
                "order": _order_response(db, order, cred=cred),
                "message": "ЧЗ отправлен в Wildberries. Проверка продолжится автоматически." if exc.status_code == 202 else "Заказ упакован. ЧЗ сохранён для фоновой отправки после открытия WB.",
            }
        db.commit()
        return {
            "ok": True,
            "wb_synced": False,
            "wb_chz_pending": True,
            "wb_chz_submitted": False,
            "wb_chz_verified": False,
            "order": _order_response(db, order, cred=cred),
            "message": f"ЧЗ сохранён в FulfillCore, но WB пока не подтвердил привязку. Статус FulfillCore не изменён. Причина: {exc.detail}",
        }

    if order.product_scanned_at and not _workflow_sent_is_sticky(order):
        _consume_picking_reservation_nonblocking(db, order, "WB_PACKED_MANUAL_CHZ_VERIFIED")
        now = _now()
        order.assembled_at = now
        order.packed_at = now
        order.local_status = "PACKED"
    _clear_pending_wb_chz(order)
    _event(db, order, "CHZ_MANUAL_SYNCED_TO_WB", value=chz, data={"previous": previous, "verified": True})
    db.commit()
    return {
        "ok": True,
        "wb_synced": True,
        "wb_chz_pending": False,
        "wb_chz_submitted": True,
        "wb_chz_verified": True,
        "order": _order_response(db, order, cred=cred),
        "message": "ЧЗ сохранён и подтверждён в Wildberries",
    }


@router.post("/wb/orders/{order_id}/chz/retry")
def wb_retry_order_chz(order_id: uuid.UUID, payload: dict[str, Any], db: Session = Depends(get_db)):
    """Retry WB binding for the already scanned physical CHZ without rescanning it."""
    try:
        credential_id = uuid.UUID(str(payload.get("credential_id")))
    except Exception:
        raise HTTPException(status_code=400, detail="Некорректный credential_id")
    _, client = _wb(db, credential_id)
    order = _order_lookup(db, order_id, credential_id)
    cred = _credential(db, credential_id, "WB")
    physical_warehouse_id = payload.get("warehouse_id")
    _assert_wb_work(cred, order, physical_warehouse_id)
    if not order.chz_code:
        raise HTTPException(status_code=409, detail="Сначала сохраните или отсканируйте ЧЗ для заказа.")
    try:
        _bind_chz_to_wb_verified(db, client, order, order.chz_code)
    except HTTPException as exc:
        _set_pending_wb_chz(order, order.chz_code, reason="MANUAL_RETRY_FAILED", supply_id=order.supply_id)
        raw = dict(order.raw_data or {})
        pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
        if isinstance(pending, dict):
            pending["last_error"] = str(exc.detail or "")
            pending["last_attempt_at"] = _now().isoformat()
            raw["_fc_chz_pending_wb"] = pending
            order.raw_data = raw
        _event(db, order, "CHZ_RETRY_FAILED", value=order.chz_code, data={"error": str(exc.detail or "")})
        if exc.status_code in {202, 208}:
            raw = dict(order.raw_data or {})
            pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
            pending["reason"] = "SUBMITTED_AWAITING_WB_VERIFY" if exc.status_code == 202 else "LOCAL_PACKED_CLOSED_SUPPLY"
            raw["_fc_chz_pending_wb"] = pending
            order.raw_data = raw
            if order.product_scanned_at and not _workflow_sent_is_sticky(order):
                _consume_picking_reservation_nonblocking(db, order, "WB_PACKED_CHZ_RETRY_PENDING")
                now = _now()
                order.assembled_at = order.assembled_at or now
                order.packed_at = order.packed_at or now
                order.local_status = "PACKED"
            print_ready = bool(order.product_scanned_at and order.local_status == "PACKED")
            _event(db, order, "CHZ_RETRY_SUBMITTED_TO_WB", value=order.chz_code, data={"background_check": True})
            db.commit()
            return {
                "ok": True,
                "wb_synced": exc.status_code == 202,
                "wb_chz_pending": True,
                "wb_chz_submitted": exc.status_code == 202,
                "wb_chz_local_fallback": exc.status_code == 208,
                "wb_chz_verified": False,
                "print_ready": print_ready,
                "order": _order_response(db, order, cred=cred),
                "message": (
                    "ЧЗ повторно отправлен в Wildberries. Проверка продолжится автоматически."
                    if exc.status_code == 202
                    else "Заказ остаётся упакованным. ЧЗ будет отправлен при следующей доступной попытке WB."
                ),
            }
        db.commit()
        return {
            "ok": True,
            "wb_synced": False,
            "wb_chz_pending": True,
            "wb_chz_submitted": False,
            "wb_chz_verified": False,
            "print_ready": False,
            "order": _order_response(db, order, cred=cred),
            "message": f"WB не принял отправку ЧЗ. Причина: {exc.detail}",
        }

    if order.product_scanned_at and not _workflow_sent_is_sticky(order):
        _consume_picking_reservation_nonblocking(db, order, "WB_PACKED_CHZ_RETRY_VERIFIED")
        now = _now()
        order.assembled_at = now
        order.packed_at = now
        order.local_status = "PACKED"
    print_ready = bool(order.product_scanned_at and order.local_status == "PACKED")
    _clear_pending_wb_chz(order)
    _event(db, order, "CHZ_RETRY_SYNCED_TO_WB", value=order.chz_code)
    db.commit()
    return {
        "ok": True,
        "wb_synced": True,
        "wb_chz_pending": False,
        "wb_chz_submitted": True,
        "wb_chz_verified": True,
        "print_ready": print_ready,
        "order": _order_response(db, order, cred=cred),
        "message": "ЧЗ подтверждён в Wildberries",
    }


@router.delete("/wb/orders/{order_id}/chz")
def wb_delete_chz(
    order_id: uuid.UUID,
    credential_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Safely unlink CHZ from an active or cancelled WB order.

    Active order: WB must confirm deletion of the ``sgtin`` metadata before the
    local binding is cleared.  This prevents one DataMatrix from becoming locally
    reusable while WB still has it attached to the old order.

    Cancelled order: WB commonly rejects metadata mutation with HTTP 409 because
    the order is terminal.  In that one case we refresh the exact WB status and,
    only when cancellation is confirmed, clear the local binding without reviving
    the order or touching physical stock.
    """
    _, client = _wb(db, credential_id)
    order = _order_lookup(db, order_id, credential_id)
    cred = _credential(db, credential_id, "WB")
    _assert_wb_work(cred, order, warehouse_id)
    if not order.chz_code:
        raise HTTPException(status_code=409, detail="За этим заказом в FulfillCore сейчас не закреплён код маркировки")

    old_chz = str(order.chz_code)
    pending_local_only = _has_pending_wb_chz(order)
    cancelled = _wb_order_currently_cancelled(order) or str(order.local_status or "").upper() == "CANCELLED"
    wb_deleted = False
    unlink_mode = "LOCAL_PENDING"

    # An order that has physically left the warehouse must not silently become
    # editable again. A confirmed WB cancellation is the only terminal exception.
    if not cancelled and str(order.local_status or "").upper() in {"SENT", "DELIVERED"}:
        raise HTTPException(status_code=409, detail="ЧЗ нельзя отвязать: заказ уже отправлен со склада")

    if not pending_local_only:
        try:
            client.delete_meta(_wb_order_number(order), "sgtin")
            wb_deleted = True
            unlink_mode = "WB_CONFIRMED"
        except HTTPException as exc:
            # WBClient wraps marketplace errors as HTTP 502 while preserving the
            # original marketplace code in detail (for example "ошибка API 409").
            # Branch on the original WB status, not on the wrapper status.
            wb_error_status = _wb_api_error_status(exc)
            # WB refuses metadata edits on cancelled/terminal orders with 409.
            # Never treat a 409 on an active order as success: first refresh this
            # exact order and require cancellation truth from WB.
            if wb_error_status != 409:
                raise
            refreshed = _refresh_exact_wb_status_for_label(db, client, order)
            cancelled = bool(refreshed and _wb_order_currently_cancelled(order)) or (
                not refreshed and _wb_order_currently_cancelled(order)
            )
            if not cancelled:
                db.rollback()
                raise HTTPException(
                    status_code=409,
                    detail=f"Wildberries не разрешил отвязать ЧЗ у действующего заказа. Локальная привязка сохранена. {exc.detail}",
                ) from exc
            unlink_mode = "WB_CANCELLED_LOCAL_RELEASE"
            _event(db, order, "CHZ_WB_DELETE_409_CANCEL_CONFIRMED", value=old_chz, data={"wb_error": str(exc.detail or "")})
    else:
        _clear_pending_wb_chz(order)
        unlink_mode = "LOCAL_PENDING_CANCELLED" if cancelled else "LOCAL_PENDING_ACTIVE"
        _event(db, order, "CHZ_PENDING_REMOVED_LOCALLY", value=old_chz)

    # Do not allow an old WB sticker to print after CHZ correction/release.
    queued_jobs = db.execute(
        select(PrintJob).where(
            PrintJob.object_type == "MARKETPLACE_ORDER",
            PrintJob.object_id == str(order.id),
            PrintJob.document_type == "WB_ORDER_LABEL",
            PrintJob.status.in_(["QUEUED", "CLAIMED", "PROCESSING", "PRINTING", "RUNNING", "BROWSER_READY"]),
        )
    ).scalars().all()
    for job in queued_jobs:
        job.status = "CANCELLED"
        job.error_message = "Отменено: ЧЗ отвязан от заказа до печати"

    raw = dict(order.raw_data or {})
    raw.pop("_fc_chz_pending_wb", None)
    raw.pop("_fc_auto_wb_label_pending", None)
    raw.pop("_fc_auto_wb_label", None)
    raw.pop(_PACK_GATE_KEY, None)
    raw["_fc_last_chz_unlink"] = {
        "at": _now().isoformat(),
        "mode": unlink_mode,
        "cancelled": bool(cancelled),
        "wb_deleted": bool(wb_deleted),
    }
    order.raw_data = raw
    order.chz_code = None
    order.chz_attached_at = None
    order.label_generated_at = None

    if cancelled:
        # Cancellation is terminal truth. Never revive the order just because its
        # marking code was released. Physical write-off/receiving state is untouched.
        _event(db, order, "CHZ_UNLINKED_CANCELLED_ORDER", value=old_chz, data={"mode": unlink_mode})
        message = "Заказ отменён в Wildberries. ЧЗ отвязан локально и заказ остаётся отменённым."
    else:
        # Active correction flow: WB already accepted the delete (or the code had
        # never reached WB). Return only the packing workflow to PICKING.
        order.assembled_at = None
        order.packed_at = None
        _set_manual_sent_marker(order, False)
        _set_manual_workflow_status(order, None)
        setattr(order, "manual_status_override", False) if hasattr(order, "manual_status_override") else None
        order.local_status = "PICKING"
        _event(db, order, "CHZ_UNLINKED_ACTIVE_ORDER", value=old_chz, data={"mode": unlink_mode})
        message = "ЧЗ отвязан. Заказ возвращён на этап «На сборке» — отсканируйте правильный ЧЗ."

    _event(db, order, "CHZ_REMOVED", value=old_chz, data={"mode": unlink_mode, "cancelled": bool(cancelled)})
    db.commit()
    return {
        "ok": True,
        "cancelled": bool(cancelled),
        "wb_deleted": bool(wb_deleted),
        "unlink_mode": unlink_mode,
        "order": _order_response(db, order, cred=cred),
        "message": message,
    }


@router.get("/wb/shortage.xlsx")
def wb_shortage_xlsx(
    credential_id: uuid.UUID, warehouse_id: uuid.UUID, wb_warehouse_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Excel of active exact-SKU demand that is no longer covered by free local stock."""
    cred = _credential(db, credential_id, "WB")
    orders = db.execute(
        select(MarketplaceOrder).where(
            MarketplaceOrder.credential_id == credential_id,
            MarketplaceOrder.provider == "WB",
            MarketplaceOrder.local_status.in_(["NEW", "PICKING"]),
        ).order_by(MarketplaceOrder.external_created_at.asc().nulls_last(), MarketplaceOrder.created_at.asc())
    ).scalars().all()
    orders = [o for o in orders if _wb_visible(cred, o.wb_warehouse_id, warehouse_id) and (wb_warehouse_id is None or int(o.wb_warehouse_id or 0) == int(wb_warehouse_id))]
    by_sku: dict[str, dict[str, Any]] = {}
    for order in orders:
        if not order.local_sku_id:
            continue
        key = str(order.local_sku_id)
        row = by_sku.setdefault(key, {"sku_id": order.local_sku_id, "orders": [], "oldest": None})
        row["orders"].append(order)
        dt = order.external_created_at or order.created_at
        if dt and (row["oldest"] is None or dt < row["oldest"]):
            row["oldest"] = dt
    reserved = active_reserved_totals(db, client_id=cred.client_id, warehouse_id=warehouse_id, sku_ids=[x["sku_id"] for x in by_sku.values()])
    wb = Workbook(); ws = wb.active; ws.title = "Дефицит"
    headers=["Артикул","Размер","ШК","Активных заказов","Физический остаток","В резерве","Свободно","Дефицит","Самый старый заказ","Заказы"]
    ws.append(headers)
    for cell in ws[1]: cell.font=Font(bold=True); cell.alignment=Alignment(horizontal="center")
    written=0
    for item in by_sku.values():
        sku=db.get(SKU,item["sku_id"]);
        if not sku: continue
        balances=db.execute(select(InventoryBalance).where(InventoryBalance.client_id==cred.client_id,InventoryBalance.warehouse_id==warehouse_id,InventoryBalance.sku_id==sku.id,InventoryBalance.quantity>0)).scalars().all()
        physical=sum(float(x.quantity or 0) for x in balances)
        reserved_qty=sum(float(qty or 0) for (sid,_loc,_cont),qty in reserved.items() if sid==str(sku.id))
        free=max(0.0,physical-reserved_qty); demand=len(item["orders"]); shortage=max(0.0,demand-physical)
        if shortage <= 0: continue
        order_nums=", ".join(str(o.external_order_num or o.external_order_id) for o in item["orders"][:100])
        ws.append([sku.article or sku.sku_code or "",sku.size or "",sku.barcode or "",demand,physical,reserved_qty,free,shortage,item["oldest"].isoformat() if item["oldest"] else "",order_nums])
        written+=1
    widths=[22,14,26,18,20,14,14,14,24,70]
    for i,w in enumerate(widths,1): ws.column_dimensions[get_column_letter(i)].width=w
    ws.freeze_panes="A2"; ws.auto_filter.ref=ws.dimensions
    out=BytesIO(); wb.save(out)
    return Response(content=out.getvalue(),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="WB_shortage_{datetime.now().date().isoformat()}.xlsx"',"X-Shortage-Positions":str(written)})


@router.get("/wb/orders/{order_id}")
def wb_order_detail(
    order_id: uuid.UUID, credential_id: uuid.UUID, warehouse_id: uuid.UUID | None = Query(default=None),
    refresh_live: bool = Query(default=False), db: Session = Depends(get_db),
):
    cred = _credential(db, credential_id, "WB")
    order = _order_lookup(db, order_id, credential_id)
    # 0.9.4: opening a card is local/instant by default. The previous synchronous WB
    # round-trip made every status card wait several seconds. Background reconciliation
    # remains authoritative; an explicit refresh_live=1 can still request a one-order read.
    if refresh_live:
        try:
            if order.external_order_num is not None:
                live = WBClient(decrypt_secret(cred.encrypted_api_key)).order_statuses([int(order.external_order_num)])
                live_status = _wb_status_row_for_order(live, order)
                if live_status is not None:
                    _apply_status(db, order, live_status)
                    db.commit()
        except Exception:
            db.rollback()
            order = _order_lookup(db, order_id, credential_id)
    if not _wb_visible(cred, order.wb_warehouse_id, warehouse_id):
        raise HTTPException(status_code=404, detail="Заказ скрыт настройками склада WB")
    data = _order_response(db, order, warehouse_id, cred)
    data["chz_code"] = order.chz_code.replace("\x1d", "␝") if order.chz_code else None
    events = db.execute(
        select(MarketplaceOrderEvent)
        .where(MarketplaceOrderEvent.marketplace_order_id == order.id)
        .order_by(MarketplaceOrderEvent.created_at.desc())
        .limit(50)
    ).scalars().all()
    data["events"] = [
        {"type": e.event_type, "value": (e.value_text or "").replace("\x1d", "␝"), "data": e.data or {}, "created_at": e.created_at}
        for e in events
    ]
    return data


def _release_chz_after_confirmed_cancelled_label_failure(
    db: Session,
    client: WBClient,
    order: MarketplaceOrder,
    *,
    source: str,
) -> dict[str, Any] | None:
    """Release SGTIN only when a fresh WB status proves the order is cancelled.

    This is intentionally narrower than the manual CHZ delete endpoint. It is called
    only from WB order-label generation after that generation failed as cancelled.
    Stock/packing facts are left untouched: 0.7.9.2 remains authoritative that a
    cancellation after PACKED stays written off. We only free the marking code so the
    same physical code can be attached to another active WB assembly order.
    """
    old_chz = str(order.chz_code or "")
    if not old_chz:
        return None

    # Never free a code based on a stale local CANCELLED bucket. The exact one-order
    # WB status read must succeed right now and must still say cancelled.
    if not _refresh_exact_wb_status_for_label(db, client, order):
        return None
    if not _wb_order_currently_cancelled(order):
        db.commit()
        return None

    attached_in_wb: bool | None = None
    meta_error: str | None = None
    try:
        meta_item = _wb_order_meta_item(client, order)
        attached_in_wb = None if meta_item is None else bool(_wb_sgtin_state(meta_item, old_chz).get("attached"))
    except HTTPException as exc:
        meta_error = str(exc.detail or "")

    delete_called = False
    if attached_in_wb is not False:
        # Metadata read can be unavailable for an already-cancelled projection. In
        # that case DELETE is still the authoritative operation WB documents for SGTIN.
        try:
            client.delete_meta(_wb_order_number(order), "sgtin")
            delete_called = True
        except HTTPException as exc:
            _event(
                db,
                order,
                "CHZ_AUTO_RELEASE_CANCELLED_LABEL_FAILED",
                value=old_chz,
                data={
                    "source": source,
                    "reason": "WB_META_DELETE_FAILED",
                    "error": str(exc.detail or ""),
                    "meta_read_error": meta_error,
                },
            )
            db.commit()
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "WB_ORDER_CANCELLED_CHZ_RELEASE_FAILED",
                    "message": (
                        "Заказ WB отменён, поэтому этикетка не печатается. "
                        "Но Wildberries не подтвердил удаление ЧЗ — код оставлен привязанным, "
                        "чтобы не использовать его повторно ошибочно. Откройте заказ и повторите удаление ЧЗ."
                    ),
                    "chz_released": False,
                    "order_id": str(order.id),
                    "external_order_id": str(order.external_order_id),
                },
            ) from exc

    # If DELETE returned 204, that response is authoritative. Read-back is only an
    # extra safety check; a transient metadata GET failure must not undo a successful
    # deletion. If read-back succeeds and still exposes the exact code, keep it locked.
    if delete_called:
        try:
            state = _wb_sgtin_state(_wb_order_meta_item(client, order), old_chz)
            if state.get("attached"):
                _event(
                    db,
                    order,
                    "CHZ_AUTO_RELEASE_CANCELLED_LABEL_FAILED",
                    value=old_chz,
                    data={"source": source, "reason": "WB_META_STILL_ATTACHED_AFTER_DELETE"},
                )
                db.commit()
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "WB_ORDER_CANCELLED_CHZ_RELEASE_FAILED",
                        "message": (
                            "Заказ WB отменён, этикетка не печатается. Wildberries ответил на удаление ЧЗ, "
                            "но код всё ещё виден в метаданных заказа. FulfillCore не освободил его для повторного использования."
                        ),
                        "chz_released": False,
                        "order_id": str(order.id),
                        "external_order_id": str(order.external_order_id),
                    },
                )
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else None
            if detail and detail.get("code") == "WB_ORDER_CANCELLED_CHZ_RELEASE_FAILED":
                raise
            # DELETE already succeeded; an unavailable read-back is not a reason to
            # keep a locally stale duplicate binding.

    # Cancel any not-yet-printed label for the cancelled order before freeing the code.
    queued_jobs = db.execute(
        select(PrintJob).where(
            PrintJob.object_type == "MARKETPLACE_ORDER",
            PrintJob.object_id == str(order.id),
            PrintJob.document_type == "WB_ORDER_LABEL",
            PrintJob.status == "QUEUED",
        )
    ).scalars().all()
    for job in queued_jobs:
        job.status = "CANCELLED"
        job.error_message = "Отменено: WB подтвердил отмену заказа до печати; ЧЗ освобождён"

    _clear_pending_wb_chz(order)
    raw = dict(order.raw_data or {})
    raw.pop("_fc_auto_wb_label", None)
    raw["_fc_cancelled_label_chz_release"] = {
        "released_at": _now().isoformat(),
        "source": source,
        "wb_delete_called": delete_called,
        "wb_meta_was_attached": attached_in_wb,
    }
    order.raw_data = raw
    order.chz_code = None
    order.chz_attached_at = None
    order.label_generated_at = None
    # Do NOT clear packed_at/assembled_at and do NOT restore stock. A PACKED
    # cancellation remains written off exactly as required by 0.7.9.2.
    _event(
        db,
        order,
        "CHZ_AUTO_RELEASED_CANCELLED_BEFORE_LABEL",
        value=old_chz,
        data={
            "source": source,
            "stock_rule": "PACKED_STAYS_WRITTEN_OFF",
            "wb_delete_called": delete_called,
            "wb_meta_was_attached": attached_in_wb,
        },
    )
    db.commit()
    return {
        "released": True,
        "order_id": str(order.id),
        "external_order_id": str(order.external_order_id),
    }


def _cancelled_label_chz_released_exception(order: MarketplaceOrder) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "WB_ORDER_CANCELLED_CHZ_RELEASED",
            "message": (
                f"Заказ WB №{order.external_order_id} отменён, поэтому этикетка не печатается. "
                "ЧЗ автоматически отвязан от отменённого заказа и теперь его можно отсканировать для другого заказа. "
                "Остаток по отменённому PACKED-заказу не восстановлен."
            ),
            "chz_released": True,
            "order_id": str(order.id),
            "external_order_id": str(order.external_order_id),
        },
    )


def _fetch_wb_order_sticker_for_print_with_cancel_release(
    db: Session,
    client: WBClient,
    order: MarketplaceOrder,
    *,
    source: str,
    fast_auto: bool = False,
) -> tuple[bytes | None, dict[str, Any] | None]:
    """Fetch a WB label; on confirmed cancellation release only its CHZ binding.

    ``fast_auto`` is used only by the automatic post-CHZ Print Agent path. It avoids
    repeating expensive status recovery that was already completed by scan-chz.
    """
    try:
        if fast_auto:
            return _fetch_wb_order_sticker_fast_auto(db, client, order)
        return _fetch_wb_order_sticker_for_print(db, client, order)
    except HTTPException as exc:
        is_cancel_label_error = (
            exc.status_code == 409
            and ("отмен" in str(exc.detail or "").lower() or _wb_order_is_cancelled_for_label(order))
        )
        if not is_cancel_label_error:
            raise
        released = _release_chz_after_confirmed_cancelled_label_failure(
            db, client, order, source=source
        )
        if released:
            raise _cancelled_label_chz_released_exception(order)
        # Exact WB refresh did not confirm cancellation right now. Never release the
        # code on an uncertain/stale status; preserve the original label error.
        raise


@router.post("/wb/orders/{order_id}/undo-writeoff")
def wb_undo_writeoff(order_id: uuid.UUID, credential_id: uuid.UUID, db: Session = Depends(get_db)):
    """Explicit operator action: restore consumed FBS stock and queue marketplace republish."""
    order = _order_lookup(db, order_id, credential_id)
    latest = latest_reservations_for_orders(db, [order.id]).get(str(order.id))
    if not latest:
        raise HTTPException(status_code=409, detail="У заказа нет резерва/списания для отмены")
    result = release_or_restore_marketplace_reservation(
        db, order=order, reservation=latest, restore_consumed=True, note="EXPLICIT_UNDO_WRITE_OFF"
    )
    db.commit()
    return {"ok": True, **result, "marketplace_stock_queued": True}


@router.get("/wb/orders/{order_id}/label.pdf")
def wb_order_label_pdf(order_id: uuid.UUID, credential_id: uuid.UUID, reprint: int = Query(default=0), width_mm: float = Query(default=58, ge=25, le=120), height_mm: float = Query(default=40, ge=25, le=120), rotate: int = Query(default=-1), scale_percent: float = Query(default=100, ge=85, le=108), offset_x_mm: float = Query(default=0, ge=-5, le=5), offset_y_mm: float = Query(default=0, ge=-5, le=5), db: Session = Depends(get_db)):
    _, client = _wb(db, credential_id)
    order = _order_lookup(db, order_id, credential_id)
    cred = _credential(db, credential_id, "WB")
    _assert_wb_work(cred, order)
    # Browser printing has a supplier-status guard before the sticker fetch. If WB
    # already projected the cancellation, run the same exact-status/CHZ-release path
    # here so browser mode behaves exactly like Print Agent mode.
    if order.chz_code and _wb_order_is_cancelled_for_label(order):
        released = _release_chz_after_confirmed_cancelled_label_failure(
            db, client, order, source="BROWSER_LABEL_PRECHECK"
        )
        if released:
            raise _cancelled_label_chz_released_exception(order)
    if order.supplier_status not in {"confirm", "complete"} and order.local_status != "PACKED":
        raise HTTPException(status_code=409, detail="WB выдаёт стикер только для заказов в confirm/complete.")
    if order.supplier_status == "confirm" and not order.chz_code:
        raise HTTPException(status_code=409, detail="Сначала закрепите код маркировки. Первичная этикетка заказа печатается только после кода маркировки.")
    if not reprint and _has_pending_wb_chz(order):
        raise HTTPException(status_code=409, detail="ЧЗ ещё не подтверждён Wildberries. Сначала добейтесь успешной привязки ЧЗ, затем печатайте первичную этикетку.")
    if not reprint and order.label_generated_at:
        # Normal UI path after CHZ already generated the sticker. This endpoint is now a reprint path.
        reprint = 1
    started_from_picking = order.local_status == "PICKING"
    image, _ = _fetch_wb_order_sticker_for_print_with_cancel_release(db, client, order, source="BROWSER_LABEL")
    if image is None:
        raise HTTPException(
            status_code=503,
            detail="WB пока не подготовил этикетку действующего заказа. FulfillCore уже повторил запрос; нажмите печать ещё раз через несколько секунд.",
        )
    pdf = png_pages_to_pdf([image], width_mm=width_mm, height_mm=height_mm, rotate=rotate,
                           scale_percent=scale_percent, offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm)
    order.label_generated_at = _now()
    order.label_print_count = (order.label_print_count or 0) + 1
    if order.chz_code and not _has_pending_wb_chz(order) and order.supplier_status in {"confirm", "complete"}:
        # Reprinting an already existing WB sticker is not a FulfillCore packing action.
        # PACKED is created only by scan-product -> scan-marking-code below.
        if order.product_scanned_at and order.local_status not in {"SENT", "DELIVERED", "CANCELLED"}:
            _consume_picking_reservation_nonblocking(db, order, "WB_PACKED_LABEL_RECONCILE")
            order.assembled_at = order.assembled_at or _now()
            order.packed_at = order.packed_at or _now()
            setattr(order, "manual_status_override", False) if hasattr(order, "manual_status_override") else None
            order.local_status = "PACKED"
        elif order.local_status not in {"DELIVERED", "CANCELLED", "PACKED"}:
            order.local_status = "SENT"
    _event(db, order, "ORDER_LABEL_GENERATED", data={"print_count": order.label_print_count})
    db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="WB-order-{order.external_order_id}.pdf"'},
    )


@router.get("/wb/orders/{order_id}/product-label.pdf")
def wb_product_label_pdf(
    order_id: uuid.UUID,
    credential_id: uuid.UUID,
    barcode: str | None = Query(default=None),
    width_mm: float = Query(default=58, ge=25, le=120),
    height_mm: float = Query(default=40, ge=25, le=120),
    rotate: int = Query(default=90),
    scale_percent: float = Query(default=100, ge=80, le=108),
    offset_x_mm: float = Query(default=0, ge=-5, le=5),
    offset_y_mm: float = Query(default=0, ge=-5, le=5),
    db: Session = Depends(get_db),
):
    cred = _credential(db, credential_id, "WB")
    order = _order_lookup(db, order_id, credential_id)
    _assert_wb_work(cred, order)
    sku = db.get(SKU, order.local_sku_id) if order.local_sku_id else None
    allowed = [str(x) for x in (order.barcodes or []) if x]
    selected = (barcode or (allowed[0] if allowed else None) or (sku.barcode if sku else None))
    if not selected:
        raise HTTPException(status_code=409, detail="У заказа WB нет штрихкода товара для печати")
    if barcode and allowed and barcode not in allowed:
        raise HTTPException(status_code=400, detail="Выбранный штрихкод не относится к этому заказу WB")
    product_card = _product_card_for_order(db, order)
    size_name = _wb_order_size(order, product_card, sku) or (sku.size if sku else None)
    pdf = product_barcode_label_pdf(
        selected, order.article or (sku.article if sku else None), size_name,
        width_mm=width_mm, height_mm=height_mm, rotate=rotate, scale_percent=scale_percent,
        offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm, **_wb_product_label_kwargs(product_card),
    )
    _event(db, order, "PRODUCT_RELABEL_GENERATED", value=selected)
    db.commit()
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="WB-product-{order.external_order_id}.pdf"'},
    )


# FC_PICKING_AGE_DISPLAY_09415C_20260903
def _wb_picking_age_text(age_hours: Any) -> str:
    """Format picking age exactly like the WB order board: N d N h / N h N min."""
    try:
        minutes = max(0, int(float(age_hours or 0) * 60))
    except (TypeError, ValueError):
        minutes = 0
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {mins} мин"
    return f"{mins} мин"


def _wb_refresh_picking_row_age(rows: list[dict[str, Any]], orders: list[MarketplaceOrder], now: datetime) -> None:
    """Attach current order age to every XLSX row, including historical PICKING rows.

    Historical rows were copied from the first picking list and could therefore have
    a missing/stale age. Recalculate from the live MarketplaceOrder just before export.
    """
    age_by_order: dict[str, float] = {}
    for order in orders:
        age = float(marketplace_order_age_hours(order, now))
        for key in (getattr(order, "external_order_id", None), getattr(order, "external_order_num", None)):
            if key is not None and str(key):
                age_by_order[str(key)] = age
    for row in rows:
        ages = [age_by_order[str(key)] for key in (row.get("order_ids") or []) if str(key) in age_by_order]
        if ages:
            age = max(ages)
        else:
            try:
                age = float(row.get("max_age_hours") or row.get("age_hours") or 0)
            except (TypeError, ValueError):
                age = 0.0
        row["age_hours"] = age
        row["max_age_hours"] = age
        row["age_text"] = _wb_picking_age_text(age)


def _wb_picking_xlsx_age_display(xlsx_bytes: bytes, rows: list[dict[str, Any]]) -> bytes:
    """Replace decimal/missing Age cells with the same human-readable text as the UI.

    The underlying picking-list generator remains untouched. This post-processing only
    edits the existing Age column, preserving allocation, quantities, statuses and styles.
    """
    try:
        book = load_workbook(BytesIO(xlsx_bytes))
        sheet = book.active
        header_row = None
        age_col = None
        barcode_col = None
        required_col = None
        status_col = None
        for row_no in range(1, min(int(sheet.max_row or 1), 60) + 1):
            for col_no in range(1, int(sheet.max_column or 1) + 1):
                value = str(sheet.cell(row_no, col_no).value or "").strip().lower().replace("ё", "е")
                compact = " ".join(value.split())
                if compact in {"возраст", "возраст заказа"}:
                    header_row, age_col = row_no, col_no
                elif compact in {"шк товара", "штрихкод товара"}:
                    barcode_col = col_no
                elif compact.startswith("собрать"):
                    required_col = col_no
                elif compact == "статус":
                    status_col = col_no
            if header_row is not None and age_col is not None:
                break
        if header_row is None or age_col is None:
            return xlsx_bytes

        sheet.cell(header_row, age_col).value = "Возраст заказа"
        candidates: list[int] = []
        check_cols = [c for c in (barcode_col, required_col, status_col) if c]
        for row_no in range(header_row + 1, int(sheet.max_row or header_row) + 1):
            if check_cols:
                if not any(sheet.cell(row_no, col_no).value not in (None, "") for col_no in check_cols):
                    continue
            else:
                if not any(sheet.cell(row_no, col_no).value not in (None, "") for col_no in range(1, int(sheet.max_column or 1) + 1)):
                    continue
            candidates.append(row_no)

        # picking_list_xlsx writes one spreadsheet data row per prepared picking row.
        # Do not touch any footer rows if the workbook contains extra information.
        for index, item in enumerate(rows):
            if index >= len(candidates):
                break
            row_no = candidates[index]
            cell = sheet.cell(row_no, age_col)
            cell.value = str(item.get("age_text") or _wb_picking_age_text(item.get("max_age_hours") or item.get("age_hours") or 0))
            cell.number_format = "@"
            cell.alignment = Alignment(
                horizontal=cell.alignment.horizontal or "center",
                vertical="center",
                wrap_text=True,
            )

        current_width = sheet.column_dimensions[get_column_letter(age_col)].width or 0
        if current_width < 16:
            sheet.column_dimensions[get_column_letter(age_col)].width = 16
        output = BytesIO()
        book.save(output)
        return output.getvalue()
    except Exception:
        logger.exception("FC_PICKING_AGE_XLSX_FORMAT_FAILED")
        return xlsx_bytes



# FC_K9R5_PICKING_CELL_TIME_LAYOUT
_PICKING_LAYOUTS = {"CELL", "TIME"}


def _wb_picking_layout(value: Any) -> str:
    mode = str(value or "CELL").strip().upper()
    if mode not in _PICKING_LAYOUTS:
        raise HTTPException(status_code=400, detail="Формат сборочного листа: CELL (по ячейкам) или TIME (по времени заказа)")
    return mode


def _wb_natural_sort_key(value: Any) -> tuple[Any, ...]:
    """Natural warehouse sort: 2-10-3 follows 2-9-20 instead of string order."""
    text_value = str(value or "").strip().lower()
    parts = re.split(r"(\d+)", text_value)
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts if part != "")


def _wb_row_required_quantity(row: dict[str, Any]) -> float:
    for key in ("required_quantity", "required", "quantity"):
        try:
            value = float(row.get(key) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    ids = [x for x in (row.get("order_ids") or []) if str(x).strip()]
    return float(len(ids) or 1)


def _wb_group_picking_rows_by_cell(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse identical SKU picks from one cell/box into one warehouse instruction.

    Allocation itself is already complete before this function runs.  We only merge
    display rows that point at the exact same physical bucket and exact product identity.
    Therefore three one-unit WB orders for one size in one box become one Excel row
    ``Собрать: 3`` while reservations still remain one durable unit per order.
    """
    grouped: dict[tuple[str, ...], dict[str, Any]] = {}
    order_seen: dict[tuple[str, ...], set[str]] = {}
    for source in rows or []:
        row = dict(source)
        status = str(row.get("status") or "").strip().upper()
        location_key = str(row.get("location_id") or row.get("location") or "").strip()
        container_key = str(
            row.get("container_id") or row.get("container_barcode") or row.get("container") or ""
        ).strip()
        product_key = str(row.get("barcode") or "").strip()
        article_key = str(row.get("article") or "").strip().lower()
        size_key = str(row.get("size") or "").strip().lower()
        # Status is part of the key: shortage/error rows can never be merged into OK stock.
        key = (location_key, container_key, product_key, article_key, size_key, status)
        required = _wb_row_required_quantity(row)
        ids = [str(x).strip() for x in (row.get("order_ids") or []) if str(x).strip()]
        if key not in grouped:
            item = row
            item["order_ids"] = []
            item["required_quantity"] = 0.0
            item["required"] = 0.0
            # The Excel generator treats quantity as the requested pick quantity for OK
            # rows.  Shortage rows keep zero physical quantity but still show required.
            item["quantity"] = 0.0
            item["available_quantity"] = float(row.get("available_quantity", row.get("available", 0)) or 0)
            item["available"] = item["available_quantity"]
            item["container_total_quantity"] = float(row.get("container_total_quantity") or 0)
            item["age_hours"] = float(row.get("age_hours") or 0)
            item["max_age_hours"] = float(row.get("max_age_hours") or row.get("age_hours") or 0)
            item["sla_critical"] = bool(row.get("sla_critical"))
            item["grouped_by_cell"] = True
            grouped[key] = item
            order_seen[key] = set()
        item = grouped[key]
        item["required_quantity"] = float(item.get("required_quantity") or 0) + required
        item["required"] = float(item.get("required") or 0) + required
        if status == "OK":
            item["quantity"] = float(item.get("quantity") or 0) + required
        item["available_quantity"] = max(float(item.get("available_quantity") or 0), float(row.get("available_quantity", row.get("available", 0)) or 0))
        item["available"] = item["available_quantity"]
        item["container_total_quantity"] = max(float(item.get("container_total_quantity") or 0), float(row.get("container_total_quantity") or 0))
        item["age_hours"] = max(float(item.get("age_hours") or 0), float(row.get("age_hours") or 0))
        item["max_age_hours"] = max(float(item.get("max_age_hours") or 0), float(row.get("max_age_hours") or row.get("age_hours") or 0))
        item["sla_critical"] = bool(item.get("sla_critical") or row.get("sla_critical"))
        item["under_zero"] = bool(item.get("under_zero") or row.get("under_zero"))
        item["synthetic_shortage"] = bool(item.get("synthetic_shortage") or row.get("synthetic_shortage"))
        for oid in ids:
            if oid not in order_seen[key]:
                order_seen[key].add(oid)
                item["order_ids"].append(oid)
    return list(grouped.values())


def _wb_expand_picking_rows_by_order(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return one Excel row per order so chronological mode is strictly chronological."""
    result: list[dict[str, Any]] = []
    for source in rows or []:
        ids = [str(x).strip() for x in (source.get("order_ids") or []) if str(x).strip()]
        if not ids:
            result.append(dict(source))
            continue
        total_required = _wb_row_required_quantity(source)
        share = total_required / max(1, len(ids))
        for oid in ids:
            row = dict(source)
            row["order_ids"] = [oid]
            row["required_quantity"] = share
            row["required"] = share
            if str(row.get("status") or "").strip().upper() == "OK":
                row["quantity"] = share
            row["grouped_by_cell"] = False
            result.append(row)
    return result


def _wb_picking_order_rank(orders: list[MarketplaceOrder]) -> dict[str, int]:
    def order_timestamp(order: MarketplaceOrder) -> float:
        value = order.external_created_at or order.created_at
        if value is None:
            return float("inf")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return float(value.timestamp())

    ordered = sorted(
        orders,
        key=lambda o: (
            order_timestamp(o),
            str(o.external_order_num or o.external_order_id or o.id),
        ),
    )
    ranks: dict[str, int] = {}
    for index, order in enumerate(ordered):
        for value in (order.external_order_id, order.external_order_num):
            if value is not None and str(value).strip():
                ranks[str(value).strip()] = index
    return ranks


def _wb_prepare_picking_rows(
    rows: list[dict[str, Any]], *, orders: list[MarketplaceOrder], now: datetime, layout: str
) -> list[dict[str, Any]]:
    """In-memory presentation only: no SQL, no reservations and no stock mutation."""
    mode = _wb_picking_layout(layout)
    if mode == "TIME":
        prepared = _wb_expand_picking_rows_by_order(rows)
        _wb_refresh_picking_row_age(prepared, orders, now)
        ranks = _wb_picking_order_rank(orders)
        prepared.sort(key=lambda row: (
            min((ranks.get(str(x).strip(), 10**12) for x in (row.get("order_ids") or [])), default=10**12),
            str(row.get("article") or ""), str(row.get("size") or ""),
        ))
        return prepared

    # CELL is the warehouse walking route: aggregate one product/size per exact cell+box,
    # then natural-sort addresses so the picker does not run back and forth.
    aged = [dict(row) for row in (rows or [])]
    _wb_refresh_picking_row_age(aged, orders, now)
    prepared = _wb_group_picking_rows_by_cell(aged)
    prepared.sort(key=lambda row: (
        1 if str(row.get("status") or "").strip().upper() != "OK" else 0,
        _wb_natural_sort_key(row.get("location")),
        _wb_natural_sort_key(row.get("container") or row.get("container_barcode")),
        _wb_natural_sort_key(row.get("article")),
        _wb_natural_sort_key(row.get("size")),
        str(row.get("barcode") or ""),
    ))
    return prepared


def _wb_unallocated_order_count(rows: list[dict[str, Any]]) -> int:
    ids: set[str] = set()
    anonymous = 0
    for row in rows or []:
        if str(row.get("status") or "").strip().upper() == "OK":
            continue
        row_ids = [str(x).strip() for x in (row.get("order_ids") or []) if str(x).strip()]
        if row_ids:
            ids.update(row_ids)
        else:
            anonymous += max(1, int(round(_wb_row_required_quantity(row))))
    return len(ids) + anonymous



# FC_K9R15_PRECOMPUTED_PICKING_PLAN_CACHE
# The read-only allocator is already proven to finish for the full scope.  Cache that
# exact plan once and let bounded commit requests reuse it, instead of repeating global
# SKU/card/inventory scans for every 75-order batch.
_PICKING_PLAN_CACHE: dict[str, dict[str, Any]] = {}
_PICKING_PLAN_CACHE_LOCK = threading.RLock()
_PICKING_PLAN_CACHE_TTL_SECONDS = 600.0


def _picking_plan_cache_put(*, client_id: uuid.UUID, warehouse_id: uuid.UUID | None,
                            planned_skus: dict[str, SKU], planned_meta: dict[str, dict[str, Any]],
                            allocation_rows: list[dict[str, Any]]) -> str:
    token = uuid.uuid4().hex
    payload = {
        "expires": time.monotonic() + _PICKING_PLAN_CACHE_TTL_SECONDS,
        "client_id": str(client_id),
        "warehouse_id": str(warehouse_id or ""),
        "planned_sku_ids": {str(k): str(v.id) for k, v in planned_skus.items()},
        "planned_meta": {str(k): dict(v or {}) for k, v in planned_meta.items()},
        "allocation_rows": [dict(row or {}) for row in allocation_rows],
    }
    now_mono = time.monotonic()
    with _PICKING_PLAN_CACHE_LOCK:
        for old_token, old in list(_PICKING_PLAN_CACHE.items()):
            if float(old.get("expires") or 0) <= now_mono:
                _PICKING_PLAN_CACHE.pop(old_token, None)
        _PICKING_PLAN_CACHE[token] = payload
    return token


def _picking_plan_cache_get(token: str | None, *, client_id: uuid.UUID,
                            warehouse_id: uuid.UUID | None) -> dict[str, Any] | None:
    key = str(token or "").strip()
    if not key:
        return None
    now_mono = time.monotonic()
    with _PICKING_PLAN_CACHE_LOCK:
        cached = _PICKING_PLAN_CACHE.get(key)
        if not cached:
            return None
        if float(cached.get("expires") or 0) <= now_mono:
            _PICKING_PLAN_CACHE.pop(key, None)
            return None
        if cached.get("client_id") != str(client_id) or cached.get("warehouse_id") != str(warehouse_id or ""):
            return None
        return cached


def _picking_plan_rows_for_orders(cached_rows: list[dict[str, Any]], orders: list[MarketplaceOrder]) -> list[dict[str, Any]]:
    wanted = {str(o.external_order_id or "") for o in orders}
    result: list[dict[str, Any]] = []
    for source in cached_rows or []:
        source_ids = [str(x) for x in (source.get("order_ids") or [])]
        kept = [x for x in source_ids if x in wanted]
        if not kept:
            continue
        row = dict(source)
        row["order_ids"] = kept
        qty = float(len(kept))
        for field in ("required_quantity", "quantity", "required"):
            row[field] = qty
        result.append(row)
    return result


@router.post("/wb/picking-lists.xlsx")
def wb_create_picking_list(
    payload: WBPickingListRequest,
    scope: str = Query(default="SELECTED"),
    move_to_picking: bool = Query(default=False),
    commit_only: bool = Query(default=False),
    plan_only: bool = Query(default=False),
    plan_token: str | None = Query(default=None),
    layout: str = Query(default="CELL"),
    wb_warehouse_id: int | None = Query(default=None),
    chz_state: str | None = Query(default=None),
    label: str | None = Query(default=None),
    brand: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Create a picking XLSX from selected orders or the complete status scope.

    Excel generation and workflow transition are deliberately independent. The
    safe default only downloads the document. NEW orders become PICKING solely
    when the operator explicitly confirms ``move_to_picking=true``.
    """
    cred, _ = _wb(db, payload.credential_id)
    normalized_scope = str(scope or "SELECTED").strip().upper()
    normalized_layout = _wb_picking_layout(layout)
    scope_statuses = {
        "NEW": {"NEW"},
        "PICKING": {"PICKING"},
        "ACTIVE": {"NEW", "PICKING"},
    }
    if normalized_scope not in {"SELECTED", *scope_statuses}:
        raise HTTPException(status_code=400, detail="Доступный объём листа: SELECTED, NEW, PICKING или ACTIVE")

    logical_ids = _logical_credential_ids(db, cred)
    if normalized_scope == "SELECTED":
        requested_ids = list(dict.fromkeys(int(x) for x in payload.order_ids))
        if not requested_ids:
            raise HTTPException(status_code=400, detail="Не выбраны заказы для сборочного листа")
        stmt = select(MarketplaceOrder).where(
            MarketplaceOrder.credential_id.in_(logical_ids),
            MarketplaceOrder.provider == "WB",
            MarketplaceOrder.external_order_num.in_(requested_ids),
        )
        orders = db.execute(stmt).scalars().all()
        by_num = {o.external_order_num: o for o in orders}
        missing = [oid for oid in requested_ids if oid not in by_num]
        if missing:
            raise HTTPException(status_code=404, detail=f"Не найдены локальные заказы WB: {missing[:10]}")
    else:
        selected_wb_warehouse_id = _wb_selected_warehouse_id(cred, wb_warehouse_id, payload.warehouse_id)
        if selected_wb_warehouse_id is None:
            raise HTTPException(status_code=409, detail="Сначала выберите рабочий склад WB")
        stmt = select(MarketplaceOrder).where(
            MarketplaceOrder.credential_id.in_(logical_ids),
            MarketplaceOrder.provider == "WB",
            MarketplaceOrder.wb_warehouse_id == selected_wb_warehouse_id,
            MarketplaceOrder.local_status.in_(scope_statuses[normalized_scope]),
        )
        selected_brands = _normalize_wb_brand_filters(brand)
        if selected_brands:
            stmt = stmt.where(MarketplaceOrder.article.in_(_wb_brand_article_subquery(cred.client_id, selected_brands)))
        normalized_chz_state = str(chz_state or "").strip().upper()
        if normalized_chz_state == "MISSING":
            stmt = stmt.where(or_(MarketplaceOrder.chz_code.is_(None), MarketplaceOrder.chz_code == ""))
        elif normalized_chz_state == "ASSIGNED":
            stmt = stmt.where(MarketplaceOrder.chz_code.is_not(None), MarketplaceOrder.chz_code != "")
        if label and label.strip():
            stmt = stmt.where(cast(MarketplaceOrder.raw_data, Text).ilike(f"%{label.strip()}%"))
        orders = db.execute(stmt.order_by(
            MarketplaceOrder.external_created_at.asc().nullslast(),
            MarketplaceOrder.created_at.asc(),
            MarketplaceOrder.external_order_num.asc(),
        )).scalars().all()

    # 0.7.9.25: orders already in PICKING are allowed in a new/consolidated sheet.
    # Their first real picking list already committed stock, so the new sheet reuses
    # the historical address only for printing and never writes the same unit off again.
    # Manual PICKING orders without a real list are handled as first-time allocations below.
    already_in_list = [o for o in orders if o.last_picking_list_id]

    if not orders:
        raise HTTPException(status_code=409, detail="В выбранном объёме нет заказов для сборочного листа")
    if move_to_picking and not payload.warehouse_id:
        raise HTTPException(status_code=409, detail="Для перевода заказов «На сборку» сначала выберите физический склад FulfillCore")
    bad = [o.external_order_id for o in orders if o.local_status not in {"NEW", "PICKING"}]
    if bad:
        raise HTTPException(status_code=409, detail=f"Сборочный лист можно делать только из «Новых» и «На сборке». Недопустимые заказы: {bad[:10]}")

    watch = [o.external_order_id for o in orders if _wb_mode(cred, o.wb_warehouse_id, payload.warehouse_id) != "WORK"]
    if watch:
        raise HTTPException(status_code=409, detail=f"Заказы склада в режиме «Наблюдение» нельзя включать в сборочный лист: {watch[:10]}")

    now = _now()
    # 0.9.0 SLA: hard priority 300+ hours, then strict oldest -> newest.
    orders.sort(key=lambda o: (0 if marketplace_order_age_hours(o, now) >= 300 else 1, -marketplace_order_age_hours(o, now), str(o.id)))
    title = (payload.name or f"Сборка WB {now.astimezone().strftime('%d.%m.%Y %H:%M')}").strip()
    supply_ids: list[str] = sorted({str(o.supply_id) for o in orders if o.supply_id})
    new_before_commit = sum(1 for o in orders if str(o.local_status or "").upper() == "NEW")
    download_order_ids: list[int] = []
    for order in orders:
        value = order.external_order_num if order.external_order_num is not None else order.external_order_id
        try:
            download_order_ids.append(int(value))
        except (TypeError, ValueError):
            pass

    # FC_K9R15_PREPARE_ONCE_REUSE_BATCHES
    # Preview without status changes already proves that one full read-only allocation is
    # acceptable. Do that exact work ONCE, cache the plain allocation/SKU plan for ten
    # minutes, and make every commit batch reuse it. This removes the repeated global
    # card + SKU + inventory scans that made the first r14 75-order batch exceed 120s.
    if plan_only:
        if not payload.warehouse_id:
            raise HTTPException(status_code=409, detail="Для подготовки сборочного листа выберите физический склад FulfillCore")
        ensure_stock_reservation_schema(db)
        prepared_plan, prepared_meta = _resolve_picking_sku_plan(
            db, orders, payload.warehouse_id, cred.client_id
        )
        prepared_rows = _readonly_picking_rows(
            db, orders=orders, warehouse_id=payload.warehouse_id, cred=cred, now=now,
            planned_skus=prepared_plan, planned_meta=prepared_meta,
        )
        for row in prepared_rows:
            row.pop("reprint_read_only", None)
        token = _picking_plan_cache_put(
            client_id=cred.client_id, warehouse_id=payload.warehouse_id,
            planned_skus=prepared_plan, planned_meta=prepared_meta, allocation_rows=prepared_rows,
        )
        prepared_display_rows = _wb_prepare_picking_rows(
            prepared_rows, orders=orders, now=now, layout=normalized_layout
        )
        prepared_unallocated = _wb_unallocated_order_count(prepared_display_rows)
        db.rollback()
        return {
            "ok": True,
            "order_ids": download_order_ids,
            "order_count": len(download_order_ids),
            "new_count": new_before_commit,
            "scope": normalized_scope,
            "layout": normalized_layout,
            "plan_token": token,
            "unallocated": prepared_unallocated,
        }

    # FC_K9_READ_ONLY_PICKING_PREVIEW_REPRINT
    # Every request with move_to_picking=false is a true Excel-only operation: no
    # MarketplacePickingList insert, no reservation mutation, no order-status update and
    # no write lock. This makes «На сборке → Excel» a fast deterministic reprint and also
    # preserves the user's original preview contract for NEW/ACTIVE.
    if not move_to_picking:
        allocation_rows = _readonly_picking_rows(db, orders=orders, warehouse_id=payload.warehouse_id, cred=cred, now=now)
        rows = _wb_prepare_picking_rows(allocation_rows, orders=orders, now=now, layout=normalized_layout)
        stamp = now.astimezone().strftime('%Y%m%d_%H%M%S')
        layout_tag = 'CELL' if normalized_layout == 'CELL' else 'TIME'
        sheet_number = f"FC-WB-PREVIEW-{layout_tag}-{stamp}"
        file_name = f"WB_picking_{layout_tag.lower()}_{stamp}.xlsx"
        display_title = f"{title} · {'По ячейкам / коробам' if normalized_layout == 'CELL' else 'По времени заказа'}"
        xlsx = picking_list_xlsx(title=display_title,created_at=now,rows=rows,supply_ids=supply_ids,order_count=len(orders),sheet_number=sheet_number)
        xlsx = _wb_picking_xlsx_age_display(xlsx, rows)
        return Response(
            content=xlsx,
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={
                'Content-Disposition': f'attachment; filename="{file_name}"',
                'X-Picking-List-Order-Count': str(len(orders)),
                'X-Picking-List-Scope': normalized_scope,
                'X-Picking-List-Moved-To-Picking': '0',
                'X-Picking-List-Read-Only': '1',
                'X-Picking-List-Layout': normalized_layout,
                'X-Picking-List-Unallocated': str(_wb_unallocated_order_count(rows)),
            },
        )

    # 0.7.8.0: the picking-list button is a LOCAL FulfillCore action only.
    # It must never create a WB supply, add orders to a supply, or modify the WB
    # supplier status. This keeps Excel generation independent from the current
    # marketplace state (cancelled/complete/already handled by another WMS) and
    # matches the warehouse workflow: Excel preview first, optional local PICKING
    # transition only after a separate operator confirmation.
    # Existing WB supply ids are kept only as informational columns in the file.

    # 0.7.9.1: allocation + reservation + local status change is serialized, but the
    # expensive read-only planning must happen BEFORE any write transaction is opened.
    # InventoryBalance remains physical stock; ACTIVE reservations are subtracted only
    # from the free quantity offered to new picking lists.
    # FC_K9R15_REUSE_PRECOMPUTED_PLAN_IN_COMMIT_BATCH
    # If the browser supplied a valid plan token, this batch performs ZERO global alias /
    # inventory planning. It only slices the already-proven read-only plan to this batch,
    # then enters the existing short lock/bind/status transaction. A missing/expired token
    # safely falls back to the r10 path for backward compatibility.
    cached_plan = _picking_plan_cache_get(
        plan_token, client_id=cred.client_id, warehouse_id=payload.warehouse_id
    )
    fast_cached_batch = cached_plan is not None
    ensure_stock_reservation_schema(db)
    if cached_plan is not None:
        current_internal_ids = {str(o.id) for o in orders}
        planned_sku_ids = {
            key: uuid.UUID(value)
            for key, value in (cached_plan.get("planned_sku_ids") or {}).items()
            if key in current_internal_ids
        }
        write_meta = {
            key: dict(value or {})
            for key, value in (cached_plan.get("planned_meta") or {}).items()
            if key in current_internal_ids
        }
        allocation_rows = _picking_plan_rows_for_orders(
            cached_plan.get("allocation_rows") or [], orders
        )
        for row in allocation_rows:
            row.pop("reprint_read_only", None)
        historic_order_keys = {
            str(order_id)
            for row in allocation_rows
            if row.get("reused_from_picking_list_id")
            for order_id in (row.get("order_ids") or [])
        }
        ordered_order_ids = [o.id for o in orders]
        first_real_order_ids = {
            o.id for o in orders
            if str(o.external_order_id or "") not in historic_order_keys
        }
        rows = _wb_prepare_picking_rows(allocation_rows, orders=orders, now=now, layout=normalized_layout)
        client_id_value = cred.client_id
        order_write_lock_key = _wb_order_write_lock_key(cred)
        lock_key = f"picking-stock:{client_id_value}:{payload.warehouse_id}"
        db.rollback()
    else:
        # Backward-compatible r10 preparation for direct callers without a plan token.
        write_plan, write_meta = _resolve_picking_sku_plan(
            db, orders, payload.warehouse_id, cred.client_id
        )
        allocation_rows = _readonly_picking_rows(
            db, orders=orders, warehouse_id=payload.warehouse_id, cred=cred, now=now,
            planned_skus=write_plan, planned_meta=write_meta,
        )
        for row in allocation_rows:
            row.pop("reprint_read_only", None)
        historic_order_keys = {
            str(order_id)
            for row in allocation_rows
            if row.get("reused_from_picking_list_id")
            for order_id in (row.get("order_ids") or [])
        }
        ordered_order_ids = [o.id for o in orders]
        first_real_order_ids = {
            o.id for o in orders
            if str(o.external_order_id or "") not in historic_order_keys
        }
        planned_sku_ids = {key: sku.id for key, sku in write_plan.items()}
        rows = _wb_prepare_picking_rows(allocation_rows, orders=orders, now=now, layout=normalized_layout)
        client_id_value = cred.client_id
        order_write_lock_key = _wb_order_write_lock_key(cred)
        lock_key = f"picking-stock:{client_id_value}:{payload.warehouse_id}"
        db.rollback()

    serialize_picking_write = True
    try:
        # FC_K9R16_NO_WAIT_HOT_PICKING_LOCKS
        # Cached commit batches are already deterministic and only mutate their own order/
        # reservation rows. Do not wait behind the global WB/process stock lock: a stale or
        # concurrent worker must fail fast instead of freezing the operator at 75/N.
        process_ctx = nullcontext() if fast_cached_batch else (process_lock(order_write_lock_key) if serialize_picking_write else nullcontext())
        with process_ctx:
            with distributed_lock(db, lock_key, blocking=not fast_cached_batch) as stock_lock_acquired:
                if fast_cached_batch and not stock_lock_acquired:
                    raise HTTPException(status_code=409, detail="Склад занят короткой операцией остатков. Повторяемая пачка не изменена — запустите ещё раз через несколько секунд.")
                # Re-read and lock the target orders only now.  This starts the short write
                # transaction after all CPU-heavy planning has finished.  Preserve the
                # original picking priority order in memory.
                locked_rows = db.execute(
                    select(MarketplaceOrder)
                    .where(MarketplaceOrder.id.in_(sorted(set(ordered_order_ids), key=str)))
                    .order_by(MarketplaceOrder.id.asc())
                    .with_for_update(nowait=fast_cached_batch)
                ).scalars().all()
                locked_by_id = {o.id: o for o in locked_rows}
                missing_locked = [oid for oid in ordered_order_ids if oid not in locked_by_id]
                if missing_locked:
                    raise HTTPException(
                        status_code=409,
                        detail="Часть заказов изменилась во время подготовки сборочного листа. Повторите операцию.",
                    )
                orders = [locked_by_id[oid] for oid in ordered_order_ids]

                # Revalidate the only state that matters for this local transition.  WB sync
                # may have changed an order while the read-only plan was being calculated.
                invalid_now = [
                    o.external_order_id for o in orders
                    if str(o.local_status or '').upper() not in {'NEW', 'PICKING'}
                ]
                if invalid_now:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Заказы изменили статус во время подготовки: {invalid_now[:10]}. Повторите сборочный лист.",
                    )

                # Reload only the already-precomputed SKU identities in one compact query so
                # no expired ORM objects from the closed read transaction are dereferenced.
                unique_sku_ids = sorted(set(planned_sku_ids.values()), key=str)
                sku_map = {}
                if unique_sku_ids:
                    sku_map = {str(s.id): s for s in db.execute(
                        select(SKU).where(SKU.id.in_(unique_sku_ids))
                    ).scalars().all()}
                locked_plan = {
                    order_key: sku_map[str(sku_id)]
                    for order_key, sku_id in planned_sku_ids.items()
                    if str(sku_id) in sku_map
                }
                _repair_unmatched_picking_skus_from_inventory(
                    db, orders, payload.warehouse_id, client_id_value,
                    resolved_plan=locked_plan, resolved_meta=write_meta,
                )
                first_real_list_orders = [o for o in orders if o.id in first_real_order_ids]
                plist = MarketplacePickingList(
                    credential_id=payload.credential_id,
                    client_id=client_id_value,
                    warehouse_id=payload.warehouse_id,
                    name=title,
                    file_name="pending.xlsx",
                    order_ids=[str(o.external_order_num or o.external_order_id) for o in orders],
                    rows=rows,
                    supply_ids=supply_ids,
                    created_by=payload.created_by,
                )
                db.add(plist)
                db.flush()
                layout_tag = 'CELL' if normalized_layout == 'CELL' else 'TIME'
                sheet_number = f"FC-WB-{layout_tag}-{str(plist.id).split('-')[0].upper()}"
                file_name = f"WB_picking_{layout_tag.lower()}_{str(plist.id).split('-')[0].upper()}.xlsx"
                plist.file_name = file_name

                reservation_result = {"created": 0, "kept": 0, "already_consumed": 0}
                # A PICKING order without a usable physical assignment must bind stock now,
                # even if it has an old picking-list id. Orders with valid bindings were
                # removed into historic_rows above and are never reserved again. NEW orders
                # keep preview-vs-move confirmation and commit only on move_to_picking=true.
                commit_orders = [
                    o for o in first_real_list_orders
                    if o.local_status == "PICKING" or (o.local_status == "NEW" and move_to_picking)
                ]
                if commit_orders:
                    reservation_rows = [row for row in allocation_rows if not row.get("synthetic_shortage")]
                    reservation_order_keys = {
                        str(order_id)
                        for row in reservation_rows
                        for order_id in (row.get("order_ids") or [])
                    }
                    reservation_orders = [
                        order for order in commit_orders
                        if str(order.external_order_id or "") in reservation_order_keys
                    ]
                    if reservation_orders:
                        reservation_result = reserve_orders_from_picking_rows(
                            db, orders=reservation_orders, picking_list=plist, rows=reservation_rows,
                            precomputed_bind_only=fast_cached_batch,
                        )

                # Reservation may mark an allocation as shortage after a concurrent stock
                # check. Rebuild only the presentation rows in memory; no extra DB reads.
                rows = _wb_prepare_picking_rows(allocation_rows, orders=orders, now=now, layout=normalized_layout)
                plist.rows = rows

                allocation_by_order: dict[str, dict[str, Any]] = {}
                for row in allocation_rows:
                    for order_id in row.get("order_ids") or []:
                        allocation_by_order[str(order_id)] = row

                # FC_K9R18_SKIP_UNHEALED_LEGACY_ORDERS
                # Missing legacy reservations that cannot be safely recovered stay NEW.
                # The successful orders in the same batch still commit.
                skipped_internal_ids = {str(x) for x in (reservation_result.get("skipped_order_ids") or [])}
                if skipped_internal_ids:
                    commit_orders = [o for o in commit_orders if str(o.id) not in skipped_internal_ids]

                # FC_K9R61_PICKING_STATUS_CANONICAL_GUARD
                # A generated list is an explicit workflow action. Older builds changed only
                # local_status/picking_list columns, leaving a stale raw_data._fc_manual_status
                # (commonly NEW after a prior manual reset). The next WB background reconcile
                # then trusted that stale marker and pushed the order straight back to NEW.
                # Keep the fast set-based write, but update the durable manual workflow marker
                # for the bounded commit batch as well. This also heals logical credential
                # siblings returned by the SELECTED batch without touching stock a second time.
                previous_status_by_id = {order.id: str(order.local_status or "").upper() for order in orders}
                allocation_ok_ids = {
                    order.id for order in commit_orders
                    if str((allocation_by_order.get(str(order.external_order_id or "")) or {}).get("status") or "").upper() == "OK"
                    and str(order.id) not in skipped_internal_ids
                }
                status_attach_orders = [
                    order for order in orders
                    if (previous_status_by_id.get(order.id) == "PICKING")
                    or (move_to_picking and previous_status_by_id.get(order.id) == "NEW" and order.id in allocation_ok_ids)
                ]
                attach_ids = sorted({order.id for order in status_attach_orders}, key=str)
                if attach_ids:
                    db.execute(
                        update(MarketplaceOrder)
                        .where(MarketplaceOrder.id.in_(attach_ids))
                        .values(
                            picking_list_generated_at=now,
                            last_picking_list_id=str(plist.id),
                            local_status="PICKING",
                            updated_at=now,
                        )
                    )
                    for order in status_attach_orders:
                        _set_manual_workflow_status(order, "PICKING")
                        order.local_status = "PICKING"
                        order.picking_list_generated_at = now
                        order.last_picking_list_id = str(plist.id)
                        if hasattr(order, "manual_status_override"):
                            order.manual_status_override = True

                for order in orders:
                    previous = previous_status_by_id.get(order.id, str(order.local_status or "").upper())
                    historic_reprint = bool(order.last_picking_list_id and previous == "PICKING")
                    should_attach_new_list = order in status_attach_orders

                    allocation = allocation_by_order.get(str(order.external_order_id or "")) or {}
                    allocated = str(allocation.get("status") or "").upper() == "OK"
                    # Keep the old preview behaviour: merely downloading a NEW preview does
                    # not alter order history. A historical PICKING rebuild and each verified
                    # real PICKING attachment are auditable events. Shortage NEW rows stay NEW
                    # and do not get a misleading PICKING_LIST_GENERATED event.
                    if historic_reprint or should_attach_new_list:
                        _event(db, order, "PICKING_LIST_REPRINTED" if historic_reprint else "PICKING_LIST_GENERATED", value=str(plist.id), data={
                            "file_name": file_name,
                            "from": previous,
                            "moved_to_picking": previous == "NEW" and move_to_picking,
                            "wb_changed": False,
                            "stock_reserved": allocated and should_attach_new_list,
                            "stock_address_bound_at_picking_list": allocated and should_attach_new_list,
                            "historical_allocation_reused": historic_reprint,
                            "stock_reserved_again": False if historic_reprint else allocated and should_attach_new_list,
                            "unallocated_no_local_stock": not allocated,
                        })

                # Verify the same logical lineage the order board reads before we tell the
                # browser that the batch was committed. A stale NEW sibling must fail inside
                # this transaction rather than make the Excel succeed with unchanged tabs.
                committed_numbers = sorted({
                    int(order.external_order_num if order.external_order_num is not None else order.external_order_id)
                    for order in status_attach_orders
                    if str(order.external_order_num if order.external_order_num is not None else order.external_order_id).isdigit()
                })
                if committed_numbers:
                    db.flush()
                    verify_logical_ids = _logical_credential_ids(db, cred)
                    remaining_new = db.execute(select(
                        MarketplaceOrder.external_order_num, MarketplaceOrder.external_order_id
                    ).where(
                        MarketplaceOrder.credential_id.in_(verify_logical_ids),
                        MarketplaceOrder.provider == "WB",
                        or_(
                            MarketplaceOrder.external_order_num.in_(committed_numbers),
                            MarketplaceOrder.external_order_id.in_([str(x) for x in committed_numbers]),
                        ),
                        MarketplaceOrder.local_status == "NEW",
                    )).all()
                    if remaining_new:
                        sample = [str(num or ext) for num, ext in remaining_new[:10]]
                        raise HTTPException(status_code=409, detail=f"Не все копии заказа перешли в «На сборке»: {sample}")

                # FC_K9R8_COMMIT_THEN_READONLY_EXCEL
                # The UI now commits the short reservation/status transaction first and
                # downloads Excel through the proven read-only path afterwards.  Keeping
                # XLSX generation outside the write request removes a large CPU/lock window
                # and makes a download failure safely repeatable without a second status write.
                if not commit_only:
                    display_title = f"{title} · {'По ячейкам / коробам' if normalized_layout == 'CELL' else 'По времени заказа'}"
                    xlsx = picking_list_xlsx(
                        title=display_title,
                        created_at=now,
                        rows=rows,
                        supply_ids=supply_ids,
                        order_count=len(orders),
                        sheet_number=sheet_number,
                    )
                    xlsx = _wb_picking_xlsx_age_display(xlsx, rows)
                db.commit()
    except Exception as exc:
        db.rollback()
        if fast_cached_batch:
            msg = str(exc).lower()
            if "could not obtain lock" in msg or "lock not available" in msg or "lock timeout" in msg:
                raise HTTPException(
                    status_code=409,
                    detail="Пачка не изменена: один из заказов/резервов сейчас обновляется параллельно. Повторите через несколько секунд.",
                ) from exc
        raise

    if commit_only:
        return {
            "ok": True,
            "picking_list_id": str(plist.id),
            "order_ids": committed_numbers,
            "skipped_order_ids": sorted({
                int(o.external_order_num if o.external_order_num is not None else o.external_order_id)
                for o in orders
                if previous_status_by_id.get(o.id) == "NEW"
                and str(o.external_order_num if o.external_order_num is not None else o.external_order_id).isdigit()
                and int(o.external_order_num if o.external_order_num is not None else o.external_order_id) not in set(committed_numbers)
            }),
            "order_count": len(orders),
            "moved_new": len({
                int(o.external_order_num if o.external_order_num is not None else o.external_order_id)
                for o in status_attach_orders
                if previous_status_by_id.get(o.id) == "NEW"
                and str(o.external_order_num if o.external_order_num is not None else o.external_order_id).isdigit()
            }) if move_to_picking else 0,
            "verified_picking": len(committed_numbers),
            "reservations_created": int(reservation_result.get("created", 0)),
            "reservations_fast_bound": int(reservation_result.get("fast_bound", 0)),
            "reservations_recovered": int(reservation_result.get("recovered_missing_sale_reserved", 0)),
            "unallocated": _wb_unallocated_order_count(rows),
            "layout": normalized_layout,
        }

    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "X-Picking-List-Id": str(plist.id),
            "X-Picking-List-Order-Count": str(len(orders)),
            "X-Picking-List-Scope": normalized_scope,
            "X-Picking-List-Moved-To-Picking": "1" if move_to_picking else "0",
            "X-Picking-List-Layout": normalized_layout,
            "X-Picking-List-Reservations-Created": str(reservation_result.get("created", 0)),
            "X-Picking-List-Unallocated": str(_wb_unallocated_order_count(rows)),
        },
    )


@router.get("/wb/receiving-queue")
def wb_receiving_queue(
    client_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Items physically waiting in virtual ПРИЕМКА after WB picking/packing cancellation."""
    return receiving_queue(db, client_id=client_id, warehouse_id=warehouse_id)


@router.get("/wb/picking-lists")
def wb_picking_lists(credential_id: uuid.UUID, limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_db)):
    _credential(db, credential_id, "WB")
    lists = db.execute(
        select(MarketplacePickingList)
        .where(MarketplacePickingList.credential_id == credential_id)
        .order_by(MarketplacePickingList.created_at.desc())
        .limit(limit)
    ).scalars().all()
    return {"lists": [{
        "id": str(x.id), "name": x.name, "file_name": x.file_name,
        "number": f"FC-WB-{str(x.id).split('-')[0].upper()}",
        "order_count": len(x.order_ids or []), "supply_ids": x.supply_ids or [],
        "created_by": x.created_by, "created_at": x.created_at,
    } for x in lists]}


# 0.9.4.9d FAST LABEL PREFETCH
# Start asking WB for the order sticker as soon as the product barcode selects an order.
# The request runs DB-free and caches only in process memory, so by the time CHZ is scanned
# the physical label can usually be queued immediately. Nothing is persisted until the
# CHZ belongs to the order and the normal auto-label worker verifies that ownership.
_AUTO_LABEL_PREFETCH_LOCK = threading.Lock()
_AUTO_LABEL_PREFETCH_CACHE: dict[tuple[str, int], dict[str, Any]] = {}
_AUTO_LABEL_PREFETCH_INFLIGHT: set[tuple[str, int]] = set()
_AUTO_LABEL_PREFETCH_TTL_SECONDS = 180.0
_AUTO_LABEL_PREFETCH_MAX_ENTRIES = 64
_AUTO_LABEL_PREFETCH_MAX_BYTES = 16 * 1024 * 1024


def _prune_auto_label_prefetch_cache(now_mono: float | None = None) -> None:
    """Bound cached WB sticker bytes by age, count and total payload size."""
    now_mono = time.monotonic() if now_mono is None else now_mono
    stale = [k for k, row in _AUTO_LABEL_PREFETCH_CACHE.items() if now_mono - float((row or {}).get("at") or 0.0) >= _AUTO_LABEL_PREFETCH_TTL_SECONDS]
    for key in stale:
        _AUTO_LABEL_PREFETCH_CACHE.pop(key, None)
    def payload_bytes(row):
        image = (row or {}).get("image")
        item = (row or {}).get("item") or {}
        encoded = item.get("file") if isinstance(item, dict) else None
        # WB item usually keeps the base64 copy in addition to decoded image bytes.
        return (len(image) if isinstance(image, (bytes, bytearray)) else 0) + (len(encoded) if isinstance(encoded, (str, bytes)) else 0)
    while len(_AUTO_LABEL_PREFETCH_CACHE) > _AUTO_LABEL_PREFETCH_MAX_ENTRIES or sum(payload_bytes(v) for v in _AUTO_LABEL_PREFETCH_CACHE.values()) > _AUTO_LABEL_PREFETCH_MAX_BYTES:
        if not _AUTO_LABEL_PREFETCH_CACHE: break
        victim = min(_AUTO_LABEL_PREFETCH_CACHE, key=lambda k: float((_AUTO_LABEL_PREFETCH_CACHE.get(k) or {}).get("at") or 0.0))
        _AUTO_LABEL_PREFETCH_CACHE.pop(victim, None)

# 0.9.4.7 DB TX AUTO LABEL STABILITY
# Automatic WB label generation must never hold a SQLAlchemy transaction while WB is
# slow. The browser may ask once and continue packing; this worker owns the bounded WB
# wait and opens PostgreSQL only after a real sticker is already in memory.
_AUTO_LABEL_BG_LOCK = threading.Lock()
_AUTO_LABEL_BG_INFLIGHT: set[tuple[str, str, str]] = set()
_AUTO_LABEL_BG_SLOTS = threading.BoundedSemaphore(4)


def _wb_sticker_bytes_without_db(client: WBClient, order_number: int) -> tuple[bytes | None, dict[str, Any] | None]:
    items = client.order_stickers([int(order_number)])
    item = next(
        (
            x for x in (items or [])
            if str(x.get("orderId") or x.get("id") or x.get("order_id") or "") == str(int(order_number))
        ),
        None,
    )
    if not item and len(items or []) == 1:
        item = (items or [None])[0]
    if not item:
        return None, None
    raw_file = item.get("file")
    if not raw_file:
        return None, item
    try:
        encoded = str(raw_file).strip()
        if encoded.lower().startswith("data:") and "," in encoded:
            encoded = encoded.split(",", 1)[1]
        missing_padding = len(encoded) % 4
        if missing_padding:
            encoded += "=" * (4 - missing_padding)
        decoded = base64.b64decode(encoded)
        return (decoded if decoded else None), item
    except Exception:
        return None, item


def _prefetch_wb_label_background(client: WBClient, order_number: int, *, credential_id=None) -> bool:
    """Warm the WB sticker before CHZ is scanned, without touching PostgreSQL."""
    if credential_id is None:
        return False
    number = int(order_number)
    cache_key = (str(credential_id), number)
    now_mono = time.monotonic()
    with _AUTO_LABEL_PREFETCH_LOCK:
        _prune_auto_label_prefetch_cache(now_mono)
        cached = _AUTO_LABEL_PREFETCH_CACHE.get(cache_key) or {}
        if cached.get("image") is not None and now_mono - float(cached.get("at") or 0.0) < 180.0:
            return False
        if cache_key in _AUTO_LABEL_PREFETCH_INFLIGHT:
            return False
        if len(_AUTO_LABEL_PREFETCH_INFLIGHT) >= 4:
            return False
        _AUTO_LABEL_PREFETCH_INFLIGHT.add(cache_key)

    def _run() -> None:
        try:
            # Product scan normally gives the operator 0.5-3 s before CHZ. Use that time.
            # These are short retries; no SQLAlchemy session exists here.
            for pause in (0.0, 0.10, 0.18, 0.28, 0.42, 0.65, 0.95, 1.35):
                if pause:
                    time.sleep(pause)
                try:
                    image, item = _wb_sticker_bytes_without_db(client, number)
                except Exception:
                    continue
                if image is not None:
                    with _AUTO_LABEL_PREFETCH_LOCK:
                        _AUTO_LABEL_PREFETCH_CACHE[cache_key] = {"image": image, "item": item, "at": time.monotonic()}
                        _prune_auto_label_prefetch_cache()
                    logger.info("FC_LABEL_PREFETCH_OK order_num=%s bytes=%s", number, len(image))
                    return
        finally:
            with _AUTO_LABEL_PREFETCH_LOCK:
                _AUTO_LABEL_PREFETCH_INFLIGHT.discard(cache_key)

    try:
        threading.Thread(target=_run, name=f"fc-label-prefetch-{number}", daemon=True).start()
    except Exception:
        with _AUTO_LABEL_PREFETCH_LOCK:
            _AUTO_LABEL_PREFETCH_INFLIGHT.discard(cache_key)
        logger.exception("FC_LABEL_PREFETCH_START_FAILED")
        return False
    return True


def _prefetched_wb_label(order_number: int, *, credential_id=None) -> tuple[bytes | None, dict[str, Any] | None]:
    if credential_id is None:
        return None, None
    number = (str(credential_id), int(order_number))
    with _AUTO_LABEL_PREFETCH_LOCK:
        _prune_auto_label_prefetch_cache()
        cached = _AUTO_LABEL_PREFETCH_CACHE.get(number) or {}
        if cached.get("image") is None:
            return None, None
        if time.monotonic() - float(cached.get("at") or 0.0) >= 180.0:
            _AUTO_LABEL_PREFETCH_CACHE.pop(number, None)
            return None, None
        return cached.get("image"), cached.get("item")


def _schedule_wb_auto_label_background(
    *,
    client: WBClient,
    order_id: uuid.UUID,
    credential_id: uuid.UUID,
    order_number: int,
    expected_chz: str,
    station_code: str,
    device_id: str | None = None,
    width_mm: float,
    height_mm: float,
    rotate: int,
    scale_percent: float,
    offset_x_mm: float,
    offset_y_mm: float,
) -> bool:
    station_code = normalize_station_code(station_code)
    key = (str(order_id), str(station_code or ""), str(expected_chz or ""))
    with _AUTO_LABEL_BG_LOCK:
        if key in _AUTO_LABEL_BG_INFLIGHT:
            logger.info("FC_AUTO_LABEL_SKIP order_id=%s station=%s reason=inflight", order_id, station_code)
            return False
        _AUTO_LABEL_BG_INFLIGHT.add(key)

    def _run() -> None:
        started = time.perf_counter()
        acquired = False
        image: bytes | None = None
        item: dict[str, Any] | None = None
        last_error: Exception | None = None
        queued_ok = False
        queued_job_id: str | None = None
        try:
            # Waiting for a semaphore and for WB consumes no DB connection.
            acquired = _AUTO_LABEL_BG_SLOTS.acquire(timeout=120.0)
            if not acquired:
                logger.warning("FC_AUTO_LABEL_FAIL order_id=%s station=%s reason=worker_busy", order_id, station_code)
                return

            # First consume the sticker warmed during product scan. In the normal scanner
            # flow this removes the WB wait completely after CHZ. If WB had not projected
            # it yet, retry aggressively at the front and then keep a bounded slow tail.
            image, item = _prefetched_wb_label(int(order_number), credential_id=credential_id)
            delays = (() if image is not None else (0.0, 0.08, 0.12, 0.18, 0.25, 0.35, 0.50, 0.75, 1.0, 1.4, 2.0, 3.0, 4.5, 6.0, 8.0))
            for pause in delays:
                if pause:
                    time.sleep(pause)
                try:
                    image, item = _wb_sticker_bytes_without_db(client, int(order_number))
                    if image is not None:
                        break
                except HTTPException as exc:
                    last_error = exc
                    status = _wb_api_error_status(exc)
                    if status in {400, 401, 402, 403}:
                        break
                except Exception as exc:
                    last_error = exc

            if image is None:
                logger.warning(
                    "FC_AUTO_LABEL_FAIL order_id=%s order_num=%s station=%s elapsed_ms=%.1f error=%s",
                    order_id, order_number, station_code, (time.perf_counter()-started)*1000.0,
                    last_error or "WB sticker not projected",
                )
                return

            # WB work is finished. Open a fresh, short DB transaction only to verify the
            # order still owns the same CHZ and to enqueue one physical print job.
            gen = get_db()
            db2 = next(gen)
            try:
                order = _order_lookup(db2, order_id, credential_id)
                if str(order.chz_code or "") != str(expected_chz or ""):
                    logger.warning("FC_AUTO_LABEL_DROP order_id=%s station=%s reason=chz_changed", order_id, station_code)
                    db2.rollback()
                    return
                if _wb_order_hard_cancelled_for_packing(order):
                    logger.warning("FC_AUTO_LABEL_DROP order_id=%s station=%s reason=order_cancelled", order_id, station_code)
                    db2.rollback()
                    return

                raw = dict(order.raw_data or {})
                marker = raw.get("_fc_auto_wb_label") if isinstance(raw.get("_fc_auto_wb_label"), dict) else {}
                marker_at = _parse_dt(marker.get("queued_at"))
                recent = False
                if marker_at is not None:
                    if marker_at.tzinfo is None:
                        marker_at = marker_at.replace(tzinfo=timezone.utc)
                    recent = (_now() - marker_at).total_seconds() < 300
                if recent and marker.get("chz") == order.chz_code and str(marker.get("station") or "") == str(station_code or ""):
                    db2.rollback()
                    return

                if item is not None:
                    _apply_wb_order_sticker(db2, order, item)
                png = wb_sticker_exact_png(
                    image, width_mm=width_mm, height_mm=height_mm, rotate=rotate,
                    scale_percent=scale_percent, offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm,
                )
                active_jobs = db2.execute(
                    select(PrintJob)
                    .where(
                        PrintJob.object_type == "MARKETPLACE_ORDER",
                        PrintJob.object_id == str(order.id),
                        PrintJob.document_type == "WB_ORDER_LABEL",
                        PrintJob.status.in_(["QUEUED", "CLAIMED", "PROCESSING", "PRINTING", "RUNNING"]),
                    )
                    .order_by(PrintJob.created_at.desc())
                    .limit(10)
                ).scalars().all()
                job = next(
                    (j for j in active_jobs if normalize_station_code(getattr(j, "station_code", None)) == station_code),
                    None,
                )
                if job is None:
                    job = queue_print_job(
                        db2, station_code=station_code, payload=png,
                        file_name=f"WB-order-{order.external_order_id}.png", document_type="WB_ORDER_LABEL",
                        client_id=order.client_id, object_type="MARKETPLACE_ORDER", object_id=str(order.id),
                    )
                    db2.flush()
                    older = db2.execute(select(PrintJob).where(
                        PrintJob.object_type == "MARKETPLACE_ORDER",
                        PrintJob.object_id == str(order.id),
                        PrintJob.document_type == "WB_ORDER_LABEL",
                        PrintJob.status == "QUEUED",
                        PrintJob.id != job.id,
                    )).scalars().all()
                    for old_job in older:
                        old_job.status = "CANCELLED"
                        old_job.error_message = "Отменено 0.9.4.8 REV2: новое задание уже создано"
                order.label_generated_at = order.label_generated_at or _now()

                raw = dict(order.raw_data or {})
                raw.pop("_fc_auto_wb_label_pending", None)
                raw["_fc_auto_wb_label"] = {
                    "chz": order.chz_code,
                    "station": station_code,
                    "print_job_id": str(job.id),
                    "queued_at": _now().isoformat(),
                }
                gate = raw.get(_PACK_GATE_KEY) if isinstance(raw.get(_PACK_GATE_KEY), dict) else {}
                gate = dict(gate)
                gate.update({
                    "stage": "PRINT_QUEUED",
                    "message": f"Этикетка получена. Печатаем на {station_code}…",
                    "print_job_id": str(job.id),
                    "station": station_code,
                    "chz": order.chz_code,
                    "wb_chz_verified": bool(gate.get("wb_chz_verified")),
                    "updated_at": _now().isoformat(),
                })
                raw[_PACK_GATE_KEY] = gate
                order.raw_data = raw
                _event(db2, order, "ORDER_LABEL_QUEUED", data={
                    "print_job_id": str(job.id), "station": station_code,
                    "auto_after_chz": True, "source": "BG_NO_DB_WAIT_0_9_4_7",
                })
                db2.commit()
                queued_ok = True
                queued_job_id = str(job.id)
                logger.info(
                    "FC_AUTO_LABEL_OK order_id=%s order_num=%s station=%s job_id=%s elapsed_ms=%.1f",
                    order_id, order_number, station_code, job.id, (time.perf_counter()-started)*1000.0,
                )
            except Exception:
                try:
                    db2.rollback()
                except Exception:
                    pass
                logger.exception("FC_AUTO_LABEL_DB_FAIL order_id=%s station=%s", order_id, station_code)
            finally:
                try:
                    gen.close()
                except Exception:
                    pass

            if queued_job_id:
                _wait_strict_pack_print_ack(
                    order_id=order_id, credential_id=credential_id, expected_chz=expected_chz,
                    print_job_id=queued_job_id, station_code=station_code, device_id=device_id,
                )
        finally:
            try:
                if not queued_ok:
                    gen_cleanup = get_db()
                    db_cleanup = next(gen_cleanup)
                    try:
                        order_cleanup = _order_lookup(db_cleanup, order_id, credential_id)
                        raw_cleanup = dict(order_cleanup.raw_data or {})
                        pending_cleanup = raw_cleanup.get("_fc_auto_wb_label_pending") if isinstance(raw_cleanup.get("_fc_auto_wb_label_pending"), dict) else {}
                        if (
                            str(pending_cleanup.get("chz") or "") == str(expected_chz or "")
                            and normalize_station_code(pending_cleanup.get("station")) == station_code
                        ):
                            raw_cleanup.pop("_fc_auto_wb_label_pending", None)
                            raw_cleanup["_fc_auto_wb_label_last_error"] = {
                                "chz": expected_chz, "station": station_code, "at": _now().isoformat(),
                                "error": str(last_error or "background worker ended without print job"),
                            }
                            order_cleanup.raw_data = raw_cleanup
                            db_cleanup.commit()
                        else:
                            db_cleanup.rollback()
                    except Exception:
                        try: db_cleanup.rollback()
                        except Exception: pass
                        logger.exception("FC_AUTO_LABEL_PENDING_CLEANUP_FAIL order_id=%s", order_id)
                    finally:
                        try: gen_cleanup.close()
                        except Exception: pass
            finally:
                if acquired:
                    try:
                        _AUTO_LABEL_BG_SLOTS.release()
                    except Exception:
                        pass
                with _AUTO_LABEL_BG_LOCK:
                    _AUTO_LABEL_BG_INFLIGHT.discard(key)

    threading.Thread(target=_run, name=f"fc-label-{str(order_id)[:8]}", daemon=True).start()
    return True


@router.post("/wb/orders/{order_id}/label/queue")
def wb_queue_order_label(order_id: uuid.UUID, payload: WBQueuePrintRequest, width_mm: float = Query(default=58, ge=25, le=120), height_mm: float = Query(default=40, ge=25, le=120), rotate: int = Query(default=-1), scale_percent: float = Query(default=100, ge=85, le=108), offset_x_mm: float = Query(default=0, ge=-5, le=5), offset_y_mm: float = Query(default=0, ge=-5, le=5), auto_after_chz: int = Query(default=0, ge=0, le=1), db: Session = Depends(get_db)):
    _, client = _wb(db, payload.credential_id)
    order = _order_lookup(db, order_id, payload.credential_id)
    if not order.chz_code:
        raise HTTPException(status_code=409, detail="Этикетка заказа WB печатается после закрепления кода маркировки")

    raw = dict(order.raw_data or {})
    if auto_after_chz:
        marker = raw.get("_fc_auto_wb_label") if isinstance(raw.get("_fc_auto_wb_label"), dict) else {}
        marker_chz = marker.get("chz")
        marker_station = marker.get("station")
        marker_at = _parse_dt(marker.get("queued_at"))
        recent = False
        if marker_at is not None:
            if marker_at.tzinfo is None:
                marker_at = marker_at.replace(tzinfo=timezone.utc)
            recent = (_now() - marker_at).total_seconds() < 300
        if recent and marker_chz == order.chz_code and str(marker_station or "") == str(payload.print_station or ""):
            return {"ok": True, "deduplicated": True, "print_job_id": marker.get("print_job_id"), "order": _order_response(db, order)}

        pending = raw.get("_fc_auto_wb_label_pending") if isinstance(raw.get("_fc_auto_wb_label_pending"), dict) else {}
        pending_at = _parse_dt(pending.get("scheduled_at"))
        pending_recent = False
        if pending_at is not None:
            if pending_at.tzinfo is None:
                pending_at = pending_at.replace(tzinfo=timezone.utc)
            pending_recent = (_now() - pending_at).total_seconds() < 120
        if pending_recent and pending.get("chz") == order.chz_code and str(pending.get("station") or "") == str(payload.print_station or ""):
            # No WB call and no wait: an existing background worker owns this label.
            db.rollback()
            return {"ok": True, "background_pending": True, "print_job_id": None}

        # Snapshot immutable values while the session is local, then COMMIT BEFORE any
        # WB HTTP. This is the transaction barrier missing in 0.9.4.6.
        order_number = _wb_order_number(order)
        expected_chz = str(order.chz_code or "")
        station = normalize_station_code(payload.print_station)
        raw = dict(order.raw_data or {})
        raw["_fc_auto_wb_label_pending"] = {
            "chz": expected_chz,
            "station": station,
            "scheduled_at": _now().isoformat(),
        }
        order.raw_data = raw
        db.commit()

        scheduled = _schedule_wb_auto_label_background(
            client=client,
            order_id=order_id,
            credential_id=payload.credential_id,
            order_number=order_number,
            expected_chz=expected_chz,
            station_code=station,
            device_id=None,
            width_mm=width_mm,
            height_mm=height_mm,
            rotate=rotate,
            scale_percent=scale_percent,
            offset_x_mm=offset_x_mm,
            offset_y_mm=offset_y_mm,
        )
        return {
            "ok": True,
            "background_pending": True,
            "background_scheduled": bool(scheduled),
            "print_job_id": None,
            "message": "Этикетка WB будет автоматически поставлена в печать сразу после готовности WB.",
        }

    # Manual reprint keeps the established conservative logic. It is an operator action,
    # not the high-volume automatic packing path.
    started_from_picking = order.local_status == "PICKING"
    image, _ = _fetch_wb_order_sticker_for_print_with_cancel_release(
        db, client, order, source="PRINT_AGENT_LABEL", fast_auto=False
    )
    if image is None:
        raise HTTPException(
            status_code=503,
            detail="WB пока не подготовил этикетку действующего заказа. Нажмите печать ещё раз через несколько секунд.",
        )
    png = wb_sticker_exact_png(
        image, width_mm=width_mm, height_mm=height_mm, rotate=rotate,
        scale_percent=scale_percent, offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm,
    )
    job = queue_print_job(
        db, station_code=normalize_station_code(payload.print_station), payload=png,
        file_name=f"WB-order-{order.external_order_id}.png", document_type="WB_ORDER_LABEL",
        client_id=order.client_id, object_type="MARKETPLACE_ORDER", object_id=str(order.id),
    )
    order.label_generated_at = order.label_generated_at or _now()
    order.label_print_count = (order.label_print_count or 0) + 1
    if order.product_scanned_at:
        order.packed_at = order.packed_at or _now()
        if order.local_status not in {"SENT", "DELIVERED", "CANCELLED"}:
            _consume_picking_reservation_nonblocking(db, order, "WB_PACKED_PRINT_QUEUE_RECONCILE")
            setattr(order, "manual_status_override", False) if hasattr(order, "manual_status_override") else None
            order.local_status = "PACKED"
    elif order.local_status not in {"DELIVERED", "CANCELLED", "PACKED"}:
        order.local_status = "SENT"
    _event(db, order, "ORDER_LABEL_QUEUED", data={"print_job_id": str(job.id), "station": payload.print_station, "auto_after_chz": False})
    db.commit()
    return {"ok": True, "print_job_id": str(job.id), "order": _order_response(db, order)}




@router.post("/wb/orders/{order_id}/product-label/queue")
def wb_queue_product_label(order_id: uuid.UUID, payload: WBQueuePrintRequest, width_mm: float = Query(default=58, ge=25, le=120), height_mm: float = Query(default=40, ge=25, le=120), rotate: int = Query(default=90), scale_percent: float = Query(default=100, ge=80, le=108), offset_x_mm: float = Query(default=0, ge=-5, le=5), offset_y_mm: float = Query(default=0, ge=-5, le=5), db: Session = Depends(get_db)):
    _credential(db, payload.credential_id, "WB")
    order = _order_lookup(db, order_id, payload.credential_id)
    sku = db.get(SKU, order.local_sku_id) if order.local_sku_id else None
    allowed = [str(x) for x in (order.barcodes or []) if x]
    selected = payload.barcode or (allowed[0] if allowed else None) or (sku.barcode if sku else None)
    if not selected:
        raise HTTPException(status_code=409, detail="У заказа WB нет штрихкода товара")
    product_card = _product_card_for_order(db, order)
    size_name = _wb_order_size(order, product_card, sku) or (sku.size if sku else None)
    png = product_barcode_label_png(
        selected, order.article or (sku.article if sku else None), size_name,
        width_mm=width_mm, height_mm=height_mm, scale_percent=scale_percent,
        offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm, **_wb_product_label_kwargs(product_card),
    )
    job = queue_print_job(
        db, station_code=normalize_station_code(payload.print_station), payload=png,
        file_name=f"WB-product-{order.external_order_id}.png", document_type="WB_PRODUCT_LABEL",
        client_id=order.client_id, object_type="MARKETPLACE_ORDER", object_id=str(order.id),
    )
    _event(db, order, "PRODUCT_RELABEL_QUEUED", value=selected, data={"print_job_id": str(job.id), "station": payload.print_station})
    db.commit()
    return {"ok": True, "print_job_id": str(job.id)}


def _wb_supply_scope_ids(cred: MarketplaceCredential, physical_warehouse_id: uuid.UUID | str | None) -> set[int]:
    settings = _wb_settings(cred, physical_warehouse_id)
    result: set[int] = set()
    for key, value in settings.items():
        if str((value or {}).get("mode") or "HIDDEN").upper() not in {"WORK", "WATCH"}:
            continue
        try:
            result.add(int(key))
        except (TypeError, ValueError):
            continue
    return result


def _wb_supply_order_rows(
    db: Session,
    cred: MarketplaceCredential,
    *,
    supply_id: str | None = None,
    order_ids: list[int] | None = None,
) -> list[MarketplaceOrder]:
    stmt = select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id.in_(_logical_credential_ids(db, cred)),
        MarketplaceOrder.provider == "WB",
    )
    if supply_id is not None:
        stmt = stmt.where(MarketplaceOrder.supply_id == str(supply_id))
    if order_ids is not None:
        stmt = stmt.where(MarketplaceOrder.external_order_num.in_(order_ids))
    return db.execute(stmt).scalars().all()


def _wb_supply_add_confirmed(client: WBClient, supply_id: str, order_ids: list[int]) -> tuple[bool, list[int], str | None]:
    """Add orders and reconcile ambiguous network/API failures against WB membership.

    A browser timeout must never encourage the operator to create a duplicate supply.
    When WB accepted the PATCH but the response was lost, the membership read-back makes
    the operation successful from FulfillCore's point of view.
    """
    normalized = list(dict.fromkeys(int(x) for x in order_ids))
    if not normalized:
        return True, [], None
    last_error: HTTPException | None = None
    for attempt in range(2):
        try:
            _wb_add_orders_to_supply(client, supply_id, normalized)
            try:
                membership = [int(x) for x in client.supply_order_ids(supply_id)]
            except Exception:
                membership = normalized
            return True, membership, None
        except HTTPException as exc:
            last_error = exc
            try:
                membership = [int(x) for x in client.supply_order_ids(supply_id)]
            except Exception:
                membership = []
            if set(normalized).issubset(set(membership)):
                return True, membership, None
            # A 409 is expensive in WB rate limiting and normally describes a business
            # rule, not a transient transport failure. Do not hammer it repeatedly.
            if exc.status_code == 409 or attempt:
                break
            if exc.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                break
            time.sleep(0.35)
    return False, [], str(last_error.detail if last_error else "WB не подтвердил добавление заказов")


def _wb_supply_registry_item_map(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(x.get("id") or ""): x for x in rows if x.get("id")}


@router.get("/wb/supplies")
def wb_supplies(
    credential_id: uuid.UUID,
    include_closed: bool = Query(default=False),
    wb_warehouse_id: int | None = Query(default=None),
    warehouse_id: uuid.UUID | None = Query(default=None),
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Return one central supply view shared by every FulfillCore workstation.

    WB remains authoritative, but a small PostgreSQL registry keeps freshly-created and
    recently-seen supplies visible on all computers immediately. Only one workstation
    refreshes WB within a short interval; the others read the same central cache. This
    removes per-browser visibility differences without mixing physical warehouses.
    When wb_warehouse_id is explicitly selected, the response is strict: only supplies
    mapped to that exact WB seller warehouse are returned; unknown/unmapped supplies
    are intentionally hidden until their membership identifies the warehouse.
    """
    cred, client = _wb(db, credential_id)
    visible_wb_ids = _wb_supply_scope_ids(cred, warehouse_id)
    if wb_warehouse_id is not None:
        if _wb_mode(cred, wb_warehouse_id, warehouse_id) == "HIDDEN":
            return {"supplies": [], "count": 0, "live_refreshed": False, "cached": True, "live_error": None}
        visible_wb_ids = {int(wb_warehouse_id)}

    registry_rows = list_wb_supply_registry(
        db,
        client_id=cred.client_id,
        cabinet_name=cred.name,
        physical_warehouse_id=warehouse_id,
        wb_warehouse_ids=visible_wb_ids,
        strict_wb_warehouse_ids=wb_warehouse_id is not None,
        include_closed=include_closed,
    )
    registry_map = _wb_supply_registry_item_map(registry_rows)

    # Existing local order links are another durable mapping source, including supplies
    # made before the central registry existed.
    order_stmt = select(
        MarketplaceOrder.supply_id,
        MarketplaceOrder.wb_warehouse_id,
        func.count(MarketplaceOrder.id),
    ).where(
        MarketplaceOrder.credential_id.in_(_logical_credential_ids(db, cred)),
        MarketplaceOrder.provider == "WB",
        MarketplaceOrder.supply_id.is_not(None),
    )
    if visible_wb_ids:
        order_stmt = order_stmt.where(MarketplaceOrder.wb_warehouse_id.in_(visible_wb_ids))
    elif warehouse_id is not None:
        # If the physical workspace has no configured WB seller warehouse yet, do not
        # leak order-linked supplies from some other workspace.
        order_stmt = order_stmt.where(text("1=0"))
    order_stmt = order_stmt.group_by(MarketplaceOrder.supply_id, MarketplaceOrder.wb_warehouse_id)
    local_supply_rows = db.execute(order_stmt).all()
    local_map: dict[str, dict[str, Any]] = {}
    for sid, wb_id, cnt in local_supply_rows:
        if not sid:
            continue
        key = str(sid)
        row = local_map.setdefault(key, {"wb_warehouse_id": wb_id, "order_count": 0})
        row["order_count"] = int(row.get("order_count") or 0) + int(cnt or 0)
        if row.get("wb_warehouse_id") is None and wb_id is not None:
            row["wb_warehouse_id"] = wb_id

    allowed_supply_ids = set(local_map) | set(registry_map)
    live_items: list[dict[str, Any]] = []
    live_refreshed = False
    live_error: str | None = None

    # The WB endpoint is external and relatively expensive. All browsers still refresh
    # every few seconds, but only one process actually hits WB during this interval.
    should_live = bool(force or not registry_rows)
    if not should_live:
        try:
            should_live = claim_runtime_interval(
                db,
                f"wb-supplies:{cred.client_id}:{cred.name}",
                30.0,
                force=False,
            )
        except Exception:
            should_live = True
    if should_live:
        # 0.9.4.9 SPEED: finish the local registry/throttle transaction before WB HTTP.
        # This prevents a slow external request from holding a PostgreSQL connection in
        # `idle in transaction` while other PCs are trying to load orders.
        try:
            if db.in_transaction():
                db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        try:
            live_items = [dict(x) for x in (client.supplies() or []) if isinstance(x, dict)]
            live_refreshed = True
        except HTTPException as exc:
            live_error = str(exc.detail or f"WB API {exc.status_code}")
        except Exception as exc:
            live_error = str(exc or "Ошибка получения поставок WB")

    if not include_closed:
        live_items = [x for x in live_items if not bool(x.get("done"))]

    # Recover membership for supplies not yet mapped to this physical workspace. Active
    # supplies are checked first. The bound keeps the UI fast and respects WB limits.
    unknown_items = [x for x in live_items if str(x.get("id") or "") not in allowed_supply_ids]
    def probe_key(item: dict[str, Any]):
        raw = item.get("createdAt") or item.get("created_at") or item.get("closedAt") or item.get("scanDt") or ""
        dt = _parse_dt(raw)
        return (1 if bool(item.get("done")) else 0, -(dt.timestamp() if dt else 0.0))
    unknown_items.sort(key=probe_key)
    # 0.9.4.9 SPEED: recover unknown supply membership progressively instead of
    # making 25-40 sequential WB calls inside one page request. Repeated refreshes
    # finish the remainder while the UI stays responsive on all workstations.
    probe_limit = 10 if include_closed else 6
    changed_links = False
    for supply_item in unknown_items[:probe_limit]:
        sid = str(supply_item.get("id") or "").strip()
        if not sid:
            continue
        # Persist any mapping from the previous iteration and release the DB connection
        # before the next external WB call. No business rule is changed.
        try:
            if db.in_transaction():
                db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        try:
            member_ids = [int(x) for x in client.supply_order_ids(sid)]
        except Exception:
            continue
        if not member_ids:
            # An externally-created empty supply has no safe warehouse identity. It is
            # intentionally not guessed into a physical warehouse. Supplies created by
            # FulfillCore are registered at creation time and therefore remain visible.
            continue
        matched_orders = _wb_supply_order_rows(db, cred, order_ids=member_ids)
        matched_visible = [o for o in matched_orders if o.wb_warehouse_id in visible_wb_ids] if visible_wb_ids else []
        if not matched_visible:
            continue
        wb_ids = {int(o.wb_warehouse_id) for o in matched_visible if o.wb_warehouse_id is not None}
        mapped_wb = next(iter(wb_ids)) if len(wb_ids) == 1 else None
        for order in matched_visible:
            if order.supply_id != sid:
                order.supply_id = sid
                changed_links = True
        local_map[sid] = {"wb_warehouse_id": mapped_wb, "order_count": len(member_ids)}
        allowed_supply_ids.add(sid)
        upsert_wb_supply(
            db,
            client_id=cred.client_id,
            cabinet_name=cred.name,
            supply_id=sid,
            credential_id=cred.id,
            physical_warehouse_id=warehouse_id,
            wb_warehouse_id=mapped_wb,
            supply_name=supply_item.get("name"),
            done=bool(supply_item.get("done")),
            order_count=len(member_ids),
            source="WB_DISCOVERED",
            raw_item=supply_item,
            live_sync=True,
        )
    if changed_links:
        db.flush()

    # Persist every live supply that is already known to belong to this workspace.
    live_map: dict[str, dict[str, Any]] = {}
    for item in live_items:
        sid = str(item.get("id") or "").strip()
        if not sid or sid not in allowed_supply_ids:
            continue
        local_info = local_map.get(sid) or {}
        cached_info = registry_map.get(sid) or {}
        wb_id = local_info.get("wb_warehouse_id") or cached_info.get("_fc_wb_warehouse_id")
        count = local_info.get("order_count")
        if count is None:
            count = cached_info.get("_fc_order_count")
        upsert_wb_supply(
            db,
            client_id=cred.client_id,
            cabinet_name=cred.name,
            supply_id=sid,
            credential_id=cred.id,
            physical_warehouse_id=warehouse_id if sid in local_map else cached_info.get("_fc_physical_warehouse_id"),
            wb_warehouse_id=int(wb_id) if wb_id is not None else None,
            supply_name=item.get("name"),
            done=bool(item.get("done")),
            order_count=int(count or 0),
            source=cached_info.get("_fc_source") or "WB",
            raw_item=item,
            last_error=None,
            live_sync=True,
        )
        enriched = dict(item)
        enriched["_fc_cached"] = False
        enriched["_fc_registry"] = True
        enriched["_fc_wb_warehouse_id"] = int(wb_id) if wb_id is not None else None
        enriched["_fc_order_count"] = int(count or 0)
        enriched["_fc_last_error"] = None
        live_map[sid] = enriched
    if live_refreshed or changed_links:
        db.commit()

    # Reload the registry after reconciliation so every current request returns exactly
    # the same state that the next workstation will see.
    registry_rows = list_wb_supply_registry(
        db,
        client_id=cred.client_id,
        cabinet_name=cred.name,
        physical_warehouse_id=warehouse_id,
        wb_warehouse_ids=visible_wb_ids,
        strict_wb_warehouse_ids=wb_warehouse_id is not None,
        include_closed=include_closed,
    )
    result_map = _wb_supply_registry_item_map(registry_rows)
    for sid, live in live_map.items():
        cached = result_map.get(sid) or {}
        merged = {**cached, **live}
        merged["_fc_cached"] = False
        result_map[sid] = merged

    items = list(result_map.values())
    def sort_key(item: dict[str, Any]):
        raw = item.get("createdAt") or item.get("created_at") or item.get("closedAt") or item.get("scanDt") or ""
        dt = _parse_dt(raw)
        ts = dt.timestamp() if dt else float(item.get("_fc_updated_epoch") or 0)
        return (1 if bool(item.get("done")) else 0, -ts, str(item.get("id") or ""))
    items.sort(key=sort_key)
    try:
        if db.in_transaction():
            db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return {
        "supplies": items,
        "count": len(items),
        "live_refreshed": live_refreshed,
        "cached": not live_refreshed,
        "live_error": live_error,
        "physical_warehouse_id": str(warehouse_id) if warehouse_id else None,
        "wb_warehouse_ids": sorted(visible_wb_ids),
    }


@router.post("/wb/supplies")
def wb_create_supply(
    payload: WBCreateSupply,
    warehouse_id: uuid.UUID | None = Query(default=None),
    wb_warehouse_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cred, client = _wb(db, payload.credential_id)
    if warehouse_id is None:
        raise HTTPException(status_code=409, detail="Сначала выберите физический склад FulfillCore")
    requested_ids = list(dict.fromkeys(int(x) for x in (payload.order_ids or [])))
    orders = _wb_supply_order_rows(db, cred, order_ids=requested_ids) if requested_ids else []
    if requested_ids:
        by_num = {int(o.external_order_num): o for o in orders if o.external_order_num is not None}
        missing = [x for x in requested_ids if x not in by_num]
        if missing:
            raise HTTPException(status_code=404, detail=f"Не найдены локальные заказы WB: {missing[:10]}")
        invalid = [o.external_order_id for o in orders if o.local_status != "NEW" or o.supply_id]
        if invalid:
            raise HTTPException(status_code=409, detail=f"В новую поставку можно добавить только новые заказы без поставки: {invalid[:10]}")
        for order in orders:
            _assert_wb_work(cred, order, warehouse_id)
        order_wb_ids = {int(o.wb_warehouse_id) for o in orders if o.wb_warehouse_id is not None}
        if len(order_wb_ids) != 1:
            raise HTTPException(status_code=409, detail="Одна поставка WB должна содержать заказы одного склада WB")
        inferred_wb_id = next(iter(order_wb_ids))
        if wb_warehouse_id is not None and int(wb_warehouse_id) != inferred_wb_id:
            raise HTTPException(status_code=409, detail="Выбранный склад WB не совпадает со складом выбранных заказов")
        wb_warehouse_id = inferred_wb_id
        cargo_types = {str(o.cargo_type or "").strip() for o in orders if str(o.cargo_type or "").strip()}
        if len(cargo_types) > 1:
            raise HTTPException(status_code=409, detail="WB не разрешает смешивать разные cargoType в одной поставке")
    else:
        if wb_warehouse_id is None:
            work_ids = [int(k) for k, v in _wb_settings(cred, warehouse_id).items() if str((v or {}).get("mode") or "").upper() == "WORK"]
            if len(work_ids) == 1:
                wb_warehouse_id = work_ids[0]
            else:
                raise HTTPException(status_code=409, detail="Для пустой поставки сначала выберите конкретный рабочий склад WB")
        if _wb_mode(cred, wb_warehouse_id, warehouse_id) != "WORK":
            raise HTTPException(status_code=409, detail="Поставку можно создавать только для склада WB в режиме «Работа»")

    lock_key = f"wb-supply-create:{cred.client_id}:{cred.name}:{warehouse_id}:{wb_warehouse_id}"
    with distributed_lock(db, lock_key, blocking=True):
        data = client.create_supply(payload.name)
        supply_id = str(data.get("id") or "").strip()
        if not supply_id:
            raise HTTPException(status_code=502, detail="WB не вернул ID созданной поставки")
        # Commit the registry immediately. Even if WB order linking is slow or fails,
        # every workstation sees the newly-created supply and the operator gets its ID.
        upsert_wb_supply(
            db,
            client_id=cred.client_id,
            cabinet_name=cred.name,
            supply_id=supply_id,
            credential_id=cred.id,
            physical_warehouse_id=warehouse_id,
            wb_warehouse_id=wb_warehouse_id,
            supply_name=payload.name,
            done=False,
            order_count=0,
            source="FULFILLCORE",
            raw_item={"id": supply_id, "name": payload.name, "done": False, "createdAt": _now().isoformat()},
        )
        db.commit()

        if not requested_ids:
            return {"ok": True, "id": supply_id, "orders_added": 0, "empty": True}

        linked, membership, error = _wb_supply_add_confirmed(client, supply_id, requested_ids)
        if not linked:
            upsert_wb_supply(
                db,
                client_id=cred.client_id,
                cabinet_name=cred.name,
                supply_id=supply_id,
                credential_id=cred.id,
                physical_warehouse_id=warehouse_id,
                wb_warehouse_id=wb_warehouse_id,
                supply_name=payload.name,
                done=False,
                order_count=len(membership),
                source="FULFILLCORE",
                last_error=error,
            )
            db.commit()
            return {
                "ok": False,
                "id": supply_id,
                "created": True,
                "orders_added": len(membership),
                "warning": f"Поставка {supply_id} создана, но WB не подтвердил добавление всех заказов: {error}",
            }

        for order in orders:
            order.supply_id = supply_id
            order.supplier_status = "confirm"
            order.local_status = _compute_local_status(order)
            _event(db, order, "ADDED_TO_SUPPLY", value=supply_id)
        upsert_wb_supply(
            db,
            client_id=cred.client_id,
            cabinet_name=cred.name,
            supply_id=supply_id,
            credential_id=cred.id,
            physical_warehouse_id=warehouse_id,
            wb_warehouse_id=wb_warehouse_id,
            supply_name=payload.name,
            done=False,
            order_count=len(set(membership) or set(requested_ids)),
            source="FULFILLCORE",
            last_error=None,
        )
        db.commit()
        return {"ok": True, "id": supply_id, "orders_added": len(requested_ids), "empty": False}


@router.patch("/wb/supplies/{supply_id}/orders")
def wb_add_orders(
    supply_id: str,
    payload: WBAddOrders,
    warehouse_id: uuid.UUID | None = Query(default=None),
    wb_warehouse_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cred, client = _wb(db, payload.credential_id)
    if warehouse_id is None:
        raise HTTPException(status_code=409, detail="Сначала выберите физический склад FulfillCore")
    requested_ids = list(dict.fromkeys(int(x) for x in payload.order_ids))
    if not requested_ids:
        raise HTTPException(status_code=400, detail="Выберите заказы, которые нужно добавить в поставку")
    orders = _wb_supply_order_rows(db, cred, order_ids=requested_ids)
    by_num = {int(o.external_order_num): o for o in orders if o.external_order_num is not None}
    missing = [x for x in requested_ids if x not in by_num]
    if missing:
        raise HTTPException(status_code=404, detail=f"Не найдены локальные заказы WB: {missing[:10]}")
    invalid = [o.external_order_id for o in orders if o.local_status != "NEW" or (o.supply_id and o.supply_id != supply_id)]
    if invalid:
        raise HTTPException(status_code=409, detail=f"Добавить можно только новые заказы без другой поставки: {invalid[:10]}")
    for order in orders:
        _assert_wb_work(cred, order, warehouse_id)
    order_wb_ids = {int(o.wb_warehouse_id) for o in orders if o.wb_warehouse_id is not None}
    if len(order_wb_ids) != 1:
        raise HTTPException(status_code=409, detail="Выбранные заказы относятся к разным складам WB")
    inferred_wb_id = next(iter(order_wb_ids))
    if wb_warehouse_id is not None and int(wb_warehouse_id) != inferred_wb_id:
        raise HTTPException(status_code=409, detail="Выбранный склад WB не совпадает со складом заказов")
    wb_warehouse_id = inferred_wb_id

    with distributed_lock(db, f"wb-supply:{cred.client_id}:{supply_id}", blocking=True):
        cached = registry_supply(db, client_id=cred.client_id, cabinet_name=cred.name, supply_id=supply_id)
        if cached and bool(cached.get("done")):
            raise HTTPException(status_code=409, detail="Поставка уже закрыта. Добавить заказы нельзя")
        linked, membership, error = _wb_supply_add_confirmed(client, supply_id, requested_ids)
        if not linked:
            set_wb_supply_error(db, client_id=cred.client_id, cabinet_name=cred.name, supply_id=supply_id, error=error)
            db.commit()
            raise HTTPException(status_code=409, detail=f"WB не подтвердил добавление заказов в {supply_id}: {error}")
        for order in orders:
            order.supply_id = supply_id
            order.supplier_status = "confirm"
            order.local_status = _compute_local_status(order)
            _event(db, order, "ADDED_TO_SUPPLY", value=supply_id)
        upsert_wb_supply(
            db,
            client_id=cred.client_id,
            cabinet_name=cred.name,
            supply_id=supply_id,
            credential_id=cred.id,
            physical_warehouse_id=warehouse_id,
            wb_warehouse_id=wb_warehouse_id,
            done=False,
            order_count=len(set(membership) or set(requested_ids)),
            source=(cached or {}).get("_fc_source") or "FULFILLCORE",
            last_error=None,
        )
        db.commit()
        return {"ok": True, "supply_id": supply_id, "orders_added": len(requested_ids), "order_count": len(set(membership) or set(requested_ids))}


@router.patch("/wb/supplies/{supply_id}/deliver")
def wb_deliver_supply(
    supply_id: str,
    credential_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
):
    cred, client = _wb(db, credential_id)
    if warehouse_id is None:
        raise HTTPException(status_code=409, detail="Сначала выберите физический склад FulfillCore")

    with distributed_lock(db, f"wb-supply:{cred.client_id}:{supply_id}", blocking=True):
        cached = registry_supply(db, client_id=cred.client_id, cabinet_name=cred.name, supply_id=supply_id)
        if cached and bool(cached.get("done")):
            return {"ok": True, "supply_id": supply_id, "already_closed": True}

        membership: list[int] | None = None
        try:
            membership = [int(x) for x in client.supply_order_ids(supply_id)]
        except HTTPException as exc:
            # Do not fail immediately: a just-closed supply can race the membership
            # projection. The final deliver/read-back below is authoritative.
            if exc.status_code not in {404, 409, 429, 502, 503, 504}:
                raise
        except Exception:
            membership = None

        if membership == []:
            raise HTTPException(status_code=409, detail="Пустую поставку WB нельзя закрыть. Сначала добавьте в неё хотя бы один заказ")

        if membership:
            orders = _wb_supply_order_rows(db, cred, order_ids=membership)
            by_num = {int(o.external_order_num): o for o in orders if o.external_order_num is not None}
            missing = [x for x in membership if x not in by_num]
            if missing:
                raise HTTPException(status_code=409, detail=f"В поставке есть заказы, которых нет в FulfillCore: {missing[:10]}. Сначала обновите заказы WB")
            for order in orders:
                _assert_wb_work(cred, order, warehouse_id)
                if order.supply_id != supply_id:
                    order.supply_id = supply_id
        else:
            orders = _wb_supply_order_rows(db, cred, supply_id=supply_id)

        if not orders:
            raise HTTPException(status_code=409, detail="FulfillCore не нашёл состав поставки. Нажмите «Обновить» и повторите закрытие")

        blocking = [
            o.external_order_id for o in orders
            if o.local_status not in {"CANCELLED", "DELIVERED", "SENT"}
            and not (o.chz_code and _has_wb_order_sticker(o))
        ]
        if blocking:
            raise HTTPException(
                status_code=409,
                detail=f"Поставка {supply_id} содержит заказы без кода маркировки и/или этикетки WB: {', '.join(blocking[:10])}",
            )

        deliver_error: str | None = None
        delivered = False
        try:
            client.deliver_supply(supply_id)
            delivered = True
        except HTTPException as exc:
            deliver_error = str(exc.detail or f"WB API {exc.status_code}")
        except Exception as exc:
            deliver_error = str(exc or "Ошибка закрытия поставки WB")

        # Re-read WB after an ambiguous response. If the PATCH reached WB but its reply
        # was lost, FulfillCore must report success instead of encouraging a second click.
        live_item: dict[str, Any] | None = None
        if not delivered:
            try:
                live_item = next((dict(x) for x in (client.supplies() or []) if str(x.get("id") or "") == str(supply_id)), None)
            except Exception:
                live_item = None
            if live_item and bool(live_item.get("done")):
                delivered = True
        if not delivered:
            set_wb_supply_error(db, client_id=cred.client_id, cabinet_name=cred.name, supply_id=supply_id, error=deliver_error)
            db.commit()
            raise HTTPException(status_code=409, detail=f"WB не подтвердил закрытие поставки {supply_id}: {deliver_error or 'неизвестная ошибка'}")

        for order in orders:
            order.supplier_status = "complete"
            if order.local_status not in {"CANCELLED", "DELIVERED", "SENT"}:
                setattr(order, "manual_status_override", False) if hasattr(order, "manual_status_override") else None
                order.local_status = "SENT"
            _event(db, order, "SUPPLY_DELIVERED", value=supply_id)
        wb_ids = {int(o.wb_warehouse_id) for o in orders if o.wb_warehouse_id is not None}
        mapped_wb = next(iter(wb_ids)) if len(wb_ids) == 1 else (cached or {}).get("_fc_wb_warehouse_id")
        upsert_wb_supply(
            db,
            client_id=cred.client_id,
            cabinet_name=cred.name,
            supply_id=supply_id,
            credential_id=cred.id,
            physical_warehouse_id=warehouse_id,
            wb_warehouse_id=int(mapped_wb) if mapped_wb is not None else None,
            supply_name=(live_item or {}).get("name") or (cached or {}).get("name"),
            done=True,
            order_count=len(membership or orders),
            source=(cached or {}).get("_fc_source") or "FULFILLCORE",
            raw_item=live_item,
            last_error=None,
            live_sync=bool(live_item),
        )
        db.commit()
        return {"ok": True, "supply_id": supply_id, "already_closed": False, "orders": len(orders)}


@router.get("/wb/supplies/{supply_id}/box-label.pdf")
def wb_supply_box_label_pdf(
    supply_id: str,
    credential_id: uuid.UUID,
    closed: bool = Query(default=False),
    copies: int = Query(default=1, ge=1, le=1000),
    width_mm: float = Query(default=58, ge=25, le=120),
    height_mm: float = Query(default=40, ge=25, le=120),
    scale_percent: float = Query(default=100, ge=85, le=108),
    offset_x_mm: float = Query(default=0, ge=-5, le=5),
    offset_y_mm: float = Query(default=0, ge=-5, le=5),
    db: Session = Depends(get_db),
):
    """Printable physical-box label for a WB supply.

    This is intentionally separate from WB transport-box (trbx) labels.
    While the supply is open it contains only the locally generated supply-ID QR.
    Once the supply is closed it additionally shows the number of WB orders.
    """
    _, client = _wb(db, credential_id)
    raw = _wb_supply_barcode_png(client, supply_id, official_available=closed)
    item_count = None
    if closed:
        try:
            item_count = len(client.supply_order_ids(supply_id))
        except Exception:
            item_count = int(db.scalar(select(func.count(MarketplaceOrder.id)).where(
                MarketplaceOrder.credential_id == credential_id,
                MarketplaceOrder.provider == "WB",
                MarketplaceOrder.supply_id == supply_id,
                MarketplaceOrder.local_status != "CANCELLED",
            )) or 0)
    png = supply_box_label_png(
        raw,
        item_count=item_count,
        width_mm=width_mm,
        height_mm=height_mm,
        scale_percent=scale_percent,
        offset_x_mm=offset_x_mm,
        offset_y_mm=offset_y_mm,
    )
    pdf = png_pages_to_pdf([png for _ in range(copies)], width_mm=width_mm, height_mm=height_mm)
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{supply_id}-box-labels.pdf"'})


@router.post("/wb/supplies/{supply_id}/box-label/queue")
def wb_queue_supply_box_label(
    supply_id: str,
    payload: WBQueuePrintRequest,
    closed: bool = Query(default=False),
    copies: int = Query(default=1, ge=1, le=1000),
    width_mm: float = Query(default=58, ge=25, le=120),
    height_mm: float = Query(default=40, ge=25, le=120),
    scale_percent: float = Query(default=100, ge=85, le=108),
    offset_x_mm: float = Query(default=0, ge=-5, le=5),
    offset_y_mm: float = Query(default=0, ge=-5, le=5),
    db: Session = Depends(get_db),
):
    """Queue one or more supply-barcode labels through the existing Print Agent."""
    cred, client = _wb(db, payload.credential_id)
    raw = _wb_supply_barcode_png(client, supply_id, official_available=closed)
    item_count = None
    if closed:
        try:
            item_count = len(client.supply_order_ids(supply_id))
        except Exception:
            item_count = int(db.scalar(select(func.count(MarketplaceOrder.id)).where(
                MarketplaceOrder.credential_id == payload.credential_id,
                MarketplaceOrder.provider == "WB",
                MarketplaceOrder.supply_id == supply_id,
                MarketplaceOrder.local_status != "CANCELLED",
            )) or 0)
    png = supply_box_label_png(
        raw,
        item_count=item_count,
        width_mm=width_mm,
        height_mm=height_mm,
        scale_percent=scale_percent,
        offset_x_mm=offset_x_mm,
        offset_y_mm=offset_y_mm,
    )
    job_ids: list[str] = []
    for idx in range(copies):
        job = queue_print_job(
            db,
            station_code=normalize_station_code(payload.print_station),
            payload=png,
            file_name=f"WB-supply-{supply_id}-box-{idx + 1}.png",
            content_type="image/png",
            document_type="WB_SUPPLY_BOX_LABEL",
            client_id=cred.client_id,
            object_type="WB_SUPPLY",
            object_id=str(supply_id),
        )
        job_ids.append(str(job.id))
    db.commit()
    return {"ok": True, "supply_id": supply_id, "copies": copies, "item_count": item_count, "print_job_ids": job_ids}


@router.get("/wb/supplies/{supply_id}/a4-label")
def wb_supply_a4_label(
    supply_id: str,
    credential_id: uuid.UUID,
    quantity: int = Query(..., ge=1, le=999999),
    db: Session = Depends(get_db),
):
    """Large A4 browser-print label for an OPEN or CLOSED WB supply.

    The operator enters the physical quantity manually. This route deliberately does
    not use Print Agent and never changes the WB supply state. For an open supply WB
    may not expose the official image yet, so we use the same status-safe local
    supply barcode representation already used by the working 58x40 print path.
    """
    cred, client = _wb(db, credential_id)

    supply = None
    try:
        supply = next((x for x in client.supplies() if str(x.get("id") or "") == str(supply_id)), None)
    except Exception:
        supply = None

    cached = registry_supply(
        db,
        client_id=cred.client_id,
        cabinet_name=cred.name,
        supply_id=supply_id,
    )
    if not supply and not cached:
        raise HTTPException(status_code=404, detail=f"Поставка {supply_id} не найдена")

    is_closed = bool((supply or {}).get("done") or (cached or {}).get("done"))
    raw = _wb_supply_barcode_png(client, supply_id, official_available=is_closed)
    if not raw:
        raise HTTPException(status_code=502, detail="Не удалось сформировать штрихкод поставки")

    barcode_b64 = base64.b64encode(raw).decode("ascii")
    safe_supply = html.escape(str(supply_id))
    safe_name = html.escape(str((supply or {}).get("name") or (cached or {}).get("name") or "Поставка WB"))
    safe_qty = html.escape(str(quantity))

    page = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>WB {safe_supply} — A4</title>
<style>
  @page {{ size: A4 portrait; margin: 0; }}
  * {{ box-sizing: border-box; }}
  html, body {{ margin: 0; padding: 0; width: 210mm; height: 297mm; background: #fff; color: #000; }}
  body {{ font-family: Arial, Helvetica, sans-serif; }}
  .sheet {{
    width: 210mm; height: 297mm; padding: 12mm 14mm 14mm;
    display: flex; flex-direction: column; align-items: center;
    justify-content: flex-start; overflow: hidden; background: #fff;
  }}
  .title {{ font-size: 11mm; line-height: 1.05; font-weight: 800; text-align: center; margin: 0 0 5mm; }}
  .supply-id {{ font-size: 7mm; font-weight: 700; text-align: center; margin-bottom: 8mm; }}
  .barcode-wrap {{
    width: 176mm; height: 150mm; display: flex; align-items: center; justify-content: center;
    padding: 4mm; border: 1.2mm solid #000; background: #fff;
  }}
  .barcode-wrap img {{ max-width: 100%; max-height: 100%; object-fit: contain; image-rendering: crisp-edges; }}
  .qty-label {{ margin-top: 9mm; font-size: 7mm; font-weight: 700; text-align: center; }}
  .qty {{ margin-top: 2mm; font-size: 24mm; line-height: 1; font-weight: 900; text-align: center; white-space: nowrap; }}
  .qty span {{ font-size: 9mm; font-weight: 800; }}
  .hint {{ display: none; }}
  @media screen {{
    body {{ width: auto; height: auto; background: #eceff3; }}
    .sheet {{ margin: 12px auto; box-shadow: 0 2px 18px rgba(0,0,0,.18); }}
    .hint {{ display: flex; gap: 10px; align-items: center; position: fixed; right: 18px; top: 18px; padding: 10px 14px; background: #111; color: #fff; border-radius: 8px; font-size: 14px; }} .hint button {{ border: 0; border-radius: 6px; padding: 8px 10px; cursor: pointer; font-weight: 700; }}
  }}
</style>
</head>
<body>
<div class="hint"><button type="button" onclick="history.back()">← Вернуться в FulfillCore</button><span>Если печать не открылась — нажмите Ctrl+P</span></div>
<main class="sheet">
  <div class="title">{safe_name}</div>
  <div class="supply-id">ID поставки: {safe_supply}</div>
  <div class="barcode-wrap"><img src="data:image/png;base64,{barcode_b64}" alt="Штрихкод поставки WB"></div>
  <div class="qty-label">КОЛИЧЕСТВО ТОВАРА</div>
  <div class="qty">{safe_qty} <span>ШТ.</span></div>
</main>
<script>window.addEventListener('load', () => setTimeout(() => window.print(), 250));</script>
</body>
</html>"""
    return Response(content=page, media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


@router.get("/wb/supplies/{supply_id}/barcode.pdf")
def wb_supply_barcode_pdf(
    supply_id: str,
    credential_id: uuid.UUID,
    closed: bool = Query(default=False),
    width_mm: float = Query(default=58, ge=25, le=120),
    height_mm: float = Query(default=40, ge=25, le=120),
    scale_percent: float = Query(default=100, ge=85, le=108),
    offset_x_mm: float = Query(default=0, ge=-5, le=5),
    offset_y_mm: float = Query(default=0, ge=-5, le=5),
    db: Session = Depends(get_db),
):
    """Print one supply QR without closing or otherwise modifying the supply."""
    _, client = _wb(db, credential_id)
    raw = _wb_supply_barcode_png(client, supply_id, official_available=closed)
    png = supply_box_label_png(
        raw,
        item_count=None,
        width_mm=width_mm,
        height_mm=height_mm,
        scale_percent=scale_percent,
        offset_x_mm=offset_x_mm,
        offset_y_mm=offset_y_mm,
    )
    pdf = png_pages_to_pdf([png], width_mm=width_mm, height_mm=height_mm)
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{supply_id}.pdf"'})


@router.post("/wb/supplies/{supply_id}/barcode/queue")
def wb_queue_supply_barcode(
    supply_id: str,
    payload: WBQueuePrintRequest,
    closed: bool = Query(default=False),
    width_mm: float = Query(default=58, ge=25, le=120),
    height_mm: float = Query(default=40, ge=25, le=120),
    scale_percent: float = Query(default=100, ge=85, le=108),
    offset_x_mm: float = Query(default=0, ge=-5, le=5),
    offset_y_mm: float = Query(default=0, ge=-5, le=5),
    db: Session = Depends(get_db),
):
    """Queue one open/closed supply label through Print Agent, state-free."""
    cred, client = _wb(db, payload.credential_id)
    raw = _wb_supply_barcode_png(client, supply_id, official_available=closed)
    png = supply_box_label_png(
        raw,
        item_count=None,
        width_mm=width_mm,
        height_mm=height_mm,
        scale_percent=scale_percent,
        offset_x_mm=offset_x_mm,
        offset_y_mm=offset_y_mm,
    )
    job = queue_print_job(
        db,
        station_code=normalize_station_code(payload.print_station),
        payload=png,
        file_name=f"WB-supply-{supply_id}.png",
        content_type="image/png",
        document_type="WB_SUPPLY_LABEL",
        client_id=cred.client_id,
        object_type="WB_SUPPLY",
        object_id=str(supply_id),
    )
    db.commit()
    return {"ok": True, "supply_id": supply_id, "print_job_id": str(job.id)}


@router.post("/wb/order-labels.pdf")
def wb_order_labels_pdf(payload: WBOrderLabelsRequest, db: Session = Depends(get_db)):
    _, client = _wb(db, payload.credential_id)
    orders = db.execute(
        select(MarketplaceOrder).where(
            MarketplaceOrder.credential_id == payload.credential_id,
            MarketplaceOrder.provider == "WB",
            MarketplaceOrder.external_order_num.in_(payload.order_ids),
        )
    ).scalars().all()
    by_num = {o.external_order_num: o for o in orders}
    cred = _credential(db, payload.credential_id, "WB")
    for order in orders:
        _assert_wb_work(cred, order)
    bad = [oid for oid in payload.order_ids if oid not in by_num or not by_num[oid].chz_code]
    if bad:
        raise HTTPException(status_code=409, detail=f"Первичная этикетка WB печатается только после кода маркировки. Заказы без кода: {bad[:10]}")

    try:
        sticker_items = client.order_stickers(payload.order_ids)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("FC_BULK_LABEL_WB_REQUEST_FAIL order_ids=%s", payload.order_ids)
        raise HTTPException(
            status_code=502,
            detail=f"Не удалось запросить этикетку в WB: {exc}",
        ) from exc
    sticker_by_id: dict[int, dict[str, Any]] = {}
    for item in sticker_items:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("orderId") or item.get("id")
        try:
            oid = int(raw_id)
        except (TypeError, ValueError):
            continue
        sticker_by_id[oid] = item

    images: list[bytes] = []
    missing: list[int] = []
    for oid in payload.order_ids:
        item = sticker_by_id.get(oid)
        order = by_num.get(oid)
        if item and order:
            _apply_wb_order_sticker(db, order, item)
        image = _decode_wb_sticker_file(item.get("file") if item else None)
        if image is None and order is not None:
            # WB can briefly return a one-order sticker without an id, a data URI, or
            # an incomplete projection. Reuse the already proven resilient one-order
            # recovery instead of leaking base64/binascii as Internal Server Error.
            try:
                image, _ = _fetch_wb_order_sticker_for_print_with_cancel_release(
                    db,
                    client,
                    order,
                    source="BULK_BROWSER_LABEL_RECOVERY",
                    fast_auto=False,
                )
            except HTTPException:
                raise
            except Exception as exc:
                logger.exception("FC_BULK_LABEL_RECOVERY_FAIL order_num=%s", oid)
                raise HTTPException(
                    status_code=502,
                    detail=f"Не удалось получить этикетку WB для заказа №{oid}: {exc}",
                ) from exc
        if image is None:
            missing.append(oid)
        else:
            images.append(image)
    if missing:
        raise HTTPException(
            status_code=503,
            detail=(
                "WB пока не подготовил этикетку для действующего заказа. "
                f"Не готовы: {missing[:10]}. Повторите печать через несколько секунд."
            ),
        )
    try:
        pdf = png_pages_to_pdf(images)
    except Exception as exc:
        logger.exception("FC_BULK_LABEL_PDF_FAIL order_ids=%s", payload.order_ids)
        raise HTTPException(
            status_code=502,
            detail="Этикетка получена от WB, но сервер не смог подготовить PDF. Повторите печать.",
        ) from exc
    now = _now()
    for order in orders:
        order.label_generated_at = order.label_generated_at or now
        order.label_print_count = (order.label_print_count or 0) + 1
        packed_from_picking = order.local_status == "PICKING"
        if packed_from_picking:
            # This endpoint is the operator's explicit «Повторная печать этикеток»
            # action. Once WB has returned a valid sticker and the PDF was built,
            # CHZ + PICKING is sufficient to finish the interrupted warehouse step.
            # Do not fabricate product_scanned_at: keep the audit trail truthful and
            # persist the deliberate PACKED workflow decision separately instead.
            order.assembled_at = order.assembled_at or now
            order.packed_at = order.packed_at or now
            _consume_picking_reservation_nonblocking(db, order, "WB_PACKED_BULK_LABEL_OPERATOR")
            _set_manual_workflow_status(order, "PACKED")
            setattr(order, "manual_status_override", True) if hasattr(order, "manual_status_override") else None
            order.local_status = "PACKED"
        # NEW and marketplace transport/terminal states remain untouched. Merely
        # requesting a reprint can never fabricate SENT or bypass the NEW gate.
        _event(
            db,
            order,
            "ORDER_LABEL_GENERATED",
            data={
                "print_count": order.label_print_count,
                "bulk": True,
                "packed_from_picking": packed_from_picking,
                "product_scan_present": bool(order.product_scanned_at),
            },
        )
    db.commit()
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="wb-order-labels.pdf"'})


@router.get("/wb/supplies/{supply_id}/boxes")
def wb_list_boxes(supply_id: str, credential_id: uuid.UUID, db: Session = Depends(get_db)):
    _, client = _wb(db, credential_id)
    return {"trbx_ids": client.supply_boxes(supply_id)}


@router.post("/wb/supplies/{supply_id}/boxes")
def wb_add_boxes(supply_id: str, payload: WBBoxesRequest, db: Session = Depends(get_db)):
    _, client = _wb(db, payload.credential_id)
    return {"trbx_ids": client.add_supply_boxes(supply_id, payload.amount)}


@router.post("/wb/supplies/{supply_id}/box-labels.pdf")
def wb_box_labels_pdf(supply_id: str, payload: WBBoxLabelsRequest, db: Session = Depends(get_db)):
    _, client = _wb(db, payload.credential_id)
    images = client.supply_box_stickers_png(supply_id, payload.trbx_ids)
    pdf = png_pages_to_pdf(images)
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{supply_id}-boxes.pdf"'})


# ---------------- Ozon FBS ----------------

@router.get("/ozon/warehouses")
def ozon_warehouses(credential_id: uuid.UUID, db: Session = Depends(get_db)):
    _, client = _ozon(db, credential_id)
    warning = None
    try:
        items = client.warehouses()
        if isinstance(items, dict):
            items = items.get("result") or items.get("items") or items.get("warehouses") or []
        if not isinstance(items, list):
            items = []
    except Exception as warehouse_exc:
        # Some restricted Ozon API keys can read FBS postings but cannot call the
        # warehouse catalogue. Keep the cabinet usable and reconstruct the selector
        # from live posting delivery metadata.
        try:
            postings = client.postings(statuses=None, days=31, limit=1000)
        except Exception as posting_exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Не удалось получить склады Ozon: {_external_error_text(warehouse_exc)}. "
                    f"Проверка отправлений также не прошла: {_external_error_text(posting_exc)}"
                ),
            ) from posting_exc
        derived: dict[int, dict[str, Any]] = {}
        for posting in postings or []:
            if not isinstance(posting, dict):
                continue
            delivery_method_id, warehouse_id, warehouse_name = _ozon_delivery_values(posting)
            if warehouse_id is None:
                continue
            item = derived.setdefault(warehouse_id, {
                "warehouse_id": warehouse_id,
                "name": warehouse_name or f"Склад {warehouse_id}",
                "delivery_method_ids": [],
                "derived_from_postings": True,
            })
            if delivery_method_id is not None and delivery_method_id not in item["delivery_method_ids"]:
                item["delivery_method_ids"].append(delivery_method_id)
        items = list(derived.values())
        warning = f"Список складов восстановлен из FBS-отправлений: {_external_error_text(warehouse_exc)}"
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        warehouse_id = item.get("warehouse_id") or item.get("id")
        normalized.append({
            **item,
            "warehouse_id": warehouse_id,
            "name": item.get("name") or item.get("warehouse_name") or f"Склад {warehouse_id}",
        })
    return {"warehouses": normalized, "count": len(normalized), "warning": warning}


@router.get("/ozon/postings")
def ozon_postings(
    credential_id: uuid.UUID,
    warehouse_id: uuid.UUID | None = Query(default=None),
    ozon_warehouse_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=365),
    limit: int = Query(default=100, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    cred, client = _ozon(db, credential_id)
    statuses = [x.strip() for x in status.split(",") if x.strip()] if status else None
    postings = client.postings(statuses=statuses, days=days, limit=limit)
    result = []
    for p in postings:
        delivery_method_id, seller_warehouse_id, seller_warehouse_name = _ozon_delivery_values(p)
        if ozon_warehouse_id is not None and seller_warehouse_id != int(ozon_warehouse_id):
            continue
        products = []
        for product in p.get("products") or []:
            sku = _find_ozon_sku(db, cred.client_id, product)
            products.append({
                "offer_id": product.get("offer_id"),
                "ozon_sku": product.get("sku"),
                "name": product.get("name"),
                "quantity": product.get("quantity"),
                "local_sku": {
                    "id": str(sku.id), "sku_code": sku.sku_code, "article": sku.article,
                    "name": sku.name, "size": sku.size, "barcode": sku.barcode,
                } if sku else None,
                "stock": _stock_for_sku(db, sku, warehouse_id) if sku else [],
            })
        result.append({
            "posting_number": p.get("posting_number"),
            "order_number": p.get("order_number"),
            "status": p.get("status") or p.get("status_alias"),
            "substatus": p.get("substatus") or p.get("status_transcription"),
            "shipment_date": p.get("shipment_date") or p.get("cutoff"),
            "destination": p.get("destination_place_name"),
            "delivery_method": p.get("delivery_method"),
            "delivery_method_id": delivery_method_id,
            "ozon_warehouse_id": seller_warehouse_id,
            "ozon_warehouse_name": seller_warehouse_name,
            "is_packed": _ozon_status(p) in OZON_PACKED_STATUSES,
            "products": products,
        })
    return {"postings": result, "count": len(result)}


@router.post("/ozon/labels.pdf")
def ozon_labels_pdf(payload: OzonLabelsRequest, db: Session = Depends(get_db)):
    _, client = _ozon(db, payload.credential_id)
    pdf = client.package_labels_pdf(payload.posting_numbers)
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": 'inline; filename="ozon-labels.pdf"'})


@router.post("/ozon/ship")
def ozon_ship(payload: OzonShipRequest, db: Session = Depends(get_db)):
    _, client = _ozon(db, payload.credential_id)

    def live_state() -> tuple[str, str, dict[str, Any]]:
        row = client.posting(payload.posting_number)
        status = str(row.get("status") or row.get("status_alias") or "").strip().lower()
        substatus = str(row.get("substatus") or row.get("status_transcription") or "").strip().lower()
        return status, substatus, row

    response: dict[str, Any] | None = None
    recovered = False
    try:
        try:
            response = client.ship(payload.posting_number, payload.packages)
        except HTTPException as exc:
            if exc.status_code not in {502, 504}:
                raise
            status, substatus, _ = live_state()
            if status == "awaiting_deliver" and substatus != "ship_failed":
                recovered = True
                response = {}
            elif status == "awaiting_packaging" or substatus == "ship_failed":
                # The read-back proves that the first mutation did not complete. One
                # replay is now safe; never replay merely because the network timed out.
                response = client.ship(payload.posting_number, payload.packages)
            else:
                raise HTTPException(
                    status_code=409,
                    detail=f"Ozon did not confirm packing; current state is {status or 'unknown'}/{substatus or '-'}.",
                ) from exc

        status, substatus, _ = live_state()
        if substatus == "ship_failed":
            raise HTTPException(status_code=409, detail="Ozon reports ship_failed after packing; refresh the posting before retrying.")
        if status != "awaiting_deliver":
            raise HTTPException(
                status_code=409,
                detail=f"Ozon did not confirm awaiting_deliver after packing; current state is {status or 'unknown'}.",
            )
        result = dict(response or {})
        result["_fc_verified"] = True
        result["_fc_recovered_after_lost_response"] = recovered
        result["_fc_status"] = status
        return result
    finally:
        client.close()


@router.post("/ozon/shipments/from-packed")
def ozon_shipment_from_packed(payload: dict[str, Any], db: Session = Depends(get_db)):
    """Create Ozon FBS carriages and immediately set their exact posting lists.

    Only live postings already reported by Ozon as packed/ready for delivery are
    accepted. Packaging products and approving/dispatching a carriage remain separate
    actions; this endpoint creates the shipment document only.
    """
    try:
        credential_id = uuid.UUID(str(payload.get("credential_id") or ""))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Не выбрано подключение Ozon") from exc
    posting_numbers = list(dict.fromkeys(
        str(value or "").strip()
        for value in (payload.get("posting_numbers") or [])
        if str(value or "").strip()
    ))
    if not posting_numbers:
        raise HTTPException(status_code=400, detail="Нет упакованных отправлений для создания отгрузки")
    if len(posting_numbers) > 1000:
        raise HTTPException(status_code=400, detail="За один раз можно создать отгрузку не более чем из 1000 отправлений")

    _, client = _ozon(db, credential_id)
    try:
        # Fetch the current FBS window once and classify statuses locally. Sending a
        # mixed list of aliases is rejected by some Ozon API revisions.
        live = client.postings(statuses=None, days=31, limit=1000)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ozon не подтвердил упакованные отправления: {_external_error_text(exc)}") from exc
    live_by_number = {
        str(row.get("posting_number") or "").strip(): row
        for row in (live or [])
        if isinstance(row, dict) and row.get("posting_number")
    }
    missing = [number for number in posting_numbers if number not in live_by_number]
    if missing:
        raise HTTPException(
            status_code=409,
            detail=(
                "Ozon больше не показывает часть выбранных отправлений как упакованные: "
                + ", ".join(missing[:20])
                + ("…" if len(missing) > 20 else "")
                + ". Обновите список и повторите действие."
            ),
        )

    groups: dict[int, list[str]] = {}
    group_warehouses: dict[int, int | None] = {}
    for number in posting_numbers:
        posting = live_by_number[number]
        status_value = _ozon_status(posting)
        if status_value not in OZON_PACKED_STATUSES:
            raise HTTPException(status_code=409, detail=f"Отправление {number} ещё не готово к отгрузке: {status_value or 'статус не указан'}")
        delivery_method_id, seller_warehouse_id, _ = _ozon_delivery_values(posting)
        if delivery_method_id is None:
            raise HTTPException(status_code=409, detail=f"У отправления {number} Ozon не вернул способ доставки")
        groups.setdefault(delivery_method_id, []).append(number)
        group_warehouses[delivery_method_id] = seller_warehouse_id

    requested_departure = str(payload.get("departure_date") or "").strip()
    if requested_departure:
        departure = _parse_dt(requested_departure)
        if departure is None:
            raise HTTPException(status_code=400, detail="Некорректная дата отгрузки Ozon")
        if departure.tzinfo is None:
            departure = departure.replace(tzinfo=timezone.utc)
        departure = departure.astimezone(timezone.utc)
        # The browser supplies a date without time. Midnight is already in the past
        # for same-day warehouse work, so use the nearest safe current time.
        if len(requested_departure) == 10 and departure.date() <= _now().date():
            departure = (_now() + timedelta(minutes=5)).replace(second=0, microsecond=0)
    else:
        departure = (_now() + timedelta(minutes=5)).replace(second=0, microsecond=0)
    departure_iso = departure.isoformat().replace("+00:00", "Z")

    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for delivery_method_id, numbers in groups.items():
        carriage_id = None
        try:
            create_data = _ozon_post_json(client, "/v1/carriage/create", {
                "delivery_method_id": delivery_method_id,
                "departure_date": departure_iso,
            })
            carriage_id = _ozon_carriage_id(create_data)
            if carriage_id is None:
                raise RuntimeError(f"Ozon создал отгрузку, но не вернул её ID: {create_data}")
            _ozon_post_json(client, "/v1/carriage/set-postings", {
                "carriage_id": carriage_id,
                "posting_numbers": numbers,
            })
            created.append({
                "carriage_id": carriage_id,
                "delivery_method_id": delivery_method_id,
                "ozon_warehouse_id": group_warehouses.get(delivery_method_id),
                "posting_numbers": numbers,
                "posting_count": len(numbers),
            })
        except Exception as exc:
            failed.append({
                "carriage_id": carriage_id,
                "delivery_method_id": delivery_method_id,
                "posting_numbers": numbers,
                "error": _external_error_text(exc),
            })

    return {
        "ok": not failed,
        "created": created,
        "failed": failed,
        "posting_count": sum(item["posting_count"] for item in created),
        "departure_date": departure_iso,
        "warning": (
            "Часть отгрузок могла быть создана в Ozon. Не нажимайте кнопку повторно: "
            "сначала проверьте созданные ID в кабинете Ozon."
            if failed and (created or any(item.get("carriage_id") for item in failed)) else None
        ),
    }
