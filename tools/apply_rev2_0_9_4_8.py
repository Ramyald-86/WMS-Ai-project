#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

MARKER = "0.9.4.8 REV2 SEQUENTIAL CONFIRMED PRINT"


def function_bounds(src: str, name: str) -> tuple[int, int]:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node.lineno, node.end_lineno or node.lineno
    raise RuntimeError(f"function {name} not found")


def function_source(src: str, name: str) -> str:
    start, end = function_bounds(src, name)
    return "\n".join(src.splitlines()[start - 1:end])


def replace_function(src: str, name: str, replacement: str) -> str:
    start, end = function_bounds(src, name)
    lines = src.splitlines(True)
    # 0.9.4.8a: replace only the function node. Route decorators must survive.
    lines[start - 1:end] = [replacement.rstrip() + "\n\n"]
    out = "".join(lines)
    ast.parse(out)
    return out


CONSUME_STRICT = r'''def _consume_picking_reservation_strict(
    db: Session,
    order: MarketplaceOrder,
    reason: str,
    *,
    require: bool = True,
) -> dict[str, Any]:
    """Consume exactly once, and never let PACKED hide a failed write-off."""
    result = consume_order_reservation(db, order, note=reason)
    if result.get("changed"):
        _event(db, order, "PICKING_RESERVATION_CONSUMED", value=reason, data=result)
        return result

    failure_reason = str(result.get("reason") or "unknown")
    if failure_reason == "already_consumed":
        # Idempotent replay after a successful previous attempt.
        return {**result, "ok": True, "idempotent": True}

    logger.error(
        "FC_PACK_WRITE_OFF_BLOCKED order_id=%s external=%s reason=%s result=%r request_reason=%s",
        order.id,
        order.external_order_num or order.external_order_id,
        failure_reason,
        result,
        reason,
    )
    _event(
        db,
        order,
        "PACKED_WITHOUT_WRITE_OFF",
        value=failure_reason,
        data={"request_reason": reason, "result": result, "blocked_before_packed": True},
    )
    if not require:
        return result

    if failure_reason == "no_picking_list":
        detail = "Заказ не проходил через сборочный лист. Товар не зарезервирован, упаковать нельзя."
    elif failure_reason == "no_reservation":
        detail = "Резерв по заказу не найден. Обратитесь к старшему смены."
    else:
        detail = f"Списание невозможно: {failure_reason}"
    raise HTTPException(status_code=409, detail=detail)
'''


PACKED_WITHOUT_WRITEOFF_ROUTE = r'''@router.get("/wb/orders/packed-without-writeoff")
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
'''


def patch_scan_product(mp: str) -> str:
    src = function_source(mp, "wb_scan_product")
    old = '''    reservation = assert_order_reservation_ready_for_packing(db, matched)\n    if reservation:\n        _event(db, matched, "PICKING_RESERVATION_READY", value=str(reservation.get("id") or ""), data={\n            "status": reservation.get("status"),\n            "source_location_id": reservation.get("source_location_id"),\n            "source_container_id": reservation.get("source_container_id"),\n        })\n    elif not matched.last_picking_list_id:\n        # Packing may intentionally be used without an Excel picking list. That path\n        # is allowed, but it is strictly stock-neutral: no address/container balance\n        # is reserved, moved or written off by the workflow status alone.\n        _event(db, matched, "PACKING_WITHOUT_PICKING_LIST", data={\n            "stock_touched": False, "reason": "status_only_picking"\n        })\n'''
    new = '''    if not matched.last_picking_list_id:\n        # REV2: never start CHZ/label work for an order that cannot be written off.\n        release_packing_lease(db, matched.id, station, device_id)\n        _event(db, matched, "PACKING_RESERVATION_REQUIRED", data={\n            "reason": "no_picking_list", "blocked_before_chz": True,\n        })\n        db.commit()\n        raise HTTPException(status_code=409, detail="Заказ не проходил через сборочный лист. Товар не зарезервирован, упаковать нельзя.")\n\n    reservation = assert_order_reservation_ready_for_packing(db, matched)\n    if not reservation:\n        release_packing_lease(db, matched.id, station, device_id)\n        _event(db, matched, "PACKING_RESERVATION_REQUIRED", data={\n            "reason": "no_reservation", "blocked_before_chz": True,\n        })\n        db.commit()\n        raise HTTPException(status_code=409, detail="Резерв по заказу не найден. Обратитесь к старшему смены.")\n    _event(db, matched, "PICKING_RESERVATION_READY", value=str(reservation.get("id") or ""), data={\n        "status": reservation.get("status"),\n        "source_location_id": reservation.get("source_location_id"),\n        "source_container_id": reservation.get("source_container_id"),\n    })\n'''
    if old not in src:
        raise RuntimeError("wb_scan_product reservation compatibility block not found")
    src = src.replace(old, new, 1)
    return replace_function(mp, "wb_scan_product", src)


