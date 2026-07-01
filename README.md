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

## Prompt templates

Ollama prompts live under `prompts/`. Each run prepends `_common.txt` (forbids reproducing full newsletter text) plus a type-specific template (`short_update`, `multi_section_digest`, `essay`, `link_roundup`, `unclassified`).

Summary cache entries are stored in SQLite during summarisation (before digest files are written). Use `--rebuild-summaries` to bypass the cache.

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
