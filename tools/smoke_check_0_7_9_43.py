from pathlib import Path
root=Path(__file__).resolve().parents[1]
j=(root/'app/static/app.js').read_text(encoding='utf-8')
r=(root/'app/routers/receiving.py').read_text(encoding='utf-8')
assert 'Excel: неподгруженные' in j
assert 'Показать список WB FBS' in j
assert 'Скрыть список' in j
assert '/unloaded.xlsx' in j
assert '@router.get("/structured-receipts/{receipt_id}/unloaded.xlsx")' in r
assert 'ws_wb = wb.create_sheet("Не на WB FBS")' in r
assert 'ws_sum = wb.create_sheet("Сводка")' in r
print("OK: FulfillCore 0.7.9.43 unloaded Excel + collapsible FBS list enabled")
