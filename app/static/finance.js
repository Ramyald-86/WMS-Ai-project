/* FC_R145F20_FINANCE_UI: category picker, atomic tariffs, separate history. */
(()=>{
'use strict';
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const number=v=>Number(v||0).toLocaleString('ru-RU',{maximumFractionDigits:4});
const money=v=>number(v)+' ₽';
const labels={RECEIVING:'Приёмка товара',PVZ_GOOD:'Приёмка ПВЗ',PVZ_DEFECT:'Брак ПВЗ',TRANSIT_RECEIVE:'Приёмка транзита',FBO:'FBO',SHIPPING:'Отгрузка / списание',STORAGE_BOX_DAY:'Хранение · коробо-день'};
const S={scope:'',seq:0,categorySeq:0,selected:new Set(),draft:new Set(),options:[],tariffs:[],tariffPage:0,history:[],historyPage:0,saving:false};
const PAGE=40, HPAGE=50;
function ctx(){return {client_id:$('clientSelect')?.value||'',warehouse_id:$('warehouseSelect')?.value||''};}
function key(c=ctx()){return c.client_id+'|'+c.warehouse_id;}
function name(id){return $(id)?.selectedOptions?.[0]?.textContent||'Не выбран';}
function localDate(d=new Date()){return [d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');}
function message(id,text='',kind=''){const e=$(id);if(e){e.textContent=text;e.dataset.kind=kind;}}
async function request(url,options={}){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),options.timeout||60000);
 try{const r=await fetch(url,{...options,credentials:'same-origin',cache:'no-store',signal:controller.signal,headers:{'Content-Type':'application/json',...(options.headers||{})}});
 const ct=r.headers.get('content-type')||'',data=ct.includes('json')?await r.json():await r.text();
 if(!r.ok){const detail=data?.detail??data?.message??data;throw new Error(typeof detail==='string'?detail:detail?.message||`Ошибка сервера (${r.status})`);}
 return data;
 }catch(e){if(e.name==='AbortError')throw new Error('Сервер не ответил вовремя. Обновите данные перед повторным действием.');throw e;}finally{clearTimeout(timer);}
}
function empty(id,col,text){if($(id))$(id).innerHTML=`<tr><td colspan="${col}" class="empty-state">${esc(text)}</td></tr>`;}
function syncContext(){
 const k=key();
 document.querySelectorAll('[data-finance-client]').forEach(e=>e.textContent=name('clientSelect'));
 document.querySelectorAll('[data-finance-warehouse]').forEach(e=>e.textContent=name('warehouseSelect'));
 if(k===S.scope)return;
 S.scope=k;S.seq++;S.categorySeq++;S.selected.clear();S.draft.clear();S.tariffs=[];S.history=[];S.tariffPage=0;S.historyPage=0;
 if($('financeCategoryDialog')?.open)$('financeCategoryDialog').close();
 if($('financeTariffRate'))$('financeTariffRate').value='';
 for(const id of ['financeTotal','financeStoragePlaces','financeStorageAmount','financeHistoryTotal'])if($(id))$(id).textContent='—';
 empty('financeSummary',4,'Обновите сводку');empty('financeTariffs',6,'Загрузка тарифов…');empty('financeHistoryRows',7,'Покажите историю для выбранного клиента');
 selectionLabel();renderHistory();
}
function selectionLabel(){
 const list=[...S.selected],text=list.includes('*')?'Общий тариф / по умолчанию':list.length?`${list.length} категорий: ${list.slice(0,4).join(', ')}${list.length>4?'…':''}`:'Категории не выбраны';
 $('financeSelectionLabel').textContent=text;
 const storage=$('financeTariffService').value==='STORAGE_BOX_DAY';
 $('financeStorageTariffHint').hidden=!storage;
 $('financeChooseCategories').disabled=storage||S.saving;
}
function requireContext(){const c=ctx();if(!c.client_id||!c.warehouse_id)throw new Error('Выберите клиента и склад');return c;}
function dates(prefix){const from=$(prefix==='history'?'financeHistoryFrom':'financeDateFrom').value,to=$(prefix==='history'?'financeHistoryTo':'financeDateTo').value;if(!from||!to)throw new Error('Укажите обе даты');if(from>to)throw new Error('Начальная дата позже конечной');return {date_from:from,date_to:to};}
async function report(kind){
 syncContext();const c=requireContext(),k=key(c),d=dates(kind),stamp=++S.seq;
 message(kind==='history'?'financeHistoryMsg':'financeMsg','Загрузка…');
 const data=await request('/finance/report?'+new URLSearchParams({...c,...d}));
 if(key()!==k||stamp!==S.seq)return;
 if(kind==='history'){
   S.history=[...(data.rows||[]),...(data.storage?.days||[]).map(r=>({created_at:r.snapshot_date,service_code:'STORAGE_BOX_DAY',service:labels.STORAGE_BOX_DAY,reference:'Хранение за сутки',category:'*',quantity:r.box_places,rate:data.storage.rate,amount:Number(r.box_places)*Number(data.storage.rate),unit:'коробо-день'}))].sort((a,b)=>String(b.created_at).localeCompare(String(a.created_at)));
   S.historyPage=0;const select=$('financeHistoryCategory'),old=select.value;
   const cats=[...new Set(S.history.map(x=>x.category||'Без категории'))].sort((a,b)=>a.localeCompare(b,'ru'));
   select.innerHTML='<option value="">Все категории</option>'+cats.map(c=>`<option value="${esc(c)}">${esc(c==='*'?'Общий тариф':c)}</option>`).join('');
   if(cats.includes(old))select.value=old;renderHistory();message('financeHistoryMsg','История загружена','ok');
 }else{
   $('financeTotal').textContent=money(data.total_amount);$('financeStoragePlaces').textContent=number(data.storage?.today_places);
   $('financeStorageAmount').textContent=money(data.storage?.amount);$('financeRange').textContent=`${data.date_from} — ${data.date_to}`;
   $('financeSummary').innerHTML=(data.summary||[]).map(r=>`<tr><td>${esc(r.service||labels[r.service_code]||r.service_code)}</td><td>${esc(r.category==='*'?'Общий тариф':r.category)}</td><td data-number>${number(r.quantity)}</td><td data-number><b>${money(r.amount)}</b></td></tr>`).join('')||'<tr><td colspan="4" class="empty-state">Начислений за этот период нет</td></tr>';
   message('financeMsg','Сводка обновлена по фактическим операциям и действующим тарифам','ok');
 }
}
async function tariffs(){
 const c=requireContext(),k=key(c);const d=await request('/finance/tariffs?'+new URLSearchParams({client_id:c.client_id}));if(k!==key())return;
 S.tariffs=d.rows||[];renderTariffs();
}
function renderTariffs(){
 const q=($('financeTariffSearch')?.value||'').trim().toLocaleLowerCase('ru');
 const rows=S.tariffs.filter(r=>`${labels[r.service_code]||r.service_code} ${r.category} ${r.note||''}`.toLocaleLowerCase('ru').includes(q));
 S.tariffPage=Math.min(S.tariffPage,Math.max(0,Math.ceil(rows.length/PAGE)-1));
 const start=S.tariffPage*PAGE;
 $('financeTariffs').innerHTML=rows.slice(start,start+PAGE).map(r=>`<tr><td>${esc(labels[r.service_code]||r.service_code)}</td><td>${esc(r.category==='*'?'Общий тариф':r.category)}</td><td data-number><b>${money(r.rate)}</b></td><td>${esc(r.unit)}</td><td>${esc(r.note||'')}</td><td><button type="button" class="secondary" data-finance-edit="${esc(r.id)}">Изменить</button></td></tr>`).join('')||'<tr><td colspan="6" class="empty-state">Тарифы не найдены</td></tr>';
 $('financeTariffRange').textContent=rows.length?`${start+1}–${Math.min(start+PAGE,rows.length)} из ${rows.length}`:'Тарифов нет';
 $('financeTariffPrev').disabled=!S.tariffPage;$('financeTariffNext').disabled=start+PAGE>=rows.length;
}
function renderHistory(){
 const service=$('financeHistoryService').value,cat=$('financeHistoryCategory').value,q=$('financeHistorySearch').value.trim().toLocaleLowerCase('ru');
 const rows=S.history.filter(r=>(!service||r.service_code===service)&&(!cat||(r.category||'Без категории')===cat)&&(!q||[r.reference,r.worker,r.article,r.barcode,r.name].join(' ').toLocaleLowerCase('ru').includes(q)));
 S.historyPage=Math.min(S.historyPage,Math.max(0,Math.ceil(rows.length/HPAGE)-1));const start=S.historyPage*HPAGE;
 $('financeHistoryRows').innerHTML=rows.slice(start,start+HPAGE).map(r=>`<tr><td>${esc(String(r.created_at||'').slice(0,10))}</td><td>${esc(labels[r.service_code]||r.service)}</td><td><b>${esc(r.reference||'Без названия')}</b><small>${esc([r.worker,r.article,r.size,r.barcode].filter(Boolean).join(' · '))}</small></td><td>${esc(r.category==='*'?'Общий тариф':r.category||'Без категории')}</td><td data-number>${number(r.quantity)}${r.service_code==='STORAGE_BOX_DAY'?' коробо-дн.':''}</td><td data-number>${number(r.rate)}</td><td data-number><b>${money(r.amount)}</b></td></tr>`).join('')||'<tr><td colspan="7" class="empty-state">Нет операций по выбранным фильтрам</td></tr>';
 $('financeHistoryCount').textContent=`Строк: ${rows.length}`;$('financeHistoryTotal').textContent=`Итого: ${money(rows.reduce((sum,r)=>sum+Number(r.amount||0),0))}`;
 $('financeHistoryRange').textContent=rows.length?`${start+1}–${Math.min(start+HPAGE,rows.length)} из ${rows.length}`:'Нет строк';
 $('financeHistoryPrev').disabled=!S.historyPage;$('financeHistoryNext').disabled=start+HPAGE>=rows.length;
}
function renderCategories(){
 $('financeCategoryDefault').checked=S.draft.has('*');
 $('financeCategoryList').innerHTML=S.options.map((r,i)=>`<label class="fc-finance-category-option"><input type="checkbox" data-category-index="${i}" ${S.draft.has(r.category)?'checked':''}><span><b>${esc(r.category)}</b><small>Карточек: ${number(r.products)}</small></span></label>`).join('')||'<p>Категории не найдены. Обновите категории из карточек WB.</p>';
 $('financeCategoryCount').textContent=`Найдено категорий: ${S.options.length}`;
 $('financeCategorySelectedCount').textContent=S.draft.has('*')?'Выбран общий тариф':`Выбрано: ${S.draft.size}`;
}
async function categorySearch(){
 const c=requireContext(),k=key(c),stamp=++S.categorySeq;message('financeCategoryMsg','Загрузка категорий…');
 try{const d=await request('/finance/category-options?'+new URLSearchParams({client_id:c.client_id,q:$('financeCategorySearch').value.trim()}));if(stamp!==S.categorySeq||key()!==k)return;S.options=d.rows||[];renderCategories();message('financeCategoryMsg');}catch(e){if(stamp===S.categorySeq){S.options=[];renderCategories();message('financeCategoryMsg',e.message,'error');}}
}
function openCategories(){requireContext();S.draft=new Set(S.selected);$('financeDialogClient').textContent=name('clientSelect');$('financeCategorySearch').value='';S.options=[];renderCategories();$('financeCategoryDialog').showModal();categorySearch();setTimeout(()=>$('financeCategorySearch').focus(),30);}
async function saveTariff(e){
 e.preventDefault();if(S.saving)return;const c=requireContext(),k=key(c),form=$('financeTariffForm'),fd=new FormData(form),service=String(fd.get('service_code')||'');
 if(service==='STORAGE_BOX_DAY')S.selected=new Set(['*']);
 if(!S.selected.size)throw new Error('Выберите категории');
 const raw=String(fd.get('rate')||'').trim(),rate=Number(raw);
 if(!raw||!Number.isFinite(rate)||rate<0)throw new Error('Укажите тариф не меньше нуля');
 const cats=[...S.selected];
 if(!confirm(`Клиент: ${name('clientSelect')}\nУслуга: ${labels[service]||service}\nКатегории: ${cats.includes('*')?'Общий тариф':cats.join(', ')}\nТариф: ${money(rate)}\n\nСохранить тариф? Сводка и история будут рассчитаны по действующим ставкам.`))return;
 S.saving=true;$('financeTariffFields').disabled=true;message('financeTariffMsg','Сохраняем тариф…');
 try{
   const d=await request('/finance/tariffs/bulk',{method:'POST',body:JSON.stringify({client_id:c.client_id,service_code:service,categories:cats,rate:raw,unit:String(fd.get('unit')||'').trim(),note:String(fd.get('note')||'').trim()})});
   if(key()!==k)return;
   message('financeTariffMsg',`Сохранено тарифов: ${d.saved}`,'ok');await tariffs();
   if($('page-finance').classList.contains('active'))await report('summary');
 }finally{S.saving=false;$('financeTariffFields').disabled=false;selectionLabel();}
}
async function refresh(){syncContext();if($('page-financehistory').classList.contains('active')){await report('history');return;}await Promise.all([tariffs(),report('summary')]);}
function caught(fn,id='financeMsg'){return (...args)=>Promise.resolve().then(()=>fn(...args)).catch(e=>message(id,e.message,'error'));}
function init(){
 if(!$('page-finance'))return;
 const before=new Date();before.setDate(before.getDate()-30);
 for(const id of ['financeDateFrom','financeHistoryFrom'])$(id).value=localDate(before);
 for(const id of ['financeDateTo','financeHistoryTo'])$(id).value=localDate();
 $('financeRefresh').onclick=caught(refresh);$('financeHistoryRefresh').onclick=caught(()=>report('history'),'financeHistoryMsg');
 $('financeChooseCategories').onclick=caught(openCategories,'financeTariffMsg');
 for(const id of ['financeCategoryClose','financeCategoryCancel'])$(id).onclick=()=>$('financeCategoryDialog').close();
 $('financeCategoryConfirm').onclick=()=>{S.selected=new Set(S.draft);selectionLabel();$('financeCategoryDialog').close();};
 $('financeCategoryDefault').onchange=e=>{if(e.target.checked)S.draft=new Set(['*']);else S.draft.delete('*');renderCategories();};
 $('financeCategorySelectAll').onclick=()=>{S.draft.delete('*');S.options.forEach(r=>S.draft.add(r.category));renderCategories();};
 $('financeCategoryClear').onclick=()=>{S.draft.clear();renderCategories();};
 $('financeCategoryList').onchange=e=>{const i=e.target.dataset.categoryIndex;if(i===undefined)return;const category=S.options[Number(i)]?.category;if(!category)return;S.draft.delete('*');e.target.checked?S.draft.add(category):S.draft.delete(category);renderCategories();};
 let timer;$('financeCategorySearch').oninput=()=>{clearTimeout(timer);timer=setTimeout(categorySearch,220);};
 $('financeTariffForm').onsubmit=e=>{e.preventDefault();caught(saveTariff,'financeTariffMsg')(e);};
 $('financeTariffService').onchange=()=>{if($('financeTariffService').value==='STORAGE_BOX_DAY'){S.selected=new Set(['*']);$('financeTariffUnit').value='коробо-день';}else if($('financeTariffUnit').value==='коробо-день')$('financeTariffUnit').value='шт';selectionLabel();};
 $('financeSyncCategories').onclick=caught(async()=>{
   const c=requireContext(),k=key(c);$('financeSyncCategories').disabled=true;message('financeTariffMsg','Обновляем категории из сохранённых карточек WB…');
   try{await request('/finance/categories/sync-wb?'+new URLSearchParams({client_id:c.client_id}),{method:'POST'});if(key()===k)message('financeTariffMsg','Категории обновлены. Откройте «Выбрать категории».','ok');}finally{$('financeSyncCategories').disabled=false;}
 },'financeTariffMsg');
 $('financeTariffSearch').oninput=()=>{S.tariffPage=0;renderTariffs();};
 $('financeTariffPrev').onclick=()=>{S.tariffPage--;renderTariffs();};$('financeTariffNext').onclick=()=>{S.tariffPage++;renderTariffs();};
 $('financeTariffs').onclick=e=>{const id=e.target.closest('[data-finance-edit]')?.dataset.financeEdit,r=S.tariffs.find(r=>r.id===id);if(!r)return;$('financeTariffService').value=r.service_code;S.selected=new Set([r.category]);$('financeTariffRate').value=r.rate;$('financeTariffUnit').value=r.unit;$('financeTariffForm').elements.note.value=r.note||'';selectionLabel();$('financeTariffForm').scrollIntoView({behavior:'smooth',block:'center'});};
 for(const id of ['financeHistoryService','financeHistoryCategory'])$(id).onchange=()=>{S.historyPage=0;renderHistory();};
 $('financeHistorySearch').oninput=()=>{S.historyPage=0;renderHistory();};
 $('financeHistoryPrev').onclick=()=>{S.historyPage--;renderHistory();};$('financeHistoryNext').onclick=()=>{S.historyPage++;renderHistory();};
 let navigationTimer,lastActive='';
 const check=()=>{clearTimeout(navigationTimer);navigationTimer=setTimeout(()=>{const page=['finance','financehistory'].find(p=>$('page-'+p)?.classList.contains('active'))||'';if(page&&page!==lastActive){lastActive=page;caught(refresh,page==='financehistory'?'financeHistoryMsg':'financeMsg')();}else if(!page)lastActive='';},0);};
 for(const id of ['page-finance','page-financehistory'])new MutationObserver(check).observe($(id),{attributes:true,attributeFilter:['class']});
 for(const id of ['clientSelect','warehouseSelect'])$(id)?.addEventListener('change',()=>{syncContext();if(lastActive)caught(refresh,lastActive==='financehistory'?'financeHistoryMsg':'financeMsg')();});
 syncContext();check();
 window.FCFinanceUI={refresh,openCategories};
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
