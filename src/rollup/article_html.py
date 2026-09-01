"""Shared HTML article extraction for LinkedIn enrichment and webpage queue."""

from __future__ import annotations

import html2text
from bs4 import BeautifulSoup

MIN_ARTICLE_CHARS = 200
MIN_OG_DESCRIPTION_CHARS = 80


def extract_article_text_from_html(html: str) -> str:
    """Best-effort article body from HTML (external blogs, Pulse pages)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "nav", "footer", "header"]):
        tag.decompose()

    best = ""
    for selector in ("article", "main", "[role=main]"):
        node = soup.select_one(selector)
        if node is None:
            continue
        text = _html_node_to_text(node)
        if len(text) >= MIN_ARTICLE_CHARS:
            return text
        if len(text) > len(best):
            best = text

    if len(best) >= MIN_ARTICLE_CHARS:
        return best

    body = soup.body or soup
    text = _html_node_to_text(body)
    if len(text) >= MIN_ARTICLE_CHARS:
        return text

    meta = soup.find("meta", attrs={"property": "og:description"})
    if meta is not None:
        content = meta.get("content")
        if isinstance(content, str) and len(content.strip()) >= MIN_OG_DESCRIPTION_CHARS:
            return content.strip()

    return text if len(text) >= MIN_ARTICLE_CHARS else ""


def extract_title_from_html(html: str) -> str:
    """Best-effort page title from og:title, <title>, or h1."""
    soup = BeautifulSoup(html, "html.parser")
    og = soup.find("meta", attrs={"property": "og:title"})
    if og is not None:
        content = og.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
        if title:
            return title
    h1 = soup.find("h1")
    if h1 is not None:
        text = h1.get_text(strip=True)
        if text:
            return text
    return ""


def _html_node_to_text(node) -> str:
    converter = html2text.HTML2Text()
    converter.ignore_links = True
    converter.ignore_images = True
    converter.ignore_tables = False
    converter.body_width = 0
    return converter.handle(str(node)).strip()
