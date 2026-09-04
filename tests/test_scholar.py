"""Google Scholar alert lane: detect, parse, fetch caps, cache, type routing."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from rollup.classify import classify_message
from rollup.config import Config
from rollup.models import ParsedMessage
from rollup.parse import compute_content_hash
from rollup.scholar.config import ScholarConfig, parse_scholar_config
from rollup.scholar.detect import is_scholar_alert, is_scholar_source_key
from rollup.scholar.enrich import enrich_scholar_messages
from rollup.scholar.parse import extract_papers_from_message
from rollup.scholar.urls import normalize_paper_url, rewrite_arxiv_pdf, unwrap_scholar_url
from rollup.source_models import SourcePolicy
from rollup.source_policy import apply_effective_type
from rollup.state import SCHEMA_VERSION, get_schema_version, init_db
from rollup.webpage.fetch import WebpageFetchError, WebpageFetchResult


SCHOLAR_HTML = """
<html><body>
<h3><a href="https://scholar.google.com/scholar_url?url=https%3A%2F%2Farxiv.org%2Fpdf%2F2401.00001.pdf&amp;hl=en">A Novel Approach to Widget Theory</a></h3>
<div>A. Author, B. Coauthor - Journal of Imaginary Results, 2024</div>
<div>We present a toy result about widgets.</div>
<a href="https://scholar.google.com/scholar?q=related:abc">Related articles</a>
<a href="https://scholar.google.com/scholar_url?url=https%3A%2F%2Fexample.edu%2Fpaper.html">Second Paper On Things</a>
<div>C. Writer - Proceedings, 2023</div>
<a href="https://scholar.google.com/scholar_url?url=https%3A%2F%2Fexample.edu%2Ffull.pdf">PDF Only Paper Title Here</a>
<a href="https://accounts.google.com/Unsubscribe">Unsubscribe</a>
</body></html>
"""


def _config(tmp_path: Path, **overrides) -> Config:
    base = dict(
        root=tmp_path / "root",
        mail_root=tmp_path / "mail",
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        lookback_days=3650,
        folders_include=(),
        folders_exclude=(),
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=20_000,
        max_chars_for_llm=4000,
        max_display_links=8,
        ollama_url="http://localhost:11434/api/generate",
        ollama_model="m",
        allow_remote_ollama=False,
        summary_profile=None,
        summary_variants=(),
        summary_type_routing=None,
        summary_profile_set_path=None,
        export_summary_profile_set_path=None,
        list_summary_profiles=False,
        list_newsletter_types=False,
        summary_routing_report=False,
        scholar=ScholarConfig(mode="detailed"),
    )
    base.update(overrides)
    return Config(**base)


def _alert(
    *,
    html: str | None = SCHOLAR_HTML,
    body: str = "Scholar alert body with papers listed.",
    sender: str = "Google Scholar <scholaralerts-noreply@google.com>",
    subject: str = "Scholar Alert: widgets",
    message_key: str = "mid:scholar-1",
) -> ParsedMessage:
    return ParsedMessage(
        message_key=message_key,
        content_hash=compute_content_hash(body),
        folder_name="Newsletters",
        relative_folder_path="Newsletters",
        subject=subject,
        sender=sender,
        date_raw="Thu, 1 Jan 2026 12:00:00 +0000",
        date_parsed=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        body_text=body,
        body_html=html,
        html_heading_count=1,
        html_link_count=5,
        html_section_break_count=0,
        links=(),
        link_items=(),
        read_time_minutes=2,
        preview=body[:80],
        parse_warnings=(),
        source_key="from:scholaralerts-noreply@google.com",
        list_id="scholar-alerts.google.com",
    )


def test_unwrap_scholar_url() -> None:
    wrapped = (
        "https://scholar.google.com/scholar_url?"
        "url=https%3A%2F%2Farxiv.org%2Fabs%2F2401.00001&hl=en"
    )
    assert unwrap_scholar_url(wrapped) == "https://arxiv.org/abs/2401.00001"


def test_arxiv_pdf_rewrite() -> None:
    assert (
        rewrite_arxiv_pdf("https://arxiv.org/pdf/2401.00001.pdf")
        == "https://arxiv.org/abs/2401.00001"
    )
    dest, skip_pdf = normalize_paper_url(
        "https://scholar.google.com/scholar_url?url=https%3A%2F%2Farxiv.org%2Fpdf%2F2401.00001.pdf"
    )
    assert dest == "https://arxiv.org/abs/2401.00001"
    assert skip_pdf is False


def test_pdf_skip_and_junk_filter() -> None:
    dest, skip = normalize_paper_url("https://example.edu/full.pdf")
    assert dest == "https://example.edu/full.pdf"
    assert skip is True
    dest2, _ = normalize_paper_url("https://scholar.google.com/citations?user=abc")
    assert dest2 is None
    dest3, _ = normalize_paper_url("https://accounts.google.com/Logout")
    assert dest3 is None


def test_detect_scholar_alert() -> None:
    msg = _alert()
    assert is_scholar_alert(msg)
    assert is_scholar_source_key(msg.source_key, msg.list_id)
    other = replace(
        _alert(sender="news@example.com", subject="Weekly", message_key="mid:other"),
        source_key="from:news@example.com",
        list_id=None,
    )
    assert not is_scholar_alert(other)


def test_extract_papers_from_html() -> None:
    papers = extract_papers_from_message(_alert())
    titles = [p.title for p in papers]
    assert "A Novel Approach to Widget Theory" in titles
    assert "Second Paper On Things" in titles
    assert "PDF Only Paper Title Here" in titles
    assert not any("Related" in t for t in titles)
    arxiv = next(p for p in papers if "Widget" in p.title)
    assert arxiv.url == "https://arxiv.org/abs/2401.00001"
    pdf = next(p for p in papers if p.title.startswith("PDF"))
    assert pdf.skip_fetch is True
    assert pdf.skip_reason == "scholar_pdf_skipped"


def test_default_mode_does_not_expand(tmp_path: Path) -> None:
    cfg = _config(tmp_path, scholar=ScholarConfig(mode="default"))
    messages, warnings = enrich_scholar_messages(
        [_alert()], cfg, allow_network=True, conn=None
    )
    assert len(messages) == 1
    assert messages[0].message_key == "mid:scholar-1"
    assert warnings == []


def test_default_mode_forces_item_list() -> None:
    classified = classify_message(_alert())
    updated, detected, effective, disagreed = apply_effective_type(classified, None)
    assert effective == "item_list"
    assert disagreed is True
    assert updated.newsletter_type == "item_list"
    override = SourcePolicy(
        source_key="from:scholaralerts-noreply@google.com",
        newsletter_type_override="essay",
    )
    _, _, effective2, _ = apply_effective_type(classified, override)
    assert effective2 == "essay"


def test_detailed_replaces_alert_without_network(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    with patch("rollup.scholar.enrich.fetch_webpage") as fetch:
        messages, warnings = enrich_scholar_messages(
            [_alert()], cfg, allow_network=False, conn=None
        )
        fetch.assert_not_called()
    keys = [m.message_key for m in messages]
    assert all(k.startswith("scholar:paper:") for k in keys)
    assert "mid:scholar-1" not in keys
    assert len(messages) == 3
    classified = classify_message(messages[0])
    assert classified.newsletter_type == "academic_paper"


def test_out_of_window_scholar_alert_not_expanded(tmp_path: Path) -> None:
    """Historical Scholar mail must not consume the per-run fetch cap."""
    cfg = _config(
        tmp_path,
        lookback_days=7,
        scholar=ScholarConfig(mode="detailed", max_fetches_per_run=1),
    )
    when = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    stale = _alert()
    recent = replace(
        _alert(message_key="mid:scholar-recent"),
        date_parsed=datetime(2026, 9, 3, 12, tzinfo=timezone.utc),
    )
    result = WebpageFetchResult(
        url="https://arxiv.org/abs/2401.00001",
        title="Fetched title",
        body_text="Fetched abstract about widgets.",
        warnings=(),
    )
    with patch("rollup.scholar.enrich.fetch_webpage", return_value=result) as fetch:
        messages, _ = enrich_scholar_messages(
            [stale, recent],
            cfg,
            allow_network=True,
            conn=None,
            generated_at=when,
        )
        assert fetch.call_count == 1
    keys = [m.message_key for m in messages]
    assert "mid:scholar-1" in keys
    assert "mid:scholar-recent" not in keys
    assert any(k.startswith("scholar:paper:") for k in keys)


def test_detailed_fetch_cap_and_cache(tmp_path: Path) -> None:
    cfg = _config(
        tmp_path,
        scholar=ScholarConfig(
            mode="detailed", max_papers_per_email=1, max_fetches_per_run=1
        ),
    )
    (tmp_path / "state").mkdir()
    conn = init_db(tmp_path / "state" / "rollup.db")
    result = WebpageFetchResult(
        url="https://arxiv.org/abs/2401.00001",
        title="Fetched title",
        body_text="Fetched abstract about widgets.",
        warnings=(),
    )
    with patch("rollup.scholar.enrich.fetch_webpage", return_value=result) as fetch:
        messages, _ = enrich_scholar_messages(
            [_alert()],
            cfg,
            allow_network=True,
            conn=conn,
            generated_at=datetime.now(timezone.utc),
        )
        assert fetch.call_count == 1
        messages2, _ = enrich_scholar_messages(
            [_alert()],
            cfg,
            allow_network=True,
            conn=conn,
            generated_at=datetime.now(timezone.utc),
        )
        assert fetch.call_count == 1
    assert "Fetched abstract about widgets." in messages[0].body_text
    assert "Fetched abstract about widgets." in messages2[0].body_text
    assert "scholar_email_cap" in messages[1].parse_warnings
    conn.close()


def test_fetch_failure_falls_back_to_snippet(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    with patch(
        "rollup.scholar.enrich.fetch_webpage",
        side_effect=WebpageFetchError("webpage_fetch_failed", "boom"),
    ):
        messages, _ = enrich_scholar_messages(
            [_alert()], cfg, allow_network=True, conn=None
        )
    assert any("scholar_fetch_failed" in m.parse_warnings for m in messages)
    assert "A Novel Approach to Widget Theory" in messages[0].body_text


def test_parse_scholar_config(tmp_path: Path) -> None:
    cfg = parse_scholar_config(
        {"mode": "detailed", "max_papers_per_email": 3},
        path=tmp_path / "c.toml",
    )
    assert cfg.mode == "detailed"
    assert cfg.max_papers_per_email == 3
    assert cfg.max_fetches_per_run == 40


def test_schema_v16_scholar_paper_bodies(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "rollup.db")
    assert get_schema_version(conn) == SCHEMA_VERSION == 16
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='scholar_paper_bodies'"
    ).fetchone()
    assert row is not None
    conn.close()


def _write_scholar_mbox(root: Path) -> None:
    root.mkdir(parents=True)
    html = " ".join(SCHOLAR_HTML.split())
    (root / "scholar").write_text(
        "\n".join(
            [
                "From - Tue Aug 04 15:30:58 2026",
                "Subject: Scholar Alert: widgets",
                "From: Google Scholar <scholaralerts-noreply@google.com>",
                "To: reader@example.com",
                "Date: Sun, 02 Aug 2026 15:30:58 +0200",
                "Message-ID: <scholar1@example.com>",
                'Content-Type: text/html; charset="utf-8"',
                "",
                html,
                "",
            ]
        ),
        encoding="utf-8",
    )


def _digest_subjects(result) -> list[str]:
    report = result.report
    assert report is not None
    subjects: list[str] = []
    for items in report.dated_by_folder.values():
        for item in items:
            entries = getattr(item, "entries", None)
            if entries is not None:
                subjects.extend(e.classified.parsed.subject for e in entries)
            else:
                subjects.append(item.classified.parsed.subject)
    return subjects


def test_pipeline_default_keeps_alert_card(tmp_path: Path) -> None:
    from rollup.clock import FixedClock
    from rollup.pipeline import run_digest
    from rollup.run_options import GroupingConfig, RunOptions

    mail = tmp_path / "mail"
    root = mail / "Newsletters.sbd"
    _write_scholar_mbox(root)
    cfg = _config(
        tmp_path,
        root=root,
        mail_root=mail,
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        scholar=ScholarConfig(mode="default"),
        lookback_days=3650,
        no_webpage=True,
    )
    with patch("rollup.scholar.enrich.fetch_webpage") as fetch:
        result = run_digest(
            cfg,
            RunOptions(dry_run=True, write_manifest=False),
            grouping=GroupingConfig(enabled=True, min_group_size=3),
            clock=FixedClock(datetime(2026, 8, 12, 12, tzinfo=timezone.utc)),
            acquire_lock=False,
        )
        fetch.assert_not_called()
    assert result.status == "dry_run"
    subjects = _digest_subjects(result)
    assert subjects == ["Scholar Alert: widgets"]


def test_pipeline_detailed_dry_run_expands_papers_without_fetch(
    tmp_path: Path,
) -> None:
    from rollup.clock import FixedClock
    from rollup.pipeline import run_digest
    from rollup.run_options import GroupingConfig, RunOptions

    mail = tmp_path / "mail"
    root = mail / "Newsletters.sbd"
    _write_scholar_mbox(root)
    cfg = _config(
        tmp_path,
        root=root,
        mail_root=mail,
        output_dir=tmp_path / "output",
        state_dir=tmp_path / "state",
        log_dir=tmp_path / "logs",
        scholar=ScholarConfig(mode="detailed"),
        lookback_days=3650,
        no_webpage=True,
    )
    with patch("rollup.scholar.enrich.fetch_webpage") as fetch:
        result = run_digest(
            cfg,
            RunOptions(dry_run=True, write_manifest=False),
            grouping=GroupingConfig(enabled=True, min_group_size=3),
            clock=FixedClock(datetime(2026, 8, 12, 12, tzinfo=timezone.utc)),
            acquire_lock=False,
        )
        fetch.assert_not_called()
    assert result.status == "dry_run"
    subjects = _digest_subjects(result)
    assert "Scholar Alert: widgets" not in subjects
    assert "A Novel Approach to Widget Theory" in subjects
    assert "Second Paper On Things" in subjects
    assert "PDF Only Paper Title Here" in subjects
    from rollup.models import DigestGroup

    keys: list[str] = []
    types: list[str] = []
    for items in result.report.dated_by_folder.values():
        for item in items:
            assert not isinstance(item, DigestGroup)
            keys.append(item.classified.parsed.message_key)
            types.append(item.classified.newsletter_type)
    assert all(k.startswith("scholar:paper:") for k in keys)
    assert types == ["academic_paper"] * 3
    assert any(
        w.code == "scholar_papers" for w in result.aggregated.parse.warnings
    )