def patch_apply_meta(mp: str) -> str:
    src = function_source(mp, "_apply_wb_order_meta")
    # Replace the weaker 0.9.4.8 blank-only guard with a strict-gate ownership guard.
    weak = '''    # 0.9.4.8: an empty WB projection is not proof that the physical CHZ vanished.\n    # Keep the scanner value until explicit delete/replace or the confirmed-cancel release path.\n    if not actual and order.chz_code:\n        return changed\n\n    if actual != order.chz_code:\n'''
    strong = '''    # REV2: while the sequential pack gate owns the scanner CHZ, passive WB metadata\n    # projections are never allowed to detach or replace that physical scan. Explicit\n    # operator delete/replace and the confirmed-cancel path remain authoritative.\n    gate_raw = dict(order.raw_data or {})\n    strict_gate = gate_raw.get("_fc_pack_gate_v1") if isinstance(gate_raw.get("_fc_pack_gate_v1"), dict) else {}\n    strict_gate_active = (\n        bool(strict_gate)\n        and str(strict_gate.get("chz") or "") == str(order.chz_code or "")\n        and str(strict_gate.get("stage") or "") != "DONE"\n    )\n    if order.chz_code and (strict_gate_active or not actual):\n        return changed\n\n    if actual != order.chz_code:\n'''
    if weak not in src:
        raise RuntimeError("_apply_wb_order_meta weak CHZ guard not found")
    src = src.replace(weak, strong, 1)
    return replace_function(mp, "_apply_wb_order_meta", src)


