from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text(encoding='utf-8')
js=(root/'app/static/app.js').read_text(encoding='utf-8')
recv=(root/'app/routers/receiving.py').read_text(encoding='utf-8')
checks=[
 ('version="0.7.9.30"' in main,'version'),
 ('/inventory-bulk' in recv,'bulk endpoint'),
 ('/inventory-bulk/undo-last' in recv,'undo endpoint'),
 ('fc_inventory_bulk_actions' in recv,'bulk ledger'),
 ('data-inventory-bulk-publish' in js,'bulk publish UI'),
 ('data-inventory-bulk-delete' in js,'bulk delete UI'),
 ('data-inventory-delete-all' in js,'delete all UI'),
 ('data-inventory-undo-last' in js,'undo UI'),
 ('УДАЛИТЬ ВСЕ' in js and 'УДАЛИТЬ ВСЕ' in recv,'delete all guard'),
]
failed=[name for ok,name in checks if not ok]
if failed: raise SystemExit('FAILED: '+', '.join(failed))
print('OK: FulfillCore 0.7.9.30 bulk inventory select/delete/WB/undo smoke-check passed')
