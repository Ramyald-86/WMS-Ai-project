from pathlib import Path

root = Path(__file__).resolve().parents[1]
main = (root / 'app' / 'main.py').read_text(encoding='utf-8')
market = (root / 'app' / 'routers' / 'marketplaces.py').read_text(encoding='utf-8')
stock = (root / 'app' / 'services' / 'stock_reservations.py').read_text(encoding='utf-8')

assert 'version="0.7.9.13"' in main
assert 'WB_PICKING_LIST_STOCK_COMMIT' in stock
assert "status='CONSUMED'" in stock
assert 'PACKING_RECONCILED_ALREADY_OUT_OF_SOURCE' in stock
assert 'CANCEL_RECONCILED_ALREADY_OUT_OF_SOURCE' in stock
assert 'WB_PICKING_LIST_RELEASE_TO_SOURCE' in stock
assert 'verify_after_submit: bool = True' in market
assert '_bind_chz_to_wb_verified(db, client, order, chz, verify_after_submit=False)' in market
assert 'slow metadata GET from the scanner' in market
print('OK: FulfillCore 0.7.9.13 picking-list write-off + fast CHZ submit smoke-check passed')
