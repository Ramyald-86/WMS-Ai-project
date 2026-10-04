#!/usr/bin/env python3
from __future__ import annotations
import argparse
import ast
from pathlib import Path

MARKER = "0.9.4.8 STRICT CHZ PRINT GATE"


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


STRICT_HELPERS = r'''
# 0.9.4.8 STRICT CHZ PRINT GATE
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
        raw[_PACK_GATE_KEY] = marker

        pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
        pending = dict(pending)
        pending["chz"] = expected_chz
        pending["last_attempt_at"] = _now().isoformat()
        if stage in {"CHZ_VERIFIED", "WAIT_LABEL", "PRINT_QUEUED"}:
            pending["reason"] = "WB_VERIFIED_WAITING_PRINT"
            pending["wb_verified"] = True
            pending.pop("last_error", None)
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
            if _wb_order_currently_cancelled(order) or str(order.local_status or "").upper() == "CANCELLED":
                msg = "WB отменил заказ до завершения печати. Товар не списан как упакованный."
                _set_pack_gate_marker(order, stage="ERROR", message=msg, error=msg)
                _event(db2, order, "STRICT_PACK_CANCELLED_BEFORE_PRINT_ACK", value=order.chz_code)
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

            # Physical print is now the warehouse write-off boundary.
            _consume_picking_reservation_strict(db2, order, "WB_PACKED_AFTER_PRINT_ACK")
            now = _now()
            order.assembled_at = order.assembled_at or now
            order.packed_at = order.packed_at or now
            _set_manual_workflow_status(order, None)
            setattr(order, "manual_status_override", False) if hasattr(order, "manual_status_override") else None
            order.local_status = "PACKED"
            order.label_generated_at = order.label_generated_at or now
            order.label_print_count = (order.label_print_count or 0) + 1
            _clear_pending_wb_chz(order)
            raw = dict(order.raw_data or {})
            marker = raw.get(_PACK_GATE_KEY) if isinstance(raw.get(_PACK_GATE_KEY), dict) else {}
            marker = dict(marker)
            marker.update({
                "stage": "DONE",
                "message": "ЧЗ подтверждён, этикетка напечатана, заказ упакован.",
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
            return {"pending": False, "done": True, "error": False, "status": outcome.get("status") or "DONE", "message": "ЧЗ подтверждён. Этикетка напечатана. Можно сканировать следующий товар."}
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
    deadline = time.monotonic() + max(10.0, float(timeout_seconds))
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
        time.sleep(1.0)
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
    width_mm: float,
    height_mm: float,
    rotate: int,
    scale_percent: float,
    offset_x_mm: float,
    offset_y_mm: float,
) -> bool:
    key = (str(order_id), str(expected_chz or ""))
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

            # First read-back: a retry of the same physical CHZ should not submit it twice.
            verified = False
            state: dict[str, Any] = {"accepted": False, "decision": ""}
            try:
                state = _wb_sgtin_state_without_db(client, int(order_number), expected_chz)
                verified = bool(state.get("accepted"))
            except Exception:
                pass

            supplier = ""
            if not verified:
                try:
                    supplier = _wb_supplier_status_without_db(client, int(order_number))
                except Exception as exc:
                    _fail(f"Не удалось получить статус заказа WB: {exc}", code="STATUS_READ")
                    return

                # Restore the proven older behaviour for NEW orders: make sure the order
                # is in one active technical CHZ supply, but do it without a DB transaction.
                # Deterministic names + a process lock avoid supply multiplication on normal
                # single-process FulfillCore deployments. Existing order supply is tried first.
                if supplier in {"new", "complete"}:
                    sid = str(supply_id or "").strip()
                    if not sid:
                        with _PACK_GATE_SUPPLY_LOCK:
                            sid = str(_WB_CHZ_SUPPLY_CACHE.get(supply_cache_key) or "").strip()
                        if not sid:
                            sid, _exact = _find_reusable_wb_chz_supply(client, supply_name)
                        if not sid:
                            data = client.create_supply(supply_name)
                            sid = str(data.get("id") or "").strip()
                        if sid:
                            with _PACK_GATE_SUPPLY_LOCK:
                                _WB_CHZ_SUPPLY_CACHE[supply_cache_key] = sid
                    if not sid:
                        _fail("WB не вернул активную поставку для перевода заказа в confirm. Повторите этот же ЧЗ.", code="NO_SUPPLY")
                        return

                    def _patch_to_supply(candidate: str) -> None:
                        _wb_add_orders_to_supply(client, candidate, [int(order_number)])

                    try:
                        _patch_to_supply(sid)
                    except HTTPException as exc:
                        code = _wb_api_error_status(exc)
                        if code not in {404, 409}:
                            _fail(f"WB не принял перевод заказа в поставку: {exc.detail}", code=f"CONFIRM_{code}")
                            return
                        # One recovery attempt: find the deterministic active supply, or
                        # create a fresh one only if none exists. Never loop on 409.
                        recovered, _exact = _find_reusable_wb_chz_supply(client, supply_name)
                        recovered = str(recovered or "").strip()
                        if not recovered or recovered == sid:
                            data = client.create_supply(supply_name)
                            recovered = str(data.get("id") or "").strip()
                        if not recovered or recovered == sid:
                            _fail(f"WB не разрешил перевести заказ в confirm: {exc.detail}", code=f"CONFIRM_{code}")
                            return
                        sid = recovered
                        with _PACK_GATE_SUPPLY_LOCK:
                            _WB_CHZ_SUPPLY_CACHE[supply_cache_key] = sid
                        try:
                            _patch_to_supply(sid)
                        except HTTPException as exc2:
                            _fail(f"WB не принял заказ в рабочую поставку: {exc2.detail}", code="CONFIRM_RETRY")
                            return

                    for pause in (0.08, 0.12, 0.18, 0.26, 0.38, 0.55, 0.78, 1.05, 1.40):
                        time.sleep(pause)
                        try:
                            supplier = _wb_supplier_status_without_db(client, int(order_number))
                        except Exception:
                            supplier = ""
                        if supplier == "confirm":
                            _pack_gate_db_update(
                                order_id, credential_id, expected_chz,
                                stage="WAIT_WB_CHZ", message="Заказ переведён в confirm WB. Передаём ЧЗ…",
                                supply_id=sid,
                            )
                            break

                if supplier not in {"confirm", "complete"}:
                    _fail(
                        f"WB пока не готов принять ЧЗ (supplierStatus={supplier or 'неизвестен'}). Повторите этот же ЧЗ.",
                        code="SUPPLIER_NOT_READY",
                    )
                    return

                try:
                    client.set_sgtin(int(order_number), [expected_chz])
                except HTTPException as exc:
                    # A 409 can be a projection race or an already-submitted code. Read
                    # metadata once before deciding it is a hard rejection.
                    code = _wb_api_error_status(exc)
                    if code == 409:
                        try:
                            state = _wb_sgtin_state_without_db(client, int(order_number), expected_chz)
                            if state.get("accepted"):
                                verified = True
                        except Exception:
                            pass
                    if not verified:
                        _fail(f"WB не принял ЧЗ: {exc.detail}", code=f"SGTIN_{code}")
                        return
                except Exception as exc:
                    _fail(f"Ошибка передачи ЧЗ в WB: {exc}", code="SGTIN_NETWORK")
                    return

            if not verified:
                _pack_gate_db_update(
                    order_id, credential_id, expected_chz,
                    stage="WAIT_WB_VERIFY", message="ЧЗ отправлен. Ждём подтверждение Wildberries…",
                )
                # Fast first reads, then bounded backoff. No DB connection exists here.
                for pause in (0.12, 0.18, 0.26, 0.36, 0.50, 0.70, 0.95, 1.30, 1.75, 2.35, 3.10, 4.10, 5.40):
                    time.sleep(pause)
                    try:
                        state = _wb_sgtin_state_without_db(client, int(order_number), expected_chz)
                    except Exception:
                        continue
                    if state.get("accepted"):
                        verified = True
                        break
                    decision = str(state.get("decision") or "").strip()
                    dkey = decision.lower()
                    if dkey in WB_SGTIN_DECISION_MESSAGES and dkey not in {"pending", "required"}:
                        _fail(_wb_sgtin_failure_detail(state, expected_chz), code="SGTIN_REJECTED", decision=decision)
                        return

            if not verified:
                _fail(
                    "WB не подтвердил ЧЗ в отведённое время. Товар не списан и заказ не упакован. Повторите этот же ЧЗ — FulfillCore продолжит проверку без создания дубля.",
                    code="VERIFY_TIMEOUT",
                    decision=str(state.get("decision") or "") or None,
                )
                return

            _pack_gate_db_update(
                order_id, credential_id, expected_chz,
                stage="CHZ_VERIFIED", message="ЧЗ подтверждён Wildberries. Получаем этикетку…",
                wb_decision=str(state.get("decision") or "") or None,
            )

            image: bytes | None = None
            item: dict[str, Any] | None = None
            last_error: Exception | None = None
            _pack_gate_db_update(
                order_id, credential_id, expected_chz,
                stage="WAIT_LABEL", message="ЧЗ подтверждён. Ждём этикетку WB…",
            )
            for pause in (0.0, 0.12, 0.18, 0.26, 0.38, 0.55, 0.78, 1.05, 1.40, 1.85, 2.45, 3.20, 4.20, 5.50, 7.0, 9.0, 12.0):
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
                _fail(
                    "ЧЗ подтверждён, но WB ещё не выдал этикетку. Товар не списан и следующий заказ заблокирован. Повторите этот же ЧЗ для продолжения.",
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

                old_jobs = db2.execute(select(PrintJob).where(
                    PrintJob.object_type == "MARKETPLACE_ORDER",
                    PrintJob.object_id == str(order.id),
                    PrintJob.document_type == "WB_ORDER_LABEL",
                    PrintJob.status == "QUEUED",
                )).scalars().all()
                for old_job in old_jobs:
                    old_job.status = "CANCELLED"
                    old_job.error_message = "Отменено 0.9.4.8: заменено строгой последовательной печатью"

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
                raw = dict(order.raw_data or {})
                marker = raw.get(_PACK_GATE_KEY) if isinstance(raw.get(_PACK_GATE_KEY), dict) else {}
                marker = dict(marker)
                marker.update({
                    "stage": "PRINT_QUEUED",
                    "message": f"Этикетка получена. Печатаем на {station_code}…",
                    "print_job_id": str(job.id),
                    "updated_at": _now().isoformat(),
                })
                raw[_PACK_GATE_KEY] = marker
                raw["_fc_auto_wb_label"] = {
                    "chz": order.chz_code,
                    "station": station_code,
                    "print_job_id": str(job.id),
                    "queued_at": _now().isoformat(),
                }
                pending = raw.get("_fc_chz_pending_wb") if isinstance(raw.get("_fc_chz_pending_wb"), dict) else {}
                pending = dict(pending)
                pending["reason"] = "WB_VERIFIED_WAITING_PRINT"
                pending["wb_verified"] = True
                pending.pop("last_error", None)
                raw["_fc_chz_pending_wb"] = pending
                order.raw_data = raw
                _event(db2, order, "ORDER_LABEL_QUEUED_STRICT_GATE", data={
                    "print_job_id": str(job.id), "station": station_code, "wb_chz_verified": True,
                })
                db2.commit()
                queued_job_id = str(job.id)
                logger.info(
                    "FC_PACK_GATE_PRINT_QUEUED order_id=%s order_num=%s station=%s job_id=%s elapsed_ms=%.1f",
                    order_id, order_number, station_code, job.id, (time.perf_counter()-started)*1000.0,
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
            if queued_job_id:
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
'''


