"""Tests for TXT / JSON / EPUB output writers and shared offline text helpers."""

from __future__ import annotations

import argparse
import json
import zipfile
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rollup.addons.offline_text import (
    clip_heading,
    strip_urls_for_offline,
    wrap_text,
)
from rollup.addons.txt.render import atomic_write_txt_digest, render_txt
from rollup.addons.json.serialize import (
    SCHEMA_VERSION,
    atomic_write_json_digest,
    render_json,
    report_to_dict,
)
from rollup.classify import classify_message
from rollup.config import Config, compute_date_window
from rollup.filter import make_digest_entry
from rollup.models import DigestGroup, DigestReport, DigestStats, LinkItem, ParsedMessage
from rollup.output_writers import (
    OutputWriterError,
    WriteContext,
    builtin_writers,
    discover_writers,
    run_enabled_writers,
    validate_requested_writers,
)
from rollup.parse import compute_content_hash


def _args(**kwargs) -> argparse.Namespace:
    defaults = {"xteink": False, "output": []}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _config(tmp_path: Path | None = None) -> Config:
    root = tmp_path or Path("/tmp")
    return Config(
        root=root,
        mail_root=root / "mail",
        output_dir=root / "out",
        state_dir=root / "state",
        log_dir=root / "logs",
        lookback_days=7,
        folders_include=(),
        folders_exclude=(),
        no_ollama=True,
        include_seen_undated=False,
        rebuild_summaries=False,
        max_body_chars=200_000,
        max_chars_for_llm=30_000,
        max_display_links=8,
        ollama_url="http://localhost:11434/api/generate",
        ollama_model="llama3.2:3b",
        allow_remote_ollama=False,
        summary_profile=None,
        summary_variants=(),
        summary_type_routing=None,
        summary_profile_set_path=None,
        export_summary_profile_set_path=None,
        list_summary_profiles=False,
        list_newsletter_types=False,
        summary_routing_report=False,
    )


def _empty_report() -> DigestReport:
    now = datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc)
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=now,
        window_end=now,
        dated_by_folder={},
        undated=(),
        stats=DigestStats(
            folders_scanned=0,
            messages_parsed=0,
            dated_included=0,
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=0,
        ),
    )


def _entry(
    subject: str = "Test Subject",
    key: str = "k1",
    *,
    summary: str | None = None,
    links: tuple[str, ...] = (),
    preview: str | None = None,
):
    body = preview if preview is not None else f"Body for {subject}"
    link_items = tuple(
        LinkItem(href=href, text=None, context=None, source_index=i)
        for i, href in enumerate(links)
    )
    parsed = ParsedMessage(
        message_key=key,
        content_hash=compute_content_hash(body),
        folder_name="tech",
        relative_folder_path="tech",
        subject=subject,
        sender="a@example.com",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text=body,
        body_html="<p>raw html body</p>",
        html_heading_count=0,
        html_link_count=len(links),
        html_section_break_count=0,
        links=links,
        link_items=link_items,
        read_time_minutes=1,
        preview=body,
        parse_warnings=(),
    )
    entry = make_digest_entry(classify_message(parsed), no_ollama=True)
    if summary is not None:
        entry = replace(entry, summary=summary, summary_source="ollama")
    return entry


def _report_with_content() -> DigestReport:
    now = datetime.now().astimezone()
    start, end = compute_date_window(now, 7)
    entry = _entry(
        "Linked newsletter",
        "k-links",
        summary="Read [the post](https://example.com/post) and more.",
        links=("https://example.com/a", "https://example.com/b"),
        preview="Preview with https://example.com/preview inside",
    )
    group = DigestGroup(
        group_id="g1",
        group_type="notification_stream",
        display_name="Cursor updates",
        sender_normalized="a@example.com",
        folder_name="tech",
        entries=(_entry("Update one", "k1"), _entry("Update two", "k2")),
        group_summary="Two product updates this week.",
    )
    return DigestReport(
        generated_at=now,
        lookback_days=7,
        window_start=start,
        window_end=end,
        dated_by_folder={"tech": (group, entry)},
        undated=(),
        stats=DigestStats(
            folders_scanned=1,
            messages_parsed=3,
            dated_included=3,
            undated_needing_review=0,
            skipped_outside_window=0,
            skipped_seen_undated=0,
            deduped_messages=0,
            parse_errors=0,
            summaries_ollama=0,
            summaries_cache=0,
            summaries_fallback=3,
        ),
    )


