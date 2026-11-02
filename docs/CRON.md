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

## Recommended setup

1. Bring up with Docker Compose ([DOCKER.md](DOCKER.md)); put sticky paths in `~/.config/rollup/config.toml` via Settings or by hand ([CONFIG.md](CONFIG.md)).
2. Run `docker compose exec rollup rollup doctor …` (or host `rollup doctor`) and fix any errors.
3. Run a manual digest once (Run Studio or CLI), then inspect `output/` and `state/manifests/`.
4. Schedule a weekly job — `docker compose exec` from launchd/crontab, or a host venv with `rollup cron print-launchd` / `print-crontab` ([advanced host install](../README.md#advanced-host-python--venv)).

### Advanced: host Python scheduling

When running without Compose, install Rollup in a project venv with a stable absolute Python path, then use `rollup cron print-launchd` (macOS) or `print-crontab`.

## Single-run lock

Only one digest may run at a time. The lock file lives at `state_dir/rollup.lock`
(never under the mail root). A second invocation exits with code **1** and:

```text
ERROR: Another digest run is in progress (run_id=...)
```

Stale locks (dead PID or older than 6 hours) are recovered automatically.
`rollup doctor` reports a `run_lock` check: a held lock (pid, operation, run id)
or a stale one that the next run will recover.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success (or dry-run success), including empty-window success and web-index-only degradation |
| 1 | Hard failure (safety, lock, missing root, invalid config, no-input, required publication failure, no usable digest) |
| 2 | Partial success — required dated digest usable, but material issues occurred. The CLI prints one `partial: <reasons>` line on stderr (for example `partial: optional writer, gmail`) and the manifest lists the same flags under `degradations` |

Degradation details (integrity matrix; see also [CONTRACT.md](CONTRACT.md) and `rollup.run_contracts`):

| Condition | Exit | Durable-write behavior |
|-----------|------|------------------------|
| No-input (include miss / no readable folders / all parse candidates failed) | 1 | Nothing published; gate runs before Ollama, review, render, writers, state, publication |
| Empty date window (`messages_included == 0`, healthy discovery/parse) | 0 | Dated empty digest + required writers may write; **`latest.*` refused** |
| Mbox mutation during parse | 2 | Mutated folders excluded from published content; `latest.*` refused; escalate to 1 if exclusion yields no-input |
| Required dated artifact or required writer failure | 1 | Irreversible boundary not crossed (or partial rename recovery on next run → 2 if half-published) |
| Optional writer failure | 2 | Required pubs already succeeded |
| Final-review sidecar write fails | 2 | Dated digest remains usable; sidecar is outside the required dated set |
| Final-review overall status is `fail` | 2 | Dated digest remains usable; inspect the final-review sidecar or manifest block |
| Final-review **context overflow skip** (prompt still too large after shrink ladder; `review_source=skipped`) | 0 if otherwise success | Dated digest + **`latest.*` allowed**; sidecar records that review did not run (digest not model-QA'd). Editorial `fail` from a completed model review still yields exit 2 |
| `latest.*` publication fails | 2 | Dated digest remains source of truth; seen-state runs only after required pubs succeeded |
| Manifest write fails after a usable digest | 2 | Original failure diagnosis preserved; minimal fallback diagnostic on stderr / run events |
| Seen-state update fails after required pubs | 2 | Dated digest may exist; undated items may repeat on future runs |
| Web-index failure | 0 if otherwise success | Typed secondary degradation; manifest event + diagnostic; does **not** alone force partial |
| Group summaries degrade | 2 | Member summaries still render; cache/read/write or stream errors are recorded |
| LinkedIn fetch fails; mail still publishes | 2 | Dated mail digest usable; LinkedIn section omitted; `linkedin_fetch_failed` (or similar) in warnings |
| LinkedIn-only run (`--folder linkedin:…`, no mbox) and fetch fails | 1 | Nothing published |
| Reddit fetch fails; mail still publishes | 2 | Dated mail digest usable; Reddit section omitted; `reddit_fetch_failed` (or similar) in warnings |
| Reddit-only run (`--folder reddit:…`, no mbox) and fetch fails | 1 | Nothing published |
| Gmail label fetch fails; other sources still publish | 2 | Dated digest usable; stale label snapshot reused when available (`gmail_cache_stale`), otherwise label omitted (`gmail_fetch_failed`) |
| Gmail-only run (no mbox / LinkedIn / Reddit / webpage input) and fetch fails | 1 | Nothing published |
| High parse/summary error rates | 2 | Dated digest remains usable but incomplete or lower quality |

**Irreversible boundary:** `latest.*`, seen-state, web index, and `success` status wait until every required dated artifact and required output writer has final paths. Manifest / web-index failures must not mark unpublished messages seen.

A global apply skip (e.g. missing fingerprint echo) alone does **not** force partial when the digest is otherwise successful—check the manifest `final_review` block.

**Invalid Phase-3 flags** (e.g. `--group-summaries` without `--ollama`, non-`primary` variant policy, cron apply without `--final-review-allow-cron-apply`) fail before the run with exit **1**.

Unattended apply uses conservative whole-set caps (`final_review_max_patches_unattended` / `final_review_max_changed_chars_unattended`): exceeding either skips **all** patches.

**Dry-run** must not create databases, migrate schema, WAL/SHM files, locks, profile exports, staged publication temps, or writer artifacts.

## Environment variables (network sources)

CLI startup loads `~/.config/rollup/env` (or `ROLLUP_ENV_FILE`) and does **not**
override variables already in the process environment. A launchd/cron job that
runs as your user therefore picks up LinkedIn cookies (and optional Reddit OAuth)
from that file — you do not have to duplicate them in the plist.

If `[linkedin].enabled` is set and the env file is missing, put cookies in the
job environment (never TOML):

```bash
export ROLLUP_LINKEDIN_LI_AT='…'
export ROLLUP_LINKEDIN_JSESSIONID='ajax:…'
```

For launchd, `EnvironmentVariables` in the plist or a wrapper script still work.
Refresh both cookies when LinkedIn 401s. How to copy cookies: [EXAMPLES.md](EXAMPLES.md#2-copy-session-cookies-li_at-and-jsessionid).
Article fetch (default on) adds extra HTTP to linked article hosts; disable with
`[linkedin].article_fetch = false` if the scheduled job should stay Voyager-only.

Optional Reddit script-app OAuth uses `ROLLUP_REDDIT_CLIENT_ID`,
`ROLLUP_REDDIT_CLIENT_SECRET`, `ROLLUP_REDDIT_USERNAME`, and
`ROLLUP_REDDIT_PASSWORD` in the same env file.

Gmail ingest (`[gmail].enabled`) needs `ROLLUP_GMAIL_CLIENT_ID` /
`ROLLUP_GMAIL_CLIENT_SECRET` in the env file and a one-time interactive
`rollup gmail auth` run as the same user with the same `--state-dir`; the
refresh token in `{state_dir}/gmail_token.json` is renewed automatically by
scheduled runs. No browser is needed after that.

See [CONFIG.md](CONFIG.md#linkedin-content-searches-optional).

## Listing cache vs weekly jobs

Reddit and LinkedIn listings reuse SQLite snapshots for `fetch_ttl_hours`
(default 24); Gmail label syncs default to 6 hours. A weekly job usually
expires those windows and fetches live. A daily job within the window will
skip the network unless you pass `--reddit-refresh` / `--linkedin-refresh` /
`--gmail-refresh` or set the TTL to `0`.

## launchd (preferred on macOS)

Generate a LaunchAgent plist with explicit paths (or rely on config.toml and omit `--root` / `--mail-root`):

```bash
rollup cron print-launchd \
  --python /Users/you/rollup/.venv/bin/python \
  --workdir /Users/you/rollup \
  --output-dir /Users/you/Documents/rollup-outputs \
  --state-dir /Users/you/rollup/state \
  --log-dir /Users/you/rollup/logs \
  --weekday 0 --hour 8 --minute 0 \
  > ~/Library/LaunchAgents/com.rollup.digest.plist

launchctl load ~/Library/LaunchAgents/com.rollup.digest.plist
```

If paths are not in TOML yet, pass `--root` / `--mail-root` pointing at your Thunderbird `Newsletters.sbd` and its parent mail account directory.

The plist sets `WorkingDirectory`, `StandardOutPath`, and `StandardErrorPath`.

## crontab (alternative)

```bash
rollup cron print-crontab \
  --python /Users/you/rollup/.venv/bin/python \
  --workdir /Users/you/rollup \
  --output-dir /Users/you/Documents/rollup-outputs \
  --state-dir /Users/you/rollup/state \
  --log-dir /Users/you/rollup/logs
```

Example weekly non-AI digest (Sundays 08:00), assuming `~/.config/rollup/config.toml` sets `root` / `mail_root`:

```text
0 8 * * 0 cd /Users/you/rollup && /Users/you/rollup/.venv/bin/python -m rollup digest --cron \
  --output-dir /Users/you/Documents/rollup-outputs \
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
   the latest aliases moved; `degradations` lists the partial-exit flags and
   `lifecycle_events` the ordered publication steps that ran.

Dated files are written **before** the previous batch is moved into
`output_dir/archive/`, so a crash mid-publish cannot leave the output root empty.

Readers still accept the legacy manifest key `outputs_published` as an alias for
`dated_outputs_written`, but new manifests write `dated_outputs_written`.
