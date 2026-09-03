<p align="center">
  <img src="assets/rollup_logo.png" alt="Rollup logo" width="120">
</p>

# Rollup

Local-first personal briefing engine. Turn the sources you deliberately follow
into a calm, high-quality periodic digest — without surrendering the underlying
data or your choice of inference.

Rollup reads newsletters from your existing Thunderbird mbox store (and optional
LinkedIn, Reddit, and webpage sources), classifies them, and writes **the
rollup** — Markdown, HTML, and optional EPUB / e-ink artifacts — without
modifying any mail.

Thunderbird owns the ongoing stream. Rollup takes a time window and produces a
**bounded reading object**. It is not an RSS reader: there is no unread count,
no scrolling feed, and no “mark all read.” Optional network sources are ingest
transports for that window, the same way LinkedIn and Reddit already are.

You do not need a special newsletter address, a second mailbox, or to make
Rollup authoritative for subscriptions.

## Quick start (digest + web UI)

Install the optional web extra once (`pip install -e ".[web]"` or `pip install 'rollup[web]'`), then:

```bash
# weekly digest (default profile; indexes into state for the UI)
rollup digest

# optional: with Ollama
rollup digest --ollama

# browse the archive (loopback only)
rollup web --open
```

Optional sticky settings: `~/.config/rollup/config.toml` or `./rollup.toml` — see [docs/CONFIG.md](docs/CONFIG.md).

## Documentation

| Doc | Purpose |
|-----|---------|
| [docs/EXAMPLES.md](docs/EXAMPLES.md) | Runnable commands (inventory, digest, web, cron, LinkedIn, Reddit, Docker) |
| [docs/CONFIG.md](docs/CONFIG.md) | TOML sticky config, profiles, paths, LinkedIn / Reddit / webpage, effort ladders |
| [docs/WEB.md](docs/WEB.md) | Local web UI (Archive, Settings, Run Studio, Articles, LinkedIn, Reddit, Admin) |
| [extension/firefox/README.md](extension/firefox/README.md) | Firefox Add to Rollup (temporary add-on) |
| [docs/CONTRACT.md](docs/CONTRACT.md) | Product contract and publication integrity |
| [docs/COMPARISON.md](docs/COMPARISON.md) | Where Rollup sits relative to other readers and AI digests |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Product sequence toward 1.0 and non-goals |
| [docs/DOCKER.md](docs/DOCKER.md) | Docker Compose setup sharing host config/state/output |
| [docs/CRON.md](docs/CRON.md) | launchd / crontab scheduling |
| [docs/SOURCES.md](docs/SOURCES.md) | Source registry and muting |
| [docs/OUTPUT_WRITERS.md](docs/OUTPUT_WRITERS.md) | `--output` writers (txt, json, epub, xteink) |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Doctor, LinkedIn session, exit codes |
| [docs/design/firefox-capture.md](docs/design/firefox-capture.md) | Firefox capture: packages, SLOC, known issues, review checklist |
| [CHANGELOG.md](CHANGELOG.md) | Release notes |

Quick picks: [EXAMPLES](docs/EXAMPLES.md) · [CONFIG](docs/CONFIG.md) · [WEB](docs/WEB.md) · [CONTRACT](docs/CONTRACT.md)

## Safety guarantee

Rollup is **strictly read-only** with respect to your Thunderbird mail store. It never modifies, deletes, renames, or writes anything under your mail root (default: `Path.home() / "email" / "gmail"`).

All output, state, and logs are written outside the mail store.

> Avoid running while Thunderbird is compacting or actively syncing large folders. The script is read-only, but mbox may be temporarily inconsistent.

## Requirements

- Python 3.10+
- Thunderbird mbox format (not Maildir)
- **No Ollama server required** for the default digest workflow

## Install

| Method | Command |
|--------|---------|
| PyPI (when published) | `pip install rollup` |
| Git checkout | `pip install .` |
| Editable dev checkout | `pip install -e ".[dev]"` |
| Web UI (Flask) | `pip install 'rollup[web]'` or `pip install -e '.[web]'` |
| LiteLLM providers | `pip install 'rollup[llm]'` |
| EPUB writer | `pip install 'rollup[epub]'` |
| Dev + web + tests | `pip install -e ".[dev,web]"` |
| uv (from checkout) | `uv sync --extra dev --extra web` |
| Docker (web + digest) | [docs/DOCKER.md](docs/DOCKER.md) |

