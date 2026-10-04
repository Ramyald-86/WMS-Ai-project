/* FC_R145F8_TSD_BOXES_FROM_ACTUAL_R112_BASE */
(()=>{
/* FC_K9R49_TSD_ASSIGNMENT_LATEST */
/* FC_K9R62_WAREHOUSE_TSD_ADMIN_CLOSE */
/* FC_K9R26_TSD_ANDROID_APP_RESUME_STATE */
/* FC_K9R25_TSD_DYNAMIC_BATCH_WEDGE_CAPTURE */
/* FC_K9R24_TSD_SCANNER_WEDGE_COMPAT */
const $=id=>document.getElementById(id);
const state={
  session:null,client:null,warehouse:null,picker:'',
  verifiedLocationId:'',verifiedSourceKey:'',
  scanBusy:false,pendingScan:'',cellListOpen:false,currentScreen:'setup',
  assignmentPollBusy:false,lastAssignmentId:'',revision:0,mutationBusy:false,scanQueue:[],scanTransport:'',moveRequest:null,scanNeedsSync:false,pendingRecovery:null,reconnectBusy:false,networkOnline:navigator.onLine!==false,initializing:true
};
const scanner={buffer:'',bufferTimer:null,inputTimer:null,lastCode:'',lastAt:0,audio:null,lastInputValue:'',pollTimer:null};
// FC_R145F20_RECEIVING_SCREEN_ISOLATION: receiving is a first-class screen.
const screens=['setup','pick','move','work','workhistory','transit','boxreceive','receivegoods'];

function scopeKey(){return [$('clientSelect')?.value||'',$('warehouseSelect')?.value||'',$('pickerInput')?.value.trim()||'',$('assignmentKind')?.value||'AUTO'].join('|');}
function normalizeBoxScan(raw){const ru='ёйцукенгшщзхъфывапролджэячсмитьбюЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ',en='`qwertyuiop[]asdfghjkl;\'zxcvbnm,.~QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>';return String(raw||'').split('').map(c=>ru.includes(c)?en[ru.indexOf(c)]:c).join('');}
function lockActions(value){for(const id of ['clientSelect','warehouseSelect','pickerInput','assignmentKind','manualPickBtn','skipPickBtn','missingBtn','confirmYes','finishPick','moveGo','homeBtn','cancelPick','startPick','openMove','openBoxes','openGoods','openBoxPicks','openStocks','openCount','openWorkHistory','openTransit'])if($(id))$(id).disabled=value;}
async function singleAction(fn){if(state.mutationBusy||state.scanBusy)return;state.mutationBusy=true;state.revision++;lockActions(true);try{await fn();}finally{state.revision++;state.mutationBusy=false;lockActions(false);}}
function nativeCall(name,...args){
  try{const n=window.TsdNative;if(n&&typeof n[name]==='function')return n[name](...args);}catch(_){}
  return null;
}
function runtimeSnapshot(){
  const current=state.session?.current||null;
  return {
    version:1,sessionId:state.session?.session?.id||'',client:state.client||$('clientSelect')?.value||'',
    warehouse:state.warehouse||$('warehouseSelect')?.value||'',picker:state.picker||$('pickerInput')?.value?.trim()||'',
    verifiedLocationId:state.verifiedLocationId||'',verifiedSourceKey:state.verifiedSourceKey||'',
    taskKey:current?.task_key||'',locationId:current?.location_id||'',sourceKey:current?sourceKey(current):'',
    screen:state.currentScreen||'setup',cellListOpen:!!state.cellListOpen,
    moveSource:$('moveSource')?.value||'',moveProduct:$('moveProduct')?.value||'',moveDest:$('moveDest')?.value||'',moveQty:$('moveQty')?.value||'1',assignmentKind:$('assignmentKind')?.value||'AUTO',moveStockClass:$('moveStockClass')?.value||'MAIN',
    moveRequest:state.moveRequest,pendingRecovery:state.pendingRecovery,work:window.FCTsdWork?.snapshot()||state.workSaved||null,transit:window.FCTsdTransit?.snapshot()||state.transitSaved||null,boxes:window.FCTsdBoxes?.snapshot()||state.boxesSaved||null,receivegoods:window.FCTsdReceiving?.snapshot()||state.receivingSaved||null,savedAt:Date.now()
  };
}
function persistRuntime(){
  try{const raw=JSON.stringify(runtimeSnapshot());try{localStorage.fcTsdRuntime=raw;}catch(e){console.warn('FC_TSD_BROWSER_STORAGE_FULL',e);}nativeCall('saveRuntimeState',raw);}catch(_){}
}
function clearRuntime(){
  try{delete localStorage.fcTsdRuntime;nativeCall('clearRuntimeState');}catch(_){}
}
function readRuntime(){
  try{
    let raw=localStorage.fcTsdRuntime||'';
    if(!raw){const nativeRaw=nativeCall('loadRuntimeState');if(typeof nativeRaw==='string')raw=nativeRaw;}
    return raw?JSON.parse(raw):null;
  }catch(_){return null;}
}
function show(name){
  if(state.currentScreen!==name)state.revision++;
  state.currentScreen=name;
  screens.forEach(x=>$(x)?.classList.toggle('active',x===name));
  if($('workPanicBtn'))$('workPanicBtn').classList.toggle('hidden',name!=='work');
  persistRuntime();
  if(name==='pick') focusScanner();
}
function msg(el,text,type=''){el.textContent=text||'';el.className='msg '+type;}
async function api(path,opt={}){
  const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),opt.timeoutMs||20000);
  try{const r=await fetch(path,{...opt,credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json',...(opt.headers||{})}});
    if(r.status===401){location.href='/tsd/login';throw new Error('Требуется вход');}
    let data=null;const ct=r.headers.get('content-type')||'';
    if(ct.includes('json'))data=await r.json();else data=await r.text();
    if(!r.ok){const e=new Error(typeof data?.detail==='string'?data.detail:(data?.detail?.message||('HTTP '+r.status)));e.httpStatus=r.status;e.detail=data?.detail;throw e;}
    return data;
  }catch(e){if(e.name==='AbortError')throw Error('Сервер не ответил вовремя. Проверьте связь и актуальное состояние операции.');throw e;}finally{clearTimeout(timer);}
}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function num(v){const n=Number(v||0);return Number.isInteger(n)?String(n):n.toFixed(1).replace('.0','');}
function sourceKey(t){return `${t?.location_id||''}|${t?.container_id||''}`;}
function saveSetup(){
  state.revision++;state.scanQueue=[];state.verifiedLocationId='';state.verifiedSourceKey='';
  try{localStorage.fcTsdClient=$('clientSelect').value;}catch(e){console.warn('FC_TSD_SETUP_NOT_SAVED',e);}
  try{localStorage.fcTsdWarehouse=$('warehouseSelect').value;}catch(e){console.warn('FC_TSD_SETUP_NOT_SAVED',e);}
  try{localStorage.fcTsdPicker=$('pickerInput').value.trim();}catch(e){console.warn('FC_TSD_SETUP_NOT_SAVED',e);}
  state.client=$('clientSelect').value;state.warehouse=$('warehouseSelect').value;state.picker=$('pickerInput').value.trim();
  persistRuntime();
}
function isWarehousePick(){return String(state.session?.session?.kind||'').toUpperCase()==='WAREHOUSE';}
async function fetchAssigned(){
  const client=$('clientSelect').value,warehouse=$('warehouseSelect').value,picker=$('pickerInput').value.trim();
  if(!client||!warehouse||!picker)return {session:null,assignment_required:true};
  const q=new URLSearchParams({client_id:client,warehouse_id:warehouse,picker});
  const kind=$('assignmentKind')?.value||'AUTO';
  const wh=()=>api(`/warehouse/tsd/assigned?${q.toString()}`),wb=()=>api(`/tsd/api/picking/assigned?${q.toString()}`);
  if(kind==='WAREHOUSE')return wh();if(kind==='WB')return wb();
  const choices=await Promise.all([wh(),wb()]);
  const time=x=>{const n=Date.parse(x?.session?.assigned_at||x?.session?.created_at||'');return Number.isFinite(n)?n:0;};
  return choices.filter(x=>x?.session).sort((a,b)=>time(b)-time(a))[0]||{session:null,assignment_required:true};
}

function renderAssignmentCard(s){
  const card=$('pickingAvailable');if(!card)return;
  card.classList.remove('empty','waiting','assigned');
  if(!s?.session){
    card.classList.add('waiting');
    card.textContent=$('pickerInput').value.trim()
      ?'Сборка этому сотруднику пока не назначена. Ожидаем назначение из FulfillCore…'
      :'Введите имя сборщика. Сборку назначает диспетчер в FulfillCore.';
    return;
  }
  const p=s.progress||{},at=s.session.assigned_at||s.session.created_at;
  const when=at?new Date(at).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'';
  card.classList.add('assigned');
  const isWh=String(s.session.kind||'').toUpperCase()==='WAREHOUSE';
  card.textContent=`${isWh?'Складское задание':'Назначена актуальная сборка'}${s.session.task_number?` №${s.session.task_number}`:''}: ${num(p.total||0)} шт. · собрано ${num(p.picked||0)} · осталось ${num(p.remaining||0)}${Number(p.missing||0)>0?` · нет в коробе ${num(p.missing)}`:''}${when?` · выдано ${when}`:''}`;
}

function markNetworkState(online,text=''){
  state.networkOnline=!!online;const dot=$('netDot');if(dot)dot.className='dot '+(online?'online':'offline');
  if(text&&$('scannerState'))$('scannerState').textContent=text;
}
function rememberUncertainScan(code,stage,task){
  state.pendingRecovery={code:String(code||''),stage,sessionId:String(state.session?.session?.id||''),taskKey:String(task?.task_key||''),beforeRemaining:Number(task?.remaining||0),locationId:String(task?.location_id||''),sourceKey:sourceKey(task),savedAt:Date.now()};
  state.scanNeedsSync=true;persistRuntime();
}
function pendingWasApplied(fresh,pending){
  if(!fresh?.session)return false;
  if(String(fresh.session.id||'')!==String(pending.sessionId||''))return true;
  const current=fresh.current;
  if(!current)return true;
  if(String(current.task_key||'')!==String(pending.taskKey||''))return true;
  if(pending.stage==='product'&&Number(current.remaining||0)<Number(pending.beforeRemaining||0))return true;
  return false;
}
async function reconcilePendingRecovery(fresh){
  const pending=state.pendingRecovery;if(!pending||state.reconnectBusy)return;
  state.reconnectBusy=true;
  try{
    if(pendingWasApplied(fresh,pending)){
      state.pendingRecovery=null;state.scanNeedsSync=false;persistRuntime();
      msg($('pickMsg'),'Связь восстановлена. Предыдущий скан уже подтверждён сервером.','ok');
      feedback('ok','Скан подтверждён после восстановления связи');return;
    }
    // Location/container verification is idempotent. Product scan is retried only after
    // the fresh server state proves that the first attempt did not reduce remaining qty.
    state.pendingRecovery=null;state.scanNeedsSync=false;persistRuntime();
    msg($('pickMsg'),'Связь восстановлена. Проверяем сохранённый скан…','ok');
    const ok=await onScan(pending.code);
    if(ok!==false){msg($('pickMsg'),'Сохранённый скан обработан после восстановления связи.','ok');feedback('ok','Скан восстановлен автоматически');}
  }finally{state.reconnectBusy=false;persistRuntime();}
}
async function connectionHeartbeat(){
  if(document.hidden)return;
  try{
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),3500);
    const r=await fetch('/health/ready',{cache:'no-store',credentials:'same-origin',signal:controller.signal});clearTimeout(timer);
    if(!r.ok)throw Error('health '+r.status);
    const wasOffline=!state.networkOnline;markNetworkState(true,wasOffline?'Связь восстановлена · сверяем задание…':'');
    if((wasOffline||state.pendingRecovery)&&!state.scanBusy&&!state.mutationBusy)await syncAssignedTask(false);
  }catch(_){markNetworkState(false,'Нет связи · текущий прогресс сохранён на ТСД');}
}
async function syncAssignedTask(userInitiated=false){
  if(['work','transit','boxreceive','receivegoods'].includes(state.currentScreen)||state.initializing||state.assignmentPollBusy||state.scanBusy||state.mutationBusy||document.hidden||!$('confirmModal').classList.contains('hidden'))return Boolean(state.session?.session);
  const client=$('clientSelect').value,warehouse=$('warehouseSelect').value,picker=$('pickerInput').value.trim();
  state.client=client;state.warehouse=warehouse;state.picker=picker;
  if(!client||!warehouse||!picker){renderAssignmentCard(null);return false;}
  state.assignmentPollBusy=true;const captured=scopeKey(),revision=state.revision;
  try{
    if(userInitiated)msg($('setupMsg'),'Проверяем актуальную сборку…');
    const fresh=await fetchAssigned();
    if(['work','transit','boxreceive','receivegoods'].includes(state.currentScreen)||captured!==scopeKey()||revision!==state.revision||state.scanBusy||state.mutationBusy)return false;
    // Do not replace the task behind a partly received barcode. Retry on the next poll.
    if(pickScreenActive()&&($('scanInput')?.value||scanner.buffer))return Boolean(state.session?.session);
    const recovered=state.scanNeedsSync;
    renderAssignmentCard(fresh);
    if(!fresh?.session){
      const had=Boolean(state.session?.session);
      state.session=null;state.lastAssignmentId='';state.verifiedLocationId='';state.verifiedSourceKey='';state.cellListOpen=false;
      if(state.currentScreen!=='move')show('setup');
      persistRuntime();
      if(userInitiated||had)msg($('setupMsg'),'Сборка не назначена. Диспетчер должен выдать её в FulfillCore.',had?'error':'');
      return false;
    }
    const nextId=String(fresh.session.id||''),oldId=String(state.session?.session?.id||'');
    if(nextId!==oldId){
      state.verifiedLocationId='';state.verifiedSourceKey='';state.cellListOpen=false;
      if(oldId)feedback('ok','Получена новая актуальная сборка');
    }
    state.lastAssignmentId=nextId;
    const changed=JSON.stringify([state.session?.session,state.session?.progress,state.session?.current])!==JSON.stringify([fresh.session,fresh.progress,fresh.current]);
    if(state.currentScreen!=='move'&&(changed||userInitiated||recovered)&&!$('scanInput')?.value)render(fresh);else state.session=fresh;
    if(recovered&&state.pendingRecovery)await reconcilePendingRecovery(fresh);
    else if(recovered){state.scanNeedsSync=false;msg($('pickMsg'),'Связь восстановлена. Актуальное задание получено.','ok');}
    msg($('setupMsg'),'');persistRuntime();
    return true;
  }catch(e){
    msg($(state.currentScreen==='pick'?'pickMsg':'setupMsg'),'Не удалось проверить назначение: '+e.message,'error');
    return Boolean(state.session?.session);
  }finally{state.assignmentPollBusy=false;}
}

