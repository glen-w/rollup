# Roadmap

Where Rollup is headed, relative to the [product contract](CONTRACT.md) and
[competitive position](COMPARISON.md). Guidance for contributors, not a
commitment calendar.

Rollup reduces a chosen information environment into a **bounded reading
object**. The next work should deepen that briefing — not add another stream
to triage.

Existing sources already fit the model. Thunderbird owns the ongoing
newsletter stream; Reddit, LinkedIn, and article capture supply extra
material at digest time; Rollup takes a window and produces the rollup.

## Product sequence

**Toward 1.0 — make the briefing the product, then stop expanding surface.**

1. **Documentation consolidation.** README is the product entry; `docs/` is the
   reference. The contract stays short and authoritative. Remaining work is
   keeping the split honest as features land. Briefing-layer invariants:
   [design/briefing-arc.md](design/briefing-arc.md).
2. **Attention / relevance / skip-safely.** **Shipped.** `[interest].profile`
   plus Quality / saved / dismissed / source priority. Bands: high relevance /
   worth scanning / background / safely skip. Ranking never silently drops
   material. Optional LLM reband with `--ollama`.
3. **Cross-source consolidation.** **Shipped.** URL / title overlap overlay
   (`CoverageCluster`); cards stay in their folders with “Covered N ways”.
   `--no-coverage` / `--coverage-report`. Same-source grouping is unchanged.
4. **Briefing-level synthesis.** **Shipped.** Deterministic glance always;
   `--briefing-synthesis` opt-in LLM narrative. Not final-review.
5. **Search the accumulated corpus.** **Shipped.** Schema v17 FTS5
   (`message_corpus_fts`) over cached reader bodies, titles, and summaries.
   `rollup search` / `GET /search`; grounded `rollup ask` / `POST /ask`. Reader
   bodies stay a convenience cache.
6. **Feedback → source intelligence.** **Shipped.** Live suggestions from
   Quality / registry / recent coverage overlap: unused for eight weeks, mostly
   dismissed, overlapping coverage, consider lowering priority, mostly
   duplicate. `rollup sources suggest`; Quality and Registry cards. Apply is
   existing `set_overrides` only. Never auto-unsubscribe or mutate Thunderbird.
7. **Reading destinations.** The writer seam is already the right extension
   point. OPDS for e-readers; then optional send-to-Kindle / Calibre-style
   delivery as plugins. Reinforces “produce something worth reading away from
   the computer” rather than another infinite-scroll app.

**Then 1.0.**

**After 1.0 — Gmail ingestion.** Optional Gmail API (OAuth) as a read-only
alternative to local mbox, aligned with the [product contract](CONTRACT.md).
Thunderbird mbox remains primary through 1.0. Gmail expands the addressable
audience; doing it earlier risks becoming another generic inbox reader.

## Shipped (recent)

- Optional TOML sticky config, run profiles, effort presets, folder themes ([CONFIG.md](CONFIG.md))
- Output-writer plugin seam + builtins (xteink, txt, json, epub) ([OUTPUT_WRITERS.md](OUTPUT_WRITERS.md))
- Loopback web UI: Archive, Quality, Registry, Admin, reader bodies ([WEB.md](WEB.md))
- Configuration Centre (`/settings`) and Run Studio (`/run`) on the real TOML + CLI digest path
- Shared `sticky_flags` registry; `cli_parser` extraction; `run_digest` on the `rollup.pipeline` package (types / status / ingest / stages / publish)
- SQLite `rollup.state` package (schema + migrations; `init_db` facade) and source-registry listing helpers
- Optional LinkedIn `fromMember` folders, Reddit subreddits, webpage article queue
- **Reddit + LinkedIn listing cache** (`rollup.db` schema v15): persist network listings; `--reddit-refresh` / `--linkedin-refresh` force a live pull
- **Firefox Add to Rollup**: toolbar/context-menu capture into `webpage_queue` via `POST /articles/capture` (Bearer token in `{state_dir}/extension_token`). Ingest-only; `rollup web` must be running.
- **Google Scholar detailed mode**: `[scholar].mode = "detailed"` / `--scholar-mode detailed` fetches paper landing pages from Scholar alert emails and summarises each paper (schema v16 cache). Default mode keeps alerts as `item_list` mail.
- **Attention / skip-safely**: `[interest].profile`, `--interest-file`, `--no-attention`, `--attention-report`. Heuristic bands plus optional LLM reband. Start here / safely skip chrome. Inclusion invariant.
- **Cross-source coverage**: `--coverage-report` / `--no-coverage`. Overlay clusters, not a new group type.
- **Briefing synthesis**: deterministic glance always; `--briefing-synthesis` opt-in LLM narrative
- **Corpus FTS + Ask** (`rollup.db` schema v17): `rollup search` / `GET /search`; grounded `rollup ask` / `POST /ask` over reader-body / title / summary cache
- **Source intelligence**: `rollup sources suggest`; Quality/Registry suggestion cards; doctor `suggestions: N`

