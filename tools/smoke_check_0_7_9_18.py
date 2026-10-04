from pathlib import Path
root=Path(__file__).resolve().parents[1]
market=(root/'app/routers/marketplaces.py').read_text(encoding='utf-8')
stock=(root/'app/services/stock_reservations.py').read_text(encoding='utf-8')
layout=(root/'app/routers/layout.py').read_text(encoding='utf-8')
js=(root/'app/static/app.js').read_text(encoding='utf-8')
main=(root/'app/main.py').read_text(encoding='utf-8')
checks={
 'version':'0.7.9.18' in main,
 'unallocated_allowed':'absence of local stock is informational' in stock,
 'unallocated_header':'X-Picking-List-Unallocated' in market,
 'storage_fill':'storageFillHtml' in js and 'volume_cm3' in layout and '96000' in js,
 'packing_fill_removed':'wbCartonIndicatorHtml' not in js and 'addPackedItemToWbCarton' not in js,
 'client_scoped_occupancy':'client_id=${qClient}' in js,
}
failed=[k for k,v in checks.items() if not v]
if failed: raise SystemExit('FAIL: '+', '.join(failed))
print('OK: FulfillCore 0.7.9.18 unallocated picking + storage fill smoke-check passed')
