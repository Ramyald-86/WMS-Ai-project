/* FC_R145F39_MANUAL_LINE_CONTROLS */
(()=>{
'use strict';
const s=v=>String(v??'').trim();
const uid=()=>crypto?.randomUUID?.()||('fc39-'+Date.now()+'-'+Math.random().toString(36).slice(2));
function worker(){try{return s(typeof fc30WorkerName==='function'?fc30WorkerName():document.getElementById('receiveBy')?.value);}catch(_){return s(document.getElementById('receiveBy')?.value);}}
function msg(x){if(!x)return 'Ошибка';if(typeof x==='string')return x;if(x.message)return x.message;if(x.detail)return msg(x.detail);try{return JSON.stringify(x);}catch(_){return String(x);}}
async function direct(url,body,timeout=15000){const c=new AbortController(),t=setTimeout(()=>c.abort(),timeout);try{const r=await fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',signal:c.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const raw=await r.text();let d;try{d=JSON.parse(raw);}catch(_){d={detail:raw};}if(!r.ok)throw Error(msg(d));return d;}catch(e){if(e.name==='AbortError')throw Error('Сервер не подтвердил изменение за отведённое время. Повторите действие.');throw e;}finally{clearTimeout(t);}}
async function flush(){if(window.FC32Receiving){await window.FC32Receiving.flush();const n=await window.FC32Receiving.pending();if(n)throw Error(`Ещё ${n} скан(ов) ожидают сервера. Подождите несколько секунд.`);}}
function currentRow(id,barcode){const rows=fc27ServerDraft?.rows||[];return rows.find(x=>String(x.id)===String(id))||rows.find(x=>s(x.barcode)===s(barcode)&&!x.posted_at)||null;}
async function adopt(snap){if(!snap)return;try{fc27ServerDraft=snap;fc27ServerDraftContext=typeof fc27DraftContext==='function'?fc27DraftContext():fc27ServerDraftContext;}catch(_){}try{if(typeof fc27CacheServerDraft==='function')await fc27CacheServerDraft(snap);}catch(_){}try{renderManualReceivingDraft();}catch(_){}}
function selector(el,quantity){const tr=el.closest('tr[data-fc30-line-id]'),id=tr?.dataset.fc30LineId||el.dataset.manualDraftQty||el.dataset.manualDraftRemove||'',barcode=tr?.dataset.fc30Barcode||'';const row=currentRow(id,barcode);return {client_id:state.clientId,warehouse_id:state.warehouseId,worker:worker(),request_id:uid(),line_id:id||null,barcode:row?.barcode||barcode||null,location_code:row?.location_code||null,container_code:row?.container_code??null,...(quantity==null?{}:{quantity})};}
function hideStockError(){const n=document.getElementById('r70StockSyncStatus');if(n&&state?.activePage==='receive'){n.textContent='';n.hidden=true;n.className='';}}

document.addEventListener('change',async e=>{
 const input=e.target?.closest?.('[data-manual-draft-qty]');if(!input)return;
 e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();
 const value=Number(input.value);if(!Number.isSafeInteger(value)||value<1){try{toast('Количество должно быть целым и больше нуля',true);}catch(_){};try{renderManualReceivingDraft();}catch(_){};return;}
 const sel=selector(input,value);input.disabled=true;
 try{await flush();const d=fc27ServerDraft||await fc27EnsureServerDraft();const snap=await direct(`/r145f39/manual-drafts/${encodeURIComponent(d.id)}/adjust-line`,sel);await adopt(snap);try{toast(`Количество сохранено: ${value} шт.`);}catch(_){}}
 catch(err){try{toast(msg(err),true);}catch(_){};try{renderManualReceivingDraft();}catch(_){}}
 finally{input.disabled=false;hideStockError();}
},true);

document.addEventListener('click',async e=>{
 const b=e.target?.closest?.('[data-manual-draft-remove]');if(!b)return;
 e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();
 if(!confirm('Убрать эту позицию из текущей приёмки?'))return;
 const sel=selector(b,null);b.disabled=true;
 try{await flush();const d=fc27ServerDraft||await fc27EnsureServerDraft();const snap=await direct(`/r145f39/manual-drafts/${encodeURIComponent(d.id)}/remove-line`,sel);await adopt(snap);try{toast('Позиция убрана из приёмки.');}catch(_){}}
 catch(err){try{toast(msg(err),true);}catch(_){};try{renderManualReceivingDraft();}catch(_){}}
 finally{b.disabled=false;hideStockError();}
},true);

// The receiving screen must never be covered by a stale global stock-refresh error.
document.addEventListener('click',hideStockError,true);
document.addEventListener('focusin',hideStockError,true);
setTimeout(hideStockError,0);setTimeout(hideStockError,500);
window.FC39ReceivingControls={version:39,hideStockError};
})();
