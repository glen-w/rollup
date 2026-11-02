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

Run `rollup init`: it scans Thunderbird profiles, asks for the `.sbd` path when it
finds nothing, and writes the sticky config. Or point `--root` at your Thunderbird
`.sbd` tree directly, or use the synthetic fixtures:

```bash
rollup inventory --root tests/fixtures/Newsletters.sbd
```

### Writable path inside mail root

Move `--output-dir`, `--state-dir`, and `--log-dir` outside `--mail-root`.

### Another digest run is in progress

A lock is held at `state/rollup.lock`. Wait for the other run, or if the process
is dead, the next run recovers stale locks automatically. `rollup doctor`
shows the holder (`run_lock`: pid, operation, run id) and whether it is stale.

### Empty digest / everything skipped outside window

Fixture or live mail dates may be outside `--lookback-days`. Increase the
lookback or regenerate fixtures with `python tests/generate_fixtures.py`.

### Exit 2 with `partial: …` on stderr

The dated digest is usable but something degraded. The line names the causes
(`optional writer`, `latest publication`, `seen-state`, `manifest`, `final
review`, `group summaries`, `linkedin`, `reddit`, `webpage`, `gmail`, `web
index`); the manifest carries the same flags under `degradations` and the
ordered steps under `lifecycle_events`. Optional destination writers (`opds`,
`calibre`) never block Markdown/HTML. Exit-code matrix:
[CRON.md](CRON.md#exit-codes).

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
auto-edited prose flag), `group_summaries` (ollama_calls, cache hits, stream /
cache errors, degraded), `degradations` (why a run was partial), and
`lifecycle_events` (ordered publication steps).

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
- **Listing cache still showing old posts:** LinkedIn listings and article bodies persist in `rollup.db` for `[linkedin].fetch_ttl_hours` (default 24). Pass `--linkedin-refresh`, or set `fetch_ttl_hours = 0`, to force a live pull. A fetch failure may reuse a stale snapshot (`linkedin_cache_stale`).
- **LinkedIn-only run (`--folder linkedin:…` and no mbox):** fetch failure is **exit 1**, not partial.
- **Dry-run / Settings GET:** never contacts LinkedIn.
- **Link-post still a short teaser:** article fetch is on by default. Confirm `[linkedin].article_fetch` is not `false` and you did not pass `--no-linkedin-article-fetch`. Voyager must expose `ArticleComponent` (`content.navigationContext.actionTarget`). Job posts and posts without a link card stay commentary-only. A failed article GET leaves the teaser and sets a parse warning (`linkedin_article_fetch_failed`, `linkedin_article_empty`, `linkedin_article_url_invalid`); it does not fail the digest.
- **How to copy cookies:** [EXAMPLES.md](EXAMPLES.md#2-copy-session-cookies-li_at-and-jsessionid).

### Gmail: not ready / fetch failed

Gmail ingest is opt-in (`[gmail].enabled` or `--gmail`) and read-only. Start with
`rollup gmail status` (offline) and `rollup doctor` (`gmail_auth` check).

- **`credentials: missing`:** set `ROLLUP_GMAIL_CLIENT_ID` / `ROLLUP_GMAIL_CLIENT_SECRET` in `~/.config/rollup/env` (Google Cloud OAuth client, type *Desktop app*, Gmail API enabled). Never TOML.
- **`token: missing`:** run `rollup gmail auth` once (`--no-browser` to print the URL; `--manual` over SSH / in containers to paste the redirect URL). The token lands in `{state_dir}/gmail_token.json`; use the same `--state-dir` as your digests.
- **`scope is not gmail.readonly`:** the grant included another scope. Revoke Rollup at `myaccount.google.com/permissions` and re-run `auth`. Rollup refuses to use any token wider than read-only.
- **No refresh token returned:** also fixed by revoking and re-authorising (Google only issues one on a fresh consent).
- **`Gmail label not found`:** the label name in `[gmail].labels` / `label_prefix` does not exist. `rollup gmail labels` lists what the account has and marks selected ones.
- **401 after it used to work:** the refresh token was revoked or expired; re-run `rollup gmail auth`.
- **429 / 5xx:** Rollup backs off and retries a few times, then reuses the stale label snapshot (`gmail_cache_stale`) or marks the run partial (`gmail_fetch_failed`, exit 2). Gmail-only runs with nothing fetched are exit 1.
- **Old messages keep appearing / new ones missing:** labels are served from cache for `fetch_ttl_hours` (default 6). Pass `--gmail-refresh` or set the TTL to `0`. A TTL expiry with a stored history id uses `history.list` instead of a full list. `gmail_history_expired` means that cursor was too old and Rollup fell back to `messages.list`.
- **Kindle send failed:** the dated digest can still be success-with-partial (`partial: optional writer`). Set `[kindle].to`, `[kindle].from`, and `ROLLUP_KINDLE_SMTP_HOST` / `ROLLUP_KINDLE_SMTP_PASSWORD`. Doctor check `kindle_smtp` does not print the password.

### RSS fetch failed

RSS ingest is opt-in (`[rss].enabled` or `--rss`). `rollup doctor` reports `rss_feeds`.

- **`rss_url_ssrf` / `rss_url_invalid`:** the feed URL or a redirect is not public HTTPS (private, loopback, link-local, or credentials in the URL).
- **`rss_doctype_rejected`:** the document declares a DTD or entity. Rollup will not parse it.
- **429 / timeout:** a stale snapshot is reused (`rss_cache_stale`, exit 2) when one exists. With no snapshot the run is `rss_fetch_failed`. RSS-only runs with nothing parsed are exit 1.
- **`partial: continuity ledger`:** the briefing published, but saving cross-run labels failed. The next run simply has a shorter memory.
- **Large newsletters skipped:** raw messages over 2 MB are not fetched (logged as a warning).
- **Dry-run / Settings GET:** never contacts Gmail.

### Reddit fetch failed (429 / missing sub)

Rollup fetches public subreddit listings via a transport ladder: **OAuth JSON** (when env credentials are set) → **public JSON** → **www RSS** → **old.reddit RSS**. Unauthenticated feeds are often rate-limited (~1 request per minute per IP).

```bash
rollup digest --reddit --lookback-days 7
```

- **429 / empty body:** rate limited or bot wall — wait and retry. Mail-only digests may still publish as **partial** (exit 2). Rollup retries 429s once per sub across the ladder, then retries failed subs once more after a longer backoff. The CLI prints a planned wait (`70s` between subs) at fetch start and remaining time per sub; HTTP 429s add extra wait beyond that estimate.
- **404:** subreddit does not exist or name is wrong — not retried. Check `/reddit` or `[reddit.subs.*]` in TOML.
- **Subs whose names start with `r`:** older builds mangled names like `raspberry_pi` → `aspberry_pi` (bad `lstrip("r/")`). Upgrade fixes this; if TOML has `[reddit.subs.aspberry_pi]`, rename to `raspberry_pi`.
- **Stale listing / skipped RSS:** default `fetch_ttl_hours = 24` reuses the last snapshot. Use `--reddit-refresh` or set `[reddit].fetch_ttl_hours = 0`.
- **Optional OAuth (script app):** set in the environment (never TOML): `ROLLUP_REDDIT_CLIENT_ID`, `ROLLUP_REDDIT_CLIENT_SECRET`, `ROLLUP_REDDIT_USERNAME`, `ROLLUP_REDDIT_PASSWORD`. When all four are set, Rollup uses `oauth.reddit.com` JSON first. Same `~/.config/rollup/env` file as LinkedIn cookies.
- **Reddit-only run (`--folder reddit:feed` and no mbox):** fetch failure is **exit 1**, not partial.
- **Dry-run / Settings GET:** never contacts Reddit.
