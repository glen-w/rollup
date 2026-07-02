# Changelog

All notable changes to Rollup are documented in this file.

## 0.1.0 — 2026-07-02

Initial release.

- Read-only Thunderbird mbox newsletter digest (`inventory`, `digest`)
- Markdown and HTML output with link cleanup, classification, and preview fallbacks (default; no Ollama server required)
- Optional local Ollama summarisation with per-type profile routing (`--ollama`)
- Prompt templates bundled in the installed package (`rollup/prompts/`; used only with `--ollama`)
- SQLite summary cache and seen-message state outside the mail root
- Summary-related CLI flags ignored (with warning) unless `--ollama` is enabled
