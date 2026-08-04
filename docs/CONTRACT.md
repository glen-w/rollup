# Product contract: Thunderbird filters → Rollup digest

Rollup stays a **local, read-only** digest over Thunderbird **mbox** folders.

## Who owns what

| Layer | Owner | Responsibility |
|-------|--------|----------------|
| Filing | Thunderbird message filters | Move newsletters into folders under a `.sbd` tree |
| Window & folders | Rollup run profile / CLI / TOML | Which calendar days and which folders enter the digest |
| Noisy senders | `rollup sources` (SQLite) | Enable/disable, priority, type override, always-surface |
| Summaries | Optional local Ollama + `--effort` | Preview excerpts by default; LLM only with `--ollama` |

## Non-goals (for now)

- IMAP / Gmail API / Maildir backends
- Thunderbird add-on (XPI)
- Multi-user or non-loopback web UI
- Exposing classifier thresholds as user knobs

## Sensible defaults

- `rollup digest` needs no config file when mail lives under a discoverable layout
- Default profile is **weekly** (7-day lookback, grouping on)
- Folder accents are deterministic from folder names; personal emoji/colors live in TOML

See [CONFIG.md](CONFIG.md) for TOML, profiles, and path discovery.
