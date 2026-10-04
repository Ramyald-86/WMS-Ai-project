/* FC_R145F20_DEFECT_BULK_UI: added controls, original defect/print functions stay intact. */
(()=>{
'use strict';
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const S={scope:'',selected:new Set(),all:false,busy:false,preview:null};
function ctx(){return {client_id:$('clientSelect')?.value||'',warehouse_id:$('warehouseSelect')?.value||''};}
function key(c=ctx()){return c.client_id+'|'+c.warehouse_id;}
function label(id){return $(id)?.selectedOptions?.[0]?.textContent||'Не выбран';}
function qty(n){return Number(n||0).toLocaleString('ru-RU',{maximumFractionDigits:3});}
function msg(text='',bad=false){const e=$('r20DefectMsg');if(e){e.textContent=text;e.className='fc-finance-msg';e.dataset.kind=bad?'error':'ok';}}
async function api(path,body){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),120000);
 try{
  const r=await fetch('/defect-storage/bulk/'+path,{method:body?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
  const data=await r.json();if(!r.ok){const e=new Error(typeof data.detail==='string'?data.detail:`Ошибка сервера (${r.status})`);e.status=r.status;throw e;}return data;
 }catch(e){if(e.name==='AbortError')throw new Error('Ответ не получен. Не создавайте новое списание: повторите подтверждение этой же операции — повторного списания не будет.');throw e;}finally{clearTimeout(timer);}
}
function lines(){return [...document.querySelectorAll('#defectStorageContent [data-defect-line]')].map(row=>({row,id:row.dataset.defectLine,quantity:Number(row.querySelector('[data-remaining]')?.dataset.remaining||0)}));}
function sync(){
 const k=key();
 if(k!==S.scope){S.scope=k;S.selected.clear();S.all=false;if(!S.busy){S.preview=null;if($('r20DefectDialog')?.open)$('r20DefectDialog').close();}}
 const rows=lines(),known=new Set(rows.map(x=>x.id));
 if(!S.all)S.selected=new Set([...S.selected].filter(id=>known.has(id)));
 for(const x of rows){if(S.all)S.selected.add(x.id);const cb=x.row.querySelector('[data-r20-defect-select]');if(cb)cb.checked=S.selected.has(x.id);}
 const chosen=rows.filter(x=>S.selected.has(x.id));
 if($('r20DefectSelected'))$('r20DefectSelected').textContent=S.all?`Выбран весь брак клиента «${label('clientSelect')}» на складе «${label('warehouseSelect')}». Количество проверится перед списанием.`:`Выбрано: ${chosen.length} позиций / ${qty(chosen.reduce((s,x)=>s+x.quantity,0))} шт.`;
 if($('r20DefectPrepare'))$('r20DefectPrepare').disabled=S.busy||(!S.all&&!S.selected.size);
 for(const id of ['r20DefectSelectAll','r20DefectClear'])if($(id))$(id).disabled=S.busy;
}
function inject(){
 const host=$('defectStorageContent');if(!host)return;
 for(const x of lines()){
  if(x.row.querySelector('[data-r20-defect-select]'))continue;
  const td=document.createElement('td'),cb=document.createElement('input');cb.type='checkbox';cb.dataset.r20DefectSelect=x.id;cb.setAttribute('aria-label','Выбрать позицию брака');td.appendChild(cb);x.row.prepend(td);
  const header=x.row.closest('table')?.querySelector('thead tr');if(header&&!header.querySelector('[data-r20-select-heading]')){const th=document.createElement('th');th.dataset.r20SelectHeading='1';th.textContent='Выбор';header.prepend(th);}
 }
 sync();
}
async function preview(){
 if(S.busy)return;sync();const c=ctx(),k=key(c);if(!c.client_id||!c.warehouse_id)throw new Error('Выберите клиента и склад');
 const actor=(typeof state!=='undefined'?state.commercialSession?.username:'')||$('currentUser')?.textContent?.trim()||'Администратор';
 S.busy=true;sync();msg('Проверяем состав и количество брака…');
 try{
  const d=await api('preview',{...c,all_active:S.all,line_ids:S.all?[]:[...S.selected]});if(k!==key())return;
  S.preview={...d,...c,actor};
  $('r20DefectConfirmText').textContent=`Клиент: ${label('clientSelect')}\nСклад: ${label('warehouseSelect')}\n\nБудет списано: ${qty(d.quantity)} шт.\nПозиций: ${d.lines_count}. Коробов: ${d.boxes_count}.\n\nИзменится только отдельный учёт брака. Основной склад, FBS и история приёмки не удаляются.`;
  $('r20DefectReason').value='';$('r20DefectConfirmed').checked=false;$('r20DefectConfirm').disabled=true;
  $('r20DefectDialogMsg').textContent='';$('r20DefectDialog').showModal();msg('Состав проверен. Подтвердите списание.');
 }finally{S.busy=false;sync();}
}
async function commit(){
 if(S.busy||!S.preview||!$('r20DefectConfirmed').checked)return;
 const p=S.preview;if(key(p)!==key())throw new Error('Клиент или склад изменился. Закройте подтверждение и обновите выбор.');
 S.busy=true;$('r20DefectConfirm').disabled=true;$('r20DefectClose').disabled=true;sync();
 $('r20DefectDialogMsg').textContent='Выполняется списание… Не закрывайте окно.';
 try{
  const result=await api('writeoff',{client_id:p.client_id,warehouse_id:p.warehouse_id,preview_id:p.preview_id,confirmed:true,reason:$('r20DefectReason').value.trim(),performed_by:p.actor});
  S.selected.clear();S.all=false;S.preview=null;$('r20DefectDialog').close();
  if(key(p)===key()){
   msg(`Списано ${qty(result.written_off)} шт. (${result.lines_count} позиций). Операция сохранена в истории.${result.replayed?' Повторный запрос не создал повторного списания.':''}`);
   await window.FCDefectStorage?.refresh?.();inject();if($('r20DefectHistory')?.open)await history();
  }
 }catch(e){$('r20DefectDialogMsg').textContent=e.message;throw e;}
 finally{S.busy=false;$('r20DefectClose').disabled=false;$('r20DefectConfirm').disabled=!$('r20DefectConfirmed').checked;sync();}
}
async function history(){
 const c=ctx(),k=key(c);$('r20DefectHistoryRows').textContent='Загрузка…';
 const d=await api('history?'+new URLSearchParams(c));if(k!==key())return;
 $('r20DefectHistoryRows').innerHTML=(d.rows||[]).map(r=>`<div class="r20-defect-history-row"><b>${esc(new Date(r.completed_at).toLocaleString('ru-RU'))} · ${qty(r.total_qty)} шт.</b><span>${r.lines_count} позиций · ${esc(r.performed_by||'')}</span><small>${esc(r.reason||'Массовое списание брака')}</small></div>`).join('')||'<p>Массовых списаний ещё нет.</p>';
}
function init(){
 const host=$('defectStorageContent');if(!host||$('r20DefectToolbar'))return;
 const bar=document.createElement('div');bar.id='r20DefectToolbar';bar.innerHTML=`<div class="r20-defect-bulk-actions"><button id="r20DefectSelectAll" type="button" class="secondary">Выбрать весь брак</button><button id="r20DefectClear" type="button" class="secondary">Снять выбор</button><button id="r20DefectPrepare" type="button" class="danger" disabled>Списать выбранное</button></div><p id="r20DefectSelected"></p><div id="r20DefectMsg" class="fc-finance-msg" role="status" aria-live="polite"></div><details id="r20DefectHistory"><summary>История массовых списаний · последние 200</summary><div id="r20DefectHistoryRows"></div></details>`;
 host.before(bar);
 const dialog=document.createElement('dialog');dialog.id='r20DefectDialog';dialog.className='fc-finance-dialog';dialog.innerHTML=`<h2>Списание брака</h2><p id="r20DefectConfirmText" style="white-space:pre-line"></p><label>Причина / комментарий<textarea id="r20DefectReason" maxlength="1000" rows="3"></textarea></label><label class="fc-finance-category-option"><input id="r20DefectConfirmed" type="checkbox"><span>Подтверждаю состав и количество. Списать товар из хранения брака.</span></label><div id="r20DefectDialogMsg" class="fc-finance-msg" role="status"></div><div class="fc-finance-actions"><button id="r20DefectClose" type="button" class="secondary">Отмена</button><button id="r20DefectConfirm" type="button" class="danger" disabled>Подтвердить списание</button></div>`;
 document.body.appendChild(dialog);
 const style=document.createElement('style');style.textContent='#r20DefectToolbar{padding:14px;border:1px solid #dce3ed;border-radius:12px;margin:16px 0;background:#f8faff}.r20-defect-bulk-actions{display:flex;flex-wrap:wrap;gap:9px}#r20DefectToolbar p{font-size:14px}#r20DefectHistory{margin-top:12px}#r20DefectHistory summary{cursor:pointer}.r20-defect-history-row{display:grid;gap:4px;padding:10px;border-bottom:1px solid #dfe5ed}#defectStorageContent [data-r20-defect-select]{width:20px;height:20px;cursor:pointer}#r20DefectDialog textarea{width:100%;box-sizing:border-box;padding:10px;font:inherit;border:1px solid #cbd4df;border-radius:10px}';document.head.appendChild(style);
 const safe=fn=>()=>Promise.resolve().then(fn).catch(e=>msg(e.message,true));
 $('r20DefectSelectAll').onclick=()=>{S.all=true;sync();};
 $('r20DefectClear').onclick=()=>{S.all=false;S.selected.clear();sync();};
 $('r20DefectPrepare').onclick=safe(preview);
 $('r20DefectConfirmed').onchange=()=>{$('r20DefectConfirm').disabled=S.busy||!$('r20DefectConfirmed').checked;};
 $('r20DefectConfirm').onclick=safe(commit);$('r20DefectClose').onclick=()=>{if(!S.busy){dialog.close();S.preview=null;}};
 dialog.addEventListener('cancel',e=>{if(S.busy)e.preventDefault();});
 host.addEventListener('change',e=>{const id=e.target.dataset.r20DefectSelect;if(!id)return;S.all=false;e.target.checked?S.selected.add(id):S.selected.delete(id);sync();});
 $('r20DefectHistory').addEventListener('toggle',()=>{if($('r20DefectHistory').open)history().catch(e=>{$('r20DefectHistoryRows').textContent=e.message;});});
 for(const id of ['clientSelect','warehouseSelect'])$(id)?.addEventListener('change',()=>{sync();$('r20DefectHistoryRows').textContent='Обновите историю для выбранного клиента.';msg();});
 let scheduled=false;new MutationObserver(()=>{if(!scheduled){scheduled=true;queueMicrotask(()=>{scheduled=false;inject();});}}).observe(host,{childList:true,subtree:true});
 inject();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
