#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

MARKER = "0.9.4.3 PACKING FIFO STABILITY"


def _read(path: Path) -> str:
    s = path.read_text(encoding="utf-8")
    ast.parse(s)
    return s


def patch_stock_core(path: Path) -> None:
    s = _read(path)
    if MARKER in s:
        print("OK: stock_core_v2.py already patched")
        return
    anchor = '    if row["reserved"] < Decimal(1) - EPS:\n        raise HTTPException(409, detail="Резерв Stock Core v2 отсутствует")\n'
    if anchor not in s:
        raise RuntimeError("stock_core_v2 consume anchor not found")
    insert = anchor + '''    # 0.9.4.3 PACKING FIFO STABILITY:\n    # The global location guard protects ACTIVE/CONSUMING reservations from manual\n    # stock edits. Final packing is different: this very transaction is consuming the\n    # reserved unit. Hide this reservation from the guard only inside the current\n    # uncommitted transaction before execute_operation() flushes InventoryBalance.\n    # PostgreSQL MVCC means other sessions still see the previous ACTIVE row until the\n    # final commit. Any exception rolls this temporary state back together with stock.\n    db.execute(\n        text(\n            "UPDATE fc_stock_reservations SET status='PACKING_WRITE_OFF', "\n            "updated_at=CURRENT_TIMESTAMP,note=:note "\n            "WHERE id=:id AND status IN ('ACTIVE','CONSUMING')"\n        ),\n        {"id": str(reservation["id"]), "note": f"PACKING_WRITE_OFF:{note}"},\n    )\n'''
    s = s.replace(anchor, insert, 1)
    ast.parse(s)
    path.write_text(s, encoding="utf-8")
    print("OK: patched stock_core_v2 packing write-off guard interaction")


def patch_reservations(path: Path) -> None:
    s = _read(path)
    if "def _fifo_reassign_newer_reservation_to_older_order" not in s:
        target = "\ndef assert_order_reservation_ready_for_packing(db: Session, order: MarketplaceOrder) -> dict[str, Any] | None:\n"
        if target not in s:
            raise RuntimeError("assert_order_reservation_ready_for_packing anchor not found")
        helper = r'''

def _fifo_reassign_newer_reservation_to_older_order(db: Session, order: MarketplaceOrder) -> dict[str, Any] | None:
    """Repair reservation drift so the oldest physical order wins at packing.

    A later order may own the only ACTIVE unit because reservations are created by
    background synchronization/picking-list timing. If an older order for the exact
    same SKU is scanned first, warehouse FIFO must win: move one untouched reservation
    from a newer NEW/PICKING order to this older order. The physical source cell/container
    does not move and total reserved stock does not change.
    """
    if not order.local_sku_id or not order.last_picking_list_id:
        return None
    try:
        picking_id = uuid.UUID(str(order.last_picking_list_id))
        picking = db.get(MarketplacePickingList, picking_id)
    except Exception:
        picking = None
    if not picking or not getattr(picking, "warehouse_id", None):
        return None

    target_time = order.external_created_at or order.created_at
    with db.no_autoflush:
        candidates = db.execute(
            text(
                """
                SELECT id,order_id,external_order_id,picking_list_id,client_id,warehouse_id,
                       sku_id,source_location_id,source_container_id,quantity,status,created_at,
                       updated_at,consumed_at,receiving_at,released_at,note
                FROM fc_stock_reservations
                WHERE client_id=:client AND warehouse_id=:warehouse AND sku_id=:sku
                  AND status='ACTIVE' AND order_id<>:target
                ORDER BY created_at ASC
                FOR UPDATE
                """
            ),
            {
                "client": str(order.client_id),
                "warehouse": str(picking.warehouse_id),
                "sku": str(order.local_sku_id),
                "target": str(order.id),
            },
        ).mappings().all()

    for row in candidates:
        try:
            donor = db.get(MarketplaceOrder, uuid.UUID(str(row["order_id"])))
        except Exception:
            donor = None
        if not donor or donor.provider != "WB":
            continue
        if str(donor.local_status or "").upper() not in {"NEW", "PICKING"}:
            continue
        # Never take a unit from an order whose packing has already physically started.
        if donor.product_scanned_at or donor.chz_code or donor.packed_at:
            continue
        donor_time = donor.external_created_at or donor.created_at
        if target_time and donor_time and donor_time <= target_time:
            continue

        result = db.execute(
            text(
                """
                UPDATE fc_stock_reservations
                SET order_id=:target,external_order_id=:external,picking_list_id=:picking,
                    updated_at=CURRENT_TIMESTAMP,note='FIFO_REASSIGNED_TO_OLDER_ORDER'
                WHERE id=:id AND status='ACTIVE' AND order_id=:donor
                """
            ),
            {
                "target": str(order.id),
                "external": str(order.external_order_id or order.external_order_num or ""),
                "picking": str(order.last_picking_list_id),
                "id": str(row["id"]),
                "donor": str(donor.id),
            },
        )
        if result.rowcount:
            healed = dict(row)
            healed.update({
                "order_id": str(order.id),
                "external_order_id": str(order.external_order_id or order.external_order_num or ""),
                "picking_list_id": str(order.last_picking_list_id),
                "note": "FIFO_REASSIGNED_TO_OLDER_ORDER",
            })
            return healed
    return None
'''
        s = s.replace(target, helper + target, 1)

    old = '''    if not latest:\n        latest = ensure_legacy_reservation_for_order(db, order)\n    if not latest:\n'''
    new = '''    if not latest:\n        latest = ensure_legacy_reservation_for_order(db, order)\n    if not latest:\n        # 0.9.4.3 PACKING FIFO STABILITY: shortage across several same-SKU orders\n        # must never block the oldest order that the operator physically scans first.\n        # Heal a newer untouched reservation onto this older order without changing\n        # total physical/reserved stock. The newer order becomes the deficit instead.\n        latest = _fifo_reassign_newer_reservation_to_older_order(db, order)\n    if not latest:\n'''
    fifo_call_marker = "latest = _fifo_reassign_newer_reservation_to_older_order(db, order)"
    if fifo_call_marker not in s:
        if old not in s:
            raise RuntimeError("reservation missing-latest anchor not found")
        s = s.replace(old, new, 1)
    if MARKER not in s:
        s = s.replace('def assert_order_reservation_ready_for_packing', f'# {MARKER}\ndef assert_order_reservation_ready_for_packing', 1)
    ast.parse(s)
    path.write_text(s, encoding="utf-8")
    print("OK: patched stock_reservations FIFO healing")


