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
- Web writes update only `{state_dir}/rollup.db` (ratings, interaction, source policy overrides, run index)
- CSRF tokens required on all POST forms
- Archived HTML artifacts are served as **attachments** (not inline)
- Digest Markdown/HTML generation is unchanged

## Data model notes

- Ratings and interaction state are keyed by stable `message_key` and survive regenerating digests or deleting artifact files
- Quality ranking uses a Bayesian adjusted score with prior = mean of per-source means (read/save/dismiss rates are display-only)
- Indexing is transactional; failures leave the previous complete index intact
- Dry-run digests create no web index rows

## Concurrent cron + web

Digest holds the run lock file; web rating writes use short SQLite transactions. If the database is busy, policy/rating POSTs return HTTP 503. Do not raise SQLite busy timeout casually.

## Backup

Back up `{state_dir}/rollup.db` (and optionally `web_secret`) to preserve ratings and interaction state.
