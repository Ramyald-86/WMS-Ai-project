from pathlib import Path
root=Path(__file__).resolve().parents[1]
m=(root/"app/main.py").read_text(encoding="utf-8")
mp=(root/"app/routers/marketplaces.py").read_text(encoding="utf-8")
sr=(root/"app/services/stock_reservations.py").read_text(encoding="utf-8")
assert 'version="0.8.1"' in m
assert 'real_free_stock + receiving_free_stock' in mp
assert 'code != "ПРИЕМКА"' in mp
assert 'location_code != "ПРИЕМКА"' in sr
assert 'ПРИЕМКА больше не блокирует упаковку' in mp
print("OK: FulfillCore 0.8.1 real-stock-first + receiving fallback packing enabled")
