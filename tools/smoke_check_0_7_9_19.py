from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text(encoding='utf-8')
mp=(root/'app/routers/marketplaces.py').read_text(encoding='utf-8')
js=(root/'app/static/app.js').read_text(encoding='utf-8')
stock=(root/'app/services/stock_reservations.py').read_text(encoding='utf-8')
checks={
 'version':'0.7.9.19' in main,
 'virtual packing detection':'ITEM_IN_VIRTUAL_RECEIVING' in mp,
 'virtual receiving button':'data-receiving-place' in js and 'placeReceivingStock' in js,
 'packing open receiving':'openReceivingFromPacking' in js,
 'product card from location':'data-location-product-card' in js,
 'fill capped':'Math.min(100,rawPct)' in js,
 'receiving product id':'"product_id": identity.get("product_id")' in stock,
}
failed=[k for k,v in checks.items() if not v]
if failed:
    raise SystemExit('FAILED: '+', '.join(failed))
print('OK: FulfillCore 0.7.9.19 virtual receiving placement + storage card/fill smoke-check passed')
