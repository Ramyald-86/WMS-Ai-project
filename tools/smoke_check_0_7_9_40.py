from pathlib import Path
root=Path(__file__).resolve().parents[1]
r=(root/'app/routers/receiving.py').read_text(encoding='utf-8')
j=(root/'app/static/app.js').read_text(encoding='utf-8')
assert '@router.post("/inventory-adjust-and-publish")' in r
assert 'WB не подтвердил остаток. FulfillCore НЕ изменён' in r
assert 'client.warehouses()' not in r
assert "/receiving/inventory-adjust-and-publish" in j
assert "локальный остаток не переносится" in j
print("OK: FulfillCore 0.7.9.42 atomic WB/local stock guard enabled")
