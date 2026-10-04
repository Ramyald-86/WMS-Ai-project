from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, select, text

from app.core.crypto import decrypt_secret
from app.core.database import SessionLocal
from app.models import Container, MarketplaceCredential, MarketplaceProduct, SKU
from app.services.marketplaces import WBClient
from app.services.runtime_reliability import apply_local_db_timeouts, background_heavy_lane, distributed_lock
from app.services.stock_core_v2 import active_order_floor_for_identity, effective_fbs_free_for_sku, stock_truth_for_skus, wb_sales_paused

_STARTED = False
_THREAD: threading.Thread | None = None
_GUARD = threading.Lock()
_WAKE = threading.Event()
_MAX_AUTO_ATTEMPTS = 12
_BLOCKED_ERROR_PREFIXES = ('PERMANENT_CHRT:', 'SUPERSEDED:', 'INTERNAL_BARCODE:', 'RETRY_LIMIT:')

# FC_K9R54_OUTBOX_MAINTENANCE_THROTTLE: legacy cleanup UPDATEs are maintenance, not
# part of every publish claim. Running four separate UPDATE/COMMIT sessions every idle
# second created constant PostgreSQL pressure even when the queue had no useful work.
_MAINTENANCE_LOCK = threading.Lock()
_MAINTENANCE_LAST_MONO = 0.0
_MAINTENANCE_INTERVAL = 60.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _claim_token_for_rows(rows: list[dict[str, Any]]) -> str:
    tokens = {str(row.get('_claim_token') or '') for row in rows if str(row.get('_claim_token') or '')}
    return next(iter(tokens)) if len(tokens) == 1 else ''


def _mark_failed(ids: list[str], message: str, attempts: int, claim_token: str) -> None:
    if not ids or not claim_token:
        return
    db = SessionLocal()
    try:
        terminal = int(attempts or 0) >= _MAX_AUTO_ATTEMPTS
        delay = min(60, max(2, 2 ** min(max(1, int(attempts or 1)), 5)))
        next_at = _now() + (timedelta(days=3650) if terminal else timedelta(seconds=delay))
        error = (('RETRY_LIMIT: ' if terminal else '') + str(message or ''))[:900]
        stmt = text("""
            UPDATE fc_marketplace_stock_outbox_v2
            SET status='FAILED',last_error=:error,next_attempt_at=:next
            WHERE id IN :ids AND status='PROCESSING' AND last_error=:claim
        """).bindparams(bindparam('ids', expanding=True))
        db.execute(stmt, {'ids': ids, 'error': error, 'next': next_at, 'claim': claim_token})
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _mark_terminal(ids: list[str], marker: str, message: str, claim_token: str) -> None:
    if not ids or not claim_token:
        return
    db = SessionLocal()
    try:
        stmt = text("""
            UPDATE fc_marketplace_stock_outbox_v2
            SET status='FAILED',last_error=:error,
                next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '3650 days'
            WHERE id IN :ids AND status='PROCESSING' AND last_error=:claim
        """).bindparams(bindparam('ids', expanding=True))
        db.execute(stmt, {
            'ids': ids, 'claim': claim_token,
            'error': (str(marker).rstrip(':') + ': ' + str(message or ''))[:900],
        })
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _mark_superseded(ids: list[str], message: str, claim_token: str) -> None:
    _mark_terminal(ids, 'SUPERSEDED', message, claim_token)


def _is_internal_product_key(value: Any) -> bool:
    code = str(value or '').strip().upper()
    return bool(code and code.startswith(('BOX_', 'FCBOX_', 'FCLOC-')))


def _internal_container_codes(db, client_id: str, codes: list[str]) -> set[str]:
    wanted = {str(code or '').strip() for code in codes if str(code or '').strip()}
    blocked = {code for code in wanted if _is_internal_product_key(code)}
    if not wanted:
        return blocked
    try:
        client_uuid = uuid.UUID(str(client_id))
        with db.begin_nested():
            values = db.execute(select(Container.barcode).where(
                Container.client_id == client_uuid, Container.barcode.in_(sorted(wanted))
            )).scalars().all()
        blocked.update(str(value or '').strip() for value in values if str(value or '').strip())
    except Exception:
        pass
    return blocked


def _current_claim_ids(db, rows: list[dict[str, Any]]) -> set[str]:
    ids = [str(row['id']) for row in rows if row.get('id')]
    claim = _claim_token_for_rows(rows)
    if not ids or not claim:
        return set()
    stmt = text("""
        SELECT id FROM fc_marketplace_stock_outbox_v2
        WHERE id IN :ids AND status='PROCESSING' AND last_error=:claim
    """).bindparams(bindparam('ids', expanding=True))
    return {str(value) for value in db.execute(stmt, {'ids': ids, 'claim': claim}).scalars().all()}


def _latest_scope_rows_by_chrt(
    db, *, client_id: str, warehouse_id: str, code_to_chrt: dict[str, int]
) -> dict[int, dict[str, Any]]:
    """Return one newest non-terminal outbox generation for every mapped WB chrtId.

    This is deliberately one batched DB read, not one query per size.  product_key is mapped
    back to the already-resolved live/cached chrt snapshot supplied by the caller.
    """
    clean_map = {str(code or '').strip(): int(chrt) for code, chrt in code_to_chrt.items() if str(code or '').strip()}
    if not clean_map:
        return {}
    stmt = text("""
        SELECT id,product_key,desired_amount,reason,status,last_error,updated_at
        FROM fc_marketplace_stock_outbox_v2
        WHERE CAST(client_id AS varchar)=:client AND CAST(warehouse_id AS varchar)=:warehouse
          AND provider='WB' AND product_key IN :codes
          AND COALESCE(last_error,'') NOT LIKE 'SUPERSEDED:%'
          AND COALESCE(last_error,'') NOT LIKE 'INTERNAL_BARCODE:%'
        ORDER BY updated_at DESC, id DESC
    """).bindparams(bindparam('codes', expanding=True))
    rows = db.execute(stmt, {
        'client': client_id, 'warehouse': warehouse_id, 'codes': sorted(clean_map),
    }).mappings().all()
    latest: dict[int, dict[str, Any]] = {}
    for raw in rows:
        chrt = clean_map.get(str(raw.get('product_key') or '').strip())
        if chrt is not None and chrt not in latest:
            latest[chrt] = dict(raw)
    return latest


# FC_K9R35_ADD_FBS_IS_EXPLICIT_UPWARD: converting STORAGE_ONLY is an operator resume/latest-truth command.
def _is_operator_resume_reason(value: Any) -> bool:
    reason = str(value or '').upper()
    return reason == 'RESUME_WB'


