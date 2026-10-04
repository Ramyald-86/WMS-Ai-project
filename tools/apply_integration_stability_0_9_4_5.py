#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

MARKER = "0.9.4.5 WB CHZ PRINT STABILITY"


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
    first = start - 1
    while first > 0 and lines[first - 1].lstrip().startswith("@"):
        first -= 1
    lines[first:end] = [replacement.rstrip() + "\n\n"]
    out = "".join(lines)
    ast.parse(out)
    return out


CHZ_BG_HELPER = r'''
# 0.9.4.5 WB CHZ PRINT STABILITY
# Keep WB network I/O outside the packing DB transaction, but do not blindly call
# /meta/sgtin while the assembly order is not in supplierStatus=confirm.
# Official WB API allows SGTIN only in confirm. A 409 counts as 10 requests, so the
# previous blind META_RETRY loop could both slow WB and keep many long HTTP calls alive.
_CHZ_BG_COORD_LOCK = threading.Lock()
_CHZ_BG_INFLIGHT: set[int] = set()
_CHZ_BG_NEXT_AT: dict[int, float] = {}
_CHZ_BG_SLOTS = threading.BoundedSemaphore(3)


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
    """Status-aware, rate-limited SGTIN submission with zero SQLAlchemy access.

    Fast warehouse rule stays unchanged: packing commits locally first. This worker only
    talks to WB. For a NEW order already linked to an active supply, it re-adds the order
    to that same supply (the official action that moves it to confirm) and then sends SGTIN.
    It never creates a new supply and never changes FulfillCore rows from the worker.
    Metadata reconciliation remains authoritative and clears the pending flag later.
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
                logger.warning(
                    "FC_CHZ_BG_STATUS source=%s order_num=%s outcome=error error=%s",
                    source, order_number, exc,
                )

            # Adding an FBS order to its existing active supply is the official WB action
            # that changes supplierStatus new -> confirm. Do it once, only when needed.
            if supplier == "new" and sid:
                try:
                    _wb_add_orders_to_supply(client, sid, [order_number])
                    logger.info("FC_CHZ_BG_CONFIRM source=%s order_num=%s supply_id=%s outcome=patched", source, order_number, sid)
                except Exception as exc:
                    logger.warning(
                        "FC_CHZ_BG_CONFIRM source=%s order_num=%s supply_id=%s outcome=error error=%s",
                        source, order_number, sid, exc,
                    )
                # WB status projection can lag briefly after PATCH. Keep this bounded.
                for pause in (0.25, 0.50, 0.80):
                    time.sleep(pause)
                    try:
                        supplier = _wb_supplier_status_without_db(client, order_number)
                    except Exception:
                        supplier = ""
                    if supplier == "confirm":
                        break

            if supplier != "confirm":
                # Do not hammer /meta/sgtin with 409s. A later metadata sweep/manual retry
                # can try again. Complete orders need the existing reshipment/manual path.
                outcome = f"deferred_status_{supplier or 'unknown'}"
                cooldown = 45.0 if supplier in {"new", ""} else 180.0
                logger.warning(
                    "FC_CHZ_BG_DEFER source=%s order_num=%s supplier_status=%s supply_id=%s",
                    source, order_number, supplier or "unknown", sid,
                )
                return

            try:
                client.set_sgtin(order_number, [chz])
            except HTTPException as exc:
                code = _wb_api_error_status(exc)
                # A short WB projection race can still return 409 after status=confirm.
                # Re-check once and retry exactly once; never start an unbounded loop.
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
            logger.info(
                "FC_CHZ_BG_SUBMIT source=%s order_num=%s outcome=ok elapsed_ms=%.1f",
                source, order_number, (time.perf_counter() - started) * 1000.0,
            )
        except Exception as exc:
            outcome = "error"
            cooldown = 30.0
            logger.warning(
                "FC_CHZ_BG_SUBMIT source=%s order_num=%s outcome=error elapsed_ms=%.1f error=%s",
                source, order_number, (time.perf_counter() - started) * 1000.0, exc,
            )
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
    s = read_py(path)
    if MARKER in s:
        print("OK: marketplaces.py already 0.9.4.5")
        return
    if "0.9.4.4 PACKING TRANSACTION FIX" not in s:
        raise RuntimeError("0.9.4.4 foundation missing; refusing to patch unknown version")

    s = replace_function(s, "_submit_wb_chz_background", CHZ_BG_HELPER)

    old_snapshot = '''    pending_snapshot: list[tuple[int, str]] = []\n    for o in orders:\n        if len(pending_snapshot) >= 10:\n            break\n        if not (o.chz_code and _has_pending_wb_chz(o)):\n            continue\n        try:\n            pending_snapshot.append((_wb_order_number(o), str(o.chz_code)))\n        except Exception:\n            continue\n    db.commit()\n    for order_number, chz in pending_snapshot:\n        _submit_wb_chz_background(client, order_number, chz, source="META_RETRY")\n'''
    new_snapshot = '''    pending_snapshot: list[tuple[int, str, str | None]] = []\n    for o in orders:\n        if len(pending_snapshot) >= 10:\n            break\n        if not (o.chz_code and _has_pending_wb_chz(o)):\n            continue\n        try:\n            pending_snapshot.append((_wb_order_number(o), str(o.chz_code), str(o.supply_id or "").strip() or None))\n        except Exception:\n            continue\n    db.commit()\n    for order_number, chz, supply_id in pending_snapshot:\n        _submit_wb_chz_background(client, order_number, chz, source="META_RETRY", supply_id=supply_id)\n'''
    if old_snapshot not in s:
        raise RuntimeError("0.9.4.4 pending CHZ snapshot anchor not found")
    s = s.replace(old_snapshot, new_snapshot, 1)

    old_capture = '''    # Capture every value needed by the worker BEFORE commit; commit may expire ORM rows.\n    order_number = _wb_order_number(order)\n    _timing_add_ms(timing, "finalize_ms", _t)\n'''
    new_capture = '''    # Capture every value needed by the worker BEFORE commit; commit may expire ORM rows.\n    order_number = _wb_order_number(order)\n    supply_id_for_wb = str(order.supply_id or "").strip() or None\n    _timing_add_ms(timing, "finalize_ms", _t)\n'''
    if old_capture not in s:
        raise RuntimeError("scan-chz capture anchor not found")
    s = s.replace(old_capture, new_capture, 1)

    old_call = '_submit_wb_chz_background(client, order_number, chz, source="PACKING")'
    new_call = '_submit_wb_chz_background(client, order_number, chz, source="PACKING", supply_id=supply_id_for_wb)'
    if old_call not in s:
        raise RuntimeError("scan-chz background call anchor not found")
    s = s.replace(old_call, new_call, 1)

    # Expose the last WB error to the card without adding any network call.
    old_reason = '"chz_pending_reason": ((order.raw_data or {}).get("_fc_chz_pending_wb") or {}).get("reason") if isinstance((order.raw_data or {}).get("_fc_chz_pending_wb"), dict) else None,'
    new_reason = old_reason + '\n        "chz_pending_error": ((order.raw_data or {}).get("_fc_chz_pending_wb") or {}).get("last_error") if isinstance((order.raw_data or {}).get("_fc_chz_pending_wb"), dict) else None,'
    if old_reason not in s:
        raise RuntimeError("order response CHZ reason anchor not found")
    s = s.replace(old_reason, new_reason, 1)

    old_compact = '"chz_attached":bool(order.chz_code),"chz_pending_wb":_has_pending_wb_chz(order),"chz_pending_reason":(((order.raw_data or {}).get("_fc_chz_pending_wb") or {}).get("reason") if isinstance((order.raw_data or {}).get("_fc_chz_pending_wb"),dict) else None),"chz_preview"'
    new_compact = '"chz_attached":bool(order.chz_code),"chz_pending_wb":_has_pending_wb_chz(order),"chz_pending_reason":(((order.raw_data or {}).get("_fc_chz_pending_wb") or {}).get("reason") if isinstance((order.raw_data or {}).get("_fc_chz_pending_wb"),dict) else None),"chz_pending_error":(((order.raw_data or {}).get("_fc_chz_pending_wb") or {}).get("last_error") if isinstance((order.raw_data or {}).get("_fc_chz_pending_wb"),dict) else None),"chz_preview"'
    if old_compact not in s:
        raise RuntimeError("compact order response CHZ anchor not found")
    s = s.replace(old_compact, new_compact, 1)

    # Marker near the worker is already present in replacement. Validate syntax.
    ast.parse(s)
    path.write_text(s, encoding="utf-8")
    print("OK: marketplaces.py 0.9.4.5 CHZ worker patched")


