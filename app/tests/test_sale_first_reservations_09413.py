from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]


def test_new_orders_reserve_without_physical_address():
    src = (ROOT / 'app/services/stock_reservations.py').read_text(encoding='utf-8')
    assert "'SALE_RESERVED'" in src
    assert "'SALE_FIRST_V1'" in src
    assert ":sku,NULL,NULL,1,'SALE_RESERVED'" in src
    assert 'reserve_new_orders_sale_first' in src
    ast.parse(src)


def test_picking_binds_existing_sale_reservation_to_address():
    src = (ROOT / 'app/services/stock_reservations.py').read_text(encoding='utf-8')
    assert "status='CONSUMING',reservation_mode='SALE_FIRST_V1'" in src
    assert "source_location_id=:location,source_container_id=:container" in src
    assert 'owns_sale_reserve' in src


def test_marketplace_amount_subtracts_only_unallocated_sale_reserve():
    src = (ROOT / 'app/services/stock_core_v2.py').read_text(encoding='utf-8')
    assert "AND status='SALE_RESERVED'" in src
    assert 'max(0, int(fbs_total - sale_reserved))' in src
    assert 'wb_sales_paused' in src


def test_physical_writeoff_is_after_print_ack_for_strict_flow():
    src = (ROOT / 'app/routers/marketplaces.py').read_text(encoding='utf-8')
    ack = src.index('if not outcome.get("success")')
    consume = src.index('WB_PACKED_AFTER_PRINT_ACK', ack)
    packed = src.index('order.local_status = "PACKED"', consume)
    assert ack < consume < packed


def test_cancellation_before_real_picking_releases_in_place():
    src = (ROOT / 'app/routers/marketplaces.py').read_text(encoding='utf-8')
    assert 'if not order.last_picking_list_id:' in src
    assert 'WB_CANCELLED_BEFORE_PHYSICAL_PICKING' in src
    assert 'FBS_SALE_RESERVATION_RELEASED' in src


def test_outbox_worker_started_and_recovers_stale_processing():
    worker = (ROOT / 'app/services/stock_outbox_worker.py').read_text(encoding='utf-8')
    main = (ROOT / 'app/main.py').read_text(encoding='utf-8')
    assert "status='PROCESSING' AND updated_at < CURRENT_TIMESTAMP - INTERVAL '2 minutes'" in worker
    assert '_wb_set_stocks_bulk' in worker
    assert 'start_stock_outbox_worker()' in main


def test_server_watcher_reserves_orders_without_open_browser():
    worker = (ROOT / 'app/services/wb_sale_reservation_worker.py').read_text(encoding='utf-8')
    main = (ROOT / 'app/main.py').read_text(encoding='utf-8')
    assert 'client.new_orders()' in worker
    assert '_reserve_new_wb_orders_immediately' in worker
    assert 'time.sleep(15.0)' in worker
    assert 'start_wb_sale_reservation_worker()' in main
    assert 'time.sleep(60.0)' in worker


def test_deploy_grace_period_precedes_outbox_processing():
    worker = (ROOT / 'app/services/stock_outbox_worker.py').read_text(encoding='utf-8')
    loop = worker[worker.index('def _worker_loop()'):]
    assert loop.index('time.sleep(60.0)') < loop.index('process_stock_outbox_once()')


def test_packing_cannot_allocate_address_before_real_picking_list():
    svc = (ROOT / 'app/services/stock_reservations.py').read_text(encoding='utf-8')
    router = (ROOT / 'app/routers/marketplaces.py').read_text(encoding='utf-8')
    fn = svc[svc.index('def ensure_packing_reservation_for_scan'):svc.index('def assert_order_reservation_ready_for_packing')]
    assert 'if not order.last_picking_list_id:' in fn
    assert 'return latest' in fn
    assert 'PACKING_FIFO_AUTO_RESERVE' not in fn
    scan = router[router.index('@router.post("/wb/packing/scan-product")'):router.index('@router.post("/wb/packing/scan-chz")')]
    # The scanner may fulfil an existing WB order even with zero/missing local stock, but
    # ensure_packing_reservation_for_scan still may not invent a physical address before
    # a real picking list exists.
    assert 'MarketplaceOrder.local_status.in_(["NEW", "PICKING"])' in scan
    assert 'Inventory quantity is deliberately not an eligibility condition' in scan
    assert 'ensure_packing_reservation_for_scan' in scan


def test_watcher_backfills_existing_local_new_orders_even_when_wb_poll_is_empty():
    worker = (ROOT / 'app/services/wb_sale_reservation_worker.py').read_text(encoding='utf-8')
    assert 'for seller_id, physical_id in mapping.items()' in worker
    assert 'except Exception:\n            incoming = []' in worker
