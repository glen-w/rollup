/* Applies a persisted colour theme before first paint. Loaded synchronously in <head>. */
(function () {
  try {
    var saved = window.localStorage.getItem("rollup.theme");
    if (saved === "light" || saved === "dark") {
      document.documentElement.setAttribute("data-theme", saved);
    }
  } catch (e) {
    /* storage unavailable — fall back to prefers-color-scheme */
  }
})();
