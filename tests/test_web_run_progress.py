"""Tests for Run Studio progress parsing."""

from __future__ import annotations

from rollup.web.run_progress import parse_run_progress


def test_parse_run_progress_starting() -> None:
    progress = parse_run_progress([], dry_run=False, status="running")
    assert progress["phase"] == "starting"
    assert progress["percent"] == 3


def test_parse_run_progress_parsing() -> None:
    lines = [
        "INFO: Digest: root=/mail folders=4 lookback=7d dry_run=False no_ollama=True",
        "INFO: Parsing tech (/mail/tech.mbox)",
        "INFO: Parsing brainfood (/mail/brainfood.mbox)",
    ]
    progress = parse_run_progress(lines, dry_run=False, status="running")
    assert progress["phase"] == "parsing"
    assert progress["percent"] > 10
    assert progress["detail"] == "Parsing brainfood"


def test_parse_run_progress_llm() -> None:
    lines = [
        "INFO: Digest: root=/mail folders=2 lookback=7d dry_run=False no_ollama=False",
        "INFO: LLM [12/53] summarising: 'Weekly update' (provider=ollama model=llama3.2:3b, profile=rough, body_chars=100, prompt_chars=200, link_count=1)",
    ]
    progress = parse_run_progress(lines, dry_run=False, status="running")
    assert progress["phase"] == "summarizing"
    assert progress["llm_current"] == 12
    assert progress["llm_total"] == 53
    assert progress["percent"] > 40


def test_parse_run_progress_complete() -> None:
    progress = parse_run_progress(
        ["Folders scanned: 5"],
        dry_run=False,
        status="success",
    )
    assert progress["phase"] == "complete"
    assert progress["percent"] == 100


def test_parse_run_progress_reddit_fetch() -> None:
    lines = [
        "INFO: Digest: root=/mail folders=4 linkedin=0 reddit=28 webpage=0 lookback=7d dry_run=False no_ollama=True",
        "INFO: Parsing tech (/mail/tech.mbox)",
        "INFO: Fetching Reddit: 28 subs, about 32 min (70s between subs; 429s add extra wait)",
        "INFO: Reddit [3/28] r/localllama (about 29 min remaining)",
    ]
    progress = parse_run_progress(lines, dry_run=False, status="running")
    assert progress["phase"] == "reddit"
    assert progress["phase_label"] == "Fetching Reddit"
    assert progress["detail"] == "r/localllama (3/28), about 29 min remaining"
    assert 30 <= progress["percent"] <= 40
