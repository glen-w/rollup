<p align="center">
  <img src="assets/rollup_logo.png" alt="Rollup logo" width="120">
</p>

# Rollup

Local, read-only Thunderbird mbox newsletter digest for macOS.

Rollup reads newsletters from your Thunderbird/Gmail mbox store, classifies them, and produces **the rollup** — weekly Markdown and HTML digests — without modifying any mail files.

## Safety guarantee

Rollup is **strictly read-only** with respect to your Thunderbird mail store. It never modifies, deletes, renames, or writes anything under your mail root (default: `/Users/89298/email/gmail`).

All output, state, and logs are written outside the mail store.

> Avoid running while Thunderbird is compacting or actively syncing large folders. The script is read-only, but mbox may be temporarily inconsistent.

## Requirements

- Python 3.10+
- Thunderbird mbox format (not Maildir)
- **No Ollama server required** for the default digest workflow

## Install

From a built wheel or PyPI (when published):

```bash
pip install rollup
```

From a git checkout:

```bash
pip install .
```

## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

No Ollama server is required. The default `rollup digest` run uses preview excerpts only and makes **no network calls**.

Optional LLM summarisation uses a local Ollama server and requires `--ollama` on the CLI. The `requests` library ships with Rollup for that path but is not loaded during default digest runs.

## Network policy

**Default digest performs no network calls.** Ollama is off unless you pass `--ollama`.

When `--ollama` is enabled, Rollup calls the local Ollama HTTP API on loopback only, unless `--allow-remote-ollama` is explicitly passed.

Summary-related flags (`--summary-profile`, `--rebuild-summaries`, and similar) are ignored on default runs; Rollup prints a warning if you pass them without `--ollama`.

## Quick start (synthetic fixtures)

Default digest skips Ollama (preview summaries only; no network):

```bash
python -m rollup inventory --root tests/fixtures/Newsletters.sbd
python -m rollup digest --root tests/fixtures/Newsletters.sbd
python -m rollup digest --root tests/fixtures/Newsletters.sbd --dry-run
python -m rollup digest --root tests/fixtures/Newsletters.sbd --folder tech
```

Explicit `--no-ollama` is equivalent to the default.

## Quick start (live mail with Ollama)

From the project root, with Ollama running locally:

```bash
source .venv/bin/activate
python -m rollup digest --list-summary-profiles
python -m rollup digest --ollama --summary-routing-report
```

**Recommended full run** (all folders, 7-day lookback, per-type model routing):

```bash
python -m rollup digest --ollama --summary-routing-report
```

`--ollama` alone enables **type routing by default**. Each newsletter type is summarized with the profile/model mapped in the built-in profile set (for example, `essay` → `deep` / `gpt-oss:20b`, `link_roundup` → `rough` / `llama3.2:3b`). Use `--summary-routing-report` to print which profiles and models were used.

More runnable examples: [docs/EXAMPLES.md](docs/EXAMPLES.md) · [CHANGELOG.md](CHANGELOG.md)

## Commands

Rollup exposes two subcommands: `inventory` (discover folders and counts) and `digest` (generate **the rollup** — Markdown + HTML output).

Common flags include `--root`, `--folder`, `--lookback-days`, `--dry-run`, `--no-ollama`, and the summary routing flags documented below. See [docs/EXAMPLES.md](docs/EXAMPLES.md) for copy-paste command recipes covering inventory, digest modes, Ollama routing, smoke tests, benchmarks, and fixture workflows.

## Live-run checklist

1. **Before copying real mail**, confirm `.gitignore` contains `fixtures/`.
2. **Never commit** files copied from `/Users/89298/email/gmail`.
3. Bootstrap Python env (see Development setup above).
4. Run against committed synthetic fixtures first:
   ```bash
   python -m rollup inventory --root tests/fixtures/Newsletters.sbd
   python -m rollup digest --root tests/fixtures/Newsletters.sbd
   ```
5. Optional local real-mail copy (gitignored):
   ```bash
   cp -R /Users/89298/email/gmail/Newsletters.sbd ./fixtures/Newsletters.sbd
   ```
6. Small live tests before a full run:
   ```bash
   python -m rollup inventory --root /Users/89298/email/gmail/Newsletters.sbd
   python -m rollup digest --folder hoops
   python -m rollup digest --folder tech
   ```
7. Full live digest without Ollama (default; preview summaries only):
   ```bash
   python -m rollup digest
   ```
8. Enable Ollama (local loopback only by default; explicit opt-in):
   ```bash
   python -m rollup digest --list-summary-profiles
   python -m rollup digest --ollama --folder tech --lookback-days 7 --summary-routing-report
   python -m rollup digest --ollama --summary-routing-report
   ```

## Configuration

All settings via CLI flags and defaults. No `.env` file required for v1.

