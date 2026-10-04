/* FC_K9R64_WMS_RECEIVING_PICKING_FBS */
(()=>{'use strict';const W=window.FCWorkflow,$=id=>document.getElementById(id);let ctx,tab='receive';
  const error=e=>{$('wfGlobalError').textContent=e.message||String(e);$('wfGlobalError').classList.remove('hidden');};
  async function busy(button,fn){button.disabled=true;$('wfGlobalError').classList.add('hidden');try{await fn();}catch(e){error(e);}finally{button.disabled=false;}}
  function show(name){tab=name;document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===name));document.querySelectorAll('[data-panel]').forEach(p=>p.hidden=p.dataset.panel!==name);if(name==='history')history().catch(error);}
  document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>show(b.dataset.tab));
  $('wfBack').onclick=()=>W.go('inventory');$('wfFbsPicks').onclick=()=>W.go('fbspicks');$('wfLayout').onclick=()=>W.go('locations');$('wfPicks').onclick=()=>W.go('warehousework');
  async function context(){ctx=await W.api(W.scoped('/workflow/context'));$('wfContext').textContent=ctx.client.name+' · '+ctx.warehouse.name;addresses();}
  function addresses(){const search=$('wfAddressSearch').value.trim().toLowerCase();$('wfAddresses').innerHTML='<table><thead><tr><th>Адрес</th><th>Тип</th><th>Название</th><th></th></tr></thead><tbody>'+ctx.locations.filter(x=>[x.code,x.name,x.barcode].join(' ').toLowerCase().includes(search)).map(x=>`<tr><td><b>${W.esc(x.code)}</b></td><td>${W.esc(({CELL:'Ячейка',SECTION:'Секция',PALLET:'Палета',PALLET_PLACE:'Паллетоместо'})[x.kind]||x.kind)}</td><td>${W.esc(x.name)}</td><td><button class="secondary" data-location="${W.esc(x.id)}">Открыть</button></td></tr>`).join('')+'</tbody></table>';}
  $('wfAddressSearch').oninput=()=>ctx&&addresses();$('wfAddresses').onclick=e=>{const b=e.target.closest('[data-location]');if(b)W.go('locations',{location_id:b.dataset.location});};
  $('wfAddressRefresh').onclick=e=>busy(e.target,context);
  $('wfKind').onchange=()=>{const k=$('wfKind').value;$('wfRackLabel').hidden=$('wfFloorsLabel').hidden=k!=='RACK';$('wfFromLabel').hidden=$('wfToLabel').hidden=$('wfAisleLabel').hidden=k==='SECTION';};
  $('wfAddressForm').onsubmit=e=>{e.preventDefault();busy(e.submitter,async()=>{
    const kind=$('wfKind').value,a=+$('wfFrom').value,b=+$('wfTo').value,p={warehouse_id:W.scope.warehouse_id,kind,section:$('wfSection').value.trim(),aisle:$('wfAisle').value.trim()};
    if(kind!=='SECTION'&&(!Number.isInteger(a)||!Number.isInteger(b)||a<1||b<a))throw Error('Проверьте первый и последний номер');
    if(kind==='CELL'||kind==='RACK'){p.cell_from=a;p.cell_to=b;}
    if(kind==='RACK'){p.rack=$('wfRack').value.trim();p.floor_from=1;p.floor_to=+$('wfFloors').value;}
    if(kind==='PALLET'){p.pallet_from=a;p.pallet_to=b;}
    if(kind==='PALLET_PLACE'){p.pallet_place_from=a;p.pallet_place_to=b;}
    await W.post('/layout/locations/bulk',p);$('wfAddressResult').textContent='Адреса готовы. Теперь их можно выбрать в приёмке.';await context();
    $('wfTerminal').contentWindow?.postMessage({type:'fc-addresses-refresh',...W.scope},location.origin);
  });};
  async function upload(input,path){const f=$(input).files[0];if(!f)throw Error('Сначала выберите файл Excel');if(!/\.xlsx$/i.test(f.name))throw Error('Выберите файл .xlsx');if(f.size>8*1024*1024)throw Error('Размер файла больше 8 МБ');const fd=new FormData();Object.entries(W.scope).forEach(([k,v])=>fd.append(k,v));fd.append('file',f);return W.api(path,{method:'POST',body:fd});}
  $('wfReceiptImport').onclick=e=>busy(e.target,async()=>W.showPlan(await upload('wfReceiptFile','/workflow/receipts/import'),{onConfirmed:history}));
  const key='fc-fbs-plan:'+W.scope.client_id+':'+W.scope.warehouse_id;
  function lastPlan(id){$('wfTransferLast').innerHTML='<button class="secondary" id="wfReopenPlan">Открыть последний план перевода</button>';$('wfReopenPlan').onclick=e=>busy(e.target,async()=>W.showPlan(await W.api(W.scoped('/workflow/plans/'+id)),{onConfirmed:history}));}
  $('wfTransferPreview').onclick=e=>busy(e.target,async()=>{const p=await upload('wfTransferFile','/workflow/transfer/preview');localStorage.setItem(key,p.plan_id);lastPlan(p.plan_id);W.showPlan(p,{onConfirmed:history});});
  async function history(){const d=await W.api(W.scoped('/workflow/plans'));$('wfHistory').innerHTML='<table><thead><tr><th>Когда</th><th>Операция</th><th>Состояние</th><th>Сотрудник</th><th>Шт.</th><th></th></tr></thead><tbody>'+d.rows.map(r=>`<tr><td>${W.esc(new Date(r.confirmed_at||r.created_at).toLocaleString('ru-RU'))}</td><td>${r.kind==='RECEIPT'?'Приёмка':'Перевод в FBS'}<small>${W.esc(r.package_id||r.file_name||'')}</small></td><td>${r.status==='CONFIRMED'?'Проведено':r.status==='CANCELLED'?'Отменено':'Предпросмотр'}</td><td>${W.esc(r.actor||'—')}</td><td>${W.esc(r.units||0)}</td><td><button data-plan="${W.esc(r.id)}" class="secondary">Открыть</button></td></tr>`).join('')+'</tbody></table>';}
  $('wfHistory').onclick=e=>{const b=e.target.closest('[data-plan]');if(b)busy(b,async()=>W.showPlan(await W.api(W.scoped('/workflow/plans/'+b.dataset.plan)),{onConfirmed:history}));};$('wfHistoryRefresh').onclick=e=>busy(e.target,history);
  window.addEventListener('message',e=>{if(e.origin===location.origin&&e.source===parent&&e.data?.type==='fc-select-transfer'){show('fbs');return;}if(e.origin===location.origin&&e.source===$('wfTerminal').contentWindow&&e.data?.type==='fc-warehouse-changed'){parent.postMessage(e.data,location.origin);return;}if(e.origin!==location.origin||e.source!==$('wfTerminal').contentWindow||!e.data||e.data.client_id!==W.scope.client_id||e.data.warehouse_id!==W.scope.warehouse_id)return;if(e.data.type==='fc-workflow-go')W.go(e.data.page,{location_id:e.data.location_id||'',task_id:e.data.task_id||''});});
  if(!W.valid()){error(Error('Выберите клиента и склад в WMS, затем откройте «Приёмка и сборка».'));document.querySelectorAll('button,input,select').forEach(x=>x.disabled=true);return;}
  $('wfTerminal').src=W.scoped('/receiving-terminal');context().catch(error);const saved=localStorage.getItem(key);if(saved&&/^[0-9a-f-]{36}$/.test(saved))lastPlan(saved);
})();