def patch_strict_worker(mp: str) -> str:
    src = function_source(mp, "_schedule_strict_pack_gate")
    src = src.replace('if supplier in {"new", "complete"}:', 'if supplier == "new":', 1)

    # Faster early polling, but keep a long DB-free safety window.
    src = src.replace(
        'for pause in (0.08, 0.12, 0.18, 0.26, 0.38, 0.55, 0.78, 1.05, 1.40):',
        'for pause in (0.04, 0.06, 0.10, 0.14, 0.20, 0.30, 0.45, 0.65, 0.90, 1.25, 1.75):',
        1,
    )
    src = src.replace(
        'for pause in (0.12, 0.18, 0.26, 0.36, 0.50, 0.70, 0.95, 1.30, 1.75, 2.35, 3.10, 4.10, 5.40):',
        'for pause in (0.05, 0.08, 0.12, 0.18, 0.26, 0.38, 0.55, 0.78, 1.05, 1.40, 1.90, 2.60, 3.50, 4.70, 6.0, 8.0, 10.0):',
        1,
    )
    src = src.replace(
        'for pause in (0.0, 0.12, 0.18, 0.26, 0.38, 0.55, 0.78, 1.05, 1.40, 1.85, 2.45, 3.20, 4.20, 5.50, 7.0, 9.0, 12.0):',
        'for pause in (0.0, 0.05, 0.08, 0.12, 0.18, 0.26, 0.38, 0.55, 0.78, 1.05, 1.40, 1.90, 2.60, 3.50, 4.70, 6.0, 8.0, 10.0, 12.0, 15.0):',
        1,
    )

    # Unknown programmer errors must fail immediately instead of sleeping through all retries.
    src = src.replace(
        '''            except Exception:\n                pass\n\n            supplier = ""''',
        '''            except HTTPException:\n                pass\n            except (TimeoutError, ConnectionError, OSError):\n                pass\n            except Exception as exc:\n                logger.exception("FC_PACK_GATE_PROGRAM_ERROR order_id=%s stage=initial_meta", order_id)\n                _fail(f"Внутренняя ошибка проверки ЧЗ: {exc}", code="PROGRAM_ERROR")\n                return\n\n            supplier = ""''',
        1,
    )
    src = src.replace(
        '''                        except Exception:\n                            supplier = ""\n                        if supplier == "confirm":''',
        '''                        except HTTPException:\n                            supplier = ""\n                        except (TimeoutError, ConnectionError, OSError):\n                            supplier = ""\n                        except Exception as exc:\n                            logger.exception("FC_PACK_GATE_PROGRAM_ERROR order_id=%s stage=confirm_poll", order_id)\n                            _fail(f"Внутренняя ошибка ожидания confirm: {exc}", code="PROGRAM_ERROR")\n                            return\n                        if supplier == "confirm":''',
        1,
    )
    src = src.replace(
        '''                    except Exception:\n                        continue\n                    if state.get("accepted"):''',
        '''                    except HTTPException:\n                        continue\n                    except (TimeoutError, ConnectionError, OSError):\n                        continue\n                    except Exception as exc:\n                        logger.exception("FC_PACK_GATE_PROGRAM_ERROR order_id=%s stage=sgtin_verify", order_id)\n                        _fail(f"Внутренняя ошибка проверки ответа WB: {exc}", code="PROGRAM_ERROR")\n                        return\n                    if state.get("accepted"):''',
        1,
    )
    src = src.replace(
        '''                except Exception as exc:\n                    last_error = exc\n\n            if image is None:''',
        '''                except (TimeoutError, ConnectionError, OSError) as exc:\n                    last_error = exc\n                    continue\n                except Exception as exc:\n                    logger.exception("FC_PACK_GATE_PROGRAM_ERROR order_id=%s stage=label_wait", order_id)\n                    _fail(f"Внутренняя ошибка получения этикетки: {exc}", code="PROGRAM_ERROR")\n                    return\n\n            if image is None:''',
        1,
    )

    old_queue = '''                old_jobs = db2.execute(select(PrintJob).where(\n                    PrintJob.object_type == "MARKETPLACE_ORDER",\n                    PrintJob.object_id == str(order.id),\n                    PrintJob.document_type == "WB_ORDER_LABEL",\n                    PrintJob.status == "QUEUED",\n                )).scalars().all()\n                for old_job in old_jobs:\n                    old_job.status = "CANCELLED"\n                    old_job.error_message = "Отменено 0.9.4.8: заменено строгой последовательной печатью"\n\n                if item is not None:\n                    _apply_wb_order_sticker(db2, order, item)\n                png = wb_sticker_exact_png(\n                    image, width_mm=width_mm, height_mm=height_mm, rotate=rotate,\n                    scale_percent=scale_percent, offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm,\n                )\n                job = queue_print_job(\n                    db2, station_code=station_code, payload=png,\n                    file_name=f"WB-order-{order.external_order_id}.png", document_type="WB_ORDER_LABEL",\n                    client_id=order.client_id, object_type="MARKETPLACE_ORDER", object_id=str(order.id),\n                )\n'''
    new_queue = '''                station_norm = normalize_station_code(station_code)\n                # Reuse an already live job for this same order/station. This makes a\n                # repeated browser request idempotent even if the previous response was lost.\n                active_jobs = db2.execute(\n                    select(PrintJob)\n                    .where(\n                        PrintJob.object_type == "MARKETPLACE_ORDER",\n                        PrintJob.object_id == str(order.id),\n                        PrintJob.document_type == "WB_ORDER_LABEL",\n                        PrintJob.status.in_(["QUEUED", "CLAIMED", "PROCESSING", "PRINTING", "RUNNING"]),\n                    )\n                    .order_by(PrintJob.created_at.desc())\n                    .limit(10)\n                ).scalars().all()\n                reusable = next(\n                    (j for j in active_jobs if normalize_station_code(getattr(j, "station_code", None)) == station_norm),\n                    None,\n                )\n\n                if item is not None:\n                    _apply_wb_order_sticker(db2, order, item)\n                if reusable is not None:\n                    job = reusable\n                else:\n                    png = wb_sticker_exact_png(\n                        image, width_mm=width_mm, height_mm=height_mm, rotate=rotate,\n                        scale_percent=scale_percent, offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm,\n                    )\n                    # Important ordering: create the replacement first. If queue_print_job\n                    # raises, the transaction rolls back and older QUEUED jobs stay alive.\n                    job = queue_print_job(\n                        db2, station_code=station_norm, payload=png,\n                        file_name=f"WB-order-{order.external_order_id}.png", document_type="WB_ORDER_LABEL",\n                        client_id=order.client_id, object_type="MARKETPLACE_ORDER", object_id=str(order.id),\n                    )\n                    db2.flush()\n                    old_jobs = db2.execute(\n                        select(PrintJob).where(\n                            PrintJob.object_type == "MARKETPLACE_ORDER",\n                            PrintJob.object_id == str(order.id),\n                            PrintJob.document_type == "WB_ORDER_LABEL",\n                            PrintJob.status == "QUEUED",\n                            PrintJob.id != job.id,\n                        )\n                    ).scalars().all()\n                    for old_job in old_jobs:\n                        old_job.status = "CANCELLED"\n                        old_job.error_message = "Отменено 0.9.4.8 REV2: новое задание уже создано"\n'''
    if old_queue not in src:
        raise RuntimeError("strict worker old print cancellation block not found")
    src = src.replace(old_queue, new_queue, 1)
    src = src.replace('"message": f"Этикетка получена. Печатаем на {station_code}…",', '"message": f"Этикетка получена. Печатаем на {station_norm}…",', 1)
    src = src.replace('"station": station_code,\n                    "print_job_id": str(job.id),', '"station": station_norm,\n                    "print_job_id": str(job.id),', 1)
    src = src.replace('"print_job_id": str(job.id), "station": station_code, "wb_chz_verified": True,', '"print_job_id": str(job.id), "station": station_norm, "wb_chz_verified": True,', 1)
    src = src.replace('order_id, order_number, station_code, job.id,', 'order_id, order_number, station_norm, job.id,', 1)

    return replace_function(mp, "_schedule_strict_pack_gate", src)