| Flag | Default | Notes |
|------|---------|-------|
| `--root` | `/Users/89298/email/gmail/Newsletters.sbd` | Newsletter mbox folder |
| `--mail-root` | `/Users/89298/email/gmail` | Safety boundary for writes |
| `--output-dir` | `./output` | Digest Markdown + HTML |
| `--state-dir` | `./state` | SQLite: `rollup.db` |
| `--log-dir` | `./logs` | Run logs (non-dry-run) |
| `--lookback-days` | `7` | Inclusive calendar-day window |
| *(digest mode)* | **no Ollama** | Omit both `--ollama` and `--no-ollama` |
| `--no-ollama` | implicit default | Preview summaries; no network |
| `--ollama` | off | Opt-in local Ollama summarisation |
| `--summary-profile` | — | **Ollama only:** one profile for all messages |
| `--summary-type-routing` | on when `--ollama` | **Ollama only:** per-type routing |
| `--no-summary-type-routing` | — | **Ollama only:** use `standard` for all |
| `--summary-variants` | — | **Ollama only:** one digest per profile |
| `--summary-profile-set` | built-in | Load profiles/routes from JSON |
| `--summary-routing-report` | off | **Ollama only:** print routing stats |
| `--rebuild-summaries` | off | **Ollama only:** bypass summary cache |
| `--dry-run` | off | Parse only; no writes or network |

## Summary profiles

Rollup includes built-in summary profiles:

| Profile | Model | Use case |
|---------|-------|----------|
| `rough` | `llama3.2:3b` | Fast summaries for short updates and link roundups |
| `standard` | `qwen2.5:7b` | Default balanced profile |
| `deep` | `gpt-oss:20b` | Higher-effort synthesis for essays and analysis |
| `max` | `qwen3.6:27b` | Experimental high-effort profile for long reads |

These are defaults, not hard requirements. Rollup does not validate local model installation at config-load time. If a model is missing at runtime, Rollup falls back gracefully and reports the issue in the stats block.

Pull the built-in models explicitly:

```bash
ollama pull llama3.2:3b
ollama pull qwen2.5:7b
ollama pull gpt-oss:20b
ollama pull qwen3.6:27b
```

If your local Ollama library uses different model names, export the built-in profile set, edit the `model` fields, and pass `--summary-profile-set`. See [docs/EXAMPLES.md](docs/EXAMPLES.md#custom-profile-sets).

List built-in or configured profiles:

```bash
python -m rollup digest --list-summary-profiles
python -m rollup digest --list-newsletter-types
```

## Summary routing modes

When `--ollama` is enabled, routing precedence is:

1. `--summary-variants` — compare whole-digest outputs across profiles
2. `--summary-profile` — one profile for every message
3. `--summary-type-routing` (or `--ollama` alone) — per-type routing from the profile set
4. otherwise — `standard` profile

Default per-type routes in the built-in profile set:

| Newsletter type | Profile | Model |
|-----------------|---------|-------|
| `short_update` | `rough` | `llama3.2:3b` |
| `link_roundup` | `rough` | `llama3.2:3b` |
| `multi_section_digest` | `standard` | `qwen2.5:7b` |
| `essay` | `deep` | `gpt-oss:20b` |
| `unclassified` | `standard` | `qwen2.5:7b` |

See [docs/EXAMPLES.md](docs/EXAMPLES.md#digest-with-ollama-recommended-full-run) for runnable routing examples.

Variant mode writes one rollup set per profile by inserting `.{profile}` before the extension, for example:

- `2026-07-02-newsletter-digest.rough.md` (+ `rollup_logo.png`, `favicon.ico`)
- `2026-07-02-newsletter-digest.rough.html`
- `2026-07-02-newsletter-digest.deep.md`
- `2026-07-02-newsletter-digest.deep.html`

## Summary profile sets

Profile sets can be loaded from or exported to JSON. See [docs/EXAMPLES.md](docs/EXAMPLES.md#custom-profile-sets).

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

Ollama prompt templates ship inside the `rollup` package (`rollup/prompts/`). Each run prepends `_common.txt` (forbids reproducing full newsletter text) plus a type-specific template (`short_update`, `multi_section_digest`, `essay`, `link_roundup`, `unclassified`).

Summary cache entries are stored in SQLite during summarisation (before digest files are written). Use `--rebuild-summaries` to bypass the cache.

Existing `rollup.db` files remain compatible: the legacy `summaries` table remains readable, and richer summary generations are stored in `summary_generations`. New databases record schema version 3 during non-dry-run initialization.

Newer summary generations are stored with richer cache identity so cached outputs are isolated by provider, profile, model, prompt style, prompt version, temperature, context, and generation options. Legacy cache rows remain readable when applicable.

## Ollama validation (live)

Prerequisites and incremental smoke-test commands are in [docs/EXAMPLES.md](docs/EXAMPLES.md#ollama-validation-sequence).

## Summary routing report

Use `--summary-routing-report` to print a compact routing/model usage summary after a run.

Rendered digests also include compact summary metadata showing:

- routing mode or active variant
- profiles used
- models used
- summary source counts
- optional compact type/profile/model counts

## Benchmark local models

Use the stdlib-only benchmark helper documented in [docs/EXAMPLES.md](docs/EXAMPLES.md#benchmark-local-models).

## Project layout

```
src/rollup/                       # package source
src/rollup/prompts/               # bundled Ollama prompt templates
tests/fixtures/Newsletters.sbd/   # committed synthetic test data
assets/                           # logo and favicon (also in package)
docs/EXAMPLES.md                  # runnable command recipes
CHANGELOG.md                      # release notes
fixtures/                         # gitignored — local real-mail copies
output/                           # generated rollups (the rollup)
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
