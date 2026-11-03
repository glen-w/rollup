# Roadmap

Where Rollup is headed, relative to the [product contract](CONTRACT.md) and
[competitive position](COMPARISON.md). Guidance for contributors, not a
commitment calendar. If this document and the contract ever disagree, the
contract wins and this file should be revised.

Rollup reduces a chosen information environment into a **bounded reading
object**. The next work thickens that object. It does not add another stream
to triage.

## The bet

A weekly (or daily) rollup should feel like an edition: a first page you can
finish, then the folders you already filed, in full, underneath. The first
page is allowed to be opinionated about attention, redundancy, and skip-safely.
The folders are the record. Both ship in the same artifact. Ranking never
quietly deletes the record.

Thunderbird owns the ongoing newsletter stream. Gmail, when used, is a
read-only view of labels you already file into. Reddit, LinkedIn, and article
capture show up at digest time and then leave. Rollup's job is the window.

The layers that make an edition — attention, coverage, glance, corpus search,
source suggestions, reading destinations — landed in 0.10 and 0.11. They are
real and they are still thin. A glance that reprints subjects, a cluster named
after a hostname, a suggestion that cannot see cadence, and a search hit that
cannot open the edition it came from are the same product, unfinished. 1.0
declares that stack the product. The sequence after 1.0 makes the edition
worth trusting for a year of Sundays.

## A test for new work

A change belongs on this roadmap when all four answers are yes:

1. It makes **this window's briefing** more worth finishing.
2. Inclusion still holds. Attention, coverage, synthesis, and search change
   order, chrome, and collapse. They do not change membership.
   `rollup.briefing.report_included_message_keys` stays the contract helper.
3. Default `rollup digest` still makes no network calls. Models stay opt-in.
4. It does not grow an inbox, a library, a second mailbox, or a knob for a
   threshold the contract keeps fixed.

Work that needs a contract revision says so in the proposal, before code.
Parked transports further down are shaped in advance so they cannot drift
into readers while they wait.

Invariants for the briefing layers live in
[design/briefing-arc.md](design/briefing-arc.md). This roadmap sequences the
thickening; that note holds the rules.

## The edition, layer by layer

What exists today, and the thinness the post-1.0 arcs are aimed at.

| Layer | Landed | Still thin |
|-------|--------|------------|
| Window | Weekly and daily profiles, lookback, folder selection, empty-window success vs no-input failure | A thin week and a rich week look like the same kind of object with different lengths. The edition does not yet know it is a sequel |
| Classification & grouping | Types, same-source groups, group blurbs for `notification_stream` and `daily_editions` only | Group blurbs never replace member summaries (keep that). They also never meet the glance |
| Attention | `[interest].profile` prose, Quality, saved / dismissed / read, source priority. Bands: high relevance / worth scanning / background / safely skip. Optional `--ollama` reband | Score is lexical Jaccard plus those signals. `source_cadence` already estimates realtime / daily / weekly / irregular and is unused here. Empty signals yield `worth_scanning` for everything, which is honest and flat |
| Coverage | URL and title overlap (`TITLE_JACCARD_MIN` 0.45), at most 24 clusters, “Covered N ways” on the card. `--no-coverage` / `--coverage-report` | Theme labels prefer the most common link host, so the reader sees `nytimes.com` more often than the story. Syndicated pieces with rewritten headlines and different tracking URLs stay apart. Cards correctly stay in their folders |
| Glance & synthesis | Deterministic glance always (up to five high-relevance subjects, three cluster lines, a folder count). `--briefing-synthesis` opt-in JSON narrative | The narrative is uncited. It sees titles and clusters, not a discipline that drops invented lines. `what_to_skip` is not bound to the safely-skip band. Final review remains a separate QA pass; keep it separate |
| Corpus | Schema v17 FTS5 over capped reader bodies, titles, and summaries. `rollup search`, grounded `rollup ask` | Ask is one shot and refuses when nothing matches (`No matching items in the Rollup cache.`). Hits do not open the edition that contained them. A title-only cache looks like a searched corpus. No conversation memory — and none is wanted |
| Source intelligence | Five live codes: unused eight weeks, mostly dismissed, overlapping coverage, consider lowering priority, mostly duplicate. Apply is `set_overrides` only | Cadence mismatch and “this letter changed character” are invisible. Suggestions cannot be snoozed, so the same card returns every run until you comply or live with it |
| Destinations | OPDS acquisition feed for **this** briefing, loopback `GET /opds` catalog of editions, Calibre drop folder, Send-to-Kindle (`[kindle]`, SMTP secrets in the environment). Optional writers; failure is partial | E-ink / TXT / EPUB can drop the first-page structure (glance, skip) without a golden catching it. OPDS must stay a catalog of editions |
| Gmail | Opt-in `gmail.readonly`, labels → folders, same parser as mbox, raw cache with TTL, stale fallback. Expired TTL uses GET `history.list` plus bounded GET `messages.get`; expired history ids fall back to a full list | Scope stays readonly and GET-only. Efficiency only |
| Capture | Firefox Add to Rollup → `webpage_queue` while `rollup web` is up. Ingest at digest time | A closed laptop loses the capture. The queue is a tray for the next edition, not a library; keep it that way while making the tray survive the web UI being down |
| Scholar | Default mode: alerts as `item_list` mail. Detailed mode: landing pages, schema v16 cache, caps (8 papers/email, 40 fetches/run) | Enrichment of mail already in the window. Not an ingest transport and not a reference manager |
| Publication | Staged rename, fsync, irreversible `latest.*` / seen-state, manifest `degradations` / `lifecycle_events`, exit 2, fault-injection tests | The colophon stops at the CLI and the manifest. The HTML/Markdown a person actually reads does not yet say “Reddit section is a stale snapshot” |
| Setup | `rollup init`, doctor, Docker Compose, CI smoke, render goldens, schema lineage through v19 | CI is install + version + fixture doctor + offline preview digest. Restore ruff, mypy, coverage, the Python matrix, and `compose-preview` only after `make check` stays green on main |

