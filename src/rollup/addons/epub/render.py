"""Build a rich EPUB from DigestReport (ebooklib)."""

from __future__ import annotations

import html as html_module
import logging
import re
from datetime import datetime
from pathlib import Path

from rollup.addons.artifact_write import atomic_write_digest_artifact
from rollup.addons.offline_text import strip_urls_for_offline
from rollup.assets import LOGO_FILENAME, asset_bytes
from rollup.final_review import format_final_review_digest_summary
from rollup.models import DigestEntry, DigestGroup, DigestItem, DigestReport
from rollup.render import (
    ROLLUP_TITLE,
    digest_output_stem,
    display_sender,
    folder_display_name,
    format_date,
    format_read_time,
    render_summary_html,
)
from rollup.summarize import clean_summary_output

logger = logging.getLogger(__name__)

EPUB_DEPENDENCY_HINT = (
    "EPUB output requires ebooklib. Install with: pip install 'rollup[epub]'"
)

_EPUB_CSS = """
body {
  font-family: "Palatino Linotype", Palatino, "Book Antiqua", Georgia, serif;
  line-height: 1.45;
  margin: 1.2em;
  color: #111;
}
h1, h2, h3 {
  font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
  font-weight: 700;
  line-height: 1.25;
  page-break-after: avoid;
}
h1 { font-size: 1.6em; }
h2 { font-size: 1.3em; margin-top: 1.4em; }
h3 { font-size: 1.1em; margin-top: 1.1em; }
.chapter { page-break-before: always; }
.chapter.first { page-break-before: auto; }
.cover { text-align: center; margin-top: 2em; }
.cover img { max-width: 40%; height: auto; }
.meta { color: #333; font-size: 0.95em; margin: 0.4em 0 1em; }
.meta dt { font-weight: 700; display: inline; }
.meta dd { display: inline; margin: 0 1em 0 0.25em; }
.entry { margin: 1.25em 0 1.75em; }
.summary { margin: 0.75em 0; }
.group-summary { margin: 0.75em 0 1em; font-style: italic; }
.compact-list { padding-left: 1.2em; }
.appendix p, .appendix li { font-size: 0.95em; }
"""


def ebooklib_available() -> bool:
    try:
        import ebooklib  # noqa: F401
        from ebooklib import epub  # noqa: F401
    except ImportError:
        return False
    return True


def _slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return cleaned or "section"


def _format_newsletter_type(ntype: str) -> str:
    parts = ntype.split("_")
    if len(parts) == 1:
        return parts[0].capitalize()
    return f"{parts[0].capitalize()} {' '.join(parts[1:])}"


def _render_entry_xhtml(entry: DigestEntry) -> str:
    p = entry.classified.parsed
    ntype = entry.classified.newsletter_type
    parts = [
        "<article class='entry'>",
        f"<h2>{html_module.escape(p.subject)}</h2>",
        "<dl class='meta'>",
        f"<dt>From</dt><dd>{html_module.escape(display_sender(p.sender))}</dd>",
        f"<dt>Date</dt><dd>{html_module.escape(format_date(p.date_parsed))}</dd>",
        f"<dt>Read</dt><dd>{html_module.escape(format_read_time(p.read_time_minutes))}</dd>",
        f"<dt>Type</dt><dd>{html_module.escape(_format_newsletter_type(ntype))}</dd>",
        f"<dt>Folder</dt><dd>{html_module.escape(folder_display_name(p.folder_name))}</dd>",
        "</dl>",
    ]
    if entry.summary:
        summary = strip_urls_for_offline(clean_summary_output(entry.summary))
        if summary:
            parts.append(
                f"<div class='summary'>{render_summary_html(summary)}</div>"
            )
    parts.append("</article>")
    return "\n".join(parts)