def patch_generic_auto_label(mp: str) -> str:
    # P0-4/P1-2 safety for older/manual code paths that still call auto_after_chz.
    route = function_source(mp, "wb_queue_order_label")
    cancel_block = '''        old_jobs = db.execute(select(PrintJob).where(\n            PrintJob.object_type == "MARKETPLACE_ORDER",\n            PrintJob.object_id == str(order.id),\n            PrintJob.document_type == "WB_ORDER_LABEL",\n            PrintJob.status == "QUEUED",\n        )).scalars().all()\n        for old_job in old_jobs:\n            old_job.status = "CANCELLED"\n            old_job.error_message = "Отменено 0.9.4.7: заменено единственной фоновой автопечатью"\n\n'''
    if cancel_block not in route:
        raise RuntimeError("0.9.4.7 premature cancel block not found")
    route = route.replace(cancel_block, '', 1)
    route = route.replace('station = str(payload.print_station or "")', 'station = normalize_station_code(payload.print_station)', 1)
    mp = replace_function(mp, "wb_queue_order_label", route)

    worker = function_source(mp, "_schedule_wb_auto_label_background")
    worker = worker.replace(
        'key = (str(order_id), str(station_code or ""), str(expected_chz or ""))',
        'station_code = normalize_station_code(station_code)\n    key = (str(order_id), str(station_code or ""), str(expected_chz or ""))',
        1,
    )
    # Mark whether a real job was safely available; finally uses it to clear stale pending markers.
    worker = worker.replace('last_error: Exception | None = None\n        try:', 'last_error: Exception | None = None\n        queued_ok = False\n        try:', 1)
    worker = worker.replace(
        '''                job = queue_print_job(\n                    db2, station_code=station_code, payload=png,\n                    file_name=f"WB-order-{order.external_order_id}.png", document_type="WB_ORDER_LABEL",\n                    client_id=order.client_id, object_type="MARKETPLACE_ORDER", object_id=str(order.id),\n                )\n''',
        '''                active_jobs = db2.execute(\n                    select(PrintJob)\n                    .where(\n                        PrintJob.object_type == "MARKETPLACE_ORDER",\n                        PrintJob.object_id == str(order.id),\n                        PrintJob.document_type == "WB_ORDER_LABEL",\n                        PrintJob.status.in_(["QUEUED", "CLAIMED", "PROCESSING", "PRINTING", "RUNNING"]),\n                    )\n                    .order_by(PrintJob.created_at.desc())\n                    .limit(10)\n                ).scalars().all()\n                job = next(\n                    (j for j in active_jobs if normalize_station_code(getattr(j, "station_code", None)) == station_code),\n                    None,\n                )\n                if job is None:\n                    job = queue_print_job(\n                        db2, station_code=station_code, payload=png,\n                        file_name=f"WB-order-{order.external_order_id}.png", document_type="WB_ORDER_LABEL",\n                        client_id=order.client_id, object_type="MARKETPLACE_ORDER", object_id=str(order.id),\n                    )\n                    db2.flush()\n                    older = db2.execute(select(PrintJob).where(\n                        PrintJob.object_type == "MARKETPLACE_ORDER",\n                        PrintJob.object_id == str(order.id),\n                        PrintJob.document_type == "WB_ORDER_LABEL",\n                        PrintJob.status == "QUEUED",\n                        PrintJob.id != job.id,\n                    )).scalars().all()\n                    for old_job in older:\n                        old_job.status = "CANCELLED"\n                        old_job.error_message = "Отменено 0.9.4.8 REV2: новое задание уже создано"\n''',
        1,
    )
    worker = worker.replace('db2.commit()\n                logger.info(', 'db2.commit()\n                queued_ok = True\n                logger.info(', 1)

    # Clear stale pending marker on every unsuccessful exit. Do it in a fresh tiny transaction.
    finally_anchor = '''        finally:\n            if acquired:\n                try:\n                    _AUTO_LABEL_BG_SLOTS.release()'''
    cleanup = '''        finally:\n            if not queued_ok:\n                gen_cleanup = get_db()\n                db_cleanup = next(gen_cleanup)\n                try:\n                    order_cleanup = _order_lookup(db_cleanup, order_id, credential_id)\n                    raw_cleanup = dict(order_cleanup.raw_data or {})\n                    pending_cleanup = raw_cleanup.get("_fc_auto_wb_label_pending") if isinstance(raw_cleanup.get("_fc_auto_wb_label_pending"), dict) else {}\n                    if (\n                        str(pending_cleanup.get("chz") or "") == str(expected_chz or "")\n                        and normalize_station_code(pending_cleanup.get("station")) == station_code\n                    ):\n                        raw_cleanup.pop("_fc_auto_wb_label_pending", None)\n                        raw_cleanup["_fc_auto_wb_label_last_error"] = {\n                            "chz": expected_chz, "station": station_code, "at": _now().isoformat(),\n                            "error": str(last_error or "background worker ended without print job"),\n                        }\n                        order_cleanup.raw_data = raw_cleanup\n                        db_cleanup.commit()\n                    else:\n                        db_cleanup.rollback()\n                except Exception:\n                    try: db_cleanup.rollback()\n                    except Exception: pass\n                    logger.exception("FC_AUTO_LABEL_PENDING_CLEANUP_FAIL order_id=%s", order_id)\n                finally:\n                    try: gen_cleanup.close()\n                    except Exception: pass\n            if acquired:\n                try:\n                    _AUTO_LABEL_BG_SLOTS.release()'''
    if finally_anchor not in worker:
        raise RuntimeError("generic auto label finally anchor not found")
    worker = worker.replace(finally_anchor, cleanup, 1)
    return replace_function(mp, "_schedule_wb_auto_label_background", worker)


