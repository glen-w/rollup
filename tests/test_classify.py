"""Tests for newsletter classification."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from rollup.classify import classify_message
from rollup.models import LinkItem, ParsedMessage
from rollup.parse import compute_content_hash, parse_mbox_folder

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "Newsletters.sbd"
CLASSIFY_ROOT = FIXTURE_ROOT / "classify.sbd"


def _parse_folder(name: str):
    path = CLASSIFY_ROOT / name
    from rollup.models import MboxFolder

    folder = MboxFolder(
        folder_name=f"classify/{name}",
        relative_path=f"classify/{name}",
        mbox_path=path,
        size_bytes=path.stat().st_size,
    )
    msgs, _, errs = parse_mbox_folder(folder, 200_000, 8)
    assert msgs, f"No messages in {name}: {errs}"
    return classify_message(msgs[0])


def _parsed_message(body: str, *, links: tuple[str, ...] = ()) -> ParsedMessage:
    link_items = tuple(
        LinkItem(href=href, text=None, context=None, source_index=index)
        for index, href in enumerate(links)
    )
    return ParsedMessage(
        message_key="classify-test",
        content_hash=compute_content_hash(body),
        folder_name="classify/test",
        relative_folder_path="classify/test",
        subject="Notes from a burning Paris",
        sender="Sarah Wilson <sarah@example.com>",
        date_raw="",
        date_parsed=datetime.now().astimezone(),
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=len(links),
        html_section_break_count=0,
        links=links,
        link_items=link_items,
        read_time_minutes=8,
        preview=body[:100],
        parse_warnings=(),
    )


def test_classify_short_update() -> None:
    result = _parse_folder("short_update")
    assert result.newsletter_type == "short_update"


def test_classify_essay() -> None:
    result = _parse_folder("essay")
    assert result.newsletter_type == "essay"


def test_classify_link_roundup() -> None:
    result = _parse_folder("link_roundup")
    assert result.newsletter_type == "link_roundup"


def test_classify_multi_section_digest() -> None:
    result = _parse_folder("multi_section_digest")
    assert result.newsletter_type == "multi_section_digest"


def test_classify_unclassified_empty() -> None:
    result = classify_message(_parsed_message(""))
    assert result.newsletter_type == "unclassified"


def test_classification_scores_immutable() -> None:
    result = _parse_folder("short_update")
    assert isinstance(result.classification_scores, tuple)


def test_long_personal_story_with_footer_links_is_essay() -> None:
    body = " ".join(["Personal reflection on Paris, grief, and what matters."] * 220)
    links = tuple(f"https://substack.com/share/{i}" for i in range(12))
    result = classify_message(_parsed_message(body, links=links))
    assert result.newsletter_type == "essay"
    assert result.classification_scores[0][0] == "essay"


def test_link_roundup_still_detects_link_heavy_short_posts() -> None:
    body = " ".join(["Brief intro."] * 20)
    links = tuple(f"https://news.example/{i}" for i in range(12))
    result = classify_message(_parsed_message(body, links=links))
    assert result.newsletter_type == "link_roundup"
