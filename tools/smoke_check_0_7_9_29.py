from pathlib import Path

root = Path(__file__).resolve().parents[1]
main = (root / 'app/main.py').read_text(encoding='utf-8')
marketplaces = (root / 'app/routers/marketplaces.py').read_text(encoding='utf-8')

checks = [
    ('version="0.7.9.29"' in main, 'version'),
    ('FC_WB_TRACE_SLOW' in marketplaces, 'slow WB stage trace'),
    ('FC_WB_TRACE_SUMMARY' in marketplaces, 'rolling WB latency summary'),
    ('trace_id' in marketplaces, 'per-scan trace id'),
    ('wb_set_sgtin_ms' in marketplaces, 'set_sgtin stage timing'),
    ('over_2s' in marketplaces and 'over_5s' in marketplaces, 'slow request counters'),
    ('p95_wb_ms' in marketplaces, 'p95 WB latency'),
]
failed = [name for ok, name in checks if not ok]
if failed:
    raise SystemExit('FAILED: ' + ', '.join(failed))
print('OK: FulfillCore 0.7.9.29 deep WB CHZ latency diagnostics smoke-check passed')
