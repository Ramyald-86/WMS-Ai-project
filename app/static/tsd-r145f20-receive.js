/* FC_R145F29_TSD_RECEIVING_FULL_LIST_REMOVE_ANY_BOX */
(()=>{
'use strict';
const $=id=>document.getElementById(id),C=()=>window.FCTsdCore;
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S={receipt:null,scope:null,busy:false,changing:false,unknown:'',confirmed:false,lastCode:'',lastAt:0,uncertain:false,seq:0,renderSeq:0};
const ids=['Start','Scan','Undo','Next','Change','Complete','Cancel','Register','Refresh','Home'];
function visible(){return C()?.state.currentScreen==='receivegoods';}
function context(){
 const c=C()?.context()||{};return {client_id:c.client_id||'',warehouse_id:c.warehouse_id||'',worker:String(c.picker||'').trim()};
}
function scopeKey(c){return [c?.client_id,c?.warehouse_id,c?.worker].join('|');}
function required(){
 const c=context();if(!c.client_id||!c.warehouse_id)throw new Error('Выберите клиента и склад');
 if(!c.worker)throw new Error('Укажите сотрудника');
 if(S.scope&&scopeKey(S.scope)!==scopeKey(c))throw new Error('Клиент, склад или сотрудник изменились. Откройте приёмку заново.');
 return c;
}
function msg(text='',bad=false){const e=$('r20ReceiveMsg');e.textContent=text;e.className='msg '+(bad?'error':text?'ok':'');}
function persist(){C()?.persistRuntime();}
function openPlace(){return S.localPlace||[...(S.receipt?.places||[])].reverse().find(p=>p.status==='OPEN');}
function scanTargetId(){
 return S.unknown&&S.confirmed?'r20ReceiveArticle':openPlace()&&!S.changing?'r20ReceiveProduct':$('r20ReceiveReference').value.trim()?'r20ReceivePlace':'r20ReceiveReference';
}
function focus(forceScroll=false){
 if(!visible()||S.busy||S.uncertain)return;
 const expected=scanTargetId();
 const attempt=(n=0)=>{
  if(!visible()||S.busy||S.uncertain)return;
  const id=scanTargetId(),el=$(id);if(!el)return;
  try{el.focus({preventScroll:true});}catch(_){try{el.focus();}catch(__){}}
  if(forceScroll||id!==expected){
   try{
    const r=el.getBoundingClientRect(),h=window.innerHeight||document.documentElement.clientHeight||0;
    if(forceScroll||r.top<90||r.bottom>h-90)el.scrollIntoView({behavior:'auto',block:'center',inline:'nearest'});
   }catch(_){}
  }
  // Some Android/WebView scanner services drop focus immediately after a DOM redraw.
  // Repeat a few times so the next physical scan always reaches the active field.
  if(n<3)setTimeout(()=>attempt(n+1),[90,180,360][n]);
 };
 setTimeout(()=>attempt(0),35);
}
function restoreScannerSurface(forceScroll=true){
 if(!visible()||S.busy||S.uncertain)return;
 try{C()?.show?.('receivegoods');}catch(_){}
 focus(forceScroll);
}
function controls(){
 const open=!!openPlace(),lines=!!S.receipt?.lines?.length;
 ids.forEach(x=>{const e=$('r20Receive'+x);if(e)e.disabled=S.busy;});
 $('r20ReceiveComplete').disabled=S.busy||S.uncertain||!lines;
 $('r20ReceiveUndo').disabled=S.busy||S.uncertain||!lines;
 $('r20ReceiveScan').disabled=S.busy||S.uncertain||!open;
 $('r20ReceiveProduct').disabled=S.busy||S.uncertain||!open;
 $('r20ReceiveStart').disabled=S.busy||S.uncertain;
 $('r20ReceiveNext').disabled=S.busy||S.uncertain||!open;
 $('r20ReceiveCancel').disabled=S.busy||S.uncertain||!S.receipt;
 $('r20ReceiveRegister').disabled=S.busy||S.uncertain||!S.confirmed;
 if($('homeBtn'))$('homeBtn').disabled=S.busy;
}
function busy(on){S.busy=on;if(C())C().state.mutationBusy=on;controls();}
async function api(path,options={}){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),45000);
 try{
  const r=await fetch('/r145f19/tsd/'+path,{...options,credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json'}});
  const text=await r.text();let data;try{data=JSON.parse(text);}catch(_){data={detail:text};}
  if(!r.ok){const e=new Error(typeof data.detail==='string'?data.detail:data.detail?.message||`Ошибка сервера (${r.status})`);e.status=r.status;e.detail=data.detail;throw e;}
  return data;
 }catch(e){if(e.name==='AbortError'){const err=new Error('Сервер не ответил. Проверьте состояние приёмки перед повтором.');err.uncertain=true;throw err;}throw e;}finally{clearTimeout(timer);}
}
/* FC_R145F31_TSD_EXISTING_BOX_RESOLVE */
async function fc31OpenPlace(payload){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
 try{
  const r=await fetch('/r145f31/tsd/receipts/open-place',{method:'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
  const text=await r.text();let data;try{data=JSON.parse(text);}catch(_){data={detail:text};}
  if(!r.ok){const e=new Error(typeof data.detail==='string'?data.detail:data.detail?.message||`Ошибка сервера (${r.status})`);e.status=r.status;e.detail=data.detail;throw e;}
  return data;
 }catch(e){if(e.name==='AbortError'){const x=new Error('Короб найден не был: сервер долго не отвечает. Повторите скан после проверки связи.');x.uncertain=true;throw x;}throw e;}finally{clearTimeout(timer);}
}

/* FC_R145F32_TSD_LOCAL_FIRST */
/* FC_R145F34_TSD_SCAN_GUARANTEE */
let fc32TsdFlushBusy=false,fc32TsdFlushTimer=0,fc32TsdPlaceFlight=null;
function fc32TsdScope(){const c=context();return ['tsd32',c.client_id,c.warehouse_id,c.worker].join('|');}
function fc32TsdKey(){return 'fc32_tsd_queue:'+fc32TsdScope();}
function fc32TsdPending(){try{const x=JSON.parse(localStorage.getItem(fc32TsdKey())||'[]');return Array.isArray(x)?x:[];}catch(_){return [];}}
function fc32TsdSave(rows){
 try{
  localStorage.setItem(fc32TsdKey(),JSON.stringify(rows));
  return true;
 }catch(e){
  msg('Локальная очередь не сохранилась: '+e.message,true);
  return false;
 }
}
function fc32FastVoice(text='Есть',bad=false){try{FC25.beep?.(bad);}catch(_){}try{FC25.speak?.(text,bad);}catch(_){}}
function fc32TsdLocalPlace(code,type){const kind=type==='LOCATION'?'LOCATION':'BOX';return {id:'fc32-local-place',target_kind:kind,target_code:code,display_name:code,location_display:'Проверяется сервером…',status:'OPEN',local_pending:true};}
function fc32TsdPendingVisual(){
 const rows=fc32TsdPending(),host=$('r20ReceiveLines');if(!host)return;
 host.querySelectorAll('[data-fc32-pending]').forEach(x=>x.remove());
 if(!rows.length)return;
 const box=new Map();for(const e of rows){const k=String(e.place_code||'Текущий короб');let a=box.get(k);if(!a){a=[];box.set(k,a);}a.push(e);}
 const html=[...box.entries()].map(([name,items])=>`<section class="fc25-tsd-group" data-fc32-pending><h3>${esc(name)} <strong>${items.length} локально</strong></h3>${items.slice(-30).reverse().map(e=>`<div class="r20-line"><div><b>ШК ${esc(e.barcode)}</b><small>Ожидает фоновой синхронизации</small></div><strong>+1</strong></div>`).join('')}</section>`).join('');
 host.insertAdjacentHTML('afterbegin',html);
}
async function fc32TsdApi(path,body){const r=await fetch(path,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const text=await r.text();let data;try{data=JSON.parse(text);}catch(_){data={detail:text};}if(!r.ok){const e=new Error(typeof data.detail==='string'?data.detail:data.detail?.message||`Ошибка сервера (${r.status})`);e.status=r.status;e.detail=data.detail;throw e;}return data;}
function fc32ScheduleTsdFlush(delay=30){clearTimeout(fc32TsdFlushTimer);fc32TsdFlushTimer=setTimeout(()=>fc32FlushTsd().catch(()=>{}),delay);}
async function fc32FlushTsd(force=false){
 if(fc32TsdFlushBusy||!navigator.onLine)return;const rows=fc32TsdPending();if(!rows.length)return;
 if(fc32TsdPlaceFlight)try{await fc32TsdPlaceFlight;}catch(_){if(!force)return;}
 fc32TsdFlushBusy=true;
 try{
  const c=required(),batch=rows.slice(0,30),reference=$('r20ReceiveReference').value.trim()||S.receipt?.reference||'Приёмка';
  const d=await fc32TsdApi('/r145f32/tsd/sync-batch',{...c,reference,events:batch.map(e=>({request_id:e.id,barcode:e.barcode,place_code:e.place_code,target_type:e.target_type||'AUTO'}))});
  const ack=new Set((d.acks||[]).map(x=>String(x.request_id))),drop=new Set(ack);let hardFail=null;
  for(const f of d.failed||[]){if(Number(f.status)<500){drop.add(String(f.request_id));if(!hardFail)hardFail=f;}}
  const current=fc32TsdPending();const left=current.filter(x=>!drop.has(String(x.id)));if(!fc32TsdSave(left))throw new Error('Не удалось сохранить очередь после ответа сервера');if(d.receipt)S.receipt=d.receipt;
  if(hardFail){const ev=rows.find(x=>String(x.id)===String(hardFail.request_id));if(hardFail.status===404&&hardFail.detail?.code==='UNKNOWN_PRODUCT'&&ev){S.unknown=ev.barcode;S.confirmed=false;$('r20ReceiveUnknown').hidden=false;for(const id of ['Article','Size','Name','Category'])$('r20Receive'+id).value='';$('r20ReceiveUnknownText').textContent=`Новый ШК: ${ev.barcode}. Отсканируйте его ещё раз для подтверждения.`;}msg(typeof hardFail.detail==='string'?hardFail.detail:(hardFail.detail?.message||'Скан не подтверждён сервером'),true);}
  if(!left.length){render(S.receipt);msg('Все локальные сканы сохранены на сервере');}else{fc32TsdPendingVisual();msg(`Работа продолжается · ждут сервера: ${left.length}`);fc32ScheduleTsdFlush(20);}
 }catch(e){msg(`Добавление продолжается локально · очередь сохранена. Сервер: ${e.message}`,true);}
 finally{fc32TsdFlushBusy=false;restoreScannerSurface(false);}
}
window.addEventListener('online',()=>fc32ScheduleTsdFlush(0));document.addEventListener('visibilitychange',()=>{if(!document.hidden)fc32ScheduleTsdFlush(0);});

function handle(e,mutation=false){FC25.speak('\u041e\u0448\u0438\u0431\u043a\u0430. \u041f\u0440\u043e\u0432\u0435\u0440\u044c\u0442\u0435 \u044d\u043a\u0440\u0430\u043d',true);
 if(mutation&&(!e.status||e.status>=500)){S.uncertain=true;persist();controls();msg(e.message+'\nРезультат последнего действия не подтверждён. Нажмите «Проверить состояние» и сверьте количество. Автоматического повтора нет.',true);}
 else msg(e.message||'Не удалось выполнить действие',true);
 if(e.status===401)msg('Сеанс входа закончился. Войдите снова. Подтверждённые сканы остались на сервере.',true);
}

function activeReceiptGroup(receipt,groups){
 const open=openPlace();if(!open)return null;
 const oid=String(open.container_id||''),olid=String(open.location_id||'');
 const found=groups.find(g=>(g.rows||[]).some(l=>open.target_kind==='BOX'&&oid?String(l.container_id||'')===oid:String(l.location_id||'')===olid));
 if(found)return found;
 return {key:`active:${open.id||open.target_code||olid}`,name:open.display_name||(open.target_kind==='BOX'?open.target_code:(open.location_display||open.target_code||'Текущее место')),total:0,items:[],rows:[],synthetic:true};
}
function itemRows(g,l){
 const sku=String(l.sku_id||''),barcode=String(l.barcode||'');
 return (g.rows||[]).filter(r=>(!sku||String(r.sku_id||'')===sku)&&String(r.barcode||'')===barcode).sort((a,b)=>Date.parse(b.scanned_at||0)-Date.parse(a.scanned_at||0));
}
function groupItemsHtml(g){
 if(!(g.items||[]).length)return '<p class="fc27-active-empty">В текущем коробе в этой приёмке пока 0 шт. Сканируйте товар.</p>';
 return g.items.map(l=>{
  const row=itemRows(g,l)[0],lineId=row?.id||'',label=l.article||l.name||l.barcode||'Товар';
  const remove=lineId?`<button type="button" class="secondary fc29-remove-one" data-fc29-remove-one="${esc(lineId)}" data-fc29-remove-label="${esc(label)}" data-fc29-remove-box="${esc(g.name)}">Удалить 1 шт.</button>`:'';
  return `<div class="r20-line"><div><b>${esc(l.article||l.name||'Товар')} · ${esc(l.size||'')}</b><small>ШК ${esc(l.barcode||'')}</small><small>${esc(FC25.publicText(l.location_display||''))}</small></div><div class="fc29-line-actions"><strong>${l.quantity} шт.</strong>${remove}</div></div>`;
 }).join('');
}
function groupHtml(g,mode='other'){
 const title=`${esc(g.name)} <strong>${Number(g.total||0)} шт.</strong>`;
 if(mode==='active')return `<section class="fc25-tsd-group fc27-active-receive-group" data-fc27-active-box="1"><div class="fc27-active-kicker">ПРИНИМАЕМ СЮДА СЕЙЧАС</div><h3>${title}</h3>${groupItemsHtml(g)}</section>`;
 return `<details class="fc25-tsd-group fc27-receive-group"><summary><span>${esc(g.name)}</span><strong>${Number(g.total||0)} шт.</strong></summary><div class="fc27-receive-group-body">${groupItemsHtml(g)}</div></details>`;
}
function renderAllReceiptGroups(groups,active){
 const host=$('r20ReceiveLines');if(!host)return;
 const token=++S.renderSeq;host.innerHTML='';
 if(active)host.insertAdjacentHTML('beforeend',groupHtml(active,'active'));
 const others=active&&!active.synthetic?groups.filter(g=>g.key!==active.key):groups.slice();
 if(!active&&!others.length){host.innerHTML='<p>Подтверждённых сканов пока нет.</p>';return;}
 if(!others.length)return;
 host.insertAdjacentHTML('beforeend',`<details class="fc27-other-receipt-groups"><summary>Остальные короба / места приёмки <strong>${others.length}</strong></summary><div class="fc27-other-receipt-list" data-fc27-other-list></div></details>`);
 const target=host.querySelector('[data-fc27-other-list]');let i=0;const chunk=40;
 const append=()=>{
  if(token!==S.renderSeq||!visible()||!target)return;
  const end=Math.min(i+chunk,others.length),html=[];
  for(;i<end;i++)html.push(groupHtml(others[i],'other'));
  target.insertAdjacentHTML('beforeend',html.join(''));
  if(i<others.length){if(typeof requestAnimationFrame==='function')requestAnimationFrame(append);else setTimeout(append,0);}
 };
 append();
}

function render(receipt){
 S.receipt=receipt||null;
 const open=openPlace(),places=new Map((receipt?.places||[]).map(x=>[String(x.id),x]));
 const reference=$('r20ReceiveReference');
 if(receipt)reference.value=receipt.reference||'';reference.readOnly=!!receipt;
 $('r20ReceiveSetup').hidden=!!open&&!S.changing;
 $('r20ReceiveActive').hidden=!open||S.changing;
 if(open){
  $('r20ReceiveKind').textContent=open.target_kind==='LOCATION'?'ЯЧЕЙКА / ПАЛЕТА БЕЗ КОРОБА':'ТЕКУЩИЙ КОРОБ';
  $('r20ReceivePlaceName').textContent=open.display_name||(open.target_kind==='BOX'?open.target_code:'Ячейка без названия');
  $('r20ReceiveAddress').textContent=open.location_display||'Адрес не указан';
  $('r20ReceiveAddress').hidden=open.target_kind==='LOCATION'&&open.location_display===open.display_name;
 }
 $('r20ReceiveReceiptInfo').textContent=receipt?`Приёмка: ${receipt.reference} · Сотрудник: ${receipt.worker}`:'Черновик ещё не создан';
 const groups=FC25.receiptGroups(receipt),active=activeReceiptGroup(receipt,groups),positions=groups.reduce((n,g)=>n+g.items.length,0);
 $('r20ReceiveTotals').textContent=`Всего: ${Number(receipt?.units||0)} шт. · ${positions} поз. · ${groups.length} мест · показан весь список`;
 renderAllReceiptGroups(groups,active);
 if($('r20ReceiveMore'))$('r20ReceiveMore').hidden=true;
 FC25.audioToggle($('r20ReceiveReceiptInfo')?.parentElement);
 controls();persist();
}
function clearUnknown(){S.unknown='';S.confirmed=false;$('r20ReceiveUnknown').hidden=true;controls();}
async function load(reconcile=false){
 if(S.busy)return;const c=required(),sequence=++S.seq;busy(true);
 try{
  const d=await api('receipts/active?'+new URLSearchParams(c));if(sequence!==S.seq)return;
  render(d.receipt);
  if(reconcile&&S.uncertain){
   msg(`На сервере: ${Number(d.receipt?.units||0)} шт. Проверьте последние позиции.`,true);
   if(confirm(`На сервере сейчас ${Number(d.receipt?.units||0)} шт.\n\nСверьте последние позиции с фактическими сканами. Продолжить работу без автоматического повтора предыдущего действия?`)){S.uncertain=false;msg('Состояние получено с сервера. Предыдущее действие автоматически не повторяется.');}
  }else if(!S.uncertain)msg(d.receipt?'Сохранённая приёмка загружена':'Сканируйте место, чтобы начать приёмку');
 }catch(e){handle(e);}finally{busy(false);persist();restoreScannerSurface(true);}
}
async function open(saved=null){
 S.scope=context();required();S.changing=false;clearUnknown();S.uncertain=!!saved?.uncertain;
 C().saveSetup();C().show('receivegoods');
 $('r20ReceiveScope').textContent=`${$('clientSelect')?.selectedOptions[0]?.textContent||''} · ${$('warehouseSelect')?.selectedOptions[0]?.textContent||''} · ${S.scope.worker}`;
 if(saved?.reference)$('r20ReceiveReference').value=saved.reference;
 render(null);msg('Загружаем сохранённую приёмку…');await load(false);fc32TsdPendingVisual();fc32ScheduleTsdFlush(0);
}
async function start(raw){
 if(S.uncertain)return;
 const c=required(),code=String(raw||'').trim(),reference=$('r20ReceiveReference').value.trim(),type=$('r20ReceiveType').value;
 if(!reference)throw new Error('Укажите номер / название приёмки');if(!code)throw new Error('Отсканируйте короб или ячейку');
 const optimistic=(type==='BOX'||type==='AUTO'||/^BOX[_-]/i.test(code));
 if(optimistic){S.localPlace=fc32TsdLocalPlace(code,type==='LOCATION'?'LOCATION':'BOX');S.changing=false;clearUnknown();$('r20ReceivePlace').value='';render(S.receipt);msg(`Открыт локально: ${code} · сервер проверяется в фоне`);fc32FastVoice('Короб');restoreScannerSurface(false);}
 const job=fc32TsdApi('/r145f32/tsd/receipts/open-place',{...c,reference,code,target_type:type}).then(d=>{S.localPlace=null;S.changing=false;S.receipt=d.receipt;clearUnknown();render(S.receipt);msg(`Короб открыт: ${d.box_name||d.place?.display_name||code}${Number(d.duplicate_count||0)>1?' · выбран актуальный из старых дублей':''}`);fc32ScheduleTsdFlush(0);return d;}).catch(e=>{S.localPlace=null;render(S.receipt);handle(e,true);throw e;}).finally(()=>{if(fc32TsdPlaceFlight===job)fc32TsdPlaceFlight=null;restoreScannerSurface(true);});
 fc32TsdPlaceFlight=job;if(!optimistic)return await job;return true;
}
function validNewBarcode(raw){
 if(!/^(?:\d{8}|\d{12}|\d{13}|\d{14})$/.test(raw))return false;
 const digits=[...raw].map(Number),check=digits.pop();let sum=0;
 for(let i=digits.length-1,weight=3;i>=0;i--,weight=weight===3?1:3)sum+=digits[i]*weight;
 return (10-sum%10)%10===check;
}
async function scanProduct(raw){
 const code=String(raw||'').trim();
 if(!code)return;
 if(S.uncertain){msg('Сначала нажмите «Проверить состояние» и сверьте количество.',true);return;}
 if(S.unknown){
  if(!S.confirmed&&code===S.unknown){S.confirmed=true;$('r20ReceiveUnknownText').textContent=`ШК подтверждён: ${code}. Заполните карточку.`;controls();focus();return;}
  if(code!==S.unknown)clearUnknown();
 }
 const place=openPlace();
 if(!place)throw new Error('Сначала откройте короб или ячейку');
 const c=required();
 const id=(crypto?.randomUUID?.()||('tsd34-'+Date.now()+'-'+Math.random()));
 const rows=fc32TsdPending();
 const event={id,barcode:code,place_code:place.target_code||place.display_name||'',target_type:place.target_kind==='LOCATION'?'LOCATION':'BOX',created_at:Date.now()};
 rows.push(event);
 if(!fc32TsdSave(rows)){
  rows.pop();
  throw new Error('Скан получен, но не сохранён локально. Повторите сканирование — подтверждающий звук приложения не выдавался.');
 }
 S.lastCode=code;S.lastAt=Date.now();
 msg(`Добавлено локально · +1 · ждут сервера: ${rows.length}`);
 C()?.feedback?.('ok','+1');
 fc32FastVoice('Есть');
 fc32TsdPendingVisual();
 restoreScannerSurface(false);
 fc32ScheduleTsdFlush(0);
}
async function register(){
 if(S.busy||S.uncertain)return;
 if(!S.unknown||!S.confirmed)throw new Error('Дважды отсканируйте один и тот же новый ШК');
 const article=$('r20ReceiveArticle').value.trim(),size=$('r20ReceiveSize').value.trim(),category=$('r20ReceiveCategory').value.trim();
 if(!article||!size||!category)throw new Error('Заполните артикул, размер и категорию');
 const c=required();busy(true);
 try{const d=await api('receipts/register',{method:'POST',body:JSON.stringify({...c,barcode:S.unknown,article,size,category,name:$('r20ReceiveName').value.trim()})});clearUnknown();render(d.receipt);msg('Карточка создана · добавлена 1 шт.');FC25.speak('\u0422\u043e\u0432\u0430\u0440 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d');}
 catch(e){handle(e,true);}finally{busy(false);restoreScannerSurface(true);}
}
async function removeOneFromAnyBox(button){
 if(S.busy||S.uncertain)return;
 const lineId=String(button?.dataset?.fc29RemoveOne||''),label=String(button?.dataset?.fc29RemoveLabel||'Товар'),box=String(button?.dataset?.fc29RemoveBox||'этого короба');
 if(!lineId)return;
 if(!confirm(`Удалить 1 шт. «${label}» из ${box}?\n\nУдаляется только ошибочный скан из этой незавершённой приёмки. Остатки склада ещё не изменяются.`))return;
 const c=required();busy(true);
 try{
  const d=await api('receipts/remove-one-v29',{method:'POST',body:JSON.stringify({...c,line_id:lineId})});
  clearUnknown();render(d.receipt);msg(`Удалено 1 шт. из ${box}.`);C()?.feedback?.('ok','−1');FC25.speak('Товар удалён');
 }catch(e){handle(e,true);}finally{busy(false);restoreScannerSurface(true);}
}

/* FC_R145F33_TSD_COMPLETE_INACTIVE_BOX */
async function fc33CompleteReceipt(c){
 const body={...c,receipt_id:S.receipt?.id||null};
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),60000);
 try{
  const r=await fetch('/r145f33/tsd/receipts/complete',{method:'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const text=await r.text();let data;try{data=JSON.parse(text);}catch(_){data={detail:text};}
  if(!r.ok){const e=new Error(typeof data.detail==='string'?data.detail:data.detail?.message||`Ошибка сервера (${r.status})`);e.status=r.status;e.detail=data.detail;throw e;}
  return data;
 }catch(e){if(e.name==='AbortError'){const x=new Error('Сервер долго завершает приёмку. Нажмите «Проверить состояние» перед повтором.');x.uncertain=true;throw x;}throw e;}finally{clearTimeout(timer);}
}

/* FC_R145F35_TSD_FINALIZE_PARITY */
async function fc35CompleteReceipt(c){
 const body={...c,receipt_id:S.receipt?.id||null};
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),90000);
 try{
  const r=await fetch('/r145f35/tsd/receipts/complete-verified',{method:'POST',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const text=await r.text();let data;try{data=JSON.parse(text);}catch(_){data={detail:text};}
  if(!r.ok){const detail=data.detail;const e=new Error(typeof detail==='string'?detail:detail?.message||`Ошибка сервера (${r.status})`);e.status=r.status;e.detail=detail;throw e;}
  return data;
 }catch(e){if(e.name==='AbortError'){const x=new Error('Сервер долго сверяет приёмку. Нажмите «Проверить состояние» перед повтором.');x.uncertain=true;throw x;}throw e;}finally{clearTimeout(timer);}
}
async function action(actionName){
 if(S.busy||S.uncertain)return;
 if(actionName==='complete'){await fc32FlushTsd(true);const pending=fc32TsdPending();if(pending.length)throw new Error(`Ещё ${pending.length} скан(ов) ожидают сервера. Дождитесь синхронизации.`);}
 const c=required();busy(true);
 try{
  const d=actionName==='complete'?await fc35CompleteReceipt(c):await api('receipts/'+actionName,{method:'POST',body:JSON.stringify(c)});S.changing=false;clearUnknown();render(d.receipt||null);
  if(actionName==='complete'){$('r20ReceiveReference').value='';const repaired=Number(d.repaired_units||0);msg(`Приёмка «${d.reference}» завершена и сверена: ${d.expected_units??d.units} шт. в черновике = ${d.posted_units??d.units} шт. в истории/остатках.${repaired?` Восстановлено: ${repaired} шт.`:''}`);}
  else if(actionName==='cancel'){$('r20ReceiveReference').value='';msg('Черновик удалён по вашему подтверждению.');}
  else if(actionName==='next-place'){msg('Место закрыто. Сканируйте следующий короб или ячейку.');}
  else msg('Последний скан отменён');
 }catch(e){handle(e,true);}finally{busy(false);restoreScannerSurface(true);}
}
function sendProduct(){const e=$('r20ReceiveProduct'),v=e.value;e.value='';scanProduct(v).catch(x=>handle(x));}
function scan(raw){if(!visible())return;if(openPlace()&&!S.changing)scanProduct(raw).catch(x=>handle(x));else start(raw).catch(x=>handle(x));}
function bindScanner(id,run){
 const el=$(id);let timer=null,first=0,previousLength=0,fast=false;
 el.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key==='Tab'){if(el.value){e.preventDefault();clearTimeout(timer);run();}}});
 el.addEventListener('paste',e=>{const v=e.clipboardData?.getData('text');if(v){e.preventDefault();el.value=v;clearTimeout(timer);run();}});
 el.addEventListener('input',()=>{
  const now=Date.now(),length=el.value.length;
  if(length<=1||length<previousLength){first=now;fast=false;}
  if(length-previousLength>2)fast=true;
  previousLength=length;clearTimeout(timer);
  if(fast||(length>=6&&now-first<150))timer=setTimeout(()=>{if(el.value&&visible())run();previousLength=0;fast=false;},180);
 });
}
function init(){
 if(!$('receivegoods'))return;
 const safe=fn=>(...args)=>Promise.resolve().then(()=>fn(...args)).catch(x=>handle(x));
 $('r20ReceiveStart').onclick=safe(()=>start($('r20ReceivePlace').value));
 $('r20ReceiveScan').onclick=sendProduct;
 $('r20ReceiveRefresh').onclick=safe(()=>load(true));
 $('r20ReceiveHome').onclick=()=>{if(!S.busy){persist();C().show('setup');}};
 $('r20ReceiveChange').onclick=()=>{S.changing=true;clearUnknown();render(S.receipt);msg('Сканируйте другое место. Ранее принятые сканы остаются привязаны к прежнему месту.');restoreScannerSurface(true);};
 $('r20ReceiveNext').onclick=safe(()=>action('next-place'));
 $('r20ReceiveUndo').onclick=safe(()=>action('undo'));
 $('r20ReceiveComplete').onclick=safe(()=>{if(confirm(`Завершить всю приёмку «${S.receipt?.reference||''}»?\nВсего: ${Number(S.receipt?.units||0)} шт.\n\nТовар будет добавлен только на Основной склад.`))return action('complete');});
 $('r20ReceiveCancel').onclick=safe(()=>{if(confirm('Удалить весь черновик приёмки? Подтверждённые в нём сканы будут удалены. Складские остатки ещё не изменены.'))return action('cancel');});
 $('r20ReceiveRegister').onclick=safe(register);
 $('r20ReceiveSkip').onclick=()=>{clearUnknown();msg('Ошибочный скан отменён');restoreScannerSurface(true);};
 $('r20ReceiveKeyboard').onclick=()=>{const e=$('r20ReceiveProduct');e.inputMode=e.inputMode==='none'?'text':'none';e.focus();};
 $('r20ReceiveLines').addEventListener('click',e=>{const b=e.target.closest('[data-fc29-remove-one]');if(!b)return;e.preventDefault();removeOneFromAnyBox(b).catch(x=>handle(x,true));});
 $('r20ReceiveReference').addEventListener('input',persist);
 bindScanner('r20ReceivePlace',()=>start($('r20ReceivePlace').value).catch(x=>handle(x)));
 bindScanner('r20ReceiveProduct',sendProduct);
 window.FCTsdReceiving={
  open:safe(()=>open()),restore:saved=>open(saved),scan,
  snapshot:()=>({version:20,reference:$('r20ReceiveReference').value,receipt_id:S.receipt?.id||'',uncertain:S.uncertain}),
  refresh:()=>load(true),rearm:()=>restoreScannerSurface(true)
 };
 window.addEventListener('focus',()=>{if(visible())restoreScannerSurface(true);});
 window.addEventListener('pageshow',()=>{if(visible())restoreScannerSurface(true);});
 document.addEventListener('visibilitychange',()=>{if(!document.hidden&&visible())restoreScannerSurface(true);});
}
// Late script loads must also resume the screen selected by core restore.
init();
const resumeLate=()=>{if(visible()&&!S.scope)open(C()?.state.receivingSaved||{}).catch(e=>handle(e));};
window.addEventListener('fc:tsd-ready',resumeLate);
if(C()?.ready)resumeLate();
})();
