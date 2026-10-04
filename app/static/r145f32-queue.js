/* FC_R145F32_LOCAL_FIRST_QUEUE */
(function(root){
'use strict';
const DB='fulfillcore-r145f32-fast-queue',STORE='events';let dbp=null;
const clone=x=>JSON.parse(JSON.stringify(x));
function open(){if(dbp)return dbp;dbp=new Promise((resolve,reject)=>{let r;try{r=indexedDB.open(DB,1);}catch(e){reject(e);return;}r.onupgradeneeded=()=>{const db=r.result;if(!db.objectStoreNames.contains(STORE)){const s=db.createObjectStore(STORE,{keyPath:'key'});s.createIndex('scope','scope',{unique:false});}};r.onsuccess=()=>resolve(r.result);r.onerror=()=>reject(r.error);});return dbp;}
function tx(mode,run){return open().then(db=>new Promise((resolve,reject)=>{const t=db.transaction(STORE,mode),s=t.objectStore(STORE);let out;try{out=run(s,t);}catch(e){reject(e);return;}t.oncomplete=()=>resolve(out);t.onabort=()=>reject(t.error||new Error('queue aborted'));t.onerror=()=>{};}));}
async function put(scope,event){const row={...clone(event),scope:String(scope),key:String(scope)+'|'+String(event.id)};await tx('readwrite',s=>s.put(row));return row;}
async function list(scope){const db=await open();return new Promise((resolve,reject)=>{const t=db.transaction(STORE,'readonly'),idx=t.objectStore(STORE).index('scope'),r=idx.getAll(String(scope));r.onsuccess=()=>resolve((r.result||[]).sort((a,b)=>Number(a.created_at||0)-Number(b.created_at||0)));r.onerror=()=>reject(r.error);});}
async function remove(scope,ids){const set=new Set((ids||[]).map(String));if(!set.size)return;const rows=await list(scope);await tx('readwrite',s=>{for(const r of rows)if(set.has(String(r.id)))s.delete(r.key);});}
async function clear(scope){const rows=await list(scope);await tx('readwrite',s=>{for(const r of rows)s.delete(r.key);});}
async function count(scope){return (await list(scope)).length;}
root.FC32Queue={put,list,remove,clear,count,version:32};
})(window);