SCAN_CHZ = r'''@router.post("/wb/packing/scan-chz")
def wb_scan_chz(payload: WBScanCHZRequest, warehouse_id: uuid.UUID | None = Query(default=None), station_code: str | None = Query(default=None), device_id: str | None = Query(default=None), wb_label_width: float = Query(default=58, ge=25, le=120), wb_label_height: float = Query(default=40, ge=25, le=120), wb_label_rotate: int = Query(default=-1), wb_label_scale: float = Query(default=100, ge=85, le=108), wb_label_offset_x: float = Query(default=0, ge=-5, le=5), wb_label_offset_y: float = Query(default=0, ge=-5, le=5), defer_print: int = Query(default=1, ge=0, le=1), db: Session = Depends(get_db)):
    """Strict sequential packing gate.

    The exact scanner CHZ is persisted first, but stock is NOT consumed and PACKED is
    NOT committed yet. A DB-free worker performs WB SGTIN verification and sticker
    retrieval, queues one Print Agent job, and the progress endpoint finalizes PACKED
    only after the agent reports successful printing.
    """
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
    if (order.wb_status or "").strip().lower() in WB_PHYSICALLY_LEFT_STATUSES | CANCEL_WB_STATUSES | {"sold"}:
        raise HTTPException(status_code=409, detail="WB уже показывает заказ как уехавший/завершённый. Код маркировки к нему прикреплять нельзя.")
    if order.chz_code and order.chz_code != chz:
        shown = order.chz_code.replace("\x1d", "␝")
        if len(shown) > 70:
            shown = shown[:42] + "…" + shown[-18:]
        raise HTTPException(status_code=409, detail=f"К этому заказу уже привязан другой ЧЗ: {shown}. Удалите/замените его в карточке заказа.")

    duplicate = db.execute(select(MarketplaceOrder).where(
        MarketplaceOrder.credential_id == payload.credential_id,
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
            "order": _order_response(db, order, warehouse_id, cred),
            "message": "ЧЗ уже подтверждён, этикетка напечатана, заказ упакован.",
        }
    if order.chz_code == chz and marker.get("stage") == "PRINT_QUEUED" and existing_job_id:
        db.commit()
        return {
            "ok": True, "gate_active": True, "packed": False, "printed": False,
            "order": _order_response(db, order, warehouse_id, cred),
            "stage": "PRINT_QUEUED", "print_job_id": existing_job_id,
            "message": marker.get("message") or "Этикетка уже передана в Print Agent. Ждём подтверждение печати.",
        }

    current_local = str(order.local_status or "").strip().upper()
    if current_local not in {"NEW", "PICKING"}:
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
    )
    _event(db, order, "CHZ_SCANNED_LOCAL" if first_local_save else "CHZ_RETRY_SCANNED", value=chz, data={
        "mode": "STRICT_CHZ_PRINT_GATE_0_9_4_8", "station_code": station, "device_id": device_id,
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
        width_mm=wb_label_width,
        height_mm=wb_label_height,
        rotate=wb_label_rotate,
        scale_percent=wb_label_scale,
        offset_x_mm=wb_label_offset_x,
        offset_y_mm=wb_label_offset_y,
    )
    return {
        "ok": True,
        "gate_active": True,
        "gate_scheduled": bool(scheduled),
        "packed": False,
        "printed": False,
        "stage": "WAIT_WB_CHZ",
        "order": _order_response(db, order, warehouse_id, cred),
        "message": "ЧЗ сохранён. Ждём подтверждение WB, затем этикетку и фактическую печать. Следующий товар пока заблокирован.",
    }
'''


