from pathlib import Path
root = Path(__file__).resolve().parents[1]
main = (root/'app/main.py').read_text(encoding='utf-8')
mp = (root/'app/routers/marketplaces.py').read_text(encoding='utf-8')
js = (root/'app/static/app.js').read_text(encoding='utf-8')
checks = {
    'version': 'version="0.7.9.25"' in main,
    'historic_helper': 'def _historic_picking_row' in mp,
    'picking_allowed': 'orders already in PICKING are allowed in a new/consolidated sheet' in mp,
    'no_old_block': 'Откройте исходный лист для повторной печати' not in mp,
    'reuse_flag': 'historical_allocation_reused' in mp,
    'no_double_commit': 'stock_committed_again' in mp,
    'manual_picking_commit': 'o.local_status == "PICKING" or (o.local_status == "NEW" and move_to_picking)' in mp,
    'ui_message': 'без повторного списания' in js,
}
failed=[k for k,v in checks.items() if not v]
if failed:
    raise SystemExit('FAIL 0.7.9.25: '+', '.join(failed))
print('OK: FulfillCore 0.7.9.25 PICKING rebuild/reprint smoke-check passed')
