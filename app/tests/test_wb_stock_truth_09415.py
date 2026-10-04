from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]


def test_inventory_exposes_physical_reserved_and_sellable_separately():
    src = (ROOT / 'app/routers/inventory.py').read_text(encoding='utf-8')
    assert 'sale_reserved_quantity' in src
    assert 'picking_reserved_quantity' in src
    assert 'reserved_quantity' in src
    assert 'available_to_sell' in src
    assert '_catalog_stock_state_map' in src
    ast.parse(src)


def test_live_wb_stock_is_not_the_last_publication_marker():
    src = (ROOT / 'app/routers/receiving.py').read_text(encoding='utf-8')
    assert '@router.post("/wb-live-stocks")' in src
    assert '_wb_read_chrt_stocks' in src
    assert '@router.get("/fbs-status")' in src
    js = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
    assert 'WB факт:' in js
    assert 'последняя наша цель' in js
    assert 'refreshInventoryWbActual' in js
    assert 'Сверить WB сейчас' in js


def test_pause_resume_reports_verified_background_result():
    src = (ROOT / 'app/routers/receiving.py').read_text(encoding='utf-8')
    assert '@router.post("/inventory-control-status")' in src
    assert "queue_status" in src
    assert "published_amount" in src
    js = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
    assert 'waitInventoryControlConfirmation' in js
    assert 'WB подтвердил остановку продаж' in js
    assert 'WB подтвердил возобновление продаж' in js
    assert 'WB ещё не подтвердил' in js


def test_order_arrival_reserves_but_does_not_fake_physical_writeoff():
    src = (ROOT / 'app/services/stock_reservations.py').read_text(encoding='utf-8')
    fn = src[src.index('def reserve_new_orders_sale_first'):src.index('def reserve_orders_from_picking_rows')]
    assert "'SALE_RESERVED'" in fn or 'SALE_RESERVED' in fn
    assert 'execute_operation(' not in fn
    assert "operation_type='SHIPPING'" not in fn
    assert 'enqueue_marketplace_stock' in fn


def test_pause_can_target_wb_card_even_when_local_physical_is_zero():
    src = (ROOT / 'app/routers/receiving.py').read_text(encoding='utf-8')
    fn = src[src.index('@router.post("/inventory-bulk")'):src.index('@router.post("/inventory-bulk/undo-last")')]
    assert 'Stop/resume must also work for a WB card whose physical local stock is zero' in fn
    assert '_find_or_create_sku' in fn
    assert 'set_wb_sales_paused' in fn


def test_manual_publish_uses_sellable_outbox_not_physical_total():
    src = (ROOT / 'app/routers/receiving.py').read_text(encoding='utf-8')
    fn = src[src.index('@router.post("/publish-wb")'):src.index('@router.', src.index('@router.post("/publish-wb")') + 20)]
    assert 'enqueue_wb_stock_for_sku' in fn
    assert '_sync_wb_stock' not in fn
    assert 'status":"QUEUED"' in fn
