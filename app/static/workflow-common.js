/* FC_K9R64_WORKFLOW_SHARED */
window.FCWorkflow=(()=>{
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const q=new URLSearchParams(location.search),scope={client_id:q.get('client_id')||'',warehouse_id:q.get('warehouse_id')||''};
  const scoped=path=>path+(path.includes('?')?'&':'?')+new URLSearchParams(scope);
  const valid=()=>Object.values(scope).every(x=>/^[0-9a-f-]{36}$/i.test(x));
  async function api(path,options={}){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),90000);
    try{
      const response=await fetch(path,{credentials:'same-origin',...options,signal:controller.signal});
      if(response.status===401||response.redirected&&response.url.includes('/login'))throw Error('Сессия WMS закончилась. Откройте WMS и войдите снова; черновик сохранён.');
      const data=await response.json().catch(()=>({detail:'Сервер вернул непонятный ответ'}));
      if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail||data));
      if(!['GET','HEAD'].includes((options.method||'GET').toUpperCase())){const message={type:'fc-warehouse-changed',...scope,at:Date.now()};if(parent!==window)parent.postMessage(message,location.origin);try{localStorage.setItem('fc:r65:changed',JSON.stringify(message));}catch{}}return data;
    }catch(e){if(e.name==='AbortError')throw Error('Нет ответа от WMS. Не начинайте новую приёмку: повторите проверку этой же операции, чтобы узнать результат.');throw e;}finally{clearTimeout(timer);}
  }
  const post=(path,data)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  function go(page,extra={}){
    const msg={type:'fc-workflow-go',...scope,page,...extra};
    if(parent!==window)parent.postMessage(msg,location.origin);
    else location.href='/?'+new URLSearchParams({workflow_page:page,...scope,...extra});
  }
  function table(rows){return `<div class="wf-table"><table><thead><tr><th>Откуда / куда</th><th>Товар</th><th>ШК</th><th>Количество</th></tr></thead><tbody>${rows.map(r=>`<tr><td><b>${esc(r.location_code)}</b><small>${esc(r.container_name||'Без короба')}${r.row_on_pallet||r.route_row?' · ряд '+esc(r.row_on_pallet||r.route_row):''}</small></td><td>${esc(r.article)}<small>${esc(r.size)}</small></td><td>${esc(r.barcode)}</td><td><b>${esc(r.qty)} шт.</b>${r.excel_row?'<small>Строка Excel '+esc(r.excel_row)+'</small>':''}</td></tr>`).join('')}</tbody></table></div>`;}
  let dialog,plan,currentOptions,returnFocus;
  function ensureDialog(){if(dialog)return;dialog=document.createElement('dialog');dialog.className='wf-dialog';dialog.setAttribute('aria-labelledby','wfPlanTitle');document.body.appendChild(dialog);dialog.addEventListener('close',()=>returnFocus?.focus());}
  function showPlan(p,options={}){
    ensureDialog();plan=p;currentOptions=options;returnFocus=document.activeElement;
    const isReceipt=p.kind==='RECEIPT',done=p.status==='CONFIRMED',cancelled=p.status==='CANCELLED',short=p.shortages||[];
    dialog.innerHTML=`<div class="wf-dialog-body"><h2 id="wfPlanTitle">${cancelled?'Приёмка отменена':done?'Операция выполнена':isReceipt?'Проверка приёмки':'Проверьте перевод в FBS'}</h2>
      <p>${isReceipt?(cancelled?'Отмена отражена в складской истории.':done?'Товар принят на <b>Основной склад</b> по адресам ниже.':'Товар будет принят на <b>Основной склад</b> по адресам ниже.'): 'Товар останется на тех же адресах. Его учёт изменится: <b>Основной склад → FBS</b>.'}</p>
      <div class="wf-note ${short.length?'bad':done?'ok':''}">${cancelled?'Эта приёмка отменена. Повторное проведение того же номера недоступно.':done?'✓ Подтверждено в WMS. Повторное нажатие не добавит товар ещё раз.':isReceipt?`Всего ${esc(p.units)} шт. · коробов ${esc(p.boxes)} · новых товаров ${esc(p.new_products)}`:`Запрошено ${esc(p.requested)} шт. · можно перевести ${esc(p.allocated)} шт.`}</div>
      ${p.new_addresses?.length?`<p>При подтверждении будут созданы адреса: <b>${p.new_addresses.map(x=>esc(x.code)).join(', ')}</b>.</p>`:''}
      ${short.length?`<div class="wf-note bad"><b>Не хватает товара. Перевод пока недоступен.</b>${short.map(r=>`<p>Строка ${esc(r.row)} · ШК ${esc(r.barcode)}: не хватает ${esc(r.missing)} шт.${r.location?' · '+esc(r.location):''}${r.container?' · '+esc(r.container):''}</p>`).join('')}Проверьте адреса, дождитесь освобождения сборки или исправьте Excel и загрузите снова.</div>`:''}
      ${table(p.rows||[])}
      ${!done&&p.occupied_boxes?.length?`<label class="wf-note"><span><input id="wfOccupied" type="checkbox"> Я проверил: добавляем в непустые короба</span><small>${p.occupied_boxes.map(b=>esc(b.box)+' — уже '+esc(b.quantity)+' шт.').join('; ')}</small></label>`:''}
      <p class="wf-help">Публикация остатков на Wildberries выполняется отдельно в WMS. Здесь подтверждается складской учёт.</p>
      <div id="wfPlanError" role="alert" class="wf-note bad hidden"></div></div>
      <div class="wf-dialog-foot"><button class="secondary" id="wfPlanClose">${done?'Закрыть':'Вернуться и исправить'}</button><div class="wf-row"><a class="wf-link secondary" href="${esc(scoped('/workflow/plans/'+p.plan_id+'/report.xlsx'))}">Скачать план Excel</a>${done&&isReceipt?'<button id="wfCreatePick">Создать сборку</button>':!done&&!cancelled?`<button id="wfPlanConfirm" ${short.length?'disabled':''}>${isReceipt?'Подтвердить приёмку':'Подтвердить перевод · '+esc(p.allocated)+' шт.'}</button>`:''}</div></div>`;
    dialog.querySelector('#wfPlanClose').onclick=()=>dialog.close();
    const btn=dialog.querySelector('#wfPlanConfirm');
    if(btn)btn.onclick=async()=>{
      btn.disabled=true;const close=dialog.querySelector('#wfPlanClose');close.disabled=true;
      const err=dialog.querySelector('#wfPlanError');err.classList.add('hidden');
      try{
        await options.beforeConfirm?.();
        const result=await post('/workflow/plans/'+p.plan_id+'/confirm',{...scope,fingerprint:p.fingerprint,confirm_occupied:!!dialog.querySelector('#wfOccupied')?.checked});
        await options.onConfirmed?.(result);showPlan(result,options);
      }catch(e){err.textContent=e.message;err.classList.remove('hidden');btn.disabled=false;close.disabled=false;}
      finally{await options.onFinished?.();}
    };
    const pick=dialog.querySelector('#wfCreatePick');if(pick)pick.onclick=async()=>{
      const number=prompt('Номер сборочного задания:', 'СБ-'+(p.package_id||p.plan_id).slice(-12));if(!number?.trim())return;
      pick.disabled=true;
      try{const res=await post('/workflow/plans/'+p.plan_id+'/pick',{...scope,task_number:number.trim()});dialog.close();go('warehousework',{task_id:res.task_id});}
      catch(e){const err=dialog.querySelector('#wfPlanError');err.textContent=e.message;err.classList.remove('hidden');pick.disabled=false;}
    };
    if(!dialog.open)dialog.showModal();
  }
  return {esc,scope,scoped,valid,api,post,go,table,showPlan};
})();
