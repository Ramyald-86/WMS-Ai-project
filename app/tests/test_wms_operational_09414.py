from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]


def section(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end, text.index(start))]


def test_product_scan_is_local_db_only_and_never_stock_gated():
    src = (ROOT / 'app/routers/marketplaces.py').read_text(encoding='utf-8')
    scan = section(src, '@router.post("/wb/packing/scan-product")', '@router.post("/wb/packing/skip-order")')
    assert 'MarketplaceOrder.local_status.in_(["NEW", "PICKING"])' in scan
    assert '_wb(db' not in scan
    assert 'client.' not in scan
    assert '_stock_for_sku' not in scan
    assert '_product_card_for_order' not in scan
    assert 'inventory_gate": False' in scan
    assert 'PACKING_WITHOUT_STOCK_RESERVATION_ALLOWED' in scan
    assert scan.index('db.commit()') < scan.index('_prepare_wb_order_for_fast_pack_background')


def test_browser_product_scan_has_no_abort_timeout():
    js = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
    scan = section(js, 'async function processWbProductScan()', 'function scheduleWbHotRefresh')
    assert 'timeoutMs:0' in scan
    assert '/marketplaces/wb/packing/scan-product' in scan


def test_existing_order_can_finish_when_stock_is_zero():
    src = (ROOT / 'app/services/stock_reservations.py').read_text(encoding='utf-8')
    fn = src[src.index('def finalize_order_stock_for_packing'):src.index('def consume_order_reservation')]
    assert 'stock_shortage' in fn
    assert 'NO_RESERVATION_FALLBACK' in fn
    assert 'SALE_RESERVED_FALLBACK' in fn
    router = (ROOT / 'app/routers/marketplaces.py').read_text(encoding='utf-8')
    assert 'require=False' in router
    assert 'order.local_status = "PACKED"' in router


def test_wb_pause_is_durable_and_outbox_forces_zero():
    core = (ROOT / 'app/services/stock_core_v2.py').read_text(encoding='utf-8')
    assert 'def wb_sales_paused' in core
    assert 'def set_wb_sales_paused' in core
    assert 'desired = max(0, int(canonical_free))' in core
    assert 'desired = 0 if (provider == "WB" and wb_sales_paused(' in core
    migration = (ROOT / 'alembic/versions/0011_wb_sales_pause_fast_scan.py').read_text(encoding='utf-8')
    assert 'CREATE TABLE IF NOT EXISTS fc_wb_sales_pauses' in migration
    assert 'ix_marketplace_orders_barcodes_gin' in migration


def test_delete_free_keeps_existing_order_reserves():
    src = (ROOT / 'app/routers/receiving.py').read_text(encoding='utf-8')
    fn = section(src, '@router.post("/inventory-bulk")', '@router.post("/inventory-bulk/undo-last")')
    assert 'DELETE_FREE_AND_PAUSE' in fn
    assert 'active_reserved_totals' in fn
    assert 'sale_reserved_total' in fn
    assert 'physical - min(physical, required)' in fn
    assert 'DELETE_FREE_KEEP_EXISTING_ORDERS' in fn
    assert 'set_wb_sales_paused' in fn


def test_browser_print_completion_moves_order_to_packed():
    src = (ROOT / 'app/routers/marketplaces.py').read_text(encoding='utf-8')
    fn = src[src.index('@router.get("/wb/packing/browser-label")'):]
    assert 'stage not in {"BROWSER_READY", "DONE"}' in fn
    assert 'WB_PACKED_BROWSER_LABEL_DELIVERED' in fn
    assert 'order.local_status = "PACKED"' in fn


def test_js_exposes_safe_wb_sales_controls():
    js = (ROOT / 'app/static/app.js').read_text(encoding='utf-8')
    assert 'Остановить продажи WB' in js
    assert 'Возобновить продажи WB' in js
    assert 'Удалить выбранные из FBS' in js
    assert 'DELETE_FBS_KEEP_RESERVES' in js
    assert 'changeFbsInventory' in js
    assert "runInventoryBulkAction('PAUSE_WB')" in js
    assert "runInventoryBulkAction('RESUME_WB')" in js
    ast.parse((ROOT / 'app/routers/receiving.py').read_text(encoding='utf-8'))
