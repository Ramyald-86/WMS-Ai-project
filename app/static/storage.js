// FC_R145F14_STORAGE_CELLS_AS_BOXES_UI
(()=>{
'use strict';
const $=id=>document.getElementById(id);
let rows=[];let timer=null;
const ctx=()=>({client_id:String(state.clientId||''),warehouse_id:String(state.warehouseId||'')});
const actor=()=>String(document.getElementById('currentUser')?.textContent||'Администратор').trim()||'Администратор';
const n=v=>Number(v||0);
function fmt(v){return new Intl.NumberFormat('ru-RU',{maximumFractionDigits:3}).format(n(v));}
function summaryHtml(s){
  const cards=[
    ['К хранению, коробо-мест',s?.storage_places||s?.system_boxes||0,'короба + занятые ячейки/палеты + транзит'],
    ['Физические короба',s?.physical_boxes||0,'активные короба с товаром'],
    ['Ячейки / палеты с товаром',s?.direct_cells||0,'без короба · считаются как 1 коробо-место'],
    ['Основной склад',s?.main_boxes||0,'мест с MAIN'],
    ['FBS',s?.fbs_boxes||0,'мест с FBS'],
    ['Транзитный склад',s?.transit_boxes||0,'активных коробов'],
    ['Неразобранные ПВЗ',s?.manual_boxes||0,'коробов без ШК'],
    ['Товар в местах хранения',s?.units||0,'короба + ячейки + транзит']
  ];
  if(n(s?.outside_storage_units)>0){
    cards.push(['Вне мест хранения',s.outside_storage_units,'ПРИЕМКА / виртуальные и другие неразмещённые места']);
  }
  return cards.map(x=>`<article class="stat-card"><span>${esc(x[0])}</span><strong>${fmt(x[1])}</strong><small>${esc(x[2])}</small></article>`).join('');
}
function actions(r){if(!r.manual)return '<span class="muted">—</span>';return `<div class="storage-actions"><button class="secondary mini" type="button" data-storage-adjust="-1" data-storage-id="${esc(r.manual_id)}">−1 короб</button><button class="secondary mini" type="button" data-storage-adjust="1" data-storage-id="${esc(r.manual_id)}">+1</button><button class="danger mini" type="button" data-storage-remove="${esc(r.manual_id)}">Убрать с хранения</button></div>`;}
function splitNote(r){if(r.manual||r.source==='Транзитный склад'||!r.split_known)return '';const a=[];if(n(r.main_units)>0)a.push(`MAIN: ${fmt(r.main_units)}`);if(n(r.fbs_units)>0)a.push(`FBS: ${fmt(r.fbs_units)}`);return a.length?`<small>${a.join(' · ')}</small>`:'';}
function kindLabel(r){if(r.kind==='location')return '<small>Ячейка/палета · = 1 коробо-место</small>';if(r.kind==='transit')return '<small>Транзитный короб</small>';if(r.kind==='container')return '<small>Физический короб</small>';return '';}
function render(data){
  rows=data.rows||[];
  $('storageSummary').innerHTML=summaryHtml(data.summary||{});
  $('storageBody').innerHTML=rows.length?rows.map(r=>`<tr><td><span class="storage-source-tag">${esc(r.source||'—')}</span></td><td><b>${esc(r.code||'—')}</b>${r.barcode&&r.barcode!==r.code?`<small>ШК: ${esc(r.barcode)}</small>`:''}${kindLabel(r)}</td><td>${esc(r.location||'—')}<small>${esc(r.detail||'')}</small></td><td>${fmt(r.units||0)}${splitNote(r)}</td><td><b>${fmt(r.box_count||0)}</b></td><td>${esc(r.status||'—')}</td><td>${actions(r)}</td></tr>`).join(''):'<tr><td colspan="7" class="muted">Активных мест хранения с товаром по этому фильтру нет.</td></tr>';
  const s=data.summary||{};
  let text=`К хранению: ${fmt(s.storage_places||s.system_boxes||0)} коробо-мест · физ. коробов: ${fmt(s.physical_boxes||0)} · ячеек/палет без короба: ${fmt(s.direct_cells||0)} · товар в хранении: ${fmt(s.units||0)} ед.`;
  if(n(s.outside_storage_units)>0)text+=` · вне мест хранения: ${fmt(s.outside_storage_units)} ед.`;
  text+=` · неразобранных ПВЗ без ШК: ${fmt(s.manual_boxes||0)} кор.`;
  $('storageMsg').textContent=text;
  $('storageMsg').className='msg ok';
}
async function refresh(){
  const c=ctx();
  if(!c.client_id||!c.warehouse_id){$('storageMsg').textContent='Сначала выберите клиента и склад';$('storageMsg').className='msg error';return;}
  const q=$('storageSearch')?.value?.trim()||'',source=$('storageSource')?.value||'ALL';
  $('storageMsg').textContent='Считаем короба и занятые ячейки/палеты с товаром…';$('storageMsg').className='msg';
  try{const data=await api('/storage/overview?'+new URLSearchParams({...c,q,source}),{timeoutMs:30000});render(data);}
  catch(e){$('storageMsg').textContent=e.message;$('storageMsg').className='msg error';}
}
async function addManual(){const c=ctx(),name=$('storageManualName').value.trim(),box_count=Number($('storageManualCount').value||0),note=$('storageManualNote').value.trim();if(!name||box_count<1){toast('Укажите название и количество коробов',true);return;}const msg=$('storageManualMsg');msg.textContent='Добавляем…';msg.className='msg';try{await api('/storage/manual-boxes',{method:'POST',body:JSON.stringify({...c,name,box_count,note,performed_by:actor()})});msg.textContent=`Добавлено на хранение: ${box_count} кор.`;msg.className='msg ok';$('storageManualCount').value='1';$('storageManualNote').value='';await refresh();}catch(e){msg.textContent=e.message;msg.className='msg error';}}
async function adjust(id,delta){const c=ctx();try{const r=await api(`/storage/manual-boxes/${encodeURIComponent(id)}/adjust`,{method:'POST',body:JSON.stringify({...c,delta,reason:delta<0?'Разобран / закончился короб ПВЗ':'Добавлено вручную',performed_by:actor()})});toast(r.removed?'Короба закончились — запись убрана с хранения':`Осталось коробов: ${r.box_count}`);await refresh();}catch(e){toast(e.message,true);}}
async function remove(id){if(!confirm('Убрать эту группу коробов с хранения?'))return;const c=ctx();try{await api(`/storage/manual-boxes/${encodeURIComponent(id)}?`+new URLSearchParams(c),{method:'DELETE'});toast('Убрано с хранения');await refresh();}catch(e){toast(e.message,true);}}
function bind(){if(!$('page-storage')||$('page-storage').dataset.bound==='1')return;$('page-storage').dataset.bound='1';$('storageRefresh').onclick=refresh;$('storageManualAdd').onclick=addManual;$('storageSearch').addEventListener('input',()=>{clearTimeout(timer);timer=setTimeout(refresh,250)});$('storageSource').onchange=refresh;$('storageBody').addEventListener('click',e=>{const a=e.target.closest('[data-storage-adjust]');if(a)adjust(a.dataset.storageId,Number(a.dataset.storageAdjust));const d=e.target.closest('[data-storage-remove]');if(d)remove(d.dataset.storageRemove);});}
bind();
window.FCStorage={refresh};
})();
