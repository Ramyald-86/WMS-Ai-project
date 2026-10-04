/* r145f25: display-only grouping, public addresses and opt-in audio. */
(function(root){
 'use strict';
 const str=v=>String(v==null?'':v),esc=v=>str(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const tech=/^(?:IN-[0-9a-f]+-|FCLOC-|FCBOX-|[0-9a-f]{8}-[0-9a-f-]{27}$)/i;
 function publicCode(x){
   if(x==null)return '';if(typeof x!=='object')x={code:str(x)};
   for(const k of ['display_code','public_code'])if(x[k]&&!tech.test(str(x[k])))return str(x[k]);
   const code=str(x.code||x.location_code||''),p=code.match(/(?:^|-)PAL-(\d+)$/i);
   if(p)return 'PAL-'+String(Number(p[1])).padStart(3,'0');
   const sec=code.match(/(?:^|-)SEC-(\d+)$/i);if(sec)return 'SEC-'+String(Number(sec[1])).padStart(3,'0');
   if(code&&!tech.test(code))return code;
   for(const k of ['display_name','name'])if(x[k]&&!tech.test(str(x[k]))&&(!code||!str(x[k]).includes(code)))return str(x[k]);
   return '\u0410\u0434\u0440\u0435\u0441 \u0431\u0435\u0437 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u044f';
 }
 function publicText(value){return str(value).replace(/IN-[0-9a-f]+-((?:(?:SEC|AISLE|ROW)-[^-\s\u2192\u00b7]+-)*(?:PAL|SEC|ROW|AISLE)-\d+)/gi,(_,p)=>{const bits=p.match(/(?:SEC|AISLE|ROW|PAL)-[^-\s]+/gi)||[];return bits.map(b=>{const [k,v]=b.split('-');return /^(PAL|SEC)$/i.test(k)?k.toUpperCase()+'-'+(/^\d+$/.test(v)?String(Number(v)).padStart(3,'0'):v):/^ROW$/i.test(k)?'\u0420\u044f\u0434 '+Number(v):'\u041f\u0440\u043e\u0445\u043e\u0434 '+Number(v);}).join(' \u2192 ');}).replace(/FCLOC-[0-9A-F-]+/gi,'\u0410\u0434\u0440\u0435\u0441 \u0431\u0435\u0437 \u043d\u0430\u0437\u0432\u0430\u043d\u0438\u044f');}
 function groupBoxes(input,scope=''){
   /* FC_R145F44_EXPLICIT_BOX_IDENTITY
      An explicit physical box code/name is authoritative. A stale UUID from
      an earlier async context must never merge BOX_A rows into BOX_B. */
   const rows=Array.isArray(input)?input:[],groups=new Map();
   const namespace=r=>[r.client_id||'',r.warehouse_id||'',scope].join('|');
   const explicit=r=>str(r.container_code||r.container_barcode||r.container_name||r.box_name||'').trim();
   const fold=v=>str(v).trim().toLocaleLowerCase('ru-RU');
   for(const r of rows){
     const ns=namespace(r),name=explicit(r),loc=str(r.location_id||r.location_code||'');
     const id=name?'boxcode:'+fold(name):(r.container_id?'boxid:'+str(r.container_id):'cell:'+loc);
     const key=ns+'|'+id;let g=groups.get(key);
     if(!g){g={key,name:str(r.container_name||r.box_name||name)||(r.location_display||publicCode({code:r.location_code})),isBox:!!(name||r.container_id),rows:[],total:0,locations:new Set(),first:groups.size};groups.set(key,g);}
     g.rows.push(r);g.total+=Number(r.quantity||0);if(r.storage_display||r.location_display||r.location_code)g.locations.add(publicText(r.storage_display||r.location_display||r.location_code));
   }
   return [...groups.values()].sort((a,b)=>a.name.localeCompare(b.name,'ru',{numeric:true,sensitivity:'base'})||a.first-b.first).map(g=>({...g,locations:[...g.locations]}));
 }
 function receiptGroups(receipt){
   const places=new Map((receipt?.places||[]).map(p=>[str(p.id),p])),lines=[];
   for(const l of receipt?.lines||[]){const p=places.get(str(l.place_id))||{},isBox=p.target_kind==='BOX';lines.push({...l,container_id:isBox?p.container_id:null,container_code:isBox?p.target_code:null,container_name:isBox?p.display_name:null,location_id:p.location_id,location_code:p.location_code||p.target_code,location_display:p.location_display,storage_display:p.location_display});}
   return groupBoxes(lines,receipt?.id||'').map(g=>{const m=new Map();for(const l of g.rows){const k=[l.sku_id,l.barcode].join('|');let a=m.get(k);if(!a){a={...l,quantity:0,scan_times:[]};m.set(k,a);}a.quantity+=Number(l.quantity||0);if(l.scanned_at)a.scan_times.push(l.scanned_at);}return {...g,items:[...m.values()]};});
 }
 let audio=null,unlocked=false,enabled=true,lastVoice=0,utterance=null;
 try{enabled=localStorage.getItem('fc25_audio')!=='off';}catch(_){}
 function unlock(){unlocked=true;try{audio=audio||new (root.AudioContext||root.webkitAudioContext)();audio.resume?.();}catch(_){} }
 function beep(bad){if(!enabled||!unlocked)return;try{audio=audio||new(root.AudioContext||root.webkitAudioContext)();const o=audio.createOscillator(),g=audio.createGain();o.connect(g);g.connect(audio.destination);o.frequency.value=bad?220:850;g.gain.value=.035;o.start();o.stop(audio.currentTime+(bad?.20:.07));}catch(_){} }
 function speak(text,bad=false){if(!enabled)return;beep(bad);if(!unlocked||!root.speechSynthesis||typeof root.SpeechSynthesisUtterance!=='function')return;
   const now=Date.now();if(now-lastVoice<80&&!bad)return;lastVoice=now;
   try{root.speechSynthesis.cancel();utterance=new root.SpeechSynthesisUtterance(text);utterance.lang='ru-RU';utterance.rate=2.4;const v=root.speechSynthesis.getVoices().find(v=>/^ru(?:-|_)/i.test(v.lang)||v.lang==='ru');if(v)utterance.voice=v;root.speechSynthesis.speak(utterance);}catch(_){}
 }
 function audioToggle(host){if(!host||host.querySelector('[data-fc25-sound]'))return;const b=document.createElement('button');b.type='button';b.className='secondary fc25-sound';b.dataset.fc25Sound='1';const label=()=>{b.textContent=enabled?'\u0417\u0432\u0443\u043a: \u0432\u043a\u043b.':'\u0417\u0432\u0443\u043a: \u0432\u044b\u043a\u043b.';b.setAttribute('aria-pressed',str(enabled));};label();b.onclick=()=>{unlock();enabled=!enabled;try{localStorage.setItem('fc25_audio',enabled?'on':'off');}catch(_){}document.querySelectorAll('[data-fc25-sound]').forEach(x=>{x.textContent=enabled?'\u0417\u0432\u0443\u043a: \u0432\u043a\u043b.':'\u0417\u0432\u0443\u043a: \u0432\u044b\u043a\u043b.';x.setAttribute('aria-pressed',str(enabled));});if(enabled)speak('\u0417\u0432\u0443\u043a \u0432\u043a\u043b\u044e\u0447\u0451\u043d');};host.prepend(b);}
 if(typeof document!=='undefined'){document.addEventListener('pointerdown',unlock,{once:true});document.addEventListener('keydown',unlock,{once:true});}
 root.FC25={esc,publicCode,publicText,groupBoxes,receiptGroups,speak,beep,audioToggle,version:25};
 if(typeof module!=='undefined'&&module.exports)module.exports=root.FC25;
})(typeof window!=='undefined'?window:globalThis);
