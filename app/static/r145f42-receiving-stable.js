/* FC_R145F42_RECEIVING_NONBLOCK_MAIN_DEFAULT */
/* FC_R145F45_INSTANT_DRAFT_HOT_SYNC */
/* FC_R145F46_RECEIVING_RESCUE_ZERO_WAIT */
(()=>{
'use strict';
let busy=false,normalizing=false,lastNormalize='',normalizeTimer=0,stockTouched=false;
const q=(s,r=document)=>r.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function worker(){try{return String(typeof fc30WorkerName==='function'?fc30WorkerName():(q('#receiveBy')?.value||'')).trim().replace(/\s+/g,' ');}catch(_){return String(q('#receiveBy')?.value||'').trim();}}
function contextKey(){return [String(state?.clientId||''),String(state?.warehouseId||''),worker().toLocaleLowerCase('ru-RU')].join('|');}
function msgOf(x){if(!x)return'Неизвестная ошибка';if(typeof x==='string')return x;if(x.message)return String(x.message);if(x.detail)return msgOf(x.detail);try{return JSON.stringify(x);}catch(_){return String(x);}}
function status(text,bad=false){
 const panel=q('#manualReceivingDraftPanel');if(!panel)return;
 let el=q('#fc42Status',panel);if(!el){el=document.createElement('div');el.id='fc42Status';el.className='result-box';panel.insertAdjacentElement('afterbegin',el);}
 const html=`<b class="${bad?'bad':'good'}">${bad?'Приёмка не завершена':'Приёмка'}</b><span>${esc(text)}</span>`;
 if(el.innerHTML!==html)el.innerHTML=html;
}
async function postJson(url,body,timeout=90000){
 const ctl=new AbortController(),tm=setTimeout(()=>ctl.abort(),timeout);
 try{
  const r=await fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',signal:ctl.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const raw=await r.text();let d;try{d=JSON.parse(raw);}catch(_){d={detail:raw};}
  if(!r.ok)throw new Error(msgOf(d));return d;
 }catch(e){if(e.name==='AbortError')throw new Error('Сервер слишком долго отвечает. Данные приёмки не удалены и не проведены повторно.');throw e;}finally{clearTimeout(tm);}
}
function setMainDefault(snap,force=false){
 const sel=q('#receiveStockClass');if(!sel)return;
 const units=Number(snap?.units||0);
 if(force||(!stockTouched&&units===0)){
  if(sel.value!=='MAIN'){sel.value='MAIN';try{sel.dispatchEvent(new Event('change',{bubbles:true}));}catch(_){}}
 }
}
function retitle(){
 const b=q('#commitManualReceivingDraft');
 if(b){b.disabled=Boolean(busy);if(!busy&&b.textContent!=='Завершить приёмку')b.textContent='Завершить приёмку';b.dataset.fc42='1';}
 const c=q('#clearManualReceivingDraft');
 if(c){c.disabled=Boolean(busy);if(!busy&&c.textContent!=='Начать новую приёмку')c.textContent='Начать новую приёмку';c.dataset.fc42='1';}
}
async function adoptSnapshot(snap){
 if(!snap)return null;
 try{fc27ServerDraft=snap;fc27ServerDraftContext=typeof fc27DraftContext==='function'?fc27DraftContext():fc27ServerDraftContext;fc27ServerDraftFlight=null;}catch(_){}
 try{if(typeof fc27CacheServerDraft==='function')await fc27CacheServerDraft(snap);}catch(_){}
 try{renderManualReceivingDraft();}catch(_){}
 setMainDefault(snap,false);retitle();return snap;
}
async function flushLocal(){
 if(window.FC32Receiving){await window.FC32Receiving.flush();const n=await window.FC32Receiving.pending();if(n)throw new Error(`Ещё ${n} скан(ов) текущей приёмки сохранены локально и ожидают сервера. Дождитесь надписи «всё сохранено на сервере».`);}
 if(typeof manualReceivingScanBusy!=='undefined'&&manualReceivingScanBusy)throw new Error('Дождитесь сохранения текущего скана.');
 if(typeof manualReceivingScanQueue!=='undefined'&&manualReceivingScanQueue.length)throw new Error(`Ещё ${manualReceivingScanQueue.length} скан(ов) в очереди обработки.`);
}
async function normalize(force=false,{skipFlush=false}={}){const w=worker();if(!w||!state?.clientId||!state?.warehouseId||normalizing)return null;const key=contextKey();if(!force&&lastNormalize===key)return fc27ServerDraft||null;normalizing=true;try{if(window.FC32Receiving?.hydrate)await window.FC32Receiving.hydrate();let snap=null;try{if(window.FC32Receiving?.state)snap=await window.FC32Receiving.state(true);}catch(e){if(fc27ServerDraft&&(fc27ServerDraft.rows||[]).length){lastNormalize=key;return fc27ServerDraft;}throw e;}lastNormalize=key;return snap||fc27ServerDraft||null;}finally{normalizing=false;}}
function scheduleNormalize(force=false,delay=160){clearTimeout(normalizeTimer);normalizeTimer=setTimeout(()=>normalize(force).catch(e=>status(msgOf(e),true)),delay);}
function resetPlace(){
 try{manualReceivingStorageContext=null;}catch(_){}
 for(const id of ['manualReceiveBox','receiveLocation','receiveContainer']){const e=q('#'+id);if(e)e.value='';}
 try{renderManualReceivingStorageContext();}catch(_){}try{q('#receiveSku')?.focus();}catch(_){}
}
async function resetCurrent(){
 if(busy)return;const w=worker();if(!w){status('Укажите имя приёмщика.',true);return;}
 busy=true;retitle();status('Проверяем текущую приёмку. Интерфейс не завис — операция выполняется…');
 try{
  if(typeof manualReceivingScanBusy!=='undefined'&&manualReceivingScanBusy)throw new Error('Дождитесь завершения текущего скана.');
  if(typeof manualReceivingScanQueue!=='undefined'&&manualReceivingScanQueue.length)throw new Error('Дождитесь обработки текущей очереди сканера.');
  // For cancellation/new receipt we intentionally do NOT upload a stale queue first.
  // The operator is explicitly discarding this draft; current-receipt queue is cleared only after confirmation.
  const snap=await normalize(true,{skipFlush:true})||fc27ServerDraft||await fc27EnsureServerDraft();
  const units=(snap?.rows||[]).filter(r=>!r.posted_at&&Number(r.quantity||0)>0).reduce((n,r)=>n+Number(r.quantity||0),0);
  if(units&&!confirm(`Начать новую приёмку?\n\nТекущий черновик ${units} шт. будет закрыт БЕЗ добавления на остатки. История и остатки не изменятся.`)){return;}
  status('Закрываем текущий черновик без движения остатков…');
  try{if(window.FC32Receiving?.clear)await window.FC32Receiving.clear();}catch(_){}
  const data=await postJson('/r145f37/manual-drafts/reset-worker',{client_id:state.clientId,warehouse_id:state.warehouseId,worker:w,reference:null},30000);
  await adoptSnapshot(data.next_receipt);if(q('#receiveReference'))q('#receiveReference').value='';resetPlace();stockTouched=false;setMainDefault(data.next_receipt,true);
  status('Открыта новая пустая приёмка на Основной склад. Старый черновик больше не попадёт в неё.');lastNormalize=contextKey();
 }catch(e){status(msgOf(e),true);try{toast(msgOf(e),true);}catch(_){} }
 finally{busy=false;retitle();}
}
async function complete(){
 if(busy)return;const w=worker();if(!w){status('Укажите имя приёмщика.',true);return;}
 busy=true;retitle();status('Синхронизируем последние сканы с сервером. Интерфейс не завис — дождитесь проверки…');
 try{
  await flushLocal();
  status('Все локальные сканы подтверждены. Фиксируем точный серверный снимок…');
  // One direct normalize only; r145f38 used flush -> normalize -> flush again and could look frozen.
  const snap=await normalize(true,{skipFlush:true})||fc27ServerDraft||await fc27EnsureServerDraft();
  const rows=(snap?.rows||[]).filter(r=>!r.posted_at&&Number(r.quantity||0)>0);
  const units=rows.reduce((n,r)=>n+Number(r.quantity||0),0);
  if(!units)throw new Error('В текущей приёмке нет товара.');
  if(!confirm(`Завершить приёмку ${w}: ${units} шт. / ${rows.length} поз.?\n\nНа остатки и в историю попадёт ровно это количество.`)){return;}
  const b=q('#commitManualReceivingDraft');if(b)b.textContent='Проводим '+units+' шт.…';
  status(`Зафиксировано: ${units} шт. / ${rows.length} поз. Проводим одной серверной операцией…`);
  const data=await postJson(`/r145f37/manual-drafts/${encodeURIComponent(snap.id)}/commit-exact`,{
    client_id:state.clientId,warehouse_id:state.warehouseId,worker:w,
    reference:q('#receiveReference')?.value?.trim()||null,performed_by:w,
    stock_class:q('#receiveStockClass')?.value||'MAIN'
  },120000);
  await adoptSnapshot(data.next_receipt);if(q('#receiveReference'))q('#receiveReference').value='';resetPlace();stockTouched=false;setMainDefault(data.next_receipt,true);
  if(data.old_draft_closed)status('Старый технический черновик закрыт без повторного начисления. Новая приёмка пустая, по умолчанию — Основной склад.');
  else{const got=Number(data.posted_units||0),want=Number(data.draft_units||0);if(got!==want)throw new Error(`Контроль не пройден: черновик ${want}, проведено ${got}.`);status(`Готово: ${want} шт. из черновика = ${got} шт. в остатках/истории. Открыта новая пустая приёмка на Основной склад.`);try{toast(`Приёмка завершена: ${got} шт.`,false);}catch(_){}}
  try{window.loadR62ManualReceipts?.().catch(()=>{});}catch(_){}lastNormalize=contextKey();
 }catch(e){status(msgOf(e),true);try{toast(msgOf(e),true);}catch(_){} }
 finally{busy=false;retitle();}
}
document.addEventListener('click',e=>{
 const b=e.target?.closest?.('#commitManualReceivingDraft');if(b){e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();complete();return;}
 const c=e.target?.closest?.('#clearManualReceivingDraft');if(c){e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();resetCurrent();return;}
 if(e.target?.closest?.('[data-route],a,button'))requestAnimationFrame(retitle);
},true);
document.addEventListener('change',e=>{
 if(e.target?.id==='receiveBy'){lastNormalize='';scheduleNormalize(true,100);}
 if(e.target?.id==='receiveStockClass'&&e.isTrusted)stockTouched=true;
},true);
document.addEventListener('focusin',e=>{if(e.target?.closest?.('#manualReceivingDraftPanel'))retitle();},true);
function boot(){retitle();setMainDefault(fc27ServerDraft||null,false);try{window.FC32Receiving?.hydrate?.().catch(()=>{});}catch(_){}scheduleNormalize(true,10);setTimeout(retitle,500);setTimeout(retitle,1400);}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();

/* FC_R145F47_STABLE_BOX_LIST_ACTIVE_UI
 * Keep receiving box lists open/closed exactly as the operator left them.
 * This lives in the already-active receiving-stable bundle, so it does not
 * depend on a historical app-r*.js filename.
 */
(()=>{
  if(globalThis.__FC_R145F47_STABLE_BOX_LIST_ACTIVE_UI)return;
  globalThis.__FC_R145F47_STABLE_BOX_LIST_ACTIVE_UI=true;
  const STORE='fc_r145f47_receiving_box_list:';
  const normalize=v=>String(v??'').replace(/\s+/g,' ').trim();
  const kind=d=>{
    const t=normalize(d?.querySelector(':scope > summary')?.textContent||'');
    if(t.startsWith('Все короба этой приёмки'))return'ALL_BOXES';
    if(t.startsWith('Остальные короба этой приёмки'))return'OTHER_BOXES';
    if(t.startsWith('Места этой приёмки'))return'ALL_PLACES';
    return'';
  };
  const scope=()=>{
    let c='',w='';
    try{c=normalize(state?.clientId||'');w=normalize(state?.warehouseId||'');}catch(_){}
    return c+'|'+w;
  };
  const key=d=>{const k=kind(d);return k?STORE+scope()+'|'+k:'';};
  const load=d=>{
    const k=key(d);if(!k)return null;
    try{const v=sessionStorage.getItem(k);return v==='1'?true:v==='0'?false:null;}catch(_){return null;}
  };
  const save=(d,v)=>{const k=key(d);if(!k)return;try{sessionStorage.setItem(k,v?'1':'0');}catch(_){}};
  const targets=()=>Array.from(document.querySelectorAll('details')).filter(d=>Boolean(kind(d)));
  const capture=()=>{
    const m=new Map();
    for(const d of targets()){const k=kind(d);if(k)m.set(k,Boolean(d.open));}
    return m;
  };
  const restore=(before=null)=>{
    for(const d of targets()){
      const k=kind(d);if(!k)continue;
      let wanted=before instanceof Map&&before.has(k)?before.get(k):load(d);
      if(wanted===null)wanted=true;
      if(Boolean(d.open)!==Boolean(wanted))d.open=Boolean(wanted);
      d.dataset.fcR145f47Stable='1';
    }
  };
  document.addEventListener('toggle',ev=>{
    const d=ev.target;
    if(d instanceof HTMLDetailsElement&&kind(d))save(d,Boolean(d.open));
  },true);
  let observerTimer=0;
  const observer=new MutationObserver(()=>{
    clearTimeout(observerTimer);
    observerTimer=setTimeout(()=>{installWrapper();restore();},0);
  });
  const startObserver=()=>{
    try{observer.observe(document.documentElement,{subtree:true,childList:true});}catch(_){}
  };
  function installWrapper(){
    try{
      if(typeof renderManualReceivingDraft!=='function')return false;
      if(renderManualReceivingDraft.__fcR145f47StableBoxList)return true;
      const original=renderManualReceivingDraft;
      const wrapped=function(){
        const before=capture();
        const result=original.apply(this,arguments);
        queueMicrotask(()=>restore(before));
        return result;
      };
      wrapped.__fcR145f47StableBoxList=true;
      wrapped.__fcR145f47Original=original;
      renderManualReceivingDraft=wrapped;
      try{globalThis.renderManualReceivingDraft=wrapped;}catch(_){}
      return true;
    }catch(_){return false;}
  }
  const boot=()=>{
    installWrapper();restore();startObserver();
    let tries=0;
    const timer=setInterval(()=>{
      tries+=1;const ok=installWrapper();restore();
      if(ok||tries>=40)clearInterval(timer);
    },250);
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();

/* FC_R145F48_NESTED_BOX_CONTENT_STABLE
 * Preserve BOTH the outer box list and each individual box contents across
 * background receiving re-renders. A click on a box summary is recorded
 * before any async fetch/render can replace the DOM, so the box cannot snap
 * shut while the operator is working with it.
 */
(()=>{
  if(globalThis.__FC_R145F48_NESTED_BOX_CONTENT_STABLE)return;
  globalThis.__FC_R145F48_NESTED_BOX_CONTENT_STABLE=true;
  const STORE='fc_r145f48_receiving_details:';
  const norm=v=>String(v??'').replace(/\s+/g,' ').trim();
  let restoring=false;
  const scope=()=>{
    let c='',w='',worker='',ref='';
    try{c=norm(state?.clientId||'');w=norm(state?.warehouseId||'');}catch(_){}
    try{worker=norm(document.getElementById('receiveWorker')?.value||'');}catch(_){}
    try{ref=norm(document.getElementById('receiveReference')?.value||'');}catch(_){}
    return [c,w,worker,ref].join('|');
  };
  const summaryText=d=>norm(d?.querySelector(':scope > summary')?.textContent||'');
  const dataId=d=>{
    const s=d?.querySelector(':scope > summary');
    const vals=[
      d?.dataset?.containerCode,d?.dataset?.boxCode,d?.dataset?.containerId,d?.dataset?.boxId,
      s?.dataset?.containerCode,s?.dataset?.boxCode,s?.dataset?.containerId,s?.dataset?.boxId
    ].map(norm).filter(Boolean);
    return vals[0]||'';
  };
  const detailId=d=>{
    const t=summaryText(d);
    if(!t)return'';
    if(t.startsWith('Все короба этой приёмки'))return'OUTER:ALL_BOXES';
    if(t.startsWith('Остальные короба этой приёмки'))return'OUTER:OTHER_BOXES';
    if(t.startsWith('Места этой приёмки'))return'OUTER:ALL_PLACES';
    const explicit=dataId(d);if(explicit)return'BOX:'+explicit;
    // Current UI summary: "BOX_NAME 13 шт. · 4 поз.". Remove the volatile counters.
    const base=norm(t.replace(/\s+\d+\s*шт\.?\s*[·•\-–—]?\s*\d+\s*поз\.?\s*$/iu,''));
    if(base&&base!==t)return'BOX:'+base;
    // Fallback for nested <details> under one of the receiving box collections.
    const parent=d.parentElement?.closest?.('details');
    const pt=summaryText(parent);
    if(parent&&(pt.startsWith('Все короба этой приёмки')||pt.startsWith('Остальные короба этой приёмки'))){
      return'BOX:'+t;
    }
    return'';
  };
  const storageKey=d=>{const id=detailId(d);return id?STORE+scope()+'|'+id:'';};
  const load=d=>{const k=storageKey(d);if(!k)return null;try{const v=sessionStorage.getItem(k);return v==='1'?true:v==='0'?false:null;}catch(_){return null;}};
  const save=(d,v)=>{const k=storageKey(d);if(!k)return;try{sessionStorage.setItem(k,v?'1':'0');}catch(_){}};
  const targets=()=>Array.from(document.querySelectorAll('details')).filter(d=>Boolean(detailId(d)));
  const capture=()=>{const m=new Map();for(const d of targets()){const id=detailId(d);if(id)m.set(id,Boolean(d.open));}return m;};
  const restore=(before=null)=>{
    restoring=true;
    try{
      for(const d of targets()){
        const id=detailId(d);if(!id)continue;
        let wanted=before instanceof Map&&before.has(id)?before.get(id):load(d);
        if(wanted===null){wanted=id.startsWith('OUTER:');}
        if(Boolean(d.open)!==Boolean(wanted))d.open=Boolean(wanted);
        d.dataset.fcR145f48Stable='1';
      }
    }finally{queueMicrotask(()=>{restoring=false;});}
  };
  // Record intent immediately on click, before a lazy server response can re-render.
  document.addEventListener('click',ev=>{
    const s=ev.target?.closest?.('summary');if(!s)return;
    const d=s.parentElement;if(!(d instanceof HTMLDetailsElement)||!detailId(d))return;
    save(d,!Boolean(d.open));
  },true);
  document.addEventListener('toggle',ev=>{
    const d=ev.target;
    if(restoring)return;
    if(d instanceof HTMLDetailsElement&&detailId(d))save(d,Boolean(d.open));
  },true);
  let observerTimer=0;
  const observer=new MutationObserver(()=>{
    clearTimeout(observerTimer);
    observerTimer=setTimeout(()=>{installWrapper();restore();},0);
  });
  const startObserver=()=>{try{observer.observe(document.documentElement,{subtree:true,childList:true});}catch(_){}};
  function installWrapper(){
    try{
      if(typeof renderManualReceivingDraft!=='function')return false;
      if(renderManualReceivingDraft.__fcR145f48NestedStable)return true;
      const original=renderManualReceivingDraft;
      const wrapped=function(){
        const before=capture();
        const result=original.apply(this,arguments);
        queueMicrotask(()=>restore(before));
        return result;
      };
      wrapped.__fcR145f48NestedStable=true;
      wrapped.__fcR145f48Original=original;
      renderManualReceivingDraft=wrapped;
      try{globalThis.renderManualReceivingDraft=wrapped;}catch(_){}
      return true;
    }catch(_){return false;}
  }
  const boot=()=>{
    installWrapper();restore();startObserver();
    let tries=0;const timer=setInterval(()=>{
      tries+=1;const ok=installWrapper();restore();if(ok||tries>=40)clearInterval(timer);
    },250);
  };
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();

