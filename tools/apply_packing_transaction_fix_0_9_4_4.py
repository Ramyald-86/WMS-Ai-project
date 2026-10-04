#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

MARKER = "0.9.4.4 PACKING TRANSACTION FIX"


def read_py(path: Path) -> str:
    s = path.read_text(encoding="utf-8")
    ast.parse(s)
    return s


def function_bounds(src: str, name: str) -> tuple[int, int]:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node.lineno, node.end_lineno or node.lineno
    raise RuntimeError(f"function {name} not found")


def replace_function(src: str, name: str, replacement: str) -> str:
    start, end = function_bounds(src, name)
    lines = src.splitlines(True)
    # Include decorators immediately above the function.
    first = start - 1
    while first > 0 and lines[first - 1].lstrip().startswith("@"):
        first -= 1
    lines[first:end] = [replacement.rstrip() + "\n\n"]
    out = "".join(lines)
    ast.parse(out)
    return out


BACKGROUND_HELPER = r'''
# 0.9.4.4 PACKING TRANSACTION FIX
# WB network I/O must never hold a PostgreSQL transaction or block the packing table.
# This helper uses only immutable primitives and the already-created WB client. It does
# not touch SQLAlchemy from the worker thread. Metadata reconciliation confirms the
# submission on a later bounded sync.
def _submit_wb_chz_background(client: WBClient, order_number: int, chz: str, *, source: str = "PACKING") -> None:
    def _run() -> None:
        started = time.perf_counter()
        try:
            client.set_sgtin(int(order_number), [chz])
            logger.info(
                "FC_CHZ_BG_SUBMIT source=%s order_num=%s outcome=ok elapsed_ms=%.1f",
                source, order_number, (time.perf_counter() - started) * 1000.0,
            )
        except Exception as exc:
            logger.warning(
                "FC_CHZ_BG_SUBMIT source=%s order_num=%s outcome=error elapsed_ms=%.1f error=%s",
                source, order_number, (time.perf_counter() - started) * 1000.0, exc,
            )

    threading.Thread(target=_run, name=f"fc-chz-{order_number}", daemon=True).start()
'''


REFRESH_META = r'''def _refresh_wb_metadata_batch(db: Session, client: WBClient, orders: list[MarketplaceOrder]) -> dict[str, int]:
    """Refresh WB metadata without ever waiting on WB inside a DB transaction.

    0.9.4.4: every external WB call has an explicit transaction barrier. This prevents
    SELECT/UPDATE marketplace_orders sessions from becoming `idle in transaction` while
    WB takes 5-15+ seconds to answer.
    """
    by_num = {o.external_order_num: o for o in orders if o.external_order_num is not None}
    if not by_num:
        return {"checked": 0, "changed": 0}

    # Build immutable request data first, then close the DB transaction before HTTP.
    meta_nums = list(by_num)
    db.commit()
    changed = 0
    try:
        meta_rows = client.order_meta(meta_nums)
    except HTTPException as exc:
        if exc.status_code == 429:
            return {"checked": 0, "changed": 0}
        raise

    for item in meta_rows:
        raw_id = item.get("id") or item.get("orderId") or item.get("order_id")
        try:
            num = int(raw_id)
        except (TypeError, ValueError):
            continue
        order = by_num.get(num)
        if order and _apply_wb_order_meta(db, order, item):
            changed += 1

    sticker_candidates = [
        o for o in orders
        if o.external_order_num is not None and o.supplier_status in {"confirm", "complete"} and not _wb_sticker_data(o)
    ]
    sticker_by_num = {o.external_order_num: o for o in sticker_candidates if o.external_order_num is not None}
    sticker_nums = list(sticker_by_num)
    # Persist metadata changes and close the transaction before sticker HTTP.
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
        for item in stickers:
            raw_id = item.get("orderId") or item.get("id")
            try:
                num = int(raw_id)
            except (TypeError, ValueError):
                continue
            order = sticker_by_num.get(num)
            if order and _apply_wb_order_sticker(db, order, item):
                changed += 1
        # Never carry the DB transaction into the next WB request.
        db.commit()

    # Retrying pending CHZ through the old DB-bound helper could hold an ORM transaction
    # during set_sgtin/order_meta. Submit a small bounded retry set in DB-free threads;
    # the metadata pass above/later pass remains authoritative for acceptance.
    pending_snapshot: list[tuple[int, str]] = []
    for o in orders:
        if len(pending_snapshot) >= 10:
            break
        if not (o.chz_code and _has_pending_wb_chz(o)):
            continue
        try:
            pending_snapshot.append((_wb_order_number(o), str(o.chz_code)))
        except Exception:
            continue
    db.commit()
    for order_number, chz in pending_snapshot:
        _submit_wb_chz_background(client, order_number, chz, source="META_RETRY")

    checked_at = _now().isoformat()
    for order in orders:
        raw = dict(order.raw_data or {})
        raw["_fc_meta_checked_at"] = checked_at
        order.raw_data = raw
        if _reconcile_local_status(db, order, "WB_META_SWEEP"):
            changed += 1
    return {"checked": len(orders), "changed": changed}
'''


