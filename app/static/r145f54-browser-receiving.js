/* FC_R145F54_CANONICAL_ONE_SCAN
 * Browser-master receiving built on the original r145f26 IndexedDB draft store.
 * - the visible draft lives in FC26DraftStore and never disappears after server ACK;
 * - the server receives an exact snapshot in the background;
 * - edits/removals are local-first and persistent;
 * - unfinished server drafts are recovered only when the browser draft is empty;
 * - unknown barcodes always get a product-card path instead of a dead error.
 */
(()=>{'use strict';
  if(globalThis.__FC_R145F54_CANONICAL_ONE_SCAN)return;
  globalThis.__FC_R145F54_CANONICAL_ONE_SCAN=true;
  const MARK='FC_R145F54_CANONICAL_ONE_SCAN';
  const $=id=>document.getElementById(id), qs=s=>document.querySelector(s), text=v=>String(v??'').trim();
  const clone=v=>{try{return structuredClone(v);}catch(_){return JSON.parse(JSON.stringify(v));}};
  const uid=()=>{try{return crypto.randomUUID();}catch(_){return `${Date.now()}-${Math.random().toString(36).slice(2)}`;}};
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const rowsOf=d=>Array.isArray(d?.rows)?d.rows:[];
  const qtyOf=d=>rowsOf(d).reduce((n,r)=>n+Math.max(0,Number(r?.quantity||0)),0);
  const posOf=d=>rowsOf(d).filter(r=>Number(r?.quantity||0)>0).length;
  const rowKey=r=>[text(r?.barcode),text(r?.location_code),text(r?.container_code||r?.container_name)].join('|');
  const worker=()=>text($('receiveBy')?.value||$('receiveWorker')?.value||'admin');
  const reference=()=>text($('receiveReference')?.value||'');
  const scopeIds=()=>{try{return{client_id:text(state?.clientId),warehouse_id:text(state?.warehouseId)};}catch(_){return{client_id:'',warehouse_id:''};}};
  let key='',draft=null,revision=1,serverRevision=0,serverReceiptId='',token=uid(),syncTimer=0,syncing=false,syncAgain=false,finishing=false,booted=false,lastServerMs=0,activeDrafts=[],localWriteBusy=false;
  let queueOriginalPut=null,cardsLookupOriginal=null;

  function localKey(){try{return typeof manualReceivingDraftKey==='function'?manualReceivingDraftKey():'';}catch(_){return'';}}
  function validScope(){const x=scopeIds();return Boolean(x.client_id&&x.warehouse_id&&worker());}
  function normalize(d){const x=clone(d||{id:uid(),rows:[]});x.rows=rowsOf(x).filter(r=>Number(r?.quantity||0)>0).map(r=>({...r,id:String(r.id||`local:${uid()}`),quantity:Math.max(0,Number(r.quantity||0)),product_found:r.product_found!==false}));x.server_saved=false;x.posting=false;return x;}
  function serverShape(){const x=scopeIds();return{...x,worker:worker(),reference:reference()||null,preferred_receipt_id:serverReceiptId||((draft?.id&&!String(draft.id).startsWith('local:'))?String(draft.id):null)};}
  function desiredRows(){return rowsOf(draft).filter(r=>Number(r.quantity||0)>0).map(r=>({id:String(r.id||'').startsWith('local:')?null:r.id||null,barcode:text(r.barcode),quantity:Number(r.quantity||0),location_code:text(r.location_code),container_code:text(r.container_code||r.container_name)||null,container_name:text(r.container_name)||null,article:r.article||null,size:r.size||null,name:r.name||null,product_found:r.product_found!==false,location_id:r.location_id||null,container_id:r.container_id||null,section:r.section||null,aisle:r.aisle||null,pallet:r.pallet||null,storage_display:r.storage_display||null,duplicated_from:r.duplicated_from||null,product_id:r.product_id||null}));}
  async function post(url,body,timeout=10000){const c=new AbortController(),t=setTimeout(()=>c.abort(),timeout);try{const r=await fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',signal:c.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const raw=await r.text();let d;try{d=JSON.parse(raw);}catch(_){d={detail:raw};}if(!r.ok){const m=typeof d?.detail==='string'?d.detail:(d?.detail?.message||raw||`HTTP ${r.status}`);const e=new Error(m);e.status=r.status;e.data=d;throw e;}return d;}finally{clearTimeout(t);}}
  function status(kind='local',extra=''){
    const el=$('receiveResult');if(!el||!draft)return;const p=posOf(draft),u=qtyOf(draft);let title='Приёмка сохранена в браузере';
    if(kind==='sync')title='Приёмка сохранена в браузере · сервер сохраняет в фоне';
    if(kind==='ok')title='Приёмка сохранена в браузере и на сервере';
    if(kind==='err')title='Приёмка сохранена в браузере · сервер временно недоступен';
    el.innerHTML=`<b${kind==='err'?' class="bad"':''}>${title}</b><span>${p} поз. · ${u} шт.${extra?` · ${extra}`:''}</span>`;
  }
  function setServerDraftForLegacy(d){try{fc27ServerDraft=clone(d);fc27ServerDraftContext=typeof fc27DraftContext==='function'?fc27DraftContext():fc27ServerDraftContext;fc27ServerDraftFlight=null;}catch(_){} }
  function render(){if(!draft)return;setServerDraftForLegacy(draft);try{renderManualReceivingDraft();}catch(e){console.warn('r145f53 render',e);}status(serverRevision>=revision?'ok':'local',lastServerMs?`сервер ${lastServerMs} мс`:'');renderActiveCard();}
  async function persist(next,{renderNow=true}={}){
    if(!key)key=localKey();if(!key)throw Error('Не удалось определить ключ черновика');
    draft=normalize(next);const current=await FC26DraftStore.ready(key);if(current?.__fc26_rev!==undefined)draft.__fc26_rev=current.__fc26_rev;if(current?.__fc26_key)draft.__fc26_key=current.__fc26_key;
    localWriteBusy=true;try{draft=await FC26DraftStore.put(key,draft);}finally{localWriteBusy=false;}revision++;if(renderNow)render();return draft;
  }
  function mergeIds(server){if(!server||!draft)return;const sm=new Map(rowsOf(server).map(r=>[rowKey(r),r]));for(const r of rowsOf(draft)){const s=sm.get(rowKey(r));if(s){if(s.id)r._server_line_id=s.id;if(s.location_id)r.location_id=s.location_id;if(s.container_id)r.container_id=s.container_id;if(s.product_id)r.product_id=s.product_id;}}if(server.id){serverReceiptId=String(server.id);draft._server_receipt_id=serverReceiptId;}}

  async function currentServer(){const d=await post('/r145f52/manual-drafts/current',serverShape(),8000);activeDrafts=Array.isArray(d?.active)?d.active:[];return d?.snapshot||null;}
  async function legacyPending(){try{if(typeof FC32Queue==='undefined'||typeof FC32Queue.list!=='function'||typeof scopeKey!=='function')return[];return await FC32Queue.list(scopeKey());}catch(_){return[];}}
  function applyEventToDraft(d,ev){const p=ev?.payload||ev||{},b=text(p.barcode),loc=text(p.location_code),box=text(p.container_code||p.container_name);if(!b||!loc)return false;let r=rowsOf(d).find(x=>rowKey(x)===[b,loc,box].join('|'));if(!r){r={id:`local:${uid()}`,barcode:b,quantity:0,location_code:loc,container_code:box||null,container_name:text(p.container_name)||box||null,article:p.article||null,size:p.size||null,name:p.name||null,product_found:p.product_found!==false,location_id:p.location_id||null,container_id:p.container_id||null,section:p.section||null,aisle:p.aisle||null,pallet:p.pallet||null,storage_display:p.storage_display||null,duplicated_from:p.duplicated_from||null,product_id:p.product_id||null};d.rows.push(r);}r.quantity=Math.max(0,Number(r.quantity||0))+Math.max(1,Number(p.quantity||1));for(const k of ['article','size','name','location_id','container_id','container_name','section','aisle','pallet','storage_display','duplicated_from','product_id'])if(p[k]!==undefined&&p[k]!==null&&p[k]!=='')r[k]=p[k];return true;}
  async function clearLegacyQueueAfterMirror(){try{if(typeof FC32Queue==='undefined'||typeof FC32Queue.list!=='function'||typeof FC32Queue.remove!=='function'||typeof scopeKey!=='function')return;const list=await FC32Queue.list(scopeKey());const ids=list.map(x=>String(x?.payload?.request_id||x?.id||'')).filter(Boolean);if(ids.length)await FC32Queue.remove(scopeKey(),ids);}catch(_){} }

  async function recover(){
    key=localKey();if(!key||!validScope())return false;
    let local=normalize(await FC26DraftStore.ready(key));
    const pending=await legacyPending();for(const ev of pending)applyEventToDraft(local,ev);
    let server=null;try{server=await currentServer();}catch(e){console.warn('r145f53 current recovery',e);}
    // Browser wins if it has rows. Server recovery is used only when browser is empty.
    if(!posOf(local)&&server&&posOf(server)){local=normalize(server);serverReceiptId=String(server.id||'');}
    else if(server?.id)serverReceiptId=String(server.id);
    if(pending.length||(!posOf(await FC26DraftStore.ready(key))&&posOf(local)))await persist(local,{renderNow:false});else draft=local;
    if(server)mergeIds(server);render();scheduleSync(50);return true;
  }

  async function syncNow(force=false){
    if(!draft||!validScope())return false;if(syncing){syncAgain=true;return false;}if(!force&&serverRevision>=revision)return true;
    const rev=revision;syncing=true;status('sync');
    try{const data=await post('/r145f52/manual-drafts/reconcile',{...serverShape(),browser_revision:rev,browser_token:token,rows:desiredRows()},12000);serverReceiptId=String(data?.target_receipt_id||serverReceiptId||'');if(data?.snapshot)mergeIds(data.snapshot);if(revision===rev)serverRevision=rev;lastServerMs=Number(data?.server_elapsed_ms||0);await clearLegacyQueueAfterMirror();status('ok',lastServerMs?`сервер ${lastServerMs} мс`:'');renderActiveCard();return true;}
    catch(e){status('err',String(e?.message||e));scheduleSync(1200);return false;}
    finally{syncing=false;if(syncAgain){syncAgain=false;scheduleSync(50);}}
  }
  function scheduleSync(ms=180){clearTimeout(syncTimer);syncTimer=setTimeout(()=>syncNow(false),ms);}

  async function localSetQty(rowId,value){if(!draft)return;const id=String(rowId||'');let r=rowsOf(draft).find(x=>String(x.id||'')===id||String(x._server_line_id||'')===id);if(!r)return;const n=Math.max(0,Number(value||0));r.quantity=n;draft.rows=rowsOf(draft).filter(x=>Number(x.quantity||0)>0);await persist(draft);scheduleSync(20);}
  async function localClear(){if(!draft)return;draft.rows=[];await persist(draft);scheduleSync(20);}
  async function localCardRefresh(barcode,saved){if(!draft)return;let changed=false;for(const r of rowsOf(draft)){if(text(r.barcode)===text(barcode)){r.article=saved.article||r.article;r.size=saved.size||r.size;r.name=saved.name||r.name;r.product_id=saved.sku_id||saved.product_id||r.product_id;r.product_found=true;changed=true;}}if(changed){await persist(draft);scheduleSync(20);}}

  function installQueueHook(){try{if(typeof FC32Queue==='undefined'||typeof FC32Queue.put!=='function'||FC32Queue.put.__fc52)return false;queueOriginalPut=FC32Queue.put.bind(FC32Queue);const wrapped=async function(scope,ev){const p=ev?.payload||ev||{};if(draft&&p.barcode&&p.location_code){const next=clone(draft);applyEventToDraft(next,ev);await persist(next);markScanFound(ev);scheduleSync(20);return ev?.id||p.request_id||true;}return queueOriginalPut(scope,ev);};wrapped.__fc52=true;wrapped.__fc53=true;FC32Queue.put=wrapped;return true;}catch(_){return false;}}

  function installCardLookupFix(){try{if(!window.FC26Cards||typeof FC26Cards.lookup!=='function'||FC26Cards.lookup.__fc52)return false;cardsLookupOriginal=FC26Cards.lookup.bind(FC26Cards);const wrapped=async function(code){try{return await cardsLookupOriginal(code);}catch(e){const m=String(e?.message||'');if(e?.status===404||/не найден.*локальн|не найден.*баз/i.test(m))return{found:false,barcode:text(code),article:'',size:'',name:'',category:'',sku_id:null,archived:false,hidden:false};throw e;}};wrapped.__fc52=true;FC26Cards.lookup=wrapped;return true;}catch(_){return false;}}

  let fc54LegacyPatchOriginal=null;
  function installCanonicalQuantityGuard(){
    try{
      if(typeof fc30PatchActiveBox!=='function')return false;
      if(fc30PatchActiveBox.__fc54Canonical)return true;
      const original=fc30PatchActiveBox;
      fc54LegacyPatchOriginal=original;
      const wrapped=function(result){
        // r145f42-fast mutates a legacy optimistic snapshot before the browser-master
        // IndexedDB draft is persisted. That snapshot can be stale and can briefly show
        // N+1 (most visibly 2 after one paste). In browser-master mode the only visible
        // quantity is the canonical IndexedDB draft rendered by persist().
        if(draft&&validScope())return false;
        return original.apply(this,arguments);
      };
      wrapped.__fc54Canonical=true;
      wrapped.__fc54Original=original;
      fc30PatchActiveBox=wrapped;
      try{globalThis.fc30PatchActiveBox=wrapped;}catch(_){}
      return true;
    }catch(_){return false;}
  }

  let fc54LastAcceptedToken='',fc54LastAcceptedBarcode='';
  function scanToken(input){
    if(!input)return'';
    let t=input.dataset.fc54ScanToken||'';
    if(!t){t=uid();input.dataset.fc54ScanToken=t;}
    return t;
  }
  function rotateScanToken(input){if(input)input.dataset.fc54ScanToken=uid();}
  let fc53ScanGeneration=0,fc53CapturedGeneration=-1,fc53CaptureBusyGeneration=-1,fc53LastInputValue='';
  function installOneScanGuard(){
    const input=$('receiveSku');
    if(!input||typeof captureManualReceivingScan!=='function'||captureManualReceivingScan.__fc53)return false;
    const original=captureManualReceivingScan;
    const wrapped=function(useCurrentQuantity=false){
      const raw=text(input.value);
      if(!raw)return original(useCurrentQuantity);
      if(!fc53ScanGeneration)fc53ScanGeneration=1;
      const gen=fc53ScanGeneration,token=scanToken(input);
      if(fc53CapturedGeneration===gen||fc53CaptureBusyGeneration===gen)return false;
      if(token&&token===fc54LastAcceptedToken&&raw===fc54LastAcceptedBarcode)return false;
      fc53CaptureBusyGeneration=gen;
      try{
        const ok=original(useCurrentQuantity);
        if(ok){
          fc53CapturedGeneration=gen;
          fc54LastAcceptedToken=token;
          fc54LastAcceptedBarcode=raw;
          rotateScanToken(input);
        }
        return ok;
      }finally{
        fc53CaptureBusyGeneration=-1;
        fc53LastInputValue=text(input.value);
        if(!fc53LastInputValue)fc53LastInputValue='';
      }
    };
    wrapped.__fc53=true;wrapped.__fc53_original=original;captureManualReceivingScan=wrapped;
    input.addEventListener('input',()=>{
      const value=text(input.value);
      if(value&&!fc53LastInputValue){fc53ScanGeneration++;rotateScanToken(input);}
      fc53LastInputValue=value;
      const hint=$('receiveSkuHint');
      if(!value){if(hint&&/^(Не найдено|Проверяем ШК)/i.test(text(hint.textContent))){hint.textContent='';hint.className='';}return;}
      let local=null;try{const normalized=typeof normalizeReceivingBarcode==='function'?normalizeReceivingBarcode(value):{barcode:value};local=typeof findSku==='function'?findSku(text(normalized?.barcode)||value):null;}catch(_){}
      if(hint){
        if(local){hint.textContent=typeof skuLabel==='function'?skuLabel(local):[local.article,local.size,local.name].filter(Boolean).join(' · ');hint.className='good';}
        else{hint.textContent='Проверяем ШК…';hint.className='';}
      }
    });
    return true;
  }

  function markScanFound(ev){
    const p=ev?.payload||ev||{},product=ev?.product||{};
    const hint=$('receiveSkuHint');if(!hint)return;
    const label=[product.article||p.article,product.name||p.name,product.size||p.size].filter(Boolean).join(' · ');
    if(label){hint.textContent=label;hint.className='good';}
    else if(p.barcode){hint.textContent=`ШК ${text(p.barcode)} добавлен`;hint.className='good';}
  }



  async function finish(commit){
    if(finishing)return;finishing=true;commit.disabled=true;status('sync','завершаем приёмку');
    try{const ok=await syncNow(true);if(!ok||serverRevision<revision)throw Error('Сервер не подтвердил текущий состав. Приёмка полностью сохранена в браузере — повторите завершение после восстановления связи.');
      const data=await currentServer();if(data){serverReceiptId=String(data.id||serverReceiptId);setServerDraftForLegacy(data);}else{setServerDraftForLegacy({...draft,id:serverReceiptId||draft.id});}
      globalThis.__FC52_COMMIT_BYPASS=true;commit.disabled=false;commit.click();globalThis.__FC52_COMMIT_BYPASS=false;
      const rid=serverReceiptId||text(data?.id);const started=Date.now();while(rid&&Date.now()-started<35000){await sleep(650);try{const st=await post(`/r145f52/manual-drafts/${encodeURIComponent(rid)}/status`,serverShape(),6000);if(!st.exists||String(st.status||'').toUpperCase()!=='DRAFT'){const fresh={id:uid(),rows:[]};await persist(fresh,{renderNow:false});draft=fresh;serverReceiptId='';serverRevision=revision;render();finishing=false;return;}}catch(_){} }throw Error('Сервер пока не подтвердил завершение. Данные сохранены в браузере; можно повторить кнопку.');
    }catch(e){status('err',String(e?.message||e));}finally{globalThis.__FC52_COMMIT_BYPASS=false;finishing=false;commit.disabled=false;}
  }

  function renderActiveCard(){const hist=$('r62ManualReceivingHistory');if(!hist||!draft)return;let box=$('fc52ActiveReceiptCard');if(!box){box=document.createElement('div');box.id='fc52ActiveReceiptCard';box.style.cssText='margin:0 0 14px;padding:14px 16px;border:1px solid #b7e4c7;border-radius:14px;background:#f1fbf5;display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap';const rows=$('r62ManualReceivingRows');hist.insertBefore(box,rows||null);}const p=posOf(draft),u=qtyOf(draft);if(!p){box.remove();return;}box.innerHTML=`<div><b>Текущая незавершённая приёмка</b><div>${p} поз. · ${u} шт. · ${worker()}${reference()?` · ${reference()}`:''}</div></div><button type="button" class="secondary" id="fc52OpenCurrentReceipt">Открыть текущую приёмку</button>`;const btn=$('fc52OpenCurrentReceipt');if(btn)btn.onclick=()=>{render();$('manualReceivingDraftPanel')?.scrollIntoView({behavior:'smooth',block:'start'});};}

  function installEvents(){
    document.addEventListener('change',ev=>{const input=ev.target?.closest?.('[data-manual-draft-qty]');if(input){ev.preventDefault();ev.stopImmediatePropagation();const n=Number(input.value);if(!Number.isInteger(n)||n<0){status('err','Количество должно быть целым числом 0 или больше');return;}localSetQty(input.dataset.manualDraftQty,n).catch(e=>status('err',String(e)));return;}if(ev.target?.id==='receiveReference'&&draft){draft.reference=reference();persist(draft,{renderNow:false}).then(()=>scheduleSync(20));}},true);
    document.addEventListener('click',ev=>{
      const edit=ev.target?.closest?.('[data-fc26-card-edit]');if(edit){ev.preventDefault();ev.stopImmediatePropagation();const code=text(edit.dataset.fc26CardEdit);(async()=>{try{let p;try{p=await FC26Cards.lookup(code);}catch(_){p={found:false,barcode:code};}const saved=await FC26Cards.edit({...p,barcode:p?.barcode||code});await localCardRefresh(code,saved);}catch(e){try{toast(String(e?.message||e),true);}catch(_){}}})();return;}
      const remove=ev.target?.closest?.('[data-manual-draft-remove]');if(remove){ev.preventDefault();ev.stopImmediatePropagation();localSetQty(remove.dataset.manualDraftRemove,0).catch(e=>status('err',String(e)));return;}
      const clear=ev.target?.closest?.('button');if(clear&&/Очистить список/i.test(text(clear.textContent))){ev.preventDefault();ev.stopImmediatePropagation();if(confirm('Очистить текущую приёмку? Данные будут удалены из браузерного черновика и затем с сервера.'))localClear().catch(e=>status('err',String(e)));return;}
      const commit=ev.target?.closest?.('#commitManualReceivingDraft');if(commit&&!globalThis.__FC52_COMMIT_BYPASS){ev.preventDefault();ev.stopImmediatePropagation();finish(commit);return;}
    },true);
  }

  async function boot(){
    let tries=0;while(tries++<80){if(window.FC26DraftStore&&window.FC26Cards&&typeof renderManualReceivingDraft==='function'&&typeof manualReceivingDraftKey==='function')break;await sleep(100);}if(!window.FC26DraftStore||!window.FC26Cards)return;
    installCardLookupFix();installQueueHook();installCanonicalQuantityGuard();installOneScanGuard();installEvents();
    const t=setInterval(()=>{installCardLookupFix();installQueueHook();installCanonicalQuantityGuard();installOneScanGuard();if(++tries>180)clearInterval(t);},500);
    await recover();booted=true;
    let last=key;setInterval(async()=>{if(finishing)return;const k=localKey();if(k&&k!==last){last=k;key=k;draft=null;serverReceiptId='';serverRevision=0;revision=1;await recover();}},1000);
    window.addEventListener('fc26-draft-state',e=>{if(!booted||finishing||localWriteBusy||e.detail?.key!==key)return;try{const x=FC26DraftStore.snapshot(key);if(x&&JSON.stringify(rowsOf(x))!==JSON.stringify(rowsOf(draft))){draft=normalize(x);revision++;setServerDraftForLegacy(draft);render();scheduleSync(80);}}catch(_){} });
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
