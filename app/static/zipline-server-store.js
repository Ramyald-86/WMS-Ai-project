/**
 * ZIPLINE / FulfillCore: Postgres as SoT for former browser-only business buffers.
 * - pending WB queue
 * - fc:r76 / fc:r77 pending ops (via localStorage mirror + sync)
 * - address-receiving terminal draft
 * - nudge receiving queues (r145f32 / r145f51) to flush to existing server drafts
 *
 * localStorage/IDB remain an offline cache only; server wins when online.
 */
(function () {
  "use strict";
  var MIGRATE_FLAG = "fc_server_state_migrated_v1";
  var wbCache = { key: "", lines: null, ready: false };
  var saveTimer = null;
  var opsTimer = null;
  var pendingOpWrites = [];

  function apiFn() {
    return typeof api === "function" ? api : null;
  }

  function ctx() {
    var st = window.state || {};
    return {
      client_id: st.clientId || st.client_id || null,
      warehouse_id: st.warehouseId || st.warehouse_id || null,
    };
  }

  function hasCtx() {
    var c = ctx();
    return !!(c.client_id && c.warehouse_id);
  }

  function cacheKey() {
    var c = ctx();
    return String(c.client_id || "") + "|" + String(c.warehouse_id || "");
  }

  async function fetchPendingWb() {
    var call = apiFn();
    if (!call || !hasCtx()) return [];
    var c = ctx();
    var r = await call(
      "/server-state/pending-wb?client_id=" +
        encodeURIComponent(c.client_id) +
        "&warehouse_id=" +
        encodeURIComponent(c.warehouse_id)
    );
    var lines = Array.isArray(r?.lines) ? r.lines : [];
    wbCache = { key: cacheKey(), lines: lines, ready: true };
    try {
      localStorage.setItem(
        "fc_pending_wb_receiving_v2:" +
          (c.client_id || "none") +
          ":" +
          (c.warehouse_id || "none"),
        JSON.stringify(lines)
      );
    } catch (_) {}
    return lines;
  }

  function scheduleWbSave(lines) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      var call = apiFn();
      if (!call || !hasCtx()) return;
      var c = ctx();
      call("/server-state/pending-wb", {
        method: "PUT",
        body: JSON.stringify({
          client_id: c.client_id,
          warehouse_id: c.warehouse_id,
          lines: lines || [],
        }),
      }).catch(function () {});
    }, 250);
  }

  function patchPendingWb() {
    if (typeof loadPendingWbReceiving !== "function") return;
    var origLoad = loadPendingWbReceiving;
    var origSave = typeof savePendingWbReceiving === "function" ? savePendingWbReceiving : null;
    var origAdd = typeof addPendingWbReceiving === "function" ? addPendingWbReceiving : null;

    window.loadPendingWbReceiving = function () {
      if (wbCache.ready && wbCache.key === cacheKey() && Array.isArray(wbCache.lines)) {
        return wbCache.lines.slice();
      }
      try {
        return origLoad();
      } catch (_) {
        return [];
      }
    };

    window.savePendingWbReceiving = function (rows) {
      var list = Array.isArray(rows) ? rows : [];
      wbCache = { key: cacheKey(), lines: list.slice(), ready: true };
      if (origSave) {
        try {
          origSave(list);
        } catch (_) {}
      }
      scheduleWbSave(list);
      if (typeof renderPendingWbReceiving === "function") {
        try {
          renderPendingWbReceiving();
        } catch (_) {}
      }
    };

    if (origAdd) {
      window.addPendingWbReceiving = function (lines) {
        var q = window.loadPendingWbReceiving();
        for (var i = 0; i < (lines || []).length; i++) {
          var x = lines[i];
          if (!x || !x.barcode) continue;
          var row = {
            barcode: String(x.barcode),
            quantity: Number(x.quantity || 0),
            mode: x.mode || "ADD",
            idempotency_key: x.idempotency_key ? String(x.idempotency_key) : null,
          };
          if (row.idempotency_key) {
            var idx = q.findIndex(function (v) {
              return String((v && v.idempotency_key) || "") === row.idempotency_key;
            });
            if (idx >= 0) q[idx] = row;
            else q.push(row);
          } else q.push(row);
        }
        window.savePendingWbReceiving(q);
        var call = apiFn();
        if (call && hasCtx()) {
          var c = ctx();
          call("/server-state/pending-wb/upsert", {
            method: "POST",
            body: JSON.stringify({
              client_id: c.client_id,
              warehouse_id: c.warehouse_id,
              lines: lines || [],
            }),
          })
            .then(function (r) {
              if (r && Array.isArray(r.lines)) {
                wbCache = { key: cacheKey(), lines: r.lines, ready: true };
                if (typeof renderPendingWbReceiving === "function") renderPendingWbReceiving();
              }
            })
            .catch(function () {});
        }
      };
    }
  }

  function kindFromScope(scopeKey) {
    if (String(scopeKey).indexOf("fc:r76:") === 0) return "RECEIPT_TO_FBS";
    if (String(scopeKey).indexOf("fc:r77:stopped-action:") === 0) return "WB_STOPPED";
    if (String(scopeKey).indexOf("fc:r77:bulk-remove:") === 0) return "BULK_REMOVE";
    return "CLIENT_PENDING";
  }

  function flushPendingOps() {
    var call = apiFn();
    if (!call || !pendingOpWrites.length) return;
    var batch = pendingOpWrites.splice(0, pendingOpWrites.length);
    batch.forEach(function (item) {
      if (item.clear) {
        call("/server-state/pending-ops/clear", {
          method: "POST",
          body: JSON.stringify({
            scope_key: item.scope_key,
            request_id: item.request_id || null,
          }),
        }).catch(function () {});
        return;
      }
      var payload = item.payload || {};
      var requestId =
        item.request_id ||
        payload.request_id ||
        payload.fingerprint ||
        String(Date.now());
      call("/server-state/pending-ops/upsert", {
        method: "POST",
        body: JSON.stringify({
          scope_key: item.scope_key,
          kind: kindFromScope(item.scope_key),
          request_id: String(requestId),
          payload: payload,
          client_id: (payload && payload.client_id) || ctx().client_id,
          warehouse_id: (payload && payload.warehouse_id) || ctx().warehouse_id,
        }),
      }).catch(function () {});
    });
  }

  function patchLocalStorageOps() {
    var proto = Storage.prototype;
    if (proto.__zipServerPatched) return;
    proto.__zipServerPatched = true;
    var setItem = proto.setItem;
    var removeItem = proto.removeItem;

    proto.setItem = function (key, value) {
      setItem.call(this, key, value);
      var k = String(key || "");
      if (k.indexOf("fc:r76:") === 0 || k.indexOf("fc:r77:") === 0) {
        var payload = null;
        try {
          payload = JSON.parse(value);
        } catch (_) {
          payload = { raw: value };
        }
        pendingOpWrites.push({
          scope_key: k,
          request_id: payload && (payload.request_id || payload.fingerprint),
          payload: payload,
        });
        clearTimeout(opsTimer);
        opsTimer = setTimeout(flushPendingOps, 200);
      }
    };

    proto.removeItem = function (key) {
      removeItem.call(this, key);
      var k = String(key || "");
      if (k.indexOf("fc:r76:") === 0 || k.indexOf("fc:r77:") === 0) {
        pendingOpWrites.push({ scope_key: k, clear: true });
        clearTimeout(opsTimer);
        opsTimer = setTimeout(flushPendingOps, 200);
      }
    };
  }

  async function hydratePendingOpsFromServer() {
    var call = apiFn();
    if (!call) return;
    try {
      var r = await call("/server-state/pending-ops");
      (r.ops || []).forEach(function (op) {
        if (!op || !op.scope_key || !op.payload) return;
        try {
          localStorage.setItem(op.scope_key, JSON.stringify(op.payload));
        } catch (_) {}
      });
    } catch (_) {}
  }

  async function migrateLocalOnce() {
    if (localStorage.getItem(MIGRATE_FLAG) === "1") return;
    var call = apiFn();
    if (!call || !hasCtx()) return;
    var c = ctx();
    var pendingWb = [];
    try {
      var key =
        "fc_pending_wb_receiving_v2:" + c.client_id + ":" + (c.warehouse_id || "");
      var scoped = localStorage.getItem(key);
      if (!scoped) {
        // older scopedStorageKey variants
        for (var i = 0; i < localStorage.length; i++) {
          var k = localStorage.key(i);
          if (k && k.indexOf("fc_pending_wb_receiving_v2:") === 0) {
            try {
              pendingWb = pendingWb.concat(JSON.parse(localStorage.getItem(k) || "[]") || []);
            } catch (_) {}
          }
        }
      } else {
        pendingWb = JSON.parse(scoped) || [];
      }
    } catch (_) {}

    var pendingOps = [];
    try {
      for (var j = 0; j < localStorage.length; j++) {
        var sk = localStorage.key(j);
        if (!sk || (sk.indexOf("fc:r76:") !== 0 && sk.indexOf("fc:r77:") !== 0)) continue;
        try {
          var payload = JSON.parse(localStorage.getItem(sk) || "null");
          if (!payload) continue;
          pendingOps.push({
            scope_key: sk,
            kind: kindFromScope(sk),
            request_id: String(payload.request_id || payload.fingerprint || sk),
            payload: payload,
            client_id: c.client_id,
            warehouse_id: c.warehouse_id,
          });
        } catch (_) {}
      }
    } catch (_) {}

    try {
      await call("/server-state/migrate-local", {
        method: "POST",
        body: JSON.stringify({
          client_id: c.client_id,
          warehouse_id: c.warehouse_id,
          pending_wb: pendingWb,
          pending_ops: pendingOps,
        }),
      });
      localStorage.setItem(MIGRATE_FLAG, "1");
    } catch (_) {}
  }

  async function flushReceivingQueues() {
    try {
      if (window.FC32Receiving && typeof window.FC32Receiving.flush === "function") {
        await window.FC32Receiving.flush();
      }
    } catch (_) {}
    try {
      if (window.FC51Receiving && typeof window.FC51Receiving.syncNow === "function") {
        await window.FC51Receiving.syncNow();
      }
    } catch (_) {}
    try {
      if (typeof window.fc32Flush === "function") await window.fc32Flush();
    } catch (_) {}
  }

  function patchTerminalIfPresent() {
    if (typeof save !== "function" || typeof STORAGE === "undefined") return;
    if (window.__zipTerminalPatched) return;
    window.__zipTerminalPatched = true;
    var origSave = save;
    window.save = function () {
      origSave.apply(this, arguments);
      var call = apiFn();
      var scope = window.wmsScope || (window.state && window.state.wmsScope) || {};
      var clientId = scope.client_id || ctx().client_id;
      var warehouseId = scope.warehouse_id || ctx().warehouse_id;
      if (!call || !clientId || !warehouseId || !window.state) return;
      call("/server-state/terminal-draft", {
        method: "PUT",
        body: JSON.stringify({
          client_id: clientId,
          warehouse_id: warehouseId,
          terminal_key: "default",
          payload: window.state,
        }),
      }).catch(function () {});
    };

    (async function hydrateTerminal() {
      var call = apiFn();
      var scope = window.wmsScope || {};
      var clientId = scope.client_id || ctx().client_id;
      var warehouseId = scope.warehouse_id || ctx().warehouse_id;
      if (!call || !clientId || !warehouseId) return;
      try {
        var r = await call(
          "/server-state/terminal-draft?client_id=" +
            encodeURIComponent(clientId) +
            "&warehouse_id=" +
            encodeURIComponent(warehouseId)
        );
        if (r && r.payload && typeof r.payload === "object") {
          window.state = r.payload;
          try {
            localStorage.setItem(STORAGE, JSON.stringify(window.state));
          } catch (_) {}
          if (typeof render === "function") render();
        }
      } catch (_) {}
    })();
  }

  async function boot() {
    patchLocalStorageOps();
    patchPendingWb();
    patchTerminalIfPresent();
    if (!hasCtx()) return;
    try {
      await migrateLocalOnce();
    } catch (_) {}
    try {
      await hydratePendingOpsFromServer();
    } catch (_) {}
    try {
      await fetchPendingWb();
      if (typeof renderPendingWbReceiving === "function") renderPendingWbReceiving();
    } catch (_) {}
    try {
      await flushReceivingQueues();
    } catch (_) {}
  }

  function onContextReady() {
    boot();
  }

  window.ZiplineServerStore = {
    refreshPendingWb: fetchPendingWb,
    flushReceivingQueues: flushReceivingQueues,
    boot: boot,
  };

  function wrapEnsureContext() {
    if (typeof window.ensureContext !== "function" || window.ensureContext.__zipWrapped) return;
    var orig = window.ensureContext;
    window.ensureContext = async function () {
      var r = await orig.apply(this, arguments);
      try {
        await boot();
      } catch (_) {}
      return r;
    };
    window.ensureContext.__zipWrapped = true;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setTimeout(onContextReady, 800);
      setTimeout(onContextReady, 2500);
      setTimeout(wrapEnsureContext, 1200);
      setTimeout(wrapEnsureContext, 4000);
    });
  } else {
    setTimeout(onContextReady, 800);
    setTimeout(onContextReady, 2500);
    setTimeout(wrapEnsureContext, 1200);
    setTimeout(wrapEnsureContext, 4000);
  }

  window.addEventListener("online", function () {
    flushReceivingQueues();
    flushPendingOps();
    if (hasCtx()) fetchPendingWb().catch(function () {});
  });
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") flushReceivingQueues();
  });
})();