## 1.0 — leave it running

Tagged **1.0.0** on 2026-11-03. 1.0 is not “the layers got deeper.” Depth is
the arc after this. 1.0 is the cut where a person can run `rollup init`, point
cron at a weekly profile, and trust the result for a year without Rollup
becoming their mailbox.

Preview mode (no `--ollama`, no network) is a complete product at that cut:
glance, folder sections, attention chrome, coverage notes, Markdown and HTML.
Models, Reddit, LinkedIn, webpages, Gmail, Scholar detailed mode, and
destination writers are opt-in arms on that product.

**Done when**

- Init, doctor, and a weekly cron line are the documented first-run story, and
  a fixture digest proves the preview edition offline.
- A partial run is legible in the artifact, not only in the process. Core
  Markdown and HTML carry a short colophon when degradations are present
  (stale Reddit or LinkedIn, stale Gmail, optional writer failed,
  `archive_failed`), in words, mapped from the manifest codes. Required
  publication still happens first. The colophon is part of the golden.
- An empty window stays exit 0, refuses `latest.*`, and is visibly an empty
  window wherever the UI would otherwise present the previous edition as fresh.
- Goldens, schema lineage, publication fault-injection, and the web route
  invariants stay green on the release.
- README remains the entry, `docs/` the reference, the contract short. No new
  source type ships in the 1.0 cut.

Gmail incremental sync and Send-to-Kindle are explicitly allowed to slip past
this cut. They are efficiency and a destination, and neither is required to
leave the weekly edition running.

## Restore the CI quality gate

Tagged **1.0.1** on 2026-11-03 after the 1.0.0 push failed the full gate
(`ruff`, `mypy`, `pytest --cov-fail-under=80` on Python 3.10–3.13; six
final-review tests). GitHub Actions is a smoke until that suite is
consecutively green: install, `rollup --version`, fixture `doctor`, offline
preview digest. `make check` and `./scripts/ci-compose-preview.sh` stay the
local commands.

**Done when**

- `make check` is green on consecutive runs on main.
- Restore the 0.11 jobs as required: ruff, mypy, coverage gate, the 3.10–3.13
  matrix, advisory `pip-audit`, and `compose-preview`. Do not grow the gate
  until that suite stays green.

**Out of this item:** using CI as a place to add new product surface. The
smoke exists so main can ship; expansion is the old gate, not a new one.

## After 1.0 — thicken the edition

Order is dependency, not a quarter plan. An arc can start when the one before
it has a `Done when` that is true in tree, not when a date arrives. Each arc
names the seam it attaches to so it cannot become a new subsystem.

### 1. A glance you can trust

The front page is the product's voice. Today the deterministic glance
reprints subjects, and the opt-in narrative may say things the edition cannot
back.

