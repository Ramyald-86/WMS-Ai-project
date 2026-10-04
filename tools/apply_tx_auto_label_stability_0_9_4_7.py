#!/usr/bin/env python3
from __future__ import annotations
import argparse
import ast
import re
from pathlib import Path

MARKER = "0.9.4.7 DB TX AUTO LABEL STABILITY"


def function_bounds(src: str, name: str) -> tuple[int, int]:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node.lineno, node.end_lineno or node.lineno
    raise RuntimeError(f"function {name} not found")


def replace_function(src: str, name: str, replacement: str) -> str:
    start, end = function_bounds(src, name)
    lines = src.splitlines(True)
    first = start - 1
    while first > 0 and lines[first - 1].lstrip().startswith("@"):
        first -= 1
    lines[first:end] = [replacement.rstrip() + "\n\n"]
    out = "".join(lines)
    ast.parse(out)
    return out


AUTO_LABEL_HELPER = r'''
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


def _schedule_wb_auto_label_background(
    *,
    client: WBClient,
    order_id: uuid.UUID,
    credential_id: uuid.UUID,
    order_number: int,
    expected_chz: str,
    station_code: str,
    width_mm: float,
    height_mm: float,
    rotate: int,
    scale_percent: float,
    offset_x_mm: float,
    offset_y_mm: float,
) -> bool:
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
        try:
            # Waiting for a semaphore and for WB consumes no DB connection.
            acquired = _AUTO_LABEL_BG_SLOTS.acquire(timeout=120.0)
            if not acquired:
                logger.warning("FC_AUTO_LABEL_FAIL order_id=%s station=%s reason=worker_busy", order_id, station_code)
                return

            # Common case is 1-5 s, but allow WB projection up to ~90 s. No database
            # transaction exists during these sleeps or HTTP calls.
            delays = (0.0, 0.45, 0.75, 1.1, 1.6, 2.3, 3.2, 4.5, 6.0, 8.0, 11.0, 15.0, 20.0)
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
                if _wb_order_currently_cancelled(order) or str(order.local_status or "").upper() == "CANCELLED":
                    logger.warning("FC_AUTO_LABEL_DROP order_id=%s station=%s reason=cancelled", order_id, station_code)
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
                job = queue_print_job(
                    db2, station_code=station_code, payload=png,
                    file_name=f"WB-order-{order.external_order_id}.png", document_type="WB_ORDER_LABEL",
                    client_id=order.client_id, object_type="MARKETPLACE_ORDER", object_id=str(order.id),
                )
                order.label_generated_at = order.label_generated_at or _now()
                order.label_print_count = (order.label_print_count or 0) + 1
                if order.product_scanned_at:
                    order.packed_at = order.packed_at or _now()
                    if order.local_status not in {"SENT", "DELIVERED", "CANCELLED"}:
                        _consume_picking_reservation_strict(db2, order, "WB_PACKED_PRINT_QUEUE_RECONCILE")
                        setattr(order, "manual_status_override", False) if hasattr(order, "manual_status_override") else None
                        order.local_status = "PACKED"
                elif order.local_status not in {"DELIVERED", "CANCELLED", "PACKED"}:
                    order.local_status = "SENT"

                raw = dict(order.raw_data or {})
                raw.pop("_fc_auto_wb_label_pending", None)
                raw["_fc_auto_wb_label"] = {
                    "chz": order.chz_code,
                    "station": station_code,
                    "print_job_id": str(job.id),
                    "queued_at": _now().isoformat(),
                }
                order.raw_data = raw
                _event(db2, order, "ORDER_LABEL_QUEUED", data={
                    "print_job_id": str(job.id), "station": station_code,
                    "auto_after_chz": True, "source": "BG_NO_DB_WAIT_0_9_4_7",
                })
                db2.commit()
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
'''


