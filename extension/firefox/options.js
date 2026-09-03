const DEFAULT_ORIGIN = "http://127.0.0.1:8765";
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

const originInput = document.getElementById("origin");
const tokenInput = document.getElementById("token");
const statusEl = document.getElementById("status");
const form = document.getElementById("options-form");
const testBtn = document.getElementById("test-connection");

function setStatus(message, kind) {
  statusEl.textContent = message;
  statusEl.className = "status" + (kind ? " " + kind : "");
}

function normalizeOrigin(raw) {
  var text = (raw || "").trim();
  var parsed;
  try {
    parsed = new URL(text);
  } catch (err) {
    return null;
  }
  if (parsed.protocol !== "http:") return null;
  if (parsed.username || parsed.password) return null;
  var host = parsed.hostname.toLowerCase();
  if (!LOOPBACK_HOSTS.has(host)) return null;
  return parsed.origin;
}

function originPattern(origin) {
  return origin.replace(/\/$/, "") + "/*";
}

async function ensureHostPermission(origin) {
  var origins = [originPattern(origin)];
  var allowed = await browser.permissions.contains({ origins: origins });
  if (allowed) return true;
  return browser.permissions.request({ origins: origins });
}

async function load() {
  var stored = await browser.storage.local.get({
    origin: DEFAULT_ORIGIN,
    token: "",
  });
  originInput.value = stored.origin || DEFAULT_ORIGIN;
  tokenInput.value = stored.token || "";
}

form.addEventListener("submit", async function (event) {
  event.preventDefault();
  var origin = normalizeOrigin(originInput.value);
  var token = (tokenInput.value || "").trim();
  if (!origin) {
    setStatus("Origin must be http://127.0.0.1, http://localhost, or http://[::1].", "error");
    return;
  }
  if (!token) {
    setStatus("Paste the capture token from Rollup → Articles.", "error");
    return;
  }
  var granted = await ensureHostPermission(origin);
  if (!granted) {
    setStatus("Firefox needs permission to reach that loopback origin.", "error");
    return;
  }
  await browser.storage.local.set({ origin: origin, token: token });
  originInput.value = origin;
  setStatus("Saved.", "ok");
});

testBtn.addEventListener("click", async function () {
  var origin = normalizeOrigin(originInput.value);
  var token = (tokenInput.value || "").trim();
  if (!origin || !token) {
    setStatus("Save a loopback origin and token first.", "error");
    return;
  }
  var granted = await ensureHostPermission(origin);
  if (!granted) {
    setStatus("Firefox needs permission to reach that loopback origin.", "error");
    return;
  }
  await browser.storage.local.set({ origin: origin, token: token });
  originInput.value = origin;
  setStatus("Testing…");
  try {
    var result = await browser.runtime.sendMessage({ type: "test-connection" });
    if (result && result.ok) {
      setStatus("Connected to Rollup web.", "ok");
      return;
    }
    var error = (result && result.error) || "request failed";
    if (error === "web_unavailable") {
      setStatus("Start Rollup web (rollup web) then try again.", "error");
      return;
    }
    if (error === "unauthorized") {
      setStatus("Token rejected. Copy it again from Articles.", "error");
      return;
    }
    if (error === "missing_token" || error === "invalid_origin") {
      setStatus("Save a loopback origin and token first.", "error");
      return;
    }
    setStatus("Could not connect (" + error + ").", "error");
  } catch (err) {
    setStatus("Start Rollup web (rollup web) then try again.", "error");
  }
});

load();
