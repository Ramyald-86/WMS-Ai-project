#!/usr/bin/env python3
from __future__ import annotations
import argparse, ast, re
from pathlib import Path

MARKER = "0.9.4.6 AUTO LABEL WAIT FIX"

NEW_QUEUE = r'''function queueWbBackgroundLabel(orderId,orderNumber,localFallback=false){
  /* 0.9.4.6 AUTO LABEL WAIT FIX
     WB can accept the packing/SGTIN request before the order sticker is projected.
     The old auto path gave up after only ~0.8 s and showed a false print failure.
     Keep packing nonblocking, preserve FIFO paper order per workstation, and wait
     for the same order sticker for ~20 s before asking the operator to intervene. */
  state.wbBackgroundPrintChain=state.wbBackgroundPrintChain.catch(()=>{}).then(async()=>{
    let lastError=null;
    const delays=[0,700,1000,1500,2200,3000,4200,6000];
    for(let attempt=0;attempt<delays.length;attempt++){
      const delay=delays[attempt];
      if(delay)await new Promise(resolve=>setTimeout(resolve,delay));
      try{
        await printWbOrderLabel(orderId,{autoAfterChz:true});
        scheduleWbHotRefresh(250);
        toast(`Заказ WB №${orderNumber}: этикетка WB отправлена на печать.`);
        return;
      }catch(e){
        const code=e?.data?.detail?.code||'';
        if(code==='WB_ORDER_CANCELLED_CHZ_RELEASED'||code==='WB_ORDER_CANCELLED_CHZ_RELEASE_FAILED'){
          toast(e.message,true);scheduleWbHotRefresh(250);return;
        }
        lastError=e;
        const msg=String(e?.message||'').toLowerCase();
        const retryable=
          msg.includes('пока не подготовил этикетку')||
          msg.includes('ещё не подготовил этикетку')||
          msg.includes('еще не подготовил этикетку')||
          msg.includes('чз ещё не подтверждён')||
          msg.includes('чз еще не подтвержден')||
          msg.includes('temporar')||msg.includes('timeout')||
          msg.includes('503')||msg.includes('502')||msg.includes('504')||msg.includes('429');
        if(!retryable){
          toast(`Заказ WB №${orderNumber}: автоматическая печать остановлена: ${e.message}. Нажмите «Этикетка WB».`,true);
          return;
        }
      }
    }
    const e=lastError||new Error('WB не вернул этикетку');
    toast(`Заказ WB №${orderNumber}: заказ уже упакован, но WB не успел сформировать этикетку примерно за 20 секунд. Нажмите «Этикетка WB» через несколько секунд. Последний ответ: ${e.message}`,true);
    scheduleWbHotRefresh(250);
  });
}
'''

def patch_js(path: Path) -> None:
    s = path.read_text(encoding='utf-8')
    if MARKER in s:
        print('OK: app.js already 0.9.4.6')
        return
    if '0.9.4.5 order card nonblocking WB refresh' not in s:
        raise RuntimeError('0.9.4.5 frontend foundation missing')
    start = s.find('function queueWbBackgroundLabel(')
    end = s.find('async function processWbChzScan()', start)
    if start < 0 or end < 0 or end <= start:
        raise RuntimeError('background label function anchors not found')
    old = s[start:end]
    required = ['[0,260,520]', 'автоматическая печать не удалась', 'printWbOrderLabel']
    if not all(x in old for x in required):
        raise RuntimeError('refusing to patch unexpected queueWbBackgroundLabel implementation')
    s = s[:start] + NEW_QUEUE + s[end:]
    old_toast = "toast(r.wb_chz_local_fallback?`Заказ WB №${orderNumber}: заказ упакован. Печать идёт в фоне.`:`Заказ WB №${orderNumber}: ЧЗ принят, заказ упакован. Печать идёт в фоне.`);"
    new_toast = "toast(`Заказ WB №${orderNumber}: ЧЗ сохранён, заказ упакован. WB подтверждает ЧЗ и готовит этикетку; печать запустится автоматически.`);"
    if old_toast not in s:
        raise RuntimeError('packing toast anchor not found')
    s = s.replace(old_toast, new_toast, 1)
    path.write_text(s, encoding='utf-8')
    print('OK: app.js auto-label wait patched')

def patch_main(path: Path) -> None:
    s = path.read_text(encoding='utf-8')
    ast.parse(s)
    if 'version="0.9.4.6"' in s:
        print('OK: main.py already 0.9.4.6')
        return
    s, n = re.subn(r'version="0\.9\.4\.5"', 'version="0.9.4.6"', s, count=1)
    if n != 1:
        raise RuntimeError('FastAPI version 0.9.4.5 not found exactly once')
    ast.parse(s)
    path.write_text(s, encoding='utf-8')
    print('OK: health version -> 0.9.4.6')

def main() -> None:
    ap=argparse.ArgumentParser(); ap.add_argument('--root',required=True); a=ap.parse_args(); root=Path(a.root)
    patch_js(root/'app/static/app.js')
    patch_main(root/'app/main.py')

if __name__=='__main__': main()