## Standing rule (network sources)

Persist fetched payloads in `rollup.db` and avoid re-calling expensive or
rate-limited APIs when a fresh-enough snapshot exists.

Network sources are **ingest transports** for the collection window: fetch at
digest time, fold into the briefing, do not grow an inbox.

## Parked (not the 1.0 arc)

These expand surface. They wait until the briefing itself is distinct.

- **RSS as ingest transport** — “include this publication in my weekly
  rollup”; fetch the lookback window at digest time; never an RSS inbox,
  unread state, or continuous feed. Same conceptual slot as LinkedIn / Reddit
  today. An RSS *reader* is a [non-goal](#non-goals-still).
- **Richer LinkedIn sources** beyond faceted `fromMember` search — keyword SRP
  if LinkedIn exposes a working content-search query again; company/org posts;
  follows/mentions. v1 stays author-list URLs mapped through Voyager
  `profileUpdatesV2`.
- Engineering hygiene when the next feature forces the edit: `render.py`
  MD/HTML shared structure. (`state` and `source_registry` listing already
  split; keep migration bodies and `classify_link` as-is.)

## Non-goals (still)

From [CONTRACT.md](CONTRACT.md). Do not prioritise these; existing non-goals
are helping the project. Output artifacts are a cheaper path to cross-device
reading than a mobile app.

- IMAP / Maildir backends (Gmail API → post-1.0 above)
- Thunderbird add-on (XPI)
- Multi-user or non-loopback web UI
- In-app digest scheduler (Run Studio stays a synchronous guided runner; use [CRON.md](CRON.md) / launchd)
- Exposing classifier thresholds as user knobs
- Built-in workflow engine or generic plugin-everything infrastructure
- Richer email-client operations (Rollup is not a mail client)
- Official mobile app
- An RSS **reader** (unread counts, folders, stars, sync, mark-all-read,
  scrolling feeds). That is Miniflux / FreshRSS / Readeck territory.

## Open product questions (parked)

- Richer onboarding when mail paths are undiscoverable
- Optional remote Ollama remains explicit (`--allow-remote-ollama`); optional LiteLLM via `rollup[llm]` + `--llm-provider litellm` (API keys from env only)
- Deeper Admin failure history when manifests are missing (incomplete-history disclaimer stays)
- LinkedIn HTML/API fragility — isolated fetch module; may break when LinkedIn changes

## Related docs

| Doc | Role |
|-----|------|
| [CONTRACT.md](CONTRACT.md) | Product shape and publication integrity |
| [COMPARISON.md](COMPARISON.md) | Competitive position |
| [CONFIG.md](CONFIG.md) | TOML, profiles, sticky ↔ CLI |
| [WEB.md](WEB.md) | Loopback UI surfaces |
| [DOCKER.md](DOCKER.md) | Default Compose deploy |
| [EXAMPLES.md](EXAMPLES.md) | Runnable recipes |
| [CHANGELOG.md](../CHANGELOG.md) | What shipped |
| [design/firefox-capture.md](design/firefox-capture.md) | Firefox Add to Rollup (packages, SLOC, reload, known issues, review) |
| [design/briefing-arc.md](design/briefing-arc.md) | Briefing-layer invariants toward 1.0 (attention, coverage, synthesis, FTS) |
