/**
 * ZIPLINE embed helpers for FulfillCore shell (ssdarold only).
 * Ensures ?page= deep-links work with app-r145f39.js (which lacks bootstrap page parsing).
 */
(function () {
  function deepPage() {
    try {
      var q = new URLSearchParams(location.search);
      var deep = (q.get("page") || "").trim() || (location.hash || "").replace(/^#/, "").trim();
      return deep;
    } catch (_) {
      return "";
    }
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

  // After FC scripts load: re-apply if bootstrap already ran with stale page.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      setTimeout(applyDeepLink, 0);
      setTimeout(applyDeepLink, 400);
      setTimeout(applyDeepLink, 1200);
    });
  } else {
    setTimeout(applyDeepLink, 0);
    setTimeout(applyDeepLink, 400);
    setTimeout(applyDeepLink, 1200);
  }
})();
