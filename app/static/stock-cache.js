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
