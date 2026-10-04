// FC_R140F1_TSD_BARCODE_GUARD: unknown product barcode must be repeated identically before registration.
(()=>{
'use strict';
const core=window.FCTsdCore,$=id=>document.getElementById(id),esc=core.esc;
const s={mode:'RECEIVE',context:null,session:null,pending:null,queue:[],busy:false,fault:false,unknown:null,lastCode:'',lastAt:0,lastTransport:'',buffer:'',timer:null,changingPlace:false,panelMode:'expanded',panelDrag:null,panelIgnoreClick:false};
const uuid=()=>{if(window.crypto?.randomUUID)return window.crypto.randomUUID();const a=new Uint8Array(16);crypto.getRandomValues(a);a[6]=(a[6]&15)|64;a[8]=(a[8]&63)|128;const h=Array.from(a,x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);};
const contextOK=()=>JSON.stringify(core.context())===JSON.stringify(s.context);
const active=()=>core.state.currentScreen==='work';
function message(text,bad=false){$('workMsg').textContent=text;$('workMsg').className='msg '+(bad?'error':'ok');const hint=$('workItemsPanelHint');if(s.session&&hint&&text){hint.textContent=text;hint.classList.toggle('work-items-hint-error',bad);hint.classList.toggle('work-items-hint-ok',!bad);}}
function snapshot(){return {mode:s.mode,context:s.context,id:s.session?.id||'',pending:s.pending,queue:s.queue,unknown:s.unknown,changingPlace:s.changingPlace,panelMode:s.panelMode,form:Object.fromEntries(['workPlaceScan','workTargetType','workSection','workPlacement','workRow','workReceiptNumber'].map(id=>[id,$(id).value])),placeEmpty:$('workPlaceEmpty').checked};}
function persist(){core.persistRuntime();}
function viewportHeight(){return Math.max(320,Math.round(window.visualViewport?.height||window.innerHeight||document.documentElement?.clientHeight||800));}
function topbarHeight(){const r=document.querySelector('.topbar')?.getBoundingClientRect?.();return Math.max(0,Math.round(r?.height||58));}
function panelStops(){const vh=viewportHeight(),expanded=topbarHeight(),target=Math.max(expanded+150,Math.round(vh*.47)),half=Math.max(expanded+110,Math.min(vh-130,target));return {expanded,half};}
function applyPanel(rawTop=null){
 const panel=$('workItemsPanel');if(!panel)return;const stops=panelStops(),mode=s.panelMode==='half'?'half':'expanded';let top=rawTop===null?stops[mode]:Number(rawTop);if(!Number.isFinite(top))top=stops[mode];top=Math.max(stops.expanded,Math.min(stops.half,top));panel.style.top=Math.round(top)+'px';panel.dataset.mode=rawTop===null?mode:'dragging';
 panel.classList.toggle('work-items-expanded',rawTop===null&&mode==='expanded');panel.classList.toggle('work-items-half',rawTop===null&&mode==='half');
 $('workItemsHandle').setAttribute('aria-expanded',String(rawTop===null&&mode==='expanded'));$('workItemsPanelHint').classList.remove('work-items-hint-error','work-items-hint-ok');$('workItemsPanelHint').textContent=mode==='expanded'?'Потяните вниз — показать сканирование':'Потяните вверх — список на весь экран';$('workItemsChevron').textContent=mode==='expanded'?'⌄':'⌃';
}
function setPanelMode(mode,save=true){s.panelMode=mode==='half'?'half':'expanded';applyPanel();if(save)persist();}
function enterSessionView(resetPanel=true){if(resetPanel)s.panelMode='expanded';try{window.scrollTo?.(0,0);}catch(_){}window.requestAnimationFrame?.(()=>applyPanel());}
function beginPanelDrag(y,pointerId=null){if(!s.session)return;const panel=$('workItemsPanel'),stops=panelStops(),rect=panel.getBoundingClientRect?.();const fallback=stops[s.panelMode==='half'?'half':'expanded'];s.panelDrag={startY:Number(y),startTop:Number.isFinite(rect?.top)?rect.top:fallback,pointerId,moved:false};panel.classList.add('work-items-dragging');}
function movePanelDrag(y){if(!s.panelDrag)return;const d=s.panelDrag,dy=Number(y)-d.startY;if(Math.abs(dy)>5)d.moved=true;applyPanel(d.startTop+dy);}
function endPanelDrag(){if(!s.panelDrag)return;const panel=$('workItemsPanel'),d=s.panelDrag,stops=panelStops(),top=parseFloat(panel.style.top)||stops[s.panelMode==='half'?'half':'expanded'];s.panelDrag=null;panel.classList.remove('work-items-dragging');s.panelIgnoreClick=!!d.moved;s.panelMode=top<=(stops.expanded+stops.half)/2?'expanded':'half';applyPanel();persist();if(s.panelIgnoreClick)setTimeout(()=>{s.panelIgnoreClick=false;},0);}
function isTransportFailure(e){
 const status=Number(e?.httpStatus||0);if(status>=500)return true;if(status>0)return false;
 const name=String(e?.name||'').toLowerCase(),msg=String(e?.message||'').toLowerCase();
 return name==='aborterror'||/сервер не ответил|failed to fetch|networkerror|network request failed|load failed|connection (?:lost|refused|reset)|нет связи|ошибка связи/.test(msg);
}
function emergencyReset(){
 clearTimeout(s.timer);s.buffer='';s.panelDrag=null;s.panelIgnoreClick=false;s.busy=false;
 try{$('workItemsPanel')?.classList.remove('work-items-dragging');}catch(_){}
 persist();
 try{location.reload();}catch(_){location.href='/tsd';}
}
function focusWorkScanner(){if(!s.session||s.session.status!=='DRAFT'||s.fault||(s.unknown&&s.unknown.confirmed)||s.changingPlace)return;setTimeout(()=>{try{$('workProductScan').disabled=false;$('workProductScan').focus();}catch(_){}},0);}

function freeze(){
 const pending=!!s.pending||s.fault||!!s.unknown,busy=s.busy||s.queue.length>0,closed=s.session?.status!=='DRAFT',unknownNeedsRescan=!!s.unknown&&!s.unknown.confirmed;
 for(const id of ['workStart','workDraftRefresh','workTargetType','workPlaceEmpty','workRow','workSection','workPlacement','workPlaceScan','workNext'])$(id).disabled=s.busy||pending;
 for(const id of ['workPost','workCancel','workUndo'])$(id).disabled=busy||pending||closed;
 $('workUndo').disabled=$('workUndo').disabled||!s.session?.can_undo;
 // Navigation and emergency recovery must never be trapped behind a busy/fault state.
 $('homeBtn').disabled=false;
 if($('workPanicBtn'))$('workPanicBtn').disabled=false;
 $('workProductScan').disabled=closed||s.fault||(!!s.unknown&&!unknownNeedsRescan)||s.changingPlace;
 $('workScanSend').disabled=$('workProductScan').disabled;
 $('workChangePlace').disabled=busy||pending||closed;
 $('workChangePlaceScan').disabled=busy||pending||closed||!s.changingPlace;
 $('workChangePlaceApply').disabled=busy||pending||closed||!s.changingPlace;
 $('workChangePlaceCancel').disabled=busy||pending||!s.changingPlace;
 $('workRetry').classList.toggle('hidden',!s.fault);
 $('workRetry').disabled=s.busy;
 const recovery=$('workRecoveryInline');if(recovery)recovery.classList.toggle('hidden',!s.fault);
 if($('workRetryInline'))$('workRetryInline').disabled=s.busy;
 if($('workReloadInline'))$('workReloadInline').disabled=false;
 if($('workRecoveryInlineText')&&s.fault)$('workRecoveryInlineText').textContent='Связь или серверная операция прервались. Черновик и незавершённый скан сохранены; восстановление безопасно сверит состояние.';
 $('workRegister').disabled=s.busy||!s.unknown?.confirmed;$('workSkipUnknown').disabled=s.busy;
 $('workQueue').textContent=s.queue.length?`Ожидают сохранения: ${s.queue.length} сканов`:(s.busy?'Сохраняем…':(s.session?.status==='DRAFT'?'Сканы сохранены':''));
 for(const b of $('workLines').querySelectorAll('button'))b.disabled=busy||pending||closed;
}
function render(){
 const x=s.session,count=s.mode==='COUNT',closed=x&&x.status!=='DRAFT';
 $('workTitle').textContent=count?'Инвентаризация':'Приёмка товара';
 $('workIntro').textContent=count?'Сканируйте место, пересчитайте товар и сохраните фактическое количество.':'Откройте готовый короб или ячейку и сканируйте товар. Приёмка поступит на Основной склад.';
 $('workSetup').classList.toggle('hidden',!!x);$('workActive').classList.toggle('hidden',!x);$('work').classList.toggle('work-session-active',!!x);
 $('workReceivingOptions').classList.toggle('hidden',count);$('workReceiptBlock').classList.toggle('hidden',count);
 $('workNewProduct').classList.toggle('hidden',!s.unknown);
 if(s.unknown)$('workNewBarcode').textContent='ШК '+s.unknown.barcode+(s.unknown.confirmed?' · повторный скан подтверждён':' · отсканируйте этот же код ещё раз');
 if(x){
  const p=x.place;
  $('workPlaceName').textContent=p.container||p.location;
  $('workAddress').textContent=[...p.ancestors.slice().reverse().map(a=>a.code),p.row?'Ряд '+p.row:'',p.position?'Место '+p.position+'/8':'',!p.container?'Товар без короба':''].filter(Boolean).join(' · ');
  $('workStatus').textContent=x.status==='POSTED'?'Сохранено в остатки':x.status==='CANCELLED'?'Черновик удалён':'Черновик · '+x.picker;
  $('workChangePlace').classList.toggle('hidden',!!closed);
  $('workChangePlacePanel').classList.toggle('hidden',!!closed||!s.changingPlace);
  $('workScanBar').classList.toggle('hidden',!!closed||s.changingPlace);$('workFinishActions').classList.toggle('hidden',!!closed||s.changingPlace);$('workNext').classList.toggle('hidden',count?!closed:!(x.status==='DRAFT'&&x.units>0));
  $('workPost').textContent=count?'\u0421\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u044c \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u043f\u0435\u0440\u0435\u0441\u0447\u0451\u0442\u0430':'\u0417\u0430\u0432\u0435\u0440\u0448\u0438\u0442\u044c \u043c\u043e\u0439 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u043f\u0440\u0438\u0451\u043c\u043a\u0438';
  const totalText=`${x.lines.length} позиций · ${x.units} шт.`+(count&&x.unchecked?` · Не проверено: ${x.unchecked}`:'');$('workTotals').textContent=totalText;$('workItemsPanelTitle').textContent=(count?'Инвентаризация':'Товары')+' · '+x.lines.length+' поз. / '+x.units+' шт.';
  const result=new Map((x.result?.lines||[]).map(r=>[r.sku_id,r]));
  $('workLines').innerHTML=x.lines.map(r=>{
   const checked=r.quantity!==null,diff=count&&checked?r.quantity-r.expected:null,posted=result.get(r.sku_id);
   return `<article class="work-card ${!checked?'work-unchecked':''}" data-work-sku="${esc(r.sku_id)}"><b>${esc(r.article||r.barcode)} · ${esc(r.size||'—')}</b><small>${esc(r.name)}</small><small>ШК ${esc(r.barcode)}</small>${count?`<p>По учёту: <b>${r.expected}</b> · в резерве: ${r.reserved}</p>`:''}<div class="work-count">${count?'Пересчитано':'Принять'}: <strong>${checked?r.quantity:'не проверено'}</strong>${diff===null?'':`<span class="${diff<0?'work-short':diff>0?'work-extra':''}">${diff>0?'+':''}${diff===0?'Совпадает':diff+' шт.'}</span>`}</div>${closed?(posted?`<p>Остаток: ${posted.before} → <b>${posted.after} шт.</b></p>`:''):`<div class="work-buttons"><button data-work-action="minus" class="secondary" ${!checked||!r.quantity?'disabled':''}>−1</button><button data-work-action="edit" class="secondary">Количество</button>${count?'<button data-work-action="zero" class="secondary">Нет · 0</button>':''}<button data-work-action="remove" class="ghost danger-text">${count&&r.expected?'Сбросить сканы':'Удалить строку'}</button></div>`}</article>`;
  }).join('')||'<p class="work-note">В списке пока нет товаров.</p>';
  if(!s.panelDrag)applyPanel();
 }else{$('workItemsPanel').style.top='';$('workItemsPanel').dataset.mode='';}
 freeze();
}
async function request(path,body){return core.api(path,{method:'POST',body:JSON.stringify(body)});}
async function recover(){
 if(s.busy)return;
 s.busy=true;freeze();
 try{
  if(!contextOK())throw Error('Выберите прежнего клиента, склад и сотрудника, чтобы продолжить этот черновик.');
  if(s.pending){
   const pending=s.pending;
   s.session=await request(pending.path,pending.body);s.mode=s.session.mode;
   if(pending.scan&&s.queue[0]?.id===pending.scan)s.queue.shift();
   s.pending=null;
  }else if(s.session?.id){s.session=await core.api(`/tsd/mobile/work/${s.session.id}?${new URLSearchParams({client_id:s.context.client_id,warehouse_id:s.context.warehouse_id})}`);}
  s.fault=false;s.unknown=null;persist();render();message('Состояние восстановлено.');
 }catch(e){handleError(e);}finally{s.busy=false;freeze();}
 if(!s.fault){await process();focusWorkScanner();}
}
function handleError(e){
 if(e.detail?.code==='UNKNOWN_PRODUCT'&&s.pending?.scan){
  s.unknown={barcode:e.detail.barcode,pending:s.pending,confirmed:false};s.pending=null;s.fault=false;s.busy=false;
  $('workNewArticle').value='';$('workNewSize').value='';$('workNewName').value='';
  message('ШК не найден. Для защиты от ошибочного присвоения отсканируйте этот же ШК второй раз.',true);
 }else if(!isTransportFailure(e)){
  // Local validation and HTTP 4xx are operator-correctable errors, not connection failures.
  // Never lock the TSD because, for example, a box was not scanned or a number is empty.
  if(s.pending?.scan&&s.queue[0]?.id===s.pending.scan)s.queue.shift();
  s.pending=null;s.fault=false;s.busy=false;
  message(e.message+(s.queue.length?' Сканов в очереди: '+s.queue.length+'.':''),true);
  if(s.session?.status==='DRAFT'){s.panelMode='half';applyPanel();}
  focusWorkScanner();
 }else{
  // Only a real network/timeout/5xx failure freezes replay-sensitive controls.
  // The exact event ID/body is preserved so retry cannot double-count a scan.
  s.fault=true;s.busy=false;if(s.session?.status==='DRAFT'){s.panelMode='half';applyPanel();}
  message(e.message+(s.queue.length?' Сканов в очереди: '+s.queue.length+'.':''),true);
 }
 core.feedback('error',e.message);persist();render();if(s.unknown&&!s.unknown.confirmed)setTimeout(()=>$('workProductScan')?.focus(),40);
}

async function mutate(action,extra={},scanId=null){
 if(!s.session||!contextOK())throw Error('Контекст склада изменён. Откройте черновик заново.');
 const body={client_id:s.context.client_id,warehouse_id:s.context.warehouse_id,event_id:scanId||uuid(),version:s.session.version,action,...extra};
 s.pending={path:`/tsd/mobile/work/${s.session.id}/event`,body,scan:scanId};persist();
 const result=await request(s.pending.path,body);
 s.session=result;s.pending=null;
 if(scanId&&s.queue[0]?.id===scanId)s.queue.shift();
 persist();render();return result;
}
async function process(){
 if(s.busy||s.fault||s.unknown||!s.session||s.session.status!=='DRAFT')return;
 s.busy=true;freeze();
 try{
  while(s.queue.length){const q=s.queue[0];await mutate('SCAN',{barcode:q.code,occurred_at:q.occurred_at||new Date().toISOString()},q.id);core.feedback('ok','Учтено: '+q.code);message('Скан сохранён');}
 }catch(e){handleError(e);}finally{s.busy=false;freeze();if(!s.fault&&!s.unknown&&active())$('workProductScan').focus();}
}
function enqueue(raw,transport='keyboard'){
 const code=String(raw||'').replace(/[\r\n\t]/g,'').trim();if(!code)return;
 clearTimeout(s.timer);s.buffer='';$('workProductScan').value='';
 if(s.fault||!s.session||s.session.status!=='DRAFT'){core.feedback('error','Сначала завершите предыдущее действие');return;}
 if(!/^(?:\d{8}|\d{12,14})$/.test(code)){message('Нужен ШК товара: 8, 12, 13 или 14 цифр. Код короба сюда не подходит.',true);core.feedback('error','Неверный штрихкод товара');return;}
 if(s.unknown){
  if(s.unknown.confirmed){core.feedback('error','Новый ШК уже подтверждён. Заполните артикул и размер или уберите ошибочный скан.');return;}
  const first=s.unknown.barcode;
  if(code===first){s.unknown.confirmed=true;s.lastCode='';s.lastAt=0;persist();render();message(`ШК ${code} повторно подтверждён. Теперь укажите артикул и размер.`,false);core.feedback('ok','ШК подтверждён повторным сканом');setTimeout(()=>$('workNewArticle')?.focus(),40);return;}
  if(s.queue[0]?.id===s.unknown.pending?.scan)s.queue.shift();
  s.unknown=null;s.lastCode='';s.lastAt=0;persist();render();message(`Повторный скан не совпал с ${first}. Первый код отброшен; новый код проверяется заново.`,true);
 }
 const now=Date.now();
 if(code===s.lastCode&&now-s.lastAt<1000){message('Повторный скан этого товара заблокирован на 1 секунду');return;}
 s.lastCode=code;s.lastTransport=transport;s.lastAt=now;
 if(s.queue.length>=80){core.feedback('error','Дождитесь сохранения очереди');return;}
 s.queue.push({id:uuid(),code,occurred_at:new Date(now).toISOString()});persist();freeze();process();
}
async function start(){
 if(s.busy||s.fault||s.pending)return;
 s.busy=true;freeze();
 try{
  if(!contextOK())throw Error('Клиент, склад или сотрудник изменены. Вернитесь на главную и откройте раздел заново.');
  const row=$('workRow').value.trim();if(row&&(!/^\d+$/.test(row)||Number(row)<1||Number(row)>999))throw Error('Ряд: целое число от 1 до 999');
  const receipt=s.mode==='RECEIVE'?$('workReceiptNumber').value.trim():'';if(s.mode==='RECEIVE'&&!receipt){message('Укажите номер или название приёмки',true);$('workReceiptNumber').focus();return;}const body={client_id:s.context.client_id,warehouse_id:s.context.warehouse_id,picker:s.context.picker,mode:s.mode,request_id:uuid(),code:$('workPlaceScan').value.trim(),target_type:$('workTargetType').value,receipt_number:receipt,
   section_code:s.mode==='RECEIVE'?$('workSection').value.trim():'',placement_code:s.mode==='RECEIVE'?$('workPlacement').value.trim():'',place_empty_box:s.mode==='RECEIVE'&&$('workPlaceEmpty').checked,row:s.mode==='RECEIVE'&&row?Number(row):null};
  if(!body.code)throw Error('Сканируйте короб или ячейку');
  s.pending={path:'/tsd/mobile/work/start',body};persist();
  s.session=await request(s.pending.path,body);s.pending=null;s.queue=[];s.panelMode='expanded';persist();render();enterSessionView(false);message('Место открыто. Сканируйте товар.');$('workProductScan').focus();
 }catch(e){handleError(e);}finally{s.busy=false;freeze();}
}
async function changePlace(raw){
 const code=String(raw||'').replace(/[\r\n\t]/g,'').trim();if(!code){message('Сканируйте правильный короб или адрес',true);return;}
 if(s.busy||s.fault||s.unknown||s.pending||s.queue.length||!s.session||s.session.status!=='DRAFT')return;
 s.busy=true;freeze();
 try{
  const count=s.mode==='COUNT';
  if(count&&s.session?.lines?.some(x=>x.quantity!==null)&&!confirm('Инвентаризация уже начата. При смене места текущий пересчёт этого черновика будет сброшен и загрузится содержимое нового места. Продолжить?'))return;
  await mutate('CHANGE_PLACE',{place_code:code,target_type:'AUTO',section_code:s.mode==='RECEIVE'?$('workSection').value.trim():'',placement_code:'',place_empty_box:false,row:null});
  s.changingPlace=false;$('workChangePlaceScan').value='';render();message('Правильный короб / место сохранено.');core.feedback('ok','Место изменено');
  setTimeout(()=>$('workProductScan').focus(),0);
 }catch(e){
  if(e.httpStatus&&e.httpStatus<500){s.pending=null;s.fault=false;persist();render();message(e.message,true);core.feedback('error',e.message);}
  else handleError(e);
 }finally{s.busy=false;freeze();}
}
function draftPlace(x){return x?.place?.container||x?.place?.location||'Без места';}
function renderDrafts(rows){
 const host=$('workDrafts'),receipts=new Map(),counts=[],selected=String(s.context?.picker||'').trim();
 for(const x of (rows||[])){
  if(x.mode==='COUNT'){counts.push(x);continue;}
  const number=String(x.receipt_number||'').trim()||'\u0411\u0435\u0437 \u043d\u043e\u043c\u0435\u0440\u0430';
  if(!receipts.has(number))receipts.set(number,[]);
  receipts.get(number).push(x);
 }
 const blocks=[];
 for(const [number,items] of receipts){
  const totalUnits=items.reduce((a,x)=>a+Number(x.units||0),0),totalBoxes=items.length,staff=new Map();
  for(const x of items){const receiver=String(x.picker||'').trim()||'\u0411\u0435\u0437 \u0438\u043c\u0435\u043d\u0438';if(!staff.has(receiver))staff.set(receiver,[]);staff.get(receiver).push(x);}
  const staffBlocks=[];
  for(const [receiver,staffItems] of staff){
   const mine=receiver===selected,units=staffItems.reduce((a,x)=>a+Number(x.units||0),0),boxes=staffItems.length;
   const cards=staffItems.map(x=>mine?`<button type="button" class="secondary big work-receipt-box" data-work-open="${esc(x.id)}"><span><b>${esc(draftPlace(x))}</b><small>${Number(x.units||0)} \u0448\u0442.</small></span></button>`:`<div class="secondary big work-receipt-box work-receipt-box-readonly"><span><b>${esc(draftPlace(x))}</b><small>${Number(x.units||0)} \u0448\u0442. \u00b7 \u0447\u0443\u0436\u043e\u0439 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442</small></span></div>`).join('');
   const mineActions=mine?`<div class="work-receiver-actions"><button type="button" class="secondary big" data-work-receipt-export-person="${esc(number)}" data-work-receipt-picker="${esc(receiver)}">Excel \u00b7 \u043c\u043e\u0439 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442</button><button type="button" class="primary big work-receipt-post" data-work-receipt-post="${esc(number)}" data-work-receipt-picker="${esc(receiver)}">\u0417\u0430\u0432\u0435\u0440\u0448\u0438\u0442\u044c \u043c\u043e\u0439 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442</button></div>`:'';
   staffBlocks.push(`<details class="work-receiver-group" ${mine?'open':''}><summary><span><b>${esc(receiver)}${mine?' \u00b7 \u044f':''}</b><small>${boxes} ${boxes===1?'\u043a\u043e\u0440\u043e\u0431':'\u043a\u043e\u0440\u043e\u0431\u043e\u0432'} \u00b7 ${units} \u0448\u0442.</small></span></summary><div class="work-receipt-boxes">${cards}</div>${mineActions}</details>`);
  }
  blocks.push(`<details class="work-receipt-group" open><summary><span><b>\u041f\u0440\u0438\u0451\u043c\u043a\u0430 \u00b7 ${esc(number)}</b><small>${totalBoxes} \u043a\u043e\u0440\u043e\u0431\u043e\u0432 \u00b7 ${totalUnits} \u0448\u0442. \u00b7 \u043f\u0440\u0438\u0451\u043c\u0449\u0438\u043a\u043e\u0432: ${staff.size}</small></span></summary>${staffBlocks.join('')}<button type="button" class="secondary big" data-work-receipt-export="${esc(number)}">Excel \u00b7 \u0432\u0441\u044f \u043f\u0440\u0438\u0451\u043c\u043a\u0430 \u043f\u043e \u0432\u0441\u0435\u043c \u043f\u0440\u0438\u0451\u043c\u0449\u0438\u043a\u0430\u043c</button></details>`);
 }
 if(counts.length)blocks.push(`<div class="work-count-drafts"><b>\u0418\u043d\u0432\u0435\u043d\u0442\u0430\u0440\u0438\u0437\u0430\u0446\u0438\u044f</b>${counts.map(x=>`<button type="button" class="secondary big" data-work-open="${esc(x.id)}">\u041f\u0435\u0440\u0435\u0441\u0447\u0451\u0442 \u00b7 ${esc(draftPlace(x))} \u00b7 ${Number(x.units||0)} \u0448\u0442.</button>`).join('')}</div>`);
 host.innerHTML=blocks.join('')||'<p>\u0421\u043e\u0445\u0440\u0430\u043d\u0451\u043d\u043d\u044b\u0445 \u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a\u043e\u0432 \u043d\u0435\u0442.</p>';
}
async function fetchDrafts(){
 const result=await core.api('/tsd/mobile/work/drafts?'+new URLSearchParams({client_id:s.context.client_id,warehouse_id:s.context.warehouse_id,picker:s.context.picker}));
 renderDrafts(Array.isArray(result?.rows)?result.rows:[]);return result;
}
async function drafts(){
 if(s.busy)return;s.busy=true;freeze();
 try{await fetchDrafts();}catch(e){message(e.message,true);}finally{s.busy=false;freeze();}
}
async function receiptExcel(receipt,picker=''){
 const number=String(receipt||'').trim(),receiver=String(picker||'').trim();if(!number)throw new Error('\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u043d\u043e\u043c\u0435\u0440 \u043f\u0440\u0438\u0451\u043c\u043a\u0438');
 const params={client_id:s.context.client_id,warehouse_id:s.context.warehouse_id,receipt_number:number};if(receiver)params.picker=receiver;
 const url='/tsd/mobile/work/receipt-batch/export.xlsx?'+new URLSearchParams(params);
 message(receiver?'\u0424\u043e\u0440\u043c\u0438\u0440\u0443\u0435\u043c Excel \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u0430 '+receiver+'\u2026':'\u0424\u043e\u0440\u043c\u0438\u0440\u0443\u0435\u043c Excel \u0432\u0441\u0435\u0439 \u043f\u0440\u0438\u0451\u043c\u043a\u0438\u2026');
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),180000);
 try{
  const r=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal});if(r.status===401){location.href='/tsd/login';return;}if(!r.ok){let detail='';try{detail=await r.text();}catch(_){}throw Error('\u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0432\u044b\u0433\u0440\u0443\u0437\u0438\u0442\u044c \u043f\u0440\u0438\u0451\u043c\u043a\u0443: HTTP '+r.status+(detail?' \u00b7 '+detail.slice(0,240):''));}
  const blob=await r.blob();if(blob.size<100)throw Error('\u0421\u0435\u0440\u0432\u0435\u0440 \u0432\u0435\u0440\u043d\u0443\u043b \u043f\u0443\u0441\u0442\u043e\u0439 Excel');
  const safe=number.replace(/[^A-Za-z0-9._-]+/g,'_').slice(0,60)||'receipt',safeReceiver=receiver.replace(/[^A-Za-z0-9._-]+/g,'_').slice(0,40),name='\u041f\u0440\u0438\u0435\u043c\u043a\u0430_'+safe+(safeReceiver?'_'+safeReceiver:'_ALL')+'_'+new Date().toISOString().slice(0,10)+'.xlsx',u=URL.createObjectURL(blob);
  const browserDownload=()=>{const a=document.createElement('a');a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();};
  if(window.TsdNative&&typeof window.TsdNative.saveFile==='function'){const reader=new FileReader();reader.onload=()=>{try{window.TsdNative.saveFile(String(reader.result).split(',')[1],name,blob.type||'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');}catch(_){browserDownload();}};reader.onerror=browserDownload;reader.readAsDataURL(blob);}else browserDownload();
  message(receiver?`Excel: ${receiver} \u00b7 \u043f\u0440\u0438\u0451\u043c\u043a\u0430 ${number} \u0441\u0444\u043e\u0440\u043c\u0438\u0440\u043e\u0432\u0430\u043d.`:`Excel \u043f\u0440\u0438\u0451\u043c\u043a\u0438 ${number} \u0441\u0444\u043e\u0440\u043c\u0438\u0440\u043e\u0432\u0430\u043d: \u0432\u0441\u0435 \u043f\u0440\u0438\u0451\u043c\u0449\u0438\u043a\u0438 \u0440\u0430\u0437\u0434\u0435\u043b\u0435\u043d\u044b.`);setTimeout(()=>URL.revokeObjectURL(u),120000);
 }finally{clearTimeout(timer);}
}
async function postReceipt(receipt){
 const number=String(receipt||'').trim(),receiver=String(s.context?.picker||'').trim();if(!number)throw new Error('\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u043d\u043e\u043c\u0435\u0440 \u043f\u0440\u0438\u0451\u043c\u043a\u0438');if(!receiver)throw new Error('\u0423\u043a\u0430\u0436\u0438\u0442\u0435 \u043f\u0440\u0438\u0451\u043c\u0449\u0438\u043a\u0430');
 if(s.queue.length)throw new Error('\u0414\u043e\u0436\u0434\u0438\u0442\u0435\u0441\u044c \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0438\u044f \u0441\u043a\u0430\u043d\u043e\u0432');
 const r=await core.api('/tsd/mobile/work/receipt-batch/post',{method:'POST',body:JSON.stringify({client_id:s.context.client_id,warehouse_id:s.context.warehouse_id,receipt_number:number,picker:receiver}),timeoutMs:300000});
 core.feedback('ok','\u0412\u0430\u0448 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u043f\u0440\u0438\u0451\u043c\u043a\u0438 \u0432\u044b\u0433\u0440\u0443\u0436\u0435\u043d');
 const empty=Number(r.ignored_empty||0);message(`\u041f\u0440\u0438\u0451\u043c\u0449\u0438\u043a ${receiver} \u00b7 \u043f\u0440\u0438\u0451\u043c\u043a\u0430 ${number}: ${r.boxes||0} \u043a\u043e\u0440\u043e\u0431\u043e\u0432 \u00b7 ${r.units||0} \u0448\u0442. \u0432\u044b\u0433\u0440\u0443\u0436\u0435\u043d\u043e.${empty?` \u041f\u0443\u0441\u0442\u044b\u0445 \u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a\u043e\u0432 \u0437\u0430\u043a\u0440\u044b\u0442\u043e: ${empty}.`:''} \u0420\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442\u044b \u0434\u0440\u0443\u0433\u0438\u0445 \u043f\u0440\u0438\u0451\u043c\u0449\u0438\u043a\u043e\u0432 \u043d\u0435 \u0437\u0430\u0442\u0440\u043e\u043d\u0443\u0442\u044b.`);
 s.session=null;s.queue=[];s.unknown=null;s.fault=false;s.changingPlace=false;$('workPlaceScan').value='';
 if($('workReceiptNumber').value.trim()===number)$('workReceiptNumber').value='';
 render();await fetchDrafts();
 try{localStorage.setItem('fc_stock_change',String(Date.now()));window.dispatchEvent(new Event('fc:stock-changed'));}catch(_){}
 return r;
}
async function open(mode){
 if(!core.ready)return;core.saveSetup();
 const context=core.context();if(!context.client_id||!context.warehouse_id||!context.picker){$('setupMsg').textContent='Выберите клиента, склад и укажите сотрудника';return;}
 if(s.pending||s.queue.length||s.unknown){if(!contextOK()){$('setupMsg').textContent='Есть несохранённый скан. Верните прежнего клиента, склад и сотрудника.';return;}core.show('work');render();return;}
 s.mode=mode;s.context=context;s.session=null;s.fault=false;s.unknown=null;s.queue=[];s.buffer='';s.changingPlace=false;s.panelMode='expanded';
 $('workPlaceScan').value='';$('workDrafts').innerHTML='';message('');core.show('work');render();persist();$('workPlaceScan').focus();
 await drafts();
}
async function restore(saved){
 s.mode=saved.mode||'RECEIVE';s.context=saved.context;s.pending=saved.pending||null;s.queue=saved.queue||[];s.unknown=saved.unknown||null;s.changingPlace=!!saved.changingPlace;s.panelMode=saved.panelMode==='half'?'half':'expanded';
 for(const [id,v] of Object.entries(saved.form||{}))if($(id))$(id).value=v;
 $('workPlaceEmpty').checked=!!saved.placeEmpty;
 if(!contextOK()){s.fault=true;core.show('setup');$('setupMsg').textContent='Есть черновик другого клиента/склада. Верните прежний контекст для продолжения.';return;}
 if(saved.id){try{s.session=await core.api(`/tsd/mobile/work/${saved.id}?${new URLSearchParams({client_id:s.context.client_id,warehouse_id:s.context.warehouse_id})}`);}catch(e){s.session={id:saved.id,mode:s.mode,status:'DRAFT',version:0,lines:[],units:0,unchecked:0,place:{location:'Черновик',ancestors:[]}};s.fault=true;message(e.message,true);}}
 if(s.pending||s.queue.length){s.fault=true;message('Есть незавершённая отправка. Нажмите «Восстановить связь и продолжить».',true);}
 render();if(s.session)enterSessionView(false);persist();
}
async function action(fn){if(s.busy||s.fault||s.unknown||s.pending||s.queue.length)return;s.busy=true;freeze();try{await fn();}catch(e){handleError(e);}finally{s.busy=false;freeze();}}
$('workLines').addEventListener('click',e=>{
 const b=e.target.closest('[data-work-action]'),card=b?.closest('[data-work-sku]');if(!b||!card||b.disabled)return;
 const sid=card.dataset.workSku,row=s.session.lines.find(r=>r.sku_id===sid);if(!row)return;
 const a=b.dataset.workAction;
 action(async()=>{
  if(a==='minus'){if((row.quantity||0)>0)await mutate('SET',{sku_id:sid,quantity:row.quantity-1,occurred_at:new Date().toISOString()});}
  else if(a==='zero')await mutate('SET',{sku_id:sid,quantity:0,occurred_at:new Date().toISOString()});
  else if(a==='remove')await mutate('REMOVE',{sku_id:sid,occurred_at:new Date().toISOString()});
  else {const value=prompt('Фактически '+(s.mode==='COUNT'?'в этом месте':'принимаем')+' (шт.)',row.quantity===null?'':String(row.quantity));if(value===null)return;if(!/^\d+$/.test(value.trim())||Number(value)>1000000){message('Введите целое количество от 0 до 1000000',true);return;}await mutate('SET',{sku_id:sid,quantity:Number(value),occurred_at:new Date().toISOString()});}
  message('Количество исправлено');
 });
});
$('workUndo').onclick=()=>action(async()=>{await mutate('UNDO');message('Последний скан убран');});
$('workCancel').onclick=()=>action(async()=>{if(confirm('Удалить этот черновик? Остатки по нему ещё не менялись.')){await mutate('CANCEL');message('Черновик удалён');}});
$('workPost').onclick=()=>action(async()=>{if(s.mode==='COUNT'){await mutate('POST');core.feedback('ok','Остатки сохранены');message('Остатки и история обновлены.');try{localStorage.setItem('fc_stock_change',String(Date.now()));window.dispatchEvent(new Event('fc:stock-changed'));}catch(_){} }else{await postReceipt($('workReceiptNumber').value.trim()||s.session?.receipt_number||'');}});
$('workNext').onclick=()=>{if(s.mode==='RECEIVE'&&s.session?.status==='DRAFT'&&s.queue.length){message('Дождитесь сохранения сканов',true);return;}s.session=null;s.queue=[];s.unknown=null;s.fault=false;s.changingPlace=false;$('workPlaceScan').value='';$('workPlaceEmpty').checked=false;message(s.mode==='RECEIVE'?`Приёмка ${$('workReceiptNumber').value.trim()}: сканируйте следующий короб / место`:'Сканируйте следующее место');render();persist();$('workPlaceScan').focus();};
$('workChangePlace').onclick=()=>{if(!s.session||s.session.status!=='DRAFT'||s.busy||s.pending||s.queue.length)return;s.changingPlace=true;$('workChangePlaceScan').value='';render();message('Сканируйте правильный короб или место.');setTimeout(()=>$('workChangePlaceScan').focus(),0);};
$('workChangePlaceCancel').onclick=()=>{if(s.busy)return;s.changingPlace=false;$('workChangePlaceScan').value='';render();message('Смена места отменена');setTimeout(()=>$('workProductScan').focus(),0);};
$('workChangePlaceApply').onclick=()=>changePlace($('workChangePlaceScan').value);
$('workChangePlaceScan').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key==='Tab'){e.preventDefault();changePlace(e.currentTarget.value);}});
$('workChangePlaceScan').addEventListener('change',e=>{if(e.currentTarget.value)changePlace(e.currentTarget.value);});
$('workStart').onclick=start;$('workDraftRefresh').onclick=drafts;$('workRetry').onclick=recover;
if($('workRetryInline'))$('workRetryInline').onclick=recover;
if($('workReloadInline'))$('workReloadInline').onclick=emergencyReset;
if($('workPanicBtn'))$('workPanicBtn').onclick=emergencyReset;
$('workScanSend').onclick=()=>enqueue($('workProductScan').value);
$('workDrafts').addEventListener('click',async e=>{
 const personal=e.target.closest('[data-work-receipt-export-person]');
 if(personal){if(s.busy)return;s.busy=true;freeze();try{await receiptExcel(personal.dataset.workReceiptExportPerson,personal.dataset.workReceiptPicker||s.context.picker);}catch(err){handleError(err);}finally{s.busy=false;freeze();}return;}
 const exp=e.target.closest('[data-work-receipt-export]');
 if(exp){if(s.busy)return;s.busy=true;freeze();try{await receiptExcel(exp.dataset.workReceiptExport);}catch(err){handleError(err);}finally{s.busy=false;freeze();}return;}
 const post=e.target.closest('[data-work-receipt-post]');
 if(post){if(s.busy)return;const number=post.dataset.workReceiptPost,receiver=post.dataset.workReceiptPicker||s.context.picker;if(receiver!==s.context.picker){message('\u041c\u043e\u0436\u043d\u043e \u0437\u0430\u0432\u0435\u0440\u0448\u0430\u0442\u044c \u0442\u043e\u043b\u044c\u043a\u043e \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u043e\u0433\u043e \u043f\u0440\u0438\u0451\u043c\u0449\u0438\u043a\u0430.',true);return;}if(!confirm(`\u0417\u0430\u0432\u0435\u0440\u0448\u0438\u0442\u044c \u0442\u043e\u043b\u044c\u043a\u043e \u0432\u0430\u0448 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442: ${receiver} \u00b7 \u043f\u0440\u0438\u0451\u043c\u043a\u0430 ${number}? \u041a\u043e\u0440\u043e\u0431\u0430 \u0434\u0440\u0443\u0433\u0438\u0445 \u043f\u0440\u0438\u0451\u043c\u0449\u0438\u043a\u043e\u0432 \u043d\u0435 \u0431\u0443\u0434\u0443\u0442 \u0437\u0430\u043a\u0440\u044b\u0442\u044b.`))return;s.busy=true;freeze();try{await postReceipt(number);}catch(err){handleError(err);}finally{s.busy=false;freeze();}return;}
 const b=e.target.closest('[data-work-open]');if(!b||s.busy)return;s.busy=true;freeze();
 try{s.session=await core.api(`/tsd/mobile/work/${b.dataset.workOpen}?${new URLSearchParams({client_id:s.context.client_id,warehouse_id:s.context.warehouse_id})}`);if(String(s.session?.picker||'').trim()!==String(s.context?.picker||'').trim())throw new Error('\u042d\u0442\u043e \u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a \u0434\u0440\u0443\u0433\u043e\u0433\u043e \u043f\u0440\u0438\u0451\u043c\u0449\u0438\u043a\u0430. \u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0435\u0433\u043e \u0438\u043c\u044f \u0432 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430\u0445, \u0435\u0441\u043b\u0438 \u043d\u0443\u0436\u043d\u043e \u043f\u0440\u043e\u0434\u043e\u043b\u0436\u0438\u0442\u044c \u0435\u0433\u043e \u0440\u0430\u0431\u043e\u0442\u0443.');s.mode=s.session.mode;s.fault=false;s.queue=[];s.panelMode='expanded';if(s.mode==='RECEIVE'&&s.session.receipt_number)$('workReceiptNumber').value=s.session.receipt_number;persist();render();enterSessionView(false);$('workProductScan').focus();}catch(err){s.session=null;handleError(err);}finally{s.busy=false;freeze();}
});
$('workRegister').onclick=async()=>{
 if(s.busy||!s.unknown)return;if(!s.unknown.confirmed){message('Сначала повторно отсканируйте этот же ШК. Без второго совпадающего скана новый ШК не создаётся.',true);focusWorkScanner();return;}
 const product={article:$('workNewArticle').value.trim(),size:$('workNewSize').value.trim(),name:$('workNewName').value.trim(),barcode_confirmed:true};if(!product.article||!product.size){message('Укажите артикул и размер',true);return;}
 const u=s.unknown;if(!confirm(`Создать новый локальный ШК ${u.barcode} для ${product.article} · ${product.size}?\n\nШК уже был отсканирован дважды одинаково.`))return;
 s.busy=true;freeze();try{await mutate('SCAN',{barcode:u.barcode,product,occurred_at:u.pending?.body?.occurred_at||new Date().toISOString()},u.pending.scan);s.unknown=null;s.fault=false;persist();render();message('Новый ШК создан и скан учтён');}catch(e){handleError(e);}finally{s.busy=false;freeze();}if(!s.fault&&!s.unknown)process();
};
$('workSkipUnknown').onclick=()=>{if(s.busy||!s.unknown)return;if(s.queue[0]?.id===s.unknown.pending.scan)s.queue.shift();s.unknown=null;s.fault=false;persist();render();message('Ошибочный скан убран');process();};
function scan(raw,transport='native'){
 if(!active())return;
 if(s.session?.status==='DRAFT'&&s.changingPlace){$('workChangePlaceScan').value=String(raw||'').trim();changePlace(raw);return;}
 if(s.session?.status==='DRAFT'){enqueue(raw,transport);return;}
 let el=document.activeElement;
 if(!['workSection','workPlacement','workPlaceScan'].includes(el?.id))el=$('workPlaceScan');
 el.value=String(raw||'').trim();persist();
 if(el.id==='workPlaceScan')start();else (el.id==='workSection'?$('workPlacement'):$('workPlaceScan')).focus();
}
for(const id of ['workSection','workPlacement','workPlaceScan'])$(id).addEventListener('keydown',e=>{if(e.key==='Enter'||e.key==='Tab'){e.preventDefault();if(id==='workPlaceScan')start();else $(id==='workSection'?'workPlacement':'workPlaceScan').focus();}});
function finishInput(){clearTimeout(s.timer);const value=$('workProductScan').value||s.buffer;s.buffer='';if(value)enqueue(value);}
$('workProductScan').addEventListener('input',()=>{clearTimeout(s.timer);s.timer=setTimeout(finishInput,160);});
$('workProductScan').addEventListener('change',finishInput);
$('workProductScan').addEventListener('paste',e=>{const v=e.clipboardData?.getData('text');if(v){e.preventDefault();enqueue(v);}});
document.addEventListener('keydown',e=>{
 if(!active()||!s.session||s.session.status!=='DRAFT'||s.unknown||s.fault||s.changingPlace||e.ctrlKey||e.altKey||e.metaKey)return;
 const focused=document.activeElement;if(focused&&focused.tagName==='INPUT'&&focused.id!=='workProductScan')return;
 if(e.key==='Enter'||e.key==='Tab'){if($('workProductScan').value||s.buffer){e.preventDefault();finishInput();}return;}
 if(e.key?.length===1&&focused!==$('workProductScan')){s.buffer+=e.key;clearTimeout(s.timer);s.timer=setTimeout(finishInput,160);}
},true);
for(const id of ['workSection','workPlacement','workPlaceScan','workRow','workTargetType','workPlaceEmpty','workReceiptNumber'])$(id).addEventListener('change',persist);
const sheetHandle=$('workItemsHandle');
if(window.PointerEvent){
 sheetHandle.addEventListener('pointerdown',e=>{if(!s.session)return;beginPanelDrag(e.clientY,e.pointerId);try{sheetHandle.setPointerCapture(e.pointerId);}catch(_){}e.preventDefault();});
 sheetHandle.addEventListener('pointermove',e=>{if(!s.panelDrag||s.panelDrag.pointerId!==e.pointerId)return;movePanelDrag(e.clientY);e.preventDefault();});
 sheetHandle.addEventListener('pointerup',e=>{if(!s.panelDrag||s.panelDrag.pointerId!==e.pointerId)return;endPanelDrag();});
 sheetHandle.addEventListener('pointercancel',()=>endPanelDrag());
}else{
 sheetHandle.addEventListener('touchstart',e=>{const t=e.touches?.[0];if(!t||!s.session)return;beginPanelDrag(t.clientY);e.preventDefault();},{passive:false});
 sheetHandle.addEventListener('touchmove',e=>{const t=e.touches?.[0];if(!t||!s.panelDrag)return;movePanelDrag(t.clientY);e.preventDefault();},{passive:false});
 sheetHandle.addEventListener('touchend',()=>endPanelDrag());sheetHandle.addEventListener('touchcancel',()=>endPanelDrag());
}
sheetHandle.addEventListener('click',()=>{if(!s.session||s.panelIgnoreClick)return;setPanelMode(s.panelMode==='expanded'?'half':'expanded');});
window.addEventListener('blur',()=>{if(s.panelDrag){s.panelDrag=null;$('workItemsPanel')?.classList.remove('work-items-dragging');applyPanel();}});
window.addEventListener('pageshow',()=>{s.busy=false;s.panelDrag=null;s.panelIgnoreClick=false;try{$('workItemsPanel')?.classList.remove('work-items-dragging');}catch(_){}freeze();if(active()&&!s.fault&&!s.unknown)focusWorkScanner();});
const resizeSheet=()=>{if(s.session&&!s.panelDrag)applyPanel();};window.addEventListener('resize',resizeSheet);window.visualViewport?.addEventListener?.('resize',resizeSheet);