async function refreshAvailability(){return syncAssignedTask(false);}

async function loadSetup(){
  try{
    const identity=await api('/auth/me');
    if(localStorage.fcTsdIdentity&&localStorage.fcTsdIdentity!==identity.username){clearRuntime();delete localStorage.fcTsdPicker;delete localStorage.fcTsdLastReport;$('lastReport').classList.add('hidden');$('lastReport').removeAttribute('href');}
    try{localStorage.fcTsdIdentity=identity.username;}catch(e){console.warn('FC_TSD_SETUP_NOT_SAVED',e);}
    const [clients,warehouses]=await Promise.all([api('/masterdata/clients'),api('/masterdata/warehouses')]);
    $('clientSelect').innerHTML=clients.map(x=>`<option value="${x.id}">${esc(x.name||x.id)}</option>`).join('');
    $('warehouseSelect').innerHTML=warehouses.map(x=>`<option value="${x.id}">${esc(x.name||x.code||x.id)}</option>`).join('');
    if(clients.some(x=>String(x.id)===localStorage.fcTsdClient))$('clientSelect').value=localStorage.fcTsdClient;
    if(warehouses.some(x=>String(x.id)===localStorage.fcTsdWarehouse))$('warehouseSelect').value=localStorage.fcTsdWarehouse;
    if(localStorage.fcTsdPicker)$('pickerInput').value=localStorage.fcTsdPicker;

  }catch(e){msg($('setupMsg'),e.message,'error');}
}