def patch_runtime(path: Path) -> None:
    s = _read(path)
    marker = "0.9.4.3 PACKING LEASE NONBLOCKING"
    if marker in s:
        print("OK: runtime_reliability.py already patched")
        return
    old = '''    with distributed_lock(db, f"packing-order:{oid}", blocking=True):\n        db.execute(text("DELETE FROM fc_packing_leases WHERE order_id=:order_id AND leased_until_epoch < :now"), {"order_id": oid, "now": now})\n'''
    new = '''    # 0.9.4.3 PACKING LEASE NONBLOCKING: scanner requests must never queue behind\n    # a stale/adversarial advisory lock. Busy order -> immediately try next candidate.\n    with distributed_lock(db, f"packing-order:{oid}", blocking=False) as acquired:\n        if not acquired:\n            return False\n        db.execute(text("DELETE FROM fc_packing_leases WHERE order_id=:order_id AND leased_until_epoch < :now"), {"order_id": oid, "now": now})\n'''
    if old not in s:
        raise RuntimeError("packing lease blocking anchor not found")
    s = s.replace(old, new, 1)
    ast.parse(s)
    path.write_text(s, encoding="utf-8")
    print("OK: patched nonblocking packing lease")


def patch_marketplaces(path: Path) -> None:
    s = _read(path)
    # 0.9.4.2 must remain present; refuse to regress database stability.
    if "0.9.4.2 DATABASE STABILITY" not in s:
        raise RuntimeError("0.9.4.2 DATABASE STABILITY marker missing from live marketplaces.py")
    print("OK: 0.9.4.2 database stability preserved")


def patch_main(path: Path) -> None:
    s = _read(path)
    if 'version="0.9.4.3"' in s:
        print("OK: main.py already 0.9.4.3")
        return
    out, n = re.subn(r'version="0\.9\.4(?:\.2)?"', 'version="0.9.4.3"', s, count=1)
    if n != 1:
        raise RuntimeError("current FastAPI 0.9.4/0.9.4.2 version literal not found")
    ast.parse(out)
    path.write_text(out, encoding="utf-8")
    print("OK: health version -> 0.9.4.3")


def patch_js(path: Path) -> None:
    s = path.read_text(encoding="utf-8")
    marker = "0.9.4.3 scan product bounded timeout"
    if marker in s:
        print("OK: app.js already patched")
        return
    old = "const r=await api('/marketplaces/wb/packing/scan-product?station_code='+encodeURIComponent(state.wbPrintStation||'PACK-01')+'&device_id='+encodeURIComponent(state.deviceId||''),{method:'POST',body:JSON.stringify({credential_id:wbCredential(),warehouse_id:state.warehouseId,barcode})});"
    new = "/* 0.9.4.3 scan product bounded timeout */ const r=await api('/marketplaces/wb/packing/scan-product?station_code='+encodeURIComponent(state.wbPrintStation||'PACK-01')+'&device_id='+encodeURIComponent(state.deviceId||''),{method:'POST',timeoutMs:15000,body:JSON.stringify({credential_id:wbCredential(),warehouse_id:state.warehouseId,barcode})});"
    if old not in s:
        raise RuntimeError("scan-product JS anchor not found")
    s = s.replace(old, new, 1)
    path.write_text(s, encoding="utf-8")
    print("OK: app.js packing scan timeout bounded to 15s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    args = ap.parse_args()
    root = Path(args.root)
    patch_marketplaces(root / "app/routers/marketplaces.py")
    patch_stock_core(root / "app/services/stock_core_v2.py")
    patch_reservations(root / "app/services/stock_reservations.py")
    patch_runtime(root / "app/services/runtime_reliability.py")
    patch_js(root / "app/static/app.js")
    patch_main(root / "app/main.py")


if __name__ == "__main__":
    main()
