#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',required=True); a=ap.parse_args(); root=Path(a.root)
    main_py=(root/'app/main.py').read_text(encoding='utf-8')
    js=(root/'app/static/app.js').read_text(encoding='utf-8')
    ast.parse(main_py)
    checks={
      'health_0_9_4_6':'version="0.9.4.6"' in main_py,
      'marker':'0.9.4.6 AUTO LABEL WAIT FIX' in js,
      'longer_retry':'const delays=[0,700,1000,1500,2200,3000,4200,6000]' in js,
      'old_short_retry_removed':'[0,260,520]' not in js,
      'truthful_packing_toast':'WB подтверждает ЧЗ и готовит этикетку' in js,
      'success_print_toast':'этикетка WB отправлена на печать' in js,
      'preserve_0945_card':'0.9.4.5 order card nonblocking WB refresh' in js,
    }
    bad=[k for k,v in checks.items() if not v]
    for k,v in checks.items(): print(('OK ' if v else 'FAIL ')+k)
    if bad: raise SystemExit('SMOKE FAILED: '+', '.join(bad))
    print('SMOKE OK: FulfillCore 0.9.4.6 AUTO LABEL WAIT FIX')
if __name__=='__main__': main()
