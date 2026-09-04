# Rollup — agent notes

Local-first personal briefing engine over a read-only Thunderbird mbox store (Python 3.10+). CLI entrypoint: `rollup` (or `python -m rollup`). Optional Flask web UI via the `[web]` extra. Product shape: [docs/CONTRACT.md](docs/CONTRACT.md). Position: [docs/COMPARISON.md](docs/COMPARISON.md). Roadmap: [docs/ROADMAP.md](docs/ROADMAP.md). Rollup produces a bounded briefing for a time window; it is not an RSS reader (no unread stream). Network sources are ingest transports, same pattern as LinkedIn/Reddit today.

## Cursor Cloud specific instructions

### Dependencies

```bash
uv sync --extra dev --extra web
source .venv/bin/activate
```

Equivalent without uv: `python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev,web]"`.

Cloud VMs do **not** have a Thunderbird mail store. Use the synthetic fixtures under `tests/fixtures/Newsletters.sbd` for digests, doctor, inventory, and web UI demos.

### Seed fixture digest + web UI

```bash
mkdir -p /tmp/rollup-cloud-demo/{state,output,logs}
rollup digest \
  --root tests/fixtures/Newsletters.sbd \
  --mail-root tests/fixtures \
  --state-dir /tmp/rollup-cloud-demo/state \
  --output-dir /tmp/rollup-cloud-demo/output \
  --log-dir /tmp/rollup-cloud-demo/logs \
  --lookback-days 3650 \
  --no-ollama \
  --no-linkedin \
  --no-reddit \
  --no-webpage
rollup web \
  --host 127.0.0.1 \
  --port 8765 \
  --state-dir /tmp/rollup-cloud-demo/state \
  --output-dir /tmp/rollup-cloud-demo/output \
  --mail-root tests/fixtures \
  --log-dir /tmp/rollup-cloud-demo/logs
```

Web binds **loopback only**. Default digest makes **no network calls** (preview summaries). Do not pass `--ollama` unless a local Ollama server is available. Do not pass `--linkedin` (cloud VMs have no LinkedIn session).

### Verify

```bash
rollup --version
rollup doctor --root tests/fixtures/Newsletters.sbd --mail-root tests/fixtures \
  --state-dir /tmp/rollup-cloud-demo/state --output-dir /tmp/rollup-cloud-demo/output \
  --log-dir /tmp/rollup-cloud-demo/logs
curl -fsS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/rollups
python -m pytest tests/ -q
```

### Notes

- Never write under the mail root; all state/output/logs stay outside it.
- Ollama is optional and local-loopback only by default.
- Sticky TOML ↔ CLI flags: `rollup.sticky_flags` (single registry). Config load/save for web: `rollup.config_service`. Parser construction: `rollup.cli_parser` (re-exported as `rollup.cli.build_parser`). Digest orchestration: `pipeline.run_digest` (phase helpers; public API unchanged).
- Docs: [README.md](README.md), [docs/CONFIG.md](docs/CONFIG.md), [docs/WEB.md](docs/WEB.md), [docs/DOCKER.md](docs/DOCKER.md), [docs/EXAMPLES.md](docs/EXAMPLES.md), [docs/CONTRACT.md](docs/CONTRACT.md), [docs/COMPARISON.md](docs/COMPARISON.md), [docs/ROADMAP.md](docs/ROADMAP.md), [docs/design/firefox-capture.md](docs/design/firefox-capture.md). Hosted view: `pip install -e '.[docs]'` then `make docs` (Sphinx HTML) or `make pages-site` (`website/` + `/guide/` in `_site/`). Markdown under `docs/` remains the corpus.

### Docker (default user deploy)

Preferred end-user path (Compose mounts host config/state/output/mail). Agents on Cloud VMs without a real mail store should prefer the fixture digest + `rollup web` flow above, or the fixture Compose smoke test:

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up -d --build
curl -fsS http://127.0.0.1:8765/rollups
```

Fixture smoke test without real mail:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

See [docs/DOCKER.md](docs/DOCKER.md). Host Python / venv remains the advanced/dev path (`uv sync` above).
