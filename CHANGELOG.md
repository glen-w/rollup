# Changelog

All notable changes to Rollup are documented in this file.

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
