from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text()
router=(root/'app/routers/marketplaces.py').read_text()
stock=(root/'app/services/stock_reservations.py').read_text()
assert 'version="0.7.9.12"' in main
for token in [
    'if not order.last_picking_list_id:\n        return None',
    'return {"changed": False, "reason": "no_picking_list"}',
    'PACKING_WITHOUT_PICKING_LIST',
    'if not order.last_picking_list_id:\n                order.picking_list_generated_at = None',
    'reservation_result = reserve_orders_from_picking_rows(',
    'order.last_picking_list_id = str(plist.id)',
]:
    assert token in (stock + router), token
# Status-only PICKING must not fabricate a timestamp anymore.
assert 'order.picking_list_generated_at = order.picking_list_generated_at or _now()' not in router
print('OK: FulfillCore 0.7.9.12 picking-list-only stock boundary smoke-check passed')
