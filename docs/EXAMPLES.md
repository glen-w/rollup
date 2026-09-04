<p align="center">
  <img src="../assets/rollup_logo.png" alt="Rollup logo" width="96">
</p>

# Rollup example commands

Runnable examples for inventory, digest generation, summary routing, the local web UI, and local tooling.

**The rollup** is the weekly Markdown + HTML digest Rollup writes to `--output-dir`
(default `~/Documents/rollup-outputs`). Each run also copies `rollup_logo.png` and
`favicon.ico` beside the HTML file, and by default runs every output writer
(xteink / txt / json / epub). See [OUTPUT_WRITERS.md](OUTPUT_WRITERS.md).

Run from the project root with the virtualenv active:

```bash
cd /path/to/rollup
source .venv/bin/activate
```

See [README.md](../README.md) for setup, safety guarantees, and configuration defaults.
Optional sticky config: [CONFIG.md](CONFIG.md). Product shape: [CONTRACT.md](CONTRACT.md).
Position: [COMPARISON.md](COMPARISON.md). Roadmap: [ROADMAP.md](ROADMAP.md). Docker: [DOCKER.md](DOCKER.md).

**Default digest mode** needs no Ollama server and makes no network calls unless you pass `--linkedin` (or enable `[linkedin]` in TOML), `--reddit` (or enable `[reddit]` in TOML), webpage queue fetches (on by default; `--no-webpage` to skip), `--scholar-mode detailed` (or `[scholar].mode = "detailed"`), or `--ollama`. Pass `--ollama` only when you want LLM summaries from a local Ollama instance.

If you pass summary flags (for example `--summary-profile`) without `--ollama`, Rollup ignores them and prints a warning.

Mode shorthand: `--dry-run` parses only and writes nothing; preview-summary mode
is the normal no-Ollama digest; `--ollama` enables entry LLM summaries;
`--final-review` runs whole-digest QA and can use Ollama even without `--ollama`;
`--final-review-mode report` writes advisory QA only, while `apply` applies
validated summary-only fixes.

## Install

| Method | Command |
|--------|---------|
| PyPI (when published) | `pip install rollup` |
| Git checkout | `pip install .` |
| Editable + tests | `pip install -e ".[dev,web]"` |
| Web UI only | `pip install 'rollup[web]'` |
| LiteLLM providers | `pip install 'rollup[llm]'` |
| EPUB writer | `pip install 'rollup[epub]'` |
| uv | `uv sync --extra dev --extra web` |
| Docker | [DOCKER.md](DOCKER.md) |

## Inventory

Discover mbox folders and message counts (read-only; no body parsing):

```bash
python -m rollup inventory
python -m rollup inventory --root tests/fixtures/Newsletters.sbd
python -m rollup inventory --json-out ./output/inventory.json
# After setting root in ~/.config/rollup/config.toml, bare inventory uses that path:
python -m rollup inventory
```

## Digest without Ollama (default)

Preview and generate digests with no Ollama server and no network calls (unless LinkedIn, Reddit, or webpage fetch is enabled). `--no-ollama` is optional — it is the default when neither `--ollama` nor `--no-ollama` is passed.

```bash
python -m rollup digest
python -m rollup digest --root tests/fixtures/Newsletters.sbd
python -m rollup digest --lookback-days 7
python -m rollup digest --dry-run --root tests/fixtures/Newsletters.sbd
python -m rollup digest --folder tech --exclude-folder hoops
python -m rollup digest --include-seen-undated
python -m rollup digest --cron --root tests/fixtures/Newsletters.sbd
python -m rollup digest --no-grouping --root tests/fixtures/Newsletters.sbd
python -m rollup digest --grouping-report --root tests/fixtures/Newsletters.sbd
```

After a digest, browse the indexed archive (requires `pip install 'rollup[web]'`):

```bash
python -m rollup web --open
```

## LinkedIn content searches (opt-in network)

