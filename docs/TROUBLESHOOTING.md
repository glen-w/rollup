# Troubleshooting

## Start with doctor

```bash
rollup doctor
rollup doctor --json
rollup doctor --full
rollup doctor --network   # or pass --ollama to enable Ollama checks
```

Fix hints are always included. Exit code 0 means no error-level checks failed
(warnings are OK).

## Common issues

### Newsletter root does not exist

Point `--root` at your Thunderbird `.sbd` tree, or use the synthetic fixtures:

```bash
rollup inventory --root tests/fixtures/Newsletters.sbd
```

### Writable path inside mail root

Move `--output-dir`, `--state-dir`, and `--log-dir` outside `--mail-root`.

### Another digest run is in progress

A lock is held at `state/rollup.lock`. Wait for the other run, or if the process
is dead, the next run recovers stale locks automatically.

### Empty digest / everything skipped outside window

Fixture or live mail dates may be outside `--lookback-days`. Increase the
lookback or regenerate fixtures with `python tests/generate_fixtures.py`.

### Ollama not reachable

Default digests do not need Ollama. If you passed `--ollama`, start the local
server or omit the flag to use preview summaries.

### Parse anomalies vs fatal errors

| Kind | Meaning |
|------|---------|
| Fatal parse error | Message produced no `ParsedMessage` (corrupt entry, open failure) |
| Parse anomaly | Recoverable (encoding replaced, body truncated, invalid date) |
| Filter outcome | Outside lookback window / seen undated |
| Content quality | Empty body with subject — still a valid short notification |

Missing dates use the established undated-message path; they are not fatal
parse errors.

### Manifests

Each non-dry-run digest writes `state/manifests/<timestamp>-<run_id>.json`.
Failure manifests are written whenever `state_dir` is writable. Inspect:

```bash
cat state/manifests/latest.json
rollup cron status
```

Manifests are local operational records (paths and folder names may be sensitive
on shared machines). They never store message bodies, subjects, or Message-IDs.

### Grouping looks wrong

```bash
rollup digest --grouping-report
rollup digest --no-grouping
```

v1 groups only `notification_stream` and `daily_editions`. Essays and long-form
messages always stay standalone.
