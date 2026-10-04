/* FC_R145F50_RECEIVING_STABLE_WORKING */
/* FC_R145F32_DESKTOP_LOCAL_FIRST */
(()=>{
'use strict';
if(typeof window.FC32Queue!=='object')return;
let flushBusy=false,flushTimer=0,localSeq=0,fc44BoxActivationSeq=0;
/* FC_R145F44_ULTRA_SYNC_STRICT_BOX */
/* FC_R145F45_INSTANT_DRAFT_HOT_SYNC: no normalize/full-snapshot gate in scanner hot path. */
/* FC_R145F46_RECEIVING_RESCUE_ZERO_WAIT: latest local queue renders first; tiny receipt resolver; proven r145f41 batch persistence. */
const s=v=>String(v??'').trim();
async function fc39Direct(url,body,timeout=12000){const c=new AbortController(),t=setTimeout(()=>c.abort(),timeout);try{const r=await fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',signal:c.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const raw=await r.text();let d;try{d=JSON.parse(raw);}catch(_){d={detail:raw};}if(!r.ok){const m=typeof d?.detail==='string'?d.detail:(d?.detail?.message||d?.message||raw||('HTTP '+r.status));throw Error(m);}return d;}catch(e){if(e.name==='AbortError')throw Error('Сервер временно не подтвердил очередь');throw e;}finally{clearTimeout(t);}}
/* FC_R145F39_FAST_SERVER_SYNC */
function worker(){return s(document.getElementById('receiveBy')?.value).replace(/\s+/g,' ');}
function scopeKey(){return ['pc',(typeof state!=='undefined'?state.clientId:''),(typeof state!=='undefined'?state.warehouseId:''),worker().toLocaleLowerCase('ru-RU')].join('|');}
let fc42PreparedReceipt='',fc42FlushPromise=null,fc42OverlapInfo=null;
function fc42TodayStart(){const d=new Date();d.setHours(0,0,0,0);return d.getTime();}
async function fc42Adopt(snap){if(!snap)return null;try{fc27ServerDraft=snap;fc27ServerDraftContext=typeof fc27DraftContext==='function'?fc27DraftContext():fc27ServerDraftContext;fc27ServerDraftFlight=null;}catch(_){}try{if(typeof fc27CacheServerDraft==='function')await fc27CacheServerDraft(snap);}catch(_){}try{renderManualReceivingDraft();}catch(_){}return snap;}
let fc45StateFlight=null;
function fc45CloneSnapshot(snap){const s0=snap&&typeof snap==='object'?snap:{};return {...s0,rows:Array.isArray(s0.rows)?s0.rows.map(x=>({...x})):[]};}
function fc45QueueReceipt(events){const bound=(events||[]).filter(e=>e&&e.receipt_id).slice().sort((a,b)=>Number(b.created_at||0)-Number(a.created_at||0));return s(bound[0]?.receipt_id);}
function fc45PendingRow(ev){const p=ev?.payload||{},product=ev?.product||{};return {id:'fc45-'+String(ev?.id||p.request_id||''),receipt_id:String(ev?.receipt_id||''),barcode:p.barcode||'',quantity:Number(p.quantity||1),location_code:p.location_code||'',container_code:p.container_code||null,location_id:p.location_id||null,container_id:p.container_id||null,container_name:p.container_name||p.container_code||null,section:p.section||'',aisle:p.aisle||'',pallet:p.pallet||'',storage_display:p.storage_display||'',article:p.article||product.article||'',size:p.size||product.size||'',name:p.name||product.name||'',product_found:p.product_found!==false,product_id:p.product_id||product.product_id||null,_fc45_pending:Number(p.quantity||1)};}
function fc45MergeSnapshot(snap,events,rid){const out=fc45CloneSnapshot(snap);out.id=out.id||rid||'';out.status=out.status||'DRAFT';out.worker=out.worker||worker();const rows=out.rows||(out.rows=[]);for(const ev of (events||[])){if(ev.receipt_id&&rid&&s(ev.receipt_id)!==s(rid))continue;const p=ev.payload||{},key=rowKey(p);let row=rows.find(x=>!x.posted_at&&rowKey(x)===key);if(row){row.quantity=Number(row.quantity||0)+Number(p.quantity||1);row._fc45_pending=Number(row._fc45_pending||0)+Number(p.quantity||1);row.article=row.article||p.article||ev.product?.article||'';row.size=row.size||p.size||ev.product?.size||'';row.name=row.name||p.name||ev.product?.name||'';}else rows.push(fc45PendingRow(ev));}const active=rows.filter(x=>!x.posted_at&&Number(x.quantity||0)>0);out.units=active.reduce((n,x)=>n+Number(x.quantity||0),0);out.positions=active.length;out.local_overlay=true;return out;}
async function fc45SetDraft(authoritative,events=[]){const rid=s(authoritative?.id);const merged=fc45MergeSnapshot(authoritative,events,rid);try{fc27ServerDraft=merged;fc27ServerDraftContext=typeof fc27DraftContext==='function'?fc27DraftContext():fc27ServerDraftContext;fc27ServerDraftFlight=null;}catch(_){}try{if(typeof fc27CacheServerDraft==='function'&&authoritative?.id)await fc27CacheServerDraft(authoritative);}catch(_){}try{renderManualReceivingDraft();}catch(_){}return merged;}
function fc46QueueGroup(events){
 const all=(events||[]).slice().sort((a,b)=>Number(a.created_at||0)-Number(b.created_at||0)),start=fc42TodayStart(),today=all.filter(e=>Number(e.created_at||0)>=start),bound=today.filter(e=>s(e.receipt_id));
 const rid=bound.length?s(bound[bound.length-1].receipt_id):'';let pivot=start;
 if(rid){const xs=today.filter(e=>s(e.receipt_id)===rid).map(e=>Number(e.created_at||0)).filter(Boolean);if(xs.length)pivot=Math.min(...xs);}
 const current=today.filter(e=>rid?(s(e.receipt_id)===rid||(!e.receipt_id&&Number(e.created_at||0)>=pivot)):!e.receipt_id),ids=new Set(current.map(e=>String(e.id))),stale=all.filter(e=>!ids.has(String(e.id)));
 return {rid,current,stale};
}
function fc46SetLocalDraft(group,idOverride=''){
 if(!group?.current?.length)return null;const rid=s(idOverride||group.rid),base={id:rid,client_id:state.clientId,warehouse_id:state.warehouseId,worker:worker(),status:'DRAFT',rows:[],units:0,positions:0,_fc46_local_only:true};
 const merged=fc45MergeSnapshot(base,group.current,rid||'');try{fc27ServerDraft=merged;fc27ServerDraftContext=typeof fc27DraftContext==='function'?fc27DraftContext():fc27ServerDraftContext;fc27ServerDraftFlight=null;}catch(_){}try{renderManualReceivingDraft();}catch(_){}queueStatus(group.current.length,false,group.stale.length);return merged;
}
async function fc46HydrateFromQueue(){const scope=scopeKey(),events=await FC32Queue.list(scope),group=fc46QueueGroup(events);if(!group.current.length){if(events.length)queueStatus(0,false,group.stale.length);return null;}return fc46SetLocalDraft(group);}
async function fc46EnsureReceiptId(){
 const scope=scopeKey(),events=await FC32Queue.list(scope),group=fc46QueueGroup(events),cur=(typeof fc27ServerDraft==='object'&&fc27ServerDraft)?fc27ServerDraft:null,preferred=group.rid||((cur&&!cur._fc46_local_only)?s(cur.id):'')||null;
 const data=await fc39Direct('/r145f46/manual-drafts/ensure-fast',{client_id:state.clientId,warehouse_id:state.warehouseId,worker:worker(),reference:document.getElementById('receiveReference')?.value?.trim()||null,preferred_receipt_id:preferred},5000),target=s(data.receipt_id);if(!target)throw Error('Сервер не вернул номер текущей приёмки.');
 if(group.current.length){for(const ev of group.current){if(s(ev.receipt_id)!==target){ev.receipt_id=target;try{await FC32Queue.put(scope,ev);}catch(_){}}}const rebound=fc46QueueGroup(await FC32Queue.list(scope));fc46SetLocalDraft(rebound,target);}else if(cur&&cur._fc46_local_only){cur.id=target;cur._fc46_local_only=false;}
 return target;
}
async function fc46FetchRows(rid){const data=await fc39Direct('/r145f46/manual-drafts/rows-fast',{client_id:state.clientId,warehouse_id:state.warehouseId,worker:worker(),reference:document.getElementById('receiveReference')?.value?.trim()||null,preferred_receipt_id:rid,receipt_id:rid},6000);return data?.snapshot||null;}
async function fc45HydrateFromQueue(){return fc46HydrateFromQueue();}
async function fc45LoadState(force=false){
 const local=await fc46HydrateFromQueue(),scope=scopeKey(),events=await FC32Queue.list(scope),group=fc46QueueGroup(events);
 if(group.current.length){try{await fc46EnsureReceiptId();}catch(e){queueStatus(group.current.length,true,group.stale.length);}return fc27ServerDraft||local;}
 const cur=(typeof fc27ServerDraft==='object'&&fc27ServerDraft)?fc27ServerDraft:null;if(!force&&cur&&s(cur.id)&&!cur._fc46_local_only&&String(cur.status||'DRAFT').toUpperCase()==='DRAFT')return cur;
 const rid=await fc46EnsureReceiptId(),snap=await fc46FetchRows(rid);if(snap)return fc45SetDraft(snap,[]);return cur;
}
async function fc42CurrentDraft(force=false){return fc45LoadState(force);}
async function fc45ReceiptId(){return fc46EnsureReceiptId();}
async function fc42RebindToday(scope,oldRid,newRid){
 const events=await FC32Queue.list(scope),start=fc42TodayStart();
 for(const ev of events){if(Number(ev.created_at||0)<start)continue;if(!ev.receipt_id||s(ev.receipt_id)===s(oldRid)){ev.receipt_id=newRid;try{await FC32Queue.put(scope,ev);}catch(_){}}}
}
function fc42RepairBox(info,rid){
 const panel=document.getElementById('manualReceivingDraftPanel');if(!panel)return;let box=document.getElementById('fc42ReceiptRepair');
 if(!info?.candidate){if(box)box.remove();return;}
 if(!box){box=document.createElement('div');box.id='fc42ReceiptRepair';box.className='result-box';panel.insertAdjacentElement('afterbegin',box);}
 const units=Number(info.overlap_units||0),pos=Number(info.overlap_positions||0);box.innerHTML=`<b>Найдена часть уже завершённой приёмки</b><span>В текущем черновике совпадает ${units} шт. / ${pos} поз. с предыдущей завершённой приёмкой. Это можно убрать из текущего черновика без повторного движения остатков.</span><div class="button-row"><button type="button" class="secondary" id="fc42SplitPreviousReceipt">Отделить завершённую приёмку</button></div>`;
 const b=box.querySelector('#fc42SplitPreviousReceipt');if(b)b.onclick=async()=>{if(!confirm(`Убрать из текущего черновика ${units} шт., которые уже есть в предыдущей завершённой приёмке?

Остатки повторно не меняются. В текущей приёмке останется только новая часть.`))return;b.disabled=true;b.textContent='Разделяем…';try{const out=await fc39Direct(`/r145f42/manual-drafts/${encodeURIComponent(rid)}/split-overlap`,{client_id:state.clientId,warehouse_id:state.warehouseId,worker:worker(),previous_receipt_id:info.previous_receipt_id},30000);if(out?.snapshot)await fc42Adopt(out.snapshot);box.remove();fc42OverlapInfo=null;try{toast(`Старая часть убрана: ${out.removed_units||units} шт. Текущая приёмка отделена.`);}catch(_){}}catch(e){b.disabled=false;b.textContent='Отделить завершённую приёмку';const out=document.getElementById('receiveResult');if(out)out.innerHTML=`<b class="bad">Не удалось разделить приёмки</b><span>${String(e.message||e)}</span>`;}};
}
async function fc42Prepare(draft){
 let rid=s(draft?.id);if(!rid)return draft;if(fc42PreparedReceipt===rid)return draft;
 const scope=scopeKey(),events=await FC32Queue.list(scope),start=fc42TodayStart(),todayLocal=events.filter(e=>Number(e.created_at||0)>=start&&(!e.receipt_id||s(e.receipt_id)===rid)).length;
 const rep=await fc39Direct(`/r145f42/manual-drafts/${encodeURIComponent(rid)}/prepare-current`,{client_id:state.clientId,warehouse_id:state.warehouseId,receipt_id:rid,worker:worker(),reference:document.getElementById('receiveReference')?.value?.trim()||null,today_local_count:todayLocal},30000);
 if(rep?.split&&rep?.snapshot){const old=rid,newRid=s(rep.snapshot.id);await fc42RebindToday(scope,old,newRid);draft=await fc42Adopt(rep.snapshot);rid=newRid;const sel=document.getElementById('receiveStockClass');if(sel)sel.value='MAIN';try{toast(`Приёмки разделены: в сегодняшнюю перенесено ${rep.moved_units||0} шт.`);}catch(_){}}
 else if(rep?.snapshot){draft=await fc42Adopt(rep.snapshot);rid=s(draft?.id)||rid;}
 if(Number(rep?.exact_removed_units||0)>0){try{toast(`Убраны точные повторы прошлой приёмки: ${rep.exact_removed_units} шт.`);}catch(_){}}
 fc42OverlapInfo=rep?.overlap||null;fc42RepairBox(fc42OverlapInfo,rid);fc42PreparedReceipt=rid;return draft;
}
async function fc42EventsForReceipt(events,rid,scope){
 const current=[],stale=[],start=fc42TodayStart();
 for(const ev of events){
  if(!ev.receipt_id&&Number(ev.created_at||0)>=start){ev.receipt_id=rid;try{await FC32Queue.put(scope,ev);}catch(_){}}
  (s(ev.receipt_id)===rid?current:stale).push(ev);
 }
 return {current,stale};
}
async function fc42PendingCount(){
 const scope=scopeKey(),events=await FC32Queue.list(scope),rid=s((typeof fc27ServerDraft==='object'&&fc27ServerDraft)?.id),start=fc42TodayStart();
 if(!rid)return events.filter(e=>Number(e.created_at||0)>=start).length;return events.filter(e=>s(e.receipt_id)===rid||(!e.receipt_id&&Number(e.created_at||0)>=start)).length;
}
async function fc42ClearCurrent(){
 const scope=scopeKey(),events=await FC32Queue.list(scope),rid=s((typeof fc27ServerDraft==='object'&&fc27ServerDraft)?.id),start=fc42TodayStart(),ids=events.filter(e=>s(e.receipt_id)===rid||(!e.receipt_id&&Number(e.created_at||0)>=start)).map(e=>e.id);if(ids.length)await FC32Queue.remove(scope,ids);
}
/* FC_R145F42_EXACT_SESSION_QUEUE */
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
 if(el){el.classList.toggle('bad',bad);const tail=stale?` · старые сканы изолированы: ${stale}`:'';el.textContent=count?`Приёмка ${worker()} · локально добавлено · ожидают сервера: ${count}${tail}`:`Приёмка ${worker()} · всё сохранено на сервере${tail}`;}
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
async function fc42FlushCore(){
 if(flushBusy||!navigator.onLine)return;flushBusy=true;
 try{
  const scope=scopeKey();let events=await FC32Queue.list(scope),group=fc46QueueGroup(events);if(!group.current.length){queueStatus(0,false,group.stale.length);return;}
  const cur=(typeof fc27ServerDraft==='object'&&fc27ServerDraft)?fc27ServerDraft:null,preferred=group.rid||((cur&&!cur._fc46_local_only)?s(cur.id):'')||null;
  const batch=group.current.slice(0,500),result=await fc39Direct('/r145f50/manual-drafts/sync',{client_id:state.clientId,warehouse_id:state.warehouseId,worker:worker(),reference:document.getElementById('receiveReference')?.value?.trim()||null,preferred_receipt_id:preferred,events:batch.map(x=>({...x.payload,request_id:String(x.payload?.request_id||x.id||''),client_created_at:Number(x.created_at||0)||null}))},10000);
  const target=s(result.target_receipt_id||preferred);if(target&&cur&&s(cur.id)!==target){try{cur.id=target;cur.status='DRAFT';cur._fc46_local_only=false;fc27ServerDraft=cur;}catch(_){}}const ackIds=(result.acks||[]).map(x=>String(x.request_id||'')).filter(Boolean);if(ackIds.length)await FC32Queue.remove(scope,ackIds);
  const leftAll=await FC32Queue.list(scope),leftGroup=fc46QueueGroup(leftAll);if(target){for(const ev of leftGroup.current){if(s(ev.receipt_id)!==target){ev.receipt_id=target;ev.retry_after=0;ev.fail_count=0;try{await FC32Queue.put(scope,ev);}catch(_){}}}}
  const finalGroup=fc46QueueGroup(await FC32Queue.list(scope));queueStatus(finalGroup.current.length,Boolean((result.failed||[]).length),finalGroup.stale.length);
  if(Number(result.ignored_previous_receipt||0)>0){const el=document.getElementById('receiveResult');if(el)el.innerHTML=`<b>Старые повторы не добавлены</b><span>${result.ignored_previous_receipt} скан(ов) уже сохранены в другой приёмке.</span>`;}
  else if((result.failed||[]).length){const f=result.failed[0],m=typeof f.detail==='string'?f.detail:(f.detail?.message||'Ошибка синхронизации');const el=document.getElementById('receiveResult');if(el)el.innerHTML=`<b class="bad">Не сохранён один скан</b><span>${String(m)}</span>`;}
  else if(!finalGroup.current.length){const el=document.getElementById('receiveResult');if(el)el.innerHTML='<b>Всё сохранено на сервере</b><span>Можно редактировать или завершать приёмку.</span>';}
  if(finalGroup.current.length)scheduleFlush((result.failed||[]).length?900:5);
 }catch(e){const scope=scopeKey(),group=fc46QueueGroup(await FC32Queue.list(scope));queueStatus(group.current.length,true,group.stale.length);const el=document.getElementById('receiveResult');if(el)el.innerHTML=`<b class="bad">Сервер временно не подтвердил очередь</b><span>${String(e.message||e)} · данные остаются локально, повторяем автоматически.</span>`;scheduleFlush(900);}
 finally{flushBusy=false;}
}
async function fc50Snapshot(){
 const scope=scopeKey(),events=await FC32Queue.list(scope),group=fc46QueueGroup(events),cur=(typeof fc27ServerDraft==='object'&&fc27ServerDraft)?fc27ServerDraft:null,rid=s(group.rid||cur?.id);if(!rid)return cur||null;
 const data=await fc39Direct(`/r145f50/manual-drafts/${encodeURIComponent(rid)}/snapshot`,{client_id:state.clientId,warehouse_id:state.warehouseId,worker:worker(),reference:document.getElementById('receiveReference')?.value?.trim()||null,preferred_receipt_id:rid},8000);return data?.snapshot||null;
}
async function fc50Adopt(snap,render=true){if(!snap)return null;try{fc27ServerDraft=snap;fc27ServerDraftContext=typeof fc27DraftContext==='function'?fc27DraftContext():fc27ServerDraftContext;fc27ServerDraftFlight=null;}catch(_){}try{if(typeof fc27CacheServerDraft==='function')await fc27CacheServerDraft(snap);}catch(_){}if(render)try{renderManualReceivingDraft();}catch(_){}return snap;}
async function fc50FlushAll(maxMs=30000){
 const deadline=Date.now()+Math.max(3000,Number(maxMs||30000));let last=-1,stalled=0;
 while(Date.now()<deadline){const group=fc46QueueGroup(await FC32Queue.list(scopeKey())),n=group.current.length;if(!n){queueStatus(0,false,group.stale.length);return await fc50Snapshot().catch(()=>fc27ServerDraft||null);}if(!navigator.onLine)throw Error('Нет соединения с сервером. Сканы сохранены локально.');await fc42FlushCore();const after=fc46QueueGroup(await FC32Queue.list(scopeKey())).current.length;if(!after)return await fc50Snapshot().catch(()=>fc27ServerDraft||null);stalled=after>=n?stalled+1:0;last=after;await new Promise(r=>setTimeout(r,stalled>=2?150:10));}
 throw Error(`Не удалось подтвердить сервером ${last>0?last:'оставшиеся'} скан(ов). Сканы сохранены локально.`);
}
async function flush(){if(fc42FlushPromise)return fc42FlushPromise;fc42FlushPromise=fc42FlushCore().finally(()=>{fc42FlushPromise=null;});return fc42FlushPromise;}
async function localFirstAdd(scan){
 if(scan.clientId&&scan.clientId!==state.clientId||scan.warehouseId&&scan.warehouseId!==state.warehouseId)throw Error('Клиент или склад изменён. Повторите сканирование.');
 if(!state.clientId||!state.warehouseId)throw Error('Сначала выберите клиента и склад');if(!worker())throw Error('Укажите имя приёмщика');
 let receiptId='';try{receiptId=await fc45ReceiptId();}catch(_){const g=fc46QueueGroup(await FC32Queue.list(scopeKey()));receiptId=g.rid||'';}
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
 if(typeof activateManualReceivingBox==='function'&&!activateManualReceivingBox._fc44){
  const original=activateManualReceivingBox;
  const wrapped=async function(mode='SCAN',forceMove=false){
   const token=++fc44BoxActivationSeq,value=s(document.getElementById('manualReceiveBox')?.value),c=mode==='SCAN'?localBox(value):null,ctx=c?ctxFromBox(c):null;
   if(!ctx)return original(mode,forceMove);
   const wantedId=s(ctx.container_id),wantedAliases=[ctx.container_barcode,ctx.container_name].map(x=>s(x).toLocaleLowerCase('ru-RU')).filter(Boolean);
   manualReceivingStorageContext=ctx;
   if(document.getElementById('receiveLocation'))document.getElementById('receiveLocation').value=ctx.location_code||'';
   if(document.getElementById('receiveContainer'))document.getElementById('receiveContainer').value=ctx.container_barcode||ctx.container_name||'';
   renderManualReceivingStorageContext();document.getElementById('receiveSku')?.focus();fastSpeak('Короб');toast('Короб открыт локально: '+ctx.container_name);
   const body={client_id:state.clientId,warehouse_id:state.warehouseId,section:s(document.getElementById('manualReceiveSection')?.value)||null,aisle:s(document.getElementById('manualReceiveAisle')?.value)||null,pallet:s(document.getElementById('manualReceivePallet')?.value)||null,row_on_pallet:document.getElementById('manualReceiveRow')?.value?Number(document.getElementById('manualReceiveRow').value):null,box_value:value,box_mode:mode,force_move:false};
   fc39Direct('/r145f25/receiving/box',body,12000).then(r=>{
    if(token!==fc44BoxActivationSeq)return;
    const cur=manualReceivingStorageContext||{},curAliases=[cur.container_barcode,cur.container_name].map(x=>s(x).toLocaleLowerCase('ru-RU')).filter(Boolean);
    const stillSame=(wantedId&&s(cur.container_id)===wantedId)||wantedAliases.some(x=>curAliases.includes(x));
    if(!stillSame)return;
    const gotAliases=[r?.container_barcode,r?.container_name].map(x=>s(x).toLocaleLowerCase('ru-RU')).filter(Boolean),sameResponse=(wantedId&&s(r?.container_id)===wantedId)||wantedAliases.some(x=>gotAliases.includes(x));
    if(!sameResponse){try{toast('Фоновый ответ относится к другому коробу и проигнорирован.',true);}catch(_){}return;}
    manualReceivingStorageContext=r;
    if(document.getElementById('receiveLocation'))document.getElementById('receiveLocation').value=r.location_code||'';
    if(document.getElementById('receiveContainer'))document.getElementById('receiveContainer').value=r.container_barcode||r.container_name||'';
    renderManualReceivingStorageContext();
   }).catch(e=>{if(token===fc44BoxActivationSeq)toast('Фоновая проверка короба: '+e.message,true);});
   return ctx;
  };
  wrapped._fc32=true;wrapped._fc44=true;activateManualReceivingBox=wrapped;
 }
 if(typeof fc26CommitManual==='function'&&!fc26CommitManual._fc32){const orig=fc26CommitManual;const wrapped=async function(...args){await flush();const n=await fc42PendingCount();if(n)throw Error(`Ещё ${n} скан(ов) текущей приёмки ожидают подтверждения сервера. Дождитесь синхронизации.`);return orig(...args);};wrapped._fc32=true;fc26CommitManual=wrapped;}
 const oldResume=typeof fc30ResumeWorkerDraft==='function'?fc30ResumeWorkerDraft:null;if(oldResume&&!oldResume._fc32){const w=async function(...a){const r=await oldResume(...a);scheduleFlush(0);return r;};w._fc32=true;fc30ResumeWorkerDraft=w;}
 window.addEventListener('online',()=>scheduleFlush(0));document.addEventListener('visibilitychange',()=>{if(!document.hidden)scheduleFlush(0);});setTimeout(()=>fc46HydrateFromQueue().catch(()=>{}),0);setTimeout(()=>scheduleFlush(0),25);
}
window.FC32Receiving={flush,flushAll:fc50FlushAll,snapshot:fc50Snapshot,adopt:fc50Adopt,pending:fc42PendingCount,clear:fc42ClearCurrent,current:()=>fc42CurrentDraft(false),prepare:()=>fc45LoadState(true),state:fc45LoadState,hydrate:fc46HydrateFromQueue,ensure:fc46EnsureReceiptId,scopeKey,version:50};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
