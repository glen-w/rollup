# Personal cron / unattended setup

Rollup is designed for a calm weekly reading habit. On macOS, prefer **launchd**.
Crontab remains a portable alternative.

## Terminology

| Term | Meaning |
|------|---------|
| **Weekly non-AI digest** | Normal `rollup digest` / `rollup digest --cron` using preview excerpts (no Ollama) |
| **Preview summaries** | Short excerpts taken from each message body when `--ollama` is off |
| **Dry-run** | `--dry-run` — parse and report only; **no** output files, state, logs, or network |
| **Cron mode** | `--cron` — quieter logs, publish `latest.*`, `mode=cron` in the run manifest |
| **Partial latest** | `--allow-partial-latest` — permit `latest.*` updates for partial runs; default is success-only |

Do not confuse preview summaries with dry-run.

## Recommended personal setup

1. Install Rollup in a project venv with a stable absolute Python path.
2. Run `rollup doctor` and fix any errors.
3. Run a manual digest once, then inspect `output/` and `state/manifests/`.
4. Schedule a weekly job with `rollup cron print-launchd` (macOS) or `print-crontab`.

## Single-run lock

Only one digest may run at a time. The lock file lives at `state_dir/rollup.lock`
(never under the mail root). A second invocation exits with code **1** and:

```text
ERROR: Another digest run is in progress (run_id=...)
```

Stale locks (dead PID or older than 6 hours) are recovered automatically.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (or dry-run success) |
| 1 | Hard failure (safety, lock, missing root, invalid config, write failure, no usable digest) |
| 2 | Partial success — usable digest written, but material issues occurred |

Degradation details:

| Condition | Exit | Durable-write behavior |
|-----------|------|------------------------|
| Final-review sidecar write fails | 2 | Dated digest remains usable; sidecar is outside the dated-output transaction |
| Final-review overall status is `fail` | 2 | Dated digest remains usable; inspect the final-review sidecar or manifest block |
| `latest.*` publication fails | 2 | Dated digest remains the source of truth; seen-state update still runs to avoid repeating undated items solely because latest aliases failed |
| Manifest write fails after a usable digest | 2 | Dated digest may exist, but the run is degraded because cron status cannot be trusted |
| Seen-state update fails after a usable digest | 2 | Dated digest may exist, but undated items may repeat on future runs |
| Group summaries degrade | 2 | Member summaries still render; cache/read/write or stream errors are recorded |
| High parse/summary error rates | 2 | Dated digest remains usable but incomplete or lower quality |

A global apply skip (e.g. missing fingerprint echo) alone does **not** force partial when the digest is otherwise successful—check the manifest `final_review` block.

**Invalid Phase-3 flags** (e.g. `--group-summaries` without `--ollama`, non-`primary` variant policy, cron apply without `--final-review-allow-cron-apply`) fail before the run with exit **1**.

Unattended apply uses conservative whole-set caps (`final_review_max_patches_unattended` / `final_review_max_changed_chars_unattended`): exceeding either skips **all** patches.

## launchd (preferred on macOS)

Generate a LaunchAgent plist with explicit paths:

```bash
rollup cron print-launchd \
  --python /Users/you/rollup/.venv/bin/python \
  --workdir /Users/you/rollup \
  --root /Users/you/email/gmail/Newsletters.sbd \
  --mail-root /Users/you/email/gmail \
  --output-dir /Users/you/rollup/output \
  --state-dir /Users/you/rollup/state \
  --log-dir /Users/you/rollup/logs \
  --weekday 0 --hour 8 --minute 0 \
  > ~/Library/LaunchAgents/com.rollup.digest.plist

launchctl load ~/Library/LaunchAgents/com.rollup.digest.plist
```

The plist sets `WorkingDirectory`, `StandardOutPath`, and `StandardErrorPath`.

## crontab (alternative)

```bash
rollup cron print-crontab \
  --python /Users/you/rollup/.venv/bin/python \
  --workdir /Users/you/rollup \
  --root /Users/you/email/gmail/Newsletters.sbd \
  --mail-root /Users/you/email/gmail \
  --output-dir /Users/you/rollup/output \
  --state-dir /Users/you/rollup/state \
  --log-dir /Users/you/rollup/logs
```

Example weekly non-AI digest (Sundays 08:00):

```cron
0 8 * * 0 cd /Users/you/rollup && /Users/you/rollup/.venv/bin/python -m rollup digest --cron \
  --root /Users/you/email/gmail/Newsletters.sbd \
  --mail-root /Users/you/email/gmail \
  --output-dir /Users/you/rollup/output \
  --state-dir /Users/you/rollup/state \
  --log-dir /Users/you/rollup/logs >> /Users/you/rollup/logs/cron.log 2>&1
```

## Check last run

```bash
rollup cron status --state-dir /Users/you/rollup/state
```

## Latest outputs

On successful `--cron` (or `--latest`) runs, Rollup atomically updates:

- `output/latest.md`
- `output/latest.html`

Partial/failed runs do **not** replace last-known-good latest digests by default.
Pass `--allow-partial-latest` only if you want partial but usable runs to update
the `latest.*` aliases. `latest.md` and `latest.html` are published as one file
set, so they do not point at different runs.

## Durable write ordering

For non-dry-run digests, durable writes are ordered so the dated digest is the
source of truth:

1. Write dated Markdown + HTML outputs.
2. If requested and allowed by status, atomically publish `latest.md` and
   `latest.html` together.
3. Update seen-state for rendered undated items. This still runs when `latest.*`
   publication fails, because the dated digest exists.
4. Write the run manifest. `dated_outputs_written` records whether the dated
   Markdown + HTML outputs were written; `latest_outputs_updated` records whether
   the latest aliases moved.

Readers still accept the legacy manifest key `outputs_published` as an alias for
`dated_outputs_written`, but new manifests write `dated_outputs_written`.
