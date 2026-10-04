from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text()
js=(root/'app/static/app.js').read_text()
assert 'version="0.7.9.11"' in main
for token in [
    "CONTEXT_MASTER_CACHE_KEY='fc_context_master_v1'",
    'contextLoadPromise',
    "api('/masterdata/clients',{timeoutMs:6000})",
    "api(`/operations?client_id=${qClient}&warehouse_id=${qWh}&limit=300`",
    'loadWorkstationProfile(state.wbPrintStation,3000)',
    "setTimeout(()=>loadContextData({quiet:true}).catch(()=>{}),1200)",
]:
    assert token in js, token
print('OK: FulfillCore 0.7.9.11 context reliability/performance smoke-check passed')