From a git checkout, activate the venv then run `rollup` (or `python -m rollup`).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web]"
```

The default `rollup digest` uses preview excerpts only and makes **no network
calls** unless you opt in with `--linkedin`, `--reddit`, webpage queue fetches,
or `--ollama`.

## Network policy

**Default digest performs no network calls.** LLM summarisation is off unless you pass `--ollama` (enablement flag; not Ollama-only).

| Source | Enable | Credentials |
|--------|--------|-------------|
| LinkedIn `fromMember` | `--linkedin` or `[linkedin].enabled` | `ROLLUP_LINKEDIN_*` in `~/.config/rollup/env` (never TOML) |
| Reddit listings | `--reddit` or `[reddit].enabled` | None by default. Optional OAuth: `ROLLUP_REDDIT_*` in the same env file |
| Webpage articles | default on; `--no-webpage` to skip | None (HTTPS fetch at digest time) |

`--no-ollama` and `--dry-run` suppress message summaries, group summaries, and availability probes. `--dry-run` also skips final review. `--final-review` is not gated on `--ollama`. Summary-related flags are ignored on default runs; Rollup prints a warning if you pass them without `--ollama`.

`--final-review` is independent of `--ollama`: it can QA a preview-summary digest. See [docs/EXAMPLES.md](docs/EXAMPLES.md#final-review-editorial-qa).

## Commands

| Command | Purpose |
|---------|---------|
| `inventory` | Discover folders and counts |
| `digest` | Generate the weekly Markdown + HTML digest |
| `doctor` | Setup, safety, and environment diagnostics |
| `cron` | Print launchd/crontab snippets; show last-run status |
| `config` | Print effective merged TOML + profile + defaults (`config print`) |
| `sources` | Manage persistent newsletter source registry |
| `bodies` | Reader-body cache stats, check, backfill, prune |
| `web` | Local loopback UI (Archive, Settings, Run Studio, Articles, LinkedIn, Reddit, Admin) |

Runnable recipes: [docs/EXAMPLES.md](docs/EXAMPLES.md). Flag and TOML reference: [docs/CONFIG.md](docs/CONFIG.md). `rollup digest --help` is the complete flag list.

## Output writers

Default MD/HTML stay in core. Niche formats attach as **output writers** after
the digest report is built. **By default every discovered writer runs**; pass
`--output NAME` to select a subset, or `--output none` for Markdown/HTML only.

Built-in writers: **`xteink`**, **`txt`**, **`json`**, **`epub`** — see
[docs/OUTPUT_WRITERS.md](docs/OUTPUT_WRITERS.md).

## Modes

| Mode | Flags | Meaning |
|------|-------|---------|
| Manual | (default) | Writes dated Markdown + HTML; publishes `latest.*` only with `--latest` |
| Cron / unattended | `--cron` | Quieter, non-interactive run; publishes `latest.*` on success by default |
| Dry-run | `--dry-run` | Parse and report only; no output files, state, logs, or network |
| Preview-summary | default / `--no-ollama` | Uses short body excerpts for entries; not a dry-run |
| Ollama summaries | `--ollama` | Uses local Ollama (or LiteLLM) for entry summaries and type routing |
| Final-review-only | `--final-review` without `--ollama` | Whole-digest QA while entries remain preview summaries |
| Report | `--final-review-mode report` | Writes the final-review JSON sidecar; digest content unchanged |
| Apply | `--final-review-mode apply` | Applies validated summary-only fixes; cron requires `--final-review-allow-cron-apply` |

| Tier | Flags | What you get |
|------|-------|--------------|
| Basic | (default) | Preview summaries — fast, private, no AI server |
| Local AI | `--ollama` | Local Ollama summaries with type-routed profiles |
| QA | `--final-review` | Whole-digest editorial report; works with preview or Ollama summaries |

## Recommended personal setup

1. Install into a venv and confirm `rollup doctor` is clean.
2. Run a manual weekly non-AI digest (preview summaries, no network).
3. Optionally enable `--ollama` for local AI summaries.
4. Schedule with **launchd** on macOS (`rollup cron print-launchd`) — see [docs/CRON.md](docs/CRON.md).

Live-mail checklist and Ollama routing examples: [docs/EXAMPLES.md](docs/EXAMPLES.md).

## Architecture

Rollup is a local ingest → classify → digest pipeline. Mail is read-only. Optional network sources and LLM calls are opt-in. The web UI is a loopback Flask extra on the same TOML and SQLite.

```
CLI (rollup / python -m rollup)
  cli_parser + sticky_flags + user_config (TOML)
       │
       ├─ digest → pipeline.run_digest
       │     mbox discovery  +  linkedin/  +  reddit/  +  webpage/
       │     parse → classify → filter / grouping
       │     summarize (preview excerpts, or --ollama / LiteLLM)
       │     render MD/HTML → output writers → publication
       │     state/rollup.db (schema v15) + manifests + web index
       │
       └─ web → rollup.web (Flask; [web] extra)
             Archive · Run Studio · Settings · Articles · LinkedIn · Reddit
             Quality / Registry · Admin
```

CLI flags always win over TOML. Settings and Run Studio use the same sticky registry (`config_service` / `sticky_flags`). Network listings persist in SQLite and reuse within `fetch_ttl_hours`.

Every non-dry-run digest writes a privacy-safe JSON manifest under `state/manifests/`. Exit codes are `0` for success, `1` for hard failure, and `2` for a usable digest with material degradation — see [docs/CRON.md](docs/CRON.md#exit-codes).

## Project layout

```
src/rollup/                       # package source
src/rollup/cli.py                 # command handlers; re-exports build_parser
src/rollup/pipeline.py            # digest orchestration
src/rollup/web/                   # optional Flask UI ([web] extra)
extension/firefox/                # Add to Rollup temporary add-on
tests/fixtures/Newsletters.sbd/   # committed synthetic test data
docs/                             # CONFIG, WEB, CONTRACT, COMPARISON, ROADMAP, …
CHANGELOG.md                      # release notes
```

## Tests

```bash
python -m pytest tests/ -q
```

Regenerate synthetic fixtures: `python tests/generate_fixtures.py`.
