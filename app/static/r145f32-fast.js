/* FC_R145F32_DESKTOP_LOCAL_FIRST */
(()=>{
'use strict';
if(typeof window.FC32Queue!=='object')return;
let flushBusy=false,flushTimer=0,localSeq=0;
const s=v=>String(v??'').trim();
function worker(){return s(document.getElementById('receiveBy')?.value).replace(/\s+/g,' ');}
function scopeKey(){return ['pc',(typeof state!=='undefined'?state.clientId:''),(typeof state!=='undefined'?state.warehouseId:''),worker().toLocaleLowerCase('ru-RU')].join('|');}
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
function queueStatus(count,bad=false){
 const el=document.querySelector('[data-fc30-worker-state]');
 if(el){el.classList.toggle('bad',bad);el.textContent=count?`Приёмка ${worker()} · локально добавлено · ожидают сервера: ${count}`:`Приёмка ${worker()} · всё сохранено на сервере`;}
}
function optimisticAdd(ev){
 const product=ev.product,p=ev.payload;
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
 if(flushBusy||!navigator.onLine)return;const scope=scopeKey(),events=await FC32Queue.list(scope);queueStatus(events.length);if(!events.length)return;
 flushBusy=true;
 try{
  const batch=events.slice(0,25),payload={client_id:state.clientId,warehouse_id:state.warehouseId,worker:worker(),reference:document.getElementById('receiveReference')?.value?.trim()||null,events:batch.map(x=>x.payload)};
  const result=await api('/r145f32/manual-drafts/sync-batch',{method:'POST',body:JSON.stringify(payload),timeoutMs:20000});
  const ackIds=(result.acks||[]).map(x=>x.request_id);if(ackIds.length)await FC32Queue.remove(scope,ackIds);
  if(result.snapshot&&typeof fc27ServerDraft==='object'&&fc27ServerDraft){
   // Keep current optimistic quantities on screen; refresh the authoritative snapshot
   // only after the local queue is empty, preventing full-list flicker per scan.
   const left=await FC32Queue.list(scope);if(!left.length){fc27ServerDraft=result.snapshot;fc27ServerDraftContext=typeof fc27DraftContext==='function'?fc27DraftContext():fc27ServerDraftContext;try{fc30ScheduleDraftCache?.();}catch(_){} }
  }
  const left=await FC32Queue.list(scope);queueStatus(left.length,Boolean((result.failed||[]).length));
  if((result.failed||[]).length){const f=result.failed[0];const m=typeof f.detail==='string'?f.detail:(f.detail?.message||'Ошибка синхронизации');const el=document.getElementById('receiveResult');if(el)el.innerHTML=`<b class="bad">Есть неподтверждённый скан</b><span>${String(m)}</span>`;return;}
  if(left.length)scheduleFlush(20);
 }catch(e){queueStatus((await FC32Queue.count(scope)),true);const el=document.getElementById('receiveResult');if(el)el.innerHTML='<b>Работа продолжается локально</b><span>Сервер временно не подтвердил очередь. Сканировать можно дальше; синхронизация повторится автоматически.</span>';}
 finally{flushBusy=false;}
}
async function localFirstAdd(scan){
 if(scan.clientId&&scan.clientId!==state.clientId||scan.warehouseId&&scan.warehouseId!==state.warehouseId)throw Error('Клиент или склад изменён. Повторите сканирование.');
 if(!state.clientId||!state.warehouseId)throw Error('Сначала выберите клиента и склад');if(!worker())throw Error('Укажите имя приёмщика');
 const quantity=Number(scan.quantity||1);if(!Number.isInteger(quantity)||quantity<1)throw Error('Количество должно быть целым и больше нуля');
 const raw=s(scan.rawBarcode),product=quickProduct(raw),barcode=product.barcode||raw;
 const loc=findLocation(scan.locationCode);if(!loc)throw Error('Ячейка не найдена. Выберите место заново.');assertLocationAvailableForClient(loc);
 const ctx=scan.storageContext||manualReceivingStorageContext||null,containerCode=s(scan.containerCode||ctx?.container_barcode)||null;
 const id=requestId(),payload={request_id:id,barcode,quantity,location_code:loc.code,container_code:containerCode,article:product.article||null,size:product.size||null,name:product.name||null,product_found:product.found!==false,location_id:loc.id,container_id:ctx?.container_id||null,container_name:ctx?.container_name||containerCode||null,section:ctx?.section||'',aisle:ctx?.aisle||'',pallet:ctx?.pallet||'',storage_display:ctx?manualReceivingStructureSummary(ctx):loc.name||loc.code,duplicated_from:product.duplicated_from||null,product_id:product.product_id||null};
 const event={id,created_at:Date.now(),payload,product};
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
 if(typeof activateManualReceivingBox==='function'&&!activateManualReceivingBox._fc32){const original=activateManualReceivingBox;const wrapped=async function(mode='SCAN',forceMove=false){const value=s(document.getElementById('manualReceiveBox')?.value),c=mode==='SCAN'?localBox(value):null,ctx=c?ctxFromBox(c):null;if(!ctx)return original(mode,forceMove);manualReceivingStorageContext=ctx;if(document.getElementById('receiveLocation'))document.getElementById('receiveLocation').value=ctx.location_code||'';if(document.getElementById('receiveContainer'))document.getElementById('receiveContainer').value=ctx.container_barcode||ctx.container_name||'';renderManualReceivingStorageContext();document.getElementById('receiveSku')?.focus();fastSpeak('Короб');toast('Короб открыт локально: '+ctx.container_name);const body={client_id:state.clientId,warehouse_id:state.warehouseId,section:s(document.getElementById('manualReceiveSection')?.value)||null,aisle:s(document.getElementById('manualReceiveAisle')?.value)||null,pallet:s(document.getElementById('manualReceivePallet')?.value)||null,row_on_pallet:document.getElementById('manualReceiveRow')?.value?Number(document.getElementById('manualReceiveRow').value):null,box_value:value,box_mode:mode,force_move:false};api('/r145f25/receiving/box',{method:'POST',body:JSON.stringify(body),timeoutMs:20000}).then(r=>{manualReceivingStorageContext=r;if(document.getElementById('receiveLocation'))document.getElementById('receiveLocation').value=r.location_code||'';if(document.getElementById('receiveContainer'))document.getElementById('receiveContainer').value=r.container_barcode||r.container_name||'';renderManualReceivingStorageContext();}).catch(e=>{toast('Фоновая проверка короба: '+e.message,true);});return ctx;};wrapped._fc32=true;activateManualReceivingBox=wrapped;}
 if(typeof fc26CommitManual==='function'&&!fc26CommitManual._fc32){const orig=fc26CommitManual;const wrapped=async function(...args){await flush();const n=await FC32Queue.count(scopeKey());if(n)throw Error(`Ещё ${n} скан(ов) ожидают подтверждения сервера. Дождитесь синхронизации.`);return orig(...args);};wrapped._fc32=true;fc26CommitManual=wrapped;}
 const oldResume=typeof fc30ResumeWorkerDraft==='function'?fc30ResumeWorkerDraft:null;if(oldResume&&!oldResume._fc32){const w=async function(...a){const r=await oldResume(...a);scheduleFlush(0);return r;};w._fc32=true;fc30ResumeWorkerDraft=w;}
 window.addEventListener('online',()=>scheduleFlush(0));document.addEventListener('visibilitychange',()=>{if(!document.hidden)scheduleFlush(0);});setTimeout(()=>scheduleFlush(0),300);
}
window.FC32Receiving={flush,pending:()=>FC32Queue.count(scopeKey()),clear:()=>FC32Queue.clear(scopeKey()),scopeKey,version:37};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
