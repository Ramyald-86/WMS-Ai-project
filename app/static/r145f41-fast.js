/* FC_R145F32_DESKTOP_LOCAL_FIRST */
(()=>{
'use strict';
if(typeof window.FC32Queue!=='object')return;
let flushBusy=false,flushTimer=0,localSeq=0;
const s=v=>String(v??'').trim();
async function fc39Direct(url,body,timeout=12000){const c=new AbortController(),t=setTimeout(()=>c.abort(),timeout);try{const r=await fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',signal:c.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const raw=await r.text();let d;try{d=JSON.parse(raw);}catch(_){d={detail:raw};}if(!r.ok){const m=typeof d?.detail==='string'?d.detail:(d?.detail?.message||d?.message||raw||('HTTP '+r.status));throw Error(m);}return d;}catch(e){if(e.name==='AbortError')throw Error('Сервер временно не подтвердил очередь');throw e;}finally{clearTimeout(t);}}
/* FC_R145F39_FAST_SERVER_SYNC */
function worker(){return s(document.getElementById('receiveBy')?.value).replace(/\s+/g,' ');}
function scopeKey(){return ['pc',(typeof state!=='undefined'?state.clientId:''),(typeof state!=='undefined'?state.warehouseId:''),worker().toLocaleLowerCase('ru-RU')].join('|');}
let fc41FreshReceipt='',fc41RepairReceipt='';
async function fc41Adopt(snap){if(!snap)return null;try{fc27ServerDraft=snap;fc27ServerDraftContext=typeof fc27DraftContext==='function'?fc27DraftContext():fc27ServerDraftContext;fc27ServerDraftFlight=null;}catch(_){}try{if(typeof fc27CacheServerDraft==='function')await fc27CacheServerDraft(snap);}catch(_){}try{renderManualReceivingDraft();}catch(_){}return snap;}
async function fc41CurrentDraft(force=false){
 const cur=(typeof fc27ServerDraft==='object'&&fc27ServerDraft)?fc27ServerDraft:null,rid=s(cur?.id);
 if(!force&&rid&&String(cur?.status||'DRAFT').toUpperCase()==='DRAFT')return cur;
 const body={client_id:state.clientId,warehouse_id:state.warehouseId,worker:worker(),reference:document.getElementById('receiveReference')?.value?.trim()||null};
 const fresh=await fc39Direct('/r145f37/manual-drafts/normalize-worker',body,12000);await fc41Adopt(fresh);fc41FreshReceipt=s(fresh?.id);return fresh;
}
async function fc41EventsForReceipt(events,rid,scope){
 const current=[],stale=[];
 for(const ev of events){
  if(!ev.receipt_id){ev.receipt_id=rid;try{await FC32Queue.put(scope,ev);}catch(_){} }
  (s(ev.receipt_id)===rid?current:stale).push(ev);
 }
 return {current,stale};
}
async function fc41PendingCount(){
 const scope=scopeKey(),events=await FC32Queue.list(scope),rid=s((typeof fc27ServerDraft==='object'&&fc27ServerDraft)?.id);
 if(!rid)return events.length;return events.filter(e=>!e.receipt_id||s(e.receipt_id)===rid).length;
}
async function fc41ClearCurrent(){
 const scope=scopeKey(),events=await FC32Queue.list(scope),rid=s((typeof fc27ServerDraft==='object'&&fc27ServerDraft)?.id),ids=events.filter(e=>!rid||!e.receipt_id||s(e.receipt_id)===rid).map(e=>e.id);if(ids.length)await FC32Queue.remove(scope,ids);
}
/* FC_R145F41_RECEIPT_BOUND_QUEUE */
function requestId(){return (crypto?.randomUUID?.()||('fc32-'+Date.now()+'-'+(++localSeq)));}
function fastSpeak(text='Есть',bad=false){
 try{window.FC25?.beep?.(bad);}catch(_){}
 try{window.FC25?.speak?.(text,bad);}catch(_){}
}
function quickProduct(raw){
 try{
  if(typeof fc30SkuFast==='function'){
   const f=fc30SkuFast(raw),sku=f.sku;
   if(sku)return {barcode:f.barcode,found:true,article:sku.article||'',size:sku.size||'',name:sku.name||'',product_id:sku.id||null,duplicated_from:f.normalized?.duplicatedFrom||null};
   return {barcode:f.barcode||s(raw),found:false,article:'',size:'',name:'',product_id:null,duplicated_from:f.normalized?.duplicatedFrom||null};
  }
 }catch(_){}
 try{
  const n=normalizeReceivingBarcode(raw),barcode=n.barcode||s(raw),sku=findSku(barcode);
  return sku?{barcode,found:true,article:sku.article||'',size:sku.size||'',name:sku.name||'',product_id:sku.id||null,duplicated_from:n.duplicatedFrom||null}:{barcode,found:false,article:'',size:'',name:'',product_id:null,duplicated_from:n.duplicatedFrom||null};
 }catch(_){return {barcode:s(raw),found:false,article:'',size:'',name:'',product_id:null};}
}
function rowKey(x){return [s(x.barcode),s(x.location_code),s(x.container_code)].join('|');}
function queueStatus(count,bad=false,stale=0){
 const el=document.querySelector('[data-fc30-worker-state]');
 if(el){el.classList.toggle('bad',bad);const tail=stale?` · старые сканы другой приёмки изолированы: ${stale}`:'';el.textContent=count?`Приёмка ${worker()} · локально добавлено · ожидают сервера: ${count}${tail}`:`Приёмка ${worker()} · всё сохранено на сервере${tail}`;}
}
function optimisticAdd(ev){
 const product=ev.product,p=ev.payload;if(ev.receipt_id&&fc27ServerDraft?.id&&s(ev.receipt_id)!==s(fc27ServerDraft.id))return;
 if(typeof fc27ServerDraft==='object'&&fc27ServerDraft){
  const rows=fc27ServerDraft.rows||(fc27ServerDraft.rows=[]),key=rowKey(p);
  let row=rows.find(x=>!x.posted_at&&rowKey(x)===key);
  if(row){row.quantity=Number(row.quantity||0)+Number(p.quantity||1);row.name=row.name||product.name;row.article=row.article||product.article;row.size=row.size||product.size;row._fc32_pending=(row._fc32_pending||0)+Number(p.quantity||1);}
  else{row={id:'fc32-'+ev.id,receipt_id:fc27ServerDraft.id||'',barcode:p.barcode,quantity:Number(p.quantity||1),location_code:p.location_code,container_code:p.container_code,location_id:p.location_id,container_id:p.container_id,container_name:p.container_name,section:p.section,aisle:p.aisle,pallet:p.pallet,storage_display:p.storage_display,article:product.article,size:product.size,name:product.name,product_found:product.found!==false,product_id:product.product_id,_fc32_pending:Number(p.quantity||1)};rows.push(row);}
  fc27ServerDraft.units=rows.filter(x=>!x.posted_at).reduce((n,x)=>n+Number(x.quantity||0),0);
  fc27ServerDraft.positions=rows.filter(x=>!x.posted_at&&Number(x.quantity||0)>0).length;
  const placeTotal=rows.filter(x=>!x.posted_at&&s(x.location_code)===s(p.location_code)&&s(x.container_code)===s(p.container_code)).reduce((n,x)=>n+Number(x.quantity||0),0);
  try{fc30PatchActiveBox({line:row,units:fc27ServerDraft.units,positions:fc27ServerDraft.positions,place_total:placeTotal});}catch(_){try{renderManualReceivingDraft();}catch(__){}}
 }else{
  const result=document.getElementById('receiveResult');if(result)result.innerHTML=`<b>Добавлено локально</b><span>${product.name||('ШК '+p.barcode)} · ${p.quantity} шт. · отправляется на сервер…</span>`;
 }
}
async function pending(){return FC32Queue.list(scopeKey());}
function scheduleFlush(delay=40){clearTimeout(flushTimer);flushTimer=setTimeout(()=>flush().catch(()=>{}),delay);}
async function flush(){
 if(flushBusy||!navigator.onLine)return;flushBusy=true;
 try{
  const scope=scopeKey();let draft=await fc41CurrentDraft(false),rid=s(draft?.id);if(!rid)throw Error('Не удалось определить текущую приёмку.');
  if(fc41RepairReceipt!==rid){
   draft=await fc41CurrentDraft(true);rid=s(draft?.id);if(!rid)throw Error('Сервер не вернул ID текущей приёмки.');
   try{const rep=await fc39Direct(`/r145f41/manual-drafts/${encodeURIComponent(rid)}/repair-cross-receipt`,{client_id:state.clientId,warehouse_id:state.warehouseId,worker:worker()},12000);if(rep?.snapshot)await fc41Adopt(rep.snapshot);if(Number(rep?.removed_events||0)>0){try{toast(`Убраны повторы прошлой приёмки: ${rep.removed_units||rep.removed_events} шт.`);}catch(_){}}}catch(e){const el=document.getElementById('receiveResult');if(el)el.innerHTML=`<b class="bad">Проверка разделения приёмок</b><span>${String(e.message||e)}</span>`;}
   fc41RepairReceipt=rid;
  }
  let events=await FC32Queue.list(scope);const split=await fc41EventsForReceipt(events,rid,scope),now=Date.now();
  const active=split.current.filter(e=>Number(e.retry_after||0)<=now),stale=split.stale;
  queueStatus(split.current.length,false,stale.length);if(!split.current.length)return;if(!active.length){scheduleFlush(1000);return;}
  const batch=active.slice(0,100),payload={client_id:state.clientId,warehouse_id:state.warehouseId,receipt_id:rid,worker:worker(),reference:document.getElementById('receiveReference')?.value?.trim()||null,events:batch.map(x=>({...x.payload,client_created_at:Number(x.created_at||0)||null})),want_snapshot:split.current.length<=100};
  const result=await fc39Direct('/r145f41/manual-drafts/sync-batch-fast',payload,12000);
  const ackIds=(result.acks||[]).map(x=>x.request_id);if(ackIds.length)await FC32Queue.remove(scope,ackIds);
  if((result.failed||[]).length){
   const byId=new Map(batch.map(e=>[String(e.id),e]));for(const f of result.failed){const ev=byId.get(String(f.request_id));if(!ev)continue;ev.fail_count=Number(ev.fail_count||0)+1;ev.retry_after=Date.now()+Math.min(30000,1000*Math.pow(2,Math.min(ev.fail_count,5)));ev.last_error=typeof f.detail==='string'?f.detail:(f.detail?.message||'Ошибка синхронизации');try{await FC32Queue.put(scope,ev);}catch(_){}}
  }
  const leftAll=await FC32Queue.list(scope),leftSplit=await fc41EventsForReceipt(leftAll,rid,scope),left=leftSplit.current;
  if(result.snapshot&&!left.length)await fc41Adopt(result.snapshot);
  queueStatus(left.length,Boolean((result.failed||[]).length),leftSplit.stale.length);
  if(Number(result.ignored_previous_receipt||0)>0){const el=document.getElementById('receiveResult');if(el)el.innerHTML=`<b>Старые повторы не перенесены</b><span>${result.ignored_previous_receipt} скан(ов) уже принадлежали прошлой приёмке и не добавлены повторно.</span>`;}
  else if((result.failed||[]).length){const f=result.failed[0],m=typeof f.detail==='string'?f.detail:(f.detail?.message||'Ошибка синхронизации');const el=document.getElementById('receiveResult');if(el)el.innerHTML=`<b class="bad">Есть неподтверждённый скан</b><span>${String(m)} · остальные сканы продолжают отправляться.</span>`;}
  if(left.length)scheduleFlush((result.failed||[]).length?1000:20);
 }catch(e){const n=await fc41PendingCount();queueStatus(n,true,0);const el=document.getElementById('receiveResult');if(el)el.innerHTML=`<b>Работа продолжается локально</b><span>${String(e.message||e)} · повтор через секунду.</span>`;scheduleFlush(1000);}
 finally{flushBusy=false;}
}
async function localFirstAdd(scan){
 if(scan.clientId&&scan.clientId!==state.clientId||scan.warehouseId&&scan.warehouseId!==state.warehouseId)throw Error('Клиент или склад изменён. Повторите сканирование.');
 if(!state.clientId||!state.warehouseId)throw Error('Сначала выберите клиента и склад');if(!worker())throw Error('Укажите имя приёмщика');
 const draft=await fc41CurrentDraft(false),receiptId=s(draft?.id);if(!receiptId)throw Error('Не удалось открыть текущую приёмку.');
 const quantity=Number(scan.quantity||1);if(!Number.isInteger(quantity)||quantity<1)throw Error('Количество должно быть целым и больше нуля');
 const raw=s(scan.rawBarcode);let product=quickProduct(raw);
 if(product.found===false){
  const code=product.barcode||raw;
  if(!window.FC26Cards)throw Error('Форма карточки товара ещё не загружена. Обновите страницу.');
  const exact=await FC26Cards.lookup(code);
  if(exact?.found&&exact?.sku_id&&!exact?.archived&&!exact?.hidden){
   product={barcode:exact.barcode||code,found:true,article:exact.article||'',size:exact.size||'',name:exact.name||'',product_id:exact.sku_id||exact.product_id||null,duplicated_from:product.duplicated_from||null};
  }else{
   const hint=document.getElementById('receiveSkuHint');if(hint){hint.textContent='Новый ШК '+code+' — заполните карточку товара';hint.className='bad';}
   const saved=await FC26Cards.edit({...exact,barcode:exact?.barcode||code,article:exact?.article||'',size:exact?.size||'',name:exact?.name||'',category:exact?.category||''});
   product={barcode:saved.barcode||code,found:true,article:saved.article||'',size:saved.size||'',name:saved.name||'',product_id:saved.sku_id||saved.product_id||null,duplicated_from:product.duplicated_from||null};
   if(hint){hint.textContent=[product.article,product.name,product.size].filter(Boolean).join(' · ');hint.className='good';}
   try{toast('Карточка создана. Товар добавляется в текущий короб.');}catch(_){}
  }
 }
 const barcode=product.barcode||raw; /* FC_R145F40_UNKNOWN_CARD_RECEIVING */
 const loc=findLocation(scan.locationCode);if(!loc)throw Error('Ячейка не найдена. Выберите место заново.');assertLocationAvailableForClient(loc);
 const ctx=scan.storageContext||manualReceivingStorageContext||null,containerCode=s(scan.containerCode||ctx?.container_barcode)||null;
 const id=requestId(),payload={request_id:id,barcode,quantity,location_code:loc.code,container_code:containerCode,article:product.article||null,size:product.size||null,name:product.name||null,product_found:product.found!==false,location_id:loc.id,container_id:ctx?.container_id||null,container_name:ctx?.container_name||containerCode||null,section:ctx?.section||'',aisle:ctx?.aisle||'',pallet:ctx?.pallet||'',storage_display:ctx?manualReceivingStructureSummary(ctx):loc.name||loc.code,duplicated_from:product.duplicated_from||null,product_id:product.product_id||null};
 const event={id,created_at:Date.now(),receipt_id:receiptId,payload,product};
 // r145f37: a positive UI/sound confirmation is emitted only AFTER the scan
 // is durably written to IndexedDB.  Therefore "Есть" means the scan cannot
 // disappear merely because the network request is still in flight.
 try{await FC32Queue.put(scopeKey(),event);}catch(e){queueStatus(1,true);throw Error('Скан прочитан, но локальная очередь не сохранилась. Повторите скан: '+e.message);}
 optimisticAdd(event);fastSpeak('Есть');
 const out=document.getElementById('receiveResult');if(out)out.innerHTML=`<b>Добавлено сразу</b><span>${product.name||('ШК '+barcode)} · ${quantity} шт. · сохранено локально, синхронизация в фоне</span>`;
 const n=await FC32Queue.count(scopeKey());queueStatus(n);scheduleFlush(0);
 return true;
}
function localBox(value){
 const q=s(value).toLocaleLowerCase('ru-RU');if(!q)return null;const list=(state.containers||[]).filter(c=>String(c.client_id)===String(state.clientId)&&String(c.warehouse_id)===String(state.warehouseId)&&String(c.status||'ACTIVE')==='ACTIVE');
 const byId=list.filter(c=>String(c.id).toLowerCase()===q),byBarcode=list.filter(c=>s(c.barcode).toLocaleLowerCase('ru-RU')===q),byName=list.filter(c=>s(c.name).toLocaleLowerCase('ru-RU')===q);const rows=byId.length?byId:byBarcode.length?byBarcode:byName;return rows.sort((a,b)=>(!a.location_id)-(!b.location_id)||String(a.id).localeCompare(String(b.id)))[0]||null;
}
function ctxFromBox(c){const loc=(state.locations||[]).find(x=>String(x.id)===String(c.location_id));if(!loc)return null;return {container_id:c.id,container_name:c.name||c.barcode,container_barcode:c.barcode||c.name,location_id:loc.id,location_code:loc.code,location_display:loc.display_name||loc.name||loc.code,section:loc.section||'',aisle:loc.aisle||'',pallet:loc.pallet||'',row_on_pallet:loc.row_on_pallet||null,created:false};}
function install(){
 if(typeof addManualReceivingDraftLine==='function')addManualReceivingDraftLine=localFirstAdd;
 if(typeof scheduleManualReceivingScan==='function')scheduleManualReceivingScan=function(){clearTimeout(manualReceivingScanTimer);manualReceivingScanTimer=0;if(!s(document.getElementById('receiveSku')?.value))return;manualReceivingScanTimer=setTimeout(()=>captureManualReceivingScan(false),35);};
 if(typeof activateManualReceivingBox==='function'&&!activateManualReceivingBox._fc32){const original=activateManualReceivingBox;const wrapped=async function(mode='SCAN',forceMove=false){const value=s(document.getElementById('manualReceiveBox')?.value),c=mode==='SCAN'?localBox(value):null,ctx=c?ctxFromBox(c):null;if(!ctx)return original(mode,forceMove);manualReceivingStorageContext=ctx;if(document.getElementById('receiveLocation'))document.getElementById('receiveLocation').value=ctx.location_code||'';if(document.getElementById('receiveContainer'))document.getElementById('receiveContainer').value=ctx.container_barcode||ctx.container_name||'';renderManualReceivingStorageContext();document.getElementById('receiveSku')?.focus();fastSpeak('Короб');toast('Короб открыт локально: '+ctx.container_name);const body={client_id:state.clientId,warehouse_id:state.warehouseId,section:s(document.getElementById('manualReceiveSection')?.value)||null,aisle:s(document.getElementById('manualReceiveAisle')?.value)||null,pallet:s(document.getElementById('manualReceivePallet')?.value)||null,row_on_pallet:document.getElementById('manualReceiveRow')?.value?Number(document.getElementById('manualReceiveRow').value):null,box_value:value,box_mode:mode,force_move:false};fc39Direct('/r145f25/receiving/box',body,12000).then(r=>{manualReceivingStorageContext=r;if(document.getElementById('receiveLocation'))document.getElementById('receiveLocation').value=r.location_code||'';if(document.getElementById('receiveContainer'))document.getElementById('receiveContainer').value=r.container_barcode||r.container_name||'';renderManualReceivingStorageContext();}).catch(e=>{toast('Фоновая проверка короба: '+e.message,true);});return ctx;};wrapped._fc32=true;activateManualReceivingBox=wrapped;}
 if(typeof fc26CommitManual==='function'&&!fc26CommitManual._fc32){const orig=fc26CommitManual;const wrapped=async function(...args){await flush();const n=await fc41PendingCount();if(n)throw Error(`Ещё ${n} скан(ов) текущей приёмки ожидают подтверждения сервера. Дождитесь синхронизации.`);return orig(...args);};wrapped._fc32=true;fc26CommitManual=wrapped;}
 const oldResume=typeof fc30ResumeWorkerDraft==='function'?fc30ResumeWorkerDraft:null;if(oldResume&&!oldResume._fc32){const w=async function(...a){const r=await oldResume(...a);scheduleFlush(0);return r;};w._fc32=true;fc30ResumeWorkerDraft=w;}
 window.addEventListener('online',()=>scheduleFlush(0));document.addEventListener('visibilitychange',()=>{if(!document.hidden)scheduleFlush(0);});setTimeout(()=>scheduleFlush(0),300);
}
window.FC32Receiving={flush,pending:fc41PendingCount,clear:fc41ClearCurrent,scopeKey,version:41};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
