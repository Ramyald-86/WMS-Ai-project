/* FC_R145F20_SAFE_UI_PREFS: navigation must not fail on a full browser quota.
   This helper is NOT used for drafts, pending operations or product data. */
function fcR20Remember(key,value){
  try{localStorage.setItem(key,String(value));return true;}
  catch(e){console.warn('FC_UI_PREFERENCE_NOT_SAVED',key,e);return false;}
}
/* FC_R145F18_PVZ_PRODUCT_RU_LAYOUT: PVZ product barcode scans auto-correct RU keyboard layout to EN. */
/* FC_R145F17_PVZ_RECEIPT_EXCEL_SPLIT: load PVZ per-receipt Excel actions separately from defect export. */
/* FC_R145F11_ADMIN_RECEIPT_GROUP_PICK_ASSIGN_MODES */
/* FC_R145F9_NAV_PRINT_SETTINGS_R145F6_R145F8_SAFE: r145f8 cumulative features + resilient navigation + centralized print mode. */
/* FC_R145F5_PVZ_RU_LAYOUT_AUTOFOCUS_R145F2_R145F3_R145F4_SAFE: load pvz.js r145f5; auto-fix RU storage scans and focus product barcode. */
/* FC_R144_TRANSIT_DELETE_BOX_CARD_FULL_LABEL: delete exact box stock, clickable product cards, full-media box QR. */
// FC_R141_DEFECT_STORAGE_SCAN_BOX_LABEL: defect custody + PVZ location scan/public box + full-size box labels.
// FC_R140F1_TSD_BARCODE_GUARD_FAST_STOCK_PVZ: r139 fast write-off + r138 PVZ search bundled; TSD guard lives in tsd-stocktake.js/server.
// FC_R139_FAST_STOCK_WRITEOFF: seeded dialog + one-pass server write-off.
/* FC_R136_LOCAL_CARD_AUTOPRINT_LABEL_FIT: manual Product Base cards + local Print Agent labels + exact 58x40 box/product agent output. */
/* 0.9.4.15g5a8k STABILITY_SCOPED_IO */
/* 0.9.4.15g5a8j WB_VERIFIED_SYNC_BOX_QR_SCAN_RETRY_ONLY */
/* FC_STRICT_WAREHOUSE_SCOPE_09415G1_20260903 */
/* legacy regression wording: последняя наша цель; UI now says последний отправленный остаток */
/* FC_BOX_UI_SAFE_DELETE_09415D_20260903 */
/* FC_BUILD_0800_COMMERCIAL */
const selectedInventoryBarcodes=new Set();
const $ = (id) => document.getElementById(id);
/* r101 INPUT_FOCUS_BACKGROUND_GUARD
   Background refreshes must never kick the operator out of a field while typing.
   Packing scanner fields are intentionally excluded: their fast live loop must keep working. */
const FC_BACKGROUND_SAFE_INPUT_IDS=new Set(['wbPackProduct','wbPackChz']);
function fcEditableControl(el){
  if(!el||el===document.body||el.disabled||el.readOnly)return false;
  if(el.isContentEditable)return true;
  const tag=String(el.tagName||'').toUpperCase();
  if(tag==='TEXTAREA'||tag==='SELECT')return true;
  if(tag!=='INPUT')return false;
  const type=String(el.type||'text').toLowerCase();
  return !['hidden','checkbox','radio','button','submit','reset','file','color','range','image'].includes(type);
}
function fcUserEditing(root=document){
  const el=document.activeElement;
  if(!fcEditableControl(el)||FC_BACKGROUND_SAFE_INPUT_IDS.has(el.id))return false;
  return !root||root===document||Boolean(root.contains?.(el));
}
function fcFocusedControlSnapshot(root=document){
  const el=document.activeElement;
  if(!fcEditableControl(el)||!root?.contains?.(el))return null;
  let selector='';
  if(el.id)selector='#'+CSS.escape(el.id);
  else if(el.dataset?.wbColumnFilter)selector='[data-wb-column-filter="'+String(el.dataset.wbColumnFilter).replace(/"/g,'\\"')+'"]';
  else if(el.name){
    const name=String(el.name).replace(/\\/g,'\\\\').replace(/"/g,'\\"');
    const candidate=String(el.tagName||'input').toLowerCase()+'[name="'+name+'"]';
    try{if(root.querySelectorAll(candidate).length===1)selector=candidate;}catch(_){}
  }
  if(!selector)return null;
  let start=null,end=null,direction=null;
  try{start=el.selectionStart;end=el.selectionEnd;direction=el.selectionDirection;}catch(_){}
  return {selector,value:Object.prototype.hasOwnProperty.call(el,'value')||'value' in el?String(el.value??''):null,start,end,direction,scrollTop:Number(el.scrollTop||0),scrollLeft:Number(el.scrollLeft||0)};
}
function fcRestoreFocusedControl(root,snapshot){
  if(!snapshot||!root)return false;
  let el=null;try{el=root.querySelector(snapshot.selector);}catch(_){}
  if(!fcEditableControl(el))return false;
  try{
    if(snapshot.value!==null&&'value' in el&&String(el.value)!==snapshot.value)el.value=snapshot.value;
    el.focus({preventScroll:true});
    if(snapshot.start!==null&&snapshot.end!==null&&typeof el.setSelectionRange==='function')el.setSelectionRange(snapshot.start,snapshot.end,snapshot.direction||'none');
    el.scrollTop=snapshot.scrollTop||0;el.scrollLeft=snapshot.scrollLeft||0;
  }catch(_){}
  return document.activeElement===el;
}
window.FCInputGuard={isEditing:fcUserEditing,capture:fcFocusedControlSnapshot,restore:fcRestoreFocusedControl};
(function installInventoryBulkStyles(){if(document.getElementById('fcInventoryBulkStyles'))return;const style=document.createElement('style');style.id='fcInventoryBulkStyles';style.textContent='.inventory-select-cell{width:42px;text-align:center}.inventory-select-cell input,[data-inventory-select-visible]{width:20px;height:20px;cursor:pointer;accent-color:#4f46e5}.inventory-bulk-toolbar{position:sticky;top:0;z-index:8;background:#fff;border:2px solid #d8d5ff;border-radius:14px;padding:12px 14px;margin:10px 0 14px;box-shadow:0 5px 18px rgba(43,38,117,.10)}.inventory-bulk-summary{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:9px}.inventory-bulk-summary small{color:#667085}.inventory-bulk-actions{display:flex;gap:8px;flex-wrap:wrap}.inventory-bulk-actions button{min-height:40px}';document.head.appendChild(style);})();

(function installGlobalTableScrollStyles(){if(document.getElementById('fcGlobalTableScrollStyles'))return;const style=document.createElement('style');style.id='fcGlobalTableScrollStyles';style.textContent='.fc-table-scroll-top{height:17px;overflow-x:auto;overflow-y:hidden;margin:2px 0 4px}#page-inventory .fc-table-scroll-top{position:sticky;z-index:13;height:17px;background:#fff;margin:2px 0 4px;padding:0;border:0;border-radius:0;box-shadow:none}#page-inventory .fc-table-scroll-top>div{height:1px}.fc-table-scroll-top>div{height:1px}.table-wrap{overflow-x:auto}.cell-qty-actions{display:inline-flex;gap:5px;margin-left:8px;vertical-align:middle}.cell-qty-actions button{min-width:30px;padding:4px 7px}.inventory-wb-errors{margin-top:8px}';document.head.appendChild(style);})();
let __fcScrollInstallQueued=false;function installTopTableScrolls(){__fcScrollInstallQueued=false;document.querySelectorAll('.table-wrap').forEach(wrap=>{if(wrap.dataset.fcTopScroll==='1')return;const table=wrap.querySelector(':scope > table');if(!table)return;wrap.dataset.fcTopScroll='1';const top=document.createElement('div');top.className='fc-table-scroll-top';const spacer=document.createElement('div');top.appendChild(spacer);wrap.parentNode?.insertBefore(top,wrap);let lock=false;const size=()=>{spacer.style.width=`${Math.max(table.scrollWidth,wrap.scrollWidth)}px`;top.style.display=table.scrollWidth>wrap.clientWidth?'block':'none';if(top.closest('#page-inventory')){const toolbar=document.querySelector('#page-inventory .inventory-bulk-toolbar');top.style.top=`${toolbar?Math.ceil(toolbar.getBoundingClientRect().height)+6:6}px`;}top.scrollLeft=wrap.scrollLeft;};top.addEventListener('scroll',()=>{if(lock)return;lock=true;wrap.scrollLeft=top.scrollLeft;requestAnimationFrame(()=>lock=false)});wrap.addEventListener('scroll',()=>{if(lock)return;lock=true;top.scrollLeft=wrap.scrollLeft;requestAnimationFrame(()=>lock=false)});size();if(window.ResizeObserver)new ResizeObserver(size).observe(wrap);});}
function queueTopTableScrolls(){if(__fcScrollInstallQueued)return;__fcScrollInstallQueued=true;requestAnimationFrame(()=>installTopTableScrolls());}
new MutationObserver(()=>queueTopTableScrolls()).observe(document.documentElement,{childList:true,subtree:true});window.addEventListener('resize',queueTopTableScrolls);
const INITIAL_WAREHOUSE_ID = localStorage.getItem('fc_warehouse') || null;
function readStoredJson(key,fallback){try{const value=JSON.parse(localStorage.getItem(key)||'null');return value??fallback}catch{return fallback}}
function normalizeContextId(value){return value===null||value===undefined?'':String(value)}
function sameContextId(a,b){return normalizeContextId(a)===normalizeContextId(b)}
const CONTEXT_MASTER_CACHE_KEY='fc_context_master_v1';
function readContextMasterCache(){const x=readStoredJson(CONTEXT_MASTER_CACHE_KEY,{});return {clients:Array.isArray(x.clients)?x.clients:[],warehouses:Array.isArray(x.warehouses)?x.warehouses:[]}}
function saveContextMasterCache(){try{localStorage.setItem(CONTEXT_MASTER_CACHE_KEY,JSON.stringify({clients:state.clients||[],warehouses:state.warehouses||[],saved_at:Date.now()}))}catch(_){}}
const FULFILLCORE_DEVICE_ID=localStorage.getItem('fc_device_id')||(`DEV-${Date.now().toString(36)}-${Math.random().toString(36).slice(2,10)}`).toUpperCase();fcR20Remember('fc_device_id',FULFILLCORE_DEVICE_ID);
function scopedStorageKey(base,warehouseId=INITIAL_WAREHOUSE_ID){return `${base}:${warehouseId||'none'}`;}
function smartWbColumnFilters(raw){
  const value=raw&&typeof raw==='object'?raw:{};
  const brands=Array.isArray(value.brands)?[...new Set(value.brands.map(x=>String(x||'').trim()).filter(Boolean))]:[];
  return {label:String(value.label||''),chz_state:['MISSING','ASSIGNED'].includes(value.chz_state)?value.chz_state:'',brands};
}
// FC_R143_CELL_DIRECT_VIRTUAL_RECEIVING_CLIENT_REPORTS: direct cell receiving without box + lean virtual receiving.
const state = {
  clients: [], warehouses: [], locations: [], locationOccupancy: {}, skus: [], containers: [], inventory: [], inventoryCatalog: [], inventoryCatalogLoaded: false, inventoryCatalogProducts: 0, inventoryCatalogContextKey: "", inventoryCatalogLoadedAt: 0, inventoryCatalogLoading: false, inventoryMovementPromise: null, inventoryMovementContextKey: '', fbsStockStatus: {}, inventoryWbControl: {}, wbLiveStock: {}, wbLiveStockSummary: null, wbLiveStockCheckedAt: null, wbLiveStockWarehouseId: null, wbLiveStockError: '', operations: [],
  clientId: localStorage.getItem('fc_client') || null,
  warehouseId: INITIAL_WAREHOUSE_ID,
  marketCredentials: [], wbOrders: [], wbSupplies: [], wbWarehouses: [], ozonPostings: [], ozonWarehouses: [], wbProducts: [], wbProductTotal: 0, wbProductMatching: 0, wbProductRequestSeq: 0, excelPreview: null, inventoryBulkWbErrors: [],
  externalPickLastBatchId: null,
  wbCredentialId: localStorage.getItem('fc_wb_credential') || '', wbProductCredentialId: localStorage.getItem('fc_wb_product_credential') || '',
  ozonCredentialId: localStorage.getItem('fc_ozon_credential') || '', ozonWarehouseId: localStorage.getItem(scopedStorageKey('fc_ozon_warehouse')) || '',
  wbOrderFilter: localStorage.getItem(scopedStorageKey('fc_wb_order_filter')) || localStorage.getItem('fc_wb_order_filter') || '', wbActivePackingOrder: null, wbActivePackingCredentialId: '', wbOrderCounts: {},
  activePage: localStorage.getItem('fc_active_page') || 'dashboard',
  wbWarehouseFilter: localStorage.getItem(scopedStorageKey('fc_wb_warehouse_filter')) || localStorage.getItem('fc_wb_warehouse_filter') || '',
  wbPageSize: Number(localStorage.getItem('fc_wb_page_size') || '50'), wbPage: 1, wbPages: 1, wbTotal: 0,
  wbColumns: readStoredJson('fc_wb_columns',['status','order','warehouse','article','name','size','barcode','reserved','supply','chz','label','created','actions']),
  wbColumnWidths: readStoredJson('fc_wb_column_widths',{}),
  wbColumnFilters: smartWbColumnFilters(readStoredJson(scopedStorageKey('fc_wb_column_filters'),{})),
  wbBrands: [], wbBrandsKey: '', wbBrandsLoading: false,
  wbSort: readStoredJson('fc_wb_sort',{key:'created',dir:'asc'}),
  wbWarehousesCollapsed: localStorage.getItem('fc_wb_wh_collapsed') !== '0',
  wbAutoLocalTimer: null, wbAutoSyncTimer: null, wbMetaRefreshTimer: null, wbReconcileTimer: null, wbSupplyTimer: null, wbLoadingOrders: false, wbOrdersRequestSeq: 0, wbUsingCache: false,
  wbShowClosedSupplies: localStorage.getItem('fc_wb_show_closed_supplies') === '1',
  wbScanBusy: false, wbHotRefreshTimer: null, wbBackgroundPrintChains: {}, wbSupplyMeta: {}, wbSupplyActionBusy: false,
  wbSelectedOrderIds: new Set(readStoredJson(scopedStorageKey('fc_wb_selected_orders'),[]).map(String)),
  wbPrintMode: localStorage.getItem('fc_wb_print_mode') || 'agent',
  wbPrintStation: localStorage.getItem('fc_wb_print_station') || 'PACK-01',
  workstationProfile: null, workstationProfileTimer: null, deviceId: FULFILLCORE_DEVICE_ID,
  contextLoadPromise: null, contextLoadKey: '', contextLoadSeq: 0, contextRetryTimer: null, contextDataKey: '',
  warehouseMenuOpen: localStorage.getItem('fc_warehouse_menu_open') !== '0',
  commercialSession: null,
  bulkReceivingHistory: [], bulkReceivingHistoryLoaded: false, bulkReceivingHistoryLoading: false, bulkReceivingHistoryContextKey: '',
};

function wbWarehouseFilterKey(warehouseId=state.warehouseId){return scopedStorageKey('fc_wb_warehouse_filter',warehouseId);}
function wbOrderFilterKey(warehouseId=state.warehouseId){return scopedStorageKey('fc_wb_order_filter',warehouseId);}
function wbColumnFiltersKey(warehouseId=state.warehouseId){return scopedStorageKey('fc_wb_column_filters',warehouseId);}
function wbSelectedOrdersKey(warehouseId=state.warehouseId){return scopedStorageKey('fc_wb_selected_orders',warehouseId);}
function saveWbSelectedOrders(){try{localStorage.setItem(wbSelectedOrdersKey(),JSON.stringify([...state.wbSelectedOrderIds]))}catch(_){}}
function restoreWbSelectedOrders(){state.wbSelectedOrderIds=new Set(readStoredJson(wbSelectedOrdersKey(),[]).map(String));}
function clearWbSelectedOrders(){state.wbSelectedOrderIds.clear();saveWbSelectedOrders();}
function saveScopedWbWarehouseFilter(){localStorage.setItem(wbWarehouseFilterKey(),state.wbWarehouseFilter||'');}
function restoreScopedWbState(){
  state.wbWarehouseFilter=localStorage.getItem(wbWarehouseFilterKey())||'';
  state.wbOrderFilter=localStorage.getItem(wbOrderFilterKey())||'';
  state.wbColumnFilters=smartWbColumnFilters(readStoredJson(wbColumnFiltersKey(),{}));
  restoreWbSelectedOrders();
  state.wbBrands=[];state.wbBrandsKey='';
  localStorage.setItem(wbColumnFiltersKey(),JSON.stringify(state.wbColumnFilters));
  renderWbBrandFilter();
}

// 0.7.8.31 replaces the duplicate date/age pair with one useful "age" view in
// the existing created column. Preserve the user's order for every other column.
if(localStorage.getItem('fc_wb_columns_schema')!=='0.7.8.31'){
  state.wbColumns=state.wbColumns.filter(k=>k!=='age');
  if(!state.wbColumns.includes('label')){const i=state.wbColumns.indexOf('actions');if(i>=0)state.wbColumns.splice(i,0,'label');else state.wbColumns.push('label');}
  if(!state.wbColumns.includes('created')){const i=state.wbColumns.indexOf('actions');if(i>=0)state.wbColumns.splice(i,0,'created');else state.wbColumns.push('created');}
  fcR20Remember('fc_wb_columns',JSON.stringify(state.wbColumns));
  fcR20Remember('fc_wb_columns_schema','0.7.8.31');
}

const titles = {
  dashboard:'Главная', receive:'Новая приёмка · вручную', palletreceive:'Приёмка', bulkimport:'Новая приёмка · Excel', inventory:'Остатки',
  locations:'Адресное хранение', containers:'Короба', storage:'Хранение', move:'Перемещение', transit:'Транзитный склад', defectstorage:'Брак · хранение', marketplaces:'Wildberries FBS',
  ozon:'Ozon FBS', products:'Карточки WB', account:'Личный кабинет',
  ship:'Отгрузка', operations:'История приёмки', finance:'Финансы и аналитика', financehistory:'История начислений', warehousework:'Сборка', printsettings:'Настройки печати', masterdata:'Справочники'
};

function esc(v){return String(v ?? '').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function uid(){return (crypto.randomUUID ? crypto.randomUUID() : `fc-${Date.now()}-${Math.random()}`).toString();}
function fmtQty(v){const n=Number(v||0);return Number.isInteger(n)?String(n):n.toLocaleString('ru-RU',{maximumFractionDigits:3});}
function mapBy(list,id){return list.find(x=>x.id===id);}
function detailText(data){
  if(!data) return 'Неизвестная ошибка';
  if(typeof data.detail === 'string') return data.detail;
  if(data.detail && typeof data.detail === 'object') return data.detail.message || JSON.stringify(data.detail);
  if(Array.isArray(data.detail)) return data.detail.map(x=>x.msg||JSON.stringify(x)).join('; ');
  return data.message || JSON.stringify(data);
}
async function fcFetch(path,options={}){
  const method=String(options.method||'GET').toUpperCase();
  // Browser aborts are only a safety net. Core warehouse actions get a generous budget,
  // and timeoutMs:0 explicitly means "do not abort" (used by the LOCAL packing scan).
  const hasTimeout=Object.prototype.hasOwnProperty.call(options,'timeoutMs');
  const defaultTimeout=(path.includes('/marketplaces/')||path.includes('/receiving/'))?120000:30000;
  const timeoutMs=hasTimeout?Math.max(0,Number(options.timeoutMs)||0):defaultTimeout;
  const attempts=method==='GET'?2:1;
  const baseHeaders={...(options.headers||{})};
  if(method!=='GET'&&!baseHeaders['X-FulfillCore-Request-Id'])baseHeaders['X-FulfillCore-Request-Id']=uid();
  let lastError=null;
  for(let attempt=0;attempt<attempts;attempt++){
    const controller=new AbortController();
    const timer=timeoutMs>0?setTimeout(()=>controller.abort(new DOMException('Request timeout','TimeoutError')),timeoutMs):null;
    const external=options.signal;
    const relay=()=>controller.abort(external?.reason);
    if(external?.aborted){if(timer)clearTimeout(timer);throw external.reason||new DOMException('Aborted','AbortError');}
    if(external)external.addEventListener('abort',relay,{once:true});
    try{
      const requestOptions={cache:'no-store',...options,headers:baseHeaders,signal:controller.signal};delete requestOptions.timeoutMs;
      const response=await fetch(path,requestOptions);
      if(method==='GET'&&attempt+1<attempts&&[502,503,504].includes(response.status)){if(timer)clearTimeout(timer);if(external)external.removeEventListener('abort',relay);await new Promise(r=>setTimeout(r,250*(attempt+1)));continue;}
      return response;
    }catch(error){
      lastError=error;
      // FC_K9R4_NO_TIMEOUT_DUPLICATE: aborting fetch does not cancel a synchronous
      // FastAPI handler already running in a worker thread. Retrying a timed-out GET
      // therefore doubled the same heavy DB work and could make every section stall.
      if(error?.name==='TimeoutError'||error?.name==='AbortError')throw error;
      if(method!=='GET'||attempt+1>=attempts||external?.aborted)throw error;
      await new Promise(r=>setTimeout(r,350*(attempt+1)));
    }finally{if(timer)clearTimeout(timer);if(external)external.removeEventListener('abort',relay);}
  }
  throw lastError||new Error('Ошибка сети');
}

async function api(path,options={}){
  const headers = {...(options.headers||{})};
  if(options.body && !(options.body instanceof FormData) && !headers['Content-Type']) headers['Content-Type']='application/json';
  const r=await fcFetch(path,{...options,headers});
  if(r.status===401){location.href='/login';throw new Error('Требуется вход');}
  const text=await r.text(); let data=null;
  try{data=text?JSON.parse(text):null}catch{data=text}
  if(!r.ok){const error=new Error(typeof data==='string'?data:detailText(data));error.status=r.status;error.data=data;throw error;}
  return data;
}
async function apiBlob(path,options={}){
  const headers={...(options.headers||{})};
  if(options.body && !(options.body instanceof FormData) && !headers['Content-Type']) headers['Content-Type']='application/json';
  const r=await fcFetch(path,{...options,headers});
  if(r.status===401){location.href='/login';throw new Error('Требуется вход');}
  if(!r.ok){
    // Read the response body exactly once. Calling json() and then text() on the
    // same Response raises "body stream already read" in Chromium.
    const raw=await r.text(); let d=raw;
    try{d=raw?JSON.parse(raw):{}}catch{}
    const error=new Error(typeof d==='string'?d:detailText(d));error.status=r.status;error.data=d;throw error;
  }
  return r.blob();
}
function base64Blob(value,type='application/pdf'){
  const bin=atob(value);const bytes=new Uint8Array(bin.length);for(let i=0;i<bin.length;i++)bytes[i]=bin.charCodeAt(i);return new Blob([bytes],{type});
}
function saveBlob(blob,name){const u=URL.createObjectURL(blob);const a=document.createElement('a');a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),30000);}
function printBlob(blob,name='document.pdf'){
  const u=URL.createObjectURL(blob);
  const frame=document.createElement('iframe');
  frame.style.cssText='position:fixed;width:1px;height:1px;right:0;bottom:0;border:0;opacity:0;pointer-events:none';
  frame.src=u;document.body.appendChild(frame);
  let printed=false;
  frame.onload=()=>{
    try{frame.contentWindow.focus();frame.contentWindow.print();printed=true;}catch(e){saveBlob(blob,name);}
    setTimeout(()=>{frame.remove();URL.revokeObjectURL(u);},60000);
  };
  setTimeout(()=>{if(!printed && document.body.contains(frame)){saveBlob(blob,name);frame.remove();URL.revokeObjectURL(u);}},8000);
}
function toast(message,error=false){const el=$('toast');el.textContent=message;el.className='toast show'+(error?' error':'');clearTimeout(window.__toastTimer);window.__toastTimer=setTimeout(()=>el.className='toast',3500);}
function opName(t){return ({RECEIVING:'Приёмка',RECEIVING_IMPORT:'Импорт Excel',MOVE:'Перемещение товара',MOVE_CONTAINER:'Перемещение короба',SHIPPING:'Отгрузка',ADJUSTMENT:'Корректировка',RECONCILIATION:'Сверка остатков',PLACE_EMPTY:'Товар закончился',PLACE_REFILLED:'Товар снова добавлен',PLACE_FIRST_STOCK:'Товар добавлен'}[t]||t);}
function skuLabel(s){if(!s)return '—';return [s.article||s.sku_code,s.size,s.name].filter(Boolean).join(' · ');}
function locationLabel(id){const x=mapBy(state.locations,id);return x?(x.display_name||FC25.publicCode(x)):'—';}
function clientLabel(id){return mapBy(state.clients,id)?.name||String(id||'Неизвестный клиент');}
function activeLocations(){return state.locations.filter(x=>x.is_active!==false&&String(x.warehouse_id||state.warehouseId)===String(state.warehouseId));}
function assignableLocations(){return activeLocations().filter(x=>String(x.location_type||'').toUpperCase()!=='SECTION');}
function activeContainers(){return state.containers.filter(x=>String(x.status||'ACTIVE').toUpperCase()==='ACTIVE'&&String(x.client_id||state.clientId)===String(state.clientId)&&String(x.warehouse_id||state.warehouseId)===String(state.warehouseId));}
function stockedContainerIds(){return new Set((state.inventory||[]).filter(x=>Number(x.quantity||0)>0&&x.container_id).map(x=>String(x.container_id)));}
function stockedActiveContainers(){const ids=stockedContainerIds();return activeContainers().filter(x=>ids.has(String(x.id)));}
function locationOccupancy(id){return state.locationOccupancy?.[String(id)]||null;}
function locationOwnerNames(id){const item=locationOccupancy(id);return structuredArray(item?.owner_client_ids).map(clientLabel);}
function locationOtherOwners(id,clientId=state.clientId){const item=locationOccupancy(id);return structuredArray(item?.owner_client_ids).filter(x=>String(x)!==String(clientId||''));}
function assertLocationAvailableForClient(location,clientId=state.clientId){
  if(!location)return;
  const code=String(location.code||'').trim().toUpperCase();if(['БЕЗ-АДРЕСА','ПРИЕМКА','ПРИЕМКА-ПАЛЕТЫ','UNPLACED'].includes(code))return;
  const others=locationOtherOwners(location.id,clientId);if(others.length)throw new Error(`Адрес «${location.code}» уже занят клиентом «${others.map(clientLabel).join(', ')}». Выберите свободный адрес.`);
}
function containerObj(id){return mapBy(state.containers,id);}
function containerLabel(id){const c=containerObj(id);return containerPublicCode(c);}
// FC_K9R56_CONTAINER_MOVE_CLEAR_IDENTITY
function containerPublicCode(c){if(!c)return '—';const barcode=String(c.barcode||'').trim(),name=String(c.name||'').trim();if(name&&!/^FCBOX-/i.test(name))return name;if(barcode&&!/^FCBOX-/i.test(barcode))return barcode;return 'Короб';}
function containerContextPublicCode(c){if(!c)return '—';const name=String(c.container_name||c.name||'').trim(),barcode=String(c.container_barcode||c.barcode||'').trim();if(name&&!/^FCBOX-/i.test(name))return name;if(barcode&&!/^FCBOX-/i.test(barcode))return barcode;return 'Короб';}
function movementLocationInfo(location){
  if(!location)return {address:'—',cell:'—',pallet:'—',type:'Адрес'};
  const meta=locationStorageMeta(location),address=String(location.code||location.barcode||'—');
  if(meta.isPalletPlace)return {address,cell:'—',pallet:meta.palletPlace||address,type:'Палетоместо'};
  if(meta.isPallet)return {address,cell:'—',pallet:meta.pallet||address,type:'Палета'};
  const namedPallet=namedLocationPart(location.name||'','Палета');
  return {address,cell:address,pallet:namedPallet||'—',type:'Ячейка'};
}
function movementPlaceLine(location){const m=movementLocationInfo(location);return `${m.type}: ${m.address} · Палета/палетоместо: ${m.pallet}`;}
function stockText(stock){if(!stock?.length)return 'нет остатка';return stock.map(x=>{const base=`${x.location}${x.container?' · '+x.container:''} · ${fmtQty(x.quantity)} шт.`;if(x.reserved_quantity===undefined&&x.free_quantity===undefined)return base;const reserved=Number(x.reserved_quantity||0),pending=Number(x.accounting_pending_quantity||0),free=Number(x.free_quantity??Math.max(0,Number(x.quantity||0)-reserved-pending));return `${base} · резерв ${fmtQty(reserved)}${pending?` · ошибка учёта списания ${fmtQty(pending)}`:''} · свободно ${fmtQty(free)}`;}).join(' | ');}
function reservedText(rows){if(!rows?.length)return '—';return rows.map(x=>`${x.location||'—'}${x.container?' · '+x.container:''}`).join(' | ');}
function stockAccountingAlert(pending){const n=Math.max(0,Number(pending||0));return n?` · ошибка учёта списания ${fmtQty(n)}`:'';}
function stockMovementHtml(row,{compact=false}={}){
  // FC_K9R48_INVENTORY_MOVEMENT_BACKGROUND: movement history is audit-only. The
  // reserve/free truth becomes usable first; historical totals hydrate afterwards.
  if(row?._movement_deferred||row?.movement_deferred){
    const failed=String(row?._movement_error||'').trim();
    if(compact)return failed?'Движение по журналу не загрузилось':'Движение по журналу — по запросу';
    return `<small class="subline ${failed?'bad':''}"><b>Движение:</b> ${failed?`не загрузилось: ${esc(failed)}`:'по запросу — откройте товар или выгрузите Excel; общий список не ждёт журнал'}</small>`;
  }
  const received=Math.max(0,Number(row?.received_quantity??row?.warehouse_received_quantity??0)),shipped=Math.max(0,Number(row?.shipped_quantity??row?.warehouse_shipped_quantity??0)),adjIn=Math.max(0,Number(row?.adjustment_in_quantity??row?.warehouse_adjustment_in_quantity??0)),adjOut=Math.max(0,Number(row?.adjustment_out_quantity??row?.warehouse_adjustment_out_quantity??0)),pending=Math.max(0,Number(row?.accounting_pending_quantity??row?.warehouse_accounting_pending_quantity??0)),opening=Number(row?.opening_balance_estimate??row?.warehouse_opening_balance_estimate??0);
  const adjust=(adjIn||adjOut)?` · корректировки +${fmtQty(adjIn)} / −${fmtQty(adjOut)}`:'';
  const gap=Math.abs(opening)>0.0001?(opening>0?` · без записи в журнале +${fmtQty(opening)}`:` · журнал больше остатка на ${fmtQty(Math.abs(opening))}`):'';
  if(compact)return `Принято ${fmtQty(received)} · списано ${fmtQty(shipped)}${adjust}${gap}${pending?` · ошибка учёта списания ${fmtQty(pending)}`:''}`;
  return `<small class="subline"><b>Движение:</b> принято ${fmtQty(received)} · списано ${fmtQty(shipped)}${adjust}</small>${gap?`<small class="subline bad"><b>Расхождение истории:${esc(gap.replace(/^ · /,''))}</b> · текущий физический остаток и журнал движений не совпадают</small>`:''}${pending?`<small class="subline bad"><b>Удержано до подтверждения списания: ${fmtQty(pending)} шт.</b> · в продажу WB не входит</small>`:''}`;
}
// FC_K9R48_TRUTH_PENDING_TERMINAL_UI: after a timed-out/failed truth request the
// screen must say that the check stopped. It must never look like an endless active job.
function inventoryTruthPendingQtyHtml(r){
  const error=String(r?._truth_error||'').trim();
  if(error)return `<b>Свободно для WB: не рассчитано</b><small class="subline"><b>Физически: ${fmtQty(r.quantity||0)} шт.</b> · резерв не подменяется нулём</small><small class="subline bad"><b>Сверка остановлена:</b> ${esc(error)}. Нажмите «Обновить».</small>`;
  return `<b>Свободно для WB: сверяем…</b><small class="subline"><b>Физически: ${fmtQty(r.quantity||0)} шт.</b> · резерв по Новым/На сборке загружается</small><small class="subline warn">До завершения сверки FulfillCore не считает резерв равным нулю и не предлагает публиковать этот остаток в WB.</small>`;
}
function inventoryTruthPendingWbHtml(r,firstLocation,code){
  const error=String(r?._truth_error||'').trim();
  const button=`<button class="mini secondary" type="button" data-inventory-open="${esc(r.product_id)}" data-inventory-barcode="${esc(code)}">Где лежит</button>`;
  if(error)return `<div class="fbs-stock-state bad"><span>Сверка резерва не завершена. Публикация WB заблокирована для безопасности.</span><small class="subline bad">${esc(error)}</small><div class="button-row">${button}</div></div>`;
  return `<div class="fbs-stock-state warn"><span>Сверяем резерв и свободный остаток. Публикация WB временно недоступна, чтобы не продать лишнее.</span><small class="subline">Физически: ${fmtQty(r.quantity||0)} шт. · резерв не подменяется нулём</small><div class="button-row">${button}</div></div>`;
}
function firstPhoto(p){const x=(p?.photos||[])[0]||{};if(typeof x==='string')return x;return x.big||x.c516x688||x.square||x.tm||'';}

function applyWarehouseMenu(){
  const menu=$('warehouseSubmenu'),btn=$('warehouseMenuToggle');if(!menu||!btn)return;
  menu.classList.toggle('hidden',!state.warehouseMenuOpen);btn.classList.toggle('open',state.warehouseMenuOpen);
}
function renderReceivingClientContext(){
  if(!document.getElementById('receivingClientContextStyles')){const style=document.createElement('style');style.id='receivingClientContextStyles';style.textContent='.receiving-client-context{display:flex;justify-content:space-between;gap:14px;align-items:center;margin:0 0 16px;padding:13px 15px;border:1px solid #c7d7fe;border-radius:14px;background:#eef4ff;color:#253b73}.receiving-client-context>div{display:grid;gap:3px}.receiving-client-context small{color:#475467}.location-owner{font-weight:750;color:#344054}.location-owner.other{color:#b42318}.location-card.occupied-other{border-color:#fda29b;background:#fff6f5}@media(max-width:700px){.receiving-client-context{align-items:flex-start;flex-direction:column}}';document.head.appendChild(style);}
  const client=state.clients.find(x=>String(x.id)===String(state.clientId||'')),warehouse=state.warehouses.find(x=>String(x.id)===String(state.warehouseId||''));
  for(const pageName of ['receive','bulkimport','palletreceive']){const page=$(`page-${pageName}`);if(!page)continue;let banner=page.querySelector('[data-receiving-client-context]');if(!banner){banner=document.createElement('div');banner.className='receiving-client-context';banner.dataset.receivingClientContext='1';page.prepend(banner);}banner.innerHTML=`<div><b>Клиент приёмки: ${esc(client?.name||'не выбран')}</b><small>Товар, короба, история и остатки записываются только этому клиенту.</small></div><div><b>Физический склад: ${esc(warehouse?.name||'не выбран')}</b><small>Чтобы сменить клиента или склад, используйте выбор в верхней части программы.</small></div>`;}
}
function installAccountClientAccessTab(){
  const page=$('page-account');if(!page||page.dataset.r105ClientAccess==='1')return;
  page.dataset.r105ClientAccess='1';
  if(!document.getElementById('r105AccountTabsStyle')){const st=document.createElement('style');st.id='r105AccountTabsStyle';st.textContent='.account-subtabs{display:flex;gap:8px;margin:0 0 16px}.account-subtabs button.active{background:#4f46e5;color:#fff;border-color:#4f46e5}.account-client-panel{max-width:760px}.account-client-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.account-client-grid .wide{grid-column:1/-1}@media(max-width:700px){.account-client-grid{grid-template-columns:1fr}}';document.head.appendChild(st);}
  const integration=document.createElement('div');integration.dataset.accountView='integrations';
  while(page.firstChild)integration.appendChild(page.firstChild);
  const tabs=document.createElement('div');tabs.className='account-subtabs';tabs.innerHTML='<button type="button" class="secondary active" data-account-tab="integrations">API кабинеты</button><button type="button" class="secondary" data-account-tab="client">Доступ клиента</button>';
  const client=document.createElement('div');client.dataset.accountView='client';client.className='hidden';client.innerHTML=`<section class="panel account-client-panel"><div class="panel-head"><div><span class="kicker">ЛИЧНЫЙ КАБИНЕТ · КЛИЕНТСКИЙ ДОСТУП</span><h2>Вход клиента</h2><p>Отдельный логин выбранного клиента для просмотра его остатков и зарегистрированного брака. Это не относится к приёмке ПВЗ.</p></div></div><div class="result-card"><b data-account-client-name>Клиент: —</b><span>Клиент определяется общим переключателем FulfillCore сверху.</span></div><form id="accountClientAccessForm" class="account-client-grid mt18"><label>Логин клиента<input name="username" required minlength="3" autocomplete="off"></label><label>Новый пароль<input name="password" type="password" required minlength="8" autocomplete="new-password"></label><div class="wide button-row"><button class="primary" type="submit">Создать / обновить доступ</button><a class="secondary" href="/client/login" target="_blank" rel="noopener">Открыть вход клиента</a></div></form><p id="accountClientAccessMsg" class="msg"></p></section>`;
  page.append(tabs,integration,client);
  const selectTab=name=>{tabs.querySelectorAll('[data-account-tab]').forEach(b=>b.classList.toggle('active',b.dataset.accountTab===name));integration.classList.toggle('hidden',name!=='integrations');client.classList.toggle('hidden',name!=='client');fcR20Remember('fc_account_subtab',name);if(name==='client')updateAccountClientAccessContext();};
  tabs.addEventListener('click',e=>{const b=e.target.closest('[data-account-tab]');if(b)selectTab(b.dataset.accountTab);});
  client.querySelector('#accountClientAccessForm').addEventListener('submit',async e=>{e.preventDefault();const btn=e.submitter,msg=client.querySelector('#accountClientAccessMsg'),fd=new FormData(e.currentTarget);if(!state.clientId){msg.textContent='Сначала выберите клиента';msg.className='msg error';return;}btn.disabled=true;msg.textContent='Сохраняем доступ…';msg.className='msg';try{const r=await api('/pvz/client-access',{method:'POST',body:JSON.stringify({client_id:state.clientId,username:String(fd.get('username')||'').trim(),password:String(fd.get('password')||'')})});msg.innerHTML=`Доступ сохранён: <b>${esc(r.username)}</b>. <a href="/client/login" target="_blank" rel="noopener">Открыть вход клиента</a>`;msg.className='msg ok';e.currentTarget.elements.password.value='';}catch(err){msg.textContent=err.message;msg.className='msg error';}finally{btn.disabled=false;}});
  selectTab(localStorage.getItem('fc_account_subtab')==='client'?'client':'integrations');
}
function updateAccountClientAccessContext(){const host=document.querySelector('[data-account-client-name]');if(!host)return;const c=state.clients.find(x=>String(x.id)===String(state.clientId||''));host.textContent='Клиент: '+(c?.name||'не выбран');}
function go(page){
  if(!document.getElementById(`page-${page}`)) page='dashboard';
  state.activePage=page;fcR20Remember('fc_active_page',page);
  document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x.id===`page-${page}`));
  document.querySelectorAll('#mainNav [data-page]').forEach(x=>x.classList.toggle('active',x.dataset.page===page));
  $('pageTitle').textContent=titles[page]||page;
  if(['receive','palletreceive','inventory','locations','containers','storage','move','transit','defectstorage'].includes(page)){state.warehouseMenuOpen=true;fcR20Remember('fc_warehouse_menu_open','1');applyWarehouseMenu();}
  stopWbLiveMode();
  if(page==='inventory')loadInventoryCatalog().catch(e=>toast(e.message,true));
  if(page==='containers')loadContextData().catch(e=>toast(e.message,true));
  if(page==='storage')window.FCStorage?.refresh?.();
  if(page==='products'){restoreWbProductsCacheImmediately();loadWbProducts().catch(e=>toast(e.message,true));}
  if(page==='account'){installAccountClientAccessTab();updateAccountClientAccessContext();}
  if(['marketplaces','ozon','products','account'].includes(page)){
    loadMarketplaceCredentials().then(async()=>{
      if(page==='marketplaces'){
        document.querySelectorAll('#wbStatusTabs [data-wb-filter]').forEach(x=>x.classList.toggle('active',x.dataset.wbFilter===state.wbOrderFilter));
        // Draw the last known local/cached state immediately. External synchronization
        // continues in the background and must never blank the workspace while waiting.
        await loadWbWarehouses(false);
        restoreWbOrdersCacheImmediately();
        await Promise.allSettled([loadWbOrders(false),loadWbSupplies(false)]);
        startWbLiveMode();
        setTimeout(()=>$('wbPackProduct')?.focus(),80);
      }
      if(page==='products')loadWbProducts().catch(e=>toast(e.message,true));
      if(page==='ozon' && $('ozonCredentialSelect')?.value){await loadOzonWarehouses();await loadOzonPostings();}
    }).catch(e=>toast(e.message,true));
  }
}
document.querySelectorAll('[data-page]').forEach(b=>b.addEventListener('click',()=>go(b.dataset.page)));
document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>go(b.dataset.go)));
$('warehouseMenuToggle')?.addEventListener('click',()=>{state.warehouseMenuOpen=!state.warehouseMenuOpen;fcR20Remember('fc_warehouse_menu_open',state.warehouseMenuOpen?'1':'0');applyWarehouseMenu();});
applyWarehouseMenu();

async function bootstrap(){
  // API liveness must not depend on optional commercial/account endpoints. 0.9.4.10
  // used Promise.all(/health, /commercial/session), so a missing/disabled commercial
  // module painted a perfectly healthy API as «API недоступен».
  let h=null,me=null;
  try{
    h=await api('/health',{timeoutMs:5000});
    document.querySelector('.status-dot')?.classList.add('ok');
    $('apiStatus').textContent=`Система работает · ${h.version||''}`;
  }catch(error){
    document.querySelector('.status-dot')?.classList.remove('ok');
    $('apiStatus').textContent='API недоступен';
  }
  if(h){
    try{me=await api('/commercial/session',{timeoutMs:4000});}catch(_commercialError){
      try{const basic=await api('/auth/me',{timeoutMs:4000});me={username:basic.username||'Пользователь',super_admin:false};}catch(_authError){me=null;}
    }
    if(me){
      state.commercialSession=me;
      $('apiStatus').textContent=`Система работает · ${h.version||''}${me.tenant_name&&!me.super_admin?' · '+me.tenant_name:''}`;
      $('currentUser').textContent=me.username||'Пользователь';
      if(me.super_admin){
        const nav=document.getElementById('mainNav');
        if(nav&&!document.getElementById('commercialAdminLink')){
          const a=document.createElement('button');a.id='commercialAdminLink';a.type='button';a.textContent='Коммерция';a.onclick=()=>location.href='/commercial-admin';nav.appendChild(a);
        }
      }
    }
  }
  await loadContexts();
}
async function loadContexts({silent=false}={}){
  const cached=readContextMasterCache();window.FCStockRooms?.boot(cached);
  const [clientsResult,warehousesResult]=await Promise.allSettled([
    api('/masterdata/clients',{timeoutMs:6000}),
    api('/masterdata/warehouses',{timeoutMs:6000}),
  ]);
  const errors=[];
  if(clientsResult.status==='fulfilled'&&Array.isArray(clientsResult.value)){state.clients=clientsResult.value;}else{errors.push(clientsResult.reason||new Error('Не загрузились клиенты'));if(!state.clients.length)state.clients=cached.clients;}
  if(warehousesResult.status==='fulfilled'&&Array.isArray(warehousesResult.value)){state.warehouses=warehousesResult.value.filter(x=>x?.is_active!==false);}else{errors.push(warehousesResult.reason||new Error('Не загрузились склады'));if(!state.warehouses.length)state.warehouses=cached.warehouses;}
  // 0.9.4.11a: only apply tenant restrictions when the commercial session
  // explicitly returned restriction arrays. In single-profile builds /commercial/session
  // can be unavailable and bootstrap falls back to /auth/me; that fallback has no
  // allowed_* fields and must NEVER hide every client/warehouse.
  const hasCommercialScope=Boolean(
    state.commercialSession &&
    !state.commercialSession.super_admin &&
    (Array.isArray(state.commercialSession.allowed_client_ids) ||
     Array.isArray(state.commercialSession.allowed_warehouse_ids))
  );
  if(hasCommercialScope){
    const allowedClients=new Set((state.commercialSession.allowed_client_ids||[]).map(String));
    const allowedWarehouses=new Set((state.commercialSession.allowed_warehouse_ids||[]).map(String));
    state.clients=state.clients.filter(x=>allowedClients.has(String(x.id)));
    state.warehouses=state.warehouses.filter(x=>allowedWarehouses.has(String(x.id)));
  }
  if(state.clients.length||state.warehouses.length)saveContextMasterCache();

  const storedClient=localStorage.getItem('fc_client')||'';
  const storedWarehouse=localStorage.getItem('fc_warehouse')||'';
  let profile=null;
  if(!storedClient||!storedWarehouse){
    try{profile=await loadWorkstationProfile(state.wbPrintStation,3000);}catch(_){/* context must still open from local/cache data */}
  }
  const choose=(rows,...candidates)=>{
    for(const candidate of candidates){if(!candidate)continue;const found=rows.find(x=>sameContextId(x.id,candidate));if(found)return normalizeContextId(found.id);}
    return rows.length?normalizeContextId(rows[0].id):null;
  };
  state.clientId=choose(state.clients,storedClient,state.clientId,profile?.client_id);
  state.warehouseId=choose(state.warehouses,storedWarehouse,state.warehouseId,profile?.warehouse_id);
  if(state.clientId)fcR20Remember('fc_client',state.clientId);
  if(state.warehouseId)fcR20Remember('fc_warehouse',state.warehouseId);
  renderContextSelects();

  if(!state.clientId||!state.warehouseId){
    renderAll();
    if(errors.length&&!silent)toast('Клиент или склад временно не загрузился. FulfillCore повторит подключение автоматически.',true);
    clearTimeout(state.contextRetryTimer);state.contextRetryTimer=setTimeout(()=>loadContexts({silent:true}).catch(()=>{}),10000);
    return;
  }
  // FC_K9R23_INVENTORY_BOOT_PARALLEL: the stock page may begin its lightweight
  // first-paint request immediately.  It must not wait for locations, containers,
  // occupancy and every SKU reference request to finish before showing physical stock.
  const targetPage=state.activePage==='inventory'&&state.r65FbsOpen?'inventoryfbs':state.activePage||'dashboard';
  const contextPromise=loadContextData({quiet:silent});
  if(['inventory','inventoryfbs','mainstock','stockhome','fbspicks','locations','containers'].includes(targetPage)){
    go(targetPage);
    await contextPromise;
  }else{
    await contextPromise;
    go(targetPage);
  }
  scheduleWorkstationProfileSync();
  if(errors.length){
    if(!silent)toast('Часть справочников ответила с задержкой. Использованы последние сохранённые клиент и склад; идёт фоновое обновление.',true);
    clearTimeout(state.contextRetryTimer);state.contextRetryTimer=setTimeout(()=>loadContexts({silent:true}).catch(()=>{}),10000);
  }
}
function renderContextSelects(){
  const clientSelect=$('clientSelect'),warehouseSelect=$('warehouseSelect');
  if(clientSelect){clientSelect.innerHTML=state.clients.length?state.clients.map(x=>`<option value="${esc(normalizeContextId(x.id))}">${esc(x.name)}</option>`).join(''):'<option value="">Нет клиентов</option>';if(state.clientId&&state.clients.some(x=>sameContextId(x.id,state.clientId)))clientSelect.value=normalizeContextId(state.clientId);}
  if(warehouseSelect){warehouseSelect.innerHTML=state.warehouses.length?state.warehouses.map(x=>`<option value="${esc(normalizeContextId(x.id))}">${esc(x.name)}</option>`).join(''):'<option value="">Нет складов</option>';if(state.warehouseId&&state.warehouses.some(x=>sameContextId(x.id,state.warehouseId)))warehouseSelect.value=normalizeContextId(state.warehouseId);}
}
// FC_R130_FAST_CONTEXT_SNAPSHOT: address storage opens from the last local snapshot,
// then PostgreSQL refreshes in the background. This is display acceleration only;
// mutations still go to the server first and update this snapshot after confirmation.
function contextSnapshotStorageKey(key){return `fc_ctx_fast_v25:${key}`;}
function readFastContextSnapshot(key){
  try{
    const raw=localStorage.getItem(contextSnapshotStorageKey(key));if(!raw)return null;
    const snap=JSON.parse(raw),age=Date.now()-Number(snap?.saved_at||0);
    if(!snap||age<0||age>30*60*1000)return null;
    if(!Array.isArray(snap.locations)||!Array.isArray(snap.containers))return null;
    return snap;
  }catch{return null;}
}
function writeFastContextSnapshot(key){
  try{
    const snap={saved_at:Date.now(),locations:state.locations||[],containers:state.containers||[],locationOccupancy:state.locationOccupancy||{}};
    const raw=JSON.stringify(snap);if(raw.length<=3500000)localStorage.setItem(contextSnapshotStorageKey(key),raw);
  }catch(error){console.warn('FC_R130_CONTEXT_SNAPSHOT_WRITE',error);}
}
function renderAddressContextFast(){renderLocations();renderContainers();renderDashboard();validateAllHints();}
async function loadContextData({quiet=false,force=false}={}){
  // The MAIN screen owns a small local snapshot. Loading unrelated reference
  // lists and WB state here used to compete with it on every revision poll.
  if(state.activePage==='mainstock'&&!force&&window.FCStockRooms){
    state.r70ContextDirty=true;
    return window.FCStockRooms.refresh('MAIN');
  }
  if(!state.clientId || !state.warehouseId){state.locations=[];state.locationOccupancy={};state.skus=[];state.containers=[];state.inventory=[];state.fbsStockStatus={};state.inventoryWbControl={};state.wbLiveStock={};state.wbLiveStockSummary=null;state.wbLiveStockCheckedAt=null;state.wbLiveStockError='';state.operations=[];renderAll();return;}
  const key=`${normalizeContextId(state.clientId)}|${normalizeContextId(state.warehouseId)}`;
  if(!force&&state.contextDataKey===key&&Date.now()-Number(state.r70ContextLoadedAt||0)<750)return;
  if(state.contextLoadPromise&&state.contextLoadKey===key)return state.contextLoadPromise;
  if(state.contextDataKey!==key){
    // Never show another warehouse's data. A same-context browser snapshot may be
    // rendered immediately while the authoritative server refresh is already running.
    state.locations=[];state.locationOccupancy={};state.skus=[];state.containers=[];state.inventory=[];state.operations=[];state.inventoryCatalog=[];state.inventoryCatalogLoaded=false;state.inventoryMovementPromise=null;state.inventoryMovementContextKey='';state.fbsStockStatus={};state.inventoryWbControl={};state.wbLiveStock={};state.wbLiveStockSummary=null;state.wbLiveStockCheckedAt=null;state.wbLiveStockError='';state.bulkReceivingHistory=[];state.bulkReceivingHistoryLoaded=false;state.bulkReceivingHistoryLoading=false;state.bulkReceivingHistoryContextKey='';const __boxPanel=$('containerDetailPanel');if(__boxPanel){__boxPanel.classList.add('hidden');__boxPanel.innerHTML='';delete __boxPanel.dataset.containerId;}
    const snap=readFastContextSnapshot(key);if(snap){state.locations=snap.locations;state.containers=snap.containers;state.locationOccupancy=snap.locationOccupancy||{};state.r130ContextSnapshotAt=Number(snap.saved_at||0);}
    state.contextDataKey=key;renderAll();
  }
  const seq=++state.contextLoadSeq;state.contextLoadKey=key;
  state.contextLoadPromise=(async()=>{
    const qClient=encodeURIComponent(state.clientId),qWh=encodeURIComponent(state.warehouseId);
    const apiList=value=>Array.isArray(value)?value:Array.isArray(value?.items)?value.items:Array.isArray(value?.rows)?value.rows:Array.isArray(value?.locations)?value.locations:[];
    const alive=()=>seq===state.contextLoadSeq&&key===`${normalizeContextId(state.clientId)}|${normalizeContextId(state.warehouseId)}`;
    // Progressive rendering: locations/boxes appear as soon as each small directory
    // arrives instead of waiting for SKU, inventory and occupancy together.
    const specs=[
      ['locations',api(`/r145f25/locations?client_id=${qClient}&warehouse_id=${qWh}`,{timeoutMs:8000}),value=>{state.locations=apiList(value);renderAddressContextFast();}],
      ['skus',api(`/masterdata/skus?client_id=${qClient}`,{timeoutMs:8000}),value=>{state.skus=apiList(value);validateAllHints();}],
      ['containers',api(`/masterdata/containers?client_id=${qClient}&warehouse_id=${qWh}`,{timeoutMs:8000}),value=>{state.containers=apiList(value);renderAddressContextFast();}],
      ['inventory',api(`/inventory?client_id=${qClient}&warehouse_id=${qWh}`,{timeoutMs:8000}),value=>{state.inventory=apiList(value);renderDashboard();renderInventory();renderLocations();renderContainers();}],
      ['occupancy',api(`/layout/location-occupancy?warehouse_id=${qWh}&client_id=${qClient}`,{timeoutMs:8000}),value=>{state.locationOccupancy=Object.fromEntries(apiList(value?.locations??value).map(x=>[String(x.location_id),x]));renderLocations();renderContainers();}],
    ];
    const wrapped=specs.map(([name,promise,apply])=>Promise.resolve(promise).then(value=>{if(alive())apply(value);return {name,value};}));
    const critical=await Promise.allSettled(wrapped);
    if(!alive())return;
    const failures=critical.filter(x=>x.status==='rejected').map(x=>x.reason);
    const successNames=new Set(critical.filter(x=>x.status==='fulfilled').map(x=>x.value.name));
    if(successNames.has('locations')&&successNames.has('containers'))writeFastContextSnapshot(key);
    if(!failures.length)state.r70ContextLoadedAt=Date.now();
    renderAll();

    // Operations history and marketplace credentials are not allowed to hold up scanning.
    Promise.allSettled([
      api(`/operations?client_id=${qClient}&warehouse_id=${qWh}&limit=300`,{timeoutMs:6000}),
      loadMarketplaceCredentials(false),
    ]).then(results=>{
      if(seq!==state.contextLoadSeq)return;
      if(results[0].status==='fulfilled'){state.operations=apiList(results[0].value);renderDashboard();renderOperations();}
    });

    if(failures.length){
      if(force)throw new Error('Не удалось получить актуальные складские данные. '+(failures[0]?.message||''));
      if(!quiet)toast('Часть складских данных отвечает медленно. Уже показаны последние сохранённые адреса; обновление продолжается в фоне.',true);
      clearTimeout(state.contextRetryTimer);state.contextRetryTimer=setTimeout(()=>loadContextData({quiet:true}).catch(()=>{}),10000);
    }
  })().finally(()=>{if(seq===state.contextLoadSeq&&state.contextLoadKey===key){state.contextLoadPromise=null;state.contextLoadKey='';}});
  return state.contextLoadPromise;
}
function renderAll(){renderDashboard();renderInventory();renderLocations();renderContainers();renderOperations();renderReceivingClientContext();renderManualReceivingDraft();validateAllHints();queueTopTableScrolls();}
function renderDashboard(){
  $('statQty').textContent=fmtQty(state.inventory.reduce((s,b)=>s+Number(b.quantity),0));
  $('statSku').textContent=new Set(state.inventory.filter(b=>Number(b.quantity)>0).map(b=>b.sku_id)).size;
  $('statLocations').textContent=activeLocations().length;$('statContainers').textContent=activeContainers().length;
  const ops=state.operations.slice(0,6);
  $('recentOps').innerHTML=ops.length?ops.map(o=>`<div class="activity"><span><b>${esc(opName(o.operation_type))}</b><small>${esc(o.reference||o.performed_by||'Без комментария')}</small></span><small>${new Date(o.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})}</small></div>`).join(''):'Операций пока нет';
}
async function loadInventoryCatalog(options={}){
  refreshGlobalSalesStop();
  const force=options===true||Boolean(options?.force);
  if(!state.clientId||!state.warehouseId){state.inventoryCatalogLoading=false;state.inventoryCatalog=[];state.inventoryMovementPromise=null;state.inventoryMovementContextKey='';state.fbsStockStatus={};state.inventoryWbControl={};state.wbLiveStock={};state.wbLiveStockSummary=null;state.wbLiveStockCheckedAt=null;state.wbLiveStockError='';state.inventoryCatalogLoaded=true;state.inventoryCatalogContextKey='';renderInventory();return;}
  const selectedWbWarehouse=Number(state.wbWarehouseFilter||0)||null;
  const contextKey=`${state.clientId}|${state.warehouseId}|${selectedWbWarehouse||''}`;
  // FC_K9R4_INVENTORY_SINGLE_FLIGHT: one catalog truth read per workstation/context.
  // A second click/navigation reuses the screen already loading instead of launching
  // another long synchronous server query behind the first one.
  if(!force&&state.inventoryCatalogLoading&&state.inventoryCatalogContextKey===contextKey){renderInventory();return;}
  const catalogSequence=state.r70CatalogSequence=(state.r70CatalogSequence||0)+1;
  state.inventoryCatalogLoading=true;
  // Inventory truth changes every time NEW/PICKING changes. A long UI cache made the
  // inventory page show reserve=0 while the location screen already showed the fresh
  // order floor. Ten seconds is enough to avoid duplicate clicks without presenting a
  // minute-old reserve as current warehouse truth.
  const fresh=state.inventoryCatalogLoaded&&state.inventoryCatalogContextKey===contextKey&&(Date.now()-Number(state.inventoryCatalogLoadedAt||0)<10000);
  if(fresh&&!force){state.inventoryCatalogLoading=false;renderInventory();return;}

  const hadAuthoritative=state.inventoryCatalogContextKey===contextKey&&(state.inventoryCatalog||[]).some(row=>!row._truth_pending);
  let truthFinished=false;
  if(hadAuthoritative){
    state.inventoryCatalog=(state.inventoryCatalog||[]).map(row=>({...row,_truth_refreshing:true}));
  }else{
    // Physical InventoryBalance can be shown immediately, but it must NEVER masquerade
    // as reserve=0/free=physical before the canonical NEW/PICKING truth has loaded.
    state.inventoryCatalog=mergeLocalInventoryRows([]).map(row=>({...row,_truth_pending:true,_truth_refreshing:true}));
    state.inventoryCatalogProducts=state.inventoryCatalog.length;
  }
  state.inventoryCatalogLoaded=true;
  state.inventoryCatalogContextKey=contextKey;
  renderInventory();

  // FC_K9R23_INVENTORY_QUICK_FIRST_PAINT: one simple joined local-stock query
  // races the canonical WB-card/reserve projection.  Quick rows are explicitly marked
  // as truth-pending, so reserve/free-to-sell is never guessed as zero while loading.
  if(!hadAuthoritative){
    const quickQ=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId});
    api(`/inventory/quick?${quickQ.toString()}`,{timeoutMs:5000}).then(quick=>{
      if(catalogSequence!==state.r70CatalogSequence||truthFinished||contextKey!==`${state.clientId}|${state.warehouseId}|${Number(state.wbWarehouseFilter||0)||''}`)return;
      const quickRows=(quick?.rows||[]).map(row=>({...row,_quick_local:true,_truth_pending:true,_truth_refreshing:true}));
      if(quickRows.length){
        state.inventoryCatalog=quickRows;
        state.inventoryCatalogProducts=Number(quick?.products||quickRows.length);
        state.inventoryCatalogLoaded=true;
        renderInventory();
      }
    }).catch(()=>{});
  }

  const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId,limit:'5000'});
  if(selectedWbWarehouse)q.set('wb_warehouse_id',String(selectedWbWarehouse));
  const catalogPath=`/inventory/catalog?${q.toString()}`;
  const fetchTruth=()=>api(catalogPath,{timeoutMs:45000});
  const fbsPromise=api(`/receiving/fbs-status?client_id=${encodeURIComponent(state.clientId)}&warehouse_id=${encodeURIComponent(state.warehouseId)}`,{timeoutMs:30000}).catch(()=>({rows:[]}));

  try{
    const catalogResult=await fetchTruth();
    truthFinished=true;
    if(catalogSequence!==state.r70CatalogSequence)return;
    if(contextKey!==`${state.clientId}|${state.warehouseId}|${Number(state.wbWarehouseFilter||0)||''}`){state.inventoryCatalogLoading=false;return;}
    const authoritativeRows=(state.inventory||[]).length?mergeLocalInventoryRows(catalogResult.rows||[]):(catalogResult.rows||[]);
    state.inventoryCatalog=authoritativeRows.map(row=>({...row,_quick_local:false,_truth_pending:false,_truth_refreshing:false,_truth_error:'',_movement_deferred:Boolean(row.movement_deferred),_movement_error:''}));
    state.inventoryCatalogProducts=Number(catalogResult.products||0);
    state.inventoryCatalogLoaded=true;
    state.inventoryCatalogLoadedAt=Date.now();
    state.inventoryCatalogLoading=false;
    renderInventory();
    // FC_K9R48_INVENTORY_TRUTH_FAST_SPLIT_UI: reserve/free truth is already safe and
    // actionable here. Historical movement is loaded only on demand (detail/Excel),
    // so a large audit ledger cannot compete with WB/order workers in the background.
  }catch(err){
    truthFinished=true;
    if(catalogSequence!==state.r70CatalogSequence)return;
    if(contextKey===`${state.clientId}|${state.warehouseId}|${Number(state.wbWarehouseFilter||0)||''}`){
      if(hadAuthoritative){
        state.inventoryCatalog=(state.inventoryCatalog||[]).map(row=>({...row,_truth_refreshing:false,_truth_stale:true,_truth_error:err.message||'Не удалось обновить резерв'}));
      }else{
        state.inventoryCatalog=(state.inventoryCatalog||[]).map(row=>({...row,_truth_pending:true,_truth_refreshing:false,_truth_error:err.message||'Не удалось загрузить резерв'}));
      }
      state.inventoryCatalogLoadedAt=0;
      state.inventoryCatalogLoading=false;
      renderInventory();
    }
    throw new Error(err?.name==='TimeoutError'?'Сервер не успел закончить сверку остатков. Повторный запрос автоматически не запускается, чтобы не перегружать базу. Через несколько секунд нажмите «Обновить».':'Не удалось получить актуальный резерв. Физический остаток показан, свободный остаток и резерв не считаются до успешной сверки. Повторите обновление.');
  }

  Promise.resolve(fbsPromise).then(r=>{
    if(catalogSequence!==state.r70CatalogSequence)return;
    if(contextKey!==`${state.clientId}|${state.warehouseId}|${Number(state.wbWarehouseFilter||0)||''}`)return;
    const rows=r?.rows||[];
    state.fbsStockStatus=Object.fromEntries(rows.map(x=>[String(x.barcode||''),x]));
    renderInventory();
  }).catch(()=>{});
  if(!options?.localOnly)setTimeout(()=>{if(catalogSequence!==state.r70CatalogSequence||contextKey!==`${state.clientId}|${state.warehouseId}|${Number(state.wbWarehouseFilter||0)||''}`)return;const context=inventoryRequestContext();refreshInventoryControlProjection().catch(()=>{});refreshInventoryWbActual({quiet:true}).catch(()=>{});ensureInventoryOrderReserveFresh(context).catch(()=>{});},0);
}

let inventoryOrderReserveSyncInFlight=false;
let inventoryOrderReserveSyncAt=0;
async function ensureInventoryOrderReserveFresh(context){
  const cred=wbCredential(),now=Date.now();
  if(!cred||!context?.wb_warehouse_id||inventoryOrderReserveSyncInFlight||now-inventoryOrderReserveSyncAt<30000)return;
  inventoryOrderReserveSyncInFlight=true;inventoryOrderReserveSyncAt=now;
  try{
    const result=await api('/marketplaces/wb/orders/sync',{method:'POST',body:JSON.stringify({credential_id:cred,warehouse_id:context.warehouse_id,wb_warehouse_id:context.wb_warehouse_id,days:3,force:false}),timeoutMs:60000});
    assertInventoryContext(context);
    const reserve=result?.immediate_fbs_reservation||{};
    if(Number(reserve.created||0)>0||Number(reserve.shortage||0)>0||Number(result?.backfilled||0)>0||Number(result?.status_updated||0)>0){
      state.inventoryCatalogLoadedAt=0;
      setTimeout(()=>loadInventoryCatalog({force:true}).catch(()=>{}),150);
    }
  }catch(err){if(Number(err?.status||0)!==409)console.warn('Inventory WB order reserve sync:',err);}
  finally{inventoryOrderReserveSyncInFlight=false;}
}

async function refreshInventoryMovementBackground(contextKey=state.inventoryCatalogContextKey){
  const currentKey=`${state.clientId}|${state.warehouseId}|${Number(state.wbWarehouseFilter||0)||''}`;
  if(!contextKey||contextKey!==currentKey)return null;
  const ids=Array.from(new Set((state.inventoryCatalog||[]).flatMap(r=>r.sku_ids||[r.sku_id]).map(x=>String(x||'').trim()).filter(Boolean)));
  if(!ids.length){for(const row of state.inventoryCatalog||[]){row._movement_deferred=false;row.movement_deferred=false;}renderInventory();return {rows:[]};}
  if(state.inventoryMovementPromise&&state.inventoryMovementContextKey===contextKey)return state.inventoryMovementPromise;
  const movementSequence=state.r70CatalogSequence;
  state.inventoryMovementContextKey=contextKey;
  state.inventoryMovementPromise=(async()=>{
    const result=await api('/inventory/movement-summary',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,sku_ids:ids}),timeoutMs:120000});
    if(movementSequence!==state.r70CatalogSequence||contextKey!==`${state.clientId}|${state.warehouseId}|${Number(state.wbWarehouseFilter||0)||''}`)return result;
    const bySku=new Map((result?.rows||[]).map(x=>[String(x.sku_id||''),x]));
    state.inventoryCatalog=(state.inventoryCatalog||[]).map(row=>{
      const rowIds=Array.from(new Set((row.sku_ids||[row.sku_id]).map(x=>String(x||'').trim()).filter(Boolean)));
      if(!rowIds.length)return {...row,_movement_deferred:false,movement_deferred:false,_movement_error:''};
      let received=0,shipped=0,adjIn=0,adjOut=0,inbound=0,outbound=0;
      for(const sid of rowIds){const m=bySku.get(sid);if(!m)continue;received+=Number(m.received_quantity||0);shipped+=Number(m.shipped_quantity||0);adjIn+=Number(m.adjustment_in_quantity||0);adjOut+=Number(m.adjustment_out_quantity||0);inbound+=Number(m.recorded_inbound_quantity||0);outbound+=Number(m.recorded_outbound_quantity||0);}
      return {...row,_movement_deferred:false,movement_deferred:false,_movement_error:'',received_quantity:received,shipped_quantity:shipped,adjustment_in_quantity:adjIn,adjustment_out_quantity:adjOut,recorded_inbound_quantity:inbound,recorded_outbound_quantity:outbound,opening_balance_estimate:Number(row.quantity||0)-(inbound-outbound)};
    });
    renderInventory();
    return result;
  })().catch(err=>{
    if(movementSequence===state.r70CatalogSequence&&contextKey===`${state.clientId}|${state.warehouseId}|${Number(state.wbWarehouseFilter||0)||''}`){state.inventoryCatalog=(state.inventoryCatalog||[]).map(row=>({...row,_movement_deferred:true,movement_deferred:true,_movement_error:err.message||'история движений отвечает медленно'}));renderInventory();}
    throw err;
  }).finally(()=>{if(movementSequence===state.r70CatalogSequence&&state.inventoryMovementContextKey===contextKey){state.inventoryMovementPromise=null;state.inventoryMovementContextKey='';}});
  return state.inventoryMovementPromise;
}
let inventoryWbDriftRepairInFlight=false;
let inventoryWbDriftRetryTimer=null;
async function repairInventoryWbDrift(context){
  if(inventoryWbDriftRepairInFlight||!context?.wb_warehouse_id)return {queued:0,rows:[]};
  const observed={};
  for(const row of state.inventoryCatalog||[]){
    if(row?._truth_pending)continue;
    const code=String(row?.barcode||'').trim(),actual=wbActualQuantity(code),target=wbTargetQuantity(code);
    // FC_K9R31_UI_CHRT_DRIFT: only repair when the backend has supplied a canonical
    // chrt-level target. Never compare a WB size amount to one barcode by accident.
    if(code&&actual!==null&&target!==null&&Math.round(actual)!==Math.round(target))observed[code]=Math.round(actual);
  }
  if(!Object.keys(observed).length)return {queued:0,rows:[]};
  inventoryWbDriftRepairInFlight=true;
  try{
    const result=await api('/receiving/wb-drift-repair',{method:'POST',body:JSON.stringify({client_id:context.client_id,warehouse_id:context.warehouse_id,wb_warehouse_id:context.wb_warehouse_id,observed}),timeoutMs:30000});
    assertInventoryContext(context);
    if(Number(result?.queued||0)>0){
      const codes=(result.rows||[]).filter(x=>x.queued).map(x=>String(x.barcode||'')).filter(Boolean);
      setTimeout(()=>refreshInventoryControlProjection(codes,{render:true,context}).catch(()=>{}),250);
    }
    if(Number(result?.queued||0)>0&&!inventoryWbDriftRetryTimer){
      // One delayed read confirms what WB actually accepted and clears red rows without
      // creating a 20-second retry loop. Upward rows can wait for the next NEW-order poll.
      inventoryWbDriftRetryTimer=setTimeout(()=>{
        inventoryWbDriftRetryTimer=null;
        if(inventoryContextCurrent(context))refreshInventoryWbActual({quiet:true,repair:false}).catch(()=>{});
      },25000);
    }
    return result;
  }finally{inventoryWbDriftRepairInFlight=false;}
}

async function refreshInventoryWbActual({quiet=false,repair=true}={}){
  await ensureContext();const context=inventoryRequestContext();
  const focused=Boolean($('inventorySearch')?.value.trim());
  const sourceRows=focused?inventoryFilteredRows():(state.inventoryCatalog||[]);
  const codes=Array.from(new Set(sourceRows.flatMap(r=>[r.barcode,...(r.barcodes||[])]).map(x=>String(x||'').trim()).filter(Boolean))); // FC_K9R44_FOCUSED_WB_RECONCILE
  if(!codes.length){state.wbLiveStock={};state.wbLiveStockSummary=null;state.wbLiveStockCheckedAt=new Date().toISOString();state.wbLiveStockError='';renderInventory();return {rows:[]};}
  const ui=!quiet&&codes.length>80?inventoryWbBulkProgressCard('PUBLISH_WB',codes.length,0):null;
  if(ui)inventoryWbBulkProgressRender(ui,{stage:'ACTUAL',percent:30,message:`Читаем WB факт пакетно: ${fmtQty(codes.length)} ШК…`,counts:'Большой список сверяется по chrtId пакетами, не по одной строке'});
  if(!quiet)toast(`Сверяем фактические остатки WB: ${codes.length} ШК…`);
  try{
    const r=await refreshInventoryWbActualCodes(codes,{context,updateSummary:true});assertInventoryContext(context);renderInventory();
    // FC_K9R27_WB_STOCK_AUTO_CONVERGE: queue both directions from fresh canonical
    // truth. The durable worker delays every upward write until a NEW-order poll has
    // completed AFTER the target was queued; downward safety corrections publish now.
    if(repair)repairInventoryWbDrift(context).catch(err=>console.warn('WB stock drift repair:',err));
    if(ui){const rows=r?.summary?.rows||[],matched=rows.filter(x=>x.target!==null&&Number(x.amount)===Number(x.target)).length,mismatch=rows.filter(x=>x.target!==null&&Number(x.amount)!==Number(x.target)).length,missing=Number(r?.summary?.missing_barcodes||0);inventoryWbBulkProgressRender(ui,{stage:mismatch||missing?'ATTENTION':'DONE',percent:100,message:`WB факт получен: совпало ${fmtQty(matched)} chrtId · расхождений ${fmtQty(mismatch)}.`,counts:`без chrtId ${fmtQty(missing)}${repair&&mismatch?' · расхождения автоматически поставлены на коррекцию':''}`});inventoryWbBulkProgressFinish(ui,{error:Boolean(mismatch||missing),delayMs:10000});}
    if(!quiet)toast(`WB сверён: ${r.rows?.length||0} поз.${r.missing?.length?` · без chrtId: ${r.missing.length}`:''}`,Boolean(r.missing?.length));return r;
  }catch(err){if(ui){inventoryWbBulkProgressRender(ui,{stage:'ERROR',percent:0,message:`Не удалось сверить WB: ${err.message}`});inventoryWbBulkProgressFinish(ui,{error:true});}if(inventoryContextCurrent(context)){state.wbLiveStockError=err.message||'Ошибка сверки WB';renderInventory();if(!quiet)toast(`Не удалось сверить WB: ${err.message}`,true);}throw err;}
}
function inventoryWhereText(row){
  if(!row.locations?.length)return '—';
  if(row._truth_pending)return row.locations.map(x=>`${x.container?`Короб ${x.container} · `:''}${x.location} · физически ${fmtQty(Number(x.quantity||0))} · резерв/свободно: сверяем…`).join(' | ');
  return row.locations.map(x=>{const physical=Number(x.quantity||0),reserved=Number(x.reserved_quantity||0),external=Number(x.external_pick_reserved_quantity||0),pending=Number(x.accounting_pending_quantity||0),storage=Number(x.storage_only_quantity||0),free=Number(x.free_quantity??Math.max(0,physical-storage-reserved-external-pending));return `${x.container?`Короб ${x.container} · `:''}${x.location} · физически ${fmtQty(physical)} · резерв ${fmtQty(reserved)}${external?` · старое удержание r37 ${fmtQty(external)}`:''}${storage?` · не для WB FBS ${fmtQty(storage)}`:''}${pending?` · ошибка учёта списания ${fmtQty(pending)}`:''} · свободно для WB ${fmtQty(free)}`;}).join(' | ');
}
function inventoryLocationsHtml(row){
  // FC_K9R62_INVENTORY_PLACES_POPUP: keep the stock table simple. Exact cells/boxes
  // are shown in the existing product modal, where the operator can then open a cell.
  const locations=(row.locations||[]).filter(x=>Number(x.quantity||0)>0);
  const total=locations.reduce((sum,x)=>sum+Number(x.quantity||0),0);
  if(!locations.length)return `<button class="mini secondary" type="button" data-inventory-open="${esc(row.product_id||'')}" data-inventory-barcode="${esc(row.barcode||'')}">Посмотреть места</button><small class="subline">Сейчас остаток 0</small>`;
  const cells=new Set(locations.map(x=>String(x.location||'')).filter(Boolean)).size,boxes=new Set(locations.map(x=>String(x.container||'')).filter(Boolean)).size;
  return `<div class="inventory-place-popup-summary"><b>${fmtQty(total)} шт. · ${locations.length} ${locations.length===1?'место':'мест'}</b><small>${cells} ячеек${boxes?` · ${boxes} коробов`:''}</small><button class="mini secondary" type="button" data-inventory-open="${esc(row.product_id||'')}" data-inventory-barcode="${esc(row.barcode||'')}">Показать где лежит</button></div>`;
}

function normalizeReceivingBarcode(value){
  const raw=String(value||'').trim().replace(/\s+/g,'');if(/^\d+$/.test(raw)&&raw.length%2===0){const half=raw.length/2;if(half>=8&&half<=14&&raw.slice(0,half)===raw.slice(half))return {barcode:raw.slice(0,half),duplicatedFrom:raw};}return {barcode:raw,duplicatedFrom:null};
}
function mergeLocalInventoryRows(catalogRows){
  // 0.9.4.15g5a: preserve address/container reservation data returned by the
  // catalog API while rebuilding physical quantities from the lightweight balance list.
  const bucketReserve=new Map();
  for(const source of (catalogRows||[])){
    const aliases=Array.from(new Set([source.barcode,...(source.barcodes||[])].map(code=>normalizeReceivingBarcode(code).barcode).filter(Boolean)));
    for(const place of (source.locations||[])){
      for(const code of aliases){
        bucketReserve.set(`${code}|${place.location_id||''}|${place.container_id||''}`,{reserved:Number(place.reserved_quantity||0),external:Number(place.external_pick_reserved_quantity||0),pending:Number(place.accounting_pending_quantity||0),storage:Number(place.storage_only_quantity||0),free:Number(place.free_quantity??Math.max(0,Number(place.quantity||0)-Number(place.storage_only_quantity||0)-Number(place.reserved_quantity||0)-Number(place.external_pick_reserved_quantity||0)-Number(place.accounting_pending_quantity||0)))});
      }
    }
  }
  // 0.8.0:
  // 1) Physical InventoryBalance is the only source of truth for local quantity.
  // 2) Every barcode listed on one WB card/size is an alias of that same stock row.
  // 3) Receiving by ANY valid WB barcode must never be blocked by another barcode
  //    being marked as the card's "primary" barcode.
  const rows=(catalogRows||[]).map(row=>{
    const aliases=Array.from(new Set(
      [row.barcode,...(row.barcodes||[])]
        .map(code=>normalizeReceivingBarcode(code).barcode)
        .filter(Boolean)
    ));
    return {...row,barcodes:aliases,quantity:0,locations:[],local_barcode_quantities:{}};
  });

  const byBarcode=new Map();
  for(const row of rows){
    for(const code of row.barcodes||[]){
      if(!byBarcode.has(code))byBarcode.set(code,row);
    }
    const primary=normalizeReceivingBarcode(row.barcode).barcode;
    if(primary&&!byBarcode.has(primary))byBarcode.set(primary,row);
  }

  const localOnly=new Map();

  for(const balance of (state.inventory||[])){
    const qty=Number(balance.quantity||0);
    if(!(qty>0))continue;
    const sku=mapBy(state.skus,balance.sku_id);
    if(!sku)continue;

    const raw=String(sku.barcode||sku.sku_code||'');
    const normalized=normalizeReceivingBarcode(raw).barcode;
    if(!normalized)continue;

    const bucket= bucketReserve.get(`${normalized}|${balance.location_id||''}|${balance.container_id||''}`) || {reserved:0,external:0,pending:0,storage:0,free:qty};
    const place={
      location_id:balance.location_id,
      location:locationLabel(balance.location_id),
      container_id:balance.container_id||null,
      container:balance.container_id?containerLabel(balance.container_id):null,
      quantity:qty,
      reserved_quantity:Number(bucket.reserved||0),
      external_pick_reserved_quantity:Number(bucket.external||0),
      accounting_pending_quantity:Number(bucket.pending||0),
      storage_only_quantity:Math.max(0,Math.min(qty,Number(bucket.storage||0))),
      free_quantity:Math.max(0,Math.min(qty,Number(bucket.free??qty))),
      barcode:normalized,
    };

    const card=byBarcode.get(normalized);
    if(card){
      card.quantity=Number(card.quantity||0)+qty;
      card.locations.push(place);
      card.local_barcode_quantities[normalized]=Number(card.local_barcode_quantities[normalized]||0)+qty;
      if(!(card.barcodes||[]).includes(normalized))card.barcodes.push(normalized);
      if(normalizeReceivingBarcode(raw).duplicatedFrom)card.has_legacy_barcode_error=true;
      continue;
    }

    if(!localOnly.has(normalized)){
      localOnly.set(normalized,{
        product_id:`local:${sku.id}`,
        local_only:true,
        _truth_pending:true,
        nm_id:null,
        article:sku.article||sku.sku_code||normalized,
        title:sku.name||sku.article||sku.sku_code||`ШК ${normalized}`,
        brand:null,
        size:sku.size||null,
        barcode:normalized,
        barcodes:[normalized],
        quantity:0,
        locations:[],
        photo:null,
        local_barcode_quantities:{},
      });
    }
    const row=localOnly.get(normalized);
    row.quantity+=qty;
    row.locations.push(place);
    row.local_barcode_quantities[normalized]=Number(row.local_barcode_quantities[normalized]||0)+qty;
  }

  // Prefer an alias that actually has local stock for actions/search display.
  for(const row of rows){
    if(Number(row.quantity||0)>0){
      const activeAlias=(row.barcodes||[]).find(code=>Number(row.local_barcode_quantities?.[code]||0)>0);
      if(activeAlias)row.barcode=activeAlias;
    }else if(!row.barcode && row.barcodes?.length){
      row.barcode=row.barcodes[0];
    }
  }

  return [...rows,...localOnly.values()];
}
function fbsPublishedQuantity(barcode){
  const marker=state.fbsStockStatus?.[String(barcode||'')];
  return marker?.status==='PUBLISHED'?Math.max(0,Number(marker.published_amount||0)):0;
}
function wbActualQuantity(barcode){
  const row=state.wbLiveStock?.[String(barcode||'')];
  return row&&Number.isFinite(Number(row.amount))?Math.max(0,Number(row.amount)):null;
}
function wbTargetQuantity(barcode){
  const row=state.wbLiveStock?.[String(barcode||'')],raw=row?.target;
  if(raw===null||raw===undefined||raw==='')return null;
  const value=Number(raw);return Number.isFinite(value)?Math.max(0,value):null;
}
function wbChrtId(barcode){const row=state.wbLiveStock?.[String(barcode||'')],v=Number(row?.chrt_id);return Number.isFinite(v)&&v>0?v:null;}
function inventoryControlRow(barcode){return state.inventoryWbControl?.[String(barcode||'')]||null;}
function inventoryDesiredQuantity(barcode){const row=inventoryControlRow(barcode),raw=row?.desired_amount,reason=String(row?.queue_reason||'').toUpperCase();if(raw===null||raw===undefined||raw==='')return null;const value=Number(raw);if(reason.includes('RESOLVE_CHRT')&&Number.isFinite(value)&&value===0)return null;return Number.isFinite(value)?Math.max(0,value):null;} // FC_K9R44_ZERO_RESOLVE_SENTINEL_NOT_A_TARGET
function inventoryPublishedControlQuantity(barcode){const row=inventoryControlRow(barcode),raw=row?.published_amount;if(raw===null||raw===undefined||raw==='')return null;const value=Number(raw);return Number.isFinite(value)?Math.max(0,value):null;}
function inventoryQueueLabel(status){const s=String(status||'').toUpperCase();return s==='PENDING'?'в очереди':s==='PROCESSING'?'отправляется':s==='FAILED'?'ошибка':s==='SUPERSEDED'?'заменено новой командой':s==='DONE'?'отправлено':'';}
function fbsStockPresentation(barcode,localQuantity,availableToSell=0){
  const code=String(barcode||''),marker=state.fbsStockStatus?.[code],control=inventoryControlRow(code),actual=wbActualQuantity(code),rowFree=Math.max(0,Math.round(Number(availableToSell||0))),chrtTarget=wbTargetQuantity(code),chrtId=wbChrtId(code),liveRow=state.wbLiveStock?.[code];
  if(!code)return {className:'unknown',text:'WB: — · нет ШК',button:'',placed:false,published:null,actual:null};
  if(liveRow?.mapping_error)return {className:'error',text:`WB: ${liveRow.mapping_error}`,button:'Обновить',placed:false,published:null,actual:null};
  const queuedTarget=inventoryDesiredQuantity(code),controlPublished=inventoryPublishedControlQuantity(code),markerTarget=marker?.status==='PUBLISHED'?Math.max(0,Number(marker.published_amount||0)):null,target=chrtTarget??queuedTarget??controlPublished??markerTarget;
  const queue=String(control?.queue_status||'').toUpperCase(),queueLabel=inventoryQueueLabel(queue),queueError=control?.queue_error||control?.publication_error||'';
  const sizeLabel=chrtId?`chrtId ${chrtId}`:'размер WB';
  if(queue==='FAILED'||String(control?.publication_status||'').toUpperCase()==='ERROR')return {className:'error',text:`WB факт: ${actual===null?'не сверён':fmtQty(actual)+' шт.'} · цель ${target===null?'—':fmtQty(target)+' шт.'} (${sizeLabel}) · ошибка публикации${queueError?' · '+queueError:''}`,button:'Повторить',placed:false,published:target,actual};
  const waitingNewSync=queue==='PENDING'&&String(queueError||'').startsWith('WAITING_NEW_SYNC:');
  if(actual!==null){
    const details=[`цель ${target===null?'—':fmtQty(target)+' шт.'} (${sizeLabel})`];if(queueLabel)details.push(queueLabel);if(controlPublished!==null)details.push(`подтверждённая запись ${fmtQty(controlPublished)} шт.`);if(chrtTarget!==null&&chrtTarget!==rowFree)details.push(`в этой строке свободно ${fmtQty(rowFree)} шт.`);
    if(waitingNewSync)details.push('ждём проверку новых заказов WB перед повышением; повтор автоматически');
    const text=`WB факт: ${fmtQty(actual)} шт. · ${details.join(' · ')}`;
    if(target!==null&&actual===target&&!['PENDING','PROCESSING','FAILED'].includes(queue))return {className:'placed',text,button:'Обновить',placed:true,published:target,actual};
    return {className:'stale',text,button:'Обновить',placed:false,published:target,actual};
  }
  if(waitingNewSync)return {className:'unknown',text:`WB факт: ${actual===null?'не сверён':fmtQty(actual)+' шт.'} · цель ${target===null?'—':fmtQty(target)+' шт.'} (${sizeLabel}) · ждём проверку новых заказов WB перед повышением; повтор автоматически`,button:'Обновить',placed:false,published:target,actual};
  if(marker?.status==='ERROR')return {className:'error',text:`WB: факт не сверён · последняя отправка с ошибкой${marker.last_error?' · '+marker.last_error:''}`,button:'Повторить',placed:false,published:null,actual:null};
  if(target!==null)return {className:'unknown',text:`WB факт: не сверён · цель ${fmtQty(target)} шт. (${sizeLabel})${queueLabel?' · '+queueLabel:''}`,button:'Обновить',placed:false,published:target,actual:null};
  return {className:'missing',text:'WB факт: не сверён · chrtId/цель размера ещё не определены',button:'Разместить',placed:false,published:0,actual:null};
}
function inventoryWbDiagnostic(r){
  const code=String(r?.barcode||''),physical=Math.max(0,Number(r?.quantity||0)),reserved=Math.max(0,Number(r?.reserved_quantity||0)),external=Math.max(0,Number(r?.external_pick_reserved_quantity||0)),pending=Math.max(0,Number(r?.accounting_pending_quantity||0)),storage=Math.max(0,Number(r?.storage_only_quantity||0)),rowFree=Math.max(0,Number(r?.available_to_sell??(physical-storage-reserved-external-pending))),actual=wbActualQuantity(code),chrtTarget=wbTargetQuantity(code),chrtId=wbChrtId(code),marker=state.fbsStockStatus?.[code],control=inventoryControlRow(code),desired=inventoryDesiredQuantity(code),published=inventoryPublishedControlQuantity(code),markerTarget=marker?.status==='PUBLISHED'?Math.max(0,Number(marker.published_amount||0)):null,target=chrtTarget??desired??published??markerTarget,queue=String(control?.queue_status||'').toUpperCase(),pubStatus=String(control?.publication_status||'').toUpperCase();
  if(r?.has_legacy_barcode_error)return {priority:6,kind:'error',reason:'Ошибка ШК: найдена старая задвоенная запись. Сначала исправьте ШК.'};
  // FC_K9R46_CLAIM_IS_PROGRESS: active queue state wins over an old publication ERROR.
  // CLAIM:<token> means the worker owns the row right now; it is not a WB failure.
  if(queue==='PROCESSING')return {priority:2,kind:'warn',reason:'Отправляется в WB сейчас. Worker уже забрал эту позицию; ожидаем подтверждение.'};
  if(queue==='PENDING'&&String(control?.queue_error||'').startsWith('WAITING_NEW_SYNC:'))return {priority:4,kind:'warn',reason:'Перед повышением остатка FulfillCore ждёт успешную проверку новых заказов WB. Повтор выполняется автоматически; эта защитная пауза больше не расходует лимит попыток.'};
  if(queue==='PENDING')return {priority:2,kind:'warn',reason:'Позиция находится в надёжной очереди WB и будет обработана пакетным worker.'};
  if(queue==='FAILED'||(!['PENDING','PROCESSING'].includes(queue)&&pubStatus==='ERROR'))return {priority:6,kind:'error',reason:`Публикация WB завершилась ошибкой: ${control?.queue_error||control?.publication_error||marker?.last_error||'причина не передана WB'}`};
  if(marker?.status==='ERROR')return {priority:6,kind:'error',reason:`Последняя публикация WB завершилась ошибкой: ${marker.last_error||'причина не передана WB'}`};
  const liveRow=state.wbLiveStock?.[code],mappingError=String(liveRow?.mapping_error||'');
  if(mappingError)return {priority:5,kind:'bad',reason:`${mappingError}. Нажмите «Обновить» — r44 выполнит точечную проверку этого ШК в WB и либо поставит реальную цель, либо покажет конкретную ошибку.`};
  if(actual===null&&String(control?.queue_reason||'').toUpperCase().includes('RESOLVE_CHRT'))return {priority:4,kind:'warn',reason:'Определяется chrtId размера WB. Служебный 0 старой очереди не является реальной целью остатка.'};
  if(actual===null)return {priority:3,kind:'warn',reason:`WB факт ещё не сверён.${target!==null?` Текущая цель FulfillCore по размеру ${fmtQty(target)} шт.`:''}`};
  if(chrtTarget===null&&chrtId===null)return {priority:4,kind:'warn',reason:'Не определён chrtId размера WB. Автоматическая коррекция отключена, чтобы один ШК не занизил остаток всего размера.'};
  const expected=r?.wb_sales_paused?0:(chrtTarget??target??rowFree);
  if(r?.wb_sales_paused&&actual!==0)return {priority:6,kind:'error',reason:`Продажи остановлены, но WB ещё показывает ${fmtQty(actual)} шт.; ожидается 0.`};
  if(target!==null&&target!==expected&&['PENDING','PROCESSING'].includes(queue))return {priority:5,kind:'bad',reason:`В очереди устаревшая цель ${fmtQty(target)} шт., а текущая цель chrtId ${fmtQty(expected)} шт. Позиция будет пересчитана.`};
  if(actual!==expected){
    if(actual>expected&&target!==expected)return {priority:6,kind:'error',reason:`ЗАЩИТА ОТ OVERSELL: WB показывает ${fmtQty(actual)} шт., безопасная цель размера ${fmtQty(expected)} шт. Автоматически ставим снижение в очередь.`};
    if(target===expected&&['PENDING','PROCESSING'].includes(queue))return {priority:5,kind:'bad',reason:`Цель chrtId ${fmtQty(expected)} шт. уже ${inventoryQueueLabel(queue)}. WB факт пока ${fmtQty(actual)} шт.; ждём подтверждение.`};
    if(target===expected)return {priority:5,kind:'bad',reason:`FulfillCore подтвердил цель chrtId ${fmtQty(expected)} шт., но текущий WB факт ${fmtQty(actual)} шт. Позиция будет перепроверена.`};
    if(target!==null)return {priority:5,kind:'bad',reason:`Последняя цель ${fmtQty(target)} шт. устарела. Сейчас цель chrtId ${fmtQty(expected)} шт.; требуется обновление WB.`};
    return {priority:5,kind:'bad',reason:`Расхождение: цель размера ${fmtQty(expected)} шт., WB показывает ${fmtQty(actual)} шт.`};
  }
  const excluded=[];if(reserved>0)excluded.push(`резерв заказов ${fmtQty(reserved)}`);if(external>0)excluded.push(`старое удержание r37 ${fmtQty(external)}`);if(pending>0)excluded.push(`ошибка учёта списания ${fmtQty(pending)}`);if(storage>0)excluded.push(`не для WB FBS ${fmtQty(storage)}`);
  const aliasNote=chrtTarget!==null&&chrtTarget!==rowFree?` Цель WB считается по всему размеру/chrtId и равна ${fmtQty(chrtTarget)} шт.; свободно в текущей строке ${fmtQty(rowFree)} шт.`:'';
  return {priority:0,kind:'ok',reason:(physical!==rowFree&&excluded.length?`WB совпадает с целью размера. Из физического остатка исключены ${excluded.join(' + ')}.`:'WB совпадает с канонической целью FulfillCore по chrtId.')+aliasNote};
}

function inventoryFilteredRows(){
  const q=$('inventorySearch')?.value.trim().toLowerCase()||'';
  const availability=$('inventoryAvailability')?.value||'IN_STOCK';
  const zone=$('inventoryStockZone')?.value||'ALL';
  const rows=(state.inventoryCatalog||[]).filter(x=>[
    x.nm_id,x.article,x.title,x.brand,x.size,x.barcode,(x.barcodes||[]).join(' '),inventoryWhereText(x)
  ].filter(Boolean).join(' ').toLowerCase().includes(q)).filter(x=>availability==='ALL'||(availability==='IN_STOCK'?Number(x.quantity||0)>0:availability==='ZERO'?Number(x.quantity||0)<=0:availability==='STORAGE_ONLY'?Number(x.storage_only_quantity||0)>0:true)).filter(x=>zone==='ALL'||(zone==='MAIN'?Number(x.storage_only_quantity||0)>0:zone==='FBS'?Math.max(0,Number(x.quantity||0)-Number(x.storage_only_quantity||0))>0:true));
  return rows.slice().sort((a,b)=>{const pa=inventoryWbDiagnostic(a).priority,pb=inventoryWbDiagnostic(b).priority;if(pa!==pb)return pb-pa;return String(a.article||'').localeCompare(String(b.article||''),'ru',{numeric:true,sensitivity:'base'});});
}
function xmlCell(value,type='String'){
  const text=String(value??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  return `<Cell><Data ss:Type="${type}">${text}</Data></Cell>`;
}
async function exportInventoryExcel(){if(window.FCStockRooms)return window.FCStockRooms.exportStock('FBS').catch(e=>toast(e.message,true));
  if(!state.inventoryCatalogLoaded){toast('Сначала откройте остатки и дождитесь загрузки',true);return;}
  let rows=inventoryFilteredRows();
  if(!rows.length){toast('Нет строк для выгрузки',true);return;}
  if(rows.some(r=>r._movement_deferred||r.movement_deferred)){
    toast('Остатки и резерв уже готовы. Для Excel догружаем историю движений отдельно…');
    try{await refreshInventoryMovementBackground(state.inventoryCatalogContextKey);rows=inventoryFilteredRows();}catch(_){/* export continues with blank audit columns; stock truth stays valid */}
  }
  // FC_K9R35_INVENTORY_EXPLAINABLE_EXPORT: current physical and every exclusion from
  // WB sellable truth are visible. Movement history is explanatory, not a second stock.
  // FC_K9R35_REAL_XLSX_EXPORT: server serializes these already-visible rows with openpyxl.
  const headers=['Артикул','Размер','ШК','chrtId','Название','Физически на складе','Резерв всего','Новые','На сборке','Удержано до подтверждения списания','Не для WB FBS','Свободно для продажи WB','Приход по журналу','Списание по журналу','Корректировки +','Корректировки -','Начальный/старый остаток вне журнала','Цель WB по chrtId','WB факт по chrtId','Разница цель - WB','Статус сверки','Ячейки / короба'];
  const bodyRows=rows.map(r=>{
    const physical=Number(r.quantity||0),reserved=Number(r.reserved_quantity||0),sale=Number(r.sale_reserved_quantity||0),picking=Number(r.picking_reserved_quantity||0),external=Number(r.external_pick_reserved_quantity||0),pending=Number(r.accounting_pending_quantity||0),storage=Number(r.storage_only_quantity||0),movementKnown=!(r._movement_deferred||r.movement_deferred),received=movementKnown?Number(r.received_quantity||0):'',shipped=movementKnown?Number(r.shipped_quantity||0):'',adjIn=movementKnown?Number(r.adjustment_in_quantity||0):'',adjOut=movementKnown?Number(r.adjustment_out_quantity||0):'',opening=movementKnown?Number(r.opening_balance_estimate||0):'',freeKnown=!r._truth_pending&&r.available_to_sell!==null&&r.available_to_sell!==undefined,free=freeKnown?Number(r.available_to_sell):'',actual=wbActualQuantity(r.barcode),target=wbTargetQuantity(r.barcode),chrt=wbChrtId(r.barcode),liveMapped=chrt!==null;
    const status=r._truth_pending?'Канонический резерв/свободно ещё сверяется':r.local_only&&liveMapped?'WB найден по live chrtId; локальная карточка WB не закэширована':r.local_only?'Только внутренний учёт — WB live mapping не найден':chrt===null?'Нет chrtId':actual===null?'WB факт не сверён':target===null?'Цель chrtId не рассчитана':(Number(actual)===Number(target)?'WB факт совпадает с целью chrtId':'WB факт отличается от цели chrtId');
    return [r.article||'',r.size||'',r.barcode||'',chrt??'',r.title||'',physical,reserved,sale,picking,pending,storage,free,received,shipped,adjIn,adjOut,opening,target===null?'':target,actual===null?'':actual,target===null||actual===null?'':Number(target)-Number(actual),status,inventoryWhereText(r)];
  });

  const localByChrt=new Map();
  for(const r of rows){
    const chrt=wbChrtId(r.barcode);if(chrt===null)continue;
    const key=String(chrt),g=localByChrt.get(key)||{articles:new Set(),sizes:new Set(),physical:0,reserved:0,sale:0,picking:0,external:0,pending:0,storage:0,free:0};
    if(r.article)g.articles.add(String(r.article));if(r.size)g.sizes.add(String(r.size));
    if(!r._truth_pending){g.physical+=Number(r.quantity||0);g.reserved+=Number(r.reserved_quantity||0);g.sale+=Number(r.sale_reserved_quantity||0);g.picking+=Number(r.picking_reserved_quantity||0);g.external+=Number(r.external_pick_reserved_quantity||0);g.pending+=Number(r.accounting_pending_quantity||0);g.storage+=Number(r.storage_only_quantity||0);g.free+=Number(r.available_to_sell||0);}
    localByChrt.set(key,g);
  }
  const chrtRows=Array.isArray(state.wbLiveStockSummary?.rows)?state.wbLiveStockSummary.rows:[];
  const chrtHeaders=['chrtId','Артикул','Размер','ШК размера','Физически','Резерв всего','Новые','На сборке','Удержано до списания','Не для WB FBS','Свободно для WB','Цель FulfillCore','WB факт','Разница цель - WB','Статус'];
  const chrtBodyRows=chrtRows.map(r=>{const g=localByChrt.get(String(r.chrt_id))||{articles:new Set(),sizes:new Set(),physical:0,reserved:0,sale:0,picking:0,external:0,pending:0,storage:0,free:0},target=r.target,actual=Number(r.amount||0),delta=target===null||target===undefined?null:Number(target)-actual,status=target===null||target===undefined?'Цель не рассчитана':Number(target)===actual?'Совпадает':actual>Number(target)?`WB выше безопасной цели на ${fmtQty(actual-Number(target))}`:`WB ниже цели на ${fmtQty(Number(target)-actual)}`;return [r.chrt_id??'',[...g.articles].join(', '),[...g.sizes].join(', '),(r.barcodes||[]).join(', '),g.physical,g.reserved,g.sale,g.picking,g.pending,g.storage,g.free,target===null||target===undefined?'':target,actual,delta??'',status];});
  const notes=[
    ['Физически на складе','Текущий InventoryBalance. Это главный фактический складской остаток FulfillCore.'],
    ['Не для WB FBS','Часть физического остатка в STORAGE_ONLY. Не публикуется на WB, пока оператор явно не добавит её в WB FBS.'],
    ['Удержано до подтверждения списания','Заказ уже вышел из Новые/На сборке, но складское списание ещё не подтверждено. В продажу не входит.'],
    ['Свободно для продажи WB','Физически - Не для WB FBS - Резерв всего - Удержано до подтверждения списания. Сборочное задание r38 уже физически списывает товар и отдельного резерва не создаёт.'],
    ['Приход/списание по журналу','История движений. Не является альтернативой текущему физическому остатку.'],
    ['Начальный/старый остаток вне журнала','Объясняет остаток, существовавший до полного журнала движений или пришедший через старые загрузки/корректировки.'],
    ['Сверка WB chrtId','Итоги WB считать только на этой вкладке: одна строка = один размер/chrtId.'],
  ];
  const client=(state.clients||[]).find(x=>String(x.id)===String(state.clientId))?.name||'client',warehouse=(state.warehouses||[]).find(x=>String(x.id)===String(state.warehouseId))?.name||'warehouse';
  const safe=x=>String(x).replace(/[\\/:*?"<>|]+/g,'_').replace(/\s+/g,'_');
  const filename=`Остатки_${safe(client)}_${safe(warehouse)}_${new Date().toISOString().slice(0,10)}.xlsx`;
  try{
    const blob=await apiBlob('/inventory/export.xlsx',{method:'POST',body:JSON.stringify({headers,rows:bodyRows,chrt_headers:chrtHeaders,chrt_rows:chrtBodyRows,notes,filename}),timeoutMs:120000});
    saveBlob(blob,filename);toast(`Excel .xlsx выгружен: ${rows.length} поз. · каноническая сверка chrtId: ${chrtRows.length}. Итоги WB считать на вкладке «Сверка WB chrtId».`);
  }catch(err){toast(`Не удалось сформировать Excel: ${err.message}`,true);}
}

async function downloadExternalFbsPickTemplate(){
  try{const blob=await apiBlob('/receiving/external-picking/template.xlsx',{timeoutMs:60000});saveBlob(blob,'Шаблон_сборочного_задания_FBS.xlsx');}
  catch(err){toast(`Не удалось скачать шаблон: ${err.message}`,true);}
}
async function cancelLastExternalFbsPick(){
  await ensureContext();
  if(!state.clientId||!state.warehouseId)throw new Error('Выберите клиента и склад');
  if(!confirm('Отменить ПОСЛЕДНЕЕ сформированное сборочное задание FBS?\n\nНа остатки вернутся только фактически списанные единицы — строго в исходные ячейки/короба. Резервы «Новые» и «На сборке» не изменятся. WB будет пересчитан через защищённую очередь.'))return;
  const result=await api('/receiving/external-picking/cancel-last',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null,performed_by:'admin'}),timeoutMs:180000});
  selectedInventoryBarcodes.clear();
  await Promise.allSettled([loadContextData(),loadInventoryCatalog({force:true})]);
  refreshInventoryWbActual({quiet:true,repair:false}).catch(()=>{});
  toast(`Сборочное задание №${result.task_number||'—'} отменено: возвращено ${fmtQty(result.restored_units||0)} шт. в исходные ячейки. WB пересчитывается.`);
}
function safePickTaskFilename(value){return String(value||'задание').replace(/[\\/:*?"<>|]+/g,'_').replace(/\s+/g,'_').slice(0,80)||'задание';}
// FC_K9R38_DIRECT_WRITEOFF_UI
// FC_K9R39_EXTERNAL_PICK_PROGRESS_UI: visible, persistent progress for large numbered FBS tasks.
function externalPickProgressId(){
  try{return crypto.randomUUID();}catch(_){return `pick-${Date.now()}-${Math.random().toString(16).slice(2)}`;}
}
function externalPickProgressCard(fileName){
  document.querySelector('[data-external-pick-progress-card]')?.remove();
  const card=document.createElement('div');card.dataset.externalPickProgressCard='1';
  card.style.cssText='position:fixed;right:24px;bottom:24px;z-index:10050;width:min(460px,calc(100vw - 48px));background:#111827;color:#fff;border-radius:16px;padding:18px 18px 16px;box-shadow:0 18px 50px rgba(0,0,0,.35);font:14px/1.4 system-ui,-apple-system,Segoe UI,sans-serif';
  card.innerHTML=`<div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start"><div><b style="font-size:16px">Формирование сборочного задания FBS</b><div data-pick-file style="opacity:.75;margin-top:3px;word-break:break-all">${esc(fileName||'')}</div></div><div data-pick-elapsed style="font-variant-numeric:tabular-nums;white-space:nowrap;opacity:.85">00:00</div></div><div data-pick-stage style="margin-top:14px;font-weight:700">Файл отправляется на сервер…</div><div data-pick-message style="margin-top:5px;opacity:.82">Не закрывайте страницу и не загружайте этот номер задания повторно.</div><div style="height:9px;border-radius:999px;background:rgba(255,255,255,.15);overflow:hidden;margin-top:14px"><div data-pick-bar style="height:100%;width:2%;background:#60a5fa;transition:width .35s ease"></div></div><div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:7px"><button type="button" data-pick-close style="display:none;border:0;border-radius:10px;padding:7px 12px;background:rgba(255,255,255,.12);color:#fff;cursor:pointer">Закрыть</button><div data-pick-percent style="margin-left:auto;text-align:right;font-weight:700">2%</div></div>`;
  document.body.appendChild(card);
  card.querySelector('[data-pick-close]')?.addEventListener('click',()=>{clearInterval(timer);card.remove();});
  const started=Date.now();
  const timer=setInterval(()=>{const sec=Math.max(0,Math.floor((Date.now()-started)/1000)),m=String(Math.floor(sec/60)).padStart(2,'0'),ss=String(sec%60).padStart(2,'0');const el=card.querySelector('[data-pick-elapsed]');if(el)el.textContent=`${m}:${ss}`;},1000);
  return {card,timer,started};
}
function externalPickProgressRender(ui,data){
  if(!ui?.card||!data)return;
  const pct=Math.max(0,Math.min(100,Number(data.percent||0)));
  const stageNames={WAITING:'Ожидаем запуск',RECEIVED:'Файл принят',PARSED:'Шаблон проверен',CATALOG:'Проверка ШК',CACHE_MAP:'Локальная карта WB',WB_CATALOG:'Карточки WB',MATCHING:'Сопоставление с базой',NEW_ORDERS:'Свежие заказы WB',WRITE_OFF:'Списание свободного остатка',TARGETS:'Расчёт целей WB',WB_LOCK:'Безопасное окно WB',WB_READ:'Сверка факта WB',WB_WRITE:'Обновление WB',WB_CONFIRMED:'WB подтвердил',COMMIT:'Фиксация в базе',DONE:'Готово',LOCAL_DELETE:'Изменяем FBS',SNAPSHOT:'Контрольный снимок',ERROR:'Ошибка'};
  const stage=ui.card.querySelector('[data-pick-stage]'),msg=ui.card.querySelector('[data-pick-message]'),bar=ui.card.querySelector('[data-pick-bar]'),percent=ui.card.querySelector('[data-pick-percent]');
  if(stage)stage.textContent=stageNames[data.stage]||String(data.stage||'Обработка');
  if(msg)msg.textContent=String(data.message||'Обработка продолжается…');
  if(bar){bar.style.width=`${Math.max(2,pct)}%`;bar.style.background=data.stage==='ERROR'?'#ef4444':data.stage==='DONE'?'#22c55e':'#60a5fa';}
  if(percent)percent.textContent=`${Math.round(pct)}%`;
}
// FC_K9R41_VISIBLE_ERROR: DONE may auto-close; ERROR never disappears by itself.
function externalPickProgressFinish(ui,{error=false,delayMs=5000}={}){
  if(!ui)return;clearInterval(ui.timer);
  const close=ui.card?.querySelector('[data-pick-close]');if(close)close.style.display='inline-block';
  if(error)return;
  if(delayMs>0)setTimeout(()=>ui.card?.remove(),delayMs);
}
async function externalPickProgressPoll(progressId,ui,stopRef){
  while(!stopRef.stop){
    try{
      const p=await api(`/receiving/external-picking/progress/${encodeURIComponent(progressId)}`,{timeoutMs:4000});
      externalPickProgressRender(ui,p);
      if(p.stage==='DONE'||p.stage==='ERROR')return p;
    }catch(_){/* the main import request remains authoritative */}
    await new Promise(r=>setTimeout(r,1000));
  }
  return null;
}
// r39 keeps r38 direct-writeoff/WB-confirm-before-commit semantics; only responsiveness/progress changes.
async function uploadExternalFbsPickFile(file,taskNumberOverride=''){
  if(!file)return;await ensureContext();
  if(!state.clientId||!state.warehouseId)throw new Error('Выберите клиента и склад');
  if(!/\.xlsx$/i.test(String(file.name||'')))throw new Error('Нужен Excel .xlsx с колонками ШК и Количество');
  const override=String(taskNumberOverride||'').trim();
  if(!confirm(`Загрузить сборочное задание ${override?'№'+override+' ':''}«${file.name}»?\n\nFulfillCore возьмёт ТОЛЬКО свободный от заказов остаток. Резервы «Новые» и «На сборке» не изменятся. Доступное количество будет физически списано из указанных ячеек и в этой же операции подтверждено на WB. Эта операция только уменьшает/сохраняет WB факт и никогда не повышает его. Если WB не подтвердит безопасный новый остаток, локальное списание откатится и лист не будет выдан.`))return;
  const progressId=externalPickProgressId(),ui=externalPickProgressCard(file.name),stopRef={stop:false};
  const pollPromise=externalPickProgressPoll(progressId,ui,stopRef);
  const fd=new FormData();fd.append('client_id',state.clientId);fd.append('warehouse_id',state.warehouseId);const wbWh=Number(state.wbWarehouseFilter||0)||null;if(wbWh)fd.append('wb_warehouse_id',String(wbWh));fd.append('performed_by','admin');fd.append('progress_id',progressId);if(override)fd.append('task_number_override',override);fd.append('file',file,file.name);
  try{
    // Large tasks may legitimately need several minutes because WB NEW sync + WB read/write confirmation are synchronous.
    // FC_K9R52_EXTERNAL_PICK_NO_BROWSER_ABORT: progress is polled separately; never turn a still-running server task into a false 10-minute browser timeout.
    const result=await api('/receiving/external-picking/import-safe',{method:'POST',body:fd,timeoutMs:0});
    externalPickProgressRender(ui,{stage:'COMMIT',percent:99,message:'Списание подтверждено. Формируем итоговый Excel с ячейками…'});
    const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId});
    const blob=await apiBlob(`/receiving/external-picking/${encodeURIComponent(result.batch_id)}/export.xlsx?${q.toString()}`,{timeoutMs:120000});
    const filename=`Сборочное_задание_${safePickTaskFilename(result.task_number)}.xlsx`;saveBlob(blob,filename);
    selectedInventoryBarcodes.clear();
    await Promise.allSettled([loadContextData(),loadInventoryCatalog({force:true})]);
    refreshInventoryWbActual({quiet:true,repair:false}).catch(()=>{});
    const duplicate=result.duplicate?' · повторная загрузка: списание НЕ выполнялось':'';
    externalPickProgressRender(ui,{stage:'DONE',percent:100,message:`Готово: задание №${result.task_number||'—'} · обработано ${fmtQty((result.positions_processed??result.positions) || 0)}/${fmtQty(result.positions||0)} поз. · списано ${fmtQty(result.allocated_units||0)} шт. · не хватило ${fmtQty(result.shortage_units||0)} шт. · не обработано WB ${fmtQty(result.failed_units||0)} шт. · Excel скачан.`});
    toast(`Сборочное задание №${result.task_number||'—'} готово: запрошено ${fmtQty(result.requested_units||0)} шт. · физически списано ${fmtQty(result.allocated_units||0)} шт. · не хватило ${fmtQty(result.shortage_units||0)} шт. · не обработано WB ${fmtQty(result.failed_units||0)} шт. · WB: ${result.wb_sync_status||'—'}${duplicate}.`,false);
    externalPickProgressFinish(ui,{delayMs:8000});
    return result;
  }catch(err){
    externalPickProgressRender(ui,{stage:'ERROR',percent:0,message:`Операция не завершена: ${err.message}`});
    externalPickProgressFinish(ui,{error:true,delayMs:0});
    throw err;
  }finally{
    stopRef.stop=true;pollPromise.catch(()=>{});
  }
}
async function publishInventoryBarcode(productId,barcode,localQuantity,reopen=false){
  const code=String(barcode||'').trim();if(!code)throw new Error('У товара нет ШК для размещения на WB');
  const row=(state.inventoryCatalog||[]).find(x=>String(x.barcode||'')===code||(x.barcodes||[]).includes(code));
  const available=Math.max(0,Math.round(Number(row?.available_to_sell??localQuantity??0)));
  if(!confirm(`Передать на WB каноническую цель размера для ШК ${code}?\n\nСвободно в строке: ${available} шт. Итоговая отправка будет пересчитана по всему chrtId/размеру, включая другие ШК этого размера.`))return false;
  const r=await api('/receiving/publish-wb',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,barcode:code,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null}),timeoutMs:0});
  // FC_K9R45_MANUAL_PUBLISH_UI_REAL_TARGET: backend has already resolved the live size,
  // cleared any older pause for that size, and recomputed the real canonical target.
  // Reflect that target immediately instead of leaving the previous cached zero on screen.
  state.wbLiveStock=state.wbLiveStock||{};
  state.wbLiveStock[code]={...(state.wbLiveStock[code]||{}),target:Number(r.after||0),chrt_id:Number(r.chrt_id||0)||null};
  toast(`Цель WB ${fmtQty(r.after)} шт. поставлена в очередь${Number(r.paused_skus_cleared||0)>0?` · старый STOP снят (${fmtQty(r.paused_skus_cleared)} SKU)`:''}. Ожидаем подтверждение…`);
  const confirmation=await waitInventoryControlConfirmation('PUBLISH_WB',[code],{timeoutMs:15000});
  await loadInventoryCatalog({force:true});
  if(confirmation.state==='CONFIRMED'){await Promise.allSettled([refreshInventoryControlProjection([code]),refreshInventoryWbActualCodes([code])]);const actual=wbActualQuantity(code),expected=wbTargetQuantity(code)??inventoryDesiredQuantity(code);if(expected!==null&&actual===Number(expected))toast(`✓ WB подтвердил остаток ${fmtQty(expected)} шт.`);else toast(`Очередь обработана, но WB факт сейчас ${actual===null?'не сверён':fmtQty(actual)+' шт.'}${expected===null?'':` при текущей цели ${fmtQty(expected)} шт.`}. Выполняем повторную сверку.`,true);}
  else if(confirmation.state==='FAILED'){const x=confirmation.failed[0]||{};toast(`WB не подтвердил остаток: ${x.queue_error||x.publication_error||'ошибка публикации'}`,true);}
  else toast('Остаток сохранён в очереди, WB ещё не подтвердил его. Складская работа не блокируется.',true);
  if(reopen)await openInventoryProduct(productId,code);return true;
}
function renderInventory(){
  if(!state.inventoryCatalogLoaded){$('inventoryBody').innerHTML='<tr><td colspan="9" class="empty-state">Откройте «Склад → Остатки», чтобы загрузить каталог товаров.</td></tr>';return;}
  const rows=inventoryFilteredRows();
  const inStock=(state.inventoryCatalog||[]).filter(x=>Number(x.quantity||0)>0).length;
  const localUnits=rows.reduce((sum,x)=>sum+Number(x.quantity||0),0),truthRows=rows.filter(x=>!x._truth_pending),truthPendingRows=rows.filter(x=>x._truth_pending).length,truthErrorRows=rows.filter(x=>x._truth_pending&&String(x._truth_error||'').trim()).length,truthActiveRows=Math.max(0,truthPendingRows-truthErrorRows),uncachedTruthRows=rows.filter(x=>x.local_only&&!x._truth_pending).length,reservedUnits=truthRows.reduce((sum,x)=>sum+window.FCR24History.fbsCounts(x,Math.max(0,Number(x.quantity||0)-Number(x.storage_only_quantity||0))).reserved,0),externalPickUnits=truthRows.reduce((sum,x)=>sum+Number(x.external_pick_reserved_quantity||0),0),pendingUnits=truthRows.reduce((sum,x)=>sum+Number(x.accounting_pending_quantity||0),0),storageUnits=truthRows.reduce((sum,x)=>sum+Number(x.storage_only_quantity||0),0),availableUnits=truthRows.reduce((sum,x)=>sum+Number(x.available_to_sell??Math.max(0,Number(x.quantity||0)-Number(x.storage_only_quantity||0)-Number(x.reserved_quantity||0)-Number(x.external_pick_reserved_quantity||0)-Number(x.accounting_pending_quantity||0))),0);
  const wbSummary=state.wbLiveStockSummary||null,actualKnown=rows.filter(x=>wbActualQuantity(x.barcode)!==null),fallbackActual=actualKnown.reduce((sum,x)=>sum+Number(wbActualQuantity(x.barcode)||0),0);
  const wbActualUnits=wbSummary&&Number.isFinite(Number(wbSummary.actual_total))?Number(wbSummary.actual_total):fallbackActual,wbTargetUnits=wbSummary&&Number.isFinite(Number(wbSummary.target_total))?Number(wbSummary.target_total):rows.reduce((sum,x)=>sum+Number(wbTargetQuantity(x.barcode)||0),0),wbDelta=wbTargetUnits-wbActualUnits;
  const wbText=wbSummary?fmtQty(wbActualUnits):(actualKnown.length===rows.filter(x=>x.barcode).length&&actualKnown.length?fmtQty(wbActualUnits):`${fmtQty(wbActualUnits)}${actualKnown.length?' (частично)':' —'}`);
  const checked=state.wbLiveStockCheckedAt?` · сверено ${new Date(state.wbLiveStockCheckedAt).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'})}`:'';
  const mappingWarn=wbSummary&&(Number(wbSummary.missing_barcodes||0)>0||Number(wbSummary.unresolved_targets||0)>0)?` · без точной привязки: ${Number(wbSummary.missing_barcodes||0)+Number(wbSummary.unresolved_targets||0)}`:'';
  const fbsPhysicalUnits=truthRows.reduce((sum,x)=>sum+Math.max(0,Number(x.quantity||0)-Number(x.storage_only_quantity||0)),0);
  if($('inventoryCatalogSummary'))$('inventoryCatalogSummary').textContent=`ФИЗИЧЕСКИ ВСЕГО: ${fmtQty(localUnits)} шт. · ОСНОВНОЙ СКЛАД: ${fmtQty(storageUnits)} шт. · FBS ФИЗИЧЕСКИ: ${fmtQty(fbsPhysicalUnits)} шт. · резерв заказов: ${fmtQty(reservedUnits)} шт. · ошибка учёта списания: ${fmtQty(pendingUnits)} шт. · СВОБОДНО ДЛЯ WB: ${truthPendingRows?`${fmtQty(availableUnits)} шт.${truthActiveRows?` + ${truthActiveRows} строк сверяются`:''}${truthErrorRows?` + ${truthErrorRows} строк НЕ СВЕРЕНЫ`:''}`:fmtQty(availableUnits)+' шт.'} · ЦЕЛЬ WB по chrtId: ${fmtQty(wbTargetUnits)} шт. · WB факт: ${wbText} шт. · разница: ${wbDelta>0?'+':''}${fmtQty(wbDelta)} шт.${uncachedTruthRows?` · live-сопоставлено без локального кэша: ${uncachedTruthRows} строк`:''}${mappingWarn}${checked} · выбрано: ${selectedInventoryBarcodes.size}`;
  $('inventoryBody').innerHTML=rows.length?rows.map(r=>{const truthPending=Boolean(r._truth_pending),available=truthPending?null:Number(r.available_to_sell??Math.max(0,Number(r.quantity||0)-Number(r.storage_only_quantity||0)-Number(r.reserved_quantity||0)-Number(r.external_pick_reserved_quantity||0)-Number(r.accounting_pending_quantity||0))),fbs=truthPending?null:fbsStockPresentation(r.barcode,r.quantity,available),diag=truthPending?{priority:0,reason:'Сверяем резерв по активным заказам'}:inventoryWbDiagnostic(r),firstLocation=r.locations?.find(x=>x.location_id)?.location_id||'',code=String(r.barcode||''),liveMapped=wbChrtId(code)!==null;return `<tr ${diag.priority>=4?'class="inventory-discrepancy" ':''}${r.local_only?'':`data-inventory-row-open="${esc(r.product_id)}" data-inventory-barcode="${esc(code)}"`}>
    <td class="inventory-select-cell"><input type="checkbox" data-inventory-select="${esc(code)}" ${selectedInventoryBarcodes.has(code)?'checked':''} ${code?'':'disabled'} aria-label="Выбрать товар"></td>
    <td>${r.photo?`<img class="inventory-thumb" src="${esc(r.photo)}" alt="" loading="lazy"/>`:'<div class="inventory-thumb placeholder">—</div>'}</td>
    <td>${r.local_only?`<button class="inventory-title-link" type="button" data-inventory-open="${esc(r.product_id)}" data-inventory-barcode="${esc(code)}">${esc(r.title||'Без названия')}</button><small class="subline ${r._quick_local?'warn':(liveMapped?'good':'bad')}">${r._quick_local?'Карточка WB и резерв загружаются…':(liveMapped?`WB найден по live chrtId ${wbChrtId(code)} · обновление остатка работает через актуальную привязку WB`:'WB live mapping не найден · только внутренний учёт')}</small>`:`<button class="inventory-title-link" type="button" data-inventory-open="${esc(r.product_id)}" data-inventory-barcode="${esc(code)}">${esc(r.title||'Без названия')}</button><small class="subline">WB ${esc(r.nm_id||'')}</small>`}${r.has_legacy_barcode_error?'<small class="subline bad">Найден ранее задвоенный ШК — откройте ячейку для исправления</small>':''}</td>
    <td><b>${esc(r.article||'—')}</b></td><td>${esc(r.size||'—')}</td><td>${esc(code||'—')}</td>
    <td class="inventory-where">${inventoryLocationsHtml(r)}</td>
    <td class="qty ${!truthPending&&Number(available||0)===0?'zero-qty':''}">${truthPending?inventoryTruthPendingQtyHtml(r):`<b>Свободно для WB: ${fmtQty(available)} шт.</b><small class="subline"><b>Физически: ${fmtQty(r.quantity||0)} шт.</b> · <b>не для WB FBS: ${fmtQty(r.storage_only_quantity||0)} шт.</b>${Number(r.external_pick_reserved_quantity||0)>0?` · <b>старое удержание r37: ${fmtQty(r.external_pick_reserved_quantity)} шт.</b>`:''} · ошибка учёта списания: ${fmtQty(r.accounting_pending_quantity||0)} шт.</small><small class="subline good">${window.FCR24History.fbsBadge(r,Math.max(0,Number(r.quantity||0)-Number(r.storage_only_quantity||0)))}</small>${Number(r.reservation_gap_quantity||0)>0?`<small class="subline bad">Контроль резерва: ${fmtQty(r.reservation_gap_quantity)} шт. учтено по активным заказам до восстановления журнала</small>`:''}${stockMovementHtml(r)}`}</td>
    <td class="inventory-row-open">${truthPending?inventoryTruthPendingWbHtml(r,firstLocation,code):`<div class="fbs-stock-state ${esc(r.wb_sales_paused?'warn':fbs.className)}"><span>${r.has_legacy_barcode_error?'Сначала удалите ошибочную строку с задвоенным ШК':(r.wb_sales_paused?`⏸ Продажи WB остановлены · На WB: ${fbs.actual===null?'не сверено':fmtQty(fbs.actual)+' шт.'}`:`На WB: ${fbs.actual===null?'не сверено':fmtQty(fbs.actual)+' шт.'} · Свободно для WB: ${fmtQty(available)} шт.${Number(r.storage_only_quantity||0)>0?` · не для WB FBS: ${fmtQty(r.storage_only_quantity)} шт.`:''}${Number(r.external_pick_reserved_quantity||0)>0?` · старое удержание r37: ${fmtQty(r.external_pick_reserved_quantity)} шт.`:''}${Number(r.accounting_pending_quantity||0)>0?` · ошибка учёта списания: ${fmtQty(r.accounting_pending_quantity)} шт.`:''}`)}</span><small class="subline ${diag.priority>=4?'bad':''}">Причина: ${esc(diag.reason)}</small><div class="button-row"><button class="mini secondary" type="button" data-inventory-open="${esc(r.product_id)}" data-inventory-barcode="${esc(code)}">Где лежит</button>${fbs.button&&!r.has_legacy_barcode_error&&!r.wb_sales_paused?`<button class="mini ${fbs.placed?'secondary':'primary'}" type="button" data-inventory-publish="${esc(r.product_id)}" data-inventory-barcode="${esc(code)}" data-inventory-quantity="${esc(r.quantity||0)}">${esc(fbs.button)}</button>`:''}</div></div>`}</td>
  </tr>`}).join(''):`<tr><td colspan="9" class="empty-state">Ничего не найдено. Если товары WB ещё не импортированы, откройте «Товары» → «Импортировать все».</td></tr>`;
  updateInventoryBulkToolbar();queueTopTableScrolls();
  const headBox=document.querySelector('[data-inventory-select-visible]');
  if(headBox){const visible=inventoryVisibleBarcodes();const selected=visible.filter(code=>selectedInventoryBarcodes.has(code)).length;headBox.checked=visible.length>0&&selected===visible.length;headBox.indeterminate=selected>0&&selected<visible.length;}
}
function inventoryAvailabilityKey(){return `fc_inventory_availability:${state.warehouseId||'none'}`;}
function restoreInventoryAvailability(){const el=$('inventoryAvailability');if(el)el.value=localStorage.getItem(inventoryAvailabilityKey())||'IN_STOCK';}
function updateInventoryBulkToolbar(){
  const bar=$('inventoryBulkToolbar');if(!bar)return;const errBtn=bar.querySelector('[data-inventory-wb-errors]');if(errBtn){const n=(state.inventoryBulkWbErrors||[]).length;errBtn.hidden=!n;errBtn.textContent=n?`Скачать ошибки WB (${n})`:'Скачать ошибки WB';}
  const count=selectedInventoryBarcodes.size;
  const selectedRows=(state.inventoryCatalog||[]).filter(r=>selectedInventoryBarcodes.has(String(r.barcode||'')));
  const selectedStorage=selectedRows.reduce((sum,r)=>sum+Number(r.storage_only_quantity||0),0);
  const label=bar.querySelector('[data-inventory-selected-count]');if(label)label.textContent=`Выбрано: ${count}${selectedStorage>0?` · не для WB FBS: ${fmtQty(selectedStorage)} шт.`:''}`;
  bar.querySelectorAll('[data-inventory-bulk-needs-selection]').forEach(btn=>btn.disabled=count===0);
  const addBtn=bar.querySelector('[data-inventory-add-fbs]');if(addBtn){addBtn.disabled=count===0||selectedStorage<=0;addBtn.textContent=selectedStorage>0?`➕ Добавить в WB FBS: ${fmtQty(selectedStorage)} шт.`:'➕ Добавить выбранные в WB FBS';}
}
function inventoryVisibleBarcodes(){return inventoryFilteredRows().map(x=>String(x.barcode||'')).filter(Boolean);}
function installInventoryAvailabilityFilter(){
  const search=$('inventorySearch');if(!search||$('inventoryAvailability'))return;
  const wrap=document.createElement('label');wrap.className='inventory-availability-filter';wrap.innerHTML='<span>Остаток</span><select id="inventoryAvailability"><option value="IN_STOCK">Только в наличии</option><option value="STORAGE_ONLY">Не для WB FBS</option><option value="ALL">Все товары</option><option value="ZERO">Нет в наличии</option></select>';
  search.insertAdjacentElement('afterend',wrap);restoreInventoryAvailability();
  $('inventoryAvailability').addEventListener('change',e=>{localStorage.setItem(inventoryAvailabilityKey(),e.target.value);renderInventory();});
  const zone=document.createElement('label');zone.className='inventory-availability-filter';zone.innerHTML='<span>Контур</span><select id="inventoryStockZone"><option value="ALL">Весь физический склад</option><option value="MAIN">Основной склад</option><option value="FBS">FBS</option></select>';wrap.insertAdjacentElement('afterend',zone);$('inventoryStockZone').value=localStorage.getItem(`fc_inventory_zone:${state.warehouseId||'none'}`)||'ALL';$('inventoryStockZone').addEventListener('change',e=>{localStorage.setItem(`fc_inventory_zone:${state.warehouseId||'none'}`,e.target.value);renderInventory();});
  const exportBtn=document.createElement('button');exportBtn.id='inventoryExcelExport';exportBtn.type='button';exportBtn.className='secondary';exportBtn.textContent='Выгрузить остатки в Excel';exportBtn.addEventListener('click',exportInventoryExcel);zone.insertAdjacentElement('afterend',exportBtn);
  const table=$('inventoryBody')?.closest('table');const heads=table?.querySelectorAll('thead th');if(heads?.length>=8&&!table.querySelector('thead [data-inventory-select-head]')){const th=document.createElement('th');th.dataset.inventorySelectHead='1';th.innerHTML='<input type="checkbox" data-inventory-select-visible title="Выбрать все показанные">';table.querySelector('thead tr')?.prepend(th);}const freshHeads=table?.querySelectorAll('thead th');if(freshHeads?.length>=9){freshHeads[7].textContent='На складе / резерв / свободно';freshHeads[8].textContent='WB / свободно / резерв';}
  const toolbar=document.createElement('div');toolbar.id='inventoryBulkToolbar';toolbar.className='inventory-bulk-toolbar';toolbar.innerHTML=`<div class="inventory-bulk-summary"><b data-inventory-selected-count>Выбрано: 0</b><small>Резерв заказа: при поступлении заказа — общий резерв SKU; после сборочного листа он закрепляется за конкретной ячейкой/коробом; после упаковки физический остаток списывается из этого места. Отмена до сборочного листа освобождает резерв на месте, отмена после сборочного листа до упаковки возвращает единицу в виртуальную ПРИЕМКА. Упакованный заказ остаётся списанным.</small></div><div class="button-row inventory-bulk-actions"><button type="button" class="danger" data-inventory-pause-all>Остановить все продажи WB</button><button type="button" class="secondary" data-inventory-resume-all>Возобновить все продажи WB</button><button type="button" class="secondary" data-inventory-select-visible-btn>☑ Выбрать все остатки</button><button type="button" class="secondary" data-inventory-clear-selection>☐ Снять выбор</button><button type="button" class="secondary" data-inventory-refresh-wb>↻ Сверить WB сейчас</button><button type="button" class="danger" data-inventory-pause-wb data-inventory-bulk-needs-selection disabled>⏸ Остановить продажи WB</button><button type="button" class="secondary" data-inventory-resume-wb data-inventory-bulk-needs-selection disabled>▶ Возобновить продажи WB</button><button type="button" class="danger" data-inventory-bulk-delete data-inventory-bulk-needs-selection disabled>Удалить выбранные из FBS</button><button type="button" class="danger" data-inventory-delete-all>Обнулить весь FBS</button><button type="button" class="secondary" data-inventory-move-main data-inventory-bulk-needs-selection disabled>В Основной + стоп WB</button><button type="button" class="secondary" data-inventory-move-main-all>Весь FBS → Основной + стоп WB</button><button type="button" class="primary" data-inventory-add-fbs disabled>➕ Добавить выбранные в WB FBS</button><button type="button" class="secondary" data-inventory-bulk-publish data-inventory-bulk-needs-selection disabled>↻ Обновить выбранные на WB FBS</button><button type="button" class="primary" data-external-pick-upload>⬆ Загрузить сборочное задание FBS</button><button type="button" class="secondary" data-external-pick-template>Шаблон сборочного задания</button><button type="button" class="secondary" data-external-pick-cancel-last>↩ Отменить последнее сборочное задание FBS</button><input type="file" data-external-pick-file accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden><button type="button" class="secondary" data-inventory-wb-errors hidden>Скачать ошибки WB</button><button type="button" class="secondary" data-inventory-undo-last>Отменить последнюю старую операцию</button></div>`;
  table?.parentElement?.insertAdjacentElement('beforebegin',toolbar);
  toolbar.querySelector('[data-external-pick-file]')?.addEventListener('change',e=>{const file=e.target.files?.[0];e.target.value='';if(file)uploadExternalFbsPickFile(file).catch(err=>toast(err.message,true));});
  updateInventoryBulkToolbar();
}
installInventoryAvailabilityFilter();
async function exportInventoryWbErrors(){
  const rows=state.inventoryBulkWbErrors||[];if(!rows.length){toast('Ошибок последнего обновления WB нет');return;}
  const headers=['ШК','chrtId','Отправляли','Ошибка'];
  const bodyRows=rows.map(r=>[r.barcode||'',r.chrt_id||'',r.after??'',r.error||'']);
  const filename=`Ошибки_WB_FBS_${new Date().toISOString().slice(0,10)}.xlsx`;
  try{const blob=await apiBlob('/inventory/export.xlsx',{method:'POST',body:JSON.stringify({headers,rows:bodyRows,chrt_headers:[],chrt_rows:[],notes:[],filename}),timeoutMs:60000});saveBlob(blob,filename);}catch(err){toast(`Не удалось сформировать Excel ошибок: ${err.message}`,true);}
}

// FC_G5A8K_INVENTORY_CONTEXT: no credentials, barcodes or responses enter persistent storage.
let inventoryRequestEpoch=0;
let inventoryBulkActionBusy=false;
const inventoryReadsInFlight=new Map();
function inventoryRequestContext(){return {client_id:state.clientId,warehouse_id:state.warehouseId,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null,epoch:inventoryRequestEpoch};}
function inventoryContextCurrent(c){return c.epoch===inventoryRequestEpoch&&c.client_id===state.clientId&&c.warehouse_id===state.warehouseId&&c.wb_warehouse_id===(Number(state.wbWarehouseFilter||0)||null);}
function assertInventoryContext(c){if(!inventoryContextCurrent(c)){const e=new Error('Клиент или склад изменён. Предыдущая сверка остановлена.');e.code='CONTEXT_CHANGED';throw e;}}
document.addEventListener('change',e=>{if(['clientSelect','warehouseSelect','wbWarehouseView','wbCredentialSelect'].includes(e.target?.id))inventoryRequestEpoch++;},true);
function inventoryRead(path,codes,context,timeoutMs){
  assertInventoryContext(context);
  const sorted=[...new Set(codes)].sort(),key=JSON.stringify([context,path,sorted]);
  if(inventoryReadsInFlight.has(key))return inventoryReadsInFlight.get(key);
  if(inventoryReadsInFlight.size>=8)return Promise.reject(new Error('Уже выполняется несколько сверок остатков. Дождитесь завершения.'));
  const body={client_id:context.client_id,warehouse_id:context.warehouse_id,barcodes:sorted};
  if(path.endsWith('/wb-live-stocks'))body.wb_warehouse_id=context.wb_warehouse_id;
  const task=Promise.resolve().then(()=>{assertInventoryContext(context);return api(path,{method:'POST',body:JSON.stringify(body),timeoutMs});}).then(r=>{assertInventoryContext(context);return r;}).finally(()=>inventoryReadsInFlight.delete(key));
  inventoryReadsInFlight.set(key,task);return task;
}
// FC_K9R29_BULK_PUBLISH_FAST_RETURN compatibility: refreshCodes.length<=40 was the old inline-only threshold; r46 keeps fast durable return and replaces inline verification with nonblocking progress for every batch.
// FC_K9R46_WB_BULK_VISIBLE_PROGRESS: persistent operator feedback for 100-10000 rows.
function inventoryWbBulkProgressId(){return `wb-${Date.now()}-${Math.random().toString(16).slice(2,10)}`;}
function inventoryWbBulkProgressCard(action,count,units=0){
  document.querySelector('[data-inventory-wb-progress-card]')?.remove();
  const card=document.createElement('div');card.dataset.inventoryWbProgressCard='1';card.style.cssText='position:fixed;right:24px;bottom:24px;z-index:10000;width:min(520px,calc(100vw - 48px));background:#111827;color:#fff;border-radius:16px;padding:16px 18px;box-shadow:0 18px 45px rgba(0,0,0,.28);font-size:14px';
  const title=action==='ADD_TO_WB_FBS'?'Добавление выбранных в WB FBS':action==='DELETE_FBS_KEEP_RESERVES'?'Удаление выбранных из FBS':action==='MOVE_FBS_TO_MAIN_AND_PAUSE'?'Перевод FBS в Основной':'Актуализация остатков WB';
  card.innerHTML=`<div style="display:flex;justify-content:space-between;gap:12px;align-items:center"><b data-wb-progress-title>${title}</b><span data-wb-progress-time>00:00</span></div><div data-wb-progress-stage style="font-weight:700;margin-top:10px">Подготовка</div><div data-wb-progress-message style="margin-top:5px;color:#d1d5db">Выбрано ${fmtQty(count)} поз.${units?` · ${fmtQty(units)} шт. «Не для WB FBS»`:''}</div><div style="height:8px;background:#374151;border-radius:999px;margin-top:12px;overflow:hidden"><div data-wb-progress-bar style="height:100%;width:1%;background:#60a5fa;transition:width .25s"></div></div><div data-wb-progress-counts style="margin-top:9px;color:#d1d5db"></div><button type="button" data-wb-progress-close style="display:none;margin-top:12px;padding:7px 12px;border:0;border-radius:8px;cursor:pointer">Закрыть</button>`;
  document.body.appendChild(card);const started=Date.now(),timer=setInterval(()=>{const sec=Math.floor((Date.now()-started)/1000),el=card.querySelector('[data-wb-progress-time]');if(el)el.textContent=`${String(Math.floor(sec/60)).padStart(2,'0')}:${String(sec%60).padStart(2,'0')}`;},1000);
  card.querySelector('[data-wb-progress-close]')?.addEventListener('click',()=>{clearInterval(timer);card.remove();});
  return {card,timer,started};
}
function inventoryWbBulkProgressRender(ui,{stage='PROCESS',percent=0,message='',counts=''}={}){
  if(!ui?.card)return;const st=ui.card.querySelector('[data-wb-progress-stage]'),msg=ui.card.querySelector('[data-wb-progress-message]'),bar=ui.card.querySelector('[data-wb-progress-bar]'),cnt=ui.card.querySelector('[data-wb-progress-counts]');
  const labels={RECEIVED:'Файл/команда принята',CATALOG:'Товары FulfillCore',SIZE_SCOPE:'Размеры WB / chrtId',CONVERT_STORAGE:'Перевод в WB FBS',UNPAUSE:'Снимаем старые STOP',TARGETS:'Расчёт целей WB',QUEUE:'Сохраняем очередь',QUEUED:'Очередь WB готова',WORKER:'Отправляем в WB',WAITING:'Проверяем новые заказы',ACTUAL:'Сверяем WB факт',REPAIR:'Исправляем расхождения',DONE:'Готово',ATTENTION:'Требует внимания',ERROR:'Ошибка'};
  if(st)st.textContent=labels[stage]||stage;if(msg&&message)msg.textContent=message;if(cnt)cnt.textContent=counts||'';if(bar)bar.style.width=`${Math.max(1,Math.min(100,Number(percent)||0))}%`;
}
function inventoryWbBulkProgressFinish(ui,{error=false,delayMs=10000}={}){if(!ui)return;clearInterval(ui.timer);const close=ui.card?.querySelector('[data-wb-progress-close]');if(close)close.style.display='inline-block';if(!error&&delayMs>0)setTimeout(()=>ui.card?.remove(),delayMs);}
async function inventoryWbPreparationProgressPoll(progressId,ui,stopRef){
  while(!stopRef.stop){try{const p=await api(`/receiving/inventory-bulk/progress/${encodeURIComponent(progressId)}`,{timeoutMs:4000});inventoryWbBulkProgressRender(ui,{stage:p.stage,percent:p.percent,message:p.message,counts:p.total?`Обработано: ${fmtQty(p.processed??p.queued??0)}/${fmtQty(p.total)}`:''});if(['ERROR'].includes(String(p.stage||'')))return p;}catch(_){ }await new Promise(r=>setTimeout(r,700));}return null;
}
async function inventoryWbBulkControlSnapshot(codes,context){const r=await inventoryControlStatus(codes,context);assertInventoryContext(context);const rows=r.rows||[],c={done:0,pending:0,processing:0,waiting:0,failed:0,superseded:0,missing:Math.max(0,codes.length-rows.length)};for(const x of rows){const q=String(x.queue_status||'').toUpperCase(),e=String(x.queue_error||'');if(q==='DONE')c.done++;else if(q==='SUPERSEDED')c.superseded++;else if(q==='PROCESSING')c.processing++;else if(q==='PENDING'&&e.startsWith('WAITING_NEW_SYNC:'))c.waiting++;else if(q==='PENDING')c.pending++;else if(q==='FAILED')c.failed++;else c.missing++;}return {rows,counts:c};}
async function monitorInventoryWbBulk(codes,{ui,context,preparationErrors=[]}={}){
  const unique=Array.from(new Set((codes||[]).map(x=>String(x||'').trim()).filter(Boolean)));let last=null,lastRender=0;const started=Date.now();
  while(Date.now()-started<600000){
    assertInventoryContext(context);
    try{last=await inventoryWbBulkControlSnapshot(unique,context);const c=last.counts,terminal=c.done+c.superseded+c.failed;const active=c.pending+c.processing+c.waiting;const percent=78+Math.round(12*terminal/Math.max(1,unique.length));const stage=c.processing?'WORKER':(c.waiting?'WAITING':'WORKER');inventoryWbBulkProgressRender(ui,{stage,percent,message:`WB очередь: подтверждено/завершено ${fmtQty(c.done+c.superseded)}/${fmtQty(unique.length)} поз.`,counts:`в очереди ${fmtQty(c.pending)} · отправляется ${fmtQty(c.processing)} · ждёт новые заказы ${fmtQty(c.waiting)} · ошибок ${fmtQty(c.failed)}${c.missing?` · без строки ${fmtQty(c.missing)}`:''}`});
      state.inventoryWbControl={...(state.inventoryWbControl||{}),...Object.fromEntries(last.rows.map(x=>[String(x.barcode||''),x]))};if(Date.now()-lastRender>5000){renderInventory();lastRender=Date.now();}
      if(active===0&&terminal+c.missing>=unique.length)break;
    }catch(_){ }
    await new Promise(r=>setTimeout(r,1800));
  }
  const failedCodes=new Set((last?.rows||[]).filter(x=>String(x.queue_status||'').toUpperCase()==='FAILED').map(x=>String(x.barcode||'')));
  const actualCodes=unique.filter(code=>!failedCodes.has(code));
  assertInventoryContext(context);inventoryWbBulkProgressRender(ui,{stage:'ACTUAL',percent:92,message:`Читаем фактические остатки WB пакетно для ${fmtQty(actualCodes.length)} ШК…`,counts:'Один снимок WB по chrtId вместо поштучных запросов; явные FAILED не тормозят снимок'});
  let live=null;try{live=await refreshInventoryWbActualCodes(actualCodes,{context,updateSummary:true});renderInventory();}catch(err){inventoryWbBulkProgressRender(ui,{stage:'ATTENTION',percent:94,message:`Очередь обработана, но WB факт пока не удалось прочитать: ${err.message}`,counts:'Нажмите «Сверить WB сейчас» позже — команды в очереди не потеряны.'});inventoryWbBulkProgressFinish(ui,{error:true});return;}
  const summary=live?.summary||{},rows=summary.rows||[];let mismatch=rows.filter(x=>x.target!==null&&Number(x.amount)!==Number(x.target)),matched=rows.filter(x=>x.target!==null&&Number(x.amount)===Number(x.target));
  if(mismatch.length){
    const observed={};for(const row of live.rows||[]){if(row.target!==null&&Number(row.amount)!==Number(row.target)&&!Object.prototype.hasOwnProperty.call(observed,String(row.barcode||'')))observed[String(row.barcode||'')]=Number(row.amount||0);}
    if(Object.keys(observed).length){try{inventoryWbBulkProgressRender(ui,{stage:'REPAIR',percent:95,message:`Нашли ${fmtQty(mismatch.length)} расхождений chrtId. Ставим только их на повторную актуализацию…`});const repair=await api('/receiving/wb-drift-repair',{method:'POST',body:JSON.stringify({client_id:context.client_id,warehouse_id:context.warehouse_id,wb_warehouse_id:context.wb_warehouse_id,observed}),timeoutMs:45000});if(Number(repair?.queued||0)>0){await new Promise(r=>setTimeout(r,6000));const retryStart=Date.now();while(Date.now()-retryStart<120000){const snap=await inventoryWbBulkControlSnapshot(Object.keys(observed),context);const a=snap.counts.pending+snap.counts.processing+snap.counts.waiting;inventoryWbBulkProgressRender(ui,{stage:'REPAIR',percent:96,message:`Повторная актуализация: активно ${fmtQty(a)} поз.`,counts:`ошибок ${fmtQty(snap.counts.failed)}`});if(a===0)break;await new Promise(r=>setTimeout(r,1800));}live=await refreshInventoryWbActualCodes(actualCodes,{context,updateSummary:true});renderInventory();mismatch=(live?.summary?.rows||[]).filter(x=>x.target!==null&&Number(x.amount)!==Number(x.target));matched=(live?.summary?.rows||[]).filter(x=>x.target!==null&&Number(x.amount)===Number(x.target));}}catch(_){}}
  }
  const missing=Number(live?.summary?.missing_barcodes||0),unresolved=Number(live?.summary?.unresolved_targets||0),failed=last?.counts?.failed||0,totalChrt=Number(live?.summary?.chrt_count||0);
  state.inventoryBulkWbErrors=[...preparationErrors,...(last?.rows||[]).filter(x=>String(x.queue_status||'').toUpperCase()==='FAILED').map(x=>({barcode:x.barcode,error:x.queue_error||x.publication_error||'ошибка WB'}))];updateInventoryBulkToolbar();
  if(!mismatch.length&&!missing&&!unresolved&&!failed&&!preparationErrors.length){inventoryWbBulkProgressRender(ui,{stage:'DONE',percent:100,message:`WB актуализирован: ${fmtQty(matched.length)}/${fmtQty(totalChrt)} размеров/chrtId совпадают с FulfillCore.`,counts:`WB факт обновлён автоматически · ШК проверено ${fmtQty(unique.length)}`});inventoryWbBulkProgressFinish(ui,{delayMs:12000});}
  else{inventoryWbBulkProgressRender(ui,{stage:'ATTENTION',percent:100,message:`Актуализация завершена не полностью: совпало ${fmtQty(matched.length)} chrtId · расхождений ${fmtQty(mismatch.length)}.`,counts:`без chrtId ${fmtQty(missing)} · без цели ${fmtQty(unresolved)} · ошибок очереди ${fmtQty(failed)} · ошибок подготовки ${fmtQty(preparationErrors.length)}`});inventoryWbBulkProgressFinish(ui,{error:true});}
}

async function inventoryControlStatus(barcodes,context=inventoryRequestContext()){
  return inventoryRead('/receiving/inventory-control-status',barcodes,context,10000);
}
async function refreshInventoryControlProjection(barcodes=null,{render=false,context=inventoryRequestContext()}={}){
  assertInventoryContext(context);
  const codes=Array.from(new Set((barcodes||(state.inventoryCatalog||[]).flatMap(r=>[r.barcode,...(r.barcodes||[])])).map(x=>String(x||'').trim()).filter(Boolean)));
  if(!codes.length){if(!barcodes)state.inventoryWbControl={};if(render)renderInventory();return {rows:[]};}
  const result=await inventoryControlStatus(codes,context);assertInventoryContext(context);
  const next=barcodes?{...(state.inventoryWbControl||{})}:{};
  for(const code of codes)delete next[code];for(const row of result.rows||[])next[String(row.barcode||'')]=row;state.inventoryWbControl=next;if(render)renderInventory();return result;
}
async function refreshInventoryWbActualCodes(barcodes,{render=false,context=inventoryRequestContext(),updateSummary=false}={}){
  assertInventoryContext(context);
  const codes=Array.from(new Set((barcodes||[]).map(x=>String(x||'').trim()).filter(Boolean)));if(!codes.length)return {rows:[]};
  const liveTimeout=codes.length>1000?120000:(codes.length>300?90000:(codes.length>80?60000:30000));
  const result=await inventoryRead('/receiving/wb-live-stocks',codes,context,liveTimeout);assertInventoryContext(context);
  const next={...(state.wbLiveStock||{})};
  for(const code of codes)delete next[code];
  for(const row of result.rows||[])next[String(row.barcode||'')]=row;
  for(const code of result.missing||[])next[String(code||'')]={amount:null,target:null,chrt_id:null,mapping_error:'WB не нашёл этот ШК в текущих карточках или не вернул chrtId размера'};
  for(const code of result.unresolved_targets||[]){const key=String(code||'');next[key]={...(next[key]||{}),mapping_error:'chrtId WB найден, но каноническая цель FulfillCore для размера пока не рассчитана'};}
  state.wbLiveStock=next;if(updateSummary)state.wbLiveStockSummary=result.summary||null;state.wbLiveStockCheckedAt=result.checked_at||new Date().toISOString();state.wbLiveStockWarehouseId=result.wb_warehouse_id||state.wbLiveStockWarehouseId||null;state.wbLiveStockError='';if(render)renderInventory();return result;
}
function inventoryPublishReason(code){
  const c=inventoryControlRow(code),actual=wbActualQuantity(code),desired=inventoryDesiredQuantity(code),queue=String(c?.queue_status||'').toUpperCase(),pub=String(c?.publication_status||'').toUpperCase();
  if(queue==='PROCESSING')return 'отправляется в WB';
  if(queue==='PENDING'&&String(c?.queue_error||'').startsWith('WAITING_NEW_SYNC:'))return 'ждём свежую проверку новых заказов WB';
  if(queue==='PENDING')return 'в очереди WB';
  if(queue==='FAILED'||(!['PENDING','PROCESSING'].includes(queue)&&pub==='ERROR'))return c?.queue_error||c?.publication_error||'ошибка публикации WB';
  if(desired===null)return 'цель FulfillCore ещё не рассчитана';
  if(actual===null)return `цель ${fmtQty(desired)} шт.; WB факт пока не удалось сверить`;
  if(actual!==desired)return `цель ${fmtQty(desired)} шт.; WB факт ${fmtQty(actual)} шт.; ${inventoryQueueLabel(queue)||'ожидается повторная отправка'}`;
  if(!['DONE','SUPERSEDED'].includes(queue)||pub!=='PUBLISHED')return `WB факт уже ${fmtQty(actual)} шт., но очередь ещё ${inventoryQueueLabel(queue)||queue||'обрабатывается'}`;
  return '';
}
async function verifyAndRepairInventoryWbPublish(barcodes,{rounds=6,context=inventoryRequestContext()}={}){
  const codes=Array.from(new Set((barcodes||[]).map(x=>String(x||'').trim()).filter(Boolean))),confirmed=new Set(),retried=new Set();let unresolved=[...codes],lastErrors=[];
  for(let round=1;round<=rounds&&unresolved.length;round++){
    await new Promise(r=>setTimeout(r,Math.min(10000,2000*round)));assertInventoryContext(context);
    try{
      await refreshInventoryControlProjection(unresolved,{context});assertInventoryContext(context);
      // The durable worker already verifies its writes. Query WB only after DONE,
      // not for every pending batch and not during each retry backoff.
      const completed=unresolved.filter(code=>String(inventoryControlRow(code)?.queue_status||'').toUpperCase()==='DONE');
      if(completed.length)await refreshInventoryWbActualCodes(completed,{context});
      assertInventoryContext(context);
      const next=[];lastErrors=[];
      for(const code of unresolved){
        const c=inventoryControlRow(code),actual=wbActualQuantity(code),desired=inventoryDesiredQuantity(code),queue=String(c?.queue_status||'').toUpperCase(),pub=String(c?.publication_status||'').toUpperCase();
        if(desired!==null&&actual!==null&&actual===desired&&(queue==='DONE'||queue==='SUPERSEDED')&&pub==='PUBLISHED'){confirmed.add(code);continue;}
        next.push(code);lastErrors.push({barcode:code,after:desired,error:inventoryPublishReason(code)});
      }
      unresolved=next;renderInventory();if(!unresolved.length)break;
      const repair=unresolved.filter(code=>['DONE','SUPERSEDED'].includes(String(inventoryControlRow(code)?.queue_status||'').toUpperCase())&&!retried.has(code));
      if(round<rounds&&repair.length){
        assertInventoryContext(context);for(const code of repair)retried.add(code);
        await api('/receiving/inventory-bulk',{method:'POST',body:JSON.stringify({client_id:context.client_id,warehouse_id:context.warehouse_id,action:'PUBLISH_WB',barcodes:repair,all_inventory:false,wb_warehouse_id:context.wb_warehouse_id,performed_by:'admin'}),timeoutMs:60000});
        assertInventoryContext(context);
      }
    }catch(error){assertInventoryContext(context);lastErrors=unresolved.map(barcode=>({barcode,error:`Сверка WB: ${error.message||'временная ошибка'}`}));renderInventory();}
  }
  return {total:codes.length,confirmed:confirmed.size,unresolved:lastErrors.filter(x=>unresolved.includes(String(x.barcode||'')))};
}
async function waitInventoryControlConfirmation(action,barcodes,{timeoutMs=15000,context=inventoryRequestContext()}={}){
  const started=Date.now();let last=null;
  while(Date.now()-started<timeoutMs){
    assertInventoryContext(context);
    try{last=await inventoryControlStatus(barcodes,context);}catch(_){assertInventoryContext(context);await new Promise(r=>setTimeout(r,700));continue;}
    const rows=last.rows||[];
    const failed=rows.filter(x=>String(x.queue_status||'').toUpperCase()==='FAILED'||String(x.publication_status||'').toUpperCase()==='ERROR');
    if(failed.length)return {state:'FAILED',rows,failed};
    const completed=rows.filter(x=>{const queue=String(x.queue_status||'').toUpperCase(),queueDone=queue==='DONE'||queue==='SUPERSEDED',pub=String(x.publication_status||'').toUpperCase(),desired=Number(x.desired_amount),published=Number(x.published_amount);if(action==='PAUSE_WB'||action==='DELETE_FREE_AND_PAUSE')return x.paused&&queueDone&&pub==='PUBLISHED'&&published===0;if(action==='RESUME_WB')return !x.paused&&queueDone&&pub==='PUBLISHED'&&Number.isFinite(desired)&&published===desired;return queueDone&&pub==='PUBLISHED'&&Number.isFinite(desired)&&published===desired;});
    if(rows.length&&completed.length===rows.length)return {state:'CONFIRMED',rows};
    await new Promise(r=>setTimeout(r,700));
  }
  return {state:'PENDING',rows:last?.rows||[]};
}

function fbsRowsForChange(allInventory=false){
  const rows=(state.inventoryCatalog||[]);
  if(allInventory)return rows.filter(r=>Math.max(0,Number(r.quantity||0)-Number(r.storage_only_quantity||0))>0);
  return rows.filter(r=>selectedInventoryBarcodes.has(String(r.barcode||'')));
}
function fbsScopeBarcodes(rows,fallback=[]){
  return Array.from(new Set((rows||[]).flatMap(r=>[r.barcode,...(r.barcodes||[])]).concat(fallback||[]).map(x=>String(x||'').trim()).filter(Boolean)));
}
function applyImmediateFbsLocalProjection(rows,{moveToMain=false}={}){
  for(const row of rows||[]){
    const physical=Math.max(0,Number(row.quantity||0)),storage=Math.min(physical,Math.max(0,Number(row.storage_only_quantity||0))),remaining=moveToMain?physical:storage;
    row.quantity=remaining;row.storage_only_quantity=remaining;row.fbs_pool_quantity=0;row.available_to_sell=0;row._truth_pending=false;row._truth_refreshing=true;
    if(moveToMain)row.wb_sales_paused=true;
    row.locations=(row.locations||[]).map(place=>{const p=Math.max(0,Number(place.quantity||0)),st=Math.min(p,Math.max(0,Number(place.storage_only_quantity||0))),keep=moveToMain?p:st;return {...place,quantity:keep,storage_only_quantity:keep,reserved_quantity:0,external_pick_reserved_quantity:0,free_quantity:0};}).filter(place=>Number(place.quantity||0)>0);
  }
}

async function runSelectedFbsResetChunks(context,codes,{moveToMain=false,ui=null}={}){
  const unique=[...new Set((codes||[]).map(x=>String(x||'').trim()).filter(Boolean))];
  const action=moveToMain?'MOVE_FBS_TO_MAIN_AND_PAUSE':'DELETE_FBS_KEEP_RESERVES';
  const failures=[],results=[];let processed=0,deleted=0,moved=0,detached=0,released=0;
  const skipped=[];
  const render=msg=>inventoryWbBulkProgressRender(ui,{stage:'LOCAL_DELETE',percent:30+Math.round(55*processed/Math.max(1,unique.length)),message:msg,counts:`Обработано: ${fmtQty(processed)}/${fmtQty(unique.length)} · ошибок ${fmtQty(failures.length)}`});
  async function run(chunk){
    if(!chunk.length)return;
    assertInventoryContext(context);
    try{
      const r=await api('/receiving/inventory-bulk',{method:'POST',body:JSON.stringify({client_id:context.client_id,warehouse_id:context.warehouse_id,action,barcodes:chunk,all_inventory:false,confirm_text:null,wb_warehouse_id:context.wb_warehouse_id,performed_by:'admin'}),timeoutMs:120000});
      results.push(r);processed+=chunk.length;deleted+=Number(r.deleted_units||0);moved+=Number(r.moved_to_main_units||0);detached+=Number(r.detached_reserve_units||0);released+=Number(r.released_external_hold_units||0);
      render(`${moveToMain?'Переводим FBS в Основной':'Удаляем FBS'}: пакет ${fmtQty(chunk.length)} поз. готов.`);return;
    }catch(e){
      const msg=String(e?.message||e||'Ошибка');
      if(Number(e?.status)===409&&(/уже нет физического остатка WB FBS/i.test(msg)||/нет физического остатка WB FBS/i.test(msg))){processed+=chunk.length;skipped.push(...chunk);render('Часть выбранных позиций уже была FBS=0 — пропускаем.');return;}
      if(chunk.length>1){const mid=Math.ceil(chunk.length/2);await run(chunk.slice(0,mid));await run(chunk.slice(mid));return;}
      failures.push({barcode:chunk[0],error:msg});processed+=1;render(`Не удалось ${chunk[0]}: ${msg}`);
    }
  }
  const CHUNK=80;
  for(let i=0;i<unique.length;i+=CHUNK)await run(unique.slice(i,i+CHUNK));
  return {ok:failures.length===0,partial:failures.length>0,positions:unique.length-failures.length,requested_positions:unique.length,deleted_units:deleted,moved_to_main_units:moved,detached_reserve_units:detached,released_external_hold_units:released,fbs_zero_verified:failures.length===0,failed_barcodes:failures.map(x=>x.barcode),errors:failures,skipped_zero_barcodes:skipped,metadata_warnings:results.flatMap(x=>x.metadata_warnings||[])};
}

async function changeFbsInventory({allInventory=false,mode='RESET'}={}){
  if(inventoryBulkActionBusy){toast('Массовая операция уже выполняется. Дождитесь результата.',true);return;}
  inventoryBulkActionBusy=true;
  try{
    await ensureContext();const context=inventoryRequestContext(),selectedRows=fbsRowsForChange(allInventory),selectedPrimary=[...selectedInventoryBarcodes],barcodes=fbsScopeBarcodes(selectedRows,selectedPrimary),moveToMain=mode==='MOVE_MAIN';
    if(!allInventory&&!barcodes.length)throw new Error('Отметьте хотя бы один товар');
    let confirmText=null;
    if(allInventory&&moveToMain){
      confirmText=prompt('ВЕСЬ текущий FBS будет переведён в Основной склад без физического удаления. Для этих товаров продажи WB будут остановлены, а на WB будет поставлена цель 0. Перед операцией автоматически сохранится контрольный Excel.\n\nДля подтверждения напишите: ВСЕ В ОСНОВНОЙ');
      if(String(confirmText||'').trim().toUpperCase()!=='ВСЕ В ОСНОВНОЙ')return;
    }else if(allInventory){
      confirmText=prompt('ВЕСЬ локальный остаток FBS будет удалён именно из FBS и после операции FBS станет 0. Остаток Основного склада не удаляется. Действующие резервы заказов сохраняются логически. Перед операцией автоматически сохранится контрольный Excel.\n\nДля подтверждения напишите: ОБНУЛИТЬ FBS');
      if(String(confirmText||'').trim().toUpperCase()!=='ОБНУЛИТЬ FBS')return;
    }else if(moveToMain){
      if(!confirm(`Перевести выбранные позиции FBS в Основной склад: ${barcodes.length} поз.?\n\nФизический товар НЕ удаляется. FBS по выбранным позициям станет 0, товар останется в тех же ячейках как Основной склад, а продажи WB будут остановлены.`))return;
    }else if(!confirm(`Удалить из FBS только выбранные позиции: ${barcodes.length} поз.?\n\nПосле операции FBS по ним станет 0. Основной склад не затрагивается. Действующие резервы заказов не отменяются; их старая адресная привязка снимается. Операция сразу фиксируется в истории склада.`))return;
    const ui=inventoryWbBulkProgressCard(moveToMain?'MOVE_FBS_TO_MAIN_AND_PAUSE':'DELETE_FBS_KEEP_RESERVES',allInventory?(selectedRows.length||barcodes.length):barcodes.length,0);
    if(allInventory){
      inventoryWbBulkProgressRender(ui,{stage:'SNAPSHOT',percent:8,message:'Сохраняем контрольный Excel перед полной операцией FBS…'});
      const blob=await apiBlob('/receiving/inventory-bulk/fbs-remove-plan.xlsx',{method:'POST',body:JSON.stringify({client_id:context.client_id,warehouse_id:context.warehouse_id,barcodes:[],all_inventory:true}),timeoutMs:120000});
      assertInventoryContext(context);
      const stamp=new Date().toISOString().replace(/[:T]/g,'-').slice(0,19);
      saveBlob(blob,`FBS_контроль_перед_${moveToMain?'переводом_в_Основной':'обнулением'}_${stamp}.xlsx`);
    }
    inventoryWbBulkProgressRender(ui,{stage:'LOCAL_DELETE',percent:allInventory?30:8,message:`${moveToMain?'Переводим':'Удаляем'} локальный FBS. ${allInventory?'':'Выбранные позиции идут пакетами без ожидания контрольного Excel. ' }Интерфейс не завис — прогресс обновляется.`});
    let result;
    if(!allInventory&&barcodes.length>1)result=await runSelectedFbsResetChunks(context,barcodes,{moveToMain,ui});
    else{
      const action=moveToMain?'MOVE_FBS_TO_MAIN_AND_PAUSE':'DELETE_FBS_KEEP_RESERVES';
      result=await api('/receiving/inventory-bulk',{method:'POST',body:JSON.stringify({client_id:context.client_id,warehouse_id:context.warehouse_id,action,barcodes:allInventory?[]:barcodes,all_inventory:allInventory,confirm_text:confirmText,wb_warehouse_id:context.wb_warehouse_id,performed_by:'admin'}),timeoutMs:180000});
    }
    assertInventoryContext(context);
    if(!result.fbs_zero_verified&&!result.partial){inventoryWbBulkProgressRender(ui,{stage:'ERROR',percent:100,message:'Сервер не подтвердил контроль FBS=0.'});inventoryWbBulkProgressFinish(ui,{error:true});throw new Error('Сервер не подтвердил контроль FBS=0. Операция не считается завершённой.');}
    // r87: the server has already committed and verified FBS=0. Apply the same result
    // to the loaded projection immediately so a slow catalog refresh cannot make it look
    // as if the stock was not deleted. The authoritative refresh still runs below.
    const failedSet=new Set(result.failed_barcodes||[]),projectRows=selectedRows.filter(r=>!failedSet.has(String(r.barcode||'')));
    applyImmediateFbsLocalProjection(projectRows,{moveToMain});
    selectedInventoryBarcodes.clear();(result.failed_barcodes||[]).forEach(code=>selectedInventoryBarcodes.add(code));
    window.FCStockRooms?.invalidate?.();
    renderInventory();
    inventoryWbBulkProgressRender(ui,{stage:result.partial?'ATTENTION':'DONE',percent:100,message:result.partial?`Готово частично: ${fmtQty(result.requested_positions-result.errors.length)}/${fmtQty(result.requested_positions)} поз. удалены. Ошибочные позиции оставлены выделенными.`:'Готово. Локальный FBS обновлён; WB синхронизируется в фоне.',counts:result.errors?.length?result.errors.slice(0,3).map(x=>`${x.barcode}: ${x.error}`).join(' · '):''});
    inventoryWbBulkProgressFinish(ui,{error:Boolean(result.partial),delayMs:result.partial?0:7000});
    const detached=Number(result.detached_reserve_units||0),released=Number(result.released_external_hold_units||0),warnings=Array.isArray(result.metadata_warnings)?result.metadata_warnings:[];
    if(moveToMain){
      toast(`✓ FBS=0 подтверждён. В Основной переведено: ${fmtQty(result.moved_to_main_units||0)} шт. · резерв сохранён: ${fmtQty(detached)} шт.${released?` · снято старых сборочных удержаний: ${fmtQty(released)} шт.`:''} Продажи WB остановлены; WB=0 отправляется в фоне.${warnings.length?` Служебное предупреждение: ${warnings.slice(0,2).join(' · ')}`:''}`,warnings.length>0);
    }else{
      toast(`✓ FBS=0 подтверждён. Из FBS удалено: ${fmtQty(result.deleted_units||0)} шт. · резерв сохранён: ${fmtQty(detached)} шт.${released?` · снято старых сборочных удержаний: ${fmtQty(released)} шт.`:''} Можно загружать FBS заново с чистого листа.${warnings.length?` Служебное предупреждение: ${warnings.slice(0,2).join(' · ')}`:''}`,warnings.length>0);
    }
    if(result.partial){const sample=(result.errors||[]).slice(0,3).map(x=>`${x.barcode}: ${x.error}`).join(' · ');toast(`Удалено ${fmtQty(result.requested_positions-(result.errors||[]).length)}/${fmtQty(result.requested_positions)} поз. Не удалено: ${(result.errors||[]).length}.${sample?` ${sample}`:''}`,true);}
    Promise.allSettled([loadContextData(),loadInventoryCatalog({force:true}),refreshInventoryWbActual({quiet:true})]).then(()=>{try{assertInventoryContext(context);window.FCStockRooms?.refresh?.('FBS',true);window.FCStockRooms?.refresh?.('MAIN',true);}catch{}});
    return result;
  }finally{inventoryBulkActionBusy=false;}
}

async function runInventoryBulkAction(action,options={}){
  if(inventoryBulkActionBusy){toast('Массовая операция уже выполняется. Дождитесь результата.',true);return;}
  inventoryBulkActionBusy=true;
  try{return await runInventoryBulkActionInternal(action,options);}finally{inventoryBulkActionBusy=false;}
}
function applyImmediateWbPauseZero(codes){ // FC_K9R60_DIRECT_ZERO_UI_ACK
  const wanted=new Set((codes||[]).map(x=>String(x||'')).filter(Boolean));
  if(!wanted.size)return;
  state.wbLiveStock=state.wbLiveStock||{};state.inventoryWbControl=state.inventoryWbControl||{};state.fbsStockStatus=state.fbsStockStatus||{};
  wanted.forEach(code=>{
    const live=state.wbLiveStock[code]||{};state.wbLiveStock[code]={...live,amount:0,target:0};
    const ctrl=state.inventoryWbControl[code]||{};state.inventoryWbControl[code]={...ctrl,paused:true,desired_amount:0,published_amount:0,queue_status:'DONE',queue_error:null,publication_status:'PUBLISHED',publication_error:null,queue_reason:'PAUSE_WB_DIRECT_CONFIRMED'};
    const pub=state.fbsStockStatus[code]||{};state.fbsStockStatus[code]={...pub,published_amount:0,status:'PUBLISHED',last_error:null};
  });
  (state.inventoryCatalog||[]).forEach(row=>{if(wanted.has(String(row.barcode||'')))row.wb_sales_paused=true;});
  renderInventory();
}
async function runInventoryBulkActionInternal(action,{allInventory=false}={}){
  await ensureContext();const context=inventoryRequestContext();
  const barcodes=[...selectedInventoryBarcodes];
  if(!allInventory&&!barcodes.length)throw new Error('Отметьте хотя бы один товар');
  let confirmText=null;
  if(action==='PAUSE_WB'){
    if(!confirm(`${allInventory?"Остановить ВСЕ продажи WB для выбранного клиента и склада (включая новые товары)?":`Остановить новые продажи WB для выбранных товаров: ${barcodes.length} поз.?`}\n\nВ WB уйдёт 0. Уже полученные заказы и их резервы сохранятся и будут собираться/упаковываться.`))return;
  }else if(action==='RESUME_WB'){
    if(!confirm(`${allInventory?"Возобновить ВСЕ продажи WB для выбранного клиента и склада?":`Возобновить продажи WB для выбранных товаров: ${barcodes.length} поз.?`}\n\nВ WB уйдёт только свободный остаток после действующих резервов.`))return;
  }else if(action==='DELETE_FREE_AND_PAUSE'&&allInventory){
    confirmText=prompt('Будут остановлены новые продажи WB и удалён только СВОБОДНЫЙ локальный остаток.\n\nВсе товары под уже существующие заказы/сборочные листы останутся.\n\nДля подтверждения напишите: УДАЛИТЬ СВОБОДНЫЕ');
    if(String(confirmText||'').trim().toUpperCase()!=='УДАЛИТЬ СВОБОДНЫЕ')return;
  }else if(action==='DELETE_FREE_AND_PAUSE'){
    if(!confirm(`Остановить продажи WB и удалить только свободный остаток выбранных товаров: ${barcodes.length} поз.?\n\nРезервы действующих заказов останутся и продолжат попадать в сборочные листы.`))return;
  }else if(action==='ADD_TO_WB_FBS'){
    const selectedRows=(state.inventoryCatalog||[]).filter(r=>selectedInventoryBarcodes.has(String(r.barcode||'')));
    const storageUnits=selectedRows.reduce((sum,r)=>sum+Number(r.storage_only_quantity||0),0);
    if(storageUnits<=0){toast('У выбранных товаров нет остатка в категории «Не для WB FBS».',true);return;}
    if(!confirm(`Добавить выбранный остаток в продажи WB FBS?\n\nВыбрано: ${barcodes.length} поз. · «Не для WB FBS»: ${fmtQty(storageUnits)} шт.\n\nФизический остаток НЕ изменится. Резервы и заказы НЕ изменятся. FulfillCore только переведёт эти единицы из «Не для WB FBS» в продаваемый FBS и поставит новую актуальную цель по размеру/chrtId в очередь WB.`))return;
  }else if(action==='PUBLISH_WB'){
    if(!confirm(`Обновить на WB FBS выбранные товары: ${barcodes.length} поз.?\n\nНа WB будет отправлена текущая уже продаваемая цель FulfillCore по каждому размеру/chrtId. Остаток «Не для WB FBS» эта команда НЕ переводит в продажу. Для него используйте отдельную кнопку «Добавить выбранные в WB FBS».`))return;
  }
  const localOnly=['PAUSE_WB','RESUME_WB','DELETE_FREE_AND_PAUSE','PUBLISH_WB','ADD_TO_WB_FBS'].includes(action);
  const allInStockCodes=(state.inventoryCatalog||[]).filter(x=>Number(x.quantity||0)>0).map(x=>String(x.barcode||'')).filter(Boolean);
  const publishAll=action==='PUBLISH_WB'&&allInStockCodes.length>0&&allInStockCodes.every(code=>selectedInventoryBarcodes.has(code));
  const effectiveAll=allInventory||publishAll;
  const publishRequest=['PUBLISH_WB','ADD_TO_WB_FBS'].includes(action);
  let wbProgressUi=null,wbProgressStop=null,wbProgressPoll=null,progressId=null,result=null;
  if(publishRequest){const selectedRows=(state.inventoryCatalog||[]).filter(r=>(effectiveAll?Number(r.quantity||0)>0:selectedInventoryBarcodes.has(String(r.barcode||''))));const units=action==='ADD_TO_WB_FBS'?selectedRows.reduce((sum,r)=>sum+Number(r.storage_only_quantity||0),0):0;progressId=inventoryWbBulkProgressId();wbProgressUi=inventoryWbBulkProgressCard(action,effectiveAll?allInStockCodes.length:barcodes.length,units);wbProgressStop={stop:false};wbProgressPoll=inventoryWbPreparationProgressPoll(progressId,wbProgressUi,wbProgressStop);}
  try{result=await api('/receiving/inventory-bulk',{method:'POST',body:JSON.stringify({client_id:context.client_id,warehouse_id:context.warehouse_id,action,barcodes:effectiveAll?[]:barcodes,all_inventory:effectiveAll,confirm_text:confirmText,wb_warehouse_id:context.wb_warehouse_id,performed_by:'admin',progress_id:progressId}),timeoutMs:publishRequest?180000:((action==='PAUSE_WB'||action==='DELETE_FREE_AND_PAUSE')?180000:(localOnly?60000:300000))});}catch(err){if(wbProgressStop)wbProgressStop.stop=true;if(wbProgressUi){inventoryWbBulkProgressRender(wbProgressUi,{stage:'ERROR',percent:0,message:`Не удалось подготовить массовую операцию: ${err.message}`});inventoryWbBulkProgressFinish(wbProgressUi,{error:true});}throw err;}finally{if(wbProgressStop)wbProgressStop.stop=true;if(wbProgressPoll)wbProgressPoll.catch(()=>{});}
  assertInventoryContext(context);
  const actionCodes=effectiveAll?allInStockCodes:barcodes;
  selectedInventoryBarcodes.clear();
  if(['PUBLISH_WB','ADD_TO_WB_FBS'].includes(action)){
    const preparationErrors=result.errors||[];state.inventoryBulkWbErrors=preparationErrors;updateInventoryBulkToolbar();renderInventory();
    const mapPending=Number(result.mapping_pending_count||0),converted=Number(result.converted_storage_units||0),prefix=action==='ADD_TO_WB_FBS'?`В WB FBS переведено ${fmtQty(converted)} шт. · `:'WB FBS: ';
    toast(`${prefix}${result.positions||0} из ${result.requested_positions||result.positions||0} поз. поставлены в очередь.${mapPending?` Для ${mapPending} поз. worker определит chrtId.`:''} Прогресс и итоговая сверка WB показаны справа.`,preparationErrors.length>0);
    const refreshCodes=[...actionCodes];
    inventoryWbBulkProgressRender(wbProgressUi,{stage:'QUEUED',percent:78,message:`Очередь создана: ${fmtQty(result.positions||0)}/${fmtQty(result.requested_positions||result.positions||0)} поз. Начинаем пакетную отправку WB…`,counts:mapPending?`нужно восстановить chrtId: ${fmtQty(mapPending)}`:''});
    await Promise.allSettled([loadContextData(),loadInventoryCatalog({force:true})]);assertInventoryContext(context);
    await monitorInventoryWbBulk(refreshCodes,{ui:wbProgressUi,context,preparationErrors});
    return;
  }
  // FC_K9R60_DIRECT_ZERO_UI_ACK: when server read-after-write verification already
  // confirmed zero, do not show a false waiting-queue state or wait for the fallback queue.
  if(['PAUSE_WB','DELETE_FREE_AND_PAUSE'].includes(action)&&result.wb_direct_confirmed){
    const kept=Number(result.sale_reserved_kept||0)+Number(result.active_reserved_kept||0);
    applyImmediateWbPauseZero(result.wb_direct_zero_codes||actionCodes);
    if(action==='PAUSE_WB')toast(`✓ WB подтвердил остановку продаж сразу: остаток 0 для ${Number((result.wb_direct_zero_codes||actionCodes).length||0)} ШК/размеров. Старые заказы и резервы сохранены.`);
    else toast(`✓ WB подтвердил 0 сразу. Свободный остаток удалён: ${fmtQty(result.deleted_units||0)} шт. · под заказы сохранено: ${fmtQty(kept)} шт.`,Number(result.reservation_shortage||0)>0);
    Promise.allSettled([loadContextData(),loadInventoryCatalog({force:true}),refreshInventoryWbActual({quiet:true})]).catch(()=>{});
    return;
  }
  await Promise.allSettled([loadContextData(),loadInventoryCatalog({force:true})]);
  assertInventoryContext(context);
  if(['PAUSE_WB','RESUME_WB','DELETE_FREE_AND_PAUSE'].includes(action)){
    const kept=Number(result.sale_reserved_kept||0)+Number(result.active_reserved_kept||0);
    const directHint=result.wb_direct_error?` Быстрая отправка 0 не подтвердилась: ${result.wb_direct_error}.`:' ';
    toast(`${result.message||'Команда принята'}${directHint} Ожидаем подтверждение WB…`,Boolean(result.wb_direct_error));
    const confirmation=await waitInventoryControlConfirmation(action,actionCodes,{timeoutMs:30000,context});
    assertInventoryContext(context);
    if(confirmation.state==='CONFIRMED'){
      if(action==='PAUSE_WB')toast(`✓ WB подтвердил остановку продаж: ${confirmation.rows.length} поз. имеют остаток 0. Старые заказы и резервы сохранены.`);
      else if(action==='RESUME_WB')toast(`✓ WB подтвердил возобновление продаж: ${confirmation.rows.length} поз. опубликованы по текущему свободному остатку.`);
      else toast(`✓ WB подтвердил 0. Свободный остаток удалён: ${fmtQty(result.deleted_units||0)} шт. · под заказы сохранено: ${fmtQty(kept)} шт.${Number(result.reservation_shortage||0)>0?' Есть расхождение резерва с физическим остатком.':''}`,Number(result.reservation_shortage||0)>0);
      await refreshInventoryWbActual({quiet:true}).catch(()=>{});
    }else if(confirmation.state==='FAILED'){
      const errors=confirmation.failed.slice(0,3).map(x=>`${x.barcode}: ${x.queue_error||x.publication_error||'ошибка WB'}`).join(' · ');
      toast(`WB не подтвердил операцию: ${confirmation.failed.length} поз. ${errors}`,true);
    }else{
      toast(`Команда сохранена, но WB ещё не подтвердил её. Позиции остаются в надёжной очереди; нажмите «Сверить WB сейчас» через несколько секунд.`,true);
    }
  }
}

async function undoLastInventoryBulkAction(){
  await ensureContext();
  if(!confirm('Отменить ПОСЛЕДНЮЮ массовую операцию в разделе «Остатки»?\n\nДля удаления будут восстановлены исходные ячейки/короба. Для WB будут возвращены остатки, которые были до массового обновления.'))return;
  const result=await api('/receiving/inventory-bulk/undo-last',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null,performed_by:'admin'}),timeoutMs:300000});
  selectedInventoryBarcodes.clear();
  await Promise.allSettled([loadContextData(),loadInventoryCatalog(true)]);
  toast(result.undone_action==='PUBLISH_WB'?`Предыдущие WB FBS остатки возвращены: ${result.restored_wb_positions||0} поз.`:`Удалённые остатки восстановлены: ${fmtQty(result.restored_units||0)} шт.`);
}
function handleInventoryBulkToolbarClick(e){
  const selectBtn=e.target.closest('[data-inventory-select-visible-btn]'),clear=e.target.closest('[data-inventory-clear-selection]'),refreshWb=e.target.closest('[data-inventory-refresh-wb]'),addFbs=e.target.closest('[data-inventory-add-fbs]'),publish=e.target.closest('[data-inventory-bulk-publish]'),pause=e.target.closest('[data-inventory-pause-wb]'),resume=e.target.closest('[data-inventory-resume-wb]'),del=e.target.closest('[data-inventory-bulk-delete]'),delAll=e.target.closest('[data-inventory-delete-all]'),moveMain=e.target.closest('[data-inventory-move-main]'),moveMainAll=e.target.closest('[data-inventory-move-main-all]'),externalUpload=e.target.closest('[data-external-pick-upload]'),externalTemplate=e.target.closest('[data-external-pick-template]'),externalCancel=e.target.closest('[data-external-pick-cancel-last]'),errors=e.target.closest('[data-inventory-wb-errors]'),undo=e.target.closest('[data-inventory-undo-last]');
  try{
    if(selectBtn){inventoryVisibleBarcodes().forEach(code=>selectedInventoryBarcodes.add(code));renderInventory();return;}
    if(clear){selectedInventoryBarcodes.clear();renderInventory();return;}
    if(refreshWb){refreshInventoryWbActual().catch(()=>{});return;}
    if(pause){runInventoryBulkAction('PAUSE_WB').catch(err=>toast(err.message,true));return;}
    if(resume){runInventoryBulkAction('RESUME_WB').catch(err=>toast(err.message,true));return;}
    if(addFbs){runInventoryBulkAction('ADD_TO_WB_FBS').catch(err=>toast(err.message,true));return;}
    if(publish){runInventoryBulkAction('PUBLISH_WB').catch(err=>toast(err.message,true));return;}
    if(del){changeFbsInventory().catch(err=>toast(err.message,true));return;}
    if(delAll){changeFbsInventory({allInventory:true}).catch(err=>toast(err.message,true));return;}
    if(moveMain){changeFbsInventory({mode:'MOVE_MAIN'}).catch(err=>toast(err.message,true));return;}
    if(moveMainAll){changeFbsInventory({allInventory:true,mode:'MOVE_MAIN'}).catch(err=>toast(err.message,true));return;}
    if(externalUpload){document.querySelector('[data-external-pick-file]')?.click();return;}
    if(externalTemplate){downloadExternalFbsPickTemplate();return;}
    if(externalCancel){cancelLastExternalFbsPick().catch(err=>toast(err.message,true));return;}
    if(errors){exportInventoryWbErrors();return;}
    if(undo){undoLastInventoryBulkAction().catch(err=>toast(err.message,true));return;}
  }catch(err){toast(err.message,true);}
}

// FC_R106_MASTERDATA_PICK_ROUTE: old FBS-pick links/bookmarks now open Справочники.
const r106PreviousGo=go;
go=function(page){if(page==='fbspicks')page='masterdata';const result=r106PreviousGo(page);if(page==='masterdata'){installR106MasterdataWorkspace();const n=$('r106PickNumber');if(n)n.value=localStorage.getItem(r106PickStorageKey())||'';}return result;};

// FC_K9R62_INVENTORY_POPUP_SIMPLE_PLACES
function inventoryDetailPlaces(rows){
  const map=new Map();
  for(const x of rows||[]){
    const key=`${x.location_id||''}|${x.container_id||''}`;let r=map.get(key);
    if(!r){r={...x,sku_ids:[],quantity:0,reserved_quantity:0,external_pick_reserved_quantity:0,accounting_pending_quantity:0,storage_only_quantity:0,free_quantity:0};map.set(key,r);}
    if(x.sku_id&&!r.sku_ids.includes(x.sku_id))r.sku_ids.push(x.sku_id);r.quantity+=Number(x.quantity||0);r.reserved_quantity+=Number(x.reserved_quantity||0);r.external_pick_reserved_quantity+=Number(x.external_pick_reserved_quantity||0);r.accounting_pending_quantity+=Number(x.accounting_pending_quantity||0);r.storage_only_quantity+=Number(x.storage_only_quantity||0);r.free_quantity+=Number(x.free_quantity||0);
    for(const k of ['first_stock_at','last_empty_at','last_refilled_at','refilled_after_empty_at'])if(x[k]&&(!r[k]||new Date(x[k])>new Date(r[k])))r[k]=x[k];
    r.refill_count=Math.max(Number(r.refill_count||0),Number(x.refill_count||0));r.empty_count=Math.max(Number(r.empty_count||0),Number(x.empty_count||0));
  }
  return [...map.values()].sort((a,b)=>String(a.location||'').localeCompare(String(b.location||''),'ru',{numeric:true})||String(a.container||'').localeCompare(String(b.container||''),'ru',{numeric:true}));
}
// FC_K9R63_SIMPLE_PLACES_POPUP
let inventoryPopupSequence=0;
let inventoryPopupReturnFocus=null;
function ensureInventoryPopupStyle(){
  if($('inventoryPopupStyle'))return;
  const style=document.createElement('style');style.id='inventoryPopupStyle';
  style.textContent=`
    .inventory-popup-section{margin:16px 0;border:1px solid #e4e7ec;border-radius:12px;padding:14px}
    .inventory-popup-section>summary{font-weight:700;cursor:pointer;padding:4px 0}
    .inventory-popup-section[open]>summary{margin-bottom:12px}
    .inventory-popup-stock{font-size:22px;margin:10px 0;color:#175cd3}
    .inventory-popup-places td{vertical-align:top;padding:12px 8px}
    .inventory-popup-places .primary{min-height:40px;white-space:normal}
    .inventory-cycle-list{list-style:none;padding:0;margin:12px 0;max-height:380px;overflow:auto}
    .inventory-cycle-list li{padding:12px;margin:8px 0;background:#f8fafc;border-left:4px solid #98a2b3;border-radius:8px}
    .inventory-cycle-list li.is-empty{border-left-color:#d92d20;background:#fff5f4}
    .inventory-cycle-list li.is-refilled{border-left-color:#079455;background:#ecfdf3}
    .inventory-cycle-list strong,.inventory-cycle-list small{display:block;margin:3px 0}
    .inventory-cycle-list small{color:#475467}
    .inventory-cycle-list .cycle-amount{font-weight:700}
    #inventoryProductModalBody{min-width:0}
    #inventoryProductModalBody .inventory-detail-head{align-items:flex-start}
    #inventoryProductModalBody .inventory-detail-head>div{min-width:0;overflow-wrap:anywhere}
    #inventoryProductModalBody .table-wrap{overflow:auto}
    @media(max-width:600px){.inventory-popup-places th,.inventory-popup-places td{padding:9px 4px}.inventory-popup-places .primary{font-size:12px;padding:8px}.inventory-popup-section{padding:10px}.inventory-popup-stock{font-size:19px}}
  `;document.head.appendChild(style);
}
function inventoryLifecycleHtml(events,{cell=false}={}){
  if(!events?.length)return '<p class="muted">В доступной истории пока нет полного опустошения или добавления в пустое место.</p>';
  return `<ol class="inventory-cycle-list">${events.map(x=>{
    const empty=x.type==='PLACE_EMPTY',refilled=x.type==='PLACE_REFILLED',place=x.source||x.destination||{};
    const label=x.label||x.comment||(empty?'Товар закончился':refilled?'Товар снова добавлен':'Товар добавлен');
    const where=cell?(x.scope==='BOX'?`Короб: ${x.container||place.container||'—'}`:'Вся ячейка, включая короба'):[place.location,place.container?`Короб: ${place.container}`:'Без короба'].filter(Boolean).join(' · ');
    return `<li class="${empty?'is-empty':refilled?'is-refilled':''}"><small>${esc(x.created_at?new Date(x.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'Дата не записана')}</small><strong>${esc(label)}</strong><div>${esc(where)}</div><div class="cycle-amount">${fmtQty(x.before_qty||0)} → ${fmtQty(x.after_qty||0)} шт.</div>${x.operation_status==='CANCELLED'?'<small>Приёмка позже отменена отдельной корректировкой</small>':''}${x.performed_by?`<small>Сотрудник: ${esc(x.performed_by)}</small>`:''}${x.reference||x.reason?`<small>Основание: ${esc(x.reference||x.reason)}</small>`:''}${!cell&&x.location_id?`<button class="mini secondary" type="button" data-inventory-local-location="${esc(x.location_id)}" data-inventory-local-container="${esc(x.container_id||'')}" >${x.container_id?'Открыть короб':'Перейти в ячейку'}</button>`:''}</li>`;
  }).join('')}</ol>`;
}
function inventoryPlaceLifeHtml(x){
  const dt=v=>v?new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'—';
  if(x.last_empty_at&&(!x.last_refilled_at||new Date(x.last_empty_at)>=new Date(x.last_refilled_at)))return `<small class="subline warn"><b>Товар закончился:</b> ${esc(dt(x.last_empty_at))}</small>`;
  if(x.last_refilled_at)return `<small class="subline good"><b>Товар снова добавлен:</b> ${esc(dt(x.last_refilled_at))}${x.refilled_after_empty_at?`<br>До этого закончился: ${esc(dt(x.refilled_after_empty_at))}`:''}</small>`;
  if(x.first_stock_at)return `<small class="subline"><b>Товар добавлен:</b> ${esc(dt(x.first_stock_at))}</small>`;
  return '';
}
function closeInventoryModal(){
  currentProductPopup=null;
  inventoryPopupSequence++;
  $('inventoryProductModal')?.classList.add('hidden');
  if(inventoryPopupReturnFocus?.isConnected)inventoryPopupReturnFocus.focus();
}
function prepareInventoryPopup(){
  ensureInventoryPopupStyle();
  const modal=$('inventoryProductModal');
  if(modal.classList.contains('hidden'))inventoryPopupReturnFocus=document.activeElement;
  modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');modal.setAttribute('aria-label','Где лежит товар');
  modal.classList.remove('hidden');
  if(!modal.dataset.keyboardReady){modal.dataset.keyboardReady='1';modal.addEventListener('keydown',e=>{
    if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closeInventoryModal();return;}
    if(e.key!=='Tab')return;
    const items=[...modal.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),summary,[tabindex="0"]')].filter(x=>x.getClientRects().length);
    if(!items.length)return;const first=items[0],last=items[items.length-1];
    if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}
    else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}
  });}
  modal.querySelector('[data-close-inventory-modal]')?.focus();
}

let currentProductPopup=null;
async function openInventoryProduct(productId,barcode=''){
  currentProductPopup={productId,barcode};
  const request=++inventoryPopupSequence,context=[state.clientId,state.warehouseId,state.wbWarehouseFilter].join('|');
  prepareInventoryPopup();
  $('inventoryProductModalBody').innerHTML='<div class="empty-state" role="status">Ищем, где лежит товар…</div>';
  try{
    const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId,product_id:productId});if(barcode)q.set('barcode',barcode);const selectedWb=Number(state.wbWarehouseFilter||0)||null;if(selectedWb)q.set('wb_warehouse_id',String(selectedWb));
    const d=await api(`/inventory/catalog/detail?${q.toString()}`,{timeoutMs:90000});
    if(request!==inventoryPopupSequence)return;
    if(context!==[state.clientId,state.warehouseId,state.wbWarehouseFilter].join('|')){closeInventoryModal();return;}
    const p=d.product||{};
    const catalogRow=(state.inventoryCatalog||[]).find(x=>String(x.product_id||'')===String(productId)||String(x.barcode||'')===String(d.barcode||barcode||''));
    const allBarcodes=Array.from(new Set([d.barcode,...(catalogRow?.barcodes||[])].map(x=>String(x||'').trim()).filter(Boolean)));
    const locOptions=assignableLocations().map(x=>`<option value="${x.id}">${esc(x.code)}</option>`).join('');
    const contOptions=['<option value="">Без короба</option>',...activeContainers().map(x=>`<option value="${x.id}" data-location="${x.location_id||''}">${esc(x.name||x.barcode)} · ${esc(locationLabel(x.location_id))}</option>`)].join('');
    const detailPlaces=inventoryDetailPlaces(d.locations||[]).filter(x=>Number(x.quantity||0)>Number(x.storage_only_quantity||0));const locRows=detailPlaces.length?detailPlaces.map(x=>{const reserved=Number(x.reserved_quantity||0),external=Number(x.external_pick_reserved_quantity||0),pending=Number(x.accounting_pending_quantity||0),main=Math.min(Number(x.quantity||0),Number(x.storage_only_quantity||0)),fbs=Math.max(0,Number(x.quantity||0)-main),free=Number(x.free_quantity??Math.max(0,fbs-reserved-external-pending));return `<tr><td><b>${esc(x.location||'Без адреса')}</b><small class="subline">${esc(x.container?'Короб: '+x.container:'Без короба')}</small>${inventoryPlaceLifeHtml(x)}</td><td class="qty"><b>${fmtQty(fbs)} шт. WB FBS</b><small class="subline">резерв ${fmtQty(reserved)}${pending?` · ошибка учёта списания ${fmtQty(pending)}`:''} · свободно WB ${fmtQty(free)}</small></td><td><button type="button" class="mini primary" data-inventory-local-location="${esc(x.location_id||'')}" data-inventory-local-container="${esc(x.container_id||'')}" ${x.location_id?'':'disabled'}>${x.container_id?'Открыть короб':'Перейти в ячейку'}</button>${window.FCStockActions?.buttons({stock_class:'FBS',barcode:d.barcode,article:d.article||'',size:d.size||'',sku_ids:x.sku_ids?.length?x.sku_ids:d.sku_ids,location_id:x.location_id,container_id:x.container_id,seed:{article:d.article||'',size:d.size||'',barcode:d.barcode||'',location:x.location||'',container:x.container||'',quantity:Number(x.quantity||0),free:Number(free||0)}})||''}</td></tr>`;}).join(''):'<tr><td colspan="3" class="empty-state">Остаток 0 — товар сейчас нигде не лежит.</td></tr>';
    const historyBasis=x=>{if(x?.place_lifecycle)return x.comment||'Событие места хранения';const reason=String(x?.reason||'').trim();const reference=String(x?.reference||'').trim();if(reason==='R69_TERMINAL_ORDER_WRITE_OFF')return 'Списано по упакованному / отгруженному заказу · '+reference;if(reason.startsWith('EXTERNAL_PICK_TASK_WRITE_OFF:')){const n=reason.split(':').slice(1).join(':').trim();return `Списано по сборочному листу №${n||reference.replace(/^Сборочное задание №?/i,'').trim()||'—'}`;}if(reason.startsWith('EXTERNAL_PICK_TASK_CANCEL:')){const n=reason.split(':').slice(1).join(':').trim();return `Возврат по отмене сборочного листа №${n||reference.replace(/^Сборочное задание №?/i,'').trim()||'—'}`;}return [reference,reason,x?.performed_by].filter(Boolean).join(' · ')||'—';};
    const movementHistory=(d.history||[]).filter(x=>!x.place_lifecycle);
    const hist=movementHistory.length?movementHistory.map(x=>`<tr${x.place_lifecycle?' class="inventory-life-row"':''}><td>${x.created_at?new Date(x.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'—'}</td><td>${esc(opName(x.type))}</td><td>${x.place_lifecycle?`${fmtQty(x.before_qty||0)} → ${fmtQty(x.after_qty||0)}`:fmtQty(x.quantity)}</td><td>${esc(x.source?.location||'—')}${x.source?.container?' · '+esc(x.source.container):''}</td><td>${esc(x.destination?.location||'—')}${x.destination?.container?' · '+esc(x.destination.container):''}</td><td>${esc(historyBasis(x))}</td></tr>`).join(''):'<tr><td colspan="6" class="empty-state">Истории движения пока нет.</td></tr>';
    const localOnly=Boolean(p.local_only);const fbs=localOnly?{className:'',text:'Внутренний складской товар. WB-действия появятся после привязки карточки.',button:null}:fbsStockPresentation(d.barcode,d.quantity,d.available_to_sell);
    $('inventoryProductModalBody').innerHTML=`<div class="inventory-detail-head">${p.photo?`<img src="${esc(p.photo)}" alt=""/>`:''}<div><span class="kicker">ГДЕ ЛЕЖИТ ТОВАР</span><h2>${esc(p.title||p.article||p.nm_id)}</h2><p>${esc(p.article||'—')} · размер ${esc(d.size||'—')} · ШК ${esc(allBarcodes.join(', ')||d.barcode||'—')}</p><div class="inventory-popup-stock">Физически на складе FBS: <b>${fmtQty(detailPlaces.reduce((n,x)=>n+Math.max(0,Number(x.quantity||0)-Number(x.storage_only_quantity||0)),0))} шт.</b></div><details class="inventory-popup-section"><summary>Резервы и продажи WB</summary><p>Свободно для продажи: <b>${fmtQty(d.available_to_sell||0)} шт.</b></p><div class="good">${window.FCR24History.fbsBadge(d,detailPlaces.reduce((n,x)=>n+Math.max(0,Number(x.quantity||0)-Number(x.storage_only_quantity||0)),0))}</div>${Number(d.reservation_gap_quantity||0)>0?`<div class="bad"><b>Контроль резерва:</b> ${fmtQty(d.reservation_gap_quantity)} шт. уже удержано по активным заказам, журнал резерва восстанавливается.</div>`:''}<div class="inventory-movement-truth">${stockMovementHtml(d)}</div><div class="fbs-stock-state ${esc(fbs.className)}"><span>${esc(fbs.text)}</span>${fbs.button?`<button class="mini primary" type="button" id="inventoryModalPublishBtn">${esc(fbs.button)}</button>`:''}</div></details></div></div>
      <div class="inventory-popup-places"><h3>Ячейки и короба</h3><p class="muted">Откройте конкретный короб или ячейку без короба. Перейти к целой палете можно внутри короба.</p><div class="table-wrap"><table class="inventory-popup-places"><thead><tr><th>Ячейка / короб</th><th>Остаток</th><th></th></tr></thead><tbody>${locRows}</tbody></table></div></div>
      <details class="inventory-popup-section"><summary>Когда товар закончился и появился снова</summary><p class="muted">События относятся к этому товару в указанном месте. Полная история заполнения короба и ячейки — внутри ячейки.</p>${inventoryLifecycleHtml(d.place_history||(d.history||[]).filter(x=>x.place_lifecycle))}</details>
      <div class="inventory-popup-section">${window.FCStockActions?.buttons({stock_class:'FBS',barcode:d.barcode,sku_ids:d.sku_ids},true)||''}${window.FCStockActions?.accountingButton(d.sku_ids)||''}</div>
      <details class="inventory-popup-section"><summary>Последние движения товара</summary><div class="table-wrap"><table><thead><tr><th>Дата</th><th>Операция</th><th>Кол-во</th><th>Откуда</th><th>Куда</th><th>Основание</th></tr></thead><tbody>${hist}</tbody></table></div></details>`;
    $('inventoryProductModal').classList.remove('hidden');
    $('inventoryModalPublishBtn')?.addEventListener('click',()=>publishInventoryBarcode(productId,d.barcode,d.quantity,true).catch(err=>toast(err.message,true)));
    $('inventoryProductModalBody').querySelectorAll('[data-inventory-local-location]').forEach(btn=>btn.addEventListener('click',()=>{const id=btn.dataset.inventoryLocalLocation,box=btn.dataset.inventoryLocalContainer;if(!id&&!box)return;closeInventoryModal();if(box){window.FCStockRooms.openBox(box,state.activePage==='mainstock'?'MAIN':'FBS').catch(err=>toast(err.message,true));return;}go('locations');openLocationContents(id).catch(err=>toast(err.message,true));}));

  }catch(e){
    if(request!==inventoryPopupSequence)return;
    $('inventoryProductModalBody').innerHTML=`<div class="error-box"><b>Не удалось загрузить места хранения</b><p>${esc(e.message)}</p><button type="button" class="primary" id="inventoryPopupRetry">Попробовать ещё раз</button></div>`;
    $('inventoryPopupRetry').addEventListener('click',()=>openInventoryProduct(productId,barcode));
  }
}

const selectedLocationIds=new Set();
const locationBrowser={level:'ROOT',storage:null,rackKey:'',floor:'',locationId:'',historyData:null,historyLoading:false,historyRequest:'',contentsData:null,contentsLoading:false,contentsRequest:''};
function physicalLocations(){return assignableLocations().filter(l=>!['БЕЗ-АДРЕСА','ПРИЕМКА','ПРИЕМКА-ПАЛЕТЫ','UNPLACED'].includes(String(l.code||'').trim().toUpperCase()));}
function naturalLocationCompare(a,b){return String(a||'').localeCompare(String(b||''),'ru',{numeric:true,sensitivity:'base'});}
function namedLocationPart(name,label){const match=String(name||'').match(new RegExp(`${label}\\s+([^·]+)`,'iu'));return String(match?.[1]||'').trim();}
function cleanLocationNumber(value,fallback=''){const text=String(value||'').trim();return /^\d+$/.test(text)?String(Number(text)):text||fallback;}
function locationStorageMeta(location){
  const code=String(location?.code||''),name=String(location?.name||''),parts=code.split('-').filter(Boolean),type=String(location?.location_type||'').toUpperCase(),isPallet=['PALLET','PALLET_PLACE'].includes(type),isPalletPlace=type==='PALLET_PLACE';
  const namedAisle=namedLocationPart(name,'Проход'),section=cleanLocationNumber(namedLocationPart(name,'Секция'));
  if(isPallet){const marker=parts.findIndex(x=>['PAL','PP'].includes(String(x).toUpperCase())),aisle=namedAisle,rawPlace=isPalletPlace?namedLocationPart(name,'Палеточное место'):namedLocationPart(name,'Палета'),value=cleanLocationNumber(rawPlace||(marker>=0?parts.slice(marker+1).join('-'):code),code);return {location,isPallet:true,isPalletPlace,section,aisle,pallet:isPalletPlace?'':value,palletPlace:isPalletPlace?value:'',rack:'',floor:'',cell:'',rackKey:''};}
  const namedRack=namedLocationPart(name,'Стеллаж'),namedFloor=namedLocationPart(name,'Этаж'),namedCell=namedLocationPart(name,'Ячейка'),hasNamedRack=Boolean(namedRack||namedFloor);
  const codeRack=hasNamedRack&&parts.length>=3?parts.at(-3):'',codeFloor=hasNamedRack&&parts.length>=3?parts.at(-2):'',codeCell=parts.length?parts.at(-1):code,codeAisle='';
  const rack=cleanLocationNumber(namedRack||codeRack,'Без стеллажа'),floor=cleanLocationNumber(namedFloor||codeFloor,'Без этажа'),cell=cleanLocationNumber(namedCell||codeCell,code),aisle=namedAisle||codeAisle;
  return {location,isPallet:false,isPalletPlace:false,section,aisle,rack,floor,cell,pallet:'',palletPlace:'',rackKey:`${section}␟${aisle}␟${rack}`};
}
function locationMetrics(location){
  const id=String(location.id),balances=state.inventory.filter(x=>String(x.location_id)===id&&Number(x.quantity)>0),occupancy=locationOccupancy(id),currentBoxes=stockedActiveContainers().filter(x=>String(x.location_id)===id).length;
  const qty=Number(occupancy?.quantity??balances.reduce((sum,x)=>sum+Number(x.quantity||0),0)),boxes=Math.max(Number(occupancy?.container_count||0),currentBoxes),skus=Number(occupancy?.sku_count??new Set(balances.map(x=>x.sku_id)).size),owners=locationOwnerNames(id),otherOwners=locationOtherOwners(id),volumeCm3=Number(occupancy?.volume_cm3||0),weightKg=Number(occupancy?.weight_kg||0),knownQty=Number(occupancy?.volume_known_quantity||0),unknownQty=Number(occupancy?.volume_unknown_quantity||0),fillPct=Math.max(0,volumeCm3/96000*100);
  return {qty,boxes,skus,owners,otherOwners,volumeCm3,weightKg,knownQty,unknownQty,fillPct,occupied:qty>0||boxes>0};
}
function locationGroupMetrics(locations){return locations.reduce((result,location)=>{const item=locationMetrics(location);result.addresses+=1;result.occupied+=item.occupied?1:0;result.boxes+=item.boxes;result.qty+=item.qty;return result;},{addresses:0,occupied:0,boxes:0,qty:0});}
async function printLocationLabels({ids=[],from='',to='',all=false}={},options={}){
  try{
    await ensureContext();
    if(!ids.length&&!from&&!to&&!all)throw new Error('Выберите хотя бы один адрес');
    const ps=wbPrintSettings(),useAgent=options.forceAgent||ps.mode==='agent';
    if(useAgent){
      const result=await api('/layout/locations/labels/queue',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,print_station:ps.station,location_ids:ids,code_from:from||null,code_to:to||null,all_locations:Boolean(all),width_mm:58,height_mm:40}),timeoutMs:90000});
      toast(`QR-этикетки отправлены в Print Agent ${result.print_station}: ${result.labels_queued} шт.`);
      return result;
    }
    const blob=await apiBlob('/layout/locations/labels.pdf',{method:'POST',body:JSON.stringify({warehouse_id:state.warehouseId,location_ids:ids,code_from:from||null,code_to:to||null,all_locations:Boolean(all),width_mm:58,height_mm:40}),timeoutMs:90000});
    printBlob(blob,'FulfillCore_location_labels_58x40.pdf');
    toast(`PDF с QR-этикетками подготовлен${ids.length?`: ${ids.length} шт.`:''}`);
    return {ok:true,pdf:true,labels_queued:ids.length};
  }catch(error){toast(error.message,true);return null;}
}
async function adjustStockInOpenedCell(button,direction){await ensureContext();const qtyRaw=prompt(`${direction>0?'Добавить':'Списать'} сколько шт.?`,'1');if(qtyRaw===null)return;const qty=Number(String(qtyRaw).replace(',','.'));if(!Number.isFinite(qty)||qty<=0)throw new Error('Количество должно быть больше 0');const current=Number(button.dataset.quantity||0);if(direction<0&&qty>current)throw new Error(`В этом месте только ${fmtQty(current)} шт.`);const publish=confirm('Сразу синхронизировать новый остаток с WB FBS?\n\nОК — FulfillCore + WB FBS. Отмена — только FulfillCore.');const payload={client_id:state.clientId,warehouse_id:state.warehouseId,barcode:button.dataset.barcode,location_id:button.dataset.locationId,container_id:button.dataset.containerId||null,delta:direction*qty,performed_by:$('currentUser')?.textContent||null,reason:direction>0?'Добавление прямо из ячейки':'Списание прямо из ячейки'};if(publish)await api('/receiving/inventory-adjust-and-publish',{method:'POST',body:JSON.stringify({...payload,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null})});else await api('/inventory/catalog/adjust',{method:'POST',body:JSON.stringify(payload)});await loadContextData();state.inventoryCatalogLoaded=false;await loadInventoryCatalog();await openLocationContents(button.dataset.locationId);toast(`${direction>0?'Добавлено':'Списано'} ${fmtQty(qty)} шт.${publish?' · WB подтверждён':''}`);}
function locationItemRows(balances){return balances.map(balance=>{const sku=balance.article!==undefined?balance:mapBy(state.skus,balance.sku_id)||{},barcode=balance.barcode||sku.barcode||'—',raw=balance.raw_barcode||sku.barcode||'',duplicated=Boolean(balance.barcode_was_duplicated),name=balance.name||sku.name||'Неизвестный товар',locationId=balance.location_id||locationBrowser.locationId,addressReserved=Number(balance.reserved_quantity||0),addressPending=Number(balance.accounting_pending_quantity||0),addressFree=Number(balance.free_quantity??Math.max(0,Number(balance.quantity||0)-addressReserved-addressPending)),warehousePhysical=Number((balance.warehouse_quantity??balance.quantity)||0),saleReserved=Number(balance.sale_reserved_quantity||0),pickingReserved=Number(balance.picking_reserved_quantity||0),warehouseReserved=Number(balance.warehouse_reserved_quantity??(saleReserved+pickingReserved)),warehousePending=Number(balance.warehouse_accounting_pending_quantity||0),warehouseFree=Number(balance.warehouse_free_quantity??Math.max(0,warehousePhysical-warehouseReserved-warehousePending)),warehouseReceived=Number(balance.warehouse_received_quantity||0),warehouseShipped=Number(balance.warehouse_shipped_quantity||0),warehouseAdjIn=Number(balance.warehouse_adjustment_in_quantity||0),warehouseAdjOut=Number(balance.warehouse_adjustment_out_quantity||0),warehouseOpening=Number(balance.warehouse_opening_balance_estimate||0),addressPendingText=addressPending?` · ошибка учёта списания ${fmtQty(addressPending)}`:'',warehousePendingText=warehousePending?` · <span class="bad">ошибка учёта списания ${fmtQty(warehousePending)}</span>`:'',warehouseAdjustText=(warehouseAdjIn||warehouseAdjOut)?` · корректировки +${fmtQty(warehouseAdjIn)} / −${fmtQty(warehouseAdjOut)}`:'',warehouseHistoryGap=Math.abs(warehouseOpening)>0.0001?` · <span class="bad">без записи в журнале ${warehouseOpening>0?'+':''}${fmtQty(warehouseOpening)}</span>`:'',reservationHint=`<small class="subline"><b>В этой ячейке:</b> закреплено ${fmtQty(addressReserved)}${addressPendingText} · свободно ${fmtQty(addressFree)}</small><small class="subline good"><b>По товару на складе:</b> физически ${fmtQty(warehousePhysical)} · <b>резерв всего ${fmtQty(warehouseReserved)}</b> (Новые ${fmtQty(saleReserved)} · На сборке ${fmtQty(pickingReserved)}) · свободно ${fmtQty(warehouseFree)}${warehousePendingText} · принято ${fmtQty(warehouseReceived)} · списано ${fmtQty(warehouseShipped)}${warehouseAdjustText}${warehouseHistoryGap}</small>`,productId=balance.product_id||sku.product_id||'';return `<tr class="${duplicated?'stock-barcode-error':''}"><td><b>${esc(balance.article||sku.article||sku.sku_code||'—')}</b></td><td>${esc(name)}${duplicated?'<small class="subline bad">Ранее принят с задвоенным ШК</small>':''}</td><td>${esc(balance.size||sku.size||'—')}</td><td><b>${esc(barcode)}</b>${duplicated&&raw!==barcode?`<small class="subline bad">Было: ${esc(raw)}</small>`:''}</td><td class="qty"><b>${fmtQty(balance.quantity)} шт. в этой ячейке</b>${reservationHint}${window.FCStockActions?.buttons({barcode,article:balance.article||sku.article||sku.sku_code||'',size:balance.size||sku.size||'',sku_ids:balance.sku_id?[balance.sku_id]:[],location_id:locationId,container_id:balance.container_id||null,seed:{article:balance.article||sku.article||sku.sku_code||'',size:balance.size||sku.size||'',barcode,location:balance.location||mapBy(state.locations,locationId)?.code||'',container:balance.container||'',quantity:Number(balance.quantity||0),free:addressFree}})||''}</td><td><div class="row-actions">${productId?`<button type="button" class="mini secondary" data-location-product-card data-product-id="${esc(productId)}" data-product-barcode="${esc(barcode)}">Карточка товара</button>`:''}</div></td></tr>`;}).join('');}
function locationItemsTable(balances,emptyText){const rows=locationItemRows(balances);return `<div class="table-wrap"><table class="storage-items-table"><thead><tr><th>Артикул</th><th>Товар</th><th>Размер</th><th>ШК</th><th>Количество</th><th>Исправление</th></tr></thead><tbody>${rows||`<tr><td colspan="6" class="empty-state">${esc(emptyText)}</td></tr>`}</tbody></table></div>`;}
function locationBreadcrumbs(searching=false){
  const pieces=[{label:'Хранение',level:'ROOT'}],current=mapBy(state.locations,locationBrowser.locationId),isReceiving=Boolean(current&&String(current.code||current.barcode||'').trim().toUpperCase()==='ПРИЕМКА');
  if(searching)pieces.push({label:'Результаты поиска'});
  else if(isReceiving){pieces.push({label:'ПРИЕМКА · требует размещения',level:'DETAIL'});if(locationBrowser.level==='HISTORY')pieces.push({label:'История'});}
  else if(locationBrowser.storage==='CELL'){
    pieces.push({label:'Ячеечное хранение',level:'RACKS'});
    const metas=physicalLocations().filter(x=>!locationStorageMeta(x).isPallet).map(locationStorageMeta),rackMeta=metas.find(x=>x.rackKey===locationBrowser.rackKey);
    if(locationBrowser.rackKey)pieces.push({label:rackMeta?.rack==='Без стеллажа'?'Отдельные ячейки':`Стеллаж ${rackMeta?.rack||'—'}`,level:'FLOORS'});
    if(locationBrowser.floor)pieces.push({label:`Этаж / полка ${locationBrowser.floor}`,level:'CELLS'});
  }else if(locationBrowser.storage==='PALLET')pieces.push({label:'Палетное хранение',level:'PALLETS'});
  if(!isReceiving){const location=current;if(location&&['DETAIL','HISTORY'].includes(locationBrowser.level)){const meta=locationStorageMeta(location),label=meta.isPalletPlace?'Палеточное место':meta.isPallet?'Палета':'Ячейка';pieces.push({label:`${label} ${location.code}`,level:'DETAIL'});}if(locationBrowser.level==='HISTORY')pieces.push({label:'История'});}
  return `<nav class="storage-breadcrumbs">${pieces.map((piece,index)=>index<pieces.length-1&&piece.level?`<button type="button" data-location-nav="${esc(piece.level)}">${esc(piece.label)}</button><span>›</span>`:`<b>${esc(piece.label)}</b>`).join('')}</nav>`;
}
function storageChoiceCard(type,title,subtitle,metrics,groups){return `<button type="button" class="storage-choice" data-location-storage="${type}"><span class="storage-choice-icon">${type==='CELL'?'▦':'▤'}</span><span><b>${esc(title)}</b><small>${esc(subtitle)}</small></span><div class="storage-choice-stats"><strong>${metrics.addresses}</strong><small>${type==='CELL'?'ячеек':'мест'}</small><strong>${groups}</strong><small>${type==='CELL'?'групп':'занято'}</small><strong>${fmtQty(metrics.qty)}</strong><small>товаров</small></div><em>Открыть →</em></button>`;}
function receivingLocation(){return activeLocations().find(x=>String(x.code||x.barcode||'').trim().toUpperCase()==='ПРИЕМКА')||null;}
function receivingClientMetrics(location){
  // Virtual ПРИЕМКА is *only* loose stock. A product already inside a physical
  // box is placed in that box even when the box currently stands at ПРИЕМКА.
  if(!location)return {qty:0,skus:0};const id=String(location.id),balances=state.inventory.filter(x=>String(x.location_id)===id&&!x.container_id&&Number(x.quantity)>0);return {qty:balances.reduce((sum,x)=>sum+Number(x.quantity||0),0),skus:new Set(balances.map(x=>String(x.sku_id||''))).size};
}
function receivingRootCard(){
  const location=receivingLocation(),metrics=receivingClientMetrics(location);return `<section class="storage-receiving-queue"><div><span class="kicker">ВИРТУАЛЬНАЯ ЯЧЕЙКА</span><h3>ПРИЕМКА · требует размещения</h3><p>Здесь показывается только товар БЕЗ физического короба: например, возврат после отмены WB между сборочным листом и упаковкой. Товар, принятый в настоящий короб, уже считается размещённым в этом коробе и здесь не висит. Сам короб позже можно целиком переместить в ячейку или на палету.</p></div><div class="storage-receiving-stats"><b>${fmtQty(metrics.qty)} шт.</b><span>${metrics.skus} SKU</span>${location?`<button type="button" class="primary" data-location-open="${esc(location.id)}">Открыть ПРИЕМКА</button>`:'<span>Пока пусто</span>'}</div></section>`;
}
function renderStorageRoot(cells,pallets){
  const cellMetas=cells.map(locationStorageMeta),rackCount=new Set(cellMetas.map(x=>x.rackKey)).size,cellMetrics=locationGroupMetrics(cells),palletMetrics=locationGroupMetrics(pallets);
  return `${receivingRootCard()}<div class="storage-intro"><h3>Выберите вид хранения</h3><p>FulfillCore поддерживает отдельные ячейки, стеллажи, секции, палеты и фиксированные палеточные места. Используйте только те уровни, которые реально есть на вашем складе.</p></div><div class="storage-choice-grid">${storageChoiceCard('CELL','Ячеечное хранение','Секция (если нужна) → стеллаж / отдельная ячейка',cellMetrics,rackCount)}${storageChoiceCard('PALLET','Палетное хранение','Секция (если нужна) → палета / палеточное место → короб → товар',palletMetrics,palletMetrics.occupied)}</div>`;
}
function renderRackGroups(cells){
  const groups=new Map();for(const location of cells){const meta=locationStorageMeta(location);if(!groups.has(meta.rackKey))groups.set(meta.rackKey,{meta,locations:[]});groups.get(meta.rackKey).locations.push(location);}
  const rows=[...groups.values()].sort((a,b)=>naturalLocationCompare(`${a.meta.aisle}-${a.meta.rack}`,`${b.meta.aisle}-${b.meta.rack}`));
  return rows.length?`<div class="storage-section-head"><div><h3>Группы ячеек</h3><p>Ячейки могут быть в стеллаже или существовать напрямую без стеллажа.</p></div><span>${rows.length} групп</span></div><div class="storage-card-grid">${rows.map(group=>{const floors=new Set(group.locations.map(x=>locationStorageMeta(x).floor)).size,metrics=locationGroupMetrics(group.locations),direct=group.meta.rack==='Без стеллажа';return `<button type="button" class="storage-group-card" data-location-rack="${esc(encodeURIComponent(group.meta.rackKey))}"><span class="tag">${direct?'ЯЧЕЙКИ':'СТЕЛЛАЖ'}</span><h3>${direct?'Отдельные ячейки':esc(group.meta.rack)}</h3><p>${group.meta.section?`Секция ${esc(group.meta.section)}${group.meta.aisle?` · проход ${esc(group.meta.aisle)}`:''}`:group.meta.aisle?`Проход ${esc(group.meta.aisle)}`:'Без секции и прохода'}</p><div class="storage-group-stats"><span><b>${floors}</b> уров.</span><span><b>${metrics.addresses}</b> ячеек</span><span><b>${metrics.occupied}</b> занято</span><span><b>${fmtQty(metrics.qty)}</b> шт.</span></div><em>Открыть ячейки →</em></button>`;}).join('')}</div>`:'<div class="empty-state">Ячеек пока нет. Создайте стеллаж или отдельные ячейки в форме справа.</div>';
}
function renderFloorGroups(cells){
  const rackCells=cells.filter(x=>locationStorageMeta(x).rackKey===locationBrowser.rackKey),groups=new Map();for(const location of rackCells){const floor=locationStorageMeta(location).floor;if(!groups.has(floor))groups.set(floor,[]);groups.get(floor).push(location);}
  const rows=[...groups.entries()].sort((a,b)=>naturalLocationCompare(a[0],b[0]));
  return rows.length?`<div class="storage-section-head"><div><h3>Этажи / полки</h3><p>Выберите этаж, чтобы открыть находящиеся на нём ячейки.</p></div><span>${rows.length} шт.</span></div><div class="storage-card-grid">${rows.map(([floor,locations])=>{const metrics=locationGroupMetrics(locations);return `<button type="button" class="storage-group-card floor-card" data-location-floor="${esc(encodeURIComponent(floor))}"><span class="tag">ЭТАЖ / ПОЛКА</span><h3>${esc(floor)}</h3><div class="storage-group-stats"><span><b>${metrics.addresses}</b> ячеек</span><span><b>${metrics.occupied}</b> занято</span><span><b>${metrics.boxes}</b> короб.</span><span><b>${fmtQty(metrics.qty)}</b> шт.</span></div><em>Открыть ячейки →</em></button>`;}).join('')}</div>`:'<div class="empty-state">На этом стеллаже этажей не найдено.</div>';
}
function storageFillHtml(metrics){
  const rawPct=Number(metrics.fillPct||0),pct=Math.min(100,rawPct),shown=pct,known=Number(metrics.knownQty||0),unknown=Number(metrics.unknownQty||0);
  if(!(metrics.qty>0))return '<div class="storage-fill storage-fill-empty"><b>Заполнение 0%</b><progress max="100" value="0"></progress><small>Можно размещать товар</small></div>';
  if(!(known>0))return `<div class="storage-fill storage-fill-unknown"><b>Заполнение: нет габаритов WB</b><progress max="100" value="0"></progress><small>${fmtQty(unknown||metrics.qty)} шт. без данных о габаритах</small></div>`;
  const status=pct>=95?'Полный':pct>=75?'Почти полный':pct>=45?'Средняя загрузка':'Есть свободное место',overflow=rawPct>100?' · габариты WB дают объём больше короба, поэтому индикатор ограничен 100%':'';
  return `<div class="storage-fill ${pct>=95?'storage-fill-full':pct>=75?'storage-fill-high':''}"><b>${status} · ${pct.toLocaleString('ru-RU',{maximumFractionDigits:1})}%</b><progress max="100" value="${shown}"></progress><small>${(metrics.volumeCm3/1000).toLocaleString('ru-RU',{maximumFractionDigits:1})} расчётного объёма · короб 96 л${metrics.weightKg?` · ≈ ${metrics.weightKg.toLocaleString('ru-RU',{maximumFractionDigits:1})} кг`:''}${unknown?` · ещё ${fmtQty(unknown)} шт. без габаритов`:''}${overflow}</small></div>`;
}
function storageAddressCard(location){
  const meta=locationStorageMeta(location),metrics=locationMetrics(location),selected=selectedLocationIds.has(String(location.id));
  const typeLabel=meta.isPalletPlace?'ПАЛЕТОМЕСТО':meta.isPallet?'ПАЛЕТА':'ЯЧЕЙКА';
  const placeValue=meta.isPalletPlace?meta.palletPlace:meta.pallet;
  const placeText=meta.isPallet?(meta.section?`Секция ${esc(meta.section)} · ${meta.isPalletPlace?'палеточное место':'палета'} ${esc(placeValue)}`:(meta.aisle?`Проход ${esc(meta.aisle)} · ${meta.isPalletPlace?'палеточное место':'палета'} ${esc(placeValue)}`:'Палетное хранение')):`${meta.section?`Секция ${esc(meta.section)} · `:''}${meta.rack==='Без стеллажа'?'Отдельная ячейка':`Стеллаж ${esc(meta.rack)} · этаж ${esc(meta.floor)} · ячейка`} ${esc(meta.cell)}`;
  return `<article class="storage-address-card ${selected?'selected':''} ${metrics.otherOwners.length?'occupied-other':''}"><div class="storage-address-top"><label><input type="checkbox" data-location-select="${esc(location.id)}" ${selected?'checked':''}/> выбрать</label><span class="tag">${typeLabel}</span></div><h3 class="fc27-public-code">${esc(meta.publicCode)}</h3><p>${placeText}</p><small class="location-owner ${metrics.otherOwners.length?'other':''}">${metrics.owners.length?`Клиент: ${esc(metrics.owners.join(', '))}`:'Свободна'}</small><div class="storage-address-stats"><span>${metrics.skus} SKU</span><span>${metrics.boxes} короб.</span><span>${fmtQty(metrics.qty)} шт.</span></div>${storageFillHtml(metrics)}<div class="location-actions"><button class="mini primary" type="button" data-location-open="${esc(location.id)}">Открыть</button><button class="mini secondary" type="button" data-location-print="${esc(location.id)}">QR-этикетка</button><button class="mini danger" type="button" data-location-delete="${esc(location.id)}" data-location-code="${esc(location.code)}">Удалить</button></div></article>`;
}
function selectedLocationsToolbar(){const count=selectedLocationIds.size;return `<div class="storage-selection-toolbar"><b>Выбрано адресов: ${count}</b><div class="button-row"><button type="button" class="secondary" data-location-print-selected ${count?'':'disabled'}>Печать выбранных QR</button><button type="button" class="danger" data-location-delete-selected ${count?'':'disabled'}>Удалить выбранные пустые</button></div></div>`;}
function locationRangeToolbar(list){
  const fromOptions=list.map((location,index)=>{const meta=locationStorageMeta(location);return `<option value="${esc(location.id)}" ${index===0?'selected':''}>${esc(meta.cell)} — ${esc(location.code)}</option>`;}).join('');
  const toOptions=list.map((location,index)=>{const meta=locationStorageMeta(location);return `<option value="${esc(location.id)}" ${index===list.length-1?'selected':''}>${esc(meta.cell)} — ${esc(location.code)}</option>`;}).join('');
  return `<div class="storage-range-toolbar" data-location-range-toolbar><div><b>Печать диапазона ячеек</b><small>Выберите первую и последнюю ячейку на этой полке — будут напечатаны все QR между ними.</small></div><div class="storage-range-controls"><label>С ячейки<select data-location-range-from>${fromOptions}</select></label><label>По ячейку<select data-location-range-to>${toOptions}</select></label><button type="button" class="primary" data-location-print-range>Печать диапазона</button></div></div>`;
}
function renderCellAddresses(cells){const list=cells.filter(x=>{const meta=locationStorageMeta(x);return meta.rackKey===locationBrowser.rackKey&&meta.floor===locationBrowser.floor;}).sort((a,b)=>naturalLocationCompare(locationStorageMeta(a).cell,locationStorageMeta(b).cell));return list.length?`<div class="storage-section-head"><div><h3>Ячейки этажа</h3><p>Откройте ячейку, чтобы увидеть все короба и товар внутри.</p></div><span>${list.length} шт.</span></div>${locationRangeToolbar(list)}${selectedLocationsToolbar()}<div class="storage-address-grid">${list.map(storageAddressCard).join('')}</div>`:'<div class="empty-state">На этом этаже ячеек не найдено.</div>';}
function renderPalletAddresses(pallets){
  const list=[...pallets].sort((a,b)=>{const am=locationStorageMeta(a),bm=locationStorageMeta(b),av=am.palletPlace||am.pallet,bv=bm.palletPlace||bm.pallet;return naturalLocationCompare(am.section,bm.section)||naturalLocationCompare(av,bv);});
  if(!list.length)return '<div class="empty-state">Палет и палеточных мест пока нет. Создайте нужный тип хранения в форме справа.</div>';
  const hasSections=list.some(x=>locationStorageMeta(x).section),placeCount=list.filter(x=>locationStorageMeta(x).isPalletPlace).length,palletCount=list.length-placeCount;
  const summary=[palletCount?`${palletCount} палет`:null,placeCount?`${placeCount} палетомест`:null].filter(Boolean).join(' · ');
  if(!hasSections)return `<div class="storage-section-head"><div><h3>Палетное хранение</h3><p>Здесь вместе видны обычные палеты и фиксированные палеточные места.</p></div><span>${esc(summary||String(list.length))}</span></div>${selectedLocationsToolbar()}<div class="storage-address-grid">${list.map(storageAddressCard).join('')}</div>`;
  const groups=new Map();for(const location of list){const section=locationStorageMeta(location).section||'Без секции';if(!groups.has(section))groups.set(section,[]);groups.get(section).push(location);}
  return `<div class="storage-section-head"><div><h3>Палетное хранение по секциям</h3><p>Секция используется только там, где она реально задана. Внутри могут быть палеты и/или палеточные места.</p></div><span>${esc(summary||String(list.length))}</span></div>${selectedLocationsToolbar()}<div class="storage-pallet-sections">${[...groups.entries()].sort((a,b)=>naturalLocationCompare(a[0],b[0])).map(([section,items])=>`<section class="storage-pallet-section"><div class="storage-pallet-section-head"><div><span class="kicker">СЕКЦИЯ</span><h3>${esc(section)}</h3></div><span>${items.length} мест</span></div><div class="storage-address-grid">${items.map(storageAddressCard).join('')}</div></section>`).join('')}</div>`;
}
function renderLocationSearch(query,locations){
  const q=query.toLocaleLowerCase('ru-RU');
  const matchingBoxLocationIds=new Set(stockedActiveContainers().filter(container=>[container.name,container.barcode].join(' ').toLocaleLowerCase('ru-RU').includes(q)).map(container=>String(container.location_id||'')).filter(Boolean));
  const list=locations.filter(location=>{const meta=locationStorageMeta(location),haystack=[location.code,location.barcode,location.name,meta.section,meta.aisle,meta.rack,meta.floor,meta.cell,meta.pallet,meta.palletPlace,...locationOwnerNames(location.id)].join(' ').toLocaleLowerCase('ru-RU');return haystack.includes(q)||matchingBoxLocationIds.has(String(location.id));}).sort((a,b)=>naturalLocationCompare(a.code,b.code));
  return `<div class="storage-section-head"><div><h3>Результаты поиска</h3><p>Ищем по секции, палете, ячейке, QR и номеру короба. Откройте найденный адрес, чтобы увидеть его содержимое.</p></div><span>${list.length} найдено</span></div>${list.length?`${selectedLocationsToolbar()}<div class="storage-address-grid">${list.map(storageAddressCard).join('')}</div>`:'<div class="empty-state">По этому запросу адресов или коробов не найдено.</div>'}`;
}
function receivingQueueTable(data){
  const items=data?.items||[];if(!items.length)return '<div class="empty-state">ПРИЕМКА пустая — товара, требующего повторного размещения, нет.</div>';
  const rows=items.map(item=>`<tr><td><b>${esc(item.article||'—')}</b></td><td>${esc(item.size||'—')}</td><td><b>${esc(item.barcode||'—')}</b></td><td class="qty">${fmtQty(item.quantity)}</td><td>${esc(item.reason||'Требуется размещение')}</td><td>${esc((item.orders||[]).filter(Boolean).join(', ')||'—')}</td><td>${item.received_at?new Date(item.received_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'—'}</td><td><div class="row-actions">${item.product_id?`<button type="button" class="mini secondary" data-location-product-card data-product-id="${esc(item.product_id)}" data-product-barcode="${esc(item.barcode||'')}">Карточка</button>`:''}<button type="button" class="mini primary" data-receiving-place data-sku-id="${esc(item.sku_id)}" data-barcode="${esc(item.barcode||'')}" data-quantity="${esc(item.quantity)}" data-source-location-id="${esc(data.location_id||'')}">Разместить</button><button type="button" class="mini danger" data-receiving-remove data-sku-id="${esc(item.sku_id)}" data-barcode="${esc(item.barcode||'')}" data-item-name="${esc([item.article,item.size].filter(Boolean).join(' · ')||item.barcode||'товар')}" data-quantity="${esc(item.quantity)}" data-container-id="${esc(item.container_id||'')}" data-source-location-id="${esc(data.location_id||'')}">Удалить</button></div></td></tr>`).join('');
  return `<section class="storage-receiving-details"><div class="storage-section-head"><div><h3>Товар, который нужно разместить</h3><p>Нажмите «Разместить», чтобы перенести товар в реальный адрес. Если строка попала сюда ошибочно, «Удалить» позволяет списать всё количество или только часть; операция останется в истории.</p></div><span>${fmtQty(data.quantity||0)} шт.</span></div><div class="table-wrap"><table class="storage-items-table"><thead><tr><th>Артикул</th><th>Размер</th><th>ШК</th><th>Кол-во</th><th>Причина</th><th>Заказ WB</th><th>Когда попал</th><th>Действие</th></tr></thead><tbody>${rows}</tbody></table></div></section>`;
}
function renderLocationDetail(location){
  const id=String(location.id),meta=locationStorageMeta(location),isReceiving=String(location.code||location.barcode||'').trim().toUpperCase()==='ПРИЕМКА',metrics=isReceiving?{...locationMetrics(location),otherOwners:[]}:locationMetrics(location),authoritative=String(locationBrowser.contentsData?.location?.id||'')===id?locationBrowser.contentsData.items:null,balances=Array.isArray(authoritative)?authoritative:state.inventory.filter(x=>String(x.location_id)===id&&Number(x.quantity)>0),direct=balances.filter(x=>!x.container_id),containers=stockedActiveContainers().filter(x=>String(x.location_id)===id).sort((a,b)=>naturalLocationCompare(a.name||a.barcode,b.name||b.barcode));
  const boxes=containers.map(container=>{const contents=balances.filter(x=>String(x.container_id)===String(container.id)),qty=contents.reduce((sum,x)=>sum+Number(x.quantity||0),0);return `<section class="storage-box-card ${contents.length?'':'empty-box'}"><div class="storage-box-head"><div><span class="kicker">КОРОБ</span><h3>${esc(containerPublicCode(container))}</h3></div><span class="${contents.length?'tag':'storage-empty-tag'}">${contents.length?`${fmtQty(qty)} шт.`:'Короб пуст'}</span></div>${locationItemsTable(contents,'Короб пуст')}</section>`;}).join('');
  const ownerText=isReceiving?'Общая виртуальная зона · показан товар выбранного клиента':metrics.owners.length?`Клиент адреса: ${metrics.owners.join(', ')}`:`${meta.isPalletPlace?'Палеточное место':meta.isPallet?'Палета':'Ячейка'} свободна`,blocked=!isReceiving&&metrics.otherOwners.length?`<div class="error-box">Адрес занят другим клиентом. Переключите клиента сверху, чтобы посмотреть его короба и товар.</div>`:'';
  const genericPlace=meta.isPalletPlace?'палеточном месте':meta.isPallet?'палете':'ячейке',genericEmpty=meta.isPalletPlace?'Палеточное место пустое':meta.isPallet?'Палета пустая':'Ячейка пустая';const genericContent=metrics.otherOwners.length&&!isReceiving?blocked:locationBrowser.contentsLoading?'<div class="empty-state">Проверяем фактический остаток и карточки товаров…</div>':`${direct.length?`<section class="storage-box-card direct-stock"><div class="storage-box-head"><div><span class="kicker">БЕЗ КОРОБА</span><h3>Товар размещён прямо в ${genericPlace}</h3></div><span class="tag">${fmtQty(direct.reduce((sum,x)=>sum+Number(x.quantity||0),0))} шт.</span></div>${locationItemsTable(direct,'Товара без короба нет')}</section>`:''}${boxes||(!direct.length?`<div class="empty-state">${genericEmpty} — коробов и товара нет.</div>`:'')}`;
  const content=isReceiving?(locationBrowser.contentsLoading?'<div class="empty-state">Загружаем ПРИЕМКА…</div>':receivingQueueTable(locationBrowser.contentsData?.receiving_queue)):genericContent;
  const actions=isReceiving?`<button type="button" class="secondary" data-location-history="${esc(id)}">История изменений</button>`:`<button type="button" class="secondary" data-location-history="${esc(id)}">История изменений</button><button type="button" class="primary" data-location-print="${esc(id)}">Печать QR-этикетки</button><button type="button" class="danger" data-location-delete="${esc(id)}" data-location-code="${esc(location.code)}">Удалить пустой адрес</button>`;
  const kicker=isReceiving?'ВИРТУАЛЬНАЯ ПРИЕМКА':meta.isPalletPlace?'ПАЛЕТОЧНОЕ МЕСТО':meta.isPallet?'ПАЛЕТНОЕ ХРАНЕНИЕ':'ЯЧЕЕЧНОЕ ХРАНЕНИЕ';
  return `<div class="storage-detail ${isReceiving?'receiving-detail':''}"><div class="storage-detail-head"><div><span class="kicker">${kicker}</span><h2 class="fc27-public-code">${esc(meta.publicCode)}</h2><p>${isReceiving?'Отменённые после сборки/упаковки товары ждут повторного физического размещения':`${esc(location.name||'')} · QR: ${esc(location.barcode||location.code)}`}</p><b class="location-owner">${esc(ownerText)}</b></div><div class="button-row">${actions}</div></div><div class="location-content-summary"><b>${isReceiving?fmtQty(locationBrowser.contentsData?.receiving_queue?.items?.length||0):metrics.skus} SKU</b><b>${isReceiving?'виртуальная зона':`${metrics.boxes} короб.`}</b><b>${fmtQty(isReceiving?(locationBrowser.contentsData?.receiving_queue?.quantity||0):metrics.qty)} шт.</b></div><div class="storage-box-list">${content}</div></div>`;
}
function historyPlace(row,prefix){const location=row[`${prefix}_location`]||locationLabel(row[`${prefix}_location_id`]),container=row[`${prefix}_container`]||containerLabel(row[`${prefix}_container_id`]);return `${location||'—'}${row[`${prefix}_container_id`]?` · ${container||'короб'}`:''}`;}
function renderLocationHistory(location){
  ensureInventoryPopupStyle();
  if(locationBrowser.historyLoading)return '<div class="empty-state">Загружаем историю ячейки…</div>';
  const data=locationBrowser.historyData||{},rows=data.rows||[],id=String(location.id),running=new Map();for(const balance of state.inventory.filter(x=>String(x.location_id)===id&&Number(x.quantity)>0))running.set(String(balance.sku_id),Number(running.get(String(balance.sku_id))||0)+Number(balance.quantity||0));
  const html=rows.map(row=>{
    const key=String(row.sku_id),qty=Math.abs(Number(row.quantity||0)),cancelled=String(row.status||'').toUpperCase()==='CANCELLED';
    const inbound=String(row.destination_location_id||'')===id?qty:0,outbound=String(row.source_location_id||'')===id?qty:0,delta=cancelled&&!row.was_reversed?0:inbound-outbound;
    const after=Number(running.get(key)||0),before=after-delta;running.set(key,before);
    const sku=row.sku||mapBy(state.skus,row.sku_id)||{},skuText=[sku.article||sku.sku_code,sku.size,sku.name].filter(Boolean).join(' · ')||'Неизвестный товар';
    const deltaText=cancelled&&!row.was_reversed?'Отменена · не влияет на остаток':delta>0?`+${fmtQty(delta)}`:delta<0?`−${fmtQty(Math.abs(delta))}`:'Без изменения количества';
    const placeEvents=(data.place_history?[]:(row.place_events||[])).map(ev=>`<small class="subline ${ev.type==='PLACE_EMPTY'?'bad':'good'}"><b>${esc(ev.label||opName(ev.type))}</b>${ev.container?` · ${esc(ev.container)}`:''} · ${fmtQty(ev.before_qty||0)} → ${fmtQty(ev.after_qty||0)} шт.</small>`).join('');
    return `<tr class="${cancelled?'operation-cancelled':''}"><td><b>${row.created_at?new Date(row.created_at).toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'}):'—'}</b><small class="subline">${row.created_at?new Date(row.created_at).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'}):''}</small></td><td><span class="operation-badge operation-${esc(String(row.operation_type||'').toLowerCase())}">${esc(opName(row.operation_type))}</span>${cancelled?`<small class="subline bad">${row.was_reversed?'Позднее отменена отдельной корректировкой':'Отменена'}</small>`:''}</td><td>${esc(skuText)}</td><td class="qty"><b class="${cancelled?'bad':delta>0?'good':delta<0?'bad':''}">${esc(deltaText)}</b>${delta?`<small class="subline balance-change">${fmtQty(before)} → ${fmtQty(after)} шт.</small>`:''}${placeEvents}</td><td>${esc(historyPlace(row,'source'))}</td><td>${esc(historyPlace(row,'destination'))}</td><td>${esc(row.performed_by||'—')}</td><td>${esc(row.reference||row.comment||row.reason||'—')}</td></tr>`;
  }).join('');
  return `<div class="storage-section-head"><div><h3>История изменений ${esc(location.code)}</h3><p>Когда ячейка или короб опустели и были заполнены снова — в отдельных событиях ниже.</p></div><button type="button" class="secondary" data-location-nav="DETAIL">Вернуться к содержимому</button></div><details class="inventory-popup-section" open><summary>История заполнения ячейки и коробов</summary>${inventoryLifecycleHtml(data.place_history||[],{cell:true})}</details><h3>Движения товара</h3>${data.has_more?`<div class="warn-box">Показаны последние ${Number(data.limit||2000)} движений этой ячейки. События заполнения выше рассчитаны по всей доступной истории.</div>`:''}<div class="table-wrap"><table class="storage-history-table"><thead><tr><th>Дата</th><th>Операция</th><th>Товар</th><th>Изменение</th><th>Откуда</th><th>Куда</th><th>Сотрудник</th><th>Основание</th></tr></thead><tbody>${html||'<tr><td colspan="8" class="empty-state">История этой ячейки пока пустая.</td></tr>'}</tbody></table></div>`;
}
function resetLocationBrowser(render=true){Object.assign(locationBrowser,{level:'ROOT',storage:null,rackKey:'',floor:'',locationId:'',historyData:null,historyLoading:false,historyRequest:'',contentsData:null,contentsLoading:false,contentsRequest:''});if(render)renderLocations();}
function navigateLocationBrowser(level){
  if(level==='ROOT')return resetLocationBrowser();
  if(level==='RACKS')Object.assign(locationBrowser,{level,storage:'CELL',rackKey:'',floor:'',locationId:'',historyData:null,contentsData:null,contentsLoading:false});
  if(level==='FLOORS')Object.assign(locationBrowser,{level,storage:'CELL',floor:'',locationId:'',historyData:null,contentsData:null,contentsLoading:false});
  if(level==='CELLS')Object.assign(locationBrowser,{level,storage:'CELL',locationId:'',historyData:null,contentsData:null,contentsLoading:false});
  if(level==='PALLETS')Object.assign(locationBrowser,{level,storage:'PALLET',rackKey:'',floor:'',locationId:'',historyData:null,contentsData:null,contentsLoading:false});
  if(level==='DETAIL')Object.assign(locationBrowser,{level,historyData:null,historyLoading:false});
  renderLocations();
}
async function openLocationContents(id){
  const location=mapBy(state.locations,id);if(!location)return;const meta=locationStorageMeta(location),isReceiving=String(location.code||location.barcode||'').trim().toUpperCase()==='ПРИЕМКА',request=`${state.clientId}:${id}:${Date.now()}`;if($('locationSearch'))$('locationSearch').value='';Object.assign(locationBrowser,{level:'DETAIL',storage:meta.isPallet?'PALLET':'CELL',rackKey:meta.rackKey,floor:meta.floor,locationId:String(id),historyData:null,historyLoading:false,contentsData:null,contentsLoading:true,contentsRequest:request});renderLocations();
  try{
    if(isReceiving){
      // r143: do not load all boxed stock from /layout and do not run the old WB
      // receiving-queue repair while merely opening the page.  One indexed,
      // read-only query now returns the exact same loose stock as the root card.
      const queue=await api(`/warehouse-ui/virtual-receiving-current?client_id=${encodeURIComponent(state.clientId)}&warehouse_id=${encodeURIComponent(state.warehouseId)}`,{timeoutMs:10000});
      if(locationBrowser.contentsRequest!==request)return;
      locationBrowser.contentsData={location:{id:String(id),code:location.code},items:[],receiving_queue:queue};
    }else{
      const path=`/layout/locations/${encodeURIComponent(id)}/contents?client_id=${encodeURIComponent(state.clientId)}`;
      const fetchContents=async()=>{try{return await api(path,{timeoutMs:60000});}catch(error){const transient=error?.name==='TimeoutError'||error?.name==='AbortError'||[502,503,504].includes(Number(error?.status||0));if(!transient)throw error;await new Promise(r=>setTimeout(r,500));return api(path,{timeoutMs:120000});}};
      const data=await fetchContents();if(locationBrowser.contentsRequest!==request)return;locationBrowser.contentsData=data;
    }
  }
  catch(error){if(locationBrowser.contentsRequest===request){locationBrowser.contentsData={location:{id:String(id)},items:[],receiving_queue:{quantity:0,sku_count:0,items:[]},load_error:error.message||'Ошибка загрузки'};toast(isReceiving?'Не удалось получить текущий остаток виртуальной ПРИЕМКА. Остатки не изменены.':'Ячейка отвечает медленно. Данные не подменены нулевыми резервами; повторите открытие или обновите страницу.',true);}}
  finally{if(locationBrowser.contentsRequest===request){locationBrowser.contentsLoading=false;renderLocations();}}
}
async function openLocationHistory(id){
  const request=`${state.clientId}:${id}:${Date.now()}`;Object.assign(locationBrowser,{level:'HISTORY',locationId:String(id),historyData:null,historyLoading:true,historyRequest:request});renderLocations();
  try{const data=await api(`/layout/locations/${encodeURIComponent(id)}/history?client_id=${encodeURIComponent(state.clientId)}&limit=2000`);if(locationBrowser.historyRequest!==request)return;locationBrowser.historyData=data;}
  catch(error){if(locationBrowser.historyRequest===request){locationBrowser.historyData={rows:[]};toast(error.message,true);}}
  finally{if(locationBrowser.historyRequest===request){locationBrowser.historyLoading=false;renderLocations();}}
}
async function placeReceivingStock(button){
  await ensureContext();
  const sourceId=button.dataset.sourceLocationId||receivingLocation()?.id,skuId=button.dataset.skuId,available=Number(button.dataset.quantity||0);
  if(!sourceId||!skuId)throw new Error('Не удалось определить товар в ПРИЕМКА');
  if(!(available>0))throw new Error('В ПРИЕМКА уже нет доступного количества этого товара. Обновите страницу.');

  // 0.7.9.20: quantity is selected before the destination. This makes partial
  // placement explicit: the remainder stays in virtual ПРИЕМКА and can be
  // placed into another cell with the next scan.
  const qtyRaw=prompt(`Сколько разместить?\n\nДоступно в ПРИЕМКА: ${fmtQty(available)} шт.\nМожно указать часть количества. Остаток останется в ПРИЕМКА.`,String(available));
  if(qtyRaw===null)return;
  const qty=Number(String(qtyRaw).trim().replace(',','.'));
  if(!Number.isFinite(qty)||!(qty>0)||qty>available)throw new Error(`Количество должно быть больше 0 и не больше ${fmtQty(available)} шт.`);

  const raw=prompt(`Куда разместить ${fmtQty(qty)} шт.?\n\nВведите код или отсканируйте QR ячейки / палеты / палеточного места:`,'');
  if(raw===null)return;
  const destination=await resolveFreshLocation(raw);if(!destination)throw new Error(`Адрес назначения «${String(raw||'').trim()}» не найден. Справочник адресов обновлён — проверьте код/QR.`);assertLocationAvailableForClient(destination);

  await api('/operations/move',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,idempotency_key:uid(),performed_by:$('currentUser')?.textContent||null,reference:null,comment:'Частичное размещение из виртуальной ПРИЕМКА',sku_id:skuId,quantity:qty,source_location_id:sourceId,source_container_id:null,destination_location_id:destination.id,destination_container_id:null})});
  await loadContextData();await openLocationContents(sourceId);
  const remainder=Math.max(0,available-qty);
  toast(remainder>0?`Размещено ${fmtQty(qty)} шт. → ${destination.code}. В ПРИЕМКА осталось ${fmtQty(remainder)} шт.`:`Размещено всё количество: ${fmtQty(qty)} шт. → ${destination.code}`);
}
async function removeReceivingStock(button){
  await ensureContext();
  const locationId=button.dataset.sourceLocationId||receivingLocation()?.id,skuId=button.dataset.skuId,available=Number(button.dataset.quantity||0),containerId=button.dataset.containerId||null,name=button.dataset.itemName||button.dataset.barcode||'товар';
  if(!locationId||!skuId||!(available>0))throw new Error('Строка ПРИЕМКА уже изменилась. Обновите страницу.');
  const qtyRaw=prompt(`Сколько удалить из виртуальной ПРИЕМКА?\n\nТовар: ${name}\nДоступно: ${fmtQty(available)} шт.\nМожно удалить часть или всё количество.`,String(available));
  if(qtyRaw===null)return;
  const quantity=Number(String(qtyRaw).trim().replace(',','.'));
  if(!Number.isFinite(quantity)||!(quantity>0)||quantity>available)throw new Error(`Количество должно быть больше 0 и не больше ${fmtQty(available)} шт.`);
  if(!confirm(`Удалить из виртуальной ПРИЕМКА «${name}»: ${fmtQty(quantity)} шт.?\n\nТовар будет списан только из FulfillCore и не будет размещён в Основной/FBS. Операция останется в истории.\n\nВы уверены?`))return;
  await api(`/layout/locations/${encodeURIComponent(locationId)}/stock/remove`,{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,sku_id:skuId,container_id:containerId,quantity,performed_by:$('currentUser')?.textContent||null})});
  await loadContextData();state.inventoryCatalogLoaded=false;await openLocationContents(locationId);
  const remainder=Math.max(0,available-quantity);toast(remainder>0?`Удалено из ПРИЕМКА: ${fmtQty(quantity)} шт. Осталось ${fmtQty(remainder)} шт.`:`Товар удалён из ПРИЕМКА: ${fmtQty(quantity)} шт. История сохранена.`);
}
async function removeErroneousLocationStock(button){
  const locationId=button.dataset.locationId,quantity=Number(button.dataset.quantity||0),name=button.dataset.itemName||button.dataset.itemBarcode||'товар';if(!locationId||!button.dataset.skuId||!(quantity>0))throw new Error('Строка остатка уже изменилась. Обновите ячейку.');
  if(!confirm(`Удалить ошибочно принятый товар «${name}» из этой ячейки полностью: ${fmtQty(quantity)} шт.?\n\nОстаток уменьшится только в FulfillCore. Операция останется в истории.\n\nВы уверены?`))return;
  await api(`/layout/locations/${encodeURIComponent(locationId)}/stock/remove`,{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,sku_id:button.dataset.skuId,container_id:button.dataset.containerId||null,quantity,performed_by:$('currentUser')?.textContent||null})});
  await loadContextData();state.inventoryCatalogLoaded=false;await openLocationContents(locationId);toast(`Ошибочная строка удалена: ${fmtQty(quantity)} шт. История сохранена.`);
}
function installLocationBrowser(){
  const grid=$('locationsGrid'),search=$('locationSearch');if(!grid||grid.dataset.storageBrowserInstalled)return;grid.dataset.storageBrowserInstalled='1';grid.classList.add('storage-browser-grid');if(search)search.placeholder='Найти секцию, ячейку, палету, палетоместо, короб или QR';const heading=grid.closest('.panel')?.querySelector('.panel-head h2');if(heading){heading.id='locationBrowserTitle';heading.textContent='Адресное хранение';}
  if(!document.getElementById('locationBrowserStyles')){const style=document.createElement('style');style.id='locationBrowserStyles';style.textContent=`#locationsGrid.storage-browser-grid{display:block}.two-col.storage-browser-drilldown{grid-template-columns:minmax(0,1fr)!important}.two-col.storage-browser-drilldown>:not(:first-child){display:none!important}.storage-breadcrumbs{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-bottom:16px;padding:10px 12px;border-radius:12px;background:#f5f7fa}.storage-breadcrumbs button{padding:0;border:0;background:transparent;color:#5b4ad1;font-weight:750;cursor:pointer}.storage-breadcrumbs span{color:#98a2b3}.storage-receiving-queue{display:flex;justify-content:space-between;align-items:center;gap:18px;margin:0 0 18px;padding:17px 18px;border:1px solid #f7b955;border-radius:16px;background:#fff8e8}.storage-receiving-queue h3{margin:4px 0 5px;font-size:22px}.storage-receiving-queue p{margin:0;max-width:860px;color:#6b5a2b}.storage-receiving-stats{display:grid;gap:7px;min-width:190px;text-align:right}.storage-receiving-stats b{font-size:24px}.storage-receiving-details{display:grid;gap:10px}.receiving-detail .location-content-summary{background:#fff8e8}.storage-intro{margin-bottom:14px}.storage-intro h3,.storage-section-head h3{margin:0 0 4px}.storage-intro p,.storage-section-head p{margin:0;color:#667085}.storage-choice-grid,.storage-card-grid,.storage-address-grid{display:grid;grid-template-columns:repeat(2,minmax(240px,1fr));gap:14px}.storage-address-grid{grid-template-columns:repeat(3,minmax(220px,1fr))}.storage-choice,.storage-group-card{display:grid;text-align:left;gap:11px;padding:18px;border:1px solid #d7deea;border-radius:16px;background:#fff;color:#101828;cursor:pointer;box-shadow:0 3px 10px rgba(16,24,40,.04)}.storage-choice:hover,.storage-group-card:hover{border-color:#6958dc;box-shadow:0 8px 22px rgba(91,74,209,.12);transform:translateY(-1px)}.storage-choice-icon{display:grid;place-items:center;width:45px;height:45px;border-radius:12px;background:#eeeafd;color:#5b4ad1;font-size:25px}.storage-choice>span:nth-child(2){display:grid;gap:3px}.storage-choice>span b{font-size:20px}.storage-choice small,.storage-group-card p{color:#667085}.storage-choice-stats,.storage-group-stats,.storage-address-stats{display:flex;gap:9px;flex-wrap:wrap}.storage-choice-stats{display:grid;grid-template-columns:auto 1fr auto 1fr auto 1fr;align-items:baseline;padding:10px;border-radius:10px;background:#f8fafc}.storage-choice-stats strong{font-size:18px}.storage-choice em,.storage-group-card em{color:#5b4ad1;font-style:normal;font-weight:750}.storage-section-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:4px 0 14px}.storage-section-head>span{padding:7px 10px;border-radius:999px;background:#f2f4f7;font-weight:750}.storage-group-card h3{margin:0;font-size:27px}.storage-group-card p{margin:0}.storage-group-stats span,.storage-address-stats span{padding:6px 8px;border-radius:8px;background:#f2f4f7;font-size:12px}.storage-selection-toolbar{display:flex;justify-content:space-between;align-items:center;gap:12px;margin:0 0 14px;padding:11px 13px;border:1px solid #dfe4ec;border-radius:12px;background:#f8fafc}.storage-range-toolbar{display:grid;gap:10px;margin:0 0 14px;padding:13px;border:1px solid #c7d7fe;border-radius:12px;background:#eef4ff}.storage-range-toolbar>div:first-child{display:grid;gap:3px}.storage-range-toolbar small{color:#475467}.storage-range-controls{display:grid;grid-template-columns:minmax(150px,1fr) minmax(150px,1fr) auto;gap:10px;align-items:end}.storage-range-controls label{display:grid;gap:5px}.storage-address-card{padding:14px;border:1px solid #d8dee8;border-radius:14px;background:#fff}.storage-address-card.selected{outline:2px solid #6958dc;background:#f7f5ff}.storage-address-top,.storage-detail-head,.storage-box-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.storage-address-top label{display:flex;align-items:center;gap:5px;font-size:12px}.storage-address-card h3{margin:8px 0 4px;font-size:20px}.storage-address-card p{min-height:34px;margin:0 0 7px;color:#667085}.storage-address-stats{margin-top:10px}.storage-fill{display:grid;gap:5px;margin-top:10px;padding:9px 10px;border-radius:10px;background:#f8fafc}.storage-fill b{font-size:13px}.storage-fill small{color:#667085}.storage-fill progress{width:100%;height:12px}.storage-fill-high{background:#fff8e8}.storage-fill-full{background:#fff1f0}.storage-fill-unknown{background:#f2f4f7}.storage-detail-head{padding-bottom:14px;border-bottom:1px solid #eaecf0}.storage-detail-head h2{margin:3px 0;font-size:30px}.storage-detail-head p{margin:0 0 6px;color:#667085}.storage-box-list{display:grid;gap:14px}.storage-box-card{padding:15px;border:1px solid #d8dee8;border-radius:14px;background:#fff}.storage-box-card.empty-box{border-style:dashed;background:#fafafa}.storage-box-head{margin-bottom:12px}.storage-box-head h3{margin:2px 0 0}.storage-empty-tag{padding:6px 9px;border-radius:999px;background:#f2f4f7;color:#475467;font-weight:750}.storage-items-table td,.storage-items-table th,.storage-history-table td,.storage-history-table th{white-space:nowrap}.storage-history-table .balance-change{min-width:0}.storage-detail .location-content-summary{margin:14px 0}.storage-browser-grid .empty-state{padding:30px}.stock-barcode-error td{background:#fff6f5!important}.warn-box{margin-bottom:12px;padding:11px 13px;border-radius:10px;background:#fff4e5;color:#8a4b00}@media(max-width:1100px){.storage-address-grid{grid-template-columns:repeat(2,minmax(220px,1fr))}}@media(max-width:760px){.storage-receiving-queue{align-items:flex-start;flex-direction:column}.storage-receiving-stats{text-align:left;min-width:0}.storage-choice-grid,.storage-card-grid,.storage-address-grid{grid-template-columns:1fr}.storage-detail-head,.storage-box-head,.storage-section-head,.storage-selection-toolbar{align-items:flex-start;flex-direction:column}.storage-range-controls{grid-template-columns:1fr}.storage-choice-stats{grid-template-columns:auto 1fr auto 1fr}.storage-address-card p{min-height:0}}`;document.head.appendChild(style);}
}
installLocationBrowser();
function renderLocations(){
  const query=($('locationSearch')?.value||'').trim(),locations=physicalLocations(),cells=locations.filter(x=>!locationStorageMeta(x).isPallet),pallets=locations.filter(x=>locationStorageMeta(x).isPallet),validIds=new Set(locations.map(x=>String(x.id)));for(const id of [...selectedLocationIds])if(!validIds.has(id))selectedLocationIds.delete(id);
  if($('locationCodesList'))$('locationCodesList').innerHTML=locations.sort((a,b)=>naturalLocationCompare(a.code,b.code)).map(x=>`<option value="${esc(x.code)}"></option>`).join('');
  const heading=$('locationBrowserTitle');let body='',title='Адресное хранение';
  if(query){title='Поиск адреса';body=renderLocationSearch(query,locations);}
  else if(locationBrowser.level==='ROOT')body=renderStorageRoot(cells,pallets);
  else if(locationBrowser.level==='RACKS')body=renderRackGroups(cells);
  else if(locationBrowser.level==='FLOORS')body=renderFloorGroups(cells);
  else if(locationBrowser.level==='CELLS')body=renderCellAddresses(cells);
  else if(locationBrowser.level==='PALLETS')body=renderPalletAddresses(pallets);
  else{const location=mapBy(state.locations,locationBrowser.locationId);if(!location){resetLocationBrowser(false);body=renderStorageRoot(cells,pallets);}else{const receiving=String(location.code||location.barcode||'').trim().toUpperCase()==='ПРИЕМКА';const meta=locationStorageMeta(location);title=receiving?'ПРИЕМКА · требует размещения':`${meta.isPalletPlace?'Палеточное место':meta.isPallet?'Палета':'Ячейка'} ${location.code}`;body=locationBrowser.level==='HISTORY'?renderLocationHistory(location):renderLocationDetail(location);}}
  if(heading)heading.textContent=title;const pageLayout=$('locationsGrid')?.closest('.two-col');pageLayout?.classList.toggle('storage-browser-drilldown',Boolean(query)||locationBrowser.level!=='ROOT');$('locationsGrid').innerHTML=`${locationBreadcrumbs(Boolean(query))}${body}`;queueTopTableScrolls();
}
function removeLocationsLocally(ids){const remove=new Set((ids||[]).map(String));state.locations=structuredArray(state.locations).filter(x=>!remove.has(String(x.id)));if(state.locationOccupancy&&typeof state.locationOccupancy==='object')for(const id of remove)delete state.locationOccupancy[id];for(const id of remove)selectedLocationIds.delete(id);if(remove.has(String(locationBrowser.locationId||'')))resetLocationBrowser(false);else renderLocations();}
function quietRefreshAfterLocationMutation(){setTimeout(()=>loadContextData({quiet:true,force:true}).catch(error=>console.warn('FC_R129_LOCATION_REFRESH',error)),700);}
$('locationsGrid')?.addEventListener('click',async e=>{
  const productCard=e.target.closest('[data-location-product-card]'),receivingPlace=e.target.closest('[data-receiving-place]'),receivingRemove=e.target.closest('[data-receiving-remove]'),qtyAdd=e.target.closest('[data-location-qty-add]'),qtyRemove=e.target.closest('[data-location-qty-remove]'),stockRemove=e.target.closest('[data-location-stock-remove]'),nav=e.target.closest('[data-location-nav]'),storage=e.target.closest('[data-location-storage]'),rack=e.target.closest('[data-location-rack]'),floor=e.target.closest('[data-location-floor]'),check=e.target.closest('[data-location-select]'),open=e.target.closest('[data-location-open]'),history=e.target.closest('[data-location-history]'),printRange=e.target.closest('[data-location-print-range]'),printSelected=e.target.closest('[data-location-print-selected]'),deleteSelected=e.target.closest('[data-location-delete-selected]'),print=e.target.closest('[data-location-print]'),remove=e.target.closest('[data-location-delete]');
  if(productCard){openInventoryProduct(productCard.dataset.productId,productCard.dataset.productBarcode||'');return;}
  if(qtyAdd){adjustStockInOpenedCell(qtyAdd,1).catch(error=>toast(error.message,true));return;}
  if(qtyRemove){adjustStockInOpenedCell(qtyRemove,-1).catch(error=>toast(error.message,true));return;}
  if(receivingPlace){placeReceivingStock(receivingPlace).catch(error=>toast(error.message,true));return;}
  if(receivingRemove){removeReceivingStock(receivingRemove).catch(error=>toast(error.message,true));return;}
  if(stockRemove){removeErroneousLocationStock(stockRemove).catch(error=>toast(error.message,true));return;}
  if(nav){navigateLocationBrowser(nav.dataset.locationNav);return;}
  if(storage){if($('locationSearch'))$('locationSearch').value='';navigateLocationBrowser(storage.dataset.locationStorage==='CELL'?'RACKS':'PALLETS');return;}
  if(rack){locationBrowser.rackKey=decodeURIComponent(rack.dataset.locationRack);locationBrowser.level='FLOORS';renderLocations();return;}
  if(floor){locationBrowser.floor=decodeURIComponent(floor.dataset.locationFloor);locationBrowser.level='CELLS';renderLocations();return;}
  if(check){const id=String(check.dataset.locationSelect);check.checked?selectedLocationIds.add(id):selectedLocationIds.delete(id);renderLocations();return;}
  if(open){openLocationContents(open.dataset.locationOpen);return;}
  if(history){openLocationHistory(history.dataset.locationHistory);return;}
  if(printRange){const toolbar=printRange.closest('[data-location-range-toolbar]'),from=toolbar?.querySelector('[data-location-range-from]'),to=toolbar?.querySelector('[data-location-range-to]'),options=[...(from?.options||[])];let start=options.findIndex(x=>x.value===from?.value),end=options.findIndex(x=>x.value===to?.value);if(start<0||end<0){toast('Выберите начало и конец диапазона',true);return;}if(end<start)[start,end]=[end,start];printLocationLabels({ids:options.slice(start,end+1).map(x=>x.value)});return;}
  if(printSelected){printLocationLabels({ids:[...selectedLocationIds]});return;}
  if(deleteSelected){deleteSelectedEmptyLocations().catch(error=>toast(error.message,true));return;}
  if(print){printLocationLabels({ids:[print.dataset.locationPrint]});return;}
  if(!remove)return;const code=remove.dataset.locationCode||'';if(!confirm(`Удалить пустой адрес ${code}?\n\nАдрес с товаром или активным коробом не удалится. История сохранится.\n\nВы уверены?`))return;
  try{const id=String(remove.dataset.locationDelete);await api(`/layout/locations/${encodeURIComponent(id)}`,{method:'DELETE'});removeLocationsLocally([id]);toast(`Адрес ${code} удалён, история сохранена`);quietRefreshAfterLocationMutation();}catch(err){toast(err.message,true)}
});
async function deleteSelectedEmptyLocations(){const ids=[...selectedLocationIds];if(!ids.length)throw new Error('Сначала выберите ячейки, палеты или палеточные места галочками');if(!confirm(`Удалить выбранные пустые адреса: ${ids.length} шт.?\n\nЗанятые адреса будут пропущены, история сохранится.\n\nВы уверены?`))return;const r=await api('/layout/bulk-delete/locations',{method:'POST',body:JSON.stringify({ids})}),deleted=Array.isArray(r?.deleted)?r.deleted:[],skipped=Array.isArray(r?.skipped)?r.skipped:[],skipIds=new Set(skipped.map(x=>String(x?.id||x?.location_id||'')).filter(Boolean)),deletedIds=deleted.length?deleted.map(x=>String(x?.id||x?.location_id||x)).filter(Boolean):ids.filter(id=>!skipIds.has(String(id)));removeLocationsLocally(deletedIds);toast(`Удалено адресов: ${deletedIds.length}${skipped.length?` · пропущено: ${skipped.length}`:''}`,skipped.length>0);quietRefreshAfterLocationMutation();}
const selectedContainerIds=new Set();
function syncContainerBulkToolbar(){
  const count=selectedContainerIds.size,summary=$('selectedContainerCount'),print=$('printSelectedContainers'),pdf=$('printSelectedContainersPdf'),move=$('moveSelectedContainers');
  if(summary)summary.textContent=`Выбрано: ${count}`;
  if(print){print.disabled=count===0;print.textContent=count?`Распечатать ШК выбранных (${count})`:'Распечатать ШК выбранных';}
  if(pdf)pdf.disabled=count===0;
  if(move){move.disabled=count===0;move.textContent=count?`Переместить выбранные (${count})`:'Переместить выбранные';}
}
function renderContainers(){
  const q=$('containerSearch').value.trim().toLowerCase();
  const validIds=new Set(activeContainers().map(x=>String(x.id)));for(const id of [...selectedContainerIds])if(!validIds.has(id))selectedContainerIds.delete(id);const __detail=$('containerDetailPanel'),__detailId=String(__detail?.dataset.containerId||'');if(__detailId&&!validIds.has(__detailId)){__detail.classList.add('hidden');__detail.innerHTML='';delete __detail.dataset.containerId;}
  const list=activeContainers().filter(c=>[c.name,c.barcode,locationLabel(c.location_id)].join(' ').toLowerCase().includes(q));
  $('containersBody').innerHTML=list.length?list.map(c=>{
    const bal=state.inventory.filter(b=>b.container_id===c.id&&Number(b.quantity)>0),qty=bal.reduce((s,b)=>s+Number(b.quantity),0),skus=new Set(bal.map(b=>b.sku_id)).size;
    const address=c.location_id?(String(locationLabel(c.location_id)||'').trim().toUpperCase()==='ПРИЕМКА'?'<span class="warn">Не размещён</span>':esc(locationLabel(c.location_id))):'<span class="warn">Без адреса</span>',moveState=c._bulkMovePending?'<small class="subline good">Перемещаем…</small>':c._bulkMoveError?`<small class="subline bad">${esc(c._bulkMoveError)}</small>`:'';
    return `<tr class="${selectedContainerIds.has(String(c.id))?'selected-row':''}"><td><label class="container-select"><input type="checkbox" data-container-select="${esc(c.id)}" ${selectedContainerIds.has(String(c.id))?'checked':''}/> <b>${esc(containerPublicCode(c))}</b></label></td><td>${esc(containerPublicCode(c))}</td><td>${address}${moveState}</td><td>${skus}</td><td class="qty">${fmtQty(qty)}</td><td><div class="row-actions"><button class="mini secondary" data-container-view="${c.id}">Состав</button><button class="mini secondary" data-container-print="${c.id}">Печать ШК</button><button class="mini primary" data-container-move="${c.id}">Переместить</button><button class="mini secondary" data-container-rename="${c.id}">Переименовать</button><button class="mini danger" data-container-delete="${c.id}" ${qty>0?'disabled title="Сначала переместите или спишите товар"':''}>Удалить</button></div></td></tr>`;
  }).join(''):'<tr><td colspan="6" class="empty-state">Коробов не найдено</td></tr>';
  syncContainerBulkToolbar();
}
function installContainerMovementHelp(){const search=$('containerSearch');if(!search||$('containerMovementHelp'))return;const note=document.createElement('div');note.id='containerMovementHelp';note.className='success-box';note.innerHTML='<b>Короба: выбор, перемещение, повторная печать и удаление.</b><span>Выберите один или несколько коробов. Их можно одним действием переместить на палету/палетоместо/ячейку или заново распечатать ШК. Массовое перемещение сразу показывает новый адрес, а проверка резерва выполняется на сервере.</span>';search.insertAdjacentElement('afterend',note);const toolbar=document.createElement('div');toolbar.id='containerBulkToolbar';toolbar.className='button-row container-bulk-toolbar';toolbar.innerHTML='<span id="selectedContainerCount" class="tag">Выбрано: 0</span><button id="selectAllContainers" type="button" class="secondary">☑ Выбрать все короба</button><button id="clearSelectedContainers" type="button" class="secondary">☐ Снять выбор</button><button id="moveSelectedContainers" type="button" class="primary" disabled>Переместить выбранные</button><button id="printSelectedContainers" type="button" class="primary" disabled>Распечатать ШК выбранных</button><button id="printSelectedContainersPdf" type="button" class="secondary" disabled>PDF выбранных</button><button id="deleteSelectedContainers" type="button" class="danger">Удалить выбранные вместе с остатками</button><button id="deleteAllContainers" type="button" class="danger">Удалить ВСЕ короба вместе с остатками</button>';note.insertAdjacentElement('afterend',toolbar);$('selectAllContainers').addEventListener('click',()=>{for(const c of activeContainers())selectedContainerIds.add(String(c.id));renderContainers();toast(`Выбрано коробов: ${selectedContainerIds.size}`);});$('clearSelectedContainers').addEventListener('click',()=>{selectedContainerIds.clear();renderContainers();});$('moveSelectedContainers').addEventListener('click',()=>openBulkContainerMove());$('printSelectedContainers').addEventListener('click',()=>printExistingContainerLabels([...selectedContainerIds],false).catch(e=>toast(e.message,true)));$('printSelectedContainersPdf').addEventListener('click',()=>printExistingContainerLabels([...selectedContainerIds],true).catch(e=>toast(e.message,true)));$('deleteSelectedContainers').addEventListener('click',()=>deleteContainersWithStock(false).catch(e=>toast(e.message,true)));$('deleteAllContainers').addEventListener('click',()=>deleteContainersWithStock(true).catch(e=>toast(e.message,true)));syncContainerBulkToolbar();}
function ensureBulkContainerMoveStyles(){if(document.getElementById('bulkContainerMoveStyles'))return;const style=document.createElement('style');style.id='bulkContainerMoveStyles';style.textContent='.fc-bulk-box-move{position:fixed;inset:0;z-index:10050;display:grid;place-items:center;padding:20px;background:rgba(15,23,42,.55)}.fc-bulk-box-move.hidden{display:none}.fc-bulk-box-move-card{width:min(650px,96vw);padding:22px;border-radius:18px;background:#fff;box-shadow:0 24px 70px rgba(0,0,0,.28)}.fc-bulk-box-move-card h2{margin:0 0 6px}.fc-bulk-box-move-card p{margin:0 0 16px;color:#667085}.fc-bulk-box-move-card label{display:grid;gap:7px}.fc-bulk-box-move-card input{font-size:18px}.fc-bulk-box-move-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:14px}.fc-bulk-box-move-hint{min-height:24px;margin-top:7px}.container-bulk-toolbar{align-items:center;flex-wrap:wrap}';document.head.appendChild(style);}
function ensureBulkContainerMoveModal(){ensureBulkContainerMoveStyles();let modal=$('bulkContainerMoveModal');if(modal)return modal;modal=document.createElement('div');modal.id='bulkContainerMoveModal';modal.className='fc-bulk-box-move hidden';modal.innerHTML='<div class="fc-bulk-box-move-card"><h2>Переместить выбранные короба</h2><p id="bulkContainerMoveSummary"></p><form id="bulkContainerMoveForm"><label>Палета / палетоместо / ячейка<input id="bulkContainerMoveDestination" autocomplete="off" placeholder="Отсканируйте QR или введите PAL-003" required></label><div id="bulkContainerMoveHint" class="fc-bulk-box-move-hint muted">Можно сканировать QR адреса.</div><div class="fc-bulk-box-move-actions"><button type="button" class="secondary" data-bulk-box-move-close>Отмена</button><button id="bulkContainerMoveSubmit" type="submit" class="primary">Переместить</button></div></form></div>';document.body.appendChild(modal);modal.querySelectorAll('[data-bulk-box-move-close]').forEach(b=>b.addEventListener('click',()=>modal.classList.add('hidden')));modal.addEventListener('click',e=>{if(e.target===modal)modal.classList.add('hidden');});const input=$('bulkContainerMoveDestination'),hint=$('bulkContainerMoveHint');input.addEventListener('input',()=>{const loc=findLocation(input.value);if(!input.value.trim()){hint.textContent='Можно сканировать QR адреса.';hint.className='fc-bulk-box-move-hint muted';return;}if(!loc){hint.textContent='Адрес пока не найден';hint.className='fc-bulk-box-move-hint warn';return;}const others=locationOtherOwners(loc.id);hint.textContent=others.length?'Адрес занят другим клиентом':`Найдено: ${loc.code}`;hint.className='fc-bulk-box-move-hint '+(others.length?'bad':'good');});$('bulkContainerMoveForm').addEventListener('submit',e=>{e.preventDefault();bulkMoveSelectedContainers().catch(err=>toast(err.message,true));});return modal;}
function openBulkContainerMove(){if(!selectedContainerIds.size)return toast('Сначала выберите короба галочками',true);const modal=ensureBulkContainerMoveModal();$('bulkContainerMoveSummary').textContent=`Выбрано коробов: ${selectedContainerIds.size}. Новый адрес появится в таблице сразу.`;$('bulkContainerMoveDestination').value='';$('bulkContainerMoveHint').textContent='Можно сканировать QR адреса.';$('bulkContainerMoveHint').className='fc-bulk-box-move-hint muted';modal.classList.remove('hidden');setTimeout(()=>$('bulkContainerMoveDestination')?.focus(),30);}
function applyOptimisticBulkContainerAddress(ids,destination,original){const wanted=new Set(ids.map(String));for(const c of activeContainers())if(wanted.has(String(c.id))){original.set(String(c.id),c.location_id||null);c.location_id=destination.id;c._bulkMovePending=true;c._bulkMoveError='';}for(const row of (state.inventory||[]))if(wanted.has(String(row.container_id||'')))row.location_id=destination.id;renderContainers();}
function rollbackBulkContainerAddress(id,originalLocation,error){const c=containerObj(id);if(c){c.location_id=originalLocation||null;c._bulkMovePending=false;c._bulkMoveError=String(error||'Не перемещено');}for(const row of (state.inventory||[]))if(String(row.container_id||'')===String(id))row.location_id=originalLocation||null;}
async function bulkMoveSelectedContainers(){await ensureContext();const ids=[...selectedContainerIds].filter(id=>containerObj(id));if(!ids.length)throw new Error('Сначала выберите короба');const input=$('bulkContainerMoveDestination'),submit=$('bulkContainerMoveSubmit'),raw=String(input?.value||'').trim();if(!raw)throw new Error('Отсканируйте или введите палету/адрес');submit.disabled=true;submit.textContent=`Ищем адрес…`;let destination;try{destination=findLocation(raw)||await resolveFreshLocation(raw);if(!destination)throw new Error('Адрес хранения не найден');assertLocationAvailableForClient(destination);if(String(destination.location_type||'').toUpperCase()==='SECTION')throw new Error('Выберите палету, палетоместо или ячейку, а не секцию');const original=new Map();applyOptimisticBulkContainerAddress(ids,destination,original);writeFastContextSnapshot(`${normalizeContextId(state.clientId)}|${normalizeContextId(state.warehouseId)}`);$('bulkContainerMoveModal')?.classList.add('hidden');toast(`Перемещаем ${ids.length} коробов → ${destination.code}. Новый адрес уже показан.`);submit.textContent=`Перемещаем ${ids.length}…`;const result=await api('/receiving/manual/boxes/bulk-move',{method:'POST',timeoutMs:90000,body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,destination_location_id:destination.id,container_ids:ids,request_id:uid(),performed_by:$('currentUser')?.textContent||null})});const rows=Array.isArray(result?.results)?result.results:[],byId=new Map(rows.map(x=>[String(x.container_id),x]));let ok=0,failed=0;const failedIds=[];for(const id of ids){const row=byId.get(String(id));const c=containerObj(id);if(row?.ok){ok++;if(c){c.location_id=destination.id;c._bulkMovePending=false;c._bulkMoveError='';}}else{failed++;failedIds.push(String(id));rollbackBulkContainerAddress(id,original.get(String(id)),row?.error||'Сервер не подтвердил перемещение');}}selectedContainerIds.clear();for(const id of failedIds)selectedContainerIds.add(id);renderContainers();writeFastContextSnapshot(`${normalizeContextId(state.clientId)}|${normalizeContextId(state.warehouseId)}`);if(failed)toast(`Перемещено ${ok} из ${ids.length} → ${destination.code}. Ошибок: ${failed}. Выделены только проблемные короба.`,true);else toast(`Готово: ${ok} коробов перемещено → ${destination.code}. Выбор снят.`);return result;}finally{submit.disabled=false;submit.textContent='Переместить';}}

installContainerMovementHelp();
let lastBulkBoxBatch=null;
function boxLabelLocalDate(){const d=new Date(),p=n=>String(n).padStart(2,'0');return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}`;}
function bulkBoxRangePreview(){
  const first=String($('bulkBoxFirst')?.value||'').trim(),count=Number($('bulkBoxCount')?.value||0),out=$('bulkBoxPreview');if(!out)return;
  const m=first.match(/^(.*?)(\d+)$/);if(!m||!Number.isInteger(count)||count<1){out.textContent='Пример: BOX_SAVASH_001 + 30 → BOX_SAVASH_001 … BOX_SAVASH_030';return;}
  const start=Number(m[2]),last=m[1]+String(start+count-1).padStart(m[2].length,'0');out.textContent=`Будет создано: ${first} … ${last} · ${count} шт.`;
}
function installContainerBulkQrGenerator(){
  const page=$('page-containers');if(!page||$('containerBulkQrPanel'))return;
  const panel=document.createElement('div');panel.id='containerBulkQrPanel';panel.className='panel';
  panel.innerHTML=`<div class="panel-head"><div><span class="kicker">КОРОБА В СИСТЕМЕ</span><h2>Создать короба и напечатать QR</h2><p>Новые короба создаются как <b>неразмещённые</b>. Палету, секцию или ячейку можно назначить позже.</p></div></div><form id="bulkBoxCreateForm" class="form-row"><label>Первый короб<input id="bulkBoxFirst" value="BOX_SAVASH_001" placeholder="BOX_SAVASH_001" required></label><label>Количество<input id="bulkBoxCount" type="number" min="1" max="1000" value="30" required></label><label class="checkline"><input id="bulkBoxShowDate" type="checkbox"> Дата создания QR внизу этикетки</label><button class="primary" type="submit">Создать короба</button></form><p id="bulkBoxPreview" class="muted"></p><div id="bulkBoxResult" class="hidden"><div class="success-box"><b id="bulkBoxResultText"></b><span>QR содержит видимое имя короба. Если включена дата — сверху остаётся имя короба, а снизу печатается дата создания QR.</span></div><div class="button-row"><button id="bulkBoxPrint" type="button" class="primary">Печать всех QR</button><button id="bulkBoxPdf" type="button" class="secondary">Печать через PDF</button></div></div>`;
  page.insertBefore(panel,page.firstChild);bulkBoxRangePreview();
  $('bulkBoxFirst').addEventListener('input',bulkBoxRangePreview);$('bulkBoxCount').addEventListener('input',bulkBoxRangePreview);
  $('bulkBoxCreateForm').addEventListener('submit',async e=>{e.preventDefault();const button=e.submitter||e.currentTarget.querySelector('button[type="submit"]');button.disabled=true;try{await ensureContext();const first_box=String($('bulkBoxFirst').value||'').trim(),count=Number($('bulkBoxCount').value||0);const r=await api('/receiving/manual/storage/boxes/bulk',{method:'POST',timeoutMs:120000,body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,first_box,count})});lastBulkBoxBatch={scope:`${state.clientId}|${state.warehouseId}`,ids:(r.rows||[]).map(x=>x.container_id),first:r.first_box,last:r.last_box,count:Number(r.count||0),showDate:Boolean($('bulkBoxShowDate')?.checked),labelDate:boxLabelLocalDate()};$('bulkBoxResult').classList.remove('hidden');$('bulkBoxResultText').textContent=`Готово: ${r.first_box} … ${r.last_box}. Создано ${r.created}${Number(r.recreated_from_archive||0)?` (из них заново после удаления: ${r.recreated_from_archive})`:''}, уже активных было ${r.existing}.`;await loadContextData();renderContainers();toast(`Короба готовы: ${r.count} шт.`);}catch(err){toast(err.message,true);}finally{button.disabled=false;}});
  $('bulkBoxPrint').addEventListener('click',()=>printBulkBoxBatch(false).catch(e=>toast(e.message,true)));
  $('bulkBoxPdf').addEventListener('click',()=>printBulkBoxBatch(true).catch(e=>toast(e.message,true)));
}
async function printBulkBoxBatch(forcePdf=false){
  if(!lastBulkBoxBatch?.ids?.length)throw new Error('Сначала создайте диапазон коробов');if(lastBulkBoxBatch.scope!==`${state.clientId}|${state.warehouseId}`)throw new Error('Клиент или склад изменён. Создайте диапазон заново.');
  const payload={client_id:state.clientId,warehouse_id:state.warehouseId,container_ids:lastBulkBoxBatch.ids,width_mm:58,height_mm:40,show_date:Boolean(lastBulkBoxBatch.showDate),label_date:lastBulkBoxBatch.labelDate||boxLabelLocalDate()},ps=wbPrintSettings();
  if(!forcePdf&&ps.mode==='agent'){
    const r=await api('/receiving/manual/boxes/labels/queue',{method:'POST',timeoutMs:120000,body:JSON.stringify({...payload,print_station:ps.station})});toast(`В очередь ${r.print_station} отправлено QR: ${r.labels_queued}`);return r;
  }
  const blob=await apiBlob('/receiving/manual/boxes/labels.pdf',{method:'POST',timeoutMs:120000,body:JSON.stringify(payload)});printBlob(blob,`Короба_${lastBulkBoxBatch.first}_${lastBulkBoxBatch.last}.pdf`);toast(`Открыта массовая печать QR: ${lastBulkBoxBatch.count} шт.`);
}
async function printExistingContainerLabels(ids,forcePdf=false){
  await ensureContext();
  const active=new Map(activeContainers().map(c=>[String(c.id),c])),unique=[...new Set((ids||[]).map(String))].filter(id=>active.has(id));
  if(!unique.length)throw new Error('Сначала выберите существующие короба галочками');
  const payload={client_id:state.clientId,warehouse_id:state.warehouseId,container_ids:unique,width_mm:58,height_mm:40,show_date:false,label_date:boxLabelLocalDate()},ps=wbPrintSettings();
  if(!forcePdf&&ps.mode==='agent'){
    const r=await api('/receiving/manual/boxes/labels/queue',{method:'POST',timeoutMs:120000,body:JSON.stringify({...payload,print_station:ps.station})});
    toast(`Повторная печать: в очередь ${r.print_station} отправлено ШК коробов: ${r.labels_queued}`);return r;
  }
  const blob=await apiBlob('/receiving/manual/boxes/labels.pdf',{method:'POST',timeoutMs:120000,body:JSON.stringify(payload)});
  const names=unique.map(id=>containerPublicCode(active.get(id))),fileName=names.length===1?`Короб_${names[0]}.pdf`:`Короба_выбранные_${names.length}.pdf`;
  printBlob(blob,fileName);toast(`Открыта повторная печать ШК выбранных коробов: ${unique.length} шт.`);return {ok:true,labels_queued:unique.length,pdf:true};
}
installContainerBulkQrGenerator();
let lastLooseBoxLabelBatch=null;
function looseBoxRangePreview(){
  const first=String($('looseBoxFirst')?.value||'').trim(),count=Number($('looseBoxCount')?.value||0),out=$('looseBoxPreview');if(!out)return;
  const m=first.match(/^(.*?)(\d+)$/);if(!m||!Number.isInteger(count)||count<1){out.textContent='Пример: BOX_SAVASH_001 + 30 → BOX_SAVASH_001 … BOX_SAVASH_030';return;}
  const start=Number(m[2]),last=m[1]+String(start+count-1).padStart(m[2].length,'0');out.textContent=`Будет подготовлено ШК: ${first} … ${last} · ${count} шт. · в базу коробов не попадут`;
}
function installLooseBoxQrGenerator(){
  const systemPanel=$('containerBulkQrPanel');if(!systemPanel||$('looseBoxQrPanel'))return;
  const panel=document.createElement('div');panel.id='looseBoxQrPanel';panel.className='panel';
  panel.innerHTML=`<div class="panel-head"><div><span class="kicker">ТОЛЬКО ПЕЧАТЬ · БЕЗ БАЗЫ</span><h2>Создание ШК коробов</h2><p>Генерирует QR/ШК коробов для печати, но <b>не создаёт короба в FulfillCore</b>. Их можно использовать отдельно от складской базы.</p></div></div><form id="looseBoxQrForm" class="form-row"><label>Первый ШК короба<input id="looseBoxFirst" value="BOX_SAVASH_001" placeholder="BOX_SAVASH_001" required></label><label>Количество<input id="looseBoxCount" type="number" min="1" max="1000" value="30" required></label><label class="checkline"><input id="looseBoxShowDate" type="checkbox" checked> Дата создания QR внизу этикетки</label><button class="primary" type="submit">Подготовить ШК</button></form><p id="looseBoxPreview" class="muted"></p><div id="looseBoxResult" class="hidden"><div class="success-box"><b id="looseBoxResultText"></b><span>Эти ШК существуют только как печатные этикетки. В список коробов и остатки они не добавляются.</span></div><div class="button-row"><button id="looseBoxPrint" type="button" class="primary">Печать всех QR</button><button id="looseBoxPdf" type="button" class="secondary">Печать через PDF</button></div></div>`;
  systemPanel.insertAdjacentElement('afterend',panel);looseBoxRangePreview();
  $('looseBoxFirst').addEventListener('input',looseBoxRangePreview);$('looseBoxCount').addEventListener('input',looseBoxRangePreview);
  $('looseBoxQrForm').addEventListener('submit',async e=>{e.preventDefault();await ensureContext();const first=String($('looseBoxFirst').value||'').trim(),count=Number($('looseBoxCount').value||0),m=first.match(/^(.*?)(\d+)$/);if(!m||!Number.isInteger(count)||count<1||count>1000)return toast('Укажите первый ШК с номером в конце и количество от 1 до 1000',true);const last=m[1]+String(Number(m[2])+count-1).padStart(m[2].length,'0');lastLooseBoxLabelBatch={scope:`${state.clientId}|${state.warehouseId}`,first,last,count,showDate:Boolean($('looseBoxShowDate')?.checked),labelDate:boxLabelLocalDate()};$('looseBoxResult').classList.remove('hidden');$('looseBoxResultText').textContent=`Готово к печати: ${first} … ${last} · ${count} шт. Короба в базу не добавлены.`;toast(`Подготовлено ШК: ${count} шт.`);});
  $('looseBoxPrint').addEventListener('click',()=>printLooseBoxBatch(false).catch(e=>toast(e.message,true)));
  $('looseBoxPdf').addEventListener('click',()=>printLooseBoxBatch(true).catch(e=>toast(e.message,true)));
}
async function printLooseBoxBatch(forcePdf=false){
  if(!lastLooseBoxLabelBatch)throw new Error('Сначала подготовьте диапазон ШК');if(lastLooseBoxLabelBatch.scope!==`${state.clientId}|${state.warehouseId}`)throw new Error('Клиент или склад изменён. Подготовьте диапазон заново.');
  const payload={client_id:state.clientId,warehouse_id:state.warehouseId,first_box:lastLooseBoxLabelBatch.first,count:lastLooseBoxLabelBatch.count,width_mm:58,height_mm:40,show_date:Boolean(lastLooseBoxLabelBatch.showDate),label_date:lastLooseBoxLabelBatch.labelDate||boxLabelLocalDate()},ps=wbPrintSettings();
  if(!forcePdf&&ps.mode==='agent'){
    const r=await api('/receiving/manual/box-labels-only/labels/queue',{method:'POST',timeoutMs:120000,body:JSON.stringify({...payload,print_station:ps.station})});toast(`В очередь ${r.print_station} отправлено QR: ${r.labels_queued}. В базу коробов не добавлено.`);return r;
  }
  const blob=await apiBlob('/receiving/manual/box-labels-only/labels.pdf',{method:'POST',timeoutMs:120000,body:JSON.stringify(payload)});printBlob(blob,`ШК_коробов_${lastLooseBoxLabelBatch.first}_${lastLooseBoxLabelBatch.last}.pdf`);toast(`Открыта печать ШК: ${lastLooseBoxLabelBatch.count} шт. · без добавления в базу`);
}
installLooseBoxQrGenerator();
let containerContentsSequence=0;
async function showContainer(id,{quiet=false}={}){
  const request=++containerContentsSequence,captured=`${state.clientId}|${state.warehouseId}`,current=containerObj(id);
  if(!current||current.status!=='ACTIVE')throw Error('Короб не найден на выбранном складе');
  if(!$('containerDetailPanel')){const panel=document.createElement('div');panel.id='containerDetailPanel';panel.className='panel';$('page-containers').appendChild(panel);}
  const panel=$('containerDetailPanel');panel.dataset.containerId=String(id);panel.classList.remove('hidden');
  try{
    const d=await api(`/masterdata/containers/${encodeURIComponent(id)}/contents?`+new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId}));
    if(request!==containerContentsSequence||captured!==`${state.clientId}|${state.warehouseId}`)return;
    if(String(d.container?.client_id)!==String(state.clientId)||String(d.container?.warehouse_id)!==String(state.warehouseId))throw Error('Ответ относится к другому складу');
    const items=(d.items||[]).filter(x=>Number(x.quantity)>0),total=items.reduce((n,x)=>n+Number(x.quantity),0);
    panel.innerHTML=`<div class="panel-head"><div><span class="kicker">СОСТАВ КОНКРЕТНОГО КОРОБА</span><h2>${esc(containerPublicCode(d.container))}</h2><p>${fmtQty(total)} шт. в этом коробе</p></div><div class="tag">${esc(d.location?.code||'Без ячейки')}</div></div><div class="table-wrap"><table><thead><tr><th>Товар</th><th>Размер</th><th>ШК</th><th>Физически в коробе</th><th>В резерве</th><th>Свободно</th><th>Действия</th></tr></thead><tbody>${items.map(x=>`<tr><td><button type="button" class="product-title-link fc-box-product-link" data-fc-product-card-barcode="${esc(x.barcode||'')}"><b>${esc(x.article||x.sku_code)}</b><small>${esc(x.name||'')}</small></button></td><td>${esc(x.size||'—')}</td><td>${esc(x.barcode||'—')}</td><td>${fmtQty(x.quantity)}</td><td>${fmtQty(x.reserved_quantity||0)}</td><td>${fmtQty(x.free_quantity??Math.max(0,Number(x.quantity)-Number(x.reserved_quantity||0)-Number(x.accounting_pending_quantity||0)))}</td><td>${window.FCStockActions?.buttons({sku_ids:[x.sku_id],location_id:d.container.location_id,container_id:id,barcode:x.barcode,article:x.article||x.sku_code||'',size:x.size||'',seed:{article:x.article||x.sku_code||'',size:x.size||'',barcode:x.barcode||'',location:d.location?.code||'',container:containerPublicCode(d.container),quantity:Number(x.quantity||0),free:Number(x.free_quantity??Math.max(0,Number(x.quantity||0)-Number(x.reserved_quantity||0)-Number(x.accounting_pending_quantity||0)))}})||''}</td></tr>`).join('')||'<tr><td colspan="7">Короб пуст</td></tr>'}</tbody></table></div>`;
    if(!quiet)panel.scrollIntoView({behavior:'smooth',block:'start'});
  }catch(e){if(request===containerContentsSequence){panel.innerHTML='<p class="bad">'+esc(e.message)+'</p>';toast(e.message,true);}throw e;}
}

async function renameContainer(id){
  const c=containerObj(id);if(!c)return;const name=prompt('Новое название короба:',containerPublicCode(c));if(name===null)return;if(!name.trim())return toast('Название не может быть пустым',true);
  try{await api(`/masterdata/containers/${id}`,{method:'PATCH',body:JSON.stringify({name:name.trim()})});await loadContextData();toast('Короб переименован. Напечатайте новую этикетку с новым именем.');}catch(e){toast(e.message,true)}
}
async function moveContainerFromList(id){const c=containerObj(id);if(!c)return;const publicCode=containerPublicCode(c),raw=prompt(`Куда переместить короб ${publicCode}?\n\nВведите код или отсканируйте QR ячейки/палеты/палеточного места:`,locationLabel(c.location_id)==='—'?'':locationLabel(c.location_id));if(raw===null)return;const destination=findLocation(raw);if(!destination)throw new Error('Адрес хранения не найден');assertLocationAvailableForClient(destination);if(String(destination.id)===String(c.location_id))throw new Error('Короб уже находится по этому адресу');if(!state.inventory.some(x=>String(x.container_id)===String(c.id)&&Number(x.quantity)!==0)){await api('/box-intake/boxes/'+encodeURIComponent(c.id)+'/move-empty',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,destination_location_id:destination.id})});applyLocalContainerMove(c,destination);toast('Короб '+publicCode+' перемещён → '+destination.code);refreshContextAfterContainerMove();return;}const preview=await fetchMoveContainerPreview(c,destination);if(!preview?.can_move)throw new Error(preview?.block_message||'В коробе есть резерв активного сборочного листа. Сначала упакуйте товар.');if(Number(preview.active_list_other_bucket_quantity||0)>0&&!confirm(`${preview.warning_message}\n\nПереместить короб ${publicCode}?`))return;await api('/operations/move-container',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,idempotency_key:uid(),performed_by:$('currentUser')?.textContent||null,reference:null,comment:'Перемещение короба из списка коробов',container_id:c.id,destination_location_id:destination.id})});applyLocalContainerMove(c,destination);toast(`Короб ${publicCode} перемещён → ${destination.code}${Number(preview.new_reservations_follow_quantity||0)>0?` · NEW резерв перенесён ${fmtQty(preview.new_reservations_follow_quantity)} шт.`:''}`);refreshContextAfterContainerMove();}
function operationBalanceChanges(allOps){
  const balance=new Map(),changes=new Map(),balanceKey=(sku,location,container)=>`${sku||''}|${location||''}|${container||''}`;
  for(const item of (state.inventory||[])){const key=balanceKey(item.sku_id,item.location_id,item.container_id);balance.set(key,Number(balance.get(key)||0)+Number(item.quantity||0));}
  for(const operation of allOps){for(const line of (operation.lines||[])){
    const amount=Math.abs(Number(line.quantity||0)),source=line.source_location_id?balanceKey(line.sku_id,line.source_location_id,line.source_container_id):'',destination=line.destination_location_id?balanceKey(line.sku_id,line.destination_location_id,line.destination_container_id):'';let text='',delta=fmtQty(amount);
    if(source&&destination){const sourceAfter=Number(balance.get(source)||0),sourceBefore=sourceAfter+amount,destinationAfter=Number(balance.get(destination)||0),destinationBefore=destinationAfter-amount;text=`Откуда: ${fmtQty(sourceBefore)} → ${fmtQty(sourceAfter)} · Куда: ${fmtQty(destinationBefore)} → ${fmtQty(destinationAfter)}`;balance.set(source,sourceBefore);balance.set(destination,destinationBefore);}
    else if(source){const after=Number(balance.get(source)||0),before=after+amount;text=`Было ${fmtQty(before)} → стало ${fmtQty(after)}`;delta=`−${fmtQty(amount)}`;balance.set(source,before);}
    else if(destination){const after=Number(balance.get(destination)||0),before=after-amount;text=`Было ${fmtQty(before)} → стало ${fmtQty(after)}`;delta=`+${fmtQty(amount)}`;balance.set(destination,before);}
    else{text='Начальный и конечный адрес не указаны';}
    changes.set(line,{text,delta});
  }}
  return changes;
}
function isReceivingHistoryOperation(operation){
  const kind=String(operation?.operation_type||'').toUpperCase(),comment=String(operation?.comment||'').toLocaleLowerCase('ru-RU');
  return ['RECEIVING','RECEIVING_IMPORT'].includes(kind)||(kind==='ADJUSTMENT'&&comment.includes('приём'));
}
function receivingHistoryTitle(operation){
  const reference=String(operation?.reference||'').trim();if(reference)return reference;
  const kind=String(operation?.operation_type||'').toUpperCase(),base=kind==='RECEIVING_IMPORT'?'Приёмка из Excel':kind==='ADJUSTMENT'?'Корректировка приёмки':'Ручная приёмка',suffix=String(operation?.id||'').replaceAll('-','').slice(-6).toUpperCase();
  return `${base}${suffix?` №${suffix}`:''}`;
}
function receivingHistoryPlace(line){
  const locationId=line.destination_location_id||line.source_location_id,containerId=line.destination_container_id||line.source_container_id,location=locationLabel(locationId),container=containerId?containerLabel(containerId):'';
  return `${location}${container?` · ${container}`:''}`;
}
function receivingHistoryStats(operation){
  const lines=operation?.lines||[],quantity=lines.reduce((sum,line)=>sum+Math.abs(Number(line.quantity||0)),0),positions=new Set(lines.map(line=>String(line.sku_id||'')).filter(Boolean)).size,addresses=new Set(lines.map(receivingHistoryPlace).filter(place=>place&&place!=='—')).size;
  return {lines,quantity,positions,addresses};
}
function receivingHistorySearchText(operation,changes){
  const lineText=(operation.lines||[]).flatMap(line=>{const sku=mapBy(state.skus,line.sku_id),change=changes.get(line)||{};return [skuLabel(sku),sku?.barcode,receivingHistoryPlace(line),change.text,line.reason];});
  return [receivingHistoryTitle(operation),opName(operation.operation_type),operation.performed_by,operation.reference,operation.comment,...lineText].filter(Boolean).join(' ').toLocaleLowerCase('ru-RU');
}
function renderReceivingHistoryBrowser(operations,changes,query){
  const browser=$('receivingHistoryBrowser'),summary=$('operationHistorySummary');if(!browser)return;
  const filtered=operations.filter(operation=>!query||receivingHistorySearchText(operation,changes).includes(query)),selected=filtered.find(operation=>String(operation.id)===String(receivingHistoryOpenOperationId));
  if(receivingHistoryOpenOperationId&&!selected)receivingHistoryOpenOperationId='';
  if(!selected){
    const active=filtered.filter(operation=>String(operation.status||'COMPLETED').toUpperCase()!=='CANCELLED'),cancelled=filtered.length-active.length,total=active.reduce((sum,operation)=>sum+receivingHistoryStats(operation).quantity,0);
    if(summary)summary.textContent=`Приёмок: ${filtered.length} · сейчас учтено как принято: ${fmtQty(total)} шт.${cancelled?` · отменено приёмок: ${cancelled}`:''}`;
    browser.innerHTML=`<div class="receiving-history-list-head"><div><h3>История приёмок</h3><p>Каждая карточка — одна проведённая приёмка. Откройте её, чтобы увидеть товары и размещение.</p></div><span>${filtered.length} шт.</span></div><div class="receiving-history-list">${filtered.map(operation=>{const stats=receivingHistoryStats(operation),date=new Date(operation.created_at),cancelled=String(operation.status||'').toUpperCase()==='CANCELLED';return `<article class="receiving-history-card ${cancelled?'receipt-cancelled':''}"><div class="receiving-history-date"><b>${date.toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'})}</b><span>${date.toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'})}</span></div><div class="receiving-history-main"><span class="operation-badge operation-${esc(String(operation.operation_type||'').toLowerCase())}">${esc(opName(operation.operation_type))}</span><h3>${esc(receivingHistoryTitle(operation))}</h3><small>${esc(operation.performed_by||'Сотрудник не указан')}${cancelled?' · Приёмка отменена':''}</small></div><div class="receiving-history-stats"><span><b>${fmtQty(stats.quantity)}</b> шт.</span><span><b>${stats.positions}</b> поз.</span><span><b>${stats.addresses}</b> адрес.</span></div><button type="button" class="primary mini" data-open-receiving-operation="${esc(operation.id)}">Открыть приёмку</button></article>`;}).join('')||'<div class="empty-state">Приёмок по выбранному фильтру не найдено.</div>'}</div>`;
    return;
  }
  const stats=receivingHistoryStats(selected),date=new Date(selected.created_at),cancelled=String(selected.status||'').toUpperCase()==='CANCELLED',addressTotals=new Map();
  for(const line of stats.lines){const place=receivingHistoryPlace(line);addressTotals.set(place,Number(addressTotals.get(place)||0)+Math.abs(Number(line.quantity||0)));}
  const detailRows=stats.lines.map((line,index)=>{const sku=mapBy(state.skus,line.sku_id)||{},change=changes.get(line)||{text:'',delta:fmtQty(line.quantity)},place=receivingHistoryPlace(line),title=sku.name||skuLabel(sku)||'Неизвестный товар',meta=[sku.article||sku.sku_code,sku.size].filter(Boolean).join(' · ');return `<tr><td>${index+1}</td><td><b>${esc(title)}</b>${meta?`<small class="subline">${esc(meta)}</small>`:''}<small class="subline">ШК: ${esc(sku.barcode||'—')}</small></td><td class="qty"><b>${fmtQty(Math.abs(Number(line.quantity||0)))} шт.</b><small class="subline balance-change">${esc(change.text||'')}</small></td><td><b>${esc(place)}</b></td><td>${esc(line.reason||'—')}</td></tr>`;}).join('');
  if(summary)summary.textContent=`${date.toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'})} ${date.toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'})} · ${stats.positions} поз. · ${fmtQty(stats.quantity)} шт.`;
  browser.innerHTML=`<div class="receiving-history-detail"><div class="receiving-history-detail-head"><button type="button" class="secondary" data-receiving-history-back>← Все приёмки</button><div><span class="kicker">ПРИЁМКА</span><h2>${esc(receivingHistoryTitle(selected))}</h2><p>${date.toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'})} · ${date.toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'})} · ${esc(selected.performed_by||'Сотрудник не указан')}</p></div>${canCancelReceivingOperation(selected)?`<button type="button" class="danger" data-cancel-receiving-operation="${esc(selected.id)}">Отменить приёмку</button>`:cancelled?'<span class="tag">Отменена · история сохранена</span>':''}</div><div class="receiving-history-detail-stats"><span><b>${fmtQty(stats.quantity)}</b> всего штук</span><span><b>${stats.positions}</b> товаров</span><span><b>${stats.lines.length}</b> строк</span><span><b>${stats.addresses}</b> адресов</span></div><div class="receiving-history-addresses"><h3>Куда размещено</h3>${[...addressTotals.entries()].map(([place,quantity])=>`<span><b>${esc(place)}</b>${fmtQty(quantity)} шт.</span>`).join('')||'<span>Адреса не указаны</span>'}</div><div class="table-wrap"><table><thead><tr><th>№</th><th>Товар</th><th>Количество</th><th>Ячейка / короб</th><th>Основание</th></tr></thead><tbody>${detailRows||'<tr><td colspan="5" class="empty-state">В этой приёмке нет строк.</td></tr>'}</tbody></table></div></div>`;
}

let unifiedReceiptHistoryRows=[];
let unifiedReceiptHistoryContext='';
let unifiedReceiptHistoryLoading=null;
async function loadUnifiedReceiptHistoryForOperations(force=false){
  if(!state.clientId||!state.warehouseId)return [];
  const contextKey=`${normalizeContextId(state.clientId)}|${normalizeContextId(state.warehouseId)}`;
  if(!force&&unifiedReceiptHistoryContext===contextKey&&Array.isArray(unifiedReceiptHistoryRows)&&unifiedReceiptHistoryRows.length)return unifiedReceiptHistoryRows;
  if(unifiedReceiptHistoryLoading&&unifiedReceiptHistoryContext===contextKey)return unifiedReceiptHistoryLoading;
  unifiedReceiptHistoryContext=contextKey;
  const job=(async()=>{
    const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId,limit:'500',offset:'0'});
    const data=await api(`/warehouse/receipts/recent-grouped?${q.toString()}`,{timeoutMs:30000});
    if(contextKey!==`${normalizeContextId(state.clientId)}|${normalizeContextId(state.warehouseId)}`)return [];
    unifiedReceiptHistoryRows=Array.isArray(data?.rows)?data.rows:[];
    return unifiedReceiptHistoryRows;
  })();
  unifiedReceiptHistoryLoading=job;
  try{return await job;}finally{if(unifiedReceiptHistoryLoading===job)unifiedReceiptHistoryLoading=null;}
}
function unifiedReceiptSearchText(row){
  return [row.id,row.reference,row.performed_by,row.type,row.zone,row.status,row.created_at,row.idempotency_key,row.structured_receipt_id]
    .filter(v=>v!==null&&v!==undefined).join(' ').toLocaleLowerCase('ru-RU');
}
function unifiedReceiptTypeLabel(row){
  const type=String(row?.type||'').toUpperCase();
  return type==='STRUCTURED'?'Структурная Excel':type==='EXCEL'?'Excel':type==='TSD'?'Приёмка ТСД':type==='PVZ'?'Приёмка ПВЗ':type==='MANUAL'?'Ручная':'Приёмка';
}
function unifiedReceiptZoneLabel(row){
  const zone=String(row?.zone||'').toUpperCase();
  return zone==='MAIN'?'Основной склад':zone==='FBS'?'WB FBS':'Склад';
}
function unifiedBulkBatchId(row){
  const key=String(row?.idempotency_key||'');
  const m=key.match(/^bulk-receive-(.+)$/i);
  return m?m[1]:'';
}
function receiptGroupIds(row){return Array.isArray(row?.operation_ids)&&row.operation_ids.length?row.operation_ids.map(String):[String(row?.id||'')].filter(Boolean);}
function receiptGroupQuery(value,{isKey=false}={}){const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId});if(isKey)q.set('receipt_group_key',String(value||''));else for(const id of (Array.isArray(value)?value:[]))q.append('operation_id',id);return q;}
async function openUnifiedReceiptGroup(groupKey){const browser=$('receivingHistoryBrowser');if(!browser)return;const q=receiptGroupQuery(groupKey,{isKey:true}),d=await api(`/warehouse/receipts/group-detail?${q.toString()}`);const date=d.operations?.[0]?.created_at?new Date(d.operations[0].created_at):null;browser.innerHTML=`<div class="receiving-history-detail"><div class="receiving-history-detail-head"><button type="button" class="secondary" data-history-group-back>← Все приёмки</button><div><span class="kicker">ОБЪЕДИНЁННАЯ ПРИЁМКА</span><h2>${esc(d.title||'Приёмка')}</h2><p>${date?date.toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'}):''} · ${esc(d.performed_by||'Сотрудник не указан')} · объединено операций: ${Number(d.operation_count||0)}</p></div><button type="button" class="secondary" data-history-group-excel="${esc(groupKey)}">Состав Excel</button></div><div class="receiving-history-detail-stats"><span><b>${fmtQty(d.units||0)}</b> всего штук</span><span><b>${Number(d.positions||0)}</b> товаров</span><span><b>${(d.lines||[]).length}</b> строк состава</span></div><div class="table-wrap"><table><thead><tr><th>Дата</th><th>Товар</th><th>ШК</th><th>Кол-во</th><th>Ячейка</th><th>Короб</th><th>Сотрудник</th></tr></thead><tbody>${(d.lines||[]).map(x=>`<tr><td>${x.created_at?new Date(x.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):''}</td><td><b>${esc(x.article||'—')} · ${esc(x.size||'—')}</b><small class="subline">${esc(x.name||'')}</small></td><td>${esc(x.barcode||'—')}</td><td><b>${fmtQty(x.quantity||0)}</b></td><td>${esc(x.location||'—')}</td><td>${esc(x.container||'—')}</td><td>${esc(x.performed_by||'—')}</td></tr>`).join('')||'<tr><td colspan="7" class="empty-state">В приёмке нет строк</td></tr>'}</tbody></table></div></div>`;}
function downloadReceiptGroupExcel(groupKey){window.location.href='/warehouse/receipts/group-detail.xlsx?'+receiptGroupQuery(groupKey,{isKey:true}).toString();}
function renderUnifiedReceiptHistory(query='',filter='RECEIVING_ALL'){
  const browser=$('receivingHistoryBrowser'),summary=$('operationHistorySummary');if(!browser)return;
  const wanted=String(filter||'').toUpperCase();
  const all=Array.isArray(unifiedReceiptHistoryRows)?unifiedReceiptHistoryRows:[];
  const rows=all.filter(row=>{
    const type=String(row.type||'').toUpperCase();
    if(wanted==='RECEIVING'&&!['MANUAL','TSD','PVZ'].includes(type))return false;
    if(wanted==='RECEIVING_IMPORT'&&!['EXCEL','STRUCTURED'].includes(type))return false;
    return !query||unifiedReceiptSearchText(row).includes(query);
  });
  const active=rows.filter(row=>String(row.status||'').toUpperCase()!=='CANCELLED');
  const total=active.reduce((s,row)=>s+Number(row.units||0),0);
  const structured=rows.filter(row=>String(row.type||'').toUpperCase()==='STRUCTURED').length;
  if(summary)summary.textContent=`Приёмок: ${rows.length} · сейчас учтено: ${fmtQty(total)} шт.${structured?` · структурных: ${structured}`:''}`;
  browser.innerHTML=`<div class="receiving-history-list-head"><div><h3>История приёмок</h3><p>Здесь вместе показаны структурные Excel, обычные Excel, ручные, ТСД- и ПВЗ-приёмки. История берётся из фактически проведённых складских операций.</p></div><span>${rows.length} шт.</span></div>
    <div class="receiving-history-list">${rows.map(row=>{
      const date=row.created_at?new Date(row.created_at):null,cancelled=String(row.status||'').toUpperCase()==='CANCELLED',type=String(row.type||'').toUpperCase(),batchId=unifiedBulkBatchId(row);
      return `<article class="receiving-history-card ${cancelled?'receipt-cancelled':''}">
        <div class="receiving-history-date"><b>${date?date.toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'}):'—'}</b><span>${date?date.toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'}):''}</span></div>
        <div class="receiving-history-main"><span class="operation-badge operation-receiving_import">${esc(unifiedReceiptTypeLabel(row))}</span><h3>${esc(row.reference||'Приёмка')}</h3><small>${esc(unifiedReceiptZoneLabel(row))}${row.performed_by?` · ${esc(row.performed_by)}`:''}${Number(row.operation_count||1)>1?` · объединено: ${Number(row.operation_count)} частей`:''}${cancelled?' · Отменена':''}</small></div>
        <div class="receiving-history-stats"><span><b>${fmtQty(row.units||0)}</b> шт.</span><span><b>${Number(row.lines||0)}</b> строк</span><span><b>${fmtQty(row.transferred||0)}</b> сейчас FBS</span>${Number(row.reloadable||0)>0?`<span><b>${fmtQty(row.reloadable)}</b> можно загрузить повторно</span>`:''}</div>
        <div class="button-row">
          ${receiptGroupIds(row).length>1?`<button type="button" class="primary mini" data-history-group-open="${esc(row.receipt_group_key||'')}">Состав (${receiptGroupIds(row).length} частей)</button><button type="button" class="secondary mini" data-history-group-excel="${esc(row.receipt_group_key||'')}">Состав Excel</button>`:`<button type="button" class="primary mini" data-history-receipt-open="${esc(row.id)}">Состав / FBS</button>`}
          ${cancelled?'':`<button type="button" class="danger mini" data-r22-cancel-receipt="${esc(receiptGroupIds(row).join(','))}">Удалить / отменить приёмку</button>`}
          ${type==='STRUCTURED'&&row.structured_receipt_id?`<button type="button" class="secondary mini" data-history-structured-open="${esc(row.structured_receipt_id)}">Структура / ряды</button>`:''}
          ${batchId?`<button type="button" class="secondary mini" data-bulk-details-excel="${esc(batchId)}">Состав в Excel</button><button type="button" class="secondary mini" data-bulk-errors-excel="${esc(batchId)}">Ошибки WB Excel</button>`:''}
        </div>
      </article>`;
    }).join('')||'<div class="empty-state">Приёмок по выбранному фильтру не найдено.</div>'}</div>`;
}
async function showUnifiedReceiptHistory(query='',filter='RECEIVING_ALL'){
  const browser=$('receivingHistoryBrowser');if(browser)browser.innerHTML='<div class="empty-state">Загружаем приёмки…</div>';
  try{await loadUnifiedReceiptHistoryForOperations(false);renderUnifiedReceiptHistory(query,filter);}
  catch(err){if(browser)browser.innerHTML=`<div class="warn-box">Не удалось загрузить историю приёмок: ${esc(err.message)}</div>`;}
}

function renderOperations(){
  const filter=$('operationFilter'),f=filter?.value||'',q=($('operationSearch')?.value||'').trim().toLocaleLowerCase('ru-RU'),rows=[],allOps=[...(state.operations||[])].sort((a,b)=>new Date(b.created_at||0)-new Date(a.created_at||0)),changes=operationBalanceChanges(allOps),browser=$('receivingHistoryBrowser'),tableWrap=$('operationsBody')?.closest('.table-wrap'),receivingMode=['RECEIVING_ALL','RECEIVING','RECEIVING_IMPORT'].includes(f);
  if(receivingMode){
    if(browser)browser.classList.remove('hidden');if(tableWrap)tableWrap.classList.add('hidden');
    // Excel receipts are grouped by batch_id directly from the database, not from the
    // generic 300-operation client cache. Therefore one large receipt can no longer
    // hide the previous receipt.
    if(f==='RECEIVING_ALL'||f==='RECEIVING_IMPORT'){
      showUnifiedReceiptHistory(q,f);return;
    }
    const operations=allOps.filter(operation=>String(operation.operation_type||'').toUpperCase()===f);
    renderReceivingHistoryBrowser(operations,changes,q);return;
  }
  receivingHistoryOpenOperationId='';if(browser)browser.classList.add('hidden');if(tableWrap)tableWrap.classList.remove('hidden');
  const ops=allOps.filter(operation=>!f||operation.operation_type===f);
  for(const operation of ops){for(const [lineIndex,line] of (operation.lines||[]).entries()){const sku=mapBy(state.skus,line.sku_id),from=`${locationLabel(line.source_location_id)}${line.source_container_id?' · '+containerLabel(line.source_container_id):''}`,to=`${locationLabel(line.destination_location_id)}${line.destination_container_id?' · '+containerLabel(line.destination_container_id):''}`,change=changes.get(line)||{text:'',delta:fmtQty(line.quantity)},cancelled=String(operation.status||'').toUpperCase()==='CANCELLED',search=[opName(operation.operation_type),skuLabel(sku),from,to,change.text,operation.performed_by,operation.reference,operation.comment,cancelled?'отменена':''].filter(Boolean).join(' ').toLocaleLowerCase('ru-RU');if(q&&!search.includes(q))continue;const cancelButton=lineIndex===0&&canCancelReceivingOperation(operation)?`<button type="button" class="mini danger" data-cancel-receiving-operation="${esc(operation.id)}">Отменить приёмку</button>`:'';rows.push(`<tr class="${cancelled?'operation-cancelled':''}"><td><b>${new Date(operation.created_at).toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'})}</b><small class="subline">${new Date(operation.created_at).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'})}</small></td><td><span class="operation-badge operation-${esc(String(operation.operation_type||'').toLowerCase())}">${esc(opName(operation.operation_type))}</span>${cancelled?'<small class="subline bad">Приёмка отменена</small>':''}</td><td>${esc(skuLabel(sku))}</td><td class="qty"><b>${esc(change.delta)} шт.</b><small class="subline balance-change">${esc(change.text)}</small></td><td>${esc(from)}</td><td>${esc(to)}</td><td>${esc(operation.performed_by||'—')}</td><td>${esc(operation.reference||operation.comment||'—')}${cancelButton?`<div class="operation-actions">${cancelButton}</div>`:''}</td></tr>`);}}
  if($('operationHistorySummary'))$('operationHistorySummary').textContent=`Показано движений: ${rows.length} · операций загружено: ${state.operations.length}`;
  $('operationsBody').innerHTML=rows.length?rows.join(''):'<tr><td colspan="8" class="empty-state">Операций не найдено</td></tr>';
}
function canCancelReceivingOperation(operation){
  if(!operation||String(operation.status||'').toUpperCase()==='CANCELLED')return false;
  const kind=String(operation.operation_type||'').toUpperCase(),comment=String(operation.comment||'').toLocaleLowerCase('ru-RU');
  return ['RECEIVING','RECEIVING_IMPORT'].includes(kind)||(kind==='ADJUSTMENT'&&comment.includes('приём'));
}
async function cancelReceivingFromHistory(operationId){
  const operation=state.operations.find(x=>String(x.id)===String(operationId));if(!operation)throw new Error('Приёмка не найдена в загруженной истории');
  if(!confirm('Отменить эту приёмку?\n\nОстаток на складе будет возвращён обратной корректировкой, исходная запись останется в истории. Если товар уже отгружен, отмена будет заблокирована.\n\nВы уверены?'))return;
  const syncWb=confirm('Также пересчитать остаток WB FBS по текущему фактическому остатку склада?\n\n«ОК» — пересчитать WB. «Отмена» — изменить только FulfillCore.');
  const qctx=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId});const result=await api(`/receiving/operations/${encodeURIComponent(operationId)}/cancel?${qctx.toString()}`,{method:'POST',body:JSON.stringify({sync_wb:syncWb,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null})});
  reconcilePendingWbAfterCancellation(result);
  await loadContextData();state.inventoryCatalogLoaded=false;if($('page-inventory')?.classList.contains('active'))await loadInventoryCatalog();
  const failed=(result.wb_results||[]).filter(x=>!x.ok);toast(failed.length?`Приёмка отменена на складе, но WB не обновил ${failed.length} поз.`:syncWb?'Приёмка отменена, остаток WB пересчитан':'Приёмка отменена, история сохранена',failed.length>0);
}
function installOperationHistoryControls(){
  const filter=$('operationFilter');if(!filter||$('operationSearch'))return;
  const receivingOption=document.createElement('option');receivingOption.value='RECEIVING_ALL';receivingOption.textContent='Все приёмки';filter.insertBefore(receivingOption,filter.options[1]||null);filter.value='RECEIVING_ALL';
  const headers=$('operationsBody')?.closest('table')?.querySelectorAll('thead th');if(headers?.[3])headers[3].textContent='Изменение остатка';
  const wrap=document.createElement('div');wrap.className='operation-history-tools';wrap.innerHTML='<label>Поиск по истории<input id="operationSearch" placeholder="Название, товар, ячейка, короб или сотрудник"/></label><small id="operationHistorySummary"></small>';filter.insertAdjacentElement('afterend',wrap);
  const browser=document.createElement('div');browser.id='receivingHistoryBrowser';browser.className='receiving-history-browser';$('operationsBody')?.closest('.table-wrap')?.insertAdjacentElement('beforebegin',browser);
  $('operationSearch').addEventListener('input',renderOperations);
  $('operationsBody')?.addEventListener('click',event=>{const button=event.target.closest('[data-cancel-receiving-operation]');if(button)cancelReceivingFromHistory(button.dataset.cancelReceivingOperation).catch(error=>toast(error.message,true));});
  browser.addEventListener('click',async event=>{
    const receiptOpen=event.target.closest('[data-history-receipt-open]');
    const groupOpen=event.target.closest('[data-history-group-open]');
    const groupExcel=event.target.closest('[data-history-group-excel]');
    const groupBack=event.target.closest('[data-history-group-back]');
    const structuredHistoryOpen=event.target.closest('[data-history-structured-open]');
    const open=event.target.closest('[data-open-receiving-operation]');
    const back=event.target.closest('[data-receiving-history-back]');
    const cancel=event.target.closest('[data-cancel-receiving-operation]');
    const batchCancel=event.target.closest('[data-cancel-bulk-batch]');
    const details=event.target.closest('[data-bulk-details-excel]');
    const errors=event.target.closest('[data-bulk-errors-excel]');
    try{
      if(groupOpen){await openUnifiedReceiptGroup(groupOpen.dataset.historyGroupOpen||'');return;}
      if(groupExcel){downloadReceiptGroupExcel(groupExcel.dataset.historyGroupExcel||'');return;}
      if(groupBack){renderOperations();return;}
      if(receiptOpen){go('receivinghome');await ensureContext();await window.FCReceiptHistory?.open(receiptOpen.dataset.historyReceiptOpen);return;}
      if(structuredHistoryOpen){go('bulkimport');await ensureContext();structuredReceiptDetail=await api(`/receiving/structured-receipts/${encodeURIComponent(structuredHistoryOpen.dataset.historyStructuredOpen)}`);renderStructuredReceiptDetail(structuredReceiptDetail);$('structuredReceiptCurrent')?.scrollIntoView({behavior:'smooth',block:'start'});return;}
      if(open){receivingHistoryOpenOperationId=open.dataset.openReceivingOperation;renderOperations();return;}
      if(back){receivingHistoryOpenOperationId='';renderOperations();return;}
      if(cancel){await cancelReceivingFromHistory(cancel.dataset.cancelReceivingOperation);return;}
      if(batchCancel){
        await cancelBulkReceivingBatch(batchCancel.dataset.cancelBulkBatch);
        unifiedReceiptHistoryRows=[];
        await loadUnifiedReceiptHistoryForOperations(true);
        renderOperations();
        return;
      }
      if(details){
        // FC_K9R47_BULK_RECEIVING_DETAILS_XLSX_UI: keep the button visibly busy while
        // the server assembles large historical receipts instead of looking like a dead click.
        const id=details.dataset.bulkDetailsExcel,q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId});
        const oldText=details.textContent;details.disabled=true;details.textContent='Формируем Excel…';
        try{
          const blob=await apiBlob(`/receiving/bulk/${encodeURIComponent(id)}/details.xlsx?${q.toString()}`,{timeoutMs:180000});
          saveBlob(blob,`Приёмка_${id}_состав.xlsx`);toast('Состав приёмки выгружен в Excel');
        }finally{details.disabled=false;details.textContent=oldText||'Состав в Excel';}
        return;
      }
      if(errors){
        const id=errors.dataset.bulkErrorsExcel,q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId});
        const blob=await apiBlob(`/receiving/bulk/${encodeURIComponent(id)}/errors.xlsx?${q.toString()}`,{timeoutMs:120000});
        saveBlob(blob,`Приёмка_${id}_ошибки_WB.xlsx`);toast('Excel ошибок WB скачан');return;
      }
    }catch(error){toast(error.message,true);}
  });
  if(!document.getElementById('fcWideTableScrollStyles')){const style=document.createElement('style');style.id='fcWideTableScrollStyles';style.textContent='.table-wrap{max-width:100%;overflow-x:auto!important;overflow-y:visible;scrollbar-gutter:stable both-edges;-webkit-overflow-scrolling:touch}.table-wrap>table{min-width:max-content}.receiving-history-browser{max-width:100%;overflow-x:auto}.receiving-history-card{min-width:760px}.receiving-history-list{max-width:100%;overflow-x:auto;padding-bottom:8px}';document.head.appendChild(style);}
if(!document.getElementById('operationHistoryStyles')){const style=document.createElement('style');style.id='operationHistoryStyles';style.textContent='.operation-history-tools{display:grid;grid-template-columns:minmax(260px,1fr) auto;align-items:end;gap:12px;margin:10px 0}.operation-history-tools small{color:#667085}.operation-badge{display:inline-block;padding:5px 8px;border-radius:999px;background:#eef2ff;font-weight:700}.operation-receiving,.operation-receiving_import{background:#dcfae6;color:#067647}.operation-move,.operation-move_container{background:#e0f2fe;color:#026aa2}.operation-shipping,.operation-adjustment{background:#fee4e2;color:#b42318}.balance-change{min-width:150px;white-space:normal;color:#344054;font-weight:650}.operation-actions{margin-top:8px}.operation-cancelled{opacity:.72}.operation-cancelled td{background:#f8fafc!important}.receiving-history-list-head,.receiving-history-detail-head{display:flex;align-items:center;justify-content:space-between;gap:16px;margin:16px 0}.receiving-history-list-head h3,.receiving-history-list-head p,.receiving-history-detail-head h2,.receiving-history-detail-head p{margin:0}.receiving-history-list-head p,.receiving-history-detail-head p{color:#667085}.receiving-history-list{display:grid;gap:12px}.receiving-history-card{display:grid;grid-template-columns:120px minmax(240px,1fr) auto auto;align-items:center;gap:16px;padding:16px;border:1px solid #d7deea;border-radius:15px;background:#fff}.receiving-history-date{display:grid;gap:3px}.receiving-history-date span,.receiving-history-main small{color:#667085}.receiving-history-main h3{margin:6px 0 3px}.receiving-history-stats{display:flex;gap:12px;flex-wrap:wrap}.receiving-history-stats span,.receiving-history-detail-stats span,.receiving-history-addresses span{padding:8px 10px;border-radius:10px;background:#f5f7fa}.receiving-history-detail-head{align-items:flex-start}.receiving-history-detail-stats,.receiving-history-addresses{display:flex;gap:10px;flex-wrap:wrap;margin:14px 0}.receiving-history-addresses{align-items:center}.receiving-history-addresses h3{width:100%;margin:0}.receiving-history-addresses span{display:grid;gap:2px}.receipt-cancelled{opacity:.72;background:#f8fafc}@media(max-width:900px){.receiving-history-card{grid-template-columns:1fr}.receiving-history-detail-head{display:grid}}@media(max-width:700px){.operation-history-tools{grid-template-columns:1fr}}';document.head.appendChild(style);}
}
installOperationHistoryControls();

function normalizeLocationScan(raw){
  let q=String(raw||'').trim();
  if(!q)return '';
  // QR scanners sometimes return a URL or a prefixed payload instead of the visible cell code.
  try{
    const u=new URL(q);
    q=u.searchParams.get('location')||u.searchParams.get('code')||u.searchParams.get('barcode')||u.pathname.split('/').filter(Boolean).pop()||q;
  }catch{}
  q=q.replace(/^(?:location|loc|ячейка|адрес)\s*[:=#-]\s*/i,'').trim();
  return q.replace(/[‐‑‒–—−]/g,'-').replace(/\s+/g,'').toLowerCase();
}
function findLocation(raw){
  const q=normalizeLocationScan(raw);if(!q)return null;
  const list=assignableLocations().filter(x=>!x.warehouse_id||String(x.warehouse_id)===String(state.warehouseId));
  const exact=list.filter(x=>[x.barcode,x.code].filter(Boolean).some(v=>normalizeLocationScan(v)===q));
  if(exact.length===1)return exact[0];
  const publicMatches=list.filter(x=>[x.display_code,FC25.publicCode(x)].some(v=>normalizeLocationScan(v)===q));
  if(publicMatches.length>1)throw new Error('\u041d\u0435\u0441\u043a\u043e\u043b\u044c\u043a\u043e \u043c\u0435\u0441\u0442 \u0441 \u044d\u0442\u0438\u043c \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u0435\u043c. \u0421\u043a\u0430\u043d\u0438\u0440\u0443\u0439\u0442\u0435 QR \u043a\u043e\u043d\u043a\u0440\u0435\u0442\u043d\u043e\u0433\u043e \u043c\u0435\u0441\u0442\u0430.');
  return publicMatches[0]||null;
}
async function resolveFreshLocation(raw){
  const scopeAtStart=boxPrintScope();let found=findLocation(raw);if(found)return found;
  // A newly created/renamed address may exist in PostgreSQL while this workstation still has
  // an older in-memory location list. Refresh only the locations directory on a miss.
  if(state.warehouseId){
    try{
      const latest=await api(`/r145f25/locations?client_id=${encodeURIComponent(state.clientId)}&warehouse_id=${encodeURIComponent(state.warehouseId)}`,{timeoutMs:8000});
      if(scopeAtStart!==boxPrintScope())return null;if(Array.isArray(latest)){state.locations=latest;renderLocations();found=findLocation(raw);}
    }catch(error){console.warn('Location refresh after scan miss:',error);}
  }
  return found;
}
function findContainer(raw){const q=(raw||'').trim().toLowerCase();return activeContainers().find(x=>String(x.barcode||'').toLowerCase()===q||String(x.name||'').toLowerCase()===q);}
function findSku(raw){const q=(raw||'').trim().toLowerCase();return state.skus.find(x=>[x.barcode,x.sku_code,x.article].filter(Boolean).some(v=>v.toLowerCase()===q));}
function hint(inputId,hintId,finder,type,allowBlank=false){
  const input=$(inputId),el=$(hintId),raw=input.value.trim();if(!raw&&allowBlank){el.textContent='Без короба';el.className='good';return null;}if(!raw){el.textContent='';el.className='';return null;}
  const found=finder(raw);if(found){
    if(type==='location'){const others=locationOtherOwners(found.id);if(inputId==='moveDestination'){const m=movementLocationInfo(found);el.textContent=others.length?`Занята клиентом: ${others.map(clientLabel).join(', ')}`:`Новый адрес: ${m.address} · ${m.type}${m.pallet!=='—'?` · палета/палетоместо ${m.pallet}`:''}`;el.className=others.length?'bad':'good';}else{el.textContent=others.length?`Занята клиентом: ${others.map(clientLabel).join(', ')}`:`Найдена: ${FC25.publicCode(found)}${locationOwnerNames(found.id).length?` · клиент ${locationOwnerNames(found.id).join(', ')}`:' · свободна'}`;el.className=others.length?'bad':'good';}}
    else if(type==='container'&&inputId==='moveContainerBarcode'){const loc=mapBy(state.locations,found.location_id),m=movementLocationInfo(loc);el.textContent=`Короб: ${containerPublicCode(found)} · адрес ${m.address}${m.pallet!=='—'?` · палета/палетоместо ${m.pallet}`:''}`;el.className='good';}
    else{el.textContent=type==='sku'?skuLabel(found):`Найден: ${found.name||found.barcode} · ${locationLabel(found.location_id)}`;el.className='good';}
  }else{if(type==='container'&&inputId==='moveContainerBarcode'){el.textContent='Короб не найден';el.className='bad';}else{el.textContent=type==='container'?'Короб не найден. Сначала создайте или примите короб.':'Не найдено';el.className='bad';}}return found;
}
const hints=[['receiveLocation','receiveLocationHint',findLocation,'location'],['receiveContainer','receiveContainerHint',findContainer,'container',true],['receiveSku','receiveSkuHint',findSku,'sku'],['moveContainerBarcode','moveContainerHint',findContainer,'container'],['moveDestination','moveDestinationHint',findLocation,'location'],['shipLocation','shipLocationHint',findLocation,'location'],['shipContainer','shipContainerHint',findContainer,'container',true],['shipSku','shipSkuHint',findSku,'sku']];
function validateAllHints(){hints.forEach(a=>hint(...a));}hints.forEach(a=>$(a[0]).addEventListener('input',()=>hint(...a)));

// FC_K9R56_CONTAINER_MOVE_RESERVATION_PREVIEW_UI
let moveContainerPreviewSeq=0,moveContainerPreviewTimer=0,currentMoveContainerPreview=null,moveContainerPreviewCacheKey='',moveContainerPreviewCacheAt=0;
function ensureMoveContainerPreviewCard(){
  let card=$('moveContainerPreview');if(card)return card;
  const step=$('moveContainerHint')?.closest('.scan-step');if(!step)return null;
  card=document.createElement('div');card.id='moveContainerPreview';card.className='result-card';card.innerHTML='<b>Короб не выбран</b><span>Сканируйте ШК короба — покажем адрес, палету и состояние резерва.</span>';step.insertAdjacentElement('afterend',card);return card;
}
function renderMoveContainerPreview(data){
  currentMoveContainerPreview=data||null;const card=ensureMoveContainerPreviewCard();if(!card)return;
  if(!data){card.className='result-card';card.innerHTML='<b>Короб не выбран</b><span>Сканируйте ШК короба — покажем адрес, палету и состояние резерва.</span>';return;}
  const c=data.container||{},source=data.source_location||{},loc=mapBy(state.locations,source.id)||source,m=movementLocationInfo(loc),physical=Number(data.physical_quantity||0),follow=Number(data.new_reservations_follow_quantity||0),warn=Number(data.active_list_other_bucket_quantity||0),block=Number(data.blocking_quantity||0);
  const rows=[`<span><b>Короб:</b> ${esc(c.display_code||containerPublicCode(c))}</span>`,`<span><b>Ячейка / адрес:</b> ${esc(m.address)}</span>`,`<span><b>Палета / палетоместо:</b> ${esc(m.pallet)}</span>`,`<span><b>В коробе:</b> ${esc(fmtQty(physical))} шт. · ${esc(String(data.sku_count||0))} SKU</span>`];
  if(follow>0)rows.push(`<span class="good"><b>NEW резерв поедет с коробом:</b> ${esc(fmtQty(follow))} шт. После перемещения будущий сборочный лист покажет новый адрес.</span>`);
  if(warn>0)rows.push(`<span class="warn"><b>Предупреждение:</b> ${esc(data.warning_message||`На этом адресе есть ${fmtQty(warn)} шт. резерва активного листа, но не в этом коробе. Перемещение разрешено.`)}</span>`);
  if(block>0)rows.push(`<span class="bad"><b>Перемещение запрещено:</b> ${esc(data.block_message||'В этом коробе есть резерв активного сборочного листа. Сначала упакуйте товар.')}</span>`);
  card.className='result-card'+(block>0?' bad':warn>0?' warn':'');card.innerHTML=`<b>${block>0?'Сначала упаковка':'Короб готов к перемещению'}</b>${rows.join('')}`;
}
// FC_K9R57_MOVE_PREVIEW_SINGLEFLIGHT
async function fetchMoveContainerPreview(container,destination=null,{force=false}={}){
  if(!container||!state.clientId||!state.warehouseId)return null;
  const key=`${state.clientId}|${state.warehouseId}|${container.id}|${destination?.id||''}`,now=Date.now();
  if(!force&&currentMoveContainerPreview&&moveContainerPreviewCacheKey===key&&now-moveContainerPreviewCacheAt<3000)return currentMoveContainerPreview;
  const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId,container_id:container.id});if(destination?.id)q.set('destination_location_id',destination.id);
  const data=await api(`/operations/move-container/preview?${q.toString()}`,{timeoutMs:10000});
  moveContainerPreviewCacheKey=key;moveContainerPreviewCacheAt=Date.now();currentMoveContainerPreview=data||null;
  return data;
}
function applyLocalContainerMove(container,destination){
  if(!container||!destination)return;container.location_id=destination.id;
  for(const row of (state.inventory||[]))if(String(row.container_id||'')===String(container.id))row.location_id=destination.id;
  renderContainers();validateAllHints();
}
function refreshContextAfterContainerMove(){
  Promise.resolve().then(()=>loadContextData()).catch(error=>console.warn('FC_K9R57_MOVE_REFRESH_DEFERRED',error));
}
async function refreshMoveContainerPreview(){
  const seq=++moveContainerPreviewSeq,c=findContainer($('moveContainerBarcode')?.value||'');if(!c){renderMoveContainerPreview(null);return null;}
  const destination=findLocation($('moveDestination')?.value||'');const card=ensureMoveContainerPreviewCard();if(card){card.className='result-card';card.innerHTML=`<b>Проверяем короб ${esc(containerPublicCode(c))}…</b><span>Сверяем адрес и активные резервы.</span>`;}
  try{const data=await fetchMoveContainerPreview(c,destination);if(seq!==moveContainerPreviewSeq)return null;renderMoveContainerPreview(data);return data;}catch(error){if(seq!==moveContainerPreviewSeq)return null;if(card){card.className='result-card bad';card.innerHTML=`<b>Не удалось проверить перемещение</b><span>${esc(error.message)}</span>`;}currentMoveContainerPreview=null;return null;}
}
function scheduleMoveContainerPreview(){clearTimeout(moveContainerPreviewTimer);moveContainerPreviewTimer=setTimeout(()=>refreshMoveContainerPreview(),180);}
$('moveContainerBarcode')?.addEventListener('input',scheduleMoveContainerPreview);$('moveDestination')?.addEventListener('input',scheduleMoveContainerPreview);ensureMoveContainerPreviewCard();
async function ensureContext(){if(!state.clientId)throw new Error('Сначала создайте и выберите клиента');if(!state.warehouseId)throw new Error('Сначала создайте и выберите склад');}
async function ensureContainer(raw,location){
  const code=(raw||'').trim();if(!code)return null;if(!location)throw new Error('Сначала укажите ячейку');assertLocationAvailableForClient(location);let c=findContainer(code);if(c)return c;
  c=await api('/inventory/containers/ensure',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,location_id:location.id,barcode:code,name:code,container_type:'BOX'})});state.containers.push(c);return c;
}

$('clientSelect').addEventListener('change',async e=>{state.clientId=e.target.value||null;fc27ServerDraft=null;fc27ServerDraftContext='';state.bulkReceivingHistory=[];state.bulkReceivingHistoryLoaded=false;state.bulkReceivingHistoryLoading=false;state.bulkReceivingHistoryContextKey='';receivingHistoryOpenOperationId='';const __boxPanel=$('containerDetailPanel');if(__boxPanel){__boxPanel.classList.add('hidden');__boxPanel.innerHTML='';delete __boxPanel.dataset.containerId;}state.inventoryCatalogLoaded=false;state.inventoryCatalog=[];state.fbsStockStatus={};state.inventoryWbControl={};state.wbLiveStock={};state.wbLiveStockSummary=null;state.wbLiveStockCheckedAt=null;state.wbLiveStockError='';selectedLocationIds.clear();selectedContainerIds.clear();resetLocationBrowser(false);fcR20Remember('fc_client',state.clientId||'');currentPalletReceiptId=localStorage.getItem(palletReceiptStorageKey())||'';palletReceiptItems=[];clearManualReceivingStorageContext({keepHierarchy:false});scheduleWorkstationProfileSync();renderPendingWbReceiving();await loadContextData();scheduleWorkstationProfileSync();if($('page-palletreceive')?.classList.contains('active'))await loadPalletReceipts();});
async function switchInternalWarehouse(nextWarehouseId){
  const select=$('warehouseSelect'),previous=state.warehouseId;
  if(!nextWarehouseId||nextWarehouseId===previous){if(select)select.value=previous||'';return;}
  if(select)select.disabled=true;
  state.warehouseId=nextWarehouseId;fcR20Remember('fc_warehouse',nextWarehouseId);
  state.bulkReceivingHistory=[];state.bulkReceivingHistoryLoaded=false;state.bulkReceivingHistoryLoading=false;state.bulkReceivingHistoryContextKey='';receivingHistoryOpenOperationId='';const __boxPanel=$('containerDetailPanel');if(__boxPanel){__boxPanel.classList.add('hidden');__boxPanel.innerHTML='';delete __boxPanel.dataset.containerId;}
  currentPalletReceiptId=localStorage.getItem(palletReceiptStorageKey())||'';palletReceiptItems=[];
  selectedLocationIds.clear();
  selectedContainerIds.clear();
  resetLocationBrowser(false);
  restoreInventoryAvailability();
  restoreScopedWbState();
  state.locations=[];state.locationOccupancy={};state.skus=[];state.containers=[];state.inventory=[];state.operations=[];
  state.inventoryCatalogLoaded=false;state.inventoryCatalog=[];state.fbsStockStatus={};state.inventoryWbControl={};state.wbLiveStock={};state.wbLiveStockSummary=null;state.wbLiveStockCheckedAt=null;state.wbLiveStockError='';state.wbWarehouses=[];state.wbOrders=[];state.wbSupplies=[];state.wbOrderCounts={};state.wbTotal=0;state.wbPage=1;
  renderAll();renderWbWarehouses();renderWbOrders();renderWbSupplies();
  resetWbPacking(false);renderPendingWbReceiving();
  try{
    await loadContextData();
    toast(`Склад переключён: ${state.warehouses.find(x=>x.id===state.warehouseId)?.name||'выбранный склад'}`);
  }catch(err){
    // Never fall back to another physical warehouse after a loading error: that can
    // make an operator work with the wrong stock. Keep the chosen context and retry.
    toast(`Склад выбран, но часть данных пока не загрузилась: ${err.message}`,true);
  }
  if($('page-marketplaces')?.classList.contains('active')){
    document.querySelectorAll('#wbStatusTabs [data-wb-filter]').forEach(x=>x.classList.toggle('active',x.dataset.wbFilter===state.wbOrderFilter));
    await loadWbWarehouses(false);restoreWbOrdersCacheImmediately();
    await Promise.allSettled([loadWbOrders(false),loadWbSupplies(false)]);
  }
  if($('page-palletreceive')?.classList.contains('active'))await loadPalletReceipts().catch(e=>toast(e.message,true));
  if(select){select.value=state.warehouseId||'';select.disabled=false;}
  scheduleWorkstationProfileSync();
}
$('warehouseSelect').addEventListener('change',e=>switchInternalWarehouse(e.target.value||null));
$('refreshBtn').addEventListener('click',()=>loadContexts().then(()=>toast('Данные обновлены')).catch(e=>toast(e.message,true)));
$('inventorySearch').addEventListener('input',renderInventory);$('refreshInventoryCatalogBtn')?.addEventListener('click',()=>loadInventoryCatalog({force:true}).catch(e=>toast(e.message,true)));$('inventoryBody')?.addEventListener('change',e=>{const box=e.target.closest('[data-inventory-select]');if(!box)return;const code=String(box.dataset.inventorySelect||'');if(box.checked)selectedInventoryBarcodes.add(code);else selectedInventoryBarcodes.delete(code);updateInventoryBulkToolbar();});$('inventoryBody')?.addEventListener('click',e=>{if(e.target.closest('[data-inventory-select]')){e.stopPropagation();return;}const publish=e.target.closest('[data-inventory-publish]'),local=e.target.closest('[data-inventory-local-location]'),b=e.target.closest('[data-inventory-open]');if(publish){e.stopPropagation();publishInventoryBarcode(publish.dataset.inventoryPublish,publish.dataset.inventoryBarcode||'',publish.dataset.inventoryQuantity||0).catch(err=>toast(err.message,true));return;}if(local){e.stopPropagation();if(local.dataset.inventoryLocalLocation){go('locations');openLocationContents(local.dataset.inventoryLocalLocation);}return;}if(b){e.stopPropagation();openInventoryProduct(b.dataset.inventoryOpen,b.dataset.inventoryBarcode||'');return;}const row=e.target.closest('[data-inventory-row-open]');if(row)openInventoryProduct(row.dataset.inventoryRowOpen,row.dataset.inventoryBarcode||'');});document.addEventListener('click',e=>{if(e.target.closest('#inventoryBulkToolbar,#r65FbsPickActions'))handleInventoryBulkToolbarClick(e);const all=e.target.closest('[data-inventory-select-visible]');if(all){const visible=inventoryVisibleBarcodes();if(all.checked)visible.forEach(code=>selectedInventoryBarcodes.add(code));else visible.forEach(code=>selectedInventoryBarcodes.delete(code));renderInventory();}});document.querySelectorAll('[data-close-inventory-modal]').forEach(x=>x.addEventListener('click',closeInventoryModal));$('locationSearch').addEventListener('input',renderLocations);$('containerSearch').addEventListener('input',renderContainers);$('operationFilter').addEventListener('change',renderOperations);

const WB_RECEIVING_QUEUE_LEGACY_KEY='fc_pending_wb_receiving_v1';
const WB_RECEIVING_QUEUE_MIGRATION_KEY='fc_pending_wb_receiving_v2_migrated';
let selectedPendingWbReceiving=new Set();
function wbReceivingQueueKey(){return scopedStorageKey(`fc_pending_wb_receiving_v2:${state.clientId||'none'}`,state.warehouseId);}
function loadPendingWbReceiving(){try{const key=wbReceivingQueueKey(),scoped=localStorage.getItem(key);if(scoped!==null)return JSON.parse(scoped)||[];const legacy=localStorage.getItem(WB_RECEIVING_QUEUE_LEGACY_KEY);if(legacy!==null&&!localStorage.getItem(WB_RECEIVING_QUEUE_MIGRATION_KEY)){localStorage.setItem(key,legacy);localStorage.setItem(WB_RECEIVING_QUEUE_MIGRATION_KEY,'1');localStorage.removeItem(WB_RECEIVING_QUEUE_LEGACY_KEY);return JSON.parse(legacy)||[];}return []}catch(_){return []}}
function savePendingWbReceiving(rows){localStorage.setItem(wbReceivingQueueKey(),JSON.stringify(rows||[]));const max=(rows||[]).length;selectedPendingWbReceiving=new Set([...selectedPendingWbReceiving].filter(i=>Number(i)>=0&&Number(i)<max));renderPendingWbReceiving();}
function addPendingWbReceiving(lines){const q=loadPendingWbReceiving();for(const x of (lines||[])){if(!x||!x.barcode)continue;const row={barcode:String(x.barcode),quantity:Number(x.quantity||0),mode:x.mode||'ADD',idempotency_key:x.idempotency_key?String(x.idempotency_key):null};if(row.idempotency_key){const i=q.findIndex(v=>String(v?.idempotency_key||'')===row.idempotency_key);if(i>=0)q[i]=row;else q.push(row);}else q.push(row);}savePendingWbReceiving(q);}
function localWarehouseQtyForBarcode(barcode){const code=String(barcode||'');const catalog=(state.inventoryCatalog||[]).find(x=>String(x.barcode||'')===code||(x.barcodes||[]).map(String).includes(code));if(catalog)return Number(catalog.quantity||0);let total=0;for(const bal of (state.inventory||[])){if(!(Number(bal.quantity||0)>0))continue;const sku=mapBy(state.skus,bal.sku_id);if(String(sku?.barcode||'')===code)total+=Number(bal.quantity||0);}return total;}
function pendingWbRowsHtml(q){if(!q.length)return '<div class="empty-state">Неотправленных позиций нет.</div>';return `<div class="table-wrap"><table class="pending-wb-table"><thead><tr><th><input type="checkbox" data-pending-wb-all></th><th>ШК</th><th>На складе</th><th>WB FBS</th><th>В очереди</th></tr></thead><tbody>${q.map((row,index)=>{const local=localWarehouseQtyForBarcode(row.barcode),fbs=fbsPublishedQuantity(row.barcode),selected=selectedPendingWbReceiving.has(index);return `<tr><td><input type="checkbox" data-pending-wb-index="${index}" ${selected?'checked':''}></td><td><b>${esc(row.barcode)}</b></td><td><b>${fmtQty(local)} шт.</b><small class="subline">FulfillCore</small></td><td><b>${fmtQty(fbs)} шт.</b><small class="subline">опубликовано FBS</small></td><td>${row.mode==='SET'?`Установить ${fmtQty(row.quantity)} шт.`:`+${fmtQty(row.quantity)} шт.`}</td></tr>`;}).join('')}</tbody></table></div><div class="button-row pending-wb-actions"><button type="button" class="primary" data-pending-wb-send-selected>Отправить выбранные на WB FBS</button><button type="button" class="secondary" data-pending-wb-select-all>Выбрать все</button><button type="button" class="danger" data-pending-wb-remove-selected>Убрать выбранные из очереди</button></div>`;}
function reconcilePendingWbAfterCancellation(result){
  const affected=result?.affected_stocks||[];if(!affected.length)return;
  const codes=new Set(affected.map(x=>String(x.barcode||'')).filter(Boolean)),before=loadPendingWbReceiving(),hadPending=new Set(before.filter(x=>codes.has(String(x.barcode||''))).map(x=>String(x.barcode||'')));
  const next=before.filter(x=>!codes.has(String(x.barcode||''))),failed=new Set((result.wb_results||[]).filter(x=>!x.ok).map(x=>String(x.barcode||'')));
  for(const row of affected){const barcode=String(row.barcode||'');if(!barcode)continue;const marker=state.fbsStockStatus?.[barcode],needsQueue=result.wb_requested?failed.has(barcode):(hadPending.has(barcode)||['PUBLISHED','ERROR'].includes(String(marker?.status||'')));if(needsQueue)next.push({barcode,quantity:Number(row.amount||0),mode:'SET'});}
  savePendingWbReceiving(next);
}
function renderPendingWbReceiving(){
  const q=loadPendingWbReceiving(),n=q.length;
  for(const id of ['pendingWbCount','pendingBulkWbCount','pendingPalletWbCount','receivingWbQueueCount']){const e=$(id);if(e)e.textContent=n?`(${n})`:'';}
  const send=$('sendPendingReceivingWbBtn');if(send)send.disabled=!n;
  const note=$('receivingWbQueueNote');if(note)note.textContent=n?`Это очередь публикации в WB, а не место хранения. На локальных остатках товар уже должен быть в выбранном коробе. Ожидают WB: ${n} изменений.`:'Неотправленных изменений нет.';
  const panel=$('receivingWbQueuePanel');
  if(panel){
    let detail=panel.querySelector('[data-pending-wb-detail]');
    if(!detail){detail=document.createElement('div');detail.dataset.pendingWbDetail='1';detail.className='pending-wb-detail';panel.appendChild(detail);}
    detail.innerHTML=pendingWbRowsHtml(q);
  }
  document.querySelectorAll('[data-pending-wb-all]').forEach(el=>{el.checked=Boolean(n)&&selectedPendingWbReceiving.size===n;el.indeterminate=selectedPendingWbReceiving.size>0&&selectedPendingWbReceiving.size<n;});
}
async function syncSelectedPendingReceivingToWb(){await ensureContext();const q=loadPendingWbReceiving(),indices=[...selectedPendingWbReceiving].map(Number).filter(i=>q[i]).sort((a,b)=>a-b);if(!indices.length)throw new Error('Отметьте товары для отправки в WB');const lines=indices.map(i=>q[i]);if(!confirm(`Отправить выбранные товары на WB FBS: ${lines.length} поз.?`))return;const r=await api('/receiving/sync-wb',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null,lines})});const failedPositions=new Set();(r.results||[]).forEach((x,j)=>{if(!x.ok)failedPositions.add(j);});const removeOriginal=new Set(indices.filter((_,j)=>!failedPositions.has(j)));const next=q.filter((_,i)=>!removeOriginal.has(i));selectedPendingWbReceiving=new Set();savePendingWbReceiving(next);state.inventoryCatalogLoaded=false;try{await loadInventoryCatalog();}catch(_){}const failed=(r.results||[]).filter(x=>!x.ok);toast(failed.length?`WB обновлено: ${r.synced}, ошибок: ${r.failed}`:`На WB FBS отправлено: ${r.synced} SKU`,failed.length>0);if(failed.length)throw new Error(failed.slice(0,3).map(x=>`${x.barcode}: ${x.error||'ошибка WB'}`).join('\n'));}
function removeSelectedPendingWb(){const q=loadPendingWbReceiving(),indices=new Set([...selectedPendingWbReceiving].map(Number));if(!indices.size)throw new Error('Отметьте позиции');if(!confirm(`Убрать из очереди ${indices.size} поз.?\n\nОстатки склада не изменятся, WB не изменится.`))return;selectedPendingWbReceiving=new Set();savePendingWbReceiving(q.filter((_,i)=>!indices.has(i)));}
function pendingWbClickHandler(e){const check=e.target.closest('[data-pending-wb-index]'),all=e.target.closest('[data-pending-wb-all]');if(check){const i=Number(check.dataset.pendingWbIndex);if(check.checked)selectedPendingWbReceiving.add(i);else selectedPendingWbReceiving.delete(i);renderPendingWbReceiving();return;}if(all){const q=loadPendingWbReceiving();selectedPendingWbReceiving=all.checked?new Set(q.map((_,i)=>i)):new Set();renderPendingWbReceiving();return;}if(e.target.closest('[data-pending-wb-select-all]')){const q=loadPendingWbReceiving();selectedPendingWbReceiving=new Set(q.map((_,i)=>i));renderPendingWbReceiving();return;}if(e.target.closest('[data-pending-wb-send-selected]')){syncSelectedPendingReceivingToWb().catch(err=>toast(err.message,true));return;}if(e.target.closest('[data-pending-wb-remove-selected]')){try{removeSelectedPendingWb();}catch(err){toast(err.message,true);}return;}}
function manualReceivingDraftKey(){const worker=fc30WorkerKey();return scopedStorageKey(`fc_manual_receiving_draft_v2:${state.clientId||'none'}:${worker}`,state.warehouseId);}
function newManualReceivingDraft(){return {id:uid(),rows:[]};}
// 0.9.4.11b: an idempotency key belongs to one exact visible list, not to the
// browser draft forever. The signature changes when barcode/qty/location/box changes,
// but remains stable across a lost HTTP response so a retry cannot double stock.
function manualReceivingCommitSignature(rows){
  const canonical=(rows||[]).map(row=>[String(row.barcode||''),String(Number(row.quantity||0)),String(row.location_code||''),String(row.container_code||'')].join('\u001f')).sort().join('\u001e');
  let hash=2166136261;for(let i=0;i<canonical.length;i++){hash^=canonical.charCodeAt(i);hash=Math.imul(hash,16777619);}return (hash>>>0).toString(16).padStart(8,'0');
}
function manualReceivingCommitKey(draft,rows,lane='known'){return `manual-${lane}-${String(draft?.id||'draft')}-${manualReceivingCommitSignature(rows)}`;}
const MANUAL_RECEIVING_SCAN_DELAY_MS=260;
let manualReceivingScanTimer=0,manualReceivingScanBusy=false;
const manualReceivingAdaptive={last:0,gap:0};
const manualReceivingScanQueue=[];
let manualReceivingStorageContext=null;
function manualReceivingIsCellMode(){return String($('manualReceiveStorageMode')?.value||'CELL').toUpperCase()==='CELL';}
function applyManualReceivingStorageMode(){
  const cellMode=manualReceivingIsCellMode(),locationStep=$('receiveLocation')?.closest('.scan-step'),boxParts=document.querySelectorAll('[data-manual-box-only]');
  boxParts.forEach(el=>el.classList.toggle('hidden',cellMode));
  if(locationStep)locationStep.classList.toggle('hidden',!cellMode);
  if(cellMode){
    if(manualReceivingStorageContext)clearManualReceivingStorageContext({keepHierarchy:true});
    if($('receiveContainer'))$('receiveContainer').value='';
    $('receiveLocation')?.focus();
  }else{
    if($('receiveLocation'))$('receiveLocation').value=manualReceivingStorageContext?.location_code||'';
    $('manualReceiveBox')?.focus();
  }
  renderManualReceivingStorageContext();renderManualReceivingDraft();
}
function manualReceivingStructureSummary(ctx=manualReceivingStorageContext){
  if(ctx?.location_display)return FC25.publicText(ctx.location_display)+(ctx.container_name?' \u2192 '+ctx.container_name:'');
  if(!ctx)return '';
  const parts=[];if(ctx.resolved_from_qr&&ctx.location_code)parts.push(String(ctx.location_code));else{if(ctx.section)parts.push(`Секция ${ctx.section}`);if(ctx.aisle)parts.push(`Проход ${ctx.aisle}`);if(ctx.pallet)parts.push(`Палета ${ctx.pallet}`);}if(ctx.row_on_pallet)parts.push(`Ряд ${ctx.row_on_pallet} · место ${ctx.row_position||'—'}/8`);if(ctx.container_name)parts.push(`Короб ${ctx.container_name}`);return parts.join(' → ')||`Короб ${containerContextPublicCode(ctx)}`;
}
function clearManualReceivingStorageContext({keepHierarchy=true}={}){
  manualReceivingStorageContext=null;
  if($('receiveContainer'))$('receiveContainer').value='';
  if($('receiveLocation'))$('receiveLocation').value='';
  const box=$('manualReceiveBox');if(box)box.value='';
  if(!keepHierarchy){for(const id of ['manualReceiveSection','manualReceiveRow','manualReceiveAisle','manualReceivePallet'])if($(id))$(id).value='';}
  renderManualReceivingStorageContext();
}
function renderManualReceivingStorageContext(){
  const el=$('manualReceiveActiveContext');if(!el)return;
  if(manualReceivingIsCellMode()){
    const code=String($('receiveLocation')?.value||'').trim();el.classList.toggle('active',Boolean(code));
    el.innerHTML=code?`<b>ТЕКУЩАЯ ЯЧЕЙКА: ${esc(code)}</b><span>Товар принимается напрямую в ячейку — короб не нужен.</span><small>Все следующие товары будут добавляться в эту ячейку, пока вы не отсканируете другую.</small>`:'<b>ХРАНЕНИЕ В ЯЧЕЙКЕ · БЕЗ КОРОБА</b><span>Отсканируйте QR ячейки или введите её точный код. Короб для этого режима не требуется.</span>';
    return;
  }
  if(!manualReceivingStorageContext){el.innerHTML='<b>Короб не выбран</b><span>Сканируйте QR/ШК короба или введите его название. Секция, ряд, проход и палета необязательны. Ряд 1 или 2 — сторона палеты, до 8 коробов.</span>';el.classList.remove('active');return;}
  el.classList.add('active');el.innerHTML=`<b>ТЕКУЩИЙ КОРОБ: ${esc(manualReceivingStorageContext.container_name||manualReceivingStorageContext.container_barcode)}</b><span>${esc(manualReceivingStructureSummary())}</span><small>Все следующие товары будут добавляться сюда, пока вы не смените короб или место.</small>`;
}
/* r73 box label printing: native agent image, scoped result, browser PDF fallback. */
const boxLabelPrintFlights=new Map(),boxLabelPrintKeys=new Map();
function boxLabelUuid(){return typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{const r=Math.floor(Math.random()*16);return(c==='x'?r:(r&3)|8).toString(16);});}
function boxPrintScope(){return JSON.stringify([state.clientId,state.warehouseId]);}
function boxLabelNotice(info,text,bad=false){
  if(info.scope!==boxPrintScope())return null;
  const host=state.activePage==='boxintake'?$('r68ReceiveTab'):$('manualReceiveActiveContext')?.parentElement;
  if(!host)return null;
  let el=$('r73BoxPrintStatus');if(!el){el=document.createElement('div');el.id='r73BoxPrintStatus';el.className='panel';host.appendChild(el);}
  el.dataset.key=info.key;el.setAttribute('role',bad?'alert':'status');
  el.innerHTML=`<b>${esc(info.name)} · печать QR</b><p>${esc(text)}</p><div class="button-row"><button class="secondary" type="button" data-box-pdf>Открыть ручную печать PDF</button><button class="secondary" type="button" data-box-close>Закрыть</button></div>`;
  el.querySelector('[data-box-pdf]').onclick=()=>printBoxLabelPdf(info).catch(e=>toast(e.message,true));
  el.querySelector('[data-box-close]').onclick=()=>el.remove();
  return el;
}
async function printBoxLabelPdf(info){
  if(info.scope!==boxPrintScope())throw Error('Клиент или склад изменён. Выберите короб заново');
  const blob=await apiBlob(`/receiving/manual/boxes/${encodeURIComponent(info.id)}/label.pdf?`+new URLSearchParams({...info.context,width_mm:58,height_mm:40}));
  if(info.scope!==boxPrintScope())return;
  printBlob(blob,`Короб_${info.name}.pdf`);boxLabelNotice(info,'Открыто окно печати PDF. Выберите принтер и нажмите «Печать».');
}
async function watchBoxLabel(info,job){
  for(let attempt=0;attempt<15;attempt++){
    await new Promise(resolve=>setTimeout(resolve,1000));if(info.scope!==boxPrintScope())return;
    const r=await api(`/receiving/manual/boxes/${encodeURIComponent(info.id)}/label/jobs/${encodeURIComponent(job.print_job_id)}?`+new URLSearchParams(info.context),{timeoutMs:5000});
    if(r.status==='PRINTED'){if($('r73BoxPrintStatus')?.dataset.key===info.key)boxLabelNotice(info,'Print Agent подтвердил передачу этикетки принтеру.');return;}
    if(r.status==='FAILED'){if($('r73BoxPrintStatus')?.dataset.key===info.key)boxLabelNotice(info,'Печать не выполнена: '+(r.error||'ошибка принтера')+'. Можно распечатать PDF.',true);return;}
  }
  if($('r73BoxPrintStatus')?.dataset.key===info.key)boxLabelNotice(info,'Подтверждение печати пока не получено. Проверьте, запущен ли Print Agent для '+job.print_station+'. Задание остаётся в очереди.',true);
}
async function printManualReceivingBoxLabel(ctx){
  if(!ctx?.container_id)throw new Error('Сначала выберите короб');
  const ps=wbPrintSettings(),info={id:ctx.container_id,name:containerContextPublicCode(ctx),scope:boxPrintScope(),context:{client_id:state.clientId,warehouse_id:state.warehouseId}};
  info.key=info.scope+':'+info.id;
  if(boxLabelPrintFlights.has(info.key))return boxLabelPrintFlights.get(info.key);
  if(ps.mode!=='agent')return printBoxLabelPdf(info);
  const requestId=boxLabelPrintKeys.get(info.key)||boxLabelUuid();boxLabelPrintKeys.set(info.key,requestId);
  const job=api(`/receiving/manual/boxes/${encodeURIComponent(info.id)}/label/queue`,{method:'POST',body:JSON.stringify({...info.context,request_id:requestId,print_station:ps.station,width_mm:58,height_mm:40})}).then(r=>{
    boxLabelPrintKeys.delete(info.key);boxLabelNotice(info,'Этикетка в очереди '+r.print_station+'. Ожидаем результат Print Agent.');
    watchBoxLabel(info,r).catch(e=>{if($('r73BoxPrintStatus')?.dataset.key===info.key)boxLabelNotice(info,'Не удалось проверить печать: '+e.message+'. Задание могло уже напечататься.',true);}).finally(()=>boxLabelPrintFlights.delete(info.key));return r;
  }).catch(e=>{boxLabelPrintFlights.delete(info.key);boxLabelNotice(info,'Не удалось подтвердить отправку: '+e.message+'. Повторная кнопка QR проверит это же задание.',true);throw e;});
  boxLabelPrintFlights.set(info.key,job);return job;
}
const fc25OpenBoxes=new Map();
async function activateManualReceivingBox(mode='SCAN',forceMove=false){
  await ensureContext();const scopeBefore=boxPrintScope(),value=String($('manualReceiveBox')?.value||'').trim();
  if(!value)throw new Error('\u0421\u043a\u0430\u043d\u0438\u0440\u0443\u0439\u0442\u0435 \u043a\u043e\u0440\u043e\u0431');
  const key=scopeBefore+'|'+mode+'|'+value;
  if(fc25OpenBoxes.has(key))return fc25OpenBoxes.get(key);
  const job=(async()=>{
    const body={client_id:state.clientId,warehouse_id:state.warehouseId,section:String($('manualReceiveSection')?.value||'').trim()||null,aisle:String($('manualReceiveAisle')?.value||'').trim()||null,pallet:String($('manualReceivePallet')?.value||'').trim()||null,row_on_pallet:$('manualReceiveRow')?.value?Number($('manualReceiveRow').value):null,box_value:value,box_mode:mode,force_move:false};
    const r=await api('/r145f25/receiving/box',{method:'POST',body:JSON.stringify(body),timeoutMs:20000});
    if(scopeBefore!==boxPrintScope())return r;
    if(r.location){const i=state.locations.findIndex(x=>String(x.id)===String(r.location.id));if(i<0)state.locations.push(r.location);else state.locations[i]=r.location;}
    if(r.container_id){const c={id:r.container_id,client_id:body.client_id,warehouse_id:body.warehouse_id,location_id:r.location_id,name:r.container_name,barcode:r.container_barcode,status:'ACTIVE'},i=state.containers.findIndex(x=>String(x.id)===String(c.id));if(i<0)state.containers.push(c);else state.containers[i]={...state.containers[i],...c};}
    manualReceivingStorageContext=r;if($('manualReceiveRow'))$('manualReceiveRow').value=r.row_on_pallet?String(r.row_on_pallet):'';
    if($('receiveLocation'))$('receiveLocation').value=r.location_code||'';
    if($('receiveContainer'))$('receiveContainer').value=r.container_barcode||r.container_name||'';
    renderManualReceivingStorageContext();renderManualReceivingDraft();$('manualReceiveBox').value='';$('receiveSku')?.focus();
    toast('\u041e\u0442\u043a\u0440\u044b\u0442 \u043a\u043e\u0440\u043e\u0431 '+r.container_name);FC25.speak('\u041a\u043e\u0440\u043e\u0431 \u043e\u0442\u043a\u0440\u044b\u0442');
    if(r.created&&$('manualReceiveAutoPrintBox')?.checked){
      if(wbPrintSettings().mode==='agent')printManualReceivingBoxLabel(r).catch(error=>{if(scopeBefore===boxPrintScope())toast('\u041a\u043e\u0440\u043e\u0431 \u0441\u043e\u0437\u0434\u0430\u043d. \u041f\u0435\u0447\u0430\u0442\u044c: '+error.message,true);});
      else toast('\u0414\u043b\u044f \u0444\u043e\u043d\u043e\u0432\u043e\u0439 \u043f\u0435\u0447\u0430\u0442\u0438 \u0432\u044b\u0431\u0435\u0440\u0438\u0442\u0435 Print Agent \u0432 \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0430\u0445. QR \u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d \u043f\u043e \u043a\u043d\u043e\u043f\u043a\u0435.');
    }
    return r;
  })();fc25OpenBoxes.set(key,job);try{return await job;}finally{fc25OpenBoxes.delete(key);}
}

let fc26DraftSaving=0,fc26CommitBusy=false;
let fc27ServerDraft=null,fc27ServerDraftContext='',fc27ServerDraftFlight=null;
function fc27DraftContext(){return `${state.clientId||''}|${state.warehouseId||''}|${fc30WorkerKey()}`;}
function fc27ServerToLocal(snap){return {id:String(snap?.id||uid()),rows:(snap?.rows||[]).filter(r=>!r.posted_at&&Number(r.quantity||0)>0).map(r=>({...r,id:String(r.id),client_id:String(snap.client_id||state.clientId),warehouse_id:String(snap.warehouse_id||state.warehouseId)})),server_saved:true,server_updated_at:snap?.updated_at||null};}
async function fc27CacheServerDraft(snap,key=manualReceivingDraftKey()){
  fc27ServerDraft=snap;fc27ServerDraftContext=fc27DraftContext();
  const local=fc27ServerToLocal(snap);try{await FC26DraftStore.put(key,local);}catch(e){console.warn('FC27 local cache write failed',e);}return local;
}
async function fc27EnsureServerDraft(){
  if(!state.clientId||!state.warehouseId)throw Error('Выберите клиента и склад');
  const worker=fc30WorkerName();if(!worker)throw Error('Укажите имя приёмщика — по нему восстанавливается незавершённая приёмка');
  const context=fc27DraftContext();
  if(fc27ServerDraft&&fc27ServerDraftContext===context&&['DRAFT','POSTING'].includes(String(fc27ServerDraft.status||'DRAFT')))return fc27ServerDraft;
  if(fc27ServerDraftFlight&&fc27ServerDraftContext===context)return fc27ServerDraftFlight;
  fc27ServerDraftContext=context;
  const job=(async()=>{
    let local=null;try{local=await FC26DraftStore.ready(manualReceivingDraftKey());}catch(_){local=null;}
    if(!local?.rows?.length){try{const legacy=await FC26DraftStore.ready(fc30LegacyDraftKey());if(legacy?.rows?.length)local=legacy;}catch(_){} }
    const snap=await api('/r145f30/manual-drafts/open-worker',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,worker,id:local?.id||null,reference:$('receiveReference')?.value?.trim()||null}),timeoutMs:20000});
    let current=snap;
    if(!(current.rows||[]).length&&(local?.rows||[]).length){
      for(const row of local.rows){if(!row?.barcode||!Number(row.quantity||0)||!row.location_code)continue;
        const payload={client_id:state.clientId,warehouse_id:state.warehouseId,worker,request_id:`migrate30-${local.id}-${row.id}`,barcode:row.barcode,quantity:Number(row.quantity),location_code:row.location_code,container_code:row.container_code||null,article:row.article||null,size:row.size||null,name:row.name||null,product_found:row.product_found!==false,location_id:row.location_id||null,container_id:row.container_id||null,container_name:row.container_name||null,section:row.section||null,aisle:row.aisle||null,pallet:row.pallet||null,storage_display:row.storage_display||null,duplicated_from:row.duplicated_from||null,product_id:row.product_id||null};
        await api(`/r145f30/manual-drafts/${encodeURIComponent(current.id)}/scan-fast`,{method:'POST',body:JSON.stringify(payload),timeoutMs:15000});
      }
      current=await api(`/r145f30/manual-drafts/${encodeURIComponent(current.id)}/snapshot?`+new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId,worker}),{timeoutMs:20000});
    }
    await fc27CacheServerDraft(current);return current;
  })();fc27ServerDraftFlight=job;try{return await job;}finally{if(fc27ServerDraftFlight===job)fc27ServerDraftFlight=null;}
}
async function ensureManualReceivingDraft(){
 const snap=await fc27EnsureServerDraft();return fc27CacheServerDraft(snap);
}
function loadManualReceivingDraft(){if(fc27ServerDraft&&fc27ServerDraftContext===fc27DraftContext())return fc27ServerToLocal(fc27ServerDraft);return FC26DraftStore.snapshot(manualReceivingDraftKey());}
function fc26Editable(draft){if(draft.posting)throw Error('\u041f\u0440\u0438\u0451\u043c\u043a\u0430 \u043e\u0436\u0438\u0434\u0430\u0435\u0442 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f. \u041d\u0430\u0436\u043c\u0438\u0442\u0435 \u00ab\u041f\u043e\u0432\u0442\u043e\u0440\u0438\u0442\u044c \u043f\u0440\u043e\u0432\u0435\u0434\u0435\u043d\u0438\u0435\u00bb: \u0431\u0443\u0434\u0435\u0442 \u043f\u0440\u043e\u0432\u0435\u0440\u0435\u043d \u0442\u043e\u0442 \u0436\u0435 \u0441\u043f\u0438\u0441\u043e\u043a \u0431\u0435\u0437 \u043f\u043e\u0432\u0442\u043e\u0440\u043d\u043e\u0433\u043e \u0437\u0430\u0447\u0438\u0441\u043b\u0435\u043d\u0438\u044f.');}
async function saveManualReceivingDraft(draft,key=manualReceivingDraftKey()){
 fc26DraftSaving++;if(key===manualReceivingDraftKey())renderManualReceivingDraft();
 try{return await FC26DraftStore.put(key,draft);}
 finally{fc26DraftSaving--;if(key===manualReceivingDraftKey())renderManualReceivingDraft();}
}

/* FC_R145F30_FAST_RECEIVING_WORKER_DRAFT */
let fc30DraftCacheTimer=0,fc30SkuIndexSource=null,fc30SkuIndexLength=-1,fc30SkuIndex=new Map();
function fc30WorkerName(){return String($('receiveBy')?.value||'').trim().replace(/\s+/g,' ');}
function fc30WorkerKey(){return fc30WorkerName().toLocaleLowerCase('ru-RU')||'__NO_WORKER__';}
function fc30LegacyDraftKey(){return scopedStorageKey(`fc_manual_receiving_draft_v1:${state.clientId||'none'}`,state.warehouseId);}
function fc30InstallFastStyles(){
 if(document.getElementById('fc30FastReceivingStyles'))return;
 const st=document.createElement('style');st.id='fc30FastReceivingStyles';st.textContent=`
 .manual-receiving-draft{contain:layout paint;overflow-anchor:none}
 .manual-receiving-draft *{animation:none!important;transition:none!important;scroll-behavior:auto!important}
 .fc30-worker-state{padding:10px 12px;border-radius:12px;background:#effaf3;color:#17623a;font-weight:700;margin-bottom:10px}
 .fc30-worker-state.bad{background:#fff2f0;color:#9f2d20}
 .fc30-other-group{border-top:1px solid #e7eaf0}.fc30-other-group>summary{cursor:pointer;padding:11px 14px;display:flex;justify-content:space-between;gap:12px;font-weight:750}
 .fc30-lazy-body{padding:0 10px 10px}.fc30-saving{opacity:.68}.fc30-scan-confirm{font-size:12px;color:#17623a}
 `;document.head.appendChild(st);
}
function fc30FastSpeak(ok=true){
 try{window.speechSynthesis?.cancel?.();}catch(_){}
 try{window.FC25?.beep?.(!ok);}catch(_){}
 try{window.FC25?.speak?.(ok?'Добавлен':'Ошибка',!ok);}catch(_){}
}
function fc30SkuFast(raw){
 const normalized=normalizeReceivingBarcode(raw),barcode=normalized.barcode||String(raw||'').trim();
 const src=state.skus||[];
 if(src!==fc30SkuIndexSource||src.length!==fc30SkuIndexLength){
  fc30SkuIndexSource=src;fc30SkuIndexLength=src.length;fc30SkuIndex=new Map();
  for(const sku of src){for(const v of [sku.barcode,sku.sku_code,sku.article]){const k=String(v||'').trim().toLowerCase();if(k&&!fc30SkuIndex.has(k))fc30SkuIndex.set(k,sku);}}
 }
 const sku=fc30SkuIndex.get(String(barcode).toLowerCase())||null;
 return {normalized,barcode,sku};
}
function fc30DraftRowHtml(row,index=0){
 const sku=findSku(row.barcode),name=row.name||(sku?skuLabel(sku):`ШК ${row.barcode}`),meta=[row.article,row.size].filter(Boolean).join(' · '),place=row.storage_display||[row.section&&`Секция ${row.section}`,row.aisle&&`Проход ${row.aisle}`,row.pallet&&`Палета ${row.pallet}`,row.container_name&&`Короб ${row.container_name}`].filter(Boolean).join(' → ')||row.location_code;
 return `<tr data-fc30-line-id="${esc(row.id)}" data-fc30-barcode="${esc(row.barcode)}" class="${row.product_found===false?'manual-draft-unmatched':''}"><td data-fc30-row-no>${index+1}</td><td><b>${esc(name)}</b>${meta?`<small class="subline">${esc(meta)}</small>`:''}<small class="subline">ШК ${esc(row.barcode)}</small><button type="button" class="mini secondary fc26-edit-card" data-fc26-card-edit="${esc(row.barcode)}">Изменить карточку</button>${row.duplicated_from?`<small class="subline good">Задвоенный скан исправлен: ${esc(row.duplicated_from)} → ${esc(row.barcode)}</small>`:''}${row.product_found===false?'<small class="subline bad">Товар не заполнен в справочнике — укажите артикул и размер</small>':''}</td><td><b>${esc(row.container_name||row.container_code||'Без короба')}</b><small class="subline">${esc(place)}</small></td><td><input class="manual-draft-qty" type="number" min="1" step="1" value="${Number(row.quantity||1)}" data-manual-draft-qty="${esc(row.id)}"/></td><td><button type="button" class="mini danger" data-manual-draft-remove="${esc(row.id)}">Убрать</button></td></tr>`;
}
function fc30ScheduleDraftCache(){
 clearTimeout(fc30DraftCacheTimer);fc30DraftCacheTimer=setTimeout(()=>{
  if(!fc27ServerDraft)return;const key=manualReceivingDraftKey(),local=fc27ServerToLocal(fc27ServerDraft);
  FC26DraftStore.put(key,local).catch(e=>console.warn('FC30 cache write failed',e));
 },350);
}
function fc30MergeFastResult(result){
 if(!fc27ServerDraft||String(fc27ServerDraft.id)!==String(result.receipt_id))return false;
 const line=result.line;if(line){const rows=fc27ServerDraft.rows||(fc27ServerDraft.rows=[]),i=rows.findIndex(x=>String(x.id)===String(line.id));if(i>=0)rows[i]={...rows[i],...line};else rows.push(line);}
 fc27ServerDraft.units=Number(result.units||0);fc27ServerDraft.positions=Number(result.positions||0);fc27ServerDraft.updated_at=result.updated_at||fc27ServerDraft.updated_at;fc30ScheduleDraftCache();return true;
}
function fc30PatchActiveBox(result){
 const panel=$('manualReceivingDraftPanel'),box=panel?.querySelector('[data-fc30-active-box="1"]'),line=result.line;if(!panel||!box||!line){renderManualReceivingDraft();return;}
 const ctx=manualReceivingStorageContext||{},same=fc44SameDraftBox(line,ctx);
 if(!same){renderManualReceivingDraft();return;}
 const tbody=box.querySelector('tbody');if(!tbody){renderManualReceivingDraft();return;}
 let tr=tbody.querySelector(`[data-fc30-line-id="${CSS.escape(String(line.id))}"]`);
 if(tr){const q=tr.querySelector('[data-manual-draft-qty]');if(q)q.value=String(Number(line.quantity||0));}
 else{tbody.querySelector('.empty-state')?.closest('tr')?.remove();tbody.insertAdjacentHTML('beforeend',fc30DraftRowHtml(line,tbody.querySelectorAll('tr[data-fc30-line-id]').length));}
 const at=box.querySelector('[data-fc30-active-total]');if(at)at.textContent=`${fmtQty(Number(result.place_total||0))} шт.`;
 const badge=panel.querySelector('[data-fc30-draft-badge]');if(badge)badge.textContent=`${Number(result.positions||0)} поз. · ${fmtQty(Number(result.units||0))} шт.`;
 const status=panel.querySelector('[data-fc30-worker-state]');if(status){status.textContent=`Серверный черновик · ${fc30WorkerName()} · ${fmtQty(Number(result.units||0))} шт. сохранено`;status.classList.remove('bad');}
}
async function fc30ResumeWorkerDraft(){
 const worker=fc30WorkerName();if(!worker){fc27ServerDraft=null;fc27ServerDraftContext='';renderManualReceivingDraft();return;}
 if(manualReceivingScanBusy||manualReceivingScanQueue.length){toast('Дождитесь сохранения текущих сканов перед сменой приёмщика',true);return;}
 const oldWorker=String(fc27ServerDraft?.worker||'').trim(),oldUnits=Number(fc27ServerDraft?.units||0);
 if(oldWorker&&oldUnits>0&&oldWorker.toLocaleLowerCase('ru-RU')!==worker.toLocaleLowerCase('ru-RU')&&!confirm(`Переключиться с приёмки «${oldWorker}» (${oldUnits} шт.) на приёмщика «${worker}»?\n\nТекущая приёмка останется сохранена на сервере.`)){if($('receiveBy'))$('receiveBy').value=oldWorker;return;}
 fc27ServerDraft=null;fc27ServerDraftContext='';fc27ServerDraftFlight=null;
 try{const snap=await fc27EnsureServerDraft();await fc27CacheServerDraft(snap);renderManualReceivingDraft();toast(`Приёмщик ${worker}: восстановлено ${fmtQty(Number(snap.units||0))} шт.`);}
 catch(e){toast(e.message,true);renderManualReceivingDraft();}
}

/* FC_R145F44_STRICT_ACTIVE_BOX_MATCH */
function fc44DraftBoxAliases(x){return [x?.container_code,x?.container_barcode,x?.container_name,x?.box_name].map(v=>String(v??'').trim().toLocaleLowerCase('ru-RU')).filter(Boolean);}
function fc44SameDraftBox(row,ctx){
  const ra=fc44DraftBoxAliases(row),ca=fc44DraftBoxAliases(ctx);
  // Explicit physical code/name wins. UUID is only a legacy fallback when the
  // row has no visible box identity at all.
  if(ra.length)return ra.some(x=>ca.includes(x));
  return Boolean(row?.container_id&&ctx?.container_id&&String(row.container_id)===String(ctx.container_id));
}
function renderManualReceivingDraft(){
  fc30InstallFastStyles();
  const panel=$('manualReceivingDraftPanel');if(!panel)return;
  const worker=fc30WorkerName();
  if(!state.clientId||!state.warehouseId){panel.innerHTML='<p>Выберите клиента и склад.</p>';return;}
  if(!worker){panel.innerHTML='<div class="fc30-worker-state bad" data-fc30-worker-state>Укажите имя приёмщика. Незавершённая приёмка будет храниться на сервере под этим именем.</div>';return;}
  const storeKey=manualReceivingDraftKey();
  if(!FC26DraftStore.isReady(storeKey)&&!fc27ServerDraft){
    panel.innerHTML=`<div class="fc30-worker-state" data-fc30-worker-state>Открываем серверную приёмку сотрудника ${esc(worker)}...</div>`;
    ensureManualReceivingDraft().then(()=>{if(storeKey===manualReceivingDraftKey())renderManualReceivingDraft();}).catch(e=>{if(storeKey===manualReceivingDraftKey())panel.innerHTML='<div class="fc30-worker-state bad">'+esc(e.message)+'</div><button type="button" class="secondary" id="fc26RetryDraft">Повторить загрузку</button>';});return;
  }
  const cellMode=manualReceivingIsCellMode(),emptyText=cellMode?'Отсканируйте ячейку, затем сканируйте ШК товаров подряд.':'Выберите короб, затем сканируйте ШК товаров подряд.',draft=loadManualReceivingDraft(),rows=draft.rows||[],total=rows.reduce((sum,row)=>sum+Number(row.quantity||0),0),unknown=rows.filter(row=>row.product_found===false).length;
  const groups=FC25.groupBoxes(rows.map(r=>({...r,client_id:r.client_id||state.clientId,warehouse_id:r.warehouse_id||state.warehouseId})),manualReceivingDraftKey()),ctx=manualReceivingStorageContext;
  const active=ctx&&!cellMode?groups.find(g=>g.rows.some(r=>fc44SameDraftBox(r,ctx))):null;
  const others=active?groups.filter(g=>g.key!==active.key):groups.slice();
  const tableFor=(g,start=0)=>`<div class="table-wrap"><table><thead><tr><th>№</th><th>Товар для проверки</th><th>Место / короб</th><th>Количество</th><th></th></tr></thead><tbody>${(g?.rows||[]).map((row,i)=>fc30DraftRowHtml(row,start+i)).join('')||'<tr><td colspan="5" class="empty-state">В этом коробе в текущей приёмке пока 0 шт.</td></tr>'}</tbody></table></div>`;
  let listHtml='';
  if(!rows.length)listHtml=`<div class="empty-state">${esc(emptyText)}</div>`;
  else if(!cellMode&&ctx){
    const currentName=ctx.container_name||ctx.container_barcode||'Текущий короб',currentTotal=active?.total||0;
    const headers=others.map((g,i)=>`<details class="fc30-other-group" data-fc30-lazy-index="${i}"><summary><span>${esc(g.name)}</span><strong>${fmtQty(g.total)} шт. · ${g.rows.length} поз.</strong></summary><div class="fc30-lazy-body" data-fc30-lazy-body></div></details>`).join('');
    listHtml=`<section id="fc30ActiveBox" class="fc27-active-draft-box" data-fc30-active-box="1"><div class="fc27-active-kicker">ПРИНИМАЕМ СЮДА СЕЙЧАС</div><div class="fc27-active-draft-head"><h3>${esc(currentName)}</h3><strong data-fc30-active-total>${fmtQty(currentTotal)} шт.</strong></div>${tableFor(active)}</section>${others.length?`<details class="fc27-other-draft-boxes"><summary>Остальные короба этой приёмки <strong>${others.length}</strong></summary><div>${headers}</div></details>`:''}`;
  }else{
    const headers=groups.map((g,i)=>`<details class="fc30-other-group" data-fc30-lazy-index="${i}"><summary><span>${esc(g.name)}</span><strong>${fmtQty(g.total)} шт. · ${g.rows.length} поз.</strong></summary><div class="fc30-lazy-body" data-fc30-lazy-body></div></details>`).join('');
    listHtml=`<details class="fc27-other-draft-boxes"><summary>${cellMode?'Места этой приёмки':'Все короба этой приёмки'} <strong>${groups.length}</strong></summary>${headers}</details>`;
  }
  panel.innerHTML=`<div class="fc30-worker-state" data-fc30-worker-state>Серверный черновик · Приёмщик: ${esc(worker)} · ${fmtQty(total)} шт. сохранено</div><div class="panel-head"><div><span class="kicker">ЧЕРНОВИК ПРИЁМКИ</span><h2>Сначала наберите и проверьте товар</h2></div><span class="badge" data-fc30-draft-badge>${rows.length} поз. · ${fmtQty(total)} шт.</span></div><p class="muted">Каждый подтверждённый скан сразу сохраняется на сервере. ${cellMode?'Отсканируйте ячейку и сканируйте товары подряд.':'Текущий короб показан сверху; остальные короба загружаются только при раскрытии.'}</p>${unknown?`<div class="warn-box"><b>Не заполнено в справочнике: ${unknown}</b> поз.</div>`:''}${listHtml}<div class="manual-draft-actions"><button id="clearManualReceivingDraft" type="button" class="secondary" ${rows.length?'':'disabled'}>Очистить список</button>${unknown?'<button id="exportManualUnmatched" type="button" class="secondary">Excel: не найдено в WB</button><button id="commitManualUnmatchedInternal" type="button" class="secondary">Принять не найденные во внутренний учёт</button>':''}<button id="commitManualReceivingDraft" type="button" class="primary big" ${rows.some(row=>row.product_found!==false)?'':'disabled'}>${unknown?'Принять найденные позиции':'Добавить всё на остатки'}</button></div>`;
  const lazySource=!cellMode&&ctx?others:groups;
  panel.querySelectorAll('[data-fc30-lazy-index]').forEach(d=>d.addEventListener('toggle',()=>{if(!d.open||d.dataset.loaded==='1')return;const g=lazySource[Number(d.dataset.fc30LazyIndex)];const body=d.querySelector('[data-fc30-lazy-body]');if(body&&g){body.innerHTML=tableFor(g);d.dataset.loaded='1';}}));
  if(fc26DraftSaving||draft.posting)panel.querySelectorAll('input,button').forEach(b=>b.disabled=true);
  if(draft.posting&&!fc26DraftSaving){const retry=panel.querySelector('#commitManualReceivingDraft');if(retry){retry.disabled=fc26CommitBusy;retry.textContent='Повторить проведение';}}
}
async function clearManualReceivingDraft(){const snap=await fc27EnsureServerDraft();const rows=(snap.rows||[]).filter(r=>!r.posted_at&&Number(r.quantity||0)>0);if(rows.length&&!confirm('Очистить серверный черновик? Остатки не изменятся.'))return;await api(`/r145f27/manual-drafts/${encodeURIComponent(snap.id)}/cancel`,{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,request_id:uid()})});fc27ServerDraft=null;fc27ServerDraftContext='';const fresh=await fc27EnsureServerDraft();await fc27CacheServerDraft(fresh);renderManualReceivingDraft();}

async function previewManualReceivingProduct(rawBarcode){
  const fast=fc30SkuFast(rawBarcode),hint=$('receiveSkuHint');
  if(fast.sku){const sku=fast.sku,result={found:true,barcode:String(sku.barcode||fast.barcode),product_id:String(sku.id||''),article:sku.article||sku.sku_code||null,size:sku.size||null,name:sku.name||null,duplicated_from:fast.normalized.duplicatedFrom||null};if(hint){hint.textContent=[result.article,result.name,result.size].filter(Boolean).join(' · ');hint.className='good';}return result;}
  const result=await api(`/receiving/product-preview?client_id=${encodeURIComponent(state.clientId)}&barcode=${encodeURIComponent(rawBarcode)}`,{timeoutMs:8000});
  if(hint){hint.textContent=result.found?[result.article,result.name,result.size].filter(Boolean).join(' · '):`ШК ${result.barcode} не найден в каталоге — проверьте перед проведением`;hint.className=result.found?'good':'bad';}
  return {...result,barcode:result.barcode||fast.barcode,duplicated_from:result.duplicated_from||fast.normalized.duplicatedFrom||null};
}
async function addManualReceivingDraftLine(scan){
  if(scan.clientId&&scan.clientId!==state.clientId||scan.warehouseId&&scan.warehouseId!==state.warehouseId)throw Error('Клиент или склад изменён. Повторите сканирование.');
  if(!state.clientId||!state.warehouseId)throw new Error('Сначала выберите клиента и склад');
  const worker=fc30WorkerName();if(!worker)throw new Error('Укажите имя приёмщика');
  const rawBarcode=String(scan.rawBarcode||'').trim(),quantity=Number(scan.quantity||1),product=await previewManualReceivingProduct(rawBarcode),barcode=product.barcode;
  if(!Number.isInteger(quantity)||quantity<1)throw new Error('Количество должно быть целым и больше нуля');
  const receivingLocation=findLocation(scan.locationCode);if(!receivingLocation)throw new Error('Ячейка не найдена. Сначала отсканируйте QR или введите точный номер ячейки.');assertLocationAvailableForClient(receivingLocation);
  const ctx=scan.storageContext||manualReceivingStorageContext||null,containerCode=String(scan.containerCode||ctx?.container_barcode||'').trim()||null;
  const draft=await fc27EnsureServerDraft();if(scan.clientId!==state.clientId||scan.warehouseId!==state.warehouseId||worker!==fc30WorkerName())throw Error('Клиент, склад или приёмщик изменён. Скан не добавлен.');
  const payload={client_id:state.clientId,warehouse_id:state.warehouseId,worker,request_id:uid(),barcode,quantity,location_code:receivingLocation.code,container_code:containerCode,article:product.article||null,size:product.size||null,name:product.name||null,product_found:Boolean(product.found),location_id:receivingLocation.id,container_id:ctx?.container_id||null,container_name:ctx?.container_name||containerCode||null,section:ctx?.section||'',aisle:ctx?.aisle||'',pallet:ctx?.pallet||'',storage_display:ctx?manualReceivingStructureSummary(ctx):receivingLocation.name||receivingLocation.code,duplicated_from:product.duplicated_from||null,product_id:product.product_id||null};
  $('receiveResult').innerHTML='<b>Сохраняем скан на сервере…</b>';
  const result=await api(`/r145f30/manual-drafts/${encodeURIComponent(draft.id)}/scan-fast`,{method:'POST',body:JSON.stringify(payload),timeoutMs:15000});
  fc30MergeFastResult(result);fc30PatchActiveBox(result);fc30FastSpeak(true);
  $('receiveResult').innerHTML=`<b>Товар сохранён на сервере</b><span>${esc(product.name||`ШК ${barcode}`)} · ${fmtQty(quantity)} шт. · ${esc(ctx?manualReceivingStructureSummary(ctx):receivingLocation.code)}.</span>`;
}
async function processManualReceivingScanQueue(){
  if(manualReceivingScanBusy)return;manualReceivingScanBusy=true;
  try{while(manualReceivingScanQueue.length){const scan=manualReceivingScanQueue.shift();try{await addManualReceivingDraftLine(scan);}catch(error){FC25.speak('\u0422\u043e\u0432\u0430\u0440 \u043d\u0435 \u0434\u043e\u0431\u0430\u0432\u043b\u0435\u043d',true);$('receiveResult').innerHTML=`<b class="bad">Товар не добавлен</b><span>${esc(error.message)}</span>`;toast(error.message,true);}}}
  finally{manualReceivingScanBusy=false;}
}
function captureManualReceivingScan(useCurrentQuantity=false){
  if(!FC26DraftStore.isReady(manualReceivingDraftKey())){toast('\u0414\u043e\u0436\u0434\u0438\u0442\u0435\u0441\u044c \u0437\u0430\u0433\u0440\u0443\u0437\u043a\u0438 \u0447\u0435\u0440\u043d\u043e\u0432\u0438\u043a\u0430',true);return false;}
  if(fc26CommitBusy){toast('\u0414\u043e\u0436\u0434\u0438\u0442\u0435\u0441\u044c \u043f\u0440\u043e\u0432\u0435\u0434\u0435\u043d\u0438\u044f \u043f\u0440\u0438\u0451\u043c\u043a\u0438',true);return false;}
  try{fc26Editable(loadManualReceivingDraft());}catch(e){toast(e.message,true);return false;}
  clearTimeout(manualReceivingScanTimer);manualReceivingScanTimer=0;manualReceivingAdaptive.last=0;manualReceivingAdaptive.gap=0;
  const input=$('receiveSku'),rawBarcode=String(input?.value||'').trim();if(!rawBarcode){if(useCurrentQuantity)toast('Отсканируйте ШК товара',true);return false;}
  const quantity=useCurrentQuantity?Number($('receiveQty')?.value||1):1,cellMode=manualReceivingIsCellMode();let locationCode=String($('receiveLocation')?.value||'').trim();if(!Number.isInteger(quantity)||quantity<1){toast('Количество должно быть целым и больше нуля',true);return false;}
  if(cellMode){
    if(!findLocation(locationCode)){toast('Сначала отсканируйте ячейку. Для ячеечного хранения короб не нужен.',true);input.value='';$('receiveLocation')?.focus();return false;}
  }else{
    if(!manualReceivingStorageContext){toast('Для коробного хранения сначала выберите или создайте короб',true);input.value='';$('manualReceiveBox')?.focus();return false;}
    locationCode=String(manualReceivingStorageContext.location_code||locationCode||'').trim();
    if(!findLocation(locationCode)){toast('Место текущего короба не найдено. Выберите короб заново.',true);input.value='';$('manualReceiveBox')?.focus();return false;}
  }
  const storageContext=cellMode?null:{...manualReceivingStorageContext},containerCode=cellMode?null:(String($('receiveContainer')?.value||'').trim()||String(manualReceivingStorageContext?.container_barcode||'').trim()||null);input.value='';$('receiveQty').value='1';$('receiveSkuHint').textContent='';$('receiveSkuHint').className='';input.focus();manualReceivingScanQueue.push({rawBarcode,quantity,locationCode,containerCode,storageContext,clientId:state.clientId,warehouseId:state.warehouseId});processManualReceivingScanQueue();return true;
}
function scheduleManualReceivingScan(){
  clearTimeout(manualReceivingScanTimer);manualReceivingScanTimer=0;if(!String($('receiveSku')?.value||'').trim())return;
  const now=performance.now();if(manualReceivingAdaptive.last){const gap=now-manualReceivingAdaptive.last;manualReceivingAdaptive.gap=Math.max(gap,manualReceivingAdaptive.gap*0.55);}manualReceivingAdaptive.last=now;
  const delay=Math.min(220,Math.max(70,Math.round((manualReceivingAdaptive.gap||0)*1.4+70)));
  manualReceivingScanTimer=setTimeout(()=>captureManualReceivingScan(false),delay);
}
async function commitManualReceivingDraft(){return fc26CommitManual('known');}
async function fc26CommitManual(lane){
 if(fc26CommitBusy)return;if(manualReceivingScanBusy||manualReceivingScanQueue.length||FC26Cards.isOpen())throw Error('Дождитесь сохранения всех сканов и закройте карточку.');
 await ensureContext();const snap=await fc27EnsureServerDraft();const rows=(snap.rows||[]).filter(r=>!r.posted_at&&Number(r.quantity||0)>0&&(lane==='known'?r.product_found!==false:r.product_found===false));if(!rows.length)throw Error('Нет товара для проведения.');
 const total=rows.reduce((n,r)=>n+Number(r.quantity||0),0),stockClass=$('receiveStockClass')?.value||'MAIN';if(!confirm(`Провести единую приёмку: ${fmtQty(total)} шт. (${rows.length} поз.)?`))return;
 fc26CommitBusy=true;renderManualReceivingDraft();try{const result=await api(`/r145f27/manual-drafts/${encodeURIComponent(snap.id)}/commit`,{method:'POST',timeoutMs:180000,body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,reference:$('receiveReference')?.value?.trim()||null,performed_by:$('receiveBy')?.value?.trim()||null,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null,lane,stock_class:lane==='known'?stockClass:'MAIN'})});
  if(result.receipt)await fc27CacheServerDraft(result.receipt);if(result.completed){fc27ServerDraft=null;fc27ServerDraftContext='';await FC26DraftStore.put(manualReceivingDraftKey(),newManualReceivingDraft()).catch(()=>{});}let wbWarn='';const inner=result.result||{};if(lane==='known'&&stockClass==='FBS'&&($('receivePublishTarget')?.value||'LOCAL')==='LOCAL_WB'&&(inner.pending_lines||[]).length){const wr=await publishFreshReceivingLines(inner.pending_lines,'Приёмка');if(wr?.failed)wbWarn=` WB ошибок: ${wr.failed}.`;}
  $('receiveResult').innerHTML=`<b>Приёмка проведена единым документом</b><span>${fmtQty(inner.quantity_received||total)} шт.${esc(wbWarn)}</span>`;state.inventoryCatalogLoaded=false;loadContextData().catch(()=>{});window.loadR62ManualReceipts?.().catch(()=>{});toast(`Принято: ${fmtQty(inner.quantity_received||total)} шт.${wbWarn}`,Boolean(wbWarn));if(result.completed)await fc27EnsureServerDraft();renderManualReceivingDraft();
 }finally{fc26CommitBusy=false;renderManualReceivingDraft();}
}
async function exportManualReceivingUnmatched(){
  await ensureContext();await ensureManualReceivingDraft();const rows=(loadManualReceivingDraft().rows||[]).filter(row=>row.product_found===false);if(!rows.length)throw new Error('Нет позиций без карточки WB');
  const blob=await apiBlob('/receiving/manual/unmatched-export.xlsx',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,reference:$('receiveReference').value.trim()||null,lines:rows.map(row=>({barcode:row.barcode,quantity:Number(row.quantity),article:row.article||null,size:row.size||null,name:row.name||null,location_code:row.location_code||null,container_code:row.container_code||null,storage_display:row.storage_display||null}))})});
  const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=`Не_найдено_WB_${new Date().toISOString().slice(0,10)}.xlsx`;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1500);toast(`Excel сформирован: ${rows.length} поз.`);
}
async function commitManualReceivingUnmatchedInternal(){return fc26CommitManual('unmatched-internal');}
function installManualReceivingDraftUi(){
  const form=$('receiveForm'),workspace=form?.closest('.workspace-grid');if(!form||!workspace||$('manualReceivingDraftPanel'))return;const panel=document.createElement('section');panel.id='manualReceivingDraftPanel';panel.className='panel manual-receiving-draft';workspace.appendChild(panel);
  const heading=form.querySelector('.panel-head h2');if(heading)heading.textContent='Добавить позиции в черновик';const submit=form.querySelector('button[type="submit"]');if(submit)submit.textContent='Добавить позицию в список';const qty=$('receiveQty');if(qty){qty.min='1';qty.step='1';qty.value='1';}
  if(!$('manualReceivingStructurePanel')){
    const structure=document.createElement('div');structure.id='manualReceivingStructurePanel';structure.className='manual-receiving-structure';structure.innerHTML=`<div class="manual-structure-title"><div><span class="kicker">МЕСТО ПРИЁМКИ</span><h3>Ячейка и короб — независимые виды хранения</h3></div><label class="manual-storage-mode"><span>Куда кладём товар</span><select id="manualReceiveStorageMode"><option value="CELL" selected>Ячейка · без короба</option><option value="BOX">Короб</option></select></label></div><div class="manual-structure-fields manual-box-only" data-manual-box-only><label><span>Секция <small>необязательно</small></span><input id="manualReceiveSection" type="text" placeholder="Например 3" autocomplete="off"/></label><label><span>Ряд <small>сторона палеты</small></span><select id="manualReceiveRow"><option value="">Не указан</option><option value="1">Ряд 1 · до 8 коробов</option><option value="2">Ряд 2 · до 8 коробов</option></select></label><label><span>Проход <small>необязательно</small></span><input id="manualReceiveAisle" type="text" placeholder="Например 2" autocomplete="off"/></label><label><span>Палета <small>необязательно</small></span><input id="manualReceivePallet" type="text" placeholder="Например 15" autocomplete="off"/></label><label class="manual-box-field"><span>Короб</span><input id="manualReceiveBox" type="text" placeholder="Сканируйте QR/ШК или введите название" autocomplete="off"/></label></div><div class="button-row manual-structure-actions manual-box-only" data-manual-box-only><button id="manualReceiveOpenBox" type="button" class="primary">Открыть / создать по скану</button><button id="manualReceiveCreateNamedBox" type="button" class="secondary">Создать по названию + QR</button><button id="manualReceivePrintBox" type="button" class="secondary">QR текущего короба</button><button id="manualReceiveClearBox" type="button" class="secondary">Сменить короб</button><label><input id="manualReceiveAutoPrintBox" type="checkbox" checked/> печатать QR нового короба</label></div><div id="manualReceiveActiveContext" class="result-box manual-active-context"></div>`;
    form.insertBefore(structure,form.querySelector('.scan-step')||form.firstChild);
    const legacyLocation=$('receiveLocation')?.closest('.scan-step');if(legacyLocation)legacyLocation.classList.add('manual-legacy-location','hidden');
    const legacyContainer=$('receiveContainer')?.closest('.scan-step');if(legacyContainer)legacyContainer.classList.add('hidden');
    $('manualReceiveOpenBox').addEventListener('click',()=>activateManualReceivingBox('SCAN').catch(e=>toast(e.message,true)));
    $('manualReceiveCreateNamedBox').addEventListener('click',()=>activateManualReceivingBox('NAME').catch(e=>toast(e.message,true)));
    $('manualReceivePrintBox').addEventListener('click',()=>printManualReceivingBoxLabel(manualReceivingStorageContext).catch(e=>toast(e.message,true)));
    $('manualReceiveClearBox').addEventListener('click',()=>{clearManualReceivingStorageContext({keepHierarchy:true});$('manualReceiveBox')?.focus();});
    $('manualReceiveBox').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();activateManualReceivingBox('SCAN').catch(x=>toast(x.message,true));}});
    $('manualReceiveSection').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();$('manualReceiveRow')?.focus();}});$('manualReceiveRow').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();$('manualReceiveAisle')?.focus();}});$('manualReceiveAisle').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();$('manualReceivePallet')?.focus();}});$('manualReceivePallet').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();$('manualReceiveBox')?.focus();}});
    for(const id of ['manualReceiveSection','manualReceiveRow','manualReceiveAisle','manualReceivePallet'])$(id).addEventListener('change',()=>{if(manualReceivingStorageContext){clearManualReceivingStorageContext({keepHierarchy:true});toast('Место изменено. Выберите короб для этого ряда.');}});
    $('manualReceiveStorageMode').addEventListener('change',()=>applyManualReceivingStorageMode());
    $('receiveLocation')?.addEventListener('input',()=>{if(manualReceivingIsCellMode())renderManualReceivingStorageContext();});
    applyManualReceivingStorageMode();
  }
  if(!document.getElementById('manualReceivingDraftStyles')){const style=document.createElement('style');style.id='manualReceivingDraftStyles';style.textContent='.manual-receiving-draft{grid-column:1/-1}.manual-receiving-draft .manual-draft-qty{width:110px}.manual-draft-actions{display:flex;justify-content:flex-end;gap:10px;margin-top:14px}.manual-receiving-draft p{margin-top:0}.manual-draft-unmatched td{background:#fff8eb!important}.manual-receiving-structure{grid-column:1/-1;padding:16px;border:1px solid #d7deea;border-radius:16px;background:#f8faff;margin-bottom:14px}.manual-structure-title{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}.manual-structure-title h3{margin:3px 0 10px}.manual-structure-fields{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:10px}.manual-structure-fields label{display:grid;gap:5px}.manual-structure-fields small{color:#667085;font-weight:400}.manual-box-field input{font-weight:700}.manual-structure-actions{align-items:center;flex-wrap:wrap;margin-top:12px}.manual-active-context{margin-top:12px}.manual-active-context.active{border-color:#79c99e;background:#effaf3}.manual-storage-mode{display:grid;gap:5px;min-width:240px}.manual-storage-mode span{font-size:12px;color:#667085}.manual-box-only.hidden{display:none!important}.fc27-active-draft-box{margin:14px 0 18px;padding:14px;border:2px solid #5a4ded;border-radius:16px;background:#f8f7ff}.fc27-active-kicker{font-size:12px;font-weight:900;letter-spacing:.08em;color:#5545ec}.fc27-active-draft-head{display:flex;align-items:center;justify-content:space-between;gap:12px}.fc27-active-draft-head h3{margin:5px 0 10px;font-size:22px}.fc27-active-draft-head strong{font-size:20px}.fc27-other-draft-boxes{margin:14px 0;border:1px solid #d7deea;border-radius:14px;background:#fff}.fc27-other-draft-boxes>summary{cursor:pointer;padding:14px 16px;font-weight:800;display:flex;justify-content:space-between}.fc27-other-draft-boxes[open]>summary{border-bottom:1px solid #e1e6ef}@media(max-width:900px){.manual-structure-fields{grid-template-columns:1fr 1fr}}@media(max-width:700px){.manual-draft-actions,.manual-structure-actions{align-items:stretch;flex-direction:column}.manual-structure-fields{grid-template-columns:1fr}.manual-structure-title{flex-direction:column}}';document.head.appendChild(style);}

  panel.addEventListener('change',async e=>{const input=e.target.closest('[data-manual-draft-qty]');if(!input)return;try{const value=Number(input.value);if(!Number.isInteger(value)||value<1)throw Error('Количество должно быть целым и больше нуля');const d=await fc27EnsureServerDraft();const snap=await api(`/r145f27/manual-drafts/${encodeURIComponent(d.id)}/lines/${encodeURIComponent(input.dataset.manualDraftQty)}`,{method:'PATCH',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,request_id:uid(),quantity:value})});await fc27CacheServerDraft(snap);renderManualReceivingDraft();}catch(err){toast(err.message,true);renderManualReceivingDraft();}});
  panel.addEventListener('click',async e=>{try{
   if(e.target.closest('#fc26RetryDraft')){await FC26DraftStore.refresh(manualReceivingDraftKey());renderManualReceivingDraft();return;}
   const remove=e.target.closest('[data-manual-draft-remove]');if(remove){const d=await fc27EnsureServerDraft();const snap=await api(`/r145f27/manual-drafts/${encodeURIComponent(d.id)}/lines/${encodeURIComponent(remove.dataset.manualDraftRemove)}`,{method:'DELETE',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,request_id:uid()})});await fc27CacheServerDraft(snap);renderManualReceivingDraft();return;}
   const edit=e.target.closest('[data-fc26-card-edit]');if(edit){const key=manualReceivingDraftKey();fc26Editable(await ensureManualReceivingDraft());const p=await FC26Cards.open(edit.dataset.fc26CardEdit);if(key!==manualReceivingDraftKey())return;await fc26UpdateDraftCard(p);return;}
   if(e.target.closest('#clearManualReceivingDraft')){await clearManualReceivingDraft();return;}
   if(e.target.closest('#exportManualUnmatched')){await exportManualReceivingUnmatched();return;}
   if(e.target.closest('#commitManualUnmatchedInternal')){await commitManualReceivingUnmatchedInternal();return;}
   if(e.target.closest('#commitManualReceivingDraft'))await commitManualReceivingDraft();
  }catch(err){toast(err.message,true);}});
  $('receiveLocation')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();renderManualReceivingStorageContext();$('receiveSku')?.focus();}});$('receiveSku')?.addEventListener('input',scheduleManualReceivingScan);$('receiveSku')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();captureManualReceivingScan(false);}});$('receiveQty')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();form.requestSubmit();}});
  const workerInput=$('receiveBy');if(workerInput&&!workerInput.dataset.fc30WorkerBound){workerInput.dataset.fc30WorkerBound='1';workerInput.addEventListener('change',()=>fc30ResumeWorkerDraft());workerInput.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();workerInput.blur();fc30ResumeWorkerDraft();}});}
  renderManualReceivingDraft();
}
async function syncPendingReceivingToWb(){await ensureContext();const q=loadPendingWbReceiving();if(!q.length)throw new Error('Нет приёмки для отправки в WB');if(!confirm(`Отправить на WB FBS ${q.length} изменений остатков?`))return;const wid=Number(state.wbWarehouseFilter||0)||null;const r=await api('/receiving/sync-wb',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,wb_warehouse_id:wid,lines:q})});const failed=(r.results||[]).filter(x=>!x.ok);if(failed.length){const keep=[];(r.results||[]).forEach((x,i)=>{if(!x.ok&&q[i])keep.push(q[i]);});savePendingWbReceiving(keep);toast(`WB обновлено: ${r.synced}, ошибок: ${r.failed}`,true);throw new Error(failed.slice(0,3).map(x=>`${x.barcode}: ${x.error||'ошибка WB'}`).join('\n'));}savePendingWbReceiving([]);toast(`Приёмка отправлена на WB FBS: ${r.synced} SKU`);}
async function publishFreshReceivingLines(lines,sourceLabel='Приёмка'){const list=(lines||[]).filter(x=>x&&x.barcode&&Number(x.quantity||0)>=0);if(!list.length)return {synced:0,failed:0,results:[]};const wid=Number(state.wbWarehouseFilter||0)||null;try{const r=await api('/receiving/sync-wb',{method:'POST',timeoutMs:180000,body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,wb_warehouse_id:wid,lines:list})});const failedLines=[];(r.results||[]).forEach((item,index)=>{if(!item?.ok&&list[index])failedLines.push(list[index]);});if(failedLines.length)addPendingWbReceiving(failedLines);if(r.failed)toast(`${sourceLabel}: WB обновлён частично — ошибок ${r.failed}. Ошибки оставлены в очереди.`,true);else toast(`${sourceLabel}: остатки WB FBS обновлены — ${r.synced||list.length} SKU.`);return r;}catch(error){addPendingWbReceiving(list);toast(`${sourceLabel}: локальный склад сохранён, но WB не обновлён. Позиции оставлены в очереди. ${error.message}`,true);return {synced:0,failed:list.length,results:[],error:error.message};}}
function receivingTemplateUrl(kind){return `/receiving/templates/${encodeURIComponent(kind)}.xlsx`;}
function installReceivingExcelControls(){const bulk=$('page-bulkimport');if(bulk&&!$('bulkExcelReliabilityPanel')){const panel=document.createElement('div');panel.id='bulkExcelReliabilityPanel';panel.className='panel';panel.innerHTML='<div class="panel-head"><div><span class="kicker">ШАБЛОН И WB FBS</span><h3>Надёжная Excel-приёмка</h3><p>ШК товара — единственный ключ сопоставления с WB. Короб, артикул и размер могут называться как угодно.</p></div></div><div class="button-row"><button type="button" class="secondary" data-download-current-bulk-template>Скачать шаблон выбранного формата</button><label>Куда принять<select id="bulkReceiveStockClass"><option value="FBS" selected>Сразу в FBS</option><option value="MAIN">Основной склад</option></select></label></div>';bulk.prepend(panel);panel.querySelector('[data-download-current-bulk-template]').addEventListener('click',()=>{const kind=$('bulkReceiveFormat')?.value==='ADDRESS5'?'BULK_BOX5':'BULK_SIMPLE2';window.location.href=receivingTemplateUrl(kind);});}const pallet=$('page-palletreceive');if(pallet&&!$('palletExcelReliabilityPanel')){const panel=document.createElement('div');panel.id='palletExcelReliabilityPanel';panel.className='panel';panel.innerHTML='<div class="panel-head"><div><span class="kicker">ШАБЛОН ПАЛЕТНОЙ ПРИЁМКИ</span><h3>Товар по принятым коробам</h3><p>Формат: Короб · ШК товара · Количество · Артикул · Размер.</p></div></div><div class="button-row"><button type="button" class="secondary" data-download-pallet-template>Скачать шаблон Excel</button><label>Куда принять<select id="palletReceiveStockClass"><option value="FBS" selected>Сразу в FBS</option><option value="MAIN">Основной склад</option></select></label><label>После приёмки FBS<select id="palletReceivePublishTarget"><option value="LOCAL_WB" selected>Сразу обновить WB FBS</option><option value="LOCAL">Только FulfillCore, WB позже</option></select></label></div>';pallet.prepend(panel);panel.querySelector('[data-download-pallet-template]').addEventListener('click',()=>{window.location.href=receivingTemplateUrl('PALLET_LAYOUT5');});}}
installReceivingExcelControls();

function installReceivingDestinationUi(){
  const form=$('receiveForm');if(!form||$('receivePublishTarget'))return;
  const boxInput=$('receiveContainer'),boxStep=boxInput?.closest('.scan-step');if(boxStep)boxStep.classList.add('hidden');if(boxInput){boxInput.value='';boxInput.disabled=false;}const locationStep=$('receiveLocation')?.closest('.scan-step');if(locationStep)locationStep.classList.add('manual-direct-location','hidden');
  const skuStep=$('receiveSku')?.closest('.scan-step'),skuNumber=skuStep?.querySelector('span');if(skuNumber)skuNumber.textContent='2';
  const mode=$('receiveMode'),modeRow=mode?.closest('.form-row');if(mode){mode.value='ADD';mode.disabled=true;}if(modeRow)modeRow.classList.add('hidden');
  const reference=$('receiveReference');if(reference){
    const referenceLabel=reference.closest('label'),referenceTitle=referenceLabel?.querySelector('span');if(referenceTitle)referenceTitle.textContent='Название приёмки';else if(referenceLabel?.firstChild)referenceLabel.firstChild.textContent='Название приёмки';reference.placeholder='Например: Поставка 937';
    const target=document.createElement('div');target.className='receive-publish-target';target.innerHTML='<label><span>Куда принять</span><select id="receiveStockClass"><option value="FBS" selected>Сразу в FBS</option><option value="MAIN">Основной склад</option></select></label><label><span>После приёмки FBS</span><select id="receivePublishTarget"><option value="LOCAL_WB" selected>Сразу обновить WB FBS</option><option value="LOCAL">Только FulfillCore, WB позже</option></select></label><small>При выборе FBS товар сразу учитывается в FBS, без промежуточного перевода через Основной склад.</small>';
    reference.insertAdjacentElement('afterend',target);
  }
  const submit=form.querySelector('button[type="submit"]');if(submit)submit.textContent='Добавить позицию в список';
  if($('executeExcelBtn'))$('executeExcelBtn').textContent='Добавить всё на остатки';
  if($('uploadPalletLayoutBtn'))$('uploadPalletLayoutBtn').textContent='Добавить всё на остатки';
  for(const id of ['syncReceivingWbBtn','syncBulkReceivingWbBtn','syncPalletReceivingWbBtn']){const button=$(id);if(button)button.classList.add('hidden');}
  $('syncReceivingWbBtn')?.nextElementSibling?.classList.add('hidden');
  const help=form.nextElementSibling?.querySelector('p');if(help)help.textContent='Отсканируйте ШК — 1 штука автоматически попадёт в черновик, а курсор останется в поле сканирования. Количество при необходимости измените в черновике. Остатки проводятся только кнопкой «Добавить всё на остатки».';
}
function installReceivingWbQueuePanel(){
  const home=$('palletReceiptHome');if(home&&!$('receivingWbQueuePanel')){const panel=document.createElement('div');panel.id='receivingWbQueuePanel';panel.className='panel receiving-wb-queue';panel.innerHTML='<div><span class="kicker">ОСТАТКИ WB FBS</span><h3>Неотправленная приёмка <span id="receivingWbQueueCount"></span></h3><small id="receivingWbQueueNote">Неотправленных изменений нет.</small></div><button id="sendPendingReceivingWbBtn" type="button" class="secondary">Отправить всё на WB FBS</button>';home.prepend(panel);$('sendPendingReceivingWbBtn').addEventListener('click',()=>syncPendingReceivingToWb().catch(e=>toast(e.message,true)));panel.addEventListener('click',pendingWbClickHandler);panel.addEventListener('change',pendingWbClickHandler);}
  // 0.8.0: no pending-WB table is inserted on the bulk-import page.
  if(!document.getElementById('pendingWbQueueStyles')){const style=document.createElement('style');style.id='pendingWbQueueStyles';style.textContent='.receiving-wb-queue{display:grid;gap:12px}.receiving-wb-queue .pending-wb-detail{grid-column:1/-1;min-width:0}.pending-wb-table{width:100%}.pending-wb-actions{margin-top:8px}';document.head.appendChild(style);}
}
installReceivingDestinationUi();
installManualReceivingDraftUi();
installBulkReceivingHistoryUi();
installReceivingWbQueuePanel();
renderPendingWbReceiving();

$('receiveMode')?.addEventListener('change',()=>{const set=$('receiveMode').value==='SET';$('receiveModeHint').textContent=set?'Изменить: указанное количество станет итоговым остатком выбранного товара.':'Добавить: указанное количество прибавится к текущему остатку.';});
$('receiveMode')?.dispatchEvent(new Event('change'));
$('receiveForm').addEventListener('submit',e=>{e.preventDefault();captureManualReceivingScan(true);});

$('moveContainerForm').addEventListener('submit',async e=>{e.preventDefault();try{
  await ensureContext();const c=findContainer($('moveContainerBarcode').value),loc=findLocation($('moveDestination').value);if(!c)throw new Error('Короб не найден');if(!loc)throw new Error('Адрес назначения не найден');
  assertLocationAvailableForClient(loc);const preview=await fetchMoveContainerPreview(c,loc);renderMoveContainerPreview(preview);if(!preview?.can_move)throw new Error(preview?.block_message||'В этом коробе есть резерв активного сборочного листа. Сначала упакуйте товар.');
  const warningQty=Number(preview.active_list_other_bucket_quantity||0);if(warningQty>0&&!confirm(`${preview.warning_message||'На текущем адресе есть резерв активного сборочного листа, но не в этом коробе.'}\n\nКороб ${containerPublicCode(c)} можно переместить. Продолжить?`))return;
  const from=preview.source_location||mapBy(state.locations,c.location_id),fromInfo=movementLocationInfo(from),toInfo=movementLocationInfo(loc),follow=Number(preview.new_reservations_follow_quantity||0),publicCode=containerPublicCode(c);
  await api('/operations/move-container',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,idempotency_key:uid(),performed_by:$('moveBy').value.trim()||null,reference:null,comment:null,container_id:c.id,destination_location_id:loc.id})});
  applyLocalContainerMove(c,loc);$('moveResult').innerHTML=`<b>Перемещено</b><span>Короб ${esc(publicCode)}</span><span>${esc(fromInfo.address)} → ${esc(toInfo.address)}</span>${follow>0?`<span class="good">NEW резерв перенесён на новый адрес: ${esc(fmtQty(follow))} шт.</span>`:''}`;e.target.reset();renderMoveContainerPreview(null);moveContainerPreviewCacheKey='';moveContainerPreviewCacheAt=0;toast('Короб перемещён');refreshContextAfterContainerMove();
}catch(err){toast(err.message,true)}});

$('moveItemForm').addEventListener('submit',async e=>{e.preventDefault();try{
  await ensureContext();const sku=findSku($('moveItemSku').value),src=findLocation($('moveItemSourceLocation').value),dst=findLocation($('moveItemDestinationLocation').value);if(!sku)throw new Error('Товар не найден');if(!src)throw new Error('Исходный адрес не найден');if(!dst)throw new Error('Адрес назначения не найден');
  assertLocationAvailableForClient(dst);
  const srcRaw=$('moveItemSourceContainer').value.trim(),dstRaw=$('moveItemDestinationContainer').value.trim();const srcC=srcRaw?findContainer(srcRaw):null;if(srcRaw&&!srcC)throw new Error('Исходный короб не найден');const dstC=dstRaw?await ensureContainer(dstRaw,dst):null;
  await api('/operations/move',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,idempotency_key:uid(),performed_by:$('moveItemBy').value.trim()||null,reference:null,comment:null,sku_id:sku.id,quantity:Number($('moveItemQty').value),source_location_id:src.id,source_container_id:srcC?.id||null,destination_location_id:dst.id,destination_container_id:dstC?.id||null})});e.target.reset();$('moveItemQty').value='1';await loadContextData();toast('Товар перемещён');
}catch(err){toast(err.message,true)}});

$('shipForm').addEventListener('submit',async e=>{e.preventDefault();try{
  await ensureContext();const loc=findLocation($('shipLocation').value),sku=findSku($('shipSku').value),raw=$('shipContainer').value.trim(),c=raw?findContainer(raw):null;if(!loc)throw new Error('Адрес хранения не найден');if(!sku)throw new Error('Товар не найден');if(raw&&!c)throw new Error('Короб не найден');const qty=Number($('shipQty').value);
  await api('/operations/ship',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,idempotency_key:uid(),performed_by:$('shipBy').value.trim()||null,reference:$('shipReference').value.trim()||null,comment:null,sku_id:sku.id,quantity:qty,source_location_id:loc.id,source_container_id:c?.id||null})});$('shipResult').innerHTML=`<b>Отгружено</b><span>${esc(skuLabel(sku))} · ${fmtQty(qty)} шт.</span>`;$('shipSku').value='';$('shipQty').value='1';await loadContextData();toast('Отгрузка проведена');
}catch(err){toast(err.message,true)}});

async function createLocation(code,barcode){await ensureContext();const c=code.trim(),b=(barcode||'').trim()||c;if(!c)throw new Error('Введите код ячейки');return api('/masterdata/locations',{method:'POST',body:JSON.stringify({warehouse_id:state.warehouseId,parent_id:null,code:c,barcode:b,name:`Ячейка ${c}`,location_type:'CELL'})});}
$('quickLocationForm').addEventListener('submit',async e=>{e.preventDefault();try{await createLocation($('quickLocationCode').value,$('quickLocationBarcode').value);e.target.reset();await loadContextData();toast('Ячейка создана');}catch(err){toast(err.message,true)}});
$('clientForm').addEventListener('submit',async e=>{e.preventDefault();try{await api('/masterdata/clients',{method:'POST',body:JSON.stringify({name:$('clientName').value.trim()})});e.target.reset();await loadContexts();toast('Клиент добавлен');}catch(err){toast(err.message,true)}});
$('warehouseForm').addEventListener('submit',async e=>{e.preventDefault();try{await api('/masterdata/warehouses',{method:'POST',body:JSON.stringify({code:$('warehouseCode').value.trim(),name:$('warehouseName').value.trim()})});e.target.reset();await loadContexts();toast('Склад добавлен');}catch(err){toast(err.message,true)}});
function nextLocationNumber(value){const s=String(value||'').trim();const m=s.match(/^(.*?)(\d+)$/);if(!m)return s?`${s}-1`:'1';return m[1]+String(Number(m[2])+1).padStart(m[2].length,'0');}
let lastCreatedLocationIds=[];
function installLocationStructureBuilder(){
  let form=$('quickLocationForm')||$('locationForm');if(!form)return;
  if(form.id==='quickLocationForm'){const clean=form.cloneNode(false);form.replaceWith(clean);form=clean;}
  const panel=form.closest('.panel,.card,section')||form.parentElement,heading=panel?.querySelector('h2');if(heading)heading.textContent='Массовое создание адресов';
  form.innerHTML=`<h3>Конструктор хранения</h3>
    <div class="location-step"><span>1</span><div><b>Что создаём</b><small>Можно использовать только нужные уровни — лишние создавать не обязательно</small></div></div>
    <label>Тип адреса<select id="locationStructureType"><option value="RACK">Стеллаж с ячейками</option><option value="CELL">Отдельные ячейки без стеллажа</option><option value="PALLET_PLACE">Палеточные места</option><option value="PALLET">Палеты</option><option value="SECTION">Секции / зоны</option></select></label>
    <div id="locationParentFields" class="location-builder-fields">
      <div class="location-step"><span>2</span><div><b>Где находится</b><small>Секция и проход необязательны. Если секцию указать, она создастся автоматически.</small></div></div>
      <label>Секция / зона (необязательно)<input id="locationSection" placeholder="Например 1, A или Мезонин"/></label>
      <label>Проход (необязательно)<input id="locationAisle" placeholder="Например A или 1"/></label>
    </div>
    <div id="locationRackFields" class="location-builder-fields">
      <div class="location-step"><span>3</span><div><b>Стеллаж, этажи и ячейки</b><small>Секция → проход → стеллаж → этаж → ячейка</small></div></div>
      <label>Название или номер стеллажа<input id="locationRack" placeholder="Например 14"/></label>
      <div class="location-range"><label>Этажи от<input id="locationFloorFrom" type="number" min="1" max="30" value="1"/></label><label>до<input id="locationFloorTo" type="number" min="1" max="30" value="1"/></label></div>
      <div class="location-range"><label>Ячейки от<input id="locationCellFrom" type="number" min="1" max="99999" value="1"/></label><label>до<input id="locationCellTo" type="number" min="1" max="99999" value="23"/></label></div>
    </div>
    <div id="locationDirectCellFields" class="location-builder-fields hidden">
      <div class="location-step"><span>3</span><div><b>Отдельные ячейки</b><small>Для склада без стеллажей и этажей</small></div></div>
      <div class="location-range"><label>Ячейки от<input id="locationDirectCellFrom" type="number" min="1" max="99999" value="1"/></label><label>до<input id="locationDirectCellTo" type="number" min="1" max="99999" value="20"/></label></div>
    </div>
    <div id="locationPalletPlaceFields" class="location-builder-fields hidden">
      <div class="location-step"><span>3</span><div><b>Палеточные места</b><small>Фиксированные места на полу/в зоне, куда ставятся палеты или короба</small></div></div>
      <div class="location-range"><label>Места от<input id="locationPalletPlaceFrom" type="number" min="1" max="99999" value="1"/></label><label>до<input id="locationPalletPlaceTo" type="number" min="1" max="99999" value="20"/></label></div>
      <button id="skipPalletPlaceNumberBtn" type="button" class="secondary">Пропустить этот номер</button>
    </div>
    <div id="locationPalletFields" class="location-builder-fields hidden">
      <div class="location-step"><span>3</span><div><b>Палеты</b><small>Сохраняет прежний тип PALLET; подходит для структурной Excel-приёмки</small></div></div>
      <div class="location-range"><label>Палеты от<input id="locationPalletFrom" type="number" min="1" max="99999" value="1"/></label><label>до<input id="locationPalletTo" type="number" min="1" max="99999" value="1"/></label></div>
      <button id="skipPalletNumberBtn" type="button" class="secondary">Пропустить этот номер</button>
    </div>
    <div id="locationSectionFields" class="location-builder-fields hidden">
      <div class="location-step"><span>2</span><div><b>Секции / зоны</b><small>Можно создать одну именованную секцию или числовой диапазон</small></div></div>
      <label>Одна секция<input id="locationSectionName" placeholder="Например A, Холодная зона или Мезонин"/></label>
      <div class="location-range"><label>Или секции от<input id="locationSectionFrom" type="number" min="1" max="99999" placeholder="1"/></label><label>до<input id="locationSectionTo" type="number" min="1" max="99999" placeholder="4"/></label></div>
    </div>
    <div class="location-auto-print"><span><b>Печать QR только вручную</b><small>Создание ячеек больше не запускает принтер. После создания выберите нужные адреса и нажмите «Печать выбранных», «Печать диапазона» или «Напечатать созданные».</small></span></div>
    <button id="locationBuilderSubmit" class="primary" type="submit">Создать ячейки</button>
    <div id="locationBuilderResult" class="result-box hidden"></div>`;
  if(!$('locationPrintToolbar'))form.insertAdjacentHTML('afterend',`<div id="locationPrintToolbar" class="location-print-toolbar"><div><b>QR-этикетки и удаление адресов</b><small>Выберите ячейки, палеты или палеточные места галочками. Пустые адреса можно удалить массово; занятые будут пропущены.</small></div><div class="location-range"><label>От<input id="locationPrintFrom" list="locationCodesList" placeholder="Начальный адрес"/></label><label>До<input id="locationPrintTo" list="locationCodesList" placeholder="Конечный адрес"/></label></div><datalist id="locationCodesList"></datalist><div class="button-row"><button id="printSelectedLocations" type="button" class="secondary">Печать выбранных</button><button id="printLocationRange" type="button" class="secondary">Печать диапазона</button><button id="printAllLocations" type="button" class="primary">Печать всех</button><button id="deleteSelectedLocations" type="button" class="danger">Удалить выбранные пустые</button></div></div>`);
  if(!document.getElementById('locationBuilderStyles')){const style=document.createElement('style');style.id='locationBuilderStyles';style.textContent='.location-builder-fields{display:grid;gap:10px;padding:12px;border:1px solid #dfe6ef;border-radius:12px;background:#f8fafc}.location-card-head{display:flex;justify-content:space-between;align-items:center;gap:8px}.location-card>small{display:block;margin-top:4px}.location-builder-fields.hidden{display:none!important}.location-step{display:flex;align-items:center;gap:10px;margin-top:8px}.location-step>span{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:#5b4ad1;color:#fff;font-weight:800}.location-step div{display:grid}.location-step small{color:#667085}.location-range{display:grid;grid-template-columns:repeat(2,minmax(120px,1fr));gap:10px}.location-auto-print{display:flex!important;align-items:flex-start!important;gap:10px;padding:12px;border:1px solid #c7d7fe;border-radius:12px;background:#eef4ff}.location-auto-print input{width:auto!important;margin-top:3px}.location-auto-print span{display:grid;gap:3px}.location-auto-print small{color:#475467}.location-print-toolbar{display:grid;gap:10px;margin:14px 0;padding:14px;border:1px solid #d9dfea;border-radius:14px;background:#fff}.location-print-toolbar>div:first-child{display:grid}.location-card-head .location-select{display:flex;align-items:center;gap:5px;font-size:12px}.location-card.selected{outline:2px solid #6958dc;background:#f6f4ff}.location-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}.fc-location-modal{position:fixed;inset:0;z-index:10020;display:grid;place-items:center;padding:20px;background:rgba(16,24,40,.55)}.fc-location-modal.hidden{display:none}.fc-location-modal-card{position:relative;width:min(1000px,96vw);max-height:88vh;overflow:auto;padding:24px;border-radius:16px;background:#fff;box-shadow:0 24px 70px rgba(0,0,0,.25)}.fc-location-modal-close{position:absolute;right:14px;top:12px;border:0;background:transparent;font-size:28px;cursor:pointer}.location-content-summary{display:flex;gap:10px;margin:12px 0}.location-content-summary b{padding:8px 12px;border-radius:10px;background:#f2f4f7}.inventory-availability-filter{display:inline-grid;gap:4px;margin-left:10px}.inventory-availability-filter span{font-size:12px;color:#667085}@media(max-width:700px){.location-range{grid-template-columns:1fr}}';document.head.appendChild(style);}
  const labels={RACK:'Создать ячейки стеллажа',CELL:'Создать ячейки',PALLET_PLACE:'Создать палеточные места',PALLET:'Создать палеты',SECTION:'Создать секции'};
  const sync=()=>{const kind=$('locationStructureType').value;for(const [id,type] of [['locationRackFields','RACK'],['locationDirectCellFields','CELL'],['locationPalletPlaceFields','PALLET_PLACE'],['locationPalletFields','PALLET'],['locationSectionFields','SECTION']])$(id).classList.toggle('hidden',kind!==type);$('locationParentFields').classList.toggle('hidden',kind==='SECTION');$('locationBuilderSubmit').textContent=labels[kind]||'Создать адреса';};
  $('locationStructureType').addEventListener('change',sync);sync();
  const skipRange=(fromId,toId,label)=>{const from=$(fromId),to=$(toId),next=Math.max(Number(from.value||0),Number(to.value||0))+1;from.value=String(next);to.value=String(next);from.focus();toast(`${label} пропущен. Следующий: ${next}`);};
  $('skipPalletNumberBtn').addEventListener('click',()=>skipRange('locationPalletFrom','locationPalletTo','Номер палеты'));
  $('skipPalletPlaceNumberBtn').addEventListener('click',()=>skipRange('locationPalletPlaceFrom','locationPalletPlaceTo','Номер палеточного места'));
  $('printSelectedLocations').addEventListener('click',()=>printLocationLabels({ids:[...selectedLocationIds]}));
  $('deleteSelectedLocations').addEventListener('click',()=>deleteSelectedEmptyLocations().catch(e=>toast(e.message,true)));
  $('printLocationRange').addEventListener('click',()=>printLocationLabels({from:$('locationPrintFrom').value.trim(),to:$('locationPrintTo').value.trim()}));
  $('printAllLocations').addEventListener('click',()=>{const count=physicalLocations().length;if(!count)return toast('На складе пока нет физических адресов для печати',true);if(confirm(`Отправить в Print Agent все QR-этикетки физических адресов склада (${count} шт.)?`))printLocationLabels({ids:physicalLocations().map(x=>x.id)});});
  form.addEventListener('submit',async e=>{e.preventDefault();try{
    await ensureContext();const kind=$('locationStructureType').value,body={warehouse_id:state.warehouseId,kind};
    if(kind==='SECTION'){
      const name=$('locationSectionName').value.trim(),from=$('locationSectionFrom').value.trim(),to=$('locationSectionTo').value.trim();if(name)body.section=name;if(from||to){body.section_from=Number(from||to);body.section_to=Number(to||from);}if(!name&&!from&&!to)throw new Error('Укажите название секции или диапазон секций');
    }else{
      body.section=$('locationSection').value.trim()||null;body.aisle=$('locationAisle').value.trim()||null;
      if(kind==='RACK'){body.rack=$('locationRack').value.trim();body.floor_from=Number($('locationFloorFrom').value);body.floor_to=Number($('locationFloorTo').value);body.cell_from=Number($('locationCellFrom').value);body.cell_to=Number($('locationCellTo').value);if(!body.rack)throw new Error('Укажите стеллаж');}
      else if(kind==='CELL'){body.cell_from=Number($('locationDirectCellFrom').value);body.cell_to=Number($('locationDirectCellTo').value);}
      else if(kind==='PALLET_PLACE'){body.pallet_place_from=Number($('locationPalletPlaceFrom').value);body.pallet_place_to=Number($('locationPalletPlaceTo').value);}
      else if(kind==='PALLET'){body.pallet_from=Number($('locationPalletFrom').value);body.pallet_to=Number($('locationPalletTo').value);}
    }
    const btn=$('locationBuilderSubmit');btn.disabled=true;btn.textContent='Создаём…';const r=await api('/layout/locations/bulk',{method:'POST',body:JSON.stringify(body)});await loadContextData();
    // FC_LOCATION_PRINT_MANUAL_ONLY_09415B_20260903
    lastCreatedLocationIds=(r.locations||[]).map(x=>x.id);const result=$('locationBuilderResult'),sectionNote=kind!=='SECTION'&&r.created_sections?`Автоматически создано секций: ${r.created_sections}. `:'';result.classList.remove('hidden');result.innerHTML=`<b>Создано: ${r.created}</b><span>${sectionNote}${r.skipped?`Уже существовало и пропущено: ${r.skipped}. `:''}Адреса готовы. Печать автоматически не запускается.</span>${lastCreatedLocationIds.length?'<button id="printCreatedLocations" type="button" class="secondary">Напечатать созданные</button>':''}`;
    $('printCreatedLocations')?.addEventListener('click',()=>printLocationLabels({ids:lastCreatedLocationIds}));
    if(kind==='PALLET'){const next=Number(body.pallet_to)+1;$('locationPalletFrom').value=String(next);$('locationPalletTo').value=String(next);}else if(kind==='PALLET_PLACE'){const next=Number(body.pallet_place_to)+1;$('locationPalletPlaceFrom').value=String(next);$('locationPalletPlaceTo').value=String(next);}toast(`${labels[kind]||'Адреса'}: ${r.created}`);
    btn.disabled=false;sync();
  }catch(err){const btn=$('locationBuilderSubmit');if(btn)btn.disabled=false;sync();toast(err.message,true)}});
}
installLocationStructureBuilder();
$('skuForm').addEventListener('submit',async e=>{e.preventDefault();try{await ensureContext();await api('/masterdata/skus',{method:'POST',body:JSON.stringify({client_id:state.clientId,sku_code:$('skuCode').value.trim(),article:$('skuArticle').value.trim()||null,name:$('skuName').value.trim(),size:$('skuSize').value.trim()||null,barcode:$('skuBarcode').value.trim()||null,unit:'PCS'})});e.target.reset();await loadContextData();toast('Товар добавлен');}catch(err){toast(err.message,true)}});

// FC_R106_MASTERDATA_PICKING: one administrative place for external warehouse picking.
function r106PickStorageKey(){return `fc:r106:pick-number:${state.clientId||'none'}:${state.warehouseId||'none'}`;}
function installR106MasterdataWorkspace(){
  const page=$('page-masterdata'),grid=page?.querySelector('.master-grid');if(!page||!grid)return;
  const loc=$('locationForm'),sku=$('skuForm');if(loc)loc.hidden=true;if(sku)sku.hidden=true;
  if(!document.getElementById('r106MasterdataStyle')){const st=document.createElement('style');st.id='r106MasterdataStyle';st.textContent='#locationForm,#skuForm,[data-external-pick-upload],[data-external-pick-template],[data-external-pick-cancel-last],[data-external-pick-file],[data-r65-go="fbspicks"]{display:none!important}.r106-pick-panel{grid-column:1/-1}.r106-pick-row{display:grid;grid-template-columns:minmax(220px,.7fr) minmax(280px,1.3fr);gap:14px}.r106-pick-row .button-row{align-items:end}.r106-warehouse-danger{margin-top:10px}.r106-pick-status{margin-top:10px;color:#667085}@media(max-width:800px){.r106-pick-row{grid-template-columns:1fr}}';document.head.appendChild(st);}
  if(!$('r106PickingPanel')){
    const panel=document.createElement('section');panel.id='r106PickingPanel';panel.className='panel compact-form r106-pick-panel';panel.innerHTML=`<span class="kicker">СБОРКА / ПОСТАВКА</span><h3>Сборочные листы</h3><p>Введите номер поставки или внутреннего задания, затем загрузите Excel с колонками <b>ШК</b> и <b>Количество</b>. FulfillCore спишет только свободный товар, не затронет резерв заказов и скачает итоговый Excel: «Факт» с ячейками/коробами, «Не хватило» и «Не обработано WB».</p><div class="r106-pick-row"><label>Поставка / № сборочного задания<input id="r106PickNumber" placeholder="Например: 4534" autocomplete="off"></label><div class="button-row"><button type="button" class="primary" id="r106PickUpload">Загрузить Excel и сформировать</button><button type="button" class="secondary" id="r106PickTemplate">Скачать шаблон</button><button type="button" class="secondary" id="r106PickCancel">Отменить последнее</button><input type="file" id="r106PickFile" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden></div></div><div id="r106PickStatus" class="r106-pick-status">После обработки готовый файл сам скачивается и показывает, из каких ячеек и коробов взять товар.</div>`;grid.appendChild(panel);
    const number=$('r106PickNumber');number.value=localStorage.getItem(r106PickStorageKey())||'';number.addEventListener('input',()=>localStorage.setItem(r106PickStorageKey(),number.value.trim()));
    $('r106PickTemplate').onclick=()=>downloadExternalFbsPickTemplate();
    $('r106PickCancel').onclick=()=>cancelLastExternalFbsPick().catch(err=>toast(err.message,true));
    $('r106PickUpload').onclick=()=>{const n=number.value.trim();if(!n){toast('Введите номер поставки / сборочного задания',true);number.focus();return;}$('r106PickFile').click();};
    $('r106PickFile').addEventListener('change',async e=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;const n=number.value.trim();if(!n){toast('Введите номер поставки / сборочного задания',true);return;}const st=$('r106PickStatus');st.textContent=`Обрабатываем задание №${n}…`;try{const r=await uploadExternalFbsPickFile(file,n);if(r)st.textContent=`Готово: №${r.task_number||n} · списано ${fmtQty(r.allocated_units||0)} шт. · не хватило ${fmtQty(r.shortage_units||0)} шт. Итоговый Excel скачан.`;}catch(err){st.textContent='Ошибка: '+err.message;toast(err.message,true);}});
  }
  const whForm=$('warehouseForm');if(whForm&&!$('r106DeleteWarehouse')){const btn=document.createElement('button');btn.id='r106DeleteWarehouse';btn.type='button';btn.className='danger r106-warehouse-danger';btn.textContent='Удалить выбранный склад';btn.onclick=async()=>{const current=state.warehouses.find(x=>String(x.id)===String(state.warehouseId));if(!current){toast('Сначала выберите склад',true);return;}if(!confirm(`Удалить склад «${current.name}» из активного списка?\n\nИстория, приёмки и складские операции НЕ удаляются. Склад будет архивирован, чтобы старые документы не потеряли связь.`))return;try{await api(`/r106/warehouses/${encodeURIComponent(current.id)}`,{method:'DELETE'});localStorage.removeItem('fc_warehouse');state.warehouses=state.warehouses.filter(x=>String(x.id)!==String(current.id));await loadContexts();toast(`Склад «${current.name}» удалён из активного списка. История сохранена.`);}catch(err){toast(err.message,true);}};whForm.appendChild(btn);}
}
installR106MasterdataWorkspace();


async function loadRecentBulkReceivings(){
  const root=$('recentBulkReceivings');
  if(!root||!state.clientId||!state.warehouseId)return;
  const contextKey=`${normalizeContextId(state.clientId)}|${normalizeContextId(state.warehouseId)}`;
  try{
    const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId,limit:'12'});
    const data=await api(`/receiving/bulk/recent?${q.toString()}`,{timeoutMs:15000});
    if(contextKey!==`${normalizeContextId(state.clientId)}|${normalizeContextId(state.warehouseId)}`)return;
    const rows=data.rows||[];
    root.innerHTML=rows.length?rows.map(row=>{
      const active=String(row.status||'').toUpperCase()==='ACTIVE';
      const when=row.created_at?new Date(row.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'—';
      return `<div class="bulk-receipt-history-row ${active?'':'cancelled'}">
        <div><b>${when}</b><small>${esc(row.reference||'Без комментария')} · batch ${esc(row.batch_id)}</small></div>
        <div><b>${fmtQty(row.units||0)} шт.</b><small>${Number(row.rows||0)} строк</small></div>
        <div><span class="badge">${active?'Активна':'Отменена'}</span></div>
        <div>${active?`<button type="button" class="danger mini" data-cancel-bulk-batch="${esc(row.batch_id)}">Отменить приёмку</button>`:''}</div>
      </div>`;
    }).join(''):'<div class="empty-state">Массовых Excel-приёмок пока нет.</div>';
  }catch(err){
    root.innerHTML=`<div class="warn-box">Не удалось загрузить историю приёмок: ${esc(err.message)}</div>`;
  }
}
function installBulkReceivingHistoryUi(){
  const page=$('page-bulkimport');if(!page||$('bulkReceivingHistoryPanel'))return;
  const panel=document.createElement('div');
  panel.id='bulkReceivingHistoryPanel';panel.className='panel bulk-receiving-history-panel';
  panel.innerHTML=`<div class="panel-head"><div><span class="kicker">ИСТОРИЯ EXCEL-ПРИЁМОК</span><h3>Последние массовые приёмки</h3><p>Если один и тот же файл был проведён несколько раз, отмените только лишние приёмки. Остальные складские операции не затрагиваются.</p></div><button type="button" class="secondary" id="refreshBulkReceivingHistory">Обновить</button></div><div id="recentBulkReceivings"><div class="empty-state">Загружаем…</div></div>`;
  page.appendChild(panel);
  $('refreshBulkReceivingHistory')?.addEventListener('click',()=>loadRecentBulkReceivings());
  panel.addEventListener('click',e=>{const b=e.target.closest('[data-cancel-bulk-batch]');if(b)cancelBulkReceivingBatch(b.dataset.cancelBulkBatch).then(loadRecentBulkReceivings).catch(err=>toast(err.message,true));});
  loadRecentBulkReceivings();
}
if(!document.getElementById('bulkReceivingHistoryStyles')){const style=document.createElement('style');style.id='bulkReceivingHistoryStyles';style.textContent='.bulk-receiving-history-panel{margin-top:18px}.bulk-receipt-history-row{display:grid;grid-template-columns:minmax(250px,1.5fr) minmax(120px,.5fr) 110px 160px;gap:12px;align-items:center;padding:12px 0;border-bottom:1px solid #e5e7eb}.bulk-receipt-history-row small{display:block;color:#667085;margin-top:3px}.bulk-receipt-history-row.cancelled{opacity:.65}@media(max-width:900px){.bulk-receipt-history-row{grid-template-columns:1fr 1fr}}';document.head.appendChild(style);}


// Массовая приёмка: сначала только локальный склад; WB — отдельной кнопкой.
$('previewExcelBtn').addEventListener('click',()=>{try{const file=$('excelFile').files[0];if(!file)throw new Error('Выберите Excel-файл');const f=$('bulkReceiveFormat').value;$('excelPreview').innerHTML=f==='ADDRESS5'?'<div class="success-box"><b>Формат 5 колонок</b><br>1 — короб · 2 — ШК · 3 — количество · 4 — артикул · 5 — размер.<br>Каждое значение первого столбца создаётся как настоящий короб в зоне ПРИЕМКА. Короб сразу виден в сборочном листе, его можно переименовать или переместить в ячейку.</div>':'<div class="success-box"><b>Формат 2 колонки</b><br>1 — ШК · 2 — количество.<br>Товар будет принят без адреса в системную ячейку БЕЗ-АДРЕСА.</div>'; }catch(e){toast(e.message,true)}});
async function cancelBulkReceivingBatch(batchId){await ensureContext();if(!batchId)throw new Error('Номер массовой приёмки не найден');if(!confirm('Удалить эту массовую приёмку?\n\nТовар будет списан обратно со склада. История останется. Если часть товара уже отгружена, операция будет заблокирована.'))return;const syncWb=confirm('Если эта приёмка уже отправлялась на WB, пересчитать WB FBS после удаления?\n\nОК — пересчитать WB. Отмена — изменить только склад FulfillCore.');const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId});const r=await api(`/receiving/bulk/${encodeURIComponent(batchId)}/cancel?${q.toString()}`,{method:'POST',body:JSON.stringify({sync_wb:syncWb,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null}),timeoutMs:120000});reconcilePendingWbAfterCancellation(r);await loadContextData();state.inventoryCatalogLoaded=false;try{await loadInventoryCatalog();}catch(_){}try{await loadRecentBulkReceivings();}catch(_){}toast(`Массовая приёмка удалена: ${r.cancelled_operations||0} операций${r.wb_failed?` · ошибок WB: ${r.wb_failed}`:''}`,Boolean(r.wb_failed));}
$('excelPreview')?.addEventListener('click',async e=>{
  const cancel=e.target.closest('[data-cancel-bulk-batch]');
  const errorsExcel=e.target.closest('[data-bulk-errors-excel]');
  const publish=e.target.closest('[data-bulk-publish-wb]');
  try{
    if(cancel){await cancelBulkReceivingBatch(cancel.dataset.cancelBulkBatch);return;}
    if(errorsExcel){
      const id=errorsExcel.dataset.bulkErrorsExcel;
      const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId});
      const blob=await apiBlob(`/receiving/bulk/${encodeURIComponent(id)}/errors.xlsx?${q.toString()}`,{timeoutMs:120000});
      saveBlob(blob,`Ошибки_приёмки_${id}.xlsx`);
      toast('Excel ошибок скачан');
      return;
    }
    if(publish){
      const id=publish.dataset.bulkPublishWb;
      if(!confirm('Отправить текущие остатки этой приёмки на WB FBS?\n\nЛокальный склад уже принят и не будет изменён.'))return;
      publish.disabled=true;publish.textContent='Отправляем на WB…';
      const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId});
      const detail=await api(`/receiving/bulk/${encodeURIComponent(id)}/detail?${q.toString()}`,{timeoutMs:30000});
      const result=await publishFreshReceivingLines(detail.pending_lines||[],'Массовая приёмка');
      if(result?.failed){
        toast(`WB обновлён частично: ошибок ${result.failed}. Скачайте Excel ошибок.`,true);
        publish.textContent='Повторить отправку на WB FBS';
      }else{
        toast('WB FBS успешно обновлён');
        publish.textContent='WB FBS обновлён';
      }
      publish.disabled=false;
      return;
    }
  }catch(err){
    if(publish){publish.disabled=false;publish.textContent='Принять на остатки WB FBS';}
    toast(err.message,true);
  }
});
$('executeExcelBtn').addEventListener('click',async()=>{
  const button=$('executeExcelBtn');
  try{
    await ensureContext();
    const file=$('excelFile').files[0];
    if(!file)throw new Error('Выберите Excel-файл');
    const format=$('bulkReceiveFormat').value;
    const bulkStockClass=$('bulkReceiveStockClass')?.value==='MAIN'?'MAIN':'FBS';
    if(!confirm(`Добавить весь проверенный Excel ${bulkStockClass==='FBS'?'СРАЗУ В FBS':'на Основной склад'}?\n\nДо подтверждения склад не изменится. WB публикуется отдельным шагом.`))return;

    if(button){button.disabled=true;button.textContent='Оприходуем… не закрывайте страницу';}
    toast('Приёмка запущена. Большой Excel может обрабатываться несколько минут.');

    const fd=new FormData();
    fd.append('client_id',state.clientId);
    fd.append('warehouse_id',state.warehouseId);
    fd.append('format',format);
    fd.append('performed_by',$('importBy').value.trim());
    fd.append('reference',$('importReference').value.trim());
    const wid=Number(state.wbWarehouseFilter||0);
    if(wid)fd.append('wb_warehouse_id',String(wid));
    fd.append('stock_class',bulkStockClass);
    fd.append('file',file);

    const r=await api('/receiving/bulk',{method:'POST',body:fd,timeoutMs:600000});
    toast(`Локальный склад оприходован: ${r.rows_imported||0} строк.`);

    // 0.8.0: bulk Excel receiving is always LOCAL first.
    // WB FBS is published only after an explicit click on the button in this same result card.
    const publishNow=false;
    let wbResult=null;
    if(r.pending_lines?.length)addPendingWbReceiving(r.pending_lines);

    await loadContextData();
    state.inventoryCatalogLoaded=false;
    try{await loadInventoryCatalog();}catch(_){}

    const warningHtml=r.warning_count?`<div class="warn-box"><b>Предупреждений WB: ${r.warning_count}</b><br>Локальная приёмка завершена. Перед отправкой на WB скачайте Excel ошибок и проверьте проблемные позиции.</div>`:'';
    const acceptedToFbs=String(r.stock_class||bulkStockClass).toUpperCase()==='FBS';
    $('excelPreview').innerHTML=`<div class="success-box"><b>Импорт завершён.</b><br>Принято строк: ${r.rows_imported}. Создано новых коробов: ${r.created_containers||0}. Ошибок строк: ${r.rows_failed}.<br><b>${acceptedToFbs?'Товар сразу добавлен в локальный FBS. Основной склад не использовался.':'Товар добавлен на Основной склад.'}</b><br><b>${acceptedToFbs?'WB FBS пока НЕ изменён — ниже можно отправить фактический остаток.':'WB не изменён.'}</b>${r.batch_id?`<div class="button-row" style="margin-top:12px"><button type="button" class="secondary" data-bulk-errors-excel="${esc(r.batch_id)}">Скачать Excel ошибок</button>${acceptedToFbs?`<button type="button" class="primary" data-bulk-publish-wb="${esc(r.batch_id)}">Обновить WB FBS</button>`:''}<button type="button" class="danger" data-cancel-bulk-batch="${esc(r.batch_id)}">Удалить эту массовую приёмку</button></div>`:''}</div>${warningHtml}${r.errors?.length?`<div class="error-box">${r.errors.map(x=>`строка ${x.row}: ${esc(x.error)}`).join('<br>')}</div>`:''}`;
    renderPendingWbReceiving();
    await loadRecentBulkReceivings();state.bulkReceivingHistoryLoaded=false;
    toast(`Приёмка завершена: ${r.rows_imported||0} строк${r.rows_failed?`, ошибок ${r.rows_failed}`:''}.`,Boolean(r.rows_failed));
  }catch(e){
    toast(`Приёмка не завершена: ${e.message}`,true);
  }finally{
    if(button){button.disabled=false;button.textContent='Добавить всё на остатки';}
  }
});

// Marketplace credentials
$('marketProvider').addEventListener('change',()=>{$('ozonClientIdLabel').classList.toggle('hidden',$('marketProvider').value!=='OZON');});
async function loadMarketplaceCredentials(render=true){
  if(!state.clientId){state.marketCredentials=[];if(render)renderMarketCredentials();return;}
  state.marketCredentials=await api(`/marketplaces/credentials?client_id=${encodeURIComponent(state.clientId)}`);if(render)renderMarketCredentials();else renderMarketCredentials();
}
function renderMarketCredentials(){
  $('marketCredentials').innerHTML=state.marketCredentials.length?state.marketCredentials.map(c=>`<div class="credential-row"><div><b>${c.provider==='WB'?'Wildberries':'Ozon'} · ${esc(c.name)}</b><small>${c.last_tested_at?(c.last_test_ok?'✓ подключение проверено':'⚠ последняя проверка с ошибкой'):'ещё не проверено'}${c.last_test_message?` · ${esc(c.last_test_message)}`:''}</small></div><div class="row-actions"><button class="mini secondary" data-cred-test="${c.id}">Проверить</button><button class="mini danger" data-cred-delete="${c.id}">Отключить API</button></div></div>`).join(''):'<div class="empty-state">Подключений пока нет.</div>';
  const wb=state.marketCredentials.filter(c=>c.provider==='WB'),oz=state.marketCredentials.filter(c=>c.provider==='OZON');
  $('wbCredentialSelect').innerHTML=wb.length?wb.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join(''):'<option value="">Нет WB</option>';
  $('ozonCredentialSelect').innerHTML=oz.length?oz.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join(''):'<option value="">Нет Ozon</option>';
  if($('wbProductCredentialSelect')) $('wbProductCredentialSelect').innerHTML=wb.length?wb.map(c=>`<option value="${c.id}">${esc(c.name)}</option>`).join(''):'<option value="">Нет WB</option>';
  const saved=wb.find(c=>c.id===state.wbCredentialId)?.id||wb[0]?.id||'';
  if(saved){state.wbCredentialId=saved;$('wbCredentialSelect').value=saved;fcR20Remember('fc_wb_credential',saved);}
  const productSaved=wb.find(c=>c.id===state.wbProductCredentialId)?.id||saved;
  if(productSaved&&$('wbProductCredentialSelect')){state.wbProductCredentialId=productSaved;$('wbProductCredentialSelect').value=productSaved;fcR20Remember('fc_wb_product_credential',productSaved);}
  const ozonSaved=oz.find(c=>c.id===state.ozonCredentialId)?.id||oz[0]?.id||'';
  if(ozonSaved){state.ozonCredentialId=ozonSaved;$('ozonCredentialSelect').value=ozonSaved;fcR20Remember('fc_ozon_credential',ozonSaved);}
}
$('marketCredentialForm').addEventListener('submit',async e=>{e.preventDefault();try{
  await ensureContext();const provider=$('marketProvider').value;await api('/marketplaces/credentials',{method:'POST',body:JSON.stringify({client_id:state.clientId,provider,name:$('marketName').value.trim(),api_key:$('marketApiKey').value.trim(),external_client_id:provider==='OZON'?$('marketClientId').value.trim():null})});e.target.reset();$('marketProvider').value='WB';$('ozonClientIdLabel').classList.add('hidden');await loadMarketplaceCredentials();toast('Подключение сохранено. Теперь нажмите «Проверить».');
}catch(err){toast(err.message,true)}});
$('marketCredentials').addEventListener('click',async e=>{
  const t=e.target.closest('[data-cred-test]'),d=e.target.closest('[data-cred-delete]');
  try{if(t){const r=await api(`/marketplaces/credentials/${t.dataset.credTest}/test`,{method:'POST'});toast(r.message);await loadMarketplaceCredentials();}if(d){if(!confirm('Разорвать связь с API? Заказы и история останутся в FulfillCore, а кабинет можно будет подключить заново с тем же названием.'))return;const r=await api(`/marketplaces/credentials/${d.dataset.credDelete}`,{method:'DELETE'});await loadMarketplaceCredentials();toast(r.message||'API-связь отключена');}}catch(err){await loadMarketplaceCredentials().catch(()=>{});toast(err.message,true)}
});
async function loadWbWarehouses(refresh=false){
  try{
    const cred=wbCredential();
    if(!cred){state.wbWarehouses=[];renderWbWarehouses();return;}
    await ensureContext();const selectedBefore=state.wbWarehouseFilter;
    const qp=new URLSearchParams({credential_id:cred,warehouse_id:state.warehouseId,refresh:refresh?'true':'false'});
    const r=await api(`/marketplaces/wb/warehouses?${qp.toString()}`);
    const rows=r.warehouses||[];
    if(rows.length||!state.wbWarehouses.length)state.wbWarehouses=rows;
    state.wbWarehouseFilter=selectedBefore;renderWbWarehouses();
    if(r.warning)toast(r.warning);
  }catch(e){toast(e.message,true)}
}
function refreshWbWarehouseSelector(){
  const sel=$('wbWarehouseView');if(!sel)return;
  const visible=state.wbWarehouses.filter(w=>['WORK','WATCH'].includes(w.mode));
  if(!visible.length){sel.innerHTML='<option value="">Выберите склад WB</option>';state.wbWarehouseFilter='';return;}
  let selected=state.wbWarehouseFilter;
  if(!visible.some(w=>String(w.id)===String(selected))){selected=String((visible.find(w=>w.mode==='WORK')||visible[0]).id);state.wbWarehouseFilter=selected;saveScopedWbWarehouseFilter();}
  sel.innerHTML=visible.map(w=>`<option value="${esc(w.id)}">${esc(w.name)} · ${w.mode==='WORK'?'Работа':'Наблюдение'}</option>`).join('');
  sel.value=selected;
  const wh=visible.find(w=>String(w.id)===String(selected));
  if($('wbSupplyWarehouseName'))$('wbSupplyWarehouseName').textContent=wh?`· ${wh.name}`:'';
}
function renderWbWarehouses(){
  const el=$('wbWarehouseSettings');if(!el)return;
  const physical=state.warehouses.find(x=>x.id===state.warehouseId)?.name||'не выбран';
  const context=`<div class="result-box"><b>Физический склад: ${esc(physical)}</b><span>Выбор ниже действует только для этого склада. Настройки другого физического склада не изменятся.</span></div>`;
  if(!state.wbWarehouses.length){el.innerHTML=context+'<div class="empty-state">Склады WB не загружены.</div>';refreshWbWarehouseSelector();return;}
  el.innerHTML=context+state.wbWarehouses.map(w=>`<div class="credential-row wb-wh-row"><div><b>${esc(w.name)}</b><small>ID склада продавца: ${esc(w.id)}${w.office_id?` · склад WB: ${esc(w.office_id)}`:''}</small></div><select class="wb-wh-mode" data-wb-wh="${esc(w.id)}"><option value="WORK" ${w.mode==='WORK'?'selected':''}>Работа</option><option value="WATCH" ${w.mode==='WATCH'?'selected':''}>Наблюдение</option><option value="HIDDEN" ${w.mode==='HIDDEN'?'selected':''}>Не показывать</option></select></div>`).join('');
  refreshWbWarehouseSelector();
}
function applyWbWarehouseCollapse(){
  const body=$('wbWarehousePanelBody'),btn=$('wbWarehouseToggleBtn');
  if(!body||!btn)return;
  body.classList.toggle('hidden',state.wbWarehousesCollapsed);
  btn.textContent=state.wbWarehousesCollapsed?'Склады ▾':'Склады ▴';
  btn.setAttribute('aria-expanded',state.wbWarehousesCollapsed?'false':'true');
}
$('wbWarehouseToggleBtn')?.addEventListener('click',()=>{
  state.wbWarehousesCollapsed=!state.wbWarehousesCollapsed;
  fcR20Remember('fc_wb_wh_collapsed',state.wbWarehousesCollapsed?'1':'0');
  applyWbWarehouseCollapse();
});
applyWbWarehouseCollapse();

$('loadWbWarehousesBtn')?.addEventListener('click',()=>loadWbWarehouses(true));
async function diagnoseWbWarehouse(){try{const cred=wbCredential();const selectedWh=state.wbWarehouseFilter||$('wbWarehouseView')?.value||'';if(!cred||!selectedWh)throw new Error('Выберите кабинет и склад WB');const btn=$('diagnoseWbWarehouseBtn');btn.disabled=true;btn.textContent='Проверка…';const orderNo=($('wbOrderNumberFilter')?.value||'').trim();const qp=new URLSearchParams({credential_id:cred,warehouse_id:state.warehouseId,wb_warehouse_id:String(selectedWh),days:'30'});if(/^\d+$/.test(orderNo))qp.set('order_id',orderNo);const r=await api(`/marketplaces/wb/warehouse-diagnostics?${qp.toString()}`);const el=$('wbWarehouseDiagnostics');el.classList.remove('hidden');const fmt=o=>Object.entries(o||{}).map(([k,v])=>`${k}: ${v}`).join(', ')||'нет';const live=r.live_warehouse||{};const exact=r.exact_order;const exactHtml=exact?`<span class="${exact.found&&exact.matches_selected?'good':exact.found?'warn':'bad'}"><b>Заказ ${esc(exact.id||orderNo)}</b>: ${exact.found?`warehouseId ${esc(exact.warehouse_id??'—')} · officeID ${esc(exact.office_id??'—')} · cargoType ${esc(exact.cargo_type??'—')} · поставка ${esc(exact.supply_id||'—')} · совпадает с выбранным складом: ${exact.matches_selected?'ДА':'НЕТ'}`:'в истории за выбранный период не найден'}${exact.statuses?.length?` · статус WB: ${esc(JSON.stringify(exact.statuses[0]))}`:''}</span>`:'';el.innerHTML=`<b>Диагностика: ${esc(r.selected?.name||r.selected?.id)}</b><span>Сохранённый seller warehouseId: ${esc(r.selected?.id||'—')} · officeID: ${esc(r.selected?.office_id||'—')}</span><span>Сейчас в WB: ${live.id||live.warehouseId?`seller warehouseId ${esc(live.id||live.warehouseId)} · officeID ${esc(live.officeId??'—')} · cargoType ${esc(live.cargoType??'—')} · isProcessing ${esc(String(live.isProcessing??'—'))}`:'склад не найден в текущем списке WB'}</span><span>Новые WB: ${r.new?.rows||0}, совпало со складом: ${r.new?.matched||0}. warehouseId: ${esc(fmt(r.new?.warehouse_ids))}</span><span>История WB за последние ${r.history_scan_days||3} дн.: ${r.history?.rows||0}, совпало: ${esc(r.history?.matched||0)}. warehouseId: ${esc(fmt(r.history?.warehouse_ids))}</span><span>officeID в ответах: ${esc(fmt(r.history?.office_ids))}</span>${exactHtml}${!orderNo?'<span>Чтобы проверить конкретный заказ, введите его номер в поле «№ заказа» и снова нажмите «Диагностика склада».</span>':''}${(r.warnings||[]).length?`<span class="bad">${esc(r.warnings.join(' · '))}</span>`:''}`;}catch(e){toast(e.message,true)}finally{const btn=$('diagnoseWbWarehouseBtn');if(btn){btn.disabled=false;btn.textContent='Диагностика склада';}}}
$('diagnoseWbWarehouseBtn')?.addEventListener('click',diagnoseWbWarehouse);

$('saveWbWarehousesBtn')?.addEventListener('click',async()=>{try{
  const cred=wbCredential();if(!cred)throw new Error('Сначала выберите кабинет WB');await ensureContext();
  const items=[...document.querySelectorAll('.wb-wh-mode')].map(x=>({warehouse_id:Number(x.dataset.wbWh),mode:x.value}));
  if(!items.length)throw new Error('Сначала загрузите список складов WB');
  const btn=$('saveWbWarehousesBtn');if(btn)btn.disabled=true;
  const saved=await api(`/marketplaces/wb/warehouse-settings?warehouse_id=${encodeURIComponent(state.warehouseId)}`,{method:'PUT',body:JSON.stringify({credential_id:cred,items})});
  const modes=saved.warehouse_settings||{};state.wbWarehouses.forEach(w=>{const mode=modes[String(w.id)]?.mode;if(mode)w.mode=mode;});renderWbWarehouses();
  const physical=state.warehouses.find(x=>x.id===state.warehouseId)?.name||'выбранного физического склада';toast(`Настройки WB сохранены отдельно для склада «${physical}».`);
  await loadWbWarehouses(false);
  syncWbOrders({silent:true,force:false}).catch(e=>console.warn('WB sync after warehouse save:',e));
}catch(e){toast(e.message,true)}finally{const btn=$('saveWbWarehousesBtn');if(btn)btn.disabled=false;}});

function selectedValues(selector){
  if(selector==='.wb-order-check')return [...state.wbSelectedOrderIds];
  return [...document.querySelectorAll(selector)].filter(x=>x.checked).map(x=>x.value);
}
function wbCredential(){return $('wbCredentialSelect')?.value||state.wbCredentialId||'';}
const WB_COLUMN_DEFS=[
  ['status','Статус'],['order','№ заказа'],['warehouse','Склад WB'],['article','Артикул'],['name','Товар'],['size','Размер'],
  ['barcode','ШК товара'],['reserved','Резерв: ячейка / короб'],['stock','Весь остаток'],['supply','Поставка'],['chz','Код маркировки'],['label','Этикетка заказа'],['created','Возраст заказа'],['actions','Действия']
];
function wbColumnName(key){return WB_COLUMN_DEFS.find(x=>x[0]===key)?.[1]||key;}
function normalizeWbColumns(){
  const allowed=new Set(WB_COLUMN_DEFS.map(x=>x[0]));state.wbColumns=state.wbColumns.filter(x=>allowed.has(x));
  if(!state.wbColumns.length)state.wbColumns=['status','order','article','name','size','barcode','reserved','supply','chz','label','created','actions'];
}
function renderWbColumnsPanel(){
  normalizeWbColumns();const el=$('wbColumnsPanel');if(!el)return;
  const order=state.wbColumns.map((k,i)=>`<div class="wb-column-order-item"><span title="Столбец можно также перетащить за заголовок таблицы">↕ ${esc(wbColumnName(k))}</span><div><button type="button" class="mini secondary" data-wb-column-left="${k}" ${i===0?'disabled':''} title="Сдвинуть влево">←</button><button type="button" class="mini secondary" data-wb-column-right="${k}" ${i===state.wbColumns.length-1?'disabled':''} title="Сдвинуть вправо">→</button></div></div>`).join('');
  el.innerHTML=`<b>Порядок столбцов</b><small class="subline">Перетаскивайте заголовки таблицы мышью или используйте стрелки.</small><div class="wb-column-order-list">${order}</div><b>Какие данные показывать</b><div class="column-checks">${WB_COLUMN_DEFS.map(([k,n])=>`<label><input type="checkbox" data-wb-column="${k}" ${state.wbColumns.includes(k)?'checked':''}/> ${esc(n)}</label>`).join('')}</div><div class="button-row"><button type="button" class="mini secondary" data-wb-clear-column-filters>Сбросить поиск этикетки и ЧЗ</button></div>`;
}
function ensureWbPickingLayoutControl(){
  const btn=$('wbPickingListBtn');if(!btn)return null;
  let select=$('wbPickingLayoutMode');if(select)return select;
  const wrap=document.createElement('label');wrap.className='wb-picking-layout-control';wrap.style.cssText='display:inline-flex;align-items:center;gap:6px;white-space:nowrap;margin-right:8px;';
  const saved=String(localStorage.getItem('fc_wb_picking_layout')||'CELL').toUpperCase();
  wrap.innerHTML=`<span style="font-size:12px;font-weight:700">Сборочный лист:</span><select id="wbPickingLayoutMode" title="По ячейкам объединяет одинаковый товар в одной ячейке/коробе в одну строку. По времени показывает заказы строго от старых к новым."><option value="CELL">По ячейкам / коробам</option><option value="TIME">По времени заказа</option></select>`;
  btn.parentNode?.insertBefore(wrap,btn);select=$('wbPickingLayoutMode');if(select){select.value=saved==='TIME'?'TIME':'CELL';select.addEventListener('change',()=>fcR20Remember('fc_wb_picking_layout',select.value));}return select;
}
ensureWbPickingLayoutControl();
function wbSupplyIdsFromOrders(rows){
  return [...new Set((rows||[]).map(o=>String(o?.supply_id||'').trim()).filter(Boolean))];
}
function wbSupplySummary(rows){
  const counts=new Map();
  for(const row of (rows||[])){
    const id=String(row?.supply_id||'').trim();if(!id)continue;
    counts.set(id,(counts.get(id)||0)+1);
  }
  return [...counts.entries()].map(([id,count])=>({id,count}));
}
function normalizeWbDeliveredSupplyIds(values){
  return [...new Set((values||[]).map(x=>String((x&&typeof x==='object')?(x.id||x.supply_id||''):x||'').trim()).filter(Boolean))];
}
function ensureWbPackedSupplyPrintButton(){
  const ship=$('wbShipPackedBtn');if(!ship)return null;
  let btn=$('wbPrintPackedSupplyLabelsBtn');if(btn)return btn;
  btn=document.createElement('button');btn.type='button';btn.id='wbPrintPackedSupplyLabelsBtn';btn.className='secondary';btn.textContent='ШК поставок';
  btn.title='Распечатать по одному ШК для каждой уникальной поставки выбранных упакованных заказов';
  ship.parentNode?.insertBefore(btn,ship);
  btn.addEventListener('click',async()=>{try{
    const selected=selectedValues('.wb-order-check'),rows=state.wbOrders.filter(o=>selected.includes(String(o.external_order_id)));
    if(!rows.length)throw new Error('Сначала выберите упакованные заказы.');
    if(rows.some(o=>o.local_status!=='PACKED'))throw new Error('Для этой кнопки оставьте в выборе только упакованные заказы.');
    const missing=rows.filter(o=>!String(o.supply_id||'').trim());
    if(missing.length)throw new Error(`У ${missing.length} упакованных заказов нет поставки WB. Сначала синхронизируйте/привяжите их к поставке.`);
    const supplies=wbSupplySummary(rows);if(!supplies.length)throw new Error('В выбранных заказах нет поставок WB.');
    const preview=supplies.slice(0,20).map(x=>`${x.id} — ${x.count} зак.`).join('\n')+(supplies.length>20?`\n… ещё ${supplies.length-20}`:'');
    if(!confirm(`Распечатать ШК всех поставок из выбранных упакованных заказов?\n\nЗаказов: ${rows.length}\nПоставок: ${supplies.length}\n\n${preview}\n\nПоставка ещё не закрывается — печать сама по себе статус WB не меняет.`))return;
    btn.disabled=true;const old=btn.textContent;btn.textContent=`Печатаем ${supplies.length}…`;
    try{await printWbSupplyBarcodes(supplies.map(x=>x.id),{closed:false});}finally{btn.textContent=old;syncWbButtons();}
  }catch(e){toast(e.message,true)}});
  return btn;
}
async function printWbSupplyBarcodes(supplyIds,{closed=null}={}){
  const ids=[...new Set((supplyIds||[]).map(x=>String(x||'').trim()).filter(Boolean))];
  if(!ids.length)throw new Error('Нет поставок для печати.');
  const cred=wbCredential();if(!cred)throw new Error('Сначала выберите кабинет WB');
  if(closed===null)await loadWbSupplies(false,false).catch(()=>{});
  const ps=wbPrintSettings();const failed=[];
  for(const supplyId of ids){
    const supply=state.wbSupplies.find(x=>String(x.id)===String(supplyId));
    const isClosed=closed===true?true:(closed===false?false:!!supply?.done);
    const qs=`closed=${isClosed?'true':'false'}&width_mm=${ps.wbWidth}&height_mm=${ps.wbHeight}&scale_percent=${ps.wbScale}&offset_x_mm=${ps.wbOffsetX}&offset_y_mm=${ps.wbOffsetY}`;
    try{
      if(ps.mode==='agent'){
        await api(`/marketplaces/wb/supplies/${encodeURIComponent(supplyId)}/barcode/queue?${qs}`,{method:'POST',body:JSON.stringify({credential_id:cred,print_station:ps.station})});
      }else{
        const blob=await apiBlob(`/marketplaces/wb/supplies/${encodeURIComponent(supplyId)}/barcode.pdf?credential_id=${encodeURIComponent(cred)}&${qs}`);
        printBlob(blob,`WB_${supplyId}_ШК_поставки.pdf`);
        await new Promise(resolve=>setTimeout(resolve,650));
      }
    }catch(e){failed.push(`${supplyId}: ${e.message||e}`);}
  }
  if(failed.length)throw new Error(`Не удалось распечатать ${failed.length} из ${ids.length} поставок: ${failed.join(' · ')}`);
  toast(ps.mode==='agent'?`ШК поставок отправлены на ${ps.station}: ${ids.length}.`:`Открыта печать ШК поставок: ${ids.length}.`);
  return ids;
}
function syncWbButtons(){
  ensureWbPickingLayoutControl();
  ensureWbPackedSupplyPrintButton();
  const selected=selectedValues('.wb-order-check');
  const rows=state.wbOrders.filter(o=>selected.includes(String(o.external_order_id)));
  $('wbPrintLabelsBtn').disabled=!rows.length || rows.some(o=>!['PACKED','SENT'].includes(o.local_status));
  const createSupplyBtn=$('wbCreateSupplyBtn');if(createSupplyBtn){createSupplyBtn.disabled=rows.length>0&&rows.some(o=>o.local_status!=='NEW'||o.supply_id);createSupplyBtn.title=rows.length?'Создать поставку из выбранных новых заказов':'Создать пустую поставку WB для текущего склада';}
  const selectedCanPick=rows.length>0&&rows.every(o=>['NEW','PICKING'].includes(o.local_status));
  const allScopeCanPick=(['NEW','PICKING'].includes(state.wbOrderFilter)&&Number(state.wbTotal||0)>0)
    ||(!state.wbOrderFilter&&(Number(state.wbOrderCounts?.NEW||0)+Number(state.wbOrderCounts?.PICKING||0)>0));
  $('wbPickingListBtn').disabled=!(selectedCanPick||allScopeCanPick);
  $('wbShipPackedBtn').disabled=!rows.length || rows.some(o=>o.local_status!=='PACKED');
  const supplyPrintBtn=$('wbPrintPackedSupplyLabelsBtn');
  if(supplyPrintBtn){
    const packedOnly=rows.length>0&&rows.every(o=>o.local_status==='PACKED');
    const supplyIds=wbSupplyIdsFromOrders(rows),missing=rows.filter(o=>!String(o.supply_id||'').trim()).length;
    supplyPrintBtn.disabled=!packedOnly||!supplyIds.length||missing>0;
    supplyPrintBtn.textContent=supplyIds.length?`ШК поставок (${supplyIds.length})`:'ШК поставок';
    supplyPrintBtn.title=missing?`У ${missing} выбранных упакованных заказов нет поставки WB`:'Распечатать по одному ШК для каждой уникальной поставки выбранных упакованных заказов';
  }
  const statusSelect=$('wbBulkStatusSelect');
  if(statusSelect){
    statusSelect.disabled=!rows.length || rows.some(o=>['CANCELLED','DELIVERED'].includes(o.local_status));
    if(!rows.length)statusSelect.value='';
  }
}
function syncOzonButtons(){
  const selected=selectedValues('.ozon-posting-check'),selectedRows=state.ozonPostings.filter(p=>selected.includes(String(p.posting_number))),packedRows=state.ozonPostings.filter(p=>p.is_packed),ship=$('ozonShipPackedBtn');
  if($('ozonPrintLabelsBtn'))$('ozonPrintLabelsBtn').disabled=!selected.length;
  if(ship){const count=selected.length?selectedRows.filter(p=>p.is_packed).length:packedRows.length;ship.disabled=!count||(selected.length>0&&selectedRows.some(p=>!p.is_packed));ship.textContent=`Создать отгрузку из упакованных${count?` (${count})`:''}`;}
}
function wbStatusClass(status){return ({NEW:'status-new',PICKING:'status-picking',PACKED:'status-collected',SENT:'status-sent',DELIVERED:'status-delivered',CANCELLED:'status-cancelled'}[status]||'status-new');}
function releaseActiveWbPackingLease(){
  const order=state.wbActivePackingOrder,credential=state.wbActivePackingCredentialId||wbCredential(),warehouse=state.warehouseId,station=String(state.wbPrintStation||'PACK-01').trim().toUpperCase();
  if(!order?.id||!credential||!station)return;
  const q=new URLSearchParams({credential_id:String(credential),order_id:String(order.id),station_code:station,device_id:String(state.deviceId||'')});if(warehouse)q.set('warehouse_id',String(warehouse));
  api('/marketplaces/wb/packing/release-order?'+q.toString(),{method:'POST',timeoutMs:12000}).catch(e=>console.warn('packing lease release:',e));
}
function wbPackingFocusTarget(){
  const chz=$('wbPackChz'),product=$('wbPackProduct');
  if(state.wbActivePackingOrder&&chz&&!chz.disabled)return chz;
  return product&&!product.disabled?product:null;
}
function selectWbPackingProductForRetry(){
  const target=$('wbPackProduct');if(!target||target.disabled)return;
  requestAnimationFrame(()=>{try{target.focus({preventScroll:true});target.select();}catch(_){}});
}
function restoreWbPackingScannerFocus(force=false){
  if(state.activePage!=='marketplaces')return;
  const target=wbPackingFocusTarget();if(!target)return;
  const active=document.activeElement;
  const scanActive=active&&['wbPackProduct','wbPackChz'].includes(active.id);
  const otherInteractive=active&&['INPUT','TEXTAREA','SELECT','BUTTON'].includes(active.tagName)&&!scanActive;
  if(otherInteractive&&!force)return;
  requestAnimationFrame(()=>{
    const now=document.activeElement;
    const safe=force||!now||now===document.body||['wbPackProduct','wbPackChz'].includes(now.id);
    if(!safe)return;
    try{target.focus({preventScroll:true});const n=target.value?.length||0;if(target.setSelectionRange)target.setSelectionRange(n,n);}catch(e){}
  });
}
function mergeWbPackingOrderUpdate(order){
  if(!order?.id)return;
  const i=state.wbOrders.findIndex(x=>String(x.id)===String(order.id));
  if(i>=0){state.wbOrders[i]={...state.wbOrders[i],...order};renderWbOrderRows();}
}
function resetWbPacking(focus=true){
  releaseActiveWbPackingLease();
  state.wbActivePackingOrder=null;state.wbActivePackingCredentialId='';state.wbScanBusy=false;
  $('wbPackProduct').disabled=false;$('wbPackProduct').value='';$('wbPackChz').value='';$('wbPackChz').disabled=true;
  $('wbPackingProductLabelBtn').disabled=true;if($('wbPackingSkipBtn'))$('wbPackingSkipBtn').disabled=true;
  $('wbPackProductHint').textContent='Система выберет следующий заказ «На сборке» с этим товаром.';
  $('wbPackProductHint').className='';$('wbPackChzHint').textContent='Без кода маркировки заказ не считается упакованным.';$('wbPackChzHint').className='';
  $('wbPackingResult').innerHTML='<b>Текущий заказ</b><span>Отсканируйте штрихкод товара.</span>';
  if(focus)setTimeout(()=>restoreWbPackingScannerFocus(true),50);
}
function updateWbCounts(){
  const c={NEW:0,PICKING:0,PACKED:0,SENT:0,DELIVERED:0,CANCELLED:0,...(state.wbOrderCounts||{})};
  $('wbCountNew').textContent=c.NEW||0;$('wbCountPicking').textContent=c.PICKING||0;$('wbCountPacked').textContent=c.PACKED||0;
  $('wbCountSent').textContent=c.SENT||0;$('wbCountDelivered').textContent=c.DELIVERED||0;$('wbCountCancelled').textContent=c.CANCELLED||0;
  $('wbCountAll').textContent=Object.values(c).reduce((a,b)=>a+Number(b||0),0);
}
const WB_DEFAULT_WIDTHS={status:145,order:135,warehouse:205,article:125,name:270,size:90,barcode:210,reserved:235,stock:280,supply:150,chz:300,label:190,created:180,actions:230};
const WB_STATUS_SORT={NEW:0,PICKING:1,PACKED:2,SENT:3,DELIVERED:4,CANCELLED:5};
(function installWbBoardEnhancementStyles(){if(document.getElementById('wbBoardEnhancementStyles'))return;const style=document.createElement('style');style.id='wbBoardEnhancementStyles';style.textContent=`
  #wbOrdersHead th[data-drag-col]{cursor:grab}.wb-column-dragging{opacity:.45}.wb-th-sort{display:flex;align-items:center;justify-content:space-between;gap:7px;width:100%;padding:0 13px 0 0;border:0;background:transparent;color:inherit;font:inherit;font-weight:700;text-align:left;cursor:pointer}.wb-sort-mark{font-size:11px;opacity:.7}
  #wbOrdersHead .wb-filter-heading{padding:6px 7px;background:#f5f7fb}.wb-column-filter{width:100%;min-width:0;height:30px;padding:4px 7px;border:1px solid #d7deea;border-radius:7px;background:#fff;font:inherit;font-size:12px;line-height:1.2;color:#263238}.wb-column-filter:focus{outline:2px solid rgba(74,107,213,.22);border-color:#4a6bd5}
  .wb-order-age{display:inline-flex;padding:4px 8px;border-radius:999px;background:#eef2f7;font-weight:800}.wb-order-age.warn{background:#fff0c2;color:#805b00}.wb-order-age.old{background:#fde2e4;color:#b42318}.wb-order-row-warn>td{background:#fffaf0!important}.wb-order-row-old>td{background:#fff1f2!important}.wb-order-row-old:hover>td{background:#ffe4e6!important}
  .wb-column-order-list{display:grid;gap:6px;margin:9px 0 14px}.wb-column-order-item{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:6px 8px;border:1px solid #e1e6ef;border-radius:8px;background:#fff}.wb-column-order-item>span{font-size:13px;font-weight:650}.wb-column-order-item>div{display:flex;gap:4px}
  .wb-brand-filter{position:relative;display:flex;align-items:center;gap:8px;margin:10px 0 12px}.wb-brand-filter-toggle{display:flex;align-items:center;gap:8px;min-width:190px;justify-content:space-between}.wb-brand-filter-toggle b{max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.wb-brand-filter-menu{position:absolute;z-index:1005;top:calc(100% + 5px);left:0;width:min(360px,92vw);padding:12px;border:1px solid #d7deea;border-radius:12px;background:#fff;box-shadow:0 16px 38px rgba(20,30,55,.18)}.wb-brand-filter-list{display:grid;gap:4px;max-height:320px;overflow:auto;margin-bottom:10px}.wb-brand-filter-option{display:flex;align-items:center;gap:9px;padding:7px 8px;border-radius:8px;cursor:pointer}.wb-brand-filter-option:hover{background:#f3f5fa}.wb-brand-filter-empty{padding:10px;color:#667085}.wb-brand-filter-actions{display:flex;justify-content:flex-end;border-top:1px solid #eaecf0;padding-top:9px}
  .print-agent-settings-card .token-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}.print-agent-settings-card .token-value{min-width:260px;flex:1;font-family:Consolas,monospace}.print-agent-settings-card .security-note{display:block;margin-top:8px;color:#667085}
`;document.head.appendChild(style);})();
function wbOrderAgeMeta(o){
  if(!o.created_at)return {minutes:0,text:'—',exact:'Дата WB отсутствует',level:'none'};
  const date=new Date(o.created_at);if(!Number.isFinite(date.getTime()))return {minutes:0,text:'—',exact:'Некорректная дата WB',level:'none'};
  const minutes=Math.max(0,Math.floor((Date.now()-date.getTime())/60000));const days=Math.floor(minutes/1440),hours=Math.floor((minutes%1440)/60),mins=minutes%60;
  const text=days?`${days} д ${hours} ч`:hours?`${hours} ч ${mins} мин`:`${mins} мин`;
  const active=['NEW','PICKING'].includes(o.local_status);const level=active&&minutes>=1440?'old':active&&minutes>=720?'warn':'normal';
  return {minutes,text,exact:date.toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}),level};
}
function wbOrderPlainValue(o,key){
  const sku=o.local_sku||{},st=o.order_sticker||{};
  if(key==='status')return [o.local_status,o.local_status_name,o.wb_status,o.wb_status_name].filter(Boolean).join(' ');
  if(key==='order')return [o.external_order_id,o.order_uid].filter(Boolean).join(' ');
  if(key==='warehouse')return [o.wb_warehouse_name,o.wb_warehouse_id,o.wb_warehouse_mode].filter(Boolean).join(' ');
  if(key==='article')return o.article||sku.article||'';
  if(key==='name')return sku.name||o.product_card?.title||o.article||'';
  if(key==='brand')return o.product_card?.brand||'';
  if(key==='size')return o.size||o.product_card?.size||sku.size||'';
  if(key==='barcode')return (o.barcodes||[]).join(' ')||sku.barcode||'';
  if(key==='reserved')return reservedText(o.reserved);
  if(key==='stock')return stockText(o.stock);
  if(key==='supply')return o.supply_id||'';
  if(key==='chz')return o.chz_display||'';
  if(key==='label')return [st.part_a,st.part_b,st.barcode].filter(v=>v!==null&&v!==undefined).join(' ');
  if(key==='created'){const age=wbOrderAgeMeta(o);return `${age.text} ${age.exact}`;}
  return '';
}
function wbFilteredSortedOrders(){
  const label=String(state.wbColumnFilters?.label||'').trim().toLocaleLowerCase('ru-RU'),chzState=String(state.wbColumnFilters?.chz_state||'');
  const selectedBrands=new Set((state.wbColumnFilters?.brands||[]).map(x=>String(x).toLocaleLowerCase('ru-RU')));
  const rows=(state.wbOrders||[]).filter(o=>(!label||wbOrderPlainValue(o,'label').toLocaleLowerCase('ru-RU').includes(label))&&(!chzState||(chzState==='MISSING'?!o.chz_attached:Boolean(o.chz_attached)))&&(!selectedBrands.size||selectedBrands.has(wbOrderPlainValue(o,'brand').toLocaleLowerCase('ru-RU'))));
  const key=state.wbSort?.key,dir=state.wbSort?.dir==='desc'?-1:1;if(!key)return rows;
  return rows.map((o,index)=>({o,index})).sort((a,b)=>{
    let av,bv;
    if(key==='created'){av=new Date(a.o.created_at||0).getTime()||0;bv=new Date(b.o.created_at||0).getTime()||0;}
    else if(key==='status'){av=WB_STATUS_SORT[a.o.local_status]??99;bv=WB_STATUS_SORT[b.o.local_status]??99;}
    else if(key==='order'){av=Number(a.o.external_order_id)||0;bv=Number(b.o.external_order_id)||0;}
    else{av=wbOrderPlainValue(a.o,key);bv=wbOrderPlainValue(b.o,key);}
    const cmp=typeof av==='number'&&typeof bv==='number'?av-bv:String(av).localeCompare(String(bv),'ru',{numeric:true,sensitivity:'base'});return cmp?cmp*dir:a.index-b.index;
  }).map(x=>x.o);
}
function wbFilterControl(key){
  if(key==='label'){const value=String(state.wbColumnFilters?.label||'');return `<input id="wbColumnLabelFilter" class="wb-column-filter" data-wb-column-filter="label" value="${esc(value)}" placeholder="Найти этикетку…" aria-label="Поиск заказа по этикетке"/>`;}
  if(key==='chz'){const value=String(state.wbColumnFilters?.chz_state||'');return `<select class="wb-column-filter" data-wb-column-filter="chz_state" aria-label="Фильтр по наличию ЧЗ"><option value="" ${!value?'selected':''}>Все ЧЗ</option><option value="MISSING" ${value==='MISSING'?'selected':''}>ЧЗ не присвоен</option><option value="ASSIGNED" ${value==='ASSIGNED'?'selected':''}>ЧЗ присвоен</option></select>`;}
  return '';
}

function wbBrandContextKey(){
  return [wbCredential()||'none',state.warehouseId||'none',state.wbWarehouseFilter||$('wbWarehouseView')?.value||'none'].join(':');
}
function saveWbBrandSelection(){localStorage.setItem(wbColumnFiltersKey(),JSON.stringify(state.wbColumnFilters));}
function renderWbBrandFilter(){
  const root=$('wbBrandFilter');if(!root)return;
  const selected=state.wbColumnFilters?.brands||[],selectedKeys=new Set(selected.map(x=>String(x).toLocaleLowerCase('ru-RU')));
  const title=root.querySelector('[data-wb-brand-title]');
  if(title)title.textContent=!selected.length?'Все бренды':selected.length===1?selected[0]:`Выбрано брендов: ${selected.length}`;
  const list=root.querySelector('[data-wb-brand-list]');if(!list)return;
  if(state.wbBrandsLoading){list.innerHTML='<div class="wb-brand-filter-empty">Загружаем бренды…</div>';return;}
  if(!state.wbBrands.length){list.innerHTML='<div class="wb-brand-filter-empty">В заказах этого склада бренды пока не найдены.</div>';return;}
  list.innerHTML=state.wbBrands.map(brand=>`<label class="wb-brand-filter-option"><input type="checkbox" data-wb-brand-value="${esc(brand)}" value="${esc(brand)}" ${selectedKeys.has(String(brand).toLocaleLowerCase('ru-RU'))?'checked':''}/><span>${esc(brand)}</span></label>`).join('');
}
function installWbBrandFilter(){
  const anchor=$('wbStatusTabs');if(!anchor||$('wbBrandFilter'))return;
  anchor.insertAdjacentHTML('afterend',`<div id="wbBrandFilter" class="wb-brand-filter"><button type="button" class="secondary wb-brand-filter-toggle" data-wb-brand-toggle aria-expanded="false"><span>Бренд</span><b data-wb-brand-title>Все бренды</b><span>▾</span></button><div class="wb-brand-filter-menu hidden" data-wb-brand-menu><div class="wb-brand-filter-list" data-wb-brand-list></div><div class="wb-brand-filter-actions"><button type="button" class="mini secondary" data-wb-brand-clear>Сбросить бренды</button></div></div></div>`);
  const root=$('wbBrandFilter'),menu=root.querySelector('[data-wb-brand-menu]'),toggle=root.querySelector('[data-wb-brand-toggle]');
  root.addEventListener('click',e=>{
    if(e.target.closest('[data-wb-brand-toggle]')){const open=menu.classList.toggle('hidden')===false;toggle.setAttribute('aria-expanded',open?'true':'false');return;}
    if(e.target.closest('[data-wb-brand-clear]')){state.wbColumnFilters.brands=[];saveWbBrandSelection();renderWbBrandFilter();menu.classList.add('hidden');toggle.setAttribute('aria-expanded','false');resetWbPageAndLoad();}
  });
  root.addEventListener('change',e=>{
    if(!e.target.closest('[data-wb-brand-value]'))return;
    state.wbColumnFilters.brands=[...root.querySelectorAll('[data-wb-brand-value]:checked')].map(x=>x.value);
    saveWbBrandSelection();renderWbBrandFilter();resetWbPageAndLoad();
  });
  document.addEventListener('click',e=>{if(!root.contains(e.target)){menu.classList.add('hidden');toggle.setAttribute('aria-expanded','false');}});
  renderWbBrandFilter();
}
async function loadWbBrands(force=false){
  const cred=wbCredential(),wh=state.wbWarehouseFilter||$('wbWarehouseView')?.value||'';
  if(!cred||!state.warehouseId||!wh){state.wbBrands=[];state.wbBrandsKey='';renderWbBrandFilter();return;}
  const key=wbBrandContextKey();if(!force&&state.wbBrandsKey===key){renderWbBrandFilter();return;}
  if(state.wbBrandsLoading)return;state.wbBrandsLoading=true;renderWbBrandFilter();
  try{
    const q=new URLSearchParams({credential_id:cred,warehouse_id:state.warehouseId,wb_warehouse_id:String(wh)});
    const r=await api(`/marketplaces/wb/order-brands?${q.toString()}`);const brands=[...new Set((r.brands||[]).map(x=>String(x||'').trim()).filter(Boolean))];
    state.wbBrands=brands;state.wbBrandsKey=key;
    const allowed=new Set(brands.map(x=>x.toLocaleLowerCase('ru-RU'))),before=state.wbColumnFilters.brands||[];
    state.wbColumnFilters.brands=before.filter(x=>allowed.has(String(x).toLocaleLowerCase('ru-RU')));
    if(state.wbColumnFilters.brands.length!==before.length)saveWbBrandSelection();
  }finally{state.wbBrandsLoading=false;renderWbBrandFilter();}
}
installWbBrandFilter();

async function syncWbOrders({silent=false,force=true}={}){
  const btn=$('syncWbOrdersBtn');
  try{
    const cred=wbCredential();if(!cred)throw new Error('Сначала подключите Wildberries');await ensureContext();
    if(btn&&!silent){btn.disabled=true;btn.textContent='Синхронизация…';}
    const selectedWh=state.wbWarehouseFilter||$('wbWarehouseView')?.value||'';if(!selectedWh)throw new Error('Выберите склад WB');
    const r=await api('/marketplaces/wb/orders/sync',{method:'POST',body:JSON.stringify({credential_id:cred,warehouse_id:state.warehouseId,wb_warehouse_id:Number(selectedWh),days:30,force})});
    state.wbOrderCounts=r.counts||state.wbOrderCounts;
    await loadWbOrders(false);
    if(!r.throttled) await loadWbSupplies(false).catch(()=>{});
    if(!silent&&!r.throttled){const warn=(r.warnings||[]).filter(Boolean);toast(`WB: ${r.total_local} заказов${Number(r.total_local||0)===0?' · нажмите «Диагностика склада»':''}${r.status_requested?` · статусы проверены ${r.status_requested}`:''}${r.status_updated?` · исправлено ${r.status_updated}`:''}${r.backfilled?` · добавлено ${r.backfilled}`:''}${warn.length?` · ${warn[0]}`:''}`,Boolean(warn.length&&Number(r.total_local||0)===0));}
    resetWbPacking(false);
  }catch(e){if(!silent)toast(e.message,true)}finally{if(btn&&!silent){btn.disabled=false;btn.textContent='Синхронизировать WB';}}
}
$('syncWbOrdersBtn').addEventListener('click',()=>syncWbOrders({silent:false,force:true}));
$('importWbDataBtn')?.addEventListener('click',async()=>{
  const btn=$('importWbDataBtn');
  try{
    const cred=wbCredential();if(!cred)throw new Error('Сначала подключите Wildberries');await ensureContext();
    const selectedWh=state.wbWarehouseFilter||$('wbWarehouseView')?.value||'';if(!selectedWh)throw new Error('Выберите склад WB');
    btn.disabled=true;btn.textContent='Импорт WB…';
    const r=await api('/marketplaces/wb/orders/sync?full_meta=true',{method:'POST',body:JSON.stringify({credential_id:cred,warehouse_id:state.warehouseId,wb_warehouse_id:Number(selectedWh),days:30,force:true})});
    state.wbOrderCounts=r.counts||state.wbOrderCounts;
    await loadWbOrders(false);
    const warn=(r.warnings||[]).filter(Boolean);
    toast(`Импорт WB: проверено ${r.meta_checked||0} заказов · обновлено ЧЗ/этикеток/статусов ${r.meta_changed||0}${r.status_updated?` · статусов ${r.status_updated}`:''}${warn.length?` · ${warn[0]}`:''}`,Boolean(warn.length));
  }catch(e){toast(e.message,true)}finally{btn.disabled=false;btn.textContent='Импортировать данные WB';}
});

function installWbShortageExport(){const page=$('page-marketplaces');if(!page||$('wbShortageExportBtn'))return;const anchor=$('wbStatusTabs')||page.querySelector('.panel-head');if(!anchor)return;const btn=document.createElement('button');btn.id='wbShortageExportBtn';btn.type='button';btn.className='secondary';btn.textContent='Excel: дефицит по заказам';btn.addEventListener('click',async()=>{try{await ensureContext();const cred=wbCredential(),wbWh=Number(state.wbWarehouseFilter||0)||null;if(!cred)throw new Error('Выберите кабинет WB');const q=new URLSearchParams({credential_id:cred,warehouse_id:state.warehouseId});if(wbWh)q.set('wb_warehouse_id',String(wbWh));const blob=await apiBlob(`/marketplaces/wb/shortage.xlsx?${q.toString()}`,{timeoutMs:90000});saveBlob(blob,`Дефицит_WB_${new Date().toISOString().slice(0,10)}.xlsx`);toast('Excel дефицита сформирован');}catch(error){toast(error.message,true)}});anchor.insertAdjacentElement('afterend',btn);}

installWbShortageExport();

/* FC_K9R49_TSD_ASSIGNMENT_MANAGER:
   Desktop FulfillCore is the source of truth for which current batch a picker sees.
   Old TSD-created sessions remain visible and can be explicitly removed. */
function installTsdResultsPanel(){
  const page=$('page-marketplaces');if(!page||$('tsdResultsBtn'))return;
  if(!document.getElementById('fcTsdResultsStyles')){
    const style=document.createElement('style');style.id='fcTsdResultsStyles';
    style.textContent='.tsd-results-panel{margin:12px 0;padding:14px;border:1px solid #ddd6fe;border-radius:14px;background:#faf9ff}.tsd-results-head{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}.tsd-results-head h4{margin:0}.tsd-result-status{font-weight:800}.tsd-result-status.finished{color:#166534}.tsd-result-status.active{color:#5b21b6}.tsd-result-status.cancelled{color:#991b1b}.tsd-missing-bad{color:#b42318;font-weight:900}.tsd-results-actions{display:flex;gap:8px;flex-wrap:wrap}.tsd-results-panel .table-wrap{margin-top:10px}.tsd-results-panel table{min-width:900px}.tsd-assign-box{margin:12px 0;padding:12px;border:1px solid #c4b5fd;border-radius:12px;background:#fff}.tsd-assign-grid{display:grid;grid-template-columns:minmax(180px,1fr) 150px auto;gap:10px;align-items:end}.tsd-assign-grid label{margin:0}.tsd-assign-grid input{width:100%}.tsd-availability{display:block;margin-top:8px;color:#52525b}.tsd-source-desktop{color:#166534;font-weight:800}.tsd-source-legacy{color:#92400e;font-weight:800}@media(max-width:900px){.tsd-assign-grid{grid-template-columns:1fr}}';
    document.head.appendChild(style);
  }
  const anchor=$('wbShortageExportBtn')||$('wbStatusTabs')||page.querySelector('.panel-head');if(!anchor)return;
  const btn=document.createElement('button');btn.id='tsdResultsBtn';btn.type='button';btn.className='secondary';btn.textContent='TSD: задания сборщикам';
  const panel=document.createElement('div');panel.id='tsdResultsPanel';panel.className='tsd-results-panel hidden';
  panel.innerHTML=`<div class="tsd-results-head"><div><span class="kicker">TSD СБОРКА</span><h4>Задания сборщикам</h4><small>Сборка выдаётся и снимается здесь. ТСД автоматически показывает последнее актуальное назначение.</small></div><div class="tsd-results-actions"><button type="button" class="secondary" id="tsdResultsRefresh">Обновить</button><button type="button" class="secondary" id="tsdOpenMobile">Открыть TSD</button></div></div>
    <div class="tsd-assign-box"><div class="tsd-assign-grid"><label>Сборщик<input id="tsdAssignPicker" list="tsdPickerList" placeholder="Например, Аббос" autocomplete="off"><datalist id="tsdPickerList"></datalist></label><label>Количество, шт.<input id="tsdAssignQty" type="number" min="1" step="1" value="1"></label><button type="button" class="primary" id="tsdAssignBtn">Выдать сборку</button></div><small id="tsdAssignAvailability" class="tsd-availability">Проверяем доступный объём…</small></div>
    <div id="tsdResultsBody"><div class="empty-state">Откройте панель, чтобы загрузить задания.</div></div>`;
  anchor.insertAdjacentElement('afterend',btn);btn.insertAdjacentElement('afterend',panel);
  let qtyEdited=false;
  $('tsdAssignQty').addEventListener('input',()=>{qtyEdited=true;});
  const statusLabel=x=>({FINISHED:'Завершено',ACTIVE:'В работе',CANCELLED:'Снято'}[String(x||'').toUpperCase()]||String(x||'—'));
  const sourceLabel=x=>String(x||'').toUpperCase()==='DESKTOP'?'Назначено из FulfillCore':'Старая TSD-сборка';
  async function load(){
    try{
      await ensureContext();
      const body=$('tsdResultsBody');body.innerHTML='<div class="empty-state">Загружаем задания TSD…</div>';
      const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId,limit:'60'});
      const aq=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId});
      const [rows,a]=await Promise.all([
        api(`/tsd/api/picking-results?${q.toString()}`,{timeoutMs:12000}),
        api(`/tsd/api/picking/availability?${aq.toString()}`,{timeoutMs:12000})
      ]);
      const free=Number(a?.available_qty||0),claimed=Number(a?.already_claimed_qty||0),total=Number(a?.total_qty||0);
      const pickerNames=[...new Set((rows||[]).map(r=>String(r.picker||'').trim()).filter(Boolean))].sort((a,b)=>a.localeCompare(b,'ru'));
      if($('tsdPickerList'))$('tsdPickerList').innerHTML=pickerNames.map(x=>`<option value="${esc(x)}"></option>`).join('');
      $('tsdAssignAvailability').textContent=`На сборке с ячейкой: ${fmtQty(total)} шт. · свободно для нового TSD: ${fmtQty(free)}${claimed>0?` · уже выдано: ${fmtQty(claimed)}`:''}`;
      if(!qtyEdited)$('tsdAssignQty').value=String(Math.max(1,Math.floor(free||1)));
      $('tsdAssignBtn').disabled=free<=0;
      body.innerHTML=rows?.length?`<div class="table-wrap"><table><thead><tr><th>Дата</th><th>Сборщик</th><th>Источник</th><th>Статус</th><th>Нужно</th><th>Собрано</th><th>Нет</th><th>Осталось</th><th>Действие</th></tr></thead><tbody>${rows.map(r=>{const st=String(r.status||'').toLowerCase(),active=String(r.status||'').toUpperCase()==='ACTIVE',src=String(r.assignment_source||'LEGACY').toUpperCase();return `<tr><td>${esc(new Date(r.assigned_at||r.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}))}</td><td><b>${esc(r.picker||'—')}</b></td><td><span class="${src==='DESKTOP'?'tsd-source-desktop':'tsd-source-legacy'}">${esc(sourceLabel(src))}</span>${r.assigned_by?`<small class="subline">${esc(r.assigned_by)}</small>`:''}</td><td><span class="tsd-result-status ${esc(st)}">${esc(statusLabel(r.status))}</span></td><td>${fmtQty(r.total_qty||0)}</td><td><b>${fmtQty(r.picked_qty||0)}</b></td><td class="${Number(r.missing_qty||0)>0?'tsd-missing-bad':''}">${fmtQty(r.missing_qty||0)}</td><td>${fmtQty(r.remaining_qty||0)}</td><td>${active?`<button type="button" class="mini danger" data-tsd-unassign="${esc(r.id)}" data-tsd-picker="${esc(r.picker||'')}">Снять сборку</button>`:(r.report_url?`<button type="button" class="mini secondary" data-tsd-result-report="${esc(r.id)}" data-tsd-picker="${esc(r.picker||'picker')}">Excel</button>`:'—')}</td></tr>`}).join('')}</tbody></table></div>`:'<div class="empty-state">Заданий TSD пока нет.</div>';
    }catch(e){$('tsdResultsBody').innerHTML=`<div class="empty-state bad">${esc(e.message)}</div>`;$('tsdAssignAvailability').textContent='Не удалось получить доступный объём';}
  }
  async function assign(){
    try{
      await ensureContext();
      const picker=$('tsdAssignPicker').value.trim(),qty=Math.max(1,Math.floor(Number($('tsdAssignQty').value||0)));
      if(!picker)throw new Error('Укажите имя сборщика');
      if(!Number.isFinite(qty)||qty<1)throw new Error('Укажите количество');
      const b=$('tsdAssignBtn');b.disabled=true;b.textContent='Выдаём…';
      const r=await api('/tsd/api/picking/assign',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,picker,batch_size:qty,assigned_by:$('currentUser')?.textContent||'Пользователь'}),timeoutMs:30000});
      toast(`Сборка выдана: ${picker} · ${fmtQty(r?.progress?.total||0)} шт.`);qtyEdited=false;await load();
    }catch(e){toast(e.message,true);}finally{const b=$('tsdAssignBtn');if(b){b.disabled=false;b.textContent='Выдать сборку';}}
  }
  btn.addEventListener('click',()=>{panel.classList.toggle('hidden');if(!panel.classList.contains('hidden'))load();});
  $('tsdResultsRefresh').addEventListener('click',load);
  $('tsdAssignBtn').addEventListener('click',assign);
  $('tsdOpenMobile').addEventListener('click',()=>window.open('/tsd','_blank','noopener'));
  panel.addEventListener('click',async e=>{
    const unassign=e.target.closest('[data-tsd-unassign]');
    if(unassign){
      const picker=unassign.dataset.tsdPicker||'сборщика';
      if(!confirm(`Снять текущую сборку у ${picker}?\n\nTSD перестанет её показывать. Статусы заказов и физические остатки не меняются.`))return;
      try{unassign.disabled=true;await api(`/tsd/api/picking/${encodeURIComponent(unassign.dataset.tsdUnassign)}/unassign`,{method:'POST',body:'{}',timeoutMs:15000});toast(`Сборка снята у ${picker}`);qtyEdited=false;await load();}catch(err){toast(err.message,true);}finally{unassign.disabled=false;}
      return;
    }
    const report=e.target.closest('[data-tsd-result-report]');if(!report)return;
    try{
      const blob=await apiBlob(`/tsd/api/picking/${encodeURIComponent(report.dataset.tsdResultReport)}/report.xlsx`,{timeoutMs:60000});
      const picker=(report.dataset.tsdPicker||'picker').replace(/[^0-9A-Za-zА-Яа-я_-]+/g,'_');
      saveBlob(blob,`TSD_сборка_${picker}_${new Date().toISOString().slice(0,10)}.xlsx`);
    }catch(err){toast(err.message,true);}
  });
}
installTsdResultsPanel();

function wbOrdersQuery(){
  const cred=wbCredential();if(!cred)return null;
  const q=new URLSearchParams({credential_id:cred,page:String(state.wbPage),page_size:String(state.wbPageSize)});
  if(state.warehouseId)q.set('warehouse_id',state.warehouseId);
  const wh=state.wbWarehouseFilter||$('wbWarehouseView')?.value||'';if(!wh)return null;q.set('wb_warehouse_id',wh);
  if(state.wbOrderFilter)q.set('local_status',state.wbOrderFilter);
  const orderNo=$('wbOrderNumberFilter')?.value.trim();if(orderNo)q.set('order_number',orderNo);
  const article=$('wbArticleFilter')?.value.trim();if(article)q.set('article',article);
  const location=$('wbLocationFilter')?.value.trim();if(location)q.set('location',location);
  const supply=$('wbSupplyFilter')?.value.trim();if(supply)q.set('supply',supply);
  const search=$('wbOrderSearch')?.value.trim();if(search)q.set('search',search);
  const cf=state.wbColumnFilters||{},label=String(cf.label||'').trim(),chzState=String(cf.chz_state||'').trim();
  if(label)q.set('label',label);if(chzState)q.set('chz_state',chzState);
  for(const brand of (cf.brands||[])){if(String(brand||'').trim())q.append('brand',String(brand).trim());}
  if(state.wbSort?.key){q.set('sort_by',state.wbSort.key);q.set('sort_dir',state.wbSort.dir==='desc'?'desc':'asc');}
  return q;
}

// FC_K9R52_STATUS_TAB_LOCAL_CACHE: status itself is part of the cache key, not a
// reason to disable cache. Only free-text/column filters bypass the local snapshot.
function wbHasNonStatusFilters(){
  return Boolean(Object.values(state.wbColumnFilters||{}).some(v=>String(v||'').trim())||$('wbOrderNumberFilter')?.value.trim()||$('wbArticleFilter')?.value.trim()||$('wbLocationFilter')?.value.trim()||$('wbSupplyFilter')?.value.trim()||$('wbOrderSearch')?.value.trim());
}
function wbHasActiveFilters(){return Boolean(state.wbOrderFilter||wbHasNonStatusFilters());}
function wbCacheKey(){return `fc_wb_orders_cache_r53_${state.clientId||'none'}_${state.warehouseId||'none'}_${wbCredential()}_${state.wbWarehouseFilter||$('wbWarehouseView')?.value||'none'}_${state.wbOrderFilter||'ALL'}_${state.wbPage}_${state.wbPageSize}`;}
function saveWbOrdersCache(r){
  if(wbHasNonStatusFilters()||Number(r.total??0)<=0)return;
  // FC_K9R53_WB_ORDER_CACHE_AFTER_ENRICH: never replace a useful cached page with
  // placeholders before card/reservation decoration has completed.
  if((r.orders||[]).some(o=>o.card_loaded===false||o.reserved_loaded===false))return;
  try{localStorage.setItem(wbCacheKey(),JSON.stringify({ts:Date.now(),orders:r.orders||[],counts:r.counts||{},total:Number(r.total||0),pages:Number(r.pages||1),page:Number(r.page||1)}));}catch{}
}
function readWbOrdersCache(){
  if(wbHasNonStatusFilters())return null;
  try{const raw=localStorage.getItem(wbCacheKey());if(!raw)return null;const data=JSON.parse(raw);if(!data?.orders?.length||Date.now()-Number(data.ts||0)>6*60*60*1000)return null;return data;}catch{return null;}
}
function restoreWbOrdersCacheImmediately(){
  const cached=readWbOrdersCache();if(!cached)return false;
  state.wbOrders=cached.orders||[];state.wbOrderCounts=cached.counts||{};state.wbTotal=Number(cached.total||0);state.wbPages=Number(cached.pages||1);state.wbPage=Number(cached.page||1);state.wbUsingCache=true;renderWbOrders();return true;
}
function mergeWbFastOrderRows(rows){
  const oldById=new Map((state.wbOrders||[]).map(o=>[String(o.id),o]));
  return (rows||[]).map(row=>{const o={...row},old=oldById.get(String(o.id));if(!old)return o;
    if(o.card_loaded===false&&old.product_card){o.product_card=old.product_card;o.card_stale=true;}
    if(o.reserved_loaded===false&&old.reserved_loaded!==false){o.reserved=old.reserved||[];o.reserved_stale=true;}
    if(o.stock_loaded===false&&old.stock_loaded===true){o.stock=old.stock||[];o.stock_stale=true;}
    return o;});
}
function wbOrderEnrichFields(){const fields=['card','reserved'];if(state.wbColumns.includes('stock'))fields.push('stock');return fields;}
async function enrichWbVisibleOrders(requestSeq=state.wbOrdersRequestSeq){
  const cred=wbCredential(),ids=(state.wbOrders||[]).map(o=>o.id).filter(Boolean);if(!cred||!state.warehouseId||!ids.length)return;
  const fields=wbOrderEnrichFields();
  // FC_K9R53_WB_ORDER_BACKGROUND_ENRICH_UI: first paint is already on screen; only
  // optional PostgreSQL decorations are fetched now. No WB network sync is triggered.
  try{const r=await api('/marketplaces/wb/orders/enrich',{method:'POST',body:JSON.stringify({credential_id:cred,warehouse_id:state.warehouseId,order_ids:ids,fields})});
    if(requestSeq!==state.wbOrdersRequestSeq)return;const byId=new Map((r.details||[]).map(x=>[String(x.id),x]));
    state.wbOrders=state.wbOrders.map(o=>{const d=byId.get(String(o.id));if(!d)return o;const next={...o};
      if(Object.prototype.hasOwnProperty.call(d,'product_card')){next.product_card=d.product_card;next.card_loaded=true;delete next.card_stale;}
      if(Object.prototype.hasOwnProperty.call(d,'reserved')){next.reserved=d.reserved||[];next.reserved_loaded=true;delete next.reserved_stale;}
      if(Object.prototype.hasOwnProperty.call(d,'stock')){next.stock=d.stock||[];next.stock_loaded=true;delete next.stock_stale;}
      return next;});renderWbOrders();saveWbOrdersCache({orders:state.wbOrders,counts:state.wbOrderCounts,total:state.wbTotal,pages:state.wbPages,page:state.wbPage});
  }catch(e){console.warn('WB order background enrichment:',e)}
}

async function loadWbOrders(showToast=false){
  // FC_K9R20_STATUS_TAB_REQUEST_TOKEN: never let an older in-flight status request
  // repaint the table after the operator has already switched to another tab.
  const requestSeq=++state.wbOrdersRequestSeq;
  const q=wbOrdersQuery();
  if(!q){state.wbOrders=[];state.wbOrderCounts={};state.wbTotal=0;state.wbPages=1;state.wbUsingCache=false;renderWbOrders();return;}
  // FC_K9R53_WB_ORDER_FIRST_PAINT_REQUEST: keep heavy warehouse decoration out of
  // the request that controls when the table first becomes visible.
  q.set('include_details','false');
  state.wbLoadingOrders=true;
  try{
    const r=await api(`/marketplaces/wb/orders?${q.toString()}`);
    if(requestSeq!==state.wbOrdersRequestSeq)return;
    const total=Number(r.total??r.count??0);const cached=total===0?readWbOrdersCache():null;
    if(cached){
      state.wbOrders=cached.orders||[];state.wbOrderCounts=cached.counts||{};state.wbTotal=Number(cached.total||0);state.wbPages=Number(cached.pages||1);state.wbPage=Number(cached.page||state.wbPage||1);state.wbUsingCache=true;
    }else if(total===0 && state.wbOrders.length && !wbHasActiveFilters()){
      // A transient empty response must never wipe an already rendered warehouse board.
      // Keep the last server-backed rows on screen while the recovery sync is running.
      state.wbUsingCache=true;
    }else{
      state.wbOrders=mergeWbFastOrderRows(r.orders||[]);state.wbOrderCounts=r.counts||{};state.wbTotal=total;state.wbPages=Number(r.pages||1);state.wbPage=Number(r.page||state.wbPage||1);state.wbUsingCache=false;
    }
    // NEW/PICKING is part of stock truth. Any successful order refresh invalidates the
    // inventory reserve cache so the next inventory opening cannot show an old reserve.
    if(!state.wbUsingCache)state.inventoryCatalogLoadedAt=0;
    renderWbOrders();
    if(!state.wbUsingCache)setTimeout(()=>enrichWbVisibleOrders(requestSeq),0);
    if(showToast)toast(state.wbUsingCache?`WB: показываем последние сохранённые ${state.wbTotal} заказов, синхронизация продолжается`:`WB: заказов ${state.wbTotal}`);
    scheduleWbMetaRefresh();
  }catch(e){
    if(requestSeq!==state.wbOrdersRequestSeq)return;
    const cached=readWbOrdersCache();if(cached){state.wbOrders=cached.orders||[];state.wbOrderCounts=cached.counts||{};state.wbTotal=Number(cached.total||0);state.wbPages=Number(cached.pages||1);state.wbUsingCache=true;renderWbOrders();}
    if(showToast)toast(e.message,true);else console.warn('WB orders refresh:',e)
  }
  finally{if(requestSeq===state.wbOrdersRequestSeq){state.wbLoadingOrders=false;loadWbBrands(false).catch(e=>console.warn('WB brands refresh:',e));}}
}

function wbOrderCell(o,key){
  const sku=o.local_sku||{};const watch=o.wb_warehouse_mode==='WATCH';
  if(key==='status')return `<span class="status-pill ${wbStatusClass(o.local_status)}">${esc(o.local_status_name||o.local_status)}</span>${o.wb_status_name?`<small class="subline">WB: ${esc(o.wb_status_name)}</small>`:''}`;
  if(key==='order')return `<button type="button" class="order-link" data-wb-order-open="${o.id}" title="Открыть заказ №${esc(o.external_order_id)}">${esc(o.external_order_id)}</button>${o.order_uid?`<small class="subline">${esc(o.order_uid)}</small>`:''}`;
  if(key==='warehouse')return `<b>${esc(o.wb_warehouse_name||('ID '+(o.wb_warehouse_id||'—')))}</b><small class="subline">${watch?'Наблюдение':'Работа'}</small>`;
  if(key==='article')return esc(o.article||sku.article||'—');
  if(key==='name')return `${o.product_card?.photo?`<img class="order-thumb" src="${esc(o.product_card.photo)}" alt=""/>`:''}<span>${esc(sku.name||o.product_card?.title||o.article||'—')}</span>`;
  if(key==='size')return esc(o.size||o.product_card?.size||sku.size||'—');
  if(key==='barcode')return esc((o.barcodes||[]).join(', ')||sku.barcode||'—');
  if(key==='reserved'){if(o.reserved_loaded===false&&!(o.reserved||[]).length)return '<span class="muted">загрузка…</span>';return `<b>${esc(reservedText(o.reserved))}</b>${o.reserved_loaded===false?'<small class="subline">обновляется…</small>':(o.reserved?.length?'<small class="subline">по последнему сборочному листу</small>':'<small class="subline">ещё не распределён</small>')}`;}
  if(key==='stock'){if(o.stock_loaded===false&&!(o.stock||[]).length)return '<span class="muted">загрузка…</span>';return `${esc(stockText(o.stock))}${o.stock_loaded===false?'<small class="subline">обновляется…</small>':''}`;}
  if(key==='supply')return o.supply_id?`<b>${esc(o.supply_id)}</b>`:'—';
  // Only the actually attached/scanned DataMatrix is shown here. Requirement flags from
  // WB are intentionally not displayed in this column.
  if(key==='chz')return o.chz_attached&&o.chz_display?`<span class="chz-code" title="${esc(o.chz_display)}">${esc(o.chz_display)}</span>`:'';
  if(key==='label'){
    const st=o.order_sticker||{};if(!o.order_sticker_available)return '';
    return `<div class="wb-sticker-cell"><div><b>${esc(st.part_a??'')}</b>${st.part_b!==null&&st.part_b!==undefined?` <span class="sticker-part-b">${esc(st.part_b)}</span>`:''}</div>${st.barcode?`<small class="subline mono-wrap">${esc(st.barcode)}</small>`:''}${watch?'':`<button class="mini secondary" data-wb-order-label="${o.id}" title="Печать этикетки заказа">Печать</button>`}</div>`;
  }
  if(key==='created'){const age=wbOrderAgeMeta(o);return `<span class="wb-order-age ${age.level}">${esc(age.text)}</span><small class="subline" title="Точная дата поступления WB">${esc(age.exact)}</small>`;}
  if(key==='actions'){
    if(watch)return '<span class="pill">Только просмотр</span>';
    const canDelete=Boolean(o.chz_attached);const canOrderLabel=o.local_status==='PACKED'||o.order_sticker_available||['confirm','complete'].includes(o.supplier_status);
    return `<div class="wb-order-actions"><button class="mini secondary" data-wb-product-label="${o.id}">ШК товара</button>${canOrderLabel?`<button class="mini secondary" data-wb-order-label="${o.id}">Этикетка WB</button>`:''}${canDelete?`<button class="mini danger" data-wb-delete-chz="${o.id}">Отвязать ЧЗ</button>`:''}</div>`;
  }
  return '—';
}

function wbColWidth(key){return Math.max(70,Math.min(650,Number(state.wbColumnWidths[key]||WB_DEFAULT_WIDTHS[key]||150)));}
function wbColStyle(key){const w=wbColWidth(key);return `width:${w}px;min-width:${w}px;max-width:${w}px`;}
function applyWbColumnWidth(key,width){
  const w=Math.max(70,Math.min(650,Math.round(width)));state.wbColumnWidths[key]=w;
  document.querySelectorAll(`#wbOrdersScroll [data-col="${CSS.escape(key)}"]`).forEach(el=>{el.style.width=`${w}px`;el.style.minWidth=`${w}px`;el.style.maxWidth=`${w}px`;});
  syncWbHorizontalScroll();
}

function syncWbHorizontalScroll(){
  const top=$('wbOrdersTopScroll'),bottom=$('wbOrdersScroll');if(!top||!bottom)return;
  const table=bottom.querySelector('table'),inner=top.firstElementChild;if(!table||!inner)return;
  requestAnimationFrame(()=>{
    inner.style.width=`${Math.max(table.scrollWidth,bottom.clientWidth)}px`;
    top.style.display='block';
    top.classList.toggle('no-overflow',table.scrollWidth<=bottom.clientWidth+2);
    top.scrollLeft=bottom.scrollLeft;
  });
}

function renderWbOrderRows(){
  const rows=wbFilteredSortedOrders(),pageSize=state.wbPageSize;
  $('wbOrdersBody').innerHTML=rows.length?rows.map(o=>{
    const watch=o.wb_warehouse_mode==='WATCH',age=wbOrderAgeMeta(o);const rowClass=age.level==='old'?'wb-order-row-old':age.level==='warn'?'wb-order-row-warn':'';
    return `<tr class="${rowClass}"><td class="select-col"><input class="wb-order-check" type="checkbox" value="${esc(o.external_order_id)}" ${state.wbSelectedOrderIds.has(String(o.external_order_id))?'checked':''} ${watch?'disabled':''}/></td>${state.wbColumns.map(k=>`<td data-col="${k}" style="${wbColStyle(k)}">${wbOrderCell(o,k)}</td>`).join('')}</tr>`;
  }).join(''):`<tr><td colspan="${state.wbColumns.length+1}" class="empty-state">Заказов с таким фильтром нет.</td></tr>`;
  const ca=$('wbCheckAll');if(ca){const visible=[...document.querySelectorAll('.wb-order-check:not(:disabled)')];ca.checked=visible.length>0&&visible.every(x=>state.wbSelectedOrderIds.has(String(x.value)));ca.onchange=e=>{visible.forEach(x=>{x.checked=e.target.checked;if(e.target.checked)state.wbSelectedOrderIds.add(String(x.value));else state.wbSelectedOrderIds.delete(String(x.value));});saveWbSelectedOrders();syncWbButtons();};}
  const start=state.wbTotal?(state.wbPage-1)*pageSize+1:0,end=state.wbTotal?Math.min(state.wbPage*pageSize,state.wbTotal):0,filtered=Object.values(state.wbColumnFilters||{}).some(v=>String(v||'').trim());
  if($('wbOrdersRange'))$('wbOrdersRange').textContent=`${start}–${end} из ${state.wbTotal}${filtered?` · найдено на странице: ${rows.length}`:''}`;
  syncWbButtons();syncWbHorizontalScroll();
}

function renderWbOrders(){
  const focusRoot=$('page-marketplaces')||document;const focusSnapshot=fcFocusedControlSnapshot(focusRoot);
  updateWbCounts();normalizeWbColumns();renderWbColumnsPanel();
  if($('wbOrdersStaleNotice'))$('wbOrdersStaleNotice').classList.toggle('hidden',!state.wbUsingCache);
  const sortKey=state.wbSort?.key,sortDir=state.wbSort?.dir;
  const headers=['<th class="select-col"><input id="wbCheckAll" type="checkbox" /></th>',...state.wbColumns.map(k=>`<th data-col="${k}" data-drag-col="${k}" draggable="true" style="${wbColStyle(k)}"><button type="button" class="wb-th-sort" data-wb-sort-col="${k}" title="Сортировать по столбцу"><span>${esc(wbColumnName(k))}</span><span class="wb-sort-mark">${sortKey===k?(sortDir==='desc'?'▼':'▲'):'↕'}</span></button><span class="col-resizer" data-resize-col="${k}" title="Потяните, чтобы изменить ширину"></span></th>` )];
  const filters=['<th class="select-col wb-filter-heading"></th>',...state.wbColumns.map(k=>`<th class="wb-filter-heading" data-col="${k}" style="${wbColStyle(k)}">${wbFilterControl(k)}</th>`)];
  $('wbOrdersHead').innerHTML=`<tr class="wb-column-head-row">${headers.join('')}</tr><tr class="wb-column-filter-row">${filters.join('')}</tr>`;
  renderWbOrderRows();
  if($('wbPageSize'))$('wbPageSize').value=String(state.wbPageSize);
  if($('wbPageInfo'))$('wbPageInfo').textContent=`${state.wbPage} / ${state.wbPages}`;
  if($('wbFirstPage'))$('wbFirstPage').disabled=state.wbPage<=1;if($('wbPrevPage'))$('wbPrevPage').disabled=state.wbPage<=1;if($('wbNextPage'))$('wbNextPage').disabled=state.wbPage>=state.wbPages;if($('wbLastPage'))$('wbLastPage').disabled=state.wbPage>=state.wbPages;
  const focusRestored=fcRestoreFocusedControl(focusRoot,focusSnapshot);
  // If an operator was typing in a filter/form, do not fall through to the packing scanner.
  if(!focusSnapshot&&!focusRestored)restoreWbPackingScannerFocus(false);
}

function resetWbPageAndLoad(){state.wbPage=1;loadWbOrders(false);}
$('wbStatusTabs').addEventListener('click',e=>{const b=e.target.closest('[data-wb-filter]');if(!b)return;const next=b.dataset.wbFilter||'';if(next===state.wbOrderFilter)return;clearWbSelectedOrders();state.wbOrderFilter=next;localStorage.setItem(wbOrderFilterKey(),state.wbOrderFilter);document.querySelectorAll('#wbStatusTabs [data-wb-filter]').forEach(x=>x.classList.toggle('active',x===b));
  // FC_K9R52_STATUS_TAB_LOCAL_CACHE: paint the last confirmed page for this exact status
  // immediately, then refresh it from PostgreSQL under the existing request-token guard.
  state.wbPage=1;state.wbPages=1;state.wbTotal=Number(state.wbOrderCounts?.[next]??0);state.wbUsingCache=false;
  if(!restoreWbOrdersCacheImmediately()){state.wbOrders=[];renderWbOrders();}
  loadWbOrders(false);
});
let __wbFilterTimer=null;
['wbOrderNumberFilter','wbOrderSearch','wbArticleFilter','wbLocationFilter','wbSupplyFilter'].forEach(id=>$(id)?.addEventListener('input',()=>{clearTimeout(__wbFilterTimer);__wbFilterTimer=setTimeout(resetWbPageAndLoad,250);}));
$('wbWarehouseView')?.addEventListener('change',async e=>{
  const previous=state.wbWarehouseFilter;state.wbWarehouseFilter=e.target.value;saveScopedWbWarehouseFilter();
  state.inventoryCatalogLoaded=false;state.inventoryCatalogContextKey='';state.inventoryCatalogLoadedAt=0;state.inventoryWbControl={};state.wbLiveStock={};state.wbLiveStockSummary=null;state.wbLiveStockCheckedAt=null;
  state.wbBrands=[];state.wbBrandsKey='';renderWbBrandFilter();
  state.wbPage=1;state.wbOrderFilter='';localStorage.setItem(wbOrderFilterKey(),'');document.querySelectorAll('#wbStatusTabs [data-wb-filter]').forEach(x=>x.classList.toggle('active',x.dataset.wbFilter===''));
  const wh=state.wbWarehouses.find(w=>String(w.id)===String(state.wbWarehouseFilter));if($('wbSupplyWarehouseName'))$('wbSupplyWarehouseName').textContent=wh?`· ${wh.name}`:'';
  state.wbOrders=[];state.wbSupplies=[];state.wbTotal=0;state.wbOrderCounts={};resetWbPacking(false);renderWbOrders();restoreWbOrdersCacheImmediately();
  try{await loadWbOrders(false);await loadWbBrands(true);await loadWbSupplies(false);setTimeout(()=>syncWbOrders({silent:true,force:false}).catch(()=>{}),400);}
  catch(err){state.wbWarehouseFilter=previous;saveScopedWbWarehouseFilter();refreshWbWarehouseSelector();toast(`Склад WB не переключён: ${err.message}`,true);}
});
$('wbPageSize')?.addEventListener('change',e=>{state.wbPageSize=Number(e.target.value)||50;state.wbPage=1;fcR20Remember('fc_wb_page_size',String(state.wbPageSize));loadWbOrders(false);});
$('wbFirstPage')?.addEventListener('click',()=>{if(state.wbPage>1){clearWbSelectedOrders();state.wbPage=1;loadWbOrders(false);$('wbOrdersScroll')?.scrollTo({top:0});}});
$('wbPrevPage')?.addEventListener('click',()=>{if(state.wbPage>1){clearWbSelectedOrders();state.wbPage--;loadWbOrders(false);$('wbOrdersScroll')?.scrollTo({top:0});}});
$('wbNextPage')?.addEventListener('click',()=>{if(state.wbPage<state.wbPages){clearWbSelectedOrders();state.wbPage++;loadWbOrders(false);$('wbOrdersScroll')?.scrollTo({top:0});}});
$('wbLastPage')?.addEventListener('click',()=>{if(state.wbPage<state.wbPages){state.wbPage=state.wbPages;loadWbOrders(false);$('wbOrdersScroll')?.scrollTo({top:0});}});
$('wbOrdersBody').addEventListener('change',e=>{if(e.target.classList.contains('wb-order-check')){const id=String(e.target.value);if(e.target.checked)state.wbSelectedOrderIds.add(id);else state.wbSelectedOrderIds.delete(id);saveWbSelectedOrders();syncWbButtons();}});
$('wbColumnsBtn')?.addEventListener('click',()=>{$('wbColumnsPanel').classList.toggle('hidden');renderWbColumnsPanel();});
$('wbColumnsPanel')?.addEventListener('change',e=>{const cb=e.target.closest('[data-wb-column]');if(!cb)return;const key=cb.dataset.wbColumn;if(cb.checked&&!state.wbColumns.includes(key))state.wbColumns.push(key);if(!cb.checked)state.wbColumns=state.wbColumns.filter(x=>x!==key);fcR20Remember('fc_wb_columns',JSON.stringify(state.wbColumns));renderWbOrders();if(cb.checked&&(key==='stock'||key==='reserved'))setTimeout(()=>enrichWbVisibleOrders(state.wbOrdersRequestSeq),0);});
$('wbColumnsPanel')?.addEventListener('click',e=>{
  const left=e.target.closest('[data-wb-column-left]'),right=e.target.closest('[data-wb-column-right]'),clear=e.target.closest('[data-wb-clear-column-filters]');
  if(clear){state.wbColumnFilters=smartWbColumnFilters({brands:state.wbColumnFilters?.brands||[]});localStorage.setItem(wbColumnFiltersKey(),JSON.stringify(state.wbColumnFilters));renderWbOrders();resetWbPageAndLoad();return;}
  const key=left?.dataset.wbColumnLeft||right?.dataset.wbColumnRight;if(!key)return;const from=state.wbColumns.indexOf(key),to=from+(left?-1:1);if(from<0||to<0||to>=state.wbColumns.length)return;
  state.wbColumns.splice(to,0,state.wbColumns.splice(from,1)[0]);fcR20Remember('fc_wb_columns',JSON.stringify(state.wbColumns));renderWbOrders();
});

$('wbOrdersHead')?.addEventListener('input',e=>{
  const control=e.target.closest('[data-wb-column-filter]');if(!control)return;state.wbColumnFilters[control.dataset.wbColumnFilter]=control.value;localStorage.setItem(wbColumnFiltersKey(),JSON.stringify(state.wbColumnFilters));renderWbOrderRows();if(control.dataset.wbColumnFilter==='label'){clearTimeout(window.__wbSmartFilterTimer);window.__wbSmartFilterTimer=setTimeout(resetWbPageAndLoad,300);}
});
$('wbOrdersHead')?.addEventListener('change',e=>{
  const control=e.target.closest('[data-wb-column-filter]');if(!control)return;state.wbColumnFilters[control.dataset.wbColumnFilter]=control.value;localStorage.setItem(wbColumnFiltersKey(),JSON.stringify(state.wbColumnFilters));renderWbOrderRows();clearTimeout(window.__wbColumnServerFilterTimer);window.__wbColumnServerFilterTimer=setTimeout(resetWbPageAndLoad,80);
});
$('wbOrdersHead')?.addEventListener('click',e=>{
  const button=e.target.closest('[data-wb-sort-col]');if(!button)return;const key=button.dataset.wbSortCol;
  state.wbSort=state.wbSort?.key===key?{key,dir:state.wbSort.dir==='asc'?'desc':'asc'}:{key,dir:'asc'};fcR20Remember('fc_wb_sort',JSON.stringify(state.wbSort));renderWbOrders();state.wbPage=1;loadWbOrders(false);
});

let __wbDraggedColumn='';
$('wbOrdersHead')?.addEventListener('dragstart',e=>{const th=e.target.closest('[data-drag-col]');if(!th||e.target.closest('[data-resize-col],input,select')){e.preventDefault();return;}__wbDraggedColumn=th.dataset.dragCol;th.classList.add('wb-column-dragging');e.dataTransfer.effectAllowed='move';e.dataTransfer.setData('text/plain',__wbDraggedColumn);});
$('wbOrdersHead')?.addEventListener('dragover',e=>{const th=e.target.closest('[data-drag-col]');if(!th||!__wbDraggedColumn)return;e.preventDefault();e.dataTransfer.dropEffect='move';});
$('wbOrdersHead')?.addEventListener('drop',e=>{const th=e.target.closest('[data-drag-col]');if(!th||!__wbDraggedColumn)return;e.preventDefault();const target=th.dataset.dragCol,from=state.wbColumns.indexOf(__wbDraggedColumn),to=state.wbColumns.indexOf(target);if(from>=0&&to>=0&&from!==to){state.wbColumns.splice(to,0,state.wbColumns.splice(from,1)[0]);fcR20Remember('fc_wb_columns',JSON.stringify(state.wbColumns));renderWbOrders();}__wbDraggedColumn='';});
$('wbOrdersHead')?.addEventListener('dragend',()=>{document.querySelectorAll('.wb-column-dragging').forEach(x=>x.classList.remove('wb-column-dragging'));__wbDraggedColumn='';});

let __wbScrollSync=false;
$('wbOrdersTopScroll')?.addEventListener('scroll',()=>{if(__wbScrollSync)return;__wbScrollSync=true;$('wbOrdersScroll').scrollLeft=$('wbOrdersTopScroll').scrollLeft;requestAnimationFrame(()=>__wbScrollSync=false);});
$('wbOrdersScroll')?.addEventListener('scroll',()=>{if(__wbScrollSync)return;__wbScrollSync=true;$('wbOrdersTopScroll').scrollLeft=$('wbOrdersScroll').scrollLeft;requestAnimationFrame(()=>__wbScrollSync=false);});
window.addEventListener('resize',syncWbHorizontalScroll);

$('wbOrdersHead')?.addEventListener('pointerdown',e=>{
  const handle=e.target.closest('[data-resize-col]');if(!handle)return;
  e.preventDefault();const key=handle.dataset.resizeCol;const th=handle.closest('th');const startX=e.clientX;const startW=th.getBoundingClientRect().width;
  document.body.classList.add('resizing-columns');handle.setPointerCapture?.(e.pointerId);
  const move=ev=>applyWbColumnWidth(key,startW+(ev.clientX-startX));
  const up=()=>{document.removeEventListener('pointermove',move);document.removeEventListener('pointerup',up);document.body.classList.remove('resizing-columns');fcR20Remember('fc_wb_column_widths',JSON.stringify(state.wbColumnWidths));};
  document.addEventListener('pointermove',move);document.addEventListener('pointerup',up,{once:true});
});
$('wbOrdersHead')?.addEventListener('dblclick',e=>{const handle=e.target.closest('[data-resize-col]');if(!handle)return;delete state.wbColumnWidths[handle.dataset.resizeCol];fcR20Remember('fc_wb_column_widths',JSON.stringify(state.wbColumnWidths));renderWbOrders();});

async function refreshWbMetaForVisibleOrders(){
  if(fcUserEditing($('page-marketplaces'))){clearTimeout(state.wbMetaRefreshTimer);state.wbMetaRefreshTimer=setTimeout(refreshWbMetaForVisibleOrders,900);return;}
  const cred=wbCredential();if(!cred||!state.wbOrders.length)return;
  const ids=state.wbOrders.map(o=>Number(o.external_order_id)).filter(Number.isFinite);
  if(!ids.length)return;
  try{const r=await api('/marketplaces/wb/orders/meta-refresh',{method:'POST',body:JSON.stringify({credential_id:cred,order_ids:ids})});if(r.changed)await loadWbOrders(false);}catch(e){console.warn('WB meta refresh:',e)}
}
function scheduleWbMetaRefresh(){
  const now=Date.now();if(now-(state.wbMetaLastAt||0)<60000)return;state.wbMetaLastAt=now;
  clearTimeout(state.wbMetaRefreshTimer);state.wbMetaRefreshTimer=setTimeout(refreshWbMetaForVisibleOrders,1200);
}
function stopWbLiveMode(){
  if(state.wbAutoLocalTimer)clearInterval(state.wbAutoLocalTimer);if(state.wbAutoSyncTimer)clearInterval(state.wbAutoSyncTimer);if(state.wbMetaRefreshTimer)clearTimeout(state.wbMetaRefreshTimer);if(state.wbReconcileTimer)clearInterval(state.wbReconcileTimer);if(state.wbSupplyTimer)clearInterval(state.wbSupplyTimer);
  state.wbAutoLocalTimer=state.wbAutoSyncTimer=state.wbMetaRefreshTimer=state.wbReconcileTimer=state.wbSupplyTimer=null;
}
function wbWorkspaceVisible(){return $('page-marketplaces')?.classList.contains('active')&&!document.hidden;}
function startWbLiveMode(){
  stopWbLiveMode();
  // Local PostgreSQL refresh is cheap and keeps several PCs in sync with each other.
  state.wbAutoLocalTimer=setInterval(()=>{if(wbWorkspaceVisible()&&!state.wbLoadingOrders&&!fcUserEditing($('page-marketplaces')))loadWbOrders(false);},30000);
  // Supplies are a central PostgreSQL view. Every PC refreshes it frequently while the
  // server lets only one caller touch the external WB list during the short cache slot.
  state.wbSupplyTimer=setInterval(()=>{if(wbWorkspaceVisible()&&!state.wbSupplyActionBusy&&!fcUserEditing($('page-marketplaces')))loadWbSupplies(false,false);},30000);
  // External WB sync is throttled server-side, so several PCs cannot multiply requests.
  state.wbAutoSyncTimer=setInterval(()=>{if(wbWorkspaceVisible()&&!fcUserEditing($('page-marketplaces')))syncWbOrders({silent:true,force:false});},60000);
  // Progressive full-workspace reconciliation fills marking codes and order stickers
  // even for orders that are not currently visible on page 1.
  state.wbReconcileTimer=setInterval(async()=>{if(!wbWorkspaceVisible()||!wbCredential()||!state.warehouseId||fcUserEditing($('page-marketplaces')))return;try{const r=await api(`/marketplaces/wb/orders/reconcile?credential_id=${encodeURIComponent(wbCredential())}&warehouse_id=${encodeURIComponent(state.warehouseId)}&limit=200`,{method:'POST'});if(r.changed)await loadWbOrders(false);}catch(e){console.warn('WB reconcile:',e)}},120000);
  setTimeout(()=>{if(wbWorkspaceVisible())syncWbOrders({silent:true,force:false});},1000);
}
document.addEventListener('visibilitychange',()=>{if(wbWorkspaceVisible()&&!fcUserEditing($('page-marketplaces'))){loadWbOrders(false);loadWbSupplies(false,false);}});

const FC_PRODUCT_LABEL_HARD_WIDTH_MM=58;
const FC_PRODUCT_LABEL_HARD_HEIGHT_MM=40;
function labelSettings(){
  const num=(key,def)=>{const raw=localStorage.getItem(key);if(raw===null||raw==='')return def;const v=Number(raw);return Number.isFinite(v)?v:def};
  return {
    // FC_R131_HARD_PRODUCT_LABEL_58X40: product labels are always one physical 58x40 mm label.
    // Workstation/localStorage width and height are intentionally ignored so PACK stations cannot diverge.
    productWidth:FC_PRODUCT_LABEL_HARD_WIDTH_MM,productHeight:FC_PRODUCT_LABEL_HARD_HEIGHT_MM,
    productRotate:num('fc_product_label_rotate',0),productScale:num('fc_product_label_scale',100),
    productOffsetX:num('fc_product_label_offset_x',0),productOffsetY:num('fc_product_label_offset_y',0),
    wbWidth:num('fc_wb_label_width',58),wbHeight:num('fc_wb_label_height',40),wbRotate:num('fc_wb_label_rotate',-1),
    wbScale:num('fc_wb_label_scale',100),wbOffsetX:num('fc_wb_label_offset_x',0),wbOffsetY:num('fc_wb_label_offset_y',0)
  };
}
function wbPrintSettings(){
  const mode=$('wbPrintMode')?.value||state.wbPrintMode||'agent';
  const station=($('wbPrintStation')?.value||state.wbPrintStation||'PACK-01').trim().toUpperCase();
  return {mode,station,...labelSettings()};
}
function scannerProfile(){
  const p=state.workstationProfile?.scanner_profile||{};
  // Conservative workstation defaults remain compatible with scanners that do not
  // append Enter. Fast USB-HID bursts are detected separately in scheduleWbScanner(),
  // so existing saved profiles do not have to be rewritten on every PC.
  return {product_idle_ms:Number(p.product_idle_ms||180),product_max_idle_ms:Number(p.product_max_idle_ms||1000),chz_idle_ms:Number(p.chz_idle_ms||700),chz_max_idle_ms:Number(p.chz_max_idle_ms||1800),receiving_idle_ms:Number(p.receiving_idle_ms||260),receiving_max_idle_ms:Number(p.receiving_max_idle_ms||1000)};
}
function workstationPrinterProfile(){
  const current={...(state.workstationProfile?.printer_profile||{})};const x=labelSettings();
  return {...current,product_label_width:x.productWidth,product_label_height:x.productHeight,product_label_rotate:x.productRotate,product_label_scale:x.productScale,product_label_offset_x:x.productOffsetX,product_label_offset_y:x.productOffsetY,wb_label_width:x.wbWidth,wb_label_height:x.wbHeight,wb_label_rotate:x.wbRotate,wb_label_scale:x.wbScale,wb_label_offset_x:x.wbOffsetX,wb_label_offset_y:x.wbOffsetY};
}
async function loadWorkstationProfile(station=state.wbPrintStation,timeoutMs=12000){
  const code=String(station||'PACK-01').trim().toUpperCase();if(!code)return null;
  const profile=await api('/system/workstations/'+encodeURIComponent(code),{timeoutMs});state.workstationProfile=profile;
  const p=profile.printer_profile||{};const map={product_label_width:'fc_product_label_width',product_label_height:'fc_product_label_height',product_label_rotate:'fc_product_label_rotate',product_label_scale:'fc_product_label_scale',product_label_offset_x:'fc_product_label_offset_x',product_label_offset_y:'fc_product_label_offset_y',wb_label_width:'fc_wb_label_width',wb_label_height:'fc_wb_label_height',wb_label_rotate:'fc_wb_label_rotate',wb_label_scale:'fc_wb_label_scale',wb_label_offset_x:'fc_wb_label_offset_x',wb_label_offset_y:'fc_wb_label_offset_y'};
  Object.entries(map).forEach(([serverKey,localKey])=>{if(p[serverKey]!==undefined&&p[serverKey]!==null)localStorage.setItem(localKey,String(p[serverKey]));});
  loadPrintSettingsUi();return profile;
}
async function syncWorkstationProfile(){
  const station=String(state.wbPrintStation||'PACK-01').trim().toUpperCase();if(!station)return;
  // Preserve capabilities published by Print Agent (DPI/language/gap/offsets) even if
  // the browser saves label settings before the workstation profile finished loading.
  if(!state.workstationProfile||String(state.workstationProfile.station_code||'').toUpperCase()!==station){
    try{state.workstationProfile=await api('/system/workstations/'+encodeURIComponent(station),{timeoutMs:12000});}catch(_){/* local cache remains the safe fallback */}
  }
  const scanner={...scannerProfile(),submit_on_enter:true,adaptive_idle:true};
  state.workstationProfile=await api('/system/workstations/'+encodeURIComponent(station),{method:'PUT',body:JSON.stringify({client_id:state.clientId||null,warehouse_id:state.warehouseId||null,printer_profile:workstationPrinterProfile(),scanner_profile:scanner}),timeoutMs:15000});
}
function scheduleWorkstationProfileSync(){clearTimeout(state.workstationProfileTimer);state.workstationProfileTimer=setTimeout(()=>syncWorkstationProfile().catch(e=>console.warn('workstation profile:',e)),400);}

function loadPrintSettingsUi(){
  const x=labelSettings();if(!$('productLabelWidth'))return;
  $('productLabelWidth').value=FC_PRODUCT_LABEL_HARD_WIDTH_MM;$('productLabelHeight').value=FC_PRODUCT_LABEL_HARD_HEIGHT_MM;
  $('productLabelWidth').readOnly=true;$('productLabelHeight').readOnly=true;
  $('productLabelWidth').title='Жесткий размер товарной этикетки: 58 мм';$('productLabelHeight').title='Жесткий размер товарной этикетки: 40 мм';
  $('productLabelRotate').value=String(x.productRotate);$('productLabelScale').value=x.productScale;$('productLabelOffsetX').value=x.productOffsetX;$('productLabelOffsetY').value=x.productOffsetY;
  $('wbLabelWidth').value=x.wbWidth;$('wbLabelHeight').value=x.wbHeight;$('wbLabelRotate').value=String(x.wbRotate);$('wbLabelScale').value=x.wbScale;$('wbLabelOffsetX').value=x.wbOffsetX;$('wbLabelOffsetY').value=x.wbOffsetY;
}
function addPrintAgentHelpLink(){
  const page=$('page-printsettings');if(!page||page.querySelector('[data-print-agent-help]'))return;
  document.querySelectorAll('[data-print-agent-help]').forEach(x=>x.remove());
  const card=document.createElement('section');card.className='card print-agent-settings-card';card.dataset.printAgentHelp='1';
  card.innerHTML=`<div class="panel-head"><div><span class="kicker">PRINT AGENT 1.1.8 STABLE · CORE 1.1.7 · FAST 150 MS</span><h2>Установка и подключение</h2></div></div><p>Архив, инструкция и токен доступны здесь после входа в FulfillCore. Подключаться к серверу по SSH больше не требуется.</p><div class="button-row"><a class="primary" href="/help/print-agent" target="_blank" rel="noopener">Установка и скачивание</a><button type="button" class="secondary" data-print-agent-token-load>Показать токен</button></div><div class="token-row hidden" data-print-agent-token-row><input class="token-value" data-print-agent-token-value type="password" readonly autocomplete="off" aria-label="Токен Print Agent"/><button type="button" class="mini secondary" data-print-agent-token-toggle>Показать</button><button type="button" class="mini primary" data-print-agent-token-copy>Скопировать</button></div><small class="security-note">Токен загружается только по нажатию, не записывается в ZIP, Word-файл или localStorage браузера.</small>`;
  page.appendChild(card);
  card.querySelector('[data-print-agent-token-load]').addEventListener('click',async e=>{const btn=e.currentTarget;try{btn.disabled=true;const station=(state.wbPrintStation||'PACK-01').trim().toUpperCase();const r=await api('/help/print-agent/token?station_code='+encodeURIComponent(station));const input=card.querySelector('[data-print-agent-token-value]');input.value=r.token||'';card.querySelector('[data-print-agent-token-row]').classList.remove('hidden');btn.textContent='Обновить токен';}catch(err){toast(err.message,true)}finally{btn.disabled=false;}});
  card.querySelector('[data-print-agent-token-toggle]').addEventListener('click',e=>{const input=card.querySelector('[data-print-agent-token-value]');const show=input.type==='password';input.type=show?'text':'password';e.currentTarget.textContent=show?'Скрыть':'Показать';});
  card.querySelector('[data-print-agent-token-copy]').addEventListener('click',async()=>{const input=card.querySelector('[data-print-agent-token-value]');if(!input.value){toast('Сначала нажмите «Показать токен»',true);return;}try{await navigator.clipboard.writeText(input.value);toast('Токен Print Agent скопирован');}catch(_){input.type='text';input.select();document.execCommand('copy');input.type='password';toast('Токен Print Agent скопирован');}});
}
function savePrintSettingsUi(){
  const vals={
    fc_product_label_width:FC_PRODUCT_LABEL_HARD_WIDTH_MM,fc_product_label_height:FC_PRODUCT_LABEL_HARD_HEIGHT_MM,fc_product_label_rotate:Number($('productLabelRotate').value||0),fc_product_label_scale:Number($('productLabelScale').value||100),fc_product_label_offset_x:Number($('productLabelOffsetX').value||0),fc_product_label_offset_y:Number($('productLabelOffsetY').value||0),
    fc_wb_label_width:Number($('wbLabelWidth').value||58),fc_wb_label_height:Number($('wbLabelHeight').value||40),fc_wb_label_rotate:Number($('wbLabelRotate').value??-1),fc_wb_label_scale:Number($('wbLabelScale').value||100),fc_wb_label_offset_x:Number($('wbLabelOffsetX').value||0),fc_wb_label_offset_y:Number($('wbLabelOffsetY').value||0)
  };
  Object.entries(vals).forEach(([k,v])=>localStorage.setItem(k,String(v)));loadPrintSettingsUi();scheduleWorkstationProfileSync();toast('Настройки печати сохранены');
}
const PRINT_SETTING_KEYS=['fc_product_label_width','fc_product_label_height','fc_product_label_rotate','fc_product_label_scale','fc_product_label_offset_x','fc_product_label_offset_y','fc_wb_label_width','fc_wb_label_height','fc_wb_label_rotate','fc_wb_label_scale','fc_wb_label_offset_x','fc_wb_label_offset_y','fc_product_label_orientation'];
$('savePrintSettingsBtn')?.addEventListener('click',savePrintSettingsUi);
$('resetPrintSettingsBtn')?.addEventListener('click',()=>{PRINT_SETTING_KEYS.forEach(k=>localStorage.removeItem(k));loadPrintSettingsUi();toast('Возвращены точные настройки 58×40');});
loadPrintSettingsUi();
addPrintAgentHelpLink();
loadWorkstationProfile(state.wbPrintStation).catch(()=>scheduleWorkstationProfileSync());
async function printCalibration(kind){
  savePrintSettingsUi();const ps=wbPrintSettings();const product=kind==='product';
  const width=product?ps.productWidth:ps.wbWidth,height=product?ps.productHeight:ps.wbHeight,rotate=product?ps.productRotate:0,scale=product?ps.productScale:ps.wbScale,ox=product?ps.productOffsetX:ps.wbOffsetX,oy=product?ps.productOffsetY:ps.wbOffsetY;
  const qs=`kind=${encodeURIComponent(kind)}&width_mm=${width}&height_mm=${height}&rotate=${rotate}&scale_percent=${scale}&offset_x_mm=${ox}&offset_y_mm=${oy}`;
  if(ps.mode==='agent'){const cred=wbCredential();if(!cred)throw new Error('Для теста через Print Agent выберите кабинет WB');await api(`/marketplaces/wb/print-test/queue?${qs}`,{method:'POST',body:JSON.stringify({credential_id:cred,print_station:ps.station})});toast('Тестовая этикетка отправлена на '+ps.station);return;}
  const blob=await apiBlob(`/marketplaces/wb/print-test.pdf?${qs}`);printBlob(blob,`FulfillCore_${kind}_test_58x40.pdf`);
}
$('testProductLabelBtn')?.addEventListener('click',()=>printCalibration('product').catch(e=>toast(e.message,true)));
$('testWbLabelBtn')?.addEventListener('click',()=>printCalibration('wb').catch(e=>toast(e.message,true)));
async function refreshWbOrderMetaStrict(localOrderId,{requireSticker=false}={}){
  const cred=wbCredential();if(!cred)throw new Error('Не выбран кабинет WB');
  const wh=state.warehouseId?`&warehouse_id=${encodeURIComponent(state.warehouseId)}`:'';
  let cached=state.wbOrders.find(x=>String(x.id)===String(localOrderId));
  let externalId=cached?.external_order_id;
  if(!externalId){
    const local=await api(`/marketplaces/wb/orders/${encodeURIComponent(localOrderId)}?credential_id=${encodeURIComponent(cred)}${wh}`,{timeoutMs:12000});
    externalId=local?.external_order_id;
  }
  const orderId=Number(externalId);
  if(!Number.isFinite(orderId))throw new Error('Не удалось определить номер заказа WB для проверки этикетки');
  await api('/marketplaces/wb/orders/meta-refresh',{method:'POST',timeoutMs:15000,body:JSON.stringify({credential_id:cred,order_ids:[orderId]})});
  const fresh=await api(`/marketplaces/wb/orders/${encodeURIComponent(localOrderId)}?credential_id=${encodeURIComponent(cred)}${wh}`,{timeoutMs:12000});
  if(Number(fresh?.external_order_id)!==orderId)throw new Error('Защитная проверка: номер заказа после обновления не совпал с WB');
  if(requireSticker){
    const sticker=fresh?.order_sticker||{};
    if(!fresh?.order_sticker_available||!String(sticker.barcode||'').trim()||sticker.part_a===null||sticker.part_a===undefined){
      throw new Error('WB не подтвердил актуальную этикетку этого заказа. Печать заблокирована, чтобы не отправить товар с чужой или старой этикеткой.');
    }
  }
  return fresh;
}
async function printWbOrderLabel(localOrderId,{autoAfterChz=false}={}){
  const cred=wbCredential();if(!cred)throw new Error('Не выбран кабинет WB');
  // r99 safety: every print is preceded by a single-order live WB metadata refresh.
  // Never trust a cached sticker for shipment.
  await refreshWbOrderMetaStrict(localOrderId,{requireSticker:true});
  const ps=wbPrintSettings();
  if(ps.mode==='agent'){
    await api(`/marketplaces/wb/orders/${encodeURIComponent(localOrderId)}/label/queue?width_mm=${ps.wbWidth}&height_mm=${ps.wbHeight}&rotate=${ps.wbRotate}&scale_percent=${ps.wbScale}&offset_x_mm=${ps.wbOffsetX}&offset_y_mm=${ps.wbOffsetY}&auto_after_chz=${autoAfterChz?1:0}`,{method:'POST',body:JSON.stringify({credential_id:cred,print_station:ps.station})});
    return;
  }
  const blob=await apiBlob(`/marketplaces/wb/orders/${encodeURIComponent(localOrderId)}/label.pdf?credential_id=${encodeURIComponent(cred)}&reprint=1&width_mm=${ps.wbWidth}&height_mm=${ps.wbHeight}&rotate=${ps.wbRotate}&scale_percent=${ps.wbScale}&offset_x_mm=${ps.wbOffsetX}&offset_y_mm=${ps.wbOffsetY}`);
  printBlob(blob,`WB_${localOrderId}.pdf`);
}
async function printWbProductLabel(localOrderId){
  const cred=wbCredential();if(!cred)throw new Error('Не выбран кабинет WB');
  const ps=wbPrintSettings();
  if(ps.mode==='agent'){
    await api(`/marketplaces/wb/orders/${encodeURIComponent(localOrderId)}/product-label/queue?width_mm=${ps.productWidth}&height_mm=${ps.productHeight}&rotate=${ps.productRotate}&scale_percent=${ps.productScale}&offset_x_mm=${ps.productOffsetX}&offset_y_mm=${ps.productOffsetY}`,{method:'POST',body:JSON.stringify({credential_id:cred,print_station:ps.station})});
    return;
  }
  const blob=await apiBlob(`/marketplaces/wb/orders/${encodeURIComponent(localOrderId)}/product-label.pdf?credential_id=${encodeURIComponent(cred)}&width_mm=${ps.productWidth}&height_mm=${ps.productHeight}&rotate=${ps.productRotate}&scale_percent=${ps.productScale}&offset_x_mm=${ps.productOffsetX}&offset_y_mm=${ps.productOffsetY}`);
  printBlob(blob,`WB_product_${localOrderId}.pdf`);
}
function closeWbOrderModal(){$('wbOrderModal')?.classList.add('hidden');}
// FC_K9R58_SENT_USES_STRICT_ENDPOINT
async function setWbOrderStatus(orderNum,target){
  const ids=[Number(orderNum)];
  const r=target==='SENT'
    ?await api(`/marketplaces/wb/orders/mark-sent?warehouse_id=${encodeURIComponent(state.warehouseId)}`,{method:'POST',body:JSON.stringify({credential_id:wbCredential(),order_ids:ids})})
    :await api('/marketplaces/wb/orders/manual-status',{method:'POST',body:JSON.stringify({credential_id:wbCredential(),warehouse_id:state.warehouseId,order_ids:ids,target_status:target})});
  if(target==='SENT'&&Number(r?.verified||0)!==ids.length)throw new Error('Сервер не подтвердил фиксацию статуса «Отправлены». Обновите страницу и повторите.');
  await loadWbOrders();return r;
}
function compatibleActiveWbSupplies(order){const wbId=String(order?.wb_warehouse_id??'');return (state.wbSupplies||[]).filter(s=>!s.done&&(!s._fc_wb_warehouse_id||!wbId||String(s._fc_wb_warehouse_id)===wbId));}
function wbOrderSupplyAttachHtml(order){if(order?.supply_id)return '';const supplies=compatibleActiveWbSupplies(order);if(!supplies.length)return `<div class="order-chz-box"><small>Поставка WB</small><div class="button-row"><b>Заказ пока без поставки.</b><button type="button" class="secondary" data-order-detail-refresh-supplies="${esc(order.id)}">Обновить поставки</button></div><small class="subline">Выберите активную поставку выбранного склада WB и привяжите заказ прямо из FulfillCore.</small></div>`;return `<div class="order-chz-box"><small>Поставка WB</small><div class="inline-controls"><select id="wbOrderAttachSupplySelect">${supplies.map(s=>`<option value="${esc(s.id)}">${esc(s.name||s.id)} · ${Number(s._fc_order_count||0)} заказов</option>`).join('')}</select><button type="button" class="primary" data-order-detail-attach-supply="${esc(order.id)}">Привязать к поставке</button></div><small class="subline">FulfillCore отправит этот orderId в WB и сохранит связь только после подтверждения WB. Статус «Упаковано» не сбрасывается.</small></div>`;}
async function openWbOrder(localId){
  try{
    const wh=state.warehouseId?`&warehouse_id=${encodeURIComponent(state.warehouseId)}`:'';
    let o,metaError='';
    try{
      // r99 safety: the card waits for a single-order WB refresh before showing a sticker.
      o=await refreshWbOrderMetaStrict(localId,{requireSticker:false});
      loadWbOrders(false).catch(()=>{});
    }catch(err){
      console.warn('WB order live meta refresh:',err);
      metaError=String(err?.message||err||'WB недоступен');
      o=await api(`/marketplaces/wb/orders/${encodeURIComponent(localId)}?credential_id=${encodeURIComponent(wbCredential())}${wh}`);
      // Fail closed: a cached sticker is not shown as current when WB could not verify it.
      o={...o,order_sticker_available:false,order_sticker:null,_wb_meta_refresh_error:metaError};
    }
    const watch=o.wb_warehouse_mode==='WATCH';const sku=o.local_sku||{};const sticker=o.order_sticker||{};
    const createdText=o.created_at?new Date(o.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'—';
    let ageText='—';if(o.created_at){const ms=Math.max(0,Date.now()-new Date(o.created_at).getTime()),mins=Math.floor(ms/60000),days=Math.floor(mins/1440),hours=Math.floor((mins%1440)/60),rem=mins%60;ageText=days>0?`${days} д ${hours} ч`:hours>0?`${hours} ч ${rem} мин`:`${rem} мин`;}
    const events=(o.events||[]).map(ev=>`<div class="order-event"><b>${esc(ev.type)}</b><span>${ev.created_at?new Date(ev.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):''}</span>${ev.value?`<small>${esc(ev.value)}</small>`:''}</div>`).join('');
    $('wbOrderModalBody').innerHTML=`<span class="kicker">WILDBERRIES FBS</span><h2>Заказ №${esc(o.external_order_id)}</h2>
      <div class="order-detail-grid">
        <div><small>Статус FulfillCore</small><b>${esc(o.local_status_name||o.local_status)}</b></div>
        <div><small>Статус WB</small><b>${esc(o.wb_status_name||o.wb_status||o.supplier_status||'—')}</b></div>
        <div><small>Склад WB</small><b>${esc(o.wb_warehouse_name||o.wb_warehouse_id||'—')}</b></div>
        <div><small>Поставка</small><b>${esc(o.supply_id||'—')}</b></div>
        <div><small>Этикетка заказа</small><b>${o.order_sticker_available?`${esc(sticker.part_a??'')}${sticker.part_b!==null&&sticker.part_b!==undefined?' / '+esc(sticker.part_b):''}`:'—'}</b>${sticker.barcode?`<small class="subline mono-wrap">${esc(sticker.barcode)}</small>`:''}${o._wb_meta_refresh_error?`<small class="subline" style="color:#b42318">Не подтверждена WB: ${esc(o._wb_meta_refresh_error)}</small>`:`<small class="subline" style="color:#067647">Проверено по WB перед открытием</small>`}</div>
        <div><small>Артикул</small><b>${esc(o.article||sku.article||'—')}</b></div>
        <div><small>Размер</small><b>${esc(o.size||o.product_card?.size||sku.size||'—')}</b></div>
        <div><small>ШК товара</small><b class="mono-wrap">${esc((o.barcodes||[]).join(', ')||sku.barcode||'—')}</b></div>
        <div><small>Резерв</small><b>${esc(reservedText(o.reserved))}</b></div>
        <div><small>Заказ создан WB</small><b>${esc(createdText)}</b></div>
        <div><small>${o.local_status==='NEW'?'В статусе «Новые»':'Возраст заказа'}</small><b>${esc(ageText)}</b></div>
      </div>
      ${wbOrderSupplyAttachHtml(o)}
      <div class="order-chz-box"><small>Код маркировки</small><div class="mono-wrap">${o.chz_code?esc(o.chz_code):'<span class="muted">Код маркировки не присвоен</span>'}</div>${o.chz_pending_wb?`<small class="subline" style="color:#b56a00">⏳ ЧЗ сохранён в FulfillCore. WB ещё не подтвердил код; повторная отправка выполняется автоматически.${o.chz_pending_error?' Последний ответ: '+esc(String(o.chz_pending_error).slice(0,220)):''}</small>`:''}${watch?'':`<div class="inline-controls mt10"><input id="wbOrderManualChzInput" type="text" placeholder="Отсканируйте или вставьте ЧЗ для ручной привязки" autocomplete="off"/><button class="secondary" data-order-detail-save-chz="${o.id}">Сохранить / заменить ЧЗ</button>${o.chz_pending_wb&&o.chz_code?`<button class="primary" data-order-detail-retry-chz="${o.id}">Повторить ЧЗ в WB</button>`:''}</div>`}</div>
      ${watch?' <div class="success-box">Склад включён в режим «Наблюдение». Изменения заблокированы.</div>':`<div class="button-row order-detail-actions"><button class="secondary" data-order-detail-product="${o.id}">Печать ШК товара</button>${o.local_status==='PACKED'||o.order_sticker_available||['confirm','complete'].includes(o.supplier_status)?`<button class="secondary" data-order-detail-label="${o.id}">Этикетка WB</button>`:''}${o.chz_attached?`<button class="danger" data-order-detail-delete-chz="${o.id}">Отвязать ЧЗ</button>`:''}<label class="wb-status-control order-status-control"><span>Статус FulfillCore</span><select data-order-detail-status-select data-order-num="${esc(o.external_order_id)}">${['NEW','PICKING','PACKED','SENT'].includes(o.local_status)?'':'<option value="" selected>Выберите статус…</option>'}<option value="NEW" ${o.local_status==='NEW'?'selected':''}>Новые</option><option value="PICKING" ${o.local_status==='PICKING'?'selected':''}>На сборке</option><option value="PACKED" ${o.local_status==='PACKED'?'selected':''}>Упаковано</option><option value="SENT" ${o.local_status==='SENT'?'selected':''}>Отправлены</option></select></label></div>`}
      <h3 class="mt18">История заказа</h3><div class="order-events">${events||'<span class="muted">История пока пустая</span>'}</div>`;
    $('wbOrderModal').classList.remove('hidden');
  }catch(err){toast(err.message,true)}
}
$('wbOrdersBody').addEventListener('click',async e=>{
  const open=e.target.closest('[data-wb-order-open]'),product=e.target.closest('[data-wb-product-label]'),label=e.target.closest('[data-wb-order-label]'),del=e.target.closest('[data-wb-delete-chz]');
  try{
    if(open){await openWbOrder(open.dataset.wbOrderOpen);return;}
    if(product){await printWbProductLabel(product.dataset.wbProductLabel);toast('Этикетка с ШК товара отправлена на печать');}
    if(label){await printWbOrderLabel(label.dataset.wbOrderLabel);await loadWbOrders();toast('Этикетка WB отправлена на печать');}
    if(del){if(!confirm('Отвязать ЧЗ от этого заказа?\n\nДля действующего заказа FulfillCore сначала удалит ЧЗ в Wildberries. Для уже отменённого заказа локальная связь будет снята только после подтверждения отмены.'))return;const r=await api(`/marketplaces/wb/orders/${encodeURIComponent(del.dataset.wbDeleteChz)}/chz?credential_id=${encodeURIComponent(wbCredential())}&warehouse_id=${encodeURIComponent(state.warehouseId)}`,{method:'DELETE'});if(state.wbActivePackingOrder?.id===del.dataset.wbDeleteChz)resetWbPacking(false);await loadWbOrders();toast(r.message||'ЧЗ отвязан');}
  }catch(err){toast(err.message,true)}
});
$('wbOrderModal')?.addEventListener('click',async e=>{
  if(e.target.closest('[data-close-order-modal]')){closeWbOrderModal();return;}
  const product=e.target.closest('[data-order-detail-product]'),label=e.target.closest('[data-order-detail-label]'),del=e.target.closest('[data-order-detail-delete-chz]'),saveChz=e.target.closest('[data-order-detail-save-chz]'),retryChz=e.target.closest('[data-order-detail-retry-chz]'),attachSupply=e.target.closest('[data-order-detail-attach-supply]'),refreshSupplies=e.target.closest('[data-order-detail-refresh-supplies]'),st=e.target.closest('[data-order-detail-status]'),sent=e.target.closest('[data-order-detail-manual-sent]');
  try{
    if(product){await printWbProductLabel(product.dataset.orderDetailProduct);toast('Этикетка с ШК товара отправлена на печать');}
    if(label){await printWbOrderLabel(label.dataset.orderDetailLabel);toast('Этикетка WB отправлена на печать');}
    if(refreshSupplies){await loadWbSupplies(true,true);await openWbOrder(refreshSupplies.dataset.orderDetailRefreshSupplies);return;}
    if(attachSupply){const supplyId=$('wbOrderAttachSupplySelect')?.value;if(!supplyId)throw new Error('Выберите активную поставку WB');if(!confirm(`Привязать этот заказ к поставке ${supplyId}?\n\nFulfillCore сначала получит подтверждение WB. Остатки и статус упаковки не изменятся.`))return;await api(`/warehouse-ui/wb/orders/${encodeURIComponent(attachSupply.dataset.orderDetailAttachSupply)}/attach-supply`,{method:'PATCH',timeoutMs:45000,body:JSON.stringify({credential_id:wbCredential(),warehouse_id:state.warehouseId,supply_id:supplyId,wb_warehouse_id:state.wbWarehouseFilter?Number(state.wbWarehouseFilter):null})});await Promise.allSettled([loadWbOrders(false),loadWbSupplies(false,true)]);toast(`Заказ привязан к поставке ${supplyId}`);await openWbOrder(attachSupply.dataset.orderDetailAttachSupply);return;}
    if(del){if(!confirm('Отвязать ЧЗ от этого заказа?\n\nДействующий заказ: сначала подтверждение Wildberries. Отменённый заказ: статус останется отменённым.'))return;const r=await api(`/marketplaces/wb/orders/${encodeURIComponent(del.dataset.orderDetailDeleteChz)}/chz?credential_id=${encodeURIComponent(wbCredential())}&warehouse_id=${encodeURIComponent(state.warehouseId)}`,{method:'DELETE'});closeWbOrderModal();await loadWbOrders();toast(r.message||'ЧЗ отвязан');}
    if(saveChz){const input=$('wbOrderManualChzInput'),chz=input?.value||'';if(!chz)throw new Error('Отсканируйте или вставьте ЧЗ');const r=await api(`/marketplaces/wb/orders/${encodeURIComponent(saveChz.dataset.orderDetailSaveChz)}/chz/manual`,{method:'POST',body:JSON.stringify({credential_id:wbCredential(),warehouse_id:state.warehouseId,chz,replace:true})});toast(r.message||'ЧЗ отправлен в Wildberries',Boolean(r.wb_chz_pending&&!r.wb_chz_submitted));await loadWbOrders();await openWbOrder(saveChz.dataset.orderDetailSaveChz);}
    if(retryChz){
      const localOrderId=retryChz.dataset.orderDetailRetryChz;retryChz.disabled=true;
      const r=await api(`/marketplaces/wb/orders/${encodeURIComponent(localOrderId)}/chz/retry`,{method:'POST',body:JSON.stringify({credential_id:wbCredential(),warehouse_id:state.warehouseId})});
      let printError=null;
      if(r.print_ready&&r.order?.id){try{await printWbOrderLabel(r.order.id,{autoAfterChz:true});}catch(err){printError=err;}}
      const baseMessage=r.message||'ЧЗ отправлен в Wildberries';
      const message=printError?`${baseMessage} Автоматическая печать не удалась: ${printError.message}. Нажмите «Этикетка WB».`:r.print_ready?`${baseMessage} Этикетка отправлена на печать.`:baseMessage;
      toast(message,Boolean(printError||(r.wb_chz_pending&&!r.wb_chz_submitted)));
      await loadWbOrders();await openWbOrder(localOrderId);
    }
    if(sent){if(!confirm('Перевести этот упакованный заказ в «Отправленные» внутри FulfillCore? WB не изменится. Физический остаток повторно списываться не будет.'))return;const orderNum=Number(sent.dataset.orderDetailManualSent);const r=await api(`/marketplaces/wb/orders/mark-sent?warehouse_id=${encodeURIComponent(state.warehouseId)}`,{method:'POST',body:JSON.stringify({credential_id:wbCredential(),order_ids:[orderNum]})});applyWbLocalStatusOptimistic([orderNum],'SENT');closeWbOrderModal();await loadWbOrders();toast(r.changed?'Заказ переведён в «Отправленные»':'Заказ уже был отправлен');}
    if(st){await setWbOrderStatus(st.dataset.orderNum,st.dataset.orderDetailStatus);closeWbOrderModal();toast(st.dataset.orderDetailStatus==='NEW'?'Заказ возвращён в новые':'Заказ переведён на сборку');}
  }catch(err){toast(err.message,true)}
});

$('wbOrderModal')?.addEventListener('change',async e=>{
  const select=e.target.closest('[data-order-detail-status-select]');if(!select)return;
  const target=select.value;const orderNum=select.dataset.orderNum;if(!target||!orderNum)return;
  select.disabled=true;
  try{await setWbOrderStatus(orderNum,target);toast(`Статус заказа изменён: ${target==='NEW'?'Новые':target==='PICKING'?'На сборке':target==='PACKED'?'Упаковано':'Отправлены'}.`);closeWbOrderModal();}
  catch(err){toast(err.message,true);select.disabled=false;}
});

$('wbPickingListBtn').addEventListener('click',async()=>{const btn=$('wbPickingListBtn');if(btn?.dataset.busy==='1')return;if(btn){btn.dataset.busy='1';btn.disabled=true;btn.dataset.oldText=btn.textContent;btn.textContent='Формируем Excel…';}let moveCommitted=false;try{
  const ids=selectedValues('.wb-order-check').map(Number).filter(Number.isFinite);
  const selectedRows=state.wbOrders.filter(o=>ids.includes(Number(o.external_order_id)));
  const selectedAllowed=selectedRows.length>0&&selectedRows.every(o=>['NEW','PICKING'].includes(o.local_status));
  let scope='SELECTED';
  if(['NEW','PICKING'].includes(state.wbOrderFilter)){
    const statusTitle=state.wbOrderFilter==='NEW'?'«Новые»':'«На сборке»';
    if(!selectedAllowed){
      scope=state.wbOrderFilter;
    }else if(confirm(`Создать сборочный лист из всех заказов вкладки ${statusTitle} (${state.wbTotal})?\n\nОК — все заказы этой вкладки.\nОтмена — только отмеченные (${ids.length}).`)){
      scope=state.wbOrderFilter;
    }
  }else if(!state.wbOrderFilter){
    const activeTotal=Number(state.wbOrderCounts?.NEW||0)+Number(state.wbOrderCounts?.PICKING||0);
    if(!selectedAllowed){
      scope='ACTIVE';
    }else if(confirm(`Создать сборочный лист из всех новых и всех заказов «На сборке» (${activeTotal})?\n\nОК — все рабочие заказы.\nОтмена — только отмеченные (${ids.length}).`)){
      scope='ACTIVE';
    }
  }
  if(scope==='SELECTED'&&!selectedAllowed)throw new Error('Отметьте заказы только из статусов «Новые» или «На сборке».');

  const includesNew=scope==='NEW'||scope==='ACTIVE'||(scope==='SELECTED'&&selectedRows.some(o=>o.local_status==='NEW'));
  const includesPicking=scope==='PICKING'||scope==='ACTIVE'||(scope==='SELECTED'&&selectedRows.some(o=>o.local_status==='PICKING'));
  const moveToPicking=includesNew?confirm('Перевести новые заказы из этого сборочного листа в «На сборке»?\n\nОК — скачать Excel и перевести.\nОтмена — только скачать Excel, статусы новых заказов не менять.\n\nЗаказы, которые уже «На сборке», можно включать повторно: их прежние ячейки будут взяты из предыдущего сборочного листа без повторного списания.'):false;
  const pickingLayout=String(ensureWbPickingLayoutControl()?.value||'CELL').toUpperCase()==='TIME'?'TIME':'CELL';
  await ensureContext();
  const name=prompt('Название сборочного листа:',`WB сборка ${new Date().toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})}`);if(name===null)return;
  const q=new URLSearchParams({scope,move_to_picking:moveToPicking?'true':'false',layout:pickingLayout});
  const selectedWh=state.wbWarehouseFilter||$('wbWarehouseView')?.value||'';if(selectedWh)q.set('wb_warehouse_id',selectedWh);
  const cf=state.wbColumnFilters||{},label=String(cf.label||'').trim(),chzState=String(cf.chz_state||'').trim();
  if(label)q.set('label',label);if(chzState)q.set('chz_state',chzState);
  for(const brand of (cf.brands||[])){if(String(brand||'').trim())q.append('brand',String(brand).trim());}
  // WBPickingListRequest in older base installations requires a non-empty list.
  // Scope NEW/PICKING/ACTIVE is resolved on the server, so a harmless placeholder
  // keeps the cumulative hotpatch compatible with that original schema.
  const requestIds=scope==='SELECTED'?ids:(ids.length?ids:[1]);
  const requestBody={credential_id:wbCredential(),warehouse_id:state.warehouseId,order_ids:requestIds,name:name||null};
  let blob;
  let skippedIds=[];
  if(moveToPicking){
    // FC_K9R14_BATCHED_PICKING_COMMIT_PROGRESS
    // Resolve the exact filtered set without writes, then commit bounded SELECTED
    // batches.  Each batch owns a short transaction and returns before the browser
    // timeout, so 500-1000 orders no longer sit in one monolithic request.
    const planQ=new URLSearchParams(q);planQ.set('plan_only','true');planQ.set('move_to_picking','true');
    if(btn)btn.textContent='Рассчитываем ячейки один раз…';
    const plan=await api(`/marketplaces/wb/picking-lists.xlsx?${planQ.toString()}`,{method:'POST',body:JSON.stringify(requestBody),timeoutMs:180000});
    const plannedIds=[...new Set((plan?.order_ids||[]).map(Number).filter(Number.isFinite))]; // FC_K9R61_PICKING_PLAN_DEDUP
    const planToken=String(plan?.plan_token||'').trim();
    if(!planToken)throw new Error('Сервер не вернул подготовленный план сборки.');
    if(!plannedIds.length)throw new Error('Сервер не вернул заказы для перевода «На сборку».');
    const batchSize=75,committedIds=[];
    for(let offset=0;offset<plannedIds.length;offset+=batchSize){
      const batch=plannedIds.slice(offset,offset+batchSize);
      const batchQ=new URLSearchParams({scope:'SELECTED',move_to_picking:'true',commit_only:'true',layout:pickingLayout,plan_token:planToken});
      if(selectedWh)batchQ.set('wb_warehouse_id',selectedWh);
      if(btn)btn.textContent=`Закрепляем резерв ${Math.min(offset+batch.length,plannedIds.length)}/${plannedIds.length}…`;
      try{
        const committed=await api(`/marketplaces/wb/picking-lists.xlsx?${batchQ.toString()}`,{method:'POST',body:JSON.stringify({...requestBody,order_ids:batch,name:plannedIds.length>batchSize?`${name||'WB сборка'} · ${Math.floor(offset/batchSize)+1}/${Math.ceil(plannedIds.length/batchSize)}`:name}),timeoutMs:120000});
        const done=[...new Set((committed?.order_ids||[]).map(Number).filter(Number.isFinite))];
        const skipped=[...new Set((committed?.skipped_order_ids||[]).map(Number).filter(Number.isFinite))];
        const verified=Number(committed?.verified_picking??-1);
        // FC_K9R61_PICKING_SERVER_VERIFICATION: never invent success from the requested
        // batch. The server returns only order numbers whose logical rows were verified
        // as PICKING in the same transaction.
        if(verified!==done.length)throw new Error(`Сервер подтвердил перевод «На сборку» не полностью: ${verified}/${done.length}.`);
        if(!done.length&&!skipped.length)throw new Error('Сервер не подтвердил ни одного заказа этой пачки и не вернул причины пропуска.');
        committedIds.push(...done);
        skippedIds.push(...skipped);
        moveCommitted=moveCommitted||done.length>0;
      }catch(batchError){
        if(committedIds.length){
          batchError.statusCommitted=true;
          batchError.message=`Переведено ${committedIds.length} из ${plannedIds.length}. Ошибка на следующей порции: ${batchError.message||'ошибка сервера'}. Повторите формирование — уже переведённые заказы повторно не спишутся.`;
        }
        throw batchError;
      }
    }
    if(!committedIds.length)throw new Error('Не удалось перевести заказы «На сборку».');
    if(btn)btn.textContent='Скачиваем Excel…';
    const downloadQ=new URLSearchParams({scope:'SELECTED',move_to_picking:'false',layout:pickingLayout});
    if(selectedWh)downloadQ.set('wb_warehouse_id',selectedWh);
    try{
      blob=await apiBlob(`/marketplaces/wb/picking-lists.xlsx?${downloadQ.toString()}`,{method:'POST',body:JSON.stringify({...requestBody,order_ids:[...new Set(committedIds)]}),timeoutMs:180000});
    }catch(downloadError){
      downloadError.statusCommitted=true;
      throw downloadError;
    }
  }else{
    blob=await apiBlob(`/marketplaces/wb/picking-lists.xlsx?${q.toString()}`,{method:'POST',body:JSON.stringify(requestBody),timeoutMs:180000});
  }
  const layoutFile=pickingLayout==='TIME'?'по_времени':'по_ячейкам';
  saveBlob(blob,`WB_сборочный_${layoutFile}_${new Date().toISOString().slice(0,10)}.xlsx`);
  state.inventoryCatalogLoadedAt=0;
  await loadWbOrders();
  // Supply refresh is informational here; do not keep the picking button blocked on it.
  setTimeout(()=>loadWbSupplies().catch(e=>console.warn('WB supplies after picking:',e)),0);
  const layoutText=pickingLayout==='TIME'?'по времени заказа':'по ячейкам / коробам';
  const skipNote=skippedIds.length?` Не переведено из-за отсутствия безопасного свободного резерва: ${[...new Set(skippedIds)].length} шт. Они остались в «Новых».`:'';
  toast((moveToPicking?'Excel сформирован. Новые заказы с подтверждённым резервом переведены «На сборку»; заказы, уже бывшие «На сборке», включены без повторного списания.':(includesPicking?'Excel сформирован. Заказы «На сборке» включены с прежними адресами без повторного списания.':'Excel сформирован. Статусы заказов не изменены.'))+` Формат: ${layoutText}.`+skipNote);
}catch(e){const committed=moveCommitted||e?.statusCommitted;toast(committed?`Заказы уже переведены «На сборку» и резерв закреплён. Excel не скачался: ${e.message||'ошибка загрузки'}. Повторно сформируйте его из вкладки «На сборке» — повторного списания не будет.`:(e?.name==='TimeoutError'?'Сборочный лист формируется слишком долго. Статусы и остатки не изменены.':e.message),true);if(committed){loadWbOrders().catch(()=>{});}}finally{if(btn){btn.dataset.busy='0';btn.disabled=false;btn.textContent=btn.dataset.oldText||'На сборку · Excel';delete btn.dataset.oldText;}}});

function applyWbLocalStatusOptimistic(orderNums,target){
  const wanted=new Set((orderNums||[]).map(x=>String(x)));if(!wanted.size)return;
  const before=state.wbOrders.length;
  for(const o of state.wbOrders){if(wanted.has(String(o.external_order_id))){o.local_status=target;o.local_status_name=({NEW:'Новый',PICKING:'На сборке',PACKED:'Упаковано',SENT:'Отправлен'})[target]||target;}}
  if(state.wbOrderFilter&&state.wbOrderFilter!==target)state.wbOrders=state.wbOrders.filter(o=>!wanted.has(String(o.external_order_id)));
  if(state.wbOrderCounts){for(const k of ['NEW','PICKING','PACKED','SENT'])state.wbOrderCounts[k]=Number(state.wbOrderCounts[k]||0);const moved=Math.max(0,before-state.wbOrders.length)||wanted.size;if(state.wbOrderFilter&&state.wbOrderFilter!==target)state.wbOrderCounts[state.wbOrderFilter]=Math.max(0,Number(state.wbOrderCounts[state.wbOrderFilter]||0)-moved);state.wbOrderCounts[target]=Number(state.wbOrderCounts[target]||0)+moved;}
  clearWbSelectedOrders();renderWbOrders();
}

$('wbBulkStatusSelect')?.addEventListener('change',async e=>{
  const target=e.target.value;if(!target)return;
  const ids=selectedValues('.wb-order-check').map(Number);
  try{
    if(!ids.length)throw new Error('Сначала выберите один или несколько заказов.');
    const titles={NEW:'«Новые»',PICKING:'«На сборке»',PACKED:'«Упаковано»',SENT:'«Отправлены»'};
    const title=titles[target]||target;
    if(!confirm(`Перевести выбранные заказы (${ids.length}) в ${title} внутри FulfillCore? Wildberries и поставка не изменятся.`))return;
    // FC_K9R58_BULK_SENT_STRICT_PATH: «Отправлены» never uses the generic status writer.
    const r=target==='SENT'
      ?await api(`/marketplaces/wb/orders/mark-sent?warehouse_id=${encodeURIComponent(state.warehouseId)}`,{method:'POST',body:JSON.stringify({credential_id:wbCredential(),order_ids:ids})})
      :await api('/marketplaces/wb/orders/manual-status',{method:'POST',body:JSON.stringify({credential_id:wbCredential(),warehouse_id:state.warehouseId,order_ids:ids,target_status:target})});
    if(target==='SENT'&&Number(r?.verified||0)!==ids.length)throw new Error('Сервер не подтвердил все выбранные заказы в «Отправленные». Ничего не повторяйте вслепую — обновите страницу.');
    applyWbLocalStatusOptimistic(ids,target);await loadWbOrders();
    const healed=Number(r?.healed_sibling_rows||0);
    toast(`Статус изменён: ${title}. Заказов: ${r.selected||ids.length}.${healed?` Исправлено старых копий заказа: ${healed}.`:''}`);
  }catch(err){toast(err.message,true)}
  finally{e.target.value='';syncWbButtons();}
});

$('wbResetNewBtn')?.addEventListener('click',async()=>{try{
  const ids=selectedValues('.wb-order-check').map(Number);if(!ids.length)return;
  if(!confirm('Перевести выбранные заказы в «Новые» внутри FulfillCore? Статус WB и поставка не изменятся.'))return;
  const r=await api(`/marketplaces/wb/orders/workflow-reset?warehouse_id=${encodeURIComponent(state.warehouseId)}`,{method:'POST',body:JSON.stringify({credential_id:wbCredential(),order_ids:ids,target_status:'NEW'})});
  await loadWbOrders();toast(`В «Новые» переведено: ${r.changed}`);
}catch(e){toast(e.message,true)}});
$('wbResetPickingBtn')?.addEventListener('click',async()=>{try{
  const ids=selectedValues('.wb-order-check').map(Number);if(!ids.length)return;
  if(!confirm('Перевести выбранные заказы в «На сборке» внутри FulfillCore? Статус WB и поставка не изменятся.'))return;
  const r=await api(`/marketplaces/wb/orders/workflow-reset?warehouse_id=${encodeURIComponent(state.warehouseId)}`,{method:'POST',body:JSON.stringify({credential_id:wbCredential(),order_ids:ids,target_status:'PICKING'})});
  await loadWbOrders();toast(`На сборку переведено: ${r.changed}`);
}catch(e){toast(e.message,true)}});

$('wbMarkSentBtn')?.addEventListener('click',async()=>{try{
  const ids=selectedValues('.wb-order-check').map(Number);if(!ids.length)return;
  if(!confirm('Перевести выбранные упакованные заказы в «Отправленные» внутри FulfillCore? WB не изменится. Физический остаток повторно списываться не будет.'))return;
  const r=await api(`/marketplaces/wb/orders/mark-sent?warehouse_id=${encodeURIComponent(state.warehouseId)}`,{method:'POST',body:JSON.stringify({credential_id:wbCredential(),order_ids:ids})});
  if(Number(r?.verified||0)!==ids.length)throw new Error('Сервер не подтвердил все выбранные заказы в «Отправленные».');
  applyWbLocalStatusOptimistic(ids,'SENT');await loadWbOrders();
  const skipped=(r.already_sent||[]).length,healed=Number(r.healed_sibling_rows||0);toast(`В «Отправленные» переведено: ${r.changed}${skipped?` · уже были отправлены: ${skipped}`:''}${healed?` · исправлено старых копий: ${healed}`:''}`);
}catch(e){toast(e.message,true)}});

$('wbShipPackedBtn')?.addEventListener('click',async()=>{try{
  const ids=selectedValues('.wb-order-check').map(Number);if(!ids.length)return;
  const selected=state.wbOrders.filter(o=>ids.includes(Number(o.external_order_id)));
  if(selected.some(o=>o.local_status!=='PACKED'))throw new Error('В доставку можно переводить только упакованные заказы.');
  const missing=selected.filter(o=>!String(o.supply_id||'').trim());
  if(missing.length)throw new Error(`У ${missing.length} упакованных заказов нет поставки WB. Сначала синхронизируйте/привяжите их к поставке.`);
  const supplies=wbSupplySummary(selected);if(!supplies.length)throw new Error('В выбранных упакованных заказах не найдено ни одной поставки WB.');
  const preview=supplies.slice(0,20).map(x=>`${x.id} — ${x.count} зак.`).join('\n')+(supplies.length>20?`\n… ещё ${supplies.length-20}`:'');
  if(!confirm(`Перевести выбранные упакованные заказы в доставку?\n\nЗаказов: ${selected.length}\nПоставок WB: ${supplies.length}\n\n${preview}\n\nFulfillCore закроет каждую поставку только если в ней нет неупакованных заказов. После успешного закрытия программа предложит распечатать ШК всех этих поставок.`))return;
  const r=await api(`/marketplaces/wb/shipments/from-packed?warehouse_id=${encodeURIComponent(state.warehouseId)}`,{method:'POST',body:JSON.stringify({credential_id:wbCredential(),order_ids:ids})});
  const supplyIds=supplies.map(x=>x.id),delivered=normalizeWbDeliveredSupplyIds(r.delivered_supplies),skipped=(r.already_sent||[]).length;
  await Promise.all([loadWbOrders(),loadWbSupplies(false,true)]);
  toast(`В доставку переведено поставок: ${delivered.length}${skipped?` · уже были отправлены WB: ${skipped}`:''}`);
  if(supplyIds.length&&confirm(`Поставки переведены в доставку. Распечатать ШК всех поставок этой отгрузки сейчас?\n\nПоставок: ${supplyIds.length}\n${supplyIds.join('\n')}`)){
    await printWbSupplyBarcodes(supplyIds,{closed:true});
  }
}catch(e){toast(e.message,true)}});

$('wbPrintLabelsBtn').addEventListener('click',async()=>{try{
  const ids=selectedValues('.wb-order-check').map(Number);if(!ids.length)return;
  const blob=await apiBlob('/marketplaces/wb/order-labels.pdf',{method:'POST',body:JSON.stringify({credential_id:wbCredential(),order_ids:ids})});
  printBlob(blob,'WB_этикетки.pdf');await loadWbOrders();toast('Этикетки WB отправлены на печать');
}catch(e){toast(e.message,true)}});
$('wbCreateSupplyBtn').addEventListener('click',async()=>{
  const btn=$('wbCreateSupplyBtn');if(state.wbSupplyActionBusy)return;
  try{
    const cred=wbCredential();if(!cred)throw new Error('Сначала выберите кабинет WB');if(!state.warehouseId)throw new Error('Сначала выберите физический склад FulfillCore');
    const ids=selectedValues('.wb-order-check').map(Number).filter(Number.isFinite);
    const selected=state.wbOrders.filter(o=>ids.includes(Number(o.external_order_id)));
    if(selected.some(o=>o.local_status!=='NEW'||o.supply_id))throw new Error('В новую поставку можно добавить только новые заказы без поставки.');
    const selectedWh=state.wbWarehouseFilter||$('wbWarehouseView')?.value||'';
    if(!ids.length&&!selectedWh)throw new Error('Для пустой поставки сначала выберите рабочий склад WB.');
    if(!ids.length&&!confirm('Создать пустую поставку WB? Затем в неё можно добавить выбранные новые заказы.'))return;
    const name=prompt('Название поставки WB:',`FulfillCore ${new Date().toLocaleDateString('ru-RU',{timeZone:'Europe/Moscow'})} ${new Date().toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'})}`);if(!name)return;
    state.wbSupplyActionBusy=true;if(btn){btn.disabled=true;btn.dataset.oldText=btn.textContent;btn.textContent='Создаём…';}toast('Создаём поставку WB…');
    const qp=new URLSearchParams({warehouse_id:String(state.warehouseId)});if(selectedWh)qp.set('wb_warehouse_id',String(selectedWh));
    const r=await api(`/marketplaces/wb/supplies?${qp.toString()}`,{method:'POST',timeoutMs:45000,body:JSON.stringify({credential_id:cred,name,order_ids:ids})});
    await Promise.all([loadWbOrders(false),loadWbSupplies(false,true)]);
    if(r.ok===false){toast(r.warning||`Поставка ${r.id} создана, но состав требует повторной синхронизации.`,true);}else if(r.empty){toast(`Пустая поставка WB создана: ${r.id}. Она уже видна на всех компьютерах.`);}else{toast(`Поставка WB создана: ${r.id} · добавлено заказов: ${r.orders_added||ids.length}.`);}
  }catch(e){await loadWbSupplies(false,true).catch(()=>{});toast(e.message,true)}finally{state.wbSupplyActionBusy=false;if(btn){btn.disabled=false;btn.textContent=btn.dataset.oldText||'Создать поставку';delete btn.dataset.oldText;}syncWbButtons();}
});

async function processWbProductScan(){
  if(state.wbScanBusy)return;const barcode=$('wbPackProduct').value.trim();if(!barcode)return;
  state.wbScanBusy=true;
  try{
    /* 0.9.4.14: product scan is LOCAL PostgreSQL only; never abort a valid scanner request. */ const r=await api('/marketplaces/wb/packing/scan-product?station_code='+encodeURIComponent(state.wbPrintStation||'PACK-01')+'&device_id='+encodeURIComponent(state.deviceId||''),{method:'POST',timeoutMs:0,body:JSON.stringify({credential_id:wbCredential(),warehouse_id:state.warehouseId,barcode})});
    state.wbActivePackingOrder=r.order;state.wbActivePackingCredentialId=wbCredential();const o=r.order;
    $('wbPackProductHint').textContent='✓ Товар найден в заказе';$('wbPackProductHint').className='good';
    $('wbPackChz').disabled=false;$('wbPackChz').value='';$('wbPackChz').placeholder='Отсканируйте DataMatrix кода маркировки';$('wbPackingProductLabelBtn').disabled=false;if($('wbPackingSkipBtn'))$('wbPackingSkipBtn').disabled=false;
    const stockLine=o.stock_unchecked?'не блокирует упаковку · работаем по заказу WB':stockText(o.stock);
    $('wbPackingResult').innerHTML=`<b>Заказ WB №${esc(o.external_order_id)}</b><span>${esc(o.local_sku?skuLabel(o.local_sku):(o.article||'Товар'))}</span><span><b>Резерв:</b> ${esc(reservedText(o.reserved))}</span><span><b>Остаток:</b> ${esc(stockLine)}</span>`;
    setTimeout(()=>restoreWbPackingScannerFocus(true),20);
  }catch(err){state.wbActivePackingOrder=null;state.wbActivePackingCredentialId='';$('wbPackChz').disabled=true;$('wbPackingProductLabelBtn').disabled=true;const detail=err?.data?.detail||{},virtual=detail?.code==='ITEM_IN_VIRTUAL_RECEIVING';$('wbPackProductHint').textContent=virtual?`${detail.message} В ПРИЕМКА: ${fmtQty(detail.quantity||0)} шт.`:err.message;$('wbPackProductHint').className=virtual?'warn':'bad';if(virtual){$('wbPackingResult').innerHTML=`<div class="warn-box"><b>Товар найден в виртуальной ПРИЕМКА</b><span>${esc(detail.article||'')} ${detail.size?`· ${esc(detail.size)}`:''} · ${fmtQty(detail.quantity||0)} шт.</span><span>Сначала разместите товар в реальную ячейку/палету, затем возвращайтесь к упаковке.</span><button type="button" class="mini primary" id="openReceivingFromPacking">Открыть ПРИЕМКА</button></div>`;$('openReceivingFromPacking')?.addEventListener('click',async()=>{go('locations');await loadContextData();const loc=receivingLocation();if(loc)await openLocationContents(String(loc.id));});}toast(virtual?detail.message:err.message,true);setTimeout(()=>selectWbPackingProductForRetry(),0);}
  finally{state.wbScanBusy=false;}
}
function scheduleWbHotRefresh(delay=900){
  clearTimeout(state.wbHotRefreshTimer);
  state.wbHotRefreshTimer=setTimeout(()=>loadWbOrders(false).catch(e=>console.warn('WB hot refresh:',e)),delay);
}
function queueWbBackgroundLabel(orderId,orderNumber,localFallback=false){
  // 0.9.4.9a: this name is kept for compatibility, but it no longer creates a second
  // manual label job. The server owns WB verification, label retrieval and exactly-one
  // PrintJob. Browser only watches progress.
  const ps=wbPrintSettings();
  state.wbBackgroundPrintChain=(state.wbBackgroundPrintChain||Promise.resolve()).catch(()=>{}).then(async()=>{
    let lastMessage='Этикетка готовится…';
    for(let i=0;i<360;i++){
      try{
        const r=await api(`/marketplaces/wb/packing/progress?credential_id=${encodeURIComponent(wbCredential())}&order_id=${encodeURIComponent(orderId)}&warehouse_id=${encodeURIComponent(state.warehouseId)}&station_code=${encodeURIComponent(ps.station)}&device_id=${encodeURIComponent(state.deviceId||'')}`,{timeoutMs:5000});
        if(r?.message)lastMessage=r.message;
        if(r?.order){mergeWbPackingOrderUpdate(r.order);restoreWbPackingScannerFocus(false);}
        if(r?.browser_ready&&r?.print_job_id){
          const q=new URLSearchParams({credential_id:wbCredential(),order_id:String(orderId),print_job_id:String(r.print_job_id),warehouse_id:String(state.warehouseId)});
          const blob=await apiBlob('/marketplaces/wb/packing/browser-label?'+q.toString(),{timeoutMs:0});
          printBlob(blob,`WB-order-${orderNumber}.png`);
          toast(`Заказ WB №${orderNumber}: этикетка открыта на печать, заказ перенесён в «Упакованные».`);scheduleWbHotRefresh(250);restoreWbPackingScannerFocus(false);return;
        }
        if(r?.done){toast(`Заказ WB №${orderNumber}: этикетка напечатана, заказ упакован.`);scheduleWbHotRefresh(250);restoreWbPackingScannerFocus(false);return;}
        if(r?.error){toast(`Заказ WB №${orderNumber}: ${r.message||'Ошибка автоматической печати'}`,true);scheduleWbHotRefresh(250);return;}
      }catch(e){
        // A short polling/network failure must not create a second print job.
      }
      // Preserve near-instant first-paper response, then back off once WB/Print Agent
      // is clearly still working. This prevents many packing PCs from hammering the
      // same read-only progress route several times per second for minutes.
      const pause=i<8?350:(i<24?750:1500);
      await new Promise(resolve=>setTimeout(resolve,pause));
    }
    toast(`Заказ WB №${orderNumber}: ${lastMessage} Проверьте Print Agent; повторно ЧЗ не сканируйте.`,true);
  });
}
async function processWbChzScan(){
  if(state.wbScanBusy)return;if(!state.wbActivePackingOrder)return;const chz=$('wbPackChz').value;if(!chz)return;
  const activeOrderId=state.wbActivePackingOrder.id,activeOrderNumber=state.wbActivePackingOrder.external_order_id;
  state.wbScanBusy=true;
  try{
    // FC_K9R50_CHZ_BROWSER_TIMEOUT_RECOVERY: a local scanner request must never leave
    // the workstation looking frozen forever.  If the HTTP response is delayed, check
    // the already-persisted packing marker instead of blindly resubmitting the same CHZ.
    const ps=wbPrintSettings();$('wbPackChzHint').textContent=ps.mode==='browser'?'Сохраняем ЧЗ и готовим ручную печать…':'Сохраняем ЧЗ и запускаем автоматическую этикетку…';$('wbPackChzHint').className='';
    let r=null;
    try{
      r=await api(`/marketplaces/wb/packing/scan-chz?warehouse_id=${encodeURIComponent(state.warehouseId)}&wb_label_width=${ps.wbWidth}&wb_label_height=${ps.wbHeight}&wb_label_rotate=${ps.wbRotate}&wb_label_scale=${ps.wbScale}&wb_label_offset_x=${ps.wbOffsetX}&wb_label_offset_y=${ps.wbOffsetY}&defer_print=1&print_mode=${encodeURIComponent(ps.mode)}&station_code=${encodeURIComponent(ps.station)}&device_id=${encodeURIComponent(state.deviceId||'')}`,{method:'POST',timeoutMs:8000,body:JSON.stringify({credential_id:wbCredential(),order_id:activeOrderId,chz,print_station:ps.station})});
    }catch(requestErr){
      if(!['TimeoutError','AbortError'].includes(requestErr?.name))throw requestErr;
      $('wbPackChzHint').textContent='Ответ сервера задержался. Проверяем, принят ли ЧЗ…';$('wbPackChzHint').className='warn';
      for(const pause of [250,450,750,1200,1800]){
        await new Promise(resolve=>setTimeout(resolve,pause));
        try{
          const q=new URLSearchParams({credential_id:wbCredential(),order_id:String(activeOrderId),warehouse_id:String(state.warehouseId),station_code:String(ps.station),device_id:String(state.deviceId||'')});
          const progress=await api('/marketplaces/wb/packing/progress?'+q.toString(),{timeoutMs:3000});
          if(progress?.order?.chz_attached){
            r={ok:true,gate_active:!progress?.done,packed:Boolean(progress?.done),printed:Boolean(progress?.done),order:{id:activeOrderId,external_order_id:activeOrderNumber},message:progress?.message||'ЧЗ принят. Этикетка готовится.'};
            break;
          }
        }catch(_){}
      }
      if(!r)throw new Error('Сервер не подтвердил ЧЗ после задержки. Повторите этот же ЧЗ — дубль печати не создастся.');
    }
    if(r?.gate_active){
      const orderId=r.order.id,orderNumber=r.order.external_order_id;
      $('wbPackChzHint').textContent=r.message||'ЧЗ сохранён. Этикетка готовится автоматически…';$('wbPackChzHint').className='';
      toast(`Заказ WB №${orderNumber}: ЧЗ принят. ${ps.mode==='browser'?'Готовим этикетку в браузере.':'Ждём автоматическую печать.'}`);
      queueWbBackgroundLabel(orderId,orderNumber,false);
      scheduleWbHotRefresh(500);
      resetWbPacking(true);
      return;
    }
    if(r?.packed){
      const orderId=r.order.id,orderNumber=r.order.external_order_id;
      toast(`Заказ WB №${orderNumber}: этикетка напечатана, заказ упакован.`);scheduleWbHotRefresh(250);resetWbPacking(true);return;
    }
    $('wbPackChzHint').textContent='⚠ '+(r?.message||'Не удалось запустить автоматическую печать.');$('wbPackChzHint').className='warn';toast(r?.message||'Не удалось запустить автоматическую печать.',true);$('wbPackChz').select();
  }catch(err){
    let msg=String(err?.message||'Ошибка передачи ЧЗ');
    if(msg.toLowerCase().includes('request timeout'))msg='Сервер не ответил вовремя. Повторите этот же ЧЗ — дубль не создастся.';
    $('wbPackChzHint').textContent=msg;$('wbPackChzHint').className='bad';toast(msg,true);$('wbPackChz').select();
  }finally{state.wbScanBusy=false;}
}

let wbProductTimer=null,wbChzTimer=null;
const wbScannerAdaptive={product:{last:0,gap:0,count:0},chz:{last:0,gap:0,count:0}};
function resetWbScannerSlot(slot){slot.last=0;slot.gap=0;slot.count=0;}
function scheduleWbScanner(kind,el,callback,minLength){
  const now=performance.now(),slot=wbScannerAdaptive[kind];
  if(slot.last){const gap=now-slot.last;slot.gap=slot.gap?Math.max(gap,slot.gap*0.72):gap;}
  slot.last=now;slot.count=(slot.count||0)+1;
  const cfg=scannerProfile(),base=kind==='product'?cfg.product_idle_ms:cfg.chz_idle_ms,max=kind==='product'?cfg.product_max_idle_ms:cfg.chz_max_idle_ms;
  let delay=Math.min(max,Math.max(base,Math.round(slot.gap*4+60)));
  const length=el.value.trim().length;
  // A hardware scanner emits a dense burst. Once that is clear, do not force the old
  // 180/700 ms idle floor. Manual typing and scanners with real pauses keep the safe path.
  const fastBurst=slot.count>=6&&slot.gap>0&&slot.gap<=45;
  if(fastBurst&&kind==='product'&&length>=8)delay=Math.min(delay,85);
  if(fastBurst&&kind==='chz'&&length>=24)delay=Math.min(delay,220);
  if(kind==='product')clearTimeout(wbProductTimer);else clearTimeout(wbChzTimer);
  if(length<minLength)return;
  const timer=setTimeout(()=>{resetWbScannerSlot(slot);callback();},delay);if(kind==='product')wbProductTimer=timer;else wbChzTimer=timer;
}
function wbSubmitKey(e){return e.key==='Enter'||e.key==='Tab';}
$('wbPackProduct').addEventListener('focus',()=>{state.wbPinnedScanField='product';});
$('wbPackChz').addEventListener('focus',()=>{state.wbPinnedScanField='chz';});
$('wbPackProduct').addEventListener('keydown',e=>{if(wbSubmitKey(e)){e.preventDefault();clearTimeout(wbProductTimer);resetWbScannerSlot(wbScannerAdaptive.product);processWbProductScan();}});
$('wbPackProduct').addEventListener('input',()=>scheduleWbScanner('product',$('wbPackProduct'),processWbProductScan,6));
function wbCaptureGsFromScanner(e){
  // In USB-HID keyboard mode FNC1/GS (ASCII 29) is commonly emitted as Ctrl+].
  // Browsers do not insert that control character into <input> automatically, so keep it.
  const isGs=(e.key==='GroupSeparator')||(e.ctrlKey&&!e.altKey&&!e.metaKey&&(e.key===']'||e.code==='BracketRight'));
  if(!isGs)return false;
  e.preventDefault();
  const el=$('wbPackChz'),start=Number.isInteger(el.selectionStart)?el.selectionStart:el.value.length,end=Number.isInteger(el.selectionEnd)?el.selectionEnd:start;
  el.value=el.value.slice(0,start)+'\x1d'+el.value.slice(end);
  try{el.setSelectionRange(start+1,start+1);}catch(_e){}
  el.dispatchEvent(new Event('input',{bubbles:true}));
  return true;
}
$('wbPackChz').addEventListener('keydown',e=>{if(wbCaptureGsFromScanner(e))return;if(wbSubmitKey(e)){e.preventDefault();clearTimeout(wbChzTimer);resetWbScannerSlot(wbScannerAdaptive.chz);processWbChzScan();}});
// Long crypto tails still retain the conservative adaptive fallback. Dense scanner bursts
// complete after ~220 ms idle, while Enter/Tab submits immediately.
$('wbPackChz').addEventListener('input',()=>scheduleWbScanner('chz',$('wbPackChz'),processWbChzScan,16));
$('wbPackingResetBtn').addEventListener('click',()=>resetWbPacking());
$('wbPackingSkipBtn')?.addEventListener('click',async()=>{
  if(state.wbScanBusy||!state.wbActivePackingOrder)return;
  const current=state.wbActivePackingOrder;
  const barcode=(current.product_scanned_barcode||$('wbPackProduct').value||'').trim();
  if(!barcode)return;
  state.wbScanBusy=true;$('wbPackingSkipBtn').disabled=true;
  try{
    await api(`/marketplaces/wb/packing/skip-order?credential_id=${encodeURIComponent(wbCredential())}&order_id=${encodeURIComponent(current.id)}&warehouse_id=${encodeURIComponent(state.warehouseId)}&station_code=${encodeURIComponent(state.wbPrintStation||'PACK-01')}&device_id=${encodeURIComponent(state.deviceId||'')}`,{method:'POST'});
    state.wbActivePackingOrder=null;state.wbActivePackingCredentialId='';$('wbPackChz').value='';$('wbPackChz').disabled=true;$('wbPackingProductLabelBtn').disabled=true;
    $('wbPackProduct').value=barcode;$('wbPackProductHint').textContent='Заказ перемещён в конец очереди. Ищем следующий…';$('wbPackProductHint').className='';
    toast(`Заказ WB №${current.external_order_id} пропущен и перемещён в конец очереди.`);
  }catch(e){toast(e.message,true);state.wbScanBusy=false;if($('wbPackingSkipBtn'))$('wbPackingSkipBtn').disabled=false;return;}
  state.wbScanBusy=false;
  await processWbProductScan();
});
$('wbPackingProductLabelBtn').addEventListener('click',async()=>{try{if(!state.wbActivePackingOrder)throw new Error('Сначала отсканируйте товар');await printWbProductLabel(state.wbActivePackingOrder.id);toast('Этикетка с артикулом и ШК WB отправлена на печать');}catch(e){toast(e.message,true)}});
if($('wbPrintMode')){$('wbPrintMode').value=state.wbPrintMode;$('wbPrintStation').value=state.wbPrintStation;}
$('wbPrintMode')?.addEventListener('change',e=>{state.wbPrintMode=e.target.value;localStorage.setItem('fc_wb_print_mode',state.wbPrintMode);});
$('wbPrintStation')?.addEventListener('change',e=>{state.wbPrintStation=e.target.value.trim().toUpperCase()||'PACK-01';e.target.value=state.wbPrintStation;localStorage.setItem('fc_wb_print_station',state.wbPrintStation);loadWorkstationProfile(state.wbPrintStation).then(scheduleWorkstationProfileSync).catch(()=>scheduleWorkstationProfileSync());});
$('wbCredentialSelect').addEventListener('change',async()=>{resetWbPacking(false);state.wbWarehouseFilter='';state.wbOrderFilter='';localStorage.removeItem(wbWarehouseFilterKey());localStorage.removeItem(wbOrderFilterKey());await loadWbWarehouses(false).catch(()=>{});restoreWbOrdersCacheImmediately();await loadWbOrders().catch(()=>{});await loadWbSupplies(false).catch(()=>{});syncWbOrders({silent:true,force:false}).catch(()=>{});});

$('loadWbSuppliesBtn').addEventListener('click',()=>loadWbSupplies(true,true));
if($('wbShowClosedSupplies')){$('wbShowClosedSupplies').checked=state.wbShowClosedSupplies;$('wbShowClosedSupplies').addEventListener('change',e=>{state.wbShowClosedSupplies=e.target.checked;localStorage.setItem('fc_wb_show_closed_supplies',e.target.checked?'1':'0');loadWbSupplies(true,true);});}
async function loadWbSupplies(showError=true,force=false){
  try{
    const cred=wbCredential();if(!cred||!state.warehouseId){state.wbSupplies=[];state.wbSupplyMeta={};renderWbSupplies();return;}
    const selectedWh=state.wbWarehouseFilter||$('wbWarehouseView')?.value||'';
    // Supplies follow the same exact WB seller-warehouse selection as the order list.
    // This prevents Pushkino/Reutov/etc. supplies from being mixed on one workstation.
    if(!selectedWh){
      state.wbSupplies=[];state.wbSupplyMeta={selected_required:true,count:0};renderWbSupplies();
      if(showError)toast('Сначала выберите склад WB — поставки показываются только для выбранного склада.',true);
      return;
    }
    const qp=new URLSearchParams({credential_id:String(cred),warehouse_id:String(state.warehouseId),wb_warehouse_id:String(selectedWh),include_closed:state.wbShowClosedSupplies?'true':'false',force:force?'true':'false'});
    const r=await api(`/marketplaces/wb/supplies?${qp.toString()}`,{timeoutMs:45000});
    state.wbSupplies=r.supplies||[];state.wbSupplyMeta=r||{};renderWbSupplies();
    if(showError&&r.live_error)toast(`Поставки показаны из общего кэша. WB: ${r.live_error}`,true);
  }catch(e){if(showError)toast(`Поставки WB: ${e.message}`,true);else console.warn('WB supplies background refresh:',e)}
}
function renderWbSupplies(){
  const el=$('wbSupplies');if(!el)return;const meta=state.wbSupplyMeta||{};
  const selectedWh=state.wbWarehouseFilter||$('wbWarehouseView')?.value||'';
  const whObj=state.wbWarehouses.find(w=>String(w.id)===String(selectedWh));
  const whTitle=whObj?.name||selectedWh||'не выбран';
  const status=`<div class="supply-sync-state"><small>${meta.selected_required?'Выберите склад WB':(meta.live_refreshed?'✓ WB обновлён сейчас':'Общий серверный список')} · склад: ${esc(whTitle)}${meta.live_error?` · <span class="bad">WB временно недоступен: ${esc(meta.live_error)}</span>`:''} · поставок: ${Number(meta.count??state.wbSupplies.length)}</small></div>`;
  const rows=state.wbSupplies.length?state.wbSupplies.map(s=>{
    const count=Number(s._fc_order_count||0);const cached=s._fc_cached?' · кэш':'';const wh=s._fc_wb_warehouse_id?` · склад WB ${esc(s._fc_wb_warehouse_id)}`:'';const err=s._fc_last_error?`<small class="bad">Последняя ошибка: ${esc(s._fc_last_error)}</small>`:'';
    return `<div class="supply-row ${s.done?'supply-closed':''}"><div class="supply-row-main"><b>${esc(s.name||s.id)}</b><small>ID: ${esc(s.id)}${s.done?' · закрыта':' · актуальная'} · заказов: ${count}${wh}${cached}</small>${err}</div><div class="row-actions supply-actions"><button class="mini secondary" data-wb-qr="${esc(s.id)}">ШК поставки</button><button class="mini secondary" data-wb-box-label="${esc(s.id)}">ШК коробов</button><button class="mini secondary" data-wb-a4-label="${esc(s.id)}">Печать A4</button>${s.done?``:`<button class="mini secondary" data-wb-add-selected="${esc(s.id)}">Добавить выбранные</button><button class="mini secondary" data-wb-boxes="${esc(s.id)}">Короба ПВЗ</button><button class="mini primary" data-wb-deliver="${esc(s.id)}">Закрыть поставку</button>`}</div></div>`;
  }).join(''):`<div class="empty-state">${meta.selected_required?'Выберите конкретный склад WB — здесь будут только его поставки.':(state.wbShowClosedSupplies?'Поставок выбранного склада не найдено.':'Активных поставок выбранного склада нет. Можно создать пустую поставку или поставку из выбранных новых заказов.')}</div>`;
  el.innerHTML=status+rows;
}
$('wbSupplies').addEventListener('click',async e=>{
  const q=e.target.closest('[data-wb-qr]'),d=e.target.closest('[data-wb-deliver]'),add=e.target.closest('[data-wb-add-selected]'),bx=e.target.closest('[data-wb-boxes]'),boxLabel=e.target.closest('[data-wb-box-label]'),a4=e.target.closest('[data-wb-a4-label]');
  try{
    const cred=wbCredential();if(!cred)throw new Error('Сначала выберите кабинет WB');
    if(q){
      const supplyId=q.dataset.wbQr;
      const supply=state.wbSupplies.find(x=>String(x.id)===String(supplyId));
      const isClosed=!!supply?.done;
      const ps=wbPrintSettings();
      const qs=`closed=${isClosed?'true':'false'}&width_mm=${ps.wbWidth}&height_mm=${ps.wbHeight}&scale_percent=${ps.wbScale}&offset_x_mm=${ps.wbOffsetX}&offset_y_mm=${ps.wbOffsetY}`;
      if(ps.mode==='agent'){
        await api(`/marketplaces/wb/supplies/${encodeURIComponent(supplyId)}/barcode/queue?${qs}`,{method:'POST',body:JSON.stringify({credential_id:cred,print_station:ps.station})});
        toast(`ШК поставки ${supplyId} отправлен на ${ps.station}. Статус поставки не изменён.`);
      }else{
        const blob=await apiBlob(`/marketplaces/wb/supplies/${encodeURIComponent(supplyId)}/barcode.pdf?credential_id=${encodeURIComponent(cred)}&${qs}`);
        printBlob(blob,`WB_${supplyId}_ШК_поставки.pdf`);
      }
    }
    if(boxLabel){
      const supplyId=boxLabel.dataset.wbBoxLabel;
      const supply=state.wbSupplies.find(x=>String(x.id)===String(supplyId));
      const isClosed=!!supply?.done;
      const amount=Number(prompt(`Сколько этикеток коробов напечатать?\n${isClosed?'Поставка закрыта — на этикетке будет количество товара.':'Поставка открыта — печатаем только баркод поставки, без количества.'}`,'1'));
      if(!Number.isInteger(amount)||amount<1)return;
      const ps=wbPrintSettings();
      const qs=`closed=${isClosed?'true':'false'}&copies=${amount}&width_mm=${ps.wbWidth}&height_mm=${ps.wbHeight}&scale_percent=${ps.wbScale}&offset_x_mm=${ps.wbOffsetX}&offset_y_mm=${ps.wbOffsetY}`;
      if(ps.mode==='agent'){
        const r=await api(`/marketplaces/wb/supplies/${encodeURIComponent(supplyId)}/box-label/queue?${qs}`,{method:'POST',body:JSON.stringify({credential_id:cred,print_station:ps.station})});
        toast(`Этикетки коробов отправлены на ${ps.station}: ${r.copies||amount} шт.${isClosed?` Товаров в поставке: ${r.item_count??'—'}.`:''}`);
      }else{
        const blob=await apiBlob(`/marketplaces/wb/supplies/${encodeURIComponent(supplyId)}/box-label.pdf?credential_id=${encodeURIComponent(cred)}&${qs}`);
        printBlob(blob,`WB_${supplyId}_короба.pdf`);
      }
    }
    if(a4){
      const supplyId=a4.dataset.wbA4Label;
      const quantity=Number(prompt('Количество товара для большой A4-этикетки:', ''));
      if(!Number.isInteger(quantity)||quantity<1)return;
      const url=`/marketplaces/wb/supplies/${encodeURIComponent(supplyId)}/a4-label?credential_id=${encodeURIComponent(cred)}&quantity=${quantity}`;
      // Open in the current tab so A4 printing works even when the browser blocks popups.
      // The print page contains a visible Back to FulfillCore button for returning after print.
      window.location.assign(url);
    }
    if(add){
      if(state.wbSupplyActionBusy)return;
      const ids=selectedValues('.wb-order-check').map(Number).filter(Number.isFinite);if(!ids.length)throw new Error('Сначала отметьте новые заказы галочками, затем нажмите «Добавить выбранные».');
      const selected=state.wbOrders.filter(o=>ids.includes(Number(o.external_order_id)));if(selected.some(o=>o.local_status!=='NEW'||o.supply_id))throw new Error('Добавить можно только новые заказы, которые ещё не находятся в поставке.');
      const supply=state.wbSupplies.find(x=>String(x.id)===String(add.dataset.wbAddSelected));
      if(!confirm(`Добавить выбранные заказы (${ids.length}) в поставку ${add.dataset.wbAddSelected}?`))return;
      state.wbSupplyActionBusy=true;add.disabled=true;const old=add.textContent;add.textContent='Добавляем…';toast('Добавляем заказы в поставку WB…');
      try{
        const qp=new URLSearchParams({warehouse_id:String(state.warehouseId)});if(supply?._fc_wb_warehouse_id)qp.set('wb_warehouse_id',String(supply._fc_wb_warehouse_id));
        const r=await api(`/marketplaces/wb/supplies/${encodeURIComponent(add.dataset.wbAddSelected)}/orders?${qp.toString()}`,{method:'PATCH',timeoutMs:45000,body:JSON.stringify({credential_id:cred,order_ids:ids})});
        await Promise.all([loadWbOrders(false),loadWbSupplies(false,true)]);toast(`В поставку добавлено заказов: ${r.orders_added||ids.length}.`);
      }finally{state.wbSupplyActionBusy=false;add.disabled=false;add.textContent=old;}
    }
    if(bx){
      const amount=Number(prompt('Сколько коробов WB добавить? Эта функция нужна для поставок на ПВЗ.','1'));
      if(!Number.isInteger(amount)||amount<1)return;
      const created=await api(`/marketplaces/wb/supplies/${encodeURIComponent(bx.dataset.wbBoxes)}/boxes`,{method:'POST',body:JSON.stringify({credential_id:cred,amount})});
      const ids=created.trbx_ids||[];if(!ids.length)throw new Error('WB не вернул ID коробов');
      const blob=await apiBlob(`/marketplaces/wb/supplies/${encodeURIComponent(bx.dataset.wbBoxes)}/box-labels.pdf`,{method:'POST',body:JSON.stringify({credential_id:cred,trbx_ids:ids})});
      printBlob(blob,`WB_${bx.dataset.wbBoxes}_короба.pdf`);toast(`Создано коробов WB: ${ids.length}. Этикетки отправлены на печать.`);
    }
    if(d){
      if(state.wbSupplyActionBusy)return;if(!confirm('Закрыть поставку WB и перевести её в доставку? После закрытия состав поставки нельзя свободно менять.'))return;
      state.wbSupplyActionBusy=true;d.disabled=true;const old=d.textContent;d.textContent='Закрываем…';toast(`Закрываем поставку ${d.dataset.wbDeliver}…`);
      try{
        const r=await api(`/marketplaces/wb/supplies/${encodeURIComponent(d.dataset.wbDeliver)}/deliver?credential_id=${encodeURIComponent(cred)}&warehouse_id=${encodeURIComponent(state.warehouseId)}`,{method:'PATCH',timeoutMs:45000});
        await Promise.all([loadWbSupplies(false,true),loadWbOrders(false)]);toast(r.already_closed?'Поставка уже была закрыта в WB. Статус синхронизирован.':'Поставка закрыта и переведена в доставку.');
      }finally{state.wbSupplyActionBusy=false;d.disabled=false;d.textContent=old;}
    }
  }catch(err){await loadWbSupplies(false,false).catch(()=>{});toast(err.message,true)}
});

function wbProductCredential(){return $('wbProductCredentialSelect')?.value||state.wbProductCredentialId||wbCredential();}
function parseWbProductArticles(){return ($('wbProductArticles')?.value||'').split(/[\n,;]+/).map(x=>x.trim()).filter(Boolean);}
function wbProductsCacheKey(){return `fc_wb_cards_cache_r100_${state.clientId||'none'}`;}
function saveWbProductsCache(){
  if(!state.wbProducts.length||state.wbProducts.some(p=>p.inventory?.loading))return;
  try{const compact=state.wbProducts.slice(0,250).map(p=>({id:p.id,local_only:!!p.local_only,nm_id:p.nm_id,vendor_code:p.vendor_code,title:p.title,brand:p.brand,subject_name:p.subject_name,photos:(p.photos||[]).slice(0,1),sizes:p.sizes||[],barcodes:p.barcodes||[],composition:p.composition||null,color:p.color||null,country:p.country||null,characteristics:p.characteristics||[],inventory:p.inventory||{quantity:0,sku_count:0},local_sku:p.local_sku||null}));localStorage.setItem(wbProductsCacheKey(),JSON.stringify({ts:Date.now(),products:compact,total:state.wbProductTotal,matching:state.wbProductMatching}));}catch{}
}
function restoreWbProductsCacheImmediately(){
  try{const raw=localStorage.getItem(wbProductsCacheKey());if(!raw)return false;const c=JSON.parse(raw);if(!c?.products?.length)return false;state.wbProducts=c.products;state.wbProductIndex=c.products;state.wbProductIndexClient='';state.wbProductTotal=Number(c.total||c.products.length);state.wbProductMatching=Number(c.matching||c.products.length);renderWbProducts();if($('wbProductsPersistHint'))$('wbProductsPersistHint').textContent=`Карточки показаны из локального кэша. Полный индекс перечитывается из локальной базы в фоне, без WB API.`;return true;}catch{return false;}
}
async function refreshWbProductInventory(requestSeq,cred,productIds,cacheAllowed){
  if(!productIds.length)return;
  // FC_K9R53_WB_PRODUCT_BACKGROUND_INVENTORY_UI: cards render first, local warehouse
  // quantity is hydrated afterward without any WB API request.
  try{const r=await api('/marketplaces/wb/products/inventory',{method:'POST',body:JSON.stringify({credential_id:cred,warehouse_id:state.warehouseId,product_ids:productIds})});
    if(requestSeq!==state.wbProductRequestSeq||cred!==wbProductCredential())return;const byId=new Map((r.items||[]).map(x=>[String(x.id),x]));
    state.wbProducts=state.wbProducts.map(p=>{const d=byId.get(String(p.id));if(!d)return p;return {...p,inventory:d.inventory||{quantity:0,sku_count:0},local_sku:d.local_sku??p.local_sku};});
    renderWbProducts();if(cacheAllowed)saveWbProductsCache();
  }catch(e){console.warn('WB product inventory background refresh:',e)}
}
async function loadWbProducts(force=false){
  const requestSeq=++state.wbProductRequestSeq;
  const q=($('wbProductSearch')?.value||'').trim().toLowerCase();
  if(!state.clientId){state.wbProducts=[];state.wbProductIndex=[];state.wbProductIndexClient='';state.wbProductTotal=0;state.wbProductMatching=0;renderWbProducts();return;}
  try{
    // r100: download the local WB-card snapshot once, then search entirely in browser memory.
    // Opening/searching this page never calls WB; only explicit import buttons do.
    if(force||state.wbProductIndexClient!==state.clientId||!Array.isArray(state.wbProductIndex)){
      const r=await api(`/warehouse-ui/catalog/wb-cards?client_id=${encodeURIComponent(state.clientId)}&limit=20000`,{timeoutMs:30000});
      if(requestSeq!==state.wbProductRequestSeq)return;
      state.wbProductIndex=(r.products||[]).map(p=>({...p,local_only:false,inventory:p.inventory||{quantity:0,sku_count:0,not_loaded:true}}));
      state.wbProductIndexClient=state.clientId;
      state.wbProductIndexLastImport=r.last_import_at||'';
      state.wbProductHiddenCount=Number(r.hidden_count||0);
      state.wbProductIndexTruncated=!!r.truncated;
      try{localStorage.setItem(wbProductsCacheKey(),JSON.stringify({ts:Date.now(),products:state.wbProductIndex.slice(0,2000),total:state.wbProductIndex.length,matching:state.wbProductIndex.length}));}catch{}
    }
    const index=state.wbProductIndex||[];
    const matched=q?index.filter(p=>[p.nm_id,p.vendor_code,p.title,p.brand,p.subject_name,(p.barcodes||[]).join(' '),(p.sizes||[]).map(x=>x?.techSize||x?.wbSize||'').join(' ')].join(' ').toLowerCase().includes(q)):index;
    state.wbProducts=matched.slice(0,300);
    state.wbProductTotal=index.length;
    state.wbProductMatching=matched.length;
    renderWbProducts();
    const hint=$('wbProductsPersistHint');if(hint){const last=state.wbProductIndexLastImport?`Последний импорт WB: ${new Date(state.wbProductIndexLastImport).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})}. `:'';const hidden=state.wbProductHiddenCount?`Скрыто локально: ${state.wbProductHiddenCount}. `:'';const more=state.wbProductIndexTruncated?'Показан локальный индекс до 20 000 карточек. ':'';hint.textContent=`Карточки WB хранятся локально · поиск моментальный и без WB API. ${last}${hidden}${more}Для новых/изменённых карточек нажмите «Дополнить из WB».`;}
  }catch(e){
    if(!state.wbProducts.length)restoreWbProductsCacheImmediately();
    if(!state.wbProducts.length)throw e;
    console.warn('Local WB card index refresh:',e);
  }
}

function wbProductSizesText(p){
  const values=[];for(const s of p.sizes||[]){const v=String(s.techSize||s.wbSize||'').trim();if(v&&!values.includes(v))values.push(v);}return values.slice(0,8);
}
function wbProductInventoryQuery(p,barcode=''){
  return String(barcode||p.vendor_code||p.local_sku?.article||p.local_sku?.barcode||'').trim();
}
function openInventoryForWbProduct(productId,barcode=''){
  const p=state.wbProducts.find(x=>String(x.id)===String(productId));if(!p)return;
  const q=wbProductInventoryQuery(p,barcode);$('inventorySearch').value=q;go('inventoryfbs');renderInventory();setTimeout(()=>$('inventorySearch')?.focus(),50);
}
function renderWbProducts(){
  const el=$('wbProductsGrid');if(!el)return;
  if($('wbProductsTotal'))$('wbProductsTotal').textContent=String(state.wbProductTotal||0);
  if($('wbProductsMatching'))$('wbProductsMatching').textContent=String(state.wbProductMatching||0);
  if(!state.wbProducts.length){el.innerHTML='<div class="empty-state">Карточки WB не найдены. Выполните первичную загрузку «Дополнить из WB» или измените локальный поиск.</div>';return;}
  el.innerHTML=state.wbProducts.map(p=>{
    const img=firstPhoto(p),sizes=wbProductSizesText(p),inv=p.inventory||{},qty=Number(inv.quantity||0),codes=(p.barcodes||[]).length;
    const stockText=inv.not_loaded||inv.loading?'по кнопке «Остатки»':fmtQty(qty)+' шт.';
    const tag=p.local_only?'Локальный':`WB ${esc(p.nm_id||'')}`;
    return `<article class="product-catalog-card">
      <button class="product-image-button" data-wb-product-open="${p.id}" type="button">${img?`<img src="${esc(img)}" alt="" loading="lazy"/>`:'<div class="product-placeholder">Нет фото</div>'}</button>
      <div class="product-catalog-info">
        <div class="product-card-topline"><span class="tag">${tag}</span>${p.subject_name?`<span class="muted">${esc(p.subject_name)}</span>`:''}</div>
        <button type="button" class="product-title-link" data-wb-product-open="${p.id}">${esc(p.title||p.local_sku?.name||'Без названия')}</button>
        <div class="product-article"><small>Артикул</small><b>${esc(p.vendor_code||p.local_sku?.article||'—')}</b></div>
        <div class="product-meta-row"><span><small>Источник</small><b>${p.local_only?'FulfillCore':'WB → локально'}</b></span><span><small>Размеры</small><b>${esc(sizes.join(', ')||'—')}</b></span><span><small>ШК</small><b>${codes}</b></span></div>
        <div class="product-stock-line"><span><small>Остаток на выбранном складе</small><b>${stockText}</b></span><div class="button-row"><button class="secondary mini" type="button" data-wb-product-stock="${p.id}">Остатки</button><button class="primary mini" type="button" data-wb-product-open="${p.id}">Открыть карточку</button><button class="danger mini" type="button" data-wb-product-hide="${p.id}">Убрать локально</button></div></div>
      </div>
    </article>`;
  }).join('');
}
function productPhotoUrl(photo){if(typeof photo==='string')return photo;return photo?.big||photo?.c516x688||photo?.square||photo?.tm||'';}
async function openWbProduct(productId){
  let p=(state.wbProductIndex||state.wbProducts||[]).find(x=>String(x.id)===String(productId));
  // Product Base and WB Cards share one local full-detail endpoint. Product Base may be
  // opened without the WB Cards page ever being loaded, so fetch the card by id directly.
  if(!p||p.description===undefined||p.characteristics===undefined){const r=await api(`/warehouse-ui/catalog/wb-cards/${encodeURIComponent(productId)}?client_id=${encodeURIComponent(state.clientId)}`,{timeoutMs:15000});p=r.product||p;if(!p)throw new Error('Карточка товара не найдена в локальной базе');const idx=(state.wbProductIndex||[]).findIndex(x=>String(x.id)===String(productId));if(idx>=0)state.wbProductIndex[idx]={...state.wbProductIndex[idx],...p};const shown=state.wbProducts.findIndex(x=>String(x.id)===String(productId));if(shown>=0)state.wbProducts[shown]={...state.wbProducts[shown],...p};}
  const photos=(p.photos||[]).map(productPhotoUrl).filter(Boolean),main=photos[0]||'';
  const sizes=(p.sizes||[]).map(sz=>{const size=sz.techSize||sz.wbSize||'—';const wbSize=sz.wbSize&&sz.wbSize!==size?` / WB ${esc(sz.wbSize)}`:'';const codes=(sz.skus||[]).map(code=>`<div class="barcode-row"><span><b>${esc(size)}${wbSize}</b><small>${esc(code)}</small></span><div class="button-row"><button class="mini secondary" type="button" data-wb-card-stock="${p.id}" data-barcode="${esc(code)}">Остатки</button>${p.local_only?'':`<button class="mini primary" data-wb-card-label="${p.id}" data-barcode="${esc(code)}">Печать ШК</button>`}</div></div>`).join('');return codes;}).join('');
  const gallery=photos.slice(0,8).map((url,i)=>`<button type="button" class="product-thumb ${i===0?'active':''}" data-product-photo="${esc(url)}"><img src="${esc(url)}" alt=""/></button>`).join('');
  const chars=(p.characteristics||[]).filter(x=>x?.name&&x?.value);
  const charHtml=chars.map(x=>`<div><small>${esc(x.name)}</small><b>${esc(x.value)}</b></div>`).join('');
  const stockText=p.inventory?.not_loaded||p.inventory?.loading?'по кнопке «Остатки»':fmtQty(p.inventory?.quantity||0)+' шт.';
  $('wbProductModalBody').innerHTML=`<div class="wb-product-editor">
    <div class="wb-product-gallery"><div class="product-thumbs">${gallery}</div><div class="product-main-photo">${main?`<img id="wbProductMainPhoto" src="${esc(main)}" alt=""/>`:'<div class="product-placeholder large">Нет фото</div>'}</div></div>
    <div class="wb-product-fields">
      <div class="product-detail-head"><div><span class="kicker">${p.local_only?'ЛОКАЛЬНАЯ КАРТОЧКА':`КАРТОЧКА WB · ${esc(p.nm_id)}`}</span><h2>${esc(p.title||'Без названия')}</h2></div><button class="secondary" type="button" data-wb-product-stock="${p.id}">Остатки</button></div>
      <div class="product-form-grid"><div><small>Категория</small><b>${esc(p.subject_name||'—')}</b></div><div><small>Источник</small><b>${p.local_only?'FulfillCore':'WB → локальная база'}</b></div><div><small>Артикул WB</small><b>${esc(p.nm_id||'—')}</b></div><div><small>Артикул</small><b>${esc(p.vendor_code||p.local_sku?.article||'—')}</b></div><div><small>Бренд</small><b>${esc(p.brand||'—')}</b></div><div><small>Штрихкоды</small><b class="mono-wrap">${esc((p.barcodes||[]).join(', ')||'—')}</b></div><div><small>Цвет</small><b>${esc(p.color||'—')}</b></div><div><small>Страна производства</small><b>${esc(p.country||'—')}</b></div><div><small>Состав</small><b>${esc(p.composition||'—')}</b></div><div><small>Остаток</small><b>${stockText}</b></div></div>
      <div class="product-description-box"><small>Описание</small><p>${esc(p.description||'Описание не заполнено')}</p></div>
      ${charHtml?`<div class="product-sizes-head"><h3>Характеристики WB</h3><span class="muted">${chars.length}</span></div><div class="product-form-grid product-characteristics-grid">${charHtml}</div>`:''}
      <div class="product-sizes-head"><h3>Размеры и штрихкоды</h3><span class="muted">${(p.sizes||[]).length} размеров · ${(p.barcodes||[]).length} ШК</span></div>
      <div class="barcode-list">${sizes||'<span class="muted">Штрихкодов нет</span>'}</div>
    </div>
  </div>`;
  $('wbProductModal').classList.remove('hidden');
}
async function printWbCatalogLabel(productId,barcode){
  const cred=wbProductCredential();if(!cred)throw new Error('Не выбран кабинет WB');
  const ps=wbPrintSettings();const blob=await apiBlob(`/marketplaces/wb/products/${encodeURIComponent(productId)}/label.pdf?credential_id=${encodeURIComponent(cred)}&barcode=${encodeURIComponent(barcode)}&width_mm=${ps.productWidth}&height_mm=${ps.productHeight}&rotate=${ps.productRotate}&scale_percent=${ps.productScale}&offset_x_mm=${ps.productOffsetX}&offset_y_mm=${ps.productOffsetY}`);printBlob(blob,`WB_product_${barcode}.pdf`);
}
$('refreshWbProductsBtn')?.addEventListener('click',()=>loadWbProducts(true));if($('refreshWbProductsBtn')){$('refreshWbProductsBtn').textContent='Обновить локально';$('refreshWbProductsBtn').title='Перечитать локальный индекс карточек FulfillCore без обращения к WB API';}
$('wbProductSearch')?.addEventListener('input',()=>{clearTimeout(window.__wbProductSearchTimer);window.__wbProductSearchTimer=setTimeout(()=>loadWbProducts(false),40);});
$('wbCredentialSelect')?.addEventListener('change',async e=>{state.wbCredentialId=e.target.value||'';fcR20Remember('fc_wb_credential',state.wbCredentialId);state.wbPage=1;await loadWbWarehouses(false);await loadWbOrders(false);});
$('wbProductCredentialSelect')?.addEventListener('change',async e=>{state.wbProductCredentialId=e.target.value||'';fcR20Remember('fc_wb_product_credential',state.wbProductCredentialId);await loadWbProducts(false);});
$('importWbProductsBtn')?.addEventListener('click',async()=>{try{const cred=wbProductCredential();if(!cred)throw new Error('Подключите кабинет WB в Личном кабинете');const articles=parseWbProductArticles();if(!articles.length)throw new Error('Введите хотя бы один артикул WB или артикул продавца');const btn=$('importWbProductsBtn');btn.disabled=true;btn.textContent='Импорт…';const r=await api('/marketplaces/wb/products/import',{method:'POST',body:JSON.stringify({credential_id:cred,articles,import_all:false}),timeoutMs:90000});await loadWbProducts(true);toast(`Импортировано карточек: ${r.imported}${r.missing?.length?` · не найдено: ${r.missing.length}`:''}`);btn.disabled=false;btn.textContent='Импортировать';}catch(e){$('importWbProductsBtn').disabled=false;$('importWbProductsBtn').textContent='Импортировать';toast(e.message,true)}});
function configureLocalWbProductCardsUi(){
  const btn=$('importAllWbProductsBtn');if(!btn)return;btn.textContent='Дополнить из WB';btn.title='Первый раз загрузить карточки WB в локальную базу; дальше — получать новые и изменённые. Открытие и поиск WB не вызывают.';
  const area=$('wbProductArticles');if(area){area.placeholder='nmID / артикул продавца — только для точечного импорта';const label=area.closest('label');if(label&&label.firstChild)label.firstChild.textContent='Точечный импорт карточек WB';}
  const importOne=$('importWbProductsBtn');if(importOne)importOne.title='Импортировать указанные карточки. Это не фильтр списка.';
  const wrap=btn.parentElement;if(wrap&&!wrap.querySelector('[data-wb-products-full-sync]')){const full=document.createElement('button');full.type='button';full.className='secondary mini';full.dataset.wbProductsFullSync='1';full.textContent='Полная пересинхронизация';full.title='Заново пройти весь каталог WB. Обычно не требуется.';wrap.appendChild(full);}
}
configureLocalWbProductCardsUi();
(function configureProductBaseR100(){
  titles.products='Карточки WB';
  const nav=document.querySelector('[data-page="products"]');if(nav){const own=[...nav.childNodes].find(n=>n.nodeType===3&&n.textContent.trim());if(own)own.textContent=' Карточки WB';else{const label=[...nav.querySelectorAll('span,b')].find(n=>/карточ|товар/i.test(n.textContent||''));if(label)label.textContent='Карточки WB';}nav.setAttribute('aria-label','Карточки WB');nav.title='Карточки WB';}
  const page=$('page-products');if(page){const heading=page.querySelector('h1,h2');if(heading&&/товар|карточ/i.test(heading.textContent||''))heading.textContent='Карточки WB';const host=$('importAllWbProductsBtn')?.parentElement;if(host&&!host.querySelector('[data-product-base-open]')){const add=document.createElement('button');add.type='button';add.className='secondary mini';add.dataset.productBaseOpen='1';add.textContent='База товаров';add.title='Открыть отдельную локальную базу ШК / артикул / размер';host.appendChild(add);add.addEventListener('click',()=>go('localcatalog'));}}
  const hint=$('wbProductsPersistHint');if(hint)hint.textContent='Карточки WB хранятся локально. Поиск не обращается к WB; обновление только по кнопке «Дополнить из WB».';
})();
$('importAllWbProductsBtn')?.addEventListener('click',async()=>{const btn=$('importAllWbProductsBtn');try{const cred=wbProductCredential();if(!cred)throw new Error('Подключите кабинет WB');btn.disabled=true;btn.textContent='Проверяем изменения…';const r=await api(`/marketplaces/wb/products/import-changes?credential_id=${encodeURIComponent(cred)}`,{method:'POST',timeoutMs:180000});await loadWbProducts(true);toast(r.initial?`Локальная копия WB создана: ${r.imported} карточек · запросов к WB: ${r.external_api_calls}`:`Карточки обновлены: новых ${r.created}, изменённых ${r.updated} · запросов к WB: ${r.external_api_calls}`);if(r.truncated)toast('Изменений очень много. Нажмите «Дополнить из WB» ещё раз, чтобы продолжить с сохранённого курсора.');}catch(e){toast(e.message,true)}finally{btn.disabled=false;btn.textContent='Дополнить из WB';}});
$('importAllWbProductsBtn')?.parentElement?.addEventListener('click',async e=>{const btn=e.target.closest('[data-wb-products-full-sync]');if(!btn)return;try{const cred=wbProductCredential();if(!cred)throw new Error('Подключите кабинет WB');if(!confirm('Полностью пересинхронизировать все карточки WB? Это нужно только при большом пропуске или повреждённой локальной базе.'))return;btn.disabled=true;btn.textContent='Полная синхронизация…';const r=await api(`/marketplaces/wb/products/import-changes?credential_id=${encodeURIComponent(cred)}&full=true`,{method:'POST',timeoutMs:240000});await loadWbProducts(true);toast(`Полная синхронизация: ${r.imported} карточек · запросов к WB: ${r.external_api_calls}`);}catch(err){toast(err.message,true)}finally{btn.disabled=false;btn.textContent='Полная пересинхронизация';}});
$('wbProductsGrid')?.addEventListener('click',async e=>{const hide=e.target.closest('[data-wb-product-hide]'),stock=e.target.closest('[data-wb-product-stock]'),open=e.target.closest('[data-wb-product-open]');if(hide){const id=hide.dataset.wbProductHide;if(!confirm('Убрать карточку только из локального списка «Карточки WB»? В Wildberries ничего не удалится; остатки, история и «База товаров» сохранятся.'))return;try{await api(`/warehouse-ui/catalog/wb-cards/${encodeURIComponent(id)}/hide?client_id=${encodeURIComponent(state.clientId)}`,{method:'POST'});state.wbProductIndex=(state.wbProductIndex||[]).filter(x=>String(x.id)!==String(id));state.wbProductHiddenCount=Number(state.wbProductHiddenCount||0)+1;try{localStorage.removeItem(wbProductsCacheKey());}catch{}await loadWbProducts(false);toast('Карточка убрана из локального списка. Товар и история не удалены.');}catch(err){toast(err.message,true)}return;}if(stock){openInventoryForWbProduct(stock.dataset.wbProductStock);return;}if(open)openWbProduct(open.dataset.wbProductOpen).catch(err=>toast(err.message,true));});
$('wbProductModal')?.addEventListener('click',async e=>{
  if(e.target.closest('[data-close-product-modal]')){$('wbProductModal').classList.add('hidden');return;}
  const photo=e.target.closest('[data-product-photo]');if(photo){const main=$('wbProductMainPhoto');if(main)main.src=photo.dataset.productPhoto;document.querySelectorAll('#wbProductModal .product-thumb').forEach(x=>x.classList.toggle('active',x===photo));return;}
  const stock=e.target.closest('[data-wb-product-stock]'),sizeStock=e.target.closest('[data-wb-card-stock]'),b=e.target.closest('[data-wb-card-label]');
  if(stock){$('wbProductModal').classList.add('hidden');openInventoryForWbProduct(stock.dataset.wbProductStock);return;}
  if(sizeStock){$('wbProductModal').classList.add('hidden');openInventoryForWbProduct(sizeStock.dataset.wbCardStock,sizeStock.dataset.barcode);return;}
  if(b){try{await printWbCatalogLabel(b.dataset.wbCardLabel,b.dataset.barcode);toast('Штрихкод товара отправлен на печать');}catch(err){toast(err.message,true)}}
});

const OZON_PACKED_STATUSES=new Set(['awaiting_deliver','awaiting_delivery','ready_to_ship','ready_for_shipment']);
function ozonCredential(){return $('ozonCredentialSelect')?.value||state.ozonCredentialId||'';}
function ozonStatusName(value){const status=String(value||'').toLowerCase();return ({awaiting_packaging:'Ожидает упаковки',awaiting_deliver:'Упаковано · готово к отгрузке',awaiting_delivery:'Упаковано · готово к отгрузке',ready_to_ship:'Упаковано · готово к отгрузке',ready_for_shipment:'Упаковано · готово к отгрузке',delivering:'Доставляется',delivered:'Доставлено',cancelled:'Отменено',canceled:'Отменено'}[status]||value||'—');}
function installOzonWorkspace(){
  const page=$('page-ozon');if(!page||$('ozonFbsControls'))return;
  const panel=document.createElement('section');panel.id='ozonFbsControls';panel.className='ozon-fbs-controls';panel.innerHTML=`<div class="ozon-control-head"><div><span class="kicker">OZON FBS</span><h3>Склад и отгрузка</h3><p>Отгрузка создаётся только из отправлений, которые Ozon уже показывает упакованными.</p></div><span id="ozonConnectionState" class="tag">Подключение не проверено</span></div><div class="ozon-control-grid"><label>Склад Ozon<select id="ozonWarehouseSelect"><option value="">Все склады</option></select></label><label>Дата отгрузки<input id="ozonDepartureDate" type="date"/></label><div class="button-row"><button id="refreshOzonWarehousesBtn" type="button" class="secondary">Обновить склады</button><button id="ozonShipPackedBtn" type="button" class="primary" disabled>Создать отгрузку из упакованных</button></div></div><small id="ozonShipmentHint">Можно выбрать отправления галочками. Если ничего не выбрано, в отгрузку попадут все видимые упакованные отправления выбранного склада.</small>`;
  const table=$('ozonPostingsBody')?.closest('.table-wrap')||$('ozonPostingsBody')?.closest('table');if(table)table.insertAdjacentElement('beforebegin',panel);else page.appendChild(panel);
  const today=new Date();$('ozonDepartureDate').value=[today.getFullYear(),String(today.getMonth()+1).padStart(2,'0'),String(today.getDate()).padStart(2,'0')].join('-');
  $('refreshOzonWarehousesBtn').addEventListener('click',()=>loadOzonWarehouses(true).catch(e=>toast(e.message,true)));
  $('ozonWarehouseSelect').addEventListener('change',async e=>{state.ozonWarehouseId=e.target.value||'';localStorage.setItem(scopedStorageKey('fc_ozon_warehouse',state.warehouseId),state.ozonWarehouseId);await loadOzonPostings();});
  $('ozonShipPackedBtn').addEventListener('click',createOzonShipmentFromPacked);
  if(!document.getElementById('ozonWorkspaceStyles')){const style=document.createElement('style');style.id='ozonWorkspaceStyles';style.textContent='.ozon-fbs-controls{display:grid;gap:14px;margin:14px 0;padding:16px;border:1px solid #c7d7fe;border-radius:15px;background:#f5f8ff}.ozon-control-head{display:flex;align-items:flex-start;justify-content:space-between;gap:15px}.ozon-control-head h3,.ozon-control-head p{margin:0}.ozon-control-head p{color:#667085}.ozon-control-grid{display:grid;grid-template-columns:minmax(220px,1fr) minmax(170px,.45fr) auto;align-items:end;gap:12px}.ozon-control-grid label{display:grid;gap:6px}.ozon-fbs-controls>small{color:#667085}.ozon-ready{color:#067647;font-weight:750}.ozon-not-ready{color:#b54708}@media(max-width:850px){.ozon-control-grid{grid-template-columns:1fr}.ozon-control-head{display:grid}}';document.head.appendChild(style);}
}
installOzonWorkspace();
async function loadOzonWarehouses(showToast=false){
  const cred=ozonCredential(),select=$('ozonWarehouseSelect'),status=$('ozonConnectionState');if(!cred){state.ozonWarehouses=[];if(select)select.innerHTML='<option value="">Сначала подключите Ozon</option>';if(status)status.textContent='Ozon не подключён';return;}
  try{const r=await api(`/marketplaces/ozon/warehouses?credential_id=${encodeURIComponent(cred)}`);state.ozonWarehouses=r.warehouses||[];const stored=localStorage.getItem(scopedStorageKey('fc_ozon_warehouse',state.warehouseId))||state.ozonWarehouseId||'',valid=state.ozonWarehouses.some(w=>String(w.warehouse_id??w.id)===String(stored));state.ozonWarehouseId=valid?String(stored):'';if(select){select.innerHTML='<option value="">Все склады Ozon</option>'+state.ozonWarehouses.map(w=>`<option value="${esc(w.warehouse_id??w.id)}">${esc(w.name||w.warehouse_name||`Склад ${w.warehouse_id??w.id}`)}</option>`).join('');select.value=state.ozonWarehouseId;}if(status){status.textContent=`Ozon подключён · складов: ${state.ozonWarehouses.length}`;status.className='tag ozon-ready';}if(r.warning)toast(r.warning);else if(showToast)toast(`Склады Ozon обновлены: ${state.ozonWarehouses.length}`);}catch(e){if(status){status.textContent='Ошибка подключения Ozon';status.className='tag ozon-not-ready';}throw e;}
}
$('loadOzonPostingsBtn')?.addEventListener('click',loadOzonPostings);
async function loadOzonPostings(){try{const cred=ozonCredential();if(!cred)throw new Error('Сначала подключите Ozon');const params=new URLSearchParams({credential_id:cred,days:'31',limit:'1000'});if(state.warehouseId)params.set('warehouse_id',state.warehouseId);if(state.ozonWarehouseId)params.set('ozon_warehouse_id',state.ozonWarehouseId);const r=await api(`/marketplaces/ozon/postings?${params.toString()}`);state.ozonPostings=(r.postings||[]).map(p=>({...p,is_packed:p.is_packed??OZON_PACKED_STATUSES.has(String(p.status||'').toLowerCase())}));renderOzonPostings();toast(`Ozon: отправлений ${r.count}`);}catch(e){toast(e.message,true)}}
function renderOzonPostings(){
  $('ozonPostingsBody').innerHTML=state.ozonPostings.length?state.ozonPostings.map(p=>{const products=(p.products||[]).map(x=>`${x.local_sku?skuLabel(x.local_sku):(x.offer_id||x.name||'—')} × ${x.quantity}`).join('; ');const stock=(p.products||[]).map(x=>stockText(x.stock)).join(' | '),warehouse=p.ozon_warehouse_name||p.ozon_warehouse_id||'';return `<tr><td><input class="ozon-posting-check" type="checkbox" value="${esc(p.posting_number)}" /></td><td><b>${esc(p.posting_number)}</b><small class="subline">${esc(p.order_number||'')}${warehouse?` · ${esc(warehouse)}`:''}</small></td><td class="${p.is_packed?'ozon-ready':'ozon-not-ready'}">${esc(ozonStatusName(p.status))}</td><td>${esc(products)}</td><td>${esc(stock)}</td></tr>`;}).join(''):'<tr><td colspan="5" class="empty-state">Отправлений Ozon не найдено.</td></tr>';if($('ozonCheckAll'))$('ozonCheckAll').checked=false;syncOzonButtons();
}
$('ozonPostingsBody')?.addEventListener('change',e=>{if(e.target.classList.contains('ozon-posting-check'))syncOzonButtons();});
$('ozonCheckAll')?.addEventListener('change',e=>{document.querySelectorAll('.ozon-posting-check').forEach(x=>x.checked=e.target.checked);syncOzonButtons();});
$('ozonPrintLabelsBtn')?.addEventListener('click',async()=>{try{const nums=selectedValues('.ozon-posting-check');if(nums.length>20)throw new Error('Ozon: печатайте не более 20 этикеток за один запрос');const blob=await apiBlob('/marketplaces/ozon/labels.pdf',{method:'POST',body:JSON.stringify({credential_id:ozonCredential(),posting_numbers:nums})});saveBlob(blob,'Ozon_этикетки.pdf');toast('Этикетки Ozon готовы');}catch(e){toast(e.message,true)}});
async function createOzonShipmentFromPacked(){
  const button=$('ozonShipPackedBtn');try{let numbers=selectedValues('.ozon-posting-check'),rows=numbers.length?state.ozonPostings.filter(p=>numbers.includes(String(p.posting_number))):state.ozonPostings.filter(p=>p.is_packed);if(!rows.length)throw new Error('Нет упакованных отправлений Ozon');if(rows.some(p=>!p.is_packed))throw new Error('В выборе есть неупакованные отправления. Оставьте только статус «Упаковано · готово к отгрузке».');numbers=rows.map(p=>String(p.posting_number));const methods=new Set(rows.map(p=>p.delivery_method_id).filter(x=>x!==null&&x!==undefined));if(!confirm(`Создать реальную отгрузку Ozon из ${numbers.length} упакованных отправлений${methods.size>1?` (${methods.size} способов доставки)`:''}?\n\nFulfillCore создаст документ отгрузки с точным составом выбранных отправлений. Подтверждение и фактическая передача перевозчику отдельно.\n\nВы уверены?`))return;button.disabled=true;button.textContent='Создаём отгрузку…';const date=$('ozonDepartureDate')?.value||null,r=await api('/marketplaces/ozon/shipments/from-packed',{method:'POST',body:JSON.stringify({credential_id:ozonCredential(),posting_numbers:numbers,departure_date:date})});const created=r.created||[],failed=r.failed||[];await loadOzonPostings();if(failed.length){const ids=[...created.map(x=>x.carriage_id),...failed.map(x=>x.carriage_id)].filter(Boolean).join(', ')||'нет';toast(`${r.warning||'Часть отгрузок не создана.'} Возможные ID: ${ids}. Ошибок: ${failed.length}`,true);return;}toast(`Отгрузка Ozon создана: ${created.map(x=>x.carriage_id).join(', ')} · отправлений ${r.posting_count}`);}catch(e){toast(e.message,true);}finally{syncOzonButtons();}
}
$('ozonCredentialSelect')?.addEventListener('change',async e=>{state.ozonCredentialId=e.target.value||'';fcR20Remember('fc_ozon_credential',state.ozonCredentialId);state.ozonWarehouseId='';state.ozonWarehouses=[];state.ozonPostings=[];renderOzonPostings();if(state.ozonCredentialId){try{await loadOzonWarehouses();await loadOzonPostings();}catch(error){toast(error.message,true);}}});

(function installWarehouseManagementStyles(){
  if(document.getElementById('warehouseManagementStyles'))return;
  const style=document.createElement('style');style.id='warehouseManagementStyles';style.textContent=`
    .fbs-stock-state{display:grid;gap:7px;min-width:180px}.fbs-stock-state>span{font-weight:750}.fbs-stock-state.placed>span{color:#067647}.fbs-stock-state.missing>span,.fbs-stock-state.stale>span{color:#b54708}.fbs-stock-state.error>span{color:#b42318}.fbs-stock-state.unknown>span{color:#667085}
    .container-select{display:flex;align-items:center;gap:8px}.selected-row td{background:#eef4ff!important}.container-bulk-toolbar{align-items:center;margin:12px 0}.container-bulk-toolbar small{color:#667085}
    .receive-publish-target{display:grid;gap:6px;padding:12px 14px;border:1px solid #c7d7fe;border-radius:12px;background:#f5f8ff}.receive-publish-target>span{font-weight:750}.receive-publish-target small{color:#475467}
    .receiving-wb-queue{display:flex;align-items:center;justify-content:space-between;gap:18px;grid-column:1/-1}.receiving-wb-queue h3{margin:3px 0}.receiving-wb-queue small{color:#667085}
    .receipt-cancelled{opacity:.72;background:#f8fafc}.receipt-cancelled .receipt-list-stats span:last-child{color:#b42318;font-weight:750}
    @media(max-width:700px){.receiving-wb-queue{align-items:stretch;flex-direction:column}}
  `;document.head.appendChild(style);
})();

bootstrap().then(()=>{window.FCTsdDesktopReady=true;window.dispatchEvent(new Event('fc:desktop-ready'));}).catch(e=>toast(e.message,true));


$('logoutBtn').addEventListener('click',async()=>{try{await api('/auth/logout',{method:'POST'});}catch(e){}location.href='/login';});


// ---------------- Палетная приёмка ----------------
function palletReceiptStorageKey(){return `fc_pallet_receipt_id:${state.clientId||'none'}:${state.warehouseId||'none'}`;}
const legacyPalletReceiptId=localStorage.getItem('fc_pallet_receipt_id')||'';
if(legacyPalletReceiptId&&!localStorage.getItem(palletReceiptStorageKey()))localStorage.setItem(palletReceiptStorageKey(),legacyPalletReceiptId);
localStorage.removeItem('fc_pallet_receipt_id');
let currentPalletReceiptId = localStorage.getItem(palletReceiptStorageKey()) || '';
let palletReceiptItems=[];
function palletReceiptShowHome(){
  $('palletReceiptHome')?.classList.remove('hidden');$('palletReceiptDetailView')?.classList.add('hidden');
  fcR20Remember('fc_pallet_receipt_view','home');
}
function palletReceiptShowDetail(){
  $('palletReceiptHome')?.classList.add('hidden');$('palletReceiptDetailView')?.classList.remove('hidden');
  fcR20Remember('fc_pallet_receipt_view','detail');
}
function fmtReceiptDate(v){if(!v)return '—';try{return new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow',day:'2-digit',month:'2-digit',year:'numeric',hour:'2-digit',minute:'2-digit'});}catch{return v;}}
function renderPalletReceiptList(){
  const el=$('palletReceiptList');if(!el)return;
  if(!palletReceiptItems.length){el.innerHTML='<div class="empty-state">Приёмок пока нет</div>';return;}
  el.innerHTML=palletReceiptItems.map(x=>{const cancelled=String(x.status||'').toUpperCase()==='CANCELLED',status=cancelled?'Отменена':String(x.status||'').toUpperCase()==='LOADED'?'Товар принят':'Открыта';return `<div class="receipt-list-row ${cancelled?'receipt-cancelled':''}"><div class="receipt-list-main"><b>${esc(x.name||'Палетная приёмка')}</b><small>${fmtReceiptDate(x.created_at)}${x.reference?' · '+esc(x.reference):''}</small><div class="receipt-list-stats"><span>${x.pallet_count||0} палет</span><span>${x.box_count||0} коробов</span><span>${esc(status)}</span></div></div><div class="row-actions"><button type="button" class="primary mini" data-open-pallet-receipt="${esc(x.id)}">Открыть</button>${cancelled?'<span class="tag">История сохранена</span>':`<button type="button" class="danger mini" data-delete-pallet-receipt="${esc(x.id)}">Удалить ошибочную</button>`}</div></div>`;}).join('');
}
async function loadPalletReceipts(){
  if(!state.clientId||!state.warehouseId)return;
  const r=await api(`/receiving/pallet-receipts?client_id=${encodeURIComponent(state.clientId)}&warehouse_id=${encodeURIComponent(state.warehouseId)}`);
  palletReceiptItems=r.items||[];renderPalletReceiptList();
  const sel=$('palletReceiptSelect');if(sel){sel.innerHTML='<option value="">Выберите приёмку</option>'+palletReceiptItems.map(x=>`<option value="${esc(x.id)}">${esc(x.name)} · ${x.pallet_count||0} пал. · ${x.box_count||0} кор.</option>`).join('');if(currentPalletReceiptId&&palletReceiptItems.some(x=>x.id===currentPalletReceiptId))sel.value=currentPalletReceiptId;}
  if(currentPalletReceiptId&&!palletReceiptItems.some(x=>x.id===currentPalletReceiptId)){currentPalletReceiptId='';localStorage.removeItem(palletReceiptStorageKey());palletReceiptShowHome();}
}
async function openPalletReceipt(id){
  currentPalletReceiptId=id||'';if(!currentPalletReceiptId)return palletReceiptShowHome();
  localStorage.setItem(palletReceiptStorageKey(),currentPalletReceiptId);palletReceiptShowDetail();
  await loadPalletReceiptDetail();$('palletReceivePallet')?.focus();
}
async function loadPalletReceiptDetail(){
  const el=$('palletReceiptStructure'),sum=$('palletReceiptSummary');if(!el||!currentPalletReceiptId)return;
  const r=await api(`/receiving/pallet-receipts/${encodeURIComponent(currentPalletReceiptId)}`),receipt=r.receipt||{};
  $('palletReceiptDetailTitle').textContent=receipt.name||'Палетная приёмка';
  const cancelled=String(receipt.status||'').toUpperCase()==='CANCELLED';$('palletReceiptDetailMeta').textContent=`${fmtReceiptDate(receipt.created_at)}${receipt.reference?' · '+receipt.reference:''} · ${cancelled?'Отменена':receipt.status||'OPEN'}`;
  const deleteButton=$('deletePalletReceiptBtn');if(deleteButton){deleteButton.disabled=cancelled;deleteButton.textContent=cancelled?'Приёмка отменена':'Удалить ошибочную приёмку';}
  const pallets=r.pallets||[],boxes=r.boxes||[];const currentCode=($('palletReceivePallet')?.value||'').trim();
  if(sum){const totalBoxes=boxes.length,totalQty=boxes.reduce((a,b)=>a+Number(b.quantity||0),0);sum.className='pallet-summary';sum.innerHTML=`<div><small>Палет</small><b>${pallets.length}</b></div><div><small>Коробов</small><b>${totalBoxes}</b></div><div><small>Товаров</small><b>${fmtQty(totalQty)} шт.</b></div>`;}
  if(!pallets.length){el.className='activity-list empty-state';el.textContent='Палет пока нет';return;}
  el.className='activity-list';el.innerHTML=pallets.map((p,pidx)=>{const bs=boxes.filter(b=>b.pallet_code===p.pallet_code);const qty=bs.reduce((a,b)=>a+Number(b.quantity||0),0);const isCurrent=currentCode&&String(p.pallet_code)===currentCode;const collapsed=!isCurrent&&pidx<pallets.length-1;const rows=bs.length?bs.map((b,i)=>`<div class="pallet-box-row"><span class="pallet-box-index">${i+1}</span><span class="pallet-box-code">${esc(b.box_code)}${b.container_id?'<small class="subline good">создан в системе</small>':''}</span><span class="pallet-box-qty">${Number(b.quantity||0)>0?fmtQty(b.quantity)+' шт.':'короб пуст'}</span>${Number(b.quantity||0)<=0?`<button type="button" class="mini" data-delete-pallet-box="${esc(b.box_code)}">Удалить</button>`:'<span></span>'}</div>`).join(''):'<div class="pallet-empty">Коробов пока нет</div>';const full=bs.length>=16;return `<div class="pallet-card ${isCurrent?'current':''} ${collapsed?'collapsed':''}"><div class="pallet-card-head" data-toggle-pallet="${esc(p.pallet_code)}"><span class="pallet-number">${pidx+1}</span><div class="pallet-title"><b>Палета ${esc(p.pallet_code)}</b><small>${p.closed_at?'Закрыта':'Открыта'} · ${fmtQty(qty)} шт.</small></div><div class="pallet-head-actions"><span class="pallet-progress ${full?'full':''}">${bs.length}/16</span><span class="pallet-toggle">▾</span><button type="button" class="mini" data-delete-receipt-pallet="${esc(p.pallet_code)}">Удалить</button></div></div><div class="pallet-box-list">${rows}</div></div>`;}).join('');
}
async function deletePalletReceipt(receiptId){
  if(!receiptId)throw new Error('Приёмка не выбрана');const item=palletReceiptItems.find(x=>x.id===receiptId),label=item?.name||'эту приёмку';
  if(String(item?.status||'').toUpperCase()==='CANCELLED')throw new Error('Эта приёмка уже отменена');
  const loaded=String(item?.status||'').toUpperCase()==='LOADED';
  if(!confirm(`Удалить ошибочную приёмку «${label}»?\n\n${loaded?'Добавленные ею локальные остатки будут сняты обратной корректировкой. ':''}Документ не исчезнет из журнала: он останется со статусом «Отменена».\n\nВы уверены?`))return;
  const syncWb=loaded&&confirm('Также пересчитать остатки WB FBS после отмены?\n\n«ОК» — обновить WB. «Отмена» — отменить только локальную приёмку.');
  const params=new URLSearchParams({reverse_stock:loaded?'true':'false',sync_wb:syncWb?'true':'false'});const wbId=Number(state.wbWarehouseFilter||0)||null;if(wbId)params.set('wb_warehouse_id',String(wbId));
  const result=await api(`/receiving/pallet-receipts/${encodeURIComponent(receiptId)}?${params.toString()}`,{method:'DELETE'});
  reconcilePendingWbAfterCancellation(result);
  if(currentPalletReceiptId===receiptId){currentPalletReceiptId='';localStorage.removeItem(palletReceiptStorageKey());palletReceiptShowHome();}await Promise.all([loadPalletReceipts(),loadContextData()]);
  state.inventoryCatalogLoaded=false;const wbFailed=Number(result.wb_failed||0);toast(wbFailed?`Приёмка отменена, но WB не обновил ${wbFailed} поз.`:syncWb?'Приёмка отменена, WB пересчитан':'Приёмка отменена, история сохранена',wbFailed>0);
}
$('palletReceiptList')?.addEventListener('click',e=>{const b=e.target.closest('[data-open-pallet-receipt]'),d=e.target.closest('[data-delete-pallet-receipt]');if(b)openPalletReceipt(b.dataset.openPalletReceipt).catch(x=>toast(x.message,true));if(d)deletePalletReceipt(d.dataset.deletePalletReceipt).catch(x=>toast(x.message,true));});
$('refreshPalletReceiptListBtn')?.addEventListener('click',()=>loadPalletReceipts().catch(e=>toast(e.message,true)));
$('backToPalletReceiptsBtn')?.addEventListener('click',async()=>{palletReceiptShowHome();await loadPalletReceipts().catch(e=>toast(e.message,true));});
$('createPalletReceiptBtn')?.addEventListener('click',async()=>{try{await ensureContext();const r=await api('/receiving/pallet-receipts',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,name:$('palletReceiptName').value.trim()||null,reference:$('palletReceiptReference').value.trim()||null})});await loadPalletReceipts();toast('Палетная приёмка создана');await openPalletReceipt(r.id);}catch(e){toast(e.message,true)}});
$('deletePalletReceiptBtn')?.addEventListener('click',()=>deletePalletReceipt(currentPalletReceiptId).catch(e=>toast(e.message,true)));
$('exportPalletReceiptBtn')?.addEventListener('click',async()=>{try{if(!currentPalletReceiptId)throw new Error('Сначала откройте приёмку');const blob=await apiBlob(`/receiving/pallet-receipts/${encodeURIComponent(currentPalletReceiptId)}/export.xlsx`);const item=palletReceiptItems.find(x=>x.id===currentPalletReceiptId);saveBlob(blob,`Приемка_${(item?.name||currentPalletReceiptId).replace(/[^a-zA-Zа-яА-Я0-9_-]+/g,'_')}.xlsx`);toast('Excel сформирован');}catch(e){toast(e.message,true)}});
$('refreshPalletReceiptBtn')?.addEventListener('click',()=>loadPalletReceiptDetail().catch(e=>toast(e.message,true)));
async function addReceiptPallet(){if(!currentPalletReceiptId)throw new Error('Сначала откройте приёмку');const code=$('palletReceivePallet').value.trim();if(!code)throw new Error('Отсканируйте палету');await api(`/receiving/pallet-receipts/${encodeURIComponent(currentPalletReceiptId)}/pallets`,{method:'POST',body:JSON.stringify({pallet_code:code})});$('palletReceivePalletHint').textContent=`Текущая палета: ${code}`;$('palletReceiveBox').value='';await loadPalletReceiptDetail();$('palletReceiveBox').focus();toast('Палета принята');}
$('addPalletBtn')?.addEventListener('click',()=>addReceiptPallet().catch(e=>toast(e.message,true)));$('palletReceivePallet')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();addReceiptPallet().catch(x=>toast(x.message,true));}});
function nextPalletCode(code){const s=String(code||'').trim();if(/^\d+$/.test(s))return String(Number(s)+1);const m=s.match(/^(.*?)(\d+)$/);if(m){const n=String(Number(m[2])+1).padStart(m[2].length,'0');return m[1]+n;}return s?`${s}-2`:'1';}
async function activateNextPallet(current,reason=''){const next=nextPalletCode(current);$('palletReceivePallet').value=next;await api(`/receiving/pallet-receipts/${encodeURIComponent(currentPalletReceiptId)}/pallets`,{method:'POST',body:JSON.stringify({pallet_code:next})});$('palletReceivePalletHint').textContent=`Текущая палета: ${next}${reason?' · '+reason:''}`;$('palletReceiveBoxHint').textContent='';await loadPalletReceiptDetail();$('palletReceiveBox').focus();toast(`Начата следующая палета ${next}`);}
async function addReceiptBox(force=false){if(!currentPalletReceiptId)throw new Error('Сначала откройте приёмку');const pallet=$('palletReceivePallet').value.trim(),box=$('palletReceiveBox').value.trim();if(!pallet)throw new Error('Сначала укажите палету');if(!box)throw new Error('Отсканируйте короб');let r;try{r=await api(`/receiving/pallet-receipts/${encodeURIComponent(currentPalletReceiptId)}/boxes`,{method:'POST',body:JSON.stringify({pallet_code:pallet,box_code:box,force})});}catch(e){if(!force&&/уже принят|уже стоит/i.test(e.message)){if(confirm(`${e.message}\n\nВы уверены, что хотите принять этот короб?`))return addReceiptBox(true);}throw e;}$('palletReceiveBox').value='';$('palletReceiveBoxHint').textContent=`Короб ${box} создан в системе пустым · палета ${pallet} · ${r.box_count||''}/16`;await loadPalletReceiptDetail();if(r.pallet_closed){await activateNextPallet(pallet,'предыдущая заполнена 16/16');return;}$('palletReceiveBox').focus();}
$('addPalletBoxBtn')?.addEventListener('click',()=>addReceiptBox().catch(e=>toast(e.message,true)));$('palletReceiveBox')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();addReceiptBox().catch(x=>toast(x.message,true));}});
$('closePalletBtn')?.addEventListener('click',async()=>{try{const pallet=$('palletReceivePallet').value.trim();if(!pallet)throw new Error('Нет текущей палеты');if(!confirm(`Закрыть палету ${pallet} и начать следующую?`))return;await api(`/receiving/pallet-receipts/${encodeURIComponent(currentPalletReceiptId)}/pallets/close`,{method:'POST',body:JSON.stringify({pallet_code:pallet})});await activateNextPallet(pallet,'предыдущая закрыта вручную');}catch(e){toast(e.message,true)}});
$('palletReceiptStructure')?.addEventListener('click',async e=>{const t=e.target.closest('[data-toggle-pallet]'),b=e.target.closest('[data-delete-pallet-box]'),p=e.target.closest('[data-delete-receipt-pallet]');if(t&&!b&&!p){e.target.closest('.pallet-card')?.classList.toggle('collapsed');return;}if(!b&&!p)return;e.stopPropagation();try{if(b){const code=b.dataset.deletePalletBox;if(!confirm(`Удалить короб ${code}?\n\nВы уверены?`))return;await api(`/receiving/pallet-receipts/${encodeURIComponent(currentPalletReceiptId)}/boxes/${encodeURIComponent(code)}`,{method:'DELETE'});}else{const code=p.dataset.deleteReceiptPallet;if(!confirm(`Удалить палету ${code} вместе со всеми коробами?\n\nВы уверены?`))return;await api(`/receiving/pallet-receipts/${encodeURIComponent(currentPalletReceiptId)}/pallets/${encodeURIComponent(code)}`,{method:'DELETE'});}await Promise.all([loadPalletReceiptDetail(),loadPalletReceipts()]);}catch(err){toast(err.message,true)}});

function updatePalletLayoutFileUi(){
  const input=$('palletLayoutFile'), btn=$('clearPalletLayoutFileBtn'), name=$('palletLayoutFileName');
  if(!input)return; const f=input.files&&input.files[0];
  if(btn)btn.classList.toggle('hidden',!f);
  if(name)name.textContent=f?f.name:'Файл не выбран';
}
$('palletLayoutFile')?.addEventListener('change',updatePalletLayoutFileUi);
$('clearPalletLayoutFileBtn')?.addEventListener('click',()=>{const input=$('palletLayoutFile');if(input)input.value='';updatePalletLayoutFileUi();toast('Выбранный Excel удалён из формы');});
$('uploadPalletLayoutBtn')?.addEventListener('click',async()=>{try{if(!currentPalletReceiptId)throw new Error('Сначала откройте приёмку');const file=$('palletLayoutFile').files[0];if(!file)throw new Error('Выберите Excel-файл');const stockClass=$('palletReceiveStockClass')?.value==='MAIN'?'MAIN':'FBS';const publishNow=stockClass==='FBS'&&($('palletReceivePublishTarget')?.value||'LOCAL_WB')==='LOCAL_WB';const destination=stockClass==='FBS'?'СРАЗУ В FBS':'на Основной склад';if(!confirm(`Добавить всю раскладку в короба и ${destination}?\n\n${stockClass==='FBS'?(publishNow?'После локальной приёмки остатки будут отправлены на WB FBS.':'WB сейчас не меняется; товар останется локально в FBS.'):'WB не изменится.'}`))return;const fd=new FormData();fd.append('file',file);fd.append('performed_by',$('palletLayoutBy').value.trim());fd.append('stock_class',stockClass);const r=await api(`/receiving/pallet-receipts/${encodeURIComponent(currentPalletReceiptId)}/layout`,{method:'POST',body:fd,timeoutMs:180000});let wbResult=null;if(r.pending_lines?.length){if(publishNow)wbResult=await publishFreshReceivingLines(r.pending_lines,'Палетная приёмка');else addPendingWbReceiving(r.pending_lines);}$('palletLayoutResult').innerHTML=`<b>Раскладка добавлена на остатки</b><span>Строк: ${r.rows_imported}. Коробов заполнено: ${r.boxes_filled}. Ошибок: ${r.rows_failed}. ${stockClass==='MAIN'?'Товар принят на Основной склад.':(publishNow?(wbResult?.failed?`Товар принят сразу в FBS. WB: ошибок ${wbResult.failed}; они оставлены в очереди.`:'Товар принят сразу в FBS, WB FBS обновлён.'):'Товар принят сразу в локальный FBS; WB можно обновить позже.')}</span>`;if(!r.rows_failed){$('palletLayoutFile').value=''; updatePalletLayoutFileUi();}await Promise.allSettled([loadContextData(),loadPalletReceiptDetail(),loadPalletReceipts()]);toast(r.rows_failed?'Добавление завершено с ошибками':'Вся раскладка добавлена на остатки',r.rows_failed>0);}catch(e){toast(e.message,true)}});
// Подгружаем палетные приёмки только когда пользователь открыл соответствующую страницу.
const _goBeforePallet = go;
go = function(page){_goBeforePallet(page);if(page==='palletreceive'){const view=localStorage.getItem('fc_pallet_receipt_view');if(view==='detail'&&currentPalletReceiptId)palletReceiptShowDetail();else palletReceiptShowHome();loadPalletReceipts().then(()=>{if(localStorage.getItem('fc_pallet_receipt_view')==='detail'&&currentPalletReceiptId)return loadPalletReceiptDetail();}).catch(e=>toast(e.message,true));}};

// 0.7.9.5 — структурная Excel-приёмка: секция -> палета -> короб -> товар.
let structuredReceipts=[];
let structuredReceiptDetail=null;
function structuredArray(value){if(Array.isArray(value))return value;if(value&&typeof value==='object'){if(Array.isArray(value.items))return value.items;if(Array.isArray(value.rows))return value.rows;if(Array.isArray(value.sections))return value.sections;return Object.values(value).filter(x=>x&&typeof x==='object');}return [];}
function structuredStatusLabel(status){return ({READY:'Готово к приёмке',NEEDS_FIX:'Нужно исправить',COMMITTED:'Принято на склад',CANCELLED:'Приёмка отменена',ARCHIVED:'Удалена из списка'}[String(status||'').toUpperCase()]||status||'—');}
function structuredStatusClass(status){const value=String(status||'').toUpperCase();return value==='COMMITTED'||value==='READY'?'good':value==='CANCELLED'?'warn':'bad';}
function installStructuredReceivingPanel(){
  const page=$('page-bulkimport');if(!page||$('structuredReceivingPanel'))return;
  const panel=document.createElement('section');panel.id='structuredReceivingPanel';panel.className='panel structured-receiving-panel';panel.innerHTML=`
    <div class="panel-head"><div><span class="kicker">НОВЫЙ ФОРМАТ EXCEL</span><h2>Структурная приёмка</h2><p>Секция → палета → короб → товар. Сначала файл загружается как черновик и проверяется. Только после проверки нажимаете «Добавить на остатки».</p></div><span class="tag">Без изменения WB до подтверждения</span></div>
    <div class="structured-receive-form">
      <label>Название приёмки<input id="structuredReceiptName" placeholder="Например: Машина 29.08"/></label>
      <label>Комментарий / партия<input id="structuredReceiptReference" placeholder="Номер машины, поставки или партии"/></label>
      <label>Принял<input id="structuredReceiptBy" placeholder="Сотрудник"/></label>
      <label class="structured-file-field">Excel-файл<input id="structuredReceiptFile" type="file" accept=".xlsx"/></label>
      <button id="structuredUploadBtn" type="button" class="primary">1. Загрузить и проверить</button>
    </div>
    <div class="structured-format-note"><b>Поддерживается файл как «Адресное хранение».</b><span>Основные колонки: Секция · Ряд · Паллета · Лимит · Короб · Партия · Источник · Принят на хранение · Артикул · Размер · ШК товара · Остаток. Название короба может быть любым непустым текстом — цифры, BOX_..., буквы и смешанный формат допустимы. Пустые товарные строки создают пустой короб; остаток 0 пропускается без ошибки.</span><div class="button-row"><button type="button" class="secondary" data-structured-template>Скачать шаблон Excel</button><label>Куда принять<select id="structuredReceiveStockClass"><option value="FBS" selected>Сразу в FBS</option><option value="MAIN">Основной склад</option></select></label><label>После приёмки FBS<select id="structuredReceivePublishTarget"><option value="LOCAL_WB" selected>Сразу обновить WB FBS</option><option value="LOCAL">Только FulfillCore, WB позже</option></select></label></div><small>Выбор FBS означает: товар сразу учитывается в контуре FBS, без промежуточного перевода через Основной склад.</small></div>
    <div id="structuredReceiptCurrent" class="structured-current"><div class="empty-state">Загрузите Excel. Склад пока не изменится.</div></div>
    <div class="structured-history-head"><div><h3>Последние структурные приёмки</h3><p>Черновик можно открыть повторно и проверить перед добавлением на остатки.</p></div><button id="refreshStructuredReceiptsBtn" type="button" class="secondary">Обновить</button></div>
    <div id="structuredReceiptHistory" class="structured-history"><div class="empty-state">История ещё не загружена.</div></div>`;
  const anchor=page.querySelector('[data-receiving-client-context]');if(anchor)anchor.insertAdjacentElement('afterend',panel);else page.prepend(panel);
  const style=document.createElement('style');style.id='structuredReceivingStyles';style.textContent=`
    .structured-receiving-panel{display:grid;gap:16px;margin-bottom:18px}.structured-receiving-panel .panel-head p{margin:4px 0 0;color:#667085}.structured-receive-form{display:grid;grid-template-columns:1fr 1fr .75fr 1.2fr auto;gap:10px;align-items:end}.structured-receive-form label{display:grid;gap:6px}.structured-file-field input{padding:8px}.structured-format-note{display:grid;gap:4px;padding:12px 14px;border:1px solid #c7d7fe;border-radius:12px;background:#eef4ff;color:#344054}.structured-format-note span{color:#475467}.structured-current{display:grid;gap:14px}.structured-summary{display:grid;grid-template-columns:repeat(8,minmax(90px,1fr));gap:8px}.structured-kpi{display:grid;gap:2px;padding:11px;border-radius:11px;background:#f8fafc;border:1px solid #eaecf0}.structured-kpi b{font-size:20px}.structured-kpi small{color:#667085}.structured-kpi.error{background:#fff6f5;border-color:#fda29b}.structured-kpi.warn{background:#fffaeb;border-color:#fedf89}.structured-receipt-banner{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;padding:14px;border:1px solid #d0d5dd;border-radius:13px}.structured-receipt-banner h3,.structured-receipt-banner p{margin:0}.structured-receipt-banner p{margin-top:4px;color:#667085}.structured-actions{display:flex;gap:8px;flex-wrap:wrap}.structured-sections{display:grid;gap:10px}.structured-section-card{border:1px solid #dfe4ec;border-radius:13px;background:#fff;overflow:hidden}.structured-section-card>header{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:12px 14px;background:#f8fafc}.structured-section-card h4{margin:0;font-size:18px}.structured-pallet-table td,.structured-pallet-table th{white-space:nowrap}.structured-pallet-table button{padding:6px 9px}.structured-box-viewer{padding:12px 14px;border-top:1px solid #eaecf0;background:#fcfcfd}.structured-box-viewer h4{margin:0 0 8px}.structured-box-status{font-size:12px;font-weight:750}.structured-error-list,.structured-warning-list{padding:12px 14px;border-radius:12px}.structured-error-list{background:#fff6f5;color:#912018}.structured-warning-list{background:#fffaeb;color:#7a2e0e}.structured-error-list h4,.structured-warning-list h4{margin:0 0 7px}.structured-history-head{display:flex;justify-content:space-between;align-items:center;gap:12px;padding-top:6px;border-top:1px solid #eaecf0}.structured-history-head h3,.structured-history-head p{margin:0}.structured-history-head p{color:#667085}.structured-history{display:grid;gap:8px}.structured-history-row{display:grid;grid-template-columns:minmax(220px,1.6fr) repeat(5,minmax(70px,.5fr)) auto;gap:10px;align-items:center;padding:11px 12px;border:1px solid #eaecf0;border-radius:11px;background:#fff}.structured-history-row small{display:block;color:#667085}.structured-history-row strong{font-size:15px}.structured-search-row{display:flex;gap:8px;align-items:end}.structured-search-row label{display:grid;gap:5px;min-width:280px}.structured-search-results{display:grid;gap:6px}.structured-search-result{display:flex;justify-content:space-between;gap:10px;padding:8px 10px;border-radius:9px;background:#f8fafc}.structured-fbs-panel{display:grid;gap:10px;padding:14px;border:1px solid #d0d5dd;border-radius:13px;background:#fcfcfd}.structured-fbs-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.structured-fbs-head h3,.structured-fbs-head p{margin:0}.structured-fbs-head p{margin-top:4px;color:#667085}.structured-fbs-panel td small{display:block;color:#667085}.structured-box-modal{position:fixed;inset:0;z-index:99999;display:flex;align-items:center;justify-content:center;padding:24px}.structured-box-modal[hidden]{display:none}.structured-box-modal-backdrop{position:absolute;inset:0;background:rgba(15,23,42,.55)}.structured-box-modal-card{position:relative;z-index:1;width:min(1050px,96vw);max-height:88vh;overflow:auto;background:#fff;border-radius:14px;padding:16px;box-shadow:0 20px 60px rgba(0,0,0,.28)}.structured-box-contents-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:10px}.structured-box-contents-head h4,.structured-box-contents-head p{margin:0}.storage-pallet-sections{display:grid;gap:18px}.storage-pallet-section{display:grid;gap:10px}.storage-pallet-section-head{display:flex;justify-content:space-between;align-items:center;padding:11px 13px;border-radius:12px;background:#eef4ff}.storage-pallet-section-head h3{margin:2px 0 0}.storage-pallet-section-head>span{font-weight:750;color:#344054}
.structured-fbs-compact{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap}
.structured-fbs-compact small{display:block;margin-top:4px;color:#667085}
.structured-fbs-collapsed{padding:12px 14px}
@media(max-width:1200px){.structured-receive-form{grid-template-columns:1fr 1fr}.structured-summary{grid-template-columns:repeat(4,1fr)}.structured-history-row{grid-template-columns:1fr 1fr 1fr}}@media(max-width:700px){.structured-receive-form,.structured-summary{grid-template-columns:1fr}.structured-receipt-banner,.structured-history-head{flex-direction:column}.structured-history-row{grid-template-columns:1fr}.structured-search-row{align-items:stretch;flex-direction:column}.structured-search-row label{min-width:0}}`;
  document.head.appendChild(style);
  document.querySelector('[data-structured-template]')?.addEventListener('click',()=>{window.location.href=receivingTemplateUrl('STRUCTURED');});
  $('structuredUploadBtn').addEventListener('click',uploadStructuredReceipt);
  $('refreshStructuredReceiptsBtn').addEventListener('click',()=>loadStructuredReceipts(true).catch(e=>toast(e.message,true)));
  const structuredStockSelect=$('structuredReceiveStockClass'),structuredPublishSelect=$('structuredReceivePublishTarget');
  const savedStructuredStock=localStorage.getItem('fc_structured_receive_stock_class');
  if(savedStructuredStock==='MAIN'||savedStructuredStock==='FBS')structuredStockSelect.value=savedStructuredStock;
  const savedStructuredPublish=localStorage.getItem('fc_structured_receive_publish_target');
  if(savedStructuredPublish==='LOCAL'||savedStructuredPublish==='LOCAL_WB')structuredPublishSelect.value=savedStructuredPublish;
  const syncStructuredDestination=()=>{const isFbs=structuredStockSelect.value==='FBS';structuredPublishSelect.disabled=!isFbs;structuredPublishSelect.closest('label').style.opacity=isFbs?'1':'.55';};
  structuredStockSelect.addEventListener('change',()=>{localStorage.setItem('fc_structured_receive_stock_class',structuredStockSelect.value);syncStructuredDestination();});
  structuredPublishSelect.addEventListener('change',()=>localStorage.setItem('fc_structured_receive_publish_target',structuredPublishSelect.value));
  syncStructuredDestination();
  panel.addEventListener('click',handleStructuredReceiptClick);
  panel.addEventListener('input',e=>{if(e.target?.id==='structuredBoxSearch')renderStructuredBoxSearch(e.target.value);});
}
function structuredReceiptSummaryHtml(receipt){
  const items=[['Секций',receipt.section_count||0,''],['Палет',receipt.pallet_count||0,''],['Коробов',receipt.box_count||0,''],['Товарных строк',receipt.product_row_count||0,''],['Товаров, шт.',fmtQty(receipt.quantity_total||0),''],['Пустых коробов',receipt.empty_box_count||0,''],['Нулевых строк',receipt.zero_product_row_count||0,(receipt.zero_product_row_count||0)?'warn':''],['Ошибок',receipt.error_count||0,(receipt.error_count||0)?'error':'']];
  return `<div class="structured-summary">${items.map(([label,value,cls])=>`<div class="structured-kpi ${cls}"><b>${esc(value)}</b><small>${esc(label)}</small></div>`).join('')}</div>`;
}
function renderStructuredReceiptHistory(){
  const root=$('structuredReceiptHistory');if(!root)return;
  const rows=structuredArray(structuredReceipts);if(!rows.length){root.innerHTML='<div class="empty-state">Структурных приёмок пока нет.</div>';return;}
  root.innerHTML=rows.map(item=>{const status=String(item.status||'').toUpperCase();const deleteBtn=status==='ARCHIVED'?'':`<button type="button" class="danger mini" data-structured-delete="${esc(item.id)}">${status==='COMMITTED'||status==='CANCELLED'?'Удалить приёмку':'Удалить'}</button>`;return `<div class="structured-history-row"><div><strong>${esc(item.name||item.source_filename||'Приёмка')}</strong><small>${item.created_at?new Date(item.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'—'} · ${esc(item.reference||'без комментария')}</small><small class="${structuredStatusClass(item.status)}">${esc(structuredStatusLabel(item.status))}${item.error_count?` · ошибок ${item.error_count}`:''}</small></div><div><b>${item.section_count||0}</b><small>секц.</small></div><div><b>${item.pallet_count||0}</b><small>палет</small></div><div><b>${item.box_count||0}</b><small>коробов</small></div><div><b>${fmtQty(item.quantity_total||0)}</b><small>шт.</small></div><div><b>${item.empty_box_count||0}</b><small>пустых</small></div><div class="structured-actions"><button type="button" class="secondary mini" data-structured-open="${esc(item.id)}">Открыть</button><button type="button" class="secondary mini" data-structured-unloaded="${esc(item.id)}">Excel: неподгруженные</button>${deleteBtn}</div></div>`;}).join('');
}
async function loadStructuredReceipts(showToast=false){
  if(!state.clientId||!state.warehouseId){structuredReceipts=[];renderStructuredReceiptHistory();return;}
  const r=await api(`/receiving/structured-receipts?client_id=${encodeURIComponent(state.clientId)}&warehouse_id=${encodeURIComponent(state.warehouseId)}&limit=30`);
  structuredReceipts=structuredArray(r?.items);renderStructuredReceiptHistory();if(showToast)toast(`Структурных приёмок: ${structuredReceipts.length}`);
}
async function uploadStructuredReceipt(){
  try{await ensureContext();const file=$('structuredReceiptFile')?.files?.[0];if(!file)throw new Error('Выберите Excel-файл');const btn=$('structuredUploadBtn');btn.disabled=true;btn.textContent='Проверяем…';const fd=new FormData();fd.append('client_id',state.clientId);fd.append('warehouse_id',state.warehouseId);fd.append('name',$('structuredReceiptName').value.trim());fd.append('reference',$('structuredReceiptReference').value.trim());fd.append('performed_by',$('structuredReceiptBy').value.trim());fd.append('file',file);const detail=await api('/receiving/structured-receipts',{method:'POST',body:fd,timeoutMs:120000});structuredReceiptDetail=detail;renderStructuredReceiptDetail(detail);await loadStructuredReceipts();const receipt=detail.receipt||{};toast(receipt.error_count?`Файл загружен. Найдено ошибок: ${receipt.error_count}`:`Файл проверен: ${receipt.box_count||0} коробов · ${fmtQty(receipt.quantity_total||0)} шт.`,Boolean(receipt.error_count));}
  catch(e){toast(e.message,true)}finally{const btn=$('structuredUploadBtn');if(btn){btn.disabled=false;btn.textContent='1. Загрузить и проверить';}}
}
function renderStructuredReceiptDetail(detail){
  structuredReceiptDetail=detail;const root=$('structuredReceiptCurrent');if(!root)return;const r=detail?.receipt||{};if(!r.id){root.innerHTML='<div class="empty-state">Черновик не выбран.</div>';return;}const status=String(r.status||'').toUpperCase(),committed=status==='COMMITTED',cancelled=status==='CANCELLED',hasErrors=Number(r.error_count||0)>0;const commitLabel=hasErrors?'Оприходовать готовые позиции':'Оприходовать / Добавить на остатки';const actions=`<div class="structured-actions"><button type="button" class="secondary" data-structured-export="${esc(r.id)}">Скачать проверку Excel</button><button type="button" class="secondary" data-structured-unloaded="${esc(r.id)}">Excel: неподгруженные</button>${!committed&&!cancelled?`<button type="button" class="primary" data-structured-commit="${esc(r.id)}">${esc(commitLabel)}</button>`:''}${!committed&&!cancelled?`<button type="button" class="danger" data-structured-delete="${esc(r.id)}">Удалить черновик</button>`:''}${committed&&r.committed_operation_id?`<button type="button" class="primary" data-r76-open="${esc(r.committed_operation_id)}">Вся приёмка или часть → FBS</button>`:''}${committed||cancelled?`<button type="button" class="danger" data-structured-delete="${esc(r.id)}">Удалить приёмку из списка</button>`:''}</div>`;const sections=structuredArray(detail?.sections).map(section=>`<section class="structured-section-card"><header><div><span class="kicker">СЕКЦИЯ</span><h4>${esc(section.section)}</h4></div><div><b>${section.pallet_count||0} палет · ${section.box_count||0} коробов · ${fmtQty(section.quantity||0)} шт.</b></div></header><div class="table-wrap"><table class="structured-pallet-table"><thead><tr><th>Палета</th><th>Ряды</th><th>Адрес FulfillCore</th><th>Лимит</th><th>Коробов</th><th>Товаров</th><th></th></tr></thead><tbody>${structuredArray(section.pallets).map(p=>`<tr><td><b>${esc(p.pallet)}</b></td><td><b>${structuredArray(p.rows_on_pallet).length?structuredArray(p.rows_on_pallet).map(x=>esc(x)).join(' / '):'—'}</b></td><td>${esc(p.location_code||'')}</td><td>${p.limit??'—'}</td><td>${p.box_count||0}</td><td>${fmtQty(p.quantity||0)} шт.</td><td><button type="button" class="mini secondary" data-structured-pallet-section="${esc(section.section)}" data-structured-pallet-code="${esc(p.pallet)}">Короба</button></td></tr>`).join('')}</tbody></table></div></section>`).join('');const errors=structuredArray(detail?.errors).length?`<div class="structured-error-list"><h4>Что не загрузится</h4>${structuredArray(detail?.errors).slice(0,50).map(x=>`<div>Строка ${x.row}${x.box?` · ${esc(x.box)}`:''}: ${esc(x.error)}</div>`).join('')}${structuredArray(detail?.errors).length>50?`<small>Показаны первые 50. Полный список есть в кнопке «Скачать проверку Excel».</small>`:''}</div>`:'';const warnings=structuredArray(detail?.warnings).length?`<div class="structured-warning-list"><h4>Предупреждения</h4>${structuredArray(detail?.warnings).slice(0,20).map(x=>`<div>Строка ${x.row}${x.box?` · ${esc(x.box)}`:''}: ${esc(x.warning)}</div>`).join('')}</div>`:'';const message=cancelled?'Приёмка отменена: товар снят с локальных остатков, история сохранена.':committed?(hasErrors?`Корректные позиции оприходованы. Строк с ошибками осталось: ${Number(r.error_count||0)} — они не попали в остатки.`:'Приёмка проведена и сохранена в истории. Кнопка «Вся приёмка или часть → FBS» открывает выбор товаров и количества.'):hasErrors?`Есть ${Number(r.error_count||0)} ошибочных строк. Они будут пропущены, а все корректные позиции можно оприходовать кнопкой ниже.`:'Проверка пройдена. Склад ещё не изменён — можно посмотреть структуру и только потом добавить на остатки.';root.innerHTML=`<div class="structured-receipt-banner"><div><span class="kicker">${esc(structuredStatusLabel(r.status))}</span><h3>${esc(r.name||r.source_filename||'Структурная приёмка')}</h3><p>${esc(r.source_filename||'')} · ${esc(r.reference||'без комментария')}${r.performed_by?` · принял ${esc(r.performed_by)}`:''}</p><p>${message}</p></div>${actions}</div>${structuredReceiptSummaryHtml(r)}<div class="structured-search-row"><label>Найти короб или товар в этом файле<input id="structuredBoxSearch" placeholder="Короб, артикул или ШК товара"/></label><button type="button" class="secondary" data-structured-clear-search>Очистить</button></div><div id="structuredSearchResults" class="structured-search-results"></div><div id="structuredPalletBoxViewer"></div>${errors}${warnings}<div class="structured-sections">${sections}</div>${!committed&&!cancelled?`<div class="structured-bottom-commit"><div><b>${hasErrors?'Оприходовать всё исправное':'Приёмка готова к оприходованию'}</b><small>${hasErrors?`Строк с ошибками: ${Number(r.error_count||0)} — они будут пропущены.`:'После нажатия товар появится в разделе «Остатки».'}</small></div><button type="button" class="primary big" data-structured-commit="${esc(r.id)}">${esc(commitLabel)}</button></div>`:''}<div id="structuredBoxModal" class="structured-box-modal" hidden></div>`;
}

function findStructuredPallet(sectionCode,palletCode){const sections=structuredArray(structuredReceiptDetail?.sections),section=sections.find(x=>String(x.section)===String(sectionCode));return structuredArray(section?.pallets).find(x=>String(x.pallet)===String(palletCode))||null;}
function findStructuredBox(sectionCode,palletCode,boxCode){const p=findStructuredPallet(sectionCode,palletCode);return structuredArray(p?.boxes).find(x=>String(x.box)===String(boxCode))||null;}
function renderStructuredPalletBoxes(sectionCode,palletCode){const root=$('structuredPalletBoxViewer'),p=findStructuredPallet(sectionCode,palletCode);if(!root||!p)return;root.className='structured-box-viewer';root.innerHTML=`<h4>Секция ${esc(sectionCode)} · палета ${esc(palletCode)} · короба ${p.box_count||0}</h4><div class="table-wrap"><table><thead><tr><th>Ряд</th><th>Короб</th><th>Партия</th><th>Источник</th><th>Товарных строк</th><th>Количество</th><th>Статус</th><th></th></tr></thead><tbody>${structuredArray(p.boxes).map(b=>`<tr><td>${esc(b.row_on_pallet||"—")}</td><td><b>${esc(b.box)}</b></td><td>${esc(b.batch||'—')}</td><td>${esc(b.source||'—')}</td><td>${b.product_rows||0}</td><td>${fmtQty(b.quantity||0)}</td><td><span class="structured-box-status ${b.has_error?'bad':b.has_warning?'warn':'good'}">${b.has_error?'Ошибка':b.empty?'Пустой короб':b.has_warning?'С предупреждением':'Готов'}</span></td><td><button type="button" class="mini secondary" data-structured-box-section="${esc(sectionCode)}" data-structured-box-pallet="${esc(palletCode)}" data-structured-box-code="${esc(b.box)}">Состав</button></td></tr>`).join('')}</tbody></table></div><div id="structuredBoxContents"></div>`;root.scrollIntoView({behavior:'smooth',block:'nearest'});}
function renderStructuredBoxContents(sectionCode,palletCode,boxCode){const b=findStructuredBox(sectionCode,palletCode,boxCode);if(!b){toast('Состав короба не найден',true);return;}let modal=$('structuredBoxModal');if(!modal){modal=document.createElement('div');modal.id='structuredBoxModal';modal.className='structured-box-modal';document.body.appendChild(modal);}const rows=structuredArray(b.items).map(item=>`<tr class="${item.error?'structured-item-error':item.warning?'structured-item-warning':''}"><td>${item.row||'—'}</td><td><b>${esc(item.article||'—')}</b></td><td>${esc(item.size||'—')}</td><td><b>${esc(item.barcode||'—')}</b></td><td>${fmtQty(item.quantity||0)}</td><td>${item.error?`<span class="bad">${esc(item.error)}</span>`:item.warning?`<span class="warn">${esc(item.warning)}</span>`:'Готов'}</td></tr>`).join('');modal.hidden=false;modal.innerHTML=`<div class="structured-box-modal-backdrop" data-structured-close-box></div><div class="structured-box-modal-card"><div class="structured-box-contents-head"><div><span class="kicker">СОСТАВ КОРОБА</span><h4>${esc(boxCode)}</h4><p>Секция ${esc(sectionCode)} · ряд ${esc(b.row_on_pallet||"—")} · палета ${esc(palletCode)} · ${fmtQty(b.quantity||0)} шт.</p></div><button type="button" class="secondary" data-structured-close-box>Закрыть</button></div>${rows?`<div class="table-wrap"><table><thead><tr><th>Строка</th><th>Артикул</th><th>Размер</th><th>ШК товара</th><th>Кол-во</th><th>Статус</th></tr></thead><tbody>${rows}</tbody></table></div>`:'<div class="empty-state">Короб пустой — товарных строк нет.</div>'}</div>`;}

function renderStructuredBoxSearch(value){const root=$('structuredSearchResults');if(!root)return;const q=String(value||'').trim().toLocaleLowerCase('ru-RU');if(!q){root.innerHTML='';return;}const found=[];for(const section of structuredArray(structuredReceiptDetail?.sections))for(const p of structuredArray(section.pallets))for(const b of structuredArray(p.boxes)){const itemText=structuredArray(b.items).map(item=>[item.article,item.size,item.barcode].join(' ')).join(' ');if([b.box,b.batch,b.source,itemText].join(' ').toLocaleLowerCase('ru-RU').includes(q))found.push({section:section.section,pallet:p.pallet,...b});}root.innerHTML=found.length?found.slice(0,100).map(b=>`<button type="button" class="structured-search-result" data-structured-box-section="${esc(b.section)}" data-structured-box-pallet="${esc(b.pallet)}" data-structured-box-code="${esc(b.box)}"><span><b>${esc(b.box)}</b><small>Секция ${esc(b.section)} · палета ${esc(b.pallet)} · ${b.product_rows||0} товарных строк</small></span><span>${fmtQty(b.quantity||0)} шт.</span></button>`).join(''):'<div class="empty-state">Короб или товар по запросу не найден.</div>';}
let structuredFbsCandidates=[];
async function loadStructuredFbsCandidates(receiptId){const root=$('structuredFbsPanel');if(!root)return;try{const data=await api(`/receiving/structured-receipts/${encodeURIComponent(receiptId)}/fbs-candidates`);structuredFbsCandidates=structuredArray(data?.items);renderStructuredFbsCandidates(data);}catch(e){root.innerHTML=`<div class="structured-error-list">Не удалось загрузить остатки WB FBS: ${esc(e.message)}</div>`;}}
function renderStructuredFbsCandidates(data={}){const root=$('structuredFbsPanel');if(!root)return;const items=structuredArray(structuredFbsCandidates);if(!items.length){root.innerHTML='<div class="empty-state">В этой приёмке нет товарных строк для WB FBS.</div>';return;}const rows=items.map((x,i)=>{const disabled=!x.wb_found,checked=x.wb_found&&x.needs_update?'checked':'',status=!x.wb_found?'Нет карточки WB':Number(x.sellable_quantity||0)===Number(x.published_amount||0)?'Уже совпадает':'Нужно обновить';return `<tr><td><input type="checkbox" data-structured-fbs-index="${i}" ${checked} ${disabled?'disabled':''}></td><td><b>${esc(x.article||'—')}</b><small>${esc(x.name||'')}</small></td><td>${esc(x.size||'—')}</td><td><b>${esc(x.barcode)}</b></td><td>${fmtQty(x.receipt_quantity||0)}</td><td>${fmtQty(x.warehouse_quantity||0)}</td><td><b>${fmtQty(x.sellable_quantity||0)}</b></td><td><b>${fmtQty(x.published_amount||0)}</b></td><td class="${disabled?'bad':x.needs_update?'warn':'good'}">${esc(status)}</td></tr>`;}).join('');root.innerHTML=`<div class="structured-fbs-head"><div><span class="kicker">WB FBS</span><h3>Массово разместить товары этой приёмки на FBS</h3><p>WB получает только свободный остаток: физический минус резерв и хранение.</p></div><div class="structured-actions"><button type="button" class="secondary" data-structured-unloaded="${esc(structuredReceiptDetail?.receipt?.id||'')}">Excel: неподгруженные</button><button type="button" class="secondary" data-structured-fbs-toggle="${esc(structuredReceiptDetail?.receipt?.id||'')}">Скрыть список</button><button type="button" class="secondary" data-structured-fbs-select-all>Выбрать все доступные</button><button type="button" class="primary" data-structured-fbs-publish>Отправить выбранные на WB FBS</button></div></div><div class="table-wrap"><table><thead><tr><th></th><th>Товар</th><th>Размер</th><th>ШК</th><th>В этой приёмке</th><th>Физически</th><th>Свободно FBS</th><th>WB FBS</th><th>Статус</th></tr></thead><tbody>${rows}</tbody></table></div>`;}
async function publishStructuredSelectedFbs(){const selected=[...document.querySelectorAll('[data-structured-fbs-index]:checked')].map(el=>structuredFbsCandidates[Number(el.dataset.structuredFbsIndex)]).filter(Boolean);if(!selected.length)throw new Error('Отметьте хотя бы один товар');if(!confirm(`Отправить на WB FBS выбранные товары: ${selected.length} поз.?\n\nНа WB будет установлен только свободный остаток FulfillCore: физический минус резерв.`))return;const btn=document.querySelector('[data-structured-fbs-publish]');if(btn){btn.disabled=true;btn.textContent='Отправляем…';}try{const result=await api('/receiving/sync-wb',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null,lines:selected.map(x=>({barcode:x.barcode,quantity:Number(x.sellable_quantity||0),mode:'SET'}))}),timeoutMs:180000});const failed=Number(result.failed||0);toast(failed?`WB обновлён частично: ошибок ${failed}`:`WB FBS обновлён: ${result.synced||selected.length} поз.`,Boolean(failed));await Promise.allSettled([loadStructuredFbsCandidates(structuredReceiptDetail.receipt.id),loadInventoryCatalog(true)]);}finally{if(btn){btn.disabled=false;btn.textContent='Отправить выбранные на WB FBS';}}}
async function cancelStructuredReceipt(id){const syncWb=confirm('Удалить эту принятую приёмку с локального склада?\n\nОК — продолжить. После этого будет отдельный вопрос про WB FBS.');if(!syncWb)return;const alsoWb=confirm('Также пересчитать WB FBS по товарам этой приёмки после отмены?\n\nОК — обновить WB. Отмена — изменить только локальный склад.');const result=await api(`/receiving/structured-receipts/${encodeURIComponent(id)}/cancel`,{method:'POST',body:JSON.stringify({sync_wb:alsoWb,wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null}),timeoutMs:180000});structuredReceiptDetail=result;renderStructuredReceiptDetail(result);structuredFbsCandidates=[];await Promise.allSettled([loadStructuredReceipts(),loadContextData(),loadInventoryCatalog(true)]);toast('Приёмка отменена. Товар снят с локальных остатков, история сохранена.');}
async function commitStructuredReceipt(id){
  const r=structuredReceiptDetail?.receipt;
  if(!r||String(r.id)!==String(id)){
    structuredReceiptDetail=await api(`/receiving/structured-receipts/${encodeURIComponent(id)}`);
    renderStructuredReceiptDetail(structuredReceiptDetail);
  }
  const current=structuredReceiptDetail.receipt;
  const errorCount=Number(current.error_count||0);
  const stockClass=String(current.stock_class||$('structuredReceiveStockClass')?.value||'FBS').toUpperCase()==='MAIN'?'MAIN':'FBS';
  const publishNow=stockClass==='FBS'&&String($('structuredReceivePublishTarget')?.value||'LOCAL_WB')==='LOCAL_WB';
  const destinationText=stockClass==='FBS'?'СРАЗУ В FBS':'на Основной склад';
  const wbText=stockClass==='FBS'?(publishNow?'После приёмки остатки будут сразу отправлены на WB FBS.':'WB сейчас не меняется; товар останется локально в FBS до отдельной публикации.'):'WB не изменится.';
  const partialText=errorCount?`

ВНИМАНИЕ: строк с ошибками: ${errorCount}. Они НЕ попадут в остатки, остальные строки будут оприходованы.`:'';
  if(!confirm(`Оприходовать готовые позиции ${destinationText}?

Секций: ${current.section_count}
Палет: ${current.pallet_count}
Коробов: ${current.box_count}
Товаров по файлу: ${fmtQty(current.quantity_total)} шт.${partialText}

Секции/палеты и короба будут созданы автоматически. ${wbText}`))return;
  const button=[...document.querySelectorAll('[data-structured-commit]')].find(node=>String(node.dataset.structuredCommit)===String(id));
  if(button){button.disabled=true;button.textContent=stockClass==='FBS'?'Добавляем сразу в FBS…':'Добавляем на Основной…';}
  try{
    const result=await api(`/receiving/structured-receipts/${encodeURIComponent(id)}/commit`,{
      method:'POST',
      body:JSON.stringify({wb_warehouse_id:Number(state.wbWarehouseFilter||0)||null,stock_class:stockClass,publish_to_fbs:stockClass==='FBS'}),
      timeoutMs:180000
    });
    let wbResult=null;
    if(stockClass==='FBS'&&result.pending_lines?.length){
      if(publishNow)wbResult=await publishFreshReceivingLines(result.pending_lines,'Структурная приёмка');
      else addPendingWbReceiving(result.pending_lines);
    }
    structuredReceiptDetail=result;
    renderStructuredReceiptDetail(result);
    window.FCReceiptHistory?.changed();
    await Promise.allSettled([loadStructuredReceipts(),loadContextData(),loadInventoryCatalog(true)]);
    const skipped=Number(result.skipped_error_rows||0);
    const finish=stockClass==='MAIN'?'Товар принят на Основной склад.':(publishNow?(wbResult?.failed?`Товар принят сразу в FBS. WB: ошибок ${wbResult.failed}, они оставлены в очереди.`:'Товар принят сразу в FBS и WB FBS обновлён.'):'Товар принят сразу в локальный FBS; WB можно обновить позже.');
    toast(`Оприходовано: ${fmtQty(result.quantity_received||0)} шт. · ${result.created_containers||0} новых коробов${skipped?` · пропущено ошибочных строк: ${skipped}`:''}. ${finish}`,Boolean(skipped)||Boolean(wbResult?.failed));
  }finally{
    if(button){button.disabled=false;button.textContent=Number(structuredReceiptDetail?.receipt?.error_count||0)>0?'Оприходовать готовые позиции':'Оприходовать / Добавить на остатки';}
  }
}
async function handleStructuredReceiptClick(e){const open=e.target.closest('[data-structured-open]'),commit=e.target.closest('[data-structured-commit]'),exp=e.target.closest('[data-structured-export]'),unloaded=e.target.closest('[data-structured-unloaded]'),del=e.target.closest('[data-structured-delete]'),cancel=e.target.closest('[data-structured-cancel]'),box=e.target.closest('[data-structured-box-code]'),pallet=e.target.closest('[data-structured-pallet-section]'),clear=e.target.closest('[data-structured-clear-search]'),closeBox=e.target.closest('[data-structured-close-box]');try{if(closeBox){const modal=$('structuredBoxModal');if(modal){modal.hidden=true;modal.innerHTML='';}return;}if(open){structuredReceiptDetail=await api(`/receiving/structured-receipts/${encodeURIComponent(open.dataset.structuredOpen)}`);renderStructuredReceiptDetail(structuredReceiptDetail);$('structuredReceiptCurrent')?.scrollIntoView({behavior:'smooth',block:'start'});return;}if(commit){await commitStructuredReceipt(commit.dataset.structuredCommit);return;}if(exp){const blob=await apiBlob(`/receiving/structured-receipts/${encodeURIComponent(exp.dataset.structuredExport)}/export.xlsx`,{timeoutMs:90000});saveBlob(blob,`Проверка_структурной_приемки_${exp.dataset.structuredExport}.xlsx`);return;}if(unloaded){const id=unloaded.dataset.structuredUnloaded;const blob=await apiBlob(`/receiving/structured-receipts/${encodeURIComponent(id)}/unloaded.xlsx`,{timeoutMs:180000});saveBlob(blob,`Неподгруженные_товары_${id}.xlsx`);toast('Excel с неподгруженными товарами скачан');return;}if(cancel){await cancelStructuredReceipt(cancel.dataset.structuredCancel);return;}if(del){const id=del.dataset.structuredDelete,item=structuredArray(structuredReceipts).find(x=>String(x?.id)===String(id)),status=String(item?.status||structuredReceiptDetail?.receipt?.status||'').toUpperCase(),postedLike=Boolean(item?.committed_operation_id||structuredReceiptDetail?.receipt?.committed_operation_id)||['COMMITTED','CANCELLED','COMPLETED','LOADED'].includes(status);const question=postedLike?'Удалить эту приёмку из списка?\n\nОстатки, резерв и история складских движений НЕ изменятся. Приёмка просто исчезнет из списка последних приёмок.':'Удалить этот черновик? Остатки склада не изменятся.';if(!confirm(question))return;const result=await api(`/receiving/structured-receipts/${encodeURIComponent(id)}`,{method:'DELETE'});structuredReceipts=structuredArray(structuredReceipts).filter(x=>String(x?.id)!==String(id));renderStructuredReceiptHistory();if(String(structuredReceiptDetail?.receipt?.id)===String(id)){structuredReceiptDetail=null;$('structuredReceiptCurrent').innerHTML=`<div class="empty-state">${result?.archived?'Приёмка удалена из списка. Остатки не изменены.':'Черновик удалён.'}</div>`;}loadStructuredReceipts().catch(()=>{});toast(result?.archived?'Приёмка удалена из списка. Остатки не изменены.':'Черновик удалён');return;}if(box){renderStructuredBoxContents(box.dataset.structuredBoxSection,box.dataset.structuredBoxPallet,box.dataset.structuredBoxCode);return;}if(pallet){renderStructuredPalletBoxes(pallet.dataset.structuredPalletSection,pallet.dataset.structuredPalletCode);return;}if(clear){const input=$('structuredBoxSearch');if(input){input.value='';renderStructuredBoxSearch('');input.focus();}return;}}catch(err){toast(err.message,true);}}

installStructuredReceivingPanel();
const _goBeforeStructuredReceiving=go;
go=function(page){_goBeforeStructuredReceiving(page);if(page==='bulkimport')loadStructuredReceipts().catch(e=>toast(e.message,true));};

// FC_TRANSPARENT_WAREHOUSE_AUDIT_09415G5A8_20260903
(function installTransparentWarehouseAudit(){
  const labels={RECEIVING:'Приёмка',PVZ_RECEIVING:'Приёмка ПВЗ',RECEIVING_IMPORT:'Приёмка Excel',MOVE:'Перемещение',MOVE_CONTAINER:'Перемещение короба',SHIPPING:'Списание / отгрузка',EXTERNAL_PICK_WRITE_OFF:'Списано по сборочному листу',ADJUSTMENT:'Ручная корректировка',RECONCILIATION:'Техническая сверка остатка',RESERVE_CREATED:'Заказ: резерв',RESERVE_BOUND:'Сборочный лист: резерв закреплён',RESERVE_CONSUMED:'Упаковка: резерв списан',RESERVE_RELEASED:'Отмена: резерв освобождён',RETURN_TO_RECEIVING:'Возврат в ПРИЕМКА'};
  function eventLabel(x){return labels[x]||x||'Событие';}
  function auditParams(){const q=new URLSearchParams({client_id:state.clientId||'',warehouse_id:state.warehouseId||'',limit:'1000'});const bc=$('auditBarcode')?.value.trim(),loc=$('auditLocation')?.value,box=$('auditContainer')?.value,order=$('auditOrder')?.value.trim(),type=$('operationFilter')?.value;if(bc)q.set('barcode',bc);if(loc)q.set('location_id',loc);if(box)q.set('container_id',box);if(order)q.set('order_id',order);if(type&&!['RECEIVING_ALL'].includes(type))q.set('operation_type',type);return q;}
  function fillAuditFilters(){const loc=$('auditLocation'),box=$('auditContainer');if(loc){const cur=loc.value;loc.innerHTML='<option value="">Все ячейки</option>'+(state.locations||[]).map(x=>`<option value="${esc(x.id)}">${esc(x.code||x.name||x.id)}</option>`).join('');loc.value=cur;}if(box){const cur=box.value;box.innerHTML='<option value="">Все короба</option>'+(state.containers||[]).filter(x=>String(x.status||'ACTIVE').toUpperCase()==='ACTIVE').map(x=>`<option value="${esc(x.id)}">${esc(x.name||x.barcode||x.id)}</option>`).join('');box.value=cur;}}
  async function loadAuditLedger(){if(!state.clientId||!state.warehouseId)return;fillAuditFilters();const q=auditParams();q.set('mode','MOVEMENTS');const safe=await api(`/r145f24/history?${q.toString()}`,{timeoutMs:65000});let rows=Array.isArray(safe)?safe:(safe?.rows||[]);const type=$('operationFilter')?.value;if(type&&type!=='RECEIVING_ALL')rows=rows.filter(r=>String(r.event_type||'').toUpperCase()===String(type).toUpperCase());const bc=$('auditBarcode')?.value.trim().toLowerCase();if(bc)rows=rows.filter(r=>String(r.barcode||'').toLowerCase().includes(bc));const order=$('auditOrder')?.value.trim().toLowerCase();if(order)rows=rows.filter(r=>String(r.reference||r.order_id||'').toLowerCase().includes(order));const loc=$('auditLocation')?.selectedOptions?.[0]?.textContent?.trim();if($('auditLocation')?.value&&loc)rows=rows.filter(r=>[r.source_location,r.destination_location].some(x=>String(x||'')===loc));const box=$('auditContainer')?.selectedOptions?.[0]?.textContent?.trim();if($('auditContainer')?.value&&box)rows=rows.filter(r=>[r.source_container,r.destination_container].some(x=>String(x||'')===box));const summary=[];const body=$('auditLedgerBody');if(body){body.innerHTML=(rows||[]).map(r=>{const src=[r.source_location,r.source_container].filter(Boolean).join(' · ')||'—',dst=[r.destination_location,r.destination_container].filter(Boolean).join(' · ')||'—',pd=Number(r.physical_delta||0),rd=Number(r.reserve_delta||0);return `<tr><td>${esc(new Date(r.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}))}</td><td><b>${esc(r.event_label||eventLabel(r.event_type))}</b><small class="subline">${esc(r.status||'')}</small></td><td>${esc([r.article,r.size,r.barcode].filter(Boolean).join(' · '))}<small class="subline">${esc(r.name||'')}</small></td><td class="qty ${pd<0?'bad':pd>0?'good':''}">${pd?`${pd>0?'+':''}${fmtQty(pd)}`:'—'}</td><td class="qty ${rd<0?'bad':rd>0?'good':''}">${rd?`${rd>0?'+':''}${fmtQty(rd)}`:'—'}</td><td>${esc(src)}</td><td>${esc(dst)}</td><td>${esc(r.reference||r.order_id||'—')}</td><td>${esc(r.actor||'—')}</td></tr>`}).join('')||'<tr><td colspan="9" class="empty-state">Движений не найдено</td></tr>';}
    const s=$('auditSummary');if(s){s.innerHTML=`<b>Загружено движений: ${fmtQty((rows||[]).length)}</b><span>Движения товара. Технические записи — в отдельном журнале. Время: МСК. ${safe.has_more?'Есть ещё записи; откройте движения или выгрузите Excel.':''}</span>`;}}
  async function exportAudit(){
    const q=auditParams();q.set('mode','MOVEMENTS');
    try{return await window.FCR24History.exportQuery(q,$('auditExportBtn'));}
    catch(e){toast(e.message);}
  }

  function bind(){
    const page=$('page-operations');
    if(!page||page.dataset.auditBound)return;
    page.dataset.auditBound='1';
    const loadBtn=$('auditLoadBtn'), exportBtn=$('auditExportBtn');
    if(loadBtn)loadBtn.addEventListener('click',()=>loadAuditLedger().catch(e=>toast(e.message,true)));
    if(exportBtn)exportBtn.addEventListener('click',()=>exportAudit().catch(e=>toast(e.message,true)));
    for(const id of ['auditBarcode','auditOrder']){const el=$(id);if(el)el.addEventListener('keydown',e=>{if(e.key==='Enter')loadAuditLedger().catch(x=>toast(x.message,true));});}
    for(const id of ['auditLocation','auditContainer']){const el=$(id);if(el)el.addEventListener('change',()=>loadAuditLedger().catch(e=>toast(e.message,true)));}
    const nav=document.querySelector('[data-page="operations"]');
    if(nav)nav.addEventListener('click',()=>setTimeout(()=>loadAuditLedger().catch(e=>toast(e.message,true)),0));
    fillAuditFilters();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bind);else bind();
})();

/* FC_K9R62_WAREHOUSE_SPLIT_RECEIPTS_TSD_ADMIN */
(function installWarehouseOperationsR62(){
  if(window.__fcR62WarehouseInstalled)return;window.__fcR62WarehouseInstalled=true;
  titles.warehousework='Складские задания';
  const style=document.createElement('style');style.id='fcR62WarehouseStyles';style.textContent=`
    .inventory-where{min-width:220px;vertical-align:top}.inventory-place-popup-summary{display:grid;gap:4px;align-items:start}.inventory-place-popup-summary small{color:#667085}.inventory-place-popup-summary button{justify-self:start}.inventory-life-row td{background:#f8fafc}.inventory-life-row td:nth-child(2){font-weight:750}.inventory-detail-grid>div:first-child{min-width:0}.r62-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:12px 0}.r62-stat{padding:14px;border:1px solid #e4e7ec;border-radius:14px;background:#fff}.r62-stat small{display:block;color:#667085}.r62-stat b{display:block;font-size:24px;margin-top:4px}.r62-section{margin-top:16px}.r62-actions{display:flex;gap:8px;flex-wrap:wrap;align-items:end}.r62-actions label{display:grid;gap:5px;min-width:190px}.r62-actions input{min-width:180px}.r62-receipt-lines td,.r62-pick-lines td{vertical-align:top}.r62-mini-input{width:82px;min-width:72px}.r62-audit{max-height:280px;overflow:auto}.r62-status{font-weight:750}.r62-status.READY_FOR_ADMIN{color:#b54708}.r62-status.CLOSED{color:#067647}.r62-status.CANCELLED{color:#b42318}.r62-status.IN_PROGRESS,.r62-status.ASSIGNED{color:#175cd3}.r62-manual-history{margin-top:18px}.r62-manual-history .table-wrap{max-height:360px;overflow:auto}
    @media(max-width:900px){.inventory-where{min-width:180px}.r62-grid{grid-template-columns:1fr}.r62-actions{display:grid}.r62-actions label{min-width:0}}
  `;document.head.appendChild(style);

  function actor(){return ($('currentUser')?.textContent||'admin').trim()||'admin';}
  function ctxQuery(extra={}){return new URLSearchParams({client_id:state.clientId||'',warehouse_id:state.warehouseId||'',...extra});}
  function dateText(v){if(!v)return '—';try{return new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'});}catch{return String(v)}}
  function receiptTypeText(v){return String(v).toUpperCase()==='MANUAL'?'Ручная':'Excel';}
  function zoneText(v){return v==='MAIN'?'Основной склад':v==='FBS'?'FBS':'Старая приёмка';}
  function taskStatus(v){return ({DRAFT:'Новое',ASSIGNED:'Назначено',IN_PROGRESS:'В работе',READY_FOR_ADMIN:'Ожидает администратора',SPLIT:'Распределено',CLOSED:'Закрыто',CANCELLED:'Отменено'}[String(v||'').toUpperCase()]||v||'—');}

  function installNavAndPage(){
    const menu=$('warehouseSubmenu')||$('mainNav');if($('page-warehousework'))return;
    let nav=menu?.querySelector('[data-page="warehousework"]')||null;
    if(!nav){
      const proto=menu?.querySelector('[data-page]');
      if(proto){nav=proto.cloneNode(false);nav.removeAttribute('id');nav.classList.remove('active');nav.dataset.page='warehousework';nav.textContent='Сборка';}
      else{nav=document.createElement('button');nav.type='button';nav.dataset.page='warehousework';nav.textContent='Сборка';}
      menu?.appendChild(nav);
      nav.addEventListener('click',()=>go('warehousework'));
    }else{nav.textContent='Сборка';}
    const host=document.querySelector('.page')?.parentElement;if(!host)return;
    const page=document.createElement('section');page.id='page-warehousework';page.className='page';page.innerHTML=`
      <div class="panel"><div class="panel-head"><div><span class="kicker">ДВА КОНТУРА — ОДИН ФИЗИЧЕСКИЙ ОСТАТОК</span><h2>Основной склад / FBS</h2><p>Перевод между контурами меняет классификацию, а не создаёт копию товара.</p></div><button type="button" class="secondary" data-r62-refresh>Обновить</button></div><div id="r62WarehouseSummary" class="r62-grid"></div></div>
      <div class="panel r62-section"><div class="panel-head"><div><span class="kicker">ИСТОРИЯ ПРИЁМОК</span><h2>Каждая приёмка отдельной партией</h2><p>Ручная приёмка, Excel и ТСД. Откройте состав, чтобы перевести в FBS всю доступную приёмку или выбранное количество.</p></div></div><div id="r62Receipts" class="table-wrap"></div><div id="r62ReceiptDetail" class="panel hidden"></div></div>
      <div class="panel r62-section"><div class="panel-head"><div><span class="kicker">СБОРКА ИЗ ФАКТИЧЕСКОГО ТОВАРА</span><h2>Складские задания → TSD → администратор</h2><p>Загрузка только резервирует Основной склад. Физическое списание выполняется при финальном закрытии администратором.</p></div></div>
        <div class="r62-actions"><label>Номер задания<input id="r62PickNumber" placeholder="Например: 2790"></label><label>Название<input id="r62PickName" placeholder="Например: Поставка 2790"></label><label class="btn">Excel<input id="r62PickFile" type="file" accept=".xlsx" hidden></label><button type="button" class="secondary" data-r62-pick-template>Скачать шаблон</button><button type="button" class="primary" data-r62-pick-create>Создать задание</button></div>
        <div id="r62PickImportMsg" class="status" style="margin-top:10px"></div><div id="r62Picks" class="table-wrap" style="margin-top:12px"></div><div id="r62PickDetail" class="panel hidden"></div>
      </div>`;
    host.appendChild(page);
    page.addEventListener('click',handleWarehouseClickR62);
  }

  async function loadWarehouseSummaryR62(){if(!state.clientId||!state.warehouseId)return;const d=await api(`/warehouse/summary?${ctxQuery()}`);$('r62WarehouseSummary').innerHTML=`<div class="r62-stat"><small>Физически всего</small><b>${fmtQty(d.physical)} шт.</b></div><div class="r62-stat"><small>Основной склад</small><b>${fmtQty(d.main)} шт.</b></div><div class="r62-stat"><small>FBS физически</small><b>${fmtQty(d.fbs)} шт.</b></div>`;}
  async function loadReceiptsR62(kind=''){if(!state.clientId||!state.warehouseId)return;const q=ctxQuery({limit:'150'});if(kind)q.set('kind',kind);const d=await api(`/warehouse/receipts/recent-grouped?${q}`);return d.rows||[];}
  function receiptTableHtml(rows,{manualOnly=false}={}){return `<table><thead><tr><th>Дата</th><th>Тип</th><th>Название / №</th><th>Контур</th><th>Позиций</th><th>Принято</th><th>Сотрудник</th><th></th></tr></thead><tbody>${rows.map(r=>{const ids=receiptGroupIds(r),grouped=ids.length>1;return `<tr><td>${esc(dateText(r.created_at))}</td><td>${esc(receiptTypeText(r.type))}</td><td><b>${esc(r.reference||'Без названия')}</b><small class="subline">${grouped?`объединено ${ids.length} частей`:esc(String(r.id).slice(0,8))}</small></td><td>${esc(zoneText(r.zone))}</td><td>${fmtQty(r.lines)}</td><td><b>${fmtQty(r.units)} шт.</b></td><td>${esc(r.performed_by||'—')}</td><td>${grouped?`<button type="button" class="mini secondary" data-r62-receipt-group-open="${esc(r.receipt_group_key||'')}"${manualOnly?' data-r62-from-manual="1"':''}>Состав</button><button type="button" class="mini secondary" data-r62-receipt-group-excel="${esc(r.receipt_group_key||'')}">Excel</button>`:`<button type="button" class="mini secondary" data-r62-receipt-open="${esc(r.id)}"${manualOnly?' data-r62-from-manual="1"':''}>Состав</button>`}</td></tr>`}).join('')||'<tr><td colspan="8" class="empty-state">Приёмок ещё нет</td></tr>'}</tbody></table>`;}
  async function openReceiptGroupR62(groupKey){const q=receiptGroupQuery(groupKey,{isKey:true}),d=await api(`/warehouse/receipts/group-detail?${q.toString()}`);const root=$('r62ReceiptDetail');root.classList.remove('hidden');root.innerHTML=`<div class="panel-head"><div><span class="kicker">ОБЪЕДИНЁННАЯ ПРИЁМКА</span><h3>${esc(d.title||'Приёмка')}</h3><p>${Number(d.operation_count||0)} частей · ${fmtQty(d.units||0)} шт. · ${Number(d.positions||0)} товаров · ${esc(d.performed_by||'')}</p></div><div class="button-row"><button type="button" class="secondary" data-r62-receipt-group-excel="${esc(r.receipt_group_key||'')}">Excel состава</button><button type="button" class="secondary" data-r62-receipt-close>Закрыть карточку</button></div></div><div class="table-wrap"><table><thead><tr><th>Дата</th><th>Товар</th><th>ШК</th><th>Количество</th><th>Ячейка</th><th>Короб</th><th>Сотрудник</th></tr></thead><tbody>${(d.lines||[]).map(x=>`<tr><td>${esc(dateText(x.created_at))}</td><td><b>${esc(x.article||'—')} · ${esc(x.size||'—')}</b><small class="subline">${esc(x.name||'')}</small></td><td>${esc(x.barcode||'—')}</td><td><b>${fmtQty(x.quantity||0)}</b></td><td>${esc(x.location||'—')}</td><td>${esc(x.container||'—')}</td><td>${esc(x.performed_by||'—')}</td></tr>`).join('')||'<tr><td colspan="7" class="empty-state">Нет строк состава</td></tr>'}</tbody></table></div>`;root.scrollIntoView({behavior:'smooth',block:'start'});}
  async function renderReceiptsR62(){if(window.FCReceiptHistory)return window.FCReceiptHistory.load();const rows=await loadReceiptsR62();$('r62Receipts').innerHTML=receiptTableHtml(rows);}
  async function openReceiptR62(id){if(window.FCReceiptHistory)return window.FCReceiptHistory.open(id);const d=await api(`/warehouse/receipts/${encodeURIComponent(id)}?${ctxQuery()}`);const root=$('r62ReceiptDetail');root.classList.remove('hidden');const r=d.receipt,lines=d.lines||[];root.innerHTML=`<div class="panel-head"><div><span class="kicker">СОСТАВ ПРИЁМКИ</span><h3>${esc(r.reference||receiptTypeText(r.type))}</h3><p>${esc(dateText(r.created_at))} · ${esc(zoneText(r.zone))} · ${esc(r.performed_by||'—')}</p></div><button type="button" class="secondary" data-r62-receipt-close>Закрыть</button></div><div class="button-row"><button type="button" class="primary" data-r62-receipt-to-fbs="${esc(r.id)}">Перевести выбранное в FBS</button><button type="button" class="secondary" data-r62-receipt-select-all>Выбрать доступное Основного склада</button></div><div class="table-wrap"><table class="r62-receipt-lines"><thead><tr><th>Ячейка / короб</th><th>Артикул</th><th>Размер</th><th>ШК</th><th>Принято</th><th>Основной сейчас</th><th>FBS сейчас</th><th>Выбрать</th></tr></thead><tbody>${lines.map(x=>`<tr><td><b>${esc(x.location||'—')}</b><small class="subline">${esc(x.container||'Без короба')}</small></td><td>${esc(x.article||'—')}</td><td>${esc(x.size||'—')}</td><td>${esc(x.barcode||'—')}</td><td>${fmtQty(x.quantity_received)}</td><td><b>${fmtQty(x.main_now)} шт.</b></td><td>${fmtQty(x.fbs_now)} шт.</td><td class="r80-select-cell"><input type="checkbox" data-r62-receipt-line="${esc(x.line_id)}" ${Number(x.main_now||0)>0?'checked':''} ${Number(x.main_now||0)>0?'':'disabled'}></td></tr>`).join('')}</tbody></table></div>`;root.scrollIntoView({behavior:'smooth',block:'start'});}

  async function renderPicksR62(){if(!state.clientId||!state.warehouseId)return;const d=await api(`/warehouse/picks?${ctxQuery({limit:'100'})}`);$('r62Picks').innerHTML=`<table><thead><tr><th>Задание</th><th>Источник</th><th>Статус</th><th>Сборщик / способ</th><th>План / выделено</th><th>Факт</th><th>Нет / пропущено</th><th></th></tr></thead><tbody>${(d.rows||[]).map(x=>`<tr><td><b>${esc(x.task_number)}</b><small class="subline">${esc(x.name||'')} · ${esc(dateText(x.created_at))}</small></td><td>${String(x.source||'ADMIN').toUpperCase()==='SELLER'?`<b>Селлер</b><small class="subline">${esc(x.seller_username||'')}</small>`:'Склад'}</td><td><span class="r62-status ${esc(x.status)}">${esc(taskStatus(x.status))}</span></td><td>${esc(x.picker||'—')}<small class="subline">${String(x.pick_mode||'CELL').toUpperCase()==='ARTICLE'?'по артикулу':'по ячейкам'}</small></td><td>${fmtQty(x.requested)} / ${fmtQty(x.allocated)}${Number(x.shortage||0)>0?`<small class="subline bad">Нехватка: ${fmtQty(x.shortage)}</small>`:''}</td><td>${fmtQty(Number(x.scan||0)+Number(x.manual||0))}<small class="subline">скан ${fmtQty(x.scan)} · вручную ${fmtQty(x.manual)}</small></td><td>${fmtQty(x.missing)} / ${fmtQty(x.skipped)}</td><td><button type="button" class="mini secondary" data-r62-pick-open="${esc(x.id)}">Открыть</button></td></tr>`).join('')||'<tr><td colspan="8" class="empty-state">Складских заданий ещё нет</td></tr>'}</tbody></table>`;}
  function auditActionR64(action){return ({CREATE:'Создано задание',CREATE_FROM_RECEIPT:'Создано из приёмки',ASSIGN:'Назначен сборщик',UNASSIGN:'Сборщик снят',ADMIN_EDIT:'Исправлен факт',CLOSE:'Задание закрыто',CANCEL:'Задание отменено',TSD_SCAN:'Товар отсканирован',TSD_MANUAL:'Подтверждено вручную',TSD_SKIP:'Позиция пропущена',TSD_NOT_IN_BOX:'Товар не найден в коробе',SUBMIT_ADMIN:'Передано администратору'})[action]||action;}
  function auditFactR64(value){if(!value)return '—';if(typeof value==='string'){try{value=JSON.parse(value);}catch{return value;}}const labels={scan:'Скан',manual:'Вручную',missing:'Нет в коробе',skipped:'Пропущено',posted:'Списано',units:'Единиц',rows:'Строк'};const out=Object.entries(labels).filter(([k])=>value[k]!==undefined).map(([k,label])=>label+': '+fmtQty(value[k]));if(value.status)out.push(taskStatus(value.status));if(value.picker!==undefined)out.push('Сборщик: '+(value.picker||'не назначен'));if(value.task_number)out.push('Задание '+value.task_number);return out.join(' · ')||'—';}
  async function openPickR62(id){const d=await api(`/warehouse/picks/${encodeURIComponent(id)}`);const t=d.task,p=d.progress||{},lines=d.lines||[],audit=d.audit||[];const root=$('r62PickDetail');root.classList.remove('hidden');root.dataset.taskId=id;const status=String(t.status||'').toUpperCase(),mode=String(t.pick_mode||'CELL').toUpperCase();root.innerHTML=`<div class="panel-head"><div><span class="kicker">СКЛАДСКОЕ ЗАДАНИЕ ${String(t.source||'ADMIN').toUpperCase()==='SELLER'?'· ОТ СЕЛЛЕРА':''}</span><h3>№${esc(t.task_number)} · ${esc(t.name||'')}</h3><p><span class="r62-status ${esc(t.status)}">${esc(taskStatus(t.status))}</span> · сборщик ${esc(t.picker||'не назначен')}${t.seller_username?` · селлер ${esc(t.seller_username)}`:''}</p></div><button type="button" class="secondary" data-r62-pick-close-panel>Закрыть карточку</button></div><div class="r62-grid"><div class="r62-stat"><small>Выделено / резерв сборки</small><b>${fmtQty(p.total)} шт.</b></div><div class="r62-stat"><small>Собрано</small><b>${fmtQty(p.picked)} шт.</b><small>скан ${fmtQty(p.scan)} · вручную ${fmtQty(p.manual)}</small></div><div class="r62-stat"><small>Нет / пропущено</small><b>${fmtQty(p.missing)} / ${fmtQty(p.skipped)}</b></div></div><div class="r62-actions"><label>Способ сборки<select id="r62PickMode"><option value="CELL" ${mode==='CELL'?'selected':''}>По ячейкам — маршрут по порядку</option><option value="ARTICLE" ${mode==='ARTICLE'?'selected':''}>По артикулу — товары сгруппированы</option></select></label><label>Один сборщик<input id="r62AssignPicker" value="${esc(t.picker||'')}" placeholder="Имя сборщика"></label><button type="button" class="secondary" data-r62-pick-assign="${esc(id)}" ${['CLOSED','CANCELLED','READY_FOR_ADMIN','SPLIT'].includes(status)?'disabled':''}>Назначить одного</button><label>Несколько сборщиков<input id="r62SplitPickers" placeholder="Азиз, Нодыр, Шахи"></label><button type="button" class="secondary" data-r62-pick-split="${esc(id)}" ${status==='DRAFT'?'':'disabled'}>Распределить между сборщиками</button><button type="button" class="secondary" data-r62-pick-unassign="${esc(id)}" ${['ASSIGNED','IN_PROGRESS'].includes(status)?'':'disabled'}>Снять сборщика</button><button type="button" class="secondary" data-r62-pick-report="${esc(id)}">Маршрут / отчёт Excel</button><button type="button" class="primary" data-r62-pick-final-close="${esc(id)}" ${status==='READY_FOR_ADMIN'?'':'disabled'}>Закрыть задание и списать факт</button><button type="button" class="danger" data-r62-pick-cancel="${esc(id)}" ${['CLOSED','CANCELLED'].includes(status)?'disabled':''}>Отменить</button></div><p class="muted">Загрузка задания резервирует выделенный MAIN-остаток. Физический остаток списывается только один раз при финальном закрытии задания. При распределении на нескольких сборщиков программа делит маршрут на дочерние задания без двойного резерва.</p><div class="table-wrap"><table class="r62-pick-lines"><thead><tr><th>Ячейка / короб</th><th>Товар</th><th>План</th><th>Скан</th><th>Вручную</th><th>Нет в коробе</th><th>Пропущено</th><th></th></tr></thead><tbody>${lines.map(x=>{const editable=Number(x.allocated_qty||0)>0&&!['CLOSED','CANCELLED','SPLIT'].includes(status);return `<tr data-r62-pick-line-row="${esc(x.id)}"><td><b>${esc(x.location_code||'—')}</b><small class="subline">${esc(x.container_name||x.container_barcode||'Без короба')}</small>${x.route_label?`<small class="subline">${esc(x.route_label)}</small>`:''}</td><td><b>${esc(x.article||x.request_article||'—')} · ${esc(x.size||x.request_size||'—')}</b><small class="subline">${esc(x.barcode||x.request_barcode||'')}</small></td><td>${fmtQty(x.allocated_qty)}${Number(x.allocated_qty||0)<=0?`<small class="subline bad">Нехватка ${fmtQty(x.requested_qty)}</small>`:''}</td><td><input class="r62-mini-input" data-r62-edit-scan type="number" min="0" step="1" value="${Number(x.scan_qty||0)}" ${editable?'':'disabled'}></td><td><input class="r62-mini-input" data-r62-edit-manual type="number" min="0" step="1" value="${Number(x.manual_qty||0)}" ${editable?'':'disabled'}></td><td><input class="r62-mini-input" data-r62-edit-missing type="number" min="0" step="1" value="${Number(x.missing_qty||0)}" ${editable?'':'disabled'}></td><td>${fmtQty(x.skipped_qty||0)}</td><td><button type="button" class="mini secondary" data-r62-pick-line-save="${esc(x.id)}" ${editable?'':'disabled'}>Сохранить</button></td></tr>`}).join('')}</tbody></table></div><div class="r62-audit"><h4>История изменений</h4>${audit.map(a=>`<div><small>${esc(dateText(a.created_at))} · ${esc(a.actor||'—')} · ${esc(auditActionR64(a.action))}${a.reason?` · ${esc(a.reason)}`:''}</small><div>Было: ${esc(auditFactR64(a.before_json))} → Стало: ${esc(auditFactR64(a.after_json))}</div></div>`).join('')||'<div class="muted">Изменений пока нет</div>'}</div>`;root.scrollIntoView({behavior:'smooth',block:'start'});}

  async function createPickR62(){const file=$('r62PickFile')?.files?.[0],number=$('r62PickNumber')?.value.trim();if(!file)throw new Error('Выберите Excel сборочного задания');if(!number)throw new Error('Укажите номер задания');const fd=new FormData();fd.append('client_id',state.clientId);fd.append('warehouse_id',state.warehouseId);fd.append('task_number',number);fd.append('name',$('r62PickName')?.value.trim()||'');fd.append('created_by',actor());fd.append('file',file);const r=await api('/warehouse/picks/import',{method:'POST',body:fd,timeoutMs:180000});$('r62PickImportMsg').textContent=`Создано №${r.task_number}: запрос ${fmtQty(r.requested)} · выделено ${fmtQty(r.allocated)} · нехватка ${fmtQty(r.shortage)}.`;$('r62PickFile').value='';await renderPicksR62();await openPickR62(r.task_id);window.location.href='/warehouse/picks/'+encodeURIComponent(r.task_id)+'/report.xlsx';}

  async function handleWarehouseClickR62(e){
    try{
      if(e.target.closest('[data-r62-refresh]')){await loadWarehouseWorkR62();return;}
      if(e.target.closest('[data-r62-pick-template]')){window.location.href='/warehouse/picks/template.xlsx';return;}
      if(e.target.closest('[data-r62-pick-create]')){await createPickR62();return;}
      const rg=e.target.closest('[data-r62-receipt-group-open]');if(rg){if(rg.dataset.r62FromManual){go('receivinghome');await ensureContext();}await openReceiptGroupR62(rg.dataset.r62ReceiptGroupOpen||'');return;}
      const rgx=e.target.closest('[data-r62-receipt-group-excel]');if(rgx){downloadReceiptGroupExcel(rgx.dataset.r62ReceiptGroupExcel||'');return;}
      const ro=e.target.closest('[data-r62-receipt-open]');if(ro){if(ro.dataset.r62FromManual){go('receivinghome');await ensureContext();}await openReceiptR62(ro.dataset.r62ReceiptOpen);return;}
      if(e.target.closest('[data-r62-receipt-close]')){$('r62ReceiptDetail').classList.add('hidden');return;}
      if(e.target.closest('[data-r62-receipt-select-all]')){$('r62ReceiptDetail').querySelectorAll('[data-r62-receipt-line]:not(:disabled)').forEach(x=>x.checked=true);return;}
      const tf=e.target.closest('[data-r62-receipt-to-fbs]');if(tf){const ids=[...$('r62ReceiptDetail').querySelectorAll('[data-r62-receipt-line]:checked')].map(x=>x.dataset.r62ReceiptLine);if(!ids.length)throw new Error('Выберите строки Основного склада');if(!confirm(`Перевести выбранные строки (${ids.length}) из Основного склада в FBS? Физический остаток не увеличится.`))return;const q=ctxQuery();const r=await api(`/warehouse/receipts/${encodeURIComponent(tf.dataset.r62ReceiptToFbs)}/to-fbs?${q}`,{method:'POST',body:JSON.stringify({line_ids:ids,performed_by:actor()}),timeoutMs:60000});toast(`Переведено в FBS: ${fmtQty(r.moved)} шт.`);await Promise.allSettled([openReceiptR62(tf.dataset.r62ReceiptToFbs),loadWarehouseSummaryR62(),loadInventoryCatalog({force:true})]);return;}
      const po=e.target.closest('[data-r62-pick-open]');if(po){await openPickR62(po.dataset.r62PickOpen);return;}
      if(e.target.closest('[data-r62-pick-close-panel]')){$('r62PickDetail').classList.add('hidden');return;}
      const as=e.target.closest('[data-r62-pick-assign]');if(as){const picker=$('r62AssignPicker')?.value.trim();if(!picker)throw new Error('Укажите имя сборщика');const pick_mode=$('r62PickMode')?.value||'CELL';await api(`/warehouse/picks/${encodeURIComponent(as.dataset.r62PickAssign)}/assign`,{method:'POST',body:JSON.stringify({picker,assigned_by:actor(),pick_mode})});toast('Задание назначено сборщику');await Promise.all([renderPicksR62(),openPickR62(as.dataset.r62PickAssign)]);return;}
      const split=e.target.closest('[data-r62-pick-split]');if(split){const raw=$('r62SplitPickers')?.value||'',pickers=raw.split(/[;,\n]+/).map(x=>x.trim()).filter(Boolean),pick_mode=$('r62PickMode')?.value||'CELL';if(pickers.length<2)throw new Error('Укажите минимум двух сборщиков через запятую');if(!confirm(`Распределить задание между сборщиками: ${pickers.join(', ')}?`))return;const r=await api(`/warehouse/picks/${encodeURIComponent(split.dataset.r62PickSplit)}/split-assign`,{method:'POST',body:JSON.stringify({pickers,assigned_by:actor(),pick_mode})});toast(`Задание распределено: ${r.assigned} сборщиков`);await renderPicksR62();await openPickR62(split.dataset.r62PickSplit);return;}
      const un=e.target.closest('[data-r62-pick-unassign]');if(un){if(!confirm('Снять сборщика с задания? Уже собранный факт сохранится.'))return;const reason=prompt('Причина:','Переназначение сборщика');if(reason===null)return;await api(`/warehouse/picks/${encodeURIComponent(un.dataset.r62PickUnassign)}/unassign`,{method:'POST',body:JSON.stringify({actor:actor(),reason})});toast('Сборщик снят, факт сохранён');await Promise.all([renderPicksR62(),openPickR62(un.dataset.r62PickUnassign)]);return;}
      const save=e.target.closest('[data-r62-pick-line-save]');if(save){const row=save.closest('[data-r62-pick-line-row]'),scan=Number(row.querySelector('[data-r62-edit-scan]').value||0),manual=Number(row.querySelector('[data-r62-edit-manual]').value||0),missing=Number(row.querySelector('[data-r62-edit-missing]').value||0),reason=prompt('Причина корректировки факта:','Корректировка администратором');if(reason===null)return;if(!reason.trim())throw new Error('Укажите причину изменения');const task=$('r62PickDetail').dataset.taskId;await api(`/warehouse/picks/${encodeURIComponent(task)}/lines/${encodeURIComponent(save.dataset.r62PickLineSave)}`,{method:'PATCH',body:JSON.stringify({scan_qty:scan,manual_qty:manual,missing_qty:missing,reason:reason.trim(),actor:actor()})});toast('Факт позиции обновлён');await Promise.all([openPickR62(task),renderPicksR62()]);return;}
      const report=e.target.closest('[data-r62-pick-report]');if(report){window.location.href=`/warehouse/picks/${encodeURIComponent(report.dataset.r62PickReport)}/report.xlsx`;return;}
      const close=e.target.closest('[data-r62-pick-final-close]');if(close){if(!confirm('Закрыть задание? Собранное и подтверждённое «Нет в коробе» будет окончательно списано с Основного склада.'))return;const reason=prompt('Комментарий к закрытию:','Проверено администратором');if(reason===null)return;const r=await api(`/warehouse/picks/${encodeURIComponent(close.dataset.r62PickFinalClose)}/close`,{method:'POST',body:JSON.stringify({actor:actor(),reason}) ,timeoutMs:120000});toast(`Задание закрыто. Списано ${fmtQty(r.posted)} шт.`);await Promise.allSettled([renderPicksR62(),openPickR62(close.dataset.r62PickFinalClose),loadWarehouseSummaryR62(),loadContextData(),loadInventoryCatalog({force:true})]);return;}
      const cancel=e.target.closest('[data-r62-pick-cancel]');if(cancel){if(!confirm('Отменить задание? До финального закрытия физический остаток не списан.'))return;const reason=prompt('Причина отмены:','Отмена администратором');if(reason===null)return;await api(`/warehouse/picks/${encodeURIComponent(cancel.dataset.r62PickCancel)}/cancel`,{method:'POST',body:JSON.stringify({actor:actor(),reason})});toast('Задание отменено');await Promise.all([renderPicksR62(),openPickR62(cancel.dataset.r62PickCancel)]);return;}
    }catch(err){toast(err.message,true);}
  }

  async function loadWarehouseWorkR62(){installNavAndPage();if(!$('page-warehousework'))throw new Error('Не удалось открыть страницу сборки. Обновите WMS через Ctrl+F5');await ensureContext();await Promise.all([loadWarehouseSummaryR62(),renderReceiptsR62(),renderPicksR62()]);}
  window.loadWarehouseWorkR62=loadWarehouseWorkR62;
  window.openWarehousePickR64=openPickR62;

  function installManualHistory(){const page=$('page-receive');if(!page||$('r62ManualReceivingHistory'))return;const box=document.createElement('div');box.id='r62ManualReceivingHistory';box.className='panel r62-manual-history';box.innerHTML='<div class="panel-head"><div><span class="kicker">ИСТОРИЯ РУЧНЫХ ПРИЁМОК</span><h2>Что приняли за один раз</h2><p>Каждое подтверждение общего черновика хранится одной приёмкой со всем составом, коробами и ячейками.</p></div><button type="button" class="secondary" data-r62-manual-refresh>Обновить</button></div><div id="r62ManualReceivingRows" class="table-wrap"></div>';page.appendChild(box);box.addEventListener('click',async e=>{try{if(e.target.closest('[data-r62-manual-refresh]'))await loadManualReceiptsR62();const group=e.target.closest('[data-r62-receipt-group-open]');if(group){go('receivinghome');await ensureContext();await openReceiptGroupR62(group.dataset.r62ReceiptGroupOpen||'');return;}const gx=e.target.closest('[data-r62-receipt-group-excel]');if(gx){downloadReceiptGroupExcel(gx.dataset.r62ReceiptGroupExcel||'');return;}const open=e.target.closest('[data-r62-receipt-open]');if(open){go('receivinghome');await ensureContext();await openReceiptR62(open.dataset.r62ReceiptOpen);}}catch(err){toast(err.message,true);}});}
  async function loadManualReceiptsR62(){if(!state.clientId||!state.warehouseId)return;const rows=await loadReceiptsR62('MANUAL');const root=$('r62ManualReceivingRows');if(root)root.innerHTML=receiptTableHtml(rows,{manualOnly:true});}
  window.loadR62ManualReceipts=loadManualReceiptsR62;

  installNavAndPage();installManualHistory();
  const oldGo=go;go=function(page){oldGo(page);if(page==='warehousework')loadWarehouseWorkR62().catch(e=>toast(e.message,true));if(page==='receive')loadManualReceiptsR62().catch(()=>{});};
  if(state.activePage==='receive')loadManualReceiptsR62().catch(()=>{});
})();

/* A display cache, never an authority for a warehouse write. Also used by tests. */
(function(root){'use strict';
class StockCache {
  constructor(storage,now=()=>Date.now()){this.storage=storage;this.now=now;this.memory=new Map();this.versions=new Map();this.prefix='fc:r65:stock:';}
  key(scope,kind){if(!scope?.user||!scope?.client||!scope?.warehouse)return '';return this.prefix+JSON.stringify([scope.user,scope.tenant||'',scope.client,scope.warehouse,kind]);}
  get(key){if(!key)return null;if(this.memory.has(key))return this.memory.get(key);try{const x=JSON.parse(this.storage.getItem(key)||'null');if(x&&x.key===key&&Array.isArray(x.data?.rows)&&this.now()-x.savedAt<7*86400000){x.stale=true;this.memory.set(key,x);return x;}}catch{}return null;}
  ticket(key){return this.versions.get(key)||0;}
  put(key,data,ticket){if(!key||ticket!==this.ticket(key))return false;const x={key,data,savedAt:this.now(),stale:false};this.memory.set(key,x);try{this.storage.setItem(key,JSON.stringify(x));}catch{x.memoryOnly=true;}return true;}
  dirty(key){if(!key)return;this.versions.set(key,this.ticket(key)+1);const x=this.get(key);if(x){x.stale=true;try{this.storage.setItem(key,JSON.stringify(x));}catch{}}}
  fresh(key){const x=this.get(key);return !!x&&!x.stale&&this.now()-x.savedAt<60000;}
  clear(){this.memory.clear();try{for(let i=this.storage.length-1;i>=0;i--){const key=this.storage.key(i);if(key?.startsWith(this.prefix))this.storage.removeItem(key);}}catch{}this.versions.clear();}
}
root.FCStockCache=StockCache;
})(typeof window==='undefined'?globalThis:window);

/* r69: one quantity editor shared by stock tables, cells and product/box popups. */
(()=>{'use strict';
  const context=()=>({client_id:state.clientId||'',warehouse_id:state.warehouseId||''});
  const key=()=>JSON.stringify(context());
  const labels={ADD:'Добавить',REMOVE:'Списать',MOVE:'Переместить',REMOVE_ALL:'Удалить позицию целиком',REMOVE_PLACE_ALL:'Удалить из этого места'};
  const name=k=>k==='MAIN'?'Основной склад':'WB FBS';
  let epoch=0;
  function close(){epoch++;const d=$('r69StockDialog');if(d?.open)d.close();d?.remove();}
  function modal(title){close();const d=document.createElement('dialog');d.id='r69StockDialog';d.className='r69-dialog';d.innerHTML=`<header><h2>${esc(title)}</h2><button type="button" class="secondary" data-close>Закрыть</button></header><div data-body></div><p role="alert" class="bad" data-error></p>`;document.body.appendChild(d);d.querySelector('[data-close]').onclick=close;d.addEventListener('cancel',()=>epoch++);d.showModal();return d;}
  function button(action,options,label){return `<button type="button" class="mini ${['REMOVE_ALL','REMOVE_PLACE_ALL'].includes(action)?'danger':'secondary'}" data-r69-stock="${esc(JSON.stringify({...options,action}))}">${esc(label||labels[action])}</button>`;}
  function buttons(options,whole=false){return `<div class="r69-actions">${(whole?['REMOVE_ALL']:['ADD','REMOVE','MOVE','REMOVE_PLACE_ALL']).map(a=>button(a,options)).join('')}</div>`;}
  function accountingButton(){return '';}
  function places(r){return r?.locations||[];}
  const qtext=p=>[p.location,p.container?'короб '+p.container:'без короба'].filter(Boolean).join(' · ');
  async function open(options){
    if(!options.stock_class){const d=modal('Выберите склад'),b=d.querySelector('[data-body]');b.innerHTML='<p>В каком разделе изменить товар?</p><div class="r69-actions"><button type="button" class="primary" data-kind="MAIN">Основной склад</button><button type="button" class="secondary" data-kind="FBS">WB FBS</button></div>';b.querySelectorAll('[data-kind]').forEach(x=>x.onclick=()=>open({...options,stock_class:x.dataset.kind}));return;}
    const requestedAction=options.action||'REMOVE',deletePlace=requestedAction==='REMOVE_PLACE_ALL',action=deletePlace?'REMOVE':requestedAction;
    const kind=options.stock_class||'FBS',ctx=context(),captured=key(),d=modal(deletePlace?'Удалить товар из этого места':(labels[action]||'Изменить товар')),ticket=epoch,body=d.querySelector('[data-body]'),err=d.querySelector('[data-error]');
    const valid=()=>ticket===epoch&&key()===captured;
    body.textContent='Проверяем остатки…';
    try{
      let item=null,ids=options.sku_ids||[],source=[];
      const seed=options.seed&&options.location_id?options.seed:null;
      if(seed&&action==='REMOVE'&&ids.length){
        // r139: the cell/box row already contains this exact bucket. Do not download
        // every warehouse position merely to open one write-off dialog.
        item={article:options.article||seed.article||options.barcode||'Товар',size:options.size||seed.size||'',barcode:options.barcode||seed.barcode||'',sku_ids:ids};
        source=[{sku_id:ids[0],location_id:options.location_id,container_id:options.container_id||null,
          location:seed.location||'',container:seed.container||'',quantity:Number(seed.quantity||0),free:Number(seed.free??seed.quantity??0)}];
      }else{
        const fresh=await api('/warehouse-ui/stocks?'+new URLSearchParams({...ctx,stock_class:kind}),{timeoutMs:30000});if(!valid())return;
        item=fresh.rows.find(r=>options.barcode?r.barcode===options.barcode:(r.sku_ids||[]).some(id=>(options.sku_ids||[]).includes(id)));
        ids=item?.sku_ids||options.sku_ids||[];
        if(!ids.length)throw Error('Товар не найден. Обновите остатки или откройте приёмку.');
        source=places(item).filter(p=>(!options.location_id||p.location_id===options.location_id)&&(!options.location_id||String(p.container_id||'')===String(options.container_id||'')));
      }
      // MAIN opens without downloading every address/box. Fetch these only
      // when an editor needs destinations, isolated to its captured context.
      let locations=state.locations||[],containers=state.containers||[];
      if(action==='MOVE'||action==='ADD'){
        [locations,containers]=await Promise.all([
          api('/r145f25/locations?'+new URLSearchParams({client_id:ctx.client_id,warehouse_id:ctx.warehouse_id}),{timeoutMs:10000}),
          api('/masterdata/containers?'+new URLSearchParams(ctx),{timeoutMs:10000})
        ]);
        if(!valid())return;
      }
      const destinationLocations=locations.filter(l=>l.is_active!==false&&String(l.warehouse_id||ctx.warehouse_id)===ctx.warehouse_id);
      const boxes=containers.filter(b=>b.status==='ACTIVE'&&String(b.client_id||ctx.client_id)===ctx.client_id&&String(b.warehouse_id||ctx.warehouse_id)===ctx.warehouse_id);
      if(action==='ADD'&&!source.length&&options.location_id){const loc=destinationLocations.find(l=>l.id===options.location_id),box=boxes.find(b=>b.id===options.container_id);if(loc)source.push({sku_id:ids[0],location_id:loc.id,container_id:box?.id||null,location:loc.code,container:box?.name||box?.barcode||'',quantity:0});}
      if(action==='ADD'&&!source.length&&!options.location_id)for(const loc of destinationLocations)source.push({sku_id:ids[0],location_id:loc.id,container_id:null,location:loc.code,container:'',quantity:0});
      body.innerHTML=`<p><b>${esc(item?.article||options.article||options.barcode||'Товар')} · ${esc(item?.size||'')}</b></p><p>${esc(name(kind))}</p>${deletePlace?'<p class="warn"><b>Удаление только из выбранного короба / ячейки.</b> История сохранится. Зарезервированный товар удалить нельзя.</p>':''}<form data-editor><div class="r69-form">${action==='REMOVE_ALL'?'<p class="wide">Будут списаны все единицы этой позиции во всех её местах в выбранном складе. Карточка товара и история сохранятся.</p>':`<label class="wide">${action==='ADD'?'Куда добавить':'Откуда'}<select name="source" required>${source.map((p,i)=>`<option value="${i}">${esc(qtext(p))} · ${fmtQty(p.quantity)} шт.</option>`).join('')}</select></label><label>Количество<input name="quantity" type="number" min="1" max="1000000" step="1" value="${deletePlace?Math.max(1,Math.floor(Number(source[0]?.quantity||1))):1}" required inputmode="numeric" ${deletePlace?'readonly':''}></label>`}
      ${action==='MOVE'?`<label class="wide">Куда: ячейка или палета<input name="destination" list="r69Destinations" placeholder="Введите или отсканируйте адрес" required autocomplete="off"><datalist id="r69Destinations">${destinationLocations.map(l=>`<option value="${esc(l.code)}"></option>`).join('')}</datalist></label><label class="wide">Короб назначения<select name="box"><option value="">Без короба</option></select></label>`:''}
      <label class="wide">Причина<input name="reason" maxlength="500" required value="${esc(deletePlace?'Удаление товара из конкретного короба / ячейки':action==='REMOVE_ALL'?'Удаление позиции из остатка':action==='MOVE'?'Перемещение товара':action==='ADD'?'Ручное добавление':'Ручное списание')}"></label></div><div class="r69-actions"><button class="${deletePlace?'danger':'primary'}" type="submit">${esc(deletePlace?'Удалить из этого места':labels[action])}</button>${kind==='FBS'?accountingButton(ids):''}</div></form><div data-preview></div>`;
      const form=body.querySelector('form'),target=body.querySelector('[data-preview]');let formVersion=0,requestId=uid(),submittedPayload=null;
      function destination(){const value=String(form.elements?.destination?.value||form.querySelector('[name=destination]')?.value||'').trim();return destinationLocations.find(l=>[l.code,l.barcode,l.id].includes(value));}
      const dest=form.querySelector('[name=destination]');if(dest)dest.oninput=()=>{const loc=destination();form.querySelector('[name=box]').innerHTML='<option value="">Без короба</option>'+boxes.filter(b=>b.location_id===loc?.id).map(b=>`<option value="${esc(b.id)}">${esc(b.name||b.barcode)}</option>`).join('');target.innerHTML='';};
      form.addEventListener('input',()=>{formVersion++;target.innerHTML='';});form.addEventListener('change',()=>{formVersion++;target.innerHTML='';});
      form.onsubmit=async e=>{e.preventDefault();err.textContent='';const started=performance.now(),submit=form.querySelector('[type=submit]'),originalText=submit.textContent;submit.disabled=true;submit.textContent=deletePlace?'Удаляем…':action==='REMOVE_ALL'?'Удаляем…':action==='REMOVE'?'Списываем…':action==='MOVE'?'Перемещаем…':'Сохраняем…';target.innerHTML='<p class="muted">Проводим локальное изменение…</p>';try{
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
        const elapsed=Math.max(0,performance.now()-started),speed=`${(elapsed/1000).toFixed(elapsed<1000?2:1)} с`;
        target.innerHTML=`<p class="good"><b>Готово за ${speed}.</b> Локальный остаток изменён${kind==='FBS'?'; WB обновляется в фоне.':'.'}</p>`;
        window.FCStockRooms?.invalidate();
        setTimeout(()=>{if(valid()){close();toast((result.wb||'Остаток изменён. Операция сохранена в истории.')+` · ${speed}`);}},40);
      }catch(error){const msg=String(error?.message||error);err.textContent=msg.includes('Есть резерв или незавершённое списание')?'Удаление остановлено только из-за реально активного заказа/списания. Если заказ уже закрыт, обновите страницу — устаревший резерв очищается автоматически.':msg;target.innerHTML='';}finally{form.querySelectorAll('input,select,button').forEach(x=>x.disabled=false);submit.textContent=originalText;}};
    }catch(error){body.innerHTML='';err.textContent=error.message;}
  }
  const bulkPending=new Map();
  async function bulkOpen(kind,codes=null){
    const ctx=context(),captured=key(),pendingKey='fc:r77:bulk-remove:'+captured+':'+kind;
    const d=modal('Списать остатки · '+name(kind)),body=d.querySelector('[data-body]'),err=d.querySelector('[data-error]'),ticket=epoch;
    const valid=()=>ticket===epoch&&key()===captured;
    const remember=req=>{if(req)bulkPending.set(pendingKey,req);else bulkPending.delete(pendingKey);try{req?localStorage.setItem(pendingKey,JSON.stringify(req)):localStorage.removeItem(pendingKey);}catch{}};
    let saved=bulkPending.get(pendingKey);try{saved=saved||JSON.parse(localStorage.getItem(pendingKey)||'null');}catch{}
    let submitting=false;
    async function send(req){if(submitting)return;submitting=true;err.textContent='';body.querySelectorAll('button,input').forEach(x=>x.disabled=true);remember(req);
      try{const result=await api('/warehouse-ui/actions/bulk-remove',{method:'POST',body:JSON.stringify(req),timeoutMs:90000});remember(null);if(!valid())return;close();window.FCStockRooms?.invalidate();toast(`Списано ${fmtQty(result.quantity)} шт. Причина сохранена в истории.`);}
      catch(e){if(valid()){err.textContent=e.message;if([400,403,404,409,422].includes(Number(e.status))){remember(null);body.innerHTML='<p>Списание не выполнено. Откройте окно заново для актуальных остатков.</p>';}else{body.innerHTML='<p>Ответ не получен. Проверьте результат тем же запросом, чтобы не списать дважды.</p><button type="button" data-replay>Проверить результат списания</button>';body.querySelector('[data-replay]').onclick=()=>send(req);}}}
      finally{submitting=false;body.querySelectorAll('button,input').forEach(x=>x.disabled=false);}
    }
    if(saved){body.innerHTML='<p>Есть незавершённый запрос списания. Сначала проверьте его результат.</p><button type="button" data-replay>Проверить результат списания</button>';body.querySelector('button').onclick=()=>send(saved);return;}
    body.textContent='Проверяем свободные остатки и резервы…';
    try{const response=await api('/warehouse-ui/actions/bulk-options?'+new URLSearchParams({...ctx,stock_class:kind}),{timeoutMs:60000});if(!valid())return;
      const rows=response.rows.filter(r=>!codes||codes.includes(r.barcode));
      body.innerHTML=`<p>Это физическое списание со склада. Зарезервированный товар сохраняется. Выберите строки и количество; «Все свободные» заполняет количества только в показанном списке.</p><div class="r69-actions"><button type="button" data-all>Все свободные</button><button type="button" data-none>Снять выбор</button></div><form><div style="max-height:50vh;overflow:auto"><table><thead><tr><th>Выбор</th><th>Товар / место</th><th>Свободно</th><th>Списать</th></tr></thead><tbody>${rows.map((r,i)=>`<tr><td><input type="checkbox" data-pick="${i}" ${r.free<1?'disabled':''}></td><td>${esc(r.article)} · ${esc(r.size)}<small>ШК ${esc(r.barcode)} · ${esc(qtext(r))}</small></td><td>${fmtQty(r.free)}</td><td><input aria-label="Количество для списания" data-qty="${i}" type="number" min="1" max="${r.free}" step="1" value="${Math.max(1,r.free)}" ${r.free<1?'disabled':''}></td></tr>`).join('')}</tbody></table></div><label>Причина<input name="reason" required maxlength="500" value="Ручное массовое списание"></label><p data-total></p><button class="danger" type="submit">Списать выбранное количество</button></form>`;
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

/* FC_K9R65_STOCK_ROOMS: bundled after app.js; existing WB write paths are retained. */
(()=>{'use strict';
  let storage;try{storage=localStorage;}catch{storage=null;}
  const cache=new window.FCStockCache(storage),flights=new Map(),errors=new Map(),retryAfter=new Map();
  const pages={MAIN:1,FBS:1},queries={MAIN:'',FBS:''},selectedMainBarcodes=new Set();let installed=false,dialogEpoch=0,refreshTimer,loggedOut=false;
  const name=k=>k==='MAIN'?'Основной склад':'WB FBS';
  const scope=()=>({user:loggedOut?'':state.commercialSession?.username||'',tenant:state.commercialSession?.tenant_id||'',client:state.clientId||'',warehouse:state.warehouseId||''});
  const key=k=>cache.key(scope(),k);
  let identityPromise;
  async function identity(){if(scope().user)return;if(!identityPromise)identityPromise=(async()=>{let me;try{me=await api('/commercial/session',{timeoutMs:5000});}catch{me=await api('/auth/me',{timeoutMs:5000});}if(!loggedOut&&!state.commercialSession?.username)state.commercialSession=me;})().finally(()=>identityPromise=null);return identityPromise;}
  const current=k=>state.activePage===(k==='MAIN'?'mainstock':'inventory');
  const qs=(extra={})=>new URLSearchParams({client_id:state.clientId||'',warehouse_id:state.warehouseId||'',...extra});
  const rows=k=>cache.get(key(k))?.data.rows||[];
  const rowKey=r=>r.barcode||'sku:'+(r.sku_ids||[])[0];
  const time=v=>v?new Date(v).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'—';
  const matched=(row,q)=>[row.article,row.size,row.barcode,...(row.locations||[]).flatMap(p=>[p.location,p.container])].join(' ').toLowerCase().includes(q.toLowerCase());
  function status(k){const entry=cache.get(key(k)),failure=errors.get(key(k));if(!entry)return failure?'Не удалось получить остатки: '+failure:'Загружаем остатки из WMS…';return `${entry.stale?'Сохранённые остатки':'Остатки на'} ${time(entry.data.generated_at)}.${flights.has(key(k))?' Обновляем в фоне…':''}${failure?' Обновление не удалось: '+failure+'. Показаны сохранённые данные.':''}${entry.memoryOnly?' Сохранение на этом устройстве недоступно.':''}`;}
  async function refresh(k,force=false){
    // Paint the saved snapshot synchronously, before the first network await.
    render(k);
    if(!scope().user){try{await identity();}catch(e){const target=$(k==='MAIN'?'r65MainStatus':'r65FbsStatus');if(target)target.textContent='Не удалось проверить вход. Обновите страницу.';return;}}
    const kkey=key(k);if(!kkey)return;
    const ticket=cache.ticket(kkey),existing=flights.get(kkey);
    // A force button/poll/second navigation shares the SAME current generation.
    // A committed mutation has a newer ticket and may bypass an obsolete read.
    if(existing?.ticket===ticket)return existing.promise;
    if(!force&&(cache.fresh(kkey)||Date.now()<(retryAfter.get(kkey)||0))){render(k);return;}
    const query=qs({stock_class:k}),controller=new AbortController();
    const job={ticket,controller,promise:null};
    job.promise=Promise.resolve().then(async()=>{try{
      const d=await api('/warehouse-ui/stocks?'+query,{timeoutMs:20000,signal:controller.signal});
      if(key(k)!==kkey||cache.ticket(kkey)!==ticket)return;
      if(d.client_id!==state.clientId||d.warehouse_id!==state.warehouseId||d.stock_class!==k)throw Error('Ответ относится к другому складу');
      if(cache.put(kkey,d,ticket)){errors.delete(kkey);retryAfter.delete(kkey);}
    }catch(e){if(key(k)===kkey&&cache.ticket(kkey)===ticket){errors.set(kkey,e.message);retryAfter.set(kkey,Date.now()+30000);}}
    finally{if(flights.get(kkey)===job)flights.delete(kkey);if(key(k)===kkey)render(k);}
    });flights.set(kkey,job);render(k);return job.promise;
  }
  function invalidate(managed=false){
    for(const k of ['MAIN','FBS']){const id=key(k);cache.dirty(id);retryAfter.delete(id);flights.get(id)?.controller.abort();}
    // Dirty is not empty: keep the last snapshot visible while refreshing it.
    clearTimeout(refreshTimer);
    if(!managed)refreshTimer=setTimeout(()=>{for(const k of ['MAIN','FBS'])if(current(k))refresh(k,true);},150);
  }
  let opened=null;
  async function refreshOpen(){const x=opened;if(!x||!$('r65Dialog')?.open)return;if(x.type==='box')return openBox(x.id,x.kind);return places(x.ref,x.kind);}
  function closeDialog(){opened=null;dialogEpoch++;const d=$('r65Dialog');if(d?.open)d.close();d?.remove();}
  function dialog(title,subtitle=''){
    closeDialog();const d=document.createElement('dialog');d.id='r65Dialog';d.className='r65-dialog';
    d.innerHTML=`<header><div><h2>${esc(title)}</h2><p>${esc(subtitle)}</p></div><button type="button" class="secondary r65-close">Закрыть</button></header><div class="r65-dialog-body"></div>`;
    document.body.appendChild(d);d.querySelector('.r65-close').onclick=closeDialog;d.addEventListener('cancel',()=>dialogEpoch++);d.showModal();return d;
  }
  function bindPlaces(root,k){
    root.querySelectorAll('[data-r65-box]').forEach(b=>b.onclick=()=>openBox(b.dataset.r65Box,k).catch(e=>toast(e.message,true)));
    root.querySelectorAll('[data-r65-location]').forEach(b=>b.onclick=()=>{const id=b.dataset.r65Location;closeDialog();go('locations');openLocationContents(id).catch(e=>toast(e.message,true));});
  }
  function placeButton(p,k,r){const actions=window.FCStockActions?.buttons({stock_class:k,barcode:r?.barcode||'',article:r?.article||'',size:r?.size||'',sku_ids:[p.sku_id],location_id:p.location_id,container_id:p.container_id,seed:{article:r?.article||'',size:r?.size||'',barcode:r?.barcode||'',location:p.location||'',container:p.container||'',quantity:Number(p.quantity||0),free:Number(p.free??p.quantity??0)}})||'';return '<div class="r69-place-controls">'+actions+(p.container_id?`<button type="button" class="primary" data-r65-box="${esc(p.container_id)}">Открыть короб</button>`:`<button type="button" class="secondary" data-r65-location="${esc(p.location_id)}">Открыть ячейку</button>`)+'</div>';}
  async function places(ref,k){
    const saved=rows(k).find(r=>rowKey(r)===ref),code=saved?.barcode||(ref.startsWith('sku:')?'':ref);const d=dialog('Где лежит товар',name(k)+(code?' · ШК '+code:' · товар без ШК')),root=d.querySelector('.r65-dialog-body'),epoch=dialogEpoch,context=key(k);
    opened={type:'places',ref,kind:k};
    const draw=r=>{root.innerHTML=`<h3>${esc(r?.article||code)} · ${esc(r?.size||'—')}</h3><p class="r65-status">${esc(status(k))}</p><div class="r65-place-list">${(r?.locations||[]).map(p=>`<div class="r65-place"><div><b>${esc(p.container||p.location||'Без адреса')}</b><small>${p.container?'Короб · адрес '+esc(p.location):'Товар без короба'}</small>${p.route_label?`<small>${esc(p.route_label)}</small>`:''}</div><div><b>${fmtQty(p.quantity)} шт.</b>${k==='MAIN'?`<small>Свободно ${fmtQty(p.free)} · в сборке ${fmtQty(p.held)}</small>`:''}</div>${placeButton(p,k,r)}</div>`).join('')||'<p>В этом разделе товар сейчас отсутствует.</p>'}</div><details data-r65-history><summary>Когда товар закончился и появился снова</summary><p class="muted">История физического товара в местах хранения, включая переводы между разделами.</p><div data-r65-history-body></div></details>`;bindPlaces(root,k);if(r&&k==='FBS'){const bar=document.createElement('div');bar.innerHTML=window.FCStockActions?.accountingButton(r.sku_ids)||'';root.prepend(bar);}const detail=root.querySelector('[data-r65-history]');if(!code){detail.remove();return;}detail.addEventListener('toggle',async()=>{if(!detail.open||detail.dataset.loaded)return;detail.dataset.loaded='1';const target=detail.querySelector('[data-r65-history-body]');target.textContent='Загружаем историю…';try{const events=await api('/warehouse-ui/history?'+qs({barcode_value:code}),{timeoutMs:30000});if(epoch!==dialogEpoch||context!==key(k))return;target.innerHTML=inventoryLifecycleHtml(events.events||[]);target.querySelectorAll('[data-inventory-local-location]').forEach(b=>b.onclick=()=>{if(b.dataset.inventoryLocalContainer)openBox(b.dataset.inventoryLocalContainer,k).catch(e=>toast(e.message,true));else{closeDialog();go('locations');openLocationContents(b.dataset.inventoryLocalLocation).catch(e=>toast(e.message,true));}});}catch(e){target.textContent=e.message;delete detail.dataset.loaded;}});};
    if(saved)draw(saved);else root.textContent='Загружаем остатки…';
    await refresh(k);if(epoch!==dialogEpoch||key(k)!==context)return;draw(rows(k).find(r=>rowKey(r)===ref));
  }
  async function openBox(id,k='FBS'){
    const d=dialog('Содержимое короба',name(k)),root=d.querySelector('.r65-dialog-body'),epoch=dialogEpoch,context=key(k);root.textContent='Загружаем выбранный короб…';
    opened={type:'box',id,kind:k};
    try{const b=await api('/warehouse-ui/boxes/'+encodeURIComponent(id)+'?'+qs({stock_class:k}),{timeoutMs:20000});if(epoch!==dialogEpoch||key(k)!==context)return;
      d.querySelector('h2').textContent=b.name||b.barcode;d.querySelector('header p').textContent=name(k)+' · адрес '+b.location;
      root.innerHTML=`<div class="r65-toolbar"><b>${b.rows.length} поз. · ${fmtQty(b.rows.reduce((n,r)=>n+r.quantity,0))} шт.</b>${b.location_id?`<button type="button" class="secondary" data-r65-location="${esc(b.location_id)}">Открыть ячейку / палету целиком</button>`:''}</div><table class="r65-table"><thead><tr><th>Товар</th><th>ШК</th><th>В этом коробе</th>${k==='MAIN'?'<th>Свободно</th>':''}<th>Действия</th></tr></thead><tbody>${b.rows.map(r=>`<tr><td><button type="button" class="product-title-link fc-box-product-link" data-fc-product-card-barcode="${esc(r.barcode||'')}"><b>${esc(r.article||'—')}</b><small>Размер ${esc(r.size||'—')}</small></button></td><td>${esc(r.barcode)}</td><td>${fmtQty(r.quantity)} шт.</td>${k==='MAIN'?`<td>${fmtQty(r.free)} шт.<small>В сборке ${fmtQty(r.held)}</small></td>`:''}<td>${window.FCStockActions?.buttons({stock_class:k,barcode:r.barcode,sku_ids:r.sku_ids,location_id:r.locations[0]?.location_id,container_id:id})||''}</td></tr>`).join('')||`<tr><td colspan="4">${b.status==='ACTIVE'?'В этом коробе нет товара раздела «'+esc(name(k))+'».':'Короб архивный.'}</td></tr>`}</tbody></table>`;bindPlaces(root,k);
      const route=document.createElement('div');route.className='r66-box-route';
      route.innerHTML=`<b>${esc(b.route?.route_label||'Ряд не указан')}</b>${b.route?.route_pallet_id&&b.status==='ACTIVE'?`<form data-r66-row-form><label>Ряд — сторона палеты<select name="row"><option value="">Не указан</option><option value="1" ${b.route.route_row===1?'selected':''}>Ряд 1 · до 8 коробов</option><option value="2" ${b.route.route_row===2?'selected':''}>Ряд 2 · до 8 коробов</option>${b.route.route_row>2?`<option value="${b.route.route_row}" selected>Ряд ${b.route.route_row} · до 8 коробов</option>`:''}</select></label><label>Причина изменения<input name="reason" maxlength="500" placeholder="Например: уточнение адреса" required></label><button class="secondary" type="submit">Сохранить ряд</button><small data-route-result></small></form>`:'<small>Ряд указывается для короба на палете или паллетоместе.</small>'}`;root.prepend(route);
      const form=route.querySelector('form');if(form)form.onsubmit=async e=>{e.preventDefault();const button=form.querySelector('button'),status=form.querySelector('[data-route-result]');button.disabled=true;status.textContent='Сохраняем…';try{const values=new FormData(form);await api('/warehouse-ui/boxes/'+encodeURIComponent(id)+'/row',{method:'PATCH',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,row_on_pallet:values.get('row')?Number(values.get('row')):null,reason:String(values.get('reason')||'').trim()})});if(epoch!==dialogEpoch||key(k)!==context)return;await openBox(id,k);}catch(err){status.textContent=err.message;button.disabled=false;}};
      const history=document.createElement('details');history.innerHTML='<summary>Когда короб опустел и заполнился снова</summary><p class="muted">История всего выбранного короба на этом складе.</p><div></div>';root.appendChild(history);
      history.addEventListener('toggle',async()=>{if(!history.open||history.dataset.loaded)return;history.dataset.loaded='1';const target=history.lastElementChild;target.textContent='Загружаем историю короба…';try{const events=await api('/warehouse-ui/boxes/'+encodeURIComponent(id)+'/history?'+qs(),{timeoutMs:30000});if(epoch!==dialogEpoch||key(k)!==context)return;target.innerHTML=inventoryLifecycleHtml(events.events||[],{cell:true});}catch(e){target.textContent=e.message;delete history.dataset.loaded;}});
    }catch(e){if(epoch===dialogEpoch)root.textContent=e.message;}
  }
  function paging(root,k,total){const count=Math.max(1,Math.ceil(total/80));pages[k]=Math.min(pages[k],count);root.innerHTML=`<button type="button" class="secondary" data-prev ${pages[k]<=1?'disabled':''}>Назад</button><span>${pages[k]} / ${count}</span><button type="button" class="secondary" data-next ${pages[k]>=count?'disabled':''}>Далее</button>`;root.querySelector('[data-prev]').onclick=()=>{pages[k]--;render(k);};root.querySelector('[data-next]').onclick=()=>{pages[k]++;render(k);};}
  function mainFiltered(){return rows('MAIN').filter(r=>matched(r,queries.MAIN));}
  function mainSelectableCode(r){return String(r.barcode||('sku:'+(r.sku_ids||[])[0]||''));}
  function mainSelectedRows(){return rows('MAIN').filter(r=>selectedMainBarcodes.has(mainSelectableCode(r)));}
  function updateMainSelection(){
    for(const code of [...selectedMainBarcodes])if(!rows('MAIN').some(r=>mainSelectableCode(r)===code))selectedMainBarcodes.delete(code);
    const filtered=mainFiltered(),selected=mainSelectedRows(),summary=$('r86MainSelection');
    if(summary)summary.textContent=`Выбрано ${selected.length} поз. · ${fmtQty(selected.reduce((s,r)=>s+Number(r.free||0),0))} шт. свободного остатка`;
    const all=$('r86MainSelectAll');if(all){all.checked=filtered.length>0&&filtered.every(r=>selectedMainBarcodes.has(mainSelectableCode(r)));all.indeterminate=!all.checked&&filtered.some(r=>selectedMainBarcodes.has(mainSelectableCode(r)));}
    const del=$('r86MainDeleteSelected');if(del)del.disabled=!selected.length;
    const move=$('r108MainTransferSelected');if(move)move.disabled=!selected.length;
  }
  function r108Uuid(){if(globalThis.crypto?.randomUUID)return crypto.randomUUID();const b=new Uint8Array(16);crypto.getRandomValues(b);b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;const h=[...b].map(x=>x.toString(16).padStart(2,'0')).join('');return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;}
  async function r108Transfer(source,target,{barcodes=[],locationQuery=''}={}){
    const clean=[...new Set((barcodes||[]).map(x=>String(x||'').trim()).filter(Boolean))];
    if(!clean.length&&!String(locationQuery||'').trim())throw Error('Выберите товары или укажите стеллаж / полку / ячейку');
    const base={client_id:state.clientId,warehouse_id:state.warehouseId,source,target,barcodes:clean,location_query:String(locationQuery||'').trim()||null,include_descendants:true};
    const preview=await api('/warehouse-ui/class-transfer/preview',{method:'POST',body:JSON.stringify(base),timeoutMs:60000});
    const from=source==='MAIN'?'Основного склада':'WB FBS',to=target==='FBS'?'WB FBS':'Основной склад';
    const place=preview.location?`
Адрес: ${preview.location.code||preview.location.name||locationQuery} + дочерние адреса`:'';
    const protectedHint=Number(preview.protected_quantity||0)>0?`
Защищено активной сборкой и остаётся на Основном: ${fmtQty(preview.protected_quantity)} шт.`:'';
    if(!confirm(`Перевести свободный товар из ${from} → ${to}?

Позиций: ${preview.positions}
Мест хранения: ${preview.places}
Количество: ${fmtQty(preview.quantity)} шт.${place}${protectedHint}

Физический остаток и адреса не меняются. Резервы заказов и активная сборка не переводятся.`))return null;
    const result=await api('/warehouse-ui/class-transfer/apply',{method:'POST',body:JSON.stringify({...base,request_id:r108Uuid(),fingerprint:preview.fingerprint,performed_by:state.commercialSession?.username||'Администратор',reason:`${source} -> ${target} из экрана остатков`}),timeoutMs:120000});
    selectedMainBarcodes.clear();try{selectedInventoryBarcodes.clear();}catch{}
    invalidate(true);await Promise.allSettled([refresh('MAIN',true),refresh('FBS',true),typeof loadInventoryCatalog==='function'?loadInventoryCatalog({force:true}):Promise.resolve()]);
    const failed=(result.publication||[]).filter(x=>x.status==='NOT_QUEUED');
    toast(`Переведено ${fmtQty(result.quantity)} шт. · ${result.positions} поз.${failed.length?` · WB требует внимания: ${failed.length}`:' · остаток WB поставлен в очередь обновления'}`,Boolean(failed.length));
    return result;
  }
  function renderMain(){const host=$('r65MainBody');if(!host)return;const filtered=mainFiltered();pages.MAIN=Math.min(pages.MAIN,Math.max(1,Math.ceil(filtered.length/80)));
    $('r65MainStatus').textContent=status('MAIN');$('r65MainTotal').textContent=`${filtered.length} позиций · ${fmtQty(filtered.reduce((s,r)=>s+r.quantity,0))} шт. · свободно ${fmtQty(filtered.reduce((s,r)=>s+r.free,0))} шт.`;
    host.innerHTML=filtered.slice((pages.MAIN-1)*80,pages.MAIN*80).map(r=>{const code=mainSelectableCode(r);return `<tr><td class="r86-main-select" style="width:52px"><input type="checkbox" aria-label="Выбрать ${esc(r.barcode||r.article||'товар')}" data-r86-main-select="${esc(code)}" ${selectedMainBarcodes.has(code)?'checked':''}></td><td><b>${esc(r.article||'Без артикула')}</b><small>Размер ${esc(r.size||'—')} · ШК ${esc(r.barcode)}</small></td><td class="r65-qty">${fmtQty(r.quantity)}<small>В сборке ${fmtQty(r.held)}</small></td><td class="r65-qty">${fmtQty(r.free)}</td><td><button type="button" class="secondary" data-r65-places="${esc(rowKey(r))}">Где лежит</button><small>${r.locations.length} мест</small>${window.FCStockActions?.buttons({stock_class:'MAIN',barcode:r.barcode,sku_ids:r.sku_ids},true)||''}</td></tr>`;}).join('')||`<tr><td colspan="5">${cache.get(key('MAIN'))?'Товары не найдены. При приёмке выберите «Основной склад».':'Ожидаем загрузку остатков…'}</td></tr>`;
    host.querySelectorAll('[data-r65-places]').forEach(b=>b.onclick=()=>places(b.dataset.r65Places,'MAIN').catch(e=>toast(e.message,true)));host.querySelectorAll('[data-r86-main-select]').forEach(x=>x.onchange=()=>{x.checked?selectedMainBarcodes.add(x.dataset.r86MainSelect):selectedMainBarcodes.delete(x.dataset.r86MainSelect);updateMainSelection();});paging($('r65MainPager'),'MAIN',filtered.length);updateMainSelection();
  }
  function fbsRows(){
    const q=$('inventorySearch')?.value.trim()||'',canon=new Map((state.inventoryCatalog||[]).map(r=>[String(r.barcode||''),r]));
    const list=rows('FBS').filter(r=>matched(r,q)).map(r=>({...canon.get(r.barcode),barcode:r.barcode,
      article:canon.get(r.barcode)?.article||r.article,size:canon.get(r.barcode)?.size||r.size,
      _truth_pending:canon.get(r.barcode)?Boolean(canon.get(r.barcode)._truth_pending):true,
      _r65:r}));
    return window.FCWBSales?window.FCWBSales.filter(list):list;
  }
  function renderFbs(){
    const body=$('inventoryBody');if(!body||!installed)return;const list=fbsRows();pages.FBS=Math.min(pages.FBS,Math.max(1,Math.ceil(list.length/80)));
    if($('inventoryCatalogSummary'))$('inventoryCatalogSummary').textContent=`WB FBS · ${list.length} позиций · физически ${fmtQty(list.reduce((s,r)=>s+r._r65.quantity,0))} шт.`;
    $('r65FbsStatus').textContent=status('FBS');
    body.innerHTML=list.slice((pages.FBS-1)*80,pages.FBS*80).map(r=>{const x=r._r65,known=!r._truth_pending&&r.available_to_sell!=null,actual=wbActualQuantity(r.barcode);return `<tr><td><input type="checkbox" aria-label="Выбрать ${esc(r.barcode)}" data-inventory-select="${esc(r.barcode)}" ${r.barcode?'':'disabled'} ${selectedInventoryBarcodes.has(r.barcode)?'checked':''}></td><td><b>${esc(r.article||'Без артикула')}</b><small>${esc(r.title||'')} · размер ${esc(r.size||'—')}</small><small>ШК ${esc(r.barcode)}</small></td><td><b class="r65-qty">${fmtQty(x.quantity)}</b><small>${known?window.FCR24History.fbsBadge(r,x.quantity):'Резерв уточняется'}</small></td><td>${window.FCWBSales?window.FCWBSales.cell(r.barcode):'Проверяем состояние WB…'}</td><td><button type="button" class="secondary" data-r65-fbs-places="${esc(rowKey(r._r65))}">Где лежит</button><small>${x.locations.length} мест</small>${window.FCWBSales?.rowActions(r)||''}${window.FCStockActions?.buttons({stock_class:'FBS',barcode:r.barcode,sku_ids:x.sku_ids},true)||''}${r.product_id?`<button type="button" class="mini secondary" data-inventory-open="${esc(r.product_id)}" data-inventory-barcode="${esc(r.barcode)}">Карточка</button>`:''}</td></tr>`;}).join('')||`<tr><td colspan="5">${cache.get(key('FBS'))?'Товары WB FBS не найдены. Перевод с Основного склада доступен отдельной кнопкой.':'Загружаем остатки WB FBS…'}</td></tr>`;
    body.querySelectorAll('[data-r65-fbs-places]').forEach(b=>b.onclick=()=>places(b.dataset.r65FbsPlaces,'FBS').catch(e=>toast(e.message,true)));paging($('r65FbsPager'),'FBS',list.length);updateInventoryBulkToolbar();
    const all=document.querySelector('[data-inventory-select-visible]');if(all){all.checked=list.length>0&&list.every(r=>selectedInventoryBarcodes.has(r.barcode));all.indeterminate=!all.checked&&list.some(r=>selectedInventoryBarcodes.has(r.barcode));}const r108move=$('r108FbsTransferSelected');if(r108move)r108move.disabled=selectedInventoryBarcodes.size===0;
  }
  function render(k){if(k==='MAIN')renderMain();else renderFbs();}
  async function saveProduct(input){return FC26Cards.save({...input,category:input.category||'\u0411\u0435\u0437 \u043a\u0430\u0442\u0435\u0433\u043e\u0440\u0438\u0438'});}
  function boot(cached){
    if(state.activePage!=='mainstock'||!scope().user)return;
    const me=state.commercialSession,c=state.clientId||localStorage.getItem('fc_client'),w=state.warehouseId||localStorage.getItem('fc_warehouse');
    const restricted=!me.super_admin&&(Array.isArray(me.allowed_client_ids)||Array.isArray(me.allowed_warehouse_ids));
    const clients=(cached.clients||[]).filter(x=>!restricted||(me.allowed_client_ids||[]).map(String).includes(String(x.id)));
    const warehouses=(cached.warehouses||[]).filter(x=>!restricted||(me.allowed_warehouse_ids||[]).map(String).includes(String(x.id)));
    if(!clients.some(x=>String(x.id)===c)||!warehouses.some(x=>String(x.id)===w))return;
    state.clients=clients;state.warehouses=warehouses;state.clientId=c;state.warehouseId=w;renderContextSelects();go('mainstock');
  }

  function registerProduct(product){return FC26Cards.edit(product);}
  async function lookupProduct(raw){const normalized=normalizeReceivingBarcode(raw);if(!normalized.barcode)throw Error('\u041e\u0442\u0441\u043a\u0430\u043d\u0438\u0440\u0443\u0439\u0442\u0435 \u0428\u041a \u0442\u043e\u0432\u0430\u0440\u0430');const captured=JSON.stringify(scope());let p=await FC26Cards.lookup(normalized.barcode);
    if(captured!==JSON.stringify(scope()))throw Error('\u041a\u043b\u0438\u0435\u043d\u0442 \u0438\u043b\u0438 \u0441\u043a\u043b\u0430\u0434 \u0438\u0437\u043c\u0435\u043d\u0451\u043d');
    if(!p.found||!p.sku_id||!p.article||!p.size||p.archived||p.hidden)p=await registerProduct(p);
    const hint=$('receiveSkuHint');if(hint){hint.textContent=[p.article,p.size,p.name].filter(Boolean).join(' \u00b7 ');hint.className='good';}return {...p,duplicated_from:normalized.duplicatedFrom||null};
  }

  async function openProductCardByBarcode(raw){
    const code=normalizeReceivingBarcode(raw).barcode;if(!code)throw Error('У товара нет ШК для открытия карточки');
    // r144: opening a product from a box must be instant. Do not load the full catalog first.
    // The exact local lookup is indexed by barcode and is enough to construct/open the card.
    if(String(catalogClient)!==String(state.clientId)){catalogRows=[];catalogClient=String(state.clientId||'');}
    let row=catalogRows.find(r=>normalizeReceivingBarcode(r.barcode).barcode===code);
    if(!row){
      const p=await api('/warehouse-ui/catalog/lookup?'+new URLSearchParams({client_id:state.clientId,barcode_value:code}),{timeoutMs:10000});
      if(!p?.found)throw Error('Карточка товара не найдена в локальной базе');
      row={id:p.sku_id||('lookup:'+code),card_key:p.card_key||('lookup:'+String(p.sku_id||code)),barcode:p.barcode||code,article:p.article||'',size:p.size||'',name:p.name||'',title:p.name||p.article||code,source:p.wb_found?'WB':'LOCAL',product_id:p.product_id||null,nm_id:p.nm_id||null,brand:p.brand||'',subject_name:p.subject_name||'',photos:p.photos||[]};
      catalogRows.push(row);
    }
    openProductBaseCard(row.card_key||('sku:'+row.id));
  }

  let r145f23ExportBusy=false;
  async function exportStock(k){
    // The FBS export is a fresh server-side read, never a browser-cache stock report.
    if(k==='FBS'){
      if(r145f23ExportBusy){toast('Выгрузка FBS уже формируется');return;}
      if(!state.clientId||!state.warehouseId)throw Error('Выберите клиента и склад');
      const context=key(k),q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId,
        search:$('inventorySearch')?.value||'',sale_tab:window.FCWBSales?.exportFilter?.()||'ALL'});
      r145f23ExportBusy=true;
      try{
        toast('Формируем Excel: свободно, резерв новых и на сборке…');
        const blob=await apiBlob('/r145f23/fbs/export.xlsx?'+q.toString(),{timeoutMs:180000});
        if(context!==key(k))throw Error('Клиент или склад изменён. Повторите выгрузку.');
        saveBlob(blob,'Остатки_WB_FBS.xlsx');
        toast('Excel FBS сформирован. Итоги — на листе «Остатки FBS».');
      }finally{r145f23ExportBusy=false;}
      return;
    }
    const context=key(k);await refresh(k,true);if(context!==key(k))throw Error('Клиент или склад изменён');
    if(errors.has(key(k))||!cache.get(key(k)))throw Error('Сначала обновите остатки: '+(errors.get(key(k))||'нет данных'));
    const q=k==='MAIN'?queries.MAIN:($('inventorySearch')?.value||''),data=rows(k).filter(r=>matched(r,q));
    const headers=k==='MAIN'?['Артикул','Размер','ШК','Основной','В сборке','Свободно','Ячейка','Короб','Секция','Ряд','Место в ряду']:['Артикул','Размер','ШК','WB FBS физически','Ячейка','Короб','Секция','Ряд','Место в ряду'];
    const values=data.flatMap(r=>r.locations.map(p=>k==='MAIN'?[r.article,r.size,r.barcode,p.quantity,p.held,p.free,p.location,p.container,p.route_section||'',p.route_row||'',p.route_position||'']:[r.article,r.size,r.barcode,p.quantity,p.location,p.container,p.route_section||'',p.route_row||'',p.route_position||'']));
    const filename=k==='MAIN'?'Основной_склад.xlsx':'Остатки_WB_FBS.xlsx';
    const blob=await apiBlob('/inventory/export.xlsx',{method:'POST',body:JSON.stringify({headers,rows:values,chrt_headers:[],chrt_rows:[],notes:[name(k)+' · '+time(cache.get(key(k)).data.generated_at),k==='FBS'?'Физическое размещение товара WB FBS. Доступность для продажи уточняется отдельной сверкой WB.':''],filename})});
    if(context!==key(k))throw Error('Клиент или склад изменён');saveBlob(blob,filename);
  }

  FC26Cards.configure({scope:()=>({client_id:state.clientId,warehouse_id:state.warehouseId}),onSaved:p=>{
    if(String(p.client_id)!==String(state.clientId))return;
    const sku={id:p.sku_id,client_id:p.client_id,barcode:p.barcode,article:p.article,size:p.size,name:p.name,is_active:true};
    const old=state.skus?.find(x=>String(x.id)===String(p.sku_id));if(old)Object.assign(old,sku);else if(Array.isArray(state.skus))state.skus.push(sku);
    if(String(catalogClient)!==String(p.client_id)){catalogRows=[];catalogClient=p.client_id;}
    const prev=catalogRows.find(x=>String(x.id)===String(p.sku_id));const row={...prev,id:p.sku_id,barcode:p.barcode,article:p.article,size:p.size,name:p.name,title:p.name,category:p.category,card_key:prev?.source==='WB'?prev.card_key:p.card_key,source:prev?.source||'LOCAL',photos:prev?.photos||[],manual_edited:true};
    catalogRows=catalogRows.filter(x=>String(x.id)!==String(p.sku_id));catalogRows.push(row);catalogCacheTs=0;
    try{persistProductBaseCache(p.client_id,catalogRows,catalogHiddenCount);if($('r65CatalogGrid'))renderCatalog();}catch(e){console.warn('Local card cache refresh',e);}
  }});
  let catalogRows=[],catalogClient='',catalogPage=1,catalogRequest=0,catalogHiddenCount=0,catalogCacheTs=0,catalogRefreshFlight=null;
  const PRODUCT_BASE_CACHE_DB='fulfillcore-product-base-r114',PRODUCT_BASE_CACHE_STORE='catalogs',PRODUCT_BASE_CACHE_FRESH_MS=30*60*1000,PRODUCT_BASE_PREVIEW_LIMIT=1200;
  function productBasePreviewKey(clientId){return `fc_product_base_preview_r114_${clientId||'none'}`;}
  function compactProductBaseRow(r){return {id:r.id,card_key:r.card_key,barcode:r.barcode,article:r.article,size:r.size,name:r.name,title:r.title,source:r.source,product_id:r.product_id,nm_id:r.nm_id,brand:r.brand,subject_name:r.subject_name,photos:(r.photos||[]).slice(0,1)};}
  function saveProductBasePreview(clientId,rows,hidden,ts=Date.now()){try{localStorage.setItem(productBasePreviewKey(clientId),JSON.stringify({v:1,ts,hidden:Number(hidden||0),rows:(rows||[]).slice(0,PRODUCT_BASE_PREVIEW_LIMIT).map(compactProductBaseRow)}));}catch(_){}}
  function restoreProductBasePreview(clientId){try{const raw=localStorage.getItem(productBasePreviewKey(clientId));if(!raw)return false;const c=JSON.parse(raw);if(!Array.isArray(c?.rows)||!c.rows.length)return false;catalogRows=c.rows;catalogHiddenCount=Number(c.hidden||0);catalogCacheTs=Number(c.ts||0);return true;}catch(_){return false;}}
  function openProductBaseCacheDb(){return new Promise(resolve=>{if(!window.indexedDB){resolve(null);return;}let req;try{req=indexedDB.open(PRODUCT_BASE_CACHE_DB,1);}catch(_){resolve(null);return;}req.onupgradeneeded=()=>{const db=req.result;if(!db.objectStoreNames.contains(PRODUCT_BASE_CACHE_STORE))db.createObjectStore(PRODUCT_BASE_CACHE_STORE,{keyPath:'client_id'});};req.onsuccess=()=>resolve(req.result);req.onerror=()=>resolve(null);req.onblocked=()=>resolve(null);});}
  async function readProductBaseCache(clientId){const db=await openProductBaseCacheDb();if(!db)return null;return await new Promise(resolve=>{try{const tx=db.transaction(PRODUCT_BASE_CACHE_STORE,'readonly'),req=tx.objectStore(PRODUCT_BASE_CACHE_STORE).get(String(clientId));req.onsuccess=()=>resolve(req.result||null);req.onerror=()=>resolve(null);tx.oncomplete=()=>db.close();tx.onabort=()=>{try{db.close();}catch(_){}};}catch(_){try{db.close();}catch(__){}resolve(null);}});}
  async function writeProductBaseCache(clientId,rows,hidden,ts=Date.now()){const db=await openProductBaseCacheDb();if(!db)return;await new Promise(resolve=>{try{const tx=db.transaction(PRODUCT_BASE_CACHE_STORE,'readwrite');tx.objectStore(PRODUCT_BASE_CACHE_STORE).put({client_id:String(clientId),ts,hidden:Number(hidden||0),rows:(rows||[]).map(compactProductBaseRow)});tx.oncomplete=()=>{db.close();resolve();};tx.onerror=tx.onabort=()=>{try{db.close();}catch(_){}resolve();};}catch(_){try{db.close();}catch(__){}resolve();}});}
  function persistProductBaseCache(clientId,rows,hidden){const ts=Date.now();catalogCacheTs=ts;saveProductBasePreview(clientId,rows,hidden,ts);writeProductBaseCache(clientId,rows,hidden,ts).catch(()=>{});}
  async function restoreFullProductBaseCache(clientId,request){const cached=await readProductBaseCache(clientId);if(request!==catalogRequest||String(clientId)!==String(state.clientId)||!cached?.rows?.length)return false;if(Number(cached.ts||0)<catalogCacheTs&&catalogRows.length)return true;catalogRows=cached.rows;catalogHiddenCount=Number(cached.hidden||0);catalogCacheTs=Number(cached.ts||0);renderCatalog();$('r65CatalogStatus').textContent+=` · показано из локального кэша${catalogCacheTs?' '+new Date(catalogCacheTs).toLocaleTimeString('ru-RU',{timeZone:'Europe/Moscow',hour:'2-digit',minute:'2-digit'}):''}`;return true;}
  function catalogCards(rows){const map=new Map();for(const r of rows){const key=r.card_key||('sku:'+r.id);let c=map.get(key);if(!c){c={card_key:key,source:r.source,product_id:r.product_id,nm_id:r.nm_id,title:r.title||r.name||r.article||'Без названия',article:r.article||'',brand:r.brand||'',subject_name:r.subject_name||'',photos:r.photos||[],barcodes:[],sizes:[],rows:[]};map.set(key,c);}c.rows.push(r);if(r.barcode&&!c.barcodes.includes(r.barcode))c.barcodes.push(r.barcode);if(r.size&&!c.sizes.includes(r.size))c.sizes.push(r.size);}return [...map.values()];}
  function productBaseCard(cardKey){return catalogCards(catalogRows).find(c=>String(c.card_key)===String(cardKey))||null;}
  function localLabelCount(skuId){const input=$('wbProductModalBody')?.querySelector(`[data-local-product-label-qty="${CSS.escape(String(skuId))}"]`);const n=Math.floor(Number(input?.value||1));return Math.min(200,Math.max(1,Number.isFinite(n)?n:1));}
  async function printProductBaseLocalLabel(skuId,count,{browser=false}={}){
    if(!state.clientId)throw new Error('Выберите клиента');
    const qty=Math.min(200,Math.max(1,Math.floor(Number(count||1))));
    if(browser){
      const blob=await apiBlob('/warehouse-ui/catalog/local-label.pdf',{method:'POST',timeoutMs:90000,body:JSON.stringify({client_id:state.clientId,sku_id:skuId,count:qty})});
      printBlob(blob,`FulfillCore_product_${skuId}_58x40.pdf`);toast(`Открыта браузерная печать 58×40: ${qty} шт.`);return;
    }
    const ps=wbPrintSettings();if(!ps.station)throw new Error('Укажите рабочее место Print Agent');
    const r=await api('/warehouse-ui/catalog/local-label/queue',{method:'POST',timeoutMs:90000,body:JSON.stringify({client_id:state.clientId,sku_id:skuId,count:qty,print_station:ps.station})});
    toast(`ШК товара отправлен в автопечать ${r.print_station}: ${r.labels_queued} шт. · 58×40`);
  }
  function openProductBaseCard(cardKey){
    const c=productBaseCard(cardKey);if(!c)throw new Error('Карточка товара не найдена в локальной базе');
    const photo=firstPhoto(c),source=c.source==='WB'?'WB → База товаров':'Добавлен вручную';
    const rows=(c.rows||[]).slice().sort((a,b)=>String(a.size||'').localeCompare(String(b.size||''),'ru',{numeric:true}));
    const labels=rows.map(r=>`<div class="barcode-row r136-local-barcode-row"><span><b>${esc(r.size||'Без размера')}</b><small>${esc(r.barcode||'—')}</small>${r.name&&r.name!==c.title?`<small>${esc(r.name)}</small>`:''}</span><div class="button-row r136-local-print-actions"><button type="button" class="mini secondary" data-fc26-catalog-edit="${esc(r.barcode)}">\u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u043a\u0430\u0440\u0442\u043e\u0447\u043a\u0443</button><label class="r136-label-qty"><small>Кол-во</small><input type="number" min="1" max="200" step="1" value="1" data-local-product-label-qty="${esc(r.id)}"></label><button class="mini primary" type="button" data-local-product-label-agent="${esc(r.id)}">Автопечать 58×40</button><button class="mini secondary" type="button" data-local-product-label-browser="${esc(r.id)}">Браузер</button></div></div>`).join('');
    $('wbProductModalBody').innerHTML=`<div class="wb-product-editor r136-local-product-card"><div class="wb-product-gallery"><div class="product-main-photo">${photo?`<img src="${esc(photo)}" alt=""/>`:'<div class="product-placeholder large">Нет фото</div>'}</div></div><div class="wb-product-fields"><div class="product-detail-head"><div><span class="kicker">ЛОКАЛЬНАЯ КАРТОЧКА FULFILLCORE</span><h2>${esc(c.title||c.article||'Без названия')}</h2></div><div class="button-row">${c.product_id?`<button class="secondary" type="button" data-local-product-open-wb="${esc(c.product_id)}">Полная WB-карточка</button>`:''}</div></div><div class="product-form-grid"><div><small>Артикул</small><b>${esc(c.article||'—')}</b></div><div><small>Источник</small><b>${esc(source)}</b></div><div><small>Размеров</small><b>${c.sizes.length}</b></div><div><small>Штрихкодов</small><b>${c.barcodes.length}</b></div></div><div class="pvz-note"><b>Печать из локальной базы.</b> «Автопечать 58×40» отправляет этикетку напрямую в выбранный Print Agent и не требует кабинета WB. Кнопка «Браузер» остаётся резервным способом.</div><div class="product-sizes-head"><h3>Размеры и штрихкоды</h3><span class="muted">выберите количество для каждого ШК</span></div><div class="barcode-list">${labels||'<span class="muted">Штрихкодов нет</span>'}</div></div></div>`;
    if(!document.getElementById('r136LocalProductStyles')){const st=document.createElement('style');st.id='r136LocalProductStyles';st.textContent='.r136-local-print-actions{align-items:end}.r136-label-qty{display:grid;gap:3px;min-width:76px}.r136-label-qty small{font-size:10px;color:#667085}.r136-label-qty input{width:76px;padding:7px 8px;border:1px solid #d0d5dd;border-radius:8px;font:inherit}.r136-local-barcode-row{gap:12px;align-items:center}@media(max-width:700px){.r136-local-print-actions{display:grid;grid-template-columns:80px 1fr 1fr}}';document.head.appendChild(st);}
    $('wbProductModal').classList.remove('hidden');
  }
  function bindProductBaseLocalModal(){
    const modal=$('wbProductModal');if(!modal||modal.dataset.r136LocalBound)return;modal.dataset.r136LocalBound='1';
    modal.addEventListener('click',async e=>{
      const edit=e.target.closest('[data-fc26-catalog-edit]');if(edit){try{await FC26Cards.open(edit.dataset.fc26CatalogEdit);$('wbProductModal').classList.add('hidden');toast('\u041a\u0430\u0440\u0442\u043e\u0447\u043a\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0430');}catch(err){toast(err.message,true);}return;}
      const wb=e.target.closest('[data-local-product-open-wb]');if(wb){try{await openWbProduct(wb.dataset.localProductOpenWb);}catch(err){toast(err.message,true);}return;}
      const agent=e.target.closest('[data-local-product-label-agent]'),browser=e.target.closest('[data-local-product-label-browser]'),btn=agent||browser;if(!btn)return;
      const skuId=agent?.dataset.localProductLabelAgent||browser?.dataset.localProductLabelBrowser;const qty=localLabelCount(skuId);btn.disabled=true;
      try{await printProductBaseLocalLabel(skuId,qty,{browser:Boolean(browser)});}catch(err){toast(err.message,true);}finally{btn.disabled=false;}
    });
  }
  function renderCatalog(){
    const q=($('r65CatalogSearch')?.value||'').trim().toLowerCase(),all=catalogCards(catalogRows),list=q?all.filter(c=>[c.nm_id,c.article,c.title,c.brand,c.subject_name,c.barcodes.join(' '),c.sizes.join(' ')].join(' ').toLowerCase().includes(q)):all;
    catalogPage=Math.min(catalogPage,Math.max(1,Math.ceil(list.length/60)));$('r65CatalogStatus').textContent=`База товаров: ${all.length} карточек · ${catalogRows.length} ШК · найдено ${list.length}${catalogHiddenCount?` · удалено локально ${catalogHiddenCount}`:''}. Поиск выполняется мгновенно по локальной базе.`;
    const grid=$('r65CatalogGrid');if(!grid)return;
    grid.innerHTML=list.slice((catalogPage-1)*60,catalogPage*60).map(c=>{const img=firstPhoto(c),tag=c.source==='WB'?`WB ${esc(c.nm_id||'')}`:'FulfillCore',source=c.source==='WB'?'WB → База товаров':'Добавлен вручную';return `<article class="product-catalog-card"><button class="product-image-button" type="button" data-product-base-open-card="${esc(c.card_key)}">${img?`<img src="${esc(img)}" alt="" loading="lazy"/>`:'<div class="product-placeholder">Нет фото</div>'}</button><div class="product-catalog-info"><div class="product-card-topline"><span class="tag">${tag}</span>${c.subject_name?`<span class="muted">${esc(c.subject_name)}</span>`:''}</div><button type="button" class="product-title-link" data-product-base-open-card="${esc(c.card_key)}">${esc(c.title||'Без названия')}</button><div class="product-article"><small>Артикул</small><b>${esc(c.article||'—')}</b></div><div class="product-meta-row"><span><small>Источник</small><b>${source}</b></span><span><small>Размеры</small><b>${esc(c.sizes.join(', ')||'—')}</b></span><span><small>ШК</small><b>${c.barcodes.length}</b></span></div><div class="product-stock-line"><span><small>Штрихкоды</small><b>${esc(c.barcodes.slice(0,4).join(', ')||'—')}${c.barcodes.length>4?' …':''}</b></span><div class="button-row"><button class="primary mini" type="button" data-product-base-open-card="${esc(c.card_key)}">Открыть карточку</button><button class="danger mini" type="button" data-product-base-hide="${esc(c.card_key)}">Удалить из базы</button></div></div></div></article>`;}).join('')||'<div class="empty-state">Товары не найдены. Нажмите «Загрузить из WB» или добавьте новый ШК вручную.</div>';
    $('r65CatalogPager').innerHTML=`<button class="secondary" data-cat-prev ${catalogPage<=1?'disabled':''}>Назад</button><span>${catalogPage} / ${Math.max(1,Math.ceil(list.length/60))}</span><button class="secondary" data-cat-next ${catalogPage*60>=list.length?'disabled':''}>Далее</button>`;
    $('r65CatalogPager').querySelector('[data-cat-prev]').onclick=()=>{catalogPage--;renderCatalog();};$('r65CatalogPager').querySelector('[data-cat-next]').onclick=()=>{catalogPage++;renderCatalog();};
  }
  async function fetchProductBaseFresh(clientId,request,{force=false}={}){
    if(catalogRefreshFlight&&!force)return catalogRefreshFlight;
    const run=(async()=>{try{
      if(force&&catalogRows.length){$('r65CatalogStatus').textContent='Обновляем локальную Базу товаров в фоне… текущие карточки остаются доступны.';}
      const r=await api('/r145f26/catalog/list?'+new URLSearchParams({client_id:clientId,warehouse_id:state.warehouseId,limit:'20000'}),{timeoutMs:45000});
      if(request!==catalogRequest||String(clientId)!==String(state.clientId))return false;
      catalogRows=r.rows||[];catalogHiddenCount=Number(r.hidden_count||0);persistProductBaseCache(clientId,catalogRows,catalogHiddenCount);renderCatalog();
      if(r.truncated)toast('В Базе товаров больше 20 000 ШК. Уточните поиск.',true);
      if(r.ambiguous_wb_barcodes)toast(`Не показано конфликтующих ШК WB: ${r.ambiguous_wb_barcodes}. Такие ШК требуют проверки карточек.`,true);
      return true;
    }catch(e){
      if(request===catalogRequest&&String(clientId)===String(state.clientId)&&!catalogRows.length)$('r65CatalogStatus').textContent='Не удалось загрузить Базу товаров: '+e.message;
      else console.warn('Product Base background refresh:',e);
      return false;
    }finally{if(catalogRefreshFlight===run)catalogRefreshFlight=null;}
    })();catalogRefreshFlight=run;return run;
  }
  async function loadCatalog(force=false){
    const c=state.clientId,request=++catalogRequest;if(!c)return;
    const switched=String(catalogClient)!==String(c);
    if(switched){catalogRows=[];catalogClient=c;catalogPage=1;catalogHiddenCount=0;catalogCacheTs=0;}
    if(force){if(!catalogRows.length)$('r65CatalogStatus').textContent='Загружаем локальную Базу товаров…';await fetchProductBaseFresh(c,request,{force:true});return;}
    if(catalogRows.length){renderCatalog();if(Date.now()-catalogCacheTs>PRODUCT_BASE_CACHE_FRESH_MS)setTimeout(()=>fetchProductBaseFresh(c,request).catch(()=>{}),0);return;}
    const preview=restoreProductBasePreview(c);
    if(preview){renderCatalog();$('r65CatalogStatus').textContent+=` · быстрый локальный кэш; полный индекс открывается без ожидания`; }
    const fullCache=await restoreFullProductBaseCache(c,request);
    if(request!==catalogRequest||String(c)!==String(state.clientId))return;
    if(preview||fullCache){
      if(!fullCache||Date.now()-catalogCacheTs>PRODUCT_BASE_CACHE_FRESH_MS)setTimeout(()=>fetchProductBaseFresh(c,request).catch(()=>{}),0);
      return;
    }
    $('r65CatalogStatus').textContent='Первое открытие локальной Базы товаров — загружаем первый блок…';
    try{
      const quick=await api('/r145f26/catalog/list?'+new URLSearchParams({client_id:c,warehouse_id:state.warehouseId,limit:'600'}),{timeoutMs:30000});
      if(request!==catalogRequest||String(c)!==String(state.clientId))return;
      catalogRows=quick.rows||[];catalogHiddenCount=Number(quick.hidden_count||0);catalogCacheTs=Date.now();saveProductBasePreview(c,catalogRows,catalogHiddenCount,catalogCacheTs);renderCatalog();
      $('r65CatalogStatus').textContent=`Показан первый локальный блок: ${catalogCards(catalogRows).length} карточек · полный индекс догружается в фоне. Следующие входы будут моментальными.`;
      setTimeout(()=>fetchProductBaseFresh(c,request,{force:true}).catch(()=>{}),0);
    }catch(e){
      if(request===catalogRequest)$('r65CatalogStatus').textContent='Не удалось загрузить Базу товаров: '+e.message;
    }
  }
  function showProductBaseExcelResult(r){
    const d=dialog('Импорт Excel в Базу товаров',''),root=d.querySelector('.r65-dialog-body');
    const errors=(r.errors||[]).slice(0,25);
    root.innerHTML=`<div class="r65-stats"><b>Добавлено новых ШК: ${Number(r.created||0)}</b></div><p>Уже были в базе и не изменялись: <b>${Number(r.skipped_existing||0)}</b> · дубликаты внутри файла: <b>${Number(r.skipped_duplicate_in_file||0)}</b> · некорректные строки: <b>${Number(r.invalid_rows||0)}</b>.</p><p class="muted">Остатки, резервы и WB не менялись. Когда позже импортируется карточка WB с тем же ШК, локальная карточка автоматически покажет доступные данные WB — фото, название, описание и характеристики.</p>${errors.length?`<div class="warn-box"><b>Строки, которые требуют внимания:</b>${errors.map(x=>`<small class="subline">Строка ${esc(x.row||'—')} · ШК ${esc(x.barcode||'—')} · ${esc(x.error||'Ошибка')}</small>`).join('')}${r.errors_truncated?'<small class="subline">Показаны первые 25 ошибок.</small>':''}</div>`:''}<div class="r65-form-actions"><button type="button" class="primary" data-close-import-result>Готово</button></div>`;
    root.querySelector('[data-close-import-result]').onclick=()=>closeDialog();
  }
  async function importProductBaseExcel(file){
    const c=state.clientId;if(!c)throw Error('Выберите клиента');if(!file)return;
    if(!/\.xlsx$/i.test(file.name||''))throw Error('Нужен файл .xlsx по шаблону FulfillCore');
    const clientName=state.clients.find(x=>String(x.id)===String(c))?.name||'выбранного клиента';
    if(!confirm(`Импортировать «${file.name}» в локальную Базу товаров клиента «${clientName}»?\n\nНовые ШК будут добавлены с артикулом и размером. Если ШК уже есть в базе, он останется без изменений. Остатки, резервы и WB не изменяются.`))return;
    const btn=$('r115ProductBaseImport'),input=$('r115ProductBaseFile');if(btn){btn.disabled=true;btn.textContent='Импортируем Excel…';}
    try{
      const form=new FormData();form.append('file',file,file.name);
      const r=await api('/warehouse-ui/catalog/product-base/import-xlsx?'+new URLSearchParams({client_id:c}),{method:'POST',body:form,timeoutMs:120000});
      await loadCatalog(true);
      toast(`Excel → База товаров: добавлено ${r.created||0}, уже было ${r.skipped_existing||0}${r.invalid_rows?` · ошибок ${r.invalid_rows}`:''}.`,Boolean(r.invalid_rows));
      showProductBaseExcelResult(r);
    }finally{if(input)input.value='';if(btn){btn.disabled=false;btn.textContent='Импорт Excel';}}
  }
  async function hideProductBaseCard(cardKey){if(!cardKey)return;if(!confirm('Удалить карточку из локальной «Базы товаров»?\n\nОстатки, история склада и «Карточки WB» не изменятся. При необходимости товар можно снова добавить из карточек WB.'))return;await api('/warehouse-ui/catalog/product-base/hide?'+new URLSearchParams({client_id:state.clientId,card_key:cardKey}),{method:'POST'});catalogRows=catalogRows.filter(r=>String(r.card_key)!==String(cardKey));catalogHiddenCount++;persistProductBaseCache(state.clientId,catalogRows,catalogHiddenCount);renderCatalog();toast('Карточка удалена из Базы товаров. Остатки и история не изменены.');}
  async function materializeWbCardsToProductBase({confirmLocal=true}={}){const c=state.clientId;if(!c)throw Error('Выберите клиента');const btn=$('r102ImportWbCardsToBase');if(confirmLocal&&!confirm('Добавить в «Базу товаров» ШК, артикулы и размеры из уже сохранённых локально «Карточек WB»?\n\nОстатки, резервы и заказы не изменятся.'))return null;if(btn){btn.disabled=true;btn.textContent='Переносим карточки…';}try{const r=await api('/warehouse-ui/catalog/product-base/import-wb-cards?'+new URLSearchParams({client_id:c}),{method:'POST',timeoutMs:120000});if(!r.cards){toast(r.message||'Сначала загрузите Карточки WB',true);return r;}await loadCatalog(true);const skipped=Number(r.skipped_conflicts||0)+Number(r.skipped_existing_conflicts||0)+Number(r.skipped_archived||0);toast(`База товаров обновлена: добавлено ${r.created||0}, дополнено ${r.enriched||0}, уже было ${r.already_present||0}${skipped?` · пропущено ${skipped}`:''}.`);return r;}finally{if(btn){btn.disabled=false;btn.textContent='Из локальных карточек';}}}
  async function syncWbCardsToProductBase(){const c=state.clientId;if(!c)throw Error('Выберите клиента');const cred=wbProductCredential();if(!cred)throw Error('Подключите кабинет WB в Личном кабинете');if(!confirm('Загрузить новые и изменённые карточки из кабинета WB и добавить их ШК / артикулы / размеры в «Базу товаров»?\n\nОстатки, резервы и заказы не изменятся.'))return;const btn=$('r102SyncWbCardsToBase');if(btn){btn.disabled=true;btn.textContent='Загружаем из WB…';}try{const sync=await api(`/marketplaces/wb/products/import-changes?credential_id=${encodeURIComponent(cred)}`,{method:'POST',timeoutMs:180000});state.wbProductIndexClient='';await loadWbProducts(true);const r=await materializeWbCardsToProductBase({confirmLocal:false});if(r?.cards){toast(`WB → База товаров готово: карточек ${r.cards}, новых ШК ${r.created||0}, дополнено ${r.enriched||0}.`);}if(sync?.truncated)toast('В WB ещё есть изменения. Нажмите «Загрузить из WB» ещё раз, чтобы продолжить с сохранённого курсора.');}finally{if(btn){btn.disabled=false;btn.textContent='Загрузить из WB';}}}
  function page(id,html){const p=document.createElement('section');p.id='page-'+id;p.className='page r65-page';p.innerHTML=html;document.querySelector('.page').parentElement.appendChild(p);return p;}
  function setup(){if(installed||!$('page-inventory'))return;
    const css=document.createElement('link');css.rel='stylesheet';css.href='/static/stock-rooms.css?v=r66';document.head.appendChild(css);
    page('stockhome',`<div class="r65-toolbar"><h2>Выберите склад</h2></div><div class="r65-choice"><div class="panel"><span class="r65-tag">ОСНОВНОЙ</span><h2>Основной склад</h2><p>Приёмка, хранение и складская сборка. Остатки по ячейкам и коробам, свободный товар и количество в сборке.</p><button class="primary" data-r65-go="mainstock">Открыть остатки</button><button class="secondary" data-r65-go="warehousework">Сборочные листы Основного склада</button></div><div class="panel"><span class="r65-tag fbs">WB FBS</span><h2>Склад WB FBS</h2><p>Товар, выделенный для FBS. Резервы, остатки WB и отдельная загрузка сборочного листа FBS.</p><button class="primary" data-r65-go="inventoryfbs">Открыть остатки</button><button class="secondary" data-r65-go="fbspicks">Сборочные листы WB FBS</button></div></div>`);
    page('mainstock',`<div class="panel"><div class="r65-toolbar"><button class="secondary" data-r65-go="stockhome">← Склады</button><h2>Основной склад</h2><button class="secondary" id="r65MainRefresh">Обновить</button></div><div class="r65-toolbar"><input id="r65MainSearch" aria-label="Поиск на Основном складе" placeholder="ШК, артикул, размер, ячейка или короб"><button class="primary" data-r65-go="warehousework">Загрузить сборочный лист</button><button class="secondary" data-r65-transfer>Перевести по Excel</button><button class="secondary" id="r65MainExport">Excel</button></div><div class="r65-toolbar r86-main-bulk"><button type="button" class="secondary" id="r86MainSelectFound">Выбрать найденные</button><button type="button" class="secondary" id="r86MainClearSelection">Снять выбор</button><button type="button" class="primary" id="r108MainTransferSelected" disabled>Выбранные → FBS</button><button type="button" class="danger" id="r86MainDeleteSelected" disabled>Удалить выбранные</button><button type="button" class="danger" id="r86MainDeleteFound">Удалить все найденные</button><span id="r86MainSelection" class="r65-stats"></span></div><div class="r65-toolbar" style="padding:14px;border:1px solid #e4e7ec;border-radius:12px;background:#f8fafc"><b>Перевести весь адрес</b><input id="r108MainLocation" placeholder="Код/QR стеллажа, полки, палеты или ячейки" autocomplete="off"><button type="button" class="secondary" id="r108MainLocationTransfer">Весь адрес → FBS</button><small class="muted">Берётся выбранный адрес и все его дочерние адреса. Товар в активной сборке остаётся на Основном.</small></div><p id="r65MainStatus" class="r65-status" role="status"></p><p id="r65MainTotal" class="r65-stats"></p><table class="r65-table"><thead><tr><th class="r86-main-select" style="width:52px"><input type="checkbox" id="r86MainSelectAll" aria-label="Выбрать все найденные товары"></th><th>Товар</th><th>На Основном</th><th>Свободно</th><th>Размещение</th></tr></thead><tbody id="r65MainBody"></tbody></table><div id="r65MainPager" class="r65-pager"></div></div>`);
    const fbs=page('fbspicks',`<div class="panel"><div class="r65-toolbar"><button class="secondary" data-r65-go="inventoryfbs">← Остатки WB FBS</button><h2>Сборочные листы · WB FBS</h2></div><p>Загрузите Excel для товара, уже выделенного на WB FBS. После подтверждения существующий механизм FBS выполняет списание и формирует отчёт.</p><p class="muted">Для адресного задания сборщику используйте «Сборочные листы Основного склада».</p><div id="r65FbsPickActions" class="r65-toolbar"></div></div>`);
    titles.stockhome='Остатки';titles.mainstock='Основной склад';titles.inventory='WB FBS';titles.fbspicks='Сборочные листы · WB FBS';titles.warehousework='Сборочные листы · Основной склад';
    const inv=$('page-inventory');inv.classList.add('r65-page');const head=document.createElement('div');head.className='r65-toolbar';head.innerHTML='<button class="secondary" data-r65-go="stockhome">← Склады</button><h2>WB FBS</h2><button class="primary" data-r65-go="fbspicks">Загрузить сборочный лист FBS</button><button class="secondary" data-r65-transfer>Перевести с Основного склада</button>';inv.prepend(head);
    const stat=document.createElement('p');stat.id='r65FbsStatus';stat.className='r65-status';stat.setAttribute('role','status');head.after(stat);const addressTransfer=document.createElement('div');addressTransfer.className='r65-toolbar';addressTransfer.style.cssText='padding:14px;border:1px solid #e4e7ec;border-radius:12px;background:#f8fafc';addressTransfer.innerHTML='<b>Перевести весь адрес</b><input id="r108FbsLocation" placeholder="Код/QR стеллажа, полки, палеты или ячейки" autocomplete="off"><button type="button" class="secondary" id="r108FbsLocationTransfer">Весь адрес → Основной</button><small class="muted">Резерв заказов WB остаётся в FBS; переводится только свободный FBS.</small>';stat.after(addressTransfer);
    const table=$('inventoryBody').closest('table');table.className='r65-table';table.querySelector('thead').innerHTML='<tr><th><input type="checkbox" aria-label="Выбрать все найденные товары FBS" data-inventory-select-visible></th><th>Товар</th><th>Физически на складе FBS</th><th>Свободно / WB</th><th>Размещение</th></tr>';
    const pager=document.createElement('div');pager.id='r65FbsPager';pager.className='r65-pager';table.parentElement.after(pager);
    const availability=$('inventoryAvailability');if(availability)availability.closest('label').hidden=true;
    const zone=$('inventoryStockZone');if(zone){zone.value='FBS';zone.closest('label').hidden=true;}
    const bar=$('inventoryBulkToolbar');if(bar){
      const actions=bar.querySelector('.inventory-bulk-actions'),advanced=document.createElement('details');advanced.className='r65-advanced';advanced.innerHTML='<summary>Управление продажами WB</summary><div class="r65-toolbar"></div>';bar.appendChild(advanced);
      for(const attr of ['data-inventory-pause-all','data-inventory-resume-all','data-inventory-pause-wb','data-inventory-resume-wb','data-inventory-wb-errors']){const b=bar.querySelector('['+attr+']');if(b)advanced.lastElementChild.appendChild(b);}
      // r79: reset/move FBS controls stay in the main row, immediately after selection.
      for(const attr of ['data-inventory-add-fbs','data-inventory-undo-last'])bar.querySelector('['+attr+']')?.remove();
      const hint=bar.querySelector('.inventory-bulk-summary small');if(hint)hint.textContent='FBS можно очистить до 0 и загрузить заново. «Удалить из FBS» физически удаляет только FBS, не трогая Основной. «В Основной + стоп WB» ничего физически не удаляет: FBS становится 0, товар остаётся в Основном складе, продажи WB останавливаются. Перед обеими операциями автоматически сохраняется контрольный Excel.';
      for(const attr of ['data-external-pick-upload','data-external-pick-template','data-external-pick-cancel-last','data-external-pick-file']){const b=bar.querySelector('['+attr+']');if(b)$('r65FbsPickActions').appendChild(b);}
      const select=actions?.querySelector('[data-inventory-select-visible-btn]');if(select)select.textContent='Выбрать найденные';
      const clear=actions?.querySelector('[data-inventory-clear-selection]');if(clear)clear.textContent='Снять выбор';
      const del=actions?.querySelector('[data-inventory-bulk-delete]'),delAll=actions?.querySelector('[data-inventory-delete-all]'),moveMain=actions?.querySelector('[data-inventory-move-main]'),moveMainAll=actions?.querySelector('[data-inventory-move-main-all]');
      if(clear){let anchor=clear;for(const b of [del,delAll,moveMain,moveMainAll])if(b){anchor.after(b);anchor=b;}}
      const sync=actions?.querySelector('[data-inventory-refresh-wb]');if(sync)sync.textContent='Сверить WB';if(actions&&!$('r108FbsTransferSelected')){const move=document.createElement('button');move.type='button';move.id='r108FbsTransferSelected';move.className='primary';move.textContent='Выбранные → Основной';move.disabled=true;actions.prepend(move);}
    }
    const work=$('page-warehousework');if(work){const first=work.querySelector('.panel');if(first)first.hidden=true;const nav=document.querySelector('[data-page="warehousework"]');if(nav)nav.textContent='Сборка · Основной';const h=work.querySelector('#r62PickNumber')?.closest('.panel');if(h){work.prepend(h);const title=h.querySelector('h2');if(title)title.textContent='Сборочные листы Основного склада';}const back=document.createElement('div');back.className='r65-toolbar';back.innerHTML='<button class="secondary" data-r65-go="mainstock">← Основной склад</button><button class="secondary" data-r65-go="fbspicks">Сборочные листы WB FBS</button>';work.prepend(back);const input=$('r62PickFile');if(input){input.hidden=false;input.closest('label').className='';input.closest('label').firstChild.textContent='Сборочный лист Основного склада';}}
    $('r65MainSearch').oninput=e=>{queries.MAIN=e.target.value;pages.MAIN=1;renderMain();};$('r65MainRefresh').onclick=()=>refresh('MAIN',true);
    const selectMainRows=list=>{for(const r of list)selectedMainBarcodes.add(mainSelectableCode(r));renderMain();};
    $('r86MainSelectFound').onclick=()=>selectMainRows(mainFiltered());$('r86MainClearSelection').onclick=()=>{selectedMainBarcodes.clear();renderMain();};
    $('r86MainSelectAll').onchange=e=>{const list=mainFiltered();if(e.target.checked)for(const r of list)selectedMainBarcodes.add(mainSelectableCode(r));else for(const r of list)selectedMainBarcodes.delete(mainSelectableCode(r));renderMain();};
    const openMainDelete=list=>{if(!list.length){toast('Выберите товар для удаления',true);return;}window.FCStockActions?.bulkOpen('MAIN',list.map(r=>r.barcode).filter(Boolean),list);};
    $('r86MainDeleteSelected').onclick=()=>openMainDelete(mainSelectedRows());
    $('r86MainDeleteFound').onclick=()=>{const list=mainFiltered();if(!list.length){toast('На экране нет доступного товара для удаления',true);return;}if(!confirm(`Удалить весь свободный остаток найденных позиций: ${list.length}?`))return;openMainDelete(list);};
    $('r108MainTransferSelected').onclick=()=>r108Transfer('MAIN','FBS',{barcodes:mainSelectedRows().map(r=>r.barcode)}).catch(e=>toast(e.message,true));
    $('r108MainLocationTransfer').onclick=()=>r108Transfer('MAIN','FBS',{locationQuery:$('r108MainLocation').value}).catch(e=>toast(e.message,true));
    $('r108FbsTransferSelected').onclick=()=>r108Transfer('FBS','MAIN',{barcodes:[...selectedInventoryBarcodes]}).catch(e=>toast(e.message,true));
    $('r108FbsLocationTransfer').onclick=()=>r108Transfer('FBS','MAIN',{locationQuery:$('r108FbsLocation').value}).catch(e=>toast(e.message,true));
    $('r65MainExport').onclick=()=>exportStock('MAIN').catch(e=>toast(e.message,true));
    page('localcatalog','<div class="panel"><div class="r65-toolbar"><button class="secondary" data-r65-go="stockhome">← Склады</button><h2>База товаров</h2><button class="secondary" data-r65-go="products">← Карточки WB</button><button class="secondary" id="r65CatalogRefresh">Перечитать локально</button></div><p>Рабочая локальная база товара в формате карточек: фото, артикул, размеры и ШК. Минимальные карточки можно загрузить из Excel только по ШК / артикулу / размеру; затем совпавший ШК автоматически связывается с импортированной карточкой WB и получает её фото, название, описание и другие доступные данные.</p><div class="r65-toolbar"><input id="r65CatalogSearch" aria-label="Поиск по Базе товаров" placeholder="ШК, артикул, размер или название"><button class="primary" id="r102SyncWbCardsToBase">Загрузить из WB</button><button class="secondary" id="r102ImportWbCardsToBase">Из локальных карточек</button><input id="r115ProductBaseFile" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden><button class="secondary" id="r115ProductBaseImport">Импорт Excel</button><a class="secondary" href="/warehouse-ui/catalog/product-base/import-template.xlsx" download="FulfillCore_Product_Base_Import.xlsx">Скачать шаблон</a><button class="secondary" id="r65CatalogNew">Добавить по ШК</button></div><p class="muted">Excel добавляет только отсутствующие ШК. Если ШК уже существует у выбранного клиента, его артикул, размер и другие данные не меняются. Импорт Excel не создаёт остатки и не обращается к WB.</p><p id="r65CatalogStatus" class="r65-status"></p><div id="r65CatalogGrid" class="product-catalog-grid"></div><div id="r65CatalogPager" class="r65-pager"></div></div>');
    titles.localcatalog='База товаров';const catalogLink=document.createElement('div');catalogLink.className='r65-toolbar';catalogLink.innerHTML='<button class="secondary" data-r65-go="localcatalog">База товаров</button>';$('page-stockhome').appendChild(catalogLink);const wbCardsNav=document.querySelector('#mainNav [data-page="products"]');if(wbCardsNav&&!document.querySelector('#mainNav [data-page="localcatalog"]')){const baseNav=wbCardsNav.cloneNode(true);baseNav.dataset.page='localcatalog';baseNav.removeAttribute('id');baseNav.classList.remove('active');baseNav.textContent='База товаров';baseNav.addEventListener('click',()=>go('localcatalog'));wbCardsNav.after(baseNav);}
    $('r65CatalogSearch').oninput=()=>{catalogPage=1;renderCatalog();};$('r65CatalogRefresh').onclick=()=>loadCatalog(true);
    $('r102SyncWbCardsToBase').onclick=()=>syncWbCardsToProductBase().catch(e=>toast(e.message,true));$('r102ImportWbCardsToBase').onclick=()=>materializeWbCardsToProductBase().catch(e=>toast(e.message,true));
    $('r115ProductBaseImport').onclick=()=>$('r115ProductBaseFile').click();$('r115ProductBaseFile').onchange=e=>importProductBaseExcel(e.target.files?.[0]).catch(err=>toast(err.message,true));
    $('r65CatalogNew').onclick=async()=>{const code=prompt('\u0428\u041a \u0442\u043e\u0432\u0430\u0440\u0430 (\u043d\u043e\u0432\u044b\u0439 \u0438\u043b\u0438 \u0443\u0434\u0430\u043b\u0451\u043d\u043d\u044b\u0439):');if(!code?.trim())return;try{await FC26Cards.open(normalizeReceivingBarcode(code).barcode);toast('\u041a\u0430\u0440\u0442\u043e\u0447\u043a\u0430 \u0441\u043e\u0445\u0440\u0430\u043d\u0435\u043d\u0430');}catch(e){toast(e.message,true);}};
    bindProductBaseLocalModal();
    $('r65CatalogGrid').addEventListener('click',e=>{const open=e.target.closest('[data-product-base-open-card]'),btn=e.target.closest('[data-product-base-hide]');if(open){openProductBaseCard(open.dataset.productBaseOpenCard);return;}if(btn)hideProductBaseCard(btn.dataset.productBaseHide).catch(err=>toast(err.message,true));});
    installed=true;
  }
  document.addEventListener('click',e=>{const nav=e.target.closest('[data-r65-go]');if(nav){e.preventDefault();go(nav.dataset.r65Go);}if(e.target.closest('[data-r65-transfer]'))window.FCReceiving?.transfer();});
  const oldGo=go;go=function(p){setup();if(p==='inventory')p='stockhome';state.r65FbsOpen=p==='inventoryfbs';if(p==='inventoryfbs'){oldGo('inventory');fcR20Remember('fc_active_page','inventoryfbs');refresh('FBS');return;}oldGo(p);if(['stockhome','mainstock','fbspicks','localcatalog'].includes(p)){state.warehouseMenuOpen=true;if(typeof applyWarehouseMenu==='function')applyWarehouseMenu();document.querySelector('#mainNav [data-page="inventory"]')?.classList.add('active');}if(p==='mainstock')refresh('MAIN');if(p==='localcatalog')loadCatalog(false);};
  const oldLoad=loadInventoryCatalog;loadInventoryCatalog=function(options={}){if(state.activePage!=='inventory'){state.inventoryCatalogLoaded=false;return Promise.resolve();}return oldLoad(options);};
  inventoryFilteredRows=fbsRows;renderInventory=renderFbs;previewManualReceivingProduct=lookupProduct;
  document.addEventListener('change',e=>{if(e.target?.matches?.('[data-inventory-select],[data-inventory-select-visible]'))setTimeout(()=>{const b=$('r108FbsTransferSelected');if(b)b.disabled=selectedInventoryBarcodes.size===0;},0);if(['clientSelect','warehouseSelect'].includes(e.target?.id)){closeDialog();selectedMainBarcodes.clear();selectedInventoryBarcodes.clear();if(e.target?.id==='clientSelect'){catalogRows=[];catalogClient='';state.wbProductIndex=[];state.wbProductIndexClient='';}setTimeout(()=>{for(const k of ['MAIN','FBS']){render(k);if(current(k))refresh(k);}},0);}},true);
  document.addEventListener('click',e=>{if(e.target.closest('#logoutBtn')){loggedOut=true;cache.clear();closeDialog();}},true);
  window.addEventListener('storage',e=>{if(e.key==='fc:r65:changed'){try{const x=JSON.parse(e.newValue||'null');if(x?.client_id===state.clientId&&x?.warehouse_id===state.warehouseId)invalidate();}catch{}return;}if(e.key?.startsWith(cache.prefix)){cache.memory.delete(e.key);for(const k of ['MAIN','FBS'])if(key(k)===e.key){cache.versions.set(e.key,cache.ticket(e.key)+1);if(current(k))render(k);}}});
  window.addEventListener('message',e=>{if(!window.FCStockSync&&e.origin===location.origin&&e.source===$('fcWorkflowFrame')?.contentWindow&&e.data?.type==='fc-warehouse-changed'&&e.data.client_id===state.clientId&&e.data.warehouse_id===state.warehouseId)invalidate();});
  document.addEventListener('visibilitychange',()=>{if(!window.FCStockSync&&!document.hidden&&!fcUserEditing(document.querySelector('.page.active')||document))for(const k of ['MAIN','FBS'])if(current(k))refresh(k);});
  setInterval(()=>{if(!document.hidden&&!fcUserEditing(document.querySelector('.page.active')||document))for(const k of ['MAIN','FBS'])if(current(k))refresh(k);},60000);
  window.FCStockRooms={revision:k=>cache.get(key(k))?.data.revision??null,isFetching:k=>Boolean(flights.get(key(k))?.ticket===cache.ticket(key(k))),retryAt:k=>retryAfter.get(key(k))||0,error:k=>k?errors.get(key(k)):null,refreshOpen,closeDialog,openBox,places,refresh,invalidate,saveProduct,lookupProduct,openProductCardByBarcode,scope,exportStock,boot};
  document.addEventListener('click',e=>{const b=e.target.closest('[data-fc-product-card-barcode]');if(!b)return;e.preventDefault();e.stopPropagation();openProductCardByBarcode(b.dataset.fcProductCardBarcode||'').catch(err=>toast(err.message,true));});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setup);else setup();
})();

/* r72: committed changes refresh scoped views; background reads keep rows visible. */
(()=>{'use strict';
  const scope=()=>JSON.stringify([state.commercialSession?.username||'',state.commercialSession?.tenant_id||'',state.clientId||'',state.warehouseId||'']);
  const contextPages=new Set(['dashboard','containers','locations','move','receive','bulkimport','palletreceive','ship','masterdata','operations']);
  let generation=0,flight=null,flightScope='',revision=null,revisionScope='',polling=false;
  const rawApi=api,rawBlob=apiBlob;
  const stockReadPosts=new Set(['/warehouse-ui/wb-sales/status','/warehouse-ui/wb-sales/check','/inventory/movement-summary','/receiving/inventory-control-status','/receiving/wb-live-stocks','/receiving/wb-drift-repair']);
  const stockMutation=(path,opt)=>!String(path).startsWith('/r145f26/catalog/')&&!['GET','HEAD'].includes(String(opt?.method||'GET').toUpperCase())&&!stockReadPosts.has(String(path).split('?')[0].replace(/\/$/,''))&&!/(\/auth\/|\/preview|\/template|\/export|\/labels|\/label\/|\/verify-|\/draft\/|\/lookup|\/resolve|\/scan-marking-code)/.test(path);
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
          // r145f22: an open address must not wait for all SKUs, all balances and occupancy.
          const detailOnly=active==='locations'&&locationBrowser.locationId&&['DETAIL','HISTORY'].includes(locationBrowser.level);
          if(contextPages.has(active)&&!detailOnly)await loadContextData({force:true,quiet:true});
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
  api=async function(path,opt={}){const captured=scope();const data=await rawApi(path,opt);if(captured===scope()&&stockMutation(path,opt)){
    if(String(path).split('?')[0]==='/r145f22/receipts/cancel/confirm'||String(path).split('?')[0]==='/receiving/manual/batch'){changed(path,data).catch(e=>note(e.message,true));}
    else await changed(path,data);
  }return data;};
  apiBlob=async function(path,opt={}){const captured=scope();const data=await rawBlob(path,opt);if(captured===scope()&&stockMutation(path,opt))await changed(path);return data;};
  const oldGo=go;
  go=function(p){oldGo(p);if(state.r70ContextDirty&&contextPages.has(state.activePage))refresh();};
  async function poll(){
    if(polling||flight||document.hidden||!state.clientId||!state.warehouseId||fcUserEditing(document.querySelector('.page.active')||document))return;
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

/* FC_R68_BOX_ACTIONS: stable event delegation for dynamic box rows. */
function containerChunks(values,size=800){const out=[];for(let i=0;i<values.length;i+=size)out.push(values.slice(i,i+size));return out;}
async function containerRemovalOptions(stockClass,ids){
  const set=new Set(ids.map(String));
  const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId,stock_class:stockClass});
  const data=await api('/warehouse-ui/actions/bulk-options?'+q,{timeoutMs:60000});
  return (Array.isArray(data?.rows)?data.rows:[]).filter(x=>set.has(String(x.container_id||''))&&Number(x.free||0)>0).map(x=>({sku_id:x.sku_id,location_id:x.location_id,container_id:x.container_id,quantity:Number(x.free)}));
}
async function removeFreeStockFromContainers(ids){
  const totals={MAIN:0,FBS:0};
  for(const stockClass of ['MAIN','FBS']){
    const rows=await containerRemovalOptions(stockClass,ids);
    for(const part of containerChunks(rows,800)){
      if(!part.length)continue;
      const result=await api('/warehouse-ui/actions/bulk-remove',{method:'POST',timeoutMs:180000,body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId,request_id:uid(),stock_class:stockClass,reason:'Массовое удаление коробов вместе с остатками',items:part})});
      totals[stockClass]+=Number(result?.quantity||0);
    }
  }
  return totals;
}
async function archiveContainersInChunks(ids){
  const deleted=[],skipped=[];
  for(const part of containerChunks(ids,800)){
    const r=await api('/box-intake/archive-empty',{method:'POST',timeoutMs:120000,body:JSON.stringify({ids:part,client_id:state.clientId,warehouse_id:state.warehouseId})});
    deleted.push(...(r.deleted||[]));skipped.push(...(r.skipped||[]));
  }
  return {deleted,skipped};
}
async function deleteContainersWithStock(all=false){
  const ids=(all?activeContainers().map(x=>String(x.id)):[...selectedContainerIds]);
  if(!ids.length)throw new Error(all?'Активных коробов нет':'Сначала выберите короба галочками');
  const idSet=new Set(ids),physical=(state.inventory||[]).filter(x=>idSet.has(String(x.container_id||''))&&Number(x.quantity||0)>0).reduce((sum,x)=>sum+Number(x.quantity||0),0);
  const title=all?`Удалить ВСЕ активные короба: ${ids.length} шт.?`:`Удалить выбранные короба: ${ids.length} шт.?`;
  const stockText=physical>0?`

В них сейчас учтено примерно ${fmtQty(physical)} шт. Свободный товар будет СПИСАН из остатков перед удалением коробов.`:`

Выбранные короба сейчас пустые.`;
  if(!confirm(`${title}${stockText}

Активные резервы заказов и незакрытые сборки не ломаем: соответствующие короба останутся и будут показаны как пропущенные. История операций сохранится.`))return;
  if(physical>0&&!confirm(`Подтвердите списание товара из выбранных коробов.

После подтверждения свободные остатки в этих коробах будут уменьшены, а FBS-изменения поставлены в очередь WB. Продолжить?`))return;
  const totals=await removeFreeStockFromContainers(ids);
  const archived=await archiveContainersInChunks(ids);
  for(const row of archived.deleted||[])selectedContainerIds.delete(String(row.id));
  await loadContextData();
  const skipped=archived.skipped||[];
  const reason=skipped.length?` Причина первых пропусков: ${skipped.slice(0,3).map(x=>x.reason||'защищённый остаток').join('; ')}.`:'';
  toast(`Короба удалены: ${(archived.deleted||[]).length} из ${ids.length}. Списано: Основной ${fmtQty(totals.MAIN)} шт., FBS ${fmtQty(totals.FBS)} шт.${skipped.length?` Пропущено: ${skipped.length}.`:''}${reason}`,skipped.length>0);
}
async function deleteSelectedEmptyContainers(){
  const ids=[...selectedContainerIds];if(!ids.length)throw new Error('Сначала выберите короба галочками');
  const r=await archiveContainersInChunks(ids);for(const x of r.deleted||[])selectedContainerIds.delete(String(x.id));await loadContextData();toast(`Удалено пустых коробов: ${(r.deleted||[]).length}. Пропущено: ${(r.skipped||[]).length}.`,Boolean(r.skipped?.length));
}
document.addEventListener('change',e=>{const b=e.target.closest('[data-container-select]');if(b){b.checked?selectedContainerIds.add(String(b.dataset.containerSelect)):selectedContainerIds.delete(String(b.dataset.containerSelect));syncContainerBulkToolbar();}});
document.addEventListener('click',async e=>{
  const b=e.target.closest('[data-container-view],[data-container-print],[data-container-move],[data-container-rename],[data-container-delete]');if(!b||b.disabled)return;
  e.preventDefault();b.disabled=true;
  try{
    if(b.dataset.containerView){await showContainer(b.dataset.containerView);return;}
    if(b.dataset.containerPrint){await printExistingContainerLabels([b.dataset.containerPrint],false);return;}
    if(b.dataset.containerMove){await moveContainerFromList(b.dataset.containerMove);return;}
    if(b.dataset.containerRename){await renameContainer(b.dataset.containerRename);return;}
    const id=b.dataset.containerDelete,c=containerObj(id);if(!c)return;
    if(!confirm(`Убрать короб ${containerPublicCode(c)} из активного списка? История сохранится. Короб с учтённым товаром удалить нельзя.`))return;
    await api('/box-intake/boxes/'+encodeURIComponent(id)+'/archive-empty',{method:'POST',body:JSON.stringify({client_id:state.clientId,warehouse_id:state.warehouseId})});selectedContainerIds.delete(String(id));await loadContextData();toast('Короб убран в архив. История сохранена.');
  }catch(err){toast(err.message,true);}finally{b.disabled=false;}
});
/* The new receiving module uses the existing authenticated API and context. */
(()=>{const native=()=>{if(document.getElementById('fcReceivingNativeScript'))return;const n=document.createElement('script');n.id='fcReceivingNativeScript';n.src='/static/receiving-native.js?v=r76';n.onload=()=>{window.FCTsdReceivingReady=true;window.dispatchEvent(new Event('fc:receiving-ready'));};n.onerror=()=>toast('Не удалось загрузить приёмку. Обновите страницу Ctrl+F5',true);document.head.appendChild(n);};if(window.FCBoxIntake){native();return;}const s=document.createElement('script');s.id='fcBoxIntakeScript';s.src='/static/box_intake.js?v=r145f27';s.onload=native;s.onerror=()=>toast('Не удалось загрузить приёмку коробов. Обновите страницу Ctrl+F5',true);document.head.appendChild(s);})();

/* r74: downloadable Android application and the shared receiving workplace. */
(()=>{const s=document.createElement('script');s.src='/static/tsd-workplace.js?v=r74';s.onerror=()=>toast('Не удалось открыть рабочее место ТСД. Повторите загрузку.',true);document.head.appendChild(s);})();

// r75: persistent global WB policy. Restocks and order cancellations do not lift it.
document.addEventListener('click',e=>{
 if(e.target.closest('[data-inventory-pause-all]'))runInventoryBulkAction('PAUSE_WB',{allInventory:true}).then(()=>refreshGlobalSalesStop(true)).catch(x=>toast(x.message,true));
 if(e.target.closest('[data-inventory-resume-all]'))runInventoryBulkAction('RESUME_WB',{allInventory:true}).then(()=>refreshGlobalSalesStop(true)).catch(x=>toast(x.message,true));
});

async function refreshGlobalSalesStop(force=false){
 const c=state.clientId,w=state.warehouseId;if(!c||!w)return;
 const key=c+'|'+w,cache=window.FCSalesPolicy||(window.FCSalesPolicy={});
 if(!force&&cache.key===key&&(cache.busy||Date.now()-cache.at<15000))return;
 cache.key=key;cache.busy=true;
 let el=document.getElementById('globalSalesStopStatus');
 if(!el){const bar=document.getElementById('inventoryBulkToolbar');if(!bar){cache.busy=false;return;}el=document.createElement('p');el.id='globalSalesStopStatus';bar.prepend(el);}
 el.textContent='Проверяем общую остановку продаж…';
 try{const data=await api('/receiving/sales-stop?'+new URLSearchParams({client_id:c,warehouse_id:w}));
 if(state.clientId!==c||state.warehouseId!==w)return;
 el.textContent=data.global_paused?'Общий стоп WB включён. Исключения — только явно возобновлённые товары. Приёмка и отмена заказов стоп не снимают.':'Общий стоп WB выключен. Отдельные товары могут быть остановлены.';
 cache.at=Date.now();
 }catch(e){if(state.clientId===c&&state.warehouseId===w)el.textContent='Не удалось проверить стоп продаж: '+e.message;}
 finally{if(cache.key===key)cache.busy=false;}
}

/* r76: posted receipts, explicit quantities, durable retry, scoped asynchronous results. */
(()=>{'use strict';
  const ctx=()=>({client_id:state.clientId||'',warehouse_id:state.warehouseId||''});
  const scope=()=>JSON.stringify([state.commercialSession?.username||'',...Object.values(ctx())]);
  const path=(p)=>p+'?'+new URLSearchParams(ctx());
  const types={STRUCTURED:'Структурная Excel',EXCEL:'Excel',MANUAL:'Ручная',TSD:'Приёмка ТСД',PVZ:'Приёмка ПВЗ'};
  const zones={MAIN:'Основной',FBS:'FBS',LEGACY:'Склад'};
  const requestId=()=>{const b=new Uint8Array(16);crypto.getRandomValues(b);b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;const h=[...b].map(x=>x.toString(16).padStart(2,'0')).join('');return h.slice(0,8)+'-'+h.slice(8,12)+'-'+h.slice(12,16)+'-'+h.slice(16,20)+'-'+h.slice(20);};
  const date=x=>x?new Date(x).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'';
  let current=null,detailEpoch=0,historyEpoch=0,posting=false,offset=0,query='',historyJob=null,historyScope='',pollTimer;
  const pendingMemory=new Map();
  const pendingKey=id=>'fc:r76:receipt-transfer:'+scope()+':'+id;
  function pending(id,value){const k=pendingKey(id);if(arguments.length>1){if(value)pendingMemory.set(k,value);else pendingMemory.delete(k);try{if(value)localStorage.setItem(k,JSON.stringify(value));else localStorage.removeItem(k);}catch{}return value;}if(pendingMemory.has(k))return pendingMemory.get(k);try{return JSON.parse(localStorage.getItem(k)||'null');}catch{return null;}}
  function root(){return $('r62ReceiptDetail');}
  function message(text,error=false){const el=root()?.querySelector('[data-r76-message]');if(el){el.textContent=text;el.className=error?'bad':'good';}}
  function table(data){return `<table class="r76-history"><thead><tr><th>Дата / сотрудник</th><th>Приёмка</th><th>Принято</th><th>Сейчас в FBS из приёмки</th><th>Состояние</th><th></th></tr></thead><tbody>${data.rows.map(r=>`<tr><td>${esc(date(r.created_at))}<small>${esc(r.performed_by||'—')}</small></td><td><b>${esc(r.reference)}</b><small>${esc(types[r.type]||r.type)} · ${esc(zones[r.zone]||r.zone)}</small></td><td>${fmtQty(r.units)} шт.<small>${r.lines} поз.</small></td><td>${fmtQty(r.transferred)} шт.${r.reloadable?`<small>Можно загрузить повторно после обнуления: ${fmtQty(r.reloadable)} шт.</small>`:'<small>Публикация WB — в составе</small>'}</td><td>${r.status==='CANCELLED'?'Отменена':'Проведена'}</td><td><button type="button" class="secondary" data-r76-open="${esc(r.id)}">Состав / FBS</button></td></tr>`).join('')||'<tr><td colspan="6">Проведённые приёмки не найдены.</td></tr>'}</tbody></table>`;}
  async function load(force=false){
    const el=$('r62Receipts');if(!el||!state.clientId||!state.warehouseId)return;
    const captured=scope();if(!force&&historyJob&&historyScope===captured)return historyJob;
    historyScope=captured;const ticket=++historyEpoch;
    if(el.dataset.scope!==captured){el.dataset.scope=captured;el.textContent='Загружаем историю приёмок…';offset=0;}
    const job=(async()=>{try{const data=await api(path('/warehouse/receipts/recent')+'&'+new URLSearchParams({limit:'50',offset:String(offset),query}),{timeoutMs:30000});
      if(captured!==scope()||ticket!==historyEpoch)return;
      el.innerHTML=`<div class="r76-actions"><input data-r76-search aria-label="Поиск приёмки" placeholder="Название, номер или сотрудник" value="${esc(query)}"><button type="button" class="secondary" data-r76-find>Найти</button><button type="button" class="secondary" data-r76-history-refresh>Обновить историю</button></div>${table(data)}<div class="r76-actions"><span>Найдено: ${data.total}</span><button type="button" data-r76-page="-1" ${offset===0?'disabled':''}>Назад</button><button type="button" data-r76-page="1" ${offset+data.rows.length>=data.total?'disabled':''}>Далее</button></div>`;
    }catch(e){if(captured===scope()&&ticket===historyEpoch){el.innerHTML=`<p class="bad">${esc(e.message)}</p><button type="button" data-r76-history-refresh>Повторить загрузку</button>`;}}finally{if(historyJob===job)historyJob=null;}})();historyJob=job;return job;
  }
  function selected(){if(!current)return [];const rows=new Map(current.lines.map(r=>[r.line_id,r]));return [...root().querySelectorAll('[data-r76-line]:checked')].map(el=>{const r=rows.get(el.dataset.r76Line);const q=Number(root().querySelector(`[data-r76-quantity="${r.line_id}"]`).value);if(!Number.isSafeInteger(q)||q<=0||q>r.available_quantity)throw Error(`${r.barcode}: укажите целое количество от 1 до ${r.available_quantity}`);return {line_id:r.line_id,quantity:q};});}
  function totals(){try{const rows=selected();const n=root()?.querySelector('[data-r76-selected]');if(n)n.textContent=`Выбрано ${rows.length} поз. · ${rows.reduce((s,r)=>s+r.quantity,0)} шт.`;}catch(e){const n=root()?.querySelector('[data-r76-selected]');if(n)n.textContent=e.message;}}
  function publicationHtml(data){
    if(window.FCWBSales&&data.wb_state)return window.FCWBSales.receiptHTML(data);
    const saved=new Map();for(const e of [...(data.events||[])].reverse())for(const x of e.result_json.publication||[])saved.set(x.barcode,{...x,last_error:x.error,recorded_at:e.created_at});
    for(const x of data.publications||[]){const previous=saved.get(x.barcode);if(!previous||!Number.isFinite(Date.parse(previous.recorded_at))||Date.parse(x.updated_at)>=Date.parse(previous.recorded_at))saved.set(x.barcode,x);}
    const labels={PENDING:'В очереди WB',PROCESSING:'Отправляется в WB',DONE:'Отправка завершена (количество ниже)',FAILED:'Ошибка отправки WB',NOT_QUEUED:'Не отправлено в WB',PAUSED:'Продажи остановлены'};
    return `<h4>Отправка в Wildberries</h4>${data.global_paused?'<p>Общий стоп продаж включён. Товар можно перевести в FBS, остаток для продажи на WB останется 0 до возобновления продаж.</p>':''}${[...saved.values()].map(x=>`<p><b>${esc(x.barcode)}</b> · ${esc(labels[x.status]||x.status)}${x.desired_amount!=null?' · '+fmtQty(x.desired_amount)+' шт.':''}${x.status==='FAILED'||x.status==='NOT_QUEUED'?`<small class="bad">${esc(x.last_error||'Повторите отправку')}</small>`:''}</p>`).join('')||'<p>После перевода свободный остаток будет поставлен в очередь отправки на рабочий склад WB.</p>'}`;
  }
  function draw(data){
    const el=root();if(!el)return;current=data;const r=data.receipt,active=r.status==='COMPLETED',waiting=pending(r.id);
    el.classList.remove('hidden');el.classList.add('r76-detail');
    el.innerHTML=`<div class="panel-head"><div><span class="kicker">СОСТАВ ПРИЁМКИ</span><h3>${esc(r.reference)}</h3><p>${esc(date(r.created_at))} · ${esc(types[r.type]||r.type)} · ${active?'Проведена':'Отменена'} · ${esc(r.performed_by||'—')}</p></div><button type="button" class="secondary" data-r76-close>Закрыть</button></div>
      <p>Доступно для загрузки в FBS: <b>${fmtQty(data.available_total)} шт.</b> · сейчас в FBS из этой приёмки: <b>${fmtQty(data.transferred_total)} шт.</b>${data.reloadable_total?` · после обнуления можно загрузить повторно: <b>${fmtQty(data.reloadable_total)} шт.</b>`:''}${r.zone==='FBS'?' · приёмка изначально принята в FBS.':'.'}</p>
      <p class="muted">Выберите позиции и количество. Если FBS этой приёмки был специально обнулён через «Обнулить FBS», удалённое количество снова доступно для загрузки. Обычные отгрузки и списания товар обратно не создают.</p>
      <div class="r76-actions"><button type="button" class="primary" data-r76-all ${active&&data.available_total>0&&!waiting?'':'disabled'}>Всю доступную приёмку → FBS</button><button type="button" class="primary" data-r76-transfer ${active&&data.available_total>0&&!waiting?'':'disabled'}>Выбранное → FBS</button><button type="button" class="secondary" data-r76-select ${waiting?'disabled':''}>Выбрать всё</button><button type="button" class="secondary" data-r76-clear ${waiting?'disabled':''}>Снять выбор</button><button type="button" class="secondary" data-r76-refresh>Обновить состав</button><button type="button" class="secondary" data-r76-export>Excel</button></div>
      ${waiting?'<p class="bad">Предыдущий запрос не получил ответа. Повторите его, чтобы проверить результат. Повторное списание или добавление не произойдёт.</p><button type="button" class="primary" data-r76-retry>Проверить результат / повторить запрос</button>':''}
      <p data-r76-message role="status"></p><p data-r76-selected>Выбрано 0 поз. · 0 шт.</p>
      <div class="table-wrap"><table class="r76-lines"><thead><tr><th>Товар</th><th>Ячейка / короб</th><th>Принято</th><th>Сейчас в FBS</th><th>Доступно</th><th>Количество</th><th class="r80-select-head">Выбрать</th></tr></thead><tbody>${data.lines.map(x=>`<tr><td><b>${esc(x.article||'Без артикула')} · ${esc(x.size||'—')}</b><small>${esc(x.name||'')}</small><small>ШК ${esc(x.barcode||'—')}</small></td><td>${esc(x.location||'—')}<small>${esc(x.container||'Без короба')}</small></td><td>${fmtQty(x.quantity_received)}</td><td>${x.initially_fbs?'Принято в FBS':fmtQty(x.transferred)}</td><td><b>${x.available_quantity}</b>${x.reloadable_available_quantity?`<small>Повторно после обнуления: ${fmtQty(x.reloadable_available_quantity)}</small>`:''}${x.held?`<small>В сборке: ${fmtQty(x.held)}</small>`:''}</td><td><label class="r76-quantity"><input type="number" step="1" min="1" max="${x.available_quantity}" value="${x.available_quantity||''}" data-r76-quantity="${esc(x.line_id)}" aria-label="Количество ${esc(x.barcode)}" ${x.available_quantity>0&&!waiting?'':'disabled'}></label></td><td class="r80-select-cell"><input type="checkbox" data-r76-line="${esc(x.line_id)}" aria-label="Выбрать ${esc(x.barcode)}" ${x.available_quantity>0&&!waiting?'':'disabled'}></td></tr>`).join('')||'<tr><td colspan="7">Товарных строк нет.</td></tr>'}</tbody></table></div>
      <div data-r76-publication>${publicationHtml(data)}</div>${active&&(data.transferred_total>0||r.zone==='FBS')?'<button type="button" class="secondary" data-r76-publish>Повторить отправку в WB</button>':''}
      <details><summary>История переводов в FBS (${data.events.length})</summary>${data.events.map(e=>`<p>${esc(date(e.created_at))} · ${esc(e.actor||'—')} · ${fmtQty(e.result_json.moved)} шт.</p>`).join('')||'<p>Переводов пока нет.</p>'}</details>`;
  }
  function pollPublication(data){clearTimeout(pollTimer);if(!(data.publications||[]).some(x=>['PENDING','PROCESSING'].includes(x.status)))return;
    const captured=scope(),id=data.receipt.id,ticket=detailEpoch;let count=0;
    const step=async()=>{if(captured!==scope()||ticket!==detailEpoch||document.hidden||root()?.classList.contains('hidden')||posting)return;try{const fresh=await api(path('/warehouse/receipts/'+id),{timeoutMs:15000});if(captured!==scope()||ticket!==detailEpoch||posting)return;const target=root()?.querySelector('[data-r76-publication]');if(target){const selected=new Set([...target.querySelectorAll('[data-wb-receipt-code]')].filter(x=>x.checked).map(x=>x.dataset.wbReceiptCode));target.innerHTML=publicationHtml(fresh);target.querySelectorAll('[data-wb-receipt-code]').forEach(x=>x.checked=selected.has(x.dataset.wbReceiptCode));}if(++count<12&&(fresh.publications||[]).some(x=>['PENDING','PROCESSING'].includes(x.status)))pollTimer=setTimeout(step,5000);}catch{}};pollTimer=setTimeout(step,5000);
  }
  async function open(id){
    if(posting)return;await ensureContext();const captured=scope(),ticket=++detailEpoch;clearTimeout(pollTimer);
    const el=root();if(!el)return;el.classList.remove('hidden');if(!current||current.receipt.id!==id)el.textContent='Загружаем состав приёмки…';
    try{const data=await api(path('/warehouse/receipts/'+encodeURIComponent(id)),{timeoutMs:30000});if(ticket!==detailEpoch||captured!==scope())return;draw(data);pollPublication(data);el.scrollIntoView?.({behavior:'smooth',block:'start'});}catch(e){if(ticket===detailEpoch&&captured===scope())el.innerHTML=`<p class="bad">${esc(e.message)}</p><button type="button" data-r76-open="${esc(id)}">Повторить</button>`;}
  }
  function publicationMessage(result){const list=result.publication||[],blocked=list.filter(x=>x.status==='NOT_QUEUED'),paused=list.filter(x=>x.status==='PAUSED');const restored=Number(result.restored_after_reset||0);return `Переведено в FBS: ${fmtQty(result.moved||0)} шт.${restored?` Из ранее обнулённой приёмки восстановлено: ${fmtQty(restored)} шт.`:''}${blocked.length?' Не отправлено в WB: '+blocked.length+' поз. — причина указана ниже.':paused.length?' Стоп продаж сохранён; в WB отправляется 0.':' Отправка свободного остатка в WB поставлена в очередь.'}`;}
  async function transfer(all=false,retry=false){
    if(posting||!current)return;const id=current.receipt.id,captured=scope();let req=pending(id);
    if(!retry&&req)throw Error('Сначала проверьте результат предыдущего запроса');
    if(!req){const lines=all?current.lines.filter(x=>x.available_quantity>0).map(x=>({line_id:x.line_id,quantity:x.available_quantity})):selected();if(!lines.length)throw Error('Выберите товар и количество');const total=lines.reduce((s,x)=>s+x.quantity,0);
      if(!confirm(`Перевести ${total} шт. (${lines.length} поз.) из этой приёмки в FBS?${current.global_paused?'\nОбщий стоп продаж останется включён.':''}`))return;
      req={request_id:requestId(),fingerprint:current.fingerprint,lines,performed_by:state.commercialSession?.username||'Администратор',wb_warehouse_id:null};pending(id,req);
    }
    posting=true;root().querySelectorAll('button,input').forEach(b=>b.disabled=true);message('Переводим выбранный товар…');
    let result=null,error=null;
    try{result=await api(path('/warehouse/receipts/'+id+'/to-fbs'),{method:'POST',body:JSON.stringify(req),timeoutMs:90000});if(captured!==scope())return;pending(id,null);}
    catch(e){error=e;if(captured===scope()&&[400,403,404,409,422].includes(Number(e.status)))pending(id,null);}
    finally{posting=false;}
    if(captured!==scope())return;await open(id);if(result){message(publicationMessage(result),result.publication.some(x=>x.status==='NOT_QUEUED'));await load(true);}else message(error?.message||'Ответ не получен. Повторите проверку запроса.',true);
  }
  async function publish(){if(posting||!current)return;const id=current.receipt.id,captured=scope();posting=true;root().querySelectorAll('button').forEach(b=>b.disabled=true);let result;
    try{result=await api(path('/warehouse/receipts/'+id+'/publish-fbs'),{method:'POST',body:JSON.stringify({wb_warehouse_id:null}),timeoutMs:60000});}finally{posting=false;if(captured===scope())await open(id);}
    if(captured===scope())message('Пересчёт свободного остатка WB: '+result.publication.map(x=>x.status==='NOT_QUEUED'?x.barcode+' — '+x.error:x.barcode+' — в очереди').join('; '),result.publication.some(x=>x.status==='NOT_QUEUED'));
  }
  function changed(){window.FCStockRooms?.invalidate();if($('r62Receipts'))load(true);}
  document.addEventListener('click',e=>{const b=e.target.closest('[data-r76-open],[data-r76-close],[data-r76-transfer],[data-r76-all],[data-r76-retry],[data-r76-select],[data-r76-clear],[data-r76-refresh],[data-r76-export],[data-r76-publish],[data-r76-history-refresh],[data-r76-page],[data-r76-find]');if(!b||b.disabled||posting)return;e.preventDefault();
    (async()=>{if(b.hasAttribute('data-r76-open')){if(state.activePage!=='receivinghome')go('receivinghome');await open(b.dataset.r76Open);}else if(b.hasAttribute('data-r76-close')){detailEpoch++;clearTimeout(pollTimer);root()?.classList.add('hidden');current=null;}else if(b.hasAttribute('data-r76-transfer'))await transfer();else if(b.hasAttribute('data-r76-all'))await transfer(true);else if(b.hasAttribute('data-r76-retry'))await transfer(false,true);else if(b.hasAttribute('data-r76-publish'))await publish();else if(b.hasAttribute('data-r76-refresh'))await open(current.receipt.id);else if(b.hasAttribute('data-r76-export')){const blob=await apiBlob(path('/warehouse/receipts/'+current.receipt.id+'/export.xlsx'));saveBlob(blob,'Приёмка_и_переводы_FBS.xlsx');}else if(b.hasAttribute('data-r76-select')||b.hasAttribute('data-r76-clear')){root().querySelectorAll('[data-r76-line]:not(:disabled)').forEach(x=>x.checked=b.hasAttribute('data-r76-select'));totals();}else{if(b.hasAttribute('data-r76-page'))offset=Math.max(0,offset+Number(b.dataset.r76Page)*50);if(b.hasAttribute('data-r76-find')){query=$('r62Receipts').querySelector('[data-r76-search]').value;offset=0;}await load(true);}})().catch(err=>{message(err.message,true);toast(err.message,true);});
  });
  document.addEventListener('input',e=>{if(e.target.matches('[data-r76-quantity]')){const checkbox=root()?.querySelector(`[data-r76-line="${e.target.dataset.r76Quantity}"]`);if(checkbox)checkbox.checked=true;totals();}});
  document.addEventListener('change',e=>{if(e.target.matches('[data-r76-line]'))totals();if(['clientSelect','warehouseSelect'].includes(e.target.id)){detailEpoch++;historyEpoch++;current=null;clearTimeout(pollTimer);root()?.classList.add('hidden');if($('r62Receipts')){$('r62Receipts').innerHTML='';delete $('r62Receipts').dataset.scope;}if(state.activePage==='receivinghome')setTimeout(()=>load(true),0);}},true);
  const style=document.createElement('style');style.textContent='.r76-actions{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:12px 0}.r76-actions input{max-width:400px;min-width:180px}.r76-detail{min-width:0}.r76-detail small,.r76-history small{display:block;white-space:normal;font-weight:400;color:#687c9b;margin-top:5px}.r76-detail table{width:100%;min-width:820px;table-layout:auto}.r76-detail td{white-space:normal;vertical-align:top}.r76-lines td:first-child{max-width:300px;min-width:170px}.r76-lines td:nth-child(2){max-width:260px;overflow-wrap:anywhere}.r76-quantity{display:flex;align-items:center;justify-content:flex-end}.r76-quantity input[type=number]{width:100px;min-width:80px;box-sizing:border-box}.r80-select-head,.r80-select-cell{text-align:right}.r80-select-cell input[type=checkbox]{width:20px;height:20px}.r76-history{width:100%;min-width:730px}.r76-detail button{white-space:normal}.r76-detail [data-r76-publication]{padding:12px;border-radius:12px;background:#f6f8fc;margin:14px 0}.r76-detail [data-r76-publication] p{margin:6px 0}.r76-detail [data-r76-message]:empty{display:none}';document.head.appendChild(style);
  window.FCReceiptHistory={load,open,changed,transfer,publish,refreshCurrent:()=>current?open(current.receipt.id):Promise.resolve()};
})();

/* r77: local FBS classification, sale permission and verified WB stock are distinct. */
(()=>{'use strict';
 const ctx=()=>({client_id:state.clientId||'',warehouse_id:state.warehouseId||''});
 const scope=()=>JSON.stringify([state.commercialSession?.username,...Object.values(ctx())]);
 const labels={ACTIVE:'Остаток подтверждён WB',PAUSED:'Продажи остановлены',PENDING:'Ожидает отправки в WB',ERROR:'Ошибка WB',MISMATCH:'Остатки не совпадают',STALE:'Нужна свежая сверка WB',UNVERIFIED:'WB ещё не проверен',EMPTY:'На WB 0 — свободного товара нет'};
 const uid=()=>{const b=new Uint8Array(16);crypto.getRandomValues(b);b[6]=(b[6]&15)|64;b[8]=(b[8]&63)|128;const h=[...b].map(x=>x.toString(16).padStart(2,'0')).join('');return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;};
 let data=null,owner='',ticket=0,job=null,jobScope='',checking=null,tab='ALL',items=[],busy=false,error='',lastCheck=0;
 const pending=new Map();
 const key=()=> 'fc:r77:stopped-action:'+scope();
 function stored(value){const k=key();if(arguments.length){if(value)pending.set(k,value);else pending.delete(k);try{value?localStorage.setItem(k,JSON.stringify(value)):localStorage.removeItem(k);}catch{}return value;}try{return pending.get(k)||JSON.parse(localStorage.getItem(k)||'null');}catch{return pending.get(k);}}
 const date=x=>x?new Date(x).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'—';
 function row(code,source=data){const r=source?.rows?.find(x=>x.barcode===code);if(!r)return {status:'UNVERIFIED',barcode:code};if(r.status==='ACTIVE'&&Date.now()-Date.parse(r.checked_at)>120000)return {...r,status:'STALE',fresh:false};return r;}
 function html(r){return `<b class="${['ERROR','MISMATCH'].includes(r.status)||r.paused&&r.confirmed_amount>0?'bad':''}">${esc(labels[r.status]||r.status)}</b><small>${r.confirmed_amount==null?'Подтверждения WB пока нет':`WB подтвердил: <b>${fmtQty(r.confirmed_amount)} шт.</b> · ${esc(date(r.checked_at))}`}</small>${r.target==null?'':`<small>К отправке в WB: ${fmtQty(r.target)} шт.</small>`}${r.paused?`<small>${r.stop_confirmed?'WB подтвердил остановку: остаток 0.':'Остановка на WB ещё не подтверждена. Нажмите «Сверить WB».'}</small>`:''}${r.error?`<small class="bad">${esc(r.error)}</small>`:''}`;}
 function controls(){let host=$('r77SalePanel');if(!host&&$('r65FbsStatus')){host=document.createElement('div');host.id='r77SalePanel';$('r65FbsStatus').after(host);}if(!host)return;
 const all=items.map(x=>row(x.barcode));const count=t=>all.filter(r=>t==='ALL'||t==='ACTIVE'&&r.status==='ACTIVE'||t==='PAUSED'&&r.paused||t==='ATTENTION'&&!r.paused&&r.status!=='ACTIVE').length;
 host.innerHTML=`<p>${data?.global_paused?'Общий стоп включён. Продаются только товары, которые вы явно возобновили. Новая приёмка и отмена заказов стоп не снимают.':'Общий стоп выключен. Остановленные позиции остаются заблокированными.'} ${data?.wb_warehouse_id?'Рабочий склад WB: '+esc(data.wb_warehouse_id)+'.':''}</p><div class="r77-tabs">${[['ALL','Все FBS'],['ACTIVE','Актуальные для продажи'],['PAUSED','Остановлены для продажи'],['ATTENTION','Ожидают / требуют внимания']].map(([t,n])=>`<button type="button" class="${tab===t?'primary':'secondary'}" data-wb-tab="${t}">${n} · ${count(t)}</button>`).join('')}</div><div class="r77-actions"><button type="button" class="secondary" data-wb-action="CHECK">Сверить WB</button><button type="button" class="primary" data-wb-action="RESUME_WB">Возобновить выбранные</button><button type="button" class="secondary" data-wb-action="PAUSE_WB">Остановить выбранные</button><button type="button" class="secondary" data-wb-action="RETRY">Повторить отправку</button>${tab==='PAUSED'?'<button type="button" class="secondary" data-wb-action="TO_MAIN">Выбранные → Основной</button><button type="button" class="danger" data-wb-action="WRITE_OFF">Списать свободные</button>':''}${stored()?'<button type="button" data-wb-action="REPLAY">Проверить результат операции</button>':''}</div><p class="bad" role="alert">${esc(error||data?.configuration_error||'')}</p><small>Число WB относится ко всему размеру товара на указанном складе и времени сверки. Локальный перевод в FBS сам по себе не означает публикацию.</small>`;
 host.querySelectorAll('[data-wb-action]').forEach(b=>b.disabled=busy);
 }
 function filter(list){items=list;if(owner!==scope()){data=null;owner=scope();tab='ALL';error='';lastCheck=0;}controls();return list.filter(x=>{const r=row(x.barcode);return tab==='ALL'||tab==='ACTIVE'&&r.status==='ACTIVE'||tab==='PAUSED'&&r.paused||tab==='ATTENTION'&&!r.paused&&r.status!=='ACTIVE';});}
 function rowActions(r){const s=row(r.barcode);return `<div class="r77-actions">${s.paused?`<button type="button" class="mini secondary" data-wb-action="RESUME_WB" data-wb-code="${esc(r.barcode)}">Возобновить</button><button type="button" class="mini secondary" data-wb-action="TO_MAIN" data-wb-code="${esc(r.barcode)}">На Основной</button>`:''}</div>`;}
 function invalidate(){ticket++;job=null;data=null;}
 function paint(){if(state.activePage==='inventory')renderInventory();}
 async function load(force=false){const captured=scope();if(job&&jobScope===captured)return job;if(!ctx().client_id||!ctx().warehouse_id)return;if(!force&&data&&owner===captured&&Date.now()-Date.parse(data.generated_at)<10000)return;
 const n=++ticket;jobScope=captured;const next=(async()=>{try{const result=await api('/warehouse-ui/wb-sales/status',{method:'POST',body:JSON.stringify(ctx()),timeoutMs:30000});if(scope()!==captured||n!==ticket)return;data=result;owner=captured;error='';paint();}catch(e){if(scope()===captured&&n===ticket){error=e.message;paint();}}finally{if(job===next)job=null;}})();job=next;return next;
 }
 async function check(ids=null){const captured=scope();const selection=JSON.stringify(ids?[...ids].sort():null);if(checking?.scope===captured){if(checking.selection===selection)return checking.promise;try{await checking.promise;}catch{}if(captured!==scope())return;}const call={scope:captured,selection};call.promise=(async()=>{try{await api('/warehouse-ui/wb-sales/check',{method:'POST',body:JSON.stringify({...ctx(),sku_ids:ids}),timeoutMs:90000});if(captured===scope()){lastCheck=Date.now();invalidate();await load(true);}}catch(e){if(captured===scope()){invalidate();await load(true);error=e.message;paint();}throw e;}finally{if(checking===call)checking=null;}})();checking=call;return call.promise;}
 function receiptHTML(d){if(!d.wb_state)return '';const src=d.wb_state;return `<h4>Состояние товаров на WB</h4><p>${src.global_paused?'Общий стоп включён. Чтобы начать продажи, явно возобновите нужные товары.':'Публикация проверяется отдельно от перевода в FBS.'} ${src.wb_warehouse_id?'Склад WB '+esc(src.wb_warehouse_id):''}</p><p>Здесь остаток всего товара на WB, а не отдельной партии приёмки.</p><div class="r77-actions"><button type="button" class="secondary" data-wb-receipt-all>Выбрать все товары приёмки</button><button type="button" class="primary" data-wb-action="RESUME_WB" data-wb-receipt>Возобновить выбранные на WB</button><button type="button" class="secondary" data-wb-action="CHECK" data-wb-receipt>Сверить WB</button></div>${src.rows.map(x=>`<div class="r77-receipt-row"><label><input type="checkbox" data-wb-receipt-code="${esc(x.barcode)}"> ${esc(x.barcode)}</label><div>${html(row(x.barcode,src))}</div></div>`).join('')}`;}
 async function actionImpl(action,code,receipt=false){const captured=scope();const codes=code?[code]:receipt?[...document.querySelectorAll('[data-wb-receipt-code]:checked')].map(x=>x.dataset.wbReceiptCode):[...selectedInventoryBarcodes];
 if(action==='CHECK'){busy=true;controls();try{await check();if(receipt&&scope()===captured)await window.FCReceiptHistory?.refreshCurrent();}finally{busy=false;controls();}return;}
 if(action!=='REPLAY'&&!codes.length)throw Error('Выберите товары');
 const names={RESUME_WB:'Возобновить продажи выбранных товаров на рабочем складе WB? Остальные товары сохранят свой стоп.',PAUSE_WB:'Остановить продажи выбранных товаров?',RETRY:'Повторить отправку свободного остатка? Стоп продаж сохранится.',TO_MAIN:'Перевести свободные остатки выбранных остановленных товаров на Основной склад? Резервы останутся в FBS.',WRITE_OFF:'Списать свободные остатки выбранных остановленных товаров? Операция сохранится в истории; резервы останутся.'};
 if(action!=='REPLAY'&&!confirm(names[action]))return;let req=null;
 if(['TO_MAIN','WRITE_OFF','REPLAY'].includes(action)){req=stored();if(req&&action!=='REPLAY')throw Error('Сначала проверьте результат предыдущей операции');if(!req){await load(true);if(captured!==scope())return;const ids=[...new Set(codes.flatMap(c=>row(c).sku_ids||[]))];if(!ids.length)throw Error('Товары не найдены. Обновите список');const reason=prompt(action==='TO_MAIN'?'Причина перевода на Основной склад:':'Причина списания:');if(!reason?.trim())return;req={...ctx(),sku_ids:ids,request_id:uid(),action,reason:reason.trim()};await check(ids);if(captured!==scope())return;stored(req);}}
 busy=true;error='';controls();let result;
 try{
  if(req){result=await api('/warehouse-ui/wb-sales/stopped-action',{method:'POST',body:JSON.stringify(req),timeoutMs:90000});if(captured===scope())stored(null);}
  else if(action==='RETRY'){result=await api('/warehouse-ui/wb-sales/retry',{method:'POST',body:JSON.stringify({...ctx(),barcodes:codes}),timeoutMs:60000});}
  else result=await api('/receiving/inventory-bulk',{method:'POST',body:JSON.stringify({...ctx(),action,barcodes:codes,wb_warehouse_id:null,all_inventory:false,performed_by:state.commercialSession?.username||'Администратор'}),timeoutMs:90000});
  if(captured!==scope())return;
  selectedInventoryBarcodes.clear();window.FCStockRooms?.invalidate();invalidate();await load(true);
  if(receipt)await window.FCReceiptHistory?.refreshCurrent();
  toast(req?`${action==='WRITE_OFF'?'Списано':'Переведено'} ${result.quantity} шт.`:result.message||'Задание сохранено. Ожидается подтверждение WB.');
 }catch(e){if(captured===scope()){if(req&&[400,403,404,409,422].includes(Number(e.status)))stored(null);error=e.message;paint();}throw e;}finally{busy=false;controls();}
 }
 async function action(...args){if(busy)return;busy=true;controls();try{return await actionImpl(...args);}finally{busy=false;controls();}}
 document.addEventListener('click',e=>{const b=e.target.closest('[data-wb-tab],[data-wb-action],[data-wb-receipt-all]');if(!b||b.disabled)return;e.preventDefault();if(b.dataset.wbTab){tab=b.dataset.wbTab;selectedInventoryBarcodes.clear();paint();return;}if(b.hasAttribute('data-wb-receipt-all')){document.querySelectorAll('[data-wb-receipt-code]').forEach(x=>x.checked=true);return;}action(b.dataset.wbAction,b.dataset.wbCode,b.hasAttribute('data-wb-receipt')).catch(x=>toast(x.message,true));});
 const oldGo=go;go=function(p){oldGo(p);if(state.activePage==='inventory'){load().then(()=>{if(scope()===owner&&Date.now()-lastCheck>60000){lastCheck=Date.now();check().catch(()=>{});}});}};
 document.addEventListener('change',e=>{if(['clientSelect','warehouseSelect'].includes(e.target.id)){ticket++;data=null;owner=scope();lastCheck=0;if(state.activePage==='inventory')setTimeout(()=>{load(true);check().catch(()=>{});},0);}},true);
 const baseApi=api;api=async function(path,options={}){const c=scope(),result=await baseApi(path,options);if(c===scope()&&options.method&&!['GET','HEAD'].includes(options.method.toUpperCase())&&!path.includes('/wb-sales/')&&(/inventory-bulk|\/actions\/|\/to-fbs|publish-fbs|structured-receipts.*commit/.test(path))){invalidate();if(state.activePage==='inventory')load(true);}return result;};
 setInterval(()=>{if(state.activePage==='inventory'&&!document.hidden&&!busy&&!fcUserEditing($('page-inventory'))){load(true);if(Date.now()-lastCheck>60000){lastCheck=Date.now();check().catch(()=>{});}}},10000);
 const style=document.createElement('style');style.textContent='.r77-tabs,.r77-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.r77-receipt-row{display:grid;grid-template-columns:minmax(160px,1fr) minmax(250px,3fr);gap:12px;padding:12px 0;border-bottom:1px solid #e6ebf3}#r77SalePanel{background:#f6f8fc;border:1px solid #dfe6f0;border-radius:14px;padding:16px;margin:12px 0}#r77SalePanel small{color:#536781}#r77SalePanel button{white-space:normal}';document.head.appendChild(style);
 window.FCWBSales={filter,cell:code=>html(row(code)),rowActions,load,check,action,receiptHTML,exportFilter:()=>tab};
})();

/* r91: PVZ receiving, defect journal, client portal and PDF barcode/CHZ preparation. */
(()=>{if(document.getElementById('fcPvzScript'))return;const s=document.createElement('script');s.id='fcPvzScript';s.src='/static/pvz.js?v=r145f18';s.onerror=()=>toast('Не удалось загрузить модуль ПВЗ. Обновите страницу Ctrl+F5',true);document.head.appendChild(s);})();

// FC_R106_FINAL_NAV_GUARD: r65/r77 wrap go() later, so apply this redirect last.
const r106FinalGo=go;
go=function(page){
  if(page==='fbspicks')page='masterdata';
  const result=r106FinalGo(page);
  if(page==='masterdata'){
    installR106MasterdataWorkspace();
    const n=$('r106PickNumber');if(n)n.value=localStorage.getItem(r106PickStorageKey())||'';
  }
  return result;
};


/* FC_R107_ASSEMBLY_HUB */
(()=>{
 const R=id=>document.getElementById(id);
 function setup(){
  R('r106PickingPanel')?.remove();document.querySelectorAll('[data-page="ship"]').forEach(n=>n.remove());const shp=R('page-ship');if(shp)shp.hidden=true;
  const page=R('page-warehousework');if(!page)return;const nav=document.querySelector('[data-page="warehousework"]');if(nav)nav.textContent='Сборка';if(R('r107AssemblyTabs'))return;
  [...page.querySelectorAll(':scope > .panel')].forEach((x,i)=>{if(i<2)x.hidden=true;});const pick=R('r62PickNumber')?.closest('.panel');if(!pick)return;pick.id='r107MainPickPanel';
  const tabs=document.createElement('div');tabs.id='r107AssemblyTabs';tabs.className='panel';tabs.innerHTML=`<div class="button-row"><button class="primary" type="button" data-r107-tab="fbs">Сборка FBS</button><button class="secondary" type="button" data-r107-tab="main">Сборка с Основного склада</button></div><div id="r107FbsPick" style="margin-top:14px"><div class="panel-head"><div><span class="kicker">FBS · TSD</span><h2>Назначения сборщикам</h2><p>Кому что назначено и сколько реально собрано.</p></div><button class="secondary" type="button" data-r107-fbs-refresh>Обновить</button></div><div id="r107FbsRows" class="table-wrap"></div></div><div id="r107MainIntro" hidden style="margin-top:14px"><div class="panel-head"><div><span class="kicker">ОСНОВНОЙ СКЛАД</span><h2>Сборка по Excel</h2><p>FBO, магазин, другой склад. Товар — по ШК и количеству; целые короба — отдельным шаблоном.</p></div></div><div class="button-row"><a class="secondary" href="/box-intake/templates/goods.xlsx">Шаблон: ШК + количество</a><a class="secondary" href="/box-intake/templates/boxes.xlsx">Шаблон: целые короба</a><button class="secondary" type="button" data-r68-open="pick">Сборка целых коробов</button></div><div class="r62-actions"><label>Назначение<select id="r107PickKind"><option>FBO</option><option>Магазин</option><option>Другой склад</option><option>Другое</option></select></label><label>Куда / комментарий<input id="r107PickDestination" placeholder="Например: FBO Коледино или Магазин №3"></label></div></div>`;page.prepend(tabs);
  async function loadFbs(){const body=R('r107FbsRows');if(!body)return;body.innerHTML='<div class="empty-state">Загружаем…</div>';try{const q=new URLSearchParams({client_id:state.clientId,warehouse_id:state.warehouseId,limit:'100'}),d=await api('/tsd/api/picking-results?'+q,{timeoutMs:15000}),rows=d.rows||d||[];body.innerHTML=`<table><thead><tr><th>Сборщик</th><th>Статус</th><th>Нужно</th><th>Собрано</th><th>Нет</th><th>Осталось</th><th>Дата</th></tr></thead><tbody>${rows.map(r=>`<tr><td><b>${esc(r.picker||'—')}</b></td><td>${esc(r.status||'—')}</td><td>${fmtQty(r.total_qty||0)}</td><td><b>${fmtQty(r.picked_qty||0)}</b></td><td>${fmtQty(r.missing_qty||0)}</td><td>${fmtQty(r.remaining_qty||0)}</td><td>${esc(r.assigned_at?new Date(r.assigned_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'}):'—')}</td></tr>`).join('')||'<tr><td colspan="7">Назначений пока нет</td></tr>'}</tbody></table>`;}catch(e){body.innerHTML=`<div class="empty-state bad">${esc(e.message)}</div>`;}}
  function tab(n){const f=n==='fbs';R('r107FbsPick').hidden=!f;R('r107MainIntro').hidden=f;pick.hidden=f;tabs.querySelectorAll('[data-r107-tab]').forEach(b=>b.className=b.dataset.r107Tab===n?'primary':'secondary');if(f)loadFbs();}
  tabs.addEventListener('click',e=>{const b=e.target.closest('[data-r107-tab],[data-r107-fbs-refresh]');if(!b)return;b.dataset.r107Tab?tab(b.dataset.r107Tab):loadFbs();});
  R('r62PickName')?.addEventListener('focus',()=>{const d=R('r107PickDestination')?.value.trim(),k=R('r107PickKind')?.value||'';if(d&&!R('r62PickName').value.trim())R('r62PickName').value=`${k}: ${d}`;});tab('fbs');
 }
 const prev=go;go=function(page){if(page==='ship'||page==='fbspicks')page='warehousework';const r=prev(page);if(page==='masterdata')setTimeout(()=>R('r106PickingPanel')?.remove(),0);if(page==='warehousework')setTimeout(setup,0);return r;};setTimeout(setup,0);
})();


/* FC_R145F7_ASSEMBLY_MAIN_TEMPLATES_READY_WRITEOFF */
(()=>{
 const R=id=>document.getElementById(id);
 function actor(){return (R('currentUser')?.textContent||'admin').trim()||'admin';}
 function install(){
  const page=R('page-warehousework');if(!page||R('r145f7AssemblyPanel'))return;
  const nav=document.querySelector('[data-page="warehousework"]');if(nav)nav.textContent='Сборка';titles.warehousework='Сборка';
  const panel=document.createElement('section');panel.id='r145f7AssemblyPanel';panel.className='panel';panel.innerHTML=`<div class="panel-head"><div><span class="kicker">ОСНОВНОЙ СКЛАД</span><h2>Шаблоны и сборка</h2><p>Все шаблоны вынесены из приёмки. По заданию программа формирует сборочный лист; готовый фактический файл можно сразу списать с Основного склада.</p></div></div>
  <div class="r68-templates">${[['boxes','Сборка коробов','Одна колонка: Короб.'],['goods','Сборка товара','ШК и количество.'],['goods_by_box','Сборка товара из коробов','Артикул, размер, ШК, количество и точный короб.'],['box_layout','Заполнение принятых коробов','Раскладка товара для приёмки.'],['fbs','Сборочное задание WB FBS','Номер в B1, ШК и количество.'],['transfer','Перевод Основной → FBS','ШК и количество; адрес необязателен.']].map(([k,t,d])=>`<article><h3>${t}</h3><p>${d}</p><a href="/box-intake/templates/${k}.xlsx">Скачать шаблон</a></article>`).join('')}</div>
  <div class="panel" style="margin-top:14px"><h3>Загрузить шаблон → создать сборочный лист Основного склада</h3><p>Для шаблонов «Сборка товара» и «Сборка товара из коробов». На этом шаге остаток не списывается: создаётся задание и маршрут по фактическим адресам.</p><div class="r62-actions"><label>Номер задания<input id="r145f7MainPickNumber" maxlength="120" placeholder="Например: FBO-25-09-01"></label><label>Excel задания<input id="r145f7MainPickFile" type="file" accept=".xlsx"></label><button id="r145f7MainPickCreate" class="primary" type="button">Загрузить и создать сборочный лист</button></div><div id="r145f7MainPickResult" class="muted"></div></div>
  <div class="panel" style="margin-top:14px"><h3>Сборка целых коробов → получить маршрут</h3><div class="r62-actions"><label>Номер задания<input id="r145f7BoxPickNumber" maxlength="120" placeholder="Например: К-001"></label><label>Excel с коробами<input id="r145f7BoxPickFile" type="file" accept=".xlsx"></label><button id="r145f7BoxPickCreate" class="primary" type="button">Получить сборочный лист</button></div><div id="r145f7BoxPickResult" class="muted"></div></div>
  <div class="panel" style="margin-top:14px"><h3>Готовый фактический файл → сразу списать с Основного</h3><p class="warn-box">Используйте только когда товар уже физически собран. Списание выполняется только если весь файл обеспечен свободным MAIN-остатком; резервы и FBS не забираются.</p><div class="r62-actions"><label>Номер / название списания<input id="r145f7ReadyNumber" maxlength="120" placeholder="Например: Отгрузка FBO 25.09"></label><label>Готовый Excel<input id="r145f7ReadyFile" type="file" accept=".xlsx"></label><button id="r145f7ReadyPost" class="danger" type="button">Загрузить и списать</button></div><p id="r145f7ReadyStatus" class="muted"></p></div>`;
  const tabs=R('r107AssemblyTabs');if(tabs)tabs.after(panel);else page.prepend(panel);
  R('r145f7MainPickCreate').onclick=async()=>{const n=R('r145f7MainPickNumber').value.trim(),f=R('r145f7MainPickFile').files?.[0],st=R('r145f7MainPickResult');if(!n||!f){toast('Укажите номер задания и Excel',true);return;}const fd=new FormData();fd.append('client_id',state.clientId);fd.append('warehouse_id',state.warehouseId);fd.append('task_number',n);fd.append('name',n);fd.append('created_by',actor());fd.append('file',f);st.textContent='Проверяем файл и строим сборочный лист…';try{const r=await api('/warehouse/picks/import',{method:'POST',body:fd,timeoutMs:120000});st.textContent=`Задание ${r.task_number||n}: запрошено ${Number(r.requested||0)} шт., распределено ${Number(r.allocated||0)} шт., нехватка ${Number(r.shortage||0)} шт.`;toast('Сборочный лист создан');if(typeof loadWarehouseWorkR62==='function')await loadWarehouseWorkR62();}catch(e){st.textContent=e.message;toast(e.message,true);}};
  R('r145f7BoxPickCreate').onclick=async()=>{const n=R('r145f7BoxPickNumber').value.trim(),f=R('r145f7BoxPickFile').files?.[0],st=R('r145f7BoxPickResult');if(!n||!f){toast('Укажите номер задания и Excel',true);return;}const fd=new FormData();fd.append('client_id',state.clientId);fd.append('warehouse_id',state.warehouseId);fd.append('number',n);fd.append('file',f);st.textContent='Формируем маршрут…';try{const r=await api('/box-intake/picks',{method:'POST',body:fd,timeoutMs:120000});st.innerHTML=`Готово: найдено ${Number(r.found||0)} из ${Number(r.requested||0)} коробов. <a href="/box-intake/picks/${encodeURIComponent(r.id)}/export.xlsx">Скачать сборочный лист Excel</a>`;}catch(e){st.textContent=e.message;toast(e.message,true);}};
  R('r145f7ReadyPost').onclick=async()=>{const n=R('r145f7ReadyNumber').value.trim(),f=R('r145f7ReadyFile').files?.[0],st=R('r145f7ReadyStatus');if(!n||!f){toast('Укажите название и готовый Excel',true);return;}if(!confirm(`Списать по готовому файлу «${n}» с Основного склада? Это физическая отгрузка.`))return;const fd=new FormData();fd.append('client_id',state.clientId);fd.append('warehouse_id',state.warehouseId);fd.append('task_number',n);fd.append('name',n);fd.append('created_by',actor());fd.append('file',f);st.textContent='Проверяем свободный MAIN-остаток и проводим файл…';try{const r=await api('/warehouse/picks/import-ready-writeoff',{method:'POST',body:fd,timeoutMs:120000});if(r.written_off){st.textContent=`Списано ${Number(r.posted||r.allocated||0)} шт. · операция ${r.operation_id||'создана'}`;toast('Готовый файл списан с Основного склада');window.FCStockRooms?.invalidate?.();}else{st.textContent=`Не списано: нехватка ${Number(r.shortage||0)} шт. Создано задание ${r.task_number||n} для проверки.`;toast('Есть нехватка — списания не было',true);}}catch(e){st.textContent=e.message;toast(e.message,true);}};
 }
 const prev=go;go=function(p){const r=prev(p);if(p==='warehousework')setTimeout(install,0);return r;};
 const timer=setInterval(()=>{if(R('page-warehousework')){install();clearInterval(timer);}},300);setTimeout(()=>clearInterval(timer),15000);
})();

/* FC_R145F9_NAV_RECOVERY_AND_PRINT_SETTINGS */
(()=>{
  const affected=new Set(['palletreceive','warehousework','defectstorage','printsettings']);
  function directActivate(page){
    const target=document.getElementById('page-'+page);if(!target)return false;
    try{state.activePage=page;fcR20Remember('fc_active_page',page);}catch(_){ }
    document.querySelectorAll('.page').forEach(x=>x.classList.toggle('active',x===target));
    document.querySelectorAll('#mainNav [data-page]').forEach(x=>x.classList.toggle('active',x.dataset.page===page));
    const title=document.getElementById('pageTitle');if(title)title.textContent=(typeof titles!=='undefined'&&titles[page])||page;
    if(['palletreceive','warehousework','defectstorage'].includes(page)){
      try{state.warehouseMenuOpen=true;fcR20Remember('fc_warehouse_menu_open','1');if(typeof applyWarehouseMenu==='function')applyWarehouseMenu();}catch(_){ }
    }
    return true;
  }
  function loadScriptOnce(id,src){return new Promise((resolve,reject)=>{const existing=document.getElementById(id);if(existing){if(existing.dataset.loaded==='1'||document.getElementById('page-defectstorage'))return resolve(existing);existing.addEventListener('load',()=>resolve(existing),{once:true});existing.addEventListener('error',()=>reject(new Error('Не удалось загрузить модуль')),{once:true});return;}const s=document.createElement('script');s.id=id;s.src=src;s.onload=()=>{s.dataset.loaded='1';resolve(s)};s.onerror=()=>reject(new Error('Не удалось загрузить '+src));document.head.appendChild(s);});}
  async function ensureSpecialPage(page){
    if(page==='warehousework'&&!document.getElementById('page-warehousework')&&typeof window.loadWarehouseWorkR62==='function'){
      try{await window.loadWarehouseWorkR62();}catch(e){console.warn('r145f9 assembly bootstrap',e);}
    }
    if(page==='defectstorage'&&!document.getElementById('page-defectstorage')){
      try{await loadScriptOnce('fcR145f9DefectStorage','/static/defect-storage.js?v=r145f9');}catch(e){console.warn('r145f9 defect bootstrap',e);}
    }
    return document.getElementById('page-'+page);
  }
  async function postOpen(page){
    try{
      if(page==='palletreceive'){
        if(typeof palletReceiptShowHome==='function'&&typeof currentPalletReceiptId!=='undefined'){
          const view=localStorage.getItem('fc_pallet_receipt_view');
          if(view==='detail'&&currentPalletReceiptId&&typeof palletReceiptShowDetail==='function')palletReceiptShowDetail();else palletReceiptShowHome();
        }
        if(typeof loadPalletReceipts==='function')await loadPalletReceipts();
      }
      if(page==='warehousework'&&typeof window.loadWarehouseWorkR62==='function')await window.loadWarehouseWorkR62();
      if(page==='defectstorage')await window.FCDefectStorage?.refresh?.();
      if(page==='printsettings'){
        if(typeof loadPrintSettingsUi==='function')loadPrintSettingsUi();
        if(typeof addPrintAgentHelpLink==='function')addPrintAgentHelpLink();
        const mode=document.getElementById('wbPrintMode'),station=document.getElementById('wbPrintStation');
        if(mode&&typeof state!=='undefined')mode.value=state.wbPrintMode||localStorage.getItem('fc_wb_print_mode')||'agent';
        if(station&&typeof state!=='undefined')station.value=state.wbPrintStation||localStorage.getItem('fc_wb_print_station')||'PACK-01';
      }
    }catch(e){console.warn('r145f9 page post-open',page,e);if(typeof toast==='function')toast(e.message||String(e),true);}
  }
  async function safeOpen(page){
    try{if(typeof go==='function')go(page);}catch(e){console.error('r145f9 wrapped go failed',page,e);}
    let target=await ensureSpecialPage(page);
    if(!target){if(typeof toast==='function')toast('Не удалось открыть раздел «'+((typeof titles!=='undefined'&&titles[page])||page)+'». Обновите страницу Ctrl+F5.',true);return;}
    if(!target.classList.contains('active'))directActivate(page);
    await postOpen(page);
  }
  function bind(){
    const nav=document.getElementById('mainNav');if(!nav||nav.dataset.r145f9NavRecovery==='1')return;nav.dataset.r145f9NavRecovery='1';
    nav.addEventListener('click',e=>{const b=e.target.closest('[data-page]');if(!b||!affected.has(b.dataset.page))return;e.preventDefault();e.stopImmediatePropagation();safeOpen(b.dataset.page);},true);
    // A static assembly nav is intentionally present in index.html. If older cached markup is still shown, add it here too.
    if(!nav.querySelector('[data-page="warehousework"]')){const receive=nav.querySelector('[data-page="palletreceive"]'),b=document.createElement('button');b.type='button';b.dataset.page='warehousework';b.innerHTML='<span>СБ</span>Сборка';receive?.after(b);}
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bind,{once:true});else bind();
  window.FCR145F9Navigation={open:safeOpen,directActivate};
})();


/* FC_R145F12_UNIFIED_STORAGE_TSD_EXISTING_BOX */

// r145f24: read-only history and FBS diagnostic; warehouse mutations stay unchanged.
window.FCR24History.install({context:()=>({client_id:state.clientId,warehouse_id:state.warehouseId})});

window.addEventListener('fc26-draft-state',e=>{if(e.detail?.key===manualReceivingDraftKey()&&!fc26DraftSaving)renderManualReceivingDraft();});
