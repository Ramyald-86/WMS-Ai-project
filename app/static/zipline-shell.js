/**
 * ZIPLINE top-level shell for FulfillCore pages (ssdarold).
 * Replaces iframe embedding: ZIPLINE nav + shared /auth session, no second login.
 */
(function () {
  var LIVE_LOGIN = "/zipline/core/ZIPLINE_OS_LIVE.html";
  var HUB = "/zipline/";
  var SIDEBAR_W = 264;
  var THEME_KEY = "zipline_theme";

  function readTheme() {
    try {
      var t = localStorage.getItem(THEME_KEY);
      if (t === "light" || t === "dark") return t;
    } catch (_) {}
    return "dark";
  }

  function applyTheme(theme) {
    theme = theme === "light" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (_) {}
    var buttons = document.querySelectorAll("[data-theme-set]");
    for (var i = 0; i < buttons.length; i++) {
      var btn = buttons[i];
      var on = btn.getAttribute("data-theme-set") === theme;
      btn.classList.toggle("active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  applyTheme(readTheme());

  function qs(name) {
    try {
      return new URLSearchParams(location.search).get(name) || "";
    } catch (_) {
      return "";
    }
  }

  function deepPage() {
    return (qs("page") || "").trim() || (location.hash || "").replace(/^#/, "").trim();
  }

  function applyDeepLink() {
    var deep = deepPage();
    if (!deep) return;
    try {
      localStorage.setItem("fc_active_page", deep);
    } catch (_) {}
    try {
      if (typeof state !== "undefined" && state) state.activePage = deep;
    } catch (_) {}
    try {
      if (typeof go === "function") go(deep);
    } catch (_) {}
  }

  function pathOnly() {
    return (location.pathname || "/").replace(/\/$/, "") || "/";
  }

  function isClientArea() {
    return pathOnly().indexOf("/client") === 0;
  }

  function isTsdLogin() {
    return pathOnly().indexOf("/tsd/login") === 0 || /tsd-login/.test(location.pathname);
  }

  function isPublicHelp() {
    return pathOnly().indexOf("/help/") === 0;
  }

  function buildUrl(mod) {
    if (!mod) return "/";
    if (mod.kind === "link") return mod.href;
    var path = mod.path || "/";
    if (path.charAt(0) !== "/") path = "/" + path;
    var url = new URL(path, location.origin);
    if (mod.page) url.searchParams.set("page", mod.page);
    if (mod.id) url.searchParams.set("module", mod.id);
    return url.pathname + url.search;
  }

  function matchModule(mod) {
    if (!mod || mod.kind === "soon" || mod.kind === "link") return false;
    var p = pathOnly();
    var modPath = (mod.path || "/").replace(/\/$/, "") || "/";
    if (modPath === "/") {
      if (p !== "/" && p !== "") return false;
      var page = deepPage() || "dashboard";
      return (mod.page || "dashboard") === page;
    }
    return p === modPath || p.indexOf(modPath + "/") === 0;
  }

  function ensureStyles() {
    if (document.getElementById("zip-shell-css")) return;
    var css = document.createElement("style");
    css.id = "zip-shell-css";
    css.textContent =
      "html.fc-zip-shell,html.fc-zip-shell body{min-height:100%;}" +
      "html.fc-zip-shell .sidebar:not(.zip-shell-side)," +
      "html.fc-zip-shell body .sidebar:not(.zip-shell-side){display:none!important;}" +
      "html.fc-zip-shell .app-shell{display:block!important;min-height:100vh;}" +
      "html.fc-zip-shell .main," +
      "html.fc-zip-shell .app-shell>.main," +
      "html.fc-zip-shell .wf-main," +
      "html.fc-zip-shell .portal-main," +
      "html.fc-zip-shell body>main," +
      "html.fc-zip-shell body>.topbar+*," +
      "html.fc-zip-shell .content{margin-left:" +
      SIDEBAR_W +
      "px!important;width:auto!important;max-width:none;}" +
      "html.fc-zip-shell .wf-head,html.fc-zip-shell .portal-head,html.fc-zip-shell body>header.topbar," +
      "html.fc-zip-shell .topbar{margin-left:" +
      SIDEBAR_W +
      "px!important;width:calc(100% - " +
      SIDEBAR_W +
      "px)!important;box-sizing:border-box;}" +
      ".zip-shell-side{position:fixed;left:0;top:0;bottom:0;width:" +
      SIDEBAR_W +
      "px;z-index:10050;display:flex;flex-direction:column;" +
      "background:rgba(7,7,9,.97);border-right:1px solid #1C1E27;backdrop-filter:blur(22px);" +
      "font-family:Inter,\"Segoe UI\",Arial,sans-serif;color:#F5F7FB;}" +
      ".zip-shell-side a.zip-brand{display:flex;gap:12px;align-items:center;padding:18px 16px 14px;" +
      "text-decoration:none;color:inherit;border-bottom:1px solid #1C1E27;}" +
      ".zip-shell-side .zip-mark{width:40px;height:40px;border-radius:12px;flex-shrink:0;" +
      "display:grid;place-items:center;background:linear-gradient(145deg,#6200EE,#260061);" +
      "box-shadow:0 0 30px #6200EE5C;color:#fff;font-weight:800;font-size:12px;}" +
      ".zip-shell-side .zip-brand strong{display:block;font-family:\"Space Grotesk\",Inter,sans-serif;" +
      "font-size:15px;letter-spacing:-.02em;}" +
      ".zip-shell-side .zip-brand span{display:block;font-size:11px;color:#9EA8B7;margin-top:2px;}" +
      ".zip-shell-side .zip-nav{flex:1;overflow:auto;padding:10px 10px 16px;}" +
      ".zip-shell-side .zip-group{margin-bottom:12px;}" +
      ".zip-shell-side .zip-group-label{padding:8px 10px 4px;font-size:10px;font-weight:800;" +
      "letter-spacing:.09em;color:#7F8998;}" +
      ".zip-shell-side .zip-nav button{display:block;width:100%;text-align:left;border:1px solid transparent;" +
      "background:transparent;color:#AEB7C8;border-radius:8px;padding:10px 12px;margin:2px 0;" +
      "cursor:pointer;font:600 13px Inter,\"Segoe UI\",Arial,sans-serif;}" +
      ".zip-shell-side .zip-nav button:hover,.zip-shell-side .zip-nav button.active{" +
      "background:linear-gradient(90deg,#6200EE30,#00D4FF0C);color:#fff;border-color:#7651D75C;" +
      "box-shadow:inset 3px 0 0 #8B62FF;}" +
      ".zip-shell-side .zip-nav button.soon{opacity:.45;cursor:default;}" +
      ".zip-shell-side .zip-foot{padding:12px 14px 16px;border-top:1px solid #1C1E27;}" +
      ".zip-shell-side .zip-theme-label{font-size:10px;font-weight:800;letter-spacing:.09em;" +
      "color:#7F8998;text-transform:uppercase;margin:0 0 8px;}" +
      ".zip-shell-side .zip-theme{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:12px;}" +
      ".zip-shell-side .zip-theme button{display:grid;place-items:center;min-height:40px;" +
      "border-radius:9px;padding:8px;border:1px solid #2C3040;background:rgba(255,255,255,.03);" +
      "color:#AEB7C8;cursor:pointer;}" +
      ".zip-shell-side .zip-theme button svg{width:18px;height:18px;display:block;}" +
      ".zip-shell-side .zip-theme button:hover{border-color:#7651D75C;color:#fff;}" +
      ".zip-shell-side .zip-theme button.active{background:linear-gradient(120deg,#4B00C9,#6200EE 48%,#1478FF);" +
      "border-color:transparent;color:#fff;box-shadow:0 0 16px #6200EE2B;}" +
      ".zip-shell-side .zip-who{font-size:12px;color:#9EA8B7;margin-bottom:8px;word-break:break-all;}" +
      ".zip-shell-side .zip-actions{display:flex;gap:8px;flex-wrap:wrap;}" +
      ".zip-shell-side .zip-actions a,.zip-shell-side .zip-actions button{" +
      "font:600 12px Inter,sans-serif;border-radius:8px;padding:7px 10px;border:1px solid #2C3040;" +
      "background:transparent;color:#AEB7C8;cursor:pointer;text-decoration:none;}" +
      ".zip-shell-side .zip-actions button.primary{background:linear-gradient(120deg,#4B00C9,#6200EE 48%,#1478FF);" +
      "border-color:transparent;color:#fff;}" +
      ".zip-shell-burger{display:none;position:fixed;top:10px;left:10px;z-index:10060;" +
      "border:1px solid #2C3040;background:#12141A;color:#F5F7FB;border-radius:8px;padding:8px 10px;cursor:pointer;}" +
      "html[data-theme=light] .zip-shell-side," +
      "html[data-theme=light] aside.sidebar.zip-shell-side{" +
      "background:#ffffff!important;border-right:1px solid #e6eaf1!important;color:#172033!important;" +
      "backdrop-filter:none!important;}" +
      "html[data-theme=light] .zip-shell-side a.zip-brand{border-bottom-color:#e6eaf1!important;color:#172033!important;}" +
      "html[data-theme=light] .zip-shell-side .zip-brand strong{color:#172033!important;}" +
      "html[data-theme=light] .zip-shell-side .zip-brand span," +
      "html[data-theme=light] .zip-shell-side .zip-group-label," +
      "html[data-theme=light] .zip-shell-side .zip-theme-label," +
      "html[data-theme=light] .zip-shell-side .zip-who{color:#748097!important;}" +
      "html[data-theme=light] .zip-shell-side .zip-nav button{color:#5b6474!important;background:transparent!important;}" +
      "html[data-theme=light] .zip-shell-side .zip-nav button:hover," +
      "html[data-theme=light] .zip-shell-side .zip-nav button.active{" +
      "background:linear-gradient(90deg,#eef2ff,#f0f9ff)!important;color:#312e81!important;" +
      "border-color:#c7d2fe!important;box-shadow:inset 3px 0 0 #6366f1!important;}" +
      "html[data-theme=light] .zip-shell-side .zip-foot{border-top-color:#e6eaf1!important;}" +
      "html[data-theme=light] .zip-shell-side .zip-theme button{" +
      "border-color:#dfe4ec!important;background:#f8fafc!important;color:#475569!important;}" +
      "html[data-theme=light] .zip-shell-side .zip-theme button:hover{" +
      "border-color:#a5b4fc!important;color:#312e81!important;}" +
      "html[data-theme=light] .zip-shell-side .zip-theme button.active{" +
      "background:linear-gradient(120deg,#4B00C9,#6200EE 48%,#1478FF)!important;" +
      "border-color:transparent!important;color:#fff!important;}" +
      "html[data-theme=light] .zip-shell-side .zip-actions a," +
      "html[data-theme=light] .zip-shell-side .zip-actions button:not(.primary){" +
      "border-color:#dfe4ec!important;color:#475569!important;background:transparent!important;}" +
      "html[data-theme=light] .zip-shell-burger{" +
      "border-color:#dfe4ec!important;background:#fff!important;color:#172033!important;}" +
      "@media(max-width:900px){" +
      ".zip-shell-burger{display:block;}" +
      ".zip-shell-side{transform:translateX(-105%);transition:transform .2s ease;}" +
      ".zip-shell-side.open{transform:translateX(0);}" +
      "html.fc-zip-shell .main,html.fc-zip-shell .wf-main,html.fc-zip-shell .portal-main," +
      "html.fc-zip-shell body>main,html.fc-zip-shell .topbar,html.fc-zip-shell .wf-head," +
      "html.fc-zip-shell .portal-head{margin-left:0!important;width:100%!important;}" +
      "}";
    document.head.appendChild(css);
  }

  function loadRegistry(cb) {
    if (window.ZiplineModules) {
      cb(window.ZiplineModules);
      return;
    }
    var s = document.createElement("script");
    s.src = "/zipline/shared/module-registry.js?v=zip-direct1";
    s.onload = function () {
      cb(window.ZiplineModules || null);
    };
    s.onerror = function () {
      cb(null);
    };
    document.head.appendChild(s);
  }

  function renderNav(host, modulesApi, activeId) {
    var grouped = modulesApi.groupModules();
    host.innerHTML = "";
    grouped.order.forEach(function (group) {
      var wrap = document.createElement("div");
      wrap.className = "zip-group";
      var lab = document.createElement("div");
      lab.className = "zip-group-label";
      lab.textContent = String(group || "").toUpperCase();
      wrap.appendChild(lab);
      grouped.map[group].forEach(function (mod) {
        var btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = mod.label;
        btn.dataset.module = mod.id;
        if (mod.kind === "soon") {
          btn.className = "soon";
          btn.title = "Скоро появится";
        } else {
          if (mod.id === activeId || matchModule(mod)) btn.className = "active";
          btn.addEventListener("click", function () {
            location.href = buildUrl(mod);
          });
        }
        wrap.appendChild(btn);
      });
      host.appendChild(wrap);
    });
  }

  function findActive(modulesApi) {
    var fromQuery = qs("module");
    if (fromQuery && modulesApi.get(fromQuery)) return fromQuery;
    var hit = (modulesApi.MODULES || []).find(function (m) {
      return matchModule(m);
    });
    return hit ? hit.id : "";
  }

  async function ensureSession() {
    if (isClientArea() || isTsdLogin() || isPublicHelp()) return { skip: true, me: null };
    try {
      var r = await fetch("/auth/me", { credentials: "include", headers: { Accept: "application/json" } });
      if (r.status === 401) {
        location.replace(LIVE_LOGIN + "?next=" + encodeURIComponent(location.pathname + location.search));
        return { redirect: true };
      }
      if (!r.ok) return { me: null };
      return { me: await r.json() };
    } catch (_) {
      return { me: null };
    }
  }

  async function doLogout() {
    try {
      await fetch("/auth/logout", { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: "{}" });
    } catch (_) {}
    location.href = LIVE_LOGIN;
  }

  function rebindFcLogout() {
    var btn = document.getElementById("logoutBtn");
    if (!btn || btn.dataset.zipBound) return;
    btn.dataset.zipBound = "1";
    btn.addEventListener(
      "click",
      function (ev) {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        doLogout();
      },
      true
    );
  }

  function mountShell(modulesApi, me) {
    ensureStyles();
    document.documentElement.classList.add("fc-zip-shell", "fc-embed");
    document.body.classList.add("fc-zip-shell", "fc-embed");

    if (document.getElementById("zip-shell-side")) return;

    var burger = document.createElement("button");
    burger.type = "button";
    burger.className = "zip-shell-burger";
    burger.textContent = "☰";
    document.body.appendChild(burger);

    var side = document.createElement("aside");
    side.id = "zip-shell-side";
    side.className = "sidebar zip-shell-side";
    side.innerHTML =
      '<a class="zip-brand" href="' +
      HUB +
      '" aria-label="На главную ZIPLINE OS">' +
      '<div class="zip-mark">Z</div>' +
      "<div><strong>ZIPLINE OS</strong><span>AI-платформа фулфилмента</span></div></a>" +
      '<nav class="zip-nav" id="zip-shell-nav"></nav>' +
      '<div class="zip-foot">' +
      '<div class="zip-theme-label">Цвет темы</div>' +
      '<div class="zip-theme" role="group" aria-label="Цвет темы">' +
      '<button type="button" data-theme-set="dark" title="Тёмная" aria-label="Тёмная тема" aria-pressed="false">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<path d="M21 14.5A8.5 8.5 0 0 1 9.5 3 7 7 0 1 0 21 14.5z"/></svg></button>' +
      '<button type="button" data-theme-set="light" title="Светлая" aria-label="Светлая тема" aria-pressed="false">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      '<circle cx="12" cy="12" r="4"/>' +
      '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>' +
      "</svg></button>" +
      "</div>" +
      '<div class="zip-who" id="zip-shell-who">—</div>' +
      '<div class="zip-actions">' +
      '<a href="' +
      HUB +
      '">На главную</a>' +
      '<button type="button" class="primary" id="zip-shell-logout">Выйти</button>' +
      "</div></div>";
    document.body.appendChild(side);

    burger.addEventListener("click", function () {
      side.classList.toggle("open");
    });

    var themeBtns = side.querySelectorAll("[data-theme-set]");
    for (var ti = 0; ti < themeBtns.length; ti++) {
      themeBtns[ti].addEventListener("click", function (ev) {
        applyTheme(ev.currentTarget.getAttribute("data-theme-set"));
      });
    }
    applyTheme(readTheme());

    var activeId = modulesApi ? findActive(modulesApi) : "";
    if (modulesApi) renderNav(document.getElementById("zip-shell-nav"), modulesApi, activeId);

    var who = document.getElementById("zip-shell-who");
    if (me) {
      who.textContent = (me.username || "user") + " · " + (me.role || me.principal || "session");
    } else if (isClientArea()) {
      who.textContent = "Кабинет клиента";
    } else {
      who.textContent = "Сессия";
    }

    document.getElementById("zip-shell-logout").addEventListener("click", doLogout);
    rebindFcLogout();
    setTimeout(rebindFcLogout, 800);
  }

  function boot() {
    document.documentElement.classList.add("fc-zip-shell");
    applyDeepLink();
    loadRegistry(function (modulesApi) {
      ensureSession().then(function (session) {
        if (session && session.redirect) return;
        var me = session && session.me;
        function go() {
          mountShell(modulesApi, me);
          applyDeepLink();
          setTimeout(applyDeepLink, 400);
          setTimeout(applyDeepLink, 1200);
        }
        if (document.body) go();
        else document.addEventListener("DOMContentLoaded", go);
      });
    });
  }

  boot();
})();
