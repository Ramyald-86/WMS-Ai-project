/* Mobile adapter for the existing r73 receiving, catalog and stock workflows. */
(()=>{
  'use strict';
  const nav=document.getElementById('mainNav');
  if(nav&&!document.getElementById('tsdInstallLink')){
    const b=document.createElement('button');b.id='tsdInstallLink';b.type='button';b.textContent='Приложение ТСД';
    b.onclick=()=>location.href='/tsd/install';nav.appendChild(b);
  }
  if(new URLSearchParams(location.search).get('tsd')!=='1')return;
  document.body.classList.add('tsd-workplace');
  const css=document.createElement('link');css.rel='stylesheet';css.href='/static/tsd-workplace.css?v=r74';document.head.appendChild(css);
  const bar=document.createElement('nav');bar.className='tsd-workplace-bar';bar.setAttribute('aria-label','Рабочее место ТСД');
  bar.innerHTML='<a href="/tsd">← ТСД</a><button type="button" data-tsd-work="receivinghome">Приёмка</button><button type="button" data-tsd-work="stockhome">Остатки</button><button type="button" data-tsd-work="containers">Короба</button>';
  document.body.prepend(bar);
  bar.addEventListener('click',e=>{const b=e.target.closest('[data-tsd-work]');if(b)go(b.dataset.tsdWork);});
  const requested=new URLSearchParams(location.search).get('work');
  const pages=new Set(['boxintake','receive','boxpicks','stockhome','containers','receivinghome']);
  let opened=false;
  function open(){
    if(opened||!window.FCTsdDesktopReady||!window.FCTsdReceivingReady)return;
    opened=true;
    if(requested==='boxpicks'){
      const b=document.querySelector('[data-r68-open="pick"]');
      if(b)b.click();else go('boxintake');
    }else go(pages.has(requested)?requested:'receivinghome');
    if(requested==='receive')document.getElementById('manualReceiveBox')?.focus();
  }
  window.addEventListener('fc:desktop-ready',open);window.addEventListener('fc:receiving-ready',open);open();
  window.FCNativeScan=raw=>{
    const code=String(raw||'').replace(/[\r\n\t]+$/g,'').trim();if(!code)return;
    // Dialog fields get precedence over the receiving scanner.
    let target=document.activeElement;
    const editing=target?.matches('input:not([type=checkbox]):not([type=number]):not([type=file]),textarea')&&!target.readOnly&&!target.disabled;
    if(!editing){
      if(state.activePage==='boxintake'&&!document.getElementById('r68ScanForm')?.hidden)target=document.getElementById('r68Scan');
      else if(state.activePage==='receive')target=document.getElementById(manualReceivingStorageContext?'receiveSku':'manualReceiveBox');
      else{toast('Выберите поле для сканирования',true);return;}
    }
    if(!target||target.disabled)return;
    target.value=code;
    if(target.id==='r68Scan'){window.FCBoxIntake?.queueScan();return;}
    target.dispatchEvent(new Event('input',{bubbles:true}));
    target.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true,cancelable:true}));
  };
  if(window.TsdNative&&typeof window.TsdNative.saveFile==='function'){
    saveBlob=function(blob,name){
      if(blob.size>32*1024*1024){toast('Файл больше 32 МБ. Скачайте его на компьютере.',true);return;}
      const reader=new FileReader();reader.onload=()=>window.TsdNative.saveFile(String(reader.result).split(',')[1],name,blob.type||'application/octet-stream');reader.onerror=()=>toast('Не удалось подготовить файл к сохранению',true);reader.readAsDataURL(blob);
    };
  }
})();
