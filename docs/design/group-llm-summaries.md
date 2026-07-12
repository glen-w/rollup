# Design note: group-level LLM summaries

**Status:** shipped in 0.4.0; hardened in 0.4.1.

## Behaviour (enforced)

- Opt-in via `--group-summaries` (requires `--ollama` and grouping enabled).
- Eligible groups: `notification_stream` and `daily_editions` only, subject to
  min size and min usable member summaries.
- Group blurbs are an **additional** layer; member entry summaries are never replaced.
- Runtime cache is the flat table `group_summary_by_key` (schema v6).
- Calls use shared `consume_ollama_stream` with group-specific output/timeout caps.
- `max_group_summary_calls` bounds **network attempts** (including retries); cache
  hits do not consume the budget.
- Cache write failures still render the summary and mark the run degraded.
- Unsupported `group_summary_variant_policy` values other than `primary` are rejected
  at validation time.

## Non-goals (unchanged)

- Embedding / LLM clustering for groups
- Undated grouping expansion / undated group blurbs
- Writing the unused rich `group_summary_generations` table (created additively for
  forward compatibility; not used at runtime)
- `each` / `shared-identical` variant policies
