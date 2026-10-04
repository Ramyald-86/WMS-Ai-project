from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text(encoding='utf-8')
mp=(root/'app/routers/marketplaces.py').read_text(encoding='utf-8')
sc=(root/'app/services/stock_core_v2.py').read_text(encoding='utf-8')
sr=(root/'app/services/stock_reservations.py').read_text(encoding='utf-8')
pl=(root/'app/services/picking_lists.py').read_text(encoding='utf-8')
assert 'version="0.9.0"' in main
assert 'fc_stocks_v2' in sc
assert 'reserve_marketplace_unit' in sr
assert 'consume_marketplace_reservation' in sr
assert '300+ ЧАСОВ' in pl
assert 'marketplace_order_age_hours' in mp
print('OK: FulfillCore 0.9.0 Stock Core v2 + hybrid write-off + SLA 300h ready')