QUEUE_LABEL = r'''@router.post("/wb/orders/{order_id}/label/queue")
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

        old_jobs = db.execute(select(PrintJob).where(
            PrintJob.object_type == "MARKETPLACE_ORDER",
            PrintJob.object_id == str(order.id),
            PrintJob.document_type == "WB_ORDER_LABEL",
            PrintJob.status == "QUEUED",
        )).scalars().all()
        for old_job in old_jobs:
            old_job.status = "CANCELLED"
            old_job.error_message = "Отменено 0.9.4.7: заменено единственной фоновой автопечатью"

        # Snapshot immutable values while the session is local, then COMMIT BEFORE any
        # WB HTTP. This is the transaction barrier missing in 0.9.4.6.
        order_number = _wb_order_number(order)
        expected_chz = str(order.chz_code or "")
        station = str(payload.print_station or "")
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
        db, station_code=payload.print_station, payload=png,
        file_name=f"WB-order-{order.external_order_id}.png", document_type="WB_ORDER_LABEL",
        client_id=order.client_id, object_type="MARKETPLACE_ORDER", object_id=str(order.id),
    )
    order.label_generated_at = order.label_generated_at or _now()
    order.label_print_count = (order.label_print_count or 0) + 1
    if order.product_scanned_at:
        order.packed_at = order.packed_at or _now()
        if order.local_status not in {"SENT", "DELIVERED", "CANCELLED"}:
            _consume_picking_reservation_strict(db, order, "WB_PACKED_PRINT_QUEUE_RECONCILE")
            setattr(order, "manual_status_override", False) if hasattr(order, "manual_status_override") else None
            order.local_status = "PACKED"
    elif order.local_status not in {"DELIVERED", "CANCELLED", "PACKED"}:
        order.local_status = "SENT"
    _event(db, order, "ORDER_LABEL_QUEUED", data={"print_job_id": str(job.id), "station": payload.print_station, "auto_after_chz": False})
    db.commit()
    return {"ok": True, "print_job_id": str(job.id), "order": _order_response(db, order)}
'''


CHZ_BG = r'''def _submit_wb_chz_background(
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
            acquired = _CHZ_BG_SLOTS.acquire(timeout=0.2)
            if not acquired:
                cooldown = 3.0
                outcome = "busy"
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
'''


def patch_marketplaces(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    ast.parse(s)
    if MARKER in s:
        print("OK: marketplaces.py already 0.9.4.7")
        return
    if "0.9.4.5 WB CHZ PRINT STABILITY" not in s:
        raise RuntimeError("0.9.4.5 CHZ foundation missing")

    # Update the DB-free CHZ worker without touching packing/reservation logic.
    s = replace_function(s, "_submit_wb_chz_background", CHZ_BG)

    # Insert the automatic print worker directly before the queue endpoint.
    anchor = '@router.post("/wb/orders/{order_id}/label/queue")\n'
    if anchor not in s:
        raise RuntimeError("label queue endpoint anchor missing")
    s = s.replace(anchor, AUTO_LABEL_HELPER.strip() + "\n\n" + anchor, 1)
    s = replace_function(s, "wb_queue_order_label", QUEUE_LABEL)

    # Easy live marker near router declaration.
    router_anchor = 'router = APIRouter(prefix="/marketplaces", tags=["Маркетплейсы"])'
    if router_anchor in s:
        s = s.replace(router_anchor, router_anchor + "\n# " + MARKER, 1)
    ast.parse(s)
    path.write_text(s, encoding="utf-8")
    print("OK: marketplaces.py auto-label DB barrier + complete CHZ direct attempt patched")


def patch_main(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    ast.parse(s)
    if "0.9.4.7 AUTH TX BARRIER" not in s:
        anchor = '            response = await call_next(request)\n'
        if anchor not in s:
            raise RuntimeError("commercial middleware call_next anchor missing")
        replacement = '''            # 0.9.4.7 AUTH TX BARRIER: commercial_session_from_request() performs\n            # read-only SQL. Never keep that implicit transaction open while the real\n            # endpoint runs (and possibly waits on WB).\n            try:\n                if db.in_transaction():\n                    db.rollback()\n            except Exception:\n                try: db.rollback()\n                except Exception: pass\n\n            response = await call_next(request)\n'''
        s = s.replace(anchor, replacement, 1)
        # Persist the rare tenant-link write explicitly after the downstream request.
        old = '                        link_resource_to_tenant(db, commercial["tenant_id"], "client" if path.endswith("/clients") else "warehouse", resource_id)\n'
        new = old + '                        db.commit()\n'
        if old in s and 'link_resource_to_tenant(db, commercial["tenant_id"]' in s:
            s = s.replace(old, new, 1)
    s, n = re.subn(r'version="0\.9\.4\.6"', 'version="0.9.4.7"', s, count=1)
    if n != 1 and 'version="0.9.4.7"' not in s:
        raise RuntimeError("FastAPI version 0.9.4.6 not found")
    ast.parse(s)
    path.write_text(s, encoding="utf-8")
    print("OK: main.py auth transaction barrier + version 0.9.4.7")


def patch_js(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    old = 'toast(`Заказ WB №${orderNumber}: этикетка WB отправлена на печать.`);'
    new = 'toast(`Заказ WB №${orderNumber}: этикетка WB поставлена в фоновую очередь и распечатается автоматически.`);'
    if old in s:
        s = s.replace(old, new, 1)
    path.write_text(s, encoding="utf-8")
    print("OK: app.js truthful background print toast")


def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--root", required=True); a = ap.parse_args(); root = Path(a.root)
    patch_marketplaces(root / "app/routers/marketplaces.py")
    patch_main(root / "app/main.py")
    patch_js(root / "app/static/app.js")

if __name__ == "__main__":
    main()
