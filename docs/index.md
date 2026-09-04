# Rollup documentation

Rollup is a local-first personal briefing engine. It reads newsletters from
your existing Thunderbird mbox store (and optional LinkedIn, Reddit, and
webpage sources), classifies them, and writes **the rollup** — a bounded
digest — without modifying any mail.

Thunderbird owns the ongoing stream. Rollup takes a time window and produces a
**bounded reading object**. It is not an RSS reader: there is no unread count,
no scrolling feed, and no “mark all read.”

**Default digest makes no network calls.** LLM summarisation is opt-in
(`--ollama`). The web UI is loopback and single-user. **Default deploy is
Docker Compose** — see [DOCKER](DOCKER.md); host Python / venv is advanced.

The GitHub [README](https://github.com/glen-w/rollup#readme) is the same
first-run story.

```{toctree}
:maxdepth: 2
:caption: Start here

DOCKER
CONTRACT
EXAMPLES
CONFIG
WEB
```

```{toctree}
:maxdepth: 2
:caption: Using Rollup

SOURCES
CRON
OUTPUT_WRITERS
XTEINK_USAGE
TROUBLESHOOTING
```

```{toctree}
:maxdepth: 1
:caption: Product

COMPARISON
ROADMAP
```

```{toctree}
:maxdepth: 1
:caption: Design notes
:glob:

design/*
```
