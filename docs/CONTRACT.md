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

`schema_version` labels one **canonical full** database shape (including empty cache/feature tables). Version is never written before migrations complete, never lowered, and future versions are refused before any mutate. See migration tests under `tests/test_schema_migrations.py`.

### Primary summary variant

The first entry in the validated summary-plan order is primary for filenames, `latest.*`, primary manifest fields, returned paths, and web indexing. Duplicate variants are rejected. Variants share the same eligible message identity set and stable group identities.
