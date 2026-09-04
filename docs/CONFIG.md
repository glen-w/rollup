# Rollup configuration

Optional TOML config, run profiles, folder themes, and path discovery.
CLI flags always win over files. The web **Configuration Centre** (`/settings`)
edits the same TOML via a shared config service (atomic write, backup, optimistic
concurrency) — there is no separate SQLite “web settings” store for digest config.

## Precedence

1. Built-in defaults (`weekly` profile, `balanced` effort, deterministic folder accents)
2. `~/.config/rollup/config.toml`
3. `./rollup.toml` (current working directory)
4. Selected `--profile` (builtin or `[profiles.*]` from TOML)
5. Explicit CLI flags

Pass `--config PATH` to load a single file instead of the search paths.

The Configuration Centre writes to `--config` when the web process was started with
it; otherwise `./rollup.toml` when that file exists; otherwise
`~/.config/rollup/config.toml` (created on first save).

Inspect the merge:

```bash
rollup config print
rollup --config ./rollup.toml config print --profile daily
```

## Sticky keys ↔ CLI flags

Sticky TOML keys (top-level or under `[profiles.*]`) map to digest CLI flags
through a single registry in `rollup.sticky_flags`. That module drives both:

- applying sticky values onto argparse when the flag was **not** passed on the CLI
  (`apply_sticky_to_namespace`)
- building Run Studio / display argv from an effective sticky view
  (`config_service.build_digest_argv` → `sticky_to_argv`)

When you add a new sticky key that should appear on the CLI, extend
`STICKY_FLAG_SPECS` (and `STICKY_KEYS` in `user_config`) together — coverage is
asserted in tests. The sticky key `profile` is resolved via `--profile` /
`EffectiveConfigView.profile_name`, not the sticky→argv body.

| TOML key | CLI |
|----------|-----|
| `lookback_days` | `--lookback-days` |
| `effort` | `--effort` |
| `grouping_min_size` | `--grouping-min-size` |
| `ollama` | `--ollama` / `--no-ollama` |
| `no_grouping` | `--no-grouping` / `--grouping` |
| `ollama_model`, `llm_model`, `llm_provider`, `summary_profile` | matching `--*` flags |
| `root`, `mail_root`, `output_dir`, `state_dir`, `log_dir` | matching `--*` flags |
| `folder`, `exclude_folder` | `--folder`, `--exclude-folder` |
| `output` | `--output` |
| `profile` | `--profile` (not sticky→argv body) |

`[linkedin]`, `[reddit]`, `[scholar]`, `[folders]`, `[profiles.*]`, `[efforts.*]`, and `[ui]` are
separate TOML tables, not sticky keys. `[linkedin].enabled` / `[reddit].enabled`
still inject `--linkedin` / `--reddit` when neither enable nor disable flag is on
the CLI. `[scholar].mode = "detailed"` (or `--scholar-mode detailed`) is mail
enrichment, not a new ingest source.

Scheduler helpers use a **separate** `cron_helpers.build_scheduled_digest_argv` (paths +
`--cron`); do not confuse it with the sticky registry.

## Minimal config

```toml
root = "~/Library/Thunderbird/Profiles/….default/Mail/…/Newsletters.sbd"
mail_root = "~/Library/Thunderbird/Profiles/….default/Mail/…"
output_dir = "~/Documents/rollup-outputs"
lookback_days = 7
effort = "balanced"
```

`output_dir` should live **outside** the mail root (and preferably outside the
git checkout). Default is `~/Documents/rollup-outputs`. Override with
`--output-dir` or sticky `output_dir` in config.toml.

Each digest run keeps only the **current batch** in the output root (dated
Markdown/HTML plus any writer artifacts). Prior batches are moved into
`output_dir/archive/` automatically. `latest.md` / `latest.html` (when
`--latest` is used) and branding assets stay in the root.

## Output writers

By default every discovered writer runs (xteink, txt, json, epub, …). Restrict or
disable via CLI or sticky config:

```toml
output = ["json", "txt"]   # subset
# output = "none"          # Markdown/HTML only
# output = "all"           # explicit default-all
```

## Folder themes

By default folders get a stable accent color from their name (no emoji).
Override per folder:

```toml
[folders.tech]
emoji = "💻"
accent = "#4a7fd4"
display_name = "Technology"
order = 10

[folders.hoops]
emoji = "🏀"
accent = "#e8923a"
order = 20
```

