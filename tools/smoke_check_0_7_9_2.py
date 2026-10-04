#!/usr/bin/env python3
"""Static release smoke-check for FulfillCore 0.7.9.2 cumulative overlay."""
from __future__ import annotations

import compileall
import hashlib
import pathlib
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def text(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def require(path: pathlib.Path, needle: str) -> None:
    value = text(path)
    if needle not in value:
        raise AssertionError(f"{path.relative_to(ROOT)}: missing {needle!r}")


def main() -> int:
    if not compileall.compile_dir(APP, quiet=1, force=True):
        raise AssertionError("Python compilation failed")

    main_py = APP / "main.py"
    market = APP / "routers" / "marketplaces.py"
    layout = APP / "routers" / "layout.py"
    reserve = APP / "services" / "stock_reservations.py"
    guard = APP / "services" / "client_location_guard.py"
    app_js = APP / "static" / "app.js"
    migration = APP / "migrations" / "0_7_9_1_stock_reservations.sql"

    # Version and 0.7.9.1 reservation foundation.
    require(main_py, 'version="0.7.9.2"')
    require(main_py, "install_stock_reservation_schema")
    require(migration, "CREATE TABLE IF NOT EXISTS fc_stock_reservations")
    require(reserve, "def reserve_orders_from_picking_rows")
    require(reserve, "def consume_order_reservation")
    require(reserve, "def cancel_order_to_receiving")
    require(reserve, "def receiving_queue")
    require(reserve, 'code="ПРИЕМКА"')
    require(reserve, "WB_PACKED_FROM_RESERVATION")
    require(reserve, "WB_CANCEL_AFTER_PICKING_RECEIVING")
    require(guard, "active_reserved_for_bucket")
    require(layout, '"reserved_quantity"')
    require(layout, '"free_quantity"')
    require(app_js, "ПРИЕМКА · требует размещения")

    # 0.7.9.2 rule: PACKED cancellation must stay written off and must return before
    # the receiving-location branch can be reached.
    reserve_text = text(reserve)
    packed_anchor = 'if previous_status == "PACKED":'
    packed_pos = reserve_text.find(packed_anchor)
    receiving_lock_pos = reserve_text.find('with distributed_lock(db, f"stock-reservation-order:{order.id}"', packed_pos)
    if packed_pos < 0 or receiving_lock_pos < 0:
        raise AssertionError("PACKED cancellation branch not found")
    packed_block = reserve_text[packed_pos:receiving_lock_pos]
    for required in [
        "WB_CANCEL_AFTER_PACKED_KEEP_WRITTEN_OFF",
        '"action": "KEEP_WRITTEN_OFF"',
        '"reason": "packed_kept_written_off"',
        "consume_order_reservation(",
    ]:
        if required not in packed_block:
            raise AssertionError(f"PACKED cancellation block missing {required!r}")
    if "_ensure_receiving_location" in packed_block or "destination_location_id=receiving.id" in packed_block:
        raise AssertionError("PACKED cancellation still auto-routes stock to receiving")

    require(market, "CANCELLED_AFTER_PACKED_STOCK_KEPT_WRITTEN_OFF")
    require(market, "CANCELLED_AFTER_PACKED_STOCK_REVIEW_REQUIRED")
    require(market, 'action == "KEEP_WRITTEN_OFF"')
    require(market, "CANCELLED_ITEM_TO_RECEIVING")

    # Keep 0.7.9.0 multi-device/print guarantees.
    require(market, "/wb/packing/release-order")
    require(market, "acquire_packing_lease")
    require(APP / "services" / "runtime_reliability.py", "fc_packing_leases")
    require(app_js, "FULFILLCORE_DEVICE_ID")
    require(layout, "LOCATION_QR_LABEL_BATCH")

    agent = APP / "downloads" / "FulfillCore_Print_Agent_1.2.0_RELIABLE_MULTI_DEVICE.zip"
    if not zipfile.is_zipfile(agent):
        raise AssertionError("Print Agent archive is not a valid ZIP")
    with zipfile.ZipFile(agent) as zf:
        names = set(zf.namelist())
        for required in {"print_agent.ps1", "setup.ps1", "README_1.2.0_RU.txt"}:
            if required not in names:
                raise AssertionError(f"Print Agent archive missing {required}")
        ps = zf.read("print_agent.ps1").decode("utf-8-sig")
        if 'Write-JobState $jobId "submitted" "spool-accepted"' not in ps:
            raise AssertionError("Print Agent durable submitted state missing")
        if "ACK failed after successful print" not in ps:
            raise AssertionError("Print Agent ACK/no-reprint guard missing")

    digest = hashlib.sha256(agent.read_bytes()).hexdigest()
    print(f"OK: FulfillCore 0.7.9.2 smoke-check passed; Print Agent SHA-256={digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise
