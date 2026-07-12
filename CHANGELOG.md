# Changelog

All notable changes to Rollup are documented in this file.

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
