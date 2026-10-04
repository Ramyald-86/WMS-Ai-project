/* FC_R145F20_WB_SUPPLY_MOVE_UI: existing server-side WB confirmation is retained. */
(()=>{
'use strict';
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let allowed=false,busy=false;
function msg(id,text,kind=''){const e=$(id);if(e){e.textContent=text;e.dataset.kind=kind;}}
async function req(url,options={}){const r=await fetch(url,{...options,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'}});const d=await r.json();if(!r.ok){const e=new Error(typeof d.detail==='string'?d.detail:d.detail?.message||`Ошибка сервера (${r.status})`);e.data=d.detail;throw e;}return d;}
function enable(){if($('wbSupplyMoveSubmit'))$('wbSupplyMoveSubmit').disabled=busy||!allowed||!$('wbCredentialSelect')?.value;}
async function capabilities(){try{const d=await req('/r145f19/wb/supply-move/capabilities');allowed=!!d.safe_detach_supported;msg('wbSupplyMoveCapability',d.message||'',allowed?'ok':'warn');}catch(e){allowed=false;msg('wbSupplyMoveCapability',e.message,'error');}enable();}
function result(d){
 const fails=d.failures||[],moved=d.moved||[];
 $('wbSupplyMoveResult').innerHTML=(moved.length?`<p>Перенесены заказы: ${esc(moved.join(', '))}</p>`:'')+(fails.length?`<div class="table-wrap"><table><thead><tr><th>Заказ</th><th>Результат</th></tr></thead><tbody>${fails.map(x=>`<tr><td>${esc(x.order_id)}</td><td>${esc(x.stage==='detach'?'WB не подтвердил снятие со старой поставки':x.rollback_ok?'Добавление в новую поставку не подтверждено; заказ возвращён в исходную':'Добавление и возврат не подтверждены. Проверьте заказ в WB')}<small>${esc(x.detail||'')}</small></td></tr>`).join('')}</tbody></table></div>`:'');
}
async function move(e){
 e.preventDefault();if(busy||!allowed)return;
 const credential=$('wbCredentialSelect')?.value,warehouse=$('warehouseSelect')?.value;
 if(!credential||!warehouse)throw new Error('Выберите склад и подключение WB');
 const fd=new FormData(e.currentTarget),source=String(fd.get('source')||'').trim(),target=String(fd.get('target')||'').trim();
 const ids=[...new Set(String(fd.get('orders')||'').split(/[\s,;]+/).filter(Boolean))];
 if(!source||!target||source===target)throw new Error('Укажите две разные поставки');
 if(!ids.length||ids.length>1000||ids.some(x=>!/^\d+$/.test(x)))throw new Error('Укажите от 1 до 1000 числовых номеров заказов WB');
 if(!confirm(`Перенести ${ids.length} заказов?\nИз: ${source}\nВ: ${target}\n\nСвязь изменится только через подтверждение WB. Не закрывайте страницу до результата.`))return;
 busy=true;$('wbSupplyMoveFields').disabled=true;msg('wbSupplyMoveMsg','Выполняется перенос. Не отправляйте его повторно.');$('wbSupplyMoveResult').textContent='';
 try{
   const d=await req('/r145f19/wb/supply-move',{method:'POST',body:JSON.stringify({credential_id:credential,warehouse_id:warehouse,source_supply_id:source,target_supply_id:target,order_ids:ids,wb_warehouse_id:null})});
   result(d);msg('wbSupplyMoveMsg',`Перенесено: ${(d.moved||[]).length}. Ошибок: ${(d.failures||[]).length}.`,d.failures?.length?'warn':'ok');
 }catch(err){if(err.data?.failures)result(err.data);msg('wbSupplyMoveMsg',err.message+'\nПеред повтором проверьте состояние поставок в WB. Автоматический повтор не выполняется.','error');}
 finally{busy=false;$('wbSupplyMoveFields').disabled=false;enable();}
}
function init(){if(!$('wbSupplyMovePanel'))return;$('wbSupplyMovePanel').addEventListener('toggle',()=>{if($('wbSupplyMovePanel').open)capabilities();});$('wbSupplyMoveForm').addEventListener('submit',e=>{e.preventDefault();move(e).catch(x=>msg('wbSupplyMoveMsg',x.message,'error'));});$('wbCredentialSelect')?.addEventListener('change',enable);}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
