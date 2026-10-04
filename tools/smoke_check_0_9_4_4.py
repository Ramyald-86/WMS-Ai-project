#!/usr/bin/env python3
import argparse, ast
from pathlib import Path
ap=argparse.ArgumentParser(); ap.add_argument('--root',default='.'); a=ap.parse_args(); r=Path(a.root)
checks={
 r/'app/main.py':['version="0.9.4.4"'],
 r/'app/routers/marketplaces.py':['0.9.4.4 PACKING TRANSACTION FIX','_submit_wb_chz_background','LOCAL_FIRST_BACKGROUND_WB_0_9_4_4','transaction barrier: numeric_pairs','never carry marketplace_orders UPDATE locks'],
 r/'app/services/stock_reservations.py':['PACKING_RECOVER_FREE_STOCK_0_9_4_4','_recover_missing_reservation_from_free_stock','FIFO_REASSIGNED_TO_OLDER_ORDER'],
}
for p,marks in checks.items():
 s=p.read_text(encoding='utf-8'); ast.parse(s)
 for m in marks: assert m in s, f'{m} missing in {p}'
print('OK: FulfillCore 0.9.4.4 packing transaction/reservation markers present')