async function restoreSessionState(){
  const saved=readRuntime();
  state.receivingSaved=saved?.receivegoods||null;state.workSaved=saved?.work||null;state.transitSaved=saved?.transit||null;state.boxesSaved=saved?.boxes||null;
  const selectSaved=(id,value)=>{if(value&&Array.from($(id).options).some(o=>o.value===value))$(id).value=value;};
  selectSaved('clientSelect',saved?.client);selectSaved('warehouseSelect',saved?.warehouse);
  if(saved?.picker&&$('pickerInput'))$('pickerInput').value=saved.picker;
  if($('assignmentKind'))$('assignmentKind').value=saved?.assignmentKind||'AUTO';
  if($('moveStockClass'))$('moveStockClass').value=saved?.moveStockClass||'MAIN';
  state.client=$('clientSelect')?.value||'';state.warehouse=$('warehouseSelect')?.value||'';state.picker=$('pickerInput')?.value?.trim()||'';
  state.moveRequest=saved?.moveRequest||null;state.pendingRecovery=saved?.pendingRecovery||null;state.scanNeedsSync=Boolean(state.pendingRecovery);
  if($('moveSource'))$('moveSource').value=saved?.moveSource||'';
  if($('moveProduct'))$('moveProduct').value=saved?.moveProduct||'';
  if($('moveDest'))$('moveDest').value=saved?.moveDest||'';
  if($('moveQty'))$('moveQty').value=saved?.moveQty||'1';
  if(saved?.screen==='receivegoods'){
    show('receivegoods');
    if(window.FCTsdReceiving)await window.FCTsdReceiving.restore(saved.receivegoods||{});
    return true;
  }
  if(saved?.screen==='work'&&saved?.work){
    show('work');if(window.FCTsdWork)await window.FCTsdWork.restore(saved.work);return true;
  }
  if(saved?.screen==='transit'&&saved?.transit){
    show('transit');if(window.FCTsdTransit)await window.FCTsdTransit.restore(saved.transit);return true;
  }
  if(saved?.screen==='boxreceive'&&saved?.boxes){
    show('boxreceive');if(window.FCTsdBoxes)await window.FCTsdBoxes.restore(saved.boxes);return true;
  }
  if(saved?.screen==='move'){
    show('move');setTimeout(()=>{const id=saved.moveSource?'moveProduct':'moveSource';$(id)?.focus();},100);
    await syncAssignedTask(false);return true;
  }
  try{
    const fresh=await fetchAssigned();
    renderAssignmentCard(fresh);
    if(!fresh?.session){state.session=null;persistRuntime();return false;}
    const current=fresh.current||null;
    const sameSession=String(fresh.session.id||'')===String(saved?.sessionId||'');
    const sameTask=sameSession&&current&&saved?.taskKey&&String(current.task_key||'')===String(saved.taskKey);
    const sameLocation=sameSession&&current&&String(current.location_id||'')===String(saved?.locationId||'');
    const sameSource=sameSession&&current&&sourceKey(current)===String(saved?.sourceKey||'');
    state.verifiedLocationId=(sameTask||sameLocation)?String(saved?.verifiedLocationId||''):'';
    state.verifiedSourceKey=(sameTask||sameSource)?String(saved?.verifiedSourceKey||''):'';
    state.cellListOpen=sameSession&&!!saved?.cellListOpen;
    state.lastAssignmentId=String(fresh.session.id||'');
    render(fresh);
    if(current){
      const locationOk=state.verifiedLocationId===String(current.location_id||'');
      const sourceOk=state.verifiedSourceKey===sourceKey(current);
      if(sameSession&&locationOk&&(!current.container||sourceOk)){
        $('scannerState').textContent='Актуальная сборка восстановлена · сканируйте ШК товара';
      }else if(sameSession&&locationOk&&current.container){
        $('scannerState').textContent='Актуальная сборка восстановлена · сканируйте короб';
      }else $('scannerState').textContent='Актуальная сборка · сканируйте ячейку';
    }
    persistRuntime();return true;
  }catch(e){msg($('setupMsg'),'Не удалось получить актуальную сборку: '+e.message,'error');return false;}
}

