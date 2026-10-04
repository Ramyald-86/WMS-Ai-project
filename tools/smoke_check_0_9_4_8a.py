#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast
from pathlib import Path

EXPECTED = {
    "wb_scan_product": ("post", "/wb/packing/scan-product"),
    "wb_scan_chz": ("post", "/wb/packing/scan-chz"),
    "wb_packing_progress": ("get", "/wb/packing/progress"),
    "wb_queue_order_label": ("post", "/wb/orders/{order_id}/label/queue"),
}

def routes(path: Path):
    src=path.read_text(encoding='utf-8'); tree=ast.parse(src); out={}
    for n in ast.walk(tree):
        if not isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)): continue
        vals=[]
        for d in n.decorator_list:
            if isinstance(d,ast.Call) and isinstance(d.func,ast.Attribute) and isinstance(d.func.value,ast.Name) and d.func.value.id=='router' and d.args and isinstance(d.args[0],ast.Constant):
                vals.append((d.func.attr,d.args[0].value))
        out[n.name]=vals
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--root',required=True); a=ap.parse_args(); root=Path(a.root)
    mp=root/'app/routers/marketplaces.py'; mainp=root/'app/main.py'
    r=routes(mp)
    for fn, expected in EXPECTED.items():
        if expected not in r.get(fn,[]): raise SystemExit(f'FAIL route {fn}: expected {expected}, got {r.get(fn)}')
    ms=mainp.read_text(encoding='utf-8')
    if 'version="0.9.4.8a"' not in ms: raise SystemExit('FAIL version')
    print('PASS: 0.9.4.8a routes + version')

if __name__=='__main__': main()
