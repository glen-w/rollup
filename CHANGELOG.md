# Changelog

All notable changes to Rollup are documented in this file.

## Unreleased

## 0.8.5 — 2026-08-31

### Added

- **LinkedIn `fromMember` feeds** as digest folders (`linkedin:<slug>`): save a faceted content-search URL in `[linkedin.searches.*]`, opt in with `--linkedin` or `[linkedin].enabled`. Fetch uses Voyager `profileUpdatesV2` with `ROLLUP_LINKEDIN_LI_AT` and `ROLLUP_LINKEDIN_JSESSIONID` from the environment (never TOML). Posts are dated from activity ids; lookback still applies after ingest. Mail-only runs degrade to partial (exit 2) if LinkedIn fails; LinkedIn-only runs hard-fail. Configuration Centre and Run Studio cover search URLs and `--linkedin`. Docs: [CONFIG.md](docs/CONFIG.md#linkedin-content-searches-optional), [EXAMPLES.md](docs/EXAMPLES.md#linkedin-content-searches-opt-in-network), [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md#linkedin-fetch-failed-401--429--checkpoint).
- **Run Studio live progress**: digest runs as a background subprocess with a progress bar and log tail (`GET /run/status`).

## 0.8.3 — 2026-08-25

### Changed

- **Structural hardening**: shared `discovery.list_flat_mbox_names` for Settings and Run Studio folder listing; unified `rollup.web.config.load_web_config_document` for TOML loading across web routes; renamed scheduler `cron_helpers.build_scheduled_digest_argv` to avoid colliding with `config_service.build_digest_argv`.

## 0.8.2 — 2026-08-25

### Added

- **`item_list` newsletter type** with **`preserve`** summary profile: minimal LLM cleaning for job alerts, scholar updates, journal TOCs, and other long bullet lists where every entry must survive. Auto-classified or set per source (`rollup sources set … --type item_list`).
- Web **info macros** and structured key/value panels (status badges, provenance chips) across Admin, Rollups, Sources, and Run Studio.

## 0.8.1 — 2026-08-24

### Added

- **Effort model overrides**: `[efforts.light|balanced|high]` in TOML (and Configuration Centre) customise which Ollama models each effort ladder uses, without replacing the whole summary profile set. `--list-efforts` and doctor show the effective models.
- **`--single-model`**: one-shot override for every summary profile, group/fallback, and final review (effort still controls budgets). Available on `digest` and `doctor`. Run Studio Compose has a checkbox plus an Ollama dropdown of local tags (`POST /run/ollama-models`; GET `/run` never contacts Ollama); not sticky.

## 0.7.0 — 2026-08-05

### Added

- **Roadmap** ([docs/ROADMAP.md](docs/ROADMAP.md)): shipped highlights, near-term engineering follow-ups, and product non-goals.
- **Agent notes** (`AGENTS.md`) for local/cloud contributor setup.
- Docs for sticky↔CLI mapping in [docs/CONFIG.md](docs/CONFIG.md); README project layout and doc index updated.
- Tests for `cli_parser` re-export, sticky argv/apply round-trip, and `run_digest` path-validation helper.

### Changed

- Ignore local coverage artifacts (`.coverage`, `htmlcov/`, …).

## 0.6.3 — 2026-08-05

### Added

- **Configuration Centre** (`/settings`): edit real digest TOML (paths, profiles, Ollama/effort/summary profile, writers, folder presentation, `[ui]` prefs) via shared `config_service` — validated preview diff, maintenance-token confirm, atomic save, `.bak` + timestamped backups, optimistic concurrency. No parallel SQLite web settings for digest config.
- **Run Studio** (`/run`): compose profile/overrides, show effective run, dry-run and synchronous subprocess digest with status/log/artifact links and CLI/cron snippets (no in-app scheduler).
- Sticky schema: `ollama_model`, `summary_profile`; folder `display_name` / `order`; `[ui]` landing page, preferred view, onboarding flag.
- Shared `sticky_flags` registry for sticky↔CLI mapping; `rollup web` applies sticky TOML paths like digest.
- First-run checklist and appearance previews in Settings; Archive “Open preferred” affordances from `[ui].preferred_view`.

### Changed

- Argparse construction lives in `cli_parser` (`rollup.cli.build_parser` still re-exported). `run_digest` orchestrates private phase helpers via `_DigestSession` (validate paths → lock/manifest → core stages → emit artifacts → release → web index).

### Fixed

- Web session secret reload no longer `.strip()`s binary bytes (whitespace in `token_bytes` corrupted secrets and flaked tests).
- Run Studio busy path returns HTTP 503 with `Retry-After` instead of a redirect tuple.

## 0.6.2 — 2026-08-05

### Added

- **Web admin observability**: read-only Admin hub (doctor/schema/runs/bodies), Registry management (`/sources/registry`), POST-only deep diagnostics, bounded redacted manifesto scanning with incomplete-history disclaimer.
- **Admin maintenance surface**: backfill / prune / delete-all / vacuum with preview→confirm tokens; alias preview/confirm; separate default-digest effective-configuration panel.
- **Read-only GET DB contract**: all browse routes use SQLite `mode=ro` + `query_only`; `init_db` only at web startup; Host validation + global `no-store`.
- Core helpers: `connect_db_readonly` / `connect_db_mutator`, `run_doctor_readonly`, paginated `list_source_registry_page`, `compute_source_revision`, backfill uniqueness scan (`scan_backfill_candidates`), server-side one-time maintenance tokens.

### Changed

- POST mutations open short-lived write connections **after** CSRF/validation; deep-check stays read-only at the connection layer.
- Source detail is provenance-first (inferred vs overrides); quality ranking kept separate from registry ops.
- Shared `parse_override_updates` + summary-profile registry validation; `set_overrides` deletes all-null rows; registry list uses batched revisions and `has_next` (no unbounded `COUNT(*)`); filter `all` includes every lifecycle.
- Alias merge flattens chains, runs post-merge invariants, and preserves `updated_by` on insert and conflict-update.
- Reader-body cheap checks use SQL aggregates; full FK/hash deep checks are Admin POST-only (incremental FK fetch).
- Manifest scan examines ≤ `max_dir_entries`, sorts by persisted timestamp, then keeps `max_files`; containment via `is_inside` / `Path.is_relative_to`.
- Backfill takes a post-scan mbox identity check, binds identities into preview fingerprints, and always validates newsletter-root containment in `run_backfill`.
- Session cookies use SameSite=Strict.

### Fixed

- Backfill no longer writes the first of conflicting duplicate `message_key` hashes; ambiguous keys are excluded after a complete scan.
- Maintenance token replay via restored session cookies: nonces live in a bounded server-side store.

## 0.6.1 — 2026-08-04

### Changed

- **Branding assets**: digest/EPUB output uses a compact grayscale e-ink logo (~5 KB, 200px) instead of the prior ~1.3 MB RGBA PNG; color logo kept (compressed) for the web UI and docs.
- **Runtime integrity foundation**: authoritative SQLite migrations (no premature/downgrade version writes; refuse future versions before mutate; canonical full schema shape; transactional summary rebuilds). See [docs/CONTRACT.md](docs/CONTRACT.md) and [docs/CRON.md](docs/CRON.md).
- **Core path safety**: `validate_writable_run_paths` fences newsletter root and mail root; called from `run_digest` before opening writable state (CLI validation remains UX-only). Newsletter root must be inside `--mail-root` (inferred from the `.sbd` parent when only `--root` is set).
- **Discovery**: dotted mbox names (e.g. `AI.News`) supported; directory/file symlinks rejected; sidecar exclusions explicit.
- **No-input gate** before Ollama/DB/summarize; empty-window success may write dated digests but refuses `latest.*`; mbox mutation excludes folder results and refuses `latest.*`.
- **Output writers** run inside the digest pipeline before latest/seen/index; profile export respects `--dry-run`.

### Fixed

- Schema singleton repair no longer lowers a future `schema_version` (reproduced 11→10) before refuse.
- Incomplete v7 registries are repaired or refused instead of accepted on `sources`-only checks.

## 0.6.0 — 2026-08-04

### Added

- **Optional TOML config** (`~/.config/rollup/config.toml`, `./rollup.toml`, or `--config`): sticky paths, lookback, folders, effort, ollama, grouping, and folder themes. CLI flags still win. See [docs/CONFIG.md](docs/CONFIG.md).
- **`rollup config print`**: dump effective merged settings as JSON.
- **Run profiles** (`--profile weekly|daily|…`, `--list-profiles`, TOML `[profiles.*]`): lookback/grouping habits, composed with existing `--effort`.
- **`--effort {light,balanced,high}`**: machine-power presets that swap the summary model ladder plus companion defaults (`ollama-model`, `final-review-model`, `max_chars_for_llm`) in one flag. Default `balanced` preserves prior behavior. `--list-efforts` prints the bundles; doctor reports expected models per effort.
- **Output writer plugin seam** (`rollup.output_writers`): post-digest addons discovered as builtins plus `rollup.output_writers` entry points. Enable with `--output NAME` (repeatable); default runs **all** discovered writers. Pass `--output none` for Markdown/HTML only. Sticky TOML `output` supported.
- **Built-in writers `txt`, `json`, `epub`**: plain-text (link-free), structured digest JSON (`schema_version` 1, no raw bodies), and rich EPUB (link-free offline summaries; optional `pip install 'rollup[epub]'`). See [docs/OUTPUT_WRITERS.md](docs/OUTPUT_WRITERS.md).
- **Thunderbird path discovery**: when defaults are missing, locate a single `Newsletters.sbd` under macOS Thunderbird profiles; doctor reports candidates.
- **Folder themes**: deterministic accents from folder names; optional emoji/accent overrides via TOML `[folders.*]`.
- Product contract doc: [docs/CONTRACT.md](docs/CONTRACT.md).

### Changed

- Personal hardcoded folder emoji/accent maps removed from package defaults (restore via TOML if desired).
- README / examples / cron docs favor config.toml over one-off personal paths.
- **Default `--output-dir`** is now `~/Documents/rollup-outputs` (outside the repo / mail root). Override with `--output-dir` or sticky `output_dir` in config.toml.
- **Output root hygiene**: each digest run moves prior dated digest artifacts into `output_dir/archive/` so only the latest batch (plus `latest.*` and branding) remains visible in the root.
- **XTEINK** (formerly X3) lives in `rollup.addons.xteink` and runs through the output-writer seam. Writes Markdown only (`…-newsletter-digest.xteink.md`); no XTEINK HTML (use `epub` for a rich offline ebook). `--xteink` / `--output xteink` are the canonical names; `--x3` and `--output x3` remain as compatibility aliases. Shared URL-strip / wrap helpers live in `rollup.addons.offline_text` for XTEINK, TXT, and EPUB.
- **GPT-OSS empty summaries**: profile `think` now accepts `"low"` / `"medium"` / `"high"` (GPT-OSS ignores bools). Built-in `deep` and `--effort high` `standard` use `think: "low"` with `num_predict: 2048` so reasoning cannot consume the whole token budget and leave `response` empty.

### Compatibility

- Zero-config still works when `~/email/gmail/Newsletters.sbd` exists.
- Existing CLI flags and source registry behavior unchanged.

## 0.5.2 — 2026-07-30

### Improved

- **Grouping**: `auto` falls back to same-source `sender_batch` when daily/notification heuristics fail (e.g. mixed promo + news from one sender)
- Digest groups render **before** standalone cards within each folder
- Group cards use the same **folder accent border** as newsletter cards

### Fixed

- **Read newsletter** expander no longer embeds the full site chrome (logo/nav) or large blank gaps: `partial=1` is built as its own query URL instead of appending `?partial=1` onto a `body_url` that already has `?run=…`

## 0.5.1 — 2026-07-13

### Added

- **Reader bodies** (schema v9–v10): capped plaintext newsletter storage in `message_reader_bodies`, indexed during digest runs
- Web UI **Read newsletter** expander (lazy fetch) and full-page reader at `/messages/<opaque>/body`
- Versioned reader-text normalisation (`READER_TEXT_VERSION=1`) and provenance columns (schema v10)
- `/admin` aggregate stats and integrity checks; `rollup bodies` CLI (`stats`, `check`, `backfill`, `prune`, `delete`, `vacuum`)
- Rollup-aware reader navigation (prev/next/back) when opened from an entry card with run context

### Fixed

- Reader-body schema migration repairs databases where `schema_version` was ahead of the `message_reader_bodies` table

### Compatibility

- Opening a v8 `rollup.db` migrates additively to v10
- Existing CLI digest Markdown/HTML outputs unchanged

## 0.5.0 — 2026-07-13

### Added

- Local **web UI** (`rollup web`) for browsing indexed rollups, rating messages, interaction state, and newsletter quality ranking
- Optional dependency extra `[web]` (`flask>=3.1.3,<4`); loopback-only bind (`127.0.0.1` / `::1`)
- SQLite schema **v8**: `rollup_runs`, `rollup_entries`, `message_ratings`, reason codes, `message_interaction`, `message_source_links`
- Digest pipeline indexes runs after dated MD/HTML (and manifest when enabled); `--no-manifest` still indexes; dry-run never indexes
- Explicit `rollup web reindex` for manifest metadata backfill (no startup mutation)
- Docs: [docs/WEB.md](docs/WEB.md)

### Compatibility

- Opening a v7 `rollup.db` migrates additively to v8
- Existing CLI digest Markdown/HTML outputs unchanged
- Core CLI (`digest`, `doctor`, `sources`, …) works without installing `[web]`

## 0.4.3 — 2026-07-12

### Added

- Persistent **source registry** in SQLite (schema **v7**): identity, observations, overrides, aliases, cadence samples, observation dedup
- Canonical source keys: `list:` (List-ID) outranks `from:` (From address); unidentifiable messages stay out of the registry
- CLI: `rollup sources list|show|set|clear|enable|disable|alias|export|import|doctor`
- Pipeline integration: observe → immutable snapshot → filter/group/summary/render; dry-run opens no DB
- Grouping policy `sender_batch` plus exact contracts for standalone / notification_stream / daily_editions / auto
- Manifest `source_registry` telemetry block (counts only; privacy-allowlisted)
- Docs: [docs/SOURCES.md](docs/SOURCES.md)

### Compatibility

- Default digests with an empty registry and gated inference remain behaviour-compatible with 0.4.x
- Opening a v6 `rollup.db` migrates additively to v7
- Manifest schema remains **v2** (additive `source_registry` block)

## 0.4.2 — 2026-07-12

### Changed

- `Config` no longer carries presentation/run-control flags (`dry_run`, `quiet`, `verbose`); `RunOptions` is the sole owner
- `EffectiveRun` / `resolve_effective_run` captures effective runtime decisions once in `run_digest`
- Manifest publication telemetry uses `dated_outputs_written`; readers still accept the legacy `outputs_published` field
- Default mail paths are based on `Path.home()` instead of machine-specific `/Users/89298/...` literals

### Fixed / Hardened

- Provider exception policy now degrades only named provider transport/payload failures; programming faults hard-fail
- Publication contracts clarified: final-review sidecar failures mark partial, latest publication failure still permits seen-state updates, and manifest/seen-state failures produce exit 2 when the digest is usable
- `latest.md` / `latest.html` are published atomically as a pair
- Final-review apply recomputes digest fingerprints before trusting cached or live review output

## 0.4.1 — 2026-07-12

### Changed

- Minimum `requests` dependency raised to `>=2.33.0` (CVE fix floor)

### Fixed / Hardened

- Final-review **apply** no longer synthesises missing fingerprint echoes; missing or mismatched echoes globally skip all patches
- Apply requires `issue_id`, unique issue ids, and literal boolean `safe_auto_fix: true`; unattended/conservative caps reject the **whole** patch set
- Central `validate_phase3_runtime_config` rejects invalid flag combinations (group-summaries without Ollama/grouping; non-`primary` variant policy; removed `group_summary_profile`)
- Group summaries use shared Ollama stream guards; call budget counts network attempts including retries; cache write failures still render blurbs and mark degraded
- Manifest schema **v2** adds `final_review` and `group_summaries` telemetry blocks (v1 manifests remain readable)
- Degraded group summaries / cache errors → run status `partial` (exit 2) when the digest remains usable

### Removed

- `group_summary_profile` config knob (presence fails validation)
- Dead `fallback_count` on group-summary metadata

### Compatibility

- Default digests without apply / group-summaries remain report-mode compatible
- Writers emit manifest schema 2; readers accept schema 1 and 2

## 0.4.0 — 2026-07-12

### Added

- Final-review **apply** mode (`--final-review-mode apply`) with pure patch transforms, hard validators, and cron fail-closed (`--final-review-allow-cron-apply`)
- Opt-in group-level LLM summaries (`--group-summaries`) with dedicated SQLite cache (schema v6)
- Group summary rendering in Markdown/HTML when present; deterministic headers unchanged when absent
- Publication failure surfaces as partial exit (2); lock/manifest/publication ImportError soft-disables removed

### Changed

- Schema version 5 → 6 (additive `group_summary_generations` / `group_summary_by_key`; entry caches preserved). Runtime group-summary cache uses `group_summary_by_key` only.
- Final-review prompts/schema support `issue_id` patches; report mode remains default

### Compatibility

- Default digests (no apply, no `--group-summaries`) match 0.3.0 behaviour
- Opening a v0.3 `rollup.db` migrates additively to v6

## 0.3.0 — 2026-07-11

### Added

- Unattended `--cron` mode with quieter logs, transactional `latest.md` / `latest.html`, and exit codes 0/1/2
- Single-run advisory lock under `state/rollup.lock` with stale-lock recovery
- Failure-safe run manifests (`state/manifests/`) with schema validation and privacy allowlist
- `rollup doctor` diagnostics (`--json`, `--full`, `--network`)
- `rollup cron print-launchd` / `print-crontab` / `status` helpers (launchd preferred on macOS)
- Conservative deterministic grouping: `notification_stream`, `daily_editions`, standalone essays
- Grouped Markdown + accessible HTML rendering; `--grouping-report` / `--no-grouping`
- Typed pipeline stage results, injectable clock, atomic filesystem helpers
- Evidence-based parse anomalies (`date_invalid`, `body_truncated`, `empty_body`) with clearer counter taxonomy
- Docs: [docs/CRON.md](docs/CRON.md), [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md), group-LLM design note

### Changed

- Digest output stems use UTC timestamps plus short run id to avoid collisions
- Digest orchestration moved to `pipeline.py`; CLI focuses on argparse and exit mapping
- Default grouping is enabled (disable with `--no-grouping` for per-message cards)

### Compatibility

- Existing CLI flags remain valid
- With `--no-grouping`, digest structure matches prior per-message cards
- SQLite migrations remain additive; opening a v0.2 `rollup.db` is supported

## 0.2.0 — 2026-07-02

### Added

- Optional whole-digest final review layer (`--final-review`) with report-only QA sidecar JSON
- Final review profiles: `strict`, `concise`, `editorial`
- Final review cache in SQLite (`final_review_generations`; schema version 5)
- QA summary embedded in digest “Digest generation details” run-details section

### Changed

- Run-details subsection headings use consistent styling (Markdown `###`, HTML `h3.run-details-heading`)
- Summary routing metadata label unified to “Summary routing” (was “AI info” in HTML)
- Prompt templates package-data includes `prompts/final_review/` JSON and text files

## 0.1.0 — 2026-07-02

Initial release.

- Read-only Thunderbird mbox newsletter digest (`inventory`, `digest`)
- Markdown and HTML output with link cleanup, classification, and preview fallbacks (default; no Ollama server required)
- Optional local Ollama summarisation with per-type profile routing (`--ollama`)
- Prompt templates bundled in the installed package (`rollup/prompts/`; used only with `--ollama`)
- SQLite summary cache and seen-message state outside the mail root
- Summary-related CLI flags ignored (with warning) unless `--ollama` is enabled