function focusScanner(){
  setTimeout(()=>{
    const el=$('scanInput');
    if(!el||!pickScreenActive())return;
    try{el.focus({preventScroll:true});}catch(_){try{el.focus();}catch(__){}}
  },50);
}
function pickScreenActive(){return $('pick')?.classList.contains('active')&&$('confirmModal')?.classList.contains('hidden');}
function clearScanTimers(){
  if(scanner.bufferTimer){clearTimeout(scanner.bufferTimer);scanner.bufferTimer=null;}
  if(scanner.inputTimer){clearTimeout(scanner.inputTimer);scanner.inputTimer=null;}
}
function normalizedRaw(raw){
  return String(raw||'').replace(/[\r\n\t]+$/g,'').trim();
}
function queueScan(raw,transport='keyboard'){
  const code=normalizedRaw(raw);
  if(!code||!state.session?.current||!pickScreenActive()||state.mutationBusy)return;
  if(state.scanNeedsSync){$('scanInput').value='';scanner.buffer='';scanner.lastInputValue='';clearScanTimers();feedback('error','Ждём подтверждения предыдущего скана · переподключение выполняется автоматически');return;}
  const now=Date.now();
  // r92: one physical unit must not be counted twice because the trigger or scanner
  // delivered the same code twice. Different barcodes are still accepted immediately.
  // The same code is accepted again after one second.
  if(code===scanner.lastCode&&now-scanner.lastAt<1000){
    $('scanInput').value='';scanner.buffer='';scanner.lastInputValue='';clearScanTimers();
    $('scannerState').textContent='Повторный скан заблокирован · подождите 1 секунду';
    return;
  }
  scanner.lastCode=code;scanner.lastAt=now;state.scanTransport=transport;
  const inp=$('scanInput');if(inp)inp.value='';scanner.lastInputValue='';scanner.buffer='';clearScanTimers();
  if(state.scanQueue.length>=40){feedback('error','Дождитесь обработки сканов');return;}
  state.scanQueue.push(code);processScans();
}
async function processScans(){
  if(state.scanBusy)return;state.scanBusy=true;state.revision++;lockActions(true);
  try{while(state.scanQueue.length&&pickScreenActive()){
    const code=state.scanQueue.shift();$('scannerState').textContent=`Скан получен: ${code}`;
    if(await onScan(code)===false){state.scanQueue=[];break;}
  }}finally{state.revision++;state.scanBusy=false;lockActions(false);}
}
function scheduleBufferFlush(){
  if(scanner.bufferTimer)clearTimeout(scanner.bufferTimer);
  scanner.bufferTimer=setTimeout(()=>{
    const v=scanner.buffer;scanner.buffer='';
    if(v.length>=2)queueScan(v);
  },120);
}
function scheduleInputFlush(){
  if(scanner.inputTimer)clearTimeout(scanner.inputTimer);
  scanner.inputTimer=setTimeout(()=>{
    const el=$('scanInput');const v=el?.value||'';
    if(v)queueScan(v);
  },90);
}

function pollScannerInput(){
  if(!pickScreenActive())return;
  const el=$('scanInput');if(!el)return;
  const v=String(el.value||'');
  if(v&&v!==scanner.lastInputValue){
    scanner.lastInputValue=v;
    $('scannerState').textContent='Сканер передаёт данные…';
    scheduleInputFlush();
  }
}