Author-list (`fromMember`) content-search URLs become digest sections. Default
layout is **`feed`** (one `linkedin:feed` section). Set `layout = "per_search"`
to keep each named search as `linkedin:<slug>` (needed for `--folder linkedin:general`
below). Fetch uses LinkedIn Voyager with **your** browser session.
Link posts also fetch the linked article body by default (external blogs, Pulse).
Reference: [CONFIG.md](CONFIG.md#linkedin-content-searches-optional).

### 1. Save a fromMember search URL

1. Log in at [linkedin.com](https://www.linkedin.com) in a desktop browser.
2. Open **Search**, switch the results type to **Content** (not People or Jobs).
3. Filter by the people you want (`fromMember`). The address bar must contain
   `/search/results/content/` and `fromMember=` with `ACo…` ids.
4. Copy that URL. Save it on the web **LinkedIn** page (`/linkedin`) with a
   display name, or in TOML. Store URLs only — never cookies.

```toml
# ~/.config/rollup/config.toml  (or ./rollup.toml)
[linkedin]
enabled = true
article_fetch = true   # default; set false to skip linked-article HTTP
layout = "per_search"  # named section per search; omit for default feed (`linkedin:feed`)

[linkedin.searches.general]
url = "https://www.linkedin.com/search/results/content/?origin=FACETED_SEARCH&datePosted=%5B%22past-week%22%5D&fromMember=%5B%22ACoAAAMN5aEBk7L5BGyjHbFsDr40zYqwuSB7tlw%22%2C%22ACoAAA5GcN4BlMrjuK1OVX4Q63rShHLMZuQ1Qyg%22%5D"
display_name = "General"
enabled = true
```

Keyword-only search, company pages, follows, and mentions are not supported yet.

### 2. Copy session cookies (li_at and JSESSIONID)

Voyager needs two cookies from the **same** logged-in browser pane. They are
session secrets: put them in the process environment only. Never TOML, Settings,
git, or screenshots you share.

| Cookie | Environment variable | Role |
|--------|----------------------|------|
| `li_at` | `ROLLUP_LINKEDIN_LI_AT` | Session |
| `JSESSIONID` | `ROLLUP_LINKEDIN_JSESSIONID` | Voyager CSRF (`ajax:…`) |

**Chrome / Edge / Brave**

1. Stay logged in on `https://www.linkedin.com`.
2. Open DevTools (`Cmd+Option+I` on macOS, `F12` elsewhere).
3. **Application** (Chrome/Edge) or **Application / Storage** → **Cookies** →
   `https://www.linkedin.com`.
4. Find `li_at`. Copy the **Value** column (a long `AQE…` string).
5. Find `JSESSIONID`. Copy the value. It usually looks like `ajax:123…` or
   `"ajax:123…"`. Surrounding quotes are optional; Rollup accepts either.

**Firefox**

1. DevTools (`Cmd+Option+I`) → **Storage** → **Cookies** → `https://www.linkedin.com`.
2. Copy `li_at` and `JSESSIONID` values as above.

**Safari**

1. Enable **Develop** menu (Settings → Advanced → Show features for web developers).
2. **Develop → Show Web Inspector → Storage → Cookies** for `linkedin.com`.
3. Copy `li_at` and `JSESSIONID`.

They rotate together. After a 401, refresh **both** from the same pane. Treat a
new `li_at` with a stale `JSESSIONID` as expired.

### 3. Export and run

In the same shell you use for `rollup` (do not commit these lines):

```bash
export ROLLUP_LINKEDIN_LI_AT='AQE…'          # li_at value
export ROLLUP_LINKEDIN_JSESSIONID='ajax:…'  # JSESSIONID value
```

Dry-run first (no LinkedIn HTTP; warns if cookies are missing):

```bash
python -m rollup digest --linkedin --dry-run
```

Then fetch. `--linkedin` is required unless `[linkedin].enabled = true` in TOML:

```bash
python -m rollup digest --linkedin --folder linkedin:general
python -m rollup digest --linkedin --lookback-days 7
python -m rollup digest --linkedin --linkedin-refresh   # bypass listing cache
python -m rollup digest --no-linkedin          # mail only, even if TOML enables LinkedIn
python -m rollup digest --linkedin --no-linkedin-article-fetch   # posts only, no article HTTP
```

A successful fetch logs `Fetching LinkedIn fromMember feed (N authors) via Voyager`.
With `layout = "per_search"` (as in the TOML above), posts land in `linkedin:<slug>`
(here `linkedin:general`) as **standalone** entries. Default `feed` layout uses
`linkedin:feed` instead. Link posts use the article title as the subject when
Voyager exposes one, and append the fetched article body after the commentary teaser.

Mute a noisy author:

```bash
python -m rollup sources disable li:member:ACoAAA5GcN4BlMrjuK1OVX4Q63rShHLMZuQ1Qyg
```

For launchd/cron, `~/.config/rollup/env` is loaded at CLI startup. You can also
put the same two variables in the job environment (plist `EnvironmentVariables`).
See [CRON.md](CRON.md#environment-variables-network-sources).
If fetch fails, see
[TROUBLESHOOTING.md](TROUBLESHOOTING.md#linkedin-fetch-failed-401--429--checkpoint).

## Reddit subreddits (opt-in network)

Public listings via a transport ladder (optional OAuth JSON → public JSON →
www RSS → old.reddit RSS). No Reddit account is required. Sections land in
`reddit:feed` or `reddit:<sub>` depending on `layout`. Listings reuse a SQLite
cache within `fetch_ttl_hours` (default 24). Reference:
[CONFIG.md](CONFIG.md#reddit-subreddits-optional).

### TOML

```toml
[reddit]
enabled = true
layout = "feed"          # feed | per_source
sort = "hot"

[reddit.subs.python]
enabled = true

[reddit.subs.machinelearning]
enabled = true
mode = "posts"
limit = 5
```

Or use **Configuration Centre → Reddit** (`/reddit`) to add subs, toggle inclusion,
and set per-sub mode/sort/cap (saved in TOML). The Reddit page and Run Studio show
the estimated fetch wait (70s between subs; ~1 request/minute).

### CLI

Dry-run first (no Reddit HTTP):

```bash
python -m rollup digest --reddit --dry-run
```

Then fetch. `--reddit` is required unless `[reddit].enabled = true` in TOML:

```bash
python -m rollup digest --reddit --lookback-days 7
python -m rollup digest --reddit --folder reddit:feed
python -m rollup digest --reddit --reddit-refresh   # bypass listing cache
python -m rollup digest --no-reddit          # mail only, even if TOML enables Reddit
```

Mute a noisy subreddit:

```bash
rollup sources disable reddit:sub:python
```

Optional script-app OAuth (never TOML) goes in `~/.config/rollup/env` — see [CONFIG.md](CONFIG.md#optional-oauth-environment-only). Failures: [TROUBLESHOOTING.md](TROUBLESHOOTING.md#reddit-fetch-failed-429--missing-sub).

## Google Scholar alerts

Scholar emails already in Thunderbird. Default mode keeps them as item-list mail
(no extra network). Detailed mode fetches each paper and summarises it.
Reference: [CONFIG.md](CONFIG.md#google-scholar-alerts-optional-detailed-mode).

```toml
[scholar]
mode = "detailed"
max_papers_per_email = 8
max_fetches_per_run = 40
```

```bash
python -m rollup digest --scholar-mode default
python -m rollup digest --ollama --scholar-mode detailed
python -m rollup digest --scholar-mode detailed --dry-run   # parse papers, no HTTP
```

Or set mode and caps in **Settings → Google Scholar**. Registry source detail
shows a banner when the source looks like Scholar.

## Webpage articles (opt-in network)

HTTPS article URLs live in SQLite (`webpage_queue`), not TOML. Add URLs
from the web **Articles** page (`/articles`), the Firefox **Add to Rollup**
add-on, or enqueue programmatically. The next
digest fetches pending rows once into folder `webpage:queue` and caches the body.
Later digests reuse that cache and include an article when it was **saved within
the lookback window** (same rule as email dates). Failed fetches stay **failed**
for retry. Pass `--no-webpage` to skip. Reference: [CONFIG.md](CONFIG.md#webpage-articles-optional).

### Web UI

```bash
python -m rollup web --open
# Articles → add HTTPS URL → Run Studio → run digest
```

### Firefox extension

Load [`extension/firefox`](../extension/firefox/README.md) as a temporary add-on
(`about:debugging#/runtime/this-firefox`). Copy the capture token from
**Articles**, paste it into add-on options, then use the toolbar or context menu
**Add to Rollup**. The add-on POSTs to `/articles/capture`; `rollup web` must be
running. Capture is ingest-only (the next digest fetches the URL).

### CLI

Digest with fixture mail plus any pending queue URLs (network fetch for pending rows):

```bash
python -m rollup digest \
  --root tests/fixtures/Newsletters.sbd \
  --mail-root tests/fixtures \
  --lookback-days 3650 \
  --no-ollama

python -m rollup digest --no-webpage   # skip webpage queue even when pending rows exist
python -m rollup digest --folder webpage:queue --no-ollama   # queue only
```

Mute a noisy site in the source registry:

```bash
python -m rollup sources disable web:host:example.com
```

## Doctor

```bash
python -m rollup doctor --root tests/fixtures/Newsletters.sbd
python -m rollup doctor --json --root tests/fixtures/Newsletters.sbd
python -m rollup doctor --full --root tests/fixtures/Newsletters.sbd
```

Inspect merged TOML + profile:

```bash
python -m rollup config print
python -m rollup --config ./rollup.toml config print --profile daily
```

## Cron helpers (launchd preferred on macOS)

```bash
python -m rollup cron print-launchd --python "$(which python)" --workdir .
python -m rollup cron print-crontab --python "$(which python)" --workdir .
python -m rollup cron status
```

See [docs/CRON.md](CRON.md) for weekly non-AI digest scheduling.

## Digest with Ollama (recommended full run)

Requires a running local Ollama server (`--ollama` enables network calls to loopback by default).

**Recommended full run** (all folders, 7-day lookback, per-type model routing):

```bash
python -m rollup digest --ollama --summary-routing-report
```

`--ollama` alone enables type routing by default. Use `--summary-routing-report` to print which profiles and models were used.

### LiteLLM / external providers

Requires `pip install 'rollup[llm]'` and provider credentials in the environment
(for example `OPENAI_API_KEY`). `--ollama` still enables LLM calls; choose the
transport with `--llm-provider`:

```bash
python -m rollup digest --ollama \
  --llm-provider litellm \
  --llm-model openai/gpt-4o \
  --summary-routing-report
```

Optional OpenAI-compatible / proxy base (CLI only, never sticky):

```bash
python -m rollup digest --ollama \
  --llm-provider litellm \
  --llm-model openai/gpt-4o \
  --llm-api-base http://127.0.0.1:4000
```

Do not use LiteLLM model strings that route native Ollama (`ollama/…`); use
`--llm-provider ollama` instead. Cron/launchd snippets may include
`--llm-provider` / `--llm-model`; secrets must be present in the scheduler
environment.

### Inspect profiles and routes

```bash
python -m rollup digest --list-summary-profiles
python -m rollup digest --list-newsletter-types
```

### Routing modes

Single profile for the whole digest:

```bash
python -m rollup digest --ollama --summary-profile standard
python -m rollup digest --ollama --summary-profile deep
```

Explicit per-type routing (same as `--ollama` default):

```bash
python -m rollup digest --ollama --summary-type-routing --summary-routing-report
```

Disable per-type routing and use the `standard` profile for every message:

```bash
python -m rollup digest --ollama --no-summary-type-routing --summary-routing-report
```

Compare multiple profiles side by side (writes one rollup set per profile):

```bash
python -m rollup digest --ollama --summary-variants rough,standard,deep --summary-routing-report
```

Variant mode writes files such as:

- `output/2026-07-02-newsletter-digest.rough.md`
- `output/2026-07-02-newsletter-digest.deep.html`
- `output/rollup_logo.png`
- `output/favicon.ico`

### Smoke tests and cache control

```bash
python -m rollup digest --ollama --folder tech --lookback-days 7 --summary-routing-report
python -m rollup digest --ollama --rebuild-summaries --folder tech --lookback-days 7
python -m rollup digest --ollama --rebuild-summaries --summary-routing-report
```

Re-run without `--rebuild-summaries` to confirm cache hits in the stats block.

### Effort presets (machine power)

Swap the whole summary ladder and companion defaults in one flag:

```bash
python -m rollup digest --list-efforts
python -m rollup digest --ollama --effort light --lookback-days 7
python -m rollup digest --ollama --effort high --lookback-days 7 --summary-routing-report
python -m rollup doctor --ollama --effort high --network
```

`balanced` matches today's built-in defaults. Override models inside an effort with `[efforts.high]` (and friends) in TOML or Settings. Explicit `--ollama-model` / `--final-review-model` / `--max-chars-for-llm` still win over the preset. Do not combine `--effort` with `--summary-profile-set`.

Use one model for every profile on a single run (not sticky):

```bash
python -m rollup digest --ollama --effort balanced --single-model qwen2.5:7b --lookback-days 7
python -m rollup doctor --ollama --single-model qwen2.5:7b --network
```

### XTEINK e-ink output

Write a second, device-optimized digest (short lines, no URLs) beside the normal files via the **xteink** output writer:

```bash
python -m rollup digest --xteink --lookback-days 7
python -m rollup digest --output xteink --lookback-days 7
python -m rollup digest --xteink --ollama --effort high --lookback-days 7
```

Outputs look like `…-newsletter-digest.xteink.md`. Skipped under `--dry-run`. See [XTEINK_USAGE.md](XTEINK_USAGE.md).

### JSON, TXT, and EPUB outputs

By default these run automatically with every digest. To write only a subset
(or Markdown/HTML alone):

```bash
python -m rollup digest --output json --output txt --lookback-days 7
python -m rollup digest --output none --lookback-days 7
# EPUB needs: pip install 'rollup[epub]'
python -m rollup digest --output epub --lookback-days 7
```

Same run stem as the core digest with `.json` / `.txt` / `.epub` extensions. See [OUTPUT_WRITERS.md](OUTPUT_WRITERS.md).

### Custom profile sets

Export built-in profiles, edit model names to match your local Ollama library, then run:

```bash
python -m rollup digest --export-summary-profile-set ./output/summary_profiles.json
python -m rollup digest --ollama --summary-profile-set ./output/summary_profiles.json --summary-routing-report
```

Each profile supports Ollama generation fields:

| Field | Default | Notes |
|-------|---------|-------|
| `num_predict` | `2048` | Max generated tokens (`options.num_predict` in the Ollama request) |
| `think` | `false` | Top-level Ollama `think` flag: `false`/`true` for Qwen3; GPT-OSS needs `"low"`/`"medium"`/`"high"` (booleans are ignored) |
| `options` | `{}` | Additional Ollama model options (temperature is set separately via `temperature`) |

Example fragment after export — adjust model names and generation settings:

```json
{
  "schema_version": 1,
  "default_profile": "standard",
  "profiles": {
    "max": {
      "provider": "ollama",
      "model": "qwen3.6:27b",
      "prompt_style": "deep",
      "temperature": 0.2,
      "num_ctx": 65536,
      "timeout_seconds": 600,
      "num_predict": 2048,
      "think": false,
      "enabled": true,
      "description": "Long essays",
      "options": {}
    }
  },
  "type_routes": {
    "essay": "max"
  }
}
```

Do **not** put `think` or `num_predict` inside `options` — Ollama ignores `think` there on `/api/generate`, which causes empty summaries on thinking models. For GPT-OSS, set `"think": "low"` (not `false`) and keep enough `num_predict` headroom for reasoning + the visible summary.

Inspect loaded profiles:

```bash
python -m rollup digest --list-summary-profiles
```

Example model pulls for the built-in profile set:

```bash
ollama pull llama3.2:3b
ollama pull qwen2.5:7b
ollama pull gpt-oss:20b
ollama pull qwen3.6:27b
```

## Live-run workflow

Incremental checks before a full live digest:

```bash
python -m rollup inventory --root tests/fixtures/Newsletters.sbd
python -m rollup digest --root tests/fixtures/Newsletters.sbd
# Prefer sticky paths in ~/.config/rollup/config.toml, then:
python -m rollup inventory
python -m rollup digest --folder tech
python -m rollup digest --profile weekly
python -m rollup digest
python -m rollup digest --ollama --folder tech --summary-routing-report
python -m rollup digest --ollama --summary-routing-report
```

### Migrate from hardcoded paths

```toml
# ~/.config/rollup/config.toml
root = "~/email/gmail/Newsletters.sbd"
mail_root = "~/email/gmail"

[folders.tech]
emoji = "💻"
accent = "#4a7fd4"
```

```bash
rollup config print
rollup doctor
rollup digest
```

Explicit `--no-ollama` is equivalent to omitting both `--ollama` and `--no-ollama`.

Optional gitignored local mail copy:

```bash
cp -R "$HOME/email/gmail/Newsletters.sbd" ./fixtures/Newsletters.sbd
python -m rollup digest --root ./fixtures/Newsletters.sbd
```

## Ollama validation sequence

```bash
python -m rollup digest --list-summary-profiles
python -m rollup digest --list-newsletter-types
python -m rollup digest --ollama --folder tech --lookback-days 7 --summary-routing-report
python -m rollup digest --ollama --summary-profile standard --folder tech --lookback-days 7
python -m rollup digest --ollama --rebuild-summaries --folder tech --lookback-days 7
python -m rollup digest --ollama --summary-routing-report
```

Stop Ollama and re-run a smoke command to confirm preview fallback without crashing:

```bash
python -m rollup digest --ollama --folder tech --lookback-days 7
```

## Final review (editorial QA)

Run a whole-digest editorial QA pass after assembly. Report-only by default: writes a JSON sidecar and does **not** change digest content. Advisory only.

```bash
python -m rollup digest --root ./fixtures/Newsletters.sbd --final-review
python -m rollup digest --root ./fixtures/Newsletters.sbd --final-review --final-review-profile concise
python -m rollup digest --root ./fixtures/Newsletters.sbd --final-review --final-review-report ./output/review.json
python -m rollup digest --root ./fixtures/Newsletters.sbd --final-review --no-final-review-cache
```

Final review does not require `--ollama` on the digest summarisation path (it uses Ollama independently when enabled). Apply mode (`--final-review-mode apply`) applies validated summary-only patches linked to `safe_auto_fix: true` issues; missing fingerprint echoes skip the whole set. Under `--cron`, apply also requires `--final-review-allow-cron-apply` and conservative whole-set caps. When enabled, a short QA summary also appears in the digest’s collapsed “Digest generation details” section at the end.

```bash
# Apply mode (interactive / manual)
# mail_root must contain the newsletter root (omit --mail-root to infer the .sbd parent)
rollup digest --root tests/fixtures/Newsletters.sbd --final-review \
  --final-review-mode apply \
  --output-dir /tmp/rollup-out --state-dir /tmp/rollup-state

# Group-level LLM summaries (opt-in; requires Ollama)
rollup digest --root tests/fixtures/Newsletters.sbd --ollama --group-summaries \
  --output-dir /tmp/rollup-out --state-dir /tmp/rollup-state
```

## Source registry

Persistent per-newsletter controls (see [SOURCES.md](SOURCES.md)):

```bash
# After a digest has observed senders
rollup sources list --state-dir /tmp/rollup-state
rollup sources show from:alerts@github.com --state-dir /tmp/rollup-state --json

# Disable a noisy source
rollup sources disable from:noisy@example.com --state-dir /tmp/rollup-state

# Force a daily newsletter into weekly sender batches
rollup sources set list:news.example.com --grouping sender_batch --priority 80 \
  --state-dir /tmp/rollup-state

# Always surface undated notifications from a source (still within lookback)
rollup sources set from:alerts@github.com --always-surface --state-dir /tmp/rollup-state

# Override a misclassified type
rollup sources set from:editor@daily.example --type essay --state-dir /tmp/rollup-state

# Backup / restore overrides
rollup sources export --out /tmp/sources.json --state-dir /tmp/rollup-state
rollup sources import --from /tmp/sources.json --state-dir /tmp/rollup-state
```

## Local web UI

Browse indexed rollups, rate emails, and review newsletter quality. Binds to **loopback only** (`127.0.0.1` by default). Pass `--allow-non-loopback-bind` only for Docker port mapping. Requires the optional Flask extra.

Bring-up (install → digest that indexes into state → start the UI):

```bash
pip install 'rollup[web]'   # or from a checkout: pip install -e '.[web]'
python -m rollup digest --root tests/fixtures/Newsletters.sbd
python -m rollup web --open
```

**Configuration Centre** (`/settings`) edits the real digest TOML (paths, profiles, writers, folder themes). **Run Studio** (`/run`) previews the effective run, dry-runs discovery, and starts a digest without composing CLI by hand. **Articles** / **LinkedIn** / **Reddit** manage those sources. See [WEB.md](WEB.md) and [CONFIG.md](CONFIG.md).

Variants:

```bash
python -m rollup web
python -m rollup web --host 127.0.0.1 --port 8765 --open
python -m rollup web --host 0.0.0.0 --port 8765 --allow-non-loopback-bind   # Docker only
python -m rollup web --config ~/.config/rollup/config.toml --open
python -m rollup web reindex --state-dir ./state --output-dir ./output
```

See [WEB.md](WEB.md) for security model, Settings / Run Studio / Articles / LinkedIn / Reddit, quality score, and backup notes.

Reader-body cache (same SQLite; Admin can prune/backfill too):

```bash
python -m rollup bodies stats
python -m rollup bodies check
python -m rollup bodies backfill --dry-run
```

## Docker (optional)

Run web + digest in one container while sharing the same config, state, output, and mail paths as native CLI. See [DOCKER.md](DOCKER.md).

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up -d --build
open http://localhost:8765
```

The image binds with `--allow-non-loopback-bind` so Flask can listen on `0.0.0.0` inside the container. Compose publishes `127.0.0.1:8765:8765`; access from the host uses `http://localhost:8765` (Host-header loopback checks unchanged). Override the port mapping only if you intentionally need LAN access.

Fixture smoke test without real mail:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
curl -fsS http://127.0.0.1:8765/rollups
```

## Benchmark local models

Compare local Ollama-compatible models on fixed prompts:

```bash
python scripts/benchmark_ollama_models.py \
  --models llama3.2:3b,qwen2.5:7b,gpt-oss:20b,qwen3.6:27b \
  --runs 2 \
  --num-ctx 16384 \
  --out benchmarks/ollama_benchmark.json \
  --markdown-out benchmarks/ollama_benchmark.md
```

## Tests and fixtures

```bash
python -m pytest tests/ -v
python tests/generate_fixtures.py
```
