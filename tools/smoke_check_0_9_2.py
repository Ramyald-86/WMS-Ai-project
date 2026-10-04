from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text(encoding='utf-8')
mp=(root/'app/routers/marketplaces.py').read_text(encoding='utf-8')
js=(root/'app/static/app.js').read_text(encoding='utf-8')
assert 'version="0.9.2"' in main
assert 'EXACT_BARCODE_SIZE_ONLY_BATCH' in mp
assert 'Batch-repair stale WB SKU links without N+1 queries' in mp
assert "limit:'5000'" in js
assert 'show physical FulfillCore stock immediately' in js
assert 'installed agents send POST /result without' in main
print('OK: FulfillCore 0.9.2 performance + strict SKU + fast inventory ready')