**Attaches to:** `rollup.synthesis`, the glance block in `rollup.render`,
Ask's citation rule in `rollup.ask`.

**Done when**

- Model bullets that ship cite `message_key`s present in this edition.
  Uncited claims are dropped before render. The published narrative can be
  audited against the pile.
- `what_to_skip` may only name entries already banded `safely_skip`, or whole
  clusters. It cannot invent a skip list the bands disagree with.
- The deterministic glance always renders, labelled as the offline glance.
  The model block, when present, is labelled as a narrative. Final review
  stays editorial QA and does not absorb either.
- A window with no interest profile, no quality, and no saves still gets an
  honest structural glance (counts, coverage, folders). It does not get a
  model pretending something “mattered.”
- Group blurbs for notification streams and daily editions may be quoted by
  the glance. They still never replace member summaries.

**Out of this arc:** embeddings, a conversational editor, user-written
headlines, dropping low-band mail to make the front page short. Length is
chrome (collapse, “start here”), not deletion.

### 2. Continuity across editions

A briefing is a sequel. The previous published manifest is the memory: what
recurred, what is new, which sources went quiet. Items outside the window do
not come back.

**Attaches to:** manifest `attention` / `coverage` / `briefing_synthesis`
blocks and the run index. Not the reader-body cache.

**Done when**

- The edition can render a short continuity note drawn only from the previous
  **published** manifest: recurring coverage themes, clusters with no prior
  counterpart, sources present last time and absent this time.
- No previous manifest yields “no continuity,” not exit 2.
- Nothing outside the current window is re-included. Seen-state is not an
  unread counter and this note must not become one.
- Continuity is deterministic and offline. A model may phrase it later; the
  facts come from manifests.

**Out of this arc:** diffing full bodies, “you have 40 unread since Tuesday,”
carrying dismissed items forward as a todo list.

### 3. Attention that knows the cadence

`estimate_cadence` already labels a source realtime, daily, several-per-week,
weekly, or irregular from dated samples. The attention score never consults
it. In a weekly edition a daily firehose and a weekly letter are different
kinds of event; the bands should be able to say so without a new knob.

**Attaches to:** `rollup.attention.score` and `rollup.source_cadence`. Reasons
already travel with the score; cadence becomes one more reason.

**Done when**

- Under the weekly profile, a high-volume daily or realtime source leans
  `background` unless the interest prose, a save, a high priority, or
  always-surface pulls it up. A weekly source that actually published inside
  the window leans `worth_scanning` or better when other signals agree.
- The daily profile does not punish daily sources for being daily.
- The four band names stay fixed. No TOML threshold, no weight vector. The
  interest profile stays a paragraph.
- Inclusion is unchanged. LLM reband remains optional and may not expand or
  shrink the included set. The cadence effect works with `--no-ollama`.

**Out of this arc:** learned ranking, per-topic weights, exposing Jaccard
cutoffs.

### 4. Coverage that survives syndication

The same story arrives as a newsletter blurb, a Reddit post, and a captured
article, with different tracking URLs and a headline that gained a prefix
(`TLDR:`, `Issue 412 —`). URL-or-title union-find misses it, and the cluster
it does form is often named after a host.

**Attaches to:** `rollup.coverage` (canonical URL, title tokens, union-find).
Still an overlay (`CoverageCluster` on the report), never a new `GroupType`.

**Done when**

- Shared outbound links inside included items can join a cluster, not only
  the item's own URL.
- Stripping issue prefixes and trailer counts is a normalizer in front of
  Jaccard, with tests that a shared prefix alone does **not** cluster, and
  that a rewritten headline with the same distinctive tokens does.
- The theme label is a short phrase from shared title tokens. The host is
  the fallback when tokens are too thin.
- “Covered N ways” stays on the card, in the folder the message already
  belongs to. Cap remains a documented constant (today 24), not a user knob.
- Behaviour stays lexical and deterministic. A report flag can still disable
  it.

**Out of this arc:** embedding similarity. That is an open question with a
high bar, below, not a silent upgrade of this arc.

### 5. The corpus indexes editions

Search and Ask are how you question the pile after the edition is filed.
The cache is a convenience, capped, and evictable. The durable reading
objects are the published digests. The index should admit which of those
two you are touching.

**Attaches to:** `message_corpus_fts`, `rollup search` / `GET /search`,
`rollup ask` / `POST /ask`, archive anchors (`#entry-…`).

