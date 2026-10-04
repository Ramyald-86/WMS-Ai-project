#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('--root', default='.')
a = ap.parse_args()
r = Path(a.root)
main = (r/'app/main.py').read_text(encoding='utf-8')
mp = (r/'app/routers/marketplaces.py').read_text(encoding='utf-8')
ast.parse(main); ast.parse(mp)
checks = {
    'health version': 'version="0.9.4.2"' in main,
    'stability marker': '0.9.4.2 DATABASE STABILITY' in mp,
    'pre-reservation recovery': 'pre-reservation DB transaction was invalid; rolling back' in mp,
    'rollback after failure': 'immediate WB reservation failed safely' in mp,
    'existing reserve logic kept': 'def _reserve_new_wb_orders_immediately' in mp,
    'distributed lock kept': 'wb-arrival-reserve:' in mp,
}
missing=[k for k,v in checks.items() if not v]
if missing: raise SystemExit('STOP: missing markers: '+', '.join(missing))
print('OK: FulfillCore 0.9.4.2 DATABASE_STABILITY markers present')
