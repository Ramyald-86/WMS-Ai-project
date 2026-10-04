/* FC_R145F51_BROWSER_FIRST_RECEIVING
 * Browser-first manual receiving:
 * - IndexedDB draft is the UI/source of truth during work;
 * - server is an exact background mirror and never replaces the working UI;
 * - edit/remove are local-first, then mirrored silently;
 * - final commit first reconciles the complete local draft, then runs the normal commit.
 */
(()=>{
  if(globalThis.__FC_R145F51_BROWSER_FIRST_RECEIVING)return;
  globalThis.__FC_R145F51_BROWSER_FIRST_RECEIVING=true;
  const DB='FulfillCoreR145F51Receiving', STORE='drafts', VER=1;
  const s=v=>String(v??'').trim();
  const clone=v=>{try{return structuredClone(v);}catch(_){return JSON.parse(JSON.stringify(v));}};
  const uid=()=>{try{return crypto.randomUUID();}catch(_){return `${Date.now()}-${Math.random().toString(36).slice(2)}`;}};
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const q=x=>document.querySelector(x);
  const worker=()=>s(q('#receiveBy')?.value||q('#receiveWorker')?.value||'admin');
  const reference=()=>s(q('#receiveReference')?.value||'');
  const ids=()=>{let c='',w='';try{c=s(state?.clientId);w=s(state?.warehouseId);}catch(_){}return{client_id:c,warehouse_id:w};};
  const scopeKey51=()=>{const x=ids();return `${x.client_id}|${x.warehouse_id}|${worker()}`;};
  const rowKey=r=>[s(r?.barcode),s(r?.location_code),s(r?.container_code||r?.container_name)].join('|');
  const units=snap=>(snap?.rows||[]).reduce((a,r)=>a+Math.max(0,Number(r.quantity||0)),0);
  const positions=snap=>(snap?.rows||[]).filter(r=>Number(r.quantity||0)>0).length;
  let rec=null, recKey='', booted=false, syncTimer=0, syncing=false, syncAgain=false, renderBusy=false, renderFingerprint='', finishing=false;
  let originalRender=null, originalLoad=null, originalQueuePut=null;

  function openDb(){return new Promise((resolve,reject)=>{const r=indexedDB.open(DB,VER);r.onupgradeneeded=()=>{const d=r.result;if(!d.objectStoreNames.contains(STORE))d.createObjectStore(STORE,{keyPath:'key'});};r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});}
  async function getRec(key){const d=await openDb();return await new Promise((resolve,reject)=>{const tx=d.transaction(STORE,'readonly'),r=tx.objectStore(STORE).get(key);r.onsuccess=()=>resolve(r.result||null);r.onerror=()=>reject(r.error);tx.oncomplete=()=>d.close();});}
  async function putRec(v){const d=await openDb();await new Promise((resolve,reject)=>{const tx=d.transaction(STORE,'readwrite');tx.objectStore(STORE).put(v);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});d.close();}
  async function delRec(key){const d=await openDb();await new Promise((resolve,reject)=>{const tx=d.transaction(STORE,'readwrite');tx.objectStore(STORE).delete(key);tx.oncomplete=resolve;tx.onerror=()=>reject(tx.error);});d.close();}

  function emptySnap(){const x=ids();return{id:`local:${uid()}`,client_id:x.client_id,warehouse_id:x.warehouse_id,worker:worker(),reference:reference(),status:'DRAFT',rows:[],positions:0,units:0,_fc51_local:true};}
  function normalizeSnap(snap){const out=clone(snap||emptySnap());out.rows=Array.isArray(out.rows)?out.rows.filter(r=>Number(r?.quantity||0)>0).map(r=>({...r,quantity:Math.max(0,Number(r.quantity||0))})):[];out.positions=positions(out);out.units=units(out);out.worker=worker()||out.worker||'admin';out.reference=reference()||out.reference||'';out.status='DRAFT';out._fc51_browser_first=true;return out;}
  function mergeServerIds(local,server){if(!local||!server)return local;const sm=new Map((server.rows||[]).map(r=>[rowKey(r),r]));for(const r of local.rows||[]){const sr=sm.get(rowKey(r));if(sr){r.id=sr.id||r.id;r.location_id=sr.location_id||r.location_id;r.container_id=sr.container_id||r.container_id;r.product_id=sr.product_id||r.product_id;}}if(server.id&&!s(server.id).startsWith('local:'))local.id=server.id;return local;}
  function bump(){if(!rec)return;rec.revision=(Number(rec.revision)||0)+1;rec.updated_at=Date.now();rec.snapshot=normalizeSnap(rec.snapshot);}
  async function save(){if(rec){rec.key=recKey;await putRec(rec);}}
  function showStatus(kind='local',extra=''){
    const el=q('#receiveResult');if(!el||!rec)return;
    const n=units(rec.snapshot),p=positions(rec.snapshot),pending=(Number(rec.server_revision)||0)<(Number(rec.revision)||0);
    let title='Приёмка сохранена в браузере',text=`${p} поз. · ${n} шт.`;
    if(kind==='syncing'||pending){title='Приёмка сохранена в браузере · сервер синхронизируется';}
    if(kind==='synced'&&!pending){title='Приёмка сохранена в браузере и на сервере';}
    if(kind==='error'){title='Приёмка сохранена в браузере · сервер временно недоступен';}
    if(extra)text+=` · ${extra}`;
    el.innerHTML=`<b${kind==='error'?' class="bad"':''}>${title}</b><span>${text}</span>`;
  }
  function assignDraft(snap){try{fc27ServerDraft=snap;fc27ServerDraftContext=typeof fc27DraftContext==='function'?fc27DraftContext():fc27ServerDraftContext;fc27ServerDraftFlight=null;}catch(_){} }
  function localFingerprint(){if(!rec)return'';return `${rec.key}|${rec.revision}|${rec.snapshot?.id}|${positions(rec.snapshot)}|${units(rec.snapshot)}`;}
  function renderLocal(force=false){if(!rec||finishing||renderBusy)return;const fp=localFingerprint();if(!force&&fp===renderFingerprint)return;renderFingerprint=fp;renderBusy=true;try{rec.snapshot=normalizeSnap(rec.snapshot);assignDraft(clone(rec.snapshot));if(originalRender)originalRender();}catch(e){console.warn('r145f51 render',e);}finally{renderBusy=false;}showStatus((Number(rec.server_revision)||0)>=(Number(rec.revision)||0)?'synced':'local');}
  function installRenderOverride(){
    try{
      const cur=globalThis.renderManualReceivingDraft;
      if(typeof cur==='function'&&!cur.__fc51){originalRender=cur.__fc51Original||cur;const wrapped=function(){if(rec&&!finishing){renderLocal(false);return;}return originalRender.apply(this,arguments);};wrapped.__fc51=true;wrapped.__fc51Original=originalRender;globalThis.renderManualReceivingDraft=wrapped;try{renderManualReceivingDraft=wrapped;}catch(_){}}
      const ld=globalThis.loadManualReceivingDraft;
      if(typeof ld==='function'&&!ld.__fc51){originalLoad=ld;const l=function(){return rec?.snapshot?clone(rec.snapshot):originalLoad.apply(this,arguments);};l.__fc51=true;globalThis.loadManualReceivingDraft=l;try{loadManualReceivingDraft=l;}catch(_){}}
    }catch(_){ }
  }

  async function post(url,body,timeout=15000){const c=new AbortController(),t=setTimeout(()=>c.abort(),timeout);try{const r=await fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',signal:c.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),raw=await r.text();let d;try{d=JSON.parse(raw);}catch(_){d={detail:raw};}if(!r.ok){const e=new Error(typeof d?.detail==='string'?d.detail:(d?.detail?.message||raw||`HTTP ${r.status}`));e.status=r.status;e.data=d;throw e;}return d;}finally{clearTimeout(t);}}
  function baseBody(){const x=ids();if(!x.client_id||!x.warehouse_id)throw Error('Не выбран клиент или склад');return{...x,worker:worker(),reference:reference()||null,preferred_receipt_id:(rec?.snapshot?.id&&!s(rec.snapshot.id).startsWith('local:'))?rec.snapshot.id:null};}
  async function fetchCurrent(){return await post('/r145f51/manual-drafts/current',baseBody(),10000);}
  function eventPayload(ev){return ev?.payload||ev||{};}
  function applyScan(ev){if(!rec)return;const p=eventPayload(ev),b=s(p.barcode),loc=s(p.location_code),box=s(p.container_code||p.container_name);if(!b||!loc)return false;let row=(rec.snapshot.rows||[]).find(r=>rowKey(r)===[b,loc,box].join('|'));if(!row){row={id:`local:${uid()}`,barcode:b,location_code:loc,container_code:box||null,container_name:s(p.container_name)||box||null,quantity:0,article:p.article||null,size:p.size||null,name:p.name||null,product_found:p.product_found!==false,location_id:p.location_id||null,container_id:p.container_id||null,section:p.section||null,aisle:p.aisle||null,pallet:p.pallet||null,storage_display:p.storage_display||null,duplicated_from:p.duplicated_from||null,product_id:p.product_id||null};rec.snapshot.rows.push(row);}row.quantity=Math.max(0,Number(row.quantity||0))+Math.max(1,Number(p.quantity||1));for(const k of ['article','size','name','location_id','container_id','container_name','section','aisle','pallet','storage_display','duplicated_from','product_id'])if(p[k]!==undefined&&p[k]!==null&&p[k]!=='')row[k]=p[k];bump();return true;}
  async function legacyQueue(){try{if(typeof FC32Queue==='undefined'||typeof FC32Queue.list!=='function')return null;let k='';try{k=scopeKey();}catch(_){}if(!k)return null;const items=await FC32Queue.list(k);return{k,items:Array.isArray(items)?items:[]};}catch(_){return null;}}
  async function clearLegacyQueue(){try{const l=await legacyQueue();if(!l?.items?.length||typeof FC32Queue.remove!=='function')return;const ids=l.items.map(x=>String(x?.payload?.request_id||x?.id||'')).filter(Boolean);if(ids.length)await FC32Queue.remove(l.k,ids);}catch(_){} }

  async function initRecord(){
    recKey=scopeKey51();if(!recKey||recKey.startsWith('||'))return false;
    rec=await getRec(recKey);
    if(rec?.snapshot){rec.snapshot=normalizeSnap(rec.snapshot);renderLocal(true);return true;}
    let server=null;try{server=(await fetchCurrent())?.snapshot||null;}catch(_){ }
    let current=null;try{current=await globalThis.FC32Receiving?.current?.()||null;}catch(_){ }
    let snap=null;
    if(current?.rows?.length)snap=normalizeSnap(current);
    else if(server)snap=normalizeSnap(server);
    else snap=emptySnap();
    rec={key:recKey,token:uid(),revision:1,server_revision:0,snapshot:snap,created_at:Date.now(),updated_at:Date.now()};
    // Bring forward any unsent legacy scans from before r145f51.
    const legacy=await legacyQueue();if(legacy?.items?.length){for(const ev of legacy.items)applyScan(ev);}
    if(server)mergeServerIds(rec.snapshot,server);
    await save();renderLocal(true);scheduleSync(0);return true;
  }

  function desiredRows(){return (rec?.snapshot?.rows||[]).filter(r=>Number(r.quantity||0)>0).map(r=>({id:s(r.id).startsWith('local:')?null:r.id||null,barcode:s(r.barcode),quantity:Math.max(0,Number(r.quantity||0)),location_code:s(r.location_code),container_code:s(r.container_code||r.container_name)||null,container_name:s(r.container_name)||null,article:r.article||null,size:r.size||null,name:r.name||null,product_found:r.product_found!==false,location_id:r.location_id||null,container_id:r.container_id||null,section:r.section||null,aisle:r.aisle||null,pallet:r.pallet||null,storage_display:r.storage_display||null,duplicated_from:r.duplicated_from||null,product_id:r.product_id||null}));}
  async function syncNow(force=false){
    if(!rec||syncing){syncAgain=true;return false;}const rev=Number(rec.revision)||0;if(!force&&Number(rec.server_revision||0)>=rev)return true;
    syncing=true;showStatus('syncing');
    try{
      const body={...baseBody(),browser_revision:rev,browser_token:rec.token||'browser',rows:desiredRows()};
      const data=await post('/r145f51/manual-drafts/reconcile',body,15000);
      if(data?.target_receipt_id)rec.snapshot.id=String(data.target_receipt_id);
      if(data?.snapshot)mergeServerIds(rec.snapshot,data.snapshot);
      if((Number(rec.revision)||0)===rev)rec.server_revision=rev;
      rec.last_server_ms=Number(data?.server_elapsed_ms||0);await save();await clearLegacyQueue();renderLocal(false);showStatus('synced',rec.last_server_ms?`сервер ${rec.last_server_ms} мс`:'');return true;
    }catch(e){rec.last_error=String(e?.message||e);await save().catch(()=>{});showStatus('error',rec.last_error);return false;}
    finally{syncing=false;if(syncAgain){syncAgain=false;scheduleSync(20);}}
  }
  function scheduleSync(ms=120){clearTimeout(syncTimer);syncTimer=setTimeout(()=>syncNow(false),ms);}

  async function updateQty(id,value){if(!rec)return;let row=(rec.snapshot.rows||[]).find(r=>String(r.id||'')===String(id||''));if(!row)return;row.quantity=Math.max(0,Number(value||0));rec.snapshot.rows=rec.snapshot.rows.filter(r=>Number(r.quantity||0)>0);bump();await save();renderLocal(true);scheduleSync(0);}
  async function clearAll(){if(!rec)return;rec.snapshot.rows=[];bump();await save();renderLocal(true);scheduleSync(0);}

  async function finish(){
    if(!rec)return;finishing=true;showStatus('syncing','завершаем приёмку');
    try{
      const ok=await syncNow(true);if(!ok)throw Error('Сервер не подтвердил текущий черновик. Данные сохранены в браузере.');
      if(Number(rec.server_revision||0)!==Number(rec.revision||0))throw Error('Есть несинхронизированные изменения. Повторите завершение.');
      assignDraft(clone(rec.snapshot));
      const btn=q('#commitManualReceivingDraft');if(!btn)throw Error('Кнопка завершения приёмки не найдена');
      globalThis.__FC51_COMMIT_BYPASS=true;btn.disabled=false;btn.click();globalThis.__FC51_COMMIT_BYPASS=false;
      const rid=s(rec.snapshot.id);const started=Date.now();
      while(Date.now()-started<30000){await sleep(600);try{const d=await post(`/r145f51/manual-drafts/${encodeURIComponent(rid)}/status`,baseBody(),6000);if(!d.exists||String(d.status||'').toUpperCase()!=='DRAFT'){const k=recKey;await delRec(k);rec=null;finishing=false;setTimeout(()=>location.reload(),150);return;}}catch(_){}}
      throw Error('Сервер пока не подтвердил завершение. Приёмка сохранена в браузере; можно повторить кнопку.');
    }catch(e){finishing=false;renderLocal(true);showStatus('error',String(e?.message||e));}
  }

  function installQueueHook(){try{if(typeof FC32Queue==='undefined'||typeof FC32Queue.put!=='function'||FC32Queue.put.__fc51)return false;originalQueuePut=FC32Queue.put.bind(FC32Queue);const wrapped=async function(scope,ev){const p=eventPayload(ev);if(rec&&p&&p.barcode&&p.location_code){applyScan(ev);await save();renderLocal(true);scheduleSync(0);return ev?.id||p.request_id||true;}return await originalQueuePut(scope,ev);};wrapped.__fc51=true;FC32Queue.put=wrapped;return true;}catch(_){return false;}}
  function installEvents(){
    document.addEventListener('change',ev=>{const input=ev.target?.closest?.('[data-manual-draft-qty]');if(input){ev.preventDefault();ev.stopImmediatePropagation();const v=Number(input.value);if(!Number.isInteger(v)||v<0){showStatus('error','Количество должно быть целым числом 0 или больше');return;}updateQty(input.dataset.manualDraftQty,v).catch(e=>showStatus('error',String(e)));return;}if(ev.target?.id==='receiveReference'&&rec){rec.snapshot.reference=reference();bump();save().then(()=>scheduleSync(0));}},true);
    document.addEventListener('click',ev=>{
      const remove=ev.target?.closest?.('[data-manual-draft-remove]');if(remove){ev.preventDefault();ev.stopImmediatePropagation();updateQty(remove.dataset.manualDraftRemove,0).catch(e=>showStatus('error',String(e)));return;}
      const clear=ev.target?.closest?.('button');if(clear&&/Очистить список/i.test(s(clear.textContent))){ev.preventDefault();ev.stopImmediatePropagation();if(confirm('Очистить текущую приёмку в браузере и на сервере?'))clearAll().catch(e=>showStatus('error',String(e)));return;}
      const commit=ev.target?.closest?.('#commitManualReceivingDraft');if(commit&&!globalThis.__FC51_COMMIT_BYPASS){ev.preventDefault();ev.stopImmediatePropagation();commit.disabled=true;finish().finally(()=>{if(commit)commit.disabled=false;});}
    },true);
  }

  async function boot(){
    installRenderOverride();installQueueHook();installEvents();
    let tries=0;const t=setInterval(()=>{installRenderOverride();installQueueHook();if(++tries>80)clearInterval(t);},250);
    await initRecord();booted=true;renderLocal(true);
    // Scope changes (client/warehouse/worker) load their own browser draft without touching another one.
    let lastScope=recKey;setInterval(async()=>{if(finishing)return;const k=scopeKey51();if(k&&k!==lastScope){lastScope=k;rec=null;renderFingerprint='';await initRecord();}},800);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
