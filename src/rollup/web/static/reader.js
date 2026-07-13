(function () {
  function editableActive() {
    var el = document.activeElement;
    if (!el) return false;
    var tag = el.tagName;
    return (
      tag === "INPUT" ||
      tag === "TEXTAREA" ||
      tag === "SELECT" ||
      tag === "BUTTON" ||
      el.isContentEditable
    );
  }

  function loadBody(details) {
    var url = details.getAttribute("data-body-url");
    var path = details.getAttribute("data-body-path");
    var content = details.querySelector(".reader-body-content");
    var fallback = details.querySelector(".reader-fallback");
    var errEl = details.querySelector(".reader-error");
    var retry = details.querySelector(".reader-retry");
    if (!url || !content || details.dataset.readerState === "loaded") return;
    if (details.dataset.readerState === "loading") return;
    details.dataset.readerState = "loading";
    content.setAttribute("aria-busy", "true");
    fetch(url, { credentials: "same-origin" })
      .then(function (resp) {
        if (!resp.ok) throw new Error("load failed");
        var finalPath = new URL(resp.url, window.location.origin).pathname;
        if (finalPath !== path) throw new Error("redirect mismatch");
        var ct = resp.headers.get("Content-Type") || "";
        if (!ct.toLowerCase().startsWith("text/html")) throw new Error("bad content type");
        return resp.text();
      })
      .then(function (html) {
        var probe = document.createElement("div");
        probe.innerHTML = html;
        // Full-page body responses include site chrome; never inject those into the expander.
        if (probe.querySelector(".site-header, .reader-page, [data-reader-nav]")) {
          throw new Error("full page response");
        }
        var fragment = probe.querySelector("[data-reader-body-fragment]");
        if (!fragment) {
          throw new Error("invalid fragment");
        }
        content.replaceChildren();
        var node = fragment.previousElementSibling;
        var prefix = [];
        while (node) {
          if (node.classList && node.classList.contains("reader-notice")) {
            prefix.unshift(node);
          }
          node = node.previousElementSibling;
        }
        prefix.forEach(function (n) {
          content.appendChild(n);
        });
        content.appendChild(fragment);
        content.hidden = false;
        details.dataset.readerState = "loaded";
        if (fallback) fallback.hidden = true;
        if (errEl) errEl.hidden = true;
        if (retry) retry.hidden = true;
      })
      .catch(function () {
        details.dataset.readerState = "error";
        if (errEl) {
          errEl.textContent = "Could not load newsletter text.";
          errEl.hidden = false;
        }
        if (retry) retry.hidden = false;
      })
      .finally(function () {
        content.removeAttribute("aria-busy");
      });
  }

  document.querySelectorAll(".reader-body").forEach(function (details) {
    details.addEventListener("toggle", function () {
      if (details.open) loadBody(details);
    });
    var retry = details.querySelector(".reader-retry");
    if (retry) {
      retry.addEventListener("click", function () {
        details.dataset.readerState = "idle";
        var errEl = details.querySelector(".reader-error");
        if (errEl) {
          errEl.textContent = "";
          errEl.hidden = true;
        }
        loadBody(details);
      });
    }
  });
})();
