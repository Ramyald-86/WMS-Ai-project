#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',required=True); a=ap.parse_args(); root=Path(a.root)
    main_py=(root/'app/main.py').read_text(encoding='utf-8')
    market=(root/'app/routers/marketplaces.py').read_text(encoding='utf-8')
    js=(root/'app/static/app.js').read_text(encoding='utf-8')
    ast.parse(main_py); ast.parse(market)
    checks={
        'health_0_9_4_5':'version="0.9.4.5"' in main_py,
        'agent_117_download':'FulfillCore_Print_Agent_1.1.7_SAFE_SIMPLE.zip' in main_py,
        'download_public':'path.startswith("/downloads/print-agent/")' in main_py,
        'chz_status_aware':'0.9.4.5 WB CHZ PRINT STABILITY' in market and '_wb_supplier_status_without_db' in market,
        'chz_cooldown':'_CHZ_BG_NEXT_AT' in market and '_CHZ_BG_SLOTS' in market,
        'chz_supply_reconfirm':'_wb_add_orders_to_supply(client, sid, [order_number])' in market,
        'scan_passes_supply':'source="PACKING", supply_id=supply_id_for_wb' in market,
        'meta_retry_passes_supply':'source="META_RETRY", supply_id=supply_id' in market,
        'card_nonblocking':'0.9.4.5 order card nonblocking WB refresh' in js,
        'pending_ui':'повторная отправка выполняется автоматически' in js,
    }
    bad=[k for k,v in checks.items() if not v]
    for k,v in checks.items(): print(('OK ' if v else 'FAIL ')+k)
    if bad: raise SystemExit('SMOKE FAILED: '+', '.join(bad))
    print('SMOKE OK: FulfillCore 0.9.4.5')
if __name__=='__main__': main()
