from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.core.crypto import decrypt_secret
from app.core.database import SessionLocal
from app.models import MarketplaceCredential, MarketplaceOrder
from app.services.marketplaces import WBClient
from app.services.runtime_reliability import apply_local_db_timeouts, background_heavy_lane, distributed_lock, process_lock

logger = logging.getLogger(__name__)

_STARTED = False
_GUARD = threading.Lock()
_STATUS_LAST: dict[str, float] = {}
_NEW_SYNC_LAST_SUCCESS: dict[str, float] = {}
_NEW_SYNC_LAST_SUCCESS_AT: dict[str, datetime] = {}


def _work_mapping(db, cred: MarketplaceCredential) -> dict[int, uuid.UUID]:
    """Return seller warehouse -> physical warehouse across the credential lineage.

    API keys are sometimes rotated.  The active credential can then have a fresh row
    while the per-physical WORK mapping still lives on its logical predecessor.  Merge
    that lineage, but keep only unambiguous seller->physical pairs so isolation is never
    guessed across warehouses.
    """
    from app.routers.marketplaces import _logical_credential_ids, _normalize_wb_settings, _wb_physical_scopes

    logical_ids = _logical_credential_ids(db, cred)
    credentials = db.execute(select(MarketplaceCredential).where(
        MarketplaceCredential.id.in_(logical_ids)
    )).scalars().all()
    candidates: dict[int, list[uuid.UUID]] = defaultdict(list)
    for logical_cred in credentials or [cred]:
        for physical_raw, scoped_raw in _wb_physical_scopes(logical_cred).items():
            try:
                physical_id = uuid.UUID(str(physical_raw))
            except Exception:
                continue
            for seller_raw, row in _normalize_wb_settings(scoped_raw).items():
                try:
                    seller_id = int(seller_raw)
                except Exception:
                    continue
                if str((row or {}).get("mode") or "HIDDEN").upper() == "WORK":
                    candidates[seller_id].append(physical_id)
    return {seller: values[0] for seller, values in candidates.items() if len(set(values)) == 1}


# FC_WB_WORKER_DEADLOCK_STABILITY_09415G3_20260903
def _sync_new_for_credential(credential_id: uuid.UUID) -> dict[str, int]:
    from app.routers.marketplaces import (
        _logical_credential_ids,
        _reserve_new_wb_orders_immediately,
        _upsert_wb_order,
        _wb_order_warehouse_values,
    )

    db = SessionLocal()
    client: WBClient | None = None
    try:
        cred = db.get(MarketplaceCredential, credential_id)
        if not cred or not cred.is_active or str(cred.provider or "").upper() != "WB":
            return {"seen": 0, "reserved": 0, "shortage": 0, "sync_ok": 0}
        mapping = _work_mapping(db, cred)
        if not mapping:
            return {"seen": 0, "reserved": 0, "shortage": 0, "sync_ok": 0}
        token = decrypt_secret(cred.encrypted_api_key)
        client_id = str(cred.client_id)
        db.commit()
        client = WBClient(token)
        touched: set[tuple[uuid.UUID, int]] = {
            (physical_id, int(seller_id)) for seller_id, physical_id in mapping.items()
        }
        try:
            incoming = client.new_orders()
        except Exception:
            return {"seen": 0, "reserved": 0, "shortage": 0, "sync_ok": 0}
        seen = 0
        # Network I/O is finished. Serialize only the local order upsert transaction so
        # browser sync and the background worker cannot lock the same rows in reverse order.
        with process_lock(f"wb-order-write-client:{client_id}"):
            # FC_K9R59_NEW_RESERVE_SHORT_TX: safety-critical NEW-order local writes fail
            # fast under contention and retry on the next worker pass instead of occupying
            # a pooled connection for minutes. WB HTTP has already finished above.
            apply_local_db_timeouts(db, lock_ms=5000, statement_ms=30000, idle_ms=30000)
            for item in incoming:
                seller_id, _office = _wb_order_warehouse_values(item)
                if seller_id is None or int(seller_id) not in mapping:
                    continue
                _upsert_wb_order(db, cred, item)
                touched.add((mapping[int(seller_id)], int(seller_id)))
                seen += 1
            db.commit()

        created = shortage = 0
        for physical_id, seller_id in sorted(touched, key=lambda x: (str(x[0]), x[1])):
            # FC_PICKING_ORDER_FIRST_SERIALIZATION_09415G5A8K3_20260905
            # The reservation path may flush repaired MarketplaceOrder rows and then lock
            # stock. Keep the whole transaction, including commit, under wb-order-write so
            # it cannot run opposite a picking transaction that binds the same inventory.
            with process_lock(f"wb-order-write-client:{client_id}"):
                apply_local_db_timeouts(db, lock_ms=5000, statement_ms=30000, idle_ms=30000)
                with distributed_lock(db, f"wb-arrival-reserve:{client_id}:{physical_id}", blocking=True):
                    result = _reserve_new_wb_orders_immediately(
                        db,
                        cred=cred,
                        physical_warehouse_id=physical_id,
                        selected_wb_warehouse_id=seller_id,
                    )
                created += int(result.get("created") or 0)
                shortage += int(result.get("shortage") or 0)
                db.commit()
        # FC_K9R27_WB_STOCK_AUTO_CONVERGE: record success only AFTER new orders
        # are upserted and all immediate SALE_RESERVED rows are committed.  The stock
        # publisher can then safely wait for a poll newer than an upward target.
        _NEW_SYNC_LAST_SUCCESS[str(credential_id)] = time.monotonic()
        _NEW_SYNC_LAST_SUCCESS_AT[str(credential_id)] = datetime.now(timezone.utc)
        return {"seen": seen, "reserved": created, "shortage": shortage, "sync_ok": 1}
    except Exception as exc:
        logger.warning("WB sale-reserve NEW sync failed credential=%s error=%s", credential_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"seen": 0, "reserved": 0, "shortage": 0, "sync_ok": 0}
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            try:
                if db.in_transaction():
                    db.rollback()
            finally:
                db.close()