PROGRESS_ROUTE = r'''@router.get("/wb/packing/progress")
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
        return {
            "ok": True, "done": True, "printed": True, "packed": True,
            "stage": "DONE", "print_job_id": print_job_id or None,
            "print_status": marker.get("print_status") or "DONE",
            "message": message,
            "order": _order_response(db, order, warehouse_id, cred),
        }
    if stage == "ERROR":
        return {
            "ok": False, "done": False, "printed": False, "packed": False,
            "error": True, "stage": "ERROR", "print_job_id": print_job_id or None,
            "message": str(marker.get("error") or message or "Ошибка упаковки"),
        }

    # The lease was renewed for 240 s at scan-chz. Do not write PostgreSQL every 350 ms
    # from the progress poll; this endpoint is intentionally read-only until print ACK.
    try:
        if db.in_transaction():
            db.rollback()
    except Exception:
        try: db.rollback()
        except Exception: pass

    if print_job_id:
        result = _finalize_strict_pack_from_job(
            order_id=order_id,
            credential_id=credential_id,
            expected_chz=expected_chz,
            print_job_id=print_job_id,
            station_code=station,
            device_id=device_id,
        )
        if result.get("done"):
            order = _order_lookup(db, order_id, credential_id)
            return {
                "ok": True, "done": True, "printed": True, "packed": True,
                "stage": "DONE", "print_job_id": print_job_id,
                "print_status": result.get("status") or "DONE",
                "message": result.get("message") or "ЧЗ подтверждён. Этикетка напечатана. Можно сканировать следующий товар.",
                "order": _order_response(db, order, warehouse_id, cred),
            }
        if result.get("error"):
            return {
                "ok": False, "done": False, "printed": False, "packed": False,
                "error": True, "stage": "ERROR", "print_job_id": print_job_id,
                "print_status": result.get("status"),
                "message": result.get("message") or "Ошибка печати",
            }
        return {
            "ok": True, "done": False, "printed": False, "packed": False,
            "stage": "PRINT_QUEUED", "print_job_id": print_job_id,
            "print_status": result.get("status"),
            "message": result.get("message") or message,
        }

    # Worker is still talking to WB. Re-read marker only on the next UI poll.
    return {
        "ok": True, "done": False, "printed": False, "packed": False,
        "stage": stage,
        "print_job_id": None,
        "message": message,
    }
'''


