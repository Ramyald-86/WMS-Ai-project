from pathlib import Path
import importlib.util

root = Path(__file__).resolve().parents[1]
main = (root / 'app/main.py').read_text(encoding='utf-8')
marketplaces = (root / 'app/routers/marketplaces.py').read_text(encoding='utf-8')
picking = (root / 'app/services/picking_lists.py').read_text(encoding='utf-8')

assert 'version="0.7.9.16"' in main
assert 'InventoryBalance.client_id == cred.client_id' in marketplaces
assert 'already_in_list = [o for o in orders if o.last_picking_list_id]' in marketplaces
assert 'rows = allocate_picking_rows(source_orders)' in marketplaces
assert 'reserved_rows + allocate_picking_rows' not in marketplaces
assert 'position,' not in picking[picking.index('def allocate_picking_rows'):picking.index('def _display_number')]

spec = importlib.util.spec_from_file_location('fc_picking_lists', root / 'app/services/picking_lists.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Same physical bucket duplicated in stock input must still have only one unit available.
stock = {
    'location_id': 'loc-1', 'location': 'A-01',
    'container_id': 'box-1', 'container': 'BOX-1',
    'quantity': 1, 'container_total_quantity': 1,
}
orders = [
    {'order_id': '1', 'sku_id': 'sku-1', 'article': 'A', 'size': 'M', 'barcode': '111', 'stock': [stock, dict(stock)]},
    {'order_id': '2', 'sku_id': 'sku-1', 'article': 'A', 'size': 'M', 'barcode': '111', 'stock': [stock, dict(stock)]},
]
rows = mod.allocate_picking_rows(orders)
ok = sum(float(r.get('required_quantity') or 0) for r in rows if r.get('status') == 'OK')
short = sum(float(r.get('required_quantity') or 0) for r in rows if r.get('status') == 'НЕ ХВАТАЕТ')
assert ok == 1.0, rows
assert short == 1.0, rows

print('OK: FulfillCore 0.7.9.16 strict live picking allocation smoke-check passed')