def sync_new_for_scope(
    credential_id: Any,
    physical_warehouse_id: Any,
    seller_warehouse_id: Any,
) -> dict[str, int]:
    """Freshen NEW orders for one exact physical/WB warehouse pair.

    The normal background worker derives warehouse scopes from credential lineage.  A
    rotated key or an intentionally reused seller warehouse can make that mapping
    ambiguous even though the Inventory page has an explicit selected physical scope.
    Stock publication must not remain PENDING forever in that case.  This helper uses
    the exact outbox scope, writes/reserves NEW orders first, commits them, and only then
    records the freshness timestamp that allows an upward WB stock write.
    """
    from app.routers.marketplaces import (
        _reserve_new_wb_orders_immediately,
        _upsert_wb_order,
        _wb_order_warehouse_values,
    )

    db = SessionLocal()
    client: WBClient | None = None
    try:
        cred_id = uuid.UUID(str(credential_id))
        physical_id = uuid.UUID(str(physical_warehouse_id))
        seller_id = int(seller_warehouse_id)
        cred = db.get(MarketplaceCredential, cred_id)
        if not cred or not cred.is_active or str(cred.provider or '').upper() != 'WB':
            return {'seen': 0, 'reserved': 0, 'shortage': 0, 'sync_ok': 0}
        token = decrypt_secret(cred.encrypted_api_key)
        client_id = str(cred.client_id)
        db.commit()
        client = WBClient(token)
        try:
            incoming = client.new_orders()
        except Exception as exc:
            logger.warning('WB scoped NEW sync HTTP failed credential=%s seller=%s error=%s', cred_id, seller_id, exc)
            return {'seen': 0, 'reserved': 0, 'shortage': 0, 'sync_ok': 0}

        seen = 0
        with process_lock(f'wb-order-write-client:{client_id}'):
            apply_local_db_timeouts(db, lock_ms=5000, statement_ms=30000, idle_ms=30000)
            for item in incoming:
                raw_seller, _office = _wb_order_warehouse_values(item)
                if raw_seller is None or int(raw_seller) != seller_id:
                    continue
                _upsert_wb_order(db, cred, item)
                seen += 1
            db.commit()

        with process_lock(f'wb-order-write-client:{client_id}'):
            apply_local_db_timeouts(db, lock_ms=5000, statement_ms=30000, idle_ms=30000)
            with distributed_lock(db, f'wb-arrival-reserve:{client_id}:{physical_id}', blocking=True):
                result = _reserve_new_wb_orders_immediately(
                    db,
                    cred=cred,
                    physical_warehouse_id=physical_id,
                    selected_wb_warehouse_id=seller_id,
                )
            db.commit()

        _NEW_SYNC_LAST_SUCCESS[str(cred_id)] = time.monotonic()
        _NEW_SYNC_LAST_SUCCESS_AT[str(cred_id)] = datetime.now(timezone.utc)
        return {
            'seen': seen,
            'reserved': int(result.get('created') or 0),
            'shortage': int(result.get('shortage') or 0),
            'sync_ok': 1,
        }
    except Exception as exc:
        logger.warning(
            'WB scoped NEW sync failed credential=%s physical=%s seller=%s error=%s',
            credential_id, physical_warehouse_id, seller_warehouse_id, exc,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return {'seen': 0, 'reserved': 0, 'shortage': 0, 'sync_ok': 0}
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            try:
                if db.in_transaction():
                    db.rollback()
            finally:
                db.close()

def _refresh_statuses_for_credential(credential_id: uuid.UUID) -> int:
    """Refresh active WB statuses without holding a pooled DB connection during HTTP.

    FC_K9R59_STATUS_REFRESH_SHORT_TX snapshots scalar order ids first, rolls the read
    transaction back, performs WB HTTP with no DB transaction, then opens a short bounded
    write transaction per batch. This removes the old ORM-across-network pattern that could
    create hidden refresh SELECTs/idle transactions after commit.
    """
    from app.routers.marketplaces import _apply_status, _logical_credential_ids

    db = SessionLocal()
    client: WBClient | None = None
    try:
        apply_local_db_timeouts(db, lock_ms=1500, statement_ms=15000, idle_ms=15000)
        cred = db.get(MarketplaceCredential, credential_id)
        if not cred or not cred.is_active or str(cred.provider or "").upper() != "WB":
            db.rollback()
            return 0
        working_seller_ids = set(_work_mapping(db, cred))
        if not working_seller_ids:
            db.rollback()
            return 0
        logical_ids = _logical_credential_ids(db, cred)
        token = decrypt_secret(cred.encrypted_api_key)
        client_id = str(cred.client_id)
        # Fair rotation: active work orders that were checked least recently go first.
        # 1500 rows = at most six WB status batches and keeps this safety worker bounded.
        pairs = db.execute(
            select(MarketplaceOrder.external_order_num, MarketplaceOrder.id).where(
                MarketplaceOrder.credential_id.in_(logical_ids),
                MarketplaceOrder.provider == "WB",
                MarketplaceOrder.wb_warehouse_id.in_(working_seller_ids),
                MarketplaceOrder.local_status.in_(["NEW", "PICKING", "PACKED"]),
                MarketplaceOrder.external_order_num.is_not(None),
            ).order_by(
                MarketplaceOrder.last_synced_at.asc().nullsfirst(),
                MarketplaceOrder.external_created_at.asc().nullsfirst(),
                MarketplaceOrder.created_at.asc(),
            ).limit(1500)
        ).all()
        snapshots = [(int(num), oid) for num, oid in pairs if num is not None]
        db.rollback()  # release the pooled connection before any WB network wait
        if not snapshots:
            return 0

        client = WBClient(token)
        changed = 0
        # Network first; DB second. Each DB write transaction is independent and bounded.
        for start in range(0, len(snapshots), 250):
            batch = snapshots[start:start + 250]
            ids = [num for num, _ in batch]
            rows = client.order_statuses(ids)
            status_by_num: dict[int, dict[str, Any]] = {}
            for row in rows:
                raw = row.get("id") or row.get("orderId") or row.get("order_id")
                try:
                    status_by_num[int(raw)] = row
                except Exception:
                    continue
            if not status_by_num:
                continue
            batch_order_ids = [oid for num, oid in batch if num in status_by_num]
            if not batch_order_ids:
                continue
            write_db = SessionLocal()
            try:
                # Acquire the in-process writer gate BEFORE starting a DB transaction;
                # otherwise waiting for this Python lock would itself be idle-in-transaction.
                with process_lock(f"wb-order-write-client:{client_id}"):
                    apply_local_db_timeouts(write_db, lock_ms=2500, statement_ms=20000, idle_ms=15000)
                    orders = write_db.execute(select(MarketplaceOrder).where(
                        MarketplaceOrder.id.in_(batch_order_ids)
                    )).scalars().all()
                    for order in orders:
                        try:
                            num = int(order.external_order_num) if order.external_order_num is not None else None
                        except Exception:
                            num = None
                        row = status_by_num.get(num) if num is not None else None
                        if not row:
                            continue
                        before = str(order.local_status or "")
                        _apply_status(write_db, order, row)
                        changed += int(str(order.local_status or "") != before)
                    write_db.commit()
            except Exception:
                try:
                    write_db.rollback()
                except Exception:
                    pass
                raise
            finally:
                write_db.close()
        return changed
    except Exception as exc:
        logger.warning("WB sale-reserve status refresh failed credential=%s error=%s", credential_id, exc)
        try:
            db.rollback()
        except Exception:
            pass
        return 0
    finally:
        try:
            if client is not None:
                client.close()
        finally:
            try:
                if db.in_transaction():
                    db.rollback()
            finally:
                db.close()



def wb_new_order_sync_is_fresh(credential_id: Any, max_age_seconds: float = 40.0) -> bool:
    """True only after a recent successful NEW-order poll in this API process."""
    last = _NEW_SYNC_LAST_SUCCESS.get(str(credential_id), 0.0)
    return bool(last and (time.monotonic() - last) <= max(5.0, float(max_age_seconds)))


def wb_new_order_sync_after(credential_id: Any, minimum_time: Any) -> bool:
    """True only when NEW-order reservation completed after ``minimum_time``.

    This is stronger than a generic freshness window: an upward stock target is not
    allowed to reach WB until at least one NEW-order poll has happened after that target
    was queued.  This closes the normal receive/cancel/resume race without adding WB I/O
    to warehouse requests.
    """
    last = _NEW_SYNC_LAST_SUCCESS_AT.get(str(credential_id))
    if last is None or minimum_time is None:
        return False
    try:
        moment = minimum_time
        if isinstance(moment, str):
            moment = datetime.fromisoformat(moment.replace('Z', '+00:00'))
        if not isinstance(moment, datetime):
            return False
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        else:
            moment = moment.astimezone(timezone.utc)
        return last >= moment
    except Exception:
        return False

def process_wb_sale_reservations_once() -> dict[str, int]:
    db = SessionLocal()
    try:
        credentials = db.execute(
            select(MarketplaceCredential.id).where(
                MarketplaceCredential.provider == "WB",
                MarketplaceCredential.is_active.is_(True),
            )
        ).scalars().all()
    finally:
        db.close()
    seen = reserved = shortage = status_changed = 0
    now = time.monotonic()
    for credential_id in credentials:
        result = _sync_new_for_credential(credential_id)
        # _sync_new_for_credential records freshness only after reservation commits.
        seen += result.get("seen", 0)
        reserved += result.get("reserved", 0)
        shortage += result.get("shortage", 0)
        key = str(credential_id)
        if now - _STATUS_LAST.get(key, 0.0) >= 60.0:
            # FC_K9R59_BACKGROUND_LANE_STATUS: broad status refresh is optional background
            # work. If stock reconciliation currently owns the heavy lane, yield instead
            # of competing for the DB pool; the next 15-second loop retries it.
            with background_heavy_lane(f"wb-status:{credential_id}", blocking=False) as lane:
                if lane:
                    status_changed += _refresh_statuses_for_credential(credential_id)
                    _STATUS_LAST[key] = time.monotonic()
    return {"seen": seen, "reserved": reserved, "shortage": shortage, "status_changed": status_changed}


def _loop() -> None:
    # Give deploy/readiness checks time to finish before any new SALE_RESERVED rows are created.
    # This keeps a failed deployment rollback data-safe. The delay happens only once per API start.
    time.sleep(60.0)
    # A 15-second NEW-order poll makes reservation independent of browser visibility while
    # staying far below WB FBS request limits. Status/cancellation refresh is once per minute.
    while True:
        try:
            process_wb_sale_reservations_once()
        except Exception as exc:
            logger.exception("WB sale-reservation worker iteration failed: %s", exc)
        time.sleep(15.0)


def start_wb_sale_reservation_worker() -> bool:
    global _STARTED
    with _GUARD:
        if _STARTED:
            return False
        threading.Thread(target=_loop, name="fc-wb-sale-reserve", daemon=True).start()
        _STARTED = True
        return True