RETRY_PENDING = r'''def _retry_pending_wb_chz(db: Session, client: WBClient, order: MarketplaceOrder) -> bool:
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
    if order.product_scanned_at:
        _consume_picking_reservation_strict(db, order, "WB_PACKED_BACKGROUND_CHZ_RETRY")
        now = _now()
        order.assembled_at = order.assembled_at or now
        order.packed_at = order.packed_at or now
        order.local_status = "PACKED"
        _event(db, order, "PACKED_AFTER_CHZ_WB_RETRY", value=order.chz_code)
    return True
'''


JS_GATE = r'''
async function waitWbStrictPackingGate(orderId,orderNumber,ps){
  const started=Date.now();let lastStage='',transientErrors=0;
  while(Date.now()-started<240000){
    const q=new URLSearchParams({credential_id:String(wbCredential()),order_id:String(orderId),station_code:String(ps.station),device_id:String(state.deviceId||'')});
    if(state.warehouseId)q.set('warehouse_id',String(state.warehouseId));
    let p;
    try{
      p=await api('/marketplaces/wb/packing/progress?'+q.toString(),{timeoutMs:12000});
      transientErrors=0;
    }catch(err){
      transientErrors+=1;
      if(transientErrors<=8){
        $('wbPackChzHint').textContent='Связь с сервером восстанавливается. Упаковка продолжается, следующий товар заблокирован…';$('wbPackChzHint').className='';
        await new Promise(resolve=>setTimeout(resolve,500));
        continue;
      }
      throw err;
    }
    const stage=String(p.stage||'');
    if(stage!==lastStage){
      lastStage=stage;
      $('wbPackChzHint').textContent=p.message||'Выполняется упаковка…';
      $('wbPackChzHint').className=p.error?'bad':stage==='PRINT_QUEUED'?'good':'';
    }
    if(p.done&&p.printed&&p.packed){
      $('wbPackChzHint').textContent='✓ ЧЗ подтверждён. Этикетка напечатана. Заказ упакован.';$('wbPackChzHint').className='good';
      toast(`Заказ WB №${orderNumber}: ЧЗ подтверждён, этикетка напечатана. Можно сканировать следующий товар.`);
      $('wbPackProduct').disabled=false;if($('wbPackingResetBtn'))$('wbPackingResetBtn').disabled=false;
      scheduleWbHotRefresh(150);
      resetWbPacking(true);
      return p;
    }
    if(p.error)throw new Error(p.message||'Упаковка остановлена');
    await new Promise(resolve=>setTimeout(resolve,350));
  }
  throw new Error('WB/Print Agent не завершили упаковку за 4 минуты. Следующий товар заблокирован. Нажмите Enter на этом же ЧЗ, чтобы продолжить проверку.');
}
'''


