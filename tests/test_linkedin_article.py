"""Tests for LinkedIn article fetch and enrichment."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from rollup.linkedin.article import (
    ARTICLE_SEPARATOR,
    enrich_post_with_article,
    enrich_posts_with_articles,
    extract_article_text_from_html,
    fetch_article_text,
    MAX_ARTICLE_FETCHES,
)
from rollup.linkedin.models import LinkedInPost

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"


def test_extract_article_text_from_html_fixture() -> None:
    html = (FIXTURES / "article_squarespace.html").read_text(encoding="utf-8")
    text = extract_article_text_from_html(html)
    assert "SC22 outcomes" in text
    assert "pole-and-line boat" in text
    assert len(text) >= 200


def test_enrich_post_with_article_appends_body() -> None:
    post = LinkedInPost(
        activity_id="1",
        author_name="A",
        author_member_id="ACoAAA",
        text="Short teaser.",
        permalink="https://www.linkedin.com/feed/update/urn:li:activity:1",
        created_at=None,
        article_url="https://example.com/article",
        article_title="Full title",
    )
    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.encoding = "utf-8"
    response.iter_content = lambda chunk_size: [
        (FIXTURES / "article_squarespace.html").read_bytes()
    ]
    session.get.return_value = response
    session.headers = {"User-Agent": "test"}

    enriched, warnings = enrich_post_with_article(post, session, enabled=True)
    assert "Short teaser." in enriched.text
    assert ARTICLE_SEPARATOR in enriched.text
    assert "SC22 outcomes" in enriched.text
    assert warnings == ()


def test_enrich_post_skipped_when_disabled() -> None:
    post = LinkedInPost(
        activity_id="1",
        author_name="A",
        author_member_id=None,
        text="Teaser",
        permalink="",
        created_at=None,
        article_url="https://example.com/article",
    )
    session = MagicMock()
    result, warnings = enrich_post_with_article(post, session, enabled=False)
    assert result is post
    assert warnings == ()
    session.get.assert_not_called()


def test_fetch_article_text_invalid_url() -> None:
    session = MagicMock()
    text, warnings = fetch_article_text("not-a-url", session)
    assert text == ""
    assert "linkedin_article_url_invalid" in warnings


def test_enrich_posts_with_articles_cap() -> None:
    session = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.encoding = "utf-8"
    response.iter_content = lambda chunk_size: [
        b"<article><p>Body long enough for extraction threshold with many words here.</p></article>"
    ]
    session.get.return_value = response
    session.headers = {"User-Agent": "test"}

    many = [
        LinkedInPost(
            activity_id=str(i),
            author_name="A",
            author_member_id=None,
            text="t",
            permalink="",
            created_at=None,
            article_url=f"https://example.com/{i}",
        )
        for i in range(MAX_ARTICLE_FETCHES + 2)
    ]
    enriched, warnings = enrich_posts_with_articles(many, session, enabled=True)
    assert len(enriched) == MAX_ARTICLE_FETCHES + 2
    assert any("linkedin_article_fetch_cap" in w for w in warnings)
