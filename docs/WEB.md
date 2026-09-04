# Rollup web UI

Local, single-user browser UI for browsing digests, rating emails, reviewing newsletter quality, operating the source registry / reader-body state, editing digest configuration, running guided digests, and managing Articles / LinkedIn / Reddit sources.

| Path | Surface |
|------|---------|
| `/rollups` | Archive (indexed digests) |
| `/run` | Run Studio (compose + background digest) |
| `/settings` | Configuration Centre (real TOML) |
| `/articles` | Webpage article queue |
| `POST /articles/capture` | Firefox Add to Rollup (Bearer token) |
| `/linkedin` | Named `fromMember` searches + layout + TTL |
| `/reddit` | Subreddits + layout + TTL |
| `/sources` | Quality ranking |
| `/sources/registry` | Source policy / aliases |
| `/admin` | Read-only health + body maintenance |

## Install / start

### Default: Docker

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up -d --build
open http://localhost:8765
```

Full guide (first-run path prep, mounts, Ollama): [DOCKER.md](DOCKER.md).

### Advanced: host Python / venv

```bash
pip install 'rollup[web]'
# or from a checkout:
pip install -e '.[web]'

rollup web
# optional:
rollup web --port 8765 --open
rollup web --host 0.0.0.0 --allow-non-loopback-bind   # Docker port mapping only
rollup web reindex   # backfill archive metadata from manifests/
```

Binds to **loopback only** (`127.0.0.1` by default; `::1` allowed). Non-loopback hosts are rejected at startup unless `--allow-non-loopback-bind` is passed (Docker port mapping). Host headers must match the configured loopback bind; forwarded-host headers are ignored.

Do not run native `rollup web` and Docker web at the same time (shared state DB).

Sticky TOML paths/profile defaults apply to `rollup web` the same way as digest (CLI flags still win). Pass `--config PATH` so Settings writes that file.

## Safety

- Never writes to Thunderbird/Gmail mail stores
- Web writes update `{state_dir}/rollup.db` (ratings, interaction, source policy overrides, run index, reader bodies) and, from Settings, the real digest **TOML** config (atomic save + `.bak` + timestamped backups under `{state_dir}/config-backups/`)
- CSRF tokens required on all POST forms; `POST /articles/capture` uses a Bearer capture token instead (extension pairing)
- Archived HTML artifacts are served as **attachments** (not inline)
- Digest Markdown/HTML generation is unchanged
- Reader bodies are capped plaintext (32,000 characters) with inline http(s) links; images and raw HTML are excluded
- Bodies never appear in manifests or default exports
- Session cookies: HttpOnly, SameSite=Strict, Secure=false for loopback HTTP
- All responses use `Cache-Control: private, no-store`

## Configuration Centre (`/settings`)

Edit sticky digest configuration in the browser: paths (with containment validation), default profile / lookback / folders / grouping, LLM enablement (`ollama` sticky) + provider/model + effort + **per-effort model ladders** + summary profile, output writers, LinkedIn/Reddit enablement (manage searches and subs on their own pages), **Google Scholar mode** (`default` vs `detailed` paper fetch), folder presentation (emoji / accent / display name / order), saved `[profiles.*]`, and `[ui]` personalisation. API keys and `--llm-api-base` are never sticky. Cookie copy + run: [EXAMPLES.md](EXAMPLES.md#linkedin-content-searches-opt-in-network).

Saves are previewed as an effective-config diff, confirmed with a one-time maintenance token, validated, backed up, and persisted atomically with optimistic concurrency (revision mismatch → re-preview). Digest settings are **not** stored in SQLite.

## Run Studio (`/run`)

Guided digest composer: pick a profile or temporary overrides, inspect the effective run (matched folders, LinkedIn search count when enabled, enabled Reddit subs, **articles in lookback**, writers, LLM enablement / provider privacy hints), dry-run discovery, then run a real digest as a **background subprocess** (single in-memory active-run slot — not a scheduler). The result page shows a live progress bar and log tail via polling `GET /run/status`; when the run finishes, the page refreshes to artifact links. Argv is built from the same sticky↔CLI registry as the CLI (`config_service.build_digest_argv` / `sticky_flags`), including `--linkedin` when `[linkedin].enabled` and `--reddit` when `[reddit].enabled` are set. The equivalent CLI / sample cron line is shown for automation (see [CRON.md](CRON.md)).

**Single-model run:** Compose includes a checkbox that forces every summary profile (plus group/fallback and final review) onto one model for that run only (`--single-model`, not sticky). When the LLM provider is Ollama, the dropdown is filled from local tags via `POST /run/ollama-models` (CSRF). GET `/run` never contacts Ollama. If Ollama is unreachable, the control falls back to a text field. LiteLLM uses a text field. Checking the box also enables LLM summaries (`--ollama`).

## Article library (`/articles`)

Add HTTPS article URLs for digest inclusion. URLs are stored in SQLite (`webpage_queue`), not TOML. The next digest fetches each new page at runtime (folder `webpage:queue`), caches the body, and includes the article when it was **saved within the lookback window** (same caching pattern as mail). Failed fetches stay **failed** for retry from the GUI. Pass `--no-webpage` on the CLI to skip. SSRF checks apply to user-supplied URLs.

**Firefox Add to Rollup:** load the unsigned add-on from [`extension/firefox`](../extension/firefox/README.md) (`about:debugging` → Load Temporary Add-on). Copy the capture token from Articles (file `{state_dir}/extension_token`, mode `0600`; never TOML). The toolbar button and context menu POST `{"url", "title"}` to `POST /articles/capture` with `Authorization: Bearer`. Session CSRF is unchanged; this route does not send CORS headers. `rollup web` must be running. Capture is ingest-only — not a read-later inbox. Rotate the token on Articles to invalidate the old value immediately. After a Firefox restart, load `manifest.json` again ([reload](design/firefox-capture.md#reload)). Developer notes (packages, SLOC, known issues, review, reload): [design/firefox-capture.md](design/firefox-capture.md).

## LinkedIn (`/linkedin`)

Add named `fromMember` content-search URLs, set a display name (and optional slug), toggle which searches to include, choose TOC layout (`feed` default / `per_source` / `per_search`), and set `fetch_ttl_hours` (default 24). Saved in TOML (`[linkedin.searches.*]`). GET never contacts LinkedIn. Session cookies stay in `~/.config/rollup/env`. Run Studio shows enabled search count with a Manage link.

## Reddit (`/reddit`)

Add public subreddit names, checkbox which subs to roll up, set global or per-sub sort/cap/mode, choose TOC layout (`feed` / `per_source`), and set `fetch_ttl_hours` (default 24). Fetches use a public JSON/RSS ladder; no credentials required. Optional Reddit script-app OAuth lives in `~/.config/rollup/env` (never the GUI). Unauthenticated fetches wait ~70s between subs; the page and Run Studio show the estimated wait for subs that would hit the network (cached subs show **cached**). Selections and overrides are saved in TOML (`[reddit]`). GET never contacts Reddit. Run Studio shows enabled sub count (and fetch ETA) with a Manage link.

## Read-only GET contract

Every web **GET** (Archive, Quality, Registry, Admin, Settings, Run, Articles, LinkedIn, Reddit, reader pages) opens the database with SQLite URI `mode=ro` and `PRAGMA query_only=ON`. Schema initialisation/`init_db` runs **once at web startup** only. GET handlers never migrate, create directories, write-probe paths, contact Ollama, or parse live mailboxes.

Admin deep diagnostics are **POST-only** (`POST /admin/deep-check` with CSRF). Deep-check opens **no write connection** — mutation routes open a short-lived mutator only after CSRF and form validation.

## Admin observability

Local **Admin** (`/admin`) shows panel-isolated health: web process settings, default digest effective configuration (pure config resolver), read-only doctor, schema, recent runs (index + bounded manifesto scan), and reader-body aggregates plus maintenance.

**Incomplete manifesto history:** a run whose manifesto write failed completely cannot be discovered through manifesto scanning. Do not treat the Admin run list as a complete failure log.

Manifest diagnostics are allowlisted, truncated, and redacted (no subjects, senders, bodies, URLs, or absolute paths). Directory listing is capped (`max_dir_entries`); candidates are parsed then sorted by persisted timestamp before retaining `max_files`.

## Source registry

- **Quality** (`/sources`): Bayesian ranking / content browsing
- **Registry** (`/sources/registry`): enable/disable, priority, always-surface, overrides, bulk actions
- Detail pages show inferred/observed facts vs user overrides with provenance
- Alias **preview + confirm** creation/merge only (no unalias in this UI)

## Reader bodies

Pipeline indexing stores plaintext bodies in `message_reader_bodies` when a digest run indexes entries. In the web UI, each entry card offers **Read newsletter** (lazy expander + full-page fallback at `/messages/<opaque>/body`).

CLI maintenance:

```bash
rollup bodies stats
rollup bodies check
rollup bodies backfill --dry-run
rollup bodies prune --dry-run
```

Web Admin maintenance (prune / backfill / delete-all / vacuum) scans the **newsletter root only** (must be configured and contained under mail root), not the whole Thunderbird account. Confirmation tokens use a **server-side one-time nonce store** (not session-cookie nonces alone), recompute-before-mutate under the application write lock, and invalidate on maintenance-generation change. Synchronous: the browser waits for completion.

Admin never shows body text — aggregates, versions, hashes, truncation and integrity codes only.

## Data model notes

- Ratings and interaction state are keyed by stable `message_key` and survive regenerating digests or deleting artifact files
- Quality ranking uses a Bayesian adjusted score with prior = mean of per-source means (read/save/dismiss rates are display-only)
- Indexing is transactional; failures leave the previous complete index intact
- Dry-run digests create no web index rows
- Sticky digest / UI preferences live in TOML; SQLite holds runtime index and user interaction state only

## Concurrent cron + web

Digest holds the run lock file; web rating/policy writes use short SQLite transactions and the shared state lock for registry bulk/alias. Run Studio refuses a second concurrent digest (busy). If the database is busy, POSTs return HTTP 503 with Retry-After. Do not raise SQLite busy timeout casually.

## Backup

Back up `{state_dir}/rollup.db` (and optionally `web_secret` and `extension_token`) to preserve ratings, interaction state, and extension pairing. Back up your `config.toml` (Settings also keeps `.bak` and timestamped copies under `config-backups/`).

## Related

- Product contract: [CONTRACT.md](CONTRACT.md)
- Comparison: [COMPARISON.md](COMPARISON.md)
- Roadmap: [ROADMAP.md](ROADMAP.md)
- Configuration: [CONFIG.md](CONFIG.md)
- Firefox capture (developer): [design/firefox-capture.md](design/firefox-capture.md)
