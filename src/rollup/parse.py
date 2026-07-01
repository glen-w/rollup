"""Parse mbox messages into ParsedMessage records."""

from __future__ import annotations

import hashlib
import logging
import mailbox
import re
from datetime import datetime
from email import policy
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from pathlib import Path

import html2text
from bs4 import BeautifulSoup

from rollup.models import ParsedMessage

logger = logging.getLogger(__name__)

BOILERPLATE_PREFIXES = (
    "unsubscribe",
    "view in browser",
    "manage preferences",
    "privacy policy",
)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def normalize_message_id(value: str) -> str:
    value = value.strip().strip("<>").lower()
    return " ".join(value.split())


def compute_message_key(
    message_id: str | None,
    folder_name: str,
    subject: str,
    sender: str,
    date_raw: str,
    body_text: str,
) -> tuple[str, tuple[str, ...]]:
    """Return (message_key, warnings)."""
    warnings: list[str] = []
    if message_id:
        mid = normalize_message_id(message_id)
        if mid:
            return f"mid:{mid}", tuple(warnings)
    warnings.append("no_message_id")
    chunk = body_text[:4096]
    payload = "\0".join([folder_name, subject, sender, date_raw, chunk])
    digest = hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
    return f"fb:{digest}", tuple(warnings)


def normalize_body_for_hash(body: str) -> str:
    lines = [line.rstrip() for line in body.splitlines()]
    out: list[str] = []
    blank = False
    for line in lines:
        if not line.strip():
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    return "\n".join(out).strip()


def compute_content_hash(body_text: str) -> str:
    """SHA-256 of whitespace-normalised body (uses post max-body-chars text)."""
    normalized = normalize_body_for_hash(body_text)
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()


