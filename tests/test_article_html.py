"""Offline unit tests for shared HTML article extraction."""

from __future__ import annotations

from rollup.article_html import extract_article_text_from_html, extract_title_from_html


def test_extract_title_prefers_og_then_title_then_h1() -> None:
    assert (
        extract_title_from_html(
            '<html><head><meta property="og:title" content=" OG ">'
            "<title>Fallback</title></head><body><h1>H1</h1></body></html>"
        )
        == "OG"
    )
    assert (
        extract_title_from_html(
            "<html><head><title> Page Title </title></head><body><h1>H1</h1></body></html>"
        )
        == "Page Title"
    )
    assert extract_title_from_html("<html><body><h1> Only H1 </h1></body></html>") == "Only H1"
    assert extract_title_from_html("<html><body><p>no title</p></body></html>") == ""


def test_extract_article_prefers_main_over_noise() -> None:
    filler = "word " * 80
    html = f"""
    <html><body>
      <nav>nav noise</nav>
      <script>evil()</script>
      <main><p>{filler}</p></main>
      <footer>footer</footer>
    </body></html>
    """
    text = extract_article_text_from_html(html)
    assert "word" in text
    assert "evil" not in text
    assert "nav noise" not in text


def test_extract_article_falls_back_to_og_description() -> None:
    desc = "A " + ("long description " * 8)
    assert len(desc.strip()) >= 80
    html = f"""
    <html><head>
      <meta property="og:description" content="{desc}">
    </head><body><p>short</p></body></html>
    """
    assert extract_article_text_from_html(html) == desc.strip()


def test_extract_article_returns_empty_when_too_short() -> None:
    assert extract_article_text_from_html("<html><body><p>hi</p></body></html>") == ""
