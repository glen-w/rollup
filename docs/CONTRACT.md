# Product contract

Rollup is a **local-first personal briefing engine**. It reduces a chosen
information environment into a **bounded reading object** — a finite digest
worth reading — without becoming the store of record for those sources.

Thunderbird **owns the ongoing stream**. Rollup takes a time window and
produces **the rollup**. You do not need a special newsletter address, a
second mailbox, or to make Rollup authoritative for subscriptions.

Optional **LinkedIn**, **Reddit**, and **webpage** sources add material at
digest time. They are ingest transports, not inboxes: no continuous feed, no
unread state. They do not replace the mail-store contract.

This is a different product from an RSS reader:

| | RSS reader | Rollup |
|--|------------|--------|
| Shape | source → stream → user triages items | source → collection window → filtering / grouping / synthesis → finished briefing |
| Job | show everything new from these sources | turn a messy pile you already let into orbit into something finite and worth reading |

The product is not “the self-hosted AI newsletter reader”. In 2026 that niche
has competitors. Rollup’s distinction is read-only existing mail + durable
source policy + publication integrity + multi-format digest output. See
[COMPARISON.md](COMPARISON.md) and [ROADMAP.md](ROADMAP.md).

## Who owns what

| Layer | Owner | Responsibility |
|-------|--------|----------------|
| Filing | Thunderbird message filters | Move newsletters into folders under a `.sbd` tree |
| Window & folders | Run profile / CLI / TOML | Which calendar days and which folders enter the digest |
| LinkedIn / Reddit / webpages | TOML + GUI + opt-in CLI flags | Ingest transports for the collection window (not inboxes). Credentials in env only; payloads cached in `rollup.db`. Details: [CONFIG.md](CONFIG.md) |
| Source policy | `rollup sources` (SQLite) | Enable/disable, priority, type override, always-surface, grouping |
| Summaries | Preview by default; LLM via `--ollama` | Entry summaries. Whole-digest QA is `--final-review` (independent of `--ollama`) |
| Attention (planned) | Interest profile + ratings | Rank or annotate. Must **not** silently drop included material |

## Invariants

These do not change without an explicit contract revision:

- Never write, delete, or rename anything under the mail root
- Default `rollup digest` makes **no network calls**
- LLM summarisation is opt-in (`--ollama`). `--final-review` calls a model
  independently and does **not** require `--ollama`
- Web UI is loopback and single-user; GET routes are read-only
- Secrets never live in TOML or the web UI
- Publication is staged + rename; `latest.*` and seen-state cross an
  **irreversible boundary** only after required artifacts land
- Reader bodies are a capped convenience cache, not a mailbox archive
- Ranking, when added, changes attention — not deterministic inclusion
- Network sources (LinkedIn, Reddit, webpages; RSS if it ever exists) are
  **ingest transports** for the collection window. Rollup never grows an RSS
  inbox, unread counts, mark-all-read, or a scrolling feed

## Non-goals (through 1.0)

- IMAP / Gmail API / Maildir backends (Gmail API is [post-1.0](ROADMAP.md))
- Thunderbird add-on (XPI)
- Multi-user or non-loopback web UI
- In-app digest scheduler (use [CRON.md](CRON.md) / launchd)
- Exposing classifier thresholds as user knobs
- Built-in workflow engine, generic plugin-everything, or a mobile app
- An RSS **reader** (unread counts, folders, stars, sync, mark-all-read). RSS
  as a silent ingest transport is [parked](ROADMAP.md), same pattern as LinkedIn
  / Reddit today
- Official LinkedIn API (v1 uses session cookies + Voyager `profileUpdatesV2`)

## Sensible defaults

- `rollup digest` needs no config file when mail lives under a discoverable layout
- Default profile is **weekly** (7-day lookback, grouping on)
- Folder accents are deterministic from folder names; personal emoji/colors live in TOML

See [CONFIG.md](CONFIG.md) for TOML, profiles, and path discovery.

## Runtime integrity (persistence and publication)

Authoritative types live in `rollup.run_contracts`. Exit codes: `0` success, `1` hard failure, `2` partial — details in [CRON.md](CRON.md).

### Irreversible publication boundary

Do **not** update `latest.*`, seen-state, web index, or derive `success` until every **required** dated artifact and every **required** output writer has landed in its final path. Staged temporary files are never published artifacts.

Ordering after a successful no-input gate:

1. Summarize / review / render into a run-scoped staging area under the output directory
2. `fsync` staged files; rename into final names
3. Cross irreversible boundary
4. Optionally publish `latest.*`
5. Update seen-state (only for messages included in the published digest)
6. Write manifest from accumulated lifecycle state (partial runs included when possible)
7. Web-index (typed secondary degradation; does not alone force partial)
8. Derive final status / exit

Publication is **staged + rename**, not a filesystem-wide atomic multi-file transaction. Crash between renames is recovered on the next run (stale temps discarded; half-published sets reported; latest/seen not advanced incorrectly).

### Writers

Enabled writers are classified `required` or `optional` on `EffectiveRun`. Core Markdown/HTML are always required. Enabled add-on writers default to **required**. Optional writer failure yields partial (exit 2) after required publication succeeds.

### Empty window vs no-input

Distinguish discovered / parse-candidate / parsed-ok / parse-failed / in-window / filtered / included message counts. Empty-window **success** is allowed when discovery and parsing are healthy and `messages_included == 0`; `latest.*` is refused. No-input **hard failure** (exit 1) when an explicit include matches nothing, no folders are readable, or every parse candidate fails — gated **before** Ollama, final review, rendering, writers, state mutation, and publication.

### Schema version

`schema_version` labels one **canonical full** database shape (including empty cache/feature tables). The current package version is **15** (listing caches for Reddit/LinkedIn, webpage queue, reader bodies, source registry, web archive). Version is never written before migrations complete, never lowered, and future versions are refused before any mutate. See migration tests under `tests/test_schema_migrations.py`.

### Primary summary variant

The first entry in the validated summary-plan order is primary for filenames, `latest.*`, primary manifest fields, returned paths, and web indexing. Duplicate variants are rejected. Variants share the same eligible message identity set and stable group identities.
