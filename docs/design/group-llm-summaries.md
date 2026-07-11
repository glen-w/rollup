# Design note: group-level LLM summaries (follow-on)

**Status:** deferred from Phase 2 acceptance.

Phase 2 ships deterministic grouping and grouped Markdown/HTML rendering using
existing per-entry preview / Ollama summaries. Group-level synthesis is optional
future work.

## Proposed approach (not implemented)

1. Assess extending `summary_generations` with a `scope` column (`entry` | `group`)
   before adding a separate `group_summaries` table.
2. Add `prompts/group_summary.txt` constrained to provided entry excerpts.
3. Cache by group identity + member content hashes + profile.
4. Treat group summary as an **additional** layer — never replace entry summaries
   unless explicitly configured.
5. Share Ollama stream guardrails; document call-budget policy separately.

## Acceptance deferred until

- Deterministic grouping is stable in weekly personal use
- Cache schema extension is designed and migration-tested
- Prompt quality is validated on notification_stream fixtures
