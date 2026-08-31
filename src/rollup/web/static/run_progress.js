(function () {
  var panel = document.getElementById("run-progress-panel");
  if (!panel) return;

  var statusUrl = panel.getAttribute("data-status-url");
  var resultUrl = panel.getAttribute("data-result-url");
  if (!statusUrl) return;

  var phaseEl = document.getElementById("run-progress-phase");
  var barEl = document.getElementById("run-progress-bar");
  var detailEl = document.getElementById("run-progress-detail");
  var logEl = document.getElementById("run-progress-log");
  var llmEl = document.getElementById("run-progress-llm");
  var modeEl = document.getElementById("run-progress-mode");
  var pollTimer = null;
  var finished = false;

  function setText(el, text) {
    if (!el) return;
    el.textContent = text || "";
  }

  function renderProgress(data) {
    var progress = (data && data.progress) || {};
    var percent = typeof progress.percent === "number" ? progress.percent : 0;
    var label = progress.phase_label || "Running";
    if (data && data.status === "running") {
      setText(phaseEl, label);
      if (barEl) {
        barEl.style.width = Math.max(2, Math.min(100, percent)) + "%";
        barEl.setAttribute("aria-valuenow", String(percent));
      }
      var detail = progress.detail || "";
      if (progress.llm_current && progress.llm_total) {
        detail =
          "Message " +
          progress.llm_current +
          " of " +
          progress.llm_total +
          (detail ? " — " + detail : "");
      }
      setText(detailEl, detail);
      if (llmEl) {
        if (progress.llm_current && progress.llm_total) {
          llmEl.hidden = false;
          llmEl.textContent =
            progress.llm_current + " / " + progress.llm_total + " summaries";
        } else {
          llmEl.hidden = true;
        }
      }
    }
    if (logEl && data && data.log) {
      logEl.textContent = data.log.join("\n");
      logEl.scrollTop = logEl.scrollHeight;
    }
    if (modeEl && data) {
      modeEl.textContent = data.dry_run ? "dry-run" : "digest";
    }
  }

  function onFinished(data) {
    if (finished) return;
    finished = true;
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    if (resultUrl && data && data.status !== "idle") {
      window.location.href = resultUrl;
      return;
    }
    renderProgress(data);
    if (phaseEl) phaseEl.textContent = (data.progress && data.progress.phase_label) || "Complete";
    if (barEl) barEl.style.width = "100%";
  }

  function poll() {
    fetch(statusUrl, { credentials: "same-origin" })
      .then(function (resp) {
        return resp.json();
      })
      .then(function (data) {
        if (!data || data.status === "idle") {
          onFinished({ status: "idle", progress: { phase_label: "Idle", percent: 0 } });
          return;
        }
        renderProgress(data);
        if (data.status !== "running") {
          onFinished(data);
        }
      })
      .catch(function () {
        setText(detailEl, "Could not reach status endpoint — retrying…");
      });
  }

  poll();
  pollTimer = setInterval(poll, 1500);
})();