SCAN_CHZ = r'''@router.post("/wb/packing/scan-chz")
def wb_scan_chz(payload: WBScanCHZRequest, warehouse_id: uuid.UUID | None = Query(default=None), station_code: str | None = Query(default=None), device_id: str | None = Query(default=None), wb_label_width: float = Query(default=58, ge=25, le=120), wb_label_height: float = Query(default=40, ge=25, le=120), wb_label_rotate: int = Query(default=-1), wb_label_scale: float = Query(default=100, ge=85, le=108), wb_label_offset_x: float = Query(default=0, ge=-5, le=5), wb_label_offset_y: float = Query(default=0, ge=-5, le=5), defer_print: int = Query(default=1, ge=0, le=1), db: Session = Depends(get_db)):
    """0.9.4.4 local-first packing: DB commit first, WB submission in background.

    The physical warehouse must never stop because WB is slow. CHZ is saved exactly,
    the physical reservation is consumed, PACKED is committed, then a DB-free worker
    submits SGTIN. Metadata reconciliation later clears the pending marker when WB accepts.
    """
    timing: dict[str, Any] = {"_started": time.perf_counter(), "trace_id": uuid.uuid4().hex[:12]}
    _t = time.perf_counter()
    _, client = _wb(db, payload.credential_id)
    order = _order_lookup(db, payload.order_id, payload.credential_id)
    cred = _credential(db, payload.credential_id, "WB")
    _assert_wb_work(cred, order, warehouse_id)
    _timing_add_ms(timing, "db_precheck_ms", _t)
    station = normalize_station_code(station_code) if station_code else None
    if station and not assert_or_renew_packing_lease(db, order_id=order.id, station_code=station, device_id=device_id):
        raise HTTPException(status_code=409, detail="Этот заказ сейчас закреплён за другим упаковочным столом. Обновите упаковку и отсканируйте товар заново.")
    if not order.product_scanned_at:
        raise HTTPException(status_code=409, detail="Сначала отсканируйте штрихкод товара, затем код маркировки.")

    chz = _normalize_chz(payload.chz)
    if not chz:
        raise HTTPException(status_code=400, detail="Пустой код маркировки")
    if (order.wb_status or "").strip().lower() in WB_PHYSICALLY_LEFT_STATUSES | CANCEL_WB_STATUSES | {"sold"}:
        raise HTTPException(status_code=409, detail="WB уже показывает заказ как уехавший/завершённый. Код маркировки к нему прикреплять нельзя.")
    if order.chz_code and order.chz_code != chz:
        shown = order.chz_code.replace("\x1d", "␝")
        if len(shown) > 70:
            shown = shown[:42] + "…" + shown[-18:]
        raise HTTPException(status_code=409, detail=f"К этому заказу уже привязан другой ЧЗ: {shown}. Откройте карточку заказа и замените код вручную.")

    duplicate = db.execute(
        select(MarketplaceOrder).where(
            MarketplaceOrder.credential_id == payload.credential_id,
            MarketplaceOrder.chz_code == chz,
            MarketplaceOrder.id != order.id,
        )
    ).scalars().first()
    if duplicate:
        raise HTTPException(status_code=409, detail=f"Этот ЧЗ уже привязан к заказу WB №{duplicate.external_order_id}. Один и тот же ЧЗ нельзя привязать к разным заказам.")

    first_local_save = not bool(order.chz_code)
    order.chz_code = chz
    order.chz_attached_at = order.chz_attached_at or _now()
    _set_pending_wb_chz(order, chz, reason="PACKED_QUEUED_FOR_WB", supply_id=order.supply_id)
    _event(db, order, "CHZ_SCANNED_LOCAL" if first_local_save else "CHZ_RETRY_SCANNED", value=chz)

    # Strict stock write-off stays fully local and atomic. 0.9.4.4 reservation recovery
    # can recreate a missing Stock Core v2 reservation from a genuinely free physical unit.
    _consume_picking_reservation_strict(db, order, "WB_PACKED_CHZ_BACKGROUND")
    now = _now()
    order.assembled_at = order.assembled_at or now
    order.packed_at = order.packed_at or now
    _set_manual_workflow_status(order, None)
    setattr(order, "manual_status_override", False) if hasattr(order, "manual_status_override") else None
    order.local_status = "PACKED"
    order.label_generated_at = None
    _event(db, order, "PACKED_IN_FULFILLCORE", value=chz, data={
        "source": "LOCAL_FIRST_BACKGROUND_WB_0_9_4_4",
        "wb_chz_verified": False,
        "background_submit": True,
        "station_code": station,
        "device_id": device_id,
    })
    if station:
        release_packing_lease(db, order.id, station, device_id)

    # Capture every value needed by the worker BEFORE commit; commit may expire ORM rows.
    order_number = _wb_order_number(order)
    _timing_add_ms(timing, "finalize_ms", _t)
    chz_timing = _finish_chz_timing(db, order, timing, station=station, outcome="PACKED_QUEUED")
    db.commit()

    # Absolutely no SQLAlchemy access in this worker. A 30-second WB outage can no longer
    # retain marketplace_orders row locks or block status changes/other packing stations.
    _submit_wb_chz_background(client, order_number, chz, source="PACKING")

    return {
        "ok": True,
        "chz_timing": chz_timing,
        "order": _order_response(db, order, warehouse_id, cred),
        "label_url": f"/marketplaces/wb/orders/{order.id}/label.pdf?credential_id={payload.credential_id}&reprint=1",
        "label_base64": None,
        "print_job_id": None,
        "print_deferred": True,
        "wb_chz_pending": True,
        "wb_chz_submitted": True,
        "wb_chz_verified": False,
        "packed": True,
        "message": "Заказ упакован без ожидания Wildberries. ЧЗ отправляется в WB в фоне; подтверждение подтянется автоматически.",
    }
'''