`display_name` replaces the raw folder name in digests; `order` sorts sections
(lower first), then alphabetical.

## Summaries (sticky)

```toml
ollama = true                  # enable LLM calls (not Ollama-only)
ollama_model = "llama3.2"      # Ollama default/fallback/group model
llm_provider = "ollama"        # ollama | litellm (fallback/group path)
llm_model = "openai/gpt-4o"    # LiteLLM model when llm_provider = litellm
summary_profile = "standard"
effort = "balanced"
```

API keys and `--llm-api-base` are **not** sticky (CLI/env only). Custom summary
profile JSON may set `"provider": "litellm"` per profile; that overrides the
global `llm_provider` for those jobs only.

`--final-review` is independent of `--ollama`: it can run whole-digest QA on a
preview-summary digest. `--group-summaries` still requires `--ollama`.

Built-in summary profiles (`--list-summary-profiles`) and default type routes:

| Profile | Typical model (`balanced`) | Use |
|---------|----------------------------|-----|
| `rough` | `llama3.2:3b` | Short updates and link roundups |
| `standard` | `qwen2.5:7b` | Default balanced profile |
| `deep` | `gpt-oss:20b` | Analytical or policy-heavy items |
| `max` | `qwen3.6:27b` | Long essays |

| Newsletter type | Profile |
|-----------------|---------|
| `short_update`, `link_roundup` | `rough` |
| `multi_section_digest`, `unclassified` | `standard` |
| `essay` | `max` |
| `item_list` | `preserve` |
| `academic_paper` | `deep` |

