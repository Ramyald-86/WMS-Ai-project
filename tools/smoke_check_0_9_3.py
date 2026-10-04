from pathlib import Path
root = Path(__file__).resolve().parents[1]
checks = {
    root / 'app/main.py': ['version="0.9.3"', 'installed agents send POST /result without'],
    root / 'app/routers/marketplaces.py': [
        'def _reserve_new_wb_orders_immediately(',
        '0.9.3_EXACT_SKU_IMMEDIATE_RESERVE',
        'FBS_RESERVED_ON_ARRIVAL',
        'WB_CANCELLED_BEFORE_PICKING',
        'immediate_fbs_reservation',
        '_repair_strict_wb_sku_links(db, orders)',
    ],
    root / 'app/services/stock_reservations.py': [
        'str(picking_list.id) if picking_list.id else None',
        'reserve_marketplace_unit(',
    ],
}
for path, markers in checks.items():
    text = path.read_text(encoding='utf-8')
    missing = [m for m in markers if m not in text]
    if missing:
        raise SystemExit(f'FAIL {path}: missing {missing}')
print('OK: FulfillCore 0.9.3 immediate exact-SKU FBS reservation ready')