def patch_marketplaces(path: Path) -> None:
    s = read_py(path)
    if MARKER in s:
        print("OK: marketplaces.py already patched")
        return
    if "0.9.4.2 DATABASE STABILITY" not in s:
        raise RuntimeError("0.9.4.2 database-stability foundation missing")

    # Add DB-free background submission helper.
    helper_anchor = "\ndef _wb_verify_sgtin(client: WBClient, order: MarketplaceOrder, chz: str, *, attempts: int = 1) -> dict[str, Any]:\n"
    if helper_anchor not in s:
        raise RuntimeError("_wb_verify_sgtin anchor not found")
    s = s.replace(helper_anchor, "\n" + BACKGROUND_HELPER.strip() + "\n\n" + helper_anchor.lstrip("\n"), 1)

    # Replace metadata sweep and CHZ hot path with transaction-safe implementations.
    s = replace_function(s, "_refresh_wb_metadata_batch", REFRESH_META)
    s = replace_function(s, "wb_scan_chz", SCAN_CHZ)

    # WB sync itself previously opened SELECT/UPDATE transactions and then waited on WB.
    # Release the throttle transaction before NEW HTTP.
    anchor = '''    if not claimed:\n        _reconcile_known_workspace_statuses(db, cred, selected_wb_warehouse_id)\n        counts = _wb_status_counts(db, cred, selected_wb_warehouse_id, physical_warehouse_id)\n        return {\n            "ok": True,\n            "throttled": True,\n            "selected_wb_warehouse_id": selected_wb_warehouse_id,\n            "total_local": sum(counts.values()),\n            "counts": counts,\n            "warnings": [],\n        }\n\n'''
    if anchor not in s:
        raise RuntimeError("wb_sync throttle anchor not found")
    s = s.replace(anchor, anchor + '''    # 0.9.4.4: claim/select work above opened a transaction. Close it before WB HTTP.\n    db.commit()\n\n''', 1)

    # Release SELECT status_candidates transaction before potentially many slow status HTTP batches.
    anchor2 = '''    status_batches = 0\n    status_requested = 0\n    try:\n'''
    if anchor2 not in s:
        raise RuntimeError("wb_sync status batch anchor not found")
    s = s.replace(anchor2, '''    status_batches = 0\n    status_requested = 0\n    # 0.9.4.4 transaction barrier: numeric_pairs are immutable, WB HTTP needs no DB lock.\n    db.commit()\n    try:\n''', 1)

    # Persist status/local reconciliation before metadata HTTP starts.
    anchor3 = '''    meta_due = payload.force or full_meta or (now_mono - _WB_LAST_META_SWEEP.get(key, 0.0) >= _WB_META_SWEEP_INTERVAL)\n'''
    if anchor3 not in s:
        raise RuntimeError("wb_sync meta_due anchor not found")
    s = s.replace(anchor3, '''    # 0.9.4.4: never carry marketplace_orders UPDATE locks into metadata/sticker WB calls.\n    db.commit()\n    meta_due = payload.force or full_meta or (now_mono - _WB_LAST_META_SWEEP.get(key, 0.0) >= _WB_META_SWEEP_INTERVAL)\n''', 1)

    # Marker near router declaration for easy live verification.
    s = s.replace('router = APIRouter(prefix="/marketplaces", tags=["Маркетплейсы"])', 'router = APIRouter(prefix="/marketplaces", tags=["Маркетплейсы"])\n# ' + MARKER, 1)
    ast.parse(s)
    path.write_text(s, encoding="utf-8")
    print("OK: marketplaces transaction/WB hot paths patched")