**Done when**

- A hit that belongs to a published digest deep-links to that edition's
  entry anchor when the file is still in the archive. A hit with only a
  cached body says so.
- Doctor (and the search surface) report body coverage for the recent
  window: how many included messages have a reader body versus title or
  summary only. A thin cache is a visible fact.
- Ask accepts a bound: this run, the last N published runs, or a date span.
  Still one question, still excerpts, still a refusal when nothing matches,
  still citations limited to returned keys.
- No transcript table, no multi-turn memory, no vector index.

**Out of this arc:** turning reader bodies into a mailbox archive, raising
the cap until SQLite is a second store of record, chatbot UI.

### 6. Source review, as a ritual

Suggestions are a season of reading, not an automation. The five codes are
the start of a review you can do and dismiss. Unsubscribe, label edits, and
filter rules stay in Thunderbird or Gmail.

**Attaches to:** `rollup.sources suggest`, Quality and Registry cards,
`source_cadence`, the interest profile. Apply remains `set_overrides`.

**Done when**

- New live codes, still computed, never stored as truth: cadence mismatch
  (realtime source inside a weekly profile), profile drift (recent subjects
  diverged from the interest paragraph), and paired overlap that names which
  of the two sources you actually save.
- Every card shows the counts that produced it (dismissed N of M, shared
  stories K, unused since a date).
- A suggestion can be snoozed for a number of runs. Snooze is its own small
  state. It expires back into visibility. It never expires into an override.
- Apply still means disable or lower priority, explicitly. No path writes
  the mail root, calls an unsubscribe endpoint, or mutates Gmail.

**Out of this arc:** auto-disable after N weeks, a workflow engine, “clean
up my subscriptions.”

### 7. The edition leaves the machine

Cross-device reading is an artifact problem. OPDS and Calibre already carry
the finished edition. The hole on the seam is Kindle, and the risk on the
seam is someone turning OPDS into an item inbox.

**Attaches to:** `OutputWriter` optional classification, `rollup.addons.opds`,
`rollup.addons.calibre`, the xteink / txt / epub offline renderers.

**Done when**

- Send-to-Kindle is an optional writer. It sends the finished edition (EPUB
  or the HTML briefing of this run), never a single article and never the
  queue. Credentials live in the environment, as with every other secret.
  Failure is `optional_writer_failed`, exit 2, after required artifacts have
  published.
- `GET /opds` lists editions. It does not list items, folders, stars, or
  unread. Acquisition links point at the briefing's own files.
- Glance and safely-skip structure survive xteink, txt, and epub. A golden
  or writer test fails if the first page disappears in those formats.

**Out of this arc:** a mobile app, a read-later sync protocol, highlighting,
audio playback.

### 8. Ingest efficiency, and a tray that survives

Two transports are already in the product and slightly unfinished.
Neither arc is allowed to widen what they ingest.

**Gmail.** `history_id` is already persisted on `gmail_label_sync`. The
client still walks labels and calls `messages.get`.

**Done when**

- A run with a live history id fetches the delta via `history.list` and
  batched `messages.get`. A missing or expired history id falls back to the
  current full-label snapshot. TTL, stale fallback, zlib raw cache, and the
  120-day raw retention stay.
- Scope remains `gmail.readonly` and GET-only. Doctor's `gmail_auth` check
  still tells you when the token is dead. Parser output for the same RFC 822
  bytes matches the mbox path (identity already dedupes across the two).

**Capture.** The Firefox add-on posts into `webpage_queue` only while
`rollup web` is running.

**Done when**

- The extension holds URLs locally when the loopback UI is down and flushes
  them into the queue later. Digest time is still when fetch and inclusion
  happen.
- The articles surface stays a tray: waiting, ingested, failed. It does not
  grow highlights, tags, or a full-text archive of the open web.

**Out of this arc:** IMAP, a Thunderbird XPI, background polling between
digests, a hosted capture service.

## Parked transports

These expand surface. They wait until arcs 1–4 have landed, so a new kind of
material joins an edition that already knows how to rank, cluster, and
introduce it. Each is specified here so a partial implementation cannot grow
reader chrome “just for now.”

### RSS as an ingest transport

**Shipped** in 0.12 as `[rss]` / `--rss` / `GET /rss`. Same slot as LinkedIn and Reddit: include this publication in the window. Not an inbox.

Shape:

