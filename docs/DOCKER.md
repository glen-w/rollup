# Docker

Run Rollup web + digest subprocesses in one container while sharing the same config, state, output, and mail paths as the native CLI.

## Prerequisites

- Docker Desktop (or Docker Engine + Compose v2)
- Existing Rollup setup: `~/.config/rollup/config.toml`, optional `~/.config/rollup/env` for LinkedIn cookies
- File sharing enabled for `~/email`, `~/Documents`, `~/.config/rollup`, and this repo

## Quick start

```bash
cp docker-compose.override.yml.example docker-compose.override.yml
docker compose up -d --build
open http://localhost:8765
```

The override bind-mounts your existing paths:

| Host | Container | Purpose |
|------|-----------|---------|
| `~/.config/rollup/config.toml` | `/data/config/config.toml` | Sticky config (Settings / Reddit page writes here) |
| `~/.config/rollup/env` | `/data/config/env` | LinkedIn cookies (never commit) |
| `~/Documents/rollup-outputs` | `/data/output` | Digests |
| `./state` | `/data/state` | `rollup.db`, manifests, article queue |
| `./logs` | `/data/logs` | Run logs |
| `~/email/gmail` | `/data/mail` | mbox (read-only) |

Edit `docker-compose.override.yml` if your paths differ.

## Secrets

LinkedIn session cookies live in `~/.config/rollup/env` (mode `600`), loaded at startup via `ROLLUP_ENV_FILE`. The container sets `ROLLUP_ENV_FILE=/data/config/env` and mounts your host file read-only.

Do **not** put cookies in TOML, `.env`, or git. See [CONFIG.md](CONFIG.md) and [CRON.md](CRON.md).

## CLI flags vs TOML paths

Compose passes `rollup --config /data/config/config.toml web …` with explicit path flags pointing at `/data/*`. CLI flags win over TOML, so `output_dir = "~/Documents/rollup-outputs"` in config still works on the host while Docker uses the mounted paths.

Non-path settings (folder themes, LinkedIn searches, Reddit subs, efforts) load from the mounted `config.toml`.

## Network

| Source | Credentials | Egress needed |
|--------|-------------|---------------|
| Mail mbox | — | No |
| LinkedIn | `~/.config/rollup/env` | Yes (when enabled) |
| Reddit RSS | None | Yes (when `[reddit].enabled`) |
| Webpage articles | None | Yes (on fetch runs) |
| Ollama on host | — | `host.docker.internal:11434` via `extra_hosts` |

Default mail-only digest makes no network calls.

## Ollama on the host

Enable LLM in Settings and point Ollama at `http://host.docker.internal:11434/api/generate` (or use sticky config). `docker-compose.yml` adds `host.docker.internal:host-gateway`.

## Do not run both web UIs

Native `rollup web` and Docker web share `./state/rollup.db` and the digest lock. Run one at a time.

## Doctor check

```bash
docker compose exec rollup rollup doctor \
  --root /data/mail/Newsletters.sbd \
  --mail-root /data/mail \
  --state-dir /data/state \
  --output-dir /data/output \
  --log-dir /data/logs
```

## Dev / CI smoke test (fixtures)

No real mail required:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
curl -fsS http://127.0.0.1:8765/rollups
```

Uses `tests/fixtures` as mail root and ephemeral volumes for state/output/logs.

## Bind flag

Docker uses `--allow-non-loopback-bind` so Flask can listen on `0.0.0.0` inside the container. Host-header validation still requires loopback `Host:` headers — access via `http://localhost:8765` only.

Native use stays unchanged:

```bash
rollup web --host 127.0.0.1
```

## Related docs

- [WEB.md](WEB.md) — web UI features (Articles, Reddit, Run Studio)
- [CONFIG.md](CONFIG.md) — TOML and env file
- [EXAMPLES.md](EXAMPLES.md) — CLI examples