Custom profile JSON, `think` / `num_predict`, and routing flags: [EXAMPLES.md](EXAMPLES.md#digest-with-ollama-recommended-full-run).

## Effort model overrides

Built-in `light` / `balanced` / `high` ladders keep the same profile names and
type routes. `--list-efforts` prints the effective bundle. Built-in defaults:

| Effort | Rough | Standard | Deep | Max | Group / fallback | Final review | `max_chars_for_llm` |
|--------|-------|----------|------|-----|------------------|--------------|---------------------|
| `light` | `llama3.2:3b` | `llama3.2:3b` | `qwen2.5:7b` | `qwen2.5:7b` | `llama3.2:3b` | `qwen2.5:7b` | 20_000 |
| `balanced` (default) | `llama3.2:3b` | `qwen2.5:7b` | `gpt-oss:20b` | `qwen3.6:27b` | `llama3.2:3b` | `qwen2.5:7b` | 30_000 |
| `high` | `qwen2.5:7b` | `gpt-oss:20b` | `qwen3.6:27b` | `qwen3.6:27b` | `qwen2.5:7b` | `gpt-oss:20b` | 50_000 |

Override **models** per effort in TOML (or the Configuration Centre):

```toml
[efforts.high]
rough = "qwen2.5:14b"
standard = "gpt-oss:20b"
deep = "qwen3:32b"
max = "qwen3:32b"
ollama_model = "qwen2.5:14b"
final_review_model = "gpt-oss:20b"
```

Omitted keys keep the built-in default. A sticky `ollama_model` or CLI
`--ollama-model` / `--final-review-model` still wins for the group/fallback and
review companions. `--list-efforts` and doctor show the effective models after
overrides. Custom `--summary-profile-set` JSON is unchanged (and still cannot
combine with `--effort`).

### Single-model override (run only)

`--single-model NAME` points every summary profile, group/fallback, and final
review at one model for **this run only** (not sticky). Effort still controls
`max_chars_for_llm`, timeouts, and routing. Useful when you want one Ollama tag
without editing the whole ladder:

```bash
rollup digest --ollama --effort balanced --single-model qwen2.5:7b
rollup doctor --ollama --single-model qwen2.5:7b --network
```

Run Studio exposes the same knob in **Compose**: a checkbox plus, for Ollama, a dropdown of local tags (filled after the page loads via `POST /run/ollama-models`; GET `/run` never contacts Ollama). LiteLLM uses a text field. Checking the box enables LLM summaries for that run. Not saved to TOML.
Explicit `--ollama-model` / `--final-review-model` still win when set.

## UI preferences

Web-only preferences live in the same TOML under `[ui]` (still not SQLite):

```toml
[ui]
landing_page = "archive"       # archive | run | settings
preferred_view = "html"        # html | markdown | entries
onboarding_complete = false
```

## LinkedIn content searches (optional)

Each saved LinkedIn **content search** URL is ingested like a mailbox folder
(include/exclude, themes, grouping, rendering). Fetch is **opt-in network**
(same idea as `--ollama`): off unless `[linkedin].enabled = true` and/or you
pass `--linkedin`.

TOC layout (`[linkedin].layout`, default **`feed`**) controls section names:

| Layout | Digest section(s) |
|--------|-------------------|
| `feed` (default) | one `linkedin:feed` section |
| `per_source` | one section per author (`linkedin:<member-id>`) |
| `per_search` | one section per named search (`linkedin:<slug>`) |

v1 supports **faceted author lists** (`fromMember=…` on a content-search URL).
Rollup fetches each author’s recent posts via LinkedIn’s Voyager
`profileUpdatesV2` API using **your** logged-in session. Keyword-only content
search, company pages, follows, and mentions are not supported yet (see
[ROADMAP.md](ROADMAP.md)).

```toml
[linkedin]
enabled = true   # opt-in fetch on digest; default false
article_fetch = true   # fetch linked article bodies for link posts (default on)
fetch_ttl_hours = 24   # reuse listing/article cache within this window; 0 = always fetch
layout = "feed"        # feed (default) | per_source | per_search

[linkedin.searches.general]
url = "https://www.linkedin.com/search/results/content/?origin=FACETED_SEARCH&fromMember=%5B%22ACo…%22%5D"
display_name = "General"
enabled = true

[linkedin.searches.bbnj]
url = "https://www.linkedin.com/search/results/content/?origin=FACETED_SEARCH&fromMember=%5B%22ACo…%22%5D"
display_name = "BBNJ"
enabled = true
```

### Session cookies (environment only)

Step-by-step (Chrome / Firefox / Safari): [EXAMPLES.md](EXAMPLES.md#2-copy-session-cookies-li_at-and-jsessionid).

Copy both cookies from a logged-in browser (DevTools → Application/Storage →
Cookies → `https://www.linkedin.com`). **Never** put them in TOML, Settings, or
git.

| Cookie | Environment variable | Role |
|--------|----------------------|------|
| `li_at` | `ROLLUP_LINKEDIN_LI_AT` | Session |
| `JSESSIONID` | `ROLLUP_LINKEDIN_JSESSIONID` | Voyager CSRF (`ajax:…`; surrounding quotes optional) |

They rotate together. If a run returns 401, refresh **both** from the same pane.

**Persistent env file (recommended on your machine):** put the variables in
`~/.config/rollup/env` (mode `600`). Rollup loads this file at startup and does
not override variables already set in the shell. Override the path with
`ROLLUP_ENV_FILE` if needed. Still never commit this file or put secrets in TOML.
Optional Reddit OAuth variables belong in the same file.

```bash
# ~/.config/rollup/env
ROLLUP_LINKEDIN_LI_AT=…
ROLLUP_LINKEDIN_JSESSIONID=ajax:…
```

One-off shell exports still work:

```bash
export ROLLUP_LINKEDIN_LI_AT='…'
export ROLLUP_LINKEDIN_JSESSIONID='ajax:…'
rollup digest --linkedin --folder linkedin:general
```

For launchd/cron, pass the same variables in the job environment (plist
`EnvironmentVariables`, or a wrapper script). Do not write them into
`config.toml`.

### Behaviour

- Enable: `[linkedin].enabled = true` and/or `rollup digest --linkedin`. `--no-linkedin` turns fetch off for that run.
- Listing cache: `fetch_ttl_hours` (default `24`) skips Voyager when the last listing snapshot is still fresh. `0` always fetches (but still persists). `--linkedin-refresh` bypasses the cache for one run. On fetch failure, a stale snapshot is reused when available (`linkedin_cache_stale`).
- Article fetch: `[linkedin].article_fetch = true` by default — link posts also fetch the URL from Voyager `ArticleComponent` (external blogs, Pulse). Adds HTTP beyond Voyager; disable with `[linkedin].article_fetch = false` or `--no-linkedin-article-fetch`. Failures leave the commentary teaser and add a parse warning (`linkedin_article_fetch_failed`, `linkedin_article_empty`, …); they do not fail the digest.
- URL must be `https://www.linkedin.com/search/results/content/…` with a `fromMember` facet (author `ACo…` ids). Copy it from LinkedIn after filtering Content search by people.
- `--folder` / `--exclude-folder` accept the layout’s folder names (`linkedin:feed` by default; `linkedin:<slug>` when `layout = "per_search"`).
- Posts are dated from LinkedIn activity ids; the digest **lookback window** still applies after ingest (older posts are skipped, not listed as undated).
- `linkedin:*` folders stay **standalone** (no `notification_stream` grouping). Subject prefers the article title when present; preview keeps the full body up to 2000 characters.
- Caps (per search): 20 authors, 2 pages × 10 posts each, 100 posts total, 2s backoff between requests. Article fetch: 50 URLs per run, 1s backoff.
- Message identity is `li:activity:…`; author source key is `li:member:…` (mute with `rollup sources disable li:member:…`).
- Fetch failure **partial** (exit 2) when mail still publishes; LinkedIn-only runs **hard-fail** (exit 1) when fetch cannot proceed.
- `--dry-run` and web **GET** routes never contact LinkedIn (dry-run warns if cookies are missing).

Configure named search URLs in the web **LinkedIn** page (`/linkedin`). Settings
only toggles fetch on/off. The LinkedIn page stores URLs and display names only.
How to copy cookies and run: [EXAMPLES.md](EXAMPLES.md#linkedin-content-searches-opt-in-network). Failures: [TROUBLESHOOTING.md](TROUBLESHOOTING.md#linkedin-fetch-failed-401--429--checkpoint).

`[linkedin].layout` is also editable on the LinkedIn page (`/linkedin`). `per_search` is the legacy one-section-per-saved-search mode.

## Reddit subreddits (optional)

Opt-in like LinkedIn: off unless `[reddit].enabled = true` and/or `--reddit`.

Fetch uses a **transport ladder** (first success wins): optional **OAuth JSON** (when env credentials are set) → **public JSON** → **www RSS** → **old.reddit RSS**. No Reddit account is required. Sorts `rising` and `controversial` map to `hot` on RSS/JSON listing paths. Unauthenticated feeds are often rate-limited (~1 request per minute per sub).

```toml
[reddit]
enabled = true
fetch_ttl_hours = 24   # reuse listing cache within this window; 0 = always fetch
layout = "feed"          # feed | per_source
sort = "hot"             # hot | new | top | rising | controversial
limit = 10
mode = "summary"         # summary | posts
# time_filter = "week"   # optional; for sort=top, otherwise derived from lookback

[reddit.subs.python]
enabled = true

[reddit.subs.machinelearning]
enabled = true
mode = "posts"
limit = 5
# display_name / emoji / accent / order work like [folders.*]
```

### Optional OAuth (environment only)

A Reddit **script app** is optional. When all four variables are set, Rollup tries `oauth.reddit.com` JSON first (then falls through the public ladder). Never put them in TOML or Settings. They belong in `~/.config/rollup/env` (or `ROLLUP_ENV_FILE`) next to LinkedIn cookies:

```bash
# ~/.config/rollup/env
ROLLUP_REDDIT_CLIENT_ID=…
ROLLUP_REDDIT_CLIENT_SECRET=…
ROLLUP_REDDIT_USERNAME=…
ROLLUP_REDDIT_PASSWORD=…
```

### Behaviour

- Enable: `[reddit].enabled = true` and/or `rollup digest --reddit`. `--no-reddit` turns fetch off for that run.
- Listing cache: `fetch_ttl_hours` (default `24`, max `168`) skips the network when the last listing snapshot is still fresh and large enough for the requested cap. `0` always fetches (but still persists). `--reddit-refresh` bypasses the cache for one run. On fetch failure, a stale snapshot is reused when available (`reddit_cache_stale`).
- **Reddit** page (`/reddit`): add subreddit names, checkbox which subs to include, per-sub overrides for mode/sort/cap, layout, and TTL. Selections saved in TOML.
- `layout = feed`: all posts in `reddit:feed`; summary-mode subs become `subreddit_digest` groups, per-post subs stay standalone.
- `layout = per_source`: one folder `reddit:<sub>` per enabled sub.
- Global defaults apply when per-sub keys are omitted.
- `time_filter` applies to `sort = top` (`day` / `week` / `month` / `year`). If omitted, Rollup derives it from `--lookback-days`.
- Caps: 10 posts/sub default (max 50), max 100 enabled subs, **70s backoff** between subs (~1 request/minute). CLI logs and the Reddit / Run Studio pages show the estimated wait (cached subs are not counted). HTTP 429 adds extra wait (retry remaining subs once after a longer backoff).
- Message identity `reddit:t3:<id>`; source key `reddit:sub:<name>` (mute with `rollup sources disable reddit:sub:…`).
- Fetch failure **partial** (exit 2) when mail still publishes; Reddit-only runs **hard-fail** (exit 1).
- `--dry-run` and web **GET** never contact Reddit.

## Webpage articles (optional)

HTTPS article URLs live in SQLite (`webpage_queue`), not TOML. Add URLs from the web **Articles** page (`/articles`) or the Firefox **Add to Rollup** add-on (`POST /articles/capture`). The next digest fetches pending rows once into folder `webpage:queue` and stores the extracted body. Later runs **reuse that cache** (no refetch) and include an article when it was **saved within the lookback window**, the same date rule as mbox messages. `date_parsed` is the save time (`created_at`). Failed fetches are retryable from the GUI. Pass `--no-webpage` to skip. Caps: 50 fetches per run, 1s backoff, 2MB per page. Message identity is `web:url:<sha256(canonical_url)>`; source key is `web:host:<netloc>`.

## Google Scholar alerts (optional detailed mode)

Scholar alert emails already live in Thunderbird. Rollup detects them from
`scholaralerts-noreply@google.com` / List-ID `scholar-alerts.google.com` (or a
`Scholar Alert:` subject). This is **mail enrichment**, not a new ingest
transport — there is no Scholar API and no extra mailbox.

```toml
[scholar]
mode = "default"          # default | detailed
max_papers_per_email = 8
max_fetches_per_run = 40
```

| Mode | Behaviour | Network |
|------|-----------|---------|
| `default` (ship default) | Treat as other mail. Auto-force newsletter type `item_list` unless the source has a type override, so `preserve` keeps every paper line. | None |
| `detailed` | Replace the alert card with one card per paper. Fetch each landing-page abstract (cached in `scholar_paper_bodies`). LLM summarises each paper when `--ollama` is on; otherwise the abstract excerpt is the preview. | HTTPS at digest time |

CLI `--scholar-mode default|detailed` wins over TOML. Settings (`/settings`) has the same controls. Dry-run never fetches.

`detailed` unwraps `scholar.google.com/scholar_url?url=…`, rewrites `arxiv.org/pdf/…` to `/abs/…`, and skips raw PDFs (`scholar_pdf_skipped` — email title/authors/snippet only). Fetch failures fall back to the email snippet (`scholar_fetch_failed`) and do not fail the digest. Papers stay in the Thunderbird folder section, standalone, with `message_key` `scholar:paper:<sha256(url)>` and the parent Scholar `source_key`. They are **not** added to the Articles queue.

## Run profiles vs effort

| Lever | Controls |
|-------|----------|
| `--profile` / `[profiles.*]` | Digest habits: lookback, folders, grouping, optional sticky `effort` / `ollama` |
| `--effort` | LLM model ladder only (`light` / `balanced` / `high`) |

Built-in profiles:

| Name | lookback_days | grouping |
|------|---------------|----------|
| `weekly` (default) | 7 | on |
| `daily` | 1 | on |

Custom profiles:

```toml
[profiles.sports]
lookback_days = 3
folder = ["hoops"]
effort = "light"
```

```bash
rollup digest --profile sports
rollup digest --list-profiles
```

## Path discovery

If `root` / `mail_root` are not set in config or CLI:

1. Use `~/email/gmail/Newsletters.sbd` when that directory exists (back-compat).
2. Otherwise scan macOS Thunderbird profiles for a single `Newsletters.sbd`.
3. If zero or multiple candidates are found, set paths explicitly (doctor explains how).

## Related

- Product contract: [CONTRACT.md](CONTRACT.md)
- Comparison: [COMPARISON.md](COMPARISON.md)
- Roadmap: [ROADMAP.md](ROADMAP.md)
- Web UI (Settings + Run Studio + Articles + LinkedIn + Reddit): [WEB.md](WEB.md)
- Docker (default deploy): [DOCKER.md](DOCKER.md)
- Source policy (per-newsletter overrides): [SOURCES.md](SOURCES.md)
- Examples: [EXAMPLES.md](EXAMPLES.md)
- Troubleshooting (LinkedIn session, Reddit ladder, listing cache): [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