- Config is a list of feed URLs plus `fetch_ttl_hours`, defaulting to the
  same TTL posture as Reddit. Fetch runs at digest time. There is no
  scheduler inside Rollup and no poll between runs.
- Payloads persist in `rollup.db`. A failed fetch reuses a stale snapshot
  and records a degradation, matching Reddit / LinkedIn / Gmail.
- Items become ordinary digest entries in a synthetic folder per feed (or
  one configured folder). They participate in attention, coverage, grouping,
  and the glance. Lookback clips them. They do not accumulate unread state.
- Coverage is how a feed story and a newsletter story become “Covered N
  ways.” The feed does not get its own reading UI.
- The web page for this source looks like the Reddit source page: a list of
  feeds, a TTL, a refresh flag. It does not look like a river of posts.

An RSS **reader** — unread counts, stars, mark-all-read, sync, folders you
triage — stays a non-goal. That product is Miniflux, FreshRSS, and Readeck.

### Richer LinkedIn sources

v1 stays author-list URLs mapped through Voyager `profileUpdatesV2`. The
fetch module stays isolated because the HTML and the private API move.

Unpark only when a real query works again, in this order: keyword search if
LinkedIn exposes a working content-search request; company or org posts;
follows and mentions. Each one is still a named source, cached, TTL'd, and
folded into the window. None of them is a LinkedIn client.

### Scholar stays an enrichment

Detailed mode fetches paper landing pages for alerts already inside the
window, caches them (schema v16), and summarises per paper. Caps stay.
PDFs stay skipped.

Do not turn this into a reference manager, a PDF library, or a bibliographic
database. A paper is an entry in the edition. The mailbox remains the reason
it was in the window.

## Standing rules

- Network sources are **ingest transports** for the collection window.
  Persist fetched payloads in `rollup.db`. Skip the network when a
  fresh-enough snapshot exists. On failure, prefer the stale snapshot plus
  a typed degradation over an empty section that looks like the source went
  quiet.
- A new network source ships with a cache, a TTL, a stale fallback, a doctor
  line, and a `--refresh` flag, or it does not ship.
- Secrets stay in the environment or in `state_dir` token files. They never
  live in TOML or the web UI.
- The web UI stays loopback and single-user. GET routes stay read-only.
- Publication stays staged + rename. `latest.*` and seen-state cross the
  irreversible boundary only after required artifacts land. Optional
  destination writers fail as exit 2.
- Reader bodies stay a capped convenience cache.
- Classifier and overlap thresholds stay documented constants. The contract
  forbids exposing them as user knobs.
- Engineering splits (migration bodies, `classify_link`, unused forward-compat
  tables such as `group_summary_generations`) happen when a feature forces
  the edit. They are not roadmap items of their own. `render`, `state`, and
  source-registry listing are already split.

## Non-goals

These stay out because building them would change the product into one of
the neighbours in [COMPARISON.md](COMPARISON.md). Existing restraint is part
of why the edition is legible. Do not “just add a small version.”

| Kept out | What it would turn Rollup into |
|----------|--------------------------------|
| IMAP / Maildir backends | Another mail sync. Gmail API already covers the non-local case as a readonly label reader |
| Thunderbird add-on (XPI) | A mail-client feature. Firefox capture is a tray into the queue; Thunderbird's job is filing |
| Multi-user or non-loopback web UI | An account system. Copy the output directory if someone else should read the edition |
| In-app digest scheduler | A daemon with opinions. Run Studio stays a synchronous guided runner. Cron and launchd are the scheduler ([CRON.md](CRON.md)) |
| Exposing classifier or overlap thresholds | A tuning product. Bands and cutoffs stay constants |
| Built-in workflow engine, generic plugin-everything | An automation host. The writer seam is for artifacts of one edition |
| Richer mail-client operations (send, mark read, move, unsubscribe, edit filters) | A mail client. Source policy is local SQLite overrides only |
| Official mobile app, highlights, audio player | Meco. Artifacts (EPUB, OPDS, Kindle-when-it-exists) are the cross-device path |
| RSS reader chrome | Miniflux / FreshRSS / Readeck. RSS ingest is a window fetch, not reader chrome |
| Learned ranking as the primary product | Cruxwire / CondenseIt. Lexical bands, cadence, and an explicit profile are the ranking Rollup is willing to stand behind |
| Inbox importance scoring | SummerMail. Rollup digests what you already filed |
| Universal capture, MCP, PWA | Lion Reader |
| Embeddings / vector DB as a quiet dependency | A second store and a non-deterministic inclusion-adjacent layer. See open questions |
| Auto-unsubscribe, auto-disable, mutating Thunderbird or Gmail | A robot with the keys to your subscriptions |

