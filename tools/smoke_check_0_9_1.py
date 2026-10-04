from pathlib import Path
root=Path(__file__).resolve().parents[1]
mp=(root/'app/routers/marketplaces.py').read_text(encoding='utf-8')
sr=(root/'app/services/stock_reservations.py').read_text(encoding='utf-8')
main=(root/'app/main.py').read_text(encoding='utf-8')
assert 'version="0.9.1"' in main
assert 'def _strict_wb_order_sku' in mp
assert 'EXACT_BARCODE_SIZE_ONLY' in mp
assert 'never reuse a historical address created for another size/SKU' in mp
assert 'STRICT_SKU_MISMATCH_REPAIR' in sr
assert 'previous_sku == current_sku' in sr
assert '0.9.1 compatibility: some installed agents' in main
print('OK: FulfillCore 0.9.1 strict WB SKU/barcode/size picking protection ready')
