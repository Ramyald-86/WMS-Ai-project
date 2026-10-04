#!/usr/bin/env python3
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MP = ROOT / "app/routers/marketplaces.py"
RS = ROOT / "app/services/stock_reservations.py"
MAIN = ROOT / "app/main.py"


def func_source(src: str, name: str) -> str:
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return "\n".join(lines[node.lineno - 1:node.end_lineno])
    raise AssertionError(f"function missing: {name}")


def route_ok(src: str, name: str, method: str, path: str) -> bool:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != name:
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call) or not dec.args:
                continue
            fn = dec.func
            if not (isinstance(fn, ast.Attribute) and fn.attr == method):
                continue
            if not (isinstance(fn.value, ast.Name) and fn.value.id == "router"):
                continue
            arg = dec.args[0]
            if isinstance(arg, ast.Constant) and arg.value == path:
                return True
    return False


def main() -> None:
    mp = MP.read_text(encoding="utf-8")
    rs = RS.read_text(encoding="utf-8")
    main_py = MAIN.read_text(encoding="utf-8")
    ast.parse(mp); ast.parse(rs); ast.parse(main_py)

    assert "0.9.4.8b FIFO PACKING EMERGENCY HOTFIX" in mp
    assert 'version="0.9.4.8b"' in main_py

    scan = func_source(mp, "wb_scan_product")
    assert 'MarketplaceOrder.local_status.in_(["NEW", "PICKING"])' in scan
    assert "_packing_credential_ids_for_scan" in scan
    assert "ensure_packing_reservation_for_scan" in scan
    assert "PACKING_FIFO_WITHOUT_RESERVATION" in scan
    assert "matched.local_status = \"PICKING\"" in scan
    assert "WBClient" not in scan
    assert "client." not in scan
    assert "Заказ не проходил через сборочный лист. Товар не зарезервирован, упаковать нельзя." not in scan
    assert "Резерв по заказу не найден. Обратитесь к старшему смены." not in scan

    helper = func_source(rs, "ensure_packing_reservation_for_scan")
    assert "FIFO_PACKING_REASSIGNED_TO_OLDER_ORDER_0_9_4_8b" in helper
    assert "PACKING_FIFO_AUTO_RESERVE_0_9_4_8b" in helper
    assert "reserve_marketplace_unit" in helper
    assert "FOR UPDATE SKIP LOCKED" in helper
    assert "donor_time <= target_time" in helper

    consume = func_source(rs, "consume_order_reservation")
    assert "if not order.last_picking_list_id:" not in consume
    assert 'reason": "no_reservation"' in consume

    finalize = func_source(mp, "_finalize_strict_pack_from_job")
    assert '_consume_picking_reservation_strict(db2, order, "WB_PACKED_AFTER_PRINT_ACK", require=False)' in finalize
    assert 'if not outcome.get("success")' in finalize
    assert 'order.local_status = "PACKED"' in finalize
    assert finalize.index('if not outcome.get("success")') < finalize.index('order.local_status = "PACKED"')

    chz = func_source(mp, "wb_scan_chz")
    assert "STRICT_CHZ_PRINT_GATE_0_9_4_8" in chz
    assert "order.local_status = \"PICKING\"" in chz
    assert "_schedule_strict_pack_gate" in chz
    assert "_packing_credential_ids_for_scan(db, cred)" in chz

    for name, method, path in [
        ("wb_scan_product", "post", "/wb/packing/scan-product"),
        ("wb_scan_chz", "post", "/wb/packing/scan-chz"),
        ("wb_packing_progress", "get", "/wb/packing/progress"),
        ("wb_queue_order_label", "post", "/wb/orders/{order_id}/label/queue"),
    ]:
        assert route_ok(mp, name, method, path), f"route missing: {method.upper()} {path}"

    print("OK: 0.9.4.8b static smoke passed")
    print("FIFO: NEW + PICKING, oldest WB order first")
    print("RESERVATION: follows FIFO best-effort, never blocks CHZ start")
    print("PRINT GATE: unchanged; PACKED only after Print Agent success ACK")
    print("ROUTES: all critical packing routes present")


if __name__ == "__main__":
    main()
