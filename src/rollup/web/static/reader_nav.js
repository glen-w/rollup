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

  document.addEventListener("keydown", function (ev) {
    if (editableActive()) return;
    var nav = document.querySelector("[data-reader-nav]");
    if (!nav) return;
    if (ev.key === "j" && nav.dataset.next) {
      window.location.href = nav.dataset.next;
    } else if (ev.key === "k" && nav.dataset.prev) {
      window.location.href = nav.dataset.prev;
    } else if (ev.key === "Escape" && nav.dataset.back) {
      window.location.href = nav.dataset.back;
    }
  });
})();