def test_builtin_discovers_new_writers() -> None:
    writers = builtin_writers()
    assert set(writers) >= {"xteink", "epub", "json", "txt"}
    assert discover_writers().keys() >= writers.keys()


def test_validate_new_writers_ok() -> None:
    writers = discover_writers()
    assert (
        validate_requested_writers(
            _args(output=["epub", "json", "txt"]), writers
        )
        is None
    )


def test_offline_strip_and_wrap() -> None:
    assert (
        strip_urls_for_offline("See [docs](https://example.com/docs) today")
        == "See docs today"
    )
    assert strip_urls_for_offline("") == ""
    assert "www." not in strip_urls_for_offline("visit www.example.com please")
    stripped = strip_urls_for_offline(
        "Apply through the provided link: <https://example.com/jobs>."
    )
    assert "http" not in stripped
    assert "<" not in stripped
    assert "Apply through the provided link:" in stripped
    wrapped = wrap_text("word " * 40, 60)
    for line in wrapped.split("\n"):
        if line.strip():
            assert len(line) <= 70
    assert wrap_text("") == ""
    # Preserve blank lines between paragraphs.
    assert wrap_text("a\n\nb", 60) == "a\n\nb"


def test_clip_heading_prefers_complete_sentence() -> None:
    first = (
        "I am pleased to share that we are recruiting a new colleague "
        "for our FAO Statistics and Information Team (NFISI)."
    )
    rest = (
        " If you are interested in fisheries and aquaculture data, information "
        "systems, analytics and global knowledge products, I encourage you to "
        "take a look at this Fishery Officer position in Rome."
    )
    clipped = clip_heading(first + rest, 280)
    assert clipped == first
    assert "…" not in clipped
    assert "Fishery Officer" not in clipped


def test_clip_heading_falls_back_to_word_boundary() -> None:
    text = "word " * 80
    clipped = clip_heading(text, 80)
    assert clipped.endswith("…")
    assert "word" in clipped
    assert not clipped[:-1].endswith(" ")


def test_artifact_paths_include_run_id(tmp_path: Path) -> None:
    from rollup.addons.artifact_write import (
        atomic_write_digest_artifact,
        digest_artifact_path,
    )

    generated_at = datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc)
    path = digest_artifact_path(
        tmp_path, generated_at, "txt", run_id_short="abcd1234"
    )
    assert path.name == "2026-07-02T103000Z-abcd1234-newsletter-digest.txt"
    written = atomic_write_digest_artifact(
        tmp_path,
        generated_at,
        "hello\n",
        extension="txt",
        run_id_short="abcd1234",
    )
    assert written == path
    assert written.read_text(encoding="utf-8") == "hello\n"
    with pytest.raises(FileExistsError):
        atomic_write_digest_artifact(
            tmp_path,
            generated_at,
            "again\n",
            extension="txt",
            run_id_short="abcd1234",
        )


def test_txt_omits_urls_and_includes_structure() -> None:
    report = _report_with_content()
    text = render_txt(report, 8)
    assert "https://" not in text
    assert "http://" not in text
    assert "www." not in text
    assert "Cursor updates" in text
    assert "Linked newsletter" in text
    assert "the post" in text
    assert "Key links" not in text
    assert "Digest Generation Details" in text


def test_txt_writer_writes_stem_txt(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    generated_at = datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc)
    ctx = WriteContext(
        output_dir=out,
        generated_at=generated_at,
        max_display_links=8,
        dry_run=False,
    )
    paths = run_enabled_writers(
        builtin_writers(),
        _report_with_content(),
        ctx,
        args=_args(output=["txt"]),
        config=_config(tmp_path),
    )
    assert len(paths) == 1
    assert paths[0].name.endswith("-newsletter-digest.txt")
    assert paths[0].exists()