def _clear_stale_pauses_for_latest_operator_rows(
    db, *, client_id: str, warehouse_id: str, rows: list[dict[str, Any]], sibling_codes: dict[str, set[str]]
) -> int:
    """Clear only pause generations older than the surviving operator command.

    A newer PAUSE that commits after the manual publish has a later updated_at and is not
    cleared.  This also handles sibling barcodes discovered only by the fresh WB size map.
    """
    from app.services.wb_sales_control import global_paused
    if global_paused(db, client_id, warehouse_id):
        return 0
    barcode_cutoff: dict[str, Any] = {}
    for row in rows:
        if not _is_operator_resume_reason(row.get('reason')) or not row.get('updated_at'):
            continue
        code = str(row.get('product_key') or '').strip()
        cutoff = row.get('updated_at')
        for sibling in sibling_codes.get(code, {code}):
            sibling = str(sibling or '').strip()
            if not sibling:
                continue
            previous = barcode_cutoff.get(sibling)
            if previous is None or cutoff > previous:
                barcode_cutoff[sibling] = cutoff
    if not barcode_cutoff:
        return 0
    try:
        client_uuid = uuid.UUID(str(client_id))
    except Exception:
        return 0
    sku_rows = db.execute(select(SKU.id, SKU.barcode).where(
        SKU.client_id == client_uuid, SKU.barcode.in_(sorted(barcode_cutoff))
    )).all()
    updates = [
        {'client': str(client_id), 'warehouse': str(warehouse_id), 'sku': str(sku_id), 'cutoff': barcode_cutoff.get(str(barcode or '').strip())}
        for sku_id, barcode in sku_rows if barcode_cutoff.get(str(barcode or '').strip()) is not None
    ]
    if not updates:
        return 0
    result = db.execute(text("""
        UPDATE fc_wb_sales_pauses
        SET paused=FALSE,updated_at=CURRENT_TIMESTAMP
        WHERE CAST(client_id AS varchar)=:client AND CAST(warehouse_id AS varchar)=:warehouse
          AND CAST(sku_id AS varchar)=:sku AND paused=TRUE AND updated_at <= :cutoff
    """), updates)
    # rowcount for executemany is driver-dependent; the operation itself is what matters.
    return max(0, int(result.rowcount or 0))


def _mark_quarantined(ids: list[str], message: str, claim_token: str) -> None:
    """Park deterministic live mapping failures for the exact claimed generation only."""
    _mark_terminal(ids, 'PERMANENT_CHRT', message, claim_token)


def _quarantine_poisoned_mapping_rows_once() -> int:
    """Stop legacy deterministic chrtId failures from being retried thousands of times."""
    db = SessionLocal()
    try:
        result = db.execute(text(
            """
            UPDATE fc_marketplace_stock_outbox_v2
            SET status='FAILED',
                last_error='PERMANENT_CHRT: ' || COALESCE(last_error,''),
                next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '3650 days'
            WHERE provider='WB'
              AND status IN ('FAILED','PROCESSING')
              AND attempts >= 3
              AND lower(COALESCE(last_error,'')) LIKE '%не удалось определить chrtid/%'
              AND COALESCE(last_error,'') NOT LIKE 'PERMANENT_CHRT:%'
            """
        ))
        db.commit()
        return int(result.rowcount or 0)
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()


def _park_retry_exhausted_rows_once() -> int:
    """Convert legacy thousands-of-attempt rows into a bounded manual-retry state."""
    db = SessionLocal()
    try:
        result = db.execute(text("""
            UPDATE fc_marketplace_stock_outbox_v2
            SET status='FAILED',
                last_error='RETRY_LIMIT: legacy attempts=' || attempts::text || ' | ' || COALESCE(last_error,''),
                next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '3650 days'
            WHERE provider='WB' AND status IN ('FAILED','PROCESSING') AND attempts >= :limit
              AND COALESCE(last_error,'') NOT LIKE 'PERMANENT_CHRT:%'
              AND COALESCE(last_error,'') NOT LIKE 'SUPERSEDED:%'
              AND COALESCE(last_error,'') NOT LIKE 'INTERNAL_BARCODE:%'
              AND COALESCE(last_error,'') NOT LIKE 'RETRY_LIMIT:%'
        """), {'limit': _MAX_AUTO_ATTEMPTS})
        db.commit()
        return int(result.rowcount or 0)
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()


def _supersede_inactive_delete_rows_once() -> int:
    """A legacy DELETE_FREE_AND_PAUSE is obsolete once its local pause is no longer active."""
    db = SessionLocal()
    try:
        result = db.execute(text("""
            UPDATE fc_marketplace_stock_outbox_v2 o
            SET status='FAILED',
                last_error='SUPERSEDED: DELETE_FREE_AND_PAUSE is no longer the active local truth',
                next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '3650 days'
            WHERE o.provider='WB' AND o.reason='DELETE_FREE_AND_PAUSE'
              AND o.status IN ('PENDING','FAILED','PROCESSING')
              AND NOT EXISTS (
                  SELECT 1 FROM fc_wb_sales_pauses p
                  WHERE CAST(p.client_id AS varchar)=CAST(o.client_id AS varchar)
                    AND CAST(p.warehouse_id AS varchar)=CAST(o.warehouse_id AS varchar)
                    AND CAST(p.sku_id AS varchar)=CAST(o.sku_id AS varchar)
                    AND p.paused=TRUE
              )
        """))
        db.commit()
        return int(result.rowcount or 0)
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()


def _quarantine_internal_rows_once() -> int:
    db = SessionLocal()
    try:
        result = db.execute(text("""
            UPDATE fc_marketplace_stock_outbox_v2
            SET status='FAILED',
                last_error='INTERNAL_BARCODE: technical box/location code is not a WB product barcode',
                next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '3650 days'
            WHERE provider='WB' AND status IN ('PENDING','FAILED','PROCESSING')
              AND (left(upper(product_key), 4)='BOX_'
                   OR left(upper(product_key), 6)='FCBOX_'
                   OR left(upper(product_key), 6)='FCLOC-')
              AND COALESCE(last_error,'') NOT LIKE 'INTERNAL_BARCODE:%'
        """))
        db.commit()
        return int(result.rowcount or 0)
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()


def _defer_pending(
    ids: list[str],
    seconds: float = 3.0,
    claim_token: str = '',
    reason: str = 'WAITING_NEW_SYNC: safety preflight is not fresh yet',
) -> None:
    """Return a safety-gated claim to PENDING without burning a retry attempt.

    FC_K9R36_SAFETY_WAIT_NOT_RETRY: claiming increments ``attempts`` before the worker
    knows whether an upward WB write is allowed.  r35 returned NEW-sync waits to PENDING
    but left that increment in place and erased the reason, so a healthy target could sit
    at hundreds/thousands of attempts while the UI only said "in queue".  Neutralize the
    claim increment and preserve an actionable WAITING_NEW_SYNC marker.
    """
    if not ids:
        return
    db = SessionLocal()
    try:
        stmt = text(
            """
            UPDATE fc_marketplace_stock_outbox_v2
            SET status='PENDING',
                attempts=GREATEST(attempts-1,0),
                last_error=:reason,
                next_attempt_at=:next
            WHERE id IN :ids AND status='PROCESSING' AND last_error=:claim
            """
        ).bindparams(bindparam('ids', expanding=True))
        db.execute(stmt, {
            'ids': ids, 'claim': claim_token, 'reason': str(reason or 'WAITING_NEW_SYNC')[:900],
            'next': _now() + timedelta(seconds=max(1.0, float(seconds))),
        })
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _published_amounts(db, client_id: str, warehouse_id: str, codes: list[str]) -> dict[str, int]:
    if not codes:
        return {}
    stmt = text(
        """
        SELECT barcode,published_amount
        FROM fc_wb_stock_publications
        WHERE client_id=:client AND warehouse_id=:warehouse AND barcode IN :codes
        """
    ).bindparams(bindparam('codes', expanding=True))
    try:
        return {
            str(row['barcode']): max(0, int(row.get('published_amount') or 0))
            for row in db.execute(stmt, {'client': client_id, 'warehouse': warehouse_id, 'codes': codes}).mappings().all()
        }
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {}




