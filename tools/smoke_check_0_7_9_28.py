from pathlib import Path

root = Path(__file__).resolve().parents[1]
main = (root / 'app/main.py').read_text(encoding='utf-8')
receiving = (root / 'app/routers/receiving.py').read_text(encoding='utf-8')
js = (root / 'app/static/app.js').read_text(encoding='utf-8')

checks = [
    ('version="0.7.9.28"' in main, 'version'),
    ('/structured-receipts/{receipt_id}/fbs-candidates' in receiving, 'structured FBS candidates route'),
    ('def cancel_structured_receipt(' in receiving, 'structured committed receipt cancel route'),
    ("status='CANCELLED'" in receiving, 'structured receipt cancellation status'),
    ('data-structured-fbs-publish' in js, 'selective structured FBS button'),
    ('Отправить выбранные на WB FBS' in js, 'structured FBS UI'),
    ('data-structured-cancel' in js, 'visible committed receipt delete/cancel button'),
    ('structured-box-modal' in js and 'data-structured-close-box' in js, 'box composition modal'),
    ('data-structured-box-code' in js, 'box composition action'),
]
failed = [name for ok, name in checks if not ok]
if failed:
    raise SystemExit('FAILED: ' + ', '.join(failed))
print('OK: FulfillCore 0.7.9.28 structured receiving delete + selective WB FBS + box composition smoke-check passed')
