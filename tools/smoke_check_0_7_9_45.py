from pathlib import Path
root=Path(__file__).resolve().parents[1]
r=(root/'app/routers/receiving.py').read_text(encoding='utf-8')
j=(root/'app/static/app.js').read_text(encoding='utf-8')
assert 'FCBOX-' in r
assert 'fulfillcore-box:{client_id}:{warehouse_id}:{box}' in r
assert 'Container.client_id == client_id' in r
assert 'Container.warehouse_id == warehouse_id' in r
assert 'Название короба может быть любым непустым текстом' in j
assert 'на склад FulfillCore товар будет принят, на WB FBS не отправится' in r
print("OK: FulfillCore 0.7.9.45 arbitrary box format + local receiving enabled")
