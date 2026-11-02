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

  function paintStars(form, upTo) {
    var labels = form.querySelectorAll(".stars label");
    labels.forEach(function (label, i) {
      label.classList.toggle("is-filled", i < upTo);
    });
  }

  function currentStars(form) {
    var checked = form.querySelector('input[name="stars"]:checked');
    return checked ? parseInt(checked.value, 10) : 0;
  }

  document.querySelectorAll("[data-rating-form]").forEach(function (form) {
    var labels = Array.prototype.slice.call(form.querySelectorAll(".stars label"));
    labels.forEach(function (label, i) {
      label.addEventListener("mouseenter", function () {
        paintStars(form, i + 1);
      });
      label.addEventListener("mouseleave", function () {
        paintStars(form, currentStars(form));
      });
    });
    form.querySelectorAll('input[name="stars"]').forEach(function (radio) {
      radio.addEventListener("change", function () {
        syncReasons(form);
        paintStars(form, currentStars(form));
        // One-click rating: submit immediately unless the reasons panel is open.
        var reasons = form.querySelector("details.run-details");
        if (radio.hasAttribute("data-star-submit") && !(reasons && reasons.open)) {
          if (typeof form.requestSubmit === "function") form.requestSubmit();
          else form.submit();
        }
      });
    });
    syncReasons(form);
    paintStars(form, currentStars(form));
  });
})();
