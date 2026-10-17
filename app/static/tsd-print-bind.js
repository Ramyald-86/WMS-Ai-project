/* TSD Mobile Print Station bind (Print Gateway §7.3). */
(function () {
  'use strict';
  function $(id) { return document.getElementById(id); }
  function deviceId() {
    try {
      let id = localStorage.getItem('fc_tsd_device') || localStorage.getItem('fc_device_id');
      if (!id) {
        id = 'TSD-' + Math.random().toString(36).slice(2, 10).toUpperCase();
        localStorage.setItem('fc_tsd_device', id);
      }
      return id;
    } catch (_) { return 'TSD-UNKNOWN'; }
  }
  async function api(path, opt) {
    if (typeof window.api === 'function') return window.api(path, opt || {});
    const r = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...((opt && opt.headers) || {}) },
      ...opt,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((data && data.detail) || r.statusText || 'Ошибка');
    return data;
  }
  function show(name) {
    if (typeof window.show === 'function') return window.show(name);
    document.querySelectorAll('main .screen').forEach((el) => {
      el.classList.toggle('active', el.id === name);
    });
  }
  async function refreshStatus() {
    const box = $('printBindStatus');
    if (!box) return;
    try {
      const r = await api('/printing/gateway/mobile-station/for-device?device_id=' + encodeURIComponent(deviceId()));
      box.textContent = r.bound
        ? ('Привязано к станции ' + r.station_code + ' · устройство ' + deviceId())
        : ('Нет привязки · устройство ' + deviceId());
      box.classList.toggle('ok', !!r.bound);
    } catch (e) {
      box.textContent = 'Gateway недоступен: ' + (e.message || e);
    }
  }
  async function bind() {
    const msg = $('printBindMsg');
    const btn = $('printBindGo');
    const qr = ($('printBindQr')?.value || '').trim();
    if (!qr) {
      if (msg) msg.textContent = 'Отсканируйте QR принтера';
      return;
    }
    if (btn) btn.disabled = true;
    if (msg) msg.textContent = 'Привязываем…';
    try {
      const r = await api('/printing/gateway/mobile-station/bind', {
        method: 'POST',
        body: JSON.stringify({ printer_qr: qr, device_id: deviceId(), minutes: 120 }),
      });
      if (msg) msg.textContent = 'Готово: ' + (r.station_code || '') + ' до ' + (r.bound_until || '');
      await refreshStatus();
      $('printBindQr').value = '';
    } catch (e) {
      if (msg) msg.textContent = e.message || String(e);
    } finally {
      if (btn) btn.disabled = false;
    }
  }
  function boot() {
    const open = $('openPrintBind');
    if (open) {
      open.addEventListener('click', () => {
        show('printbind');
        refreshStatus();
        setTimeout(() => $('printBindQr')?.focus(), 120);
      });
    }
    $('printBindGo')?.addEventListener('click', bind);
    $('printBindHome')?.addEventListener('click', () => show('setup'));
    $('printBindQr')?.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        bind();
      }
    });
    // Keep screens list aware if tsd.js uses a fixed list
    try {
      if (Array.isArray(window.screens) && !window.screens.includes('printbind')) {
        window.screens.push('printbind');
      }
    } catch (_) {}
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