def patch_js(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    if "0.9.4.5 order card nonblocking WB refresh" in s:
        print("OK: app.js already 0.9.4.5")
        return

    old = '''    const cached=state.wbOrders.find(x=>x.id===localId);\n    if(cached?.external_order_id){try{await api('/marketplaces/wb/orders/meta-refresh',{method:'POST',body:JSON.stringify({credential_id:wbCredential(),order_ids:[Number(cached.external_order_id)]})});}catch(e){console.warn('WB order refresh:',e)}}\n    const wh=state.warehouseId?`&warehouse_id=${encodeURIComponent(state.warehouseId)}`:'';\n    const o=await api(`/marketplaces/wb/orders/${encodeURIComponent(localId)}?credential_id=${encodeURIComponent(wbCredential())}${wh}`);\n'''
    new = '''    const cached=state.wbOrders.find(x=>x.id===localId);\n    const wh=state.warehouseId?`&warehouse_id=${encodeURIComponent(state.warehouseId)}`:'';\n    /* 0.9.4.5 order card nonblocking WB refresh: open from local DB immediately. */\n    const o=await api(`/marketplaces/wb/orders/${encodeURIComponent(localId)}?credential_id=${encodeURIComponent(wbCredential())}${wh}`);\n    if(cached?.external_order_id){api('/marketplaces/wb/orders/meta-refresh',{method:'POST',timeoutMs:12000,body:JSON.stringify({credential_id:wbCredential(),order_ids:[Number(cached.external_order_id)]})}).then(()=>loadWbOrders(false)).catch(e=>console.warn('WB order background refresh:',e));}\n'''
    if old not in s:
        raise RuntimeError("openWbOrder blocking refresh anchor not found")
    s = s.replace(old, new, 1)

    old_chz = '''<div class="order-chz-box"><small>Код маркировки</small><div class="mono-wrap">${o.chz_code?esc(o.chz_code):'<span class="muted">Код маркировки не присвоен</span>'}</div>${o.chz_pending_wb&&!String(o.chz_pending_reason||'').includes('SUBMITTED')&&!String(o.chz_pending_reason||'').includes('LOCAL_PACKED')?'<small class="subline" style="color:#b56a00">⚠ WB не принял отправку ЧЗ. Код сохранён в FulfillCore — исправьте причину и повторите передачу.</small>':''}${watch?'':`<div class="inline-controls mt10"><input id="wbOrderManualChzInput" type="text" placeholder="Отсканируйте или вставьте ЧЗ для ручной привязки" autocomplete="off"/><button class="secondary" data-order-detail-save-chz="${o.id}">Сохранить / заменить ЧЗ</button>${o.chz_pending_wb&&o.chz_code&&!String(o.chz_pending_reason||'').includes('SUBMITTED')&&!String(o.chz_pending_reason||'').includes('LOCAL_PACKED')?`<button class="primary" data-order-detail-retry-chz="${o.id}">Передать ЧЗ в WB повторно</button>`:''}</div>`}</div>'''
    new_chz = '''<div class="order-chz-box"><small>Код маркировки</small><div class="mono-wrap">${o.chz_code?esc(o.chz_code):'<span class="muted">Код маркировки не присвоен</span>'}</div>${o.chz_pending_wb?`<small class="subline" style="color:#b56a00">⏳ ЧЗ сохранён в FulfillCore. WB ещё не подтвердил код; повторная отправка выполняется автоматически.${o.chz_pending_error?' Последний ответ: '+esc(String(o.chz_pending_error).slice(0,220)):''}</small>`:''}${watch?'':`<div class="inline-controls mt10"><input id="wbOrderManualChzInput" type="text" placeholder="Отсканируйте или вставьте ЧЗ для ручной привязки" autocomplete="off"/><button class="secondary" data-order-detail-save-chz="${o.id}">Сохранить / заменить ЧЗ</button>${o.chz_pending_wb&&o.chz_code?`<button class="primary" data-order-detail-retry-chz="${o.id}">Повторить ЧЗ в WB</button>`:''}</div>`}</div>'''
    if old_chz not in s:
        raise RuntimeError("order card CHZ warning anchor not found")
    s = s.replace(old_chz, new_chz, 1)
    path.write_text(s, encoding="utf-8")
    print("OK: app.js nonblocking card + CHZ pending UI patched")


def patch_main(path: Path) -> None:
    s = read_py(path)
    s, n = re.subn(r'version="0\.9\.4(?:\.\d+)?"', 'version="0.9.4.5"', s, count=1)
    if n != 1:
        raise RuntimeError("FastAPI 0.9.4.x version literal not found")
    s = re.sub(r'PRINT_AGENT_ARCHIVE = DOWNLOADS_DIR / "[^"]+"', 'PRINT_AGENT_ARCHIVE = DOWNLOADS_DIR / "FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip"', s, count=1)
    s = re.sub(r'PRINT_AGENT_INSTRUCTION = DOWNLOADS_DIR / "[^"]+"', 'PRINT_AGENT_INSTRUCTION = DOWNLOADS_DIR / "FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE_INSTRUCTION_RU.txt"', s, count=1)
    # The installer archive/help page are safe to download without a FulfillCore session.
    # The token endpoint remains protected because only the exact help page is public.
    public_old = 'public = path in {"/login", "/auth/login", "/commercial-login", "/commercial/auth/login", "/health"} or path.startswith("/static/")'
    public_new = 'public = path in {"/login", "/auth/login", "/commercial-login", "/commercial/auth/login", "/health", "/help/print-agent"} or path.startswith("/static/") or path.startswith("/downloads/print-agent/")'
    if public_old in s:
        s = s.replace(public_old, public_new, 1)
    elif '/downloads/print-agent/' not in s:
        raise RuntimeError("main.py public-route anchor not found")
    ast.parse(s)
    path.write_text(s, encoding="utf-8")
    print("OK: main.py version/downloads -> 0.9.4.5 / Print Agent 1.1.7 SAFE SIMPLE")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    a = ap.parse_args()
    root = Path(a.root)
    patch_marketplaces(root / "app/routers/marketplaces.py")
    patch_js(root / "app/static/app.js")
    patch_main(root / "app/main.py")


if __name__ == "__main__":
    main()
