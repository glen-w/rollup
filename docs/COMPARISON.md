# How Rollup compares

A 2026 snapshot of the field around Rollup. Not a feature checklist, and not a
promise that other projects stay still.

Rollup is a **local-first personal briefing engine**. It reduces a chosen
information environment into a bounded reading object — without becoming the
store of record, and without locking you to a particular model.

RSS readers are built around “show me everything new from these sources.”
Rollup is built around “take a messy pile of things I’ve deliberately let into
my orbit and turn it into something finite and worth reading.”

| | RSS reader | Rollup |
|--|------------|--------|
| Pipeline | source → stream → user triages items | source → collection window → filtering / grouping / synthesis → finished briefing |
| Ongoing stream | the product | owned elsewhere (Thunderbird, the web, LinkedIn, Reddit) |
| Rollup’s job | — | take a time window and produce the rollup |

Adding RSS as a *reader* would pull Rollup toward unread counts, scrolling
feeds, folders, stars, sync, and “mark all read” — the territory of
Miniflux, FreshRSS, and Readeck. RSS as a silent *ingest transport* (fetch
the last seven days at digest time; never show an RSS inbox) is a different
question, and is parked on the [roadmap](ROADMAP.md).

In 2026 local-AI aggregation is no longer unusual. Rollup’s distinction is
the combination of:

- a **read-only existing mail store** (Thunderbird files; Rollup never becomes
  another mailbox)
- **durable source policy** (identities, mute, priority, type, grouping)
- **publication integrity** (staged artifacts, irreversible `latest.*` /
  seen-state, partial-failure exit codes)
- **multi-format digest output** (Markdown, HTML, EPUB, e-ink, JSON, TXT)

The pitch is not “the self-hosted AI newsletter reader”. That niche now has
competitors. The pitch is:

> Turn the sources you deliberately follow into a calm, high-quality periodic
> briefing, without surrendering the underlying data or your choice of inference.

Product shape: [CONTRACT.md](CONTRACT.md). Where this is headed: [ROADMAP.md](ROADMAP.md).

## Landscape

| Category | Examples | Relative to Rollup |
|----------|----------|--------------------|
| Consumer newsletter reader | [Meco](https://www.meco.app) | Meco is further ahead on mobile UX, highlights, notes, unsubscribe, discovery, and audio. Rollup wins on control, self-hosting, arbitrary model choice, and the posture that **your existing mail remains authoritative**. Meco is a dedicated reading environment (own address or connected Gmail/Outlook). Rollup never requires a special newsletter address, never moves messages into another service, and never becomes authoritative for subscriptions. |
| Traditional self-hosted reader | [FreshRSS](https://freshrss.org), [Miniflux](https://miniflux.app) | Built around a **stream**: unread counts, triage, tags, APIs. Mature at that job. Rollup must not become another feed reader. It has richer **digest construction** — classification, grouping, source policy, publication semantics — because the output is a finished briefing, not a queue. |
| Self-hosted read-later | [Readeck](https://readeck.org) | Ahead on effortless capture, highlighting, search, and e-reader / OPDS distribution. Rollup is an **active briefing pipeline**, not a capture-and-archive library. OPDS as a *destination* for the finished briefing is on the [roadmap](ROADMAP.md); a read-later inbox is not. |
| AI digest / briefing | [Cruxwire](https://cruxwire.app), [CondenseIt](https://github.com/vector0902/condenseit) | Closest philosophically: local or cloud LLMs, ranking, summarisation, browser digests. They are ahead on **semantic ranking, deduplication, and learned interests**. Rollup is ahead on mail-store fidelity, source policy, and publication integrity. Ranking in Rollup should change *attention*, not silently throw material away. |
| AI email digest | [SummerMail](https://github.com/wanleung/summermail) | Similar Ollama / LiteLLM + web dashboard idea, centred on general Gmail/IMAP and importance scoring rather than a highly controlled reading corpus. Rollup digests what you already filed; it does not try to score an inbox. |
| All-in-one reader | [Lion Reader](https://github.com/brendanlong/lion-reader) | Broader capture surface — RSS, newsletters, read-later, file import, APIs / MCP, PWA. Useful as a warning: Rollup should **not** become a universal reader. Output artifacts are the cheaper path to cross-device reading. |

## What Rollup is not competing on

- Mobile app polish, highlights, and audio (Meco)
- Being the best RSS host (FreshRSS, Miniflux)
- Being the best read-later / OPDS library (Readeck)
- Learned feed ranking as the primary product (Cruxwire, CondenseIt)
- Inbox importance scoring (SummerMail)
- Universal capture + MCP + PWA (Lion Reader)

Those are strong products. Rollup should stay a **briefing engine over a corpus
you already chose**. Thunderbird handles the ongoing newsletter stream;
Reddit, LinkedIn, and article capture supply additional material; Rollup does
not replace those places. It takes a time window and produces the rollup.

The interesting direction is not “support more feeds.” It is **cross-source
consolidation**, “what actually mattered this week?”, relevance, recurring
themes, redundancy, and “what can I safely skip?” See [ROADMAP.md](ROADMAP.md).

## Starting from Thunderbird

Starting from the existing Thunderbird store without becoming another mailbox is
still unusual. Thunderbird filters file mail into folders; Rollup digests those
folders. Optional LinkedIn, Reddit, and webpage sources are ingest transports
for the same window; they are not inboxes, and they do not replace that contract.

Gmail API ingestion is [post-1.0](ROADMAP.md): it would expand the audience, but
doing it before the briefing experience itself is distinct risks turning Rollup
into another generic inbox reader.
