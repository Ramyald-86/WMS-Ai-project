from pathlib import Path

root = Path(__file__).resolve().parents[1]
market = (root / 'app/routers/marketplaces.py').read_text(encoding='utf-8')
stock = (root / 'app/services/stock_reservations.py').read_text(encoding='utf-8')
js = (root / 'app/static/app.js').read_text(encoding='utf-8')
main = (root / 'app/main.py').read_text(encoding='utf-8')

checks = {
    'version': '0.7.9.17' in main,
    'package_metrics': 'def _wb_package_metrics' in market and '"package_metrics": _wb_package_metrics' in market,
    'standard_carton': 'WB_STD_CARTON={length_cm:60,width_cm:40,height_cm:40,volume_cm3:96000}' in js,
    'carton_persist': 'addPackedItemToWbCarton' in js and 'data-wb-carton-reset' in js,
    'selection_persist': 'wbSelectedOrderIds' in js and 'saveWbSelectedOrders' in js,
    'cancel_truth': 'packed_physically = bool(order.packed_at)' in stock,
    'cancel_repair': 'def repair_cancelled_picking_to_receiving' in stock and 'repair_cancelled_picking_to_receiving(db' in stock,
    'no_old_source_restore_on_cancel': 'WB_CANCELLED_TO_RECEIVING' in stock,
}
failed = [name for name, ok in checks.items() if not ok]
if failed:
    raise SystemExit('FAIL: ' + ', '.join(failed))
print('OK: FulfillCore 0.7.9.17 carton fill + stable selection + cancellation placement smoke-check passed')
