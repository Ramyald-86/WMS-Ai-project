/* r69: one quantity editor shared by stock tables, cells and product/box popups. */
(()=>{'use strict';
  const context=()=>({client_id:state.clientId||'',warehouse_id:state.warehouseId||''});
  const key=()=>JSON.stringify(context());
  const labels={ADD:'Добавить',REMOVE:'Списать',MOVE:'Переместить',REMOVE_ALL:'Удалить позицию целиком'};
  const name=k=>k==='MAIN'?'Основной склад':'WB FBS';
  let epoch=0;
  function close(){epoch++;const d=$('r69StockDialog');if(d?.open)d.close();d?.remove();}
  function modal(title){close();const d=document.createElement('dialog');d.id='r69StockDialog';d.className='r69-dialog';d.innerHTML=`<header><h2>${esc(title)}</h2><button type="button" class="secondary" data-close>Закрыть</button></header><div data-body></div><p role="alert" class="bad" data-error></p>`;document.body.appendChild(d);d.querySelector('[data-close]').onclick=close;d.addEventListener('cancel',()=>epoch++);d.showModal();return d;}
  function button(action,options,label){return `<button type="button" class="mini ${action==='REMOVE_ALL'?'danger':'secondary'}" data-r69-stock="${esc(JSON.stringify({...options,action}))}">${esc(label||labels[action])}</button>`;}
  function buttons(options,whole=false){return `<div class="r69-actions">${(whole?['REMOVE_ALL']:['ADD','REMOVE','MOVE']).map(a=>button(a,options)).join('')}</div>`;}
  function accountingButton(){return '';}
  function places(r){return r?.locations||[];}
  const qtext=p=>[p.location,p.container?'короб '+p.container:'без короба'].filter(Boolean).join(' · ');
  async function open(options){
    if(!options.stock_class){const d=modal('Выберите склад'),b=d.querySelector('[data-body]');b.innerHTML='<p>В каком разделе изменить товар?</p><div class="r69-actions"><button type="button" class="primary" data-kind="MAIN">Основной склад</button><button type="button" class="secondary" data-kind="FBS">WB FBS</button></div>';b.querySelectorAll('[data-kind]').forEach(x=>x.onclick=()=>open({...options,stock_class:x.dataset.kind}));return;}
    const kind=options.stock_class||'FBS',ctx=context(),captured=key(),d=modal(labels[options.action]||'Изменить товар'),ticket=epoch,body=d.querySelector('[data-body]'),err=d.querySelector('[data-error]');
    const valid=()=>ticket===epoch&&key()===captured;
    body.textContent='Проверяем остатки…';
    try{
      const fresh=await api('/warehouse-ui/stocks?'+new URLSearchParams({...ctx,stock_class:kind}),{timeoutMs:30000});if(!valid())return;
      const item=fresh.rows.find(r=>options.barcode?r.barcode===options.barcode:(r.sku_ids||[]).some(id=>(options.sku_ids||[]).includes(id)));
      const ids=item?.sku_ids||options.sku_ids||[];
      if(!ids.length)throw Error('Товар не найден. Обновите остатки или откройте приёмку.');
      const source=places(item).filter(p=>(!options.location_id||p.location_id===options.location_id)&&(!options.location_id||String(p.container_id||'')===String(options.container_id||'')));
      const action=options.action||'REMOVE';
      // MAIN opens without downloading every address/box. Fetch these only
      // when an editor needs destinations, isolated to its captured context.
      let locations=state.locations||[],containers=state.containers||[];
      if(action==='MOVE'||action==='ADD'){
        [locations,containers]=await Promise.all([
          api('/masterdata/locations?'+new URLSearchParams({warehouse_id:ctx.warehouse_id}),{timeoutMs:10000}),
          api('/masterdata/containers?'+new URLSearchParams(ctx),{timeoutMs:10000})
        ]);
        if(!valid())return;
      }
      const destinationLocations=locations.filter(l=>l.is_active!==false&&String(l.warehouse_id||ctx.warehouse_id)===ctx.warehouse_id);
      const boxes=containers.filter(b=>b.status==='ACTIVE'&&String(b.client_id||ctx.client_id)===ctx.client_id&&String(b.warehouse_id||ctx.warehouse_id)===ctx.warehouse_id);
      if(action==='ADD'&&!source.length&&options.location_id){const loc=destinationLocations.find(l=>l.id===options.location_id),box=boxes.find(b=>b.id===options.container_id);if(loc)source.push({sku_id:ids[0],location_id:loc.id,container_id:box?.id||null,location:loc.code,container:box?.name||box?.barcode||'',quantity:0});}
      if(action==='ADD'&&!source.length&&!options.location_id)for(const loc of destinationLocations)source.push({sku_id:ids[0],location_id:loc.id,container_id:null,location:loc.code,container:'',quantity:0});
      body.innerHTML=`<p><b>${esc(item?.article||options.article||options.barcode||'Товар')} · ${esc(item?.size||'')}</b></p><p>${esc(name(kind))}</p><form data-editor><div class="r69-form">${action==='REMOVE_ALL'?'<p class="wide">Будут списаны все единицы этой позиции во всех её местах в выбранном складе. Карточка товара и история сохранятся.</p>':`<label class="wide">${action==='ADD'?'Куда добавить':'Откуда'}<select name="source" required>${source.map((p,i)=>`<option value="${i}">${esc(qtext(p))} · ${fmtQty(p.quantity)} шт.</option>`).join('')}</select></label><label>Количество<input name="quantity" type="number" min="1" max="1000000" step="1" value="1" required inputmode="numeric"></label>`}
      ${action==='MOVE'?`<label class="wide">Куда: ячейка или палета<input name="destination" list="r69Destinations" placeholder="Введите или отсканируйте адрес" required autocomplete="off"><datalist id="r69Destinations">${destinationLocations.map(l=>`<option value="${esc(l.code)}"></option>`).join('')}</datalist></label><label class="wide">Короб назначения<select name="box"><option value="">Без короба</option></select></label>`:''}
      <label class="wide">Причина<input name="reason" maxlength="500" required value="${esc(action==='REMOVE_ALL'?'Удаление позиции из остатка':action==='MOVE'?'Перемещение товара':action==='ADD'?'Ручное добавление':'Ручное списание')}"></label></div><div class="r69-actions"><button class="primary" type="submit">${esc(labels[action])}</button>${kind==='FBS'?accountingButton(ids):''}</div></form><div data-preview></div>`;
      const form=body.querySelector('form'),target=body.querySelector('[data-preview]');let formVersion=0,requestId=uid(),submittedPayload=null;
      function destination(){const value=String(form.elements?.destination?.value||form.querySelector('[name=destination]')?.value||'').trim();return destinationLocations.find(l=>[l.code,l.barcode,l.id].includes(value));}
      const dest=form.querySelector('[name=destination]');if(dest)dest.oninput=()=>{const loc=destination();form.querySelector('[name=box]').innerHTML='<option value="">Без короба</option>'+boxes.filter(b=>b.location_id===loc?.id).map(b=>`<option value="${esc(b.id)}">${esc(b.name||b.barcode)}</option>`).join('');target.innerHTML='';};
      form.addEventListener('input',()=>{formVersion++;target.innerHTML='';});form.addEventListener('change',()=>{formVersion++;target.innerHTML='';});
      form.onsubmit=async e=>{e.preventDefault();err.textContent='';const submit=form.querySelector('[type=submit]'),originalText=submit.textContent;submit.disabled=true;submit.textContent=action==='REMOVE_ALL'?'Удаляем…':action==='REMOVE'?'Списываем…':action==='MOVE'?'Перемещаем…':'Сохраняем…';target.innerHTML='<p class="muted">Операция выполняется. Локальный остаток изменится сразу, обновление WB пойдёт в фоне.</p>';try{
        if(!valid())throw Error('Выбран другой клиент или склад. Откройте действие заново');
        const revision=formVersion,values=new FormData(form),p=source[Number(values.get('source')||0)],qty=Number(values.get('quantity')||0);
        if(action!=='REMOVE_ALL'&&(!p||!Number.isInteger(qty)||qty<1))throw Error('Выберите место и целое количество от 1');
        const loc=action==='MOVE'?destination():null;if(action==='MOVE'&&!loc)throw Error('Адрес назначения не найден');
        const payload={...ctx,action,stock_class:kind,sku_ids:action==='REMOVE_ALL'?ids:[p.sku_id],location_id:action==='REMOVE_ALL'?null:p.location_id,container_id:action==='REMOVE_ALL'?null:p.container_id||null,quantity:action==='REMOVE_ALL'?null:qty,reason:String(values.get('reason')||'').trim(),destination_location_id:loc?.id||null,destination_container_id:values.get('box')||null,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null};
        const content=JSON.stringify(payload);
        if(submittedPayload!==null&&submittedPayload!==content)requestId=uid();
        submittedPayload=content;
        form.querySelectorAll('input,select,button').forEach(x=>x.disabled=true);
        const result=await api('/warehouse-ui/actions/apply',{method:'POST',body:JSON.stringify({...payload,request_id:requestId}),timeoutMs:45000});
        if(!valid())return;
        target.innerHTML='<p class="good"><b>Готово.</b> Локальный остаток изменён; WB обновляется в фоне.</p>';
        setTimeout(()=>{if(valid()){close();toast(result.wb||'Остаток изменён. Операция сохранена в истории.');}},180);
      }catch(error){const msg=String(error?.message||error);err.textContent=msg.includes('Есть резерв или незавершённое списание')?'Удаление остановлено только из-за реально активного заказа/списания. Если заказ уже закрыт, обновите страницу — устаревший резерв теперь очищается автоматически.':msg;target.innerHTML='';}finally{form.querySelectorAll('input,select,button').forEach(x=>x.disabled=false);submit.textContent=originalText;}};
    }catch(error){body.innerHTML='';err.textContent=error.message;}
  }
  const bulkPending=new Map();
  async function bulkOpen(kind,codes=null,seedProducts=null){
    const ctx=context(),captured=key(),pendingKey='fc:r77:bulk-remove:'+captured+':'+kind;
    const d=modal('Удалить остатки · '+name(kind)),body=d.querySelector('[data-body]'),err=d.querySelector('[data-error]'),ticket=epoch;
    const valid=()=>ticket===epoch&&key()===captured;
    const remember=req=>{if(req)bulkPending.set(pendingKey,req);else bulkPending.delete(pendingKey);try{req?localStorage.setItem(pendingKey,JSON.stringify(req)):localStorage.removeItem(pendingKey);}catch{}};
    let saved=bulkPending.get(pendingKey);try{saved=saved||JSON.parse(localStorage.getItem(pendingKey)||'null');}catch{}
    let submitting=false;
    async function send(req){if(submitting)return;submitting=true;err.textContent='';body.querySelectorAll('button,input').forEach(x=>x.disabled=true);remember(req);
      try{const result=await api('/warehouse-ui/actions/bulk-remove',{method:'POST',body:JSON.stringify(req),timeoutMs:90000});remember(null);if(!valid())return;close();window.FCStockRooms?.invalidate();toast(`Удалено ${fmtQty(result.quantity)} шт. Причина сохранена в истории.`);}
      catch(e){if(valid()){err.textContent=e.message;if([400,403,404,409,422].includes(Number(e.status))){remember(null);body.innerHTML='<p>Удаление не выполнено. Откройте окно заново для актуальных остатков.</p>';}else{body.innerHTML='<p>Ответ не получен. Проверьте результат тем же запросом, чтобы не удалить дважды.</p><button type="button" data-replay>Проверить результат удаления</button>';body.querySelector('[data-replay]').onclick=()=>send(req);}}}
      finally{submitting=false;body.querySelectorAll('button,input').forEach(x=>x.disabled=false);}
    }
    if(saved){body.innerHTML='<p>Есть незавершённый запрос удаления. Сначала проверьте его результат.</p><button type="button" data-replay>Проверить результат удаления</button>';body.querySelector('button').onclick=()=>send(saved);return;}
    body.textContent=kind==='MAIN'&&Array.isArray(seedProducts)?'Готовим выбранные остатки…':'Проверяем свободные остатки и резервы…';
    try{let optionRows;
      if(kind==='MAIN'&&Array.isArray(seedProducts)){
        // r86 MAIN fast path: the visible stock snapshot already contains exact free quantity
        // per bucket. Do not re-download the whole warehouse just to open the delete dialog.
        optionRows=seedProducts.flatMap(product=>(Array.isArray(product.locations)?product.locations:[]).map(p=>({
          sku_id:p.sku_id,location_id:p.location_id,container_id:p.container_id||null,location:p.location||'',container:p.container||'',
          article:product.article||'',size:product.size||'',barcode:product.barcode||'',free:Math.max(0,Math.floor(Number(p.free||0)))
        }))).filter(r=>r.free>0);
      }else{
        const response=await api('/warehouse-ui/actions/bulk-options?'+new URLSearchParams({...ctx,stock_class:kind}),{timeoutMs:60000});if(!valid())return;
        optionRows=response.rows.filter(r=>!codes||codes.includes(r.barcode));
      }
      const rows=optionRows;
      if(!rows.length)throw Error('У выбранных товаров нет свободного остатка для удаления');
      body.innerHTML=`<p>Это физическое списание со склада. ${kind==='MAIN'?'Товар в активной сборке защищён и сюда не включён.':'Зарезервированный товар сохраняется.'} Выберите места и количество; «Все свободные» отмечает весь показанный свободный остаток.</p><div class="r69-actions"><button type="button" data-all>Все свободные</button><button type="button" data-none>Снять выбор</button></div><form><div style="max-height:50vh;overflow:auto"><table><thead><tr><th>Выбор</th><th>Товар / место</th><th>Свободно</th><th>Удалить</th></tr></thead><tbody>${rows.map((r,i)=>`<tr><td><input type="checkbox" data-pick="${i}" ${r.free<1?'disabled':''}></td><td>${esc(r.article)} · ${esc(r.size)}<small>ШК ${esc(r.barcode)} · ${esc(qtext(r))}</small></td><td>${fmtQty(r.free)}</td><td><input aria-label="Количество для удаления" data-qty="${i}" type="number" min="1" max="${r.free}" step="1" value="${Math.max(1,r.free)}" ${r.free<1?'disabled':''}></td></tr>`).join('')}</tbody></table></div><label>Причина<input name="reason" required maxlength="500" value="Ручное массовое удаление остатка"></label><p data-total></p><button class="danger" type="submit">Удалить выбранное количество</button></form>`;
      const selected=()=>[...body.querySelectorAll('[data-pick]')].filter(x=>x.checked);
      const total=()=>body.querySelector('[data-total]').textContent='Выбрано мест: '+selected().length+' · '+selected().reduce((n,x)=>n+Number(body.querySelector('[data-qty="'+x.dataset.pick+'"]').value||0),0)+' шт.';
      body.querySelector('[data-all]').onclick=()=>{body.querySelectorAll('[data-pick]').forEach(x=>x.checked=!x.disabled);total();};body.querySelector('[data-none]').onclick=()=>{body.querySelectorAll('[data-pick]').forEach(x=>x.checked=false);total();};body.addEventListener('input',total);body.addEventListener('change',total);total();
      body.querySelector('form').onsubmit=e=>{e.preventDefault();err.textContent='';try{const items=selected().map(x=>{const r=rows[Number(x.dataset.pick)],quantity=Number(body.querySelector('[data-qty="'+x.dataset.pick+'"]').value);if(!Number.isInteger(quantity)||quantity<1||quantity>r.free)throw Error('Проверьте количество для '+r.barcode);return {sku_id:r.sku_id,location_id:r.location_id,container_id:r.container_id||null,quantity};});if(!items.length)throw Error('Выберите товар');if(!valid())throw Error('Выбран другой склад');const reason=body.querySelector('[name=reason]').value.trim();if(!reason)throw Error('Укажите причину');send({...ctx,stock_class:kind,items,reason,request_id:uid()});}catch(e){err.textContent=e.message;}};
    }catch(e){if(valid()){body.textContent='';err.textContent=e.message;}}
  }

  document.addEventListener('click',e=>{const b=e.target.closest('[data-r69-stock]');if(b){e.preventDefault();try{open(JSON.parse(b.dataset.r69Stock)).catch(x=>toast(x.message,true));}catch(x){toast(x.message,true);}}});
  document.addEventListener('change',e=>{if(['clientSelect','warehouseSelect'].includes(e.target?.id))close();},true);
  window.FCStockActions={bulkOpen,open,buttons,button,accountingButton};
  const link=document.createElement('link');link.rel='stylesheet';link.href='/static/receiving-native.css?v=r69';document.head.appendChild(link);
})();