def patch_generic_chz_worker(mp: str) -> str:
    """P1-4 guard for legacy/reconciliation CHZ submissions; strict scanner uses its own gate."""
    mp = mp.replace('_CHZ_BG_SLOTS = threading.BoundedSemaphore(3)', '_CHZ_BG_SLOTS = threading.BoundedSemaphore(8)', 1)
    src = function_source(mp, '_submit_wb_chz_background')
    # Purge old cooldown keys before checking the current order so the dictionary cannot grow forever.
    old = '''    now_mono = time.monotonic()\n    with _CHZ_BG_COORD_LOCK:\n        if order_number in _CHZ_BG_INFLIGHT:\n'''
    new = '''    now_mono = time.monotonic()\n    with _CHZ_BG_COORD_LOCK:\n        stale_keys = [k for k, until in _CHZ_BG_NEXT_AT.items() if float(until or 0.0) < now_mono - 600.0]\n        for stale_key in stale_keys[:2000]:\n            _CHZ_BG_NEXT_AT.pop(stale_key, None)\n        if len(_CHZ_BG_NEXT_AT) > 5000:\n            for stale_key, _until in sorted(_CHZ_BG_NEXT_AT.items(), key=lambda kv: float(kv[1] or 0.0))[:1000]:\n                _CHZ_BG_NEXT_AT.pop(stale_key, None)\n        if order_number in _CHZ_BG_INFLIGHT:\n'''
    if old not in src:
        raise RuntimeError('generic CHZ coordination block not found')
    src = src.replace(old, new, 1)
    # Do not silently discard work during a small burst. This wait is only in the daemon thread, never the HTTP request.
    src = src.replace('_CHZ_BG_SLOTS.acquire(timeout=0.2)', '_CHZ_BG_SLOTS.acquire(timeout=15.0)', 1)
    src = src.replace('cooldown = 3.0\n                outcome = "busy"', 'cooldown = 1.0\n                outcome = "busy_retry_required"', 1)
    return replace_function(mp, '_submit_wb_chz_background', src)


