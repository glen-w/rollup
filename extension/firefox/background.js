const DEFAULT_ORIGIN = "http://127.0.0.1:8765";
const TIMEOUT_MS = 3000;
const BLOCKED_SCHEME = /^(about|moz-extension|chrome|resource|file|blob|data|view-source|javascript):/i;
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1"]);

const MENU_ID = "add-to-rollup";

browser.menus.create(
  {
    id: MENU_ID,
    title: "Add to Rollup",
    contexts: ["page", "link", "tab"],
  },
  function () {
    void browser.runtime.lastError;
  }
);

browser.action.onClicked.addListener(function (tab) {
  addToRollup(tab && tab.url, tab && tab.title);
});

browser.menus.onClicked.addListener(function (info, tab) {
  if (info.menuItemId !== MENU_ID) return;
  if (info.linkUrl) {
    addToRollup(info.linkUrl, info.linkText || (tab && tab.title) || "");
    return;
  }
  addToRollup(
    info.pageUrl || (tab && tab.url),
    (tab && tab.title) || ""
  );
});

browser.runtime.onMessage.addListener(function (message) {
  if (message && message.type === "test-connection") {
    return testConnection();
  }
  return undefined;
});

function notify(title, message) {
  browser.notifications.create({
    type: "basic",
    iconUrl: "icons/icon-48.png",
    title: title,
    message: message,
  });
}

function isAddableUrl(url) {
  if (!url || typeof url !== "string") return false;
  if (BLOCKED_SCHEME.test(url)) return false;
  try {
    var parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch (err) {
    return false;
  }
}

function normalizeOrigin(raw) {
  var text = (raw || "").trim() || DEFAULT_ORIGIN;
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

async function getSettings() {
  var stored = await browser.storage.local.get({
    origin: DEFAULT_ORIGIN,
    token: "",
  });
  return {
    origin: stored.origin || DEFAULT_ORIGIN,
    token: stored.token || "",
  };
}

async function addToRollup(url, title) {
  if (!isAddableUrl(url)) {
    notify("Rollup", "This page can't be added.");
    return;
  }
  var settings = await getSettings();
  if (!settings.token) {
    notify("Rollup", "Paste the capture token from Articles into add-on options.");
    browser.runtime.openOptionsPage();
    return;
  }
  var origin = normalizeOrigin(settings.origin);
  if (!origin) {
    notify("Rollup", "Loopback origin is invalid. Check add-on options.");
    browser.runtime.openOptionsPage();
    return;
  }
  try {
    var result = await postCapture(origin, settings.token, url, title);
    if (result.status === 401) {
      notify("Rollup", "Capture token rejected. Copy a new token from Articles.");
      browser.runtime.openOptionsPage();
      return;
    }
    if (result.network) {
      notify("Rollup", "Start Rollup web (rollup web) then try again.");
      return;
    }
    if (!result.body || !result.body.ok) {
      var error = (result.body && result.body.error) || "request failed";
      if (error === "url_invalid" || error === "url_ssrf") {
        notify("Rollup", "This page can't be added.");
        return;
      }
      notify("Rollup", "Could not add to Rollup (" + error + ").");
      return;
    }
    if (result.body.outcome === "duplicate") {
      notify("Rollup", "Already in Rollup.");
      return;
    }
    if (result.body.outcome === "retried") {
      notify("Rollup", "Queued again for the next digest.");
      return;
    }
    notify("Rollup", "Added to Rollup.");
  } catch (err) {
    notify("Rollup", "Start Rollup web (rollup web) then try again.");
  }
}

async function postCapture(origin, token, url, title) {
  var controller = new AbortController();
  var timer = setTimeout(function () {
    controller.abort();
  }, TIMEOUT_MS);
  try {
    var payload = { url: url };
    if (title && String(title).trim()) {
      payload.title = String(title).trim().slice(0, 280);
    }
    var resp = await fetch(origin + "/articles/capture", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: "Bearer " + token,
      },
      body: JSON.stringify(payload),
      credentials: "omit",
      signal: controller.signal,
    });
    var body = null;
    try {
      body = await resp.json();
    } catch (err) {
      body = null;
    }
    return { status: resp.status, body: body, network: false };
  } catch (err) {
    return { status: 0, body: null, network: true };
  } finally {
    clearTimeout(timer);
  }
}

async function testConnection() {
  var settings = await getSettings();
  if (!settings.token) {
    return { ok: false, error: "missing_token" };
  }
  var origin = normalizeOrigin(settings.origin);
  if (!origin) {
    return { ok: false, error: "invalid_origin" };
  }
  var result = await postCapture(origin, settings.token, "", "");
  if (result.network) {
    return { ok: false, error: "web_unavailable" };
  }
  if (result.status === 401) {
    return { ok: false, error: "unauthorized" };
  }
  if (result.status === 400 && result.body && result.body.error === "url_invalid") {
    return { ok: true };
  }
  if (result.body && result.body.ok) {
    return { ok: true };
  }
  return { ok: false, error: (result.body && result.body.error) || "request failed" };
}