def _wb_size_targets(
    db,
    *,
    client_id: str,
    warehouse_id: str,
    external_wh: str,
    codes: list[str],
    extra_size_rows: list[dict[str, Any]] | None = None,
    mapping_only: bool = False,
) -> tuple[dict[str, int], dict[str, set[str]], dict[str, int | None]]:
    """Return one canonical sellable target per WB size/chrtId.

    Local physical accounting remains barcode/SKU based, but WB FBS inventory is published
    per chrtId (size). One WB size may contain several seller barcodes. Every barcode in
    that size therefore MUST resolve to the same target: the sum of canonical free stock
    represented by all local sibling barcodes. A barcode with no trustworthy chrtId mapping
    is intentionally left unresolved; falling back to one SKU can silently overwrite the
    whole WB size with a smaller quantity.
    """
    wanted = {str(code or '').strip() for code in codes if str(code or '').strip()}
    if not wanted:
        return {}, {}, {}
    try:
        client_uuid = uuid.UUID(str(client_id))
        warehouse_uuid = uuid.UUID(str(warehouse_id))
        seller_id = int(external_wh)
    except Exception:
        return {}, {}, {}

    # FC_K9R46_RUNTIME_CHRT_REUSE: the worker may already have resolved a complete live
    # WB size earlier in this process. Reuse that row so thousands of confirmation rows do
    # not force another full MarketplaceProduct/catalogue scan. Correctness still falls
    # back to MarketplaceProduct and fresh WB when this acceleration cache is absent.
    runtime_rows: list[dict[str, Any]] = []
    try:
        from app.routers.receiving import _wb_runtime_size_cache_rows
        runtime_rows = _wb_runtime_size_cache_rows(client_uuid, wanted)
    except Exception:
        runtime_rows = []
    runtime_covered = {str(code or '').strip() for row in runtime_rows for code in (row.get('skus') or []) if str(code or '').strip()}
    size_rows: list[dict[str, Any]] = []
    if not wanted.issubset(runtime_covered):
        cached = db.execute(
            select(MarketplaceProduct.sizes).where(
                MarketplaceProduct.client_id == client_uuid,
                MarketplaceProduct.provider == 'WB',
            )
        ).scalars().all()
        for sizes in cached:
            for row in sizes or []:
                if isinstance(row, dict):
                    size_rows.append(row)
    # Runtime/fresh rows are appended last and win stale code->chrt mapping.
    size_rows.extend(row for row in runtime_rows if isinstance(row, dict))
    size_rows.extend(row for row in (extra_size_rows or []) if isinstance(row, dict))

    code_group: dict[str, str] = {}
    group_codes: dict[str, set[str]] = {}
    group_chrt: dict[str, int | None] = {}
    for row in size_rows:
        row_codes = {str(x or '').strip() for x in (row.get('skus') or []) if str(x or '').strip()}
        if not row_codes or not (wanted & row_codes):
            continue
        raw_chrt = row.get('chrtID') or row.get('chrtId') or row.get('chrt_id')
        try:
            chrt = int(raw_chrt)
        except (TypeError, ValueError):
            chrt = None
        if chrt is None:
            continue
        key = f'chrt:{chrt}'
        group_codes.setdefault(key, set()).update(row_codes)
        group_chrt[key] = chrt
        for code in row_codes:
            code_group[code] = key

    # Cache only the matched complete sizes. The next target calculation in the same
    # bulk operator request can then skip another full MarketplaceProduct JSON scan.
    if group_codes:
        try:
            from app.routers.receiving import _wb_runtime_size_cache_put
            _wb_runtime_size_cache_put(client_uuid, [
                {'chrtID': int(group_chrt[key]), 'skus': sorted(values)}
                for key, values in group_codes.items() if group_chrt.get(key) is not None
            ])
        except Exception:
            pass

    # FC_K9R78_CONTROL_MAPPING_ONLY: stop/remove controls only need complete WB size
    # membership. Do not calculate canonical stock truth here; after pause every queued
    # target is zero and enqueue_wb_stock_for_sku() has its own paused fast path.
    if mapping_only:
        siblings: dict[str, set[str]] = {}
        chrt_by_code: dict[str, int | None] = {}
        for code in wanted:
            key = code_group.get(code)
            if key:
                siblings[code] = set(group_codes.get(key, {code}))
                chrt_by_code[code] = group_chrt.get(key)
            else:
                siblings[code] = {code}
                chrt_by_code[code] = None
        return {}, siblings, chrt_by_code

    # Do NOT invent barcode-only WB groups. Unknown mapping is safer than publishing the
    # free quantity of one barcode as if it were the quantity of the whole WB size.
    all_codes = sorted({code for values in group_codes.values() for code in values})
    skus = db.execute(select(SKU).where(
        SKU.client_id == client_uuid,
        SKU.barcode.in_(all_codes),
    )).scalars().all() if all_codes else []
    skus_by_code: dict[str, list[SKU]] = defaultdict(list)
    for sku in skus:
        code = str(sku.barcode or '').strip()
        if code:
            skus_by_code[code].append(sku)

    all_sku_ids = [sku.id for sku in skus]
    truth = stock_truth_for_skus(
        db,
        client_id=client_uuid,
        warehouse_id=warehouse_uuid,
        sku_ids=all_sku_ids,
        wb_warehouse_id=seller_id,
        include_movement=False,
    ) if all_sku_ids else {}

    from app.services.wb_sales_control import paused_ids as sale_pauses
    paused_ids = sale_pauses(db, client_uuid, warehouse_uuid, all_sku_ids)

    targets_by_group: dict[str, int] = {}
    for key, aliases in group_codes.items():
        row_skus: list[SKU] = []
        seen_skus: set[str] = set()
        for code in aliases:
            for sku in skus_by_code.get(code, []):
                sid = str(sku.id)
                if sid not in seen_skus:
                    seen_skus.add(sid)
                    row_skus.append(sku)

        # A valid WB size with no local SKU/balance is an explicit zero in FulfillCore.
        # This lets drift repair clear stale WB stock instead of leaving it for sale.
        if not row_skus:
            targets_by_group[key] = 0
            continue

        truth_groups: dict[tuple[str, ...], dict[str, Any]] = {}
        for sku in row_skus:
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
        # FC_K9R37_EXTERNAL_PICK_WB_TARGET: an uploaded external FBS picking list is
        # an immediate sellability hold. Units remain physical in their source cells
        # until the picker takes them, but WB must already see the reduced free target.
        external_pick_reserved = total('external_pick_reserved')
        floor = active_order_floor_for_identity(
            db,
            client_id=client_uuid,
            warehouse_id=warehouse_uuid,
            sku_ids=[sku.id for sku in row_skus],
            barcodes=set(aliases),
            wb_warehouse_id=seller_id,
        )
        sale_reserved = max(ledger_sale, Decimal(str(floor.get('new') or 0)))
        picking_reserved = max(ledger_picking, Decimal(str(floor.get('picking') or 0)))
        free = max(Decimal(0), physical - storage - sale_reserved - picking_reserved - accounting_pending - external_pick_reserved)
        paused = any(str(sku.id) in paused_ids for sku in row_skus)
        targets_by_group[key] = 0 if paused else max(0, int(free))

    targets: dict[str, int] = {}
    siblings: dict[str, set[str]] = {}
    chrt_by_code: dict[str, int | None] = {}
    for code in wanted:
        key = code_group.get(code)
        if key and key in targets_by_group:
            targets[code] = targets_by_group[key]
            siblings[code] = set(group_codes.get(key, {code}))
            chrt_by_code[code] = group_chrt.get(key)
        else:
            siblings[code] = {code}
            chrt_by_code[code] = None
    return targets, siblings, chrt_by_code


