/* FC_R145F8_FBO_FOUNDATION_NO_STOCK_EFFECTS */
(()=>{
  const S={batches:[],batchId:'',detail:null,label:{BARCODE:null,CHZ:null},busy:false};
  const R=id=>document.getElementById(id);
  const actor=()=>String(localStorage.getItem('fc_fbo_worker')||'').trim()||'Администратор';
  function station(){try{return (typeof wbPrintSettings==='function'?wbPrintSettings().station:'PACK-01')||'PACK-01';}catch(_){return 'PACK-01';}}
  function setMsg(v,err=false){const e=R('fboMsg');if(!e)return;e.textContent=v||'';e.className='msg'+(err?' error':v?' ok':'');}
  function ctx(){if(!state.clientId||!state.warehouseId)throw new Error('Выберите клиента и склад');return `client_id=${encodeURIComponent(state.clientId)}&warehouse_id=${encodeURIComponent(state.warehouseId)}`;}
  function fmtDate(v){return v?new Date(v).toLocaleString('ru-RU'):'';}
  async function loadBatches(selectId=''){
    const d=await api('/fbo/batches?'+ctx());S.batches=d.rows||[];
    const host=R('fboBatchRows');if(host)host.innerHTML=S.batches.map(x=>`<button type="button" class="fbo-batch-row ${S.batchId===x.id?'active':''}" data-fbo-batch="${esc(x.id)}"><span><b>${esc(x.name)}</b><small>${esc(x.source||'')} · ${fmtDate(x.created_at)}</small></span><span><b>${fmtQty(x.units)} ед.</b><small>${fmtQty(x.boxes)} кор.</small></span></button>`).join('')||'<div class="empty-state">FBO-партий пока нет.</div>';
    const wanted=selectId||S.batchId||(S.batches[0]?.id||'');if(wanted){S.batchId=wanted;await loadDetail();}
  }
  async function createBatch(){
    const name=R('fboBatchName').value.trim();if(!name)throw new Error('Введите название FBO-партии');
    const source=R('fboBatchSource').value;const comment=R('fboBatchComment').value.trim();
    localStorage.setItem('fc_fbo_worker',R('fboWorker').value.trim());
    const row=await api('/fbo/batches',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,name,source,comment,performed_by:actor()})});
    R('fboBatchName').value='';R('fboBatchComment').value='';S.batchId=row.id;setMsg(`FBO-партия «${row.name}» создана. Остатки склада не изменены.`);await loadBatches(row.id);
  }
  async function loadDetail(){
    if(!S.batchId)return;S.detail=await api(`/fbo/batches/${encodeURIComponent(S.batchId)}`);
    const b=S.detail.batch;R('fboCurrentTitle').textContent=b.name;R('fboCurrentMeta').textContent=`${b.source||''} · ${fmtQty(b.units)} ед. · ${fmtQty(b.boxes)} коробов · остатки не меняются`;
    renderBoxes();renderItems();
    for(const k of ['BARCODE','CHZ']){const id=k==='BARCODE'?b.barcode_label_batch_id:b.chz_label_batch_id;S.label[k]=id?await api(`/fbo/label-batches/${encodeURIComponent(id)}`):null;renderLabels(k);}
    document.querySelectorAll('[data-fbo-batch]').forEach(x=>x.classList.toggle('active',x.dataset.fboBatch===S.batchId));
  }
  function renderBoxes(){const boxes=S.detail?.boxes||[];const sel=R('fboBoxSelect');const prev=sel.value;sel.innerHTML='<option value="">Выберите короб</option>'+boxes.map(x=>`<option value="${esc(x.name)}">${esc(x.name)} · ${fmtQty(x.units)} ед.</option>`).join('');if(boxes.some(x=>x.name===prev))sel.value=prev;R('fboBoxes').innerHTML=boxes.map(x=>`<article class="result-card"><b>${esc(x.name)}</b><span>${fmtQty(x.units)} ед. · ${fmtQty(x.lines)} строк</span><small>${esc(x.performed_by||'')}</small></article>`).join('')||'<div class="empty-state">Коробов ещё нет.</div>';}
  function renderItems(){const rows=S.detail?.items||[];R('fboItems').innerHTML=rows.map(x=>`<tr><td>${fmtDate(x.created_at)}</td><td><b>${esc(x.box_name)}</b></td><td><b>${esc(x.article)} · ${esc(x.size)}</b><small>${esc(x.product_name)}</small></td><td>${esc(x.barcode)}</td><td>${fmtQty(x.quantity)}</td><td>${esc(x.performed_by||'')}</td><td><button class="mini danger" type="button" data-fbo-delete="${esc(x.id)}">Удалить</button></td></tr>`).join('')||'<tr><td colspan="7">Товар в короба ещё не уложен.</td></tr>';}
  async function createBox(){if(!S.batchId)throw new Error('Сначала откройте FBO-партию');const name=R('fboBoxName').value.trim();if(!name)throw new Error('Введите или отсканируйте название короба');const r=await api(`/fbo/batches/${encodeURIComponent(S.batchId)}/boxes`,{method:'POST',body:JSON.stringify({name,performed_by:actor()})});R('fboBoxName').value='';await loadDetail();R('fboBoxSelect').value=r.name;R('fboScan').focus();setMsg(r.created?`Короб ${r.name} создан`:`Короб ${r.name} уже существует — открыт`);}
  async function addItem(){if(!S.batchId)throw new Error('Сначала откройте FBO-партию');const box=R('fboBoxSelect').value;if(!box)throw new Error('Выберите короб');const barcode=R('fboScan').value.trim();if(!barcode)throw new Error('Отсканируйте ШК товара');const quantity=Math.max(1,Number(R('fboQty').value||1));const r=await api(`/fbo/batches/${encodeURIComponent(S.batchId)}/items`,{method:'POST',body:JSON.stringify({barcode,box_name:box,quantity,performed_by:actor()})});R('fboScan').value='';await loadDetail();R('fboBoxSelect').value=box;R('fboScan').focus();setMsg(`Добавлено: ${r.article||r.barcode} → ${r.box}`);}
  async function deleteItem(id){if(!confirm('Удалить ошибочно добавленную позицию из раскладки FBO? Остатки склада это не меняет.'))return;await api(`/fbo/batches/${encodeURIComponent(S.batchId)}/items/${encodeURIComponent(id)}`,{method:'DELETE'});await loadDetail();setMsg('Позиция удалена из раскладки FBO');}
  async function upload(kind){if(!S.batchId)throw new Error('Сначала откройте FBO-партию');const input=R(kind==='BARCODE'?'fboBarcodePdf':'fboChzPdf'),file=input.files?.[0];if(!file)throw new Error('Выберите PDF');const fd=new FormData();fd.append('file',file);fd.append('performed_by',actor());setMsg('Разбираем PDF…');const r=await api(`/fbo/batches/${encodeURIComponent(S.batchId)}/labels/${kind}`,{method:'POST',body:fd,timeoutMs:120000});S.label[kind]=r;renderLabels(kind);setMsg(`${kind==='BARCODE'?'ШК':'ЧЗ'} PDF загружен: ${r.page_count} стр.`);}
  function renderLabels(kind){const host=R(kind==='BARCODE'?'fboBarcodeGroups':'fboChzGroups'),d=S.label[kind];if(!host)return;if(!d){host.innerHTML='<div class="empty-state">PDF ещё не загружен.</div>';return;}if(kind==='CHZ'){host.innerHTML=`<div class="result-card"><b>${esc(d.file_name||'ЧЗ PDF')}</b><span>${fmtQty(d.page_count)} этикеток/страниц</span><small>«Распечатать все ЧЗ» отправит каждую страницу в Print Agent один раз.</small></div>`;return;}host.innerHTML=(d.groups||[]).map((g,i)=>`<div class="fbo-label-row"><div><b>${esc(g.title||('Этикетка '+(i+1)))}</b><small>Найдено в PDF: ${fmtQty(g.found)} · стр. ${esc((g.page_nos||[]).join(', '))}</small></div><label>Печатать, шт.<input type="number" min="0" max="500" value="${Math.max(1,Number(g.found||1))}" data-fbo-copy-page="${g.page_no}"></label><a class="secondary mini" target="_blank" href="/fbo/label-batches/${encodeURIComponent(d.id)}/pages/${g.page_no}.pdf">Просмотр</a></div>`).join('')||'<div class="empty-state">Страницы не определены.</div>';}
  async function printBarcode(){const d=S.label.BARCODE;if(!d)throw new Error('Сначала загрузите PDF ШК');const groups=[...document.querySelectorAll('[data-fbo-copy-page]')].map(x=>({page_no:Number(x.dataset.fboCopyPage),copies:Number(x.value||0)})).filter(x=>x.copies>0);if(!groups.length)throw new Error('Укажите количество для печати');const r=await api(`/fbo/label-batches/${encodeURIComponent(d.id)}/print`,{method:'POST',body:JSON.stringify({print_station:station(),groups}),timeoutMs:120000});setMsg(`В Print Agent ${r.print_station} отправлено ШК: ${r.queued}`);}
  async function printChz(){const d=S.label.CHZ;if(!d)throw new Error('Сначала загрузите PDF ЧЗ');if(!confirm(`Отправить в автопечать все ${d.page_count} ЧЗ?`))return;const r=await api(`/fbo/label-batches/${encodeURIComponent(d.id)}/print-all`,{method:'POST',body:JSON.stringify({print_station:station()}),timeoutMs:120000});setMsg(`В Print Agent ${r.print_station} отправлены все ЧЗ: ${r.queued}`);}
  async function exportXlsx(){if(!S.batchId)throw new Error('Откройте FBO-партию');const blob=await apiBlob(`/fbo/batches/${encodeURIComponent(S.batchId)}/composition.xlsx`,{timeoutMs:120000});saveBlob(blob,`FBO_раскладка_${(S.detail?.batch?.name||'партия').replace(/[^0-9A-Za-zА-Яа-я_-]+/g,'_')}.xlsx`);}
  function install(){
    if(typeof titles!=='undefined')titles.fbo='Отгрузка FBO';
    const nav=document.querySelector('[data-page="fbo"]');if(nav&&!nav.dataset.fboBound){nav.dataset.fboBound='1';nav.addEventListener('click',()=>setTimeout(()=>loadBatches().catch(e=>setMsg(e.message,true)),20));}
    R('fboWorker').value=localStorage.getItem('fc_fbo_worker')||'';
    R('fboCreate')?.addEventListener('click',()=>createBatch().catch(e=>setMsg(e.message,true)));
    R('fboRefresh')?.addEventListener('click',()=>loadBatches().catch(e=>setMsg(e.message,true)));
    R('fboBatchRows')?.addEventListener('click',e=>{const b=e.target.closest('[data-fbo-batch]');if(!b)return;S.batchId=b.dataset.fboBatch;loadDetail().catch(x=>setMsg(x.message,true));});
    R('fboCreateBox')?.addEventListener('click',()=>createBox().catch(e=>setMsg(e.message,true)));
    R('fboBoxName')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();createBox().catch(x=>setMsg(x.message,true));}});
    R('fboAddItem')?.addEventListener('click',()=>addItem().catch(e=>setMsg(e.message,true)));
    R('fboScan')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();addItem().catch(x=>setMsg(x.message,true));}});
    R('fboItems')?.addEventListener('click',e=>{const b=e.target.closest('[data-fbo-delete]');if(b)deleteItem(b.dataset.fboDelete).catch(x=>setMsg(x.message,true));});
    R('fboUploadBarcode')?.addEventListener('click',()=>upload('BARCODE').catch(e=>setMsg(e.message,true)));
    R('fboUploadChz')?.addEventListener('click',()=>upload('CHZ').catch(e=>setMsg(e.message,true)));
    R('fboPrintBarcode')?.addEventListener('click',()=>printBarcode().catch(e=>setMsg(e.message,true)));
    R('fboPrintChz')?.addEventListener('click',()=>printChz().catch(e=>setMsg(e.message,true)));
    R('fboExport')?.addEventListener('click',()=>exportXlsx().catch(e=>setMsg(e.message,true)));
    R('fboWorker')?.addEventListener('change',()=>localStorage.setItem('fc_fbo_worker',R('fboWorker').value.trim()));
  }
  install();
  window.FCFbo={loadBatches};
})();