const historyLabels={RECEIVE:'Приёмка товара',COUNT:'Инвентаризация',MOVE:'Перемещение',PICK:'Сборка'};
function historyDate(v){try{return new Date(v).toLocaleString('ru-RU');}catch(_){return String(v||'');}}
function groupedHistory(rows){
 const out=[],receipts=new Map();
 for(const r of rows){
  const number=String(r.receipt_number||'').trim();
  if(r.kind!=='RECEIVE'||!number){out.push(r);continue;}
  const key=[number,r.picker||'',r.owner||'',r.status||''].join('\u0001');
  let g=receipts.get(key);
  if(!g){g={...r,boxes:[],items:[],units:0,updated_at:r.updated_at||r.created_at,first_item_at:r.first_item_at||null,last_item_at:r.last_item_at||null};receipts.set(key,g);out.push(g);}
  g.units+=Number(r.units||0);g.boxes.push({place:r.place||r.address||'Без места',units:Number(r.units||0),items:r.items||[],first_item_at:r.first_item_at||null,last_item_at:r.last_item_at||null});
  for(const x of (r.items||[]))g.items.push(x);
  if(new Date(r.updated_at||r.created_at||0)>new Date(g.updated_at||0))g.updated_at=r.updated_at||r.created_at;
  if(r.first_item_at&&(!g.first_item_at||new Date(r.first_item_at)<new Date(g.first_item_at)))g.first_item_at=r.first_item_at;
  if(r.last_item_at&&(!g.last_item_at||new Date(r.last_item_at)>new Date(g.last_item_at)))g.last_item_at=r.last_item_at;
 }
 return out;
}
async function historyLoad(){
 core.saveSetup();const c=core.context();if(!c.client_id||!c.warehouse_id){$('setupMsg').textContent='Выберите клиента и склад';return;}
 $('workHistoryMsg').textContent='Загружаем историю…';
 try{const data=await core.api('/tsd/mobile/work/history/list?'+new URLSearchParams({client_id:c.client_id,warehouse_id:c.warehouse_id,limit:'300'}),{timeoutMs:60000});const raw=Array.isArray(data?.rows)?data.rows:[],rows=groupedHistory(raw);
  $('workHistoryRows').innerHTML=rows.map(r=>`<article class="work-history-card"><div><b>${esc(historyLabels[r.kind]||r.kind||'ТСД')} · ${esc(r.receipt_number||r.reference||'')}</b><small>${esc(historyDate(r.last_item_at||r.updated_at||r.created_at))}</small></div><p><b>Сотрудник:</b> ${esc(r.picker||'—')}<br><b>Учётная запись:</b> ${esc(r.owner||'—')}</p><p>${r.kind==='RECEIVE'&&r.boxes?`${r.boxes.length} коробов · ${Number(r.units||0)} шт.`:`${esc(r.place||r.address||'Без места')} · ${Number(r.units||0)} шт.`} · ${esc(r.status||'')}</p>${r.kind==='RECEIVE'&&r.boxes?.length?`<details><summary>Короба этой приёмки</summary>${r.boxes.map(b=>`<div class="work-history-box"><b>${esc(b.place)}</b> · ${Number(b.units||0)} шт.${b.items?.length?`<div>${b.items.map(x=>`${esc(x.article||x.barcode)} · ${esc(x.size||'—')} — <b>${Number(x.quantity||0)} шт.</b>`).join('<br>')}</div>`:''}</div>`).join('')}</details>`:r.kind==='RECEIVE'&&r.items?.length?`<details><summary>Товары этой приёмки / места</summary>${r.items.map(x=>`<div>${esc(x.article||x.barcode)} · ${esc(x.size||'—')} · ${esc(x.barcode||'')} — <b>${Number(x.quantity||0)} шт.</b></div>`).join('')}</details>`:''}</article>`).join('')||'<p class="work-note">Завершённых действий ТСД пока нет.</p>';
  $('workHistoryMsg').textContent=`Операций: ${rows.length}`;$('workHistoryMsg').className='msg';
 }catch(e){$('workHistoryMsg').textContent=e.message;$('workHistoryMsg').className='msg error';}
}
async function historyOpen(){core.saveSetup();const c=core.context();if(!c.client_id||!c.warehouse_id){$('setupMsg').textContent='Выберите клиента и склад';return;}core.show('workhistory');$('workHistoryMsg').className='msg';await historyLoad();}
async function historyExcel(){
 const c=core.context();if(!c.client_id||!c.warehouse_id)return;const url='/tsd/mobile/work/history/export.xlsx?'+new URLSearchParams({client_id:c.client_id,warehouse_id:c.warehouse_id});
 $('workHistoryMsg').textContent='Формируем Excel…';
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),120000);
 try{const r=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal});if(r.status===401){location.href='/tsd/login';return;}if(!r.ok){let detail='';try{detail=await r.text();}catch(_){}throw Error('Не удалось сформировать Excel: HTTP '+r.status+(detail?' · '+detail.slice(0,240):''));}const blob=await r.blob(),name='История_ТСД_'+new Date().toISOString().slice(0,10)+'.xlsx';
  if(blob.size<100)throw Error('Сервер вернул пустой Excel');
  const u=URL.createObjectURL(blob);
  const browserDownload=()=>{const a=document.createElement('a');a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();};
  if(window.TsdNative&&typeof window.TsdNative.saveFile==='function'){const reader=new FileReader();reader.onload=()=>{try{window.TsdNative.saveFile(String(reader.result).split(',')[1],name,blob.type||'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');}catch(_){browserDownload();}};reader.onerror=browserDownload;reader.readAsDataURL(blob);}else browserDownload();
  $('workHistoryMsg').innerHTML='Excel сформирован. <a id="workHistoryDownloadAgain" href="'+u+'" download="'+name+'">Скачать ещё раз</a>';
  setTimeout(()=>{const a=document.getElementById('workHistoryDownloadAgain');if(!a)URL.revokeObjectURL(u);},120000);
 }catch(e){$('workHistoryMsg').textContent=e.name==='AbortError'?'Excel формируется слишком долго. Повторите выгрузку.':e.message;$('workHistoryMsg').className='msg error';}finally{clearTimeout(timer);}
}
$('openWorkHistory').onclick=historyOpen;$('workHistoryRefresh').onclick=historyLoad;$('workHistoryExcel').onclick=historyExcel;
window.FCTsdWork={open,restore,snapshot,scan,setPanelMode,emergencyReset};
})();
