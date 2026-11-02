(function () {
  "use strict";

  /* ---------- Theme toggle (auto → light → dark → auto) ---------- */
  var root = document.documentElement;
  function currentTheme() {
    return root.getAttribute("data-theme") || "auto";
  }
  function labelTheme(btn) {
    var t = currentTheme();
    btn.textContent = t === "auto" ? "Theme: auto" : t === "dark" ? "Theme: dark" : "Theme: light";
  }
  document.querySelectorAll("[data-theme-toggle]").forEach(function (btn) {
    labelTheme(btn);
    btn.addEventListener("click", function () {
      var t = currentTheme();
      var next = t === "auto" ? "light" : t === "light" ? "dark" : "auto";
      if (next === "auto") {
        root.removeAttribute("data-theme");
      } else {
        root.setAttribute("data-theme", next);
      }
      try {
        if (next === "auto") window.localStorage.removeItem("rollup.theme");
        else window.localStorage.setItem("rollup.theme", next);
      } catch (e) {
        /* ignore */
      }
      labelTheme(btn);
    });
  });

  /* ---------- Narrow-screen nav disclosure ---------- */
  document.querySelectorAll("[data-nav-toggle]").forEach(function (btn) {
    var target = document.getElementById(btn.getAttribute("aria-controls") || "");
    if (!target) return;
    btn.addEventListener("click", function () {
      var open = target.classList.toggle("is-open");
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
  });

  /* ---------- Expand / collapse all cards ---------- */
  document.getElementById("expand-all-cards")?.addEventListener("click", function () {
    document.querySelectorAll("details.entry-card").forEach(function (el) {
      el.open = true;
    });
  });
  document.getElementById("collapse-all-cards")?.addEventListener("click", function () {
    document.querySelectorAll("details.entry-card").forEach(function (el) {
      el.open = false;
    });
  });

  /* ---------- Confirm before submit (CSP-safe replacement for onsubmit) ---------- */
  document.querySelectorAll("form[data-confirm]").forEach(function (form) {
    form.addEventListener("submit", function (ev) {
      var msg = form.getAttribute("data-confirm") || "Are you sure?";
      if (!window.confirm(msg)) ev.preventDefault();
    });
  });

  /* ---------- Pending state on slow submits ---------- */
  document.querySelectorAll("form[data-pending]").forEach(function (form) {
    form.addEventListener("submit", function (ev) {
      if (ev.defaultPrevented) return;
      var label = form.getAttribute("data-pending") || "Working…";
      // Defer so the clicked submit button's name/value is still serialised.
      window.setTimeout(function () {
        form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach(function (b) {
          b.setAttribute("aria-busy", "true");
          b.disabled = true;
          if (b.tagName === "BUTTON" && !b.hasAttribute("data-keep-label")) {
            b.dataset.prevLabel = b.textContent;
            b.textContent = label;
          }
        });
      }, 0);
    });
  });

  /* ---------- Mirror a visible input into a hidden form field on submit ---------- */
  document.querySelectorAll("[data-mirror]").forEach(function (hidden) {
    var form = hidden.form;
    if (!form) return;
    form.addEventListener("submit", function () {
      var src = document.querySelector(hidden.getAttribute("data-mirror") || "");
      if (src) hidden.value = src.value;
    });
  });

  /* ---------- Tabs (progressive enhancement over #hash anchors) ---------- */
  document.querySelectorAll("[data-tabs]").forEach(function (container) {
    var tabs = Array.prototype.slice.call(container.querySelectorAll(".tabs a[href^='#']"));
    if (!tabs.length) return;
    var panels = tabs
      .map(function (a) {
        return document.getElementById(a.getAttribute("href").slice(1));
      })
      .filter(Boolean);
    if (panels.length !== tabs.length) return;
    container.classList.add("tabs-enhanced");
    container.querySelector(".tabs")?.setAttribute("role", "tablist");

    function activate(id, focus) {
      tabs.forEach(function (a, i) {
        var on = panels[i].id === id;
        a.setAttribute("aria-selected", on ? "true" : "false");
        a.setAttribute("role", "tab");
        a.tabIndex = on ? 0 : -1;
        panels[i].classList.toggle("is-active", on);
        panels[i].setAttribute("role", "tabpanel");
        if (on && focus) a.focus();
      });
    }
    function fromHash() {
      var id = (window.location.hash || "").slice(1);
      var match = panels.some(function (p) {
        return p.id === id;
      });
      activate(match ? id : panels[0].id, false);
    }
    tabs.forEach(function (a, i) {
      a.addEventListener("click", function (ev) {
        ev.preventDefault();
        activate(panels[i].id, false);
        if (window.history.replaceState) {
          window.history.replaceState(null, "", "#" + panels[i].id);
        } else {
          window.location.hash = panels[i].id;
        }
      });
      a.addEventListener("keydown", function (ev) {
        var dir = ev.key === "ArrowRight" ? 1 : ev.key === "ArrowLeft" ? -1 : 0;
        if (!dir) return;
        ev.preventDefault();
        var next = (i + dir + tabs.length) % tabs.length;
        activate(panels[next].id, true);
        if (window.history.replaceState) window.history.replaceState(null, "", "#" + panels[next].id);
      });
    });
    window.addEventListener("hashchange", fromHash);
    // If a field error lives inside a panel, open that panel first.
    var errPanel = container.querySelector(".tab-panel .field-error, .tab-panel [aria-invalid='true']");
    if (errPanel && !window.location.hash) {
      var p = errPanel.closest(".tab-panel");
      if (p) activate(p.id, false);
      else fromHash();
    } else {
      fromHash();
    }
  });

  /* ---------- Bulk select-all + selected count ---------- */
  document.querySelectorAll("[data-bulk]").forEach(function (form) {
    var all = form.querySelector("[data-select-all]");
    var boxes = Array.prototype.slice.call(form.querySelectorAll("input[type='checkbox'][data-bulk-item]"));
    var counter = form.querySelector("[data-selected-count]");
    var actions = form.querySelectorAll("[data-bulk-action]");
    function refresh() {
      var n = boxes.filter(function (b) {
        return b.checked;
      }).length;
      if (counter) counter.textContent = n ? n + " selected" : "None selected";
      actions.forEach(function (b) {
        b.disabled = n === 0;
      });
      if (all) {
        all.checked = n > 0 && n === boxes.length;
        all.indeterminate = n > 0 && n < boxes.length;
      }
    }
    if (all) {
      all.addEventListener("change", function () {
        boxes.forEach(function (b) {
          b.checked = all.checked;
        });
        refresh();
      });
    }
    boxes.forEach(function (b) {
      b.addEventListener("change", refresh);
    });
    refresh();
  });

  /* ---------- Reader font size ---------- */
  var reader = document.querySelector("[data-reader-page]");
  if (reader) {
    try {
      var size = window.localStorage.getItem("rollup.reader.size");
      if (size) reader.setAttribute("data-size", size);
    } catch (e) {
      /* ignore */
    }
    document.querySelectorAll("[data-reader-size]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var s = btn.getAttribute("data-reader-size") || "normal";
        reader.setAttribute("data-size", s);
        try {
          window.localStorage.setItem("rollup.reader.size", s);
        } catch (e) {
          /* ignore */
        }
      });
    });
  }

  /* ---------- Run Studio: single-model select ---------- */
  function showSingleModelCustom(select, current) {
    var wrap = document.getElementById("single-model-custom-wrap");
    var custom = document.getElementById("single-model-custom");
    var checked = document.getElementById("use-single-model")?.checked;
    if (!wrap || !custom || !select) return;
    select.hidden = true;
    select.removeAttribute("name");
    select.disabled = true;
    wrap.hidden = false;
    custom.name = "single_model";
    custom.disabled = !checked;
    if (current && !custom.value) custom.value = current;
  }

  function fillOllamaModelSelect() {
    var select = document.getElementById("single-model-select");
    if (!select) return;
    var url = select.getAttribute("data-models-url");
    var token = document.querySelector('input[name="csrf_token"]')?.value;
    if (!url || !token) return;
    var current = select.value || select.getAttribute("data-selected") || "";
    fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        "X-CSRFToken": token,
      },
      body: "csrf_token=" + encodeURIComponent(token),
    })
      .then(function (resp) {
        return resp.json();
      })
      .then(function (data) {
        var models = (data && data.models) || [];
        if (!models.length) {
          showSingleModelCustom(select, current);
          return;
        }
        select.innerHTML = "";
        models.forEach(function (name) {
          var opt = document.createElement("option");
          opt.value = name;
          opt.textContent = name;
          if (name === current) opt.selected = true;
          select.appendChild(opt);
        });
        if (current && models.indexOf(current) === -1) {
          var extra = document.createElement("option");
          extra.value = current;
          extra.textContent = current + " (not listed)";
          extra.selected = true;
          select.insertBefore(extra, select.firstChild);
        }
      })
      .catch(function () {
        showSingleModelCustom(select, current);
      });
  }

  function syncSingleModelFields() {
    var box = document.getElementById("use-single-model");
    var fields = document.getElementById("single-model-fields");
    if (!box || !fields) return;
    fields.hidden = !box.checked;
    if (box.checked) fillOllamaModelSelect();
  }

  document.getElementById("use-single-model")?.addEventListener("change", syncSingleModelFields);
  syncSingleModelFields();

  /* ---------- LinkedIn: add another row ---------- */
  document.getElementById("linkedin-add-another")?.addEventListener("click", function () {
    var wrap = document.getElementById("linkedin-add-rows");
    var tmpl = document.getElementById("linkedin-add-row-template");
    if (!wrap || !tmpl) return;
    wrap.appendChild(tmpl.content.cloneNode(true));
  });

  /* ---------- Copy to clipboard ---------- */
  document.querySelectorAll("[data-copy-target]").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var sel = btn.getAttribute("data-copy-target");
      var el = sel ? document.querySelector(sel) : null;
      if (!el) return;
      var text = el.value || el.textContent || "";
      if (!text) return;
      var prev = btn.textContent;
      function copied() {
        btn.textContent = "Copied";
        setTimeout(function () {
          btn.textContent = prev;
        }, 1500);
      }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(copied).catch(function () {
          if (el.select) el.select();
        });
        return;
      }
      if (el.select) el.select();
    });
  });

  /* ---------- Settings: colour picker ↔ text sync ---------- */
  document.querySelectorAll("[data-accent-picker]").forEach(function (picker) {
    var text = picker.parentElement && picker.parentElement.querySelector("[data-accent-text]");
    if (!text) return;
    picker.addEventListener("input", function () {
      text.value = picker.value;
    });
    text.addEventListener("input", function () {
      if (/^#[0-9a-fA-F]{6}$/.test(text.value)) picker.value = text.value;
    });
  });
})();
