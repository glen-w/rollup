# Design note: Firefox Add to Rollup

**Status:** shipped MVP (unsigned temporary add-on). Ingest-only; not a
read-later library. Thunderbird XPI remains a [non-goal](../CONTRACT.md).

Capture enqueues HTTPS URLs into the existing `webpage_queue`. The digest still
fetches the page later. `rollup web` must be running.

## Packages

The add-on has **no npm/bundler dependencies**. It uses Firefox WebExtensions
APIs only (`browser.action`, `browser.menus`, `browser.notifications`,
`browser.storage`, `browser.permissions`, `fetch`). Manifest V3, `gecko.id`
`addon@rollup.local`, `strict_min_version` 109.

The capture route adds **no new Python packages**. It uses the existing
`rollup[web]` extra (`flask>=3.1.3,<4`) plus stdlib (`hmac`, `secrets`). Queue
validation stays in `rollup.webpage.url` / `rollup.webpage.queue`.

## SLOC

Snapshot **2026-09-03** (`cloc`, excluding PNG and Markdown):

| Tree | Files | Code |
|------|-------|------|
| [`extension/firefox`](../../extension/firefox) (JS/CSS/HTML/JSON) | 5 | 433 |
| Capture server + tests (`articles.py`, `secrets.py`, `test_web_articles_capture.py`) | 3 | 494 |

`articles.py` includes the older HTML form routes (`/add`, remove, retry), not
only capture. Re-count:

```bash
cloc --exclude-ext=md,png extension/firefox
cloc src/rollup/web/routes/articles.py src/rollup/web/secrets.py tests/test_web_articles_capture.py
```

No live Firefox harness; pytest covers the HTTP contract only.

## Known issues

- **Unsigned / temporary.** Load via `about:debugging`. The add-on unloads when
  Firefox restarts. AMO signing is out of scope.
- **Firefox only.** No Chrome/Safari build. Manifest uses `background.scripts`
  (not Chrome `service_worker`).
- **`rollup web` required.** There is no nativeMessaging helper and no enqueue
  path that talks to SQLite directly.
- **Loopback HTTP.** Origin must be `http://127.0.0.1`, `http://localhost`, or
  `http://[::1]`. Custom ports need an extra host permission at save time.
  `http://[::1]/*` optional permission support varies by Firefox version.
- **Restricted pages.** `about:`, `moz-extension:`, `file:`, `blob:`, `data:`
  URLs are rejected in the add-on before POST.
- **Token on GET `/articles`.** Anyone who can read the loopback HTML can copy
  the token. Random websites cannot: no CORS. Do not put the token in query
  strings or TOML.
- **Do not “fix” pairing with CORS or CSRF exemptions.** Session cookies are
  `SameSite=Strict`, so `moz-extension://` cannot use `/articles/add`. Bearer
  on `/articles/capture` is the intended seam.
- **Test connection** POSTs an empty `url` (expects `400 url_invalid` if the
  token is good). It must not enqueue.
- **Notifications** require the Firefox permission prompt on first use.

## Code reviews

Review this surface as a **local capability**, not a public API.

**Must stay true**

- No `Access-Control-Allow-*` headers
- Form POSTs still require session CSRF; capture ignores CSRF and requires Bearer
- Host header remains loopback-only (`headers.trusted_host_allowed`)
- `{state_dir}/extension_token` is mode `0600`, no symlinks, never TOML
- `validate_queue_url` + `enqueue_url` unchanged for SSRF / duplicates
- Extension sends URL + title only (no HTML body)

**Test map** (`tests/test_web_articles_capture.py`): created / duplicate /
retried; invalid URL + SSRF; missing/wrong token; CSRF does not authorize
capture; form-urlencoded → 415; GET → 405; non-loopback Host → 400; rotate
invalidates; token file perms + symlink refuse.

**When reviewing a change**, prefer those tests over a browser walkthrough.
If you change pairing (token header, origin rules, JSON shape), update this
note, [`WEB.md`](../WEB.md), and [`extension/firefox/README.md`](../../extension/firefox/README.md).