def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _parse_date(date_raw: str) -> datetime | None:
    if not date_raw.strip():
        return None
    try:
        dt = parsedate_to_datetime(date_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt
    except Exception:
        return None


def _get_payload_text(part, max_bytes: int = 5_000_000) -> str:
    try:
        payload = part.get_payload(decode=True)
        if payload is None:
            raw = part.get_payload()
            return raw if isinstance(raw, str) else ""
        charset = part.get_content_charset() or "utf-8"
        return payload[:max_bytes].decode(charset, errors="replace")
    except Exception:
        return ""


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    cleaned = str(soup)
    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0
    return converter.handle(cleaned).strip()


def _extract_html_features(html: str) -> tuple[int, int, int]:
    soup = BeautifulSoup(html, "html.parser")
    headings = soup.find_all(re.compile(r"^h[1-6]$", re.I))
    links = soup.find_all("a", href=True)
    breaks = len(soup.find_all("hr"))
    return len(headings), len(links), breaks


def _extract_links_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith("http"):
            links.append(href)
    return links


def _dedupe_links(urls: list[str], max_links: int) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for url in urls:
        key = url.rstrip("/")
        if key not in seen:
            seen.add(key)
            result.append(url)
        if len(result) >= max_links:
            break
    return tuple(result)


def _collect_urls(plain: str, html: str | None) -> list[str]:
    urls: list[str] = []
    if html:
        urls.extend(_extract_links_from_html(html))
    urls.extend(URL_RE.findall(plain))
    if html:
        urls.extend(URL_RE.findall(html))
    return urls


def _choose_body(plain: str, html_text: str) -> str:
    plain = plain.strip()
    html_text = html_text.strip()
    if not plain and not html_text:
        return ""
    if not plain:
        return html_text
    if not html_text:
        return plain
    # Prefer longer substantive text when difference is significant
    if len(html_text) > len(plain) * 1.25:
        return html_text
    if len(plain) > len(html_text) * 1.25:
        return plain
    # Tie-break: more structure (headings/links proxy via length of lines)
    plain_lines = sum(1 for line in plain.splitlines() if line.strip())
    html_lines = sum(1 for line in html_text.splitlines() if line.strip())
    return html_text if html_lines > plain_lines else plain


def _make_preview(body_text: str) -> str:
    if not body_text:
        return ""
    lines = body_text.splitlines()
    filtered: list[str] = []
    for i, line in enumerate(lines):
        if i < 5 and any(line.lower().startswith(p) for p in BOILERPLATE_PREFIXES):
            continue
        filtered.append(line)
    text = "\n".join(filtered).strip() or body_text.strip()
    if len(text) <= 500:
        return text
    snippet = text[:500]
    if " " in snippet:
        snippet = snippet.rsplit(" ", 1)[0]
    return snippet + "…"


def _read_time_minutes(body_text: str) -> int:
    words = len(body_text.split())
    return max(1, round(words / 200))


def _walk_parts(msg) -> tuple[str, str | None]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = part.get_content_disposition()
            if disposition in ("attachment", "inline") and part.get_filename():
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plain_parts.append(_get_payload_text(part))
            elif ctype == "text/html":
                html_parts.append(_get_payload_text(part))
    else:
        ctype = msg.get_content_type()
        if ctype == "text/plain":
            plain_parts.append(_get_payload_text(msg))
        elif ctype == "text/html":
            html_parts.append(_get_payload_text(msg))
    plain = "\n\n".join(p for p in plain_parts if p.strip())
    html_raw = "\n\n".join(p for p in html_parts if p.strip()) or None
    return plain, html_raw


def parse_message(
    msg,
    folder_name: str,
    relative_folder_path: str,
    max_body_chars: int,
    max_display_links: int,
) -> ParsedMessage:
    subject = _decode_header_value(msg.get("Subject"))
    sender = _decode_header_value(msg.get("From"))
    date_raw = msg.get("Date", "") or ""
    date_parsed = _parse_date(date_raw)
    message_id_header = msg.get("Message-ID")

    plain, html_raw = _walk_parts(msg)
    html_text = _html_to_text(html_raw) if html_raw else ""
    body_text = _choose_body(plain, html_text)

    if len(body_text) > max_body_chars:
        body_text = body_text[:max_body_chars]

    if html_raw:
        html_heading_count, html_link_count, html_section_break_count = _extract_html_features(
            html_raw
        )
    else:
        html_heading_count = html_link_count = html_section_break_count = 0

    links = _dedupe_links(_collect_urls(plain, html_raw), max_display_links)
    message_key, key_warnings = compute_message_key(
        message_id_header, folder_name, subject, sender, date_raw, body_text
    )
    content_hash = compute_content_hash(body_text)
    preview = _make_preview(body_text)
    warnings = list(key_warnings)

    return ParsedMessage(
        message_key=message_key,
        content_hash=content_hash,
        folder_name=folder_name,
        relative_folder_path=relative_folder_path,
        subject=subject or "(no subject)",
        sender=sender or "(unknown)",
        date_raw=date_raw,
        date_parsed=date_parsed,
        body_text=body_text,
        body_html=html_raw,
        html_heading_count=html_heading_count,
        html_link_count=html_link_count,
        html_section_break_count=html_section_break_count,
        links=links,
        read_time_minutes=_read_time_minutes(body_text),
        preview=preview,
        parse_warnings=tuple(warnings),
    )


def iter_parsed_messages(
    mbox_path: Path,
    folder_name: str,
    relative_folder_path: str,
    max_body_chars: int,
    max_display_links: int,
) -> Iterator[tuple[ParsedMessage | None, str | None]]:
    """Yield (message, error) per mbox entry."""
    try:
        mbox = mailbox.mbox(str(mbox_path), create=False)
    except Exception as exc:
        yield None, f"mbox open failed: {exc}"
        return
    try:
        for key in mbox.keys():
            try:
                msg = mbox[key]
                if msg is None:
                    continue
                parsed = parse_message(
                    msg,
                    folder_name,
                    relative_folder_path,
                    max_body_chars,
                    max_display_links,
                )
                yield parsed, None
            except Exception as exc:
                yield None, str(exc)
    finally:
        mbox.close()


def parse_mbox_folder(
    folder,
    max_body_chars: int,
    max_display_links: int,
) -> tuple[list[ParsedMessage], int, list[str]]:
    """Parse all messages in a folder. Returns (messages, error_count, folder_errors)."""
    messages: list[ParsedMessage] = []
    errors = 0
    folder_errors: list[str] = []
    for parsed, err in iter_parsed_messages(
        folder.mbox_path,
        folder.folder_name,
        folder.relative_path,
        max_body_chars,
        max_display_links,
    ):
        if err and parsed is None and err.startswith("mbox open failed:"):
            folder_errors.append(err)
            break
        if err:
            errors += 1
            logger.debug("Parse error in %s: %s", folder.mbox_path, err)
            continue
        if parsed:
            messages.append(parsed)
    return messages, errors, folder_errors
