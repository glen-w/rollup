"""Extract papers from Google Scholar alert emails and map them to ParsedMessage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bs4 import BeautifulSoup

from rollup.addons.offline_text import clip_heading
from rollup.models import LinkItem, ParsedMessage
from rollup.parse import URL_RE, compute_content_hash
from rollup.scholar.detect import PAPER_MESSAGE_KEY_PREFIX
from rollup.scholar.urls import is_junk_anchor_text, normalize_paper_url
from rollup.webpage.url import url_hash

_SUBJECT_MAX = 280
_PREVIEW_MAX = 2000
_MIN_TITLE_LEN = 8
_FOLLOWING_TEXT_MAX = 800


@dataclass(frozen=True)
class ScholarPaper:
    title: str
    url: str
    authors: str | None = None
    venue: str | None = None
    snippet: str | None = None
    skip_fetch: bool = False
    skip_reason: str | None = None


def paper_message_key(url: str) -> str:
    return f"{PAPER_MESSAGE_KEY_PREFIX}{url_hash(url)}"


def extract_papers_from_message(parsed: ParsedMessage) -> tuple[ScholarPaper, ...]:
    html = parsed.body_html or ""
    if html.strip():
        papers = _extract_from_html(html)
        if papers:
            return papers
    return _extract_from_plain(parsed.body_text or "")


def _extract_from_html(html: str) -> tuple[ScholarPaper, ...]:
    soup = BeautifulSoup(html, "html.parser")
    papers: list[ScholarPaper] = []
    seen: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "")
        title = " ".join(anchor.get_text(" ", strip=True).split())
        dest, skip_pdf = normalize_paper_url(href)
        if dest is None or dest in seen:
            continue
        if is_junk_anchor_text(title):
            continue
        if len(title) < _MIN_TITLE_LEN:
            continue
        seen.add(dest)
        authors, venue, snippet = _meta_after_anchor(anchor)
        papers.append(
            ScholarPaper(
                title=title,
                url=dest,
                authors=authors,
                venue=venue,
                snippet=snippet,
                skip_fetch=skip_pdf,
                skip_reason="scholar_pdf_skipped" if skip_pdf else None,
            )
        )
    return tuple(papers)


def _meta_after_anchor(anchor) -> tuple[str | None, str | None, str | None]:
    chunks: list[str] = []
    parent = getattr(anchor, "parent", None)
    nodes = []
    if parent is not None:
        nodes.extend(parent.find_all_next(string=True, limit=24))
    else:
        nodes.extend(anchor.find_all_next(string=True, limit=24))
    for node in nodes:
        text = " ".join(str(node).split())
        if not text:
            continue
        chunks.append(text)
        joined = " ".join(chunks)
        if len(joined) >= _FOLLOWING_TEXT_MAX:
            break
    following = " ".join(chunks).strip()[:_FOLLOWING_TEXT_MAX]
    if not following:
        return None, None, None
    authors, venue, snippet = _split_authors_venue_snippet(following)
    return authors, venue, snippet


def _split_authors_venue_snippet(
    following: str,
) -> tuple[str | None, str | None, str | None]:
    first, _, rest = following.partition("  ")
    line = first.strip() or following.split("\n", 1)[0].strip()
    authors: str | None = None
    venue: str | None = None
    if " - " in line and len(line) < 280:
        left, right = line.split(" - ", 1)
        if left and not left.lower().startswith("http"):
            authors = left.strip()
            venue = right.strip() or None
            snippet_src = rest.strip() or following[len(line) :].strip()
            return authors, venue, snippet_src or None
    return None, None, following or None


def _extract_from_plain(text: str) -> tuple[ScholarPaper, ...]:
    papers: list[ScholarPaper] = []
    seen: set[str] = set()
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    for index, line in enumerate(lines):
        match = URL_RE.search(line)
        if not match:
            continue
        dest, skip_pdf = normalize_paper_url(match.group(0))
        if dest is None or dest in seen:
            continue
        title = URL_RE.sub("", line).strip(" -•*\t")
        if len(title) < _MIN_TITLE_LEN and index > 0:
            prev = URL_RE.sub("", lines[index - 1]).strip(" -•*\t")
            if len(prev) >= _MIN_TITLE_LEN:
                title = prev
        if is_junk_anchor_text(title) or len(title) < _MIN_TITLE_LEN:
            continue
        seen.add(dest)
        authors = None
        venue = None
        snippet = None
        if index + 1 < len(lines):
            nxt = lines[index + 1]
            if not URL_RE.search(nxt):
                authors, venue, snippet = _split_authors_venue_snippet(nxt)
                if snippet is None and nxt:
                    snippet = nxt
        papers.append(
            ScholarPaper(
                title=title,
                url=dest,
                authors=authors,
                venue=venue,
                snippet=snippet,
                skip_fetch=skip_pdf,
                skip_reason="scholar_pdf_skipped" if skip_pdf else None,
            )
        )
    return tuple(papers)


def assemble_paper_body(
    paper: ScholarPaper,
    *,
    page_title: str | None = None,
    page_text: str | None = None,
) -> str:
    lines = [f"Title: {paper.title}"]
    if paper.authors:
        lines.append(f"Authors: {paper.authors}")
    if paper.venue:
        lines.append(f"Venue: {paper.venue}")
    lines.append(f"URL: {paper.url}")
    abstract = (page_text or "").strip() or (paper.snippet or "").strip()
    heading = page_title.strip() if page_title and page_title.strip() else None
    lines.append("")
    lines.append("Abstract / page:")
    if heading and heading.lower() not in paper.title.lower():
        lines.append(heading)
    if abstract:
        lines.append(abstract)
    elif paper.skip_reason == "scholar_pdf_skipped":
        lines.append("(PDF landing page skipped; email snippet only.)")
    else:
        lines.append("(No abstract extracted.)")
    return "\n".join(lines).strip()


def paper_to_parsed_message(
    paper: ScholarPaper,
    parent: ParsedMessage,
    *,
    body_text: str,
    extra_warnings: tuple[str, ...] = (),
    max_body_chars: int,
) -> ParsedMessage:
    body = body_text.strip()
    warnings: list[str] = list(extra_warnings)
    if paper.skip_reason:
        warnings.append(paper.skip_reason)
    if len(body) > max_body_chars:
        body = body[:max_body_chars]
        warnings.append("body_truncated")
    subject = clip_heading(paper.title, _SUBJECT_MAX) or paper.title
    preview = body if len(body) <= _PREVIEW_MAX else body[: _PREVIEW_MAX - 1] + "…"
    sender = paper.authors or parent.sender
    words = len(body.split())
    read_time = max(1, (words + 199) // 200)
    saved_at: datetime | None = parent.date_parsed
    return ParsedMessage(
        message_key=paper_message_key(paper.url),
        content_hash=compute_content_hash(body),
        folder_name=parent.folder_name,
        relative_folder_path=parent.relative_folder_path,
        subject=subject,
        sender=sender,
        date_raw=parent.date_raw,
        date_parsed=saved_at,
        body_text=body,
        body_html=None,
        html_heading_count=0,
        html_link_count=1,
        html_section_break_count=0,
        links=(paper.url,),
        link_items=(
            LinkItem(href=paper.url, text=paper.title, context=None, source_index=0),
        ),
        read_time_minutes=read_time,
        preview=preview,
        parse_warnings=tuple(warnings),
        source_key=parent.source_key,
        list_id=parent.list_id,
    )
