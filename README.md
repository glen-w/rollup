# Rollup

Local, read-only Thunderbird mbox newsletter digest for macOS.

Rollup reads newsletters from your Thunderbird/Gmail mbox store, classifies them, and produces weekly Markdown and HTML digests — without modifying any mail files.

## Safety guarantee

Rollup is **strictly read-only** with respect to your Thunderbird mail store. It never modifies, deletes, renames, or writes anything under your mail root (default: `/Users/89298/email/gmail`).

All output, state, and logs are written outside the mail store.

> Avoid running while Thunderbird is compacting or actively syncing large folders. The script is read-only, but mbox may be temporarily inconsistent.

## Requirements

- Python 3.10+
- Thunderbird mbox format (not Maildir)

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

For Ollama summarisation (optional):

```bash
pip install -e ".[ollama]"
```

## Network policy

**MVP performs no network calls.** `--no-ollama` defaults to `True`.

The Ollama phase performs only local loopback calls unless `--allow-remote-ollama` is explicitly passed.

## Quick start (synthetic fixtures)

```bash
python -m rollup inventory --root tests/fixtures/Newsletters.sbd
python -m rollup digest --root tests/fixtures/Newsletters.sbd --no-ollama
python -m rollup digest --root tests/fixtures/Newsletters.sbd --dry-run
python -m rollup digest --root tests/fixtures/Newsletters.sbd --no-ollama --folder tech
```

## Commands

### Inventory

Discover mbox folders and message counts (no body parsing):

```bash
python -m rollup inventory
python -m rollup inventory --root tests/fixtures/Newsletters.sbd
python -m rollup inventory --json-out ./output/inventory.json
```

### Digest

Generate weekly digest (Markdown + HTML in `./output/`):

```bash
python -m rollup digest --no-ollama
python -m rollup digest --lookback-days 7
python -m rollup digest --dry-run
python -m rollup digest --folder tech --exclude-folder hoops
python -m rollup digest --include-seen-undated
```

With Ollama (requires `.[ollama]` extra, local Ollama running, and explicit `--ollama`):

```bash
python -m rollup digest --ollama
python -m rollup digest --ollama --rebuild-summaries
python -m rollup digest --ollama --summary-profile deep
python -m rollup digest --ollama --summary-type-routing
python -m rollup digest --ollama --summary-variants rough,deep
```

## Live-run checklist

1. **Before copying real mail**, confirm `.gitignore` contains `fixtures/`.
2. **Never commit** files copied from `/Users/89298/email/gmail`.
3. Bootstrap Python env (see Setup above).
4. Run against committed synthetic fixtures first:
   ```bash
   python -m rollup inventory --root tests/fixtures/Newsletters.sbd
   python -m rollup digest --root tests/fixtures/Newsletters.sbd --no-ollama
   ```
5. Optional local real-mail copy (gitignored):
   ```bash
   cp -R /Users/89298/email/gmail/Newsletters.sbd ./fixtures/Newsletters.sbd
   ```
6. Small live tests before a full run:
   ```bash
   python -m rollup inventory --root /Users/89298/email/gmail/Newsletters.sbd
   python -m rollup digest --no-ollama --folder hoops
   python -m rollup digest --no-ollama --folder tech
   ```
7. Full live digest without Ollama:
   ```bash
   python -m rollup digest --no-ollama
   ```
8. Enable Ollama (local loopback only by default; explicit opt-in):
   ```bash
   pip install -e ".[ollama]"
   python -m rollup digest --ollama
   ```

## Configuration

All settings via CLI flags and defaults. No `.env` file required for v1.

| Flag | Default |
|------|---------|
| `--root` | `/Users/89298/email/gmail/Newsletters.sbd` |
| `--mail-root` | `/Users/89298/email/gmail` |
| `--output-dir` | `./output` |
| `--state-dir` | `./state` (SQLite: `rollup.db`) |
| `--lookback-days` | `7` |
| `--no-ollama` | `True` (MVP default; no network) |
| `--ollama` | Explicit opt-in to enable local Ollama summarisation |
| `--dry-run` | No output files, state DB, logs, or Ollama calls |

## Summary profiles

Rollup now includes built-in summary profiles:

- `rough` -> `llama3.2:3b`
- `standard` -> `qwen2.5:7b`
- `deep` -> `gpt-oss:20b`
- `max` -> `qwen3.6:27b`

