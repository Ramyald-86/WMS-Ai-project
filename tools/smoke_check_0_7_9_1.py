#!/usr/bin/env python3
"""Static release smoke-check for FulfillCore 0.7.9.1 cumulative overlay."""
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


def require_near(path: pathlib.Path, anchor: str, needle: str, before: int = 900) -> None:
    value = text(path)
    pos = value.find(anchor)
    if pos < 0:
        raise AssertionError(f"{path.relative_to(ROOT)}: missing anchor {anchor!r}")
    if needle not in value[max(0, pos-before):pos+len(anchor)+150]:
        raise AssertionError(f"{path.relative_to(ROOT)}: {needle!r} not near {anchor!r}")


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

    require(main_py, 'version="0.7.9.1"')
    require(main_py, "install_stock_reservation_schema")
    require(main_py, "backfill_legacy_reservations")

    require(migration, "CREATE TABLE IF NOT EXISTS fc_stock_reservations")
    require(migration, "uq_fc_stock_reservations_live_order")
    require(reserve, "def reserve_orders_from_picking_rows")
    require(reserve, "def consume_order_reservation")
    require(reserve, "def cancel_order_to_receiving")
    require(reserve, "def receiving_queue")
    require(reserve, "status='CANCELLED_TO_RECEIVING'")
    require(reserve, 'code="ПРИЕМКА"')
    require(reserve, "status IN ('ACTIVE','CONSUMING','CANCELLING')")
    require(reserve, "system-location-receiving")
    require(reserve, "stock-reservation-backfill")
    require(reserve, "WB_PACKED_FROM_RESERVATION")
    require(reserve, "WB_CANCEL_AFTER_PICKING_RECEIVING")
    require(reserve, "WB_CANCEL_AFTER_PACKING_RECEIVING")

    require(guard, "active_reserved_for_bucket")
    require(guard, "зарезервировано")
    require(layout, '"reserved_quantity"')
    require(layout, '"free_quantity"')

    require(market, '@router.get("/wb/receiving-queue")')
    require(market, "reserve_orders_from_picking_rows")
    require(market, "picking-stock:")
    require(market, "free_quantity")
    require(market, "SYSTEM_LOCATION_CODES")
    require(market, "CANCELLED_ITEM_TO_RECEIVING")
    if "CANCELLED_ITEM_TO_UNPLACED" in text(market) or "WB_CANCEL_AFTER_PICKING_UNPLACED" in text(market):
        raise AssertionError("legacy cancellation-to-unplaced path is still active")

    # Every direct PACKED assignment in this overlay must be close to a reservation
    # consume/reconcile call. This guards future edits that reintroduce stock drift.
    market_text = text(market)
    start = 0
    while True:
        pos = market_text.find('order.local_status = "PACKED"', start)
        if pos < 0:
            break
        window = market_text[max(0, pos-1200):pos]
        if "consume" not in window.lower():
            raise AssertionError(f"PACKED assignment at byte {pos} has no nearby reservation consume")
        start = pos + 1

    require(app_js, "ПРИЕМКА · требует размещения")
    require(app_js, "/marketplaces/wb/receiving-queue")
    require(app_js, "reserved_quantity")
    require(app_js, "free_quantity")
    require(app_js, "Товар, который нужно разместить")

    # Keep all 0.7.9.0 reliability/print guarantees in the cumulative release.
    require(market, "/wb/packing/release-order")
    require(market, "acquire_packing_lease")
    require(APP / "services" / "runtime_reliability.py", "fc_packing_leases")
    require(APP / "static" / "app.js", "FULFILLCORE_DEVICE_ID")
    require(APP / "routers" / "layout.py", "LOCATION_QR_LABEL_BATCH")

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
    print(f"OK: FulfillCore 0.7.9.1 smoke-check passed; Print Agent SHA-256={digest}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise
