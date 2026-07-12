# Rollup web UI

Local, single-user browser UI for browsing digests, rating emails, and reviewing newsletter quality.

## Install

```bash
pip install 'rollup[web]'
# or from a checkout:
pip install -e '.[web]'
```

## Start

```bash
rollup web
# optional:
rollup web --port 8765 --open
rollup web reindex   # backfill archive metadata from manifests/
```

Binds to **loopback only** (`127.0.0.1` by default; `::1` allowed). Non-loopback hosts are rejected.

## Safety

- Never writes to Thunderbird/Gmail mail stores
- Web writes update only `{state_dir}/rollup.db` (ratings, interaction, source policy overrides, run index, reader bodies)
- CSRF tokens required on all POST forms
- Archived HTML artifacts are served as **attachments** (not inline)
- Digest Markdown/HTML generation is unchanged
- Reader bodies are capped plaintext (32,000 characters) with inline http(s) links; images and raw HTML are excluded
- Bodies never appear in manifests or default exports

## Reader bodies

Pipeline indexing stores plaintext bodies in `message_reader_bodies` when a digest run indexes entries. In the web UI, each entry card offers **Read newsletter** (lazy expander + full-page fallback at `/messages/<opaque>/body`).

Historic runs without bodies require a new digest that includes those messages. Manifest reindex does not backfill bodies.

CLI maintenance:

```bash
rollup bodies stats
rollup bodies check
rollup bodies backfill --dry-run
rollup bodies prune --dry-run
```

Local **Admin** page (`/admin`) shows aggregate stats and integrity checks (no body snippets).

## Data model notes

- Ratings and interaction state are keyed by stable `message_key` and survive regenerating digests or deleting artifact files
- Quality ranking uses a Bayesian adjusted score with prior = mean of per-source means (read/save/dismiss rates are display-only)
- Indexing is transactional; failures leave the previous complete index intact
- Dry-run digests create no web index rows

## Concurrent cron + web

Digest holds the run lock file; web rating writes use short SQLite transactions. If the database is busy, policy/rating POSTs return HTTP 503. Do not raise SQLite busy timeout casually.

## Backup

Back up `{state_dir}/rollup.db` (and optionally `web_secret`) to preserve ratings and interaction state.