function getAudio(){
  try{
    const A=window.AudioContext||window.webkitAudioContext;
    if(!A)return null;
    if(!scanner.audio)scanner.audio=new A();
    if(scanner.audio.state==='suspended')scanner.audio.resume().catch(()=>{});
    return scanner.audio;
  }catch(_){return null;}
}
function tone(freq,dur=0.07,delay=0){
  try{
    const a=getAudio();if(!a)return;
    const o=a.createOscillator(),g=a.createGain();
    o.type='sine';o.frequency.value=freq;g.gain.value=.05;
    o.connect(g);g.connect(a.destination);
    const at=a.currentTime+delay;o.start(at);o.stop(at+dur);
  }catch(_){}
}
function vibrate(pattern){try{navigator.vibrate&&navigator.vibrate(pattern);}catch(_){}}
function feedback(kind,text){
  const ok=kind==='ok';
  const el=$('scanFeedback');
  el.textContent=text||'';
  el.className='scan-feedback '+(ok?'ok':'error');
  document.body.classList.remove('scan-ok','scan-bad');
  void document.body.offsetWidth;
  document.body.classList.add(ok?'scan-ok':'scan-bad');
  if(ok){tone(900,.06,0);tone(1250,.07,.075);vibrate(35);nativeCall('feedbackOk');}
  else{tone(190,.14,0);vibrate([170,80,170]);nativeCall('feedbackError');}
  clearTimeout(window.__tsdFeedbackTimer);
  window.__tsdFeedbackTimer=setTimeout(()=>{
    el.className='scan-feedback hidden';
    document.body.classList.remove('scan-ok','scan-bad');
  },700);
}

function renderCellSummary(s){
  const loc=s?.current_location;
  const root=$('cellSummary');
  if(!root)return;
  if(!loc){root.classList.add('hidden');return;}
  root.classList.remove('hidden');
  $('cellTotalLine').textContent=`${loc.route_row?'Взять из ряда '+loc.route_row+' этой палеты':'Взять из ячейки'}: ${num(loc.remaining)} шт. · ${num(loc.task_count)} поз.`;
  $('cellProgressLine').textContent=`Всего в задании по этой ячейке ${num(loc.required)} · собрано ${num(loc.picked)}${Number(loc.missing||0)>0?` · нет ${num(loc.missing)}`:''}`;
  const items=(loc.items||[]).filter(x=>Number(x.remaining||0)>0);
  $('cellItems').innerHTML=items.length?items.map(x=>`<div class="cell-item"><span><b>${esc(x.article||'—')} · ${esc(x.size||'—')}</b>${x.container?`<br><em>Короб ${esc(x.container)}</em>`:''}</span><b>${num(x.remaining)} шт.</b></div>`).join(''):'<div class="cell-item"><span>По этой ячейке всё обработано</span></div>';
  $('cellItems').classList.toggle('hidden',!state.cellListOpen);
  $('cellListToggle').textContent=state.cellListOpen?'Скрыть состав':'Показать состав';
}
function renderStage(t){
  if(!t)return;
  const sk=sourceKey(t);
  const locationOk=state.verifiedLocationId===String(t.location_id||'');
  const sourceOk=state.verifiedSourceKey===sk;
  const hint=$('scanHint');
  if(!locationOk){
    hint.textContent=`${t.container?'1/3':'1/2'} · Сканируйте QR ячейки ${t.location||''}`;
    hint.className='scan-hint';
  }else if(t.container&&!sourceOk){
    hint.textContent=`2/3 · Ячейка верна. Сканируйте короб ${t.container}`;
    hint.className='scan-hint';
  }else{
    hint.textContent=`${t.container?'3/3':'2/2'} · Сканируйте ШК товара`;
    hint.className='scan-hint ready';
  }
  focusScanner();
}
function render(s){
  state.session=s;
  if(!s||!s.session){show('setup');return;}
  show('pick');
  $('pickerName').textContent=s.session.picker;
  const assignedAt=s.session.assigned_at||s.session.created_at;
  const assignedBy=s.session.assigned_by||'';
  if($('assignmentMeta'))$('assignmentMeta').textContent=`${String(s.session.kind||'').toUpperCase()==='WAREHOUSE'?'Складское задание · списание при завершении':'Актуальная сборка FulfillCore'}${assignedAt?' · '+new Date(assignedAt).toLocaleString('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):''}${assignedBy?' · выдал '+assignedBy:''}`;
  const p=s.progress||{};
  $('progressText').textContent=`${num(p.done)}/${num(p.total)}`;
  $('progressBar').style.width=(p.total?Math.min(100,100*p.done/p.total):0)+'%';
  const t=s.current;
  let route=document.getElementById('warehouseRouteHint');if(!route){route=document.createElement('div');route.id='warehouseRouteHint';route.className='warehouse-route-hint';$('locationCode').after(route);}route.textContent=isWarehousePick()&&t?.route_label?t.route_label:'';route.hidden=!route.textContent;
  if(!t){
    $('locationCode').textContent='Готово';
    $('containerCode').textContent='';
    $('article').textContent='—';$('size').textContent='—';
    $('productName').textContent='Все позиции обработаны';
    $('needLine').textContent=`Собрано ${num(p.picked)}${isWarehousePick()?` (скан ${num(p.scan||0)} · вручную ${num(p.manual||0)})`:''} · Нет ${num(p.missing)}${Number(p.skipped||0)>0?` · пропущено ${num(p.skipped)}`:''}`;
    $('scanHint').textContent=isWarehousePick()?'Маршрут пройден · завершите сборку для списания':'Можно завершить задание';
    $('scanHint').className='scan-hint ready';
    $('scannerState').textContent='Сканирование завершено';
    $('cellSummary').classList.add('hidden');
    $('missingBtn').classList.add('hidden');
    $('manualFocus').classList.add('hidden');
    $('warehousePickActions')?.classList.add('hidden');
    $('finishPick').textContent=isWarehousePick()?'Завершить сборку':'Завершить и скачать Excel';
    $('finishPick').classList.remove('hidden');
    return;
  }
  $('finishPick').classList.add('hidden');
  $('missingBtn').classList.remove('hidden');
  $('missingBtn').textContent=t.container?'Нет в коробе':'Нет в ячейке';
  $('manualFocus').classList.remove('hidden');
  $('warehousePickActions')?.classList.toggle('hidden',!isWarehousePick());
  $('locationCode').textContent=t.location||'—';
  $('containerCode').textContent=t.container?`Короб: ${t.container}`:'';
  $('article').textContent=t.article||'—';
  $('size').textContent=t.size||'—';
  $('productName').textContent=t.name||'';
  $('needLine').textContent=`Нужно ${num(t.required)} · Собрано ${num(t.picked)}${isWarehousePick()?` (скан ${num(t.scan||0)} · вручную ${num(t.manual||0)})`:''} · Нет ${num(t.missing)}`;
  renderCellSummary(s);
  renderStage(t);
  persistRuntime();
}

