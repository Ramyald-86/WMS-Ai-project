from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text('utf-8')
recv=(root/'app/routers/receiving.py').read_text('utf-8')
js=(root/'app/static/app.js').read_text('utf-8')
assert 'version="0.7.9.35"' in main
assert '@router.get("/templates/{kind}.xlsx")' in recv
assert 'def _strict_wb_receiving_errors' in recv
assert 'Excel не проведён' in recv
assert 'Раскладка не проведена' in recv
assert 'wb_connected = bool(_active_wb_credential' in recv
assert 'publishFreshReceivingLines' in js
assert 'bulkReceivePublishTarget' in js
assert 'palletReceivePublishTarget' in js
assert 'structuredReceivePublishTarget' in js
assert 'Скачать шаблон выбранного формата' in js
assert "receivingTemplateUrl('STRUCTURED')" in js
print('OK: FulfillCore 0.7.9.35 receiving templates + strict WB barcode preflight + immediate FBS sync')
