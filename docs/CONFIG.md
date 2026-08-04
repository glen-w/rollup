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
ollama = true
ollama_model = "llama3.2"   # optional; otherwise effort defaults apply
summary_profile = "standard"
effort = "balanced"
```

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
- Web UI (Settings + Run Studio): [WEB.md](WEB.md)
- Source policy (per-newsletter overrides): [SOURCES.md](SOURCES.md)
- Examples: [EXAMPLES.md](EXAMPLES.md)
