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

### Provider errors vs programming faults

Rollup degrades only named provider transport/payload failures, such as
`requests.RequestException`, malformed provider JSON, or Unicode decode errors
from provider payloads. Programming faults such as `TypeError` or
`AttributeError` are not converted into fallbacks; they hard-fail so the bug is
visible.

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

Each non-dry-run digest writes `state/manifests/<timestamp>-<run_id>.json`
(schema version **2**). Readers still accept schema **1**. Failure manifests are
written whenever `state_dir` is writable. Inspect:

```bash
cat state/manifests/latest.json
rollup cron status
```

Manifests are local operational records (paths and folder names may be sensitive
on shared machines). They never store message bodies, subjects, Message-IDs,
prompts, model responses, or patch text.

Structured logs follow the same privacy rule: they may record status, counters,
codes, and bounded exception messages, but not newsletter subject/body text,
prompts, model responses, or patch contents.

Schema v2 may include `final_review` (apply skip reason, reject counts by code,
auto-edited prose flag) and `group_summaries` (ollama_calls, cache hits, stream /
cache errors, degraded).

### Apply mode skipped every patch

Check the manifest `final_review.apply_global_skip_reason` and logs. Common codes:
`fingerprint_missing`, `fingerprint_mismatch`, `unsafe_to_publish`,
`unattended_patch_cap`, `unattended_char_cap`. Cron apply also requires
`--final-review-allow-cron-apply` and conservative policy.

Final-review apply binds patches to the digest fingerprint. Live model responses
may echo either `echoed_digest_fingerprint` or the schema alias
`digest_fingerprint`; cached results preserve only the model/cache echo and never
synthesise a missing echo from the host-computed fingerprint. Missing or
mismatched echoes skip the whole patch set.

### Group summaries degraded / exit 2

Partial exit with a usable digest usually means stream failures, cache read/write
errors, or all attempted group blurbs failed. Member entry summaries still render;
deterministic group headers remain when a blurb is omitted.

### Grouping looks wrong

```bash
rollup digest --grouping-report
rollup digest --no-grouping
```

v1 groups `notification_stream`, `daily_editions`, and `sender_batch`
(including an auto fallback to same-source batches). Essays and long-form
messages always stay standalone.

### LinkedIn fetch failed (401 / 429 / checkpoint)

`fromMember` searches use Voyager (`profileUpdatesV2`) with **your** LinkedIn
session. Set **both** cookies before `--linkedin` or `[linkedin].enabled = true`.
Never store them in TOML. See [CONFIG.md](CONFIG.md#linkedin-content-searches-optional).

```bash
export ROLLUP_LINKEDIN_LI_AT='…'            # DevTools → Cookies → li_at
export ROLLUP_LINKEDIN_JSESSIONID='ajax:…'  # same pane; JSESSIONID (quotes optional)
rollup digest --linkedin
```

- **Missing `ROLLUP_LINKEDIN_LI_AT`:** fetch refuses to start. Dry-run warns instead of calling the network.
- **Missing `ROLLUP_LINKEDIN_JSESSIONID`:** `fromMember` searches need it as Voyager CSRF (`csrf-token`). Copy it from the same cookie pane as `li_at`.
- **401 / session expired:** refresh **both** cookies from a logged-in browser. They rotate together; a new `li_at` with a stale `JSESSIONID` still 401s.
- **0 posts / all undated (older builds):** current Rollup dates posts from activity ids and applies lookback after fetch. If `Messages parsed` is 0, the session may be valid HTML-only (no Voyager) — confirm the log line `Fetching LinkedIn fromMember feed (N authors) via Voyager` and that the URL has `fromMember=`.
- **429:** rate limited — wait and retry. Mail-only digests may still publish as **partial** (exit 2).
- **Checkpoint / authwall:** complete LinkedIn’s verification in the browser, then export fresh cookies.
- **LinkedIn-only run (`--folder linkedin:…` and no mbox):** fetch failure is **exit 1**, not partial.
- **Dry-run / Settings GET:** never contacts LinkedIn.
- **Link-post still a short teaser:** article fetch is on by default. Confirm `[linkedin].article_fetch` is not `false` and you did not pass `--no-linkedin-article-fetch`. Voyager must expose `ArticleComponent` (`content.navigationContext.actionTarget`). Job posts and posts without a link card stay commentary-only. A failed article GET leaves the teaser and sets a parse warning (`linkedin_article_fetch_failed`, `linkedin_article_empty`, `linkedin_article_url_invalid`); it does not fail the digest.
- **How to copy cookies:** [EXAMPLES.md](EXAMPLES.md#2-copy-session-cookies-li_at-and-jsessionid).
