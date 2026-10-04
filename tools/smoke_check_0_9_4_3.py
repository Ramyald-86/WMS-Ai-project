#!/usr/bin/env python3
from pathlib import Path
import argparse, ast

ap=argparse.ArgumentParser(); ap.add_argument('--root',default='.'); a=ap.parse_args(); r=Path(a.root)
files=[r/'app/main.py',r/'app/routers/marketplaces.py',r/'app/services/stock_core_v2.py',r/'app/services/stock_reservations.py',r/'app/services/runtime_reliability.py']
for p in files:
    ast.parse(p.read_text(encoding='utf-8'))
checks={
    r/'app/main.py':['version="0.9.4.3"'],
    r/'app/routers/marketplaces.py':['0.9.4.2 DATABASE STABILITY'],
    r/'app/services/stock_core_v2.py':['0.9.4.3 PACKING FIFO STABILITY','PACKING_WRITE_OFF'],
    r/'app/services/stock_reservations.py':['0.9.4.3 PACKING FIFO STABILITY','FIFO_REASSIGNED_TO_OLDER_ORDER'],
    r/'app/services/runtime_reliability.py':['0.9.4.3 PACKING LEASE NONBLOCKING'],
    r/'app/static/app.js':['0.9.4.3 scan product bounded timeout'],
}
for p, marks in checks.items():
    s=p.read_text(encoding='utf-8')
    for m in marks:
        assert m in s, f'{m} missing in {p}'
print('OK: FulfillCore 0.9.4.3 packing/FIFO/database stability markers present')