# 0.9.4.15g5a8j: isolate deterministic per-SKU WB errors without turning transient
# network/marketplace failures into an API request storm. A bad barcode/card must not
# prevent valid stock targets from being delivered.
def _is_item_specific_publish_error(exc: Exception) -> bool:
    message = str(exc or '').lower()
    transient = ('timeout', 'timed out', 'connection', 'reset by peer', '429', '502', '503', '504', 'too many requests', 'temporar', 'недоступ')
    if any(x in message for x in transient):
        return False
    item_specific = ('шк не найдены', 'не найден chrtid', 'chrtid', 'один шк', 'несколько шк', 'два разных целевых остатка', 'нет штрихкода')
    return any(x in message for x in item_specific)


def _publish_rows_with_isolation(db, client: WBClient, client_id: str, external_wh: str, rows: list[dict[str, Any]]) -> tuple[int, int]:
    if not rows:
        return 0, 0
    ids = [str(r['id']) for r in rows]
    attempts = max(int(r.get('attempts') or 0) + 1 for r in rows)
    try:
        # All rows in a publish group are one physical warehouse.  Keeping this invariant
        # avoids mixing two local stocks that happen to use the same seller warehouse.
        warehouse_id = str(rows[0].get('warehouse_id') or '')
        if not warehouse_id or any(str(r.get('warehouse_id') or '') != warehouse_id for r in rows):
            raise RuntimeError('Смешаны разные физические склады в одной WB публикации')

        # FC_K9R34_CLAIM_GENERATION_GATE: a newer UPSERT clears/replaces the claim token.
        # Drop stale in-flight rows BEFORE any WB network call.
        active_claim_ids = _current_claim_ids(db, rows)
        rows = [row for row in rows if str(row.get('id')) in active_claim_ids]
        if not rows:
            return 0, 0
        ids = [str(row['id']) for row in rows]
        claim_token = _claim_token_for_rows(rows)

        codes = [str(r.get('product_key') or '').strip() for r in rows if str(r.get('product_key') or '').strip()]
        internal_codes = _internal_container_codes(db, client_id, codes)
        if internal_codes:
            blocked_rows = [row for row in rows if str(row.get('product_key') or '').strip() in internal_codes]
            _mark_terminal(
                [str(row['id']) for row in blocked_rows], 'INTERNAL_BARCODE',
                'technical box/location barcode is excluded from WB stock queue', claim_token,
            )
            rows = [row for row in rows if str(row.get('product_key') or '').strip() not in internal_codes]
            if not rows:
                return 0, len(blocked_rows)
            ids = [str(row['id']) for row in rows]
            codes = [str(r.get('product_key') or '').strip() for r in rows if str(r.get('product_key') or '').strip()]

        previous = _published_amounts(db, client_id, warehouse_id, list(dict.fromkeys(codes)))
        size_targets, sibling_codes, chrt_by_code = _wb_size_targets(
            db, client_id=client_id, warehouse_id=warehouse_id, external_wh=external_wh, codes=codes
        )
        unresolved = [code for code in dict.fromkeys(codes) if size_targets.get(code) is None or chrt_by_code.get(code) is None]
        if unresolved:
            # FC_K9R31_FRESH_CHRT_TARGET_RECOVERY: a stale local card cache must never
            # cause a barcode-level fallback. Resolve the full WB size (including sibling
            # barcodes), then recompute the local chrt target before any stock PUT.
            from app.routers.receiving import _wb_fresh_size_rows
            if db.in_transaction():
                db.rollback()
            fresh_rows = _wb_fresh_size_rows(client, unresolved)
            try:
                from app.routers.receiving import _wb_runtime_size_cache_put
                _wb_runtime_size_cache_put(client_id, fresh_rows)
            except Exception:
                pass
            size_targets, sibling_codes, chrt_by_code = _wb_size_targets(
                db, client_id=client_id, warehouse_id=warehouse_id, external_wh=external_wh,
                codes=codes, extra_size_rows=fresh_rows,
            )
            unresolved = [code for code in dict.fromkeys(codes) if size_targets.get(code) is None or chrt_by_code.get(code) is None]
        quarantined_count = 0
        if unresolved:
            # FC_K9R33_POISON_PARTITION: deterministic unmapped WB/internal barcodes must
            # never force recursive re-resolution of the whole 500-1000 row warehouse
            # snapshot. Park only the unresolved rows and continue publishing every
            # safely mapped row from the same claim in this pass.
            unresolved_set = set(unresolved)
            quarantined_rows = [row for row in rows if str(row.get('product_key') or '').strip() in unresolved_set]
            quarantined_ids = [str(row['id']) for row in quarantined_rows]
            quarantined_count = len(quarantined_rows)
            _mark_quarantined(
                quarantined_ids,
                'Не удалось определить chrtId/полный размер WB для ШК: ' + ', '.join(unresolved[:15]),
                claim_token,
            )
            rows = [row for row in rows if str(row.get('product_key') or '').strip() not in unresolved_set]
            ids = [str(row['id']) for row in rows]
            if not rows:
                return 0, quarantined_count

        # FC_K9R34_SINGLE_LATEST_CHRT: one current command may represent an entire WB size.
        # For each chrtId keep only the newest non-superseded outbox generation. Older
        # sibling rows are parked and can never write the size after the newer command.
        by_chrt: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            code = str(row.get('product_key') or '').strip()
            raw_chrt = chrt_by_code.get(code)
            if raw_chrt is not None:
                by_chrt[int(raw_chrt)].append(row)
        # Map every sibling barcode back to the same resolved chrt so one batched query can
        # select the newest generation for all sizes in the claim.
        code_to_chrt: dict[str, int] = {}
        for chrt_id, chrt_rows in by_chrt.items():
            for row in chrt_rows:
                code = str(row.get('product_key') or '').strip()
                code_to_chrt[code] = chrt_id
                for sibling in sibling_codes.get(code, {code}):
                    if str(sibling or '').strip():
                        code_to_chrt[str(sibling).strip()] = chrt_id
        newest_by_chrt = _latest_scope_rows_by_chrt(
            db, client_id=client_id, warehouse_id=warehouse_id, code_to_chrt=code_to_chrt,
        )
        latest_rows: list[dict[str, Any]] = []
        for chrt_id, chrt_rows in by_chrt.items():
            latest = newest_by_chrt.get(chrt_id)
            current_ids = {str(row['id']) for row in chrt_rows}
            latest_id = str((latest or {}).get('id') or '')
            if latest_id and latest_id not in current_ids:
                _mark_superseded(
                    list(current_ids),
                    f'chrtId {chrt_id}: newer command {latest_id} / {(latest or {}).get("reason") or "unknown"}',
                    claim_token,
                )
                continue
            representative = next((row for row in chrt_rows if str(row['id']) == latest_id), None)
            if representative is None:
                representative = sorted(chrt_rows, key=lambda row: str(row.get('id') or ''))[-1]
            older_ids = [str(row['id']) for row in chrt_rows if str(row['id']) != str(representative['id'])]
            if older_ids:
                _mark_superseded(older_ids, f'chrtId {chrt_id}: replaced by latest sibling command {representative["id"]}', claim_token)
            latest_rows.append(representative)
        rows = latest_rows
        ids = [str(row['id']) for row in rows]
        if not rows:
            return 0, quarantined_count

        # FC_K9R34_FRESH_SIBLING_PAUSE_CUTOFF: manual publish/resume must also clear an
        # old pause belonging to a sibling that was discovered only by the live WB size
        # snapshot.  Never clear a pause newer than this surviving command generation.
        _clear_stale_pauses_for_latest_operator_rows(
            db, client_id=client_id, warehouse_id=warehouse_id, rows=rows, sibling_codes=sibling_codes,
        )
        # Persist the operator's resume intent before the later read transaction is released
        # for WB network I/O. The timestamp cutoff above prevents overwriting a newer PAUSE.
        db.commit()
        # Recompute canonical targets after the safe pause cleanup. This is still local DB
        # truth; no WB stock write has happened yet.
        current_codes = [str(row.get('product_key') or '').strip() for row in rows]
        size_targets, sibling_codes, chrt_by_code = _wb_size_targets(
            db, client_id=client_id, warehouse_id=warehouse_id, external_wh=external_wh,
            codes=current_codes, extra_size_rows=locals().get('fresh_rows') or None,
        )

        amounts: dict[str, int] = {}
        safe_rows: list[dict[str, Any]] = []
        wait_ids: list[str] = []

        # Import lazily: no circular startup dependency, and no marketplace request is
        # made here.  We only inspect the timestamp written by the NEW-order worker.
        from app.services.wb_sale_reservation_worker import wb_new_order_sync_after

        for row in rows:
            code = str(row.get('product_key') or '').strip()
            requested = max(0, int(row.get('desired_amount') or 0))
            if not code:
                raise RuntimeError('У SKU нет штрихкода для публикации WB')
            try:
                sku_id = uuid.UUID(str(row.get('sku_id') or ''))
                client_uuid = uuid.UUID(str(row.get('client_id') or client_id))
                warehouse_uuid = uuid.UUID(str(row.get('warehouse_id') or ''))
            except Exception as exc:
                raise RuntimeError(f'Некорректный идентификатор строки outbox для {code}') from exc
            sku = db.get(SKU, sku_id)
            if not sku or str(sku.client_id) != str(client_uuid):
                raise RuntimeError(f'Локальный SKU для {code} не найден')
            cap = size_targets.get(code)
            if cap is None:
                raise RuntimeError(f'Не рассчитана единая цель WB по chrtId для ШК {code}')

            # FC_K9R27_WB_STOCK_AUTO_CONVERGE: the durable row is a wake-up signal.
            # Publish CURRENT canonical free stock, never an old low target.  Increasing
            # WB is stricter than decreasing it: at least one successful NEW-order poll
            # must have completed after this target was queued/last claimed.  Therefore a
            # just-created WB order cannot be accidentally added back to sale.
            last_published = previous.get(code)
            upward_by_publication = cap > (last_published if last_published is not None else 0)
            reason_upper = str(row.get('reason') or '').upper()
            upward_by_queue = cap > requested or 'DRIFT_UP' in reason_upper
            explicit_republish = any(token in reason_upper for token in ('MANUAL_PUBLISH_WB', 'RESUME_WB', 'ADD_TO_WB_FBS'))
            needs_upward_guard = cap > 0 and (upward_by_publication or upward_by_queue or explicit_republish)
            if needs_upward_guard and not wb_new_order_sync_after(row.get('credential_id'), row.get('updated_at')):
                wait_ids.append(str(row['id']))
                continue

            row['_published_amount'] = cap
            safe_rows.append(row)
            if code in amounts and amounts[code] != cap:
                raise RuntimeError(f'Один ШК {code} получил два разных безопасных целевых остатка')
            amounts[code] = cap

        if wait_ids:
            # Use a separate short transaction.  This is a safety wait, not a red FAILED
            # state.  The 15s NEW-order worker will make the row eligible automatically.
            _defer_pending(
                wait_ids, 3.0, claim_token,
                'WAITING_NEW_SYNC: ждем успешную синхронизацию новых заказов WB перед повышением остатка',
            )

        if not safe_rows:
            try:
                db.rollback()
            except Exception:
                pass
            return 0, quarantined_count

        # FC_K9R34_FINAL_PREWRITE_GATE: close the race window after NEW-order preflight.
        # Recheck BOTH the exact claim token and the newest sibling/chrt generation. A new
        # command on a different barcode of the same size is therefore detected here too.
        active_now = _current_claim_ids(db, safe_rows)
        safe_rows = [row for row in safe_rows if str(row.get('id')) in active_now]
        final_code_to_chrt: dict[str, int] = {}
        for row in safe_rows:
            code = str(row.get('product_key') or '').strip()
            raw_chrt = chrt_by_code.get(code)
            if raw_chrt is None:
                continue
            chrt_id = int(raw_chrt)
            final_code_to_chrt[code] = chrt_id
            for sibling in sibling_codes.get(code, {code}):
                if str(sibling or '').strip():
                    final_code_to_chrt[str(sibling).strip()] = chrt_id
        final_latest = _latest_scope_rows_by_chrt(
            db, client_id=client_id, warehouse_id=warehouse_id, code_to_chrt=final_code_to_chrt,
        )
        still_latest: list[dict[str, Any]] = []
        for row in safe_rows:
            code = str(row.get('product_key') or '').strip()
            raw_chrt = chrt_by_code.get(code)
            latest = final_latest.get(int(raw_chrt)) if raw_chrt is not None else None
            if latest is not None and str(latest.get('id') or '') != str(row.get('id') or ''):
                _mark_superseded(
                    [str(row['id'])],
                    f'chrtId {raw_chrt}: newer command {latest.get("id")} arrived before WB PUT',
                    claim_token,
                )
                continue
            still_latest.append(row)
        safe_rows = still_latest
        if not safe_rows:
            try:
                db.rollback()
            except Exception:
                pass
            return 0, quarantined_count
        amounts = {
            str(row.get('product_key') or '').strip(): int(row.get('_published_amount') or 0)
            for row in safe_rows
        }
        ids = [str(row['id']) for row in safe_rows]

        from app.routers.receiving import _wb_set_stocks_bulk
        verify_started_at=datetime.now(timezone.utc)
        verified_amounts = _wb_set_stocks_bulk(
            db, physical_warehouse_id=warehouse_id, client_id=uuid.UUID(client_id), client=client, warehouse_id=int(external_wh),
            amounts=amounts, release_read_tx=True,
            chrt_map_override={code: int(chrt_by_code[code]) for code in amounts if chrt_by_code.get(code) is not None},
        )

        if not isinstance(verified_amounts,dict) or any(code not in verified_amounts for code in amounts):
            raise RuntimeError('WB publisher did not return verified quantities')
        from app.services.wb_sale_status import record_observations
        for row in safe_rows:
            row['_published_amount']=verified_amounts[str(row['product_key'])]
        record_observations(db,client_id,warehouse_id,rows[0]['credential_id'],external_wh,
            {chrt_by_code[code]:qty for code,qty in verified_amounts.items()},source='PUBLISH',observed_at=verify_started_at)

        # FC_K9R34_POSTVERIFY_LATEST_GATE: WB has confirmed the write, but a newer command
        # may have arrived while the network request was in flight. Such an older claim is
        # NEVER allowed to become DONE/current. The newer row remains/re-enters the queue
        # and converges WB to the latest truth on the next pass.
        active_after_write = _current_claim_ids(db, safe_rows)
        post_code_to_chrt: dict[str, int] = {}
        for row in safe_rows:
            code = str(row.get('product_key') or '').strip()
            raw_chrt = chrt_by_code.get(code)
            if raw_chrt is None:
                continue
            chrt_id = int(raw_chrt)
            post_code_to_chrt[code] = chrt_id
            for sibling in sibling_codes.get(code, {code}):
                if str(sibling or '').strip():
                    post_code_to_chrt[str(sibling).strip()] = chrt_id
        post_latest = _latest_scope_rows_by_chrt(
            db, client_id=client_id, warehouse_id=warehouse_id, code_to_chrt=post_code_to_chrt,
        )
        verified_latest_rows: list[dict[str, Any]] = []
        for row in safe_rows:
            row_id = str(row.get('id') or '')
            if row_id not in active_after_write:
                continue
            code = str(row.get('product_key') or '').strip()
            raw_chrt = chrt_by_code.get(code)
            latest = post_latest.get(int(raw_chrt)) if raw_chrt is not None else None
            if latest is not None and str(latest.get('id') or '') != row_id:
                _mark_superseded(
                    [row_id],
                    f'chrtId {raw_chrt}: newer command {latest.get("id")} arrived during verified WB write',
                    claim_token,
                )
                continue
            verified_latest_rows.append(row)

        published = 0
        for row in verified_latest_rows:
            result = db.execute(
                text("""
                    UPDATE fc_marketplace_stock_outbox_v2
                    SET status='DONE',desired_amount=:published,last_error=NULL,next_attempt_at=CURRENT_TIMESTAMP
                    WHERE id=:id AND status='PROCESSING' AND desired_amount=:desired AND last_error=:claim
                """),
                {'id': str(row['id']), 'desired': int(row.get('desired_amount') or 0), 'claim': claim_token,
                 'published': int(row.get('_published_amount', row.get('desired_amount') or 0))},
            )
            if result.rowcount:
                published_amount = int(row.get('_published_amount', row.get('desired_amount') or 0))
                for publication_code in sorted(sibling_codes.get(str(row['product_key']), {str(row['product_key'])})):
                    db.execute(
                        text("""
                            INSERT INTO fc_wb_stock_publications(
                                client_id,warehouse_id,barcode,wb_warehouse_id,
                                published_amount,status,last_error,updated_at
                            ) VALUES(:client,:warehouse,:barcode,:wb,:amount,'PUBLISHED',NULL,NOW())
                            ON CONFLICT(client_id,warehouse_id,barcode) DO UPDATE SET
                                wb_warehouse_id=EXCLUDED.wb_warehouse_id,
                                published_amount=EXCLUDED.published_amount,status='PUBLISHED',
                                last_error=NULL,updated_at=NOW()
                        """),
                        {'client': client_id, 'warehouse': str(row['warehouse_id']), 'barcode': publication_code, 'wb': int(external_wh), 'amount': published_amount},
                    )
                published += 1
        db.commit()
        return published, quarantined_count
    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        # Rows already deferred for NEW-order freshness are PENDING and must never be
        # pulled back into recursive deterministic-error isolation during the same WB I/O.
        retry_rows = list(locals().get('safe_rows') or rows)
        retry_ids = [str(r['id']) for r in retry_rows]
        if len(retry_rows) > 1 and _is_item_specific_publish_error(exc):
            middle = max(1, len(retry_rows) // 2)
            left = _publish_rows_with_isolation(db, client, client_id, external_wh, retry_rows[:middle])
            right = _publish_rows_with_isolation(db, client, client_id, external_wh, retry_rows[middle:])
            return left[0] + right[0], quarantined_count + left[1] + right[1]
        _mark_failed(retry_ids, str(exc), attempts, _claim_token_for_rows(retry_rows))
        return 0, quarantined_count + len(retry_rows)



def _reconcile_stale_targets_once(limit: int = 5000) -> dict[str, int]:
    """Refresh durable WB targets without a multi-minute DB transaction.

    FC_K9R59_RECONCILE_SHORT_TRANSACTIONS snapshots outbox rows, closes that read
    transaction, computes each physical/WB scope in its own bounded read transaction,
    then applies changed targets in one short write. No WB network I/O occurs here.
    """
    db = SessionLocal()
    try:
        apply_local_db_timeouts(db, lock_ms=1000, statement_ms=15000, idle_ms=10000)
        raw_rows = db.execute(text("""
            SELECT id,client_id,warehouse_id,sku_id,external_warehouse_id,product_key,desired_amount,status,last_error
            FROM fc_marketplace_stock_outbox_v2
            WHERE provider='WB' AND external_warehouse_id IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT :limit
        """), {'limit': max(1, min(int(limit), 10000))}).mappings().all()
        snapshots = [dict(row) for row in raw_rows]
        db.rollback()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return {'checked': 0, 'requeued': 0}
    finally:
        db.close()
    if not snapshots:
        return {'checked': 0, 'requeued': 0}

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in snapshots:
        grouped[(str(raw['client_id']), str(raw['warehouse_id']), str(raw['external_warehouse_id']))].append(raw)

    updates: list[dict[str, Any]] = []
    checked = 0
    for (client_id, warehouse_id, external_wh), rows in grouped.items():
        # FC_K9R54_OUTBOX_SKIP_TERMINAL_BEFORE_TRUTH: exclude terminal poison before
        # FC_K9R31_RECONCILE_CHRT_BATCH: one canonical batched truth calculation per scope.
        eligible_rows = [row for row in rows if not str(row.get('last_error') or '').startswith(
            ('SUPERSEDED:', 'INTERNAL_BARCODE:', 'RETRY_LIMIT:')
        )]
        if not eligible_rows:
            continue
        group_db = SessionLocal()
        try:
            apply_local_db_timeouts(group_db, lock_ms=1000, statement_ms=20000, idle_ms=15000)
            size_targets, _siblings, _chrts = _wb_size_targets(
                group_db, client_id=client_id, warehouse_id=warehouse_id, external_wh=external_wh,
                codes=[str(row.get('product_key') or '') for row in eligible_rows],
            )
            # Read-only truth calculation must release its connection before Python walks
            # the next scope. This is the key r59 pressure guard.
            group_db.rollback()
        except Exception:
            try:
                group_db.rollback()
            except Exception:
                pass
            continue
        finally:
            group_db.close()
        for row in eligible_rows:
            checked += 1
            code = str(row.get('product_key') or '').strip()
            target = size_targets.get(code)
            if target is None:
                continue
            old = max(0, int(row.get('desired_amount') or 0))
            marker = str(row.get('last_error') or '')
            quarantined = marker.startswith('PERMANENT_CHRT:')
            if old == target and not quarantined:
                continue
            updates.append({'id': str(row['id']), 'target': target})

    if not updates:
        return {'checked': checked, 'requeued': 0}
    write_db = SessionLocal()
    try:
        apply_local_db_timeouts(write_db, lock_ms=2500, statement_ms=20000, idle_ms=10000)
        write_db.execute(text("""
            UPDATE fc_marketplace_stock_outbox_v2
            SET desired_amount=:target,reason='AUTO_TARGET_RECONCILE',status='PENDING',attempts=0,
                next_attempt_at=CURRENT_TIMESTAMP,last_error=NULL,updated_at=CURRENT_TIMESTAMP
            WHERE id=:id AND (desired_amount IS DISTINCT FROM :target OR COALESCE(last_error,'') LIKE 'PERMANENT_CHRT:%')
        """), updates)
        write_db.commit()
        return {'checked': checked, 'requeued': len(updates)}
    except Exception:
        try:
            write_db.rollback()
        except Exception:
            pass
        return {'checked': checked, 'requeued': 0}
    finally:
        write_db.close()


def _run_legacy_maintenance_if_due() -> dict[str, int]:
    global _MAINTENANCE_LAST_MONO
    now_mono = time.monotonic()
    with _MAINTENANCE_LOCK:
        if now_mono - float(_MAINTENANCE_LAST_MONO or 0.0) < _MAINTENANCE_INTERVAL:
            return {"quarantined": 0, "retry_limited": 0, "superseded": 0, "internal_blocked": 0}
    # FC_K9R59_BACKGROUND_LANE_MAINTENANCE: cleanup is optional. Never compete with a
    # broad order-status or stock-truth pass for DB resources. A skipped pass is retried
    # on the next worker iteration because the cadence timestamp is not advanced.
    with background_heavy_lane("outbox-maintenance", blocking=False) as lane:
        if not lane:
            return {"quarantined": 0, "retry_limited": 0, "superseded": 0, "internal_blocked": 0}
        with _MAINTENANCE_LOCK:
            now_mono = time.monotonic()
            if now_mono - float(_MAINTENANCE_LAST_MONO or 0.0) < _MAINTENANCE_INTERVAL:
                return {"quarantined": 0, "retry_limited": 0, "superseded": 0, "internal_blocked": 0}
            _MAINTENANCE_LAST_MONO = now_mono
        return {
            "quarantined": _quarantine_poisoned_mapping_rows_once(),
            "retry_limited": _park_retry_exhausted_rows_once(),
            "superseded": _supersede_inactive_delete_rows_once(),
            "internal_blocked": _quarantine_internal_rows_once(),
        }


# FC_K9R31_WB_CHRT_SNAPSHOT_CONVERGENCE
def process_stock_outbox_once(limit: int = 1000) -> dict[str, int]:
    """Publish durable absolute WB stock targets outside warehouse transactions.

    Rows are claimed with SKIP LOCKED. If a newer reserve changes the same dedupe row while
    WB I/O is in flight, enqueue_marketplace_stock resets it to PENDING; the old worker then
    cannot mark it DONE, so the newer absolute amount is sent on the next pass.
    """
    # FC_K9R33_POISON_QUARANTINE is preserved: deterministic mapping poison is still parked.
    # FC_K9R34_SINGLE_LATEST_HOUSEKEEPING: sanitize r33-era poison/stale work before
    # claim. These are local outbox state changes only; no warehouse balance or WB write.
    maintenance = _run_legacy_maintenance_if_due()
    quarantined = int(maintenance.get("quarantined") or 0)
    retry_limited = int(maintenance.get("retry_limited") or 0)
    stale_delete_superseded = int(maintenance.get("superseded") or 0)
    internal_blocked = int(maintenance.get("internal_blocked") or 0)
    db = SessionLocal()
    try:
        # FC_K9R59_OUTBOX_CLAIM_SHORT_TX: queue claiming must never wait behind a
        # warehouse writer long enough to consume the API pool. SKIP LOCKED + local
        # statement/lock limits make this a bounded bookkeeping transaction.
        apply_local_db_timeouts(db, lock_ms=1500, statement_ms=10000, idle_ms=10000)
        rows = db.execute(
            text(
                """
                SELECT id,client_id,warehouse_id,sku_id,provider,credential_id,
                       external_warehouse_id,product_key,desired_amount,reason,attempts,status,updated_at
                FROM fc_marketplace_stock_outbox_v2
                WHERE (
                    status IN ('PENDING','FAILED') AND next_attempt_at <= CURRENT_TIMESTAMP
                    AND COALESCE(last_error,'') NOT LIKE 'PERMANENT_CHRT:%'
                    AND COALESCE(last_error,'') NOT LIKE 'SUPERSEDED:%'
                    AND COALESCE(last_error,'') NOT LIKE 'INTERNAL_BARCODE:%'
                    AND COALESCE(last_error,'') NOT LIKE 'RETRY_LIMIT:%'
                ) OR (
                    status='PROCESSING' AND (
                        (COALESCE(last_error,'') LIKE 'CLAIM:%' AND next_attempt_at <= CURRENT_TIMESTAMP)
                        OR (
                            COALESCE(last_error,'') NOT LIKE 'CLAIM:%'
                            AND COALESCE(last_error,'') NOT LIKE 'PERMANENT_CHRT:%'
                            AND COALESCE(last_error,'') NOT LIKE 'SUPERSEDED:%'
                            AND COALESCE(last_error,'') NOT LIKE 'INTERNAL_BARCODE:%'
                            AND COALESCE(last_error,'') NOT LIKE 'RETRY_LIMIT:%'
                            AND updated_at < CURRENT_TIMESTAMP - INTERVAL '2 minutes'
                        )
                    )
                )
                ORDER BY
                    -- FC_K9R60_ZERO_QUEUE_PRIORITY: an operator stop-sales command is a
                    -- safety action. It must jump ahead of stale/background inventory work.
                    CASE
                        WHEN upper(COALESCE(reason,'')) IN ('PAUSE_WB','DELETE_FREE_AND_PAUSE') THEN 0
                        WHEN upper(COALESCE(reason,''))='RESUME_WB' THEN 1
                        ELSE 2
                    END,
                    CASE status WHEN 'PENDING' THEN 0 WHEN 'FAILED' THEN 1 ELSE 2 END,
                    CASE WHEN upper(COALESCE(reason,'')) IN ('PAUSE_WB','DELETE_FREE_AND_PAUSE') THEN updated_at END DESC NULLS LAST,
                    updated_at ASC
                LIMIT :limit
                FOR UPDATE SKIP LOCKED
                """
            ),
            {"limit": max(1, min(int(limit), 1000))},
        ).mappings().all()
        if not rows:
            db.rollback()
            return {"claimed": 0, "published": 0, "failed": 0, "quarantined": quarantined, "retry_limited": retry_limited, "superseded": stale_delete_superseded, "internal_blocked": internal_blocked}
        ids = [str(r["id"]) for r in rows]
        claim_token = 'CLAIM:' + uuid.uuid4().hex
        claim_stmt = text(
            """
            UPDATE fc_marketplace_stock_outbox_v2
            SET status='PROCESSING',attempts=attempts+1,last_error=:claim,
                next_attempt_at=CURRENT_TIMESTAMP + INTERVAL '2 minutes'
            WHERE id IN :ids
            """
        ).bindparams(bindparam("ids", expanding=True))
        db.execute(claim_stmt, {"ids": ids, 'claim': claim_token})
        db.commit()
    except Exception:
        db.rollback()
        db.close()
        return {"claimed": 0, "published": 0, "failed": 0, "quarantined": quarantined, "retry_limited": retry_limited, "superseded": stale_delete_superseded, "internal_blocked": internal_blocked}
    finally:
        if db.is_active:
            db.close()

    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(
            str(row.get("credential_id") or ""), str(row.get("external_warehouse_id") or ""),
            str(row.get("client_id") or ""), str(row.get("warehouse_id") or ""),
        )].append({**dict(row), '_claim_token': claim_token})

    published = failed = 0
    for (credential_id, external_wh, client_id, _warehouse_id), group in groups.items():
        group_ids = [str(r['id']) for r in group]
        attempts = max(int(r.get('attempts') or 0) + 1 for r in group)
        if not credential_id or not external_wh:
            _mark_failed(group_ids, 'Не задан credential_id или рабочий склад WB', attempts, _claim_token_for_rows(group))
            failed += len(group)
            continue
        db2 = SessionLocal()
        client: WBClient | None = None
        try:
            apply_local_db_timeouts(db2, lock_ms=1500, statement_ms=10000, idle_ms=10000)
            cred = db2.get(MarketplaceCredential, uuid.UUID(credential_id))
            if not cred or not cred.is_active or str(cred.provider or '').upper() != 'WB':
                raise RuntimeError('Подключение WB для автоматической публикации остатка недоступно')
            if str(cred.client_id) != str(client_id):
                raise RuntimeError('Подключение WB не принадлежит клиенту строки очереди')
            if any(str(row.get('provider') or '').upper() != 'WB' for row in group):
                raise RuntimeError('Строка очереди относится к другому маркетплейсу')
            token = decrypt_secret(cred.encrypted_api_key)
            db2.rollback()

            # FC_K9R28_WB_QUEUE_DRAIN: a manual/upward queue target must not depend on
            # an ambiguous credential-lineage background mapping. If no NEW-order poll
            # newer than a positive target exists, perform one exact-scope poll/reserve
            # for THIS physical+seller warehouse before the WB stock write.
            from app.services.wb_sale_reservation_worker import wb_new_order_sync_after, sync_new_for_scope
            # FC_K9R32_RESOLVE_QUEUE_SCOPE_PREFLIGHT: an uncached WB barcode may enter the
            # durable queue with desired_amount=0 only as a wake-up marker. Manual publish
            # / resume / drift-up still require an exact-scope NEW-order refresh before the
            # worker is allowed to resolve and potentially raise WB stock.
            def _scope_row_needs_sync(row):
                reason = str(row.get('reason') or '').upper()
                explicit_upward = any(token in reason for token in (
                    'MANUAL_PUBLISH_WB', 'MANUAL_PUBLISH_CHRT', 'RESUME_WB', 'ADD_TO_WB_FBS', 'DRIFT_UP',
                ))
                return (max(0, int(row.get('desired_amount') or 0)) > 0 or explicit_upward) and not wb_new_order_sync_after(
                    row.get('credential_id'), row.get('updated_at')
                )
            needs_scope_sync = any(_scope_row_needs_sync(row) for row in group)
            if needs_scope_sync:
                sync_result = sync_new_for_scope(credential_id, _warehouse_id, external_wh)
                if not int((sync_result or {}).get('sync_ok') or 0):
                    # FC_K9R31_SCOPE_SNAPSHOT_GUARD: do not publish only the downward half
                    # of a warehouse snapshot while upward rows are blocked by a failed NEW
                    # order refresh. Keep the whole scope pending and retry as one snapshot.
                    _defer_pending(
                        group_ids, 5.0, _claim_token_for_rows(group),
                        'WAITING_NEW_SYNC: проверка новых заказов WB временно не подтверждена; повтор автоматический',
                    )
                    continue

            client = WBClient(token)
            # FC_K9R38_WB_PUBLISH_SCOPE_LOCK: serialize the actual WB write/verification
            # only AFTER NEW-order sync has released its sale-reserve locks. Direct r38
            # write-off holds sale-reserve locks first and then takes this scope lock, so
            # this ordering cannot deadlock and an old claimed generation cannot overwrite
            # the synchronously confirmed task target afterward.
            # FC_K9R59_OUTBOX_YIELD_TO_FOREGROUND_LOCK: the background publisher never
            # waits while a synchronous warehouse write owns this scope. Yield the claim
            # back to PENDING and retry shortly instead of consuming a second pool slot.
            with distributed_lock(db2, f'wb-stock-publish:{client_id}:{_warehouse_id}:{external_wh}', blocking=False) as acquired:
                if not acquired:
                    _defer_pending(
                        group_ids, 1.5, _claim_token_for_rows(group),
                        'WAITING_WB_PUBLISH_LOCK: складская операция имеет приоритет; повтор автоматический',
                    )
                    continue
                # WB accepts up to 1000 chrtIds per stock request. Claim/publish the complete
                # same-scope set as one snapshot; recursive isolation still splits only when WB
                # identifies a deterministic bad card. This avoids 100-row partial convergence.
                ok, bad = _publish_rows_with_isolation(db2, client, client_id, external_wh, group)
                published += ok
                failed += bad
        except Exception as exc:
            try:
                db2.rollback()
            except Exception:
                pass
            _mark_failed(group_ids, str(exc), attempts, _claim_token_for_rows(group))
            failed += len(group)
        finally:
            try:
                if client is not None:
                    client.close()
            finally:
                db2.close()
    return {'claimed': len(rows), 'published': published, 'failed': failed, 'quarantined': quarantined, 'retry_limited': retry_limited, 'superseded': stale_delete_superseded, 'internal_blocked': internal_blocked}


