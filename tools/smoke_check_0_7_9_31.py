from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text(encoding='utf-8')
js=(root/'app/static/app.js').read_text(encoding='utf-8')
mp=(root/'app/routers/marketplaces.py').read_text(encoding='utf-8')
help_html=(root/'app/static/print-agent.html').read_text(encoding='utf-8')
assert 'version="0.7.9.34"' in main
assert 'FulfillCore_Print_Agent_1.2.2_ONE_CLICK_INSTALL.zip' in main
assert js.startswith('const selectedInventoryBarcodes=new Set();\nconst $ =')
assert '☑ Выбрать все' in js and 'Обновить выбранные на WB FBS' in js
assert 'Удалить выбранные со склада' in js and 'Удалить все остатки' in js
retry=mp.split('def _retry_pending_wb_chz',1)[1].split('def _wb_reshipment_order_ids',1)[0]
assert '_timing_add_ms(timing, "wb_total_ms", _t)' not in retry
assert 'INSTALL_PRINT_AGENT.bat' in help_html
assert (root/'app/downloads/FulfillCore_Print_Agent_1.2.2_ONE_CLICK_INSTALL.zip').exists()
print('OK: FulfillCore 0.7.9.34 inventory controls + frontend repair + CHZ hotfix + Print Agent 1.2.2 passed')
