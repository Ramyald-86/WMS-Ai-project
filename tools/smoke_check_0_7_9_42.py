from pathlib import Path
root=Path(__file__).resolve().parents[1]
r=(root/'app/routers/receiving.py').read_text(encoding='utf-8')
j=(root/'app/static/app.js').read_text(encoding='utf-8')
assert '@router.get("/structured-receipts/{receipt_id}/unloaded.xlsx")' in r
assert 'на склад FulfillCore товар будет принят, на WB FBS не отправится' in r
assert 'data-structured-unloaded' in j
assert 'Excel: неподгруженные' in j
print('OK: FulfillCore 0.7.9.42 unloaded Excel + WB-nonblocking local receiving enabled')