RECOVERY_HELPER = r'''
def _recover_missing_reservation_from_free_stock(db: Session, order: MarketplaceOrder) -> dict[str, Any] | None:
    """0.9.4.4: recreate a missing reservation from one truly free physical unit.

    This is used only at packing time for a real picking-list order. It never creates
    negative stock and never steals an ACTIVE/CONSUMING/CANCELLING unit. Real cells are
    preferred; virtual ПРИЕМКА is a last-resort source, matching the established 0.8.1 rule.
    """
    if not order.last_picking_list_id or not order.local_sku_id:
        return None
    try:
        plist = db.get(MarketplacePickingList, uuid.UUID(str(order.last_picking_list_id)))
    except Exception:
        plist = None
    if not plist or not plist.warehouse_id:
        return None

    lock_key = f"packing-reservation-recover:{order.client_id}:{plist.warehouse_id}:{order.local_sku_id}"
    with distributed_lock(db, lock_key, blocking=False) as acquired:
        if not acquired:
            return None
        rows = db.execute(
            select(InventoryBalance, Location)
            .join(Location, Location.id == InventoryBalance.location_id)
            .where(
                InventoryBalance.client_id == order.client_id,
                InventoryBalance.warehouse_id == plist.warehouse_id,
                InventoryBalance.sku_id == order.local_sku_id,
                InventoryBalance.quantity > 0,
            )
            .with_for_update(of=InventoryBalance, skip_locked=True)
        ).all()
        # Real storage first, ПРИЕМКА last, forbidden system zones never.
        rows.sort(key=lambda pair: 1 if str(pair[1].code or "").strip().upper() == "ПРИЕМКА" else 0)
        for balance, location in rows:
            code = str(location.code or "").strip().upper()
            if code in SYSTEM_LOCATION_CODES and code != "ПРИЕМКА":
                continue
            reserved = live_reserved_for_bucket(
                db,
                client_id=order.client_id,
                warehouse_id=plist.warehouse_id,
                sku_id=order.local_sku_id,
                location_id=balance.location_id,
                container_id=balance.container_id,
            )
            if _d(balance.quantity) - reserved < Decimal(1) - EPSILON:
                continue
            rid = str(uuid.uuid4())
            db.execute(
                text(
                    """
                    INSERT INTO fc_stock_reservations(
                        id,order_id,external_order_id,picking_list_id,client_id,warehouse_id,
                        sku_id,source_location_id,source_container_id,quantity,status,note
                    ) VALUES (
                        :id,:order_id,:external,:picking,:client,:warehouse,
                        :sku,:location,:container,1,'ACTIVE','PACKING_RECOVER_FREE_STOCK_0_9_4_4'
                    )
                    ON CONFLICT DO NOTHING
                    """
                ),
                {
                    "id": rid,
                    "order_id": str(order.id),
                    "external": str(order.external_order_num or order.external_order_id or "")[:64],
                    "picking": str(order.last_picking_list_id),
                    "client": str(order.client_id),
                    "warehouse": str(plist.warehouse_id),
                    "sku": str(order.local_sku_id),
                    "location": str(balance.location_id),
                    "container": str(balance.container_id) if balance.container_id else None,
                },
            )
            recovered = latest_reservations_for_orders(db, [order.id], statuses=["ACTIVE"]).get(str(order.id))
            if recovered:
                return recovered
    return None
'''


