#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast
from pathlib import Path

ap=argparse.ArgumentParser(); ap.add_argument('--root',required=True); a=ap.parse_args(); r=Path(a.root)
main=(r/'app/main.py').read_text(encoding='utf-8')
mp=(r/'app/routers/marketplaces.py').read_text(encoding='utf-8')
js=(r/'app/static/app.js').read_text(encoding='utf-8')
ast.parse(main); ast.parse(mp)
checks={
 'version':'version="0.9.4.7"' in main,
 'marker':'0.9.4.7 DB TX AUTO LABEL STABILITY' in mp,
 'auth barrier':'0.9.4.7 AUTH TX BARRIER' in main,
 'background label helper':'def _schedule_wb_auto_label_background' in mp,
 'db-free sticker':'def _wb_sticker_bytes_without_db' in mp,
 'auto route barrier':'db.commit()\n\n        scheduled = _schedule_wb_auto_label_background' in mp,
 'complete direct chz':'outcome=complete_direct_ok' in mp,
 'packing untouched':'def wb_scan_chz' in mp and 'PACKED_QUEUED_FOR_WB' in mp,
 'reservation untouched':'WB_PACKED_CHZ_BACKGROUND' in mp,
 'frontend 0.9.4.6 kept':'0.9.4.6 AUTO LABEL WAIT FIX' in js,
 'truthful print toast':'фоновую очередь' in js,
}
for k,v in checks.items(): print(('OK ' if v else 'FAIL ')+k)
bad=[k for k,v in checks.items() if not v]
if bad: raise SystemExit('SMOKE FAILED: '+', '.join(bad))
print('SMOKE OK: 0.9.4.7 DB_TX_AUTO_LABEL_STABILITY')
