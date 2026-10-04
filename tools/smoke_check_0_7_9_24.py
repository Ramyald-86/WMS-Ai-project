from pathlib import Path

root=Path(__file__).resolve().parents[1]
app=(root/'app/static/app.js').read_text(encoding='utf-8')
main=(root/'app/main.py').read_text(encoding='utf-8')
checks={
    'version':'version="0.7.9.24"' in main,
    'export_button':'Выгрузить остатки в Excel' in app,
    'export_function':'function exportInventoryExcel()' in app,
    'warehouse_column':"heads[6].textContent='На складе'" in app,
    'fbs_column':"heads[7].textContent='WB FBS'" in app,
    'warehouse_export':'Остаток на складе FulfillCore' in app,
    'fbs_export':'Остаток WB FBS' in app,
    'xls':'application/vnd.ms-excel' in app,
}
failed=[k for k,v in checks.items() if not v]
if failed:
    raise SystemExit('FAIL 0.7.9.24: '+', '.join(failed))
print('OK: FulfillCore 0.7.9.24 inventory Excel export + warehouse/FBS split smoke-check passed')
