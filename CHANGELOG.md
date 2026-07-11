# Changelog

All notable changes to Rollup are documented in this file.

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
