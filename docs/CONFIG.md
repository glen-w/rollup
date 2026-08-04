# Rollup configuration

Optional TOML config, run profiles, folder themes, and path discovery.
CLI flags always win over files.

## Precedence

1. Built-in defaults (`weekly` profile, `balanced` effort, deterministic folder accents)
2. `~/.config/rollup/config.toml`
3. `./rollup.toml` (current working directory)
4. Selected `--profile` (builtin or `[profiles.*]` from TOML)
5. Explicit CLI flags

Pass `--config PATH` to load a single file instead of the search paths.

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
`--output-dir` or sticky `output_dir` in TOML.

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

[folders.hoops]
emoji = "🏀"
accent = "#e8923a"
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
- Source policy (per-newsletter overrides): [SOURCES.md](SOURCES.md)
- Examples: [EXAMPLES.md](EXAMPLES.md)
