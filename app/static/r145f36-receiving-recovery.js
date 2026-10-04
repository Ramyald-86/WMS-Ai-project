/* FC_R145F36_STUCK_RECEIPT_RECOVERY */
(() => {
  let busy = false;
  const q = (s, root=document) => root.querySelector(s);
  const esc36 = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function detailMessage(x){
    if(!x) return 'Неизвестная ошибка';
    if(typeof x === 'string') return x;
    if(typeof x.message === 'string') return x.message;
    if(x.detail) return detailMessage(x.detail);
    try{return JSON.stringify(x);}catch(_){return String(x);}
  }
  function statusBox(text, bad=false){
    const panel=q('#manualReceivingDraftPanel'); if(!panel) return;
    let box=q('#fc36RecoveryStatus', panel);
    if(!box){box=document.createElement('div');box.id='fc36RecoveryStatus';box.className='result-box';const actions=q('.manual-draft-actions',panel);(actions||panel).insertAdjacentElement(actions?'beforebegin':'afterbegin',box);}
    box.innerHTML=`<b class="${bad?'bad':'good'}">${bad?'Не завершено':'Сверка приёмки'}</b><span>${esc36(text)}</span>`;
  }
  function retitle(){
    const b=q('#commitManualReceivingDraft');
    if(!b || busy) return;
    if(b.textContent!=='Завершить и сверить') b.textContent='Завершить и сверить';
    if(b.dataset.fc36!=='1') b.dataset.fc36='1';
    const panel=q('#manualReceivingDraftPanel');
    const hasRows=!!q('[data-manual-draft-qty]',panel||document);
    if(b.disabled===hasRows) b.disabled=!hasRows;
  }
  async function recover(){
    if(busy) return;
    if(typeof state==='undefined' || !state.clientId || !state.warehouseId){statusBox('Выберите клиента и склад.',true);return;}
    let snap;
    try{
      statusBox('Получаем актуальный серверный черновик…');
      snap = await fc27EnsureServerDraft();
    }catch(e){statusBox(detailMessage(e),true);return;}
    const rows=(snap?.rows||[]).filter(r=>Number(r.quantity||0)>0 && !r.posted_at);
    const units=rows.reduce((n,r)=>n+Number(r.quantity||0),0);
    const positions=rows.length;
    if(!units){statusBox('В серверном черновике нет непроведённого товара.',true);return;}
    const worker=(typeof fc30WorkerName==='function'?fc30WorkerName():String(q('#receiveBy')?.value||snap.worker||'').trim()) || String(snap.worker||'').trim();
    if(!worker){statusBox('Укажите имя приёмщика.',true);return;}
    if(!confirm(`Завершить и сверить серверную приёмку ${worker}: ${units} шт. / ${positions} поз.?\n\nУже проведённая часть будет учтена. Повторно она не начисляется. Недостающие единицы будут добавлены только после точной сверки.`)) return;
    busy=true;const btn=q('#commitManualReceivingDraft');if(btn){btn.disabled=true;btn.textContent='Сверяем сервер…';}
    statusBox(`Сверяем ${units} шт. с историей и остатками…`);
    try{
      const payload={client_id:state.clientId,warehouse_id:state.warehouseId,worker,reference:q('#receiveReference')?.value?.trim()||null,performed_by:q('#receiveBy')?.value?.trim()||worker,stock_class:q('#receiveStockClass')?.value||'MAIN'};
      const r=await fetch(`/r145f36/manual-drafts/${encodeURIComponent(snap.id)}/recover-complete`,{method:'POST',credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      const raw=await r.text();let data;try{data=JSON.parse(raw);}catch(_){data={detail:raw};}
      if(!r.ok) throw new Error(detailMessage(data));
      statusBox(`Готово: черновик ${data.expected_units} шт. = история/остатки ${data.posted_units} шт.${data.repaired_units?` Восстановлено ${data.repaired_units} шт.`:''} Старая приёмка закрыта.`);
      try{if(q('#receiveReference'))q('#receiveReference').value='';}catch(_){ }
      try{if(typeof clearManualReceivingStorageContext==='function')clearManualReceivingStorageContext({keepHierarchy:true});}catch(_){ }
      try{if(data.next_receipt && typeof fc27CacheServerDraft==='function')await fc27CacheServerDraft(data.next_receipt);}catch(_){ }
      try{if(typeof renderManualReceivingDraft==='function')renderManualReceivingDraft();}catch(_){ }
      try{if(typeof loadContextData==='function'){state.inventoryCatalogLoaded=false;loadContextData().catch(()=>{});}}catch(_){ }
      try{window.loadR62ManualReceipts?.().catch(()=>{});}catch(_){ }
      try{if(typeof toast==='function')toast(`Приёмка закрыта: ${data.posted_units} шт.${data.repaired_units?` Восстановлено ${data.repaired_units} шт.`:''}`);}catch(_){ }
      setTimeout(()=>{try{renderManualReceivingDraft();retitle();}catch(_){location.reload();}},350);
    }catch(e){
      statusBox(detailMessage(e),true);
      try{if(typeof toast==='function')toast(detailMessage(e),true);}catch(_){ }
    }finally{
      busy=false;retitle();
    }
  }
  document.addEventListener('click', (e) => {
    const b=e.target?.closest?.('#commitManualReceivingDraft');
    if(!b) return;
    e.preventDefault();e.stopPropagation();e.stopImmediatePropagation();
    recover();
  }, true);
  new MutationObserver(retitle).observe(document.documentElement,{childList:true,subtree:true});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',retitle,{once:true});else retitle();
})();