def _render_group_xhtml(group: DigestGroup) -> str:
    n = len(group.entries)
    if group.group_type == "notification_stream":
        title = f"{group.display_name} — {n} updates this week"
        summary_label = "This week"
        fallback = f"Grouped notification stream ({n} messages). Newest first."
        compact = True
    else:
        unit = "editions" if group.group_type == "daily_editions" else "messages"
        title = f"{group.display_name} — {n} {unit} this week"
        summary_label = "Edition roundup"
        fallback = f"Grouped {group.group_type.replace('_', ' ')} ({n} messages)."
        compact = False

    parts = [
        "<section class='entry group'>",
        f"<h2>{html_module.escape(title)}</h2>",
    ]
    if group.group_summary:
        summary = strip_urls_for_offline(group.group_summary.strip())
        parts.append(
            f"<div class='group-summary'><p><strong>"
            f"{html_module.escape(summary_label)}:</strong></p>"
            f"{render_summary_html(summary)}</div>"
        )
    else:
        parts.append(f"<p>{html_module.escape(fallback)}</p>")

    if compact:
        parts.append("<ol class='compact-list'>")
        for entry in group.entries:
            p = entry.classified.parsed
            subject = html_module.escape(p.subject[:100])
            date = (
                p.date_parsed.strftime("%Y-%m-%d") if p.date_parsed else "undated"
            )
            preview_src = strip_urls_for_offline(
                (entry.summary or p.preview or "")[:200]
            )
            preview = html_module.escape(preview_src)
            item = f"<li><strong>{date}</strong> — {subject}"
            if preview:
                item += f"<br/><em>{preview}</em>"
            item += "</li>"
            parts.append(item)
        parts.append("</ol>")
    else:
        for entry in group.entries:
            parts.append(_render_entry_xhtml(entry))

    parts.append("</section>")
    return "\n".join(parts)


def _render_item_xhtml(item: DigestItem) -> str:
    if isinstance(item, DigestGroup):
        return _render_group_xhtml(item)
    return _render_entry_xhtml(item)


def _wrap_chapter(title: str, body: str, *, first: bool = False) -> str:
    """Return chapter body HTML (ebooklib wraps the document shell)."""
    cls = "chapter first" if first else "chapter"
    return (
        f"<div class='{cls}'>"
        f"<h1>{html_module.escape(title)}</h1>"
        f"{body}"
        "</div>"
    )


def _cover_xhtml(
    report: DigestReport, *, include_logo: bool
) -> str:
    ws = report.window_start.strftime("%Y-%m-%d")
    we = report.window_end.strftime("%Y-%m-%d")
    total = report.stats.dated_included + report.stats.undated_needing_review
    gen = report.generated_at.strftime("%Y-%m-%d")
    logo = (
        f'<p><img src="images/{LOGO_FILENAME}" alt="{html_module.escape(ROLLUP_TITLE)} logo"/></p>'
        if include_logo
        else ""
    )
    return (
        f"<div class='cover'>"
        f"{logo}"
        f"<h1>{html_module.escape(ROLLUP_TITLE)}</h1>"
        f"<p><strong>Week of {ws} to {we}</strong></p>"
        f"<p>{total} newsletters · generated {gen}</p>"
        "</div>"
    )


def _appendix_xhtml(report: DigestReport) -> str:
    parts = [
        "<div class='appendix'>",
        "<h2>Stats</h2>",
        "<ul>",
        f"<li>Folders scanned: {report.stats.folders_scanned}</li>",
        f"<li>Messages parsed: {report.stats.messages_parsed}</li>",
        f"<li>Dated included: {report.stats.dated_included}</li>",
        f"<li>Undated needing review: {report.stats.undated_needing_review}</li>",
        "</ul>",
    ]
    if report.summary_metadata:
        parts.extend(
            [
                "<h2>Summary metadata</h2>",
                "<ul>",
                f"<li>Mode: {html_module.escape(report.summary_metadata.mode)}</li>",
                "<li>Profiles used: "
                f"{html_module.escape(', '.join(report.summary_metadata.profiles_used) or 'none')}</li>",
                "<li>Models used: "
                f"{html_module.escape(', '.join(report.summary_metadata.models_used) or 'none')}</li>",
                "</ul>",
            ]
        )
    if report.final_review is not None:
        summary = format_final_review_digest_summary(report.final_review)
        parts.append("<h2>Final review</h2>")
        parts.append(f"<pre>{html_module.escape(summary)}</pre>")
    parts.append("</div>")
    return _wrap_chapter("Digest generation details", "\n".join(parts))


