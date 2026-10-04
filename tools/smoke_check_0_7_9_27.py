from pathlib import Path

root = Path(__file__).resolve().parents[1]
main = (root / "app" / "main.py").read_text(encoding="utf-8")
js = (root / "app" / "static" / "app.js").read_text(encoding="utf-8")
recv = (root / "app" / "routers" / "receiving.py").read_text(encoding="utf-8")
checks = [
    ('version="0.7.9.27"' in main, 'version'),
    ('BATCH:{batch_id}' in recv, 'batch tag'),
    ('/bulk/{batch_id}/cancel' in recv, 'bulk cancel route'),
    ('data-pending-wb-send-selected' in js, 'selected WB send'),
    ('На складе' in js and 'WB FBS' in js, 'warehouse/FBS display'),
    ('data-cancel-bulk-batch' in js, 'bulk cancel button'),
]
failed=[name for ok,name in checks if not ok]
if failed:
    raise SystemExit('FAIL: ' + ', '.join(failed))
print('OK: FulfillCore 0.7.9.27 bulk receiving cancel + selective WB FBS smoke-check passed')