PROCESS_CHZ_JS = r'''async function processWbChzScan(){
  if(state.wbScanBusy)return;if(!state.wbActivePackingOrder)return;const chz=$('wbPackChz').value;if(!chz)return;
  state.wbScanBusy=true;
  try{
    const ps=wbPrintSettings();
    if(ps.mode!=='agent')throw new Error('Для последовательной упаковки ШК → ЧЗ → этикетка нужен Print Agent. Выберите режим Print Agent в настройках печати.');
    $('wbPackProduct').disabled=true;$('wbPackChz').disabled=true;if($('wbPackingResetBtn'))$('wbPackingResetBtn').disabled=true;
    $('wbPackChzHint').textContent='Передаём ЧЗ в Wildberries…';$('wbPackChzHint').className='';
    const currentOrder=state.wbActivePackingOrder;
    const r=await api(`/marketplaces/wb/packing/scan-chz?warehouse_id=${encodeURIComponent(state.warehouseId)}&wb_label_width=${ps.wbWidth}&wb_label_height=${ps.wbHeight}&wb_label_rotate=${ps.wbRotate}&wb_label_scale=${ps.wbScale}&wb_label_offset_x=${ps.wbOffsetX}&wb_label_offset_y=${ps.wbOffsetY}&defer_print=1&station_code=${encodeURIComponent(ps.station)}&device_id=${encodeURIComponent(state.deviceId||'')}`,{method:'POST',timeoutMs:15000,body:JSON.stringify({credential_id:wbCredential(),order_id:currentOrder.id,chz,print_station:ps.station})});
    if(r.packed&&r.printed){
      $('wbPackProduct').disabled=false;resetWbPacking(true);return;
    }
    $('wbPackChzHint').textContent=r.message||'Ждём подтверждение WB…';$('wbPackChzHint').className='';
    await waitWbStrictPackingGate(currentOrder.id,r.order?.external_order_id||currentOrder.external_order_id,ps);
  }catch(err){
    $('wbPackProduct').disabled=true;$('wbPackChz').disabled=false;if($('wbPackingResetBtn'))$('wbPackingResetBtn').disabled=false;
    $('wbPackChzHint').textContent='⚠ '+err.message;$('wbPackChzHint').className='bad';toast(err.message,true);
    setTimeout(()=>{$('wbPackChz').select();$('wbPackChz').focus();},30);
  }finally{state.wbScanBusy=false;}
}'''


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--root', required=True)
    args = ap.parse_args()
    root = Path(args.root)
    main_py = root / 'app/main.py'
    mp_py = root / 'app/routers/marketplaces.py'
    app_js = root / 'app/static/app.js'

    main_src = main_py.read_text(encoding='utf-8')
    mp = mp_py.read_text(encoding='utf-8')
    js = app_js.read_text(encoding='utf-8')

    if MARKER in mp and 'version="0.9.4.8"' in main_src:
        print('Already patched 0.9.4.8')
        return
    if 'version="0.9.4.7"' not in main_src:
        raise RuntimeError('Expected live foundation 0.9.4.7')
    if '0.9.4.7 DB TX AUTO LABEL STABILITY' not in mp:
        raise RuntimeError('Expected 0.9.4.7 marketplaces foundation')

    # Version only; keep every other main.py reliability change untouched.
    main_src = main_src.replace('version="0.9.4.7"', 'version="0.9.4.8"', 1)

    # Never clear a warehouse-scanned CHZ just because one WB metadata projection is empty.
    anchor = '''    if actual != order.chz_code:\n        # Reconciliation must be idempotent.'''
    guard = '''    # 0.9.4.8: an empty WB projection is not proof that the physical CHZ vanished.\n    # Keep the scanner value until explicit delete/replace or the confirmed-cancel release path.\n    if not actual and order.chz_code:\n        return changed\n\n    if actual != order.chz_code:\n        # Reconciliation must be idempotent.'''
    if anchor not in mp:
        raise RuntimeError('CHZ reconciliation anchor not found')
    mp = mp.replace(anchor, guard, 1)

    # While the strict gate owns this CHZ, an ordinary WB metadata sweep may observe
    # acceptance but must NOT clear the pending marker. Clearing it would let the normal
    # workflow reconciler mark PACKED before the physical label is printed.
    accepted_anchor = '''        if state.get("accepted"):
            if _clear_pending_wb_chz(order):'''
    accepted_repl = '''        if state.get("accepted"):
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
            if _clear_pending_wb_chz(order):'''
    if accepted_anchor not in mp:
        raise RuntimeError('CHZ accepted reconciliation anchor not found')
    mp = mp.replace(accepted_anchor, accepted_repl, 1)

    # Insert strict worker helpers just before the scan-chz route.
    scan_decorator = '@router.post("/wb/packing/scan-chz")\n'
    if scan_decorator not in mp:
        raise RuntimeError('scan-chz decorator not found')
    mp = mp.replace(scan_decorator, STRICT_HELPERS.strip() + '\n\n' + scan_decorator, 1)
    mp = replace_function(mp, 'wb_scan_chz', SCAN_CHZ)
    mp = replace_function(mp, '_retry_pending_wb_chz', RETRY_PENDING)

    # Insert progress endpoint immediately after scan-chz function and before manual CHZ.
    manual_anchor = '@router.post("/wb/orders/{order_id}/chz/manual")\n'
    if manual_anchor not in mp:
        raise RuntimeError('manual CHZ route anchor not found')
    mp = mp.replace(manual_anchor, PROGRESS_ROUTE.strip() + '\n\n\n' + manual_anchor, 1)
    ast.parse(mp)

    # Frontend: reset must always unlock the product input after a strict gate/error.
    reset_anchor = "  $('wbPackProduct').value='';$('wbPackChz').value='';$('wbPackChz').disabled=true;"
    reset_repl = "  $('wbPackProduct').disabled=false;$('wbPackProduct').value='';$('wbPackChz').value='';$('wbPackChz').disabled=true;"
    if reset_anchor not in js:
        raise RuntimeError('resetWbPacking anchor not found')
    js = js.replace(reset_anchor, reset_repl, 1)

    # Replace the whole CHZ handler using the stable marker that immediately follows it.
    # This avoids trying to parse JavaScript template literals/braces in Python.
    process_anchor = 'async function processWbChzScan(){'
    process_tail = '\nlet wbProductTimer=null,wbChzTimer=null;'
    if process_anchor not in js or process_tail not in js:
        raise RuntimeError('processWbChzScan boundaries not found')
    start = js.index(process_anchor)
    end = js.index(process_tail, start)
    js = js[:start] + JS_GATE.strip() + '\n' + PROCESS_CHZ_JS.strip() + js[end:]

    main_py.write_text(main_src, encoding='utf-8')
    mp_py.write_text(mp, encoding='utf-8')
    app_js.write_text(js, encoding='utf-8')
    print('OK: 0.9.4.8 strict CHZ -> label -> print gate applied')


if __name__ == '__main__':
    main()
