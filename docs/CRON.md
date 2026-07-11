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
| 1 | Hard failure (safety, lock, missing root, write failure, no usable digest) |
| 2 | Partial success — usable digest written, but material issues occurred |

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
