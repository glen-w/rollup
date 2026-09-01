# Roadmap

Where Rollup is headed, relative to the [product contract](CONTRACT.md).
This is guidance for contributors, not a commitment calendar.

## Shipped (recent)

- Optional TOML sticky config, run profiles, effort presets, folder themes ([CONFIG.md](CONFIG.md))
- Output-writer plugin seam + builtins (xteink, txt, json, epub) ([OUTPUT_WRITERS.md](OUTPUT_WRITERS.md))
- Loopback web UI: Archive, Quality, Registry, Admin, reader bodies ([WEB.md](WEB.md))
- Configuration Centre (`/settings`) and Run Studio (`/run`) on the real TOML + CLI digest path
- Shared `sticky_flags` registry (sticky ↔ CLI argv/argparse); `cli_parser` extraction; `run_digest` phase helpers
- Optional LinkedIn `fromMember` folders (`[linkedin]` + `--linkedin`; Voyager `profileUpdatesV2`; session cookies from env; default-on article fetch for link posts)
- **Webpage articles** (`/articles` GUI + SQLite `webpage_queue`): add HTTPS URLs; digest fetches once into `webpage:queue`, caches the body, and includes pages saved within the lookback window; `--no-webpage` skips ingest
- **Reddit subreddits** (`/reddit` GUI + `[reddit]` TOML): public RSS fetch (no credentials); add sub names in GUI; per-sub or global sort/cap/mode; `summary` → `subreddit_digest` groups, `posts` → standalone items; shared `feed` / `per_source` layout with LinkedIn

## Near-term (product)

- **Richer LinkedIn sources beyond faceted `fromMember` search** — keyword SRP if LinkedIn exposes a working content-search query again; company/org posts; follows/mentions; Settings knobs that are not “paste a search URL”. v1 stays author-list URLs mapped through Voyager `profileUpdatesV2`.

## Near-term (engineering hygiene)

Incremental follow-ups from the post–0.6.3 refactor audit — **behavior-preserving** preferred:

1. ~~**Shared Thunderbird folder listing**~~ for Settings and Run Studio (`discovery.list_flat_mbox_names`)
2. ~~**One web helper**~~ to load the active `ConfigDocument` (`rollup.web.config.load_web_config_document`)
3. **`state.py` / `source_registry.py` splits** by concern (schema migrate vs cache vs registry APIs) when the next feature forces edits there
4. **`render.py` MD/HTML twin paths** — shared structure + thin formatters if a new output surface needs them
5. ~~Optional rename of `cron_helpers.build_digest_argv`~~ → `build_scheduled_digest_argv` (done; different jobs; do not merge with `config_service.build_digest_argv`)

## Post 1.0

- **Gmail integration** — optional Gmail API (OAuth) backend as an alternative to local mbox; read-only ingest aligned with the [product contract](CONTRACT.md); Thunderbird mbox remains the primary path through 1.0

## Product non-goals (still)

From [CONTRACT.md](CONTRACT.md) — not planned for 1.0 unless the contract changes:

- IMAP / Maildir backends (Gmail API → post-1.0 above)
- Thunderbird add-on (XPI)
- Multi-user or non-loopback web UI
- Exposing classifier thresholds as user knobs
- In-app digest scheduler (Run Studio stays a synchronous guided runner; use [CRON.md](CRON.md) / launchd)

## Open product questions (parked)

- Richer onboarding when mail paths are undiscoverable
- Optional remote Ollama remains explicit (`--allow-remote-ollama`); optional LiteLLM via `rollup[llm]` + `--llm-provider litellm` (API keys from env only)
- Deeper Admin failure history when manifests are missing (incomplete-history disclaimer stays)
- LinkedIn HTML/API fragility — isolated fetch module; may break when LinkedIn changes

## Related docs

| Doc | Role |
|-----|------|
| [CONTRACT.md](CONTRACT.md) | Product shape and publication integrity |
| [CONFIG.md](CONFIG.md) | TOML, profiles, sticky ↔ CLI |
| [WEB.md](WEB.md) | Loopback UI surfaces |
| [DOCKER.md](DOCKER.md) | Optional container setup |
| [EXAMPLES.md](EXAMPLES.md) | Runnable recipes |
| [CHANGELOG.md](../CHANGELOG.md) | What shipped |
