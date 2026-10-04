/* r68: box-only receiving, durable scan queue and separate Excel workflows. */
/* FC_R134_BOX_SCAN_BUTTON_GS1_FIX: accept scanner edge controls and make the accept button explicit. */
/* FC_R145F7_ASSEMBLY_SEPARATE_TAB: receiving stays here; templates live under Сборка. */
(()=>{
  'use strict';
  let receiptId='',snapshot=null,plan=null,epoch=0,working=false,scanTimer=0,lastInput=0,fastKeys=0,startId='',changing=false,refreshSequence=0;
  const by=()=>String($('currentUser')?.textContent||'admin').trim();
  const ctx=()=>({client_id:state.clientId||'',warehouse_id:state.warehouseId||''});
  const context=()=>JSON.stringify(ctx());
  const scopeKey=()=>`fc:r68:boxes:${by()}:${state.clientId||''}:${state.warehouseId||''}`;
  const qs=()=>new URLSearchParams(ctx()).toString();
  const h=v=>esc(window.FC25?FC25.publicText(v):v);const palName=p=>FC25.publicCode({code:p?.public_code||p?.location_code||('PAL-'+String(p?.number||'').padStart(3,'0'))});const palLabel=p=>[p?.section&&`Секция ${p.section}`,palName(p),p?.first_row&&`Ряд ${p.first_row}`].filter(Boolean).join(' · ');
  const newId=()=>typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.floor(Math.random()*16);return(c==='x'?r:(r&3)|8).toString(16);});
  // FC_R145F20_BOX_QUEUE_QUOTA: never clear drafts or silently accept an unsaved scan.
  const fallbackKey=key=>'fc:r20:box-queue-fallback:'+key;
  function storageWarning(){
    let e=$('r20BoxStorageWarning');if(!e){e=document.createElement('div');e.id='r20BoxStorageWarning';e.className='pvz-note bad';$('r68ScanForm')?.prepend(e);}
    e.textContent='Хранилище браузера заполнено. Очередь сохранена в этой вкладке. Не закрывайте её, пока все сканы не отправлены на сервер. Старые данные не удалялись.';
  }
  function readScoped(key){
    try{const backup=sessionStorage.getItem(fallbackKey(key));if(backup!==null){storageWarning();return JSON.parse(backup)||{};}}catch(_){}
    try{return JSON.parse(localStorage.getItem(key)||'{}');}catch(_){return {};}
  }
  function writeScoped(key,value){
    const raw=JSON.stringify(value);
    try{localStorage.setItem(key,raw);try{sessionStorage.removeItem(fallbackKey(key));}catch(_){}return value;}
    catch(_){
      try{sessionStorage.setItem(fallbackKey(key),raw);storageWarning();return value;}
      catch(e){throw new Error('Скан не сохранён: хранилище браузера и вкладки недоступно. Поле сканирования не очищено. Не закрывайте вкладку; восстановите доступ к хранилищу.');}
    }
  }
  function readLocal(){return readScoped(scopeKey());}
  function saveLocal(change){return writeScoped(scopeKey(),{...readLocal(),...change});}
  function message(text,bad=false){if(bad)window.FC25?.beep(true);const el=$('r68Message');if(el){el.textContent=text;el.classList.toggle('bad',bad);el.setAttribute('role',bad?'alert':'status');}}
  const apiPost=(path,extra={},method='POST')=>api('/box-intake'+path,{method,body:JSON.stringify({...ctx(),...extra}),timeoutMs:60000});
  function download(url){const a=document.createElement('a');a.href=url;document.body.appendChild(a);a.click();a.remove();}
  const ru='ёйцукенгшщзхъфывапролджэячсмитьбюЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ';
  const en='`qwertyuiop[]asdfghjkl;\'zxcvbnm,.~QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>';
  const trimScannerEdges=value=>{let text=String(value||'').trim();text=text.replace(/^[\x00-\x1f\x7f]+|[\x00-\x1f\x7f]+$/g,'').trim();return text;};
  const normalizeScan=value=>trimScannerEdges(value).split('').map(ch=>{const i=ru.indexOf(ch);return i<0?ch:en[i];}).join('');
  function showQueue(){const el=$('r73QueueItems'),q=readLocal().queue||[];if(!el)return;el.innerHTML=q.map((code,i)=>`<div class="r68-saved"><span>${h(code)}</span><button type="button" class="mini danger" data-r73-drop="${i}" ${(working&&i===0)?'disabled':''}>Убрать скан</button></div>`).join('');}
  function draw(){
    const root=$('r68Details'),scanForm=$('r68ScanForm');if(!root||!scanForm)return;
    if(!snapshot){root.innerHTML='';scanForm.hidden=true;for(const id of ['r73NewPallet','r73DeleteReceipt','r68CloseReceipt'])if($(id))$(id).hidden=true;return;}
    const r=snapshot.receipt,open=r.status==='OPEN',pallets=snapshot.pallets||[],allBoxes=snapshot.boxes||[],boxes=allBoxes.filter(b=>b.status==='ACTIVE'),removed=allBoxes.filter(b=>b.status!=='ACTIVE'),cancelled=r.status==='CANCELLED';
    scanForm.hidden=!open;$('r68ReceiptTitle').textContent=r.name;
    $('r68CloseReceipt').hidden=!open;
    const current=pallets.find(p=>p.id===r.current_pallet_id);
    $('r68ScanPlace').textContent=r.to_pallets?(current&&!current.is_closed?`${palLabel(current)}: ${current.box_count} из ${current.capacity} коробов`:'Выберите секцию и палету перед сканированием'):'Приёмка коробов без палет';if(current){if($('r27Section'))$('r27Section').value=current.section||r.default_section||'';if($('r27Pallet'))$('r27Pallet').value=palName(current);if($('r27Row'))$('r27Row').value=current.first_row||r.first_row||'';}
    $('r68NextPallet').hidden=!r.to_pallets;
    $('r73ExtendCurrent').hidden=!current||!r.to_pallets;
    $('r73ExtendCurrent').dataset.r68Extend=current?.id||'';
    $('r73ExtendCurrent').textContent=current?`Добавить места на палету ${palName(current)}`:'Добавить места';
    $('r73PalletSelect').hidden=!r.to_pallets;
    $('r73PalletSelect').innerHTML='<option value="">Выберите палету для сканирования</option>'+pallets.map(p=>`<option value="${h(p.id)}" ${p.id===r.current_pallet_id?'selected':''}>${h(palLabel(p))} · ${p.box_count} / ${p.capacity}</option>`).join('');
    $('r73DeleteReceipt').hidden=cancelled;$('r73NewPallet').hidden=cancelled;
    FC25.audioToggle($('r68ScanForm'));showQueue();
    const queue=readLocal().queue||[];$('r68Retry').hidden=!queue.length;$('r68Queue').textContent=queue.length?`Ожидают записи на сервер: ${queue.length}`:'';
    root.innerHTML=`<div class="r68-overview"><b>${boxes.filter(b=>b.status==='ACTIVE').length} активных коробов</b><span>${pallets.length} палет</span><span>${cancelled?'Приёмка удалена':open?'Сканирование открыто':'Сканирование завершено'}</span></div>
      <div class="r68-pallets">${pallets.map(p=>`<article class="r68-pallet"><span class="kicker">${p.section?`СЕКЦИЯ ${h(p.section)}`:'СЕКЦИЯ НЕ УКАЗАНА'}</span><h3>${h(palName(p))}</h3><b>${p.box_count} / ${p.capacity} коробов</b><small>${h(palLabel(p))}</small><form data-r68-address="${h(p.id)}"><label>Секция<input name="section" maxlength="80" value="${h(p.section||'')}" placeholder="Необязательно"></label><label>Первый ряд<input name="row" type="number" min="1" max="900" value="${p.first_row??''}" placeholder="Необязательно"></label><button class="secondary" type="submit">Сохранить адрес</button></form>${!cancelled?`<div class="r73-row-actions"><button type="button" class="secondary" data-r68-extend="${h(p.id)}">Добавить места</button><button type="button" class="danger" data-r73-delete-pallet="${h(p.id)}">Удалить палету</button></div>`:''}</article>`).join('')}</div>
      <div class="r68-toolbar"><button type="button" class="secondary" data-r68-export-receipt>Реестр коробов Excel</button><label class="r68-file">Загрузить раскладку товара<input id="r68LayoutFile" type="file" accept=".xlsx"></label><button type="button" class="secondary" data-r68-layout>Проверить раскладку</button><a href="/box-intake/templates/box_layout.xlsx">Шаблон раскладки</a></div>
      <p class="muted">16 коробов — стандарт, а не предел: нажмите «Добавить места», чтобы продолжить на той же палете. Короба учтены отдельно. Поштучный товар появляется после ручной приёмки или подтверждения раскладки. Ряд содержит до 8 коробов.</p>
      <div id="r68LayoutPreview"></div><div class="table-wrap"><table><thead><tr><th>Короб / ШК</th><th>Где сейчас</th><th>Учтено товара</th><th>Действия</th></tr></thead><tbody>${boxes.map(b=>`<tr><td><b>${h(b.container_name||b.box_code)}</b>${b.status!=='ACTIVE'?'<small>Архивный</small>':''}</td><td>${h(b.route_label)}<small>${h(b.location_code||'Без адреса')}</small></td><td>${Number(b.quantity)>0?fmtQty(b.quantity)+' шт.':'Без поштучного состава'}</td><td>${b.status==='ACTIVE'?`<div class="r73-row-actions"><button class="mini primary" type="button" data-r73-move="${h(b.container_id)}">На другую палету</button><button class="mini danger" type="button" data-r73-delete-box="${h(b.container_id)}">Удалить короб</button><button class="mini secondary" type="button" data-r73-content="${h(b.container_id)}">Состав</button><button class="mini secondary" type="button" data-r68-manual="${h(b.container_id)}">Принять товар вручную</button><button class="mini secondary" type="button" data-r68-label="${h(b.container_id)}">Этикетка</button></div>`:''}</td></tr>`).join('')||'<tr><td colspan="4">Отсканируйте первый короб.</td></tr>'}</tbody></table></div>`;
    if(removed.length){const archived=document.createElement('details');archived.innerHTML=`<summary>Удалённые короба: ${removed.length}</summary>${removed.map(b=>`<div class="r68-saved"><span>${h(b.container_name||b.box_code)}</span>${cancelled?'':`<button class="mini secondary" type="button" data-r73-restore="${h(b.container_id)}">Восстановить</button>`}</div>`).join('')}`;root.appendChild(archived);}
    const history=document.createElement('details');history.innerHTML='<summary>История приёмки коробов · последние 100 действий</summary><div class="table-wrap"><table><thead><tr><th>Время</th><th>Действие</th><th>Короб / палета</th><th>Сотрудник</th></tr></thead><tbody>'+(snapshot.audit||[]).map(e=>{const names={BOX_RESTORE:'Короб восстановлен',BOX_PALLET_MOVE:'Короб перенесён на палету',PALLET_DELETE:'Палета удалена',RECEIPT_DELETE:'Приёмка удалена',PALLET_SELECT:'Выбрана палета для сканирования',CREATE:'Начата приёмка',PALLET_CREATE:'Создана палета',BOX_SCAN:'Принят короб',NEXT_PALLET:'Следующая палета',PALLET_EXTEND:'Добавлены места',PALLET_ADDRESS:'Изменена секция / ряд',CLOSE_SCANNING:'Сканирование завершено',LAYOUT_POST:'Принят товар из раскладки',BOX_MOVE:'Короб перемещён',BOX_ARCHIVE:'Короб убран в архив'};const x=e.after_json||{};return '<tr><td>'+h(new Date(e.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}))+'</td><td>'+h(names[e.action]||e.action)+'</td><td>'+h(x.box||x.location_code||(x.pallet_number||x.number?'Палета '+(x.pallet_number||x.number):''))+'</td><td>'+h(e.actor||'')+'</td></tr>';}).join('')+'</tbody></table></div>';root.appendChild(history);
    drawPlan();
  }
  function drawPlan(){const el=$('r68LayoutPreview');if(!el||!plan)return;const posted=plan.status==='POSTED';el.innerHTML=`<div class="r68-preview"><h3>${posted?'Раскладка уже проведена':'Проверьте товары перед приёмкой'}</h3><p>${plan.rows.length} строк · ${fmtQty(plan.rows.reduce((s,r)=>s+r.qty,0))} шт.</p>${plan.occupied_boxes?.length?`<label><input type="checkbox" id="r68ConfirmOccupied"> Подтверждаю добавление в непустые короба: ${h(plan.occupied_boxes.join(', '))}</label>`:''}<div class="table-wrap"><table><thead><tr><th>Короб</th><th>Адрес</th><th>Товар</th><th>ШК</th><th>Добавить</th></tr></thead><tbody>${plan.rows.map(r=>`<tr><td>${h(r.container_name)}</td><td>${h(r.location_code)}</td><td>${h(r.article)} · ${h(r.size)}</td><td>${h(r.barcode)}</td><td>${r.qty}</td></tr>`).join('')}</tbody></table></div>${posted?'':`<button class="primary" type="button" data-r68-confirm-layout>Принять товары на Основной склад</button>`}</div>`;}
  async function openReceipt(id){
    const ticket=++epoch,k=context();receiptId=id;plan=null;snapshot=null;draw();
    const d=await api('/box-intake/receipts/'+encodeURIComponent(id)+'?'+qs());
    if(ticket!==epoch||k!==context())return;
    snapshot=d;saveLocal({receipt:id});draw();$('r68Scan')?.focus();
  }
  async function refreshReceipt(){const serial=++refreshSequence,id=receiptId,k=context(),ticket=epoch;if(!id)return;const d=await api('/box-intake/receipts/'+encodeURIComponent(id)+'?'+qs());if(id!==receiptId||k!==context()||ticket!==epoch||serial!==refreshSequence)return;snapshot=d;draw();}
  async function listReceipts(){await ensureContext();const k=context(),data=await api('/box-intake/receipts?'+qs()+'&include_deleted='+Boolean($('r73ShowDeleted')?.checked));if(k!==context())return;const select=$('r68Existing');select.innerHTML='<option value="">Выберите приёмку</option>'+data.rows.map(r=>`<option value="${h(r.id)}">${r.status==='CANCELLED'?'[Удалена] ':''}${h(r.name)} · ${r.box_count} коробов · ${new Date(r.created_at).toLocaleDateString('ru-RU')}</option>`).join('');const saved=readLocal().receipt;if(!receiptId&&saved&&data.rows.some(r=>r.id===saved)){select.value=saved;await openReceipt(saved);}else select.value=receiptId;}
  async function drain(){
    if(working||changing||!receiptId)return;working=true;let failed=false;const k=context(),id=receiptId,key=scopeKey();
    try{
      while(k===context()&&id===receiptId){
        const saved=readLocal(),queue=saved.queue||[];if(!queue.length)break;
        if(saved.queueReceipt&&saved.queueReceipt!==id)throw new Error('В очереди есть сканы другой приёмки. Откройте её и нажмите «Повторить отправку»');
        const code=queue[0],target=saved.queueTargets?.[0]||null,r=await api('/r145f25/box-intake/'+id+'/scan',{method:'POST',body:JSON.stringify({...ctx(),box_code:code,expected_pallet_id:target}),timeoutMs:30000});
        // A context switch must not leave a successfully acknowledged scan in the old queue.
        const stored=readScoped(key);if(stored.queue?.[0]===code){stored.queue.shift();if(stored.queueTargets)stored.queueTargets.shift();writeScoped(key,stored);}
        if(k!==context()||id!==receiptId)return;
        if(r.added)FC25.speak('\u041a\u043e\u0440\u043e\u0431 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d');
        message(r.added?`Принят ${r.box_code}${r.pallet_code?' \u00b7 '+FC25.publicCode(r.pallet_code):''}${r.pallet_full?' заполнена. Выберите нужную палету или добавьте места.':''}`:`${r.box_code} уже принят${r.pallet_code?' \u043d\u0430 '+FC25.publicCode(r.pallet_code):''}. Повтор не добавлен.`);
      }
      if(k===context()&&id===receiptId)await refreshReceipt();
    }catch(e){failed=true;if(k===context()){message(e.message+' Скан сохранён в очереди.',true);draw();}}
    finally{working=false;showQueue();$('r68Scan')?.focus();if(!failed&&k===context()&&id===receiptId&&(readLocal().queue||[]).length)setTimeout(drain,0);}
  }
  function queueScan(){clearTimeout(scanTimer);const el=$('r68Scan'),raw=String(el?.value||''),code=normalizeScan(raw);if(!code)return;if(changing){message('Дождитесь завершения изменения короба',true);return;}if(!receiptId||snapshot?.receipt.status!=='OPEN'){message('Сначала начните или откройте приёмку',true);return;}if(code.length>120||/[\x00-\x1f\x7f]/.test(code)){message('Сканер передал служебный символ внутри ШК. Повторите сканирование короба.',true);return;}const saved=readLocal();if(saved.queue?.length&&saved.queueReceipt!==receiptId){message('Сначала отправьте очередь предыдущей приёмки',true);showQueue();return;}const queue=saved.queue||[];if(queue.length>=500){message('Очередь заполнена. Восстановите связь и отправьте сканы.',true);return;}if(snapshot.receipt.to_pallets&&!snapshot.receipt.current_pallet_id){message('\u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043f\u0430\u043b\u0435\u0442\u0443',true);return;}queue.push(code);const queueTargets=[...(saved.queueTargets||[])];queueTargets.push(snapshot.receipt.current_pallet_id||null);try{saveLocal({queue,queueTargets,queueReceipt:receiptId});}catch(e){message(e.message,true);return;}el.value='';fastKeys=0;showQueue();message(`Короб ${code}: записываем…`);drain();}
  async function choosePallet(code=null,section=null,row=null){
    ensureQueueEmpty();if(!receiptId)throw Error('Сначала откройте приёмку');
    section=section??$('r27Section')?.value??snapshot?.receipt?.default_section??'';code=code??$('r27Pallet')?.value??'';row=row??$('r27Row')?.value??snapshot?.receipt?.first_row??null;
    section=String(section||'').trim();code=String(code||'').trim();if(!section)throw Error('Укажите секцию, например 6');if(!code)throw Error('Укажите палету, например PAL-006');
    changing=true;const id=receiptId,k=context();try{const d=await api('/r145f27/box-intake/'+id+'/pallet',{method:'POST',body:JSON.stringify({...ctx(),section,pallet_code:code,first_row:row?Number(row):null,receiver:by()}),timeoutMs:30000});if(k!==context()||id!==receiptId)return;snapshot=d.receipt_snapshot;draw();message('Выбрано: Секция '+section+' · '+d.pallet.display_code);FC25.speak('Палета выбрана');}finally{changing=false;$('r68Scan')?.focus();}
  }
  function ensureQueueEmpty(){if(changing||working||(readLocal().queue||[]).length)throw new Error('Сначала дождитесь записи всех сканов или повторите отправку очереди');}
  async function manual(id){
    const k=context(),b=snapshot?.boxes.find(x=>x.container_id===id);if(!b)throw new Error('Короб не найден');
    go('receive');if($('manualReceiveStorageMode')){$('manualReceiveStorageMode').value='BOX';$('manualReceiveStorageMode').dispatchEvent(new Event('change',{bubbles:true}));}
    $('manualReceiveBox').value=b.container_barcode||b.container_name;await activateManualReceivingBox('SCAN');if(k!==context())return;
    if($('manualReceiveLegacyMode'))$('manualReceiveLegacyMode').checked=false;
    renderManualReceivingStorageContext();
    const zone=$('receiveStockClass');if(zone){zone.value='MAIN';zone.dispatchEvent(new Event('change',{bubbles:true}));}
    $('receiveSku')?.focus();toast('Приёмка товара в короб '+b.container_name);
  }
  async function createSheet(){const file=$('r68PickFile').files[0],number=$('r68PickNumber').value.trim();if(!file||!number)throw new Error('Укажите номер задания и выберите Excel со списком коробов');const k=context(),fd=new FormData();for(const [key,v]of Object.entries(ctx()))fd.append(key,v);fd.append('number',number);fd.append('file',file);const r=await api('/box-intake/picks',{method:'POST',body:fd,timeoutMs:120000});if(k!==context())return;$('r68PickResult').innerHTML=`<b>Найдено ${r.found} из ${r.requested} коробов.</b>${r.errors.length?`<p class="bad">Не найдены: ${h(r.errors.map(x=>x.container+': '+x.error).join('; '))}</p>`:''}<p>Лист содержит адреса на момент формирования. Остатки и размещение от загрузки задания не изменяются.</p><a href="/box-intake/picks/${encodeURIComponent(r.id)}/export.xlsx?${qs()}">Скачать сборочный лист</a>`;download('/box-intake/picks/'+encodeURIComponent(r.id)+'/export.xlsx?'+qs());await listSheets();}
  async function listSheets(){const k=context(),data=await api('/box-intake/picks?'+qs());if(k!==context())return;$('r68Sheets').innerHTML=data.rows.map(r=>`<div class="r68-saved"><b>${h(r.number)}</b><span>${r.found} / ${r.requested} коробов</span><a href="/box-intake/picks/${encodeURIComponent(r.id)}/export.xlsx?${qs()}">Скачать Excel</a><button class="mini danger" type="button" data-r73-delete-sheet="${h(r.id)}">Удалить</button></div>`).join('')||'<p>Сборочных листов коробов пока нет.</p>';}
  async function change(path,body,note,method='POST'){
    ensureQueueEmpty();changing=true;const id=receiptId,k=context();refreshSequence++;
    try{const result=await apiPost(path,body,method);if(id!==receiptId||k!==context())return result;plan=null;if(result.receipt_snapshot){snapshot=result.receipt_snapshot;draw();}else await refreshReceipt();message(note);listReceipts().catch(err=>message(note+' Не удалось обновить список приёмок: '+err.message,true));return result;}
    finally{changing=false;$('r68Scan')?.focus();}
  }
  function moveDialog(id){
    const box=snapshot?.boxes.find(b=>b.container_id===id);if(!box)return;
    const rid=receiptId,k=context(),pallets=(snapshot.pallets||[]).filter(p=>p.location_id!==box.location_id);
    if(!pallets.length){message('Сначала создайте другую палету кнопкой «Новая палета». Затем нажмите «На другую палету».',true);return;}
    $('r73MoveDialog')?.remove();const d=document.createElement('dialog');d.id='r73MoveDialog';
    d.innerHTML=`<form><h2>Переместить ${h(box.container_name||box.box_code)}</h2><label>На какую палету?<select name="pallet" required>${pallets.map(p=>`<option value="${h(p.id)}">Палета ${h(palName(p))} · ${h(p.section?'секция '+p.section+' · ':'')}${p.box_count} / ${p.capacity} коробов</option>`).join('')}</select></label><label class="r68-check"><input type="checkbox" name="expand" checked> Добавить место, если палета заполнена</label><p>Короб и учтённый товар переместятся вместе. Новое место появится в маршруте сборки.</p><p data-error role="alert"></p><div class="r73-row-actions"><button type="button" data-close class="secondary">Отмена</button><button type="submit" class="primary">Переместить</button></div></form>`;
    document.body.appendChild(d);d.showModal();d.querySelector('[data-close]').onclick=()=>d.remove();
    d.querySelector('form').onsubmit=async e=>{e.preventDefault();const btn=d.querySelector('[type=submit]');if(btn.disabled)return;
      if(k!==context()||rid!==receiptId){d.remove();return;}btn.disabled=true;
      try{const f=d.querySelector('form');await change('/receipts/'+rid+'/boxes/'+id+'/move',{pallet_id:f.elements?f.elements.pallet.value:f.querySelector('[name=pallet]').value,allow_expand:f.querySelector('[name=expand]').checked,expected_location_id:box.location_id},'Короб перемещён. Адрес и количество коробов обновлены.');d.remove();}
      catch(err){d.querySelector('[data-error]').textContent=err.message;}finally{btn.disabled=false;}
    };
  }
  document.addEventListener('click',async e=>{
    const b=e.target.closest('[data-r73-delete-box],[data-r73-move],[data-r73-content],[data-r73-restore],[data-r73-delete-pallet],[data-r73-delete-receipt],[data-r73-drop],[data-r73-new-pallet],[data-r73-delete-sheet]');if(!b||b.disabled)return;e.preventDefault();b.disabled=true;
    try{
      if(b.hasAttribute('data-r73-drop')){const i=Number(b.dataset.r73Drop),saved=readLocal(),q=saved.queue||[];if(working&&i===0)throw Error('Этот скан сейчас записывается. Дождитесь ответа сервера');if(q[i]&&confirm('Убрать ошибочный скан '+q[i]+' из очереди?')){q.splice(i,1);const queueTargets=saved.queueTargets||[];queueTargets.splice(i,1);saveLocal({queue:q,queueTargets});showQueue();draw();}return;}
      if(b.dataset.r73Content){const k=context();go('containers');await loadContextData();if(k===context())await showContainer(b.dataset.r73Content);return;}
      ensureQueueEmpty();
      if(b.dataset.r73Move){moveDialog(b.dataset.r73Move);return;}
      if(b.hasAttribute('data-r73-new-pallet')){if($('r27Pallet')){$('r27Pallet').value='';$('r27Pallet').focus();}message('Укажите секцию и номер новой палеты, затем нажмите «Выбрать палету».');return;}
      if(b.dataset.r73DeleteSheet){if(confirm('Удалить сборочный лист коробов? Остатки не изменятся.')){await change('/picks/'+b.dataset.r73DeleteSheet+'/archive',{},'Сборочный лист удалён');$('r68PickResult').innerHTML='';await listSheets();}return;}
      if(b.dataset.r73DeleteBox){const box=snapshot?.boxes.find(x=>x.container_id===b.dataset.r73DeleteBox);if(!box)return;if(Number(box.quantity)>0){message('В коробе '+box.container_name+' есть '+fmtQty(box.quantity)+' шт. Откройте «Состав», перенесите или спишите товар, затем удалите короб.',true);return;}if(confirm('Удалить ошибочно принятый короб '+(box.container_name||box.box_code)+'? История сохранится.'))await change('/receipts/'+receiptId+'/boxes/'+box.container_id+'/archive',{},'Короб удалён. Место на палете освободилось.');return;}
      if(b.dataset.r73Restore){await change('/receipts/'+receiptId+'/boxes/'+b.dataset.r73Restore+'/restore',{},'Короб восстановлен');return;}
      if(b.dataset.r73DeletePallet){const p=snapshot?.pallets.find(x=>x.id===b.dataset.r73DeletePallet);if(p&&confirm('Удалить палету '+p.number+' и её пустые короба ('+p.box_count+')? Если есть товар или резерв, удаление не выполнится. История сохранится.'))await change('/receipts/'+receiptId+'/pallets/'+p.id+'/archive',{},'Палета удалена');return;}
      if(b.hasAttribute('data-r73-delete-receipt')&&confirm('Удалить эту приёмку, её палеты и пустые короба? Если есть товар или резерв, удаление не выполнится. История сохранится.')){await change('/receipts/'+receiptId+'/archive',{},'Приёмка удалена. История доступна через «Показать удалённые приёмки».');if(snapshot?.receipt.status==='CANCELLED')saveLocal({receipt:''});}
    }catch(err){message(err.message,true);}finally{b.disabled=false;}
  });
  function setup(){
    if($('page-boxintake'))return;const host=document.querySelector('.page')?.parentElement;if(!host)return;
    const style=document.createElement('link');style.rel='stylesheet';style.href='/static/box_intake.css?v=r145f27';document.head.appendChild(style);
    const page=document.createElement('section');page.id='page-boxintake';page.className='page';page.innerHTML=`<div class="r68-tabs"><button type="button" class="primary" data-r68-tab="receive">Приёмка коробов</button></div><div id="r68Message" class="r68-message" role="status"></div>
      <div id="r68ReceiveTab"><div class="panel"><h2>Принять короба</h2><p>Сканируйте короба при разгрузке. Товар внутри можно внести позже.</p><form id="r68Start" class="r68-fields"><label>Название приёмки<input name="name" maxlength="240" placeholder="Например: Фура 16 сентября" required></label><label class="r68-check"><input name="pallets" type="checkbox" checked> Приёмка коробов на палеты</label><label>Секция<input name="section" maxlength="80" placeholder="Например 6"></label><label>Палета<input name="pallet" maxlength="120" placeholder="PAL-006 или QR"></label><label>Ряд<input name="row" type="number" min="1" max="900" value="1"></label><button type="submit" class="primary">Начать приёмку</button></form><div class="r68-toolbar"><label>Открыть приёмку<select id="r68Existing"><option value="">Выберите приёмку</option></select></label><button type="button" class="secondary" data-r68-refresh>Обновить</button><label class="r68-check"><input type="checkbox" id="r73ShowDeleted">Показать удалённые приёмки</label></div></div>
      <div class="panel"><div class="r68-toolbar"><h2 id="r68ReceiptTitle">Выберите приёмку</h2><button type="button" class="secondary" id="r73NewPallet" data-r73-new-pallet hidden>Новая палета</button><button type="button" id="r73DeleteReceipt" class="danger" data-r73-delete-receipt hidden>Удалить приёмку</button><button type="button" id="r68CloseReceipt" class="secondary" hidden>Завершить сканирование</button></div><form id="r68ScanForm" hidden><b id="r68ScanPlace"></b><div class="r27-pallet-picker"><label>Секция<input id="r27Section" maxlength="80" placeholder="6"></label><label>Палета / QR<input id="r27Pallet" maxlength="120" placeholder="PAL-006"></label><label>Ряд<input id="r27Row" type="number" min="1" max="900" placeholder="1"></label><button type="button" class="primary" id="r27SelectPallet">Выбрать палету</button></div><div class="r68-toolbar"><select id="r73PalletSelect" aria-label="Палета для сканирования"></select><button type="button" class="secondary" id="r73ExtendCurrent" hidden>Добавить места</button></div><div class="r68-scanner"><input id="r68Scan" autocomplete="off" placeholder="Сканируйте ШК короба" aria-label="Штрихкод короба"><button id="r68AcceptBox" class="primary" type="submit">Принять короб</button><button id="r68NextPallet" class="secondary" type="button">Сменить палету</button></div><p class="muted">Русская раскладка сканера исправляется автоматически: ИЩЧ → BOX.</p><span id="r68Queue"></span><div id="r73QueueItems"></div><button type="button" id="r68Retry" class="secondary" hidden>Повторить отправку</button><button type="button" class="secondary" data-r68-drop-scan>Убрать ошибочный скан из очереди</button></form><div id="r68Details"></div></div></div>
      <div id="r68PickTab" hidden><div class="panel"><h2>Сборка целых коробов</h2><p>Один короб — одна строка в колонке «Короб». Программа найдёт адреса и расположит их по секциям и рядам.</p><div class="r68-fields"><label>Номер задания<input id="r68PickNumber" maxlength="120" placeholder="Например: К-001"></label><label>Список коробов Excel<input id="r68PickFile" type="file" accept=".xlsx"></label><button class="primary" type="button" data-r68-box-pick>Получить сборочный лист</button></div><div id="r68PickResult"></div><div id="r68Sheets"></div></div>
      <div class="panel"><h2>Шаблоны Excel</h2><div class="r68-templates">${[['boxes','Сборка коробов','Одна колонка: Короб.'],['goods','Сборка товара','ШК и количество. Адреса подбираются автоматически.'],['goods_by_box','Сборка товара из коробов','Артикул, размер, ШК, количество и точный короб.'],['box_layout','Заполнение принятых коробов','Раскладка товара для приёмки на Основной склад.'],['fbs','Сборочное задание WB FBS','Номер в B1. ШК и количество с 4-й строки.'],['transfer','Перевод Основной → FBS','ШК и количество; ячейка и короб необязательны.']].map(([key,title,desc])=>`<article><h3>${title}</h3><p>${desc}</p><a href="/box-intake/templates/${key}.xlsx">Скачать шаблон</a></article>`).join('')}</div><div class="r68-toolbar"><button type="button" class="primary" data-r68-goods>Загрузить задание на товар</button><button type="button" class="secondary" data-r65-go="fbspicks">Сборочные листы WB FBS</button></div></div></div>`;host.appendChild(page);titles.boxintake='Приёмка коробов';
    const nav=document.createElement('button');nav.type='button';nav.dataset.r68Open='receive';nav.className='nav-link';nav.textContent='Приёмка коробов';const anchor=document.querySelector('#mainNav [data-page="containers"]');nav.dataset.page='boxintake';if(anchor){nav.className=anchor.className;nav.classList.remove('active');anchor.after(nav);}else $('mainNav')?.appendChild(nav);
    for(const name of ['receive','mainstock','stockhome']){const target=$('page-'+name);if(target&&!target.querySelector('[data-r68-open]')){const bar=document.createElement('div');bar.className='r68-toolbar';bar.innerHTML='<button type="button" class="primary" data-r68-open="receive">Принять короба</button>';target.prepend(bar);}}
    $('r68Start').onsubmit=async e=>{e.preventDefault();const btn=e.submitter;if(btn?.disabled)return;if(btn)btn.disabled=true;try{ensureQueueEmpty();await ensureContext();const f=new FormData(e.target),toPallets=f.get('pallets')==='on',section=String(f.get('section')||'').trim(),firstPal=String(f.get('pallet')||'').trim(),row=f.get('row')?Number(f.get('row')):null;if(toPallets&&(!section||!firstPal))throw Error('Укажите секцию и палету');startId=startId||newId();const k=context();const r=await api('/r145f25/box-intake',{method:'POST',body:JSON.stringify({...ctx(),id:startId,name:f.get('name'),to_pallets:toPallets,section,first_row:row})});if(k!==context())return;startId='';await openReceipt(r.id);if(toPallets)await choosePallet(firstPal,section,row);await listReceipts();message('Приёмка начата. Сканируйте первый короб.');}catch(err){message(err.message,true);}finally{if(btn)btn.disabled=false;}};
    $('r73ShowDeleted').onchange=()=>listReceipts().catch(e=>message(e.message,true));
    $('r73PalletSelect').onchange=e=>e.target.value&&change('/receipts/'+receiptId+'/pallets/'+e.target.value+'/select',{},'Палета выбрана').catch(err=>message(err.message,true));
    $('r68Existing').onchange=async e=>{try{ensureQueueEmpty();if(e.target.value)await openReceipt(e.target.value);}catch(err){message(err.message,true);}};
    $('r68ScanForm').onsubmit=e=>{e.preventDefault();queueScan();};$('r68AcceptBox').onclick=e=>{e.preventDefault();queueScan();};$('r68Scan').oninput=()=>{const t=Date.now();fastKeys=t-lastInput<90?fastKeys+1:0;lastInput=t;clearTimeout(scanTimer);if(fastKeys>=3)scanTimer=setTimeout(queueScan,300);};
    $('r68Retry').onclick=drain;
    $('r68NextPallet').textContent='Сменить палету';$('r68NextPallet').onclick=()=>{$('r27Pallet')?.focus();};$('r27SelectPallet').onclick=()=>choosePallet().catch(e=>message(e.message,true));
    $('r68CloseReceipt').onclick=async()=>{try{ensureQueueEmpty();if(!confirm('Все короба отсканированы? После завершения можно принять товар в эти короба вручную или из Excel.'))return;await change('/receipts/'+receiptId+'/close',{},'Сканирование завершено. Короба сохранены на складе.');}catch(e){message(e.message,true);}};
    page.addEventListener('submit',async e=>{const f=e.target.closest('[data-r68-address]');if(!f)return;e.preventDefault();try{ensureQueueEmpty();const d=new FormData(f);const id=receiptId,k=context(),res=await api('/r145f27/box-intake/'+id+'/pallets/'+f.dataset.r68Address+'/address',{method:'PATCH',body:JSON.stringify({...ctx(),section:d.get('section'),first_row:d.get('row')?Number(d.get('row')):null})});if(k!==context()||id!==receiptId)return;snapshot=res.receipt_snapshot;draw();message('Секция и ряд палеты сохранены.');}catch(err){message(err.message,true);}});
  }
  function tab(which){if(which==='pick'){go('warehousework');return;}$('r68ReceiveTab').hidden=false;if($('r68PickTab'))$('r68PickTab').hidden=true;document.querySelectorAll('[data-r68-tab]').forEach(b=>{b.className=b.dataset.r68Tab==='receive'?'primary':'secondary';});}
  document.addEventListener('click',async e=>{
    const b=e.target.closest('[data-r68-open],[data-r68-tab],[data-r68-refresh],[data-r68-extend],[data-r68-export-receipt],[data-r68-layout],[data-r68-confirm-layout],[data-r68-manual],[data-r68-label],[data-r68-box-pick],[data-r68-goods],[data-r68-drop-scan]');if(!b||b.disabled)return;e.preventDefault();
    try{
      if(b.dataset.r68Open){setup();if(b.dataset.r68Open==='pick'){go('warehousework');return;}go('boxintake');tab('receive');return;}
      if(b.dataset.r68Tab){tab(b.dataset.r68Tab);return;}
      if(b.hasAttribute('data-r68-goods')){go('warehousework');return;}
      if(b.hasAttribute('data-r68-refresh')){await listReceipts();await refreshReceipt();return;}
      if(b.dataset.r68Manual){await manual(b.dataset.r68Manual);return;}
      if(b.dataset.r68Label){await printManualReceivingBoxLabel(snapshot?.boxes.find(x=>x.container_id===b.dataset.r68Label)||{container_id:b.dataset.r68Label});return;}
      if(b.hasAttribute('data-r68-export-receipt')){download('/box-intake/receipts/'+receiptId+'/export.xlsx?'+qs());return;}
      if(b.hasAttribute('data-r68-drop-scan')){if(working)throw new Error('Дождитесь ответа сервера');const s=readLocal(),q=s.queue||[];if(!q.length)return;if(confirm('Убрать из очереди скан '+q[0]+'? Уже принятый сервером короб не удаляется.')){q.shift();const queueTargets=s.queueTargets||[];queueTargets.shift();saveLocal({queue:q,queueTargets});draw();}return;}
      ensureQueueEmpty();b.disabled=true;
      if(b.dataset.r68Extend){const raw=prompt('Сколько дополнительных коробов разрешить на этой палете?','8');if(raw===null)return;const count=Number(raw);if(!Number.isInteger(count)||count<=0)throw new Error('Введите целое положительное число');await change('/receipts/'+receiptId+'/pallets/'+b.dataset.r68Extend+'/extend',{count},'Места добавлены. Можно продолжить сканировать в выбранную палету.');}
      if(b.hasAttribute('data-r68-box-pick'))await createSheet();
      if(b.hasAttribute('data-r68-layout')){const f=$('r68LayoutFile')?.files?.[0];if(!f)throw new Error('Выберите файл раскладки');const fd=new FormData();for(const [key,v]of Object.entries(ctx()))fd.append(key,v);fd.append('file',f);const k=context(),id=receiptId;const result=await api('/box-intake/receipts/'+id+'/layout',{method:'POST',body:fd,timeoutMs:120000});if(k!==context()||id!==receiptId)return;plan=result;drawPlan();message(result.status==='POSTED'?'Этот файл уже проведён. Повторного прихода нет.':'Проверьте раскладку и подтвердите приёмку.');}
      if(b.hasAttribute('data-r68-confirm-layout')){const occupied=Boolean($('r68ConfirmOccupied')?.checked);const k=context(),id=receiptId,r=await apiPost('/layout/'+plan.id+'/confirm',{confirm_occupied:occupied});if(k!==context()||id!==receiptId)return;plan=null;await refreshReceipt();message(r.already_posted?'Раскладка уже принята. Повторного прихода нет.':`Принято ${r.units} шт. на Основной склад.`);}
    }catch(err){message(err.message,true);}finally{b.disabled=false;}
  });
  const originalGo=go;go=function(p){setup();originalGo(p);if(p==='boxintake')listReceipts().catch(e=>message(e.message,true));};
  document.addEventListener('change',e=>{if(['clientSelect','warehouseSelect'].includes(e.target.id)){epoch++;receiptId='';snapshot=null;plan=null;startId='';$('r73MoveDialog')?.remove();clearTimeout(scanTimer);if($('r68Scan'))$('r68Scan').value='';draw();if(state.activePage==='boxintake')setTimeout(()=>listReceipts().catch(err=>message(err.message,true)),0);}},true);
  window.FCBoxIntake={normalizeScan,setup,queueScan,drain,openReceipt,positionInfo:()=>snapshot};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);else setup();
})();
