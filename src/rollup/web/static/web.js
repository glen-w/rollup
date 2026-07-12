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
})();