def wake_stock_outbox_worker() -> None:
    """Wake the durable worker and self-heal a missed startup.

    FC_K9R36_OUTBOX_WAKE_SELF_HEAL: API startup wraps several reliability initializers in
    one guarded block.  If an earlier initializer fails, the HTTP API can still be alive
    while the stock worker was never started.  An explicit operator WB command must be
    sufficient to start the worker instead of leaving valid rows PENDING forever.
    """
    if not _STARTED or _THREAD is None or not _THREAD.is_alive():
        try:
            start_stock_outbox_worker()
        except Exception:
            pass
    _WAKE.set()


def _worker_loop() -> None:
    # Preserve deploy rollback safety unless an operator explicitly queues a command.
    # Manual stock actions may wake the worker immediately; ordinary startup still waits.
    _WAKE.wait(60.0)
    _WAKE.clear()
    next_reconcile = 0.0
    while True:
        try:
            # FC_K9R60_FOREGROUND_QUEUE_BEFORE_RECONCILE: explicit durable commands are
            # drained before optional broad repair. A manual zero must never sit behind a
            # 5k-row truth sweep that exists only as a safety net.
            result = process_stock_outbox_once()
            now = time.monotonic()
            if now >= next_reconcile and not result.get('claimed'):
                # FC_K9R59_BACKGROUND_LANE_RECONCILE: broad truth repair is a safety net,
                # not a foreground action. If another broad worker is active, yield for five
                # seconds; explicit queue rows have already had the first chance this loop.
                with background_heavy_lane("outbox-reconcile", blocking=False) as lane:
                    if lane:
                        _reconcile_stale_targets_once()
                        # FC_K9R54_STALE_TARGET_CADENCE: keep the 30-second broad safety
                        # cadence; r59 only prevents it from competing with another heavy pass.
                        now = time.monotonic()
                        next_reconcile = now + 30.0
                    else:
                        next_reconcile = time.monotonic() + 5.0
            elif result.get('claimed') and now >= next_reconcile:
                next_reconcile = now + 1.0
            # Fast when there is work, quiet when idle. An explicit operator command wakes
            # this wait without starting a second worker or a duplicate WB publisher.
            _WAKE.wait(0.20 if result.get('claimed') else 1.0)
            _WAKE.clear()
        except Exception:
            _WAKE.wait(2.0)
            _WAKE.clear()


def start_stock_outbox_worker() -> bool:
    global _STARTED, _THREAD
    with _GUARD:
        if _THREAD is not None and _THREAD.is_alive():
            _STARTED = True
            return False
        thread = threading.Thread(target=_worker_loop, name="fc-stock-outbox", daemon=True)
        thread.start()
        _THREAD = thread
        _STARTED = True
        return True
