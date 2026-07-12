# Source registry

Rollup keeps a durable **source registry** in `{state_dir}/rollup.db` so newsletter behaviour can stay consistent across weekly digests.

## Identity

Each message gets an optional `source_key`:

1. `list:` + normalised `List-ID` when present
2. else `from:` + normalised From address
3. else unidentifiable (`source_key` is null) — still digested, not stored in the registry

Folder is **not** part of identity. Subject / Reply-To / Return-Path / Sender are not used as keys.

## Controls

Per-source overrides (CLI):

| Setting | Meaning |
|---------|---------|
| enabled / disabled | Disabled sources are excluded after the date window |
| always-surface | Include undated seen messages from this source (does **not** bypass the lookback window) |
| priority | 0–100; higher sorts earlier in MD/HTML |
| type | Preferred newsletter type (classifier still runs; override is effective type) |
| grouping | `auto`, `standalone`, `sender_batch`, `notification_stream`, `daily_editions` |
| summary-profile | Preferred summary profile name |
| display-name | Render label |
| notes | Free-text note |

Disable always outranks always-surface.

## CLI

```bash
rollup sources list
rollup sources show from:alerts@github.com --json
rollup sources disable from:noisy@example.com
rollup sources set list:news.example.com --grouping sender_batch --priority 80
rollup sources clear list:news.example.com --all
rollup sources alias from:old@example.com from:new@example.com
rollup sources export --out ~/backup/sources.json
rollup sources import --from ~/backup/sources.json
rollup sources import --from ~/backup/sources.json --replace-all --i-understand-replace-all
rollup sources doctor --json
```

SQLite is canonical. Export/import covers anchors, overrides, and aliases (not regenerable observations by default).

Import merge semantics: absent field = unchanged; JSON `null` = clear; value = set. Validate-then-write; dry-run supported.

## Cron / dry-run

- Digest `--dry-run` opens **no** SQLite and writes no observations.
- Normal / cron digests observe sources under the shared state lock.
- `rollup sources … --dry-run` may read state but does not write.

## Cadence

Observed cadence is derived from up to 60 dated samples per source. `cadence_sample_count` is the number of dated **messages**. Inferred grouping applies only when confidence ≥ 0.6 and sample_count ≥ 5.

## Backup

```bash
rollup sources export --out sources.json
# and/or copy state/rollup.db
```

Observations are regenerable from mail; back up overrides and aliases.

## Limitations

- Shared ESP From addresses without List-ID collapse to one source.
- Reproducibility across machines depends on registry state (`policy_state_revision` in manifests); `config_fingerprint` does not include per-source overrides.
