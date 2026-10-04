from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text()
recv=(root/'app/routers/receiving.py').read_text()
js=(root/'app/static/app.js').read_text()
checks={
 'version':'version="0.7.9.9"' in main,
 'box endpoint':'/manual/storage/box' in recv,
 'generated qr':'FCBOX-' in recv and 'box_mode == "NAME"' in recv,
 'optional hierarchy':'section: str | None' in recv and 'aisle: str | None' in recv and 'pallet: str | None' in recv,
 'box label queue':'/label/queue' in recv and 'BOX_QR_LABEL' in recv,
 'ui structure':'manualReceivingStructurePanel' in js,
 'legacy mode':'manualReceiveLegacyMode' in js,
 'continuous box context':'manualReceivingStorageContext' in js and 'Все следующие товары' in js,
 'draft container':'container_code:containerCode' in js,
}
failed=[k for k,v in checks.items() if not v]
if failed:
    raise SystemExit('FAILED: '+', '.join(failed))
print('OK: FulfillCore 0.7.9.9 flexible manual receiving smoke-check passed')
