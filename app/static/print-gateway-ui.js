/* Print Gateway §7.3 — admin UI on printsettings + mobile station override for packing. */
(function () {
  'use strict';

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  async function api(path, opt) {
    if (typeof window.api === 'function') return window.api(path, opt || {});
    const r = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...((opt && opt.headers) || {}) },
      ...opt,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText || 'Ошибка API');
    return data;
  }
  function toast(msg, bad) {
    if (typeof window.toast === 'function') return window.toast(msg, bad);
    console.log(bad ? 'ERR' : 'OK', msg);
  }
  function deviceId() {
    try {
      if (window.state && state.deviceId) return state.deviceId;
      return localStorage.getItem('fc_device_id') || '';
    } catch (_) { return ''; }
  }

  async function refreshMobileBindBadge() {
    const el = $('pgwMobileBindStatus');
    const dev = deviceId();
    if (!dev) return;
    try {
      const r = await api('/printing/gateway/mobile-station/for-device?device_id=' + encodeURIComponent(dev), { timeoutMs: 5000 });
      if (el) {
        el.textContent = r.bound
          ? ('ТСД привязан к ' + r.station_code + ' · печать пойдёт на мобильный принтер')
          : ('Устройство ' + dev + ' · мобильный принтер не привязан');
        el.classList.toggle('ok', !!r.bound);
      }
      if (r.bound && r.station_code && window.state) {
        state.wbPrintStation = r.station_code;
        const input = $('wbPrintStation');
        if (input) input.value = r.station_code;
      }
    } catch (_) { /* gateway may be warming up */ }
  }

  function ensurePanel() {
    const page = $('page-printsettings');
    if (!page || page.querySelector('[data-print-gateway]')) return;
    const panel = document.createElement('section');
    panel.className = 'panel';
    panel.dataset.printGateway = '1';
    panel.innerHTML = `
      <div class="panel-head"><div><span class="kicker">PRINT GATEWAY · ТЗ §7.3</span>
      <h2>Маршрутизация печати</h2></div>
      <button type="button" class="secondary" id="pgwRefresh">Обновить</button></div>
      <p class="muted">Gateway выбирает шаблон и принтер (LAN / Wi‑Fi / USB Edge / Mobile) и ставит задание в очередь Print Agent.
      Повторная печать сохраняет тот же business id — новый ШК/HU не создаётся.</p>
      <p id="pgwMobileBindStatus" class="muted">Проверяем привязку устройства…</p>
      <div class="master-grid">
        <div class="panel compact-form">
          <span class="kicker">ПРИНТЕРЫ</span>
          <div id="pgwPrinters" class="empty-state">Загрузка…</div>
          <label>Код<input id="pgwPrCode" placeholder="PACK-02"></label>
          <label>Имя<input id="pgwPrName" placeholder="Упаковка 2"></label>
          <label>Станция агента<input id="pgwPrStation" placeholder="PACK-02"></label>
          <label>Тип<select id="pgwPrKind"><option>LAN</option><option>WIFI</option><option>USB_EDGE</option><option>MOBILE</option></select></label>
          <label class="checkbox"><input type="checkbox" id="pgwPrDefault"> По умолчанию</label>
          <button type="button" class="primary" id="pgwPrSave">Сохранить принтер</button>
        </div>
        <div class="panel compact-form">
          <span class="kicker">МАРШРУТЫ</span>
          <div id="pgwRoutes" class="empty-state">Загрузка…</div>
          <label>Тип этикетки<input id="pgwRtLabel" placeholder="WB_ORDER / HU / BOX"></label>
          <label>Workstation<input id="pgwRtWs" placeholder="PACK-01"></label>
          <label>Зона<input id="pgwRtZone" placeholder="PACK"></label>
          <label>Printer ID<input id="pgwRtPrinterId" placeholder="uuid принтера"></label>
          <label>Приоритет<input id="pgwRtPrio" type="number" value="100"></label>
          <button type="button" class="primary" id="pgwRtSave">Добавить маршрут</button>
        </div>
        <div class="panel compact-form">
          <span class="kicker">MOBILE PRINT STATION</span>
          <div id="pgwMobileList" class="empty-state">Загрузка…</div>
          <label>Станция агента<input id="pgwMobStation" placeholder="MOBILE-01" value="MOBILE-01"></label>
          <label>Имя принтера<input id="pgwMobName" placeholder="Тележка A"></label>
          <button type="button" class="primary" id="pgwMobCreate">Создать QR принтера</button>
          <div id="pgwMobQr" class="result-card mt18 hidden"></div>
        </div>
        <div class="panel compact-form">
          <span class="kicker">EDGE / ШАБЛОНЫ / ОЧЕРЕДЬ</span>
          <div id="pgwEdges" class="empty-state">—</div>
          <div id="pgwTemplates" class="empty-state">—</div>
          <label>Business id для reprint<input id="pgwReprintBid" placeholder="HU-… / order id"></label>
          <label>Причина reprint<input id="pgwReprintReason" placeholder="Замятие бумаги"></label>
          <button type="button" class="secondary" id="pgwReprint">Reprint (тот же id)</button>
          <div id="pgwJobs" class="empty-state mt18">Очередь gateway…</div>
        </div>
      </div>`;
    page.appendChild(panel);

    $('pgwRefresh')?.addEventListener('click', () => loadAll().catch(e => toast(e.message, true)));
    $('pgwPrSave')?.addEventListener('click', async () => {
      try {
        const body = {
          code: ($('pgwPrCode').value || '').trim(),
          name: ($('pgwPrName').value || '').trim() || ($('pgwPrCode').value || '').trim(),
          station_code: ($('pgwPrStation').value || $('pgwPrCode').value || '').trim(),
          kind: $('pgwPrKind').value,
          is_default: !!$('pgwPrDefault').checked,
        };
        if (!body.code || !body.station_code) throw new Error('Укажите код и станцию');
        await api('/printing/gateway/printers', { method: 'POST', body: JSON.stringify(body) });
        toast('Принтер сохранён');
        await loadAll();
      } catch (e) { toast(e.message, true); }
    });
    $('pgwRtSave')?.addEventListener('click', async () => {
      try {
        const body = {
          priority: Number($('pgwRtPrio').value || 100),
          label_type: ($('pgwRtLabel').value || '').trim() || null,
          workstation_code: ($('pgwRtWs').value || '').trim() || null,
          zone_code: ($('pgwRtZone').value || '').trim() || null,
          printer_id: ($('pgwRtPrinterId').value || '').trim(),
        };
        if (!body.printer_id) throw new Error('Нужен printer_id');
        await api('/printing/gateway/routes', { method: 'POST', body: JSON.stringify(body) });
        toast('Маршрут добавлен');
        await loadAll();
      } catch (e) { toast(e.message, true); }
    });
    $('pgwMobCreate')?.addEventListener('click', async () => {
      try {
        const r = await api('/printing/gateway/mobile-station', {
          method: 'POST',
          body: JSON.stringify({
            station_code: ($('pgwMobStation').value || 'MOBILE-01').trim(),
            printer_name: ($('pgwMobName').value || '').trim() || null,
            warehouse_id: (window.state && state.warehouseId) || null,
          }),
        });
        const box = $('pgwMobQr');
        box.classList.remove('hidden');
        box.innerHTML = `<b>QR принтера</b><span>${esc(r.qr_payload || r.printer_qr)}</span>
          <small>Отсканируйте на ТСД в «Привязка принтера». Станция агента: <b>${esc(r.station_code)}</b></small>
          <button type="button" class="mini secondary" data-copy="${esc(r.qr_payload || r.printer_qr)}">Копировать</button>`;
        box.querySelector('[data-copy]')?.addEventListener('click', async (ev) => {
          try {
            await navigator.clipboard.writeText(ev.currentTarget.dataset.copy);
            toast('QR скопирован');
          } catch (_) { toast('Скопируйте вручную'); }
        });
        toast('Mobile Print Station создан');
        await loadAll();
      } catch (e) { toast(e.message, true); }
    });
    $('pgwReprint')?.addEventListener('click', async () => {
      try {
        const business_id = ($('pgwReprintBid').value || '').trim();
        const reason = ($('pgwReprintReason').value || '').trim();
        if (!business_id || reason.length < 3) throw new Error('Нужны business id и причина');
        const r = await api('/printing/gateway/reprint', {
          method: 'POST',
          body: JSON.stringify({
            business_id,
            reason,
            device_id: deviceId() || null,
            preferred_station: (window.state && state.wbPrintStation) || null,
          }),
        });
        toast('Reprint → ' + (r.station_code || '') + ' · тот же id');
        await loadAll();
      } catch (e) { toast(e.message, true); }
    });
  }

  async function loadAll() {
    ensurePanel();
    await refreshMobileBindBadge();
    const [printers, routes, edges, templates, mobile, jobs] = await Promise.all([
      api('/printing/gateway/printers', { timeoutMs: 8000 }),
      api('/printing/gateway/routes', { timeoutMs: 8000 }),
      api('/printing/gateway/edges', { timeoutMs: 8000 }),
      api('/printing/gateway/templates', { timeoutMs: 8000 }),
      api('/printing/gateway/mobile-stations', { timeoutMs: 8000 }),
      api('/printing/gateway/jobs?limit=15', { timeoutMs: 8000 }),
    ]);
    const pr = $('pgwPrinters');
    if (pr) {
      const items = printers.items || [];
      pr.innerHTML = items.length
        ? `<div class="table-wrap"><table><thead><tr><th>Код</th><th>Станция</th><th>Тип</th><th>Default</th><th>ID</th></tr></thead><tbody>
          ${items.map(p => `<tr><td><b>${esc(p.code)}</b><br><small>${esc(p.name)}</small></td><td>${esc(p.station_code)}</td><td>${esc(p.kind)}</td><td>${p.is_default ? '✓' : ''}</td><td><code>${esc(p.id)}</code></td></tr>`).join('')}
          </tbody></table></div>`
        : '<div class="empty-state">Нет принтеров</div>';
    }
    const rt = $('pgwRoutes');
    if (rt) {
      const items = routes.items || [];
      rt.innerHTML = items.length
        ? `<ul>${items.slice(0, 40).map(r =>
          `<li>#${esc(r.priority)} ${esc(r.label_type || '*')} → <b>${esc(r.printer_code)}</b> (${esc(r.station_code)})${r.workstation_code ? ' · ws ' + esc(r.workstation_code) : ''}${r.zone_code ? ' · zone ' + esc(r.zone_code) : ''}</li>`
        ).join('')}</ul>`
        : '<div class="empty-state">Нет маршрутов</div>';
    }
    const ed = $('pgwEdges');
    if (ed) {
      ed.innerHTML = '<b>Edge</b>: ' + ((edges.items || []).map(e => esc(e.code) + (e.host ? ' @' + esc(e.host) : '')).join(', ') || '—');
    }
    const tp = $('pgwTemplates');
    if (tp) {
      tp.innerHTML = '<b>Шаблоны</b>: ' + ((templates.items || []).map(t => esc(t.code)).join(', ') || '—');
    }
    const ml = $('pgwMobileList');
    if (ml) {
      const items = mobile.items || [];
      ml.innerHTML = items.length
        ? `<ul>${items.slice(0, 20).map(m =>
          `<li><code>${esc(m.printer_qr)}</code> → ${esc(m.station_code)}${m.bound_device_id ? ' · bound ' + esc(m.bound_device_id) : ''}</li>`
        ).join('')}</ul>`
        : '<div class="empty-state">Пока нет mobile QR</div>';
    }
    const jb = $('pgwJobs');
    if (jb) {
      const items = jobs.items || [];
      jb.innerHTML = items.length
        ? `<div class="table-wrap"><table><thead><tr><th>Когда</th><th>Тип</th><th>Business</th><th>Станция</th><th>Статус</th><th>Reprint</th></tr></thead><tbody>
          ${items.map(j => `<tr><td>${esc((j.created_at || '').replace('T', ' ').slice(0, 19))}</td><td>${esc(j.label_type)}</td><td>${esc(j.business_id || '—')}</td><td>${esc(j.station_code)}</td><td>${esc(j.status)}</td><td>${j.is_reprint ? 'да' : ''}</td></tr>`).join('')}
          </tbody></table></div>`
        : '<div class="empty-state">Очередь gateway пуста</div>';
    }
  }

  function boot() {
    ensurePanel();
    const page = $('page-printsettings');
    if (page && page.classList.contains('active')) {
      loadAll().catch(e => console.warn('print-gateway', e));
    }
    document.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-page="printsettings"]');
      if (btn) setTimeout(() => loadAll().catch(() => {}), 50);
    });
    refreshMobileBindBadge();
    setInterval(refreshMobileBindBadge, 60000);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
