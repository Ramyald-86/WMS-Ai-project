/* r145f22: scoped cell reads and explicit receipt reversal dialog. */
(()=>{'use strict';
 const $=id=>document.getElementById(id);
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const num=v=>Number(v||0).toLocaleString('ru-RU',{maximumFractionDigits:3});
 const ctx=()=>({client_id:String(state.clientId||''),warehouse_id:String(state.warehouseId||'')});
 const ctxKey=c=>c.client_id+'|'+c.warehouse_id;
 const date=v=>{const d=new Date(v);return Number.isNaN(d.getTime())?'—':d.toLocaleString('ru-RU');};
 const oldOpen=openLocationContents,oldRender=renderLocationDetail;
 let sequence=0,cellFlight=null,cellFlightKey='',cellController=null,cellToken='';
 const currentCell=(c,id,t)=>ctxKey(ctx())===ctxKey(c)&&String(locationBrowser.locationId)===String(id)&&locationBrowser.contentsRequest===t;
 function cellRow(b,live){
   const barcode=b.barcode||'',locationId=b.location_id||locationBrowser.locationId;
   let actions='';
   if(live&&window.FCStockActions?.buttons){
     actions=window.FCStockActions.buttons({barcode,article:b.article||'',size:b.size||'',
       sku_ids:b.sku_id?[b.sku_id]:[],location_id:locationId,container_id:b.container_id||null,
       seed:{article:b.article||'',size:b.size||'',barcode,location:locationBrowser.contentsData?.location?.code||'',
         container:b.container||'',quantity:Number(b.quantity||0)}},true)||'';
   }
   return `<tr><td><b>${esc(b.article||b.sku_code||'—')}</b></td><td>${esc(b.name||'—')}</td><td>${esc(b.size||'—')}</td><td>${esc(barcode||'—')}</td><td><b>${num(b.quantity)} шт.</b><small class="subline">Физический остаток${live?'':' · требует обновления'}</small></td><td>${actions}${b.product_id?`<button type="button" class="mini secondary" data-location-product-card data-product-id="${esc(b.product_id)}" data-product-barcode="${esc(barcode)}">Карточка товара</button>`:''}</td></tr>`;
 }
 function cellTable(items,live){return `<div class="table-wrap"><table class="storage-items-table"><thead><tr><th>Артикул</th><th>Товар</th><th>Размер</th><th>ШК</th><th>Количество</th><th>Действия</th></tr></thead><tbody>${items.map(b=>cellRow(b,live)).join('')}</tbody></table></div>`;}
 renderLocationDetail=function(location){
   if(String(location.code||location.barcode||'').trim().toUpperCase()==='ПРИЕМКА')return oldRender(location);
   const d=locationBrowser.contentsData,scope=ctx(),id=String(location.id);
   const usable=d?.source==='physical_local'&&String(d.location?.id)===id&&ctxKey(d)===ctxKey(scope);
   const loading=Boolean(locationBrowser.contentsLoading),err=String(d?.load_error||''),live=usable&&!loading&&!err;
   const items=usable&&Array.isArray(d.items)?d.items:[],summary=usable?d.summary:null;
   let body='';
   if(loading)body+='<div class="empty-state" role="status">Загружаем только выбранный адрес…</div>';
   if(err)body+=`<div class="warn-box" role="alert"><b>Состав адреса не обновлён.</b> ${esc(err)}<br>${usable?'Ниже — последний подтверждённый состав, не текущий остаток.':'Остаток неизвестен: пустая таблица не означает ноль.'}</div>`;
   if(usable){
     const groups=new Map();
     for(const b of items){const k=b.container_id||'';if(!groups.has(k))groups.set(k,[]);groups.get(k).push(b);}
     for(const [key,rows] of groups){
       body+=`<section class="storage-box-card"><div class="storage-box-head"><div><span class="kicker">${key?'КОРОБ':'БЕЗ КОРОБА'}</span><h3>${esc(key?rows[0].container:'Товар прямо в адресе')}</h3></div><span class="tag">${num(rows.reduce((n,x)=>n+Number(x.quantity||0),0))} шт.</span></div>${cellTable(rows,live)}</section>`;
     }
     if(!items.length&&!loading&&!err)body+='<div class="empty-state">В этом адресе нет товара выбранного клиента. Товары других клиентов здесь не показываются.</div>';
   }else if(!loading&&!err){body+='<div class="empty-state">Состав ещё не прочитан. Нажмите «Обновить состав».</div>';}
   const title=(usable?d.location?.code:'')||location.code||'',name=(usable?d.location?.name:'')||location.name||'';
   return `<div class="storage-detail r22-cell"><div class="storage-detail-head"><div><span class="kicker">АДРЕСНОЕ ХРАНЕНИЕ</span><h2>${esc(title)}</h2><p>${esc(name)}</p><small>Клиент: ${esc(state.clients?.find(x=>String(x.id)===scope.client_id)?.name||scope.client_id)}</small></div><div class="button-row"><button type="button" class="primary" data-r22-cell-retry="${esc(id)}">Обновить состав</button><button type="button" class="secondary" data-location-history="${esc(id)}">История изменений</button><button type="button" class="secondary" data-location-print="${esc(id)}">Печать QR-этикетки</button><button type="button" class="danger" data-location-delete="${esc(id)}" data-location-code="${esc(location.code)}">Удалить пустой адрес</button></div></div><div class="location-content-summary">${summary?`<b>${num(summary.sku_count)} SKU</b><b>${num(summary.container_count)} короб.</b><b>${num(summary.quantity)} шт.</b><span>${loading||err?'Последний состав':'Прочитано'}: ${esc(date(d.observed_at))}</span>`:'<b>Количество ещё не проверено</b>'}</div><p class="r22-cell-note">Показан физический товар из локальной базы. Резерв и доступность для изменения проверяются отдельно перед складской операцией; данные WB здесь не пересчитываются.</p><div class="storage-box-list">${body}</div></div>`;
 };
 openLocationContents=function(id){
   const location=state.locations?.find(x=>String(x.id)===String(id));if(!location)return Promise.resolve();
   if(String(location.code||location.barcode||'').trim().toUpperCase()==='ПРИЕМКА')return oldOpen(id);
   const c=ctx(),key=ctxKey(c)+'|'+id;
   if(cellFlight&&cellFlightKey===key){
     locationBrowser.contentsRequest=cellToken;
     return cellFlight;
   }
   cellController?.abort();cellController=new AbortController();
   const controller=cellController,t=key+'|'+(++sequence),meta=locationStorageMeta(location);
   cellToken=t;cellFlightKey=key;
   const old=locationBrowser.contentsData;
   const keep=old?.source==='physical_local'&&String(old.location?.id)===String(id)&&ctxKey(old)===ctxKey(c);
   if($('locationSearch'))$('locationSearch').value='';
   Object.assign(locationBrowser,{level:'DETAIL',storage:meta.isPallet?'PALLET':'CELL',rackKey:meta.rackKey,
     floor:meta.floor,locationId:String(id),historyData:null,historyLoading:false,
     contentsData:keep?{...old,load_error:''}:null,contentsLoading:true,contentsRequest:t});
   renderLocations();
   const job=(async()=>{
     try{
       const data=await api(`/r145f22/locations/${encodeURIComponent(id)}/contents?`+new URLSearchParams(c),{timeoutMs:15000,signal:controller.signal});
       if(currentCell(c,id,t))locationBrowser.contentsData=data;
     }catch(e){
       if(currentCell(c,id,t)&&!controller.signal.aborted){
         locationBrowser.contentsData={...(keep?old:{...c,location:{id:String(id)}}),load_error:e.message||'Нет ответа сервера'};
       }
     }finally{
       if(currentCell(c,id,t)){locationBrowser.contentsLoading=false;renderLocations();}
       if(cellFlight===job){cellFlight=null;cellFlightKey='';}
     }
   })();
   cellFlight=job;return job;
 };
 const st=document.createElement('style');st.textContent=`
 .r22-cell-note{color:#667085;line-height:1.5;font-size:13px}.r22-cell .storage-detail-head{gap:14px;flex-wrap:wrap}
 .r22-dialog{box-sizing:border-box;border:0;border-radius:18px;padding:24px;width:min(1020px,calc(100vw - 36px));max-height:90vh;overflow:auto;color:#101828}
 .r22-dialog::backdrop{background:#10182888}.r22-dialog h2{margin:0 0 12px}.r22-dialog table{width:100%;border-collapse:collapse}
 .r22-dialog td,.r22-dialog th{padding:10px;text-align:left;vertical-align:top;border-bottom:1px solid #e4e7ec}
 .r22-dialog .table-wrap{overflow:auto}.r22-dialog textarea{box-sizing:border-box;width:100%;min-height:76px;padding:10px}.r22-dialog .r22-actions{display:flex;justify-content:flex-end;gap:10px;flex-wrap:wrap;margin-top:18px}
 .r22-dialog .r22-note{background:#fff5e8;border-radius:10px;padding:12px;margin:12px 0}.r22-dialog .r22-error{color:#b42318;white-space:pre-wrap}
 .r22-dialog .r22-parts{display:grid;gap:8px;margin:12px 0}.r22-dialog .r22-part{display:flex;gap:8px;align-items:flex-start}
 .r22-dialog .r22-ack{display:flex;gap:8px;align-items:flex-start;margin-top:14px}.r22-dialog .r22-part input,.r22-dialog .r22-ack input{width:auto;min-width:18px}
 `;document.head.appendChild(st);
 const dialog=document.createElement('dialog');dialog.id='r22CancelDialog';dialog.className='r22-dialog';dialog.setAttribute('aria-labelledby','r22CancelTitle');
 dialog.innerHTML=`<h2 id="r22CancelTitle">Удаление / отмена приёмки</h2><p id="r22CancelScope"></p><div class="r22-note">Это реальное уменьшение остатка. Списывается только количество выбранной приёмки и только из исходных мест. Короба и остальной товар не удаляются. Запись, время и сотрудник остаются в истории.</div><div id="r22CancelParts" class="r22-parts"></div><div id="r22CancelBody" role="status"></div><p id="r22CancelMsg" class="r22-error" role="alert"></p><label>Причина отмены<textarea id="r22CancelReason" maxlength="500" placeholder="Например: отсканирован не тот короб"></textarea></label><label class="r22-ack"><input id="r22CancelAck" type="checkbox"><span>Проверил(а) клиента, состав, количество и места. Подтверждаю отмену выбранной приёмки.</span></label><div class="r22-actions"><button type="button" id="r22CancelClose" class="secondary">Закрыть</button><button type="button" id="r22CancelPreview" class="secondary">Проверить заново</button><button type="button" id="r22CancelConfirm" class="danger" disabled>Подтвердить отмену</button></div>`;
 document.body.appendChild(dialog);
 let cancelState=null,cancelSeq=0;
 function cancelReady(){return cancelState&&cancelState.plan?.can_cancel&&!cancelState.busy&&ctxKey(ctx())===ctxKey(cancelState.context)&&$('r22CancelAck').checked&&$('r22CancelReason').value.trim().length>=3;}
 function controls(){
   const busy=Boolean(cancelState?.busy);
   $('r22CancelConfirm').disabled=!cancelReady();
   $('r22CancelClose').disabled=busy;
   $('r22CancelPreview').disabled=busy||!cancelState||ctxKey(ctx())!==ctxKey(cancelState.context);
   $('r22CancelReason').disabled=busy;$('r22CancelAck').disabled=busy;
   dialog.querySelectorAll('[data-r22-part]').forEach(x=>{x.disabled=busy;});
 }
 function describe(plan){
   if(plan.already_cancelled){return '<div class="r22-note">Эта приёмка уже отменена. Повторное списание не выполняется.</div>';}
   const bs=plan.buckets||[];
   return `<p><b>К отмене: ${num(plan.quantity)} шт.</b> · мест/товаров: ${bs.length} · операций: ${plan.operations.length}</p><div class="table-wrap"><table><thead><tr><th>Товар</th><th>Место</th><th>Принято / убрать</th><th>Физически сейчас</th><th>Результат проверки</th></tr></thead><tbody>${bs.map(b=>`<tr><td><b>${esc(b.article||'—')} · ${esc(b.size||'—')}</b><br>${esc(b.barcode||'')}</td><td>${esc(b.location)}<br>${esc(b.container||'Без короба')}</td><td>${num(b.quantity)}</td><td>${num(b.physical)}</td><td>${b.issues?.length?`<span class="r22-error">${b.issues.map(esc).join('<br>')}</span>`:'Можно отменить'}</td></tr>`).join('')}</tbody></table></div>${plan.can_cancel?'':'<p class="r22-error">Есть блокирующие причины. Ни одна строка не будет списана.</p>'}`;
 }
 async function loadCancel(){
   const s=cancelState;if(!s||s.busy)return;
   if(ctxKey(ctx())!==ctxKey(s.context)){$('r22CancelMsg').textContent='Клиент или склад изменён. Закройте окно и откройте приёмку заново.';s.plan=null;controls();return;}
   if(!s.ids.length){$('r22CancelMsg').textContent='Выберите хотя бы одну часть приёмки.';controls();return;}
   const token=++cancelSeq;s.busy=true;s.plan=null;controls();$('r22CancelBody').textContent='Проверяем исходные места, резервы и историю движений…';$('r22CancelMsg').textContent='';$('r22CancelAck').checked=false;
   try{
     const p=await api('/r145f22/receipts/cancel/preview',{method:'POST',body:JSON.stringify({...s.context,operation_ids:s.ids}),timeoutMs:55000});
     if(s!==cancelState||token!==cancelSeq)return;
     if(ctxKey(ctx())!==ctxKey(s.context))throw new Error('Клиент или склад изменён. Откройте приёмку заново.');
     s.plan=p;$('r22CancelBody').innerHTML=describe(p);
     if(!s.partsLoaded){s.allOps=p.operations||[];s.partsLoaded=true;
       $('r22CancelParts').innerHTML=s.allOps.map(op=>`<label class="r22-part"><input type="checkbox" data-r22-part="${esc(op.id)}" checked><span><b>${esc(op.reference||'Приёмка')}</b> · ${esc(date(op.created_at))} · ${esc(op.performed_by||'Сотрудник не указан')}<small class="subline">Операция ${esc(String(op.id).slice(0,8))}</small></span></label>`).join('');
     }
   }catch(e){if(s===cancelState&&token===cancelSeq){$('r22CancelBody').textContent='Проверка не завершена. Остатки не изменены.';$('r22CancelMsg').textContent=e.message;}}
   finally{if(s===cancelState){s.busy=false;controls();}}
 }
 async function openCancel(ids){
   if(cancelState?.busy)return;
   const c=ctx();if(!c.client_id||!c.warehouse_id){toast('Выберите клиента и склад',true);return;}
   cancelState={ids:[...new Set(ids.map(String))],context:c,plan:null,busy:false,partsLoaded:false};
   $('r22CancelParts').innerHTML='';$('r22CancelReason').value='';$('r22CancelAck').checked=false;$('r22CancelMsg').textContent='';
   const cn=state.clients?.find(x=>String(x.id)===c.client_id)?.name||c.client_id,wn=state.warehouses?.find(x=>String(x.id)===c.warehouse_id)?.name||c.warehouse_id;
   $('r22CancelScope').textContent=`Клиент: ${cn} · Склад: ${wn}`;
   if(!dialog.open)dialog.showModal();await loadCancel();
 }
 async function confirmCancel(){
   if(!cancelReady())return;
   const s=cancelState,ids=s.ids.slice(),fp=s.plan.fingerprint;s.busy=true;controls();$('r22CancelMsg').textContent='Выполняем отмену. Не закрывайте окно…';
   try{
     const r=await api('/r145f22/receipts/cancel/confirm',{method:'POST',timeoutMs:105000,
       body:JSON.stringify({...s.context,operation_ids:ids,fingerprint:fp,confirmed:true,reason:$('r22CancelReason').value.trim()})});
     if(s!==cancelState)return;
     s.plan=null;$('r22CancelAck').checked=false;
     $('r22CancelMsg').textContent='';
     $('r22CancelBody').innerHTML=`<div class="r22-note"><b>${r.replayed?'Отмена уже была выполнена':'Приёмка отменена'}.</b> ${num(r.quantity)} шт. ${r.replayed?'Повторного списания нет.':'убрано из исходных мест.'} История сохранена.</div>`;
     if(ctxKey(ctx())===ctxKey(s.context)){
       unifiedReceiptHistoryRows=[];unifiedReceiptHistoryContext='';
       state.operations=(state.operations||[]).map(x=>ids.includes(String(x.id))?{...x,status:'CANCELLED'}:x);
       loadUnifiedReceiptHistoryForOperations(true).then(()=>{if(ctxKey(ctx())===ctxKey(s.context))renderOperations();}).catch(()=>toast('Отмена выполнена, но историю пока не удалось обновить. Откройте её заново.',true));
     }
   }catch(e){
     if(s===cancelState){s.plan=null;$('r22CancelAck').checked=false;$('r22CancelMsg').textContent=e.message+'\nНе принимайте товар заново. Нажмите «Проверить заново»: повторная отмена этой операции защищена.';}
   }finally{if(s===cancelState){s.busy=false;controls();}}
 }
 document.addEventListener('click',e=>{
   const retry=e.target.closest('[data-r22-cell-retry]');if(retry){e.preventDefault();openLocationContents(retry.dataset.r22CellRetry);return;}
   const cancel=e.target.closest('[data-r22-cancel-receipt]');if(cancel){e.preventDefault();openCancel(String(cancel.dataset.r22CancelReceipt||'').split(',').filter(Boolean));}
 });
 $('r22CancelClose').addEventListener('click',()=>{if(!cancelState?.busy){++cancelSeq;dialog.close();cancelState=null;}});
 dialog.addEventListener('cancel',e=>{if(cancelState?.busy)e.preventDefault();else{++cancelSeq;cancelState=null;}});
 $('r22CancelPreview').addEventListener('click',loadCancel);
 $('r22CancelConfirm').addEventListener('click',confirmCancel);
 $('r22CancelReason').addEventListener('input',controls);$('r22CancelAck').addEventListener('change',controls);
 $('r22CancelParts').addEventListener('change',()=>{if(!cancelState||cancelState.busy)return;cancelState.ids=[...dialog.querySelectorAll('[data-r22-part]:checked')].map(x=>x.dataset.r22Part);cancelState.plan=null;$('r22CancelAck').checked=false;$('r22CancelMsg').textContent='Выбор изменён. Нажмите «Проверить заново».';controls();});
 document.addEventListener('change',e=>{if(['clientSelect','warehouseSelect'].includes(e.target?.id)&&cancelState){cancelState.plan=null;$('r22CancelAck').checked=false;$('r22CancelMsg').textContent='Клиент или склад изменён. Закройте это окно.';controls();}},true);
 window.FCR22UI={openCancel,loadCancel,confirmCancel,cellTable};
})();
