<p align="center">
  <img src="assets/rollup_logo.png" alt="Rollup logo" width="120">
</p>

# Rollup

Local, read-only Thunderbird mbox newsletter digest for macOS.

Rollup reads newsletters from your Thunderbird/Gmail mbox store, classifies them, and produces **the rollup** — weekly Markdown and HTML digests — without modifying any mail files.

**Contract:** Thunderbird filters file mail into folders; Rollup digests those folders. See [docs/CONTRACT.md](docs/CONTRACT.md).

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

See [docs/WEB.md](docs/WEB.md).

More runnable examples: [docs/EXAMPLES.md](docs/EXAMPLES.md) · product shape: [docs/CONTRACT.md](docs/CONTRACT.md) · roadmap: [docs/ROADMAP.md](docs/ROADMAP.md) · [CHANGELOG.md](CHANGELOG.md)

## Safety guarantee

Rollup is **strictly read-only** with respect to your Thunderbird mail store. It never modifies, deletes, renames, or writes anything under your mail root (default: `Path.home() / "email" / "gmail"`).

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

For the local browser UI (Flask), install the optional web extra: `pip install 'rollup[web]'` or `pip install -e '.[web]'` from a checkout. See [Quick start (digest + web UI)](#quick-start-digest--web-ui) and [docs/WEB.md](docs/WEB.md).
For rich EPUB digests (`--output epub`), install `pip install 'rollup[epub]'` (or `.[epub]` / `.[dev]` which includes ebooklib).
## Development setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

No Ollama server is required. The default `rollup digest` run uses preview excerpts only and makes **no network calls**.

Optional entry-level LLM summarisation requires `--ollama` on the CLI. By default that uses a local Ollama server; with `pip install 'rollup[llm]'` you can set `--llm-provider litellm` and a LiteLLM model string. Final review (`--final-review`) also requires `--ollama` (LLM enablement) and uses `--final-review-provider` independently. The `requests` library ships with Rollup for the Ollama path but is not loaded during default digest runs.

Optional web UI (requires Flask):

```bash
pip install -e ".[dev,web]"
rollup web --open
```

See [docs/WEB.md](docs/WEB.md).

## Network policy

**Default digest performs no network calls.** LLM summarisation is off unless you pass `--ollama` (enablement flag; not Ollama-only).

When `--ollama` is set, Rollup uses the configured provider (`--llm-provider`, default `ollama`):

- **ollama** — local Ollama HTTP API on loopback only, unless `--allow-remote-ollama` is passed
- **litellm** — optional `[llm]` extra; credentials come from the environment (never TOML/web). Newsletter text may leave the machine when using non-loopback endpoints

`--no-ollama` and `--dry-run` suppress every LLM call (message summaries, group summaries, final review, availability probes).

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

Optional: after `pip install -e ".[web]"`, browse the indexed digest:

```bash
rollup web --open
```

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

`--ollama` alone enables **type routing by default**. Each newsletter type is summarized with the profile/model mapped in the built-in profile set (for example, `essay` → `max` / `qwen3.6:27b`, `link_roundup` → `rough` / `llama3.2:3b`). Use `--summary-routing-report` to print which profiles and models were used.

## Commands

Rollup exposes:

| Command | Purpose |
|---------|---------|
| `inventory` | Discover folders and counts |
| `digest` | Generate the weekly Markdown + HTML digest |
| `doctor` | Setup, safety, and environment diagnostics |
| `cron` | Print launchd/crontab snippets; show last-run status |
| `sources` | Manage persistent newsletter source registry |
| `web` | Local loopback UI for archive, ratings, and source quality |

Common flags include `--root`, `--folder`, `--lookback-days`, `--dry-run`, `--cron`,
`--ollama`, `--grouping` / `--no-grouping`, and `--final-review`.

See [docs/EXAMPLES.md](docs/EXAMPLES.md), [docs/SOURCES.md](docs/SOURCES.md), [docs/WEB.md](docs/WEB.md), [docs/CRON.md](docs/CRON.md), [docs/OUTPUT_WRITERS.md](docs/OUTPUT_WRITERS.md), [docs/XTEINK_USAGE.md](docs/XTEINK_USAGE.md), and
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Output writers / addons

Default MD/HTML stay in core. Niche formats attach as **output writers** after
the digest report is built. **By default every discovered writer runs**; pass
`--output NAME` to select a subset, or `--output none` for Markdown/HTML only:

```bash
rollup digest --lookback-days 7                 # all writers
rollup digest --output xteink --lookback-days 7 # xteink only (+ md/html)
rollup digest --output none --lookback-days 7   # md/html only
rollup digest --output epub --lookback-days 7   # pip install 'rollup[epub]'
```

Built-in writers: **`xteink`**, **`txt`**, **`json`**, **`epub`** — see
[docs/OUTPUT_WRITERS.md](docs/OUTPUT_WRITERS.md).

Third-party packages can register writers via setuptools entry points:

```toml
[project.entry-points."rollup.output_writers"]
custom = "my_pkg.writers:CustomWriter"
```

Writers implement `name`, `register_cli`, `enabled`, and `write` (see
`rollup.output_writers`). Addon code should import public helpers from
`rollup.render` (`display_sender`, `format_date`, `format_read_time`,
`render_summary_html`, `folder_display_name`, `digest_output_stem`).

## XTEINK output

`--xteink` / `--output xteink` writes an extra e-ink-oriented Markdown digest beside the normal output:

- Short lines (~60 chars), no external URLs
- Filename uses a `.xteink` variant suffix, e.g. `2026-07-02T103000Z-newsletter-digest.xteink.md`
- For a rich offline ebook, use `--output epub` instead

```bash
rollup digest --xteink --lookback-days 7
rollup digest --output xteink --ollama --effort high --lookback-days 7
```

See [docs/XTEINK_USAGE.md](docs/XTEINK_USAGE.md).

## Recommended personal setup

1. Install into a venv and confirm `rollup doctor` is clean.
2. Run a manual weekly non-AI digest (preview summaries, no network).
3. Optionally enable `--ollama` for local AI summaries.
4. Schedule with **launchd** on macOS (`rollup cron print-launchd`) — see [docs/CRON.md](docs/CRON.md).

## Mode glossary

| Mode | Flags | Meaning |
|------|-------|---------|
| Manual | (default) | Writes dated Markdown + HTML; publishes `latest.*` only with `--latest` |
| Cron / unattended | `--cron` | Quieter, non-interactive run; publishes `latest.*` on success by default |
| Dry-run | `--dry-run` | Parse and report only; no output files, state, logs, or network |
| Preview-summary | default / `--no-ollama` | Uses short body excerpts for entries; not a dry-run |
| Ollama summaries | `--ollama` | Uses local Ollama for entry summaries and type routing |
| Final-review-only | `--final-review` without `--ollama` | Uses Ollama for whole-digest QA while entries remain preview summaries |
| Report | `--final-review-mode report` | Writes the final-review JSON sidecar and leaves digest content unchanged |
| Apply | `--final-review-mode apply` | Applies validated summary-only fixes; cron requires `--final-review-allow-cron-apply` |

**Preview summaries** (default) are short body excerpts — not the same as dry-run.

## Doctor

```bash
rollup doctor
rollup doctor --json
rollup doctor --full
```

## Run manifests

Every non-dry-run digest writes a privacy-safe JSON manifest under
`state/manifests/`. Successful runs that publish latest also update
`state/manifests/latest.json`.

Exit codes are `0` for success, `1` for hard failure, and `2` for a usable digest with material degradation. See [docs/CRON.md](docs/CRON.md#exit-codes) for the degradation rules.

## Grouping

By default Rollup groups high-frequency notification streams and daily editions
so the weekly digest stays readable. Essays stay standalone. Disable with
`--no-grouping`. Inspect decisions with `--grouping-report`.

## AI modes (simple)

| Tier | Flags | What you get |
|------|-------|--------------|
| Basic | (default) | Preview summaries — fast, private, no AI server |
| Local AI | `--ollama` | Local Ollama summaries with type-routed profiles |
| QA | `--final-review` | Whole-digest editorial report; can run with preview or Ollama summaries |

## What Rollup will never do

- Write, delete, or rename anything under your mail root
- Call cloud email APIs (no Gmail API)
- Require Ollama for default digests or CI tests
- Open a browser or prompt interactively in `--cron` mode
- Store message bodies, subjects, prompts, model responses, or patch text in run manifests

## Live-run checklist

1. **Before copying real mail**, confirm `.gitignore` contains `fixtures/`.
2. **Never commit** files copied from your live mail root, for example `~/email/gmail`.
3. Bootstrap Python env (see Development setup above).
4. Run `rollup doctor` against your paths.
5. Run against committed synthetic fixtures first:
   ```bash
   python -m rollup inventory --root tests/fixtures/Newsletters.sbd
   python -m rollup digest --root tests/fixtures/Newsletters.sbd
   ```
6. Optional local real-mail copy (gitignored):
   ```bash
   cp -R ~/email/gmail/Newsletters.sbd ./fixtures/Newsletters.sbd
   ```
7. Small live tests before a full run:
   ```bash
   python -m rollup inventory --root ~/email/gmail/Newsletters.sbd
   python -m rollup digest --folder hoops
   python -m rollup digest --folder tech
   ```
8. Full live digest without Ollama (default; preview summaries only):
   ```bash
   python -m rollup digest
   ```
9. Browse the indexed archive (requires `pip install 'rollup[web]'` or `.[web]`):
   ```bash
   rollup web --open
   ```
10. Enable Ollama (local loopback only by default; explicit opt-in):
   ```bash
   python -m rollup digest --list-summary-profiles
   python -m rollup digest --ollama --folder tech --lookback-days 7 --summary-routing-report
   python -m rollup digest --ollama --summary-routing-report
   ```

## Configuration

Settings come from built-in defaults, optional TOML (`~/.config/rollup/config.toml`, `./rollup.toml`, or `--config`), run `--profile`, then explicit CLI flags (CLI wins). No `.env` file is required. Full reference: [docs/CONFIG.md](docs/CONFIG.md).

| Flag | Default | Notes |
|------|---------|-------|
| `--config` | search paths | Load one TOML file instead of `~/.config/rollup/config.toml` + `./rollup.toml` |
| `--root` | discovered or `~/email/gmail/Newsletters.sbd` | Newsletter mbox folder |
| `--mail-root` | parent of `--root` / `~/email/gmail` | Safety boundary for writes |
| `--output-dir` | `~/Documents/rollup-outputs` | Digest Markdown + HTML (+ writers); prior batches → `archive/`; keep outside mail root / repo |
| `--state-dir` | `./state` | SQLite: `rollup.db` |
| `--log-dir` | `./logs` | Run logs (non-dry-run) |
| `--profile` | `weekly` | Run profile: `weekly` / `daily` / custom `[profiles.*]` |
| `--list-profiles` | off | List run profiles and exit |
| `--lookback-days` | from profile (`7` weekly) | Inclusive calendar-day window |
| *(digest mode)* | **no Ollama** | Omit both `--ollama` and `--no-ollama` |
| `--no-ollama` | implicit default | Preview summaries; no network |
| `--ollama` | off | Opt-in local Ollama summarisation |
| `--effort` | `balanced` | Machine-power preset: `light` / `balanced` / `high` (models + related defaults) |
| `--list-efforts` | off | List effort presets and exit |
| `--summary-profile` | — | **Ollama only:** one profile for all messages |
| `--summary-type-routing` | on when `--ollama` | **Ollama only:** per-type routing |
| `--no-summary-type-routing` | — | **Ollama only:** use `standard` for all |
| `--summary-variants` | — | **Ollama only:** one digest per profile |
| `--summary-profile-set` | built-in | Load profiles/routes from JSON (cannot combine with `--effort`) |
| `--summary-routing-report` | off | **Ollama only:** print routing stats |
| `--rebuild-summaries` | off | **Ollama only:** bypass summary cache |
| `--final-review` | off | Whole-digest editorial QA; writes JSON sidecar |
| `--final-review-mode` | `report` | `report` or `apply` (cron apply needs `--final-review-allow-cron-apply`) |
| `--final-review-allow-cron-apply` | off | Explicit opt-in for unattended apply |
| `--final-review-profile` | `strict` | `strict`, `concise`, or `editorial` |
| `--final-review-model` | from `--effort` | Override Ollama model for review |
| `--group-summaries` | off | Opt-in group LLM blurbs (requires `--ollama`) |
| `--final-review-report` | `<digest-stem>.final-review.json` | Explicit sidecar path |
| `--no-final-review-cache` | off | Bypass final review cache |
| `--dry-run` | off | Parse only; no writes or network |
| `--cron` | off | Unattended mode; quieter; publish latest on success |
| `--latest` | off | Publish `output/latest.md` / `latest.html` |
| `--no-grouping` | off | Disable notification/daily grouping |
| `--grouping-report` | off | Print grouping reason codes |
| `--xteink` | (see `--output`) | Write XTEINK e-ink Markdown (alias for `--output xteink`; `--x3` still works; replaces default-all) |
| `--output NAME` | **all writers** | Named writer addon (repeatable; built-ins: `xteink`, `txt`, `json`, `epub`). Pass `none` for Markdown/HTML only |

Final review does **not** require `--ollama`. It calls Ollama independently when enabled. Digest content is not mutated in report mode; a short QA summary appears in the collapsed run-details section. See [docs/EXAMPLES.md](docs/EXAMPLES.md#final-review-editorial-qa).

## Effort presets

`--effort` swaps the whole summary ladder plus companion defaults (`--ollama-model`, `--final-review-model`, `--max-chars-for-llm`) in one flag. Profile *names* and type routes stay the same; only models and budgets change.

| Effort | Rough | Standard | Deep | Max | Group / fallback | Final review | `max_chars_for_llm` |
|--------|-------|----------|------|-----|------------------|--------------|---------------------|
| `light` | `llama3.2:3b` | `llama3.2:3b` | `qwen2.5:7b` | `qwen2.5:7b` | `llama3.2:3b` | `qwen2.5:7b` | 20_000 |
| `balanced` (default) | `llama3.2:3b` | `qwen2.5:7b` | `gpt-oss:20b` | `qwen3.6:27b` | `llama3.2:3b` | `qwen2.5:7b` | 30_000 |
| `high` | `qwen2.5:7b` | `gpt-oss:20b` | `qwen3.6:27b` | `qwen3.6:27b` | `qwen2.5:7b` | `gpt-oss:20b` | 50_000 |

Explicit flags (`--ollama-model`, `--final-review-model`, `--max-chars-for-llm`) override the effort preset. Customise the models inside each effort via `[efforts.<name>]` in TOML or the Configuration Centre — profile names and type routes stay the same. Do not combine `--effort` with `--summary-profile-set` (custom JSON remains the escape hatch for nonstandard tags).

```bash
python -m rollup digest --list-efforts
python -m rollup digest --ollama --effort high
python -m rollup digest --ollama --effort balanced --single-model qwen2.5:7b
python -m rollup doctor --ollama --effort high --network
```

## Summary profiles

Rollup includes built-in summary profiles:

| Profile | Model | `num_predict` | `think` | Use case |
|---------|-------|---------------|---------|----------|
| `rough` | `llama3.2:3b` | 256 | `false` | Fast summaries for short updates and link roundups |
| `standard` | `qwen2.5:7b` | 512 | `false` | Default balanced profile |
| `deep` | `gpt-oss:20b` | 2048 | `low` | Higher-effort synthesis for analytical or policy-heavy items |
| `max` | `qwen3.6:27b` | 2048 | `false` | Highest-effort profile for long essays (default route) |

These are defaults, not hard requirements. Rollup does not validate local model installation at config-load time. If a model is missing at runtime, Rollup falls back gracefully and reports the issue in the stats block.

### Ollama generation settings (`think` and `num_predict`)

Each summary profile exposes two generation controls for Ollama summarisation:

| Field | Default | Sent to Ollama as | Purpose |
|-------|---------|-------------------|---------|
| `think` | `false` | Top-level `"think"` on `/api/generate` | Controls reasoning. Use `false`/`true` for Qwen3-family models. **GPT-OSS ignores booleans** and requires `"low"`, `"medium"`, or `"high"` — otherwise it may spend the whole `num_predict` budget on a reasoning trace and return an empty `response`, which Rollup treats as a failed summary. |
| `num_predict` | `2048` | `options.num_predict` | Maximum number of tokens the model may generate for one summary. Lower values are faster; higher values leave more room for long syntheses (and for GPT-OSS reasoning + visible output). |

**Defaults:** new profiles inherit `think: false` and `num_predict: 2048` unless you override them. The built-in tiered profiles above keep smaller `num_predict` values on `rough` / `standard` for speed. GPT-OSS profiles use `think: "low"` plus a larger `num_predict`.

**Configure in a profile set JSON** (preferred):

```json
{
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
      "options": {}
    }
  }
}
```

**Important:** set `think` and `num_predict` as **profile fields**, not inside `options`. Ollama expects `think` at the top level of the request body; placing it inside `options` is silently ignored and thinking stays enabled on Qwen3 models. Rollup validates profile sets and rejects `think` / `num_predict` nested under `options`.

**Legacy imports:** if an older exported profile set stored `num_predict` or `think` inside `options`, Rollup migrates those values to the profile fields on load.

**Cache behaviour:** summary cache keys include both `num_predict` (via `options_json`) and `think` (via an internal cache identity marker). Changing either field causes a cache miss for that profile — you do not need `--rebuild-summaries` unless you want to refresh everything.

**When to enable thinking:** leave `think: false` for Qwen3 digest summarisation. For GPT-OSS, use `think: "low"` (not `false`) so the model keeps a short reasoning trace and still emits a visible summary before hitting `num_predict`.

List configured values:

```bash
python -m rollup digest --list-summary-profiles
```

Example line:

```text
max: provider=ollama model=qwen3.6:27b prompt_style=deep temperature=0.2 num_predict=2048 think=False
```

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
| `essay` | `max` | `qwen3.6:27b` |
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

- built-in or user-defined profiles (`model`, `prompt_style`, `temperature`, `num_ctx`, `timeout_seconds`, **`num_predict`**, **`think`**, and any extra Ollama `options`)
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

Ollama prompt templates ship inside the `rollup` package (`rollup/prompts/`). Each run prepends `_common.txt` (forbids reproducing full newsletter text) plus a type-specific template (`short_update`, `multi_section_digest`, `essay`, `link_roundup`, `unclassified`). Final review prompts live in `rollup/prompts/final_review/`.

Summary cache entries are stored in SQLite during summarisation (before digest files are written). Use `--rebuild-summaries` to bypass the cache.

Existing `rollup.db` files remain compatible: the legacy `summaries` table remains readable, and richer summary generations are stored in `summary_generations`. Final review results are cached in `final_review_generations`. Group summaries are cached in `group_summary_by_key` (schema v6 also creates an unused forward-compatible `group_summary_generations` table). Source registry tables are schema **v7**; web archive/ratings tables are schema **v8**; reader plaintext bodies are schema **v9–v10** (`message_reader_bodies`). New databases record **schema version 10** during initialization.

Reader bodies are stored in `rollup.db` (including `-wal`/`-shm` when present). They are a convenience cache, not a mailbox archive. Sources export/import excludes bodies. Backups of `rollup.db` are sensitive.

Newer summary generations are stored with richer cache identity so cached outputs are isolated by provider, profile, model, prompt style, prompt version, temperature, context, generation options (including `num_predict`), and the profile's `think` setting. Legacy cache rows remain readable when applicable.

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
src/rollup/cli.py                 # command handlers; re-exports build_parser
src/rollup/cli_parser.py          # argparse construction
src/rollup/pipeline.py            # digest orchestration (run_digest + stages)
src/rollup/sticky_flags.py        # sticky TOML ↔ CLI flag registry
src/rollup/config_service.py      # load/validate/save TOML (web Settings / Run)
src/rollup/addons/                # in-tree output writer addons (e.g. xteink)
src/rollup/prompts/               # bundled Ollama + final-review prompt templates
src/rollup/web/                   # optional Flask UI ([web] extra)
tests/fixtures/Newsletters.sbd/   # committed synthetic test data
assets/                           # logo and favicon (also in package)
docs/EXAMPLES.md                  # runnable command recipes
docs/CONFIG.md                    # TOML, profiles, sticky ↔ CLI
docs/WEB.md                       # local web UI
docs/CONTRACT.md                  # product contract + publication integrity
docs/ROADMAP.md                   # near-term follow-ups and non-goals
docs/SOURCES.md                   # source registry
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