These are defaults, not hard requirements. Rollup does not validate local model installation at config-load time. If a model is missing at runtime, Rollup falls back gracefully and reports the issue. Pull models explicitly:

```bash
ollama pull llama3.2:3b
ollama pull qwen2.5:7b
ollama pull gpt-oss:20b
ollama pull qwen3.6:27b
```

List built-in or configured profiles:

```bash
python -m rollup digest --list-summary-profiles
python -m rollup digest --list-newsletter-types
```

## Summary routing modes

Single profile for the whole digest:

```bash
python -m rollup digest --ollama --summary-profile deep
```

Per-type routing for one mixed-model digest:

```bash
python -m rollup digest --ollama --summary-type-routing
```

Whole-digest comparison variants from one parsed/classified/filtered input set:

```bash
python -m rollup digest --ollama --summary-variants rough,deep
```

Variant mode writes one output set per profile by inserting `.{profile}` before the extension, for example:

- `2026-07-02-newsletter-digest.rough.md`
- `2026-07-02-newsletter-digest.rough.html`
- `2026-07-02-newsletter-digest.deep.md`
- `2026-07-02-newsletter-digest.deep.html`

## Summary profile sets

Profile sets can be loaded from or exported to JSON:

```bash
python -m rollup digest --export-summary-profile-set ./output/summary_profiles.json
python -m rollup digest --ollama --summary-profile-set ./output/summary_profiles.json
```

The serialized profile set includes:

- built-in or user-defined profiles
- default and fallback profile names
- per-type routes keyed by canonical classifier labels
- `schema_version` for future migrations

Canonical newsletter classifier labels are:

- `short_update`
- `multi_section_digest`
- `essay`
- `link_roundup`
- `unclassified`

## Prompt templates

Ollama prompts live under `prompts/`. Each run prepends `_common.txt` (forbids reproducing full newsletter text) plus a type-specific template (`short_update`, `multi_section_digest`, `essay`, `link_roundup`, `unclassified`).

Summary cache entries are stored in SQLite during summarisation (before digest files are written). Use `--rebuild-summaries` to bypass the cache.

Existing `rollup.db` files remain compatible: the legacy `summaries` table remains readable, and richer summary generations are stored in `summary_generations`. New databases record schema version 3 during non-dry-run initialization.

Newer summary generations are stored with richer cache identity so cached outputs are isolated by provider, profile, model, prompt style, prompt version, temperature, context, and generation options. Legacy cache rows remain readable when applicable.

## Ollama validation (live)

Prerequisites:

```bash
pip install -e ".[ollama]"
ollama pull llama3.2:3b
```

Incremental checks (default digest performs **no network calls** unless `--ollama` is passed):

```bash
# Single-folder smoke
python -m rollup digest --ollama --folder tech --lookback-days 7

# Re-run — expect cache hits in stats
python -m rollup digest --ollama --folder tech --lookback-days 7

# Force rebuild
python -m rollup digest --ollama --rebuild-summaries --folder tech --lookback-days 7

# Stop Ollama, then confirm preview fallback (no crash)
python -m rollup digest --ollama --folder tech --lookback-days 7

# Full digest after smoke passes
python -m rollup digest --ollama
```

## Summary routing report

Use `--summary-routing-report` to print a compact routing/model usage summary after a run.

Rendered digests also include compact summary metadata showing:

- routing mode or active variant
- profiles used
- models used
- summary source counts
- optional compact type/profile/model counts

## Benchmark local models

Use the stdlib-only benchmark helper to compare local Ollama-compatible models on the same prompts:

```bash
python scripts/benchmark_ollama_models.py \
  --models llama3.2:3b,qwen2.5:7b,gpt-oss:20b,qwen3.6:27b \
  --runs 2 \
  --num-ctx 16384 \
  --out benchmarks/ollama_benchmark.json \
  --markdown-out benchmarks/ollama_benchmark.md
```

## Project layout

```
tests/fixtures/Newsletters.sbd/   # committed synthetic test data
fixtures/                         # gitignored — local real-mail copies
output/                           # generated digests
state/                            # seen_messages SQLite
```

## Tests

```bash
python -m pytest tests/ -v
```

Regenerate synthetic fixtures:

```bash
python tests/generate_fixtures.py
```

## GitHub

`gh` is not required. After local development:

```bash
git init   # already done if cloned
git remote add origin git@github.com:YOU/rollup.git
git push -u origin main
```

Create the empty GitHub repository manually first.