def patch_legacy_js_queue(js: str) -> str:
    """Keep any legacy/manual background-label path per station and single-shot."""
    js = js.replace('wbScanBusy: false, wbHotRefreshTimer: null, wbBackgroundPrintChain: Promise.resolve(), wbSupplyMeta: {},',
                    'wbScanBusy: false, wbHotRefreshTimer: null, wbBackgroundPrintChains: {}, wbSupplyMeta: {},', 1)
    old_start = js.find('function queueWbBackgroundLabel(orderId,orderNumber,localFallback=false){')
    if old_start < 0:
        raise RuntimeError('queueWbBackgroundLabel not found')
    next_start = js.find('async function waitWbStrictPackingGate(', old_start)
    if next_start < 0:
        raise RuntimeError('strict gate JS anchor not found')
    replacement = r'''function queueWbBackgroundLabel(orderId,orderNumber,localFallback=false){
  /* 0.9.4.8 REV2: legacy/manual path only. The strict scanner does NOT use this
     queue. Keep one short request per workstation; server-side worker owns WB wait. */
  const ps=wbPrintSettings();
  const station=String(ps.station||state.wbPrintStation||'PACK-01').trim().toUpperCase();
  const chains=state.wbBackgroundPrintChains||(state.wbBackgroundPrintChains={});
  const previous=chains[station]||Promise.resolve();
  const current=previous.catch(()=>{}).then(async()=>{
    try{
      const r=await printWbOrderLabel(orderId,{autoAfterChz:true});
      scheduleWbHotRefresh(250);
      if(r?.background_pending)toast(`Заказ WB №${orderNumber}: ждём этикетку WB. Печать запустится автоматически.`);
      else toast(`Заказ WB №${orderNumber}: этикетка передана в печать.`);
    }catch(e){
      const code=e?.data?.detail?.code||'';
      if(code==='WB_ORDER_CANCELLED_CHZ_RELEASED'||code==='WB_ORDER_CANCELLED_CHZ_RELEASE_FAILED'){
        toast(e.message,true);scheduleWbHotRefresh(250);return;
      }
      toast(`Заказ WB №${orderNumber}: автоматическая печать не запущена: ${e.message}.`,true);
    }
  }).finally(()=>{if(chains[station]===current)delete chains[station];});
  chains[station]=current;
}
'''
    return js[:old_start] + replacement + '\n' + js[next_start:]


