/* FC_R145F50_RECEIVING_STABLE_WORKING
 * Small controller loaded after the existing receiving UI.
 * It does not replace the whole receiving renderer. It only:
 *  - suppresses redundant identical re-renders (no flicker),
 *  - persists quantity/remove exactly after draining the scan queue,
 *  - drains the queue before final commit.
 */
(()=>{
  if(globalThis.__FC_R145F50_CONTROLLER)return;
  globalThis.__FC_R145F50_CONTROLLER=true;
  const q=s=>document.querySelector(s);
  const s=v=>String(v??'').trim();
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  const uid=()=>{try{return crypto.randomUUID();}catch(_){return `${Date.now()}-${Math.random().toString(36).slice(2)}`;}};
  const msg=e=>{if(!e)return'Неизвестная ошибка';if(typeof e==='string')return e;if(e.message)return String(e.message);if(e.detail)return msg(e.detail);try{return JSON.stringify(e);}catch(_){return String(e);}};
  const show=(title,text,bad=false)=>{const el=q('#receiveResult');if(el)el.innerHTML=`<b${bad?' class="bad"':''}>${title}</b><span>${String(text||'')}</span>`;};
  const worker=()=>s(q('#receiveBy')?.value||q('#receiveWorker')?.value||'');
  async function post(url,body,timeout=12000){const c=new AbortController(),t=setTimeout(()=>c.abort(),timeout);try{const r=await fetch(url,{method:'POST',credentials:'same-origin',cache:'no-store',signal:c.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}),raw=await r.text();let d;try{d=JSON.parse(raw);}catch(_){d={detail:raw};}if(!r.ok)throw Error(msg(d));return d;}finally{clearTimeout(t);}}
  async function current(){return await globalThis.FC32Receiving?.current?.()||null;}
  async function drain(){if(!globalThis.FC32Receiving?.flushAll)throw Error('Быстрая синхронизация r145f50 не активна');return await globalThis.FC32Receiving.flushAll(30000);}
  async function serverSnapshot(){if(globalThis.FC32Receiving?.snapshot)return await globalThis.FC32Receiving.snapshot();return await current();}
  async function adopt(snap){if(snap&&globalThis.FC32Receiving?.adopt)return await globalThis.FC32Receiving.adopt(snap,true);return snap;}
  function findRow(snap,id){const rows=snap?.rows||[];return rows.find(r=>String(r.id||'')===String(id||''))||null;}
  function bodyBase(snap){if(!snap?.client_id||!snap?.warehouse_id)throw Error('Не удалось определить клиента или склад текущей приёмки');return {client_id:snap.client_id,warehouse_id:snap.warehouse_id,worker:worker()||snap.worker||'admin',reference:q('#receiveReference')?.value?.trim()||null,preferred_receipt_id:snap.id||null};}
  async function setQuantity(input,value){
    globalThis.__FC50_EDIT_BUSY=true;input.disabled=true;
    try{
      let local=await current(),row=findRow(local,input.dataset.manualDraftQty);
      show('Сохраняем','Отправляем ожидающие сканы и изменение количества…');
      await drain();
      let snap=await serverSnapshot();
      if(!snap?.id)throw Error('Сервер не вернул текущую приёмку');
      if(!row)row=findRow(snap,input.dataset.manualDraftQty);
      const payload={...bodyBase(snap),request_id:`fc50-edit-${uid()}`,line_id:input.dataset.manualDraftQty||row?.id||null,barcode:row?.barcode||null,location_code:row?.location_code||null,container_code:row?.container_code||row?.container_name||null,quantity:value};
      const data=await post(`/r145f50/manual-drafts/${encodeURIComponent(snap.id)}/line-set`,payload,12000);
      await adopt(data.snapshot);show('Сохранено на сервере',value===0?'Позиция убрана из текущей приёмки.':`Количество сохранено: ${value}`);
    }finally{globalThis.__FC50_EDIT_BUSY=false;input.disabled=false;}
  }
  function installRenderGuard(){
    try{
      if(typeof globalThis.renderManualReceivingDraft!=='function'||globalThis.renderManualReceivingDraft.__fc50)return;
      const original=globalThis.renderManualReceivingDraft;let last='';
      const fingerprint=()=>{try{const d=typeof globalThis.loadManualReceivingDraft==='function'?globalThis.loadManualReceivingDraft():null;const rows=(d?.rows||[]).map(r=>[s(r.id),s(r.barcode),Number(r.quantity||0),s(r.container_code||r.container_name),s(r.location_code)]);return JSON.stringify([Boolean(d?.posting),rows]);}catch(_){return String(Date.now());}};
      const wrapped=function(){
        if(globalThis.__FC50_EDIT_BUSY)return;
        const active=document.activeElement,panel=q('#manualReceivingDraftPanel');if(active&&panel?.contains(active)&&active.matches?.('input,textarea,select'))return;
        const fp=fingerprint();if(fp===last)return;last=fp;return original.apply(this,arguments);
      };
      wrapped.__fc50=true;wrapped.__fc50Original=original;globalThis.renderManualReceivingDraft=wrapped;
    }catch(_){}
  }
  document.addEventListener('change',ev=>{
    const input=ev.target?.closest?.('[data-manual-draft-qty]');if(!input)return;
    ev.preventDefault();ev.stopImmediatePropagation();
    const value=Number(input.value);if(!Number.isInteger(value)||value<0){show('Не сохранено','Количество должно быть целым числом 0 или больше.',true);return;}
    setQuantity(input,value).catch(e=>show('Не сохранено',msg(e),true));
  },true);
  document.addEventListener('click',ev=>{
    const remove=ev.target?.closest?.('[data-manual-draft-remove]');
    if(remove){ev.preventDefault();ev.stopImmediatePropagation();const fake={dataset:{manualDraftQty:remove.dataset.manualDraftRemove},disabled:false};setQuantity(fake,0).catch(e=>show('Не сохранено',msg(e),true));return;}
    const commit=ev.target?.closest?.('#commitManualReceivingDraft');
    if(commit&&!globalThis.__FC50_COMMIT_BYPASS){
      ev.preventDefault();ev.stopImmediatePropagation();commit.disabled=true;
      (async()=>{try{show('Завершаем приёмку','Сначала сохраняем все ожидающие сканы…');await drain();const snap=await serverSnapshot();if(snap)await adopt(snap);show('Все сканы сохранены','Запускаем проведение приёмки.');globalThis.__FC50_COMMIT_BYPASS=true;commit.disabled=false;commit.click();await sleep(0);}catch(e){commit.disabled=false;show('Приёмка не завершена',msg(e),true);}finally{globalThis.__FC50_COMMIT_BYPASS=false;}})();
    }
  },true);
  const boot=()=>{installRenderGuard();let n=0;const t=setInterval(()=>{installRenderGuard();if(++n>40)clearInterval(t);},250);};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
