# Rollup example commands

Runnable examples for inventory, digest generation, summary routing, and local tooling.

Run from the project root with the virtualenv active:

```bash
cd /path/to/rollup
source .venv/bin/activate
```

See [README.md](../README.md) for setup, safety guarantees, and configuration defaults.

## Inventory

Discover mbox folders and message counts (read-only; no body parsing):

```bash
python -m rollup inventory
python -m rollup inventory --root tests/fixtures/Newsletters.sbd
python -m rollup inventory --json-out ./output/inventory.json
python -m rollup inventory --root /Users/89298/email/gmail/Newsletters.sbd
```

## Digest without Ollama

Preview and generate digests without network calls:

```bash
python -m rollup digest --no-ollama
python -m rollup digest --root tests/fixtures/Newsletters.sbd --no-ollama
python -m rollup digest --lookback-days 7 --no-ollama
python -m rollup digest --dry-run --root tests/fixtures/Newsletters.sbd
python -m rollup digest --folder tech --exclude-folder hoops --no-ollama
python -m rollup digest --include-seen-undated --no-ollama
```

## Digest with Ollama (recommended full run)

Requires `pip install -e ".[ollama]"` and a running local Ollama server.

**Recommended full run** (all folders, 7-day lookback, per-type model routing):

```bash
python -m rollup digest --ollama --summary-routing-report
```

`--ollama` alone enables type routing by default. Use `--summary-routing-report` to print which profiles and models were used.

### Inspect profiles and routes

```bash
python -m rollup digest --list-summary-profiles
python -m rollup digest --list-newsletter-types
```

### Routing modes

Single profile for the whole digest:

```bash
python -m rollup digest --ollama --summary-profile standard
python -m rollup digest --ollama --summary-profile deep
```

Explicit per-type routing (same as `--ollama` default):

```bash
python -m rollup digest --ollama --summary-type-routing --summary-routing-report
```

Disable per-type routing and use the `standard` profile for every message:

```bash
python -m rollup digest --ollama --no-summary-type-routing --summary-routing-report
```

Compare multiple profiles side by side (writes one output set per profile):

```bash
python -m rollup digest --ollama --summary-variants rough,standard,deep --summary-routing-report
```

Variant mode writes files such as:

- `output/2026-07-02-newsletter-digest.rough.md`
- `output/2026-07-02-newsletter-digest.deep.html`

### Smoke tests and cache control

```bash
python -m rollup digest --ollama --folder tech --lookback-days 7 --summary-routing-report
python -m rollup digest --ollama --rebuild-summaries --folder tech --lookback-days 7
python -m rollup digest --ollama --rebuild-summaries --summary-routing-report
```

Re-run without `--rebuild-summaries` to confirm cache hits in the stats block.

### Custom profile sets

Export built-in profiles, edit model names to match your local Ollama library, then run:

```bash
python -m rollup digest --export-summary-profile-set ./output/summary_profiles.json
python -m rollup digest --ollama --summary-profile-set ./output/summary_profiles.json --summary-routing-report
```

Example model pulls for the built-in profile set:

```bash
ollama pull llama3.2:3b
ollama pull qwen2.5:7b
ollama pull gpt-oss:20b
ollama pull qwen3.6:27b
```

## Live-run workflow

Incremental checks before a full live digest:

```bash
python -m rollup inventory --root tests/fixtures/Newsletters.sbd
python -m rollup digest --root tests/fixtures/Newsletters.sbd --no-ollama
python -m rollup inventory --root /Users/89298/email/gmail/Newsletters.sbd
python -m rollup digest --no-ollama --folder hoops
python -m rollup digest --no-ollama --folder tech
python -m rollup digest --no-ollama
python -m rollup digest --ollama --folder tech --lookback-days 7 --summary-routing-report
python -m rollup digest --ollama --summary-routing-report
```

Optional gitignored local mail copy:

```bash
cp -R /Users/89298/email/gmail/Newsletters.sbd ./fixtures/Newsletters.sbd
python -m rollup digest --root ./fixtures/Newsletters.sbd --no-ollama
```

## Ollama validation sequence

```bash
pip install -e ".[ollama]"
python -m rollup digest --list-summary-profiles
python -m rollup digest --list-newsletter-types
python -m rollup digest --ollama --folder tech --lookback-days 7 --summary-routing-report
python -m rollup digest --ollama --summary-profile standard --folder tech --lookback-days 7
python -m rollup digest --ollama --rebuild-summaries --folder tech --lookback-days 7
python -m rollup digest --ollama --summary-routing-report
```

Stop Ollama and re-run a smoke command to confirm preview fallback without crashing:

```bash
python -m rollup digest --ollama --folder tech --lookback-days 7
```

## Benchmark local models

Compare local Ollama-compatible models on fixed prompts:

```bash
python scripts/benchmark_ollama_models.py \
  --models llama3.2:3b,qwen2.5:7b,gpt-oss:20b,qwen3.6:27b \
  --runs 2 \
  --num-ctx 16384 \
  --out benchmarks/ollama_benchmark.json \
  --markdown-out benchmarks/ollama_benchmark.md
```

## Tests and fixtures

```bash
python -m pytest tests/ -v
python tests/generate_fixtures.py
```
