(function () {
  function syncReasons(form) {
    var checked = form.querySelector('input[name="stars"]:checked');
    if (!checked) return;
    var stars = parseInt(checked.value, 10);
    var allowed;
    if (stars <= 2) allowed = "negative";
    else if (stars >= 4) allowed = "positive";
    else allowed = null;
    form.querySelectorAll('input[name="reasons"]').forEach(function (cb) {
      var pol = cb.getAttribute("data-polarity");
      if (!allowed) {
        cb.disabled = false;
        return;
      }
      var ok = pol === allowed;
      cb.disabled = !ok;
      if (!ok) cb.checked = false;
    });
  }

  document.querySelectorAll("[data-rating-form]").forEach(function (form) {
    form.querySelectorAll('input[name="stars"]').forEach(function (radio) {
      radio.addEventListener("change", function () {
        syncReasons(form);
      });
    });
    syncReasons(form);
  });
})();