## Open questions

Reopen only under the pressure named here. Otherwise they stay shut.

- **Semantic coverage.** Reopen only after arc 4 has been watched on real
  editions and still misses stories a reader would call the same. A proposal
  would amend the contract in the same change: local embeddings, default
  digest still offline, inclusion still exact, no vector store as a system
  of record, clusters still an overlay. “We might want embeddings later” is
  not that proposal.
- **Reading position inside one edition.** A long HTML or e-ink file loses
  your place. A local mark, discarded when the next edition publishes, would
  be position inside one bounded object. The risk is a read-state product
  with sync and badges. Default is no until a single edition is genuinely
  hard to finish in one sitting and the colophon-and-collapse work has
  already shortened the front page.
- **Thunderbird filter discovery during init.** A read-only hint (“these
  folders look like filters”) could make setup faster. It must not write
  filters or become a second source of filing truth. Pressure: people who
  bounce off `rollup init` because they do not know which `.sbd` they mean.
- **Admin history when manifests are missing.** Deeper failure history is
  welcome. The incomplete-history disclaimer stays until the history is
  actually complete. Do not invent runs the manifest directory cannot prove.
- **Remote models.** `--allow-remote-ollama` stays explicit. LiteLLM via
  `rollup[llm]` and `--llm-provider litellm` stays env-keyed. No hosted
  default, no key in TOML.
- **LinkedIn fragility.** Expected. Keep the blast radius inside
  `rollup.linkedin`. A breakage is a stale cache plus a doctor line, not a
  rewrite of the digest.
- **Household reading.** Two people, one mail store, different profiles is
  a contract change (multi-user). Until then the sharing story is: run two
  state directories, or hand someone the published files.
- **An audio edition.** A writer that emits a script of the glance is an
  artifact. A player, a voice subscription, or a queue of articles to hear
  is a different product.

## Shipped (so this file stays a map, not only a wish)

Kept brief on purpose. Detail lives in [CHANGELOG.md](../CHANGELOG.md).

- Sticky TOML, run profiles (`weekly`, `daily`), effort presets, folder themes ([CONFIG.md](CONFIG.md))
- Writer seam: xteink, txt, json, epub, opds, calibre ([OUTPUT_WRITERS.md](OUTPUT_WRITERS.md))
- Loopback web UI, Configuration Centre, Run Studio, Home dashboard ([WEB.md](WEB.md))
- `sticky_flags`, `cli_parser`, `rollup.pipeline.run_digest`, `rollup.state` through schema v18
- LinkedIn `fromMember`, Reddit subreddits, webpage queue, listing caches (v15), Firefox capture
- Scholar detailed mode (v16) as mail enrichment
- Attention, coverage overlay, briefing glance / opt-in narrative (0.10)
- Corpus FTS and grounded Ask (v17)
- Source suggestions; OPDS and Calibre; publication telemetry; Gmail readonly ingest (v18)
- 0.11 hardening: `rollup init`, CI gate, render goldens, publication fault injection, schema lineage, `rollup.render` package split, `archive_failed`

## Related docs

| Doc | Role |
|-----|------|
| [CONTRACT.md](CONTRACT.md) | Product shape and publication integrity. Wins over this file |
| [COMPARISON.md](COMPARISON.md) | Who the neighbours are, and why their features stay there |
| [design/briefing-arc.md](design/briefing-arc.md) | Inclusion, offline-first, folder geography, fixed bands |
| [CONFIG.md](CONFIG.md) | TOML, profiles, sticky ↔ CLI |
| [WEB.md](WEB.md) | Loopback UI surfaces |
| [DOCKER.md](DOCKER.md) | Default Compose deploy |
| [EXAMPLES.md](EXAMPLES.md) | Runnable recipes |
| [CRON.md](CRON.md) | Exit codes, launchd, crontab |
| [SOURCES.md](SOURCES.md) | Registry identity and overrides |
| [OUTPUT_WRITERS.md](OUTPUT_WRITERS.md) | Artifact writers and optional vs required |
| [CHANGELOG.md](../CHANGELOG.md) | What shipped |
| [design/firefox-capture.md](design/firefox-capture.md) | Firefox tray: packages, reload, review |
