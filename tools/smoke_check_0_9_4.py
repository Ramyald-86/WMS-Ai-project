from pathlib import Path
root=Path(__file__).resolve().parents[1]
checks={
 root/'app/main.py':['version="0.9.4"'],
 root/'app/routers/receiving.py':['def _wb_publish_amounts_partial(','"errors": [row for row in partial["results"] if not row.get("ok")]','status="PUBLISHED"'],
 root/'app/routers/marketplaces.py':['def wb_shortage_xlsx(','refresh_live: bool = Query(default=False)','def _consume_picking_reservation_strict'],
 root/'app/services/stock_reservations.py':['if not order.last_picking_list_id:','reserve_marketplace_unit(','def consume_order_reservation'],
 root/'app/static/app.js':['adjustStockInOpenedCell(','installTopTableScrolls()','exportInventoryWbErrors()','Excel: дефицит по заказам'],
}
for path,markers in checks.items():
    text=path.read_text(encoding='utf-8')
    missing=[m for m in markers if m not in text]
    if missing: raise SystemExit(f'FAIL {path}: missing {missing}')
print('OK: FulfillCore 0.9.4 stable stock/reservation/UI markers present')