def render_epub_bytes(
    report: DigestReport,
    max_display_links: int,
    *,
    run_id_short: str | None = None,
) -> bytes:
    """Build EPUB bytes for the digest. Raises ImportError if ebooklib missing."""
    del max_display_links  # EPUB digests omit link lists
    from ebooklib import epub

    stem = digest_output_stem(
        report.generated_at, run_id_short=run_id_short
    )
    gen_date = report.generated_at.strftime("%Y-%m-%d")
    book = epub.EpubBook()
    book.set_identifier(f"rollup-{stem}")
    book.set_title(f"{ROLLUP_TITLE} — {gen_date}")
    book.set_language("en")
    book.add_author(ROLLUP_TITLE)
    book.add_metadata("DC", "date", report.generated_at.date().isoformat())

    style = epub.EpubItem(
        uid="style_book",
        file_name="style/book.css",
        media_type="text/css",
        content=_EPUB_CSS.encode("utf-8"),
    )
    book.add_item(style)

    include_logo = True
    try:
        logo_data = asset_bytes(LOGO_FILENAME)
        logo_item = epub.EpubItem(
            uid="logo",
            file_name=f"images/{LOGO_FILENAME}",
            media_type="image/png",
            content=logo_data,
        )
        book.add_item(logo_item)
    except Exception as exc:  # pragma: no cover - asset always present in package
        logger.warning("Could not embed logo in EPUB: %s", exc)
        include_logo = False

    cover = epub.EpubHtml(
        title="Cover",
        file_name="cover.xhtml",
        lang="en",
    )
    cover.content = _cover_xhtml(report, include_logo=include_logo)
    cover.add_item(style)
    book.add_item(cover)

    chapters: list = []
    toc_chapters: list = []

    folder_index = 0
    for folder, items in sorted(report.dated_by_folder.items()):
        folder_index += 1
        title = folder_display_name(folder)
        file_name = f"folder-{folder_index:02d}-{_slug(folder)}.xhtml"
        body_parts = [_render_item_xhtml(item) for item in items]
        chapter = epub.EpubHtml(title=title, file_name=file_name, lang="en")
        chapter.content = _wrap_chapter(
            title, "\n".join(body_parts), first=folder_index == 1
        )
        chapter.add_item(style)
        book.add_item(chapter)
        chapters.append(chapter)
        toc_chapters.append(chapter)

    if report.undated:
        title = "Undated / needs review"
        body_parts = [_render_item_xhtml(item) for item in report.undated]
        chapter = epub.EpubHtml(
            title=title, file_name="undated.xhtml", lang="en"
        )
        chapter.content = _wrap_chapter(title, "\n".join(body_parts))
        chapter.add_item(style)
        book.add_item(chapter)
        chapters.append(chapter)
        toc_chapters.append(chapter)

    appendix = epub.EpubHtml(
        title="Digest generation details",
        file_name="appendix.xhtml",
        lang="en",
    )
    appendix.content = _appendix_xhtml(report)
    appendix.add_item(style)
    book.add_item(appendix)
    chapters.append(appendix)

    # Flat TOC: cover + folder chapters + appendix (EpubHtml items only).
    book.toc = (cover, *toc_chapters, appendix)

    book.add_item(epub.EpubNcx())
    nav = epub.EpubNav()
    book.add_item(nav)
    book.spine = [nav, cover, *chapters]

    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".epub", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        epub.write_epub(str(tmp_path), book)
        return tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Could not remove temp EPUB %s: %s", tmp_path, exc)


def atomic_write_epub_digest(
    output_dir: Path,
    generated_at: datetime,
    data: bytes,
    *,
    run_id_short: str | None = None,
) -> Path:
    """Write EPUB beside the core stem (``.epub``)."""
    return atomic_write_digest_artifact(
        output_dir,
        generated_at,
        data,
        extension="epub",
        run_id_short=run_id_short,
    )
