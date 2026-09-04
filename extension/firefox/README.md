# Firefox Add to Rollup

Temporary Firefox add-on that enqueues the current page (or a link) into your
local Rollup webpage queue. It is ingest-only: Rollup still fetches the URL on
the next digest. It is not a read-later library.

Requires `rollup web` running on loopback (default `http://127.0.0.1:8765`).

## Load (unsigned MVP)

1. Start Rollup: `rollup web`
2. In Firefox open `about:debugging#/runtime/this-firefox`
3. **Load Temporary Add-on…** and choose [`manifest.json`](manifest.json) in this directory
4. Open Rollup **Articles**, copy the capture token
5. Open the add-on options, paste the token, **Save**, then **Test connection**

Temporary add-ons unload when Firefox restarts. AMO signing is out of scope for this MVP.

## Reload

- **Firefox restarted:** This Firefox → **Load Temporary Add-on…** → `manifest.json` again. Re-paste the token if options are empty.
- **Code edit, Firefox still open:** This Firefox → **Reload** next to Rollup. Manifest permission/`gecko.id` changes need Remove then load again.

Developer notes: [docs/design/firefox-capture.md](../../docs/design/firefox-capture.md#reload).

## Use

- Toolbar button **Add to Rollup** — current tab URL and title
- Context menu **Add to Rollup** — page, link, or tab

A notification reports added, already queued, a missing web UI, or a bad token.

## Pairing

The token lives in `{state_dir}/extension_token` (mode `0600`) and is shown on
`/articles`. Rotate it there if it leaks. Custom loopback ports need an extra
host permission when you save options.

The add-on POSTs JSON to `POST /articles/capture` with `Authorization: Bearer`.
It never sends page HTML.

## Developer notes

Packages, SLOC snapshot, known issues, and review checklist:
[docs/design/firefox-capture.md](../../docs/design/firefox-capture.md).