async function startPick(){
  saveSetup();
  if(!$('pickerInput').value.trim()){msg($('setupMsg'),'Укажите имя сборщика','error');return;}
  state.verifiedLocationId='';state.verifiedSourceKey='';state.cellListOpen=false;
  await syncAssignedTask(true);
}

async function onScan(code){
  if(!state.session?.current)return false;
  const task=state.session.current;let recoveryStage='product';
  try{
    const locationOk=state.verifiedLocationId===String(task.location_id||'');
    const sk=sourceKey(task);
    const sourceOk=state.verifiedSourceKey===sk;
    recoveryStage=!locationOk?'location':(task.container&&!sourceOk?'container':'product');
    if(!locationOk){
      code=normalizeBoxScan(code);
      await api(isWarehousePick()?`/warehouse/picks/${state.session.session.id}/verify-location`:`/tsd/api/picking/${state.session.session.id}/verify-location`,{method:'POST',body:JSON.stringify({task_key:task.task_key,code,actor:state.picker})});
      state.verifiedLocationId=String(task.location_id||'');
      if(!task.container)state.verifiedSourceKey=sk;
      feedback('ok',`Ячейка ${task.location} верна`);
      $('scannerState').textContent='Ячейка подтверждена';
      renderStage(task);persistRuntime();return;
    }
    if(task.container&&!sourceOk){
      code=normalizeBoxScan(code);
      await api(isWarehousePick()?`/warehouse/picks/${state.session.session.id}/verify-container`:`/tsd/api/picking/${state.session.session.id}/verify-container`,{method:'POST',body:JSON.stringify({task_key:task.task_key,code,actor:state.picker})});
      state.verifiedSourceKey=sk;
      feedback('ok',`Короб ${task.container} верен`);
      $('scannerState').textContent='Короб подтверждён';
      renderStage(task);persistRuntime();return;
    }
    const oldLocation=String(task.location_id||''),oldSource=sk;
    const s=await api(isWarehousePick()?`/warehouse/picks/${state.session.session.id}/scan`:`/tsd/api/picking/${state.session.session.id}/scan-product`,{method:'POST',body:JSON.stringify({task_key:task.task_key,code,actor:state.picker})});
    feedback('ok','Товар принят');
    msg($('pickMsg'),'Принято','ok');setTimeout(()=>msg($('pickMsg'),''),700);
    render(s);
    const nt=s.current;
    if(nt){
      if(String(nt.location_id||'')!==oldLocation){
        state.verifiedLocationId='';state.verifiedSourceKey='';
      }else{
        state.verifiedLocationId=oldLocation;
        if(sourceKey(nt)===oldSource)state.verifiedSourceKey=oldSource;
        else state.verifiedSourceKey='';
      }
      renderStage(nt);
    }
    persistRuntime();
  }catch(e){
    if(!e.httpStatus||e.httpStatus>=500){rememberUncertainScan(code,recoveryStage,task);markNetworkState(false);e.message+=' Скан сохранён на ТСД. FulfillCore автоматически сверит сервер и повторит его только если первая попытка не была принята.';}
    feedback('error',e.message);
    msg($('pickMsg'),e.message,'error');
    $('scanHint').className='scan-hint error';
    setTimeout(()=>state.session?.current&&renderStage(state.session.current),800);
    focusScanner();return false;
  }
}

function openMissing(){
  const t=state.session?.current;if(!t)return;
  const remaining=Number(t.remaining||0);
  const place=t.container?`коробе ${t.container} (ячейка ${t.location})`:`ячейке ${t.location}`;
  $('confirmText').textContent=`В ${place} должно быть ещё ${num(remaining)} шт. товара ${t.article}${t.size?' / '+t.size:''}. Вы уверены? Отметка попадёт в историю и отчёт сборки.`;
  $('confirmModal').classList.remove('hidden');
}
async function confirmMissing(){
  const t=state.session?.current;if(!t)return;
  try{
    const oldLocation=String(t.location_id||''),oldSource=sourceKey(t);
    const s=await api(isWarehousePick()?`/warehouse/picks/${state.session.session.id}/missing`:`/tsd/api/picking/${state.session.session.id}/missing`,{method:'POST',body:JSON.stringify({task_key:t.task_key,confirm:true,actor:state.picker})});
    $('confirmModal').classList.add('hidden');
    feedback('ok','Отсутствие записано');
    render(s);
    const nt=s.current;
    if(nt){
      if(String(nt.location_id||'')!==oldLocation){state.verifiedLocationId='';state.verifiedSourceKey='';}
      else{state.verifiedLocationId=oldLocation;state.verifiedSourceKey=sourceKey(nt)===oldSource?oldSource:'';}
      renderStage(nt);
    }
    persistRuntime();
    msg($('pickMsg'),'Отсутствие записано в отчёт','ok');
  }catch(e){msg($('pickMsg'),e.message,'error');feedback('error',e.message);}
}
async function manualConfirm(){
  const t=state.session?.current;if(!t||!isWarehousePick())return;
  const locationOk=state.verifiedLocationId===String(t.location_id||'');
  const sourceOk=!t.container||state.verifiedSourceKey===sourceKey(t);
  if(!locationOk||!sourceOk){msg($('pickMsg'),'Сначала подтвердите сканированием ячейку и короб.','error');feedback('error','Сначала ячейка и короб');return;}
  const remaining=Math.max(0,Number(t.remaining||0));
  const raw=prompt(`Подтвердить вручную сколько единиц? Осталось ${num(remaining)} шт.`,`1`);
  if(raw===null)return;const quantity=Number(String(raw).replace(',','.'));
  if(!Number.isInteger(quantity)||quantity<=0||quantity>remaining){msg($('pickMsg'),`Введите целое количество от 1 до ${num(remaining)}.`, 'error');return;}
  if(!confirm(`Подтвердить вручную ${num(quantity)} шт.\n\nАртикул: ${t.article||'—'}\nРазмер: ${t.size||'—'}\nКороб: ${t.container||'без короба'}?`))return;
  try{const oldLocation=String(t.location_id||''),oldSource=sourceKey(t);const fresh=await api(`/warehouse/picks/${state.session.session.id}/manual`,{method:'POST',body:JSON.stringify({task_key:t.task_key,quantity,actor:state.picker})});feedback('ok',`Вручную подтверждено: ${num(quantity)} шт.`);render(fresh);const nt=fresh.current;if(nt){if(String(nt.location_id||'')!==oldLocation){state.verifiedLocationId='';state.verifiedSourceKey='';}else{state.verifiedLocationId=oldLocation;state.verifiedSourceKey=sourceKey(nt)===oldSource?oldSource:'';}renderStage(nt);}persistRuntime();}
  catch(e){msg($('pickMsg'),e.message,'error');feedback('error',e.message);}
}
async function skipCurrent(){
  const t=state.session?.current;if(!t||!isWarehousePick())return;
  try{const fresh=await api(`/warehouse/picks/${state.session.session.id}/skip`,{method:'POST',body:JSON.stringify({task_key:t.task_key,actor:state.picker})});feedback('ok','Позиция пропущена');state.verifiedLocationId='';state.verifiedSourceKey='';render(fresh);persistRuntime();}
  catch(e){msg($('pickMsg'),e.message,'error');feedback('error',e.message);}
}

