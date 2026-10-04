/* FC_R145F38_RECEIVING_STABLE_UI */
(()=>{
'use strict';
let busy=false,normalizing=false,lastNormalize='',normalizeTimer=0;
const q=(s,r=document)=>r.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function worker(){try{return String(typeof fc30WorkerName==='function'?fc30WorkerName():(q('#receiveBy')?.value||'')).trim().replace(/\s+/g,' ');}catch(_){return String(q('#receiveBy')?.value||'').trim();}}
function contextKey(){return [String(state?.clientId||''),String(state?.warehouseId||''),worker().toLocaleLowerCase('ru-RU')].join('|');}
function msgOf(x){if(!x)return'Неизвестная ошибка';if(typeof x==='string')return x;if(x.message)return String(x.message);if(x.detail)return msgOf(x.detail);try{return JSON.stringify(x);}catch(_){return String(x);}}
function status(text,bad=false){
 const panel=q('#manualReceivingDraftPanel');if(!panel)return;
 let el=q('#fc38Status',panel);if(!el){el=document.createElement('div');el.id='fc38Status';el.className='result-box';panel.insertAdjacentElement('afterbegin',el);}
 const html=`<b class="${bad?'bad':'good'}">${bad?'Приёмка не завершена':'Приёмка'}</b><span>${esc(text)}</span>`;
 if(el.innerHTML!==html)el.innerHTML=html;
}
async function postJson(url,body,timeout=180000){
 const ctl=new AbortController(),tm=setTimeout(()=>ctl.abort(),timeout);
 try{
  const r=await fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',signal:ctl.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const raw=await r.text();let d;try{d=JSON.parse(raw);}catch(_){d={detail:raw};}
  if(!r.ok)throw new Error(msgOf(d));return d;
 }catch(e){if(e.name==='AbortError')throw new Error('Сервер слишком долго отвечает. Приёмка не считается завершённой; обновите страницу и проверьте состояние.');throw e;}finally{clearTimeout(tm);}
}
async function flushLocal(){
 if(window.FC32Receiving){await window.FC32Receiving.flush();const n=await window.FC32Receiving.pending();if(n)throw new Error(`Ещё ${n} скан(ов) сохранены локально и ожидают сервера. Дождитесь надписи «всё сохранено на сервере».`);}
 if(typeof manualReceivingScanBusy!=='undefined'&&manualReceivingScanBusy)throw new Error('Дождитесь сохранения текущего скана.');
 if(typeof manualReceivingScanQueue!=='undefined'&&manualReceivingScanQueue.length)throw new Error(`Ещё ${manualReceivingScanQueue.length} скан(ов) в очереди обработки.`);
}
function retitle(){
 const b=q('#commitManualReceivingDraft');
 if(b&&!busy){if(b.textContent!=='Завершить приёмку')b.textContent='Завершить приёмку';if(b.dataset.fc38!=='1')b.dataset.fc38='1';}
 const c=q('#clearManualReceivingDraft');
 if(c&&!busy){if(c.textContent!=='Начать новую приёмку')c.textContent='Начать новую приёмку';if(c.dataset.fc38!=='1')c.dataset.fc38='1';}
}
async function adoptSnapshot(snap){
 if(!snap)return;
 try{fc27ServerDraft=snap;fc27ServerDraftContext=typeof fc27DraftContext==='function'?fc27DraftContext():fc27ServerDraftContext;fc27ServerDraftFlight=null;}catch(_){ }
 try{if(typeof fc27CacheServerDraft==='function')await fc27CacheServerDraft(snap);}catch(_){ }
 try{renderManualReceivingDraft();}catch(_){ }
 retitle();
}
async function normalize(force=false){
 const w=worker();if(!w||!state?.clientId||!state?.warehouseId||normalizing)return null;
 const key=contextKey();if(!force&&lastNormalize===key)return null;
 normalizing=true;
 try{
  await flushLocal();
  const before=Number(fc27ServerDraft?.units||0),beforeId=String(fc27ServerDraft?.id||'');
  const snap=await postJson('/r145f37/manual-drafts/normalize-worker',{client_id:state.clientId,warehouse_id:state.warehouseId,worker:w,reference:q('#receiveReference')?.value?.trim()||null},30000);
  await adoptSnapshot(snap);lastNormalize=key;
  if(beforeId&&beforeId!==String(snap.id||'')&&before>0&&Number(snap.units||0)===0)status('Старый технический черновик закрыт. Открыта новая пустая приёмка.');
  return snap;
 }finally{normalizing=false;}
}
function scheduleNormalize(force=false,delay=120){
 clearTimeout(normalizeTimer);
 normalizeTimer=setTimeout(()=>normalize(force).catch(e=>status(msgOf(e),true)),delay);
}
function resetPlace(){
 try{manualReceivingStorageContext=null;}catch(_){ }
 for(const id of ['manualReceiveBox','receiveLocation','receiveContainer']){const e=q('#'+id);if(e)e.value='';}
 try{renderManualReceivingStorageContext();}catch(_){ }
 try{q('#receiveSku')?.focus();}catch(_){ }
}
async function resetCurrent(){
 if(busy)return;
 const w=worker();if(!w){status('Укажите имя приёмщика.',true);return;}
 try{
  if(typeof manualReceivingScanBusy!=='undefined'&&manualReceivingScanBusy)throw new Error('Дождитесь завершения текущего скана.');
  if(typeof manualReceivingScanQueue!=='undefined'&&manualReceivingScanQueue.length)throw new Error('Дождитесь обработки текущей очереди сканера.');
  const snap=fc27ServerDraft||await fc27EnsureServerDraft(),units=(snap?.rows||[]).filter(r=>!r.posted_at&&Number(r.quantity||0)>0).reduce((n,r)=>n+Number(r.quantity||0),0);
  if(units&&!confirm(`Начать новую приёмку?\n\nТекущий черновик ${units} шт. будет закрыт БЕЗ добавления на остатки. История и остатки не изменятся.`))return;
  busy=true;retitle();status('Закрываем старый черновик без движения остатков…');
  try{if(window.FC32Receiving?.clear)await window.FC32Receiving.clear();}catch(_){ }
  const data=await postJson('/r145f37/manual-drafts/reset-worker',{client_id:state.clientId,warehouse_id:state.warehouseId,worker:w,reference:null},30000);
  await adoptSnapshot(data.next_receipt);if(q('#receiveReference'))q('#receiveReference').value='';resetPlace();
  status('Открыта новая пустая приёмка. Старый черновик больше не вернётся в рабочее окно.');lastNormalize=contextKey();
 }catch(e){status(msgOf(e),true);try{toast(msgOf(e),true);}catch(_){ }}
 finally{busy=false;retitle();}
}
async function complete(){
 if(busy)return;
 const w=worker();if(!w){status('Укажите имя приёмщика.',true);return;}
 try{
  await flushLocal();
  const snap=await normalize(true)||fc27ServerDraft||await fc27EnsureServerDraft();
  const rows=(snap?.rows||[]).filter(r=>!r.posted_at&&Number(r.quantity||0)>0);
  const units=rows.reduce((n,r)=>n+Number(r.quantity||0),0);
  if(!units)throw new Error('В текущей приёмке нет товара.');
  if(!confirm(`Завершить приёмку ${w}: ${units} шт. / ${rows.length} поз.?\n\nНа остатки и в историю должно попасть ровно это количество.`))return;
  busy=true;const b=q('#commitManualReceivingDraft');if(b){b.disabled=true;b.textContent='Фиксируем '+units+' шт.…';}
  status(`Зафиксирован серверный снимок: ${units} шт. / ${rows.length} поз. Проводим одной операцией…`);
  const data=await postJson(`/r145f37/manual-drafts/${encodeURIComponent(snap.id)}/commit-exact`,{client_id:state.clientId,warehouse_id:state.warehouseId,worker:w,reference:q('#receiveReference')?.value?.trim()||null,performed_by:w,stock_class:q('#receiveStockClass')?.value||'MAIN'});
  await adoptSnapshot(data.next_receipt);if(q('#receiveReference'))q('#receiveReference').value='';resetPlace();
  if(data.old_draft_closed)status('Старый технический черновик закрыт без повторного начисления. Новая приёмка пустая и готова к работе.');
  else{const got=Number(data.posted_units||0),want=Number(data.draft_units||0);if(got!==want)throw new Error(`Контроль не пройден: черновик ${want}, проведено ${got}.`);status(`Готово: ${want} шт. из черновика = ${got} шт. в остатках/истории. Старая приёмка закрыта, новая пустая открыта.`);try{toast(`Приёмка завершена: ${got} шт.`,false);}catch(_){ }}
  try{window.loadR62ManualReceipts?.().catch(()=>{});}catch(_){ }
  lastNormalize=contextKey();
 }catch(e){status(msgOf(e),true);try{toast(msgOf(e),true);}catch(_){ }}
 finally{busy=false;const b=q('#commitManualReceivingDraft');if(b)b.disabled=false;retitle();}
}
// Event delegation only. There is intentionally NO MutationObserver and NO interval.
// The previous r145f37 observer watched the entire document and retitled buttons on
// every mutation; changing textContent generated another mutation and could create
// a self-sustaining render loop that froze/reloaded the Receiving tab.
document.addEventListener('click',e=>{
 const b=e.target?.closest?.('#commitManualReceivingDraft');
 if(b){e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();complete();return;}
 const c=e.target?.closest?.('#clearManualReceivingDraft');
 if(c){e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();resetCurrent();return;}
 // After normal user navigation/clicks, decorate once in the next frame. Idempotent.
 if(e.target?.closest?.('[data-route],a,button'))requestAnimationFrame(retitle);
},true);
document.addEventListener('change',e=>{
 if(e.target?.id==='receiveBy'){lastNormalize='';scheduleNormalize(true,80);}
},true);
document.addEventListener('focusin',e=>{
 if(e.target?.closest?.('#manualReceivingDraftPanel'))retitle();
},true);
function boot(){retitle();scheduleNormalize(true,350);setTimeout(retitle,900);setTimeout(retitle,2200);}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
