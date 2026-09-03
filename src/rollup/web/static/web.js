(function () {
  document.getElementById("expand-all-cards")?.addEventListener("click", function () {
    document.querySelectorAll("details.newsletter-card").forEach(function (el) {
      el.open = true;
    });
  });
  document.getElementById("collapse-all-cards")?.addEventListener("click", function () {
    document.querySelectorAll("details.newsletter-card").forEach(function (el) {
      el.open = false;
    });
  });

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

  document.getElementById("linkedin-add-another")?.addEventListener("click", function () {
    var wrap = document.getElementById("linkedin-add-rows");
    var tmpl = document.getElementById("linkedin-add-row-template");
    if (!wrap || !tmpl) return;
    wrap.appendChild(tmpl.content.cloneNode(true));
  });

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
          el.select();
        });
        return;
      }
      el.select();
    });
  });
})();