async function finish(){
  try{
    if(isWarehousePick()){
      const id=state.session.session.id;
      await api(`/warehouse/picks/${id}/submit`,{method:'POST',body:JSON.stringify({actor:state.picker})});
      report('/warehouse/picks/'+encodeURIComponent(id)+'/report.xlsx');
      feedback('ok','Сборка завершена. Остаток списан.');
      msg($('pickMsg'),'Маршрут завершён. Остатки списаны, причина и адреса сохранены в истории.','ok');
      state.session=null;state.verifiedLocationId='';state.verifiedSourceKey='';clearRuntime();
      setTimeout(()=>{show('setup');syncAssignedTask(false);},900);return;
    }
    const x=await api(`/tsd/api/picking/${state.session.session.id}/finish`,{method:'POST',body:'{}'});
    feedback('ok','Задание завершено');
    report(x.report_url);
    state.session=null;state.verifiedLocationId='';state.verifiedSourceKey='';clearRuntime();
    setTimeout(()=>{show('setup');syncAssignedTask(false);},500);
  }catch(e){msg($('pickMsg'),e.message,'error');feedback('error',e.message);}
}
async function cancelPick(){
  await syncAssignedTask(true);
  if(state.session?.session)msg($('pickMsg'),'Показана актуальная сборка, назначенная в FulfillCore.','ok');
}