def test_json_schema_and_omits_bodies() -> None:
    report = _report_with_content()
    payload = report_to_dict(report, 8)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["format"] == "rollup.digest"
    assert isinstance(payload["generated_at"], str)
    assert isinstance(payload["window_start"], str)
    dumped = json.dumps(payload)
    assert "body_html" not in dumped
    assert "body_text" not in dumped
    items = payload["dated_by_folder"]["tech"]
    kinds = {item["kind"] for item in items}
    assert kinds == {"entry", "group"}
    entry = next(item for item in items if item["kind"] == "entry")
    assert entry["main_links"] or entry["other_links"]
    assert "https://example.com" in json.dumps(entry["main_links"] + entry["other_links"])
    text = render_json(report, 8)
    loaded = json.loads(text)
    assert loaded["schema_version"] == SCHEMA_VERSION


def test_json_writer_writes_stem_json(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    generated_at = datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc)
    path = atomic_write_json_digest(
        out, generated_at, render_json(_empty_report(), 8)
    )
    assert path.name.endswith("-newsletter-digest.json")
    assert json.loads(path.read_text(encoding="utf-8"))["format"] == "rollup.digest"


def test_txt_refuses_overwrite(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    generated_at = datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc)
    atomic_write_txt_digest(out, generated_at, "one\n")
    with pytest.raises(FileExistsError):
        atomic_write_txt_digest(out, generated_at, "two\n")


def test_dry_run_skips_json_txt(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    ctx = WriteContext(
        output_dir=out,
        generated_at=datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc),
        max_display_links=8,
        dry_run=True,
    )
    paths = run_enabled_writers(
        builtin_writers(),
        _empty_report(),
        ctx,
        args=_args(output=["json", "txt"]),
        config=_config(tmp_path),
    )
    assert paths == []
    assert list(out.iterdir()) == []


def test_epub_missing_dependency_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rollup.addons.epub import EpubOutputWriter

    monkeypatch.setattr(
        "rollup.addons.epub.ebooklib_available", lambda: False
    )
    writer = EpubOutputWriter()
    ctx = WriteContext(
        output_dir=tmp_path / "out",
        generated_at=datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc),
        max_display_links=8,
        dry_run=False,
    )
    with pytest.raises(OutputWriterError, match="rollup\\[epub\\]"):
        writer.write(_empty_report(), ctx)


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("ebooklib") is None,
    reason="ebooklib not installed",
)
def test_epub_builds_zip_with_chapters_without_links(tmp_path: Path) -> None:
    from rollup.addons.epub.render import render_epub_bytes, atomic_write_epub_digest

    report = _report_with_content()
    data = render_epub_bytes(report, 8)
    assert data[:4] == b"PK\x03\x04" or data[:2] == b"PK"
    path = atomic_write_epub_digest(
        tmp_path,
        datetime(2026, 7, 2, 10, 30, tzinfo=timezone.utc),
        data,
    )
    assert path.name.endswith("-newsletter-digest.epub")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        assert any(n.endswith("book.css") or "style/" in n for n in names)
        assert any("cover" in n for n in names)
        assert any("folder-" in n for n in names)
        assert any("rollup_cover_epub" in n or "images/" in n for n in names)
        opf_names = [n for n in names if n.endswith(".opf")]
        assert opf_names
        opf = zf.read(opf_names[0]).decode("utf-8")
        assert 'properties="cover-image"' in opf
        assert 'name="cover"' in opf and 'content="cover-img"' in opf
        assert "rollup_cover_epub.png" in opf
        assert 'type="cover"' in opf and "cover.xhtml" in opf
        cover_xhtml = next(n for n in names if n.endswith("cover.xhtml"))
        cover_html = zf.read(cover_xhtml).decode("utf-8")
        assert "rollup_cover_epub.png" in cover_html
        assert "Week of" in cover_html
        folder_files = [n for n in names if "folder-" in n and n.endswith(".xhtml")]
        assert folder_files
        content = zf.read(folder_files[0]).decode("utf-8")
        assert "https://example.com" not in content
        assert "Key links" not in content
        assert "<a href=" not in content
        assert "the post" in content  # markdown link label kept
        assert "Linked newsletter" in content
        assert "Cursor updates" in content
