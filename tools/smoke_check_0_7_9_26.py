from pathlib import Path

root = Path(__file__).resolve().parents[1]
market = (root / 'app/routers/marketplaces.py').read_text(encoding='utf-8')
main = (root / 'app/main.py').read_text(encoding='utf-8')
checks = {
    'version': 'version="0.7.9.26"' in main,
    'timing_event': 'CHZ_TIMING' in market,
    'timing_log': 'FC_CHZ_TIMING' in market,
    'wb_set_metric': 'wb_set_sgtin_ms' in market,
    'wb_status_metric': 'wb_status_ms' in market,
    'wb_meta_metric': 'wb_meta_ms' in market,
    'response_metric': '"chz_timing": chz_timing' in market,
    'no_flow_change': 'verify_after_submit=False, diagnostics=timing' in market,
}
failed = [k for k, ok in checks.items() if not ok]
if failed:
    raise SystemExit('FAIL: ' + ', '.join(failed))
print('OK: FulfillCore 0.7.9.26 CHZ timing diagnostics smoke-check passed')