function report(url){if(!/^\/(?:warehouse\/picks|tsd\/api\/picking)\/[^/]+\/report\.xlsx$/.test(url))return;localStorage.fcTsdLastReport=url;const a=$('lastReport');a.href=url;a.classList.remove('hidden');a.click();}
async function move(){
  const payload={client_id:$('clientSelect').value,warehouse_id:$('warehouseSelect').value,picker:$('pickerInput').value.trim(),source_container:$('moveSource').value.trim(),product_barcode:$('moveProduct').value.trim(),destination_container:$('moveDest').value.trim(),quantity:Number($('moveQty').value||1),stock_class:$('moveStockClass').value};
  if(!payload.picker||!payload.source_container||!payload.product_barcode||!payload.destination_container){msg($('moveMsg'),'Заполните все сканы','error');return;}
  if(!Number.isInteger(payload.quantity)||payload.quantity<1){msg($('moveMsg'),'Введите целое количество от 1','error');return;}
  const key=JSON.stringify(payload),saved=state.moveRequest;
  if(!saved||saved.key!==key)state.moveRequest={key,id:typeof crypto.randomUUID==='function'?crypto.randomUUID():'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const n=Math.random()*16|0;return(c==='x'?n:(n&3)|8).toString(16);})};
  try{
    persistRuntime();
    msg($('moveMsg'),'Перемещаем…');
    const x=await api('/tsd/mobile/move',{method:'POST',body:JSON.stringify({...payload,request_id:state.moveRequest.id})});
    msg($('moveMsg'),`Готово: ${x.article||''} ${x.size||''}, ${num(x.quantity)} шт. → ${x.destination} · ${x.destination_address}`,'ok');
    state.moveRequest=null;$('moveProduct').value='';$('moveQty').value='1';$('moveProduct').focus();persistRuntime();feedback('ok','Перемещение выполнено');
  }catch(e){msg($('moveMsg'),e.message+' Повтор той же команды не создаёт второе перемещение.','error');feedback('error',e.message);}
}
function openWorkplace(page){saveSetup();if(!state.client||!state.warehouse){msg($('setupMsg'),'Выберите клиента и склад','error');return;}localStorage.setItem('fc_client',state.client);localStorage.setItem('fc_warehouse',state.warehouse);location.href='/?tsd=1&work='+encodeURIComponent(page);}
window.FCTsdPersist=persistRuntime;
window.FCNativeScan=raw=>{
  if(state.currentScreen==='receivegoods'){window.FCTsdReceiving?.scan(raw,'native');setTimeout(()=>window.FCTsdReceiving?.rearm?.(),60);return;}
  if(state.currentScreen==='work'){window.FCTsdWork?.scan(raw,'native');return;}
  if(state.currentScreen==='transit'){window.FCTsdTransit?.scan(raw,'native');return;}
  if(state.currentScreen==='boxreceive'){window.FCTsdBoxes?.scan(raw,'native');return;}
  if(pickScreenActive()){queueScan(raw,'native');return;}
  if(state.currentScreen==='move'){
    let el=document.activeElement;if(!['moveSource','moveProduct','moveDest'].includes(el?.id))el=$('moveSource');
    el.value=normalizedRaw(raw);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  }
};

/* Keyboard-wedge compatibility:
   1) Enter suffix, 2) Tab suffix, 3) no suffix with 120 ms idle flush,
   4) direct input/change/paste injection used by older Urovo scanner services. */
document.addEventListener('keydown',e=>{
  if(!pickScreenActive())return;
  if(e.ctrlKey||e.altKey||e.metaKey)return;
  if(e.key==='Enter'||e.key==='Tab'){
    const v=scanner.buffer||$('scanInput')?.value||'';
    scanner.buffer='';
    if(v){e.preventDefault();queueScan(v);}
    return;
  }
  if(typeof e.key==='string'&&e.key.length===1){
    $('scannerState').textContent='Сканер передаёт данные…';
    scanner.buffer+=e.key;scheduleBufferFlush();
  }
},true);
$('scanInput').setAttribute('inputmode','none');
$('scanInput').addEventListener('beforeinput',()=>{$('scannerState').textContent='Сканер передаёт данные…';});
$('scanInput').addEventListener('input',()=>{scanner.lastInputValue=$('scanInput').value||'';$('scannerState').textContent='Сканер передаёт данные…';scheduleInputFlush();});
$('scanInput').addEventListener('keyup',()=>{if($('scanInput').value)scheduleInputFlush();});
$('scanInput').addEventListener('compositionend',e=>{if(e.data||e.target.value)scheduleInputFlush();});
$('scanInput').addEventListener('change',e=>{const v=e.target.value;e.target.value='';scanner.lastInputValue='';if(v)queueScan(v);});
$('scanInput').addEventListener('paste',e=>{
  const v=e.clipboardData?.getData('text')||'';
  if(v){e.preventDefault();queueScan(v);}
});
document.addEventListener('pointerdown',()=>{if(pickScreenActive())focusScanner();},{passive:true});

$('startPick').onclick=startPick;
$('openMove').onclick=()=>{saveSetup();show('move');setTimeout(()=>$('moveSource').focus(),100)};
$('homeBtn').onclick=()=>show('setup');
$('manualFocus').onclick=()=>{focusScanner();$('scannerState').textContent='Сканер активирован. Выполните скан.';};
$('missingBtn').onclick=openMissing;
$('manualPickBtn').onclick=()=>singleAction(manualConfirm);
$('skipPickBtn').onclick=()=>singleAction(skipCurrent);
$('confirmNo').onclick=()=>{$('confirmModal').classList.add('hidden');focusScanner()};
$('confirmYes').onclick=()=>singleAction(confirmMissing);
$('finishPick').onclick=()=>singleAction(finish);
$('cancelPick').onclick=cancelPick;
$('moveGo').onclick=()=>singleAction(move);
$('openBoxes').onclick=()=>window.FCTsdBoxes?.open();
$('openGoods').onclick=()=>window.FCTsdReceiving?.open();
$('openCount').onclick=()=>window.FCTsdWork?.open('COUNT');
$('openTransit').onclick=()=>window.FCTsdTransit?.open();
$('openBoxPicks').onclick=()=>openWorkplace('boxpicks');
$('openStocks').onclick=()=>openWorkplace('stockhome');
$('assignmentKind').onchange=()=>{saveSetup();state.session=null;refreshAvailability();};
$('cellListToggle').onclick=()=>{state.cellListOpen=!state.cellListOpen;renderCellSummary(state.session);};

for(const id of ['moveSource','moveProduct','moveDest']){
  $(id).addEventListener('input',persistRuntime);
  $(id).addEventListener('change',persistRuntime);
  $(id).addEventListener('keydown',e=>{
  if(e.key==='Enter'||e.key==='Tab'){
    e.preventDefault();
    const next=id==='moveSource'?'moveProduct':id==='moveProduct'?'moveDest':'moveGo';
    $(next)?.focus();
    if(id!=='moveDest')feedback('ok','Скан получен');
  }
  });
}
$('moveQty').addEventListener('input',persistRuntime);
$('moveStockClass').addEventListener('change',persistRuntime);
$('clientSelect').addEventListener('change',()=>{saveSetup();refreshAvailability();});
$('warehouseSelect').addEventListener('change',()=>{saveSetup();refreshAvailability();});
$('pickerInput').addEventListener('change',()=>{saveSetup();refreshAvailability();});
$('pickerInput').addEventListener('blur',()=>{saveSetup();refreshAvailability();});
setInterval(pollScannerInput,70);
setInterval(()=>{
  if(pickScreenActive()&&document.activeElement!==$('scanInput')){
    try{$('scanInput').focus({preventScroll:true});}catch(_){try{$('scanInput').focus();}catch(__){}}
  }
},700);
window.addEventListener('focus',()=>{if(!['move','work','transit','boxreceive','receivegoods'].includes(state.currentScreen))syncAssignedTask(false);if(pickScreenActive())focusScanner();});
window.addEventListener('online',()=>{markNetworkState(true,'Связь появилась · сверяем сохранённый прогресс…');connectionHeartbeat().catch(()=>{});});
window.addEventListener('offline',()=>markNetworkState(false,'Нет связи · прогресс сохранён на ТСД'));
markNetworkState(navigator.onLine!==false);
setInterval(()=>connectionHeartbeat().catch(()=>{}),5000);
const nativeVersion=nativeCall('appVersion');if(nativeVersion)$('mobileVersion').textContent='Android '+nativeVersion+' · WMS r145f32';
const last=localStorage.fcTsdLastReport;if(last&&/^\/(warehouse\/picks|tsd\/api\/picking)\/[^/]+\/report\.xlsx$/.test(last)){$('lastReport').href=last;$('lastReport').classList.remove('hidden');}
window.FCTsdCore={api,show,saveSetup,persistRuntime,readRuntime,feedback,esc,state,ready:false,context:()=>({client_id:$('clientSelect').value,warehouse_id:$('warehouseSelect').value,picker:$('pickerInput').value.trim(),owner:localStorage.fcTsdIdentity||''})};
lockActions(true);
loadSetup().then(async()=>{
  await restoreSessionState();persistRuntime();
}).catch(e=>msg($('setupMsg'),e.message,'error')).finally(()=>{state.initializing=false;lockActions(false);window.FCTsdCore.ready=true;window.dispatchEvent(new Event('fc:tsd-ready'));});
// Always converge to the newest server assignment. A stale local WebView session can no longer win.
setInterval(()=>{if(!document.hidden&&!['move','work','transit','boxreceive','receivegoods'].includes(state.currentScreen)&&$('pickerInput')?.value.trim())syncAssignedTask(false);},8000);
window.addEventListener('pagehide',persistRuntime);
window.addEventListener('beforeunload',persistRuntime);
document.addEventListener('visibilitychange',()=>{if(document.hidden)persistRuntime();});
})();
