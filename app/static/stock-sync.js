/* r72: committed changes refresh scoped views; background reads keep rows visible. */
(()=>{'use strict';
  const scope=()=>JSON.stringify([state.commercialSession?.username||'',state.commercialSession?.tenant_id||'',state.clientId||'',state.warehouseId||'']);
  const contextPages=new Set(['dashboard','containers','locations','move','receive','bulkimport','palletreceive','ship','masterdata','operations']);
  let generation=0,flight=null,flightScope='',revision=null,revisionScope='',polling=false;
  const rawApi=api,rawBlob=apiBlob;
  const stockReadPosts=new Set(['/warehouse-ui/wb-sales/status','/warehouse-ui/wb-sales/check','/inventory/movement-summary','/receiving/inventory-control-status','/receiving/wb-live-stocks','/receiving/wb-drift-repair']);
  const stockMutation=(path,opt)=>!['GET','HEAD'].includes(String(opt?.method||'GET').toUpperCase())&&!stockReadPosts.has(String(path).split('?')[0].replace(/\/$/,''))&&!/(\/auth\/|\/preview|\/template|\/export|\/labels|\/label\/|\/verify-|\/draft\/|\/lookup|\/resolve|\/scan-marking-code)/.test(path);
  function note(text,error=false){let n=$('r70StockSyncStatus');if(!n){n=document.createElement('div');n.id='r70StockSyncStatus';n.setAttribute('role','status');document.body.appendChild(n);}n.textContent=text;n.hidden=!text;n.className=error?'error':'';}
  function invalidate(){
    generation++;
    state.inventoryMovementPromise=null;state.inventoryMovementContextKey='';state.bulkReceivingHistoryLoaded=false;
    state.r70CatalogSequence=(state.r70CatalogSequence||0)+1;state.inventoryCatalogLoading=false;
    state.inventoryCatalog=(state.inventoryCatalog||[]).map(x=>({...x,_truth_pending:true}));
    state.inventoryCatalogLoaded=false;state.inventoryCatalogLoadedAt=0;
    state.r70ContextDirty=true;state.r70ContextLoadedAt=0;
    state.contextLoadSeq++;state.contextLoadPromise=null;state.contextLoadKey='';
    window.FCStockRooms?.invalidate(true);
    inventoryPopupSequence++;containerContentsSequence++;
    if(typeof locationBrowser!=='undefined')locationBrowser.contentsRequest='r70-invalid';
  }
  function removeArchived(path,data){
    const ids=new Set((data?.deleted||[]).map(x=>String(x.id)));
    const m=String(path).match(/\/boxes\/([^/]+)\/archive-empty/);if(m)ids.add(decodeURIComponent(m[1]));
    if(!ids.size)return;
    state.containers=state.containers.filter(x=>!ids.has(String(x.id)));
    state.inventory=state.inventory.filter(x=>!ids.has(String(x.container_id)));
    for(const id of ids)selectedContainerIds.delete(id);
    const panel=$('containerDetailPanel');
    if(panel&&ids.has(panel.dataset.containerId)){panel.classList.add('hidden');panel.innerHTML='';delete panel.dataset.containerId;}
    renderContainers();renderDashboard();
  }
  async function refresh(){
    const captured=scope();
    if(flight&&flightScope===captured)return flight;
    flightScope=captured;
    const job=Promise.resolve().then(async()=>{
      let seen=-1;
      while(captured===scope()&&seen!==generation){
        seen=generation;if(!state.clientId||!state.warehouseId)return;
        const active=state.activePage,box=$('containerDetailPanel')?.dataset.containerId;
        const product=typeof currentProductPopup!=='undefined'?currentProductPopup:null;
        try{
          // MAIN and FBS use their own local endpoint. No five-reference load,
          // credential lookup or external WB reconciliation ahead of the rows.
          if(contextPages.has(active))await loadContextData({force:true,quiet:true});
          if(captured!==scope())return;
          const jobs=[];
          if(active==='mainstock')jobs.push(window.FCStockRooms?.refresh('MAIN',true));
          if(active==='inventory'){
            jobs.push(window.FCStockRooms?.refresh('FBS',true));
            jobs.push(loadInventoryCatalog({force:true,localOnly:true}));
          }
          if(active==='containers'&&box&&state.containers.some(x=>String(x.id)===box&&x.status==='ACTIVE'))jobs.push(showContainer(box,{quiet:true}));
          if(active==='locations'&&locationBrowser.locationId){if(locationBrowser.level==='HISTORY')jobs.push(openLocationHistory(locationBrowser.locationId));else jobs.push(openLocationContents(locationBrowser.locationId));}
          if(product&&!$('inventoryProductModal')?.classList.contains('hidden'))jobs.push(openInventoryProduct(product.productId,product.barcode));
          jobs.push(window.FCStockRooms?.refreshOpen());
          await Promise.all(jobs);
          const failed=window.FCStockRooms?.error(active==='mainstock'?'MAIN':active==='inventory'?'FBS':'');
          if(failed)throw Error(failed);
          if(captured===scope()&&seen===generation){
            // Other screens must refresh their reference data when entered later.
            state.r70ContextDirty=!contextPages.has(active);note('');
          }
        }catch(e){
          if(captured===scope())note('Не удалось обновить отображение. Показаны последние данные: '+e.message,true);
          // Do not start an unbounded two-second retry loop. Revision polling
          // and the explicit Refresh button can retry without erasing the table.
          return;
        }
      }
    }).finally(()=>{if(flight===job){flight=null;flightScope='';}});
    flight=job;return job;
  }
  let channel;
  try{channel=new BroadcastChannel('fc-r70-stock');channel.onmessage=e=>{if(e.data?.scope===scope()){invalidate();refresh();}};}catch{}
  async function changed(path='',data=null){
    invalidate();removeArchived(path,data);
    try{channel?.postMessage({scope:scope()});}catch{}
    await refresh();
  }
  api=async function(path,opt={}){const captured=scope();const data=await rawApi(path,opt);if(captured===scope()&&stockMutation(path,opt))await changed(path,data);return data;};
  apiBlob=async function(path,opt={}){const captured=scope();const data=await rawBlob(path,opt);if(captured===scope()&&stockMutation(path,opt))await changed(path);return data;};
  const oldGo=go;
  go=function(p){oldGo(p);if(state.r70ContextDirty&&contextPages.has(state.activePage))refresh();};
  async function poll(){
    if(polling||flight||document.hidden||!state.clientId||!state.warehouseId)return;
    const kind=state.activePage==='mainstock'?'MAIN':state.activePage==='inventory'?'FBS':null;
    const rooms=window.FCStockRooms;
    // The in-flight snapshot includes its own revision. Wait for it instead of
    // cancelling it on every timer tick and restarting the very same request.
    if(kind&&(rooms?.isFetching?.(kind)||Date.now()<(rooms?.retryAt?.(kind)||0)))return;
    polling=true;const captured=scope();
    try{
      const r=await rawApi('/warehouse-ui/revision?'+new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId}),{timeoutMs:5000});
      if(captured!==scope())return;
      if(revisionScope!==captured){revisionScope=captured;revision=null;}
      const snapshot=kind?rooms?.revision?.(kind):null;
      const different=String(snapshot??revision)!==String(r.revision);revision=String(r.revision);
      if(different){invalidate();await refresh();}
    }catch{}finally{polling=false;}
  }
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)poll();});
  document.addEventListener('change',e=>{if(['clientSelect','warehouseSelect'].includes(e.target?.id)){revision=null;revisionScope='';generation++;note('');}},true);
  window.addEventListener('message',e=>{if(e.origin===location.origin&&e.source===$('fcWorkflowFrame')?.contentWindow&&e.data?.type==='fc-warehouse-changed'&&e.data.client_id===state.clientId&&e.data.warehouse_id===state.warehouseId)changed();});
  setInterval(poll,3000);setTimeout(poll,0);
  window.FCStockSync={changed,refresh,invalidate,poll};
  const style=document.createElement('style');
  style.textContent='#r70StockSyncStatus{position:fixed;bottom:18px;right:18px;z-index:30000;padding:12px 18px;border-radius:12px;background:#eef2ff;color:#27326c;box-shadow:0 3px 18px #0002;max-width:min(560px,90vw)}#r70StockSyncStatus.error{background:#fff0ee;color:#a12020}#r70StockSyncStatus[hidden]{display:none}#containerMovementHelp{flex:1 1 340px;min-width:0}#containerBulkToolbar{flex:0 1 230px}.container-actions{display:flex;flex-wrap:wrap;gap:8px}';
  document.head.appendChild(style);
})();