def add_packed_without_writeoff_route(mp: str) -> str:
    if 'def wb_packed_without_writeoff(' in mp:
        return mp
    anchor = '@router.post("/wb/orders/manual-status")\n'
    if anchor not in mp:
        raise RuntimeError("manual-status anchor not found for diagnostic route")
    return mp.replace(anchor, PACKED_WITHOUT_WRITEOFF_ROUTE.strip() + '\n\n\n' + anchor, 1)


def relax_reconcile_only_calls(mp: str) -> str:
    # TЗ: reconciliation/repair paths stay diagnostic and must not break manual recovery.
    for reason in (
        "WB_PACKED_LABEL_RECONCILE",
        "WB_PACKED_PRINT_QUEUE_RECONCILE",
        "WB_PACKED_BULK_LABEL_RECONCILE",
    ):
        old = f'_consume_picking_reservation_strict(db, order, "{reason}")'
        mp = mp.replace(old, f'_consume_picking_reservation_strict(db, order, "{reason}", require=False)')
        old2 = f'_consume_picking_reservation_strict(db2, order, "{reason}")'
        mp = mp.replace(old2, f'_consume_picking_reservation_strict(db2, order, "{reason}", require=False)')
    return mp


def normalize_queue_calls(mp: str) -> str:
    mp = mp.replace('station_code=payload.print_station', 'station_code=normalize_station_code(payload.print_station)')
    # Calls inside the two background helpers already normalize station_code before queueing.
    return mp


def patch_js(js: str) -> str:
    js = js.replace('await new Promise(resolve=>setTimeout(resolve,350));', 'await new Promise(resolve=>setTimeout(resolve,250));', 1)
    js = patch_legacy_js_queue(js)
    # Keep the website aligned with the deliberately retained, proven Print Agent 1.1.7.
    js = js.replace('PRINT AGENT 1.2.0', 'PRINT AGENT 1.1.7 SAFE SIMPLE')
    js = js.replace('Архив, Word-инструкция и токен доступны здесь после входа в FulfillCore.', 'Архив, инструкция и токен доступны здесь после входа в FulfillCore.')
    return js


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    args = ap.parse_args()
    root = Path(args.root)
    mp_path = root / 'app/routers/marketplaces.py'
    js_path = root / 'app/static/app.js'

    mp = mp_path.read_text(encoding='utf-8')
    js = js_path.read_text(encoding='utf-8')

    if MARKER in mp:
        # Idempotent maintenance: keep the site download card on the approved 1.1.7 agent.
        patched_js = patch_js(js)
        if patched_js != js:
            js_path.write_text(patched_js, encoding='utf-8')
        print('Already patched 0.9.4.8 REV2')
        return
    if '0.9.4.8 STRICT CHZ PRINT GATE' not in mp:
        raise RuntimeError('Expected stage-1 0.9.4.8 strict gate')

    mp = mp.replace('# 0.9.4.8 STRICT CHZ PRINT GATE', f'# 0.9.4.8 STRICT CHZ PRINT GATE\n# {MARKER}', 1)
    mp = replace_function(mp, '_consume_picking_reservation_strict', CONSUME_STRICT)
    mp = patch_scan_product(mp)
    mp = patch_apply_meta(mp)
    mp = patch_strict_worker(mp)
    mp = patch_generic_auto_label(mp)
    mp = patch_generic_chz_worker(mp)
    mp = add_packed_without_writeoff_route(mp)
    mp = relax_reconcile_only_calls(mp)
    mp = normalize_queue_calls(mp)
    js = patch_js(js)

    ast.parse(mp)
    mp_path.write_text(mp, encoding='utf-8')
    js_path.write_text(js, encoding='utf-8')
    print('OK: 0.9.4.8 REV2 marketplace/packing follow-ups applied')


if __name__ == '__main__':
    main()