def patch_reservations(path: Path) -> None:
    s = read_py(path)
    if "PACKING_RECOVER_FREE_STOCK_0_9_4_4" in s:
        print("OK: stock_reservations.py already patched")
        return
    target = "\ndef assert_order_reservation_ready_for_packing(db: Session, order: MarketplaceOrder) -> dict[str, Any] | None:\n"
    if target not in s:
        raise RuntimeError("packing reservation assert anchor missing")
    s = s.replace(target, "\n" + RECOVERY_HELPER.strip() + "\n\n" + target.lstrip("\n"), 1)

    old = '''    if not latest:\n        # Manually toggled PICKING orders historically had no physical allocation.\n'''
    new = '''    if not latest:\n        # 0.9.4.4: reservation rows can be missing after old picking-list/sync failures.\n        # If one genuinely FREE exact-SKU unit exists, atomically bind it to this order\n        # instead of blocking the packing table with “Резерв Stock Core v2 отсутствует”.\n        latest = _recover_missing_reservation_from_free_stock(db, order)\n    if not latest:\n        # Manually toggled PICKING orders historically had no physical allocation.\n'''
    if old not in s:
        raise RuntimeError("missing-reservation fallback anchor not found")
    s = s.replace(old, new, 1)
    ast.parse(s)
    path.write_text(s, encoding="utf-8")
    print("OK: missing Stock Core v2 reservation recovery patched")


def patch_main(path: Path) -> None:
    s = read_py(path)
    if 'version="0.9.4.4"' in s:
        print("OK: main.py already 0.9.4.4")
        return
    out, n = re.subn(r'version="0\.9\.4(?:\.\d+)?"', 'version="0.9.4.4"', s, count=1)
    if n != 1:
        raise RuntimeError("FulfillCore 0.9.4.x FastAPI version literal not found")
    ast.parse(out)
    path.write_text(out, encoding="utf-8")
    print("OK: health version -> 0.9.4.4")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    a = ap.parse_args()
    root = Path(a.root)
    patch_marketplaces(root / "app/routers/marketplaces.py")
    patch_reservations(root / "app/services/stock_reservations.py")
    patch_main(root / "app/main.py")


if __name__ == "__main__":
    main()
