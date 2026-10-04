/* Preserve terminal v1.6c catalog, labels and Excel; bind its draft to WMS. */
(()=>{'use strict';const W=window.FCWorkflow,T=window.receivingTerminal,B=T.wms,$=id=>document.getElementById(id);let ctx=null;
  const saving=new Map();
  async function saveLocal(row){if(!W.valid())return;if(saving.has(row.barcode))return saving.get(row.barcode);const p=W.post('/warehouse-ui/catalog/local',{client_id:W.scope.client_id,barcode:row.barcode,article:row.article,size:row.size,name:row.name||''}).then(r=>{B.localSaved(r);return r;}).finally(()=>saving.delete(row.barcode));saving.set(row.barcode,p);return p;}
  async function flushLocal(){for(const row of B.pendingProducts())await saveLocal(row);}
  window.addEventListener('fc-local-product',e=>saveLocal(e.detail).then(()=>message('Товар сохранён в справочнике WMS. Остатки изменятся после подтверждения приёмки.')).catch(e=>message('Товар в черновике. Сохранение в WMS пока не выполнено: '+e.message,true)));
  const stable=()=>{const p=T.buildPackage();delete p.exportedAt;return JSON.stringify(p);};
  function message(text,bad=false){$('wmsMessage').textContent=text;$('wmsMessage').className='status '+(bad?'bad':'ok');}
  function lock(){const locked=B.isLocked();document.querySelectorAll('#receivingPage input,#receivingPage select,#receivingPage button').forEach(el=>{if(locked){if(!el.disabled)el.dataset.wmsDisabled='1';el.disabled=true;}else if(el.dataset.wmsDisabled){el.disabled=false;delete el.dataset.wmsDisabled;}});if(!locked&&!B.currentBox())['itemScan','addItemBtn','finishBoxBtn','deleteBoxBtn'].forEach(id=>$(id).disabled=true);$('wmsPreview').disabled=locked;['wmsSetAddress','wmsLoose','wmsResync','wmsClosePallet'].forEach(id=>$(id).disabled=locked);const posted=T.state().draft.wmsPosted;if(posted)message('✓ Приёмка проведена в WMS. Для следующей партии нажмите «Новая приёмка».');else if(locked)message('Черновик временно заблокирован: идёт операция или он изменён в другой вкладке.');const id=T.state().draft.wmsPlan||posted?.plan_id;$('wmsLast').hidden=!id;}
  window.addEventListener('fc-terminal-render',lock);
  async function context(){ctx=await W.api(W.scoped('/workflow/context'));$('wmsContext').textContent=ctx.client.name+' · '+ctx.warehouse.name;B.catalog(ctx.catalog);await flushLocal();$('wmsLocations').innerHTML=ctx.locations.filter(x=>['CELL','PALLET','PALLET_PLACE'].includes(x.kind)).map(x=>`<option value="${W.esc(x.code)}">${W.esc(x.name)}</option>`).join('');message('WMS подключена. Черновик сохраняется на этом компьютере.');lock();}
  $('wmsRefresh').onclick=async()=>{try{await context();}catch(e){message(e.message,true);}};
  $('wmsSetAddress').onclick=()=>{const raw=$('wmsAddress').value.trim();if(!raw){B.address('');$('wmsAddressHint').textContent='Выбран автоматический адрес по секции и паллетоместу.';return;}const matches=(ctx?.locations||[]).filter(x=>['CELL','PALLET','PALLET_PLACE'].includes(x.kind)&&[x.code,x.barcode].some(v=>String(v||'').toLowerCase()===raw.toLowerCase()));if(matches.length!==1){message('Выберите существующий адрес или сначала добавьте его в разделе «Адреса склада».',true);return;}const loc=matches[0];B.address(loc.code);$('wmsAddress').value=loc.code;$('wmsAddressHint').textContent='Выбран адрес: '+loc.code+' · '+loc.name;};
  $('wmsClosePallet').onclick=()=>{if(T.state().draft.wmsDestination){message('Эта кнопка нужна при автоматическом заполнении палет. Для выбранной ячейки завершите текущий короб.',true);return;}B.closePallet();};
  $('wmsLoose').onclick=()=>{if(!T.state().draft.wmsDestination){message('Для приёмки без короба сначала выберите адрес WMS.',true);return;}B.loose();};
  $('wmsResync').onclick=async()=>{try{await context();if(confirm('Заменить артикулы и размеры известных товаров в черновике на значения из WMS? Количества и адреса сохранятся.'))message('Сверено позиций: '+B.resyncDraft(ctx.catalog));}catch(e){message(e.message,true);}};
  const options=snapshot=>({beforeConfirm:()=>{if(stable()!==snapshot)throw Error('Черновик изменился. Закройте окно и нажмите «Проверить» заново.');B.setBusy(true);},onConfirmed:r=>{B.posted(r);parent.postMessage({type:'fc-warehouse-changed',...W.scope},location.origin);message('✓ Товар принят на Основной склад. Можно создать сборку.');},onFinished:()=>B.setBusy(false)});
  $('wmsPreview').onclick=async()=>{const btn=$('wmsPreview');btn.disabled=true;try{if(!W.valid())throw Error('Откройте терминал из WMS после выбора клиента и склада');await flushLocal();const snapshot=stable();const p=await W.post('/workflow/receipts/preview',{...W.scope,package:T.buildPackage()});B.savePlan(p.plan_id);if(p.status==='CONFIRMED')B.posted(p);W.showPlan(p,options(snapshot));}catch(e){message(e.message,true);}finally{lock();}};
  $('wmsLast').onclick=async()=>{try{const id=T.state().draft.wmsPosted?.plan_id||T.state().draft.wmsPlan;const p=await W.api(W.scoped('/workflow/plans/'+id));if(p.status==='CONFIRMED')B.posted(p);W.showPlan(p,{beforeConfirm:()=>{throw Error('Для проведения закройте это окно и нажмите «Проверить и принять в WMS».');}});}catch(e){message(e.message,true);}};
  // A fast keyboard wedge adds after an idle gap; Enter/Tab cancels the timer.
  for(const [id,scan] of [['itemScan',B.scanItem],['boxScan',B.scanBox]]){
    const input=$(id);let timer,started=0,last=0,count=0,lastValue='';
    input.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key==='Tab'){clearTimeout(timer);count=0;lastValue='';}},true);
    input.addEventListener('input',e=>{
      clearTimeout(timer);const now=performance.now();if(now-last>100||!input.value.startsWith(lastValue)){started=now;count=0;}count++;last=now;lastValue=input.value;
      const wedge=(count>=5&&now-started<Math.max(400,input.value.length*45))||String(e.data||'').length>=8;
      if(!$('wmsAutoScan').checked||!wedge)return;
      const value=input.value;timer=setTimeout(()=>{if(input.value!==value||B.isLocked()||input.disabled||document.activeElement!==input)return;count=0;lastValue='';scan();},240);
    });
  }
  window.addEventListener('message',e=>{if(e.origin===location.origin&&e.source===parent&&e.data?.type==='fc-addresses-refresh'&&e.data.client_id===W.scope.client_id&&e.data.warehouse_id===W.scope.warehouse_id)context().catch(x=>message(x.message,true));});
  if(W.valid())context().catch(e=>message('WMS недоступна. Можно продолжать черновик и сохранить Excel. '+e.message,true));else message('Терминал открыт без клиента/склада. Откройте его через «Приёмка и сборка» в WMS. Черновик и Excel доступны.',true);
  lock();
})();
