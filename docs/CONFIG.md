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

## Effort model overrides

Built-in `light` / `balanced` / `high` ladders keep the same profile names and
type routes. Override **models** per effort in TOML (or the Configuration Centre):

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
- Roadmap: [ROADMAP.md](ROADMAP.md)
- Web UI (Settings + Run Studio): [WEB.md](WEB.md)
- Source policy (per-newsletter overrides): [SOURCES.md](SOURCES.md)
- Examples: [EXAMPLES.md](EXAMPLES.md)
