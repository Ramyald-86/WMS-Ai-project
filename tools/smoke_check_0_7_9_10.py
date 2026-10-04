from pathlib import Path
root=Path(__file__).resolve().parents[1]
main=(root/'app/main.py').read_text()
mp=(root/'app/routers/marketplaces.py').read_text()
js=(root/'app/static/app.js').read_text()
agent=(root/'app/downloads/FulfillCore_Print_Agent_1.2.1_FAST_POLL_RELIABLE.zip')
checks={
 'version':'version="0.7.9.10"' in main,
 'fast auto helper':'def _fetch_wb_order_sticker_fast_auto' in mp,
 'bounded fast pauses':'for pause in (0.0, 0.10, 0.16, 0.22)' in mp,
 'fast auto queue':'fast_auto=bool(auto_after_chz)' in mp,
 'cancel release preserved':'_release_chz_after_confirmed_cancelled_label_failure' in mp,
 'manual conservative path':'return _fetch_wb_order_sticker_for_print(db, client, order)' in mp,
 'browser short retries':'for(const delay of [0,260,520])' in js,
 'agent 1.2.1 archive':agent.exists() and agent.stat().st_size>1000,
}
failed=[k for k,v in checks.items() if not v]
if failed:
    raise SystemExit('FAILED: '+', '.join(failed))
print('OK: FulfillCore 0.7.9.10 fast CHZ-to-label smoke-check passed')
