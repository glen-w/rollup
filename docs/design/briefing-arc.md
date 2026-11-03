# Design note: briefing layers toward 1.0

**Status:** contracts, attention, coverage, synthesis, corpus FTS/Ask, source
intelligence, and reading destinations (OPDS / Calibre drop / Kindle) have
landed. Not a commitment calendar. The sequence that thickens these layers —
1.0 bar, then the edition arcs — is [the roadmap](../ROADMAP.md). This note
holds the invariants.

Rollup reduces a collection window into a **bounded reading object**. These
layers deepen that briefing. They do not add another stream to triage.

## Invariants

- **Inclusion.** Attention, coverage clusters, and synthesis never drop a
  message that passed filter. Ranking changes order, chrome, and collapse —
  not membership. `rollup.briefing.report_included_message_keys` is the
  contract helper.
- **Offline-first.** Default `rollup digest` makes no network calls. Heuristics
  always run; LLM refine/narrative/Ask require `--ollama` / LiteLLM, same
  posture as group summaries.
- **Folder geography.** Thunderbird folders remain sections. New chrome is a
  Start here lead (high-relevance, by reference), within-folder sort by
  attention band then date, and safely-skip collapsed by default in HTML.
- **Grouping stays same-source.** Coverage is an overlay (`CoverageCluster` on
  the report), not a new `GroupType`.
- **No threshold knobs.** Bands and overlap cutoffs are documented constants,
  not TOML. CONTRACT forbids exposing classifier thresholds as user knobs.
- **Interest profile is prose.** `[interest].profile` is a short paragraph,
  not a weight vector. Empty profile falls back to Quality / interaction /
  source-priority; if those are empty too, every item is `worth_scanning`.
- **Reader bodies stay a convenience cache.** FTS indexes that cache plus
  titles and summaries. It is not a second mailbox.

## Attention bands

Fixed labels (not user-tunable):

| Band | Role |
|------|------|
| `high_relevance` | Start here |
| `worth_scanning` | Default when signals are empty |
| `background` | Keep, lower in the folder |
| `safely_skip` | Present; HTML `<details>` closed by default |

## Pipeline slots

```
filter → group → annotate_attention → cluster_coverage → continuity
      → summarize → group_summaries → synthesize_briefing
      → final_review → render
```

Search / Ask sit outside the digest pipeline (CLI + loopback). Source
suggestions sit on Quality / Registry / `rollup sources suggest`.

Final-review remains editorial QA. Briefing synthesis is a separate
“what actually mattered” layer.

## Schema

Waves 1–4 and 6 are in-memory + TOML + manifest. Corpus FTS is schema **v17**.
Suggestions are computed live from Quality + registry — no suggestion table.

Manifest may carry optional `attention`, `coverage`, and `briefing_synthesis`
blocks. Reader bodies never appear in manifests.

## Non-goals (this arc)

- Richer LinkedIn (beyond the existing `fromMember` ingest)
- Embeddings / vector DB
- Auto-unsubscribe or mutating Thunderbird
- Dropping mail from the briefing
